"""Tests for the diode reverse-leakage plugin.

The end-to-end case uses the Vishay BAT54W-G datasheet (document 85885 Rev 1.1),
which is the panel the plugin was written against: five plotted temperatures,
25 through 125 C, a decade current axis in uA and a linear 0..30 V reverse axis.

Every guard below is exercised by CONSTRUCTING its target failure, not by
asserting that the happy path stays happy. A guard that has only ever been
observed to pass has not been tested.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from datasheet_chart_digitizer.chart_classifier import classify_chart
from datasheet_chart_digitizer.diode_reverse_leakage import (
    _verify_no_crossing,
    interpolate_leakage,
)

BAT54W = (
    Path.home()
    / "dev/ee/hw/o2-probe/datasheets/BAT54W-G-vishay-85885-rev1.1.pdf"
)


def _family(*curves: tuple[float, list[float]]) -> list[dict[str, object]]:
    """Build an assigned-family fixture from (temperature, log10 Ir) pairs."""
    return [
        {"temperature_c": temperature, "log10_ir_on_shared_grid": values}
        for temperature, values in curves
    ]


class ClassificationTests(unittest.TestCase):
    def test_reverse_leakage_caption_is_owned(self):
        self.assertEqual(
            classify_chart(
                "Typical Reverse Current vs. Reverse Voltage vs. Various Temperatures",
                "",
            ),
            "reverse_leakage",
        )

    def test_title_only_classification_works(self):
        """The caption finder classifies with ``text=""``; this must still bind.

        Side-by-side captions are segmented per figure before any panel text
        exists, so a text-only rule would never fire for a two-column layout --
        which is exactly how this chart went unsupported.
        """
        self.assertEqual(
            classify_chart("- Typical Reverse Current vs. Reverse Voltage vs.", ""),
            "reverse_leakage",
        )

    def test_mosfet_body_diode_is_not_claimed(self):
        """A third-quadrant MOSFET plot must stay with body_diode.

        Its curves cross; handing it to a digitizer whose safety gate assumes
        they cannot would turn a correct extraction into a hard failure, or
        worse, mislabel the temperatures.
        """
        self.assertEqual(
            classify_chart("Reverse Drain Source Characteristics", ""), "body_diode"
        )

    def test_recovery_and_test_circuits_are_not_claimed(self):
        for title in (
            "Typical Reverse Recovery vs. Reverse Voltage",
            "Reverse Current Test Circuit vs Reverse Voltage",
        ):
            with self.subTest(title=title):
                self.assertNotEqual(classify_chart(title, ""), "reverse_leakage")


class NonCrossingGateTests(unittest.TestCase):
    def test_monotone_family_passes(self):
        diagnostics = _verify_no_crossing(
            _family((25.0, [-7.0, -6.8]), (75.0, [-5.5, -5.2]), (125.0, [-4.0, -3.8]))
        )
        self.assertIn("noncrossing_verified_over_3_curves", diagnostics)

    def test_crossing_family_is_refused(self):
        """The target failure: a hot curve dipping below a cooler one."""
        with self.assertRaises(ValueError) as raised:
            _verify_no_crossing(
                _family((25.0, [-7.0, -4.0]), (125.0, [-4.0, -7.0]))
            )
        self.assertIn("cross", str(raised.exception))

    def test_barely_separated_family_is_refused(self):
        """Two copies of one curve must not pass as two temperatures."""
        with self.assertRaises(ValueError) as raised:
            _verify_no_crossing(
                _family((25.0, [-7.0] * 10), (125.0, [-7.0] * 9 + [-6.0]))
            )
        self.assertIn("separated over only", str(raised.exception))


class InterpolationTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            "curves": [
                {"temperature_c": 75.0, "points": [[1.0, 1e-6], [30.0, 1e-5]]},
                {"temperature_c": 100.0, "points": [[1.0, 1e-4], [30.0, 1e-3]]},
            ]
        }

    def test_interpolates_in_log_space(self):
        """85 C must land on the geometric, not the arithmetic, mean path.

        Between 1 uA and 100 uA, 40 % of the way, log interpolation gives
        6.3 uA; a linear-in-current interpolation would give 40 uA -- a factor
        of six, in the direction that makes a part look worse than it is.
        """
        value = interpolate_leakage(self.result, 1.0, 85.0)
        self.assertAlmostEqual(math.log10(value), -6.0 + 0.4 * 2.0, places=6)

    def test_refuses_temperature_extrapolation(self):
        with self.assertRaises(ValueError) as raised:
            interpolate_leakage(self.result, 1.0, 150.0)
        self.assertIn("without extrapolation", str(raised.exception))

    def test_refuses_voltage_extrapolation(self):
        with self.assertRaises(ValueError) as raised:
            interpolate_leakage(self.result, 0.2, 85.0)
        self.assertIn("outside this curve", str(raised.exception))


@unittest.skipUnless(BAT54W.is_file(), f"reference datasheet not present: {BAT54W}")
class Bat54wEndToEndTests(unittest.TestCase):
    """End-to-end against the real Vishay panel."""

    @classmethod
    def setUpClass(cls):
        import tempfile

        from datasheet_chart_digitizer.diode_reverse_leakage import digitize_pdf

        cls._tmp = tempfile.TemporaryDirectory(prefix="revleak-")
        out = Path(cls._tmp.name)
        cls.results = digitize_pdf(BAT54W, out, dpi=400)
        cls.out = out

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_one_panel_five_temperatures(self):
        self.assertEqual(len(self.results), 1)
        temperatures = [c["temperature_c"] for c in self.results[0]["curves"]]
        self.assertEqual(temperatures, [25.0, 50.0, 75.0, 100.0, 125.0])

    def test_axes_are_linear_volts_against_log_amps(self):
        result = self.results[0]
        self.assertEqual(result["y_axis"]["model"], "log10")
        self.assertEqual(result["x_axis"]["model"], "linear")

    def test_current_unit_is_read_not_assumed(self):
        """uA must come from the axis title; assuming it is a 1e6 error."""
        self.assertIn(
            "current_axis_unit_read_from_panel_as_uA", self.results[0]["diagnostics"]
        )

    def test_values_are_amps_and_match_the_printed_curves(self):
        """Spot-check against values legible on the printed chart.

        At the left edge the 25 C curve sits just under 0.1 uA and the 125 C
        curve just under 100 uA -- three decades apart, which is the sanity
        check that catches a collapsed or mis-scaled current axis.
        """
        curves = {c["temperature_c"]: c for c in self.results[0]["curves"]}
        cool = curves[25.0]["points"][0][1]
        hot = curves[125.0]["points"][0][1]
        self.assertLess(2e-8, cool)
        self.assertLess(cool, 1e-7)
        self.assertLess(5e-5, hot)
        self.assertLess(hot, 2e-4)
        self.assertAlmostEqual(math.log10(hot / cool), 3.26, delta=0.3)

    # Gridline centres measured on the 400 dpi crop by a raster column/row ink
    # profile (2026-09-29). The frame rules are 5 px thick (389-393, 1149-1153,
    # 308-312, 994-998); their centres are used, like every other line.
    GRID_X = {0: 391.0, 5: 517.0, 10: 644.0, 15: 770.5, 20: 897.5, 25: 1024.5, 30: 1151.0}
    GRID_Y = {1000: 310.0, 100: 447.5, 10: 585.0, 1: 720.5, 0.1: 858.0, 0.01: 996.0}

    def test_served_calibration_lands_on_the_gridlines(self):
        """The label-centre fit this replaced missed them by up to 6.9 px (x)
        and 5.7 px (y) while reporting a 1.4 px y residual against its labels."""
        from datasheet_chart_digitizer.gridline_anchor import served_pixel
        from datasheet_chart_digitizer.numeric_axis import NumericAxis

        result = self.results[0]
        for key, grid in (("x_axis", self.GRID_X), ("y_axis", self.GRID_Y)):
            payload = result[key]
            axis = NumericAxis(
                payload["model"], payload["m"], payload["b"], (), 0.0, ()
            )
            anchoring = result[f"{key}_anchoring"]
            self.assertEqual(anchoring["residual_basis"], "observed_lines")
            self.assertEqual(len(anchoring["ticks"]), len(grid))
            for value, line in grid.items():
                with self.subTest(axis=key, value=value):
                    self.assertLessEqual(abs(served_pixel(axis, value) - line), 1.0)
            self.assertLessEqual(anchoring["max_served_error_px"], 1.0)

    def test_25v_label_offset_is_measured_not_served(self):
        """Fab's catch: the "25" glyph sits ~12 px right of its gridline."""
        ticks = {t["value"]: t for t in self.results[0]["x_axis_anchoring"]["ticks"]}
        self.assertAlmostEqual(ticks[25.0]["label_offset_px"], 12.2, delta=1.0)
        self.assertAlmostEqual(ticks[25.0]["line_px"], 1024.5, delta=0.6)

    def test_eightyfive_c_value_pinned_after_grid_anchoring(self):
        """Ir(85 C, 1.34 V) = 8.30 uA on the gridline-anchored calibration.

        The label-centre calibration served 8.68 uA at the same point
        (-0.0195 decade, -4.4 %); across the served curves the anchoring moved
        Vr by -0.10..+0.27 V and log10(Ir) by -0.010..-0.036 decade.
        """
        value = interpolate_leakage(self.results[0], 1.34, 85.0)
        self.assertAlmostEqual(math.log10(value), math.log10(8.30e-6), delta=0.01)

    def test_eightyfive_c_interpolation_is_bounded_by_its_neighbours(self):
        result = self.results[0]
        lower = max(c["points"][0][0] for c in result["curves"])

        def at(temperature: float) -> float:
            return interpolate_leakage(result, lower, temperature)

        self.assertLess(at(75.0), at(85.0))
        self.assertLess(at(85.0), at(100.0))

    def test_overlay_is_written_for_human_review(self):
        overlay = self.out / self.results[0]["overlay"]
        self.assertTrue(overlay.is_file())
        self.assertGreater(overlay.stat().st_size, 10_000)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
