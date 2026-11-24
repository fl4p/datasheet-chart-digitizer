"""digitize-mlcc on synthetic TY-style vector pages with exact truth.

Each page is drawn with PyMuPDF the way TY's Excel prints are: a stroked plot
frame, black major / grey minor full-span rules, tick labels centred on their
rules, a two-line title above the frame, a rotated y title, and one stroked
Bezier (or polyline) per series, clipped to the frame. Truth is the plotted
samples, so served values are compared exactly; the known-bad variants must
never come back ``ok``.
"""

from __future__ import annotations

import math
import unittest
from unittest import mock

import numpy as np

from datasheet_chart_digitizer import mlcc_vector as M

try:
    import fitz
except ImportError:  # pragma: no cover
    fitz = None

NAVY, MAGENTA, GREY = (0, 0, 0.5), (1, 0, 1), (0.74, 0.74, 0.74)


def _clipped_bezier_chain(doc, pg, frame, P, col, width):
    """One stroked Bezier chain through P, under a clip to `frame` -- the way
    Excel prints a series (the part outside the axis range is drawn but hidden
    by the clip). PyMuPDF's Shape has no clip, so the operators are appended to
    the page's content stream in PDF space (y up)."""
    H = pg.rect.height
    x0, y0, x1, y1 = frame

    def q(p):
        return f"{p.x:.4f} {H - p.y:.4f}"
    ops = [f"q {x0:.4f} {H - y1:.4f} {x1 - x0:.4f} {y1 - y0:.4f} re W n",
           f"{col[0]} {col[1]} {col[2]} RG {width} w", f"{q(P[0])} m"]
    for a, b in zip(P[:-1], P[1:]):
        c1, c2 = a + (b - a) * (1 / 3), a + (b - a) * (2 / 3)
        ops.append(f"{q(c1)} {q(c2)} {q(b)} c")
    ops.append("S Q")
    pg.clean_contents()
    xref = pg.get_contents()[0]
    doc.update_stream(xref, pg.read_contents() + ("\n" + "\n".join(ops) + "\n").encode())


def _page(*, title="Impedance,ESR Freq.", label_shift_pt=0.0, minor_shift_pt=0.0, y_unit="OHM",
          legend=("ESR", "Z"), series=None, log_y=True, xdec=(-3, 4), ydec=(-4, 2), superscript=""):
    """*superscript*: the axes ("x", "y", "xy") whose decade tick labels are
    typeset as "10" + a smaller raised exponent span (word extraction flattens
    10^2 to "102")."""
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    x0, y0, x1, y1 = 330.0, 355.0, 480.0, 475.0

    def X(f_mhz):
        return x0 + (math.log10(f_mhz) - xdec[0]) / (xdec[1] - xdec[0]) * (x1 - x0)

    def Y(v):
        return y1 - (math.log10(v) - ydec[0]) / (ydec[1] - ydec[0]) * (y1 - y0)

    shp = pg.new_shape()
    shp.draw_rect(fitz.Rect(x0, y0, x1, y1))
    shp.finish(color=(0, 0, 0), width=0.63)
    for d in range(xdec[0], xdec[1]):
        for k in range(2, 10):
            x = X(k * 10.0 ** d) + minor_shift_pt
            shp.draw_line((x, y0), (x, y1))
    for d in range(ydec[0], ydec[1]):
        for k in range(2, 10):
            y = Y(k * 10.0 ** d)
            shp.draw_line((x0, y), (x1, y))
    shp.finish(color=GREY, width=0.63)
    for d in range(xdec[0] + 1, xdec[1]):
        shp.draw_line((X(10.0 ** d), y0), (X(10.0 ** d), y1))
    for d in range(ydec[0] + 1, ydec[1]):
        shp.draw_line((x0, Y(10.0 ** d)), (x1, Y(10.0 ** d)))
    shp.finish(color=(0, 0, 0), width=0.63)
    shp.commit()

    def label(d, x, y, anchor, sup):
        if not sup:
            t = f"{10.0 ** d:g}"
            w = fitz.get_text_length(t, fontsize=8)
            pg.insert_text((x - w * anchor, y), t, fontsize=8)
            return
        w0, w1 = fitz.get_text_length("10", fontsize=8), fitz.get_text_length(str(d), fontsize=5)
        left = x - (w0 + w1) * anchor
        pg.insert_text((left, y), "10", fontsize=8)
        pg.insert_text((left + w0, y - 3), str(d), fontsize=5)
    for d in range(xdec[0], xdec[1] + 1):
        label(d, X(10.0 ** d) + label_shift_pt, y1 + 11, 0.5, "x" in superscript)
    for d in range(ydec[0], ydec[1] + 1):
        label(d, x0 - 3, Y(10.0 ** d) + 3, 1.0, "y" in superscript)
    pg.insert_text((385, y1 + 24), "Frequency[MHz]", fontsize=8)
    pg.insert_text((x0 - 28, 440), f"Z,ESR[{y_unit}]", fontsize=8, rotate=90)
    pg.insert_text((375, y0 - 22), "SYNTH000", fontsize=8)
    pg.insert_text((375, y0 - 11), title, fontsize=8)
    truth = {}
    series = series or {
        "Z": (MAGENTA, lambda f: 1 / (2 * math.pi * f * 1e6 * 22e-6) + 2 * math.pi * f * 1e6 * 0.4e-9),
        "ESR": (NAVY, lambda f: 0.002 + 0.1 / (f * 1e3) ** 0.5),
    }
    for lab, (col, fn) in series.items():
        f = np.logspace(xdec[0] - 1, xdec[1] - 0.5, 60)    # starts left of the frame: the clip hides it
        P = [fitz.Point(X(v), Y(fn(v))) for v in f]
        _clipped_bezier_chain(doc, pg, (x0, y0, x1, y1), P, col, 0.63)
        inside = (f >= 10.0 ** xdec[0]) & (f <= 10.0 ** xdec[1]) & np.array([10 ** ydec[0] <= fn(v) <= 10 ** ydec[1] for v in f])
        truth[lab] = (f[inside] * 1e6, np.array([fn(v) for v in f[inside]]))
    if legend:
        for i, lab in enumerate(legend):
            col = series[lab][0]
            yy = y0 + 10 + 13 * i
            pg.draw_line((420, yy), (437, yy), color=col, width=0.63)
            pg.insert_text((440, yy + 3), lab, fontsize=8)
    return doc, pg, truth


