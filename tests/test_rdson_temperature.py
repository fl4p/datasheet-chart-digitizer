from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.find_charts import ChartPanel, PageText, Word
from datasheet_chart_digitizer.numeric_axis import AxisTick, NumericAxis
from datasheet_chart_digitizer.rdson_temperature import (
    DIAG_AXIS_IDENTITY,
    DIAG_AXIS_RESIDUAL,
    DIAG_CURVE_BINDING,
    DIAG_MONOTONIC,
    DIAG_NORMALIZED_SPAN,
    DIAG_REFERENCE_BRACKET,
    DIAG_REFERENCE_UNITY,
    DIAG_TEMPERATURE_SPAN,
    DIAG_VGS_ORDER,
    CurveBindingError,
    PanelCalibration,
    VectorTrace,
    _RDS_TITLE_RE,
    _bind_and_calibrate_curves,
    _draw_overlay,
    _rdson_temperature_titles,
    _validation_reasons,
    digitize_pdf,
)


def _axis(
    values: list[float], pixels: list[float], residual: float = 0.0
) -> NumericAxis:
    m, b = np.polyfit(pixels, values, 1)
    return NumericAxis(
        "linear",
        float(m),
        float(b),
        tuple(
            AxisTick(f"{value:g}", value, pixel)
            for value, pixel in zip(values, pixels)
        ),
        residual,
        (("linear", residual),),
    )


def _panel(text: str | None = None) -> ChartPanel:
    return ChartPanel(
        "sample.pdf",
        "sample",
        1,
        8,
        "Normalized On-State Resistance vs Temperature",
        "rds_on",
        (0, 0, 100, 100),
        (0, 0, 100, 100),
        "crop.png",
        text
        or "Normalized On-State Resistance vs Temperature T C Case Temperature (° C)",
        "",
        [],
    )


def _calibration(
    x_residual: float = 0.0, y_residual: float = 0.0
) -> PanelCalibration:
    plot = PlotBox(10, 10, 90, 90)
    return PanelCalibration(
        plot,
        _axis([-75, 25, 175], [10, 42, 90], x_residual),
        _axis([1.8, 1.0, 0.4], [10, 50, 90], y_residual),
        plot,
    )


def _curve(
    values: list[tuple[float, float]] | None = None,
    gate_voltage_v: float = 4.5,
) -> dict[str, object]:
    points = values or [(-75, 0.7), (25, 1.0), (175, 1.6)]
    return {
        "gate_voltage_v": gate_voltage_v,
        "style_rgb": [1.0, 0.0, 0.0],
        "trace_source": "pdf_vector",
        "points_px": [[10, 80], [42, 50], [90, 20]],
        "points": [[x, y] for x, y in points],
    }


