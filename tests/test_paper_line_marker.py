"""digitize-paper-lines on Keil et al. 2016 (JES 163(9) A1872, open access), Fig. 2c.

Fig. 2c (page 4, third panel of the top row) is LFP relative capacity vs storage
SoC after 9 months: a MATLAB line+marker plot with box-off axes. Fab passed its
overlay and axis calibration on 2026-11-24; the values below are from that
approved output. The PDF is not committed: the test skips when it is absent.
"""

from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from datasheet_chart_digitizer import paper_line_marker as P

try:
    import fitz
except ImportError:  # pragma: no cover
    fitz = None

KEIL = Path("/Users/fab/dev/pv/ee/lit/bat/Keil 2016 Calendar Aging of Lithium-Ion Batteries.pdf")
KEIL_SHA256 = "7f8bc24276c15f131dd140b8e8663a26342a9fb7b3cf7c08f41a3c0d4d7417ef"

SOC_ALL = [0, 5, 10, 20, 30, 40, 45, 50, 55, 60, 65, 70, 80, 90, 95, 100]
# (series, SoC %) -> relative capacity, approved output 2026-11-24
APPROVED = {
    ("25°C 9 months", 0): 0.99896, ("25°C 9 months", 45): 0.97066, ("25°C 9 months", 80): 0.94087,
    ("25°C 9 months", 100): 0.94181,
    ("40°C 9 months", 0): 0.97779, ("40°C 9 months", 50): 0.93206, ("40°C 9 months", 90): 0.89459,
    ("50°C 9 months", 5): 0.92552, ("50°C 9 months", 50): 0.88231, ("50°C 9 months", 80): 0.86540,
}


