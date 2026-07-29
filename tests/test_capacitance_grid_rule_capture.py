"""Grid-rule capture: a trace that abandons its curve to ride a decade line.

Calibrated against CRMicro CRSM038N10N4 figure 6, which served a flat 99.7 pF
Crss across 30-35 V (entered by a 13 px step) with ``status: ok`` because grid
ink satisfies the source-ink check.
"""

from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer.capacitance_source_support import (
    grid_rule_capture_diagnostics,
)
from datasheet_chart_digitizer.capacitance_types import PlotBox, Trace
from datasheet_chart_digitizer.capacitance_validation import (
    trace_validation_summary,
)


WIDTH = 400
HEIGHT = 300
RULE_Y = 150


def _plot() -> PlotBox:
    return PlotBox(0, 0, WIDTH - 1, HEIGHT - 1)


def _canvas_with_rule(rule_rows: tuple[int, ...] = (RULE_Y,)) -> np.ndarray:
    gray = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    for row in rule_rows:
        gray[row, :] = 0
    return gray


def _trace(name: str, points: list[tuple[int, int]]) -> Trace:
    return Trace(name=name, area=len(points), bbox=(0, 0, WIDTH, HEIGHT), points=points)


def _draw_curve(gray: np.ndarray, fn, thickness: int = 3) -> np.ndarray:
    """Ink the curve the trace is supposed to follow.

    A capture is only a capture if the abandoned curve is still visible, so a
    fixture without this ink does not describe one.
    """
    for x in range(WIDTH):
        y = int(round(fn(x)))
        for dy in range(-(thickness // 2), thickness // 2 + 1):
            if 0 <= y + dy < HEIGHT:
                gray[y + dy, x] = 0
    return gray


class GridRuleCaptureTests(unittest.TestCase):
    def test_segment_pinned_to_a_rule_off_its_approach_is_captured(self) -> None:
        # The real curve stays inked and visible; the trace leaves it for the
        # rule and comes back.  That abandoned stroke is what makes it a capture.
        curve = lambda x: 60 + 0.5 * x
        points = []
        for x in range(WIDTH):
            y = RULE_Y if 200 <= x <= 260 else curve(x)
            points.append((x, int(round(y))))
        gray = _draw_curve(_canvas_with_rule(), curve)
        result = grid_rule_capture_diagnostics(
            gray, _plot(), [_trace("Crss", points)]
        )
        self.assertTrue(result["evaluated"])
        captured = result["captured_traces"]
        assert isinstance(captured, dict)
        self.assertIn("Crss", captured)
        capture = captured["Crss"][0]
        self.assertEqual(RULE_Y, capture["rule_y_px"])
        self.assertGreaterEqual(capture["approach_deviation_px"], 4.0)

    def test_flat_trace_coinciding_with_its_own_row_is_not_captured(self) -> None:
        # Toshiba TPH2R70AR5: a flat trace darkens its own row across the full
        # width, so the row looks like a rule.  No rule exists off the trace.
        gray = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
        gray[RULE_Y, :] = 0
        points = [(x, RULE_Y) for x in range(WIDTH)]
        result = grid_rule_capture_diagnostics(gray, _plot(), [_trace("Ciss", points)])
        self.assertEqual({}, result["captured_traces"])

    def test_curve_arriving_at_a_rule_continuously_is_not_captured(self) -> None:
        # A real plateau: the trace's own approach already predicts the rule.
        points = []
        for x in range(WIDTH):
            y = min(RULE_Y, 140 + 0.05 * x)
            points.append((x, int(round(y))))
        result = grid_rule_capture_diagnostics(
            _canvas_with_rule(), _plot(), [_trace("Coss", points)]
        )
        self.assertEqual({}, result["captured_traces"])

    def test_shared_collapse_columns_are_left_to_their_own_gate(self) -> None:
        points = []
        for x in range(WIDTH):
            y = 100 + 0.25 * x
            if 200 <= x <= 260:
                y = RULE_Y
            points.append((x, int(round(y))))
        spans = [{"x0_px": 190, "x1_px": 270, "curves": ["Ciss", "Coss"]}]
        result = grid_rule_capture_diagnostics(
            _canvas_with_rule(), _plot(), [_trace("Crss", points)], spans
        )
        self.assertEqual({}, result["captured_traces"])

    def test_short_pinned_run_is_below_the_material_threshold(self) -> None:
        points = []
        for x in range(WIDTH):
            y = 100 + 0.25 * x
            if 200 <= x <= 204:
                y = RULE_Y
            points.append((x, int(round(y))))
        result = grid_rule_capture_diagnostics(
            _canvas_with_rule(), _plot(), [_trace("Crss", points)]
        )
        self.assertEqual({}, result["captured_traces"])

    def test_capture_is_monotone_in_run_length(self) -> None:
        """A longer ride on the rule must never flip back to clean."""
        curve = lambda x: 60 + 0.5 * x
        detected = []
        for end in (240, 260, 300, 340):
            points = []
            for x in range(WIDTH):
                y = RULE_Y if 200 <= x <= end else curve(x)
                points.append((x, int(round(y))))
            gray = _draw_curve(_canvas_with_rule(), curve)
            result = grid_rule_capture_diagnostics(
                gray, _plot(), [_trace("Crss", points)]
            )
            detected.append(bool(result["captured_traces"]))
        self.assertEqual([True] * 4, detected)

    def test_flat_trace_on_the_only_stroke_is_not_captured(self) -> None:
        """The false-positive class the full-corpus collateral run caught.

        XR100N20G/H/T lost servability because a flat Ciss correctly seated on
        its own stroke read as a capture: a rule ran beside it and a slightly
        mis-seated approach produced a 4.0 px "deviation" -- the noise floor of
        a linear fit through a flat trace, and below every real capture measured
        on the corpus (6.0 / 11.0 / 11.9 / 17.0 px).
        """
        gray = _canvas_with_rule()
        # The trace's own stroke, 1 px off the rule, is the only other ink.
        points = []
        for x in range(WIDTH):
            y = RULE_Y - 4 if x < 199 else RULE_Y
            points.append((x, y))
        _draw_curve(gray, lambda x: RULE_Y - 4 if x < 199 else RULE_Y)
        result = grid_rule_capture_diagnostics(
            gray, _plot(), [_trace("Ciss", points)]
        )
        self.assertEqual({}, result["captured_traces"])


    def test_undecidable_run_is_reported_not_silently_dropped(self) -> None:
        # The trace occupies the rule's row over nearly every column, so too
        # few columns remain to tell rule from trace.  That must surface.
        points = []
        for x in range(WIDTH):
            if x <= 180 or x >= 200:
                y = RULE_Y
            else:
                y = 100 + (x - 181)
            points.append((x, int(y)))
        result = grid_rule_capture_diagnostics(
            _canvas_with_rule(), _plot(), [_trace("Crss", points)]
        )
        self.assertEqual({}, result["captured_traces"])
        undecidable = result["undecidable_runs"]
        assert isinstance(undecidable, dict)
        self.assertIn("Crss", undecidable)
        self.assertEqual(
            "rule_indistinguishable_from_trace",
            undecidable["Crss"][0]["reason"],
        )

    def test_unusable_raster_reports_unevaluated_not_clean(self) -> None:
        result = grid_rule_capture_diagnostics(
            np.zeros((0, 0), dtype=np.uint8), _plot(), []
        )
        self.assertFalse(result["evaluated"])
        self.assertNotIn("captured_traces", result)


def _passing_diagnostics() -> dict[str, object]:
    trace = {
        "points": 400,
        "x_span_fraction": 0.98,
        "y_range_px": 90,
        "value_rise_fraction": -0.2,
    }
    return {
        "Ciss": {**trace, "y_range_px": 6},
        "Coss": dict(trace),
        "Crss": dict(trace),
        "checks": {
            "common_samples": 300,
            "ciss_coss_rank_swap_count": 0,
            "crss_bottom_fraction": 1.0,
            "ciss_flatter_than_coss": True,
        },
    }


def _reasons(summary: dict[str, object]) -> list[str]:
    reasons = summary["reasons"]
    assert isinstance(reasons, list)
    return reasons


def _support(grid_rule_capture: object) -> dict[str, object]:
    return {
        "applicable": True,
        "trace_support": {
            name: {"material_source_absent_runs": []}
            for name in ("Ciss", "Coss", "Crss")
        },
        "material_shared_orphan_source_runs": [],
        "grid_rule_capture": grid_rule_capture,
    }


class GridRuleCaptureValidationTests(unittest.TestCase):
    def test_captured_trace_refuses_the_chart(self) -> None:
        summary = trace_validation_summary(
            _passing_diagnostics(),
            "raster",
            source_support_diagnostics=_support(
                {
                    "evaluated": True,
                    "captured_traces": {"Crss": [{"x0_px": 307, "x1_px": 324}]},
                }
            ),
        )
        self.assertEqual("suspect", summary["status"])
        self.assertIn("Crss_captured_by_grid_rule", _reasons(summary))

    def test_clean_capture_check_still_passes(self) -> None:
        summary = trace_validation_summary(
            _passing_diagnostics(),
            "raster",
            source_support_diagnostics=_support(
                {"evaluated": True, "captured_traces": {}}
            ),
        )
        self.assertEqual("pass", summary["status"])

    def test_unevaluated_capture_check_is_not_treated_as_clean(self) -> None:
        """Absence of evidence must not encode absence of the problem."""
        summary = trace_validation_summary(
            _passing_diagnostics(),
            "raster",
            source_support_diagnostics=_support(
                {"evaluated": False, "reason": "unusable_plot_raster"}
            ),
        )
        self.assertEqual("suspect", summary["status"])
        self.assertIn("grid_rule_capture_unevaluated", _reasons(summary))

    def test_missing_capture_check_is_not_treated_as_clean(self) -> None:
        support = _support(None)
        del support["grid_rule_capture"]
        summary = trace_validation_summary(
            _passing_diagnostics(), "raster", source_support_diagnostics=support
        )
        self.assertEqual("suspect", summary["status"])
        self.assertIn("grid_rule_capture_unevaluated", _reasons(summary))
