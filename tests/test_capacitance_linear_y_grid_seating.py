"""Seating linear capacitance Y ticks onto SOURCE gridlines.

Two facts drive this module: some vendors draw the horizontal grid as filled
rectangles/quads rather than line items (so a line-only scan finds nothing and
falls back to noisy raster detection), and a tick LABEL's text center drifts a
few pixels from its rule while the rule itself is exact. Assignment therefore
gets latitude only when the candidates are source vector geometry -- the fit
residual gate stays tight either way, so acceptance never loosens.
"""
from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer import capacitance_axis as ca
from datasheet_chart_digitizer.capacitance_types import AxisCalibration, PlotBox
from datasheet_chart_digitizer.crop_transform import CropTransform


class _Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _Rect:
    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1


class _Quad:
    def __init__(self, rect, rectangular=True):
        self.rect = rect
        self.is_rectangular = rectangular


class _Page:
    def __init__(self, drawings):
        self._drawings = drawings

    def get_drawings(self):
        return list(self._drawings)


PLOT = PlotBox(x0=0, y0=0, x1=100, y1=200)
# 1 px per pt, no offset: keeps expected values readable.
TRANSFORM = CropTransform(x0_pt=0.0, y0_pt=0.0, scale_x=1.0, scale_y=1.0)


def _line_rule(y):
    return {"type": "s", "items": [("l", _Pt(0.0, y), _Pt(100.0, y))]}


def _rect_rule(y0, y1):
    return {"type": "s", "items": [("re", _Rect(0.0, y0, 100.0, y1))]}


def _quad_rule(y0, y1, rectangular=True):
    return {
        "type": "s",
        "items": [("qu", _Quad(_Rect(0.0, y0, 100.0, y1), rectangular))],
    }


class VectorGridlineCandidateTests(unittest.TestCase):
    def test_line_rules_are_recovered(self) -> None:
        page = _Page([_line_rule(40.0), _line_rule(120.0)])
        found = ca._vector_horizontal_gridline_candidates(page, TRANSFORM, PLOT)
        self.assertEqual([round(v, 1) for v in found], [40.0, 120.0])

    def test_full_width_rect_contributes_both_edges(self) -> None:
        # A grid drawn as a tall filled rectangle: its top and bottom edges
        # ARE rules; a line-only scan would return nothing here.
        page = _Page([_rect_rule(20.0, 180.0)])
        found = ca._vector_horizontal_gridline_candidates(page, TRANSFORM, PLOT)
        self.assertEqual([round(v, 1) for v in found], [20.0, 180.0])

    def test_rectangular_quad_is_treated_like_a_rect(self) -> None:
        page = _Page([_quad_rule(20.0, 180.0)])
        found = ca._vector_horizontal_gridline_candidates(page, TRANSFORM, PLOT)
        self.assertEqual([round(v, 1) for v in found], [20.0, 180.0])

    def test_non_rectangular_quad_is_ignored(self) -> None:
        page = _Page([_quad_rule(20.0, 180.0, rectangular=False)])
        self.assertEqual(
            ca._vector_horizontal_gridline_candidates(page, TRANSFORM, PLOT), []
        )

    def test_narrow_rect_is_not_a_rule(self) -> None:
        # A legend box or inset panel does not span the plot width.
        page = _Page([{"type": "s", "items": [("re", _Rect(10.0, 20.0, 40.0, 180.0))]}])
        self.assertEqual(
            ca._vector_horizontal_gridline_candidates(page, TRANSFORM, PLOT), []
        )

    def test_short_rect_is_not_a_rule(self) -> None:
        # Full width but under half the plot height: a bar/annotation, not the
        # grid envelope whose edges are rules.
        page = _Page([_rect_rule(20.0, 60.0)])
        self.assertEqual(
            ca._vector_horizontal_gridline_candidates(page, TRANSFORM, PLOT), []
        )


