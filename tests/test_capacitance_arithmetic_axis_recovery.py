"""Arithmetic (linear) capacitance Y axes: unit ownership, log disproof, rules.

Three defects kept every Chinese-vendor linear C(V) panel (goford, huayi, NCE,
Sinopower) out of the digitizer, and one of them served silently WRONG numbers:

* the pF unit is printed as part of the rotated axis TITLE ("C-Capacitance(pF)"),
  which is a single word in the PyMuPDF stream and therefore never matched the
  standalone-token unit test;
* a 0..10000 pF ARITHMETIC ladder contains two decade-valued labels (1000 and
  10000).  Reading just those two as a log axis produced a trusted calibration
  whose Ciss was +20583 % against the part's own spec table (HYG030N10NS1P);
* the horizontal-rule detector opened the image with a long horizontal kernel,
  which erases dotted grids entirely and cannot see a solid rule that a curve
  LABEL interrupts, while merging a near-horizontal TRACE into the rule it
  touches and dragging its center ~2 px off.

Each test below also pins the known-bad input the guard exists to catch.
"""
from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer import axis_calibration as ac
from datasheet_chart_digitizer import capacitance_axis as ca
from datasheet_chart_digitizer.capacitance_types import PlotBox


class CapacitanceUnitTokenTests(unittest.TestCase):
    def test_axis_titles_own_their_wrapped_unit(self) -> None:
        for text in ("pF", "(pF)", "[nF]", "Capacitance(pF)",
                     "C-Capacitance(pF)", "C(pF)"):
            with self.subTest(text=text):
                self.assertIn(ac._capacitance_unit_token(text), {"pf", "nf"})

    def test_numeric_annotations_never_donate_the_unit(self) -> None:
        # A plotted annotation is not an axis declaration; admitting one would
        # let a legend decide the physical scale of the whole trace.
        for text in ("5000pF", "Ciss=5000pF", "1000(pF)", "f=1MHz", "pFoo",
                     "10pF/div"):
            with self.subTest(text=text):
                self.assertIsNone(ac._capacitance_unit_token(text))

    def test_conflicting_units_still_refuse_a_linear_fit(self) -> None:
        labels = [(1000.0, 400.0), (2000.0, 300.0), (3000.0, 200.0), (4000.0, 100.0)]
        self.assertIsNotNone(ac._linear_capacitance_y_fit(labels, {"pf"}))
        self.assertIsNone(ac._linear_capacitance_y_fit(labels, {"pf", "nf"}))
        self.assertIsNone(ac._linear_capacitance_y_fit(labels, set()))


class DecadeLadderDisproofTests(unittest.TestCase):
    """`yd` holds (exponent, pixel); `yd_numeric` holds (value, pixel)."""

    def test_arithmetic_ladder_disproves_its_own_decade_subset(self) -> None:
        # HYG030N10NS1P: 1000..10000 pF evenly spaced, so 1000 and 10000 are
        # decade-VALUED but the axis is linear.
        numeric = [(1000.0 * n, 391.2 - 38.805 * (n - 1)) for n in range(1, 11)]
        decades = [(3.0, 391.2), (4.0, 41.955)]
        self.assertTrue(ac._decade_ladder_is_contradicted(decades, numeric))

    def test_real_decade_axis_is_not_contradicted(self) -> None:
        numeric = [(100.0, 300.0), (1000.0, 200.0), (10000.0, 100.0)]
        decades = [(2.0, 300.0), (3.0, 200.0), (4.0, 100.0)]
        self.assertFalse(ac._decade_ladder_is_contradicted(decades, numeric))

    def test_one_stray_ocr_fragment_cannot_veto_a_decade_axis(self) -> None:
        # IAUTN15S6N025ATMA1: OCR emits a spurious "2" on top of the 10^3
        # label. A residual-only disproof vetoed the (correct) log axis and
        # lost three already-verified extractions; positive ladder evidence
        # does not.
        numeric = [
            (100000.0, 135.1), (10000.0, 195.7), (2.0, 255.7),
            (1000.0, 256.2), (100.0, 316.8), (10.0, 377.4),
        ]
        decades = [(5.0, 135.1), (4.0, 195.7), (3.0, 256.2), (2.0, 316.8), (1.0, 377.4)]
        self.assertFalse(ac._decade_ladder_is_contradicted(decades, numeric))


def _blank(height: int = 200, width: int = 400) -> np.ndarray:
    return np.full((height, width), 255, dtype=np.uint8)


class HorizontalGridlineDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = PlotBox(20, 10, 380, 190)

    def test_dotted_rule_is_a_rule(self) -> None:
        image = _blank()
        image[100, 20:380:2] = 0          # dotted: half the columns inked
        image[101, 20:380:2] = 0
        (center,) = ca._horizontal_gridline_candidates(image, self.plot)
        self.assertAlmostEqual(center, 101.0)

    def test_rule_interrupted_by_a_curve_label_survives(self) -> None:
        image = _blank()
        image[100, 20:380] = 0
        image[100, 170:230] = 255         # a printed "Ciss" sits on the rule
        self.assertEqual(ca._horizontal_gridline_candidates(image, self.plot), [100.5])

    def test_flat_trace_touching_a_rule_does_not_move_its_center(self) -> None:
        # GT045N10T: the flat Ciss curve merged with the 6000 pF rule into one
        # 7 px contour whose center missed the rule by ~2 px -- enough to fail
        # the 1 px seating-fit gate on an otherwise perfect axis.
        image = _blank()
        for row in (100, 101, 102, 103):
            image[row, 20:300] = 0        # trace: dense but not full width
        for row in (104, 105, 106):
            image[row, 20:380] = 0        # the rule
        (center,) = ca._horizontal_gridline_candidates(image, self.plot)
        # Inside the rule's own stroke (rows 104-106, center 105.5), not the
        # 103.5 midpoint of the merged trace+rule run the old detector reported.
        self.assertGreater(center, 104.5)
        self.assertLess(center, 106.0)

    def test_short_dense_segment_is_not_a_rule(self) -> None:
        image = _blank()
        image[100, 20:120] = 0            # legend underline, ~28 % of the span
        self.assertEqual(ca._horizontal_gridline_candidates(image, self.plot), [])

    def test_filled_band_is_not_a_rule(self) -> None:
        image = _blank()
        image[100:140, 20:380] = 0
        self.assertEqual(ca._horizontal_gridline_candidates(image, self.plot), [])

    def test_neighbouring_rules_without_a_blank_row_stay_separate(self) -> None:
        # Densely gridded log panels (AGM15T06C) leave curve ink between two
        # rules; collapsing or discarding the run loses real major gridlines.
        image = _blank()
        image[100:104, 20:380] = 0
        image[104:110, 20:320:2] = 0      # curve/label ink bridging the gap
        # 40 % coverage: dense enough to keep the run merged, too faint to be
        # mistaken for either rule's own antialiased edge (half-peak floor).
        image[110:114, 20:380] = 0
        self.assertEqual(
            ca._horizontal_gridline_candidates(image, self.plot), [102.0, 112.0]
        )


if __name__ == "__main__":
    unittest.main()
