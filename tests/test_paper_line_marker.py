"""digitize-paper-lines on Keil et al. 2016 (JES 163(9) A1872, open access), Fig. 2c.

Fig. 2c (page 4, third panel of the top row) is LFP relative capacity vs storage
SoC after 9 months: a MATLAB line+marker plot with box-off axes. Fab passed its
overlay and axis calibration on 2026-11-24; the values below are from that
approved output. The PDF is not committed: the test skips when it is absent.
"""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

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
            self.assertEqual(c["reasons"], [])
            self.assertEqual(c["vertices_without_marker"], [])

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


if __name__ == "__main__":
    unittest.main()
