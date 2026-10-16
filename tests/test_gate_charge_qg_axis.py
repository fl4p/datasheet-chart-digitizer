"""Qg axis served on its rules, and the plot box on the served rules (corpus).

RJK1001DPP-A0#T2 p4: the VGS corner "0" (x 120.1..124.6 pt) shares the Qg
label row, so main bound the Qg origin and the box's left edge to that glyph
and stretched the label run onto the box: served ticks +16.6 ... -0.4 px off
the printed rules, status ok. FDP020N06B-F102 p4: the box spanned the
body-diode chart above and the Qg labels below. Skips when the local
fetlib corpus is missing.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np
import pymupdf

from datasheet_chart_digitizer.gate_charge import digitize_gate_charge
from datasheet_chart_digitizer.gate_charge_grid_anchor import check_gate_x_on_grid

DS = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets")
RJK1001 = DS / "renesas/RJK1001DPP-A0#T2.pdf"
FDP020 = DS / "onsemi/FDP020N06B-F102.pdf"
SCALE = 220 / 72.0


def _result(pdf, page, diagram):
    if not pdf.exists():
        raise unittest.SkipTest(f"missing local corpus fixture: {pdf}")
    found = [r for r in digitize_gate_charge(pdf) if (r.panel.page, r.panel.diagram) == (page, diagram)]
    assert len(found) == 1, found
    return found[0]


def _crop_gray(pdf, page, crop_pt):
    with pymupdf.open(pdf) as doc:
        pix = doc[page - 1].get_pixmap(
            matrix=pymupdf.Matrix(SCALE, SCALE), clip=pymupdf.Rect(crop_pt), alpha=False
        )
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return rgb.mean(axis=2).astype(np.uint8)


class RJK1001QgAxisTests(unittest.TestCase):
    # main c78991e's served result for this chart, verbatim
    MAIN_CROP_PT = (77.53355407714844, 261.55255126953125, 343.2362365722656, 531.8472900390625)
    MAIN_BOX = (137, 169, 587, 614)
    MAIN_X = ((0.0, 137.0), (40.0, 227.0), (80.0, 317.0), (120.0, 407.0), (160.0, 497.0), (200.0, 587.0))
    # label glyph centres (pt) on the Qg row; "0" is the VGS corner label
    LABEL_X_PT = {0.0: 122.35, 40.0: 155.45, 80.0: 183.85, 120.0: 212.3, 160.0: 240.2, 200.0: 268.6}

    def test_main_mapping_fails_the_check(self):
        if not RJK1001.exists():
            self.skipTest("missing RJK1001")
        gray = _crop_gray(RJK1001, 4, self.MAIN_CROP_PT)
        labels = [(v, (x - self.MAIN_CROP_PT[0]) * SCALE) for v, x in self.LABEL_X_PT.items()]
        check = check_gate_x_on_grid(gray, self.MAIN_X, labels, self.MAIN_BOX)
        self.assertEqual(check.status, "failed", check.reason)
        errors = {t["value"]: t["served_error_px"] for t in check.ticks}
        self.assertLess(errors[0.0], -15.0)  # origin 16 px left of its rule
        self.assertLess(abs(errors[200.0]), 1.5)

    def test_branch_serves_the_rules(self):
        r = _result(RJK1001, 4, 951)
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.qg_axis_status, "verified", r.x_grid)
        check = r.x_grid["grid_check"]
        self.assertLessEqual(check["max_abs_error_px"], 1.0)
        self.assertEqual(len(check["ticks"]), 6)
        # the origin is served on its rule, not on the VGS corner "0" glyph
        origin = dict(r.x_ticks_px)[0.0]
        self.assertGreater(origin - dict(r.x_label_ticks_px)[0.0], 10.0)
        # box on the outermost served rules on both axes
        x0, y0, x1, y1 = r.plot_box_px
        xs = sorted(p for _v, p in r.x_ticks_px)
        ys = sorted(p for _v, p in r.y_ticks_px)
        self.assertLessEqual(abs(x0 - xs[0]), 1.5)
        self.assertLessEqual(abs(x1 - xs[-1]), 1.5)
        self.assertLessEqual(abs(y0 - ys[0]), 1.5)
        self.assertLessEqual(abs(y1 - ys[-1]), 1.5)
        manifest = r.to_manifest()
        self.assertTrue(manifest["curve_physical_output_available"])
        self.assertGreater(len(manifest["curve_px"]), 20)
        self.assertAlmostEqual(r.vpl, 4.37, delta=0.1)



class FDP020PlotBoxTests(unittest.TestCase):
    def test_box_is_bound_to_the_vgs_rules(self):
        r = _result(FDP020, 4, 6)
        self.assertEqual(r.status, "ok")
        _x0, y0, _x1, y1 = r.plot_box_px
        ys = {v: p for v, p in r.y_ticks_px}
        self.assertLessEqual(abs(y0 - ys[10.0]), 1.5)  # not 179 px up in the body-diode chart
        self.assertLessEqual(abs(y1 - ys[0.0]), 1.5)   # not 19 px down on the Qg labels
        # The first pass used to bind a foreign column whose values rise
        # downward (0.4 @ 492.9 pt, 6 @ 537.5, 30 @ 701.5 pt) and the box top
        # sat 179 px up in the body-diode chart until _bind_plot_box_to_rules
        # moved it (input_box_px[1] == 307 in that crop). Since 2026-10-16
        # such a positive rising run is not a y axis, the native 10..0 column
        # at x 350.8 pt wins, and the box is on the rules before rebinding.
        self.assertEqual(r.plot_box_binding["y"], {"lo": "on_rule", "hi": "on_rule"})
        self.assertLessEqual(abs(r.plot_box_binding["input_box_px"][1] - ys[10.0]), 1.5)
        self.assertEqual(r.qg_axis_status, "verified")
        # the served trace stays inside the 0..10 V rules
        self.assertLessEqual(max(y for _x, y in r.curve_px), ys[0.0] + 1.5)


NTMFS4C020 = DS / "onsemi/NTMFS4C020NT1G.pdf"
NTMFS4854 = DS / "onsemi/NTMFS4854NST3G.pdf"


class DualYIdentityTests(unittest.TestCase):
    def _chart(self, pdf, page, diagram):
        if not pdf.exists():
            self.skipTest(f"missing {pdf.name}")
        found = [r for r in digitize_gate_charge(pdf, dpi=220, finder_dpi=220)
                 if (r.panel.page, r.panel.diagram) == (page, diagram)]
        self.assertEqual(len(found), 1)
        return found[0]

    def test_vds_column_read_as_vgs_is_never_served_as_vgs(self):
        # 0..12 V VGS on the left, 0..18/0..20 V VDS on the right, the VDS
        # column nearer: main read Vpl 4.39 V / 5.03 V off the VDS scale for
        # ~2.9 / ~3.0 V plateaus. The drain title rules that column out; the
        # gate-titled column on the other side is the VGS axis, and the chart
        # is served only once both axes are verified (rule order + table Qg).
        for pdf, page, plateau in ((NTMFS4C020, 4, 2.9), (NTMFS4854, 5, 3.0)):
            r = self._chart(pdf, page, 951)
            self.assertIn("y_axis_rebound_to_gate_titled_column", r.diagnostics)
            self.assertNotIn("y_axis_title_names_drain_not_gate", r.diagnostics)
            self.assertEqual(max(v for v, _ in r.y_ticks_px), 12.0)
            if r.status == "ok":
                self.assertAlmostEqual(r.vpl, plateau, delta=0.15)

    def test_gate_titled_column_is_not_flagged(self):
        r = _result(RJK1001, 4, 951)
        self.assertNotIn("y_axis_title_names_drain_not_gate", r.diagnostics)


if __name__ == "__main__":
    unittest.main()
