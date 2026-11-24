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
    file_sha256,
    producer_sha256,
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

    def test_known_bad_nonfinite_reference(self):
        v, q = _sqrt_trace()
        for ref in ({"qoss_pc": float("nan"), "vint_v": 50.0},
                    {"qoss_pc": float("inf"), "vint_v": 50.0},
                    {"qoss_pc": REF["qoss_pc"], "vint_v": float("nan")},
                    {"qoss_pc": -REF["qoss_pc"], "vint_v": 50.0}):
            self.assertEqual(evaluate_trace(v, q, ref)["status"], "rejected", ref)

    def test_known_bad_missing_initial_charge_is_caught_absolutely(self):
        # Q = 1000 V pC sampled from 2 V: the knots must carry the charge below 2 V
        # (zero-hold from 0 V), and a table at 2.5 V must be met by the knots from 0 V.
        v = np.linspace(2.0, 100.0, 400)
        r = evaluate_trace(v, 1000.0 * v, {"qoss_pc": 2500.0, "vint_v": 2.5})
        self.assertEqual(r["status"], "pass", r["reasons"])  # consistent: C = 1000 pF from 0 V
        self.assertEqual(r["coss_eff_curve"][0][0], 0.0)
        self.assertAlmostEqual(r["anchor"]["coss_eff_integral_pc"], 2500.0, delta=25.0)
        # the same slope with the first 2000 pC missing: zero-hold cannot invent it
        r = evaluate_trace(v, 1000.0 * (v - 2.0), {"qoss_pc": 500.0, "vint_v": 2.5})
        self.assertEqual(r["status"], "rejected")
        self.assertTrue(any(s.startswith("coss_eff_anchor_mismatch") for s in r["reasons"]))
        # offset origin, either sign
        v = np.linspace(0.0, 100.0, 400)
        for q0 in (-40_000.0, -5_000.0, 5_000.0):
            r = evaluate_trace(v, 1000.0 * v + q0, {"qoss_pc": 50_000.0 + q0, "vint_v": 50.0})
            self.assertEqual(r["status"], "rejected", q0)
            self.assertTrue(any(s.startswith("qoss_trace_not_from_origin") for s in r["reasons"]))

    def test_knots_integrate_absolutely_from_zero(self):
        v, q = _sqrt_trace()
        r = evaluate_trace(v, q, REF)
        kv = np.array([k[0] for k in r["coss_eff_curve"]])
        got = integrate_knots(r["coss_eff_curve"], kv)
        np.testing.assert_allclose(got, _q(kv), atol=0.03 * q.max())
        self.assertAlmostEqual(r["anchor"]["coss_eff_rel_error"], 0.0, delta=0.02)

    def test_pixel_quantized_trace_withholds_or_resolves_within_tolerance(self):
        # constant 1000 pF over 0-100 V drawn on a 400 px tall, 500 px wide panel
        q_px, v_px = 100_000.0 / 400, 100.0 / 500
        v = np.round(np.linspace(0.0, 100.0, 500) / v_px) * v_px
        q = np.round(1000.0 * v / q_px) * q_px
        r = evaluate_trace(v, q, {"qoss_pc": 50_000.0, "vint_v": 50.0},
                           q_quantum_pc=q_px, v_quantum_v=v_px)
        if r["status"] == "pass":
            for _, c in r["coss_eff_curve"]:
                self.assertAlmostEqual(c / 1000.0, 1.0, delta=0.10)
        else:
            self.assertTrue(any(s.startswith("coss_eff_unresolved") for s in r["reasons"]))
        # the same curve with a 10x coarser vertical quantum cannot resolve the slope
        q10 = np.round(1000.0 * v / (10 * q_px)) * (10 * q_px)
        r = evaluate_trace(v, q10, {"qoss_pc": 50_000.0, "vint_v": 50.0},
                           q_quantum_pc=10 * q_px, v_quantum_v=v_px)
        self.assertEqual(r["status"], "rejected")
        self.assertTrue(any(s.startswith("coss_eff_unresolved") for s in r["reasons"]))

    def test_short_or_nonfinite_trace_rejected(self):
        self.assertEqual(evaluate_trace(np.arange(5.0), np.arange(5.0), REF)["status"], "rejected")
        v, q = _sqrt_trace()
        q = q.copy()
        q[10] = np.nan
        self.assertEqual(evaluate_trace(v, q, REF)["status"], "rejected")


