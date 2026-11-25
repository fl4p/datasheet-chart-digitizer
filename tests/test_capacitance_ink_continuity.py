"""Served raster C(V) traces must stay on ink between samples, and must not
leave a shared stroke by a jump (capacitance_ink_continuity)."""

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from cap_wrong_serve_fixtures import FIXTURES, served_traces
from datasheet_chart_digitizer import mosfet_capacitance as mc
from datasheet_chart_digitizer.capacitance_ink_continuity import (
    BRIDGE_MAX_TRAJECTORY_DEVIATION_PX,
    SHARED_STROKE_EXIT_MIN_STEP_PX,
    continuity_reasons,
    material_off_ink_runs,
    raster_continuity_diagnostics,
    shared_stroke_exit_jumps,
)
from datasheet_chart_digitizer.capacitance_types import Trace
from datasheet_chart_digitizer.capacitance_validation import trace_validation_summary


def _trace(name, points):
    return Trace(name=name, area=len(points), bbox=(0, 0, 1, 1), points=points)


def _reasons(gray, traces):
    return continuity_reasons(raster_continuity_diagnostics(gray, traces))


class KnownBadServesTests(unittest.TestCase):
    """The benchmark's wrong `ok` serves, through the real raster chain."""

    def test_g6n90f_crss_chord_across_the_knee_is_refused(self):
        gray, _plot, traces = served_traces("G6N90F")
        diagnostics = raster_continuity_diagnostics(gray, traces)
        self.assertEqual(["Crss_served_connection_off_ink"], continuity_reasons(diagnostics))
        (run,) = diagnostics["off_ink"]["off_ink_traces"]["Crss"]
        self.assertGreater(run["length_px"], 70.0)

    def test_psmn020_coss_on_ciss_ink_is_refused(self):
        gray, _plot, traces = served_traces("PSMN020-30MLC")
        self.assertIn("Coss_leaves_shared_stroke_by_jump", _reasons(gray, traces))

    def test_sp010_coss_on_ciss_ink_is_refused(self):
        gray, _plot, traces = served_traces("SP010N02GHTF")
        diagnostics = raster_continuity_diagnostics(gray, traces)
        self.assertEqual(["Coss_leaves_shared_stroke_by_jump"], continuity_reasons(diagnostics))
        (jump,) = diagnostics["shared_stroke_exits"]["jumping_traces"]["Coss"]
        self.assertEqual("Ciss", jump["peer"])
        self.assertGreaterEqual(jump["step_px"], 13.0)

    def test_repaired_charts_pass_on_their_own_ink(self):
        for name in ("AGMH022N10H", "DMN39M1LFVW-7-HXY.unicoded"):
            gray, _plot, traces = served_traces(name)
            self.assertEqual([], _reasons(gray, traces), name)


