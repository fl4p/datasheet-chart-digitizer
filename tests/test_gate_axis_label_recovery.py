"""Lattice-rule recovery of printed gate-charge axis labels the bounded OCR missed.

Unit cases run on a synthetic raster (rules + label blobs) with a fake OCR
that returns every printed label whose box meets the OCR region -- the worst
case for a stray glyph. Real-PDF cases pin the five charts of review packet
corpus-fix-001 that Fab flagged for missing tick labels.
"""

import os
import unittest
from pathlib import Path

import numpy as np
import pymupdf

from datasheet_chart_digitizer import gate_axis_label_recovery as rec

SCALE = 3.0  # crop px per page point (216 dpi)
PITCH = 90  # px between rules = 30 pt -> label tolerance min(3, 2) = 2 pt
X0, Y0, X1, Y1 = 90, 30, 570, 480  # plot box, continuous crop px
CROP = (0.0, 0.0, 700 / SCALE, 600 / SCALE)


def _canvas():
    gray = np.full((600, 700), 255, dtype=np.uint8)
    gray[Y0:Y1 + 1, X0] = 0
    gray[Y0:Y1 + 1, X1] = 0
    gray[Y0, X0:X1 + 1] = 0
    gray[Y1, X0:X1 + 1] = 0
    return gray


def _y_chart(printed, *, rules=None, extra=()):
    """VGS 0..10 V on rules every PITCH px (10 V at Y0). *printed*: value -> (dy_pt)
    label centre offset from its rule; *extra*: (text, cx_pt, cy_pt) other glyphs."""
    gray = _canvas()
    rule_vals = rules if rules is not None else [0, 2, 4, 6, 8, 10]
    for v in rule_vals:
        y = int(Y1 - v / 2 * PITCH)
        gray[y, X0:X1] = 0
    words = []
    for text, cx, cy in [
        *((f"{v:g}", X0 / SCALE - 5.0, (Y1 - v / 2 * PITCH) / SCALE + dy) for v, dy in printed.items()),
        *extra,
    ]:
        x0, x1, y0, y1 = cx - 2.0, cx + 2.0, cy - 2.5, cy + 2.5
        gray[int(y0 * SCALE):int(y1 * SCALE), int(x0 * SCALE):int(x1 * SCALE)] = 0
        words.append((x0, y0, x1, y1, text))
    return gray, words


def _fake_ocr(words):
    def run(rect: pymupdf.Rect):
        return [w for w in words if pymupdf.Rect(w[:4]).intersects(rect)]
    return run


def _y_px(v):
    return float(Y1 - v / 2 * PITCH) + 0.5  # continuous px of the rule


def _recover_y(gray, words, served_values):
    served = [(float(v), _y_px(v)) for v in served_values]
    labels = [(v, p) for v, p in served]  # label centre on the rule (dy = 0)
    return rec.recover_axis_labels(
        gray, orientation="y", served_px=served, label_px=labels,
        plot_box=(X0, Y0, X1, Y1), crop_box_pt=CROP, scale=SCALE, tol_px=1.5,
        ocr=_fake_ocr(words),
    )


def _verdicts(recovery):
    return {c["value"]: c["verdict"] for c in recovery.record["candidates"]}


class LatticeTests(unittest.TestCase):
    def test_lattice_requires_whole_multiple_gaps(self):
        self.assertEqual(rec.label_lattice([(0, 500), (4, 300), (10, 0)]), None)
        step, slope, _ = rec.label_lattice([(10, 0), (20, 50), (50, 200)])
        self.assertEqual(step, 10)
        self.assertAlmostEqual(slope, 5.0)

    def test_candidates_stay_inside_the_plot(self):
        cands = rec.lattice_candidates([(10, 100.0), (20, 200.0), (50, 500.0)], 0.0, 800.0, 1.5)
        self.assertEqual([v for v, _ in cands], [0, 30, 40, 60, 70, 80])


