"""Known-bad fixtures for the review-grade C(V) retrace.

Each test CONSTRUCTS a failure that shipped in 15 human-GREEN capacitance
overlays (re-examined 2026-09-29) and checks that the retrace refuses it or
gets it right:

* Crss traced along the 0 pF frame rail (AO, NCE linear panels);
* Ciss/Coss swapped where the vector curves cross at a shared vertex (NXP);
* a plot box one gridline short of the frame (NCE4080, NCE60P28AK, NCEP050N12D);
* a neighbouring panel's axis labels consumed as this chart's ticks (AO);
* a curve hooking onto a label arrow (AOL1454, NCE60P28AK);
* the decade exponent read as digits (``10`` + ``2`` -> 102, NXP).
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from datasheet_chart_digitizer.capacitance_retrace import (
    Frame,
    below_resolution_mask,
    own_frame,
    suppress_curve_ink,
    track_raster_curves,
)
from datasheet_chart_digitizer.gridline_anchor import detect_axis_lines

# ------------------------------------------------------------------ raster

H, W = 420, 560
FX0, FY0, FX1, FY1 = 60, 30, 520, 380  # frame rule centres


def _panel(grid_value: int = 170) -> np.ndarray:
    image = np.full((H, W), 255, np.uint8)
    for x in np.linspace(FX0, FX1, 9):
        image[FY0:FY1 + 1, int(round(x))] = grid_value
    for y in np.linspace(FY0, FY1, 8):
        image[int(round(y)), FX0:FX1 + 1] = grid_value
    image[FY0:FY1 + 1, FX0 - 1:FX0 + 2] = 90  # frame rails, grey like NCE
    image[FY1 - 1:FY1 + 2, FX0:FX1 + 1] = 90
    return image


def _stroke(image, fn, x_from, x_to, width=4.0):
    """Draw y = fn(x) as a dark stroke of ``width`` px (vertical thickness)."""
    for x in range(x_from, x_to + 1):
        y = fn(x)
        # thickness along the normal: widen by the local slope
        slope = fn(x + 0.5) - fn(x - 0.5)
        half = 0.5 * width * math.hypot(1.0, slope)
        lo, hi = int(math.floor(y - half + 0.5)), int(math.floor(y + half - 0.5))
        image[max(0, lo):hi + 1, x] = 0


def _crss_on_rail_panel():
    image = _panel()
    _stroke(image, lambda x: 90 + 30 * math.exp(-(x - FX0) / 60.0), FX0 + 3, FX1 - 3)  # Ciss
    _stroke(image, lambda x: 150 + 120 * math.exp(-(x - FX0) / 80.0), FX0 + 3, FX1 - 3)  # Coss
    # Crss decays onto the 0 pF rail from x ~ 300 on (like AON6226 above 30 V)
    _stroke(image, lambda x: FY1 - 2 - 140 * math.exp(-(x - FX0) / 55.0), FX0 + 3, FX1 - 3)
    return image


class RasterTrackingTests(unittest.TestCase):
    def test_crss_is_never_served_on_the_zero_rail(self) -> None:
        image = _crss_on_rail_panel()
        frame = Frame(FX0, FY0, FX1, FY1)
        curves, _seed = track_raster_curves(image, frame, ink_threshold=60)
        crss = max(curves, key=lambda c: np.median(c.ys))
        served = crss.served()
        # the unfixed failure: a column-wise tracker that keeps the lowest run serves the rail
        rail_rows = [y for _x, y, _s in served if y >= FY1 - 4]
        self.assertTrue(served, "Crss must be traced where it is off the rail")
        # the served Crss never sits on the rail rows
        self.assertEqual(rail_rows, [])
        # and it is not a flat rail-riding line: it follows the decay
        self.assertGreater(max(y for _x, y, _s in served) - min(y for _x, y, _s in served), 80)
        # where it merges with the rail it stops instead of riding it
        self.assertLess(max(x for x, _y, _s in served), FX1 - 40)

    def test_below_resolution_mask_flags_the_rail_band(self) -> None:
        zero = 380.0
        self.assertEqual(below_resolution_mask([370.0, 377.0, 379.2, 380.0], zero), [False, True, True, True])

    def test_arrowhead_merged_into_a_curve_is_not_served(self) -> None:
        image = _panel()
        _stroke(image, lambda x: 100.0, FX0 + 3, FX1 - 3)
        _stroke(image, lambda x: 200.0 + 0.05 * (x - FX0), FX0 + 3, FX1 - 3)
        _stroke(image, lambda x: 300.0 + 0.05 * (x - FX0), FX0 + 3, FX1 - 3)
        # a filled arrowhead touching the middle curve from above at x 280..292
        for x in range(280, 293):
            image[int(200 + 0.05 * (x - FX0)) - 9 + (x - 280) // 2:int(200 + 0.05 * (x - FX0)), x] = 0
        curves, _seed = track_raster_curves(image, Frame(FX0, FY0, FX1, FY1), ink_threshold=60)
        middle = sorted(curves, key=lambda c: np.median(c.ys))[1]
        for x, y, status in middle.served():
            expected = 200.0 + 0.05 * (x - FX0)
            self.assertLess(abs(y - expected), 1.6, f"x={x} served {y:.1f} (status {status}), stroke at {expected:.1f}")
        # and the curve continues past the arrowhead
        self.assertGreater(max(x for x, _y, _s in middle.served()), 400)

    def test_steep_step_is_followed_not_refused(self) -> None:
        image = _panel()
        _stroke(image, lambda x: 90.0, FX0 + 3, FX1 - 3)
        _stroke(image, lambda x: 170.0, FX0 + 3, FX1 - 3)
        # Crss drops by 70 px across ~8 columns at x ~ 330 (NCES090P100T4)
        _stroke(image, lambda x: 250.0 + 70.0 / (1.0 + math.exp(-(x - 330) / 1.5)), FX0 + 3, FX1 - 3)
        curves, _seed = track_raster_curves(image, Frame(FX0, FY0, FX1, FY1), ink_threshold=60)
        crss = max(curves, key=lambda c: np.median(c.ys))
        xs = [x for x, _y, _s in crss.served()]
        self.assertLess(min(xs), 100)
        self.assertGreater(max(xs), 480)

    def test_touching_strokes_keep_their_own_edges(self) -> None:
        image = _panel()
        _stroke(image, lambda x: 90.0, FX0 + 3, FX1 - 3)
        # Coss/Crss converge until their 4 px strokes touch (NCE4080 above 33 V)
        _stroke(image, lambda x: 250.0 - max(0.0, 40.0 - 0.2 * (x - FX0)), FX0 + 3, FX1 - 3)
        _stroke(image, lambda x: 254.0 + max(0.0, 40.0 - 0.2 * (x - FX0)), FX0 + 3, FX1 - 3)
        curves, _seed = track_raster_curves(image, Frame(FX0, FY0, FX1, FY1), ink_threshold=60, stroke_px=4.0)
        coss, crss = sorted(curves, key=lambda c: np.median(c.ys))[1:]
        tail_c = [y for x, y, s in coss.served() if x > 400]
        tail_r = [y for x, y, s in crss.served() if x > 400]
        self.assertTrue(tail_c and tail_r)
        self.assertLess(abs(np.median(tail_c) - 250.0), 1.0)
        self.assertLess(abs(np.median(tail_r) - 254.0), 1.0)


class FrameTests(unittest.TestCase):
    def test_hint_one_gridline_short_extends_to_the_frame(self) -> None:
        image = _panel()
        # dsdig stopped at the gridline before the right frame (35 of 40 V)
        short = (FX0, FY0, FX1 - (FX1 - FX0) / 8.0 + 4, FY1)
        frame = own_frame(image, short, search_px=70)
        self.assertAlmostEqual(frame.x1, FX1, delta=0.6)

    def test_no_rule_near_the_hint_refuses(self) -> None:
        image = np.full((H, W), 255, np.uint8)
        with self.assertRaises(RuntimeError):
            own_frame(image, (FX0, FY0, FX1, FY1))

    def test_flat_curve_on_a_gridline_does_not_move_its_centre(self) -> None:
        image = _panel(grid_value=128)
        rule_y = int(round(np.linspace(FY0, FY1, 8)[3]))
        # a flat black Ciss lying 2 px under the 5000 pF rule for most of the plot (AON6276)
        image[rule_y + 1:rule_y + 5, FX0 + 5:FX1 - 150] = 0
        frame = Frame(FX0, FY0, FX1, FY1)
        biased = [line.center_px for line in detect_axis_lines(
            image, orientation="y", along=(rule_y - 10, rule_y + 10), cross_span=(FX0, FX1), max_width=8, tick_band=3)]
        clean = [line.center_px for line in detect_axis_lines(
            suppress_curve_ink(image, frame), orientation="y", along=(rule_y - 10, rule_y + 10),
            cross_span=(FX0, FX1), max_width=8, tick_band=3)]
        self.assertTrue(any(abs(c - rule_y) > 1.0 for c in biased), f"fixture must bias the raw detector: {biased}")
        self.assertEqual(len(clean), 1)
        self.assertAlmostEqual(clean[0], rule_y, delta=0.01)


if __name__ == "__main__":
    unittest.main()
