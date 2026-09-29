"""Known-bad cases from the independent Codex review of RDS(on)-vs-VGS batch 15.

Each test reproduces one confirmed finding on the real datasheet it was found
on (review: ~/dev/ee/solar-charger-eval/rds-vgs/codex-review/FINDINGS.md,
per-point measurements in scratch/review-20260928/REVIEW.md). The reference
values are the reviewer's independent measurements of the source PDFs, which
were re-checked against the PDFs before the fixes.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from datasheet_chart_digitizer import rdson_gate_voltage as rgv
from datasheet_chart_digitizer import rdson_gate_voltage_traces as traces_mod
from datasheet_chart_digitizer import rdson_gate_voltage_report as report
from datasheet_chart_digitizer.rdson_spec_table import parse_rdson_spec_rows

rgv_vector_traces = rgv.vector_traces

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


_CAPTURE: dict[str, dict] = {}


def _captured(name: str) -> dict:
    """Run a part with `_curves` and `readouts` instrumented.

    Returns {(page, diagram): {"traces", "calibration", "scale", "gray",
    "readout_calls"}}: the REAL inputs of `_curves` for each panel, so a test
    can re-run it on real traces with one real feature changed, and the gap
    lists the production code handed to `readouts`. Uses the module
    attributes, so a mutation harness patch on either is honoured.
    """
    if name not in _CAPTURE:
        panels: dict = {}
        current: dict = {}
        real_curves, real_readouts = rgv._curves, rgv.readouts

        def curves_spy(traces, calibration, scale, gray=None):
            current.clear()
            current.update({"traces": traces, "calibration": calibration, "scale": scale, "gray": gray,
                            "readout_calls": []})
            result = real_curves(traces, calibration, scale, gray)
            panels[len(panels)] = dict(current)
            return result

        def readouts_spy(points, log_y, gaps=None, *args, **kwargs):
            if "readout_calls" in current:
                current["readout_calls"].append({"first_vgs": points[0][0] if len(points) else None,
                                                 "gaps": [list(g) for g in gaps or []]})
            return real_readouts(points, log_y, gaps, *args, **kwargs)

        OUT_ROOT.mkdir(exist_ok=True)
        with patch.object(rgv, "_curves", curves_spy), patch.object(rgv, "readouts", readouts_spy):
            with tempfile.TemporaryDirectory(prefix="rdsvgs-capture-", dir=OUT_ROOT) as tmp:
                rows, _ = rgv.digitize_pdf(DS / f"{name}.pdf", Path(tmp))
        keyed = {}
        for index, row in enumerate(r for r in rows if "curves" in r):
            keyed[(row["page"], row["diagram"])] = dict(panels[index], row=row)
        _CAPTURE[name] = keyed
    return _CAPTURE[name]


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
    """Finding 2: label leader lines served as curve data.

    Each test asserts the REAL curve points are present (reviewer-measured
    values) AND the leader points absent, so an empty result cannot pass
    (review R2-2).
    """

    def _usable_value_at(self, row, vgs):
        values = []
        for curve in row["curves"]:
            if not curve.get("usable", True):
                continue
            pts = curve["points"]
            near = [r for v, r in pts if abs(v - vgs) <= 0.01]
            if near:
                values.append(near[0])
        return values

    def test_rq6e080aj_serves_the_curve_and_no_leader_values(self):
        # Source curve at 2.10 / 2.20 V: 20.5 / 18.7 mOhm (reviewer); the
        # 4.0 A label leader sits at ~26.3 mOhm.
        row = _panel("RQ6E080AJ_Rohm", 7, "12")
        for vgs, expected in ((2.10, 20.5), (2.20, 18.7)):
            values = self._usable_value_at(row, vgs)
            self.assertTrue(any(abs(v - expected) <= 1.5 for v in values), (vgs, values))
        for curve in row["curves"]:
            for vgs, rds in curve["points"]:
                if 2.05 <= vgs <= 2.30:
                    self.assertLess(rds, 22.5, (curve["curve_index"], vgs, rds))

    def test_rq3e180aj_serves_the_18a_branch_and_knee_and_no_leader_jog(self):
        row = _panel("RQ3E180AJ_Rohm", 7, "12")
        # reviewer: 18 A branch 13.86 mOhm at 1.5495 V, 11.65 at 1.5999 V; knee
        # continuous to the tail (2.5005 V: 4.245)
        for vgs, expected, tol in ((1.5495, 13.86, 1.2), (1.5999, 11.65, 1.0), (2.0, 5.5, 1.5), (2.5005, 4.245, 0.3)):
            values = self._usable_value_at(row, vgs)
            self.assertTrue(any(abs(v - expected) <= tol for v in values), (vgs, values))
        for curve in row["curves"]:
            for vgs, rds in curve["points"]:
                # Opus B: six leader points, 1.364-1.431 V at ~30.3 mOhm
                self.assertFalse(1.36 <= vgs <= 1.45 and 29.5 <= rds <= 31.0, (curve["curve_index"], vgs, rds))
            # Round 3: after the stub reordering the 9 A branch carried the
            # whole 9 A leader again, a flat 30.3-30.6 mOhm run 1.37-1.84 V.
            # The branches cross 30 mOhm steeply (one or two points); a flat
            # run there, >= 0.1 V long within 1 mOhm, is the leader.
            flat = [(v, r) for v, r in curve["points"] if 29.5 <= r <= 31.0]
            for i in range(len(flat)):
                run = [(v, r) for v, r in flat if flat[i][0] <= v <= flat[i][0] + 0.1]
                if len(run) >= 5 and run[-1][0] - run[0][0] >= 0.09:
                    self.assertGreater(max(r for _v, r in run) - min(r for _v, r in run), 1.0,
                                       (curve["curve_index"], run[:3]))

    def test_rq6e080aj_keeps_the_8a_steep_branch(self):
        # Review R2-3: v1 served 9 points 1.754-1.865 V, 39.8-29.0 mOhm on the
        # right (8 A) steep line, checked on the crop's pixels (the column run
        # at each point is the right line's ink). v2 dropped them with the
        # leader; they must be served again, and only them -- no leader tail.
        row = _panel("RQ6E080AJ_Rohm", 7, "12")
        branch = [
            (v, r) for curve in row["curves"] for v, r in curve["points"]
            if 1.75 <= v <= 1.87 and 29.0 <= r <= 40.0
        ]
        self.assertGreaterEqual(len(branch), 6, branch)
        for vgs, expected in ((1.7956, 36.90), (1.8370, 33.09)):
            self.assertTrue(any(abs(v - vgs) < 0.008 and abs(r - expected) < 1.5 for v, r in branch), (vgs, branch))


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class GapTests(unittest.TestCase):
    """Opus A: trace gaps were bridged by straight chords and could be read across."""

    GAP_REASONS = ("_gaps (", "_untraced_sections (", "_annotation_contacts (")

    def _check_gaps_explicit(self, row):
        for curve in row["curves"]:
            px = [p[0] for p in curve["points_px"]]
            vgs = [p[0] for p in curve["points"]]
            gaps = [list(g) for g in curve.get("gaps", [])]
            jumps = [[round(vgs[i], 4), round(vgs[i + 1], 4)] for i in range(len(px) - 1) if px[i + 1] - px[i] > 2.5]
            # every pixel jump is a listed gap ...
            def listed(interval, among):
                return any(abs(interval[0] - g[0]) < 2e-4 and abs(interval[1] - g[1]) < 2e-4 for g in among)

            for jump in jumps:
                self.assertTrue(listed(jump, gaps), (row["part"], curve["curve_index"], jump, gaps))
            # ... and every listed gap is a jump or an annotation contact,
            # which opens an interval however close its neighbours (R3-2)
            contacts = [list(g) for g in curve.get("gap_kinds", {}).get("annotation_contact", [])]
            for gap in gaps:
                self.assertTrue(listed(gap, jumps) or listed(gap, contacts), (row["part"], curve["curve_index"], gap))
            for reading in curve["readouts"]:
                for g0, g1 in curve.get("gaps", []):
                    if g0 < reading["vgs_v"] < g1:
                        self.assertNotEqual(reading["status"], "read", reading)

    @unittest.skipUnless(HAVE_TESSERACT, "needs tesseract")
    def test_raster_gaps_are_explicit_and_never_read_across(self):
        found_any = False
        for name, page, diagram in (("BRCS020N03RA_LCSC_C22449012", 4, "5"), ("RQ3E180AJ_Rohm", 7, "12"),
                                    ("RQ6E080AJ_Rohm", 7, "12"), ("WSR3090_LCSC_C719278", 3, "2")):
            row = _panel(name, page, diagram)
            self._check_gaps_explicit(row)
            if any(c.get("gaps") for c in row["curves"]):
                found_any = True
                self.assertNotEqual(row["status"], "ok")
                self.assertTrue(any(k in r for r in row["reasons"] for k in self.GAP_REASONS), row["reasons"])
        self.assertTrue(found_any)

    def test_a_readout_inside_a_gap_is_refused(self):
        # Real CSD17306Q5A 25 C curve with every point between 3.0 and 4.0 V
        # removed: 3.3 V lies inside the gap and must NOT be interpolated
        # (review R2-2: a mutant that ignores gaps read 6.8 mOhm here).
        row = _panel("CSD17306Q5A_TI", 4, "7")
        curve = next(c for c in row["curves"] if _temperature(c) == 25.0)
        points = [(v, r) for v, r in curve["points"] if not 3.0 < v < 4.0]
        gap = [max(v for v, _ in points if v <= 3.0), min(v for v, _ in points if v >= 4.0)]
        got = {r["vgs_v"]: r for r in report.readouts(points, False, [gap])}
        self.assertEqual(got[3.3]["status"], "not_in_extracted_trace", got[3.3])
        self.assertIsNone(got[3.3]["rds_mohm"])
        self.assertEqual(got[4.5]["status"], "read")

    @unittest.skipUnless(HAVE_TESSERACT, "needs tesseract")
    def test_rq6e080aj_has_no_chord_across_the_steep_branch(self):
        # v1 curve 1 chorded from (1.865 V, 29.0) to (2.03 V, 26.2) where the
        # curve is ~22 mOhm. The real curve falls from ~30 mOhm at 1.88 V to
        # 18.7 at 2.20 V (reviewer); it must be served there, and every served
        # point must lie at most 1 mOhm above the straight line through those
        # two measured points (which lies above this convex curve).
        row = _panel("RQ6E080AJ_Rohm", 7, "12")
        served = [(v, r) for c in row["curves"] if c.get("usable", True) for v, r in c["points"] if 1.95 <= v <= 2.25]
        self.assertGreater(len(served), 15)
        for curve in row["curves"]:
            for vgs, rds in curve["points"]:
                if 1.95 <= vgs <= 2.25:
                    ceiling = 30.0 + (18.7 - 30.0) * (vgs - 1.88) / (2.20 - 1.88) + 1.0
                    self.assertLessEqual(rds, ceiling, (curve["curve_index"], vgs, rds))

    def test_a_gap_injected_into_a_vector_curve_blocks_ok_and_its_readout(self):
        def gapped(page, transform, plot):
            found, swatches, leaders = rgv_vector_traces(page, transform, plot)
            for trace in found:
                # remove 3.0-4.0 V (crop pixels via the TI x axis: 0 V at 242, 10 V at 1042)
                lo, hi = 242 + 3.0 * 80.0, 242 + 4.0 * 80.0
                trace.points_px = [p for p in trace.points_px if not lo < p[0] < hi]
            return found, swatches, leaders

        OUT_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rdsvgs-review-", dir=OUT_ROOT) as tmp, patch.object(
            rgv, "vector_traces", gapped
        ):
            results, _ = rgv.digitize_pdf(DS / "CSD17306Q5A_TI.pdf", Path(tmp))
        row = next(r for r in results if r["diagram"] == "7")
        self.assertNotEqual(row["status"], "ok")
        self.assertTrue(any("_gaps (" in r for r in row["reasons"]), row["reasons"])
        for curve in row["curves"]:
            self.assertEqual(len(curve["gaps"]), 1)
            self.assertEqual(_readout(curve, 3.3)["status"], "not_in_extracted_trace")
            self.assertEqual(_readout(curve, 4.5)["status"], "read")


@unittest.skipUnless(HAVE_DS and HAVE_TESSERACT, "needs the datasheets and tesseract")
class UsableFlagTests(unittest.TestCase):
    """Opus E: fragments with false points must be flagged, not served silently."""

    def _fragment_row(self, v0, v1):
        # The REAL RQ6E080AJ main curve (c2, 1.88-9.96 V), cut to v0..v1 V:
        # both ends inside the plot and neither end's ink running to the
        # frame (the curve is flat there), i.e. an unattributable fragment.
        cap = _captured("RQ6E080AJ_Rohm")[(7, "12")]
        cal = cap["calibration"]
        main = max(cap["traces"], key=lambda t: len(t.points_px))
        kept = [p for p in main.points_px if v0 <= cal.x_axis.value(p[0]) <= v1]
        piece = traces_mod.Trace(kept, "raster", None, 0)
        curves, reasons, _ = rgv._curves([piece], cal, cap["scale"], cap["gray"])
        return curves[0], reasons

    def test_short_fragments_open_at_both_ends_are_not_usable(self):
        # 4.0-6.9 V is 29 % of the 0-10 V axis: under the 30 % limit
        curve, reasons = self._fragment_row(4.0, 6.9)
        self.assertFalse(curve["trace_complete"]["left_ink_reaches_frame"])
        self.assertFalse(curve["trace_complete"]["right_ink_reaches_frame"])
        self.assertIs(curve.get("usable"), False)
        self.assertTrue(all(r["status"] == "curve_not_usable" for r in curve["readouts"]))
        self.assertIn("curve_0_not_usable (fragment)", reasons)

    def test_a_fragment_over_the_span_limit_is_usable(self):
        # 4.0-7.1 V is 31 %: the same real ink, now over the limit
        curve, _reasons = self._fragment_row(4.0, 7.1)
        self.assertIs(curve.get("usable"), True)
        self.assertEqual(_readout(curve, 4.5)["status"], "read")


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class SteepTopTests(unittest.TestCase):
    """Opus D: steep tops were cut where the path's first vertex lay outside the frame."""

    def test_vector_curves_reach_the_top_of_the_plot(self):
        # y-axis tops: CSD17306 16 mOhm, IRLB8748 18 mOhm, SIS176LDN 50 mOhm
        for name, page, diagram, top in (("CSD17306Q5A_TI", 4, "7", 16.0), ("IRLB8748_IFX", 6, "12", 18.0),
                                         ("SIS176LDN_Vishay", 4, "t491", 50.0)):
            row = _panel(name, page, diagram)
            for curve in row["curves"]:
                self.assertGreater(max(r for _v, r in curve["points"]), 0.97 * top, (name, curve["curve_index"]))


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
    def test_wsr3090_serves_the_hot_curves_and_does_not_claim_absence(self):
        # The source has the hot curves at 4.5 V (9.466 / 8.699 mOhm) and the
        # cold one at 6.461 (reviewer). Curves that start inside the plot say
        # not_in_extracted_trace to their left, never not_on_chart.
        row = _panel("WSR3090_LCSC_C719278", 3, "2")
        read = sorted(_readout(c, 4.5)["rds_mohm"] for c in row["curves"] if _readout(c, 4.5)["status"] == "read")
        for expected in (9.466, 8.699, 6.461):
            self.assertTrue(any(abs(v - expected) < 0.1 for v in read), (expected, read))
        for curve in row["curves"]:
            if not curve["trace_complete"]["left_end_at_frame"]:
                self.assertEqual(_readout(curve, 3.3)["status"], "not_in_extracted_trace")
        self.assertTrue(any("partial_raster_trace" in reason for reason in row["reasons"]))
        # three printed curves; un-OCRed label ink must not add a fourth
        self.assertEqual(len(row["curves"]), 3)


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