@unittest.skipIf(fitz is None, "PyMuPDF missing")
class MlccVectorSynthetic(unittest.TestCase):
    def _run(self, **kw):
        doc, pg, truth = _page(**kw)
        rules, shapes, sf = M._rules_and_shapes(pg)
        words, rot = M._words(pg), M._rotated_lines(pg)
        charts = []
        for panel in M.find_panels(rules):
            res, cal = M.digitize_chart(pg, panel, words, rot, shapes, sf)
            if res.get("chart_class"):
                charts.append(res)
        self.assertEqual(len(charts), 1, [c.get("reasons") for c in charts])
        return charts[0], truth

    def test_clean_page_serves_the_plotted_samples(self):
        ch, truth = self._run()
        self.assertEqual(ch["status"], "ok", ch["reasons"])
        self.assertEqual(ch["axes"]["x"]["unit_si"], "Hz")
        self.assertEqual(ch["axes"]["y"]["unit_si"], "ohm")
        got = {c["label"]: c for c in ch["curves"]}
        self.assertEqual(set(got), {"Z", "ESR"})
        for lab, (fx, fy) in truth.items():
            c = got[lab]
            gx, gy = np.array(c["x_si"]), np.array(c["y_si"])
            # every served sample is a plotted sample (log-space error < 1e-3 decade)
            for x, y in zip(gx, gy):
                k = int(np.argmin(np.abs(np.log10(fx) - math.log10(x))))
                self.assertLess(abs(math.log10(fx[k]) - math.log10(x)), 1e-3)
                self.assertLess(abs(math.log10(fy[k]) - math.log10(y)), 1e-3)
            # nothing outside the printed axis range is served
            self.assertGreaterEqual(gx.min(), 1e3 * 0.999)

    def test_label_off_its_rule_is_not_ok(self):
        # monotonicity: the further the labels move off their rules, never back to ok
        for shift in (3.0, 6.0, 12.0, 20.0):
            ch, _ = self._run(label_shift_pt=shift)
            self.assertNotEqual(ch["status"], "ok", shift)

    def test_minor_rules_off_lattice_is_not_ok(self):
        ch, _ = self._run(minor_shift_pt=1.6)
        self.assertNotEqual(ch["status"], "ok")
        self.assertTrue(any("lattice" in r for r in ch["reasons"]), ch["reasons"])

    def test_unknown_unit_is_refused(self):
        ch, _ = self._run(y_unit="furlongs")
        self.assertEqual(ch["status"], "refused")

    def test_multi_series_without_legend_is_refused(self):
        ch, _ = self._run(legend=())
        self.assertEqual(ch["status"], "refused")
        self.assertTrue(any("legend" in r for r in ch["reasons"]))

    def test_legend_entry_without_curve_is_refused(self):
        series = {"Z": (MAGENTA, lambda f: 1 / (2 * math.pi * f * 1e6 * 22e-6))}
        doc, pg, _ = _page(series=series, legend=None)
        pg.draw_line((420, 365), (437, 365), color=NAVY, width=0.63)
        pg.insert_text((440, 368), "ESR", fontsize=8)
        pg.draw_line((420, 378), (437, 378), color=MAGENTA, width=0.63)
        pg.insert_text((440, 381), "Z", fontsize=8)
        rules, shapes, sf = M._rules_and_shapes(pg)
        res, _ = M.digitize_chart(pg, M.find_panels(rules)[0], M._words(pg), M._rotated_lines(pg), shapes, sf)
        self.assertEqual(res["status"], "refused")

    def test_unsupported_title_is_refused(self):
        ch_doc, pg, _ = _page(title="Insulation Resistance")
        rules, shapes, sf = M._rules_and_shapes(pg)
        res, _ = M.digitize_chart(pg, M.find_panels(rules)[0], M._words(pg), M._rotated_lines(pg), shapes, sf)
        self.assertEqual(res["status"], "refused")
        self.assertIsNone(res["chart_class"])


