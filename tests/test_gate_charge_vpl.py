import unittest
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from datasheet_chart_digitizer import cli
from datasheet_chart_digitizer import gate_charge as gate
from datasheet_chart_digitizer import gate_charge_estimation as estimation
from datasheet_chart_digitizer import gate_charge_vpl as vpl
from datasheet_chart_digitizer import gate_charge_trace as trace
from datasheet_chart_digitizer import find_charts


def _sample_panel() -> find_charts.ChartPanel:
    return find_charts.ChartPanel(
        pdf="sample.pdf",
        part="sample",
        page=3,
        diagram=7,
        title="Gate charge",
        kind="gate_charge",
        bbox_pt=(10.0, 20.0, 110.0, 120.0),
        crop_box_pt=(8.0, 18.0, 112.0, 122.0),
        crop_png="crops/sample.png",
        text="Gate charge",
        formula="",
        mentions=[],
    )


class GateChargeVplTests(unittest.TestCase):
    def test_samples_from_chart_extraction_strips_datasheet_prefix(self) -> None:
        text = '''
        "datasheets/infineon/Foo.pdf": {"ref": 3.25, "comment": "first"},
        "datasheets/ao/Bar.pdf": {"comment": "no ref"}
        '''
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "chart-extraction.md"
            path.write_text(text)

            samples = vpl._samples_from_chart_extraction(path, start=1, count=2)

        self.assertEqual(samples[0], ("infineon/Foo.pdf", 3.25, "first"))
        self.assertEqual(samples[1], ("ao/Bar.pdf", None, "no ref"))

    def test_sample_pdf_path_resolves_relative_to_datasheets(self) -> None:
        root = Path("/tmp/pwr-mosfet-lib")

        self.assertEqual(
            vpl._sample_pdf_path(root, "infineon/Foo.pdf"),
            root / "datasheets" / "infineon/Foo.pdf",
        )
        self.assertEqual(
            vpl._sample_pdf_path(root, "/tmp/Foo.pdf"),
            Path("/tmp/Foo.pdf"),
        )

    def test_context_filter_accepts_gate_charge_and_rejects_diode(self) -> None:
        self.assertFalse(estimation._reject_non_gate_context("Figure 8 Gate Charge Characteristics"))
        self.assertTrue(estimation._reject_non_gate_context("Figure 6 Source-Drain Diode Forward"))

    def test_refined_plot_context_rejects_non_gate_panels(self) -> None:
        cases = {
            "Normalized D-S Breakdown Voltage (a) vs Tj": "breakdown_voltage",
            "Drain-source on-state resistance as a function of drain current": "on_resistance",
            "Output Characteristics Drain Current Drain-to-Source Voltage": "output_characteristics",
            (
                "Symbol Parameter Conditions Static characteristics Drain leakage current "
                "Gate leakage current Gate resistance Dynamic characteristics QG(tot) Total gate charge"
            ): "spec_table",
        }
        for context, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(estimation._non_gate_plot_reason("axis ticks", context), expected)
        self.assertIsNone(
            estimation._non_gate_plot_reason(
                "Total Gate Charge Qg (nC)",
                "Gate Charge Characteristics Gate-to-Source Voltage",
            )
        )
        self.assertIsNone(
            estimation._non_gate_plot_reason("", "Dynamic Input/Output Characteristics")
        )
        self.assertEqual(
            estimation._non_gate_plot_reason("", "Dynamic Output Characteristics"),
            "output_characteristics",
        )

    def test_numeric_tick_parser_rejects_axis_words(self) -> None:
        self.assertIsNone(estimation._parse_numeric_label("Gate-to-Source"))
        self.assertIsNone(estimation._parse_numeric_label("Voltage"))
        self.assertEqual(estimation._parse_numeric_label("1O"), 10.0)

    def test_y_tick_column_uses_longest_linear_run(self) -> None:
        candidates = [
            (12.0, 10.0),
            (10.0, 20.0),
            (8.0, 30.0),
            (6.0, 40.0),
            (4.0, 50.0),
            (2.0, 60.0),
            (0.0, 70.0),
            (8.0, 150.0),
        ]

        ticks = estimation._normalize_y_tick_candidates(candidates)

        self.assertEqual([value for value, _y in ticks], [12.0, 10.0, 8.0, 6.0, 4.0, 2.0, 0.0])

    def test_y_tick_column_keeps_run_with_one_missing_label(self) -> None:
        candidates = [
            (12.0, 10.0),
            (10.0, 30.0),
            (8.0, 50.0),
            (4.0, 90.0),
            (2.0, 110.0),
            (0.0, 130.0),
        ]

        runs = estimation._normalize_y_tick_candidate_runs(candidates)

        self.assertIn(candidates, runs)

    def test_plateau_estimator_resamples_large_flat_x_gap(self) -> None:
        plateau_y = 180
        curve = [
            (x, int(470 - (x - 20) * (470 - (plateau_y + 25)) / 220))
            for x in range(20, 241, 4)
        ]
        curve.extend([(244, plateau_y + 25), (510, plateau_y + 10)])
        curve.extend(
            (x, int(plateau_y + 10 - (x - 514) * (plateau_y - 50) / 266))
            for x in range(514, 781, 4)
        )

        value, y_px = estimation._estimate_vpl_from_curve(
            curve,
            mock.MagicMock(y_ticks=[]),
            estimation.pymupdf.Rect(0.0, 0.0, 800.0, 500.0),
            1.0,
            (0, 0, 800, 500),
            [(10.0, 0.0), (0.0, 500.0)],
        )

        self.assertAlmostEqual(value, 6.05, delta=0.2)
        self.assertAlmostEqual(y_px, 197.5, delta=10.0)

    def test_plateau_resampler_does_not_bridge_steep_x_gap(self) -> None:
        curve = [(0, 400), (4, 396), (8, 392), (200, 200), (204, 196)]

        resampled = estimation._resample_flat_x_gaps(curve, (0, 0, 240, 500))

        self.assertEqual(resampled, curve)

    def test_x_axis_refinement_rejects_row_above_panel(self) -> None:
        panel_rect = estimation.pymupdf.Rect(100.0, 200.0, 400.0, 500.0)

        def row(y: float, count: int) -> list[tuple[object, ...]]:
            return [
                (x - 2, y - 2, x + 2, y + 2, str(index * 20))
                for index, x in enumerate(range(130, 130 + 30 * count, 30))
            ]

        page = mock.MagicMock()
        page.get_text.return_value = row(180.0, 8) + row(485.0, 6)

        axis = estimation._best_x_axis_for_panel(page, panel_rect)

        self.assertIsNotNone(axis)
        assert axis is not None
        self.assertEqual(axis[1], 485.0)
        self.assertEqual(len(axis[0]), 6)

    def test_regular_grid_detector_selects_larger_neighboring_grid(self) -> None:
        from PIL import Image, ImageDraw

        image = Image.new("L", (520, 300), 255)
        draw = ImageDraw.Draw(image)
        for x in (10, 55, 100, 145):
            draw.line((x, 40, x, 240), fill=80, width=2)
        for x in (210, 264, 318, 372, 426, 480):
            for y in range(40, 241, 8):
                draw.line((x, y, x, min(y + 4, 240)), fill=80, width=2)
        for y in (40, 80, 120, 160, 200, 240):
            for x in range(210, 481, 8):
                draw.line((x, y, min(x + 4, 480), y), fill=80, width=2)

        detected = trace._detect_regular_grid_box(image.convert("RGB"), (0, 0, 519, 299))

        self.assertIsNotNone(detected)
        assert detected is not None
        box, horizontal, vertical = detected
        self.assertEqual(box, (210, 40, 480, 240))
        self.assertEqual(len(horizontal), 6)
        self.assertEqual(len(vertical), 6)

    def test_vector_trace_densifies_only_connected_curve_segments(self) -> None:
        def line(a: tuple[float, float], b: tuple[float, float]) -> dict[str, object]:
            return {
                "color": (0.13, 0.16, 0.18),
                "items": [("l", estimation.pymupdf.Point(*a), estimation.pymupdf.Point(*b))],
            }

        page = mock.MagicMock()
        page.get_drawings.return_value = [
            line((0.0, 100.0), (20.0, 60.0)),
            line((20.0, 60.0), (50.0, 60.0)),
            line((50.0, 60.0), (100.0, 0.0)),
            line((5.0, 10.0), (15.0, 14.0)),
        ]

        points = trace._trace_vector_gate_curve(
            page,
            estimation.pymupdf.Rect(0.0, 0.0, 100.0, 100.0),
            1.0,
            (0, 0, 100, 100),
        )

        self.assertGreaterEqual(len(points), 100)
        self.assertEqual(points[0], (0, 100))
        self.assertEqual(points[-1], (100, 0))

    def test_vector_trace_rejects_grid_lines_alone(self) -> None:
        page = mock.MagicMock()
        page.get_drawings.return_value = [
            {
                "color": (0.1, 0.1, 0.1),
                "items": [
                    ("l", estimation.pymupdf.Point(0.0, y), estimation.pymupdf.Point(100.0, y))
                    for y in (0.0, 25.0, 50.0, 75.0, 100.0)
                ]
                + [
                    ("l", estimation.pymupdf.Point(x, 0.0), estimation.pymupdf.Point(x, 100.0))
                    for x in (0.0, 25.0, 50.0, 75.0, 100.0)
                ]
                + [
                    ("l", estimation.pymupdf.Point(x, 40.0), estimation.pymupdf.Point(x + 20.0, 40.0))
                    for x in (0.0, 20.0, 40.0, 60.0, 80.0)
                ],
            }
        ]

        points = trace._trace_vector_gate_curve(
            page,
            estimation.pymupdf.Rect(0.0, 0.0, 100.0, 100.0),
            1.0,
            (0, 0, 100, 100),
        )

        self.assertEqual(points, [])

    def test_axis_binding_expands_detected_box_without_clipping_curve(self) -> None:
        detected = (120, 80, 400, 320)

        bound = gate._bind_plot_box_to_axes(
            detected,
            estimation.pymupdf.Rect(10.0, 20.0, 210.0, 180.0),
            2.0,
            [(0.0, 50.0), (10.0, 100.0), (20.0, 150.0)],
            [(10.0, 40.0), (5.0, 90.0), (0.0, 140.0)],
            (500, 400),
        )

        self.assertEqual(bound, (80, 40, 400, 320))

    def test_regular_grid_rejects_partial_left_neighbor(self) -> None:
        self.assertFalse(
            gate._regular_grid_matches_panel(
                (167, 154, 813, 646),
                (168, 154, 813, 769),
                (141, 116, 843, 805),
            )
        )
        self.assertTrue(
            gate._regular_grid_matches_panel(
                (471, 176, 1113, 576),
                (141, 116, 1233, 749),
                (141, 116, 1233, 749),
                allow_neighbor_split=True,
            )
        )

    def test_clipped_panel_expands_only_to_much_larger_containing_image(self) -> None:
        page = mock.MagicMock()
        page.rect = estimation.pymupdf.Rect(0.0, 0.0, 600.0, 800.0)
        page.get_images.return_value = [(10,), (20,)]
        page.get_image_rects.side_effect = [
            [estimation.pymupdf.Rect(90.0, 90.0, 210.0, 210.0)],
            [estimation.pymupdf.Rect(70.0, 70.0, 230.0, 230.0)],
        ]

        expanded = gate._containing_chart_image(
            page, estimation.pymupdf.Rect(100.0, 100.0, 200.0, 200.0)
        )

        self.assertEqual(expanded, estimation.pymupdf.Rect(70.0, 70.0, 230.0, 230.0))

    def test_clipped_panel_does_not_expand_to_full_page_scan(self) -> None:
        page = mock.MagicMock()
        page.rect = estimation.pymupdf.Rect(0.0, 0.0, 400.0, 600.0)
        page.get_images.return_value = [(10,)]
        page.get_image_rects.return_value = [page.rect]

        expanded = gate._containing_chart_image(
            page, estimation.pymupdf.Rect(100.0, 100.0, 200.0, 200.0)
        )

        self.assertIsNone(expanded)

    def test_neighbor_image_count_deduplicates_repeated_placement(self) -> None:
        page = mock.MagicMock()
        placement = estimation.pymupdf.Rect(80.0, 80.0, 220.0, 220.0)
        page.get_images.return_value = [(10,), (10,)]
        page.get_image_rects.return_value = [placement]

        count = gate._overlapping_image_count(
            page, estimation.pymupdf.Rect(100.0, 100.0, 200.0, 200.0)
        )

        self.assertEqual(count, 1)

    def test_y_axis_refinement_rejects_remote_higher_scoring_column(self) -> None:
        panel_rect = estimation.pymupdf.Rect(215.0, 310.0, 595.0, 522.0)

        def words_at(x: float, rows: list[tuple[float, float]]) -> list[tuple[object, ...]]:
            return [(x - 2, y - 2, x + 2, y + 2, str(value)) for value, y in rows]

        local_words = words_at(
            335.0,
            [(10.0, 326.0), (8.0, 358.0), (6.0, 390.0), (4.0, 422.0), (2.0, 454.0), (0.0, 486.0)],
        )
        remote_words = words_at(
            87.0,
            [(12.0, 310.0), (10.0, 340.0), (8.0, 370.0), (6.0, 400.0), (4.0, 430.0), (2.0, 460.0), (0.0, 490.0)],
        )
        x_axis_words = [
            (x - 2, 491.0, x + 2, 495.0, str(value))
            for value, x in [(0.0, 341.0), (45.0, 368.0), (90.0, 395.0), (135.0, 421.0), (180.0, 448.0)]
        ]
        page = mock.MagicMock()
        page.get_text.return_value = remote_words + local_words + x_axis_words

        axis = estimation._best_y_axis_for_panel(page, panel_rect)

        self.assertIsNotNone(axis)
        assert axis is not None
        ticks, axis_x = axis
        self.assertEqual(
            ticks,
            [(10.0, 326.0), (8.0, 358.0), (6.0, 390.0), (4.0, 422.0), (2.0, 454.0), (0.0, 486.0)],
        )
        self.assertEqual(axis_x, 335.0)

        page.get_text.return_value = remote_words + x_axis_words
        self.assertIsNone(estimation._best_y_axis_for_panel(page, panel_rect))

    def test_result_manifest_retains_status_and_panel_provenance(self) -> None:
        panel = _sample_panel()
        result = gate.GateChargeResult(
            pdf="sample.pdf",
            panel=panel,
            vpl=4.2,
            status="axis_assumed",
            score=3.5,
            trace_source="raster",
            dpi=220,
            crop_box_pt=(8.0, 18.0, 112.0, 122.0),
            plot_box_px=(10, 20, 100, 120),
            curve_px=((10, 100), (90, 30)),
            vpl_y_px=62.0,
            y_tick_count=0,
            diagnostics=("axis_assumed_0_10",),
        )

        manifest = result.to_manifest()

        self.assertEqual(manifest["status"], "axis_assumed")
        self.assertEqual(manifest["diagnostics"], ("axis_assumed_0_10",))
        self.assertEqual(manifest["panel"]["page"], 3)
        self.assertEqual(manifest["panel"]["kind"], "gate_charge")

        detached = gate._detach_transient_panel_artifacts(panel)
        self.assertEqual(detached.crop_png, "")
        self.assertEqual(detached.crop_box_pt, panel.crop_box_pt)

        higher_score = replace(result, score=9.0)
        unresolved = replace(result, vpl=None, status="unresolved", score=99.0)
        ordered = sorted([unresolved, result, higher_score], key=gate._result_sort_key)
        self.assertEqual(ordered, [higher_score, result, unresolved])

    def test_digitize_detaches_temporary_finder_crop_path(self) -> None:
        with TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "sample.pdf"
            pdf.touch()
            document = mock.MagicMock()
            with mock.patch.object(gate, "process_pdf", return_value=[_sample_panel()]), mock.patch.object(
                gate.pymupdf, "open", return_value=document
            ), mock.patch.object(gate, "_digitize_panel", return_value=None) as digitize_panel:
                results = gate.digitize_gate_charge(pdf)

        self.assertEqual(results, [])
        passed_panel = digitize_panel.call_args.args[2]
        self.assertEqual(passed_panel.crop_png, "")
        self.assertEqual(passed_panel.crop_box_pt, _sample_panel().crop_box_pt)

    def test_trace_component_tracks_monotone_gate_curve(self) -> None:
        import numpy as np

        mask = np.zeros((80, 100), dtype=np.uint8)
        for x in range(5, 95):
            y = int(round(70 - 0.55 * x))
            mask[max(0, y - 1) : min(mask.shape[0], y + 2), x] = 255

        points = trace._trace_component(mask)

        self.assertGreater(len(points), 70)
        self.assertLess(points[-1][1], points[0][1])

    def test_main_has_no_dslib_runtime_requirement(self) -> None:
        with TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "sample.pdf"
            pdf.touch()
            argv = ["dsdig digitize-vpl", str(pdf), "--out", str(Path(tmp) / "out")]
            with mock.patch("sys.argv", argv), mock.patch.object(
                vpl, "digitize_gate_charge", return_value=[]
            ):
                result = vpl.main()

        self.assertEqual(result, 1)

    def test_cli_propagates_vpl_failure_exit_code(self) -> None:
        argv = ["dsdig", "digitize-vpl", "missing.pdf"]
        with mock.patch("sys.argv", argv), mock.patch.object(vpl, "main", return_value=7):
            with self.assertRaises(SystemExit) as raised:
                cli.main()

        self.assertEqual(raised.exception.code, 7)

    def test_real_di110_selects_local_gate_charge_panel(self) -> None:
        pdf = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets/diotec/DI110N15PQ.pdf"
        if not pdf.exists():
            self.skipTest("DI110 regression PDF is not configured")

        result = gate.find_vpl_result(pdf)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.vpl, 3.2, delta=0.5)
        panel_y0, panel_y1 = result.panel.bbox_pt[1], result.panel.bbox_pt[3]
        crop_y0, crop_y1 = result.crop_box_pt[1], result.crop_box_pt[3]
        overlap = max(0.0, min(panel_y1, crop_y1) - max(panel_y0, crop_y0))
        self.assertGreaterEqual(overlap / min(panel_y1 - panel_y0, crop_y1 - crop_y0), 0.75)

    def test_real_psmn_selects_right_gate_panel_and_numeric_plateau(self) -> None:
        root = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"
        pdf = root / "nxp/PSMN1R2-55SLH.pdf"
        if not pdf.exists():
            self.skipTest("PSMN regression PDF is not configured")

        result = gate.find_vpl_result(pdf)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.vpl, 2.4, delta=0.5)
        self.assertGreater(result.crop_box_pt[0], result.panel.bbox_pt[0] + 50.0)

    def test_real_sij_selects_gate_charge_instead_of_output_characteristics(self) -> None:
        pdf = (
            Path(os.environ.get("DSDIG_DATASHEET_ROOT", "."))
            / "datasheets/vishay/SIJ482DP-T1-GE3.pdf"
        )
        if not pdf.exists():
            self.skipTest("SIJ regression PDF is not configured")

        result = gate.find_vpl_result(pdf)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.vpl, 2.9, delta=0.5)
        self.assertLess(result.crop_box_pt[1], result.panel.bbox_pt[3])
        self.assertGreater(result.crop_box_pt[3], result.panel.bbox_pt[1])

    def test_real_axis_binding_backlog_is_numeric_and_local(self) -> None:
        root = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"
        cases = {
            "xnrusemi/XR150N04.pdf": (
                3.1,
                lambda result: (
                    result.crop_box_pt[1] + result.plot_box_px[1] / (result.dpi / 72) < 190
                    and result.plot_box_px[3] - result.plot_box_px[1] >= 350
                ),
            ),
            "ti/TPS1100.pdf": (
                3.1,
                lambda result: result.crop_box_pt[0] + result.plot_box_px[0] / (result.dpi / 72) < 220,
            ),
            "infineon/IPB019N08N3GATMA1.pdf": (4.6, lambda result: result.crop_box_pt[1] < 100),
            "infineon/IRFS4310TRRPBF.pdf": (6.5, lambda result: result.crop_box_pt[0] < 340),
            "huayi/HYG016N04LS1B.pdf": (
                3.6,
                lambda result: result.crop_box_pt[0] + result.plot_box_px[0] / (result.dpi / 72) > 330,
            ),
        }
        if not all((root / rel).exists() for rel in cases):
            self.skipTest("axis-binding regression PDFs are not configured")

        for rel, (reference, bbox_gate) in cases.items():
            with self.subTest(pdf=rel):
                result = gate.find_vpl_result(root / rel)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertAlmostEqual(result.vpl, reference, delta=0.5)
                self.assertNotEqual(result.status, "axis_assumed")
                self.assertGreaterEqual(result.y_tick_count, 4)
                self.assertTrue(bbox_gate(result), result)

    def test_real_regular_grid_fallback_preserves_bsb056(self) -> None:
        pdf = (
            Path(os.environ.get("DSDIG_DATASHEET_ROOT", "."))
            / "datasheets/infineon/BSB056N10NN3GXUMA2.pdf"
        )
        if not pdf.exists():
            self.skipTest("BSB056 regression PDF is not configured")

        result = gate.find_vpl_result(pdf)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result.vpl, 4.2, delta=0.5)
        self.assertNotIn("axis_inferred_from_regular_grid", result.diagnostics)

    def test_real_irregular_gap_plateau_is_numeric(self) -> None:
        pdf = (
            Path(os.environ.get("DSDIG_DATASHEET_ROOT", "."))
            / "datasheets/infineon/IPI65R190CFD.pdf"
        )
        if not pdf.exists():
            self.skipTest("IPI65R190CFD regression PDF is not configured")

        result = gate.find_vpl_result(pdf)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result.vpl, 6.4, delta=0.5)
        self.assertGreaterEqual(result.y_tick_count, 4)

    def test_real_dual_axis_dynamic_input_output_is_numeric(self) -> None:
        pdf = (
            Path(os.environ.get("DSDIG_DATASHEET_ROOT", "."))
            / "datasheets/toshiba/XPQR8308QB.pdf"
        )
        if not pdf.exists():
            self.skipTest("XPQR8308QB regression PDF is not configured")

        result = gate.find_vpl_result(pdf)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.panel.page, 7)
        self.assertIn("Dynamic Input/Output", result.panel.title)
        self.assertAlmostEqual(result.vpl, 5.5, delta=0.5)
        self.assertEqual(result.status, "axis_grid_inferred")
        page_three = next(item for item in gate.digitize_gate_charge(pdf) if item.panel.page == 3)
        self.assertIsNone(page_three.vpl)
        self.assertEqual(page_three.status, "unresolved")

    def test_real_faint_vector_gate_curve_is_numeric(self) -> None:
        pdf = (
            Path(os.environ.get("DSDIG_DATASHEET_ROOT", "."))
            / "datasheets/hxy/SIS444DN-T1-GE3-HXY.pdf"
        )
        if not pdf.exists():
            self.skipTest("SIS444DN regression PDF is not configured")

        result = gate.find_vpl_result(pdf)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result.vpl, 3.0, delta=0.5)
        self.assertEqual(result.trace_source, "vector")
        self.assertGreaterEqual(len(result.curve_px), 100)
        self.assertGreaterEqual(result.y_tick_count, 4)


if __name__ == "__main__":
    unittest.main()
