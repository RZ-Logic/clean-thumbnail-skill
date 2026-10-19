"""Regression tests for the v0.9.1 post fixes in scripts/thumb.py (--mode post, --backdrop solid).
Run: python -m unittest discover -s tests

Long links and emails fit the type column (broken without a hyphen) or are named in a warning that keeps the
user's words; a post that can't fit says how many lines are lost; the CLI prints the lines as laid out; a 9:16
solid post keeps the photo where Reels and the profile grid show it; a typed '\\n' inside a Windows path and a
dot inside a word are drawn as typed; the solid idle note names the setting, not a flag; and the label font is
checked for every character of --body and --details."""
import io
import json
import os
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
from test_thumb import run_cli, warned  # noqa: E402

BS = "\\"  # one backslash, as the user types it in a shell
URL = "https://www.example-coffee-roasters-and-bakery.com/events/saturday-cupping-session-registration"
BG = "#1c2430"


def photo(w=1280, h=720, color=(150, 120, 80), seed=3):
    """A warm, grainy photo-like frame (bright enough to tell from the panel)."""
    rng = np.random.default_rng(seed)
    a = np.full((h, w, 3), color, np.float32) + rng.normal(0, 10, (h, w, 1))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def dark(w=1280, h=720, seed=4):
    rng = np.random.default_rng(seed)
    a = np.linspace(30, 90, w, dtype=np.float32)[None, :, None] * np.ones((h, 1, 3), np.float32)
    a += rng.normal(0, 6, (h, w, 1))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def boxes(info, name):
    return [b for n, b in info["elements"] if n == name]


def max_w(info):
    """The post type column's width at this render's size and aspect (what the fit holds every line to)."""
    cv = thumb.Canvas(*info["size"])
    if not cv.tall:
        cv.col_share = thumb.POST_COL_SHARE
    return cv.col_w


def no_cut_advice(test, info):
    """Post warnings never tell the agent to cut the user's words."""
    for w in info["warnings"]:
        test.assertNotIn("shorten it.", w)


