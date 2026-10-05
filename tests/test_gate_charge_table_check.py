"""Spec-table Qg cross-check of the gate-charge Qg axis, ordinal rule binding.

Unit tests use synthetic rows/curves/rasters; corpus tests (skipped without the
fetlib library) pin the cases the coordinator measured from PDF vector
geometry: FDMS86500L (rules 117.2 nC, labels 123.3 nC, table 117),
NTMFS4C022NT1G (VGS on the LEFT column, Qg on the rules, table 45.2 nC),
and FDP045N10A-F102 (rules read 56.7 nC against a 54 nC table: refused).
"""

from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from datasheet_chart_digitizer.gate_charge_grid_anchor import ordinal_rule_ticks
from datasheet_chart_digitizer.gate_charge_spec_table import QgSpecRow, parse_qg_spec_rows
from datasheet_chart_digitizer.gate_charge_table_check import read_qg_at_vgs, table_check

DS = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets")


def _row(typ=40.0, res=1.0, vgs=10.0, vd=30.0):
    return QgSpecRow(1, typ, res, vgs, vd, "DD", 10.0, "Qg Total Gate Charge", "", 0.0)


# Qg 0..60 nC over x 100..700 px; VGS 0..10 V over y 500..100 px.
X = [(float(v), 100.0 + 10.0 * v) for v in range(0, 61, 10)]
Y = [(float(v), 500.0 - 40.0 * v) for v in range(0, 11, 2)]
# a straight curve reaching 10 V at 40 nC
CURVE = [(100.0 + 10.0 * q, 500.0 - 40.0 * (q / 4.0)) for q in np.linspace(0, 40, 41)]
SINGLE = {"binding": "single_curve", "curve_count": 1, "curves": [], "labels": []}


class TableCheckUnitTests(unittest.TestCase):
    def test_reads_the_curve_at_the_table_vgs(self):
        read, how = read_qg_at_vgs(CURVE, X, Y, 10.0)
        self.assertAlmostEqual(read, 40.0, delta=0.05)
        read5, _ = read_qg_at_vgs(CURVE, X, Y, 5.0)
        self.assertAlmostEqual(read5, 20.0, delta=0.05)

    def test_agrees_contradicts_and_far_tail(self):
        check = table_check([_row()], per_vdd=SINGLE, legacy_curve=CURVE, x_ticks=X, y_ticks=Y)
        self.assertEqual(check.status, "agrees", check.reason)
        # tolerance 3 % * 40 + 0.5 = 1.7 nC; a stretched/shifted Qg mapping fails,
        # and keeps failing as the error grows (monotone, far tail included)
        for scale in (1.05, 1.1, 1.5, 3.0, 0.95, 0.5):
            bad = [(v, 100.0 + (p - 100.0) * scale) for v, p in X]
            got = table_check([_row()], per_vdd=SINGLE, legacy_curve=CURVE, x_ticks=bad, y_ticks=Y)
            self.assertEqual(got.status, "contradicts", (scale, got.reason))
        for shift in (25.0, 60.0, 300.0, -25.0):
            bad = [(v, p + shift) for v, p in X]
            got = table_check([_row()], per_vdd=SINGLE, legacy_curve=CURVE, x_ticks=bad, y_ticks=Y)
            self.assertEqual(got.status, "contradicts", (shift, got.reason))

    def test_unevaluable_is_never_a_pass(self):
        cases = {
            "no rows": [],
            "no typ": [_row(typ=None)],
            "no vgs": [_row(vgs=None)],
            "no drain voltage": [_row(vd=None)],
        }
        for name, rows in cases.items():
            got = table_check(rows, per_vdd=SINGLE, legacy_curve=CURVE, x_ticks=X, y_ticks=Y)
            self.assertEqual(got.status, "not_evaluable", name)
        # a multi-curve chart with no curve bound to the table voltage
        multi = {"binding": "partly_bound", "curve_count": 2, "labels": [],
                 "curves": [{"index": 0, "vdd_v": 20.0, "gates": [], "curve_px": CURVE},
                            {"index": 1, "vdd_v": None, "gates": [], "curve_px": CURVE}]}
        self.assertEqual(table_check([_row()], per_vdd=multi, legacy_curve=CURVE, x_ticks=X,
                                     y_ticks=Y).status, "not_evaluable")
        # the only curve labelled with another voltage
        other = {"binding": "single_curve", "curve_count": 1, "labels": [],
                 "curves": [{"index": 0, "vdd_v": 48.0, "gates": [], "curve_px": CURVE}]}
        self.assertEqual(table_check([_row()], per_vdd=other, legacy_curve=CURVE, x_ticks=X,
                                     y_ticks=Y).status, "not_evaluable")
        # the curve never reaches the table VGS
        low = [(x, max(y, 300.0)) for x, y in CURVE]
        self.assertEqual(table_check([_row()], per_vdd=SINGLE, legacy_curve=low, x_ticks=X,
                                     y_ticks=Y).status, "not_evaluable")
        # too few ticks
        self.assertEqual(table_check([_row()], per_vdd=SINGLE, legacy_curve=CURVE, x_ticks=X[:1],
                                     y_ticks=Y).status, "not_evaluable")

    def test_curve_by_elimination(self):
        per = {"binding": "partly_bound", "curve_count": 3,
               "labels": [{"vdd_v": 20.0}, {"vdd_v": 30.0}, {"vdd_v": 40.0}],
               "curves": [{"index": 0, "vdd_v": 20.0, "gates": [], "curve_px": [(0, 0), (1, 1)]},
                          {"index": 1, "vdd_v": None, "gates": [], "curve_px": CURVE},
                          {"index": 2, "vdd_v": 40.0, "gates": [], "curve_px": [(0, 0), (1, 1)]}]}
        got = table_check([_row()], per_vdd=per, legacy_curve=None, x_ticks=X, y_ticks=Y)
        self.assertEqual(got.status, "agrees", got.reason)
        self.assertIn("elimination", got.curve)
        # out of order (the free curve is not in the 30 V slot): refused
        per["curves"][0]["vdd_v"], per["curves"][2]["vdd_v"] = 40.0, 20.0
        got = table_check([_row()], per_vdd=per, legacy_curve=None, x_ticks=X, y_ticks=Y)
        self.assertEqual(got.status, "not_evaluable")


