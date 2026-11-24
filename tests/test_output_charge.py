"""Qoss(VDS) diagram plugin and its use in export-coss-dslib (issue #17).

Every gate is driven with a KNOWN-BAD input and must be seen to fire.  The pass case
is a synthetic Qoss(V) = K*(sqrt(1+V) - 1) curve (finite Coss(0), like a real die)
whose derivative is known in closed form.
"""

import math
import os
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from datasheet_chart_digitizer.coss_dslib import export_row
from datasheet_chart_digitizer.output_charge import (
    charge_unit_scale_pc,
    evaluate_trace,
    integrate_knots,
    is_output_charge_chart,
)

from test_coss_dslib import _good_row, _write_points_csv

K = 15_000.0  # pC / sqrt(V): Qoss(50 V) = 92 nC, an M8-like magnitude


def _q(v):
    return K * (np.sqrt(1.0 + v) - 1.0)


REF = {"qoss_pc": float(_q(50.0)), "vint_v": 50.0}


def _sqrt_trace(n: int = 500, vmax: float = 100.0):
    v = np.linspace(0.0, vmax, n)
    return v, _q(v)


class SelectionAndUnitTests(unittest.TestCase):
    def test_selects_output_charge_but_not_gate_or_recovery_charge(self):
        self.assertTrue(is_output_charge_chart({"kind": "chart", "title": "Typ. output charge"}))
        self.assertTrue(is_output_charge_chart({"kind": "chart", "title": "Typ. Qoss output charge"}))
        self.assertFalse(is_output_charge_chart({"kind": "gate_charge", "title": "Typ. gate charge"}))
        self.assertFalse(is_output_charge_chart({"kind": "chart", "title": "Reverse recovery charge"}))
        self.assertFalse(is_output_charge_chart({"kind": "capacitances", "title": "Typ. capacitances"}))

    def test_unit_needs_exactly_one_bracketed_charge_unit(self):
        self.assertEqual(charge_unit_scale_pc("125 100 [nC] oss Q 0 20 V [V]"), ("nC", 1e3))
        self.assertEqual(charge_unit_scale_pc("Qoss [µC]"), ("uC", 1e6))
        self.assertIsNone(charge_unit_scale_pc("Qoss 0 20 40"))
        self.assertIsNone(charge_unit_scale_pc("Qoss [nC] and [pC]"))


class EvaluateTraceTests(unittest.TestCase):
    def test_sqrt_curve_passes_and_coss_eff_matches_closed_form(self):
        v, q = _sqrt_trace()
        r = evaluate_trace(v, q, REF)
        self.assertEqual(r["status"], "pass", r["reasons"])
        self.assertAlmostEqual(r["anchor"]["rel_error"], 0.0, places=3)
        knots = dict((round(kv, 3), c) for kv, c in r["coss_eff_curve"])
        # dQ/dV = K / (2 sqrt(1+V))
        # knots whose window is cut by V=0 keep some curvature bias; interior knots do not
        for vk, tol in ((0.0, 0.08), (1.0, 0.04), (10.0, 0.02), (50.0, 0.02), (90.0, 0.02)):
            self.assertAlmostEqual(knots[vk] / (K / (2 * math.sqrt(1.0 + vk))), 1.0, delta=tol)
        self.assertLessEqual(r["coss_eff_integral_max_error"], 0.03)
        self.assertIn(50.0, knots)  # the table voltage is an explicit knot

    def test_integrate_knots_is_exact_for_linear_capacitance(self):
        knots = [(0.0, 100.0), (10.0, 50.0), (20.0, 50.0)]
        got = integrate_knots(knots, np.array([5.0, 10.0, 20.0]))
        np.testing.assert_allclose(got, [437.5, 750.0, 1250.0])

    def test_known_bad_table_mismatch_rejects(self):
        v, q = _sqrt_trace()
        r = evaluate_trace(v, q * 1.10, REF)
        self.assertEqual(r["status"], "rejected")
        self.assertTrue(any(s.startswith("qoss_diagram_anchor_mismatch") for s in r["reasons"]))

    def test_known_bad_no_table_reference_never_passes(self):
        v, q = _sqrt_trace()
        for ref in (None, {}, {"qoss_pc": REF["qoss_pc"], "vint_v": None},
                    {"qoss_pc": None, "vint_v": 50.0}):
            r = evaluate_trace(v, q, ref)
            self.assertEqual(r["status"], "rejected", ref)
            self.assertIn("qoss_table_reference_unavailable", r["reasons"])

    def test_known_bad_reference_voltage_outside_trace(self):
        v, q = _sqrt_trace(vmax=40.0)
        r = evaluate_trace(v, q, REF)
        self.assertIn("qoss_reference_vds_outside_trace:50 V", r["reasons"])

    def test_known_bad_trace_not_from_origin(self):
        v, q = _sqrt_trace()
        keep = v >= 10.0
        r = evaluate_trace(v[keep], q[keep], REF)
        self.assertTrue(any(s.startswith("qoss_trace_not_from_origin") for s in r["reasons"]))

    def test_known_bad_non_monotone_trace(self):
        v, q = _sqrt_trace()
        q = q.copy()
        q[300:320] -= 0.05 * q.max()  # a 5% dip: wrong-branch jump or a label hit
        r = evaluate_trace(v, q, REF)
        self.assertTrue(any(s.startswith("qoss_not_monotone") for s in r["reasons"]))

    def test_known_bad_flat_tail_is_unresolved_not_zero(self):
        v = np.linspace(0.0, 300.0, 600)
        q = np.minimum(_q(v), float(_q(40.0)))  # flat above 40 V
        r = evaluate_trace(v, q, {"qoss_pc": float(_q(40.0)), "vint_v": 300.0})
        self.assertEqual(r["status"], "rejected")
        self.assertTrue(any(s.startswith("coss_eff_unresolved_by_diagram") for s in r["reasons"]))

    def test_far_tail_mismatch_stays_rejected(self):
        v, q = _sqrt_trace()
        for scale in (1.06, 1.5, 3.0, 0.3):
            r = evaluate_trace(v, q * scale, REF)
            self.assertEqual(r["status"], "rejected", scale)

    def test_short_or_nonfinite_trace_rejected(self):
        self.assertEqual(evaluate_trace(np.arange(5.0), np.arange(5.0), REF)["status"], "rejected")
        v, q = _sqrt_trace()
        q = q.copy()
        q[10] = np.nan
        self.assertEqual(evaluate_trace(v, q, REF)["status"], "rejected")


