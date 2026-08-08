"""The bottom frame rail must never be served as Crss, and Crss must not
climb onto Coss when its own curve ends.

Both defects are ONE mechanism: the raster lanes are assigned by RANK inside
each column, and rank is not identity.

  * The frame-rail removal was gated on the black-grid signature, so on
    gray-grid panels (the whole GT/goford family) the bottom rail survived as
    the lowest per-column stroke. `_trace_candidates(..., "bottom")` takes
    `centers[-1]`, so the rail won and the real Crss -- carrying hundreds of
    pF -- was discarded for the whole span. GT048N10T, GT080N10T, GT085N10TH,
    GT035N12T and GT060N10T all shipped this while reporting
    `trace_validation_status == "pass"`.

  * With the rail gone, a column where Crss has decayed below resolution holds
    only two strokes, and `centers[-1]` is then the COSS stroke. Crss climbed
    onto Coss and was served ~50x too high in the tail (GT045N10T/TH), while
    Coss simultaneously lost its lane.

The repaired behaviour fails toward an explicit SHORT SPAN -- a gap the caller
must handle -- never toward a confident wrong value.
"""
from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer import capacitance_traces as ct
from datasheet_chart_digitizer.capacitance_types import PlotBox


PLOT = PlotBox(20, 10, 320, 300)
RAIL_ROWS = (294, 295, 296)


def _panel(*, crss_ends_at: int | None = None) -> np.ndarray:
    """Gray-grid panel: a black bottom rail plus three descending curves.

    `crss_ends_at` stops the Crss stroke early, reproducing a Crss that has
    decayed into the axis while Coss continues.
    """

    image = np.full((330, 340), 255, dtype=np.uint8)
    for row in RAIL_ROWS:
        image[row, 20:321] = 0
    # A gray grid: light enough to fall outside the dark mask, which is exactly
    # why the black-grid gate never fired on these panels.
    for row in range(40, 290, 40):
        image[row, 20:321] = 180
    for x in range(20, 321):
        t = (x - 20) / 300.0
        ciss = 40 + int(round(10 * t))
        coss = 90 + int(round(150 * t))
        crss = 250 + int(round(40 * t))
        for y in (ciss, coss):
            image[y - 1 : y + 2, x] = 0
        if crss_ends_at is None or x <= crss_ends_at:
            image[crss - 1 : crss + 2, x] = 0
    return image


class FrameRailIsNotATrace(unittest.TestCase):
    def test_rail_row_is_not_offered_as_a_column_center(self) -> None:
        gray = _panel()
        _, centers = ct._raster_source_centers_by_x(gray, PLOT)
        rail_local = [row - PLOT.y0 for row in RAIL_ROWS]
        mid = centers[len(centers) // 2]
        self.assertTrue(mid, "expected strokes in a mid panel column")
        for center in mid:
            self.assertFalse(
                any(abs(center - rail) <= 2.0 for rail in rail_local),
                f"rail row served as a stroke center: {center} in {mid}",
            )

    def test_crss_is_the_real_curve_not_the_rail(self) -> None:
        gray = _panel()
        traces = ct.extract_trace_components(gray, PLOT)
        crss = {t.name: t for t in traces}["Crss"]
        mid_x = (PLOT.x0 + PLOT.x1) // 2
        served = ct._interp_y(crss.points, mid_x)
        expected = 250 + 40 * (mid_x - 20) / 300.0 + PLOT.y0 - PLOT.y0
        # The rail sits at ~295; the real Crss at ~270. Anything within a
        # couple of pixels of the rail means the rail won again.
        self.assertLess(
            abs(served - (expected + PLOT.y0 - PLOT.y0)),
            8.0,
            f"Crss served at {served}, expected near {expected}",
        )
        self.assertGreater(
            min(abs(served - row) for row in RAIL_ROWS),
            8.0,
            "Crss is sitting on the frame rail",
        )


class CrssMustNotClimbOntoCoss(unittest.TestCase):
    def test_reserved_stroke_is_withheld_not_reassigned(self) -> None:
        # The Coss stroke is reserved at x=50; the only bottom candidate IS
        # that stroke, so Crss must be given nothing rather than Coss's ink.
        reserved = {50: 120.0}
        self.assertEqual(
            ct._drop_reserved_candidates([120.5], reserved, 50),
            [],
            "Crss was allowed to claim the stroke Coss already owns",
        )
        # A genuinely separate stroke a few pixels away is still offered.
        self.assertEqual(
            ct._drop_reserved_candidates([132.0], reserved, 50),
            [132.0],
        )
        # No reservation for this column -> unchanged.
        self.assertEqual(ct._drop_reserved_candidates([120.5], reserved, 51), [120.5])

    def test_crss_ends_rather_than_following_coss(self) -> None:
        end_x = 170
        gray = _panel(crss_ends_at=end_x)
        traces = {t.name: t for t in ct.extract_trace_components(gray, PLOT)}
        crss_max_x = max(x for x, _ in traces["Crss"].points)
        coss_max_x = max(x for x, _ in traces["Coss"].points)
        self.assertLess(
            crss_max_x,
            end_x + 30,
            "Crss ran past the end of its own stroke -- it climbed onto a peer",
        )
        self.assertGreater(
            coss_max_x,
            PLOT.x1 - 20,
            "Coss must keep its full span when Crss disappears",
        )


class SeedMustNotSitOnADiscontinuity(unittest.TestCase):
    def test_seed_requires_three_centers_across_a_window(self) -> None:
        # Three centers up to column 9, two thereafter: column 9 is a valid
        # three-center column but the worst possible seed, because the very
        # next column has already lost a lane.
        centers = [[10.0, 50.0, 90.0] for _ in range(10)]
        centers += [[10.0, 50.0] for _ in range(30)]
        stable = ct.stable_three_center_columns(centers)
        self.assertNotIn(9, stable)
        self.assertIn(4, stable)
        self.assertTrue(
            all(index <= 9 - ct.SEED_STABILITY_WINDOW_PX for index in stable),
            f"unstable seed columns survived: {stable}",
        )

    def test_no_stable_column_is_reported_as_empty_not_as_a_guess(self) -> None:
        # Alternating availability: nothing qualifies. The helper must say so
        # and let the caller decide, rather than inventing a seed.
        centers = [
            [10.0, 50.0, 90.0] if index % 2 else [10.0, 50.0]
            for index in range(40)
        ]
        self.assertEqual(ct.stable_three_center_columns(centers), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
