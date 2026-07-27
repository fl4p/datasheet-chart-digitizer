"""A plot must calibrate against ITS OWN y-axis label column, not a neighbour's.

Side-by-side charts share gridline rows, so their tick labels are separable by
COLUMN and never by row. Selecting the label run with the most entries -- which is
what ``_local_y_ticks_for_plot`` used to do -- therefore picks whichever chart
happens to be better labelled, and on a tie picks the leftmost, i.e. the neighbour.

The failure is silent by construction: a neighbour's axis is still linear and still
descending, so it calibrates cleanly and produces a plausible number. EPC2934C read
Vpl = 4.97 V for a 2.15 V plateau (its Coss stored-energy neighbour is labelled
0.0 .. 12.0 uJ against the gate chart's 0 .. 5 V) with status "ok" and no
diagnostic. Nothing downstream could tell.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import pymupdf

from datasheet_chart_digitizer.gate_charge_estimation import _local_y_ticks_for_plot


EPC2934C = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/epc/EPC2934C.pdf")

# A plot frame with room for label columns on either side. scale=1 keeps pixel and
# point coordinates identical, so the numbers below read as the geometry they are.
RECT = pymupdf.Rect(0.0, 0.0, 400.0, 400.0)
PLOT_BOX = (100, 20, 300, 320)
ROW_YS = (20.0, 80.0, 140.0, 200.0, 260.0, 320.0)


def _column(values, centre_x: float, row_ys=ROW_YS):
    """Numeric labels stacked in one column, as pymupdf ``words`` tuples."""

    return [
        (centre_x - 3.0, y - 4.0, centre_x + 3.0, y + 4.0, str(value))
        for value, y in zip(values, row_ys)
    ]


def _page(words, *, text_source: str = "pdftotext"):
    return SimpleNamespace(get_text=lambda _kind: list(words), text_source=text_source)


class NeighbourAxisColumnTests(unittest.TestCase):
    def test_own_column_wins_over_an_equally_long_neighbour(self):
        """The EPC2934C geometry: two six-label columns, tie on count.

        Direction matters as much as firing here. A selector that simply flipped to
        "rightmost" would pass this too, so the assertion names WHICH axis survived:
        0..5 V (seated 6 pt from the frame), not 0..12 uJ (60 pt out).
        """

        page = _page(
            _column((12.0, 9.6, 7.2, 4.8, 2.4, 0.0), 40.0)   # neighbour, 60 pt out
            + _column((5, 4, 3, 2, 1, 0), 94.0)              # own axis, 6 pt out
        )

        ticks = _local_y_ticks_for_plot(page, RECT, 1.0, PLOT_BOX)

        self.assertEqual([value for value, _y in ticks], [5.0, 4.0, 3.0, 2.0, 1.0, 0.0])
        self.assertEqual(max(value for value, _y in ticks), 5.0)

    def test_own_column_wins_even_with_fewer_labels(self):
        """Count must not outrank adjacency.

        Half the point of the fix: when only part of a plot's own axis parses, the
        answer is its own partial axis, not the neighbour's complete one. Preferring
        the fuller foreign column is precisely the bug, just with a wider margin.
        """

        page = _page(
            _column((12.0, 9.6, 7.2, 4.8, 2.4, 0.0), 40.0)
            + _column((5, 3, 1), 94.0, row_ys=(20.0, 140.0, 260.0))
        )

        ticks = _local_y_ticks_for_plot(page, RECT, 1.0, PLOT_BOX)

        self.assertEqual([value for value, _y in ticks], [5.0, 3.0, 1.0])

    def test_a_nearer_column_wins_at_subplot_spacing_too(self):
        """No tolerance window around the nearest gap.

        Bucketing the gap (an earlier form of this fix used ``round(gap / 6.0)``) lets
        two columns share a bucket, at which point count decides again and the farther
        column wins -- the original bug at a tighter spacing.

        It has to be an OPPOSITE-side pair to be reachable: within one side, distinct
        columns are already >6 pt apart (that is the clustering threshold), so their
        gaps are too, and no two can share a 6 pt bucket. Across sides nothing couples
        them, and this layout is ordinary -- our own axis 3.5 pt left of our frame,
        and the next chart in a 2-up row putting ITS y labels 8.9 pt right of ours.
        """

        page = _page(
            _column((5, 0), 96.5, row_ys=(20.0, 320.0))          # own, left, 3.5 pt, 2 labels
            + _column((9.6, 7.2, 4.8, 2.4, 0.0), 308.9, row_ys=ROW_YS[1:])  # right, 8.9 pt, 5
        )

        ticks = _local_y_ticks_for_plot(page, RECT, 1.0, PLOT_BOX)

        self.assertEqual([value for value, _y in ticks], [5.0, 0.0])

    def test_right_hand_axis_is_measured_against_the_right_frame_edge(self):
        """A right-side axis is nearest to x1, not to x0 -- the owning edge is
        per-side. Measuring both sides against the left edge would invert this case
        and hand a right-hand plot its far neighbour."""

        page = _page(
            _column((5, 4, 3, 2, 1, 0), 306.0)               # own axis, 6 pt right
            + _column((12.0, 9.6, 7.2, 4.8, 2.4, 0.0), 360.0)  # neighbour, 60 pt out
        )

        ticks = _local_y_ticks_for_plot(page, RECT, 1.0, PLOT_BOX)

        self.assertEqual([value for value, _y in ticks], [5.0, 4.0, 3.0, 2.0, 1.0, 0.0])

    def test_single_column_charts_are_unchanged(self):
        """The no-neighbour case is the overwhelming majority of the corpus. It must
        read exactly as before, otherwise this fix is a corpus-wide rescale wearing a
        bug fix's clothes."""

        page = _page(_column((5, 4, 3, 2, 1, 0), 94.0))

        ticks = _local_y_ticks_for_plot(page, RECT, 1.0, PLOT_BOX)

        self.assertEqual([value for value, _y in ticks], [5.0, 4.0, 3.0, 2.0, 1.0, 0.0])

    def test_unreadable_text_layer_yields_no_ticks(self):
        """When the words cannot be read there is no axis, and the caller tests
        ``len(...) >= 2`` to decide whether one was measured. This used to return the
        ``{"left": [], "right": []}`` accumulator, whose length is 2 -- an unreadable
        page presenting itself as a calibrated axis."""

        def explode(_kind):
            raise RuntimeError("no text layer")

        ticks = _local_y_ticks_for_plot(
            SimpleNamespace(get_text=explode, text_source="pdftotext"),
            RECT,
            1.0,
            PLOT_BOX,
        )

        self.assertEqual(ticks, [])
        self.assertLess(len(ticks), 2)


