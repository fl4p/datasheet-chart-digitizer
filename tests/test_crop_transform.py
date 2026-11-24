import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

import numpy as np

from datasheet_chart_digitizer import capacitance_axis as cap_axis
from datasheet_chart_digitizer import capacitance_vector as cap_vector
from datasheet_chart_digitizer import find_charts, mosfet_capacitance
from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.crop_transform import CROP_MARGIN_PT, CropTransform


class CropTransformTests(unittest.TestCase):
    def test_exact_crop_box_round_trip_with_unequal_scales(self) -> None:
        transform = CropTransform.for_chart(
            {
                "bbox_pt": [12.0, 24.0, 58.0, 116.0],
                "crop_box_pt": [10.0, 20.0, 60.0, 120.0],
            },
            (300, 200),
        )

        self.assertEqual(transform.scale_x, 4.0)
        self.assertEqual(transform.scale_y, 3.0)
        self.assertEqual(transform.to_px(12.5, 25.0), (10.0, 15.0))
        self.assertEqual(transform.to_pt(10.0, 15.0), (12.5, 25.0))

    def test_legacy_index_falls_back_to_bbox_plus_margin(self) -> None:
        transform = CropTransform.for_chart(
            {"bbox_pt": (10.0, 20.0, 60.0, 120.0)},
            (208, 108),
        )

        self.assertEqual(transform.x0_pt, 10.0 - CROP_MARGIN_PT)
        self.assertEqual(transform.y0_pt, 20.0 - CROP_MARGIN_PT)
        self.assertEqual(transform.scale_x, 2.0)
        self.assertEqual(transform.scale_y, 2.0)

    def test_missing_bbox_is_refused(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "bbox_pt missing"):
            CropTransform.for_chart({}, (100, 100))


class BlankRotatedWordsTests(unittest.TestCase):
    """Review 2026-11-24: a rotated curve label inside the plot erased frame ink
    and moved the diode calibrator's plot hint from x0 100 to 150."""

    @staticmethod
    def _gridded_page(y_ticks: bool):
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=500, height=500)
        for v in range(100, 401, 50):
            page.draw_line((v, 100), (v, 400), color=(0, 0, 0))
            page.draw_line((100, v), (400, v), color=(0, 0, 0))
        page.insert_text((105, 280), "CurveLabel", fontsize=12, rotate=90)
        if y_ticks:
            for k, v in enumerate(range(400, 99, -50)):
                page.insert_text((80, v + 3), str(k), fontsize=8)
            page.insert_text((60, 300), "Current", fontsize=10, rotate=90)
        pix = page.get_pixmap()
        rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        return doc, page, rgb[:, :, :3].min(axis=2).copy()

    def test_in_plot_rotated_label_keeps_frame_and_hint(self) -> None:
        from datasheet_chart_digitizer.crop_transform import blank_rotated_words
        from datasheet_chart_digitizer.diode_forward_voltage import _plot_hint

        doc, page, gray = self._gridded_page(y_ticks=False)
        with doc:
            blank = blank_rotated_words(gray, page, CropTransform(0, 0, 1, 1))
        self.assertEqual(int(np.count_nonzero((gray[:, 100] < 128) & (blank[:, 100] == 255))), 0)
        self.assertEqual(_plot_hint(blank)[0].x0, _plot_hint(gray)[0].x0)
        self.assertEqual(_plot_hint(blank)[0].x0, 100)

    def test_only_gutter_title_left_of_tick_labels_is_blanked(self) -> None:
        from datasheet_chart_digitizer.crop_transform import blank_rotated_words

        doc, page, gray = self._gridded_page(y_ticks=True)
        with doc:
            blank = blank_rotated_words(gray, page, CropTransform(0, 0, 1, 1))
            title = next(w for w in page.get_text("words") if w[4] == "Current")
        changed = np.argwhere(blank != gray)
        self.assertGreater(len(changed), 0)
        self.assertLessEqual(changed[:, 1].max(), int(title[2]) + 1)   # the gutter title only
        self.assertTrue((blank[:, 100] == gray[:, 100]).all())


