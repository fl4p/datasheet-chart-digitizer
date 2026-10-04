"""Coverage guard and grid-bound ladder extension (bench round 1, bug 3).

TMB160N08A Figure 6 (body diode, log I_S 1e-4..1e2 A) was served ``ok`` up to
10 A: the plot box stopped at the 10^1 gridline and both curves continue to
the 10^2 frame in the print. NTMFS4C13NT1G (Fab's human-verified set) had the
same defect at 25 A of a 0..30 A axis.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from datasheet_chart_digitizer import diode_forward_voltage
from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.crop_transform import CropTransform
from datasheet_chart_digitizer.curve_coverage import served_curve_coverage

TMB160 = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/wuxi/TMB160N08A.pdf")
NTMFS = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/onsemi/NTMFS4C13NT1G.pdf")


def _pt(x, y):
    return SimpleNamespace(x=x, y=y)


class _Page:
    def __init__(self, polyline):
        items = [("l", _pt(*a), _pt(*b)) for a, b in zip(polyline, polyline[1:])]
        self._drawings = [{"type": "s", "width": 1.0, "items": items}]

    def get_drawings(self):
        return self._drawings


def _scene(continue_px: int, *, ink: bool = True):
    """A diagonal curve served up to the top edge (y=100) of a 400x400 plot.

    Identity transform (1 px = 1 pt). The source stroke continues
    ``continue_px`` above the top edge; ``ink`` decides whether that part is
    rendered (a stroke drawn past the frame under a clip path is not).
    """
    plot = PlotBox(100, 100, 500, 500)
    transform = CropTransform(0.0, 0.0, 1.0, 1.0)
    gray = np.full((600, 600), 255, np.uint8)
    source = [(150.0, 500.0), (400.0, 100.0 - continue_px)]
    xs = np.linspace(150, 400, 800)
    ys = np.linspace(500, 100 - continue_px, 800)
    for x, y in zip(xs, ys):
        if y >= 100 or ink:
            gray[int(round(y)), int(round(x))] = 0
    served = [(int(round(x)), int(round(y))) for x, y in zip(xs, ys) if y >= 100]
    return _Page(source), transform, gray, plot, [served]


class CoverageGuardTests(unittest.TestCase):
    def test_monotone_in_continuation_length(self) -> None:
        # needed = max(10 px, 4 % of 400) = 16 px
        verdicts = {n: served_curve_coverage(*_scene(n)).status for n in (0, 8, 15, 17, 40, 100, 300)}
        self.assertEqual(verdicts[0], "verified")
        self.assertEqual(verdicts[8], "verified")
        for n in (17, 40, 100, 300):
            self.assertEqual(verdicts[n], "truncated", n)

    def test_invisible_continuation_is_not_truncation(self) -> None:
        self.assertEqual(served_curve_coverage(*_scene(80, ink=False)).status, "verified")

    def test_continuation_short_of_every_printed_tick_is_a_note(self) -> None:
        verdict = served_curve_coverage(*_scene(80), label_beyond=lambda edge, extent: False)
        self.assertEqual(verdict.status, "verified")
        self.assertEqual(len(verdict.notes), 1)
        verdict = served_curve_coverage(*_scene(80), label_beyond=lambda edge, extent: True)
        self.assertEqual(verdict.status, "truncated")

    def test_edge_end_without_a_source_stroke_is_unverified_not_ok(self) -> None:
        page, transform, gray, plot, curves = _scene(80)
        empty = SimpleNamespace(get_drawings=lambda: [])
        verdict = served_curve_coverage(empty, transform, gray, plot, curves)
        self.assertEqual(verdict.status, "unverified")
        self.assertTrue(verdict.reasons)


class TruncatedBodyDiodeTests(unittest.TestCase):
    def _run(self, pdf: Path, *, extend: bool):
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        from datasheet_chart_digitizer.find_charts import process_pdf

        with tempfile.TemporaryDirectory() as tmp:
            panels = process_pdf(pdf, Path(tmp), 220)
            if extend:
                return diode_forward_voltage.digitize_panels_fail_closed(panels, Path(tmp))
            with patch.object(
                diode_forward_voltage,
                "_extend_rule_bound_axis",
                lambda labels, axis, raw, orientation, hint, image: (axis, raw),
            ):
                return diode_forward_voltage.digitize_panels_fail_closed(panels, Path(tmp))

    def test_guard_downgrades_the_truncated_serve(self) -> None:
        # known-bad calibration: the shortened calibration main served `ok`
        for pdf in (TMB160, NTMFS):
            with self.subTest(pdf=pdf.name):
                results, errors = self._run(pdf, extend=False)
                self.assertEqual(len(results), 1, errors)
                self.assertEqual(results[0]["status"], "unverified")
                self.assertIn("served_curve_truncated_before_source_end", results[0]["diagnostics"])
                self.assertEqual(results[0]["coverage_check"]["status"], "truncated")

    def test_unlabelled_end_interval_is_not_truncation(self) -> None:
        # STW46NF30 Figure 13: curves run past the last labelled current
        # (30 A) into the unlabelled end interval; the class never
        # extrapolates past its last label, so the serve stays ok.
        pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st/STW46NF30.pdf")
        results, errors = self._run(pdf, extend=True)
        self.assertEqual(len(results), 1, errors)
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[0]["coverage_check"]["status"], "verified")
        self.assertTrue(results[0]["coverage_check"]["notes"])

    def test_grid_bound_ladder_reaches_the_frame(self) -> None:
        for pdf, top in ((TMB160, 100.0), (NTMFS, 30.0)):
            with self.subTest(pdf=pdf.name):
                results, errors = self._run(pdf, extend=True)
                self.assertEqual(len(results), 1, errors)
                result = results[0]
                self.assertEqual(result["status"], "ok", result["diagnostics"])
                self.assertEqual(result["coverage_check"]["status"], "verified")
                self.assertEqual(result["grid_check"]["y"]["status"], "verified")
                self.assertIn(top, [tick["value"] for tick in result["y_axis"]["ticks"]])
                for curve in result["curves"]:
                    self.assertGreater(max(point[1] for point in curve["points"]), 0.97 * top)


if __name__ == "__main__":
    unittest.main()
