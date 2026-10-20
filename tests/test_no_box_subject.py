"""Tests for the face check without --subject-box in scripts/thumb.py. Run: python -m unittest discover -s tests

Without a box, the 16:9 side and the 4:5 and 9:16 crops follow busyness, which can't tell a person from a hay cart.
find_faces looks for a face (OpenCV's frontal face cascade, run in numpy) and _check_found_faces warns, naming the
--subject-box to pass, when the type covers it, its shade darkens it or a crop cuts it; it never moves anything.
black_flag_edward.jpg is a 1024x576 copy of the author's own Assassin's Creed IV Black Flag Resynced capture: Edward
stands center-left, his face at about x 0.355-0.405, y 0.33-0.395, and a hay cart fills the right."""
import os
import re
import sys
import unittest
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "skill", "clean-thumbnail", "scripts"))
sys.path.insert(0, HERE)

import thumb  # noqa: E402
from test_thumb import built_zip_names, cutout, grainy, run_cli  # noqa: E402

EDWARD = os.path.join(HERE, "black_flag_edward.jpg")
FACE = (0.355, 0.33, 0.405, 0.395)  # Edward's face, brows to chin, marked by eye in the 4K capture
TITLE, LABEL = "BLACK FLAG", "Resynced · 4K"


def edward():
    with Image.open(EDWARD) as im:
        return im.convert("RGB")


def found_warnings(info):
    return [w for w in info["warnings"] if "a face the script found" in w]


def named_box(warning):
    m = re.search(r"--subject-box ([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)", warning)
    return tuple(float(v) for v in m.groups()) if m else None