class OffInkTests(unittest.TestCase):
    def setUp(self):
        self.gray = np.full((200, 300), 255, dtype=np.uint8)

    def _draw(self, points):
        cv2.polylines(self.gray, [np.array(points, np.int32).reshape(-1, 1, 2)], False, 0, 2)

    def test_chord_across_a_gap_in_the_samples_is_off_ink(self):
        curve = [(x, 50) for x in range(20, 100)] + [(x, 50 + (x - 100) * 4) for x in range(100, 125)]
        curve += [(x, 150) for x in range(125, 280)]
        self._draw(curve)
        served = [(x, y) for x, y in curve if not 95 <= x <= 140]
        result = material_off_ink_runs(cv2.distanceTransform((self.gray >= 90).astype(np.uint8), cv2.DIST_L2, 5), [_trace("Crss", served)])
        self.assertIn("Crss", result["off_ink_traces"])

    def test_straight_label_bridge_is_exempt_and_recorded(self):
        line = [(x, 80 + x // 10) for x in range(20, 280)]
        self._draw(line)
        self.gray[70:100, 130:170] = 255  # a white label box over the curve
        distance = cv2.distanceTransform((self.gray >= 90).astype(np.uint8), cv2.DIST_L2, 5)
        served = [(x, y) for x, y in line if not 128 <= x <= 172]
        result = material_off_ink_runs(distance, [_trace("Coss", served)])
        self.assertEqual({}, result["off_ink_traces"])
        self.assertIn("Coss", result["straight_bridges"])

    def test_bridge_with_an_unmeasurable_side_is_not_exempt(self):
        line = [(x, 80) for x in range(20, 280)]
        self._draw(line)
        self.gray[70:100, 30:70] = 255
        distance = cv2.distanceTransform((self.gray >= 90).astype(np.uint8), cv2.DIST_L2, 5)
        # Only 5 samples (23..27) left of the bridge: no left trajectory.
        served = [(x, y) for x, y in line if 23 <= x < 28 or x > 72]
        result = material_off_ink_runs(distance, [_trace("Coss", served)])
        self.assertIn("Coss", result["off_ink_traces"])
        self.assertIsNone(result["off_ink_traces"]["Coss"][0]["trajectory_deviation_px"][0])

    def test_bending_bridge_is_monotone_in_its_deviation(self):
        """A deeper knee under the gap never flips back to exempt."""

        verdicts = []
        for drop in range(0, 60, 2):
            self.gray[:] = 255
            left = [(x, 60) for x in range(20, 120)]
            right = [(x, 60 + drop) for x in range(160, 280)]
            self._draw(left)
            self._draw(right)
            distance = cv2.distanceTransform((self.gray >= 90).astype(np.uint8), cv2.DIST_L2, 5)
            result = material_off_ink_runs(distance, [_trace("Crss", left + right)])
            verdicts.append("Crss" in result["off_ink_traces"])
        first = verdicts.index(True)
        self.assertTrue(all(verdicts[first:]), verdicts)
        self.assertFalse(verdicts[0])  # a flat bridge across 40 px of blank is a label bridge
        # the flip sits where the trajectory miss passes the bridge limit
        self.assertLessEqual(2 * first, BRIDGE_MAX_TRAJECTORY_DEVIATION_PX + 2)

    def test_unusable_raster_is_unevaluated_not_clean(self):
        blank = np.full((100, 100), 255, dtype=np.uint8)
        traces = [_trace(n, [(x, 50) for x in range(10, 90)]) for n in ("Ciss", "Coss", "Crss")]
        diagnostics = raster_continuity_diagnostics(blank, traces)
        self.assertFalse(diagnostics["off_ink"]["evaluated"])
        self.assertIn("served_connection_off_ink_unevaluated", continuity_reasons(diagnostics))

    def test_path_outside_the_raster_counts_as_off_ink(self):
        self._draw([(x, 50) for x in range(20, 280)])
        distance = cv2.distanceTransform((self.gray >= 90).astype(np.uint8), cv2.DIST_L2, 5)
        served = [(x, 50) for x in range(20, 280)] + [(299, 50), (310, 50)]
        result = material_off_ink_runs(distance, [_trace("Ciss", served)])
        self.assertGreaterEqual(result["longest_off_ink_px"]["Ciss"], 10.0)


class SharedStrokeExitTests(unittest.TestCase):
    def _pair(self, step, shared=40):
        ciss = [(x, 50) for x in range(10, 200)]
        coss = [(x, 50) for x in range(10, 10 + shared)]
        coss += [(x, 50 + step + (x - 10 - shared) // 4) for x in range(10 + shared, 200)]
        return [_trace("Ciss", ciss), _trace("Coss", coss)]

    def test_stroke_split_passes_and_jump_is_flagged(self):
        self.assertEqual({}, shared_stroke_exit_jumps(self._pair(3))["jumping_traces"])
        jumps = shared_stroke_exit_jumps(self._pair(13))["jumping_traces"]
        self.assertEqual(["Coss"], list(jumps))
        self.assertEqual("Ciss", jumps["Coss"][0]["peer"])

    def test_exit_step_is_monotone(self):
        verdicts = [
            bool(shared_stroke_exit_jumps(self._pair(step))["jumping_traces"])
            for step in range(0, 80)
        ]
        first = verdicts.index(True)
        self.assertEqual(SHARED_STROKE_EXIT_MIN_STEP_PX, first)
        self.assertTrue(all(verdicts[first:]))

    def test_short_coincidence_is_not_a_shared_stroke(self):
        self.assertEqual({}, shared_stroke_exit_jumps(self._pair(30, shared=2))["jumping_traces"])


class ProcessChartWiringTests(unittest.TestCase):
    def test_process_chart_runs_the_check_and_withholds_the_chart(self):
        """Precondition: the production entry point computes, records and applies it."""

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = mc.process_chart(
                {"part": "G6N90F", "text": "Capacitance", "pdf": None},
                FIXTURES / "G6N90F.webp",
                out,
                Path("crops/G6N90F/p04_diagram_05"),
                out,
            )
        self.assertEqual("raster", result["extraction_method"])
        self.assertTrue(result["continuity_diagnostics"]["off_ink"]["evaluated"])
        self.assertIn("Crss_served_connection_off_ink", result["trace_validation_reasons"])
        self.assertEqual("suspect", result["trace_validation_status"])
        self.assertFalse(result["physical_output_available"])


class ValidationWiringTests(unittest.TestCase):
    DIAGNOSTICS = {
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
    FLAGGED = {
        "applicable": True,
        "off_ink": {"evaluated": True, "off_ink_traces": {"Crss": [{"length_px": 76.0}]}},
        "shared_stroke_exits": {"evaluated": True, "jumping_traces": {}},
    }

    def test_raster_flag_makes_the_chart_suspect(self):
        summary = trace_validation_summary(
            self.DIAGNOSTICS, "raster", continuity_diagnostics=self.FLAGGED
        )
        self.assertEqual("suspect", summary["status"])
        self.assertIn("Crss_served_connection_off_ink", summary["reasons"])

    def test_an_unevaluated_check_inside_the_diagnostics_is_a_reason(self):
        diagnostics = {**self.FLAGGED, "shared_stroke_exits": {"evaluated": False}}
        summary = trace_validation_summary(
            self.DIAGNOSTICS, "raster", continuity_diagnostics=diagnostics
        )
        self.assertIn("leaves_shared_stroke_by_jump_unevaluated", summary["reasons"])

    def test_vector_paths_are_not_judged_on_raster_ink(self):
        summary = trace_validation_summary(
            self.DIAGNOSTICS, "vector", continuity_diagnostics=self.FLAGGED
        )
        self.assertNotIn("Crss_served_connection_off_ink", summary["reasons"])


if __name__ == "__main__":
    unittest.main()
