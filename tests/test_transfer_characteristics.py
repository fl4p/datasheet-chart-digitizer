"""Tests for the review-gated saturation transfer-curve digitizer/fitter."""

from __future__ import annotations

import csv
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pymupdf

from datasheet_chart_digitizer import find_charts
from datasheet_chart_digitizer import transfer_characteristics as tc

INFINEON = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon")
RENESAS_RJK = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/renesas/RJK0853DPB-00#J5.pdf"
)
HXY_SPD = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/hxy/SPD03N50C3ATMA1-HXY.pdf"
)
ONSEMI_NVB = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/onsemi/NVB055N60S5F.pdf"
)
ONSEMI_FDB035 = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/onsemi/FDB035N10A.pdf"
)
TI_CSD19537 = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/ti/CSD19537Q3.pdf"
)
TI_GRAY_TRANSFER_CASES = (
    Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/ti/CSD17573Q5B.pdf"),
    Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/ti/CSD18512Q5B.pdf"),
)
INFINEON_IRF6644 = INFINEON / "IRF6644.pdf"
INFINEON_BSZ018 = INFINEON / "BSZ018N04LS6.pdf"
INFINEON_IRF100PW219 = INFINEON / "IRF100PW219.pdf"


def _synthetic_curves():
    tref, thot = 25.0, 175.0
    vth, k, p = 3.0, 25.0, 2.0
    d_vth = -3.0e-3 * (thot - tref)
    d_log_k = -2.0e-3 * (thot - tref)
    vgs = np.linspace(2.5, 7.0, 800)
    cold_i = k * np.maximum(vgs - vth, 0.0) ** p
    hot_i = k * math.exp(d_log_k) * np.maximum(vgs - (vth + d_vth), 0.0) ** p
    curves = [
        tc.TransferCurve(tref, list(zip(vgs, cold_i))),
        tc.TransferCurve(thot, list(zip(vgs, hot_i))),
    ]
    anchor = {
        "tref_c": tref,
        "vth_eff_v": vth,
        "k_a_per_vp": k,
        "p": p,
        "id_gc_a": 100.0,
        "vpl_v": 5.0,
    }
    return curves, anchor


def _two_temperature_curves(delta):
    currents = np.linspace(1.0, 100.0, 400)
    cold_gates = 3.0 + 0.01 * currents
    hot_gates = cold_gates + delta(currents)
    return list(zip(cold_gates, currents)), list(zip(hot_gates, currents))


class TwoTemperatureOrderUnit(unittest.TestCase):
    def test_accepts_label_bound_hot_left_curves_below_ztc(self):
        cold, hot = _two_temperature_curves(
            lambda currents: np.full_like(currents, -0.2)
        )

        self.assertFalse(tc.validate_two_curve_order(cold, hot))

    def test_accepts_one_clean_source_resolved_crossover(self):
        cold, hot = _two_temperature_curves(
            lambda currents: -0.2 + 0.005 * currents
        )

        self.assertTrue(tc.validate_two_curve_order(cold, hot))

    def test_refuses_label_bound_hot_curve_on_the_right_at_low_current(self):
        cold, hot = _two_temperature_curves(
            lambda currents: np.full_like(currents, 0.2)
        )

        with self.assertRaisesRegex(RuntimeError, "not visibly left"):
            tc.validate_two_curve_order(cold, hot)

    def test_refuses_robust_temperature_order_recrossing(self):
        cold, hot = _two_temperature_curves(
            lambda currents: -0.15
            + 0.40 * np.exp(-((currents - 50.0) / 18.0) ** 2)
        )

        with self.assertRaisesRegex(RuntimeError, "multiple robust"):
            tc.validate_two_curve_order(cold, hot)

    def test_refuses_low_current_separation_below_source_margin(self):
        cold, hot = _two_temperature_curves(
            lambda currents: np.full_like(currents, -0.001)
        )

        with self.assertRaisesRegex(RuntimeError, "not visibly left"):
            tc.validate_two_curve_order(cold, hot)


