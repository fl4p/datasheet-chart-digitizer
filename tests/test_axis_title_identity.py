"""A panel whose own axis titles name another chart is refused (body diode).

Found by the 2026-09-30 astra-review-50 pass: IXFB100N50P p4 served Fig. 7
Input Admittance (VGS/ID transfer curves) as the body-diode chart, status ok.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from datasheet_chart_digitizer import diode_forward_voltage as dfv
from datasheet_chart_digitizer.axis_title_identity import body_diode_title_contradiction
from datasheet_chart_digitizer.find_charts import process_pdf

ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"


class ContradictionRuleTests(unittest.TestCase):
    def test_transfer_titles_contradict(self) -> None:
        self.assertIsNotNone(body_diode_title_contradiction("VGS - Volts", "ID - Amperes"))

    def test_body_diode_titles_do_not(self) -> None:
        self.assertIsNone(body_diode_title_contradiction("VSD, Source-Drain Voltage (V)", "IS, Reverse Drain Current (A)"))
        self.assertIsNone(body_diode_title_contradiction("VSD - Volts", "IS - Amperes"))

    def test_one_contradicting_band_is_not_enough(self) -> None:
        self.assertIsNone(body_diode_title_contradiction("VGS - Volts", "IS - Amperes"))

    def test_unevaluated_is_not_a_refusal(self) -> None:
        self.assertIsNone(body_diode_title_contradiction("", ""))


class RealPdfTests(unittest.TestCase):
    def _run(self, rel: str):
        pdf = ROOT / rel
        if not pdf.exists():
            self.skipTest(f"{rel} regression PDF is not configured")
        with tempfile.TemporaryDirectory() as tmp:
            return dfv.digitize_panels_fail_closed(process_pdf(pdf, Path(tmp), 220), Path(tmp))

    def test_ixfb100n50p_input_admittance_is_not_served_as_body_diode(self) -> None:
        results, errors = self._run("littelfuse/IXFB100N50P.pdf")
        self.assertFalse([r for r in results if r["panel"]["page"] == 4 and r["status"] == "ok"])
        self.assertTrue(any("axis titles name another chart" in e["error"] for e in errors))

    def test_dmt8008lps_body_diode_still_ok(self) -> None:
        results, _ = self._run("diodes/DMT8008LPS-13.pdf")
        self.assertTrue(any(r["status"] == "ok" for r in results))


if __name__ == "__main__":
    unittest.main()