@unittest.skipUnless(fitz is not None and KEIL.exists(), f"PyMuPDF or local {KEIL.name} unavailable")
class KeilFig2c(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        res, _, cls.cals = P.digitize_pdf(KEIL, 4)
        cls.charts = res["charts"]
        cls.chart = cls.charts[2]
        cls.curves = {c["label"]: c for c in cls.chart["curves"]}

    def test_source_pdf_is_the_approved_one(self):
        self.assertEqual(hashlib.sha256(KEIL.read_bytes()).hexdigest(), KEIL_SHA256)

    def test_panels_and_status(self):
        self.assertEqual(len(self.charts), 6)
        self.assertEqual([round(v, 1) for v in self.chart["frame_pt"]], [406.6, 78.8, 538.4, 179.4])
        for ch in self.charts:
            self.assertEqual(ch["status"], "unverified")      # the class never reports ok

    def test_series_bound_by_colour_and_marker(self):
        self.assertEqual(sorted(self.curves), ["25°C 9 months", "40°C 9 months", "50°C 9 months", "before storage"])
        markers = {k: c["marker"] for k, c in self.curves.items()}
        self.assertEqual(markers, {"before storage": "dot", "25°C 9 months": "square",
                                   "40°C 9 months": "diamond", "50°C 9 months": "circle"})
        for c in self.curves.values():
            self.assertEqual(c["status"], "ok")
            self.assertEqual(c["binding"], "bound")
            self.assertEqual(c["reasons"], [])
            self.assertEqual(c["vertices_without_marker"], [])
            self.assertEqual(c["vertices_withheld"], 0)

    def test_point_counts_and_soc_grid(self):
        for lab in ("before storage", "25°C 9 months", "40°C 9 months"):
            c = self.curves[lab]
            self.assertEqual(c["n_points"], 16, lab)
            self.assertEqual([round(x) for x in c["x"]], SOC_ALL, lab)
        c50 = self.curves["50°C 9 months"]
        self.assertEqual(c50["n_points"], 14)
        self.assertEqual([round(x) for x in c50["x"]], [s for s in SOC_ALL if s not in (0, 45)])
        for c in self.curves.values():
            for x in c["x"]:
                self.assertAlmostEqual(x, round(x), delta=1e-3)

    def test_values_match_approved_output(self):
        for (lab, soc), want in APPROVED.items():
            c = self.curves[lab]
            i = [round(x) for x in c["x"]].index(soc)
            self.assertAlmostEqual(c["y"][i], want, delta=1e-3, msg=f"{lab} @ {soc} %")
        self.assertTrue(all(abs(y - 1.0) < 1e-3 for y in self.curves["before storage"]["y"]))

    def test_axis_fit(self):
        ax = self.chart["axes"]
        self.assertEqual(ax["x"]["title"], "Storage SoC (%)")
        self.assertEqual(ax["y"]["title"], "Relative Capacity")
        self.assertEqual([lab["value"] for lab in ax["x"]["labels"]], [float(v) for v in range(0, 101, 10)])
        self.assertEqual([lab["value"] for lab in ax["y"]["labels"]], [0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0])
        for k in ("x", "y"):
            self.assertEqual(ax[k]["scale"], "linear")
            self.assertEqual(ax[k]["status"], "verified")
            self.assertLess(ax[k]["fit_resid_pt"], 0.01)
        xcal, ycal = self.cals[2]
        self.assertAlmostEqual(float(xcal.to_pt(0.0)), 406.626, delta=0.01)
        self.assertAlmostEqual(float(xcal.to_pt(100.0)), 538.389, delta=0.01)
        self.assertAlmostEqual(float(ycal.to_pt(0.7)), 179.41, delta=0.01)
        self.assertAlmostEqual(float(ycal.to_pt(1.0)), 89.34, delta=0.01)

    def test_gridlines_inside_own_plot_box(self):
        x0, y0, x1, y1 = self.chart["frame_pt"]
        gy, gx = self.chart["axes"]["y"]["gridlines_pt"], self.chart["axes"]["x"]["gridlines_pt"]
        self.assertEqual(len(gy), 7)                         # 0.70 .. 1.00; the panel below has six more
        self.assertTrue(all(y0 - 1 <= g <= y1 + 1 for g in gy), gy)
        self.assertTrue(all(x0 - 1 <= g <= x1 + 1 for g in gx), gx)


class Gridlines(unittest.TestCase):
    def test_neighbour_panel_rules_excluded(self):
        grey = (0.873, 0.873, 0.873)
        rule = lambda a, b: dict(colour=grey, a=a, b=b, width=0.5)
        lines = [rule((0, 50), (300, 50)),                  # spans both side-by-side panels, inside both
                 rule((0, 150), (300, 150)),                # same span, in the stacked panel below
                 rule((40, 0), (40, 100)),                  # left panel's vertical rule
                 rule((240, 0), (240, 100))]                # right panel's vertical rule
        gh, gv = P._gridlines(lines, (200, 0, 300, 100))
        self.assertEqual(gh, [50])
        self.assertEqual(gv, [240])


def _synthetic(path=None, marker_indices=range(5), bad_grid=False, errorbars=False, superscript=False):
    """One 300x200 pt box-off panel, x ticks 0..4 (or 10^0..10^4 typeset as
    10 + superscript), y ticks 0..4, one red square-marked series of 5 vertices
    and its legend entry (review 2026-11-24 probes)."""
    doc = fitz.open()
    pg = doc.new_page(width=500, height=400)
    pg.draw_line((100, 100), (100, 300), color=(0, 0, 0))
    pg.draw_line((100, 300), (400, 300), color=(0, 0, 0))
    for i in range(5):
        x, y = 100 + 75 * i, 300 - 50 * i
        pg.draw_line((x, 300), (x, 297), color=(0, 0, 0))
        pg.draw_line((100, y), (103, y), color=(0, 0, 0))
        if superscript:
            w0 = fitz.get_text_length("10", fontsize=8)
            left = x - (w0 + fitz.get_text_length(str(i), fontsize=5)) / 2
            pg.insert_text((left, 311), "10", fontsize=8)
            pg.insert_text((left + w0, 308), str(i), fontsize=5)
        else:
            pg.insert_text((x - fitz.get_text_length(str(i), fontsize=8) / 2, 311), str(i), fontsize=8)
        pg.insert_text((94 - fitz.get_text_length(str(i), fontsize=8), y + 2.3), str(i), fontsize=8)
    if bad_grid:
        pg.draw_line((100, 212), (400, 212), color=(.87, .87, .87))
    col = (.8, 0, 0)
    nodes = [(140, 250), (200, 220), (260, 240), (320, 180), (370, 200)]
    pg.draw_polyline(nodes, color=col, closePath=False)
    for i in marker_indices:
        x, y = nodes[i]
        pg.draw_rect(fitz.Rect(x - 2, y - 2, x + 2, y + 2), color=col)
    pg.draw_line((200, 120), (220, 120), color=col)
    pg.draw_rect(fitz.Rect(208, 118, 212, 122), color=col)
    pg.insert_text((226, 122.3), "Series A", fontsize=8)
    if errorbars:
        for x, y in nodes:
            pg.draw_polyline([(x - 3, y - 12), (x + 3, y - 12), (x, y - 12), (x, y + 12), (x - 3, y + 12),
                              (x + 3, y + 12)], color=col, closePath=False)
    if path is not None:
        doc.save(str(path))
    lines, polys, marks = P._collect(pg)
    ch, cals = P.digitize_panel(pg, (100, 100, 400, 300), lines, polys, marks, P._words(pg), P._rotated_lines(pg))
    return ch


@unittest.skipUnless(fitz is not None, "PyMuPDF unavailable")
class SyntheticReviewCases(unittest.TestCase):
    """Review 2026-11-24 findings on synthetic panels; each served wrong or
    unqualified values on 6386165."""

    def test_clean_panel_is_ok(self):
        c = _synthetic()["curves"]
        self.assertEqual([(k["label"], k["status"], k["n_points"]) for k in c], [("Series A", "ok", 5)])

    def test_superscript_decade_labels_read_as_powers_of_ten(self):
        ch = _synthetic(superscript=True)
        ax = ch["axes"]["x"]
        self.assertEqual((ax["scale"], ax["status"]), ("log10", "verified"))
        self.assertEqual([lab["value"] for lab in ax["labels"]], [1.0, 10.0, 100.0, 1000.0, 10000.0])
        self.assertAlmostEqual(ch["curves"][0]["x"][0], 10 ** (40 / 75), places=6)   # not 100.53

    def test_unpaired_superscript_label_refuses_axis(self):
        with mock.patch.object(P, "_explicit_power_labels", return_value=[], create=True):
            ch = _synthetic(superscript=True)
        self.assertEqual(ch["status"], "refused")
        self.assertEqual(ch["axes"]["x"]["status"], "refused")
        self.assertIn("mix glyph sizes", " ".join(ch["axes"]["x"]["reasons"]))
        self.assertNotIn("curves", ch)

    def test_unverified_axis_keeps_curve_below_ok(self):
        ch = _synthetic(bad_grid=True)
        self.assertEqual(ch["axes"]["y"]["status"], "unverified")
        (c,) = ch["curves"]
        self.assertEqual(c["status"], "unverified")
        self.assertIn("y axis unverified", c["reasons"])
        self.assertEqual(c["binding"], "bound")

    def test_unmarked_vertices_are_withheld(self):
        (c,) = _synthetic(marker_indices=(0, 2, 4))["curves"]
        self.assertEqual((c["status"], c["n_points"]), ("unverified", 3))
        self.assertEqual([round(x, 4) for x in c["x"]], [0.5333, 2.1333, 3.6])
        self.assertEqual((c["binding"], c["vertices_withheld"]), ("bound", 2))

    def test_unbound_error_bar_paths_carry_no_values(self):
        curves = _synthetic(errorbars=True)["curves"]
        unbound = [c for c in curves if c["status"] == "unbound"]
        self.assertEqual(len(unbound), 5)
        for c in unbound:
            self.assertEqual((c["status"], c["n_points"], c["x"], c["y"]), ("unbound", 0, [], []))

    def test_csv_rows_carry_status_and_skip_unbound_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "bad_grid_errorbars.pdf"
            _synthetic(pdf, bad_grid=True, errorbars=True)
            self.assertEqual(P.main([str(pdf), "--page", "1", "--out", tmp]), 0)
            with (Path(tmp) / "bad_grid_errorbars_p1_panel0.csv").open() as fh:
                rows = list(csv.reader(fh))
        self.assertEqual(rows[0], ["series", "marker", "x", "y", "curve_status", "chart_status"])
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r[0] == "Series A" and r[4:] == ["unverified", "unverified"] for r in rows[1:]))


if __name__ == "__main__":
    unittest.main()
