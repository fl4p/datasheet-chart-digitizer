import unittest

import cv2
import numpy as np

from datasheet_chart_digitizer.capacitance_plot_box import (
    find_capacitance_plot_box,
)
from datasheet_chart_digitizer.capacitance_traces import find_plot_box
from datasheet_chart_digitizer.capacitance_types import PlotBox


class OpenRightPlotBoxTests(unittest.TestCase):
    def test_common_grid_row_endpoint_extends_occluded_right_frame(self) -> None:
        gray = np.full((500, 700), 255, dtype=np.uint8)
        for x in (120, 220, 300, 380, 460, 540, 620):
            cv2.line(gray, (x, 55), (x, 420), 0, 2)
        for y in (55, 130, 225, 320, 420):
            cv2.line(gray, (120, y), (680, y), 0, 2)

        detected = find_plot_box(gray)
        self.assertEqual(620, detected.x1)
        recovered = find_capacitance_plot_box(gray)
        self.assertEqual(
            (detected.x0, detected.y0, detected.y1),
            (recovered.x0, recovered.y0, recovered.y1),
        )
        self.assertGreaterEqual(recovered.x1, 680)
        self.assertLessEqual(recovered.x1, 682)

    def test_interior_rows_without_top_bottom_support_do_not_extend(self) -> None:
        gray = np.full((500, 700), 255, dtype=np.uint8)
        for x in (120, 220, 300, 380, 460, 540, 620):
            cv2.line(gray, (x, 55), (x, 420), 0, 2)
        cv2.line(gray, (120, 55), (620, 55), 0, 2)
        cv2.line(gray, (120, 420), (620, 420), 0, 2)
        for y in (130, 225, 320):
            cv2.line(gray, (120, y), (680, y), 0, 2)

        detected = find_plot_box(gray)
        self.assertEqual(detected, find_capacitance_plot_box(gray))


if __name__ == "__main__":
    unittest.main()
