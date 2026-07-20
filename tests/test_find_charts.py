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
            ("Gate charge waveform", "0 10 20 Qg (nC) VGS (V)"),
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
            ("Body Diode Transfer Characteristics", "IS=f(VSD)"),
        ]

        for title, text in cases:
            with self.subTest(title=title):
                self.assertNotEqual(find_charts.classify_chart(title, text), "gate_charge")

    def test_gate_charge_test_circuit_is_not_a_data_chart(self) -> None:
        self.assertEqual(
            find_charts.classify_chart("Gate Charge Test Circuit & Waveform", ""),
            "chart",
        )

    def test_st_vbrdss_temperature_title_classifies_as_breakdown(self) -> None:
        self.assertEqual(
            find_charts.classify_chart(
                "Normalized V(BR)DSS vs temperature", "TJ (°C)"
            ),
            "breakdown_voltage",
        )

    def test_explicit_waveform_definition_is_not_gate_charge_data(self) -> None:
        self.assertEqual(
            find_charts.classify_chart("Gate charge waveform definitions", ""),
            "chart",
        )

    def test_explicit_characteristics_owns_synthetic_caption_kind(self) -> None:
        title = "Typical gate charge characteristics"
        self.assertEqual(
            find_charts.title_owns_chart_kind(title, 901), "gate_charge"
        )
        self.assertIsNone(
            find_charts.title_owns_chart_kind("Gate charge waveform definitions", 901)
        )

    def test_qg_axis_fallback_is_gate_charge_only(self) -> None:
        page = find_charts.PageText(page_num=1, width_pt=600, height_pt=800, words=[])
        for number, title in (
            (3, "Typical Transfer Characteristics"),
            (4, "Typical Capacitance vs. Drain-to-Source Voltage"),
            (5, "Breakdown Voltage vs. Junction Temperature"),
            (6, "Body Diode Forward Voltage"),
        ):
            with self.subTest(title=title):
                self.assertIsNone(
                    find_charts.choose_caption_axis_label_bbox_for_kind(
                        page,
                        find_charts.DiagramTitle(
                            number=number,
                            title=title,
                            bbox_pt=(100.0, 200.0, 300.0, 212.0),
                            line_text=f"Figure {number}. {title}",
                        ),
                    )
                )


