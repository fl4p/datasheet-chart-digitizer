import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from datasheet_chart_digitizer import find_charts


class ChartClassificationTests(unittest.TestCase):
    def test_gate_charge_titles_classify_as_gate_charge(self) -> None:
        cases = [
            ("Typ. gate charge", "VGS=f(QGate); ID=20A"),
            ("Gate Charge Characteristics", "Gate-to-source voltage vs total gate charge"),
            ("Dynamic Input/Output Characteristics", "Total gate charge Qg (nC) Gate-source voltage VGS"),
        ]

        for title, text in cases:
            with self.subTest(title=title):
                self.assertEqual(find_charts.classify_chart(title, text), "gate_charge")

    def test_non_gate_charge_chart_titles_are_rejected(self) -> None:
        cases = [
            ("Source-Drain Diode Forward", "IS reverse drain current VSD source-drain voltage"),
            ("Safe Operating Area", "ID drain current VDS drain-source voltage"),
            ("Drain-source on-state resistance", "RDSon as a function of drain current"),
            ("Transfer Characteristics", "ID=f(VGS) VDS=10 V"),
            ("Output Characteristics", "ID=f(VDS) parameter VGS"),
        ]

        for title, text in cases:
            with self.subTest(title=title):
                self.assertNotEqual(find_charts.classify_chart(title, text), "gate_charge")


class CropPanelTests(unittest.TestCase):
    def test_returns_effective_pdf_box_from_integer_crop(self) -> None:
        page = find_charts.PageText(page_num=1, width_pt=500.0, height_pt=400.0, words=[])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_png = root / "page.png"
            out_png = root / "crop.png"
            Image.new("RGB", (1000, 800), "white").save(page_png)

            crop_box = find_charts.crop_panel(
                page_png,
                page,
                (10.4, 20.6, 110.7, 120.9),
                out_png,
            )

            self.assertEqual(crop_box, (8.0, 18.5, 112.5, 122.5))
            with Image.open(out_png) as crop:
                self.assertEqual(crop.size, (209, 208))


class TextFallbackTests(unittest.TestCase):
    @staticmethod
    def _page(words: list[find_charts.Word], source: str = "pdftotext") -> find_charts.PageText:
        return find_charts.PageText(1, 600.0, 800.0, words, source)

    def test_deduplicates_nearly_overprinted_words(self) -> None:
        words = [
            find_charts.Word("Typ.", 100.0, 200.0, 120.0, 210.0),
            find_charts.Word("Typ.", 100.3, 200.2, 120.3, 210.2),
            find_charts.Word("Typ.", 160.0, 200.0, 180.0, 210.0),
        ]

        deduped = find_charts._dedupe_overprinted_words(words)

        self.assertEqual(len(deduped), 2)
        self.assertEqual([word.x0 for word in deduped], [100.0, 160.0])

    def test_selects_substantially_more_readable_fallback(self) -> None:
        primary = self._page(
            [find_charts.Word("!", float(i), 0.0, float(i + 1), 1.0) for i in range(20)]
        )
        fallback = self._page(
            [find_charts.Word(f"word{i}", float(i), 0.0, float(i + 1), 1.0) for i in range(10)],
            "pymupdf_fallback",
        )

        self.assertTrue(find_charts._page_text_looks_corrupt(primary))
        self.assertIs(find_charts._select_page_text(primary, fallback), fallback)

    def test_keeps_primary_when_fallback_gain_is_marginal(self) -> None:
        primary = self._page(
            [find_charts.Word(f"word{i}", float(i), 0.0, float(i + 1), 1.0) for i in range(10)]
        )
        fallback = self._page(
            [find_charts.Word(f"label{i}", float(i), 0.0, float(i + 1), 1.0) for i in range(12)],
            "pymupdf_fallback",
        )

        self.assertIs(find_charts._select_page_text(primary, fallback), primary)


