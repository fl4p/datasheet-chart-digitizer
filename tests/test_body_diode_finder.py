from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from datasheet_chart_digitizer.find_charts import (
    DiagramTitle,
    PageText,
    Word,
    choose_caption_axis_label_bbox,
    choose_caption_axis_label_bbox_for_kind,
    choose_caption_panel_bbox,
    choose_caption_synthetic_bbox,
    classify_chart,
    find_caption_titles,
    process_pdf,
    run_text_bbox,
)


def _words(text: str, *, x: float, y: float) -> list[Word]:
    out: list[Word] = []
    for token in text.split():
        width = max(8.0, 5.5 * len(token))
        out.append(Word(token, x, y, x + width, y + 10.0))
        x += width + 4.0
    return out


def _page(*lines: tuple[str, float, float]) -> PageText:
    words = [word for text, x, y in lines for word in _words(text, x=x, y=y)]
    return PageText(page_num=1, width_pt=612.0, height_pt=792.0, words=words)


class BodyDiodeFinderUnitTests(unittest.TestCase):
    def test_classifier_requires_diode_context_and_rejects_recovery(self):
        positives = (
            "Typ. forward characteristics of reverse diode",
            "Body Diode Forward Voltage",
            "Diode Forward Voltage vs. Current",
            "Body-Diode Characteristics (Note E)",
        )
        for title in positives:
            with self.subTest(title=title):
                self.assertEqual(classify_chart(title, ""), "body_diode")

        negatives = (
            "Peak Diode Recovery dv/dt Test Circuit & Waveforms",
            "Forward Voltage",
            "Forward Characteristics",
        )
        for title in negatives:
            with self.subTest(title=title):
                self.assertNotEqual(classify_chart(title, ""), "body_diode")

        noisy_neighbor = " ".join(["capacitance Ciss Coss Crss"] * 20)
        self.assertEqual(
            classify_chart("Diode Forward Voltage vs. Current", noisy_neighbor),
            "body_diode",
        )

    def test_body_diode_caption_prefers_above_but_accepts_adjacent_plot_below(self):
        page = _page(("Figure 4. Body Diode Forward Voltage", 300.0, 300.0))
        title = find_caption_titles(page)[0]
        above = (290.0, 100.0, 540.0, 275.0)
        below = (290.0, 320.0, 540.0, 495.0)

        selected = choose_caption_panel_bbox(page, title, [above, below])
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertLess(selected[3], title.bbox_pt[1])
        self.assertIsNotNone(choose_caption_panel_bbox(page, title, [below]))
        self.assertIsNone(
            choose_caption_panel_bbox(page, title, [(290.0, 360.0, 540.0, 535.0)])
        )

    def test_qg_axis_fallback_is_owned_only_by_gate_charge(self):
        page = _page(
            ("Qg Gate Charge nC", 250.0, 250.0),
            ("Figure 4. Body Diode Forward Voltage", 300.0, 300.0),
        )
        body = DiagramTitle(4, "Body Diode Forward Voltage", (300, 300, 500, 310), "Figure 4")
        gate = DiagramTitle(5, "Gate Charge Characteristics", (300, 300, 500, 310), "Figure 5")
        breakdown = DiagramTitle(6, "Drain-source breakdown voltage", (300, 300, 500, 310), "Figure 6")

        self.assertIsNotNone(choose_caption_axis_label_bbox(page, body))
        self.assertIsNone(choose_caption_axis_label_bbox_for_kind(page, body))
        self.assertEqual(
            choose_caption_axis_label_bbox_for_kind(page, gate),
            choose_caption_axis_label_bbox(page, gate),
        )
        self.assertIsNone(choose_caption_axis_label_bbox_for_kind(page, breakdown))


class BodyDiodeFinderCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.environ.get("DSDIG_DATASHEET_ROOT")
        cls.datasheets = Path(root) / "datasheets" if root else None

    def _pdf(self, relative: str) -> Path:
        if self.datasheets is None:
            self.skipTest("DSDIG_DATASHEET_ROOT is not set")
        pdf = self.datasheets / relative
        if not pdf.exists():
            self.skipTest(f"missing local corpus PDF: {pdf}")
        return pdf

    def _body_panels(self, relative: str):
        with tempfile.TemporaryDirectory() as tmp:
            panels = process_pdf(self._pdf(relative), Path(tmp), dpi=180)
        return [panel for panel in panels if panel.kind == "body_diode"]

    def test_three_acceptance_panels_have_exact_identity_and_local_bbox(self):
        cases = (
            ("infineon/IPP024N08NF2S.pdf", 8, 12, (311.63, 451.941, 580.88, 755.301)),
            ("onsemi/FDA032N08.pdf", 3, 4, (281.211, 267.302, 612.0, 478.302)),
            ("diodes/DMTH83M2SPSWQ-13.pdf", 5, 9, (27.59, 299.27, 367.59, 510.27)),
        )
        for relative, page, diagram, expected_bbox in cases:
            with self.subTest(pdf=relative):
                panels = self._body_panels(relative)
                self.assertEqual([(p.page, p.diagram) for p in panels], [(page, diagram)])
                for actual, expected in zip(panels[0].bbox_pt, expected_bbox):
                    self.assertAlmostEqual(actual, expected, delta=0.15)
        dmth = self._body_panels("diodes/DMTH83M2SPSWQ-13.pdf")[0]
        self.assertGreaterEqual(dmth.bbox_pt[2], 347.0)
        self.assertLess(dmth.bbox_pt[2], 370.0)

    def test_dmth_synthetic_bbox_stays_in_left_column(self):
        page = run_text_bbox(self._pdf("diodes/DMTH83M2SPSWQ-13.pdf"))[4]
        title = next(title for title in find_caption_titles(page) if title.number == 9)
        self.assertEqual(
            tuple(round(value, 3) for value in choose_caption_synthetic_bbox(page, title) or ()),
            (27.59, 299.27, 367.59, 510.27),
        )

    def test_recovery_circuit_is_not_body_diode_and_aot_positive_survives(self):
        fda = self._body_panels("onsemi/FDA032N08.pdf")
        self.assertEqual([(panel.page, panel.diagram) for panel in fda], [(3, 4)])
        aot = self._body_panels("ao/AOT414.pdf")
        self.assertIn((3, 6), [(panel.page, panel.diagram) for panel in aot])


if __name__ == "__main__":
    unittest.main()
