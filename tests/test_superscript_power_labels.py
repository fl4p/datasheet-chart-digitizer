"""Tick labels typeset as "10" + raised exponent, in every form the PyMuPDF
word stream delivers them: flattened ("102"), split on one line ("10" + "2"),
the exponent on its own line, and flattened at (nearly) the base glyph size.

On bfeb0b5 only the flattened form with a clearly smaller exponent was read as
10^n; the split forms parsed the exponents as small integers that fit a perfect
LINEAR axis (AGM15T06C p5 body diode: Y ticks 2, 1, 0, -1, IF = -0.996 A at
0.613 V where the print shows ~0.10 A), and the same-size form escaped both the
pairing and the mixed-size refusal (100x off on vishay SiA4620DJ p6).
"""

from __future__ import annotations

import math
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import fitz

from datasheet_chart_digitizer import axis_calibration as A
from datasheet_chart_digitizer import diode_forward_voltage as dfv
from datasheet_chart_digitizer import rdson_gate_voltage as rgv
from datasheet_chart_digitizer import served_axis_guard
from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.crop_transform import CropTransform
from datasheet_chart_digitizer.find_charts import PageText
from datasheet_chart_digitizer.gridline_anchor import GridCheck
from datasheet_chart_digitizer.rdson_gate_voltage_axes import _axis_or_robust, _text_labels

# exponent (glyph size, rise, leading text) next to an 8 pt "10"
FORMS = {"flat": (5.0, 3.0, ""), "split_line": (5.0, 3.0, " "), "own_line": (5.0, 6.0, ""),
         "same_size": (7.7, 2.0, "")}
SPLIT_FORMS = ("split_line", "own_line", "same_size")
DECADES = {"positive": (1, 4), "negative": (-2, 1), "far_tail": (-12, -9)}
FX0, FY0, FX1, FY1 = 150.0, 200.0, 450.0, 450.0
S = 4.0
TRANSFORM = CropTransform(0.0, 0.0, S, S)
HINT = PlotBox(int(FX0 * S), int(FY0 * S), int(FX1 * S), int(FY1 * S))


def _label(pg, x, y, d, form, anchor, base="10"):
    """Decade label 10^d (*form* None: plain number) right/centre-anchored at x."""
    if form is None:
        t = f"{d:g}"
        pg.insert_text((x - fitz.get_text_length(t, fontsize=8) * anchor, y), t, fontsize=8)
        return
    size, rise, lead = FORMS[form]
    e = lead + str(d)
    w0, w1 = fitz.get_text_length(base, fontsize=8), fitz.get_text_length(e, fontsize=size)
    left = x - (w0 + w1) * anchor
    pg.insert_text((left, y), base, fontsize=8)
    pg.insert_text((left + w0, y - rise), e, fontsize=size)


def _chart(yform, ydec=(1, 4), *, xform=None, xdec=(0, 3), xlin=None, y_unit=None, base="10"):
    """A framed chart, log Y decades *ydec*; X log decades *xdec* or linear 0..*xlin*."""
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    sh = pg.new_shape()
    sh.draw_rect(fitz.Rect(FX0, FY0, FX1, FY1))
    sh.finish(color=(0, 0, 0), width=0.6)
    sh.commit()
    if xlin is not None:
        for i in range(6):
            _label(pg, FX0 + i / 5 * (FX1 - FX0), FY1 + 11, xlin * i / 5, None, 0.5)
    else:
        for d in range(xdec[0], xdec[1] + 1):
            x = FX0 + (d - xdec[0]) / (xdec[1] - xdec[0]) * (FX1 - FX0)
            _label(pg, x, FY1 + 11, 10.0 ** d if xform is None else d, xform, 0.5, base)
    for d in range(ydec[0], ydec[1] + 1):
        y = FY1 - (d - ydec[0]) / (ydec[1] - ydec[0]) * (FY1 - FY0)
        _label(pg, FX0 - 3, y + 3, 10.0 ** d if yform is None else d, yform, 1.0, base)
    if y_unit:
        pg.insert_text((FX0 - 30, FY0 + 60), y_unit, fontsize=8)
    return doc, pg


