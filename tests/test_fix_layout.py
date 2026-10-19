"""Layout fixes of v0.9.1: every layout warning names a fix that works for the format it fires on.
Run: python -m unittest tests.test_fix_layout (or discover -s tests)"""
import os
import re
import sys
import unittest

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skill", "clean-thumbnail", "scripts"))

import thumb  # noqa: E402


def scene(w, h, box=None, seed=0, color=(120, 140, 160)):
    """Noisy flat frame with a bright textured "face" at box (fractions)."""
    rng = np.random.default_rng(seed)
    a = np.clip(rng.normal(0, 18, (h, w, 3)).astype(np.float32) + np.array(color, np.float32), 0, 255)
    if box:
        x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
        a[y0:y1, x0:x1] = np.clip(rng.normal(200, 60, (y1 - y0, x1 - x0, 3)), 0, 255)
    return Image.fromarray(a.astype(np.uint8))


def bright(w, h, seed=1):
    """A bright inset, like a lit game frame on a screen."""
    return Image.fromarray(np.clip(np.random.default_rng(seed).normal(225, 12, (h, w, 3)), 0, 255).astype(np.uint8))


def figure(w, h):
    """An opaque rounded cut-out on transparency."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, w - 1, h - 1), radius=min(w, h) // 4, fill=(90, 110, 160, 255))
    return img


def warned(info, text):
    return [w for w in info["warnings"] if text in w]


def noted(info, text):
    return [n for n in info["notes"] if text in n]


def suggested(info, flag):
    """The values of a --crop or --inset-box a warning suggests, as a tuple of floats."""
    for w in info["warnings"]:
        m = re.search(r"%s ([0-9.,]+)" % flag, w)
        if m:
            return tuple(float(v) for v in m.group(1).split(","))
    return None


class TallInset(unittest.TestCase):
    """layout-1: on 4:5 and 9:16 the type band keeps off the inset, and the advice is --band or --inset-box."""
    bg = scene(1920, 1080, color=(40, 45, 50))
    inset = bright(800, 450)

    def test_auto_band_moves_off_the_inset(self):
        for aspect in ("4:5", "9:16"):
            _, info = thumb.render(self.bg, "FORBIDDEN WEST", "ON A CRT", inset=self.inset,
                                   inset_box=(0.3, 0.5, 0.7, 0.95), lead="title", aspect=aspect)
            self.assertEqual(info["text_side"], "top", aspect)
            self.assertTrue(noted(info, "type band at the top, clear of the inset"), info["notes"])
            self.assertFalse(warned(info, "overlaps the inset"), info["warnings"])
            self.assertFalse(warned(info, "contrast"), info["warnings"])

    def test_forced_band_names_the_other_band_not_text_side_or_focus(self):
        _, info = thumb.render(self.bg, "FORBIDDEN WEST", "ON A CRT", inset=self.inset, inset_box=(0.3, 0.5, 0.7, 0.95),
                               lead="title", aspect="4:5", band="bottom")
        self.assertTrue(warned(info, "type overlaps the inset image: use --band top"), info["warnings"])
        self.assertTrue(warned(info, "contrast"), info["warnings"])
        for w in warned(info, "contrast"):  # the bright inset under the type: the same fix, never --focus
            self.assertIn("use --band top, which keeps the type off it", w)
            self.assertNotIn("--focus", w)
        for w in info["warnings"]:
            self.assertNotIn("--text-side", w)
        _, info = thumb.render(self.bg, "FORBIDDEN WEST", "ON A CRT", inset=self.inset, inset_box=(0.3, 0.5, 0.7, 0.95),
                               lead="title", aspect="4:5", band="top")  # the advice, followed
        self.assertEqual(info["warnings"], [])

    def test_inset_box_advice_when_no_band_clears_it(self):
        box = (0.1, 0.05, 0.9, 0.95)  # the inset fills the height: both bands land on it
        _, info = thumb.render(self.bg, "FORBIDDEN WEST", "ON A CRT", inset=self.inset, inset_box=box, lead="title",
                               aspect="4:5")
        self.assertTrue(warned(info, "type overlaps the inset image: use --inset-box"), info["warnings"])
        new = suggested(info, "--inset-box")
        self.assertIsNotNone(new, info["warnings"])
        self.assertTrue(warned(info, "contrast"), info["warnings"])
        for w in warned(info, "contrast"):  # light type on the bright inset: the inset fix, not a band or --focus
            self.assertIn("use --inset-box %s" % thumb._fmt(new), w)
        self.assertAlmostEqual((new[2] - new[0]) / (new[3] - new[1]), (box[2] - box[0]) / (box[3] - box[1]), delta=0.1)
        _, info = thumb.render(self.bg, "FORBIDDEN WEST", "ON A CRT", inset=self.inset, inset_box=new, lead="title",
                               aspect="4:5")
        self.assertFalse(warned(info, "overlaps the inset"), info["warnings"])
        self.assertFalse(warned(info, "contrast"), info["warnings"])

    def test_wide_overlap_names_the_side_that_clears_it(self):
        _, info = thumb.render(self.bg, "FORBIDDEN WEST", "ON A CRT", inset=self.inset, inset_box=(0.02, 0.2, 0.5, 0.8),
                               lead="title", size="720p", text_side="left")
        self.assertTrue(warned(info, "type overlaps the inset image: use --text-side right"), info["warnings"])
        _, info = thumb.render(self.bg, "FORBIDDEN WEST", "ON A CRT", inset=self.inset, inset_box=(0.02, 0.2, 0.5, 0.8),
                               lead="title", size="720p", text_side="right")
        self.assertFalse(warned(info, "overlaps the inset"), info["warnings"])


class TallContrastFix(unittest.TestCase):
    """layout-1: light type on a bright patch on 4:5 names the fix that works there: the other band when a re-run
    reads, --focus only when the capture is wider than the crop, darker captures for panels."""

    @staticmethod
    def hints(info):
        return sorted({w.split("bright; ")[1] for w in info["warnings"] if "the background under it is bright" in w})

    def render(self, src, **kw):
        # no shade and a light --bg: the type can't be helped by the shade, only by moving or another image
        _, info = thumb.render(src, "SIMON", "No hit", shade=0.0, bg="#e1e1e1", size="720p", lead="title",
                               aspect="4:5", **kw)
        return self.hints(info)

    def test_each_case_names_its_own_fix(self):
        half = np.full((1080, 1920, 3), 230, np.uint8)
        half[:540] = 30  # dark at the top: the type reads in a top band
        half = Image.fromarray(np.clip(half.astype(int) + np.random.default_rng(0).integers(-8, 8, half.shape),
                                       0, 255).astype(np.uint8))
        light = (230, 230, 230)
        self.assertEqual(self.render(half), ["use --band top, where it reads."])
        self.assertEqual(self.render(Image.new("RGB", (1920, 1080), light)),
                         ["use --focus to move the crop, or a darker image."])
        self.assertEqual(self.render(Image.new("RGB", (1080, 1350), light)), ["use a darker image."])  # shown whole
        self.assertEqual(self.render(None, panels=[Image.new("RGB", (1920, 1080), light)] * 2), ["use darker captures."])


class ReelsCutouts(unittest.TestCase):
    """layout-2: a 9:16 cut-out's face (its top third) stays above the Reels/Shorts caption area, or is flagged."""
    room = scene(1920, 1080, seed=3)

    def test_two_cutouts_with_faces_in_the_caption_area_are_flagged(self):
        _, info = thumb.render(self.room, "HOME STUDIO", "SMALL ROOM", subjects=[figure(700, 900)] * 2, aspect="9:16",
                               lead="title", push=0.0)
        ws = warned(info, "falls under the Reels/Shorts caption area")
        self.assertEqual(len(ws), 2, info["warnings"])
        self.assertIn("use one subject for 9:16", ws[0])
        _, info = thumb.render(self.room, "HOME STUDIO", "SMALL ROOM", subjects=[figure(700, 900)], aspect="9:16",
                               lead="title", push=0.0)  # the advice, followed
        self.assertFalse(warned(info, "caption area"), info["warnings"])

    def test_wide_head_and_shoulders_cutout_is_flagged(self):
        _, info = thumb.render(self.room, "HOME STUDIO", "SMALL ROOM", subjects=[figure(900, 600)], aspect="9:16",
                               lead="title", push=0.0)
        self.assertTrue(warned(info, "subject 1's face (the top third of the cut-out) falls under the Reels/Shorts "
                                     "caption area: use a cut-out from head to waist"), info["warnings"])

    def test_cutout_height_puts_the_top_40_percent_above_the_caption_line(self):
        cv = thumb.Canvas(1080, 1920)
        warnings = []
        _, _, mask = thumb.place_subjects(scene(1080, 1920), [figure(300, 900)] * 2, cv, "left", warnings,
                                          band=(200, 500))
        rows = np.nonzero(np.asarray(mask).max(axis=1) > 128)[0]
        top, height = rows[0], rows[-1] + 1 - rows[0]
        self.assertEqual(rows[-1] + 1, cv.h)  # still standing on the bottom edge
        self.assertLessEqual(top + thumb.REELS_FACE * height, cv.safe_box[3] + 1)  # 0.58 of the height was 7 px short
        self.assertEqual(warnings, [])

    def test_post_room_warning_never_asks_to_cut_words(self):
        canvas = Image.new("RGB", (1080, 1920), (40, 40, 40))
        cv = thumb.Canvas(1080, 1920)
        cv.col_share = thumb.POST_COL_SHARE  # as _load_sources sets it for --mode post
        for post in (True, None):  # passed, or read from the canvas
            warnings = []
            thumb.place_subjects(canvas, [figure(600, 900)], cv, "left", warnings, band=(200, 1500), post=post)
            self.assertEqual(len(warnings), 2, warnings)  # little room, and the face under the caption area
            for w in warnings:
                self.assertNotIn("shorten", w)
                self.assertIn("use --aspect 16:9", w)


