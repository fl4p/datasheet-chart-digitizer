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
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf
import numpy as np

from datasheet_chart_digitizer import breakdown_voltage as bv
from datasheet_chart_digitizer import find_charts
from datasheet_chart_digitizer.crop_transform import CropTransform

INFINEON = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon")
ST = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st")
PARTS = ["IPP040N08NF2S", "IPP024N08NF2S", "IPP022N12NM6"]
HXY_SPD = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/hxy/SPD03N50C3ATMA1-HXY.pdf")


def _find_and_digitize(parts: list[str]):
    return _find_and_digitize_paths([INFINEON / f"{part}.pdf" for part in parts])


def _find_and_digitize_paths(paths: list[Path]):
    out = Path(tempfile.mkdtemp(prefix="bv-test-"))
    panels = []
    for pdf in paths:
        panels.extend(find_charts.process_pdf(pdf, out, dpi=180))
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
    def test_table_value_parser_rejects_conditions_and_accepts_value_column(self):
        self.assertIsNone(bv._row_vbrdss_value(", VDD = 400 V. Thermal data"))
        self.assertIsNone(
            bv._row_vbrdss_value(
                " Drain-source breakdown voltage VGS = 0 V, ID = 1 mA 650 V IDSS"
            )
        )
        self.assertEqual(bv._row_vbrdss_value(" -30 - - V IDSS"), -30.0)
        self.assertIsNone(
            bv._row_vbrdss_value(
                " Breakdown voltage versus junction temperature 100 V"
            )
        )

    """Guard calibration: constructed failures must FAIL, not pass silently."""

    @classmethod
    def setUpClass(cls):
        cls.charts, cls.results, cls.out = _find_and_digitize(["IPP040N08NF2S"])

    def _chart(self, kind: str) -> dict:
        return next(c for c in self.charts if c["kind"] == kind)

    def test_multi_curve_panel_refused(self):
        # a capacitance chart (multiple curves) must be refused, not silently
        # digitized. The axis fit moved onto the shared numeric_axis core but
        # breakdown forces model="linear", so the collinear axis still calibrates
        # and the input reaches the downstream curve-count guard exactly as before
        # (CurveCountGuardUnit covers that guard directly too).
        cap = self._chart("capacitances")
        with self.assertRaisesRegex(RuntimeError, "expected exactly 1"):
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
        self.assertTrue(any("does not satisfy" in w for w in r["warnings"]))

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

    def test_absolute_axis_typical_curve_may_exceed_spec_minimum(self):
        status, anchor, warnings = bv._anchor_breakdown_curve(
            81.34, "absolute_axis", (75.0, 2)
        )
        self.assertEqual(status, "verified")
        self.assertEqual(anchor["comparison"], "absolute_axis_meets_spec_min")
        self.assertGreater(anchor["err"], 0.08)
        self.assertEqual(warnings, [])

    def test_absolute_axis_curve_below_minimum_fails(self):
        status, _, warnings = bv._anchor_breakdown_curve(
            70.0, "absolute_axis", (75.0, 2)
        )
        self.assertEqual(status, "FAIL")
        self.assertTrue(any("does not satisfy" in warning for warning in warnings))

    def test_conflicting_maximum_and_table_ratings_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "conflicting source-owned"):
            bv._validate_rating_consistency((-100.0, 2), (-60.0, 1))

        bv._validate_rating_consistency((-100.0, 2), (-100.0, 1))

    def test_signed_absolute_axis_uses_magnitude_and_polarity(self):
        self.assertEqual(
            bv._anchor_breakdown_curve(-31.0, "absolute_axis", (-30.0, 2))[0],
            "verified",
        )
        self.assertEqual(
            bv._anchor_breakdown_curve(31.0, "absolute_axis", (-30.0, 2))[0],
            "FAIL",
        )
        self.assertEqual(
            bv._anchor_breakdown_curve(
                31.0, "absolute_magnitude_axis", (-30.0, 2)
            )[0],
            "verified",
        )

    def test_avalanche_energy_panel_is_not_breakdown_voltage(self):
        with self.assertRaisesRegex(RuntimeError, "avalanche energy"):
            bv._validate_breakdown_panel_semantics(
                {
                    "title": "Drain-to-Source Breakdown Voltage",
                    "text": "Single Pulse Avalanche Energy EAS (mJ) versus TJ",
                }
            )


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


