"""Mutation check: put each fixed bug back into a copy of thumb.py and confirm its regression test fails.

Usage, from the repository root:  python tools/mutate.py [name ...]
With names (any part of a mutant's name), only the matching mutants run; --list prints them all.

Each mutant is (name, exact source line or fragment, replacement, test id[, options]). The script first runs
every named test on the real code (they must pass), then once per mutant on a mutated copy. A mutant counts as
caught only when its test fails an assertion. A test that errors instead is reported apart:
- a NameError, AttributeError, ImportError or SyntaxError (or a copy that doesn't load) means the mutant itself
  is broken: INVALID;
- an exception named in the options' "errors" is the old behavior itself (the bug was a traceback) and counts
  as caught by that error;
- any other exception is a CRASH: the test stopped before its assertion, so the mutant shows nothing about it.
  Rewrite the mutant so the old code path runs to the end (e.g. "elif False:" instead of returning None).
A test skipped on this machine (a font or library it needs is missing), or a mutant whose options say
"needs": "raqm" on a Pillow without raqm layout (the mutated code path only runs with it), is reported as
SKIPPED and not counted.
Exit code 1 if any mutant is not caught, crashed, invalid, or its source text is missing (the code changed:
update the mutant)."""
import importlib.util
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skill", "clean-thumbnail", "scripts", "thumb.py")
FONTS = os.path.join(ROOT, "skill", "clean-thumbnail", "fonts")
FACE_MODEL = os.path.join(ROOT, "skill", "clean-thumbnail", "models", "haarcascade_frontalface_alt2.xml")
BROKEN = ("NameError", "AttributeError", "ImportError", "ModuleNotFoundError", "SyntaxError", "IndentationError")
TEST_MODULES = ("test_thumb", "test_backdrop", "test_fix_text", "test_fix_post", "test_fix_layout", "test_fix_io",
                "test_fix_shade", "test_no_box_subject")  # test ids: "Class.test" (test_thumb) or "module.Class.test"

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
    ("auto never used", '    if key in ("white", "#fff", "#ffffff"):', '    if key in ("white", "#fff", "#ffffff", "auto"):',
     "Colors.test_auto_title_color_takes_the_hue_and_passes_contrast"),
    ("crop ignored", "        box = (max(box[0], c[0]), max(box[1], c[1]), min(box[2], c[2]), min(box[3], c[3]))",
     "        box = box", "Crop.test_crop_is_in_given_coordinates_and_bars_still_trim"),
    ("crop skips bar trimming", "    box = trim_box(src)", "    box = (0, 0, w, h)",
     "Crop.test_crop_is_in_given_coordinates_and_bars_still_trim"),
    ("inset box not remapped", "    if region is None or kept == (0, 0, w, h):", "    if True:",
     "Crop.test_crop_is_in_given_coordinates_and_bars_still_trim"),
    ("no tiny-crop guard", "        if box[2] - box[0] < 16 or box[3] - box[1] < 16:\n            raise ValueError(\"--crop leaves almost nothing of the image (after",
     "        if False:\n            raise ValueError(\"--crop leaves almost nothing of the image (after",
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
    ("subject box with a cut-out allowed", "        if panels or subjects:", "        if panels:",
     "SubjectBox.test_subject_box_is_validated"),
    ("CLI drops --subject-box", "crop=args.crop, subject_box=args.subject_box, mode=", "crop=args.crop, mode=",
     "SubjectBox.test_subject_box_is_validated"),
    ("no side switch for the box", "            elif j.subject_box is not None:\n                text_side = _side_for_box(j)",
     "            elif False:\n                text_side = _side_for_box(j)", "SubjectBox.test_text_side_moves_to_keep_the_box_clear"),
    ("side switch picks the shaded side", '    side = "left" if scores["left"] > scores["right"] else "right"',
     '    side = "left" if scores["left"] < scores["right"] else "right"', "SubjectBox.test_text_side_moves_to_keep_the_box_clear"),
    ("no shade warning", "    shade_bad = not solid and x1 > x0 and y1 > y0 and (vis < 0.7 or heavy > 0.2)", "    shade_bad = False",
     "SubjectBox.test_explicit_side_warns_with_a_crop_that_works"),
    ("crop suggestion too lax", "SUGGEST_MIN_CLEAR = 0.9  #", "SUGGEST_MIN_CLEAR = 0.3  #",
     "SubjectBox.test_explicit_side_warns_with_a_crop_that_works"),
    ("tall crop ignores the box",
     "    elif focus is None and cv.tall and j.subject_box is not None:  # the face wins over an inset on tall crops",
     "    elif False:",
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
    ("system_fonts takes any text", '    elif key in ("logo_white", "system_fonts"):',
     '    elif key == "system_fonts":\n        pass\n    elif key in ("logo_white",):',
     "SystemFonts.test_system_fonts_must_be_a_bool"),
    ("previews follow the system label font", "    return load_font(resolve_font(LABEL_FONTS), size)",
     "    return load_font(resolve_font(SYSTEM_LABEL_FONTS), size)",
     "SystemFonts.test_previews_use_the_bundled_label_font_even_with_system_fonts"),
    ("two-panel Reels label says use two panels", "            if not safe_room:", "            if True:",
     "ReviewRound2.test_reels_two_panel_labels_avoid_the_hook"),
    # review 9: --subject-box advice, --band, the duration badge and preview text
    ("crop advice ignores the trimmed bars", "bars=(tb[0], tb[2]),", "bars=(0, W),",
     "SubjectBoxAdvice.test_crop_advice_with_a_crop_follows_the_trimmed_bars"),
    ("no vertical crop search", "        grid = [dflt] if dflt is None else [dflt] + [i / 50.0 for i in range(51)]",
     "        grid = [dflt]", "SubjectBoxAdvice.test_tall_portrait_crop_moves_the_face_above_the_band"),
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
     "    elif focus is None and cv.tall and j.subject_box is not None:  # the face wins over an inset on tall crops",
     "    elif focus is None and cv.tall and j.subject_box is not None and inset is None:",
     "SubjectBoxAdvice.test_inset_and_subject_box_together"),
    ("title line omits the style tracking",
     '            if title_tracking is None and own and abs(own["tracking"] - tracking_em) > 1e-9:', "            if False:",
     "SubjectBoxAdvice.test_default_title_line_names_the_exact_reproduction"),
    ("band choice ignores the safe zone",
     "    inside, v, heavy = box_cover(rect, j.cv, vis_y=vis, region=region or box_region(j.cv))",
     "    inside, v, heavy = box_cover(rect, j.cv, vis_y=vis, region=region)", "SubjectBoxAdvice.test_band_choice_counts_the_reels_safe_zone"),
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
    # post mode (--mode post)
    ("body accepted in thumbnail mode", '    if (j.body or j.details) and j.mode != "post":', "    if False:",
     "PostMode.test_body_and_details_need_post_mode"),
    ("last wrapped line dropped", "    return lines + ([cur] if cur else [])", "    return lines",
     "PostMode.test_post_keeps_every_word_in_order"),
    ("details wrap by words only", '        for part in [p.strip() for p in line.split(" · ") if p.strip()]:',
     "        for part in [line]:",
     "PostMode.test_details_break_at_their_separators"),
    ("word cap in post mode",
     '    if j.mode == "post":  # no duration badge on a post image, and no word cap: the message is the text',
     "    if False:", "PostMode.test_no_word_count_warning_in_post_mode"),
    ("readability floor ignored", "        if feed_cap < POST_MIN_CAP - 1e-6:", "        if False:",
     "PostMode.test_readability_warning_fires_only_for_an_over_long_message"),
    ("body shrinks before the headline", "    big_min = min(j.base, POST_BIG_MIN)", "    big_min = j.base",
     "PostMode.test_headline_shrinks_before_the_body"),
    ("post band takes the whole height", '    if j.mode == "post" and not cv.reels:', "    if False:",
     "PostMode.test_post_band_leaves_the_image_its_share"),
    ("badge corner checked on posts",
     '        bottom_limit = (cv.timestamp_zone[1] - cv.px(12) if text_side == "right" and j.mode != "post"',
     '        bottom_limit = (cv.timestamp_zone[1] - cv.px(12) if text_side == "right"',
     "PostMode.test_contrast_and_frame_checks_cover_body_and_details"),
    ("post uses the saved aspect", '    aspects = (s["aspect"] if not post or args.aspect is not None else "4:5").split(",")',
     '    aspects = s["aspect"].split(",")', "PostMode.test_post_cli_defaults_and_report"),
    ("post renders an A/B pair", '    lead_choice = s["lead"] if not post or args.lead is not None else "title"',
     '    lead_choice = s["lead"]', "PostMode.test_post_cli_defaults_and_report"),
    ("post column as narrow as a thumbnail's", "        cv.col_share = POST_COL_SHARE", "        pass",
     "PostMode.test_post_band_leaves_the_image_its_share"),
    # --backdrop solid (tests/test_backdrop.py)
    ("solid panel not opaque", "    canvas = Image.new(\"RGB\", (cv.w, cv.h), tuple(bg))\n    canvas.paste(depth(grade(img, vignette=0)",
     "    canvas = Image.blend(Image.new(\"RGB\", (cv.w, cv.h), tuple(bg)), cover(src, cv.w, cv.h, focus, vfocus)[0], 0.12)"
     "\n    canvas.paste(depth(grade(img, vignette=0)",
     "test_backdrop.SolidTall.test_feed_4x5_panel_below_the_photo"),
    ("photo bleeds into the panel", "    img, scale, sx, sy = cover(src, rw, rh, focus, vfocus)",
     "    img, scale, sx, sy = cover(src, rw + cv.px(24), rh + cv.px(24), focus, vfocus)",
     "test_backdrop.SolidTall.test_feed_4x5_panel_below_the_photo"),
    ("solid photo vignetted", "    canvas.paste(depth(grade(img, vignette=0), cv, push), (rect[0], rect[1]))",
     "    canvas.paste(depth(grade(img), cv, push), (rect[0], rect[1]))",
     "test_backdrop.SolidTall.test_photo_is_not_vignetted"),
    ("tall photo runs under the type", "        return (0, 0, cv.w, int(round(min(max(band[0], 1), cv.h))))",
     "        return (0, 0, cv.w, int(round(min(max(band[0] + cv.px(120), 1), cv.h))))",
     "test_backdrop.SolidTall.test_feed_4x5_panel_below_the_photo"),
    ("top band panel on the wrong side", "            return (0, int(round(min(max(band[1], 0), cv.h - 1))), cv.w, cv.h)",
     "            return (0, 0, cv.w, int(round(min(max(band[0], 1), cv.h))))",
     "test_backdrop.SolidTall.test_band_at_the_top_puts_the_panel_above"),
    ("16:9 panel without breathing room", "    edge = int(min(cv.w - 1, cv.margin + cv.col_w + cv.margin))",
     "    edge = int(min(cv.w - 1, cv.margin + cv.col_w))", "test_backdrop.SolidWide.test_panel_covers_the_type_column"),
    ("feather applied to solid", "    elif j.backdrop == \"solid\":  # no shade: the type sits on the opaque panel",
     "    elif j.backdrop == \"solid\" and abs(j.feather - 0.36) < 1e-9:",
     "test_backdrop.SolidNotes.test_shade_and_feather_have_no_effect_and_say_so"),
    ("no shade/feather note", "    if idle:\n        info[\"notes\"].append(SOLID_IDLE_NOTE",
     "    if False:\n        info[\"notes\"].append(SOLID_IDLE_NOTE",
     "test_backdrop.SolidNotes.test_shade_and_feather_have_no_effect_and_say_so"),
    ("shade/feather note always", "            if not (key == \"shade\" and j.shade_auto)\n            and all(abs(v - d) > 1e-9 for d in dflt if d is not None)]",
     "            ]",
     "test_backdrop.SolidNotes.test_shade_and_feather_have_no_effect_and_say_so"),
    ("subject box ignored in the photo rect", "    if j.subject_box is not None:\n        return focus_on(j.subject_box, j.src, rw, rh)",
     # the crop the photo gets without the box: the busiest window (None would crash cover_box, not the test)
     "    if j.subject_box is not None:\n"
     "        return subject_focus(j.src, (rw / max(rw / j.src.width, rh / j.src.height)) / j.src.width)",
     "test_backdrop.SolidTall.test_subject_box_stays_in_the_photo_and_visible"),
    ("solid vertical crop ignores the box", "            dflt = vfocus_in(j.subject_box, j.src, rect, region)",
     "            dflt = None", "test_backdrop.SolidTall.test_portrait_capture_crop_follows_the_box_vertically"),
    ("panel counts as in view", "        vis_x, vis_y = span_mask(cv.w, ir[0], ir[2]), span_mask(cv.h, ir[1], ir[3])",
     "        vis_x, vis_y = None, None", "test_backdrop.SolidWide.test_subject_box_in_the_panel_area_is_out_of_view"),
    ("explicit focus loses to the box", "    if j.focus is not None:\n        return j.focus",
     "    if False:\n        return j.focus", "test_backdrop.SolidWide.test_subject_box_in_the_panel_area_is_out_of_view"),
    ("cut-out placed over the panel", "band if cv.tall else None, tex, area=j.image_rect)",
     "band if cv.tall else None, tex, area=None)", "test_backdrop.SolidWide.test_wide_cutout_keeps_to_the_photo_columns"),
    ("no note when --panel ignores solid",
     "        j.info[\"notes\"].append(\"--backdrop solid is ignored with --panel: comparison panels fill the frame\")",
     "        pass",
     "test_backdrop.SolidNotes.test_solid_is_ignored_with_panels"),
    ("unknown backdrop accepted", "    if j.backdrop not in BACKDROPS:", "    if False:",
     "test_backdrop.FadeDefault.test_unknown_backdrop_is_rejected"),
    ("channel key unvalidated", ' "backdrop": list(BACKDROPS),', "",
     "test_backdrop.SolidCLI.test_channel_key_validates_and_applies"),
    ("fade gets solid info", "    if j.backdrop != \"solid\":\n        return\n    cv, src, info = j.cv, j.src, j.info",
     "    if False:\n        return\n    cv, src, info = j.cv, j.src, j.info",
     "test_backdrop.FadeDefault.test_fade_is_the_default_and_identical"),
    ("render defaults to solid", 'body="", details="", backdrop="fade", trim=True,', 'body="", details="", backdrop="solid", trim=True,',
     "test_backdrop.FadeDefault.test_fade_is_the_default_and_identical"),
    ("channel default is solid", '                mode="thumbnail", backdrop="fade")', '                mode="thumbnail", backdrop="solid")',
     "test_backdrop.SolidCLI.test_cli_prints_the_backdrop_line_after_colors"),
    ("CLI drops --backdrop", 'details=args.details, backdrop=s["backdrop"], trim=', "details=args.details, trim=",
     "test_backdrop.SolidCLI.test_cli_prints_the_backdrop_line_after_colors"),
    ("CLI omits the backdrop line", '            print("backdrop: solid (panel %s)" % info["backdrop_color"])',
     "            pass", "test_backdrop.SolidCLI.test_cli_prints_the_backdrop_line_after_colors"),
    ("solid contrast hint names --focus", '            if j.backdrop == "solid":\n                hint = ("the --bg panel is light',
     '            if False:\n                hint = ("the --bg panel is light',
     "test_backdrop.SolidContrast.test_light_panel_says_use_a_dark_title_color"),
    # inputs and outputs the suite never checked (tests-ci review)
    ("capture ignores EXIF rotation", "    img = to_srgb(ImageOps.exif_transpose(img), icc)", "    img = to_srgb(img, icc)",
     "ExifOrientation.test_capture_subject_and_logo_are_turned_upright"),
    ("render ignores EXIF rotation", "    img = to_srgb(ImageOps.exif_transpose(img), icc)", "    img = to_srgb(img, icc)",
     "ExifOrientation.test_render_sees_the_upright_photo"),
    ("cut-out ignores EXIF rotation", "    img = ImageOps.exif_transpose(img)\n    if img.mode in (\"RGBA\", \"LA\", \"PA\")",
     "    if img.mode in (\"RGBA\", \"LA\", \"PA\")", "ExifOrientation.test_capture_subject_and_logo_are_turned_upright"),
    ("logo ignores EXIF rotation", "logo = to_srgb(ImageOps.exif_transpose(logo).convert(\"RGBA\")",
     "logo = to_srgb(logo.convert(\"RGBA\")", "ExifOrientation.test_capture_subject_and_logo_are_turned_upright"),
    ("masters at q60", "    q = 92\n    while True:", "    q = 60\n    while True:",
     "Resolution.test_export_writes_master_phone_copy_and_preview"),
    ("4:2:0 chroma at any quality", "subsampling=0 if q >= 88 else 2", "subsampling=2",
     "Resolution.test_export_writes_master_phone_copy_and_preview"),
    ("4:4:4 chroma at any quality", "subsampling=0 if q >= 88 else 2", "subsampling=0",
     "Resolution.test_phone_copy_stays_under_2mb_on_pure_noise"),
    ("phone limit 20 MB", "PHONE_MAX_BYTES = 2 * 1024 * 1024", "PHONE_MAX_BYTES = 20 * 1024 * 1024",
     "Resolution.test_phone_copy_stays_under_2mb_on_pure_noise"),
    ("no quality step-down", "        q -= 4\n    write_file(path", "        break\n    write_file(path",
     "Resolution.test_phone_copy_stays_under_2mb_on_pure_noise"),
    ("quality steps down too far", "        q -= 4\n    write_file(path", "        q -= 8\n    write_file(path",
     "Resolution.test_phone_copy_stays_under_2mb_on_pure_noise"),
    ("inner trim accepts a standard shape", "(not pristine_only and (common(cw, h - top - bottom) or common(cw, h)))",
     "(common(cw, h - top - bottom) or common(cw, h))", "Crop.test_bars_inside_the_crop_are_trimmed"),
    # ---------------------------------------------------------------- v0.9.1 text (tests/test_fix_text.py)
    # A line break in a one-line element: Pillow 12 draws it as a glyph, Pillow 13's raqm raises RuntimeError
    # (raqm_layout() failed). Either is the old bug, so these three count the RuntimeError as caught.
    ("type-color-1: typed \\n reaches the label box", "small(one_line(title)), False", "small(title), False",
     "test_fix_text.LineBreaks.test_title_with_a_line_break_reads_as_words_in_the_label_box",
     {"errors": ("RuntimeError",)}),
    ("type-color-1: label's \\n reaches the box", "small(one_line(label)), True", "small(label), True",
     "test_fix_text.LineBreaks.test_label_with_a_line_break_splits_the_hook_and_joins_in_the_box",
     {"errors": ("RuntimeError",)}),
    ("type-color-1: typed \\n stays text", r'''unicodedata.normalize("NFC", t or "").replace("\\n", "\n").replace("\r\n", "\n")''',
     r'''unicodedata.normalize("NFC", t or "").replace("\r\n", "\n")''',
     "test_fix_text.LineBreaks.test_label_with_a_line_break_splits_the_hook_and_joins_in_the_box"),
    ("type-color-1: kicker keeps its line break", "    kicker, subtitle = one_line(kicker), one_line(subtitle)\n",
     "", "test_fix_text.LineBreaks.test_kicker_subtitle_and_panel_labels_join_their_lines",
     {"errors": ("RuntimeError",)}),
    ("type-color-1: panel labels keep their breaks", 'panel_labels = [one_line(unicodedata.normalize("NFC", p or "")) for p',
     'panel_labels = [(unicodedata.normalize("NFC", p or "")) for p',
     "test_fix_text.LineBreaks.test_kicker_subtitle_and_panel_labels_join_their_lines"),
    ("type-color-1: hook not stacked at a typed break", 'stack=(j.lead == "label" and ("·" in j.label or "\\n" in j.label)),',
     'stack=(j.lead == "label" and "·" in j.label),',
     "test_fix_text.LineBreaks.test_label_with_a_line_break_splits_the_hook_and_joins_in_the_box"),
    ("G1: no note for a title the logo replaces", "    if logo is not None and title.strip():\n        where =",
     "    if False:\n        where =", "test_fix_text.LogoNote.test_thumbnail_and_post_say_the_title_is_not_drawn"),
    ("G1: note always names --label", '    where = "--body" if j.mode == "post" else "--kicker" if label else "--label"',
     '    where = "--label"', "test_fix_text.LogoNote.test_thumbnail_and_post_say_the_title_is_not_drawn"),
    ("G2: tracked pairs measured with ligatures", "    kw = _fkw(font, ligatures=False)\n    units = _clusters(text)",
     "    kw = _fkw(font)\n    units = _clusters(text)",
     "test_fix_text.TrackedShaping.test_tracked_pairs_are_measured_without_ligatures", {"needs": "raqm"}),
    ("G2: marks drawn apart from their letter", "        if out and (attach or joined):", "        if False:",
     "test_fix_text.TrackedShaping.test_a_letter_keeps_its_marks"),
    ("G2: tracked type drawn per code point", "    for c, a in zip(_clusters(text), _advances(font, text, tracking, space)):",
     "    for c, a in zip(list(text), _advances(font, text, tracking, space)):",
     "test_fix_text.TrackedShaping.test_a_letter_keeps_its_marks"),
    ("type-color-3: shaping scripts not detected", '        if (unicodedata.bidirectional(c) in ("R", "AL", "AN") or',
     '        if False and (unicodedata.bidirectional(c) in ("R", "AL", "AN") or',
     "test_fix_text.TrackedShaping.test_scripts_that_need_shaping_are_detected"),
    ("type-color-3: shaped line drawn tracked", "    if (not tracking and not space) or needs_shaping(text):",
     "    if (not tracking and not space):", "test_fix_text.TrackedShaping.test_scripts_that_need_shaping_are_drawn_whole"),
    ("type-color-3: shaped line measured tracked", "    if (tracking or space) and not needs_shaping(text):",
     "    if (tracking or space):", "test_fix_text.TrackedShaping.test_scripts_that_need_shaping_are_drawn_whole"),
    ("type-color-3: no note for an untracked script", "    elif shaped:\n        tracked =", "    elif False:\n        tracked =",
     "test_fix_text.TrackedShaping.test_render_notes_it_and_warns_without_raqm", {"needs": "raqm"}),
    ("type-color-3: no FriBiDi warning without raqm", "    if shaped and not LAYOUT_FEATURES:", "    if False:",
     "test_fix_text.TrackedShaping.test_render_notes_it_and_warns_without_raqm"),
    ("type-color-2: the dot is never checked", "    return [ch for ch in sorted(set(text)) if not ch.isspace()",
     "    return [ch for ch in sorted(set(text)) if not ch.isspace() and ch != \"\\u00b7\"",
     "test_fix_text.Glyphs.test_the_separator_dot_is_checked"),
    ("type-color-2: label font glyphs unchecked", "            for ch in missing_glyphs(lfont, text):", "            for ch in []:",
     "test_fix_text.Glyphs.test_label_box_kicker_and_subtitle_are_checked_in_the_label_font"),
    ("type-color-2: only the label box is checked", '    small = [("label box", j.box), ("kicker", j.kicker), ("subtitle", j.subtitle)]',
     '    small = [("label box", j.box)]', "test_fix_text.Glyphs.test_label_box_kicker_and_subtitle_are_checked_in_the_label_font"),
    ("type-color-4: no column warning", "        if over <= 1:\n            continue\n        done.add(name)",
     "        if True:\n            continue\n        done.add(name)", "test_fix_text.Column.test_a_long_label_box_warns_with_a_word_count"),
    ("type-color-4: column fix says split one word", '            fix = ("split it over more lines with \\\\n" if len(words) > 1',
     '            fix = ("split it over more lines with \\\\n" if True', "test_fix_text.Column.test_a_single_long_word_warns"),
    ("type-color-4: post column advice cuts words", '        elif post:\n            fix = "move it into --body',
     '        elif False:\n            fix = "move it into --body', "test_fix_text.Column.test_post_advice_keeps_the_words"),
    ("type-color-4: no three-line title", "            if len(words) >= 3 and _too_wide(font, lines, tracking_em * tsize, max_w):",
     "            if False:", "test_fix_text.Column.test_a_long_title_takes_three_lines_inside_the_column"),
    ("type-color-5: small type white on a light bg",
     "    return INK_DARK if (lum + 0.05) / (ink_lum + 0.05) > 1.05 / (lum + 0.05) else WHITE", "    return WHITE",
     "test_fix_text.LightBackground.test_kicker_and_subtitle_go_dark_on_a_light_panel"),
    ("type-color-5: small type ignores the ink", "    j.ink = ink_for(j.bg)", "    j.ink = WHITE",
     "test_fix_text.LightBackground.test_kicker_and_subtitle_go_dark_on_a_light_panel"),
    ("type-color-5: auto lightens on a light bg", "    end = 0.0 if is_light(bg) else 255.0", "    end = 255.0",
     "test_fix_text.LightBackground.test_auto_darkens_on_a_light_background"),
    ("type-color-5: auto white on a light bg", "        if sig is None and is_light(bg):", "        if False:",
     "test_fix_text.LightBackground.test_auto_darkens_on_a_light_background"),
    ("type-color-7: oldstyle fonts unflagged", "    if oldstyle_figures(font, digits):\n        if not LAYOUT_FEATURES:",
     "    if False:\n        if not LAYOUT_FEATURES:", "test_fix_text.Figures.test_without_raqm_lining_figures_need_fribidi"),
    ("type-color-7: tracked digits unflagged", "    if alone and oldstyle_figures(font, digits, context=False):",
     "    if False:", "test_fix_text.Figures.test_slab_numbers_warn_and_lining_fonts_do_not", {"needs": "raqm"}),
    ("type-color-7: tracking fix for digit-only lines",
     '        fix = "set --title-tracking 0" if all(any(c.isalpha() for c in ln) for ln in alone) else lining',
     '        fix = "set --title-tracking 0"', "test_fix_text.Figures.test_slab_numbers_warn_and_lining_fonts_do_not",
     {"needs": "raqm"}),
    ("type-color-8: label font case ignored", '    label_caps = j.label_caps = lentry["caps"] if lentry else True',
     "    label_caps = j.label_caps = True",
     "test_fix_text.LabelCase.test_gothic_label_font_is_not_upper_cased"),
    ("type-color-9: small type judged by font size", "    if cap < BIG_MIN_CAP * cv.u:\n        lines =",
     "    if font.size < 72 * cv.u:\n        lines =", "test_fix_text.SmallType.test_cap_height_not_font_size"),
    ("type-color-9: Title Case judged by its capitals", "    lower = not j.caps_ok and any(c.islower() for c in text)",
     "    lower = False", "test_fix_text.SmallType.test_cap_height_not_font_size"),
    ("visual-design-5: strokes unchecked", "    if stem >= BIG_MIN_STEM:\n        return", "    if True:\n        return",
     "test_fix_text.SmallType.test_light_weight_warns_on_its_strokes"),
    ("visual-design-5: stroke fix names no weight",
     "    heavier = _heavier_weight(j, font.size, letter, BIG_MIN_STEM * cv.w / float(shown))", "    heavier = None",
     "test_fix_text.SmallType.test_light_weight_warns_on_its_strokes"),
    # ---------------------------------------------------------------- v0.9.1 post (tests/test_fix_post.py)
    ("post-backdrop-1: long words never break",
     "        pieces = word_pieces(word) if text_width(font, word, 0) > max_w else [word]", "        pieces = [word]",
     "test_fix_post.LongWords.test_url_in_details_fits_the_4x5_column"),
    ("post-backdrop-1: links break before a letter only", "        if c in POST_BREAKS and nxt.isalnum():",
     "        if c in POST_BREAKS:", "test_fix_post.LongWords.test_word_pieces_break_links_without_hyphens"),
    ("post-backdrop-1: no break before a dot",
     '        if cur and c == "." and cur[-1] not in POST_BREAKS + "." and nxt.isalnum():', "        if False:",
     "test_fix_post.LongWords.test_word_pieces_break_links_without_hyphens"),
    ("post-backdrop-1: continued lines not recorded", "                if k and glued is not None:",
     "                if False:", "test_fix_post.LongWords.test_url_in_details_fits_the_4x5_column"),
    ("post-backdrop-1: the fit ignores the width", "        if wide and n < len(caps) - 1:", "        if False:",
     "test_fix_post.LongWords.test_an_unbreakable_word_shrinks_the_text_not_the_headline"),
    ("post-backdrop-1: the headline shrinks for a long word", "        cap = j.base if n == start else big_min",
     "        cap = j.base if n == 0 else big_min",
     "test_fix_post.LongWords.test_an_unbreakable_word_shrinks_the_text_not_the_headline"),
    ("post-backdrop-1: overflow says shorten it", "        if over:\n            _post_overflow(j, name, text, lines, boxes)",
     "        if False:\n            _post_overflow(j, name, text, lines, boxes)",
     "test_fix_post.LongWords.test_a_word_that_never_fits_is_named_and_kept"),
    ("post-backdrop-1: long-word fix never offers 4:5",
     '    return ("use --aspect 4:5, which has the widest column, or " + ask) if j.aspect != "4:5" else ask',
     "    return ask", "test_fix_post.LongWords.test_a_word_that_never_fits_is_named_and_kept"),
    ("post-backdrop-1: long-word fix offers 4:5 on 4:5",
     '    return ("use --aspect 4:5, which has the widest column, or " + ask) if j.aspect != "4:5" else ask',
     '    return ("use --aspect 4:5, which has the widest column, or " + ask)',
     "test_fix_post.LongWords.test_a_word_that_never_fits_is_named_and_kept"),
    ("post-backdrop-1: too-small warning ignores the long word", '    if pf.get("long"):', "    if False:",
     "test_fix_post.LongWords.test_a_long_word_that_sets_the_size_is_named"),
    ("post-backdrop-1: overflow not recorded",
     '                     "continued": list(glued), "overflow": over}', '                     "continued": list(glued), "overflow": []}',
     "test_fix_post.LongWords.test_a_word_that_never_fits_is_named_and_kept"),
    ("post-backdrop-2: 9:16 solid band stays at the bottom",
     '        if seen < SOLID_MIN_SEEN and j.anchor == "bottom" and not j.band_forced and j.band_scores is None:',
     "        if False:", "test_fix_post.ReelsSolid.test_long_message_moves_the_band_up"),
    ("post-backdrop-2: a forced band moves anyway",
     '        if seen < SOLID_MIN_SEEN and j.anchor == "bottom" and not j.band_forced and j.band_scores is None:',
     '        if seen < SOLID_MIN_SEEN and j.anchor == "bottom" and j.band_scores is None:',
     "test_fix_post.ReelsSolid.test_forced_bottom_band_warns"),
    ("post-backdrop-2: no small-photo warning",
     "        if seen < SOLID_MIN_SEEN:\n            info[\"warnings\"].append(\"the photo fills only",
     "        if False:\n            info[\"warnings\"].append(\"the photo fills only",
     "test_fix_post.ReelsSolid.test_forced_bottom_band_warns"),
    ("post-backdrop-2: any message moves the band up", "SOLID_MIN_SEEN = 0.20", "SOLID_MIN_SEEN = 0.95",
     "test_fix_post.ReelsSolid.test_short_message_keeps_the_bottom_band"),
    ("post-backdrop-2: the photo share counts the header",
     '    lo, hi = max(cv.h * REELS_SAFE["top"], (cv.h - grid_h) // 2), (cv.h + grid_h) // 2',
     "    lo, hi = 0, cv.h", "test_fix_post.ReelsSolid.test_long_message_moves_the_band_up"),
    ("post-backdrop-3: lost lines not counted", "    if hidden:\n        parts.append(", "    if False:\n        parts.append(",
     "test_fix_post.Overflow.test_lines_off_the_canvas_are_counted"),
    ("post-backdrop-4: the CLI omits the lines", "            for row in post_report_lines(pt):\n                print(row)",
     "            for row in []:\n                print(row)", "test_fix_post.CliReport.test_lines_are_printed"),
    ("post-backdrop-4: no mark for a continued word", '                s += " |+ " if i in pt[name]["continued"] else " | "',
     '                s += " | "', "test_fix_post.CliReport.test_lines_are_printed"),
    ("post-backdrop-4: no mark for an overflow line", '(" [does not fit]" if i in pt[name]["overflow"] else "")', '""',
     "test_fix_post.CliReport.test_overflow_is_marked_and_the_report_survives_a_narrow_console"),
    ("post-backdrop-4: the report crashes a narrow console",
     '    return [r.encode(enc, "backslashreplace").decode(enc) for r in rows]', "    return rows",
     "test_fix_post.CliReport.test_overflow_is_marked_and_the_report_survives_a_narrow_console",
     {"errors": ("UnicodeEncodeError",)}),
    ("post-backdrop-6: \\n in a path breaks the line",
     '    return " ".join(t if is_path(t) else t.replace("\\\\n", "\\n") for t in text.split(" "))',
     '    return text.replace("\\\\n", "\\n")', "test_fix_post.TypedText.test_backslash_n_in_a_path_is_text"),
    ("post-backdrop-6: only drive paths are kept",
     '        return drive or any(c == "\\\\" and tok[i + 1:i + 2] != "n" for i, c in enumerate(tok))', "        return drive",
     "test_fix_post.TypedText.test_backslash_n_in_a_path_is_text"),
    ("post-backdrop-7: details break at a dot inside a word",
     '        for part in [p.strip() for p in line.split(" · ") if p.strip()]:',
     '        for part in [p.strip() for p in line.split("·") if p.strip()]:',
     "test_fix_post.TypedText.test_dot_inside_a_word_is_not_a_separator"),
    ("post-backdrop-7: the word check drops every dot", '    return [w for w in post_newlines(text).split() if w != "·"]',
     '    return post_newlines(text).replace("·", " ").split()',
     "test_fix_post.TypedText.test_dot_inside_a_word_is_not_a_separator"),
    ("post-backdrop-9: idle note names the flags",
     'SOLID_IDLE_NOTE = "%s %s no effect with --backdrop solid: the type sits on an opaque panel"',
     'SOLID_IDLE_NOTE = "with --backdrop solid, --shade and --feather have no effect%.0s%.0s"',
     "test_fix_post.SolidIdleNote.test_channel_shade_is_not_called_a_flag"),
    ("post-backdrop-9: idle note names one setting", '        info["notes"].append(SOLID_IDLE_NOTE % (" and ".join(idle),',
     '        info["notes"].append(SOLID_IDLE_NOTE % (idle[0],', "test_fix_post.SolidIdleNote.test_api_names_both"),
    ("missing-glyphs-post: body glyphs unchecked",
     "        missing = missing_glyphs(load_font(j.lspec, 64), post_newlines(text))", "        missing = []",
     "test_fix_post.MissingGlyphs.test_missing_glyph_is_named"),
    # ---------------------------------------------------------------- v0.9.1 layout (tests/test_fix_layout.py)
    ("layout-1: the band never moves off the inset", "        _inset_anchor(j)\n", "",
     "test_fix_layout.TallInset.test_auto_band_moves_off_the_inset"),
    ("layout-1: tall inset fix never names --band",
     '    if cv.tall and j.layout == "single" and not j.subjects and j.backdrop != "solid":\n        flip =',
     '    if False:\n        flip =', "test_fix_layout.TallInset.test_forced_band_names_the_other_band_not_text_side_or_focus"),
    ("layout-1: 16:9 inset fix names no side", '    elif not cv.tall and j.layout == "single":\n        other =',
     '    elif False:\n        other =', "test_fix_layout.TallInset.test_wide_overlap_names_the_side_that_clears_it"),
    ("layout-1: no smaller --inset-box", "        if 0.25 <= k < 1 and 0 <= new[0] < new[2] <= 1 and 0 <= new[1] < new[3] <= 1:",
     "        if False:", "test_fix_layout.TallInset.test_inset_box_advice_when_no_band_clears_it"),
    ("layout-1: inset fix unconfirmed", "        if r is not None and _type_clear(r, contrast):\n            fix = \"use --band %s, which",
     "        if True:\n            fix = \"use --band %s, which",
     "test_fix_layout.TallInset.test_inset_box_advice_when_no_band_clears_it"),
    ("layout-1: overlap warning is the old text", '        j.info["warnings"].append("type overlaps the inset image: %s." % _inset_fix(j))',
     '        j.info["warnings"].append("type overlaps the inset image: use --text-side left or right.")',
     "test_fix_layout.TallInset.test_forced_band_names_the_other_band_not_text_side_or_focus"),
    ("layout-1: contrast over the inset says --focus",
     '    if "inset_rect" in j.info and overlaps(j.built["bbox"], j.info["inset_rect"]):\n        fix = _inset_fix(j)',
     '    if False:\n        fix = _inset_fix(j)',
     "test_fix_layout.TallInset.test_forced_band_names_the_other_band_not_text_side_or_focus"),
    ("layout-1: tall contrast never offers the other band", "            if r is not None and r[\"contrast\"] >= MIN_CONTRAST:",
     "            if False:", "test_fix_layout.TallContrastFix.test_each_case_names_its_own_fix"),
    ("layout-1: tall contrast always says --focus",
     '            fix = "use --focus to move the crop, or a darker image" if wider else "use a darker image"',
     '            fix = "use --focus to move the crop, or a darker image"',
     "test_fix_layout.TallContrastFix.test_each_case_names_its_own_fix"),
    ("layout-1: tall panels contrast says --focus", '    elif j.layout == "panels":\n        fix = "use darker captures"',
     '    elif j.layout == "panels":\n        fix = "use --focus to move the crop, or a darker image"',
     "test_fix_layout.TallContrastFix.test_each_case_names_its_own_fix"),
    ("layout-1: tall contrast hint is the old one", "                hint = _tall_contrast_fix(j)",
     '                hint = "use --focus to move the crop, or a darker image"',
     "test_fix_layout.TallContrastFix.test_each_case_names_its_own_fix"),
    ("layout-2: 9:16 cut-outs sized as before", "            target_h = max(target_h, reels_face_height(cv))\n",
     "            pass\n",
     "test_fix_layout.ReelsCutouts.test_cutout_height_puts_the_top_40_percent_above_the_caption_line"),
    ("layout-2: no face-under-caption warning", "        if cv.reels and py + s.height / 3.0 > cv.safe_box[3]:",
     "        if False:", "test_fix_layout.ReelsCutouts.test_two_cutouts_with_faces_in_the_caption_area_are_flagged"),
    ("layout-2: face fix never says one subject",
     "    if n > 1 and s.height * min(one / s.height, cv.w / s.width) >= need:", "    if False:",
     "test_fix_layout.ReelsCutouts.test_two_cutouts_with_faces_in_the_caption_area_are_flagged"),
    ("layout-2: face fix always says shorten", "    if room < need:\n        return (\"use --aspect 16:9",
     "    if True:\n        return (\"use --aspect 16:9", "test_fix_layout.ReelsCutouts.test_wide_head_and_shoulders_cutout_is_flagged"),
    ("layout-2: posts not detected from the canvas", "        post = cv.col_share == POST_COL_SHARE", "        post = False",
     "test_fix_layout.ReelsCutouts.test_post_room_warning_never_asks_to_cut_words"),
    ("layout-2: post face fix says shorten",
     '        return ("use --aspect 16:9, where the text sits beside the subject" if post\n'
     '                else "shorten the text so the cut-out can be larger")',
     '        return "shorten the text so the cut-out can be larger"',
     "test_fix_layout.ReelsCutouts.test_post_room_warning_never_asks_to_cut_words"),
    ("layout-3: panel type reaches the badge",
     "            max_w = min(max_w, 2 * (cv.timestamp_zone[0] - cv.px(12) - cv.w // 2))", "            pass",
     "test_fix_layout.PanelBadge.test_panel_titles_stay_clear_of_the_badge"),
    ("layout-3: badge fix for panels says shorten the text", '    if j.layout == "panels":\n        return "shorten the title or the label"',
     '    if False:\n        return "shorten the title or the label"', "test_fix_layout.PanelBadge.test_badge_warning_names_a_fix"),
    ("layout-3: badge fix never names the left side",
     '    return "use --text-side left" if j.text_side == "right" else "shorten the text"', '    return "shorten the text"',
     "test_fix_layout.PanelBadge.test_badge_warning_names_a_fix"),
    ("layout-4: out-of-view box gets no computed crop", "            elif _tall_crop_fix(j):\n                fix = _tall_crop_fix(j)",
     "            elif False:\n                fix = _tall_crop_fix(j)", "test_fix_layout.TallSubjectBoxCrop.test_box_too_high_for_the_safe_zone"),
    ("layout-4: shaded box gets no computed crop",
     '            shade_fix = overlap_fix = _tall_crop_fix(j) or "%s, or use another frame" % _shorten(j)',
     '            shade_fix = overlap_fix = "%s, or use another frame" % _shorten(j)',
     "test_fix_layout.TallSubjectBoxCrop.test_box_under_the_band"),
    ("layout-4: the crop never brings the band along", '            flip = {"band": anchor} if anchor != j.anchor else {}',
     "            flip = {}", "test_fix_layout.TallSubjectBoxCrop.test_box_in_the_caption_area_gets_a_crop_and_band"),
    ("layout-5: lower panel label hugs the band",
     "            raw = [y0 + cv.px(32), band[0] - h - cv.px(12), corner - h - cv.px(32), band[1] + cv.px(12)]",
     "            raw = [y0 + cv.px(32), band[0] - h - cv.px(12), band[1] + cv.px(12)]",
     "test_fix_layout.TallPanelLabels.test_lower_label_is_set_apart_from_the_type_block"),
    ("layout-5: a label near the band stays at the margin",
     "        if cv.tall and band is not None and chosen[1] < band[1] + near and chosen[3] > band[0] - near:",
     "        if False:", "test_fix_layout.TallPanelLabels.test_lower_label_is_set_apart_from_the_type_block"),
    ("layout-6: the caption area's shade counts",
     '    if cv.reels and anchor == "bottom" and band is not None and rect[3] > band[1]:', "    if False:",
     "test_fix_layout.ReelsCaptionShade.test_box_in_the_caption_area_gets_only_the_safe_zone_warning"),
    ("layout-7: the share is truncated", '                        % (round(100 * info["subject_box"]["inside"]), where, fix))',
     '                        % (int(100 * info["subject_box"]["inside"]), where, fix))',
     "test_fix_layout.OneNumber.test_safe_zone_share_is_rounded_like_the_report"),
    ("layout-8: --text-side ignored silently on tall", '        if text_side != "auto":  # the type is a band across the frame, never a column',
     "        if False:", "test_fix_layout.IgnoredFlags.test_text_side_on_tall_formats"),
    ("layout-8: --focus ignored silently on narrow art",
     '        if narrow and focus is not None and j.backdrop != "solid":  # compose_single shows narrow art whole',
     "        if False:", "test_fix_layout.IgnoredFlags.test_focus_on_narrow_art"),
    ("G3: the cut-out reference is stretched", "        behind = canvas.crop(vis)\n        tex = texture_ref.crop(vis) if",
     "        behind = canvas.crop(vis).resize(s.size)\n        tex = texture_ref.crop(vis) if",
     "test_fix_layout.CutoutReference.test_reference_is_the_visible_part_unstretched"),
    ("G3: the texture reference is stretched", "        tex = texture_ref.crop(vis) if texture_ref is not None else None",
     "        tex = texture_ref.crop(vis).resize(s.size) if texture_ref is not None else None",
     "test_fix_layout.CutoutReference.test_reference_is_the_visible_part_unstretched"),
    ("G4: the mirrored type keeps its height", "        dy = max(j.safe[1], min(int(cv.h * 0.5 - j.bh / 2), limit - j.bh)) - j.top",
     "        dy = 0", "test_fix_layout.MirroredSide.test_mirrored_boxes_match_the_real_other_side"),
    # ---------------------------------------------------------------- v0.9.1 io (tests/test_fix_io.py)
    ("image-pipeline-1: subtitle rows veto the bar", "        if flat.mean() < 0.25:  # subtitle lines may fill most of a bar",
     "        if flat.mean() < 0.6:  # subtitle lines may fill most of a bar",
     "test_fix_io.SubtitleBars.test_two_line_subtitles_in_239_bars_are_trimmed"),
    ("image-pipeline-1: one subtitle line vetoes a thin bar", "        if flat.mean() < 0.25:  # subtitle lines may fill most of a bar",
     "        if flat.mean() < 0.5:  # subtitle lines may fill most of a bar",
     "test_fix_io.SubtitleBars.test_one_subtitle_line_in_thin_20_bars_is_trimmed"),
    ("image-pipeline-1: a dark scene counts as bar", "        if n - last > max(2, 0.01 * len(med)):\n            n = last",
     "        if False:\n            n = last", "test_fix_io.SubtitleBars.test_dark_textured_picture_next_to_the_bar_is_kept"),
    ("image-pipeline-1: JPEG ringing is picture", "        if n - last > max(2, 0.01 * len(med)):\n            n = last",
     "        if n - last > 0:\n            n = last",
     "test_fix_io.SubtitleBars.test_jpeg_ringing_next_to_the_picture_still_trims_to_the_picture"),
    ("P1: a dark field around sparse content is a bar", "        if last > 0.4 * len(med):\n            sparse.append(axis)",
     "        if False:\n            sparse.append(axis)", "test_fix_io.SparseContentOnBlack.test_sparse_content_keeps_the_whole_frame"),
    ("P1: pristine row bars trim beside a dark field", '    if top and not ((pristine(rt, rb) and "cols" not in sparse)',
     "    if top and not ((pristine(rt, rb))", "test_fix_io.SparseContentOnBlack.test_sparse_content_keeps_the_whole_frame"),
    ("P1: pristine column bars trim beside a dark field", '    if left and not ((pristine(cl, cr) and "rows" not in sparse)',
     "    if left and not ((pristine(cl, cr))", "test_fix_io.SparseContentOnBlack.test_sparse_content_keeps_the_whole_frame"),
    ("P1: --no-trim ignored", "    if not trim or (crop is not None and tuple(crop) == (0, 0, 1, 1)):",
     "    if crop is not None and tuple(crop) == (0, 0, 1, 1):", "test_fix_io.NoTrim.test_no_trim_and_full_frame_crop_keep_the_frame"),
    ("P1: a full-frame crop still trims", "    if not trim or (crop is not None and tuple(crop) == (0, 0, 1, 1)):",
     "    if not trim:", "test_fix_io.NoTrim.test_no_trim_and_full_frame_crop_keep_the_frame"),
    ("P1: render drops trim", "    j.trim, j.face_check = trim, face_check\n", "    j.trim, j.face_check = True, face_check\n",
     "test_fix_io.NoTrim.test_no_trim_and_full_frame_crop_keep_the_frame"),
    ("P1: panels trimmed with --no-trim", "    if j.trim:\n        for i, p in enumerate(panels):",
     "    if True:\n        for i, p in enumerate(panels):", "test_fix_io.NoTrim.test_no_trim_keeps_panels_whole"),
    ("P1: no note on a trim", "        if kept != asked:  # say so", "        if False:  # say so",
     "test_fix_io.NoTrim.test_no_trim_and_full_frame_crop_keep_the_frame"),
    ("P1: CLI drops --no-trim", "trim=not args.no_trim,", "trim=True,", "test_fix_io.NoTrim.test_cli_no_trim"),
    ("image-pipeline-4: large images trimmed at full size", "    if g.width * g.height > TRIM_MAX_PIXELS:",
     "    if False:", "test_fix_io.SparseContentOnBlack.test_large_images_are_measured_on_a_reduced_copy"),
    ("image-pipeline-2: ICC profiles ignored", '    if not icc or ImageCms is None or img.mode not in ("RGB", "RGBA", "CMYK"):',
     "    if True:", "test_fix_io.ColorManagement.test_display_p3_photo_is_converted"),
    ("image-pipeline-2: cut-out colors unconverted", '        rgba = to_srgb(img.convert("RGBA"), icc)',
     '        rgba = img.convert("RGBA")', "test_fix_io.ColorManagement.test_cutout_and_logo_keep_alpha_and_convert"),
    ("image-pipeline-2: logo colors unconverted",
     'logo = to_srgb(ImageOps.exif_transpose(logo).convert("RGBA"), logo.info.get("icc_profile"))',
     'logo = ImageOps.exif_transpose(logo).convert("RGBA")',
     "test_fix_io.ColorManagement.test_cutout_and_logo_keep_alpha_and_convert"),
    ("image-pipeline-2: sRGB-tagged inputs reconverted",
     "            if np.abs(np.asarray(moved, np.int16) - np.asarray(cube, np.int16)).max() <= 1:", "            if False:",
     "test_fix_io.ColorManagement.test_srgb_tagged_and_untagged_stay_exact"),
    ("image-pipeline-3: JPEG XR not sniffed", '    if head[:3] == b"II\\xbc":', "    if False:",
     "test_fix_io.UnreadableFormats.test_jpeg_xr_capture_named_jpg"),
    ("image-pipeline-3: JPEG XR gets Pillow's error", '        if kind == "JPEG XR":\n            named',
     '        if False:\n            named', "test_fix_io.UnreadableFormats.test_jpeg_xr_capture_named_jpg"),
    ("image-pipeline-3: HEIC gets Pillow's error", '        if kind == "HEIF":\n            raise',
     '        if False:\n            raise', "test_fix_io.UnreadableFormats.test_heic_photo"),
    ("image-pipeline-4: Pillow's bomb limit applies", "    Image.MAX_IMAGE_PIXELS = None  # checked below",
     "    pass  # checked below", "test_fix_io.UnreadableFormats.test_photos_past_pillows_bomb_limit_open",
     {"errors": ("DecompressionBombError",)}),
    ("image-pipeline-4: the limit is not restored", "    finally:\n        Image.MAX_IMAGE_PIXELS = old",
     "    finally:\n        pass", "test_fix_io.UnreadableFormats.test_photos_past_pillows_bomb_limit_open"),
    ("image-pipeline-4: no plain message past the limit", "        if img.width * img.height > MAX_INPUT_PIXELS:",
     "        if False:", "test_fix_io.UnreadableFormats.test_photos_past_pillows_bomb_limit_open"),
    ("image-pipeline-5: no narrow-art warning", "    if share < NARROW_MIN_SHARE:", "    if False:",
     "test_fix_io.NarrowArt.test_sliver_warns_and_phone_capture_does_not"),
    ("image-pipeline-5: narrow-art check never runs", "    _check_narrow(j)\n", "",
     "test_fix_io.NarrowArt.test_sliver_warns_and_phone_capture_does_not"),
    ("image-pipeline-6: -o checked after the render",
     "        os.makedirs(folder, exist_ok=True)\n        fd, probe = tempfile.mkstemp(suffix=\".tmp\", dir=folder)\n"
     "        os.close(fd)\n        os.remove(probe)\n", "        pass\n",
     "test_fix_io.OutputChecks.test_bad_output_folder_fails_before_rendering"),
    ("image-pipeline-6: a folder named like an output", '    if taken:\n        ap.error("can\'t write %s: a folder',
     '    if False:\n        ap.error("can\'t write %s: a folder', "test_fix_io.OutputChecks.test_bad_output_folder_fails_before_rendering"),
    ("image-pipeline-7: -o keeps rarer image extensions",
     'IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".heif")',
     'IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")', "test_fix_io.OutputChecks.test_any_image_extension_on_o_is_replaced"),
    ("image-pipeline-8: piped output keeps the console encoding",
     '                stream.reconfigure(encoding="utf-8", errors="backslashreplace")',
     '                stream.reconfigure(errors="backslashreplace")', "test_fix_io.PipedOutput.test_wrote_lines_carry_the_real_name"),
    ("P3: -o guard compares paths, not files", "        if st.st_ino:\n            return (st.st_dev, st.st_ino)",
     "        if False:\n            return (st.st_dev, st.st_ino)", "test_fix_io.OutputChecks.test_hard_link_of_the_input_is_caught"),
    ("P3: files written in place", '        with open(tmp, "wb") as f:\n            f.write(data)\n        os.replace(tmp, path)',
     '        with open(path, "wb") as f:\n            f.write(data)', "test_fix_io.OutputChecks.test_writes_go_through_a_temp_file"),
    ("P3: a failed write leaves its temp file", "        try:\n            os.remove(tmp)\n        except OSError:\n            pass\n        raise",
     "        raise", "test_fix_io.OutputChecks.test_a_failed_write_leaves_the_old_file_and_no_temp"),
    ("P3: previews written in place", '    img.save(buf, "PNG")\n    write_file(path, buf.getvalue())',
     '    img.save(path, "PNG")', "test_fix_io.OutputChecks.test_writes_go_through_a_temp_file"),
    ("G5a: --no-system-fonts ignored", 'ap.add_argument("--no-system-fonts", dest="system_fonts", action="store_false", default=None,',
     'ap.add_argument("--no-system-fonts", dest="no_system_fonts", action="store_true", default=None,',
     "test_fix_io.NegatingFlags.test_flags_override_the_channel_file"),
    ("G5a: --no-logo-white ignored", 'ap.add_argument("--no-logo-white", dest="logo_white", action="store_false", default=None,',
     'ap.add_argument("--no-logo-white", dest="no_logo_white", action="store_true", default=None,',
     "test_fix_io.NegatingFlags.test_flags_override_the_channel_file"),
    ("post-backdrop-8: a 16:9 post copy is _phone.jpg", '    return "_1280.jpg" if mode == "post" else "_phone.jpg"',
     '    return "_phone.jpg"', "test_fix_io.WidePostFiles.test_export_by_mode"),
    ("post-backdrop-8: a 16:9 post gets YouTube tiles", '    previews = {"16:9": feed_preview if mode == "post" else preview_sheet,',
     '    previews = {"16:9": preview_sheet,', "test_fix_io.WidePostFiles.test_export_by_mode"),
    ("post-backdrop-8: a 16:9 post copy is the phone copy", '        if aspect == "16:9" and mode != "post":',
     '        if aspect == "16:9":', "test_fix_io.WidePostFiles.test_export_by_mode"),
    ("post-backdrop-8: the feed card is always 4:5", "    ratio = thumb.height / thumb.width  # 1.25 for 4:5",
     "    ratio = 1.25", "test_fix_io.WidePostFiles.test_export_by_mode"),
    ("post-backdrop-8: CLI exports posts as thumbnails", '            files = export(thumb, file_stem, aspect, s["mode"])',
     "            files = export(thumb, file_stem, aspect)", "test_fix_io.WidePostFiles.test_cli_post_at_16x9"),
    # ---------------------------------------------------------------- v0.9.1 shade (tests/test_fix_shade.py)
    ("visual-design-2: the default shade is fixed", "    if j.shade_auto and j.backdrop != \"solid\":\n        shade = _auto_shade(j)",
     "    if False:\n        shade = _auto_shade(j)", "test_fix_shade.AutoShade.test_dark_frame_gets_a_lighter_shade_than_the_fixed_one"),
    ("visual-design-2: the auto shade is the darkest", "    shade = hi / 100.0\n", "    shade = SHADE_MAX\n",
     "test_fix_shade.AutoShade.test_dark_frame_gets_a_lighter_shade_than_the_fixed_one"),
    ("visual-design-2: the auto shade aims at 4.5:1", "    want = [min(SHADE_TARGET, r) for r in ratios(SHADE_MAX)]",
     "    want = [min(4.5, r) for r in ratios(SHADE_MAX)]",
     "test_fix_shade.AutoShade.test_bright_frame_still_reaches_the_target"),
    ("visual-design-2: the auto shade may undercut the fixed one", "    want = [min(SHADE_TARGET, r) for r in ratios(SHADE_MAX)]",
     "    want = [min(SHADE_TARGET, r) - 1.5 for r in ratios(SHADE_MAX)]",
     "test_fix_shade.AutoShade.test_bright_frame_still_reaches_the_target"),
    ("visual-design-2: no floor under the auto shade", "    lo, hi = int(round(SHADE_MIN * 100)), int(round(SHADE_MAX * 100))",
     "    lo, hi = 0, int(round(SHADE_MAX * 100))",
     "test_fix_shade.AutoShade.test_never_less_contrast_than_the_fixed_shade_up_to_the_target"),
    ("visual-design-2: the auto shade keeps the fixed geometry", "        cv.shade_reach = cv.margin + SHADE_HOLD * j.bw",
     "        pass", "test_fix_shade.AutoShade.test_short_block_shades_less_of_the_frame"),
    ("visual-design-2: the fixed geometry moved", "        plateau, falloff = cv.margin + cv.col_w * 0.75, cv.w * 0.28",
     "        plateau, falloff = cv.margin + cv.col_w * 0.85, cv.w * 0.28",
     "test_fix_shade.AutoShade.test_fixed_geometry_is_unchanged"),
    ("visual-design-2: a channel shade turns auto", "shade_auto=shade is None)", "shade_auto=True)",
     "test_fix_shade.AutoShade.test_cli_default_is_auto_and_a_channel_shade_fixes_it"),
    ("visual-design-2: the CLI default is fixed", 'feather=0.36, shade=None,\n                title_font=None',
     'feather=0.36, shade=0.88,\n                title_font=None',
     "test_fix_shade.AutoShade.test_cli_default_is_auto_and_a_channel_shade_fixes_it"),
    ("visual-design-2: no auto note", '    j.info["notes"].append("shade %.2f (auto): the lightest',
     '    str("shade %.2f (auto): the lightest', "test_fix_shade.AutoShade.test_dark_frame_gets_a_lighter_shade_than_the_fixed_one"),
    ("visual-design-2: panels keep the fixed shade",
     '    if j.layout == "panels":  # both ends are composed now: the build mixes them (_backdrop)\n',
     '    if j.layout == "panels":  # both ends are composed now: the build mixes them (_backdrop)\n        return SHADE_MAX\n',
     "test_fix_shade.AutoShade.test_tall_band_and_panels_adapt_too"),
    ("visual-design-3: no dark-page floor", "    return dark_tone_floor(dark_tone_pixels(np.asarray(img.convert(\"RGB\").resize((128, 128)))))",
     "    return tuple(int(v) for v in dark_tone_pixels(np.asarray(img.convert(\"RGB\").resize((128, 128)))))",
     "test_fix_shade.DarkFloor.test_background_tone_is_lifted_off_the_dark_page_keeping_its_hue"),
    ("visual-design-3: the floor scales the color", "    out = np.minimum(np.ceil(c + hi), 255.0)",
     "    out = np.minimum(np.ceil(c * (1 + hi / max(1.0, float(c.min())))), 255.0)",
     "test_fix_shade.DarkFloor.test_background_tone_is_lifted_off_the_dark_page_keeping_its_hue"),
    ("visual-design-3: an explicit --bg is lifted", "    bg = rgb(bg) if bg else dark_tone(backgrounds[0])",
     "    bg = dark_tone_floor(rgb(bg)) if bg else dark_tone(backgrounds[0])",
     "test_fix_shade.DarkFloor.test_explicit_dark_bg_is_respected_with_a_note"),
    ("S2: no note for an edge lost on the dark page", "        if merged > 0.4:", "        if False:",
     "test_fix_shade.DarkFloor.test_explicit_dark_bg_is_respected_with_a_note"),
    ("S2: the note suggests the same --bg", "hexcolor(dark_tone_floor(j.bg))))", "hexcolor(j.bg)))",
     "test_fix_shade.DarkFloor.test_explicit_dark_bg_is_respected_with_a_note"),
    ("type-color-6: contrast pooled over the element", "    if n == 1 or not len(slices):", "    if True:",
     "test_fix_shade.PerSliceContrast.test_one_bright_letter_fails_the_element"),
    ("skill-docs-3: boxes not recorded", '    if boxes:\n        built["canvas"].info["boxes"] = boxes',
     '    if False:\n        built["canvas"].info["boxes"] = boxes', "test_fix_shade.PreviewBoxes.test_sheet_gets_a_boxes_tile"),
    ("skill-docs-3: boxes not outlined", "        d.rectangle(r, outline=BOX_COLORS.get(name, WHITE), width=2)",
     "        pass", "test_fix_shade.PreviewBoxes.test_sheet_gets_a_boxes_tile"),
    ("skill-docs-3: no boxes tile on the sheet", '    if thumb.info.get("boxes"):  # where --subject-box, --inset-box',
     '    if False:  # where --subject-box, --inset-box', "test_fix_shade.PreviewBoxes.test_sheet_gets_a_boxes_tile"),
    ("skill-docs-3: the feed card has no boxes panel",
     "    width = pad * 2 + sum(w for w, _ in widths) + 360 + gap * len(widths) + (360 + gap if boxes else 0)",
     "    width = pad * 2 + sum(w for w, _ in widths) + 360 + gap * len(widths)",
     "test_fix_shade.PreviewBoxes.test_inset_box_and_tall_previews"),
    ("skill-docs-3: the Reels sheet has no boxes panel", "    width = pad * 2 + pw * 2 + 160 + gap * 2 + (pw + gap if boxes else 0)",
     "    width = pad * 2 + pw * 2 + 160 + gap * 2", "test_fix_shade.PreviewBoxes.test_inset_box_and_tall_previews"),
    ("visual-design-6: no note for the 120 px badge", "    if hit:\n        info[\"notes\"].append(\"on the preview's 120 px tile",
     "    if False:\n        info[\"notes\"].append(\"on the preview's 120 px tile",
     "test_fix_shade.BadgeAt120.test_centered_panel_title_gets_the_note"),
    ("visual-design-6: posts get the badge note", '    if cv.tall or j.mode == "post":\n        return\n    tw, th',
     "    if cv.tall:\n        return\n    tw, th", "test_fix_shade.BadgeAt120.test_left_type_and_posts_get_no_note"),
    ("G6c: the picture is graded per build", "    if j.unshaded is None:\n        if j.backdrop != \"solid\" and cv.tall:",
     "    if True:\n        if j.backdrop != \"solid\" and cv.tall:", "test_fix_shade.GradeOnce.test_one_grade_per_render"),
    ("tests-ci-7: the dead constant is back", "SHADE_MIN, SHADE_MAX, SHADE_TARGET = 0.35, 0.88, 7.0\n",
     "SHADE_MIN, SHADE_MAX, SHADE_TARGET = 0.35, 0.88, 7.0\nMAX_LUMA_BEHIND_TYPE = 0.45\n",
     "test_fix_shade.DeadConstantAndVignette.test_max_luma_constant_is_gone"),
    ("tests-ci-7: the fade photo is not vignetted", "        graded = grade(img)\n        canvas = depth(graded, cv, push)",
     "        graded = grade(img, vignette=0)\n        canvas = depth(graded, cv, push)",
     "test_fix_shade.DeadConstantAndVignette.test_fade_photo_is_vignetted"),
    # ---------------------------------------------------------------- v0.9.1 verifier leftovers
    ("post text ignores the panel's ink", "box_first=True, extra=extra,\n                                                 ink=j.ink)",
     "box_first=True, extra=extra)", "test_fix_post.FollowUps.test_post_text_takes_the_light_panels_ink"),
    ("a light panel is told to go darker",
     'hint = ("the --bg panel is light: use a dark %s" % color_flag(name) if is_light(j.bg)',
     'hint = ("the --bg panel is light: use a dark %s" % color_flag(name) if False',
     "test_fix_post.FollowUps.test_post_text_takes_the_light_panels_ink"),
    ("panel labels always in caps", "text = name.upper() if caps else name", "text = name.upper()",
     "test_fix_text.LabelCase.test_panel_labels_follow_the_label_font_case"),
    ("the post title is told to shorten",
     'if j.mode == "post" and name not in ("body", "details"):  # body and details: _post_overflow',
     "if False:  # body and details: _post_overflow",
     "test_fix_post.FollowUps.test_post_title_out_of_frame_never_says_shorten_it"),
    ("the stock channel prints the post note", 'str(saved.get("aspect", "16:9")).replace(" ", "") != "16:9"',
     '"aspect" in saved', "test_fix_post.FollowUps.test_stock_channel_file_prints_no_post_note"),
    ("small-type ink by the black threshold",
     "    return INK_DARK if (lum + 0.05) / (ink_lum + 0.05) > 1.05 / (lum + 0.05) else WHITE",
     "    return INK_DARK if is_light(bg) else WHITE", "test_fix_text.InkCrossover.test_ink_is_the_higher_contrast_one"),
    ("upscale names an outside tool",'"higher." % (src.width, src.height, scale, cv.w, cv.h))',
     '"higher, or upscale with Real-ESRGAN first." % (src.width, src.height, scale, cv.w, cv.h))',
     "test_fix_io.UpscaleAdvice.test_upscale_warning_names_only_a_recapture"),
    # no --subject-box: the face check (find_faces, _check_found_faces)
    ("no-box: no face check", "    _check_found_faces(j)\n    _check_feed(j)", "    _check_feed(j)",
     "test_no_box_subject.FoundFace.test_black_flag_16x9_warns_and_names_the_box"),
    ("no-box: type over a face not checked", '        covered = view["glyphs"] > 0.02 or view["under_type"] > 0.10',
     "        covered = False", "test_no_box_subject.FoundFace.test_black_flag_16x9_warns_and_names_the_box"),
    ("no-box: the named box rounds inward", "    lo = [np.floor(v * 1000 + 1e-6) / 1000 for v in box[:2]]",
     "    lo = [np.round(v * 1000) / 1000 for v in box[:2]]",
     "test_no_box_subject.FoundFace.test_black_flag_16x9_warns_and_names_the_box"),
    ("no-box: face out of frame not checked", '        cut = view["inside"] < FACE_IN_FRAME', "        cut = False",
     "test_no_box_subject.FoundFace.test_vertical_crops_warn"),
    ("no-box: crop through the person not checked", "        side = _crop_edge(j, view)", "        side = None",
     "test_no_box_subject.FoundFace.test_vertical_crops_warn"),
    ("no-box: shade over a face not checked",
     '                  and (view["shade_vis"] < FACE_SHADE_VIS or view["shade_heavy"] > FACE_SHADE_HEAVY))',
     "                  and False)", "test_no_box_subject.FoundFace.test_shade_alone_is_reported"),
    ("no-box: a clear face gets no note", "        elif not (cut or shaded or covered or edged):",
     "        elif False:", "test_no_box_subject.FoundFace.test_clear_face_gets_a_note"),
    ("no-box: the face is not remapped through --crop",
     '        b = remap_box(face["box"], j.given_size, j.kept_px)', '        b = face["box"]',
     "test_no_box_subject.FoundFace.test_found_face_lands_where_its_box_would"),
    ("no-box: CLI ignores --no-face-check", "face_check=not args.no_face_check)", "face_check=True)",
     "test_no_box_subject.FoundFace.test_cli_warns_and_no_face_check_skips_it"),
    ("no-box: the check overrides an explicit box",
     "if not j.face_check or j.subject_box_given is not None or j.layout", "if not j.face_check or j.layout",
     "test_no_box_subject.FaceCheckScope.test_explicit_box_wins"),
    ("no-box: face_check=False ignored", "    j.trim, j.face_check = trim, face_check", "    j.trim = trim",
     "test_no_box_subject.FaceCheckScope.test_off_switch"),
    ("no-box: a crop through the person under --focus",
     '    if j.focus is not None:\n        return None\n    rect, mapper = view["rect"], j.built["mapper"]',
     '    rect, mapper = view["rect"], j.built["mapper"]',
     "test_no_box_subject.FaceCheckScope.test_focus_and_crop_are_the_users_choice"),
    ("no-box: a face --crop cut away is reported", "            continue  # --crop (or a bar) cut it away",
     "            pass  # --crop (or a bar) cut it away",
     "test_no_box_subject.FaceCheckScope.test_focus_and_crop_are_the_users_choice"),
    ("no-box: cut-outs get the face check", 'j.layout != "single" or j.subjects:', 'j.layout != "single":',
     "test_no_box_subject.FaceCheckScope.test_cutouts_and_panels_keep_their_own_checks"),
    ("no-box: any window is a face", "FACE_MIN_HITS = 18  #", "FACE_MIN_HITS = 1  #",
     "test_no_box_subject.NoFalseAlarms.test_scenery_stays_silent"),
    ("no-box: a panorama is scanned whole", ", (FACE_PIXELS / float(W0 * H0)) ** 0.5)", ")",
     "ReviewInputs.test_huge_wide_source_renders_tall_without_resizing_everything"),
    ("no-box: the preview skips the face",
     '    boxes += [("face found", f["rect"]) for f in info.get("faces", []) if f["outlined"]]\n', "",
     "test_no_box_subject.FacePreview.test_boxes_tile_outlines_the_face"),
    ("no-box: every face found is outlined", 'for f in info.get("faces", []) if f["outlined"]]',
     'for f in info.get("faces", [])]', "test_no_box_subject.FacePreview.test_clear_face_is_not_outlined"),
]


