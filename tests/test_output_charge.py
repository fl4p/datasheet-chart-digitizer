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
    _fit_slope,
    _window_slope,
    charge_unit_scale_pc,
    evaluate_trace,
    file_sha256,
    integrate_knots,
    is_output_charge_chart,
    producer_sha256,
    read_points_csv,
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

    @staticmethod
    def _peak_trace(sigma, amp=2000.0, center=70.0, q_step=150.0, decay=0.0):
        # C = 1000 + decay*exp(-V/5) + amp*gauss(center, sigma) pF
        v = np.round(np.linspace(0.0, 100.0, 501) / 0.2) * 0.2
        vv = np.linspace(0.0, 100.0, 200_001)
        cc = (1000.0 + decay * np.exp(-vv / 5.0)
              + amp * np.exp(-((vv - center) ** 2) / (2 * sigma ** 2)))
        qq = np.concatenate([[0.0], np.cumsum(0.5 * (cc[1:] + cc[:-1]) * np.diff(vv))])
        exact = np.interp(v, vv, qq)
        ref = {"qoss_pc": float(np.interp(50.0, vv, qq)), "vint_v": 50.0}
        return v, exact, np.round(exact / q_step) * q_step, ref

    def test_served_knots_match_the_window_resolved_truth(self):
        # A knot is the derivative resolved at its fit window (structure narrower than
        # the window is averaged, preserving charge).  Against the SAME fit applied to the
        # exact charge, every served knot must lie within its reported uncertainty.
        for sigma in (0.3, 1.0, 3.0):
            for q_step in (50.0, 150.0, 500.0):
                v, exact, q, ref = self._peak_trace(sigma, q_step=q_step)
                r = evaluate_trace(v, q, ref, q_quantum_pc=q_step, v_quantum_v=0.0)
                if r["status"] != "pass":
                    continue
                for (kv, c), u, h in zip(r["coss_eff_curve"], r["coss_eff_rel_uncertainty"],
                                         r["coss_eff_half_window_v"]):
                    if kv == 0.0:
                        continue
                    m = (v >= kv - h) & (v <= kv + h)
                    truth, _ = _fit_slope(v[m] - kv, exact[m], 2 if kv - h < v[0] else 1)
                    self.assertLessEqual(abs(c - truth) / c, u + 1e-3, (sigma, q_step, kv))

    def test_known_bad_more_allowed_noise_never_turns_refusal_into_pass(self):
        # identical samples; only the declared quantum grows.  The decaying case is
        # where subtracting the higher-order fit's noise bound from the bias passed
        # again at 75-250 pC after refusing at 50 pC.
        for sigma, center, decay, step in ((0.3, 70.0, 0.0, 100.0), (1.0, 70.0, 0.0, 100.0),
                                           (0.5, 20.0, 3000.0, 50.0)):
            v, _, q, ref = self._peak_trace(sigma, center=center, q_step=step, decay=decay)
            seen_reject = False
            for allow in (step, 1.5 * step, 2 * step, 3 * step, 5 * step, 10_000.0):
                st = evaluate_trace(v, q, ref, q_quantum_pc=allow, v_quantum_v=0.0)["status"]
                if seen_reject:
                    self.assertEqual(st, "rejected", (sigma, center, allow))
                seen_reject |= st == "rejected"
            self.assertTrue(seen_reject)

    def test_horizontal_error_bound_covers_the_true_slope(self):
        # Q = 1000 V pF sampled at positions each off by at most v_err (half a pixel):
        # the bound must cover the error of the slope it is reported for.
        x = np.round(np.arange(-1.5, 1.5001, 0.2), 6)
        actual = x - 0.1 * np.sign(x)
        slope, bound, _ = _window_slope(x, 1000.0 * actual, 0.0, 1, 0.0, 0.1)
        self.assertGreater(abs(slope - 1000.0), 90.0)  # the distortion is real (905.9 pF)
        self.assertGreaterEqual(bound + 1e-9, abs(slope - 1000.0))  # by the bound alone

    def test_uncertainty_uses_the_fits_true_sensitivity(self):
        # a line through N uniform points over width W has sum|w| -> 3/W (not 1/W)
        x = np.linspace(-1.5, 1.5, 301)
        slope, bound, _ = _window_slope(x, 1000.0 * x, 0.0, 1, q_err=50.0, v_err=0.0)
        self.assertAlmostEqual(slope, 1000.0, places=6)
        width = 3.0
        self.assertAlmostEqual(bound, 50.0 * 3.0 / width, delta=0.02 * 50.0)  # 1.5x of 2*q_err/W
        # adversarial +/-q_err pattern matching the weight signs reaches the bound
        X = np.vander(x, 2, increasing=True)
        w = np.linalg.pinv(X)[1]
        s_adv, _, _ = _window_slope(x, 1000.0 * x + 50.0 * np.sign(w), 0.0, 1, 50.0, 0.0)
        self.assertAlmostEqual(s_adv - 1000.0, bound, delta=1e-6 * bound)

    def test_short_or_nonfinite_trace_rejected(self):
        self.assertEqual(evaluate_trace(np.arange(5.0), np.arange(5.0), REF)["status"], "rejected")
        v, q = _sqrt_trace()
        q = q.copy()
        q[10] = np.nan
        self.assertEqual(evaluate_trace(v, q, REF)["status"], "rejected")