class CropPanelTests(unittest.TestCase):
    def test_smaller_overlap_fraction_detects_contained_duplicate(self) -> None:
        self.assertEqual(
            find_charts._bbox_overlap_fraction_of_smaller(
                (0.0, 0.0, 100.0, 100.0),
                (20.0, 20.0, 80.0, 80.0),
            ),
            1.0,
        )

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

    def test_numbered_caption_drops_recognized_neighbor_after_column_gap(self) -> None:
        left = [
            find_charts.Word(text, x0, 100.0, x1, 110.0)
            for text, x0, x1 in (
                ("Diagram", 45.0, 84.0),
                ("15:", 86.0, 101.0),
                ("Drain-source", 103.0, 162.0),
                ("breakdown", 164.0, 216.0),
                ("voltage", 218.0, 253.0),
                ("Gate", 310.0, 331.0),
                ("charge", 333.0, 365.0),
                ("waveforms", 367.0, 418.0),
            )
        ]
        page = self._page(left)

        titles = find_charts.find_diagram_titles(page)

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].title, "Drain-source breakdown voltage")
        self.assertEqual(find_charts.classify_chart(titles[0].title, ""), "breakdown_voltage")

    def test_large_title_gap_is_preserved_without_two_chart_families(self) -> None:
        words = [
            find_charts.Word("Diagram", 45.0, 100.0, 84.0, 110.0),
            find_charts.Word("15:", 86.0, 100.0, 101.0, 110.0),
            find_charts.Word("Drain-source", 103.0, 100.0, 162.0, 110.0),
            find_charts.Word("breakdown", 164.0, 100.0, 216.0, 110.0),
            find_charts.Word("voltage", 218.0, 100.0, 253.0, 110.0),
            find_charts.Word("normalized", 310.0, 100.0, 360.0, 110.0),
        ]

        title = find_charts.find_diagram_titles(self._page(words))[0]

        self.assertEqual(title.title, "Drain-source breakdown voltage normalized")

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
    @staticmethod
    def _page(words: list[find_charts.Word]) -> find_charts.PageText:
        return find_charts.PageText(1, 600.0, 800.0, words)

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

    def test_extends_numbered_gate_charge_test_circuit_title(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
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

        titles = find_charts.extend_wrapped_titles(
            page, find_charts.find_diagram_titles(page)
        )

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].title, "Gate Charge Test Circuit & Waveform")
        self.assertEqual(find_charts.classify_chart(titles[0].title, ""), "chart")

    def test_extends_numbered_gate_charge_measurement_title(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Diagram", 60, 200, 102, 210),
                find_charts.Word("5:", 106, 200, 118, 210),
                find_charts.Word("Gate", 122, 200, 150, 210),
                find_charts.Word("Charge", 155, 200, 196, 210),
                find_charts.Word("Characteristics", 122, 211, 202, 221),
            ],
        )

        titles = find_charts.extend_wrapped_titles(
            page, find_charts.find_diagram_titles(page)
        )

        self.assertEqual(titles[0].title, "Gate Charge Characteristics")
        self.assertEqual(
            find_charts.classify_chart(titles[0].title, ""), "gate_charge"
        )

    def test_numbered_title_does_not_take_adjacent_column_continuation(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Diagram", 60, 200, 102, 210),
                find_charts.Word("5:", 106, 200, 118, 210),
                find_charts.Word("Gate", 122, 200, 150, 210),
                find_charts.Word("Charge", 155, 200, 196, 210),
                find_charts.Word("Test", 410, 211, 437, 221),
                find_charts.Word("Circuit", 442, 211, 481, 221),
            ],
        )

        titles = find_charts.extend_wrapped_titles(
            page, find_charts.find_diagram_titles(page)
        )

        self.assertEqual(titles[0].title, "Gate Charge")

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

    def test_rejects_wrapped_gate_charge_test_circuit_caption(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Fig.", 60, 200, 78, 210),
                find_charts.Word("13.", 82, 200, 102, 210),
                find_charts.Word("Gate", 110, 200, 138, 210),
                find_charts.Word("Charge", 143, 200, 184, 210),
                find_charts.Word("Test", 110, 211, 137, 221),
                find_charts.Word("Circuit", 142, 211, 181, 221),
                find_charts.Word("&", 186, 211, 194, 221),
                find_charts.Word("Waveform", 199, 211, 255, 221),
            ],
        )

        self.assertEqual(find_charts.find_caption_titles(page), [])

    def test_wrapped_gate_charge_measurement_caption_stays_data_bearing(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Fig.", 60, 200, 78, 210),
                find_charts.Word("13.", 82, 200, 102, 210),
                find_charts.Word("Gate", 110, 200, 138, 210),
                find_charts.Word("Charge", 143, 200, 184, 210),
                find_charts.Word("Characteristics", 110, 211, 190, 221),
            ],
        )

        titles = find_charts.find_caption_titles(page)

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].title, "Gate Charge Characteristics")

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

    def test_comma_numbered_two_column_captions_keep_their_figure_owner(self) -> None:
        page = self._page([
            find_charts.Word(text, x0, 100.0, x1, 110.0)
            for text, x0, x1 in (
                ("Figure", 20, 55), ("3,", 58, 70), ("Typ.", 73, 100),
                ("capacitances", 104, 180), ("Figure", 330, 365),
                ("4,", 368, 380), ("Typ.", 383, 410),
                ("gate", 414, 445), ("charge", 449, 490),
            )
        ])

        titles = find_charts.find_caption_titles(page)

        self.assertEqual([(item.number, item.title) for item in titles], [
            (3, "Typ. capacitances"), (4, "Typ. gate charge"),
        ])

    def test_ocr_spaced_figure_caption_starts_its_own_panel(self) -> None:
        page = self._page([
            find_charts.Word(text, x0, 100, x1, 110)
            for text, x0, x1 in (
                ("Figure", 20, 55), ("3.", 58, 70), ("Capacitance", 74, 145),
                ("Characteristics", 149, 225), ("Fi", 330, 340), ("g", 344, 350),
                ("u", 354, 360), ("r", 364, 370), ("e", 374, 380),
                ("4", 384, 390), (".G", 394, 405), ("a", 409, 415),
                ("te", 419, 430), ("Charge", 434, 475),
            )
        ])

        self.assertEqual(
            [(title.number, title.title) for title in find_charts.find_caption_titles(page)],
            [(3, "Capacitance Characteristics"), (4, "Gate Charge")],
        )

    def test_bare_axis_tick_is_not_a_figure_number(self) -> None:
        page = self._page([
            find_charts.Word("6000", 20, 100, 50, 110),
            find_charts.Word("Gate-Source", 55, 100, 120, 110),
            find_charts.Word("Capacitance", 125, 100, 195, 110),
        ])

        self.assertEqual(find_charts.find_caption_titles(page), [])

    def test_explicit_definition_does_not_corrupt_following_chart_caption(self) -> None:
        page = self._page([
            find_charts.Word("Fig.", 20, 100, 40, 110),
            find_charts.Word("15.", 44, 100, 58, 110),
            find_charts.Word("Gate", 62, 100, 90, 110),
            find_charts.Word("charge", 94, 100, 135, 110),
            find_charts.Word("waveform", 139, 100, 190, 110),
            find_charts.Word("definitions", 194, 100, 250, 110),
            find_charts.Word("Fig.", 300, 112, 320, 122),
            find_charts.Word("16.", 324, 112, 338, 122),
            find_charts.Word("Input", 342, 112, 375, 122),
            find_charts.Word("capacitances", 379, 112, 450, 122),
        ])
        titles = find_charts.find_caption_titles(page)
        self.assertEqual([(title.number, title.title) for title in titles], [
            (16, "Input capacitances"),
        ])

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

    def test_numbered_transfer_caption_leads_plot_with_own_axis_evidence(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[find_charts.Word("VGS(V)", 420.0, 318.0, 450.0, 330.0)],
        )
        title = find_charts.DiagramTitle(
            number=2,
            title="Typical Transfer Characteristics",
            bbox_pt=(358.0, 146.0, 527.0, 159.0),
            line_text="Figure 2: Typical Transfer Characteristics",
        )
        below = (336.0, 162.0, 537.0, 308.0)

        bbox = find_charts.choose_caption_panel_bbox(page, title, [below])

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertGreater(0.5 * (bbox[1] + bbox[3]), title.bbox_pt[3])

    def test_caption_synthetic_bbox_stops_at_first_own_axis(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("VGS(V)", 420.0, 318.0, 450.0, 330.0),
                # Same-column neighbor chart below: must not extend the crop.
                find_charts.Word("VGS(V)", 420.0, 530.0, 450.0, 542.0),
            ],
        )
        title = find_charts.DiagramTitle(
            number=2,
            title="Typical Transfer Characteristics",
            bbox_pt=(358.0, 146.0, 527.0, 159.0),
            line_text="Figure 2: Typical Transfer Characteristics",
        )

        bbox = find_charts.choose_caption_synthetic_bbox(page, title)

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLess(bbox[3], 350.0)

    def test_numbered_breakdown_caption_without_axis_evidence_fails_closed(self) -> None:
        page = find_charts.PageText(page_num=1, width_pt=600, height_pt=800, words=[])
        title = find_charts.DiagramTitle(
            number=10,
            title="Drain-to-Source Breakdown Voltage",
            bbox_pt=(350.0, 480.0, 540.0, 492.0),
            line_text="Fig 10. Drain-to-Source Breakdown Voltage",
        )
        above = (350.0, 300.0, 540.0, 470.0)
        below = (350.0, 500.0, 540.0, 680.0)

        bbox = find_charts.choose_caption_panel_bbox(page, title, [above, below])

        self.assertIsNone(bbox)

    def test_breakdown_synthetic_bbox_uses_region_with_breakdown_evidence(self) -> None:
        page = find_charts.PageText(
            1, 600, 800,
            [
                find_charts.Word("VGS(th)", 400, 220, 440, 230),
                find_charts.Word("V(BR)DSS", 400, 420, 455, 430),
            ],
        )
        title = find_charts.DiagramTitle(
            10, "Normalized breakdown voltage vs temperature",
            (330, 300, 540, 312), "Figure 10.",
        )

        bbox = find_charts.choose_caption_synthetic_bbox(page, title)

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertGreater(bbox[1], title.bbox_pt[3])

    def test_numbered_breakdown_caption_leads_plot_with_tj_axis_evidence(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("T", 135.0, 282.0, 140.0, 292.0),
                find_charts.Word("J", 140.0, 287.0, 143.0, 295.0),
                find_charts.Word("(℃)", 146.0, 282.0, 162.0, 295.0),
            ],
        )
        title = find_charts.DiagramTitle(
            number=7,
            title="Normalized Breakdown voltage vs.",
            bbox_pt=(95.0, 115.0, 278.0, 128.0),
            line_text="Figure 7: Normalized Breakdown voltage vs.",
        )
        below = (60.0, 140.0, 283.0, 281.0)

        bbox = find_charts.choose_caption_panel_bbox(page, title, [below])

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertGreater(bbox[1], title.bbox_pt[3])

    def test_breakdown_direction_ignores_junction_temperature_inside_caption(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("Junction", 145.0, 115.0, 195.0, 127.0),
                find_charts.Word("Temperature", 198.0, 115.0, 268.0, 127.0),
                find_charts.Word("T", 135.0, 282.0, 140.0, 292.0),
                find_charts.Word("J", 140.0, 287.0, 143.0, 295.0),
                find_charts.Word("(℃)", 146.0, 282.0, 162.0, 295.0),
            ],
        )
        title = find_charts.DiagramTitle(
            number=7,
            title="Breakdown voltage versus Junction Temperature",
            bbox_pt=(95.0, 110.0, 278.0, 130.0),
            line_text="Figure 7: Breakdown voltage versus Junction Temperature",
        )

        direction = find_charts.caption_axis_direction(
            page, title, "breakdown_voltage", find_charts._token_norm
        )

        self.assertEqual(direction, "below")

    def test_caption_self_token_is_not_axis_direction_evidence(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[find_charts.Word("VGS", 400.0, 146.0, 425.0, 158.0)],
        )
        title = find_charts.DiagramTitle(
            number=2,
            title="VGS Transfer Characteristics",
            bbox_pt=(358.0, 140.0, 527.0, 160.0),
            line_text="Figure 2: VGS Transfer Characteristics",
        )

        direction = find_charts.caption_axis_direction(
            page, title, "transfer", find_charts._token_norm
        )

        self.assertIsNone(direction)

    def test_breakdown_direction_joins_interleaved_split_tj_axis(self) -> None:
        # Same-row two-column pages can interleave the left chart's words
        # between the right chart's split T/J glyphs in pdftotext order.
        page = find_charts.PageText(
            page_num=1,
            width_pt=612,
            height_pt=792,
            words=[
                find_charts.Word("T", 390.0, 491.0, 395.0, 503.0),
                find_charts.Word("Case", 120.0, 491.2, 145.0, 503.2),
                find_charts.Word("J", 396.0, 491.4, 400.0, 503.4),
                find_charts.Word("(°C)", 404.0, 491.4, 424.0, 503.4),
                find_charts.Word("Junction", 430.0, 722.0, 475.0, 734.0),
            ],
        )
        title = find_charts.DiagramTitle(
            number=10,
            title="Drain-to-Source Breakdown Voltage",
            bbox_pt=(352.0, 507.0, 549.0, 519.0),
            line_text="Fig 10. Drain-to-Source Breakdown Voltage",
        )

        direction = find_charts.caption_axis_direction(
            page, title, "breakdown_voltage", find_charts._token_norm
        )

        self.assertEqual(direction, "above")

    def test_breakdown_direction_accepts_degree_symbol_ocr_as_five(self) -> None:
        page = find_charts.PageText(
            1, 600, 800,
            [
                find_charts.Word("TJ", 120, 240, 132, 250),
                find_charts.Word("Junction", 136, 240, 180, 250),
                find_charts.Word("Temperature", 184, 240, 250, 250),
                find_charts.Word("(5C)", 254, 240, 276, 250),
            ],
        )
        title = find_charts.DiagramTitle(
            7, "Breakdown Voltage Variation", (100, 260, 280, 272), "Figure 7"
        )

        self.assertEqual(
            find_charts.caption_axis_direction(page, title, "breakdown_voltage", find_charts._token_norm),
            "above",
        )

    def test_transfer_condition_token_is_not_axis_direction_evidence(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600,
            height_pt=800,
            words=[
                find_charts.Word("VGS", 410.0, 420.0, 438.0, 432.0),
                find_charts.Word("=", 442.0, 420.0, 448.0, 432.0),
                find_charts.Word("0", 452.0, 420.0, 458.0, 432.0),
            ],
        )
        title = find_charts.DiagramTitle(
            number=2,
            title="Typical Transfer Characteristics",
            bbox_pt=(358.0, 240.0, 527.0, 253.0),
            line_text="Figure 2: Typical Transfer Characteristics",
        )

        self.assertIsNone(
            find_charts.caption_axis_direction(
                page, title, "transfer", find_charts._token_norm
            )
        )

    def test_numbered_body_caption_synthetic_bbox_uses_vsd_axis_below(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=595,
            height_pt=842,
            words=[find_charts.Word("VSD(V)", 423.0, 539.0, 448.0, 552.0)],
        )
        title = find_charts.DiagramTitle(
            number=4,
            title="Body Diode Characteristics",
            bbox_pt=(365.0, 356.0, 517.0, 369.0),
            line_text="Figure 4: Body Diode Characteristics",
        )

        bbox = find_charts.choose_caption_synthetic_bbox(page, title)

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertGreaterEqual(bbox[1], title.bbox_pt[3])
        self.assertLessEqual(bbox[0], 303.0)
        self.assertLess(bbox[3], 563.0)

    def test_numbered_capacitance_caption_uses_vds_axis_below(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=595,
            height_pt=842,
            words=[find_charts.Word("VDS(V)", 420.0, 770.0, 455.0, 783.0)],
        )
        title = find_charts.DiagramTitle(
            number=6,
            title="Capacitance Characteristics",
            bbox_pt=(364.0, 563.0, 518.0, 576.0),
            line_text="Figure 6: Capacitance Characteristics",
        )

        bbox = find_charts.choose_caption_synthetic_bbox(page, title)

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertGreaterEqual(bbox[1], title.bbox_pt[3])

    def test_spec_table_sections_reject_gate_charge_axis_candidate(self) -> None:
        words = []
        x = 10.0
        for y, text in (
            (20.0, "Off Characteristics"),
            (40.0, "On Characteristics"),
            (60.0, "Dynamic Characteristics"),
            (80.0, "Total Gate Charge"),
        ):
            for token in text.split():
                words.append(find_charts.Word(token, x, y, x + 30.0, y + 10.0))
                x += 35.0
            x = 10.0
        page = find_charts.PageText(1, 600.0, 800.0, words)

        self.assertTrue(
            find_charts._bbox_looks_like_spec_table(
                page, (0.0, 0.0, 500.0, 120.0), own_families=frozenset({"charge"})
            )
        )

    def test_switching_and_charge_parameter_rows_reject_axis_candidate(self) -> None:
        page = self._page([
            find_charts.Word("Turn-On", 20, 100, 60, 110),
            find_charts.Word("Rise", 64, 100, 88, 110),
            find_charts.Word("Time", 92, 100, 120, 110),
            find_charts.Word("Total", 20, 120, 52, 130),
            find_charts.Word("Gate", 56, 120, 84, 130),
            find_charts.Word("Charge", 88, 120, 130, 130),
        ])

        self.assertTrue(find_charts._bbox_looks_like_spec_table(
            page, (0.0, 90.0, 150.0, 140.0), own_families=frozenset({"charge"})
        ))

    def test_capacitance_and_charge_parameter_rows_reject_axis_candidate(self) -> None:
        page = self._page([
            find_charts.Word(text, 20, y, 140, y + 10)
            for text, y in (
                ("Output Capacitance", 100),
                ("Reverse Transfer Capacitance", 120),
                ("Total Gate Charge", 140),
            )
        ])

        self.assertTrue(find_charts._bbox_looks_like_spec_table(
            page, (0.0, 90.0, 160.0, 160.0), own_families=frozenset({"charge"})
        ))

    def test_caption_row_midpoint_separates_adjacent_plot_columns(self) -> None:
        page = self._page([])
        left = find_charts.DiagramTitle(4, "Gate Charge", (120, 500, 220, 510), "Figure 4")
        right = find_charts.DiagramTitle(5, "Capacitance", (380, 500, 480, 510), "Figure 5")

        bbox = find_charts.bound_caption_bbox_to_caption_row(
            page, left, (80, 300, 450, 490), [left, right]
        )

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLess(bbox[2], right.bbox_pt[0])

    def test_caption_row_does_not_clip_own_frame_before_neighbor_caption(self) -> None:
        page = self._page([])
        left = find_charts.DiagramTitle(15, "Breakdown", (59, 448, 228, 457), "15 Breakdown")
        right = find_charts.DiagramTitle(16, "Waveforms", (298, 448, 424, 457), "16 Waveforms")

        bbox = find_charts.bound_caption_bbox_to_caption_row(
            page, left, (93, 499, 290, 754), [left, right]
        )

        self.assertEqual(bbox, (93, 499, 290, 754))

    def test_superscript_axis_digit_is_not_a_bare_caption_number(self) -> None:
        line = [
            find_charts.Word("10²", 20, 100, 35, 110),
            find_charts.Word("Gate", 40, 100, 65, 110),
            find_charts.Word("charge", 70, 100, 105, 110),
        ]

        self.assertEqual(find_charts._caption_starts(line), [])

    def test_plot_above_excludes_previous_same_column_caption(self) -> None:
        page = self._page([])
        prior = find_charts.DiagramTitle(9, "Gate Charge", (100, 300, 220, 312), "Figure 9")
        current = find_charts.DiagramTitle(11, "Capacitance", (100, 520, 220, 532), "Figure 11")

        bbox = find_charts.bound_caption_bbox_to_caption_row(
            page, current, (80, 290, 260, 515), [prior, current], plot_above=True
        )

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertGreater(bbox[1], prior.bbox_pt[3])

    def test_plot_above_ignores_previous_caption_in_neighbor_column(self) -> None:
        page = self._page([])
        prior = find_charts.DiagramTitle(15, "Definitions", (50, 286, 300, 299), "Figure 15")
        current = find_charts.DiagramTitle(16, "Capacitance", (330, 315, 540, 328), "Figure 16")

        bbox = find_charts.bound_caption_bbox_to_caption_row(
            page, current, (335, 123, 506, 297), [prior, current], plot_above=True
        )

        self.assertEqual(bbox, (335, 123, 506, 297))

    def test_revision_history_page_is_not_chart_source(self) -> None:
        page = self._page([
            find_charts.Word("Revision", 20, 20, 70, 30),
            find_charts.Word("history", 74, 20, 120, 30),
            find_charts.Word("Date", 20, 40, 45, 50),
            find_charts.Word("Revision", 50, 40, 100, 50),
            find_charts.Word("Changes", 105, 40, 150, 50),
            find_charts.Word("Figure", 20, 70, 55, 80),
            find_charts.Word("4.", 60, 70, 70, 80),
            find_charts.Word("Transfer", 75, 70, 120, 80),
            find_charts.Word("Characteristics", 125, 70, 200, 80),
        ])

        self.assertIsNotNone(
            find_charts.revision_history_region(page.words, find_charts._token_norm)
        )

    def test_product_summary_rejects_gate_charge_axis_candidate(self) -> None:
        page = find_charts.PageText(
            1,
            600.0,
            800.0,
            [
                find_charts.Word(text, x, y, x + 60.0, y + 10.0)
                for text, x, y in (
                    ("Product", 10.0, 10.0),
                    ("Summary", 75.0, 10.0),
                    ("VALUE", 200.0, 30.0),
                    ("UNIT", 270.0, 30.0),
                    ("Qg", 10.0, 50.0),
                    ("Gate", 75.0, 50.0),
                    ("Charge", 140.0, 50.0),
                    ("21", 200.0, 50.0),
                    ("nC", 270.0, 50.0),
                )
            ],
        )

        self.assertTrue(
            find_charts._bbox_looks_like_spec_table(
                page, (0.0, 0.0, 350.0, 80.0), own_families=frozenset({"charge"})
            )
        )

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

    def test_caption_vector_frame_bbox_stays_in_own_column(self) -> None:
        page = find_charts.PageText(page_num=1, width_pt=595.22, height_pt=842.0, words=[])
        title = find_charts.DiagramTitle(
            number=901,
            title="Typical Capacitance vs.",
            bbox_pt=(391.9, 63.5, 486.7, 71.8),
            line_text="Typical Capacitance vs.",
        )
        left_neighbor = (127.8, 94.8, 269.2, 236.5)
        own_frame = (368.1, 94.4, 509.5, 236.2)

        bbox = find_charts.choose_caption_vector_frame_bbox(
            page, title, [left_neighbor, own_frame]
        )

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertGreater(bbox[0], 300.0)
        self.assertLess(bbox[2], 540.0)
        self.assertLess(bbox[3], 300.0)

    def test_numbered_transfer_caption_does_not_bind_frame_below(self) -> None:
        page = find_charts.PageText(page_num=1, width_pt=600, height_pt=800, words=[])
        title = find_charts.DiagramTitle(
            number=7,
            title="Typical Transfer Characteristics",
            bbox_pt=(330.0, 300.0, 500.0, 312.0),
            line_text="Figure 7. Typical Transfer Characteristics",
        )

        bbox = find_charts.choose_caption_vector_frame_bbox(
            page, title, [(330.0, 330.0, 520.0, 520.0)]
        )

        self.assertIsNone(bbox)

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
        # The body-diode panel starts below the preceding Figure 2 caption;
        # retaining that caption would make the crop own text from another plot.
        self.assertEqual(body[0].bbox_pt, (281.211, 280.932, 612.0, 478.302))
        self.assertEqual(gate[0].bbox_pt, (345.472, 566.032, 554.128, 658.768))


