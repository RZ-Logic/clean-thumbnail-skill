"""Mutation check: put each fixed bug back into a copy of thumb.py and confirm its regression test fails.

Usage, from the repository root:  python tools/mutate.py

Each mutant is (name, exact source line or fragment, replacement, test id). The script first runs every
named test on the real code (they must pass), then once per mutant on a mutated copy. A mutant counts as
caught when its test fails or raises a real error; a NameError, AttributeError, ImportError or SyntaxError
means the mutant itself is broken and counts as invalid. Exit code 1 if any mutant is not caught, invalid,
or its source text is missing (the code changed: update the mutant)."""
import importlib.util
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skill", "clean-thumbnail", "scripts", "thumb.py")
FONTS = os.path.join(ROOT, "skill", "clean-thumbnail", "fonts")
BROKEN = ("NameError", "AttributeError", "ImportError", "ModuleNotFoundError", "SyntaxError", "IndentationError")

BAND_FLIP = '    if not j.band_forced and j.anchor == "bottom" and scores["top"]["score"] >= scores["bottom"]["score"] + BAND_MARGIN:'
MUTANTS = [
    ("big type color ignored", "        items.append((\"text\", ln, f, tracking_em * s, a, d, gap, big_color, big_name))",
     "        items.append((\"text\", ln, f, tracking_em * s, a, d, gap, (255, 255, 255, 255), big_name))",
     "Colors.test_explicit_colors_and_the_one_color_rule"),
    ("no lightening", "    lb = float(wcag_luminance(np.array(bg[:3], dtype=np.float32) / 255.0))",
     "    return tuple(int(v) for v in color[:3])",
     "Colors.test_auto_lightens_a_deep_color_and_falls_back_to_white"),
    ("skin counts as vivid", "    vivid = (s >= 0.55) & (v >= 0.45)", "    vivid = (s >= 0.2) & (v >= 0.3)",
     "Colors.test_signature_color_finds_vivid_families_only"),
    ("no one-color rule", "    if big_rgb != WHITE and accent_rgb != WHITE and big_rgb != accent_rgb:  # a logo or a missing box is white here", "    if False:",
     "Colors.test_explicit_colors_and_the_one_color_rule"),
    ("bg accepts auto", '        if key != "bg" and isinstance(value, str) and value.strip().lower() == "auto":',
     '        if isinstance(value, str) and value.strip().lower() == "auto":', "Colors.test_colors_from_channel_and_cli"),
    ("auto never used", '    if key == "auto":', '    if key == "auto-off":',
     "Colors.test_auto_title_color_takes_the_hue_and_passes_contrast"),
    ("crop ignored", "        box = (max(box[0], c[0]), max(box[1], c[1]), min(box[2], c[2]), min(box[3], c[3]))",
     "        box = box", "Crop.test_crop_is_in_given_coordinates_and_bars_still_trim"),
    ("crop skips bar trimming", "    box = trim_box(src)", "    box = (0, 0, w, h)",
     "Crop.test_crop_is_in_given_coordinates_and_bars_still_trim"),
    ("inset box not remapped", "    if region is None or kept == (0, 0, w, h):", "    if True:",
     "Crop.test_crop_is_in_given_coordinates_and_bars_still_trim"),
    ("no tiny-crop guard", "        if box[2] - box[0] < 16 or box[3] - box[1] < 16:", "        if False:",
     "Crop.test_crop_is_in_given_coordinates_and_bars_still_trim"),
    ("crop with panels allowed", "    if crop is not None and panels:", "    if False:", "Crop.test_crop_is_validated"),
    ("crop box unchecked", "    if crop is not None and (len(crop) != 4 or not all(0 <= v <= 1 for v in crop)",
     "    if False and (len(crop) != 4 or not all(0 <= v <= 1 for v in crop)", "Crop.test_crop_is_validated"),
    ("CLI drops --crop", "inset_box=args.inset_box, crop=args.crop,", "inset_box=args.inset_box,", "Crop.test_crop_is_validated"),
    ("hint always --focus", '                move = ("--focus to move the subject away from the type" if src.width / src.height > cv.w / cv.h * 1.1',
     '                move = ("--focus to move the subject away from the type" if True', "Crop.test_contrast_hint_names_the_tool_that_moves_the_art"),
    ("logo colors line", '        big_rgb, big_desc = WHITE, ("logo, white (--logo-white)" if logo_white else "logo, its own colors")',
     '        big_rgb, big_desc = _pick_color(j.title_color, "the big type", j.backgrounds[0], j.bg, info["notes"])', "Crop.test_logo_reports_its_own_colors_and_skips_the_one_color_rule"),
    ("crop uses the 90% band", "(0.99 if cropped else 0.9)", "(0.9 if cropped else 0.9)",
     "Crop.test_any_crop_narrower_than_16x9_keeps_its_full_height"),
    ("narrow band for captures", "(0.99 if cropped else 0.9)", "(0.99 if cropped else 0.99)",
     "Crop.test_any_crop_narrower_than_16x9_keeps_its_full_height"),
    ("inner trim takes any bars", "trim_box(src.crop(box), pristine_only=True)", "trim_box(src.crop(box), pristine_only=False)",
     "Crop.test_bars_inside_the_crop_are_trimmed"),
    ("no trim inside the crop", "        box = (box[0] + inner[0], box[1] + inner[1], box[0] + inner[2], box[1] + inner[3])",
     "        box = box", "Crop.test_bars_inside_the_crop_are_trimmed"),
    ("inset not clipped to the image", "    vis = (max(0, x0, int(round(ix0))), max(0, y0, int(round(iy0))),",
     "    vis = (max(0, x0), max(0, y0),", "Crop.test_inset_box_keeps_its_place_inside_a_crop"),
    ("label box color without a box", '        accent_rgb, accent_desc = WHITE, "none (no label box)"',
     '        accent_rgb, accent_desc = _pick_color(j.accent, "the label box", j.backgrounds[0], j.bg, info["notes"])', "Crop.test_colors_line_says_when_there_is_no_label_box"),
    ("tracking override ignored", "        tracking_em = float(title_tracking)", "        pass",
     "Crop.test_title_tracking_spaces_the_big_type_only"),
    ("tracking range unchecked", "        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 0.5:",
     "        if False:", "Crop.test_title_tracking_spaces_the_big_type_only"),
    ("tracking not reported", '        font_source += ", tracking %.2f em" % tracking_em', "        pass",
     "Crop.test_title_tracking_spaces_the_big_type_only"),
    ("CLI drops --title-tracking", 'label_font=s["label_font"], title_tracking=s["title_tracking"],', 'label_font=s["label_font"],',
     "Crop.test_title_tracking_spaces_the_big_type_only"),
    # --subject-box
    ("subject box not remapped", "    b = remap_box(j.subject_box, given, kept)", "    b = j.subject_box",
     "SubjectBox.test_subject_box_remaps_like_the_inset_box"),
    ("no cut-away warning", "    if kept_share < 0.9:", "    if False:", "SubjectBox.test_subject_box_remaps_like_the_inset_box"),
    ("subject box with a cut-out allowed", "        if panels or subjects:", "        if False:",
     "SubjectBox.test_subject_box_is_validated"),
    ("CLI drops --subject-box", "crop=args.crop, subject_box=args.subject_box)", "crop=args.crop)",
     "SubjectBox.test_subject_box_is_validated"),
    ("no side switch for the box", "            elif j.subject_box is not None:\n                text_side = _side_for_box(j)",
     "            elif False:\n                text_side = _side_for_box(j)", "SubjectBox.test_text_side_moves_to_keep_the_box_clear"),
    ("side switch picks the shaded side", '    side = "left" if scores["left"] > scores["right"] else "right"',
     '    side = "left" if scores["left"] < scores["right"] else "right"', "SubjectBox.test_text_side_moves_to_keep_the_box_clear"),
    ("no shade warning", "    shade_bad = x1 > x0 and y1 > y0 and (vis < 0.7 or heavy > 0.2)", "    shade_bad = False",
     "SubjectBox.test_explicit_side_warns_with_a_crop_that_works"),
    ("crop suggestion too lax", "SUGGEST_MIN_CLEAR = 0.9  #", "SUGGEST_MIN_CLEAR = 0.3  #",
     "SubjectBox.test_explicit_side_warns_with_a_crop_that_works"),
    ("tall crop ignores the box", "        focus_eff = focus_on(j.subject_box, src, cv.w, cv.h)", "        focus_eff = None",
     "SubjectBox.test_tall_formats_center_on_the_box"),
    ("band never moves to the top", BAND_FLIP, "    if False:",
     "SubjectBox.test_type_band_moves_to_the_top_only_when_it_clears_the_box"),
    ("band always moves to the top", BAND_FLIP, '    if not j.band_forced and j.anchor == "bottom":',
     "SubjectBox.test_type_band_moves_to_the_top_only_when_it_clears_the_box"),
    ("no out-of-frame warning", "    if inside < 0.9:", "    if False:",
     "SubjectBox.test_warnings_for_a_box_out_of_frame_or_under_the_type"),
    ("no type-overlap warning", "    overlap_bad = glyphs > 0.02 or under_type > 0.10", "    overlap_bad = False",
     "SubjectBox.test_warnings_for_a_box_out_of_frame_or_under_the_type"),
    ("wide crop ignores the box", "            focus_eff = focus_away(j.subject_box, src, cv, text_side)",
     "            focus_eff = 0.5", "SubjectBox.test_wide_capture_puts_the_box_away_from_the_type"),
    # bundled fonts by default, system fonts opt-in
    ("default serif tries Bodoni first", '    "serif": {"title": ["playfair@Bold"], "tracking": 0.06},',
     '    "serif": {"title": ["BOD_R.TTF", "playfair@Bold"], "tracking": 0.06},',
     "SystemFonts.test_default_is_bundled_even_with_bodoni_and_bahnschrift_installed"),
    ("default serif at SemiBold", '    "serif": {"title": ["playfair@Bold"], "tracking": 0.06},',
     '    "serif": {"title": ["playfair@SemiBold"], "tracking": 0.06},',
     "SystemFonts.test_default_is_bundled_even_with_bodoni_and_bahnschrift_installed"),
    ("default label tries Bahnschrift first", 'LABEL_FONTS = ["outfit@SemiBold"]',
     'LABEL_FONTS = ["bahnschrift.ttf@SemiBold", "outfit@SemiBold"]',
     "SystemFonts.test_default_is_bundled_even_with_bodoni_and_bahnschrift_installed"),
    ("default sans tries Bahnschrift first", '    "sans": {"title": ["outfit@Bold"], "tracking": 0.02},',
     '    "sans": {"title": ["bahnschrift.ttf@Bold", "outfit@Bold"], "tracking": 0.02},',
     "SystemFonts.test_default_is_bundled_even_with_bodoni_and_bahnschrift_installed"),
    ("--system-fonts ignored for the title",
     '        tspec = resolve_font(SYSTEM_TITLE_FONTS[style] if j.system_fonts else STYLES[style]["title"])',
     '        tspec = resolve_font(STYLES[style]["title"])', "SystemFonts.test_system_fonts_pick_the_installed_faces"),
    ("--system-fonts ignored for the label",
     "        lspec = resolve_font(SYSTEM_LABEL_FONTS if j.system_fonts else LABEL_FONTS)",
     "        lspec = resolve_font(LABEL_FONTS)", "SystemFonts.test_system_fonts_pick_the_installed_faces"),
    ("CLI drops --system-fonts", '                      system_fonts=bool(s["system_fonts"]),',
     "                      system_fonts=False,", "SystemFonts.test_cli_flag_and_channel_key"),
    ("system_fonts takes any text", '    elif key in ("logo_white", "system_fonts"):', '    elif key in ("logo_white",):',
     "SystemFonts.test_system_fonts_must_be_a_bool"),
    ("previews follow the system label font", "    return load_font(resolve_font(LABEL_FONTS), size)",
     "    return load_font(resolve_font(SYSTEM_LABEL_FONTS), size)",
     "SystemFonts.test_previews_use_the_bundled_label_font_even_with_system_fonts"),
    ("two-panel Reels label says use two panels", "            if not safe_room:", "            if True:",
     "ReviewRound2.test_reels_two_panel_labels_avoid_the_hook"),
    # review 9: --subject-box advice, --band, the duration badge and preview text
    ("crop advice ignores the trimmed bars", "bars=(tb[0], tb[2]),", "bars=(0, W),",
     "SubjectBoxAdvice.test_crop_advice_with_a_crop_follows_the_trimmed_bars"),
    ("no vertical crop search", "    grid = [default] if default is None else [default] + [i / 50.0 for i in range(51)]",
     "    grid = [default]", "SubjectBoxAdvice.test_tall_portrait_crop_moves_the_face_above_the_band"),
    ("overlap keeps its own side advice", "        shade_fix = overlap_fix = _wide_fix(j, shade_bad, overlap_bad)",
     '        shade_fix, overlap_fix = _wide_fix(j, shade_bad, overlap_bad), "use --text-side right or shorten the text"',
     "SubjectBoxAdvice.test_shade_and_overlap_name_one_fix_and_no_useless_side_switch"),
    ("other side always counts as better", "    if score(om) >= score(cm) + 0.10:", "    if True:",
     "SubjectBoxAdvice.test_shade_and_overlap_name_one_fix_and_no_useless_side_switch"),
    ("no --focus advice", "    if j.focus is None or j.narrow or src.width / src.height <= (cv.w / cv.h) * 1.1:",
     "    if True:", "SubjectBoxAdvice.test_wide_capture_with_focus_gets_the_focus_fix"),
    ("crop advice may cut the inset", "kept_h=kept_h, y=y, keep=keep, exact=exact)", "kept_h=kept_h, y=y, keep=(), exact=exact)",
     "SubjectBoxAdvice.test_inset_and_subject_box_together"),
    ("crop advice puts the inset under the type", '            if (k0 < column[1]) if side == "left" else (k1 > cv.w - column[1]):',
     "            if False:", "SubjectBoxAdvice.test_inset_and_subject_box_together"),
    ("tall crop follows the inset over the box",
     "    if focus is None and cv.tall and j.subject_box is not None:  # the face wins over an inset on tall crops",
     "    if focus is None and cv.tall and j.subject_box is not None and inset is None:",
     "SubjectBoxAdvice.test_inset_and_subject_box_together"),
    ("title line omits the style tracking",
     '            if title_tracking is None and own and abs(own["tracking"] - tracking_em) > 1e-9:', "            if False:",
     "SubjectBoxAdvice.test_default_title_line_names_the_exact_reproduction"),
    ("band choice ignores the safe zone",
     "    inside, v, heavy = box_cover(rect, j.cv, vis_y=vis, region=box_region(j.cv))",
     "    inside, v, heavy = box_cover(rect, j.cv, vis_y=vis)", "SubjectBoxAdvice.test_band_choice_counts_the_reels_safe_zone"),
    ("band choice scores the whole box",
     "    middle = box_cover((rect[0], rect[1] + 0.35 * h, rect[2], rect[1] + 0.75 * h), j.cv, vis_y=vis)[1]",
     "    middle = box_cover(rect, j.cv, vis_y=vis)[1]", "SubjectBoxAdvice.test_band_choice_weighs_the_middle_of_the_box"),
    ("--band ignored", "                anchor, j.band_forced = j.band_pos, True", "                pass",
     "SubjectBoxAdvice.test_band_flag_is_validated_and_only_moves_tall_single_layouts"),
    ("--band unchecked", "    if j.band_pos not in BANDS:", "    if False:",
     "SubjectBoxAdvice.test_band_flag_is_validated_and_only_moves_tall_single_layouts"),
    ("CLI drops --band", ' band=s["band"],', "",
     "SubjectBoxAdvice.test_band_flag_is_validated_and_only_moves_tall_single_layouts"),
    ("shade warning never offers --band", '        if clears or (m and here and m["score"] >= here["score"] + BAND_MARGIN):',
     "        if False:", "SubjectBoxAdvice.test_dawnwalker_flips_and_ds2_stays"),
    ("badge zone hard-coded", "        return (int(self.w * int(x0) / tw), int(self.h * int(y0) / th), self.w, self.h)",
     "        return (int(self.w * 0.80), int(self.h * 0.82), self.w, self.h)",
     "DurationBadge.test_right_side_type_keeps_clear_of_the_badge_at_168px_and_up"),
    ("small preview text untracked", "PREVIEW_TRACKING = 0.03", "PREVIEW_TRACKING = 0.0",
     "DurationBadge.test_small_preview_text_keeps_its_word_spaces"),
    ("caption word space ignored", "            a = max(a, space)", "            pass",
     "DurationBadge.test_small_preview_text_keeps_its_word_spaces"),
    ("push blurs plain captures", "    push = (0.5 if push is None else push) if subjects else 0.0",
     "    push = (0.5 if subjects else 0.0) if push is None else push",
     "ReviewArgon.test_push_only_softens_behind_a_subject"),
    ("contrast advice always --accent",
     '    return "--title-color" if name in ("title", "hook") else "--accent" if name == "label box" else "color"',
     '    return "--accent"', "ReviewArgon.test_contrast_advice_names_the_flag_that_sets_the_color"),
    ("failing ratio rounded up", "    return int(ratio * 10) / 10.0 if ratio < MIN_CONTRAST else ratio",
     "    return round(ratio, 1)", "ReviewArgon.test_summary_and_warning_print_the_same_failing_ratio"),
    ("rembg once per variant", 'subjects=[cut_out(open_image(p, "subject")) for p in args.subject or []] or None,',
     'subjects=[open_image(p, "subject") for p in args.subject or []] or None,',
     "ReviewArgon.test_rembg_runs_once_per_subject_across_variants_and_formats"),
]