def _raw_words(pg):
    return [w[4] for w in pg.get_text("words")]


class HelperForms(unittest.TestCase):
    def test_every_form_and_exponent_reads_as_one_power_word(self):
        for form in FORMS:
            for name, dec in DECADES.items():
                with self.subTest(form=form, decades=name):
                    _doc, pg = _chart(form, dec, xlin=10)
                    words, unread = A.power_tick_words(pg)
                    self.assertEqual(unread, [])
                    self.assertEqual(sorted(w[4] for w in words if "^" in w[4]),
                                     sorted(f"10^{d}" for d in range(dec[0], dec[1] + 1)))
                    # the exponent words are consumed: only the 10^n words stay in the Y gutter
                    self.assertEqual([w[4] for w in words if w[2] < FX0 and "^" not in w[4]], [])

    def test_the_forms_are_the_ones_the_word_stream_shows(self):
        expect = {"flat": ["102"], "split_line": ["10", "2"], "own_line": ["10", "2"], "same_size": ["102"]}
        for form, raw in expect.items():
            doc = fitz.open()
            pg = doc.new_page()
            _label(pg, 100, 100, 2, form, 0.0)
            self.assertEqual(_raw_words(pg), raw, form)
            if form in ("own_line", "same_size"):
                # the dict-span pairing bfeb0b5 relied on does not see these
                self.assertEqual(A._explicit_power_labels(pg), [])

    def test_plain_numbers_pass_unchanged(self):
        doc = fitz.open()
        pg = doc.new_page()
        w = fitz.get_text_length("1000", fontsize=8)
        pg.insert_text((100, 100), "1000", fontsize=8)
        pg.insert_text((100 + w + 0.6, 100), "10000", fontsize=8)    # Excel-glued "100010000"
        pg.insert_text((200, 100), "102", fontsize=8)
        pg.insert_text((250, 100), "10", fontsize=8)
        pg.insert_text((300, 100), "-10", fontsize=8)
        pg.insert_text((350, 100), "x10", fontsize=8)
        raw = pg.get_text("words")
        words, unread = A.power_tick_words(pg)
        self.assertEqual(words, [tuple(w) for w in raw])
        self.assertEqual(unread, [])

    def test_a_raised_exponent_on_no_lone_10_is_unread(self):
        # "20" + superscript: no 10^n, and must not read as 201 / 20 + 1
        _doc, pg = _chart("flat", (1, 4), xlin=10, base="20")
        words, unread = A.power_tick_words(pg)
        self.assertEqual(sorted(w[4] for w in unread), sorted(f"20{d}" for d in range(1, 5)))
        self.assertFalse(any(w[4].startswith("20") for w in words))

    def test_unpaired_flattened_power_is_unread(self):
        _doc, pg = _chart("same_size", (1, 4), xlin=10)
        with unittest.mock.patch.object(A, "_raised_power_pairs", return_value=[]):
            _words, unread = A.power_tick_words(pg)
        self.assertEqual(sorted(w[4] for w in unread), sorted(f"10{d}" for d in range(1, 5)))


