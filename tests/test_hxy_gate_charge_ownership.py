import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pymupdf

from datasheet_chart_digitizer import find_charts, gate_charge
from datasheet_chart_digitizer import gate_charge_estimation as estimation


HXY = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/hxy/NTMD4840NR2G-HXY.pdf"
)
HXY_MATERIALIZED = HXY.exists() and HXY.stat().st_size > 1024


class HxyGateChargeOwnershipTests(unittest.TestCase):
    def test_ocr_qe_token_is_only_accepted_with_owned_charge_unit(self) -> None:
        label = "Qe, Total Gate Charge (nC)"

        self.assertFalse(find_charts._is_gate_charge_axis_label(label))
        self.assertTrue(
            find_charts._is_gate_charge_axis_label(label, ocr_tolerant=True)
        )
        self.assertFalse(
            find_charts._is_gate_charge_axis_label(
                "Qe, Normalized On Resistance", ocr_tolerant=True
            )
        )
        self.assertTrue(
            find_charts._is_gate_charge_axis_label(
                "Qs, Total Gate Charge (nC)", ocr_tolerant=True
            )
        )

    def test_y_axis_rejects_tick_run_that_straddles_remote_panels(self) -> None:
        page = mock.MagicMock()
        page.get_text.return_value = [
            (383.0, 295.0, 389.0, 299.0, "4"),
            (383.0, 727.0, 389.0, 731.0, "0"),
        ]
        panel = pymupdf.Rect(272.0, 330.0, 578.0, 536.0)

        self.assertIsNone(estimation._best_y_axis_for_panel(page, panel))

    def test_ocr_qe_axis_label_owns_numbered_gate_caption(self) -> None:
        page = find_charts.PageText(
            3,
            595.0,
            842.0,
            [
                find_charts.Word("Qe", 337.0, 516.0, 350.0, 529.0),
                find_charts.Word("Total", 355.0, 516.0, 382.0, 529.0),
                find_charts.Word("Gate", 387.0, 516.0, 415.0, 529.0),
                find_charts.Word("Charge", 420.0, 516.0, 460.0, 529.0),
                find_charts.Word("(nC)", 465.0, 516.0, 491.0, 529.0),
            ],
            "tesseract_fallback",
        )
        title = find_charts.DiagramTitle(
            4,
            "Gate-Charge Characteristics",
            (345.0, 538.0, 506.0, 553.0),
            "Fig.4 Gate-Charge Characteristics",
        )

        bbox = find_charts.choose_caption_axis_label_bbox_for_kind(page, title)

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLess(bbox[3], title.bbox_pt[1])

    @unittest.skipUnless(HXY_MATERIALIZED, "materialized HXY PDF unavailable")
    def test_finder_binds_figure_4_to_gate_charge_plot(self) -> None:
        with TemporaryDirectory(prefix="hxy-gate-finder-") as tmp:
            panels = find_charts.process_pdf(HXY, Path(tmp), dpi=120)

        gate = [panel for panel in panels if panel.kind == "gate_charge"]
        self.assertEqual(len(gate), 1)
        self.assertEqual((gate[0].page, gate[0].diagram), (3, 4))
        self.assertLess(gate[0].bbox_pt[1], 340.0)
        self.assertLess(gate[0].bbox_pt[3], 545.0)
        self.assertGreater(gate[0].bbox_pt[3], 525.0)

    @unittest.skipUnless(HXY_MATERIALIZED, "materialized HXY PDF unavailable")
    def test_digitizer_no_longer_rejects_owned_gate_plot_as_rds(self) -> None:
        results = gate_charge.digitize_gate_charge(HXY, finder_dpi=120)

        self.assertEqual(len(results), 1)
        self.assertNotEqual(results[0].status, "rejected_non_gate")
        self.assertNotIn("non_gate_plot:on_resistance", results[0].diagnostics)
        self.assertAlmostEqual(results[0].vpl or 0.0, 3.26, delta=0.15)
        self.assertFalse(results[0].to_manifest()["physical_output_available"])


if __name__ == "__main__":
    unittest.main()
