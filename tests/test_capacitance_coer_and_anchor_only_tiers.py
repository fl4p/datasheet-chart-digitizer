"""Calibration for the two non-Qoss Coss validation tiers.

Both exist because 26 of 30 parts in the fugu2 HS corpus print no Qoss, Eoss,
Co(er) or Co(tr) at all, so `qoss_validation_status` returned `None` and the
export gate rejected every Coss trace with `qoss_validation:None:None`.

The rule these tests defend is the one that keeps getting broken: a check that
CANNOT EVALUATE its input must never return the value meaning "fine". Every
unevaluable branch below is asserted to return its own explicit,
non-servable status -- not `None`, not a passing tier.

The second rule is monotonicity of the tier ladder: a part that HAS an integral
reference and FAILS it must keep failing. It must never fall through to the
weaker anchor-only tier, which would make the check vanish exactly when it has
evidence and that evidence is bad.
"""
from __future__ import annotations

import unittest

from datasheet_chart_digitizer.capacitance_validation import (
    COER_ENERGY_INTEGRAL_STATUS,
    COSS_ANCHOR_ONLY_STATUS,
    QOSS_CHARGE_INTEGRAL_STATUSES,
    QOSS_SERVABLE_STATUSES,
    coer_energy_validation,
    coer_integration_voltage,
    coss_anchor_only_validation,
)


class _Metrics:
    def __init__(
        self,
        co_er: float,
        *,
        extrapolated: float = 0.05,
        clipped: bool = False,
    ) -> None:
        self.Co_er = co_er
        self.extrapolated_qoss_fraction = extrapolated
        self.clipped_completion_active = clipped


def _anchor(relative_error: float | None, *, reason: str | None = None) -> dict:
    return {
        "anchor_residuals": {
            "Coss": {
                "vds_v": 50.0,
                "table_pf": 1230.0,
                "sampled_pf": 1230.0 * (1.0 + (relative_error or 0.0)),
                "relative_error": relative_error,
                "reason": reason,
            }
        }
    }


class CoerEnergyTierCalibration(unittest.TestCase):
    def test_agreeing_energy_integral_passes_with_its_own_name(self) -> None:
        status, diagnostics = coer_energy_validation(_Metrics(1500.0), 1521.0, 50.0)
        self.assertEqual(status, COER_ENERGY_INTEGRAL_STATUS)
        self.assertIn(status, QOSS_SERVABLE_STATUSES)
        self.assertNotIn(
            status,
            QOSS_CHARGE_INTEGRAL_STATUSES,
            "the Co(er) tier must not masquerade as a Qoss charge check",
        )
        self.assertLess(diagnostics["relative_residual"], 0.25)

    def test_known_bad_curve_is_rejected(self) -> None:
        # A curve whose energy integral is half the datasheet Co(er): the exact
        # failure this tier exists to catch. Watch it fail.
        status, diagnostics = coer_energy_validation(_Metrics(760.0), 1521.0, 50.0)
        self.assertEqual(status, "coer_graph_table_inconsistent")
        self.assertNotIn(status, QOSS_SERVABLE_STATUSES)
        self.assertGreater(diagnostics["relative_residual"], 0.25)

    def test_rejection_is_monotone_as_the_curve_gets_worse(self) -> None:
        # No region where a worse curve flips back to passing.
        previous = None
        for factor in (1.0, 1.2, 1.26, 2.0, 10.0, 1000.0):
            status, diagnostics = coer_energy_validation(
                _Metrics(1521.0 * factor), 1521.0, 50.0
            )
            residual = diagnostics["relative_residual"]
            if previous is not None:
                self.assertGreaterEqual(residual, previous)
            previous = residual
            if factor > 1.25:
                self.assertNotIn(status, QOSS_SERVABLE_STATUSES, f"factor {factor}")

    def test_every_unevaluable_input_is_explicit_and_not_servable(self) -> None:
        cases = {
            "coer_reference_unavailable": (_Metrics(1500.0), None, 50.0),
            "coer_reference_condition_voltage_unavailable": (
                _Metrics(1500.0),
                1521.0,
                None,
            ),
            "coer_energy_integral_unavailable": (None, 1521.0, 50.0),
            "coer_unreliable_extrapolation": (
                _Metrics(1500.0, extrapolated=0.60),
                1521.0,
                50.0,
            ),
            "coer_chart_clipped": (
                _Metrics(1500.0, clipped=True),
                1521.0,
                50.0,
            ),
        }
        for expected, args in cases.items():
            with self.subTest(expected):
                status, _ = coer_energy_validation(*args)
                self.assertEqual(status, expected)
                self.assertNotIn(status, QOSS_SERVABLE_STATUSES)
                self.assertIsNotNone(status)

    def test_a_nonfinite_integral_does_not_pass(self) -> None:
        for value in (float("nan"), 0.0, -5.0):
            with self.subTest(value=value):
                status, _ = coer_energy_validation(_Metrics(value), 1521.0, 50.0)
                self.assertEqual(status, "coer_energy_integral_unavailable")