def load(path):
    spec = importlib.util.spec_from_file_location("thumb", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.FONT_ROOT, mod._catalog = FONTS, None  # a temp copy must still find the bundled fonts
    mod.FACE_MODEL, mod._face_model = FACE_MODEL, None  # ... and the face model
    return mod


def test_id(test):
    """A test id names its module ("test_backdrop.Class.test"); a bare "Class.test" is in test_thumb."""
    return test if test.startswith("test_") else "test_thumb." + test


def run(mod, test):
    sys.modules["thumb"] = mod
    for name in TEST_MODULES:  # re-import so each module's `thumb` is this copy
        sys.modules.pop(name, None)
    for name in TEST_MODULES:
        importlib.import_module(name).thumb = mod
    return unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromName(test_id(test)))


def last_line(trace):
    return trace.strip().splitlines()[-1]


def error_name(line):
    return line.split(":")[0].split(".")[-1].strip()


def verdict(r, expected=()):
    """(kind, detail) for one mutant's test run: caught, expected, invalid, crash, skipped or missed."""
    if r.failures:
        return "caught", ""
    errors = [last_line(t[1]) for t in r.errors]
    if any(error_name(e) in BROKEN for e in errors):
        return "invalid", next(e for e in errors if error_name(e) in BROKEN)
    if errors and all(error_name(e) in expected for e in errors):
        return "expected", errors[0]
    if errors:
        return "crash", errors[0]
    if r.skipped and r.testsRun == len(r.skipped):
        return "skipped", r.skipped[0][1]
    return "missed", ""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    names = [m[0] for m in MUTANTS]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        print("mutant names must be unique: %s" % ", ".join(dup))
        return 1
    if "--list" in argv:
        for m in MUTANTS:
            print("%-52s %s" % (m[0], test_id(m[3])))
        return 0
    chosen = [m for m in MUTANTS if not argv or any(a in m[0] for a in argv)]
    if not chosen:
        print("no mutant matches %s" % " ".join(argv))
        return 1
    os.environ.pop("CT_FAST", None)  # every named test runs here, the slow ones too
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    src = open(SCRIPT, encoding="utf-8").read()
    real = load(SCRIPT)
    bad, skipped_tests = 0, set()
    for test in sorted({m[3] for m in chosen}):
        r = run(real, test)
        if r.failures or r.errors or r.testsRun == 0:
            print("baseline FAILS (fix the code or the test first): %s" % test)
            for t in (r.failures + r.errors)[:1]:
                print("    " + last_line(t[1])[:200])
            bad += 1
        elif r.skipped and len(r.skipped) == r.testsRun:
            skipped_tests.add(test)
    if bad:
        return 1
    counts = dict.fromkeys(("caught", "expected", "crash", "invalid", "skipped", "missed", "missing"), 0)
    with tempfile.TemporaryDirectory() as tmp:
        for i, m in enumerate(chosen):
            name, old, new, test = m[:4]
            opts = m[4] if len(m) > 4 else {}
            expected = tuple(opts.get("errors", ()))
            n = src.count(old)
            if n != 1:
                print("%-52s TEXT NOT FOUND (%d matches): update this mutant" % (name, n))
                counts["missing"] += 1
                continue
            if test in skipped_tests:
                print("%-52s SKIPPED: %s skips on this machine" % (name, test))
                counts["skipped"] += 1
                continue
            if opts.get("needs") == "raqm" and not real.LAYOUT_FEATURES:
                print("%-52s SKIPPED: needs Pillow's raqm layout" % name)
                counts["skipped"] += 1
                continue
            path = os.path.join(tmp, "thumb_%d.py" % i)
            with open(path, "w", encoding="utf-8") as f:
                f.write(src.replace(old, new))
            try:
                mod = load(path)
            except Exception as e:  # a mutant that doesn't even load says nothing about its test
                kind, detail = "invalid", "the copy doesn't load: %s: %s" % (type(e).__name__, e)
            else:
                kind, detail = verdict(run(mod, test), expected)
            counts[kind] += 1
            if kind == "caught":
                print("%-52s caught" % name)
            elif kind == "expected":
                print("%-52s caught by the old error (%s)" % (name, detail[:60]))
            elif kind == "crash":
                print("%-52s CRASH, not an assertion (%s): make the mutant run the old code to the end"
                      % (name, detail[:70]))
            elif kind == "invalid":
                print("%-52s INVALID: %s" % (name, detail[:90]))
            elif kind == "skipped":
                print("%-52s SKIPPED: %s" % (name, detail[:60]))
            else:
                print("%-52s NOT CAUGHT by %s" % (name, test))
    total = len(chosen)
    print("%d/%d mutants caught by an assertion, %d by the old error itself; %d crashed before the assertion, "
          "%d invalid, %d not caught, %d with missing source text, %d skipped on this machine"
          % (counts["caught"], total, counts["expected"], counts["crash"], counts["invalid"], counts["missed"],
             counts["missing"], counts["skipped"]))
    return 1 if counts["crash"] or counts["invalid"] or counts["missed"] or counts["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
