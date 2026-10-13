"""Tests for scripts/thumb.py. Run: python -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skill", "clean-thumbnail", "scripts"))

import thumb  # noqa: E402

RNG = np.random.default_rng(0)


def noise(w, h, lo=0, hi=255):
    return Image.fromarray(RNG.integers(lo, hi, (h, w, 3), dtype=np.uint8), "RGB")


def pillarboxed_square():
    """637x358 frame: a 358 px square of art between black bars, like a phone screenshot."""
    canvas = Image.new("RGB", (637, 358), (0, 0, 0))
    canvas.paste(noise(358, 358, 60, 230), (139, 0))
    return canvas


def half_busy(w=1920, h=1080, busy="left"):
    """Wide frame with one busy (noisy) half and one flat half."""
    img = Image.new("RGB", (w, h), (90, 80, 70))
    img.paste(noise(w // 2, h), (0 if busy == "left" else w // 2, 0))
    return img


def grainy(w=1920, h=1080, color=(120, 105, 90), sigma=10):
    a = np.full((h, w, 3), color, np.float32) + RNG.normal(0, sigma, (h, w, 1))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def cutout(w=600, h=900, color=(70, 95, 160)):
    """Crisp, noiseless, blue-cast figure with stripes on transparency."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 120, w - 60, h), radius=120, fill=color + (255,))
    for x in range(60, w - 60, 16):
        d.line((x, 120, x, h), fill=(color[0] + 40, color[1] + 40, min(255, color[2] + 40), 255), width=2)
    d.ellipse((w // 2 - 110, 0, w // 2 + 110, 240), fill=(150, 170, 205, 255))
    return img


class Source(unittest.TestCase):
    def test_trims_pillarbox_bars(self):
        self.assertEqual(thumb.trim_bars(pillarboxed_square()).size, (358, 358))

    def test_leaves_full_frame_alone(self):
        self.assertEqual(thumb.trim_bars(noise(320, 180, 40, 200)).size, (320, 180))

    def test_background_comes_from_the_image_and_is_near_black(self):
        bg = thumb.dark_tone(Image.new("RGB", (64, 64), (200, 140, 60)))
        self.assertLessEqual(max(bg), 22)
        self.assertGreater(bg[0], bg[2])  # keeps the warm hue

    def test_upscale_warning_for_small_art(self):
        _, info = thumb.render(pillarboxed_square(), "SIMON", "No hit")
        self.assertAlmostEqual(info["scale"], 2.01, places=2)
        self.assertTrue(any("upscaled" in w for w in info["warnings"]))


class Resolution(unittest.TestCase):
    def test_master_is_largest_size_without_upscaling(self):
        cases = [((637, 358), (1280, 720)), ((1920, 1080), (1920, 1080)), ((2560, 1440), (2560, 1440)),
                 ((3840, 2160), (3840, 2160)), ((5120, 2880), (3840, 2160)), ((1500, 1500), (2560, 1440))]
        for src, want in cases:
            self.assertEqual(thumb.pick_size([Image.new("RGB", src)], "single"), want, src)

    def test_forced_size(self):
        out, info = thumb.render(half_busy(), "SIMON", size="720p")
        self.assertEqual(out.size, (1280, 720))

    def test_layout_scales_with_the_canvas(self):
        boxes = []
        for size in ("720p", "4k"):
            out, info = thumb.render(half_busy(), "SIMON", "Parry only", size=size, text_side="left")
            boxes.append(np.array(info["text_box"], float) / [out.width, out.height, out.width, out.height])
        self.assertTrue(np.allclose(boxes[0], boxes[1], atol=0.02), boxes)

    def test_export_writes_master_phone_copy_and_preview(self):
        out, _ = thumb.render(half_busy(3840, 2160), "SIMON", "Parry only")
        with tempfile.TemporaryDirectory() as d:
            files = thumb.export(out, os.path.join(d, "t"))
            self.assertEqual(files["master"][1], (3840, 2160))
            self.assertEqual(files["phone"][1], (1280, 720))
            self.assertLessEqual(files["phone"][3], thumb.PHONE_MAX_BYTES)
            with Image.open(files["phone"][0]) as im:
                self.assertEqual(im.size, (1280, 720))
            self.assertTrue(os.path.exists(files["preview"]))

    def test_phone_copy_stays_under_2mb_on_pure_noise(self):
        with tempfile.TemporaryDirectory() as d:
            _, size = thumb.save_jpeg(noise(1280, 720), os.path.join(d, "t.jpg"))
        self.assertLessEqual(size, thumb.PHONE_MAX_BYTES)


class Layout(unittest.TestCase):
    def test_type_goes_on_the_calmer_side(self):
        _, info = thumb.render(half_busy(busy="left"), "SIMON")
        self.assertEqual(info["text_side"], "right")
        _, info = thumb.render(half_busy(busy="right"), "SIMON")
        self.assertEqual(info["text_side"], "left")

    def test_type_never_enters_the_duration_badge_corner(self):
        for side in ("left", "right"):
            out, info = thumb.render(half_busy(), "SIMON", "No hit · Parry only", kicker="Expedition 33",
                                     subtitle="The Divergent Star", text_side=side)
            self.assertFalse(thumb.overlaps(info["text_box"], thumb.Canvas(*out.size).timestamp_zone), side)

    def test_long_title_fits_the_column(self):
        out, info = thumb.render(half_busy(), "THE DIVERGENT STAR OF LUMIERE", text_side="left")
        x0, _, x1, _ = info["text_box"]
        self.assertLessEqual(x1 - x0, thumb.Canvas(*out.size).col_w + 1)

    def test_word_budget_warning(self):
        _, info = thumb.render(half_busy(), "SIMON", "No hit · Parry only · Expert run",
                               kicker="Clair Obscur Expedition 33")
        self.assertTrue(any("words of type" in w for w in info["warnings"]))


class Lead(unittest.TestCase):
    def test_label_lead_puts_the_hook_in_big_type(self):
        _, a = thumb.render(half_busy(), "SIMON", "No hit · Parry only", lead="title", text_side="left")
        _, b = thumb.render(half_busy(), "SIMON", "No hit · Parry only", lead="label", text_side="left")
        self.assertEqual((a["lead"], b["lead"]), ("title", "label"))
        self.assertGreater(b["big_size"], thumb.Canvas(1920, 1080).px(80))  # the hook is set large

    def test_stacked_lines_lead_with_the_short_one(self):
        cv = thumb.Canvas(1280, 720)
        spec = thumb.resolve_font(thumb.STYLES["sans"]["title"])
        lspec = thumb.resolve_font(thumb.LABEL_FONTS)
        items, *_ = thumb.text_block(cv, "NO HIT\nPARRY ONLY", "SIMON", "", "", spec, lspec, 0.02,
                                     (255, 255, 255, 255), stack=True)
        sizes = [i[2].size for i in items if i[0] == "text"]
        self.assertGreater(sizes[0], sizes[1])
        self.assertLessEqual(sizes[0], sizes[1] * 1.6 + 1)

    def test_ab_cli_writes_two_variants_and_skips_b_without_label(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            thumb.main([src, "--title", "SIMON", "--label", "Parry only", "-o", os.path.join(d, "t")])
            for f in ("t_A.jpg", "t_A_phone.jpg", "t_A_preview.png", "t_B.jpg", "t_B_phone.jpg"):
                self.assertTrue(os.path.exists(os.path.join(d, f)), f)
            thumb.main([src, "--title", "SIMON", "-o", os.path.join(d, "solo")])
            self.assertTrue(os.path.exists(os.path.join(d, "solo.jpg")))
            self.assertFalse(os.path.exists(os.path.join(d, "solo_B.jpg")))


class Roles(unittest.TestCase):
    def test_lifted_near_black_subject_does_not_gain_false_color(self):
        person = Image.new("RGBA", (320, 400), (2, 3, 7, 255))
        ImageDraw.Draw(person).rectangle((0, 0, 319, 119), fill=(165, 135, 110, 255))
        background = Image.new("RGB", person.size, (150, 150, 150))
        matched, _ = thumb.match_layer(person, background, thumb.Canvas(1280, 720), cast_strength=0)
        shirt = matched.getpixel((160, 300))[:3]
        self.assertGreater(min(shirt), 30)  # its black point was lifted toward the bright room
        self.assertLessEqual(max(shirt) - min(shirt), 8)  # tiny blue-channel noise stays tiny

    def test_panels_side_by_side_with_labels_and_bottom_type(self):
        a, b = grainy(color=(60, 70, 90)), grainy(color=(110, 90, 70))
        out, info = thumb.render(None, "GAMING", "Which is faster", panels=[a, b], panel_labels=["25H2", "26H2"])
        self.assertEqual(info["layout"], "panels")
        self.assertEqual(out.size, (1920, 1080))
        cv = thumb.Canvas(*out.size)
        self.assertFalse(thumb.overlaps(info["text_box"], cv.timestamp_zone))
        mid = np.asarray(out)[cv.h // 3, cv.w // 2 - 1: cv.w // 2 + 2]
        self.assertGreater(mid.mean(), 150)  # light divider between the panels

    def test_bright_panels_get_a_dark_band_under_the_type(self):
        a, b = grainy(color=(225, 220, 210)), grainy(color=(235, 230, 215))
        for lead in ("title", "label"):
            _, info = thumb.render(None, "GAMING", "Which is faster", panels=[a, b], lead=lead)
            self.assertLessEqual(info["behind_type"], thumb.MAX_LUMA_BEHIND_TYPE, lead)

    def test_panels_and_capture_are_exclusive(self):
        with self.assertRaises(ValueError):
            thumb.render(half_busy(), "X", panels=[half_busy(), half_busy()])
        with self.assertRaises(ValueError):
            thumb.render(None, "X", panels=[half_busy()])

    def test_subject_goes_opposite_the_type_and_is_matched(self):
        bg = grainy(color=(150, 120, 80), sigma=9)  # warm and grainy
        out, info = thumb.render(bg, "4 MONTHS", "Of silence", subjects=[cutout()], text_side="left", push=0.0)
        rep = info["subjects"][0]
        self.assertGreater(rep["cast_shift"], 0.05)  # blue cast pulled toward the warm photo
        self.assertIn("grain_added", rep)  # clean cut-out gets the photo's grain
        a = np.asarray(out, np.float32)
        right, left = a[:, int(out.width * 0.7):], a[:, : int(out.width * 0.3)]
        blue = lambda x: float((x[..., 2] - x[..., 0]).mean())  # noqa: E731
        self.assertGreater(blue(right), blue(left))  # the figure landed on the right, away from the type

    def test_subject_sharper_than_photo_gets_softened(self):
        soft_bg = grainy(sigma=2).resize((480, 270)).resize((1920, 1080))
        _, info = thumb.render(soft_bg, "X", subjects=[cutout()], text_side="left", push=0.0)
        self.assertIn("softened_px", info["subjects"][0])

    def test_opaque_subject_needs_cutout(self):
        try:
            import rembg  # noqa: F401
            self.skipTest("rembg installed")
        except ImportError:
            pass
        with self.assertRaises(ValueError):
            thumb.render(half_busy(), "X", subjects=[Image.new("RGB", (300, 300), (200, 0, 0))])

    def test_inset_lands_in_its_box_and_warns_on_type_overlap(self):
        room = grainy(color=(40, 36, 32), sigma=6)
        art = Image.new("RGB", (800, 600), (30, 160, 60))  # green screen content
        out, info = thumb.render(room, "ON A CRT", "DLSS 5", inset=art, inset_box=(0.05, 0.25, 0.40, 0.75),
                                 text_side="right")
        x0, y0, x1, y1 = info["inset_rect"]
        region = np.asarray(out, np.float32)[y0 + 20:y1 - 20, x0 + 20:x1 - 20]
        self.assertGreater(region[..., 1].mean(), region[..., 0].mean() + 40)
        self.assertFalse(any("overlaps the inset" in w for w in info["warnings"]))
        _, info = thumb.render(room, "ON A CRT", "DLSS 5", inset=art, inset_box=(0.30, 0.20, 0.95, 0.80),
                               text_side="right")
        self.assertTrue(any("overlaps the inset" in w for w in info["warnings"]))


class Vertical(unittest.TestCase):
    def test_sizes_per_aspect_without_upscaling(self):
        big = [Image.new("RGB", (3840, 2160))]
        self.assertEqual(thumb.pick_size(big, "single", aspect="4:5"), (1440, 1800))
        self.assertEqual(thumb.pick_size(big, "single", aspect="9:16"), (1080, 1920))
        tall = [Image.new("RGB", (2160, 3840))]
        self.assertEqual(thumb.pick_size(tall, "single", aspect="9:16"), (2160, 3840))

    def test_reels_type_stays_in_the_safe_zone(self):
        for lead in ("title", "label"):
            out, info = thumb.render(half_busy(), "SIMON", "No hit · Parry only", lead=lead, aspect="9:16")
            self.assertEqual(out.size, (1080, 1920))
            x0, y0, x1, y1 = info["text_box"]
            self.assertGreaterEqual(y0, 0.14 * 1920 - 1)
            self.assertLessEqual(y1, 0.65 * 1920 + 1)
            self.assertLessEqual(x1, 0.85 * 1080 + 1)
            self.assertFalse(any("safe zone" in w for w in info["warnings"]), lead)

    def test_reels_type_shrinks_to_fit_then_warns_when_it_cannot(self):
        _, info = thumb.render(half_busy(), "ONE\nTWO\nTHREE\nFOUR\nFIVE\nSIX", "Label", aspect="9:16")
        self.assertIn("type scaled down to fit the height", info["notes"])
        self.assertFalse(any("safe zone" in w for w in info["warnings"]))
        many = "\n".join("LINE%d" % i for i in range(40))  # too tall even at the 48 px floor
        _, info = thumb.render(half_busy(), many, "Label", aspect="9:16")
        self.assertTrue(any("safe zone" in w for w in info["warnings"]))

    def test_feed_type_goes_below_the_image_and_above_with_a_subject(self):
        _, info = thumb.render(half_busy(), "SIMON", "Parry only", aspect="4:5")
        self.assertEqual(info["text_side"], "bottom")
        self.assertGreater(info["text_box"][1], 1350 * 0.5)
        out, info = thumb.render(grainy(color=(150, 120, 80)), "4 MONTHS", "Of silence", subjects=[cutout()],
                                 aspect="4:5")
        self.assertEqual(info["text_side"], "top")
        self.assertLess(info["text_box"][3], 1350 * 0.5)
        lower = np.asarray(out, np.float32)[int(out.height * 0.6):]
        self.assertGreater(float((lower[..., 2] - lower[..., 0]).mean()), -20)  # the blue figure is down there

    def test_crop_follows_the_subject(self):
        img = Image.new("RGB", (1920, 1080), (90, 80, 70))
        img.paste(noise(400, 1080), (1400, 0))  # all the detail is on the right
        self.assertGreater(thumb.subject_focus(img, 0.3), 0.7)

    def test_panels_stack_on_tall_canvases(self):
        a, b = grainy(color=(60, 70, 90)), grainy(color=(110, 90, 70))
        out, info = thumb.render(None, "GAMING", "Which is faster", panels=[a, b], panel_labels=["25H2", "26H2"],
                                 aspect="9:16")
        a_ = np.asarray(out.convert("L"), np.float32)
        upper, lower = a_[int(out.height * 0.05):int(out.height * 0.30)], a_[int(out.height * 0.70):]
        self.assertGreater(lower.mean(), 0.6 * upper.mean())  # the lower panel is not shaded away
        x0, y0, x1, y1 = info["text_box"]
        self.assertLess(abs((y0 + y1) / 2 - out.height / 2), out.height * 0.05)  # type on the seam
        self.assertFalse(any("safe zone" in w for w in info["warnings"]))

    def test_export_and_cli_for_all_aspects(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(3840, 2160).save(src)
            thumb.main([src, "--title", "SIMON", "--label", "Parry only", "--aspect", "all", "-o", os.path.join(d, "t")])
            for f in ("t_A.jpg", "t_A_phone.jpg", "t_B_4x5.jpg", "t_B_4x5_1080.jpg", "t_A_4x5_preview.png",
                      "t_A_9x16.jpg", "t_A_9x16_preview.png"):
                self.assertTrue(os.path.exists(os.path.join(d, f)), f)
            self.assertFalse(os.path.exists(os.path.join(d, "t_A_9x16_1080.jpg")))  # master already 1080 wide
            with Image.open(os.path.join(d, "t_B_4x5_1080.jpg")) as im:
                self.assertEqual(im.size, (1080, 1350))
            with self.assertRaises(SystemExit):
                thumb.main([src, "--title", "X", "--aspect", "1:1", "-o", os.path.join(d, "bad")])


class Contrast(unittest.TestCase):
    def test_bright_frame_gets_darkened_under_the_type(self):
        white = Image.new("RGB", (1920, 1080), (250, 248, 240))
        _, info = thumb.render(white, "SIMON", "Parry only", text_side="left")
        self.assertLessEqual(info["behind_type"], thumb.MAX_LUMA_BEHIND_TYPE)
        self.assertFalse(any("bright" in w for w in info["warnings"]))

    def test_warns_when_contrast_cannot_be_fixed(self):
        white = Image.new("RGB", (1920, 1080), (250, 248, 240))
        _, info = thumb.render(white, "SIMON", "Parry only", text_side="left", bg=(210, 210, 210))
        self.assertTrue(any("bright" in w for w in info["warnings"]))


class Assets(unittest.TestCase):
    def test_logo_replaces_title_and_can_be_whitened(self):
        logo = Image.new("RGBA", (900, 200), (0, 0, 0, 0))
        logo.paste((180, 30, 30, 255), (50, 50, 850, 150))
        out, info = thumb.render(half_busy(busy="right"), "ignored", "Parry only", logo=logo,
                                 logo_white=True, text_side="left")
        x0, y0, x1, y1 = info["text_box"]
        self.assertLessEqual(x1 - x0, thumb.Canvas(*out.size).col_w + 1)
        region = np.asarray(out.crop((x0, y0, x1, y1)))
        self.assertGreater((region.min(axis=2) > 235).mean(), 0.05)  # white logo pixels present
        self.assertFalse(((region[..., 0] > 150) & (region[..., 1] < 60)).any())  # no red left

    def test_missing_style_fonts_fall_back_but_a_named_font_must_exist(self):
        self.assertIsNone(thumb.resolve_font(["no-such-font-xyz.ttf"]))
        for kw in ({"title_font": "no-such-font-xyz.ttf"}, {"label_font": "no-such-font-xyz.ttf"}):
            with self.assertRaises(ValueError):
                thumb.render(half_busy(), "SIMON", "Parry only", **kw)
        saved = thumb.STYLES["serif"]["title"]
        thumb.STYLES["serif"]["title"] = ["no-such-font-xyz.ttf"]
        try:
            out, info = thumb.render(half_busy(), "SIMON", "Parry only")
        finally:
            thumb.STYLES["serif"]["title"] = saved
        self.assertEqual(out.size, (1920, 1080))
        self.assertTrue(any("Pillow's default font" in w for w in info["warnings"]))

    def test_squint_drops_color_and_detail(self):
        tile = thumb.render(half_busy(), "SIMON", "Parry only")[0].resize((320, 180))
        s = np.asarray(thumb.squint(tile), dtype=np.float32)
        self.assertEqual(float(np.abs(s[..., 0] - s[..., 2]).max()), 0.0)  # grayscale
        detail = lambda a: float(np.abs(np.diff(a.mean(axis=2), axis=1)).mean())  # noqa: E731
        self.assertLess(detail(s), 0.5 * detail(np.asarray(tile, dtype=np.float32)))

    def test_preview_has_feed_sizes_notification_and_squint(self):
        sheet = thumb.preview_sheet(thumb.render(half_busy(), "SIMON")[0])
        self.assertEqual(sheet.width, 2 * 28 + 480 + 320 + 168 + 120 + 88 + 320 + 5 * 28)

    def test_feed_grid_places_both_variants(self):
        a = thumb.render(half_busy(1280, 720), "SIMON", "Parry only", lead="title")[0]
        b = thumb.render(half_busy(1280, 720), "SIMON", "Parry only", lead="label")[0]
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(thumb.feed_grid([a, b], d).size[1], 180 + 2 * 16)  # two tiles: one row
            for i in range(5):
                noise(640, 360).save(os.path.join(d, "o%d.png" % i))
            self.assertEqual(thumb.feed_grid([a, b], d).size[1], 2 * 180 + 3 * 16)  # seven tiles: two rows


class Channel(unittest.TestCase):
    def test_channel_file_applies_and_flags_override(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "channel.json")
            with open(path, "w") as f:
                json.dump({"style": "sans", "accent": "#d9b26f", "lead": "title", "size": "720p"}, f)
            parser_ns = type("NS", (), {})()  # as argparse would leave it: only --style passed
            for k in thumb.DEFAULTS:
                setattr(parser_ns, k, None)
            parser_ns.channel, parser_ns.style = path, "serif"
            s, source = thumb.settings(parser_ns)
            self.assertEqual(source, path)
            self.assertEqual((s["style"], s["accent"], s["lead"], s["size"]), ("serif", "#d9b26f", "title", "720p"))
            self.assertEqual(s["shade"], thumb.DEFAULTS["shade"])

    def test_saved_formats_are_used_and_flag_overrides_for_one_run(self):
        import contextlib
        import io as _io
        with tempfile.TemporaryDirectory() as d:
            half_busy(1920, 1080).save(os.path.join(d, "cap.png"))
            with open(os.path.join(d, thumb.CHANNEL_FILE), "w") as f:
                json.dump({"aspect": "4:5,9:16", "lead": "title"}, f)
            cwd = os.getcwd()
            os.chdir(d)
            try:
                out = _io.StringIO()
                with contextlib.redirect_stdout(out):
                    thumb.main(["cap.png", "--title", "SIMON", "-o", "saved"])
                thumb.main(["cap.png", "--title", "SIMON", "--aspect", "16:9", "-o", "once"])
            finally:
                os.chdir(cwd)
            self.assertTrue(os.path.exists(os.path.join(d, "saved_4x5.jpg")))
            self.assertTrue(os.path.exists(os.path.join(d, "saved_9x16.jpg")))
            self.assertFalse(os.path.exists(os.path.join(d, "saved.jpg")))  # 16:9 not saved, not rendered
            self.assertNotIn("no saved formats", out.getvalue())
            self.assertTrue(os.path.exists(os.path.join(d, "once.jpg")))  # flag wins for this run
            self.assertFalse(os.path.exists(os.path.join(d, "once_4x5.jpg")))
            with open(os.path.join(d, thumb.CHANNEL_FILE)) as f:
                self.assertEqual(json.load(f)["aspect"], "4:5,9:16")  # saved value untouched

    def test_notes_when_no_formats_are_saved(self):
        import contextlib
        import io as _io
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            out = _io.StringIO()
            cwd = os.getcwd()
            os.chdir(d)  # no channel file here
            try:
                with contextlib.redirect_stdout(out):
                    thumb.main([src, "--title", "SIMON", "-o", os.path.join(d, "t")])
            finally:
                os.chdir(cwd)
            self.assertIn("no saved formats", out.getvalue())

    def test_shipped_example_is_valid(self):
        path = os.path.join(ROOT, "skill", "clean-thumbnail", "references", "channel.example.json")
        self.assertTrue(set(thumb.load_channel(path)) <= set(thumb.DEFAULTS))

    def test_channel_file_rejects_unknown_keys(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "channel.json")
            with open(path, "w") as f:
                json.dump({"styel": "sans"}, f)
            with self.assertRaises(ValueError):
                thumb.load_channel(path)

    def test_cli_picks_up_channel_file_in_working_dir(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1920, 1080).save(src)
            with open(os.path.join(d, thumb.CHANNEL_FILE), "w") as f:
                json.dump({"lead": "title", "size": "720p"}, f)
            cwd = os.getcwd()
            os.chdir(d)
            try:
                thumb.main(["cap.png", "--title", "SIMON", "--label", "Parry only", "-o", "t"])
            finally:
                os.chdir(cwd)
            self.assertTrue(os.path.exists(os.path.join(d, "t.jpg")))  # lead title from the file: one variant
            with Image.open(os.path.join(d, "t.jpg")) as im:
                self.assertEqual(im.size, (1280, 720))  # size from the file


# ---------------------------------------------------------------- regression tests from the v0.3 review

def run_cli(args, cwd=None):
    """Run thumb.main quietly; returns (exit code or 0, stdout text)."""
    import contextlib
    import io as _io
    out, err = _io.StringIO(), _io.StringIO()
    old = os.getcwd()
    if cwd:
        os.chdir(cwd)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = thumb.main(args)
            except SystemExit as e:
                code = e.code
    finally:
        os.chdir(old)
    return code, out.getvalue() + err.getvalue()


def warned(info, text):
    return any(text in w for w in info["warnings"])


class ReviewOutputs(unittest.TestCase):
    def test_missing_output_folder_is_created(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            code, _ = run_cli([src, "--title", "SIMON", "--label", "Parry only", "-o",
                               os.path.join(d, "out", "deeper", "simon")])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(os.path.join(d, "out", "deeper", "simon_A.jpg")))

    def test_output_never_overwrites_an_input(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.jpg")
            half_busy(1280, 720).save(src)
            before = open(src, "rb").read()
            code, text = run_cli([src, "--title", "SIMON", "--lead", "title", "-o", src])
            self.assertNotEqual(code, 0)
            self.assertIn("overwrite an input", text)
            self.assertEqual(open(src, "rb").read(), before)

    def test_output_stems_with_dots_and_folders(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            run_cli([src, "--title", "SIMON", "--lead", "title", "-o", os.path.join(d, "ep.12")])
            self.assertTrue(os.path.exists(os.path.join(d, "ep.12.jpg")))
            os.mkdir(os.path.join(d, "outdir"))
            run_cli([src, "--title", "SIMON", "--lead", "title", "-o", os.path.join(d, "outdir")])
            self.assertTrue(os.path.exists(os.path.join(d, "outdir", "cap_thumb.jpg")))

    def test_missing_inputs_and_bad_arguments_are_clean_errors(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            for args in ([os.path.join(d, "nope.png"), "--title", "X"],
                         [src, "--title", "   "],
                         [src, "--title", "X", "--inset-box", "0.1,0.1,0.5,0.5"],
                         [src, "--title", "X", "--feed", os.path.join(d, "no-folder")],
                         [src, "--title", "X", "--shade", "1.5"],
                         [src, "--title", "X", "--style", "comic"]):
                code, text = run_cli(args + ["-o", os.path.join(d, "t")])
                self.assertEqual(code, 2, args)
                self.assertNotIn("Traceback", text)

    def test_feed_skips_this_runs_outputs_and_explains_empty_runs(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            noise(640, 360).save(os.path.join(d, "competitor.png"))
            code, text = run_cli([src, "--title", "SIMON", "--label", "Parry only", "--feed", d,
                                  "-o", os.path.join(d, "t")])
            self.assertIn("(1 other thumbnails)", text)  # the competitor only: not the outputs, not the capture
            code, text = run_cli([src, "--title", "SIMON", "--aspect", "4:5", "--feed", d, "-o", os.path.join(d, "v")])
            self.assertIn("--feed compares 16:9", text)


class ReviewInputs(unittest.TestCase):
    def test_dark_scene_is_not_trimmed_as_bars(self):
        a = np.full((1080, 1920, 3), 10, np.uint8)
        a[:400] = np.linspace(3, 15, 400)[:, None, None].astype(np.uint8)  # dark night sky at the top only
        rng = np.random.default_rng(5)
        ys, xs = rng.integers(0, 400, 300), rng.integers(0, 1920, 300)
        a[ys, xs] = 230  # stars
        a[400:] = 90
        img = Image.fromarray(a, "RGB")
        self.assertEqual(thumb.trim_bars(img).size, (1920, 1080))

    def test_sixteen_bit_and_transparent_backgrounds(self):
        g = Image.fromarray((np.full((720, 1280), 30000, np.uint16)), "I;16")
        rgb16 = thumb.to_rgb(g)
        self.assertLess(abs(float(np.asarray(rgb16).mean()) - 30000 / 257), 2)  # rescaled, not clipped white
        rgba = Image.new("RGBA", (1280, 720), (255, 255, 255, 0))  # hidden white under transparency
        rgba.paste((90, 60, 40, 255), (0, 0, 640, 720))
        filled = np.asarray(thumb.to_rgb(rgba))
        self.assertLess(filled[:, 900:].mean(), 40)  # filled with a dark tone, not white

    def test_role_conflicts_and_bad_cut_outs_are_rejected(self):
        a, b = grainy(), grainy()
        with self.assertRaises(ValueError):
            thumb.render(None, "X", panels=[a, b], inset=noise(100, 100), inset_box=(0.1, 0.1, 0.5, 0.5))
        with self.assertRaises(ValueError):
            thumb.render(None, "X", panels=[a, b], subjects=[cutout()])
        with self.assertRaises(ValueError):
            thumb.render(None, "X", panels=[a, b], panel_labels=["A", "B", "C"])
        with self.assertRaises(ValueError):
            thumb.render(half_busy(), "X", subjects=[Image.new("RGBA", (200, 200), (255, 0, 0, 0))])
        with self.assertRaises(ValueError):
            thumb.render(half_busy(), "X", logo=Image.new("RGB", (400, 100), (200, 30, 30)))

    def test_channel_values_are_validated(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ({"shade": "dark"}, {"logo_white": "false"}, {"lead": "both"}, {"aspect": "1:1"},
                        {"accent": "not-a-color"}):
                path = os.path.join(d, "c.json")
                with open(path, "w") as f:
                    json.dump(bad, f)
                with self.assertRaises(ValueError, msg=str(bad)):
                    thumb.load_channel(path)
            with open(path, "w") as f:
                json.dump({"aspect": "all"}, f)
            self.assertEqual(thumb.load_channel(path)["aspect"], "16:9,4:5,9:16")
            with open(path, "w") as f:
                f.write("{not json")
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            code, text = run_cli([src, "--title", "X", "--channel", path, "-o", os.path.join(d, "t")])
            self.assertEqual(code, 2)
            self.assertNotIn("Traceback", text)

    def test_huge_wide_source_renders_tall_without_resizing_everything(self):
        biggest = [0]
        real = Image.Image.resize

        def spy(self, size, *a, **k):
            biggest[0] = max(biggest[0], size[0] * size[1])
            return real(self, size, *a, **k)
        Image.Image.resize = spy
        try:
            out, info = thumb.render(Image.new("RGB", (12000, 1000), (80, 70, 60)), "X", aspect="9:16")
        finally:
            Image.Image.resize = real
        self.assertEqual(out.size, (1080, 1920))
        self.assertLessEqual(biggest[0], 1080 * 1920)  # only the visible window is ever resized


class ReviewLayout(unittest.TestCase):
    def test_wide_type_fits_the_height(self):
        for side in ("left", "right"):
            out, info = thumb.render(half_busy(), "ONE\nTWO\nSIX\nTEN", "Of silence", kicker="Game",
                                     subtitle="Epithet", text_side=side)
            cv = thumb.Canvas(*out.size)
            x0, y0, x1, y1 = info["text_box"]
            self.assertGreaterEqual(y0, cv.margin - 1, side)
            limit = cv.timestamp_zone[1] if side == "right" else cv.h - cv.margin
            self.assertLessEqual(y1, limit + 1, side)
            self.assertFalse(warned(info, "runs outside"), side)

    def test_long_label_shrinks_inside_the_frame(self):
        out, info = thumb.render(half_busy(), "SIMON", "Extraordinarily Uncompromising Deathless Speedrunning",
                                 text_side="right", lead="title")
        x0, y0, x1, y1 = info["text_box"]
        self.assertGreaterEqual(x0, 0)
        self.assertFalse(warned(info, "runs outside"))

    def test_unfittable_type_warns_on_16x9(self):
        _, info = thumb.render(half_busy(), "\n".join("L%d" % i for i in range(40)), "X", text_side="left")
        self.assertTrue(warned(info, "runs outside"))

    def test_three_stacked_panels_type_on_a_seam_and_labels_apart(self):
        ps = [grainy(color=(200, 195, 190)) for _ in range(3)]
        for aspect in ("4:5", "9:16"):
            out, info = thumb.render(None, "GAMING", "No hit · Parry only · Expert", panels=ps,
                                     panel_labels=["Low", "Mid", "High"], lead="label", aspect=aspect)
            cv = thumb.Canvas(*out.size)
            rects, div = thumb.panel_rects(cv, 3)
            seams = [r[3] + div / 2 for r in rects[:-1]]
            mid = (info["text_box"][1] + info["text_box"][3]) / 2
            self.assertTrue(any(abs(mid - s) < cv.h * 0.05 for s in seams), aspect)
            self.assertFalse(warned(info, "covers"), aspect)

    def test_panel_labels_stay_in_their_panels_without_colliding(self):
        ps = [grainy(color=(120, 110, 100)) for _ in range(3)]
        layer_boxes = []
        orig = thumb.place_panel_labels

        def spy(*a, **k):
            canvas, elements, flagged = orig(*a, **k)
            layer_boxes.extend(b for _, b in elements)
            return canvas, elements, flagged
        thumb.place_panel_labels = spy
        try:
            out, info = thumb.render(None, "GAMING", "No hit · Parry only · Expert", panels=ps,
                                     panel_labels=["Low", "Mid", "High"], lead="label", aspect="4:5")
        finally:
            thumb.place_panel_labels = orig
        rects, _ = thumb.panel_rects(thumb.Canvas(*out.size), 3)
        boxes = layer_boxes[-3:]
        for b, r in zip(boxes, rects):
            self.assertTrue(r[1] <= b[1] and b[3] <= r[3], (b, r))
        for i in range(3):
            for j in range(i + 1, 3):
                self.assertFalse(thumb.overlaps(boxes[i], boxes[j]))

    def test_reels_three_panels_flag_the_label_under_the_caption(self):
        ps = [noise(1920, 1080, 40, 200) for _ in range(3)]
        out, info = thumb.render(None, "GAMING", "Which is faster", panels=ps, panel_labels=["A", "B", "C"],
                                 aspect="9:16", lead="title")
        self.assertTrue(warned(info, "panel 3 sits under the Reels/Shorts caption"))

    def test_subject_stays_clear_of_the_type(self):
        def blue_rows_cols(out):
            a = np.asarray(out, np.int32)
            m = (a[..., 2] - a[..., 0]) > 40  # the blue cut-out on a warm background
            rows, cols = np.where(m.any(axis=1))[0], np.where(m.any(axis=0))[0]
            return rows, cols
        out, info = thumb.render(grainy(color=(150, 120, 80), sigma=4), "X", "No hit · Parry only · Expert",
                                 subjects=[cutout()], lead="label", aspect="4:5")
        rows, _ = blue_rows_cols(out)
        self.assertGreaterEqual(rows.min(), info["text_box"][3])  # the subject starts below the type
        self.assertFalse(warned(info, "overlaps the subject"))
        out, info = thumb.render(grainy(color=(150, 120, 80), sigma=4), "SIMON", "Four words of hook",
                                 subjects=[cutout(1500, 500)], text_side="left")
        _, cols = blue_rows_cols(out)
        self.assertGreaterEqual(cols.min(), info["text_box"][2])  # the wide cut-out stays right of the type
        self.assertFalse(warned(info, "overlaps the subject"))
        out, info = thumb.render(grainy(color=(150, 120, 80), sigma=4), "SIMON", "Four words of hook",
                                 subjects=[cutout(1200, 700), cutout(1200, 700)], text_side="left")
        _, cols = blue_rows_cols(out)
        self.assertGreaterEqual(cols.min(), info["text_box"][2])  # two wide cut-outs also stay clear

    def test_narrow_art_honors_shade(self):
        art = noise(400, 400, 150, 255)
        a = np.asarray(thumb.render(art, "X", shade=0.0, text_side="left")[0], np.float32)
        b = np.asarray(thumb.render(art, "X", shade=0.88, text_side="left")[0], np.float32)
        band = slice(int(a.shape[1] * 0.45), int(a.shape[1] * 0.60))
        self.assertLess(b[:, band].mean(), a[:, band].mean() - 5)


class ReviewChecks(unittest.TestCase):
    def test_dark_accent_label_is_flagged(self):
        out, info = thumb.render(Image.new("RGB", (1920, 1080), (90, 80, 70)), "SIMON", "Parry only",
                                 accent="#000000", text_side="left", lead="title")
        self.assertTrue(warned(info, "label box has"))
        self.assertTrue(warned(info, "too dark"))

    def test_contrast_is_per_element_and_wcag(self):
        canvas = Image.new("RGB", (400, 200), (0, 0, 0))
        canvas.paste((250, 250, 250), (200, 0, 400, 200))  # right half white
        layer = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rectangle((20, 20, 180, 180), fill=(255, 255, 255, 255))  # big white element on black
        d.rectangle((300, 90, 330, 110), fill=(255, 255, 255, 255))  # small white element on white
        res = dict((n, r) for n, r, *_ in thumb.element_contrast(canvas, layer, [("big", (20, 20, 181, 181)),
                                                                               ("small", (300, 90, 331, 111))]))
        self.assertGreater(res["big"], 15)
        self.assertLess(res["small"], 1.2)
        magenta = Image.new("RGB", (100, 100), (255, 0, 255))
        lay = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        ImageDraw.Draw(lay).rectangle((10, 10, 90, 90), fill=(255, 255, 255, 255))
        (_, r, *_), = thumb.element_contrast(magenta, lay, [("t", (10, 10, 91, 91))])
        self.assertLess(r, 4.5)  # white on magenta is about 3.1:1; a gray-luma check would pass it

    def test_stacked_panel_scale_is_reported_truthfully(self):
        p = noise(300, 1600, 40, 200)
        _, info = thumb.render(None, "GAMING", panels=[p, p.copy()], aspect="9:16", lead="title")
        self.assertAlmostEqual(info["scale"], 3.6, delta=0.05)
        self.assertTrue(warned(info, "panel 1 is 300x1600"))
        big, small = noise(1920, 1080, 40, 200), noise(400, 225, 40, 200)
        _, info = thumb.render(None, "GAMING", panels=[big, small], lead="title")
        self.assertTrue(warned(info, "panel 2 is 400x225"))

    def test_inset_follows_the_crop_and_warns_when_hidden(self):
        bg = Image.new("RGB", (1920, 1080), (60, 60, 60))
        bg.paste(noise(400, 1080), (800, 0))
        art = Image.new("RGB", (400, 300), (30, 200, 60))
        box = (0.0, 0.2, 0.15, 0.6)
        out, info = thumb.render(bg, "X", inset=art, inset_box=box, aspect="9:16", lead="title")
        self.assertIn("inset_rect", info)  # the crop moved to show it
        self.assertFalse(warned(info, "outside the visible frame"))
        _, info = thumb.render(bg, "X", inset=art, inset_box=box, aspect="9:16", focus=1.0, lead="title")
        self.assertTrue(warned(info, "outside the visible frame"))
        _, info = thumb.render(bg, "X", inset=art, inset_box=(0.0, 0.2, 0.2, 0.6), aspect="9:16", focus=0.05,
                               lead="title")
        self.assertTrue(warned(info, "of the inset box is inside the frame"))

    def test_small_inset_and_logo_upscales_are_flagged(self):
        _, info = thumb.render(grainy(), "X", inset=noise(96, 54), inset_box=(0.05, 0.2, 0.35, 0.7),
                               text_side="right")
        self.assertTrue(warned(info, "inset is 96x54"))
        tiny = Image.new("RGBA", (60, 20), (0, 0, 0, 0))
        tiny.paste((255, 255, 255, 255), (2, 2, 58, 18))
        _, info = thumb.render(half_busy(), "", "X", logo=tiny)
        self.assertTrue(warned(info, "logo is 56x16"))
        wide = Image.new("RGBA", (4000, 104), (0, 0, 0, 0))
        wide.paste((255, 255, 255, 255), (2, 2, 3998, 102))
        _, info = thumb.render(half_busy(), "", "X", logo=wide)
        self.assertTrue(warned(info, "too wide to read"))

    def test_rebuild_does_not_duplicate_warnings(self):
        white = Image.new("RGB", (1920, 1080), (250, 248, 240))  # bright, with a light --bg: forces the rebuild
        _, info = thumb.render(white, "SIMON", "Parry only", inset=noise(400, 300), inset_box=(0.9, 0.2, 1.0, 0.3),
                               text_side="left", focus=0.0, aspect="9:16", bg=(210, 210, 210))
        msgs = [w for w in info["warnings"] if "inset" in w]
        self.assertTrue(msgs)
        self.assertEqual(len(msgs), len(set(msgs)))

    def test_word_budget_counts_split_lines(self):
        _, info = thumb.render(half_busy(), r"FOUR\nMONTHS\nOF\nSILENCE", "Change your life now", kicker="Self")  # literal \n, as from the CLI
        self.assertTrue(warned(info, "words of type"))

    def test_subject_is_not_softened_because_of_the_push_blur(self):
        def blocks(w, h, seed):  # sharp 8 px blocks: the same detail in the photo and the cut-out
            a = np.random.default_rng(seed).integers(40, 200, (h // 8, w // 8, 3)).astype(np.uint8)
            return Image.fromarray(a, "RGB").resize((w, h), Image.NEAREST)
        subj = blocks(1200, 1800, 2).convert("RGBA")
        alpha = Image.new("L", subj.size, 0)
        ImageDraw.Draw(alpha).ellipse((100, 100, 1100, 1790), fill=255)
        subj.putalpha(alpha)
        _, info = thumb.render(blocks(3840, 2160, 1), "SIMON", subjects=[subj], push=0.5, size="1080p", lead="title")
        self.assertFalse([k for k in info["subjects"][0] if k.startswith("softened")], info["subjects"][0])


class ReviewRound2(unittest.TestCase):
    """Regressions and gaps found by the adversarial review of the v0.4.0 fixes."""

    def test_cover_box_never_leaves_the_source(self):
        for w, h in [(1280, 720), (1920, 1080), (3840, 2160), (1080, 1350), (1440, 1800), (1080, 1920), (2160, 3840)]:
            for sw, sh in [(1920, y) for y in range(560, 1081)] + [(3840, y) for y in range(1500, 2161, 7)]:
                for focus in (0.0, 0.5, 1.0):
                    (x0, y0, x1, y1), _ = thumb.cover_box(sw, sh, w, h, focus)
                    self.assertTrue(0 <= x0 <= x1 <= sw and 0 <= y0 <= y1 <= sh, (sw, sh, w, h, focus))
        out, _ = thumb.render(Image.new("RGB", (1920, 804), (80, 70, 60)), "X", aspect="9:16")  # 2.39 trailer frame
        self.assertEqual(out.size, (1080, 1920))

    def test_trim_bars_real_world_cases(self):
        def scene(w=1920, h=1080):
            a = np.zeros((h, w, 3), np.uint8)
            a[:] = np.linspace(40, 200, w)[None, :, None].astype(np.uint8)
            return a
        a = scene()
        a[:140], a[-140:] = 16, 16  # limited-range video black
        self.assertEqual(thumb.trim_bars(Image.fromarray(a)).size, (1920, 800))
        a = scene()
        a[:140], a[-140:] = 0, 0
        a[-90:-60, 600:1300] = 235  # burned-in subtitle in the bottom bar
        self.assertEqual(thumb.trim_bars(Image.fromarray(a)).size, (1920, 800))
        a = np.zeros((2160, 3840, 3), np.uint8)  # black starfield around a centered planet
        rng = np.random.default_rng(3)
        ys, xs = rng.integers(0, 2160, 16000), rng.integers(0, 3840, 16000)
        a[ys, xs] = 220
        a[700:1460, 1500:2340] = 120
        self.assertEqual(thumb.trim_bars(Image.fromarray(a)).size, (3840, 2160))
        a = scene(1080, 2340)
        a[:29] = 0  # a phone status bar on one edge only is not a letterbox
        self.assertEqual(thumb.trim_bars(Image.fromarray(a)).size, (1080, 2340))
        # dark, nearly flat sky over dark, nearly flat ground: bar-like but not pristine, and trimming it
        # would leave a 3:1 frame, which is no standard shape, so it stays
        a = scene()
        g = np.random.default_rng(9)
        a[:230] = (np.linspace(8, 12, 230)[:, None, None] + g.normal(0, 1.5, (230, 1920, 1))).clip(0, 255).astype(np.uint8)
        a[-210:] = (np.linspace(12, 9, 210)[:, None, None] + g.normal(0, 1.5, (210, 1920, 1))).clip(0, 255).astype(np.uint8)
        self.assertEqual(thumb.trim_bars(Image.fromarray(a)).size, (1920, 1080))

    def test_reels_two_panel_labels_avoid_the_hook(self):
        ps = [grainy(color=(110, 100, 90)), grainy(color=(90, 100, 110))]
        boxes = []
        orig = thumb.place_panel_labels

        def spy(*a, **k):
            r = orig(*a, **k)
            boxes[:] = [b for _, b in r[1]]
            return r
        thumb.place_panel_labels = spy
        try:
            # the type band covers the top of panel 2, where its label would go (with the bundled fonts the
            # label lands 52 px clear of the hook and 52 px inside the safe zone)
            out, info = thumb.render(None, "GAMING", "Big gains", kicker="Benchmark", panels=ps,
                                     panel_labels=["25H2", "26H2"], lead="label", aspect="9:16")
        finally:
            thumb.place_panel_labels = orig
        cv = thumb.Canvas(*out.size)
        self.assertEqual(len(boxes), 2)
        for b in boxes:
            self.assertFalse(thumb.overlaps(b, info["text_box"]), b)
            self.assertTrue(thumb.inside(b, cv.safe_box), b)
        self.assertLess(boxes[0][1], cv.safe_box[1] + cv.px(40))  # panel 1's label at the top of its panel
        panel2_top = thumb.panel_rects(cv, 2)[0][1][1]
        # its usual spot, near the top of the panel, is under the type...
        self.assertTrue(info["text_box"][1] < panel2_top + cv.px(32) < info["text_box"][3])
        self.assertGreater(boxes[1][1], info["text_box"][3])  # ...so it moved below the hook
        self.assertLessEqual(boxes[1][3], cv.safe_box[3] - cv.px(20))  # with real room to spare
        self.assertFalse(warned(info, "no clear spot"))
        self.assertFalse(warned(info, "safe zone"))
        # a two-line hook plus a kicker leaves no safe spot below the type: the warning says what to cut,
        # not "use two panels" (there are two already)
        _, info = thumb.render(None, "GAMING", "Big gains · Or not", kicker="Benchmark", panels=ps,
                               panel_labels=["25H2", "26H2"], lead="label", aspect="9:16")
        self.assertTrue(warned(info, "panel 2's label has no spot clear of the type inside the Reels/Shorts "
                                     "safe zone: shorten the type"))
        self.assertFalse(warned(info, "use two panels"))

    def test_darker_shade_still_fixes_the_title_with_a_dark_accent(self):
        flat = Image.new("RGB", (1920, 1080), (245, 245, 245))
        _, info = thumb.render(flat, "SIMON", "Parry only", accent="#1e3a8a", shade=0.4, lead="title",
                               text_side="left")
        self.assertIn("raised --shade to 0.97 for contrast", info["notes"])
        self.assertFalse(warned(info, "the title has"))
        self.assertTrue(warned(info, "the label box has"))

    def test_outlined_logo_on_dark_frame_passes(self):
        from PIL import ImageFont
        logo = Image.new("RGBA", (1400, 400), (0, 0, 0, 0))
        f = thumb.load_font(thumb.resolve_font(thumb.LABEL_FONTS), 260)
        ImageDraw.Draw(logo).text((40, 40), "HALO", font=f, fill=(255, 255, 255, 255), stroke_width=10,
                                  stroke_fill=(0, 0, 0, 255))
        _, info = thumb.render(Image.new("RGB", (1920, 1080), (30, 30, 34)), "", "Parry only", logo=logo)
        self.assertFalse(warned(info, "the logo has"))

    def test_dim_accent_gets_the_color_hint(self):
        _, info = thumb.render(Image.new("RGB", (1920, 1080), (10, 10, 12)), "SIMON", "Parry only",
                               accent="#c62828", lead="title", text_side="left")
        self.assertTrue(warned(info, "too dim to read even on black"))
        self.assertFalse(warned(info, "background under it is bright"))

    def test_reels_shade_leaves_most_of_the_frame(self):
        out, _ = thumb.render(Image.new("RGB", (3840, 2160), (128, 128, 128)), "SIMON", "Parry only",
                              aspect="9:16", lead="title")
        rows = np.asarray(out.convert("L"), np.float32).mean(axis=1)
        dark = (rows < 0.56 * rows[: int(len(rows) * 0.1)].mean()).mean()
        self.assertLess(dark, 0.40)

    def test_ultrawide_16x9_crop_keeps_the_subject_away_from_the_type(self):
        a = np.full((1440, 5120, 3), 110, np.uint8)
        a[300:1300, 500:900] = np.random.default_rng(1).integers(0, 255, (1000, 400, 3))
        out, info = thumb.render(Image.fromarray(a), "SIMON", "No hit · Parry only", lead="title")
        o = np.asarray(out.convert("L"), np.float32)
        busy = np.where(np.abs(np.diff(o, axis=0)).mean(axis=0) > 20)[0]
        self.assertTrue(len(busy))  # the textured hero is in the frame
        x0, _, x1, _ = info["text_box"]
        self.assertTrue(busy.max() < x0 or busy.min() > x1)  # and not under the type

    def test_inset_decides_the_text_side(self):
        room = grainy(color=(60, 55, 50), sigma=5)
        _, info = thumb.render(room, "DLSS 5", "On a CRT", inset=noise(800, 600), inset_box=(0.18, 0.14, 0.62, 0.70),
                               lead="title")
        self.assertEqual(info["text_side"], "right")
        self.assertFalse(warned(info, "overlaps the inset"))

    def test_auto_wrapped_hooks_share_one_size(self):
        cv = thumb.Canvas(1920, 1080)
        _, info = thumb.render(half_busy(), "RONALDO", "Of silence", lead="label")
        layer_items = thumb.text_block(cv, "OF SILENCE", "RONALDO", "", "", thumb.resolve_font(
            thumb.STYLES["serif"]["title"]), thumb.resolve_font(thumb.LABEL_FONTS), 0.06, (255, 255, 255, 255),
            stack=False)[0]
        sizes = {it[2].size for it in layer_items if it[0] == "text" and it[8] == "title"}
        self.assertEqual(len(sizes), 1)

    def test_contrast_warning_once_per_element(self):
        g = np.linspace(110, 255, 1080).astype(np.uint8)  # brighter lower down: each line scores differently
        bright = Image.fromarray(np.repeat(np.repeat(g[:, None, None], 1920, 1), 3, 2))
        _, info = thumb.render(bright, "TWO\nLINES", "X", lead="title", text_side="left", bg=(225, 225, 225),
                               shade=0.0)
        self.assertTrue(warned(info, "the title has"))
        self.assertEqual(sum(1 for w in info["warnings"] if w.startswith("the title has")), 1)

    def test_channel_nulls_mean_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            path = os.path.join(d, "chan.json")
            with open(path, "w") as f:
                json.dump({"aspect": None, "shade": None, "accent": None, "text_side": None}, f)
            code, text = run_cli([src, "--title", "X", "--channel", path, "-o", os.path.join(d, "t")])
            self.assertEqual(code, 0)
            self.assertIn(path, text)  # the 'no saved formats' note names the file in use

    def test_feed_keeps_competitors_with_output_like_names(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            feed = os.path.join(d, "feed")
            os.mkdir(feed)
            for n in ("gamespot.jpg", "mkbhd_1080.jpg", "ltt_phone.jpg"):
                noise(640, 360).save(os.path.join(feed, n))
            code, text = run_cli([src, "--title", "X", "--lead", "title", "--feed", feed, "-o", os.path.join(d, "t")])
            self.assertIn("(3 other thumbnails)", text)

    def test_wide_panel_label_wider_than_its_panel_warns(self):
        ps = [grainy() for _ in range(3)]
        _, info = thumb.render(None, "GAMING", panels=ps, panel_labels=["A", "RTX 5090 with DLSS Performance mode on", "C"],
                               lead="title")
        self.assertTrue(warned(info, "panel 2's label is wider than its panel"))

    def test_wide_subjects_stay_in_frame(self):
        for size in ((2400, 700), (1600, 1000)):
            _, info = thumb.render(grainy(color=(150, 120, 80)), "SIMON", subjects=[cutout(*size)], text_side="left",
                                   lead="title")
            self.assertFalse(warned(info, "off the frame edge"), size)


class Fonts(unittest.TestCase):
    """Bundled title fonts matched to the game, film or brand."""

    def test_every_bundled_font_loads_with_its_license(self):
        cat = thumb.font_catalog()
        self.assertGreaterEqual(len(cat), 14)
        for name, e in cat.items():
            spec = thumb.bundled_spec(name)
            self.assertIsNotNone(spec, name)
            font = thumb.load_font(spec, 60)
            self.assertFalse(thumb.missing_glyphs(font, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"), name)
            lic = os.path.join(thumb.FONT_ROOT, os.path.dirname(e["file"]), "OFL.txt")
            self.assertIn("SIL OPEN FONT LICENSE", open(lic, encoding="utf-8").read().upper(), name)
            for key in ("look", "cues", "fits", "tracking", "caps"):
                self.assertIn(key, e, name)

    def test_title_font_by_name_and_weight(self):
        _, info = thumb.render(half_busy(), "DAWN\nWALKER", "OP skills", title_font="cinzel", lead="title")
        self.assertIn("bundled font cinzel", info["title_source"])
        self.assertTrue(info["fonts"][0].endswith("@Bold"))
        _, info = thumb.render(half_busy(), "DAWN\nWALKER", "OP skills", title_font="cinzel@Black", lead="title")
        self.assertTrue(info["fonts"][0].endswith("@Black"))
        _, info = thumb.render(half_busy(), "DAWN\nWALKER", "OP skills", title_font="cinzel@Heavy", lead="title")
        self.assertTrue(warned(info, "weight 'Heavy' is not in"))
        with self.assertRaises(ValueError) as ctx:
            thumb.render(half_busy(), "X", title_font="comic-sans")
        self.assertIn("bundled fonts:", str(ctx.exception))

    def test_title_source_is_reported(self):
        logo = Image.new("RGBA", (800, 200), (0, 0, 0, 0))
        logo.paste((255, 255, 255, 255), (10, 10, 790, 190))
        self.assertEqual(thumb.render(half_busy(), "", "X", logo=logo)[1]["title_source"], "official logo")
        self.assertIn("default serif font", thumb.render(half_busy(), "X")[1]["title_source"])

    def test_blackletter_keeps_case_and_warns_on_all_caps(self):
        cv = thumb.Canvas(1920, 1080)
        _, info = thumb.render(half_busy(), "Dawnwalker", "Op skills · Ranked", title_font="gothic", lead="label")
        self.assertFalse(warned(info, "reads poorly in all caps"))  # the hook stays in the case it was written
        _, info = thumb.render(half_busy(), "DAWNWALKER", "OP skills", title_font="gothic", lead="title")
        self.assertTrue(warned(info, "gothic reads poorly in all caps"))
        del cv

    def test_missing_glyphs_and_small_type_warn(self):
        _, info = thumb.render(half_busy(), "Жук", "X", title_font="cinzel", lead="title")
        self.assertTrue(warned(info, "has no glyph for"))
        _, info = thumb.render(half_busy(1280, 720), "DAWNWALKER", "OP skills", title_font="orbitron",
                               lead="title")
        self.assertTrue(warned(info, "the big type is only"))
        _, info = thumb.render(half_busy(1280, 720), "DAWN\nWALKER", "OP skills", title_font="orbitron",
                               lead="title")
        self.assertFalse(warned(info, "the big type is only"))

    def test_bundled_fonts_are_the_fallback_without_system_fonts(self):
        saved = thumb._font_index
        thumb._font_index = {}  # --system-fonts on a machine with no system fonts at all
        try:
            self.assertIn("outfit", thumb.resolve_font(thumb.SYSTEM_LABEL_FONTS).lower())
            self.assertIn("playfair", thumb.resolve_font(thumb.SYSTEM_TITLE_FONTS["serif"]).lower())
            self.assertIn("outfit", thumb.resolve_font(thumb.SYSTEM_TITLE_FONTS["sans"]).lower())
        finally:
            thumb._font_index = saved

    def test_list_fonts_and_channel_title_font(self):
        code, text = run_cli(["--list-fonts"])
        self.assertEqual(code, 0)
        for name in thumb.font_catalog():
            self.assertIn(name, text)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            with open(os.path.join(d, thumb.CHANNEL_FILE), "w") as f:
                json.dump({"title_font": "bebas", "lead": "title"}, f)
            code, text = run_cli(["cap.png", "--title", "SIMON", "-o", "t"], cwd=d)
            self.assertEqual(code, 0)
            self.assertIn("title type: bundled font bebas", text)

    def test_zip_ships_fonts_and_licenses(self):
        import subprocess
        import zipfile
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_zip.py")], check=True,
                       capture_output=True)
        names = zipfile.ZipFile(os.path.join(ROOT, "dist", "clean-thumbnail.zip")).namelist()
        for name, e in thumb.font_catalog().items():
            self.assertIn("clean-thumbnail/fonts/" + e["file"], names)
            self.assertIn("clean-thumbnail/fonts/%s/OFL.txt" % os.path.dirname(e["file"]), names)
        self.assertIn("clean-thumbnail/fonts/catalog.json", names)
        import re
        unsafe = [n for n in names if not re.fullmatch(r"[A-Za-z0-9._/-]+", n)]
        self.assertEqual(unsafe, [])  # claude.ai refuses a zip with "[wght]" or similar in any path


def eclipse(w=1920, h=1080):
    """Dark frame with a vivid orange ring on the right, like an eclipse key art."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.hypot(xx - w * 0.75, yy - h * 0.4)
    a = np.zeros((h, w, 3), np.float32) + [40, 20, 10]
    a += np.exp(-((r - 300) / 60) ** 2)[..., None] * [255, 150, 40] + np.exp(-(r / 700) ** 2)[..., None] * [200, 80, 10]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def glyph_colors(out, info, name):
    """Median color of the solid pixels of one drawn element: the brightest 3% inside its own boxes
    (info["elements"], one per line), so the label box or another element never decides it."""
    boxes = [b for n, b in info["elements"] if n == name]
    assert boxes, "no %r element in %s" % (name, [n for n, _ in info["elements"]])
    a = np.concatenate([np.asarray(out.crop(b), np.int32).reshape(-1, 3) for b in boxes])
    lum = a.sum(axis=1)
    return np.median(a[lum >= np.percentile(lum, 97)], axis=0)


class Colors(unittest.TestCase):
    def test_signature_color_finds_vivid_families_only(self):
        r, g, b = thumb.signature_color(eclipse())
        self.assertGreater(r, 200)
        self.assertLess(b, 90)  # orange
        self.assertIsNone(thumb.signature_color(grainy(color=(110, 110, 110))))
        skin = np.zeros((1080, 1920, 3), np.uint8) + np.array([70, 60, 55], np.uint8)
        skin[200:1000, 1100:1700] = (205, 150, 120)
        self.assertIsNone(thumb.signature_color(Image.fromarray(skin)))  # skin is not a signature color

    def test_auto_title_color_takes_the_hue_and_passes_contrast(self):
        out, info = thumb.render(eclipse(), "DAWN\nWALKER", "OP skills", title_color="auto", lead="title",
                                 text_side="left")
        self.assertIn("auto", info["colors"]["big type"])
        self.assertGreaterEqual(info["contrast"], thumb.MIN_CONTRAST)
        r, g, b = glyph_colors(out, info, "title")
        self.assertGreater(r, g + 40)
        self.assertGreater(g, b)  # orange glyphs, not white
        self.assertFalse(warned(info, "contrast"))

    def test_auto_lightens_a_deep_color_and_falls_back_to_white(self):
        c = thumb.lift_to_contrast((40, 60, 255), (12, 12, 30))
        lf = float(thumb.wcag_luminance(np.array(c, np.float32) / 255))
        lb = float(thumb.wcag_luminance(np.array((12, 12, 30), np.float32) / 255))
        self.assertGreaterEqual((lf + 0.05) / (lb + 0.05), thumb.MIN_CONTRAST)
        self.assertGreater(c[2], c[0])  # still blue
        _, info = thumb.render(grainy(color=(110, 110, 110)), "X", title_color="auto", lead="title")
        self.assertIn("no signature color", info["colors"]["big type"])

    def test_explicit_colors_and_the_one_color_rule(self):
        out, info = thumb.render(half_busy(), "SIMON", "Parry only", title_color="#f2b84b", lead="title",
                                 text_side="left")
        r, g, b = glyph_colors(out, info, "title")
        self.assertTrue(abs(r - 0xf2) < 20 and abs(g - 0xb8) < 25 and abs(b - 0x4b) < 30, (r, g, b))
        self.assertEqual(info["colors"]["big type"], "#f2b84b")
        _, info = thumb.render(half_busy(), "SIMON", "Parry only", title_color="#f2b84b", accent="#5fb3ff",
                               lead="title")
        self.assertTrue(warned(info, "two accent colors"))
        _, info = thumb.render(half_busy(), "SIMON", "Parry only", title_color="#f2b84b", accent="#f2b84b",
                               lead="title")
        self.assertFalse(warned(info, "two accent colors"))
        _, info = thumb.render(half_busy(), "SIMON", "Parry only", title_color="#1b2a8a", lead="title",
                               text_side="left")
        self.assertTrue(warned(info, "the title has"))  # a deep blue big type can't be read on the dark side

    def test_colors_from_channel_and_cli(self):
        for good in ("auto", "white", "#abc"):
            self.assertIsNotNone(thumb.check_setting("title_color", good))
        with self.assertRaises(ValueError):
            thumb.check_setting("title_color", "autox")
        with self.assertRaises(ValueError):
            thumb.check_setting("bg", "auto")  # bg is a real color, never auto
        with tempfile.TemporaryDirectory() as d:
            eclipse(1280, 720).save(os.path.join(d, "cap.png"))
            with open(os.path.join(d, thumb.CHANNEL_FILE), "w") as f:
                json.dump({"title_color": "auto", "lead": "title"}, f)
            code, text = run_cli(["cap.png", "--title", "DAWN", "-o", "t"], cwd=d)
            self.assertEqual(code, 0)
            self.assertIn("colors: big type #", text)


class FontsReview(unittest.TestCase):
    """Regression tests from the review of the bundled-font feature."""

    def test_cp1252_console_never_aborts_a_run(self):
        import io as _io
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            stream = _io.TextIOWrapper(_io.BytesIO(), encoding="cp1252")
            old = sys.stdout
            sys.stdout = stream
            try:
                code = thumb.main([src, "--title", "Dawn Walker \U0001f525", "--label", "No hits", "--title-font",
                                   "cinzel", "--aspect", "all", "-o", os.path.join(d, "x\U0001f525")])
            finally:
                sys.stdout = old
            self.assertEqual(code, 0)
            for f in ("_A.jpg", "_B.jpg", "_A_4x5.jpg", "_B_9x16.jpg"):  # the emoji path is printed after each
                self.assertTrue(os.path.exists(os.path.join(d, "x\U0001f525" + f)), f)
            stream.seek(0)
            self.assertIn("U+1F525", stream.buffer.getvalue().decode("cp1252"))

    def test_all_caps_check_sees_split_titles(self):
        _, info = thumb.render(half_busy(), r"DAWN\nWALKER", "X", title_font="gothic", lead="title")
        self.assertTrue(warned(info, "reads poorly in all caps"))

    def test_small_type_advice_matches_the_layout(self):
        _, info = thumb.render(half_busy(1280, 720), "THE LAST OF US PART II", "X", title_font="pixel", lead="title")
        self.assertTrue(warned(info, "the big type is only"))
        self.assertFalse(warned(info, "split it over two lines"))  # it is already on two lines

    def test_weights_are_case_insensitive_and_fall_back_sensibly(self):
        _, info = thumb.render(half_busy(), "X", title_font="outfit@semibold", lead="title")
        self.assertTrue(info["fonts"][0].endswith("@SemiBold"))
        self.assertFalse(warned(info, "weight"))
        _, info = thumb.render(half_busy(), "X", title_font="cinzel@Heavy", lead="title")
        self.assertTrue(info["fonts"][0].endswith("@Bold"))  # the catalog weight, not the file's thinnest
        _, info = thumb.render(half_busy(), "X", title_font="rajdhani@SemiBold", lead="title")
        self.assertFalse(warned(info, "weight"))
        self.assertFalse(warned(info, "single weight"))
        _, info = thumb.render(half_busy(), "X", title_font="rajdhani@Bold", lead="title")
        self.assertTrue(warned(info, "has a single weight"))

    def test_channel_bundled_name_beside_a_same_named_folder(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "cinzel"))
            path = os.path.join(d, "chan.json")
            with open(path, "w") as f:
                json.dump({"title_font": "cinzel@Black"}, f)
            self.assertEqual(thumb.load_channel(path)["title_font"], "cinzel@Black")

    def test_damaged_fonts_folder_degrades_cleanly(self):
        saved_root, saved_cat = thumb.FONT_ROOT, thumb._catalog
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "catalog.json"), "w") as f:
                json.dump(["not", "a", "dict"], f)
            thumb.FONT_ROOT, thumb._catalog = d, None
            try:
                self.assertEqual(thumb.font_catalog(), {})
                out, _ = thumb.render(half_busy(), "SIMON", "X", lead="title")
                self.assertEqual(out.size, (1920, 1080))
                code, text = run_cli(["--list-fonts"])
                self.assertEqual(code, 1)
                self.assertIn("no bundled fonts found", text)
            finally:
                thumb.FONT_ROOT, thumb._catalog = saved_root, saved_cat

    def test_decomposed_accents_render_like_composed(self):
        _, info = thumb.render(half_busy(), "Pokémon", "X", title_font="brush", lead="title")
        self.assertFalse(warned(info, "has no glyph"))
        bg = half_busy()
        a, _ = thumb.render(bg, "POKÉMON", "X", title_font="cinzel", lead="title")
        b, _ = thumb.render(bg, "POKÉMON", "X", title_font="cinzel", lead="title")
        self.assertTrue(np.array_equal(np.asarray(a), np.asarray(b)))

    def test_lining_figures_and_kerning(self):
        if not thumb.LAYOUT_FEATURES:
            self.skipTest("Pillow without raqm")
        f = thumb.load_font(thumb.bundled_spec("playfair"), 100)
        kw = thumb._fkw(f)
        digits, caps = f.getbbox("2049", **kw), f.getbbox("HXE", **kw)
        self.assertLess(abs(digits[1] - caps[1]), 6)  # numerals reach cap height
        self.assertLess(thumb.text_width(f, "AV", 8), f.getlength("A") + f.getlength("V") + 8)

    def test_narrow_font_wraps_two_words_when_it_pays(self):
        cv = thumb.Canvas(1920, 1080)
        spec = thumb.bundled_spec("bebas")
        items, *_ = thumb.text_block(cv, "DAWN WALKER", "", "", "", spec, thumb.resolve_font(thumb.LABEL_FONTS),
                                     0.04, (255, 255, 255, 255))
        self.assertEqual([it[1] for it in items if it[0] == "text"], ["DAWN", "WALKER"])
        items, *_ = thumb.text_block(cv, "DLSS 5", "", "", "", spec, thumb.resolve_font(thumb.LABEL_FONTS),
                                     0.04, (255, 255, 255, 255))
        self.assertEqual([it[1] for it in items if it[0] == "text"], ["DLSS 5"])  # no orphaned short word

    def test_logo_with_hook_reports_both(self):
        logo = Image.new("RGBA", (800, 200), (0, 0, 0, 0))
        logo.paste((255, 255, 255, 255), (10, 10, 790, 190))
        _, info = thumb.render(half_busy(), "Dawn Walker", "No hit", logo=logo, title_font="cinzel", lead="label")
        self.assertIn("official logo; hook in bundled font cinzel", info["title_source"])

    def test_catalog_names_families_and_zip_carries_mit(self):
        import subprocess
        import zipfile
        cat = thumb.font_catalog()
        self.assertEqual(len(cat), 16)
        self.assertTrue(all(e.get("family") for e in cat.values()))
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_zip.py")], check=True, capture_output=True)
        names = zipfile.ZipFile(os.path.join(ROOT, "dist", "clean-thumbnail.zip")).namelist()
        self.assertIn("clean-thumbnail/LICENSE", names)


def centered(w=1920, h=1080):
    """Key art with the subject in the middle third and calm sides."""
    img = Image.new("RGB", (w, h), (150, 120, 90))
    img.paste(noise(w // 3, h), (w // 3, 0))
    return img


def letterboxed(w=1920, h=1080, content_h=804):
    """A 2.39:1 film frame inside 16:9, black bars top and bottom. Returns (image, bar height)."""
    img = Image.new("RGB", (w, h), (0, 0, 0))
    top = (h - content_h) // 2
    img.paste(noise(w, content_h, 60, 230), (0, top))
    return img, top


def busy_center(img):
    """Energy-weighted column of the busy part of an image."""
    a = np.asarray(img.convert("L"), np.float32)
    e = np.abs(np.diff(a, axis=0)).mean(axis=0)
    return float((e * np.arange(len(e))).sum() / e.sum())


class Crop(unittest.TestCase):
    """--crop (making room beside a centered subject) and the logo's colors line."""

    def test_crop_moves_a_centered_subject_away_from_the_type(self):
        bg = centered()
        plain, _ = thumb.render(bg, "SIMON", "X", lead="title", text_side="left")
        out, info = thumb.render(bg, "SIMON", "X", lead="title", text_side="left", crop=(0.2, 0, 0.9, 1))
        self.assertEqual(info["source"], (1344, 1080))
        self.assertEqual(out.size, (1920, 1080))  # full height kept, so no upscaling
        self.assertTrue(any("cropped the capture" in n for n in info["notes"]))
        self.assertGreater(busy_center(out) - busy_center(plain), 120)  # the subject moved toward the far side

    def test_crop_is_in_given_coordinates_and_bars_still_trim(self):
        img, top = letterboxed()
        out, _, box = thumb.prepare_background(img, (0.1, 0, 0.9, 1))
        self.assertEqual(out.size, (1536, 804))  # the bars go although the crop spans the full height
        self.assertEqual(box, (192, top, 1728, top + 804))
        _, mapped, box = thumb.prepare_background(img, None, (0.25, 0.5, 0.75, 0.6))
        self.assertIsNone(box)
        self.assertAlmostEqual(mapped[0], 0.25)
        self.assertAlmostEqual(mapped[1], (0.5 * 1080 - top) / 804)  # inset box stays where the user saw it
        self.assertAlmostEqual(mapped[3], (0.6 * 1080 - top) / 804)
        with self.assertRaisesRegex(ValueError, "--crop leaves"):
            thumb.prepare_background(img, (0, 0, 1, 0.1))  # only the top bar

    def test_crop_is_validated(self):
        a, b = noise(640, 360), noise(640, 360)
        with self.assertRaisesRegex(ValueError, "--crop applies"):
            thumb.render(None, "X", panels=[a, b], crop=(0, 0, 0.5, 1))
        with self.assertRaisesRegex(ValueError, "--crop takes"):
            thumb.render(a, "X", crop=(0.6, 0, 0.4, 1))
        with tempfile.TemporaryDirectory() as d:
            a.save(os.path.join(d, "a.png"))
            b.save(os.path.join(d, "b.png"))
            code, text = run_cli(["--panel", "a.png", "--panel", "b.png", "--title", "X", "--crop", "0,0,1,1"], cwd=d)
            self.assertEqual(code, 2)
            self.assertIn("--crop applies", text)
            code, text = run_cli(["a.png", "--title", "X", "--crop", "0.5,0,0.2,1"], cwd=d)
            self.assertEqual(code, 2)
            self.assertIn("--crop takes x0,y0,x1,y1", text)
            half_busy(1280, 720).save(os.path.join(d, "cap.png"))
            code, text = run_cli(["cap.png", "--title", "X", "--crop", "0.1,0,0.9,1", "-o", "t"], cwd=d)
            self.assertEqual(code, 0)
            self.assertIn("cropped the capture to x 128-1152, y 0-720 of 1280x720", text)

    def test_contrast_hint_names_the_tool_that_moves_the_art(self):
        _, info = thumb.render(Image.new("RGB", (1280, 720), (250, 250, 250)), "SIMON", "X", lead="title", bg="#ffffff")
        self.assertTrue(warned(info, "--crop to narrow the art"))  # --focus can't move a 16:9 frame
        _, info = thumb.render(Image.new("RGB", (2560, 720), (250, 250, 250)), "SIMON", "X", lead="title", bg="#ffffff")
        self.assertTrue(warned(info, "--focus to move the subject"))

    def test_logo_reports_its_own_colors_and_skips_the_one_color_rule(self):
        logo = Image.new("RGBA", (800, 400), (0, 0, 0, 0))
        logo.paste((200, 150, 60, 255), (10, 10, 790, 390))
        _, info = thumb.render(half_busy(), "", "OP skills", logo=logo, lead="title", title_color="#ff0000",
                               accent="#00ff00")
        self.assertEqual(info["colors"]["big type"], "logo, its own colors")
        self.assertFalse(warned(info, "two accent colors"))  # the big element is the logo, not colored type
        _, info = thumb.render(half_busy(), "", "OP skills", logo=logo, logo_white=True, lead="title")
        self.assertEqual(info["colors"]["big type"], "logo, white (--logo-white)")
        _, info = thumb.render(half_busy(), "", "OP skills", logo=logo, lead="label", title_color="#ff0000",
                               accent="#00ff00")
        self.assertEqual(info["colors"]["big type"], "#ff0000")
        self.assertFalse(warned(info, "two accent colors"))  # no label box: the logo stands for the title

    def test_any_crop_narrower_than_16x9_keeps_its_full_height(self):
        src = noise(2560, 1440)
        out, info = thumb.render(src, "SIMON", "X", lead="title", text_side="left", crop=(0.05, 0, 0.95, 1))
        self.assertEqual(info["source"], (2304, 1440))  # 1.6:1, inside the old 90% band
        self.assertEqual(out.size, (2560, 1440))  # far-side layout at full height, not cover-cropped smaller
        self.assertEqual(info["scale"], 1.0)
        _, info = thumb.render(noise(2304, 1440), "SIMON", "X", lead="title")
        self.assertEqual(info["size"], (1920, 1080))  # an uncropped capture of that shape still covers

    def test_bars_inside_the_crop_are_trimmed(self):
        desk = Image.new("RGB", (1920, 1080), (225, 228, 232))  # light app window holding a video player
        player, _ = letterboxed(1280, 720, 536)
        desk.paste(player, (320, 180))
        out, _, box = thumb.prepare_background(desk, (320 / 1920, 180 / 1080, 1600 / 1920, 900 / 1080))
        self.assertEqual(out.size, (1280, 536))
        self.assertEqual(box, (320, 272, 1600, 808))
        stage = np.full((1080, 1920, 3), 228, np.uint8)  # a performer on a black stage: the dark is content
        stage[90:990, 160:1760] = 0
        stage[200:900, 689:1311] = RNG.integers(90, 220, (700, 622, 3))
        stage[400:480, 600:1400] = (240, 40, 40)  # outstretched arms reach into the dark margin
        _, _, box = thumb.prepare_background(Image.fromarray(stage), (400 / 1920, 150 / 1080, 1600 / 1920, 980 / 1080))
        self.assertEqual(box, (400, 150, 1600, 980))  # kept as asked, arms included

    def test_inset_box_keeps_its_place_inside_a_crop(self):
        _, m, _ = thumb.prepare_background(noise(1000, 500), (0.2, 0, 0.7, 1), (0.3, 0.2, 0.5, 0.4))
        self.assertAlmostEqual(m[0], 0.2)
        self.assertAlmostEqual(m[2], 0.6)
        bg = centered()
        _, info = thumb.render(bg, "SIMON", "X", lead="title", text_side="left", crop=(0.2, 0, 0.9, 1),
                               inset=noise(400, 300), inset_box=(0.1, 0.3, 0.4, 0.7))
        self.assertGreaterEqual(info["inset_rect"][0], 1920 - 1344)  # never drawn on the dark type side
        self.assertTrue(warned(info, "of the inset box is inside the frame"))
        _, info = thumb.render(bg, "SIMON", "X", lead="title", text_side="left", crop=(0.2, 0, 0.9, 1),
                               inset=noise(400, 300), inset_box=(0.0, 0.3, 0.15, 0.7))
        self.assertNotIn("inset_rect", info)
        self.assertTrue(warned(info, "falls outside the visible frame"))

    def test_crop_applies_to_vertical_formats(self):
        for aspect in ("4:5", "9:16"):
            _, info = thumb.render(centered(), "SIMON", "X", lead="title", aspect=aspect, crop=(0.2, 0, 0.9, 1))
            self.assertEqual(info["source"], (1344, 1080), aspect)

    def test_colors_line_says_when_there_is_no_label_box(self):
        logo = Image.new("RGBA", (800, 400), (0, 0, 0, 0))
        logo.paste((200, 150, 60, 255), (10, 10, 790, 390))
        _, info = thumb.render(half_busy(), "", "OP skills", logo=logo, lead="label", accent="#f2b84b")
        self.assertEqual(info["colors"]["label box"], "none (no label box)")
        _, info = thumb.render(half_busy(), "SIMON", "", accent="#00ff00", lead="title")
        self.assertEqual(info["colors"]["label box"], "none (no label box)")
        _, info = thumb.render(grainy(color=(110, 110, 110)), "", "OP", logo=logo, title_color="auto", lead="title")
        self.assertFalse(any("big type stays white" in n for n in info["notes"]))  # the logo is the big element

    def test_dawnwalker_demo_command_runs(self):
        art, logo = os.path.join(ROOT, "demo", "dawnwalker-art.jpg"), os.path.join(ROOT, "demo", "dawnwalker-logo.png")
        if not (os.path.exists(art) and os.path.exists(logo)):
            self.skipTest("demo files not present")
        with tempfile.TemporaryDirectory() as d:
            code, text = run_cli([art, "--crop", "0.117,0,0.898,1", "--text-side", "left", "--logo", logo,
                                  "--label", "OP Skills", "-o", os.path.join(d, "dawnwalker")])
            self.assertEqual(code, 0, text)
            self.assertIn("title type: official logo", text)
            self.assertIn("colors: big type logo, its own colors", text)
            self.assertIn("cropped the capture to x 300-2299, y 0-1440 of 2560x1440", text)  # rounded, not floored
            self.assertNotIn("warning:", text)
            with Image.open(os.path.join(d, "dawnwalker_A.jpg")) as im:
                self.assertEqual(im.size, (2560, 1440))

    def test_title_tracking_spaces_the_big_type_only(self):
        bg = half_busy()
        _, tight = thumb.render(bg, "DEATH STRANDING", "No hit", title_font="outfit@Light", lead="title", text_side="left")
        _, wide = thumb.render(bg, "DEATH STRANDING", "No hit", title_font="outfit@Light", lead="title", text_side="left",
                               title_tracking=0.2)
        self.assertLess(wide["big_size"], tight["big_size"])  # same width budget, wider letters: smaller type
        self.assertIn("tracking 0.20 em", wide["title_source"])
        self.assertNotIn("tracking", tight["title_source"])
        f = thumb.load_font(thumb.bundled_spec("outfit"), 100)
        self.assertGreater(thumb.text_width(f, "DEATH", 0.2 * 100), thumb.text_width(f, "DEATH", 0.02 * 100) + 60)
        for bad in (0.8, -0.1, True, "wide"):
            with self.assertRaises(ValueError):
                thumb.check_setting("title_tracking", bad)
            with self.assertRaises(ValueError):
                thumb.render(bg, "X", title_tracking=bad)
        self.assertEqual(thumb.check_setting("title_tracking", 0.12), 0.12)
        with tempfile.TemporaryDirectory() as d:
            half_busy(1280, 720).save(os.path.join(d, "cap.png"))
            with open(os.path.join(d, thumb.CHANNEL_FILE), "w") as fh:
                json.dump({"title_tracking": 0.15, "lead": "title"}, fh)
            code, text = run_cli(["cap.png", "--title", "DAWN", "-o", "t"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn("tracking 0.15 em", text)
            code, text = run_cli(["cap.png", "--title", "DAWN", "--title-tracking", "0.9", "-o", "t"], cwd=d)
            self.assertEqual(code, 2)
            self.assertIn("from 0 to 0.5", text)

    def test_creator_demo_command_runs(self):
        photo = os.path.join(ROOT, "demo", "creator-photo.jpg")
        if not os.path.exists(photo):
            self.skipTest("demo files not present")
        with tempfile.TemporaryDirectory() as d:
            code, text = run_cli([photo, "--title", "ONE TAKE", "--label", "NO SCRIPT", "--style", "sans",
                                  "--title-font", "outfit@Bold", "--label-font", "outfit@SemiBold", "--text-side", "left",
                                  "--crop", "0.2,0,0.85,1", "--feather", "0.2", "--aspect", "16:9",
                                  "-o", os.path.join(d, "creator")])
            self.assertEqual(code, 0, text)
            self.assertNotIn("warning:", text)  # no upscaling, contrast and layout checks all pass
            self.assertIn("source 2496x2560, scaled 0.84x, type left", text)
            for v in ("A", "B"):
                with Image.open(os.path.join(d, "creator_%s.jpg" % v)) as im:
                    self.assertEqual(im.size, (3840, 2160))
                phone = os.path.join(d, "creator_%s_phone.jpg" % v)
                with Image.open(phone) as im:
                    self.assertEqual(im.size, (1280, 720))
                self.assertLess(os.path.getsize(phone), 2 * 1024 * 1024)

    def test_subject_demo_command_runs(self):
        room, person = (os.path.join(ROOT, "demo", n) for n in ("subject-room.jpg", "subject-person.png"))
        if not (os.path.exists(room) and os.path.exists(person)):
            self.skipTest("demo files not present")
        with tempfile.TemporaryDirectory() as d:
            code, text = run_cli([room, "--subject", person, "--title", "HOME STUDIO", "--label", "SMALL ROOM",
                                  "--style", "sans", "--title-font", "outfit@Bold", "--label-font", "outfit@SemiBold",
                                  "--text-side", "right", "--push", "0.35", "--aspect", "16:9",
                                  "-o", os.path.join(d, "subject")])
            self.assertEqual(code, 0, text)
            self.assertNotIn("warning:", text)  # no upscaling, no type over the person, contrast passes
            self.assertEqual(text.count("subject matched:"), 2)  # layer matching ran for both variants
            for v in ("A", "B"):
                with Image.open(os.path.join(d, "subject_%s.jpg" % v)) as im:
                    self.assertEqual(im.size, (3840, 2160))
                phone = os.path.join(d, "subject_%s_phone.jpg" % v)
                with Image.open(phone) as im:
                    self.assertEqual(im.size, (1280, 720))
                self.assertLess(os.path.getsize(phone), 2 * 1024 * 1024)

    def test_ds2_vertical_demo_command_runs(self):
        cap = os.path.join(ROOT, "demo", "ds2-capture.jpg")
        if not os.path.exists(cap):
            self.skipTest("demo files not present")
        with tempfile.TemporaryDirectory() as d:
            code, text = run_cli([cap, "--title", "DEATH STRANDING 2", "--label", "4K · Max settings", "--title-font",
                                  "outfit@Light", "--title-tracking", "0.12", "--focus", "0.46", "--lead", "title",
                                  "--aspect", "4:5,9:16", "-o", os.path.join(d, "ds2")])
            self.assertEqual(code, 0, text)
            self.assertNotIn("warning:", text)
            self.assertIn("title type: bundled font outfit (Outfit, geometric modern sans), tracking 0.12 em", text)
            self.assertIn("Outfit-VariableFont_wght.ttf@Light", text)
            for name, size in (("ds2_4x5.jpg", (1440, 1800)), ("ds2_4x5_1080.jpg", (1080, 1350)),
                               ("ds2_9x16.jpg", (1080, 1920))):
                with Image.open(os.path.join(d, name)) as im:
                    self.assertEqual(im.size, size, name)


def face_scene(w=1620, h=1080, box=(0.30, 0.12, 0.42, 0.39)):
    """3:2 photo: a textured "face" at box on a calm left half, and a busier (but dark) right half."""
    img = Image.new("RGB", (w, h), (70, 64, 58))
    img.paste(noise(w // 2, h, 20, 110), (w // 2, 0))
    x0, y0, x1, y1 = (int(round(v)) for v in (box[0] * w, box[1] * h, box[2] * w, box[3] * h))
    img.paste(noise(x1 - x0, y1 - y0, 90, 230), (x0, y0))
    return img


def busy_right(w=1920, h=1080):
    img = Image.new("RGB", (w, h), (70, 64, 58))
    img.paste(noise(w // 2, h, 20, 110), (w // 2, 0))
    return img


FACE = (0.30, 0.12, 0.42, 0.39)


class SubjectBox(unittest.TestCase):
    """--subject-box: a face or product label the type keeps clear of."""

    def test_subject_box_is_validated(self):
        img = noise(640, 360)
        with self.assertRaises(Exception):
            thumb.parse_subject_box("0.5,0,0.4,1")
        with self.assertRaisesRegex(ValueError, "--subject-box takes"):
            thumb.render(img, "X", subject_box=(0.5, 0, 0.4, 1), size="720p")
        with self.assertRaisesRegex(ValueError, "--subject-box marks"):
            thumb.render(None, "X", panels=[img, img], subject_box=FACE, size="720p")
        with self.assertRaisesRegex(ValueError, "--subject-box marks"):
            thumb.render(img, "X", subjects=[cutout()], subject_box=FACE, size="720p")
        with tempfile.TemporaryDirectory() as d:
            img.save(os.path.join(d, "cap.png"))
            cutout().save(os.path.join(d, "me.png"))
            code, text = run_cli(["cap.png", "--title", "X", "--subject-box", "0.5,0,0.4,1", "-o", "t"], cwd=d)
            self.assertEqual(code, 2)
            self.assertIn("--subject-box takes x0,y0,x1,y1", text)
            code, text = run_cli(["cap.png", "--title", "X", "--subject", "me.png", "--subject-box", "0.3,0.1,0.4,0.4",
                                  "-o", "t"], cwd=d)
            self.assertEqual(code, 2)
            self.assertIn("can't be used with --panel or --subject", text)
            with open(os.path.join(d, "ch.json"), "w") as f:
                json.dump({"subject_box": [0.3, 0.1, 0.4, 0.4]}, f)
            with self.assertRaisesRegex(ValueError, "unknown keys"):  # per image, never a channel setting
                thumb.load_channel(os.path.join(d, "ch.json"))
            code, text = run_cli(["cap.png", "--title", "X", "--subject-box", "0.3,0.1,0.4,0.4", "--lead", "title",
                                  "-o", "t"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertEqual(text.count("subject box: "), 1)  # one line per variant
            self.assertRegex(text, r"subject box: \d+% in frame, \d+% visible, \d+% under heavy shade, \d+% under type")

    def test_subject_box_remaps_like_the_inset_box(self):
        img, top = letterboxed()
        box = (0.30, 0.30, 0.50, 0.60)
        for crop in (None, (0.1, 0, 0.9, 1)):
            want = thumb.prepare_background(img, crop, box)[1]
            self.assertEqual(want, thumb.remap_box(box, img.size, thumb.background_box(img, crop)))
            _, info = thumb.render(img, "X", subject_box=box, crop=crop, size="720p")
            for got, w in zip(info["subject_box"]["box"], want):
                self.assertAlmostEqual(got, w, places=3)
        self.assertNotAlmostEqual(want[1], box[1], places=2)  # the bars moved it
        _, info = thumb.render(img, "X", subject_box=(0.30, 0.0, 0.50, 0.30), size="720p")
        self.assertTrue(warned(info, "of --subject-box is cut away by --crop or the trimmed bars"), info["warnings"])
        self.assertAlmostEqual(info["subject_box"]["box"][1], 0.0)  # clipped to the kept image
        _, info = thumb.render(img, "X", subject_box=(0.30, 0.0, 0.50, 0.05), size="720p")
        self.assertTrue(warned(info, "entirely cut away"), info["warnings"])
        self.assertNotIn("subject_box", info)

    def test_text_side_moves_to_keep_the_box_clear(self):
        img = face_scene()
        _, info = thumb.render(img, "ONE TAKE", "NO SCRIPT", size="720p")
        self.assertEqual(info["text_side"], "left")  # busyness alone puts the type over the face
        cv = thumb.Canvas(*info["size"])
        mapper, vis = thumb.wide_view(img, cv, "left")
        self.assertLess(thumb.box_cover(thumb.box_rect(FACE, mapper), cv, vis_x=vis)[1], 0.5)
        _, info = thumb.render(img, "ONE TAKE", "NO SCRIPT", size="720p", subject_box=FACE)
        self.assertEqual(info["text_side"], "right")
        self.assertGreater(info["subject_box"]["visibility"], 0.9)
        self.assertEqual(info["subject_box"]["inside"], 1.0)
        self.assertEqual(info["warnings"], [])
        self.assertTrue(any("clear of --subject-box" in n for n in info["notes"]))

    def test_explicit_side_warns_with_a_crop_that_works(self):
        import re
        img = face_scene()
        _, info = thumb.render(img, "ONE TAKE", "NO SCRIPT", size="720p", subject_box=FACE, text_side="left")
        self.assertEqual(info["text_side"], "left")  # respected, only warned
        w = [x for x in info["warnings"] if "darkens --subject-box" in x]
        self.assertEqual(len(w), 1, info["warnings"])
        m = re.search(r"--crop ([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+) --text-side (left|right)", w[0])
        self.assertIsNotNone(m, w[0])
        crop = tuple(float(v) for v in m.groups()[:4])
        _, info = thumb.render(img, "ONE TAKE", "NO SCRIPT", size="720p", subject_box=FACE, text_side=m.group(5),
                               crop=crop)
        self.assertFalse(any("--subject-box" in x for x in info["warnings"]), info["warnings"])
        self.assertGreaterEqual(info["subject_box"]["visibility"], 0.9)  # what the suggestion promises

    def test_tall_formats_center_on_the_box(self):
        img = busy_right()
        box = (0.08, 0.30, 0.22, 0.55)
        for aspect in ("9:16", "4:5"):
            _, info = thumb.render(img, "SIMON", "No hit", aspect=aspect, subject_box=box)
            self.assertGreaterEqual(info["subject_box"]["inside"], 0.95, aspect)
            self.assertFalse(warned(info, "of --subject-box is"), info["warnings"])

    def test_type_band_moves_to_the_top_only_when_it_clears_the_box(self):
        img = Image.new("RGB", (1920, 1080), (70, 64, 58))
        _, info = thumb.render(img, "SIMON", "No hit", aspect="9:16", subject_box=(0.42, 0.40, 0.58, 0.62))
        self.assertEqual(info["text_side"], "top")
        self.assertLess(info["subject_box"]["heavy"], 0.15)
        self.assertFalse(warned(info, "--subject-box"), info["warnings"])
        _, info = thumb.render(img, "SIMON", "No hit", aspect="9:16", subject_box=(0.42, 0.16, 0.58, 0.34))
        self.assertEqual(info["text_side"], "bottom")  # a face in the top third keeps the default band
        self.assertFalse(warned(info, "--subject-box"), info["warnings"])
        _, info = thumb.render(img, "SIMON", "No hit", aspect="9:16")
        self.assertEqual(info["text_side"], "bottom")

    def test_warnings_for_a_box_out_of_frame_or_under_the_type(self):
        img = busy_right()
        _, info = thumb.render(img, "SIMON", "No hit", aspect="9:16", subject_box=(0.08, 0.30, 0.22, 0.55), focus=1.0)
        self.assertTrue(warned(info, "of --subject-box is inside the Reels/Shorts safe zone"), info["warnings"])
        self.assertTrue(warned(info, "--focus"), info["warnings"])
        _, info = thumb.render(face_scene(), "ONE TAKE", "NO SCRIPT", size="720p", subject_box=(0.02, 0.35, 0.30, 0.65),
                               text_side="left")
        self.assertTrue(warned(info, "the type overlaps --subject-box on 16:9: use --text-side right"), info["warnings"])
        self.assertGreater(info["subject_box"]["glyphs"], 0.02)

    def test_wide_capture_puts_the_box_away_from_the_type(self):
        img = Image.new("RGB", (2592, 1080), (70, 64, 58))
        img.paste(noise(260, 400, 90, 230), (1166, 200))  # a face in the middle of a 2.4:1 frame
        box = (1166 / 2592.0, 200 / 1080.0, 1426 / 2592.0, 600 / 1080.0)
        _, info = thumb.render(img, "SIMON", "No hit", size="720p", subject_box=box, text_side="left")
        r = info["subject_box"]["rect"]
        self.assertGreater((r[0] + r[2]) / 2.0, 1280 * 0.6)
        self.assertGreater(info["subject_box"]["visibility"], 0.9)
        self.assertFalse(warned(info, "--subject-box"), info["warnings"])

    def test_creator_demo_with_subject_box_in_every_format(self):
        import re
        photo = os.path.join(ROOT, "demo", "creator-photo.jpg")
        if not os.path.exists(photo):
            self.skipTest("demo files not present")
        with tempfile.TemporaryDirectory() as d:
            code, text = run_cli([photo, "--title", "ONE TAKE", "--label", "NO SCRIPT", "--style", "sans",
                                  "--title-font", "outfit@Bold", "--label-font", "outfit@SemiBold",
                                  "--subject-box", "0.30,0.12,0.42,0.39", "--aspect", "all", "-o", os.path.join(d, "c")])
        self.assertEqual(code, 0, text)
        self.assertNotIn("warning:", text)  # no --crop needed: the type takes the side away from the face
        lines = re.findall(r"subject box: (\d+)% (?:in frame|in the safe zone), (\d+)% visible", text)
        self.assertEqual(len(lines), 6)  # A and B in three formats
        for inside, visible in lines:
            self.assertGreaterEqual(int(inside), 90)
            self.assertGreaterEqual(int(visible), 90)


def scene(w, h, box=None, bars=0, seed=0, color=(120, 140, 160)):
    """Mid-gray noisy frame with a bright textured "face" at box (fractions) and optional 16-level pillarbox
    bars `bars` px wide on each side."""
    rng = np.random.default_rng(seed)
    a = np.clip(rng.normal(0, 18, (h, w, 3)) + np.array(color), 0, 255)
    if box:
        x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
        a[y0:y1, x0:x1] = np.clip(rng.normal(0, 60, (y1 - y0, x1 - x0, 3)) + 200, 0, 255)
    if bars:
        a[:, :bars] = 16
        a[:, w - bars:] = 16
    return Image.fromarray(a.astype(np.uint8))


def box_warnings(info):
    return [w for w in info["warnings"] if "--subject-box" in w]


def crop_advice(info):
    import re
    for w in info["warnings"]:
        m = re.search(r"try --crop ([0-9.,]+) --text-side (left|right)", w)
        if m:
            return tuple(float(v) for v in m.group(1).split(",")), m.group(2)
    return None


def fix_of(warning):
    return warning.split(": ", 1)[1]


class SubjectBoxAdvice(unittest.TestCase):
    """The fixes the --subject-box warnings name work when followed, and agree with each other."""

    def test_crop_advice_with_a_crop_follows_the_trimmed_bars(self):
        box = (0.22, 0.25, 0.32, 0.6)
        img = scene(1920, 1080, box=box, bars=280, seed=280)
        _, info = thumb.render(img, "DAWN", subject_box=box, text_side="left", crop=(0.0, 0.0, 1.0, 0.98))
        sug = crop_advice(info)
        self.assertIsNotNone(sug, info["warnings"])
        self.assertEqual(sug[0][1:4:2], (0.0, 0.98))  # the re-run keeps the crop's height
        _, info = thumb.render(img, "DAWN", subject_box=box, text_side=sug[1], crop=sug[0])
        self.assertEqual(box_warnings(info), [])  # the shade warning is gone, not the same crop again
        self.assertGreaterEqual(info["subject_box"]["visibility"], 0.9)

    def test_tall_portrait_crop_moves_the_face_above_the_band(self):
        box = (0.3, 0.45, 0.7, 0.6)
        img = scene(1080, 3600, box=box, seed=7)
        _, info = thumb.render(img, "DAWN", "No hit", subject_box=box, aspect="9:16")
        self.assertEqual(box_warnings(info), [])
        self.assertGreaterEqual(info["subject_box"]["visibility"], 0.9)
        self.assertGreaterEqual(info["subject_box"]["inside"], 0.9)  # inside the Reels safe zone
        self.assertTrue(any("crop moved up or down" in n for n in info["notes"]), info["notes"])

    def test_shade_and_overlap_name_one_fix_and_no_useless_side_switch(self):
        box = (0.3, 0.1, 0.7, 0.9)  # centered: the other side is no better
        img = scene(1920, 1080, box=box, seed=2)
        fixes = {}
        for side in ("auto", "right"):
            _, info = thumb.render(img, "DAWN", subject_box=box, text_side=side, size="720p")
            ws = box_warnings(info)
            shade = [w for w in ws if "darkens" in w]
            overlap = [w for w in ws if "overlaps" in w]
            self.assertEqual(len(shade), 1, ws)
            self.assertEqual(len(overlap), 1, ws)
            self.assertEqual(fix_of(shade[0]).split("): ")[-1], fix_of(overlap[0]), ws)  # one consistent fix
            self.assertNotIn("use --text-side", fix_of(overlap[0]))
            fixes[side] = crop_advice(info)
            self.assertIsNotNone(fixes[side], ws)
            self.assertEqual(fixes[side][1], info["text_side"])  # the crop keeps the side, no ping-pong
        img = scene(1920, 1080, box=(0.15, 0.1, 0.85, 0.9), seed=3)  # nothing clears it
        _, info = thumb.render(img, "DAWN", subject_box=(0.15, 0.1, 0.85, 0.9), size="720p")
        ws = box_warnings(info)
        self.assertTrue(ws)
        self.assertFalse(any("--text-side" in w for w in ws), ws)

    def test_wide_capture_with_focus_gets_the_focus_fix(self):
        import re
        box = (0.45, 0.2, 0.55, 0.6)
        img = scene(3840, 1080, box=box, seed=45)
        _, info = thumb.render(img, "DAWN", subject_box=box, focus=0.5, text_side="left", size="720p")
        w = [x for x in info["warnings"] if "darkens --subject-box" in x]
        self.assertEqual(len(w), 1, info["warnings"])
        m = re.search(r"drop --focus \(or set --focus ([0-9.]+)\)", w[0])
        self.assertIsNotNone(m, w[0])
        for focus in (float(m.group(1)), None):
            _, info = thumb.render(img, "DAWN", subject_box=box, focus=focus, text_side="left", size="720p")
            self.assertEqual(box_warnings(info), [], focus)

    def test_inset_and_subject_box_together(self):
        img = scene(1920, 1080, box=(0.42, 0.2, 0.55, 0.6))
        inset = scene(800, 450, seed=9, color=(200, 60, 60))
        ib, sb = (0.6, 0.2, 0.95, 0.7), (0.42, 0.2, 0.55, 0.6)
        _, info = thumb.render(img, "DAWN", inset=inset, inset_box=ib, subject_box=sb, size="720p")
        sug = crop_advice(info)
        self.assertIsNotNone(sug, info["warnings"])
        self.assertLessEqual(sug[0][0], ib[0])  # the crop keeps the whole inset
        self.assertGreaterEqual(sug[0][2], ib[2])
        _, info = thumb.render(img, "DAWN", inset=inset, inset_box=ib, subject_box=sb, crop=sug[0],
                               text_side=sug[1], size="720p")
        self.assertEqual(box_warnings(info), [])
        self.assertFalse(warned(info, "inset"), info["warnings"])
        # the inset and the face on opposite sides of the frame: no crop keeps both clear of the type
        _, info = thumb.render(img, "DAWN", inset=inset, inset_box=(0.1, 0.2, 0.5, 0.7),
                               subject_box=(0.6, 0.2, 0.8, 0.6), size="720p")
        self.assertTrue(warned(info, "no --crop keeps both the inset and --subject-box"), info["warnings"])
        self.assertIsNone(crop_advice(info))
        # tall: the crop centers the face, not the inset, and says so
        _, info = thumb.render(img, "DAWN", inset=inset, inset_box=(0.05, 0.2, 0.3, 0.7), subject_box=(0.7, 0.2, 0.8, 0.5),
                               aspect="4:5")
        self.assertTrue(any("rather than the inset" in n for n in info["notes"]), info["notes"])
        self.assertGreaterEqual(info["subject_box"]["inside"], 0.95)

    def test_band_choice_counts_the_reels_safe_zone(self):
        box = (0.3, 0.75, 0.7, 0.9)  # too low for the safe zone with either band
        img = scene(1080, 2400, box=box, seed=7)
        _, info = thumb.render(img, "DAWN", "No hit", subject_box=box, aspect="9:16")
        self.assertEqual(info["text_side"], "bottom")
        self.assertFalse(any("type band at the top" in n for n in info["notes"]), info["notes"])
        self.assertTrue(warned(info, "inside the Reels/Shorts safe zone"), info["warnings"])

    def test_band_choice_weighs_the_middle_of_the_box(self):
        img = Image.new("RGB", (1920, 1080), (70, 64, 58))
        # the bottom band shades the mouth and chin, the top band only the hair: the whole box scores about
        # the same either way, its middle (eyes to mouth) clearly better at the top
        _, info = thumb.render(img, "SIMON", "No hit", aspect="9:16", subject_box=(0.42, 0.20, 0.58, 0.66))
        self.assertEqual(info["text_side"], "top")
        self.assertTrue(any("of its middle in view" in n for n in info["notes"]), info["notes"])

    def test_dawnwalker_flips_and_ds2_stays(self):
        art, logo, ds2 = (os.path.join(ROOT, "demo", n) for n in ("dawnwalker-art.jpg", "dawnwalker-logo.png",
                                                                   "ds2-capture.jpg"))
        if not all(os.path.exists(p) for p in (art, logo, ds2)):
            self.skipTest("demo files not present")
        with Image.open(art) as a, Image.open(logo) as lg:
            a.load()
            lg.load()
            for lead in ("title", "label"):
                _, info = thumb.render(a, "", "OP Skills", logo=lg, lead=lead, aspect="9:16",
                                       subject_box=(0.40, 0.28, 0.60, 0.62))
                self.assertEqual(info["text_side"], "top", lead)
            _, info = thumb.render(a, "", "OP Skills", logo=lg, lead="title", aspect="9:16",
                                   subject_box=(0.40, 0.28, 0.60, 0.62), band="bottom")
            self.assertEqual(info["text_side"], "bottom")  # --band wins
            self.assertTrue(warned(info, "use --band top"), info["warnings"])
            self.assertFalse(warned(info, "--crop the capture"), info["warnings"])
        with Image.open(ds2) as d:
            d.load()
            kw = dict(title="DEATH STRANDING 2", label="4K · Max settings", title_font="outfit@Light",
                      title_tracking=0.12, focus=0.46, lead="title", subject_box=(0.43, 0.13, 0.50, 0.30))
            for aspect in ("4:5", "9:16"):
                _, info = thumb.render(d, aspect=aspect, **kw)
                self.assertEqual(info["text_side"], "bottom", aspect)
                self.assertEqual(info["warnings"], [], aspect)
            _, info = thumb.render(d, aspect="9:16", band="top", **kw)
            self.assertEqual(info["text_side"], "top")
            self.assertTrue(warned(info, "use --band bottom, which keeps it clear"), info["warnings"])

    def test_band_flag_is_validated_and_only_moves_tall_single_layouts(self):
        img = half_busy(1280, 720)
        _, info = thumb.render(img, "SIMON", "No hit", aspect="4:5", band="top")
        self.assertEqual(info["text_side"], "top")
        top_rows = [b[3] for n, b in info["elements"]]
        self.assertLess(max(top_rows), info["size"][1] * 0.5)
        a, _ = thumb.render(img, "SIMON", "No hit", band="top", size="720p")
        b, _ = thumb.render(img, "SIMON", "No hit", size="720p")
        self.assertEqual(a.tobytes(), b.tobytes())  # 16:9 has no band
        _, info = thumb.render(None, "SIMON", panels=[img, img], aspect="9:16", band="top")
        self.assertTrue(any("--band top is ignored with --panel" in n for n in info["notes"]), info["notes"])
        with self.assertRaisesRegex(ValueError, "band must be one of"):
            thumb.render(img, "SIMON", band="middle")
        self.assertEqual(thumb.check_setting("band", "bottom"), "bottom")
        with self.assertRaises(ValueError):
            thumb.check_setting("band", "up")
        with tempfile.TemporaryDirectory() as d:
            img.save(os.path.join(d, "cap.png"))
            code, text = run_cli(["cap.png", "--title", "X", "--band", "middle", "-o", "t"], cwd=d)
            self.assertEqual(code, 2)
            self.assertIn("must be one of auto, top, bottom", text)
            with open(os.path.join(d, thumb.CHANNEL_FILE), "w") as f:
                json.dump({"band": "top", "aspect": "9:16", "lead": "title"}, f)
            code, text = run_cli(["cap.png", "--title", "X", "-o", "t"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn("type top", text)
            code, text = run_cli(["cap.png", "--title", "X", "--band", "auto", "-o", "t"], cwd=d)
            self.assertIn("type bottom", text)  # the flag wins over the channel file

    def test_default_title_line_names_the_exact_reproduction(self):
        img = half_busy(1280, 720)
        a, info = thumb.render(img, "SIMON", "No hit", lead="title")
        self.assertEqual(info["title_source"], "default serif font (bundled playfair@Bold, tracking 0.06 em)")
        b, _ = thumb.render(img, "SIMON", "No hit", lead="title", title_font="playfair@Bold", title_tracking=0.06)
        self.assertEqual(a.tobytes(), b.tobytes())


class DurationBadge(unittest.TestCase):
    """The duration badge corner comes from the badge the previews draw, and right-side type stays clear of it."""

    def test_zone_is_the_badge_on_the_168px_tile(self):
        tile = Image.new("RGB", (168, thumb.tile_height(168)), (255, 255, 255))
        thumb.badge(tile)
        ys, xs = np.nonzero(np.asarray(tile.convert("L")) < 128)
        for w, h in ((1280, 720), (3840, 2160)):
            z = thumb.Canvas(w, h).timestamp_zone
            self.assertLessEqual(z[0], xs.min() / 168.0 * w)  # every badge pixel is inside the zone
            self.assertLessEqual(z[1], ys.min() / float(tile.height) * h)
            self.assertEqual(z[2:], (w, h))
        x0, y0, _, _ = thumb.badge_rect(168, thumb.tile_height(168))
        self.assertEqual(thumb.Canvas(1280, 720).timestamp_zone[:2],
                         (int(1280 * int(x0) / 168), int(720 * int(y0) / thumb.tile_height(168))))
        self.assertEqual(thumb.Canvas(1080, 1920).timestamp_zone, (0, 0, 0, 0))

    def test_right_side_type_keeps_clear_of_the_badge_at_168px_and_up(self):
        out, info = thumb.render(half_busy(busy="left"), "ONE\\nTWO\\nSIX", "No hit", kicker="Expedition 33",
                                 subtitle="The long night", text_side="right", size="720p")
        self.assertEqual(info["text_side"], "right")
        bottom = max(b[3] for _, b in info["elements"])
        self.assertGreater(bottom, 720 * 0.70)  # tall enough to reach the limit
        for w in (168, 320, 480):
            h = thumb.tile_height(w)
            badge = thumb.badge_rect(w, h)
            for name, b in info["elements"]:
                scaled = tuple(v * w / 1280.0 for v in b)
                self.assertFalse(thumb.overlaps(scaled, badge), (w, name, scaled, badge))
        self.assertFalse(warned(info, "duration badge"), info["warnings"])

    def test_small_preview_text_keeps_its_word_spaces(self):
        gap = thumb.preview_text_width("squint test", 13) - thumb.preview_text_width("squinttest", 13)
        # Outfit's own space is 2.5 px at 13 px; the preview minimum holds with or without raqm kerning
        self.assertGreaterEqual(gap, thumb.PREVIEW_WORD_SPACE * 13 - 0.01)
        from PIL import ImageFont
        basic = ImageFont.truetype(thumb.bundled_spec("outfit").partition("@")[0], 39,
                                   layout_engine=ImageFont.Layout.BASIC)  # how macOS CI's Pillow lays text out
        gap_basic = thumb.text_width(basic, "squint test", 0.03 * 39, 0.3 * 39) - thumb.text_width(basic, "squinttest", 0.03 * 39, 0.3 * 39)
        self.assertGreaterEqual(gap_basic, 0.3 * 39 - 0.01)
        tracked = thumb.preview_text_width("24:13", 12)
        plain = thumb.text_width(thumb.preview_font(36), "24:13", 0) / 3
        self.assertGreater(tracked - plain, 1.0)  # the badge figures and colon get air at 12 px
        m, dx, base = thumb.preview_text_mask("24:13", 12)
        self.assertGreater(np.asarray(m).max(), 200)
        self.assertLess(abs(m.getbbox()[2] - dx - thumb.preview_text_width("24:13", 12)), 3)


class SystemFonts(unittest.TestCase):
    """Bundled fonts by default, the machine's own only with --system-fonts. A fake system index holds
    copies of bundled files under the names of Office's Bodoni MT and Windows' Bahnschrift."""

    def setUp(self):
        import shutil
        self.tmp = tempfile.mkdtemp()
        self.bodoni = os.path.join(self.tmp, "BOD_R.TTF")
        self.bahn = os.path.join(self.tmp, "bahnschrift.ttf")
        shutil.copy(os.path.join(thumb.FONT_ROOT, "slab", "ZillaSlab-Bold.ttf"), self.bodoni)
        shutil.copy(os.path.join(thumb.FONT_ROOT, "oswald", "Oswald-VariableFont_wght.ttf"), self.bahn)  # has SemiBold, Bold
        self.saved = thumb._font_index
        thumb._font_index = {"bod_r.ttf": self.bodoni, "bahnschrift.ttf": self.bahn}

    def tearDown(self):
        import shutil
        thumb._font_index = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bundled(self, spec):
        folder = os.path.dirname(os.path.dirname(spec.partition("@")[0]))
        return os.path.normcase(folder) == os.path.normcase(thumb.FONT_ROOT)

    def test_default_is_bundled_even_with_bodoni_and_bahnschrift_installed(self):
        _, info = thumb.render(half_busy(), "SIMON", "Parry only", lead="title")
        title, label = info["fonts"]
        self.assertTrue(self.bundled(title) and self.bundled(label), info["fonts"])
        self.assertIn("PlayfairDisplay", title)
        self.assertTrue(title.endswith("@Bold"), title)
        self.assertIn("Outfit", label)
        self.assertTrue(label.endswith("@SemiBold"), label)
        # the exact reproduction: --title-font playfair@Bold alone takes the catalog's 0.04 em
        self.assertEqual(info["title_source"], "default serif font (bundled playfair@Bold, tracking 0.06 em)")
        _, info = thumb.render(half_busy(), "SIMON", "Parry only", style="sans", lead="title")
        self.assertEqual(info["title_source"], "default sans font (bundled outfit@Bold)")  # catalog tracking matches
        self.assertIn("Outfit", info["fonts"][0])
        self.assertTrue(info["fonts"][0].endswith("@Bold"))

    def test_system_fonts_pick_the_installed_faces(self):
        _, info = thumb.render(half_busy(), "SIMON", "Parry only", lead="title", system_fonts=True)
        self.assertEqual(info["fonts"], (self.bodoni, self.bahn + "@SemiBold"))
        self.assertEqual(info["title_source"], "default serif font (BOD_R.TTF)")
        _, info = thumb.render(half_busy(), "SIMON", "Parry only", style="sans", lead="title", system_fonts=True)
        self.assertEqual(info["fonts"][0], self.bahn + "@Bold")
        # a named font still wins over either chain
        _, info = thumb.render(half_busy(), "SIMON", "Parry only", title_font="cinzel", label_font="outfit@Bold",
                               lead="title", system_fonts=True)
        self.assertIn("Cinzel", info["fonts"][0])
        self.assertTrue(info["fonts"][1].endswith("Outfit-VariableFont_wght.ttf@Bold"), info["fonts"][1])

    def test_cli_flag_and_channel_key(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "cap.png")
            half_busy(1280, 720).save(src)
            code, text = run_cli([src, "--title", "SIMON", "-o", os.path.join(d, "a")])
            self.assertEqual(code, 0, text)
            self.assertNotIn("BOD_R", text)
            self.assertNotIn("(system fonts)", text)
            code, text = run_cli([src, "--title", "SIMON", "--system-fonts", "-o", os.path.join(d, "b")])
            self.assertEqual(code, 0, text)
            self.assertIn("fonts: title %s; label %s@SemiBold (system fonts)" % (self.bodoni, self.bahn), text)
            with open(os.path.join(d, thumb.CHANNEL_FILE), "w") as f:
                json.dump({"system_fonts": True}, f)
            code, text = run_cli(["cap.png", "--title", "SIMON", "-o", "c"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn("title type: default serif font (BOD_R.TTF)", text)
            self.assertIn("(system fonts)", text)

    def test_system_fonts_must_be_a_bool(self):
        self.assertIs(thumb.check_setting("system_fonts", True), True)
        for bad in ("yes", "true", 1):
            with self.assertRaises(ValueError, msg=repr(bad)):
                thumb.check_setting("system_fonts", bad)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            with open(path, "w") as f:
                json.dump({"system_fonts": "yes"}, f)
            with self.assertRaises(ValueError):
                thumb.load_channel(path)

    def test_previews_use_the_bundled_label_font_even_with_system_fonts(self):
        used, in_export = [], [False]
        real_load, real_export = thumb.load_font, thumb.export

        def load(spec, size):
            if in_export[0]:
                used.append(spec)
            return real_load(spec, size)

        def export(*a, **k):
            in_export[0] = True
            try:
                return real_export(*a, **k)
            finally:
                in_export[0] = False
        thumb.load_font, thumb.export = load, export
        try:
            with tempfile.TemporaryDirectory() as d:
                src = os.path.join(d, "cap.png")
                half_busy(1280, 720).save(src)
                code, text = run_cli([src, "--title", "SIMON", "--label", "No hit", "--system-fonts", "--aspect",
                                      "all", "-o", os.path.join(d, "t")])
        finally:
            thumb.load_font, thumb.export = real_load, real_export
        self.assertEqual(code, 0, text)
        self.assertIn("BOD_R.TTF", text)  # the thumbnails themselves used the system faces
        self.assertTrue(used)  # badge and captions on all three kinds of sheet
        for spec in used:
            self.assertTrue(self.bundled(spec) and "Outfit" in spec and spec.endswith("@SemiBold"), spec)



class ReviewArgon(unittest.TestCase):
    """Regression tests from the v0.8.1 outside review."""

    def test_push_only_softens_behind_a_subject(self):
        bg = half_busy(1280, 720)
        plain, _ = thumb.render(bg, "SIMON", "No hit", lead="title")
        pushed, _ = thumb.render(bg, "SIMON", "No hit", lead="title", push=0.5)
        self.assertTrue(np.array_equal(np.asarray(plain), np.asarray(pushed)))  # no cut-out, nothing to soften
        with tempfile.TemporaryDirectory() as d:
            bg.save(os.path.join(d, "cap.png"))
            run_cli(["cap.png", "--title", "SIMON", "--lead", "title", "-o", "plain"], cwd=d)
            with open(os.path.join(d, thumb.CHANNEL_FILE), "w") as f:
                json.dump({"push": 0.5}, f)  # as copied from references/channel.example.json
            run_cli(["cap.png", "--title", "SIMON", "--lead", "title", "-o", "chan"], cwd=d)
            with Image.open(os.path.join(d, "plain.jpg")) as a, Image.open(os.path.join(d, "chan.jpg")) as b:
                self.assertTrue(np.array_equal(np.asarray(a), np.asarray(b)))

    def test_contrast_advice_names_the_flag_that_sets_the_color(self):
        _, info = thumb.render(half_busy(), "SIMON", "Parry only", title_color="#1b2a8a", lead="title",
                               text_side="left")
        bad = [w for w in info["warnings"] if w.startswith("the title has")]
        self.assertTrue(bad)
        self.assertIn("--title-color", bad[0])
        self.assertNotIn("--accent", bad[0])
        self.assertEqual(thumb.color_flag("hook"), "--title-color")
        self.assertEqual(thumb.color_flag("label box"), "--accent")

    def test_summary_and_warning_print_the_same_failing_ratio(self):
        self.assertEqual(thumb.shown_ratio(4.46), 4.4)  # never a passing-looking 4.5
        self.assertEqual("%.1f" % thumb.shown_ratio(4.46), "4.4")
        self.assertEqual("%.1f" % thumb.shown_ratio(4.5), "4.5")
        self.assertEqual("%.1f" % thumb.shown_ratio(14.53), "14.5")

    def test_rembg_runs_once_per_subject_across_variants_and_formats(self):
        import types
        calls = []

        def remove(img):
            calls.append(1)
            out = img.convert("RGBA")
            a = Image.new("L", out.size, 0)
            ImageDraw.Draw(a).ellipse((20, 20, out.width - 20, out.height - 20), fill=255)
            out.putalpha(a)
            return out

        saved = sys.modules.get("rembg")
        sys.modules["rembg"] = types.SimpleNamespace(remove=remove)
        try:
            with tempfile.TemporaryDirectory() as d:
                half_busy(1280, 720).save(os.path.join(d, "cap.png"))
                Image.new("RGB", (400, 600), (90, 110, 150)).save(os.path.join(d, "me.jpg"))  # no transparency
                code, text = run_cli(["cap.png", "--subject", "me.jpg", "--title", "X", "--label", "Y",
                                      "--aspect", "all", "-o", "t"], cwd=d)
                self.assertEqual(code, 0, text)
        finally:
            if saved is None:
                sys.modules.pop("rembg", None)
            else:
                sys.modules["rembg"] = saved
        self.assertEqual(len(calls), 1)  # not once per variant and format (6)


if __name__ == "__main__":
    unittest.main()