def load(path):
    spec = importlib.util.spec_from_file_location("thumb", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.FONT_ROOT, mod._catalog = FONTS, None  # a temp copy must still find the bundled fonts
    return mod


def run(mod, test):
    sys.modules["thumb"] = mod
    sys.modules.pop("test_thumb", None)
    import test_thumb
    test_thumb.thumb = mod
    return unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromName("test_thumb." + test))


def main():
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    src = open(SCRIPT, encoding="utf-8").read()
    real = load(SCRIPT)
    bad = 0
    for test in sorted({m[3] for m in MUTANTS}):
        r = run(real, test)
        if r.failures or r.errors:
            print("baseline FAILS (fix the code or the test first): %s" % test)
            bad += 1
    if bad:
        return 1
    caught = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, (name, old, new, test) in enumerate(MUTANTS):
            if src.count(old) != 1:
                print("%-44s TEXT NOT FOUND (%d matches): update this mutant" % (name, src.count(old)))
                bad += 1
                continue
            path = os.path.join(tmp, "thumb_%d.py" % i)
            with open(path, "w", encoding="utf-8") as f:
                f.write(src.replace(old, new))
            r = run(load(path), test)
            errors = [t[1].strip().splitlines()[-1] for t in r.errors]
            if any(e.split(":")[0].split(".")[-1] in BROKEN for e in errors):
                print("%-44s INVALID: %s" % (name, errors[0][:80]))
                bad += 1
            elif r.failures or r.errors:
                caught += 1
                print("%-44s caught" % name)
            else:
                print("%-44s NOT CAUGHT by %s" % (name, test))
                bad += 1
    print("%d/%d mutants caught" % (caught, len(MUTANTS)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
