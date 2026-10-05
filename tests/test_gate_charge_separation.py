"""Separating the VDD/VDS source curves of a gate-charge chart (synthetic vector pages)."""

from __future__ import annotations

import unittest


from datasheet_chart_digitizer.gate_charge_separation import plateau_run, separate_vector_curves
from gate_charge_vector_fixtures import CURVES, PLOT, RECT, _page, _stroke, _x_at


class SeparationTests(unittest.TestCase):
    def test_three_curves_each_follow_their_own_stroke(self) -> None:
        sep = separate_vector_curves(_page([_stroke(c) for c in CURVES]), RECT, 1.0, PLOT)
        self.assertEqual(len(sep.curves), 3)
        self.assertFalse(sep.refused)
        for curve, source in zip(sep.curves, CURVES):  # sorted left to right
            ends = (curve.polyline_pt[0], curve.polyline_pt[-1])
            self.assertEqual(ends, (source[0], source[-1]))

    def test_shared_part_belongs_to_every_curve(self) -> None:
        # the shared rise + plateau drawn once, branches drawn separately
        shared = [(0, 400), (100, 250), (130, 250), (160, 250), (190, 250)]
        drawings = [_stroke(shared)] + [_stroke([(x, 250), (x + 170, 20)]) for x in (130, 160, 190)]
        sep = separate_vector_curves(_page(drawings), RECT, 1.0, PLOT)
        self.assertEqual(len(sep.curves), 3)
        for curve in sep.curves:
            self.assertEqual(curve.polyline_pt[0], (0, 400))
            self.assertIn((100, 250), curve.polyline_pt)

    def test_curves_that_rejoin_are_split_by_pen_strokes(self) -> None:
        # IPB180N04S4: the 32 V rise ends on the 8 V plateau -> the endpoint
        # graph rejoins; each curve is still one pen stroke in content order
        a = [(0, 400), (90, 250), (130, 250), (300, 20)]
        b = [(0, 400), (100, 250), (150, 250), (320, 20)]
        drawings = [_stroke(a[:2]), _stroke(a[1:]), _stroke(b[:2]), _stroke(b[1:])]
        sep = separate_vector_curves(_page(drawings), RECT, 1.0, PLOT)
        self.assertEqual(sep.method, "vector_pen_strokes")
        self.assertEqual(len(sep.curves), 2)
        self.assertEqual(sep.curves[0].polyline_pt[-1], (300, 20))
        self.assertEqual(sep.curves[1].polyline_pt[-1], (320, 20))

    def test_strokes_closer_than_a_stroke_width_are_refused(self) -> None:
        # 1.2 pt apart at 1 pt width: no visible gap, never two VDD curves
        a = CURVES[0]
        b = [(x + 1.2, y) for x, y in a]
        sep = separate_vector_curves(_page([_stroke(a, 1.0), _stroke(b, 1.0)]), RECT, 1.0, PLOT)
        self.assertTrue(sep.refused)
        self.assertEqual(sep.curves, [])

    def test_dark_filled_outline_is_not_a_centreline(self) -> None:
        # FDP16AN08A0: a thick curve drawn as a dark-filled outline shape
        a = CURVES[0]
        outline = a + [(x + 1.5, y) for x, y in reversed(a)] + [a[0]]
        shape = dict(_stroke(outline, 0.7), fill=(0.2, 0.2, 0.2), type="fs")
        sep = separate_vector_curves(_page([shape]), RECT, 1.0, PLOT)
        self.assertEqual(sep.curves, [])

    def test_a_curve_drawn_twice_is_one_curve(self) -> None:
        sep = separate_vector_curves(_page([_stroke(CURVES[0]), _stroke(CURVES[0]), _stroke(CURVES[2])]), RECT, 1.0, PLOT)
        self.assertEqual(len(sep.curves), 2)

    def test_a_fragment_that_stops_low_is_not_a_curve(self) -> None:
        stub = [(0, 400), (100, 250), (200, 250), (260, 230)]
        sep = separate_vector_curves(_page([_stroke(CURVES[0]), _stroke(stub)]), RECT, 1.0, PLOT)
        self.assertEqual(len(sep.curves), 1)

    def test_curve_weight_leader_is_not_a_curve(self) -> None:
        # FDS8447: a curve-weight shaft from a point on a curve to a label
        x = _x_at(CURVES[0], 150)
        leader = _stroke([(x, 150), (x + 40, 120)], width=2.0)
        sep = separate_vector_curves(_page([_stroke(c) for c in CURVES] + [leader]), RECT, 1.0, PLOT)
        self.assertEqual(len(sep.curves), 3)

    def test_plateau_run(self) -> None:
        points = [(x, 400 - 1.5 * x) for x in range(100)] + [(x, 250) for x in range(100, 160)] + \
                 [(x, 250 - (x - 160)) for x in range(160, 300)]
        x0, x1, y = plateau_run(points)
        self.assertEqual((x0, y), (100, 250))
        self.assertIn(x1, (160, 161))  # 1 px flatness tolerance

    def test_coloured_curves_are_curve_ink(self) -> None:
        drawings = [dict(_stroke(c), color=col) for c, col in zip(CURVES, ((0.8, 0.1, 0.1), (0.1, 0.3, 0.8), (0.1, 0.6, 0.2)))]
        sep = separate_vector_curves(_page(drawings), RECT, 1.0, PLOT)
        self.assertEqual(len(sep.curves), 3)

    def test_dense_polyline_needs_no_recursion_and_keeps_its_style(self) -> None:
        # 3000 tiny segments per curve (matplotlib-style output)
        dense = []
        for curve in CURVES:
            points = []
            for (ax, ay), (bx, by) in zip(curve, curve[1:]):
                points += [(ax + (bx - ax) * k / 1000, ay + (by - ay) * k / 1000) for k in range(1000)]
            dense.append(points + [curve[-1]])
        drawings = [_stroke(dense[0]), _stroke(dense[1], dashes="[ 5 2 ] 0"), _stroke(dense[2], dashes="[ 1 1 ] 0")]
        sep = separate_vector_curves(_page(drawings), RECT, 1.0, PLOT)
        self.assertEqual(len(sep.curves), 3)
        self.assertEqual([c.style[0] for c in sep.curves], ["solid", "[ 5 2 ] 0", "[ 1 1 ] 0"])


if __name__ == "__main__":
    unittest.main()
