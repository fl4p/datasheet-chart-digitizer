"""Mutation check for the RDS(on)-vs-VGS review tests.

Each mutant switches ONE review fix off (by monkeypatching the module-level
function or constant that implements it) and runs the tests written for that
fix. A mutant must make at least one of its tests fail; a mutant that leaves
them all passing means the tests do not guard the fix. The baseline (no
mutant) must pass everything.

Usage (from the repo root):
    PYTHONPATH=src:tests .venv/bin/python tools/rds_vgs_mutation_check.py [LOG]
"""

from __future__ import annotations

import contextlib
import inspect
import io
import sys
import time
import unittest
from unittest.mock import patch

import numpy as np

from datasheet_chart_digitizer import rdson_gate_voltage as rgv
from datasheet_chart_digitizer import rdson_gate_voltage_axes as axes
from datasheet_chart_digitizer import rdson_gate_voltage_report as report
from datasheet_chart_digitizer import rdson_gate_voltage_traces as traces
from datasheet_chart_digitizer import rdson_spec_table as spec

import test_rdson_gate_voltage_golden as golden
import test_rdson_gate_voltage_review as review

T = "test_rdson_gate_voltage_review."


def _old_clip(points, rect):
    """v1: out-of-frame vertices dropped, crossing segments lost."""
    x0, y0, x1, y1 = rect
    runs, current = [], []
    for x, y in points:
        if x0 <= x <= x1 and y0 <= y <= y1:
            current.append((x, y))
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return [run for run in runs if len(run) >= 2]


def _old_long_enough(track, width, height):
    xs = [p[0] for p in track["points"]]
    ys = [p[1] for p in track["points"]]
    x_span, y_span = max(xs) - min(xs), max(ys) - min(ys)
    return x_span >= traces.MIN_CURVE_SPAN_FRACTION * width or (y_span >= 0.25 * height and x_span >= 0.02 * width)


def _old_binding_reasons(curves, labels):
    reasons = []
    keys = {k for c in curves for k in ("temperature_c", "id_a") if k in c["parameter_binding"]}
    for curve in curves:
        for key in keys:
            if curve.get(key) is None:
                reasons.append(f"curve_{curve['curve_index']}_{key}_unknown")
    return reasons


_real_readouts = report.readouts
_real_owned_values = spec._owned_values
_real_header = report._header_lines


def _readouts_ignoring_gaps(points, log_y, gaps=None, *args, **kwargs):
    return _real_readouts(points, log_y, None, *args, **kwargs)


class _AllNc(dict):
    """v2 legend: every missing readout shown as n/c."""

    def get(self, key, default=None):
        return "n/c"


def _header_first_four(row, panel, width_px=None):
    lines = _real_header(row, panel, width_px)
    return lines[:2] + [(text[:110], color) for text, color in lines[2:6]]


class _source_mutant:
    """Patch `module.name` with a copy of its source where each `old` -> `new`.

    For decisions written inline (a comparison, a literal, an order of
    steps) that no constant exposes. The copy runs in a snapshot of the
    module's namespace, so it calls the real helpers. Every chart-class
    module that imported the same function object gets the mutant too
    (rdson_gate_voltage imports `readouts` and `validate_against_table` by
    name; patching only their home module would leave production unmutated).
    """

    def __init__(self, module, name: str, *pairs: tuple[str, str]):
        original = getattr(module, name)
        source = inspect.getsource(original)
        for old, new in pairs:
            if source.count(old) != 1:
                raise AssertionError(f"mutant anchor for {name} found {source.count(old)}x: {old!r}")
            source = source.replace(old, new)
        scope = dict(vars(module))
        exec(compile(source, f"<mutant {name}>", "exec"), scope)
        holders = [m for m in (rgv, report, traces, axes, spec) if getattr(m, name, None) is original]
        self._patches = [patch.object(m, name, scope[name]) for m in holders]
        self._stack = None

    def __enter__(self):
        self._stack = contextlib.ExitStack()
        for item in self._patches:
            self._stack.enter_context(item)
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


# Codex round 3 (codex-review-r3/scratch/own_mutants_extended.py), same behaviour
def _eroded_readouts(points, log_y, gaps=None, end_tolerance_v=0.0, *args, **kwargs):
    return _real_readouts(points, log_y, [(a + end_tolerance_v, b - end_tolerance_v) for a, b in gaps or []],
                          end_tolerance_v, *args, **kwargs)


_real_curves = rgv._curves


