"""Calibration of the served-mapping grid guard (``check_served_on_grid``).

The guard must FAIL a label-centre calibration, PASS the gridline-anchored
one, never report an unevaluable axis as passing, and never flip back to
PASS as the served offset grows -- including a whole-pitch shift that lands
every served tick exactly on a NEIGHBOURING gridline.
"""

from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer.gridline_anchor import (
    anchor_axis_on_grid,
    check_served_on_grid,
    served_pixel,
)
from datasheet_chart_digitizer.numeric_axis import AxisTick, NumericAxis

from tests.test_gridline_anchor import (
    BOTTOM,
    HEIGHT,
    LEFT,
    RIGHT,
    TOP,
    WIDTH,
    X_VALUES,
    Y_DECADES,
    _anchor_x,
    _hline,
    _panel,
    _vline,
    _x_grid,
    _x_labels,
    _y_grid,
    _y_labels,
)

X_SPAN = (TOP + 4, BOTTOM - 1)
Y_SPAN = (LEFT - 3, RIGHT + 1)


def _shifted(axis: NumericAxis, shift_px: float) -> NumericAxis:
    """The same mapping with every served pixel moved by *shift_px*."""
    return NumericAxis(axis.model, axis.m, axis.b - axis.m * shift_px, (), 0.0, ())


def _check_x(image, served, labels=None, **kwargs):
    return check_served_on_grid(
        image, served, (labels or _x_labels()).ticks,
        orientation="x", cross_span=X_SPAN, name="X", **kwargs,
    )


class KnownBadCalibrationTests(unittest.TestCase):
    def test_label_centre_calibration_fails(self):
        """The defect itself: a fit to label glyph centres (25 V +12 px)."""
        label_fit = _x_labels()
        check = _check_x(_panel(), label_fit)
        self.assertEqual(check.status, "failed", check.reason)
        self.assertGreater(check.max_abs_error_px, check.tolerance_px)
        with self.assertRaises(RuntimeError):
            check.require("X")

    def test_label_centre_log_calibration_fails(self):
        """Decade labels 5.5 px off their rules: the log fit misses the grid."""
        check = check_served_on_grid(
            _panel(), _y_labels(), _y_labels().ticks,
            orientation="y", cross_span=Y_SPAN, name="Y",
        )
        self.assertEqual(check.status, "failed", check.reason)

    def test_gridline_anchored_calibration_passes(self):
        anchored = _anchor_x(_panel())
        check = _check_x(_panel(), anchored.axis)
        self.assertEqual(check.status, "verified", check.reason)
        self.assertLessEqual(check.max_abs_error_px, 0.6)
        self.assertIs(check.require("X"), check)

    def test_identity_is_from_labels_not_from_the_served_mapping(self):
        """A mapping shifted by exactly one gridline pitch puts every served
        tick ON a gridline -- the neighbour's. It must still fail."""
        anchored = _anchor_x(_panel())
        pitch = _x_grid(5.0) - _x_grid(0.0)
        for shift in (pitch, -pitch, 2 * pitch):
            with self.subTest(shift=shift):
                check = _check_x(_panel(), _shifted(anchored.axis, shift))
                self.assertEqual(check.status, "failed", check.reason)


class MonotonicityTests(unittest.TestCase):
    def test_verdict_never_returns_to_pass_as_offset_grows(self):
        anchored = _anchor_x(_panel())
        pitch = _x_grid(5.0) - _x_grid(0.0)
        verdicts = []
        for shift in np.arange(0.0, 3.0 * pitch, 0.25):
            verdicts.append(_check_x(_panel(), _shifted(anchored.axis, float(shift))).status)
        first_fail = verdicts.index("failed")
        self.assertGreater(first_fail, 0)  # small shifts are within tolerance
        self.assertTrue(all(v == "failed" for v in verdicts[first_fail:]), verdicts)

    def test_scale_error_grows_monotonically_too(self):
        anchored = _anchor_x(_panel())
        verdicts = []
        for stretch in np.linspace(1.0, 1.5, 51):
            axis = NumericAxis("linear", anchored.axis.m / stretch, anchored.axis.b, (), 0.0, ())
            # pin the 0 V end on its line so only the scale error grows
            axis = NumericAxis(
                "linear", axis.m, -axis.m * served_pixel(anchored.axis, 0.0), (), 0.0, ()
            )
            verdicts.append(_check_x(_panel(), axis).status)
        first_fail = verdicts.index("failed")
        self.assertTrue(all(v == "failed" for v in verdicts[first_fail:]), verdicts)


