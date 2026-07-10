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


if __name__ == "__main__":
    unittest.main()