class PanelBadge(unittest.TestCase):
    """layout-3: 16:9 panel type stays left of the duration badge, and the badge warning names a fix."""

    def test_panel_titles_stay_clear_of_the_badge(self):
        a, b = scene(1920, 1080, seed=1), scene(1920, 1080, seed=2)
        for title in ("BLACK FLAG", "CYBERPUNK", "WWWWWWWWWW"):
            _, info = thumb.render(None, title, "NR ON VS OFF", panels=[a, b], panel_labels=["A", "B"], lead="title")
            zone = thumb.Canvas(*info["size"]).timestamp_zone
            self.assertFalse(thumb.overlaps(info["text_box"], zone), (title, info["text_box"], zone))
            self.assertFalse(warned(info, "duration badge"), info["warnings"])

    def test_badge_warning_names_a_fix(self):
        class J(object):
            pass
        j = J()
        j.layout, j.text_side = "panels", "bottom"
        self.assertEqual(thumb._badge_fix(j), "shorten the title or the label")
        j.layout, j.text_side = "single", "right"
        self.assertEqual(thumb._badge_fix(j), "use --text-side left")


class TallSubjectBoxCrop(unittest.TestCase):
    """layout-4: 4:5 and 9:16 --subject-box warnings compute a --crop, and the crop clears the box."""

    def check(self, box, title, warning):
        img = scene(2880, 1620, box=box, seed=4)
        _, info = thumb.render(img, title, "NO HUD", subject_box=box, aspect="9:16", lead="title")
        self.assertTrue(warned(info, warning), info["warnings"])
        crop = suggested(info, "try --crop")
        self.assertIsNotNone(crop, info["warnings"])
        self.assertFalse(warned(info, "more room above"), info["warnings"])
        self.assertFalse(warned(info, "shorten the text"), info["warnings"])
        _, info = thumb.render(img, title, "NO HUD", subject_box=box, aspect="9:16", lead="title", crop=crop)
        self.assertEqual(warned(info, "--subject-box"), [])
        self.assertGreaterEqual(info["subject_box"]["inside"], 0.9)
        self.assertGreaterEqual(info["subject_box"]["visibility"], 0.7)
        self.assertLessEqual(info["scale"], 1.5)

    def test_box_too_high_for_the_safe_zone(self):
        self.check((0.47, 0.10, 0.53, 0.24), "BLACK FLAG", "inside the Reels/Shorts safe zone")

    def test_box_under_the_band(self):
        self.check((0.47, 0.36, 0.51, 0.42), "FIRST LIGHT", "the type band darkens --subject-box")

    def test_box_in_the_caption_area_gets_a_crop_and_band(self):
        # a --crop that cuts the frame off above the box's row: the fix restores the height and moves the band up
        box = (0.398, 0.482, 0.432, 0.528)
        img = scene(2880, 1620, box=box, seed=4)
        _, info = thumb.render(img, "X", subject_box=box, crop=(0.1, 0, 0.75, 0.62), aspect="9:16", lead="title")
        w = warned(info, "of --subject-box is inside the Reels/Shorts safe zone")
        self.assertEqual(len(w), 1, info["warnings"])
        m = re.search(r"try --crop ([0-9.,]+) --band (top|bottom)\.", w[0])
        self.assertIsNotNone(m, w[0])
        crop, band = tuple(float(v) for v in m.group(1).split(",")), m.group(2)
        _, info = thumb.render(img, "X", subject_box=box, crop=crop, band=band, aspect="9:16", lead="title")
        self.assertEqual(warned(info, "--subject-box"), [])
        self.assertGreaterEqual(info["subject_box"]["inside"], 0.9)


