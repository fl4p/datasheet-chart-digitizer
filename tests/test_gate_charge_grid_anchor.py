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
    check_gate_x_on_grid,
    check_gate_y_on_grid,
    seat_gate_x_ticks,
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


# --- Qg (x) axis: the same served-on-grid check as VGS (2026-10-05) ---------

QG_VALUES = [0.0, 40.0, 80.0, 120.0, 160.0, 200.0]


def _qg_rule_px(v):  # continuous px; a 1 px vertical rule at index px - 0.5
    return 80.5 + (520.0 - 80.0) * v / 200.0


def _qg_panel():
    image = _panel()
    for v in QG_VALUES:
        image[PLOT[1]:PLOT[3] + 1, int(_qg_rule_px(v) - 0.5)] = 0
    return image


QG_TRUE = [(v, _qg_rule_px(v)) for v in QG_VALUES]
# label glyph centres 4 px right of their rules, the corner "0" 9 px left
QG_LABELS = [(v, _qg_rule_px(v) + (4.0 if v else -9.0)) for v in QG_VALUES]


def _shifted(ticks, dx):
    return [(v, p + dx) for v, p in ticks]


def _scaled(ticks, factor, origin=80.5):
    return [(v, origin + (p - origin) * factor) for v, p in ticks]


class GateChargeQgAxisTests(unittest.TestCase):
    def test_served_rules_verify(self):
        check = check_gate_x_on_grid(_qg_panel(), QG_TRUE, QG_LABELS, PLOT)
        self.assertEqual(check.status, "verified", check.reason)
        self.assertLessEqual(check.max_abs_error_px, 0.6)

    def test_seating_serves_the_rules_not_the_glyphs(self):
        seated = seat_gate_x_ticks(_qg_panel(), QG_LABELS, PLOT)
        self.assertTrue(seated.anchored, seated.anchor_error)
        self.assertEqual(seated.check.status, "verified", seated.check.reason)
        for value, px in seated.ticks_px:
            self.assertAlmostEqual(px, _qg_rule_px(value), delta=0.6)

    def test_label_glyph_line_fails(self):
        check = check_gate_x_on_grid(_qg_panel(), QG_LABELS, QG_LABELS, PLOT)
        self.assertEqual(check.status, "failed", check.reason)

    def test_known_bad_shift_and_scale_fail_monotonically(self):
        # a correct mapping shifted or stretched fails, and keeps failing as
        # the error grows (far tail: a whole label pitch)
        for dx in (2.5, 4.0, 8.0, 16.6, 44.0, 88.0, -3.0, -88.0):
            check = check_gate_x_on_grid(_qg_panel(), _shifted(QG_TRUE, dx), QG_LABELS, PLOT)
            self.assertEqual(check.status, "failed", (dx, check.reason))
        for factor in (1.02, 1.05, 1.2, 0.96, 0.5):
            check = check_gate_x_on_grid(_qg_panel(), _scaled(QG_TRUE, factor), QG_LABELS, PLOT)
            self.assertEqual(check.status, "failed", (factor, check.reason))

    def test_rjk1001_main_shape_fails(self):
        # main's RJK1001 shape: origin bound to the VGS corner "0" glyph, the
        # run stretched to the 200 nC rule (+16.6 ... -0.4 px)
        bad = [(v, _qg_rule_px(v) - 16.6 * (1 - v / 200.0)) for v in QG_VALUES]
        check = check_gate_x_on_grid(_qg_panel(), bad, QG_LABELS, PLOT)
        self.assertEqual(check.status, "failed", check.reason)

    def test_unevaluable_is_unverified(self):
        blank = np.full((480, 560), 255, dtype=np.uint8)
        self.assertEqual(check_gate_x_on_grid(blank, QG_TRUE, QG_LABELS, PLOT).status, "unverified")
        self.assertEqual(check_gate_x_on_grid(_qg_panel(), QG_TRUE[:1], QG_LABELS, PLOT).status, "unverified")
        self.assertEqual(check_gate_x_on_grid(_qg_panel(), QG_TRUE, QG_LABELS[:1], PLOT).status, "unverified")
        collapsed = [(v, 200.0) for v in QG_VALUES]
        self.assertEqual(check_gate_x_on_grid(_qg_panel(), collapsed, QG_LABELS, PLOT).status, "unverified")
        seated = seat_gate_x_ticks(blank, QG_LABELS, PLOT)
        self.assertFalse(seated.anchored)
        self.assertEqual(seated.check.status, "unverified")

