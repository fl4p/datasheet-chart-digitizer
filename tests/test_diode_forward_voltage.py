from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.diode_forward_voltage import (
    PanelCalibration,
    _anchor_linear_axis_to_plot_frame,
    _draw_overlay,
    _expanded_grid_search_box,
    _join_vector_paths,
    _normalize_numeric_text,
    _physical_plot_hint,
    _snap_axis_to_grid,
    calibrate_panel,
    digitize_pdf,
)
from datasheet_chart_digitizer.find_charts import ChartPanel, process_pdf
from datasheet_chart_digitizer.numeric_axis import AxisTick, NumericAxis, fit_numeric_axis, tick_aligned_plot


class NumericAxisTests(unittest.TestCase):
    def test_overlay_crosshairs_are_centered_on_axis_tick_intersections(self):
        x_axis = NumericAxis(
            "linear", 0.01, 0.0, (AxisTick("0", 0.0, 10), AxisTick("1", 1.0, 90)), 0.0, ()
        )
        y_axis = NumericAxis(
            "linear", -0.01, 1.0, (AxisTick("1", 1.0, 10), AxisTick("0", 0.0, 90)), 0.0, ()
        )
        plot = PlotBox(10, 10, 90, 90)
        calibration = PanelCalibration(plot, x_axis, y_axis, plot, "synthetic")
        panel = ChartPanel(
            "sample.pdf", "sample", 1, 1, "Body Diode", "body_diode", (0, 0, 1, 1),
            (0, 0, 1, 1), "crop.png", "", "", [],
        )
        with patch(
            "datasheet_chart_digitizer.diode_forward_voltage.cv2.imread",
            return_value=np.full((100, 100, 3), 255, dtype=np.uint8),
        ), patch("datasheet_chart_digitizer.overlay.cv2.drawMarker") as marker:
            overlay = _draw_overlay(Path("crop.png"), calibration, [], panel)

        centers = [call.args[1] for call in marker.call_args_list]
        self.assertEqual(centers, [(10, 90), (90, 90), (10, 10), (10, 90)])
        self.assertEqual(overlay.shape, (133, 100, 3))

    def test_structured_exponents_decode_but_raw_run_refuses(self):
        explicit = fit_numeric_axis(
            [("10^2", 200), ("10^0", 600), ("10^3", 0), ("10^1", 400)],
            "structured exponent",
        )
        self.assertEqual(sorted(tick.value for tick in explicit.ticks), [1, 10, 100, 1000])
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            fit_numeric_axis(
                [("100", 600), ("101", 400), ("102", 200), ("103", 0)],
                "raw run",
            )

        ordinary = fit_numeric_axis(
            [("1", 400), ("10", 300), ("100", 200), ("400", 140)],
            "FDA current",
        )
        self.assertEqual(ordinary.model, "log10")
        self.assertEqual([tick.value for tick in ordinary.ticks], [400, 100, 10, 1])

    def test_axis_model_selection_is_generic_for_linear_and_log_x(self):
        linear = fit_numeric_axis([("0", 10), ("0.5", 60), ("1.0", 110)], "linear X")
        logarithmic = fit_numeric_axis([("1", 10), ("10", 60), ("100", 110)], "log X")
        self.assertEqual(linear.model, "linear")
        self.assertEqual(logarithmic.model, "log10")

    def test_single_tick_and_ambiguous_two_positive_ticks_refuse(self):
        with self.assertRaisesRegex(RuntimeError, "need >=2"):
            fit_numeric_axis([("0", 10)], "single")
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            fit_numeric_axis([("1", 10), ("10", 100)], "ambiguous")

    def test_tick_edges_override_a_distant_detector_edge(self):
        x_axis = fit_numeric_axis(
            [("0.0", 197), ("0.5", 351), ("1.0", 517), ("1.5", 663)],
            "FDA X",
        )
        y_axis = fit_numeric_axis(
            [("400", 92), ("100", 168), ("10", 294), ("1", 424)],
            "FDA Y",
        )
        plot = tick_aligned_plot(x_axis, y_axis, PlotBox(189, 88, 670, 510))
        self.assertEqual(plot, PlotBox(189, 88, 670, 424))

    def test_detector_hint_cannot_crop_a_consumed_outer_tick(self):
        x_axis = fit_numeric_axis([("0", 10), ("1", 90)], "X")
        y_axis = fit_numeric_axis([("100", 10), ("10", 50), ("1", 90)], "Y")
        plot = tick_aligned_plot(x_axis, y_axis, PlotBox(12, 12, 88, 82))
        self.assertEqual(plot, PlotBox(10, 10, 90, 90))

    def test_detector_hint_can_include_a_partial_outer_log_interval(self):
        x_axis = fit_numeric_axis([("0", 10), ("1", 90)], "X")
        y_axis = fit_numeric_axis([("100", 40), ("10", 140), ("1", 240)], "Y")
        self.assertEqual(
            tick_aligned_plot(x_axis, y_axis, PlotBox(10, 10, 90, 240)),
            PlotBox(10, 10, 90, 240),
        )
        self.assertEqual(
            tick_aligned_plot(x_axis, y_axis, PlotBox(10, 0, 90, 240)),
            PlotBox(10, 40, 90, 240),
        )

    def test_pdf_scientific_tick_text_normalizes_for_the_shared_axis_fitter(self):
        self.assertEqual(_normalize_numeric_text("1E-3"), "0.001")
        self.assertEqual(_normalize_numeric_text("2.5e-2"), "0.025")
        self.assertEqual(_normalize_numeric_text("ordinary"), "ordinary")

    def test_split_vector_strokes_join_at_either_endpoint(self):
        paths = [
            [(10.0, 50.0), (10.0, 90.0)],
            [(20.0, 10.0), (10.0, 50.0)],
            [(80.0, 90.0), (70.0, 50.0)],
            [(70.0, 50.0), (60.0, 10.0)],
        ]
        groups = _join_vector_paths(paths, 100.0)
        self.assertEqual(len(groups), 2)
        self.assertEqual((min(y for _, y in groups[0]), max(y for _, y in groups[0])), (10, 90))
        self.assertEqual((min(y for _, y in groups[1]), max(y for _, y in groups[1])), (10, 90))

    def test_detector_extension_requires_a_full_span_physical_line(self):
        x_axis = fit_numeric_axis([("0", 10), ("1", 90)], "X")
        y_axis = fit_numeric_axis([("100", 20), ("10", 50), ("1", 80)], "Y")
        hint = _physical_plot_hint(
            PlotBox(10, 5, 90, 95),
            x_axis,
            y_axis,
            (10.0, 90.0),
            (5.0, 20.0, 50.0, 80.0),
        )
        self.assertEqual(hint, PlotBox(10, 5, 90, 80))

        # A partial logarithmic interval above the first labelled decade can
        # be real plot area even when no horizontal top frame is printed.  The
        # unsupported lower extension remains rejected as label margin.
        frameless_top = _physical_plot_hint(
            PlotBox(10, 8, 90, 95),
            x_axis,
            y_axis,
            (10.0, 90.0),
            (20.0, 50.0, 80.0),
        )
        self.assertEqual(frameless_top, PlotBox(10, 8, 90, 80))

    def test_grid_search_expands_to_outer_ticks_before_projection(self):
        x_axis = fit_numeric_axis([("0", 20), ("1", 80)], "X")
        y_axis = fit_numeric_axis([("100", 15), ("10", 50), ("1", 92)], "Y")
        expanded = _expanded_grid_search_box(
            PlotBox(22, 20, 78, 70), x_axis, y_axis, (100, 100)
        )
        self.assertEqual(expanded, PlotBox(4, 0, 96, 99))

    def test_unsnapped_linear_labels_anchor_to_verified_plot_frame(self):
        axis = fit_numeric_axis(
            [("0.3", 28), ("0.4", 102), ("0.5", 176), ("1.0", 534)],
            "offset labels",
        )
        anchored = _anchor_linear_axis_to_plot_frame(axis, PlotBox(27, 20, 540, 388), "x")
        self.assertEqual(tuple(round(tick.pixel) for tick in anchored.ticks), (27, 100, 174, 540))
        self.assertGreater(anchored.residual_px, 1.0)
        self.assertIs(
            _anchor_linear_axis_to_plot_frame(axis, PlotBox(0, 20, 540, 388), "x"),
            axis,
        )

    def test_grid_snap_uses_value_anchored_major_not_nearer_minor(self):
        axis = fit_numeric_axis(
            [("1000", 0), ("100", 36), ("10", 60), ("1", 90)],
            "log majors",
        )
        snapped = _snap_axis_to_grid(
            axis, tuple(float(value) for value in range(0, 91, 10)), "log majors", True
        )
        self.assertEqual(tuple(tick.pixel for tick in snapped.ticks), (0, 30, 60, 90))
        with self.assertRaisesRegex(RuntimeError, "full-span grid residual exceeds"):
            _snap_axis_to_grid(axis, (0.0, 27.0, 35.0, 60.0, 90.0), "log majors", True)

    def test_dense_log_minor_does_not_replace_clearly_nearer_major(self):
        axis = fit_numeric_axis(
            [("100", 47), ("10", 112), ("1", 177), ("0.1", 242)],
            "dense log majors",
        )
        snapped = _snap_axis_to_grid(
            axis,
            (46.0, 49.0, 52.0, 111.0, 114.0, 117.0, 176.0, 179.0, 182.0, 241.0),
            "dense log majors",
            True,
        )
        self.assertEqual(tuple(tick.pixel for tick in snapped.ticks), (46, 111, 176, 241))

    def test_log_endpoint_can_use_unique_line_just_outside_tight_interior_tolerance(self):
        axis = fit_numeric_axis(
            [("100", 20), ("10", 60), ("1", 96)],
            "log endpoint",
        )
        snapped = _snap_axis_to_grid(axis, (20.0, 60.0, 102.0), "log endpoint", True)
        self.assertEqual(tuple(tick.pixel for tick in snapped.ticks), (20.0, 60.0, 102.0))


class DiodeForwardCalibrationCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.environ.get("DSDIG_DATASHEET_ROOT")
        cls.datasheets = Path(root) / "datasheets" if root else None

    def test_three_panels_have_exact_titles_ticks_and_tick_derived_plots(self):
        if self.datasheets is None:
            self.skipTest("DSDIG_DATASHEET_ROOT is not set")
        cases = (
            (
                "infineon/IPP024N08NF2S.pdf",
                "Typ. forward characteristics of reverse diode",
                PlotBox(102, 62, 642, 665),
                "linear",
                "log10",
                (1, 10, 100, 1000),
                (102, 178, 256, 332, 410, 488, 564, 642),
                (62, 262, 464, 665),
            ),
            (
                "onsemi/FDA032N08.pdf",
                "Body Diode Forward Voltage",
                PlotBox(189, 88, 670, 424),
                "linear",
                "log10",
                (1, 10, 100, 400),
                (189, 349, 509, 670),
                (88, 166, 296, 424),
            ),
            (
                "diodes/DMTH83M2SPSWQ-13.pdf",
                "Diode Forward Voltage vs. Current",
                PlotBox(196, 36, 664, 462),
                "linear",
                "linear",
                (0, 5, 10, 15, 20, 25, 30),
                (196, 274, 352, 430, 508, 586, 664),
                (36, 107, 178, 249, 320, 391, 462),
            ),
        )
        for relative, title, plot, x_model, y_model, y_values, x_pixels, y_pixels in cases:
            with self.subTest(pdf=relative), tempfile.TemporaryDirectory() as tmp:
                pdf = self.datasheets / relative
                if not pdf.exists():
                    self.skipTest(f"missing local corpus PDF: {pdf}")
                out = Path(tmp)
                panel = next(panel for panel in process_pdf(pdf, out, 180) if panel.kind == "body_diode")
                calibration = calibrate_panel(panel, out / panel.crop_png)
                self.assertEqual(panel.title, title)
                self.assertEqual(calibration.plot, plot)
                self.assertEqual(calibration.x_axis.model, x_model)
                self.assertEqual(calibration.y_axis.model, y_model)
                self.assertEqual(
                    tuple(sorted(round(tick.value, 6) for tick in calibration.y_axis.ticks)),
                    y_values,
                )
                self.assertGreaterEqual(len(calibration.x_axis.ticks), 4)
                self.assertEqual(tuple(round(t.pixel) for t in calibration.x_axis.ticks), x_pixels)
                self.assertEqual(tuple(round(t.pixel) for t in calibration.y_axis.ticks), y_pixels)

                if relative.startswith("onsemi/"):
                    self.assertLess(calibration.x_axis.residual_px, 0.5)
                    self.assertLess(calibration.y_axis.residual_px, 0.5)
                    normalized = panel.text.lower().replace("−", "-")
                    self.assertIn("reverse", normalized)
                    self.assertIn("drain", normalized)
                    self.assertIn("current", normalized)
                    self.assertIn("source-drain voltage", normalized)

    def test_three_panels_digitize_full_curves_with_dense_fit_regions(self):
        if self.datasheets is None:
            self.skipTest("DSDIG_DATASHEET_ROOT is not set")
        cases = (
            ("infineon/IPP024N08NF2S.pdf", (25, 175), 500, True, True),
            ("onsemi/FDA032N08.pdf", (25, 175), 350, True, False),
            ("diodes/DMTH83M2SPSWQ-13.pdf", (-55, 25, 85, 125, 150, 175), 29.5, False, False),
        )
        for relative, temperatures, max_current, logarithmic, crossing in cases:
            with self.subTest(pdf=relative), tempfile.TemporaryDirectory() as tmp:
                pdf = self.datasheets / relative
                if not pdf.exists():
                    self.skipTest(f"missing local corpus PDF: {pdf}")
                out = Path(tmp)
                results = digitize_pdf(pdf, out)
                self.assertEqual(len(results), 1)
                result = results[0]
                self.assertEqual(result["status"], "ok")
                self.assertNotIn("fit", result)
                self.assertEqual(result["point_columns"], ["vsd_v", "current_a"])
                self.assertEqual(result["crossing_detected_high_current"], crossing)
                self.assertEqual(result["crossover_current_a"] is not None, crossing)
                self.assertTrue(any("crossover" in item for item in result["diagnostics"]))
                if crossing:
                    self.assertGreater(result["crossover_current_a"], 400)
                    self.assertLess(result["crossover_current_a"], 500)
                self.assertTrue((out / result["overlay"]).exists())
                self.assertTrue((out / "diode_forward_voltage.json").exists())
                curves = result["curves"]
                self.assertEqual(tuple(curve["temperature_c"] for curve in curves), temperatures)
                for curve in curves:
                    currents = [point[1] for point in curve["points"]]
                    self.assertLessEqual(min(currents), 1.1 if logarithmic else 0.1)
                    self.assertGreaterEqual(max(currents), max_current)
                    if logarithmic:
                        self.assertGreaterEqual(sum(1 <= value < 10 for value in currents), 40)
                        self.assertGreaterEqual(sum(10 <= value < 100 for value in currents), 40)
                        self.assertGreaterEqual(sum(100 <= value for value in currents), 40)
                    else:
                        self.assertGreaterEqual(sum(0 <= value < 5 for value in currents), 50)

    def test_onsemi_outer_decades_and_plot_bottom_bind_to_physical_lines(self):
        if self.datasheets is None:
            self.skipTest("DSDIG_DATASHEET_ROOT is not set")
        cases = (
            (
                "onsemi/NTMFS0D6N03CT1G.pdf",
                PlotBox(28, 20, 540, 380),
                (28, 112, 198, 284, 369, 454, 540),
                (20, 140, 260, 380),
            ),
            (
                "onsemi/NTMFS5C406NLT1G.pdf",
                PlotBox(27, 26, 540, 388),
                (27, 100, 174, 247, 320, 393, 467, 540),
                (27, 208, 388),
            ),
            (
                "onsemi/NTMFS5C410NT1G.pdf",
                PlotBox(27, 27, 540, 386),
                (27, 100, 173, 246, 320, 393, 466, 540),
                (27, 206, 386),
            ),
            (
                "onsemi/NTMFS5C410NT3G.pdf",
                PlotBox(27, 27, 540, 386),
                (27, 100, 173, 246, 320, 393, 466, 540),
                (27, 206, 386),
            ),
            (
                "onsemi/FDP3651U.pdf",
                PlotBox(23, 21, 449, 333),
                (23, 94, 165, 236, 307, 378, 449),
                (21, 74, 125, 177, 229, 281, 333),
            ),
            (
                "onsemi/FDP8D5N10C.pdf",
                PlotBox(26, 26, 479, 371),
                (26, 101, 176, 252, 328, 403, 479),
                (26, 46, 111, 176, 241, 306, 371),
            ),
            (
                "onsemi/FDPF51N25.pdf",
                PlotBox(26, 26, 508, 362),
                (26, 86, 146, 207, 267, 327, 387, 448, 508),
                (69, 216, 362),
            ),
            (
                "onsemi/FDPF8D5N10C.pdf",
                PlotBox(26, 26, 479, 371),
                (26, 101, 176, 252, 328, 403, 479),
                (26, 46, 111, 176, 241, 306, 371),
            ),
            (
                "onsemi/FDS4435BZ.pdf",
                PlotBox(23, 19, 437, 322),
                (23, 92, 161, 230, 299, 368, 437),
                (19, 69, 120, 170, 221, 272, 322),
            ),
            (
                "onsemi/FDS8447.pdf",
                PlotBox(24, 19, 449, 330),
                (24, 95, 166, 236, 307, 378, 449),
                (19, 81, 143, 205, 268, 330),
            ),
            (
                "onsemi/NVMFS6H852NT1G.pdf",
                PlotBox(26, 26, 540, 384),
                (26, 100, 173, 246, 320, 393, 466, 540),
                (26, 205, 384),
            ),
        )
        for relative, plot, x_pixels, y_pixels in cases:
            with self.subTest(pdf=relative), tempfile.TemporaryDirectory() as tmp:
                pdf = self.datasheets / relative
                if not pdf.exists():
                    self.skipTest(f"missing local corpus PDF: {pdf}")
                out = Path(tmp)
                panel = next(panel for panel in process_pdf(pdf, out, 180) if panel.kind == "body_diode")
                calibration = calibrate_panel(panel, out / panel.crop_png)
                self.assertEqual(calibration.plot, plot)
                self.assertEqual(tuple(round(tick.pixel) for tick in calibration.x_axis.ticks), x_pixels)
                self.assertEqual(tuple(round(tick.pixel) for tick in calibration.y_axis.ticks), y_pixels)
                self.assertLessEqual(calibration.plot.y0, min(y_pixels))
                self.assertGreaterEqual(calibration.plot.y1, max(y_pixels))

    def test_split_strokes_and_partial_outer_interval_preserve_full_source_traces(self):
        if self.datasheets is None:
            self.skipTest("DSDIG_DATASHEET_ROOT is not set")
        cases = (
            ("onsemi/FDP3651U.pdf", 3, 250, 70),
            ("onsemi/FDPF51N25.pdf", 2, 300, 35),
        )
        for relative, curve_count, minimum_points, maximum_top_y in cases:
            with self.subTest(pdf=relative), tempfile.TemporaryDirectory() as tmp:
                pdf = self.datasheets / relative
                if not pdf.exists():
                    self.skipTest(f"missing local corpus PDF: {pdf}")
                result = digitize_pdf(pdf, Path(tmp))[0]
                self.assertEqual(len(result["curves"]), curve_count)
                for curve in result["curves"]:
                    self.assertGreaterEqual(len(curve["points_px"]), minimum_points)
                    self.assertLessEqual(min(point[1] for point in curve["points_px"]), maximum_top_y)

    def test_failed_extraction_does_not_write_an_ok_manifest(self):
        if self.datasheets is None:
            self.skipTest("DSDIG_DATASHEET_ROOT is not set")
        pdf = self.datasheets / "infineon/IPP024N08NF2S.pdf"
        if not pdf.exists():
            self.skipTest(f"missing local corpus PDF: {pdf}")
        with tempfile.TemporaryDirectory() as tmp, patch(
            "datasheet_chart_digitizer.diode_forward_voltage._assign_temperatures",
            side_effect=RuntimeError("synthetic refusal"),
        ):
            out = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "synthetic refusal"):
                digitize_pdf(pdf, out)
            self.assertFalse((out / "diode_forward_voltage.json").exists())


if __name__ == "__main__":
    unittest.main()
