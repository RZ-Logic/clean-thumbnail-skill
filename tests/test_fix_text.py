"""v0.9.1 text fixes: line breaks in single-line elements, the logo note, tracked type that keeps ligatures,
clusters and shaped scripts intact, glyph checks for every element, type wider than its column, small type on
a light --bg, oldstyle figures, label case and measured small-type warnings.
Run: python -m unittest discover -s tests"""
import os
import sys
import unittest

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skill", "clean-thumbnail", "scripts"))

import thumb  # noqa: E402

RNG = np.random.default_rng(1)


def half_busy(w=1280, h=720):
    """Wide frame with a busy (noisy) right half and a flat left half, so the type goes left."""
    img = Image.new("RGB", (w, h), (90, 80, 70))
    img.paste(Image.fromarray(RNG.integers(0, 255, (h, w // 2, 3), dtype=np.uint8), "RGB"), (w // 2, 0))
    return img


def flat(w=1280, h=720, color=(110, 110, 110)):
    return Image.new("RGB", (w, h), color)


def warned(info, text):
    return any(text in w for w in info["warnings"])


def noted(info, text):
    return any(text in n for n in info["notes"])


class Drawn(object):
    """Records every line draw_text draws during a render (the label box draws through it too)."""

    def __enter__(self):
        self.texts, self._orig = [], thumb.draw_text

        def hook(draw, x, baseline, text, *a, **k):
            self.texts.append(text)
            return self._orig(draw, x, baseline, text, *a, **k)
        thumb.draw_text = hook
        return self

    def __exit__(self, *exc):
        thumb.draw_text = self._orig


def ink(draw_fn, size=(900, 300)):
    im = Image.new("L", size, 0)
    draw_fn(ImageDraw.Draw(im))
    return np.asarray(im) > 127


class LineBreaks(unittest.TestCase):
    """type-color-1: a typed \\n never reaches a single-line element as a backslash or an upper-cased \\N."""

    def test_title_with_a_line_break_reads_as_words_in_the_label_box(self):
        with Drawn() as d:
            thumb.render(half_busy(), "DAWN\\nWALKER", "NO HIT RUN", lead="label")
        self.assertIn("DAWN WALKER", d.texts)
        self.assertFalse([t for t in d.texts if "\\" in t])

    def test_label_with_a_line_break_splits_the_hook_and_joins_in_the_box(self):
        with Drawn() as d:
            thumb.render(half_busy(), "SAM", "No hit\\nParry only", lead="title")
        self.assertIn("NO HIT PARRY ONLY", d.texts)
        with Drawn() as d:
            _, typed = thumb.render(half_busy(), "SAM", "No hit\\nParry only", lead="label")
        self.assertIn("NO HIT", d.texts)
        self.assertIn("PARRY ONLY", d.texts)
        self.assertFalse([t for t in d.texts if "\\" in t])
        # stacked like a hook split at " · ": each line fitted on its own, so the short first one is set larger
        _, dot = thumb.render(half_busy(), "SAM", "No hit · Parry only", lead="label")
        hook = [b for n, b in typed["elements"] if n == "hook"]
        self.assertEqual(len(hook), 2)
        self.assertEqual(hook, [b for n, b in dot["elements"] if n == "hook"])
        self.assertGreater(hook[0][3] - hook[0][1], hook[1][3] - hook[1][1])

    def test_kicker_subtitle_and_panel_labels_join_their_lines(self):
        with Drawn() as d:
            thumb.render(half_busy(), "SAM", "", kicker="Act\\none", subtitle="The\nend")
        self.assertIn("ACT ONE", d.texts)
        self.assertIn("THE END", d.texts)
        self.assertEqual(thumb.one_line("A\\nB\n C "), "A B C")
        with Drawn() as d:
            thumb.render(None, "SAM", "", panels=[flat(), flat(color=(60, 60, 60))],
                         panel_labels=["Ray\\ntracing", "Raster"])
        self.assertIn("RAY TRACING", d.texts)
        self.assertFalse([t for t in d.texts if "\\" in t or "\n" in t])


class LogoNote(unittest.TestCase):
    """G1: a title the logo replaces is named in a note with where it can go instead."""

    def logo(self):
        img = Image.new("RGBA", (800, 200), (0, 0, 0, 0))
        ImageDraw.Draw(img).rectangle((10, 10, 790, 190), fill=(255, 255, 255, 255))
        return img

    def test_thumbnail_and_post_say_the_title_is_not_drawn(self):
        _, info = thumb.render(half_busy(), "STILLROOM", "", logo=self.logo(), title_font="playfair@SemiBold")
        self.assertTrue(noted(info, "--logo replaces the title, so 'STILLROOM' is not drawn"))
        self.assertTrue(noted(info, "--title-font has no effect"))
        self.assertTrue(noted(info, "put it in --label"))
        _, info = thumb.render(half_busy(), "Make room", "", logo=self.logo(), mode="post", body="Better work.")
        self.assertTrue(noted(info, "put it in --body"))
        _, info = thumb.render(half_busy(), "", "", logo=self.logo())
        self.assertFalse(noted(info, "--logo replaces"))


class TrackedShaping(unittest.TestCase):
    """G2 and type-color-3: tracked type is measured as it is drawn (no ligatures), keeps a letter with its
    marks, and draws a script that needs shaping whole."""

    def test_tracked_pairs_are_measured_without_ligatures(self):
        f = thumb.load_font(thumb.bundled_spec("outfit@Bold"), 150)
        text, trk = "Office", 0.02 * 150
        units = list(text)  # one cluster per letter here
        xs = np.cumsum([0.0] + thumb._advances(f, text, trk)[:-1])
        masks = [ink(lambda d, u=u, x=x: d.text((40 + x, 220), u, font=f, fill=255, anchor="ls",
                                               **thumb._fkw(f))) for u, x in zip(units, xs)]
        shared = sum(int((a & b).sum()) for a, b in zip(masks, masks[1:]))
        self.assertEqual(shared, 0)  # the ff no longer sets the second f into the first

    def test_a_letter_keeps_its_marks(self):
        self.assertEqual(thumb._clusters("e\u0301x"), ["e\u0301", "x"])
        self.assertEqual(len(thumb._advances(thumb.load_font(thumb.bundled_spec("outfit"), 60), "e\u0301x", 4)), 2)
        f = thumb.load_font(thumb.bundled_spec("outfit"), 120)
        nfd = ink(lambda d: thumb.draw_text(d, 40, 200, "e\u0301", f, 12, 255))
        whole = ink(lambda d: thumb.draw_text(d, 40, 200, "e\u0301", f, 0, 255))
        cols = lambda m: np.flatnonzero(m.any(axis=0))  # noqa: E731
        self.assertLessEqual(cols(nfd)[-1], cols(whole)[-1] + 1)  # the accent sits over the e, not after it
        self.assertTrue((nfd == whole).all())  # one cluster: drawn exactly as the whole, accent included
        self.assertGreater(int(nfd.sum()), int(ink(lambda d: thumb.draw_text(d, 40, 200, "e", f, 0, 255)).sum()))

    def test_scripts_that_need_shaping_are_detected(self):
        self.assertTrue(thumb.needs_shaping("שלום"))
        self.assertTrue(thumb.needs_shaping("مرحبا"))
        self.assertTrue(thumb.needs_shaping("नमस्ते"))
        self.assertFalse(thumb.needs_shaping("Tiếng Việt Ж"))

    def test_scripts_that_need_shaping_are_drawn_whole(self):
        spec = thumb.resolve_font(["arialbd.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arial.ttf"])
        if spec is None:
            self.skipTest("no font with Hebrew here")
        f = thumb.load_font(spec, 80)
        tracked = ink(lambda d: thumb.draw_text(d, 40, 200, "שלום", f, 8, 255))
        plain = ink(lambda d: thumb.draw_text(d, 40, 200, "שלום", f, 0, 255))
        self.assertTrue((tracked == plain).all())
        self.assertEqual(thumb.text_width(f, "שלום", 8), thumb.text_width(f, "שלום", 0))

    def test_render_notes_it_and_warns_without_raqm(self):
        spec = thumb.resolve_font(["arialbd.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arial.ttf"])
        if spec is None:
            self.skipTest("no font with Hebrew here")
        if thumb.LAYOUT_FEATURES:
            _, info = thumb.render(half_busy(), "שלום עולם", title_font=spec)
            self.assertTrue(noted(info, "right-to-left or joining script"))
        saved = thumb.LAYOUT_FEATURES
        thumb.LAYOUT_FEATURES = None
        try:
            _, info = thumb.render(half_busy(), "שלום עולם", title_font=spec)
        finally:
            thumb.LAYOUT_FEATURES = saved
        self.assertTrue(warned(info, "install the FriBiDi library"))


class Glyphs(unittest.TestCase):
    """type-color-2: every element is checked against the font that draws it."""

    def test_label_box_kicker_and_subtitle_are_checked_in_the_label_font(self):
        _, info = thumb.render(half_busy(), "SIMON", "東京 ノーダメージ")
        self.assertTrue(warned(info, "has no glyph for"))
        self.assertTrue(warned(info, "(in the label box)"))
        _, info = thumb.render(half_busy(), "SIMON", "", kicker="Ну что ж")
        self.assertTrue(warned(info, "(in the kicker)"))
        _, info = thumb.render(half_busy(), "SIMON", "", subtitle="Tiếng Việt Ở Đây")
        self.assertTrue(warned(info, "U+1EDE"))

    def test_the_separator_dot_is_checked(self):
        f = thumb.load_font(thumb.bundled_spec("orbitron"), 64)
        self.assertEqual(thumb.missing_glyphs(f, "A · B"), ["·"])
        _, info = thumb.render(half_busy(), "ALOY", "No hit · Ultra hard", label_font="orbitron")
        self.assertTrue(warned(info, "U+00B7"))
        _, info = thumb.render(half_busy(), "ALOY", "No hit · Ultra hard")
        self.assertFalse(warned(info, "no glyph"))  # the default label font has the dot


class Column(unittest.TestCase):
    """type-color-4: type that can't fit its column at the smallest size says so; long titles take three lines."""

    def test_a_long_label_box_warns_with_a_word_count(self):
        _, info = thumb.render(half_busy(), "SAM", "The Complete Hundred Percent Walkthrough Guide")
        self.assertTrue(any("label box is" in w and "wider than the type column" in w and "shorten it to" in w
                            for w in info["warnings"]))

    def test_a_long_title_takes_three_lines_inside_the_column(self):
        with Drawn() as d:
            _, info = thumb.render(half_busy(), "THE LAST OF US PART TWO REMASTERED", "")
        cv = thumb.Canvas(*info["size"])
        title = [b for n, b in info["elements"] if n == "title"]
        self.assertEqual(len(title), 3)
        self.assertLessEqual(max(b[2] for b in title), cv.margin + cv.col_w + 1)
        self.assertFalse(warned(info, "wider than the type column"))

    def test_a_single_long_word_warns(self):
        _, info = thumb.render(half_busy(), "WELTRAUMSCHIFFFAHRTSGESELLSCHAFT", "", title_font="cinzel")
        self.assertTrue(warned(info, "the title is"))
        self.assertTrue(warned(info, "pick a narrower font"))

    def test_post_advice_keeps_the_words(self):
        _, info = thumb.render(half_busy(1280, 1600), "SAM", "The Complete Hundred Percent Walkthrough Guide Book",
                               mode="post", body="Short body.", aspect="16:9")
        col = [w for w in info["warnings"] if "wider than the type column" in w]
        self.assertTrue(col)
        self.assertFalse([w for w in col if "shorten" in w])


class LightBackground(unittest.TestCase):
    """type-color-5: small type follows the background's polarity; auto colors darken on a light one."""

    def test_kicker_and_subtitle_go_dark_on_a_light_panel(self):
        _, info = thumb.render(half_busy(), "ON THE BEACH", "PART 7", kicker="DEATH STRANDING 2",
                               subtitle="THE LAST CITY", backdrop="solid", bg="#f2efe8", title_color="#111111",
                               accent="#111111", lead="title")
        self.assertFalse(warned(info, "the kicker has"))
        self.assertFalse(warned(info, "the subtitle has"))
        self.assertEqual(thumb.ink_for((242, 239, 232)), thumb.INK_DARK)
        self.assertEqual(thumb.ink_for((20, 20, 20)), thumb.WHITE)

    def test_post_text_reads_on_a_light_panel(self):
        _, info = thumb.render(half_busy(1280, 1600), "MAKE YOUR FIRST VIDEO", mode="post",
                               body="Learn to frame a shot and record clear sound.", details="Saturday · Studio 04",
                               title_color="#1a1a1a", backdrop="solid", bg="#f3efe6")
        self.assertFalse(warned(info, "the body has"))
        self.assertFalse(warned(info, "the details has"))

    def test_auto_darkens_on_a_light_background(self):
        bg = (242, 239, 232)
        c = thumb.lift_to_contrast((217, 132, 56), bg)
        lf = float(thumb.wcag_luminance(np.array(c, np.float32) / 255))
        lb = float(thumb.wcag_luminance(np.array(bg, np.float32) / 255))
        self.assertGreaterEqual((lb + 0.05) / (lf + 0.05), thumb.MIN_CONTRAST)
        self.assertGreater(c[0], c[2])  # still orange, darker
        notes = []
        _, desc = thumb._pick_color("auto", "the big type", Image.new("RGB", (64, 64), (230, 120, 30)), bg, notes)
        self.assertIn("darkened for contrast", desc)
        c, desc = thumb._pick_color("auto", "the big type", flat(), bg, notes)
        self.assertEqual(c, thumb.INK_DARK)


class Figures(unittest.TestCase):
    """type-color-7: numbers a font sets as oldstyle figures are flagged."""

    def test_slab_numbers_warn_and_lining_fonts_do_not(self):
        _, info = thumb.render(half_busy(), "1899", "", title_font="slab")
        self.assertTrue(warned(info, "oldstyle figures"))
        _, info = thumb.render(half_busy(), "1899", "", title_font="oswald")
        self.assertFalse(warned(info, "oldstyle"))
        if not thumb.LAYOUT_FEATURES:  # Playfair's lining figures need raqm: the warning says so
            return
        _, info = thumb.render(half_busy(), "1899", "", title_font="slab")
        self.assertTrue(warned(info, "pick a font with lining figures"))
        _, info = thumb.render(half_busy(), "1899", "", title_font="playfair")
        self.assertFalse(warned(info, "oldstyle"))

    def test_without_raqm_lining_figures_need_fribidi(self):
        # Playfair's lining figures are an OpenType feature only raqm layout turns on: without it, every
        # setting is oldstyle, and the fix is the library, not another font
        saved = thumb.LAYOUT_FEATURES
        thumb.LAYOUT_FEATURES = None
        try:
            _, info = thumb.render(half_busy(), "1899", "", title_font="playfair")
        finally:
            thumb.LAYOUT_FEATURES = saved
        self.assertTrue(warned(info, "sets numbers as oldstyle figures"))
        self.assertTrue(warned(info, "install the FriBiDi library"))
        self.assertFalse(warned(info, "only beside letters"))

    def test_tracked_slab_with_letters_names_tracking(self):
        if not thumb.LAYOUT_FEATURES:
            self.skipTest("Pillow without raqm: slab is oldstyle everywhere")
        _, info = thumb.render(half_busy(), "RDR 2", "", title_font="slab")
        self.assertTrue(warned(info, "--title-tracking 0"))
        _, info = thumb.render(half_busy(), "RDR 2", "", title_font="slab", title_tracking=0)
        self.assertFalse(warned(info, "oldstyle"))


class InkCrossover(unittest.TestCase):
    """ink_for picks the ink with the higher contrast: near-black (18, 18, 18) is not black, so just above
    LIGHT_LUMINANCE white still reads better."""

    def test_ink_is_the_higher_contrast_one(self):
        def ratio(a, b):
            la, lb = (float(thumb.wcag_luminance(np.array(c, dtype=np.float32) / 255.0)) for c in (a, b))
            return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
        for v in range(100, 140):
            bg = (v, v, v)
            other = thumb.WHITE if thumb.ink_for(bg) == thumb.INK_DARK else thumb.INK_DARK
            self.assertGreaterEqual(ratio(thumb.ink_for(bg), bg), ratio(other, bg), bg)
        self.assertEqual(thumb.ink_for((119, 119, 119)), thumb.WHITE)  # luminance 0.184: white 4.5:1, ink 4.2:1
        self.assertEqual(thumb.ink_for((242, 239, 232)), thumb.INK_DARK)
        self.assertEqual(thumb.ink_for(None), thumb.WHITE)


class LabelCase(unittest.TestCase):
    """type-color-8: a label font that reads poorly in caps keeps the case given."""

    def test_gothic_label_font_is_not_upper_cased(self):
        with Drawn() as d:
            thumb.render(half_busy(), "Dracula", "Castle Night", kicker="Chapter one", title_font="gothic",
                         label_font="gothic", lead="title")
        self.assertIn("Castle Night", d.texts)
        self.assertIn("Chapter one", d.texts)
        with Drawn() as d:
            thumb.render(half_busy(), "Dracula", "Castle Night", kicker="Chapter one", lead="title")
        self.assertIn("CASTLE NIGHT", d.texts)

    def test_panel_labels_follow_the_label_font_case(self):
        panels = [half_busy(), half_busy()]
        with Drawn() as d:
            _, info = thumb.render(None, "Dracula", panels=panels, panel_labels=["Nr off", "Nr on"],
                                   title_font="gothic", label_font="gothic")
        self.assertIn("Nr off", d.texts)
        self.assertNotIn("NR OFF", d.texts)
        with Drawn() as d:
            thumb.render(None, "Dracula", panels=panels, panel_labels=["Nr off", "Nr on"])
        self.assertIn("NR OFF", d.texts)


class SmallType(unittest.TestCase):
    """type-color-9 and visual-design-5: the small-type warning is measured on the letters and their strokes."""

    def job(self, title_font=None, caps_ok=True):
        j = thumb.Job(info={"warnings": [], "notes": []}, title_font=title_font)
        j.cv, j.caps_ok, j.tspec = thumb.Canvas(1280, 720), caps_ok, thumb.resolve_font([title_font or "playfair@Bold"])
        return j

    def test_cap_height_not_font_size(self):
        j = self.job("typewriter")
        f = thumb.load_font(j.tspec, 80)  # above the old 72 px floor, but its capitals are 46 px
        thumb._check_big_type(j, [("text", "TYPED", f, 0, 0, 0, 0, None, "title")])
        self.assertTrue(warned(j.info, "the big type is only"))
        j = self.job("gothic", caps_ok=False)
        f = thumb.load_font(j.tspec, 100)  # capitals 62 px, x-height 47: Title Case reads at its x-height
        thumb._check_big_type(j, [("text", "Dracula", f, 0, 0, 0, 0, None, "title")])
        self.assertTrue(warned(j.info, "x-height"))

    def test_light_weight_warns_on_its_strokes(self):
        _, info = thumb.render(half_busy(), "FORBIDDEN WEST", "4K · Max settings", title_font="outfit@Light",
                               title_tracking=0.12, lead="title")
        self.assertTrue(warned(info, "strokes are only"))
        self.assertTrue(warned(info, "--title-font outfit@"))
        _, info = thumb.render(half_busy(), "FORBIDDEN WEST", "4K · Max settings", title_font="outfit@Bold",
                               lead="title")
        self.assertFalse(warned(info, "strokes are only"))


if __name__ == "__main__":
    unittest.main()