def center_in(box, region):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def scenery(kind, w=1280, h=720, seed=7):
    """Warm game-like scenery with no one in it: the colors a skin-tone rule mistakes for faces."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    def blotches(cell):  # smooth random variation, cell px across
        small = rng.integers(0, 255, (h // cell, w // cell), dtype=np.uint8)
        return np.asarray(Image.fromarray(small).resize((w, h), Image.BICUBIC), np.float32)
    if kind == "sand":  # tan sand with pebbles and soft blotches
        a = np.array([205, 168, 122], np.float32) + (blotches(16)[..., None] - 128) * 0.25 + rng.normal(0, 14, (h, w, 1))
    elif kind == "sky":  # an orange sky darkening to red, with a bright ring
        t = (yy / h)[..., None]
        a = np.array([255, 176, 70], np.float32) * (1 - t) + np.array([196, 64, 26], np.float32) * t
        r = np.hypot(xx - w * 0.6, yy - h * 0.45)
        a = a + np.exp(-((r - h * 0.3) / 8) ** 2)[..., None] * 80 + rng.normal(0, 4, (h, w, 1))
    elif kind == "wood":  # weathered planks with dark gaps and grain
        a = np.array([150, 98, 58], np.float32) + 18 * np.sin(xx / 7 + 3 * np.sin(yy / 40))[..., None]
        a = a * np.where((yy % 90) < 6, 0.35, 1.0)[..., None] + rng.normal(0, 8, (h, w, 1))
    else:  # "rock": orange sandstone with dark crevices
        n = blotches(8)
        crevice = np.where(np.abs(n - 128) < 6, 0.4, 1.0)
        a = np.array([196, 112, 62], np.float32) * ((0.75 + n / 510) * crevice)[..., None] + rng.normal(0, 10, (h, w, 1))
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")
    return img.filter(ImageFilter.GaussianBlur(0.6))


class FoundFace(unittest.TestCase):
    """The Black Flag case: no box, so the type lands on Edward and the vertical crops follow the hay cart."""

    def test_black_flag_16x9_warns_and_names_the_box(self):
        img = edward()
        for lead in ("title", "label"):
            _, info = thumb.render(img, TITLE, LABEL, lead=lead)
            faces = info.get("faces") or []
            self.assertEqual(len(faces), 1, info.get("faces"))
            self.assertTrue(center_in(faces[0]["box"], FACE), faces)
            self.assertTrue(faces[0]["warned"])
            w = found_warnings(info)
            self.assertEqual(len(w), 1, info["warnings"])
            self.assertIn("the type overlaps a face the script found on 16:9", w[0])
            box = named_box(w[0])
            self.assertIsNotNone(box, w[0])
            self.assertTrue(center_in(box, FACE), box)
            found = faces[0]["box"]  # the named box holds the face found: rounded outward
            self.assertTrue(box[0] <= found[0] and box[1] <= found[1] and box[2] >= found[2] and box[3] >= found[3],
                            (box, found))

    def test_vertical_crops_warn(self):
        img = edward()
        _, info = thumb.render(img, TITLE, LABEL, lead="title", aspect="4:5")
        w = found_warnings(info)
        self.assertEqual(len(w), 1, info["warnings"])
        self.assertIn("the 4:5 crop cuts the person around a face the script found", w[0])
        self.assertIn("at its left edge", w[0])
        self.assertIn("so the crop centers it", w[0])
        self.assertEqual(info["faces"][0]["inside"], 1.0)  # the face itself is in frame: the person is cut
        _, info = thumb.render(img, TITLE, LABEL, lead="title", aspect="9:16")
        w = found_warnings(info)
        self.assertEqual(len(w), 1, info["warnings"])
        self.assertIn("only 0% of a face the script found", w[0])
        self.assertIn("inside the Reels/Shorts safe zone", w[0])

    def test_the_named_box_fixes_the_layout(self):
        img = edward()
        _, info = thumb.render(img, TITLE, LABEL, lead="title")
        w = found_warnings(info)
        box = named_box(w[0]) if w else None
        self.assertIsNotNone(box, info["warnings"])
        _, fixed = thumb.render(img, TITLE, LABEL, lead="title", subject_box=box)
        self.assertNotIn("faces", fixed)  # an explicit box: the face check stands down
        self.assertEqual(fixed["text_side"], "right")
        self.assertEqual([x for x in fixed["warnings"] if "subject-box" in x], [])
        sb = fixed["subject_box"]
        self.assertTrue(sb["inside"] >= 0.99 and sb["visibility"] >= 0.9 and sb["under_type"] == 0, sb)
        _, tall = thumb.render(img, TITLE, LABEL, lead="title", aspect="4:5", subject_box=box)
        self.assertEqual([x for x in tall["warnings"] if "subject-box" in x], [])
        self.assertTrue(any("crop centers --subject-box" in n for n in tall["notes"]), tall["notes"])

    def test_shade_alone_is_reported(self):
        _, info = thumb.render(edward(), "AC", "", lead="title", text_side="left", shade=0.88)
        w = found_warnings(info)
        self.assertEqual(len(w), 1, info["warnings"])
        self.assertIn("the type shade darkens a face the script found on 16:9", w[0])
        self.assertRegex(w[0], r"\d+% under heavy shade, \d+% visible")
        self.assertEqual(info["faces"][0]["under_type"], 0.0)

    def test_clear_face_gets_a_note(self):
        _, info = thumb.render(edward(), TITLE, LABEL, lead="title", text_side="right")
        self.assertEqual(found_warnings(info), [])
        faces = info.get("faces") or []
        self.assertEqual([f["warned"] for f in faces], [False])
        notes = [n for n in info["notes"] if "a face the script found" in n]
        self.assertEqual(len(notes), 1, info["notes"])
        self.assertIn("is in frame and clear of the type on 16:9; --subject-box", notes[0])

    def test_found_face_lands_where_its_box_would(self):
        img = edward()  # a --crop moves the face on the canvas; it is measured where it ends up
        for aspect, pinned in (("16:9", dict(text_side="left")), ("4:5", dict(focus=0.3, band="bottom"))):
            # the layout is pinned, so the box itself can't move the type or the crop
            kw = dict(lead="title", aspect=aspect, crop=(0.2, 0, 1, 1), **pinned)
            _, info = thumb.render(img, TITLE, LABEL, **kw)
            self.assertEqual(len(info.get("faces") or []), 1, aspect)
            face = info["faces"][0]
            _, boxed = thumb.render(img, TITLE, LABEL, subject_box=face["box"], **kw)
            self.assertEqual(face["rect"], boxed["subject_box"]["rect"], aspect)
            self.assertEqual(face["inside"], boxed["subject_box"]["inside"], aspect)

    def test_cli_warns_and_no_face_check_skips_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            code, out = run_cli([EDWARD, "--title", TITLE, "--label", LABEL, "--lead", "title", "-o",
                                 os.path.join(d, "bf")])
            self.assertEqual(code, 0, out)
            self.assertIn("warning: the type overlaps a face the script found on 16:9", out)
            code, out = run_cli([EDWARD, "--title", TITLE, "--label", LABEL, "--lead", "title", "--no-face-check",
                                 "-o", os.path.join(d, "bf2")])
            self.assertEqual(code, 0, out)
            self.assertNotIn("a face the script found", out)


class FaceCheckScope(unittest.TestCase):
    """What the check leaves alone: an explicit box, the off switch, the user's own --focus and --crop, cut-outs
    and panels; and it never changes the picture."""

    def test_explicit_box_wins(self):
        img = edward()
        _, plain = thumb.render(img, TITLE, LABEL, lead="title")
        self.assertTrue(plain.get("faces"), "the face is found without a box")
        _, boxed = thumb.render(img, TITLE, LABEL, lead="title", subject_box=(0.6, 0.4, 0.75, 0.6))
        self.assertNotIn("faces", boxed)
        self.assertEqual(found_warnings(boxed), [])

    def test_off_switch(self):
        img = edward()
        on, info_on = thumb.render(img, TITLE, LABEL, lead="title", face_check=True)
        off, info_off = thumb.render(img, TITLE, LABEL, lead="title", face_check=False)
        self.assertTrue(found_warnings(info_on))
        self.assertNotIn("faces", info_off)
        self.assertEqual(found_warnings(info_off), [])
        self.assertTrue(np.array_equal(np.asarray(on), np.asarray(off)), "the check moved something")
        self.assertIsNone(off.info.get("boxes"))

    def test_focus_and_crop_are_the_users_choice(self):
        img = edward()
        share = (1080 / max(1080 / img.width, 1350 / img.height)) / img.width
        focus = thumb.subject_focus(img, share)  # the crop the script would pick, now asked for
        _, info = thumb.render(img, TITLE, LABEL, lead="title", aspect="4:5", focus=focus)
        self.assertEqual([f["warned"] for f in info.get("faces") or []], [False])
        self.assertEqual(found_warnings(info), [])
        _, info = thumb.render(img, TITLE, LABEL, lead="title", crop=(0.47, 0, 1, 1))  # Edward cropped away
        self.assertEqual(info.get("faces"), [])
        self.assertEqual(found_warnings(info), [])

    def test_cutouts_and_panels_keep_their_own_checks(self):
        img = edward()
        _, alone = thumb.render(img, TITLE, LABEL, lead="title")
        self.assertTrue(alone.get("faces"))
        _, info = thumb.render(img, TITLE, LABEL, lead="title", subjects=[cutout()])
        self.assertNotIn("faces", info)
        _, info = thumb.render(None, TITLE, LABEL, lead="title", panels=[img, img])
        self.assertNotIn("faces", info)


class NoFalseAlarms(unittest.TestCase):
    """Sand, sky, wood, rock and a hay cart look like skin to a color rule; the cascade sees no face in them."""

    def test_scenery_stays_silent(self):
        img = edward()
        cart = img.crop((int(0.47 * img.width), 0, img.width, img.height))  # the hay cart, no one in it
        for name, im in [("cart", cart)] + [(k, scenery(k)) for k in ("sand", "sky", "wood", "rock")]:
            self.assertEqual(thumb.find_faces(im), [], name)
            for aspect in ("16:9", "4:5"):
                _, info = thumb.render(im, TITLE, LABEL, lead="title", aspect=aspect)
                self.assertEqual(info.get("faces"), [], (name, aspect))
                self.assertEqual(found_warnings(info), [], (name, aspect))
        _, info = thumb.render(grainy(1280, 720, color=(200, 150, 120)), TITLE, LABEL, lead="title")
        self.assertEqual(info.get("faces"), [])

    def test_finder_is_deterministic_and_mirrors(self):
        img = edward()
        thumb._face_cache[:] = []
        first = thumb.find_faces(img)
        thumb._face_cache[:] = []
        self.assertEqual(thumb.find_faces(img), first)
        self.assertEqual(len(first), 1)
        b = first[0]["box"]
        m = thumb.find_faces(img.transpose(Image.FLIP_LEFT_RIGHT))
        self.assertEqual(len(m), 1)
        self.assertAlmostEqual(m[0]["box"][0], 1 - b[2], delta=0.01)
        self.assertAlmostEqual(m[0]["box"][1], b[1], delta=0.01)
        self.assertGreaterEqual(first[0]["hits"], thumb.FACE_MIN_HITS)

    def test_the_model_ships_with_the_skill(self):
        self.assertTrue(os.path.isfile(thumb.FACE_MODEL), thumb.FACE_MODEL)
        with open(thumb.FACE_MODEL, encoding="utf-8") as f:
            head = f.read(3000)
        self.assertIn("Intel License Agreement", head)  # its license travels with it
        self.assertIn("clean-thumbnail/models/haarcascade_frontalface_alt2.xml", built_zip_names())


class FaceCheckSkips(unittest.TestCase):
    """A check that can't run says so: a gray capture (the finder gates on skin tone) or a missing model file."""

    def test_gray_capture_says_the_check_was_skipped(self):
        gray = edward().convert("L").convert("RGB")
        _, info = thumb.render(gray, TITLE, LABEL, size="720p", lead="title")
        self.assertTrue(any(n.startswith("face check skipped: the capture has no color") for n in info["notes"]),
                        info["notes"])
        _, info = thumb.render(edward(), TITLE, LABEL, size="720p", lead="title")
        self.assertFalse(any(n.startswith("face check skipped") for n in info["notes"]), info["notes"])

    def test_missing_model_says_the_check_was_skipped(self):
        model, cached = thumb.FACE_MODEL, thumb._face_model
        thumb.FACE_MODEL, thumb._face_model = os.path.join(HERE, "no-such-model.xml"), None
        try:
            _, info = thumb.render(edward(), TITLE, LABEL, size="720p", lead="title")
        finally:
            thumb.FACE_MODEL, thumb._face_model = model, cached
        self.assertTrue(any(n.startswith("face check skipped: models/") for n in info["notes"]), info["notes"])
        self.assertEqual(found_warnings(info), [])