class RecoveryTests(unittest.TestCase):
    def test_missing_labels_are_read_on_their_rules(self):
        gray, words = _y_chart({v: 0.0 for v in (0, 2, 4, 6, 8, 10)})
        out = _recover_y(gray, words, (10, 8, 2, 0))
        self.assertEqual(sorted(v for v, _ in out.labels_px), [4.0, 6.0])
        for v, px in out.labels_px:
            self.assertLess(abs(px - _y_px(v)), 3.0)

    def test_known_bad_a_different_number_is_refused(self):
        # the region of the 6 V rule holds a "5" (GSFL1003-style stray)
        gray, words = _y_chart({0: 0, 2: 0, 4: 0, 8: 0, 10: 0}, extra=(("5", X0 / SCALE - 5.0, _y_px(6) / SCALE),))
        out = _recover_y(gray, words, (10, 8, 2, 0))
        self.assertEqual([v for v, _ in out.labels_px], [4.0])
        self.assertIn("reads '5'", _verdicts(out)[6.0])

    def test_known_bad_an_empty_region_is_refused(self):
        gray, words = _y_chart({0: 0, 2: 0, 6: 0, 8: 0, 10: 0})  # no "4" printed
        out = _recover_y(gray, words, (10, 8, 2, 0))
        self.assertEqual([v for v, _ in out.labels_px], [6.0])
        self.assertIn("no digits", _verdicts(out)[4.0])

    def test_known_bad_the_right_number_off_its_rule_is_refused(self):
        # "4" printed 3 pt below its rule's label position: tolerance is 2 pt
        gray, words = _y_chart({0: 0, 2: 0, 4: 3.0, 6: 0, 8: 0, 10: 0})
        out = _recover_y(gray, words, (10, 8, 2, 0))
        self.assertEqual([v for v, _ in out.labels_px], [6.0])
        self.assertIn("off its rule", _verdicts(out)[4.0])
        # ... and the same label inside the tolerance is accepted
        gray, words = _y_chart({0: 0, 2: 0, 4: 1.0, 6: 0, 8: 0, 10: 0})
        out = _recover_y(gray, words, (10, 8, 2, 0))
        self.assertEqual(sorted(v for v, _ in out.labels_px), [4.0, 6.0])

    def test_no_rule_no_candidate(self):
        gray, words = _y_chart({v: 0.0 for v in (0, 2, 4, 6, 8, 10)}, rules=[0, 2, 6, 8, 10])
        out = _recover_y(gray, words, (10, 8, 2, 0))
        self.assertEqual([v for v, _ in out.labels_px], [6.0])
        self.assertIn("no observed rule", _verdicts(out)[4.0])

    def test_corner_zero_of_the_other_axis_is_not_taken(self):
        # VGS 0 V not printed; the Qg axis's "0" sits below the corner
        gray, words = _y_chart(
            {2: 0, 4: 0, 6: 0, 8: 0, 10: 0},
            extra=(("0", X0 / SCALE - 1.0, Y1 / SCALE + 5.0),),
        )
        out = _recover_y(gray, words, (10, 8, 6, 4, 2))
        self.assertEqual(out.labels_px, ())
        self.assertIn("off its rule", _verdicts(out)[0.0])

    def test_unestablished_label_geometry_recovers_nothing(self):
        # the served labels themselves do not read back in their regions
        gray, words = _y_chart({4: 0.0, 6: 0.0})
        out = _recover_y(gray, words, (10, 8, 2, 0))
        self.assertEqual(out.labels_px, ())
        self.assertIn("control reads", out.record["refused"])

    def test_x_axis_corner_zero_is_judged_by_its_own_row(self):
        gray = _canvas()
        for k in range(0, 9):
            gray[Y0:Y1, X0 + k * 60] = 0
        words = []
        row = Y1 / SCALE + 6.0
        for v in (10, 20, 30, 40):
            cx = (X0 + v / 10 * 120) / SCALE
            words.append((cx - 3, row - 2.5, cx + 3, row + 2.5, f"{v}"))
        # VGS "0" left of the corner, dropped a little below the bottom rule so
        # its glyph reaches the Qg label band; no Qg "0" printed
        words.append((X0 / SCALE - 7, Y1 / SCALE - 1.0, X0 / SCALE - 3, Y1 / SCALE + 4.0, "0"))
        for x0, y0, x1, y1, _t in words:
            gray[int(y0 * SCALE):int(y1 * SCALE), int(x0 * SCALE):int(x1 * SCALE)] = 0
        served = [(float(v), X0 + v / 10 * 120 + 0.5) for v in (10, 20, 30, 40)]
        out = rec.recover_axis_labels(
            gray, orientation="x", served_px=served, label_px=served,
            plot_box=(X0, Y0, X1, Y1), crop_box_pt=CROP, scale=SCALE, tol_px=1.5,
            ocr=_fake_ocr(words),
        )
        self.assertNotIn(0.0, [v for v, _ in out.labels_px])
        self.assertRegex(_verdicts(out)[0.0], "off its rule|row/column")


ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", "/Users/fab/dev/pv/pwr-mosfet-lib")) / "datasheets"


