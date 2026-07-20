import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from datasheet_chart_digitizer import capacitance_traces as ct
from datasheet_chart_digitizer import capacitance_vector as cv
from datasheet_chart_digitizer import mosfet_capacitance as mc
from datasheet_chart_digitizer.capacitance_plot_box import find_capacitance_plot_box


class AxisCalibrationTests(unittest.TestCase):
    def test_position_axis_refuses_multiple_unseen_endpoint_intervals(self) -> None:
        plot = mc.PlotBox(40, 34, 722, 510)
        calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=5.0,
            y_min_decade=1.0,
            y_max_decade=2.0,
            source="position_ocr",
            x_ticks_v=(0.0, 5.0),
            y_decades=(1.0, 2.0),
            x_resid_v=0.0,
            y_resid_dec=0.0,
            x_scale=0.059224119937113025,
            x_offset=-2.3906803897356426,
            y_scale=-0.0021008403361344537,
            y_offset=2.0714285714285716,
        )

        rejection = mc.reject_bad_position_calibration(calibration, plot)

        self.assertIn("X axis right endpoint", rejection or "")
        self.assertIn("unlabeled tick intervals", rejection or "")

    def test_position_axis_allows_one_unlabeled_endpoint_interval(self) -> None:
        plot = mc.PlotBox(40, 34, 440, 434)
        calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=30.0,
            y_min_decade=1.0,
            y_max_decade=3.0,
            source="position_text",
            x_ticks_v=(0.0, 10.0, 20.0, 30.0),
            y_decades=(1.0, 2.0, 3.0),
            x_resid_v=0.0,
            y_resid_dec=0.0,
            x_scale=0.1,
            x_offset=-4.0,
            y_scale=-0.01,
            y_offset=4.34,
        )

        self.assertIsNone(mc.reject_bad_position_calibration(calibration, plot))

    def test_vector_pixels_without_axis_are_review_only_not_physical(self) -> None:
        status, reasons = mc._capacitance_status(
            False, "vector", {"status": "pass", "reasons": []}
        )

        self.assertEqual(status, "overlay-review-required")
        self.assertEqual(
            reasons,
            [
                "axis_calibration_untrusted",
                "pixel_overlay_only_physical_axis_unavailable",
            ],
        )
        self.assertEqual(
            mc._capacitance_status(
                False, "raster", {"status": "pass", "reasons": []}
            )[0],
            "unverified",
        )

    def test_vector_physics_conflict_is_explicit_review_only(self) -> None:
        status, reasons = mc._capacitance_status(
            True,
            "vector",
            {
                "status": "suspect",
                "reasons": ["Crss_rises_with_vds_unphysical"],
            },
        )

        self.assertEqual(status, "overlay-review-required")
        self.assertEqual(
            reasons,
            [
                "Crss_rises_with_vds_unphysical",
                "source_faithful_vector_physics_conflict_review_only",
            ],
        )

    def test_sparse_closed_log_frame_is_recovered_for_capacitance(self) -> None:
        gray = np.full((500, 700), 255, dtype=np.uint8)
        # Only the frame and two decade rails are solid. The right frame sits
        # inside the shared detector's crop-edge exclusion; dotted minor grids
        # would not survive its vertical morphology either.
        cv2.rectangle(gray, (180, 55), (680, 420), 0, 2)
        cv2.line(gray, (350, 55), (350, 420), 0, 1)
        cv2.line(gray, (520, 140), (520, 420), 0, 1)
        cv2.line(gray, (180, 175), (680, 175), 0, 1)
        cv2.line(gray, (180, 300), (680, 300), 0, 1)

        with self.assertRaisesRegex(RuntimeError, "found 3"):
            ct.find_plot_box(gray)
        self.assertEqual(
            find_capacitance_plot_box(gray),
            mc.PlotBox(180, 55, 680, 420),
        )

    def test_closed_right_frame_extends_a_successful_inner_grid_box(self) -> None:
        gray = np.full((500, 700), 255, dtype=np.uint8)
        cv2.rectangle(gray, (120, 55), (680, 420), 0, 2)
        for x in (220, 300, 380, 460, 540, 620):
            cv2.line(gray, (x, 55), (x, 420), 0, 1)
        for y in (175, 300):
            cv2.line(gray, (120, y), (680, y), 0, 1)

        self.assertEqual(ct.find_plot_box(gray), mc.PlotBox(120, 55, 620, 422))
        self.assertEqual(
            find_capacitance_plot_box(gray), mc.PlotBox(120, 55, 680, 420)
        )

    def test_sparse_frame_ignores_foreign_neighbor_rail(self) -> None:
        gray = np.full((500, 700), 255, dtype=np.uint8)
        cv2.rectangle(gray, (125, 35), (680, 420), 0, 1)
        for x in (265, 405, 545):
            cv2.line(gray, (x, 35), (x, 420), 0, 1)
        for y in (130, 225, 320):
            cv2.line(gray, (125, y), (680, y), 0, 1)
        # A rail from the neighboring panel survives the crop, but no owned
        # top/bottom horizontal closes against it.
        cv2.line(gray, (25, 35), (25, 420), 0, 1)

        with self.assertRaisesRegex(RuntimeError, "found 4"):
            ct.find_plot_box(gray)
        self.assertEqual(
            find_capacitance_plot_box(gray),
            mc.PlotBox(125, 35, 680, 420),
        )

    def test_sparse_frame_recovery_refuses_missing_bottom_closure(self) -> None:
        gray = np.full((500, 700), 255, dtype=np.uint8)
        cv2.line(gray, (180, 55), (680, 55), 0, 2)
        cv2.line(gray, (180, 55), (180, 420), 0, 2)
        cv2.line(gray, (680, 55), (680, 420), 0, 2)
        for x in (350, 520):
            cv2.line(gray, (x, 55), (x, 420), 0, 1)
        for y in (175, 300):
            cv2.line(gray, (180, y), (680, y), 0, 1)

        with self.assertRaisesRegex(RuntimeError, "plot grid verticals"):
            find_capacitance_plot_box(gray)

    def test_closed_frame_without_solid_inner_grid_is_recovered(self) -> None:
        gray = np.full((500, 700), 255, dtype=np.uint8)
        cv2.rectangle(gray, (180, 55), (680, 420), 0, 2)
        # A long trace is not a grid rail, but cannot confuse the four closing
        # frame sides because it does not terminate at the side rails.
        cv2.line(gray, (230, 180), (620, 180), 0, 3)

        with self.assertRaisesRegex(RuntimeError, "found 1"):
            ct.find_plot_box(gray)
        self.assertEqual(
            find_capacitance_plot_box(gray),
            mc.PlotBox(180, 55, 680, 420),
        )

    def test_sparse_grid_raster_tracker_does_not_serve_frame_rails(self) -> None:
        gray = np.full((240, 360), 255, dtype=np.uint8)
        plot = mc.PlotBox(30, 20, 330, 220)
        cv2.rectangle(gray, (plot.x0, plot.y0), (plot.x1, plot.y1), 0, 2)
        for y in (65, 120, 175):
            cv2.line(gray, (plot.x0 + 2, y), (plot.x1 - 2, y + 18), 0, 3)

        traces = mc.extract_trace_components(gray, plot)

        for trace in traces:
            ys = [point[1] for point in trace.points]
            self.assertGreater(min(ys), plot.y0 + 3)
            self.assertLess(max(ys), plot.y1 - 3)

    def test_sparse_frame_recovery_refuses_outer_crop_border(self) -> None:
        gray = np.full((500, 700), 255, dtype=np.uint8)
        cv2.rectangle(gray, (0, 30), (699, 470), 0, 2)
        for x in (200, 400):
            cv2.line(gray, (x, 30), (x, 470), 0, 1)
        for y in (175, 320):
            cv2.line(gray, (0, y), (699, y), 0, 1)

        with self.assertRaisesRegex(RuntimeError, "plot grid verticals"):
            find_capacitance_plot_box(gray)

    def test_major_gridline_fit_prefers_full_axis_span(self) -> None:
        image = np.full((480, 520, 3), 255, dtype=np.uint8)
        plot = mc.PlotBox(x0=40, y0=30, x1=439, y1=430)

        # A real 5-decade major grid spanning the whole plot.
        for y in [30, 130, 230, 330, 430]:
            cv2.line(image, (plot.x0, y), (plot.x1, y), (40, 40, 40), 2)

        # A tempting, very uniform internal sequence. It must lose because it
        # does not cover the whole log axis.
        for y in [50, 145, 240, 335, 430]:
            cv2.line(image, (plot.x0 + 5, y), (plot.x1 - 5, y), (30, 30, 30), 1)

        fit = mc._major_horizontal_gridline_fit(image, plot, 5)

        self.assertGreaterEqual(fit.candidate_count, 5)
        self.assertGreater(fit.span_fraction, 0.99)
        self.assertLess(fit.residual_px, 1.0)
        self.assertEqual([round(y) for y in fit.centers], [30, 130, 230, 330, 430])

    def test_major_gridline_fit_skips_wider_bad_residual_candidate(self) -> None:
        image = np.full((480, 520, 3), 255, dtype=np.uint8)
        plot = mc.PlotBox(x0=40, y0=30, x1=439, y1=430)

        for y in [45, 141, 238, 334, 430]:
            cv2.line(image, (plot.x0, y), (plot.x1, y), (40, 40, 40), 2)
        cv2.line(image, (plot.x0, 29), (plot.x1, 29), (35, 35, 35), 2)

        fit = mc._major_horizontal_gridline_fit(image, plot, 5)

        self.assertLess(fit.residual_px, 1.0)
        expected = [45, 141, 238, 334, 430]
        self.assertTrue(all(abs(actual - want) <= 1.0 for actual, want in zip(fit.centers, expected)))

    def test_axis_json_exposes_mapping_and_gridline_provenance(self) -> None:
        calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=100.0,
            y_min_decade=0.0,
            y_max_decade=4.0,
            source="grid_text",
            x_ticks_v=(0.0, 20.0, 40.0, 60.0, 80.0, 100.0),
            y_decades=(0.0, 1.0, 2.0, 3.0, 4.0),
            x_scale=0.25,
            x_offset=-10.0,
            y_scale=-0.01,
            y_offset=5.0,
            x_source="plot_box_endpoints_from_text_ticks",
            y_source="gridline_fit_from_text_decades",
            y_gridline_px=(30.0, 130.0, 230.0, 330.0, 430.0),
            y_grid_candidate_count=21,
            y_grid_span_fraction=1.0,
            y_grid_residual_px=0.2,
        )

        payload = mc.axis_calibration_to_json(calibration)

        self.assertEqual(payload["x_source"], "plot_box_endpoints_from_text_ticks")
        self.assertEqual(payload["y_source"], "gridline_fit_from_text_decades")
        self.assertEqual(payload["x_scale"], 0.25)
        self.assertEqual(payload["y_offset"], 5.0)
        self.assertEqual(payload["y_gridline_px"], [30.0, 130.0, 230.0, 330.0, 430.0])
        self.assertEqual(payload["y_grid_candidate_count"], 21)

    def test_trace_overlay_crosshairs_use_exact_consumed_tick_centers(self) -> None:
        image = np.full((110, 110, 3), 255, dtype=np.uint8)
        plot = mc.PlotBox(10, 10, 90, 90)
        calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=10.0,
            y_min_decade=0.0,
            y_max_decade=2.0,
            source="fixture",
            x_ticks_v=(0.0, 10.0),
            y_decades=(0.0, 2.0),
        )
        centers = []
        labels = []

        with (
            mock.patch.object(
                cv2,
                "drawMarker",
                side_effect=lambda _image, center, *_args, **_kwargs: centers.append(
                    center
                ),
            ),
            mock.patch.object(
                cv2,
                "putText",
                side_effect=lambda _image, text, *_args, **_kwargs: labels.append(text),
            ),
        ):
            mc.draw_trace_overlay(image, plot, [], calibration)

        self.assertEqual(centers, [(10, 90), (90, 90), (10, 90), (10, 10)])
        self.assertEqual(labels, ["0V", "10V", "1pF", "100pF"])

    def test_pchannel_overlay_labels_source_ticks_and_declares_magnitude_serving(self) -> None:
        image = np.full((110, 110, 3), 255, dtype=np.uint8)
        plot = mc.PlotBox(10, 10, 90, 90)
        calibration = mc.AxisCalibration(
            x_min_v=0.1,
            x_max_v=100.0,
            y_min_decade=2.0,
            y_max_decade=4.0,
            source="fixture",
            x_ticks_v=(0.1, 1.0, 10.0, 100.0),
            y_decades=(2.0, 4.0),
            x_log=True,
            x_source_ticks_v=(-0.1, -1.0, -10.0, -100.0),
            x_value_transform="abs_source_negative_vds",
        )
        labels = []

        with mock.patch.object(
            cv2,
            "putText",
            side_effect=lambda _image, text, *_args, **_kwargs: labels.append(text),
        ):
            mc.draw_trace_overlay(image, plot, [], calibration)

        self.assertIn("-0.1V", labels)
        self.assertIn("-100V", labels)
        self.assertIn("SOURCE X: negative VDS; served X: |VDS|", labels)
        payload = mc.axis_calibration_to_json(calibration)
        self.assertEqual(payload["x_source_ticks_v"], [-0.1, -1.0, -10.0, -100.0])
        self.assertEqual(payload["x_value_transform"], "abs_source_negative_vds")

    def test_sustained_ciss_coss_merge_is_flagged_but_crossing_is_not(self) -> None:
        plot = mc.PlotBox(0, 0, 199, 199)
        ciss = [(x, 80) for x in range(200)]
        prefix_shared_coss = [(x, 81 if x < 80 else 120) for x in range(200)]
        crossing_coss = [
            (x, 80 + int(round(0.15 * (x - 100)))) for x in range(200)
        ]

        sustained = mc.ciss_coss_shared_spans(
            [
                mc.Trace("Ciss", 200, (0, 0, 200, 1), ciss),
                mc.Trace("Coss", 200, (0, 0, 200, 40), prefix_shared_coss),
            ],
            plot,
        )
        crossing = mc.ciss_coss_shared_spans(
            [
                mc.Trace("Ciss", 200, (0, 0, 200, 1), ciss),
                mc.Trace("Coss", 200, (0, 0, 200, 30), crossing_coss),
            ],
            plot,
        )

        self.assertEqual(len(sustained), 1)
        self.assertEqual((sustained[0]["x0_px"], sustained[0]["x1_px"]), (0, 79))
        self.assertEqual(crossing, [])

    def test_shared_merge_detection_is_scale_invariant(self) -> None:
        def detected(scale: int) -> list[dict[str, object]]:
            width = 200 * scale
            shared_end = 80 * scale
            ciss = [(x, 80 * scale) for x in range(width)]
            coss = [
                (x, 81 * scale if x < shared_end else 120 * scale)
                for x in range(width)
            ]
            return mc.ciss_coss_shared_spans(
                [
                    mc.Trace("Ciss", width, (0, 0, width, 1), ciss),
                    mc.Trace("Coss", width, (0, 0, width, 40 * scale), coss),
                ],
                mc.PlotBox(0, 0, width - 1, width - 1),
            )

        one_x = detected(1)
        two_x = detected(2)

        self.assertEqual(len(one_x), 1)
        self.assertEqual(len(two_x), 1)
        self.assertAlmostEqual(
            float(one_x[0]["span_fraction"]),
            float(two_x[0]["span_fraction"]),
            places=2,
        )

    def test_merged_tail_flatness_guard_repairs_identity_swap(self) -> None:
        plot = mc.PlotBox(0, 0, 199, 199)
        wrong_ciss = [
            (x, 40 + int(round(39 * x / 169))) if x < 170 else (x, 79)
            for x in range(200)
        ]
        wrong_coss = [
            (x, 72 + int(round(7 * x / 169))) if x < 170 else (x, 79)
            for x in range(200)
        ]
        crss = [(x, 140) for x in range(200)]
        traces = [
            mc.Trace("Ciss", 200, (0, 40, 200, 40), wrong_ciss),
            mc.Trace("Coss", 200, (0, 72, 200, 8), wrong_coss),
            mc.Trace("Crss", 200, (0, 140, 200, 1), crss),
        ]

        raw_summary = mc.trace_validation_summary(
            mc.trace_semantic_diagnostics(traces, plot)
        )
        repaired, identity = mc.repair_merged_ciss_coss_identity(traces, plot)
        repaired_checks = mc.trace_semantic_diagnostics(repaired, plot)["checks"]

        self.assertIn("ciss_not_flatter_than_coss", raw_summary["reasons"])
        self.assertTrue(identity["changed"])
        self.assertEqual(
            identity["reason"], "flatness_guard_repaired_merged_tail_swap"
        )
        self.assertTrue(repaired_checks["ciss_flatter_than_coss"])
        self.assertEqual(len(mc.ciss_coss_shared_spans(repaired, plot)), 1)

    def test_flatness_inversion_without_shared_tail_fails_closed(self) -> None:
        plot = mc.PlotBox(0, 0, 199, 199)
        traces = [
            mc.Trace("Ciss", 200, (0, 40, 200, 80), [(x, 40 + x // 3) for x in range(200)]),
            mc.Trace("Coss", 200, (0, 130, 200, 1), [(x, 130) for x in range(200)]),
        ]

        repaired, identity = mc.repair_merged_ciss_coss_identity(traces, plot)

        self.assertEqual(repaired, traces)
        self.assertFalse(identity["changed"])
        self.assertEqual(identity["reason"], "no_shared_tail_or_single_crossing")

    def test_process_chart_writes_axis_debug_overlay_when_requested(self) -> None:
        image = np.full((80, 100, 3), 255, dtype=np.uint8)
        crop_rel = Path("crops") / "P" / "chart.png"
        traces = [
            mc.Trace(name="Ciss", area=2, bbox=(0, 0, 2, 2), points=[(10, 12), (20, 12)]),
            mc.Trace(name="Coss", area=2, bbox=(0, 0, 2, 2), points=[(10, 22), (20, 24)]),
            mc.Trace(name="Crss", area=2, bbox=(0, 0, 2, 2), points=[(10, 35), (20, 36)]),
        ]
        calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=10.0,
            y_min_decade=0.0,
            y_max_decade=1.0,
            source="grid_text",
            x_ticks_v=(0.0, 10.0),
            y_decades=(0.0, 1.0),
            x_scale=1.0,
            x_offset=0.0,
            y_scale=-0.1,
            y_offset=2.0,
            x_source="plot_box_endpoints_from_text_ticks",
            y_source="gridline_fit_from_text_decades",
            y_gridline_px=(10.0, 20.0),
            y_grid_candidate_count=2,
            y_grid_span_fraction=1.0,
            y_grid_residual_px=0.0,
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop_path = root / crop_rel
            crop_path.parent.mkdir(parents=True)
            cv2.imwrite(str(crop_path), image)
            with mock.patch.object(mc, "find_capacitance_plot_box", return_value=mc.PlotBox(10, 10, 50, 50)), \
                mock.patch.object(mc, "parse_capacitance_anchors", return_value={}), \
                mock.patch.object(mc, "parse_output_charge_reference", return_value=mc.OutputChargeReference(None, None, None, None)), \
                mock.patch.object(mc, "infer_text_order_axis_calibration", return_value=calibration), \
                mock.patch.object(mc, "infer_position_axis_calibration", return_value=calibration), \
                mock.patch.object(
                    mc,
                    "extract_vector_trace_components_with_provenance",
                    return_value=(traces, "pooled_components"),
                ):
                result = mc.process_chart(
                    {"part": "P", "diagram": 1, "pdf": "p.pdf", "page": 1},
                    crop_path,
                    root / "out",
                    crop_rel.with_suffix(""),
                    root,
                    debug_axis_overlays=True,
                )

            self.assertIsNotNone(result["axis_debug_overlay"])
            self.assertTrue((root / "out" / str(result["axis_debug_overlay"])).exists())

    def test_chart_text_fallback_is_not_trusted_for_physical_output(self) -> None:
        image = np.full((80, 100, 3), 255, dtype=np.uint8)
        crop_rel = Path("crops") / "P" / "chart.png"
        traces = [
            mc.Trace(name="Ciss", area=2, bbox=(0, 0, 2, 2), points=[(10, 12), (20, 12)]),
            mc.Trace(name="Coss", area=2, bbox=(0, 0, 2, 2), points=[(10, 22), (20, 24)]),
            mc.Trace(name="Crss", area=2, bbox=(0, 0, 2, 2), points=[(10, 35), (20, 36)]),
        ]
        calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=10.0,
            y_min_decade=0.0,
            y_max_decade=1.0,
            source="chart_text",
            x_ticks_v=(0.0, 10.0),
            y_decades=(0.0, 1.0),
            x_source="text_order_normalized_plot_extent",
            y_source="text_order_normalized_plot_extent",
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop_path = root / crop_rel
            crop_path.parent.mkdir(parents=True)
            cv2.imwrite(str(crop_path), image)
            with mock.patch.object(mc, "find_capacitance_plot_box", return_value=mc.PlotBox(10, 10, 50, 50)), \
                mock.patch.object(mc, "parse_capacitance_anchors", return_value={}), \
                mock.patch.object(
                    mc,
                    "parse_output_charge_reference",
                    return_value=mc.OutputChargeReference(1000.0, 10.0, None, None),
                ), \
                mock.patch.object(mc, "infer_text_order_axis_calibration", return_value=calibration), \
                mock.patch.object(mc, "infer_position_axis_calibration", side_effect=RuntimeError("no positions")), \
                mock.patch.object(mc, "infer_gridline_axis_calibration", side_effect=RuntimeError("no grid")), \
                mock.patch.object(
                    mc,
                    "extract_vector_trace_components_with_provenance",
                    return_value=(traces, "pooled_components"),
                ):
                result = mc.process_chart(
                    {"part": "P", "diagram": 1, "pdf": "p.pdf", "page": 1},
                    crop_path,
                    root / "out",
                    crop_rel.with_suffix(""),
                    root,
                )

            self.assertFalse(result["axis_calibration_trusted"])
            self.assertEqual("unverified", result["status"])
            self.assertEqual("axis_calibration_untrusted", result["status_reasons"][0])
            self.assertFalse(result["physical_output_available"])
            self.assertEqual(
                {"qoss_pc": None, "vint_v": None, "coer_pf": None, "cotr_pf": None},
                result["output_charge_reference"],
            )
            self.assertIn("untrusted text-order axis fallback", result["axis_warning"])
            self.assertEqual(result["qoss_validation_error"], "untrusted axis calibration")
            rows = (root / "out" / str(result["points"])).read_text().splitlines()
            self.assertTrue(rows[1].endswith(",,false"))

    def test_review_only_chart_blanks_physical_points_and_demotes_qoss(self) -> None:
        image = np.full((80, 100, 3), 255, dtype=np.uint8)
        crop_rel = Path("crops") / "P" / "chart.png"
        traces = [
            mc.Trace(name="Ciss", area=2, bbox=(0, 0, 2, 2), points=[(10, 12), (20, 12)]),
            mc.Trace(name="Coss", area=2, bbox=(0, 0, 2, 2), points=[(10, 22), (20, 24)]),
            mc.Trace(name="Crss", area=2, bbox=(0, 0, 2, 2), points=[(10, 35), (20, 30)]),
        ]
        calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=100.0,
            y_min_decade=0.0,
            y_max_decade=3.0,
            source="grid_text",
            x_ticks_v=(0.0, 100.0),
            y_decades=(0.0, 1.0, 2.0, 3.0),
            x_scale=2.5,
            x_offset=-25.0,
            y_scale=-0.075,
            y_offset=3.75,
            x_source="plot_box_endpoints_from_text_ticks",
            y_source="gridline_fit_from_text_decades",
            y_gridline_px=(10.0, 20.0, 30.0, 40.0),
            y_grid_candidate_count=4,
            y_grid_span_fraction=1.0,
            y_grid_residual_px=0.0,
        )
        metrics = SimpleNamespace(
            Qoss=1000.0,
            Eoss=2000.0,
            Co_tr=100.0,
            Co_er=200.0,
            Qoss_below_first=10.0,
            Qoss_chart_range=900.0,
            Qoss_above_last=90.0,
            Eoss_below_first=20.0,
            Eoss_chart_range=1800.0,
            Eoss_above_last=180.0,
            C0=1000.0,
            phi=1.0,
            m=0.5,
            first_vds=1.0,
            first_coss=500.0,
            splice_rel_error=0.01,
            extrapolated_qoss_fraction=0.05,
            clipped_completion_active=False,
            clip_boundary_vds=None,
            Qoss_clip_completed=0.0,
            Qoss_clip_visible_floor=0.0,
            Qoss_clip_added=0.0,
            clipped_completion_fraction=0.0,
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop_path = root / crop_rel
            crop_path.parent.mkdir(parents=True)
            cv2.imwrite(str(crop_path), image)
            with mock.patch.object(mc, "find_capacitance_plot_box", return_value=mc.PlotBox(10, 10, 50, 50)), \
                mock.patch.object(mc, "parse_capacitance_anchors", return_value={}), \
                mock.patch.object(
                    mc,
                    "parse_output_charge_reference",
                    return_value=mc.OutputChargeReference(None, 100.0, None, None),
                ), \
                mock.patch.object(mc, "infer_text_order_axis_calibration", return_value=calibration), \
                mock.patch.object(mc, "infer_position_axis_calibration", return_value=calibration), \
                mock.patch.object(
                    mc,
                    "extract_vector_trace_components_with_provenance",
                    return_value=(traces, "pooled_components"),
                ), \
                mock.patch.object(
                    mc,
                    "trace_validation_summary",
                    return_value={
                        "status": "suspect",
                        "reasons": ["Crss_rises_with_vds_unphysical"],
                    },
                ), \
                mock.patch.object(mc, "coss_metrics", return_value=metrics), \
                mock.patch.object(mc, "validate_axis", return_value=None):
                result = mc.process_chart(
                    {"part": "P", "diagram": 1, "pdf": "p.pdf", "page": 1},
                    crop_path,
                    root / "out",
                    crop_rel.with_suffix(""),
                    root,
                )

            self.assertEqual("overlay-review-required", result["status"])
            self.assertFalse(result["physical_output_available"])
            self.assertFalse(result["points_physical_output_available"])
            self.assertIsNone(result["qoss_metrics"])
            self.assertIsNotNone(result["qoss_diagnostic_metrics"])
            self.assertFalse(result["qoss_metrics_physical_output_available"])
            self.assertEqual("reference_unavailable", result["qoss_validation_status"])
            self.assertIn(
                "chart_physical_output_unavailable",
                result["qoss_metrics_status_reasons"],
            )
            rows = (root / "out" / str(result["points"])).read_text().splitlines()
            self.assertTrue(all(row.split(",")[5:7] == ["", ""] for row in rows[1:]))

    def test_active_axis_fit_error_overrides_structurally_trusted_calibration(self) -> None:
        calibration = mc.AxisCalibration(
            x_min_v=1.0,
            x_max_v=100.0,
            y_min_decade=1.0,
            y_max_decade=4.0,
            source="position_ocr",
            x_ticks_v=(1.0, 10.0, 100.0),
            y_decades=(1.0, 2.0, 3.0, 4.0),
            x_log=True,
            x_scale=0.01,
            x_offset=-1.0,
            y_scale=-0.01,
            y_offset=4.0,
        )
        self.assertTrue(
            mc._axis_result_is_trusted(
                calibration, position_error=None, grid_error=None, ocr_error=None
            )
        )
        self.assertFalse(
            mc._axis_result_is_trusted(
                calibration,
                position_error="position x residual exceeds threshold",
                grid_error=None,
                ocr_error=None,
            )
        )

    def test_trace_validation_summary_flags_semantic_failures(self) -> None:
        diagnostics = {
            "Ciss": {"points": 20, "x_span_fraction": 0.95, "y_range_px": 100},
            "Coss": {"points": 20, "x_span_fraction": 0.95, "y_range_px": 20},
            "Crss": {"points": 20, "x_span_fraction": 0.40, "y_range_px": 30},
            "checks": {
                "common_samples": 100,
                "ciss_coss_rank_swap_count": 2,
                "crss_bottom_fraction": 0.5,
                "ciss_flatter_than_coss": False,
            },
        }

        summary = mc.trace_validation_summary(diagnostics)

        self.assertEqual(summary["status"], "suspect")
        self.assertIn("Crss_short_x_span", summary["reasons"])
        self.assertIn("ciss_coss_rank_swap_count", summary["reasons"])
        self.assertIn("crss_not_bottom", summary["reasons"])
        self.assertIn("ciss_not_flatter_than_coss", summary["reasons"])

    def test_material_source_span_accepts_complete_stroke_short_of_plot_frame(self) -> None:
        diagnostics = {
            "Ciss": {"points": 275, "x_span_fraction": 0.685, "y_range_px": 17},
            "Coss": {"points": 275, "x_span_fraction": 0.685, "y_range_px": 46},
            "Crss": {"points": 275, "x_span_fraction": 0.685, "y_range_px": 60},
            "checks": {
                "common_samples": 275,
                "ciss_coss_rank_swap_count": 1,
                "crss_bottom_fraction": 1.0,
                "ciss_flatter_than_coss": True,
            },
        }

        summary = mc.trace_validation_summary(diagnostics)

        self.assertEqual("pass", summary["status"])
        self.assertEqual([], summary["reasons"])

    def test_full_span_flat_trace_refuses_without_claiming_grid_capture(self) -> None:
        diagnostics = {
            "Ciss": {"points": 200, "x_span_fraction": 1.0, "y_range_px": 0},
            "Coss": {"points": 200, "x_span_fraction": 1.0, "y_range_px": 80},
            "Crss": {"points": 200, "x_span_fraction": 1.0, "y_range_px": 120},
            "checks": {
                "common_samples": 200,
                "ciss_coss_rank_swap_count": 0,
                "crss_bottom_fraction": 1.0,
                "ciss_flatter_than_coss": True,
            },
        }

        summary = mc.trace_validation_summary(diagnostics)

        self.assertEqual("suspect", summary["status"])
        self.assertEqual(["Ciss_flat_full_span_unverified"], summary["reasons"])

    def test_dense_black_grid_removes_full_width_major_rail(self) -> None:
        mask = np.zeros((80, 120), dtype=np.uint8)
        mask[39:42, :] = 1
        # A real, thick sloped trace crosses the rail but never owns most of a
        # complete row.  Removing the rail must preserve its source ink on both
        # sides of the crossing.
        cv2.line(mask, (5, 20), (114, 65), 1, 3)

        cleaned = ct._remove_full_width_horizontal_rails(mask)

        self.assertFalse(np.any(cleaned[39:42, :]))
        self.assertGreater(int(cleaned[:39, :].sum()), 0)
        self.assertGreater(int(cleaned[42:, :].sum()), 0)

    def test_flat_raster_trace_in_dead_zone_span_refuses(self) -> None:
        # A dead-flat RASTER trace (y_range_px <= 1) with span in [0.65, 0.90) is
        # above the short-span floor yet below the old 0.90 full-span-flat gate,
        # so it used to escape BOTH guards and pass a mis-seated flat line.  This
        # is the HXY/onsemi/AO Crss-latched-onto-bottom-axis failure (12 corpus
        # C(V) charts, all raster).  Grid-capture is a raster phenomenon, so the
        # raster gate fires across the whole non-short span range: a flat trace
        # is never more trustworthy for also being shorter.
        diagnostics = {
            "Ciss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 40},
            "Coss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 60},
            "Crss": {"points": 200, "x_span_fraction": 0.76, "y_range_px": 0},
            "checks": {
                "common_samples": 200,
                "ciss_coss_rank_swap_count": 0,
                "crss_bottom_fraction": 1.0,
                "ciss_flatter_than_coss": True,
            },
        }

        summary = mc.trace_validation_summary(diagnostics, "raster")

        self.assertEqual("suspect", summary["status"])
        self.assertIn("Crss_flat_full_span_unverified", summary["reasons"])
        # Unknown provenance is treated conservatively as raster.
        self.assertEqual("suspect", mc.trace_validation_summary(diagnostics)["status"])

    def test_flat_vector_trace_in_dead_zone_span_passes(self) -> None:
        # Vector extraction reads actual PDF curve paths and cannot latch onto a
        # gridline, so a flat vector trace in the [0.65, 0.90) band is a genuine
        # flat curve, not grid-capture.  It must keep passing (this is the
        # RJK0853 full-span vector regression at the unit level).
        diagnostics = {
            "Ciss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 40},
            "Coss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 60},
            "Crss": {"points": 200, "x_span_fraction": 0.76, "y_range_px": 0},
            "checks": {
                "common_samples": 200,
                "ciss_coss_rank_swap_count": 0,
                "crss_bottom_fraction": 1.0,
                "ciss_flatter_than_coss": True,
            },
        }

        summary = mc.trace_validation_summary(diagnostics, "vector")

        self.assertEqual("pass", summary["status"])
        self.assertEqual([], summary["reasons"])

    def test_rising_trace_is_unphysical_and_refuses(self) -> None:
        # Capacitance is monotonically non-increasing in Vds. A trace whose value
        # climbs with Vds is a mis-seat onto a non-cap panel (SOA/Zth) that the
        # classifier mislabeled as capacitance. Calibrated: 24 good charts top out
        # at +0.011, the SOA/Zth leaks at +0.221/+0.097 (threshold 0.05).
        diagnostics = {
            "Ciss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 40,
                     "value_rise_fraction": -0.30},
            "Coss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 60,
                     "value_rise_fraction": 0.22},
            "Crss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 60,
                     "value_rise_fraction": -0.20},
            "checks": {
                "common_samples": 200,
                "ciss_coss_rank_swap_count": 0,
                "crss_bottom_fraction": 1.0,
                "ciss_flatter_than_coss": True,
            },
        }

        summary = mc.trace_validation_summary(diagnostics, "raster")

        self.assertEqual("suspect", summary["status"])
        self.assertIn("Coss_rises_with_vds_unphysical", summary["reasons"])

    def test_small_low_v_rise_within_tolerance_passes(self) -> None:
        # A real capacitance can wobble slightly near 0 V; net rise below the
        # calibrated 0.05 floor (good charts top out at +0.011) must NOT flag.
        diagnostics = {
            "Ciss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 40,
                     "value_rise_fraction": 0.011},
            "Coss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 60,
                     "value_rise_fraction": -0.08},
            "Crss": {"points": 200, "x_span_fraction": 0.95, "y_range_px": 60,
                     "value_rise_fraction": -0.30},
            "checks": {
                "common_samples": 200,
                "ciss_coss_rank_swap_count": 0,
                "crss_bottom_fraction": 1.0,
                "ciss_flatter_than_coss": True,
            },
        }

        summary = mc.trace_validation_summary(diagnostics, "raster")

        self.assertEqual("pass", summary["status"])
        self.assertEqual([], summary["reasons"])

    def test_column_runs_keep_reseparated_crossing_curves_distinct(self) -> None:
        column = np.zeros(80, dtype=np.uint8)
        column[20:23] = 1
        column[30:33] = 1

        centers = mc._cluster_column_runs(column)

        self.assertEqual([21.0, 31.0], centers)

    def test_column_runs_merge_nearby_antialias_fragments_of_one_stroke(self) -> None:
        column = np.zeros(80, dtype=np.uint8)
        column[20:22] = 1
        column[25:27] = 1

        centers = mc._cluster_column_runs(column)

        self.assertEqual([23.0], centers)

    def test_reseparated_crossing_reconnects_both_upper_traces(self) -> None:
        plot = mc.PlotBox(0, 0, 119, 199)
        centers_by_x: list[list[float]] = []
        assigned = {"Ciss": [], "Coss": [], "Crss": []}
        for x in range(120):
            steep = 30.0 + 0.35 * x
            flat = 50.0 + 0.02 * x
            if 50 <= x <= 70:
                merged = (steep + flat) / 2.0
                centers_by_x.append([merged, 150.0])
                assigned["Ciss"].append((x, round(merged)))
                assigned["Coss"].append((x, round(merged)))
            else:
                upper, lower = sorted((steep, flat))
                centers_by_x.append([upper, lower, 150.0])
                assigned["Ciss"].append((x, round(upper)))
                # Model the greedy failure: both names stay on the upper
                # source branch after the line-width-obscured crossing.
                assigned["Coss"].append(
                    (x, round(lower if x < 50 else upper))
                )
            assigned["Crss"].append((x, 150))

        repaired = mc._repair_reseparated_upper_crossing(
            centers_by_x, assigned, plot
        )
        traces = [
            mc.Trace(name, 120, (0, 0, 120, 150), repaired[name])
            for name in ("Ciss", "Coss", "Crss")
        ]
        repaired_ciss = dict(repaired["Ciss"])
        repaired_coss = dict(repaired["Coss"])
        coincident_crossing_columns = [
            x for x in range(50, 71) if repaired_ciss[x] == repaired_coss[x]
        ]

        self.assertGreater(repaired["Ciss"][-1][1], repaired["Coss"][-1][1])
        self.assertLessEqual(len(coincident_crossing_columns), 3)
        self.assertEqual(
            sorted(repaired_ciss[x] for x in range(50, 71)),
            [repaired_ciss[x] for x in range(50, 71)],
        )
        self.assertEqual(1, mc.trace_semantic_diagnostics(traces, plot)["checks"]["ciss_coss_rank_swap_count"])
        self.assertEqual([], mc.ciss_coss_shared_spans(traces, plot))

    def test_unbounded_upper_merge_is_not_fabricated_into_a_crossing(self) -> None:
        plot = mc.PlotBox(0, 0, 119, 199)
        centers_by_x = [
            [30.0 + x * 0.2, 50.0, 150.0] if x < 50 else [45.0, 150.0]
            for x in range(120)
        ]
        assigned = {
            "Ciss": [(x, round(centers[0])) for x, centers in enumerate(centers_by_x)],
            "Coss": [
                (x, round(centers[1] if len(centers) >= 3 else centers[0]))
                for x, centers in enumerate(centers_by_x)
            ],
            "Crss": [(x, 150) for x in range(120)],
        }

        repaired = mc._repair_reseparated_upper_crossing(
            centers_by_x, assigned, plot
        )

        self.assertEqual(assigned, repaired)

    def test_crossing_approach_does_not_snap_to_neighbor_curve(self) -> None:
        plot = mc.PlotBox(0, 0, 119, 199)
        centers_by_x: list[list[float]] = []
        assigned = {"Ciss": [], "Coss": [], "Crss": []}
        for x in range(120):
            source_coss = 30.0 + 0.35 * x
            source_ciss = 50.0 + 0.02 * x
            if 50 <= x <= 70:
                merged = (source_coss + source_ciss) / 2.0
                centers_by_x.append([merged, 150.0])
                assigned["Ciss"].append((x, round(merged)))
                assigned["Coss"].append((x, round(merged)))
            else:
                upper, lower = sorted((source_coss, source_ciss))
                centers_by_x.append([upper, lower, 150.0])
                assigned["Ciss"].append((x, round(source_ciss)))
                assigned["Coss"].append(
                    (
                        x,
                        round(
                            source_coss + 0.25 * (x - 40)
                            if 41 <= x < 50
                            else source_coss
                        ),
                    )
                )
            assigned["Crss"].append((x, 150))

        repaired = mc._repair_reseparated_upper_crossing(
            centers_by_x, assigned, plot
        )
        repaired_coss = dict(repaired["Coss"])

        for x in range(41, 50):
            source_coss = 30.0 + 0.35 * x
            source_ciss = 50.0 + 0.02 * x
            self.assertLessEqual(
                abs(repaired_coss[x] - source_coss),
                ct.UPPER_CROSSING_MAX_SOURCE_SEATING_DISTANCE_PX,
            )
            self.assertLess(
                abs(repaired_coss[x] - source_coss),
                abs(repaired_coss[x] - source_ciss),
            )
        self.assertEqual(
            sorted(repaired_coss[x] for x in range(35, 55)),
            [repaired_coss[x] for x in range(35, 55)],
        )


class AnchorAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = mc.PlotBox(x0=0, y0=0, x1=100, y1=400)
        self.calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=100.0,
            y_min_decade=0.0,
            y_max_decade=4.0,
            source="grid_text",
            x_ticks_v=(0.0, 100.0),
            y_decades=(0.0, 1.0, 2.0, 3.0, 4.0),
            x_scale=1.0,
            x_offset=0.0,
            y_scale=-0.01,
            y_offset=4.0,
        )

    @staticmethod
    def _trace(name: str, y: int) -> mc.Trace:
        return mc.Trace(
            name=name,
            area=81,
            bbox=(10, y, 81, 1),
            points=[(x, y) for x in range(10, 91)],
        )

    def test_multiple_anchors_relabel_swapped_candidate_traces(self) -> None:
        traces = [
            self._trace("Ciss", 200),
            self._trace("Coss", 100),
            self._trace("Crss", 300),
        ]
        anchors = {
            "Ciss": mc.CapAnchor("Ciss", 1000.0, 50.0),
            "Coss": mc.CapAnchor("Coss", 100.0, 50.0),
            "Crss": mc.CapAnchor("Crss", 10.0, 50.0),
        }

        assigned, diagnostics = mc.select_trace_assignment(
            traces, self.plot, self.calibration, anchors
        )

        self.assertTrue(diagnostics["assignment_changed"])
        self.assertEqual(diagnostics["selection_reason"], "anchor_evidence_selected")
        self.assertEqual(
            diagnostics["selected_source_assignment"],
            {"Ciss": "Coss", "Coss": "Ciss", "Crss": "Crss"},
        )
        self.assertEqual(dict(assigned[0].points)[50], 100)
        self.assertEqual(dict(assigned[1].points)[50], 200)
        for residual in diagnostics["anchor_residuals"].values():
            self.assertAlmostEqual(residual["relative_error"], 0.0)

    def test_single_anchor_reports_residual_without_relabeling(self) -> None:
        traces = [
            self._trace("Ciss", 200),
            self._trace("Coss", 100),
            self._trace("Crss", 300),
        ]
        anchors = {"Ciss": mc.CapAnchor("Ciss", 1000.0, 50.0)}

        assigned, diagnostics = mc.select_trace_assignment(
            traces, self.plot, self.calibration, anchors
        )

        self.assertFalse(diagnostics["assignment_changed"])
        self.assertEqual(diagnostics["selection_reason"], "insufficient_anchor_coverage")
        self.assertEqual([trace.name for trace in assigned], ["Ciss", "Coss", "Crss"])
        self.assertAlmostEqual(diagnostics["anchor_residuals"]["Ciss"]["log10_ratio"], -1.0)

    def test_poor_table_fit_is_diagnostic_not_forced_assignment(self) -> None:
        traces = [
            self._trace("Ciss", 100),
            self._trace("Coss", 200),
            self._trace("Crss", 300),
        ]
        anchors = {
            "Ciss": mc.CapAnchor("Ciss", 10.0**1.4, 50.0),
            "Coss": mc.CapAnchor("Coss", 10.0**2.4, 50.0),
            "Crss": mc.CapAnchor("Crss", 10.0**3.4, 50.0),
        }

        assigned, diagnostics = mc.select_trace_assignment(
            traces, self.plot, self.calibration, anchors
        )

        self.assertFalse(diagnostics["assignment_changed"])
        self.assertEqual(diagnostics["selection_reason"], "best_anchor_fit_too_poor")
        self.assertEqual([dict(trace.points)[50] for trace in assigned], [100, 200, 300])
        self.assertLess(
            diagnostics["best_candidate_total_score_decades"],
            diagnostics["baseline_total_score_decades"],
        )

    def test_anchor_outside_all_trace_spans_has_finite_diagnostics(self) -> None:
        traces = [
            self._trace("Ciss", 100),
            self._trace("Coss", 200),
            self._trace("Crss", 300),
        ]
        anchors = {"Ciss": mc.CapAnchor("Ciss", 1000.0, 5.0)}

        assigned, diagnostics = mc.select_trace_assignment(
            traces, self.plot, self.calibration, anchors
        )

        self.assertEqual(diagnostics["status"], "unavailable")
        self.assertEqual(diagnostics["selection_reason"], "no_locally_sampled_anchors")
        self.assertEqual(assigned, traces)
        self.assertIsNone(diagnostics["anchor_residuals"]["Ciss"]["sampled_pf"])
        for candidate in diagnostics["candidates"]:
            self.assertTrue(np.isfinite(candidate["total_score_decades"]))

    def test_anchor_inside_wide_trace_gap_is_not_sampled(self) -> None:
        def gapped_trace(name: str, y: int) -> mc.Trace:
            points = [(x, y) for x in range(0, 11)]
            points.extend((x, y) for x in range(90, 101))
            return mc.Trace(name=name, area=len(points), bbox=(0, y, 101, 1), points=points)

        traces = [
            gapped_trace("Ciss", 100),
            gapped_trace("Coss", 200),
            gapped_trace("Crss", 300),
        ]
        anchors = {
            "Ciss": mc.CapAnchor("Ciss", 1000.0, 50.0),
            "Coss": mc.CapAnchor("Coss", 100.0, 50.0),
            "Crss": mc.CapAnchor("Crss", 10.0, 50.0),
        }

        assigned, diagnostics = mc.select_trace_assignment(
            traces, self.plot, self.calibration, anchors
        )

        self.assertEqual(assigned, traces)
        self.assertEqual(diagnostics["status"], "unavailable")
        self.assertEqual(diagnostics["selection_reason"], "no_locally_sampled_anchors")
        for residual in diagnostics["anchor_residuals"].values():
            self.assertIsNone(residual["sampled_pf"])
            self.assertEqual(residual["reason"], "anchor_inside_trace_gap")

    def test_anchor_inside_short_trace_gap_is_sampled(self) -> None:
        def short_gap_trace(name: str, y: int) -> mc.Trace:
            points = [(x, y) for x in range(0, 49)]
            points.extend((x, y) for x in range(52, 101))
            return mc.Trace(name=name, area=len(points), bbox=(0, y, 101, 1), points=points)

        traces = [
            short_gap_trace("Ciss", 100),
            short_gap_trace("Coss", 200),
            short_gap_trace("Crss", 300),
        ]
        anchors = {
            "Ciss": mc.CapAnchor("Ciss", 1000.0, 50.0),
            "Coss": mc.CapAnchor("Coss", 100.0, 50.0),
            "Crss": mc.CapAnchor("Crss", 10.0, 50.0),
        }

        _, diagnostics = mc.select_trace_assignment(
            traces, self.plot, self.calibration, anchors
        )

        self.assertEqual(diagnostics["status"], "scored")
        for residual in diagnostics["anchor_residuals"].values():
            self.assertIsNotNone(residual["sampled_pf"])
            self.assertIsNone(residual["reason"])


