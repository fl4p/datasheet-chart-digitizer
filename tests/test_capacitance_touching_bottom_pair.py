"""`_split_touching_bottom_pair`: split only what a nearby clean column confirms.

TPCC8105 is the end-to-end case (test_toshiba_capacitance_raster). These pin
the rule on small masks: each refusal case is a way the split could otherwise
put a second curve on a single stroke -- the GT045N10T failure, where Crss
vanished and was served on Coss ~50x too high.
"""

from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer.capacitance_traces import (
    _cluster_column_runs,
    _split_touching_bottom_pair,
)

H, W = 120, 200
STROKE = 4


def _draw(mask: np.ndarray, x0: int, x1: int, top: int, height: int = STROKE) -> None:
    mask[top : top + height, x0:x1] = 1


def _chart(bottom_from: int = 100, bottom_gap: int = 2, clean_spacing: int = 7) -> np.ndarray:
    """Ciss at row 10; Coss/Crss `clean_spacing` apart left of `bottom_from`,
    then two strokes `bottom_gap` px apart (fused by the cluster step)."""
    mask = np.zeros((H, W), np.uint8)
    _draw(mask, 0, W, 10)
    _draw(mask, 0, bottom_from, 60)
    _draw(mask, 0, bottom_from, 60 + clean_spacing)
    _draw(mask, bottom_from, W, 60)
    _draw(mask, bottom_from, W, 60 + STROKE + bottom_gap)
    return mask


def _centers(mask: np.ndarray) -> list[list[float]]:
    return [_cluster_column_runs(mask[:, x]) for x in range(mask.shape[1])]


class SplitTouchingBottomPairTests(unittest.TestCase):
    def test_fused_pair_next_to_clean_columns_is_split_onto_ink(self) -> None:
        mask = _chart()
        centers = _centers(mask)
        self.assertEqual(len(centers[110]), 2, "precondition: the cluster step fuses the pair")
        out = _split_touching_bottom_pair(mask, centers)
        self.assertEqual(len(out[110]), 3)
        for y in out[110][1:]:
            self.assertTrue(mask[int(round(y)), 110], f"split center {y} is off ink")
        # beyond SPLIT_MAX_DISTANCE_PX from the last clean column: untouched
        self.assertEqual(out[150], centers[150])

    def test_touching_run_is_split_one_half_stroke_inside_each_edge(self) -> None:
        # TPCC8105 shape: 5 px strokes touching, one 10 px run (stroke median 4)
        mask = _chart()
        mask[:, 100:] = 0
        _draw(mask, 100, W, 10)
        _draw(mask, 100, W, 60, height=10)
        out = _split_touching_bottom_pair(mask, _centers(mask))
        self.assertEqual(out[110][1:], [61.5, 67.5])

    def test_converged_closer_than_the_clean_spacing_is_not_split(self) -> None:
        # two 4 px strokes touching are 4 px apart; the clean columns say 7
        mask = _chart(bottom_gap=0)
        centers = _centers(mask)
        self.assertEqual(_split_touching_bottom_pair(mask, centers)[110], centers[110])

    def test_spacing_that_does_not_match_the_clean_columns_is_not_split(self) -> None:
        # nearby clean pair is 20 px apart; a 2-px-gap pair is not the same two
        # curves converging, so it is not split
        mask = _chart(clean_spacing=20)
        centers = _centers(mask)
        self.assertEqual(_split_touching_bottom_pair(mask, centers)[110], centers[110])

    def test_a_single_thick_stroke_is_not_split(self) -> None:
        # Crss gone and Coss thickened (e.g. by a rail residue) to 6 px: implied
        # spacing 2 px vs the clean 7 px
        mask = _chart()
        mask[:, 100:] = 0
        _draw(mask, 100, W, 10)
        _draw(mask, 100, W, 60, height=6)
        centers = _centers(mask)
        self.assertEqual(_split_touching_bottom_pair(mask, centers)[110], centers[110])

    def test_too_few_clean_columns_means_no_split(self) -> None:
        mask = _chart(bottom_from=5)
        centers = _centers(mask)
        self.assertEqual(_split_touching_bottom_pair(mask, centers), centers)


if __name__ == "__main__":
    unittest.main()
