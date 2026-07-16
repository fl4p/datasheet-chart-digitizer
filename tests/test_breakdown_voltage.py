"""Regression tests for the breakdown-voltage (V(BR)DSS vs Tj) plugin.

Pinned to the Infineon Diagram-15 style charts of IPP040N08NF2S,
IPP024N08NF2S and IPP022N12NM6 — the first HUMAN-VERIFIED samples (overlays
+ values reviewed by Fab 2026-07-14, after the CropTransform tick-alignment
fix). If the extraction drifts, these numbers move — do not relax bands
without re-verifying overlays. Independent corroboration: manual chart reads
(recorded on fl4p/dcdc-tools#19) and the vendor S5 model `ab` temperature
coefficients agree; the 25 C values equal the parameter-table V(BR)DSS
minimum (min-anchored spec floor, typical-die slope).

The end-to-end tests need the local datasheet library; they skip cleanly
when it is absent so the unit-level tests still run anywhere.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from datasheet_chart_digitizer import breakdown_voltage as bv
from datasheet_chart_digitizer import find_charts

INFINEON = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon")
PARTS = ["IPP040N08NF2S", "IPP024N08NF2S", "IPP022N12NM6"]


def _find_and_digitize(parts: list[str]):
    out = Path(tempfile.mkdtemp(prefix="bv-test-"))
    panels = []
    for part in parts:
        panels.extend(find_charts.process_pdf(INFINEON / f"{part}.pdf", out, dpi=180))
    charts = [find_charts.asdict(p) for p in panels]
    index = out / "charts.json"
    index.write_text(json.dumps(charts))
    results = []
    for chart in charts:
        if chart.get("kind") != "breakdown_voltage":
            continue
        crop_rel = Path(chart["crop_png"])
        result = bv.process_chart(chart, out / crop_rel, out, crop_rel.with_suffix(""))
        result["part"] = chart["part"]
        results.append(result)
    return charts, results, out


@unittest.skipUnless(INFINEON.exists(), "local datasheet library not available")
class InfineonDiagram15EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.charts, cls.results, cls.out = _find_and_digitize(PARTS)
        cls.by_part = {r["part"]: r for r in cls.results}

    def test_one_panel_per_part(self):
        self.assertEqual(sorted(self.by_part), sorted(PARTS))

    def test_pinned_values_80v_parts(self):
        for part in ("IPP040N08NF2S", "IPP024N08NF2S"):
            r = self.by_part[part]
            self.assertAlmostEqual(r["v_at_25c"], 80.0, delta=0.15, msg=part)
            self.assertAlmostEqual(r["slope_mv_per_k"], 40.0, delta=1.5, msg=part)

    def test_pinned_values_ipp022(self):
        r = self.by_part["IPP022N12NM6"]
        self.assertAlmostEqual(r["v_at_25c"], 120.0, delta=0.25)
        self.assertAlmostEqual(r["slope_mv_per_k"], 75.0, delta=2.0)

    def test_min_anchor_verified(self):
        """The 25 C chart value must equal the table minimum on these parts."""
        for part, r in self.by_part.items():
            self.assertEqual(r["status"], "verified", (part, r["anchor"], r["warnings"]))
            self.assertLess(abs(r["anchor"]["err"]), bv.ANCHOR_TOL, part)

    def test_line_is_actually_linear(self):
        for part, r in self.by_part.items():
            self.assertLess(r["line_fit_rms_v"], 0.05, part)
            self.assertEqual(r["warnings"], [], part)

    def test_covers_25c_and_full_span(self):
        for part, r in self.by_part.items():
            lo, hi = r["tj_range_c"]
            self.assertLess(lo, 0.0, part)
            self.assertGreater(hi, 150.0, part)

    def test_artifacts_exist(self):
        for r in self.results:
            self.assertTrue(Path(r["csv"]).exists())
            self.assertTrue(Path(r["overlay"]).exists())
            rows = Path(r["csv"]).read_text().splitlines()
            self.assertEqual(rows[0], "Tj_C,VBR_DSS_V")
            self.assertGreater(len(rows), 200)

    def test_manifest_is_strict_json(self):
        s = json.dumps(self.results)
        json.loads(s)
        self.assertNotIn("NaN", s)


@unittest.skipUnless(INFINEON.exists(), "local datasheet library not available")
class KnownBadInputsRefuse(unittest.TestCase):
    """Guard calibration: constructed failures must FAIL, not pass silently."""

    @classmethod
    def setUpClass(cls):
        cls.charts, cls.results, cls.out = _find_and_digitize(["IPP040N08NF2S"])

    def _chart(self, kind: str) -> dict:
        return next(c for c in self.charts if c["kind"] == kind)

    def test_multi_curve_panel_refused(self):
        # a capacitance chart must be refused, not silently digitized. Since the
        # axis fit moved onto the shared numeric_axis core, this now fails closed
        # even earlier — at the axis-type mismatch (a capacitance chart's log Y is
        # ambiguous/non-linear) rather than at the downstream curve-count guard.
        # Accept either refusal reason so the test tracks the fail-closed outcome,
        # not one specific guard's wording.
        cap = self._chart("capacitances")
        with self.assertRaisesRegex(RuntimeError, "ambiguous|not.*monotone|expected exactly 1"):
            bv.process_chart(cap, self.out / cap["crop_png"], self.out / "bad", Path("bad/cap"))

    def test_wrong_spec_anchor_fails_loud(self):
        chart = self._chart("breakdown_voltage")
        orig = bv._spec_min_vbrdss
        bv._spec_min_vbrdss = lambda doc: (100.0, 2)
        try:
            r = bv.process_chart(chart, self.out / chart["crop_png"], self.out / "bad", Path("bad/anchor"))
        finally:
            bv._spec_min_vbrdss = orig
        self.assertEqual(r["status"], "FAIL")
        self.assertTrue(any("does not match" in w for w in r["warnings"]))

    def test_missing_spec_anchor_is_unverified_not_verified(self):
        chart = self._chart("breakdown_voltage")
        orig = bv._spec_min_vbrdss
        bv._spec_min_vbrdss = lambda doc: None
        try:
            r = bv.process_chart(chart, self.out / chart["crop_png"], self.out / "bad", Path("bad/noanchor"))
        finally:
            bv._spec_min_vbrdss = orig
        self.assertEqual(r["status"], "unverified")
        self.assertTrue(any("NOT" in w or "unavailable" in w for w in r["warnings"]))


@unittest.skipUnless(INFINEON.exists(), "local datasheet library not available")
class OldCaptionLayoutEndToEnd(unittest.TestCase):
    """IPP040N06N: older Infineon layout — numbered caption ("15 Drain-source
    breakdown voltage"), tight caption-derived crop, plot frame at the crop
    edge (raster frame detection clipped the curve at an interior gridline;
    the vector uniform-grid frame recovers the full span). Overlay
    HUMAN-VERIFIED by Fab 2026-07-14."""

    @classmethod
    def setUpClass(cls):
        cls.charts, cls.results, cls.out = _find_and_digitize(["IPP040N06N"])

    def test_caption_layout_detected_and_digitized(self):
        self.assertEqual([r["part"] for r in self.results], ["IPP040N06N"])
        r = self.results[0]
        self.assertEqual(r["frame_method"], "vector")
        self.assertEqual(r["status"], "verified")

    def test_pinned_values(self):
        r = self.results[0]
        self.assertAlmostEqual(r["v_at_25c"], 60.0, delta=0.15)
        self.assertAlmostEqual(r["slope_mv_per_k"], 30.0, delta=1.5)

    def test_full_span_not_clipped(self):
        lo, hi = self.results[0]["tj_range_c"]
        self.assertLess(lo, -50.0)   # raster frame clipped this at -42
        self.assertGreater(hi, 170.0)
        self.assertEqual(self.results[0]["warnings"], [])


class UniformFamilyUnit(unittest.TestCase):
    def test_grid_family_extent(self):
        grid = [29.0 + 37.5 * i for i in range(13)]
        self.assertEqual(bv._uniform_family_extent(grid), (29.0, 29.0 + 37.5 * 12))

    def test_border_only_rejected(self):
        # newer Infineon panels: the only long strokes are the outer border
        self.assertIsNone(bv._uniform_family_extent([5.4, 679.0]))

    def test_border_lines_trimmed_from_grid(self):
        # a panel border hugs the plot frame within a few px — far off the
        # grid pitch — and must not widen the detected frame
        grid = [50.0 + 40.0 * i for i in range(8)]
        self.assertEqual(bv._uniform_family_extent([42.0, *grid, 338.0]), (50.0, 330.0))

    def test_irregular_rules_rejected(self):
        self.assertIsNone(bv._uniform_family_extent([6.5, 41.4, 300.0, 729.7, 764.6]))


class AxisFitUnit(unittest.TestCase):
    def test_fits_negative_ticks(self):
        ticks = [(-75.0 + 25.0 * i, 100.0 + 40.0 * i) for i in range(12)]
        axis = bv._fit_axis(ticks, "X")
        self.assertAlmostEqual(axis.value(100.0), -75.0, places=6)
        self.assertAlmostEqual(axis.value(500.0), 175.0, places=6)
        self.assertLess(axis.resid, 1e-9)

    def test_too_few_ticks_refused(self):
        with self.assertRaisesRegex(RuntimeError, "need >="):
            bv._fit_axis([(0.0, 0.0), (25.0, 40.0), (50.0, 80.0)], "X")

    def test_non_monotone_ticks_refused(self):
        ticks = [(0.0, 0.0), (25.0, 40.0), (75.0, 80.0), (50.0, 120.0)]
        # the shared fitter phrases this as "not strictly monotone"
        with self.assertRaisesRegex(RuntimeError, "monotone"):
            bv._fit_axis(ticks, "X")

    def test_bad_pairing_residual_refused(self):
        # one label shifted a full grid cell: linear fit residual blows the gate
        ticks = [(0.0, 0.0), (25.0, 40.0), (50.0, 80.0), (100.0, 120.0), (100.1, 160.0)]
        with self.assertRaisesRegex(RuntimeError, "untrusted"):
            bv._fit_axis(ticks, "X")


class SpecParseUnit(unittest.TestCase):
    class _FakePage:
        def __init__(self, text: str):
            self._text = text

        def get_text(self, kind: str) -> str:
            return self._text

    class _FakeDoc(list):
        pass

    def test_parses_table_row(self):
        doc = self._FakeDoc([self._FakePage(
            "Drain-source breakdown voltage\nV(BR)DSS\n80\n-\n-\nV\nVGS=0 V, ID=1 mA")])
        self.assertEqual(bv._spec_min_vbrdss(doc), (80.0, 1))

    def test_parses_old_layout_conditions_between(self):
        # older layout: conditions precede the limit; '=0'/'=1' must not match
        doc = self._FakeDoc([self._FakePage(
            "Drain-source breakdown voltage V (BR)DSS V GS =0 V, I D =1 mA 60 - - V")])
        self.assertEqual(bv._spec_min_vbrdss(doc), (60.0, 1))

    def test_rejects_implausible_and_absent(self):
        self.assertIsNone(bv._spec_min_vbrdss(self._FakeDoc([self._FakePage("V(BR)DSS 5")])))
        self.assertIsNone(bv._spec_min_vbrdss(self._FakeDoc([self._FakePage("no such row here")])))


class CurveCountGuardUnit(unittest.TestCase):
    """_extract_single_trace must refuse unless EXACTLY one full-span curve is
    found — never silently pick one. Exercised directly here: the capacitance
    end-to-end fixture (test_multi_curve_panel_refused) now fails closed one step
    earlier, at axis calibration, so it no longer reaches this guard. The vector
    pipeline feeding the guard is patched so only the candidate COUNT varies."""

    class _Xf:  # identity crop<->pt transform
        def to_pt(self, x, y):
            return (x, y)

        def to_px(self, x, y):
            return (x, y)

    class _Rect:
        def __init__(self, x0, y0, x1, y1):
            self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
            self.width = x1 - x0

    class _Page:
        def get_drawings(self):
            return []

    def _run(self, n_components):
        plot = bv.PlotBox(0, 0, 100, 100)
        # each component spans the full plot width so it passes the span filter
        comps = [[(0.0, 50.0), (100.0, 50.0)] for _ in range(n_components)]
        fitz = type("F", (), {"Rect": self._Rect})
        with patch.object(bv, "_vector_curve_edges", return_value=[]), \
             patch.object(bv, "_chain_vector_components", return_value=comps), \
             patch.object(bv, "_mostly_inside_plot", return_value=True), \
             patch.object(bv, "_resample_vector_trace_pixels", return_value=[(0, 0)] * 60), \
             patch.object(bv, "_path_length", return_value=1.0):
            return bv._extract_single_trace(self._Page(), self._Xf(), plot, fitz)

    def test_two_full_span_curves_refused(self):
        with self.assertRaisesRegex(RuntimeError, r"expected exactly 1.*found 2"):
            self._run(2)

    def test_zero_full_span_curves_refused(self):
        with self.assertRaisesRegex(RuntimeError, r"expected exactly 1.*found 0"):
            self._run(0)

    def test_single_full_span_curve_accepted(self):
        self.assertEqual(len(self._run(1)), 60)


if __name__ == "__main__":
    unittest.main()
