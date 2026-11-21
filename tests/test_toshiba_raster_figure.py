"""Whole-figure raster panels (Toshiba): transfer, V(BR)DSS(T) and Vth(T).

Unit tests run anywhere; the end-to-end classes need the local datasheet
library and tesseract.  TPH1R306PL.pdf in the library is Rev.4.0 (2019-10-18);
its chart images are byte-identical to Rev.4.0.A (2026-04-14).
"""

from __future__ import annotations

import csv
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from datasheet_chart_digitizer import breakdown_voltage as bv
from datasheet_chart_digitizer import find_charts
from datasheet_chart_digitizer import raster_figure as rf
from datasheet_chart_digitizer import raster_plot_labels as rpl
from datasheet_chart_digitizer import raster_tick_ocr as rto
from datasheet_chart_digitizer import threshold_voltage as vth
from datasheet_chart_digitizer import transfer_characteristics as tc
from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.chart_classifier import classify_chart

pytestmark = pytest.mark.xdist_group("toshiba_raster_figure")

TOSHIBA = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/toshiba")
TPH1R306PL = TOSHIBA / "TPH1R306PL.pdf"
TK100E10N1 = TOSHIBA / "TK100E10N1.pdf"
TPCC8105 = TOSHIBA / "TPCC8105.pdf"
XPW4R10ANB = TOSHIBA / "XPW4R10ANB.pdf"
_HAVE_TESSERACT = shutil.which("tesseract") is not None


def _native(gray: np.ndarray) -> rf.NativeRender:
    return rf.NativeRender(gray, 1.0, 72.0)