def _contacts_called_gaps(*args, **kwargs):
    curves, reasons, refusal = _real_curves(*args, **kwargs)
    for curve in curves:
        kinds = curve.get("gap_kinds")
        if kinds:
            kinds["gap"] = sorted(kinds["gap"] + kinds["annotation_contact"])
            kinds["annotation_contact"] = []
    reasons = [r.replace("_annotation_contacts (", "_gaps (").replace(
        "points pulled off the curve by a touching arrow/label were removed", "no curve ink traced there")
        for r in reasons]
    return curves, reasons, refusal


_real_drop_orphans = traces._drop_orphan_ends


def _char_clipped(text, color, width_px=None):
    """v3 overlay text: 118-character slices, cut mid-word."""
    return [("    " * bool(i) + text[i:i + 118], color) for i in range(0, max(1, len(text)), 118)]


R3 = "RoundThreeTests."
L = "RoundThreeLateTests."
B = "BoundaryTests."


MUTANTS = {
    "clip_drops_crossing_segments (Codex #1)": (
        [patch.object(traces, "_clip_polyline", _old_clip)],
        ["ClippingTests"]),
    "no_filled_piece_join (Codex #3, FDP8870)": (
        [patch.object(traces, "_boxes_touch", lambda *a, **k: False)],
        ["CoverageTests"]),
    "erase_every_ocr_box (Codex #2 knee, #3 WSR)": (
        [patch.object(rgv, "_is_texty", lambda *a, **k: True)],
        ["LeaderTests.test_rq3e180aj_serves_the_18a_branch_and_knee_and_no_leader_jog",
         "RasterCoverageHonestyTests"]),
    "no_leader_cutter (Codex #2, Opus B)": (
        [patch.object(traces, "_split_leader_runs", lambda t: [t])],
        ["LeaderTests", "GapTests.test_rq6e080aj_has_no_chord_across_the_steep_branch"]),
    "readouts_ignore_gaps (Opus A, R2-2)": (
        [patch.object(report, "readouts", _readouts_ignoring_gaps),
         patch.object(rgv, "readouts", _readouts_ignoring_gaps)],
        ["GapTests"]),
    "no_gap_listing (Opus A)": (
        [patch.object(rgv, "GAP_PX", 1e9)],
        ["GapTests"]),
    "no_usable_flag (Opus E)": (
        [patch.object(rgv, "USABLE_MIN_SPAN_FRACTION", 0.0)],
        ["UsableFlagTests"]),
    "early_axis_aligned_rule_filter (Opus D)": (
        [patch.object(traces, "_is_full_span_rule", traces._is_rule)],
        ["SteepTopTests"]),
    "label_kind_dropped (Codex #4)": (
        [patch.object(traces, "temperature_kind", lambda sub, prefixed: "unspecified")],
        ["TemperatureKindTests", "RoundTwoTests.test_r2_1_single_curve_keeps_the_printed_kind"]),
    "table_heading_kind_dropped (Codex #4)": (
        [patch.object(spec, "_HEADING_TEMPERATURE_RE", __import__("re").compile(r"(?!x)x"))],
        ["TemperatureKindTests.test_ti_chart_prints_tc_and_the_table_ta"]),
    "approximate_id_counts_as_exact (Codex #5)": (
        [patch.object(report, "EXACT_ID_TOLERANCE", 1.0)],
        ["ValidationScopeTests.test_approximate_current_alone_does_not_verify"]),
    "unparsed_cells_ignored (Codex #6)": (
        [patch.object(spec, "_owned_values", lambda line, header: (_real_owned_values(line, header)[0], {}))],
        ["ValidationScopeTests.test_malformed_max_cell_stays_null_and_is_reported"]),
    "open_ends_claim_not_on_chart (Codex #3)": (
        [patch.object(rgv, "_open_ends", lambda trace, plot: (False, False))],
        ["RasterCoverageHonestyTests"]),
    "single_curve_kind_not_attached (R2-1)": (
        [patch.object(traces, "_attach_temperature_kinds", lambda *a, **k: None)],
        ["RoundTwoTests.test_r2_1_single_curve_keeps_the_printed_kind"]),
    "no_reason_for_missing_temperature (R2-1)": (
        [patch.object(rgv, "_binding_reasons", _old_binding_reasons)],
        ["RoundTwoTests.test_r2_1_missing_temperature_is_a_reason"]),
    "leader_cutter_drops_steep_branch (R2-3)": (
        [patch.object(traces, "_long_enough", _old_long_enough)],
        ["LeaderTests.test_rq6e080aj_keeps_the_8a_steep_branch"]),
    "legend_prints_n/c_for_every_state (R2-4)": (
        [patch.object(report, "_STATE_SHORT", _AllNc())],
        ["RoundTwoTests.test_r2_4_legend_distinguishes_readout_states"]),
    "header_shows_four_reasons (R2-5)": (
        [patch.object(report, "_header_lines", _header_first_four)],
        ["RoundTwoTests.test_r2_5_header_shows_every_reason"]),
    "no_frame_seating (R2-6)": (
        [patch.object(axes, "_seat_on_outer_tick_rules", lambda plot, *a: plot)],
        ["RoundTwoTests.test_r2_6_fdp8870_frame_sits_on_the_printed_frame"]),
    "no_contact_bump_removal (R2-7)": (
        [patch.object(traces, "_remove_contact_bumps", lambda track: None)],
        ["RoundTwoTests.test_r2_7_wsr3090_points_are_not_pulled_onto_the_arrows"]),
    "untraced_ink_called_gap (R2-8)": (
        [patch.object(rgv, "_ink_connects", lambda *a: False)],
        ["RoundTwoTests.test_r2_8_unsampled_stretch_on_continuous_ink_is_an_untraced_section"]),
    "orphan_edge_point_kept (R2-8)": (
        [patch.object(traces, "_drop_orphan_ends", lambda points, erased: (list(points), []))],
        ["RoundTwoTests.test_r2_8_continuous_ink_is_not_called_a_gap"]),
    "floating_text_fragment_kept (Codex #3)": (
        [patch.object(traces, "_floating_flat_fragment", lambda *a: False)],
        ["RasterCoverageHonestyTests"]),
    # ---- Codex round 3: the three survivors of round 2 ----------------------------
    "codex_gap_bounds_eroded_one_pixel (R3-1)": (
        [patch.object(report, "readouts", _eroded_readouts), patch.object(rgv, "readouts", _eroded_readouts)],
        [R3 + "test_r3_1a_readouts_refuse_the_interior_edges_of_an_unread_interval", "GapTests"]),
    "codex_annotation_contact_mislabeled_gap (R3-1)": (
        [patch.object(rgv, "_curves", _contacts_called_gaps)],
        [R3 + "test_r3_1b_wsr3090_arrow_contacts_are_typed_and_cover_every_removal"]),
    "codex_readouts_get_only_kind_gap (R3-1)": (
        [_source_mutant(rgv, "_curves", ('readouts(points, log_y, curve["gaps"],',
                                         'readouts(points, log_y, curve["gap_kinds"]["gap"],'))],
        [R3 + "test_r3_1c_production_readouts_receive_every_unread_interval",
         R3 + "test_r3_1c_a_readout_inside_an_untraced_section_is_refused"]),
    # ---- round-3 fixes ---------------------------------------------------------------
    "contact_needs_a_pixel_jump (R3-2)": (
        [_source_mutant(rgv, "_curves", ("if b[2] - a[2] <= GAP_PX and not contact:", "if b[2] - a[2] <= GAP_PX:"))],
        [R3 + "test_r3_2_contact_between_close_neighbours_opens_an_unread_interval",
         R3 + "test_r3_1b_wsr3090_arrow_contacts_are_typed_and_cover_every_removal"]),
    "overlay_breaks_on_pixel_jumps_only (R3-2)": (
        [patch.object(report, "_breaks_between", lambda curve, i, pts: pts[i + 1, 0] - pts[i, 0] > 2.5)],
        [R3 + "test_r3_2_contact_between_close_neighbours_opens_an_unread_interval"]),
    "contact_checked_after_ink (gap-kind precedence)": (
        [_source_mutant(rgv, "_curves", (
            '''            if contact:
                kinds["annotation_contact"].append(span)
            elif trace.method == "raster" and _ink_connects(gray, a[2:], b[2:]):
                kinds["untraced_section"].append(span)''',
            '''            if trace.method == "raster" and _ink_connects(gray, a[2:], b[2:]):
                kinds["untraced_section"].append(span)
            elif contact:
                kinds["annotation_contact"].append(span)'''))],
        [R3 + "test_r3_1b_wsr3090_arrow_contacts_are_typed_and_cover_every_removal"]),
    "stubs_dropped_after_admission (R3-3, v3 order)": (
        [_source_mutant(traces, "_admit_tracks", (
            '''        track["points"], track["dropped_stubs"] = _drop_orphan_ends(track["points"], erased_cols)
''', ""), (
            '''    kept = _drop_duplicates(kept)
''',
            '''    for t in kept:
        t["points"], t["dropped_stubs"] = _drop_orphan_ends(t["points"], erased_cols)
    kept = _drop_duplicates(kept)
'''))],
        [R3 + "test_r3_3_the_v3_leader_junction_track_is_not_admitted",
         R3 + "test_r3_3_every_admitted_raster_track_passes_on_its_own_points"]),
    "steep_branch_erased_as_rule (R3-4)": (
        [patch.object(rgv, "_verified_rules", lambda gray, lines, plot, ticks, vertical: tuple(lines))],
        [R3 + "test_r3_4_a_steep_branch_is_not_erased_as_a_grid_rule",
         R3 + "test_r3_4_rule_verification_on_the_real_pixels"]),
    "rule_dark_fraction_0.5 (R3-4)": (
        [patch.object(rgv, "RULE_DARK_FRACTION", 0.5)],
        [R3 + "test_r3_4_rule_verification_on_the_real_pixels", R3 + "test_r3_4_a_steep_branch_is_not_erased_as_a_grid_rule"]),
    "rule_dark_fraction_0.95_both (R3-4)": (
        [patch.object(rgv, "RULE_DARK_FRACTION", 0.95), patch.object(rgv, "RULE_LATTICE_DARK_FRACTION", 0.95)],
        [R3 + "test_r3_4_rule_verification_on_the_real_pixels", R3 + "test_r3_4_a_steep_branch_is_not_erased_as_a_grid_rule"]),
    "no_lattice_admission (R3-4)": (
        [patch.object(rgv, "RULE_LATTICE_DARK_FRACTION", 1.01)],
        [R3 + "test_r3_4_rule_verification_on_the_real_pixels", R3 + "test_r3_4_a_steep_branch_is_not_erased_as_a_grid_rule"]),
    "stub_points_not_recorded (R3-5)": (
        [patch.object(traces, "_drop_orphan_ends",
                      lambda points, erased: (_real_drop_orphans(points, erased)[0], []))],
        [R3 + "test_r3_5_dropped_stub_points_are_recorded_and_are_on_ink"]),
    "usable_ignores_ink_to_frame (R3-6)": (
        [patch.object(rgv, "_ink_reaches_frame", lambda *a: False)],
        [R3 + "test_r3_6_twin_branches_get_the_same_flag_from_their_ink"]),
    "ink_always_reaches_frame (R3-6)": (
        [patch.object(rgv, "_ink_reaches_frame", lambda *a: True)],
        ["UsableFlagTests"]),
    "overlay_text_char_clipped (R3-7)": (
        [patch.object(report, "_wrapped", _char_clipped)],
        [R3 + "test_r3_7_overlay_text_is_word_wrapped_and_never_clipped"]),
    "tick_source_clipped_40 (R3-7)": (
        [_source_mutant(report, "_header_lines", ("get('tick_source', '-')}", "get('tick_source', '-')[:40]}"))],
        [R3 + "test_r3_7_overlay_text_is_word_wrapped_and_never_clipped"]),
    "leader_needs_8_points_before (round-3 leader)": (
        [_source_mutant(traces, "_split_leader_runs", ("steep_before = i >= 2 and slope(max(0, i - 8), i)",
                                                       "steep_before = i >= 8 and slope(i - 8, i)"))],
        ["LeaderTests.test_rq3e180aj_serves_the_18a_branch_and_knee_and_no_leader_jog"]),
    # ---- boundary and kind decisions --------------------------------------------------
    "GAP_PX_1.5": ([patch.object(rgv, "GAP_PX", 1.5)], [B + "test_gap_threshold_is_between_two_and_three_pixels"]),
    "GAP_PX_3.5": ([patch.object(rgv, "GAP_PX", 3.5)], [B + "test_gap_threshold_is_between_two_and_three_pixels"]),
    "USABLE_MIN_SPAN_0.28": ([patch.object(rgv, "USABLE_MIN_SPAN_FRACTION", 0.28)], ["UsableFlagTests"]),
    "USABLE_MIN_SPAN_0.32": ([patch.object(rgv, "USABLE_MIN_SPAN_FRACTION", 0.32)], ["UsableFlagTests"]),
    "TYP_TOL_0.10": ([patch.object(report, "TYP_RELATIVE_TOLERANCE", 0.10)],
                     [B + "test_irlb8748_real_anchors_are_each_consistent", B + "test_typ_tolerance_is_twenty_percent"]),
    "TYP_TOL_0.35": ([patch.object(report, "TYP_RELATIVE_TOLERANCE", 0.35)],
                     [B + "test_ao3400a_real_anchors_are_each_inconsistent", B + "test_typ_tolerance_is_twenty_percent"]),
    "TYP_TOL_0.19": ([patch.object(report, "TYP_RELATIVE_TOLERANCE", 0.19)], [B + "test_typ_tolerance_is_twenty_percent"]),
    "TYP_TOL_0.21": ([patch.object(report, "TYP_RELATIVE_TOLERANCE", 0.21)], [B + "test_typ_tolerance_is_twenty_percent"]),
    "MAX_OVERSHOOT_0.04": ([patch.object(report, "MAX_OVERSHOOT_TOLERANCE", 0.04)], [B + "test_max_overshoot_tolerance_is_five_percent"]),
    "MAX_OVERSHOOT_0.06": ([patch.object(report, "MAX_OVERSHOOT_TOLERANCE", 0.06)], [B + "test_max_overshoot_tolerance_is_five_percent"]),
    "max_check_off": (
        [_source_mutant(report, "validate_against_table",
                        ("if value > row.max_mohm * (1 + MAX_OVERSHOOT_TOLERANCE) + slack:", "if False:"))],
        [B + "test_max_overshoot_tolerance_is_five_percent"]),
    "EXACT_ID_TOL_0.01": ([patch.object(report, "EXACT_ID_TOLERANCE", 0.01)], [B + "test_exact_drain_current_is_within_two_percent"]),
    "EXACT_ID_TOL_0.03": ([patch.object(report, "EXACT_ID_TOLERANCE", 0.03)], [B + "test_exact_drain_current_is_within_two_percent"]),
    "ID_RATIO_lo_0.70": ([patch.object(report, "ID_MATCH_RATIO", (0.70, 1.34))], [B + "test_drain_current_evaluation_window"]),
    "ID_RATIO_lo_0.80": ([patch.object(report, "ID_MATCH_RATIO", (0.80, 1.34))], [B + "test_drain_current_evaluation_window"]),
    "ID_RATIO_hi_1.30": ([patch.object(report, "ID_MATCH_RATIO", (0.75, 1.30))], [B + "test_drain_current_evaluation_window"]),
    "ID_RATIO_hi_1.40": ([patch.object(report, "ID_MATCH_RATIO", (0.75, 1.40))], [B + "test_drain_current_evaluation_window"]),
    "TJ_MATCH_0.5": ([patch.object(report, "TJ_MATCH_C", 0.5)], [B + "test_temperature_match_window"]),
    "TJ_MATCH_1.5": ([patch.object(report, "TJ_MATCH_C", 1.5)], [B + "test_temperature_match_window"]),
    "exact_outranks_inconsistent (verdict kind)": (
        [_source_mutant(report, "validate_against_table", (
            '''    elif any(a["verdict"] == "inconsistent" for a in evaluable):
        overall, reason = "inconsistent", "a curve contradicts its table row"
    elif exact:
        overall, reason = "verified", f"{len(exact)} table row(s) consistent at the table's own drain current"''',
            '''    elif exact:
        overall, reason = "verified", f"{len(exact)} table row(s) consistent at the table's own drain current"
    elif any(a["verdict"] == "inconsistent" for a in evaluable):
        overall, reason = "inconsistent", "a curve contradicts its table row"'''))],
        [B + "test_an_inconsistent_anchor_outranks_an_exact_consistent_one"]),
    "approximate_counts_as_verified (verdict kind)": (
        [_source_mutant(report, "validate_against_table", ('exact = [a for a in evaluable if a.get("condition_match") == "exact"]',
                                                           'exact = list(evaluable)'))],
        ["ValidationScopeTests.test_approximate_current_alone_does_not_verify"]),
    "readout_end_tolerance_2px": (
        [_source_mutant(report, "readouts", ("vgs[0] - end_tolerance_v <= target", "vgs[0] - 2 * end_tolerance_v <= target"))],
        [B + "test_readout_end_tolerance_is_one_pixel"]),
    "readout_end_tolerance_0px": (
        [_source_mutant(report, "readouts", ("vgs[0] - end_tolerance_v <= target", "vgs[0] <= target"))],
        [B + "test_readout_end_tolerance_is_one_pixel"]),
    "gap_bounds_inclusive": (
        [_source_mutant(report, "readouts", ("g[0] < target_read < g[1]", "g[0] <= target_read <= g[1]"))],
        [R3 + "test_r3_1a_readouts_refuse_the_interior_edges_of_an_unread_interval"]),
    "lost_end_called_not_on_chart (readout-state kind)": (
        [_source_mutant(report, "readouts", ('"not_in_extracted_trace" if lost else "not_on_chart"', '"not_on_chart"'))],
        ["RasterCoverageHonestyTests", "RoundTwoTests.test_r2_4_legend_distinguishes_readout_states"]),
    "no_ink_called_untraced (gap kind)": (
        [patch.object(rgv, "_ink_connects", lambda *a: True)],
        [B + "test_a_stretch_with_no_ink_is_a_gap_not_an_untraced_section"]),
    "BUMP_CORE_2.0": ([patch.object(traces, "BUMP_CORE_PX", 2.0)],
                      [R3 + "test_r3_1b_contact_removal_touches_only_the_wsr3090_arrows"]),
    "BUMP_CORE_4.5": ([patch.object(traces, "BUMP_CORE_PX", 4.5)],
                      ["RoundTwoTests.test_r2_7_wsr3090_points_are_not_pulled_onto_the_arrows",
                       R3 + "test_r3_1b_wsr3090_arrow_contacts_are_typed_and_cover_every_removal"]),
    "BUMP_EDGE_0.5": ([patch.object(traces, "BUMP_EDGE_PX", 0.5)],
                      [R3 + "test_r3_1b_contact_removal_touches_only_the_wsr3090_arrows",
                       R3 + "test_r3_1b_wsr3090_arrow_contacts_are_typed_and_cover_every_removal",
                       "RoundTwoTests.test_r2_7_wsr3090_points_are_not_pulled_onto_the_arrows"]),
    "BUMP_EDGE_3.0": ([patch.object(traces, "BUMP_EDGE_PX", 3.0)],
                      [R3 + "test_r3_1b_wsr3090_arrow_contacts_are_typed_and_cover_every_removal",
                       "RoundTwoTests.test_r2_7_wsr3090_points_are_not_pulled_onto_the_arrows"]),
    "LEADER_MIN_RUN_4": ([patch.object(traces, "LEADER_MIN_RUN_PX", 4)],
                         ["LeaderTests", "RasterCoverageHonestyTests"]),
    "LEADER_MIN_RUN_16": ([patch.object(traces, "LEADER_MIN_RUN_PX", 16)], ["LeaderTests"]),
    "LEADER_STEEP_0.5": ([patch.object(traces, "LEADER_STEEP_SLOPE", 0.5)], ["LeaderTests", "RasterCoverageHonestyTests"]),
    "LEADER_FLAT_0.05": ([patch.object(traces, "LEADER_FLAT_SLOPE", 0.05)], ["LeaderTests"]),
    "steep_admission_4_points": (
        [_source_mutant(traces, "_long_enough", ("len(xs) >= 5", "len(xs) >= 4"))],
        [R3 + "test_r3_3_every_admitted_raster_track_passes_on_its_own_points",
         R3 + "test_r3_3_steep_admission_needs_five_points"]),
    "steep_admission_6_points": (
        [_source_mutant(traces, "_long_enough", ("len(xs) >= 5", "len(xs) >= 6"))],
        [R3 + "test_r3_3_steep_admission_needs_five_points"]),
    "steep_admission_10_points": (
        [_source_mutant(traces, "_long_enough", ("len(xs) >= 5", "len(xs) >= 10"))],
        ["LeaderTests.test_rq6e080aj_keeps_the_8a_steep_branch"]),
    "steep_admission_height_0.40": (
        [_source_mutant(traces, "_long_enough", ("y_span >= 0.25 * height", "y_span >= 0.40 * height"))],
        ["LeaderTests.test_rq6e080aj_keeps_the_8a_steep_branch", R3 + "test_r3_6_twin_branches_get_the_same_flag_from_their_ink"]),
    "STUB_MAX_POINTS_1": ([patch.object(traces, "STUB_MAX_POINTS", 1)], [R3 + "test_r3_5_stub_length_and_remainder_boundaries"]),
    "STUB_MAX_POINTS_3": ([patch.object(traces, "STUB_MAX_POINTS", 3)], [R3 + "test_r3_5_stub_length_and_remainder_boundaries"]),
    "STUB_KEEP_MIN_2": ([patch.object(traces, "STUB_KEEP_MIN", 2)], [R3 + "test_r3_5_stub_length_and_remainder_boundaries"]),
    "STUB_KEEP_MIN_4": ([patch.object(traces, "STUB_KEEP_MIN", 4)], [R3 + "test_r3_5_stub_length_and_remainder_boundaries"]),
    "seat_reach_10px (R2-6 boundary)": (
        [_source_mutant(axes, "_seat_on_outer_tick_rules", ("(tick - edge) * inward <= 15", "(tick - edge) * inward <= 10"))],
        ["RoundTwoTests.test_r2_6_fdp8870_frame_sits_on_the_printed_frame"]),
    "no_rule_bridging": (
        [patch.object(traces, "_bridge_erased_rules", lambda points, erased: (list(points), 0))],
        ["GapTests", "RoundTwoTests", R3 + "test_r3_4_a_steep_branch_is_not_erased_as_a_grid_rule"]),
    "erase_no_ocr_box": (
        [patch.object(rgv, "_is_texty", lambda *a, **k: False)],
        ["LeaderTests", "RasterCoverageHonestyTests", "RoundTwoTests"]),
    # ---- R3-8..R3-14 (Fab's v3 overlay inspection) --------------------------------
    "tick_origin_always_text_layer (R3-8)": (
        [patch.object(rgv, "_tick_origins", lambda axis, plot, orientation, sources: ["text_layer"] * len(axis.ticks))],
        [L + "test_r3_8_image_chart_ticks_are_not_credited_to_the_text_layer"]),
    "locator_ocr_named_text_layer (R3-8, v3 label)": (
        [patch.object(rgv, "_LOCATOR_SOURCE_NAMES", {"tesseract_fallback": "text_layer", "text_layer+tesseract_panel": "text_layer"})],
        [L + "test_r3_8_image_chart_ticks_are_not_credited_to_the_text_layer"]),
    "no_tick_completion (R3-9)": (
        [patch.object(rgv, "_complete_ticks", lambda calibration, *a: (calibration, "off"))],
        [L + "test_r3_9_rq3e110aj_uses_every_printed_tick"]),
    "TICK_COMPLETION_MAX_SHIFT_0.5 (R3-9)": (
        [patch.object(rgv, "TICK_COMPLETION_MAX_SHIFT_PX", 0.5)],
        [L + "test_r3_9_rq3e110aj_uses_every_printed_tick"]),
    "span_always_inside (R3-9)": (
        [patch.object(rgv, "_span_state", lambda *a: {"state": "inside"})],
        [L + "test_r3_9_readouts_below_the_used_ticks_are_flagged"]),
    "span_anchor_tolerance_5px (R3-9)": (
        [_source_mutant(rgv, "_span_state", ("if step and deviation <= 1.0:", "if step and deviation <= 5.0:"))],
        [L + "test_r3_9_readouts_below_the_used_ticks_are_flagged"]),
    "span_anchor_tolerance_0px (R3-9)": (
        [_source_mutant(rgv, "_span_state", ("if step and deviation <= 1.0:", "if step and deviation <= 0.0 - 1:"))],
        [L + "test_r3_9_readouts_below_the_used_ticks_are_flagged"]),
    "unanchored_not_a_reason (R3-9)": (
        [_source_mutant(rgv, "_flag_calibration_span", ('''            if span["state"] == "outside_unanchored":
                reasons.append(f"curve_''', '''            if False:
                reasons.append(f"curve_'''))],
        [L + "test_r3_9_readouts_below_the_used_ticks_are_flagged"]),
    "ticks_not_marked (R3-10)": (
        [_source_mutant(report, "_axis_bands", ('''        marks["y"].append(tick.value)
''', ""))],
        [L + "test_r3_10_every_used_tick_is_marked_outside_the_datasheet_crop"]),
    "no_max_diagnostics (R3-11)": (
        [patch.object(report, "_max_diagnostics", lambda *a: [])],
        [L + "test_r3_11_brcs020n03ra_curve_above_table_max_is_recorded_not_judged"]),
    "max_diagnostic_ignores_bound_temperature (R3-11)": (
        [_source_mutant(report, "_max_diagnostics", ("if temperature is not None and abs(temperature - row.temperature_c) > TJ_MATCH_C:", "if False:"))],
        [L + "test_r3_11_an_evaluated_or_differently_bound_curve_is_not_a_diagnostic"]),
    "no_direct_curve_labels (R3-12)": (
        [patch.object(report, "_place_curve_labels", lambda *a: [])],
        [L + "test_r3_12_legend_row_and_direct_label_per_curve"]),
    "labels_ignore_ink (R3-12)": (
        [patch.object(report, "LABEL_CLEARANCE_PX", -10000)],
        [L + "test_r3_12_legend_row_and_direct_label_per_curve", L + "test_r3_12_direct_labels_never_sit_on_curve_ink"]),
    "legend_without_temperature_kind (R3-12)": (
        [patch.object(report, "temperature_text", lambda curve: "T")],
        [L + "test_r3_12_legend_row_and_direct_label_per_curve", L + "test_r3_12_unknown_labels_are_spelled_out"]),
    "no_coincidence_marking (R3-13)": (
        [patch.object(rgv, "_mark_coincident", lambda curves, calibration: [curve.setdefault("coincident_with", []) and None for curve in curves] and [])],
        [L + "test_r3_13_fdp8870_coincidence_is_recorded_both_ways"]),
    "COINCIDENT_PX_40 (R3-13)": (
        [patch.object(rgv, "COINCIDENT_PX", 40.0)],
        [L + "test_r3_13_fdp8870_coincidence_is_recorded_both_ways"]),
    "COINCIDENT_MIN_PX_huge (R3-13)": (
        [patch.object(rgv, "COINCIDENT_MIN_PX", 10000)],
        [L + "test_r3_13_fdp8870_coincidence_is_recorded_both_ways"]),
    "coincident_drawn_solid (R3-13b)": (
        [_source_mutant(report, "_draw_curves", ("if over and (p0[0] // 10) % 2:", "if False:"))],
        [L + "test_r3_13_both_coincident_curves_stay_visible"]),
    "dark_palette_colour (R3-14)": (
        [patch.object(report, "_COLORS", ((130, 0, 75),) + report._COLORS[1:])],
        [L + "test_r3_14_palette_is_bright_against_black_ink"]),
    "no_halo (R3-14)": (
        [_source_mutant(report, "_draw_curves", ("cv2.line(body, p0, p1, (255, 255, 255), 7, cv2.LINE_AA)", "pass"),
                        ("cv2.circle(body, (int(round(x)), int(round(y))), 5, (255, 255, 255), -1, cv2.LINE_AA)", "pass"))],
        [L + "test_r3_14_traces_have_white_halos_over_black_ink"]),
}


