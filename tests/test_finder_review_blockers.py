import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from datasheet_chart_digitizer import find_charts


class FinderProductionPathGuardTests(unittest.TestCase):
    def test_gate_charge_measurement_circuit_is_not_data(self) -> None:
        self.assertEqual(
            find_charts.classify_chart("Gate Charge Measurement Circuit", ""),
            "chart",
        )

    def test_paired_nonnumeric_gate_waveform_is_definition(self) -> None:
        siblings = [(21, "Gate Charge Measurement Circuit"), (22, "Gate Charge Waveform")]
        self.assertTrue(find_charts.paired_gate_charge_waveform_is_definition(
            "Gate Charge Waveform", 22, siblings, "VGS Qg Qgs Qgd Charge",
        ))
        self.assertFalse(find_charts.paired_gate_charge_waveform_is_definition(
            "Gate Charge Waveform", 22, siblings, "0 10 20 Qg (nC)",
        ))

    def test_capacitance_and_switching_rows_are_spec_table_evidence(self) -> None:
        page = find_charts.PageText(1, 600, 800, [
            find_charts.Word(text, 20, y, 160, y + 10)
            for text, y in (
                ("Output capacitance", 100),
                ("Reverse transfer capacitance", 120),
                ("Turn-on delay time", 140),
            )
        ])
        self.assertTrue(find_charts._bbox_looks_like_spec_table(
            page, (0, 90, 200, 170), own_families=frozenset({"charge"}),
        ))

    def test_bbox_process_failure_falls_back_to_pymupdf_words(self) -> None:
        fallback = [
            find_charts.PageText(
                page_num=1,
                width_pt=100.0,
                height_pt=200.0,
                words=[find_charts.Word("Transfer", 10.0, 20.0, 40.0, 30.0)],
                text_source="pymupdf_bbox_fallback",
            )
        ]
        failure = find_charts.subprocess.CalledProcessError(134, ["pdftotext"])

        with (
            patch.object(find_charts.subprocess, "run", side_effect=failure),
            patch.object(find_charts, "_run_pymupdf_text", return_value=fallback) as retry,
        ):
            self.assertEqual(find_charts.run_text_bbox(Path("broken.pdf")), fallback)

        retry.assert_called_once_with(Path("broken.pdf"), "pymupdf_bbox_fallback")

    def test_empty_bbox_output_falls_back_to_pymupdf_words(self) -> None:
        completed = find_charts.subprocess.CompletedProcess(
            ["pdftotext"], 0, stdout="", stderr=""
        )
        fallback = [
            find_charts.PageText(
                page_num=1,
                width_pt=100.0,
                height_pt=200.0,
                words=[find_charts.Word("VGS", 10.0, 20.0, 30.0, 30.0)],
                text_source="pymupdf_bbox_fallback",
            )
        ]

        with (
            patch.object(find_charts.subprocess, "run", return_value=completed),
            patch.object(find_charts, "_run_pymupdf_text", return_value=fallback),
        ):
            self.assertEqual(find_charts.run_text_bbox(Path("empty.pdf")), fallback)

    def test_well_formed_bbox_without_pages_falls_back(self) -> None:
        completed = find_charts.subprocess.CompletedProcess(
            ["pdftotext"], 0, stdout="<doc></doc>", stderr=""
        )
        fallback = [
            find_charts.PageText(
                page_num=1,
                width_pt=100.0,
                height_pt=200.0,
                words=[find_charts.Word("VGS", 10.0, 20.0, 30.0, 30.0)],
                text_source="pymupdf_bbox_fallback",
            )
        ]
        with (
            patch.object(find_charts.subprocess, "run", return_value=completed),
            patch.object(find_charts, "_run_pymupdf_text", return_value=fallback) as retry,
        ):
            self.assertEqual(find_charts.run_text_bbox(Path("no-pages.pdf")), fallback)
        retry.assert_called_once_with(
            Path("no-pages.pdf"), "pymupdf_bbox_fallback"
        )

    def test_well_formed_bbox_with_only_wordless_pages_falls_back(self) -> None:
        completed = find_charts.subprocess.CompletedProcess(
            ["pdftotext"],
            0,
            stdout='<doc><page width="100" height="200"></page></doc>',
            stderr="",
        )
        fallback = [
            find_charts.PageText(
                page_num=1,
                width_pt=100.0,
                height_pt=200.0,
                words=[find_charts.Word("ID", 10.0, 20.0, 30.0, 30.0)],
                text_source="pymupdf_bbox_fallback",
            )
        ]
        with (
            patch.object(find_charts.subprocess, "run", return_value=completed),
            patch.object(find_charts, "_run_pymupdf_text", return_value=fallback) as retry,
        ):
            self.assertEqual(find_charts.run_text_bbox(Path("no-words.pdf")), fallback)
        retry.assert_called_once_with(
            Path("no-words.pdf"), "pymupdf_bbox_fallback"
        )

    def test_malformed_bbox_xml_falls_back(self) -> None:
        completed = find_charts.subprocess.CompletedProcess(
            ["pdftotext"], 0, stdout="<doc><page>", stderr=""
        )
        fallback = [
            find_charts.PageText(
                page_num=1,
                width_pt=100.0,
                height_pt=200.0,
                words=[find_charts.Word("ID", 10.0, 20.0, 30.0, 30.0)],
                text_source="pymupdf_bbox_fallback",
            )
        ]
        with (
            patch.object(find_charts.subprocess, "run", return_value=completed),
            patch.object(find_charts, "_run_pymupdf_text", return_value=fallback),
        ):
            self.assertEqual(find_charts.run_text_bbox(Path("malformed.pdf")), fallback)

    def test_wordless_primary_and_wordless_fallback_refuse_explicitly(self) -> None:
        completed = find_charts.subprocess.CompletedProcess(
            ["pdftotext"], 0, stdout="<doc></doc>", stderr=""
        )
        with (
            patch.object(find_charts.subprocess, "run", return_value=completed),
            patch.object(
                find_charts,
                "_run_pymupdf_text",
                side_effect=RuntimeError("PyMuPDF fallback yielded no page words"),
            ),
            self.assertRaisesRegex(RuntimeError, "no page words"),
        ):
            find_charts.run_text_bbox(Path("terminal-wordless.pdf"))

    def test_stl135_text_extraction_completes(self) -> None:
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st/STL135N8F7AG.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")

        pages = find_charts._run_pymupdf_text(pdf, "pymupdf_bbox_fallback")

        self.assertEqual(len(pages), 15)
        self.assertTrue(
            all(page.text_source == "pymupdf_bbox_fallback" for page in pages)
        )
        self.assertGreater(sum(len(page.words) for page in pages), 500)
        with TemporaryDirectory(prefix="stl135-pymupdf-fallback-") as tmp:
            panels = find_charts.process_page_texts(pdf, Path(tmp), 180, pages)
        self.assertEqual(
            [(panel.page, panel.diagram) for panel in panels],
            [(4, 951), (6, 5), (6, 6), (7, 8), (7, 11), (7, 12)],
        )

    def test_toshiba_compact_formula_titles_route_only_supported_axes(self) -> None:
        expected = {
            "I D - V GS": "transfer",
            "R DS(ON) - I D": "rds_on",
            "R DS(ON) - T a": "rds_on",
            "I DR - V DS": "body_diode",
            "V (BR)DSS - T a": "breakdown_voltage",
        }
        for title, kind in expected.items():
            with self.subTest(title=title):
                self.assertEqual(find_charts.classify_chart(title, ""), kind)

        self.assertEqual(find_charts.rdson_formula_direction("R DS(ON) - I D"), "current")
        self.assertEqual(find_charts.rdson_formula_direction("R DS(ON) - T a"), "temperature")
        for unsupported in ("I D - V DS", "V DS - V GS", "V th - T a", "R DS(ON) - V GS"):
            with self.subTest(unsupported=unsupported):
                self.assertEqual(find_charts.classify_chart(unsupported, ""), "chart" if not unsupported.startswith("R DS") else "rds_on")
                self.assertIsNone(find_charts.rdson_formula_direction(unsupported))

    def test_toshiba_compact_formula_captions_own_the_preceding_frames(self) -> None:
        pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/toshiba/XPW4R10ANB.pdf")
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        page = find_charts.run_text_bbox(pdf)[6]
        with TemporaryDirectory(prefix="toshiba-compact-formulas-") as tmp:
            panels = find_charts.process_page_texts(pdf, Path(tmp), 180, [page])

        self.assertEqual(
            [(panel.diagram, panel.kind) for panel in panels],
            [(87, "transfer"), (88, "rds_on"), (89, "rds_on"),
             (810, "body_diode"), (811, "breakdown_voltage")],
        )
        by_diagram = {panel.diagram: panel for panel in panels}
        for left, right in ((87, 88), (89, 810)):
            self.assertLess(by_diagram[left].bbox_pt[2], 306.0)
            self.assertGreater(by_diagram[right].bbox_pt[0], 306.0)
            self.assertAlmostEqual(by_diagram[left].bbox_pt[1], by_diagram[right].bbox_pt[1], delta=1.0)
        self.assertLess(by_diagram[88].bbox_pt[3], by_diagram[89].bbox_pt[1])
        self.assertLess(by_diagram[810].bbox_pt[3], by_diagram[811].bbox_pt[1])

    def test_mcc_compact_formula_captions_own_the_following_frames(self) -> None:
        pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/mcc/MCP118N085Y-BP.pdf")
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with TemporaryDirectory(prefix="mcc-compact-formulas-") as tmp:
            panels = find_charts.process_pdf(pdf, Path(tmp), dpi=180)
        by_diagram = {panel.diagram: panel for panel in panels}
        self.assertGreater(by_diagram[3].bbox_pt[1], 320.0)
        self.assertGreater(by_diagram[5].bbox_pt[1], 550.0)
        self.assertEqual(by_diagram[3].kind, "rds_on")
        self.assertEqual(by_diagram[5].kind, "body_diode")

    def test_toshiba_formula_synthetic_crop_stays_in_its_column(self) -> None:
        pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/toshiba/SSM6N815R.pdf")
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with TemporaryDirectory(prefix="toshiba-formula-column-") as tmp:
            panel = next(
                row for row in find_charts.process_pdf(pdf, Path(tmp), dpi=180)
                if row.page == 5 and row.diagram == 75
            )
        self.assertLess(panel.bbox_pt[2] - panel.bbox_pt[0], 275.0)
        self.assertLess(panel.bbox_pt[2], 330.0)

    def test_composite_toshiba_body_diode_caption_owns_preceding_plot(self) -> None:
        pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/toshiba/SSM6N55NU.pdf")
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with TemporaryDirectory(prefix="toshiba-composite-body-diode-") as tmp:
            panel = next(
                row for row in find_charts.process_pdf(pdf, Path(tmp), dpi=180)
                if row.page == 6 and row.diagram == 99
            )
        self.assertLess(panel.bbox_pt[3], 480.0)

    def test_fused_next_caption_splits_mcc_formula_grid_rows(self) -> None:
        pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/mcc/MCU80N06AHE3-TP.pdf")
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with TemporaryDirectory(prefix="mcc-fused-next-caption-") as tmp:
            panel = next(
                row for row in find_charts.process_pdf(pdf, Path(tmp), dpi=180)
                if row.page == 4 and row.diagram == 9
            )
        self.assertLess(panel.bbox_pt[3] - panel.bbox_pt[1], 240.0)
        self.assertLess(panel.bbox_pt[3], 560.0)

    def test_spec_table_header_run_is_not_a_chart_caption(self) -> None:
        self.assertTrue(
            find_charts.is_spec_table_header_title(
                "Gate Charge Characteristics Symbol Test Condition Min Typ Max"
            )
        )
        for title in (
            "Gate Charge Characteristics",
            "Dynamic Input/Output Characteristics",
            "Gate Charge Characteristics (typ.)",
        ):
            with self.subTest(title=title):
                self.assertFalse(find_charts.is_spec_table_header_title(title))

    def test_toshiba_table_heading_is_rejected_but_real_gate_chart_remains(self) -> None:
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/toshiba/TK25S06N1L.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with TemporaryDirectory(prefix="toshiba-table-heading-") as tmp:
            panels = find_charts.process_pdf(pdf, Path(tmp), dpi=180)

        self.assertFalse(any(panel.diagram == 901 for panel in panels))
        self.assertIn(
            (6, 810, "gate_charge"),
            [(panel.page, panel.diagram, panel.kind) for panel in panels],
        )

    def test_toshiba_dynamic_caption_binds_to_gate_charge_frame_above(self) -> None:
        corpus = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/toshiba")
        pdfs = [corpus / "TPH3R70APL1,LQ.pdf", corpus / "TPN2R903PL.pdf"]
        if any(not pdf.exists() for pdf in pdfs):
            self.skipTest("missing local Toshiba gate-charge fixtures")

        for pdf in pdfs:
            with self.subTest(pdf=pdf.name), TemporaryDirectory(
                prefix="toshiba-dynamic-caption-"
            ) as tmp:
                panels = find_charts.process_pdf(pdf, Path(tmp), dpi=180)
                gate = next(
                    panel
                    for panel in panels
                    if panel.page == 6 and panel.diagram == 810
                )

                self.assertEqual(gate.kind, "gate_charge")
                self.assertEqual(gate.title, "Dynamic Input/Output Characteristics")
                self.assertLess(gate.bbox_pt[0], 315.0)
                self.assertLess(gate.bbox_pt[1], 320.0)
                self.assertGreater(gate.bbox_pt[2], 535.0)
                self.assertGreater(gate.bbox_pt[3], 440.0)
                self.assertLess(gate.bbox_pt[3], 470.0)

        single_y = corpus / "SSM3K76FS.pdf"
        if single_y.exists():
            with TemporaryDirectory(prefix="toshiba-single-y-dynamic-") as tmp:
                panels = find_charts.process_pdf(single_y, Path(tmp), dpi=180)
            gate = next(panel for panel in panels if panel.diagram == 711)
            self.assertGreater(gate.bbox_pt[0], 100.0)
            self.assertLess(gate.bbox_pt[2], 285.0)

    def test_st_short_titles_bind_only_to_their_own_vector_frames(self) -> None:
        pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st/STD30NF06.pdf")
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with TemporaryDirectory(prefix="std30nf06-finder-") as tmp:
            panels = find_charts.process_pdf(pdf, Path(tmp), dpi=180)
            bottom_band_min = {}
            for panel in panels:
                if panel.page == 4 and panel.kind in {"gate_charge", "capacitances"}:
                    with Image.open(Path(tmp) / panel.crop_png).convert("L") as crop:
                        bottom_band_min[panel.kind] = crop.crop(
                            (0, crop.height - 5, crop.width, crop.height)
                        ).getextrema()[0]

        page_four = [panel for panel in panels if panel.page == 4]
        self.assertEqual(
            [(panel.kind, panel.title) for panel in page_four],
            [
                ("transfer", "Transfer Characteristics"),
                ("rds_on", "Static Drain-source On Resistance"),
                ("gate_charge", "Gate Charge vs Gate-source Voltage"),
                ("capacitances", "Capacitance Variations"),
            ],
        )
        self.assertFalse(any(panel.kind in {"output", "chart"} for panel in page_four))
        for panel in page_four:
            with self.subTest(kind=panel.kind):
                self.assertLess(panel.bbox_pt[0], panel.bbox_pt[2])
                self.assertLess(panel.bbox_pt[1], panel.bbox_pt[3])
                self.assertLess(panel.bbox_pt[3] - panel.bbox_pt[1], 260.0)
                self.assertNotIn("4/10", panel.text)
        for panel in page_four[-2:]:
            self.assertLess(panel.bbox_pt[3], 743.0)
            self.assertGreater(bottom_band_min[panel.kind], 245)

    def test_grid_region_bridges_one_missing_row_without_crossing_caption(self) -> None:
        rows = [100.0, 120.0, 140.0, 190.0, 210.0, 230.0]
        rules = [(100.0, y, 280.0, y + 1.0) for y in rows]
        page = find_charts.PageText(1, 600, 800, [])

        self.assertEqual(
            find_charts.infer_grid_regions_from_h_rules(page, rules),
            [(100.0, 100.5, 280.0, 230.5)],
        )

        with_caption = find_charts.PageText(
            1,
            600,
            800,
            [find_charts.Word("Figure", 150, 160, 190, 170)],
        )
        self.assertEqual(
            find_charts.infer_grid_regions_from_h_rules(with_caption, rules), []
        )

        caption_just_outside_plot_column = find_charts.PageText(
            1,
            600,
            800,
            [find_charts.Word("Figure", 64, 160, 98, 170)],
        )
        self.assertEqual(
            find_charts.infer_grid_regions_from_h_rules(
                caption_just_outside_plot_column, rules
            ),
            [],
        )

    def test_detached_figure_number_inside_column_is_a_caption_barrier(self) -> None:
        rows = [100.0, 120.0, 140.0, 190.0, 210.0, 230.0]
        rules = [(100.0, y, 280.0, y + 1.0) for y in rows]
        page = find_charts.PageText(
            1,
            600,
            800,
            [
                find_charts.Word("Figure", 54, 160, 88, 170),
                find_charts.Word("12.", 90, 160, 103, 170),
            ],
        )

        self.assertEqual(find_charts.infer_grid_regions_from_h_rules(page, rules), [])

        standalone_number = find_charts.PageText(
            1,
            600,
            800,
            [find_charts.Word("12.", 90, 160, 103, 170)],
        )
        self.assertEqual(
            find_charts.infer_grid_regions_from_h_rules(standalone_number, rules),
            [(100.0, 100.5, 280.0, 230.5)],
        )

    def test_stp15nk50z_breakdown_owns_only_figure_12_row(self) -> None:
        pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st/STP15NK50Z.pdf")
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with TemporaryDirectory(prefix="stp15nk50z-finder-") as tmp:
            panels = find_charts.process_pdf(pdf, Path(tmp), dpi=180)
        panel = next(
            row
            for row in panels
            if row.page == 6 and row.diagram == 12 and row.kind == "breakdown_voltage"
        )

        self.assertLess(panel.bbox_pt[3] - panel.bbox_pt[1], 190.0)
        self.assertGreater(panel.bbox_pt[1], 540.0)
        self.assertLess(panel.bbox_pt[3], 720.0)

    def test_outer_cell_rails_do_not_absorb_narrow_inner_plot_grid(self) -> None:
        page = find_charts.PageText(1, 600, 800, [])
        outer = [(40.0, y, 555.0, y + 1.0) for y in (530.0, 730.0)]
        inner = [(230.0, y, 380.0, y + 1.0) for y in range(560, 711, 25)]

        self.assertEqual(
            find_charts.infer_grid_regions_from_h_rules(page, outer + inner),
            [(230.0, 560.5, 380.0, 710.5)],
        )

    def test_hyphenated_ti_axis_is_direction_evidence_but_condition_is_not(self) -> None:
        title = find_charts.DiagramTitle(
            3,
            "Transfer Characteristics",
            (380, 275, 510, 284),
            "Figure 3. Transfer Characteristics",
        )
        axis_words = [
            find_charts.Word("VGS", 400, 230, 420, 240),
            find_charts.Word("-", 424, 230, 428, 240),
            find_charts.Word("Gate-to-Source", 432, 230, 510, 240),
            find_charts.Word("Voltage", 514, 230, 550, 240),
            find_charts.Word("-", 554, 230, 558, 240),
            find_charts.Word("V", 562, 230, 568, 240),
        ]
        page = find_charts.PageText(1, 612, 792, axis_words)
        self.assertEqual(
            find_charts.caption_axis_direction(
                page, title, "transfer", find_charts._token_norm
            ),
            "above",
        )

        condition = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("VGS", 400, 230, 420, 240),
                find_charts.Word("=", 424, 230, 428, 240),
                find_charts.Word("5V", 432, 230, 446, 240),
            ],
        )
        self.assertIsNone(
            find_charts.caption_axis_direction(
                condition, title, "transfer", find_charts._token_norm
            )
        )

    @staticmethod
    def _blank_page(path: Path) -> Path:
        Image.new("RGB", (600, 800), "white").save(path)
        return path

    def _run_page(self, page: find_charts.PageText):
        with TemporaryDirectory(prefix="finder-production-guard-") as tmp:
            root = Path(tmp)
            page_png = self._blank_page(root / "page.png")
            with (
                patch.object(find_charts, "render_page", return_value=page_png),
                patch.object(find_charts, "detect_rule_boxes", return_value=([], [])),
                patch.object(
                    find_charts,
                    "choose_panel_bbox",
                    return_value=(60.0, 230.0, 300.0, 500.0),
                ),
                patch.object(
                    find_charts,
                    "choose_caption_panel_bbox",
                    return_value=(300.0, 100.0, 600.0, 300.0),
                ),
                patch.object(find_charts, "_page_image_rects", return_value=[]),
            ):
                return find_charts.process_page_texts(
                    root / "fixture.pdf", root / "out", 72, [page]
                )

    def test_wrapped_diagram_test_circuit_is_non_data_in_production(self) -> None:
        page = find_charts.PageText(
            1,
            600,
            800,
            [
                find_charts.Word("Diagram", 60, 200, 102, 210),
                find_charts.Word("5:", 106, 200, 118, 210),
                find_charts.Word("Gate", 122, 200, 150, 210),
                find_charts.Word("Charge", 155, 200, 196, 210),
                find_charts.Word("Test", 122, 211, 149, 221),
                find_charts.Word("Circuit", 154, 211, 193, 221),
                find_charts.Word("&", 198, 211, 206, 221),
                find_charts.Word("Waveform", 211, 211, 267, 221),
            ],
        )

        panels = self._run_page(page)

        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0].title, "Gate Charge Test Circuit & Waveform")
        self.assertEqual(panels[0].kind, "chart")

    def test_wrapped_diagram_measurement_remains_gate_charge(self) -> None:
        page = find_charts.PageText(
            1,
            600,
            800,
            [
                find_charts.Word("Diagram", 60, 200, 102, 210),
                find_charts.Word("5:", 106, 200, 118, 210),
                find_charts.Word("Gate", 122, 200, 150, 210),
                find_charts.Word("Charge", 155, 200, 196, 210),
                find_charts.Word("Characteristics", 122, 211, 202, 221),
            ],
        )

        panels = self._run_page(page)

        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0].title, "Gate Charge Characteristics")
        self.assertEqual(panels[0].kind, "gate_charge")

    def test_capacitance_uses_aligned_own_tick_run_in_production(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("Figure", 340, 80, 375, 90),
                find_charts.Word("6.", 380, 80, 390, 90),
                find_charts.Word("Capacitance", 395, 80, 465, 90),
                find_charts.Word("Characteristics", 470, 80, 555, 90),
                find_charts.Word("130", 280, 120, 298, 128),
                find_charts.Word("120", 280, 180, 298, 188),
                find_charts.Word("110", 280, 240, 298, 248),
                find_charts.Word("175", 242, 240, 262, 248),
            ],
        )

        panels = self._run_page(page)

        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0].kind, "capacitances")
        self.assertEqual(panels[0].bbox_pt[0], 278.0)

    def test_right_column_midpoint_clamp_preserves_evidenced_axis_gutter(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("100", 280, 120, 298, 128),
                find_charts.Word("10", 284, 180, 298, 188),
                find_charts.Word("1", 290, 240, 298, 248),
                find_charts.Word("(nC)", 170, 286, 195, 298),
                find_charts.Word("(V)", 430, 286, 448, 298),
            ],
        )
        title = find_charts.DiagramTitle(
            2,
            "Typical Transfer Characteristics",
            (360, 80, 520, 90),
            "Figure 2. Typical Transfer Characteristics",
        )

        bbox = find_charts._bound_caption_bbox_to_own_column(
            page, title, (300, 100, 590, 300)
        )

        self.assertEqual(bbox, (278.0, 100, 590, 300))

    def test_mixed_revision_page_keeps_chart_outside_table_band(self) -> None:
        page = find_charts.PageText(
            1,
            600,
            800,
            [
                find_charts.Word("Revision", 20, 20, 70, 30),
                find_charts.Word("history", 74, 20, 120, 30),
                find_charts.Word("Date", 20, 40, 45, 50),
                find_charts.Word("Revision", 50, 40, 100, 50),
                find_charts.Word("Changes", 105, 40, 150, 50),
                find_charts.Word("Figure", 20, 70, 55, 80),
                find_charts.Word("4.", 60, 70, 70, 80),
                find_charts.Word("Transfer", 75, 70, 120, 80),
                find_charts.Word("Characteristics", 125, 70, 200, 80),
                find_charts.Word("Qg", 220, 70, 235, 80),
                find_charts.Word("Gate", 240, 70, 268, 80),
                find_charts.Word("Charge", 272, 70, 313, 80),
                find_charts.Word("(nC)", 318, 70, 340, 80),
                find_charts.Word("Figure", 340, 200, 375, 210),
                find_charts.Word("5.", 380, 200, 390, 210),
                find_charts.Word("Typical", 395, 200, 440, 210),
                find_charts.Word("Transfer", 445, 200, 495, 210),
                find_charts.Word("Characteristics", 500, 200, 585, 210),
            ],
        )

        panels = self._run_page(page)

        self.assertEqual([(panel.diagram, panel.kind) for panel in panels], [(5, "transfer")])

    def test_unicode_superscript_caption_number_fails_closed(self) -> None:
        for text in ("10² Gate charge", "² Gate charge"):
            with self.subTest(text=text):
                self.assertIsNone(find_charts._parse_caption_text(text))

    def test_supported_caption_stops_at_explicit_unsupported_neighbor_figure(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("Fig.7", 80, 130, 101, 140),
                find_charts.Word("Typ.", 103, 130, 121, 140),
                find_charts.Word("forward", 123, 130, 155, 140),
                find_charts.Word("characteristics", 157, 130, 218, 140),
                find_charts.Word("of", 220, 130, 228, 140),
                find_charts.Word("body", 230, 130, 251, 140),
                find_charts.Word("diode", 253, 130, 276, 140),
                find_charts.Word("Fig.8", 379, 130, 400, 140),
                find_charts.Word("Safe", 402, 130, 420, 140),
                find_charts.Word("operating", 422, 130, 462, 140),
                find_charts.Word("area", 464, 130, 482, 140),
            ],
        )

        titles = find_charts.find_caption_titles(page)

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].title, "Typ. forward characteristics of body diode")
        self.assertLess(titles[0].bbox_pt[2], 300)

    def test_body_millivolt_axis_below_caption_is_direction_evidence(self) -> None:
        page = find_charts.PageText(
            1,
            595,
            842,
            [
                find_charts.Word("V", 346, 544, 351, 549),
                find_charts.Word("SD", 351, 548, 358, 553),
                find_charts.Word("(mV)", 347, 554, 359, 560),
            ],
        )
        title = find_charts.DiagramTitle(
            12,
            "Typical reverse diode forward characteristics",
            (306, 525, 544, 535),
            "Figure 12. Typical reverse diode forward characteristics",
        )

        self.assertEqual(
            find_charts.caption_axis_direction(
                page, title, "body_diode", find_charts._token_norm
            ),
            "below",
        )

    def test_capacitance_vds_with_numeric_tick_run_is_direction_evidence(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("0", 112, 716, 116, 723),
                find_charts.Word("100", 137, 716, 149, 723),
                find_charts.Word("200", 165, 716, 178, 723),
                find_charts.Word("V", 266, 728, 272, 737),
                find_charts.Word("DS", 272, 731, 283, 739),
            ],
        )
        title = find_charts.DiagramTitle(
            15, "Typ. capacitances", (78, 435, 192, 446), "15 Typ. capacitances"
        )

        self.assertEqual(
            find_charts.caption_axis_direction(
                page, title, "capacitances", find_charts._token_norm
            ),
            "below",
        )

    def test_capacitance_three_glyph_vds_axis_leads_to_plot_below(self) -> None:
        page = find_charts.PageText(
            1,
            595,
            842,
            [
                find_charts.Word("V", 120, 759, 126, 773),
                find_charts.Word("D", 126, 762, 132, 772),
                find_charts.Word("s", 132, 759, 137, 773),
                find_charts.Word("Drain-Source", 139, 759, 198, 773),
                find_charts.Word("Voltage(V)", 201, 759, 248, 773),
            ],
        )
        title = find_charts.DiagramTitle(
            5, "Capacitance vs Vds", (103, 556, 241, 570), "Figure 5. Capacitance vs Vds"
        )

        self.assertEqual(
            find_charts.caption_axis_direction(
                page, title, "capacitances", find_charts._token_norm
            ),
            "below",
        )

    def test_body_diode_bracketed_vsd_axis_leads_to_plot_below(self) -> None:
        page = find_charts.PageText(
            1,
            595,
            842,
            [
                find_charts.Word("V", 389, 475, 393, 483),
                find_charts.Word("SD", 393, 480, 398, 485),
                find_charts.Word("Source-Drain", 402, 475, 440, 483),
                find_charts.Word("voltage", 442, 475, 462, 483),
                find_charts.Word("[V]", 464, 475, 472, 483),
            ],
        )
        title = find_charts.DiagramTitle(
            4,
            "Body Diode Forward Voltage",
            (336, 292, 517, 303),
            "Figure 4. Body Diode Forward Voltage",
        )

        self.assertEqual(
            find_charts.caption_axis_direction(
                page, title, "body_diode", find_charts._token_norm
            ),
            "below",
        )

    def test_numbered_breakdown_caption_does_not_use_nearer_grid_fallback(self) -> None:
        page = find_charts.PageText(1, 612, 792, [])
        title = find_charts.DiagramTitle(
            10,
            "Drain-to-Source Breakdown Voltage",
            (361, 493, 558, 505),
            "Fig 10. Drain-to-Source Breakdown Voltage",
        )

        bbox = find_charts.choose_caption_panel_bbox(
            page,
            title,
            [(368, 315, 536, 449), (368, 525, 536, 608)],
        )

        self.assertIsNone(bbox)

    def test_numbered_gate_definition_synthetic_crop_stays_above_caption(self) -> None:
        page = find_charts.PageText(1, 595, 842, [])
        title = find_charts.DiagramTitle(
            14,
            "Gate charge waveform definitions",
            (52, 286, 233, 299),
            "Fig. 14. Gate charge waveform definitions",
        )

        bbox = find_charts.choose_caption_synthetic_bbox(page, title)

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLess(bbox[3], title.bbox_pt[1])