class CaptionBboxExpansionGuardTests(unittest.TestCase):
    def test_two_column_axis_units_clamp_crop_at_midpoint(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=600.0,
            height_pt=800.0,
            words=[
                find_charts.Word("(nC)", 170.0, 590.0, 195.0, 602.0),
                find_charts.Word("(V)", 430.0, 590.0, 448.0, 602.0),
            ],
        )
        left_title = find_charts.DiagramTitle(
            number=5,
            title="Gate Charge Characteristics",
            bbox_pt=(100.0, 400.0, 250.0, 412.0),
            line_text="Figure 5: Gate Charge Characteristics",
        )
        right_title = find_charts.DiagramTitle(
            number=6,
            title="Capacitance Characteristics",
            bbox_pt=(360.0, 400.0, 520.0, 412.0),
            line_text="Figure 6: Capacitance Characteristics",
        )

        self.assertEqual(
            find_charts._bound_caption_bbox_to_own_column(
                page, left_title, (20.0, 420.0, 330.0, 620.0)
            ),
            (20.0, 420.0, 300.0, 620.0),
        )
        self.assertEqual(
            find_charts._bound_caption_bbox_to_own_column(
                page, right_title, (270.0, 420.0, 590.0, 620.0)
            ),
            (306.0, 420.0, 590.0, 620.0),
        )
        self.assertIsNone(
            find_charts._bound_caption_bbox_to_own_column(
                page, right_title, (40.0, 420.0, 295.0, 620.0)
            )
        )

    def test_single_stray_numeral_does_not_suppress_expansion(self) -> None:
        # A lone condition numeral (the "25" split off "Tj = 25 degC") inside
        # the bbox near an edge must not read as "tick labels already present";
        # both gutters here hold their real tick labels OUTSIDE the bbox.
        words = [
            find_charts.Word("25", 108.0, 280.0, 116.0, 288.0),  # stray, in-bbox
            # real left tick labels, outside the bbox
            find_charts.Word("100", 80.0, 120.0, 96.0, 128.0),
            find_charts.Word("10", 82.0, 180.0, 96.0, 188.0),
            # real bottom tick labels, outside the bbox
            find_charts.Word("1", 150.0, 305.0, 156.0, 313.0),
            find_charts.Word("10", 200.0, 305.0, 212.0, 313.0),
        ]
        page = find_charts.PageText(page_num=1, width_pt=500.0, height_pt=400.0, words=words)
        out = find_charts._expand_caption_bbox_to_axis_labels(page, (100.0, 100.0, 300.0, 300.0))
        self.assertLess(out[0], 100.0, "left gutter was not taken in")
        self.assertGreater(out[3], 300.0, "bottom gutter was not taken in")

    def test_real_tick_runs_inside_bbox_keep_crop_unchanged(self) -> None:
        # Panels that already include their gutters (>=2 tick-like numerals
        # per edge) must keep byte-identical crops.
        words = [
            find_charts.Word("100", 104.0, 120.0, 120.0, 128.0),
            find_charts.Word("10", 106.0, 180.0, 120.0, 188.0),
            find_charts.Word("1", 150.0, 280.0, 156.0, 288.0),
            find_charts.Word("10", 200.0, 280.0, 212.0, 288.0),
            # decoy numerals outside the bbox that must NOT be swallowed
            find_charts.Word("50", 70.0, 150.0, 90.0, 158.0),
            find_charts.Word("5", 170.0, 305.0, 176.0, 313.0),
        ]
        page = find_charts.PageText(page_num=1, width_pt=500.0, height_pt=400.0, words=words)
        bbox = (100.0, 100.0, 300.0, 300.0)
        self.assertEqual(find_charts._expand_caption_bbox_to_axis_labels(page, bbox), bbox)

    def test_bottom_expansion_stops_at_first_aligned_tick_row(self) -> None:
        page = find_charts.PageText(
            page_num=1,
            width_pt=500.0,
            height_pt=500.0,
            words=[
                find_charts.Word("0", 140.0, 304.0, 146.0, 312.0),
                find_charts.Word("10", 200.0, 304.0, 212.0, 312.0),
                # A later panel's numeric row is still inside the old broad
                # 40-point gutter, but must not extend this crop to y=338.
                find_charts.Word("0", 140.0, 330.0, 146.0, 338.0),
                find_charts.Word("50", 200.0, 330.0, 212.0, 338.0),
            ],
        )
        bbox = (100.0, 100.0, 300.0, 300.0)

        self.assertEqual(
            find_charts._expand_caption_bbox_to_axis_labels(page, bbox)[3], 314.0
        )

    def test_neighbor_single_tick_does_not_pull_crop_across_columns(self) -> None:
        words = [
            # Own Y ticks form an aligned run immediately outside the crop.
            find_charts.Word("130", 280.0, 120.0, 298.0, 128.0),
            find_charts.Word("120", 280.0, 180.0, 298.0, 188.0),
            find_charts.Word("110", 280.0, 240.0, 298.0, 248.0),
            # A terminal tick from the left-hand chart is farther outboard.
            find_charts.Word("175", 242.0, 240.0, 262.0, 248.0),
        ]
        page = find_charts.PageText(
            page_num=1, width_pt=612.0, height_pt=792.0, words=words
        )

        out = find_charts._expand_caption_bbox_to_axis_labels(
            page, (300.0, 100.0, 600.0, 300.0)
        )

        self.assertEqual(out[0], 278.0)