# Mutants that change NO output on any of the 15 real panels (checked by
# digitizing all five raster panels with and without each and diffing every
# point, readout, flag and reason; the vector panels never reach this code).
# They cannot be killed by a real-data test; they are run and reported, and
# must keep surviving -- if one starts to be killed, a panel now depends on
# that boundary and it moves into MUTANTS.
EQUIVALENT_ON_REAL_DATA = {
    # The run builder only extends a run while |dy| <= 1.5 px, so a run of
    # >= 8 px has slope <= 0.19 anyway: 0.15 -> 0.4 matters only for 8-10 px
    # runs rising 1.2-1.5 px next to a steep stretch. None on any panel.
    "LEADER_FLAT_0.4": ([patch.object(traces, "LEADER_FLAT_SLOPE", 0.4)], ["LeaderTests", "RasterCoverageHonestyTests"]),
    # Three real flat runs sit next to a 1.0-2.0 px/px stretch (RQ3E180AJ
    # x 505-528 at y 236, steep after 1.42; BRCS020N03RA x 428-438 and
    # 774-784, steep before 1.25). All three are on tracks refused later
    # (floating/straight/length) whether or not the run is cut.
    "LEADER_STEEP_2.0": ([patch.object(traces, "LEADER_STEEP_SLOPE", 2.0)], ["LeaderTests"]),
}


