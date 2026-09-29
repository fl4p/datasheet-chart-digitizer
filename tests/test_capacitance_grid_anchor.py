"""capacitance_grid_anchor on a synthetic C(V) panel with offset label glyphs.

Known-bad: an AxisCalibration fitted to label centres (VDS labels 4 px right
of their rules, pF decade labels 5 px below) must be re-seated on the grid;
the same calibration handed straight to the guard must be reported failed,
and a blank raster must be unverified, never verified.
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from datasheet_chart_digitizer.capacitance_grid_anchor import (
    anchor_capacitance_axes_on_grid,
    check_axis_on_grid,
    grid_check_problems,
)
from datasheet_chart_digitizer.capacitance_types import AxisCalibration, PlotBox

PLOT = PlotBox(60, 20, 560, 420)
X_VALUES = [0.0, 20.0, 40.0, 60.0, 80.0, 100.0]
DECADES = [1.0, 2.0, 3.0, 4.0]  # 10..10^4 pF, 10^4 at the top


def _x(v):  # continuous crop px of a VDS rule
    return PLOT.x0 + (PLOT.x1 - PLOT.x0) * v / 100.0


def _y(d):  # continuous crop px of a decade rule
    return PLOT.y1 - (PLOT.y1 - PLOT.y0) * (d - 1.0) / 3.0


def _panel():
    image = np.full((460, 600), 255, dtype=np.uint8)
    # a 1 px rule at continuous c occupies pixel index c - 0.5
    for v in X_VALUES:
        image[PLOT.y0:PLOT.y1 + 1, int(_x(v) - 0.5)] = 0
    for d in DECADES:
        image[int(_y(d) - 0.5), PLOT.x0:PLOT.x1 + 1] = 0
        if d < 4.0:
            for j in range(2, 10):
                image[int(round(_y(d + math.log10(j)) - 0.5)), PLOT.x0:PLOT.x1 + 1] = 120
    return image


def _label_calibration(x_shift=4.0, y_shift=5.0):
    x_labels = tuple(_x(v) + x_shift for v in X_VALUES)
    y_labels = tuple(_y(d) + y_shift for d in DECADES)
    mx, bx = np.polyfit(x_labels, X_VALUES, 1)
    my, by = np.polyfit(y_labels, DECADES, 1)
    return AxisCalibration(
        x_min_v=0.0, x_max_v=100.0, y_min_decade=1.0, y_max_decade=4.0,
        source="position_text", x_ticks_v=tuple(X_VALUES), y_decades=tuple(DECADES),
        x_scale=float(mx), x_offset=float(bx), y_scale=float(my), y_offset=float(by),
        x_source="position_text", y_source="position_text",
        x_tick_label_px=x_labels, y_tick_label_px=y_labels,
    )


class CapacitanceGridAnchorTests(unittest.TestCase):
    def test_label_centre_calibration_fails_the_guard(self):
        calibration = _label_calibration()
        gray = _panel()
        self.assertEqual(check_axis_on_grid(calibration, gray, PLOT, "x").status, "failed")
        self.assertEqual(check_axis_on_grid(calibration, gray, PLOT, "y").status, "failed")

    def test_anchoring_serves_the_rules_and_verifies(self):
        anchored = anchor_capacitance_axes_on_grid(_label_calibration(), _panel(), PLOT)
        self.assertEqual(anchored.x_source, "position_text_grid_anchored")
        self.assertEqual(anchored.y_source, "position_text_grid_anchored")
        for v in X_VALUES:
            served = (v - anchored.x_offset) / anchored.x_scale
            self.assertAlmostEqual(served, _x(v), delta=1.0)  # rules drawn at int px
        for d in DECADES:
            served = (d - anchored.y_offset) / anchored.y_scale
            self.assertAlmostEqual(served, _y(d), delta=1.0)
        self.assertEqual(anchored.x_grid_check["status"], "verified")
        self.assertEqual(anchored.y_grid_check["status"], "verified")
        self.assertEqual(grid_check_problems(anchored), ([], []))

    def test_blank_raster_is_unverified_and_keeps_the_label_fit(self):
        blank = np.full((460, 600), 255, dtype=np.uint8)
        calibration = _label_calibration()
        result = anchor_capacitance_axes_on_grid(calibration, blank, PLOT)
        self.assertEqual(result.x_scale, calibration.x_scale)
        self.assertEqual(result.x_grid_check["status"], "unverified")
        failed, unverified = grid_check_problems(result)
        self.assertEqual(failed, [])
        self.assertEqual(len(unverified), 2)

    def test_check_not_run_is_unverified(self):
        failed, unverified = grid_check_problems(_label_calibration())
        self.assertEqual(failed, [])
        self.assertEqual(len(unverified), 2)


if __name__ == "__main__":
    unittest.main()
