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
from dataclasses import dataclass, field
import io
import json
import os
import unicodedata
import sys

import numpy as np
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFile, ImageFilter, ImageFont, ImageOps

__version__ = "0.8.1"

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
BANDS = ("auto", "top", "bottom")  # --band: where the type band goes on 4:5 and 9:16
COL_SHARE = 0.40  # type column width as a share of the canvas
MAX_LUMA_BEHIND_TYPE = 0.45  # white type over sRGB 0.45 is about 4.5:1 contrast
MIN_CONTRAST = 4.5  # WCAG AA for normal text, applied to every element
CHANNEL_FILE = "clean-thumbnail.json"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# First font found wins. "file@Instance" picks a named instance of a variable font; a bare short
# name ("cinzel", "outfit@Bold") is one of the fonts bundled in ../fonts (see --list-fonts).
# The defaults are bundled, so the same command sets the same type on every machine. The previews'
# captions and duration badge always use LABEL_FONTS.
LABEL_FONTS = ["outfit@SemiBold"]
STYLES = {
    # high-contrast serif: fantasy, period, literary, sport-heritage
    "serif": {"title": ["playfair@Bold"], "tracking": 0.06},
    # geometric sans: tech, sci-fi, modern, competitive
    "sans": {"title": ["outfit@Bold"], "tracking": 0.02},
}
# system_fonts=True (--system-fonts, "system_fonts": true): installed faces first, as up to v0.7.0. They
# differ per machine (Bodoni MT comes with Office, Didot and Helvetica Neue with macOS), so the type does too.
SYSTEM_LABEL_FONTS = ["bahnschrift.ttf@SemiBold", "segoeuib.ttf", "Avenir Next.ttc", "HelveticaNeue.ttc",
                      "outfit@SemiBold", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"]
SYSTEM_TITLE_FONTS = {
    "serif": ["BOD_R.TTF", "Didot.ttc", "GARA.TTF", "constan.ttf", "georgia.ttf", "playfair",
              "DejaVuSerif.ttf", "LiberationSerif-Regular.ttf", "times.ttf"],
    "sans": ["bahnschrift.ttf@Bold", "segoeuib.ttf", "HelveticaNeue.ttc", "outfit@Bold",
             "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"],
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
    def timestamp_zone(self):
        """The corner the duration badge covers (16:9 only): the badge the preview draws on the 168 px "up next"
        tile (badge_rect), in fractions of the frame. Larger tiles show a relatively smaller badge."""
        if self.tall:
            return (0, 0, 0, 0)
        tw, th = BADGE["tile"], tile_height(BADGE["tile"])
        x0, y0, _, _ = badge_rect(tw, th)  # from the first tile pixel the badge touches
        return (int(self.w * int(x0) / tw), int(self.h * int(y0) / th), self.w, self.h)

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


def background_box(src, crop=None, trimmed=None):
    """The pixel box (left, top, right, bottom) of the capture that the thumbnail keeps: bars
    trimmed, then --crop (fractions of the image as given, so bars go whether or not the crop
    includes them), then clean bars inside the crop (a video player inside a screenshot).
    trimmed: trim_box(src) when the caller already has it."""
    w, h = src.size
    box = trim_box(src) if trimmed is None else tuple(trimmed)
    if crop is not None:
        c = (crop[0] * w, crop[1] * h, crop[2] * w, crop[3] * h)
        box = (max(box[0], c[0]), max(box[1], c[1]), min(box[2], c[2]), min(box[3], c[3]))
        box = tuple(int(round(v)) for v in box)
        if box[2] - box[0] < 16 or box[3] - box[1] < 16:
            raise ValueError("--crop leaves almost nothing of the image (after its bars are trimmed): widen it")
        inner = trim_box(src.crop(box), pristine_only=True)  # clean bars of a video inside the crop, e.g. a player
        box = (box[0] + inner[0], box[1] + inner[1], box[0] + inner[2], box[1] + inner[3])
    return box


def remap_box(region, size, kept):
    """A box in fractions of the image as given (size) -> fractions of the kept pixel box. None stays None."""
    w, h = size
    if region is None or kept == (0, 0, w, h):
        return region
    bw, bh = kept[2] - kept[0], kept[3] - kept[1]
    return ((region[0] * w - kept[0]) / bw, (region[1] * h - kept[1]) / bh,
            (region[2] * w - kept[0]) / bw, (region[3] * h - kept[1]) / bh)


def prepare_background(src, crop=None, inset_box=None):
    """Trim bars, then apply --crop (see background_box). Returns (image, inset_box remapped
    to fractions of the returned image, crop box in pixels or None)."""
    w, h = src.size
    box = background_box(src, crop)
    inset_box = remap_box(inset_box, (w, h), box)
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


def cover(src, w, h, focus=0.5, vfocus=None):
    """Scale and crop src to exactly w x h, resizing only the visible window (fast and
    memory-safe on huge sources). Returns (image, scale, sx, sy): canvas = (src - (sx, sy)) * scale."""
    box, scale = cover_box(src.width, src.height, w, h, focus, vfocus)
    return src.resize((w, h), Image.LANCZOS, box=box), scale, box[0], box[1]


def cover_box(sw, sh, w, h, focus=0.5, vfocus=None):
    """Source window (x0, y0, x1, y1) and scale for a w x h cover crop, always inside the source
    (float error in w/scale can overshoot the source by 1e-13, which Pillow rejects). vfocus moves
    the crop vertically (0 top, 1 bottom) when the source is taller than the canvas; None centers."""
    scale = max(w / sw, h / sh)
    cw, ch = min(sw, w / scale), min(sh, h / scale)
    sx = min(max(0.0, (sw - cw) * min(max(focus, 0.0), 1.0)), sw - cw)
    if vfocus is None:
        sy = max(0.0, (sh - ch) / 2)
    else:
        sy = min(max(0.0, (sh - ch) * min(max(vfocus, 0.0), 1.0)), sh - ch)
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


def column_alpha(cv, shade, text_side):
    """Per-column shade of the 16:9 type column: solid across it, fading out by ~65% of the width."""
    plateau, falloff = cv.margin + cv.col_w * 0.75, cv.w * 0.28
    alpha = shade * (1 - smoothstep((np.arange(cv.w, dtype=np.float32) - plateau) / falloff))
    if text_side == "right":
        alpha = alpha[::-1]
    return alpha


def feather_ramp(w, feather, text_side):
    """Opacity across narrow art (w px wide): its edge toward the type fades over feather * w."""
    ramp = smoothstep(np.arange(w, dtype=np.float32) / max(1.0, feather * w))
    if text_side == "right":
        ramp = ramp[::-1]
    return ramp


def band_alpha(cv, shade, band, anchor):
    """Per-row shade of the tall type band (see shade_band)."""
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
    return alpha


def column_shade(canvas, cv, bg, shade, text_side):
    """Shade the type column with the background color: solid across it, fading out by ~65% of the width."""
    alpha = column_alpha(cv, shade, text_side)
    canvas.paste(Image.new("RGB", canvas.size, bg), (0, 0),
                 Image.fromarray((np.tile(alpha, (cv.h, 1)) * 255).astype(np.uint8), "L"))
    return canvas


def compose_tall(src, cv, bg, focus=None, shade=0.88, push=0.0, band=(0, 0), anchor="bottom", vfocus=None):
    """Cover a tall canvas with the image and shade a band for the type.

    The crop keeps the busiest region (the subject) unless focus is given. The shade is
    solid across `band` (the type block, padded) and fades out toward the image.
    Returns (canvas, scale, mapper from source fractions to canvas px)."""
    if focus is None:
        focus = subject_focus(src, (cv.w / max(cv.w / src.width, cv.h / src.height)) / src.width)
    img, scale, sx, sy = cover(src, cv.w, cv.h, focus, vfocus)
    canvas = depth(grade(img), cv, push)
    canvas = shade_band(canvas, cv, bg, shade, band, anchor)
    mapper = lambda fx, fy, s=scale, sx=sx, sy=sy: ((fx * src.width - sx) * s, (fy * src.height - sy) * s)  # noqa: E731
    return canvas, scale, mapper


def shade_band(canvas, cv, bg, shade, band, anchor):
    """Shade from the type band to the canvas edge on the anchor side, fading toward the image.
    On 9:16 the type sits mid-frame (above the caption area), so the shade is held around the
    band and eases to a light 0.35 below it instead of darkening the whole lower frame."""
    alpha = band_alpha(cv, shade, band, anchor)
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
        ramp = feather_ramp(w, feather, text_side)
        x0 = cv.w - w if text_side == "left" else 0
        canvas.paste(img, (x0, 0), Image.fromarray((np.tile(ramp, (cv.h, 1)) * 255).astype(np.uint8), "L"))
        mapper = lambda fx, fy: (x0 + fx * w, fy * cv.h)  # noqa: E731
    else:
        img, scale, sx, sy = cover(src, cv.w, cv.h, focus)
        canvas = depth(grade(img), cv, push)
        mapper = lambda fx, fy, s=scale, sx=sx, sy=sy: ((fx * src.width - sx) * s, (fy * src.height - sy) * s)  # noqa: E731
    return column_shade(canvas, cv, bg, shade, text_side), scale, mapper


# ---------------------------------------------------------------- subject box (a face or product label to keep clear)
# The shade is 1-D (columns on 16:9, rows on tall canvases), so where a box lands and how much of it
# the shade darkens is computed exactly from the geometry, before anything is rendered.

def wide_view(src, cv, text_side, focus=0.5, feather=0.36, shade=0.88, cropped=False):
    """Where a single 16:9 background lands, without rendering it: (mapper from source fractions to
    canvas px, per-column visibility 0..1 after the narrow-art feather and the column shade)."""
    vis = 1 - column_alpha(cv, shade, text_side)
    if is_narrow(src, cv.w, cv.h, cropped):
        w = int(round(src.width / src.height * cv.h))
        x0 = cv.w - w if text_side == "left" else 0
        art = np.zeros(cv.w, dtype=np.float32)
        art[max(0, x0):x0 + w] = feather_ramp(w, feather, text_side)[max(0, -x0):cv.w - x0]
        vis = vis * art
        mapper = lambda fx, fy: (x0 + fx * w, fy * cv.h)  # noqa: E731
    else:
        (sx, sy, _, _), scale = cover_box(src.width, src.height, cv.w, cv.h, focus)
        mapper = lambda fx, fy: ((fx * src.width - sx) * scale, (fy * src.height - sy) * scale)  # noqa: E731
    return mapper, vis


def tall_view(src, cv, focus, vfocus, shade, band, anchor):
    """Tall-canvas counterpart of wide_view: (mapper, per-row visibility under the type band)."""
    (sx, sy, _, _), scale = cover_box(src.width, src.height, cv.w, cv.h, focus, vfocus)
    mapper = lambda fx, fy: ((fx * src.width - sx) * scale, (fy * src.height - sy) * scale)  # noqa: E731
    return mapper, 1 - band_alpha(cv, shade, band, anchor)


def box_rect(box, mapper):
    x0, y0 = mapper(box[0], box[1])
    x1, y1 = mapper(box[2], box[3])
    return (x0, y0, x1, y1)


def box_cover(rect, cv, vis_x=None, vis_y=None, region=None):
    """(share of rect inside region (default: the canvas), mean visibility of its on-canvas part,
    share of that part under heavy shade, visibility below 0.5)."""
    rw, rh = rect[2] - rect[0], rect[3] - rect[1]
    if rw <= 0 or rh <= 0:
        return 0.0, 0.0, 0.0
    rg = region or (0, 0, cv.w, cv.h)
    iw = max(0.0, min(rect[2], rg[2]) - max(rect[0], rg[0]))
    ih = max(0.0, min(rect[3], rg[3]) - max(rect[1], rg[1]))
    inside = iw * ih / (rw * rh)
    x0, x1 = int(round(max(0, rect[0]))), int(round(min(cv.w, rect[2])))
    y0, y1 = int(round(max(0, rect[1]))), int(round(min(cv.h, rect[3])))
    if x1 <= x0 or y1 <= y0:
        return inside, 0.0, 0.0
    vx = vis_x[x0:x1] if vis_x is not None else np.ones(x1 - x0, dtype=np.float32)
    vy = vis_y[y0:y1] if vis_y is not None else np.ones(y1 - y0, dtype=np.float32)
    vis = float(vx.mean() * vy.mean())
    heavy = float((np.outer(vy, vx) < 0.5).mean())
    return inside, vis, heavy


def focus_away(box, src, cv, text_side):
    """Crop position for a 16:9 cover crop of a wider source that puts the box (fractions of src)
    clear of the type column's shade: its center near 70% of the width (30% with the type on the right)."""
    scale = max(cv.w / src.width, cv.h / src.height)
    cw = cv.w / scale
    if cw >= src.width:
        return 0.5
    half = (box[2] - box[0]) * src.width / cw / 2  # half the box width, in canvas fractions
    c = min(max(0.70, 0.64 + half), max(0.70, 0.98 - half))
    if text_side == "right":
        c = 1 - c
    sx = (box[0] + box[2]) / 2 * src.width - c * cw
    return float(min(max(sx / (src.width - cw), 0.0), 1.0))


def vfocus_on(box, src, cv):
    """Vertical crop position for a tall canvas when the source is taller than it: the box center at
    40% of the height (above the type band, below the Reels header). None when nothing is cropped."""
    scale = max(cv.w / src.width, cv.h / src.height)
    ch = cv.h / scale
    if ch >= src.height:
        return None
    sy = (box[1] + box[3]) / 2 * src.height - 0.40 * ch
    return float(min(max(sy / (src.height - ch), 0.0), 1.0))


SUGGEST_MIN_CLEAR = 0.9  # mean visibility a suggested --crop must give the box


def suggest_crop(box, size, cv, sides, feather=0.36, shade=0.88, bars=None, kept_h=None, y=(0.0, 1.0), keep=(),
                 exact=None):
    """A --crop (fractions of the capture as given) narrower than 16:9 that puts the box on the far side,
    clear of the type shade: widest first, in 0.01 steps, the first with mean box visibility 0.9 or more.
    box is in fractions of the capture as given (size); bars is the (left, right) pixel range left after
    trimming pillarbox bars; kept_h the art height in pixels the re-run keeps. keep: boxes (fractions of the
    capture as given, such as an inset) a crop must keep whole and beside the type column. exact: crop -> the pixel box a re-run with
    that crop keeps (background_box, which also trims bars inside the crop), or None; it confirms a
    candidate that passes the quick check, up to 12 times. Returns ((x0, y0, x1, y1), side) or None."""
    W, H = size
    bars = bars or (0, W)
    kh = float(kept_h or H)
    tries = 12

    column = (cv.margin, cv.margin + cv.col_w)

    def visible(side, clear, ramps, left, right, kh):
        if right - left < 16 or (right - left) / kh >= cv.w / cv.h * 0.99:
            return 0.0
        w = int(round((right - left) / kh * cv.h))
        x0 = cv.w - w if side == "left" else 0

        def cols(b):
            return (int(round(x0 + (b[0] * W - left) / (right - left) * w)),
                    int(round(x0 + (b[2] * W - left) / (right - left) * w)))
        for b in keep:  # an inset must stay out of the type column
            k0, k1 = cols(b)
            if (k0 < column[1]) if side == "left" else (k1 > cv.w - column[1]):
                return 0.0
        if w not in ramps:
            ramps[w] = feather_ramp(w, feather, side)
        c0, c1 = cols(box)
        c0, c1 = max(c0, x0, 0), min(c1, x0 + w, cv.w)
        return float((clear[c0:c1] * ramps[w][c0 - x0:c1 - x0]).mean()) if c1 > c0 else 0.0

    for side in sides:
        clear = 1 - column_alpha(cv, shade, side)
        ramps = {}
        for k in range(85, 49, -1):
            for a in range(0, 101 - k):
                a0, a1 = a / 100.0, (a + k) / 100.0
                if box[0] < a0 - 1e-9 or box[2] > a1 + 1e-9:
                    continue
                if any(b[0] < a0 - 1e-9 or b[2] > a1 + 1e-9 or b[1] < y[0] - 1e-9 or b[3] > y[1] + 1e-9
                       for b in keep):
                    continue
                if visible(side, clear, ramps, max(a0 * W, bars[0]), min(a1 * W, bars[1]), kh) < SUGGEST_MIN_CLEAR:
                    continue
                crop = (a0, y[0], a1, y[1])
                if exact is not None:
                    if tries <= 0:
                        return None
                    tries -= 1
                    kb = exact(crop)
                    if kb is None or visible(side, clear, ramps, kb[0], kb[2], float(kb[3] - kb[1])) < SUGGEST_MIN_CLEAR:
                        continue
                return crop, side
    return None


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


def _advances(font, text, tracking, space=0.0):
    """Per-character advance for tracked text, keeping each pair's kerning (AV, TO, YO). `space` is a
    minimum advance for a word space (preview captions: some fonts' spaces vanish at 12 px)."""
    kw = _fkw(font)
    w = [font.getlength(c, **kw) for c in text]
    out = []
    for i in range(len(text)):
        a = w[i]
        if i + 1 < len(text):
            a += tracking + font.getlength(text[i:i + 2], **kw) - w[i] - w[i + 1]
        if text[i] == " ":
            a = max(a, space)
        out.append(a)
    return out


def text_width(font, text, tracking, space=0.0):
    if not text:
        return 0.0
    if tracking or space:
        return sum(_advances(font, text, tracking, space))
    return font.getlength(text, **_fkw(font))


def draw_text(draw, x, baseline, text, font, tracking, fill, space=0.0):
    if not tracking and not space:
        draw.text((x, baseline), text, font=font, fill=fill, anchor="ls", **_fkw(font))
        return
    for c, a in zip(text, _advances(font, text, tracking, space)):
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
        safe_room = True  # some spot in this panel is inside the Reels/Shorts safe zone
        if cv.tall:
            lo, hi = y0 + cv.px(8), y1 - h - cv.px(8)
            slo, shi = (max(lo, safe[1]), min(hi, safe[3] - h)) if cv.reels else (lo, hi)
            safe_room = slo <= shi
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
            if not safe_room:
                warnings.append("panel %d sits under the Reels/Shorts caption area, so its label can't be in the "
                                "safe zone: use two panels for 9:16." % (i + 1))
            else:  # the type takes the panel's safe part
                warnings.append("panel %d's label has no spot clear of the type inside the Reels/Shorts safe "
                                "zone: shorten the type (one line, no kicker) or the label." % (i + 1))
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

@dataclass
class Job:
    """One render's state. The arguments come first; each stage of render() reads what earlier stages set
    and fills in its own fields, so a stage's inputs and outputs are visible at its top and bottom."""
    src: object = None
    title: str = ""
    label: str = ""
    kicker: str = ""
    subtitle: str = ""
    lead: str = "title"
    style: str = "serif"
    text_side: str = "auto"
    focus: object = None
    accent: object = "#ffffff"
    bg: object = None
    feather: float = 0.36
    shade: float = 0.88
    title_font: object = None
    label_font: object = None
    logo: object = None
    logo_white: bool = False
    system_fonts: bool = False
    size: str = "auto"
    panels: object = None
    panel_labels: object = None
    subjects: object = None
    push: object = None
    inset: object = None
    inset_box: object = None
    aspect: str = "16:9"
    title_color: object = "white"
    crop: object = None
    title_tracking: object = None
    subject_box: object = None
    band_pos: str = "auto"  # --band
    info: dict = field(default_factory=dict)
    # set by _load_sources
    layout: str = None
    src_given: object = None
    crop_px: object = None
    given_size: tuple = None
    kept_px: tuple = None
    subject_box_given: object = None
    backgrounds: list = None
    cropped: bool = False
    cv: object = None
    narrow: bool = False
    # set by _choose_layout
    anchor: object = None
    band_forced: bool = False
    focus_eff: object = None
    vfocus: object = None
    # set by _pick_fonts
    entry: object = None
    tracking_em: float = 0.0
    caps_ok: bool = True
    tspec: object = None
    lspec: object = None
    # set by _assign_roles
    big: str = ""
    box: str = ""
    logo_big: bool = True
    big_name: str = "title"
    big_rgb: tuple = None
    accent_rgba: tuple = None
    # set by _type_room
    safe: tuple = None
    rects: list = None
    div: float = None
    max_w: int = 0
    base: int = 0
    avail: int = 0
    bottom_limit: int = None
    # set by _pick_anchor: per band ("top", "bottom"), how it leaves --subject-box (see _band_measure)
    band_scores: dict = None
    # set by _fit_type and _place_band
    items: list = None
    bw: int = 0
    bh: int = 0
    top: int = 0
    band: tuple = None
    # set by _compose
    built: dict = None


def _check_args(j):
    """Normalize the text and reject impossible argument combinations before any image work."""
    title, label, kicker, subtitle, panel_labels = j.title, j.label, j.kicker, j.subtitle, j.panel_labels
    aspect, style, panels, src, inset, subjects, crop = j.aspect, j.style, j.panels, j.src, j.inset, j.subjects, j.crop
    subject_box = j.subject_box
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
    if j.band_pos not in BANDS:
        raise ValueError("band must be one of %s" % ", ".join(BANDS))
    if crop is not None and (len(crop) != 4 or not all(0 <= v <= 1 for v in crop)
                             or crop[0] >= crop[2] or crop[1] >= crop[3]):
        raise ValueError("--crop takes x0,y0,x1,y1 as fractions of the capture, 0 to 1")
    if subject_box is not None:
        if panels or subjects:
            raise ValueError("--subject-box marks a face or product inside the background capture; it can't be used "
                             "with --panel or --subject (a cut-out is placed by the script and checked on its own)")
        if (len(subject_box) != 4 or not all(0 <= v <= 1 for v in subject_box)
                or subject_box[0] >= subject_box[2] or subject_box[1] >= subject_box[3]):
            raise ValueError("--subject-box takes x0,y0,x1,y1 as fractions of the capture, 0 to 1")
        j.subject_box = tuple(float(v) for v in subject_box)
    j.title, j.label, j.kicker, j.subtitle, j.panel_labels = title, label, kicker, subtitle, panel_labels


def _load_sources(j):
    """Decode the images, crop the capture and pick the canvas size."""
    panels, subjects, src, crop, inset_box, info = j.panels, j.subjects, j.src, j.crop, j.inset_box, j.info
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
        j.src_given = src  # the capture as given, for --crop advice
        kept = background_box(src, crop)  # trim_box is the costly part: once for the inset and the subject box
        inset_box = remap_box(inset_box, given, kept)
        crop_px = kept if crop is not None else None
        _keep_subject_box(j, given, kept)
        src = src if kept == (0, 0) + tuple(given) else src.crop(kept)
        if crop_px is not None:
            info["notes"].append("cropped the capture to x %d-%d, y %d-%d of %dx%d"
                                 % (crop_px[0], crop_px[2], crop_px[1], crop_px[3], given[0], given[1]))
    backgrounds = panels or [src]
    info["source"] = backgrounds[0].size
    cropped = layout == "single" and crop_px is not None
    cv = Canvas(*pick_size(backgrounds, layout, j.size, j.aspect, cropped))
    info["size"] = (cv.w, cv.h)
    narrow = layout == "single" and is_narrow(src, cv.w, cv.h, cropped)
    j.panels, j.subjects, j.src, j.inset_box, j.layout, j.crop_px = panels, subjects, src, inset_box, layout, crop_px
    j.backgrounds, j.cropped, j.cv, j.narrow = backgrounds, cropped, cv, narrow


def _keep_subject_box(j, given, kept):
    """Remap --subject-box through the trimmed bars and --crop like the inset box, clipped to the kept image."""
    j.given_size, j.kept_px, j.subject_box_given = tuple(given), tuple(kept), j.subject_box
    if j.subject_box is None:
        return
    b = remap_box(j.subject_box, given, kept)
    c = tuple(min(max(v, 0.0), 1.0) for v in b)
    area = (b[2] - b[0]) * (b[3] - b[1])
    kept_share = max(0.0, c[2] - c[0]) * max(0.0, c[3] - c[1]) / area
    if kept_share <= 0:
        j.info["warnings"].append("--subject-box is entirely cut away by --crop or the trimmed bars, so it is "
                                  "ignored: box what stays in the picture.")
        j.subject_box = None
        return
    if kept_share < 0.9:
        j.info["warnings"].append("%d%% of --subject-box is cut away by --crop or the trimmed bars: widen --crop "
                                  "or box what stays in the picture." % round(100 * (1 - kept_share)))
    j.subject_box = c


def _side_for_box(j):
    """16:9, --text-side auto: the side whose shade leaves --subject-box most visible; busyness breaks near-ties."""
    src, cv, box = j.src, j.cv, j.subject_box
    wider = not j.narrow and src.width / src.height > (cv.w / cv.h) * 1.1
    scores = {}
    for side in ("left", "right"):
        f = j.focus if j.focus is not None else (focus_away(box, src, cv, side) if wider else 0.5)
        mapper, vis = wide_view(src, cv, side, f, j.feather, j.shade, j.cropped)
        inside, v, _ = box_cover(box_rect(box, mapper), cv, vis_x=vis)
        scores[side] = inside * v
    if abs(scores["left"] - scores["right"]) < 0.05:
        left, right = busyness(src)
        return "left" if left <= right else "right"
    side = "left" if scores["left"] > scores["right"] else "right"
    other = "right" if side == "left" else "left"
    j.info["notes"].append("type on the %s, clear of --subject-box (%d%% visible there, %d%% with the type on the %s)"
                           % (side, round(100 * scores[side]), round(100 * scores[other]), other))
    return side


def _choose_layout(j):
    """Which side the type takes, the background color, and where the crop centers."""
    cv, subjects, layout, text_side, info, inset, inset_box = j.cv, j.subjects, j.layout, j.text_side, j.info, j.inset, j.inset_box
    src, bg, backgrounds, push, focus, narrow = j.src, j.bg, j.backgrounds, j.push, j.focus, j.narrow
    anchor = None
    if cv.tall:
        anchor = "top" if subjects and layout == "single" else "bottom"
        if j.band_pos != "auto":  # --band: the type band where asked, on a single capture without a cut-out
            if layout == "single" and not subjects:
                anchor, j.band_forced = j.band_pos, True
            else:
                info["notes"].append("--band %s is ignored with %s, which sets where the type goes"
                                     % (j.band_pos, "--panel" if layout == "panels" else "--subject"))
        text_side = "left"
        info["text_side"] = anchor
    else:
        if layout == "single" and text_side == "auto":
            if inset is not None:  # type on the side with more room beside the inset
                ib = inset_box or (0.25, 0.25, 0.75, 0.75)
                text_side = "left" if ib[0] >= 1 - ib[2] else "right"
            elif j.subject_box is not None:
                text_side = _side_for_box(j)
            else:
                # wide frames: type over the calmer half; narrow art: its calmer half fades toward the type
                left, right = busyness(src)
                text_side = "left" if left <= right else "right"
        info["text_side"] = text_side if layout == "single" else "bottom"
    bg = rgb(bg) if bg else dark_tone(backgrounds[0])
    push = (0.5 if subjects else 0.0) if push is None else push
    focus_eff = focus
    if focus is None and cv.tall and j.subject_box is not None:  # the face wins over an inset on tall crops
        focus_eff = focus_on(j.subject_box, src, cv.w, cv.h)
        info["notes"].append("crop centers --subject-box%s (focus %.2f); set --focus to override"
                             % (" rather than the inset" if inset is not None else "", focus_eff))
    elif inset is not None and focus is None and layout == "single" and not narrow:
        focus_eff = focus_on(inset_box or (0.25, 0.25, 0.75, 0.75), src, cv.w, cv.h)
    elif (focus is None and layout == "single" and not cv.tall and not narrow
          and src.width / src.height > (cv.w / cv.h) * 1.1):
        if j.subject_box is not None:  # wider than 16:9: the box goes clear of the type
            focus_eff = focus_away(j.subject_box, src, cv, text_side)
            info["notes"].append("crop keeps --subject-box clear of the type (focus %.2f); set --focus to override"
                                 % focus_eff)
        else:
            # wider than 16:9: keep the busiest region (the subject) on the side away from the type
            share = (cv.w / max(cv.w / src.width, cv.h / src.height)) / src.width
            focus_eff = side_focus(src, share, text_side)
            info["notes"].append("crop follows the subject (focus %.2f); set --focus to override" % focus_eff)
    if cv.tall and j.subject_box is not None:
        j.vfocus = vfocus_on(j.subject_box, src, cv)
    j.anchor, j.text_side, j.bg, j.push, j.focus_eff = anchor, text_side, bg, push, focus_eff


def _pick_fonts(j):
    """Resolve the title and label fonts, their weights and the title's tracking."""
    title_font, label_font, style, title_tracking = j.title_font, j.label_font, j.style, j.title_tracking
    warnings = j.info["warnings"]
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
        tspec = resolve_font(SYSTEM_TITLE_FONTS[style] if j.system_fonts else STYLES[style]["title"])
        if tspec is None:
            warnings.append("none of the %s title fonts are installed; using Pillow's default font." % style)
    if label_font:
        lspec = resolve_font([label_font])
        if lspec is None:
            raise ValueError("label font not found: %s" % label_font)
    else:
        lspec = resolve_font(SYSTEM_LABEL_FONTS if j.system_fonts else LABEL_FONTS)
        if lspec is None:
            warnings.append("none of the label fonts are installed; using Pillow's default font.")
    tspec = canonical_weight(tspec, entry, warnings)
    lspec = canonical_weight(lspec, bundled_entry(label_font) if label_font else None, warnings)
    j.info["fonts"] = (tspec, lspec)
    j.entry, j.tracking_em, j.caps_ok, j.tspec, j.lspec = entry, tracking_em, caps_ok, tspec, lspec


def _pick_color(value, what, image, bg, notes):
    """white | auto | any color -> (rgb, description). auto lifts the image's signature color."""
    key = str(value).strip().lower() if value is not None else "white"
    if key in ("white", "#fff", "#ffffff"):
        return WHITE, "white"
    if key == "auto":
        sig = signature_color(image)
        if sig is None:
            notes.append("no strong signature color in the image, so %s stays white" % what)
            return WHITE, "white (auto: no signature color in the image)"
        c = lift_to_contrast(sig, bg)
        if c == sig:
            return c, "%s (auto: the image's own color)" % hexcolor(c)
        return c, "%s (auto: the image's %s, lightened for contrast)" % (hexcolor(c), hexcolor(sig))
    c = rgb(value)
    return c, hexcolor(c)


def _prepare_logo(logo, logo_white):
    """Orient, check and trim the logo; recolor it white on request."""
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
    return logo


def _assign_roles(j):
    """Which text is big and which goes in the box, their colors, and the title-source line."""
    lead, label, title, logo, logo_white, caps_ok = j.lead, j.label, j.title, j.logo, j.logo_white, j.caps_ok
    title_font, title_tracking, tracking_em, entry, tspec, style = j.title_font, j.title_tracking, j.tracking_em, j.entry, j.tspec, j.style
    info = j.info
    warnings = info["warnings"]
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
        big_rgb, big_desc = _pick_color(j.title_color, "the big type", j.backgrounds[0], j.bg, info["notes"])
    if box:
        accent_rgb, accent_desc = _pick_color(j.accent, "the label box", j.backgrounds[0], j.bg, info["notes"])
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
        first = STYLES[style]["title"][0]
        if tspec and not j.system_fonts and tspec == bundled_spec(first):
            # --title-font takes the catalog's tracking, so name the style's own when it differs: together they
            # are the flags that reproduce this look
            shown = "bundled %s" % first
            own = bundled_entry(first)
            if title_tracking is None and own and abs(own["tracking"] - tracking_em) > 1e-9:
                shown += ", tracking %.2f em" % tracking_em
        else:
            shown = os.path.basename(tspec.partition("@")[0]) if tspec else "Pillow default"
        font_source = "default %s font (%s)" % (style, shown)
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
    j.big, j.box, j.logo_big, j.big_name, j.big_rgb, j.accent_rgba = big, box, logo_big, big_name, big_rgb, accent_rgba


def _type_room(j):
    """Where the type may go, and how much width and height it gets."""
    cv, layout, panels, anchor, text_side, inset, narrow = j.cv, j.layout, j.panels, j.anchor, j.text_side, j.inset, j.narrow
    inset_box, src, focus_eff = j.inset_box, j.src, j.focus_eff
    bottom_limit = None
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
    j.safe, j.rects, j.div, j.max_w, j.base, j.avail, j.bottom_limit = safe, rects, div, max_w, base, avail, bottom_limit


def _fit_block(j, avail):
    """The type block at the largest size (from j.base down) whose height fits avail.
    Returns (items, width, height, big size, size cap used)."""
    cap = j.base
    while True:
        items, bw, bh, big_size = text_block(j.cv, j.big, j.box, j.kicker, j.subtitle, j.tspec, j.lspec,
                                             j.tracking_em, j.accent_rgba, j.logo, j.logo_big, j.max_w,
                                             stack=(j.lead == "label" and "·" in j.label), max_size=cap,
                                             big_name=j.big_name, big_color=j.big_rgb + (255,))
        if bh <= avail or cap <= 48:
            break
        cap = max(48, int(cap * 0.88))
    return items, bw, bh, big_size, cap


def tall_band(cv, safe, bh, anchor):
    """Type top and the shaded band around it on a tall single-image canvas."""
    top = safe[1] + cv.px(16) if anchor == "top" else safe[3] - bh
    return top, (top - cv.px(40), top + bh + cv.px(40))


BAND_MARGIN = 0.10  # how much better the top band must keep --subject-box in view before it moves there


def box_region(cv):
    """Where a --subject-box must sit to be seen: the Reels/Shorts safe zone on 9:16, else the canvas."""
    if cv.reels:  # what the Reels/Shorts header, buttons and caption leave visible
        return (0, cv.h * REELS_SAFE["top"], cv.w * (1 - REELS_SAFE["right"]), cv.h * (1 - REELS_SAFE["bottom"]))
    return None


def _band_measure(j, mapper, vis):
    """How a tall type band leaves --subject-box: (score, inside, visibility, heavy). inside is its share in
    view (Reels: the safe zone); visibility and heavy cover the whole box. The score is inside x the
    visibility of the box's middle rows (35-75% of its height: eyes to mouth on a face), so shade on hair
    and forehead counts for little."""
    rect = box_rect(j.subject_box, mapper)
    inside, v, heavy = box_cover(rect, j.cv, vis_y=vis, region=box_region(j.cv))
    h = rect[3] - rect[1]
    middle = box_cover((rect[0], rect[1] + 0.35 * h, rect[2], rect[1] + 0.75 * h), j.cv, vis_y=vis)[1]
    return inside * middle, inside, v, heavy


def _pick_anchor(j):
    """Tall canvases with --subject-box. For each band position, the vertical crop (when the capture is
    taller than the canvas) that keeps the box most in view; then the band moves to the top only when that
    keeps it clearly more in view than the bottom (bottom stays the default) and --band did not set it."""
    cv = j.cv
    if not (cv.tall and j.layout == "single" and j.subject_box is not None):
        return
    focus = j.focus_eff
    if focus is None:
        focus = subject_focus(j.src, (cv.w / max(cv.w / j.src.width, cv.h / j.src.height)) / j.src.width)
    default = j.vfocus  # the box center at 40% of the height, or None when nothing is cropped vertically
    grid = [default] if default is None else [default] + [i / 50.0 for i in range(51)]
    scores = {}
    for anchor in ("bottom", "top"):
        avail = j.safe[3] - j.safe[1] if anchor == "bottom" else int(min(j.safe[3] - j.safe[1], cv.h * 0.42))
        band = tall_band(cv, j.safe, _fit_block(j, avail)[2], anchor)[1]
        ms = [(vf,) + _band_measure(j, *tall_view(j.src, cv, focus, vf, j.shade, band, anchor)) for vf in grid]
        best = max(m[1] for m in ms)
        if ms[0][1] >= best - 0.02:  # the default crop is as good: keep it
            pick = ms[0]
        else:  # the best crop, nearest the default
            pick = min((m for m in ms if m[1] >= best - 0.005), key=lambda m: abs(m[0] - default))
        scores[anchor] = {"vfocus": pick[0], "score": pick[1], "inside": pick[2], "visibility": pick[3],
                          "heavy": pick[4], "avail": avail}
    j.band_scores = scores
    if not j.band_forced and j.anchor == "bottom" and scores["top"]["score"] >= scores["bottom"]["score"] + BAND_MARGIN:
        j.anchor, j.avail = "top", scores["top"]["avail"]
        j.info["text_side"] = "top"
        j.info["notes"].append("type band at the top, clear of --subject-box (%d%% of its middle in view there, "
                               "%d%% at the bottom)" % (round(100 * scores["top"]["score"]),
                                                        round(100 * scores["bottom"]["score"])))
    if scores[j.anchor]["vfocus"] != default:
        j.info["notes"].append("crop moved up or down to keep --subject-box clear of the type band (vertical focus "
                               "%.2f)" % scores[j.anchor]["vfocus"])
    j.vfocus = scores[j.anchor]["vfocus"]


def _fit_type(j):
    """Set the type block, shrinking it until it fits the height, and warn when it ends up small."""
    cv, big, logo, logo_big, big_name = j.cv, j.big, j.logo, j.logo_big, j.big_name
    base, info = j.base, j.info
    warnings = info["warnings"]
    items, bw, bh, big_size, cap = _fit_block(j, j.avail)
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
    j.items, j.bw, j.bh = items, bw, bh


def _place_band(j):
    """The type's top edge, and the band behind it that the composition shades."""
    cv, layout, rects, div, bh, safe, anchor = j.cv, j.layout, j.rects, j.div, j.bh, j.safe, j.anchor
    warnings = j.info["warnings"]
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
        top, band = tall_band(cv, safe, bh, anchor)
    elif layout == "panels":
        top = cv.h - cv.px(64) - bh
        band = (top - cv.px(24), cv.h)
        shaded = (cv.h - (band[0] - cv.px(70))) / cv.h  # where the shade is about half strength
        if shaded > 0.6:
            warnings.append("the type band shades %d%% of the panels: shorten the text." % (shaded * 100))
    else:
        top = int(cv.h * 0.5 - bh / 2)
        top = max(safe[1], min(top, j.bottom_limit - bh))
        band = None
    j.top, j.band = top, band


def _build(j, shade_now):
    """Compose the picture at one shade and draw the type over it; returns this build's state and warnings."""
    layout, panels, cv, bg, band, src, focus_eff, push = j.layout, j.panels, j.cv, j.bg, j.band, j.src, j.focus_eff, j.push
    anchor, subjects, text_side, inset, inset_box = j.anchor, j.subjects, j.text_side, j.inset, j.inset_box
    w = []  # warnings from this build only, so a rebuild never duplicates them
    out = {"warnings": w, "scales": None, "subjects": [], "inset": None, "inset_rect": None, "mask": None}
    mapper = None
    tex = None
    if layout == "panels":
        canvas, scales = compose_panels(panels, cv, bg, shade_now, top_shade=not cv.tall and bool(j.panel_labels),
                                        band_top=band[0], band=band)
        out["scales"], scale = scales, max(scales)
    elif cv.tall:
        canvas, scale, mapper = compose_tall(src, cv, bg, focus_eff, shade_now, push, band, anchor, j.vfocus)
        if subjects and push > 0:
            tex = compose_tall(src, cv, bg, focus_eff, shade_now, 0.0, band, anchor, j.vfocus)[0]
    else:
        fx = 0.5 if focus_eff is None else focus_eff
        canvas, scale, mapper = compose_single(src, cv, text_side, bg, fx, j.feather, shade_now, push, j.cropped)
        if subjects and push > 0:
            tex = compose_single(src, cv, text_side, bg, fx, j.feather, shade_now, 0.0, j.cropped)[0]
    if inset is not None:
        canvas, out["inset"], out["inset_rect"] = place_inset(canvas, inset, inset_box or (0.25, 0.25, 0.75, 0.75),
                                                              mapper, cv, w)
    if subjects:
        canvas, out["subjects"], out["mask"] = place_subjects(canvas, subjects, cv, text_side, w,
                                                              band if cv.tall else None, tex)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    align = "center" if layout == "panels" and not cv.tall else ("left" if cv.tall else text_side)
    bbox, elements = draw_block(layer, j.items, j.bw, j.bh, cv, align, j.top)
    label_elements, flagged = [], set()
    if layout == "panels":
        canvas, label_elements, flagged = place_panel_labels(layer, canvas, cv, panel_rects(cv, len(panels))[0],
                                                             j.panel_labels, j.lspec, band, bg, w)
    out.update(canvas=canvas, scale=scale, layer=layer, bbox=bbox, elements=elements, mapper=mapper, shade=shade_now,
               label_elements=label_elements, flagged=flagged,
               contrast=element_contrast(canvas, layer, elements + label_elements))
    return out


def _worst(b, names=None):
    return min((r for n, r, _, _ in b["contrast"] if names is None or n in names), default=99.0)


def _fixable(c):  # light type that fails only because of what is behind it
    _, r, light, best = c
    return light and r < MIN_CONTRAST and best >= MIN_CONTRAST * 1.05


def _compose(j):
    """Build once; when light type fails only on a bright patch, rebuild darker and keep the better one."""
    shade, info = j.shade, j.info
    warnings = info["warnings"]
    built = _build(j, shade)
    targets = {c[0] for c in built["contrast"] if _fixable(c)}
    if shade < 0.97 and targets:
        darker = _build(j, 0.97)  # light type on a bright patch: darken the type area once more
        # judged only on the elements darkening is meant to fix; a dark --accent gets its own warning
        if _worst(darker, targets) > _worst(built, targets) + 0.05:
            built = darker
            info["notes"].append("raised --shade to 0.97 for contrast")
    warnings.extend(built["warnings"])
    info["subjects"] = built["subjects"]
    if built["inset"] is not None:
        info["inset"] = built["inset"]
    if built["inset_rect"] is not None:
        info["inset_rect"] = built["inset_rect"]
    j.built = built


def _under_type(rect, boxes, cv):
    """Share of rect (clipped to the canvas) that the type boxes cover, summed over the boxes."""
    x0, y0 = int(round(max(0, rect[0]))), int(round(max(0, rect[1])))
    x1, y1 = int(round(min(cv.w, rect[2]))), int(round(min(cv.h, rect[3])))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    under = 0.0
    for b in boxes:
        ow = max(0, min(x1, b[2]) - max(x0, b[0]))
        oh = max(0, min(y1, b[3]) - max(y0, b[1]))
        under += ow * oh / float((x1 - x0) * (y1 - y0))
    return under


def _wide_side(j, side, focus, shade):
    """--subject-box with the 16:9 type on `side` and the crop at `focus`, without rendering: (inside, visibility,
    heavy, share under the type, type over the inset?). The drawn type is mirrored for the other side."""
    cv = j.cv
    mapper, vis = wide_view(j.src, cv, side, focus, j.feather, shade, j.cropped)
    rect = box_rect(j.subject_box, mapper)
    inside, v, heavy = box_cover(rect, cv, vis_x=vis)
    boxes = [b if side == j.text_side else (cv.w - b[2], b[1], cv.w - b[0], b[3]) for _, b in j.built["elements"]]
    on_inset = False
    if j.inset is not None:
        ir = box_rect(j.inset_box or (0.25, 0.25, 0.75, 0.75), mapper)
        on_inset = any(overlaps(b, ir) for b in boxes)
    return inside, v, heavy, min(1.0, _under_type(rect, boxes, cv)), on_inset


def _clears(m):
    """A _wide_side measurement with no --subject-box warning: in frame, not shaded, not under the type."""
    return m[0] >= 0.9 and m[1] >= 0.7 and m[2] <= 0.2 and m[3] <= 0.10 and not m[4]


def _crop_advice(j, sides, keep):
    """suggest_crop for this capture: the bars of the whole capture (the re-run trims them whatever the
    crop), the height the re-run keeps, and each hit confirmed against background_box."""
    W, H = j.given_size
    tb = trim_box(j.src_given)
    y = (j.crop[1], j.crop[3]) if j.crop is not None else (0.0, 1.0)
    kept_h = int(round(min(tb[3], y[1] * H))) - int(round(max(tb[1], y[0] * H)))

    def exact(crop):
        try:
            return background_box(j.src_given, crop, tb)
        except ValueError:
            return None
    return suggest_crop(j.subject_box_given, j.given_size, j.cv, sides, j.feather, j.shade, bars=(tb[0], tb[2]),
                        kept_h=kept_h, y=y, keep=keep, exact=exact)


def _focus_fix(j):
    """On a capture wider than 16:9 with --focus set: the advice to drop it (or the --focus value) when the crop
    that would keep --subject-box clear of the type clears it, on this side or the other; else None."""
    cv, src = j.cv, j.src
    if j.focus is None or j.narrow or src.width / src.height <= (cv.w / cv.h) * 1.1:
        return None
    other = "right" if j.text_side == "left" else "left"
    for side in (j.text_side, other):
        f = focus_away(j.subject_box, src, cv, side)
        if _clears(_wide_side(j, side, f, j.built["shade"])):
            return "%sdrop --focus (or set --focus %.2f)" % ("" if side == j.text_side else
                                                            "use --text-side %s and " % side, f)
    return None


def _wide_fix(j, shade_bad, overlap_bad):
    """One fix for the 16:9 --subject-box warnings, simplest first: on a capture wider than 16:9 with --focus
    set, the crop position that clears the box; a --crop that keeps the chosen side; the other side when it
    clears the box on its own; a --crop with the other side; the other side when it measurably helps."""
    cv, src, box = j.cv, j.src, j.subject_box
    other = "right" if j.text_side == "left" else "left"
    shade = j.built["shade"]
    wider = not j.narrow and src.width / src.height > (cv.w / cv.h) * 1.1
    fix = _focus_fix(j)  # --focus overrides the crop that would put the box clear of the type
    if fix:
        return fix
    keep = []
    if j.inset is not None:  # a suggested crop keeps the whole inset: its box in fractions of the capture as given
        ib, (kx0, ky0, kx1, ky1) = j.inset_box or (0.25, 0.25, 0.75, 0.75), j.kept_px
        W, H = j.given_size
        keep = [((kx0 + ib[0] * (kx1 - kx0)) / W, (ky0 + ib[1] * (ky1 - ky0)) / H,
                 (kx0 + ib[2] * (kx1 - kx0)) / W, (ky0 + ib[3] * (ky1 - ky0)) / H)]

    def crop_fix(sug):
        (a0, b0, a1, b1), side = sug
        return "try --crop %s --text-side %s" % (
            ",".join(("%.2f" % v).rstrip("0").rstrip(".") for v in (a0, b0, a1, b1)), side)
    sug = _crop_advice(j, (j.text_side,), keep)
    if sug:
        return crop_fix(sug)
    if j.focus is not None:
        focus_other = j.focus
    elif j.inset is not None or not wider:
        focus_other = 0.5 if j.focus_eff is None else j.focus_eff
    else:
        focus_other = focus_away(box, src, cv, other)
    om = _wide_side(j, other, focus_other, shade)
    if _clears(om):
        return "use --text-side %s" % other
    sug = _crop_advice(j, (other,), keep)
    if sug:
        return crop_fix(sug)
    cm = _wide_side(j, j.text_side, 0.5 if j.focus_eff is None else j.focus_eff, shade)

    def score(m):
        return 0.0 if m[4] else m[0] * m[1] * (1 - m[3])
    if score(om) >= score(cm) + 0.10:
        return "use --text-side %s, which shows more of it, or shorten the text" % other
    if not shade_bad:
        return "shorten the text"
    what = ("no --crop keeps both the inset and --subject-box clear of the type" if keep
            else "no --crop clears it beside the type column")
    return "%s; %suse another frame" % (what, "shorten the text or " if overlap_bad else "")


def _check_subject_box(j):
    """Where --subject-box landed in the finished picture: in frame (Reels: the safe zone), under the
    shade, under the type. Each problem gets a warning that names its fix (one fix when the shade and the
    type both hit it): 16:9 tries --focus, a computed --crop or the other side; 4:5 and 9:16 the other
    --band. Measurements go in info["subject_box"]."""
    if j.subject_box is None:
        return
    cv, built, info = j.cv, j.built, j.info
    warnings = info["warnings"]
    rect = box_rect(j.subject_box, built["mapper"])
    if cv.tall:
        vis_x, vis_y = None, 1 - band_alpha(cv, built["shade"], j.band, j.anchor)
    else:
        fx = 0.5 if j.focus_eff is None else j.focus_eff
        vis_x, vis_y = wide_view(j.src, cv, j.text_side, fx, j.feather, built["shade"], j.cropped)[1], None
    region = box_region(cv)
    inside, vis, heavy = box_cover(rect, cv, vis_x, vis_y, region)
    x0, y0 = int(round(max(0, rect[0]))), int(round(max(0, rect[1])))
    x1, y1 = int(round(min(cv.w, rect[2]))), int(round(min(cv.h, rect[3])))
    glyphs = 0.0
    if x1 > x0 and y1 > y0:
        glyphs = float((np.asarray(built["layer"].getchannel("A"))[y0:y1, x0:x1] > 128).mean())
    under_type = _under_type(rect, [b for _, b in built["elements"]], cv)
    info["subject_box"] = {"box": tuple(round(v, 4) for v in j.subject_box),
                           "rect": tuple(int(round(v)) for v in rect), "inside": round(inside, 3),
                           "visibility": round(vis, 3), "heavy": round(heavy, 3), "glyphs": round(glyphs, 3),
                           "under_type": round(min(1.0, under_type), 3)}
    if inside < 0.9:
        where = ("inside the Reels/Shorts safe zone (clear of the top 14%, bottom 35%, right 15%)" if cv.reels
                 else "in the %s frame" % j.aspect)
        rg = region or (0, 0, cv.w, cv.h)
        across = max(0.0, min(rect[2], rg[2]) - max(rect[0], rg[0])) / (rect[2] - rect[0])
        down = max(0.0, min(rect[3], rg[3]) - max(rect[1], rg[1])) / (rect[3] - rect[1])
        if down < across and cv.tall:  # cut at the top or bottom: --focus only moves the crop sideways
            fix = ("it sits too %s in the capture for this format; use a frame with more room %s the subject"
                   % (("high", "above") if rect[1] < rg[1] else ("low", "below")))
        elif cv.tall:
            fix = "move the crop with --focus (0 = left edge, 1 = right edge) or tighten --crop around it"
        else:
            fix = _focus_fix(j) or "check --focus and --crop"
        warnings.append("only %d%% of --subject-box is %s: %s." % (int(inside * 100), where, fix))
    shade_bad = x1 > x0 and y1 > y0 and (vis < 0.7 or heavy > 0.2)
    overlap_bad = glyphs > 0.02 or under_type > 0.10
    if not (shade_bad or overlap_bad):
        return
    if cv.tall:
        flip = "top" if j.anchor == "bottom" else "bottom"
        m, here = (j.band_scores or {}).get(flip), (j.band_scores or {}).get(j.anchor)
        clears = m and m["inside"] >= 0.9 and m["visibility"] >= 0.7 and m["heavy"] <= 0.2
        if clears or (m and here and m["score"] >= here["score"] + BAND_MARGIN):  # the other band does better
            shade_fix = overlap_fix = "use --band %s, which keeps %s, or another frame" % (
                flip, "it clear" if clears else "more of it in view")
        else:
            shade_fix = "shorten the text, or --crop the capture so the subject sits away from the band"
            overlap_fix = "shorten the text, or move the subject with --focus or --crop"
    else:
        shade_fix = overlap_fix = _wide_fix(j, shade_bad, overlap_bad)
    if shade_bad:
        warnings.append("the type %s darkens --subject-box on %s (%d%% under heavy shade, %d%% visible): %s." % (
            "band" if cv.tall else "shade", j.aspect, round(100 * heavy), round(100 * vis), shade_fix))
    if overlap_bad:
        warnings.append("the type overlaps --subject-box on %s: %s." % (j.aspect, overlap_fix))


def _check_result(j):
    """Measure the finished picture: upscaling, contrast per element, frame, overlaps and word count."""
    built, info, layout, panels, cv, src, text_side, narrow = j.built, j.info, j.layout, j.panels, j.cv, j.src, j.text_side, j.narrow
    logo, title, kicker, subtitle, label, panel_labels = j.logo, j.title, j.kicker, j.subtitle, j.label, j.panel_labels
    warnings = info["warnings"]
    canvas, layer, bbox = built["canvas"], built["layer"], built["bbox"]
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
    info["contrast"] = round(_worst(built), 2)
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
    # (name, (x0, y0, x1, y1)) per drawn line or box: "title" or "hook" (one per line), "label box",
    # "kicker", "subtitle", "logo", "panel label N"
    info["elements"] = [(name, tuple(int(v) for v in b)) for name, b in built["elements"] + built["label_elements"]]

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
    _check_subject_box(j)
    info["warnings"] = list(dict.fromkeys(warnings))  # drop exact repeats, keep order


def render(src=None, title="", label="", kicker="", subtitle="", lead="title", style="serif",
           text_side="auto", focus=None, accent="#ffffff", bg=None, feather=0.36, shade=0.88,
           title_font=None, label_font=None, logo=None, logo_white=False, size="auto",
           panels=None, panel_labels=None, subjects=None, push=None, inset=None, inset_box=None,
           aspect="16:9", title_color="white", crop=None, title_tracking=None, subject_box=None,
           system_fonts=False, band="auto"):
    """Return (thumbnail at master size, info dict with warnings and measurements).

    lead="title": the title is the big type and the label goes in the box.
    lead="label": the label is the big type (split at " · ") and the title goes in the box.
    aspect "16:9" puts the type beside the image; "4:5" and "9:16" put it in a band below the
    image (above it when there is a subject), inside the Reels/Shorts safe zone for 9:16.
    crop (x0, y0, x1, y1 fractions of the capture) keeps only that part of it; a crop narrower
    than 16:9 puts the art on the far side, fading into the dark side, which makes room for the
    type when the subject sits in the middle of the frame.
    subject_box (x0, y0, x1, y1 fractions of the capture as given: a face or a product label) is kept clear
    of the type: 16:9 picks the text side for it, 4:5 and 9:16 center the crop on it and pick the band; it
    warns when the box ends up out of frame, under heavy shade or under the type (info["subject_box"]).
    The default fonts are bundled, so the type is the same on every machine; system_fonts=True tries the
    installed faces first (Bodoni MT, Bahnschrift, Didot...), as up to v0.7.0. title_font and label_font
    always win. band ("auto", "top", "bottom") sets where the type band goes on 4:5 and 9:16 (a single
    capture without a cut-out); auto keeps it at the bottom unless the top keeps subject_box clearly more in view."""
    j = Job(src=src, title=title, label=label, kicker=kicker, subtitle=subtitle, lead=lead, style=style,
            text_side=text_side, focus=focus, accent=accent, bg=bg, feather=feather, shade=shade,
            title_font=title_font, label_font=label_font, logo=logo, logo_white=logo_white, size=size,
            panels=panels, panel_labels=panel_labels, subjects=subjects, push=push, inset=inset,
            inset_box=inset_box, aspect=aspect, title_color=title_color, crop=crop, title_tracking=title_tracking,
            subject_box=subject_box, system_fonts=system_fonts, band_pos=band,
            info={"warnings": [], "notes": [], "lead": lead, "aspect": aspect})
    _check_args(j)
    _load_sources(j)
    _choose_layout(j)
    _pick_fonts(j)
    j.logo = _prepare_logo(j.logo, j.logo_white)
    _assign_roles(j)
    _type_room(j)
    _pick_anchor(j)
    _fit_type(j)
    _place_band(j)
    _compose(j)
    _check_result(j)
    out = j.built["canvas"].convert("RGBA")
    out.alpha_composite(j.built["layer"])
    return out.convert("RGB"), j.info


# ---------------------------------------------------------------- previews and export

def corner_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def preview_font(size):
    """Captions and the duration badge on the preview sheets: always the bundled label font, so the
    sheets look the same on every machine, with or without --system-fonts."""
    return load_font(resolve_font(LABEL_FONTS), size)


PREVIEW_SUPERSAMPLE = 3  # small preview text is drawn this much larger and scaled down: even spacing at 12-13 px
PREVIEW_TRACKING = 0.03  # em, so words and figures of the small text don't run together
PREVIEW_WORD_SPACE = 0.3  # em, minimum word space: Outfit's own is 0.19 em, and without raqm no kerning helps
# YouTube's duration badge as the previews draw it: fixed px whatever the tile size. Canvas.timestamp_zone
# is this badge on the smallest tile type stays clear of (the 168 px "up next" tile).
BADGE = {"text": "24:13", "size": 12, "pad": 4, "height": 17, "inset": 4, "radius": 4, "tile": 168}


def tile_height(w):
    """Height of a 16:9 preview tile w px wide."""
    return int(round(w * 9 / 16))


def preview_text_mask(text, size):
    """(mask, x offset, baseline row) of small preview text, drawn at PREVIEW_SUPERSAMPLE times the size with
    PREVIEW_TRACKING and scaled down with LANCZOS."""
    k = PREVIEW_SUPERSAMPLE
    f = preview_font(size * k)
    trk, gap = PREVIEW_TRACKING * size * k, PREVIEW_WORD_SPACE * size * k
    asc, desc = f.getmetrics() if hasattr(f, "getmetrics") else (size * k, size * k // 3)
    top = (-asc) % k + k  # the baseline lands on a whole pixel after scaling down
    w = int(text_width(f, text, trk, gap)) + 4 * k
    h = top + asc + desc + k
    w, h = w + (-w) % k, h + (-h) % k
    m = Image.new("L", (w, h), 0)
    draw_text(ImageDraw.Draw(m), 2 * k, top + asc, text, f, trk, 255, gap)
    return m.resize((w // k, h // k), Image.LANCZOS), 2, (top + asc) // k


def preview_text_width(text, size):
    """Advance width of preview text at 1x, as preview_text draws it."""
    k = PREVIEW_SUPERSAMPLE
    return text_width(preview_font(size * k), text, PREVIEW_TRACKING * size * k, PREVIEW_WORD_SPACE * size * k) / k


def preview_text(img, x, baseline, text, size, fill):
    """Draw small preview text with its left end at x and its baseline at `baseline` (like anchor "ls")."""
    m, dx, base = preview_text_mask(text, size)
    img.paste(Image.new("RGB", m.size, tuple(fill[:3])), (int(round(x)) - dx, int(round(baseline)) - base), m)


def badge_rect(w, h, text=None):
    """Where the duration badge sits on a w x h tile: (x0, y0, x1, y1) in px."""
    b = BADGE
    tw = preview_text_width(text or b["text"], b["size"])
    x1, y1 = w - b["inset"], h - b["inset"]
    return (x1 - tw - 2 * b["pad"], y1 - b["height"], x1, y1)


def badge(tile, text=None):
    """YouTube's duration badge: fixed ~12 px type whatever the thumbnail size (see BADGE)."""
    b = BADGE
    text = text or b["text"]
    x0, y0, x1, y1 = badge_rect(tile.width, tile.height, text)
    ImageDraw.Draw(tile).rounded_rectangle((x0, y0, x1, y1), radius=b["radius"], fill=(0, 0, 0))
    preview_text(tile, x0 + b["pad"], y1 - b["pad"], text, b["size"], (255, 255, 255))
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
    for r, (bg, fg) in enumerate((((15, 15, 15), (241, 241, 241)), ((255, 255, 255), (15, 15, 15)))):
        y0 = r * row_h + (pad // 2 if r else 0)
        ImageDraw.Draw(sheet).rectangle((0, y0 - (pad // 2 if r else 0), width, y0 + row_h), fill=bg)
        x = pad
        for w, name, kind in sizes:
            h = tile_height(w)
            tile = thumb.resize((w, h), Image.LANCZOS)
            if kind == "badge":
                tile = badge(tile)
            elif kind == "squint":
                tile = squint(tile)
            sheet.paste(tile, (x, y0 + pad // 2), corner_mask(tile.size, max(4, int(w * 0.03))))
            preview_text(sheet, x, y0 + pad // 2 + h + 18, "%d px  %s" % (w, name), 13, fg)
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
            preview_text(sheet, x, y0 + pad // 2 + card_h(w) + 20, "%d px  %s" % (w, name), 13, fg)
            x += w + gap
        sq = squint(thumb.resize((360, 450), Image.LANCZOS))
        sheet.paste(sq, (x, y0 + pad // 2 + 56))
        preview_text(sheet, x, y0 + pad // 2 + 56 + 450 + 20, "360 px  squint test", 13, fg)
    return sheet


def reels_preview(thumb):
    """9:16 under a Reels/Shorts-style overlay (header, button column, caption), the 3:4 profile-grid
    crop, and a squint, in dark and light."""
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
        preview_text(sheet, x, y0 + pad // 2 + ph + 22, "%d px  reels / shorts overlay" % pw, 13, fg)
        x += pw + gap
        crop_h = int(thumb.width * 4 / 3)
        grid = thumb.crop((0, (thumb.height - crop_h) // 2, thumb.width, (thumb.height + crop_h) // 2))
        sheet.paste(grid.resize((160, 213), Image.LANCZOS), (x, y0 + pad // 2))
        preview_text(sheet, x, y0 + pad // 2 + 213 + 22, "160 px  profile grid", 13, fg)
        x += 160 + gap
        sheet.paste(squint(thumb.resize((pw, ph), Image.LANCZOS)), (x, y0 + pad // 2), corner_mask((pw, ph), 18))
        preview_text(sheet, x, y0 + pad // 2 + ph + 22, "%d px  squint test" % pw, 13, fg)
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
                kicker="", aspect="16:9", title_color="white", title_tracking=None, system_fonts=False, band="auto")
PATH_KEYS = ("title_font", "label_font", "logo")
CHOICES = {"style": sorted(STYLES), "text_side": ["auto", "left", "right"], "lead": ["title", "label", "ab"],
           "band": list(BANDS),
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
    elif key in ("logo_white", "system_fonts"):
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


def parse_subject_box(text):
    return parse_box(text, "--subject-box", "the capture")


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
    ap.add_argument("--subject-box", type=parse_subject_box,
                    help="x0,y0,x1,y1 of a person's face or a product label inside the capture, fractions 0 to 1 "
                    "of the image as given: 16:9 puts the type on the side that keeps it clear, 4:5 and 9:16 center "
                    "the crop on it, and every format warns if it is cut off, shaded or under the type. Box the face "
                    "from the brows to the chin (not the hair or the whole body), or the label. Not for --subject "
                    "cut-outs or --panel images")
    ap.add_argument("--style", help="serif or sans")
    ap.add_argument("--text-side", help="auto, left or right (16:9)")
    ap.add_argument("--band", help="auto, top or bottom: where the type band goes on 4:5 and 9:16 (default auto: "
                    "the bottom, or the top when that keeps --subject-box clearly more in view)")
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
    ap.add_argument("--system-fonts", action="store_true", default=None,
                    help="default title and label fonts from this machine (Bodoni MT, Bahnschrift, Didot...) "
                    "instead of the bundled Playfair and Outfit; the type then differs between machines")
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
    if args.subject_box and (args.panel or args.subject):
        ap.error("--subject-box marks a face or product inside the background capture; it can't be used with "
                 "--panel or --subject (a cut-out is placed by the script and checked on its own)")
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
                      system_fonts=bool(s["system_fonts"]), band=s["band"],
                      size=s["size"], panels=[open_image(p, "panel") for p in args.panel or []] or None,
                      panel_labels=args.panel_label,
                      subjects=[open_image(p, "subject") for p in args.subject or []] or None,
                      push=s["push"], inset=open_image(args.inset, "inset") if args.inset else None,
                      inset_box=args.inset_box, crop=args.crop, subject_box=args.subject_box)
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
        sb = info.get("subject_box")
        if sb:
            print("subject box: %d%% %s, %d%% visible, %d%% under heavy shade, %d%% under type" % (
                round(100 * sb["inside"]), "in the safe zone" if aspect == "9:16" else "in frame",
                round(100 * sb["visibility"]), round(100 * sb["heavy"]), round(100 * max(sb["glyphs"], sb["under_type"]))))
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
    print("fonts: title %s; label %s%s" % (info["fonts"] + (" (system fonts)" if s["system_fonts"] else "",)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