class TallPanelLabels(unittest.TestCase):
    """layout-5: the lower panel's label doesn't read as one more line of the type stack."""

    def test_lower_label_is_set_apart_from_the_type_block(self):
        a, b = scene(1920, 1080, seed=1), scene(1920, 1080, seed=2)
        for aspect in ("4:5", "9:16"):
            _, info = thumb.render(None, "BLACK FLAG", "RAY RECONSTRUCTION", panels=[a, b],
                                   panel_labels=["NR OFF", "NR ON"], aspect=aspect, lead="title")
            el = dict(info["elements"])
            box, lab = el["label box"], el["panel label 2"]
            h = lab[3] - lab[1]
            apart = lab[0] != box[0] or lab[1] - box[3] > 4 * h
            self.assertTrue(apart, (aspect, box, lab))
            self.assertEqual(info["warnings"], [], aspect)
            cv = thumb.Canvas(*info["size"])
            if aspect == "4:5":  # the panel's bottom-left corner, where nothing else is
                self.assertEqual((lab[0], lab[3]), (cv.margin, cv.h - cv.px(32)), lab)
            else:  # the bottom of the safe zone, still next to the stack: right-aligned at the column's edge
                self.assertEqual((lab[2], lab[3]), (cv.margin + cv.col_w, cv.safe_box[3] - cv.px(32)), lab)


