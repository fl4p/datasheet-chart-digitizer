"""MLCC capacitance charts are not MOSFET capacitance panels (bench round 1, bug 5).

Every Taiyo Yuden MLCC "Capacitance Freq." / "Capacitance Temp." chart was
filed ``capacitances`` and sent to the Ciss/Coss/Crss digitizer (39 refusals,
3 ``unverified`` over 20 PDFs on main 6a1a61b).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from datasheet_chart_digitizer.chart_classifier import classify_chart, title_owns_chart_kind

VENDOR = Path("/Users/fab/dev/pv/ee/datasheet-chart-digitizer/out/vlm-chart2table/vendor_src")


class MlccClassifierTests(unittest.TestCase):
    MLCC = [
        ("Capacitance Freq.", "Capacitance Freq. 1000 100 Capacitance[uF] 10 1 0.0001 0.001 0.01"),
        ("Capacitance Temp.", "Capacitance Temp. 10 0 dC/C[%] -10 -20 -30 -55 -35 -15 5 25"),
        ("Capacitance - DC Bias", "Capacitance Change [%] DC Bias [V]"),
        ("Capacitance vs. Frequency", ""),
    ]
    MOSFET = [
        ("Typ. capacitances", "C=f( V V f ); =0 V; =1 MHz DS GS Ciss Coss Crss"),
        ("Junction Capacitances", "C (pF) ISS Capacitance C OSS C RSS (f = 1MHz) Drain Voltage, V (V) DS"),
        ("Capacitance Characteristics", "VDS, Drain-to-Source Voltage (V) C, Capacitance (pF) Ciss Coss Crss"),
        ("Capacitance vs. Frequency", "Ciss Coss f, Frequency (MHz)"),
        ("Typical Capacitance vs. Temperature", "C (pF) Ciss"),
        ("Junction Capacitance vs Reverse Voltage", "VR (V) CT (pF)"),
    ]

    def test_mlcc_charts_are_not_mosfet_capacitance(self) -> None:
        for title, text in self.MLCC:
            with self.subTest(title=title):
                self.assertEqual(classify_chart(title, text), "mlcc_capacitance")
                self.assertEqual(classify_chart(title, ""), "mlcc_capacitance")
                self.assertNotEqual(title_owns_chart_kind(title, 1), "capacitances")

    def test_semiconductor_capacitance_charts_keep_their_kind(self) -> None:
        for title, text in self.MOSFET:
            with self.subTest(title=title):
                self.assertEqual(classify_chart(title, text), "capacitances")

    def test_taiyo_yuden_and_murata_pdfs_yield_no_capacitance_panel(self) -> None:
        from datasheet_chart_digitizer.find_charts import process_pdf

        pdfs = [
            VENDOR / "taiyo_yuden" / "MAASJ32MSB7476MPNA01.pdf",
            VENDOR / "taiyo_yuden" / "MBARQ105SCG1R6BFRA18.pdf",
            VENDOR / "taiyo_yuden" / "MCASU063SCH9R4DFNA01.pdf",
            VENDOR / "murata" / "GRM188R71H104KA93.pdf",
        ]
        missing = [pdf for pdf in pdfs if not pdf.exists()]
        if missing:
            self.skipTest(f"missing local corpus fixture: {missing[0]}")
        for pdf in pdfs:
            with self.subTest(pdf=pdf.name), tempfile.TemporaryDirectory() as tmp:
                kinds = [panel.kind for panel in process_pdf(pdf, Path(tmp), 120)]
                self.assertNotIn("capacitances", kinds)


if __name__ == "__main__":
    unittest.main()