class ExtrapolatedPlateauTests(unittest.TestCase):
    """Two ticks fix a line, but that line is only evidence BETWEEN them."""

    RECT = pymupdf.Rect(0.0, 0.0, 400.0, 400.0)
    TICKS = [(20.0, 100.0), (2.0, 200.0)]   # calibrated span: y 100..200 (pdf points)

    def _extrapolated(self, vpl_y_px, ticks=None):
        from datasheet_chart_digitizer.gate_charge import _vpl_extrapolated_beyond_ticks

        return _vpl_extrapolated_beyond_ticks(
            vpl_y_px, self.TICKS if ticks is None else ticks, self.RECT, 1.0
        )

    def test_plateau_inside_the_tick_span_is_measured(self):
        self.assertFalse(self._extrapolated(150.0))
        self.assertFalse(self._extrapolated(100.0))
        self.assertFalse(self._extrapolated(200.0))

    def test_plateau_past_the_last_tick_is_extrapolated(self):
        # SUP90140E's shape: the plateau pixel sits well below the lowest tick, where
        # the fit's error grows without bound while its residuals stay perfect.
        self.assertTrue(self._extrapolated(320.0))
        self.assertTrue(self._extrapolated(-50.0))

    def test_small_overshoot_is_tolerated(self):
        """A plateau a hair outside the labelled span is ordinary (ticks rarely reach
        the frame). The tolerance is a fraction of the SPAN, so a sparse axis is not
        judged by the same absolute slack as a dense one."""
        self.assertFalse(self._extrapolated(205.0))    # 5% of a 100 px span
        self.assertTrue(self._extrapolated(215.0))     # 15%

    def test_nothing_to_judge_is_not_a_pass(self):
        """Returns False for "cannot evaluate", which here is correct ONLY because
        those states carry their own diagnostics (vpl_unresolved, axis_assumed_0_10).
        Pinned so a future edit cannot quietly make this the sole guard for them."""
        self.assertFalse(self._extrapolated(None))
        self.assertFalse(self._extrapolated(150.0, ticks=[(5.0, 100.0)]))
        self.assertFalse(self._extrapolated(150.0, ticks=[]))


SUP90140E = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/vishay/SUP90140E.pdf")


@unittest.skipUnless(SUP90140E.exists(), "local SUP90140E datasheet unavailable")
class Sup90140eStatusTests(unittest.TestCase):
    def test_an_implausible_reading_is_not_reported_as_ok(self):
        """status is a provenance claim and must not claim more than was done.

        This panel produced 28.6 V for a part whose gate never leaves +-20 V, off two
        ticks spanning 48% of the plot box, and reported status "ok" WITH
        vpl_outside_expected_range already in its diagnostics. Every consumer that keys
        on status -- fetlib's read_charts among them -- stored it as a clean reading.
        """

        from datasheet_chart_digitizer.gate_charge import (
            digitize_gate_charge,
            find_vpl_result,
        )

        results = digitize_gate_charge(SUP90140E, dpi=220, finder_dpi=120)
        panel = next(r for r in results if r.vpl is not None)

        self.assertGreater(panel.vpl, 20.0, "the wrong value is still produced ...")
        self.assertNotEqual(panel.status, "ok", "... it must simply not be called ok")
        self.assertIn("vpl_outside_expected_range", panel.diagnostics)
        self.assertIn("vpl_extrapolated_beyond_ticks", panel.diagnostics)

        # and the scalar API, which serves only status == "ok", now serves nothing here
        self.assertIsNone(find_vpl_result(str(SUP90140E)))


@unittest.skipUnless(EPC2934C.exists(), "local EPC2934C datasheet unavailable")
class Epc2934cGateChargeAxisTests(unittest.TestCase):
    def test_gate_charge_reads_its_own_volt_axis(self):
        """End-to-end on the datasheet that surfaced this.

        The plateau PIXEL was always right (vpl_y_px 472 of a 176..696 frame); only
        the calibration was wrong, so this pins the calibrated value AND the axis it
        came from. 4.97 is asserted against explicitly: a regression here returns a
        clean-looking number, not an error."""

        from datasheet_chart_digitizer.gate_charge import find_vpl_result

        result = find_vpl_result(str(EPC2934C))

        self.assertIsNotNone(result)
        self.assertEqual(result.status, "ok")
        self.assertEqual(
            [value for value, _pixel in result.y_ticks_px],
            [5.0, 4.0, 3.0, 2.0, 1.0, 0.0],
            "gate chart must calibrate on its own 0..5 V axis, not the Coss "
            "stored-energy chart's 0..12 uJ axis to its left",
        )
        self.assertAlmostEqual(result.vpl, 2.1509, delta=0.01)
        self.assertLess(result.vpl, 5.0, "a plateau at the 5 V drive rail is the bug")


if __name__ == "__main__":
    unittest.main()