class LongWords(unittest.TestCase):
    """post-backdrop-1: a long link or email fits the column, broken where a link may break, never clipped."""

    def assert_in_column(self, info, name):
        cv = thumb.Canvas(*info["size"])
        for b in boxes(info, name):
            self.assertLessEqual(b[2] - b[0], max_w(info) + 1, (name, b))
            self.assertTrue(thumb.inside(b, cv.frame_box), (name, b, cv.frame_box))

    def assert_word_for_word(self, info, name, text):
        pt = info["post_text"][name]
        joined = thumb.post_joined(pt["lines"], pt["continued"])
        self.assertEqual(thumb.post_words(joined), thumb.post_words(text))
        self.assertNotIn("-" + " ", joined.replace(" - ", ""))  # no hyphen added at a break

    def test_word_pieces_break_links_without_hyphens(self):
        self.assertEqual(thumb.word_pieces("https://www.example.com/events/a-b?x=1&y=2"),
                         ["https://", "www", ".example", ".com/", "events/", "a-", "b?", "x=", "1&", "y=", "2"])
        for w in (URL, "careers@example.org.", "wait...", "really?", "--flag"):
            self.assertEqual("".join(thumb.word_pieces(w)), w)
        self.assertEqual(thumb.word_pieces("careers@example.org."), ["careers@example", ".org."])
        self.assertEqual(thumb.word_pieces("wait..."), ["wait..."])

    def test_url_in_details_fits_the_4x5_column(self):
        details = "Register at " + URL
        _, info = thumb.render(dark(), "Saturday Cupping", mode="post", body="Short body.", details=details)
        self.assertEqual(info["aspect"], "4:5")
        self.assert_in_column(info, "details")
        self.assert_word_for_word(info, "details", details)
        pt = info["post_text"]["details"]
        self.assertGreater(len(pt["lines"]), 1)
        self.assertTrue(pt["continued"])  # the link continues across lines
        self.assertEqual(pt["overflow"], [])
        self.assertFalse(warned(info, "runs outside"), info["warnings"])
        self.assertFalse(warned(info, "not laid out word for word"))

    def test_url_in_body_fits_the_9x16_column(self):
        body = "Register at " + URL
        _, info = thumb.render(dark(), "Saturday Cupping", mode="post", body=body, aspect="9:16")
        self.assert_in_column(info, "body")
        self.assert_word_for_word(info, "body", body)
        self.assertFalse(warned(info, "runs outside"), info["warnings"])

    def test_16x9_solid_link_stays_on_the_panel(self):
        details = "Tickets: eventbrite.com/e/saturday-cupping-1234567890"
        _, info = thumb.render(photo(), "Saturday Cupping", mode="post", body="Short body.", details=details,
                               aspect="16:9", text_side="left", bg=BG, backdrop="solid")
        x0 = info["image_rect"][0]
        for b in boxes(info, "details") + boxes(info, "body"):
            self.assertLessEqual(b[2], x0, (b, info["image_rect"]))  # not across the edge into the photo
        self.assert_in_column(info, "details")
        self.assert_word_for_word(info, "details", details)

    def test_an_unbreakable_word_shrinks_the_text_not_the_headline(self):
        word = "ABCDEFGHJKLMNPQRSTUVWXYZ2345"  # no break point, wider than the 9:16 column at full size
        body = "Use code %s at the till." % word
        _, ref = thumb.render(dark(), "Saturday Cupping", mode="post", body="Use code at the till.", aspect="9:16")
        _, info = thumb.render(dark(), "Saturday Cupping", mode="post", body=body, aspect="9:16")
        self.assert_in_column(info, "body")
        self.assertIn(word, info["post_text"]["body"]["lines"])  # whole, on its own line
        self.assertLess(info["post_text"]["body"]["feed_cap"], thumb.POST_BODY_CAP)
        self.assertEqual(info["big_size"], ref["big_size"])  # the headline had the room: it keeps its size

    def test_a_long_word_that_sets_the_size_is_named(self):
        word = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789ABCDEFGH"  # fits the 9:16 column only below the readable size
        _, info = thumb.render(dark(), "Saturday Cupping", mode="post", body="Use code %s at the till." % word,
                               aspect="9:16")
        self.assertEqual(info["post_text"]["body"]["overflow"], [])
        self.assertIn(word, info["post_text"]["body"]["lines"])
        small = [x for x in info["warnings"] if x.startswith("post text too small")]
        self.assertEqual(len(small), 1, info["warnings"])
        self.assertIn('it is set this small so "%s" fits the column: use --aspect 4:5' % word, small[0])
        self.assertNotIn("shorten the text", small[0])

    def test_a_word_that_never_fits_is_named_and_kept(self):
        _, info = thumb.render(dark(), "REVIEWS", mode="post", body="x" * 400)
        self.assertEqual(info["post_text"]["body"]["lines"], ["x" * 400])
        self.assertEqual(info["post_text"]["body"]["overflow"], [0])
        w = [x for x in info["warnings"] if x.startswith("the body runs outside the edge of the frame")]
        self.assertEqual(len(w), 1, info["warnings"])
        self.assertIn('"xxxx', w[0])
        self.assertIn("wider than the column", w[0])
        self.assertIn("ask the user to approve a shorter form", w[0])
        self.assertNotIn("--aspect 4:5", w[0])  # 4:5 already has the widest column
        small = [x for x in info["warnings"] if x.startswith("post text too small")]
        self.assertEqual(len(small), 1, info["warnings"])
        self.assertIn('"xxxx', small[0])
        self.assertIn("is still wider than the column at that size", small[0])
        no_cut_advice(self, info)
        _, reels =thumb.render(dark(), "REVIEWS", mode="post", body="y" * 300, aspect="9:16")
        w = [x for x in reels["warnings"] if x.startswith("the body runs outside the Reels/Shorts safe zone")]
        self.assertEqual(len(w), 1, reels["warnings"])
        self.assertIn("--aspect 4:5", w[0])
        no_cut_advice(self, reels)


