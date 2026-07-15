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

    def test_ocr_arithmetic_x_axis_extrapolates_omitted_zero(self) -> None:
        ticks = [(25.0, 350.0), (50.0, 400.0), (75.0, 450.0), (100.0, 500.0)]

        repaired = gate._ocr_x_ticks_with_zero(
            ticks, pymupdf.Rect(295.0, 100.0, 550.0, 400.0)
        )

        self.assertEqual(repaired[0][0], 0.0)
        self.assertAlmostEqual(repaired[0][1], 300.0)
        self.assertEqual(repaired[1:], ticks)
        irregular = [(25.0, 350.0), (50.0, 400.0), (90.0, 450.0)]
        self.assertEqual(
            gate._ocr_x_ticks_with_zero(
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
            gate._ocr_x_ticks_with_zero(
                irregular_positions, pymupdf.Rect(295.0, 100.0, 550.0, 400.0)
            ),
            irregular_positions,
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


if __name__ == "__main__":
    unittest.main()
