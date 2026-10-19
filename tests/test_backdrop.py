"""Tests for --backdrop fade|solid in scripts/thumb.py. Run: python -m unittest discover -s tests

fade (the default) must leave every render as it was. solid keeps the photo clean in its own rectangle
(info["image_rect"], canvas px, PIL box convention: x1 and y1 exclusive) and puts the type on an opaque
panel of the --bg color: no gradient, no darkening of the photo, a hard straight edge between the two."""
import json
import os
import re
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "skill", "clean-thumbnail", "scripts"))
sys.path.insert(0, HERE)

import thumb  # noqa: E402
from test_thumb import (POST_BODY, POST_DETAILS, POST_LONG, busy_right, cutout, dark_scene,  # noqa: E402
                        grainy, half_busy, run_cli, scene, warned)

BG = "#1c2430"
BG_RGB = (0x1C, 0x24, 0x30)
SOLID_NOTE = "no effect with --backdrop solid"


def warm_photo(w=1920, h=1080):
    """A bright, grainy, warm photo: far from BG in color, with texture to tell photo pixels from panel ones."""
    return grainy(w, h, color=(150, 120, 80), sigma=10)


def wcag_ratio(a, b):
    la = float(thumb.wcag_luminance(np.array(a, np.float32) / 255.0))
    lb = float(thumb.wcag_luminance(np.array(b, np.float32) / 255.0))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def rect_of(info):
    return tuple(int(v) for v in info["image_rect"])


def panel_sides(rect, w, h):
    x0, y0, x1, y1 = rect
    return [s for s, c in (("top", y0 > 0), ("bottom", y1 < h), ("left", x0 > 0), ("right", x1 < w)) if c]


