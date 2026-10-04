"""Binding VDD/VDS labels to separated gate-charge curves, or refusing (synthetic pages)."""

from __future__ import annotations

import unittest

import pymupdf

from datasheet_chart_digitizer.gate_charge_separation import separate_vector_curves
from datasheet_chart_digitizer.gate_charge_vdd_labels import Label, bind_labels, parse_labels
from gate_charge_vector_fixtures import CURVES, PLOT, RECT, _page, _stroke, _word, _x_at


class LabelTests(unittest.TestCase):
    def test_parse_voltage_labels(self) -> None:
        words = [
            _word("VDS", 10, 10, 30, 20), _word("=", 32, 10, 36, 20), _word("5", 38, 10, 44, 20), _word("V", 46, 10, 52, 20),
            _word("12", 10, 40, 22, 50), _word("V", 24, 40, 30, 50),
            _word("VDD", 10, 70, 30, 80), _word("=32V", 32, 70, 60, 80),
            _word("VGS", 10, 100, 30, 110), _word("=", 32, 100, 36, 110), _word("10", 38, 100, 50, 110), _word("V", 52, 100, 58, 110),
            _word("ID", 10, 130, 20, 140), _word("=", 22, 130, 26, 140), _word("8.3", 28, 130, 40, 140), _word("A", 42, 130, 48, 140),
        ]
        labels = parse_labels(_page([], words), pymupdf.Rect(0, 0, 400, 400))
        self.assertEqual([(l.value, l.explicit) for l in labels], [(5.0, True), (12.0, False), (32.0, True)])

    def _bind(self, drawings, labels):
        sep = separate_vector_curves(_page([_stroke(c) for c in CURVES] + drawings), RECT, 1.0, PLOT)
        curves_px = [c.points_px for c in sep.curves]
        return bind_labels(labels, sep, curves_px, RECT, 1.0, PLOT)

    def test_leaders_bind_and_physics_agrees(self) -> None:
        labels, drawings = [], []
        for value, curve, y in ((10, CURVES[0], 120), (20, CURVES[1], 140), (30, CURVES[2], 160)):
            box = (360, y - 40, 385, y - 30)
            labels.append(Label(value, f"{value} V", box, False))
            drawings.append(_stroke([(box[0] - 1, y - 35), (_x_at(curve, y), y)], width=0.3))
        binding = self._bind(drawings, labels)
        self.assertEqual([l.curve for l in labels], [0, 1, 2])
        self.assertEqual({l.rule for l in labels}, {"leader"})
        self.assertEqual(binding.physics, "consistent")

    def test_shaft_crossing_a_curve_binds_the_curve_at_its_tip(self) -> None:
        # the 20 V arrow crosses the 30 V curve on its way to the 20 V one
        y = 150
        box = (380, y - 5, 398, y + 5)
        label = Label(20, "20 V", box, False)
        shaft = _stroke([(379, y), (_x_at(CURVES[1], y), y)], width=0.3)
        self._bind([shaft], [label])
        self.assertEqual(label.curve, 1)

    def test_outer_side_labels(self) -> None:
        left = Label(10, "10 V", (150, 95, 175, 105), False)   # left of the bundle at y=100
        right = Label(30, "30 V", (380, 95, 398, 105), False)  # right of it
        binding = self._bind([], [left, right])
        self.assertEqual((left.curve, left.rule), (0, "outer_side_left"))
        self.assertEqual((right.curve, right.rule), (2, "outer_side_right"))
        self.assertEqual(binding.physics, "consistent")  # 2 labels on 3 curves: order check

    def test_label_inside_the_bundle_is_refused(self) -> None:
        x = (_x_at(CURVES[0], 100) + _x_at(CURVES[1], 100)) / 2
        label = Label(20, "20 V", (x - 2, 98, x + 2, 102), False)
        self._bind([], [label])
        self.assertIsNone(label.curve)

    def test_contradicting_physics_refuses_every_binding(self) -> None:
        # print puts 30 V on the left and 10 V on the right: refuse, never reorder
        left = Label(30, "30 V", (150, 95, 175, 105), False)
        right = Label(10, "10 V", (380, 95, 398, 105), False)
        binding = self._bind([], [left, right])
        self.assertIsNone(left.curve)
        self.assertIsNone(right.curve)
        self.assertEqual(binding.physics, "contradicted")

    def test_lone_binding_checked_by_rank(self) -> None:
        # three curves, three printed values: a lone label bound to the wrong
        # rank is refused even though nothing else is bound (FDMS4D5N08LC)
        labels = [Label(10, "10 V", (5, 5, 20, 12), False), Label(20, "20 V", (5, 20, 20, 27), False),
                  Label(30, "30 V", (380, 95, 398, 105), False)]
        labels[2].value = 20.0  # make the right-hand label 20 V -> rank 1 on curve 2
        labels[1].value = 30.0
        binding = self._bind([], labels)
        self.assertTrue(all(l.curve is None for l in labels))
        self.assertEqual(binding.physics, "contradicted")

    def test_two_labels_on_one_curve_refuses_both(self) -> None:
        a = Label(10, "10 V", (150, 95, 175, 105), False)
        b = Label(20, "20 V", (150, 115, 175, 125), False)
        binding = self._bind([], [a, b])
        self.assertIsNone(a.curve)
        self.assertIsNone(b.curve)
        self.assertIn("vdd_binding_conflict:two_labels_on_one_curve", binding.diagnostics)

    def test_legend_binds_by_stroke_style(self) -> None:
        dashed = "[ 5 2 ] 0"
        drawings = [_stroke(CURVES[0]), _stroke(CURVES[1], dashes=dashed), _stroke(CURVES[2], dashes="[ 1 1 ] 0")]
        samples = [_stroke([(20, 30), (40, 30)]), _stroke([(20, 45), (40, 45)], dashes=dashed)]
        sep = separate_vector_curves(_page(drawings + samples), RECT, 1.0, PLOT)
        labels = [Label(20, "20 V", (45, 25, 60, 35), False), Label(50, "50 V", (45, 40, 60, 50), False)]
        bind_labels(labels, sep, [c.points_px for c in sep.curves], RECT, 1.0, PLOT)
        self.assertEqual([(l.curve, l.rule) for l in labels], [(0, "legend"), (1, "legend")])

    def test_fraction_of_vds_max_is_not_a_voltage(self) -> None:
        # BSS83P: "0,2 V_DS max" means 0.2 x VDS(max)
        words = [_word("0,2", 10, 10, 20, 20), _word("V", 21, 10, 26, 20), _word("DS", 26, 14, 34, 21), _word("max", 35, 14, 45, 21)]
        self.assertEqual(parse_labels(_page([], words), pymupdf.Rect(0, 0, 400, 400)), [])

    def test_label_written_as_one_word(self) -> None:
        labels = parse_labels(_page([], [_word("VDS=40V", 10, 10, 40, 20)]), pymupdf.Rect(0, 0, 400, 400))
        self.assertEqual([(l.value, l.explicit) for l in labels], [(40.0, True)])

    def test_outer_side_label_on_a_sloped_plateau_is_refused(self) -> None:
        # synthetic syn7_0000: plateaus rise 10 px; a label row inside that band
        sloped = [[(0, 400), (100, 250), (x, 240), (x + 170, 20)] for x in (130, 160, 190)]
        sep = separate_vector_curves(_page([_stroke(c) for c in sloped]), RECT, 1.0, PLOT)
        label = Label(10, "10 V", (20, 236, 60, 244), False)
        bind_labels([label], sep, [c.points_px for c in sep.curves], RECT, 1.0, PLOT)
        self.assertIsNone(label.curve)


if __name__ == "__main__":
    unittest.main()
