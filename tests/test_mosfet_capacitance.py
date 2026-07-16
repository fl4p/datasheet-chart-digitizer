import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import cv2
import numpy as np

from datasheet_chart_digitizer import mosfet_capacitance as mc


class AxisCalibrationTests(unittest.TestCase):
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
            with mock.patch.object(mc, "find_plot_box", return_value=mc.PlotBox(10, 10, 50, 50)), \
                mock.patch.object(mc, "parse_capacitance_anchors", return_value={}), \
                mock.patch.object(mc, "parse_output_charge_reference", return_value=mc.OutputChargeReference(None, None, None, None)), \
                mock.patch.object(mc, "infer_text_order_axis_calibration", return_value=calibration), \
                mock.patch.object(mc, "infer_position_axis_calibration", return_value=calibration), \
                mock.patch.object(mc, "extract_vector_trace_components", return_value=traces):
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
            with mock.patch.object(mc, "find_plot_box", return_value=mc.PlotBox(10, 10, 50, 50)), \
                mock.patch.object(mc, "parse_capacitance_anchors", return_value={}), \
                mock.patch.object(
                    mc,
                    "parse_output_charge_reference",
                    return_value=mc.OutputChargeReference(1000.0, 10.0, None, None),
                ), \
                mock.patch.object(mc, "infer_text_order_axis_calibration", return_value=calibration), \
                mock.patch.object(mc, "infer_position_axis_calibration", side_effect=RuntimeError("no positions")), \
                mock.patch.object(mc, "infer_gridline_axis_calibration", side_effect=RuntimeError("no grid")), \
                mock.patch.object(mc, "extract_vector_trace_components", return_value=traces):
                result = mc.process_chart(
                    {"part": "P", "diagram": 1, "pdf": "p.pdf", "page": 1},
                    crop_path,
                    root / "out",
                    crop_rel.with_suffix(""),
                    root,
                )

            self.assertFalse(result["axis_calibration_trusted"])
            self.assertIn("untrusted text-order axis fallback", result["axis_warning"])
            self.assertEqual(result["qoss_validation_error"], "untrusted axis calibration")
            rows = (root / "out" / str(result["points"])).read_text().splitlines()
            self.assertTrue(rows[1].endswith(",,"))

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


TK100E10N1 = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/toshiba/TK100E10N1.pdf")
_HAVE_TESSERACT = shutil.which("tesseract") is not None


@unittest.skipUnless(TK100E10N1.exists(), "local TK100E10N1 datasheet not available")
@unittest.skipUnless(_HAVE_TESSERACT, "tesseract not available for OCR axis calibration")
class ToshibaRasterEndToEndTests(unittest.TestCase):
    def test_ocr_calibrated_raster_digitization_matches_nameplate(self) -> None:
        # Whole-figure raster chart: OCR position calibration (log-X) + black
        # grid removed by stroke thickness. Pin against the table typ values
        # @ VDS=50 V: Ciss 8800 / Coss 1500 / Crss 63 pF.
        from datasheet_chart_digitizer import find_charts

        with TemporaryDirectory(prefix="tk100e10n1-e2e-") as tmp:
            out = Path(tmp)
            # CLI-default render DPI. At higher DPI the 1-px black gridlines
            # render 2 px and survive the 2x2 stroke-thickness opening; the
            # semantic validation then (correctly) reports "suspect".
            panels = find_charts.process_pdf(TK100E10N1, out, dpi=180)
            caps = [panel for panel in panels if panel.kind == "capacitances"]
            self.assertEqual(len(caps), 1)
            panel = caps[0]
            chart = {
                "pdf": str(TK100E10N1),
                "part": "TK100E10N1",
                "page": panel.page,
                "bbox_pt": list(panel.bbox_pt),
                "crop_box_pt": list(panel.crop_box_pt),
                "text": "",
            }
            crop_path = out / panel.crop_png
            result = mc.process_chart(
                chart, crop_path, out, Path("tk100e10n1"), TK100E10N1.parent
            )

            calibration = result["axis_calibration"]
            self.assertIsNotNone(calibration)
            self.assertEqual(calibration["source"], "position_ocr")
            self.assertTrue(calibration["x_log"])
            self.assertTrue(result["axis_calibration_trusted"])
            self.assertEqual(result["trace_validation_status"], "pass")

            import csv

            with open(out / result["points"]) as fh:
                rows = list(csv.DictReader(fh))
        expected = {"Ciss": 8800.0, "Coss": 1500.0, "Crss": 63.0}
        for name, typ_pf in expected.items():
            pts = sorted(
                (float(r["vds_V"]), float(r["cap_pF"]))
                for r in rows
                if r["trace"] == name and r.get("vds_V")
            )
            self.assertTrue(pts, f"{name} produced no calibrated points")
            vds, cap = min(pts, key=lambda p: abs(p[0] - 50.0))
            self.assertLess(abs(vds - 50.0) / 50.0, 0.1, f"{name}: no sample near 50 V")
            self.assertLess(
                abs(cap - typ_pf) / typ_pf,
                0.15,
                f"{name} @ {vds:.1f} V: {cap:.0f} pF vs table typ {typ_pf:.0f} pF",
            )


if __name__ == "__main__":
    unittest.main()
