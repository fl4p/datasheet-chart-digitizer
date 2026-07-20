import unittest

from datasheet_chart_digitizer import mosfet_capacitance as mc


def _log_calibration(*, y_scale: float | None = -0.01) -> mc.AxisCalibration:
    return mc.AxisCalibration(
        x_min_v=0.0,
        x_max_v=100.0,
        y_min_decade=0.0,
        y_max_decade=3.0,
        source="position_text",
        x_ticks_v=(0.0, 10.0, 100.0),
        y_decades=(0.0, 1.0, 2.0, 3.0),
        y_scale=y_scale,
        y_offset=4.0 if y_scale is not None else None,
    )


class CapacitanceQossPlotTopTests(unittest.TestCase):
    def test_uses_plot_ceiling_not_highest_labeled_tick(self) -> None:
        result = mc.top_decade_clip_diagnostic(
            {"Coss": [(0.0, 2000.0), (2.0, 1800.0), (100.0, 100.0)]},
            _log_calibration(),
            mc.PlotBox(0, 0, 100, 300),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["near_axis_top"])
        self.assertFalse(result["near_plot_top"])
        self.assertEqual(1000.0, result["axis_top_pf"])
        self.assertEqual(1000.0, result["highest_labeled_tick_pf"])
        self.assertEqual(10000.0, result["plot_top_pf"])

    def test_activates_at_actual_plot_ceiling(self) -> None:
        result = mc.top_decade_clip_diagnostic(
            {"Coss": [(0.0, 9900.0), (2.0, 9800.0), (100.0, 100.0)]},
            _log_calibration(),
            mc.PlotBox(0, 0, 100, 300),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["near_plot_top"])
        self.assertEqual(10000.0, result["plot_top_pf"])

    def test_uses_linear_plot_ceiling(self) -> None:
        calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=100.0,
            y_min_decade=2.0,
            y_max_decade=3.0,
            source="position_text_grid_seated",
            x_ticks_v=(0.0, 10.0, 100.0),
            y_decades=(2.0, 3.0),
            y_log=False,
            y_ticks_pf=(0.0, 1000.0, 2000.0),
            y_scale=-5.0,
            y_offset=2000.0,
        )
        result = mc.top_decade_clip_diagnostic(
            {"Coss": [(0.0, 1930.0), (2.0, 1920.0), (100.0, 100.0)]},
            calibration,
            mc.PlotBox(0, 10, 100, 300),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(1950.0, result["plot_top_pf"])
        self.assertTrue(result["near_plot_top"])

    def test_refuses_completion_without_pixel_axis_map(self) -> None:
        result = mc.top_decade_clip_diagnostic(
            {"Coss": [(0.0, 10000.0), (2.0, 9000.0), (100.0, 100.0)]},
            _log_calibration(y_scale=None),
            mc.PlotBox(0, 0, 100, 300),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsNone(result["plot_top_pf"])
        self.assertFalse(result["near_plot_top"])

    def test_refuses_positive_y_axis_slope(self) -> None:
        calibration = _log_calibration(y_scale=0.01)
        result = mc.top_decade_clip_diagnostic(
            {"Coss": [(0.0, 20.0), (2.0, 18.0), (100.0, 10.0)]},
            calibration,
            mc.PlotBox(0, 10, 100, 300),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsNone(result["plot_top_pf"])
        self.assertFalse(result["near_plot_top"])


if __name__ == "__main__":
    unittest.main()
