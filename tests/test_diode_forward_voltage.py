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
    _draw_overlay,
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
        ), patch("datasheet_chart_digitizer.diode_forward_voltage.cv2.drawMarker") as marker:
            _draw_overlay(Path("crop.png"), calibration, [], panel)

        centers = [call.args[1] for call in marker.call_args_list]
        self.assertEqual(centers, [(10, 90), (90, 90), (10, 10), (10, 90)])

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

    def test_grid_snap_uses_value_anchored_major_not_nearer_minor(self):
        axis = fit_numeric_axis(
            [("1000", 0), ("100", 36), ("10", 60), ("1", 90)],
            "log majors",
        )
        snapped = _snap_axis_to_grid(
            axis, tuple(float(value) for value in range(0, 91, 10)), "log majors", True
        )
        self.assertEqual(tuple(tick.pixel for tick in snapped.ticks), (0, 30, 60, 90))
        with self.assertRaisesRegex(RuntimeError, "ambiguous full-span"):
            _snap_axis_to_grid(axis, (0.0, 27.0, 35.0, 60.0, 90.0), "log majors", True)


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