class BodyDiodeAxis(unittest.TestCase):
    """diode_forward_voltage._page_labels feeds body diode, RDS(Id), RDS(Tj) and leakage."""

    def test_y_decades_read_log_in_every_form(self):
        for form in FORMS:
            for name, dec in DECADES.items():
                with self.subTest(form=form, decades=name):
                    _doc, pg = _chart(form, dec, xlin=1.2)
                    axis = dfv._select_axis(dfv._page_labels(pg, TRANSFORM), HINT, "y")
                    self.assertEqual(axis.model, "log10")
                    self.assertEqual(sorted(t.value for t in axis.ticks),
                                     [10.0 ** d for d in range(dec[0], dec[1] + 1)])

    def test_unread_superscript_refuses_the_axis(self):
        _doc, pg = _chart("flat", (1, 4), xlin=1.2, base="20")
        with self.assertRaisesRegex(RuntimeError, "superscript"):
            dfv._select_axis(dfv._page_labels(pg, TRANSFORM), HINT, "y")

    def test_plain_linear_labels_unchanged(self):
        doc = fitz.open()
        pg = doc.new_page(width=595, height=842)
        for i, v in enumerate((0, 51, 102, 153, 204)):
            _label(pg, FX0 - 3, FY1 - i / 4 * (FY1 - FY0) + 3, v, None, 1.0)
        axis = dfv._select_axis(dfv._page_labels(pg, TRANSFORM), HINT, "y")
        self.assertEqual(axis.model, "linear")
        self.assertEqual(sorted(t.value for t in axis.ticks), [0, 51, 102, 153, 204])


class RdsVgsAxis(unittest.TestCase):
    def labels(self, pg):
        words = rgv._panel_words(pg, PageText(1, 595.0, 842.0, [], "text_layer"))
        return _text_labels(words, TRANSFORM, (int(842 * S), int(595 * S)))

    def test_y_decades_read_log_in_every_form(self):
        for form in FORMS:
            for name, dec in DECADES.items():
                with self.subTest(form=form, decades=name):
                    _doc, pg = _chart(form, dec, xlin=10)
                    labels = [l for l in self.labels(pg)
                              if dfv._NUMERIC_RE.fullmatch(l.text) or l.text.startswith("10^")]
                    axis = _axis_or_robust(labels, HINT, "y", [])
                    self.assertEqual(axis.model, "log10")
                    self.assertEqual(sorted(t.value for t in axis.ticks),
                                     [10.0 ** d for d in range(dec[0], dec[1] + 1)])

    def test_unread_superscript_is_marked(self):
        _doc, pg = _chart("flat", (1, 4), xlin=10, base="20")
        marked = [l.text for l in self.labels(pg) if l.text.startswith(A.UNREAD_POWER)]
        self.assertEqual(len(marked), 4)


class CapacitanceAxes(unittest.TestCase):
    BANDS = dict(x_row_band=(FY1 + 2, FY1 + 14), y_label_x_band=(FX0 - 40, FX0 - 1),
                 plot_y_band=(FY0 - 8, FY1 + 8), x_col_band=(FX0 - 20, FX1 + 20))

    def test_log_x_in_every_form(self):
        for form in FORMS:
            for name, dec in DECADES.items():
                with self.subTest(form=form, decades=name):
                    _doc, pg = _chart(None, (1, 4), xform=form, xdec=dec)
                    cal = A.calibrate_axes(pg, **self.BANDS)
                    self.assertTrue(cal.x_log)
                    self.assertEqual([v for v, _ in cal.x_ticks], [10.0 ** d for d in range(dec[0], dec[1] + 1)])
                    self.assertLess(cal.x_resid, 1e-3)

    def test_log_y_in_every_form_with_owned_unit(self):
        for form in FORMS:
            with self.subTest(form=form):
                _doc, pg = _chart(form, (1, 4), xlin=100, y_unit="pF")
                cal = A.calibrate_axes(pg, **self.BANDS)
                self.assertTrue(cal.y_log)
                self.assertEqual(sorted(round(e, 9) for e, _ in cal.y_decades), [1.0, 2.0, 3.0, 4.0])

    def test_unread_superscript_refuses(self):
        _doc, pg = _chart(None, (1, 4), xform="flat", xdec=(0, 3), base="20")
        with self.assertRaisesRegex(RuntimeError, "superscript"):
            A.calibrate_axes(pg, **self.BANDS)