class FacePreview(unittest.TestCase):
    """The preview's boxes tile outlines a face the check warned about, so the box can be confirmed by eye."""

    def test_boxes_tile_outlines_the_face(self):
        img = edward()
        out, info = thumb.render(img, TITLE, LABEL, lead="title")
        self.assertTrue(info.get("faces"), info["warnings"])
        rect = info["faces"][0]["rect"]
        self.assertEqual(out.info.get("boxes"), [("face found", rect)])
        plain = thumb.preview_sheet(thumb.render(img, TITLE, LABEL, lead="title", face_check=False)[0])
        sheet = thumb.preview_sheet(out)
        self.assertEqual(sheet.width, plain.width + 320 + 28)
        k = 320.0 / out.width
        tx = sheet.width - 28 - 320  # the boxes tile is the last one in the row
        a = np.asarray(sheet, np.int16)
        top = a[14 + int(round(rect[1] * k)), tx + int(rect[0] * k) + 2: tx + int(rect[2] * k) - 2]
        self.assertTrue((np.abs(top - np.array(thumb.BOX_COLORS["face found"])).max(axis=1) <= 40).any())
        tall, info = thumb.render(img, TITLE, LABEL, lead="title", aspect="9:16")  # cropped out: nothing to outline
        self.assertTrue(found_warnings(info))
        self.assertIsNone(tall.info.get("boxes"))
        self.assertNotIn("last tile", found_warnings(info)[0])

    def test_clear_face_is_not_outlined(self):
        out, info = thumb.render(edward(), TITLE, LABEL, lead="title", text_side="right")
        self.assertEqual([f["warned"] for f in info.get("faces") or []], [False])
        self.assertIsNone(out.info.get("boxes"))


if __name__ == "__main__":
    unittest.main()
