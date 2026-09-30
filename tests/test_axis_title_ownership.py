"""Geometric axis-title ownership on multi-panel pages (transfer ID/VGS titles).

Small generated PDF pages: the titles sit outside a tight panel crop, and a
neighbouring panel's titles sit in the same gutter. Ownership must take the
panel's own titles and never a neighbour's.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import pymupdf

from datasheet_chart_digitizer import transfer_characteristics as tc
from datasheet_chart_digitizer.axis_title_identity import owned_axis_titles, page_rules

FS = 7.0


def _panel(page, frame, *, ticks_x=True, ticks_y=True, y_title=None, y_top=None, x_title=None, caption=None):
    x0, y0, x1, y1 = frame
    page.draw_rect(pymupdf.Rect(*frame), color=(0, 0, 0), width=0.8)
    for i in range(1, 4):
        gx = x0 + i * (x1 - x0) / 4
        gy = y0 + i * (y1 - y0) / 4
        page.draw_line((gx, y0), (gx, y1), color=(0.6, 0.6, 0.6), width=0.3)
        page.draw_line((x0, gy), (x1, gy), color=(0.6, 0.6, 0.6), width=0.3)
    if ticks_x:
        for i in range(5):
            page.insert_text((x0 + i * (x1 - x0) / 4 - 2, y1 + 9), str(2 * i), fontsize=FS)
    if ticks_y:
        for i in range(5):
            page.insert_text((x0 - 12, y1 - i * (y1 - y0) / 4 + 2), str(10 * i), fontsize=FS)
    if y_title:
        # rotated, bottom-to-top, left of the tick gutter
        page.insert_text((x0 - 17, 0.5 * (y0 + y1) + 20), y_title, fontsize=FS, rotate=90)
    if y_top:
        page.insert_text((x0 - 10, y0 - 4), y_top, fontsize=FS)
    if x_title:
        page.insert_text((0.5 * (x0 + x1) - 20, y1 + 19), x_title, fontsize=FS)
    if caption:
        page.insert_text((0.5 * (x0 + x1) - 40, y1 + 29), caption, fontsize=FS)


def _owned(page, frame):
    rules = page_rules(page.get_drawings(), 0.3 * min(frame[2] - frame[0], frame[3] - frame[1]))
    return owned_axis_titles(page.get_text("words"), rules, frame)


def _chart(title="Typical Transfer Characteristics"):
    return {"title": title, "text": "Tj = 25 °C Tj = 150 °C"}


class AxisTitleOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=595, height=842)

    def tearDown(self):
        self.doc.close()

    def test_owns_rotated_y_title_and_x_title_outside_the_tight_crop(self):
        frame = (100.0, 100.0, 260.0, 240.0)
        _panel(self.page, frame, y_title="Drain Current, ID (A)", x_title="Gate-Source Voltage, VGS (V)",
               caption="Figure 3. Transfer Characteristics")
        x_text, y_text = _owned(self.page, frame)
        self.assertIn("VGS", x_text)
        self.assertIn("ID", y_text)
        tc._validate_transfer_panel_semantics(_chart(), owned_titles=(x_text, y_text))

    def test_owns_horizontal_top_left_y_title(self):
        frame = (100.0, 100.0, 260.0, 240.0)
        _panel(self.page, frame, y_top="ID [A]", x_title="VGS [V]")
        self.assertEqual(_owned(self.page, frame), ("VGS [V]", "ID [A]"))

    def test_side_by_side_neighbours_keep_their_own_y_titles(self):
        left = (100.0, 100.0, 260.0, 240.0)
        right = (300.0, 100.0, 460.0, 240.0)  # 40 pt gutter holds only right's ticks
        _panel(self.page, left, y_title="Drain Current, ID (A)", x_title="VDS [V]")
        _panel(self.page, right, x_title="VGS [V]")
        # known-bad: the left neighbour's ID title is the only ID text near
        # the right frame's left gutter; it is across the neighbour's frame
        self.assertEqual(_owned(self.page, right), ("VGS [V]", ""))
        self.assertEqual(_owned(self.page, left), ("VDS [V]", "Drain Current, ID (A)"))
        with self.assertRaisesRegex(RuntimeError, "lacks owned VGS and ID"):
            tc._validate_transfer_panel_semantics(
                {"title": "Typical Transfer Characteristics", "text": ""},
                owned_titles=_owned(self.page, right),
            )

    def test_neighbours_right_axis_title_in_a_tight_gutter_is_not_owned(self):
        left = (100.0, 100.0, 260.0, 240.0)
        right = (300.0, 100.0, 460.0, 240.0)
        _panel(self.page, left, x_title="QG [nC]")
        # dual-axis neighbour: right tick labels and a rotated right title
        for i in range(5):
            self.page.insert_text((left[2] + 2, left[3] - i * 35 + 2), str(2 * i), fontsize=FS)
        self.page.insert_text((left[2] + 20, 190.0), "ID (A)", fontsize=FS, rotate=90)
        _panel(self.page, right, x_title="VGS [V]")
        self.assertEqual(_owned(self.page, right), ("VGS [V]", ""))

    def test_title_across_another_frames_rule_is_not_owned(self):
        narrow = (230.0, 100.0, 280.0, 240.0)
        frame = (300.0, 100.0, 460.0, 240.0)
        _panel(self.page, narrow, ticks_x=False, ticks_y=False)
        self.page.insert_text((226.0, 190.0), "ID (A)", fontsize=FS, rotate=90)
        _panel(self.page, frame, x_title="VGS [V]")
        self.assertEqual(_owned(self.page, frame), ("VGS [V]", ""))

    def test_figure_border_around_the_panel_is_not_a_neighbour(self):
        frame = (100.0, 100.0, 260.0, 240.0)
        self.page.draw_rect(pymupdf.Rect(70.0, 80.0, 270.0, 275.0), color=(0, 0, 0), width=0.5)
        _panel(self.page, frame, y_title="ID (A)", x_title="VGS (V)")
        self.assertEqual(_owned(self.page, frame), ("VGS (V)", "ID (A)"))

    def test_stacked_tight_gap_does_not_steal_the_upper_panels_id_x_title(self):
        upper = (100.0, 100.0, 260.0, 240.0)
        lower = (100.0, 272.0, 260.0, 412.0)  # upper x title ends ~10 pt above lower frame
        # an RDS(on)-vs-ID panel above: its x title names ID and sits left-anchored
        _panel(self.page, upper, x_title=None)
        self.page.insert_text((upper[0] - 8, upper[3] + 19), "ID, Drain Current (A)", fontsize=FS)
        _panel(self.page, lower, x_title="VGS [V]")
        x_text, y_text = _owned(self.page, lower)
        self.assertEqual((x_text, y_text), ("VGS [V]", ""))
        with self.assertRaisesRegex(RuntimeError, "lacks owned VGS and ID"):
            tc._validate_transfer_panel_semantics(
                {"title": "Typical Transfer Characteristics", "text": ""}, owned_titles=(x_text, y_text)
            )

    def test_stacked_tight_gap_keeps_the_lower_panels_own_top_title(self):
        upper = (100.0, 100.0, 260.0, 240.0)
        lower = (100.0, 272.0, 260.0, 412.0)
        _panel(self.page, upper, x_title="VDS [V]")
        _panel(self.page, lower, y_top="ID [A]", x_title="VGS [V]")
        self.assertEqual(_owned(self.page, lower), ("VGS [V]", "ID [A]"))
        # the lower panel's top title is not the upper panel's x title
        self.assertEqual(_owned(self.page, upper)[0], "VDS [V]")

    def test_x_title_of_the_panel_below_is_not_owned(self):
        upper = (100.0, 100.0, 260.0, 240.0)
        lower = (100.0, 262.0, 260.0, 300.0)  # short panel: its x title is within reach
        _panel(self.page, upper, y_title="Drain Current, ID (A)")
        _panel(self.page, lower, ticks_x=False, ticks_y=False)
        self.page.insert_text((150.0, lower[3] - 12), "VGS [V]", fontsize=FS)
        self.page.insert_text((150.0, lower[3] + 12), "VGS (V)", fontsize=FS)
        self.assertEqual(_owned(self.page, upper)[0], "")

    def test_caption_between_frame_and_text_blocks_x_title(self):
        frame = (100.0, 100.0, 260.0, 240.0)
        _panel(self.page, frame, y_title="ID (A)", caption="Figure 3. Output Characteristics")
        self.page.insert_text((140.0, frame[3] + 40), "VGS (V)", fontsize=FS)
        self.assertEqual(_owned(self.page, frame)[0], "")

    def test_absent_titles_still_refuse(self):
        frame = (100.0, 100.0, 260.0, 240.0)
        _panel(self.page, frame, caption="Figure 3. Transfer Characteristics")
        self.page.insert_text((180.0, 130.0), "VDS = 5 V", fontsize=FS)
        owned = _owned(self.page, frame)
        self.assertEqual(owned, ("", ""))
        with self.assertRaisesRegex(RuntimeError, "lacks owned VGS and ID"):
            tc._validate_transfer_panel_semantics({"title": "Transfer Characteristics", "text": ""}, owned_titles=owned)

    def test_condition_callout_below_frame_is_not_a_gate_axis_title(self):
        frame = (100.0, 100.0, 260.0, 240.0)
        _panel(self.page, frame, y_title="ID (A)")
        self.page.insert_text((150.0, frame[3] + 19), "VGS = 10 V", fontsize=FS)
        self.assertEqual(_owned(self.page, frame), ("", "ID (A)"))



LIB = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets")
BSZ018 = LIB / "infineon" / "BSZ018N04LS6.pdf"
FDB035 = LIB / "onsemi" / "FDB035N10A.pdf"


class RealMultiPanelOwnershipTests(unittest.TestCase):
    """Real 2x2 / 2x3 pages: every panel owns its own titles, none a neighbour's.

    Frames are the dsdig-detected vector plot frames (PDF points) of each panel.
    """

    def _owned_on(self, pdf, page_no, frames):
        with pymupdf.open(pdf) as document:
            page = document[page_no - 1]
            return {name: _owned(page, frame) for name, frame in frames.items()}

    @unittest.skipUnless(BSZ018.exists(), "local BSZ018N04LS6 datasheet unavailable")
    def test_infineon_boxed_2x2_page(self):
        owned = self._owned_on(BSZ018, 7, {
            "output": (81.2, 122.7, 297.1, 363.8), "rds_id": (350.2, 122.7, 566.5, 366.8),
            "transfer": (81.2, 474.1, 297.1, 715.3), "rds_vgs": (350.2, 474.1, 566.5, 715.3),
        })
        self.assertEqual(owned["transfer"], ("VGS [V]", "ID [A]"))
        # the right neighbour's RDS(on) title sits in the gutter beside the transfer frame
        self.assertEqual(owned["rds_vgs"][0], "VGS [V]")
        self.assertTrue(owned["rds_vgs"][1].startswith("RDS(on) [m"))
        self.assertEqual(owned["output"], ("VDS [V]", "ID [A]"))
        self.assertEqual(owned["rds_id"][0], "ID [A]")
        self.assertTrue(owned["rds_id"][1].startswith("RDS(on) [m"))

    @unittest.skipUnless(FDB035.exists(), "local FDB035N10A datasheet unavailable")
    def test_onsemi_six_panel_page(self):
        owned = self._owned_on(FDB035, 4, {
            "transfer": (356.1, 90.0, 514.5, 234.0), "rds_id": (122.1, 307.3, 280.8, 451.3),
            "body_diode": (356.1, 307.6, 514.8, 452.0), "gate_charge": (356.1, 524.3, 514.8, 668.3),
        })
        self.assertEqual(owned["transfer"], ("VGS, Gate−Source Voltage (V)", "ID, Drain Current (A)"))
        # the transfer's lower neighbour names IS/VSD, never the transfer's ID/VGS
        self.assertEqual(owned["body_diode"], ("VSD, Body Diode Forward Voltage (V)", "IS, Reverse Drain Current (A)"))
        self.assertEqual(owned["rds_id"][0], "ID, Drain Current (A)")
        self.assertEqual(owned["gate_charge"], ("Qg, Total Gate Charge (nC)", "VGS, Gate−Source Voltage (V)"))


SET1_PAGE_0047 = Path(
    "/Users/fab/dev/pv/ee/datasheet-chart-digitizer/out/synth-charts/set1/dsdig/page_0047"
)


@unittest.skipUnless((SET1_PAGE_0047 / "charts.json").exists(), "synthetic set1 page_0047 unavailable")
class SyntheticTightCropProcessChartTests(unittest.TestCase):
    """set1 syn1_0047: the finder crop clips the rotated "ID [A]" title.

    Base reported the semantic refusal for every later failure; with the owned
    titles the panel reports its real refusal (a two-label binding ambiguity).
    """

    def test_owned_titles_let_the_real_refusal_surface(self):
        import json
        import tempfile

        chart = next(c for c in json.loads((SET1_PAGE_0047 / "charts.json").read_text()) if c["kind"] == "transfer")
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(RuntimeError) as caught:
                tc.process_chart(chart, SET1_PAGE_0047 / chart["crop_png"], Path(out), Path("x"))
        self.assertNotIn("lacks owned VGS and ID", str(caught.exception))
        self.assertIn("binding is ambiguous", str(caught.exception))

if __name__ == "__main__":
    unittest.main()
