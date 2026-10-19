# Title look: logo, matched font, or channel font

The big type should look like the thing the video is about; the label box keeps the channel's own
font every time, so each thumbnail looks like its game and the channel still looks like itself.

## Order

1. **Official logo.** If the user gave a logo, or one is in the working folder (`logo.png`,
   `*_logo.png`), use `--logo FILE` (add `--logo-white` for a colored logo on the dark side). Press
   kits publish transparent logos; this is the most authentic option and what publisher
   thumbnails use. An opaque logo (JPG, no transparency) is refused.
2. **Matched bundled font.** Otherwise look at the title's logo (in the capture, the store page,
   or what you know of it) and pick the closest look below with `--title-font NAME`. Say which
   font you picked and why ("cinzel: the logo uses classical Roman capitals"). If you can't see
   the logo, pick by genre and say that you did.
3. **No good match?** Don't force one: a wrong look is worse than a neutral one. Use the channel's
   saved `title_font`, else the serif or sans default, and say that nothing bundled fits.

The channel's saved `title_font` (in `clean-thumbnail.json`) is the default; a matched font for
this video overrides it for this run only. The script prints `title type: ...` for every variant,
and `--list-fonts` prints the catalog with each font's real family name.

**The defaults** are bundled, so the same command sets the same type on every OS: the serif style
is `playfair@Bold` at 0.06 em, the sans style `outfit@Bold` at 0.02 em, and the label box (and the
preview captions, always) `outfit@SemiBold`. `--title-font playfair` gives the catalog's SemiBold at
0.04 em instead; use `playfair@Bold --title-tracking 0.06` to match the default by name.
`--system-fonts` (or `"system_fonts": true`) tries installed faces first, as before v0.8.0: serif
Bodoni MT (Office), Didot (macOS), Garamond, Constantia, Georgia; sans and label Bahnschrift, Segoe
UI Bold, Helvetica Neue (the label also Avenir Next); then the bundled fonts. The look then depends
on the machine. An explicit `--title-font` or `--label-font` wins either way.

## Reading a logo

Decide in this order; the first clear answer usually picks the font.

| Question | If yes |
|---|---|
| Pixel grid, blocky bitmap letters? | `pixel` |
| Broken angular strokes, blackletter? | `gothic` (write the title in Title Case) |
| Typed, even-width letters, case-file feel? | `typewriter` |
| Stencil cuts or military crate lettering? | `stencil` |
| Heavy block serifs, western or industrial? | `slab` |
| Serifs with very thin hairlines and heavy stems? | `playfair` |
| All-caps Roman letters with wedge serifs, carved feel? | `cinzel` |
| Playful pointed calligraphy, storybook or tavern sign? | `medieval` |
| Casual slanted hand-lettering? | `brush` (Title Case) |
| Sans, very tall and narrow, uniform weight? | `bebas` |
| Sans, narrow, newspaper headline? | `oswald` |
| Sans, squarish rounded corners, heavy, scoreboard? | `teko` |
| Sans, flat-sided squares, lighter and wider than teko? | `rajdhani` |
| Sans, wide and geometric, space-age? | `orbitron` |
| Sans, rounded ends, soft and friendly? | `nunito` |
| Sans, clean geometric, app or brand feel? | `outfit` |
| None of these | channel or default font (step 3) |

Tie-breaks: **gothic vs medieval**: dense, broken blackletter is gothic; playful readable
calligraphy is medieval. **teko vs rajdhani**: teko is heavier with rounded-square bowls; rajdhani
is lighter, wider and flat-sided. **orbitron vs outfit**: orbitron is wide and sci-fi; outfit is
neutral and round. **Horror**: gothic only for gothic and vampire horror; psychological and modern
horror reads better in `typewriter` or `playfair`; survival horror can use `slab`.

## Bundled fonts