def type_mask(info, w, h):
    """Every drawn element's box, padded so anti-aliasing, overhangs and the label box outline are covered."""
    m = max(8, w // 48)
    mask = np.zeros((h, w), bool)
    for _, (x0, y0, x1, y1) in info["elements"]:
        mask[max(0, y0 - m):min(h, y1 + m), max(0, x0 - m):min(w, x1 + m)] = True
    return mask


def edge_lines(a, rect, side, depth):
    """(last photo line at the edge, a photo line `depth` px further in, first panel line) for one side."""
    x0, y0, x1, y1 = rect
    if side == "bottom":
        return a[y1 - 1, x0:x1], a[y1 - 1 - depth, x0:x1], a[y1, :]
    if side == "top":
        return a[y0, x0:x1], a[y0 + depth, x0:x1], a[y0 - 1, :]
    if side == "left":
        return a[y0:y1, x0], a[y0:y1, x0 + depth], a[:, x0 - 1]
    return a[y0:y1, x1 - 1], a[y0:y1, x1 - 1 - depth], a[:, x1]


class SolidChecks(object):
    """Mixin: the pixel-level contract of a solid render."""

    def assert_solid(self, out, info, bg=BG_RGB, min_checked=0.15, photo_far=True):
        """The panel is exactly bg outside the type, the photo rect has image content, the edge is hard.
        Returns (image rect, panel sides)."""
        self.assertEqual(info.get("backdrop"), "solid")
        self.assertIn("image_rect", info)
        a = np.asarray(out.convert("RGB"))
        h, w = a.shape[:2]
        self.assertEqual((w, h), tuple(info["size"]))
        rect = rect_of(info)
        x0, y0, x1, y1 = rect
        self.assertTrue(0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, rect)
        sides = panel_sides(rect, w, h)
        self.assertEqual(len(sides), 1, (rect, sides))  # one panel, on one side
        panel = np.ones((h, w), bool)
        panel[y0:y1, x0:x1] = False
        tmask = type_mask(info, w, h)
        check = panel & ~tmask
        self.assertGreater(check.sum(), min_checked * panel.sum(), "too little panel left to check")
        off = np.any(a[check] != np.array(bg, np.uint8), axis=1)
        if off.any():
            ys, xs = np.nonzero(check)
            i = int(np.argmax(off))
            self.fail("%d panel pixels are not bg %s, first at (%d, %d): %s"
                      % (int(off.sum()), bg, xs[i], ys[i], tuple(a[ys[i], xs[i]])))
        # the type sits on the panel, not on the photo
        for name, b in info["elements"]:
            ix0, iy0 = max(b[0], x0), max(b[1], y0)
            ix1, iy1 = min(b[2], x1), min(b[3], y1)
            self.assertFalse(ix1 > ix0 and iy1 > iy0, "%s %s overlaps the photo rect %s" % (name, b, rect))
        # the photo rect holds the image: textured, and nowhere near the panel color (bright source)
        photo = a[y0:y1, x0:x1].astype(np.float32)
        self.assertGreater(float(photo.mean(axis=2).std()), 2.0)
        if photo_far:
            self.assertGreater(float(np.abs(photo.mean(axis=(0, 1)) - np.array(bg)).mean()), 25)
        # hard straight edge: the last photo line is still photo (no fade toward bg), the next line is panel
        side = sides[0]
        span = (y1 - y0) if side in ("top", "bottom") else (x1 - x0)
        edge, inner, first = edge_lines(a.astype(np.float32), rect, side, max(4, span // 20))
        if photo_far:
            self.assertGreater(float(np.abs(edge.mean(axis=0) - np.array(bg)).mean()), 25, "photo edge fades to bg")
            self.assertLess(float(np.abs(edge.mean(axis=0) - inner.mean(axis=0)).max()), 20,
                            "the photo darkens toward the panel")
        lm = {"bottom": lambda: tmask[y1, :], "top": lambda: tmask[y0 - 1, :],
              "left": lambda: tmask[:, x0 - 1], "right": lambda: tmask[:, x1]}[side]()
        self.assertTrue(np.all(first[~lm] == np.array(bg, np.float32)), "first panel line is not pure bg")
        # no darkening of the photo next to the panel (fade shades it there)
        luma = photo.mean(axis=2)
        k = max(2, int(span * 0.08))
        near = {"bottom": luma[-k:], "top": luma[:k], "left": luma[:, :k], "right": luma[:, -k:]}[side]
        mid = luma[luma.shape[0] // 3:2 * luma.shape[0] // 3, luma.shape[1] // 3:2 * luma.shape[1] // 3]
        if photo_far:
            self.assertGreater(float(near.mean()), 0.7 * float(mid.mean()), "photo darkened next to the panel")
        return rect, sides


class FadeDefault(unittest.TestCase):
    """fade is the default and changes nothing: same pixels, same info, no new keys."""

    def test_fade_is_the_default_and_identical(self):
        cases = [
            (half_busy(1280, 720), dict(title="SIMON", label="No hit", lead="title")),
            (warm_photo(), dict(title="SIMON", label="No hit", lead="title", aspect="4:5")),
            (warm_photo(), dict(title="SIMON", label="No hit", lead="label", aspect="9:16")),
            (dark_scene(), dict(title="FREE RESUME REVIEWS", mode="post", body=POST_BODY, details=POST_DETAILS)),
        ]
        for img, kw in cases:
            a, ia = thumb.render(img, **kw)
            b, ib = thumb.render(img, backdrop="fade", **kw)
            self.assertTrue(np.array_equal(np.asarray(a), np.asarray(b)), kw)
            self.assertEqual(ia, ib, kw)
            self.assertNotIn("backdrop", ia)
            self.assertNotIn("image_rect", ia)
            self.assertFalse(any("--backdrop" in n for n in ia["notes"]), ia["notes"])

    def test_unknown_backdrop_is_rejected(self):
        with self.assertRaises(ValueError):
            thumb.render(half_busy(1280, 720), "SIMON", backdrop="blur")


class SolidTall(SolidChecks, unittest.TestCase):
    """4:5 and 9:16: a full-width panel from the band's outer edge to the canvas edge; the photo above (or below)."""

    def test_feed_4x5_panel_below_the_photo(self):
        out, info = thumb.render(warm_photo(), "SIMON", "Parry only", lead="title", aspect="4:5", bg=BG,
                                 backdrop="solid")
        rect, sides = self.assert_solid(out, info)
        cv = thumb.Canvas(*out.size)
        self.assertEqual(sides, ["bottom"])
        self.assertEqual((rect[0], rect[1], rect[2]), (0, 0, cv.w))
        top = info["text_box"][1]
        self.assertLessEqual(rect[3], top)  # the panel starts above the type: band top minus padding
        self.assertGreaterEqual(rect[3], top - cv.px(80))
        self.assertGreater(rect[3], 0.4 * cv.h)  # the photo keeps a real share of the frame

    def test_photo_is_not_vignetted(self):
        # a flat photo stays flat in its rect: grade() without the vignette, so the corners match the middle
        flat = Image.new("RGB", (1920, 1080), (150, 120, 80))
        for aspect, kw in (("4:5", {}), ("16:9", dict(text_side="left"))):
            out, info = thumb.render(flat, "SIMON", "No hit", lead="title", aspect=aspect, bg=BG, backdrop="solid",
                                     **kw)
            x0, y0, x1, y1 = rect_of(info)
            a = np.asarray(out, np.int16)[y0:y1, x0:x1]
            mid = a[a.shape[0] // 2, a.shape[1] // 2]
            for corner in (a[0, 0], a[0, -1], a[-1, 0], a[-1, -1]):
                self.assertLessEqual(int(np.abs(corner - mid).max()), 2, (aspect, tuple(corner), tuple(mid)))

    def test_reels_9x16_panel_runs_through_the_caption_area(self):
        for lead in ("title", "label"):
            out, info = thumb.render(warm_photo(), "SIMON", "No hit · Parry only", lead=lead, aspect="9:16",
                                     bg=BG, backdrop="solid")
            rect, sides = self.assert_solid(out, info)
            cv = thumb.Canvas(*out.size)
            self.assertEqual(out.size, (1080, 1920))
            self.assertEqual(sides, ["bottom"], lead)
            self.assertEqual((rect[0], rect[1], rect[2]), (0, 0, cv.w))
            self.assertLessEqual(rect[3], info["text_box"][1])
            # the type stays in the Reels safe zone, as with fade
            x0, y0, x1, y1 = info["text_box"]
            self.assertGreaterEqual(y0, 0.14 * cv.h - 1)
            self.assertLessEqual(y1, 0.65 * cv.h + 1)
            self.assertLessEqual(x1, 0.85 * cv.w + 1)
            self.assertFalse(warned(info, "safe zone"), info["warnings"])
            # the caption area under the safe zone is panel, all the way to the bottom edge
            a = np.asarray(out)
            self.assertTrue(np.all(a[int(0.66 * cv.h):, :] == np.array(BG_RGB, np.uint8)))

    def test_band_at_the_top_puts_the_panel_above(self):
        out, info = thumb.render(warm_photo(), "SIMON", "No hit", lead="title", aspect="4:5", bg=BG,
                                 backdrop="solid", band="top")
        rect, sides = self.assert_solid(out, info)
        cv = thumb.Canvas(*out.size)
        self.assertEqual(info["text_side"], "top")
        self.assertEqual(sides, ["top"])
        self.assertEqual((rect[0], rect[2], rect[3]), (0, cv.w, cv.h))
        self.assertGreaterEqual(rect[1], info["text_box"][3])  # band bottom plus padding
        self.assertLessEqual(rect[1], info["text_box"][3] + cv.px(80))

    def test_subject_box_stays_in_the_photo_and_visible(self):
        img = busy_right()
        for aspect in ("4:5", "9:16"):
            for box in ((0.08, 0.30, 0.22, 0.55), (0.55, 0.35, 0.70, 0.60)):
                out, info = thumb.render(img, "SIMON", "No hit", lead="title", aspect=aspect, subject_box=box,
                                         bg=BG, backdrop="solid")
                self.assert_solid(out, info, photo_far=False)
                rect, sb = rect_of(info), info["subject_box"]
                r = sb["rect"]
                msg = (aspect, box, r, rect, info["warnings"])
                self.assertTrue(r[0] >= rect[0] - 2 and r[1] >= rect[1] - 2 and r[2] <= rect[2] + 2
                                and r[3] <= rect[3] + 2, msg)  # inside the photo, never in the panel
                self.assertGreaterEqual(sb["inside"], 0.9, msg)
                self.assertGreaterEqual(sb["visibility"], 0.9, msg)
                self.assertLessEqual(sb["heavy"], 0.1, msg)
                self.assertFalse(warned(info, "of --subject-box is"), msg)
                self.assertFalse(warned(info, "darkens --subject-box"), msg)
            # a box in the middle of the capture is centered across the photo
            cx = (r[0] + r[2]) / 2.0
            self.assertLess(abs(cx - (rect[0] + rect[2]) / 2.0), 0.1 * out.width, (aspect, r, rect))

    def test_portrait_capture_crop_follows_the_box_vertically(self):
        # a tall capture is cut top and bottom to fit the photo rect: the crop moves to keep the box in it
        for box in ((0.3, 0.08, 0.7, 0.2), (0.3, 0.62, 0.7, 0.74)):
            img = scene(1080, 3600, box=box, seed=7)
            for aspect in ("4:5", "9:16"):
                out, info = thumb.render(img, "DAWN", "No hit", lead="title", aspect=aspect, subject_box=box,
                                         bg=BG, backdrop="solid")
                self.assert_solid(out, info, photo_far=False)
                sb, msg = info["subject_box"], (box, aspect, info["subject_box"], info["warnings"])
                self.assertGreaterEqual(sb["inside"], 0.9, msg)
                self.assertGreaterEqual(sb["visibility"], 0.9, msg)
                self.assertFalse(warned(info, "of --subject-box is"), msg)

    def test_subject_cutout_stays_in_the_photo(self):
        bg_img = grainy(color=(150, 120, 80), sigma=9)
        for aspect in ("4:5", "16:9"):
            kw = dict(text_side="left") if aspect == "16:9" else {}
            out, info = thumb.render(bg_img, "4 MONTHS", "Of silence", lead="title", subjects=[cutout()], push=0.0,
                                     aspect=aspect, bg=BG, backdrop="solid", **kw)
            rect, sides = self.assert_solid(out, info)  # no cut-out pixels in the panel
            if aspect == "4:5":
                self.assertEqual(info["text_side"], "top")
                self.assertEqual(sides, ["top"])
            plain, pi = thumb.render(bg_img, "4 MONTHS", "Of silence", lead="title", aspect=aspect, bg=BG,
                                     backdrop="solid", **kw)

            def blue(img, r):
                a = np.asarray(img, np.float32)[r[1]:r[3], r[0]:r[2]]
                return float((a[..., 2] - a[..., 0]).mean())
            self.assertGreater(blue(out, rect), blue(plain, rect_of(pi)) + 3, aspect)  # the figure is in the photo


class SolidWide(SolidChecks, unittest.TestCase):
    """16:9: the panel covers the type column side; the photo fills the rest at full height."""

    def test_panel_covers_the_type_column(self):
        for side in ("left", "right"):
            out, info = thumb.render(warm_photo(), "SIMON", "No hit · Parry only", kicker="Expedition 33",
                                     lead="title", text_side=side, bg=BG, backdrop="solid")
            rect, sides = self.assert_solid(out, info)
            cv = thumb.Canvas(*out.size)
            self.assertEqual(info["text_side"], side)
            self.assertEqual(sides, [side])
            self.assertEqual((rect[1], rect[3]), (0, cv.h))  # full height
            want = 2 * cv.margin + cv.col_w  # margin + column + one margin of breathing room
            if side == "left":
                self.assertEqual(rect[2], cv.w)
                self.assertGreaterEqual(rect[0], info["text_box"][2])
                self.assertLess(abs(rect[0] - want), 0.02 * cv.w, (rect, want))
            else:
                self.assertEqual(rect[0], 0)
                self.assertLessEqual(rect[2], info["text_box"][0])
                self.assertLess(abs((cv.w - rect[2]) - want), 0.02 * cv.w, (rect, want))
            # duration badge corner still kept clear
            self.assertFalse(thumb.overlaps(info["text_box"], cv.timestamp_zone), side)
            self.assertFalse(warned(info, "duration badge"), info["warnings"])

    def test_auto_side_and_crop(self):
        out, info = thumb.render(half_busy(busy="left"), "SIMON", "No hit", lead="title", bg=BG, backdrop="solid")
        self.assertEqual(info["text_side"], "right")
        rect, sides = self.assert_solid(out, info, photo_far=False)
        self.assertEqual(sides, ["right"])
        out, info = thumb.render(warm_photo(), "SIMON", "No hit", lead="title", text_side="left", bg=BG,
                                 crop=(0.1, 0.0, 0.9, 1.0), backdrop="solid")
        self.assert_solid(out, info)
        self.assertTrue(any("cropped the capture" in n for n in info["notes"]), info["notes"])

    def test_subject_box_in_the_panel_area_is_out_of_view(self):
        # text on the left and the crop forced to the right edge: a box at the far left maps into the panel
        _, info = thumb.render(busy_right(), "SIMON", "No hit", lead="title", text_side="left", focus=1.0,
                               subject_box=(0.05, 0.30, 0.15, 0.60), bg=BG, backdrop="solid", size="1080p")
        self.assertEqual(info.get("backdrop"), "solid")
        sb = info["subject_box"]
        self.assertLess(sb["visibility"], 0.3, sb)
        self.assertTrue(warned(info, "--subject-box"), info["warnings"])
        # and a box the crop keeps lands in the photo, fully visible
        _, info = thumb.render(busy_right(), "SIMON", "No hit", lead="title", text_side="left", focus=1.0,
                               subject_box=(0.75, 0.30, 0.85, 0.60), bg=BG, backdrop="solid", size="1080p")
        rect, r = rect_of(info), info["subject_box"]["rect"]
        self.assertGreaterEqual(r[0], rect[0] - 2)
        self.assertGreaterEqual(info["subject_box"]["visibility"], 0.9, info["subject_box"])

    def test_wide_cutout_keeps_to_the_photo_columns(self):
        # a cut-out wider than the photo is scaled to the photo rect's width and starts at its edge, never
        # in the panel's breathing room beside the type column
        out, info = thumb.render(warm_photo(), "4 MONTHS", "Of silence", lead="title", text_side="left",
                                 subjects=[cutout(1800, 700)], push=0.0, bg=BG, backdrop="solid")
        rect, sides = self.assert_solid(out, info, photo_far=False)
        self.assertEqual(sides, ["left"])
        a = np.asarray(out, np.float32)
        col = a[int(0.85 * out.height), rect[0]:rect[0] + 8]
        self.assertGreater(float((col[:, 2] - col[:, 0]).mean()), 40)  # the blue figure reaches the photo edge

    def test_logo_and_label_on_the_panel(self):
        logo = Image.new("RGBA", (900, 200), (0, 0, 0, 0))
        logo.paste((230, 230, 230, 255), (50, 50, 850, 150))
        for aspect in ("16:9", "4:5"):
            out, info = thumb.render(warm_photo(), "ignored", "Parry only", logo=logo, lead="title", aspect=aspect,
                                     text_side="left" if aspect == "16:9" else "auto", bg=BG, backdrop="solid")
            self.assert_solid(out, info)
            names = [n for n, _ in info["elements"]]
            self.assertIn("logo", names)
            self.assertIn("label box", names)


class SolidContrast(unittest.TestCase):
    """Per-element WCAG contrast is measured against the solid panel, not the photo."""

    def test_contrast_is_against_the_panel(self):
        white_photo = Image.new("RGB", (1920, 1080), (250, 248, 240))
        dark = "#202020"
        _, solid = thumb.render(white_photo, "SIMON", lead="title", text_side="left", bg=dark, backdrop="solid")
        want = wcag_ratio((255, 255, 255), (0x20, 0x20, 0x20))
        self.assertGreaterEqual(solid["contrast"], 0.85 * want)  # white on the panel, not white on white
        self.assertLessEqual(solid["contrast"], want + 0.05)
        self.assertFalse(any(w.startswith("the title has") for w in solid["warnings"]), solid["warnings"])
        self.assertFalse(any("raised --shade" in n for n in solid["notes"]), solid["notes"])
        _, fade = thumb.render(white_photo, "SIMON", lead="title", text_side="left", bg=dark)
        self.assertGreater(solid["contrast"], fade["contrast"])  # the fade lets the bright photo through

    def test_light_panel_says_use_a_darker_bg(self):
        # white type on a light panel: shade cannot help, so no shade raise; the fix is the panel color
        dark_photo = Image.new("RGB", (1920, 1080), (20, 20, 24))
        for aspect in ("16:9", "4:5"):
            _, info = thumb.render(dark_photo, "SIMON", lead="title", text_side="left" if aspect == "16:9" else "auto",
                                   aspect=aspect, bg="#d8d8d8", backdrop="solid")
            self.assertLess(info["contrast"], thumb.MIN_CONTRAST)
            self.assertFalse(any("--shade" in n for n in info["notes"] if n != SOLID_NOTE), info["notes"])
            hits = [w for w in info["warnings"] if w.startswith("the title has")]
            self.assertTrue(hits and "use a darker --bg" in hits[0], info["warnings"])

    def test_dark_title_on_dark_panel_warns(self):
        white_photo = Image.new("RGB", (1920, 1080), (250, 248, 240))
        for aspect in ("16:9", "4:5"):
            _, info = thumb.render(white_photo, "SIMON", lead="title", text_side="left" if aspect == "16:9" else "auto",
                                   aspect=aspect, bg="#202020", title_color="#303030", backdrop="solid")
            want = wcag_ratio((0x30, 0x30, 0x30), (0x20, 0x20, 0x20))
            self.assertLess(info["contrast"], thumb.MIN_CONTRAST)
            self.assertLessEqual(info["contrast"], want + 0.05)  # dark on dark: the photo's white never counts
            self.assertTrue(any(w.startswith("the title has") for w in info["warnings"]), info["warnings"])
            self.assertEqual(info.get("contrast_element"), "title")


class SolidNotes(unittest.TestCase):
    def test_shade_and_feather_have_no_effect_and_say_so(self):
        img = warm_photo()
        for aspect, kw in (("16:9", dict(text_side="left")), ("4:5", {})):
            base, ib = thumb.render(img, "SIMON", "No hit", lead="title", aspect=aspect, bg=BG, backdrop="solid", **kw)
            self.assertFalse(any(SOLID_NOTE in n for n in ib["notes"]), ib["notes"])
            for extra in (dict(shade=0.5), dict(feather=0.1), dict(shade=0.6, feather=0.2)):
                out, info = thumb.render(img, "SIMON", "No hit", lead="title", aspect=aspect, bg=BG,
                                         backdrop="solid", **dict(kw, **extra))
                self.assertTrue(np.array_equal(np.asarray(out), np.asarray(base)), (aspect, extra))
                self.assertTrue(any(SOLID_NOTE in n for n in info["notes"]), (aspect, extra, info["notes"]))
                self.assertEqual(info["image_rect"], ib["image_rect"])

    def test_solid_is_ignored_with_panels(self):
        a, b = grainy(color=(60, 70, 90)), grainy(color=(110, 90, 70))
        kw = dict(panels=[a, b], panel_labels=["25H2", "26H2"])
        for aspect in ("16:9", "9:16"):
            fade, fi = thumb.render(None, "GAMING", "Which is faster", aspect=aspect, **kw)
            solid, si = thumb.render(None, "GAMING", "Which is faster", aspect=aspect, backdrop="solid", **kw)
            self.assertTrue(np.array_equal(np.asarray(fade), np.asarray(solid)), aspect)
            self.assertNotIn("image_rect", si)
            self.assertNotEqual(si.get("backdrop"), "solid")
            new = [n for n in si["notes"] if n not in fi["notes"]]
            self.assertTrue(any("solid" in n for n in new), si["notes"])
            self.assertEqual(si["warnings"], fi["warnings"])


class SolidPost(SolidChecks, unittest.TestCase):
    """--mode post with --backdrop solid: every word kept, the image keeps its 45% of a 4:5 frame."""

    def test_post_keeps_every_word_and_the_image_share(self):
        details = POST_DETAILS + " · Room 2B"
        for body in (POST_BODY, POST_LONG):
            out, info = thumb.render(warm_photo(), "FREE RESUME REVIEWS", label="Free", mode="post", body=body,
                                     details=details, bg=BG, backdrop="solid")
            self.assertEqual(info["aspect"], "4:5")
            rect, sides = self.assert_solid(out, info, min_checked=0.05)
            cv = thumb.Canvas(*out.size)
            pt = info["post_text"]
            for name, text in (("body", body), ("details", details)):
                lines = pt[name]["lines"]
                self.assertEqual(thumb.post_words(" ".join(lines)), thumb.post_words(text))
                drawn = [bx for n, bx in info["elements"] if n == name]
                self.assertEqual(len(drawn), len(lines))
            self.assertFalse(warned(info, "not laid out word for word"))
            self.assertGreaterEqual(rect[3] - rect[1], thumb.POST_IMAGE_SHARE * cv.h - 1, rect)
            self.assertFalse(any(w.startswith("the body has") or w.startswith("the details has")
                                 for w in info["warnings"]), info["warnings"])
            self.assertFalse(warned(info, "duration badge"))

    def test_post_reels_and_wide(self):
        out, info = thumb.render(warm_photo(), "REVIEWS", mode="post", body=POST_BODY, details=POST_DETAILS,
                                 aspect="9:16", bg=BG, backdrop="solid")
        self.assert_solid(out, info, min_checked=0.05)
        self.assertFalse(warned(info, "safe zone"), info["warnings"])
        out, info = thumb.render(warm_photo(), "REVIEWS", mode="post", body=POST_BODY, details=POST_DETAILS,
                                 aspect="16:9", text_side="left", bg=BG, backdrop="solid")
        rect, sides = self.assert_solid(out, info, min_checked=0.05)
        self.assertEqual(sides, ["left"])
        self.assertGreaterEqual(rect[0], info["text_box"][2])


class SolidCLI(unittest.TestCase):
    def test_cli_prints_the_backdrop_line_after_colors(self):
        with tempfile.TemporaryDirectory() as d:
            warm_photo(1280, 720).save(os.path.join(d, "cap.png"))
            base = ["cap.png", "--title", "SIMON", "--lead", "title"]
            code, text = run_cli(base + ["--backdrop", "solid", "--bg", BG, "-o", "s"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn("backdrop: solid (panel %s)" % BG, text)
            self.assertLess(text.index("colors: "), text.index("backdrop: solid"))
            self.assertTrue(os.path.exists(os.path.join(d, "s.jpg")))
            code, text = run_cli(base + ["--backdrop", "solid", "--aspect", "4:5", "-o", "d"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertRegex(text, r"backdrop: solid \(panel #[0-9a-f]{6}\)")  # default bg: the shadow tone
            code, text = run_cli(base + ["-o", "f"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertNotIn("backdrop:", text)
            code, text = run_cli(base + ["--backdrop", "fade", "-o", "g"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertNotIn("backdrop:", text)

    def test_cli_rejects_unknown_backdrop_and_notes_shade(self):
        with tempfile.TemporaryDirectory() as d:
            warm_photo(1280, 720).save(os.path.join(d, "cap.png"))
            base = ["cap.png", "--title", "SIMON", "--lead", "title"]
            code, text = run_cli(base + ["--backdrop", "blur", "-o", "t"], cwd=d)
            self.assertEqual(code, 2)
            self.assertIn("solid", text)
            self.assertNotIn("Traceback", text)
            self.assertFalse(os.path.exists(os.path.join(d, "t.jpg")))
            code, text = run_cli(base + ["--backdrop", "solid", "--shade", "0.5", "-o", "n"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn(SOLID_NOTE, text)
            code, text = run_cli(base + ["--backdrop", "solid", "-o", "m"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertNotIn(SOLID_NOTE, text)

    def test_channel_key_validates_and_applies(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "channel.json")
            with open(path, "w") as f:
                json.dump({"backdrop": "blur"}, f)
            with self.assertRaises(ValueError) as e:
                thumb.load_channel(path)
            self.assertIn("solid", str(e.exception))
            self.assertEqual(thumb.DEFAULTS["backdrop"], "fade")
            self.assertEqual(set(thumb.CHOICES["backdrop"]), {"fade", "solid"})
            with open(path, "w") as f:
                json.dump({"backdrop": "solid"}, f)
            self.assertEqual(thumb.load_channel(path)["backdrop"], "solid")
            # the saved key applies to a run, and the flag overrides it for one run
            warm_photo(1280, 720).save(os.path.join(d, "cap.png"))
            with open(os.path.join(d, thumb.CHANNEL_FILE), "w") as f:
                json.dump({"backdrop": "solid", "lead": "title"}, f)
            code, text = run_cli(["cap.png", "--title", "SIMON", "-o", "c"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertTrue(re.search(r"backdrop: solid \(panel #[0-9a-f]{6}\)", text), text)
            code, text = run_cli(["cap.png", "--title", "SIMON", "--backdrop", "fade", "-o", "o"], cwd=d)
            self.assertEqual(code, 0, text)
            self.assertNotIn("backdrop:", text)


if __name__ == "__main__":
    unittest.main()
