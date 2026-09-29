"""Known-bad calibration tests for gridline anchoring.

Every panel here is synthetic and CONSTRUCTS the failure the anchor exists to
catch: tick labels whose glyph centres are offset from the gridlines they
label. A label-centre fit on these panels misses the grid while its own
residual (measured against the labels) looks fine; the anchored calibration
must land on the grid, or refuse.
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from datasheet_chart_digitizer.gridline_anchor import (
    anchor_axis_on_grid,
    detect_axis_lines,
    served_pixel,
)
from datasheet_chart_digitizer.numeric_axis import AxisTick, fit_axis_ticks

HEIGHT, WIDTH = 900, 1000
# Plot frame (centre of a 5 px rule), like a Vishay panel at 400 dpi.
LEFT, RIGHT, TOP, BOTTOM = 150, 870, 100, 800
X_VALUES = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
Y_DECADES = [1000.0, 100.0, 10.0, 1.0, 0.1]


def _x_grid(value: float) -> float:
    return LEFT + (RIGHT - LEFT) * value / 30.0


def _y_grid(value: float) -> float:
    """Pixel of a log value; 1000 at TOP, 0.1 at BOTTOM (4 decades)."""
    return TOP + (BOTTOM - TOP) * (3.0 - math.log10(value)) / 4.0


def _vline(image, x: float, width: int = 2, y0: int = TOP, y1: int = BOTTOM) -> None:
    start = int(round(x - (width - 1) / 2))
    image[y0 : y1 + 1, start : start + width] = 0


def _hline(image, y: float, width: int = 2, x0: int = LEFT, x1: int = RIGHT) -> None:
    start = int(round(y - (width - 1) / 2))
    image[start : start + width, x0 : x1 + 1] = 0


def _panel(*, x_grid=True, minors=True, skip_x=(), skip_decades=(), nudge_x=None):
    """White canvas, 5 px frame, linear x gridlines and log y grid with minors."""
    image = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    _vline(image, LEFT, 5)
    _vline(image, RIGHT, 5)
    _hline(image, TOP, 5)
    _hline(image, BOTTOM, 5)
    if x_grid:
        for value in X_VALUES[1:-1]:
            if value in skip_x:
                continue
            shift = (nudge_x or {}).get(value, 0.0)
            _vline(image, _x_grid(value) + shift)
    for decade in Y_DECADES[1:]:
        if decade not in skip_decades:
            _hline(image, _y_grid(decade))
        if minors:
            for j in range(2, 10):
                _hline(image, _y_grid(decade * j))
    return image


def _label_axis(values, pixels, model):
    ticks = [AxisTick(f"{v:g}", v, float(p)) for v, p in zip(values, pixels)]
    return fit_axis_ticks(ticks, "labels", model=model)


# Label glyph offsets from the gridline each labels, in px. The 25 V label is
# 12 px right of its line, as measured on BAT54W-G; the others wander.
X_LABEL_OFFSETS = [-3.0, -4.0, 3.0, 1.0, 4.0, 12.0, 1.0]
# Decade labels sit 5.5 px BELOW their rule -- closer to the 90 % minor line
# (0.046 decade = 8.0 px away here) than a nearest-line search can tell apart.
Y_LABEL_OFFSETS = [4.5, 5.5, 5.5, 5.0, -1.5]


def _x_labels():
    return _label_axis(
        X_VALUES,
        [_x_grid(v) + d for v, d in zip(X_VALUES, X_LABEL_OFFSETS)],
        "linear",
    )


def _y_labels():
    return _label_axis(
        Y_DECADES,
        [_y_grid(v) + d for v, d in zip(Y_DECADES, Y_LABEL_OFFSETS)],
        "log10",
    )


def _anchor_x(image):
    return anchor_axis_on_grid(
        image, _x_labels(), orientation="x", cross_span=(TOP + 4, BOTTOM - 1), name="X"
    )


def _anchor_y(image):
    return anchor_axis_on_grid(
        image, _y_labels(), orientation="y", cross_span=(LEFT - 3, RIGHT + 1), name="Y"
    )


class LabelOffsetFromGridTests(unittest.TestCase):
    def test_label_centre_fit_misses_the_grid(self):
        """Calibration of the known-bad input: the label fit IS wrong here.

        Without this the anchor tests below could pass on a panel where labels
        and gridlines coincide, which would prove nothing.
        """
        axis = _x_labels()
        misses = [abs(served_pixel(axis, v) - _x_grid(v)) for v in X_VALUES]
        self.assertGreater(max(misses), 3.0)

    def test_x_ticks_calibrate_onto_gridlines_not_labels(self):
        anchored = _anchor_x(_panel())
        for anchor in anchored.anchors:
            with self.subTest(value=anchor.value):
                self.assertLessEqual(abs(anchor.line_px - _x_grid(anchor.value)), 0.5)
                self.assertLessEqual(
                    abs(served_pixel(anchored.axis, anchor.value) - _x_grid(anchor.value)),
                    0.6,
                )
        # the 25 V label's 12 px offset is recorded, not absorbed
        by_value = {anchor.value: anchor for anchor in anchored.anchors}
        self.assertAlmostEqual(by_value[25.0].label_offset_px, 12.0, delta=0.6)
        # residual is measured against the gridlines, not the labels
        self.assertLess(anchored.axis.residual_px, 0.5)

    def test_frame_is_anchored_at_its_centre(self):
        anchored = _anchor_x(_panel())
        by_value = {anchor.value: anchor for anchor in anchored.anchors}
        self.assertAlmostEqual(by_value[0.0].line_px, LEFT, delta=0.5)
        self.assertAlmostEqual(by_value[30.0].line_px, RIGHT, delta=0.5)

    def test_log_decades_are_not_substituted_by_adjacent_minor(self):
        """The 90 % minor line sits nearer the label than the decade does."""
        image = _panel()
        labels = _y_labels()
        label_100 = next(t.pixel for t in labels.ticks if t.value == 100.0)
        minor_90 = _y_grid(90.0)
        self.assertLess(abs(label_100 - minor_90), abs(label_100 - _y_grid(100.0)))

        anchored = _anchor_y(image)
        for anchor in anchored.anchors:
            with self.subTest(value=anchor.value):
                self.assertLessEqual(abs(anchor.line_px - _y_grid(anchor.value)), 0.5)
                self.assertLessEqual(abs(anchor.served_error_px), anchored.tolerance_px)

    def test_interior_decades_are_registered_by_their_minor_pattern(self):
        """No frame endpoints to pin the registration: only the minors decide.

        Labels for 100/10/1 only, each 5.5 px below its rule. Seating all three
        on their 90 % minors is an equally straight, equally spaced sequence
        that is CLOSER to the labels; only the predicted-minor pattern tells
        the decades apart.
        """
        values = [100.0, 10.0, 1.0]
        axis = _label_axis(values, [_y_grid(v) + 5.5 for v in values], "log10")
        anchored = anchor_axis_on_grid(
            _panel(), axis, orientation="y", cross_span=(LEFT - 3, RIGHT + 1), name="Y"
        )
        for anchor in anchored.anchors:
            with self.subTest(value=anchor.value):
                self.assertLessEqual(abs(anchor.line_px - _y_grid(anchor.value)), 0.5)

    def test_log_decades_anchor_without_minor_lines(self):
        anchored = _anchor_y(_panel(minors=False))
        for anchor in anchored.anchors:
            self.assertLessEqual(abs(anchor.line_px - _y_grid(anchor.value)), 0.5)

    def test_tick_marks_anchor_a_grid_free_axis(self):
        """No x gridlines: the ticks hang off the bottom frame rule."""
        image = _panel(x_grid=False)
        for value in X_VALUES[1:-1]:
            _vline(image, _x_grid(value), 2, BOTTOM - 15, BOTTOM)
        anchored = _anchor_x(image)
        sources = {anchor.value: anchor.source for anchor in anchored.anchors}
        self.assertEqual(sources[15.0], "tick_mark")
        for anchor in anchored.anchors:
            self.assertLessEqual(abs(anchor.line_px - _x_grid(anchor.value)), 0.5)


class FailClosedTests(unittest.TestCase):
    def test_label_without_gridline_is_refused(self):
        with self.assertRaises(RuntimeError) as raised:
            _anchor_x(_panel(skip_x=(15.0,)))
        self.assertIn("'15'", str(raised.exception))
        self.assertIn("no gridline or tick mark", str(raised.exception))

    def test_missing_decade_rule_is_not_replaced_by_its_minor(self):
        """The 10 decade rule is absent but its 90/20 minors are present.

        A nearest-line search would seat "10" on the 9 minor 0.046 decade away
        and serve a calibration 8 px off. The registration must refuse instead.
        """
        with self.assertRaises(RuntimeError) as raised:
            _anchor_y(_panel(skip_decades=(10.0,)))
        self.assertIn("labels and the grid disagree", str(raised.exception))

    def test_irregular_grid_is_refused(self):
        """A gridline 6 px off the linear sequence cannot be served through."""
        with self.assertRaises(RuntimeError):
            _anchor_x(_panel(nudge_x={15.0: 6.0}))

    def test_label_halfway_between_dense_lines_is_ambiguous(self):
        """A 1 V grid under 5 V labels offset ~half a pitch: refuse, don't guess.

        Interior labels only, so no frame pins the registration: seating every
        label one 1 V line left or right is equally consistent with the grid.
        """
        image = _panel(x_grid=False, minors=False)
        for volts in range(1, 30):
            _vline(image, _x_grid(float(volts)))
        pitch_1v = _x_grid(1.0) - _x_grid(0.0)
        interior = X_VALUES[1:-1]
        axis = _label_axis(
            interior, [_x_grid(v) + 0.45 * pitch_1v for v in interior], "linear"
        )
        with self.assertRaises(RuntimeError) as raised:
            anchor_axis_on_grid(
                image, axis, orientation="x", cross_span=(TOP + 4, BOTTOM - 1), name="X"
            )
        self.assertIn("equally well", str(raised.exception))

    def test_served_mapping_that_misses_a_line_is_refused(self):
        """The final assertion checks the SERVED mapping, whatever produced it.

        Registration normally keeps the re-fit inside tolerance, so the
        backstop is exercised by substituting a fit shifted 3 px off the grid.
        """
        from unittest import mock

        from datasheet_chart_digitizer import gridline_anchor

        real_fit = gridline_anchor.fit_axis_ticks

        def shifted_fit(ticks, name, *, model):
            axis = real_fit(ticks, name, model=model)
            return type(axis)(
                axis.model, axis.m, axis.b + 3.0 * axis.m, axis.ticks,
                axis.residual_px, axis.candidate_residuals_px,
            )

        with mock.patch.object(gridline_anchor, "fit_axis_ticks", shifted_fit):
            with self.assertRaises(RuntimeError) as raised:
                _anchor_x(_panel())
        self.assertIn("served calibration misses", str(raised.exception))

    def test_far_tail_offset_is_refused_not_snapped(self):
        """Monotonicity: labels far from every line must not find a match."""
        axis = _label_axis(
            X_VALUES, [_x_grid(v) + 60.0 for v in X_VALUES], "linear"
        )
        with self.assertRaises(RuntimeError):
            anchor_axis_on_grid(
                _panel(), axis, orientation="x", cross_span=(TOP + 4, BOTTOM - 1), name="X"
            )


class LineDetectorTests(unittest.TestCase):
    def test_curves_and_text_are_not_gridlines(self):
        image = _panel(x_grid=False, minors=False)
        # a steep diagonal curve and a text-like blob inside the plot
        for x in range(300, 400):
            image[400 + (x - 300) * 3 : 403 + (x - 300) * 3, x] = 0
        image[300:330, 500:540] = 0
        lines = detect_axis_lines(
            image,
            orientation="x",
            along=(LEFT - 20, RIGHT + 20),
            cross_span=(TOP + 4, BOTTOM - 1),
            max_width=10,
            tick_band=6,
        )
        self.assertEqual([round(line.center_px) for line in lines], [LEFT, RIGHT])



class LightRuleInkThresholdTests(unittest.TestCase):
    """A hairline rule anti-aliased to ~212 grey is ink only above the default.

    NCEP050N12D's 10 pF frame rule renders at 212/220 on two rows at 180 dpi;
    with the default ink threshold (200) that decade has no line, so the axis
    refuses. Raising ``ink_threshold`` for such a chart must find it, and the
    default must keep refusing (the parameter is opt-in).
    """

    def _light_x_panel(self):
        image = _panel()
        for value in X_VALUES[1:-1]:
            start = int(round(_x_grid(value) - 0.5))
            image[TOP:BOTTOM + 1, start:start + 2] = 255
            image[TOP:BOTTOM + 1, int(round(_x_grid(value)))] = 212
        return image

    def test_default_threshold_refuses_the_light_rules(self):
        with self.assertRaises(RuntimeError):
            _anchor_x(self._light_x_panel())

    def test_raised_threshold_anchors_on_them(self):
        anchored = anchor_axis_on_grid(
            self._light_x_panel(), _x_labels(), orientation="x", cross_span=(TOP + 4, BOTTOM - 1),
            name="X", ink_threshold=235,
        )
        for value in X_VALUES[1:-1]:
            self.assertAlmostEqual(served_pixel(anchored.axis, value), round(_x_grid(value)), delta=0.6)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