class TemperatureFitUnit(unittest.TestCase):
    def test_transfer_uses_shared_six_pixel_review_frame(self):
        image = np.full((120, 120, 3), 255, dtype=np.uint8)
        axis = MagicMock(ticks=[])

        overlay = tc._draw_overlay(
            image,
            tc.PlotBox(10, 10, 100, 100),
            [],
            [],
            axis,
            axis,
            None,
        )

        self.assertTupleEqual(tuple(overlay[50, 12]), (0, 180, 0))

    def test_transfer_tick_row_excludes_condition_number_below_axis(self):
        plot = tc.PlotBox(70, 26, 547, 364)
        candidates = [
            (value, 70 + index * (477 / 9), 385.0)
            for index, value in enumerate(np.linspace(0.0, 1.8, 10))
        ]
        candidates.append((5.0, 333.0, 439.0))  # VDS = 5 V condition

        selected = tc._select_horizontal_tick_row(candidates, plot)

        self.assertEqual(len(selected), 10)
        self.assertEqual([value for value, _px in selected], list(np.linspace(0.0, 1.8, 10)))

    def test_disconnected_legend_sample_is_not_appended_to_curve_run(self):
        curve = [
            tc.VectorEdge((0, 10), (20, 8), [(0, 10), (20, 8)]),
            tc.VectorEdge((20, 8), (40, 2), [(20, 8), (40, 2)]),
        ]
        legend = tc.VectorEdge((36, 16), (44, 16), [(36, 16), (44, 16)])

        runs = tc._split_monotone_edge_runs([*curve, legend], 50)

        self.assertEqual(runs, [curve, [legend]])

    def test_foreign_panel_refuses_before_plot_detection(self):
        chart = {
            "pdf": "/tmp/foreign.pdf",
            "page": 1,
            "title": "Typical Transfer Characteristics",
            "text": "VDS drain-to-source voltage",
            "crop_box_pt": [0.0, 0.0, 100.0, 100.0],
        }
        fake_doc = MagicMock()
        fake_doc.__getitem__.return_value = MagicMock()
        fake_fitz = MagicMock()
        fake_fitz.open.return_value = fake_doc
        image = np.full((100, 100, 3), 255, dtype=np.uint8)

        with (
            patch.object(tc, "_load_fitz", return_value=fake_fitz),
            patch.object(tc.cv2, "imread", return_value=image),
            patch.object(tc, "_words_in_crop_px", return_value=[]),
            patch.object(tc, "_vector_plot_frame") as vector_frame,
            patch.object(tc, "find_plot_box") as raster_frame,
        ):
            with self.assertRaisesRegex(RuntimeError, "lacks owned VGS and ID"):
                tc.process_chart(chart, Path("unused.png"), Path("/tmp"), Path("x"))

        vector_frame.assert_not_called()
        raster_frame.assert_not_called()

    def test_foreign_panel_cannot_borrow_source_axes_without_temperatures(self):
        chart = {
            "pdf": "/tmp/foreign.pdf",
            "page": 1,
            "title": "Typical Transfer Characteristics",
            "text": "",
            "crop_box_pt": [0.0, 0.0, 100.0, 100.0],
        }
        fake_doc = MagicMock()
        fake_doc.__getitem__.return_value = MagicMock()
        fake_fitz = MagicMock()
        fake_fitz.open.return_value = fake_doc
        image = np.full((100, 100, 3), 255, dtype=np.uint8)

        with (
            patch.object(tc, "_load_fitz", return_value=fake_fitz),
            patch.object(tc.cv2, "imread", return_value=image),
            patch.object(
                tc, "_words_in_crop_px", return_value=[("VGS", 20.0, 80.0), ("ID", 5.0, 40.0)]
            ),
            patch.object(tc, "_vector_plot_frame") as vector_frame,
            patch.object(tc, "find_plot_box") as raster_frame,
        ):
            with self.assertRaisesRegex(RuntimeError, "lacks owned VGS and ID"):
                tc.process_chart(chart, Path("unused.png"), Path("/tmp"), Path("x"))

        vector_frame.assert_not_called()
        raster_frame.assert_not_called()

    def test_clipped_bezier_tail_requires_existing_curve_endpoint(self):
        base = (
            "c",
            pymupdf.Point(10, 90),
            pymupdf.Point(20, 85),
            pymupdf.Point(30, 70),
            pymupdf.Point(40, 60),
        )
        connected_tail = (
            "c",
            pymupdf.Point(40, 60),
            pymupdf.Point(85, 55),
            pymupdf.Point(180, 45),
            pymupdf.Point(220, 40),
        )
        unrelated_clip = (
            "c",
            pymupdf.Point(70, 20),
            pymupdf.Point(120, 20),
            pymupdf.Point(180, 20),
            pymupdf.Point(220, 20),
        )
        wrong_tangent = (
            "c",
            pymupdf.Point(40, 60),
            pymupdf.Point(10, 40),
            pymupdf.Point(-80, 20),
            pymupdf.Point(-140, 10),
        )
        drawings = [
            {
                "type": "s",
                "color": (0.0, 0.0, 0.0),
                "width": 1.2,
                "items": [base, connected_tail, unrelated_clip, wrong_tangent],
            }
        ]

        edges = tc._transfer_curve_edges(drawings, pymupdf.Rect(0, 0, 100, 100))

        self.assertEqual(len(edges), 2)
        self.assertGreater(max(x for edge in edges for x, _ in edge.points), 98.0)
        self.assertFalse(any(abs(edge.p0[0] - 70.0) < 0.1 for edge in edges))
        self.assertFalse(any(edge.p1[0] < 0.0 for edge in edges))

    def test_temperature_parser_keeps_unicode_minus_and_ignores_current(self):
        text = "30 Current 25°C Tc = 75°C –25°C"

        self.assertEqual(tc._temperatures(text), [-25.0, 25.0, 75.0])

    def test_temperature_parser_collapses_pdftotext_subscript_c_duplicate(self):
        text = "TC = 125°C TC = 25°C C TC = −55°C C"

        self.assertEqual(tc._temperatures(text), [-55.0, 25.0, 125.0])

    def test_contextual_temperatures_ignore_interleaved_current_tick(self):
        text = "T = 125°C C T = 25°C I 1 C T = -55°C C"

        self.assertEqual(tc._temperatures(text), [-55.0, 25.0, 125.0])

    def test_transfer_semantics_accept_ti_drain_to_source_current_axis(self):
        chart = {
            "title": "Transfer Characteristics",
            "text": (
                "VGS - Gate-to-Source Voltage - V "
                "IDS - Drain-to-Source Current - A"
            ),
        }

        tc._validate_transfer_panel_semantics(chart)

    def test_transfer_semantics_accept_rotated_ti_current_axis_word_order(self):
        chart = {
            "title": "Transfer Characteristics",
            "text": (
                "A - 8 Current 6 Drain-to-Source 4 DS I "
                "V - Gate-to-Source Voltage - V GS"
            ),
        }

        tc._validate_transfer_panel_semantics(chart)

    def test_transfer_semantics_accept_exact_id_vgs_formula_title(self):
        tc._validate_transfer_panel_semantics({"title": "I D - V GS", "text": ""})
        with self.assertRaisesRegex(RuntimeError, "source title"):
            tc._validate_transfer_panel_semantics({"title": "I D - V DS", "text": ""})

    def test_temperature_parser_rejects_capacitance_formula_numbers(self):
        text = "5 C = Coss + Cgd 4 C = Ciss + Cgs 2 C Crss"

        with self.assertRaisesRegex(RuntimeError, "expected 2..6 temperature labels"):
            tc._temperatures(text)

    def test_temperature_parser_keeps_reordered_nxp_tj_labels(self):
        text = "T = 150 °C j T 25 °C = j"

        self.assertEqual(tc._temperatures(text), [25.0, 150.0])

    def test_transfer_semantics_reject_capacitance_panel(self):
        chart = {
            "title": "Typical Transfer Characteristics",
            "text": "VGS ID Ciss Coss Crss capacitance pF",
        }

        with self.assertRaisesRegex(RuntimeError, "capacitance semantics"):
            tc._validate_transfer_panel_semantics(chart)

    def test_transfer_semantics_require_owned_axes(self):
        chart = {
            "title": "Typical Transfer Characteristics",
            "text": "VDS drain to source voltage",
        }

        with self.assertRaisesRegex(RuntimeError, "lacks owned VGS and ID"):
            tc._validate_transfer_panel_semantics(chart)

    def test_transfer_semantics_accept_axes_from_owned_source_words(self):
        chart = {
            "title": "Typical Transfer Characteristics",
            "text": "Tj = -55 C 25 C 125 C",
        }

        tc._validate_transfer_panel_semantics(
            chart,
            [("VGS", 100.0, 300.0), ("ID", 30.0, 150.0)],
        )

    def test_two_temperature_layout_does_not_enable_clipped_tail_recovery(self):
        base = (
            "c",
            pymupdf.Point(10, 90),
            pymupdf.Point(20, 85),
            pymupdf.Point(30, 70),
            pymupdf.Point(40, 60),
        )
        clipped_tail = (
            "c",
            pymupdf.Point(40, 60),
            pymupdf.Point(85, 55),
            pymupdf.Point(180, 45),
            pymupdf.Point(220, 40),
        )
        drawings = [{
            "type": "s",
            "color": (0.0, 0.0, 0.0),
            "width": 1.2,
            "items": [base, clipped_tail],
        }]

        edges = tc._transfer_curve_edges(
            drawings,
            pymupdf.Rect(0, 0, 100, 100),
            recover_clipped_cubics=False,
        )

        self.assertEqual(len(edges), 1)

    def test_current_resampling_requires_monotone_source_order(self):
        plot = tc.PlotBox(0, 0, 100, 100)
        foldback = [(10, 90), (20, 70), (18, 50), (30, 30)]
        scrambled = [(10, 90), (20, 50), (18, 80), (30, 30)]

        self.assertTrue(tc._current_resampling_is_safe(foldback, plot))
        self.assertFalse(tc._current_resampling_is_safe(scrambled, plot))

    def test_three_labels_with_only_two_source_paths_refuses(self):
        def drawing(x0: float, x1: float):
            return {
                "type": "s",
                "color": (0.0, 0.0, 0.0),
                "width": 1.2,
                "items": [
                    (
                        "c",
                        pymupdf.Point(x0, 90),
                        pymupdf.Point(x0 + 5, 70),
                        pymupdf.Point(x1 - 5, 30),
                        pymupdf.Point(x1, 10),
                    )
                ],
            }

        page = MagicMock()
        page.get_drawings.return_value = [drawing(10, 60), drawing(20, 70)]

        with self.assertRaisesRegex(
            RuntimeError,
            "expected exactly 3 left-to-right transfer curves, found 2",
        ):
            tc._extract_curves(
                page,
                tc.CropTransform(0.0, 0.0, 1.0, 1.0),
                tc.PlotBox(0, 0, 100, 100),
                pymupdf,
                3,
            )

    def test_thin_retry_recovers_only_three_contiguous_source_paths(self):
        def chunk(x0, x1, y0, y1, *, width=0.62, segments=10):
            points = [
                pymupdf.Point(
                    x0 + (x1 - x0) * index / segments,
                    y0 + (y1 - y0) * index / segments,
                )
                for index in range(segments + 1)
            ]
            return {
                "type": "s",
                "color": (0.0, 0.0, 0.0),
                "width": width,
                "items": [
                    ("l", left, right)
                    for left, right in zip(points, points[1:])
                ],
            }

        drawings = []
        for offset in (0.0, 8.0, 16.0):
            drawings.extend([
                chunk(10 + offset, 30 + offset, 90, 60),
                chunk(30 + offset, 70 + offset, 60, 10),
            ])
        # Same-width black grid dots are individually short and disjoint. They
        # must not become a fourth source path merely because the retry lowers
        # the stroke-width floor.
        dotted = chunk(5, 50, 50, 50, segments=18)
        dotted["items"] = [
            ("l", pymupdf.Point(x, 50), pymupdf.Point(x + 0.5, 50))
            for x in range(5, 50, 2)
        ]
        drawings.append(dotted)

        candidates = tc._thin_transfer_candidates(
            drawings,
            pymupdf.Rect(0, 0, 100, 100),
            tc.CropTransform(0.0, 0.0, 1.0, 1.0),
            tc.PlotBox(0, 0, 100, 100),
            3,
        )

        self.assertEqual(len(candidates), 3)
        self.assertTrue(all(len(points) >= 50 for points in candidates))

    def test_thin_retry_refuses_two_paths_under_three_labels(self):
        def drawing(y):
            points = [pymupdf.Point(10 + 5 * index, y - 8 * index) for index in range(11)]
            return {
                "type": "s",
                "color": (0.0, 0.0, 0.0),
                "width": 0.62,
                "items": [
                    ("l", left, right)
                    for left, right in zip(points, points[1:])
                ],
            }

        candidates = tc._thin_transfer_candidates(
            [drawing(90), drawing(98)],
            pymupdf.Rect(0, 0, 100, 100),
            tc.CropTransform(0.0, 0.0, 1.0, 1.0),
            tc.PlotBox(0, 0, 100, 100),
            3,
        )

        self.assertEqual(candidates, [])

    def test_normal_width_exact_count_does_not_enter_thin_retry(self):
        def drawing(x0, y0):
            return {
                "type": "s",
                "color": (0.0, 0.0, 0.0),
                "width": 1.2,
                "items": [(
                    "c",
                    pymupdf.Point(x0, y0),
                    pymupdf.Point(x0 + 5, y0 - 25),
                    pymupdf.Point(x0 + 35, y0 - 55),
                    pymupdf.Point(x0 + 50, y0 - 80),
                )],
            }

        page = MagicMock()
        page.get_drawings.return_value = [
            drawing(10, 90),
            drawing(20, 90),
            drawing(30, 90),
        ]
        with patch.object(tc, "_thin_transfer_candidates") as retry:
            candidates = tc._extract_curves(
                page,
                tc.CropTransform(0.0, 0.0, 1.0, 1.0),
                tc.PlotBox(0, 0, 100, 100),
                pymupdf,
                3,
            )

        self.assertEqual(len(candidates), 3)
        retry.assert_not_called()

    def test_gray_rescue_refuses_when_two_full_gray_paths_compete(self):
        def drawing(x0, color):
            return {
                "type": "s",
                "color": color,
                "width": 1.2,
                "items": [(
                    "c",
                    pymupdf.Point(x0, 90),
                    pymupdf.Point(x0 + 5, 65),
                    pymupdf.Point(x0 + 35, 35),
                    pymupdf.Point(x0 + 50, 10),
                )],
            }

        page = MagicMock()
        page.get_drawings.return_value = [
            drawing(5, (0.0, 0.0, 0.0)),
            drawing(15, (1.0, 0.0, 0.0)),
            drawing(25, (0.3, 0.3, 0.3)),
            drawing(35, (0.6, 0.6, 0.6)),
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            "expected exactly 3 left-to-right transfer curves, found 2",
        ):
            tc._extract_curves(
                page,
                tc.CropTransform(0.0, 0.0, 1.0, 1.0),
                tc.PlotBox(0, 0, 100, 100),
                pymupdf,
                3,
            )

    def test_assigns_three_low_current_branches_hot_left_cold_right(self):
        currents = np.linspace(0.5, 50.0, 120)
        # At a shared sub-ZTC current, hotter curves sit left (lower VGS).
        hot = list(zip(2.10 + 0.012 * currents, currents))
        room = list(zip(2.20 + 0.012 * currents, currents))
        cold = list(zip(2.30 + 0.012 * currents, currents))

        assigned = tc._assign_temperatures(
            [room, cold, hot], [-25.0, 25.0, 75.0]
        )

        self.assertEqual([curve.tj_c for curve in assigned], [-25.0, 25.0, 75.0])
        self.assertIs(assigned[0].points, cold)
        self.assertIs(assigned[1].points, room)
        self.assertIs(assigned[2].points, hot)

    def test_three_branch_assignment_refuses_ambiguous_low_current_order(self):
        currents = np.linspace(0.5, 50.0, 120)
        shared = list(zip(2.20 + 0.012 * currents, currents))
        cold = list(zip(2.30 + 0.012 * currents, currents))

        with self.assertRaisesRegex(RuntimeError, "not strictly separated"):
            tc._assign_temperatures(
                [shared, cold, list(shared)], [-25.0, 25.0, 75.0]
            )

    def test_two_temperature_rectangles_bind_directly_to_source_curves(self):
        plot = tc.PlotBox(0, 0, 100, 100)
        left = [(30, y) for y in range(10, 91)]
        right = [(70, y) for y in range(10, 91)]
        labels = [
            (150.0, (18.0, 44.0, 25.0, 56.0)),
            (25.0, (75.0, 44.0, 82.0, 56.0)),
        ]

        bindings = tc._bind_two_temperature_labels(
            [right, left], labels, [25.0, 150.0], plot
        )

        self.assertEqual(bindings, [(25.0, 0), (150.0, 1)])

    def test_two_temperature_rectangles_refuse_ambiguous_binding(self):
        plot = tc.PlotBox(0, 0, 100, 100)
        curves = [
            [(40, y) for y in range(10, 91)],
            [(60, y) for y in range(10, 91)],
        ]
        labels = [
            (25.0, (48.0, 24.0, 52.0, 34.0)),
            (150.0, (48.0, 64.0, 52.0, 74.0)),
        ]

        with self.assertRaisesRegex(RuntimeError, "binding is ambiguous"):
            tc._bind_two_temperature_labels(
                curves, labels, [25.0, 150.0], plot
            )

    def test_opposite_outer_labels_resolve_crossing_curve_global_distance_tie(self):
        curves = [
            [(530 - (y - 680) // 3, y) for y in range(640, 721)],
            [(497 - (y - 680) // 3, y) for y in range(640, 721)],
        ]
        labels = [
            (25.0, (573.0, 680.0, 633.0, 716.0)),
            (175.0, (391.0, 655.0, 465.0, 691.0)),
        ]

        self.assertEqual(
            tc.bind_opposite_outer_labels(curves, labels, 637.0), [0, 1]
        )

    def test_between_or_same_side_labels_do_not_use_outer_label_fallback(self):
        curves = [
            [(40, y) for y in range(10, 91)],
            [(60, y) for y in range(10, 91)],
        ]
        for labels in (
            [(25.0, (46, 40, 50, 50)), (175.0, (50, 60, 54, 70))],
            [(25.0, (20, 40, 24, 50)), (175.0, (24, 60, 28, 70))],
        ):
            with self.subTest(labels=labels):
                self.assertIsNone(
                    tc.bind_opposite_outer_labels(curves, labels, 100.0)
                )

    def test_two_temperature_rectangles_require_exact_owned_label_count(self):
        plot = tc.PlotBox(0, 0, 100, 100)
        curves = [
            [(30, y) for y in range(10, 91)],
            [(70, y) for y in range(10, 91)],
        ]

        with self.assertRaisesRegex(RuntimeError, "exactly 2 owned source labels"):
            tc._bind_two_temperature_labels(
                curves,
                [(25.0, (72.0, 40.0, 78.0, 50.0))],
                [25.0, 150.0],
                plot,
            )

    def test_two_temperature_rectangles_require_bounded_source_distance(self):
        plot = tc.PlotBox(0, 0, 100, 100)
        curves = [
            [(20, y) for y in range(10, 91)],
            [(80, y) for y in range(10, 91)],
        ]
        labels = [
            (25.0, (47.0, 40.0, 49.0, 50.0)),
            (150.0, (51.0, 55.0, 53.0, 65.0)),
        ]

        with self.assertRaisesRegex(RuntimeError, "binding is ambiguous"):
            tc._bind_two_temperature_labels(
                curves, labels, [25.0, 150.0], plot
            )

    def test_label_centers_disambiguate_rectangles_overlapping_both_curves(self):
        plot = tc.PlotBox(0, 0, 100, 100)
        curves = [
            [(30, y) for y in range(10, 91)],
            [(70, y) for y in range(10, 91)],
        ]
        labels = [
            (25.0, (15.0, 20.0, 70.0, 30.0)),
            (150.0, (30.0, 65.0, 85.0, 75.0)),
        ]

        self.assertEqual(
            tc._bind_two_temperature_labels(
                curves, labels, [25.0, 150.0], plot
            ),
            [(25.0, 0), (150.0, 1)],
        )

    def test_positioned_temperature_labels_use_text_rectangles_not_lines(self):
        page = MagicMock()
        page.get_text.return_value = [
            (10.0, 40.0, 20.0, 50.0, "TJ", 1, 0, 0),
            (22.0, 40.0, 25.0, 50.0, "=", 1, 0, 1),
            (27.0, 40.0, 45.0, 50.0, "150°C", 1, 0, 2),
            (70.0, 55.0, 82.0, 65.0, "25", 2, 0, 0),
            (84.0, 55.0, 94.0, 65.0, "°C", 2, 0, 1),
            (10.0, 120.0, 50.0, 130.0, "25°C", 3, 0, 0),
        ]

        labels = tc._positioned_temperature_labels(
            page,
            tc.CropTransform(0.0, 0.0, 1.0, 1.0),
            tc.PlotBox(0, 0, 100, 100),
        )

        self.assertEqual(
            labels,
            [
                (150.0, (10.0, 40.0, 45.0, 50.0)),
                (25.0, (70.0, 55.0, 94.0, 65.0)),
            ],
        )
        page.get_drawings.assert_not_called()

    def test_positioned_labels_accept_shared_ti_condition_and_private_degree(self):
        page = MagicMock()
        page.get_text.return_value = [
            (10.0, 40.0, 20.0, 50.0, "Tj", 1, 0, 0),
            (22.0, 40.0, 25.0, 50.0, "=", 1, 0, 1),
            (27.0, 40.0, 45.0, 50.0, "25\uf0b0C,", 1, 0, 2),
            (47.0, 40.0, 60.0, 50.0, "VDS", 1, 0, 3),
            (62.0, 40.0, 65.0, 50.0, "=", 1, 0, 4),
            (67.0, 40.0, 82.0, 50.0, "4.5V", 1, 0, 5),
            (10.0, 60.0, 20.0, 70.0, "Tj", 2, 0, 0),
            (22.0, 60.0, 25.0, 70.0, "=", 2, 0, 1),
            (27.0, 60.0, 49.0, 70.0, "150\uf0b0C,", 2, 0, 2),
            (51.0, 60.0, 64.0, 70.0, "VDS", 2, 0, 3),
            (66.0, 60.0, 69.0, 70.0, "=", 2, 0, 4),
            (71.0, 60.0, 86.0, 70.0, "4.5V", 2, 0, 5),
        ]

        labels = tc._positioned_temperature_labels(
            page,
            tc.CropTransform(0.0, 0.0, 1.0, 1.0),
            tc.PlotBox(0, 0, 100, 100),
        )

        self.assertEqual(
            labels,
            [
                (25.0, (10.0, 40.0, 45.0, 50.0)),
                (150.0, (10.0, 60.0, 49.0, 70.0)),
            ],
        )

    def test_positioned_labels_refuse_mismatched_operating_conditions(self):
        page = MagicMock()
        page.get_text.return_value = [
            (10.0, 40.0, 20.0, 50.0, "Tj", 1, 0, 0),
            (22.0, 40.0, 25.0, 50.0, "=", 1, 0, 1),
            (27.0, 40.0, 45.0, 50.0, "25°C,", 1, 0, 2),
            (47.0, 40.0, 60.0, 50.0, "VDS", 1, 0, 3),
            (62.0, 40.0, 65.0, 50.0, "=", 1, 0, 4),
            (67.0, 40.0, 82.0, 50.0, "4.5V", 1, 0, 5),
            (10.0, 60.0, 20.0, 70.0, "Tj", 2, 0, 0),
            (22.0, 60.0, 25.0, 70.0, "=", 2, 0, 1),
            (27.0, 60.0, 49.0, 70.0, "150°C,", 2, 0, 2),
            (51.0, 60.0, 64.0, 70.0, "VDS", 2, 0, 3),
            (66.0, 60.0, 69.0, 70.0, "=", 2, 0, 4),
            (71.0, 60.0, 86.0, 70.0, "5V", 2, 0, 5),
        ]

        self.assertEqual(
            tc._positioned_temperature_labels(
                page,
                tc.CropTransform(0.0, 0.0, 1.0, 1.0),
                tc.PlotBox(0, 0, 100, 100),
            ),
            [],
        )

    def test_positioned_labels_reject_arbitrary_trailing_prose(self):
        page = MagicMock()
        page.get_text.return_value = [
            (10.0, 40.0, 20.0, 50.0, "Tj", 1, 0, 0),
            (22.0, 40.0, 25.0, 50.0, "=", 1, 0, 1),
            (27.0, 40.0, 45.0, 50.0, "25°C", 1, 0, 2),
            (47.0, 40.0, 70.0, 50.0, "typical", 1, 0, 3),
        ]

        self.assertEqual(
            tc._positioned_temperature_labels(
                page,
                tc.CropTransform(0.0, 0.0, 1.0, 1.0),
                tc.PlotBox(0, 0, 100, 100),
            ),
            [],
        )

    def test_recovers_known_coefficients_and_pivot(self):
        curves, anchor = _synthetic_curves()
        fit = tc.fit_saturation_tempco(curves, anchor)
        self.assertAlmostEqual(fit["d_vth_eff_v_per_k"], -3.0e-3, delta=2e-6)
        self.assertAlmostEqual(fit["d_log_k_per_k"], -2.0e-3, delta=2e-6)
        self.assertLess(fit["matched_shift_fit_rms_v"], 1e-4)
        self.assertFalse(fit["cold_anchor_conflict"])

    def test_three_curve_fit_uses_exact_reference_and_hot_branch(self):
        curves, anchor = _synthetic_curves()
        colder = tc.TransferCurve(
            -25.0, [(vgs + 0.2, current) for vgs, current in curves[0].points]
        )

        fit = tc.fit_saturation_tempco([colder, *curves], anchor)

        self.assertEqual(fit["tref_c"], 25.0)
        self.assertEqual(fit["thot_c"], 175.0)
        self.assertAlmostEqual(fit["d_vth_eff_v_per_k"], -3.0e-3, delta=2e-6)

    def test_fit_refuses_when_exact_reference_curve_is_missing(self):
        curves, anchor = _synthetic_curves()

        with self.assertRaisesRegex(RuntimeError, "matches anchor Tref"):
            tc.fit_saturation_tempco([curves[1]], anchor)

    def test_moved_plateau_anchor_refuses(self):
        curves, anchor = _synthetic_curves()
        anchor["id_gc_a"] = 80.0
        with self.assertRaisesRegex(RuntimeError, "does not reproduce"):
            tc.fit_saturation_tempco(curves, anchor)

    def test_missing_anchor_refuses(self):
        curves, anchor = _synthetic_curves()
        del anchor["p"]
        with self.assertRaisesRegex(RuntimeError, "missing"):
            tc.fit_saturation_tempco(curves, anchor)


@unittest.skipUnless((INFINEON / "IPP024N08NF2S.pdf").exists(), "local datasheet unavailable")
class InfineonTransferPilot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="transfer-test-"))
        panels = find_charts.process_pdf(INFINEON / "IPP024N08NF2S.pdf", cls.out, dpi=180)
        cls.chart = next(find_charts.asdict(p) for p in panels if p.kind == "transfer")
        cls.anchor = {
            "tref_c": 25.0,
            "vth_eff_v": 3.0793103448275865,
            "k_a_per_vp": 38.071525577184254,
            "p": 2.0,
            "id_gc_a": 100.0,
            "vpl_v": 4.7,
        }
        crop = cls.out / cls.chart["crop_png"]
        cls.result = tc.process_chart(
            cls.chart, crop, cls.out / "digitized", Path(cls.chart["crop_png"]).with_suffix(""), cls.anchor
        )

    def test_extracts_two_labeled_curves(self):
        self.assertEqual(self.result["temperatures_c"], [25.0, 175.0])
        self.assertGreater(min(self.result["n_points"].values()), 400)

    def test_fit_is_numerically_well_conditioned_but_unapproved(self):
        fit = self.result["fit"]
        self.assertLess(fit["matched_shift_fit_rms_v"], 0.03)
        self.assertLess(fit["d_vth_eff_v_per_k"], 0.0)
        self.assertLess(fit["d_log_k_per_k"], 0.0)
        self.assertIsNotNone(fit["ztc_chart_a"])
        self.assertAlmostEqual(fit["ztc_model_a"], fit["ztc_chart_a"], delta=0.10 * fit["ztc_chart_a"])
        self.assertEqual(self.result["status"], "overlay-review-required")
        self.assertIn("HUMAN REVIEW REQUIRED", self.result["warnings"][0])

    def test_review_artifacts_exist_and_json_is_strict(self):
        self.assertTrue(Path(self.result["csv"]).exists())
        self.assertTrue(Path(self.result["overlay"]).exists())
        json.loads(json.dumps(self.result, allow_nan=False))


@unittest.skipUnless(INFINEON_BSZ018.exists(), "local BSZ018 datasheet unavailable")
class InfineonBelowZtcTransferPilot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="transfer-bsz018-test-"))
        cls.chart = next(
            find_charts.asdict(panel)
            for panel in find_charts.process_pdf(INFINEON_BSZ018, cls.out, dpi=220)
            if panel.kind == "transfer" and panel.diagram == 7
        )
        cls.result = tc.process_chart(
            cls.chart,
            cls.out / cls.chart["crop_png"],
            cls.out / "digitized",
            Path(cls.chart["crop_png"]).with_suffix(""),
        )
        with Path(cls.result["csv"]).open() as handle:
            rows = list(csv.DictReader(handle))
        cls.curves = {
            temperature: [
                (float(row["Vgs_V"]), float(row["Id_A"]))
                for row in rows
                if float(row["Tj_C"]) == temperature
            ]
            for temperature in (25.0, 175.0)
        }

    def test_label_bound_below_ztc_curves_are_served_without_crossover_claim(self):
        self.assertEqual(self.result["temperatures_c"], [25.0, 175.0])
        self.assertEqual(self.result["status"], "overlay-review-required")
        self.assertIsNone(self.result["fit"])
        self.assertFalse(
            tc.validate_two_curve_order(
                self.curves[25.0], self.curves[175.0]
            )
        )

    def test_hot_curve_stays_source_resolved_to_the_left(self):
        currents = np.asarray((20.0, 40.0, 80.0, 120.0))
        cold = tc._inverse_vgs(self.curves[25.0], currents)
        hot = tc._inverse_vgs(self.curves[175.0], currents)

        self.assertTrue(np.all(hot < cold))
        self.assertEqual(self.result["n_points"], {"25C": 427, "175C": 423})
        self.assertLess(self.result["calibration"]["x_resid"], 0.5)
        self.assertLess(self.result["calibration"]["y_resid"], 0.3)


