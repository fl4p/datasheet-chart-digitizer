"""Spreadsheet E-notation decade labels ("1E+4", "1.0E+02") on C(V) axes.

Found by the 2026-09-30 astra-review-50 pass on HYG292N60NP1D p5 (y labels
"1E+0 .. 1E+4"); with the Office-palette stroke fix the chart is served from
its vector paths.
"""

from __future__ import annotations

import csv
import os
import tempfile
import unittest
from pathlib import Path

from datasheet_chart_digitizer.axis_calibration import _exact_number_token, _number_tokens

ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"


class ScientificLabelTests(unittest.TestCase):
    def test_e_notation_decades(self) -> None:
        self.assertEqual(_exact_number_token("1E+4"), 1e4)
        self.assertEqual(_exact_number_token("1.0E+02"), 100.0)
        self.assertAlmostEqual(_exact_number_token("1E-01"), 0.1)
        self.assertEqual(_exact_number_token("1E+0"), 1.0)

    def test_existing_forms_unchanged(self) -> None:
        self.assertEqual(_exact_number_token("10²"), 100.0)
        self.assertEqual(_exact_number_token("1K"), 1000.0)
        self.assertEqual(_number_tokens("1MHz"), [])

    def test_mixed_superscript_and_e_is_not_a_number(self) -> None:
        value = _exact_number_token("1K" + "E+3")
        self.assertTrue(value is None or value != value)


class RealPdfTests(unittest.TestCase):
    def test_hyg292n60np1d_excel_chart_is_served_from_its_vector_paths(self) -> None:
        pdf = ROOT / "huayi/HYG292N60NP1D.pdf"
        if not pdf.exists():
            self.skipTest("HYG292N60NP1D regression PDF is not configured")
        from datasheet_chart_digitizer import mosfet_capacitance as mc
        from datasheet_chart_digitizer.find_charts import asdict, process_pdf

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            panels = [p for p in process_pdf(pdf, out, 220) if p.kind == "capacitances"]
            self.assertEqual(len(panels), 1)
            panel = panels[0]
            result = mc.process_chart(asdict(panel), out / panel.crop_png, out,
                                      Path(panel.crop_png).with_suffix(""), pdf.parent)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["extraction_method"], "vector")
            cal = result["axis_calibration"]
            self.assertTrue(cal["y_log"])
            self.assertEqual(cal["y_grid_check"]["status"], "verified")
            rows = list(csv.DictReader(open(out / result["points"])))
        at = {}
        for row in rows:
            v = float(row["vds_V"])
            if abs(v - 300.0) < 1.5:
                at.setdefault(row["trace"], float(row["cap_pF"]))
        # printed legend: Ciss blue ~500 pF flat, Coss red ~12.6 pF, Crss green ~2.1 pF at 300 V
        self.assertAlmostEqual(at["Ciss"], 501.0, delta=15.0)
        self.assertAlmostEqual(at["Coss"], 12.6, delta=0.8)
        self.assertAlmostEqual(at["Crss"], 2.08, delta=0.2)


if __name__ == "__main__":
    unittest.main()