class OutlineCentreline(unittest.TestCase):
    """PostScript prints: a stroke arrives as a filled outline with round caps."""

    def _outline(self, P, w=0.6):
        P = np.asarray(P, float)
        d = np.gradient(P, axis=0)
        n = np.c_[-d[:, 1], d[:, 0]] / np.hypot(*d.T)[:, None]
        A, B = P + n * w / 2, P - n * w / 2

        class Pt:
            def __init__(self, x, y):
                self.x, self.y = float(x), float(y)

        items = [("l", Pt(*a), Pt(*b)) for a, b in zip(A[:-1], A[1:])]
        e = A[-1]
        items += [("c", Pt(*e), Pt(*(e + d[-1] / np.hypot(*d[-1]) * w / 2)), Pt(*(B[-1] + d[-1] / np.hypot(*d[-1]) * w / 2)), Pt(*B[-1]))]
        Br = B[::-1]
        items += [("l", Pt(*a), Pt(*b)) for a, b in zip(Br[:-1], Br[1:])]
        s = Br[-1]
        items += [("c", Pt(*s), Pt(*(s - d[0] / np.hypot(*d[0]) * w / 2)), Pt(*(A[0] - d[0] / np.hypot(*d[0]) * w / 2)), Pt(*A[0]))]
        return items

    def test_centre_line_recovers_the_path(self):
        x = np.linspace(100, 250, 300)
        y = 400 + 30 * np.sin((x - 100) / 25)
        shp = M._outline_shape(self._outline(np.c_[x, y]), (0, 0, 0.5), type("R", (), dict(x0=100, y0=370, x1=250, y1=430))())
        self.assertIsNotNone(shp)
        for p in shp.pts[5:-5]:
            q, d = M._nearest_on_polyline(p, np.c_[x, y])
            self.assertLess(d, 0.02)

    def test_sharp_v_does_not_add_a_false_cap(self):
        x = np.r_[np.linspace(100, 175, 150), np.linspace(175.5, 250, 150)]
        y = 400 + np.abs(x - 175) * 0.8
        shp = M._outline_shape(self._outline(np.c_[x, y]), (1, 0, 1), type("R", (), dict(x0=100, y0=390, x1=250, y1=470))())
        self.assertIsNotNone(shp)
        self.assertGreater(np.ptp(shp.pts[:, 0]), 140)




class GluedLabels(unittest.TestCase):
    @staticmethod
    def _chars(text, x0, adv, jump_at=None, jump=0.0):
        out, x = [], x0
        for i, c in enumerate(text):
            if i == jump_at:
                x += jump
            out.append({"c": c, "bbox": (x, 0.0, x + 4.17, 9.0)})
            x += adv
        return out

    def test_two_touching_powers_of_ten_split(self):
        for jump in (0.76, -0.34):           # both seen on TMR325B7106KM-T
            parts = M._split_glued_number(self._chars("100010000", 454.68, 4.48, 4, jump))
            self.assertEqual(["".join(c["c"] for c in p) for p in parts], ["1000", "10000"])

    def test_regular_labels_are_not_split(self):
        for t in ("10000", "0.001", "100"):
            self.assertEqual(len(M._split_glued_number(self._chars(t, 0.0, 4.48))), 1)
        # an even advance gives no boundary even for a 10..10.. word
        self.assertEqual(len(M._split_glued_number(self._chars("1010", 0.0, 4.48))), 1)