def _grid_image(curve_width: int, curves: list[tuple[tuple[int, int], tuple[int, int]]]) -> np.ndarray:
    """400x400 white image, frame 5 px, minor rules 2 px, major 4 px, given curves."""
    img = np.full((400, 400), 255, np.uint8)
    for i, v in enumerate(range(20, 381, 40)):
        w = 5 if v in (20, 380) else (4 if i % 2 == 0 else 2)
        img[:, v - w // 2: v - w // 2 + w] = 0
        img[v - w // 2: v - w // 2 + w, :] = 0
    for a, b in curves:
        cv2.line(img, a, b, 0, curve_width, cv2.LINE_AA)
    return img


PLOT = PlotBox(20, 20, 380, 380)
RULES = [float(v) for v in range(20, 381, 40)]


class ClassifierCaptions(unittest.TestCase):
    def test_threshold_and_channel_temperature_captions(self):
        self.assertEqual(classify_chart("V th - T a", ""), "threshold_voltage")
        self.assertEqual(classify_chart("Vth - Tch", ""), "threshold_voltage")
        self.assertEqual(classify_chart("V GS(th) - T j", ""), "threshold_voltage")
        self.assertEqual(classify_chart("Threshold Voltage vs Temperature", ""), "threshold_voltage")
        self.assertEqual(classify_chart("V (BR)DSS - T ch", ""), "breakdown_voltage")
        self.assertEqual(classify_chart("Safe Operating Area", ""), "safe_operating_area")
        # a threshold-voltage word alone is not a temperature chart
        self.assertEqual(classify_chart("Gate threshold voltage", ""), "chart")


class CurveMaskByStrokeWidth(unittest.TestCase):
    def test_sloped_curve_survives_and_every_rule_goes(self):
        img = _grid_image(8, [((40, 330), (360, 90))])
        native = _native(img)
        widest = rf.rule_width(native, PLOT, RULES, RULES)
        self.assertEqual(widest, 4)
        mask, _k = rf.curve_mask(native, PLOT, widest)
        trace = rf.trace_single_curve(native, mask)
        xs = np.array([p[0] for p in trace.points_crop])
        ys = np.array([p[1] for p in trace.points_crop])
        resid = np.abs(ys - (330 + (xs - 40) * (90 - 330) / 320.0))
        # round line caps and rule crossings shift single columns by up to ~half a stroke
        self.assertLess(float(np.median(resid)), 0.5)
        self.assertLess(float(np.percentile(resid, 90)), 1.0)
        self.assertLessEqual(float(resid.max()), 3.0)
        self.assertGreater(xs.max() - xs.min(), 280)

    def test_curve_no_thicker_than_the_rules_refuses(self):
        img = _grid_image(1, [((40, 330), (360, 90))])
        native = _native(img)
        with self.assertRaisesRegex(RuntimeError, "no ink thicker than the widest grid rule"):
            rf.curve_mask(native, PLOT, rf.rule_width(native, PLOT, RULES, RULES))

    def test_two_curves_refuse_a_single_curve_trace(self):
        img = _grid_image(8, [((40, 330), (360, 90)), ((40, 200), (360, 260))])
        native = _native(img)
        mask, _k = rf.curve_mask(native, PLOT, rf.rule_width(native, PLOT, RULES, RULES))
        with self.assertRaisesRegex(RuntimeError, "more than one thick run|split into separate pieces"):
            rf.trace_single_curve(native, mask)

    def test_rows_trace_three_steep_curves_left_to_right(self):
        img = _grid_image(8, [((100, 370), (160, 30)), ((150, 370), (210, 30)), ((210, 370), (270, 30))])
        native = _native(img)
        mask, _k = rf.curve_mask(native, PLOT, rf.rule_width(native, PLOT, RULES, RULES))
        rows = rf.trace_curves_by_rows(native, mask, 3)
        self.assertEqual(len(rows.curves_crop), 3)
        mids = [float(np.median([p[0] for p in c])) for c in rows.curves_crop]
        self.assertEqual(mids, sorted(mids))
        for curve, (x0, x1) in zip(rows.curves_crop, [(100, 160), (150, 210), (210, 270)]):
            resid = np.array([abs(x - (x0 + (370 - y) * (x1 - x0) / 340.0)) for x, y in curve])
            self.assertLess(float(np.median(resid)), 0.5)
            self.assertLessEqual(float(resid.max()), 3.0)

    def test_rows_refuse_when_the_count_is_never_separated(self):
        img = _grid_image(8, [((100, 370), (160, 30)), ((150, 370), (210, 30))])
        native = _native(img)
        mask, _k = rf.curve_mask(native, PLOT, rf.rule_width(native, PLOT, RULES, RULES))
        with self.assertRaisesRegex(RuntimeError, "hold exactly 3"):
            rf.trace_curves_by_rows(native, mask, 3)


class LabelVoteAndTemperatureTokens(unittest.TestCase):
    def test_split_vote_is_no_value(self):
        native = rf.NativeRender(np.full((50, 50), 255, np.uint8), 1.0, 72.0)
        reads = iter(["72", "12", "72", "12", "72"])
        with patch.object(rto, "render_label", lambda *a, **k: Path("x.png")), \
                patch.object(rto, "_tesseract_digits", lambda png, psm: next(reads)):
            self.assertIsNone(rto._ocr_label(native, (0, 0, 10, 10), 2))

    def test_near_unanimous_vote_is_the_value(self):
        native = rf.NativeRender(np.full((50, 50), 255, np.uint8), 1.0, 72.0)
        reads = iter(["56", "56", "96", "56", "56."])
        with patch.object(rto, "render_label", lambda *a, **k: Path("x.png")), \
                patch.object(rto, "_tesseract_digits", lambda png, psm: next(reads)):
            self.assertEqual(rto._ocr_label(native, (0, 0, 10, 10), 2), "56")

    def test_condition_numbers_and_letters_are_not_temperatures(self):
        tok = rpl.Token
        tokens = [
            tok((100, 10, 120, 28), "10", "10", "10"), tok((126, 10, 136, 28), None, "V", "V"),  # VDS = 10 V
            tok((10, 50, 30, 68), "7", "Ta", "Ta"),                                            # letters
            tok((10, 90, 40, 108), "100", "100", "100"),
            tok((60, 90, 90, 108), "25", "25°", "25"),
            tok((10, 130, 120, 148), "--55", "Ta = -55 °C", "Ta = -55 °C"),
        ]
        values = sorted(label.value for label in rpl.temperature_labels(tokens, 18.0))
        self.assertEqual(values, [-55.0, 25.0, 100.0])


class ThresholdTableWindow(unittest.TestCase):
    def test_p_channel_window_is_ordered(self):
        spec = {"min": -0.8, "max": -2.0}
        self.assertEqual(vth._table_check(-1.3, spec)["verdict"], "consistent")
        self.assertEqual(vth._table_check(-0.5, spec)["verdict"], "inconsistent")

    def test_missing_row_is_not_evaluable(self):
        self.assertEqual(vth._table_check(2.0, None)["verdict"], "not_evaluable")

    def test_temperature_kind_from_caption(self):
        self.assertEqual(vth.temperature_kind("V th - T a"), "Ta")
        self.assertEqual(vth.temperature_kind("Vth - Tch"), "Tch")


def _find(pdf: Path, out: Path) -> list[dict]:
    panels = find_charts.process_pdf(pdf, out, dpi=180)
    charts = []
    for p in panels:
        charts.append({
            "pdf": str(pdf), "part": pdf.stem, "page": p.page, "diagram": p.diagram, "kind": p.kind,
            "title": p.title, "bbox_pt": list(p.bbox_pt), "crop_box_pt": list(p.crop_box_pt),
            "crop_png": str(p.crop_png), "text": p.text,
        })
    return charts


def _run(module, chart: dict, out: Path) -> dict:
    crop = Path(chart["crop_png"])
    return module.process_chart(chart, out / crop, out, crop.with_suffix(""))


def _points(path: str) -> list[list[str]]:
    with open(path) as fh:
        return list(csv.reader(fh))[1:]


@unittest.skipUnless(_HAVE_TESSERACT and TPH1R306PL.exists(), "tesseract or local TPH1R306PL unavailable")
class Tph1r306plEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="tph1r306pl-")
        cls.out = Path(cls._tmp.name)
        cls.charts = {c["diagram"]: c for c in _find(TPH1R306PL, cls.out)}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_finder_binds_whole_figure_images_and_new_kinds(self):
        self.assertEqual(self.charts[83]["kind"], "transfer")
        self.assertEqual(self.charts[87]["kind"], "breakdown_voltage")
        self.assertEqual(self.charts[88]["kind"], "threshold_voltage")
        self.assertEqual(self.charts[816]["kind"], "safe_operating_area")
        # label-complete: the image rect, not the tick-clipping grid bbox
        x0, _y0, x1, _y1 = self.charts[87]["bbox_pt"]
        self.assertLess(x0, 80.0)
        self.assertGreater(x1, 300.0)

    def test_breakdown_voltage_raster_trace(self):
        result = _run(bv, self.charts[87], self.out)
        self.assertEqual(result["method"], "raster_native")
        self.assertEqual(result["axis_text_source"], "ocr_raster_figure")
        # a raster-figure trace is never served as verified before review
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["anchor"]["verdict"], "verified")
        self.assertAlmostEqual(result["v_at_25c"], 60.0, delta=0.3)
        self.assertAlmostEqual(result["slope_mv_per_k"], 40.5, delta=3.0)
        self.assertEqual({k: v["status"] for k, v in result["calibration"]["grid_check"].items()},
                         {"x": "verified", "y": "verified"})

    def test_threshold_voltage_readouts(self):
        result = _run(vth, self.charts[88], self.out)
        self.assertEqual(result["status"], "overlay-review-required")
        self.assertEqual(result["temperature_kind"], "Ta")
        reads = {r["t_c"]: r["vth_v"] for r in result["readouts"]}
        self.assertAlmostEqual(reads[25.0], 1.99, delta=0.05)
        self.assertAlmostEqual(reads[100.0], 1.58, delta=0.05)
        self.assertEqual(result["table_check"]["verdict"], "consistent")
        self.assertEqual(result["table_check"]["spec"]["min"], 1.5)
        self.assertEqual(result["table_check"]["spec"]["max"], 2.5)

    def test_transfer_raster_three_temperatures_bound_by_print(self):
        result = _run(tc, self.charts[83], self.out)
        self.assertEqual(result["status"], "overlay-review-required")
        self.assertEqual(result["temperatures_c"], [-55.0, 25.0, 100.0])
        self.assertEqual(result["temperature_binding"]["rule"], "print+physics_agrees")
        rules = {b["value"]: b["rule"] for b in result["temperature_binding"]["labels"]}
        self.assertEqual(rules, {100.0: "outer_side_left", 25.0: "leader", -55.0: "outer_side_right"})
        pts = [(float(v), float(i)) for t, v, i in _points(result["csv"]) if float(t) == 25.0]
        pts.sort(key=lambda p: p[1])
        vgs = np.interp([1.0, 48.0], [p[1] for p in pts], [p[0] for p in pts])
        # hand trace of the same print (dcdc-tools fugu2-hs-short): 2.593 V and 3.166 V
        self.assertAlmostEqual(float(vgs[0]), 2.593, delta=0.03)
        self.assertAlmostEqual(float(vgs[1]), 3.166, delta=0.03)