class ReelsCaptionShade(unittest.TestCase):
    """layout-6: the light shade the 9:16 band keeps over the caption area is not a shade warning."""

    def test_box_in_the_caption_area_gets_only_the_safe_zone_warning(self):
        box = (0.398, 0.482, 0.432, 0.528)
        img = scene(2880, 1620, box=box, seed=4)
        for title in ("X", "FORBIDDEN WEST"):
            _, info = thumb.render(img, title, subject_box=box, crop=(0.1, 0, 0.75, 0.62), aspect="9:16", lead="title")
            self.assertTrue(warned(info, "inside the Reels/Shorts safe zone"), info["warnings"])
            self.assertFalse(warned(info, "darkens"), info["warnings"])
            self.assertFalse(warned(info, "shorten the text"), info["warnings"])


class OneNumber(unittest.TestCase):
    """layout-7: the warning and the 'subject box:' line print the same share."""

    def test_safe_zone_share_is_rounded_like_the_report(self):
        box = (0.185, 0.121, 0.27, 0.245)  # 84.7% inside: rounds to 85, truncates to 84
        _, info = thumb.render(scene(1920, 1080, box=box), "BLACK FLAG", "NO HUD", subject_box=box, aspect="9:16",
                               lead="title")
        w = warned(info, "of --subject-box is inside")
        self.assertEqual(len(w), 1, info["warnings"])
        shown = int(re.search(r"only (\d+)%", w[0]).group(1))
        self.assertEqual(shown, round(100 * info["subject_box"]["inside"]))
        self.assertEqual(shown, 85)


