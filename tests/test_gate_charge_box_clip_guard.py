"""A gate-charge box that ends on an interior rule must not serve the cut curve as ok.

Found by the 2026-09-30 astra-review-50 pass: AP80N08D / SL80N08D (box top on
the 8 V rule, frame and stroke to 10 V), NCE30H12 (right edge at 40 of 50 nC),
AGM16N65F (65 of 75 nC), STL7N6LF3 (10 of 12 V/nC) were served ok with the
curve cut at the box edge.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import numpy as np

from datasheet_chart_digitizer import gate_charge as gate
from datasheet_chart_digitizer.gate_charge_trace import _curve_clipped_by_plot_box

ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"


def _chart(stroke_beyond: bool) -> tuple[np.ndarray, list[tuple[int, int]]]:
    gray = np.full((300, 300), 255, np.uint8)
    gray[20, 20:280] = 0; gray[280, 20:280] = 0; gray[20:281, 20] = 0; gray[20:281, 280] = 0  # frame
    gray[80, 20:280] = 0  # interior rule the box wrongly ends on
    curve = [(x, 280 - x) for x in range(40, 201)]  # rises to y=80 at x=200
    for x, y in curve:
        gray[y - 1:y + 2, x] = 0
    if stroke_beyond:
        for x in range(200, 250):
            gray[280 - x - 1:280 - x + 2, x] = 0
    return gray, curve


class ClipPredicateTests(unittest.TestCase):
    BOX = (20, 80, 280, 280)
    FRAME = (20, 20, 280, 280)

    def test_curve_cut_at_interior_rule_is_flagged(self) -> None:
        gray, curve = _chart(stroke_beyond=True)
        self.assertTrue(_curve_clipped_by_plot_box(gray, curve, self.BOX, self.FRAME))

    def test_curve_that_ends_at_the_rule_is_not_flagged(self) -> None:
        gray, curve = _chart(stroke_beyond=False)
        self.assertFalse(_curve_clipped_by_plot_box(gray, curve, self.BOX, self.FRAME))

    def test_box_on_the_frame_is_not_flagged(self) -> None:
        gray, curve = _chart(stroke_beyond=True)
        self.assertFalse(_curve_clipped_by_plot_box(gray, curve, self.FRAME, self.FRAME))

    def test_no_frame_is_not_evidence(self) -> None:
        gray, curve = _chart(stroke_beyond=True)
        self.assertFalse(_curve_clipped_by_plot_box(gray, curve, self.BOX, None))


class RealPdfTests(unittest.TestCase):
    def _served(self, rel: str):
        pdf = ROOT / rel
        if not pdf.exists():
            self.skipTest(f"{rel} regression PDF is not configured")
        results, _ = gate.digitize_gate_charge_fail_closed(pdf, dpi=220, finder_dpi=220)
        return results

    def test_ap80n08d_ok_result_reaches_the_frame_top(self) -> None:
        ok = [r for r in self._served("apm/AP80N08D.pdf") if r.status == "ok"]
        self.assertEqual(len(ok), 1)
        r = ok[0]
        top = min(y for _x, y in r.curve_px)
        self.assertLessEqual(top - r.plot_box_px[1], 0.02 * (r.plot_box_px[3] - r.plot_box_px[1]))
        self.assertAlmostEqual(r.vpl, 2.75, delta=0.05)
        # the OCR'd Qg labels survive although the clipped OCR result is not served
        xs = dict(r.x_ticks_px)
        self.assertEqual(sorted(xs), [0.0, 10.0, 20.0, 30.0, 40.0])
        self.assertAlmostEqual(xs[0.0], r.plot_box_px[0], delta=3.0)
        self.assertAlmostEqual(xs[40.0], r.plot_box_px[2], delta=3.0)

    def test_stl7n6lf3_cut_curve_is_not_served_ok(self) -> None:
        panels = [r for r in self._served("st/STL7N6LF3.pdf") if r.panel.page == 7 and r.panel.diagram == 8]
        self.assertTrue(panels)
        self.assertTrue(all(r.status != "ok" for r in panels))


if __name__ == "__main__":
    unittest.main()
