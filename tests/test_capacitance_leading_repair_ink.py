"""Leading C(V) repairs must not splice a segment that crosses blank paper.

Known-bad cases from the 2026-11 raster benchmark (fixtures/cap_wrong_serve):
AGMH022N10H served Coss and Crss on Ciss ink for the first columns, then dropped
58 px through white; DMN39M1LFVW-7-HXY served Crss on Ciss and then Coss ink.
Both came from the left-edge row-walk repairs, whose shape guard accepted them.
"""

import unittest

import cv2
import numpy as np

from cap_wrong_serve_fixtures import load_gray, served_traces
from datasheet_chart_digitizer import capacitance_traces as ct
from datasheet_chart_digitizer.capacitance_ink_continuity import (
    CONNECTION_OFF_INK_MIN_PX,
    ink_distance_map,
    off_ink_runs,
)
from datasheet_chart_digitizer.capacitance_leading_repairs import _repair_shape_guard
from datasheet_chart_digitizer.capacitance_types import PlotBox


def _by_name(traces):
    return {trace.name: dict(trace.points) for trace in traces}


def _longest_off_ink(gray, points):
    return max(
        (run["length_px"] for run in off_ink_runs(ink_distance_map(gray), points)),
        default=0.0,
    )


class LeadingRepairInkTests(unittest.TestCase):
    def test_agmh_leading_samples_stay_on_their_own_strokes(self):
        gray, _plot, traces = served_traces("AGMH022N10H")
        by_name = _by_name(traces)
        ciss = by_name["Ciss"]
        for name in ("Coss", "Crss"):
            first_x = min(by_name[name])
            # Ciss runs at y ~121 at the left edge; its own stroke is >=50 px lower.
            ciss_y = ciss.get(first_x, ciss[min(ciss)])
            self.assertGreater(by_name[name][first_x] - ciss_y, 50, name)
        for trace in traces:
            self.assertLess(_longest_off_ink(gray, trace.points), CONNECTION_OFF_INK_MIN_PX, trace.name)

    def test_dmn39_crss_keeps_its_tracked_stroke(self):
        gray, _plot, traces = served_traces("DMN39M1LFVW-7-HXY.unicoded")
        by_name = _by_name(traces)
        crss, coss = by_name["Crss"], by_name["Coss"]
        shared = sorted(set(crss) & set(coss))[:8]
        self.assertEqual(8, len(shared))
        # The source Crss stroke runs 15-20 px below Coss over the first columns.
        for x in shared:
            self.assertGreater(crss[x] - coss[x], 10, x)
        crss_trace = next(trace for trace in traces if trace.name == "Crss")
        self.assertLess(_longest_off_ink(gray, crss_trace.points), CONNECTION_OFF_INK_MIN_PX)

    def test_known_bad_calibration_without_ink_proof_the_swap_returns(self):
        """Blind the check (every pixel 'on ink') and the main-era defect comes back."""

        original = ct.ink_distance_map
        ct.ink_distance_map = lambda gray: np.zeros(gray.shape, dtype=np.float32)
        try:
            _gray, _plot, traces = served_traces("DMN39M1LFVW-7-HXY.unicoded")
        finally:
            ct.ink_distance_map = original
        by_name = _by_name(traces)
        first_x = min(by_name["Crss"])
        # main served Crss at y=168 here: on Coss ink (Coss 168).
        self.assertLessEqual(abs(by_name["Crss"][first_x] - by_name["Coss"][first_x]), 2)

    def test_unusable_raster_refuses_instead_of_repairing_blind(self):
        blank = np.full((300, 400), 255, dtype=np.uint8)
        self.assertIsNone(ink_distance_map(blank))
        gray = load_gray("G6N90F")
        original = ct.ink_distance_map
        ct.ink_distance_map = lambda gray: None
        try:
            with self.assertRaisesRegex(RuntimeError, "no usable source ink raster"):
                served_traces("G6N90F")
        finally:
            ct.ink_distance_map = original
        self.assertIsNotNone(ink_distance_map(gray))

    def test_shape_guard_rejects_a_splice_through_white(self):
        gray = np.full((120, 120), 255, dtype=np.uint8)
        gray[39:42, 10:40] = 0  # a peer's stroke the repair climbed onto
        gray[60:63, 40:110] = 0  # the trace's own stroke
        plot = PlotBox(5, 5, 114, 114)
        original = [(x, 61) for x in range(40, 110)]
        repaired = [(x, 40) for x in range(30, 40)] + original
        # Every sample is on ink and the 21 px join passes the splice-continuity
        # rule, so only the ink check can see the drop through white.
        self.assertTrue(_repair_shape_guard(repaired, original, plot))
        self.assertFalse(
            _repair_shape_guard(repaired, original, plot, ink_distance=ink_distance_map(gray))
        )

    def test_shape_guard_keeps_a_steep_splice_drawn_in_ink(self):
        gray = np.full((120, 120), 255, dtype=np.uint8)
        plot = PlotBox(5, 5, 114, 114)
        original = [(x, 61) for x in range(40, 110)]
        repaired = [(x, 40 + 2 * (x - 30)) for x in range(30, 40)] + original
        path = np.array(repaired, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(gray, [path], False, 0, 2)
        self.assertTrue(
            _repair_shape_guard(repaired, original, plot, ink_distance=ink_distance_map(gray))
        )

if __name__ == "__main__":
    unittest.main()
