"""A registration translated by one line pitch must not bind, nor pass the check.

Found on the vendor-truth benchmark: on six Infineon charts the y frame rules
are hairlines lighter than the ink threshold (grey ~207-224) while the minor
rules (grey ~185) and a black rule ABOVE the plot are ink. The end labels'
own rules are therefore missing at the first evidence level, the only
registration ``_register`` can enumerate is the one that binds every label to
the rule one minor pitch (~27 px) above it, and it won without a rival.
``check_served_on_grid`` re-uses the same binding, so it passed a served
mapping one pitch off and would have FAILED the correct one.

The fixtures are the benchmark's own case images (180 dpi renders of the
Infineon PDFs, cropped; see fixtures/gridline_one_pitch/PROVENANCE.md) with
the vector gridline pixel of every labelled value as ground truth.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from datasheet_chart_digitizer.gridline_anchor import (
    anchor_axis_on_grid,
    anchor_axis_on_grid_attempts,
    check_served_on_grid,
    check_served_on_grid_attempts,
    identify_tick_lines,
    suppress_curve_ink,
)
from datasheet_chart_digitizer.numeric_axis import AxisTick, NumericAxis, fit_axis_ticks

FIXTURES = Path(__file__).parent / "fixtures" / "gridline_one_pitch"


def _shifted(axis: NumericAxis, shift_px: float) -> NumericAxis:
    return NumericAxis(axis.model, axis.m, axis.b - axis.m * shift_px, (), 0.0, ())


def _cases():
    return json.loads((FIXTURES / "cases.json").read_text())


def _load(case):
    gray = np.asarray(Image.open(FIXTURES / case["image"]).convert("L"))
    x0, y0, x1, y1 = case["plot_box_index_px"]
    frame = SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y1)
    model = "log10" if case["y_scale"].startswith("log") else "linear"
    labels = [AxisTick(f"{t['value']:g}", t["value"], t["label_px"]) for t in case["y_ticks"]]
    truth = {t["value"]: t["true_rule_px"] for t in case["y_ticks"]}
    true_axis = fit_axis_ticks(
        [AxisTick(f"{v:g}", v, p) for v, p in truth.items()], "y", model=model
    )
    return gray, frame, (x0, x1), fit_axis_ticks(labels, "y", model=model), labels, truth, true_axis


class InfineonOnePitchRegressionTests(unittest.TestCase):
    """The six vendor-GT charts that bound one grid pitch off on main 7d8dde7."""

    def test_fixture_set_is_complete(self):
        self.assertEqual(len(_cases()), 6)

    def test_y_axis_calibrates_on_the_true_rules(self):
        for case in _cases():
            with self.subTest(case=case["id"]):
                gray, frame, span, label_axis, _, truth, _ = _load(case)
                anchored, _attempt = anchor_axis_on_grid_attempts(
                    gray, label_axis, frame=frame, orientation="y", cross_span=span, name="y"
                )
                self.assertEqual({a.value for a in anchored.anchors}, set(truth))
                for anchor in anchored.anchors:
                    self.assertLess(abs(anchor.served_px - truth[anchor.value]), 0.5, anchor)

    def test_the_level_that_lacks_the_frame_rules_refuses(self):
        """ink<200 on the suppressed raster sees the minors and the black rule
        above the plot but not the hairline frame: it must refuse, not bind."""
        for case in _cases():
            with self.subTest(case=case["id"]):
                gray, frame, span, label_axis, labels, _, _ = _load(case)
                with self.assertRaisesRegex(RuntimeError, "one line pitch off"):
                    identify_tick_lines(
                        suppress_curve_ink(gray, frame), labels, model=label_axis.model,
                        orientation="y", cross_span=span, name="y",
                    )

    def test_served_check_verifies_truth_and_fails_whole_pitch_shifts(self):
        """Known-bad control: a whole-pitch shift lands every served tick on
        a rule. It must FAIL; the true mapping must verify."""
        for case in _cases():
            gray, frame, span, _, labels, truth, true_axis = _load(case)
            rules = sorted(truth.values())
            label_pitch = float(np.median(np.diff(rules)))
            minor_pitch = label_pitch / 4.0  # every one of these charts draws 3 minors
            with self.subTest(case=case["id"], shift=0.0):
                check = check_served_on_grid_attempts(
                    gray, true_axis, labels, frame=frame, orientation="y", cross_span=span, name="y"
                )
                self.assertEqual(check.status, "verified", check.reason)
            for shift in (minor_pitch, -minor_pitch, label_pitch, -label_pitch):
                with self.subTest(case=case["id"], shift=round(shift, 1)):
                    check = check_served_on_grid_attempts(
                        gray, _shifted(true_axis, shift), labels, frame=frame,
                        orientation="y", cross_span=span, name="y",
                    )
                    self.assertEqual(check.status, "failed", check.reason)


# ----------------------------------------------------------------- synthetic
# The same geometry drawn from scratch: 5 labelled rules 108 px apart, three
# minors between, the frame (top and bottom labelled rules) a light hairline
# and an unrelated black rule one minor pitch above the plot.
H, W = 700, 640
LEFT, RIGHT = 100, 620
TOP, PITCH = 76, 108
VALUES = [500.0, 400.0, 300.0, 200.0, 100.0, 0.0]
MINOR = PITCH / 4


def _rule_px(value: float) -> float:
    return TOP + (500.0 - value) / 100.0 * PITCH


def _panel(*, frame_grey=215, rule_grey=185, outside_rule=True):
    image = np.full((H, W), 255, dtype=np.uint8)
    bottom = _rule_px(0.0)
    y = TOP + MINOR
    while y < bottom - 1:
        image[int(round(y)), LEFT:RIGHT + 1] = rule_grey
        y += MINOR
    for edge in (TOP, int(round(bottom))):
        image[edge, LEFT:RIGHT + 1] = frame_grey
    image[TOP:int(round(bottom)) + 1, LEFT] = frame_grey
    image[TOP:int(round(bottom)) + 1, RIGHT] = frame_grey
    if outside_rule:
        image[int(round(TOP - MINOR)) - 1:int(round(TOP - MINOR)) + 2, LEFT:RIGHT + 1] = 0
    return image


def _labels(offsets=None):
    offsets = offsets or [0.0] * len(VALUES)
    return [AxisTick(f"{v:g}", v, _rule_px(v) + d) for v, d in zip(VALUES, offsets)]


SPAN = (LEFT, RIGHT)
FRAME = SimpleNamespace(x0=LEFT, y0=TOP, x1=RIGHT, y1=_rule_px(0.0))


class SyntheticOnePitchTests(unittest.TestCase):
    def _anchor(self, image, labels):
        return anchor_axis_on_grid_attempts(
            image, fit_axis_ticks(labels, "y", model="linear"), frame=FRAME,
            orientation="y", cross_span=SPAN, name="y",
        )

    def test_light_frame_panel_binds_the_labelled_rules(self):
        anchored, _ = self._anchor(_panel(), _labels())
        for anchor in anchored.anchors:
            self.assertLess(abs(anchor.served_px - _rule_px(anchor.value)), 0.5, anchor)

    def test_label_offsets_do_not_break_the_binding(self):
        """Typographic offsets of a few px stay bound to their own rules."""
        labels = _labels([3.0, -2.0, 4.0, 1.0, -3.0, 2.5])
        anchored, _ = self._anchor(_panel(), labels)
        for anchor in anchored.anchors:
            self.assertLess(abs(anchor.served_px - _rule_px(anchor.value)), 0.5, anchor)

    def test_one_level_refuses_rather_than_binding_the_neighbours(self):
        with self.assertRaisesRegex(RuntimeError, "one line pitch off"):
            anchor_axis_on_grid(
                _panel(), fit_axis_ticks(_labels(), "y", model="linear"),
                orientation="y", cross_span=SPAN, name="y", ink_threshold=200,
            )

    def test_single_level_check_is_unverified_not_verified_for_a_shift(self):
        """At the level whose evidence lacks the frame rules, the check must
        not verify the pitch-shifted mapping it used to bind."""
        true_axis = fit_axis_ticks(
            [AxisTick(f"{v:g}", v, _rule_px(v)) for v in VALUES], "y", model="linear"
        )
        check = check_served_on_grid(
            _panel(), _shifted(true_axis, -MINOR), _labels(),
            orientation="y", cross_span=SPAN, name="y", ink_threshold=200,
        )
        self.assertEqual(check.status, "unverified", check.reason)

    def test_dark_frame_control_is_unchanged(self):
        """Control: with the frame as dark as the rules nothing is missing and
        the ordinary registration binds at the first level."""
        anchored, attempt = self._anchor(_panel(frame_grey=185), _labels())
        self.assertIn("ink<200", attempt)
        for anchor in anchored.anchors:
            self.assertLess(abs(anchor.served_px - _rule_px(anchor.value)), 0.5, anchor)

    def test_whole_pitch_shift_fails_and_verdict_is_monotone(self):
        true_axis = fit_axis_ticks(
            [AxisTick(f"{v:g}", v, _rule_px(v)) for v in VALUES], "y", model="linear"
        )
        verdicts = []
        for shift in np.arange(-2.0 * PITCH, 2.0 * PITCH + 0.01, 0.5):
            check = check_served_on_grid_attempts(
                _panel(), _shifted(true_axis, float(shift)), _labels(), frame=FRAME,
                orientation="y", cross_span=SPAN, name="y",
            )
            verdicts.append((float(shift), check.status))
        passed = [s for s, v in verdicts if v != "failed"]
        self.assertTrue(passed, verdicts)
        self.assertTrue(all(v in ("verified", "failed") for _, v in verdicts), verdicts)
        # only a contiguous window around zero may verify: never a whole pitch
        self.assertLess(max(abs(s) for s in passed), 2.0, passed)


if __name__ == "__main__":
    unittest.main()