| Name | Family | Look | Fits |
|---|---|---|---|
| cinzel | Cinzel | classical Roman capitals | epic, mythic and dark fantasy, ancient history |
| playfair | Playfair Display | high-contrast didone serif | period drama, mystery, luxury, cinematic titles |
| slab | Zilla Slab | slab serif | westerns, crime, industrial and survival settings |
| medieval | MedievalSharp | storybook medieval display | whimsical fantasy, folklore, tavern and adventure |
| gothic | Grenze Gotisch | blackletter | gothic horror, vampires, metal, grimdark |
| typewriter | Courier Prime | typewriter | psychological horror, true crime, mystery, espionage |
| bebas | Bebas Neue | tall condensed capitals | action, thrillers, sports, trailers |
| oswald | Oswald | condensed grotesque | news, explainers, sports analysis, history |
| teko | Teko | squared sans | esports, racing, sports games, shooters |
| rajdhani | Rajdhani | squared technical sans | tech, hardware, benchmarks, cyberpunk UI |
| orbitron | Orbitron | geometric sci-fi | space, sci-fi shooters, synthwave |
| outfit | Outfit | geometric modern sans | tech brands, apps, gadgets, clean modern titles |
| nunito | Nunito | rounded friendly sans | cozy games, family titles, lifestyle |
| stencil | Black Ops One | military stencil | military shooters, tactical, war history |
| pixel | Press Start 2P | 8-bit pixel | retro and pixel-art games, arcade |
| brush | Kaushan Script | casual brush-marker script | street, skate, surf, travel, food, vlogs |

## Weight and spacing

Match how heavy and how spaced out the logo's letters are, not only their shape: a thin, airy logo
set in a bold weight reads as a different brand.

- **Weight:** `--title-font cinzel@Black`, `outfit@Light` and so on pick a named weight (case
  doesn't matter). Variable fonts: cinzel, playfair, gothic, oswald, teko, orbitron, outfit,
  nunito. An unknown weight gets a warning and the catalog's weight. Single-weight fonts (bebas,
  slab, medieval, typewriter, rajdhani, stencil, pixel, brush) ignore `@Weight` with a warning
  unless it names their own weight. Without `@Weight` a variable font uses its catalog weight
  (Medium to ExtraBold, chosen for legibility).
- **Spacing:** `--title-tracking 0.12` sets the big type's letter spacing in em (0 to 0.5). Without
  it each font keeps its own, 0 to 0.08 em. Use 0.1 to 0.2 for logos with wide, even gaps between
  letters.
- **Example:** Death Stranding's thin, widely spaced sans is `--title-font outfit@Light
  --title-tracking 0.12`. The catalog default (`outfit`, SemiBold, 0.02 em) is the right family
  but the wrong weight and spacing.
- **Limits:** stay at Light or heavier (ExtraLight and Thin strokes risk fading at 168 px), and
  wider spacing makes long titles smaller. The script measures the strokes on the smallest tile the
  type is shown at (168 px on 16:9, the phone feed width on 4:5 and 9:16) and warns under 1.2 px,
  naming the lightest heavier weight that passes. The wide, spaced Light recipe
  suits one or two short words on 16:9 (a long title such as DEATH STRANDING 2 at 16:9 warns that the
  big type is small) and works on 4:5 and 9:16, as in the README demo; otherwise use `outfit@Regular`
  or `outfit@SemiBold`. Check the preview.

## Readability at small sizes

- Thin at 168 px: playfair, medieval, cinzel, rajdhani, gothic. Keep these titles to one or two
  short words.
- Wide fonts (cinzel, orbitron, pixel, stencil, nunito, outfit) set long single words small. The
  script splits two-word titles when that makes the type clearly bigger, and warns when the type
  ends up small anyway.
- Numbers: the script asks every font for lining figures, so playfair and gothic numbers ("2049",
  "142 FPS") sit on the baseline at full height. This needs Pillow with raqm (the FriBiDi library;
  the standard Linux wheels include it, some Windows and macOS builds do not). Some fonts (slab)
  only give lining figures beside letters and untracked: the script warns and names the fix
  (`--title-tracking 0`, or oswald or outfit). Without raqm, playfair and slab print oldstyle
  figures and the script says so.
- Tracked type is measured without ligatures, so "fi" and "ff" no longer collide, and accents stay
  with their letter. Right-to-left and joining scripts (Hebrew, Arabic, Indic, Thai) are drawn as
  whole shaped lines without tracking, with a note; without raqm the script warns that they would
  draw unjoined.
- The script warns when the font has no glyph for a character (accents, other scripts, emoji, and
  the middle dot of a hook such as "No hit · Parry only"), for the big type and for the label box,
  kicker, subtitle and panel labels. It names the font and the elements.
- Label fonts marked as not capitals-only (gothic, brush) keep the case you write for the label box,
  kicker and subtitle; the others set them in capitals.

## Limits

- A match is close in spirit, not the real lettering: most game and film titles are custom-drawn
  logos, and most tech brands use proprietary typefaces (NVIDIA Sans, Netflix Sans and the like)
  that can't be bundled or downloaded. For the real thing, use the official logo.
- Never download fonts at render time. The bundled fonts are SIL Open Font License 1.1, unmodified,
  with each license in `fonts/<name>/OFL.txt`.