def _grid(rules, labels_n=None, size=(520, 820)):
    image = np.full(size, 255, dtype=np.uint8)
    for x in rules:
        image[60:461, int(x - 0.5)] = 0
    return image


class OrdinalRuleTests(unittest.TestCase):
    BOX = (100, 60, 700, 460)

    def test_labels_spaced_unlike_their_grid_bind_in_order(self):
        rules = [100.5 + 100.0 * k for k in range(7)]           # 0..60 nC on the rules
        labels = [(10.0 * k, 100.5 + 92.6 * k) for k in range(7)]  # glyphs 7.4 % tighter
        ticks, note = ordinal_rule_ticks(_grid(rules), labels, self.BOX, orientation="x")
        self.assertIsNotNone(ticks, note)
        for (v, p), r in zip(ticks, rules):
            self.assertAlmostEqual(p, r, delta=0.6)

    def test_refuses_without_unambiguous_identity(self):
        rules = [100.5 + 100.0 * k for k in range(7)]
        labels = [(10.0 * k, 100.5 + 100.0 * k) for k in range(7)]
        # one rule missing
        self.assertIsNone(ordinal_rule_ticks(_grid(rules[:-1]), labels, self.BOX, orientation="x")[0])
        # an extra rule
        self.assertIsNone(ordinal_rule_ticks(_grid(rules + [150.5]), labels, self.BOX, orientation="x")[0])
        # non-uniform rules
        bent = rules[:3] + [r + 12.0 for r in rules[3:]]
        self.assertIsNone(ordinal_rule_ticks(_grid(bent), labels, self.BOX, orientation="x")[0])
        # labels a whole pitch off (the run shifted by one)
        shifted = [(v, p + 100.0) for v, p in labels]
        self.assertIsNone(ordinal_rule_ticks(_grid(rules), shifted, self.BOX, orientation="x")[0])
        # non-arithmetic label values
        odd = [(v if k != 3 else 35.0, p) for k, (v, p) in enumerate(labels)]
        self.assertIsNone(ordinal_rule_ticks(_grid(rules), odd, self.BOX, orientation="x")[0])