@unittest.skipUnless(
    INFINEON_IRF100PW219.exists(), "local IRF100PW219 datasheet unavailable"
)
class InfineonOuterLabelCrossingTransferPilot(unittest.TestCase):
    def test_opposite_outer_labels_recover_source_proven_ztc_crossing(self):
        out = Path(tempfile.mkdtemp(prefix="transfer-irf100pw219-test-"))
        chart = next(
            find_charts.asdict(panel)
            for panel in find_charts.process_pdf(INFINEON_IRF100PW219, out, dpi=220)
            if panel.kind == "transfer" and panel.diagram == 7
        )
        result = tc.process_chart(
            chart,
            out / chart["crop_png"],
            out / "digitized",
            Path(chart["crop_png"]).with_suffix(""),
        )

        self.assertEqual(result["status"], "overlay-review-required")
        self.assertEqual(result["temperatures_c"], [25.0, 175.0])
        self.assertEqual(result["n_points"], {"25C": 543, "175C": 563})
        self.assertLess(result["calibration"]["x_resid"], 0.001)
        self.assertLess(result["calibration"]["y_resid"], 0.001)
        self.assertIsNone(result["fit"])


@unittest.skipUnless(INFINEON_IRF6644.exists(), "local IRF6644 datasheet unavailable")
class InfineonTwoTemperatureLogAxisPilot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="transfer-irf6644-test-"))
        panels = find_charts.process_pdf(INFINEON_IRF6644, cls.out, dpi=220)
        cls.chart = next(
            find_charts.asdict(panel)
            for panel in panels
            if panel.kind == "transfer" and panel.diagram == 5
        )
        cls.result = tc.process_chart(
            cls.chart,
            cls.out / cls.chart["crop_png"],
            cls.out / "digitized",
            Path(cls.chart["crop_png"]).with_suffix(""),
        )
        with Path(cls.result["csv"]).open() as handle:
            cls.rows = list(csv.DictReader(handle))

    def test_two_curve_transfer_uses_log_capable_id_calibration(self):
        self.assertEqual(self.result["temperatures_c"], [25.0, 150.0])
        self.assertEqual(self.result["n_points"], {"25C": 292, "150C": 362})
        self.assertEqual(self.result["calibration"]["y_ticks"], 4)
        self.assertLess(self.result["calibration"]["y_resid"], 1.0)
        self.assertEqual(self.result["status"], "overlay-review-required")
        self.assertGreater(min(self.result["n_points"].values()), 250)
        currents = [float(row["Id_A"]) for row in self.rows]
        self.assertLess(min(currents), 2.0)
        self.assertGreater(max(currents), 100.0)
        self.assertGreater(max(currents) / min(currents), 100.0)

    def test_source_labels_bind_hot_left_and_cold_right(self):
        curves = {
            temperature: sorted(
                (
                    (float(row["Id_A"]), float(row["Vgs_V"]))
                    for row in self.rows
                    if float(row["Tj_C"]) == temperature
                )
            )
            for temperature in (25.0, 150.0)
        }
        for current, hot_vgs, cold_vgs in (
            (10.0, 4.40019, 5.04308),
            (100.0, 6.06659, 6.32638),
        ):
            at_current = {
                temperature: float(
                    np.interp(
                        current,
                        [point[0] for point in points],
                        [point[1] for point in points],
                    )
                )
                for temperature, points in curves.items()
            }
            self.assertAlmostEqual(at_current[150.0], hot_vgs, delta=0.02)
            self.assertAlmostEqual(at_current[25.0], cold_vgs, delta=0.02)
            self.assertLess(at_current[150.0], at_current[25.0])