class TransferRefusesPowersItsPairingMisses(unittest.TestCase):
    def test_refusal_only_for_labels_the_span_pairing_did_not_read(self):
        from datasheet_chart_digitizer import transfer_characteristics as tc
        for form in FORMS:
            with self.subTest(form=form):
                _doc, pg = _chart(form, (-1, 2), xlin=10)
                known = tc._power_labels_px(pg, TRANSFORM)
                if form in ("flat", "split_line"):
                    tc._refuse_unpaired_powers(pg, TRANSFORM, HINT, known)
                else:
                    with self.assertRaisesRegex(RuntimeError, "superscript"):
                        tc._refuse_unpaired_powers(pg, TRANSFORM, HINT, known)


class UnevaluableGridCheckRefuses(unittest.TestCase):
    """served_axis_guard: a check that evaluated no tick refuses (was: unverified)."""

    def test_no_tick_checked_refuses(self):
        checks = {"x": GridCheck("verified", "ok", 1.0, ({"value": 0.0},)),
                  "y": GridCheck("unverified", "y: no gridlines", None, ())}
        with self.assertRaisesRegex(RuntimeError, "evaluated no tick"):
            served_axis_guard.refuse_unevaluable(checks, "body diode")

    def test_evaluated_checks_and_missing_checks_pass_through(self):
        served_axis_guard.refuse_unevaluable(
            {"x": GridCheck("verified", "ok", 1.0, ({"value": 0.0},)),
             "y": GridCheck("unverified", "non-finite served pixel", 1.0, ({"value": 1.0},))}, "x")
        served_axis_guard.refuse_unevaluable(None, "x")


def _corpus(relative: str) -> Path | None:
    root = os.environ.get("DSDIG_DATASHEET_ROOT")
    if not root:
        return None
    path = Path(root) / "datasheets" / relative
    return path if path.exists() else None


class RealDatasheets(unittest.TestCase):
    def test_agm15t06c_body_diode_y_is_log_decades(self):
        from datasheet_chart_digitizer.find_charts import process_pdf
        for part in ("AGM15T06C", "AGM15T06F"):
            pdf = _corpus(f"agmsemi/{part}.pdf")
            if pdf is None:
                self.skipTest("DSDIG_DATASHEET_ROOT / AGM corpus PDF missing")
            with self.subTest(part=part), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                # annotate's finder dpi: bfeb0b5 served this panel unverified there
                panel = next(p for p in process_pdf(pdf, out, 220) if p.kind == "body_diode" and p.page == 5)
                try:
                    result = dfv._digitize_panel(panel, out)
                except RuntimeError:
                    continue          # refusal is acceptable; a linear exponent axis is not
                self.assertEqual(result["y_axis"]["model"], "log10")
                self.assertEqual(sorted(t["value"] for t in result["y_axis"]["ticks"]), [0.1, 1.0, 10.0, 100.0])
                # the print: IF ~0.10 A at VSD 0.613 V, 25 C (bfeb0b5 served -0.996 A)
                curve = next(c for c in result["curves"] if c["temperature_c"] == 25.0)
                _vsd, current = min(curve["points"], key=lambda p: abs(p[0] - 0.613))
                self.assertLess(abs(math.log10(current / 0.10)), math.log10(1.3))

    def test_sia4620dj_same_size_exponents_read(self):
        pdf = _corpus("vishay/SiA4620DJ.pdf")
        if pdf is None:
            self.skipTest("DSDIG_DATASHEET_ROOT / SiA4620DJ missing")
        with fitz.open(pdf) as doc:
            words, unread = A.power_tick_words(doc[5])
        self.assertEqual(unread, [])
        powers = sorted(w[4] for w in words if "^" in w[4])
        self.assertEqual(powers, sorted([f"10^{d}" for d in (-4, -3, -2, -1)] * 2))
        # the plain decades between them stay plain numbers
        self.assertTrue({"10", "100", "1000"} <= {w[4] for w in words})


if __name__ == "__main__":
    unittest.main()
