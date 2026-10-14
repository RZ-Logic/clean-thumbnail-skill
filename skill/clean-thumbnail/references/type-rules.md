# Type rules: from video title to thumbnail text

The thumbnail and the title are read together, so the thumbnail never repeats the title. It works
for any video because it asks one question, not "what kind of video is this".

These rules are for thumbnails. A standalone post (a Facebook or Instagram image whose text is the
message) follows [Posts](#posts) instead: there is no video title to carry the rest.

## The one rule

**The big type is what makes this video different from every other video about the same thing,
especially what the image can't show.** The label is the context that makes the big type make
sense. Everything else stays in the video title.

Ask: if ten videos about this subject sat in a row, which words would make a viewer pick this one?

## Slots

| Slot | Flag | What goes in it | Length |
|---|---|---|---|
| Title | `--title` (or `--logo`) | The subject: boss, character, person, product, place, game | 1 to 3 words |
| Label | `--label` | The hook: the challenge, twist, claim, number or question | 4 words max |
| Kicker | `--kicker` | Off by default. Only when the subject needs naming context nothing else gives | 1 to 3 words |
| Subtitle | `--subtitle` | Rarely. An epithet that adds intrigue ("The Divergent Star") | 3 words max |
| Panel labels | `--panel-label` | One per comparison panel: what differs between them | 1 to 2 words each |

Keep the total at 8 words or fewer. At "up next" size (168 px) only the big type reads.

## Lead: which slot is big

`--lead ab` (the default) renders both, as the pair for YouTube's Test & Compare:

- **A, title leads:** the subject is big, the hook sits in the box. Best when the subject is the
  draw: a famous boss, a new product, a person people search for.
- **B, label leads:** the hook is big, the subject sits in the box. Best when many videos share the
  subject and the hook is the difference: a challenge run, a twist, a result. Write multi-part
  hooks with " · " ("No hit · Parry only"): each part gets its own line, the first one largest.

Don't guess which wins. Ship both and let the test decide.

## Examples across topics

| Video title | Title | Label | Images |
|---|---|---|---|
| Clair Obscur Expedition 33 GOTY - Simon Fight (No hit - Only Parry) \| Expert | SIMON | No hit · Parry only | one frame |
| DLSS 5 on a CRT Monitor Looks UNREAL | DLSS 5 | On a CRT | monitor photo + game frame as inset |
| Windows 11 26H2 vs Windows 11 25H2 - Gaming | GAMING | Which is faster | two panels, labels 25H2 and 26H2 |
| The Ronaldo Situation | RONALDO | The situation | one photo, or a background plus a cut-out |
| 4 Months of Silence Can Change Your Life | 4 MONTHS | Of silence | background plus a cut-out of the creator |
| Elden Ring Official Story Trailer | game logo (`--logo`) | Story trailer | one frame |
| RTX 5060 Review: 142 FPS at 1440p? | 142 FPS | RTX 5060 · 1440p | one frame or product shot |
| Walkthrough Part 7 - Old Lumière | OLD LUMIÈRE | Part 7 | one frame, same layout every episode |

## Dropping words

Drop: "GOTY", "|", "-", brackets, hype words (insane, unreal, best, ultimate, shocking), the
channel's name, and anything the image already shows. Keep numbers: they read at small sizes and
promise something specific.

## Style

The big type should look like the thing in the video: the official logo first, else the closest of
the 16 bundled fonts (cinzel, playfair, slab, medieval, gothic, typewriter, bebas, oswald, teko,
rajdhani, orbitron, outfit, nunito, stencil, pixel, brush). How to match: [fonts.md](fonts.md). Without a match, the
two defaults:

- `serif` (bundled Playfair Display Bold): fantasy, period, gothic, painterly,
  literary, heritage sport, documentary.
- `sans` (bundled Outfit Bold): tech, hardware, sci-fi, modern, esports,
  news, education.

The big type changes per video to match its subject; the label box keeps the channel's own font on
every video, and that is what makes the channel look like one channel.

Color: white unless the image has one color it is known for (an eclipse, a neon sign, a team
kit). Then `--title-color auto` takes that color from the image, or a hex code sets the brand's.
One color per thumbnail: on the big type or the label box, not both.

<a id="posts"></a>
## Posts

A post has no video title, so the image text is the message. Keep the user's words.

| Slot | Flag | What goes in it |
|---|---|---|
| Headline | `--title` | The one thing a scroller should take away, in the user's own words: their headline if they wrote one, else the key phrase of their first sentence |
| Body | `--body` | The rest of the message, verbatim and in order; `
` for a line break the user made |
| Details | `--details` | When, where, price, how to act or reply; parts joined with " · " |
| Label | `--label` | Optional: a short tag the user gave ("Free", "This weekend") |

- **Don't cut, reorder or move text into the caption.** Fix spelling and capitals only. No added
  hype words, emoji or claims the user didn't make; keep their numbers, dates and names exactly.
- **No word cap.** The script checks that the body reads at feed size instead. If it warns, show
  the user the shortest cut that fixes it (or a taller format, 4:5 or 9:16) and wait for a yes.
- **Hierarchy over size.** The headline is big, the body comfortable, the details small; if
  everything is important, the headline is still one idea.
- The quiet look stays: one font family per role, white type, one accent color at most.

## Never

Outlines or strokes on text, glows, drop shadows, arrows, circles, emoji, stickers, color washes,
two different accent colors (one color, on the big type or the label box, is enough),
white keylines around cut-outs, scenes generated by an image model, a label box in anything but
the channel font, more than one title font in one thumbnail.
