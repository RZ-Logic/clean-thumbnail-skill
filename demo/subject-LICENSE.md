# Subject-layer demo: sources and preparation

This demo combines two real photographs under the [Pexels License](https://www.pexels.com/license/), accessed October 13, 2026. The images and their derivatives are separate from the repository's MIT code license.

## Person

- Photographer: **Ron Lach**.
- Original photo: [A Man Creating Video Content](https://www.pexels.com/photo/a-man-creating-video-content-8360444/), Pexels photo 8360444.
- The same source is credited in [the creator photo note](creator-photo-LICENSE.md).
- `subject-person.png` is a 1924×2328 transparent cut-out of the real person. The original 6720×4480 photo was cropped to `(840, 440, 3740, 3600)`, resized to 2200×2397, masked and cropped to its visible bounds.
- Background removal used rembg 2.0.85 with `u2net_human_seg` and alpha matting. The original photo's RGB values were retained in opaque interior pixels (alpha at least 250); matting refined the soft edges. No replacement face or scene was generated.
- The rembg matte left some of the original white wall in the hair edge and a patch of the sofa cushion in the gap between his right arm and body. A NumPy pass then cleaned both, using the original photo: near the hair edge, transparency was computed from how much darker each pixel is than the flat wall behind it, and the wall color was removed from those edge pixels; loose wall-colored specks, and the wall seen through the curl left of his head, became transparent; skin, the earring and everything below the hair were left as rembg cut them. The cushion seen through the arm gap was made transparent, so the new room shows through. Only existing pixels were kept, made transparent or had the wall color subtracted; nothing was painted in.
- [rembg](https://github.com/danielgatis/rembg) and [U2Net](https://github.com/xuebinqin/U-2-Net) were preparation tools. Their packages and model weights are not included in this repository or needed to render the supplied PNG.

## Separate background

- Photographer: **Curtis Adams**.
- Original photo: [Indoor view of office room with desk and laptop at home](https://www.pexels.com/photo/indoor-view-of-office-room-with-desk-and-laptop-at-home-11125425/), Pexels photo 11125425.
- `subject-room.jpg` is the original 4000×2668 photograph cropped to `(0, 350, 4000, 2600)`, producing a 4000×2250 background. This removes excess ceiling. In the thumbnails the person covers the desk and chair, so what shows is the room: blue walls, the window, the picture frames, a console table and an armchair.

## Rendered derivatives

`subject_A.jpg`, `subject_B.jpg`, their `_phone.jpg` copies and `_preview.png` sheets combine the supplied photographs with the renderer's shading, layer matching and type. The text **HOME STUDIO / SMALL ROOM** represents a fictional example video; the model and room are stand-ins, with no claim of an actual studio tour or endorsement of this repository.

The Pexels License permits use and modification without attribution. It does not allow implying endorsement by depicted people or brands, showing identifiable people in a bad or offensive light, selling unaltered copies, redistributing the photos on other stock photo or wallpaper platforms, or using them as part of a trademark, design mark, trade name, business name or service mark. The model is composited into a room he was not photographed in; the pairing is fictional and portrays him neutrally, as a creator in a home studio. See the linked license for its full terms.

Reproduce the pair from the repository root, with only NumPy and Pillow installed:

```bash
python skill/clean-thumbnail/scripts/thumb.py demo/subject-room.jpg --subject demo/subject-person.png --title "HOME STUDIO" --label "SMALL ROOM" --style sans --title-font outfit@Bold --label-font outfit@SemiBold --text-side right --push 0.35 --aspect 16:9 -o out/subject
```