def _contradiction_row() -> dict:
    """A C(V) row whose chart integrates to 0.386x the table: the M8 situation."""
    row = _good_row()
    row.update({
        "qoss_validation_status": "graph_table_inconsistent",
        "qoss_validation_error": "Qoss: extracted 3.59e+04, datasheet 9.3e+04, rel 61.4%",
        "qoss_metrics": None,
        "qoss_diagnostic_metrics": {"Qoss_pc": 35_900.0},
        "output_charge_reference": {"qoss_pc": 93_000.0, "vint_v": 50.0},
    })
    return row


def _diagram(**over) -> dict:
    d = {
        "part": "TESTFET", "pdf": "/nonexistent/TESTFET.pdf", "diagram": 12, "page": 9,
        "status": "pass", "reasons": [],
        "anchor": {"vds_v": 50.0, "table_pc": 93_000.0, "diagram_pc": 93_100.0, "rel_error": 0.001},
        "coss_eff_curve": [[0.0, 3700.0], [50.0, 1076.0], [100.0, 382.0]],
        "coss_eff_integral_max_error": 0.008,
    }
    d.update(over)
    return d


class ExportGateTests(unittest.TestCase):
    def _export(self, row, panels):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_points_csv(base / row["points"])
            return export_row(row, base, output_charge=panels)

    def test_matching_diagram_explains_contradiction_and_serves_both_curves(self):
        res = self._export(_contradiction_row(), [_diagram()])
        self.assertEqual(res.coss_status, "pass", res.coss_reasons)
        self.assertEqual(res.qoss_check["status"], "source_contradiction")
        self.assertAlmostEqual(res.qoss_check["coss_chart_integral_over_table"], 0.386, places=3)
        self.assertEqual(res.coss_basis, "small_signal_chart")
        self.assertEqual(res.coss_eff_status, "pass")
        self.assertEqual(res.coss_eff_curve[1], (50.0, 1076.0))

    def test_known_bad_without_diagram_stays_rejected(self):
        res = self._export(_contradiction_row(), [])
        self.assertEqual(res.coss_status, "rejected")
        self.assertTrue(any(r.startswith("qoss_validation:graph_table_inconsistent")
                            for r in res.coss_reasons))
        self.assertEqual(res.coss_eff_status, "absent")

    def test_known_bad_non_passing_diagram_explains_nothing(self):
        for status in ("rejected", "unverified"):
            res = self._export(_contradiction_row(), [_diagram(status=status, reasons=["x"])])
            self.assertEqual(res.coss_status, "rejected", status)
            self.assertEqual(res.coss_eff_status, status)
            self.assertEqual(res.coss_eff_curve, [])

    def test_known_bad_diagram_anchored_to_another_reference(self):
        for anchor in ({"vds_v": 50.0, "table_pc": 114_000.0, "diagram_pc": 114_000.0},
                       {"vds_v": 80.0, "table_pc": 93_000.0, "diagram_pc": 93_000.0},
                       {}):
            res = self._export(_contradiction_row(), [_diagram(anchor=anchor)])
            self.assertEqual(res.coss_status, "rejected", anchor)

    def test_known_bad_other_part_or_ambiguous_diagram(self):
        res = self._export(_contradiction_row(), [_diagram(part="OTHERFET")])
        self.assertEqual(res.coss_status, "rejected")
        self.assertEqual(res.coss_eff_status, "absent")
        res = self._export(_contradiction_row(), [_diagram(pdf="/nonexistent/OTHER.pdf")])
        self.assertEqual(res.coss_status, "rejected")
        res = self._export(_contradiction_row(), [_diagram(), _diagram(diagram=13)])
        self.assertEqual(res.coss_status, "rejected")

    def test_known_bad_other_qoss_failures_are_not_explained(self):
        for status in ("unreliable_extrapolation", "chart_clipped_table_authoritative", None):
            row = _contradiction_row()
            row["qoss_validation_status"] = status
            res = self._export(row, [_diagram()])
            self.assertEqual(res.coss_status, "rejected", status)

    def test_known_bad_missing_chart_integral(self):
        row = _contradiction_row()
        row["qoss_diagnostic_metrics"] = None
        res = self._export(row, [_diagram()])
        self.assertEqual(res.coss_status, "rejected")

    def test_exemption_does_not_bypass_coss_anchor_gate(self):
        row = _contradiction_row()
        row["anchors"]["Coss"]["value_pf"] *= 1.5
        res = self._export(row, [_diagram()])
        self.assertEqual(res.coss_status, "rejected")
        self.assertTrue(any(r.startswith("coss_anchor_mismatch") for r in res.coss_reasons))
        self.assertEqual(res.coss_eff_status, "pass")  # independent evidence

    def test_crop_directory_matches_when_rows_carry_no_pdf(self):
        row = _contradiction_row()
        del row["pdf"]
        row["crop"] = "/out/crops/TESTFET/p09_diagram_11.png"
        d = _diagram(crop="/out/crops/TESTFET/p09_diagram_12.png")
        self.assertEqual(self._export(row, [d]).coss_status, "pass")
        d = _diagram(crop="/elsewhere/crops/TESTFET/p09_diagram_12.png")
        self.assertEqual(self._export(row, [d]).coss_status, "rejected")


ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"