class BreakdownOwnershipGuardTests(unittest.TestCase):
    def test_numbered_breakdown_uses_adjacent_closed_frame_below_near_tie(self) -> None:
        page = find_charts.PageText(6, 595, 842, [])
        title = find_charts.DiagramTitle(
            11,
            "Normalized breakdown voltage vs temperature",
            (48, 525, 292, 535),
            "Figure 11. Normalized breakdown voltage vs temperature",
        )

        bbox = find_charts._numbered_breakdown_vector_frame_bbox(
            page,
            title,
            [(107, 350, 257, 511), (107, 549, 257, 698)],
        )

        self.assertEqual(bbox, (107, 549, 257, 698))

    def test_breakdown_axis_label_expands_only_from_local_semantic_gutter(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("V", 81.2, 259.0, 90.2, 265.6),
                find_charts.Word("(BR)DSS", 84.3, 225.6, 91.8, 259.0),
                find_charts.Word("600", 99.7, 177.5, 112.2, 184.5),
                find_charts.Word("570", 99.7, 220.7, 112.2, 227.7),
            ],
        )

        expanded = find_charts._expand_caption_bbox_to_axis_labels(
            page, (107.0, 166.9, 294.0, 406.7), "breakdown_voltage"
        )

        self.assertAlmostEqual(expanded[0], 79.2)

    def test_synthetic_breakdown_without_axis_evidence_fails_closed(self) -> None:
        page = find_charts.PageText(1, 600, 800, [])
        title = find_charts.DiagramTitle(
            10,
            "Drain-to-Source Breakdown Voltage",
            (350.0, 480.0, 540.0, 492.0),
            "Fig 10. Drain-to-Source Breakdown Voltage",
        )

        self.assertIsNone(find_charts.choose_caption_synthetic_bbox(page, title))

    def test_breakdown_semantics_include_local_left_axis_gutter(self) -> None:
        words = [
            find_charts.Word("V", 343, 336, 349, 346),
            find_charts.Word("(BR)DSS", 349, 336, 364, 351),
        ]

        self.assertTrue(
            find_charts.bbox_evidences_breakdown(
                words, (364, 339, 516, 490), find_charts._token_norm
            )
        )


