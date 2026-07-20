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


class _SpanPage(_WordsPage):
    def __init__(self, words, lines):
        super().__init__(words)
        self._lines = lines

    def get_text(self, kind):
        if kind == "dict":
            return {"blocks": [{"lines": self._lines}]}
        return super().get_text(kind)


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
    def test_strictly_negative_vds_ticks_serve_log_magnitude_with_provenance(self) -> None:
        source_values = [-0.1, -1.0, -10.0, -100.0]
        words = [
            _word(100.0 + 50.0 * math.log10(abs(value) / 0.1), 215.0, f"{value:g}")
            for value in source_values
        ]

        cal = axis_calibration.calibrate_axes(
            _WordsPage(words + _y_decade_words()), **_BANDS
        )

        self.assertTrue(cal.x_log)
        self.assertEqual(
            tuple(value for value, _pixel in cal.x_ticks),
            (0.1, 1.0, 10.0, 100.0),
        )
        self.assertEqual(cal.x_source_ticks, tuple(source_values))
        self.assertEqual(cal.x_value_transform, "abs_source_negative_vds")
        self.assertLess(cal.x_resid, 1e-9)

    def test_mixed_sign_vds_ticks_refuse_magnitude_transform(self) -> None:
        words = [
            _word(100.0 + index * 50.0, 215.0, value)
            for index, value in enumerate(("-0.1", "1", "10"))
        ]

        with self.assertRaisesRegex(RuntimeError, "mixed-sign VDS"):
            axis_calibration.calibrate_axes(
                _WordsPage(words + _y_decade_words()), **_BANDS
            )

    def test_sparse_negative_vds_ticks_refuse_magnitude_transform(self) -> None:
        words = [
            _word(100.0 + index * 50.0, 215.0, value)
            for index, value in enumerate(("-1", "-10"))
        ]

        with self.assertRaisesRegex(RuntimeError, "needs >=3 distinct ticks"):
            axis_calibration.calibrate_axes(
                _WordsPage(words + _y_decade_words()), **_BANDS
            )

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

    def test_clipped_trailing_multi_digit_tick_does_not_poison_fit(self) -> None:
        words = [
            _word(100.0 + index * 40.0, 215.0, value)
            for index, value in enumerate(("0", "20", "40", "60", "80", "1"))
        ]

        cal = axis_calibration.calibrate_axes(
            _WordsPage(words + _y_decade_words()), **_BANDS
        )

        self.assertEqual(
            (0.0, 20.0, 40.0, 60.0, 80.0),
            tuple(value for value, _pixel in cal.x_ticks),
        )
        self.assertFalse(cal.x_log)
        self.assertAlmostEqual(100.0, cal.v_of_x(300.0), places=6)

    def test_positive_only_linear_ticks_stay_linear(self) -> None:
        # All-positive but narrow-span (<1.5 decades) linear labels must not
        # trip the dual-fit log path.
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        words = [_word(100.0 + 3.0 * v, 215.0, f"{v:g}") for v in values]
        cal = axis_calibration.calibrate_axes(_WordsPage(words + _y_decade_words()), **_BANDS)
        self.assertFalse(cal.x_log)

    def test_native_superscript_spans_restore_exact_log_tick_values(self) -> None:
        values = [0.01, 0.1, 1.0, 10.0, 100.0]
        texts = ["10-2", "10-1", "1", "10", "102"]
        words = [
            _word(100.0 + 50.0 * math.log10(value), 215.0, text)
            for value, text in zip(values, texts)
        ]
        lines = []
        for value in (0.01, 0.1, 100.0):
            cx = 100.0 + 50.0 * math.log10(value)
            exponent = int(round(math.log10(value)))
            lines.append(
                {
                    "spans": [
                        {"text": "10", "size": 7.0, "bbox": (cx - 5.0, 211.0, cx + 1.0, 219.0)},
                        {
                            "text": str(exponent),
                            "size": 5.0,
                            "bbox": (cx + 1.0, 209.0, cx + 5.0, 215.0),
                        },
                    ]
                }
            )
        cal = axis_calibration.calibrate_axes(
            _SpanPage(words + _y_decade_words(), lines), **_BANDS
        )
        self.assertTrue(cal.x_log)
        self.assertEqual(tuple(value for value, _ in cal.x_ticks), tuple(values))
        self.assertLess(cal.x_resid, 1e-9)

    def test_real_linear_102_tick_is_not_reinterpreted_without_superscript_geometry(self) -> None:
        values = [0.0, 51.0, 102.0]
        words = [_word(100.0 + value, 215.0, f"{value:g}") for value in values]
        cal = axis_calibration.calibrate_axes(_WordsPage(words + _y_decade_words()), **_BANDS)
        self.assertFalse(cal.x_log)
        self.assertEqual(tuple(value for value, _ in cal.x_ticks), tuple(values))

    def test_unicode_superscript_number_tokens_include_signed_exponents(self) -> None:
        self.assertEqual(axis_calibration._number_tokens("10⁻² 10² 3.5"), [0.01, 100.0, 3.5])

    def test_uppercase_engineering_axis_tokens_preserve_multiplier(self) -> None:
        self.assertEqual(
            axis_calibration._number_tokens("1K 10K 3.3M 2G"),
            [1e3, 1e4, 3.3e6, 2e9],
        )
        self.assertEqual(
            axis_calibration._number_tokens("4m 10kohm 1MHz 10KΩ 1MΩ"),
            [],
        )

    def test_position_fit_accepts_k_suffixed_y_decades(self) -> None:
        x_words = [
            _word(100 + index * 50, 215, value)
            for index, value in enumerate(("1", "10", "100"))
        ]
        y_words = [
            _word(75, 80 + index * 50, value)
            for index, value in enumerate(("10K", "1K", "100", "10", "1"))
        ]
        cal = axis_calibration.calibrate_axes(
            _WordsPage(x_words + y_words),
            **{**_BANDS, "plot_y_band": (70.0, 290.0)},
        )
        self.assertEqual(tuple(value for value, _ in cal.y_decades), (4.0, 3.0, 2.0, 1.0, 0.0))
        self.assertLess(cal.y_resid, 1e-9)

    def test_position_fit_accepts_interleaved_subunit_nf_decades(self) -> None:
        x_words = [
            _word(100 + index * 50, 265, value)
            for index, value in enumerate(("0", "5", "10"))
        ]
        y_words = [
            _word(70, 80 + index * 40, value)
            for index, value in enumerate(("10", "1", "0.1", "0.01", "0.001"))
        ]
        formula_words = [
            _word(180, 100, "1MHz"),
            _word(180, 140, "0V"),
            _word(180, 180, "10"),
        ]
        bands = {
            **_BANDS,
            "x_row_band": (255.0, 275.0),
            "plot_y_band": (70.0, 250.0),
        }

        first = axis_calibration.calibrate_axes(
            _WordsPage(formula_words + y_words[::-1] + x_words + [_word(52, 140, "nF")]),
            **bands,
        )
        repeat = axis_calibration.calibrate_axes(
            _WordsPage(x_words + y_words + [_word(52, 140, "nF")] + formula_words),
            **bands,
        )

        self.assertTrue(first.y_log)
        self.assertEqual(first.y_decades, repeat.y_decades)
        self.assertEqual(
            tuple(value for value, _pixel in first.y_decades),
            (4.0, 3.0, 2.0, 1.0, 0.0),
        )
        self.assertLess(first.y_resid, 1e-9)

    def test_position_fit_accepts_owned_dense_log_pf_ladder(self) -> None:
        x_words = [
            _word(100.0 + 100.0 * math.log10(value), 215.0, f"{value:g}")
            for value in (0.2, 1.0, 10.0)
        ]
        y_words = [
            _word(70.0, 100.0 + 120.0 * math.log10(10.0 / value), f"{value:g}")
            for value in range(2, 11)
        ]

        cal = axis_calibration.calibrate_axes(
            _WordsPage(x_words + y_words + [_word(52.0, 140.0, "(pF)")]),
            **_BANDS,
        )

        self.assertTrue(cal.x_log)
        self.assertTrue(cal.y_log)
        self.assertEqual(
            tuple(round(10.0 ** exponent) for exponent, _pixel in cal.y_decades),
            tuple(range(2, 11)),
        )
        self.assertLess(cal.y_resid, 1e-9)

    def test_dense_arithmetic_pf_ladder_keeps_linear_y_path(self) -> None:
        x_words = [_word(100 + index * 40, 215, value) for index, value in enumerate(("0", "5", "10"))]
        y_words = [
            _word(70, 205 - index * 12, str(value))
            for index, value in enumerate(range(2, 11))
        ]

        cal = axis_calibration.calibrate_axes(
            _WordsPage(x_words + y_words + [_word(52, 140, "[pF]")]),
            **_BANDS,
        )

        self.assertFalse(cal.y_log)
        self.assertEqual(tuple(value for value, _pixel in cal.y_decades), tuple(range(2, 11)))

    def test_dense_log_ladder_requires_exact_owned_unit(self) -> None:
        labels = [
            (float(value), 80.0 + 120.0 * math.log10(10.0 / value))
            for value in range(2, 11)
        ]
        self.assertIsNotNone(
            axis_calibration._positioned_log_capacitance_y_fit(labels, {"pf"})
        )
        for units in (set(), {"pf", "nf"}):
            with self.subTest(units=units):
                self.assertIsNone(
                    axis_calibration._positioned_log_capacitance_y_fit(labels, units)
                )
        for token in ("pF/V", "(pF/V)", "1MHz", "pF)", "(pF"):
            with self.subTest(token=token):
                self.assertIsNone(axis_calibration._capacitance_unit_token(token))

    def test_noisy_dense_ladder_that_is_not_decisively_log_refuses(self) -> None:
        labels = [
            (2.0, 200.0),
            (3.0, 180.0),
            (4.0, 157.0),
            (5.0, 143.0),
            (6.0, 125.0),
            (7.0, 112.0),
            (8.0, 96.0),
            (9.0, 84.0),
            (10.0, 70.0),
        ]
        self.assertIsNone(
            axis_calibration._positioned_log_capacitance_y_fit(labels, {"pf"})
        )

    def test_subunit_log_fit_refuses_missing_or_non_power_labels(self) -> None:
        for labels in (
            [(10.0, 80.0), (1.0, 120.0), (0.01, 160.0)],
            [(10.0, 80.0), (1.0, 120.0), (0.5, 160.0), (0.1, 200.0)],
        ):
            with self.subTest(labels=labels):
                self.assertIsNone(
                    axis_calibration._subunit_log_capacitance_y_fit(labels, {"nf"})
                )
        self.assertIsNone(
            axis_calibration._subunit_log_capacitance_y_fit(
                [(10.0, 80.0), (1.0, 120.0), (0.1, 160.0)], set()
            )
        )

    def test_position_fit_accepts_owned_linear_nf_ladder(self) -> None:
        x_words = [
            _word(100 + index * 40, 215, value)
            for index, value in enumerate(("0", "5", "10", "15", "20", "25"))
        ]
        y_words = [
            _word(70, 180 - index * 40, value)
            for index, value in enumerate(("0.5", "1.0", "1.5", "2.0"))
        ]
        cal = axis_calibration.calibrate_axes(
            _WordsPage(x_words + y_words + [_word(52, 120, "nF")]),
            **{**_BANDS, "y_label_x_band": (40.0, 80.0), "plot_y_band": (40.0, 210.0)},
        )

        self.assertFalse(cal.y_log)
        self.assertEqual(tuple(value for value, _ in cal.y_decades), (500.0, 1000.0, 1500.0, 2000.0))
        self.assertLess(cal.y_resid, 1e-8)

    def test_position_fit_refuses_linear_ladder_without_owned_unit(self) -> None:
        x_words = [_word(100 + index * 40, 215, value) for index, value in enumerate(("0", "5", "10"))]
        y_words = [_word(70, 180 - index * 40, value) for index, value in enumerate(("0.5", "1.0", "1.5", "2.0"))]

        with self.assertRaisesRegex(RuntimeError, "owned arithmetic capacitance labels"):
            axis_calibration.calibrate_axes(
                _WordsPage(x_words + y_words),
                **{**_BANDS, "y_label_x_band": (40.0, 80.0), "plot_y_band": (40.0, 210.0)},
            )

    def test_position_fit_refuses_sparse_or_irregular_linear_ladder(self) -> None:
        x_words = [_word(100 + index * 40, 215, value) for index, value in enumerate(("0", "5", "10"))]
        for values in (("0.5", "1.0", "1.5"), ("0.5", "1.0", "1.7", "2.0")):
            with self.subTest(values=values):
                y_words = [_word(70, 180 - index * 40, value) for index, value in enumerate(values)]
                with self.assertRaisesRegex(RuntimeError, "owned arithmetic capacitance labels"):
                    axis_calibration.calibrate_axes(
                        _WordsPage(x_words + y_words + [_word(52, 120, "nF")]),
                        **{**_BANDS, "y_label_x_band": (40.0, 80.0), "plot_y_band": (40.0, 210.0)},
                    )

    def test_position_fit_refuses_duplicate_linear_ladder(self) -> None:
        x_words = [_word(100 + index * 40, 215, value) for index, value in enumerate(("0", "5", "10"))]
        y_words = [
            _word(70, 180 - index * 40, value)
            for index, value in enumerate(("0.5", "1.0", "1.0", "1.5"))
        ]

        with self.assertRaisesRegex(RuntimeError, "duplicate Y tick values"):
            axis_calibration.calibrate_axes(
                _WordsPage(x_words + y_words + [_word(52, 120, "nF")]),
                **{**_BANDS, "y_label_x_band": (40.0, 80.0), "plot_y_band": (40.0, 210.0)},
            )

    def test_log_decades_take_priority_over_linear_unit_fallback(self) -> None:
        x_words = [_word(100 + index * 40, 215, value) for index, value in enumerate(("0", "5", "10"))]
        y_words = [
            _word(70, 180 - index * 40, value)
            for index, value in enumerate(("1", "10", "100", "1000"))
        ]

        cal = axis_calibration.calibrate_axes(
            _WordsPage(x_words + y_words + [_word(52, 120, "pF")]),
            **{**_BANDS, "y_label_x_band": (40.0, 80.0), "plot_y_band": (40.0, 210.0)},
        )

        self.assertTrue(cal.y_log)
        self.assertEqual(tuple(value for value, _pixel in cal.y_decades), (3.0, 2.0, 1.0, 0.0))


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