@unittest.skipUnless(RENESAS_RJK.exists(), "local Renesas datasheet unavailable")
class RenesasThreeTemperaturePilot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="transfer-rjk-test-"))
        panels = find_charts.process_pdf(RENESAS_RJK, cls.out, dpi=220)
        cls.chart = next(find_charts.asdict(panel) for panel in panels if panel.kind == "transfer")
        cls.result = tc.process_chart(
            cls.chart,
            cls.out / cls.chart["crop_png"],
            cls.out / "digitized",
            Path(cls.chart["crop_png"]).with_suffix(""),
        )

    def test_crop_owns_only_the_right_column_frame(self):
        self.assertGreater(self.chart["bbox_pt"][0], 300.0)
        self.assertLess(self.chart["bbox_pt"][2], 540.0)

    def test_extracts_all_three_temperature_branches(self):
        self.assertEqual(self.result["temperatures_c"], [-25.0, 25.0, 75.0])
        self.assertGreaterEqual(min(self.result["n_points"].values()), 80)
        self.assertEqual(self.result["status"], "overlay-review-required")


@unittest.skipUnless(HXY_SPD.exists(), "local HXY datasheet unavailable")
class HxyThreeTemperaturePilot(unittest.TestCase):
    def test_keeps_lower_saturation_current_branches(self):
        with tempfile.TemporaryDirectory(prefix="transfer-hxy-test-") as tmp:
            out = Path(tmp)
            panels = find_charts.process_pdf(HXY_SPD, out, dpi=220)
            chart = next(find_charts.asdict(panel) for panel in panels if panel.kind == "transfer")
            result = tc.process_chart(
                chart,
                out / chart["crop_png"],
                out / "digitized",
                Path(chart["crop_png"]).with_suffix(""),
            )
            rows = list(csv.DictReader(Path(result["csv"]).open()))

        self.assertEqual(result["temperatures_c"], [-55.0, 25.0, 125.0])
        self.assertGreaterEqual(min(result["n_points"].values()), 45)
        self.assertEqual(result["status"], "overlay-review-required")
        for temperature in ("-55.00", "25.00", "125.00"):
            branch = [row for row in rows if row["Tj_C"] == temperature]
            currents = [float(row["Id_A"]) for row in branch]
            gates = [float(row["Vgs_V"]) for row in branch]
            self.assertTrue(
                all(right >= left for left, right in zip(currents, currents[1:]))
            )
            self.assertLess(
                max(right - left for left, right in zip(currents, currents[1:])),
                0.05,
            )
            self.assertGreaterEqual(max(gates), 7.9)
            if temperature == "-55.00":
                self.assertTrue(
                    any(right < left for left, right in zip(gates, gates[1:]))
                )