@unittest.skipIf(fitz is None, "PyMuPDF missing")
class SuperscriptDecadeLabels(unittest.TestCase):
    """Tick labels typeset as "10" + superscript exponent: word extraction
    flattens 10^2 to "102" and drops 10^-3 ("10-3"). On e2d8ddc 10^0..10^7
    calibrated linear as 100..107 and served x ~1e8 Hz for 1.04 MHz."""

    _run = MlccVectorSynthetic._run

    def _assert_truth(self, ch, truth, xdec, ydec):
        self.assertEqual(ch["status"], "ok", ch["reasons"])
        for k, dec in (("x", xdec), ("y", ydec)):
            ax = ch["axes"][k]
            self.assertEqual((ax["scale"], ax["status"]), ("log10", "verified"))
            self.assertEqual([lab["value"] for lab in ax["labels"]], [10.0 ** d for d in range(dec[0], dec[1] + 1)])
        got = {c["label"]: c for c in ch["curves"]}
        for lab, (fx, fy) in truth.items():
            for x, y in zip(got[lab]["x_si"], got[lab]["y_si"]):
                k = int(np.argmin(np.abs(np.log10(fx) - math.log10(x))))
                self.assertLess(abs(math.log10(fx[k]) - math.log10(x)), 1e-3)
                self.assertLess(abs(math.log10(fy[k]) - math.log10(y)), 1e-3)

    def test_positive_exponents_read_as_powers_of_ten(self):
        ch, truth = self._run(superscript="x", xdec=(0, 7))
        self._assert_truth(ch, truth, (0, 7), (-4, 2))

    def test_negative_exponents_far_tail_both_axes(self):
        for xdec in ((-1, 4), (-3, 4)):
            ch, truth = self._run(superscript="xy", xdec=xdec)
            self._assert_truth(ch, truth, xdec, (-4, 2))

    def test_unpaired_superscript_label_refuses_axis(self):
        with mock.patch.object(M, "_explicit_power_labels", return_value=[], create=True):
            ch, _ = self._run(superscript="x", xdec=(0, 7))
        self.assertEqual(ch["status"], "refused")
        self.assertEqual(ch["axes"]["x"]["status"], "refused")
        self.assertIn("mix glyph sizes", " ".join(ch["axes"]["x"]["reasons"]))
        self.assertNotIn("curves", ch)

    def test_plain_number_labels_unchanged(self):
        ch, _ = self._run()
        self.assertEqual([lab["value"] for lab in ch["axes"]["x"]["labels"]], [10.0 ** d for d in range(-3, 5)])
        self.assertEqual([lab["value"] for lab in ch["axes"]["y"]["labels"]], [10.0 ** d for d in range(-4, 3)])

    def test_single_size_numbers_pass_power_words_unchanged(self):
        doc = fitz.open()
        pg = doc.new_page()
        w = fitz.get_text_length("1000", fontsize=8)
        pg.insert_text((100, 100), "1000", fontsize=8)
        pg.insert_text((100 + w + 0.6, 100), "10000", fontsize=8)    # Excel-glued: one word "100010000"
        pg.insert_text((200, 100), "102", fontsize=8)
        pg.insert_text((250, 100), "1000", fontsize=8)
        words, mixed = M._power_words(pg, M._words(pg))
        self.assertEqual([w.text for w in words], ["1000", "10000", "102", "1000"])
        self.assertEqual(mixed, [])


# --------------------------------------------------------------------------- #
# frame printed only as a filled ring; ink past the detected frame
# --------------------------------------------------------------------------- #

