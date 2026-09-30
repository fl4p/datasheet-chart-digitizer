"""One VGS row + one surviving trace must not bind by count when the panel labels other curves.

Found by the 2026-09-30 astra-review-50 pass: PSMN0R7-25YLD p7 (RDS(on) vs ID)
served the 3 V curve as VGS = 10 V with status ok (1.6-5.3 mOhm where the
printed 10 V curve is ~0.75 mOhm); p8 (normalized RDS(on) vs Tj) likewise
bound its one trace to "VGS = 4.5 V" while a second curve is labelled 10 V.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from datasheet_chart_digitizer import rdson_current, rdson_temperature

ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"


class CountOnlyBindingTests(unittest.TestCase):
    def _run(self, rel: str, runner):
        pdf = ROOT / rel
        if not pdf.exists():
            self.skipTest(f"{rel} regression PDF is not configured")
        with tempfile.TemporaryDirectory() as tmp:
            results, _errors = runner(pdf, Path(tmp), 220)
        return results

    def test_psmn0r7_rds_id_single_trace_is_not_bound_to_vgs_10(self) -> None:
        results = self._run("nxp/PSMN0R7-25YLD.pdf", rdson_current.digitize_pdf_fail_closed)
        panel = [r for r in results if r["panel"]["page"] == 7 and r["panel"]["diagram"] == 8]
        self.assertEqual(len(panel), 1)
        self.assertEqual(panel[0]["status"], "refused")
        self.assertIn("count-only binding refused", panel[0]["binding_error"])
        self.assertEqual(panel[0]["curves"], [])

    def test_psmn0r7_rds_tj_second_labelled_curve_blocks_count_binding(self) -> None:
        results = self._run("nxp/PSMN0R7-25YLD.pdf", rdson_temperature.digitize_pdf_fail_closed)
        panel = [r for r in results if r["panel"]["page"] == 8]
        self.assertTrue(panel)
        self.assertTrue(all(r["status"] != "ok" for r in panel))

    def test_tick_label_on_the_condition_row_is_not_a_curve_label(self) -> None:
        # TSM60NE084CIT p3: the y tick "0.75" and "V GS =10V" share a text line;
        # a joined-text match read "0.75 V" and refused a correct single curve.
        results = self._run("ts/TSM60NE084CIT.pdf", rdson_temperature.digitize_pdf_fail_closed)
        self.assertTrue(any(r["status"] == "ok" for r in results))

    def test_single_labelled_curve_still_binds(self) -> None:
        results = self._run("ti/CSD18543Q3A.pdf", rdson_temperature.digitize_pdf_fail_closed)
        self.assertTrue(any(r["status"] == "ok" for r in results))


if __name__ == "__main__":
    unittest.main()