def run(names: list[str]) -> unittest.TestResult:
    """The mutant's targeted tests PLUS every human-verified golden panel
    (Fab's v3 check, tests/fixtures/rds_vgs_golden): each mutant is also
    checked against the goldens."""
    review._CACHE.clear()
    review._CAPTURE.clear()
    golden._CACHE.clear()
    suite = unittest.defaultTestLoader.loadTestsFromNames([T + n for n in names])
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(golden.GoldenTests))
    stream = io.StringIO()
    return unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)


def main() -> int:
    log = open(sys.argv[1], "w") if len(sys.argv) > 1 else sys.stdout
    all_names = sorted({n for _patches, names in MUTANTS.values() for n in names})
    start = time.time()
    base = run(all_names)
    print(f"BASELINE (no mutant): ran {base.testsRun}, failures {len(base.failures)}, errors {len(base.errors)}",
          file=log, flush=True)
    for test, trace in base.failures + base.errors:
        print(f"  BASELINE FAIL {test.id()}\n{trace}", file=log)
    # a skipped golden (PDF missing or hash changed) is NOT a pass
    for test, why in base.skipped:
        print(f"  BASELINE SKIP (counts as failure) {test.id()}: {why}", file=log)
    survived = 0
    for label, (patches, names) in MUTANTS.items():
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            result = run(names)
        killed = [t.id().rsplit(".", 2)[-2] + "." + t.id().rsplit(".", 1)[-1] for t, _ in result.failures + result.errors]
        killed = [k.replace("GoldenTests.test_golden_", "GOLDEN:") for k in killed]
        verdict = "KILLED" if killed else "SURVIVED"
        survived += not killed
        print(f"{verdict:8} {label}: ran {result.testsRun}; failing: {killed}", file=log, flush=True)
    unexpected = 0
    for label, (patches, names) in EQUIVALENT_ON_REAL_DATA.items():
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            result = run(names)
        killed = bool(result.failures or result.errors)
        unexpected += killed
        print(f"{'KILLED?!' if killed else 'EQUIV':8} {label} (equivalent on all 15 real panels): ran {result.testsRun}",
              file=log, flush=True)
    print(f"mutants {len(MUTANTS)}, survived {survived}; equivalent-on-real-data {len(EQUIVALENT_ON_REAL_DATA)} "
          f"(unexpectedly killed {unexpected}); seconds {time.time() - start:.0f}", file=log, flush=True)
    return 1 if survived or unexpected or base.failures or base.errors or base.skipped else 0


if __name__ == "__main__":
    np.seterr(all="ignore")
    raise SystemExit(main())
