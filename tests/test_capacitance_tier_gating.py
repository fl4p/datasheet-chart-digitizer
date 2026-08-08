"""Calibration for the tier LADDER's gating, at the call site.

Both rules here were documented and believed in force while production violated
them, because the tests exercised the validators with values the call site never
produced. These pin the gate itself.
"""
from __future__ import annotations

import unittest

from datasheet_chart_digitizer.capacitance_validation import (
    COSS_ANCHOR_ONLY_STATUS,
    QOSS_SERVABLE_STATUSES,
    coss_anchor_only_validation,
    integral_reference_available,
    weaker_tier_may_follow,
)


class _Ref:
    def __init__(self, qoss_pc=None, coer_pf=None, cotr_pf=None):
        self.qoss_pc = qoss_pc
        self.coer_pf = coer_pf
        self.cotr_pf = cotr_pf


def _good_anchor() -> dict:
    return {"anchor_residuals": {"Coss": {
        "vds_v": 50.0, "table_pf": 1250.0, "sampled_pf": 1373.0,
        "relative_error": 0.098, "reason": None}}}


class IntegralReferenceIsAnyOutputChargeFigure(unittest.TestCase):
    def test_cotr_alone_counts_as_a_reference(self) -> None:
        # THE known-bad input. Co(tr) was invisible to the tier selector while
        # validate_axis checked it, so a Co(tr)-only part whose charge check
        # failed by 69% was served on one anchor point.
        self.assertTrue(integral_reference_available(_Ref(cotr_pf=950.0)))

    def test_each_figure_alone_counts(self) -> None:
        for ref in (_Ref(qoss_pc=47000.0), _Ref(coer_pf=820.0), _Ref(cotr_pf=950.0)):
            with self.subTest(ref=vars(ref)):
                self.assertTrue(integral_reference_available(ref))

    def test_no_figure_at_all_is_the_only_false(self) -> None:
        self.assertFalse(integral_reference_available(_Ref()))

    def test_a_cotr_only_part_cannot_reach_the_anchor_tier(self) -> None:
        status, _ = coss_anchor_only_validation(
            _good_anchor(),
            integral_reference_available=integral_reference_available(
                _Ref(cotr_pf=950.0)),
            trace_validation_status="pass",
        )
        self.assertNotEqual(status, COSS_ANCHOR_ONLY_STATUS)
        self.assertNotIn(status, QOSS_SERVABLE_STATUSES)

    def test_a_part_with_nothing_still_reaches_it(self) -> None:
        status, _ = coss_anchor_only_validation(
            _good_anchor(),
            integral_reference_available=integral_reference_available(_Ref()),
            trace_validation_status="pass",
        )
        self.assertEqual(status, COSS_ANCHOR_ONLY_STATUS)


class WeakerTiersMayNotOverwriteEvidence(unittest.TestCase):
    def test_evidence_based_failures_are_never_followed(self) -> None:
        # Each of these means the curve WAS compared against a table figure and
        # disagreed. Falling through would delete the finding.
        for status in ("graph_table_inconsistent", "unreliable_extrapolation",
                       "chart_clipped_table_authoritative"):
            with self.subTest(status=status):
                self.assertFalse(weaker_tier_may_follow(status))

    def test_absence_of_a_reference_is_followed(self) -> None:
        for status in (None, "reference_unavailable",
                       "chart_clipped_reference_unavailable"):
            with self.subTest(status=status):
                self.assertTrue(weaker_tier_may_follow(status))

    def test_passes_are_never_overwritten(self) -> None:
        for status in ("pass", "pass_vendor_qoss_curve_tail",
                       "clipped_chart_completed", "pass_coer_energy"):
            with self.subTest(status=status):
                self.assertFalse(weaker_tier_may_follow(status))

    def test_an_unknown_status_fails_closed(self) -> None:
        # Allowlist, not denylist: a status added later must not gain a silent
        # fallthrough by default.
        self.assertFalse(weaker_tier_may_follow("some_future_status"))
