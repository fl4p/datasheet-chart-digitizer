"""A trace BORN on a rule must fail at least as hard as one that steps onto it.

`_approach_deviation_px` used to answer "how far is the rule from where this
trace was heading?" using an approach window that the capture itself fills.
When a trace rides rule ink across its whole span, every approach sample is
already on the rule, the extrapolation lands on the rule, and the guard reported
agreement.  The check therefore got WEAKER as the capture got worse and went
silent at the far tail -- GT020N10T served the plot's bottom FRAME as Crss for
all 495 columns, passed every trace check, and was only stopped by an export
gate keyed on the ANCHOR's pixel resolution, which is blind to the trace.

The replacement measures the approach over off-rule columns only, reports None
when there are none, and decides that case on what the trace LEFT BEHIND: a
captured trace abandons real curve ink that no trace claims, while a flat trace
legitimately coinciding with a rule abandons nothing.
"""
from __future__ import annotations

import unittest

import numpy as np

from datasheet_chart_digitizer import capacitance_source_support as ss
from datasheet_chart_digitizer.capacitance_types import PlotBox, Trace
from datasheet_chart_digitizer.capacitance_validation import trace_validation_summary


PLOT = PlotBox(20, 10, 320, 290)
FRAME_ROWS = (287, 288, 289)


def _panel() -> np.ndarray:
    """White panel with a bottom frame rule and three descending curves."""
    image = np.full((320, 340), 255, dtype=np.uint8)
    for row in FRAME_ROWS:
        image[row, 20:320] = 0
    for x in range(20, 320):
        t = (x - 20) / 299.0
        for y in _curve_ys(x):
            image[y - 1 : y + 2, x] = 0
        del t
    return image


def _curve_ys(x: int) -> tuple[int, int, int]:
    t = (x - 20) / 299.0
    ciss = int(40 + 25 * t)
    coss = int(90 + 120 * t)
    crss = int(180 + 70 * t)
    return ciss, coss, crss


def _traces(captured_fraction: float) -> list[Trace]:
    """Crss follows its own curve, then rides the frame for the tail fraction."""
    xs = list(range(20, 320))
    switch = int(len(xs) * (1.0 - captured_fraction))
    ciss, coss, crss = [], [], []
    for index, x in enumerate(xs):
        c_i, c_o, c_r = _curve_ys(x)
        ciss.append((x, c_i))
        coss.append((x, c_o))
        crss.append((x, c_r if index < switch else FRAME_ROWS[1]))
    return [_trace("Ciss", ciss), _trace("Coss", coss), _trace("Crss", crss)]


def _trace(name: str, points: list[tuple[int, int]]) -> Trace:
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return Trace(
        name=name,
        area=len(points),
        bbox=(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1),
        points=points,
    )


def _captured_names(captured_fraction: float) -> set[str]:
    diagnostics = ss.grid_rule_capture_diagnostics(
        _panel(), PLOT, _traces(captured_fraction), []
    )
    assert diagnostics["evaluated"], diagnostics
    names = set(diagnostics["captured_traces"])
    names.update(diagnostics["undecidable_runs"])
    return names


class GridCaptureMonotonicityTests(unittest.TestCase):
    def test_verdict_never_returns_to_clean_as_capture_grows(self) -> None:
        # The far tail is the point: 1.0 is a trace born on the rule, which the
        # approach-window formulation scored as perfect agreement.
        flagged = {
            fraction: "Crss" in _captured_names(fraction)
            for fraction in (0.0, 0.2, 0.4, 0.6, 0.8, 0.95, 1.0)
        }
        self.assertFalse(flagged[0.0], "an uncaptured trace must stay clean")
        first_flagged = next(f for f, hit in flagged.items() if hit)
        worse = [hit for f, hit in flagged.items() if f > first_flagged]
        self.assertTrue(
            all(worse),
            f"verdict flipped back to clean as capture grew: {flagged}",
        )
        self.assertTrue(flagged[1.0], f"total capture read as clean: {flagged}")

    def test_full_span_capture_reports_unmeasurable_not_agreeing(self) -> None:
        traces = {trace.name: dict(trace.points) for trace in _traces(1.0)}
        run = sorted(traces["Crss"])
        rule_rows = frozenset(FRAME_ROWS)
        self.assertIsNone(
            ss._approach_deviation_px(
                traces["Crss"], run, FRAME_ROWS[1], set(), rule_rows
            )
        )

    def test_partial_capture_still_measures_its_approach(self) -> None:
        # The established measured path must be untouched: a trace that steps
        # onto a rule from its own trajectory still reports a real deviation.
        traces = {trace.name: dict(trace.points) for trace in _traces(0.4)}
        y_by_x = traces["Crss"]
        run = [x for x, y in sorted(y_by_x.items()) if y == FRAME_ROWS[1]]
        deviation = ss._approach_deviation_px(
            y_by_x, run, FRAME_ROWS[1], set(), frozenset(FRAME_ROWS)
        )
        self.assertIsNotNone(deviation)
        self.assertGreater(deviation, ss.GRID_RULE_CAPTURE_MIN_APPROACH_DEVIATION_PX)


