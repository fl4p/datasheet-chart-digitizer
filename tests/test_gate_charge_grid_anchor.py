"""gate_charge_grid_anchor: VGS labels identify values, rules carry pixels.

Known-bad: VGS label centres 3 px below their rules (0..10 V, 2 V steps). The
label-centre line is reported failed; seating serves every rule and verifies;
a raster without rules is unverified and keeps the labels (the caller then
refuses "ok").
"""

from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer.gate_charge_grid_anchor import (
    check_gate_y_on_grid,
    seat_gate_y_ticks,
    served_value,
)

PLOT = (80, 40, 520, 440)
VALUES = [10.0, 8.0, 6.0, 4.0, 2.0, 0.0]


def _rule_px(v):  # continuous px; drawn as a 1 px rule at index px - 0.5
    return 40.5 + (440.0 - 40.0) * (10.0 - v) / 10.0


def _panel():
    image = np.full((480, 560), 255, dtype=np.uint8)
    for v in VALUES:
        image[int(_rule_px(v) - 0.5), PLOT[0]:PLOT[2] + 1] = 0
    image[PLOT[1]:PLOT[3] + 1, PLOT[0]] = 0
    image[PLOT[1]:PLOT[3] + 1, PLOT[2]] = 0
    return image


LABELS = [(v, _rule_px(v) + 3.0) for v in VALUES]


class GateChargeGridAnchorTests(unittest.TestCase):
    def test_label_centre_line_fails(self):
        check = check_gate_y_on_grid(_panel(), LABELS, LABELS, PLOT)
        self.assertEqual(check.status, "failed", check.reason)

    def test_seating_serves_the_rules(self):
        seated = seat_gate_y_ticks(_panel(), LABELS, PLOT)
        self.assertEqual(seated.check.status, "verified", seated.check.reason)
        for value, px in seated.ticks_px:
            self.assertAlmostEqual(px, _rule_px(value), delta=0.6)
        # Vpl read at the 5 V rule position is 5 V, not 5.075 V
        self.assertAlmostEqual(served_value(seated.ticks_px, 240.5), 5.0, delta=0.02)
        self.assertAlmostEqual(served_value(LABELS, 240.5), 5.075, delta=0.01)

    def test_no_rules_is_unverified_and_keeps_labels(self):
        blank = np.full((480, 560), 255, dtype=np.uint8)
        seated = seat_gate_y_ticks(blank, LABELS, PLOT)
        self.assertEqual(seated.check.status, "unverified")
        self.assertEqual(list(seated.ticks_px), LABELS)
        self.assertIsNotNone(seated.anchor_error)


if __name__ == "__main__":
    unittest.main()