class CaptionTitleTests(unittest.TestCase):
    def test_accepts_numbered_transfer_caption(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Figure", 100, 200, 135, 210),
                find_charts.Word("3:", 140, 200, 152, 210),
                find_charts.Word("Transfer", 157, 200, 207, 210),
                find_charts.Word("Characteristics", 212, 200, 300, 210),
            ],
        )

        titles = find_charts.find_caption_titles(page)

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].number, 3)
        self.assertEqual(titles[0].title, "Transfer Characteristics")

    def test_splits_multiple_figure_captions(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Figure", 100, 200, 125, 210),
                find_charts.Word("7:", 128, 200, 140, 210),
                find_charts.Word("Gate-Charge", 145, 200, 210, 210),
                find_charts.Word("Characteristics", 215, 200, 300, 210),
                find_charts.Word("Figure", 330, 200, 355, 210),
                find_charts.Word("8:", 358, 200, 370, 210),
                find_charts.Word("Capacitance", 375, 200, 435, 210),
                find_charts.Word("Characteristics", 440, 200, 520, 210),
            ],
        )

        titles = find_charts.find_caption_titles(page)

        # Capacitance captions are admitted (Toshiba/TI C(V) support); the
        # split still has to keep the two captions separate.
        self.assertEqual(len(titles), 2)
        self.assertEqual(titles[0].number, 7)
        self.assertEqual(titles[0].title, "Gate-Charge Characteristics")
        self.assertEqual(titles[1].number, 8)
        self.assertEqual(titles[1].title, "Capacitance Characteristics")

    def test_splits_infineon_numbered_captions(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("13", 50, 130, 62, 140),
                find_charts.Word("Avalanche", 65, 130, 120, 140),
                find_charts.Word("characteristics", 125, 130, 205, 140),
                find_charts.Word("14", 300, 130, 312, 140),
                find_charts.Word("Typ.", 315, 130, 340, 140),
                find_charts.Word("gate", 345, 130, 370, 140),
                find_charts.Word("charge", 375, 130, 410, 140),
            ],
        )

        titles = find_charts.find_caption_titles(page)

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].number, 14)
        self.assertEqual(titles[0].title, "Typ. gate charge")

    def test_splits_compact_fig_caption_tokens(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Fig.5-Capacitance", 80, 200, 180, 210),
                find_charts.Word("Characteristics", 185, 200, 265, 210),
                find_charts.Word("Fig.6-Gate", 330, 200, 395, 210),
                find_charts.Word("Charge", 400, 200, 445, 210),
            ],
        )

        titles = find_charts.find_caption_titles(page)

        self.assertEqual(len(titles), 2)
        self.assertEqual(titles[0].number, 5)
        self.assertEqual(titles[0].title, "Capacitance Characteristics")
        self.assertEqual(titles[1].number, 6)
        self.assertEqual(titles[1].title, "Gate Charge")

    def test_accepts_decimal_dynamic_input_output_caption(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Fig.", 120, 250, 142, 260),
                find_charts.Word("8.13", 146, 250, 170, 260),
                find_charts.Word("Capacitance", 174, 250, 240, 260),
                find_charts.Word("-", 244, 250, 250, 260),
                find_charts.Word("V", 254, 250, 262, 260),
                find_charts.Word("DS", 263, 250, 275, 260),
                find_charts.Word("Fig.", 330, 250, 352, 260),
                find_charts.Word("8.14", 356, 250, 380, 260),
                find_charts.Word("Dynamic", 384, 250, 430, 260),
                find_charts.Word("Input/Output", 434, 250, 512, 260),
                find_charts.Word("Characteristics", 516, 250, 590, 260),
            ],
        )

        titles = find_charts.find_caption_titles(page)

        self.assertEqual(len(titles), 2)
        self.assertEqual(titles[0].number, 813)
        self.assertEqual(titles[0].title, "Capacitance - V DS")
        self.assertEqual(titles[1].number, 814)
        self.assertEqual(titles[1].title, "Dynamic Input/Output Characteristics")

    def test_accepts_wrapped_gate_charge_caption(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Fig.", 60, 200, 78, 210),
                find_charts.Word("13.", 82, 200, 102, 210),
                find_charts.Word("Gate-source", 110, 200, 180, 210),
                find_charts.Word("voltage", 185, 200, 230, 210),
                find_charts.Word("as", 235, 200, 248, 210),
                find_charts.Word("a", 252, 200, 258, 210),
                find_charts.Word("function", 262, 200, 315, 210),
                find_charts.Word("of", 320, 200, 333, 210),
                find_charts.Word("gate", 338, 200, 365, 210),
                find_charts.Word("charge;", 90, 211, 135, 221),
                find_charts.Word("typical", 140, 211, 185, 221),
                find_charts.Word("values", 190, 211, 230, 221),
            ],
        )

        titles = find_charts.find_caption_titles(page)

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].number, 13)
        self.assertEqual(titles[0].title, "Gate-source voltage as a function of gate charge; typical values")

    def test_splits_unnumbered_typ_gate_charge_header(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Typ.", 120, 300, 145, 310),
                find_charts.Word("gate", 150, 300, 178, 310),
                find_charts.Word("charge", 182, 300, 225, 310),
                find_charts.Word("Typ.", 340, 300, 365, 310),
                find_charts.Word("capacitances", 370, 300, 450, 310),
            ],
        )

        titles = find_charts.find_caption_titles(page)

        self.assertEqual(len(titles), 2)
        self.assertEqual(titles[0].number, 901)
        self.assertEqual(titles[0].title, "Typ. gate charge")
        self.assertEqual(titles[1].number, 902)
        self.assertEqual(titles[1].title, "Typ. capacitances")

    def test_splits_paired_typ_gate_charge_and_breakdown_headers(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Typ.", 120, 450, 145, 462),
                find_charts.Word("gate", 150, 450, 178, 462),
                find_charts.Word("charge", 182, 450, 225, 462),
                find_charts.Word("Drain-source", 340, 450, 405, 462),
                find_charts.Word("breakdown", 410, 450, 470, 462),
                find_charts.Word("voltage", 475, 450, 520, 462),
            ],
        )

        titles = find_charts.find_caption_titles(page)

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].title, "Typ. gate charge")
        self.assertLess(titles[0].bbox_pt[2], 230)

    def test_splits_gate_charge_characteristic_from_transfer_header(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Transfer", 40, 430, 95, 442),
                find_charts.Word("characteristic", 100, 430, 178, 442),
                find_charts.Word("(typical),", 183, 430, 240, 442),
                find_charts.Word("IGBT,", 245, 430, 280, 442),
                find_charts.Word("T1", 285, 430, 300, 442),
                find_charts.Word("/", 305, 430, 310, 442),
                find_charts.Word("T4", 315, 430, 330, 442),
                find_charts.Word("Gate", 360, 430, 390, 442),
                find_charts.Word("charge", 395, 430, 435, 442),
                find_charts.Word("characteristic", 440, 430, 520, 442),
                find_charts.Word("(typical),", 525, 430, 580, 442),
            ],
        )

        titles = find_charts.find_caption_titles(page)

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].title, "Gate charge characteristic (typical),")

    def test_caption_axis_label_fallback_synthesizes_panel(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Figure", 330, 320, 360, 331),
                find_charts.Word("10:", 365, 320, 382, 331),
                find_charts.Word("Gate", 386, 320, 414, 331),
                find_charts.Word("Charge", 418, 320, 460, 331),
                find_charts.Word("Characteristics", 464, 320, 540, 331),
                find_charts.Word("Q", 340, 510, 348, 520),
                find_charts.Word("G", 352, 510, 360, 520),
                find_charts.Word("-Gate", 365, 510, 402, 520),
                find_charts.Word("Charge", 407, 510, 450, 520),
                find_charts.Word("(nC)", 455, 510, 485, 520),
            ],
        )
        title = find_charts.find_caption_titles(page)[0]

        bbox = find_charts.choose_caption_axis_label_bbox(page, title)

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLessEqual(bbox[0], 435)
        self.assertGreaterEqual(bbox[2], 435)
        self.assertLess(bbox[1], 410)
        self.assertGreater(bbox[3], 510)

    def test_caption_axis_label_fallback_requires_qg_axis_evidence(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Figure", 330, 320, 360, 331),
                find_charts.Word("10:", 365, 320, 382, 331),
                find_charts.Word("Gate", 386, 320, 414, 331),
                find_charts.Word("Charge", 418, 320, 460, 331),
                find_charts.Word("Characteristics", 464, 320, 540, 331),
                find_charts.Word("Total", 340, 510, 375, 520),
                find_charts.Word("Gate", 380, 510, 408, 520),
                find_charts.Word("Charge", 412, 510, 455, 520),
            ],
        )
        title = find_charts.find_caption_titles(page)[0]

        self.assertIsNone(find_charts.choose_caption_axis_label_bbox(page, title))

    def test_figure_caption_prefers_plot_above(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Figure", 150, 298, 185, 306),
                find_charts.Word("4.", 190, 298, 202, 306),
                find_charts.Word("Gate", 207, 298, 235, 306),
                find_charts.Word("Charge", 240, 298, 280, 306),
                find_charts.Word("Q", 150, 255, 158, 265),
                find_charts.Word("g", 162, 255, 168, 265),
                find_charts.Word("-", 172, 255, 176, 265),
                find_charts.Word("Gate", 180, 255, 208, 265),
                find_charts.Word("Charge", 212, 255, 252, 265),
                find_charts.Word("(nC)", 256, 255, 286, 265),
            ],
        )
        title = find_charts.find_caption_titles(page)[0]

        bbox = find_charts.choose_caption_panel_bbox(
            page,
            title,
            [
                (90.0, 100.0, 290.0, 255.0),
                (90.0, 310.0, 290.0, 455.0),
            ],
        )

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLess(bbox[1], title.bbox_pt[1])
        self.assertLess(bbox[3], title.bbox_pt[1])

    def test_numbered_transfer_caption_rejects_plot_below(self) -> None:
        page = find_charts.PageText(page_num=1, width_pt=600, height_pt=800, words=[])
        title = find_charts.DiagramTitle(
            number=2,
            title="Transfer Characteristics",
            bbox_pt=(370.0, 268.0, 520.0, 279.0),
            line_text="Figure 2. Transfer Characteristics",
        )
        above = (280.0, 53.0, 590.0, 264.0)
        below = (280.0, 283.0, 590.0, 498.0)

        bbox = find_charts.choose_caption_panel_bbox(page, title, [above, below])

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLess(0.5 * (bbox[1] + bbox[3]), title.bbox_pt[1])

    def test_formula_below_caption_prefers_lower_plot_with_larger_gap(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("15", 60, 448, 72, 457),
                find_charts.Word("Typ.", 76, 448, 105, 457),
                find_charts.Word("gate", 110, 448, 138, 457),
                find_charts.Word("charge", 143, 448, 185, 457),
                find_charts.Word("V", 60, 467, 68, 477),
                find_charts.Word("GS", 69, 467, 84, 477),
                find_charts.Word("=", 88, 467, 95, 477),
                find_charts.Word("f(Q", 100, 467, 118, 477),
                find_charts.Word("gate", 119, 467, 145, 477),
                find_charts.Word(")", 146, 467, 150, 477),
            ],
        )
        title = find_charts.find_caption_titles(page)[0]

        bbox = find_charts.choose_caption_panel_bbox(
            page,
            title,
            [
                (100.0, 170.0, 284.0, 401.0),
                (100.0, 628.0, 283.0, 744.0),
            ],
        )

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertGreater(bbox[1], title.bbox_pt[3])

    def test_gate_charge_axis_label_span_is_local_on_shared_line(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("V", 120, 300, 126, 310),
                find_charts.Word("DS", 127, 300, 140, 310),
                find_charts.Word(",", 141, 300, 143, 310),
                find_charts.Word("DRAIN-TO-SOURCE", 146, 300, 240, 310),
                find_charts.Word("VOLTAGE", 243, 300, 292, 310),
                find_charts.Word("(V)", 295, 300, 315, 310),
                find_charts.Word("Q", 382, 300, 390, 310),
                find_charts.Word("G", 391, 300, 398, 310),
                find_charts.Word(",", 399, 300, 401, 310),
                find_charts.Word("TOTAL", 404, 300, 440, 310),
                find_charts.Word("GATE", 443, 300, 472, 310),
                find_charts.Word("CHARGE", 475, 300, 525, 310),
                find_charts.Word("(nC)", 528, 300, 558, 310),
            ],
        )

        spans = find_charts.gate_charge_axis_label_spans(page)

        self.assertEqual(len(spans), 1)
        self.assertGreater(spans[0][0], 370)
        self.assertLess(spans[0][2], 565)

    def test_axis_label_grid_bbox_binds_to_plot_above(self) -> None:
        page = find_charts.PageText(page_num=1, width_pt=600, height_pt=800, words=[])

        bbox = find_charts.choose_axis_label_grid_bbox(
            page,
            (382.0, 300.0, 558.0, 310.0),
            [
                (95.0, 100.0, 305.0, 285.0),
                (340.0, 100.0, 550.0, 285.0),
                (340.0, 390.0, 550.0, 520.0),
            ],
        )

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLess(bbox[0], 340.0)
        self.assertGreater(bbox[2], 550.0)
        self.assertLess(bbox[1], 105.0)
        self.assertGreater(bbox[3], 285.0)

    def test_axis_label_synthetic_bbox_covers_plot_above_light_grid(self) -> None:
        page = find_charts.PageText(page_num=1, width_pt=600, height_pt=800, words=[])

        bbox = find_charts.choose_axis_label_synthetic_bbox(page, (260.0, 347.0, 347.0, 356.0))

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLessEqual(bbox[0], 205.0)
        self.assertGreaterEqual(bbox[2], 402.0)
        self.assertLessEqual(bbox[1], 158.0)
        self.assertGreaterEqual(bbox[3], 343.0)

    def test_caption_synthetic_bbox_covers_plot_below_light_grid(self) -> None:
        page = find_charts.PageText(page_num=1, width_pt=600, height_pt=800, words=[])
        title = find_charts.DiagramTitle(
            number=7,
            title="Gate charge vs gate-source voltage",
            bbox_pt=(73.0, 94.0, 295.0, 103.0),
            line_text="Figure 7. Gate charge vs gate-source voltage",
        )

        bbox = find_charts.choose_caption_synthetic_bbox(page, title)

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLessEqual(bbox[0], 127.0)
        self.assertGreaterEqual(bbox[2], 262.0)
        self.assertLessEqual(bbox[1], 132.0)
        self.assertGreaterEqual(bbox[3], 264.0)

    def test_caption_synthetic_bbox_uses_above_plot_for_midpage_figure_caption(self) -> None:
        page = find_charts.PageText(page_num=1, width_pt=600, height_pt=800, words=[])
        title = find_charts.DiagramTitle(
            number=10,
            title="Gate Charge Characteristics",
            bbox_pt=(380.0, 344.0, 528.0, 358.0),
            line_text="10 Gate Charge Characteristics",
        )

        bbox = find_charts.choose_caption_synthetic_bbox(page, title)

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLessEqual(bbox[1], 146.0)
        self.assertGreaterEqual(bbox[3], 340.0)

    def test_transfer_synthetic_bbox_ignores_page_position_heuristic(self) -> None:
        page = find_charts.PageText(page_num=1, width_pt=612, height_pt=792, words=[])
        title = find_charts.DiagramTitle(
            number=2,
            title="Transfer Characteristics",
            bbox_pt=(376.44, 268.06, 519.36, 278.93),
            line_text="Figure 2. Transfer Characteristics",
        )

        bbox = find_charts.choose_caption_synthetic_bbox(page, title)

        self.assertEqual(
            tuple(round(value, 3) for value in bbox or ()),
            (277.9, 53.06, 612, 264.06),
        )


