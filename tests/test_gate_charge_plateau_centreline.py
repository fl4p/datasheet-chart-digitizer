"""A Miller plateau erased with the grid rules is bridged on the stroke centreline.

Fab, review packet corpus-fix-001 (good_ark/SSFB3910L p3 d4): "miller plateau is
a bit tilted". The plateau stroke is longer than 35 % of the plot width, so
``gate_ink_mask`` erases its rows with the grid rules. The trace then jumped
from the first ramp's last column (bottom edge of the stroke) to the second
ramp's first column (top edge): one served segment tilted by the stroke
thickness, and Vpl read from it 3 px low.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from datasheet_chart_digitizer import gate_charge as gate
from datasheet_chart_digitizer import gate_charge_trace as trace

ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"

W, H = 500, 400
PLATEAU_ROWS = range(198, 203)  # 5 px stroke, centre 200
PLATEAU_X = (140, 360)  # 220 px = 44 % of the width: erased by row_frac > 0.35


def _thick_chart(*, grid: bool = True) -> Image.Image:
    gray = np.full((H, W), 255, np.uint8)
    if grid:
        gray[100, :] = 0  # grid rules (erased by the long-line opening)
        gray[:, 250] = 0
    for x in range(0, PLATEAU_X[0] + 1):  # steep first ramp ending on the plateau
        yc = (H - 1) + x * (200 - (H - 1)) / PLATEAU_X[0]
        gray[int(round(yc)) - 2 : int(round(yc)) + 3, max(0, x - 1) : x + 2] = 0
    for y in PLATEAU_ROWS:
        gray[y, PLATEAU_X[0] : PLATEAU_X[1] + 1] = 0
    for x in range(PLATEAU_X[1], W):  # gentle second ramp leaving it
        yc = 200 - 0.7 * (x - PLATEAU_X[1])
        gray[int(round(yc)) - 2 : int(round(yc)) + 3, x : x + 2] = 0
    return Image.fromarray(gray).convert("RGB")


class SyntheticPlateauTests(unittest.TestCase):
    BOX = (0, 0, W - 1, H - 1)

    def _served(self, image: Image.Image) -> list[tuple[int, int]]:
        return trace._smooth_polyline(trace._trace_gate_curve(image, self.BOX))

    def test_erased_thick_plateau_is_served_flat_on_the_centreline(self) -> None:
        # Known-bad on 27c2559: no point between the corners, served segment
        # runs edge to edge (tilted by the stroke thickness).
        served = self._served(_thick_chart())
        inside = [(x, y) for x, y in served if PLATEAU_X[0] + 6 <= x <= PLATEAU_X[1] - 6]
        self.assertGreaterEqual(len(inside), 40, served)
        self.assertEqual({y for _x, y in inside}, {200})

    def test_flat_without_grid_specks_too(self) -> None:
        served = self._served(_thick_chart(grid=False))
        inside = [(x, y) for x, y in served if PLATEAU_X[0] + 6 <= x <= PLATEAU_X[1] - 6]
        self.assertGreaterEqual(len(inside), 40, served)
        self.assertEqual({y for _x, y in inside}, {200})

    def test_band_centre_is_the_stroke_middle_not_an_edge(self) -> None:
        gray = np.full((H, W), 255, np.uint8)
        for y in PLATEAU_ROWS:
            gray[y, 140:361] = 0
        # ends on the bottom edge at the left, top edge at the right
        self.assertEqual(trace._source_plateau_band_centre((140, 203), (360, 197), gray), 200)

    def test_grid_rule_alone_is_not_a_plateau(self) -> None:
        gray = np.full((H, W), 255, np.uint8)
        gray[200:202, :] = 0  # a rule across the whole plot, no stroke
        self.assertIsNone(trace._source_plateau_band_centre((140, 203), (360, 197), gray))

    def test_rule_touching_the_stroke_is_stripped(self) -> None:
        gray = np.full((H, W), 255, np.uint8)
        for y in PLATEAU_ROWS:
            gray[y, 140:361] = 0
        gray[203, :] = 0  # rule just under the stroke
        self.assertEqual(trace._source_plateau_band_centre((140, 203), (360, 197), gray), 200)

    def test_partial_stroke_is_not_bridged(self) -> None:
        gray = np.full((H, W), 255, np.uint8)
        for y in PLATEAU_ROWS:
            gray[y, 140:300] = 0  # stroke stops 60 px short of the right end
        self.assertIsNone(trace._source_plateau_band_centre((140, 203), (360, 197), gray))

    def test_band_far_from_the_trace_ends_is_not_used(self) -> None:
        gray = np.full((H, W), 255, np.uint8)
        for y in range(180, 185):
            gray[y, 140:361] = 0
        self.assertIsNone(trace._source_plateau_band_centre((140, 203), (360, 203), gray))

    def test_speck_inside_the_gap_is_skipped(self) -> None:
        ordered = [(x, 300) for x in range(100, 141)] + [(250, 100), (251, 100)] + [
            (x, 200) for x in range(360, 380)
        ]
        ends = trace._plateau_bridge_ends(ordered, 40)
        self.assertEqual([ordered[i][0] for i in ends], [360, 250])


class RealPdfTests(unittest.TestCase):
    def test_ssfb3910l_plateau_flat_at_the_stroke_centre(self) -> None:
        # Page measurement, 220 dpi, this result's crop: the plateau stroke
        # is ink rows 285..289 at every column x=310..485 (centre 287).
        # VGS ticks 4.5/3.6/2.7/1.8/0.9/0 give ~0.0096 V/px, so the centre
        # row reads 3.41 V; 27c2559 served 3.383 V from y=290 with a single
        # segment (301, 291) -> (497, 282).
        pdf = ROOT / "good_ark/SSFB3910L.pdf"
        if not pdf.exists():
            self.skipTest("SSFB3910L regression PDF is not configured")
        found = [
            r for r in gate.digitize_gate_charge(pdf, dpi=220, finder_dpi=220)
            if (r.panel.page, r.panel.diagram) == (3, 4)
        ]
        self.assertEqual(len(found), 1)
        r = found[0]
        self.assertEqual(r.status, "ok")
        inside = [(x, y) for x, y in r.curve_px if 310 <= x <= 485]
        self.assertGreaterEqual(len(inside), 40)
        self.assertEqual({y for _x, y in inside}, {287})
        self.assertEqual(r.vpl_y_px, 287.0)
        self.assertAlmostEqual(r.vpl, 3.41, delta=0.01)


if __name__ == "__main__":
    unittest.main()
