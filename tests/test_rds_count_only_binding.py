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

import numpy as np

from datasheet_chart_digitizer import rds_source_labels, rdson_current, rdson_temperature

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

    def _rds_id_panel(self, rel: str):
        pdf = ROOT / rel
        if not pdf.exists():
            self.skipTest(f"{rel} regression PDF is not configured")
        with tempfile.TemporaryDirectory() as tmp:
            panel = next(
                p for p in rdson_current.process_pdf(pdf, Path(tmp), 220)
                if rdson_current._is_rdson_current_panel(p)
            )
            rows = rds_source_labels.vgs_label_rows(panel)
        return panel, rows

    def test_glued_subscript_labels_both_parse_once(self) -> None:
        # TSM60NE200CIT / 180CIT p3 print "V" + subscript "GS" + one word "=10V".
        # Only one of the two rows parsed (via joined line text), so one label met
        # one trace and 200CIT served its VGS=10V curve as 12 V (status ok).
        for rel in ("ts/TSM60NE200CIT.pdf", "ts/TSM60NE180CIT.pdf"):
            _panel, rows = self._rds_id_panel(rel)
            self.assertEqual(sorted(row[0] for row in rows), [10.0, 12.0], rel)

    def test_tsm60ne200cit_binds_each_vgs_to_its_printed_curve(self) -> None:
        results = self._run("ts/TSM60NE200CIT.pdf", rdson_current.digitize_pdf_fail_closed)
        panel = [r for r in results if r["panel"]["page"] == 3]
        self.assertEqual(len(panel), 1)
        self.assertEqual(panel[0]["status"], "ok")
        at = {}
        for curve in panel[0]["curves"]:
            points = sorted(curve["points"])
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            at[curve["gate_voltage_v"]] = float(np.interp(12.5, xs, ys))
        # printed: VGS=10V curve ~184 mOhm at 12.5 A, VGS=12V curve ~171 mOhm
        self.assertAlmostEqual(at[10.0], 184.0, delta=3.0)
        self.assertAlmostEqual(at[12.0], 171.0, delta=3.0)

    def test_count_only_binding_refused_when_text_names_another_vgs(self) -> None:
        panel, _rows = self._rds_id_panel("ts/TSM60NE200CIT.pdf")
        refusal = rds_source_labels.count_only_binding_refusal(panel, 12.0)
        self.assertIsNotNone(refusal)
        self.assertIn("[10.0]", refusal)


if __name__ == "__main__":
    unittest.main()