@unittest.skipUnless(ONSEMI_NVB.exists(), "local onsemi datasheet unavailable")
class OnsemiLogCurrentTransferPilot(unittest.TestCase):
    def test_sparse_closed_frame_and_log_current_axis_are_source_bound(self):
        with tempfile.TemporaryDirectory(prefix="transfer-onsemi-log-test-") as tmp:
            out = Path(tmp)
            panels = find_charts.process_pdf(ONSEMI_NVB, out, dpi=220)
            chart = next(
                find_charts.asdict(panel)
                for panel in panels
                if panel.kind == "transfer" and panel.diagram == 2
            )
            result = tc.process_chart(
                chart,
                out / chart["crop_png"],
                out / "digitized",
                Path(chart["crop_png"]).with_suffix(""),
            )
            rows = list(csv.DictReader(Path(result["csv"]).open()))

        self.assertEqual(result["temperatures_c"], [-55.0, 25.0, 150.0])
        self.assertEqual(result["status"], "overlay-review-required")
        self.assertEqual(result["calibration"]["y_ticks"], 4)
        self.assertLess(result["calibration"]["y_resid"], 1.5)
        currents = [float(row["Id_A"]) for row in rows]
        self.assertGreater(max(currents), 70.0)
        self.assertGreater(min(currents), 0.0)