@unittest.skipUnless(HAVE_DS and HAVE_TESSERACT, "needs the datasheets and tesseract")
class RoundTwoTests(unittest.TestCase):
    """Review round 2 (R2-1, R2-4..R2-8), each on the real case it was found on."""

    def test_r2_1_single_curve_keeps_the_printed_kind(self):
        # RQ3E110AJ prints "Ta=25C"; OCR reads "T.=25C" -> the kind is kept as
        # "T (subscript unread)", not dropped to null.
        row = _panel("RQ3E110AJ_Rohm", 7, "12")
        self.assertEqual({c["temperature_kind"] for c in row["curves"]}, {"T (subscript unread)"})

    def test_r2_1_missing_temperature_is_a_reason(self):
        row = _panel("RQ3E180AJ_Rohm", 7, "12")
        for curve in row["curves"]:
            self.assertIsNone(curve.get("temperature_c"))
            self.assertTrue(
                any(r.startswith(f"curve_{curve['curve_index']}_temperature_c_unknown") for r in row["reasons"]),
                row["reasons"],
            )

    def test_r2_4_legend_distinguishes_readout_states(self):
        row = _panel("WSR3090_LCSC_C719278", 3, "2")
        # every WSR3090 curve starts inside the plot at 3.38 V: 3.3 V is
        # not_in_extracted_trace and must be shown as such, not as "n/c"
        states = {_readout(c, 3.3)["status"] for c in row["curves"]}
        self.assertEqual(states, {"not_in_extracted_trace"})
        lines = [t for t, _c in report._legend_lines(row) if t.startswith("c")]
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertIn("3.3V:not traced", line)
            self.assertNotIn("3.3V:n/c", line)

    def test_r2_5_header_shows_every_reason(self):
        row = _panel("WSR3090_LCSC_C719278", 3, "2")
        panel = type("P", (), {"part": row["part"], "page": row["page"], "diagram": row["diagram"], "title": row["title"]})
        header = " ".join(t for t, _c in report._header_lines(row, panel)).split()
        header = " " + " ".join(header) + " "
        self.assertGreater(len(row["reasons"]), 4)
        for reason in row["reasons"]:
            self.assertIn(" " + " ".join(reason.split()) + " ", header)

    def test_r2_6_fdp8870_frame_sits_on_the_printed_frame(self):
        # printed frame and 2 V rule: filled rule at 253-255 px (reviewer)
        row = _panel("FDP8870_onsemi", 5, "9")
        self.assertLessEqual(abs(row["plot_box_px"]["x0"] - 254), 2, row["plot_box_px"])
        ticks = row["calibration"]["x_axis"]["ticks"]
        self.assertEqual((ticks[0]["value"], round(ticks[0]["pixel"])), (2.0, 254))

    def test_r2_7_wsr3090_points_are_not_pulled_onto_the_arrows(self):
        # c0 was +3.5 px at the 125 C arrowhead (7.98-8.08 V), c1 -5 px on the
        # arrow shaft at 8.33-8.37 V. At 65.4 px/mOhm those are >= 0.05 mOhm;
        # any served point there must lie within 0.05 mOhm (3.3 px) of the
        # chord between the curve's own points 0.12 V either side.
        row = _panel("WSR3090_LCSC_C719278", 3, "2")
        checked = 0
        for curve in row["curves"]:
            pts = curve["points"]
            for vgs, rds in pts:
                if 7.95 <= vgs <= 8.10 or 8.30 <= vgs <= 8.40:
                    left = [(v, r) for v, r in pts if vgs - 0.14 <= v <= vgs - 0.10]
                    right = [(v, r) for v, r in pts if vgs + 0.10 <= v <= vgs + 0.14]
                    if not left or not right:
                        continue
                    (v0, r0), (v1, r1) = left[0], right[-1]
                    expected = r0 + (r1 - r0) * (vgs - v0) / (v1 - v0)
                    self.assertLess(abs(rds - expected), 0.05, (curve["curve_index"], vgs, rds, expected))
                    checked += 1
        self.assertGreater(checked, 10)

    def test_r2_8_continuous_ink_is_not_called_a_gap(self):
        # v2 reported "gap 1.94-2.05 V": one edge point at 1.94 V, then the
        # near-vertical double line the column tracker does not sample. The ink
        # is continuous (reviewer pixel dump), so no stretch there may be
        # called a gap. Since F4-1 the steep heads are traced row by row up to
        # the top frame, so neither curve is partial at its head any more.
        row = _panel("RQ3E110AJ_Rohm", 7, "12")
        for curve in row["curves"]:
            self.assertEqual(curve["gap_kinds"]["gap"], [])
            self.assertTrue(curve["trace_complete"]["left_end_at_frame"], curve["trace_complete"])
        self.assertFalse(any("_gaps (" in r for r in row["reasons"]), row["reasons"])

    def test_r2_8_unsampled_stretch_on_continuous_ink_is_an_untraced_section(self):
        # WSR3090 curve 2: 7.56-7.78 V, where the 25 C curve runs along the
        # 5 mOhm rule; the ink is continuous, so it is an untraced section.
        row = _panel("WSR3090_LCSC_C719278", 3, "2")
        kinds = [c["gap_kinds"] for c in row["curves"]]
        self.assertTrue(any(any(g0 < 7.6 < g1 for g0, g1 in k["untraced_section"]) for k in kinds), kinds)


