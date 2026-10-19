"""Regression tests for the v0.9.1 input/output fixes: bar trimming (subtitle bars, sparse content on black,
--no-trim), ICC color management, unreadable-format messages, large photos, the narrow-art warning, the -o
checks and atomic writes, file names in piped output, the negating flags and 16:9 post files.
Run: python -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skill", "clean-thumbnail", "scripts"))

import thumb  # noqa: E402

try:
    from PIL import ImageCms
except ImportError:
    ImageCms = None


def run(args, cwd=None):
    """thumb.main with stdout and stderr captured; returns (exit code or 0, text)."""
    out = io.StringIO()
    old = os.getcwd()
    if cwd:
        os.chdir(cwd)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = thumb.main(args)
            except SystemExit as e:
                code = e.code
    finally:
        os.chdir(old)
    return code, out.getvalue()


def picture(w, h, seed=0):
    """A bright, textured frame: a horizontal ramp plus noise, never bar-like."""
    rng = np.random.default_rng(seed)
    a = np.linspace(40, 200, w)[None, :, None] + rng.normal(0, 12, (h, w, 3))
    return np.clip(a, 0, 255).astype(np.uint8)


def letterbox(w=1920, h=1080, ratio=2.39, level=0):
    """Content of the given shape between bars at level; returns (array, content top, content bottom)."""
    ch = int(round(w / ratio))
    top = (h - ch) // 2
    a = np.full((h, w, 3), level, np.uint8)
    a[top:top + ch] = picture(w, ch)
    return a, top, top + ch


def subtitles(a, y, lines, size=44):
    """Burn centered subtitle lines (bundled Outfit) into a, from y down."""
    img = Image.fromarray(a)
    d = ImageDraw.Draw(img)
    font = thumb.load_font(thumb.bundled_spec("outfit@Medium"), size)
    for text in ["I never thought we would make it this far.", "Neither did I, but here we are."][:lines]:
        d.text(((a.shape[1] - d.textlength(text, font=font)) / 2, y), text, font=font, fill=(235, 235, 235))
        y += int(size * 1.15)
    return np.asarray(img).copy()


def on_black(w, h, box, fill=None):
    """A black frame with content (a picture, or a flat fill) at box (x0, y0, x1, y1)."""
    a = np.zeros((h, w, 3), np.uint8)
    x0, y0, x1, y1 = box
    a[y0:y1, x0:x1] = picture(x1 - x0, y1 - y0, seed=3) if fill is None else fill
    return Image.fromarray(a)


class SubtitleBars(unittest.TestCase):
    """image-pipeline-1: bars carrying one or two subtitle lines are trimmed like clean bars."""

    def test_two_line_subtitles_in_239_bars_are_trimmed(self):
        for level in (0, 16):
            a, top, bottom = letterbox(level=level)
            bar = 1080 - bottom
            a = subtitles(a, bottom + (bar - 2 * int(44 * 1.15)) // 2, 2)
            self.assertEqual(thumb.trim_box(Image.fromarray(a)), (0, top, 1920, bottom), level)
        a, top, bottom = letterbox()
        a = subtitles(a, bottom + 20, 2, size=32)
        self.assertEqual(thumb.trim_box(Image.fromarray(a)), (0, top, 1920, bottom))

    def test_one_subtitle_line_in_thin_20_bars_is_trimmed(self):
        a, top, bottom = letterbox(ratio=2.0)  # 60 px bars
        a = subtitles(a, bottom + 6, 1)
        self.assertEqual(thumb.trim_box(Image.fromarray(a)), (0, top, 1920, bottom))

    def test_jpeg_ringing_next_to_the_picture_still_trims_to_the_picture(self):
        a, top, bottom = letterbox()
        a = subtitles(a, bottom + 30, 2)
        buf = io.BytesIO()
        Image.fromarray(a).save(buf, "JPEG", quality=85)
        buf.seek(0)
        self.assertEqual(thumb.trim_box(Image.open(buf).convert("RGB")), (0, top, 1920, bottom))
        # two dark, noisy lines between each bar and the picture (ringing, a soft edge) go with the bar
        a, top, bottom = letterbox()
        rng = np.random.default_rng(5)
        for y in (top - 2, bottom):
            a[y:y + 2] = np.clip(rng.normal(10, 7, (2, 1920, 3)), 0, 255).astype(np.uint8)
        self.assertEqual(thumb.trim_box(Image.fromarray(a)), (0, top, 1920, bottom))

    def test_dark_textured_picture_next_to_the_bar_is_kept(self):
        a, top, bottom = letterbox()
        rng = np.random.default_rng(4)
        a[top:top + 120] = np.clip(rng.normal(12, 6, (120, 1920, 3)), 0, 255).astype(np.uint8)  # a dark sky
        self.assertEqual(thumb.trim_box(Image.fromarray(a)), (0, top, 1920, bottom))


class SparseContentOnBlack(unittest.TestCase):
    """P1: a logo or product on flat black is not letterboxed; nothing of it is cut."""

    def test_sparse_content_keeps_the_whole_frame(self):
        cases = [
            on_black(1280, 720, (590, 260, 690, 460), fill=(200, 60, 40)),  # the reviewer's rect
            on_black(1920, 1080, (750, 410, 1170, 670)),  # a 420x260 photo package
            on_black(1920, 1080, (810, 320, 1110, 760)),  # a 300x440 photo piece
            on_black(1920, 1080, (300, 380, 1620, 700), fill=(30, 120, 200)),  # a wide logo plate
            on_black(1920, 1080, (640, 300, 1280, 780)),  # a small 4:3 frame
        ]
        for img in cases:
            self.assertEqual(thumb.trim_box(img), (0, 0) + img.size)
            self.assertEqual(thumb.trim_bars(img).size, img.size)

    def test_real_bars_still_trim(self):
        img = on_black(1920, 1080, (656, 0, 1264, 1080))  # a 9:16 phone video pillarboxed in 16:9
        self.assertEqual(thumb.trim_box(img), (656, 0, 1264, 1080))
        img = on_black(1080, 1920, (0, 656, 1080, 1264))  # a 16:9 video in a phone screenshot
        self.assertEqual(thumb.trim_box(img), (0, 656, 1080, 1264))
        img = on_black(1920, 1080, (480, 300, 1440, 780))  # windowboxed: bars on all four sides
        self.assertEqual(thumb.trim_box(img), (480, 300, 1440, 780))

    def test_render_keeps_the_subject_whole(self):
        img = on_black(1280, 720, (590, 260, 690, 460), fill=(200, 60, 40))
        _, info = thumb.render(img, "LOGO", lead="title")
        self.assertEqual(info["source"], (1280, 720))
        self.assertFalse(any("trimmed" in n for n in info["notes"]), info["notes"])

    def test_large_images_are_measured_on_a_reduced_copy(self):
        calls = []
        real_reduce, real_limit = Image.Image.reduce, thumb.TRIM_MAX_PIXELS

        def reduce(self, factor, *a, **k):
            calls.append(factor)
            return real_reduce(self, factor, *a, **k)
        a, top, bottom = letterbox()
        Image.Image.reduce, thumb.TRIM_MAX_PIXELS = reduce, 400000
        try:
            box = thumb.trim_box(Image.fromarray(a))
        finally:
            Image.Image.reduce, thumb.TRIM_MAX_PIXELS = real_reduce, real_limit
        self.assertTrue(calls and calls[0] > 1, calls)
        f = calls[0]
        self.assertTrue(top - f < box[1] <= top and bottom <= box[3] < bottom + f, (box, f))  # within a block
        self.assertEqual((box[0], box[2]), (0, 1920))


class NoTrim(unittest.TestCase):
    """P1: --no-trim and an explicit --crop 0,0,1,1 keep the full frame; trimming is reported."""

    def test_no_trim_and_full_frame_crop_keep_the_frame(self):
        a, top, bottom = letterbox(1280, 720)
        img = Image.fromarray(a)
        self.assertEqual(thumb.background_box(img), (0, top, 1280, bottom))
        self.assertEqual(thumb.background_box(img, trim=False), (0, 0, 1280, 720))
        self.assertEqual(thumb.background_box(img, (0, 0, 1, 1)), (0, 0, 1280, 720))
        self.assertEqual(thumb.background_box(img, (0.1, 0, 0.9, 1), trim=False), (128, 0, 1152, 720))
        self.assertEqual(thumb.background_box(img, (0.1, 0, 0.9, 1)), (128, top, 1152, bottom))  # bars still go
        _, info = thumb.render(img, "X", lead="title", trim=False)
        self.assertEqual(info["source"], (1280, 720))
        _, info = thumb.render(img, "X", lead="title")
        self.assertEqual(info["source"], (1280, bottom - top))
        self.assertTrue(any("trimmed black bars" in n and "--no-trim" in n for n in info["notes"]), info["notes"])

    def test_no_trim_keeps_panels_whole(self):
        a, top, bottom = letterbox(1280, 720)
        p = Image.fromarray(a)
        _, info = thumb.render(None, "X", lead="title", panels=[p, p], trim=False)
        self.assertEqual(info["source"], (1280, 720))
        _, info = thumb.render(None, "X", lead="title", panels=[p, p])
        self.assertEqual(info["source"], (1280, bottom - top))
        self.assertTrue(any("panel 1" in n and "--no-trim" in n for n in info["notes"]), info["notes"])

    def test_cli_no_trim(self):
        with tempfile.TemporaryDirectory() as d:
            a, top, bottom = letterbox(1280, 720)
            Image.fromarray(a).save(os.path.join(d, "cap.png"))
            code, text = run(["cap.png", "--title", "X", "--lead", "title", "--no-trim", "-o", "t"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn("source 1280x720", text)
            self.assertNotIn("trimmed black bars", text)
            code, text = run(["cap.png", "--title", "X", "--lead", "title", "--crop", "0,0,1,1", "-o", "t"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn("source 1280x720", text)


def wide_profile(primaries):
    """ICC bytes of an sRGB profile whose colorants are replaced (D50 XYZ per primary): a wide-gamut RGB space."""
    b = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    for i in range(struct.unpack(">I", b[128:132])[0]):
        sig, off, _ = struct.unpack(">4sII", b[132 + 12 * i:144 + 12 * i])
        if sig in primaries:
            b[off + 8:off + 20] = b"".join(struct.pack(">i", int(round(v * 65536))) for v in primaries[sig])
    return bytes(b)


P3 = {b"rXYZ": (0.5151, 0.2412, -0.0011), b"gXYZ": (0.2920, 0.6922, 0.0419), b"bXYZ": (0.1571, 0.0666, 0.7841)}
RSWOP = "C:/Windows/System32/spool/drivers/color/RSWOP.icm"


def data(path):
    with open(path, "rb") as f:
        return f.read()


def tagged(img, icc, fmt="PNG"):
    buf = io.BytesIO()
    img.save(buf, fmt, icc_profile=icc)
    buf.seek(0)
    return Image.open(buf)


@unittest.skipIf(ImageCms is None, "Pillow without littlecms")
class ColorManagement(unittest.TestCase):
    """image-pipeline-2: tagged inputs are converted to sRGB before compositing."""

    def test_display_p3_photo_is_converted(self):
        icc = wide_profile(P3)
        out = thumb.to_rgb(tagged(Image.new("RGB", (32, 32), (200, 40, 40)), icc))
        r, g, b = out.getpixel((0, 0))
        self.assertGreater(r, 210)  # P3 red is more saturated than sRGB red of the same numbers
        self.assertLess(g, 20)
        # EXIF rotation and the conversion both apply
        ex = Image.Exif()
        ex[0x0112] = 6
        buf = io.BytesIO()
        Image.new("RGB", (40, 20), (200, 40, 40)).save(buf, "JPEG", icc_profile=icc, exif=ex, quality=95)
        buf.seek(0)
        out = thumb.to_rgb(Image.open(buf))
        self.assertEqual(out.size, (20, 40))
        self.assertGreater(out.getpixel((10, 20))[0], 210)

    def test_srgb_tagged_and_untagged_stay_exact(self):
        a = np.random.default_rng(0).integers(0, 256, (48, 48, 3), dtype=np.uint8)
        img = Image.fromarray(a)
        srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        self.assertTrue(np.array_equal(np.asarray(thumb.to_rgb(tagged(img, srgb))), a))
        self.assertTrue(np.array_equal(np.asarray(thumb.to_rgb(img)), a))
        self.assertIs(thumb.to_srgb(img, b"not a profile"), img)  # unreadable profile: unchanged
        # a camera's sRGB profile is rarely bit-identical to littlecms's: one that moves no color by more than
        # a level is taken as sRGB, so the pixels stay exactly as they are (a full conversion moves some by 1)
        near = {b"rXYZ": (0.4362, 0.2226, 0.0139), b"gXYZ": (0.3852, 0.7170, 0.0971), b"bXYZ": (0.1432, 0.0607, 0.7141)}
        self.assertTrue(np.array_equal(np.asarray(thumb.to_rgb(tagged(img, wide_profile(near)))), a))

    def test_cutout_and_logo_keep_alpha_and_convert(self):
        icc = wide_profile(P3)
        rgba = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        rgba.paste((200, 40, 40, 255), (8, 8, 56, 56))
        sub = thumb.cut_out(tagged(rgba, icc))
        self.assertEqual(sub.getpixel((0, 0))[3], 0)
        self.assertEqual(sub.getpixel((32, 32))[3], 255)
        self.assertGreater(sub.getpixel((32, 32))[0], 210)
        logo = thumb._prepare_logo(tagged(rgba, icc), False)
        self.assertGreater(logo.getpixel((20, 20))[0], 210)
        self.assertEqual(logo.size, (48, 48))

    @unittest.skipUnless(os.path.isfile(RSWOP), "needs a CMYK profile (Windows RSWOP.icm)")
    def test_cmyk_uses_its_profile(self):
        with open(RSWOP, "rb") as f:
            icc = f.read()
        img = tagged(Image.new("CMYK", (16, 16), (0, 200, 200, 0)), icc, "JPEG")
        naive = img.convert("RGB").getpixel((0, 0))
        managed = thumb.to_rgb(img).getpixel((0, 0))
        self.assertGreater(sum(abs(x - y) for x, y in zip(naive, managed)), 30)


class UnreadableFormats(unittest.TestCase):
    """image-pipeline-3 and -4: JPEG XR and HEIC get a message with the fix; large photos open."""

    def test_jpeg_xr_capture_named_jpg(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "shot.jpg"), "wb") as f:
                f.write(b"II\xbc\x01" + b"\x00" * 200)
            code, text = run(["shot.jpg", "--title", "X", "-o", "t"], cwd=d)
            self.assertEqual(code, 2)
            self.assertIn("JPEG XR HDR capture (despite its .jpg name)", text)
            self.assertIn("SDR PNG or JPEG", text)
            self.assertNotIn("cannot identify", text)

    def test_heic_photo(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "photo.heic"), "wb") as f:
                f.write(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 200)
            code, text = run(["photo.heic", "--title", "X", "-o", "t"], cwd=d)
            self.assertEqual(code, 2)
            self.assertIn("HEIC/HEIF", text)
            self.assertIn("pillow-heif", text)

    def test_photos_past_pillows_bomb_limit_open(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "big.png")
            Image.new("RGB", (120, 100), (90, 80, 70)).save(p)
            old = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = 4000  # stands in for Pillow's 179 MP error at 2x this
            try:
                with thumb.read_image(p) as im:
                    self.assertEqual(im.size, (120, 100))
                self.assertEqual(Image.MAX_IMAGE_PIXELS, 4000)  # restored
                real = thumb.MAX_INPUT_PIXELS
                thumb.MAX_INPUT_PIXELS = 10000
                try:
                    with self.assertRaisesRegex(ValueError, "more than the 0 MP this script reads: downscale"):
                        thumb.read_image(p)
                finally:
                    thumb.MAX_INPUT_PIXELS = real
            finally:
                Image.MAX_IMAGE_PIXELS = old


class NarrowArt(unittest.TestCase):
    """image-pipeline-5: a sliver of art on a wide frame is flagged."""

    def test_sliver_warns_and_phone_capture_does_not(self):
        sliver = Image.fromarray(picture(160, 1080))
        _, info = thumb.render(sliver, "ALOY", lead="title")
        self.assertTrue(any("fills only" in w and "--aspect 9:16" in w for w in info["warnings"]), info["warnings"])
        phone = Image.fromarray(picture(608, 1080))
        _, info = thumb.render(phone, "ALOY", lead="title")
        self.assertFalse(any("fills only" in w for w in info["warnings"]), info["warnings"])


class OutputChecks(unittest.TestCase):
    """image-pipeline-6, -7 and P3: -o is checked before rendering, by file identity, and written atomically."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.src = os.path.join(self.d, "cap.png")
        Image.fromarray(picture(1280, 720)).save(self.src)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def no_render(self):
        real = thumb.render

        def render(*a, **k):
            raise AssertionError("rendered before -o was checked")
        thumb.render = render
        return real

    def test_bad_output_folder_fails_before_rendering(self):
        real = self.no_render()
        try:
            code, text = run([self.src, "--title", "X", "--lead", "title", "-o", os.path.join(self.src, "x")])
            self.assertEqual(code, 2, text)
            self.assertIn("can't write to", text)
            self.assertNotIn("Traceback", text)
            os.makedirs(os.path.join(self.d, "t.jpg"))
            code, text = run([self.src, "--title", "X", "--lead", "title", "-o", os.path.join(self.d, "t")])
            self.assertEqual(code, 2, text)
            self.assertIn("a folder has that name", text)
        finally:
            thumb.render = real

    def test_hard_link_of_the_input_is_caught(self):
        alias = os.path.join(self.d, "alias.jpg")
        try:
            os.link(self.src, alias)
        except (OSError, AttributeError):
            self.skipTest("no hard links here")
        before = data(self.src)
        code, text = run([self.src, "--title", "X", "--lead", "title", "-o", alias])
        self.assertEqual(code, 2, text)
        self.assertIn("overwrite an input", text)
        self.assertEqual(data(self.src), before)

    def test_writes_go_through_a_temp_file(self):
        a, b = os.path.join(self.d, "a.jpg"), os.path.join(self.d, "b.jpg")
        with open(a, "wb") as f:
            f.write(b"original")
        try:
            os.link(a, b)
        except (OSError, AttributeError):
            self.skipTest("no hard links here")
        thumb.save_jpeg(Image.new("RGB", (64, 36), (10, 20, 30)), b)
        self.assertEqual(data(a), b"original")  # the other name keeps its data
        with Image.open(b) as im:
            self.assertEqual(im.size, (64, 36))
        self.assertEqual([n for n in os.listdir(self.d) if n.endswith(".tmp")], [])
        # previews and feed grids too
        c, e = os.path.join(self.d, "c.png"), os.path.join(self.d, "e.png")
        with open(c, "wb") as f:
            f.write(b"original")
        os.link(c, e)
        thumb.save_png(Image.new("RGB", (40, 30), (10, 20, 30)), e)
        self.assertEqual(data(c), b"original")
        with Image.open(e) as im:
            self.assertEqual((im.format, im.size), ("PNG", (40, 30)))
        self.assertEqual([n for n in os.listdir(self.d) if n.endswith(".tmp")], [])

    def test_a_failed_write_leaves_the_old_file_and_no_temp(self):
        path = os.path.join(self.d, "keep.jpg")
        with open(path, "wb") as f:
            f.write(b"previous render")

        class Boom(object):
            def __len__(self):
                return 10

        with self.assertRaises(TypeError):
            thumb.write_file(path, Boom())  # the write fails after the file was opened
        self.assertEqual(data(path), b"previous render")
        self.assertEqual([n for n in os.listdir(self.d) if n.endswith(".tmp")], [])

    def test_any_image_extension_on_o_is_replaced(self):
        for ext in (".avif", ".tif", ".tiff", ".heic", ".bmp", ".gif", ".JPG", ".png"):
            self.assertEqual(thumb.output_stem(os.path.join("out", "fmt" + ext), "cap.png"), os.path.join("out", "fmt"))
        self.assertEqual(thumb.output_stem(os.path.join("out", "v1.2"), "cap.png"), os.path.join("out", "v1.2"))


