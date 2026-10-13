#!/usr/bin/env python3
"""Render a restrained, editorial YouTube thumbnail from real images.

Quiet type over real frames: no outlines, glows, arrows or color washes. Images
take roles, and every thumbnail is a stack of them plus the type:

  background  the capture (positional); fills the canvas, dark side for type
  panels      2-3 captures side by side for comparisons (--panel, repeated)
  subject     a cut-out with transparency in front of the background (--subject)
  inset       an image placed inside a region of the background (--inset)

Subjects and insets are matched to the background (color cast, tone, grain,
sharpness) so the stack reads as one photograph.

Usage:
  python thumb.py CAPTURE --title "SIMON" --label "No hit · Parry only"
  python thumb.py --panel a.png --panel b.png --panel-label 25H2 --panel-label 26H2 --title GAMING
  python thumb.py BG.png --subject me.png --title "4 MONTHS" --label "Of silence"
  python thumb.py CRT.png --inset game.png --inset-box 0.18,0.14,0.62,0.70 --title "ON A CRT" --label "DLSS 5"

Writes, per variant: <out>.jpg (master, up to 3840x2160, never upscaled past the
source), <out>_phone.jpg (1280x720 under 2 MB for phone uploads) and
<out>_preview.png (feed sizes, 88 px notification and a squint test, dark and
light). --lead ab (default) writes an A (title leads) and B (label leads) pair.
--aspect 4:5 (Facebook/Instagram feed) and 9:16 (Reels, Shorts, Stories) add
<out>_4x5 and <out>_9x16 files with a 1080-wide copy and a feed or Reels preview;
--aspect all renders every format.
Settings in ./clean-thumbnail.json or --channel FILE apply to every video.
Requires numpy and Pillow.
"""
import argparse
import io
import json
import os
import unicodedata
import sys

import numpy as np
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFile, ImageFilter, ImageFont, ImageOps

__version__ = "0.7.0"

SIZES = {"720p": (1280, 720), "1080p": (1920, 1080), "1440p": (2560, 1440), "4k": (3840, 2160)}
PHONE_SIZE = (1280, 720)
PHONE_MAX_BYTES = 2 * 1024 * 1024  # YouTube's limit for phone uploads
MASTER_MAX_BYTES = 50 * 1024 * 1024  # YouTube's limit for desktop uploads
# Aspect ratios: master sizes largest first, the standard upload copy, and the file suffix.
# 4:5 is the Facebook and Instagram feed portrait (1080x1350); 9:16 covers Reels, Shorts and Stories.
ASPECTS = {
    "16:9": {"sizes": [SIZES[k] for k in ("4k", "1440p", "1080p", "720p")], "copy": PHONE_SIZE, "suffix": ""},
    "4:5": {"sizes": [(2160, 2700), (1440, 1800), (1080, 1350)], "copy": (1080, 1350), "suffix": "_4x5"},
    "9:16": {"sizes": [(2160, 3840), (1440, 2560), (1080, 1920)], "copy": (1080, 1920), "suffix": "_9x16"},
}
# Reels and Shorts cover the top 14% (header), bottom 35% (caption, audio) and right 15% (buttons).
REELS_SAFE = {"top": 0.14, "bottom": 0.35, "right": 0.15}
COL_SHARE = 0.40  # type column width as a share of the canvas
MAX_LUMA_BEHIND_TYPE = 0.45  # white type over sRGB 0.45 is about 4.5:1 contrast
MIN_CONTRAST = 4.5  # WCAG AA for normal text, applied to every element
CHANNEL_FILE = "clean-thumbnail.json"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# First font found wins. "file@Instance" picks a named instance of a variable font; a bare short
# name ("cinzel", "outfit@Bold") is one of the fonts bundled in ../fonts (see --list-fonts).
LABEL_FONTS = ["bahnschrift.ttf@SemiBold", "segoeuib.ttf", "Avenir Next.ttc", "HelveticaNeue.ttc",
               "outfit@SemiBold", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"]
STYLES = {
    # high-contrast or old-style serif: fantasy, period, literary, sport-heritage
    "serif": {"title": ["BOD_R.TTF", "Didot.ttc", "GARA.TTF", "constan.ttf", "georgia.ttf", "playfair",
                        "DejaVuSerif.ttf", "LiberationSerif-Regular.ttf", "times.ttf"],
              "tracking": 0.06},
    # geometric or grotesque sans: tech, sci-fi, modern, competitive
    "sans": {"title": ["bahnschrift.ttf@Bold", "segoeuib.ttf", "HelveticaNeue.ttc", "outfit@Bold",
                       "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"],
             "tracking": 0.02},
}
FONT_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "fonts"))
_catalog = None
try:
    from PIL import features as _pil_features
    LAYOUT_FEATURES = ["lnum"] if _pil_features.check_feature("raqm") else None
except Exception:  # pragma: no cover - very old Pillow
    LAYOUT_FEATURES = None
FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.expanduser(r"~\AppData\Local\Microsoft\Windows\Fonts"),
    "/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"), "/Library/Fonts", "/System/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
]
REC709 = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)  # linear luminance
REC601 = np.array([0.299, 0.587, 0.114], dtype=np.float32)  # gamma-encoded luma
_font_index = None


class Canvas:
    """Output geometry. Lengths are designed at 1280x720 (wide) or 1080 px wide (tall)
    and scaled by `u`."""

    def __init__(self, w, h):
        self.w, self.h = int(w), int(h)
        self.tall = self.h > self.w
        self.reels = self.tall and self.h / self.w > 1.6
        self.u = self.w / (1080.0 if self.tall else 1280.0)

    def px(self, v):
        return int(round(v * self.u))

    @property
    def margin(self):
        return self.px(80)

    @property
    def col_w(self):
        if not self.tall:
            return int(self.w * COL_SHARE)
        right = self.w * REELS_SAFE["right"] if self.reels else self.margin
        return int(self.w - self.margin - right)

    @property
    def timestamp_zone(self):  # the duration badge covers this corner (16:9 only)
        return (int(self.w * 0.80), int(self.h * 0.82), self.w, self.h) if not self.tall else (0, 0, 0, 0)

    @property
    def safe_box(self):
        """Where type may go: Reels/Shorts UI covers the rest; elsewhere the margins."""
        if self.reels:
            return (self.margin, int(self.h * REELS_SAFE["top"]), int(self.w * (1 - REELS_SAFE["right"])),
                    int(self.h * (1 - REELS_SAFE["bottom"])))
        return (self.margin, self.margin, self.w - self.margin, self.h - self.margin)

    @property
    def frame_box(self):
        """What the edge check holds type to: the safe zone on Reels, a thin inset elsewhere."""
        if self.reels:
            return self.safe_box
        e = self.px(16)
        return (e, e, self.w - e, self.h - e)


# ---------------------------------------------------------------- fonts

def font_index():
    global _font_index
    if _font_index is None:
        _font_index = {}
        for d in FONT_DIRS:
            for base, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith((".ttf", ".otf", ".ttc")):
                        _font_index.setdefault(f.lower(), os.path.join(base, f))
    return _font_index


def font_catalog():
    """The bundled display fonts (fonts/catalog.json): short name -> file, instance, tracking,
    caps (reads well in all caps), look, cues, fits."""
    global _catalog
    if _catalog is None:
        try:
            with open(os.path.join(FONT_ROOT, "catalog.json"), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        _catalog = {}
        for name, e in (data.items() if isinstance(data, dict) else []):  # skip broken entries and lost files
            if (isinstance(e, dict) and all(k in e for k in ("file", "tracking", "caps", "look", "cues", "fits"))
                    and os.path.isfile(os.path.join(FONT_ROOT, e["file"]))):
                _catalog[name.strip().lower()] = e
    return _catalog


def bundled_entry(name):
    """Catalog entry for 'cinzel' or 'cinzel@Black', or None."""
    if not name or os.sep in name or "/" in name:
        return None
    return font_catalog().get(name.partition("@")[0].strip().lower())


def bundled_spec(name):
    """'cinzel' or 'cinzel@Black' -> 'path@Instance' for a bundled font, else None."""
    entry = bundled_entry(name)
    if not entry:
        return None
    path = os.path.normpath(os.path.join(FONT_ROOT, entry["file"]))
    if not os.path.isfile(path):
        return None
    inst = name.partition("@")[2] or entry.get("instance")
    return path + ("@" + inst if inst else "")


def canonical_weight(spec, entry, warnings):
    """Match an @Weight case-insensitively to the font's named instances. An unknown weight falls
    back to the catalog's weight for bundled fonts; a single-weight font ignores the request."""
    if not spec or "@" not in spec:
        return spec
    path, _, inst = spec.partition("@")
    inst, base = inst.strip(), os.path.basename(path)
    try:
        font = ImageFont.truetype(path, 20)
    except Exception:
        font = None
    try:  # raises for single-weight (static) fonts
        names = [n.decode() if isinstance(n, bytes) else n for n in font.get_variation_names()]
    except Exception:
        names = []
    if not inst:
        return path
    if names:
        for n in names:
            if n.lower() == inst.lower():
                return path + "@" + n
        fallback = entry.get("instance") if entry else None
        warnings.append("weight %r is not in %s (has: %s); using %s." % (
            inst, base, ", ".join(names), fallback or "its default weight"))
        return path + ("@" + fallback if fallback else "")
    style = font.getname()[1] if font else ""
    if inst.lower() != style.lower():
        warnings.append("%s has a single weight (%s); %r is ignored." % (base, style or "regular", inst))
    return path


def missing_glyphs(font, text):
    """Characters of `text` the font has no glyph for (drawn the same as an unassigned code point)."""
    size = getattr(font, "size", 40)

    def ink(ch):
        im = Image.new("L", (size * 3, size * 3), 0)
        ImageDraw.Draw(im).text((size, size), ch, font=font, fill=255)
        return im.tobytes()

    try:
        blank = ink("\U000FFFFD")
    except Exception:
        return []
    return [ch for ch in sorted(set(text)) if not ch.isspace() and ch != "·"
            and not unicodedata.category(ch).startswith("M")  # combining marks are drawn with their letter
            and ink(ch) == blank]


def resolve_font(candidates):
    """Return the first available 'path@Instance' spec, or None for Pillow's default."""
    for spec in candidates:
        bundled = bundled_spec(spec)
        if bundled:
            return bundled
        name, _, inst = spec.partition("@")
        path = name if os.path.isfile(name) else font_index().get(os.path.basename(name).lower())
        if path:
            return path + ("@" + inst if inst else "")
    return None


def load_font(spec, size):
    size = max(6, int(size))
    if spec is None:
        try:
            return ImageFont.load_default(size)
        except TypeError:  # Pillow < 10.1
            return ImageFont.load_default()
    path, _, inst = spec.partition("@")
    try:
        font = ImageFont.truetype(path, size)
    except OSError as e:
        raise ValueError("cannot load font %s: %s" % (path, e))
    if inst:
        try:
            font.set_variation_by_name(inst)
        except Exception:
            pass
    return font


# ---------------------------------------------------------------- color and tone

def srgb_to_linear(s):
    s = np.clip(s, 0.0, 1.0)
    return np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055).astype(np.float32)


def estimate_cast(lin, iterations=2):
    """Log-RGB offset of the scene's near-neutral surfaces, zero mean across channels.

    Copied from snapshot-realism (scripts/realism.py). Gray-world restricted to the
    least saturated 40% of mid and bright, unclipped pixels, weighted toward
    brighter surfaces; dark pixels are left out. Red above zero and blue below zero
    means a warm cast."""
    y = lin @ REC709
    ok = (y > 0.02) & (lin.max(axis=-1) < 0.97)
    if ok.sum() < 64:
        return np.zeros(3, dtype=np.float32)
    logc = np.log(lin[ok] + 1e-4)
    chroma = logc - logc.mean(axis=1, keepdims=True)
    w_all = np.sqrt(y[ok])
    offset = np.zeros(3, dtype=np.float32)
    for _ in range(iterations):
        c = chroma - offset
        sat = np.linalg.norm(c, axis=1)
        sel = sat <= np.percentile(sat, 40)
        w = w_all[sel]
        offset = offset + (c[sel] * w[:, None]).sum(axis=0) / w.sum()
    norm = float(np.linalg.norm(offset))
    if norm > 0.35:  # beyond this the "neutral" set is probably a colored scene
        offset *= 0.35 / norm
    return offset.astype(np.float32)


def dark_tone_pixels(pixels):
    """Median of the darkest 15% of an (N, 3) pixel array, pulled to near-black, partly desaturated."""
    a = np.asarray(pixels, dtype=np.float32).reshape(-1, 3)
    y = a @ REC601
    c = np.median(a[y <= np.percentile(y, 15)], axis=0)
    c = 0.6 * c + 0.4 * c.mean()
    peak = max(float(c.max()), 1.0)
    return tuple(int(round(v)) for v in c * min(1.0, 22.0 / peak))


def dark_tone(img):
    """Background color from the image's own shadows."""
    return dark_tone_pixels(np.asarray(img.convert("RGB").resize((128, 128))))