def _calibration(values, label_pixels):
    return AxisCalibration(
        x_min_v=0.0, x_max_v=100.0, y_min_decade=0.0, y_max_decade=3.0,
        source="position_text", x_ticks_v=(0.0, 100.0), y_decades=(),
        y_log=False, y_ticks_pf=tuple(values),
        y_tick_label_px=tuple(label_pixels),
        y_scale=-1.0, y_offset=0.0,
    )


class SeatingToleranceTests(unittest.TestCase):
    @staticmethod
    def _image():
        return np.full((201, 101), 255, dtype=np.uint8)

    def test_vector_candidates_absorb_label_center_drift(self) -> None:
        # EPC linear panels: labels sit ~1.7-3.2 px below their exact rules.
        # With source vector rules that drift must not refuse the seating.
        rules = [10.0, 60.0, 110.0, 160.0]
        # values ascend while page-y descends, as the production fit emits them
        labels = [l + d for l, d in zip(rules, (1.7, 2.3, 2.9, 3.2))][::-1]
        page = _Page([_line_rule(y) for y in rules])
        seated = ca._seat_linear_y_ticks_on_grid(
            _calibration([0.0, 1000.0, 2000.0, 3000.0], labels),
            self._image(), PLOT, page=page, transform=TRANSFORM,
        )
        self.assertEqual(seated.y_ticks_pf, (0.0, 1000.0, 2000.0, 3000.0))

    def test_drift_beyond_the_vector_window_still_refuses(self) -> None:
        rules = [10.0, 60.0, 110.0, 160.0]
        labels = [y + 9.0 for y in rules][::-1]
        page = _Page([_line_rule(y) for y in rules])
        with self.assertRaises(RuntimeError):
            ca._seat_linear_y_ticks_on_grid(
                _calibration([0.0, 1000.0, 2000.0, 3000.0], labels),
                self._image(), PLOT, page=page, transform=TRANSFORM,
            )

    def test_closely_spaced_rules_make_ownership_ambiguous(self) -> None:
        # The widened window must not silently pick one of two rules: with
        # minor rules 5 px from the majors, a label sees two candidates and
        # ownership is unprovable, so seating refuses.
        rules = [10.0, 15.0, 60.0, 65.0, 110.0, 115.0, 160.0, 165.0]
        labels = [12.0, 62.0, 112.0, 162.0][::-1]
        page = _Page([_line_rule(y) for y in rules])
        with self.assertRaises(RuntimeError) as caught:
            ca._seat_linear_y_ticks_on_grid(
                _calibration([0.0, 1000.0, 2000.0, 3000.0], labels),
                self._image(), PLOT, page=page, transform=TRANSFORM,
            )
        self.assertIn("exactly one", str(caught.exception))

    def test_non_affine_seated_rules_fail_the_fit_gate(self) -> None:
        # Each label owns exactly one rule, but the owned rules do not form an
        # affine ladder against the tick values. The <=1 px fit gate is what
        # catches that, and it stays load-bearing under the wider window --
        # so the window can never launder a bad calibration.
        rules = [10.0, 60.0, 110.0, 195.0]
        labels = [12.0, 62.0, 112.0, 197.0][::-1]
        page = _Page([_line_rule(y) for y in rules])
        with self.assertRaises(RuntimeError) as caught:
            ca._seat_linear_y_ticks_on_grid(
                _calibration([0.0, 1000.0, 2000.0, 3000.0], labels),
                self._image(), PLOT, page=page, transform=TRANSFORM,
            )
        self.assertIn("residual", str(caught.exception))

    def test_raster_fallback_keeps_the_tight_window(self) -> None:
        # No vector rules at all -> raster candidates, which get no latitude.
        page = _Page([])
        image = self._image()
        for y in (10, 60, 110, 160):
            image[y, :] = 0
        labels = [y + 5.0 for y in (10.0, 60.0, 110.0, 160.0)][::-1]
        with self.assertRaises(RuntimeError) as caught:
            ca._seat_linear_y_ticks_on_grid(
                _calibration([0.0, 1000.0, 2000.0, 3000.0], labels),
                image, PLOT, page=page, transform=TRANSFORM,
            )
        self.assertIn("3 px", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