class RdsonTemperatureUnitTests(unittest.TestCase):
    def test_title_match_accepts_common_normalized_rdson_temperature_phrasings(self) -> None:
        accepted = [
            "Normalized On-State Resistance vs Temperature",
            "Normalized Drain to Source On-State Resistance vs Junction Temperature",
            (
                "Normalized drain-source on-state resistance factor "
                "as a function of junction temperature"
            ),
        ]
        for title in accepted:
            with self.subTest(title=title):
                self.assertIsNotNone(_RDS_TITLE_RE.search(title))
        self.assertIsNone(
            _RDS_TITLE_RE.search("Drain-source on-state resistance vs current")
        )

    def test_merged_captions_keep_only_normalized_rdson_temperature(self):
        texts = (
            "Figure 7. Normalized On-State Resistance vs Drain Current "
            "Figure 8. Normalized On-State Resistance vs Temperature "
            "Figure 9. Gate Threshold Voltage vs Temperature"
        )
        words = []
        x = 10.0
        for text in texts.split():
            words.append(Word(text, x, 100, x + 8, 108))
            x += 9
        page = PageText(1, 700, 900, words)
        titles = _rdson_temperature_titles(page)
        self.assertEqual([(title.number, title.title) for title in titles], [
            (8, "Normalized On-State Resistance vs Temperature")
        ])

    def test_each_named_validation_guard_fires_on_its_known_bad(self):
        calibration = _calibration()
        cases = [
            (
                _panel("Normalized On-State Resistance vs Current (A)"),
                calibration,
                [_curve()],
                DIAG_AXIS_IDENTITY,
            ),
            (_panel(), _calibration(x_residual=2.0), [_curve()], DIAG_AXIS_RESIDUAL),
            (
                _panel(),
                calibration,
                [_curve([(-10, 0.8), (25, 1.0), (60, 1.3)])],
                DIAG_TEMPERATURE_SPAN,
            ),
            (
                _panel(),
                calibration,
                [_curve([(-75, 1.0), (25, 1.0), (175, 1.0)])],
                DIAG_NORMALIZED_SPAN,
            ),
            (
                _panel(),
                calibration,
                [_curve([(-162.5, 0.7), (-50, 0.8), (25, 1.0)])],
                DIAG_REFERENCE_BRACKET,
            ),
            (
                _panel(),
                calibration,
                [_curve([(-75, 0.8), (25, 1.2), (175, 1.6)])],
                DIAG_REFERENCE_UNITY,
            ),
            (
                _panel(),
                calibration,
                [_curve([(-75, 0.7), (25, 1.0), (175, 0.95)])],
                DIAG_MONOTONIC,
            ),
        ]
        for panel, fitted, curves, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(
                    _validation_reasons(panel, fitted, curves), [diagnostic]
                )

    def test_flat_trace_cannot_be_laundered_by_monotonicity(self):
        reasons = _validation_reasons(
            _panel(),
            _calibration(),
            [_curve([(-75, 1.0), (25, 1.0), (175, 1.0)])],
        )
        self.assertIn(DIAG_NORMALIZED_SPAN, reasons)
        self.assertNotIn(DIAG_MONOTONIC, reasons)

    def test_physical_vgs_order_is_validation_not_assignment(self):
        curves = [_curve(gate_voltage_v=2.5), _curve(gate_voltage_v=4.5)]
        self.assertEqual(
            _validation_reasons(_panel(), _calibration(), curves),
            [DIAG_VGS_ORDER],
        )

    def test_reference_temperature_must_be_strictly_bracketed(self):
        curve = _curve([(-162.5, 0.7), (-50, 0.8), (25, 1.0)])
        reasons = _validation_reasons(
            _panel(),
            _calibration(),
            [curve],
        )
        self.assertEqual(reasons, [DIAG_REFERENCE_BRACKET])
        self.assertNotIn("normalized_rds_on_at_25c", curve)

    def test_ambiguous_legend_trace_binding_fails_closed(self):
        trace = VectorTrace((0.0, 0.0, 0.0), ((10, 80), (90, 20)))
        with self.assertRaisesRegex(CurveBindingError, "mismatch"):
            _bind_and_calibrate_curves([trace], [], _calibration())
        self.assertEqual(DIAG_CURVE_BINDING, "legend_curve_binding_ambiguous")

    def test_empty_curve_set_cannot_validate_ok(self):
        self.assertEqual(
            _validation_reasons(_panel(), _calibration(), []),
            [DIAG_CURVE_BINDING],
        )

    def test_overlay_crosshairs_are_centered_at_calibrated_ticks(self):
        with patch(
            "datasheet_chart_digitizer.rdson_temperature.cv2.imread",
            return_value=np.full((100, 100, 3), 255, dtype=np.uint8),
        ), patch(
            # tick crosshairs now render via the shared overlay helper
            "datasheet_chart_digitizer.overlay.cv2.drawMarker"
        ) as marker:
            _draw_overlay(Path("crop.png"), _panel(), _calibration(), [], "ok")
        centers = [call.args[1] for call in marker.call_args_list]
        self.assertEqual(
            centers,
            [(10, 90), (42, 90), (90, 90), (10, 10), (10, 50), (10, 90)],
        )


