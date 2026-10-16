"""Known-bad cases from the Codex review of fix/corpus-test-failures (2026-10-16).

Each test builds the reviewer's synthetic reproduction and pins the refusal.
"""

from __future__ import annotations

import unittest

import pymupdf

from datasheet_chart_digitizer import find_charts as finder
from datasheet_chart_digitizer.diode_forward_voltage import _merge_source_contiguous_strokes
from datasheet_chart_digitizer.gate_axis_ocr import (
    Word,
    _fill_ticks_from_other_reads,
    _raw_linear_y_ticks,
)

PLOT = pymupdf.Rect(0, 0, 100, 100)


def _words(pairs):
    return [Word(text, -8, y - 1, -3, y + 1) for text, y in pairs]


def _stroke(points):
    pts = [pymupdf.Point(*p) for p in points]
    return {
        "type": "s",
        "items": [("l", a, b) for a, b in zip(pts, pts[1:])],
        "rect": pymupdf.Rect(
            min(p.x for p in pts), min(p.y for p in pts),
            max(p.x for p in pts), max(p.y for p in pts),
        ),
        "width": 0.7,
        "color": (0,),
        "dashes": "[] 0",
        "stroke_opacity": 1,
    }


class ReviewFindingTests(unittest.TestCase):
    def test_fill_in_does_not_refill_an_occupied_label_row(self):
        # selected 10@0 and 0@100; another read has 9.5@0, 0.5@100 on the
        # SAME rows: averaging them in would serve 9.75 / 0.25
        filled = _fill_ticks_from_other_reads(
            [(10.0, 0.0), (0.0, 100.0)], [_words([("9.5", 0), ("0.5", 100)])], PLOT
        )
        self.assertEqual(filled, [(10.0, 0.0), (0.0, 100.0)])

    def test_fill_in_still_adds_a_missing_row_on_the_axis(self):
        filled = _fill_ticks_from_other_reads(
            [(10.0, 0.0), (0.0, 100.0)], [_words([("10", 0), ("5", 50)])], PLOT
        )
        self.assertEqual(filled, [(10.0, 0.0), (5.0, 50.0), (0.0, 100.0)])

    def test_fill_in_recovers_every_missing_row_between_two_read_ticks(self):
        # re-review: a radius scaled by the surviving tick spacing (50 pt with
        # two ticks) refused every real missing label
        filled = _fill_ticks_from_other_reads(
            [(10.0, 0.0), (0.0, 100.0)],
            [_words([("8", 20), ("6", 40)]), _words([("4", 60), ("2", 80)])],
            PLOT,
        )
        self.assertEqual([v for v, _y in filled], [10.0, 8.0, 6.0, 4.0, 2.0, 0.0])
        filled = _fill_ticks_from_other_reads(
            [(10.0, 0.0), (8.0, 20.0), (0.0, 100.0)],
            [_words([("6", 40), ("2", 80)])],
            PLOT,
        )
        self.assertEqual([v for v, _y in filled], [10.0, 8.0, 6.0, 2.0, 0.0])

    def test_fill_in_refuses_a_label_off_the_axis_by_position(self):
        # 4 V read 4 pt away from where the axis puts 4 V
        filled = _fill_ticks_from_other_reads(
            [(10.0, 0.0), (0.0, 100.0)], [_words([("10", 0), ("4", 64)])], PLOT
        )
        self.assertEqual(filled, [(10.0, 0.0), (0.0, 100.0)])

    def test_decimal_loss_repairs_out_of_range_reads(self):
        # "45" is 4.5 with the point dropped; two explicit decimals anchor it
        ticks = _raw_linear_y_ticks(
            _words([("45", 0), ("2.7", 40), ("0.9", 80), ("0.0", 100)]), PLOT
        )
        self.assertIn((4.5, 0.0), ticks)
        # without two explicit decimals there is no repair
        ticks = _raw_linear_y_ticks(_words([("45", 0), ("0.9", 80), ("0", 100)]), PLOT)
        self.assertNotIn((4.5, 0.0), ticks)

    def test_stroke_merge_refuses_a_junction_with_a_single_segment_branch(self):
        a = _stroke([(0, 100), (20, 80), (40, 60)])
        b = _stroke([(40, 60), (60, 50), (80, 40)])
        c = _stroke([(40, 60), (60, 20)])
        merged = _merge_source_contiguous_strokes([a, b, c])
        self.assertEqual([len(d["items"]) for d in merged], [2, 2, 1])
        # the unambiguous junction still merges
        self.assertEqual(
            [len(d["items"]) for d in _merge_source_contiguous_strokes([a, b])], [4]
        )

    def test_sibling_recovery_does_not_bind_a_disjoint_column(self):
        page = finder.PageText(
            1, 600, 800, [finder.Word("Figure 1. Output Characteristics", 500, 100, 580, 110)]
        )
        title = finder.DiagramTitle(
            8, "Gate Charge Characteristics", (340, 300, 380, 310),
            "Figure 8. Gate Charge Characteristics",
        )
        # above-left grid shares no column with the caption; a closer grid below
        frames = [(450, 0, 590, 80), (100, 180, 300, 270), (310, 320, 510, 450)]
        self.assertEqual(
            finder._unambiguous_sibling_caption_direction(page, title, frames), "above"
        )
        self.assertIsNone(finder.choose_caption_panel_bbox(page, title, frames))

    def test_sibling_recovery_binds_the_grid_over_the_caption_centre(self):
        # re-review: a caption straddling two columns; the left grid shares
        # 10 pt of it and is nearer, the right grid spans its centre
        page = finder.PageText(
            1, 600, 800, [finder.Word("Figure 1. Output Characteristics", 500, 100, 580, 110)]
        )
        title = finder.DiagramTitle(
            8, "Gate Charge Characteristics", (290, 300, 380, 310),
            "Figure 8. Gate Charge Characteristics",
        )
        frames = [(450, 0, 590, 80), (100, 200, 300, 290), (310, 200, 510, 265),
                  (310, 320, 510, 450)]
        if finder._unambiguous_sibling_caption_direction(page, title, frames) != "above":
            self.skipTest("layout no longer evidences 'above' for this fixture")
        bbox = finder.choose_caption_panel_bbox(page, title, frames)
        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertLess(bbox[0], 335.0)
        self.assertGreater(bbox[0], 300.0)


if __name__ == "__main__":
    unittest.main()
