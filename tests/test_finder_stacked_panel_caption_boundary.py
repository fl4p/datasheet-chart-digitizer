"""Caption boundaries between vertically stacked panels in one column.

Calibrated against Infineon IAUTN12S5N018G page 8, a 2x2 layout whose
"10 Typ. capacitances" crop swallowed the "12 Typ. avalanche characteristics"
plot below it and then digitized the avalanche chart as capacitance.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from datasheet_chart_digitizer import find_charts as fc
from datasheet_chart_digitizer.finder_grid_geometry import (
    grid_rows_belong_to_same_panel,
)


@dataclass
class _Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def _caption_row(number: str, title_words: list[str], y: float, x0: float) -> list[_Word]:
    """Lay out ``<number> <Title...>`` as a caption line starting at ``x0``."""
    words = [_Word(number, x0, y - 5.0, x0 + 10.0, y + 5.0)]
    cursor = x0 + 14.0
    for token in title_words:
        width = 6.0 * len(token)
        words.append(_Word(token, cursor, y - 5.0, cursor + width, y + 5.0))
        cursor += width + 4.0
    return words


COLUMN = (283.0, 531.0)


class StackedPanelCaptionBoundaryTests(unittest.TestCase):
    def test_bare_numbered_caption_splits_short_frame_bracketed_gap(self) -> None:
        # The real IAUTN12S5N018G geometry: panel 10's outer frame bottom at
        # 383.3, the caption at 399.8, panel 12's frame top at 410.3.  The
        # 27 pt gap used to short-circuit to "same panel" without looking.
        words = _caption_row("12", ["Typ.", "avalanche", "characteristics"], 399.8, 290.0)
        self.assertFalse(
            grid_rows_belong_to_same_panel(words, 383.3, 410.3, *COLUMN)
        )

    def test_figure_caption_also_splits_a_short_gap(self) -> None:
        words = _caption_row("Figure", ["12", "Avalanche"], 399.8, 290.0)
        self.assertFalse(
            grid_rows_belong_to_same_panel(words, 383.3, 410.3, *COLUMN)
        )

    def test_far_apart_rules_stay_separate_panels(self) -> None:
        self.assertFalse(grid_rows_belong_to_same_panel([], 100.0, 200.0, *COLUMN))

    def test_empty_gap_still_bridges_one_missing_grid_row(self) -> None:
        self.assertTrue(grid_rows_belong_to_same_panel([], 337.3, 383.3, *COLUMN))

    def test_condition_line_number_is_not_a_caption(self) -> None:
        # "C = f(V DS ); V GS = 0 V; f = 1 MHz" sits between the panel frame
        # top and the first grid row.  Its "1" is adjacent to an alphabetic
        # token but never OPENS the line, so the panel must not split.
        y = 90.0
        words = [
            _Word("C", 290.0, y - 5, 297.0, y + 5),
            _Word("=", 300.0, y - 5, 306.0, y + 5),
            _Word("f(V", 309.0, y - 5, 325.0, y + 5),
            _Word("GS", 328.0, y - 5, 340.0, y + 5),
            _Word("=", 343.0, y - 5, 349.0, y + 5),
            _Word("0", 352.0, y - 5, 358.0, y + 5),
            _Word("V;", 361.0, y - 5, 370.0, y + 5),
            _Word("f", 374.0, y - 5, 378.0, y + 5),
            _Word("=", 381.0, y - 5, 387.0, y + 5),
            _Word("1", 390.0, y - 5, 396.0, y + 5),
            _Word("MHz", 400.0, y - 5, 420.0, y + 5),
        ]
        self.assertTrue(grid_rows_belong_to_same_panel(words, 75.2, 106.7, *COLUMN))

    def test_tick_label_sharing_a_line_with_a_legend_is_not_a_caption(self) -> None:
        # A Y tick label opens its line inside the column and a far-right
        # legend entry follows on the same line.  Only the 14 pt adjacency
        # requirement separates this from a caption.
        y = 200.0
        words = [
            _Word("10", 300.0, y - 5, 312.0, y + 5),
            _Word("Ciss", 480.0, y - 5, 500.0, y + 5),
        ]
        self.assertTrue(grid_rows_belong_to_same_panel(words, 194.4, 224.6, *COLUMN))

    def test_left_column_caption_does_not_split_the_right_column(self) -> None:
        # Page-wide text lines carry both columns' caption numbers.  "11" opens
        # the line on the page, but inside the right column "12" opens it.
        words = _caption_row("11", ["Typical", "forward", "diode"], 399.8, 30.0)
        self.assertTrue(
            grid_rows_belong_to_same_panel(words, 383.3, 410.3, *COLUMN)
        )
        words += _caption_row("12", ["Typ.", "avalanche"], 399.8, 290.0)
        self.assertFalse(
            grid_rows_belong_to_same_panel(words, 383.3, 410.3, *COLUMN)
        )

    def test_condition_number_survives_a_band_that_clips_its_line_start(self) -> None:
        """The regression the full-corpus A/B caught, as a unit test.

        onsemi NTMFS010N10GTWG p4: the rule pair bounding this gap yields a
        column band starting at x=107.3, which clips the ``f =`` off
        ``f = 1 MHz`` and left ``1`` looking like it opened the line.  34
        capacitance panels split below their own x tick labels.
        """
        y = 203.83
        words = [
            _Word("f", 98.6, y - 4, 103.2, y + 4),
            _Word("=", 104.0, y - 4, 109.0, y + 4),
            _Word("1", 111.1, y - 4, 115.5, y + 4),
            _Word("MHz", 118.0, y - 4, 133.8, y + 4),
        ]
        # Band from the real failing call: x0=138.6, x1=295.2 -> left edge 107.3.
        self.assertTrue(
            grid_rows_belong_to_same_panel(words, 202.50, 206.46, 138.6, 295.2)
        )

    def test_tick_label_beside_an_in_plot_annotation_is_not_a_caption(self) -> None:
        """Second regression the A/B caught: FDB15N50 / IRF644S class.

        The Y tick ``50`` sits ~5 pt left of the in-plot annotation
        ``VDD = 100V`` and opens its own line, so geometry alone read it as a
        caption and truncated a transfer plot's top.  Only the absence of a
        caption phrase separates the two.
        """
        y = 336.0
        words = [
            _Word("50", 99.6, y - 4, 106.8, y + 4),
            _Word("VDD", 112.0, y - 1, 124.0, y + 7),
            _Word("=", 126.0, y - 1, 130.0, y + 7),
            _Word("100V", 132.0, y - 1, 148.0, y + 7),
        ]
        self.assertTrue(
            grid_rows_belong_to_same_panel(words, 313.56, 336.5, 107.3, 288.0)
        )

    def test_embedded_figure_crossref_in_a_short_gap_is_not_a_caption(self) -> None:
        """IRF644S figure 6 prints "For test circuit see figure 13" in-plot.

        The pre-existing keyword rule never saw it while short gaps were
        skipped unscanned; once they are scanned it must not read a mid-sentence
        "figure" as a panel caption.
        """
        y = 649.1
        words = [
            _Word("see", 490.0, y - 4, 501.6, y + 4),
            _Word("figure", 503.6, y - 4, 520.6, y + 4),
            _Word("13", 522.6, y - 4, 530.4, y + 4),
        ]
        self.assertTrue(
            grid_rows_belong_to_same_panel(words, 626.58, 653.22, 348.1, 567.4)
        )

    def test_line_opening_figure_caption_in_a_short_gap_still_splits(self) -> None:
        y = 649.1
        words = [
            _Word("Figure", 135.6, y - 4, 160.0, y + 4),
            _Word("7.", 162.0, y - 4, 170.0, y + 4),
            _Word("Capacitance", 172.0, y - 4, 220.0, y + 4),
        ]
        self.assertFalse(
            grid_rows_belong_to_same_panel(words, 626.58, 653.22, 120.0, 300.0)
        )

    def test_wide_gaps_keep_their_pre_change_behaviour(self) -> None:
        """A gap over 28 pt was already scanned; do not alter its verdict."""
        y = 340.0
        embedded = [
            _Word("see", 490.0, y - 4, 501.6, y + 4),
            _Word("figure", 503.6, y - 4, 520.6, y + 4),
        ]
        # 43 pt gap: the keyword still splits, exactly as it did before.
        self.assertFalse(
            grid_rows_belong_to_same_panel(embedded, 313.0, 356.0, 460.0, 560.0)
        )

    def test_caption_number_above_fifty_is_not_a_panel_caption(self) -> None:
        words = _caption_row("100", ["Volts", "per", "division"], 399.8, 290.0)
        self.assertTrue(
            grid_rows_belong_to_same_panel(words, 383.3, 410.3, *COLUMN)
        )


class StackedPanelRegionEndToEndTests(unittest.TestCase):
    PDF = Path(
        "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon/IAUTN12S5N018GATMA1.pdf"
    )

    @unittest.skipUnless(PDF.exists(), "corpus datasheet not available")
    def test_capacitance_panel_excludes_the_avalanche_plot_below(self) -> None:
        pages = fc.run_text_bbox(self.PDF)
        page = next(item for item in pages if item.page_num == 8)
        with TemporaryDirectory(prefix="stacked-panel-") as tmp:
            page_png = fc.render_page(self.PDF, 8, 200, Path(tmp))
            from PIL import Image

            with Image.open(page_png) as rendered:
                width_px, height_px = rendered.size
            _, h_rules_px = fc.detect_rule_boxes(page_png)
            h_rules_pt = [
                fc.box_px_to_pt(box, width_px, height_px, page) for box in h_rules_px
            ]

        regions = fc.infer_grid_regions_from_h_rules(page, h_rules_pt)
        # A 2x2 layout must yield four regions, not two column-tall ones.
        self.assertEqual(4, len(regions))

        title = next(
            item for item in fc.find_caption_titles(page) if item.number == 10
        )
        bbox = fc.choose_caption_panel_bbox(page, title, regions)
        assert bbox is not None
        # Panel 12's caption sits at y=399.8; the capacitance panel must end
        # above it rather than reaching the avalanche grid at y>=440.
        self.assertLess(bbox[3], 399.8)
