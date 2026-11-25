"""A served raster C(V) trace must start where its printed ink starts
(capacitance_ink_start)."""

import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

from cap_wrong_serve_fixtures import FIXTURES, served_traces
from datasheet_chart_digitizer import mosfet_capacitance as mc
from datasheet_chart_digitizer.capacitance_grid_mask import trace_frame_margin_px
from datasheet_chart_digitizer.capacitance_ink_start import (
    LOW_V_START_SLACK_PX,
    low_v_start_diagnostics,
    low_v_start_reasons,
)
from datasheet_chart_digitizer.capacitance_types import PlotBox, Trace
from datasheet_chart_digitizer.capacitance_validation import trace_validation_summary

PLOT = PlotBox(20, 10, 419, 309)  # 400 x 300 -> frame margin 4 px


def _trace(name, points):
    return Trace(name=name, area=len(points), bbox=(0, 0, 1, 1), points=points)


def _canvas():
    gray = np.full((330, 440), 255, dtype=np.uint8)
    cv2.rectangle(gray, (PLOT.x0, PLOT.y0), (PLOT.x1, PLOT.y1), 0, 1)  # frame
    return gray


def _draw(gray, points, thickness=2):
    cv2.polylines(gray, [np.array(points, np.int32).reshape(-1, 1, 2)], False, 0, thickness)


def _knee(x):
    """A Coss-like curve: steep knee from the frame, then a slow fall."""
    return int(round(60 + 120 * (1 - np.exp(-(x - PLOT.x0) / 12.0)) + 0.1 * (x - PLOT.x0)))


def _reasons(gray, plot, traces):
    return low_v_start_reasons(low_v_start_diagnostics(gray, plot, traces))


class KnownBadStartsTests(unittest.TestCase):
    """Benchmark charts served ok without their printed low-VDS start."""

    def test_agmh_all_three_knees_unserved(self):
        gray, plot, traces = served_traces("AGMH022N10H")
        diagnostics = low_v_start_diagnostics(gray, plot, traces)
        self.assertEqual(
            ["Ciss_low_v_start_unserved", "Coss_low_v_start_unserved", "Crss_low_v_start_unserved"],
            low_v_start_reasons(diagnostics),
        )
        coss = diagnostics["traces"]["Coss"]
        self.assertEqual(plot.x0, coss["ink_start_x_px"])  # the printed curve meets the frame
        self.assertGreaterEqual(coss["unserved_rise_px"], 30)

    def test_4435_ciss_and_crss_unserved_coss_complete(self):
        gray, plot, traces = served_traces("4435")
        diagnostics = low_v_start_diagnostics(gray, plot, traces)
        self.assertEqual(
            ["Ciss_low_v_start_unserved", "Crss_low_v_start_unserved"],
            low_v_start_reasons(diagnostics),
        )
        self.assertGreaterEqual(diagnostics["traces"]["Ciss"]["unserved_px"], 20)

    def test_bsc320_crss_knee_unserved(self):
        gray, plot, traces = served_traces("BSC320N20NS3GATMA1")
        self.assertEqual(["Crss_low_v_start_unserved"], _reasons(gray, plot, traces))

    def test_complete_starts_pass(self):
        for name in ("G6N90F", "DMN39M1LFVW-7-HXY.unicoded", "SP010N02GHTF", "PSMN020-30MLC"):
            gray, plot, traces = served_traces(name)
            self.assertEqual([], _reasons(gray, plot, traces), name)