class UnevaluableInputTests(unittest.TestCase):
    def test_no_gridlines_is_unverified_not_pass(self):
        blank = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
        check = _check_x(blank, _anchor_x(_panel()).axis)
        self.assertEqual(check.status, "unverified")
        self.assertIsNone(check.max_abs_error_px)
        with self.assertRaises(RuntimeError):
            check.require("X")

    def test_single_tick_is_unverified(self):
        one = (AxisTick("10", 10.0, _x_grid(10.0)),)
        check = check_served_on_grid(
            _panel(), _anchor_x(_panel()).axis, one,
            orientation="x", cross_span=X_SPAN, name="X",
        )
        self.assertEqual(check.status, "unverified")

    def test_label_without_line_is_unverified(self):
        check = _check_x(_panel(skip_x=(15.0,)), _anchor_x(_panel()).axis)
        self.assertEqual(check.status, "unverified")
        self.assertIn("no gridline", check.reason)

    def test_unplaceable_log_value_is_unverified(self):
        axis = NumericAxis("log10", 0.0, 1.0, (), 0.0, ())
        check = check_served_on_grid(
            _panel(), axis, _y_labels().ticks, orientation="y", cross_span=Y_SPAN, name="Y"
        )
        self.assertNotEqual(check.status, "verified")


def _dual_axis_panel():
    """Left axis 0..30 on 7 gridlines; right axis 0..5 in 0.5 steps -- its
    interior labels sit BETWEEN the gridlines and have no tick marks (AO
    reverse-recovery softness axis)."""
    image = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    _vline(image, LEFT, 5)
    _vline(image, RIGHT, 5)
    _hline(image, TOP, 5)
    _hline(image, BOTTOM, 5)
    for k in range(1, 6):
        _hline(image, TOP + (BOTTOM - TOP) * k / 6)
    return image


def _right_labels(offset=-2.0):
    # -2 px: every interior half-step label stays >0.3 label pitch from the
    # nearest gridline, so only 0, 2.5 and 5 have a line under them.
    values = [v * 0.5 for v in range(11)]
    return [AxisTick(f"{v:g}", v, BOTTOM - (BOTTOM - TOP) * v / 5.0 + offset) for v in values]


class IdentityOnlyLabelTests(unittest.TestCase):
    def _label_axis(self, ticks):
        from datasheet_chart_digitizer.numeric_axis import fit_axis_ticks

        return fit_axis_ticks(ticks, "right", model="linear")

    def test_half_step_labels_refuse_by_default(self):
        with self.assertRaises(RuntimeError):
            anchor_axis_on_grid(
                _dual_axis_panel(), self._label_axis(_right_labels()),
                orientation="y", cross_span=Y_SPAN, name="right",
            )

    def test_identity_only_anchors_on_the_lined_ticks(self):
        anchored = anchor_axis_on_grid(
            _dual_axis_panel(), self._label_axis(_right_labels()),
            orientation="y", cross_span=Y_SPAN, name="right",
            unlined_labels="identity_only",
        )
        self.assertEqual({a.value for a in anchored.anchors}, {0.0, 2.5, 5.0})
        self.assertAlmostEqual(served_pixel(anchored.axis, 0.0), BOTTOM, delta=0.6)
        self.assertAlmostEqual(served_pixel(anchored.axis, 5.0), TOP, delta=0.6)
        self.assertTrue(anchored.unlined)
        # the label-centre fit of the same labels fails the guard
        check = check_served_on_grid(
            _dual_axis_panel(), self._label_axis(_right_labels()), _right_labels(),
            orientation="y", cross_span=Y_SPAN, name="right", unlined_labels="identity_only",
        )
        self.assertEqual(check.status, "failed", check.reason)

    def test_interior_label_near_an_unrelated_line_is_identity_only(self):
        """+2.5 px glyph offset puts the 1.0 label 0.3 pitch from the 0.833
        gridline. Proximity alone would bind it there and break the
        registration; the end-label hypothesis leaves it identity-only."""
        ticks = _right_labels(offset=2.5)
        anchored = anchor_axis_on_grid(
            _dual_axis_panel(), self._label_axis(ticks), orientation="y",
            cross_span=Y_SPAN, name="right", unlined_labels="identity_only",
        )
        self.assertEqual({a.value for a in anchored.anchors}, {0.0, 2.5, 5.0})
        self.assertIn(1.0, {t.value for t in anchored.unlined})

    def test_identity_only_label_far_from_its_value_refuses(self):
        """An unlined interior label must still sit where its value belongs."""
        ticks = _right_labels()
        ticks[3] = AxisTick(ticks[3].text, ticks[3].value, ticks[3].pixel + 30.0)
        with self.assertRaises(RuntimeError):
            anchor_axis_on_grid(
                _dual_axis_panel(),
                # the displaced glyph would fail a residual-gated label fit, so
                # hand the ticks over directly: identity is what is tested here
                NumericAxis("linear", -5.0 / 700, 5.0 * BOTTOM / 700, tuple(ticks), 0.0, ()),
                orientation="y", cross_span=Y_SPAN, name="right",
                unlined_labels="identity_only",
            )

    def test_end_label_without_line_still_refuses(self):
        ticks = _right_labels()
        # drop the frame rule under the top (5.0) label
        image = _dual_axis_panel()
        image[TOP - 3 : TOP + 3, :] = 255
        with self.assertRaises(RuntimeError):
            anchor_axis_on_grid(
                image, self._label_axis(ticks), orientation="y", cross_span=Y_SPAN,
                name="right", unlined_labels="identity_only",
            )


if __name__ == "__main__":
    unittest.main()
