import unittest

import cv2
import numpy as np

from datasheet_chart_digitizer.capacitance_axis import (
    _seat_regular_log_x_ticks_on_grid,
    calibration_v_of_x,
)
from datasheet_chart_digitizer.capacitance_types import AxisCalibration, PlotBox


class RegularLogXGridSeatingTests(unittest.TestCase):
    def setUp(self):
        self.plot = PlotBox(50, 20, 450, 320)
        self.image = np.full((350, 500), 255, dtype=np.uint8)
        for x in (50, 183, 317, 450):
            cv2.line(self.image, (x, 20), (x, 320), 0, 2)

    def calibration(self, values=(0.1, 1.0, 10.0, 100.0)):
        return AxisCalibration(
            x_min_v=min(values),
            x_max_v=max(values),
            y_min_decade=0.0,
            y_max_decade=4.0,
            source="position_text",
            x_ticks_v=values,
            y_decades=(0.0, 1.0, 2.0, 3.0, 4.0),
            x_log=True,
            x_resid_v=0.01,
            x_scale=3.0 / 380.0,
            x_offset=-1.0 - 3.0 * 60.0 / 380.0,
            y_scale=-4.0 / 300.0,
            y_offset=4.0,
            x_source="position_text",
            y_source="position_text",
            x_tick_label_px=(60.0, 189.0, 315.0, 440.0),
        )

    def test_regular_log_ticks_are_reseated_on_full_span_source_grid(self):
        seated = _seat_regular_log_x_ticks_on_grid(
            self.calibration(), self.image, self.plot
        )

        self.assertEqual(seated.x_gridline_px, (50.5, 183.5, 317.5, 450.5))
        self.assertTrue(seated.x_source.endswith("_grid_seated"))
        self.assertAlmostEqual(
            calibration_v_of_x(seated, self.plot, 50.5), 0.1, places=3
        )
        self.assertLess(
            abs(calibration_v_of_x(seated, self.plot, 450.5) - 100.0), 0.2
        )
        self.assertLess(seated.x_grid_residual_px, 1.0)

    def test_irregular_log_values_keep_the_position_fit(self):
        original = self.calibration((0.1, 1.0, 20.0, 100.0))
        self.assertIs(
            _seat_regular_log_x_ticks_on_grid(original, self.image, self.plot),
            original,
        )

    def test_nonpositive_log_value_keeps_the_position_fit(self):
        original = self.calibration((0.0, 1.0, 10.0, 100.0))
        self.assertIs(
            _seat_regular_log_x_ticks_on_grid(original, self.image, self.plot),
            original,
        )


if __name__ == "__main__":
    unittest.main()
