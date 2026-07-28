"""OCR axis-evidence repair: mangled decade ladders, x outliers, rotated units.

Raster C(V) charts (NCE, goford) OCR their superscript decade labels into
tokens like ``10°``/``104``/``462``; repairs may only fire on strong column
evidence and must leave everything else untouched so refusal paths keep
failing closed.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from datasheet_chart_digitizer.capacitance_axis import (
    _drop_ocr_x_tick_outlier,
    _repair_ocr_decade_ladder,
    _unit_from_rotated_title,
)


class _Rect:
    def __init__(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1


PLOT = _Rect(65.0, 80.0, 290.0, 265.0)


def _gutter_word(cy: float, text: str) -> tuple[float, float, float, float, str]:
    return (46.0, cy - 3.0, 57.0, cy + 3.0, text)


def _xrow_word(cx: float, text: str) -> tuple[float, float, float, float, str]:
    return (cx - 5.0, PLOT.y1 + 6.0, cx + 5.0, PLOT.y1 + 14.0, text)


class DecadeLadderRepairTests(unittest.TestCase):
    def test_two_agreeing_anchors_rewrite_the_whole_ladder(self) -> None:
        # NCEP023N10 p3: '104' (row 1 -> top=5) and '462' (row 3 -> top=5)
        # agree; every row becomes an explicit power.
        words = [
            _gutter_word(93.0, "10°"),
            _gutter_word(133.0, "104"),
            _gutter_word(173.0, "40°"),
            _gutter_word(213.0, "462"),
            _gutter_word(253.0, "10°"),
        ]
        repaired = _repair_ocr_decade_ladder(words, PLOT)
        self.assertEqual(
            [w[4] for w in repaired], ["10⁵", "10⁴", "10³", "10²", "10¹"]
        )

    def test_single_anchor_is_not_enough_evidence(self) -> None:
        # One exposed digit could be an OCR lie; a lone anchor must not
        # position the whole ladder (a one-decade offset serves 10x values).
        words = [
            _gutter_word(93.0, "10°"),
            _gutter_word(133.0, "104"),
            _gutter_word(173.0, "40°"),
            _gutter_word(213.0, "10°"),
        ]
        self.assertEqual(_repair_ocr_decade_ladder(words, PLOT), words)

    def test_contradicting_anchors_refuse(self) -> None:
        # GT120N10: '107' appears at two different rows; the exposed digits
        # cannot both be right, so nothing may be rewritten.
        words = [
            _gutter_word(104.0, "107"),
            _gutter_word(155.0, "10°"),
            _gutter_word(207.0, "107"),
            _gutter_word(259.0, "10°"),
        ]
        self.assertEqual(_repair_ocr_decade_ladder(words, PLOT), words)

    def test_arithmetic_gutter_is_not_a_decade_column(self) -> None:
        # A clean linear ladder (24000..4000) must never be turned into
        # powers; the presence of any non-10^N numeric token refuses repair.
        words = [
            _gutter_word(94.0, "24000"),
            _gutter_word(118.0, "20000"),
            _gutter_word(142.0, "16000"),
            _gutter_word(166.0, "12000"),
            _gutter_word(191.0, "8000"),
            _gutter_word(215.0, "4000"),
        ]
        self.assertEqual(_repair_ocr_decade_ladder(words, PLOT), words)

    def test_non_uniform_spacing_refuses(self) -> None:
        words = [
            _gutter_word(93.0, "104"),
            _gutter_word(133.0, "10°"),
            _gutter_word(210.0, "462"),
            _gutter_word(253.0, "10°"),
        ]
        self.assertEqual(_repair_ocr_decade_ladder(words, PLOT), words)

    def test_bottom_exponent_below_zero_refuses(self) -> None:
        # Anchors solving a ladder that runs into negative pF decades are not
        # a plausible capacitance axis.
        words = [
            _gutter_word(93.0, "103"),
            _gutter_word(133.0, "102"),
            _gutter_word(173.0, "10°"),
            _gutter_word(213.0, "10°"),
        ]
        self.assertEqual(
            [w[4] for w in _repair_ocr_decade_ladder(words, PLOT)],
            ["10³", "10²", "10¹", "10⁰"],
        )
        words_low = [
            _gutter_word(93.0, "101"),
            _gutter_word(133.0, "100"),
            _gutter_word(173.0, "10°"),
            _gutter_word(213.0, "10°"),
        ]
        self.assertEqual(_repair_ocr_decade_ladder(words_low, PLOT), words_low)


class XTickOutlierDropTests(unittest.TestCase):
    def test_one_prepended_digit_tick_is_blanked(self) -> None:
        # NCEP026N10: tesseract reads '50' as '350'; the other eight ticks fit
        # a line exactly, so exactly that token is blanked.
        ticks = [(102.0, "10"), (120.0, "20"), (139.0, "30"), (157.0, "40"),
                 (176.0, "350"), (213.0, "70"), (232.0, "80"),
                 (251.0, "90"), (270.0, "100")]
        words = [_xrow_word(cx, text) for cx, text in ticks]
        repaired = _drop_ocr_x_tick_outlier(words, PLOT)
        self.assertEqual([w[4] for w in repaired].count(""), 1)
        self.assertEqual(repaired[4][4], "")

    def test_log_axis_ticks_survive(self) -> None:
        # A genuine log axis has no near-exact linear remainder; nothing may
        # be dropped even though the linear fit is terrible.
        ticks = [(80.0, "1"), (118.0, "2"), (143.0, "5"), (181.0, "10"),
                 (219.0, "20"), (244.0, "50"), (282.0, "100")]
        words = [_xrow_word(cx, text) for cx, text in ticks]
        self.assertEqual(_drop_ocr_x_tick_outlier(words, PLOT), words)

    def test_fewer_than_six_ticks_refuse(self) -> None:
        ticks = [(102.0, "10"), (139.0, "30"), (176.0, "350"),
                 (213.0, "70"), (251.0, "90")]
        words = [_xrow_word(cx, text) for cx, text in ticks]
        self.assertEqual(_drop_ocr_x_tick_outlier(words, PLOT), words)


class RotatedUnitTests(unittest.TestCase):
    def test_clean_pf_title(self) -> None:
        self.assertEqual(_unit_from_rotated_title("Capacitance (pF)"), "pf")
        self.assertEqual(_unit_from_rotated_title("C-Capacitance(pF)"), "pf")

    def test_degraded_or_ambiguous_reads_stay_absent(self) -> None:
        # 'oF' is exactly the pF-vs-nF ambiguity; two different units on the
        # strip is no evidence either.
        self.assertIsNone(_unit_from_rotated_title("Capacitance (oF)"))
        self.assertIsNone(_unit_from_rotated_title("pF or nF"))
        self.assertIsNone(_unit_from_rotated_title(None))
        self.assertIsNone(_unit_from_rotated_title("Voltage (V)"))


_REFRESH = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/out/fugu2-100v-LS1p/coss-review-top50-2026-07-28-refresh"
)


@unittest.skipUnless(_REFRESH.exists(), "local refresh packet not available")
class NcepRasterEndToEndTests(unittest.TestCase):
    def test_ncep023n10_recovers_trusted_log_calibration(self) -> None:
        from PIL import Image
        from datasheet_chart_digitizer.capacitance_plot_box import (
            find_capacitance_plot_box,
        )
        from datasheet_chart_digitizer.capacitance_axis import (
            infer_ocr_position_axis_calibration,
            reject_bad_position_calibration,
        )

        charts = json.loads((_REFRESH / "charts.json").read_text())
        chart = next(
            c for c in charts if c["part"] == "NCEP023N10" and c["diagram"] == 7
        )
        image = np.asarray(Image.open(_REFRESH / chart["crop_png"]).convert("L"))
        plot = find_capacitance_plot_box(image)
        calibration = infer_ocr_position_axis_calibration(chart, image, plot)
        self.assertIsNone(reject_bad_position_calibration(calibration, plot))
        self.assertTrue(calibration.y_log)
        self.assertEqual(calibration.y_decades, (1.0, 2.0, 3.0, 4.0, 5.0))
        self.assertEqual(calibration.x_ticks_v, (20.0, 40.0, 60.0, 80.0))


if __name__ == "__main__":
    unittest.main()