class TraceRepairTests(unittest.TestCase):
    def test_track_direction_waits_for_trace_after_label_gap(self) -> None:
        centers_by_x = [[100.0] for _ in range(120)]
        for x in range(41, 75):
            centers_by_x[x] = [135.0, 300.0]
        for x in range(75, 120):
            centers_by_x[x] = [100.0, 140.0, 300.0]

        tracked = mc._track_direction(
            centers_by_x,
            seed_x=40,
            seed_y=100.0,
            direction=1,
            candidate_kind="upper",
        )

        by_x = dict(tracked)
        self.assertNotIn(50, by_x)
        self.assertEqual(by_x[75], 100.0)
        self.assertEqual(by_x[100], 100.0)

    def test_leading_coss_upper_envelope_repairs_shared_ciss_prefix(self) -> None:
        plot = mc.PlotBox(x0=10, y0=20, x1=130, y1=140)
        centers_by_x: list[list[float]] = []
        for x in range(plot.width):
            if x <= 18:
                centers_by_x.append([30.0 + x * 0.5, 70.0, 105.0])
            elif x <= 44:
                centers_by_x.append([70.0, 105.0])
            else:
                centers_by_x.append([70.0, 86.0, 105.0])

        ciss = [(plot.x0 + x, plot.y0 + 70) for x in range(3, 100)]
        coss = [(plot.x0 + x, plot.y0 + 70) for x in range(3, 45)]
        coss.extend((plot.x0 + x, plot.y0 + 86) for x in range(45, 100))
        crss = [(plot.x0 + x, plot.y0 + 105) for x in range(3, 100)]

        repaired = mc._repair_leading_coss_upper_envelope(
            centers_by_x,
            {"Ciss": ciss, "Coss": coss, "Crss": crss},
            plot,
        )

        repaired_coss = dict(repaired["Coss"])
        self.assertLess(repaired_coss[plot.x0 + 3], dict(ciss)[plot.x0 + 3] - 20)
        self.assertLess(repaired_coss[plot.x0 + 15], dict(ciss)[plot.x0 + 15] - 20)
        self.assertEqual(len(repaired["Coss"]), len(coss))
        self.assertTrue(mc._low_v_nonfolding(repaired["Coss"], plot))

    def test_leading_coss_upper_envelope_uses_nearest_branch_above_ciss(self) -> None:
        plot = mc.PlotBox(x0=10, y0=20, x1=130, y1=140)
        centers_by_x: list[list[float]] = []
        for x in range(plot.width):
            if x <= 18:
                # A high Ciss knee and a middle Coss branch both sit above the
                # Ciss plateau. Coss repair must choose the middle branch.
                centers_by_x.append([30.0 + x * 0.4, 55.0 + x * 0.2, 70.0, 105.0])
            elif x <= 44:
                centers_by_x.append([70.0, 105.0])
            else:
                centers_by_x.append([70.0, 86.0, 105.0])

        ciss = [(plot.x0 + x, plot.y0 + 70) for x in range(3, 100)]
        coss = [(plot.x0 + x, plot.y0 + 70) for x in range(3, 45)]
        coss.extend((plot.x0 + x, plot.y0 + 86) for x in range(45, 100))
        crss = [(plot.x0 + x, plot.y0 + 105) for x in range(3, 100)]

        repaired = mc._repair_leading_coss_upper_envelope(
            centers_by_x,
            {"Ciss": ciss, "Coss": coss, "Crss": crss},
            plot,
        )

        repaired_coss = dict(repaired["Coss"])
        self.assertEqual(repaired_coss[plot.x0 + 3], plot.y0 + 56)
        self.assertEqual(repaired_coss[plot.x0 + 15], plot.y0 + 58)
        self.assertNotEqual(repaired_coss[plot.x0 + 3], plot.y0 + 31)
        self.assertTrue(mc._low_v_nonfolding(repaired["Coss"], plot))

    def test_missing_leading_knee_repair_is_single_valued_and_nonfolding(self) -> None:
        plot = mc.PlotBox(x0=10, y0=20, x1=109, y1=139)
        mask = np.zeros((plot.height, plot.width), dtype=np.uint8)

        # Near-vertical missing knee from local x ~= 0..5 and y 31..49.
        for local_y in range(31, 50):
            local_x = int(round(np.interp(local_y, [31, 49], [0, 5])))
            mask[local_y, max(0, local_x - 1) : min(mask.shape[1], local_x + 2)] = 1

        original = [(plot.x0 + 5, plot.y0 + 50)]
        original.extend((plot.x0 + x, plot.y0 + 50 + x) for x in range(6, 18))

        repaired = mc._repair_missing_leading_knee(mask, original, plot)

        self.assertIsNotNone(repaired)
        assert repaired is not None
        self.assertTrue(mc._single_valued_by_x(repaired))
        self.assertTrue(mc._low_v_nonfolding(repaired, plot))
        self.assertLessEqual(min(x for x, _ in repaired), plot.x0 + 1)

    def test_splice_guard_rejects_discontinuous_changed_segment(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=200, y1=120)
        original = [(x, 100) for x in range(20, 80)]
        repaired = [(x, 20) for x in range(0, 10)] + original

        self.assertFalse(mc._splice_continuity_ok(repaired, original, plot))

    def test_splice_guard_checks_split_changed_runs(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=200, y1=120)
        original = [(x, 50) for x in range(0, 100)]
        repaired = []
        for x, y in original:
            if 10 <= x <= 14:
                repaired.append((x, 52))
            elif 40 <= x <= 44:
                repaired.append((x, 100))
            else:
                repaired.append((x, y))

        self.assertFalse(mc._splice_continuity_ok(repaired, original, plot))

    def test_peer_overlap_guard_uses_local_interpolated_repair_segment(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=120, y1=120)
        original = [(x, 80) for x in range(20, 100)]
        repaired = [(x, 30) for x in range(0, 20)] + original
        peer = [(x, 30) for x in range(1, 21)]

        self.assertFalse(mc._repair_shape_guard(repaired, original, plot, peers={"Ciss": peer}))

    def test_crss_bottom_guard_uses_local_repair_segment(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=120, y1=120)
        original = [(x, 90) for x in range(20, 100)]
        repaired = [(x, 35) for x in range(0, 20)] + original
        coss = [(x, 60) for x in range(0, 100)]

        self.assertFalse(
            mc._repair_shape_guard(repaired, original, plot, peers={"Coss": coss}, require_bottom=True)
        )

    def test_trim_repair_points_on_peer_keeps_only_separated_segment(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=120, y1=120)
        original = [(x, 90) for x in range(40, 100)]
        repaired = [(x, 50) for x in range(0, 10)]
        repaired.extend((x, 20) for x in range(10, 15))
        repaired.extend((x, 82) for x in range(15, 25))
        repaired.extend(original)
        peer = [(x, 50) for x in range(0, 100)]

        trimmed = mc._trim_repair_points_on_peer(repaired, original, peer, plot)

        self.assertIsNotNone(trimmed)
        assert trimmed is not None
        trimmed_by_x = dict(trimmed)
        self.assertNotIn(5, trimmed_by_x)
        self.assertNotIn(12, trimmed_by_x)
        self.assertEqual(trimmed_by_x[18], 82)
        self.assertEqual(trimmed_by_x[50], 90)

    def test_low_v_coss_monotone_cleanup_clamps_only_prefix(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=100, y1=100)
        points = [(0, 10), (10, 20), (20, 18), (30, 25), (50, 15)]

        cleaned = mc._enforce_low_v_coss_monotone(points, plot, fraction=0.35)

        self.assertEqual(cleaned[:4], [(0, 10), (10, 20), (20, 20), (30, 25)])
        self.assertEqual(cleaned[4], (50, 15))

    def test_low_v_coss_monotone_cleanup_does_not_hide_large_swaps(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=100, y1=100)
        points = [(0, 80), (10, 95), (20, 35), (30, 100)]

        cleaned = mc._enforce_low_v_coss_monotone(points, plot, fraction=0.35)

        self.assertEqual(cleaned[2], (20, 35))
        self.assertFalse(mc._low_v_nonfolding(cleaned, plot))

    def test_coss_ciss_overlap_gap_is_bridged_between_separated_segments(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=200, y1=120)
        ciss = [(x, 50) for x in range(0, 110)]
        coss = [(x, 30) for x in range(0, 20)]
        coss.extend((x, 50) for x in range(20, 81))
        coss.extend((x, 70) for x in range(81, 110))
        crss = [(x, 95) for x in range(0, 110)]

        repaired = mc._repair_coss_ciss_overlap_gap(
            {"Ciss": ciss, "Coss": coss, "Crss": crss},
            plot,
        )

        repaired_coss = dict(repaired["Coss"])
        self.assertGreater(repaired_coss[65], 50)
        self.assertLess(repaired_coss[65], 70)
        self.assertTrue(mc._low_v_nonfolding(repaired["Coss"], plot))

    def test_coss_ciss_overlap_gap_does_not_bridge_without_rank_swap(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=200, y1=120)
        ciss = [(x, 50) for x in range(0, 110)]
        coss = [(x, 70) for x in range(0, 20)]
        coss.extend((x, 50) for x in range(20, 81))
        coss.extend((x, 75) for x in range(81, 110))
        crss = [(x, 95) for x in range(0, 110)]

        repaired = mc._repair_coss_ciss_overlap_gap(
            {"Ciss": ciss, "Coss": coss, "Crss": crss},
            plot,
        )

        self.assertEqual(repaired["Coss"], coss)

    def test_coss_ciss_overlap_gap_does_not_bridge_short_antialias_contact(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=200, y1=120)
        ciss = [(x, 50) for x in range(0, 110)]
        coss = [(x, 30) for x in range(0, 45)]
        coss.extend((x, 52) for x in range(45, 52))
        coss.extend((x, 70) for x in range(52, 110))
        crss = [(x, 95) for x in range(0, 110)]

        repaired = mc._repair_coss_ciss_overlap_gap(
            {"Ciss": ciss, "Coss": coss, "Crss": crss},
            plot,
        )

        self.assertEqual(repaired["Coss"], coss)

    def test_crss_left_knee_repair_accepts_anchor_beyond_eight_percent_width(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=519, y1=519)
        mask = np.zeros((plot.height, plot.width), dtype=np.uint8)
        for y in range(220, 401):
            x = int(round(np.interp(y, [220, 400], [0, 47])))
            mask[y, max(0, x - 1) : min(plot.width, x + 2)] = 1

        ciss = [(x, 80) for x in range(0, 500)]
        coss = [(x, 170) for x in range(0, 500)]
        crss = [(x, 400 + min(80, x - 47)) for x in range(47, 500)]

        repaired = mc._repair_leading_steep_crss(
            mask,
            {"Ciss": ciss, "Coss": coss, "Crss": crss},
            plot,
        )

        self.assertLess(min(x for x, _ in repaired["Crss"]), 10)
        self.assertTrue(mc._repair_shape_guard(repaired["Crss"], crss, plot, peers={"Ciss": ciss, "Coss": coss}, require_bottom=True))


