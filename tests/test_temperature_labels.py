"""Shared Celsius-label parser used by transfer and body-diode panels."""

from __future__ import annotations

import unittest
from pathlib import Path

from datasheet_chart_digitizer.diode_forward_voltage import _temperatures
from datasheet_chart_digitizer.diode_legend_color import temperatures_from_source_words
from datasheet_chart_digitizer.find_charts import run_text_bbox, words_in_bbox
from datasheet_chart_digitizer.finder_types import Word
from datasheet_chart_digitizer.transfer_temperature_labels import (
    LEGEND_LINE_RE,
    normalize_temperature_text,
    temperature_labels,
    temperature_values,
    temperature_values_in_text,
)

DATASHEETS = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets")


def _row(*tokens: str, x: float = 0.0, y: float = 10.0, gap: float = 2.0) -> list[Word]:
    words = []
    for token in tokens:
        width = 4.0 * len(token)
        words.append(Word(token, x, y, x + width, y + 8.0))
        x += width + gap
    return words


class TemperatureLabelTests(unittest.TestCase):
    def test_prefixed_and_split_labels_parse(self):
        cases = {
            ("Tj=25°C",): [25.0],
            ("Tj", "=", "25", "°C"): [25.0],
            ("125", "°C"): [125.0],
            ("TJ", "=", "-55", "°C"): [-55.0],
            ("T_J=150℃",): [150.0],
            ("T", "=", "25", "°C"): [25.0],
            ("Tj=−55°C",): [-55.0],  # U+2212 MINUS SIGN
            ("Tj", "=", "−", "55", "°C"): [-55.0],
            ("TJ", "=", "-", "55", "°C"): [-55.0],
            ("175", "o", "C"): [175.0],
            ("25ºC",): [25.0],
            ("25˚C",): [25.0],
            ("125", "deg", "C"): [125.0],
            ("Tc", "=", "25", "°C"): [25.0],
        }
        for tokens, expected in cases.items():
            with self.subTest(tokens=tokens):
                self.assertEqual(temperature_values(_row(*tokens)), expected)

    def test_non_temperature_numbers_are_not_labels(self):
        for tokens in (
            ("VGS", "=", "0", "V"),
            ("10", "A"),
            ("5", "C", "=", "Coss"),
            ("1", "Current"),
            ("tp", "=", "300", "µs"),
            ("1E+2",),
            ("0.5",),
        ):
            with self.subTest(tokens=tokens):
                self.assertEqual(temperature_values(_row(*tokens)), [])

    def test_touching_legend_entries_split_into_two_labels(self):
        # Two legend rows can abut: ``... −55 °CT = 25 °C``.
        words = _row("Tj", "=", "−55", "°CT", "=", "25", "°C")
        self.assertEqual(temperature_values(words), [-55.0, 25.0])

    def test_distant_tick_is_not_joined_to_unit(self):
        words = [*_row("10", x=0.0), *_row("150", "°C", x=40.0)]
        self.assertEqual(temperature_values(words), [150.0])

    def test_duplicate_label_values_collapse_but_positions_are_kept(self):
        words = [*_row("Tj", "=", "25", "°C", y=10.0), *_row("25", "°C", y=40.0)]
        labels = temperature_labels(words)
        self.assertEqual([label.value_c for label in labels], [25.0, 25.0])
        self.assertEqual(temperatures_from_source_words(words), [25.0])
        # The prefixed label's box owns the prefix words.
        prefixed = next(label for label in labels if label.prefixed)
        self.assertEqual(prefixed.x0, 0.0)

    def test_out_of_range_values_are_dropped(self):
        self.assertEqual(temperature_values(_row("400", "°C")), [])

    def test_flattened_text_parser(self):
        self.assertEqual(
            temperature_values_in_text("T = -55°C C T = 25°C C 1 Current T = 125°C C"),
            [-55.0, 25.0, 125.0],
        )
        self.assertEqual(
            temperature_values_in_text("Tj = 25 °C, VGS = 0 V, 10 A, T_J=150℃"),
            [25.0, 150.0],
        )
        self.assertEqual(_temperatures("TJ = −55 °C  Tj=175°C"), [-55.0, 175.0])

    def test_legend_line_keeps_maximum_role(self):
        match = LEGEND_LINE_RE.fullmatch(normalize_temperature_text("Tj = 25 °C, max"))
        self.assertIsNotNone(match)
        self.assertEqual(match.group("role"), "max")
        self.assertIsNone(LEGEND_LINE_RE.fullmatch("VGS = 0 V"))


class RealDatasheetTemperatureLabelTests(unittest.TestCase):
    """onsemi prints ``T`` ``J`` (subscript) ``=`` ``150`` ``o`` ``C``."""

    def _panel_values(self, relative: str, page: int, bbox: tuple[float, ...]):
        pdf = DATASHEETS / relative
        if not pdf.exists():
            self.skipTest(f"missing local corpus PDF: {pdf}")
        text = run_text_bbox(pdf)[page - 1]
        return temperature_values(words_in_bbox(text.words, bbox))

    def test_onsemi_subscript_j_and_superscript_o_labels(self):
        cases = (
            ("onsemi/FDS8840NZ.pdf", 4, (340.0, 480.0, 545.0, 665.0), [-55.0, 25.0, 150.0]),
            ("onsemi/FDS5670.pdf", 4, (320.0, 480.0, 520.0, 645.0), [-55.0, 25.0, 125.0]),
            ("onsemi/FDMS3662.pdf", 4, (320.0, 500.0, 555.0, 705.0), [-55.0, 25.0, 150.0]),
        )
        for relative, page, bbox, expected in cases:
            with self.subTest(pdf=relative):
                self.assertEqual(self._panel_values(relative, page, bbox), expected)

    def test_onsemi_fds5670_serves_three_labelled_curves(self):
        # Before the shared parser, ``T`` ``J`` ``=125`` ``o`` ``C`` lost the
        # 125 °C label and the panel refused "3 curves, 2 labels".  Values
        # checked against the 200 dpi source render (Figure 6, page 4).
        import tempfile

        from datasheet_chart_digitizer.diode_forward_voltage import digitize_pdf

        pdf = DATASHEETS / "onsemi/FDS5670.pdf"
        if not pdf.exists():
            self.skipTest(f"missing local corpus PDF: {pdf}")
        with tempfile.TemporaryDirectory() as tmp:
            (result,) = digitize_pdf(pdf, Path(tmp), dpi=220)
        self.assertEqual(result["status"], "ok")
        by_temperature = {curve["temperature_c"]: curve["points"] for curve in result["curves"]}
        self.assertEqual(sorted(by_temperature), [-55.0, 25.0, 125.0])
        # VSD at the 0.1 mA bottom of the log current axis: hot lowest.
        for temperature, vsd in ((125.0, 0.19), (25.0, 0.46), (-55.0, 0.65)):
            self.assertAlmostEqual(by_temperature[temperature][0][0], vsd, delta=0.02)


if __name__ == "__main__":
    unittest.main()