@unittest.skipUnless(TI_CSD19537.exists(), "local TI datasheet unavailable")
class TiConvergingThreePathTransferPilot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="transfer-ti-csd19537-test-"))
        panels = find_charts.process_pdf(TI_CSD19537, cls.out, dpi=220)
        cls.chart = next(
            find_charts.asdict(panel)
            for panel in panels
            if panel.kind == "transfer" and panel.diagram == 53
        )
        cls.result = tc.process_chart(
            cls.chart,
            cls.out / cls.chart["crop_png"],
            cls.out / "digitized",
            Path(cls.chart["crop_png"]).with_suffix(""),
        )
        cls.rows = list(csv.DictReader(Path(cls.result["csv"]).open()))

    def test_recovers_exactly_three_source_paths(self):
        self.assertEqual(self.result["temperatures_c"], [-55.0, 25.0, 125.0])
        counts = list(self.result["n_points"].values())
        self.assertEqual(len(counts), 3)
        self.assertEqual(len(set(counts)), 1)
        self.assertGreaterEqual(counts[0], 300)
        self.assertEqual(self.result["status"], "overlay-review-required")

    def test_axis_scale_and_branch_order_are_source_bound(self):
        calibration = self.result["calibration"]
        self.assertEqual(calibration["x_ticks"], 6)
        self.assertEqual(calibration["y_ticks"], 9)
        self.assertLess(calibration["x_resid"], 1.0)
        self.assertLess(calibration["y_resid"], 1.0)
        first_gate = {
            temperature: float(next(
                row["Vgs_V"]
                for row in self.rows
                if row["Tj_C"] == temperature
            ))
            for temperature in ("-55.00", "25.00", "125.00")
        }
        self.assertGreater(first_gate["-55.00"], first_gate["25.00"])
        self.assertGreater(first_gate["25.00"], first_gate["125.00"])


