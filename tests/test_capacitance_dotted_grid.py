"""C(V) charts without solid gridlines: axis-rail plot box + dotted-rule evidence.

Known-bad controls are constructed here: labels with no rule or tick under
them, a 1 % served-calibration shift, a far-tail shift, two panels in one
crop, a crop border, curves and text that must not count as rules.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from datasheet_chart_digitizer.capacitance_frame_rails import rail_plot_box
from datasheet_chart_digitizer.capacitance_plot_box import (
    find_capacitance_plot_box_with_method,
    find_closed_frame_plot_box,
)
from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.gridline_anchor import (
    check_served_on_grid_attempts,
    detect_axis_lines,
)
from datasheet_chart_digitizer.numeric_axis import AxisTick, NumericAxis, fit_axis_ticks

H, W = 640, 900
L, R, T, B = 200, 800, 60, 540


def _canvas(paper: int = 255) -> np.ndarray:
    return np.full((H, W), paper, dtype=np.uint8)


def _v(img, x, y0, y1, width=3, ink=0):
    img[y0 : y1 + 1, x - width // 2 : x - width // 2 + width] = ink


def _h(img, y, x0, x1, width=3, ink=0):
    img[y - width // 2 : y - width // 2 + width, x0 : x1 + 1] = ink


def _dotted_v(img, x, y0, y1, pitch=6, dot=2, ink=110):
    for y in range(y0, y1 + 1, pitch):
        img[y : y + dot, x : x + 2] = ink


def _dotted_h(img, y, x0, x1, pitch=6, dot=2, ink=110):
    for x in range(x0, x1 + 1, pitch):
        img[y : y + 2, x : x + dot] = ink


X_VALUES = [0.0, 10.0, 20.0, 30.0, 40.0]


def _x_px(value: float) -> float:
    return L + (R - L) * value / 40.0


def _open_dotted_panel(*, rules: bool = True, curve: bool = True) -> np.ndarray:
    """Open frame (left + bottom axes only) with dotted major verticals."""
    img = _canvas()
    _v(img, L, T, B)
    _h(img, B, L, R)
    if rules:
        for value in X_VALUES[1:]:
            _dotted_v(img, int(round(_x_px(value))), T, B)
        for y in (180, 300, 420):
            _dotted_h(img, y, L, R)
    if curve:
        for x in range(L + 5, R - 5):  # a falling Coss-like curve, 3 px stroke
            y = int(T + 40 + 300 * np.exp(-(x - L) / 120.0))
            img[y - 1 : y + 2, x] = 0
    return img


class RailPlotBoxTests(unittest.TestCase):
    def test_open_dotted_frame_is_refused_by_shared_detectors(self):
        with self.assertRaisesRegex(RuntimeError, "could not find plot grid verticals"):
            find_closed_frame_plot_box(_open_dotted_panel())

    def test_open_frame_box_comes_from_the_axis_corner(self):
        box, method = find_capacitance_plot_box_with_method(_open_dotted_panel())
        self.assertEqual(method, "axis_rail_corner")
        self.assertEqual((box.x0, box.y0, box.y1), (L, T, B))
        # the last dotted rule's dots on the axis row may extend it by a pixel
        self.assertLessEqual(abs(box.x1 - R), 1)

    def test_closed_frame_with_outward_ticks_on_every_side(self):
        img = _canvas()
        for y in (T, B):
            _h(img, y, L - 12, R + 12)  # ticks out on the left AND right ends
        for x in (L, R):
            _v(img, x, T - 12, B + 12)  # ticks out at the top AND bottom ends
        self.assertEqual(rail_plot_box(img), PlotBox(L, T, R, B))

    def test_dense_minor_just_inside_an_open_frame_does_not_shrink_it(self):
        img = _open_dotted_panel(rules=False)
        _v(img, R - 9, T, B - 30, width=1, ink=120)  # label-broken minor rule
        _h(img, T + 9, L, R, width=1, ink=120)
        self.assertEqual(rail_plot_box(img), PlotBox(L, T, R, B))

    def test_curve_lying_on_the_bottom_rail_keeps_the_rail_centre(self):
        img = _open_dotted_panel(rules=False, curve=False)
        img[B - 6 : B, L : L + 380] = 0  # flat Crss tail merged onto the axis
        box = rail_plot_box(img)
        self.assertIsNotNone(box)
        self.assertEqual((box.x0, box.x1, box.y1), (L, R, B))

    def test_grey_paper_scan(self):
        img = _canvas(paper=205)
        _v(img, L, T, B)
        _h(img, B, L, R)
        self.assertEqual(rail_plot_box(img), PlotBox(L, T, R, B))

    def test_two_panels_in_one_crop_are_ambiguous(self):
        img = np.full((H, 2 * W), 255, dtype=np.uint8)
        for offset in (0, W):
            _v(img, L + offset, T, B)
            _h(img, B, L + offset, R + offset - 300)
        self.assertIsNone(rail_plot_box(img))

    def test_no_corner_is_refused_with_the_original_error(self):
        img = _canvas()
        _v(img, L, T, B)  # a lone rail and some label-like blobs
        for x in range(L, R, 100):
            img[B + 20 : B + 34, x : x + 18] = 0
        self.assertIsNone(rail_plot_box(img))
        with self.assertRaisesRegex(RuntimeError, "could not find plot grid verticals"):
            find_capacitance_plot_box_with_method(img)

    def test_crop_border_is_not_a_plot(self):
        img = _canvas()
        img[2:5, :] = img[-5:-2, :] = 0
        img[:, 2:5] = img[:, -5:-2] = 0
        self.assertIsNone(rail_plot_box(img))

    def test_small_legend_box_is_not_a_plot(self):
        img = _canvas()
        _v(img, 300, 100, 330)
        _h(img, 330, 300, 500)
        self.assertIsNone(rail_plot_box(img))


def _x_label_ticks(offsets=(-3.0, 4.0, -2.0, 5.0, 3.0)) -> list[AxisTick]:
    return [AxisTick(f"{v:g}", v, _x_px(v) + d) for v, d in zip(X_VALUES, offsets)]


def _served_on_lines(shift_px: float = 0.0) -> NumericAxis:
    ticks = [AxisTick(f"{v:g}", v, _x_px(v) + 0.5 + shift_px) for v in X_VALUES]
    return fit_axis_ticks(ticks, "served", model="linear")


def _check(img, served, *, broken_rules=True, ticks=None):
    return check_served_on_grid_attempts(
        img, served, ticks or _x_label_ticks(), frame=PlotBox(L, T, R, B),
        orientation="x", cross_span=(T + 2, B - 2), name="X", broken_rules=broken_rules,
    )


class BrokenRuleEvidenceTests(unittest.TestCase):
    def test_dotted_rules_are_observed_only_on_opt_in(self):
        img = _open_dotted_panel()
        kwargs = dict(orientation="x", along=(L - 5, R + 5), cross_span=(T + 2, B - 2),
                      max_width=8, tick_band=5)
        solid = [line.center_px for line in detect_axis_lines(img, **kwargs)]
        self.assertEqual(len(solid), 1)  # the left axis only
        found = detect_axis_lines(img, broken_rules=True, **kwargs)
        broken = [line.center_px for line in found if line.source == "broken_rule"]
        self.assertEqual(len(broken), 4)
        for value, px in zip(X_VALUES[1:], broken):
            self.assertAlmostEqual(px, _x_px(value) + 0.5, delta=0.6)

    def test_served_on_dotted_rules_is_verified(self):
        check = _check(_open_dotted_panel(), _served_on_lines())
        self.assertEqual(check.status, "verified", check.reason)
        self.assertIn("broken_rules", check.reason)

    def test_default_callers_are_unchanged(self):
        check = _check(_open_dotted_panel(), _served_on_lines(), broken_rules=False)
        self.assertEqual(check.status, "unverified", check.reason)

    def test_one_percent_shift_fails(self):
        shift = 0.01 * (R - L)
        check = _check(_open_dotted_panel(), _served_on_lines(shift))
        self.assertEqual(check.status, "failed", check.reason)

    def test_far_tail_shift_never_passes(self):
        for fraction in (0.02, 0.05, 0.25, 0.5):
            check = _check(_open_dotted_panel(), _served_on_lines(fraction * (R - L)))
            self.assertNotEqual(check.status, "verified", (fraction, check.reason))

    def test_labels_without_rules_or_ticks_never_verify(self):
        """Label centres alone are not calibration evidence, dotted level or not."""
        img = _open_dotted_panel(rules=False)
        label_fit = fit_axis_ticks(_x_label_ticks(), "labels", model="linear")
        for served in (label_fit, _served_on_lines()):
            check = _check(img, served)
            self.assertEqual(check.status, "unverified", check.reason)

    def test_curves_and_text_columns_are_not_broken_rules(self):
        img = _open_dotted_panel(rules=False)
        for x in range(L + 20, L + 200):  # steep diagonal
            y = T + 20 + 2 * (x - L)
            img[y : y + 3, x] = 0
        img[T + 50 : T + 70, 500:560] = 0  # a text block
        found = detect_axis_lines(img, orientation="x", along=(L - 5, R + 5),
                                  cross_span=(T + 2, B - 2), max_width=8, tick_band=5,
                                  broken_rules=True)
        self.assertEqual([line.source for line in found if line.source == "broken_rule"], [])


_LIB = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets")


def _render(pdf: Path, page: int, clip) -> np.ndarray:
    import fitz  # type: ignore

    pixmap = fitz.open(pdf)[page - 1].get_pixmap(
        dpi=220, clip=fitz.Rect(*clip), colorspace=fitz.csGRAY
    )
    return np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width)


class RealPanelRailTests(unittest.TestCase):
    """Real C(V) panels: the rail box sits on the drawn frame rules.

    Both panels are bounded by the shared detectors today (the fallback is
    additive and does not run on them); these pin the rail detector's
    geometry on real rendering where the shared boxes are measurably loose.
    """

    def _panel(self, rel: str, page: int, clip) -> np.ndarray:
        pdf = _LIB / rel
        if not pdf.exists():
            self.skipTest(f"{pdf} not in the local datasheet library")
        return _render(pdf, page, clip)

    def _assert_box(self, box, expected, tol=2):
        self.assertIsNotNone(box)
        for got, want in zip((box.x0, box.y0, box.x1, box.y1), expected):
            self.assertLessEqual(abs(got - want), tol, (box, expected))

    def test_nce6008as_frame_includes_the_zero_volt_rail(self):
        # outward ticks: the bottom rail runs left of the 0 V rail, the 0 V
        # rail below the 0 pF rule; the shared detector's box starts at the
        # 5 V gridline (x0=189) and ends at the tick tips (y1=485)
        gray = self._panel("nce/NCE6008AS.pdf", 5, (49.411, 96.864, 279.776, 270.958))
        self._assert_box(rail_plot_box(gray), (90, 24, 679, 475))
        _box, method = find_capacitance_plot_box_with_method(gray)
        self.assertEqual(method, "grid_or_closed_frame")

    def test_irf2804_frame_top_is_above_the_legend(self):
        # the legend block interrupts the upper gridlines; the frame's top
        # rule is at 12000 pF, not the first full gridline below the legend
        gray = self._panel("infineon/IRF2804.pdf", 4, (97.855, 137.455, 307.964, 323.345))
        self._assert_box(rail_plot_box(gray), (94, 36, 614, 532))


if __name__ == "__main__":
    unittest.main()
