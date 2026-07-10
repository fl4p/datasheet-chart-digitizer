import unittest

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


class CaptionTitleTests(unittest.TestCase):
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

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].number, 7)
        self.assertEqual(titles[0].title, "Gate-Charge Characteristics")

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

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].number, 6)
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


if __name__ == "__main__":
    unittest.main()