def _result(name, page):
    from datasheet_chart_digitizer.gate_charge import digitize_gate_charge
    pdf = DS / f"onsemi/{name}.pdf"
    if not pdf.exists():
        raise unittest.SkipTest(f"missing {pdf}")
    found = [r for r in digitize_gate_charge(pdf, dpi=220, finder_dpi=220) if r.panel.page == page
             and r.status != "rejected_non_gate"]
    assert len(found) == 1, found
    return found[0]


class SpecTableCorpusTests(unittest.TestCase):
    def test_rows(self):
        cases = {
            "onsemi/FDMS86500L": (117.0, 1.0, 10.0, 30.0),
            "onsemi/NTMFS4C022NT1G": (45.2, 0.1, 10.0, 15.0),
            "onsemi/FDPF041N06BL1-F154": (53.0, 1.0, 10.0, 30.0),
        }
        for name, (typ, res, vgs, vd) in cases.items():
            pdf = DS / f"{name}.pdf"
            if not pdf.exists():
                self.skipTest(f"missing {pdf}")
            rows = [r for r in parse_qg_spec_rows(pdf) if r.vgs_v == vgs and r.vdrain_v is not None]
            self.assertEqual(len(rows), 1, (name, parse_qg_spec_rows(pdf)))
            self.assertEqual((rows[0].typ_nc, rows[0].typ_resolution_nc, rows[0].vdrain_v), (typ, res, vd))
        # AO: the drive printed in the row label wins over a merged condition cell
        pdf = DS / "ao/AON7290.pdf"
        if pdf.exists():
            rows = {r.typ_nc: r.vgs_v for r in parse_qg_spec_rows(pdf)}
            self.assertEqual(rows.get(9.0), 4.5)


class TableCheckCorpusTests(unittest.TestCase):
    def test_fdms86500l_rules_verified_labels_known_bad(self):
        r = _result("FDMS86500L", 5)
        self.assertEqual(r.qg_axis_status, "verified", r.x_grid)
        self.assertEqual(r.x_grid["grid_check"]["method"], "ordinal_rules+table_qg")
        self.assertEqual(r.qg_table_check["status"], "agrees")
        self.assertAlmostEqual(r.qg_table_check["read_nc"], 117.0, delta=1.0)
        # known-bad: the label-glyph mapping reads ~123 nC and is contradicted
        rows = parse_qg_spec_rows(r.pdf)
        bad = table_check(rows, per_vdd=r.per_vdd, legacy_curve=r.curve_px,
                          x_ticks=r.x_label_ticks_px, y_ticks=r.y_ticks_px)
        self.assertEqual(bad.status, "contradicts", bad.reason)
        self.assertGreater(bad.read_nc, 121.0)

    def test_ntmfs4c022_left_vgs_column_rules_and_table(self):
        r = _result("NTMFS4C022NT1G", 4)
        self.assertIn("y_axis_rebound_to_gate_titled_column", r.diagnostics)
        self.assertEqual(r.status, "ok")
        self.assertAlmostEqual(r.vpl, 2.8, delta=0.1)
        self.assertEqual(max(v for v, _ in r.y_ticks_px), 12.0)
        self.assertEqual(r.qg_axis_status, "verified")
        self.assertAlmostEqual(r.qg_table_check["read_nc"], 45.2, delta=1.0)
        # Qg on the rules (0 @ 323.94 pt ... 50 @ 529.28 pt), not the glyphs
        scale = 220 / 72.0
        ticks = dict(r.x_ticks_px)
        self.assertAlmostEqual(r.crop_box_pt[0] + (ticks[0.0] - 0.5) / scale, 323.94, delta=0.4)
        self.assertAlmostEqual(r.crop_box_pt[0] + (ticks[50.0] - 0.5) / scale, 529.28, delta=0.4)

    def test_fdp045_rules_contradicted_by_table_stay_unverified(self):
        r = _result("FDP045N10A-F102", 4)
        self.assertEqual(r.qg_table_check["status"], "contradicts")
        self.assertNotEqual(r.qg_axis_status, "verified")
        self.assertEqual(r.to_manifest()["curve_px"], ())
        self.assertIsNotNone(r.to_manifest()["vpl"])


if __name__ == "__main__":
    unittest.main()