class Overflow(unittest.TestCase):
    """post-backdrop-3: a post too long for the frame even at the smallest size says how many lines are lost."""

    def test_lines_off_the_canvas_are_counted(self):
        body = " ".join("word%d" % i for i in range(1, 401))
        _, info = thumb.render(dark(), "Saturday Cupping", mode="post", body=body, details="Sat · Sun", aspect="16:9")
        cv = thumb.Canvas(*info["size"])
        hidden = [b for b in boxes(info, "body") if b[1] >= cv.h or b[3] <= 0]
        self.assertTrue(hidden)
        w = [x for x in info["warnings"] if x.startswith("the body runs outside the edge of the frame")]
        self.assertEqual(len(w), 1, info["warnings"])
        self.assertIn("%d of its %d lines fall outside the image and are not shown"
                      % (len(hidden), len(boxes(info, "body"))), w[0])
        self.assertIn("--aspect 4:5", w[0])
        self.assertTrue(warned(info, "the details runs outside the edge of the frame: all of it falls outside"))
        self.assertEqual(info["post_text"]["details"]["overflow"], [0])
        no_cut_advice(self, info)


class CliReport(unittest.TestCase):
    """post-backdrop-4: the CLI prints each slot's lines as laid out, after the 'post text:' line."""

    def test_lines_are_printed(self):
        with tempfile.TemporaryDirectory() as d:
            dark().save(os.path.join(d, "cap.png"))
            body = "Line one" + BS + "nLine two" + BS + "n" + BS + "nPara two here."
            code, text = run_cli(["cap.png", "--mode", "post", "--title", "Saturday Cupping", "--body", body,
                                  "--details", "Register at " + URL, "-o", "b1"], cwd=d)
            self.assertEqual(code, 0, text)
            rows = text.splitlines()
            i = [k for k, r in enumerate(rows) if r.startswith("post text: ")][0]
            self.assertEqual(rows[i + 1], "  body: Line one | Line two | Para two here.")
            self.assertTrue(rows[i + 2].startswith("  details: Register at https://"), rows[i + 2])
            self.assertIn(" |+ ", rows[i + 2])  # the link continues on the next line, no space
            self.assertEqual(rows[i + 2][len("  details: "):].replace(" |+ ", "").replace(" | ", " "),
                             "Register at " + URL)

    def test_overflow_is_marked_and_the_report_survives_a_narrow_console(self):
        pt = {"body": {"lines": ["Arrow → 日本", "x" * 20], "continued": [], "overflow": [1]}}
        old = sys.stdout
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        try:
            rows = thumb.post_report_lines(pt)
            print(rows[0])  # would raise UnicodeEncodeError without the escape
        finally:
            sys.stdout = old
        self.assertEqual(rows, ["  body: Arrow \\u2192 \\u65e5\\u672c | " + "x" * 20 + " [does not fit]"])


class ReelsSolid(unittest.TestCase):
    """post-backdrop-2: 9:16 --backdrop solid keeps the photo where the Reels screen and profile grid show it."""

    BODY = ("Join us for a guided tasting of three new single-origin coffees from Ethiopia, Colombia and "
            "Guatemala. Bring a friend.")

    def test_long_message_moves_the_band_up(self):
        _, info = thumb.render(photo(), "Saturday Cupping", mode="post", body=self.BODY,
                               details="Sat 9 am · 12 King St · Free", aspect="9:16", bg=BG, backdrop="solid")
        cv = thumb.Canvas(*info["size"])
        rect = info["image_rect"]
        self.assertEqual(info["text_side"], "top")
        self.assertEqual(rect[3], cv.h)  # the photo fills the frame below the type
        self.assertGreaterEqual(thumb.solid_seen(cv, rect), thumb.SOLID_MIN_SEEN)
        self.assertGreater(rect[1], cv.h * thumb.REELS_SAFE["top"])
        self.assertTrue(any(n.startswith("type band at the top: the photo below it") for n in info["notes"]))
        self.assertFalse(warned(info, "the photo fills only"))
        sb = thumb.Canvas(*info["size"]).safe_box
        for name, b in info["elements"]:  # the type stays in the safe zone, on the panel
            self.assertTrue(thumb.inside(b, sb), (name, b))
            self.assertLessEqual(b[3], rect[1], (name, b))

    def test_forced_bottom_band_warns(self):
        _, info = thumb.render(photo(), "Saturday Cupping", mode="post", body=self.BODY,
                               details="Sat 9 am · 12 King St · Free", aspect="9:16", bg=BG, backdrop="solid",
                               band="bottom")
        cv = thumb.Canvas(*info["size"])
        self.assertEqual(info["image_rect"][1], 0)
        self.assertLess(thumb.solid_seen(cv, info["image_rect"]), thumb.SOLID_MIN_SEEN)
        w = [x for x in info["warnings"] if x.startswith("the photo fills only")]
        self.assertEqual(len(w), 1, info["warnings"])
        self.assertIn("--aspect 4:5", w[0])

    def test_short_message_keeps_the_bottom_band(self):
        _, info = thumb.render(photo(), "REVIEWS", mode="post", body="Bring a laptop.", aspect="9:16", bg=BG,
                               backdrop="solid")
        self.assertEqual(info["text_side"], "bottom")
        self.assertEqual(info["image_rect"][1], 0)
        self.assertFalse(warned(info, "the photo fills only"))


