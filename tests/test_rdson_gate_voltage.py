"""Tests for the RDS(on)-versus-VGS chart class (``dsdig digitize-rds-vgs``).

Every fixture is a real datasheet from the solar-charger evaluation set, or a
real extraction from one with a single, named defect injected (the guard's
known-bad calibration). The end-to-end tests skip cleanly when the datasheet
folder is absent; the OCR-dependent ones also need ``tesseract``.

Reference numbers were read off the panels by this pipeline on 2026-09-28 and
cross-checked against the datasheets' own RDS(on) tables (TI prints its
typical table values exactly on its typical curves, so a TI Figure 7 is a
calibration standard: chart = table to within a pixel).

Scratch output goes under the repo's gitignored ``out/`` -- never /tmp.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from datasheet_chart_digitizer import rdson_gate_voltage as rgv
from datasheet_chart_digitizer import rdson_gate_voltage_axes as axes
from datasheet_chart_digitizer import rdson_gate_voltage_traces as traces_mod
from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.diode_forward_voltage import TextLabel
from datasheet_chart_digitizer.rdson_gate_voltage_locate import locate_panels
from datasheet_chart_digitizer.rdson_gate_voltage_report import readouts
from datasheet_chart_digitizer.rdson_spec_table import parse_rdson_spec_rows

import rds_digitize_cache as dcache

DS = Path("/Users/fab/dev/ee/solar-charger-eval/ds")
OUT_ROOT = Path(__file__).resolve().parents[1] / "out"
HAVE_DS = DS.is_dir()
HAVE_TESSERACT = shutil.which("tesseract") is not None


def _pdf(name: str) -> Path:
    return DS / f"{name}.pdf"


def _scratch(prefix: str) -> tempfile.TemporaryDirectory:
    OUT_ROOT.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=OUT_ROOT)


def _panel(results: list[dict], page: int, diagram: str) -> dict:
    matches = [r for r in results if r["page"] == page and r["diagram"] == diagram]
    assert len(matches) == 1, [(r["page"], r["diagram"]) for r in results]
    return matches[0]


def _curve(row: dict, temperature_c: float) -> dict:
    matches = [c for c in row["curves"] if c["temperature_c"] == temperature_c]
    assert len(matches) == 1, [c["label"] for c in row["curves"]]
    return matches[0]


def _readout(curve: dict, vgs: float) -> dict:
    return next(r for r in curve["readouts"] if r["vgs_v"] == vgs)


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class SpecTableTests(unittest.TestCase):
    def test_ti_rows_are_owned_by_typ_and_max_columns(self):
        rows = parse_rdson_spec_rows(_pdf("CSD17306Q5A_TI"))
        got = [(r.vgs_v, r.id_a, r.typ_mohm, r.max_mohm, r.unit_token) for r in rows]
        self.assertEqual(
            got,
            [(3.0, 22.0, 4.2, 5.4, "mΩ"), (4.5, 22.0, 3.3, 4.2, "mΩ"), (8.0, 22.0, 2.9, 3.7, "mΩ")],
        )
        self.assertTrue(all(r.temperature_source == "table_heading" and r.temperature_kind == "Ta" for r in rows))

    def test_ir_second_row_values_sit_on_their_own_baseline(self):
        rows = {r.vgs_v: r for r in parse_rdson_spec_rows(_pdf("IRLB8748_IFX"))}
        self.assertEqual((rows[10.0].typ_mohm, rows[10.0].max_mohm), (3.8, 4.8))
        self.assertEqual((rows[4.5].id_a, rows[4.5].typ_mohm, rows[4.5].max_mohm), (32.0, 5.5, 6.8))

    def test_ohm_values_convert_and_an_adjacent_temperature_binds_its_row(self):
        rows = parse_rdson_spec_rows(_pdf("FDP8870_onsemi"))
        got = [(r.vgs_v, r.temperature_c, r.typ_mohm, r.max_mohm) for r in rows]
        self.assertIn((10.0, 25.0, 3.4, 4.1), got)
        self.assertIn((4.5, 25.0, 4.0, 4.6), got)
        # "TJ = 175oC" is printed one baseline below its row: it must not be
        # left at the 25 C table default.
        self.assertIn((10.0, 175.0, 5.1, 6.5), got)

    def test_symbol_font_ohm_glyphs_are_read(self):
        rows = {r.vgs_v: r for r in parse_rdson_spec_rows(_pdf("SIS176LDN_Vishay"))}
        self.assertEqual(rows[4.5].unit_token, "")
        self.assertEqual((rows[4.5].typ_mohm, rows[4.5].max_mohm), (8.6, 10.9))
        rows = {r.vgs_v: r for r in parse_rdson_spec_rows(_pdf("AO3416_AOS"))}
        self.assertEqual((rows[4.5].unit_token, rows[4.5].typ_mohm), ("mW", 16.0))

    # --- batch_all class C (2026-09-30) -------------------------------------------

    def test_c1_unit_glued_to_the_next_rows_vgs_by_symbol_spaces(self):
        # IRLB8314: "mVGS" was one word; both rows lost their unit
        # and the 10 V row lost its VGS. Printed: 10 V 1.9/2.4, 4.5 V 2.6/3.2 mOhm @ 68 A.
        rows = {r.vgs_v: r for r in parse_rdson_spec_rows(_pdf("IRLB8314_IFX"))}
        self.assertEqual(sorted(rows), [4.5, 10.0])
        self.assertEqual((rows[10.0].id_a, rows[10.0].typ_mohm, rows[10.0].max_mohm), (68.0, 1.9, 2.4))
        self.assertEqual((rows[4.5].id_a, rows[4.5].typ_mohm, rows[4.5].max_mohm), (68.0, 2.6, 3.2))

    def test_c1_symbol_omega_padded_with_symbol_spaces(self):
        # IRL3705N: the unit cell is "" (Omega + Symbol spaces).
        rows = {r.vgs_v: r for r in parse_rdson_spec_rows(_pdf("IRL3705N_IFX"))}
        self.assertEqual({v: (r.typ_mohm, r.max_mohm) for v, r in rows.items()},
                         {10.0: (None, 10.0), 5.0: (None, 12.0), 4.0: (None, 18.0)})
        # the label column's "Resistance" is not a row qualifier
        self.assertEqual({r.qualifier for r in rows.values()}, {""})

    def test_c1_undecodable_omega_and_a_unit_printed_once_per_block(self):
        # FDP5800: Omega is U+0002 in IntDutch801G (dropped by PyMuPDF), printed
        # once on the 10 V row; the table heading is "(TC = 25 C ...)".
        rows = parse_rdson_spec_rows(_pdf("FDP5800_onsemi"))
        got = [(r.vgs_v, r.temperature_c, r.temperature_kind, r.typ_mohm, r.max_mohm) for r in rows]
        self.assertEqual(got, [(10.0, 25.0, "Tc", 4.6, 6.0), (4.5, 25.0, "Tc", 5.9, 7.2),
                               (5.0, 25.0, "Tc", 5.6, 7.0), (10.0, 175.0, "Tj", 10.4, 12.6)])
        self.assertEqual([r.unit_source.split(":")[0] for r in rows], ["row", "row", "row_block", "row_block"])

    def test_c1_known_bad_bare_m_or_conflicting_block_units_stay_unreadable(self):
        import pymupdf
        from datasheet_chart_digitizer import rdson_spec_table as st
        from datasheet_chart_digitizer.finder_types import Word
        with pymupdf.open(_pdf("FDP5800_onsemi")) as document:
            words = st.page_words(document[2])
        # (a) a bare "m" (no undecodable glyph after it) is no unit at all
        bare = [Word(w.text.replace("�", ""), w.x0, w.y0, w.x1, w.y1) for w in words]
        self.assertEqual({(r.typ_mohm, r.max_mohm) for r in st._page_rows(3, bare)}, {(None, None)})
        # (b) a block whose units disagree lends none: an Ohm cell on the 5 V row
        five = next(w for w in words if w.text == "5" and 225 < w.y0 < 227)
        clash = words + [Word("Ω", 528.0, five.y0, 534.0, five.y1)]
        rows = {(r.vgs_v, r.temperature_c): r for r in st._page_rows(3, clash)}
        self.assertEqual((rows[(4.5, 25.0)].typ_mohm, rows[(4.5, 25.0)].unit_source), (None, "unreadable"))
        self.assertFalse(any(r.unit_source.startswith("row_block") for r in rows.values()))

    def test_c1_smd_version_rows_are_read_and_qualified(self):
        rows = parse_rdson_spec_rows(_pdf("IPP100N06S2L05_IFX"))
        got = sorted((r.vgs_v, r.typ_mohm, r.max_mohm, r.qualifier) for r in rows)
        self.assertEqual(got, [(4.5, 4.0, 5.6, "SMD version"), (4.5, 4.3, 5.9, ""),
                               (10.0, 3.2, 4.4, "SMD version"), (10.0, 3.5, 4.7, "")])

    def test_c2_drain_current_in_milliamps(self):
        rows = {r.vgs_v: r for r in parse_rdson_spec_rows(_pdf("BS107P_Diodes"))}
        self.assertEqual({v: (r.id_a, r.id_unit) for v, r in rows.items()}, {2.6: (0.025, "mA"), 5.0: (0.1, "mA")})
        rows = {r.vgs_v: r for r in parse_rdson_spec_rows(_pdf("TN0606_Microchip"))}
        self.assertEqual({v: r.id_a for v, r in rows.items()}, {3.0: 0.25, 5.0: 0.75, 10.0: 0.75})
        # amps stay amps
        self.assertEqual({r.id_unit for r in parse_rdson_spec_rows(_pdf("IRLB8748_IFX"))}, {"A"})

    def test_c3_rows_of_other_parameters_are_not_rdson_rows(self):
        # Goford gFS "VGS = 5V, ID = 50A ... S"; Toshiba V(BR)DSX "ID = 10 mA, VGS = -20 V";
        # Microchip "Change in RDS(ON) with Temperature ... %/C VGS = 10V, ID = 1A".
        self.assertEqual(sorted(r.vgs_v for r in parse_rdson_spec_rows(_pdf("G020N03T_Goford"))), [4.5, 10.0])
        self.assertEqual(sorted(r.vgs_v for r in parse_rdson_spec_rows(_pdf("TK3R1E04PL_Toshiba"))), [4.5, 10.0])
        tn = parse_rdson_spec_rows(_pdf("TN0104_Microchip"))
        self.assertEqual(sorted((r.vgs_v, r.typ_mohm) for r in tn), [(3.0, 5000.0), (5.0, 2300.0)])
        self.assertFalse(any("%" in r.row_text for pdf in ("TN0606_Microchip", "VN2406_Microchip")
                             for r in parse_rdson_spec_rows(_pdf(pdf))))


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class LocatorTests(unittest.TestCase):
    def test_gate_voltage_caption_over_a_drain_current_axis_is_refused(self):
        # onsemi NDP6060L Figure 2 "On-Resistance Variation with Gate Voltage and
        # Drain Current" plots RDS against ID: the caption names the gate, the
        # x axis does not.
        with _scratch("rdsvgs-loc-") as tmp:
            located, refused, _ = locate_panels(_pdf("NDP6060L_onsemi"), Path(tmp))
        self.assertEqual(located, [])
        self.assertTrue(any("not the gate voltage" in r.reason for r in refused), refused)

    def test_ir_fig12_is_located_on_its_own_x_axis(self):
        with _scratch("rdsvgs-loc-") as tmp:
            located, _refused, _ = locate_panels(_pdf("IRLB8721_IFX"), Path(tmp))
        self.assertEqual([(p.page, p.diagram) for p in located], [(6, "12")])
        self.assertIn("Gate", located[0].x_axis_title)

    # --- batch_all class A (2026-09-30) -------------------------------------------

    def _locate(self, name: str, upright: bool = True):
        from datasheet_chart_digitizer.rdson_gate_voltage_locate import upright_pdf
        with _scratch("rdsvgs-loc-") as tmp:
            pdf = upright_pdf(_pdf(name), Path(tmp)) if upright else _pdf(name)
            return locate_panels(pdf, Path(tmp))

    def test_a1_axis_titles_overrule_a_transfer_caption(self):
        # Diodes Inc. captions its RDS(on)-vs-VGS Figure 4 "Typical Transfer Characteristic(s)"
        for name in ("DMN3023L_Diodes", "DMN4008LFG_Diodes", "DMT6009LCT_Diodes"):
            located, _refused, _ = self._locate(name)
            self.assertEqual([(p.page, p.diagram) for p in located], [(3, "4")], name)
            self.assertTrue(located[0].identity.startswith("axis_titles:"), located[0].identity)
            self.assertIn("Transfer", located[0].identity)

    def test_a1_known_bad_real_transfer_and_rds_vs_id_charts_are_not_taken(self):
        import pymupdf
        from datasheet_chart_digitizer import rdson_gate_voltage_locate as loc
        from datasheet_chart_digitizer.find_charts import run_text_bbox
        pdf = _pdf("DMN3023L_Diodes")
        page = run_text_bbox(pdf)[2]
        with _scratch("rdsvgs-loc-") as tmp, pymupdf.open(pdf) as document:
            frames = loc._page_frames(pdf, page, document[2], Path(tmp))
            captions = {c.number: c for c in loc._numbered_captions(page, [])}
            everything = list(captions.values())
            side = loc._caption_side(everything, frames)
            self.assertEqual(side, "below")
            # Figure 2 is a real transfer chart (y "I D, DRAIN CURRENT"): never RDS(VGS)
            self.assertIsNone(loc._axis_titled_frame(captions["2"], frames, page, document[2], side, everything))
            # without the page's caption side, Figure 2's caption grabs the Figure 4
            # frame 24 pt below it: the side rule is what keeps the number right
            hazard = loc._axis_titled_frame(captions["2"], frames, page, document[2], None, everything)
            self.assertIsNotNone(hazard)
            self.assertAlmostEqual(hazard[0][1], 305.8, delta=1.0)
        # AON7524 Figure 3 (RDS vs ID; caption continues "Gate Voltage (Note E)" under the frame)
        located, _refused, _ = self._locate("AON7524_AOS")
        self.assertEqual([(p.page, p.diagram) for p in located], [(3, "5")])

    def test_a2_rotated_page_is_located_on_an_upright_copy(self):
        located, _refused, _ = self._locate("ZVNL120A_Diodes")
        self.assertEqual([(p.page, p.title) for p in located], [(3, "On-resistance vs gate-source voltage")])
        self.assertEqual(located[0].frame_source, "vector_hairline_grid")
        # known-bad: the stored (sideways) PDF itself yields nothing
        located, _refused, _ = self._locate("ZVNL120A_Diodes", upright=False)
        self.assertEqual(located, [])

    def test_a3_caption_naming_both_axes_stands_in_for_an_unreadable_x_title(self):
        located, refused, _ = self._locate("HSP4048_LCSC_C701029")
        self.assertEqual([(p.page, p.diagram) for p in located], [(3, "2")])
        self.assertTrue(located[0].identity.startswith("caption_names_both_axes:"), located[0].identity)
        # the frame ABOVE the caption (captions sit under their charts on this
        # page), not the Fig.4 gate-charge frame 13 pt below it
        self.assertLess(located[0].frame_pt[3], located[0].caption_bbox_pt[1])
        from datasheet_chart_digitizer.rdson_gate_voltage_locate import caption_names_both_axes
        self.assertTrue(caption_names_both_axes("On-Resistance vs G-S Voltage"))
        for title in ("On-Resistance Variation with Gate Voltage and Drain Current",
                      "Normalized R DSON vs T J", "On-Resistance vs. Drain Current and Gate Voltage",
                      "On-Resistance vs Gate-Source Voltage and Temperature"):
            self.assertFalse(caption_names_both_axes(title), title)


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = _scratch("rdsvgs-e2e-")
        cls.out = Path(cls._tmp.name)
        cls.results = {}
        for name in ("CSD17306Q5A_TI", "IRLB8748_IFX", "FDP8870_onsemi", "AO3400A_UMW_C347475"):
            cls.results[name], _ = dcache.digitize_pdf(_pdf(name), cls.out)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_ti_figure7_matches_table_at_assumed_temperature_kind(self):
        row = _panel(self.results["CSD17306Q5A_TI"], 4, "7")
        self.assertEqual(row["status"], "review_required", row.get("reasons"))
        self.assertEqual(row["trace_method"], "vector")
        self.assertEqual(row["validation"]["verdict"], "consistent_at_assumed_conditions")
        for anchor in row["validation"]["anchors"]:
            self.assertEqual(anchor["verdict"], "consistent")
            self.assertLess(abs(anchor["residual_vs_typ_mohm"]), 0.05)
        cold, hot = _curve(row, 25.0), _curve(row, 125.0)
        self.assertEqual(cold["id_a"], 22.0)
        self.assertEqual(cold["parameter_binding"]["temperature_c"], "leader_line")
        self.assertAlmostEqual(_readout(cold, 3.3)["rds_mohm"], 3.88, delta=0.05)
        self.assertAlmostEqual(_readout(hot, 3.3)["rds_mohm"], 5.51, delta=0.05)
        self.assertEqual(_readout(cold, 3.3)["note"], "typical curve, not a guaranteed value")

    def test_readouts_left_of_the_plotted_span_are_not_on_chart(self):
        row = _panel(self.results["IRLB8748_IFX"], 6, "12")
        self.assertEqual(row["status"], "review_required", row.get("reasons"))  # approximate 4.5 V current anchor
        cold = _curve(row, 25.0)
        self.assertGreater(cold["vgs_range_v"][0], 3.3)
        for vgs in (2.5, 3.3):
            self.assertEqual(_readout(cold, vgs)["status"], "not_on_chart")
            self.assertIsNone(_readout(cold, vgs)["rds_mohm"])
        self.assertAlmostEqual(_readout(cold, 4.5)["rds_mohm"], 6.11, delta=0.08)
        # The curve ends ON the 10 V frame: that end is data, not a clip.
        self.assertGreaterEqual(cold["vgs_range_v"][1], 9.99)
        rows = {a["row"]["vgs_v"]: a for a in row["validation"]["anchors"]}
        self.assertEqual(rows[10.0]["verdict"], "consistent")
        self.assertAlmostEqual(rows[10.0]["chart_mohm"], 4.20, delta=0.08)

    def test_filled_outline_curves_with_leaderless_id_labels_go_to_review(self):
        # "ID = 35A" / "ID = 1A" have no leaders; since F5-1 the IDs were bound
        # by the ID order rule. Since F-v2-1 the steep heads are traced to the
        # frame, and "ID = 35A" now sits nearer its own (traced) head: bound by
        # proximity, the 1 A by elimination -- the same values, 35 A on the
        # curve with the higher RDS(on). The coincident tails keep the panel in review.
        row = _panel(self.results["FDP8870_onsemi"], 5, "9")
        self.assertEqual(row["trace_method"], "vector")
        self.assertEqual({c["trace_method"] for c in row["curves"]}, {"vector_filled_outline"})
        self.assertEqual(len(row["curves"]), 2)
        self.assertEqual(row["status"], "review_required")
        self.assertEqual({c["parameter_binding"]["id_a"] for c in row["curves"]},
                         {"proximity_far_curve_behind_near_curve", "elimination_last_label_last_curve"})
        high = max(row["curves"], key=lambda c: next(r["rds_mohm"] for r in c["readouts"] if r["vgs_v"] == 3.3))
        self.assertEqual(high["id_a"], 35.0)
        self.assertEqual(sorted(c["id_a"] for c in row["curves"]), [1.0, 35.0])
        self.assertTrue(any("coincident_with" in r for r in row["reasons"]), row["reasons"])
        self.assertEqual(row["validation"]["verdict"], "verified")
        self.assertTrue(all(c["temperature_c"] == 25.0 for c in row["curves"]))
        self.assertTrue(all(c["parameter_binding"]["temperature_c"] == "page_note_unless_otherwise_noted" for c in row["curves"]))

    def test_a_chart_that_contradicts_its_table_is_inconsistent_not_ok(self):
        # UMW's AO3400A table claims 20 mOhm typ at 4.5 V; its chart shows ~27.
        row = _panel(self.results["AO3400A_UMW_C347475"], 3, "5")
        self.assertEqual(row["validation"]["verdict"], "inconsistent")
        self.assertEqual(row["status"], "review_required")

    def test_points_csv_and_overlay_are_written(self):
        row = _panel(self.results["CSD17306Q5A_TI"], 4, "7")
        self.assertTrue((self.out / row["overlay"]).is_file())
        lines = (self.out / row["points_csv"]).read_text().splitlines()
        self.assertEqual(lines[0].split(","), ["curve_index", "curve_label", "usable", "temperature_c", "temperature_kind", "id_a", "vgs_v", "rds_mohm", "crop_x_px", "crop_y_px"])
        self.assertGreater(len(lines), 500)


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class GuardTests(unittest.TestCase):
    """Known-bad inputs built from the real CSD17306Q5A Figure 7 extraction."""

    def _run(self, **patches) -> dict:
        with _scratch("rdsvgs-guard-") as tmp, _patched(patches):
            results, _ = rgv.digitize_pdf(_pdf("CSD17306Q5A_TI"), Path(tmp))
        return _panel(results, 4, "7")

    def test_unreadable_unit_refuses(self):
        def no_unit_words(page, ocr_words):
            words = rgv_panel_words(page, ocr_words)
            return replace(words, words=[
                w for w in words.words
                if not any(glyph in w.text for glyph in ("\u03a9", "\u2126", "\uf057", "m:"))
            ])

        row = self._run(**{"rdson_gate_voltage._panel_words": no_unit_words,
                           "rdson_gate_voltage_axes._ocr_rotated_gutter": lambda *a, **k: ""})
        self.assertEqual(row["status"], "refused")
        self.assertTrue(row["reasons"][0].startswith("rdson_unit_unreadable"), row["reasons"])

    def test_axes_without_tick_labels_refuse(self):
        def no_numbers(words, transform, shape):
            return [l for l in rgv_text_labels(words, transform, shape) if not any(ch.isdigit() for ch in l.text)]

        row = self._run(**{"rdson_gate_voltage._text_labels": no_numbers,
                           "rdson_gate_voltage._ocr_crop_labels": lambda *a, **k: [],
                           "rdson_gate_voltage._ocr_axis_band_labels": lambda *a, **k: []})
        self.assertEqual(row["status"], "refused")
        self.assertTrue(row["reasons"][0].startswith("axes_uncalibrated"), row["reasons"])

    def test_a_trace_rising_with_vgs_refuses_and_a_worse_one_still_does(self):
        for depth in (1.0, 3.0):
            def mirrored(page, transform, plot, depth=depth):
                found, swatches, leaders = rgv_vector_traces(page, transform, plot)
                mid = 0.5 * (plot.y0 + plot.y1)
                for trace in found:
                    trace.points_px = [(x, max(plot.y0 + 5, min(plot.y1 - 5, mid - depth * (y - mid)))) for x, y in trace.points_px]
                return found, swatches, leaders

            row = self._run(**{"rdson_gate_voltage.vector_traces": mirrored})
            self.assertEqual(row["status"], "refused", depth)
            self.assertIn("rises_with_vgs", row["reasons"][0])

    def test_a_trace_riding_the_frame_needs_review(self):
        def clipped(page, transform, plot):
            found, swatches, leaders = rgv_vector_traces(page, transform, plot)
            lift = 0.72 * (plot.y1 - plot.y0)
            for trace in found:
                trace.points_px = [(x, max(plot.y0 + 1.0, y - lift)) for x, y in trace.points_px]
            return found, swatches, leaders

        row = self._run(**{"rdson_gate_voltage.vector_traces": clipped})
        self.assertEqual(row["status"], "review_required")
        self.assertTrue(any("runs_along_the_frame" in r for r in row["reasons"]), row["reasons"])

    def test_missing_table_anchor_is_not_evaluable_and_never_ok(self):
        row = self._run(**{"rdson_gate_voltage.parse_rdson_spec_rows": lambda pdf: []})
        self.assertEqual(row["validation"]["verdict"], "not_evaluable")
        self.assertEqual(row["status"], "review_required")

    def test_a_worse_table_mismatch_never_flips_back_to_verified(self):
        for factor in (1.3, 2.0, 10.0):
            def corrupted(pdf, factor=factor):
                return [replace(r, typ_mohm=r.typ_mohm * factor, max_mohm=r.max_mohm * factor)
                        for r in rgv_parse_rows(pdf)]

            row = self._run(**{"rdson_gate_voltage.parse_rdson_spec_rows": corrupted})
            self.assertEqual(row["validation"]["verdict"], "inconsistent", factor)
            self.assertEqual(row["status"], "review_required", factor)

    def test_a_x5_tick_scale_misread_is_flagged_implausible(self):
        def scaled(words, transform, shape):
            out = []
            for label in rgv_text_labels(words, transform, shape):
                if label.text.isdigit() and label.cy > 0.87 * shape[0]:  # the x tick row only
                    label = TextLabel(str(int(label.text) * 5), label.cx, label.cy, label.x0, label.x1)
                out.append(label)
            return out

        row = self._run(**{"rdson_gate_voltage._text_labels": scaled})
        self.assertNotEqual(row["status"], "ok")
        self.assertTrue(any("implausible" in r for r in row["reasons"]), row["reasons"])

    def test_swapped_temperature_labels_are_unbound_by_physics(self):
        with _scratch("rdsvgs-swap-") as tmp:
            results, _ = dcache.digitize_pdf(_pdf("CSD17306Q5A_TI"), Path(tmp))
        row = _panel(results, 4, "7")
        plot = PlotBox(**row["plot_box_px"])
        traces = [
            traces_mod.Trace([tuple(p) for p in c["points_px"]], "vector", ("s", c["curve_index"]))
            for c in row["curves"]
        ]
        hot_index = next(i for i, c in enumerate(row["curves"]) if c["temperature_c"] == 125.0)
        hot, cold = traces[hot_index], traces[1 - hot_index]

        def label_beside(trace, value):
            x, y = trace.points_px[len(trace.points_px) // 2]
            return traces_mod.Label(f"TJ = {value}°C", x + 4, y - 30, x + 90, y - 14, {"temperature_c": float(value)})

        # 25 C printed beside the hot curve and 125 C beside the cold one.
        notes = traces_mod.bind_labels(traces, [label_beside(hot, 25), label_beside(cold, 125)], [], plot)
        self.assertIn("temperature_binding_contradicts_rdson_order", notes)
        self.assertTrue(all(t.params["temperature_c"] is None for t in traces))


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class ExtremeTemperatureTests(unittest.TestCase):
    def test_hottest_label_on_a_curve_with_another_curve_above_is_unbound(self):
        # Winsok WSR3090 failure mode, rebuilt on real TI traces: the only
        # label that binds says 125 C (the hottest printed) but sits on the
        # LOWER curve, and the 25 C label binds to nothing.
        with _scratch("rdsvgs-extreme-") as tmp:
            results, _ = dcache.digitize_pdf(_pdf("CSD17306Q5A_TI"), Path(tmp))
        row = _panel(results, 4, "7")
        plot = PlotBox(**row["plot_box_px"])
        traces = [
            traces_mod.Trace([tuple(p) for p in c["points_px"]], "raster")
            for c in row["curves"]
        ]
        lower = max(traces, key=lambda t: sum(p[1] for p in t.points_px) / len(t.points_px))
        x, y = lower.points_px[len(lower.points_px) // 2]
        labels = [
            traces_mod.Label("TJ=125°C", x + 2, y + 3, x + 60, y + 20, {"temperature_c": 125.0}),
            traces_mod.Label("TJ=25°C", plot.x0 + 5, plot.y0 + 5, plot.x0 + 60, plot.y0 + 20, {"temperature_c": 25.0}),
            # a third printed temperature keeps elimination from binding the rest
            traces_mod.Label("TJ=100°C", plot.x0 + 5, plot.y0 + 30, plot.x0 + 60, plot.y0 + 45, {"temperature_c": 100.0}),
        ]
        notes = traces_mod.bind_labels(traces, labels, [], plot)
        self.assertIn("extreme_temperature_binding_has_a_curve_beyond_it", notes)
        self.assertTrue(all(t.params["temperature_c"] is None for t in traces))


class RobustLadderTests(unittest.TestCase):
    # Recorded 2026-09-28 from the real RQ3E180AJ (Rohm) Fig.12 crop: the
    # page-OCR tick readings of its 0..5 V axis in 0.5 V steps. OCR dropped
    # every decimal point and read "1" as "4".
    PLOT = PlotBox(244, 52, 988, 840)
    READINGS = [("0", 243), ("05", 317), ("4", 390), ("15", 467), ("2", 540), ("25", 614),
                ("3", 690), ("35", 764), ("4", 838), ("45", 912), ("5", 987)]

    def _labels(self, readings):
        return [TextLabel(text, x, 880.0, x - 8, x + 8) for text, x in readings]

    def test_decimal_dropped_readings_choose_the_half_volt_ladder(self):
        dropped: list[str] = []
        axis = axes._axis_or_robust(self._labels(self.READINGS), self.PLOT, "x", dropped)
        self.assertAlmostEqual(axis.value(987), 5.0, delta=0.05)
        self.assertAlmostEqual(axis.value(614), 2.5, delta=0.05)
        self.assertIn("05", dropped)

    def test_the_x10_ladder_alone_is_not_taken_as_certain(self):
        # Only the decimal-dropped readings survive: they are collinear on their
        # own (0, 5, 15 ... 45). The ladder may be accepted by the fit, but the
        # plausibility guard in the digitizer must then flag a 45 V gate axis.
        only_bad = [r for r in self.READINGS if r[0] in {"0", "05", "15", "25", "35", "45"}]
        dropped: list[str] = []
        try:
            axis = axes._axis_or_robust(self._labels(only_bad), self.PLOT, "x", dropped)
        except RuntimeError:
            return
        self.assertGreater(max(t.value for t in axis.ticks), rgv.MAX_PLAUSIBLE_VGS_AXIS_V)


class ReadoutTests(unittest.TestCase):
    def test_readouts_never_extrapolate(self):
        points = [(3.5 + 0.01 * i, 10.0 - 0.01 * i) for i in range(600)]
        got = {r["vgs_v"]: r for r in readouts(points, False)}
        self.assertEqual(got[2.5]["status"], "not_on_chart")
        self.assertEqual(got[3.3]["status"], "not_on_chart")
        self.assertEqual(got[4.5]["status"], "read")
        self.assertAlmostEqual(got[4.5]["rds_mohm"], 9.0, places=3)

    def test_a_gap_in_the_trace_is_not_bridged(self):
        points = [(2.0 + 0.01 * i, 8.0) for i in range(100)] + [(4.0 + 0.01 * i, 6.0) for i in range(100)]
        got = {r["vgs_v"]: r for r in readouts(points, False, gaps=[(2.99, 4.0)])}
        self.assertEqual(got[3.3]["status"], "not_in_extracted_trace")
        self.assertIn("gap", got[3.3]["detail"])


@unittest.skipUnless(HAVE_DS and HAVE_TESSERACT, "needs the datasheet folder and tesseract")
class RasterTests(unittest.TestCase):
    def test_rohm_embedded_image_chart_reads_both_printed_curves(self):
        with _scratch("rdsvgs-raster-") as tmp:
            results, _ = dcache.digitize_pdf(_pdf("RQ3E110AJ_Rohm"), Path(tmp))
        row = _panel(results, 7, "12")
        self.assertEqual(row["trace_method"], "raster")
        self.assertEqual([t["value"] for t in row["calibration"]["x_axis"]["ticks"]][-1], 10.0)
        # Two printed curves (11.0 A, 5.5 A) run as one band and then one line
        # (review F4-4): two curves, coincident over the shared line. Both
        # ID leaders end where the lines touch, so the IDs are bound by the
        # ID order rule (F5-1), and the panel goes to review, never ok.
        self.assertEqual(len(row["curves"]), 2)
        self.assertEqual(sorted(c["id_a"] for c in row["curves"]), [5.5, 11.0])
        for curve in row["curves"]:
            self.assertEqual(curve["parameter_binding"]["id_a"], "id_order_rule")
            self.assertAlmostEqual(_readout(curve, 4.5)["rds_mohm"], 8.62, delta=0.25)
            self.assertTrue(curve["coincident_with"])
        self.assertEqual(row["status"], "review_required")


@unittest.skipUnless(HAVE_DS, f"datasheet folder not present: {DS}")
class CliTests(unittest.TestCase):
    def test_cli_writes_the_manifest(self):
        with _scratch("rdsvgs-cli-") as tmp:
            code = rgv.main(["--pdf", str(_pdf("CSD17306Q5A_TI")), "--out", tmp])
            manifest = json.loads((Path(tmp) / "rdson_gate_voltage.json").read_text())
        self.assertEqual(code, 0)
        self.assertEqual(manifest["kind"], "rds_on_vgs")
        self.assertEqual(sorted(p["diagram"] for p in manifest["panels"]), ["7", "t519"])


# Unpatched originals, captured once so a patch can wrap the real producer.
rgv_panel_words = rgv._panel_words
rgv_text_labels = rgv._text_labels
rgv_vector_traces = rgv.vector_traces
rgv_parse_rows = rgv.parse_rdson_spec_rows


class _patched:
    def __init__(self, patches: dict):
        self._patches = [
            patch(f"datasheet_chart_digitizer.{target}", replacement)
            for target, replacement in patches.items()
        ]

    def __enter__(self):
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in reversed(self._patches):
            item.stop()
        return False


if __name__ == "__main__":
    unittest.main()
