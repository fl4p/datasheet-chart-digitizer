"""Text a reader cannot see is not read as a label, title, unit or spec value.

Render mode 3, zero alpha and white-on-white text sit in real datasheet text
layers (Vishay Excel leftovers "Axis Title" / "10000" beside the plot, HXY
table cells with stale values, TI "TEXT ADDED FOR SPACING"). Each reader
below served such text at 61deaca. Kept on purpose: the OCR layer of a scan
(invisible text over an image), white text on a dark fill, text used as a
clip path.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pymupdf

from datasheet_chart_digitizer.axis_calibration import power_tick_words
from datasheet_chart_digitizer.axis_title_identity import frame_owned_axis_titles
from datasheet_chart_digitizer.breakdown_voltage import _max_vds_rating
from datasheet_chart_digitizer.find_charts import run_text_bbox
from datasheet_chart_digitizer.gate_charge_estimation import _local_y_ticks_for_plot
from datasheet_chart_digitizer.threshold_voltage import spec_vth_window

DATASHEETS = Path(os.environ.get("DSDIG_DATASHEET_ROOT", "/Users/fab/dev/pv/pwr-mosfet-lib")) / "datasheets"
HIDDEN = (
    ("render_mode_3", {"render_mode": 3}),
    ("alpha_0", {"fill_opacity": 0}),
    ("white", {"color": (1, 1, 1)}),
    ("near_white", {"color": (0.99, 0.99, 0.99)}),
)


def _vt():
    # imported per test: the per-class tests below must run (and fail) without it
    from datasheet_chart_digitizer import visible_text

    return visible_text


def _texts(page) -> list[str]:
    return [w[4] for w in _vt().words(page)]


class ReaderTests(unittest.TestCase):
    def _page(self):
        doc = pymupdf.open()
        return doc, doc.new_page(width=300, height=300)

    def test_each_hidden_kind_is_dropped_from_every_reader(self):
        for name, style in HIDDEN:
            with self.subTest(name):
                doc, page = self._page()
                page.insert_text((20, 30), "shown 100", fontsize=10)
                page.insert_text((20, 60), "decoy 1000", fontsize=10, **style)
                self.assertEqual(_texts(page), ["shown", "100"])
                self.assertEqual(_vt().text(page), "shown 100\n")
                spans = [s["text"] for b in _vt().payload(page, "dict")["blocks"]
                         for ln in b.get("lines", []) for s in ln["spans"]]
                self.assertEqual(spans, ["shown 100"])
                # the raw layer still holds it: the reader, not the fixture, drops it
                self.assertIn("1000", [w[4] for w in page.get_text("words")])

    def test_white_text_on_a_dark_fill_is_kept(self):
        doc, page = self._page()
        page.draw_rect(pymupdf.Rect(10, 40, 200, 70), color=None, fill=(0.1, 0.1, 0.4))
        page.insert_text((20, 60), "banner 25", fontsize=10, color=(1, 1, 1))
        self.assertEqual(_texts(page), ["banner", "25"])

    def test_white_text_verdict_is_monotone_in_the_fill_darkness(self):
        verdicts = []
        for level in (1.0, 0.95, 0.9, 0.8, 0.6, 0.4, 0.2, 0.0):
            doc, page = self._page()
            page.draw_rect(pymupdf.Rect(10, 40, 200, 70), color=None, fill=(level, level, level))
            page.insert_text((20, 60), "label", fontsize=10, color=(1, 1, 1))
            verdicts.append(bool(_texts(page)))
        # hidden on white and near-white, visible from some darkness on, never back
        self.assertFalse(verdicts[0])
        self.assertTrue(verdicts[-1])
        self.assertEqual(verdicts, sorted(verdicts))

    def test_invisible_ocr_layer_over_an_image_is_kept(self):
        for name, style in HIDDEN[:2]:
            with self.subTest(name):
                doc, page = self._page()
                pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 30))
                pix.clear_with(230)
                for col in range(3, 26, 2):  # the scanned label's ink under the OCR text
                    pix.set_rect(pymupdf.IRect(col, 12, col + 1, 16), (20, 20, 20))
                page.insert_image(pymupdf.Rect(10, 40, 250, 120), pixmap=pix, keep_proportion=False)
                page.insert_text((20, 80), "Drain Current (A)", fontsize=10, **style)
                page.insert_text((20, 200), "off image", fontsize=10, **style)
                self.assertEqual(_texts(page), ["Drain", "Current", "(A)"])

    def test_unpainted_text_over_a_blank_image_is_dropped(self):
        for name, style in HIDDEN[:2]:
            with self.subTest(name):
                doc, page = self._page()
                pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4))
                pix.clear_with(255)
                page.insert_image(page.rect, pixmap=pix, keep_proportion=False)
                page.insert_text((20, 30), "shown", fontsize=10)
                page.insert_text((20, 80), "Axis Title 10000", fontsize=10, **style)
                self.assertEqual(_texts(page), ["shown"])

    def test_gate_charge_vdd_labels_skip_hidden_text(self):
        from datasheet_chart_digitizer.gate_charge_vdd_labels import _words

        doc, page = self._page()
        page.insert_text((20, 30), "VDS=40V", fontsize=10)
        page.insert_text((20, 80), "VDS=80V", fontsize=10, render_mode=3)
        texts = "".join(w[4] for w in _words(page))
        self.assertIn("40V", texts)
        self.assertNotIn("80V", texts)

    def test_text_used_as_a_clip_path_is_kept(self):
        doc, page = self._page()
        page.insert_text((20, 60), "°C", fontsize=10, render_mode=7)
        self.assertEqual(_texts(page), ["°C"])

    def test_decoy_over_a_visible_label_drops_only_the_decoy(self):
        doc, page = self._page()
        page.insert_text((20, 60), "100", fontsize=10)
        page.insert_text((20, 60), "1000", fontsize=10, render_mode=3)
        self.assertEqual(_texts(page), ["100"])

    def test_word_completed_by_a_hidden_character_is_dropped(self):
        # "10" + hidden "0" reads "100" in the text layer; a reader sees "10"
        doc, page = self._page()
        page.insert_text((20, 60), "10", fontsize=10)
        page.insert_text((20 + pymupdf.get_text_length("10", fontsize=10), 60), "0", fontsize=10, render_mode=3)
        self.assertEqual([w[4] for w in page.get_text("words")], ["100"])
        self.assertEqual(_texts(page), [])

    def test_text_added_after_a_read_is_seen(self):
        doc, page = self._page()
        page.insert_text((20, 30), "shown", fontsize=10)
        self.assertEqual(_texts(page), ["shown"])
        page.insert_text((20, 60), "decoy", fontsize=10, render_mode=3)
        self.assertEqual(_texts(page), ["shown"])

    def test_span_without_render_flags_is_unevaluable(self):
        with self.assertRaises(RuntimeError):
            _vt().hidden_reason({"bbox": (0, 0, 1, 1), "color": 0, "alpha": 255})

    def test_poppler_words_are_filtered_on_rotated_and_cropped_pages(self):
        for rotation, origin in [(r, o) for r in (0, 90, 180, 270) for o in (False, True)]:
            with self.subTest(rotation=rotation, mediabox_origin=origin), tempfile.TemporaryDirectory() as tmp:
                doc, page = self._page()
                if origin:  # MediaBox not at (0, 0)
                    doc.xref_set_key(page.xref, "MediaBox", "[50 20 350 320]")
                page.insert_text((60, 80), "shown", fontsize=10)
                page.insert_text((60, 120), "decoy", fontsize=10, render_mode=3)
                x0 = page.mediabox.x0
                page.set_cropbox(pymupdf.Rect(x0 + 30, 40, x0 + 280, 290))
                page.set_rotation(rotation)
                pdf = Path(tmp) / "p.pdf"
                doc.save(pdf)
                self.assertEqual([w.text for w in run_text_bbox(pdf)[0].words], ["shown"])


class SpecTableTests(unittest.TestCase):
    def _vth_doc(self, decoy: dict | None):
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=300)
        for x, head in ((380, "Min"), (420, "Typ"), (460, "Max"), (500, "Unit")):
            page.insert_text((x, 40), head, fontsize=8)
        page.insert_text((40, 70), "VGS(th) Gate Threshold Voltage VDS = VGS, ID = 250 µA", fontsize=8)
        for x, value in ((380, "1.0"), (420, "2.0"), (460, "3.0"), (500, "V")):
            page.insert_text((x, 70), value, fontsize=8)
        if decoy is not None:
            page.insert_text((428, 70), "2.5", fontsize=8, **decoy)
        return doc

    def test_hidden_cell_value_does_not_overwrite_the_printed_one(self):
        self.assertEqual(spec_vth_window(self._vth_doc(None))["typ"], 2.0)
        for name, style in HIDDEN:
            with self.subTest(name):
                window = spec_vth_window(self._vth_doc(style))
                self.assertEqual((window["min"], window["typ"], window["max"]), (1.0, 2.0, 3.0))

    def test_hidden_rating_row_is_not_the_rating(self):
        for name, style in HIDDEN:
            with self.subTest(name):
                doc = pymupdf.open()
                page = doc.new_page(width=600, height=300)
                page.insert_text((40, 40), "VDS Drain-Source Voltage 30 V", fontsize=8, **style)
                page.insert_text((40, 80), "VDS Drain-Source Voltage 100 V", fontsize=8)
                self.assertEqual(_max_vds_rating(doc), (100.0, 1))


class OwnedTitleTests(unittest.TestCase):
    def test_hidden_y_title_is_not_owned(self):
        for name, style in HIDDEN:
            with self.subTest(name):
                doc = pymupdf.open()
                page = doc.new_page(width=400, height=400)
                page.draw_rect(pymupdf.Rect(100, 50, 350, 300), color=(0, 0, 0), width=1)
                page.insert_text((150, 330), "VGS - Gate-to-Source Voltage (V)", fontsize=8)
                page.insert_text((85, 250), "VDS - Drain-Source Voltage (V)", fontsize=8, rotate=90, **style)
                frame = SimpleNamespace(x0=100, y0=50, x1=350, y1=300)
                identity = SimpleNamespace(to_pt=lambda x, y: (x, y))
                x_title, y_title = frame_owned_axis_titles(page, identity, lambda: frame)
                self.assertIn("Gate-to-Source", x_title)
                self.assertEqual(y_title, "")


class TickLabelTests(unittest.TestCase):
    """A hidden tick column beside the plot (Vishay's white "10000 1000 100")."""

    def _page(self, style: dict):
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=300)
        page.draw_rect(pymupdf.Rect(100, 50, 300, 250), color=(0, 0, 0), width=1)
        for i in range(6):
            y = 250 - 40 * i + 3
            shown, hidden = str(2 * i), str(3 * i)
            page.insert_text((92 - pymupdf.get_text_length(shown, fontsize=8), y), shown, fontsize=8)
            page.insert_text((303, y), hidden, fontsize=8, **style)
        return doc, page

    def test_hidden_column_nearer_the_frame_is_not_the_gate_axis(self):
        for name, style in HIDDEN:
            with self.subTest(name):
                doc, page = self._page(style)
                ticks = _local_y_ticks_for_plot(page, page.rect, 1.0, (100, 50, 300, 250))
                self.assertEqual(sorted(v for v, _ in ticks), [0, 2, 4, 6, 8, 10])

    def test_shared_tick_reader_returns_printed_labels_only(self):
        for name, style in HIDDEN:
            with self.subTest(name):
                doc, page = self._page(style)
                words, unread = power_tick_words(page)
                self.assertEqual(sorted(w[4] for w in words if w[0] > 300), [])
                self.assertEqual(len([w for w in words if w[0] < 100]), 6)


class RealPdfTests(unittest.TestCase):
    def _pdf(self, rel: str) -> Path:
        path = DATASHEETS / rel
        if not path.is_file():
            self.skipTest(f"reference datasheet not present: {path}")
        return path

    def test_scanned_pdf_keeps_its_ocr_layer(self):
        # every page is one image with an alpha-0 OCR text layer over it
        with pymupdf.open(self._pdf("infineon/BSC021N08NS5ATMA1.pdf")) as doc:
            for page in doc:
                self.assertEqual(len(_vt().words(page)), len(page.get_text("words")), page.number)

    def test_stale_hidden_table_values_are_dropped(self):
        # the cell shows 5 / 3.75 / 0.375; unpainted 500 / 0.5 / 0.05 sit beside it
        texts = [w.text for w in run_text_bbox(self._pdf("hxy/2N7002KDW.unicoded.pdf"))[1].words]
        for stale in ("500", "0.05"):
            self.assertNotIn(stale, texts)
        for shown in ("3.75", "0.375"):
            self.assertIn(shown, texts)