class CapacitanceCropTransformIntegrationTests(unittest.TestCase):
    @staticmethod
    def _fitz_stub(rectangles: list[object] | None = None):
        class Rect:
            def __init__(self, x0, y0, x1, y1):
                self.x0 = float(x0)
                self.y0 = float(y0)
                self.x1 = float(x1)
                self.y1 = float(y1)
                self.width = self.x1 - self.x0
                self.height = self.y1 - self.y0
                if rectangles is not None:
                    rectangles.append(self)

        class Page:
            def get_drawings(self):
                return []

            def get_text(self, option):
                # a real fitz.Page always has it; the legend binder reads words
                return []

        class Doc:
            def __getitem__(self, index):
                return Page()

        return SimpleNamespace(Rect=Rect, open=lambda path: Doc())

    def test_position_calibration_uses_effective_crop_box(self) -> None:
        rectangles: list[object] = []
        fitz = self._fitz_stub(rectangles)
        pos_cal = SimpleNamespace(
            mx=0.5,
            bx=-50.0,
            my=-0.02,
            by=6.0,
            x_ticks=((0.0, 100.0), (20.0, 140.0)),
            y_decades=((4.0, 100.0), (2.0, 200.0)),
            x_resid=0.0,
            y_resid=0.0,
        )
        captured: dict[str, object] = {}

        def calibrate(page, **kwargs):
            captured.update(kwargs)
            return pos_cal

        chart = {
            "pdf": "/unused.pdf",
            "page": 1,
            "bbox_pt": [102.0, 202.0, 148.0, 298.0],
            "crop_box_pt": [100.0, 200.0, 150.0, 300.0],
        }
        image = np.zeros((200, 100, 3), dtype=np.uint8)
        plot = PlotBox(10, 20, 80, 150)

        with mock.patch.object(cap_axis, "_load_fitz", return_value=fitz), mock.patch.object(
            cap_axis, "calibrate_axes", side_effect=calibrate
        ):
            calibration = cap_axis.infer_position_axis_calibration(chart, image, plot)

        rect = rectangles[0]
        self.assertEqual((rect.x0, rect.y0, rect.x1, rect.y1), (105.0, 210.0, 140.0, 275.0))
        self.assertEqual(captured["x_row_band"], (277.0, 299.0))
        self.assertEqual(captured["y_label_x_band"], (63.0, 104.0))
        self.assertEqual(captured["plot_y_band"], (202.0, 283.0))
        self.assertAlmostEqual(calibration.x_scale, 0.25)
        self.assertAlmostEqual(calibration.x_offset, 0.0)
        self.assertAlmostEqual(calibration.y_scale, -0.01)
        self.assertAlmostEqual(calibration.y_offset, 2.0)

    def test_vector_plot_and_points_use_same_effective_crop_box(self) -> None:
        rectangles: list[object] = []
        fitz = self._fitz_stub(rectangles)
        components = [
            [(111.0 + 9.75 * index, y) for index in range(9)]
            for y in (220.0, 240.0, 260.0)
        ]
        chart = {
            "pdf": "/unused.pdf",
            "page": 1,
            "bbox_pt": [102.0, 202.0, 198.0, 298.0],
            "crop_box_pt": [100.0, 200.0, 200.0, 300.0],
        }
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        plot = PlotBox(10, 10, 90, 90)

        with mock.patch.object(cap_vector, "_load_fitz", return_value=fitz), mock.patch.object(
            cap_vector, "_vector_curve_edges", return_value=[]
        ), mock.patch.object(
            cap_vector, "_chain_vector_components", return_value=components
        ), mock.patch.object(cap_vector, "_mostly_inside_plot", return_value=True):
            traces = cap_vector.extract_vector_trace_components(chart, image, plot)

        rect = rectangles[0]
        self.assertEqual((rect.x0, rect.y0, rect.x1, rect.y1), (110.0, 210.0, 190.0, 290.0))
        self.assertEqual([trace.name for trace in traces], ["Ciss", "Coss", "Crss"])
        self.assertEqual([min(x for x, _ in trace.points) for trace in traces], [11, 11, 11])
        self.assertEqual([max(x for x, _ in trace.points) for trace in traces], [89, 89, 89])
        self.assertEqual([round(np.median([y for _, y in trace.points])) for trace in traces], [20, 40, 60])


