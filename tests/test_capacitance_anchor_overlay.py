"""Anchors are drawn on every capacitance overlay, or explicitly reported as not drawn.

The anchor residual is the number a reviewer trusts, but it is a scalar: it
cannot show WHERE on the chart the comparison happened or how much axis room
was left there. SUP70060E's Crss residual of +1.4% looked like agreement while
the trace sat 3 px off the frame. Drawing the anchor makes the comparison
inspectable.

The failure mode this guards is the usual one: an anchor that cannot be placed
must not simply be absent, because an absent marker reads exactly like a chart
that carried no anchor at all -- the reading under which a bad comparison
passes review unseen.
"""
from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer.capacitance_overlay import draw_trace_overlay
from datasheet_chart_digitizer.capacitance_types import (
    AxisCalibration,
    CapAnchor,
    PlotBox,
    Trace,
)

PLOT = PlotBox(x0=10, y0=10, x1=190, y1=140)

# A linear pF ladder, the axis type the resolution problem lives on.
LINEAR = AxisCalibration(
    x_min_v=0.0, x_max_v=100.0, y_min_decade=0.0, y_max_decade=3.5,
    source="test", x_ticks_v=(0.0, 50.0, 100.0), y_decades=(),
    y_log=False, y_scale=-19.21, y_offset=2500.0,
    x_scale=0.5556, x_offset=-5.556,
)


def _blank() -> np.ndarray:
    return np.full((150, 200, 3), 255, dtype=np.uint8)


def _trace(name: str = "Crss") -> Trace:
    return Trace(name=name, area=2, bbox=(0, 0, 100, 1),
                 points=[(20, 120), (180, 130)])


def _painted(overlay: np.ndarray) -> int:
    return int((overlay != 255).any(axis=2).sum())


class AnchorsAreAlwaysAccountedFor(unittest.TestCase):
    def test_a_placeable_anchor_is_drawn(self) -> None:
        anchors = {"Crss": CapAnchor(name="Crss", value_pf=950.0, vds_v=50.0)}
        without = draw_trace_overlay(_blank(), PLOT, [_trace()], LINEAR)
        with_anchor = draw_trace_overlay(
            _blank(), PLOT, [_trace()], LINEAR, None, anchors)
        self.assertGreater(_painted(with_anchor), _painted(without),
                           "the anchor added no ink to the overlay")

    def test_an_untrusted_axis_reports_the_anchor_instead_of_dropping_it(self) -> None:
        # calibration=None is how an untrusted axis reaches the renderer. The
        # anchor cannot be positioned -- but silence would claim there was none.
        anchors = {"Coss": CapAnchor(name="Coss", value_pf=1395.0, vds_v=50.0)}
        without = draw_trace_overlay(_blank(), PLOT, [_trace("Coss")], None)
        with_anchor = draw_trace_overlay(
            _blank(), PLOT, [_trace("Coss")], None, None, anchors)
        self.assertGreater(_painted(with_anchor), _painted(without),
                           "an unplaceable anchor vanished silently")

    def test_an_off_chart_anchor_is_reported(self) -> None:
        # 5000 pF is above the top of this 2500 pF axis: it has no position on
        # the plot, and that is itself worth seeing.
        anchors = {"Ciss": CapAnchor(name="Ciss", value_pf=5000.0, vds_v=50.0)}
        without = draw_trace_overlay(_blank(), PLOT, [_trace("Ciss")], LINEAR)
        with_anchor = draw_trace_overlay(
            _blank(), PLOT, [_trace("Ciss")], LINEAR, None, anchors)
        self.assertGreater(_painted(with_anchor), _painted(without),
                           "an off-chart anchor vanished silently")

    def test_a_non_positive_anchor_is_reported(self) -> None:
        anchors = {"Crss": CapAnchor(name="Crss", value_pf=0.0, vds_v=50.0)}
        without = draw_trace_overlay(_blank(), PLOT, [_trace()], LINEAR)
        with_anchor = draw_trace_overlay(
            _blank(), PLOT, [_trace()], LINEAR, None, anchors)
        self.assertGreater(_painted(with_anchor), _painted(without),
                           "an unusable anchor value vanished silently")

    def test_no_anchors_supplied_draws_nothing_extra(self) -> None:
        # The renderer must not invent a marker or a note when the caller had
        # no anchors to give it.
        baseline = draw_trace_overlay(_blank(), PLOT, [_trace()], LINEAR)
        for empty in (None, {}):
            self.assertEqual(
                _painted(baseline),
                _painted(draw_trace_overlay(
                    _blank(), PLOT, [_trace()], LINEAR, None, empty)))

    def test_the_marker_lands_on_the_anchor_coordinates(self) -> None:
        # 950 pF on this axis is (2500-950)/19.21 = 80.7 px from the top of the
        # crop; 50 V is x = 50*0.5556 - 5.556 -> 22.2... solved through the same
        # calibration the traces use, so a marker that drifts from the curve
        # would be the calibration's drift, not the marker's.
        from datasheet_chart_digitizer.capacitance_axis import (
            calibration_x_of_v, calibration_y_of_log_c,
        )
        x = int(round(calibration_x_of_v(LINEAR, PLOT, 50.0)))
        y = int(round(calibration_y_of_log_c(LINEAR, PLOT, np.log10(950.0))))
        overlay = draw_trace_overlay(
            _blank(), PLOT, [_trace()], LINEAR, None,
            {"Crss": CapAnchor(name="Crss", value_pf=950.0, vds_v=50.0)})
        window = overlay[max(0, y - 9):y + 10, max(0, x - 9):x + 10]
        self.assertTrue((window != 255).any(),
                        "no marker ink within 9 px of the anchor position")


if __name__ == "__main__":
    unittest.main()