@unittest.skipUnless(ROOT.is_dir(), "datasheet root not available")
class RealChartTests(unittest.TestCase):
    """Packet corpus-fix-001 cards. Printed labels read off the page renders
    (2026-10-17); on 27c2559 the served ticks were the subsets in the comments.
    Vpl values are the 27c2559 serve; recovery moves them by < 0.002 V."""

    CASES = {
        # served 10/8/2/0; printed 10/8/6/4/2/0 (stray "5" at 5 V stays unserved)
        ("good_ark/GSFL1003.pdf", 3, 4): ([10, 8, 6, 4, 2, 0], [0, 4, 8, 12, 16, 20], 3.0587),
        # x served 10/20/50/60; printed 0..80 step 10
        ("nce/NCE0130GA.pdf", 4, 5): ([10, 8, 6, 4, 2, 0], [0, 10, 20, 30, 40, 50, 60, 70, 80], 3.3508),
        # y served 10/8/6/4/0; printed also 2
        ("nce/NCE6890K.pdf", 4, 5): ([10, 8, 6, 4, 2, 0], [0, 10, 20, 30, 40], 6.0224),
        # x served 25/50/100, printed 0/25/50/75/100: the bold JPEG "0" under the
        # corner reads as nothing at any dpi/psm tried, so it stays unserved
        # (refused as empty, never inferred); y served 10/8/6/4/0, printed also 2
        ("nce/NCEP025F90D.pdf", 4, 5): ([10, 8, 6, 4, 2, 0], [25, 50, 75, 100], 5.8751),
        # x served 4/12/16, printed 0/4/8/12/16; y served 10/8/6/2/0, printed also 4
        ("xnrusemi/XRS45J03F.pdf", 3, 4): ([10, 8, 6, 4, 2, 0], [0, 4, 8, 12, 16], 3.1298),
    }

    def test_printed_labels_are_served(self):
        from datasheet_chart_digitizer.gate_charge import digitize_gate_charge

        for (rel, page, diagram), (y_vals, x_vals, vpl) in self.CASES.items():
            with self.subTest(pdf=rel):
                results = digitize_gate_charge(ROOT / rel, dpi=220, finder_dpi=220)
                (r,) = [x for x in results if (x.panel.page, x.panel.diagram) == (page, diagram)]
                self.assertEqual(r.status, "ok")
                self.assertEqual(sorted(v for v, _ in r.y_ticks_px), sorted(y_vals))
                self.assertEqual(sorted(v for v, _ in r.x_ticks_px), sorted(x_vals))
                self.assertEqual(r.y_grid["grid_check"]["status"], "verified")
                self.assertEqual(r.qg_axis_status, "verified")
                self.assertAlmostEqual(r.vpl, vpl, delta=0.005)


if __name__ == "__main__":
    unittest.main()
