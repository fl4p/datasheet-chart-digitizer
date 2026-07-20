import tempfile
import unittest
from pathlib import Path

from datasheet_chart_digitizer.diode_forward_voltage import digitize_pdf
from datasheet_chart_digitizer import find_charts


STW46NF30 = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st/STW46NF30.pdf"
)


@unittest.skipUnless(STW46NF30.exists(), "local STW46NF30 unavailable")
class Stw46TransposedBodyDiodeTests(unittest.TestCase):
    def test_current_condition_without_tick_ladder_cannot_reverse_caption(self):
        title = find_charts.DiagramTitle(
            13,
            "Typical reverse diode forward characteristics",
            (40.0, 100.0, 260.0, 112.0),
            "Figure 13. Typical reverse diode forward characteristics",
        )
        words = [
            find_charts.Word("I", 220.0, 150.0, 225.0, 158.0),
            find_charts.Word("SD", 225.0, 150.0, 235.0, 158.0),
            find_charts.Word("(A)", 238.0, 150.0, 250.0, 158.0),
            find_charts.Word("=", 253.0, 150.0, 258.0, 158.0),
            find_charts.Word("10", 261.0, 150.0, 272.0, 158.0),
        ]
        page = find_charts.PageText(1, 595.0, 842.0, words)

        self.assertIsNone(
            find_charts.caption_axis_direction(
                page, title, "body_diode", find_charts._token_norm
            )
        )
        self.assertIsNone(
            find_charts.caption_leading_plot_bbox(
                page, title, "body_diode", find_charts._token_norm
            )
        )

    def test_current_axis_with_owned_tick_ladder_atomically_leads_plot(self):
        title = find_charts.DiagramTitle(
            13,
            "Typical reverse diode forward characteristics",
            (40.0, 100.0, 260.0, 112.0),
            "Figure 13. Typical reverse diode forward characteristics",
        )
        words = [
            find_charts.Word("0", 75.0, 278.0, 82.0, 286.0),
            find_charts.Word("5", 125.0, 278.0, 132.0, 286.0),
            find_charts.Word("10", 175.0, 278.0, 187.0, 286.0),
            find_charts.Word("I", 220.0, 278.0, 225.0, 286.0),
            find_charts.Word("SD", 225.0, 278.0, 235.0, 286.0),
            find_charts.Word("(A)", 238.0, 278.0, 250.0, 286.0),
        ]
        page = find_charts.PageText(1, 595.0, 842.0, words)

        self.assertEqual(
            find_charts.caption_axis_direction(
                page, title, "body_diode", find_charts._token_norm
            ),
            "below",
        )
        self.assertEqual(
            find_charts.caption_leading_plot_bbox(
                page, title, "body_diode", find_charts._token_norm
            ),
            (30.0, 114.0, 268.0, 292.0),
        )

    def test_caption_current_axis_and_frame_bound_ticks_recover_figure_13(self):
        with tempfile.TemporaryDirectory(prefix="stw46-body-diode-") as tmp:
            results = digitize_pdf(STW46NF30, Path(tmp), dpi=180)

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["panel"]["page"], 7)
        self.assertEqual(result["panel"]["diagram"], 13)
        self.assertEqual(result["panel"]["kind"], "body_diode")
        self.assertEqual(
            result["panel"]["bbox_pt"],
            (41.108, 116.974, 297.041, 292.94),
        )
        self.assertEqual(result["plot_box_px"], {"x0": 165, "y0": 85, "x1": 440, "y1": 405})
        self.assertEqual(
            result["hint_source"], "capacitance_grid_y_tick_frame_retry"
        )
        self.assertIn("source_axes_current_x_voltage_y", result["diagnostics"])
        self.assertEqual(
            [tick["value"] for tick in result["x_axis"]["ticks"]],
            [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0],
        )
        self.assertEqual(
            [tick["value"] for tick in result["y_axis"]["ticks"]],
            [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3],
        )
        self.assertLess(result["x_axis"]["residual_px"], 0.5)
        self.assertLess(result["y_axis"]["residual_px"], 0.5)

        by_temperature = {
            curve["temperature_c"]: curve for curve in result["curves"]
        }
        self.assertEqual(tuple(by_temperature), (-50.0, 25.0, 175.0))
        for curve in by_temperature.values():
            self.assertEqual(len(curve["points"]), 247)
            currents = [point[1] for point in curve["points"]]
            self.assertLess(max(currents), 30.0)
            self.assertGreater(max(currents), 29.8)
        for index in range(0, 247, 20):
            self.assertGreater(
                by_temperature[-50.0]["points"][index][0],
                by_temperature[25.0]["points"][index][0],
            )
            self.assertGreater(
                by_temperature[25.0]["points"][index][0],
                by_temperature[175.0]["points"][index][0],
            )


if __name__ == "__main__":
    unittest.main()
