"""Known-bad controls for body-diode temperature identity."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pymupdf

from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.diode_forward_voltage import _excluded_wide_curve, digitize_pdf
from datasheet_chart_digitizer.diode_temperature_assignment import (
    LABEL_BOUND,
    ORDER_BOUND,
    ORDER_CONFIRMED,
    LabelEvidence,
    assign_temperatures,
    in_plot_label_evidence,
)
from datasheet_chart_digitizer.numeric_axis import AxisTick, NumericAxis
from datasheet_chart_digitizer.transfer_temperature_labels import TemperatureLabel

# VSD 0..2 V over x px 0..200; current 0..100 A over y px 200..0.
X_AXIS = NumericAxis(
    "linear", 0.01, 0.0, (AxisTick("0", 0.0, 0), AxisTick("2", 2.0, 200)), 0.0, ()
)
Y_AXIS = NumericAxis(
    "linear", -0.5, 100.0, (AxisTick("100", 100.0, 0), AxisTick("0", 0.0, 200)), 0.0, ()
)
CALIBRATION = SimpleNamespace(x_axis=X_AXIS, y_axis=Y_AXIS)
PLOT = PlotBox(0, 0, 200, 200)


def _curve(vsd_of_current) -> list[tuple[int, int]]:
    """Pixel trace of VSD(I) sampled every 0.5 A over 1..100 A."""

    points = []
    for step in range(199):
        current = 1.0 + 0.5 * step
        points.append((round(vsd_of_current(current) / 0.01), round((100.0 - current) / 0.5)))
    return points


COLD = _curve(lambda i: 0.60 + 0.008 * i)  # 25 C
HOT = _curve(lambda i: 0.40 + 0.011 * i)  # 175 C: lower VSD, crosses at ~67 A


class _IdentityTransform:
    def to_px(self, x: float, y: float) -> tuple[float, float]:
        return x, y


def _label(value: float, x: float, y: float, width: float = 30.0) -> TemperatureLabel:
    return TemperatureLabel(value, x, y - 4.0, x + width, y + 4.0, True)


class OrderingAndLabelEvidenceTests(unittest.TestCase):
    def _assign(self, curves, temperatures, evidence=None):
        return assign_temperatures(
            curves, CALIBRATION, temperatures, label_evidence=evidence
        )

    def test_stable_ordering_binds_hot_to_lower_vsd(self):
        curves, crossover, diagnostic = self._assign([COLD, HOT], [25.0, 175.0])
        self.assertEqual(diagnostic, ORDER_BOUND)
        by_temperature = {curve["temperature_c"]: curve["points_px"] for curve in curves}
        self.assertEqual(by_temperature[175.0][0], list(HOT[0]))
        self.assertAlmostEqual(crossover, 66.7, delta=1.5)

    def test_agreeing_labels_confirm_the_ordering(self):
        evidence = LabelEvidence({0: 25.0, 1: 175.0}, 2)
        _, _, diagnostic = self._assign([COLD, HOT], [25.0, 175.0], evidence)
        self.assertEqual(diagnostic, ORDER_CONFIRMED)

    def test_swapped_labels_refuse(self):
        evidence = LabelEvidence({0: 175.0, 1: 25.0}, 2)
        with self.assertRaisesRegex(RuntimeError, "contradict the VSD temperature ordering"):
            self._assign([COLD, HOT], [25.0, 175.0], evidence)
        # One contradicting label is enough.
        with self.assertRaisesRegex(RuntimeError, "contradict"):
            self._assign([COLD, HOT], [25.0, 175.0], LabelEvidence({1: 25.0}, 2))

    def test_label_count_differing_from_curve_count_refuses(self):
        for temperatures in ([25.0], [25.0, 125.0, 175.0]):
            with self.subTest(temperatures=temperatures):
                with self.assertRaisesRegex(RuntimeError, "curve/temperature mismatch"):
                    self._assign([COLD, HOT], temperatures)

    def test_touching_curves_at_the_ordering_current_refuse(self):
        # Hot comes within 15 mV (< the 20 mV margin) of cold at the binding
        # sample (40.6 A) without crossing: the rank there is not evidence.
        touching = _curve(
            lambda i: 0.60 + 0.008 * i - 0.015 - 0.004 * ((i - 40.6) / 10.0) ** 2
        )
        with self.assertRaisesRegex(RuntimeError, "not separated at the ordering current"):
            self._assign([COLD, touching], [25.0, 175.0])

    def test_low_current_crossing_refuses_without_labels(self):
        # Crossing at ~20 A flips the rank inside the low/mid window.
        early = _curve(lambda i: 0.50 + 0.013 * i)
        with self.assertRaisesRegex(RuntimeError, "unstable"):
            self._assign([COLD, early], [25.0, 125.0])

    def test_complete_labels_bind_an_unstable_ordering_only_when_physical(self):
        early = _curve(lambda i: 0.50 + 0.013 * i)  # hot first, one crossing
        curves, _, diagnostic = self._assign(
            [COLD, early], [25.0, 125.0], LabelEvidence({0: 25.0, 1: 125.0}, 2)
        )
        self.assertEqual(diagnostic, LABEL_BOUND)
        self.assertEqual(
            {curve["temperature_c"]: curve["points_px"][0] for curve in curves}[125.0],
            list(early[0]),
        )
        # Swapped labels put the hot label on the curve with the higher
        # low-current VSD: physics refuses.
        with self.assertRaisesRegex(RuntimeError, "unstable"):
            self._assign([COLD, early], [25.0, 125.0], LabelEvidence({0: 125.0, 1: 25.0}, 2))
        # Partial label evidence cannot bind an unstable ordering.
        with self.assertRaisesRegex(RuntimeError, "unstable"):
            self._assign([COLD, early], [25.0, 125.0], LabelEvidence({1: 125.0}, 2))


class InPlotLabelEvidenceTests(unittest.TestCase):
    def _evidence(self, labels):
        return in_plot_label_evidence(labels, _IdentityTransform(), PLOT, [COLD, HOT])

    def test_label_next_to_one_curve_binds_it(self):
        # At y=150 (25 A): cold at x=80, hot at x=67.5.  A label right of the
        # cold curve is nearest cold and far from hot.
        evidence = self._evidence([_label(25.0, 84.0, 150.0)])
        self.assertEqual(evidence.by_curve, {0: 25.0})

    def test_label_between_curves_gives_no_evidence(self):
        # At 25 A hot is at x=67.5 and cold at x=80: a narrow label in the gap.
        evidence = self._evidence([_label(25.0, 70.0, 150.0, width=8.0)])
        self.assertEqual(evidence.by_curve, {})

    def test_zero_or_one_curve_gives_no_evidence_and_count_check_refuses(self):
        for curves in ([], [COLD]):
            with self.subTest(curves=len(curves)):
                evidence = in_plot_label_evidence(
                    [_label(25.0, 84.0, 150.0)], _IdentityTransform(), PLOT, curves
                )
                self.assertEqual(evidence.by_curve, {})
                with self.assertRaisesRegex(RuntimeError, "curve/temperature mismatch"):
                    assign_temperatures(
                        curves, CALIBRATION, [25.0, 175.0], label_evidence=evidence
                    )

    def test_leader_tip_names_the_curve_not_the_curve_under_the_label(self):
        # onsemi NTMFS5C456NL Fig 10: `TJ = 25°C` sits on the 125 °C curve
        # and its leader arrow ends on the 25 °C curve.
        on_hot = _label(25.0, 60.0, 150.0, width=12.0)  # hot passes x=67.5
        self.assertEqual(self._evidence([on_hot]).by_curve, {1: 25.0})
        leader = ((73.0, 150.0), (85.0, 138.0))  # tip on cold (x=84.8 at 31 A)
        evidence = in_plot_label_evidence(
            [on_hot], _IdentityTransform(), PLOT, [COLD, HOT], leaders=[leader]
        )
        self.assertEqual(evidence.by_curve, {0: 25.0})
        # Two leaders from one label: no evidence.
        other = ((73.0, 146.0), (62.0, 120.0))
        evidence = in_plot_label_evidence(
            [on_hot], _IdentityTransform(), PLOT, [COLD, HOT], leaders=[leader, other]
        )
        self.assertEqual(evidence.by_curve, {})

    def test_legend_entry_with_swatch_gives_no_position_evidence(self):
        label = _label(25.0, 84.0, 150.0)
        swatch = ((120.0, 150.0), (136.0, 150.0))
        evidence = in_plot_label_evidence(
            [label], _IdentityTransform(), PLOT, [COLD, HOT], swatches=[swatch]
        )
        self.assertEqual((evidence.by_curve, evidence.labels_in_plot), ({}, 1))

    def test_label_outside_plot_is_ignored(self):
        evidence = self._evidence([_label(25.0, 84.0, 230.0)])
        self.assertEqual((evidence.by_curve, evidence.labels_in_plot), ({}, 0))

    def test_contradictory_claims_are_dropped(self):
        # Two temperatures claim the cold curve; one temperature claims both.
        both_on_cold = [_label(25.0, 84.0, 150.0), _label(175.0, 84.0, 170.0)]
        self.assertEqual(self._evidence(both_on_cold).by_curve, {})
        # Second 25 C label left of the hot curve at 5 A (hot x=45.5, cold x=64).
        one_value_twice = [_label(25.0, 84.0, 150.0), _label(25.0, 10.0, 190.0)]
        self.assertNotIn(25.0, self._evidence(one_value_twice).by_curve.values())


class RealDatasheetLabelEvidenceTests(unittest.TestCase):
    DATASHEETS = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/onsemi")

    def _digitize(self, name):
        pdf = self.DATASHEETS / name
        if not pdf.exists():
            self.skipTest(f"missing local corpus PDF: {pdf}")
        with tempfile.TemporaryDirectory() as tmp:
            (result,) = digitize_pdf(pdf, Path(tmp), dpi=220)
        return result

    def test_leader_labels_confirm_the_ordering(self):
        result = self._digitize("NTMFS5C456NLT1G.pdf")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["diagnostics"][0], ORDER_CONFIRMED)
        self.assertEqual([c["temperature_c"] for c in result["curves"]], [-55.0, 25.0, 125.0])

    def test_in_plot_legend_is_not_position_evidence(self):
        result = self._digitize("NTMFS2D3N04XMT1G.pdf")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["diagnostics"][0], ORDER_BOUND)


class WideCurveStrokeGuardTests(unittest.TestCase):
    RECT = pymupdf.Rect(0.0, 0.0, 100.0, 100.0)

    def _drawing(self, points, width):
        items = [
            ("l", pymupdf.Point(*left), pymupdf.Point(*right))
            for left, right in zip(points, points[1:])
        ]
        return {"type": "s", "color": (0.0, 0.0, 0.0), "width": width, "items": items}

    def test_thick_curve_shaped_stroke_is_reported(self):
        curve = [(10.0 + 60.0 * math.sqrt(t / 20.0), 98.0 - 4.8 * t) for t in range(21)]
        self.assertTrue(_excluded_wide_curve(self._drawing(curve, 2.86), self.RECT, False, 3))
        # Inside the width gate it is an ordinary curve, not a guard hit.
        self.assertFalse(_excluded_wide_curve(self._drawing(curve, 1.5), self.RECT, False, 3))

    def test_thick_frame_rail_is_not_a_curve(self):
        rail = [(0.0, 0.0), (0.0, 100.0)]
        self.assertFalse(_excluded_wide_curve(self._drawing(rail, 3.0), self.RECT, False, 3))


if __name__ == "__main__":
    unittest.main()