def _ring_frame_dc_bias_page(*, ring=True):
    """A PScript-style DC-bias chart whose axis ends at 6.3 V: gridlines at
    0..6 V, and the 6.3 V right edge drawn ONLY as the frame ring (a filled
    path of outer + inner rectangle lines), like JMK325BJ476MM-T. The curve is
    one stroked polyline from 0 to 6.3 V. Returns (page, truth x, truth y)."""
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    x0, y0, y1, pitch = 100.0, 350.0, 470.0, 24.0
    x1 = x0 + 6.3 * pitch

    def X(v):
        return x0 + v * pitch

    def Y(pct):                     # -100 .. +20 %
        return y1 - (pct + 100.0) / 120.0 * (y1 - y0)

    shp = pg.new_shape()
    for v in range(0, 7):
        shp.draw_line((X(v), y0), (X(v), y1))
    for pct in range(-100, 21, 20):
        shp.draw_line((x0, Y(pct)), (x1, Y(pct)))
    shp.finish(color=(0, 0, 0), width=0.6)
    shp.commit()
    if ring:
        t = 0.6
        shp = pg.new_shape()
        for (a, b, c, d) in ((x0 - t / 2, y0 - t / 2, x1 + t / 2, y1 + t / 2),
                             (x0 + t / 2, y0 + t / 2, x1 - t / 2, y1 - t / 2)):
            shp.draw_polyline([(a, b), (c, b), (c, d), (a, d), (a, b)])
        shp.finish(fill=(0, 0, 0), color=None, even_odd=True, closePath=True)
        shp.commit()
    for v in range(0, 7):
        w = fitz.get_text_length(str(v), fontsize=8)
        pg.insert_text((X(v) - w / 2, y1 + 11), str(v), fontsize=8)
    for pct in range(-100, 21, 20):
        w = fitz.get_text_length(str(pct), fontsize=8)
        pg.insert_text((x0 - 3 - w, Y(pct) + 3), str(pct), fontsize=8)
    pg.insert_text((150, y1 + 24), "DC Voltage[V]", fontsize=8)
    pg.insert_text((x0 - 30, 450), "Capacitance Change[%]", fontsize=8, rotate=90)
    pg.insert_text((140, y0 - 22), "SYNTH000", fontsize=8)
    pg.insert_text((140, y0 - 11), "DC Bias Char.", fontsize=8)
    vx = np.linspace(0.0, 6.3, 64)
    vy = -60.0 * (1 - np.exp(-vx / 2.5))
    shp = pg.new_shape()
    shp.draw_polyline([fitz.Point(X(a), Y(b)) for a, b in zip(vx, vy)])
    shp.finish(color=NAVY, width=0.63, closePath=False)
    shp.commit()
    return doc, pg, vx, vy


def _dc_bias_chart(pg):
    rules, shapes, sf = M._rules_and_shapes(pg)
    words, rot = M._words(pg), M._rotated_lines(pg)
    out = []
    for panel in M.find_panels(rules):
        res, _ = M.digitize_chart(pg, panel, words, rot, shapes, sf)
        if res.get("chart_class") == "dc_bias":
            out.append(res)
    assert len(out) == 1, [o.get("reasons") for o in out]
    return out[0]


@unittest.skipIf(fitz is None, "PyMuPDF missing")
class RingFrame(unittest.TestCase):
    def test_ring_side_is_the_frame_edge_and_the_tail_is_served(self):
        doc, pg, vx, vy = _ring_frame_dc_bias_page()
        ch = _dc_bias_chart(pg)
        self.assertEqual(ch["status"], "ok", ch["reasons"])
        self.assertAlmostEqual(ch["axes"]["x"]["frame_range"][1], 6.3, delta=0.01)
        c = ch["curves"][0]
        self.assertAlmostEqual(max(c["x_si"]), 6.3, delta=0.01)
        self.assertLessEqual(c["ink_past_frame_pt"]["worst"], M.INK_PAST_FRAME_PT)

    def test_without_the_ring_the_truncated_chart_is_not_ok(self):
        # known-bad: the ring is the only 6.3 V edge, so without it the 6 V
        # gridline becomes the right edge; the 6-6.3 V ink must make it not ok
        doc, pg, vx, vy = _ring_frame_dc_bias_page(ring=False)
        ch = _dc_bias_chart(pg)
        self.assertNotEqual(ch["status"], "ok")
        self.assertTrue(any("past the detected frame" in r for r in ch["reasons"]), ch["reasons"])


# --------------------------------------------------------------------------- #
# the real PDF: JMK325BJ476MM-T, from the dcdc-tools vendorpull store
# --------------------------------------------------------------------------- #

import os
import subprocess
import tempfile
from pathlib import Path

#: dcdc-tools checkout holding vendorpull/store (default: the sibling checkout)
VENDORPULL_ROOT = Path(os.environ.get("DSDIG_VENDORPULL_ROOT")
                       or Path(__file__).resolve().parents[2] / "dcdc-tools")