IPI65R190CFD = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon/IPI65R190CFD.pdf")


@unittest.skipUnless(IPI65R190CFD.exists(), "local IPI65R190CFD datasheet not available")
class CorruptGlyphFinderEndToEnd(unittest.TestCase):
    def test_recovers_transfer_and_gate_charge_charts(self) -> None:
        with TemporaryDirectory(prefix="ipi65r190cfd-finder-test-") as tmp:
            panels = find_charts.process_pdf(IPI65R190CFD, Path(tmp), dpi=180)

        self.assertEqual(
            [(panel.page, panel.kind, panel.text_source) for panel in panels],
            [
                (11, "transfer", "pymupdf_fallback"),
                (11, "gate_charge", "pymupdf_fallback"),
                # Capacitance captions are admitted since the Toshiba/TI C(V)
                # finder extension; page 12 carries a real Typ. capacitances
                # chart that the old caption-kind whitelist dropped.
                (12, "capacitances", "pymupdf_fallback"),
            ],
        )
        panel = next(panel for panel in panels if panel.kind == "gate_charge")
        self.assertIn("Typ. gate charge", panel.title)
        self.assertLessEqual(panel.bbox_pt[0], 346.433)
        self.assertLessEqual(panel.bbox_pt[1], 150.131)
        self.assertGreaterEqual(panel.bbox_pt[2], 560.393)
        self.assertGreaterEqual(panel.bbox_pt[3], 393.8)


