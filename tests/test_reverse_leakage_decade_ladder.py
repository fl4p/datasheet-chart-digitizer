"""Decade-label formats on the reverse-leakage current axis.

Real leakage panels print their decade ladder as ``10`` plus a raised
exponent span (PDF text keeps them as two spans, the exponent often with a
U+2212 minus), as Unicode superscripts (``10⁻⁶``), as spreadsheet E-notation
(``1E-2``, ``1.0E-02``), or with SI prefixes (``100n``, ``1µ``, ``10µA``).
Every format must read to the exact value, and every known-bad reading --
a flipped exponent sign, one contradicting label, a one-decade slip, a linear
ladder, a unit applied twice -- must refuse rather than serve.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from datasheet_chart_digitizer.crop_transform import CropTransform
from datasheet_chart_digitizer.diode_forward_voltage import TextLabel, _page_labels
from datasheet_chart_digitizer.diode_reverse_leakage import (
    _current_unit_scale,
    _fit_ladder,
    _require_reverse_leakage_axes,
)
from datasheet_chart_digitizer.numeric_axis import (
    AxisTick,
    NumericAxis,
    fit_numeric_axis,
    parse_tick_text,
)

SET1 = Path("/Users/fab/dev/pv/ee/datasheet-chart-digitizer/out/synth-charts/set1/pdf")


def _ladder(texts: list[str], pitch: float = 50.0) -> list[tuple[str, float]]:
    """Labels top-to-bottom at equal pitch (a y axis: larger value higher)."""
    return [(text, 10.0 + pitch * index) for index, text in enumerate(reversed(texts))]


class TokenSemanticsTests(unittest.TestCase):
    def test_unicode_superscript_powers_are_exponents_on_every_axis(self):
        self.assertEqual(parse_tick_text("10⁻⁶"), 1e-6)
        self.assertEqual(parse_tick_text("10⁶"), 1e6)
        self.assertEqual(parse_tick_text("10⁰"), 1.0)
        self.assertEqual(parse_tick_text("10²"), 100.0)  # never 102

    def test_current_prefixes_and_e_notation_are_exact(self):
        cases = {
            "1µ": 1e-6, "1u": 1e-6, "1μ": 1e-6, "100n": 1e-7, "10n": 1e-8,
            "1n": 1e-9, "100p": 1e-10, "1m": 1e-3, "10µA": 1e-5, "1nA": 1e-9,
            "1E-2": 1e-2, "1E+0": 1.0, "1.0E-02": 1e-2, "0.001": 1e-3, "1000": 1e3,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertAlmostEqual(parse_tick_text(text, "current_a"), expected, delta=expected * 1e-12)

    def test_prefixes_stay_context_gated(self):
        for text in ("1m", "100n", "10µA", "1E-2"):
            with self.subTest(text=text):
                self.assertIsNone(parse_tick_text(text))
        for text in ("1MHz", "5V", "1mV", "1E-2n", "Tj"):
            with self.subTest(text=text):
                self.assertIsNone(parse_tick_text(text, "current_a"))

    def test_mixed_formats_share_one_log_fit(self):
        axis = fit_numeric_axis(
            _ladder(["100µ", "1m", "10m", "100m", "1", "10", "100"]), "Y", quantity="current_a"
        )
        self.assertEqual(axis.model, "log10")
        self.assertLess(axis.residual_px, 1e-6)
        self.assertAlmostEqual(axis.value(10.0), 100.0)
        self.assertAlmostEqual(axis.value(310.0), 1e-4)


class KnownBadLadderTests(unittest.TestCase):
    def test_flipped_exponent_sign_refuses(self):
        # 10⁻² mis-read as 10² inside a 10⁻⁴..10⁰ ladder breaks monotonicity
        with self.assertRaisesRegex(RuntimeError, "monotone"):
            fit_numeric_axis(_ladder(["10⁻⁴", "10⁻³", "10²", "10⁻¹", "10⁰"]), "Y")

    def test_one_contradicting_label_in_mixed_formats_refuses(self):
        # "100µ" printed where the ladder position says 10µ (1E-5)
        with self.assertRaisesRegex(RuntimeError, "untrusted"):
            fit_numeric_axis(
                _ladder(["100n", "1µ", "100µ", "1E-4", "1m"]), "Y", quantity="current_a"
            )

    def test_one_decade_slip_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "untrusted"):
            fit_numeric_axis(_ladder(["10⁻⁶", "10⁻⁵", "10⁻³", "10⁻²", "10⁻¹"]), "Y")

    def test_linear_ladder_is_not_a_leakage_axis(self):
        linear = fit_numeric_axis(_ladder(["0", "2", "4", "6", "8"]), "Y")
        self.assertEqual(linear.model, "linear")
        with self.assertRaisesRegex(ValueError, "not logarithmic"):
            _require_reverse_leakage_axes(SimpleNamespace(y_axis=linear, x_axis=linear))

    def test_log_ladder_needs_three_power_of_ten_labels(self):
        x = fit_numeric_axis([("0", 0), ("10", 100), ("20", 200)], "X")
        one_two_five = NumericAxis(
            "log10", -0.01, 1.0,
            tuple(AxisTick(t, v, p) for t, v, p in (("1", 1, 100), ("2", 2, 70), ("5", 5, 30), ("10", 10, 0))),
            0.5, (),
        )
        with self.assertRaisesRegex(ValueError, "power-of-ten"):
            _require_reverse_leakage_axes(SimpleNamespace(y_axis=one_two_five, x_axis=x))


def _unit_axis(texts: list[str]) -> NumericAxis:
    return fit_numeric_axis(_ladder(texts), "Y", quantity="current_a")


class ServedUnitTests(unittest.TestCase):
    def panel(self, text: str):
        return SimpleNamespace(text=text)

    def test_bare_si_ticks_take_the_title_unit_once(self):
        axis = _unit_axis(["100n", "1µ", "10µ", "100µ"])
        scale, unit = _current_unit_scale(self.panel("Reverse Current IR [µA]"), axis)
        self.assertEqual((scale, unit), (1e-6, "uA"))

    def test_bracketed_title_is_read(self):
        self.assertEqual(_current_unit_scale(self.panel("IR, Reverse Current [nA]"))[0], 1e-9)

    def test_self_dimensioned_ticks_are_amps(self):
        axis = _unit_axis(["1nA", "10nA", "100nA", "1µA"])
        self.assertEqual(_current_unit_scale(self.panel("IR (A)"), axis), (1.0, "A"))
        self.assertEqual(_current_unit_scale(self.panel("Reverse current"), axis), (1.0, "A"))

    def test_title_prefix_over_self_dimensioned_ticks_refuses(self):
        axis = _unit_axis(["1nA", "10nA", "100nA", "1µA"])
        with self.assertRaisesRegex(ValueError, "double"):
            _current_unit_scale(self.panel("Reverse Current IR (µA)"), axis)

    def test_mixed_self_dimensioned_and_bare_refuses(self):
        axis = _unit_axis(["1n", "10nA", "100n", "1µ"])
        with self.assertRaisesRegex(ValueError, "mix"):
            _current_unit_scale(self.panel("IR (A)"), axis)


    def test_rotated_title_unit_is_read_from_crop_words(self):
        panel = self.panel("T = 25 °C Tj=50°C 20 40 60")
        self.assertEqual(_current_unit_scale(panel, None, ("IR,", "Current", "(µA)")), (1e-6, "uA"))
        with self.assertRaisesRegex(ValueError, "cannot read"):
            _current_unit_scale(panel, None, ("IR,", "Current"))
        with self.assertRaisesRegex(ValueError, "different current units"):
            _current_unit_scale(panel, None, ("(µA)", "[nA]"))


class GluedTemperatureTokenTests(unittest.TestCase):
    def test_tj_equals_token_is_one_temperature(self):
        from datasheet_chart_digitizer.diode_legend_color import temperatures_from_source_words

        def word(text, x0):
            return SimpleNamespace(text=text, x0=x0, x1=x0 + 20.0, y0=0.0, y1=8.0)

        words = [word("Tj=150°C", 0), word("TJ=25°C", 100), word("VGS=10V", 200), word("Ta=85°C", 300)]
        self.assertEqual(temperatures_from_source_words(words), [25.0, 150.0])


class _FakePage:
    """The two PyMuPDF text views _page_labels reads, as set1 page_0127 emits them."""

    def __init__(self, words, lines):
        self._words, self._lines = words, lines

    def get_text(self, kind):
        if kind == "words":
            return self._words
        return {"blocks": [{"lines": [{"spans": spans} for spans in self._lines]}]}


class RaisedExponentSpanTests(unittest.TestCase):
    def test_baseline_ten_plus_raised_minus_exponent_becomes_a_power(self):
        words, lines = [], []
        for row, exponent in enumerate(("−2", "−1", "2", "3")):
            y = 100.0 + 20.0 * row
            words.append((80.0, y, 91.0, y + 8.0, f"10{exponent}", 0, 0, 0))
            lines.append([
                {"text": "10", "size": 7.8, "bbox": (80.0, y, 88.0, y + 8.0)},
                {"text": exponent, "size": 5.5, "bbox": (88.2, y - 1.0, 91.0, y + 5.0)},
            ])
        transform = CropTransform.for_chart(
            {"crop_box_pt": (0.0, 0.0, 200.0, 200.0)}, (200, 200)
        )
        texts = [label.text for label in _page_labels(_FakePage(words, lines), transform)]
        self.assertEqual(texts, ["10^-2", "10^-1", "10^2", "10^3"])
        self.assertEqual([parse_tick_text(t) for t in texts], [1e-2, 1e-1, 1e2, 1e3])

    def test_same_size_digits_stay_an_ordinary_number(self):
        words = [(80.0, 100.0, 92.0, 108.0, "102", 0, 0, 0)]
        lines = [[
            {"text": "10", "size": 7.8, "bbox": (80.0, 100.0, 88.0, 108.0)},
            {"text": "2", "size": 7.8, "bbox": (88.0, 100.0, 92.0, 108.0)},
        ]]
        transform = CropTransform.for_chart({"crop_box_pt": (0.0, 0.0, 200.0, 200.0)}, (200, 200))
        self.assertEqual([l.text for l in _page_labels(_FakePage(words, lines), transform)], ["102"])


class RightAlignedLadderTests(unittest.TestCase):
    def labels(self, texts, x1=60.0):
        return [
            TextLabel(text, x1 - 10.0, 20.0 + 40.0 * i, x1 - 20.0, x1)
            for i, text in enumerate(texts)
        ]

    def test_superscript_ladder_is_found_and_centred_annotations_are_not(self):
        ladder = self.labels(["10⁴", "10³", "10²", "10¹", "10⁰", "10⁻¹", "10⁻²"])
        annotations = [TextLabel(t, 200.0 + 17 * i, 60.0 + 50 * i, 190.0 + 23 * i, 210.0 + 11 * i)
                       for i, t in enumerate(("150", "125", "100", "75"))]
        axis, used = _fit_ladder(
            ladder + annotations, align_attr="x1", position_attr="cy",
            align_tolerance=4.0, min_span=100.0, name="Y axis", quantity="current_a",
        )
        self.assertEqual(axis.model, "log10")
        self.assertEqual(len(used), 7)
        self.assertAlmostEqual(axis.value(20.0), 1e4)
        self.assertAlmostEqual(axis.value(260.0), 1e-2)


LIB = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/onsemi")


class RealDatasheetLadderTests(unittest.TestCase):
    """Real leakage panels whose decade ladders the old prefilter dropped.

    FDMS0310AS p7 Fig 15 (SyncFET body-diode reverse leakage): "10" + a
    separate smaller "−2" span, U+2212 minus. NVMTS001N06CLTXG p3 Fig 6
    (reverse leakage, IDSS (A)): one-span "1E−03".."1E−10". The region is the
    Y-label column of that figure in PDF points.
    """

    def ladder(self, pdf: Path, page: int, region: tuple[float, float, float, float]):
        import pymupdf

        if not pdf.is_file():
            self.skipTest(f"{pdf} not in the local library")
        scale = 2.0
        shape = (int((region[3] - region[1]) * scale), int((region[2] - region[0]) * scale))
        transform = CropTransform.for_chart({"crop_box_pt": region}, shape)
        with pymupdf.open(pdf) as doc:
            labels = _page_labels(doc[page - 1], transform)
        inside = [l for l in labels if 0 <= l.cy <= shape[0] and 0 <= l.cx <= shape[1]
                  and parse_tick_text(l.text, "current_a") is not None]
        axis, _ = _fit_ladder(inside, align_attr="x1", position_attr="cy", align_tolerance=4.0,
                              min_span=0.35 * shape[0], name="Y axis", quantity="current_a")
        return axis

    def test_fdms0310as_raised_exponent_spans(self):
        axis = self.ladder(LIB / "FDMS0310AS.pdf", 7, (320.0, 155.0, 600.0, 330.0))
        self.assertEqual(axis.model, "log10")
        self.assertEqual(sorted(t.value for t in axis.ticks), [1e-6, 1e-5, 1e-4, 1e-3, 1e-2])
        self.assertEqual({t.text for t in axis.ticks}, {"10^-6", "10^-5", "10^-4", "10^-3", "10^-2"})

    def test_nvmts001n06cl_e_notation(self):
        axis = self.ladder(LIB / "NVMTS001N06CLTXG.pdf", 3, (305.0, 495.0, 600.0, 660.0))
        self.assertEqual(axis.model, "log10")
        values = sorted(t.value for t in axis.ticks)
        self.assertEqual(len(values), 8)
        for got, exponent in zip(values, range(-10, -2), strict=True):
            self.assertAlmostEqual(got, 10.0**exponent, delta=10.0**exponent * 1e-9)


@unittest.skipUnless((SET1 / "page_0104.pdf").is_file(), "synthetic set1 not generated")
class SyntheticPageLadderTests(unittest.TestCase):
    """set1 page_0104 carries a 10⁻²..10⁴ Unicode ladder (Fig 14) and a
    100µ..100 SI ladder in µA (Fig 12); both must read exactly."""

    def ladder(self, diagram: int):
        import cv2
        from datasheet_chart_digitizer.find_charts import process_pdf
        import tempfile, pymupdf
        from dataclasses import asdict

        with tempfile.TemporaryDirectory() as tmp:
            panel = next(p for p in process_pdf(SET1 / "page_0104.pdf", Path(tmp), 220)
                         if p.diagram == diagram)
            image = cv2.imread(str(Path(tmp) / panel.crop_png), cv2.IMREAD_GRAYSCALE)
        transform = CropTransform.for_chart(asdict(panel), image.shape)
        with pymupdf.open(panel.pdf) as doc:
            labels = _page_labels(doc[0], transform)
        height = image.shape[0]
        current = [l for l in labels if 0 <= l.cy <= height and 0 <= l.x1 <= image.shape[1]
                   and parse_tick_text(l.text, "current_a") is not None]
        axis, _ = _fit_ladder(current, align_attr="x1", position_attr="cy", align_tolerance=4.0,
                              min_span=0.35 * height, name="Y axis", quantity="current_a")
        return panel, axis

    def test_unicode_ladder(self):
        _, axis = self.ladder(14)
        self.assertEqual(axis.model, "log10")
        self.assertEqual(sorted(t.value for t in axis.ticks), [1e-2, 1e-1, 1.0, 10.0, 1e2, 1e3, 1e4])

    def test_si_ladder_in_micro_amps(self):
        panel, axis = self.ladder(12)
        values = sorted(t.value for t in axis.ticks)
        for got, want in zip(values, [1e-4, 1e-3, 1e-2, 1e-1, 1.0, 10.0, 100.0], strict=True):
            self.assertAlmostEqual(got, want, delta=want * 1e-9)
        self.assertEqual(_current_unit_scale(panel, axis), (1e-6, "uA"))


if __name__ == "__main__":
    unittest.main()
