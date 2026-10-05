"""gate_charge_plot_box: the served plot box agrees with the served axis rules.

Synthetic panels (continuous px; a 1 px rule at continuous p is drawn at index
p - 0.5). Known-bad: a box spanning a stacked neighbour chart above and the
label row below (FDP020N06B-F102 shape) is bound to the outermost rules; a
real frame one or two unlabelled intervals beyond the labels is kept; a
neighbour's frame separated by paper is not.
"""

from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer.gate_charge_plot_box import bind_plot_box_to_rules

X0, X1 = 80, 520
PITCH = 50.0


def _rule_y(v):  # VGS v (0..10 V, 2 V per 50 px), 0 V at y=440
    return 440.5 - v / 2.0 * PITCH


def _draw_chart(image, y_top_v, y_bottom_v=0.0, *, gap_above=None):
    top, bottom = _rule_y(y_top_v), _rule_y(y_bottom_v)
    v = y_bottom_v
    while v <= y_top_v + 1e-9:
        image[int(_rule_y(v) - 0.5), X0:X1 + 1] = 0
        v += 2.0
    image[int(top - 0.5):int(bottom - 0.5) + 1, X0] = 0
    image[int(top - 0.5):int(bottom - 0.5) + 1, X1] = 0
    return image


def _blank():
    return np.full((520, 600), 255, dtype=np.uint8)


def _served(values):
    return [(v, _rule_y(v)) for v in values]


class GateChargePlotBoxTests(unittest.TestCase):
    def test_box_over_a_stacked_neighbour_is_bound_to_the_rules(self):
        image = _draw_chart(_blank(), 10.0)
        # neighbour chart above, separated by paper: frame rows 20..150
        image[20, X0:X1 + 1] = 0
        image[150, X0:X1 + 1] = 0
        image[20:151, X0] = 0
        box, binding = bind_plot_box_to_rules(
            image, (X0, 20, X1, 465), y_ticks_px=_served([10, 8, 6, 4, 2, 0])
        )
        self.assertEqual(box, (X0, round(_rule_y(10)), X1, round(_rule_y(0))))
        self.assertTrue(binding["changed"])
        self.assertTrue(binding["y"]["lo"].startswith("bound_to_rule"))
        self.assertTrue(binding["y"]["hi"].startswith("bound_to_rule"))

    def test_neighbour_frame_one_pitch_out_but_separated_is_not_kept(self):
        image = _draw_chart(_blank(), 8.0)
        top8 = _rule_y(8.0)
        neighbour = int(top8 - PITCH - 0.5)  # exactly one pitch above, own frame
        image[neighbour, X0:X1 + 1] = 0
        image[neighbour - 40:neighbour + 1, X0] = 0
        box, binding = bind_plot_box_to_rules(
            image, (X0, neighbour, X1, 441), y_ticks_px=_served([8, 6, 4, 2, 0])
        )
        self.assertEqual(box[1], round(top8))
        self.assertTrue(binding["y"]["lo"].startswith("bound_to_rule"))

    def test_unlabelled_terminal_intervals_on_the_frame_are_kept(self):
        image = _draw_chart(_blank(), 10.0)
        top = int(round(_rule_y(10.0)))
        # labels stop at 8 V (one interval) and at 6 V (two intervals)
        for labelled in ([8, 6, 4, 2, 0], [6, 4, 2, 0]):
            box, binding = bind_plot_box_to_rules(
                image, (X0, top, X1, 441), y_ticks_px=_served(labelled)
            )
            self.assertEqual(box[1], top, (labelled, binding))
            self.assertEqual(binding["y"]["lo"], "frame_beyond_last_rule")

    def test_box_past_the_frame_is_pulled_onto_it(self):
        # MCPF90N12A shape: labels 10..2 V (the 0 V label missed), box bottom
        # on the Qg labels 19 px under the 0 V frame
        image = _draw_chart(_blank(), 10.0)
        box, binding = bind_plot_box_to_rules(
            image, (X0, int(round(_rule_y(10))), X1, 460), y_ticks_px=_served([10, 8, 6, 4, 2])
        )
        self.assertEqual(box[3], round(_rule_y(0)))
        self.assertTrue(binding["y"]["hi"].startswith("bound_to_frame_beyond_last_rule"))

    def test_edge_inside_the_labelled_range_grows_to_the_rule(self):
        image = _draw_chart(_blank(), 10.0)
        box, _binding = bind_plot_box_to_rules(
            image, (X0, int(round(_rule_y(8))), X1, 441), y_ticks_px=_served([10, 8, 6, 4, 2, 0])
        )
        self.assertEqual(box[1], round(_rule_y(10)))

    def test_qg_edges_follow_the_qg_rules(self):
        # RJK1001 shape: x0 bound to the VGS corner "0" glyph 16 px left of
        # the Qg origin rule
        image = _draw_chart(_blank(), 10.0)
        for x in range(X0, X1 + 1, 88):
            image[int(_rule_y(10) - 0.5):int(_rule_y(0) - 0.5) + 1, x] = 0
        served_x = [(float(i * 40), X0 + 0.5 + i * 88.0) for i in range(6)]
        box, binding = bind_plot_box_to_rules(
            image, (X0 - 16, int(round(_rule_y(10))), X1, 441), x_ticks_px=served_x
        )
        self.assertEqual(box[0], round(X0 + 0.5))
        self.assertTrue(binding["x"]["lo"].startswith("bound_to_rule"))

    def test_unseated_axes_leave_the_box_alone(self):
        image = _draw_chart(_blank(), 10.0)
        box, binding = bind_plot_box_to_rules(image, (10, 20, 590, 500))
        self.assertEqual(box, (10, 20, 590, 500))
        self.assertEqual(binding["y"], "not_seated")
        self.assertEqual(binding["x"], "not_seated")


if __name__ == "__main__":
    unittest.main()