class AnchorOnlyTierCalibration(unittest.TestCase):
    def test_agreeing_anchor_with_no_integral_reference_passes(self) -> None:
        status, diagnostics = coss_anchor_only_validation(
            _anchor(0.02),
            integral_reference_available=False,
            trace_validation_status="pass",
        )
        self.assertEqual(status, COSS_ANCHOR_ONLY_STATUS)
        self.assertIn(status, QOSS_SERVABLE_STATUSES)
        self.assertNotIn(status, QOSS_CHARGE_INTEGRAL_STATUSES)
        self.assertEqual(diagnostics["coss_anchor_relative_error"], 0.02)

    def test_a_part_with_an_integral_reference_can_never_reach_this_tier(self) -> None:
        # The anti-monotone trap: a part that HAS a Qoss/Co(er) reference and
        # failed it must not be rescued by the weaker tier.
        status, _ = coss_anchor_only_validation(
            _anchor(0.001),
            integral_reference_available=True,
            trace_validation_status="pass",
        )
        self.assertEqual(
            status, "integral_reference_present_anchor_only_not_applicable"
        )
        self.assertNotIn(status, QOSS_SERVABLE_STATUSES)

    def test_known_bad_anchor_is_rejected(self) -> None:
        status, _ = coss_anchor_only_validation(
            _anchor(0.42),
            integral_reference_available=False,
            trace_validation_status="pass",
        )
        self.assertEqual(status, "anchor_only_coss_anchor_inconsistent")
        self.assertNotIn(status, QOSS_SERVABLE_STATUSES)

    def test_rejection_is_monotone_in_both_signs(self) -> None:
        for error in (0.11, 0.3, 1.0, 50.0, -0.11, -0.5, -0.99):
            with self.subTest(error=error):
                status, _ = coss_anchor_only_validation(
                    _anchor(error),
                    integral_reference_available=False,
                    trace_validation_status="pass",
                )
                self.assertNotIn(status, QOSS_SERVABLE_STATUSES)

    def test_suspect_traces_do_not_reach_the_tier(self) -> None:
        for trace_status in ("suspect", None, "unverified"):
            with self.subTest(trace_status):
                status, _ = coss_anchor_only_validation(
                    _anchor(0.001),
                    integral_reference_available=False,
                    trace_validation_status=trace_status,
                )
                self.assertEqual(status, "anchor_only_trace_validation_not_pass")
                self.assertNotIn(status, QOSS_SERVABLE_STATUSES)

    def test_unreadable_anchors_are_explicit_not_fine(self) -> None:
        cases = {
            "anchor_only_no_coss_anchor": {},
            "anchor_only_coss_anchor_unusable": _anchor(None),
        }
        for expected, diagnostics in cases.items():
            with self.subTest(expected):
                status, _ = coss_anchor_only_validation(
                    diagnostics,
                    integral_reference_available=False,
                    trace_validation_status="pass",
                )
                self.assertEqual(status, expected)
                self.assertNotIn(status, QOSS_SERVABLE_STATUSES)

    def test_an_anchor_the_sampler_refused_cannot_be_the_sole_evidence(self) -> None:
        # `anchor_inside_trace_gap`: the value was interpolated across a hole,
        # so a small residual proves nothing.
        status, diagnostics = coss_anchor_only_validation(
            _anchor(0.001, reason="anchor_inside_trace_gap"),
            integral_reference_available=False,
            trace_validation_status="pass",
        )
        self.assertEqual(status, "anchor_only_coss_anchor_unusable")
        self.assertEqual(diagnostics["coss_anchor_reason"], "anchor_inside_trace_gap")

    def test_none_diagnostics_do_not_crash_or_pass(self) -> None:
        status, _ = coss_anchor_only_validation(
            None,
            integral_reference_available=False,
            trace_validation_status="pass",
        )
        self.assertEqual(status, "anchor_only_no_coss_anchor")
        self.assertNotIn(status, QOSS_SERVABLE_STATUSES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class CoerIntegrationVoltageSeam(unittest.TestCase):
    """Co(er)'s energy integral must use Co(er)'s OWN condition voltage.

    This pins the CALL SITE, not just the validator. The previous expression was
    `output_ref.coer_vint_v or output_ref.vint_v`, and every validator test still
    passed with it in place -- the same untested-wiring gap that let an earlier
    silent-wrong survive a full green suite.
    """

    class _Ref:
        def __init__(self, coer_vint_v, vint_v, coer_pf=820.0):
            self.coer_vint_v = coer_vint_v
            self.vint_v = vint_v
            self.coer_pf = coer_pf

    def test_uses_coers_own_condition(self) -> None:
        # Infineon shape: Qoss/Coss stated at 50 V, Co(er) integrated over 0->80 V.
        ref = self._Ref(coer_vint_v=80.0, vint_v=50.0)
        self.assertEqual(coer_integration_voltage(ref), 80.0)

    def test_never_borrows_qoss_condition_voltage(self) -> None:
        # THE known-bad input: Co(er) condition did not parse, Qoss's did. Borrowing
        # 50 V for an 80 V reference is a 2.56x energy error reported as a pass.
        ref = self._Ref(coer_vint_v=None, vint_v=50.0)
        self.assertIsNone(coer_integration_voltage(ref))
        status, _ = coer_energy_validation(
            _Metrics(co_er=820.0), ref.coer_pf, coer_integration_voltage(ref)
        )
        self.assertNotEqual(status, COER_ENERGY_INTEGRAL_STATUS)
        self.assertNotIn(status, QOSS_SERVABLE_STATUSES)

    def test_absent_and_degenerate_voltages_are_not_fine(self) -> None:
        for bad in (None, 0.0, -80.0):
            with self.subTest(coer_vint_v=bad):
                self.assertIsNone(
                    coer_integration_voltage(self._Ref(bad, vint_v=50.0)))
