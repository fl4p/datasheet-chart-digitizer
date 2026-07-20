from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.overlay import (
    PLOT_FRAME_THICKNESS_PX,
    draw_plot_frame,
)


class OverlayFrameTests(unittest.TestCase):
    def test_shared_plot_frame_is_six_pixels_thick(self) -> None:
        image = np.full((80, 80, 3), 255, dtype=np.uint8)

        draw_plot_frame(image, PlotBox(10, 10, 70, 70), color=(0, 180, 0))

        self.assertEqual(PLOT_FRAME_THICKNESS_PX, 6)
        self.assertTupleEqual(tuple(image[40, 12]), (0, 180, 0))
        self.assertTupleEqual(tuple(image[40, 14]), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
