"""Contract tests for numeric_axis.fit_axis_ticks, especially the ``model`` param.

The public shared fitter must let a consumer that KNOWS its axis type force
linear/log, because ``"auto"`` refuses a genuinely-linear narrow-positive axis as
ambiguous (log fits nearly as well over a narrow positive range). These pin that
contract per codex-ee-root's spec: auto byte-parity, forced-linear, forced-log,
residual refusal, min-count, plus the exact narrow-positive regression.
"""
from __future__ import annotations

import unittest

from datasheet_chart_digitizer.numeric_axis import AxisTick, fit_axis_ticks


def _ticks(values, pixels):
    return [AxisTick(f"{v:g}", float(v), float(p)) for v, p in zip(values, pixels)]


class FitAxisTicksModelParam(unittest.TestCase):
    def test_auto_byte_parity_with_forced_when_unambiguous(self):
        # a clear linear axis (wide range incl. 0): auto and forced-linear agree
        ticks = _ticks([0, 10, 20, 30], [10, 20, 30, 40])
        auto = fit_axis_ticks(ticks)
        forced = fit_axis_ticks(ticks, model="linear")
        self.assertEqual(auto.model, "linear")
        self.assertEqual((auto.model, auto.m, auto.b), (forced.model, forced.m, forced.b))

    def test_narrow_positive_linear_regression(self):
        # the exact case that BLOCKED the breakdown fold: values 100..103 are a
        # valid linear axis but auto calls linear/log ambiguous; forced-linear
        # must accept it (this is why consumers force the model).
        ticks = _ticks([100, 101, 102, 103], [0, 100, 200, 300])
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            fit_axis_ticks(ticks)  # auto
        forced = fit_axis_ticks(ticks, model="linear")
        self.assertEqual(forced.model, "linear")
        self.assertAlmostEqual(forced.value(0.0), 100.0, places=6)
        self.assertAlmostEqual(forced.value(300.0), 103.0, places=6)

    def test_forced_log_on_decade_axis(self):
        ticks = _ticks([1, 10, 100, 1000], [10, 20, 30, 40])
        axis = fit_axis_ticks(ticks, model="log10")
        self.assertEqual(axis.model, "log10")
        self.assertAlmostEqual(axis.value(10.0), 1.0, places=6)
        self.assertAlmostEqual(axis.value(40.0), 1000.0, places=6)

    def test_forced_log_on_nonpositive_fails_closed(self):
        ticks = _ticks([-1, 0, 1, 2], [10, 20, 30, 40])
        with self.assertRaisesRegex(RuntimeError, "positive"):
            fit_axis_ticks(ticks, model="log10")

    def test_forced_linear_still_applies_residual_gate(self):
        # a badly non-linear (curved) tick set forced to linear must still be
        # rejected by the residual gate, not silently mis-fit
        ticks = _ticks([1, 10, 100, 1000], [10, 20, 30, 40])  # exponential -> curved for linear
        with self.assertRaisesRegex(RuntimeError, "untrusted"):
            fit_axis_ticks(ticks, model="linear")

    def test_min_count_refused_in_every_model(self):
        for model in ("auto", "linear", "log10"):
            with self.assertRaisesRegex(RuntimeError, "need >=2"):
                fit_axis_ticks(_ticks([1], [1]), model=model)

    def test_unknown_model_is_value_error(self):
        with self.assertRaises(ValueError):
            fit_axis_ticks(_ticks([0, 1, 2], [0, 1, 2]), model="quadratic")  # type: ignore[arg-type]

    def test_default_is_explicit_auto(self):
        # the default must be byte-identical to passing model="auto"
        ticks = _ticks([0, 10, 20, 30], [10, 20, 30, 40])
        default = fit_axis_ticks(ticks)
        explicit = fit_axis_ticks(ticks, model="auto")
        self.assertEqual(
            (default.model, default.m, default.b, default.residual_px),
            (explicit.model, explicit.m, explicit.b, explicit.residual_px),
        )

    def test_auto_no_candidate_keeps_legacy_error(self):
        # a degenerate near-zero-slope signed axis has no valid candidate; auto
        # must keep the pre-model-param wording "no valid linear/log calibration"
        ticks = _ticks([-1e-13, 0.0, 1e-13], [0.0, 1.0, 2.0])
        with self.assertRaisesRegex(RuntimeError, "no valid linear/log calibration"):
            fit_axis_ticks(ticks)


if __name__ == "__main__":
    unittest.main()