class TransferAxisGutterOwnershipTests(unittest.TestCase):
    def test_local_semantic_axis_titles_expand_transfer_crop(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("A", 328.4, 122.6, 334.7, 127.2),
                find_charts.Word("Drain-to-Source", 328.4, 158.1, 334.7, 206.5),
                find_charts.Word("Current", 328.4, 133.3, 334.7, 156.2),
                find_charts.Word("V", 397.3, 246.3, 401.9, 252.6),
                find_charts.Word("Gate-to-Source", 415.0, 246.3, 461.9, 252.6),
                find_charts.Word("Voltage", 463.8, 246.3, 487.1, 252.6),
            ],
        )
        bbox = (347.8, 102.7, 545.7, 241.3)

        expanded = find_charts._expand_caption_bbox_to_axis_labels(
            page, bbox, "transfer"
        )

        self.assertLess(expanded[0], 328.4)
        self.assertGreater(expanded[3], 252.6)
        self.assertLess(expanded[3], 275.0)

    def test_condition_callout_does_not_expand_transfer_crop(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("VGS", 410.0, 246.0, 425.0, 252.0),
                find_charts.Word("=", 427.0, 246.0, 431.0, 252.0),
                find_charts.Word("5V", 433.0, 246.0, 443.0, 252.0),
            ],
        )
        bbox = (347.8, 102.7, 545.7, 241.3)

        self.assertEqual(
            find_charts._expand_caption_bbox_to_axis_labels(
                page, bbox, "transfer"
            ),
            bbox,
        )

    def test_local_symbolic_id_and_vgs_axes_expand_transfer_crop(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("(A)", 316, 228, 326, 237),
                find_charts.Word("D", 318, 237, 326, 241),
                find_charts.Word("I", 316, 241, 326, 243),
                find_charts.Word("V", 424, 318, 430, 331),
                find_charts.Word("GS", 430, 322, 437, 330),
            ],
        )
        bbox = (330, 159, 542, 312)

        expanded = find_charts._expand_caption_bbox_to_axis_labels(
            page, bbox, "transfer"
        )

        self.assertLess(expanded[0], 316)
        self.assertGreater(expanded[3], 330)