class TransferBodyCaptionIntegrationTests(unittest.TestCase):
    def test_fda_transfer_and_body_captions_bind_their_own_plots(self) -> None:
        root = os.environ.get("DSDIG_DATASHEET_ROOT")
        if root is None:
            self.skipTest("DSDIG_DATASHEET_ROOT is not set")
        pdf = Path(root) / "datasheets/onsemi/FDA032N08.pdf"
        if not pdf.exists():
            self.skipTest(f"missing local corpus PDF: {pdf}")

        with TemporaryDirectory(prefix="fda032-caption-integration-") as tmp:
            panels = find_charts.process_pdf(pdf, Path(tmp), dpi=180)

        transfer = [panel for panel in panels if panel.kind == "transfer"]
        body = [panel for panel in panels if panel.kind == "body_diode"]
        gate = [panel for panel in panels if panel.kind == "gate_charge" and panel.page == 3]
        self.assertEqual([(panel.page, panel.diagram) for panel in transfer], [(3, 2)])
        self.assertEqual([(panel.page, panel.diagram) for panel in body], [(3, 4)])
        self.assertEqual([(panel.page, panel.diagram) for panel in gate], [(3, 6)])
        self.assertEqual(transfer[0].bbox_pt, (277.902, 53.06, 612.0, 264.06))
        self.assertEqual(body[0].bbox_pt, (281.211, 267.302, 612.0, 478.302))
        self.assertEqual(gate[0].bbox_pt, (345.472, 566.032, 554.128, 658.768))


if __name__ == "__main__":
    unittest.main()
