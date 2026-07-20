from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.find_charts import ChartPanel, DiagramTitle, PageText, Word
from datasheet_chart_digitizer.numeric_axis import AxisTick, NumericAxis
from datasheet_chart_digitizer.rdson_temperature import (
    DIAG_ABSOLUTE_LIMIT_LABELS,
    DIAG_AXIS_IDENTITY,
    DIAG_AXIS_RESIDUAL,
    DIAG_CURVE_BINDING,
    DIAG_MONOTONIC,
    DIAG_NO_FULL_SPAN_CURVE,
    DIAG_NORMALIZED_SPAN,
    DIAG_REFERENCE_BRACKET,
    DIAG_REFERENCE_UNITY,
    DIAG_TEMPERATURE_SPAN,
    CurveBindingError,
    PanelCalibration,
    VectorTrace,
    _RDS_TITLE_RE,
    _bind_absolute_typ_max_curves,
    _rdson_temperature_panel_owned,
    _bind_and_calibrate_curves,
    _draw_overlay,
    _directional_grid_candidates,
    _rdson_temperature_titles,
    _validation_reasons,
    digitize_pdf,
    digitize_pdf_fail_closed,
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
    def test_unresolved_caption_adopts_one_immediately_following_grid(self):
        title = DiagramTitle(
            8,
            "On-Resistance Variation vs. Temperature",
            (300, 100, 500, 120),
            "Figure 8. On-Resistance Variation vs. Temperature",
        )
        below = (340, 130, 510, 280)

        self.assertEqual(
            _directional_grid_candidates([below], title, None),
            ([below], "below"),
        )

    def test_unresolved_caption_refuses_grids_on_both_sides(self):
        title = DiagramTitle(
            8,
            "On-Resistance Variation vs. Temperature",
            (300, 100, 500, 120),
            "Figure 8. On-Resistance Variation vs. Temperature",
        )
        above = (340, -60, 510, 90)
        below = (340, 130, 510, 280)

        self.assertEqual(
            _directional_grid_candidates([above, below], title, None),
            ([], None),
        )

    def test_unresolved_caption_refuses_multiple_following_grids(self):
        title = DiagramTitle(
            8,
            "On-Resistance Variation vs. Temperature",
            (300, 100, 500, 120),
            "Figure 8. On-Resistance Variation vs. Temperature",
        )
        below = [(340, 130, 510, 280), (340, 300, 510, 450)]

        self.assertEqual(
            _directional_grid_candidates(below, title, None),
            ([], None),
        )

    def test_positive_direction_wins_over_opposite_side_competitor(self):
        title = DiagramTitle(
            8,
            "On-Resistance Variation vs. Temperature",
            (300, 100, 500, 120),
            "Figure 8. On-Resistance Variation vs. Temperature",
        )
        above = (340, -60, 510, 90)
        below = (340, 130, 510, 280)

        self.assertEqual(
            _directional_grid_candidates([above, below], title, "below"),
            ([below], "below"),
        )
        self.assertEqual(
            _directional_grid_candidates([above, below], title, "above"),
            ([above], "above"),
        )

    def test_title_match_accepts_common_normalized_rdson_temperature_phrasings(self) -> None:
        accepted = [
            "On Resistance vs Temperature",
            "Normalized On-Resistance vs. Temperature",
            "On−Resistance Variation with Temperature",
            "On−Resistance Variation vs. Temperature",
            "On-State Resistance (Normalized) vs Junction Temperature",
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

    def test_normalized_stem_routes_only_with_owned_bracket_tj_and_formula(self) -> None:
        panel = _panel("T j [°C] R DS(on) =f(T j ), I D =50 A, V GS =10 V")
        panel.title = "Normalized drain-source on resistance"
        self.assertTrue(_rdson_temperature_panel_owned(panel))

        panel.text = "I D [A] R DS(on) =f(I D )"
        self.assertFalse(_rdson_temperature_panel_owned(panel))
        panel.text = "V GS [V] R DS(on) =f(V GS )"
        self.assertFalse(_rdson_temperature_panel_owned(panel))

    def test_diagram_title_without_temperature_clause_is_retained_for_local_proof(self) -> None:
        words = []
        x = 45.0
        for text in "Diagram 9: Normalized drain-source on resistance".split():
            words.append(Word(text, x, 100, x + 8, 108))
            x += 9

        titles = _rdson_temperature_titles(PageText(1, 612, 792, words))

        self.assertEqual(
            [(title.number, title.title) for title in titles],
            [(9, "Normalized drain-source on resistance")],
        )

    def test_compact_temperature_formula_title_is_retained_and_owned(self) -> None:
        words = []
        x = 45.0
        for text in "Fig. 8.9 R DS(ON) - T a".split():
            words.append(Word(text, x, 100, x + 8, 108))
            x += 9
        titles = _rdson_temperature_titles(PageText(1, 612, 792, words))
        self.assertEqual([(title.number, title.title) for title in titles], [(89, "R DS(ON) - T a")])
        panel = _panel("")
        panel.title = titles[0].title
        self.assertTrue(_rdson_temperature_panel_owned(panel))

    def test_packed_infineon_diagram_words_are_split_by_column(self) -> None:
        words = [
            Word(
                "Diagram 9: Normalized drain-source on resistance",
                45,
                100,
                260,
                108,
            ),
            Word("Diagram 10: Typ. gate threshold voltage", 315, 100, 486, 108),
        ]

        titles = _rdson_temperature_titles(PageText(1, 612, 792, words))

        self.assertEqual(
            [(title.number, title.title) for title in titles],
            [(9, "Normalized drain-source on resistance")],
        )

    def test_short_ti_title_requires_and_accepts_normalized_hyphenated_axis(self) -> None:
        panel = _panel(
            "Normalized On-State Resistance TC - Case Temperature - °C"
        )
        panel.title = "On Resistance vs Temperature"

        self.assertNotIn(
            DIAG_AXIS_IDENTITY,
            _validation_reasons(panel, _calibration(), [_curve()]),
        )

    def test_onsemi_ocr_degree_five_still_requires_temperature_semantics(self) -> None:
        panel = _panel(
            "On−Resistance Variation with Temperature "
            "TJ, Junction Temperature (5C)"
        )
        panel.title = "On−Resistance Variation with Temperature"

        self.assertNotIn(
            DIAG_AXIS_IDENTITY,
            _validation_reasons(panel, _calibration(), [_curve()]),
        )

    def test_spaced_celsius_unit_preserves_temperature_axis_identity(self) -> None:
        panel = _panel(
            "Normalized On-Resistance Vs. Temperature "
            "T J , Junction Temperature ( ° C)"
        )

        self.assertNotIn(
            DIAG_AXIS_IDENTITY,
            _validation_reasons(panel, _calibration(), [_curve()]),
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

    def test_hyphenated_figure_number_and_wrapped_temperature_are_retained(self):
        words = []
        x = 70.0
        for text in "Figure 5-8. Normalized On-State Resistance vs".split():
            words.append(Word(text, x, 100, x + 8, 108))
            x += 9
        words.append(Word("Temperature", 92, 112, 132, 120))

        titles = _rdson_temperature_titles(PageText(1, 612, 792, words))

        self.assertEqual(
            [(title.number, title.title) for title in titles],
            [(58, "Normalized On-State Resistance vs Temperature")],
        )

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

    def test_source_bound_curves_may_cross_without_identity_rejection(self):
        curves = [_curve(gate_voltage_v=2.5), _curve(gate_voltage_v=4.5)]
        self.assertEqual(
            _validation_reasons(_panel(), _calibration(), curves),
            [],
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
            [DIAG_NO_FULL_SPAN_CURVE],
        )

    def test_curve_binding_error_carries_structured_reason_not_message_guess(self):
        error = CurveBindingError(
            DIAG_NO_FULL_SPAN_CURVE,
            "legend words in free text do not change this trace reason",
        )
        self.assertEqual(error.diagnostic, DIAG_NO_FULL_SPAN_CURVE)
        self.assertIn("legend words", str(error))

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
    def test_infineon_absolute_mohm_typ_max_curves_are_served(self):
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon/IPT007N06N.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory(prefix="rdson-ipt007-absolute-") as tmp:
            results, errors = digitize_pdf_fail_closed(pdf, Path(tmp), dpi=220)
            result = next(row for row in results if row["panel"]["diagram"] == 9)
            self.assertTrue((Path(tmp) / result["overlay"]).exists())

        self.assertEqual(errors, [])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["axis_kind"], "absolute_rds_on")
        self.assertEqual(result["point_columns"], ["temperature_c", "rdson_mohm"])
        self.assertEqual(result["y_unit"], "mohm")
        self.assertEqual(
            result["diagnostics"],
            [
                "absolute_rds_axis_unit_mohm",
                "typ_max_identity_bound_by_source_order",
                "vgs_and_id_conditions_bound_by_local_text",
            ],
        )
        self.assertEqual(
            [tick["value"] for tick in result["y_axis"]["ticks"]],
            [1.6, 1.2, 0.8, 0.4, 0.0],
        )
        self.assertEqual([curve["limit"] for curve in result["curves"]], ["typ", "max"])
        self.assertEqual([len(curve["points"]) for curve in result["curves"]], [634, 634])
        for curve in result["curves"]:
            self.assertEqual(curve["gate_voltage_v"], 10.0)
            self.assertEqual(curve["drain_current_a"], 150.0)
            values = np.asarray(curve["points"], dtype=float)
            self.assertTrue(np.all(np.diff(values[:, 1]) >= -1e-9))
        typical, maximum = result["curves"]
        self.assertAlmostEqual(typical["rdson_mohm_at_25c"], 0.658696, places=5)
        self.assertAlmostEqual(maximum["rdson_mohm_at_25c"], 0.747826, places=5)
        self.assertGreater(
            maximum["rdson_mohm_at_25c"], typical["rdson_mohm_at_25c"]
        )
    def test_absolute_same_style_curves_without_typ_max_labels_refuse(self):
        panel = _panel(
            "Drain-source on-state resistance R DS(on) [mOhm] "
            "T j [C] R DS(on)=f(T j); I D=150 A; V GS=10 V"
        )
        traces = [
            VectorTrace((0.0, 0.0, 0.0), tuple((x, 55) for x in range(10, 91))),
            VectorTrace((0.0, 0.0, 0.0), tuple((x, 35) for x in range(10, 91))),
        ]
        with self.assertRaises(CurveBindingError) as caught:
            _bind_absolute_typ_max_curves(panel, traces, _calibration())
        self.assertEqual(caught.exception.diagnostic, DIAG_ABSOLUTE_LIMIT_LABELS)

    def test_irf3205_spaced_celsius_axis_is_served(self):
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon/IRF3205.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory(prefix="rdson-irf3205-axis-") as tmp:
            results, errors = digitize_pdf_fail_closed(pdf, Path(tmp), dpi=220)

        self.assertEqual(errors, [])
        result = next(row for row in results if row["panel"]["diagram"] == 4)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["diagnostics"], [
            "vgs_identity_bound_by_local_label_and_trace_geometry",
            "normalized_rds_validated_at_25C",
            "no_absolute_rds_or_temperature_interpolation",
        ])
        self.assertEqual(result["x_axis"]["model"], "linear")
        self.assertEqual(
            [tick["value"] for tick in result["x_axis"]["ticks"]],
            [-60.0, -40.0, -20.0, 0.0, 20.0, 40.0, 60.0,
             80.0, 100.0, 120.0, 140.0, 160.0, 180.0],
        )
        self.assertEqual(
            [tick["pixel"] for tick in result["x_axis"]["ticks"]],
            [33.0, 77.0, 120.0, 162.0, 206.0, 249.0, 292.0,
             336.0, 379.0, 421.0, 465.0, 508.0, 551.0],
        )
        self.assertLess(result["x_axis"]["residual_px"], 0.5)
        self.assertEqual(len(result["curves"]), 1)
        curve = result["curves"][0]
        self.assertEqual(curve["gate_voltage_v"], 10.0)
        self.assertAlmostEqual(curve["normalized_rds_on_at_25c"], 1.0, delta=0.01)

    def test_onsemi_thick_frame_quantization_preserves_coherent_temperature_rails(self):
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/onsemi/NTMFS6H864NLT1G.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory(prefix="rdson-ntmfs6h864-") as tmp:
            results, errors = digitize_pdf_fail_closed(pdf, Path(tmp), dpi=220)

        self.assertEqual(errors, [])
        result = next(row for row in results if row["panel"]["diagram"] == 5)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["plot_box_px"], {
            "x0": 31, "y0": 31, "x1": 662, "y1": 470,
        })
        self.assertEqual(
            [tick["value"] for tick in result["x_axis"]["ticks"]],
            [-50.0, -25.0, 0.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0, 175.0],
        )
        self.assertEqual(
            [tick["pixel"] for tick in result["x_axis"]["ticks"]],
            [32.0, 103.0, 172.0, 242.0, 312.0, 381.0, 450.0, 520.0, 590.0, 660.0],
        )
        self.assertAlmostEqual(result["x_axis"]["residual_px"], 0.457928, places=5)
        self.assertAlmostEqual(result["y_axis"]["residual_px"], 0.469044, places=5)
        self.assertEqual(len(result["curves"]), 1)
        curve = result["curves"][0]
        self.assertEqual(curve["gate_voltage_v"], 10.0)
        self.assertEqual(curve["trace_source"], "pdf_vector")
        self.assertEqual(len(curve["points"]), 628)
        values = np.asarray(curve["points"], dtype=float)
        self.assertTrue(np.all(np.diff(values[:, 1]) >= -1e-9))
        self.assertAlmostEqual(curve["normalized_rds_on_at_25c"], 0.997309, places=5)

    def test_fairchild_caption_leads_unique_temperature_grid(self):
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/onsemi/FDB047N10.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory(prefix="rdson-fdb047-") as tmp:
            results, errors = digitize_pdf_fail_closed(pdf, Path(tmp), dpi=220)

        self.assertEqual(errors, [])
        result = next(row for row in results if row["panel"]["diagram"] == 8)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["panel"]["page"], 4)
        self.assertEqual(len(result["curves"]), 1)
        curve = result["curves"][0]
        self.assertEqual(curve["gate_voltage_v"], 10.0)
        self.assertEqual(len(curve["points"]), 372)
        self.assertEqual(
            [tick["value"] for tick in result["x_axis"]["ticks"]],
            [-100.0, -50.0, 0.0, 50.0, 100.0, 150.0, 200.0],
        )
        values = np.asarray(curve["points"], dtype=float)
        self.assertAlmostEqual(
            float(np.interp(25.0, values[:, 0], values[:, 1])),
            1.0,
            delta=0.01,
        )
        self.assertTrue(np.all(np.diff(values[:, 1]) >= -1e-9))

    def test_st_thin_curve_is_recovered_but_axis_identity_still_refuses(self):
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st/STP45N60DM2AG.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory(prefix="rdson-st-thin-") as tmp:
            results, errors = digitize_pdf_fail_closed(pdf, Path(tmp), dpi=220)
        self.assertEqual(errors, [])
        result = next(row for row in results if row["panel"]["diagram"] == 9)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["diagnostics"], [DIAG_AXIS_IDENTITY])
        self.assertIsNone(result["binding_error"])
        self.assertEqual(len(result["curves"]), 1)
        curve = result["curves"][0]
        self.assertEqual(curve["gate_voltage_v"], 10.0)
        self.assertEqual(len(curve["points_px"]), 326)
        values = np.asarray(curve["points"], dtype=float)
        self.assertTrue(np.all(np.diff(values[:, 1]) >= -1e-9))
        at_25 = float(np.interp(25.0, values[:, 0], values[:, 1]))
        self.assertAlmostEqual(at_25, 1.0, delta=0.02)

    def test_infineon_formula_variable_routes_unnormalized_title(self) -> None:
        pdf = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets",
            )
        ) / "infineon/IPA60R125CFD7.pdf"
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory() as tmp:
            results = digitize_pdf(pdf, Path(tmp))
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual((result["panel"]["page"], result["panel"]["diagram"]), (8, 8))
        self.assertEqual(result["status"], "ok")

    def test_infineon_diagram_stem_uses_owned_tj_axis_and_formula(self) -> None:
        pdf = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets",
            )
        ) / "infineon/BSC059N04LS6ATMA1.pdf"
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")

        with tempfile.TemporaryDirectory() as tmp:
            results = digitize_pdf(pdf, Path(tmp))

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            (result["panel"]["page"], result["panel"]["diagram"]), (8, 9)
        )
        self.assertEqual(
            result["panel"]["title"], "Normalized drain-source on resistance"
        )
        self.assertEqual(
            [tick["value"] for tick in result["x_axis"]["ticks"]],
            [-80.0, -40.0, 0.0, 40.0, 80.0, 120.0, 160.0, 200.0],
        )
        self.assertEqual([curve["gate_voltage_v"] for curve in result["curves"]], [10.0])
        curve = result["curves"][0]
        self.assertGreaterEqual(len(curve["points"]), 400)
        self.assertAlmostEqual(curve["normalized_rds_on_at_25c"], 1.0, delta=0.01)

    def test_title_variants_and_split_vector_curve_are_served(self):
        corpus = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets",
            )
        )
        samples = {
            "infineon/IRF100B201.pdf": (6, 10.0, 100),
            "onsemi/NDB5060L.pdf": (3, 5.0, 3),
            "onsemi/NVB055N60S5F.pdf": (8, 10.0, 100),
        }
        for relative, (diagram, gate_voltage, minimum_points) in samples.items():
            pdf = corpus / relative
            if not pdf.exists():
                self.skipTest(f"missing local corpus fixture: {pdf}")
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                results = digitize_pdf(pdf, Path(tmp), dpi=220)

            result = next(row for row in results if row["panel"]["diagram"] == diagram)
            self.assertEqual(result["status"], "ok")
            self.assertIsNone(result["binding_error"])
            self.assertEqual(
                [curve["gate_voltage_v"] for curve in result["curves"]],
                [gate_voltage],
            )
            self.assertGreaterEqual(len(result["curves"][0]["points"]), minimum_points)

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
                "bbox": (84.8, 513.4, 289.6, 661.6),
                "plot": PlotBox(25, 25, 497, 356),
                "vgs": [6.0, 10.0],
                "x": (-75.0, 200.0),
                "y": (0.4, 2.5),
            },
            "CSD13302W": {
                "page": 6,
                "diagram": 8,
                "bbox": (85.2, 103.4, 290.0, 252.2),
                "plot": PlotBox(25, 25, 497, 358),
                "vgs": [2.5, 4.5],
                "x": (-75.0, 175.0),
                "y": (0.7, 1.4),
            },
            "CSD87313DMS": {
                "page": 4,
                "diagram": 5,
                "bbox": (84.8, 496.2, 289.6, 644.4),
                "plot": PlotBox(25, 25, 497, 356),
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
            side_effect=CurveBindingError(
                DIAG_CURVE_BINDING, "synthetic ambiguous binding"
            ),
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