class TypedText(unittest.TestCase):
    """post-backdrop-6 and -7: a typed '\\n' inside a path and a dot inside a word are drawn as typed."""

    def test_backslash_n_in_a_path_is_text(self):
        body = "Save it to C:" + BS + "new folder today"
        _, info = thumb.render(dark(), "T", mode="post", body=body)
        self.assertEqual(info["post_text"]["body"]["lines"], [body])
        self.assertEqual(thumb.post_paragraphs("C:" + BS + "Users" + BS + "nina is home"),
                         [["C:" + BS + "Users" + BS + "nina is home"]])
        # a relative or network path has no drive: any other backslash in the word still marks it as a path
        self.assertEqual(thumb.post_paragraphs("Open logs" + BS + "new" + BS + "2026 today"),
                         [["Open logs" + BS + "new" + BS + "2026 today"]])
        # a '\n' between words, after a colon or a full stop still breaks the line
        self.assertEqual(thumb.post_paragraphs("When:" + BS + "nSaturday. Free" + BS + "nDrop in"),
                         [["When:", "Saturday. Free", "Drop in"]])
        self.assertEqual(thumb.post_words("one" + BS + "ntwo C:" + BS + "new"), ["one", "two", "C:" + BS + "new"])

    def test_dot_inside_a_word_is_not_a_separator(self):
        details = "Café·Bar · 12 King Street West, Toronto · Saturday the twenty-fourth of October · Free entry"
        _, info = thumb.render(dark(), "T", mode="post", details=details)
        lines = info["post_text"]["details"]["lines"]
        self.assertTrue(lines[0].startswith("Café·Bar"), lines)
        self.assertFalse(any("Café · Bar" in ln for ln in lines), lines)
        self.assertEqual(thumb.post_words(details)[0], "Café·Bar")
        self.assertNotEqual(thumb.post_words("Café · Bar"), thumb.post_words("Café·Bar"))  # the check sees it


