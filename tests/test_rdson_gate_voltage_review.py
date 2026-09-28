"""Known-bad cases from the independent Codex review of RDS(on)-vs-VGS batch 15.

Each test reproduces one confirmed finding on the real datasheet it was found
on (review: ~/dev/ee/solar-charger-eval/rds-vgs/codex-review/FINDINGS.md,
per-point measurements in scratch/review-20260928/REVIEW.md). The reference
values are the reviewer's independent measurements of the source PDFs, which
were re-checked against the PDFs before the fixes.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from datasheet_chart_digitizer import rdson_gate_voltage as rgv
from datasheet_chart_digitizer import rdson_gate_voltage_traces as traces_mod
from datasheet_chart_digitizer.rdson_spec_table import parse_rdson_spec_rows

DS = Path("/Users/fab/dev/ee/solar-charger-eval/ds")
OUT_ROOT = Path(__file__).resolve().parents[1] / "out"
HAVE_DS = DS.is_dir()
HAVE_TESSERACT = shutil.which("tesseract") is not None
_CACHE: dict[str, list[dict]] = {}


def _results(name: str) -> list[dict]:
    if name not in _CACHE:
        OUT_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rdsvgs-review-", dir=OUT_ROOT) as tmp:
            _CACHE[name], _ = rgv.digitize_pdf(DS / f"{name}.pdf", Path(tmp))
    return _CACHE[name]


def _panel(name: str, page: int, diagram: str) -> dict:
    rows = [r for r in _results(name) if r["page"] == page and r["diagram"] == diagram]
    assert len(rows) == 1, [(r["page"], r["diagram"]) for r in _results(name)]
    return rows[0]


def _temperature(curve: dict):
    return curve.get("temperature_c", curve.get("tj_c"))


def _readout(curve: dict, vgs: float) -> dict:
    return next(r for r in curve["readouts"] if r["vgs_v"] == vgs)


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class ClippingTests(unittest.TestCase):
    """Finding 1: a segment crossing the frame lost its in-frame part."""

    def test_segment_crossing_the_top_frame_keeps_its_in_frame_part(self):
        # IRLB8743 p6, path 267: its first vertex (131.28, 161.76) is above the
        # frame top y=165.12; the next (136.08, 204.48) is inside.
        runs = traces_mod._clip_polyline(
            [(131.28, 161.76), (136.08, 204.48), (140.64, 222.24)],
            (126.18, 164.82, 292.62, 331.02),
        )
        self.assertEqual(len(runs), 1)
        self.assertAlmostEqual(runs[0][0][1], 164.82, places=2)
        self.assertAlmostEqual(runs[0][0][0], 131.28 + (164.82 - 161.76) / (204.48 - 161.76) * 4.8, places=2)

    def test_irlb8743_hot_curve_is_on_chart_at_3v3(self):
        row = _panel("IRLB8743_IFX", 6, "12")
        hot = next(c for c in row["curves"] if _temperature(c) == 125.0)
        reading = _readout(hot, 3.3)
        self.assertEqual(reading["status"], "read", reading)
        self.assertAlmostEqual(reading["rds_mohm"], 8.274, delta=0.12)

    def test_genuinely_absent_3v3_points_stay_absent(self):
        for name, page, diagram, starts in (
            ("IRLB8748_IFX", 6, "12", (3.4349, 3.4567)),
            ("CSD18502KCS_TI", 5, "4-7", (3.4190, 3.5029)),
        ):
            row = _panel(name, page, diagram)
            got = sorted(c["vgs_range_v"][0] for c in row["curves"])
            for curve in row["curves"]:
                self.assertEqual(_readout(curve, 3.3)["status"], "not_on_chart", name)
            # the fix recovers the in-frame start the reviewer measured
            for value, expected in zip(got, sorted(starts)):
                self.assertAlmostEqual(value, expected, delta=0.03, msg=name)


@unittest.skipUnless(HAVE_DS and HAVE_TESSERACT, "needs the datasheets and tesseract")
class LeaderTests(unittest.TestCase):
    """Finding 2: label leader lines served as curve data."""

    def test_rq6e080aj_serves_no_leader_values(self):
        # Source curve band at 2.10 / 2.20 V: 20.5 / 18.7 mOhm (+-1.4); the
        # 4.0 A label leader sits at ~26.3 mOhm.
        row = _panel("RQ6E080AJ_Rohm", 7, "12")
        for curve in row["curves"]:
            for vgs, rds in curve["points"]:
                if 2.05 <= vgs <= 2.30:
                    self.assertLess(rds, 22.5, (curve["curve_index"], vgs, rds))

    def test_rq3e180aj_has_no_leader_jog_and_keeps_its_knee(self):
        row = _panel("RQ3E180AJ_Rohm", 7, "12")
        for curve in row["curves"]:
            for vgs, rds in curve["points"]:
                self.assertFalse(1.37 <= vgs <= 1.45 and 29.5 <= rds <= 31.0, (curve["curve_index"], vgs, rds))
        # the knee between the steep branches and the tail is traced
        covered = [vgs for curve in row["curves"] for vgs, _ in curve["points"] if 1.8 <= vgs <= 2.2]
        self.assertGreater(len(covered), 20)


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class CoverageTests(unittest.TestCase):
    """Finding 3: traces stopping short of what the source draws."""

    def test_fdp8870_tails_reach_10v(self):
        # Filled outline pieces 716+717+718 and 719+720+721 are ONE curve each.
        row = _panel("FDP8870_onsemi", 5, "9")
        self.assertEqual(len(row["curves"]), 2)
        for curve in row["curves"]:
            vgs, rds = curve["points"][-1]
            self.assertGreater(vgs, 9.9)
            self.assertAlmostEqual(rds, 3.248, delta=0.06)


@unittest.skipUnless(HAVE_DS and HAVE_TESSERACT, "needs the datasheets and tesseract")
class RasterCoverageHonestyTests(unittest.TestCase):
    def test_wsr3090_does_not_claim_source_absence_where_it_lost_the_trace(self):
        # The source has the hot curves at 4.5 V (9.466 / 8.699 mOhm).
        row = _panel("WSR3090_LCSC_C719278", 3, "2")
        at_45 = [_readout(c, 4.5) for c in row["curves"]]
        read = sorted(r["rds_mohm"] for r in at_45 if r["status"] == "read")
        hot_values_present = any(abs(v - 9.466) < 0.25 for v in read) and any(abs(v - 8.699) < 0.25 for v in read)
        if not hot_values_present:
            self.assertFalse(any(r["status"] == "not_on_chart" for r in at_45), at_45)
            self.assertTrue(any("partial_raster_trace" in reason for reason in row["reasons"]), row["reasons"])


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class TemperatureKindTests(unittest.TestCase):
    """Finding 4: Tc / Ta / Tj were all stored as tj_c."""

    def test_ti_chart_prints_tc_and_the_table_ta(self):
        row = _panel("CSD17306Q5A_TI", 4, "7")
        self.assertEqual({c.get("temperature_kind") for c in row["curves"]}, {"Tc"})
        rows = parse_rdson_spec_rows(DS / "CSD17306Q5A_TI.pdf")
        self.assertTrue(all(getattr(r, "temperature_kind", None) == "Ta" for r in rows))
        for anchor in row["validation"]["anchors"]:
            self.assertIn("chart Tc taken as table Ta", " ".join(anchor.get("assumptions", [])))

    def test_ir_chart_and_table_both_print_tj_without_assumption(self):
        row = _panel("IRLB8748_IFX", 6, "12")
        self.assertEqual({c.get("temperature_kind") for c in row["curves"]}, {"Tj"})
        exact = [a for a in row["validation"]["anchors"] if a["row"]["vgs_v"] == 10.0][0]
        self.assertEqual(exact.get("assumptions"), [])


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class ValidationScopeTests(unittest.TestCase):
    """Finding 5: approximate-ID anchors made panels verified."""

    def test_approximate_current_alone_does_not_verify(self):
        only_45 = [r for r in parse_rdson_spec_rows(DS / "IRLB8748_IFX.pdf") if r.vgs_v == 4.5]
        OUT_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rdsvgs-review-", dir=OUT_ROOT) as tmp, patch.object(
            rgv, "parse_rdson_spec_rows", lambda pdf: only_45
        ):
            results, _ = rgv.digitize_pdf(DS / "IRLB8748_IFX.pdf", Path(tmp))
        row = results[0]
        self.assertNotEqual(row["validation"]["verdict"], "verified")
        self.assertNotEqual(row["status"], "ok")
        self.assertEqual(row["validation"]["anchors"][0].get("condition_match"), "approximate_drain_current")

    def test_malformed_max_cell_stays_null_and_is_reported(self):
        rows = {r.vgs_v: r for r in parse_rdson_spec_rows(DS / "CSD17302Q5A_TI.pdf")}
        self.assertIsNone(rows[3.0].max_mohm)
        self.assertEqual(getattr(rows[3.0], "unparsed_cells", {}).get("max"), "12..8")


if __name__ == "__main__":
    unittest.main()