class VectorExtractionTests(unittest.TestCase):
    def test_curve_stroke_color_accepts_teal_but_rejects_gray_grid(self) -> None:
        self.assertTrue(mc._is_curve_stroke_color((0.03, 0.40, 0.36)))
        self.assertTrue(mc._is_curve_stroke_color((0.0, 0.0, 0.0)))
        self.assertFalse(mc._is_curve_stroke_color((0.55, 0.55, 0.55)))
        self.assertTrue(mc._is_curve_stroke_color((0.7243, 0.7244, 0.7243)))
        # Bright saturated primaries ARE curves since TI C(V) support (TI draws
        # Ciss/Coss/Crss in pure red/green/blue); gray grid stays rejected.
        self.assertTrue(mc._is_curve_stroke_color((0.8, 0.1, 0.1)))

    def test_internal_long_horizontal_segment_is_not_treated_as_grid(self) -> None:
        class Rect:
            x0 = 10.0
            y0 = 20.0
            x1 = 210.0
            y1 = 120.0
            width = 200.0
            height = 100.0

        self.assertFalse(mc._is_long_orthogonal_segment((20.0, 70.0), (190.0, 70.0), Rect))
        self.assertTrue(mc._is_long_orthogonal_segment((20.0, 20.5), (190.0, 20.5), Rect))

    def test_mostly_inside_plot_allows_clipped_curve_continuations(self) -> None:
        class Rect:
            x0 = 0.0
            y0 = 0.0
            x1 = 100.0
            y1 = 100.0
            width = 100.0

            def contains(self, point: tuple[float, float]) -> bool:
                x, y = point
                return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

        points = [(float(x), 50.0) for x in range(-30, 101, 10)]
        self.assertTrue(mc._mostly_inside_plot(points, Rect()))

    def test_mostly_inside_plot_rejects_weak_visible_span(self) -> None:
        class Rect:
            x0 = 0.0
            y0 = 0.0
            x1 = 100.0
            y1 = 100.0
            width = 100.0

            def contains(self, point: tuple[float, float]) -> bool:
                x, y = point
                return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

        points = [(float(x), 50.0) for x in range(0, 50, 5)]
        self.assertFalse(mc._mostly_inside_plot(points, Rect()))

    def test_source_path_rescue_rejects_horizontal_grid_or_annotation(self) -> None:
        self.assertFalse(cv._has_material_vertical_response([(0.0, 50.0), (100.0, 50.0)], 100.0))
        self.assertFalse(cv._has_material_vertical_response([(0.0, 50.0), (100.0, 50.9)], 100.0))
        self.assertTrue(cv._has_material_vertical_response([(0.0, 50.0), (100.0, 51.0)], 100.0))

    def test_vector_resampling_emits_dense_single_valued_trace(self) -> None:
        plot = mc.PlotBox(x0=10, y0=10, x1=110, y1=110)
        raw = [(10, 20), (110, 20)]

        dense = mc._resample_vector_trace_pixels(raw, plot)

        self.assertGreater(len(dense), 90)
        self.assertTrue(mc._single_valued_by_x(dense))
        self.assertEqual(dense[0], (10, 20))
        self.assertEqual(dense[-1], (110, 20))

    def test_vector_resampling_does_not_interpolate_disconnected_jump(self) -> None:
        plot = mc.PlotBox(x0=10, y0=10, x1=110, y1=110)
        raw = [(10, 20), (30, 20), (90, 90), (110, 90)]

        dense = mc._resample_vector_trace_pixels(raw, plot)

        self.assertTrue(mc._single_valued_by_x(dense))
        xs = [x for x, _ in dense]
        self.assertNotIn(60, xs)


if __name__ == "__main__":
    unittest.main()