@unittest.skipUnless(_HAVE_TESSERACT and TK100E10N1.exists() and TPCC8105.exists() and XPW4R10ANB.exists(),
                     "tesseract or local Toshiba corpus unavailable")
class OtherToshibaRasterFigures(unittest.TestCase):
    """The same paths on two more Toshiba layouts: not a TPH1R306PL special case."""

    def test_tk100e10n1_threshold_and_transfer(self):
        with tempfile.TemporaryDirectory(prefix="tk100-") as tmp:
            out = Path(tmp)
            charts = {c["kind"]: c for c in _find(TK100E10N1, out)}
            v = _run(vth, charts["threshold_voltage"], out)
            reads = {r["t_c"]: r["vth_v"] for r in v["readouts"]}
            self.assertAlmostEqual(reads[25.0], 3.02, delta=0.06)
            t = _run(tc, charts["transfer"], out)
            self.assertEqual(t["temperatures_c"], [-55.0, 25.0, 100.0])
            self.assertEqual(t["temperature_binding"]["rule"], "print+physics_agrees")

    def test_tpcc8105_p_channel_threshold_is_negative_and_in_window(self):
        with tempfile.TemporaryDirectory(prefix="tpcc8105-") as tmp:
            out = Path(tmp)
            charts = {c["kind"]: c for c in _find(TPCC8105, out)}
            v = _run(vth, charts["threshold_voltage"], out)
            reads = {r["t_c"]: r["vth_v"] for r in v["readouts"]}
            self.assertAlmostEqual(reads[25.0], -1.30, delta=0.04)
            self.assertEqual(v["table_check"]["verdict"], "consistent")
            # overlapping P-channel transfer branches: refused, never guessed
            with self.assertRaises(RuntimeError):
                _run(tc, charts["transfer"], out)

    def test_xpw4r10anb_breakdown_and_threshold(self):
        with tempfile.TemporaryDirectory(prefix="xpw4r10anb-") as tmp:
            out = Path(tmp)
            charts = {c["kind"]: c for c in _find(XPW4R10ANB, out)}
            b = _run(bv, charts["breakdown_voltage"], out)
            self.assertEqual(b["method"], "raster_native")
            self.assertEqual(b["status"], "unverified")
            v = _run(vth, charts["threshold_voltage"], out)
            reads = {r["t_c"]: r["vth_v"] for r in v["readouts"]}
            self.assertAlmostEqual(reads[25.0], 3.00, delta=0.06)


if __name__ == "__main__":
    unittest.main()
