"""A gate-charge trace between two source strokes is never served (bench round 1, bug 4).

IPB180N04S4 p7 Figure 15 (VDD = 8 V and 32 V, thick strokes sharing the initial
ramp) was served ``ok`` as the per-column median of both curves: a line on
neither of them.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from datasheet_chart_digitizer.gate_charge import CURVE_BLOCKING_DIAGNOSTICS, digitize_gate_charge
from datasheet_chart_digitizer.gate_charge_blend import BLEND_DIAGNOSTIC, served_curve_blend

IPB180 = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon/IPB180N04S401ATMA1.pdf")
ISC040 = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon/ISC040N10NM8.pdf")


def _pt(x, y):
    return SimpleNamespace(x=x, y=y)


def _page(*polylines, width=2.0):
    drawings = []
    for line in polylines:
        items = [("l", _pt(*a), _pt(*b)) for a, b in zip(line, line[1:])]
        drawings.append({"type": "s", "color": (0, 0, 0), "width": width, "items": items})
    return SimpleNamespace(get_drawings=lambda: drawings)


RECT = SimpleNamespace(x0=0.0, y0=0.0)
PLOT = (0, 0, 400, 400)
LOWER = [(0, 400), (100, 250), (200, 250), (400, 50)]
UPPER = [(0, 400), (90, 250), (180, 250), (380, 50)]  # second VDD curve


def _along(polyline, shift=0.0, n=300):
    points = []
    for (ax, ay), (bx, by) in zip(polyline, polyline[1:]):
        for i in range(n // 3):
            t = i / (n // 3)
            points.append((ax + t * (bx - ax) + shift, ay + t * (by - ay)))
    return points


class BlendVerdictTests(unittest.TestCase):
    def test_served_curve_on_one_stroke_is_not_a_blend(self) -> None:
        verdict = served_curve_blend(_page(LOWER, UPPER), RECT, 1.0, PLOT, _along(LOWER))
        self.assertIsNotNone(verdict)
        self.assertFalse(verdict.blended)

    def test_midline_of_two_strokes_is_a_blend(self) -> None:
        midline = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(LOWER, UPPER)]
        verdict = served_curve_blend(_page(LOWER, UPPER), RECT, 1.0, PLOT, _along(midline))
        self.assertTrue(verdict.blended)

    def test_off_stroke_fraction_grows_with_the_offset(self) -> None:
        fractions = [
            served_curve_blend(_page(LOWER), RECT, 1.0, PLOT, _along(LOWER, shift)).off_fraction
            for shift in (0.0, 1.5, 3.0, 6.0)
        ]
        self.assertEqual(fractions, sorted(fractions))
        self.assertEqual(fractions[0], 0.0)

    def test_far_off_curve_still_scores_as_a_blend(self) -> None:
        # monotone: a trace nowhere near the strokes is not "not applicable"
        verdict = served_curve_blend(_page(LOWER), RECT, 1.0, PLOT, _along(LOWER, 40.0))
        self.assertTrue(verdict.blended)

    def test_no_vector_strokes_is_not_applicable(self) -> None:
        self.assertIsNone(served_curve_blend(_page(), RECT, 1.0, PLOT, _along(LOWER)))

    def test_blend_blocks_axis_repair_from_restoring_ok(self) -> None:
        self.assertIn(BLEND_DIAGNOSTIC, CURVE_BLOCKING_DIAGNOSTICS)


class GateChargeBlendEndToEndTests(unittest.TestCase):
    def test_ipb180_two_vdd_midline_is_not_served(self) -> None:
        if not IPB180.exists():
            self.skipTest(f"missing local corpus fixture: {IPB180}")
        results = [r for r in digitize_gate_charge(IPB180, dpi=220, finder_dpi=220)
                   if (r.panel.page, r.panel.diagram) == (7, 15)]
        self.assertEqual(len(results), 1)
        self.assertNotEqual(results[0].status, "ok")
        self.assertIn(BLEND_DIAGNOSTIC, results[0].diagnostics)

    def test_three_curve_median_on_a_stroke_stays_served(self) -> None:
        # ISC040N10NM8 Figure 15 (20/50/80 V): the median of three strokes
        # lies on a stroke; this check alone does not refuse it.
        if not ISC040.exists():
            self.skipTest(f"missing local corpus fixture: {ISC040}")
        results = [r for r in digitize_gate_charge(ISC040, dpi=220, finder_dpi=220)
                   if (r.panel.page, r.panel.diagram) == (10, 15)]
        self.assertEqual(len(results), 1)
        self.assertNotIn(BLEND_DIAGNOSTIC, results[0].diagnostics)


if __name__ == "__main__":
    unittest.main()
