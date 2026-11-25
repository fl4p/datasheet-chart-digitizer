"""Unit and scale reads that used to fail open: each case served ``ok`` with a wrong value.

Leakage current unit, digit-grouped Y labels ("1 000") in the shared axis reader,
the RDS(I_D) ohm unit, powder DC-bias / core-loss status without a printed-law
check, and a body-diode log frame past its last labelled decade (FCI7N60).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pymupdf

from datasheet_chart_digitizer import diode_forward_voltage as dfv
from datasheet_chart_digitizer import powder_core_loss, powder_dc_bias, rdson_current
from datasheet_chart_digitizer.diode_reverse_leakage import _title_current_prefix
from datasheet_chart_digitizer.find_charts import ChartPanel

DATASHEETS = Path(os.environ.get("DSDIG_DATASHEET_ROOT", "/Users/fab/dev/pv/pwr-mosfet-lib")) / "datasheets"
BAT54W = Path.home() / "dev/ee/hw/o2-probe/datasheets/BAT54W-G-vishay-85885-rev1.1.pdf"
KDM_CASES = json.loads((Path(__file__).parent / "fixtures/kdm_sendust_dc_bias/cases.json").read_text())
KDM_CASE = next(c for c in KDM_CASES["cases"] if c["id"] == "ks184_vector")
KDM_PDF = Path(KDM_CASE["pdf"])


def _kdm_pdf_or_skip() -> None:
    if not KDM_PDF.exists() or hashlib.sha256(KDM_PDF.read_bytes()).hexdigest() != KDM_CASE["pdf_sha256"]:
        raise unittest.SkipTest(f"REFERENCE NOT CHECKED: {KDM_PDF} missing or SHA-256 differs")


class LeakageTitleUnitTests(unittest.TestCase):
    def test_owned_title_beats_a_stray_panel_unit(self):
        # base read the first "(xA)" in the panel text: mA, 1000x high
        text = "Reverse current (mA) 1000 T = 125 °C (µA) 100 Current"
        self.assertEqual(_title_current_prefix(text, "I - Reverse Current (µA) R"), "u")

    def test_panel_text_with_two_units_and_no_owned_title_refuses(self):
        with self.assertRaisesRegex(ValueError, "different current units"):
            _title_current_prefix("Reverse current (mA) ... (µA) 100", "")

    def test_panel_text_with_one_unit_still_reads(self):
        self.assertEqual(_title_current_prefix("(µA) 100 100 °C Current Reverse", ""), "u")
        self.assertIsNone(_title_current_prefix("Reverse current 100", ""))


@unittest.skipUnless(BAT54W.is_file(), f"reference datasheet not present: {BAT54W}")
class LeakageHiddenUnitEndToEnd(unittest.TestCase):
    """BAT54W-G with an invisible (render mode 3) "Reverse current (mA)" in the panel."""

    def _digitize(self, hidden: bool) -> dict:
        from datasheet_chart_digitizer.diode_reverse_leakage import digitize_pdf

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "bat54.pdf"
            doc = pymupdf.open(BAT54W)
            if hidden:
                doc[1].insert_text((318.0, 296.5), "Reverse current (mA)", fontsize=6, render_mode=3)
            doc.save(pdf)
            results = digitize_pdf(pdf, Path(tmp) / "out", dpi=180)
        self.assertEqual(len(results), 1)
        return results[0]

    def test_clean_and_hidden_both_read_microamps(self):
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                result = self._digitize(hidden)
                self.assertIn("current_axis_unit_read_from_panel_as_uA", result["diagnostics"])
                hot = {c["temperature_c"]: c for c in result["curves"]}[125.0]["points"][0][1]
                self.assertTrue(5e-5 < hot < 2e-4, hot)


def _synthetic_panel(tmp: Path, labels: dict[int, list[tuple[str, float]]]):
    """Linear 0..10 x 0..4000 grid; *labels* maps a Y value to (text, gap_in_digits) pieces."""
    doc = pymupdf.open()
    page = doc.new_page(width=320, height=260)
    x0, y0, x1, y1 = 70, 20, 290, 200
    for i in range(6):
        x = x0 + i * (x1 - x0) / 5
        page.draw_line((x, y0), (x, y1), color=(0, 0, 0), width=0.6 if 0 < i < 5 else 1.0)
        page.insert_text((x - 3, y1 + 12), str(2 * i), fontsize=8)
    digit = pymupdf.get_text_length("0", fontsize=8)
    for j in range(5):
        y = y1 - j * (y1 - y0) / 4
        page.draw_line((x0, y), (x1, y), color=(0, 0, 0), width=0.6 if 0 < j < 4 else 1.0)
        pieces = labels[1000 * j]
        width = sum(pymupdf.get_text_length(t, fontsize=8) + gap * digit for t, gap in pieces)
        x = x0 - 4 - width
        for text, gap in pieces:
            x += gap * digit
            page.insert_text((x, y + 3), text, fontsize=8)
            x += pymupdf.get_text_length(text, fontsize=8)
    points = [(x0 + k * (x1 - x0) / 50, y1 - (y1 - y0) * (0.1 + 0.8 * (k / 50) ** 1.5)) for k in range(51)]
    page.draw_polyline(points, color=(1, 0, 0), width=1.2)
    pdf = tmp / "synthetic.pdf"
    doc.save(pdf)
    crop_box = (20.0, 5.0, 305.0, 225.0)
    png = tmp / "crop.png"
    pymupdf.open(pdf)[0].get_pixmap(dpi=220, clip=pymupdf.Rect(*crop_box)).save(png)
    panel = ChartPanel(str(pdf), "synthetic", 1, 1, "RDS(on) vs ID", "rds_on_current",
                       (25.0, 10.0, 300.0, 220.0), crop_box, "crop.png", "", "", [])
    return panel, png


class DigitGroupedYLabelTests(unittest.TestCase):
    def _y_values(self, labels) -> list[float]:
        with tempfile.TemporaryDirectory() as tmp:
            panel, png = _synthetic_panel(Path(tmp), labels)
            cal = dfv.calibrate_panel(panel, png)
        self.assertEqual(dfv.served_axis_guard.problems(cal.grid_checks), ([], []))
        return sorted(t.value for t in cal.y_axis.ticks)

    def test_split_thousands_read_as_thousands(self):
        # base clustered "1 000" by right edge into a "1".."4" column: 1000x low
        labels = {0: [("0", 0)], **{v: [(str(v // 1000), 0), ("000", 0.3)] for v in (1000, 2000, 3000, 4000)}}
        self.assertEqual(self._y_values(labels), [0, 1000, 2000, 3000, 4000])

    def test_plain_thousands_unchanged(self):
        labels = {v: [(str(v), 0)] for v in (0, 1000, 2000, 3000, 4000)}
        self.assertEqual(self._y_values(labels), [0, 1000, 2000, 3000, 4000])

    def test_wide_digit_group_gap_refuses(self):
        labels = {0: [("0", 0)], **{v: [(str(v // 1000), 0), ("000", 1.4)] for v in (1000, 2000, 3000, 4000)}}
        with tempfile.TemporaryDirectory() as tmp:
            panel, png = _synthetic_panel(Path(tmp), labels)
            with self.assertRaisesRegex(RuntimeError, "unjoined digit group"):
                dfv.calibrate_panel(panel, png)


class RdsCurrentUnitTests(unittest.TestCase):
    def _scale(self, local: str, owned: str = "") -> float:
        with mock.patch.object(rdson_current, "_local_axis_text", return_value=local):
            return rdson_current._rdson_y_scale_to_mohm(None, *([owned] if owned else []))

    def test_owned_title_decides(self):
        # base: a neighbour's "mΩ" anywhere within 30 pt won (factor 1)
        self.assertEqual(self._scale("RDS(on) [mΩ] neighbour, RDS(on) (Ω)", "RDS(on) , Drain-Source On-Resistance (Ω)"), 1000.0)
        self.assertEqual(self._scale("RDS(on) (Ω) neighbour", "RDS(on) [mΩ]"), 1.0)

    def test_both_units_without_owned_title_refuse(self):
        with self.assertRaisesRegex(RuntimeError, "both mΩ and Ω"):
            self._scale("RDS(on) [mΩ] neighbour, RDS(on) (Ω)")

    def test_no_unit_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "no mΩ or Ω"):
            self._scale("RDS(on) Drain current ID (A)")

    def test_single_unit_reads(self):
        self.assertEqual(self._scale("RDS(on) (mOhm)"), 1.0)
        self.assertEqual(self._scale("RDS(on) [Ω]"), 1000.0)
        # Symbol-font Ω (U+F057, or read as W), or a glyph with no text mapping
        # (AO4882, BSC016N06NS, NTMFS10N7D2C)
        for text in ("RDS(on) [m\uf057]", "RDS(ON) (mW)", "RDS(on) [m\n]", "SOURCE ON-RESISTANCE (m\x02)"):
            self.assertEqual(self._scale(text), 1.0, text)
        with self.assertRaisesRegex(RuntimeError, "no mΩ or Ω"):
            self._scale("RDS(on) 0 10 20  PD (W)")

    def test_milliohm_titled_real_chart_unchanged(self):
        pdf = DATASHEETS / "ts/TSM60NE200CIT.pdf"
        if not pdf.exists():
            self.skipTest(f"corpus PDF missing: {pdf}")
        with tempfile.TemporaryDirectory() as tmp:
            results, errors = rdson_current.digitize_pdf_fail_closed(pdf, Path(tmp), 180)
        self.assertEqual(errors, [])
        self.assertEqual([r["status"] for r in results], ["ok"])
        first = results[0]["curves"][0]["points"][0]
        self.assertAlmostEqual(first[1], 164.02, delta=0.5)


class PowderStatusTests(unittest.TestCase):
    def setUp(self):
        _kdm_pdf_or_skip()
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _dc_bias(self, printed: bool, x_range: str | None = None) -> dict:
        args = [str(KDM_PDF), "--page", str(KDM_CASE["page"]), "--mode", KDM_CASE["mode"],
                "--perms", ",".join(str(p) for p in KDM_CASES["perms_left_to_right"]),
                "--out", str(self.out), "--tag", "t", *KDM_CASE["args"]]
        if printed:
            args += ["--printed", ";".join(f"{k}:{a:g},{b:g},{c:g}" for k, (a, b, c) in KDM_CASE["printed_rows"].items())]
        if x_range:
            args += ["--x-range", x_range]
        with redirect_stdout(io.StringIO()):
            powder_dc_bias.main(args)
        return json.loads((self.out / "t-dcbias.json").read_text())

    def test_dc_bias_without_printed_rows_is_unverified(self):
        # base: "ok" with the H axis off by 10x (76.4 Oe vs 7.64)
        self.assertEqual(self._dc_bias(False)["status"], "unverified")
        self.assertEqual(self._dc_bias(False, "10,10000")["status"], "unverified")

    def test_dc_bias_with_printed_rows_stays_ok(self):
        self.assertEqual(self._dc_bias(True)["status"], "ok")

    def _core_loss(self, *extra: str) -> dict:
        with redirect_stdout(io.StringIO()):
            powder_core_loss.main([str(KDM_PDF), "--out", str(self.out), *extra])
        return json.loads((self.out / f"{KDM_PDF.stem}-core-loss.json").read_text())

    def test_core_loss_ok_only_against_the_printed_law(self):
        result = self._core_loss()
        self.assertEqual(result["status"], "ok")
        ratios = {c["frequency_khz"]: c["printed_law_ratio"] for c in result["curves"]}
        self.assertAlmostEqual(ratios[8.0], 1.045, delta=0.01)  # approved 2026-09-06
        self.assertAlmostEqual(ratios[50.0], 0.978, delta=0.01)
        self.assertEqual(self._core_loss("--law", "none")["status"], "unverified")
        self.assertEqual(self._core_loss("--y-range", "10,100000")["status"], "unverified")


class LogFramePastLastDecadeTests(unittest.TestCase):
    def test_fci7n60_body_diode_reaches_the_frame(self):
        pdf = DATASHEETS / "onsemi/FCI7N60.pdf"
        if not pdf.exists():
            self.skipTest(f"corpus PDF missing: {pdf}")
        from datasheet_chart_digitizer.find_charts import process_pdf

        with tempfile.TemporaryDirectory() as tmp:
            panels = process_pdf(pdf, Path(tmp), 180)
            results, _errors = dfv.digitize_panels_fail_closed(panels, Path(tmp))
        result = next(r for r in results if r["panel"]["page"] == 4 and r["panel"]["diagram"] == 4)
        top = [max(p[1] for p in c["points"]) for c in result["curves"]]
        # base: plot box stopped at the 10 A label, curves cut there, served ok
        self.assertTrue(result["status"] != "ok" or min(top) > 17.0, (result["status"], top))


if __name__ == "__main__":
    unittest.main()
