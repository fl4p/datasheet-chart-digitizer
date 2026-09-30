"""Transfer VGS/ID calibration: sparse (3-label) axes and the shared current grammar.

A three-label axis is served only when its labels are equally stepped, the frame
overhangs the outer labels by at most one unlabelled interval, and every label
is seated on an observed rule (served-axis grid check ``verified``).
"""

from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer import served_axis_guard
from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.numeric_axis import NumericAxis
from datasheet_chart_digitizer.transfer_axis_calibration import (
    calibrate_transfer,
    require_seated_sparse_axes,
    sparse_axis_refusal,
)

PLOT = PlotBox(x0=100, y0=50, x1=500, y1=450)


def _x_row(values, pixels):
    return [(str(v), float(px), PLOT.y1 + 15.0) for v, px in zip(values, pixels)]


def _y_col(texts, pixels):
    return [(t, PLOT.x0 - 20.0, float(py)) for t, py in zip(texts, pixels)]


# y: five decades 1m..10 over the frame (pitch 100 px)
Y_DECADES = _y_col(["1m", "10m", "100m", "1", "10"], [450, 350, 250, 150, 50])


class SparseAxisCalibrationTests(unittest.TestCase):
    def test_three_equal_labels_bounded_by_the_frame_are_a_sparse_axis(self):
        # 0 4 8 V, frame to 10 V: overhang half an interval
        words = _x_row([0, 4, 8], [100, 260, 420]) + Y_DECADES
        x_axis, y_axis, sparse = calibrate_transfer(words, PLOT)
        self.assertEqual(sparse, {"x"})
        self.assertAlmostEqual(x_axis.value(500), 10.0, places=6)
        self.assertEqual(y_axis.model, "log10")
        self.assertAlmostEqual(y_axis.value(250), 0.1, places=6)

    def test_four_labels_stay_dense(self):
        words = _x_row([0, 2, 4, 6], [100, 200, 300, 400]) + Y_DECADES
        self.assertEqual(calibrate_transfer(words, PLOT)[2], set())

    def test_extrapolation_across_two_unseen_intervals_refuses(self):
        # 0 3 6 V on a frame running to 10 V: 1.33 unlabelled intervals past "6"
        words = _x_row([0, 3, 6], [100, 220, 340]) + Y_DECADES
        with self.assertRaisesRegex(RuntimeError, "extrapolate"):
            calibrate_transfer(words, PLOT)
        # 0 2 4 V on the same frame: the row does not even span the frame
        with self.assertRaises(RuntimeError):
            calibrate_transfer(_x_row([0, 2, 4], [100, 180, 260]) + Y_DECADES, PLOT)

    def test_unequal_value_steps_refuse(self):
        words = _x_row([0, 4, 10], [100, 260, 500]) + Y_DECADES
        with self.assertRaisesRegex(RuntimeError, "not equally stepped"):
            calibrate_transfer(words, PLOT)

    def test_unequal_pixel_spacing_refuses(self):
        self.assertRegex(
            sparse_axis_refusal("X", [0, 4, 8], [100, 260, 400], 100, 500, log=False),
            "not equally spaced",
        )

    def test_three_y_decades_are_sparse_log(self):
        words = _x_row([0, 2, 4, 6], [100, 200, 300, 400]) + _y_col(["1", "10", "100"], [400, 250, 100])
        _x, y_axis, sparse = calibrate_transfer(words, PLOT)
        self.assertEqual((sparse, y_axis.model), ({"y"}, "log10"))

    def test_e_notation_and_split_superscript_decades_are_read(self):
        words = _x_row([0, 2, 4, 6], [100, 200, 300, 400]) + _y_col(
            ["1.0E-03", "1.0E-02", "1.0E-01", "1.0E+00"], [450, 350, 250, 150]
        )
        self.assertAlmostEqual(calibrate_transfer(words, PLOT)[1].value(350), 0.01, places=8)
        # "10" base + raised "−2" exponent as separate words, recovered as 10^n spans
        split = [("10", 80.0, 350.0), ("−2", 88.0, 344.0), ("10", 80.0, 250.0), ("−1", 88.0, 244.0),
                 ("1", 80.0, 150.0), ("10", 80.0, 50.0)]
        powers = [(1e-2, 84.0, 347.0, (76.0, 340.0, 92.0, 354.0)), (1e-1, 84.0, 247.0, (76.0, 240.0, 92.0, 254.0))]
        y_axis = calibrate_transfer(_x_row([0, 2, 4, 6], [100, 200, 300, 400]) + split, PLOT, powers)[1]
        self.assertEqual(y_axis.model, "log10")
        self.assertAlmostEqual(y_axis.value(350), 0.01, places=8)


def _grid_image(xs=(), ys=()):
    gray = np.full((500, 600), 255, dtype=np.uint8)
    gray[PLOT.y0:PLOT.y1 + 1, [PLOT.x0, PLOT.x1]] = 0
    gray[[PLOT.y0, PLOT.y1], PLOT.x0:PLOT.x1 + 1] = 0
    for x in xs:
        gray[PLOT.y0:PLOT.y1, int(x)] = 90
    for y in ys:
        gray[int(y), PLOT.x0:PLOT.x1] = 90
    return gray


class SparseAxisSeatingTests(unittest.TestCase):
    """The seating gate on the real served-axis grid check."""

    def setUp(self):
        self.words = _x_row([0, 4, 8], [100, 260, 420]) + Y_DECADES
        self.x_axis, self.y_axis, self.sparse = calibrate_transfer(self.words, PLOT)
        self.y_lines = [50, 150, 250, 350, 450]

    def _checks(self, gray, x_served=None):
        served = {"x": x_served or self.x_axis, "y": self.y_axis}
        return served_axis_guard.check_axes(gray, PLOT, served, {"x": self.x_axis, "y": self.y_axis}, "transfer")

    def test_labels_seated_on_rules_pass(self):
        checks = self._checks(_grid_image(xs=[180, 260, 340, 420], ys=self.y_lines))
        self.assertEqual(checks["x"].status, "verified", checks["x"].reason)
        require_seated_sparse_axes(checks, self.sparse)

    def test_three_labels_without_rules_refuse(self):
        checks = self._checks(_grid_image(ys=self.y_lines))
        self.assertNotEqual(checks["x"].status, "verified")
        with self.assertRaisesRegex(RuntimeError, "not all seated on observed rules"):
            require_seated_sparse_axes(checks, self.sparse)

    def test_one_percent_shifted_calibration_refuses(self):
        a = self.x_axis
        shift = 0.01 * (a.value(PLOT.x1) - a.value(PLOT.x0))
        shifted = NumericAxis(a.model, a.m, a.b + shift, a.ticks, a.residual_px, a.candidate_residuals_px)
        checks = self._checks(_grid_image(xs=[180, 260, 340, 420], ys=self.y_lines), shifted)
        self.assertEqual(checks["x"].status, "failed", checks["x"].reason)
        with self.assertRaises(RuntimeError):
            require_seated_sparse_axes(checks, self.sparse)

    def test_missing_grid_check_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "grid check not run"):
            require_seated_sparse_axes({}, {"x"})


if __name__ == "__main__":
    unittest.main()