class OptiMOS8CorpusTests(unittest.TestCase):
    """End to end on the issue #17 part; skipped without the datasheet corpus."""

    def test_isc040n10nm8_diagram_matches_table_and_explains_coss(self):
        pdf = ROOT / "infineon" / "ISC040N10NM8.pdf"
        if not pdf.exists():
            self.skipTest("ISC040N10NM8 regression PDF is not configured")
        import json
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)

            def run(*args):
                subprocess.run([sys.executable, "-m", "datasheet_chart_digitizer.cli", *args],
                               check=True, capture_output=True, text=True)

            run("find", str(pdf), "--out", str(out), "--dpi", "180")
            run("digitize-capacitance", str(out / "charts.json"), "--out", str(out))
            run("digitize-output-charge", str(out / "charts.json"), "--out", str(out))
            run("export-coss-dslib", str(out), "--out", str(out / "dslib"))
            panel = json.loads((out / "output_charge_digitization.json").read_text())["panels"][0]
            self.assertEqual(panel["status"], "pass", panel["reasons"])
            self.assertEqual(panel["method"], "vector")
            self.assertAlmostEqual(panel["anchor"]["diagram_pc"] / 93_000.0, 1.0, delta=0.01)
            res = json.loads((out / "dslib" / "dslib_coss_manifest.json").read_text())[0]
            self.assertEqual(res["coss_status"], "pass")
            self.assertEqual(res["coss_eff_status"], "pass")
            self.assertEqual(res["qoss_check"]["status"], "source_contradiction")
            self.assertAlmostEqual(res["qoss_check"]["coss_chart_integral_over_table"], 0.39,
                                   delta=0.02)


if __name__ == "__main__":
    unittest.main()