def to_rgb(img, fill=None):
    """RGB copy of any input image: EXIF rotation applied, 16-bit and float modes rescaled
    (Pillow would clip them), and transparency filled with a dark tone from the opaque part."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        a = np.asarray(img, dtype=np.float64)
        top = float(a.max()) if a.size else 1.0
        if img.mode.startswith("I;16") or (img.mode == "I" and top > 255):
            hi = 65535.0
        elif img.mode == "F":
            hi = 1.0 if top <= 1.0 else top
        else:
            hi = 255.0
        img = Image.fromarray(np.clip(a / hi * 255.0 + 0.5, 0, 255).astype(np.uint8), "L")
    if img.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        alpha = np.asarray(rgba.getchannel("A"))
        if alpha.min() < 250:
            if fill is None:
                opaque = np.asarray(rgba.convert("RGB"))[alpha >= 250]
                fill = dark_tone_pixels(opaque) if len(opaque) >= 16 else (16, 16, 16)
            base = Image.new("RGBA", rgba.size, tuple(int(v) for v in fill) + (255,))
            base.alpha_composite(rgba)
            return base.convert("RGB")
        return rgba.convert("RGB")
    return img.convert("RGB")


# Content shapes that letterbox and pillarbox bars leave behind (width / height).
COMMON_RATIOS = (2.76, 2.40, 2.39, 2.35, 2.33, 2.2, 2.0, 1.9, 1.85, 16 / 9, 1.66, 1.6, 1.5, 1.43, 4 / 3,
                 1.25, 1.0, 0.8, 0.75, 9 / 16, 0.5, 9 / 19.5, 9 / 20)


def trim_bars(img):
    """Crop letterbox or pillarbox bars (see trim_box)."""
    box = trim_box(img)
    return img if box == (0, 0, img.width, img.height) else img.crop(box)


def trim_box(img, pristine_only=False):
    """The part of img left after letterbox or pillarbox bars, as (left, top, right, bottom).
    pristine_only (inside a --crop) accepts only clean, even bars: a dark stage there is content.

    A bar is a run of lines from an edge that are dark-dominated (median at most 28, 60% of
    pixels near it) and mostly flat at one constant level, so video black (16), dark-theme gray
    and bars carrying subtitles or a watermark all count, while a night sky (gradients, stars)
    does not. Bars are trimmed only in pairs on opposite edges at a similar level, and only when
    the bars are pristine or what remains has a standard shape (2.39, 16:9, 4:3, 1:1, 9:16...)."""
    a = np.asarray(img.convert("L"), dtype=np.float32)
    h, w = a.shape

    def stats(lines):
        med = np.median(lines, axis=1)
        return med, (np.abs(lines - med[:, None]) <= 10).mean(axis=1), lines.std(axis=1)

    def edge_run(med, dom, std, reverse):
        n = 0
        for i in (range(len(med) - 1, -1, -1) if reverse else range(len(med))):
            if med[i] <= 28 and dom[i] >= 0.6:
                n += 1
            else:
                break
        if n < 2:
            return None
        sel = slice(len(med) - n, len(med)) if reverse else slice(0, n)
        flat = std[sel] <= 3
        if flat.mean() < 0.6:
            return None
        levels = med[sel][flat]
        spread = float(np.percentile(levels, 90) - np.percentile(levels, 10))
        if spread > 4:
            return None
        return n, float(np.median(levels)), float(flat.mean()), spread

    def pristine(r1, r2):
        return (min(r1[2], r2[2]) >= 0.95 and max(r1[3], r2[3]) <= 2
                and abs(r1[0] - r2[0]) <= max(4, 0.15 * max(r1[0], r2[0])))

    def common(cw, ch):
        return ch > 0 and any(abs(cw / ch / r - 1) <= 0.025 for r in COMMON_RATIOS)

    rows, cols = stats(a), stats(a.T)
    rt, rb = edge_run(*rows, False), edge_run(*rows, True)
    cl, cr = edge_run(*cols, False), edge_run(*cols, True)
    top = bottom = left = right = 0
    if rt and rb and abs(rt[1] - rb[1]) <= 6:
        top, bottom = min(rt[0], int(h * 0.4)), min(rb[0], int(h * 0.4))
    if cl and cr and abs(cl[1] - cr[1]) <= 6:
        left, right = min(cl[0], int(w * 0.4)), min(cr[0], int(w * 0.4))
    cw, ch = w - left - right, h - top - bottom
    if top and not (pristine(rt, rb) or (not pristine_only and (common(cw, ch) or common(w, ch)))):
        top = bottom = 0
    if left and not (pristine(cl, cr) or (not pristine_only and (common(cw, h - top - bottom) or common(cw, h)))):
        left = right = 0
    if not (top or bottom or left or right) or top + bottom >= h - 8 or left + right >= w - 8:
        return (0, 0, w, h)
    return (left, top, w - right, h - bottom)


def prepare_background(src, crop=None, inset_box=None):
    """Trim bars, then apply --crop. crop and inset_box are fractions of the image as given, so
    bars are trimmed whether or not the crop includes them. Returns (image, inset_box remapped
    to fractions of the returned image, crop box in pixels or None)."""
    w, h = src.size
    box = trim_box(src)
    if crop is not None:
        c = (crop[0] * w, crop[1] * h, crop[2] * w, crop[3] * h)
        box = (max(box[0], c[0]), max(box[1], c[1]), min(box[2], c[2]), min(box[3], c[3]))
        box = tuple(int(round(v)) for v in box)
        if box[2] - box[0] < 16 or box[3] - box[1] < 16:
            raise ValueError("--crop leaves almost nothing of the image (after its bars are trimmed): widen it")
        inner = trim_box(src.crop(box), pristine_only=True)  # clean bars of a video inside the crop, e.g. a player
        box = (box[0] + inner[0], box[1] + inner[1], box[0] + inner[2], box[1] + inner[3])
    if inset_box is not None and box != (0, 0, w, h):
        bw, bh = box[2] - box[0], box[3] - box[1]
        inset_box = ((inset_box[0] * w - box[0]) / bw, (inset_box[1] * h - box[1]) / bh,
                     (inset_box[2] * w - box[0]) / bw, (inset_box[3] * h - box[1]) / bh)
    out = src if box == (0, 0, w, h) else src.crop(box)
    return out, inset_box, (box if crop is not None else None)


def grade(img, contrast=0.08, saturation=0.9, vignette=0.22):
    """Gentle film-style grade: a little contrast, a little less saturation, soft vignette."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    a = np.clip(0.5 + (a - 0.5) * (1 + contrast), 0, 1)
    y = (a @ REC601)[..., None]
    a = np.clip(y + (a - y) * saturation, 0, 1)
    if vignette:
        h, w = a.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r2 = ((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2
        a *= (1 - vignette * np.clip(r2 / 2, 0, 1))[..., None]
    return Image.fromarray((a * 255 + 0.5).astype(np.uint8), "RGB")


def busyness(img):
    """Edge energy of the left and right halves (lower = calmer, better for type)."""
    g = np.asarray(img.convert("L").resize((256, 144)), dtype=np.float32)
    e = np.abs(np.diff(g, axis=0))[:, :-1] + np.abs(np.diff(g, axis=1))[:-1, :]
    half = e.shape[1] // 2
    return float(e[:, :half].mean()), float(e[:, half:].mean())


def rgb(value):
    return tuple(value) if isinstance(value, (tuple, list)) else ImageColor.getrgb(value)[:3]


def smoothstep(t):
    t = np.clip(t, 0, 1)
    return t * t * (3 - 2 * t)


def depth(img, cv, amount):
    """Push a background behind a subject: soft blur and a little darker."""
    if amount <= 0:
        return img
    img = img.filter(ImageFilter.GaussianBlur(8 * amount * cv.u))
    a = np.asarray(img, dtype=np.float32) * (1 - 0.25 * amount)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def wcag_luminance(srgb01):
    return srgb_to_linear(srgb01) @ REC709


WHITE = (255, 255, 255)


def hexcolor(c):
    return "#%02x%02x%02x" % tuple(int(v) for v in c[:3])


def signature_color(img, min_share=0.04):
    """The image's most vivid color family (a sunset, neon, an eclipse), or None if it has none.

    Only strongly saturated, reasonably bright pixels count (HSV s >= 0.55, v >= 0.45), so skin,
    wood, fog and gray scenes don't qualify. Hues are binned around the color wheel; the heaviest
    family (with its neighbours) must cover at least `min_share` of the frame. Returns the median of
    its brighter, more saturated half."""
    small = img.convert("RGB").resize((96, 96))
    hsv = np.asarray(small.convert("HSV"), dtype=np.float32) / 255.0
    px = np.asarray(small, dtype=np.float32).reshape(-1, 3)
    h, s, v = (hsv[..., i].ravel() for i in range(3))
    vivid = (s >= 0.55) & (v >= 0.45)
    if vivid.mean() < min_share:
        return None
    bins = (h[vivid] * 24).astype(int) % 24
    weights = (s * v)[vivid]
    hist = np.bincount(bins, weights=weights, minlength=24)
    b = int(np.argmax(hist + np.roll(hist, 1) + np.roll(hist, -1)))  # hue is circular
    sel = np.isin(bins, [(b - 1) % 24, b, (b + 1) % 24])
    if sel.sum() < min_share * h.size:
        return None
    family, w = px[vivid][sel], weights[sel]
    top = np.argsort(w)[len(w) // 2:]
    return tuple(int(round(x)) for x in np.median(family[top], axis=0))


def lift_to_contrast(color, bg, target=MIN_CONTRAST * 1.15):
    """Mix a color toward white, keeping its hue, until it reaches `target` WCAG contrast on bg."""
    lb = float(wcag_luminance(np.array(bg[:3], dtype=np.float32) / 255.0))
    c = np.array(color[:3], dtype=np.float32)
    for t in np.linspace(0, 1, 41):
        m = c + (255 - c) * t
        lf = float(wcag_luminance(m / 255.0))
        if (max(lf, lb) + 0.05) / (min(lf, lb) + 0.05) >= target:
            return tuple(int(round(x)) for x in m)
    return WHITE


# ---------------------------------------------------------------- layer matching

def _luma(a):
    return a[..., :3] @ REC601


def grain_level(luma, mask=None):
    """Noise sigma from the high-pass residual (robust MAD), in sRGB units."""
    blur = np.asarray(Image.fromarray((luma * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.0)),
                      dtype=np.float32) / 255.0
    r = np.abs(luma - blur)
    if mask is not None:
        r = r[mask]
    return float(np.median(r) * 1.4826) if r.size else 0.0


def detail_level(luma, mask=None):
    """Fine-detail energy: mean absolute difference from a 1.5 px blur."""
    blur = np.asarray(Image.fromarray((luma * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)),
                      dtype=np.float32) / 255.0
    r = np.abs(luma - blur)
    if mask is not None:
        r = r[mask]
    return float(r.mean()) if r.size else 0.0


def match_layer(layer, ref, cv, cast_strength=0.6, tone_strength=0.35, seed=0, texture_ref=None):
    """Make an RGBA layer read as part of the RGB photo behind it.

    Shifts the layer's color cast toward the reference's, moves its black and white
    points partly toward the reference's, softens it if it is much sharper, and adds
    grain if the reference is noisier. Sharpness and grain are compared with
    `texture_ref` when given (the background before it was softened behind a subject),
    so a deliberate background blur doesn't blur the subject too. Edges get a 1 px
    feather. Returns (layer, report)."""
    a = np.asarray(layer.convert("RGBA"), dtype=np.float32) / 255.0
    alpha = a[..., 3]
    # measure inside the cut-out only: erode so the edge against transparency doesn't count as detail
    mask = np.asarray(Image.fromarray(((alpha > 0.5) * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(5))) > 0
    report = {}
    if mask.sum() < 64:
        return layer, report
    r = np.asarray(ref.convert("RGB"), dtype=np.float32) / 255.0
    lin, rlin = srgb_to_linear(a[..., :3]), srgb_to_linear(r)
    shift = cast_strength * (estimate_cast(rlin) - estimate_cast(lin[mask][None, ...]))
    lin = lin * np.exp(shift).astype(np.float32)
    report["cast_shift"] = round(float(np.linalg.norm(shift)), 3)

    y = lin @ REC709
    ry = rlin @ REC709
    lo, hi = np.percentile(y[mask], 5), np.percentile(y[mask], 95)
    rlo, rhi = np.percentile(ry, 5), np.percentile(ry, 95)
    nlo, nhi = lo + tone_strength * (rlo - lo), hi + tone_strength * (rhi - hi)
    if hi - lo > 1e-4:
        # Apply the black/white-point curve evenly to the RGB channels. Scaling
        # by new/old luminance amplifies channel noise in nearly black clothing.
        gain = (nhi - nlo) / (hi - lo)
        lin = lin * gain + (nlo - lo * gain)
    s = linear_to_srgb(lin)

    tex = np.asarray((texture_ref if texture_ref is not None else ref).convert("RGB"), dtype=np.float32) / 255.0
    ly, ry_s = _luma(s), _luma(tex)
    ratio = detail_level(ly, mask) / max(detail_level(ry_s), 1e-5)
    report["sharpness_ratio"] = round(ratio, 2)
    if ratio > 1.4:  # cut-out much crisper than the photo behind it
        for sigma in (0.6, 0.9, 1.3, 1.8):
            soft = np.asarray(Image.fromarray((s * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(sigma * cv.u)),
                              dtype=np.float32) / 255.0
            if detail_level(_luma(soft), mask) / max(detail_level(ry_s), 1e-5) <= 1.2:
                break
        s = soft
        report["softened_px"] = round(sigma * cv.u, 2)

    need = grain_level(ry_s) ** 2 - grain_level(_luma(s), mask) ** 2
    if need > 1e-6:
        n = np.random.default_rng(seed).normal(0, np.sqrt(need), s.shape[:2]).astype(np.float32)
        s = np.clip(s + n[..., None], 0, 1)
        report["grain_added"] = round(float(np.sqrt(need)), 4)

    edge = Image.fromarray((alpha * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(max(0.6, cv.u)))
    out = Image.fromarray((s * 255 + 0.5).astype(np.uint8), "RGB")
    out.putalpha(edge)
    return out, report


def crop_to_alpha(img):
    """Crop to the visible part of an RGBA image; None when nothing is visible."""
    img = img.convert("RGBA")
    box = img.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return img.crop(box) if box else None


def cut_out(img):
    """Use the image's own transparency; otherwise remove the background with rembg if installed."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        if np.asarray(rgba.getchannel("A")).min() < 250:
            return rgba
    try:
        from rembg import remove
    except ImportError:
        raise ValueError("subject images need a transparent background (PNG cut-out), "
                         "or install rembg for automatic removal: python -m pip install rembg")
    return remove(to_rgb(img)).convert("RGBA")


# ---------------------------------------------------------------- composition

def is_narrow(src, w, h, cropped=False):
    """Art narrower than a wide canvas goes on the far side at full height instead of being
    cover-cropped. A capture must be clearly narrower (under 90% of the canvas shape); a --crop
    is taken as asked, so anything visibly narrower than the canvas counts."""
    return h <= w and src.width / src.height < (w / h) * (0.99 if cropped else 0.9)


def required_scale(src, w, h, layout="single", n_panels=1, cropped=False):
    if layout == "panels":
        if h > w:  # stacked vertically on tall canvases
            return max(w / src.width, (h / n_panels) / src.height)
        return max((w / n_panels) / src.width, h / src.height)
    if h > w:
        return max(w / src.width, h / src.height)  # tall canvases always cover
    if is_narrow(src, w, h, cropped):
        return h / src.height
    return max(w / src.width, h / src.height)


def pick_size(sources, layout, requested="auto", aspect="16:9", cropped=False):
    """Largest standard size every background supports without upscaling (5% slack).
    --size names (720p...4k) apply to 16:9; other aspects always pick automatically."""
    if requested and requested != "auto" and aspect == "16:9":
        return SIZES[requested]
    sizes = ASPECTS[aspect]["sizes"]
    for w, h in sizes[:-1]:
        if all(required_scale(s, w, h, layout, len(sources), cropped) <= 1.05 for s in sources):
            return w, h
    return sizes[-1]


def subject_focus(src, crop_share):
    """Horizontal crop position (0..1) that keeps the busiest window: where the subject is."""
    g = np.asarray(src.convert("L").resize((256, 64)), dtype=np.float32)
    e = (np.abs(np.diff(g, axis=1))[:-1, :] + np.abs(np.diff(g, axis=0))[:, :-1]).sum(axis=0)
    win = max(1, int(round(crop_share * len(e))))
    if win >= len(e):
        return 0.5
    sums = np.convolve(e, np.ones(win), mode="valid")
    sums *= 1 - 0.15 * np.abs(np.linspace(-1, 1, len(sums)))  # slight pull to the center
    return float(np.argmax(sums) / (len(sums) - 1)) if len(sums) > 1 else 0.5


def focus_on(box, src, w, h):
    """Horizontal crop position that centers a box (fractions of src) in a w x h cover crop."""
    share = (w / max(w / src.width, h / src.height)) / src.width
    if share >= 1:
        return 0.5
    c = (box[0] + box[2]) / 2
    return float(min(max((c - share / 2) / (1 - share), 0.0), 1.0))


def cover(src, w, h, focus=0.5):
    """Scale and crop src to exactly w x h, resizing only the visible window (fast and
    memory-safe on huge sources). Returns (image, scale, sx, sy): canvas = (src - (sx, sy)) * scale."""
    box, scale = cover_box(src.width, src.height, w, h, focus)
    return src.resize((w, h), Image.LANCZOS, box=box), scale, box[0], box[1]


def cover_box(sw, sh, w, h, focus=0.5):
    """Source window (x0, y0, x1, y1) and scale for a w x h cover crop, always inside the source
    (float error in w/scale can overshoot the source by 1e-13, which Pillow rejects)."""
    scale = max(w / sw, h / sh)
    cw, ch = min(sw, w / scale), min(sh, h / scale)
    sx = min(max(0.0, (sw - cw) * min(max(focus, 0.0), 1.0)), sw - cw)
    sy = max(0.0, (sh - ch) / 2)
    return (sx, sy, sx + cw, sy + ch), scale


def side_focus(src, share, text_side):
    """Crop position (0..1) for a 16:9 crop of a wider source that keeps the busiest region on
    the side away from the type."""
    g = np.asarray(src.convert("L").resize((256, 64)), dtype=np.float32)
    e = (np.abs(np.diff(g, axis=1))[:-1, :] + np.abs(np.diff(g, axis=0))[:, :-1]).sum(axis=0)
    win = int(round(share * len(e)))
    if win >= len(e) or win < 4:
        return 0.5
    lo, hi = (int(win * 0.45), win) if text_side == "left" else (0, int(win * 0.55))
    c = np.concatenate([[0.0], np.cumsum(e)])
    starts = np.arange(len(e) - win + 1)
    scores = c[starts + hi] - c[starts + lo]
    return float(np.argmax(scores) / max(1, len(starts) - 1))


def column_shade(canvas, cv, bg, shade, text_side):
    """Shade the type column with the background color: solid across it, fading out by ~65% of the width."""
    plateau, falloff = cv.margin + cv.col_w * 0.75, cv.w * 0.28
    alpha = shade * (1 - smoothstep((np.arange(cv.w, dtype=np.float32) - plateau) / falloff))
    if text_side == "right":
        alpha = alpha[::-1]
    canvas.paste(Image.new("RGB", canvas.size, bg), (0, 0),
                 Image.fromarray((np.tile(alpha, (cv.h, 1)) * 255).astype(np.uint8), "L"))
    return canvas


def compose_tall(src, cv, bg, focus=None, shade=0.88, push=0.0, band=(0, 0), anchor="bottom"):
    """Cover a tall canvas with the image and shade a band for the type.

    The crop keeps the busiest region (the subject) unless focus is given. The shade is
    solid across `band` (the type block, padded) and fades out toward the image.
    Returns (canvas, scale, mapper from source fractions to canvas px)."""
    if focus is None:
        focus = subject_focus(src, (cv.w / max(cv.w / src.width, cv.h / src.height)) / src.width)
    img, scale, sx, sy = cover(src, cv.w, cv.h, focus)
    canvas = depth(grade(img), cv, push)
    canvas = shade_band(canvas, cv, bg, shade, band, anchor)
    mapper = lambda fx, fy, s=scale, sx=sx, sy=sy: ((fx * src.width - sx) * s, (fy * src.height - sy) * s)  # noqa: E731
    return canvas, scale, mapper


def shade_band(canvas, cv, bg, shade, band, anchor):
    """Shade from the type band to the canvas edge on the anchor side, fading toward the image.
    On 9:16 the type sits mid-frame (above the caption area), so the shade is held around the
    band and eases to a light 0.35 below it instead of darkening the whole lower frame."""
    y = np.arange(cv.h, dtype=np.float32)
    fade = cv.h * 0.22
    if anchor == "bottom" and cv.reels:
        f = cv.h * 0.12
        up = smoothstep((y - (band[0] - f)) / f)
        down = 1 - smoothstep((y - band[1]) / f)
        alpha = np.maximum(shade * up * down, min(shade, 0.35) * smoothstep((y - band[0]) / f))
    elif anchor == "bottom":
        alpha = shade * smoothstep((y - (band[0] - fade)) / fade)
    else:
        alpha = shade * (1 - smoothstep((y - band[1]) / fade))
    canvas.paste(Image.new("RGB", canvas.size, bg), (0, 0),
                 Image.fromarray((np.tile(alpha[:, None], (1, cv.w)) * 255).astype(np.uint8), "L"))
    return canvas


def compose_single(src, cv, text_side, bg, focus=0.5, feather=0.36, shade=0.88, push=0.0, cropped=False):
    """One background with a dark side for type.

    Narrower than 16:9 (see is_narrow): full height on the side away from the text, inner edge
    feathered into the background color. Otherwise cover-crop (focus moves the
    crop). Both then shade the type column, solid across it and fading out by about
    65% of the width. Returns (canvas, scale, mapper from source fractions to canvas px)."""
    if is_narrow(src, cv.w, cv.h, cropped):
        canvas = Image.new("RGB", (cv.w, cv.h), bg)
        w = int(round(src.width / src.height * cv.h))
        scale = cv.h / src.height
        img = depth(grade(src.resize((w, cv.h), Image.LANCZOS)), cv, push)
        ramp = smoothstep(np.arange(w, dtype=np.float32) / max(1.0, feather * w))
        if text_side == "right":
            ramp = ramp[::-1]
        x0 = cv.w - w if text_side == "left" else 0
        canvas.paste(img, (x0, 0), Image.fromarray((np.tile(ramp, (cv.h, 1)) * 255).astype(np.uint8), "L"))
        mapper = lambda fx, fy: (x0 + fx * w, fy * cv.h)  # noqa: E731
    else:
        img, scale, sx, sy = cover(src, cv.w, cv.h, focus)
        canvas = depth(grade(img), cv, push)
        mapper = lambda fx, fy, s=scale, sx=sx, sy=sy: ((fx * src.width - sx) * s, (fy * src.height - sy) * s)  # noqa: E731
    return column_shade(canvas, cv, bg, shade, text_side), scale, mapper


def panel_rects(cv, n):
    """Side by side on wide canvases, stacked on tall ones."""
    div = max(2, cv.px(4))
    if cv.tall:
        ph = (cv.h - (n - 1) * div) / n
        return [(0, int(round(i * (ph + div))), cv.w, int(round(i * (ph + div) + ph))) for i in range(n)], div
    pw = (cv.w - (n - 1) * div) / n
    return [(int(round(i * (pw + div))), 0, int(round(i * (pw + div) + pw)), cv.h) for i in range(n)], div


def compose_panels(srcs, cv, bg, shade=0.88, top_shade=False, band_top=None, band=None):
    """Two or three captures with a thin light divider. Wide canvases: side by side, with a
    shaded band at the bottom, solid from band_top down. Tall canvases: stacked, with a shade
    only around the type `band` on a seam, so every panel stays visible.
    Returns (canvas, per-panel scale factors)."""
    canvas = Image.new("RGB", (cv.w, cv.h), (232, 232, 232))  # gaps become the divider
    rects, _ = panel_rects(cv, len(srcs))
    scales = []
    for src, (x0, y0, x1, y1) in zip(srcs, rects):
        canvas.paste(grade(ImageOps.fit(src, (x1 - x0, y1 - y0), Image.LANCZOS), vignette=0), (x0, y0))
        scales.append(max((x1 - x0) / src.width, (y1 - y0) / src.height))  # ImageOps.fit always covers
    y = np.arange(cv.h, dtype=np.float32)
    if cv.tall and band is not None:
        fade = cv.h * 0.10
        alpha = shade * smoothstep((y - (band[0] - fade)) / fade) * (1 - smoothstep((y - band[1]) / fade))
    else:
        band_top = cv.h * 0.6 if band_top is None else band_top
        fade = cv.px(140)
        alpha = shade * smoothstep((y - (band_top - fade)) / fade)
    if top_shade:
        alpha = np.maximum(alpha, 0.65 * (1 - smoothstep(y / (cv.h * 0.24))))
    canvas.paste(Image.new("RGB", canvas.size, bg), (0, 0),
                 Image.fromarray((np.tile(alpha[:, None], (1, cv.w)) * 255).astype(np.uint8), "L"))
    return canvas, scales


def place_subjects(canvas, subjects, cv, text_side, warnings, band=None, texture_ref=None):
    """Cut-outs away from the type (beside it on wide canvases, below it on tall ones),
    bottom-anchored, never into the type column, matched to what is behind them.
    Returns (canvas, match reports, mask of where the subjects landed)."""
    n = len(subjects)
    if cv.tall:
        x0, x1 = 0, cv.w
        target_h = int(cv.h * (0.66 if n == 1 else 0.58))
        if band is not None:  # stay below the type band
            room = cv.h - band[1] - cv.px(16)
            if room < cv.h * 0.3:
                warnings.append("the type leaves little room for the subject below it: shorten the text.")
            target_h = max(int(cv.h * 0.2), min(target_h, room))
    else:
        if text_side == "left":
            x0, x1 = cv.margin + cv.col_w + cv.px(20), cv.w
        else:
            x0, x1 = 0, cv.w - cv.margin - cv.col_w - cv.px(20)
        target_h = int(cv.h * (0.94 if n == 1 else 0.84))
    slot = (x1 - x0) / n
    reports = []
    mask = Image.new("L", canvas.size, 0)
    for i, subj in enumerate(subjects):
        s = crop_to_alpha(subj)
        k = min(target_h / s.height, min(slot * 1.35, x1 - x0) / s.width)
        if k > 1.5:
            warnings.append("subject %d is %dx%d and gets upscaled %.1fx: use a larger cut-out."
                            % (i + 1, s.width, s.height, k))
        s = s.resize((max(1, int(s.width * k)), max(1, int(s.height * k))), Image.LANCZOS)
        px = int(round(x0 + slot * (i + 0.5) - s.width / 2))
        if not cv.tall:  # never into the type column
            px = max(px, x0) if text_side == "left" else min(px, x1 - s.width)
        py = cv.h - s.height
        vis = (max(0, px), max(0, py), min(cv.w, px + s.width), cv.h)
        if (vis[2] - vis[0]) < 0.85 * s.width:
            warnings.append("subject %d is %d%% off the frame edge: use fewer subjects or a narrower cut-out."
                            % (i + 1, round(100 * (1 - (vis[2] - vis[0]) / s.width))))
        behind = canvas.crop(vis).resize(s.size)
        tex = texture_ref.crop(vis).resize(s.size) if texture_ref is not None else None
        s, rep = match_layer(s, behind, cv, seed=i, texture_ref=tex)
        reports.append(rep)
        rgba = canvas.convert("RGBA")
        rgba.alpha_composite(s, (max(0, px), py), (max(0, -px), 0))
        canvas = rgba.convert("RGB")
        footprint = Image.new("L", canvas.size, 0)
        footprint.paste(s.getchannel("A"), (px, py))
        mask = ImageChops.lighter(mask, footprint)
    return canvas, reports, mask


def place_inset(canvas, inset, box, mapper, cv, warnings):
    """Fit an image into a region of the background (box in fractions of the background image).
    Only the part inside both the frame and the background image is drawn and matched (narrow or
    cropped art leaves the dark type side outside the image). Returns (canvas, match report,
    visible rect or None)."""
    fx0, fy0, fx1, fy1 = box
    x0, y0 = mapper(fx0, fy0)
    x1, y1 = mapper(fx1, fy1)
    x0, y0, x1, y1 = (int(round(v)) for v in (x0, y0, x1, y1))
    if x1 - x0 < 8 or y1 - y0 < 8:
        warnings.append("inset box is under 8 px at this size; make --inset-box larger.")
        return canvas, {}, None
    ix0, iy0 = mapper(0.0, 0.0)  # where the background image itself sits
    ix1, iy1 = mapper(1.0, 1.0)
    vis = (max(0, x0, int(round(ix0))), max(0, y0, int(round(iy0))),
           min(cv.w, x1, int(round(ix1))), min(cv.h, y1, int(round(iy1))))
    if vis[2] - vis[0] < 8 or vis[3] - vis[1] < 8:
        warnings.append("inset box falls outside the visible frame: check --inset-box, --focus and --crop.")
        return canvas, {}, None
    shown = (vis[2] - vis[0]) * (vis[3] - vis[1]) / ((x1 - x0) * (y1 - y0))
    if shown < 0.95:
        if x1 - x0 > cv.w or y1 - y0 > cv.h:
            warnings.append("the inset box is larger than this format's crop (only %d%% fits): use a smaller "
                            "--inset-box for this aspect." % int(shown * 100))
        else:
            warnings.append("only %d%% of the inset box is inside the frame: check --inset-box, --focus and --crop."
                            % int(shown * 100))
    img = trim_bars(to_rgb(inset))
    k = max((x1 - x0) / img.width, (y1 - y0) / img.height)
    if k > 1.5:
        warnings.append("inset is %dx%d and gets upscaled %.1fx: use a larger capture." % (img.width, img.height, k))
    full = ImageOps.fit(img, (x1 - x0, y1 - y0), Image.LANCZOS)
    layer = full.crop((vis[0] - x0, vis[1] - y0, vis[2] - x0, vis[3] - y0)).convert("RGBA")
    layer, rep = match_layer(layer, canvas.crop(vis), cv, cast_strength=0.3, tone_strength=0.2)
    rgba = canvas.convert("RGBA")
    rgba.alpha_composite(layer, (vis[0], vis[1]))
    return rgba.convert("RGB"), rep, vis


# ---------------------------------------------------------------- type

def _fkw(font):
    """OpenType features for layout: lining figures, so serif and blackletter numbers sit on the
    baseline at full height. Needs Pillow's raqm layout; without it, layout is unchanged."""
    raqm = getattr(ImageFont, "Layout", None) and getattr(font, "layout_engine", None) == ImageFont.Layout.RAQM
    return {"features": LAYOUT_FEATURES} if LAYOUT_FEATURES and raqm else {}


def _advances(font, text, tracking):
    """Per-character advance for tracked text, keeping each pair's kerning (AV, TO, YO)."""
    kw = _fkw(font)
    w = [font.getlength(c, **kw) for c in text]
    out = []
    for i in range(len(text)):
        a = w[i]
        if i + 1 < len(text):
            a += tracking + font.getlength(text[i:i + 2], **kw) - w[i] - w[i + 1]
        out.append(a)
    return out


def text_width(font, text, tracking):
    if not text:
        return 0.0
    if tracking:
        return sum(_advances(font, text, tracking))
    return font.getlength(text, **_fkw(font))


def draw_text(draw, x, baseline, text, font, tracking, fill):
    if not tracking:
        draw.text((x, baseline), text, font=font, fill=fill, anchor="ls", **_fkw(font))
        return
    for c, a in zip(text, _advances(font, text, tracking)):
        draw.text((x, baseline), c, font=font, fill=fill, anchor="ls", **_fkw(font))
        x += a


def cap_metrics(font, text):
    """(height above baseline, depth below baseline) of the drawn text."""
    _, top, _, bottom = font.getbbox(text, anchor="ls", **_fkw(font))
    return -top, max(0, bottom)


def split_lines(text):
    text = text.replace("\\n", "\n")
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def fit_title(spec, lines, tracking_em, max_w, cv, max_size=190):
    size, floor, step = cv.px(max_size), cv.px(min(48, max_size)), max(2, cv.px(4))
    while size > floor:
        font = load_font(spec, size)
        if max(text_width(font, ln, tracking_em * size) for ln in lines) <= max_w:
            break
        size -= step
    return load_font(spec, size), size


def fit_small(spec, text, size, floor, max_w, tracking_ratio, pad=0):
    """Font and tracking for a small line (label, kicker, subtitle) that fits max_w with padding."""
    s = size
    while True:
        font = load_font(spec, s)
        trk = int(round(s * tracking_ratio))
        if text_width(font, text, trk) + pad <= max_w or s <= floor:
            return font, trk
        s -= max(1, size // 30)


def fit_logo(logo, max_w, max_h):
    k = min(max_w / logo.width, max_h / logo.height)
    return logo.resize((max(1, int(logo.width * k)), max(1, int(logo.height * k))), Image.LANCZOS)


def text_block(cv, big, box, kicker, subtitle, tspec, lspec, tracking_em, accent,
               logo=None, logo_big=True, max_w=None, stack=False, max_size=190, big_name="title",
               big_color=(255, 255, 255, 255)):
    """Type stack: kicker, (small logo), big lines or big logo, subtitle, boxed label.
    stack=True fits each big line on its own (capped at 1.6x the smallest), so a short
    first line leads a longer second one. Small lines shrink to fit max_w.
    Returns (items, block width, block height, big size)."""
    max_w = max_w or cv.col_w
    lines = split_lines(big) if big else []
    tsize = cv.px(140)
    fonts = []
    if lines:
        font, tsize = fit_title(tspec, lines, tracking_em, max_w, cv, max_size)
        if len(lines) == 1 and " " in lines[0]:  # wrap at the middle space when it helps
            words = lines[0].split()
            mid = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
            wrapped = [" ".join(words[:mid]), " ".join(words[mid:])]
            wfont, wsize = fit_title(tspec, wrapped, tracking_em, max_w, cv, max_size)
            # too small on one line, or a clear gain (narrow fonts) without orphaning a short word
            if tsize < cv.px(100) or (wsize >= tsize * 1.3 and min(len(w) for w in wrapped) >= 3):
                lines, font, tsize = wrapped, wfont, wsize
        fonts = [(font, tsize)] * len(lines)
        if stack and len(lines) > 1:
            fitted = [fit_title(tspec, [ln], tracking_em, max_w, cv, max_size) for ln in lines]
            cap = int(min(s for _, s in fitted) * 1.6)
            fonts = [(f, s) if s <= cap else (load_font(tspec, cap), cap) for f, s in fitted]
            tsize = max(s for _, s in fonts)
    if logo is not None:
        logo = fit_logo(logo, max_w, cv.px(max_size) if logo_big else cv.px(min(70, max_size * 0.4)))
        if logo_big:
            tsize = int(logo.height * 0.9)
    # (kind, text, font, tracking, height_above, depth, gap_before, color, name)
    items = []
    if kicker:
        f, trk = fit_small(lspec, kicker.upper(), cv.px(26), cv.px(16), max_w, 6 / 26)
        a, d = cap_metrics(f, kicker.upper())
        items.append(("text", kicker.upper(), f, trk, a, d, 0, (255, 255, 255, 185), "kicker"))
    if logo is not None and not logo_big:
        items.append(("image", logo, None, 0, logo.height, 0, cv.px(18) if items else 0, None, "logo"))
    for i, (ln, (f, s)) in enumerate(zip(lines, fonts)):
        a, d = cap_metrics(f, ln)
        gap = 0 if not items else (int(s * 0.28) if i == 0 else int(s * 0.16))
        items.append(("text", ln, f, tracking_em * s, a, d, gap, big_color, big_name))
    if logo is not None and logo_big:
        items.append(("image", logo, None, 0, logo.height, 0, int(tsize * 0.22) if items else 0, None, "logo"))
    if subtitle:
        f, trk = fit_small(lspec, subtitle.upper(), cv.px(28), cv.px(16), max_w, 5 / 28)
        a, d = cap_metrics(f, subtitle.upper())
        items.append(("text", subtitle.upper(), f, trk, a, d, int(tsize * 0.18), (255, 255, 255, 215), "subtitle"))
    if box:
        f, trk = fit_small(lspec, box.upper(), cv.px(30), cv.px(18), max_w, 5 / 30, pad=2 * cv.px(22))
        a, _ = cap_metrics(f, box.upper())
        items.append(("box", box.upper(), f, trk, int(a + 2 * cv.px(15)), 0, int(tsize * 0.32), accent,
                      "label box"))
    width, height = 0.0, 0
    for kind, text, f, trk, a, d, gap, _, _ in items:
        w = text.width if kind == "image" else text_width(f, text, trk) + (2 * cv.px(22) if kind == "box" else 0)
        width = max(width, w)
        height += gap + a + d
    return items, int(width), int(height), tsize


def draw_box(draw, cv, x0, y, text, font, trk, color, h=None):
    w = text_width(font, text, trk)
    cap, _ = cap_metrics(font, text)
    h = h or int(cap + 2 * cv.px(15))
    bw = int(w + 2 * cv.px(22))
    draw.rectangle((x0, y, x0 + bw, y + h), outline=color, width=max(2, cv.px(3)))
    draw_text(draw, x0 + cv.px(22), y + (h + cap) / 2, text, font, trk, color)
    return (x0, y, x0 + bw, y + h)


def draw_block(layer, items, block_w, block_h, cv, align, top):
    """Draw the stack; align is left (at the margin), right, or center.
    Returns (block bbox, [(element name, element bbox)])."""
    draw = ImageDraw.Draw(layer)
    edge = {"left": cv.margin, "right": cv.w - cv.margin, "center": cv.w // 2}[align]

    def x_for(w):
        return edge if align == "left" else edge - w if align == "right" else edge - w / 2

    elements = []
    y = top
    for kind, text, font, trk, a, d, gap, color, name in items:
        y += gap
        if kind == "image":
            x = int(x_for(text.width))
            layer.alpha_composite(text, (max(0, x), int(max(0, y))), (max(0, -x), int(max(0, -y))))
            elements.append((name, (x, int(y), x + text.width, int(y + a))))
            y += a
        elif kind == "box":
            bw = text_width(font, text, trk) + 2 * cv.px(22)
            elements.append((name, draw_box(draw, cv, int(x_for(bw)), int(y), text, font, trk, color, a)))
            y += a
        else:
            w = text_width(font, text, trk)
            x = x_for(w)
            draw_text(draw, x, y + a, text, font, trk, color)
            elements.append((name, (int(x), int(y), int(x + w), int(y + a + d))))
            y += a + d
    x0 = x_for(block_w)
    return (int(x0), int(top), int(x0 + block_w), int(top + block_h)), elements


def place_panel_labels(layer, canvas, cv, rects, labels, lspec, band, bg, warnings):
    """Boxed labels inside their own panels, clear of the type band and of each other, inside the
    Reels safe zone where the panel allows; a soft scrim behind each keeps them readable on bright
    panels. Returns (canvas, [(element name, bbox)], names of labels already warned about)."""
    draw = ImageDraw.Draw(layer)
    safe = cv.safe_box
    placed, elements, flagged = [], [], set()
    for i, ((x0, y0, x1, y1), name) in enumerate(zip(rects, labels or [])):
        if not name:
            continue
        text = name.upper()
        avail_w = cv.col_w if cv.tall else (x1 - x0) - 2 * cv.px(36)
        font, trk = fit_small(lspec, text, cv.px(26), cv.px(16), avail_w, 5 / 26, pad=2 * cv.px(22))
        cap, _ = cap_metrics(font, text)
        h = int(cap + 2 * cv.px(15))
        bw = int(text_width(font, text, trk) + 2 * cv.px(22))
        lx = cv.margin if cv.tall else x0 + cv.px(36)
        label_name = "panel label %d" % (i + 1)
        if cv.tall:
            lo, hi = y0 + cv.px(8), y1 - h - cv.px(8)
            slo, shi = (max(lo, safe[1]), min(hi, safe[3] - h)) if cv.reels else (lo, hi)
            raw = [y0 + cv.px(32), band[0] - h - cv.px(12), band[1] + cv.px(12)]
            # each preferred spot (nudged into the safe zone if needed) in order, then anywhere in the panel
            cands = [c if slo <= c <= shi else min(max(c, slo), shi) for c in raw if slo <= shi]
            cands += [min(max(c, lo), hi) for c in raw if lo <= hi]
        else:
            cands = [y0 + cv.px(32)]
        chosen = None
        for c in cands:
            r = (lx, c, lx + bw, c + h)
            if cv.tall and band is not None and overlaps(r, (0, band[0], cv.w, band[1])):
                continue
            if any(overlaps(r, p) for p in placed):
                continue
            chosen = r
            break
        if chosen is None:
            c = max(y0 + cv.px(8), min(y0 + cv.px(32), y1 - h - cv.px(8)))
            chosen = (lx, c, lx + bw, c + h)
            flagged.add(label_name)
            warnings.append("panel %d's label has no clear spot in its panel: shorten the type or the label."
                            % (i + 1))
        elif cv.reels and not (safe[1] <= chosen[1] and chosen[3] <= safe[3]):
            flagged.add(label_name)
            warnings.append("panel %d sits under the Reels/Shorts caption area, so its label can't be in the "
                            "safe zone: use two panels for 9:16." % (i + 1))
        if not cv.tall and chosen[2] > x1 - cv.px(8):
            flagged.add(label_name)
            warnings.append("panel %d's label is wider than its panel: shorten it." % (i + 1))
        placed.append(chosen)
        pad = cv.px(6)
        scrim = Image.new("L", canvas.size, 0)
        ImageDraw.Draw(scrim).rectangle((chosen[0] - pad, chosen[1] - pad, chosen[2] + pad, chosen[3] + pad),
                                        fill=140)
        canvas.paste(Image.new("RGB", canvas.size, bg), (0, 0), scrim.filter(ImageFilter.GaussianBlur(pad)))
        draw_box(draw, cv, chosen[0], chosen[1], text, font, trk, (255, 255, 255, 255), h)
        elements.append((label_name, chosen))
    return canvas, elements, flagged


def glyph_contrast(canvas, layer):
    """85th-percentile luma of the background where glyphs land (the worst areas)."""
    glyphs = np.asarray(layer.getchannel("A")) > 128
    luma = np.asarray(canvas.convert("L"), dtype=np.float32) / 255.0
    return float(np.percentile(luma[glyphs], 85)) if glyphs.any() else 0.0


def element_contrast(canvas, layer, elements):
    """WCAG contrast of each drawn element against what is behind it.

    For every element, the glyph pixels are the solid ones (alpha at least 90% of the
    element's own maximum, so a translucent kicker counts at its real strength); each is
    composited over the background and compared with it. Type scores at its 15th percentile
    (its worst parts); a logo at its 75th, so an outline or drop shadow drawn into the logo
    doesn't count against it. Returns [(name, ratio, light-on-dark?, best possible ratio for
    that color on any background)]."""
    L = np.asarray(layer, dtype=np.float32) / 255.0
    C = np.asarray(canvas.convert("RGB"), dtype=np.float32) / 255.0
    out = []
    for name, (x0, y0, x1, y1) in elements:
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(canvas.width, int(x1)), min(canvas.height, int(y1))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        la, ca = L[y0:y1, x0:x1], C[y0:y1, x0:x1]
        alpha = la[..., 3]
        if alpha.max() < 0.05:
            continue
        m = alpha >= 0.9 * alpha.max()
        if m.sum() < 12:
            continue
        a = alpha[..., None]
        fg = ca * (1 - a) + la[..., :3] * a
        lf, lb = wcag_luminance(fg)[m], wcag_luminance(ca)[m]
        ratio = (np.maximum(lf, lb) + 0.05) / (np.minimum(lf, lb) + 0.05)
        light = float(np.median(lf - lb)) >= 0
        own = float(np.median(wcag_luminance(la[..., :3])[m]))
        best = (own + 0.05) / 0.05 if light else 1.05 / (own + 0.05)
        out.append((name, float(np.percentile(ratio, 75 if name == "logo" else 15)), light, best))
    return out


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def inside(a, b, slack=1):
    return a[0] >= b[0] - slack and a[1] >= b[1] - slack and a[2] <= b[2] + slack and a[3] <= b[3] + slack


# ---------------------------------------------------------------- render

def render(src=None, title="", label="", kicker="", subtitle="", lead="title", style="serif",
           text_side="auto", focus=None, accent="#ffffff", bg=None, feather=0.36, shade=0.88,
           title_font=None, label_font=None, logo=None, logo_white=False, size="auto",
           panels=None, panel_labels=None, subjects=None, push=None, inset=None, inset_box=None,
           aspect="16:9", title_color="white", crop=None, title_tracking=None):
    """Return (thumbnail at master size, info dict with warnings and measurements).

    lead="title": the title is the big type and the label goes in the box.
    lead="label": the label is the big type (split at " · ") and the title goes in the box.
    aspect "16:9" puts the type beside the image; "4:5" and "9:16" put it in a band below the
    image (above it when there is a subject), inside the Reels/Shorts safe zone for 9:16.
    crop (x0, y0, x1, y1 fractions of the capture) keeps only that part of it; a crop narrower
    than 16:9 puts the art on the far side, fading into the dark side, which makes room for the
    type when the subject sits in the middle of the frame."""
    info = {"warnings": [], "notes": [], "lead": lead, "aspect": aspect}
    warnings = info["warnings"]
    # composed accents (e + U+0301 -> é) so tracked type and glyph checks see one letter
    title, label, kicker, subtitle = (unicodedata.normalize("NFC", t or "") for t in (title, label, kicker, subtitle))
    panel_labels = [unicodedata.normalize("NFC", p or "") for p in panel_labels] if panel_labels else panel_labels
    if aspect not in ASPECTS:
        raise ValueError("aspect must be one of %s" % ", ".join(ASPECTS))
    if style not in STYLES:
        raise ValueError("style must be one of %s" % ", ".join(STYLES))
    if panels and src is not None:
        raise ValueError("use either one background capture or --panel images, not both")
    if not panels and src is None:
        raise ValueError("no image: pass a capture or two or more --panel images")
    if panels and len(panels) < 2:
        raise ValueError("panels need at least two images")
    if panels and len(panels) > 3:
        raise ValueError("panels take two or three images")
    if panels and (inset is not None or subjects):
        raise ValueError("--inset and --subject need one background capture; they can't be used with --panel")
    if panel_labels and len(panel_labels) > len(panels or []):
        raise ValueError("%d panel labels for %d panels" % (len(panel_labels), len(panels or [])))
    if crop is not None and panels:
        raise ValueError("--crop applies to the background capture; crop panel images before passing them")
    if crop is not None and (len(crop) != 4 or not all(0 <= v <= 1 for v in crop)
                             or crop[0] >= crop[2] or crop[1] >= crop[3]):
        raise ValueError("--crop takes x0,y0,x1,y1 as fractions of the capture, 0 to 1")
    panels = [trim_bars(to_rgb(p)) for p in (panels or [])]
    subjects = [cut_out(s) for s in (subjects or [])]
    for i, s in enumerate(subjects):
        if crop_to_alpha(s) is None:
            raise ValueError("subject %d is fully transparent" % (i + 1))
    layout = "panels" if panels else "single"
    info["layout"] = layout
    crop_px = None
    if src is not None:
        src = to_rgb(src)
        given = src.size
        src, inset_box, crop_px = prepare_background(src, crop, inset_box)
        if crop_px is not None:
            info["notes"].append("cropped the capture to x %d-%d, y %d-%d of %dx%d"
                                 % (crop_px[0], crop_px[2], crop_px[1], crop_px[3], given[0], given[1]))
    backgrounds = panels or [src]
    info["source"] = backgrounds[0].size
    cropped = layout == "single" and crop_px is not None
    cv = Canvas(*pick_size(backgrounds, layout, size, aspect, cropped))
    info["size"] = (cv.w, cv.h)
    narrow = layout == "single" and is_narrow(src, cv.w, cv.h, cropped)

    anchor = None
    if cv.tall:
        anchor = "top" if subjects and layout == "single" else "bottom"
        text_side = "left"
        info["text_side"] = anchor
    else:
        if layout == "single" and text_side == "auto":
            if inset is not None:  # type on the side with more room beside the inset
                ib = inset_box or (0.25, 0.25, 0.75, 0.75)
                text_side = "left" if ib[0] >= 1 - ib[2] else "right"
            else:
                # wide frames: type over the calmer half; narrow art: its calmer half fades toward the type
                left, right = busyness(src)
                text_side = "left" if left <= right else "right"
        info["text_side"] = text_side if layout == "single" else "bottom"
    bg = rgb(bg) if bg else dark_tone(backgrounds[0])
    push = (0.5 if subjects else 0.0) if push is None else push
    focus_eff = focus
    if inset is not None and focus is None and layout == "single" and not narrow:
        focus_eff = focus_on(inset_box or (0.25, 0.25, 0.75, 0.75), src, cv.w, cv.h)
    elif (focus is None and layout == "single" and not cv.tall and not narrow
          and src.width / src.height > (cv.w / cv.h) * 1.1):
        # wider than 16:9: keep the busiest region (the subject) on the side away from the type
        share = (cv.w / max(cv.w / src.width, cv.h / src.height)) / src.width
        focus_eff = side_focus(src, share, text_side)
        info["notes"].append("crop follows the subject (focus %.2f); set --focus to override" % focus_eff)

    entry = bundled_entry(title_font) if title_font else None
    tracking_em = entry["tracking"] if entry else STYLES[style]["tracking"]
    if title_tracking is not None:  # letter spacing of the big type, in em: wide for a spaced-out logo
        if isinstance(title_tracking, bool) or not isinstance(title_tracking, (int, float)) or not 0 <= title_tracking <= 0.5:
            raise ValueError("--title-tracking takes a number from 0 to 0.5 (em), e.g. 0.12")
        tracking_em = float(title_tracking)
    caps_ok = entry["caps"] if entry else True
    if title_font:
        tspec = resolve_font([title_font])
        if tspec is None:
            raise ValueError("title font not found: %s (bundled fonts: %s)"
                             % (title_font, ", ".join(sorted(font_catalog()))))
    else:
        tspec = resolve_font(STYLES[style]["title"])
        if tspec is None:
            warnings.append("none of the %s title fonts are installed; using Pillow's default font." % style)
    if label_font:
        lspec = resolve_font([label_font])
        if lspec is None:
            raise ValueError("label font not found: %s" % label_font)
    else:
        lspec = resolve_font(LABEL_FONTS)
        if lspec is None:
            warnings.append("none of the label fonts are installed; using Pillow's default font.")
    tspec = canonical_weight(tspec, entry, warnings)
    lspec = canonical_weight(lspec, bundled_entry(label_font) if label_font else None, warnings)
    info["fonts"] = (tspec, lspec)
    def pick_color(value, what):
        """white | auto | any color -> (rgb, description). auto lifts the image's signature color."""
        key = str(value).strip().lower() if value is not None else "white"
        if key in ("white", "#fff", "#ffffff"):
            return WHITE, "white"
        if key == "auto":
            sig = signature_color(backgrounds[0])
            if sig is None:
                info["notes"].append("no strong signature color in the image, so %s stays white" % what)
                return WHITE, "white (auto: no signature color in the image)"
            c = lift_to_contrast(sig, bg)
            if c == sig:
                return c, "%s (auto: the image's own color)" % hexcolor(c)
            return c, "%s (auto: the image's %s, lightened for contrast)" % (hexcolor(c), hexcolor(sig))
        c = rgb(value)
        return c, hexcolor(c)

    if logo is not None:
        logo = ImageOps.exif_transpose(logo).convert("RGBA")
        if np.asarray(logo.getchannel("A")).min() >= 250:
            raise ValueError("--logo needs a transparent background (a PNG with alpha); this one is opaque")
        logo = crop_to_alpha(logo)
        if logo is None:
            raise ValueError("--logo is fully transparent")
        if logo_white:
            white = Image.new("RGB", logo.size, (255, 255, 255))
            logo = Image.merge("RGBA", white.split() + (logo.getchannel("A"),))
    if lead == "label" and label:
        hook = label.upper() if caps_ok else label  # blackletter and script fonts break in all caps
        big, box, logo_big = hook.replace(" · ", "\n").replace("·", "\n"), title, False
        if logo is not None:
            box = ""  # the logo stands for the title
    else:
        big, box, logo_big = ("" if logo is not None else title), label, True
    big_name = "hook" if lead == "label" and label else "title"
    if logo is not None and not big:  # the logo is the big element and keeps its own colors (or turns white)
        big_rgb, big_desc = WHITE, ("logo, white (--logo-white)" if logo_white else "logo, its own colors")
    else:
        big_rgb, big_desc = pick_color(title_color, "the big type")
    if box:
        accent_rgb, accent_desc = pick_color(accent, "the label box")
    else:  # no label, or variant B with a logo standing for the title
        accent_rgb, accent_desc = WHITE, "none (no label box)"
    info["colors"] = {"big type": big_desc, "label box": accent_desc}
    accent_rgba = accent_rgb + (255,)
    if logo is not None:
        info["title_source"] = "official logo"
    if big_rgb != WHITE and accent_rgb != WHITE and big_rgb != accent_rgb:  # a logo or a missing box is white here
        warnings.append("two accent colors (%s big type, %s label box): color the big type or the label box, "
                        "not both; the quiet look uses one." % (hexcolor(big_rgb), hexcolor(accent_rgb)))
    if entry:
        font_source = "bundled font %s (%s, %s)" % (title_font.partition("@")[0].strip().lower(), entry.get(
            "family", "?"), entry["look"])
    elif title_font:
        font_source = "font file %s" % os.path.basename(tspec.partition("@")[0])
    else:
        font_source = "default %s font (%s)" % (style, os.path.basename(tspec.partition("@")[0]) if tspec
                                                else "Pillow default")
    if title_tracking is not None:
        font_source += ", tracking %.2f em" % tracking_em
    if logo is None:
        info["title_source"] = font_source
    elif big:  # variant B: logo above, hook set in the title font
        info["title_source"] = "official logo; hook in %s" % font_source
    if big:
        shown = " ".join(split_lines(big))
        missing = missing_glyphs(load_font(tspec, 64), shown)
        if missing:
            warnings.append("the title font has no glyph for %s: pick another font (--list-fonts) or drop the "
                            "character." % ", ".join("U+%04X" % ord(c) for c in missing))
        if not caps_ok and len(shown) > 3 and shown.upper() == shown and shown.lower() != shown:
            warnings.append("%s reads poorly in all caps: write the big type in Title Case."
                            % title_font.partition("@")[0].strip().lower())

    # where the type may go, and how much height it gets
    safe = cv.safe_box
    rects = div = None
    if cv.tall and layout == "panels":
        rects, div = panel_rects(cv, len(panels))
        ph = rects[0][3] - rects[0][1]
        max_w, base = cv.col_w, 150
        avail = min(safe[3] - safe[1], int(0.8 * ph) - 2 * cv.px(40))
    elif cv.tall:
        max_w, base = cv.col_w, 190
        avail = safe[3] - safe[1] if anchor == "bottom" else int(min(safe[3] - safe[1], cv.h * 0.42))
    elif layout == "panels":
        max_w, base = int(cv.w * 0.56), 110
        avail = min(cv.h - cv.px(64) - cv.px(150), int(cv.h * 0.36))  # the panels stay the picture
    else:
        max_w, base = cv.col_w, 190
        bottom_limit = cv.timestamp_zone[1] - cv.px(12) if text_side == "right" else safe[3]
        avail = bottom_limit - safe[1]
        if inset is not None and not narrow:  # keep the type in the room beside the inset
            ib = inset_box or (0.25, 0.25, 0.75, 0.75)
            sc = max(cv.w / src.width, cv.h / src.height)
            sx = (src.width - cv.w / sc) * (0.5 if focus_eff is None else focus_eff)
            ix0, ix1 = (ib[0] * src.width - sx) * sc, (ib[2] * src.width - sx) * sc
            free = (ix0 - cv.margin if text_side == "left" else cv.w - cv.margin - ix1) - cv.px(40)
            if cv.px(200) <= free < max_w:
                max_w = int(free)
    cap = base
    while True:
        items, bw, bh, big_size = text_block(cv, big, box, kicker, subtitle, tspec, lspec,
                                             tracking_em, accent_rgba, logo, logo_big, max_w,
                                             stack=(lead == "label" and "·" in label), max_size=cap,
                                             big_name=big_name, big_color=big_rgb + (255,))
        if bh <= avail or cap <= 48:
            break
        cap = max(48, int(cap * 0.88))
    info["big_size"] = big_size
    if cap < base:
        info["notes"].append("type scaled down to fit the height")
    if big and big_size < cv.px(72):
        big_lines = [it[1] for it in items if it[0] == "text" and it[8] == big_name]
        if len(big_lines) == 1 and " " in big_lines[0]:
            fix = "split it over two lines with \\n (e.g. DAWN\\nWALKER) or pick a narrower font (bebas, oswald)"
        else:
            fix = "this font is wide for this text: shorten it or pick a narrower font (bebas, oswald)"
        warnings.append("the big type is only %d px tall at this width: %s." % (big_size, fix))
    if logo is not None:
        logo_item = [it for it in items if it[0] == "image"][0][1]
        k = logo_item.width / logo.width
        if k > 1.5:
            warnings.append("logo is %dx%d and gets upscaled %.1fx: use a larger logo." % (logo.width, logo.height, k))
        if logo_big and logo_item.height < cv.px(60):
            warnings.append("the logo is too wide to read as the title (%d px tall): use a stacked logo or text."
                            % logo_item.height)

    if cv.tall and layout == "panels":
        best = None
        for seam in [rects[i][3] + div / 2 for i in range(len(rects) - 1)]:
            t = int(seam - bh / 2)
            tc = int(min(max(t, safe[1]), safe[3] - bh))
            if best is None or abs(tc - t) < best[0]:
                best = (abs(tc - t), tc)
        top = best[1]
        band = (top - cv.px(40), top + bh + cv.px(40))
        for i, (_, ry0, _, ry1) in enumerate(rects):
            covered = max(0, min(ry1, band[1]) - max(ry0, band[0])) / (ry1 - ry0)
            if covered > 0.5:
                warnings.append("the type band covers %d%% of panel %d: shorten the text." % (covered * 100, i + 1))
    elif cv.tall:
        top = safe[1] + cv.px(16) if anchor == "top" else safe[3] - bh
        band = (top - cv.px(40), top + bh + cv.px(40))
    elif layout == "panels":
        top = cv.h - cv.px(64) - bh
        band = (top - cv.px(24), cv.h)
        shaded = (cv.h - (band[0] - cv.px(70))) / cv.h  # where the shade is about half strength
        if shaded > 0.6:
            warnings.append("the type band shades %d%% of the panels: shorten the text." % (shaded * 100))
    else:
        top = int(cv.h * 0.5 - bh / 2)
        top = max(safe[1], min(top, bottom_limit - bh))
        band = None

    def build(shade_now):
        w = []  # warnings from this build only, so a rebuild never duplicates them
        out = {"warnings": w, "scales": None, "subjects": [], "inset": None, "inset_rect": None, "mask": None}
        mapper = None
        tex = None
        if layout == "panels":
            canvas, scales = compose_panels(panels, cv, bg, shade_now, top_shade=not cv.tall and bool(panel_labels),
                                            band_top=band[0], band=band)
            out["scales"], scale = scales, max(scales)
        elif cv.tall:
            canvas, scale, mapper = compose_tall(src, cv, bg, focus_eff, shade_now, push, band, anchor)
            if subjects and push > 0:
                tex = compose_tall(src, cv, bg, focus_eff, shade_now, 0.0, band, anchor)[0]
        else:
            fx = 0.5 if focus_eff is None else focus_eff
            canvas, scale, mapper = compose_single(src, cv, text_side, bg, fx, feather, shade_now, push, cropped)
            if subjects and push > 0:
                tex = compose_single(src, cv, text_side, bg, fx, feather, shade_now, 0.0, cropped)[0]
        if inset is not None:
            canvas, out["inset"], out["inset_rect"] = place_inset(canvas, inset, inset_box or (0.25, 0.25, 0.75, 0.75),
                                                                  mapper, cv, w)
        if subjects:
            canvas, out["subjects"], out["mask"] = place_subjects(canvas, subjects, cv, text_side, w,
                                                                  band if cv.tall else None, tex)
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        align = "center" if layout == "panels" and not cv.tall else ("left" if cv.tall else text_side)
        bbox, elements = draw_block(layer, items, bw, bh, cv, align, top)
        label_elements, flagged = [], set()
        if layout == "panels":
            canvas, label_elements, flagged = place_panel_labels(layer, canvas, cv, panel_rects(cv, len(panels))[0],
                                                                 panel_labels, lspec, band, bg, w)
        out.update(canvas=canvas, scale=scale, layer=layer, bbox=bbox, elements=elements,
                   label_elements=label_elements, flagged=flagged,
                   contrast=element_contrast(canvas, layer, elements + label_elements))
        return out

    def worst(b, names=None):
        return min((r for n, r, _, _ in b["contrast"] if names is None or n in names), default=99.0)

    def fixable(c):  # light type that fails only because of what is behind it
        _, r, light, best = c
        return light and r < MIN_CONTRAST and best >= MIN_CONTRAST * 1.05

    built = build(shade)
    targets = {c[0] for c in built["contrast"] if fixable(c)}
    if shade < 0.97 and targets:
        darker = build(0.97)  # light type on a bright patch: darken the type area once more
        # judged only on the elements darkening is meant to fix; a dark --accent gets its own warning
        if worst(darker, targets) > worst(built, targets) + 0.05:
            built = darker
            info["notes"].append("raised --shade to 0.97 for contrast")
    warnings.extend(built["warnings"])
    canvas, layer, bbox = built["canvas"], built["layer"], built["bbox"]
    info["subjects"] = built["subjects"]
    if built["inset"] is not None:
        info["inset"] = built["inset"]
    if built["inset_rect"] is not None:
        info["inset_rect"] = built["inset_rect"]

    scale = built["scale"]
    info["scale"] = round(scale, 2)
    if layout == "panels":
        i = int(np.argmax(built["scales"]))
        if built["scales"][i] > 1.5:
            warnings.append("panel %d is %dx%d and gets upscaled %.1fx at %dx%d: use larger captures."
                            % (i + 1, panels[i].width, panels[i].height, built["scales"][i], cv.w, cv.h))
    elif scale > 1.5:
        warnings.append(
            "source is %dx%d, upscaled %.1fx even at %dx%d: art will look soft. Recapture at 1440p or "
            "higher, or upscale with Real-ESRGAN first." % (src.width, src.height, scale, cv.w, cv.h))

    info["behind_type"] = round(glyph_contrast(canvas, layer), 2)
    info["contrast"] = round(worst(built), 2)
    by_name = {}  # one warning per element: a two-line title is one title
    for c in built["contrast"]:
        if c[1] < MIN_CONTRAST and (c[0] not in by_name or c[1] < by_name[c[0]][1]):
            by_name[c[0]] = c
    failing = sorted(by_name.values(), key=lambda c: c[1])
    if failing:
        info["contrast_element"] = failing[0][0]
    for name, ratio, light, best in failing:
        ratio = int(ratio * 10) / 10.0  # never print 4.49 as 4.5
        if light and best < MIN_CONTRAST * 1.05:
            fix = "use --logo-white or a lighter logo" if name == "logo" else "use a lighter --accent"
            warnings.append("the %s has %.1f:1 contrast (want %.1f:1 or more): its color is too dim to read "
                            "even on black; %s." % (name, ratio, MIN_CONTRAST, fix))
        elif light:
            if cv.tall:
                hint = "use --focus to move the crop, or a darker image"
            elif narrow:
                hint = "use --text-side %s or a darker --bg" % ("right" if text_side == "left" else "left")
            elif layout == "single":
                move = ("--focus to move the subject away from the type" if src.width / src.height > cv.w / cv.h * 1.1
                        else "--crop to narrow the art so it moves away from the type")
                hint = "try --text-side %s, %s, or a darker capture" % ("right" if text_side == "left" else "left", move)
            else:
                hint = "use darker captures"
            warnings.append("the %s has %.1f:1 contrast (want %.1f:1 or more): the background under it is "
                            "bright; %s." % (name, ratio, MIN_CONTRAST, hint))
        else:
            fix = "use --logo-white or a light version of the logo" if name == "logo" else "use a light --accent"
            warnings.append("the %s has %.1f:1 contrast (want %.1f:1 or more): its color is too dark for the "
                            "background; %s." % (name, ratio, MIN_CONTRAST, fix))
    info["text_box"] = bbox

    frame = cv.frame_box
    where = ("the Reels/Shorts safe zone (clear of the top 14%, bottom 35%, right 15%)" if cv.reels
             else "the edge of the frame")
    for name, b in built["elements"] + built["label_elements"]:
        if name not in built["flagged"] and not inside(b, frame):
            warnings.append("the %s runs outside %s: shorten it." % (name, where))
    if "inset_rect" in info and overlaps(bbox, info["inset_rect"]):
        warnings.append("type overlaps the inset image: use --text-side, or a background with room beside the "
                        "screen or frame.")
    if built["mask"] is not None:
        glyphs = np.asarray(layer.getchannel("A")) > 128
        hit = (glyphs & (np.asarray(built["mask"]) > 128)).sum()
        if glyphs.any() and hit > 0.02 * glyphs.sum():
            warnings.append("the type overlaps the subject: shorten the text%s." %
                            ("" if cv.tall else " or use --text-side"))
    if overlaps(bbox, cv.timestamp_zone):
        warnings.append("type reaches the bottom-right corner, where YouTube's duration badge sits.")
    title_words = [] if logo is not None else split_lines(title)
    words = " ".join([kicker] + title_words + [subtitle, label] + list(panel_labels or []))
    n_words = len(words.replace("·", " ").split())
    if n_words > 8:
        warnings.append("%d words of type; keep it to 8 or fewer so it reads at sidebar size." % n_words)
    info["warnings"] = list(dict.fromkeys(warnings))  # drop exact repeats, keep order
    out = canvas.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert("RGB"), info


# ---------------------------------------------------------------- previews and export

def corner_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def badge(tile, text="24:13"):
    """YouTube's duration badge: fixed ~12 px type whatever the thumbnail size."""
    d = ImageDraw.Draw(tile)
    f = load_font(resolve_font(LABEL_FONTS), 12)
    tw = f.getlength(text)
    x1, y1 = tile.width - 4, tile.height - 4
    d.rounded_rectangle((x1 - tw - 8, y1 - 17, x1, y1), radius=4, fill=(0, 0, 0))
    d.text((x1 - tw - 4, y1 - 4), text, font=f, fill=(255, 255, 255), anchor="ls")
    return tile


def squint(img):
    """What survives a fast scroll: no color, no fine detail. The title and the
    subject should still be the two things you see."""
    return img.convert("L").filter(ImageFilter.GaussianBlur(max(1.0, img.width / 80))).convert("RGB")


def preview_sheet(thumb):
    # (width, label, transform); notifications show no duration badge
    sizes = [(480, "home feed", "badge"), (320, "search", "badge"), (168, "up next", "badge"),
             (120, "smallest", "badge"), (88, "notification", None), (320, "squint test", "squint")]
    gap, pad = 28, 28
    width = pad * 2 + sum(w for w, _, _ in sizes) + gap * (len(sizes) - 1)
    row_h = int(480 * 9 / 16) + 40
    sheet = Image.new("RGB", (width, row_h * 2 + pad), (255, 255, 255))
    f = load_font(resolve_font(LABEL_FONTS), 13)
    for r, (bg, fg) in enumerate((((15, 15, 15), (241, 241, 241)), ((255, 255, 255), (15, 15, 15)))):
        y0 = r * row_h + (pad // 2 if r else 0)
        ImageDraw.Draw(sheet).rectangle((0, y0 - (pad // 2 if r else 0), width, y0 + row_h), fill=bg)
        x = pad
        for w, name, kind in sizes:
            h = int(round(w * 9 / 16))
            tile = thumb.resize((w, h), Image.LANCZOS)
            if kind == "badge":
                tile = badge(tile)
            elif kind == "squint":
                tile = squint(tile)
            sheet.paste(tile, (x, y0 + pad // 2), corner_mask(tile.size, max(4, int(w * 0.03))))
            ImageDraw.Draw(sheet).text((x, y0 + pad // 2 + h + 18), "%d px  %s" % (w, name), font=f, fill=fg,
                                       anchor="ls")
            x += w + gap
    return sheet


OUTPUT_SUFFIXES = ("_preview.png", "_feed.png", "_phone.jpg", "_1080.jpg")


def feed_grid(thumbs, folder, cols=4, tw=320, exclude=()):
    """Your variants among other thumbnails (screenshots in a folder). Files in `exclude` and this
    tool's own outputs (previews, feed sheets, phone and 1080 copies) are skipped. The number of
    other thumbnails loaded is in grid.info["competitors"]."""
    if isinstance(thumbs, Image.Image):
        thumbs = [thumbs]
    th = int(tw * 9 / 16)
    skip = {os.path.normcase(os.path.abspath(p)) for p in exclude}
    paths = []
    if os.path.isdir(folder):
        names = sorted(os.listdir(folder))
        lower = {n.lower() for n in names}
        for name in names:
            p = os.path.join(folder, name)
            low = name.lower()
            # a suffixed file is one of this tool's outputs only if its master sits next to it
            ours = any(low.endswith(s) and low[:-len(s)] + ".jpg" in lower for s in OUTPUT_SUFFIXES)
            if (low.endswith(IMAGE_EXTS) and not ours
                    and os.path.normcase(os.path.abspath(p)) not in skip and os.path.isfile(p)):
                paths.append(p)
    tiles = []
    for p in paths:
        if len(tiles) >= 12 - len(thumbs):
            break
        try:
            im = Image.open(p)
            if im.format == "JPEG":
                im.draft("RGB", (tw * 2, th * 2))
            tiles.append(ImageOps.fit(to_rgb(im), (tw, th), Image.LANCZOS))
        except (OSError, ValueError, Image.DecompressionBombError):
            continue
    loaded = len(tiles)
    # screenshots of other thumbnails usually carry their own duration badge; only yours get one added
    for i, t in enumerate(thumbs):
        tiles.insert(min(5 + i, len(tiles)), badge(t.resize((tw, th), Image.LANCZOS)))
    rows = -(-len(tiles) // cols)
    gap = 16
    grid = Image.new("RGB", (cols * tw + (cols + 1) * gap, rows * th + (rows + 1) * gap), (15, 15, 15))
    for i, t in enumerate(tiles):
        grid.paste(t, (gap + (i % cols) * (tw + gap), gap + (i // cols) * (th + gap)), corner_mask(t.size, 10))
    grid.info["competitors"] = loaded
    return grid


def encode_jpeg(img, q):
    opts = dict(quality=q, subsampling=0 if q >= 88 else 2)
    buf = io.BytesIO()
    # Pillow sizes the optimize buffer at w*h bytes; very grainy frames overflow it
    old = ImageFile.MAXBLOCK
    ImageFile.MAXBLOCK = max(old, img.width * img.height * 3 + 65536)
    try:
        img.save(buf, "JPEG", optimize=True, **opts)
    except OSError:
        buf = io.BytesIO()
        img.save(buf, "JPEG", **opts)
    finally:
        ImageFile.MAXBLOCK = old
    return buf


def save_jpeg(img, path, max_bytes=PHONE_MAX_BYTES):
    """Highest quality that fits the byte limit (4:4:4 chroma down to q88)."""
    q = 92
    while True:
        buf = encode_jpeg(img, q)
        if buf.tell() <= max_bytes or q <= 60:
            break
        q -= 4
    with open(path, "wb") as f:
        f.write(buf.getvalue())
    return q, buf.tell()


def _themes():
    # (page background, card background, text bars, label text)
    return (((24, 25, 26), (36, 37, 38), (70, 72, 74), (228, 230, 235)),
            ((240, 242, 245), (255, 255, 255), (214, 217, 222), (28, 30, 33)))


def feed_preview(thumb):
    """4:5 in a Facebook-style feed card (desktop 500 px, phone 360 px) in dark and light, plus a squint."""
    f = load_font(resolve_font(LABEL_FONTS), 13)
    widths = [(500, "desktop feed"), (360, "phone feed")]
    pad, gap = 28, 28
    card_h = lambda w: 56 + int(w * 1.25) + 44  # noqa: E731
    width = pad * 2 + sum(w for w, _ in widths) + 360 + gap * len(widths)
    row_h = card_h(500) + 44
    sheet = Image.new("RGB", (width, row_h * 2), (255, 255, 255))
    for r, (page, card, bars, fg) in enumerate(_themes()):
        y0 = r * row_h
        d = ImageDraw.Draw(sheet)
        d.rectangle((0, y0, width, y0 + row_h), fill=page)
        x = pad
        for w, name in widths:
            h = int(w * 1.25)
            d.rounded_rectangle((x, y0 + pad // 2, x + w, y0 + pad // 2 + card_h(w)), radius=10, fill=card)
            d.ellipse((x + 12, y0 + pad // 2 + 12, x + 44, y0 + pad // 2 + 44), fill=bars)
            d.rounded_rectangle((x + 54, y0 + pad // 2 + 16, x + 54 + w // 3, y0 + pad // 2 + 26), radius=5, fill=bars)
            d.rounded_rectangle((x + 54, y0 + pad // 2 + 32, x + 54 + w // 5, y0 + pad // 2 + 40), radius=4, fill=bars)
            sheet.paste(thumb.resize((w, h), Image.LANCZOS), (x, y0 + pad // 2 + 56))
            fy = y0 + pad // 2 + 56 + h + 14
            for i in range(3):
                d.rounded_rectangle((x + 16 + i * (w // 3), fy, x + 16 + i * (w // 3) + w // 5, fy + 14), radius=7,
                                    fill=bars)
            d.text((x, y0 + pad // 2 + card_h(w) + 20), "%d px  %s" % (w, name), font=f, fill=fg, anchor="ls")
            x += w + gap
        sq = squint(thumb.resize((360, 450), Image.LANCZOS))
        sheet.paste(sq, (x, y0 + pad // 2 + 56))
        d.text((x, y0 + pad // 2 + 56 + 450 + 20), "360 px  squint test", font=f, fill=fg, anchor="ls")
    return sheet


def reels_preview(thumb):
    """9:16 under a Reels/Shorts-style overlay (header, button column, caption), the 3:4 profile-grid
    crop, and a squint, in dark and light."""
    f = load_font(resolve_font(LABEL_FONTS), 13)
    pw, ph = 300, 533
    pad, gap = 28, 28
    width = pad * 2 + pw * 2 + 160 + gap * 2
    row_h = ph + 56
    sheet = Image.new("RGB", (width, row_h * 2), (255, 255, 255))
    for r, (page, _, _, fg) in enumerate(_themes()):
        y0 = r * row_h
        d = ImageDraw.Draw(sheet)
        d.rectangle((0, y0, width, y0 + row_h), fill=page)
        x = pad
        phone = thumb.resize((pw, ph), Image.LANCZOS).convert("RGBA")
        ov = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        grad = np.linspace(0, 150, int(ph * 0.35)).astype(np.uint8)
        shade = Image.fromarray(np.tile(grad[:, None], (1, pw)), "L")
        ov.paste((0, 0, 0, 255), (0, ph - len(grad)), shade)
        od.rounded_rectangle((12, 16, 90, 28), radius=6, fill=(255, 255, 255, 200))
        for i in range(4):
            cy = int(ph * 0.52) + i * 46
            od.ellipse((pw - 40, cy, pw - 14, cy + 26), fill=(255, 255, 255, 210))
        od.ellipse((12, int(ph * 0.80), 36, int(ph * 0.80) + 24), fill=(255, 255, 255, 220))
        od.rounded_rectangle((44, int(ph * 0.80) + 6, 150, int(ph * 0.80) + 16), radius=5, fill=(255, 255, 255, 220))
        od.rounded_rectangle((12, int(ph * 0.87), pw - 70, int(ph * 0.87) + 10), radius=5, fill=(255, 255, 255, 190))
        od.rounded_rectangle((12, int(ph * 0.91), pw - 120, int(ph * 0.91) + 10), radius=5, fill=(255, 255, 255, 190))
        phone.alpha_composite(ov)
        sheet.paste(phone.convert("RGB"), (x, y0 + pad // 2), corner_mask((pw, ph), 18))
        d.text((x, y0 + pad // 2 + ph + 22), "%d px  reels / shorts overlay" % pw, font=f, fill=fg, anchor="ls")
        x += pw + gap
        crop_h = int(thumb.width * 4 / 3)
        grid = thumb.crop((0, (thumb.height - crop_h) // 2, thumb.width, (thumb.height + crop_h) // 2))
        sheet.paste(grid.resize((160, 213), Image.LANCZOS), (x, y0 + pad // 2))
        d.text((x, y0 + pad // 2 + 213 + 22), "160 px  profile grid", font=f, fill=fg, anchor="ls")
        x += 160 + gap
        sheet.paste(squint(thumb.resize((pw, ph), Image.LANCZOS)), (x, y0 + pad // 2), corner_mask((pw, ph), 18))
        d.text((x, y0 + pad // 2 + ph + 22), "%d px  squint test" % pw, font=f, fill=fg, anchor="ls")
    return sheet


def export_paths(stem, aspect="16:9"):
    """The files export() writes for one variant: master, copy (when written), preview."""
    return [stem + ".jpg", stem + ("_phone.jpg" if aspect == "16:9" else "_1080.jpg"), stem + "_preview.png"]


def export(thumb, stem, aspect="16:9"):
    """Master (largest size, desktop upload), the standard-size copy, and the preview sheet.

    16:9: phone copy at 1280x720 under 2 MB (key "phone"). 4:5 and 9:16: a 1080-wide copy when the
    master is larger. Creates the output folder. Returns paths and sizes under "master", "copy"
    and "preview"."""
    folder = os.path.dirname(os.path.abspath(stem))
    os.makedirs(folder, exist_ok=True)
    mq, msize = save_jpeg(thumb, stem + ".jpg", MASTER_MAX_BYTES)
    files = {"master": (stem + ".jpg", thumb.size, mq, msize)}
    copy_size = ASPECTS[aspect]["copy"]
    if aspect == "16:9" or thumb.size != copy_size:
        name = stem + ("_phone.jpg" if aspect == "16:9" else "_1080.jpg")
        small = thumb if thumb.size == copy_size else thumb.resize(copy_size, Image.LANCZOS)
        q, size = save_jpeg(small, name, PHONE_MAX_BYTES)
        files["copy"] = (name, copy_size, q, size)
        if aspect == "16:9":
            files["phone"] = files["copy"]
    sheet = {"16:9": preview_sheet, "4:5": feed_preview, "9:16": reels_preview}[aspect](thumb)
    sheet.save(stem + "_preview.png")
    files["preview"] = stem + "_preview.png"
    return files


# ---------------------------------------------------------------- CLI

DEFAULTS = dict(style="serif", text_side="auto", focus=None, accent="#ffffff", bg=None, feather=0.36, shade=0.88,
                title_font=None, label_font=None, logo=None, logo_white=False, lead="ab", size="auto", push=None,
                kicker="", aspect="16:9", title_color="white", title_tracking=None)
PATH_KEYS = ("title_font", "label_font", "logo")
CHOICES = {"style": sorted(STYLES), "text_side": ["auto", "left", "right"], "lead": ["title", "label", "ab"],
           "size": ["auto"] + sorted(SIZES)}
FRACTIONS = ("focus", "feather", "shade", "push")


def check_setting(key, value, source="setting"):
    """Validate and normalize one setting (from the channel file or the command line)."""
    if value is None:
        return None
    if key in CHOICES:
        if value not in CHOICES[key]:
            raise ValueError("%s %r must be one of %s" % (source, value, ", ".join(CHOICES[key])))
    elif key in FRACTIONS:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError("%s %r must be a number from 0 to 1" % (source, value))
        value = float(value)
    elif key == "title_tracking":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 0.5:
            raise ValueError("%s %r must be a number from 0 to 0.5 (em), e.g. 0.12" % (source, value))
        value = float(value)
    elif key in ("accent", "bg", "title_color"):
        if key != "bg" and isinstance(value, str) and value.strip().lower() == "auto":
            return "auto"
        try:
            rgb(value)
        except (ValueError, TypeError):
            raise ValueError("%s %r is not a color (use e.g. #d9b26f%s)" % (source, value, "" if key == "bg" else ", white or auto"))
    elif key == "logo_white":
        if not isinstance(value, bool):
            raise ValueError("%s must be true or false" % source)
    elif key == "aspect":
        parts = value if isinstance(value, list) else str(value).split(",")
        parts = [str(p).strip() for p in parts if str(p).strip()]
        if parts == ["all"]:
            return ",".join(ASPECTS)
        bad = [p for p in parts if p not in ASPECTS]
        if bad or not parts:
            raise ValueError("%s %r must be any of %s, comma-separated, or all" % (source, value, ", ".join(ASPECTS)))
        return ",".join(parts)
    elif not isinstance(value, str):
        raise ValueError("%s %r must be text" % (source, value))
    return value


def load_channel(path):
    """Channel settings: any DEFAULTS key, validated. Relative font and logo paths resolve next to the file."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError("cannot read channel settings %s: %s" % (path, e))
    if not isinstance(data, dict):
        raise ValueError("channel settings %s must be a JSON object" % path)
    unknown = sorted(set(data) - set(DEFAULTS))
    if unknown:
        raise ValueError("unknown keys in %s: %s (allowed: %s)" % (path, ", ".join(unknown), ", ".join(sorted(DEFAULTS))))
    data = {k: v for k, v in data.items() if v is not None}  # null means "use the default"
    for k in list(data):
        data[k] = check_setting(k, data[k], "%s in %s" % (k, path))
    base = os.path.dirname(os.path.abspath(path))
    for k in PATH_KEYS:
        if data.get(k):
            name, _, inst = str(data[k]).partition("@")
            if not os.path.isabs(name) and os.path.isfile(os.path.join(base, name)):
                data[k] = os.path.join(base, name) + ("@" + inst if inst else "")
    return data


def settings(args):
    """CLI flags win over the channel file, which wins over defaults."""
    channel, source = {}, None
    path = args.channel or (CHANNEL_FILE if os.path.isfile(CHANNEL_FILE) else None)
    if path:
        channel, source = load_channel(path), path
    merged = {}
    for k, default in DEFAULTS.items():
        v = getattr(args, k)
        merged[k] = check_setting(k, v, "--" + k.replace("_", "-")) if v is not None else channel.get(k, default)
    return merged, source


def parse_box(text, flag="--inset-box", of="the background"):
    try:
        parts = [float(v) for v in text.split(",")]
    except ValueError:
        parts = []
    if len(parts) != 4 or not all(0 <= v <= 1 for v in parts) or parts[0] >= parts[2] or parts[1] >= parts[3]:
        raise argparse.ArgumentTypeError("%s takes x0,y0,x1,y1 as fractions of %s, 0 to 1" % (flag, of))
    return tuple(parts)


def parse_crop(text):
    return parse_box(text, "--crop", "the capture")


def output_stem(output, first):
    """-o may be a stem, a file name with an image extension, or a folder."""
    default = os.path.splitext(os.path.basename(first))[0] + "_thumb"
    if not output:
        return os.path.join(os.path.dirname(first), default)
    if output.endswith(("/", "\\")) or os.path.isdir(output):
        return os.path.join(output, default)
    root, ext = os.path.splitext(output)
    return root if ext.lower() in IMAGE_EXTS else output


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render a restrained editorial YouTube thumbnail.")
    ap.add_argument("capture", nargs="?", help="background frame: photo mode, HUD off, highest resolution you have")
    ap.add_argument("--title", default="", help="the subject: boss, character, person, product or game. \\n splits lines")
    ap.add_argument("--label", default="", help="the hook: what makes this video different, 4 words max")
    ap.add_argument("--lead", help="title, label, or ab: which one is big; ab renders both (default)")
    ap.add_argument("--kicker", help="small line above the big type (off unless given)")
    ap.add_argument("--subtitle", default="", help="small line under the big type")
    ap.add_argument("--panel", action="append", help="comparison image; pass 2-3 times instead of a capture")
    ap.add_argument("--panel-label", action="append", help="label per panel, in the same order")
    ap.add_argument("--subject", action="append", help="cut-out in front of the background (transparent PNG)")
    ap.add_argument("--push", type=float, help="blur and darken the background behind subjects, 0 to 1 (0.5)")
    ap.add_argument("--inset", help="image to place inside the background, e.g. a game frame on a monitor")
    ap.add_argument("--inset-box", type=parse_box, help="x0,y0,x1,y1 of the inset region, fractions of the background")
    ap.add_argument("--crop", type=parse_crop, help="x0,y0,x1,y1 of the capture to keep, fractions 0 to 1; narrower "
                    "than 16:9 puts the art on the far side, making room for type beside a centered subject")
    ap.add_argument("--style", help="serif or sans")
    ap.add_argument("--text-side", help="auto, left or right (16:9)")
    ap.add_argument("--focus", type=float, help="horizontal crop position, 0 to 1 (default: center on 16:9, "
                    "the subject on 4:5 and 9:16, the inset when there is one)")
    ap.add_argument("--aspect", help="16:9 (YouTube, default), 4:5 (Facebook/Instagram feed), 9:16 (Reels, "
                    "Shorts, Stories); comma-separated or 'all'")
    ap.add_argument("--title-color", help="big type color: white (default), auto (the image's signature color, "
                    "lifted for contrast) or a color like #f2b84b")
    ap.add_argument("--accent", help="label box color: white (default), auto, or a color")
    ap.add_argument("--bg", help="background color, e.g. #0d0c0b (default: from the image's shadows)")
    ap.add_argument("--feather", type=float, help="edge fade for narrow art, share of its width")
    ap.add_argument("--shade", type=float, help="darkness of the type side, 0 to 1")
    ap.add_argument("--title-font", help="bundled font name (cinzel, bebas...; see --list-fonts) or a font file; "
                    "optional @Weight, e.g. cinzel@Black")
    ap.add_argument("--title-tracking", type=float, help="letter spacing of the big type in em, 0 to 0.5 (default: the font's own); wide, e.g. 0.12, for a thin, spaced-out logo")
    ap.add_argument("--label-font", help="bundled font name or font file for the label box; optional @Weight")
    ap.add_argument("--logo", help="transparent PNG of the game or brand logo; replaces the title text")
    ap.add_argument("--logo-white", action="store_true", default=None, help="recolor the logo white")
    ap.add_argument("--size", help="16:9 master size: auto (largest without upscaling), 720p, 1080p, 1440p, 4k")
    ap.add_argument("--channel", help="channel settings JSON (default: ./%s if present)" % CHANNEL_FILE)
    ap.add_argument("--feed", help="folder of other thumbnails to test yours against")
    ap.add_argument("-o", "--output", help="output stem, .jpg name, or folder (default: <capture>_thumb)")
    ap.add_argument("--list-fonts", action="store_true", help="list the bundled title fonts and exit")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # a cp1252 console must never abort a run over one character
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    if args.list_fonts:
        cat = font_catalog()
        if not cat:
            print("no bundled fonts found in %s" % FONT_ROOT)
            return 1
        for name, e in sorted(cat.items()):
            print("%-10s %s (%s). Cues: %s. Fits: %s.%s" % (
                name, e.get("family", "?"), e["look"], e["cues"], e["fits"],
                "" if e["caps"] else " Use Title Case, not all caps."))
        return 0

    try:
        s, channel_path = settings(args)
    except ValueError as e:
        ap.error(str(e))
    if not args.title.strip() and not s["logo"]:
        ap.error("give --title (or a --logo)")
    if args.inset_box and not args.inset:
        ap.error("--inset-box needs --inset")
    first = args.capture or (args.panel or [None])[0]
    if not first:
        ap.error("give a capture, or two or more --panel images")
    if args.feed and not os.path.isdir(args.feed):
        ap.error("--feed folder not found: %s" % args.feed)
    leads = ["title", "label"] if s["lead"] == "ab" and args.label else ["label" if s["lead"] == "label" else "title"]
    aspects = s["aspect"].split(",")
    stem = output_stem(args.output, first)

    def norm(p):
        return os.path.normcase(os.path.abspath(p))

    plan = []  # (aspect, lead, file stem)
    for aspect in aspects:
        for lead in leads:
            tag = ("" if len(leads) == 1 else ("_A" if lead == "title" else "_B")) + ASPECTS[aspect]["suffix"]
            plan.append((aspect, lead, stem + tag))
    outputs = [p for a, _, st in plan for p in export_paths(st, a)] + [stem + "_feed.png"]
    inputs = [args.capture, args.inset, s["logo"], channel_path] + list(args.panel or []) + list(args.subject or [])
    clash = sorted({norm(p) for p in outputs} & {norm(p) for p in inputs if p})
    if clash:
        ap.error("output would overwrite an input file: %s; choose another -o" % clash[0])

    def open_image(path, what):
        if not os.path.isfile(path):
            ap.error("%s not found: %s" % (what, path))
        try:
            img = Image.open(path)
            img.load()
            return img
        except (OSError, ValueError, Image.DecompressionBombError) as e:
            ap.error("%s is not a readable image: %s (%s)" % (what, path, e))

    try:
        common = dict(title=args.title, label=args.label, kicker=s["kicker"] or "", subtitle=args.subtitle,
                      style=s["style"], text_side=s["text_side"], focus=s["focus"], accent=s["accent"], bg=s["bg"], title_color=s["title_color"],
                      feather=s["feather"], shade=s["shade"], title_font=s["title_font"],
                      label_font=s["label_font"], title_tracking=s["title_tracking"],
                      logo=open_image(s["logo"], "logo") if s["logo"] else None, logo_white=bool(s["logo_white"]),
                      size=s["size"], panels=[open_image(p, "panel") for p in args.panel or []] or None,
                      panel_labels=args.panel_label,
                      subjects=[open_image(p, "subject") for p in args.subject or []] or None,
                      push=s["push"], inset=open_image(args.inset, "inset") if args.inset else None,
                      inset_box=args.inset_box, crop=args.crop)
        src = open_image(args.capture, "capture") if args.capture else None
    except ValueError as e:
        ap.error(str(e))
    if channel_path:
        print("channel settings: %s" % channel_path)
    if args.aspect is None and not (channel_path and "aspect" in load_channel(channel_path)):
        print("note: no saved formats, so 16:9 only. Save \"aspect\" (e.g. \"16:9,4:5\") in %s to set them "
              "once." % (channel_path or CHANNEL_FILE))
    thumbs, written = [], []
    for aspect, lead, file_stem in plan:
        try:
            thumb, info = render(src, lead=lead, aspect=aspect, **common)
        except ValueError as e:
            ap.error(str(e))
        files = export(thumb, file_stem, aspect)
        written += [f[0] if isinstance(f, tuple) else f for f in files.values()]
        if aspect == "16:9":
            thumbs.append(thumb)
        print("== %s%s" % (aspect, "" if len(leads) == 1 else ", variant %s: %s leads"
                           % ("A" if lead == "title" else "B", lead)))
        print("layout %s, source %dx%d, scaled %.2fx, type %s, contrast %.1f:1"
              % (info["layout"], info["source"][0], info["source"][1], info["scale"], info["text_side"],
                 info["contrast"]))
        for rep in info.get("subjects", []):
            print("subject matched: %s" % ", ".join("%s %s" % kv for kv in rep.items()))
        if "inset" in info:
            print("inset matched: %s" % ", ".join("%s %s" % kv for kv in info["inset"].items()))
        print("title type: %s" % info["title_source"])
        print("colors: big type %s; label box %s" % (info["colors"]["big type"], info["colors"]["label box"]))
        for n in info["notes"]:
            print("note: " + n)
        for key in ("master", "copy"):
            if key not in files:
                continue
            path, (w, h), q, size = files[key]
            print("wrote %s (%dx%d, JPEG q%d, %d KB)" % (path, w, h, q, size // 1024))
        print("wrote %s" % files["preview"])
        for w in info["warnings"]:
            print("warning: " + w)
    if len(leads) == 1 and s["lead"] == "ab":
        print("note: no --label, so only one variant")
    if args.feed:
        if not thumbs:
            print("warning: --feed compares 16:9 thumbnails; add 16:9 to --aspect to get a feed grid.")
        else:
            grid = feed_grid(thumbs, args.feed, exclude=written + [p for p in inputs if p])
            grid.save(stem + "_feed.png")
            print("wrote %s_feed.png (%d other thumbnails)" % (stem, grid.info["competitors"]))
            if grid.info["competitors"] == 0:
                print("warning: no other thumbnails were loaded from %s." % args.feed)
    print("fonts: title %s; label %s" % info["fonts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