Q50 = 93_000.0
K93 = Q50 / (math.sqrt(51.0) - 1.0)


Q_QUANTUM, V_QUANTUM = 1.0, 0.001


def _write_qoss_points(path: Path) -> None:
    """A clean synthetic diagram trace (Qoss(50 V) = 93 nC), written like process_chart."""
    v = np.linspace(0.0, 100.0, 500)
    q_nc = K93 * (np.sqrt(1.0 + v) - 1.0) / 1e3
    path.write_text("VDS_V,Qoss_nC\n" + "".join(f"{a:.4f},{b:.4f}\n" for a, b in zip(v, q_nc)))


def _contradiction_row(sha: str) -> dict:
    """A C(V) row whose chart integrates to 0.386x the table: the M8 situation."""
    row = _good_row()
    row.update({
        "source_pdf_sha256": sha,
        "qoss_validation_status": "graph_table_inconsistent",
        "qoss_validation_error": "Qoss: extracted 3.59e+04, datasheet 9.3e+04, rel 61.4%",
        "qoss_metrics": None,
        "qoss_diagnostic_metrics": {"Qoss_pc": 35_900.0},
        "output_charge_reference": {"qoss_pc": Q50, "vint_v": 50.0},
    })
    return row


class ExportGateTests(unittest.TestCase):
    SHA = "a" * 64

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.csv = self.base / "TESTFET.qoss_points.csv"
        _write_qoss_points(self.csv)
        v, q = read_points_csv(self.csv)
        self.trace = evaluate_trace(v, q, {"qoss_pc": Q50, "vint_v": 50.0},
                                    q_quantum_pc=Q_QUANTUM, v_quantum_v=V_QUANTUM)
        assert self.trace["status"] == "pass", self.trace["reasons"]

    def tearDown(self):
        self._tmp.cleanup()

    def _row(self):
        return _contradiction_row(self.SHA)

    def _diagram(self, **over) -> dict:
        t = self.trace
        verified = {"status": "verified"}
        d = {
            "part": "TESTFET", "diagram": 12, "page": 9, "status": "pass", "reasons": [],
            "method": "vector",
            "calibration": {"grid_check": {"x": dict(verified), "y": dict(verified)}},
            "anchor": t["anchor"],
            "coss_eff_curve": [list(k) for k in t["coss_eff_curve"]],
            "coss_eff_rel_uncertainty": t["coss_eff_rel_uncertainty"],
            "coss_eff_integral_max_error": t["coss_eff_integral_max_error"],
            "quantum": {"q_pc": Q_QUANTUM, "v_v": V_QUANTUM},
            "csv": str(self.csv),
            "points_sha256": file_sha256(self.csv),
            "provenance": {"pdf_sha256": self.SHA, "producer_sha256": producer_sha256()},
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
        self.assertEqual(res.coss_eff_status, "pass", res.coss_eff_reasons)
        self.assertEqual(res.coss_eff_curve[0][0], 0.0)

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

    def test_known_bad_served_curve_must_equal_the_recomputation(self):
        good = self._diagram()["coss_eff_curve"]
        bad_curves = {
            "times_10": [[v, c * 10] for v, c in good],
            "times_0.9": [[v, c * 0.9] for v, c in good],
            # same Q at the 50 V anchor, wrong shape (review 3, R1)
            "tail_above_50V_x10": [[v, c * 10 if v > 50.0 else c] for v, c in good],
            "constant_three_knots": [[0.0, 1860.0], [50.0, 1860.0], [100.0, 1860.0]],
            "singleton": good[:1],
            "nan_c": [good[0]] + [[good[1][0], float("nan")]] + good[2:],
            "unsorted": [good[0], good[2], good[1]] + good[3:],
            "not_from_zero": good[1:],
            "malformed": [[1.0], "x"],
            "missing": None,
        }
        for name, curve in bad_curves.items():
            res = self._export(self._row(), [self._diagram(coss_eff_curve=curve)])
            self.assertEqual(res.coss_eff_status, "rejected", name)
            self.assertEqual(res.coss_status, "rejected", name)
            self.assertEqual(res.coss_eff_curve, [], name)

    def test_stored_gates_and_anchor_are_ignored_not_trusted(self):
        # whatever the record claims, the export recomputes from the points (R2)
        for over in ({"coss_eff_rel_uncertainty": [-math.inf]},
                     {"coss_eff_rel_uncertainty": [0.0]},
                     {"coss_eff_integral_max_error": -1.0},
                     {"anchor": {**self.trace["anchor"], "diagram_pc": 10_000.0}}):
            res = self._export(self._row(), [self._diagram(**over)])
            self.assertEqual(res.coss_eff_status, "pass", over)
            self.assertEqual(res.qoss_check["diagram_qoss_pc"], self.trace["anchor"]["diagram_pc"])
            self.assertEqual(res.coss_eff_source["rel_uncertainty"],
                             self.trace["coss_eff_rel_uncertainty"])

    def test_known_bad_record_evidence(self):
        mutations = {
            "raster": {"method": "raster"},
            "grid_unverified": {"calibration": {"grid_check": {"x": {"status": "verified"},
                                                               "y": {"status": "unverified"}}}},
            "grid_x_twice": {"calibration": {"grid_check": {"x": {"status": "verified"},
                                                            "x2": {"status": "verified"}}}},
            "grid_missing": {"calibration": {}},
            "stale_producer": {"provenance": {"pdf_sha256": self.SHA, "producer_sha256": "0" * 64}},
            "points_digest_wrong": {"points_sha256": "0" * 64},
            "points_digest_missing": {"points_sha256": None},
            "points_missing": {"csv": str(self.base / "nope.csv")},
            "quantum_missing": {"quantum": None},
            "quantum_nan": {"quantum": {"q_pc": float("nan"), "v_v": V_QUANTUM}},
            # a coarser declared pixel than the points support: recomputation refuses
            "quantum_coarse": {"quantum": {"q_pc": 50_000.0, "v_v": V_QUANTUM}},
        }
        for name, over in mutations.items():
            res = self._export(self._row(), [self._diagram(**over)])
            self.assertEqual(res.coss_eff_status, "rejected", name)
            self.assertEqual(res.coss_status, "rejected", name)

    def test_known_bad_points_edited_after_digitization(self):
        d = self._diagram()
        lines = self.csv.read_text().splitlines()
        lines[300] = lines[300].split(",")[0] + ",500.0000"
        self.csv.write_text("\n".join(lines) + "\n")
        res = self._export(self._row(), [d])
        self.assertEqual(res.coss_eff_status, "rejected")
        self.assertIn("output_charge_points_missing_or_changed", res.coss_eff_reasons)

    def test_known_bad_nonfinite_row_reference_or_integral(self):
        for ref in ({"qoss_pc": float("nan"), "vint_v": 50.0}, {"qoss_pc": Q50, "vint_v": None},
                    {"qoss_pc": 2 * Q50, "vint_v": 50.0}):
            row = self._row()
            row["output_charge_reference"] = ref
            res = self._export(row, [self._diagram()])
            self.assertEqual(res.coss_status, "rejected", ref)
            self.assertEqual(res.coss_eff_status, "rejected", ref)
        for metrics in (None, {"Qoss_pc": float("nan")}, {"Qoss_pc": float("inf")}, {"Qoss_pc": 0.0}):
            row = self._row()
            row["qoss_diagnostic_metrics"] = metrics
            self.assertEqual(self._export(row, [self._diagram()]).coss_status, "rejected", metrics)

    def test_known_bad_source_not_bound(self):
        for sha in (None, "", "b" * 64, "a" * 63):
            row = self._row()
            row["source_pdf_sha256"] = sha
            res = self._export(row, [self._diagram()])
            self.assertEqual(res.coss_status, "rejected", sha)
            self.assertEqual(res.coss_eff_status, "absent", sha)
            self.assertEqual(res.coss_eff_reasons, ["output_charge_diagram_not_bound_to_this_pdf"])
        res = self._export(self._row(), [self._diagram(part="OTHERFET")])
        self.assertEqual(res.coss_status, "rejected")
        res = self._export(self._row(), [self._diagram(), self._diagram(diagram=13)])
        self.assertEqual(res.coss_status, "rejected")

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

    def test_no_panels_reads_nothing(self):
        # a row with no diagrams in play must export exactly as before, whatever lies around
        row = _good_row()
        row["crop"] = "/nonexistent/crops/TESTFET/p09_diagram_11.png"
        res = self._export(row, [])
        self.assertEqual(res.coss_status, "pass", res.coss_reasons)
        self.assertEqual(res.coss_eff_status, "absent")

    def test_producer_fingerprint_covers_the_whole_package(self):
        import hashlib
        import datasheet_chart_digitizer.output_charge as oc
        here = Path(oc.__file__).resolve().parent
        h = hashlib.sha256()
        for path in sorted(here.glob("*.py")):
            h.update(path.name.encode())
            h.update(path.read_bytes())
        self.assertEqual(producer_sha256(), h.hexdigest())
        self.assertIn("capacitance_refs.py", [p.name for p in here.glob("*.py")])


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