class RdsonTemperatureRealCorpusTests(unittest.TestCase):
    def test_onsemi_filled_vector_curve_and_unicode_negative_ticks_are_numeric(self):
        pdf = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets",
            )
        ) / "onsemi/FDP3682.pdf"
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory() as tmp:
            results = digitize_pdf(pdf, Path(tmp))
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["status"], "ok")
        self.assertEqual((result["panel"]["page"], result["panel"]["diagram"]), (4, 10))
        self.assertEqual([curve["gate_voltage_v"] for curve in result["curves"]], [10.0])
        self.assertEqual(
            [tick["value"] for tick in result["x_axis"]["ticks"]],
            [-80.0, -40.0, 0.0, 40.0, 80.0, 120.0, 160.0, 200.0],
        )
        self.assertAlmostEqual(result["curves"][0]["normalized_rds_on_at_25c"], 1.0, delta=0.06)

    def test_three_ti_layouts_are_numeric_local_and_legend_bound(self):
        corpus = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets",
            )
        )
        samples = {
            "CSD19534KCS": {
                "page": 5,
                "diagram": 8,
                "bbox": (84.8, 507.59, 289.6, 661.6),
                "plot": PlotBox(25, 40, 497, 371),
                "vgs": [6.0, 10.0],
                "x": (-75.0, 200.0),
                "y": (0.4, 2.5),
            },
            "CSD13302W": {
                "page": 6,
                "diagram": 8,
                "bbox": (85.2, 97.26, 290.0, 252.2),
                "plot": PlotBox(25, 40, 497, 372),
                "vgs": [2.5, 4.5],
                "x": (-75.0, 175.0),
                "y": (0.7, 1.4),
            },
            "CSD87313DMS": {
                "page": 4,
                "diagram": 5,
                "bbox": (84.8, 490.39, 289.6, 644.4),
                "plot": PlotBox(25, 40, 497, 371),
                "vgs": [2.5, 4.5],
                "x": (-75.0, 175.0),
                "y": (0.4, 1.8),
            },
        }
        for part, expected in samples.items():
            pdf = corpus / "ti" / f"{part}.pdf"
            if not pdf.exists():
                self.skipTest(f"missing local corpus fixture: {pdf}")
            with self.subTest(part=part), tempfile.TemporaryDirectory() as tmp:
                results = digitize_pdf(pdf, Path(tmp))
                self.assertEqual(len(results), 1)
                result = results[0]
                panel = result["panel"]
                self.assertEqual(result["status"], "ok")
                self.assertEqual((panel["page"], panel["diagram"]), (
                    expected["page"], expected["diagram"]
                ))
                self.assertEqual(
                    panel["title"],
                    "Normalized On-State Resistance vs Temperature",
                )
                for actual, target in zip(panel["bbox_pt"], expected["bbox"]):
                    self.assertAlmostEqual(actual, target, delta=0.05)
                self.assertEqual(PlotBox(**result["plot_box_px"]), expected["plot"])
                self.assertEqual(result["x_axis"]["model"], "linear")
                self.assertEqual(result["y_axis"]["model"], "linear")
                self.assertLess(result["x_axis"]["residual_px"], 0.6)
                self.assertLess(result["y_axis"]["residual_px"], 0.4)
                x_values = [tick["value"] for tick in result["x_axis"]["ticks"]]
                y_values = [tick["value"] for tick in result["y_axis"]["ticks"]]
                self.assertEqual((min(x_values), max(x_values)), expected["x"])
                self.assertEqual((min(y_values), max(y_values)), expected["y"])
                self.assertLess(min(x_values), 0)
                self.assertIn("temperature", panel["text"].lower())
                self.assertIn("c)", panel["text"].lower())
                curves = result["curves"]
                self.assertEqual(
                    [curve["gate_voltage_v"] for curve in curves], expected["vgs"]
                )
                self.assertEqual(
                    [curve["style_rgb"] for curve in curves],
                    [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
                )
                for curve in curves:
                    points = np.asarray(curve["points"], dtype=float)
                    self.assertGreater(
                        np.ptp(points[:, 0]) / np.ptp(x_values), 0.8
                    )
                    self.assertAlmostEqual(
                        curve["normalized_rds_on_at_25c"], 1.0, delta=0.01
                    )
                lo = max(np.min(np.asarray(curve["points"])[:, 0]) for curve in curves)
                hi = min(np.max(np.asarray(curve["points"])[:, 0]) for curve in curves)
                probe = lo + 0.10 * (hi - lo)
                low_values = []
                for curve in curves:
                    points = np.asarray(curve["points"], dtype=float)
                    order = np.argsort(points[:, 0])
                    low_values.append(
                        np.interp(probe, points[order, 0], points[order, 1])
                    )
                self.assertGreater(low_values[0], low_values[1])
                overlay = Path(tmp) / result["overlay"]
                self.assertTrue(overlay.exists())
                self.assertEqual(overlay.suffix, ".webp")

    def test_calibrated_but_ambiguous_curve_binding_emits_refused_artifact(self):
        corpus = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets",
            )
        )
        pdf = corpus / "ti" / "CSD19534KCS.pdf"
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory() as tmp, patch(
            "datasheet_chart_digitizer.rdson_temperature._extract_vector_traces",
            side_effect=CurveBindingError("synthetic ambiguous binding"),
        ):
            result = digitize_pdf(pdf, Path(tmp))[0]
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["diagnostics"], [DIAG_CURVE_BINDING])
            self.assertEqual(result["curves"], [])
            self.assertEqual(
                result["binding_error"], "synthetic ambiguous binding"
            )
            self.assertTrue((Path(tmp) / result["overlay"]).exists())


if __name__ == "__main__":
    unittest.main()
