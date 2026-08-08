"""The resolution downgrade must key on the SERVED CURVE, not the anchor.

The anchor is stated at one voltage; the curve keeps falling past it. Judging
resolvability on the anchor alone missed traces resolvable where the anchor sits
and sub-pixel further along -- HYG065N10LS1P's Crss anchor is 7.8 px while its
served curve reaches 0.57 px, worse than the 0.84 px case the rule was written
for, and its anchor residual is -92.7%. It reported status=ok.
"""
from __future__ import annotations

import re
import unittest

from datasheet_chart_digitizer.capacitance_validation import (
    MIN_ANCHOR_RESOLUTION_PX,
    TINY_CRSS_ANCHOR_PF,
    anchor_resolution_reason,
    unresolved_anchor_traces,
)

PF_PER_PX = 11.3  # HYG065N10LS1P's linear axis


class ServedCurveDecidesResolvability(unittest.TestCase):
    def test_the_known_bad_case_is_now_caught(self) -> None:
        # anchor 88 pF = 7.8 px (resolvable), served curve reaches 6.4 pF = 0.57 px
        out = unresolved_anchor_traces(
            False, PF_PER_PX, {"Crss": 88.0}, {"Crss": 6.4})
        self.assertIn("Crss", out)
        self.assertLess(out["Crss"][0], 1.0)

    def test_anchor_alone_would_have_missed_it(self) -> None:
        # the pre-fix behaviour, reproduced by withholding the served minimum
        self.assertNotIn("Crss", unresolved_anchor_traces(
            False, PF_PER_PX, {"Crss": 88.0}))

    def test_a_genuinely_resolvable_trace_is_untouched(self) -> None:
        self.assertEqual({}, unresolved_anchor_traces(
            False, PF_PER_PX, {"Coss": 900.0}, {"Coss": 400.0}))

    def test_a_sub_pixel_anchor_is_still_caught_without_a_served_curve(self) -> None:
        # the GT060N10T case: 16 pF = 0.84 px. Absent served data must not
        # weaken the check that already worked.
        out = unresolved_anchor_traces(False, 19.06, {"Crss": 16.0}, {})
        self.assertIn("Crss", out)

    def test_verdict_is_monotone_as_the_curve_decays(self) -> None:
        seen_unresolved = False
        for served in (2000.0, 500.0, 100.0, 45.2, 20.0, 5.0, 0.5):
            out = unresolved_anchor_traces(
                False, PF_PER_PX, {"Crss": 88.0}, {"Crss": served})
            now = "Crss" in out
            if seen_unresolved:
                self.assertTrue(
                    now, f"verdict returned to resolvable at {served} pF")
            seen_unresolved = seen_unresolved or now
        self.assertTrue(seen_unresolved, "never fired at the far tail")

    def test_log_axis_still_returns_empty(self) -> None:
        # absence of a linear scale is absence of THIS failure mode, and the
        # served curve must not change that
        self.assertEqual({}, unresolved_anchor_traces(
            True, PF_PER_PX, {"Crss": 88.0}, {"Crss": 0.001}))

    def test_the_reason_reports_the_quantity_it_measured(self) -> None:
        # SUP70060E: anchor 95 pF = 4.95 px, served floor 57.9 pF = 3.02 px.
        # The message used to pair the ANCHOR's value with the FLOOR's pixel
        # count -- "95 pF = 3.02 px" -- naming a quantity that is in neither
        # place. The reason string is all a human reviewer sees, so the pF and
        # the px it quotes together must be the same measurement.
        pf_per_px = 19.21
        (pixels, _, deciding_pf) = unresolved_anchor_traces(
            False, pf_per_px, {"Crss": 95.0}, {"Crss": 57.9})["Crss"]
        reason = anchor_resolution_reason(
            "Crss", pixels, pf_per_px, 95.0, deciding_pf)
        quoted_pf, quoted_px = re.search(
            r"([\d.]+) pF = ([\d.]+) px", reason).groups()
        self.assertAlmostEqual(
            float(quoted_pf) / pf_per_px, float(quoted_px), places=1,
            msg=f"pF and px in {reason!r} are different measurements")
        self.assertIn("curve_floor", reason)
        self.assertIn("anchor 95 pF", reason)  # not lost, just not conflated

    def test_the_reason_falls_back_to_the_anchor_when_that_is_all_there_is(self) -> None:
        (pixels, _, deciding_pf) = unresolved_anchor_traces(
            False, 19.06, {"Crss": 16.0})["Crss"]
        reason = anchor_resolution_reason("Crss", pixels, 19.06, 16.0, deciding_pf)
        quoted_pf, quoted_px = re.search(
            r"([\d.]+) pF = ([\d.]+) px", reason).groups()
        self.assertAlmostEqual(float(quoted_pf), 16.0, places=1)
        self.assertAlmostEqual(float(quoted_px), 16.0 / 19.06, places=1)

    def test_the_tiny_crss_exemption_still_keys_on_the_anchor(self) -> None:
        # The export offset is derived from the SPEC-TABLE value, so how far the
        # trace falls afterwards must not grant or revoke the exemption.
        anchor = TINY_CRSS_ANCHOR_PF - 1.0
        self.assertIn(
            "_offset_corrected_at_export",
            anchor_resolution_reason("Crss", 0.3, 11.3, anchor, 0.5))
        self.assertNotIn(
            "_offset_corrected_at_export",
            anchor_resolution_reason(
                "Crss", 0.3, 11.3, TINY_CRSS_ANCHOR_PF + 1.0, 0.5))

    def test_the_threshold_is_the_documented_one(self) -> None:
        just_under = (MIN_ANCHOR_RESOLUTION_PX - 0.01) * PF_PER_PX
        just_over = (MIN_ANCHOR_RESOLUTION_PX + 0.01) * PF_PER_PX
        self.assertIn("Crss", unresolved_anchor_traces(
            False, PF_PER_PX, {"Crss": 5000.0}, {"Crss": just_under}))
        self.assertNotIn("Crss", unresolved_anchor_traces(
            False, PF_PER_PX, {"Crss": 5000.0}, {"Crss": just_over}))