INFINEON = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon")


@unittest.skipUnless(INFINEON.exists(), "local datasheet library not available")
class FreshFinderCropTransformEndToEnd(unittest.TestCase):
    """Exercise authoritative crop_box_pt through real finder + C(V) paths.

    Fab visually verified the labeled axis guides and extracted curve overlays
    for all three samples on 2026-07-14. Do not relax the pinned coefficients
    without repeating that review.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = TemporaryDirectory(prefix="cv-crop-transform-test-")
        cls.root = Path(cls.tmp.name)
        cls.results: dict[str, tuple[dict[str, object], dict[str, object]]] = {}
        for part in ("BSC014N04LS", "BSC016N06NS", "IAUCN08S5L160T"):
            panels = find_charts.process_pdf(INFINEON / f"{part}.pdf", cls.root / part, dpi=180)
            charts = [find_charts.asdict(panel) for panel in panels if panel.kind == "capacitances"]
            if len(charts) != 1:
                raise AssertionError(f"{part}: expected one capacitance chart, got {len(charts)}")
            chart = charts[0]
            crop_rel = Path(str(chart["crop_png"]))
            result = mosfet_capacitance.process_chart(
                chart,
                cls.root / part / crop_rel,
                cls.root / part / "digitized",
                crop_rel.with_suffix(""),
                INFINEON,
            )
            cls.results[part] = (chart, result)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_fresh_indexes_carry_distinct_effective_crop_boxes(self) -> None:
        for part, (chart, _) in self.results.items():
            self.assertIn("crop_box_pt", chart, part)
            self.assertNotEqual(chart["crop_box_pt"], chart["bbox_pt"], part)

    def test_real_extraction_calibration_strata(self) -> None:
        expected = {
            # Three complete black source paths share their low-VDS endpoint;
            # path-isolation rescue preserves them instead of grid-riding.
            "BSC014N04LS": ("vector", "position_text"),
            "BSC016N06NS": ("vector", "position_text"),
            "IAUCN08S5L160T": ("vector", "position_text"),
        }
        for part, (_, result) in self.results.items():
            self.assertEqual(
                (result["extraction_method"], result["axis_calibration"]["source"]),
                expected[part],
            )
            self.assertEqual(result["trace_validation_status"], "pass", part)
            self.assertFalse(result["anchor_diagnostics"]["assignment_changed"], part)

    def test_exact_transform_axis_coefficients_are_pinned(self) -> None:
        # Re-pinned 2026-09-29 to the gridline-anchored calibration (both
        # axes of all three verified on their rules). Fab's 2026-07-14 visual
        # verification covered the previous label-centre coefficients
        # (0.07627819, -8.42946005, -0.00552817, 4.38814795 /
        #  0.11441731, -12.64419946, same y / 0.15255660, -15.82277066,
        #  -0.00737473, 4.59065286); the new ones need that review repeated.
        expected = {
            "BSC014N04LS": (0.07633588, -8.43511450, -0.00553097, 4.39020960),
            "BSC016N06NS": (0.11449594, -12.63338157, -0.00553097, 4.39020960),
            "IAUCN08S5L160T": (0.15267176, -15.80152672, -0.00737191, 4.59270075),
        }
        for part, (_, result) in self.results.items():
            axis = result["axis_calibration"]
            actual = (axis["x_scale"], axis["x_offset"], axis["y_scale"], axis["y_offset"])
            for got, want in zip(actual, expected[part]):
                self.assertAlmostEqual(got, want, places=6, msg=part)


if __name__ == "__main__":
    unittest.main()