class SpecTableGuardFamilyTests(unittest.TestCase):
    @staticmethod
    def _page_with_tokens(tokens: list[str]) -> "find_charts.PageText":
        words = [
            find_charts.Word(text, 10.0 + 40.0 * i, 10.0, 40.0 + 40.0 * i, 18.0)
            for i, text in enumerate(tokens)
        ]
        return find_charts.PageText(page_num=1, width_pt=700.0, height_pt=400.0, words=words)

    def test_own_family_does_not_count_toward_table_signal(self) -> None:
        # A legitimate gate-charge panel always contains "charge" (that's how
        # the candidate was found); with condition callouts naming resistance,
        # threshold and recovery it must still NOT read as a spec table.
        page = self._page_with_tokens(
            ["Gate", "charge", "resistance", "RDS(on)", "threshold", "Vth", "recovery", "reverse"]
        )
        bbox = (0.0, 0.0, 700.0, 50.0)
        self.assertTrue(find_charts._bbox_looks_like_spec_table(page, bbox))
        self.assertFalse(
            find_charts._bbox_looks_like_spec_table(page, bbox, own_families=frozenset({"charge"}))
        )

    def test_real_table_row_column_still_rejected(self) -> None:
        # A row-name column listing >=4 families beyond the candidate's own
        # keeps tripping the guard.
        page = self._page_with_tokens(
            ["leakage", "threshold", "capacitance", "charge", "resistance", "recovery"]
        )
        bbox = (0.0, 0.0, 700.0, 50.0)
        self.assertTrue(
            find_charts._bbox_looks_like_spec_table(page, bbox, own_families=frozenset({"charge"}))
        )


TK100E10N1 = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/toshiba/TK100E10N1.pdf")


@unittest.skipUnless(TK100E10N1.exists(), "local TK100E10N1 datasheet not available")
class ToshibaRasterImagePanelTests(unittest.TestCase):
    def test_capacitance_caption_binds_to_embedded_image_rect(self) -> None:
        # Toshiba renders the whole figure (grid, traces AND tick labels) as
        # one embedded raster image; the panel must be the full image rect,
        # not the grid-rule bbox that clips the tick labels off.
        with TemporaryDirectory(prefix="tk100e10n1-finder-") as tmp:
            panels = find_charts.process_pdf(TK100E10N1, Path(tmp), dpi=180)
        caps = [panel for panel in panels if panel.kind == "capacitances"]
        self.assertEqual([(panel.page, panel.diagram) for panel in caps], [(6, 88)])
        for got, want in zip(caps[0].bbox_pt, (308.421, 62.692, 537.308, 248.264)):
            self.assertAlmostEqual(got, want, places=2)
        self.assertIn(
            (6, 810, "gate_charge"),
            [(panel.page, panel.diagram, panel.kind) for panel in panels],
        )


if __name__ == "__main__":
    unittest.main()
