"""Tests for the review-gated saturation transfer-curve digitizer/fitter."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from datasheet_chart_digitizer import find_charts
from datasheet_chart_digitizer import transfer_characteristics as tc

INFINEON = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon")


def _synthetic_curves():
    tref, thot = 25.0, 175.0
    vth, k, p = 3.0, 25.0, 2.0
    d_vth = -3.0e-3 * (thot - tref)
    d_log_k = -2.0e-3 * (thot - tref)
    vgs = np.linspace(2.5, 7.0, 800)
    cold_i = k * np.maximum(vgs - vth, 0.0) ** p
    hot_i = k * math.exp(d_log_k) * np.maximum(vgs - (vth + d_vth), 0.0) ** p
    curves = [
        tc.TransferCurve(tref, list(zip(vgs, cold_i))),
        tc.TransferCurve(thot, list(zip(vgs, hot_i))),
    ]
    anchor = {
        "tref_c": tref,
        "vth_eff_v": vth,
        "k_a_per_vp": k,
        "p": p,
        "id_gc_a": 100.0,
        "vpl_v": 5.0,
    }
    return curves, anchor


class TemperatureFitUnit(unittest.TestCase):
    def test_recovers_known_coefficients_and_pivot(self):
        curves, anchor = _synthetic_curves()
        fit = tc.fit_saturation_tempco(curves, anchor)
        self.assertAlmostEqual(fit["d_vth_eff_v_per_k"], -3.0e-3, delta=2e-6)
        self.assertAlmostEqual(fit["d_log_k_per_k"], -2.0e-3, delta=2e-6)
        self.assertLess(fit["matched_shift_fit_rms_v"], 1e-4)
        self.assertFalse(fit["cold_anchor_conflict"])

    def test_moved_plateau_anchor_refuses(self):
        curves, anchor = _synthetic_curves()
        anchor["id_gc_a"] = 80.0
        with self.assertRaisesRegex(RuntimeError, "does not reproduce"):
            tc.fit_saturation_tempco(curves, anchor)

    def test_missing_anchor_refuses(self):
        curves, anchor = _synthetic_curves()
        del anchor["p"]
        with self.assertRaisesRegex(RuntimeError, "missing"):
            tc.fit_saturation_tempco(curves, anchor)


@unittest.skipUnless((INFINEON / "IPP024N08NF2S.pdf").exists(), "local datasheet unavailable")
class InfineonTransferPilot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="transfer-test-"))
        panels = find_charts.process_pdf(INFINEON / "IPP024N08NF2S.pdf", cls.out, dpi=180)
        cls.chart = next(find_charts.asdict(p) for p in panels if p.kind == "transfer")
        cls.anchor = {
            "tref_c": 25.0,
            "vth_eff_v": 3.0793103448275865,
            "k_a_per_vp": 38.071525577184254,
            "p": 2.0,
            "id_gc_a": 100.0,
            "vpl_v": 4.7,
        }
        crop = cls.out / cls.chart["crop_png"]
        cls.result = tc.process_chart(
            cls.chart, crop, cls.out / "digitized", Path(cls.chart["crop_png"]).with_suffix(""), cls.anchor
        )

    def test_extracts_two_labeled_curves(self):
        self.assertEqual(self.result["temperatures_c"], [25.0, 175.0])
        self.assertGreater(min(self.result["n_points"].values()), 400)

    def test_fit_is_numerically_well_conditioned_but_unapproved(self):
        fit = self.result["fit"]
        self.assertLess(fit["matched_shift_fit_rms_v"], 0.03)
        self.assertLess(fit["d_vth_eff_v_per_k"], 0.0)
        self.assertLess(fit["d_log_k_per_k"], 0.0)
        self.assertIsNotNone(fit["ztc_chart_a"])
        self.assertAlmostEqual(fit["ztc_model_a"], fit["ztc_chart_a"], delta=0.10 * fit["ztc_chart_a"])
        self.assertEqual(self.result["status"], "overlay-review-required")
        self.assertIn("HUMAN REVIEW REQUIRED", self.result["warnings"][0])

    def test_review_artifacts_exist_and_json_is_strict(self):
        self.assertTrue(Path(self.result["csv"]).exists())
        self.assertTrue(Path(self.result["overlay"]).exists())
        json.loads(json.dumps(self.result, allow_nan=False))


if __name__ == "__main__":
    unittest.main()