#: an interpreter with `zstandard` (dsdig's venv has none)
VENDORPULL_PYTHON = os.environ.get("DSDIG_VENDORPULL_PYTHON") or "python3"

_EXTRACT = (
    "import sys, pathlib; sys.path.insert(0, sys.argv[1]); "
    "from vendorpull import densestore as ds; "
    "root = pathlib.Path(sys.argv[1]) / 'vendorpull' / 'store'; "
    "f = ds.read_part(root, 'ty', sys.argv[2], ds.load_dict(root, 'ty')); "
    "pathlib.Path(sys.argv[3]).write_bytes(f[sys.argv[2] + '_characteristics.pdf'])")


def _stored_ty_pdf(mpn, out_dir):
    """The characteristics_pdf artifact of a stored TY part, or None (skip)."""
    if not (VENDORPULL_ROOT / "vendorpull" / "store" / "ty" / f"{mpn}.tar.zst").exists():
        return None
    out = Path(out_dir) / f"{mpn}_characteristics.pdf"
    r = subprocess.run([VENDORPULL_PYTHON, "-c", _EXTRACT, str(VENDORPULL_ROOT), mpn, str(out)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"extracting {mpn} from the vendorpull store failed: {r.stderr[-400:]}")
    if not out.read_bytes().startswith(b"%PDF"):
        raise RuntimeError(f"{mpn}: stored artifact is not a PDF")
    return out


@unittest.skipIf(fitz is None, "PyMuPDF missing")
class StoredJmk325bj476(unittest.TestCase):
    """dsdig took JMK325BJ476MM-T's 6 V gridline for the DC-bias plot's right
    edge (the 6.3 V edge exists only as the PScript frame ring), served a curve
    ending at 6.02 V and still reported ok (review 2026-10-17, F2)."""

    MPN = "JMK325BJ476MM-T"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pdf = _stored_ty_pdf(self.MPN, self._tmp.name)
        if self.pdf is None:
            self.skipTest(f"{self.MPN} not in {VENDORPULL_ROOT}/vendorpull/store "
                          f"(set DSDIG_VENDORPULL_ROOT)")

    def tearDown(self):
        self._tmp.cleanup()

    def _dc_bias(self):
        res, _, _ = M.digitize_pdf(self.pdf)
        dcb = [c for c in res["charts"] if c.get("chart_class") == "dc_bias"]
        self.assertEqual(len(dcb), 1)
        return res, dcb[0]

    def test_dc_bias_tail_to_6v3_is_served(self):
        res, ch = self._dc_bias()
        self.assertEqual(ch["status"], "ok", ch["reasons"])
        lo, hi = ch["axes"]["x"]["frame_range"]
        self.assertAlmostEqual(hi, 6.3, delta=0.35e-2 * 6.3)   # calibration band
        x = ch["curves"][0]["x_si"]
        self.assertGreater(max(x), 6.25)
        # the other four charts are unchanged in kind
        self.assertEqual(sorted(c["chart_class"] for c in res["charts"] if c["status"] == "ok"),
                         ["c_vs_f", "dc_bias", "impedance_esr_vs_f", "ripple_temperature_rise",
                          "temperature"])

    def test_without_ring_sides_the_chart_is_not_ok(self):
        # known-bad calibration: the pre-fix frame detection must not come back ok
        orig = M._ring_sides
        M._ring_sides = lambda *a, **k: []
        try:
            _, ch = self._dc_bias()
        finally:
            M._ring_sides = orig
        self.assertNotEqual(ch["status"], "ok")
        self.assertTrue(any("past the detected frame" in r for r in ch["reasons"]), ch["reasons"])


if __name__ == "__main__":
    unittest.main()



class TestPdfLabelsAreSafe(unittest.TestCase):
    """Legend labels come from the vendor PDF: defuse spreadsheet formulas and
    terminal control sequences before they reach a CSV cell or stdout."""

    def test_cell(self):
        for raw, want in [("Z", "Z"), ("ESR", "ESR"),
                          ('=HYPERLINK("x")', '\'=HYPERLINK("x")'), ("+1", "'+1"),
                          ("-A", "'-A"), ("@SUM", "'@SUM"), ("Z\x1b[2J", "Z[2J")]:
            with self.subTest(raw=raw):
                self.assertEqual(M._cell(raw), want)
