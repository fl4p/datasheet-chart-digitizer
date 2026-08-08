"""The resolution downgrade must key on the SERVED CURVE, not the anchor.

The anchor is stated at one voltage; the curve keeps falling past it. Judging
resolvability on the anchor alone missed traces resolvable where the anchor sits
and sub-pixel further along -- HYG065N10LS1P's Crss anchor is 7.8 px while its
served curve reaches 0.57 px, worse than the 0.84 px case the rule was written
for, and its anchor residual is -92.7%. It reported status=ok.
"""
from __future__ import annotations

import unittest

from datasheet_chart_digitizer.capacitance_validation import (
    MIN_ANCHOR_RESOLUTION_PX,
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

    def test_the_threshold_is_the_documented_one(self) -> None:
        just_under = (MIN_ANCHOR_RESOLUTION_PX - 0.01) * PF_PER_PX
        just_over = (MIN_ANCHOR_RESOLUTION_PX + 0.01) * PF_PER_PX
        self.assertIn("Crss", unresolved_anchor_traces(
            False, PF_PER_PX, {"Crss": 5000.0}, {"Crss": just_under}))
        self.assertNotIn("Crss", unresolved_anchor_traces(
            False, PF_PER_PX, {"Crss": 5000.0}, {"Crss": just_over}))