@unittest.skipUnless(HAVE_DS and HAVE_TESSERACT, "needs the datasheets and tesseract")
class RoundThreeTests(unittest.TestCase):
    """Round-3 findings (Codex R3-1/R3-2, Opus N3-1..N3-5), each on its real case."""

    # -- R3-1: the three mutants that survived round 2 -----------------------

    def _csd_curve(self):
        row = next(r for r in _results("CSD17306Q5A_TI") if r.get("curves"))
        curve = min(row["curves"], key=lambda c: c["curve_index"])
        return row, curve

    def test_r3_1a_readouts_refuse_the_interior_edges_of_an_unread_interval(self):
        # Real CSD17306Q5A 25 C points around 3.3 V; the interval between the
        # sampled point just below 3.3 V and the second point above it is
        # declared unread. Targets a quarter pixel inside either bound are
        # inside it and must be refused; the bounds themselves are measured
        # points and are read; a quarter pixel outside is read.
        row, curve = self._csd_curve()
        one_px = abs(row["calibration"]["x_axis"]["m"])
        pts = [tuple(p) for p in curve["points"]]
        i = max(k for k, (v, _r) in enumerate(pts) if v <= 3.3)
        g0, g1 = pts[i][0], pts[i + 2][0]
        self.assertLess(g1 - g0, 3 * one_px)
        served = pts[: i + 1] + pts[i + 2:]
        inside = (g0 + 0.25 * one_px, g1 - 0.25 * one_px)
        outside = (g0 - 0.25 * one_px, g1 + 0.25 * one_px)
        got = report.readouts(served, False, [(g0, g1)], one_px, targets=inside + (g0, g1) + outside)
        self.assertEqual([r["status"] for r in got[:2]], ["not_in_extracted_trace"] * 2, got[:2])
        self.assertEqual([r["status"] for r in got[2:]], ["read"] * 4, got[2:])
        self.assertAlmostEqual(got[2]["rds_mohm"], pts[i][1], places=3)

    def test_r3_1b_wsr3090_arrow_contacts_are_typed_and_cover_every_removal(self):
        row = _panel("WSR3090_LCSC_C719278", 3, "2")
        removed_total = 0
        for curve in row["curves"]:
            contacts = curve["gap_kinds"]["annotation_contact"]
            removed = curve["annotation_contact_removed_vgs_v"]
            removed_total += len(removed)
            self.assertEqual(len(removed), curve["points_removed_as_annotation_contact"])
            for vgs in removed:
                self.assertTrue(any(g0 < vgs < g1 for g0, g1 in contacts), (curve["curve_index"], vgs, contacts))
                self.assertFalse(any(g0 < vgs < g1 for g0, g1 in curve["gap_kinds"]["gap"]))
            if removed:
                self.assertTrue(any(r.startswith(f"curve_{curve['curve_index']}_annotation_contacts (")
                                    for r in row["reasons"]), row["reasons"])
        # reviewer: c0 at 7.98-8.08 V on the arrowhead, c1 at 8.34-8.37 and
        # 8.44-8.52 V on the shaft
        by_index = {c["curve_index"]: c["gap_kinds"]["annotation_contact"] for c in row["curves"]}
        for index, vgs in ((0, 7.98), (0, 8.05), (1, 8.35), (1, 8.44192), (1, 8.50)):
            self.assertTrue(any(g0 < vgs < g1 for g0, g1 in by_index[index]), (index, vgs, by_index[index]))
        self.assertGreaterEqual(removed_total, 18)

    def test_r3_1b_contact_removal_touches_only_the_wsr3090_arrows(self):
        # Opus round 3: no annotation-contact removal on any other raster panel
        for name, page, diagram in (("RQ3E110AJ_Rohm", 7, "12"), ("RQ6E080AJ_Rohm", 7, "12"),
                                    ("RQ3E180AJ_Rohm", 7, "12"), ("BRCS020N03RA_LCSC_C22449012", 4, "5")):
            for curve in _panel(name, page, diagram)["curves"]:
                self.assertEqual(curve["points_removed_as_annotation_contact"], 0, (name, curve["curve_index"]))
        # and on WSR3090 only the points the reviewer measured as pulled by
        # the arrow (az_wsr_contacts.txt), +-1 px (0.0096 V): c0 7.98-8.08 V
        # on the arrowhead; c1 8.34-8.37 V (shaft above) and 8.44-8.52 V
        # (shaft below). Their neighbours sit in the curve band and stay.
        pulled = {0: [(7.98, 8.08)], 1: [(8.34, 8.37), (8.44, 8.52)]}
        for curve in _panel("WSR3090_LCSC_C719278", 3, "2")["curves"]:
            for vgs in curve["annotation_contact_removed_vgs_v"]:
                self.assertTrue(any(lo - 0.0096 <= vgs <= hi + 0.0096 for lo, hi in pulled.get(curve["curve_index"], [])),
                                (curve["curve_index"], vgs))

    def test_r3_1c_production_readouts_receive_every_unread_interval(self):
        cap = _captured("WSR3090_LCSC_C719278")[(3, "2")]
        row = cap["row"]
        self.assertEqual(len(cap["readout_calls"]), len(row["curves"]))
        for curve, call in zip(row["curves"], cap["readout_calls"]):
            every = sorted(g for spans in curve["gap_kinds"].values() for g in spans)
            self.assertTrue(curve["gap_kinds"]["untraced_section"] or curve["gap_kinds"]["annotation_contact"])
            self.assertEqual(sorted(call["gaps"]), [list(g) for g in every], curve["curve_index"])

    def test_r3_1c_a_readout_inside_an_untraced_section_is_refused(self):
        # Codex round 3: the real WSR3090 curves with every sample 4.40-4.60 V
        # removed. The ink there is continuous, so the stretch is an
        # untraced_section -- and 4.5 V inside it must not be read.
        cap = _captured("WSR3090_LCSC_C719278")[(3, "2")]
        cal = cap["calibration"]
        cut = []
        for trace in cap["traces"]:
            kept = [p for p in trace.points_px if not 4.40 <= cal.x_axis.value(p[0]) <= 4.60]
            cut.append(traces_mod.Trace(kept, trace.method, trace.style, trace.merged_columns, dict(trace.params),
                                        dict(trace.binding), trace.bridged_columns, list(trace.contact_removed_x)))
        curves, reasons, _ = rgv._curves(cut, cal, cap["scale"], cap["gray"])
        for curve in curves:
            self.assertTrue(any(g0 < 4.5 < g1 for g0, g1 in curve["gap_kinds"]["untraced_section"]), curve["gap_kinds"])
            self.assertEqual(_readout(curve, 4.5)["status"], "not_in_extracted_trace")

    # -- R3-2 -------------------------------------------------------------------

    def test_r3_2_contact_between_close_neighbours_opens_an_unread_interval(self):
        # Codex: c1's 8.44192 V point was removed; its neighbours (814 and
        # 816 px) are 2 px apart, so v3 listed nothing and read 6.6812 there.
        row = _panel("WSR3090_LCSC_C719278", 3, "2")
        curve = next(c for c in row["curves"] if any(abs(v - 8.44192) < 0.002 for v in c["annotation_contact_removed_vgs_v"]))
        self.assertTrue(any(g0 < 8.44192 < g1 for g0, g1 in curve["gap_kinds"]["annotation_contact"]))
        one_px = abs(row["calibration"]["x_axis"]["m"])
        got = report.readouts(curve["points"], False, curve["gaps"], one_px, targets=(8.44192,))[0]
        self.assertEqual(got["status"], "not_in_extracted_trace", got)
        # and the overlay does not draw through it
        pts = __import__("numpy").asarray(curve["points_px"], dtype=float)
        i = max(k for k, (v, _r) in enumerate(curve["points"]) if v < 8.44192)
        self.assertLessEqual(pts[i + 1, 0] - pts[i, 0], 2.5)
        self.assertTrue(report._breaks_between(curve, i, pts))

    # -- R3-3 / N3-1 ------------------------------------------------------------

    def test_r3_3_every_admitted_raster_track_passes_on_its_own_points(self):
        # v3 admitted two RQ3E180AJ tracks (n=5 and n=6) only through a final
        # point on the 30.3 mOhm leader junction that the stub rule removed
        # afterwards; the residues (4 and 5 points) failed the admission rule.
        for name, page, diagram in (("RQ3E180AJ_Rohm", 7, "12"), ("RQ6E080AJ_Rohm", 7, "12"),
                                    ("RQ3E110AJ_Rohm", 7, "12")):
            cap = _captured(name)[(page, diagram)]
            plot = cap["calibration"].plot
            width, height = plot.x1 - plot.x0, plot.y1 - plot.y0
            for trace in cap["traces"]:
                own = trace.points_px
                self.assertTrue(traces_mod._long_enough({"points": own}, width, height), (name, own[:3], len(own)))
                self.assertGreaterEqual(len(own), 5, (name, own[:3]))

    def test_r3_3_the_v3_leader_junction_track_is_not_admitted(self):
        # The real v3 RQ3E180AJ 9 A top: four points on the branch (v3 CSV
        # c0, reviewer: 4/4 on ink) plus its final point on the 30.3 mOhm
        # leader junction at (446, 362.5) (reviewer's orphan_probe). With the
        # junction it spans 236 px >= 197 px (25 % of the height) and v3
        # admitted it; without it, 40 px. Stubs go first, so it is refused.
        cap = _captured("RQ3E180AJ_Rohm")[(7, "12")]
        plot = cap["calibration"].plot
        points = [(434.0, 132.0), (436.0, 127.0), (437.0, 145.0), (438.0, 167.0), (446.0, 362.5)]
        track = {"points": list(points), "x": 446, "y": 362.5, "run": (361, 364), "slope": 0.0, "merged": 0}
        width, height = cap["gray"].shape[1], plot.y1 - plot.y0
        self.assertTrue(traces_mod._long_enough({"points": points}, width, height))
        kept = traces_mod._admit_tracks([track], __import__("numpy").zeros(width, dtype=bool), width, height, plot)
        self.assertEqual(kept, [])
        self.assertEqual(track["dropped_stubs"], [(446.0, 362.5)])

    def test_r3_3_steep_admission_needs_five_points(self):
        # The real RQ6E080AJ 8 A branch (9 points, 208 px tall: the reviewer
        # confirmed all on the right line's ink), thinned to 5 and to 4 of its
        # own points, same height: 5 are a branch, 4 are not.
        cap = _captured("RQ6E080AJ_Rohm")[(7, "12")]
        plot = cap["calibration"].plot
        # the branch as v3 served it (golden fixture; since F4-4 it is part of
        # the 8 A curve, so it is read from the frozen v3 points)
        fixture = Path(__file__).resolve().parent / "fixtures" / "rds_vgs_golden" / "RQ6E080AJ_Rohm" / "points_c0.csv"
        branch = [tuple(float(v) for v in line.split(",")[2:4]) for line in fixture.read_text().splitlines()[1:]]
        self.assertEqual(len(branch), 9)
        width, height = cap["gray"].shape[1], plot.y1 - plot.y0
        five = [branch[i] for i in (0, 2, 4, 6, 8)]
        four = [branch[i] for i in (0, 3, 5, 8)]
        self.assertTrue(traces_mod._long_enough({"points": five}, width, height))
        self.assertFalse(traces_mod._long_enough({"points": four}, width, height))

    # -- R3-4 / N3-2 ------------------------------------------------------------

    def test_r3_4_a_steep_branch_is_not_erased_as_a_grid_rule(self):
        # Opus: x=460 px (the 18 A branch, dark in 48 of 380 lower rows) was
        # erased as a rule next to the real 1.5 V rule at 466 px.
        row = _panel("RQ3E180AJ_Rohm", 7, "12")
        grid = row["raster_grid_rules_px"]
        self.assertTrue(any(abs(x - 460) <= 1 for x in grid["x_refused_not_a_rule"]), grid)
        self.assertFalse(any(abs(x - 460) <= 1 for x in grid["x_erased"]), grid)
        # the real rules stay erased, including 4.5 V (x=913), whose ink is
        # hidden behind the legend box for 12 % of its height
        for rule in (317, 393, 466, 541, 617, 690, 764, 838, 913):
            self.assertTrue(any(abs(x - rule) <= 1 for x in grid["x_erased"]), (rule, grid))
        # the 18 A branch is served continuously through 460 px, from its top
        # (48 mOhm) through the 36.9-29.6 mOhm stretch v3 left untraced
        branch = next(c for c in row["curves"] if c["points"] and abs(c["points"][0][0] - 1.418) < 0.01)
        on_branch = [(v, r) for v, r in branch["points"] if 1.418 <= v <= 1.47]
        self.assertTrue(any(29.6 <= r <= 36.9 for _v, r in on_branch), on_branch)
        self.assertFalse(any(g0 < 1.45 < g1 for g0, g1 in branch["gaps"]), branch["gaps"])
        # the tail readout is unchanged (reviewer ink 3.36, v3 3.3947)
        tail = max(row["curves"], key=lambda c: c["n_points"])
        self.assertAlmostEqual(_readout(tail, 4.5)["rds_mohm"], 3.39, delta=0.04)

    def test_r3_4_rule_verification_on_the_real_pixels(self):
        cap = _captured("RQ3E180AJ_Rohm")[(7, "12")]
        cal, gray = cap["calibration"], cap["gray"]
        ticks = [t.pixel for t in cal.x_axis.ticks]
        kept = rgv._verified_rules(gray, (317.0, 393.0, 460.0, 466.0, 541.0, 913.0, 988.0), cal.plot, ticks,
                                   vertical=True)
        self.assertNotIn(460.0, kept)
        for rule in (317.0, 393.0, 466.0, 541.0, 913.0):
            self.assertIn(rule, kept)

    # -- R3-5 / N3-3 ------------------------------------------------------------

    def test_r3_5_dropped_stub_points_are_recorded_and_are_on_ink(self):
        for name, vgs, rds, x, y in (("RQ3E180AJ_Rohm", 1.29, 40.0, 436, 209.5), ("RQ3E110AJ_Rohm", 1.94, 24.05, 384, 206)):
            cap = _captured(name)[(7, "12")]
            row, gray = cap["row"], cap["gray"]
            stubs = [s for c in row["curves"] for s in c["dropped_end_stub_points"]]
            match = [s for s in stubs if abs(s[2] - x) <= 1 and abs(s[3] - y) <= 1.5]
            self.assertTrue(match, (name, stubs))
            # values are the reviewer's, read with the v3 calibration; R3-9's
            # full-ladder refit moved RQ3E110AJ's x fit by <= 0.92 px (0.013 V)
            self.assertAlmostEqual(match[0][0], vgs, delta=0.015)
            self.assertAlmostEqual(match[0][1], rds, delta=0.3)
            self.assertLess(int(gray[int(round(y)) - 2:int(round(y)) + 3, x - 2:x + 3].min()), 150)
            self.assertTrue(any("_end_stub_points_dropped (" in r for r in row["reasons"]), row["reasons"])

    def test_r3_5_stub_length_and_remainder_boundaries(self):
        # Real RQ3E180AJ 9 A branch samples (its first 12 points), with a
        # lost stretch opened after the first k points by deleting 4 of them:
        # a stub of 1-2 points is dropped if >= 3 points remain; 3 points
        # are not a stub; a remainder of 2 is not enough to drop against.
        cap = _captured("RQ3E180AJ_Rohm")[(7, "12")]
        trace = next(t for t in cap["traces"] if t.points_px[0][0] < 445)
        rows = set(map(tuple, trace.row_traced_points))
        branch = [p for p in trace.points_px if tuple(p) not in rows]   # the column samples
        erased = __import__("numpy").zeros(cap["gray"].shape[1], dtype=bool)

        def dropped(k, rest):
            points = list(branch[:k]) + list(branch[k + 4:k + 4 + rest])
            return len(traces_mod._drop_orphan_ends(points, erased)[1])

        self.assertEqual(dropped(1, 8), 1)
        self.assertEqual(dropped(2, 8), 2)
        self.assertEqual(dropped(3, 8), 0)
        self.assertEqual(dropped(1, 3), 1)
        self.assertEqual(dropped(1, 2), 0)

    # -- R3-6 / N3-4 ------------------------------------------------------------

    def test_r3_6_twin_branches_get_the_same_flag_from_their_ink(self):
        # both RQ6E080AJ branches reach the top frame; since F4-4 each is a
        # whole curve (its branch + the shared tail), and both are usable
        row = _panel("RQ6E080AJ_Rohm", 7, "12")
        self.assertEqual(len(row["curves"]), 2, [c["vgs_range_v"] for c in row["curves"]])
        self.assertEqual({c["usable"] for c in row["curves"]}, {True})
        for curve in row["curves"]:
            self.assertTrue(curve["trace_complete"]["left_end_at_frame"], curve["trace_complete"])
        self.assertFalse(any("both ends inside the plot" in r for r in row["reasons"]))

    # -- R3-7 / N3-5 ------------------------------------------------------------

    def test_r3_7_overlay_text_is_word_wrapped_and_never_clipped(self):
        row = _panel("RQ3E180AJ_Rohm", 7, "12")
        panel = type("P", (), {"part": row["part"], "page": row["page"], "diagram": row["diagram"], "title": row["title"]})
        width = 700
        for lines in (report._header_lines(row, panel, width), report._legend_lines(row, width)):
            for text, _c in lines:
                if len(text.split()) > 1:
                    self.assertLessEqual(report._text_px(text), width, text)
        joined = " ".join(" ".join(t.split()) for t, _c in report._header_lines(row, panel, width))
        self.assertIn(" ".join(row["calibration"]["tick_source"].split()), joined)
        # every token of every reason arrives whole (no word split across lines)
        tokens = set(joined.split())
        for reason in row["reasons"]:
            for token in reason.split():
                self.assertIn(token, tokens, reason)
        # the NOT USABLE legend line carries its whole reason
        legend = " ".join(" ".join(t.split()) for t, _c in report._legend_lines(row, width))
        for curve in row["curves"]:
            if not curve.get("usable", True):
                self.assertIn(" ".join(curve["not_usable_reason"].split()), legend)


