"""Unit tests for position-based axis calibration (log-X detection tiers)."""

import math
import unittest
from unittest import mock

import numpy as np

from datasheet_chart_digitizer import axis_calibration, capacitance_axis
from datasheet_chart_digitizer.capacitance_types import AxisCalibration, PlotBox


class _WordsPage:
    """Minimal stand-in for a PyMuPDF page: get_text('words') tuples."""

    def __init__(self, words):
        self._words = words

    def get_text(self, kind):
        return list(self._words)


def _word(cx: float, cy: float, text: str):
    return (cx - 5.0, cy - 4.0, cx + 5.0, cy + 4.0, text)


def _y_decade_words():
    # Plain-decade labels 10^4 / 10^3 / 10^2 spaced evenly down the left edge.
    return [
        _word(60.0, 100.0, "10000"),
        _word(60.0, 150.0, "1000"),
        _word(60.0, 200.0, "100"),
    ]


_BANDS = dict(x_row_band=(205.0, 225.0), y_label_x_band=(40.0, 80.0), plot_y_band=(90.0, 210.0))


class CalibrateAxesLogXTests(unittest.TestCase):
    def test_decade_ticks_fit_log(self) -> None:
        values = [0.1, 1.0, 10.0, 100.0]
        words = [_word(100.0 + 100.0 * math.log10(v), 215.0, str(v)) for v in values]
        cal = axis_calibration.calibrate_axes(_WordsPage(words + _y_decade_words()), **_BANDS)
        self.assertTrue(cal.x_log)
        self.assertAlmostEqual(cal.v_of_x(100.0 + 100.0 * math.log10(10.0)), 10.0, delta=0.05)

    def test_one_two_five_ticks_fit_log_via_dual_fit(self) -> None:
        # The common 1-2-5 sub-decade labeling fails the pure decade-ratio
        # test; the dual linear/log fit must still classify the axis as log.
        values = [1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0]
        words = [_word(100.0 + 100.0 * math.log10(v), 215.0, str(v)) for v in values]
        cal = axis_calibration.calibrate_axes(_WordsPage(words + _y_decade_words()), **_BANDS)
        self.assertTrue(cal.x_log)
        self.assertLess(cal.x_resid, 0.01)  # decades
        self.assertAlmostEqual(cal.v_of_x(100.0 + 100.0 * math.log10(20.0)), 20.0, delta=0.5)

    def test_linear_ticks_stay_linear(self) -> None:
        values = [0.0, 20.0, 40.0, 60.0, 80.0, 100.0]
        words = [_word(100.0 + 2.0 * v, 215.0, f"{v:g}") for v in values]
        cal = axis_calibration.calibrate_axes(_WordsPage(words + _y_decade_words()), **_BANDS)
        self.assertFalse(cal.x_log)
        self.assertAlmostEqual(cal.v_of_x(100.0 + 2.0 * 40.0), 40.0, delta=0.1)

    def test_positive_only_linear_ticks_stay_linear(self) -> None:
        # All-positive but narrow-span (<1.5 decades) linear labels must not
        # trip the dual-fit log path.
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        words = [_word(100.0 + 3.0 * v, 215.0, f"{v:g}") for v in values]
        cal = axis_calibration.calibrate_axes(_WordsPage(words + _y_decade_words()), **_BANDS)
        self.assertFalse(cal.x_log)


class GridTierLogXRefusalTests(unittest.TestCase):
    def test_gridline_tier_refuses_log_spaced_ticks(self) -> None:
        # The grid tier maps px->V linearly between the extreme ticks and is
        # reported as TRUSTED; on a log axis it must refuse, not mis-scale.
        fake_text_cal = AxisCalibration(
            x_min_v=0.1,
            x_max_v=100.0,
            y_min_decade=1.0,
            y_max_decade=5.0,
            source="chart_text",
            x_ticks_v=(0.1, 1.0, 10.0, 100.0),
            y_decades=(1.0, 2.0, 3.0, 4.0, 5.0),
        )
        plot = PlotBox(x0=0, y0=0, x1=99, y1=99)
        image = np.full((120, 120), 255, dtype=np.uint8)
        with mock.patch.object(
            capacitance_axis, "infer_text_order_axis_calibration", return_value=fake_text_cal
        ):
            with self.assertRaisesRegex(RuntimeError, "log-spaced X ticks"):
                capacitance_axis.infer_gridline_axis_calibration({}, image, plot)


if __name__ == "__main__":
    unittest.main()
