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


class TraceRepairTests(unittest.TestCase):
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
        plot = mc.PlotBox(x0=0, y0=0, x1=120, y1=120)
        original = [(x, 100) for x in range(20, 80)]
        repaired = [(x, 20) for x in range(0, 10)] + original

        self.assertFalse(mc._splice_continuity_ok(repaired, original, plot))

    def test_splice_guard_checks_split_changed_runs(self) -> None:
        plot = mc.PlotBox(x0=0, y0=0, x1=120, y1=120)
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


class VectorExtractionTests(unittest.TestCase):
    def test_curve_stroke_color_accepts_teal_but_rejects_gray_grid(self) -> None:
        self.assertTrue(mc._is_curve_stroke_color((0.03, 0.40, 0.36)))
        self.assertTrue(mc._is_curve_stroke_color((0.0, 0.0, 0.0)))
        self.assertFalse(mc._is_curve_stroke_color((0.55, 0.55, 0.55)))
        self.assertFalse(mc._is_curve_stroke_color((0.8, 0.1, 0.1)))

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


if __name__ == "__main__":
    unittest.main()