class IgnoredFlags(unittest.TestCase):
    """layout-8: --text-side on 4:5/9:16 and --focus on narrow art say they are ignored."""

    def test_text_side_on_tall_formats(self):
        img = scene(1920, 1080)
        for aspect in ("4:5", "9:16"):
            _, info = thumb.render(img, "X", aspect=aspect, text_side="right")
            self.assertTrue(noted(info, "--text-side right is ignored on %s" % aspect), info["notes"])
            self.assertTrue(noted(info, "--band top or --band bottom moves it"), info["notes"])
        _, info = thumb.render(img, "X", aspect="4:5")
        self.assertFalse(noted(info, "--text-side"), info["notes"])

    def test_focus_on_narrow_art(self):
        img = scene(1920, 1080)
        _, info = thumb.render(img, "X", crop=(0, 0, 0.55, 1), focus=0.0, size="720p")
        self.assertTrue(noted(info, "--focus is ignored: the art is narrower than 16:9"), info["notes"])
        _, info = thumb.render(img, "X", focus=0.0, size="720p")
        self.assertFalse(noted(info, "--focus is ignored"), info["notes"])


class CutoutReference(unittest.TestCase):
    """G3: a cut-out clipped by the frame edge is matched to what is behind it, never a stretched copy."""

    def test_reference_is_the_visible_part_unstretched(self):
        seen = []
        orig = thumb.match_layer

        def spy(layer, ref, cv, **kw):
            seen.append((layer.size, ref.size, kw["texture_ref"].size if kw.get("texture_ref") is not None else None))
            return orig(layer, ref, cv, **kw)
        thumb.match_layer = spy
        try:
            canvas = scene(1280, 720)
            cv = thumb.Canvas(1280, 720)
            thumb.place_subjects(canvas, [figure(900, 700)] * 2, cv, "left", [], texture_ref=canvas.copy())
        finally:
            thumb.match_layer = orig
        clipped = [s for s in seen if s[0][0] != s[1][0]]
        self.assertTrue(clipped, seen)  # the outer cut-out runs off the frame edge
        for layer, ref, tex in seen:
            self.assertEqual(ref[1], layer[1])
            self.assertLessEqual(ref[0], layer[0])
            self.assertEqual(tex, ref)


class MirroredSide(unittest.TestCase):
    """G4: the other-side check moves the mirrored type to where that side places it (above the badge)."""

    def test_mirrored_boxes_match_the_real_other_side(self):
        box = (0.62, 0.08, 0.9, 0.2)
        img = scene(1920, 1080, box=box, seed=5)
        # a stack that fits both sides at one size (465 px tall at 720p): centered on the left (top y 127),
        # lifted above the duration badge on the right (top y 82)
        kw = dict(title="NORTHERN\nLIGHTS\nTONIGHT\nAGAIN", label="NO HUD", kicker="PART ONE",
                  subtitle="A LONG SUBTITLE", subject_box=box, size="720p", lead="title", style="sans", shade=0.3)
        jobs = []
        orig = thumb._check_subject_box

        def spy(j):
            jobs.append(j)
            return orig(j)
        thumb._check_subject_box = spy
        try:
            _, left = thumb.render(img, text_side="left", **kw)
            _, right = thumb.render(img, text_side="right", **kw)
        finally:
            thumb._check_subject_box = orig
        j = jobs[0]
        rboxes = [b for n, b in right["elements"]]
        lboxes = [b for n, b in left["elements"]]
        self.assertNotEqual(lboxes[0][1], rboxes[0][1])  # the right side sits higher, above the badge
        m = thumb._wide_side(j, "right", 0.5 if j.focus_eff is None else j.focus_eff, j.built["shade"])
        self.assertAlmostEqual(m[3], right["subject_box"]["under_type"], delta=0.01)


if __name__ == "__main__":
    unittest.main()
