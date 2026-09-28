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
            jumps = [(a, b) for a, b in zip(px, px[1:]) if b - a > 2.5]
            self.assertEqual(len(jumps), len(curve.get("gaps", [])), (row["part"], curve["curve_index"], jumps[:3]))
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

    def test_short_fragments_open_at_both_ends_are_not_usable(self):
        row = _panel("RQ6E080AJ_Rohm", 7, "12")
        fragments = [
            c for c in row["curves"]
            if not c["trace_complete"]["left_end_at_frame"] and not c["trace_complete"]["right_end_at_frame"]
            and (c["vgs_range_v"][1] - c["vgs_range_v"][0]) < 3.0
        ]
        self.assertTrue(fragments)
        for curve in fragments:
            self.assertIs(curve.get("usable"), False)
            self.assertTrue(all(r["status"] == "curve_not_usable" for r in curve["readouts"]))
        self.assertNotEqual(row["status"], "ok")


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
        self.assertEqual([c["temperature_kind"] for c in row["curves"]], ["T (subscript unread)"])

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
        header = "".join(t.replace("    ", "", 1) if t.startswith("    ") else "\n" + t for t, _c in report._header_lines(row, panel))
        self.assertGreater(len(row["reasons"]), 4)
        for reason in row["reasons"]:
            self.assertIn(reason, header)

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
        # called a gap; the lone edge point is not served; the untraced top is
        # reported as a partial trace.
        row = _panel("RQ3E110AJ_Rohm", 7, "12")
        curve = row["curves"][0]
        self.assertEqual(curve["gap_kinds"]["gap"], [])
        self.assertFalse(any("_gaps (" in r for r in row["reasons"]), row["reasons"])
        self.assertGreater(curve["vgs_range_v"][0], 2.0)
        self.assertTrue(any(r.startswith("curve_0_partial_raster_trace") for r in row["reasons"]))

    def test_r2_8_unsampled_stretch_on_continuous_ink_is_an_untraced_section(self):
        # WSR3090 curve 2: 7.56-7.78 V, where the 25 C curve runs along the
        # 5 mOhm rule; the ink is continuous, so it is an untraced section.
        row = _panel("WSR3090_LCSC_C719278", 3, "2")
        kinds = [c["gap_kinds"] for c in row["curves"]]
        self.assertTrue(any(any(g0 < 7.6 < g1 for g0, g1 in k["untraced_section"]) for k in kinds), kinds)


if __name__ == "__main__":
    unittest.main()
