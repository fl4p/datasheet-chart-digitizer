"""Breakdown-axis OCR for vector PDFs whose glyphs are outline paths."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from datasheet_chart_digitizer import breakdown_voltage as bv
from datasheet_chart_digitizer import find_charts


ST = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st")
INFINEON = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon")


def _chart_and_crop(pdf: Path, kind: str, *, dpi: int = 180):
    out = Path(tempfile.mkdtemp(prefix=f"{pdf.stem.lower()}-outline-axis-"))
    panels = find_charts.process_pdf(pdf, out, dpi=dpi)
    chart = next(find_charts.asdict(panel) for panel in panels if panel.kind == kind)
    return chart, out / chart["crop_png"], out


class OcrTickSubsetUnit(unittest.TestCase):
    def test_discards_only_two_misreads_from_unique_linear_source_run(self):
        grid = [6.0 + 38.4 * index for index in range(13)]
        candidates = [
            (5.0, 14.0),       # source -75 was OCRed without both sign/digit
            (-25.0, 82.9),
            (25.0, 159.8),
            (15.0, 236.8),     # source 75 was OCRed as 15
            (125.0, 313.5),
            (175.0, 390.4),
        ]

        ticks = bv._trustworthy_ocr_axis_ticks(candidates, grid, "X OCR")

        self.assertEqual([value for value, _pixel in ticks], [-25, 25, 125, 175])
        axis = bv._fit_axis(ticks, "X")
        self.assertAlmostEqual(axis.value(159.8), 25.0, delta=0.1)

    def test_multiple_full_span_models_refuse_as_ambiguous(self):
        candidates = []
        for pixel, value in zip((0.0, 10.0, 20.0, 30.0), (0, 10, 20, 30)):
            candidates.extend(((float(value), pixel), (float(value + 100), pixel)))

        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            bv._trustworthy_ocr_axis_ticks(
                candidates, [0.0, 10.0, 20.0, 30.0], "ambiguous OCR"
            )

    def test_fewer_than_four_source_labels_remains_refused(self):
        with self.assertRaisesRegex(RuntimeError, "no trustworthy"):
            bv._trustworthy_ocr_axis_ticks(
                [(0.0, 0.0), (10.0, 10.0), (20.0, 20.0)],
                [0.0, 10.0, 20.0, 30.0],
                "short OCR",
            )


@unittest.skipUnless((ST / "STL52DN4LF7AG.pdf").exists(), "local ST fixture unavailable")
class VectorOutlineBreakdownEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chart, cls.crop, cls.out = _chart_and_crop(
            ST / "STL52DN4LF7AG.pdf", "breakdown_voltage"
        )
        cls.result = bv.process_chart(
            cls.chart,
            cls.crop,
            cls.out / "digitized",
            Path(cls.chart["crop_png"]).with_suffix(""),
        )

    def test_empty_native_text_uses_bounded_axis_ocr(self):
        self.assertEqual(self.chart["text"], "")
        self.assertEqual(
            self.result["axis_text_source"], "ocr_vector_outlined_glyphs"
        )
        self.assertEqual(self.result["calibration"]["x_ticks"], 4)
        self.assertEqual(self.result["calibration"]["y_ticks"], 5)
        self.assertLessEqual(self.result["calibration"]["x_exact_center_max_px"], 1)
        self.assertLessEqual(self.result["calibration"]["y_exact_center_max_px"], 1)

    def test_curve_and_absolute_value_are_source_anchored(self):
        self.assertEqual(self.result["status"], "verified")
        self.assertEqual(self.result["value_basis"], "normalized_to_spec_min")
        self.assertEqual(self.result["source_trace_points"], self.result["n_points"])
        self.assertGreater(self.result["n_points"], 250)
        self.assertLess(self.result["tj_range_c"][0], -50)
        self.assertGreater(self.result["tj_range_c"][1], 170)
        self.assertAlmostEqual(self.result["v_at_25c"], 40.0, delta=0.15)
        self.assertEqual(self.result["warnings"], [])

    def test_overlay_and_csv_exist(self):
        self.assertTrue(Path(self.result["overlay"]).is_file())
        self.assertTrue(Path(self.result["csv"]).is_file())
        self.assertGreater(len(Path(self.result["csv"]).read_text().splitlines()), 250)


@unittest.skipUnless(
    (INFINEON / "BSZ018N04LS6.pdf").exists(), "local Infineon fixture unavailable"
)
class NativeVectorTextControl(unittest.TestCase):
    def test_native_axis_never_calls_outline_ocr_fallback(self):
        chart, crop, out = _chart_and_crop(
            INFINEON / "BSZ018N04LS6.pdf", "breakdown_voltage"
        )
        with patch.object(
            bv, "ocr_words_in_rect", side_effect=AssertionError("unexpected OCR")
        ):
            result = bv.process_chart(
                chart, crop, out / "digitized", Path(chart["crop_png"]).with_suffix("")
            )

        self.assertEqual(result["status"], "verified")
        self.assertNotIn("axis_text_source", result)
        self.assertAlmostEqual(result["v_at_25c"], 40.0, delta=0.2)


if __name__ == "__main__":
    unittest.main()