class SyntheticStartTests(unittest.TestCase):
    def test_served_start_sweep_is_monotone(self):
        """Serving the knee from further right never flips back to clean."""

        gray = _canvas()
        curve = [(x, _knee(x)) for x in range(PLOT.x0 + 1, PLOT.x1)]
        _draw(gray, curve)
        tolerance = trace_frame_margin_px(PLOT.width, PLOT.height) + LOW_V_START_SLACK_PX
        verdicts, unserved = [], []
        for start in range(PLOT.x0 + 1, PLOT.x0 + 80):
            diagnostics = low_v_start_diagnostics(
                gray, PLOT, [_trace("Coss", [p for p in curve if p[0] >= start])]
            )
            unserved.append(diagnostics["traces"]["Coss"]["unserved_px"])
            verdicts.append(bool(diagnostics["unserved_traces"]))
        self.assertEqual(sorted(unserved), unserved)
        first = verdicts.index(True)
        self.assertTrue(all(verdicts[first:]))
        self.assertFalse(any(verdicts[:first]))
        self.assertEqual(tolerance + 1, unserved[first])

    def test_curve_printed_from_mid_plot_is_a_legitimate_start(self):
        gray = _canvas()
        curve = [(x, 80 + (x - 150) // 5) for x in range(150, PLOT.x1)]
        _draw(gray, curve)
        diagnostics = low_v_start_diagnostics(gray, PLOT, [_trace("Ciss", curve[1:])])
        self.assertEqual({}, diagnostics["unserved_traces"])
        self.assertLessEqual(diagnostics["traces"]["Ciss"]["unserved_px"], 2)

    def test_curve_hidden_behind_the_frame_within_the_margin_passes(self):
        gray = _canvas()
        curve = [(x, _knee(x)) for x in range(PLOT.x0 + 1, PLOT.x1)]
        _draw(gray, curve)
        margin = trace_frame_margin_px(PLOT.width, PLOT.height)
        # smoothing/seating may cost the tracer a few columns past its margin
        for extra in (0, 3):
            served = [p for p in curve if p[0] >= PLOT.x0 + margin + extra]
            self.assertEqual([], _reasons(gray, PLOT, [_trace("Coss", served)]), extra)

    def test_curves_fused_at_the_left_edge_served_on_the_shared_stroke_pass(self):
        gray = _canvas()
        upper = [(x, 100 + (x - PLOT.x0) // 40) for x in range(PLOT.x0 + 1, PLOT.x1)]
        lower = [(x, y + max(0, (x - 120) // 3)) for x, y in upper]
        _draw(gray, upper, 3)
        _draw(gray, lower, 3)
        margin = trace_frame_margin_px(PLOT.width, PLOT.height)
        traces = [
            _trace("Ciss", [p for p in upper if p[0] >= PLOT.x0 + margin]),
            _trace("Coss", [p for p in lower if p[0] >= PLOT.x0 + margin]),
        ]
        self.assertEqual([], _reasons(gray, PLOT, traces))

    def test_a_full_span_rule_through_the_start_is_not_followed(self):
        gray = _canvas()
        cv2.line(gray, (PLOT.x0, 150), (PLOT.x1, 150), 0, 1)  # black major rule
        curve = [(x, 150 + (x - 200) // 4) for x in range(200, PLOT.x1)]
        _draw(gray, curve)
        self.assertEqual([], _reasons(gray, PLOT, [_trace("Crss", curve[1:])]))

    def test_vertical_rules_are_crossed_and_the_knee_still_found(self):
        gray = _canvas()
        for x in range(60, PLOT.x1, 40):
            cv2.line(gray, (x, PLOT.y0), (x, PLOT.y1), 0, 1)  # black grid verticals
        curve = [(x, _knee(x)) for x in range(PLOT.x0 + 1, PLOT.x1)]
        _draw(gray, curve)
        served = [p for p in curve if p[0] >= 90]
        diagnostics = low_v_start_diagnostics(gray, PLOT, [_trace("Coss", served)])
        self.assertIn("Coss", diagnostics["unserved_traces"])
        self.assertLessEqual(diagnostics["traces"]["Coss"]["ink_start_x_px"], PLOT.x0 + 2)


class UnevaluableTests(unittest.TestCase):
    def test_no_ink_is_unevaluated_not_clean(self):
        blank = np.full((330, 440), 255, dtype=np.uint8)
        traces = [_trace("Coss", [(x, 100) for x in range(40, 400)])]
        diagnostics = low_v_start_diagnostics(blank, PLOT, traces)
        self.assertFalse(diagnostics["evaluated"])
        self.assertEqual(["low_v_start_unevaluated"], low_v_start_reasons(diagnostics))

    def test_missing_plot_or_raster_is_unevaluated(self):
        traces = [_trace("Coss", [(x, 100) for x in range(40, 400)])]
        for gray, plot in ((None, PLOT), (_canvas(), None), (_canvas(), PlotBox(0, 0, 900, 900))):
            self.assertEqual(
                ["low_v_start_unevaluated"], _reasons(gray, plot, traces)
            )

    def test_served_start_off_ink_is_unevaluated_for_that_trace(self):
        gray = _canvas()
        ciss = [(x, 100 + x // 40) for x in range(PLOT.x0 + 1, PLOT.x1)]
        _draw(gray, ciss)
        traces = [
            _trace("Ciss", [p for p in ciss if p[0] >= 24]),
            _trace("Coss", [(x, 200) for x in range(30, 400)]),  # no ink there
        ]
        self.assertEqual(["Coss_low_v_start_unevaluated"], _reasons(gray, PLOT, traces))

    def test_a_dead_flat_full_span_stroke_reads_as_a_rule_and_fails_closed(self):
        """Known limit: indistinguishable from a printed rule, so unevaluated, never clean."""

        gray = _canvas()
        flat = [(x, 280) for x in range(PLOT.x0 + 1, PLOT.x1)]
        coss = [(x, _knee(x)) for x in range(PLOT.x0 + 1, PLOT.x1)]
        _draw(gray, flat)
        _draw(gray, coss)
        traces = [_trace("Ciss", flat[3:]), _trace("Coss", coss[3:])]
        self.assertEqual(["Ciss_low_v_start_unevaluated"], _reasons(gray, PLOT, traces))

    def test_malformed_diagnostics_are_unevaluated(self):
        self.assertEqual(
            ["low_v_start_unevaluated"],
            low_v_start_reasons({"applicable": True, "evaluated": True}),
        )
        self.assertEqual([], low_v_start_reasons(None))
        self.assertEqual([], low_v_start_reasons({"applicable": False}))


class WiringTests(unittest.TestCase):
    def test_process_chart_runs_the_check_and_withholds_the_chart(self):
        """Precondition: the production entry point computes, records and applies it."""

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = mc.process_chart(
                {"part": "AGMH022N10H", "text": "Capacitance", "pdf": None},
                FIXTURES / "AGMH022N10H.webp",
                out,
                Path("crops/AGMH022N10H/p05_diagram_07"),
                out,
            )
        self.assertEqual("raster", result["extraction_method"])
        self.assertTrue(result["low_v_start_diagnostics"]["evaluated"])
        self.assertIn("Coss_low_v_start_unserved", result["trace_validation_reasons"])
        self.assertEqual("suspect", result["trace_validation_status"])
        self.assertFalse(result["physical_output_available"])
        self.assertFalse(result["qoss_metrics_physical_output_available"])
        self.assertIsNone(result["qoss_metrics"])

    def test_vector_extraction_is_not_judged_on_raster_ink(self):
        diagnostics = {
            "Ciss": {"points": 100, "x_span_fraction": 1.0, "y_range_px": 10},
            "Coss": {"points": 100, "x_span_fraction": 1.0, "y_range_px": 40},
            "Crss": {"points": 100, "x_span_fraction": 1.0, "y_range_px": 60},
            "checks": {
                "common_samples": 100,
                "ciss_coss_rank_swap_count": 0,
                "crss_bottom_fraction": 1.0,
                "ciss_flatter_than_coss": True,
            },
        }
        flagged = {"applicable": True, "evaluated": True,
                   "unserved_traces": {"Coss": {}}, "unevaluated_traces": []}
        raster = trace_validation_summary(diagnostics, "raster", low_v_start_diagnostics=flagged)
        vector = trace_validation_summary(diagnostics, "vector", low_v_start_diagnostics=flagged)
        self.assertEqual("suspect", raster["status"])
        self.assertIn("Coss_low_v_start_unserved", raster["reasons"])
        self.assertNotIn("Coss_low_v_start_unserved", vector["reasons"])

    def test_runtime_per_chart_is_small(self):
        gray, plot, traces = served_traces("AGMH022N10H")
        started = time.perf_counter()
        for _ in range(5):
            low_v_start_diagnostics(gray, plot, traces)
        self.assertLess((time.perf_counter() - started) / 5, 0.25)


if __name__ == "__main__":
    unittest.main()
