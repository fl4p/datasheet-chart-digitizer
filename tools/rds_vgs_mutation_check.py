"""Mutation check for the RDS(on)-vs-VGS review tests.

Each mutant switches ONE review fix off (by monkeypatching the module-level
function or constant that implements it) and runs the tests written for that
fix. A mutant must make at least one of its tests fail; a mutant that leaves
them all passing means the tests do not guard the fix. The baseline (no
mutant) must pass everything.

Usage (from the repo root):
    PYTHONPATH=src:tests .venv/bin/python tools/rds_vgs_mutation_check.py [LOG] \
        [--jobs N] [--since-commit REV] [--only SUBSTRING ...]

Mutants run N at a time (default: cores - 2), each in its own process on a
snapshot of src/ tests/ tools/ taken at start; --jobs 1 is the sequential,
in-process run. The log lines and the summary line are the same either way.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np

from datasheet_chart_digitizer import rdson_gate_voltage as rgv
from datasheet_chart_digitizer import rdson_gate_voltage_axes as axes
from datasheet_chart_digitizer import rdson_gate_voltage_labels as labels
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
        holders = [m for m in (rgv, report, traces, axes, spec, labels) if getattr(m, name, None) is original]
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
F4 = "RoundFourTests."
B = "BoundaryTests."
F5 = "RoundFiveTests."
F6 = "RoundSixTests."


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
        # since F4-1 the R2-8 test no longer sees the lone edge point (the head
        # is traced to the frame over it); the stub rule is pinned by R3-5
        ["RoundTwoTests.test_r2_8_continuous_ink_is_not_called_a_gap",
         R3 + "test_r3_5_dropped_stub_points_are_recorded_and_are_on_ink",
         R3 + "test_r3_5_stub_length_and_remainder_boundaries"]),
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
    # ---- round 4 (Fab's review of the v4 overlays) ---------------------------------
    "no_row_tracking (F4-1)": (
        [patch.object(traces, "extend_steep_heads", lambda traces_, *a: traces_)],
        [F4 + "test_f4_1_steep_heads_reach_the_top_frame"]),
    "row_tracker_samples_rule_rows (F4-1)": (
        [_source_mutant(traces, "_track_up", ("if 0 <= cy < len(erased_rows) and erased_rows[cy]:", "if False:"))],
        [F4 + "test_f4_1_steep_heads_reach_the_top_frame", F4 + "test_f4_1_row_traced_points_are_on_ink"]),
    "row_tracker_follows_band_centre (F4-1/F4-4)": (
        [patch.object(traces, "_dark_cores", lambda profile, offset: [offset + 0.5 * (len(profile) - 1)])],
        [F4 + "test_f4_4_two_printed_curves_are_two_complete_curves", F4 + "test_f4_1_steep_heads_reach_the_top_frame",
         F4 + "test_f4_4_rq3e110aj_pair_is_two_lines_side_by_side"]),
    "untraced_head_not_stated (F4-1)": (
        [_source_mutant(rgv, "_curves", ('reasons.append(f"curve_{index}_head_not_traced_to_frame (printed ink continues to the frame; "',
                                         'print(f"curve_{index}_head_not_traced_to_frame (printed ink continues to the frame; "'))],
        [F4 + "test_f4_1_an_untraced_head_is_stated_plainly"]),
    "no_raster_leaders (F4-3)": (
        [patch.object(rgv, "raster_leaders", lambda *a, **k: ([], []))],
        [F4 + "test_f4_3_wsr3090_temperatures_follow_the_arrows", F4 + "test_f4_3_rq3e180aj_ids_bound_by_their_leaders"]),
    "leader_bends_with_the_curve (F4-3)": (
        [_source_mutant(traces, "_follow_straight", ("tip, last_run", "tip, last_run"),
                        ("p = centre + ((origin - centre) @ axis + t) * axis", "p = (tip if miss == 0 else centre) + axis"))],
        [F4 + "test_f4_3_wsr3090_temperatures_follow_the_arrows"]),
    "no_tail_ocr (F4-3)": (
        [patch.object(rgv, "_ocr_leader_tail", lambda *a: None)],
        [F4 + "test_f4_3_wsr3090_temperatures_follow_the_arrows"]),
    "touching_lines_not_ambiguous (F4-3)": (
        [_source_mutant(traces, "raster_leaders", ("ambiguous=len(touched) > 1", "ambiguous=False"))],
        [F4 + "test_f4_3_a_leader_ending_in_touching_lines_names_neither"]),
    "tip_measured_to_samples (F4-3)": (
        [_source_mutant(traces, "_point_to_trace", ('if trace.method == "raster" and len(trace.points_px) > 1:', "if False:"))],
        [F4 + "test_f4_3_rq3e180aj_ids_bound_by_their_leaders"]),
    "stacked_pair_read_as_one_line (F4-4)": (
        [patch.object(traces, "_column_half", lambda gray, point, upper, erased_rows: point)],
        [F4 + "test_f4_4_rq3e110aj_pair_is_two_lines_side_by_side"]),
    "no_branch_grouping (F4-4)": (
        [patch.object(traces, "group_branches", lambda traces_, plot: traces_)],
        [F4 + "test_f4_4_two_printed_curves_are_two_complete_curves"]),
    "tail_head_samples_lost (F4-4)": (
        [_source_mutant(traces, "group_branches", ("if j in tails else []", "if False else []"))],
        []),
    "ticks_not_drawn (F4-2, R3-10 reverted to the v3 style)": (
        [patch.object(report, "draw_axis_ticks", lambda *a, **k: None)],
        [L + "test_f4_2_every_used_tick_is_drawn_in_the_v3_style_on_the_plot"]),
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
    "no_nesting_equal_widths (F4-5, R3-13b)": (
        [patch.object(report, "NESTED_STEP_PX", 0)],
        [L + "test_r3_13_both_coincident_curves_stay_visible", F4 + "test_f4_5_fdp8870_both_curves_show_everywhere_with_one_style",
         F4 + "test_f4_5_nesting_holds_on_every_multi_curve_panel"]),
    "nesting_reversed (F4-5)": (
        [_source_mutant(report, "line_widths", ("(len(order) - 1 - rank)", "rank"))],
        [F4 + "test_f4_5_fdp8870_both_curves_show_everywhere_with_one_style",
         F4 + "test_f4_5_nesting_holds_on_every_multi_curve_panel"]),
    "dark_palette_colour (R3-14)": (
        [patch.object(report, "_COLORS", ((130, 0, 75),) + report._COLORS[1:])],
        [L + "test_r3_14_palette_is_bright_against_black_ink"]),
    "no_halo (R3-14)": (
        [_source_mutant(report, "_draw_curves",
                        ('_stroke(body, curve, (255, 255, 255), widths[curve["curve_index"]] + 2)', "pass"))],
        [L + "test_r3_14_traces_have_white_halos_over_black_ink"]),
    # ---- round 5 (Fab's review of the v5 overlays) ---------------------------------
    "no_id_order_rule (F5-1)": (
        [patch.object(traces, "bind_by_order_rule", lambda *a, **k: [])],
        [F5 + "test_f5_1_ids_bound_by_the_order_rule", F5 + "test_f5_1_table_rows_become_evaluable"]),
    "order_rule_reversed (F5-1)": (
        [_source_mutant(traces, "bind_by_order_rule", ("ordered = sorted(traces, key=lambda t: above[id(t)])",
                                                       "ordered = sorted(traces, key=lambda t: -above[id(t)])"))],
        [F5 + "test_f5_1_ids_bound_by_the_order_rule", F5 + "test_f5_1_the_binding_follows_the_ink_not_the_index"]),
    "order_rule_ignores_label_count (F5-1)": (
        [_source_mutant(traces, "bind_by_order_rule", ("if len(printed) != len(traces) or len(traces) < 2:",
                                                       "if len(traces) < 2 or len(printed) < 2:"))],
        [F5 + "test_f5_1_mismatched_label_count_stays_unknown"]),
    "order_rule_overrides_bound_curves (F5-1)": (
        [_source_mutant(traces, "bind_by_order_rule", (
            'if any(t.params.get(key) is not None or t.binding.get(key) != "unbound" for t in traces):', "if False:"))],
        [F5 + "test_f5_1_a_curve_bound_by_other_evidence_is_not_overridden"]),
    "order_rule_ignores_other_parameter (F5-1)": (
        [patch.object(traces, "_shares_other_parameter", lambda *a: None)],
        [F5 + "test_f5_1_varying_temperature_blocks_the_id_rule"]),
    "ORDER_MARGIN_PX_2.5 (F5-1)": ([patch.object(traces, "ORDER_MARGIN_PX", 2.5)], [F5 + "test_f5_1_separation_margin_is_three_pixels"]),
    "ORDER_MARGIN_PX_3.5 (F5-1)": ([patch.object(traces, "ORDER_MARGIN_PX", 3.5)], [F5 + "test_f5_1_separation_margin_is_three_pixels"]),
    "ORDER_MIN_RUN_4 (F5-1)": ([patch.object(traces, "ORDER_MIN_RUN", 4)], [F5 + "test_f5_1_separation_needs_five_columns"]),
    "ORDER_MIN_RUN_6 (F5-1)": ([patch.object(traces, "ORDER_MIN_RUN", 6)], [F5 + "test_f5_1_separation_needs_five_columns"]),
    "ORDER_MIN_RUN_1 (F5-1: head flips count)": (
        [patch.object(traces, "ORDER_MIN_RUN", 1)],
        [F5 + "test_f5_1_separation_needs_five_columns", F5 + "test_f5_1_crossings"]),
    "separation_ignores_margin (F5-1: never-separating curves)": (
        [_source_mutant(traces, "separated_runs", ("sign = 1 if d >= ORDER_MARGIN_PX else -1 if d <= -ORDER_MARGIN_PX else 0",
                                                   "sign = 1 if d >= 0 else -1"))],
        [F5 + "test_f5_1_never_separating_curves_stay_unknown", F5 + "test_f5_1_separation_margin_is_three_pixels"]),
    "id_crossing_allowed (F5-1)": (
        [_source_mutant(traces, "pair_order", ('if key == "id_a" and changes:', "if False:"))],
        [F5 + "test_f5_1_crossings"]),
    "temperature_decided_by_first_run (F5-3)": (
        [_source_mutant(traces, "pair_order", ("return signs[-1], evidence", "return signs[0], evidence"))],
        [F5 + "test_f5_1_crossings"]),
    "temperature_crossings_unlimited (F5-3)": (
        [_source_mutant(traces, "pair_order", ("if changes > 1:", "if False:"))],
        [F5 + "test_f5_1_crossings"]),
    "no_id_order_check (F5-1 swapped labels)": (
        [patch.object(traces, "_id_order_check", lambda *a: [])],
        [F5 + "test_f5_1_swapped_leader_ids_are_caught"]),
    "id_order_check_backwards (F5-1 swapped labels)": (
        [_source_mutant(traces, "_id_order_check", ("if higher_id is not higher_rds:", "if higher_id is higher_rds:"))],
        [F5 + "test_f5_1_swapped_leader_ids_are_caught", F5 + "test_f5_1_ids_bound_by_the_order_rule"]),
    "legend_hides_order_rule (F5-1)": (
        [patch.object(report, "_ORDER_RULE_NOTE", {})],
        [F5 + "test_f5_1_legend_names_the_order_rule", F5 + "test_f5_3_brcs_free_labels_bind_by_temperature_order"]),
    "no_legend_box_reader (F5-3)": (
        [patch.object(rgv, "read_legend_boxes", lambda *a, **k: [])],
        [F5 + "test_f5_3_rq3e180aj_box_temperature_is_read", F5 + "test_f5_3_rohm_boxes_give_the_kind"]),
    "box_reading_does_not_replace_words (F5-3)": (
        [_source_mutant(rgv, "_add_condition_labels", (
            "out = [l for l in out if not (x0 <= 0.5 * (l.x0 + l.x1) <= x1 and y0 <= 0.5 * (l.y0 + l.y1) <= y1)]", "pass"))],
        [F5 + "test_f5_3_rohm_boxes_give_the_kind"]),
    "no_subscript_reader (F5-3)": (
        [patch.object(labels, "_read_subscript", lambda *a: (None, ""))],
        [F5 + "test_f5_3_rohm_boxes_give_the_kind"]),
    "subscript_not_lowered_accepted (F5-3)": (
        [_source_mutant(labels, "_read_subscript", ("lowered = (sy + sh) - (ty + th) >= 0.10 * th", "lowered = True"),
                        ("kind = SUBSCRIPT_KINDS.get(text.lower()) if len(text) == 1 else None", "kind = \"Ta\""))],
        [F5 + "test_f5_3_subscript_reader_refuses_a_plain_line"]),
    "grid_cell_taken_as_box (F5-3)": (
        [_source_mutant(labels, "legend_boxes", ("if not ((off(X0, rules_x) or off(X1, rules_x)) and (off(Y0, rules_y) or off(Y1, rules_y))):",
                                                  "if False:"))],
        [F5 + "test_f5_3_boxes_are_framed_boxes_not_grid_cells"]),
    "no_rule_erased_ocr (F5-3)": (
        [patch.object(rgv, "ocr_plot_labels_rules_erased", lambda *a, **k: [])],
        [F5 + "test_f5_3_brcs_free_labels_bind_by_temperature_order"]),
    "rule_free_reading_overrides (F5-3)": (
        [_source_mutant(rgv, "_add_condition_labels", ("if not clash:", "if True:"))],
        [F5 + "test_f5_3_brcs_free_labels_bind_by_temperature_order"]),
    "no_temperature_order_rule (F5-3)": (
        [_source_mutant(traces, "bind_by_order_rule", ('if key not in ORDER_RULE_BINDING:', 'if key != "id_a":'))],
        [F5 + "test_f5_3_brcs_free_labels_bind_by_temperature_order"]),
    "condition_mismatch_dropped (F5-3)": (
        [patch.object(report, "condition_mismatch_notes", lambda *a: [])],
        [F5 + "test_f5_3_brcs_free_labels_bind_by_temperature_order"]),
    "tube_core_not_restored (F5-2)": (
        [_source_mutant(report, "_draw_curves", ("body[core > 0] = source[core > 0]", "pass"))],
        [F5 + "test_f5_2_source_ink_visible_along_every_trace"]),
    "TRACE_CORE_1px (F5-2)": (
        [patch.object(report, "TRACE_CORE_PX", 1)],
        [F5 + "test_f5_2_source_ink_visible_along_every_trace"]),
    "tube_rails_white (F5-2 opposite: legibility)": (
        [_source_mutant(report, "_draw_curves", ("_stroke(body, curve, color, width)", "_stroke(body, curve, (255, 255, 255), width)"))],
        [F5 + "test_f5_2_traces_stay_colourful_beside_the_print", L + "test_r3_14_traces_have_white_halos_over_black_ink"]),
    "no_frame_tail_tracing (R5 right ends)": (
        [patch.object(traces, "extend_tails_to_frame", lambda traces_, gray, plot: traces_)],
        [F5 + "test_r5_right_ends_reach_the_frame_on_ink"]),
    "frame_bridged_without_far_ink (R5 right ends)": (
        [_source_mutant(traces, "extend_tails_to_frame", ("            if far is not None:",
                                                          "            far = far or (float(outer + 1), y)\n            if far is not None:"))],
        [F5 + "test_r5_no_ink_beyond_the_frame_means_no_bridge"]),
    "frame_tail_from_anywhere (R5 right ends)": (
        [_source_mutant(traces, "extend_tails_to_frame", ("if x_last < plot.x1 - FRAME_TAIL_START_PX or not", "if not"))],
        [F5 + "test_r5_a_trace_that_stopped_on_its_own_is_not_extended"]),
    "FRAME_FAR_SIDE_PX_0 (R5 right ends)": (
        [patch.object(traces, "FRAME_FAR_SIDE_PX", 0)],
        [F5 + "test_r5_right_ends_reach_the_frame_on_ink"]),
    # ---- round 6 (Fab's review of the v6 overlays: F6-1) ------------------------
    "no_stretch_tracer (F6-1)": (
        [patch.object(traces, "fill_unsampled_stretches", lambda traces_, *a, **k: traces_)],
        [F6 + "test_f6_1_every_listed_stretch_is_traced_on_ink", F6 + "test_f6_1_rq3e110aj_each_line_keeps_its_half_of_the_band"]),
    "band_half_ignored (F6-1)": (
        [_source_mutant(traces, "_follow_rows", ('centre = run["x0"] + LINE_HALF_WIDTH_PX if next(iter(sides)) < 0 else run["x1"] - LINE_HALF_WIDTH_PX',
                                                 'centre = run["centre"]'))],
        [F6 + "test_f6_1_rq3e110aj_each_line_keeps_its_half_of_the_band"]),
    "band_sides_disagree_accepted (F6-1)": (
        [_source_mutant(traces, "_follow_rows", ("if len(sides) != 1:", "if not sides:"))],
        [F6 + "test_f6_1_ink_that_cannot_be_assigned_is_refused_concretely"]),
    "rule_bridge_off_ink_accepted (F6-1)": (
        [_source_mutant(traces, "fill_unsampled_stretches", ("off_ink = [p for p in bridged if not _on_ink(gray, p)]", "off_ink = []"))],
        [F6 + "test_f6_1_a_rule_crossing_is_bridged_only_on_ink"]),
    "chord_without_ink_check (F6-1)": (
        [_source_mutant(traces, "_chord_on_ink", ("if gray[int(round(y)), int(round(x))] >= ROW_INK_GRAY:", "if False:"))],
        [F6 + "test_f6_1_short_chord_needs_ink_at_every_pixel"]),
    "follower_never_gives_up (F6-1)": (
        [patch.object(traces, "GAP_MAX_MISS", 10_000)],
        [F6 + "test_f6_1_ink_that_cannot_be_assigned_is_refused_concretely"]),
    "stub_always_served (F6-1)": (
        [_source_mutant(traces, "_stub_decision", ("if distance <= STUB_ON_STROKE_PX and _on_ink(gray, (x, y)):", "if True:"))],
        [F6 + "test_f6_1_end_stubs_are_served_or_named"]),
    "stub_never_served (F6-1)": (
        [patch.object(traces, "STUB_ON_STROKE_PX", -1.0)],
        [F6 + "test_f6_1_end_stubs_are_served_or_named"]),
    "stub_reason_not_concrete (F6-1)": (
        [_source_mutant(traces, "_stub_decision", ("    if rows:\n", "    if False:\n"))],
        [F6 + "test_f6_1_end_stubs_are_served_or_named"]),
    "stretch_points_move_existing (F6-1)": (
        [_source_mutant(traces, "fill_unsampled_stretches", (
            "out.append(replace(trace, points_px=pts[:-1] + added + pts[-1:], gap_traced=notes,",
            "out.append(replace(trace, points_px=[(x, y + 0.6) for x, y in pts[:-1]] + added + pts[-1:], gap_traced=notes,"))],
        [F6 + "test_f6_1_no_existing_point_moves"]),
    "stretch_points_appended_after_end (F6-1)": (
        [_source_mutant(traces, "fill_unsampled_stretches", ("points_px=pts[:-1] + added + pts[-1:]", "points_px=pts + added"))],
        [F6 + "test_f6_1_no_new_end_reasons"]),
    "tube_core_whole_width (F5-2 opposite: legibility)": (
        [_source_mutant(report, "_draw_curves", ("_stroke(core, curve, 255, TRACE_CORE_PX)", "_stroke(core, curve, 255, widths[curve['curve_index']])"))],
        [F5 + "test_f5_2_traces_stay_colourful_beside_the_print"]),
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


def _select(args: list[str]) -> list[str]:
    """Apply --only / --since-commit to MUTANTS (and EQUIVALENT_ON_REAL_DATA); return the rest of argv."""
    only = args[args.index("--only") + 1:] if "--only" in args else []
    args = args[:args.index("--only")] if "--only" in args else args
    if only:
        for label in [k for k in MUTANTS if not any(o in k for o in only)]:
            del MUTANTS[label]
        EQUIVALENT_ON_REAL_DATA.clear()
    if "--since-commit" in args:
        at = args.index("--since-commit")
        rev, args = args[at + 1], args[:at] + args[at + 2:]
        old = subprocess.run(["git", "show", f"{rev}:tools/rds_vgs_mutation_check.py"], cwd=Path(__file__).resolve().parents[1],
                             capture_output=True, text=True, check=True).stdout
        for table in (MUTANTS, EQUIVALENT_ON_REAL_DATA):
            for label in [k for k in table if k in old]:
                del table[label]
    return args


def _jobs(args: list[str]) -> tuple[int, list[str]]:
    if "--jobs" not in args:
        return max(1, (os.cpu_count() or 3) - 2), args
    at = args.index("--jobs")
    return max(1, int(args[at + 1])), args[:at] + args[at + 2:]


def _killed(result: unittest.TestResult) -> list[str]:
    killed = [t.id().rsplit(".", 2)[-2] + "." + t.id().rsplit(".", 1)[-1] for t, _ in result.failures + result.errors]
    return [k.replace("GoldenTests.test_golden_", "GOLDEN:") for k in killed]


# ---- parallel mode ---------------------------------------------------------------
# Mutants patch code IN MEMORY, so they are isolated by running each in its own
# worker process. All workers import one frozen snapshot of src/, tests/ and
# tools/ taken at start (under out/, never /tmp), so an edit to the worktree
# during a long run cannot reach later mutants. Unpatched digitizations and OCR
# results are shared through the test cache (tests/rds_digitize_cache.py):
# it refuses any digitization while a mutant is active, so a mutant never reads
# or writes an unmutated entry.

_RESULT = "@@RESULT "


def _worker() -> int:
    """--worker: read {"kind", "label"|"names"} on stdin, run it, print one result line."""
    job = json.loads(sys.stdin.read())
    if job["kind"] == "baseline":
        base = run(job["names"])
        out = {"ran": base.testsRun,
               "failures": [[t.id(), tb] for t, tb in base.failures],
               "errors": [[t.id(), tb] for t, tb in base.errors],
               "skipped": [[t.id(), why] for t, why in base.skipped]}
    else:
        table = MUTANTS if job["kind"] == "mutant" else EQUIVALENT_ON_REAL_DATA
        patches, names = table[job["label"]]
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            result = run(names)
        out = {"ran": result.testsRun, "killed": _killed(result)}
    print(_RESULT + json.dumps(out), flush=True)
    return 0


def _snapshot(repo: Path) -> Path:
    root = repo / "out" / "mutation-snapshots" / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    for part in ("src", "tests", "tools"):
        shutil.copytree(repo / part, root / part, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return root


def _spawn(snap: Path, repo: Path, job: dict) -> dict:
    env = dict(os.environ, PYTHONPATH=f"{snap / 'src'}{os.pathsep}{snap / 'tests'}",
               OMP_THREAD_LIMIT=os.environ.get("OMP_THREAD_LIMIT", "1"),
               DSDIG_TEST_CACHE_DIR=os.environ.get("DSDIG_TEST_CACHE_DIR", str(repo / "out" / "test-cache")))
    proc = subprocess.run([sys.executable, str(snap / "tools" / Path(__file__).name), "--worker"],
                          input=json.dumps(job), capture_output=True, text=True, env=env, cwd=repo)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(_RESULT)]
    if proc.returncode != 0 or len(lines) != 1:
        return {"error": f"worker exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-800:]}"}
    return json.loads(lines[0][len(_RESULT):])


def _main_parallel(log, jobs: int, all_names: list[str], start: float) -> int:
    repo = Path(__file__).resolve().parents[1]
    snap = _snapshot(repo)
    print(f"# parallel: {jobs} workers, snapshot {snap.relative_to(repo)}", file=sys.stderr, flush=True)
    base = _spawn(snap, repo, {"kind": "baseline", "names": all_names})
    if "error" in base:
        print(f"BASELINE (no mutant): ERROR {base['error']}", file=log, flush=True)
        return 1
    print(f"BASELINE (no mutant): ran {base['ran']}, failures {len(base['failures'])}, errors {len(base['errors'])}",
          file=log, flush=True)
    for test_id, trace in base["failures"] + base["errors"]:
        print(f"  BASELINE FAIL {test_id}\n{trace}", file=log)
    for test_id, why in base["skipped"]:
        print(f"  BASELINE SKIP (counts as failure) {test_id}: {why}", file=log)
    work = [("mutant", label) for label in MUTANTS] + [("equivalent", label) for label in EQUIVALENT_ON_REAL_DATA]
    survived = unexpected = errors = 0
    with ThreadPoolExecutor(jobs) as pool:
        futures = [pool.submit(_spawn, snap, repo, {"kind": kind, "label": label}) for kind, label in work]
        for (kind, label), future in zip(work, futures):     # logged in table order, as they complete
            result = future.result()
            if "error" in result:
                errors += 1
                print(f"ERROR    {label}: not evaluated ({result['error']})", file=log, flush=True)
            elif kind == "mutant":
                verdict = "KILLED" if result["killed"] else "SURVIVED"
                survived += not result["killed"]
                print(f"{verdict:8} {label}: ran {result['ran']}; failing: {result['killed']}", file=log, flush=True)
            else:
                killed = bool(result["killed"])
                unexpected += killed
                print(f"{'KILLED?!' if killed else 'EQUIV':8} {label} (equivalent on all 15 real panels): ran {result['ran']}",
                      file=log, flush=True)
    note = f"; NOT EVALUATED (worker errors) {errors}" if errors else ""
    print(f"mutants {len(MUTANTS)}, survived {survived}; equivalent-on-real-data {len(EQUIVALENT_ON_REAL_DATA)} "
          f"(unexpectedly killed {unexpected}); seconds {time.time() - start:.0f}{note}", file=log, flush=True)
    shutil.rmtree(snap, ignore_errors=True)
    return 1 if survived or unexpected or errors or base["failures"] or base["errors"] or base["skipped"] else 0


def main() -> int:
    """Usage: rds_vgs_mutation_check.py [LOG] [--jobs N] [--since-commit REV] [--only SUBSTRING ...]

    --only runs the baseline and just the mutants whose label contains one of
    the substrings (a quick re-check after fixing survivors; the full run is
    the record). --since-commit REV runs just the mutants whose label is not
    in this file at REV (the ones added since). --jobs N runs N mutants at a
    time, each in its own process (default: cores - 2); --jobs 1 is the
    original sequential, in-process run."""
    args = sys.argv[1:]
    if args == ["--worker"]:
        return _worker()
    args = _select(args)
    jobs, args = _jobs(args)
    log = open(args[0], "w") if args else sys.stdout
    all_names = sorted({n for _patches, names in MUTANTS.values() for n in names})
    start = time.time()
    if jobs > 1:
        return _main_parallel(log, jobs, all_names, start)
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