@unittest.skipUnless(INFINEON.exists() and ST.exists(), "local datasheet library not available")
class CaptionAndVectorEncodingRegressions(unittest.TestCase):
    def test_drawn_minus_glyphs_recover_bsb028_breakdown_temperature_axis(self):
        pdf = INFINEON / "BSB028N06NN3_G.pdf"
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        _, results, _ = _find_and_digitize_paths([pdf])
        result = next(item for item in results if item["part"] == pdf.stem)

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["frame_method"], "vector")
        self.assertEqual(result["source_trace_points"], 415)
        self.assertEqual(result["n_points"], 415)
        self.assertEqual(result["withheld_source_points"], 0)
        self.assertEqual(result["calibration"]["x_ticks"], 7)
        self.assertLessEqual(result["calibration"]["x_exact_center_max_px"], 1.0)
        self.assertAlmostEqual(result["v_at_25c"], 60.0, delta=0.2)
        self.assertAlmostEqual(result["slope_mv_per_k"], 35.0, delta=2.0)
        self.assertEqual(result["warnings"], [])

    def test_large_label_gutter_keeps_source_plot_frame(self):
        _, results, _ = _find_and_digitize_paths([INFINEON / "IRF1018E.pdf"])
        result = next(item for item in results if item["part"] == "IRF1018E")
        self.assertEqual(result["frame_method"], "vector")
        self.assertEqual(result["status"], "verified")
        self.assertAlmostEqual(result["v_at_25c"], 66.8, delta=0.5)

    def test_filled_and_stroked_st_curve_is_recovered(self):
        _, results, _ = _find_and_digitize_paths([ST / "STF7N60M2.pdf"])
        result = next(item for item in results if item["part"] == "STF7N60M2")
        self.assertEqual(result["status"], "verified")
        self.assertGreater(result["n_points"], 250)
        self.assertAlmostEqual(result["v_at_25c"], 600.0, delta=15.0)

    def test_pure_filled_st_curve_and_column_owned_minimum_are_recovered(self):
        _, results, _ = _find_and_digitize_paths([ST / "STD14NM50NAG.pdf"])
        result = next(item for item in results if item["part"] == "STD14NM50NAG")
        self.assertEqual(result["status"], "verified")
        self.assertGreater(result["n_points"], 300)
        self.assertTrue(result["served_trace_clipped_to_one_unlabeled_interval"])
        self.assertEqual(result["source_trace_points"], 374)
        self.assertEqual(result["n_points"], 325)
        self.assertEqual(result["withheld_source_points"], 49)
        self.assertEqual(
            result["warnings"],
            ["withheld 49 source points beyond one unlabeled X-axis interval"],
        )
        self.assertLessEqual(result["tj_range_c"][1], 126.0)
        self.assertGreaterEqual(result["tj_range_c"][1], 124.0)
        self.assertLessEqual(result["calibration"]["x_exact_center_max_px"], 1.0)
        self.assertLessEqual(result["calibration"]["y_exact_center_max_px"], 1.0)
        self.assertAlmostEqual(result["v_at_25c"], 500.0, delta=2.0)
        self.assertAlmostEqual(result["slope_mv_per_k"], 472.0, delta=12.0)

    def test_full_labeled_x_span_does_not_claim_raster_clip(self):
        _, results, _ = _find_and_digitize_paths([INFINEON / "IPA60R125CFD7.pdf"])
        result = next(item for item in results if item["part"] == "IPA60R125CFD7")

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["frame_method"], "raster")
        self.assertEqual(result["source_trace_points"], 443)
        self.assertEqual(result["withheld_source_points"], 0)
        self.assertFalse(any("CLIPPED" in warning for warning in result["warnings"]))
        self.assertAlmostEqual(result["v_at_25c"], 600.0, delta=1.0)

    def test_verified_vector_frame_contact_keeps_provenance_flag(self):
        pdf = INFINEON / "AIMDQ75R004M2H.pdf"
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        _, results, _ = _find_and_digitize_paths([pdf])
        result = next(item for item in results if item["part"] == pdf.stem)

        self.assertEqual(result["frame_method"], "vector")
        self.assertTrue(result["source_trace_clipped_to_verified_vector_frame"])
        self.assertFalse(any("CLIPPED" in warning for warning in result["warnings"]))

    def test_thin_stroked_st_curve_is_recovered(self):
        _, results, _ = _find_and_digitize_paths([ST / "ST8L65N044M9.pdf"])
        result = next(item for item in results if item["part"] == "ST8L65N044M9")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["frame_method"], "vector")
        self.assertGreater(result["n_points"], 300)
        self.assertAlmostEqual(result["v_at_25c"], 650.0, delta=2.0)

    def test_neighbor_chart_rail_is_rejected_by_owned_y_tick_ladder(self):
        _, results, _ = _find_and_digitize_paths([ST / "STH310N10F7-2.pdf"])
        result = next(item for item in results if item["part"] == "STH310N10F7-2")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["frame_method"], "raster_y_tick_owned")
        self.assertGreater(result["n_points"], 250)
        self.assertLessEqual(result["calibration"]["x_exact_center_max_px"], 1.0)
        self.assertLessEqual(result["calibration"]["y_exact_center_max_px"], 1.0)
        self.assertAlmostEqual(result["v_at_25c"], 100.0, delta=1.0)


