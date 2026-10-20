# Flags, recipes and what the script prints

Everything `scripts/thumb.py` takes, grouped by what it does. `python <skill-dir>/scripts/thumb.py
--help` prints the same flags with one-line help. Fractions in `--crop`, `--subject-box`,
`--inset-box` are of the image as given (before bars are trimmed), `x0,y0,x1,y1` from the top left.

## Contents

- [Output and formats](#output-and-formats)
- [Text](#text)
- [Look: fonts, color, shade, backdrop](#look)
- [Image roles and layout](#roles)
- [Post mode](#post-mode)
- [Inputs, bars and color](#inputs)
- [Channel file](#channel-file)
- [Recipes: mark the subject, vertical frames, subject in the middle](#recipes)
- [Fixed specs](#fixed-specs)
- [Notes and warnings, and the fix for each](#notes-and-warnings)

<a id="output-and-formats"></a>
## Output and formats

| Flag | What it does |
|---|---|
| `-o PATH` / `--output PATH` | Output stem, `.jpg` name or folder (default `<capture>_thumb`). Any image extension on it is replaced: masters and copies are always `.jpg`, previews `.png`. Variants and formats add `_A`, `_B`, `_4x5`, `_9x16`, so a second run with the same `-o` for other formats keeps the first run's files. An `-o` that is the same file as an input (hard link, short name, junction) stops with an error. Files are written through a temp file, so an interrupted run leaves nothing half-written. |
| `--aspect 16:9,4:5,9:16` | Formats to render: `16:9` (YouTube, default), `4:5` (Facebook and Instagram feed, 1080x1350), `9:16` (Reels, Shorts, Stories, 1080x1920), a comma list, or `all`. A CLI value replaces the saved `aspect` for that run; it does not add to it. |
| `--lead title\|label\|ab` | Which slot is big. `ab` (default) renders A (title big) and B (label big). Post mode renders one image. |
| `--size auto\|720p\|1080p\|1440p\|4k` | Force the 16:9 master size. `auto` is the largest standard size the images support without upscaling, up to 3840x2160. |
| `--feed DIR` | Place both variants in a grid with other thumbnails from a local folder (`_feed.png`). |
| `--channel FILE` | Channel settings JSON, see [Channel file](#channel-file). |

Each 16:9 variant writes a master `.jpg`, a `_phone.jpg` (1280x720, under 2 MB) and a
`_preview.png`. Each vertical file gets a `_1080.jpg` copy when the master is larger. A 16:9 post
writes `_1280.jpg` instead of `_phone.jpg`, with a Facebook-style feed card as preview. The 16:9
preview shows feed sizes with the duration badge, the 88 px notification and a squint test; the 4:5
preview is a Facebook feed card and the 9:16 preview a Reels overlay with the profile-grid crop. When
`--subject-box` or `--inset-box` is given, the last tile outlines the box (cyan, amber); it also outlines a
face the script found and warns about (magenta). Piped
output is UTF-8, so printed paths are the real file names.

<a id="text"></a>
## Text

| Flag | What it does |
|---|---|
| `--title TEXT` | The subject, the big type in variant A. `\n` splits lines. |
| `--label TEXT` | The hook, in the boxed label (big in variant B). A `\n` in it splits the hook like ` · `. Newlines in the box, kicker, subtitle and panel labels are shown as spaces. |
| `--kicker TEXT` / `--subtitle TEXT` | A small line above or below the big type. Off unless given; see the [type rules](type-rules.md). |
| `--panel-label TEXT` | One per `--panel`, in the same order. |

The label box, kicker and subtitle keep the case you write when their font has no capitals-only
design (`gothic`, `brush`); other fonts set them in capitals.

<a id="look"></a>
## Look: fonts, color, shade, backdrop

| Flag | What it does |
|---|---|
| `--style serif\|sans` | The default title look when no logo or matched font is given: bundled Playfair Display Bold or Outfit Bold. |
| `--title-font NAME\|FILE` / `--label-font NAME\|FILE` | A bundled font name (see `--list-fonts`) or a font file; optional `@Weight` (`outfit@Light`). See [fonts.md](fonts.md). |
| `--list-fonts` | Print the bundled fonts with their real family names, looks and the titles they fit. |
| `--title-tracking 0..0.5` | Letter spacing of the big type in em. 0.1 to 0.2 for a thin, spaced-out logo. |
| `--title-color white\|auto\|#hex` | Big type color. `auto` takes the image's signature color and lightens it until it passes the contrast check; it stays white when the image has none. |
| `--accent white\|auto\|#hex` | Label box color. Color the big type or the box, not both. |
| `--logo logo.png` | A transparent PNG replaces the title text. A `--title` given with it is not drawn (the script says so). |
| `--logo-white` / `--no-logo-white` | Recolor the logo white / keep its own colors, whatever the channel file says. |
| `--system-fonts` / `--no-system-fonts` | Use installed faces (Bodoni MT, Bahnschrift, Didot) for the default title and label / use the bundled fonts, whatever the channel file says. The look with system fonts differs by machine. |
| `--shade 0..1` | Fixes the darkness of the type side. Without it the shade is **automatic**: the lightest value from 0.35 to 0.88 at which every element (title lines, label box, kicker, subtitle, logo, panel labels) reaches 7:1 contrast, or what 0.88 gives it when that is less. A short title shades less of the picture. Each element is scored at its worst letter-sized slice, so one letter on a bright patch counts. A number here, or `"shade"` in the channel file, keeps the old fixed amount and geometry. |
| `--bg #hex` | Color of the type side and of a solid panel (default: the image's shadows, lifted to at least 1.2:1 against YouTube's dark page `#0f0f0f`, so a neutral becomes charcoal `#222222`). A `--bg` darker than that is kept, with a note if the edge then merges with the page. |
| `--feather 0..1` | Fade length where narrow or cropped art meets the type side, as a share of the art's width (default 0.36). Shorten it when the fade reaches a face. |
| `--backdrop fade\|solid` | `fade` (default): the photo runs under the type, shaded toward it. `solid`: the photo keeps its own rectangle and the type sits on an opaque `--bg` panel with a hard edge. `--shade` and `--feather` do nothing with `solid`; panels ignore it. |

<a id="roles"></a>
## Image roles and layout

| Flag | What it does |
|---|---|
| `--panel IMG` (2 or 3) | Comparison images instead of a capture; stacked on 4:5 and 9:16 with the type on the seam. |
| `--subject cutout.png` (repeatable) | A transparent cut-out in front of the background, matched to it. `--push 0..1` softens the background behind it (default 0.5). |
| `--inset IMG --inset-box x0,y0,x1,y1` | An image shown inside the background. Estimate the box by looking at the background. |
| `--crop x0,y0,x1,y1` | Keep part of the capture. Narrower than 16:9, the art moves to the far side and fades into the type side. Applies to every format. |
| `--focus 0..1` | Horizontal crop position on a capture wider than the format (default: the center on 16:9, the subject on 4:5 and 9:16). Ignored, with a note, when the art is narrower than 16:9. |
| `--text-side left\|right` | Override the automatically chosen side (16:9 only; on 4:5 and 9:16 it is ignored with a note). |
| `--band auto\|top\|bottom` | Where the type band goes on 4:5 and 9:16. `auto` keeps the bottom, or takes the top when that keeps `--subject-box` or `--inset` clearer. |
| `--subject-box x0,y0,x1,y1` | Mark a face (brows to chin) or a product label. See [Recipes](#recipes). Not with `--panel` or `--subject`. |
| `--no-face-check` | Skip the face check. Without `--subject-box`, on a single capture without `--subject`, the script looks for a face and warns when the type, its shade or a crop covers or cuts it. The check only warns and outlines; it never moves the type or the crop. |

Vertical formats crop to the busiest part of the image unless `--focus` or a box says otherwise.
The type goes in a band below the image (above it when there is a subject) or on the seam
between stacked panels.

<a id="post-mode"></a>
## Post mode

`--mode post` renders one 4:5 image by default (`--aspect` or `--lead` changes that).

| Flag | What it does |
|---|---|
| `--title TEXT` | The headline. Not drawn when `--logo` is given. |
| `--body TEXT` | The message, word for word, wrapped to the type column in the label font. `\n` breaks a line, `\n\n` starts a paragraph. A typed `\n` inside a Windows path (`C:\new`) stays text. |
| `--details TEXT` | When, where, price, how to act; smaller than the body. Lines break only at ` · ` with spaces around it. |
| `--label TEXT` | Optional short tag. |

A word wider than the column (a link, an email) breaks after `/ - _ ? & =` or before a dot, with no
hyphen added. The script checks the body and details at the width of a phone feed (360 px; 300 px
for 9:16, where the Reels screen is narrower) and warns below 8 px cap height. After `post text:`
it prints one line per slot (`  body: line | line`); ` |+ ` marks a break inside a word and
`[does not fit]` marks a line past the column or off the frame. Show the user these lines.

With `--backdrop solid` on 9:16, a long message that would leave the photo under 20% of the frame
moves the type band to the top and the photo fills the frame below it (`--band bottom` keeps it
down).

<a id="inputs"></a>
## Inputs, bars and color

- **Formats read:** PNG, JPEG, WebP, BMP, GIF, TIFF, and AVIF when Pillow reads it. **JPEG XR**
  (`.jxr`, sometimes named `.jpg`) is what the NVIDIA app and Xbox Game Bar save when HDR is on; the
  script cannot read it. Export an SDR PNG or JPEG, or capture with HDR off. **HEIC/HEIF** opens
  when `python -m pip install pillow-heif` is done; otherwise export as JPEG. Inputs up to 400 MP
  are accepted.
- **Black bars:** letterbox and pillarbox bars (video black, thin 2.0 bars, bars carrying one or two
  subtitle lines) are trimmed, and every trim prints `trimmed black bars: kept x..., y... of WxH`.
  Dark scene edges such as a night sky are kept, and a logo or product on a flat black field is never trimmed. `--no-trim` keeps the capture and the
  panels whole (an explicit `--crop 0,0,1,1` does too); `--inset` images are still trimmed.
- **Color profiles:** an embedded ICC profile (Display P3 phone photos, Adobe RGB, CMYK) is converted
  to sRGB on input for captures, panels, insets, cut-outs and logos. Outputs are untagged sRGB JPEGs.

<a id="channel-file"></a>
## Channel file

`clean-thumbnail.json` holds settings a channel keeps on every video. The script reads
`./clean-thumbnail.json` from the **working folder** only; keep the file next to the user's work,
or keep it in a fixed place and pass `--channel PATH` every time. Flags override it; `--no-logo-white`
and `--no-system-fonts` override a saved `true`. Relative font and logo paths resolve next to the
file. Unknown keys stop the run and the error lists the allowed ones:

`style`, `title_font`, `label_font`, `title_tracking`, `title_color`, `accent`, `logo`,
`logo_white`, `system_fonts`, `lead`, `aspect`, `size`, `shade`, `bg`, `feather`, `push`,
`focus`, `text_side`, `band`, `backdrop`, `kicker`, `mode`.

Save only what the user wants on every video. A saved `shade` turns off the automatic shade. Post
mode ignores a saved `aspect` and `lead` (it prints a note when it does).

<a id="recipes"></a>
## Recipes

**Mark the subject.** When the capture shows a person, character or product, look at it and pass
`--subject-box` around the face from the brows to the chin (not the hair, not the whole body), or
around the product's name or label. Type crossing a torso is fine; type or shade on a face is not.
The script then puts the 16:9 type on the side that keeps the box clear, centers the 4:5 and 9:16
crops on it, moves the 9:16 band to the top when that shades the face less, and warns when the box
still ends up under type or shade or out of frame. Each variant prints a `subject box:` line (in
frame, visible, under shade, under type), and the preview gets a last tile that outlines the box
(cyan; an inset box is amber). Open that tile and check the box holds the face before you trust the
line.

**No box given.** The script then looks for a face itself (OpenCV's frontal face cascade, run in NumPy;
it finds faces turned toward the camera, not backs of heads, helmets or profiles). It moves nothing: a face
the type, its shade or a crop would cover or cut gets a warning that names the `--subject-box` to pass,
and the preview's last tile outlines it in magenta. If the outline holds a face, re-run with that box. A
face that stays clear gets a note with its box.

**A different picture for 4:5 and 9:16** is often better. The 9:16 type sits mid-frame (safe zone), so
it needs a frame with the face in the top third and something calm below. With a centered face,
`--subject-box` moves the band to the top when that clears it and warns when it can't; without a box the
band stays at the bottom and shades it, and the face check warns when it finds the face there. When the
wide capture does not suit, ask for (or pick from the user's captures) one that does and render it in a
second run with `--aspect 4:5,9:16` and the same `-o`: vertical files get `_4x5` and `_9x16` suffixes,
so the 16:9 files stay.

**Subject in the middle.** In 16:9 the type and its shade then land on the subject whichever side
they take, and `--focus` cannot help. With `--subject-box`, the warning prints a crop that clears
it, for example `try --crop 0.11,0,0.96,1 --text-side right`; re-run with it and the same box
(both are fractions of the image as given). Fixes named in warnings are confirmed by re-rendering,
so use them as printed. By hand: keep 75 to 80% of the width, with the subject toward the side away
from the type. A crop can also keep a second element in view that the box does not know about, such
as a phone on a gimbal.

<a id="fixed-specs"></a>
## Fixed specs

Master up to 3840x2160 (YouTube accepts up to 50 MB from desktop); phone copy 1280x720 under 2 MB.
4:5 master up to 2160x2700 with a 1080x1350 copy; 9:16 up to 2160x3840 with a 1080x1920 copy.

- Type stays out of the bottom-right corner, where YouTube's duration badge sits (sized to the badge
  on the 168 px "up next" tile). On the 120 px tile the badge can still touch a right-side element,
  which the script notes; that is expected.
- On 9:16, type stays inside the Reels/Shorts safe zone: clear of the top 14%, bottom 35% and
  right 15%. This is a conservative preset. Meta's Reels ad guide suggests leaving roughly 14% at
  the top, 35% at the bottom and 6% on each side; the right 15% covers the icon rail. Neither
  platform publishes a cover spec for organic posts.
- Type shrinks to fit the height and width and warns if it still cannot. The big type must be at
  least 50 design px of cap height and have strokes at least 1.2 px wide on the smallest tile it
  is shown at (the 168 px tile, or the phone feed width).
- Every element needs WCAG 4.5:1 contrast against what is behind it (the automatic shade aims for
  7:1). The script darkens the type area once when light type fails because of the background, and
  names the element and the fix when it cannot. `auto` colors are lightened until they pass; a
  hand-picked color is never changed, only flagged. On a light `--bg` the kicker and subtitle turn
  near-black; the label box and title stay white unless you pass dark `--title-color` and `--accent`.
- Inputs are checked rather than guessed: a missing font, an opaque logo, a fully transparent
  cut-out, `--inset`/`--subject`/`--crop`/`--subject-box` with `--panel`, `--subject-box` with
  `--subject`, a `--crop` that leaves nothing, bad channel settings, an unwritable `-o` and an `-o`
  that would overwrite an input all stop with a clear message.

<a id="notes-and-warnings"></a>
## Notes and warnings, and the fix for each

`note:` lines are information; `warning:` lines need a fix or a sentence to the user. Most warnings
name their fix, and a fix with a `--crop`, `--band` or `--inset-box` value has been confirmed by
re-rendering.

| You see | What to do |
|---|---|
| `shade 0.67 (auto): the lightest that keeps every element at 7:1 contrast` | Nothing; pass `--shade N` only if the user wants a fixed darkness. |
| `trimmed black bars: kept x..., y...` | Check the kept area is the picture. If a logo or product on black was cut, re-run with `--no-trim`. |
| `--logo replaces the title, so 'X' is not drawn` | Put the words in `--label` (thumbnail with a label), `--kicker`, or `--body` (post) if they must show. |
| `N% of the edge is as dark as YouTube's dark-theme page` | Give `--bg` the same color, lifted (the note prints it). |
| `on the preview's 120 px tile the duration badge covers part of the ...` | Nothing; it clears from 168 px up. Shorten or move the element only if the user cares about the 120 px tile. |
| `the <element> is N px wider than the type column even at its smallest size` | Split it with `\n`, pick a narrower font (bebas, oswald), or shorten it. In a post, move it into `--body` or `--details`. |
| `the big type is only N px tall` or `its strokes are only X px wide` | Shorten the text, or use the heavier weight the warning names (`--title-font outfit@SemiBold`). |
| `the label font ... has no glyph for U+...` | Pick another `--label-font` (`--list-fonts`) or drop the character. In a post, keep the user's text and change the font. |
| `<font> sets numbers as oldstyle figures` | Use `--title-tracking 0` or a font with lining figures (oswald, outfit). Without raqm, install FriBiDi. |
| `a right-to-left or joining script, so ... set as shaped words without letter spacing` | Nothing to do; the line is drawn whole and shaped. |
| `no raqm text layout ... drawn unjoined and left to right` | Install the FriBiDi library and render again. |
| `the art fills only N% of the frame's width` | Render art this tall as `--aspect 9:16`, or widen `--crop`. |
| `type overlaps the inset image` | Use the `--band`, `--text-side` or `--inset-box` the warning names. |
| `only N% of --subject-box is ...` | Use the printed `--crop` (and `--band`), or another frame. |
| `the type overlaps a face the script found`, `the type shade (band) darkens a face the script found`, `only N% of a face the script found is ...`, `the 4:5 crop cuts the person around a face the script found` | Open the preview's last tile: if the magenta outline holds a face, re-run with the `--subject-box` the warning names, then fix what its own warnings say. If it outlines no face, pass `--no-face-check` and say so. |
| `a face the script found (...) is in frame and clear of the type on 16:9` | Nothing. Pass the `--subject-box` it names if the other formats warn about that face. |
| `subject N's face (the top third of the cut-out) falls under the Reels/Shorts caption area` | Use one subject for 9:16, or a cut-out from head to waist or lower. |
| `type band at the top, clear of the inset` / `... the photo below it fills N% of the frame` | Information; `--band bottom` overrides. |
| `the body ... runs outside ...: "word" is wider than the column` | Use `--aspect 4:5`, or ask the user to approve a shorter form (a short link). Do not cut their words yourself. |
| `N of its M lines fall outside the image` | Same: `--aspect 4:5` or a shorter text the user approves. |
| `post text too small to read` | Show the user the shortest cut that fixes it, or offer a taller format, and wait for a yes. |
| `the photo fills only N% of the frame` (9:16 solid post) | Use `--aspect 4:5`. |
| `source is WxH, upscaled N x` | Recapture at 1440p or higher. |
| `capture ... is a JPEG XR HDR capture` | Export an SDR PNG or JPEG, or capture with HDR off. |
| `can't write to <folder>` | Choose another `-o`. |