class SolidIdleNote(unittest.TestCase):
    """post-backdrop-9: the note names the setting and its value, so a channel file's shade isn't called a flag."""

    def test_channel_shade_is_not_called_a_flag(self):
        with tempfile.TemporaryDirectory() as d:
            photo().save(os.path.join(d, "cap.png"))
            with open(os.path.join(d, "ch.json"), "w") as f:
                json.dump({"mode": "post", "backdrop": "solid", "shade": 0.9}, f)
            code, text = run_cli(["cap.png", "--channel", "ch.json", "--title", "Saturday Cupping", "-o", "c"], cwd=d)
            self.assertEqual(code, 0, text)
            notes = [r for r in text.splitlines() if "no effect" in r]
            self.assertEqual(notes, ["note: shade 0.90 has no effect with --backdrop solid: the type sits on an "
                                     "opaque panel"])
            code, text = run_cli(["cap.png", "--title", "SIMON", "--backdrop", "solid", "--feather", "0.2", "-o", "f"],
                                 cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn("note: feather 0.20 has no effect with --backdrop solid", text)
            self.assertNotIn("--shade", text)

    def test_api_names_both(self):
        _, info = thumb.render(photo(), "SIMON", lead="title", bg=BG, backdrop="solid", shade=0.6, feather=0.2)
        self.assertIn("shade 0.60 and feather 0.20 have no effect with --backdrop solid: the type sits on an "
                      "opaque panel", info["notes"])
        _, info = thumb.render(photo(), "SIMON", lead="title", bg=BG, backdrop="solid")
        self.assertFalse(any("no effect" in n for n in info["notes"]), info["notes"])


class MissingGlyphs(unittest.TestCase):
    """--body and --details are checked against the label font that draws them."""

    def test_missing_glyph_is_named(self):
        _, info = thumb.render(dark(), "T", mode="post", body="Tea and 日本 sweets", details="Sat → Sun")
        w = [x for x in info["warnings"] if "has no glyph" in x]
        self.assertEqual(len(w), 1, info["warnings"])
        self.assertIn("the label font, which sets the body, has no glyph for U+65E5, U+672C", w[0])
        self.assertIn("--label-font", w[0])
        self.assertEqual(info["post_text"]["body"]["lines"], ["Tea and 日本 sweets"])  # kept, not dropped
        _, ok = thumb.render(dark(), "T", mode="post", body="Café crème · déjà vu", details="Sat · Sun")
        self.assertFalse(warned(ok, "has no glyph"), ok["warnings"])


if __name__ == "__main__":
    unittest.main()


class FollowUps(unittest.TestCase):
    """Fable's v0.9.1 leftovers: post text takes a light panel's ink, the post title's frame warning keeps the
    user's words, and the stock channel file (aspect 16:9 only) prints no post-mode note."""

    def test_post_text_takes_the_light_panels_ink(self):
        _, info = thumb.render(dark(), "Hello", mode="post", body="Body text here.", details="Sat",
                               backdrop="solid", bg="#f2efe8")
        self.assertFalse(warned(info, "the body has"), info["warnings"])
        self.assertFalse(warned(info, "the details has"), info["warnings"])
        self.assertTrue(warned(info, "the --bg panel is light: use a dark --title-color"), info["warnings"])
        self.assertFalse(warned(info, "use a darker --bg"), info["warnings"])
        _, info = thumb.render(dark(), "Hello", mode="post", body="Body text here.", details="Sat",
                               backdrop="solid", bg="#f2efe8", title_color="#111111")
        self.assertFalse([w for w in info["warnings"] if "contrast" in w], info["warnings"])

    def test_post_title_out_of_frame_never_says_shorten_it(self):
        body = " ".join("word%d" % i for i in range(1, 401))
        _, info = thumb.render(dark(), "Saturday Cupping", mode="post", aspect="9:16", body=body, details="Sat · Sun")
        title = [w for w in info["warnings"] if w.startswith("the title runs outside")]
        self.assertTrue(title, info["warnings"])
        self.assertIn("don't cut the user's words without asking", title[0])
        self.assertFalse([w for w in info["warnings"] if "shorten it" in w], info["warnings"])

    def test_stock_channel_file_prints_no_post_note(self):
        stock = os.path.join(ROOT, "skill", "clean-thumbnail", "references", "channel.example.json")
        note = "post mode renders 4:5 unless --aspect is given"
        with tempfile.TemporaryDirectory() as d:
            dark().save(os.path.join(d, "cap.png"))
            with open(stock, encoding="utf-8") as f, open(os.path.join(d, "clean-thumbnail.json"), "w",
                                                         encoding="utf-8") as g:
                g.write(f.read())
            args = ["cap.png", "--mode", "post", "--title", "HI", "--body", "x y", "-o", "o3"]
            code, text = run_cli(args, cwd=d)
            self.assertEqual(code, 0, text)
            self.assertNotIn(note, text)
            with open(os.path.join(d, "clean-thumbnail.json"), "w", encoding="utf-8") as g:
                json.dump({"aspect": "16:9,4:5"}, g)
            code, text = run_cli(args, cwd=d)
            self.assertEqual(code, 0, text)
            self.assertIn(note, text)
