"""Office chart-palette strokes are capacitance curve strokes (Excel-drawn datasheets).

Found by the 2026-09-30 astra-review-50 pass on HYG292N60NP1D p5 (Huayi,
iLovePDF/Excel): three clean vector curves in Office blue/red/green were
rejected by the stroke-colour predicate.
"""

from __future__ import annotations

import unittest

from datasheet_chart_digitizer.capacitance_vector import _is_curve_stroke_color


class OfficePaletteTests(unittest.TestCase):
    def test_office_default_series_colours_are_curves(self) -> None:
        for rgb in ((0.31, 0.51, 0.74), (0.75, 0.31, 0.3), (0.61, 0.73, 0.35)):
            self.assertTrue(_is_curve_stroke_color(rgb), rgb)

    def test_gridline_greys_are_not_curves(self) -> None:
        for rgb in ((0.85, 0.85, 0.85), (0.95, 0.95, 0.95), (0.6, 0.62, 0.64)):
            self.assertFalse(_is_curve_stroke_color(rgb), rgb)

    def test_pale_tint_is_not_a_curve(self) -> None:
        # a light fill-like tint is saturated but brighter than the palette
        self.assertFalse(_is_curve_stroke_color((0.95, 0.6, 0.6)))


if __name__ == "__main__":
    unittest.main()
