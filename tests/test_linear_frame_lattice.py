"""A linear body-diode axis is served out to the drawn frame past its last
label only when the frame and every rule before it sit on the label-step
lattice (``diode_forward_voltage._linear_frame_on_lattice``).

ST prints the current axis to a frame one step (STP45N60DM2AG 36 A,
ST8L65N044M9 60 A) or two steps (STW46NF30 40 A, axis title in place of the
35/40 A labels) past the last label; the curves run into that interval.
NTMFS5C404NLT1G's frame is 0.8 step above its 46 A label: not served.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from datasheet_chart_digitizer import diode_forward_voltage as dfv
from datasheet_chart_digitizer import served_axis_guard
from datasheet_chart_digitizer.numeric_axis import AxisTick, fit_axis_ticks

DATASHEETS = Path(os.environ.get("DSDIG_DATASHEET_ROOT", "/Users/fab/dev/pv/pwr-mosfet-lib")) / "datasheets"

PITCH = 40.0
FIRST = 100.0
LAST = FIRST + 6 * PITCH  # the 30 A label


def _axis(values=(0, 5, 10, 15, 20, 25, 30), pixels=None):
    pixels = pixels or [FIRST + PITCH * i for i in range(len(values))]
    return fit_axis_ticks(
        [AxisTick(str(v), float(v), float(p)) for v, p in zip(values, pixels)], "axis", model="linear"
    )


def _lines(frame_steps: float, *, minors: int = 1, beyond=None):
    """Label rules (with 1/minors subdivisions), the rules past the last
    label (default: the same subdivision up to the frame) and the frame."""
    inside = [FIRST + PITCH * j / minors for j in range(6 * minors + 1)]
    if beyond is None:
        beyond = [j / minors for j in range(1, int(frame_steps * minors - 1e-9) + 1)
                  if j / minors < frame_steps - 0.05]
    return tuple(inside + [LAST + PITCH * s for s in beyond] + [LAST + PITCH * frame_steps])


def _on_lattice(frame_steps: float, **kwargs) -> bool:
    axis = kwargs.pop("axis", None) or _axis()
    return dfv._linear_frame_on_lattice(axis, round(LAST), round(LAST + PITCH * frame_steps), _lines(frame_steps, **kwargs))


class LinearFrameLatticeTests(unittest.TestCase):
    def test_frame_one_or_two_whole_steps_out_is_served(self):
        self.assertTrue(_on_lattice(1.0))
        self.assertTrue(_on_lattice(2.0))
        self.assertTrue(_on_lattice(1.0, minors=2))  # ST half-step minors
        self.assertTrue(_on_lattice(2.0, minors=2))

    def test_inverted_y_axis_top_frame(self):
        # y pixels fall as values rise: frame above the top label
        axis = _axis(pixels=[400.0 - PITCH * i for i in range(7)])
        top = 400.0 - 6 * PITCH
        lines = tuple(400.0 - PITCH * i for i in range(7)) + (top - PITCH,)
        self.assertTrue(dfv._linear_frame_on_lattice(axis, round(top), round(top - PITCH), lines))
        self.assertFalse(dfv._linear_frame_on_lattice(axis, round(top), round(top - 1.4 * PITCH), lines[:-1] + (top - 1.4 * PITCH,)))

    def test_frame_off_the_lattice_is_not_served(self):
        for steps in (0.5, 0.8, 1.4, 1.5, 2.5):
            self.assertFalse(_on_lattice(steps), steps)

    def test_frame_too_far_out_is_not_served(self):
        self.assertFalse(_on_lattice(3.0))
        self.assertFalse(_on_lattice(4.0))

    def test_missing_or_off_lattice_intermediate_rule_is_not_served(self):
        self.assertFalse(_on_lattice(2.0, beyond=[]))  # the 1-step rule is missing
        self.assertFalse(_on_lattice(2.0, beyond=[1.0, 1.3]))  # an extra off-lattice rule
        self.assertFalse(_on_lattice(1.0, beyond=[0.3]))
        self.assertFalse(_on_lattice(1.0, minors=2, beyond=[]))  # half-step rule missing past the label
        self.assertFalse(_on_lattice(1.0, beyond=[0.5]))  # half-step rule the labelled range does not show

    def test_non_uniform_labels_are_not_served(self):
        # values 0..25, 35 (a skipped 30 label), pixels exactly linear: residual 0
        skipped = _axis(values=(0, 5, 10, 15, 20, 25, 35), pixels=[FIRST + 8 * v for v in (0, 5, 10, 15, 20, 25, 35)])
        last = FIRST + 8 * 35
        lines = tuple(FIRST + 8 * v for v in (0, 5, 10, 15, 20, 25, 30, 35)) + (last + PITCH,)
        self.assertFalse(dfv._linear_frame_on_lattice(skipped, round(last), round(last + PITCH), lines))
        jittered = [FIRST + PITCH * i + (6.0 if i == 3 else 0.0) for i in range(7)]
        axis = _axis(pixels=jittered)
        self.assertGreater(axis.residual_px, 1.5)
        self.assertFalse(_on_lattice(1.0, axis=axis))

    def test_unevaluable_is_not_served(self):
        axis = _axis()
        frame = round(LAST + PITCH)
        self.assertFalse(dfv._linear_frame_on_lattice(axis, round(LAST), frame, ()))  # no rule scan
        self.assertFalse(dfv._linear_frame_on_lattice(axis, round(LAST), frame, _lines(1.0)[:-1]))  # no frame rule
        two = _axis(values=(25, 30), pixels=[LAST - PITCH, LAST])
        self.assertFalse(dfv._linear_frame_on_lattice(two, round(LAST), frame, _lines(1.0)))  # uniformity unprovable
        log = dataclasses.replace(axis, model="log10")
        self.assertFalse(dfv._linear_frame_on_lattice(log, round(LAST), frame, _lines(1.0)))

    def test_monotone_in_frame_offset(self):
        # sweep 0.9..2.2 steps: served only within 2 px of 1 and 2 steps, never further out
        served = []
        for hundredths in range(90, 221):
            steps = hundredths / 100
            if _on_lattice(steps):
                served.append(steps)
        self.assertTrue(served)
        for steps in served:
            self.assertTrue(min(abs(steps - 1.0), abs(steps - 2.0)) * PITCH <= 2.0 + 0.5, steps)
        self.assertLessEqual(max(served), 2.0 + 2.5 / PITCH)
        self.assertIn(1.0, served)
        self.assertIn(2.0, served)


@pytest.mark.slow
class LinearFrameCorpusTests(unittest.TestCase):
    def _digitize(self, rel: str, page: int, dpi: int = 180):
        pdf = DATASHEETS / rel
        if not pdf.exists():
            self.skipTest(f"corpus PDF missing: {pdf}")
        with tempfile.TemporaryDirectory() as tmp:
            results = dfv.digitize_pdf(pdf, Path(tmp), dpi=dpi)
        return next(r for r in results if r["panel"]["page"] == page)

    def test_stp45n60dm2ag_current_reaches_the_36a_frame(self):
        result = self._digitize("st/STP45N60DM2AG.pdf", 6)
        self.assertEqual(result["status"], "ok", result["diagnostics"])
        self.assertNotIn("source_curve_continues_past_last_labelled_tick_not_extrapolated", result["diagnostics"])
        self.assertIn("served_past_last_label_to_lattice_frame_y0_x1", result["diagnostics"])
        for curve in result["curves"]:
            # source curves end at 34.0 A (vector paths); the last label is 32 A
            self.assertGreater(max(p[1] for p in curve["points"]), 33.7)
            self.assertLessEqual(max(p[1] for p in curve["points"]), 36.0)

    def test_ntmfs5c404nl_frame_off_the_label_lattice_stays_unverified(self):
        # labels 1, 6, ..., 46 A; the top frame is 0.8 step above 46 A (50 A)
        result = self._digitize("onsemi/NTMFS5C404NLT1G.pdf", 4)
        self.assertEqual(result["status"], "unverified")
        self.assertIn("source_curve_continues_past_last_labelled_tick_not_extrapolated", result["diagnostics"])
        self.assertLessEqual(max(max(p[1] for p in c["points"]) for c in result["curves"]), 46.0)

    def test_curve_span_gate_keeps_the_labelled_box(self):
        # STK184N4F7AG: labels 10..90 A, frame at 110 A; curves 20..90 A span
        # < 75 % of the frame box, so gating on it extracted no curve at all
        result = self._digitize("st/STK184N4F7AG.pdf", 5, dpi=220)
        self.assertEqual(result["status"], "ok", result["diagnostics"])
        for curve in result["curves"]:
            self.assertAlmostEqual(max(p[1] for p in curve["points"]), 90.0, delta=0.3)  # source ends at 90 A
        with mock.patch.object(dfv, "_labelled_box", lambda calibration: calibration.plot):
            with self.assertRaisesRegex(RuntimeError, "0 curves"):
                self._digitize("st/STK184N4F7AG.pdf", 5, dpi=220)

    def test_frame_not_served_when_the_axis_grid_check_is_unverified(self):
        real = served_axis_guard.check_axes

        def x_unverified(*args, **kwargs):
            checks = real(*args, **kwargs)
            return {**checks, "x": dataclasses.replace(checks["x"], status="unverified")}

        with mock.patch.object(served_axis_guard, "check_axes", x_unverified):
            result = self._digitize("st/STW46NF30.pdf", 7)
        self.assertEqual(result["status"], "unverified")
        for curve in result["curves"]:
            self.assertLessEqual(max(p[1] for p in curve["points"]), 30.0 + 1e-6)


if __name__ == "__main__":
    unittest.main()