class TiGrayTemperaturePathTransferPilot(unittest.TestCase):
    def test_gray_cold_branch_requires_exact_panel_local_width_match(self):
        for pdf, expected_points in zip(TI_GRAY_TRANSFER_CASES, (378, 405)):
            if not pdf.exists():
                self.skipTest(f"missing local TI datasheet: {pdf}")
            with self.subTest(pdf=pdf.name), tempfile.TemporaryDirectory(
                prefix="transfer-ti-gray-test-"
            ) as tmp:
                out = Path(tmp)
                chart = next(
                    find_charts.asdict(panel)
                    for panel in find_charts.process_pdf(pdf, out, dpi=220)
                    if panel.kind == "transfer" and panel.diagram == 3
                )
                result = tc.process_chart(
                    chart,
                    out / chart["crop_png"],
                    out / "digitized",
                    Path(chart["crop_png"]).with_suffix(""),
                )

                self.assertEqual(result["temperatures_c"], [-55.0, 25.0, 125.0])
                self.assertEqual(
                    tuple(result["n_points"].values()),
                    (expected_points, expected_points, expected_points),
                )
                self.assertEqual(result["status"], "overlay-review-required")


@unittest.skipUnless(ONSEMI_FDB035.exists(), "local FDB035N10A datasheet unavailable")
class OnsemiThinTransferGridCalibrationPilot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="transfer-fdb035-grid-test-"))
        cls.chart = next(
            find_charts.asdict(panel)
            for panel in find_charts.process_pdf(ONSEMI_FDB035, cls.out, dpi=220)
            if panel.kind == "transfer" and panel.diagram == 2
        )
        crop = cls.out / cls.chart["crop_png"]
        image = cv2.imread(str(crop))
        cls.gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        cls.transform = tc.CropTransform.for_chart(cls.chart, cls.gray.shape)
        with pymupdf.open(ONSEMI_FDB035) as document:
            page = document[cls.chart["page"] - 1]
            words = tc._words_in_crop_px(page, cls.transform, cls.gray.shape)
            cls.plot, cls.x_axis, cls.y_axis, cls.pixel_curves = tc._extract_panel_curves(
                page, cls.transform, cls.gray, words, pymupdf, 3
            )

    def test_consumed_ticks_are_seated_on_source_grid_centers(self):
        self.assertEqual(
            tuple(tick.pixel for tick in self.x_axis.ticks),
            (131.0, 252.0, 373.0, 494.0, 615.0),
        )
        self.assertEqual(
            tuple(tick.pixel for tick in self.y_axis.ticks),
            (33.0, 118.0, 295.0, 473.0),
        )
        self.assertLess(self.x_axis.residual_px, 1e-6)
        self.assertLess(self.y_axis.residual_px, 0.25)

    def test_grid_calibration_does_not_change_thin_source_curve_count(self):
        self.assertEqual(len(self.pixel_curves), 3)
        self.assertEqual(
            tuple(sorted(len(curve) for curve in self.pixel_curves)),
            (435, 437, 441),
        )


if __name__ == "__main__":
    unittest.main()
