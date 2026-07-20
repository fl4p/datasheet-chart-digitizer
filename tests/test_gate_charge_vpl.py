import unittest
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

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

    def test_y_tick_column_repairs_one_sequence_proven_missing_decimal(self) -> None:
        candidates = [
            (45.0, 10.0),
            (3.6, 30.0),
            (2.7, 50.0),
            (1.8, 70.0),
            (0.9, 90.0),
            (0.0, 110.0),
        ]

        ticks = estimation._normalize_y_tick_candidates(
            candidates, repair_missing_decimal=True
        )

        self.assertEqual([value for value, _y in ticks], [4.5, 3.6, 2.7, 1.8, 0.9, 0.0])
        underconstrained = estimation._normalize_y_tick_candidates(
            [(45.0, 10.0), (3.6, 30.0), (2.7, 50.0), (1.8, 70.0)],
            repair_missing_decimal=True,
        )
        self.assertNotIn(4.5, [value for value, _y in underconstrained])

    def test_curve_score_is_invariant_to_context_crop_padding(self) -> None:
        local = [(x, 200 - x) for x in range(0, 161, 4)]
        shifted = [(x + 120, y + 80) for x, y in local]

        score = gate._score_curve_in_plot(shifted, (120, 80, 320, 280))

        self.assertGreater(score, -1e8)
        self.assertAlmostEqual(score, gate._curve_score(local, 201, 201))

    def test_plot_local_curve_score_still_refuses_a_genuinely_sparse_trace(self) -> None:
        sparse = [(120 + 8 * index, 260 - 5 * index) for index in range(20)]

        score = gate._score_curve_in_plot(sparse, (120, 80, 320, 280))

        self.assertEqual(score, -1e9)

    def test_initial_ramp_coverage_gate_is_independently_load_bearing(self) -> None:
        plot_box = (100, 80, 500, 380)
        source_complete = [(120, 350), (180, 290), (260, 240), (420, 100)]
        source_truncated = [(121, 350), (180, 290), (260, 240), (420, 100)]

        self.assertFalse(gate._curve_missing_initial_ramp(source_complete, plot_box))
        self.assertTrue(gate._curve_missing_initial_ramp(source_truncated, plot_box))
        self.assertEqual(gate.MAX_CURVE_LEFT_GAP_FRACTION, 0.05)

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

    def test_x_axis_refinement_joins_split_digit_tick_labels(self) -> None:
        panel_rect = estimation.pymupdf.Rect(95.0, 100.0, 310.0, 300.0)
        page = mock.MagicMock()
        words: list[tuple[object, ...]] = []
        for value, x in zip((10, 20, 30, 40, 50), (120, 160, 200, 240, 280)):
            for offset, glyph in enumerate(str(value)):
                gx0 = x - 4.0 + 4.0 * offset
                words.append((gx0, 286.0, gx0 + 3.9, 296.0, glyph))
        page.get_text.return_value = words

        axis = estimation._best_x_axis_for_panel(page, panel_rect)

        self.assertIsNotNone(axis)
        assert axis is not None
        self.assertEqual([value for value, _x in axis[0]], [10, 20, 30, 40, 50])

    def test_x_axis_refinement_skips_ocr_corruption_without_inventing_ticks(self) -> None:
        panel_rect = estimation.pymupdf.Rect(90.0, 100.0, 340.0, 300.0)
        page = mock.MagicMock()
        observed = (0, 10, 20, 30, 40, 30, 60, 70, 80, 30)
        page.get_text.return_value = [
            (x - 3.0, 286.0, x + 3.0, 296.0, str(value))
            for value, x in zip(observed, range(100, 300, 20), strict=True)
        ]

        axis = estimation._best_x_axis_for_panel(page, panel_rect)

        self.assertIsNotNone(axis)
        assert axis is not None
        self.assertEqual(
            [value for value, _x in axis[0]],
            [0, 10, 20, 30, 40, 60, 70, 80],
        )

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

    def test_axis_binding_contracts_fallback_box_to_evidenced_ticks(self) -> None:
        bound = gate._bind_plot_box_to_axes(
            (90, 70, 450, 350),
            estimation.pymupdf.Rect(0.0, 0.0, 250.0, 200.0),
            2.0,
            [(0.0, 60.0), (10.0, 110.0), (20.0, 160.0)],
            [(10.0, 50.0), (5.0, 100.0), (0.0, 150.0)],
            (500, 400),
            detector_used_fallback=True,
        )

        self.assertEqual((120, 100, 320, 300), bound)

    def test_aligned_frame_rejects_adjacent_panel_divider_and_scales(self) -> None:
        def fixture(scale: int) -> Image.Image:
            image = Image.new("RGB", (340 * scale, 240 * scale), "white")
            draw = ImageDraw.Draw(image)
            frame = tuple(value * scale for value in (40, 30, 240, 180))
            draw.rectangle(frame, outline="black", width=2 * scale)
            for y in (60, 90, 120, 150):
                draw.line((40 * scale, y * scale, 240 * scale, y * scale), fill="black", width=scale)
            for x in (80, 120, 160, 200):
                draw.line((x * scale, 30 * scale, x * scale, 180 * scale), fill="black", width=scale)
            # A taller neighbouring-panel divider must not replace the frame.
            draw.line((280 * scale, 10 * scale, 280 * scale, 220 * scale), fill="black", width=2 * scale)
            return image

        detected_1x = trace._detect_aligned_plot_frame(
            np.asarray(fixture(1).convert("L")), (20, 10, 300, 210)
        )
        detected_2x = trace._detect_aligned_plot_frame(
            np.asarray(fixture(2).convert("L")), (40, 20, 600, 420)
        )

        self.assertIsNotNone(detected_1x)
        self.assertIsNotNone(detected_2x)
        assert detected_1x is not None and detected_2x is not None
        for actual, expected in zip(detected_1x, (40, 30, 240, 180), strict=True):
            self.assertAlmostEqual(actual, expected, delta=1)
        for actual, expected in zip(
            detected_2x, (2 * value for value in detected_1x), strict=True
        ):
            self.assertAlmostEqual(actual, expected, delta=1)

    def test_aligned_frame_preserves_real_edge_past_last_labeled_tick(self) -> None:
        image = Image.new("RGB", (320, 220), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 25, 260, 185), outline="black", width=2)
        for y in (65, 105, 145):
            draw.line((30, y, 260, y), fill="black")
        for x in (75, 120, 165, 210):
            draw.line((x, 25, x, 185), fill="black")

        detected = trace._detect_aligned_plot_frame(
            np.asarray(image.convert("L")), (20, 15, 275, 200)
        )

        # The final labeled tick would be x=210; frame evidence, not tick
        # spacing, preserves the genuine unlabeled interval to x=260.
        self.assertIsNotNone(detected)
        assert detected is not None
        for actual, expected in zip(detected, (30, 25, 260, 185), strict=True):
            self.assertAlmostEqual(actual, expected, delta=1)

    def test_aligned_frame_wins_axis_tie_against_loose_extra_interval(self) -> None:
        accepted = gate._aligned_frame_improves_axis_binding(
            (100, 80, 500, 380),
            (100, 80, 600, 380),
            estimation.pymupdf.Rect(0.0, 0.0, 700.0, 500.0),
            1.0,
            [(0.0, 100.0), (1.0, 200.0), (2.0, 300.0), (3.0, 400.0), (4.0, 500.0)],
            [(3.0, 80.0), (2.0, 180.0), (1.0, 280.0), (0.0, 380.0)],
        )

        self.assertTrue(accepted)

    def test_inboard_edge_tick_is_snapped_to_evidenced_plot_corner(self) -> None:
        ticks = ((0.0, 100.0), (10.0, 200.0), (20.0, 295.0))

        snapped = gate._snap_tick_coordinates_to_plot(ticks, 100, 300)

        self.assertEqual(((0.0, 100.0), (10.0, 200.0), (20.0, 300.0)), snapped)

    def test_two_endpoint_ticks_snap_to_evidenced_plot_corners(self) -> None:
        ticks = ((10.0, 102.0), (0.0, 296.0))

        snapped = gate._snap_tick_coordinates_to_plot(ticks, 100, 300)

        self.assertEqual(((10.0, 100.0), (0.0, 300.0)), snapped)

    def test_only_evidenced_edge_snaps_when_frame_extends_past_last_tick(self) -> None:
        ticks = ((0.0, 102.0), (10.0, 150.0), (20.0, 200.0))

        snapped = gate._snap_tick_coordinates_to_plot(ticks, 100, 260)

        self.assertEqual(((0.0, 100.0), (10.0, 150.0), (20.0, 200.0)), snapped)

    def test_terminal_vector_bundle_stays_on_one_branch(self) -> None:
        median = [(x, 100) for x in range(61)]
        upper = [(x, 100) for x in range(31)]
        upper.extend((x, 100 - 2 * (x - 30)) for x in range(31, 51))
        upper.extend((x, 80 - 2 * (x - 51)) for x in range(51, 61))
        for index in range(31, 61):
            median[index] = (index, upper[index][1] + 10)

        selected = trace._terminal_bundle_upper_branch(
            median, upper, (0, 0, 60, 120)
        )

        self.assertEqual(selected[-1], (50, 60))
        self.assertTrue(all(b[1] <= a[1] for a, b in zip(selected, selected[1:])))

    def test_terminal_gridline_run_is_trimmed_after_source_curve_ends(self) -> None:
        curve = [(x, 100 - x) for x in range(0, 81, 4)]
        curve.extend((x, 20) for x in range(84, 101, 4))

        trimmed = gate._trim_terminal_flat_grid_capture(curve, (0, 0, 100, 120))

        self.assertEqual(trimmed[-1], (80, 20))

    def test_short_terminal_flat_source_segment_is_retained(self) -> None:
        curve = [(x, 100 - x) for x in range(0, 93, 4)]
        curve.extend([(96, 8), (100, 8)])

        self.assertEqual(
            curve, gate._trim_terminal_flat_grid_capture(curve, (0, 0, 100, 120))
        )

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

    def test_closed_panel_cell_clips_gate_context_at_all_four_rails(self) -> None:
        point = estimation.pymupdf.Point
        page = mock.MagicMock()
        page.get_drawings.return_value = [
            {
                "items": [
                    ("l", point(50.0, 100.0), point(300.0, 100.0)),
                    ("l", point(50.0, 300.0), point(300.0, 300.0)),
                    ("l", point(50.0, 100.0), point(50.0, 300.0)),
                    ("l", point(300.0, 100.0), point(300.0, 300.0)),
                    # Neighbour divider and inner plot grid must not replace
                    # the source-owned enclosing cell.
                    ("l", point(330.0, 100.0), point(330.0, 300.0)),
                    ("l", point(80.0, 150.0), point(280.0, 150.0)),
                ]
            }
        ]

        cell = gate._enclosing_panel_cell(
            page, estimation.pymupdf.Rect(80.0, 110.0, 280.0, 285.0)
        )

        self.assertEqual(cell, estimation.pymupdf.Rect(50.0, 100.0, 300.0, 300.0))

    def test_incomplete_panel_cell_does_not_clip_gate_context(self) -> None:
        point = estimation.pymupdf.Point
        page = mock.MagicMock()
        page.get_drawings.return_value = [
            {
                "items": [
                    ("l", point(50.0, 100.0), point(300.0, 100.0)),
                    ("l", point(50.0, 300.0), point(300.0, 300.0)),
                    ("l", point(50.0, 100.0), point(50.0, 300.0)),
                ]
            }
        ]

        cell = gate._enclosing_panel_cell(
            page, estimation.pymupdf.Rect(80.0, 110.0, 280.0, 285.0)
        )

        self.assertIsNone(cell)

    def test_gate_context_stops_after_foreign_caption_and_before_cap_axis(self) -> None:
        page = mock.MagicMock()
        page.get_text.return_value = [
            (120.0, 80.0, 160.0, 90.0, "Figure", 1, 1, 0),
            (164.0, 80.0, 174.0, 90.0, "2.", 1, 1, 1),
            (178.0, 80.0, 250.0, 90.0, "Saturation", 1, 1, 2),
            (310.0, 130.0, 320.0, 210.0, "Capacitance", 2, 1, 0),
        ]

        clipped = gate._clip_context_at_foreign_text(
            page,
            estimation.pymupdf.Rect(100.0, 92.0, 280.0, 250.0),
            estimation.pymupdf.Rect(54.0, 54.0, 322.0, 302.0),
            4,
        )

        self.assertEqual(clipped.y0, 91.0)
        self.assertEqual(clipped.x1, 295.0)

    def test_own_figure_caption_does_not_clip_gate_context(self) -> None:
        page = mock.MagicMock()
        page.get_text.return_value = [
            (120.0, 80.0, 160.0, 90.0, "Figure", 1, 1, 0),
            (164.0, 80.0, 174.0, 90.0, "4.", 1, 1, 1),
            (178.0, 80.0, 240.0, 90.0, "Gate Charge", 1, 1, 2),
        ]
        crop = estimation.pymupdf.Rect(54.0, 54.0, 322.0, 302.0)

        clipped = gate._clip_context_at_foreign_text(
            page,
            estimation.pymupdf.Rect(100.0, 92.0, 280.0, 250.0),
            crop,
            4,
        )

        self.assertEqual(clipped, crop)

    def test_finder_owned_caption_clips_after_axis_refinement_expands_upward(self) -> None:
        page = mock.MagicMock()
        page.get_text.return_value = [
            (120.0, 80.0, 160.0, 90.0, "Figure", 1, 1, 0),
            (164.0, 80.0, 174.0, 90.0, "2.", 1, 1, 1),
        ]

        clipped = gate._clip_context_at_foreign_text(
            page,
            estimation.pymupdf.Rect(100.0, 50.0, 280.0, 250.0),
            estimation.pymupdf.Rect(54.0, 20.0, 322.0, 302.0),
            4,
            caption_owner_rect=estimation.pymupdf.Rect(100.0, 92.0, 280.0, 250.0),
        )

        self.assertEqual(clipped.y0, 91.0)

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
        self.assertFalse(manifest["physical_output_available"])
        self.assertIsNone(manifest["vpl"])
        self.assertEqual(manifest["curve_px"], ())
        self.assertIsNone(manifest["vpl_y_px"])
        self.assertEqual(manifest["panel"]["page"], 3)
        self.assertEqual(manifest["panel"]["kind"], "gate_charge")

        detached = gate._detach_transient_panel_artifacts(panel)
        self.assertEqual(detached.crop_png, "")
        self.assertEqual(detached.crop_box_pt, panel.crop_box_pt)

        higher_score = replace(result, score=9.0)
        unresolved = replace(result, vpl=None, status="unresolved", score=99.0)
        ordered = sorted([unresolved, result, higher_score], key=gate._result_sort_key)
        self.assertEqual(ordered, [higher_score, result, unresolved])

        served = replace(result, status="ok")
        served_manifest = served.to_manifest()
        self.assertTrue(served_manifest["physical_output_available"])
        self.assertEqual(served_manifest["vpl"], 4.2)
        self.assertEqual(served_manifest["curve_px"], ((10, 100), (90, 30)))

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
        tick_spacing = result.x_ticks_px[-1][1] - result.x_ticks_px[-2][1]
        self.assertGreater(result.plot_box_px[2], result.x_ticks_px[-1][1] + 0.75 * tick_spacing)
        plot_height = result.plot_box_px[3] - result.plot_box_px[1]
        self.assertLessEqual(
            min(y for _x, y in result.curve_px) - result.plot_box_px[1],
            0.05 * plot_height,
        )
        panel_y0, panel_y1 = result.panel.bbox_pt[1], result.panel.bbox_pt[3]
        crop_y0, crop_y1 = result.crop_box_pt[1], result.crop_box_pt[3]
        overlap = max(0.0, min(panel_y1, crop_y1) - max(panel_y0, crop_y0))
        self.assertGreaterEqual(overlap / min(panel_y1 - panel_y0, crop_y1 - crop_y0), 0.75)

    def test_real_panjit_huayi_frames_bind_to_own_grid_not_neighbor_divider(self) -> None:
        root = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"
        cases = {
            "panjit/PSMB050N10NS2_R2_00601.pdf": ((166, 170, 764, 604), 4.96),
            "panjit/PSMB055N08NS1_R2_00601.pdf": ((164, 168, 763, 591), 4.86),
            "panjit/PSMP050N10NS2_T0_00601.pdf": ((166, 170, 764, 604), 4.96),
            "panjit/PSMP055N08NS1_T0_00601.pdf": ((164, 168, 763, 591), 4.86),
            "huayi/HY1001D.pdf": ((141, 162, 737, 605), 4.61),
        }
        if not all((root / relative).exists() for relative in cases):
            self.skipTest("Panjit/Huayi frame regressions are not configured")

        for relative, (expected_box, expected_vpl) in cases.items():
            with self.subTest(pdf=relative):
                result = gate.find_vpl_result(root / relative)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(expected_box, result.plot_box_px)
                self.assertAlmostEqual(expected_vpl, result.vpl, delta=0.03)
                self.assertEqual(result.plot_box_px[0], result.x_ticks_px[0][1])
                self.assertEqual(result.plot_box_px[1], result.y_ticks_px[0][1])
                self.assertEqual(result.plot_box_px[3], result.y_ticks_px[-1][1])
                if relative.endswith("HY1001D.pdf"):
                    spacing = result.x_ticks_px[-1][1] - result.x_ticks_px[-2][1]
                    self.assertAlmostEqual(
                        result.plot_box_px[2] - result.x_ticks_px[-1][1],
                        spacing,
                        delta=4.0,
                    )
                else:
                    self.assertEqual(result.plot_box_px[2], result.x_ticks_px[-1][1])

    def test_real_gate_frame_does_not_expand_into_neighbor_or_whitespace(self) -> None:
        root = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"
        cases = (
            "xnrusemi/2N7002K.pdf",
            "onsemi/FDB120N10.pdf",
            "agmsemi/AGM056N10C.pdf",
        )
        if not all((root / relative).exists() for relative in cases):
            self.skipTest("gate-frame expansion regressions are not configured")

        for relative in cases:
            with self.subTest(pdf=relative):
                result = gate.find_vpl_result(root / relative)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.status, "ok")
                self.assertAlmostEqual(
                    result.plot_box_px[2], result.x_ticks_px[-1][1], delta=4.0
                )

    def test_real_mcc_omitted_zero_keeps_initial_rise_and_plateau(self) -> None:
        root = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets/mcc"
        cases = {
            "MCG35N04A-TP.pdf": 3.0,
            "MCACL120N10Y-TP.pdf": 5.0,
        }
        if not all((root / name).exists() for name in cases):
            self.skipTest("MCC omitted-zero regression PDFs are not configured")

        for name, expected_vpl in cases.items():
            with self.subTest(pdf=name):
                result = gate.find_vpl_result(root / name)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.status, "ok")
                self.assertAlmostEqual(result.vpl, expected_vpl, delta=0.5)
                curve_xs = [x for x, _y in result.curve_px]
                plot_width = result.plot_box_px[2] - result.plot_box_px[0]
                self.assertLessEqual(min(curve_xs) - result.plot_box_px[0], 0.02 * plot_width)

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

    def test_real_dual_axis_without_charge_unit_is_fail_closed(self) -> None:
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
        self.assertEqual(result.status, "unresolved")
        self.assertIn("gate_charge_unit_unresolved", result.diagnostics)
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

    def test_real_hxy_gate_caption_cannot_override_refined_non_gate_plot(self) -> None:
        pdf = (
            Path(os.environ.get("DSDIG_DATASHEET_ROOT", "."))
            / "datasheets/hxy/DMN3009LFVQ-13-HXY.pdf"
        )
        if not pdf.exists():
            self.skipTest("DMN3009LFVQ-13-HXY regression PDF is not configured")

        result = next(
            item
            for item in gate.digitize_gate_charge(pdf, dpi=180)
            if item.panel.page == 4 and item.panel.diagram == 8
        )

        self.assertIsNone(result.vpl)
        self.assertEqual(result.status, "rejected_non_gate")
        self.assertTrue(
            any(reason.startswith("non_gate_plot:") for reason in result.diagnostics)
        )

    def test_real_raster_axis_ocr_replaces_unverified_grid_scale(self) -> None:
        pdf = (
            Path(os.environ.get("DSDIG_DATASHEET_ROOT", "."))
            / "datasheets/xnrusemi/XR100N02F.pdf"
        )
        if not pdf.exists():
            self.skipTest("XR100N02F regression PDF is not configured")

        result = next(
            item
            for item in gate.digitize_gate_charge(pdf, dpi=180)
            if item.panel.page == 4 and item.panel.diagram == 7
        )

        self.assertAlmostEqual(result.vpl, 2.2, delta=0.25)
        self.assertEqual(result.status, "ok")
        self.assertGreater(result.score, 0.0)
        self.assertNotIn("low_trace_confidence", result.diagnostics)
        self.assertNotIn("axis_inferred_from_regular_grid", result.diagnostics)
        self.assertNotIn("vpl_unresolved", result.diagnostics)
        self.assertEqual(result.y_tick_count, 6)
        self.assertEqual(
            [value for value, _y in result.y_ticks_px],
            [4.5, 3.6, 2.7, 1.8, 0.9, 0.0],
        )
        self.assertEqual(result.x_tick_unit, "nC")

    def test_real_spelled_out_gate_charge_unit_is_resolved(self) -> None:
        pdf = (
            Path(os.environ.get("DSDIG_DATASHEET_ROOT", "."))
            / "datasheets/littelfuse/IXFL32N120P.pdf"
        )
        if not pdf.exists():
            self.skipTest("IXFL32N120P regression PDF is not configured")

        result = gate.find_vpl_result(pdf)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertNotIn("gate_charge_unit_unresolved", result.diagnostics)
        self.assertEqual("nC", result.x_tick_unit)
        self.assertEqual("ok", result.status)

    def test_real_inboard_edge_tick_is_axis_aligned(self) -> None:
        root = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"
        pdf = root / "xnrusemi/XR100N02.pdf"
        if not pdf.exists():
            self.skipTest("axis-integrity regression PDFs are not configured")

        result = gate.find_vpl_result(pdf, dpi=180)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result.x_ticks_px[0][1], result.plot_box_px[0])
        self.assertAlmostEqual(result.x_ticks_px[-1][1], result.plot_box_px[2])
        self.assertAlmostEqual(result.y_ticks_px[0][1], result.plot_box_px[1])
        self.assertAlmostEqual(result.y_ticks_px[-1][1], result.plot_box_px[3])


if __name__ == "__main__":
    unittest.main()
