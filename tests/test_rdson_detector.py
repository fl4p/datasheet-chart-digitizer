from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from datasheet_chart_digitizer import find_charts
from datasheet_chart_digitizer.finder_caption_geometry import caption_axis_direction
from datasheet_chart_digitizer.rdson_temperature import digitize_pdf as digitize_temperature


PDF = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/hxy/"
    "SPD03N50C3ATMA1-HXY.pdf"
)


class RdsonDetectorTests(unittest.TestCase):
    def test_both_title_families_classify_as_rdson(self) -> None:
        for title in (
            "On-resistance vs. Drain Current",
            "Normalized on Resistance vs. Junction Temperature",
            "Normalized On-State Resistance vs Temperature",
            "Typ. drain-source on resistance",
            "RDS(on) vs. TJ",
        ):
            with self.subTest(title=title):
                self.assertEqual(find_charts.classify_chart(title, ""), "rds_on")

    def test_rdson_title_match_is_not_an_rds_substring_search(self) -> None:
        for title in (
            "Compliance with safety standards",
            "Stored energy versus drain-source voltage",
            "Forward source-drain characteristics",
        ):
            with self.subTest(title=title):
                self.assertNotEqual(find_charts.classify_chart(title, ""), "rds_on")

    def test_coss_stored_energy_is_not_a_capacitance_family_panel(self) -> None:
        self.assertEqual(
            find_charts.classify_chart("Typ. Coss stored energy", ""),
            "coss_energy",
        )

    def test_split_id_axis_is_direction_evidence(self) -> None:
        title = find_charts.DiagramTitle(
            3,
            "On-resistance vs. Drain Current",
            (100.0, 100.0, 270.0, 112.0),
            "Figure 3: On-resistance vs. Drain Current",
        )
        page = find_charts.PageText(
            1,
            600.0,
            800.0,
            [
                find_charts.Word("I", 160.0, 300.0, 166.0, 311.0),
                find_charts.Word("D", 166.0, 300.0, 172.0, 311.0),
                find_charts.Word("(A)", 173.0, 300.0, 190.0, 311.0),
            ],
        )
        self.assertEqual(
            caption_axis_direction(page, title, "rds_on", find_charts._token_norm),
            "below",
        )

    def test_spd03_detects_seven_owned_panels(self) -> None:
        if not PDF.exists():
            self.skipTest(f"missing local corpus fixture: {PDF}")
        with tempfile.TemporaryDirectory() as tmp:
            panels = find_charts.process_pdf(PDF, Path(tmp), 220)
        self.assertEqual(
            [(panel.page, panel.diagram, panel.kind) for panel in panels],
            [
                (3, 2, "transfer"),
                (3, 3, "rds_on"),
                (3, 4, "body_diode"),
                (3, 5, "gate_charge"),
                (3, 6, "capacitances"),
                (4, 7, "breakdown_voltage"),
                (4, 8, "rds_on"),
            ],
        )
        current = next(panel for panel in panels if panel.diagram == 3)
        temperature = next(panel for panel in panels if panel.diagram == 8)
        self.assertGreater(current.bbox_pt[1], 368.0)
        self.assertLess(current.bbox_pt[2], 300.0)
        self.assertLess(temperature.bbox_pt[0], 310.0)
        self.assertGreater(temperature.bbox_pt[1], 139.0)
        self.assertGreater(temperature.bbox_pt[3], 301.0)

    def test_spd03_temperature_direct_plugin_accepts_caption_above_plot(self) -> None:
        if not PDF.exists():
            self.skipTest(f"missing local corpus fixture: {PDF}")
        with tempfile.TemporaryDirectory() as tmp:
            result = digitize_temperature(PDF, Path(tmp), dpi=220)[0]
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual((result["panel"]["page"], result["panel"]["diagram"]), (4, 8))
        self.assertAlmostEqual(
            result["curves"][0]["normalized_rds_on_at_25c"], 1.0, delta=0.06
        )


if __name__ == "__main__":
    unittest.main()
