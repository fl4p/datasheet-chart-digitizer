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


if __name__ == "__main__":
    unittest.main()
