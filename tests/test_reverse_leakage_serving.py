"""Reverse-leakage serving evidence: dotted rules, major-rule weight, frame extent.

Each opt-in is exercised against the failure it must still refuse:
an ambiguous solid binding is never retried on dotted rules, a tie between
registrations is settled only by a >= 2x major-rule ink margin, and the plot
frame bounds extraction only when it hugs the anchored ticks.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from datasheet_chart_digitizer import diode_reverse_leakage as rl
from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.gridline_anchor import (
    AmbiguousRegistration,
    identify_tick_lines,
)
from datasheet_chart_digitizer.numeric_axis import AxisTick, fit_axis_ticks

SET1 = Path("/Users/fab/dev/pv/ee/datasheet-chart-digitizer/out/synth-charts/set1")


def _grid(major_grey: int, minor_grey: int, major_width: int = 3) -> np.ndarray:
    """Vertical majors every 100 px (x=50..450), minors every 10 px (30..480)."""
    image = np.full((200, 520), 255, dtype=np.uint8)
    for x in range(30, 481, 10):
        image[20:180, x] = minor_grey
    for x in range(50, 451, 100):
        image[20:180, x - major_width // 2 : x + major_width // 2 + 1] = major_grey
    return image


def _labels() -> list[AxisTick]:
    # glyphs sit exactly halfway between each major and the next minor, so
    # label offset alone cannot tell the two registrations apart
    return [AxisTick(str(v), float(v), 55.0 + 100 * i) for i, v in enumerate((0, 100, 200, 300, 400))]


def _identify(image, *, weight: bool):
    return identify_tick_lines(
        image, _labels(), model="linear", orientation="x", cross_span=(20, 180),
        name="X axis", major_rule_weight=weight,
    )


class MajorRuleWeightTests(unittest.TestCase):
    def test_tie_refuses_without_the_opt_in(self):
        with self.assertRaises(AmbiguousRegistration):
            _identify(_grid(0, 150), weight=False)

    def test_heavy_majors_settle_the_tie_on_the_majors(self):
        match = _identify(_grid(0, 150), weight=True)
        self.assertEqual([round(px) for px in match.line_px], [50, 150, 250, 350, 450])
        self.assertEqual(match.registration_evidence, "major_rule_weight")

    def test_equal_weight_rules_still_refuse(self):
        with self.assertRaises(AmbiguousRegistration):
            _identify(_grid(150, 150, major_width=1), weight=True)

    def test_a_small_weight_margin_still_refuses(self):
        # majors 1.5x the ink of the minors: below the 2x evidence bar
        with self.assertRaises(AmbiguousRegistration):
            _identify(_grid(135, 175, major_width=1), weight=True)


class SolidThenBrokenTests(unittest.TestCase):
    def test_ambiguous_solid_binding_is_final(self):
        calls = []

        def fake(image, axis, **kwargs):
            calls.append(kwargs)
            raise AmbiguousRegistration("tie")

        with mock.patch.object(rl, "anchor_axis_on_grid", fake), self.assertRaises(AmbiguousRegistration):
            rl._anchor_solid_then_broken(None, None, orientation="x", cross_span=(0, 1), name="X")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("broken_rules", calls[0])

    def test_unbound_solid_level_retries_on_dotted_rules(self):
        calls = []

        def fake(image, axis, **kwargs):
            calls.append(kwargs)
            if not kwargs.get("broken_rules"):
                raise RuntimeError("no line")
            return "anchored"

        with mock.patch.object(rl, "anchor_axis_on_grid", fake):
            self.assertEqual(
                rl._anchor_solid_then_broken(None, None, orientation="x", cross_span=(0, 1), name="X"),
                "anchored",
            )
        self.assertEqual([c.get("broken_rules", False) for c in calls], [False, True])


class FrameExtentTests(unittest.TestCase):
    def axes(self):
        x = fit_axis_ticks([AxisTick(str(v), v, 100 + 2 * v) for v in (0.0, 20.0, 40.0, 60.0, 80.0)])
        y = fit_axis_ticks(
            [AxisTick(str(v), v, 500 - 100 * i) for i, v in enumerate((0.001, 0.01, 0.1, 1.0))],
            model="log10",
        )
        return x, y

    def frame(self, box):
        return mock.patch.object(rl, "find_closed_frame_plot_box", lambda image: box)

    def test_frame_one_dropped_label_beyond_the_ticks_bounds_extraction(self):
        x, y = self.axes()
        box = PlotBox(100, 150, 308, 500)  # x to 104 V (1.2 steps), y half a decade up
        with self.frame(box):
            self.assertEqual(rl._frame_around_ticks(None, x, y), box)

    def test_a_distant_or_non_enclosing_frame_is_not_used(self):
        x, y = self.axes()
        for box in (PlotBox(100, 150, 400, 500), PlotBox(120, 200, 260, 500)):
            with self.subTest(box=box), self.frame(box):
                self.assertIsNone(rl._frame_around_ticks(None, x, y))


@unittest.skipUnless((SET1 / "cases.json").is_file(), "synthetic set1 not generated")
class SyntheticServedFamilyTests(unittest.TestCase):
    """Served end to end: identity, amps, and GT accuracy (<1 % of span)."""

    CASES = (
        "syn1_0041_reverse_leakage",  # 10^n spans, unit only in the rotated owned title
        "syn1_0163_reverse_leakage",  # SI ticks in uA, two curves in one source path
        "syn1_0105_reverse_leakage",  # tie settled by solid majors over dotted minors
    )

    def test_cases(self):
        import tempfile

        from datasheet_chart_digitizer.find_charts import process_pdf

        cases = {c["id"]: c for c in json.loads((SET1 / "cases.json").read_text())}
        for case_id in self.CASES:
            case = cases[case_id]
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as tmp:
                number = int(re.search(r"(\d+)", case["gt_provenance"]["diagram"]).group(1))
                pdf = SET1 / case["gt_provenance"]["pdf"]
                panel = next(p for p in process_pdf(pdf, Path(tmp), 220)
                             if p.kind == rl.KIND and p.diagram == number)
                result = rl._digitize_panel(panel, Path(tmp))
                gt = {float(c["label"]): c for c in case["curves"]}
                self.assertEqual([c["temperature_c"] for c in result["curves"]], sorted(gt))
                axis = case["axis"]["y"]
                self.assertEqual(axis["unit"], "µA")
                span = np.log10(axis["max"]) - np.log10(axis["min"])
                for curve in result["curves"]:
                    points = np.asarray(curve["points"])
                    truth = gt[curve["temperature_c"]]
                    xs = np.asarray(truth["x"])
                    inside = (points[:, 0] >= xs.min()) & (points[:, 0] <= xs.max())
                    expected = np.interp(points[inside, 0], xs, np.log10(np.asarray(truth["y"]) * 1e-6))
                    error = np.abs(np.log10(points[inside, 1]) - expected)
                    self.assertLess(np.percentile(error, 95) / span, 0.01)


if __name__ == "__main__":
    unittest.main()
