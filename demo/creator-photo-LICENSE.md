# Creator photo: source and license

Photographer: **Ron Lach**.

- Original photo: [A Man Creating Video Content](https://www.pexels.com/photo/a-man-creating-video-content-8360444/), Pexels photo 8360444.
- Original image: [Pexels download](https://images.pexels.com/photos/8360444/pexels-photo-8360444.jpeg?cs=srgb&dl=pexels-ron-lach-8360444.jpg&fm=jpg).
- License: [Pexels License](https://www.pexels.com/license/), accessed October 12, 2026.

The photo is provided under the Pexels License, **not the repository's MIT license**. The license permits use and modification without attribution. It does not allow implying endorsement by depicted people or brands, showing identifiable people in a bad or offensive light, selling unaltered copies, redistributing the photo on other stock photo or wallpaper platforms, or using it as part of a trademark, design mark, trade name, business name or service mark. Consult the linked license for its full terms.

`creator-photo.jpg` is the original 6720×4480 photograph resized to 3840×2560 with Pillow's Lanczos filter and saved as JPEG. The room, person and recording equipment come from the photograph.

`creator_A.jpg`, `creator_B.jpg`, their `_phone.jpg` copies and `_preview.png` sheets are derivatives rendered by `thumb.py`. They crop the photo (`--crop 0.2,0,0.85,1`, keeping x 768-3264 of the 3840×2560 file) and add shading and type; they contain no generated scene or replacement face. The demo uses the photo as a background image, rather than a separate `--subject` layer.

The text **ONE TAKE / NO SCRIPT** represents a fictional example video. The depicted model is a stand-in for a creator; no claim is made about his own video or endorsement of this repository.

Reproduce the thumbnail pair from the repository root:

```bash
python skill/clean-thumbnail/scripts/thumb.py demo/creator-photo.jpg --title "ONE TAKE" --label "NO SCRIPT" --style sans --title-font outfit@Bold --label-font outfit@SemiBold --text-side left --crop 0.2,0,0.85,1 --feather 0.2 --aspect 16:9 -o out/creator
```
