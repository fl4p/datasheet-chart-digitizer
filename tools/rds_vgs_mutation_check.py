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


def _header_first_four(row, panel):
    lines = _real_header(row, panel)
    return lines[:2] + [(text[:110], color) for text, color in lines[2:6]]


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
        [patch.object(traces, "_drop_orphan_ends", lambda points, erased: list(points))],
        ["RoundTwoTests.test_r2_8_continuous_ink_is_not_called_a_gap"]),
    "floating_text_fragment_kept (Codex #3)": (
        [patch.object(traces, "_floating_flat_fragment", lambda *a: False)],
        ["RasterCoverageHonestyTests"]),
}


def run(names: list[str]) -> unittest.TestResult:
    review._CACHE.clear()
    suite = unittest.defaultTestLoader.loadTestsFromNames([T + n for n in names])
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
    survived = 0
    for label, (patches, names) in MUTANTS.items():
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            result = run(names)
        killed = [t.id().rsplit(".", 2)[-2] + "." + t.id().rsplit(".", 1)[-1] for t, _ in result.failures + result.errors]
        verdict = "KILLED" if killed else "SURVIVED"
        survived += not killed
        print(f"{verdict:8} {label}: ran {result.testsRun}; failing: {killed}", file=log, flush=True)
    print(f"mutants {len(MUTANTS)}, survived {survived}, seconds {time.time() - start:.0f}", file=log, flush=True)
    return 1 if survived or base.failures or base.errors else 0


if __name__ == "__main__":
    np.seterr(all="ignore")
    raise SystemExit(main())