class PipedOutput(unittest.TestCase):
    """image-pipeline-8: piped stdout is UTF-8, so printed paths are the real ones."""

    def test_wrote_lines_carry_the_real_name(self):
        with tempfile.TemporaryDirectory() as d:
            name = "cap \u540d\u524d\u2019.png"
            Image.fromarray(picture(1280, 720)).save(os.path.join(d, name))
            raw = io.BytesIO()
            stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
            stream.isatty = lambda: False
            old_out, old_env = sys.stdout, os.environ.pop("PYTHONIOENCODING", None)
            sys.stdout = stream
            try:
                code = thumb.main([os.path.join(d, name), "--title", "X", "--lead", "title"])
                stream.flush()
            finally:
                sys.stdout = old_out
                if old_env is not None:
                    os.environ["PYTHONIOENCODING"] = old_env
            self.assertEqual(code, 0)
            text = raw.getvalue().decode("utf-8", "replace")  # cp1252 bytes would not decode: the check fails
            self.assertIn("cap \u540d\u524d\u2019_thumb.jpg", text)
            self.assertTrue(os.path.exists(os.path.join(d, "cap \u540d\u524d\u2019_thumb.jpg")))


class NegatingFlags(unittest.TestCase):
    """G5a: --no-system-fonts and --no-logo-white override a saved true."""

    def test_flags_override_the_channel_file(self):
        with tempfile.TemporaryDirectory() as d:
            Image.fromarray(picture(1280, 720)).save(os.path.join(d, "cap.png"))
            logo = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
            logo.paste((200, 30, 30, 255), (10, 10, 390, 190))
            logo.save(os.path.join(d, "logo.png"))
            with open(os.path.join(d, "clean-thumbnail.json"), "w") as f:
                json.dump({"system_fonts": True, "logo_white": True, "aspect": "16:9"}, f)
            code, text = run(["cap.png", "--title", "X", "--lead", "title", "-o", "a"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn("(system fonts)", text)
            code, text = run(["cap.png", "--title", "X", "--lead", "title", "--no-system-fonts", "-o", "b"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertNotIn("(system fonts)", text)
            code, text = run(["cap.png", "--logo", "logo.png", "--label", "Y", "--lead", "title", "-o", "c"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn("--logo-white", text)
            code, text = run(["cap.png", "--logo", "logo.png", "--label", "Y", "--lead", "title", "--no-logo-white",
                              "-o", "e"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn("logo, its own colors", text)


class WidePostFiles(unittest.TestCase):
    """post-backdrop-8: a 16:9 post gets a feed-card preview and a _1280.jpg copy, not YouTube's."""

    def test_export_by_mode(self):
        thumb_img = Image.fromarray(picture(1280, 720))
        with tempfile.TemporaryDirectory() as d:
            files = thumb.export(thumb_img, os.path.join(d, "p"), "16:9", "post")
            self.assertNotIn("phone", files)
            self.assertTrue(files["copy"][0].endswith("p_1280.jpg"))
            with Image.open(files["preview"]) as im:
                self.assertEqual(im.size, thumb.feed_preview(thumb_img).size)
                self.assertNotEqual(im.size, thumb.preview_sheet(thumb_img).size)
                # the card holds the image at its own 16:9 shape: two theme rows of a 500 px card (header 56,
                # image 281, footer 44) plus a 44 px gap, not a 4:5 card with the image stretched to 625
                self.assertEqual(im.size[1], 2 * (56 + 500 * 720 // 1280 + 44 + 44))
            with Image.open(thumb.export(Image.fromarray(picture(1080, 1350)), os.path.join(d, "q"), "4:5",
                                         "post")["preview"]) as im:
                self.assertEqual(im.size[1], 2 * (56 + 625 + 44 + 44))  # 4:5 as before
            files = thumb.export(thumb_img, os.path.join(d, "t"), "16:9")
            self.assertTrue(files["phone"][0].endswith("t_phone.jpg"))
        self.assertEqual(thumb.export_paths("s", "16:9", "post")[1], "s_1280.jpg")
        self.assertEqual(thumb.export_paths("s", "4:5", "post")[1], "s_1080.jpg")

    def test_cli_post_at_16x9(self):
        with tempfile.TemporaryDirectory() as d:
            Image.fromarray(picture(1280, 720)).save(os.path.join(d, "cap.png"))
            code, text = run(["cap.png", "--mode", "post", "--title", "Cupping", "--body", "Real one", "--aspect",
                              "16:9", "-o", "p"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertTrue(os.path.exists(os.path.join(d, "p_1280.jpg")))
            self.assertFalse(os.path.exists(os.path.join(d, "p_phone.jpg")))


if __name__ == "__main__":
    unittest.main()


class UpscaleAdvice(unittest.TestCase):
    """skill-docs-14: the upscale warning names a recapture, not a tool the skill never mentions."""

    def test_upscale_warning_names_only_a_recapture(self):
        rng = np.random.default_rng(9)
        small = Image.fromarray(rng.integers(0, 255, (358, 358, 3), dtype=np.uint8), "RGB")
        _, info = thumb.render(small, "SIMON", "No hit")
        w = [x for x in info["warnings"] if x.startswith("source is 358x358")]
        self.assertEqual(len(w), 1, info["warnings"])
        self.assertTrue(w[0].endswith("Recapture at 1440p or higher."), w[0])
