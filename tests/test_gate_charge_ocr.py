from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pymupdf

from datasheet_chart_digitizer import find_charts
from datasheet_chart_digitizer import gate_charge as gate


def _panel(*, kind: str = "gate_charge") -> find_charts.ChartPanel:
    return find_charts.ChartPanel(
        pdf="sample.pdf",
        part="sample",
        page=2,
        diagram=7,
        title="Gate charge",
        kind=kind,
        bbox_pt=(100.0, 100.0, 300.0, 300.0),
        crop_box_pt=(95.0, 95.0, 305.0, 305.0),
        crop_png="temporary.png",
        text="Gate charge",
        formula="",
        mentions=[],
    )


def _tsv(words: list[tuple[int, int, int, int, str]]) -> str:
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        f"5\t1\t1\t1\t1\t{index}\t{x}\t{y}\t{width}\t{height}\t95\t{text}"
        for index, (x, y, width, height, text) in enumerate(words, start=1)
    ]
    return "\n".join([header, *rows])


class GateChargeOcrTests(unittest.TestCase):
    def test_tesseract_tsv_maps_pixels_to_pdf_points(self) -> None:
        page = find_charts._page_text_from_tesseract_tsv(
            _tsv([(100, 200, 50, 20, "Gate")]),
            page_num=3,
            width_pt=600.0,
            height_pt=800.0,
            width_px=1200,
            height_px=1600,
        )

        self.assertEqual(page.text_source, "tesseract_fallback")
        self.assertEqual(page.words[0], find_charts.Word("Gate", 50.0, 100.0, 75.0, 110.0))

    def test_ocr_axis_labels_accept_qgtotal_and_guard_qc_confusion(self) -> None:
        sp = find_charts._page_text_from_tesseract_tsv(
            _tsv(
                [
                    (100, 200, 55, 20, "Qg-Total"),
                    (165, 200, 45, 20, "Gate"),
                    (220, 200, 60, 20, "Charge"),
                    (290, 200, 40, 20, "(nC)"),
                ]
            ),
            page_num=3,
            width_pt=600.0,
            height_pt=800.0,
            width_px=1200,
            height_px=1600,
        )
        hy = find_charts._page_text_from_tesseract_tsv(
            _tsv(
                [
                    (700, 900, 30, 20, "Qc"),
                    (740, 900, 10, 20, "-"),
                    (760, 900, 45, 20, "Gate"),
                    (815, 900, 60, 20, "Charge"),
                    (885, 900, 40, 20, "(nC)"),
                ]
            ),
            page_num=6,
            width_pt=600.0,
            height_pt=800.0,
            width_px=1200,
            height_px=1600,
        )

        self.assertEqual(len(find_charts.gate_charge_axis_label_spans(sp)), 1)
        self.assertEqual(len(find_charts.gate_charge_axis_label_spans(hy)), 1)
        native_hy = find_charts.PageText(
            page_num=hy.page_num,
            width_pt=hy.width_pt,
            height_pt=hy.height_pt,
            words=hy.words,
        )
        self.assertEqual(find_charts.gate_charge_axis_label_spans(native_hy), [])

    def test_tesseract_absence_and_timeout_degrade_to_no_fallback(self) -> None:
        with mock.patch.object(find_charts.shutil, "which", return_value=None):
            self.assertIsNone(find_charts._tesseract_tsv(Path("page.png")))
            self.assertEqual(find_charts.run_tesseract_page_text(Path("sample.pdf")), [])

        with (
            mock.patch.object(find_charts.shutil, "which", return_value="/usr/bin/tesseract"),
            mock.patch.object(
                find_charts.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["tesseract"], 3),
            ),
        ):
            self.assertIsNone(find_charts._tesseract_tsv(Path("page.png"), timeout=3))

    def test_tesseract_uses_argv_without_shell(self) -> None:
        with (
            mock.patch.object(find_charts.shutil, "which", return_value="/usr/bin/tesseract"),
            mock.patch.object(
                find_charts.subprocess,
                "run",
                return_value=SimpleNamespace(stdout="tsv"),
            ) as run,
        ):
            self.assertEqual(find_charts._tesseract_tsv(Path("page.png"), timeout=7), "tsv")

        args, kwargs = run.call_args
        self.assertEqual(
            args[0],
            ["/usr/bin/tesseract", "page.png", "stdout", "--psm", "11", "tsv"],
        )
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["timeout"], 7)

    def test_gate_discovery_skips_ocr_when_normal_finder_has_gate_panel(self) -> None:
        with (
            mock.patch.object(gate, "process_pdf", return_value=[_panel()]),
            mock.patch.object(gate, "run_tesseract_page_text") as ocr,
        ):
            panels, page_text = gate._discover_gate_panels(
                Path("sample.pdf"), Path("out"), 120
            )

        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0].crop_png, "")
        self.assertEqual(page_text, {})
        ocr.assert_not_called()

    def test_gate_discovery_injects_ocr_only_after_zero_normal_gate_panels(self) -> None:
        ocr_page = find_charts.PageText(2, 600.0, 800.0, [], "tesseract_fallback")
        with (
            mock.patch.object(gate, "process_pdf", return_value=[_panel(kind="capacitance")]),
            mock.patch.object(gate, "run_tesseract_page_text", return_value=[ocr_page]) as ocr,
            mock.patch.object(gate, "process_page_texts", return_value=[_panel()]) as injected,
        ):
            panels, page_text = gate._discover_gate_panels(
                Path("sample.pdf"), Path("out"), 120
            )

        self.assertEqual(len(panels), 1)
        self.assertEqual(page_text, {2: ocr_page})
        ocr.assert_called_once_with(Path("sample.pdf"))
        injected.assert_called_once_with(Path("sample.pdf"), Path("out"), 120, [ocr_page])

    def test_dual_y_ocr_uses_numbered_figure_semantics_not_literal_810(self) -> None:
        panel = _panel()
        panel.title = "Dynamic Input/Output Characteristics"
        result = SimpleNamespace(status="unresolved", diagnostics=("gate_charge_unit_unresolved",))

        panel.diagram = 811
        self.assertTrue(gate._needs_dual_y_axis_ocr(panel, result))
        panel.diagram = 901
        self.assertFalse(gate._needs_dual_y_axis_ocr(panel, result))

    def test_arithmetic_x_axis_extrapolates_omitted_zero(self) -> None:
        ticks = [(25.0, 350.0), (50.0, 400.0), (75.0, 450.0), (100.0, 500.0)]

        repaired = gate._x_ticks_with_zero(
            ticks, pymupdf.Rect(295.0, 100.0, 550.0, 400.0)
        )

        self.assertEqual(repaired[0][0], 0.0)
        self.assertAlmostEqual(repaired[0][1], 300.0)
        self.assertEqual(repaired[1:], ticks)
        irregular = [(25.0, 350.0), (50.0, 400.0), (90.0, 450.0)]
        self.assertEqual(
            gate._x_ticks_with_zero(
                irregular, pymupdf.Rect(295.0, 100.0, 550.0, 400.0)
            ),
            irregular,
        )
        irregular_positions = [
            (25.0, 350.0),
            (50.0, 400.0),
            (75.0, 475.0),
            (100.0, 525.0),
        ]
        self.assertEqual(
            gate._x_ticks_with_zero(
                irregular_positions, pymupdf.Rect(295.0, 100.0, 550.0, 400.0)
            ),
            irregular_positions,
        )

    def test_arithmetic_y_axis_extrapolates_signed_or_unsigned_edge_zero(self) -> None:
        panel = pymupdf.Rect(100.0, 100.0, 300.0, 310.0)
        positive = [(16.0, 120.0), (12.0, 160.0), (8.0, 200.0), (4.0, 240.0)]
        negative = [(-20.0, 110.0), (-15.0, 150.0), (-10.0, 190.0), (-5.0, 230.0)]

        self.assertEqual(gate._y_ticks_with_zero(positive, panel)[-1], (0.0, 280.0))
        self.assertEqual(gate._y_ticks_with_zero(negative, panel)[-1], (0.0, 270.0))
        self.assertEqual(
            gate._y_ticks_with_zero(
                [(16.0, 120.0), (12.0, 160.0), (7.0, 200.0)], panel
            ),
            [(16.0, 120.0), (12.0, 160.0), (7.0, 200.0)],
        )

    def test_signed_ocr_y_axis_preserves_negative_values(self) -> None:
        ticks = gate._best_y_axis_for_panel(
            gate._PageWordOverride(
                mock.MagicMock(),
                find_charts.PageText(
                    1,
                    600.0,
                    800.0,
                    [
                        find_charts.Word("—20", 505.0, 100.0, 520.0, 110.0),
                        find_charts.Word("-15", 505.0, 140.0, 520.0, 150.0),
                        find_charts.Word("-10", 505.0, 180.0, 520.0, 190.0),
                        find_charts.Word("-5", 505.0, 220.0, 520.0, 230.0),
                    ],
                    "tesseract_fallback",
                ),
            ),
            pymupdf.Rect(340.0, 90.0, 500.0, 280.0),
        )

        self.assertIsNotNone(ticks)
        assert ticks is not None
        self.assertEqual([value for value, _y in ticks[0]], [-20.0, -15.0, -10.0, -5.0])

    def test_narrow_plateau_crossing_excursion_is_repaired(self) -> None:
        curve = [(x, 200) for x in range(0, 101, 4)]
        curve[10:15] = [(40, 200), (44, 190), (48, 165), (52, 188), (56, 201)]

        repaired = gate._repair_narrow_plateau_branch_excursion(
            curve, (0, 0, 240, 400)
        )

        self.assertEqual(min(y for x, y in curve if 40 <= x <= 56), 165)
        self.assertGreaterEqual(min(y for x, y in repaired if 40 <= x <= 56), 199)

    def test_multiple_small_crossing_excursions_are_repaired_in_both_directions(self) -> None:
        curve = [(x, 200) for x in range(0, 121, 4)]
        curve[6:11] = [(24, 200), (28, 195), (32, 194), (36, 199), (40, 200)]
        curve[16:21] = [(64, 200), (68, 204), (72, 205), (76, 201), (80, 200)]

        repaired = gate._repair_narrow_plateau_branch_excursion(
            curve, (0, 0, 240, 400)
        )

        self.assertEqual({y for x, y in repaired if 24 <= x <= 80}, {200})

    def test_real_rising_segment_is_not_flattened_as_crossing_excursion(self) -> None:
        curve = [(x, 300 - x) for x in range(0, 101, 4)]

        self.assertEqual(
            gate._repair_narrow_plateau_branch_excursion(curve, (0, 0, 200, 400)),
            curve,
        )

    def test_raster_mask_selection_keeps_the_higher_scoring_curve(self) -> None:
        complete = [
            (x, int(round(90 - 0.7 * min(x, 45) - 0.1 * max(0, x - 75))))
            for x in range(5, 96)
        ]
        clipped = [(x, 55) for x in range(35, 65)]

        selected = gate._select_raster_curve([clipped, complete], 100, 100)

        self.assertIs(selected, complete)
        self.assertIs(
            gate._select_raster_curve([complete, clipped], 100, 100), complete
        )

    def test_dual_y_raster_selection_requires_the_qg_vgs_origin(self) -> None:
        wrong_vds_branch = [(x, 25 - x // 20) for x in range(0, 96)]
        owned_vgs_branch = [(x, 96 - min(80, x)) for x in range(0, 96)]

        selected = gate._select_dual_y_raster_curve(
            [wrong_vds_branch, owned_vgs_branch], (0, 0, 100, 100), 100, 100
        )

        self.assertIs(selected, owned_vgs_branch)
        self.assertTrue(gate._curve_starts_at_axis_origin(selected, (0, 0, 100, 100)))

    def test_dual_y_terminal_branch_switch_stops_without_inventing_a_flat(self) -> None:
        curve = [(x, 100 - x) for x in range(0, 81, 4)]
        curve.extend([(84, 26), (88, 24), (92, 21)])

        trimmed = gate._trim_dual_y_terminal_branch_switch(
            curve, (0, 0, 100, 120)
        )

        self.assertEqual(trimmed[-1], (80, 20))

    def test_dual_y_interior_notch_is_retained_when_curve_progresses_after_it(self) -> None:
        curve = [(x, 100 - x) for x in range(0, 61, 4)]
        curve.extend([(64, 46), (68, 32), (72, 28), (76, 24), (80, 20)])

        self.assertEqual(
            curve,
            gate._trim_dual_y_terminal_branch_switch(curve, (0, 0, 100, 120)),
        )

    def test_dual_y_terminal_grid_capture_stops_at_first_flat_point(self) -> None:
        curve = [
            *[(x, 100 - x // 2) for x in range(0, 81, 4)],
            *[(x, 60) for x in range(84, 101, 4)],
        ]

        trimmed = gate._trim_dual_y_terminal_grid_capture(curve, (0, 0, 100, 100))

        self.assertEqual(trimmed[-1], (80, 60))

    def test_real_ocr_gate_charge_backlog_is_numeric_and_local(self) -> None:
        if shutil.which("tesseract") is None:
            self.skipTest("tesseract is not installed")
        root = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"
        cases = {
            "ao/AON6220.pdf": (2.5, 4, "top_left"),
            "infineon/IRF150DM115XTMA1.pdf": (5.7, 9, "top_right"),
            "huayi/HY3912W.pdf": (5.35, 6, "bottom_right"),
        }
        if not all((root / rel).exists() for rel in cases):
            self.skipTest("required OCR regression PDFs are not configured")

        self._assert_real_ocr_cases(root, cases)

    def test_real_optional_sp30_ocr_gate_charge_is_numeric_and_full_width(self) -> None:
        if shutil.which("tesseract") is None:
            self.skipTest("tesseract is not installed")
        root = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"
        cases = {"siliup/SP30N01AGHNP.pdf": (4.8, 3, "middle_right")}
        if not (root / next(iter(cases))).exists():
            self.skipTest("optional SP30 OCR regression PDF is not configured")

        self._assert_real_ocr_cases(root, cases)

    def _assert_real_ocr_cases(
        self,
        root: Path,
        cases: dict[str, tuple[float, int, str]],
    ) -> None:

        for rel, (reference, page_num, position) in cases.items():
            with self.subTest(pdf=rel):
                pdf = root / rel
                result = gate.find_vpl_result(pdf)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.panel.page, page_num)
                self.assertEqual(result.panel.text_source, "tesseract_fallback")
                self.assertAlmostEqual(result.vpl, reference, delta=0.5)
                with pymupdf.open(pdf) as doc:
                    page_rect = doc[page_num - 1].rect
                x0, y0, x1, y1 = result.panel.bbox_pt
                center_x = 0.5 * (x0 + x1)
                center_y = 0.5 * (y0 + y1)
                if position.endswith("right"):
                    self.assertGreater(center_x, 0.5 * page_rect.width)
                if position == "top_left":
                    self.assertLess(center_x, 0.5 * page_rect.width)
                    self.assertLess(center_y, 0.5 * page_rect.height)
                elif position == "bottom_right":
                    self.assertGreater(center_y, 0.5 * page_rect.height)
                elif position == "middle_right":
                    self.assertGreater(center_y, 0.25 * page_rect.height)
                    self.assertLess(center_y, 0.75 * page_rect.height)
                    plot_left = result.crop_box_pt[0] + result.plot_box_px[0] / (
                        result.dpi / 72
                    )
                    self.assertLess(plot_left, 340.0)


class GateChargeOcrContaminationRegression(unittest.TestCase):
    """A panel's OCR retry must not leak into another panel's primary (native) text.

    Regression for PSMB050N10NS2 / PSMB055N08NS1 / PSMP050N10NS2: a page-1
    part-summary 'Gate charge' spec-table match (axis_assumed) triggered a global
    OCR populate that then bled the neighbour Fig.8 normalized 0.9-1.1 axis into
    the correct page-4 Fig.7 native panel, dropping Vpl from the native 4.96 V to
    a bogus ~1.0 V.
    """

    def test_ocr_retry_does_not_contaminate_other_panels_native_extraction(self) -> None:
        import tempfile

        panel_summary = _panel()
        object.__setattr__(panel_summary, "page", 1)
        object.__setattr__(panel_summary, "diagram", 951)
        panel_chart = _panel()
        object.__setattr__(panel_chart, "page", 4)

        calls: list[tuple[int, object]] = []

        def fake_digitize(pdf, doc, panel, dpi, page_text=None):  # noqa: ANN001
            calls.append((panel.page, page_text))
            if panel.page == 1:
                # spec-table match: no real plot -> fail-closed axis_assumed
                return SimpleNamespace(
                    status="axis_assumed", vpl=1.75, y_tick_count=0, panel=panel, score=-1e9
                )
            # page-4 real chart: native gives the true ~5 V plateau; any injected
            # (OCR) page_text simulates the neighbour-axis contamination -> ~1.0 V.
            if page_text is None:
                return SimpleNamespace(
                    status="ok", vpl=4.96, y_tick_count=5, panel=panel, score=26.0
                )
            return SimpleNamespace(
                status="ok", vpl=1.0, y_tick_count=5, panel=panel, score=26.0
            )

        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            with mock.patch.object(
                gate, "_discover_gate_panels", return_value=([panel_summary, panel_chart], {})
            ), mock.patch.object(
                gate, "_digitize_panel", side_effect=fake_digitize
            ), mock.patch.object(
                gate,
                "run_tesseract_page_text",
                return_value=[SimpleNamespace(page_num=1), SimpleNamespace(page_num=4)],
            ), mock.patch.object(
                gate.pymupdf, "open", return_value=mock.MagicMock()
            ):
                results = gate.digitize_gate_charge(tmp.name)

        # The page-4 panel's PRIMARY extraction must have received native text
        # (None), never the OCR text populated by the page-1 panel's retry.
        primary_page4 = [pt for page, pt in calls if page == 4][0]
        self.assertIsNone(primary_page4)

        selected = sorted(results, key=gate._result_sort_key)[0]
        self.assertEqual(selected.panel.page, 4)
        self.assertAlmostEqual(selected.vpl, 4.96, places=2)


class ToshibaDualYAxisOcrRegression(unittest.TestCase):
    def test_real_dynamic_input_output_charts_use_owned_right_vgs_axis(self) -> None:
        if shutil.which("tesseract") is None:
            self.skipTest("tesseract is not installed")
        root = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"
        cases = {
            "toshiba/TK25S06N1L.pdf": (3.75, 1, 810),
            "toshiba/TJ40S04M3L.pdf": (-3.86, -1, 810),
            "toshiba/TPH3R70APL1,LQ.pdf": (3.97, 1, 810),
            "toshiba/TPN2R903PL.pdf": (3.07, 1, 810),
            "toshiba/TPHR8504PL1.pdf": (3.18, 1, 810),
            "toshiba/TK110U65Z.pdf": (5.96, 1, 811),
        }
        if not all((root / rel).exists() for rel in cases):
            self.skipTest("optional Toshiba dual-Y regression PDFs are not configured")

        for rel, (reference, polarity, diagram) in cases.items():
            with self.subTest(pdf=rel):
                result = next(
                    item
                    for item in gate.digitize_gate_charge(root / rel)
                    if item.panel.page == 6 and item.panel.diagram == diagram
                )
                self.assertEqual(result.status, "ok")
                self.assertEqual(result.x_tick_unit, "nC")
                self.assertIn("axis_ocr_bounded_dual_y", result.diagnostics)
                self.assertGreaterEqual(result.y_tick_count, 4)
                self.assertAlmostEqual(result.vpl, reference, delta=0.25)
                assert result.vpl is not None
                self.assertEqual(1 if result.vpl > 0 else -1, polarity)
                y_values = [value for value, _y in result.y_ticks_px]
                if polarity < 0:
                    self.assertLess(max(y_values[:-1]), 0.0)
                    self.assertEqual(y_values[-1], 0.0)
                else:
                    self.assertGreater(y_values[0], 0.0)
                    self.assertEqual(y_values[-1], 0.0)
                self.assertTrue(
                    gate._curve_starts_at_axis_origin(
                        list(result.curve_px), result.plot_box_px
                    )
                )
                plot_x0, _plot_y0, plot_x1, _plot_y1 = result.plot_box_px
                terminal_start = plot_x0 + 0.55 * (plot_x1 - plot_x0)
                material_reverse = max(
                    current[1] - previous[1]
                    for previous, current in zip(result.curve_px, result.curve_px[1:])
                    if current[0] >= terminal_start
                )
                self.assertLess(material_reverse, 5)

                if "TPH3R70" in rel:
                    x0, y0, x1, y1 = result.plot_box_px
                    plateau = [
                        y
                        for x, y in result.curve_px
                        if x0 + 0.21 * (x1 - x0) <= x <= x0 + 0.37 * (x1 - x0)
                    ]
                    self.assertTrue(plateau)
                    self.assertLessEqual(max(plateau) - min(plateau), 0.03 * (y1 - y0))
                    self.assertLess(result.curve_px[-1][0], x0 + 0.9 * (x1 - x0))


if __name__ == "__main__":
    unittest.main()