@unittest.skipUnless(HXY_SPD.exists(), "local HXY datasheet unavailable")
class NormalizedBreakdownEndToEnd(unittest.TestCase):
    def test_normalized_curve_uses_source_table_minimum(self):
        out = Path(tempfile.mkdtemp(prefix="bv-hxy-test-"))
        panels = find_charts.process_pdf(HXY_SPD, out, dpi=220)
        chart = next(find_charts.asdict(panel) for panel in panels if panel.kind == "breakdown_voltage")

        result = bv.process_chart(
            chart,
            out / chart["crop_png"],
            out / "digitized",
            Path(chart["crop_png"]).with_suffix(""),
        )

        self.assertEqual(result["value_basis"], "normalized_to_spec_min")
        self.assertEqual(result["status"], "verified")
        self.assertAlmostEqual(result["v_at_25c"], 500.0, delta=1.0)
        self.assertAlmostEqual(result["slope_mv_per_k"], 620.0, delta=20.0)


class UniformFamilyUnit(unittest.TestCase):
    def test_owned_y_tick_ladder_reseats_foreign_neighbor_rail(self):
        gray = np.full((498, 763), 255, dtype=np.uint8)
        plot = bv.PlotBox(209, 55, 723, 445)
        words = [
            (f"{value:.2f}", x, y)
            for value, x, y in zip(
                (1.04, 1.02, 1.00, 0.98, 0.96, 0.94),
                (335, 334, 332, 332, 333, 334),
                (119, 182, 248, 314, 380, 440),
            )
        ]
        lines = (208, 362, 397, 434, 470, 506, 542, 578, 614, 650, 686, 722, 758)

        with patch.object(bv, "_full_span_grid_lines", return_value=(lines, ())):
            repaired = bv._repair_raster_plot_from_owned_y_ticks(gray, words, plot)

        self.assertEqual(repaired, bv.PlotBox(362, 55, 758, 445))

    def test_neighbor_rail_repair_requires_owned_numeric_ladder(self):
        gray = np.full((498, 763), 255, dtype=np.uint8)
        plot = bv.PlotBox(209, 55, 723, 445)
        lines = (208, 362, 397, 434, 470, 506, 542, 578, 614, 650, 686, 722, 758)

        with patch.object(bv, "_full_span_grid_lines", return_value=(lines, ())):
            repaired = bv._repair_raster_plot_from_owned_y_ticks(
                gray,
                [("1.04", 335, 119), ("0.94", 334, 440)],
                plot,
            )

        self.assertEqual(repaired, plot)

    def test_closed_vector_frame_completes_one_missing_right_interval(self):
        raster = bv.PlotBox(66, 4, 467, 463)
        outer = bv.PlotBox(66, 6, 524, 463)
        grid = [66, 124, 181, 238, 295, 352, 409, 467]

        self.assertEqual(
            bv._complete_raster_plot_with_closed_vector_frame(raster, outer, grid),
            outer,
        )

    def test_closed_vector_frame_rejects_foreign_or_remote_edges(self):
        raster = bv.PlotBox(66, 4, 467, 463)
        grid = [66, 124, 181, 238, 295, 352, 409, 467]

        self.assertIsNone(
            bv._complete_raster_plot_with_closed_vector_frame(
                raster, bv.PlotBox(66, 35, 524, 490), grid
            )
        )
        self.assertIsNone(
            bv._complete_raster_plot_with_closed_vector_frame(
                raster, bv.PlotBox(66, 6, 590, 463), grid
            )
        )

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

    def test_explicit_outer_rectangle_completes_uniform_grid_family(self):
        items = [("re", pymupdf.Rect(10, 10, 290, 210), 1)]
        items.extend(
            ("l", pymupdf.Point(x, 10), pymupdf.Point(x, 210))
            for x in (50, 90, 130, 170, 210, 250)
        )
        items.extend(
            ("l", pymupdf.Point(10, y), pymupdf.Point(290, y))
            for y in (50, 90, 130, 170)
        )
        page = SimpleNamespace(
            get_drawings=lambda: [{"type": "s", "items": items}]
        )

        plot = bv._vector_plot_frame(
            page, CropTransform(0.0, 0.0, 1.0, 1.0), (220, 300)
        )

        self.assertIsNotNone(plot)
        self.assertEqual((plot.x0, plot.y0, plot.x1, plot.y1), (10, 10, 290, 210))


