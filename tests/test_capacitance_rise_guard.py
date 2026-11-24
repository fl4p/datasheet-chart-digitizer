"""Rise guard: the superjunction fall-then-rise exemption and its title gate.

Split from test_mosfet_capacitance.py (1,500-line file budget). The exemption
exists for IPLT60R160CM8 (CoolMOS CM8: Crss falls to a minimum, then rises);
the guard it relaxes exists for NCE2010E p4 d10, a body-diode panel mis-seated
as C(V) whose traces all rise.
"""

import unittest

from datasheet_chart_digitizer import mosfet_capacitance as mc
from datasheet_chart_digitizer.axis_title_identity import capacitance_titles_confirmed
from datasheet_chart_digitizer.capacitance_validation import fall_then_rise_fractions


class RiseGuardTests(unittest.TestCase):
    @staticmethod
    def _rise_diagnostics(crss: dict) -> dict:
        return {
            "Ciss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 40,
                     "value_rise_fraction": -0.02},
            "Coss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 60,
                     "value_rise_fraction": -0.30},
            "Crss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 300, **crss},
            "checks": {
                "common_samples": 200,
                "ciss_coss_rank_swap_count": 0,
                "crss_bottom_fraction": 1.0,
                "ciss_flatter_than_coss": True,
            },
        }

    def test_superjunction_crss_fall_then_rise_passes(self) -> None:
        # IPLT60R160CM8 measured: Crss falls 0.254 to its minimum, rises 0.088.
        summary = mc.trace_validation_summary(self._rise_diagnostics({
            "value_rise_fraction": 0.083,
            "fall_before_min_fraction": 0.254,
            "rise_after_min_fraction": 0.088,
        }), "vector", capacitance_titles_confirmed=True)
        self.assertNotIn("Crss_rises_with_vds_unphysical", summary["reasons"])

    def test_fall_then_rise_needs_confirmed_capacitance_titles(self) -> None:
        # Codex review 2026-11-24: a rising trace with a high left-edge segment
        # (handoff, glyph) measures fall 0.243 > rise 0.128 and would pass on
        # shape alone. Without C-vs-VDS title evidence it must stay flagged.
        diagnostics = self._rise_diagnostics({
            "value_rise_fraction": 0.115,
            "fall_before_min_fraction": 0.2434,
            "rise_after_min_fraction": 0.1284,
        })
        for kwargs in ({}, {"capacitance_titles_confirmed": False}):
            summary = mc.trace_validation_summary(diagnostics, "vector", **kwargs)
            self.assertIn("Crss_rises_with_vds_unphysical", summary["reasons"], kwargs)

    def test_capacitance_title_confirmation(self) -> None:
        ok = capacitance_titles_confirmed
        # Titles measured on the corpus (IPLT60R160CM8, NCE2010E p4 d8).
        self.assertTrue(ok("VDS [V]", "[pF] C")[0])
        self.assertTrue(ok("Vds Drain-Source Voltage (V)", "C Capacitance (pF)")[0])
        # NCE2010E p4 d10, the mis-seated panel: contradicted, not confirmed.
        self.assertEqual(
            (False, "capacitance_y_title_contradicted"),
            ok("Vds Drain-Source Voltage (V)", "Is- Reverse Drain Current (A)"),
        )
        self.assertFalse(ok("t [s]", "ZthJC [K/W]")[0])
        self.assertFalse(ok("VDS [V]", "ID [A]")[0])
        # Missing text is unevaluated, never a confirmation.
        self.assertEqual((False, "capacitance_titles_unevaluated"), ok("", "C [pF]"))
        self.assertEqual((False, "capacitance_titles_unevaluated"), ok("VDS [V]", "  "))

    def test_rise_without_prior_fall_still_flags(self) -> None:
        # NCE2010E SOA panel measured: fall 0.026, rise 0.807.
        summary = mc.trace_validation_summary(self._rise_diagnostics({
            "value_rise_fraction": 0.623,
            "fall_before_min_fraction": 0.026,
            "rise_after_min_fraction": 0.807,
        }), "vector", capacitance_titles_confirmed=True)
        self.assertIn("Crss_rises_with_vds_unphysical", summary["reasons"])

    def test_rise_above_cap_flags_even_after_larger_fall(self) -> None:
        # Monotonicity: past the cap a larger prior fall must not buy a pass.
        for rise in (0.26, 0.5, 0.9):
            summary = mc.trace_validation_summary(self._rise_diagnostics({
                "value_rise_fraction": rise,
                "fall_before_min_fraction": 0.95,
                "rise_after_min_fraction": rise,
            }), "vector", capacitance_titles_confirmed=True)
            self.assertIn("Crss_rises_with_vds_unphysical", summary["reasons"], rise)

    def test_fall_then_rise_fractions_measures_shape(self) -> None:
        # y_px grows downward. V shape: value falls 100 px, then rises 30 px.
        down = [(x, 100 + x) for x in range(0, 100)]
        up = [(x, 200 - (x - 100) * 30 // 100) for x in range(100, 200)]
        fall, rise = fall_then_rise_fractions(down + up, 400)
        self.assertGreater(fall, 0.2)
        self.assertAlmostEqual(0.07, rise, delta=0.02)
        # Monotone rise from the left edge: no fall.
        fall, rise = fall_then_rise_fractions([(x, 300 - x) for x in range(200)], 400)
        self.assertLess(fall, 0.01)
        self.assertGreater(rise, 0.4)
        # Too short to evaluate: None, so no exemption can be granted.
        self.assertIsNone(fall_then_rise_fractions([(x, 10) for x in range(5)], 400))

    def test_units_that_contain_c_do_not_confirm(self) -> None:
        # Codex review pass 2: a bare "C" is also the coulomb and part of "°C".
        for y in ("Temperature [°C]", "Qoss [C]", "Qg [n C]", "Qg [nC]", "Eoss [µJ]", "C"):
            self.assertFalse(capacitance_titles_confirmed("VDS [V]", y)[0], y)
        for y in ("C [pF]", "Ciss, Coss, Crss (pF)", "Capacitance (nF)"):
            self.assertTrue(capacitance_titles_confirmed("VDS [V]", y)[0], y)


    def test_other_axis_variables_and_inverse_units_do_not_confirm(self) -> None:
        # Codex review pass 3: VDS as a condition, or C in a denominator.
        for x, y in (
            ("Frequency (MHz), VDS 25 V", "C (pF)"),
            ("Gate voltage VGS (V) at VDS 25 V", "C (pF)"),
            ("VDS (V)", "1/C^2 (1/pF^2)"),
        ):
            self.assertFalse(capacitance_titles_confirmed(x, y)[0], (x, y))
        self.assertTrue(capacitance_titles_confirmed("V DS, Drain-to-Source Voltage (V)", "Capacitance (pF)")[0])

if __name__ == "__main__":
    unittest.main()