@unittest.skipUnless(HAVE_DS and HAVE_TESSERACT, "needs the datasheets and tesseract")
class BoundaryTests(unittest.TestCase):
    """Every boundary and kind decision pinned on real data (review R3-1).

    Where no real panel sits near a threshold, the test takes a REAL curve
    and a REAL table row and moves the one number the threshold judges to
    either side of it, so a mutated threshold flips a verdict.
    """

    def _ao3400a(self):
        row = next(r for r in _results("AO3400A_UMW_C347475") if r.get("validation"))
        cap = _captured("AO3400A_UMW_C347475")[(row["page"], row["diagram"])]
        spec_rows = parse_rdson_spec_rows(DS / "AO3400A_UMW_C347475.pdf")
        return row, cap, spec_rows

    def _anchor(self, row, cap, spec_row):
        return report.validate_against_table(row["curves"], [spec_row], cap["calibration"], cap["scale"])["anchors"][0]

    def test_ao3400a_real_anchors_are_each_inconsistent(self):
        # real datasheet disagreement: chart +30 / +34 / +80 % over table typ
        row, _cap, _spec = self._ao3400a()
        self.assertEqual(row["validation"]["verdict"], "inconsistent")
        verdicts = {a["row"]["vgs_v"]: a["verdict"] for a in row["validation"]["anchors"]}
        self.assertEqual(verdicts, {10.0: "inconsistent", 4.5: "inconsistent", 2.5: "inconsistent"})

    def test_irlb8748_real_anchors_are_each_consistent(self):
        # the largest real residual that is consistent: +11.0 % at 4.5 V
        row = next(r for r in _results("IRLB8748_IFX") if r.get("validation"))
        self.assertEqual({a["verdict"] for a in row["validation"]["anchors"]}, {"consistent"})
        self.assertEqual(row["validation"]["verdict"], "verified")

    def test_typ_tolerance_is_twenty_percent(self):
        import dataclasses
        row, cap, spec_rows = self._ao3400a()
        base = next(r for r in spec_rows if r.vgs_v == 4.5)
        chart = next(a for a in row["validation"]["anchors"] if a["row"]["vgs_v"] == 4.5)["chart_mohm"]
        slack = next(a for a in row["validation"]["anchors"] if a["row"]["vgs_v"] == 4.5)["reading_resolution_mohm_per_px"]
        # |chart - typ| <= 0.20 typ + slack  <=>  typ >= (chart - slack) / 1.20
        edge = (chart - slack) / 1.20
        for typ, verdict in ((edge * 1.002, "consistent"), (edge * 0.998, "inconsistent")):
            spec = dataclasses.replace(base, typ_mohm=typ, max_mohm=None)
            self.assertEqual(self._anchor(row, cap, spec)["verdict"], verdict, typ)

    def test_max_overshoot_tolerance_is_five_percent(self):
        import dataclasses
        row, cap, spec_rows = self._ao3400a()
        base = next(r for r in spec_rows if r.vgs_v == 4.5)
        anchor = next(a for a in row["validation"]["anchors"] if a["row"]["vgs_v"] == 4.5)
        chart, slack = anchor["chart_mohm"], anchor["reading_resolution_mohm_per_px"]
        edge = (chart - slack) / 1.05
        for mx, verdict in ((edge * 1.002, "consistent"), (edge * 0.998, "inconsistent")):
            spec = dataclasses.replace(base, typ_mohm=None, max_mohm=mx)
            self.assertEqual(self._anchor(row, cap, spec)["verdict"], verdict, mx)

    def test_exact_drain_current_is_within_two_percent(self):
        import dataclasses
        row, cap, spec_rows = self._ao3400a()
        base = next(r for r in spec_rows if r.vgs_v == 4.5)
        anchor = next(a for a in row["validation"]["anchors"] if a["row"]["vgs_v"] == 4.5)
        chart_id = anchor["chart_id_a"]
        for id_a, match in ((chart_id / 1.019, "exact"), (chart_id / 1.021, "approximate_drain_current"),
                            (chart_id / 0.981, "exact"), (chart_id / 0.979, "approximate_drain_current")):
            spec = dataclasses.replace(base, id_a=id_a)
            self.assertEqual(self._anchor(row, cap, spec).get("condition_match"), match, id_a)

    def test_drain_current_evaluation_window(self):
        import dataclasses
        row, cap, spec_rows = self._ao3400a()
        base = next(r for r in spec_rows if r.vgs_v == 4.5)
        chart_id = next(a for a in row["validation"]["anchors"] if a["row"]["vgs_v"] == 4.5)["chart_id_a"]
        lo, hi = 0.75, 1.34  # the documented window (README), not read back from the code
        for ratio, evaluable in ((lo * 1.01, True), (lo * 0.99, False), (hi * 0.99, True), (hi * 1.01, False)):
            spec = dataclasses.replace(base, id_a=chart_id / ratio)
            verdict = self._anchor(row, cap, spec)["verdict"]
            self.assertEqual(verdict != "not_evaluable", evaluable, (ratio, verdict))

    def test_temperature_match_window(self):
        import dataclasses
        row, cap, spec_rows = self._ao3400a()
        base = next(r for r in spec_rows if r.vgs_v == 4.5)
        for dt, evaluable in ((0.9, True), (1.1, False), (-0.9, True), (-1.1, False)):
            spec = dataclasses.replace(base, temperature_c=base.temperature_c + dt)
            verdict = self._anchor(row, cap, spec)["verdict"]
            self.assertEqual(verdict != "not_evaluable", evaluable, (dt, verdict))

    def test_an_inconsistent_anchor_outranks_an_exact_consistent_one(self):
        import dataclasses
        row, cap, spec_rows = self._ao3400a()
        base = next(r for r in spec_rows if r.vgs_v == 4.5)
        anchor = next(a for a in row["validation"]["anchors"] if a["row"]["vgs_v"] == 4.5)
        good = dataclasses.replace(base, typ_mohm=anchor["chart_mohm"], max_mohm=None)
        result = report.validate_against_table(row["curves"], [good, base], cap["calibration"], cap["scale"])
        self.assertEqual([a["verdict"] for a in result["anchors"]], ["consistent", "inconsistent"])
        self.assertEqual(result["verdict"], "inconsistent")
        only_good = report.validate_against_table(row["curves"], [good], cap["calibration"], cap["scale"])
        self.assertEqual(only_good["verdict"], "verified")

    def test_readout_end_tolerance_is_one_pixel(self):
        # real IRLB8743 25 C curve starts at 3.38 V: 3.3 V is 8 px off it
        row = next(r for r in _results("IRLB8743_IFX") if r.get("curves"))
        one_px = abs(row["calibration"]["x_axis"]["m"])
        curve = max(row["curves"], key=lambda c: c["vgs_range_v"][0])
        self.assertAlmostEqual(curve["vgs_range_v"][0], 3.38, delta=0.03)
        v0 = curve["points"][0][0]
        got = report.readouts(curve["points"], False, [], one_px,
                              targets=(v0 - 0.9 * one_px, v0 - 1.1 * one_px))
        self.assertEqual(got[0]["status"], "read")
        self.assertAlmostEqual(got[0]["rds_mohm"], curve["points"][0][1], places=3)
        self.assertEqual(got[1]["status"], "not_on_chart")
        self.assertEqual(_readout(curve, 3.3)["status"], "not_on_chart")

    def test_gap_threshold_is_between_two_and_three_pixels(self):
        # real WSR3090 25 C curve, flat clean stretch near 9.5 V: one missing
        # column (a 2-px step) is sampling, two (3 px) are an unread interval
        cap = _captured("WSR3090_LCSC_C719278")[(3, "2")]
        cal = cap["calibration"]
        base = max(cap["traces"], key=lambda t: sum(1 for p in t.points_px if cal.x_axis.value(p[0]) > 9.3))
        xs = sorted(p[0] for p in base.points_px if 9.4 <= cal.x_axis.value(p[0]) <= 9.6)
        mid = xs[len(xs) // 2]
        for missing, expect_gap in ((1, False), (2, True)):
            kept = [p for p in base.points_px if not mid <= p[0] < mid + missing]
            trace = traces_mod.Trace(kept, base.method, base.style, base.merged_columns, dict(base.params),
                                     dict(base.binding), base.bridged_columns, list(base.contact_removed_x))
            curve = rgv._curves([trace], cal, cap["scale"], cap["gray"])[0][0]
            hit = [g for g in curve["gaps"] if g[0] < cal.x_axis.value(mid) < g[1]]
            self.assertEqual(bool(hit), expect_gap, (missing, curve["gaps"]))
            if hit:
                self.assertIn(hit[0], curve["gap_kinds"]["untraced_section"])

    def test_a_stretch_with_no_ink_is_a_gap_not_an_untraced_section(self):
        # the same real curve with its ink blanked over 9.40-9.60 V (the
        # reviewer's white-paper case): no ink between the ends -> "gap"
        cap = _captured("WSR3090_LCSC_C719278")[(3, "2")]
        cal = cap["calibration"]
        gray = cap["gray"].copy()
        base = max(cap["traces"], key=lambda t: sum(1 for p in t.points_px if cal.x_axis.value(p[0]) > 9.3))
        cols = [int(p[0]) for p in base.points_px if 9.40 <= cal.x_axis.value(p[0]) <= 9.60]
        gray[:, min(cols):max(cols) + 1] = 255
        kept = [p for p in base.points_px if not 9.40 <= cal.x_axis.value(p[0]) <= 9.60]
        trace = traces_mod.Trace(kept, base.method, base.style, base.merged_columns, dict(base.params),
                                 dict(base.binding), base.bridged_columns, list(base.contact_removed_x))
        curve = rgv._curves([trace], cal, cap["scale"], gray)[0][0]
        self.assertTrue(any(g0 < 9.5 < g1 for g0, g1 in curve["gap_kinds"]["gap"]), curve["gap_kinds"])
        self.assertFalse(any(g0 < 9.5 < g1 for g0, g1 in curve["gap_kinds"]["untraced_section"]))



@unittest.skipUnless(HAVE_DS and HAVE_TESSERACT, "needs the datasheets and tesseract")
class RoundThreeLateTests(unittest.TestCase):
    """Fab's v3 overlay inspection (R3-8..R3-14), each on its real case."""

    RASTER = (("RQ3E110AJ_Rohm", 7, "12"), ("RQ6E080AJ_Rohm", 7, "12"), ("RQ3E180AJ_Rohm", 7, "12"))

    # -- R3-8: tick provenance -------------------------------------------------

    def test_r3_8_image_chart_ticks_are_not_credited_to_the_text_layer(self):
        # pdftotext finds 2 numeric words on RQ3E110AJ p7 (both charts are
        # images): no tick label there can come from the text layer.
        import pymupdf
        for name, page, diagram in self.RASTER:
            with pymupdf.open(DS / f"{name}.pdf") as document:
                numeric = [w[4] for w in document[page - 1].get_text("words") if re.fullmatch(r"[\d.]+", w[4])]
            self.assertLessEqual(len(numeric), 3, (name, numeric))
            cal = _panel(name, page, diagram)["calibration"]
            self.assertFalse(cal["tick_source"].startswith("text_layer"), (name, cal["tick_source"]))
            origins = {o["origin"] for axis in cal["tick_origins"].values() for o in axis}
            self.assertNotIn("text_layer", origins, name)
            self.assertNotIn("unmatched", origins, name)

    def test_r3_8_vector_chart_ticks_come_from_the_text_layer(self):
        row = next(r for r in _results("CSD17306Q5A_TI") if r["page"] == 4)
        cal = row["calibration"]
        self.assertEqual(cal["tick_source"], "text_layer")
        self.assertEqual({o["origin"] for axis in cal["tick_origins"].values() for o in axis}, {"text_layer"})

    # -- R3-9: used-tick span and extrapolation ------------------------------------

    def test_r3_9_rq3e110aj_uses_every_printed_tick(self):
        cal = _panel("RQ3E110AJ_Rohm", 7, "12")["calibration"]
        self.assertEqual(cal["used_tick_span"]["y_axis"]["values"], [0.0, 30.0])
        self.assertEqual(cal["used_tick_span"]["x_axis"]["values"], [0.0, 10.0])
        self.assertEqual(sorted(t["value"] for t in cal["y_axis"]["ticks"]), [float(v) for v in range(0, 31, 2)])
        self.assertIn("added ticks", cal["tick_completion"])
        for curve in _panel("RQ3E110AJ_Rohm", 7, "12")["curves"]:
            for reading in curve["readouts"]:
                if reading["rds_mohm"] is not None:
                    self.assertEqual(reading["calibration_span"], "inside", reading)

    def _v3_ladder(self):
        # the v3 calibration: y ticks 10-30 and x ticks 1-10 only (panel OCR)
        import dataclasses
        from datasheet_chart_digitizer.numeric_axis import NumericAxis
        cap = _captured("RQ3E110AJ_Rohm")[(7, "12")]
        cal = cap["calibration"]

        def cut(axis, keep):
            ticks = tuple(t for t in axis.ticks if keep(t.value))
            return NumericAxis(axis.model, axis.m, axis.b, ticks, axis.residual_px, axis.candidate_residuals_px)

        return cap, dataclasses.replace(cal, x_axis=cut(cal.x_axis, lambda v: v >= 1), y_axis=cut(cal.y_axis, lambda v: v >= 10))

    def test_r3_9_readouts_below_the_used_ticks_are_flagged(self):
        cap, v3 = self._v3_ladder()
        # 8.6 mOhm (the 4.5 V reading) lies below the lowest used tick (10);
        # the frame bottom (0 mOhm) sits on the fitted lattice: anchored
        state = rgv._span_state(v3, 4.5, 8.6)
        self.assertEqual(state["state"], "outside_anchored", state)
        # the same with the frame 3 px off the lattice: NOT anchored -> a reason
        import dataclasses
        moved = dataclasses.replace(v3, plot=dataclasses.replace(v3.plot, y1=v3.plot.y1 + 3))
        self.assertEqual(rgv._span_state(moved, 4.5, 8.6)["state"], "outside_unanchored")
        curves = [{"curve_index": 0, "readouts": [{"vgs_v": 4.5, "rds_mohm": 8.6, "status": "read"}]}]
        reasons = rgv._flag_calibration_span(curves, {"anchors": []}, moved, 1.0)
        self.assertTrue(reasons and reasons[0].startswith("curve_0_readout_outside_calibrated_span"), reasons)
        self.assertEqual(rgv._span_state(v3, 4.5, 12.0)["state"], "inside")

    # -- R3-10 as reverted by F4-2: the v3 tick marks, every used tick --------

    def test_f4_2_every_used_tick_is_drawn_in_the_v3_style_on_the_plot(self):
        calls = []
        real = report.draw_axis_ticks

        def spy(image, plot, **kwargs):
            calls.append((image.shape, kwargs))
            return real(image, plot, **kwargs)

        for name, page, diagram in (("RQ3E110AJ_Rohm", 7, "12"), ("FDP8870_onsemi", 5, "9")):
            calls.clear()
            _CACHE.pop(name, None)
            with patch.object(report, "draw_axis_ticks", spy):
                row = _panel(name, page, diagram)
            self.assertEqual(len(calls), 1, name)
            shape, kwargs = calls[0]
            self.assertEqual(sorted(v for _p, v in kwargs["x_ticks"]),
                             sorted(t["value"] for t in row["calibration"]["x_axis"]["ticks"]), name)
            self.assertEqual(sorted(v for _p, v in kwargs["y_ticks"]),
                             sorted(t["value"] for t in row["calibration"]["y_axis"]["ticks"]), name)
            self.assertEqual(kwargs["color"], (255, 0, 0))   # blue "+" markers, as in v3
            self.assertNotIn("overlay_tick_marks", row)
            # drawn on the crop itself: no white axis band added around it
            box = row["crop_box_pt"]
            self.assertLessEqual(abs(shape[1] - (box[2] - box[0]) * row["crop_dpi"] / 72), 2, (name, shape))
            self.assertLessEqual(abs(shape[0] - (box[3] - box[1]) * row["crop_dpi"] / 72), 2, (name, shape))

    # -- R3-11: a curve above the table max, bindings unknown -------------------

    def test_r3_11_brcs020n03ra_curve_above_table_max_is_recorded_not_judged(self):
        row = _panel("BRCS020N03RA_LCSC_C22449012", 4, "5")
        notes = row["validation"]["diagnostics"]
        hit = [n for n in notes if n["vgs_v"] == 4.5 and n["kind"] == "curve_exceeds_table_max_at_table_vgs"]
        self.assertEqual(len(hit), 1, notes)
        self.assertGreater(hit[0]["chart_mohm"], hit[0]["table_max_mohm"])
        self.assertIn("temperature binding unknown", hit[0]["text"])
        # no claim either way: the verdict is untouched, the reason is stated
        self.assertEqual(row["validation"]["verdict"], "not_evaluable")
        self.assertTrue(any(r.startswith(f"curve_{hit[0]['curve_index']}_exceeds_table_max_at_4.5V") for r in row["reasons"]))

    def test_r3_11_an_evaluated_or_differently_bound_curve_is_not_a_diagnostic(self):
        # AO3400A: the 25 C curve is judged by its anchors; the 125 C curve is
        # bound to another temperature than the rows, so it is not listed
        row = next(r for r in _results("AO3400A_UMW_C347475") if r.get("validation"))
        self.assertEqual(row["validation"]["diagnostics"], [])
        for name in ("CSD17306Q5A_TI", "IRLB8748_IFX", "SIS176LDN_Vishay"):
            for r in _results(name):
                if r.get("validation"):
                    self.assertEqual(r["validation"]["diagnostics"], [], name)

    # -- R3-12: per-curve legend and direct labels -------------------------------

    def test_r3_12_legend_row_and_direct_label_per_curve(self):
        row = next(r for r in _results("CSD17306Q5A_TI") if r["page"] == 4)
        legend = [t for t, _c in report._legend_lines(row, 4000)]
        for curve in row["curves"]:
            line = next(t for t in legend if t.startswith(f"c{curve['curve_index']} "))
            self.assertIn(f"{curve['temperature_kind']}={curve['temperature_c']:g}C", line)
            self.assertIn(f"ID={curve['id_a']:g}A", line)
            self.assertIn("usable", line)
            for reading in curve["readouts"]:
                self.assertIn(f"{reading['vgs_v']:g}V:", line)
        labels = row["overlay_curve_labels"]
        self.assertEqual(sorted(l["curve_index"] for l in labels), [c["curve_index"] for c in row["curves"]])
        self.assertIn("c1 Tc=25C", [l["text"] for l in labels])
        boxes = [l["box_px"] for l in labels]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                self.assertTrue(a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1], (a, b))
        for label in labels:
            self.assertEqual(label["placement"], "beside its trace, clear of all ink")
            x0, y0, x1, y1 = label["box_px"]
            for curve in row["curves"]:
                for x, y in curve["points_px"]:
                    self.assertFalse(x0 - 3 <= x <= x1 + 3 and y0 - 3 <= y <= y1 + 3, (label, x, y))

    def test_r3_12_direct_labels_never_sit_on_curve_ink(self):
        # the panels where the first candidate spot would land on a curve
        # (label placement ignoring ink put 35-60 traced points under labels)
        for name, page, diagram in (("AO3400A_UMW_C347475", 3, "5"), ("WSR3090_LCSC_C719278", 3, "2"),
                                    ("BRCS020N03RA_LCSC_C22449012", 4, "5")):
            row = _panel(name, page, diagram)
            self.assertEqual(len(row["overlay_curve_labels"]), len(row["curves"]), name)
            for label in row["overlay_curve_labels"]:
                x0, y0, x1, y1 = label["box_px"]
                on_ink = [(x, y) for c in row["curves"] for x, y in c["points_px"]
                          if x0 - 3 <= x <= x1 + 3 and y0 - 3 <= y <= y1 + 3]
                self.assertEqual(on_ink, [], (name, label))

    def test_r3_12_unknown_labels_are_spelled_out(self):
        legend = " ".join(t for t, _c in report._legend_lines(_panel("RQ3E180AJ_Rohm", 7, "12"), 4000))
        self.assertIn("T unknown", legend)
        legend = " ".join(t for t, _c in report._legend_lines(_panel("RQ6E080AJ_Rohm", 7, "12"), 4000))
        self.assertIn("ID unknown", legend)

    # -- R3-13: FDP8870 coincident tails ------------------------------------------

    def test_r3_13_fdp8870_source_draws_the_tails_coincident(self):
        # PDF drawings 716 (1 A) and 719 (35 A) are separate filled outlines:
        # at 6 V both have vertices at y 621.07 / 623.11 pt, 0.36 pt apart in x
        import pymupdf
        with pymupdf.open(DS / "FDP8870_onsemi.pdf") as document:
            drawings = document[4].get_drawings()
        ys = []
        for index in (716, 719):
            pts = [(p.x, p.y) for item in drawings[index]["items"] for p in item[1:] if hasattr(p, "x")]
            ys.append({round(y, 2) for x, y in pts if 207.5 <= x <= 211.0})
        self.assertTrue({621.07, 623.11} <= ys[0] & ys[1], ys)

    def test_r3_13_fdp8870_coincidence_is_recorded_both_ways(self):
        row = _panel("FDP8870_onsemi", 5, "9")
        c0, c1 = row["curves"]
        for curve, other in ((c0, 1), (c1, 0)):
            spans = [c for c in curve["coincident_with"] if c["curve_index"] == other]
            self.assertEqual(len(spans), 1, curve["coincident_with"])
            self.assertAlmostEqual(spans[0]["from_vgs_v"], 4.41, delta=0.15)
            self.assertAlmostEqual(spans[0]["to_vgs_v"], 10.0, delta=0.01)
        self.assertTrue(any(r.startswith("curve_0_coincident_with_curve_1") for r in row["reasons"]))
        # separated curves are not called coincident (WSR3090: >= 35 px apart)
        for name, page, diagram in (("WSR3090_LCSC_C719278", 3, "2"), ("BRCS020N03RA_LCSC_C22449012", 4, "5")):
            for curve in _panel(name, page, diagram)["curves"]:
                self.assertEqual(curve["coincident_with"], [], name)

    def test_r3_13_both_coincident_curves_stay_visible(self):
        import numpy as np
        row = _panel("FDP8870_onsemi", 5, "9")
        x0 = min(p[0] for p in row["curves"][0]["points_px"] if p[0] > 0)
        body = np.full((1000, 1200, 3), 255, dtype=np.uint8)
        report._draw_curves(body, row["curves"])
        lo = int(_px_at(row, 6.0))
        hi = int(_px_at(row, 9.0))
        self.assertGreater(hi, lo + 50, (lo, hi, x0))
        for curve in row["curves"]:
            color = np.asarray(report.curve_color(curve), dtype=int)
            near = np.abs(body[:, lo:hi].astype(int) - color).max(axis=2) <= 40
            hits = near.any(axis=0).sum()
            # dashes alternate every 10 px; line caps and halos eat part of each gap
            self.assertGreater(hits, 0.2 * (hi - lo), (curve["curve_index"], hits, hi - lo))

    # -- R3-14: palette and halos ----------------------------------------------------

    def test_r3_14_palette_is_bright_against_black_ink(self):
        self.assertGreaterEqual(len(report._COLORS), 6)
        for color in report._COLORS:
            self.assertGreaterEqual(report.contrast_vs_black(color), report.MIN_CONTRAST_VS_BLACK, color)
        self.assertGreaterEqual(report.MIN_CONTRAST_VS_BLACK, 4.0)
        self.assertNotIn((130, 0, 75), report._COLORS)
        self.assertEqual(len(set(report._COLORS)), len(report._COLORS))

    def test_r3_14_traces_have_white_halos_over_black_ink(self):
        import numpy as np
        row = next(r for r in _results("CSD17306Q5A_TI") if r["page"] == 4)
        body = np.zeros((1000, 1200, 3), dtype=np.uint8)   # all "black ink"
        report._draw_curves(body, row["curves"])
        white = (body >= 230).all(axis=2)
        for curve in row["curves"]:
            x, y = (int(round(v)) for v in curve["points_px"][len(curve["points_px"]) // 2])
            window = white[y - 6:y + 7, x - 6:x + 7]
            self.assertTrue(window.any(), curve["curve_index"])
            color = np.asarray(report.curve_color(curve), dtype=int)
            near = np.abs(body[y - 2:y + 3, x - 2:x + 3].astype(int) - color).max(axis=2) <= 40
            self.assertTrue(near.any(), curve["curve_index"])


@unittest.skipUnless(HAVE_DS and HAVE_TESSERACT, "needs the datasheets and tesseract")
class RoundFourTests(unittest.TestCase):
    """Fab's v4 overlay review (F4-1..F4-5), each on its real case."""

    # -- F4-1: steep heads to the frame -----------------------------------------

    def _top_mohm(self, row):
        cal = row["calibration"]["y_axis"]
        return (cal["m"] * row["plot_box_px"]["y0"] + cal["b"]) * row["calibration"]["y_to_mohm"]

    def test_f4_1_steep_heads_reach_the_top_frame(self):
        # v4: RQ3E110AJ started at 23.4 mOhm, RQ3E180AJ's 9 A / 18 A branches
        # at 39.7 / 48.2 mOhm; the printed ink runs to the 30 / 50 mOhm top
        for name, page, diagram, count in (("RQ3E110AJ_Rohm", 7, "12", 2), ("RQ3E180AJ_Rohm", 7, "12", 2),
                                           ("RQ6E080AJ_Rohm", 7, "12", 2)):
            row = _panel(name, page, diagram)
            top = self._top_mohm(row)
            self.assertEqual(len(row["curves"]), count, name)
            for curve in row["curves"]:
                highest = max(r for _v, r in curve["points"])
                self.assertGreaterEqual(highest, 0.98 * top, (name, curve["curve_index"], highest, top))
                self.assertTrue(curve["trace_complete"]["left_end_at_frame"], (name, curve["curve_index"]))
            self.assertFalse(any("head_not_traced_to_frame" in r for r in row["reasons"]), name)

    def test_f4_1_row_traced_points_are_on_ink(self):
        for name in ("RQ3E110AJ_Rohm", "RQ3E180AJ_Rohm"):
            cap = _captured(name)[(7, "12")]
            gray = cap["gray"]
            traced = [p for t in cap["traces"] for p in t.row_traced_points]
            self.assertGreater(len(traced), 100, name)
            off = [(x, y) for x, y in traced
                   if gray[int(y), max(0, int(round(x)) - 2):int(round(x)) + 3].min() >= 150]
            self.assertLessEqual(len(off), 0.02 * len(traced), (name, off[:5]))

    def test_f4_1_an_untraced_head_is_stated_plainly(self):
        # the same real panel with the row tracker off: the ink still runs to
        # the frame, and the reason must say what was not traced
        _CACHE.pop("RQ3E110AJ_Rohm", None)
        try:
            with patch.object(traces_mod, "extend_steep_heads", lambda traces, *a: traces):
                row = _panel("RQ3E110AJ_Rohm", 7, "12")
            stated = [r for r in row["reasons"] if "head_not_traced_to_frame" in r]
            self.assertTrue(stated, row["reasons"])
            self.assertIn("printed ink continues to the frame; not traced from", stated[0])
            self.assertIn("to 30 mOhm", stated[0])
        finally:
            _CACHE.pop("RQ3E110AJ_Rohm", None)

    # -- F4-3: arrows followed to the curve they point at ------------------------

    def test_f4_3_wsr3090_temperatures_follow_the_arrows(self):
        row = _panel("WSR3090_LCSC_C719278", 3, "2")
        by_height = sorted(row["curves"], key=lambda c: np.median([p[1] for p in c["points_px"]]))
        self.assertEqual([c["temperature_c"] for c in by_height], [125.0, 100.0, 25.0])
        for curve in row["curves"]:
            self.assertEqual(curve["parameter_binding"]["temperature_c"], "leader_line")
        self.assertTrue(any("25" in t for t in row["labels_read_at_arrow_tails"]), row["labels_read_at_arrow_tails"])
        # the 125 C arrow crosses the 100 C curve on its way: its tip is on the top curve
        tips = [l["tip"] for l in row["raster_leaders_px"] if 740 <= l["tip"][0] <= 780]
        self.assertTrue(tips)
        top = by_height[0]
        self.assertLess(min(np.hypot(p[0] - tips[0][0], p[1] - tips[0][1]) for p in top["points_px"]), 5.0)
        # bound, the table rows are evaluable, and the curve-above-max notes resolve
        self.assertNotEqual(row["validation"]["verdict"], "not_evaluable")
        self.assertEqual(row["validation"]["diagnostics"], [])
        self.assertFalse(any("temperature_c_unknown" in r for r in row["reasons"]), row["reasons"])

    def test_f4_3_a_leader_ending_in_touching_lines_names_neither(self):
        # RQ3E110AJ: both ID leaders end in the 11.0 A / 5.5 A band where the
        # two lines touch: IDs stay unknown, and the note says why
        row = _panel("RQ3E110AJ_Rohm", 7, "12")
        self.assertEqual({c["id_a"] for c in row["curves"]}, {None})
        self.assertTrue(any("leader_tip_between_touching_curves" in n for n in row["label_binding_notes"]),
                        row["label_binding_notes"])

    def test_f4_3_rq3e180aj_ids_bound_by_their_leaders(self):
        row = _panel("RQ3E180AJ_Rohm", 7, "12")
        by_x = sorted(row["curves"], key=lambda c: c["points_px"][0][0])
        self.assertEqual([c["id_a"] for c in by_x], [9.0, 18.0])
        self.assertEqual({c["parameter_binding"]["id_a"] for c in by_x}, {"leader_line"})

    # -- F4-4: exactly the printed curves ------------------------------------------

    def test_f4_4_rq3e110aj_pair_is_two_lines_side_by_side(self):
        # The 11.0 A / 5.5 A pair is printed as two touching lines: side by
        # side in the steep part, stacked in the flatter part (a column run of
        # 10 px at x=600 where one line is 5 px). Each curve takes its own
        # half: at 4.5 V the upper line reads higher, and neither curve is the
        # band's middle.
        cap = _captured("RQ3E110AJ_Rohm")[(7, "12")]
        col = cap["gray"][560:640, 600] < 150
        self.assertGreaterEqual(int(col.sum()), 9)
        row = _panel("RQ3E110AJ_Rohm", 7, "12")
        upper, lower = sorted(row["curves"], key=lambda c: -_readout(c, 4.5)["rds_mohm"])
        diff = _readout(upper, 4.5)["rds_mohm"] - _readout(lower, 4.5)["rds_mohm"]
        self.assertGreater(diff, 0.08)            # ~5 px apart at 0.0384 mOhm/px
        self.assertLess(diff, 0.35)

    def test_f4_4_two_printed_curves_are_two_complete_curves(self):
        for name, merge_from in (("RQ6E080AJ_Rohm", 1.95), ("RQ3E180AJ_Rohm", 1.75)):
            row = _panel(name, 7, "12")
            self.assertEqual(len(row["curves"]), 2, name)
            for curve in row["curves"]:
                self.assertGreaterEqual(curve["vgs_range_v"][1], 4.9, (name, curve["vgs_range_v"]))
                other = 1 - curve["curve_index"]
                spans = [c for c in curve["coincident_with"] if c["curve_index"] == other]
                self.assertEqual(len(spans), 1, (name, curve["coincident_with"]))
                self.assertAlmostEqual(spans[0]["from_vgs_v"], merge_from, delta=0.1)
                self.assertEqual(_readout(curve, 4.5)["status"], "read")
            # the merged tail is never a curve of its own: every curve has its
            # own head at the top frame
            self.assertEqual({c["trace_complete"]["left_end_at_frame"] for c in row["curves"]}, {True}, name)

    def _visible_columns(self, row, curve, v0, v1):
        import numpy as np
        body = np.full((1000, 1200, 3), 255, dtype=np.uint8)
        report._draw_curves(body, row["curves"])
        lo, hi = int(_px_at(row, v0)), int(_px_at(row, v1))
        color = np.asarray(report.curve_color(curve), dtype=int)
        near = np.abs(body[:, lo:hi].astype(int) - color).max(axis=2) <= 40
        return near.any(axis=0).mean()

    def test_f4_5_fdp8870_both_curves_show_everywhere_with_one_style(self):
        # v4: c0 looked cut 3.4-4.4 V (c1 drawn over it 0.5-2.5 px away) and
        # dotted from 4.41 V (dashes). Nested widths: every column shows both.
        row = _panel("FDP8870_onsemi", 5, "9")
        for v0, v1 in ((3.4, 4.4), (4.45, 9.9)):
            for curve in row["curves"]:
                self.assertGreaterEqual(self._visible_columns(row, curve, v0, v1), 0.95, (curve["curve_index"], v0, v1))
        widths = report.line_widths(row["curves"])
        self.assertGreater(widths[0], widths[1])

    def test_f4_5_nesting_holds_on_every_multi_curve_panel(self):
        for name, page, diagram in (("WSR3090_LCSC_C719278", 3, "2"), ("RQ6E080AJ_Rohm", 7, "12")):
            row = _panel(name, page, diagram)
            widths = report.line_widths(row["curves"])
            ordered = [widths[c["curve_index"]] for c in sorted(row["curves"], key=lambda c: c["curve_index"])]
            self.assertEqual(ordered, sorted(ordered, reverse=True), name)
            self.assertEqual(len(set(ordered)), len(ordered), name)


def _px_at(row: dict, vgs: float) -> float:
    axis = row["calibration"]["x_axis"]
    return (vgs - axis["b"]) / axis["m"]


if __name__ == "__main__":
    unittest.main()
