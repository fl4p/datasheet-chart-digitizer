"""Caption-below-plot layouts whose plot yields no grid region (dotted/no grid).

The finder's synthetic fallback used page position alone and put the plot
BELOW any caption in the top 35 % of the page. These tests construct the
layouts that decide the side: a plot above its caption (flip), a stacked
caption-above layout where the plot above belongs to an earlier caption (no
flip: never steal the neighbour), and side-by-side captions on one row.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from datasheet_chart_digitizer.finder_caption_side import (
    bound_to_caption_column,
    caption_owns_plot_above,
)

W = 595.0
CAPTION = (100.0, 290.0, 260.0, 299.0)


def _is_caption(text: str) -> bool:
    return text.lower().startswith(("fig", "figure", "diagram"))


class CaptionSideTests(unittest.TestCase):
    def test_tick_row_directly_above_flips(self):
        lines = [("0 10 20 30", (95.0, 268.0, 265.0, 276.0)),
                 ("VDS, Drain-Source Voltage (V)", (120.0, 279.0, 240.0, 287.0))]
        self.assertTrue(caption_owns_plot_above(CAPTION, lines, W, None, _is_caption))

    def test_own_axis_title_above_flips(self):
        self.assertTrue(caption_owns_plot_above(CAPTION, [], W, "above", _is_caption))

    def test_earlier_caption_above_owns_that_plot(self):
        lines = [("Figure 2. Output Characteristics", (100.0, 60.0, 260.0, 69.0)),
                 ("0 10 20 30", (95.0, 268.0, 265.0, 276.0))]
        self.assertFalse(caption_owns_plot_above(CAPTION, lines, W, None, _is_caption))
        self.assertFalse(caption_owns_plot_above(CAPTION, lines, W, "above", _is_caption))

    def test_axis_evidence_below_never_flips(self):
        lines = [("0 10 20 30", (95.0, 268.0, 265.0, 276.0))]
        self.assertFalse(caption_owns_plot_above(CAPTION, lines, W, "below", _is_caption))

    def test_tick_row_in_the_other_column_or_far_above_is_not_evidence(self):
        other_column = [("0 10 20 30", (400.0, 268.0, 560.0, 276.0))]
        far = [("0 10 20 30", (95.0, 200.0, 265.0, 208.0))]
        prose = [("see note 3 and table 1 for details", (95.0, 268.0, 265.0, 276.0))]
        for lines in (other_column, far, prose):
            self.assertFalse(caption_owns_plot_above(CAPTION, lines, W, None, _is_caption))

    def test_same_row_caption_trims_and_recentres_a_merged_title(self):
        words = [("Fig.", (105.0, 271.0, 127.0, 284.0)), ("11", (131.0, 271.0, 146.0, 284.0)),
                 ("Typ.", (149.0, 271.0, 173.0, 284.0)), ("capacitances", (177.0, 271.0, 253.0, 284.0)),
                 ("Fig.", (403.0, 271.0, 414.0, 284.0)), ("12", (416.0, 271.0, 423.0, 284.0)),
                 ("Typical", (425.0, 271.0, 446.0, 284.0)), ("Core", (448.0, 271.0, 462.0, 284.0)),
                 ("Loss", (464.0, 271.0, 477.0, 284.0)), ("B", (508.0, 271.0, 512.0, 284.0))]
        merged_title = (131.0, 271.0, 527.0, 284.0)  # the parser appended "B [mT]"
        box = bound_to_caption_column((91.0, 54.0, 567.0, 267.0), merged_title, words, 170.0, W)
        self.assertLessEqual(box[0], 20.0)          # own y labels kept
        self.assertLess(box[2], 403.0)              # neighbour caption's column excluded
        self.assertGreater(box[2], 253.0)

    def test_lone_caption_is_unchanged(self):
        words = [("Figure", (100.0, 290.0, 130.0, 299.0)), ("3.", (132.0, 290.0, 140.0, 299.0))]
        bbox = (20.0, 75.0, 340.0, 286.0)
        self.assertEqual(bound_to_caption_column(bbox, CAPTION, words, 170.0, W), bbox)


class FinderEndToEndTests(unittest.TestCase):
    """A generated vector page: dotted-grid C(V) plot in the top row, caption below."""

    def _page(self, path: Path, *, caption_above_layout: bool) -> tuple[float, float, float, float]:
        import fitz  # type: ignore

        doc = fitz.open()
        page = doc.new_page(width=W, height=842)
        page.insert_text((40, 30), "SYN0001N  N-Channel Power MOSFET", fontsize=9)
        x0, y0, x1, y1 = 90.0, 90.0, 270.0, 250.0
        if caption_above_layout:
            page.insert_text((100, 75), "Figure 2. Output Characteristics", fontsize=8)
        shape = page.new_shape()
        shape.draw_line((x0, y0), (x0, y1))
        shape.draw_line((x0, y1), (x1, y1))
        shape.finish(color=(0, 0, 0), width=1.0)
        for k in range(1, 4):
            x = x0 + k * (x1 - x0) / 3
            shape.draw_line((x, y0), (x, y1))
            y = y1 - k * (y1 - y0) / 3
            shape.draw_line((x0, y), (x1, y))
        shape.finish(color=(0.5, 0.5, 0.5), width=0.5, dashes="[1 2] 0")
        shape.commit()
        for k, label in enumerate(("0", "10", "20", "30")):
            page.insert_text((x0 + k * (x1 - x0) / 3 - 4, y1 + 12), label, fontsize=7)
        for k, label in enumerate(("10", "100", "1000", "10000")):
            page.insert_text((x0 - 26, y1 - k * (y1 - y0) / 3 + 3), label, fontsize=7)
        page.insert_text((140, y1 + 24), "VDS, Drain-Source Voltage (V)", fontsize=7)
        page.insert_text((60, 190), "C [pF]", fontsize=7, rotate=90)
        page.insert_text((110, y1 + 40), "Figure 3. Typical Capacitance", fontsize=8)
        doc.save(path)
        return (x0, y0, x1, y1)

    def _crops(self, caption_above_layout: bool):
        from datasheet_chart_digitizer.find_charts import process_pdf

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "page.pdf"
            plot = self._page(pdf, caption_above_layout=caption_above_layout)
            panels = process_pdf(pdf, Path(tmp) / "out", 150)
        return plot, {p.diagram: p.crop_box_pt for p in panels}

    def test_plot_above_its_caption_is_cropped(self):
        plot, crops = self._crops(caption_above_layout=False)
        self.assertIn(3, crops)
        c = crops[3]
        self.assertTrue(c[0] <= plot[0] and c[1] <= plot[1] and c[2] >= plot[2] and c[3] >= plot[3], c)

    def test_plot_owned_by_an_earlier_caption_is_not_taken(self):
        plot, crops = self._crops(caption_above_layout=True)
        c = crops.get(3)
        if c is not None:
            # figure 3 must not claim the plot that Figure 2 owns
            self.assertGreaterEqual(c[1], plot[3], c)


if __name__ == "__main__":
    unittest.main()
