"""served_axis_guard on the gridline_anchor synthetic panel.

Known-bad: a served axis equal to the label-centre fit -- what
_snap_axis_to_grid returns silently when it finds no nearby line -- must be
reported failed; the grid-seated axis verified; ticks that cannot be paired
one to one unverified; a unit rescale of the served values (RDS(Id) serves
mOhm from Ohm labels) must still pair label pixels by order.
"""

from __future__ import annotations

import unittest

from datasheet_chart_digitizer import served_axis_guard as sag
from datasheet_chart_digitizer.numeric_axis import AxisTick, NumericAxis

from tests.test_gridline_anchor import (
    BOTTOM,
    LEFT,
    RIGHT,
    TOP,
    _anchor_x,
    _anchor_y,
    _panel,
    _x_labels,
    _y_labels,
)


class _Plot:
    x0, y0, x1, y1 = LEFT, TOP, RIGHT, BOTTOM


class ServedAxisGuardTests(unittest.TestCase):
    def test_label_fallback_fails(self):
        checks = sag.check_axes(
            _panel(), _Plot, {"x": _x_labels(), "y": _y_labels()},
            {"x": _x_labels(), "y": _y_labels()}, "synthetic",
        )
        self.assertEqual(checks["x"].status, "failed")
        self.assertEqual(checks["y"].status, "failed")
        failed, unverified = sag.problems(checks)
        self.assertEqual(len(failed), 2)

    def test_grid_seated_axes_verify(self):
        served = {"x": _anchor_x(_panel()).axis, "y": _anchor_y(_panel()).axis}
        checks = sag.check_axes(
            _panel(), _Plot, served, {"x": _x_labels(), "y": _y_labels()}, "synthetic"
        )
        self.assertEqual({c.status for c in checks.values()}, {"verified"})
        self.assertEqual(sag.problems(checks), ([], []))

    def test_unpairable_ticks_are_unverified(self):
        served = _anchor_x(_panel()).axis
        short = NumericAxis(served.model, served.m, served.b, served.ticks[:-1], 0.0, ())
        checks = sag.check_axes(
            _panel(), _Plot, {"x": short, "y": _anchor_y(_panel()).axis},
            {"x": _x_labels(), "y": _y_labels()}, "synthetic",
        )
        self.assertEqual(checks["x"].status, "unverified")

    def test_rescaled_served_values_pair_by_order(self):
        seated = _anchor_x(_panel()).axis
        rescaled = NumericAxis(
            seated.model, seated.m * 1000.0, seated.b * 1000.0,
            tuple(AxisTick(t.text, t.value * 1000.0, t.pixel) for t in seated.ticks),
            0.0, (),
        )
        checks = sag.check_axes(
            _panel(), _Plot, {"x": rescaled, "y": _anchor_y(_panel()).axis},
            {"x": _x_labels(), "y": _y_labels()}, "synthetic",
        )
        self.assertEqual(checks["x"].status, "verified", checks["x"].reason)

    def test_not_run_is_unverified(self):
        self.assertEqual(sag.problems(None), ([], ["served axis grid check not run"]))


if __name__ == "__main__":
    unittest.main()
