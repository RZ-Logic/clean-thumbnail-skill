"""Tests for the v0.9.1 shade fixes in scripts/thumb.py. Run: python -m unittest discover -s tests

The auto shade (--shade not given) is the lightest that keeps every element readable and on 16:9 follows the
type block; flat fills stay off YouTube's dark page; contrast is scored per slice of a line; the previews outline
--subject-box and --inset-box; a --subject render grades its picture once."""
import json
import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "skill", "clean-thumbnail", "scripts"))
sys.path.insert(0, HERE)

import thumb  # noqa: E402
from test_thumb import cutout, grainy, half_busy, noise, run_cli, warned  # noqa: E402

PAGE = (15, 15, 15)  # YouTube's dark-theme page


def wcag(c):
    return float(thumb.wcag_luminance(np.array(c, np.float32) / 255.0))


def ratio(a, b):
    la, lb = wcag(a), wcag(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def dark_frame(w=1280, h=720, seed=0):
    """A dim, softly textured capture (lighter than its own shadow tone): type reads on it with little shade."""
    rng = np.random.default_rng(seed)
    a = np.clip(rng.normal(0, 6, (h, w, 1)) + np.array([60, 54, 48]), 0, 255)
    return Image.fromarray(a.astype(np.uint8), "RGB")


def edge_merge_share(img):
    """Share of a thumbnail's outer ring (at 480 px wide) under 1.1:1 against the dark page."""
    a = np.asarray(img.convert("RGB").resize((480, 270), Image.BOX), np.float32) / 255.0
    ring = np.concatenate([a[:2].reshape(-1, 3), a[-2:].reshape(-1, 3), a[2:-2, :2].reshape(-1, 3),
                           a[2:-2, -2:].reshape(-1, 3)])
    le, lp = thumb.wcag_luminance(ring), wcag(PAGE)
    return float(((np.maximum(le, lp) + 0.05) / (np.minimum(le, lp) + 0.05) < 1.1).mean())


def element_box(info, name):
    return [b for n, b in info["elements"] if n == name]


class AutoShade(unittest.TestCase):
    """visual-design-2: the shade is only as dark as the type needs, and on 16:9 follows the type block."""

    def test_dark_frame_gets_a_lighter_shade_than_the_fixed_one(self):
        img = dark_frame()
        auto, ia = thumb.render(img, "SIMON", "Parry only", lead="title", text_side="left")
        fixed, i88 = thumb.render(img, "SIMON", "Parry only", lead="title", text_side="left", shade=0.88)
        self.assertLess(ia["shade"], 0.88)
        self.assertEqual(i88["shade"], 0.88)
        self.assertTrue(any(n.startswith("shade %.2f (auto)" % ia["shade"]) for n in ia["notes"]), ia["notes"])
        self.assertFalse(any("(auto)" in n for n in i88["notes"]), i88["notes"])
        a, f = np.asarray(auto, np.float32), np.asarray(fixed, np.float32)
        col = slice(int(0.46 * a.shape[1]), int(0.55 * a.shape[1]))  # just past the type, inside the old fade
        self.assertGreater(a[:, col].mean(), f[:, col].mean() + 2)
        self.assertGreaterEqual(ia["contrast"], thumb.SHADE_TARGET - 0.05)

    def test_bright_frame_still_reaches_the_target(self):
        img = Image.new("RGB", (1280, 720), (236, 232, 222))
        _, info = thumb.render(img, "SIMON", "Parry only", lead="title", text_side="left")
        _, fixed = thumb.render(img, "SIMON", "Parry only", lead="title", text_side="left", shade=0.88)
        self.assertGreaterEqual(info["contrast"], min(thumb.SHADE_TARGET, fixed["contrast"]) - 0.05)
        self.assertLessEqual(info["shade"], 0.88)
        self.assertGreater(info["shade"], 0.5)  # white on a white frame needs real shade
        self.assertFalse(warned(info, "contrast"), info["warnings"])

    def test_never_less_contrast_than_the_fixed_shade_up_to_the_target(self):
        for img in (half_busy(1280, 720), grainy(1280, 720, color=(150, 120, 80)), dark_frame(seed=3)):
            for aspect in ("16:9", "4:5", "9:16"):
                _, a = thumb.render(img, "WAYFINDERS", "No hit", lead="title", aspect=aspect)
                _, f = thumb.render(img, "WAYFINDERS", "No hit", lead="title", aspect=aspect, shade=0.88)
                self.assertGreaterEqual(a["contrast"], min(thumb.SHADE_TARGET, f["contrast"]) - 0.1, aspect)
                self.assertTrue(thumb.SHADE_MIN <= a["shade"] <= 0.97, (aspect, a["shade"]))

    def test_short_block_shades_less_of_the_frame(self):
        img = Image.new("RGB", (1280, 720), (128, 128, 128))
        dark = {}
        for title in ("IO", "WAYFINDERS"):
            out, info = thumb.render(img, title, lead="title", text_side="left", shade=None)
            rows = np.asarray(out.convert("L"), np.float32)[: int(0.2 * out.height)]  # above the type
            cols = rows.mean(axis=0)
            dark[title] = int((cols < cols[-40:].mean() - 3).sum())  # columns darker than the open image side
            self.assertGreater(info["text_box"][2] - info["text_box"][0], 0)
        self.assertLess(dark["IO"], dark["WAYFINDERS"] - 100)
        fixed = {}
        for title in ("IO", "WAYFINDERS"):
            out, _ = thumb.render(img, title, lead="title", text_side="left", shade=0.6)
            fixed[title] = np.asarray(out.convert("L"), np.float32)[:100].mean(axis=0)
        self.assertTrue(np.array_equal(fixed["IO"], fixed["WAYFINDERS"]))  # a fixed shade keeps the fixed geometry

    def test_fixed_geometry_is_unchanged(self):
        cv = thumb.Canvas(1920, 1080)
        alpha = thumb.column_alpha(cv, 0.88, "left")
        self.assertAlmostEqual(float(alpha.mean()), 0.442, places=3)
        self.assertAlmostEqual(float(alpha[cv.margin + int(cv.col_w * 0.75) - 1]), 0.88, places=3)

    def test_cli_default_is_auto_and_a_channel_shade_fixes_it(self):
        with tempfile.TemporaryDirectory() as d:
            dark_frame().save(os.path.join(d, "cap.png"))
            code, text = run_cli(["cap.png", "--title", "X", "--lead", "title", "-o", "a"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn("(auto): the lightest that keeps every element at 7:1", text)
            code, text = run_cli(["cap.png", "--title", "X", "--lead", "title", "--shade", "0.88", "-o", "b"], cwd=d)
            self.assertNotIn("(auto)", text)
            with open(os.path.join(d, "ch.json"), "w") as f:
                json.dump({"shade": 0.88}, f)
            code, text = run_cli(["cap.png", "--title", "X", "--lead", "title", "--channel", "ch.json", "-o", "c"], cwd=d)
            self.assertNotIn("(auto)", text)
            with Image.open(os.path.join(d, "b.jpg")) as b, Image.open(os.path.join(d, "c.jpg")) as c:
                self.assertTrue(np.array_equal(np.asarray(b), np.asarray(c)))
            self.assertIsNone(thumb.DEFAULTS["shade"])

    def test_tall_band_and_panels_adapt_too(self):
        img = dark_frame(1920, 1080, seed=5)
        for aspect in ("4:5", "9:16"):
            _, info = thumb.render(img, "SIMON", "No hit", lead="title", aspect=aspect)
            self.assertLess(info["shade"], 0.88, aspect)
        a, b = dark_frame(seed=6), dark_frame(seed=7)
        _, info = thumb.render(None, "GAMING", "Which is faster", panels=[a, b], panel_labels=["A", "B"],
                               lead="title")
        self.assertLess(info["shade"], 0.88)
        self.assertGreaterEqual(info["contrast"], thumb.SHADE_TARGET - 0.05)

    def test_subject_box_beside_a_short_block_is_less_shaded(self):
        img = Image.new("RGB", (1280, 720), (150, 145, 140))
        box = (0.50, 0.30, 0.60, 0.55)
        _, auto = thumb.render(img, "IO", lead="title", text_side="left", subject_box=box)
        _, fixed = thumb.render(img, "IO", lead="title", text_side="left", subject_box=box, shade=0.88)
        self.assertGreater(auto["subject_box"]["visibility"], fixed["subject_box"]["visibility"] + 0.1)


class DarkFloor(unittest.TestCase):
    """visual-design-3 / S2: flat fills keep the thumbnail's edge on YouTube's dark page."""

    def test_background_tone_is_lifted_off_the_dark_page_keeping_its_hue(self):
        for color in ((2, 2, 2), (40, 28, 16), (14, 18, 40)):
            img = Image.new("RGB", (64, 64), color)
            bg = thumb.dark_tone(img)
            raw = thumb.dark_tone_pixels(np.asarray(img))
            self.assertGreaterEqual(ratio(bg, PAGE), 1.2 - 1e-3, (color, bg))
            self.assertLessEqual(max(bg), 48, bg)  # still near black
            self.assertEqual(int(np.argmax(bg)), int(np.argmax(raw)), (raw, bg))  # same dominant channel
            self.assertLessEqual(max(bg) - min(bg), max(raw) - min(raw) + 1, (raw, bg))  # no extra chroma
        light = Image.new("RGB", (64, 64), (200, 140, 60))
        self.assertGreater(thumb.dark_tone(light)[0], thumb.dark_tone(light)[2])  # warm stays warm

    def test_narrow_art_slab_keeps_the_edge(self):
        art = noise(500, 720, 40, 220)
        out, info = thumb.render(art, "ONE TAKE", "No script", lead="title", text_side="left")
        slab = out.getpixel((4, out.height // 2))
        self.assertGreaterEqual(ratio(slab, PAGE), 1.2 - 1e-3, slab)
        self.assertLess(edge_merge_share(out), 0.1)
        self.assertFalse(any("dark-theme page" in n for n in info["notes"]), info["notes"])

    def test_explicit_dark_bg_is_respected_with_a_note(self):
        art = noise(500, 720, 40, 220)
        out, info = thumb.render(art, "ONE TAKE", "No script", lead="title", text_side="left", bg="#070707")
        self.assertEqual(out.getpixel((4, out.height // 2)), (7, 7, 7))
        self.assertGreater(edge_merge_share(out), 0.4)
        notes = [n for n in info["notes"] if "dark-theme page" in n]
        self.assertEqual(len(notes), 1, info["notes"])
        self.assertIn("--bg #222222", notes[0])

    def test_solid_panel_from_the_image_is_lifted_too(self):
        out, info = thumb.render(dark_frame(), "SIMON", "No hit", lead="title", text_side="left", backdrop="solid")
        panel = out.getpixel((4, out.height // 2))
        self.assertGreaterEqual(ratio(panel, PAGE), 1.2 - 1e-3, panel)
        self.assertNotIn("shade", info)  # nothing is shaded on a solid backdrop


class PerSliceContrast(unittest.TestCase):
    """type-color-6: one letter on a bright patch fails its line."""

    def test_one_bright_letter_fails_the_element(self):
        canvas = Image.new("RGB", (600, 60), (20, 20, 20))
        canvas.paste((235, 232, 225), (545, 0, 600, 60))  # under the last of ten letters
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for i in range(10):
            d.rectangle((i * 60 + 8, 5, i * 60 + 52, 55), fill=(255, 255, 255, 255))
        (_, r, light, _), = thumb.element_contrast(canvas, layer, [("title", (0, 0, 600, 60))])
        self.assertTrue(light)
        self.assertLess(r, 1.5)
        canvas.paste((20, 20, 20), (545, 0, 600, 60))
        (_, r, _, _), = thumb.element_contrast(canvas, layer, [("title", (0, 0, 600, 60))])
        self.assertGreater(r, 15)

    def test_render_catches_and_fixes_the_letter(self):
        rng = np.random.default_rng(4)
        src = np.clip(rng.normal(0, 10, (720, 1280, 1)) + 30, 0, 255).repeat(3, 2).astype(np.uint8)
        kw = dict(lead="title", text_side="left")
        _, info = thumb.render(Image.fromarray(src), "WAYFINDERS", shade=0.4, **kw)
        (x0, y0, x1, y1), = element_box(info, "title")
        patched = src.copy()
        patched[y0:y1, int(x1 - 0.09 * (x1 - x0)):x1 + 4] = (235, 232, 225)
        _, info = thumb.render(Image.fromarray(patched), "WAYFINDERS", shade=0.4, **kw)
        self.assertIn("raised --shade to 0.97 for contrast", info["notes"])  # the last letter failed at 0.4
        _, plain = thumb.render(Image.fromarray(src), "WAYFINDERS", **kw)
        _, auto = thumb.render(Image.fromarray(patched), "WAYFINDERS", **kw)
        self.assertGreater(auto["shade"], plain["shade"] + 0.1)  # the auto shade answers to that letter

    def test_unfixable_letter_is_warned_about(self):
        rng = np.random.default_rng(5)
        src = np.clip(rng.normal(0, 10, (720, 1280, 1)) + 30, 0, 255).repeat(3, 2).astype(np.uint8)
        kw = dict(lead="title", text_side="left", bg=(225, 225, 225), shade=0.0)
        _, info = thumb.render(Image.fromarray(src), "WAYFINDERS", **kw)
        self.assertFalse(warned(info, "the title has"), info["warnings"])
        (x0, y0, x1, y1), = element_box(info, "title")
        src[y0:y1, int(x1 - 0.09 * (x1 - x0)):x1 + 4] = (235, 232, 225)
        _, info = thumb.render(Image.fromarray(src), "WAYFINDERS", **kw)
        self.assertTrue(warned(info, "the title has"), info["warnings"])


class PreviewBoxes(unittest.TestCase):
    """skill-docs-3: the previews outline --subject-box and --inset-box where they landed."""

    def test_sheet_gets_a_boxes_tile(self):
        img = grainy(1280, 720, color=(120, 110, 100))
        box = (0.62, 0.2, 0.74, 0.45)
        out, info = thumb.render(img, "SIMON", "No hit", lead="title", text_side="left", subject_box=box)
        self.assertEqual(out.info.get("boxes"), [("subject box", tuple(info["subject_box"]["rect"]))])
        plain = thumb.preview_sheet(thumb.render(img, "SIMON", "No hit", lead="title", text_side="left")[0])
        sheet = thumb.preview_sheet(out)
        self.assertEqual(sheet.width, plain.width + 320 + 28)
        x0, y0, x1, y1 = info["subject_box"]["rect"]
        k = 320.0 / out.width
        tx = sheet.width - 28 - 320  # the boxes tile is the last one in the row
        a = np.asarray(sheet, np.int16)
        top = a[14 + int(round(y0 * k)), tx + int(x0 * k) + 4: tx + int(x1 * k) - 4]
        self.assertTrue((np.abs(top - np.array(thumb.BOX_COLORS["subject box"])).max(axis=1) <= 40).any())

    def test_inset_box_and_tall_previews(self):
        room = grainy(1280, 720, color=(60, 55, 50), sigma=5)
        out, info = thumb.render(room, "DLSS 5", "On a CRT", inset=noise(400, 300), inset_box=(0.5, 0.2, 0.9, 0.7),
                                 lead="title", text_side="left")
        self.assertEqual(out.info.get("boxes"), [("inset box", tuple(info["inset_rect"]))])
        for aspect, sheet in (("4:5", thumb.feed_preview), ("9:16", thumb.reels_preview)):
            img = grainy(1920, 1080, color=(120, 110, 100))
            with_box = thumb.render(img, "SIMON", "No hit", lead="title", aspect=aspect,
                                    subject_box=(0.4, 0.2, 0.55, 0.45))[0]
            without = thumb.render(img, "SIMON", "No hit", lead="title", aspect=aspect)[0]
            self.assertGreater(sheet(with_box).width, sheet(without).width, aspect)

    def test_export_writes_the_boxes_tile(self):
        img = grainy(1280, 720, color=(120, 110, 100))
        out, _ = thumb.render(img, "SIMON", "No hit", lead="title", text_side="left", subject_box=(0.62, 0.2, 0.74, 0.45))
        with tempfile.TemporaryDirectory() as d:
            files = thumb.export(out, os.path.join(d, "t"))
            with Image.open(files["preview"]) as im:
                self.assertEqual(im.width, thumb.preview_sheet(out).width)


class BadgeAt120(unittest.TestCase):
    """visual-design-6: the 120 px tile's badge covering type is reported, not silent."""

    def test_centered_panel_title_gets_the_note(self):
        a, b = grainy(1280, 720, color=(60, 70, 90)), grainy(1280, 720, color=(110, 90, 70))
        _, info = thumb.render(None, "PATH TRACING", "007 First Light", panels=[a, b], lead="title", style="sans")
        notes = [n for n in info["notes"] if "120 px tile" in n]
        self.assertEqual(len(notes), 1, info["notes"])
        self.assertIn("title", notes[0])
        self.assertFalse(warned(info, "120 px"))

    def test_left_type_and_posts_get_no_note(self):
        _, info = thumb.render(half_busy(1280, 720), "SIMON", "No hit", lead="title", text_side="left")
        self.assertFalse(any("120 px tile" in n for n in info["notes"]), info["notes"])
        _, info = thumb.render(half_busy(1280, 720), "SIMON", "No hit", lead="title", aspect="16:9", mode="post",
                               body="One line of body text.", text_side="right")
        self.assertFalse(any("120 px tile" in n for n in info["notes"]), info["notes"])


class GradeOnce(unittest.TestCase):
    """G6c: a --subject render with --push grades (and resizes) its picture once, whatever the shade does."""

    def count_grades(self, **kw):
        calls = []
        real = thumb.grade

        def counting(img, *a, **k):
            calls.append(img.size)
            return real(img, *a, **k)
        thumb.grade = counting
        try:
            thumb.render(grainy(1280, 720, color=(150, 120, 80)), "4 MONTHS", "Of silence", subjects=[cutout()],
                         lead="title", **kw)
        finally:
            thumb.grade = real
        return calls

    def test_one_grade_per_render(self):
        for kw in (dict(text_side="left"), dict(text_side="left", shade=0.4), dict(aspect="4:5"),
                   dict(aspect="9:16", shade=0.88), dict(text_side="left", backdrop="solid", bg="#202020")):
            self.assertEqual(len(self.count_grades(push=0.5, **kw)), 1, kw)


class DeadConstantAndVignette(unittest.TestCase):
    """tests-ci-7: no unused contract constant; the fade-mode vignette is part of the look."""

    def test_max_luma_constant_is_gone(self):
        self.assertFalse(hasattr(thumb, "MAX_LUMA_BEHIND_TYPE"))

    def test_fade_photo_is_vignetted(self):
        flat = Image.new("RGB", (1280, 720), (150, 150, 150))
        for shade in (None, 0.88):
            out, _ = thumb.render(flat, "X", lead="title", text_side="left", shade=shade)
            a = np.asarray(out.convert("L"), np.float32)
            h, w = a.shape
            corner, mid = a[2, w - 3], a[h // 2, int(w * 0.8)]
            self.assertLess(corner, 0.85 * mid, shade)  # the image side darkens toward its corners


if __name__ == "__main__":
    unittest.main()