class AxisFitUnit(unittest.TestCase):
    def test_fits_negative_ticks(self):
        ticks = [(-75.0 + 25.0 * i, 100.0 + 40.0 * i) for i in range(12)]
        axis = bv._fit_axis(ticks, "X")
        self.assertAlmostEqual(axis.value(100.0), -75.0, places=6)
        self.assertAlmostEqual(axis.value(500.0), 175.0, places=6)
        self.assertLess(axis.resid, 1e-9)

    def test_narrow_positive_linear_axis_accepted(self):
        # regression: a valid narrow-positive linear axis (values 100..103) is
        # near-indistinguishable from log, so the shared fitter's "auto" mode
        # would false-refuse it as ambiguous. breakdown forces model="linear",
        # so this exact case must calibrate, not raise. (codex-ee-root's BLOCK.)
        axis = bv._fit_axis([(100.0, 0.0), (101.0, 100.0), (102.0, 200.0), (103.0, 300.0)], "probe")
        self.assertAlmostEqual(axis.value(0.0), 100.0, places=6)
        self.assertAlmostEqual(axis.value(300.0), 103.0, places=6)

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

    def test_accepts_source_owned_taller_x_label_gutter(self):
        plot = bv.PlotBox(86, 30, 654, 415)
        words = [
            (str(value), pixel, 442.5)
            for value, pixel in zip(range(-80, 201, 40), np.linspace(86, 654, 8))
        ]
        words.extend(
            (f"{value:.2f}", 36.0, pixel)
            for value, pixel in zip(np.linspace(1.20, 0.80, 9), np.linspace(30, 415, 9))
        )

        x_axis, y_axis = bv._calibrate(words, plot, 444)

        self.assertAlmostEqual(x_axis.value(86), -80.0, places=5)
        self.assertAlmostEqual(y_axis.value(415), 0.80, places=5)

    def test_refits_label_centers_to_observed_grid_centers(self):
        axis = bv._fit_axis(
            [(0.0, 12.0), (25.0, 49.0), (50.0, 91.0), (75.0, 128.0)],
            "X",
        )

        snapped, assertions = bv._snap_axis_ticks_to_grid(
            axis, [10.0, 50.0, 90.0, 130.0, 170.0], "X"
        )

        self.assertEqual([tick[1] for tick in snapped.ticks], [10.0, 50.0, 90.0, 130.0])
        self.assertLessEqual(max(item["fit_to_grid_px"] for item in assertions), 1.0)

    def test_center_constrained_refit_keeps_one_pixel_contract(self):
        values = [1.11, 1.09, 1.07, 1.05, 1.03, 1.01, 0.99, 0.97, 0.95, 0.93]
        labels = [53.62, 99.28, 145.26, 188.19, 234.75, 280.71, 326.28, 372.25, 414.64, 457.96]
        grid = [4.0, 52.0, 96.0, 142.0, 188.0, 234.0, 279.0, 324.0, 370.0, 416.0, 463.0]
        axis = bv._fit_axis(list(zip(values, labels)), "Y")

        _snapped, assertions = bv._snap_axis_ticks_to_grid(axis, grid, "Y")

        self.assertLessEqual(max(item["fit_to_grid_px"] for item in assertions), 1.0)

    def test_center_constrained_refit_refuses_infeasible_grid(self):
        axis = bv.LinearAxis(0.1, 0.0, [(0, 0), (1, 10), (2, 30), (3, 40)], 0.0)
        assignments = [(value, pixel, pixel) for value, pixel in axis.ticks]

        with self.assertRaisesRegex(RuntimeError, "no linear fit"):
            bv._refit_axis_with_center_tolerance(axis, assignments, "Y")

    def test_grid_center_binding_rejects_duplicate_ownership(self):
        axis = bv.LinearAxis(
            1.0,
            0.0,
            [(0.0, 10.0), (25.0, 19.0), (50.0, 50.0), (75.0, 90.0)],
            0.0,
        )
        with self.assertRaisesRegex(RuntimeError, "multiple labels"):
            bv._snap_axis_ticks_to_grid(axis, [10.0, 50.0, 90.0, 130.0], "X")

    def test_trace_extent_allows_only_one_unlabeled_interval(self):
        axis = bv.LinearAxis(
            1.0,
            0.0,
            [(0.0, 10.0), (25.0, 50.0), (50.0, 90.0), (75.0, 130.0)],
            0.0,
        )
        points = [(x, 50) for x in range(0, 220)]

        served, withheld = bv._clip_points_to_one_unlabeled_interval(points, axis)

        self.assertEqual((served[0][0], served[-1][0]), (0, 170))
        self.assertEqual(withheld, 49)

    def test_full_x_span_inside_y_frame_is_not_a_clip(self):
        plot = bv.PlotBox(10, 10, 110, 110)
        points = [(x, 90 - (x - 10) // 2) for x in range(10, 111)]

        self.assertFalse(bv._trace_runs_along_horizontal_frame(points, plot))

    def test_single_y_frame_contact_is_not_a_clip(self):
        plot = bv.PlotBox(10, 10, 110, 110)
        points = [(x, 70 - x) for x in range(10, 61)]

        self.assertFalse(bv._trace_runs_along_horizontal_frame(points, plot))

    def test_sustained_horizontal_y_frame_run_is_a_clip(self):
        plot = bv.PlotBox(10, 10, 110, 110)
        points = [(x, 60 - x // 2) for x in range(10, 81)]
        points.extend((x, 11) for x in range(81, 101))

        self.assertTrue(bv._trace_runs_along_horizontal_frame(points, plot))


class TemperatureTickSignRecoveryUnit(unittest.TestCase):
    @staticmethod
    def _dash(x0, y0=104.75, *, fill=(0.0, 0.0, 0.0), closed=True):
        x1, y1 = x0 + 4.0, y0 + 0.5
        points = [
            (pymupdf.Point(x0, y0), pymupdf.Point(x1, y0)),
            (pymupdf.Point(x1, y0), pymupdf.Point(x1, y1)),
            (pymupdf.Point(x1, y1), pymupdf.Point(x0, y1)),
            (pymupdf.Point(x0, y1), pymupdf.Point(x0, y0)),
        ]
        if not closed:
            points[-1] = (pymupdf.Point(x0, y1), pymupdf.Point(x0 + 1.0, y0))
        return {
            "type": "f",
            "fill": fill,
            "fill_opacity": 1.0,
            "rect": pymupdf.Rect(x0, y0, x1, y1),
            "items": [("l", start, end) for start, end in points],
        }

    @staticmethod
    def _page(words, drawings):
        return SimpleNamespace(
            get_text=lambda _kind: words,
            get_drawings=lambda: drawings,
        )

    def test_source_owned_filled_rectangles_restore_negative_ticks(self):
        words = [
            (20.0, 100.0, 28.0, 110.0, "60"),
            (60.0, 100.0, 68.0, 110.0, "20"),
            (100.0, 100.0, 108.0, 110.0, "20"),
            (140.0, 100.0, 148.0, 110.0, "60"),
        ]
        ticks = bv._owned_temperature_x_ticks(
            self._page(words, [self._dash(15.0), self._dash(55.0)]),
            CropTransform(0.0, 0.0, 1.0, 1.0),
            (200, 200),
            bv.PlotBox(20, 20, 148, 100),
        )

        self.assertEqual([value for value, _pixel in ticks], [-60, -20, 20, 60])
        self.assertEqual(
            [pixel for _value, pixel in ticks], [21.5, 61.5, 104.0, 144.0]
        )
        axis = bv._fit_axis(ticks, "X axis (Tj)")
        self.assertAlmostEqual(axis.value(21.5), -60.0, delta=0.3)

    def test_light_distant_open_and_vertical_marks_do_not_supply_sign(self):
        word = [(20.0, 100.0, 28.0, 110.0, "60")]
        vertical = self._dash(15.0)
        vertical["rect"] = pymupdf.Rect(17.0, 101.0, 17.5, 105.0)
        distant = self._dash(5.0)
        light = self._dash(15.0, fill=(0.8, 0.8, 0.8))
        open_path = self._dash(15.0, closed=False)

        for drawing in (vertical, distant, light, open_path):
            ticks = bv._owned_temperature_x_ticks(
                self._page(word, [drawing]),
                CropTransform(0.0, 0.0, 1.0, 1.0),
                (200, 200),
                bv.PlotBox(20, 20, 148, 100),
            )
            self.assertEqual(ticks, [(60.0, 24.0)])

    def test_two_eligible_minus_glyphs_refuse_as_ambiguous(self):
        word = [(20.0, 100.0, 28.0, 110.0, "60")]
        with self.assertRaisesRegex(RuntimeError, "ambiguous drawn-minus"):
            bv._owned_temperature_x_ticks(
                self._page(word, [self._dash(15.0), self._dash(15.5)]),
                CropTransform(0.0, 0.0, 1.0, 1.0),
                (200, 200),
                bv.PlotBox(20, 20, 148, 100),
            )

    def test_unicode_minus_is_consumed_without_drawn_geometry(self):
        ticks = bv._owned_temperature_x_ticks(
            self._page([(20.0, 100.0, 28.0, 110.0, "−60")], []),
            CropTransform(0.0, 0.0, 1.0, 1.0),
            (200, 200),
            bv.PlotBox(20, 20, 148, 100),
        )
        self.assertEqual(ticks, [(-60.0, 24.0)])

    def test_fully_unsigned_progression_remains_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "monotone"):
            bv._fit_axis(
                list(zip([60, 20, 20, 60, 100, 140, 180], range(0, 280, 40))),
                "X axis (Tj)",
            )


class SpecParseUnit(unittest.TestCase):
    class _FakePage:
        def __init__(self, text: str):
            self._text = text

        def get_text(self, kind: str) -> str:
            return self._text

    class _FakeDoc(list):
        pass

    class _PositionedPage:
        def get_text(self, kind: str):
            if kind == "text":
                return "V(BR)DSS Drain-source breakdown voltage ID=1mA VGS=0V 500 V"
            return [
                (445.5, 180.0, 461.5, 190.0, "Min", 0, 0, 0),
                (530.8, 180.0, 546.4, 190.0, "Unit", 0, 0, 1),
                (111.7, 201.0, 145.0, 211.0, "V(BR)DSS", 1, 0, 0),
                (160.0, 201.0, 270.0, 211.0, "Drain-source breakdown voltage", 1, 0, 1),
                (286.0, 201.0, 361.0, 211.0, "ID=1mA,VGS=0V", 1, 0, 2),
                (446.9, 201.0, 460.2, 211.0, "500", 1, 0, 3),
                (535.9, 201.0, 541.3, 211.0, "V", 1, 0, 4),
                (111.7, 221.0, 145.0, 231.0, "IDSS", 2, 0, 0),
                (330.0, 221.0, 370.0, 231.0, "VDS=500", 2, 0, 1),
            ]

    def test_parses_table_row(self):
        doc = self._FakeDoc([self._FakePage(
            "Drain-source breakdown voltage\nV(BR)DSS\n80\n-\n-\nV\nVGS=0 V, ID=1 mA")])
        self.assertEqual(bv._spec_min_vbrdss(doc), (80.0, 1))

    def test_parses_only_the_positioned_minimum_cell(self):
        self.assertEqual(
            bv._spec_min_vbrdss(self._FakeDoc([self._PositionedPage()])),
            (500.0, 1),
        )

    def test_positioned_parser_rejects_wrong_unit_column(self):
        page = self._PositionedPage()
        words = page.get_text("words")
        words[6] = (490.0, 201.0, 495.0, 211.0, "V", 1, 0, 4)
        page.get_text = lambda kind: words if kind == "words" else ""
        self.assertIsNone(bv._positioned_min_vbrdss(page))

    def test_parses_old_layout_conditions_between(self):
        # older layout: conditions precede the limit; '=0'/'=1' must not match
        doc = self._FakeDoc([self._FakePage(
            "Drain-source breakdown voltage V (BR)DSS V GS =0 V, I D =1 mA 60 - - V")])
        self.assertEqual(bv._spec_min_vbrdss(doc), (60.0, 1))

    def test_parses_labeled_row_when_pdf_text_drops_the_subscripted_symbol(self):
        doc = self._FakeDoc([self._FakePage(
            "Static characteristics Parameter Symbol Values Min. Typ. Max. "
            "Drain-source breakdown voltage 5) V 840 - - V VGS=0 V, ID=5.54 mA"
        )])
        self.assertEqual(bv._spec_min_vbrdss(doc), (840.0, 1))

    def test_labeled_prose_without_table_dash_slots_stays_refused(self):
        doc = self._FakeDoc([self._FakePage(
            "The drain-source breakdown voltage is rated at 840 V under test."
        )])
        self.assertIsNone(bv._spec_min_vbrdss(doc))

    def test_rejects_implausible_and_absent(self):
        self.assertIsNone(bv._spec_min_vbrdss(self._FakeDoc([self._FakePage("V(BR)DSS 5")])))
        self.assertIsNone(bv._spec_min_vbrdss(self._FakeDoc([self._FakePage("no such row here")])))

    def test_normalized_chart_without_source_table_minimum_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "absolute values are unverified"):
            bv._breakdown_value_scale(
                {"title": "Typical normalized breakdown voltage vs temperature"},
                None,
            )

    def test_absolute_chart_does_not_require_table_scaling(self):
        self.assertEqual(
            bv._breakdown_value_scale(
                {"title": "Breakdown voltage vs temperature"}, None
            ),
            (1.0, "absolute_axis"),
        )

    def test_ratio_ticks_prove_normalized_axis_when_title_is_short(self):
        axis = bv.LinearAxis(
            1.0,
            0.0,
            [(0.9, 10.0), (1.0, 20.0), (1.1, 30.0), (1.2, 40.0)],
            0.0,
        )
        self.assertEqual(
            bv._breakdown_value_scale(
                {"title": "Drain-source breakdown voltage"},
                (120.0, 2),
                axis,
            ),
            (120.0, "normalized_to_spec_min"),
        )

    def test_positive_ticks_with_negative_spec_record_magnitude_basis(self):
        axis = bv.LinearAxis(
            1.0,
            0.0,
            [(200.0, 10.0), (225.0, 20.0), (250.0, 30.0), (275.0, 40.0)],
            0.0,
        )
        self.assertEqual(
            bv._breakdown_value_scale(
                {"title": "Drain-source breakdown voltage"},
                (-250.0, 2),
                axis,
            ),
            (1.0, "absolute_magnitude_axis"),
        )


class FusedTickRunUnit(unittest.TestCase):
    def test_splits_three_digit_arithmetic_tick_run(self):
        self.assertEqual(
            bv._split_fused_numeric_run("100120140160180"),
            [100.0, 120.0, 140.0, 160.0, 180.0],
        )

    def test_splits_two_digit_arithmetic_tick_run(self):
        self.assertEqual(bv._split_fused_numeric_run("10203040"), [10.0, 20.0, 30.0, 40.0])

    def test_does_not_split_short_or_non_arithmetic_number(self):
        self.assertEqual(bv._split_fused_numeric_run("120"), [])
        self.assertEqual(bv._split_fused_numeric_run("100125180"), [])


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
        comps = [[(0.0, 60.0), (100.0, 40.0)] for _ in range(n_components)]
        fitz = type("F", (), {"Rect": self._Rect})
        with patch.object(bv, "_vector_curve_edges", return_value=[]), \
             patch.object(bv, "_chain_vector_components", return_value=comps), \
             patch.object(bv, "_path_length", return_value=1.0):
            return bv._extract_single_trace(self._Page(), self._Xf(), plot, fitz)

    def test_two_full_span_curves_refused(self):
        with self.assertRaisesRegex(RuntimeError, r"expected exactly 1.*found 2"):
            self._run(2)

    def test_zero_full_span_curves_refused(self):
        with self.assertRaisesRegex(RuntimeError, r"expected exactly 1.*found 0"):
            self._run(0)

    def test_single_full_span_curve_accepted(self):
        self.assertGreaterEqual(len(self._run(1)), 100)

    def test_flat_full_span_gridline_is_not_counted_as_a_curve(self):
        plot = bv.PlotBox(0, 0, 100, 100)
        fitz = type("F", (), {"Rect": self._Rect})
        components = [[(0.0, 50.0), (100.0, 50.0)], [(0.0, 60.0), (100.0, 40.0)]]
        with patch.object(bv, "_vector_curve_edges", return_value=[]), \
             patch.object(bv, "_chain_vector_components", return_value=components):
            points = bv._extract_single_trace(self._Page(), self._Xf(), plot, fitz)
        self.assertGreaterEqual(len(points), 100)

    def test_two_vertex_curve_protruding_past_both_sides_is_clipped_and_dense(self):
        plot = bv.PlotBox(0, 0, 100, 100)
        fitz = type("F", (), {"Rect": self._Rect})
        component = [[(-2.2, 80.0), (102.2, 20.0)]]
        with patch.object(bv, "_vector_curve_edges", return_value=[]), \
             patch.object(bv, "_chain_vector_components", return_value=component):
            points = bv._extract_single_trace(self._Page(), self._Xf(), plot, fitz)
        self.assertEqual((points[0][0], points[-1][0]), (0, 100))
        self.assertGreaterEqual(len(points), 100)

    def test_short_interior_line_stays_rejected(self):
        plot = bv.PlotBox(0, 0, 100, 100)
        fitz = type("F", (), {"Rect": self._Rect})
        with patch.object(bv, "_vector_curve_edges", return_value=[]), \
             patch.object(
                 bv, "_chain_vector_components",
                 return_value=[[(20.0, 70.0), (40.0, 60.0)]],
             ):
            with self.assertRaisesRegex(RuntimeError, r"found 0"):
                bv._extract_single_trace(self._Page(), self._Xf(), plot, fitz)

    def test_off_frame_annotation_line_stays_rejected(self):
        plot = bv.PlotBox(0, 0, 100, 100)
        fitz = type("F", (), {"Rect": self._Rect})
        with patch.object(bv, "_vector_curve_edges", return_value=[]), \
             patch.object(
                 bv, "_chain_vector_components",
                 return_value=[[(-2.0, -10.0), (102.0, -10.0)]],
             ):
            with self.assertRaisesRegex(RuntimeError, r"found 0"):
                bv._extract_single_trace(self._Page(), self._Xf(), plot, fitz)

    def test_white_full_span_fill_stays_rejected(self):
        plot = bv.PlotBox(0, 0, 100, 100)
        fitz = type("F", (), {"Rect": self._Rect})
        page = SimpleNamespace(
            get_drawings=lambda: [{"type": "f", "fill": (1.0, 1.0, 1.0)}]
        )
        with patch.object(bv, "_vector_curve_edges", return_value=[]), \
             patch.object(bv, "_chain_vector_components", return_value=[]), \
             patch.object(
                 bv,
                 "_filled_path_centerline",
                 return_value=[(0.0, 80.0), (100.0, 20.0)],
             ) as centerline:
            with self.assertRaisesRegex(RuntimeError, r"found 0"):
                bv._extract_single_trace(page, self._Xf(), plot, fitz)
        centerline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
