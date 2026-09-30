"""A served VGS(Qg) curve follows ONE VDD branch.

Found by the synthetic set1 benchmark (2026-09-30): on multi-VDD gate-charge
charts the column tracer left its branch wherever the branch was missing from
the mask (an erased in-plot label, a dash gap, the branch's own end) and
carried on along a neighbour. Ten charts were served "ok" with in-range p95
2.4-6.5 % of span, and on syn1_0025 the hop's back-and-forth faked a plateau:
Vpl 6.8 V served for a 4.44 V plateau.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from datasheet_chart_digitizer import gate_charge as gate
from datasheet_chart_digitizer import gate_charge_trace as trace
from datasheet_chart_digitizer import gate_charge_branch as branch
from datasheet_chart_digitizer.gate_charge_branch import BRANCH_HOP_DIAGNOSTIC, cut_at_branch_hop

BOX = (40, 20, 560, 470)
SYNTH = Path(os.environ.get("DSDIG_SYNTH_SET1", "out/synth-charts/set1"))


def _chart(erase=None) -> Image.Image:
    """Shared ramp + plateau, branch A leaves at x=200, branch B at x=260; both
    rise at slope 1 to y=80 (A ends at x=450, B at x=510)."""

    image = Image.new("RGB", (600, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.line([(40, 470), (140, 330), (200, 330)], fill="black", width=3)
    draw.line([(200, 330), (450, 80)], fill="black", width=3)
    draw.line([(200, 330), (260, 330), (510, 80)], fill="black", width=3)
    if erase:
        draw.rectangle(erase, fill="white")  # an in-plot label masked out
    return image


def _on(point, knee_x) -> bool:
    return abs(point[1] - (330 - (point[0] - knee_x))) <= 3


def _branch_counts(curve):
    post = [p for p in curve if p[0] > 270]
    return sum(_on(p, 200) for p in post), sum(_on(p, 260) for p in post)


class _NaiveTracer:
    """The tracer as it was before the branch follower (known-bad control)."""

    def __enter__(self):
        self.saved = trace.follow_branch_through_hops
        trace.follow_branch_through_hops = lambda points, _mask, _centers: points

    def __exit__(self, *_exc):
        trace.follow_branch_through_hops = self.saved


class RasterBranchTests(unittest.TestCase):
    ERASE = (300, 180, 345, 240)  # over branch A only, 45 px wide

    def test_known_bad_naive_tracer_hops_at_an_erased_label(self) -> None:
        with _NaiveTracer():
            curve = trace._trace_gate_curve(_chart(self.ERASE), BOX)
        on_a, on_b = _branch_counts(curve)
        self.assertGreater(on_b, 3 * on_a)  # it left A for B at the label
        # and the final guard refuses to call what is left of A a full curve
        kept, cut_short = cut_at_branch_hop(curve, BOX)
        self.assertLess(len(kept), len(curve))
        self.assertTrue(cut_short)

    def test_follower_stays_on_its_branch_through_the_label(self) -> None:
        curve = trace._trace_gate_curve(_chart(self.ERASE), BOX)
        on_a, on_b = _branch_counts(curve)
        self.assertGreater(on_a, 100)
        self.assertEqual(on_b, 0)
        self.assertLessEqual(max(x for x, _y in curve), 452)  # A's own end
        self.assertEqual(cut_at_branch_hop(curve, BOX), (curve, False))

    def test_terminal_drop_onto_the_longer_branch_is_cut(self) -> None:
        curve = trace._trace_gate_curve(_chart(), BOX)
        self.assertGreater(_branch_counts(curve)[1], 10)  # the raw trace hopped at A's end
        kept, cut_short = cut_at_branch_hop(curve, BOX)
        self.assertFalse(cut_short)  # A reached the top: a complete branch
        self.assertEqual(_branch_counts(kept)[1], 0)
        self.assertLessEqual(max(x for x, _y in kept), 452)


class SlopedPlateauKneeTests(unittest.TestCase):
    """IRFBA90N20D / IRLB4030 (2026-09-30): piecewise-linear vector charts whose
    trace leaves a (sloped) plateau at a sharp knee while the other branches'
    plateau ink continues. The knee is not a hop."""

    @staticmethod
    def _mask_and_trace():
        image = Image.new("L", (520, 450), 0)
        draw = ImageDraw.Draw(image)
        draw.line([(0, 450), (80, 310), (210, 271)], fill=255, width=3)
        draw.line([(210, 271), (340, 11)], fill=255, width=3)  # traced branch
        draw.line([(210, 271), (260, 256), (385, 6)], fill=255, width=3)  # plateau goes on

        def y(x):
            if x <= 80:
                return 450 - 140 * x / 80
            if x <= 210:
                return 310 - 39 * (x - 80) / 130
            return 271 - 2 * (x - 210)

        return np.asarray(image), [(x, int(round(y(x)))) for x in range(0, 340)]

    def test_follower_keeps_the_rise_after_a_sharp_sloped_knee(self) -> None:
        mask, points = self._mask_and_trace()
        out = branch.follow_branch_through_hops(points, mask, trace._cluster_runs)
        self.assertEqual(out, points)

    def test_known_bad_walk_along_the_plateau_would_delete_the_rise(self) -> None:
        mask, points = self._mask_and_trace()
        saved = branch._climbs_like_a_branch
        branch._climbs_like_a_branch = lambda *_args: True
        try:
            out = branch.follow_branch_through_hops(points, mask, trace._cluster_runs)
        finally:
            branch._climbs_like_a_branch = saved
        self.assertLess(max(x for x, _y in out), 280)  # the rise is gone

    def test_cut_guard_ignores_a_window_straddling_the_knee(self) -> None:
        _mask, points = self._mask_and_trace()
        curve = [(x + 40, y + 20) for x, y in points[::4]]
        self.assertEqual(cut_at_branch_hop(curve, BOX), (curve, False))


def _ramp_plateau(plateau_end=260):
    curve = [(x, 470 - int(1.4 * (x - 40))) for x in range(40, 140, 4)]
    curve += [(x, 330) for x in range(140, plateau_end, 4)]
    return curve


class CutGuardTests(unittest.TestCase):
    def test_single_curve_is_unchanged(self) -> None:
        curve = _ramp_plateau() + [(x, 330 - (x - 260)) for x in range(264, 512, 4)]
        self.assertEqual(cut_at_branch_hop(curve, BOX), (curve, False))

    def test_parallel_hop_high_up_is_cut_but_served(self) -> None:
        curve = _ramp_plateau() + [(x, 330 - (x - 260)) for x in range(264, 460, 4)]
        curve += [(x, 330 - (x - 300)) for x in range(460, 560, 4)]  # 40 px lower
        kept, cut_short = cut_at_branch_hop(curve, BOX)
        self.assertEqual(max(x for x, _y in kept), 456)
        self.assertFalse(cut_short)

    def test_parallel_hop_low_down_is_cut_short(self) -> None:
        curve = _ramp_plateau() + [(x, 330 - (x - 260)) for x in range(264, 330, 4)]
        curve += [(x, 330 - (x - 300)) for x in range(330, 560, 4)]
        kept, cut_short = cut_at_branch_hop(curve, BOX)
        self.assertEqual(max(x for x, _y in kept), 328)
        self.assertTrue(cut_short)

    def test_lift_off_the_plateau_onto_a_branch_above_is_cut(self) -> None:
        # rode the plateau to x=300, then jumped 40 px up onto a branch that
        # left the plateau at x=260 (a dash gap on the higher-VDD plateau)
        curve = _ramp_plateau(304) + [(x, 330 - (x - 260)) for x in range(304, 520, 4)]
        kept, cut_short = cut_at_branch_hop(curve, BOX)
        self.assertEqual(max(x for x, _y in kept), 300)
        self.assertTrue(cut_short)

    def test_monotone_in_hop_size(self) -> None:
        # a bigger jump must not stop being a hop
        for drop in (20, 40, 80, 160):
            curve = _ramp_plateau() + [(x, 330 - (x - 260)) for x in range(264, 330, 4)]
            curve += [(x, 330 - (x - 260) + drop) for x in range(330, 560, 4)]
            self.assertTrue(cut_at_branch_hop(curve, BOX)[1], drop)

    def test_gap_across_the_plateau_is_not_a_hop(self) -> None:
        # an erased label over the whole plateau: ramp, 100 px gap, rising branch
        curve = [(x, 470 - int(1.4 * (x - 40))) for x in range(40, 140, 4)]
        curve += [(x, 330 - int(0.9 * (x - 240))) for x in range(240, 520, 4)]
        self.assertEqual(cut_at_branch_hop(curve, BOX), (curve, False))

    def test_isolated_terminal_fragment_is_dropped(self) -> None:
        curve = _ramp_plateau() + [(x, 330 - (x - 260)) for x in range(264, 512, 4)]
        kept, cut_short = cut_at_branch_hop(curve + [(558, 82)], BOX)
        self.assertEqual(kept, curve)
        self.assertFalse(cut_short)

    def test_unevaluable_curve_is_returned_unchanged(self) -> None:
        curve = [(40, 470), (60, 440), (80, 420)]
        self.assertEqual(cut_at_branch_hop(curve, BOX), (curve, False))


class SynthPageTests(unittest.TestCase):
    def _result(self, page: str, diagram: int):
        pdf = SYNTH / "pdf" / f"{page}.pdf"
        if not pdf.exists():
            self.skipTest(f"synthetic set1 page {pdf} is not generated here")
        results, _errors = gate.digitize_gate_charge_fail_closed(pdf, dpi=220, finder_dpi=220)
        match = [r for r in results if r.panel.diagram == diagram]
        self.assertEqual(len(match), 1)
        return match[0]

    def test_syn1_0025_bridges_the_40v_label_and_keeps_vpl(self) -> None:
        result = self._result("page_0092", 11)
        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.vpl, 4.44, delta=0.1)  # was 6.82 through the hop

    def test_syn1_0213_plateau_lift_is_not_served_ok(self) -> None:
        result = self._result("page_0067", 7)
        self.assertNotEqual(result.status, "ok")
        self.assertIn(BRANCH_HOP_DIAGNOSTIC, result.diagnostics)


if __name__ == "__main__":
    unittest.main()