def _contradiction_row(pdf: Path) -> dict:
    """A C(V) row whose chart integrates to 0.386x the table: the M8 situation."""
    row = _good_row()
    row.update({
        "pdf": str(pdf),
        "qoss_validation_status": "graph_table_inconsistent",
        "qoss_validation_error": "Qoss: extracted 3.59e+04, datasheet 9.3e+04, rel 61.4%",
        "qoss_metrics": None,
        "qoss_diagnostic_metrics": {"Qoss_pc": 35_900.0},
        "output_charge_reference": {"qoss_pc": 93_000.0, "vint_v": 50.0},
    })
    return row


class ExportGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.pdf = self.base / "TESTFET.pdf"
        self.pdf.write_bytes(b"%PDF-1.7 test sheet")
        self.table = self.base / "TESTFET.pdf.nop.csv"
        self.table.write_text("Output charge,Qoss,93,nC\n")

    def tearDown(self):
        self._tmp.cleanup()

    def _row(self):
        return _contradiction_row(self.pdf)

    def _diagram(self, **over) -> dict:
        d = {
            "part": "TESTFET", "pdf": str(self.pdf), "diagram": 12, "page": 9,
            "status": "pass", "reasons": [],
            "anchor": {"vds_v": 50.0, "table_pc": 93_000.0, "diagram_pc": 93_100.0,
                       "rel_error": 0.001, "coss_eff_integral_pc": 93_300.0},
            "coss_eff_curve": [[0.0, 3700.0], [50.0, 1076.0], [100.0, 382.0]],
            "coss_eff_integral_max_error": 0.008,
            "provenance": {
                "pdf_sha256": file_sha256(self.pdf),
                "table_csv": str(self.table),
                "table_csv_sha256": file_sha256(self.table),
                "producer_sha256": producer_sha256(),
            },
        }
        d.update(over)
        return d

    def _export(self, row, panels):
        _write_points_csv(self.base / row["points"])
        return export_row(row, self.base, output_charge=panels)

    def test_matching_diagram_explains_contradiction_and_serves_both_curves(self):
        res = self._export(self._row(), [self._diagram()])
        self.assertEqual(res.coss_status, "pass", res.coss_reasons)
        self.assertEqual(res.qoss_check["status"], "source_contradiction")
        self.assertAlmostEqual(res.qoss_check["coss_chart_integral_over_table"], 0.386, places=3)
        self.assertEqual(res.coss_basis, "small_signal_chart")
        self.assertEqual(res.coss_eff_status, "pass")
        self.assertEqual(res.coss_eff_curve[1], (50.0, 1076.0))

    def test_known_bad_without_diagram_stays_rejected(self):
        res = self._export(self._row(), [])
        self.assertEqual(res.coss_status, "rejected")
        self.assertTrue(any(r.startswith("qoss_validation:graph_table_inconsistent")
                            for r in res.coss_reasons))
        self.assertEqual(res.coss_eff_status, "absent")

    def test_known_bad_non_passing_diagram_explains_nothing(self):
        for status in ("rejected", "unverified"):
            res = self._export(self._row(), [self._diagram(status=status, reasons=["x"])])
            self.assertEqual(res.coss_status, "rejected", status)
            self.assertEqual(res.coss_eff_status, status)
            self.assertEqual(res.coss_eff_curve, [])

    def test_known_bad_diagram_anchored_to_another_reference(self):
        good = self._diagram()["anchor"]
        for change in ({"table_pc": 114_000.0, "diagram_pc": 114_000.0,
                        "coss_eff_integral_pc": 114_000.0},
                       {"vds_v": 80.0}):
            res = self._export(self._row(), [self._diagram(anchor={**good, **change})])
            self.assertEqual(res.coss_status, "rejected", change)

    def test_known_bad_stored_status_pass_is_rechecked(self):
        good = self._diagram()["anchor"]
        for change in ({"diagram_pc": 10_000.0}, {"diagram_pc": None},
                       {"diagram_pc": float("nan")}, {"table_pc": float("nan")},
                       {"coss_eff_integral_pc": 60_000.0}, {"coss_eff_integral_pc": None},
                       {"vds_v": float("inf")}):
            res = self._export(self._row(), [self._diagram(anchor={**good, **change})])
            self.assertEqual(res.coss_status, "rejected", change)
            self.assertEqual(res.coss_eff_status, "rejected", change)
        for curve in ([], [[1.0, 3700.0], [50.0, 1076.0]], [[0.0, 3700.0], [50.0, -1.0]],
                      [[0.0, float("nan")], [50.0, 1076.0]]):
            res = self._export(self._row(), [self._diagram(coss_eff_curve=curve)])
            self.assertEqual(res.coss_eff_status, "rejected", curve)
            self.assertEqual(res.coss_status, "rejected", curve)

    def test_known_bad_nonfinite_row_reference_or_integral(self):
        for ref in ({"qoss_pc": float("nan"), "vint_v": 50.0}, {"qoss_pc": 93_000.0, "vint_v": None}):
            row = self._row()
            row["output_charge_reference"] = ref
            self.assertEqual(self._export(row, [self._diagram()]).coss_status, "rejected", ref)
        for metrics in (None, {"Qoss_pc": float("nan")}, {"Qoss_pc": float("inf")}, {"Qoss_pc": 0.0}):
            row = self._row()
            row["qoss_diagnostic_metrics"] = metrics
            self.assertEqual(self._export(row, [self._diagram()]).coss_status, "rejected", metrics)

    def test_known_bad_stale_or_foreign_provenance(self):
        d = self._diagram()
        d["provenance"] = {**d["provenance"], "producer_sha256": "0" * 64}
        res = self._export(self._row(), [d])
        self.assertEqual(res.coss_status, "rejected")
        self.assertTrue(any("different digitizer code" in r for r in res.coss_eff_reasons))
        # table edited after the diagram was digitized
        d = self._diagram()
        self.table.write_text("Output charge,Qoss,94,nC\n")
        res = self._export(self._row(), [d])
        self.assertEqual(res.coss_status, "rejected")
        self.assertTrue(any("table csv" in r for r in res.coss_eff_reasons))

    def test_known_bad_pdf_content_changed_or_other_part(self):
        d = self._diagram()
        self.pdf.write_bytes(b"%PDF-1.7 another revision, same file name")
        res = self._export(self._row(), [d])
        self.assertEqual(res.coss_status, "rejected")
        self.assertEqual(res.coss_eff_status, "absent")
        self.pdf.write_bytes(b"%PDF-1.7 test sheet")
        res = self._export(self._row(), [self._diagram(part="OTHERFET")])
        self.assertEqual(res.coss_status, "rejected")
        res = self._export(self._row(), [self._diagram(), self._diagram(diagram=13)])
        self.assertEqual(res.coss_status, "rejected")
        row = self._row()
        row["pdf"] = str(self.base / "missing.pdf")
        self.assertEqual(self._export(row, [self._diagram()]).coss_status, "rejected")

    def test_known_bad_other_qoss_failures_are_not_explained(self):
        for status in ("unreliable_extrapolation", "chart_clipped_table_authoritative", None):
            row = self._row()
            row["qoss_validation_status"] = status
            res = self._export(row, [self._diagram()])
            self.assertEqual(res.coss_status, "rejected", status)

    def test_exemption_does_not_bypass_coss_anchor_gate(self):
        row = self._row()
        row["anchors"]["Coss"]["value_pf"] *= 1.5
        res = self._export(row, [self._diagram()])
        self.assertEqual(res.coss_status, "rejected")
        self.assertTrue(any(r.startswith("coss_anchor_mismatch") for r in res.coss_reasons))
        self.assertEqual(res.coss_eff_status, "pass")  # independent evidence

    def test_row_without_pdf_resolves_through_charts_json(self):
        import json
        row = self._row()
        del row["pdf"]
        row["crop"] = str(self.base / "crops/TESTFET/p09_diagram_11.png")
        (self.base / "charts.json").write_text(json.dumps([
            {"crop_png": "crops/TESTFET/p09_diagram_11.png", "pdf": str(self.pdf)}]))
        self.assertEqual(self._export(row, [self._diagram()]).coss_status, "pass")
        (self.base / "charts.json").unlink()
        self.assertEqual(self._export(row, [self._diagram()]).coss_status, "rejected")


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
