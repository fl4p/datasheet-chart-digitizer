"""VGS(Qg) monotonicity is checked on every served gate-charge curve.

Found by the 2026-09-30 astra-review-50 pass: on STN1NF20 p7 (ST, VGS + VDS on
one plot) the raster trace follows the VGS ramp and the 5 V plateau, then at
~2.4 nC leaves it for the falling VDS stroke and rides VDS down to 0 V. The
coarse monotonicity check existed but ran only on the bounded-OCR dual-y
path, so the chart was served "ok".
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from datasheet_chart_digitizer import gate_charge as gate
from datasheet_chart_digitizer.gate_charge_trace import _gate_curve_is_monotone, _trim_terminal_branch_hop

ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"


class MonotonePredicateTests(unittest.TestCase):
    BOX = (0, 0, 480, 400)

    def test_ramp_plateau_ramp_is_monotone(self) -> None:
        curve = [(x, 400 - x) for x in range(0, 100)]
        curve += [(x, 300) for x in range(100, 250)]
        curve += [(x, 300 - (x - 250)) for x in range(250, 480)]
        self.assertTrue(_gate_curve_is_monotone(curve, self.BOX))

    def test_plateau_then_falling_branch_is_not_monotone(self) -> None:
        # the STN1NF20 shape: ramp, plateau, then the falling VDS stroke
        curve = [(x, 400 - x) for x in range(0, 100)]
        curve += [(x, 300) for x in range(100, 180)]
        curve += [(x, 300 + (x - 180)) for x in range(180, 280)]
        curve += [(x, 398) for x in range(280, 480)]
        self.assertFalse(_gate_curve_is_monotone(curve, self.BOX))

    def test_terminal_hop_after_the_rise_is_trimmed_to_one_branch(self) -> None:
        # rises to 85 % of the height, then drops onto a lower branch
        curve = [(x, 400 - x) for x in range(0, 100)]
        curve += [(x, 300) for x in range(100, 200)]
        curve += [(x, 300 - 2 * (x - 200)) for x in range(200, 330)]  # to y=40
        curve += [(x, 120 - (x - 330) // 4) for x in range(330, 480)]
        trimmed = _trim_terminal_branch_hop(curve, self.BOX)
        self.assertLessEqual(max(x for x, _ in trimmed), 330)
        self.assertTrue(_gate_curve_is_monotone(trimmed, self.BOX))

    def test_early_fall_is_not_trimmed(self) -> None:
        curve = [(x, 400 - x) for x in range(0, 100)]
        curve += [(x, 300) for x in range(100, 180)]
        curve += [(x, 300 + (x - 180)) for x in range(180, 280)]
        curve += [(x, 398) for x in range(280, 480)]
        self.assertEqual(_trim_terminal_branch_hop(curve, self.BOX), curve)

    def test_unevaluable_curve_is_not_monotone(self) -> None:
        self.assertFalse(_gate_curve_is_monotone([(0, 400), (10, 390)], self.BOX))


class RealPdfTests(unittest.TestCase):
    def _results(self, rel: str):
        pdf = ROOT / rel
        if not pdf.exists():
            self.skipTest(f"{rel} regression PDF is not configured")
        results, _errors = gate.digitize_gate_charge_fail_closed(pdf, dpi=220, finder_dpi=220)
        return results

    def test_stn1nf20_branch_switch_onto_vds_is_not_served(self) -> None:
        panel = [r for r in self._results("st/STN1NF20.pdf") if r.panel.page == 7 and r.panel.diagram == 8]
        self.assertEqual(len(panel), 1)
        self.assertNotEqual(panel[0].status, "ok")
        self.assertIn("non_monotone_gate_curve", panel[0].diagnostics)

    def test_irfp4127_terminal_branch_hop_is_trimmed_and_served(self) -> None:
        pdf = ROOT / "infineon/IRFP4127PBF.pdf"
        if not pdf.exists():
            self.skipTest("IRFP4127PBF regression PDF is not configured")
        ok = [r for r in gate.digitize_gate_charge(pdf, finder_dpi=120) if r.status == "ok"]
        self.assertEqual(len(ok), 1)
        self.assertAlmostEqual(ok[0].vpl, 4.48, delta=0.1)
        self.assertTrue(_gate_curve_is_monotone(list(ok[0].curve_px), ok[0].plot_box_px))

    def test_ao4480_monotone_curve_stays_ok(self) -> None:
        ok = [r for r in self._results("ao/AO4480.pdf") if r.status == "ok"]
        self.assertEqual(len(ok), 1)
        self.assertNotIn("non_monotone_gate_curve", ok[0].diagnostics)


if __name__ == "__main__":
    unittest.main()
