import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from datasheet_chart_digitizer import find_charts


PSMN2R8 = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/nxp/PSMN2R8-40YSD.pdf"
)
PH2925U = PSMN2R8.with_name("PH2925U.pdf")
PSMN038 = PSMN2R8.with_name("PSMN038-100YL.pdf")
BUK9K12 = PSMN2R8.with_name("BUK9K12-80L.pdf")
BUK9M16 = PSMN2R8.with_name("BUK9M16-100L.pdf")


class ShallowRuledRowTests(unittest.TestCase):
    def test_two_local_rules_enclose_a_table_cross_reference(self) -> None:
        bbox = (52.2, 122.8, 277.2, 140.5)
        rules = [
            (50.4, 126.4, 215.5, 126.8),
            (50.4, 141.6, 545.7, 142.0),
        ]

        self.assertTrue(find_charts.bbox_is_shallow_ruled_row(bbox, rules))

    def test_one_nearby_plot_rule_is_not_a_table_row(self) -> None:
        bbox = (299.8, 289.5, 539.5, 302.4)
        rules = [(297.0, 286.0, 545.0, 286.4)]

        self.assertFalse(find_charts.bbox_is_shallow_ruled_row(bbox, rules))

    def test_tall_region_is_not_a_shallow_table_row(self) -> None:
        bbox = (50.0, 100.0, 300.0, 180.0)
        rules = [(40.0, 105.0, 320.0, 105.4), (40.0, 175.0, 320.0, 175.4)]

        self.assertFalse(find_charts.bbox_is_shallow_ruled_row(bbox, rules))

    def test_wrapped_table_cell_is_still_one_ruled_row(self) -> None:
        bbox = (54.1, 324.3, 299.2, 354.5)
        rules = [
            (49.8, 339.4, 544.9, 340.0),
            (49.8, 357.4, 214.6, 358.0),
        ]

        self.assertTrue(find_charts.bbox_is_shallow_ruled_row(bbox, rules))

    def test_open_table_row_still_has_owned_upper_rule(self) -> None:
        bbox = (52.2, 598.3, 244.9, 611.6)
        rules = [(49.8, 596.6, 544.9, 597.8)]

        self.assertTrue(find_charts.bbox_follows_long_horizontal_rule(bbox, rules))

    def test_numeric_parameter_row_is_a_spec_cross_reference(self) -> None:
        bbox = (52.2, 122.8, 277.2, 140.5)
        rules = [
            (50.4, 126.4, 215.5, 126.8),
            (50.4, 141.6, 545.7, 142.0),
        ]
        words = [
            find_charts.Word("QG(tot)", 52.2, 127.0, 88.0, 137.0),
            find_charts.Word("28", 320.0, 127.0, 332.0, 137.0),
            find_charts.Word("44", 360.0, 127.0, 372.0, 137.0),
            find_charts.Word("62", 400.0, 127.0, 412.0, 137.0),
            find_charts.Word("nC", 450.0, 127.0, 464.0, 137.0),
        ]

        self.assertTrue(find_charts.is_ruled_spec_crossref(words, bbox, rules))

    def test_ruled_plot_caption_is_not_a_spec_cross_reference(self) -> None:
        bbox = (59.8, 138.3, 173.6, 147.2)
        rules = [
            (56.0, 135.0, 520.0, 135.5),
            (56.0, 150.0, 520.0, 150.5),
        ]
        words = [
            find_charts.Word(
                "Typ.", 59.8, 138.3, 79.0, 147.2
            ),
            find_charts.Word(
                "transfer", 81.0, 138.3, 120.0, 147.2
            ),
            find_charts.Word(
                "characteristics", 122.0, 138.3, 173.6, 147.2
            ),
        ]

        self.assertFalse(find_charts.is_ruled_spec_crossref(words, bbox, rules))


@unittest.skipUnless(PSMN2R8.exists(), "local Nexperia medoid is unavailable")
class NexperiaSpecTableCrossReferenceTests(unittest.TestCase):
    def test_table_cross_references_are_not_panels(self) -> None:
        with TemporaryDirectory(prefix="nexperia-table-crossref-") as tmp:
            panels = find_charts.process_pdf(PSMN2R8, Path(tmp), dpi=180)

        identities = {(panel.page, panel.diagram, panel.kind) for panel in panels}
        self.assertNotIn((2, 13, "gate_charge"), identities)
        self.assertNotIn((6, 14, "capacitances"), identities)
        self.assertIn((7, 12, "gate_charge"), identities)
        self.assertIn((8, 14, "capacitances"), identities)
        self.assertIn((8, 15, "body_diode"), identities)

    @unittest.skipUnless(
        PH2925U.exists() and PSMN038.exists(), "local Nexperia controls unavailable"
    )
    def test_rejecting_one_table_row_does_not_reveal_another(self) -> None:
        with TemporaryDirectory(prefix="nexperia-table-neighbor-") as tmp:
            panels = [
                *find_charts.process_pdf(PH2925U, Path(tmp) / "ph2925", dpi=120),
                *find_charts.process_pdf(PSMN038, Path(tmp) / "psmn038", dpi=120),
            ]

        identities = {(panel.part, panel.page, panel.diagram) for panel in panels}
        self.assertNotIn(("PH2925U", 5, 951), identities)
        self.assertNotIn(("PSMN038-100YL", 6, 15), identities)
        self.assertNotIn(("PSMN038-100YL", 6, 16), identities)

    @unittest.skipUnless(
        BUK9K12.exists() and BUK9M16.exists(), "local Nexperia controls unavailable"
    )
    def test_open_gate_charge_table_row_does_not_become_axis_fallback(self) -> None:
        with TemporaryDirectory(prefix="nexperia-table-open-row-") as tmp:
            panels = [
                *find_charts.process_pdf(BUK9K12, Path(tmp) / "buk9k12", dpi=120),
                *find_charts.process_pdf(BUK9M16, Path(tmp) / "buk9m16", dpi=120),
            ]

        identities = {(panel.part, panel.page, panel.diagram) for panel in panels}
        self.assertNotIn(("BUK9K12-80L", 5, 951), identities)
        self.assertNotIn(("BUK9M16-100L", 5, 951), identities)


if __name__ == "__main__":
    unittest.main()