class AbandonedStrokeAttributionTests(unittest.TestCase):
    def test_flat_trace_on_a_rule_abandoning_nothing_is_not_a_capture(self) -> None:
        # The legitimate case the 5 px floor existed to protect: a genuinely
        # flat trace coinciding with a printed rule. Nothing is left unclaimed.
        image = np.full((320, 340), 255, dtype=np.uint8)
        for row in FRAME_ROWS:
            image[row, 20:320] = 0
        flat_row = 150
        image[flat_row - 1 : flat_row + 2, 20:320] = 0
        for x in range(20, 320):
            image[59:62, x] = 0
            image[249:252, x] = 0
        traces = [
            _trace("Ciss", [(x, 60) for x in range(20, 320)]),
            _trace("Coss", [(x, flat_row) for x in range(20, 320)]),
            _trace("Crss", [(x, 250) for x in range(20, 320)]),
        ]
        diagnostics = ss.grid_rule_capture_diagnostics(image, PLOT, traces, [])
        self.assertTrue(diagnostics["evaluated"])
        self.assertEqual(diagnostics["captured_traces"], {})

    def test_columns_missing_a_peer_do_not_incriminate_this_trace(self) -> None:
        # GT045N10D5: Ciss is correct and flat on its own row, while Coss stops
        # at 57 V and leaves its own printed tail unclaimed.  The unclaimed ink
        # is the peer-span gates' finding, not a Ciss capture -- attributing it
        # to Ciss put a capture label on the one trace that was right.
        image = np.full((320, 340), 255, dtype=np.uint8)
        for row in FRAME_ROWS:
            image[row, 20:320] = 0
        image[39:42, 20:320] = 0                       # flat Ciss, full width
        for x in range(20, 320):
            _ciss, coss, crss = _curve_ys(x)
            image[coss - 1 : coss + 2, x] = 0          # printed across the span
            image[crss - 1 : crss + 2, x] = 0
        traces = [
            _trace("Ciss", [(x, 40) for x in range(20, 320)]),
            _trace("Coss", [(x, _curve_ys(x)[1]) for x in range(20, 200)]),
            _trace("Crss", [(x, _curve_ys(x)[2]) for x in range(20, 200)]),
        ]
        diagnostics = ss.grid_rule_capture_diagnostics(image, PLOT, traces, [])
        self.assertNotIn("Ciss", diagnostics["captured_traces"])


class KnownBadPanelFixtureTests(unittest.TestCase):
    """The two panels a human reviewer marked wrong must both be rejected."""

    def _summary(self, reasons_source: dict) -> dict:
        return trace_validation_summary(
            {
                name: {
                    "points": 400,
                    "x_span_fraction": 0.98,
                    "y_range_px": 40,
                    "value_rise_fraction": 0.0,
                }
                for name in ("Ciss", "Coss", "Crss")
            }
            | {
                "checks": {
                    "common_samples": 200,
                    "ciss_coss_rank_swap_count": 0,
                    "crss_bottom_fraction": 1.0,
                    "ciss_flatter_than_coss": True,
                }
            },
            extraction_method="raster",
            source_support_diagnostics=reasons_source,
        )

    def test_gt020n10t_shape_full_span_frame_capture_is_suspect(self) -> None:
        summary = self._summary(
            {
                "applicable": True,
                "grid_rule_capture": {
                    "evaluated": True,
                    "captured_traces": {"Crss": [{"rule_y_px": 388}]},
                    "undecidable_runs": {},
                },
            }
        )
        self.assertEqual(summary["status"], "suspect")
        self.assertIn("Crss_captured_by_grid_rule", summary["reasons"])

    def test_an_undecidable_capture_check_is_not_a_clean_one(self) -> None:
        summary = self._summary(
            {
                "applicable": True,
                "grid_rule_capture": {
                    "evaluated": True,
                    "captured_traces": {},
                    "undecidable_runs": {"Ciss": [{"rule_y_px": 74}]},
                },
            }
        )
        self.assertEqual(summary["status"], "suspect")
        self.assertIn("Ciss_grid_rule_capture_undecidable", summary["reasons"])


if __name__ == "__main__":
    unittest.main()