class BodyDiodeAxisGutterOwnershipTests(unittest.TestCase):
    def test_local_source_drain_axes_expand_body_diode_crop(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("(A)", 318, 290, 326, 301),
                find_charts.Word("Source-to-Drain", 318, 326, 326, 373),
                find_charts.Word("Current", 318, 302, 326, 324),
                find_charts.Word("V", 400, 416, 406, 425),
                find_charts.Word("Source-to-Drain", 410, 416, 470, 425),
                find_charts.Word("Voltage", 474, 416, 505, 425),
            ],
        )
        bbox = (345, 268, 542, 412)

        expanded = find_charts._expand_caption_bbox_to_axis_labels(
            page, bbox, "body_diode"
        )

        self.assertLess(expanded[0], 318)
        self.assertGreater(expanded[3], 425)


class SyntheticGateChargeTableGuardTests(unittest.TestCase):
    def test_synthetic_qg_panel_requires_owned_plot_evidence(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [find_charts.Word("Low QG and Capacitance", 60, 100, 240, 112)],
        )
        bbox = (0, 20, 360, 180)

        self.assertFalse(
            find_charts.synthetic_bbox_has_plot_evidence(
                page, Path("/missing.pdf"), 1, bbox, grids=[],
                horizontal_rules=[], vertical_rules=[],
            )
        )
        self.assertTrue(
            find_charts.synthetic_bbox_has_plot_evidence(
                page, Path("/missing.pdf"), 1, bbox, grids=[],
                horizontal_rules=[
                    (40, 50, 260, 51), (40, 80, 260, 81), (40, 110, 260, 111)
                ],
                vertical_rules=[],
            )
        )

    def test_onsemi_cover_qg_prose_is_not_emitted_as_a_chart(self) -> None:
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/onsemi/NVMYS2D3N06CTWG.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with TemporaryDirectory(prefix="onsemi-cover-prose-") as tmp:
            panels = find_charts.process_pdf(pdf, Path(tmp), dpi=180)

        keys = {(panel.page, panel.diagram, panel.kind) for panel in panels}
        self.assertNotIn((1, 951, "capacitances"), keys)
        self.assertIn((4, 7, "capacitances"), keys)
        self.assertIn((4, 951, "gate_charge"), keys)

    def test_st_numbered_marketing_prose_is_not_emitted_as_a_chart(self) -> None:
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st/STL260N4F7.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with TemporaryDirectory(prefix="st-cover-prose-") as tmp:
            panels = find_charts.process_pdf(pdf, Path(tmp), dpi=180)

        self.assertNotIn(
            (1, 4, "capacitances"),
            {(panel.page, panel.diagram, panel.kind) for panel in panels},
        )

    def test_qg_axis_fallback_does_not_exempt_mixed_parameter_table(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("capacitance", 50, 100, 90, 108),
                find_charts.Word("charge", 50, 120, 80, 128),
                find_charts.Word("resistance", 50, 140, 90, 148),
                find_charts.Word("recovery", 50, 160, 85, 168),
            ],
        )

        self.assertTrue(
            find_charts._bbox_looks_like_spec_table(
                page, (40, 90, 120, 180)
            )
        )

    def test_description_first_ti_capacitance_and_charge_rows_are_a_table(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word(text, 20, y, 180, y + 10)
                for text, y in (
                    ("Input capacitance", 100),
                    ("Output capacitance", 120),
                    ("Reverse transfer capacitance", 140),
                    ("Gate charge total", 160),
                )
            ],
        )

        self.assertTrue(
            find_charts._bbox_looks_like_spec_table(page, (0, 90, 200, 180))
        )


class RealPanelOwnershipRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = os.environ.get("DSDIG_DATASHEET_ROOT")
        if root is None:
            raise unittest.SkipTest("DSDIG_DATASHEET_ROOT is not set")
        cls.datasheets = Path(root) / "datasheets"

    def _page_panels(self, relative: str, page_num: int):
        pdf = self.datasheets / relative
        if not pdf.exists():
            self.skipTest(f"missing local corpus PDF: {pdf}")
        page = find_charts.run_text_bbox(pdf)[page_num - 1]
        with TemporaryDirectory(prefix="finder-ownership-real-") as tmp:
            return find_charts.process_page_texts(pdf, Path(tmp), 180, [page])

    @staticmethod
    def _by_kind(panels, kind: str):
        matches = [panel for panel in panels if panel.kind == kind]
        if len(matches) != 1:
            raise AssertionError(f"expected one {kind}, got {matches!r}")
        return matches[0]

    def test_agm_figures_7_and_9_own_separate_left_column_frames(self) -> None:
        panels = self._page_panels("agmsemi/AGM012N10LLM1.pdf", 4)
        body = self._by_kind(panels, "body_diode")
        cap = self._by_kind(panels, "capacitances")

        self.assertEqual(body.title, "Typ. forward characteristics of body diode")
        self.assertEqual(cap.title, "Typ. Capacitance")
        self.assertLess(body.bbox_pt[2], 306)
        self.assertLess(cap.bbox_pt[2], 306)
        self.assertLess(body.bbox_pt[3], cap.bbox_pt[1])

    def test_spw52_breakdown_and_capacitance_own_separate_rows(self) -> None:
        panels = self._page_panels("infineon/SPW52N50C3.pdf", 8)
        breakdown = self._by_kind(panels, "breakdown_voltage")
        cap = self._by_kind(panels, "capacitances")

        self.assertLess(breakdown.bbox_pt[3], cap.bbox_pt[1])
        self.assertLess(breakdown.bbox_pt[2], 306)
        self.assertLess(cap.bbox_pt[2], 306)

    def test_std5_capacitance_and_body_own_separate_rows(self) -> None:
        panels = self._page_panels("st/STD5NM50AG.pdf", 6)
        cap = self._by_kind(panels, "capacitances")
        body = self._by_kind(panels, "body_diode")

        self.assertLess(cap.bbox_pt[3], body.bbox_pt[1])
        self.assertGreater(body.bbox_pt[1], 525)

    def test_st_template_breakdown_and_body_own_distinct_right_frames(self) -> None:
        for part in ("STF12NK60Z", "STF13NK50Z", "STP10NK70ZFP", "STP20NK50Z"):
            with self.subTest(part=part):
                panels = self._page_panels(f"st/{part}.pdf", 6)
                breakdown = self._by_kind(panels, "breakdown_voltage")
                body = self._by_kind(panels, "body_diode")
                self.assertLess(breakdown.bbox_pt[3], body.bbox_pt[1])
                self.assertLess(breakdown.bbox_pt[3], 510)
                self.assertGreater(body.bbox_pt[1], 530)

    def test_rohm_gate_data_is_separate_from_table_and_definition_diagrams(self) -> None:
        page3 = self._page_panels("rohm/RX3P10BBHC16.pdf", 3)
        page8 = self._page_panels("rohm/RX3P10BBHC16.pdf", 8)
        page9 = self._page_panels("rohm/RX3P10BBHC16.pdf", 9)

        self.assertFalse([panel for panel in page3 if panel.kind == "gate_charge"])
        gate = [panel for panel in page8 if panel.kind == "gate_charge"]
        self.assertEqual([(panel.diagram, panel.title) for panel in gate], [
            (902, "Typical Gate Charge"),
        ])
        self.assertFalse([panel for panel in page9 if panel.kind == "gate_charge"])
        self.assertEqual(
            {panel.title for panel in page9 if panel.kind == "chart"},
            {"Gate Charge Waveform"},
        )

    def test_nexperia_gate_data_is_separate_from_explicit_definition_sketch(self) -> None:
        panels = self._page_panels("nxp/PXP8R3-20QX.pdf", 9)

        gate = [panel for panel in panels if panel.kind == "gate_charge"]
        self.assertEqual([(panel.diagram, panel.title) for panel in gate], [
            (14, "Gate-source voltage as a function of gate charge; typical values 003aal160"),
        ])
        self.assertFalse([panel for panel in panels if panel.diagram == 15])


if __name__ == "__main__":
    unittest.main()
