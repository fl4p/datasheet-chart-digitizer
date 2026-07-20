import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from datasheet_chart_digitizer import mosfet_capacitance as mc
from datasheet_chart_digitizer.chart_classifier import (
    classify_chart,
    strong_noncapacitance_panel_kind,
    title_owns_chart_kind,
)


SOA_TEXT = (
    "(A) Current Drain I_D Vds Drain-Source Voltage (V) "
    "10us 100us 1ms 10ms DC Figure 13 Safe Operation Area"
)
REVERSE_DIODE_TEXT = (
    "(A) Current Drain Reverse I_s Vds Drain-Source Voltage (V) "
    "Figure 10 Reverse Drain Diode Forward"
)


class CapacitancePanelSemanticTests(unittest.TestCase):
    def test_owned_soa_overrides_misbound_capacitance_caption(self) -> None:
        self.assertEqual(
            "safe_operating_area",
            title_owns_chart_kind("Capacitance vs Vds", 10, SOA_TEXT),
        )
        self.assertEqual("safe_operating_area", classify_chart("", SOA_TEXT))

    def test_owned_reverse_diode_overrides_misbound_caption(self) -> None:
        self.assertEqual(
            "body_diode",
            title_owns_chart_kind("Capacitance vs Vds", 8, REVERSE_DIODE_TEXT),
        )

    def test_capacitance_identity_vetoes_following_caption_bleed(self) -> None:
        text = (
            "Capacitance (pF) Ciss Coss Crss Vds Drain-Source Voltage (V) "
            "Figure 12 Safe Operating Area"
        )
        self.assertIsNone(strong_noncapacitance_panel_kind(text))
        self.assertEqual(
            "capacitances",
            title_owns_chart_kind("Capacitance Characteristics", 11, text),
        )

    def test_split_capacitance_identities_veto_false_contradiction(self) -> None:
        text = (
            "C Input Capacitance iss C Output Capacitance oss "
            "C Reverse Transfer Capacitance rss"
        )
        self.assertIsNone(strong_noncapacitance_panel_kind(text))

    def test_digitizer_refuses_stale_wrong_panel_before_reading_crop(self) -> None:
        with TemporaryDirectory(prefix="noncap-panel-") as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(
                RuntimeError,
                "panel semantics identify safe_operating_area, not capacitance",
            ):
                mc.process_chart(
                    {"part": "fixture", "text": SOA_TEXT},
                    root / "missing.png",
                    root,
                    Path("fixture"),
                    root,
                )


if __name__ == "__main__":
    unittest.main()
