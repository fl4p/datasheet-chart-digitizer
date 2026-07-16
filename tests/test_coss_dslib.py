"""Gate calibration for the dslib triple export (export-coss-dslib).

Every acceptance gate is exercised against a KNOWN-BAD input and must be seen to
fire — a guard never seen to fire is not a guard. The pass case uses a synthetic
power-law chart whose anchors agree with the traces by construction.
"""

import csv
import json
import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from datasheet_chart_digitizer.coss_dslib import (
    _pin_anchor_knots,
    export_manifest,
    export_row,
)


def _coss(v: float) -> float:
    return 4000.0 / math.sqrt(1.0 + v)


def _crss(v: float) -> float:
    return 900.0 / (1.0 + v) ** 0.8


def _write_points_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trace", "x_px", "y_px", "x_norm", "y_norm_log_axis", "vds_V", "cap_pF"])
        for i in range(400):
            v = 0.05 + i * (80.0 - 0.05) / 399.0
            w.writerow(["Coss", i, 0, 0, 0, v, _coss(v)])
            w.writerow(["Crss", i, 0, 0, 0, v, _crss(v)])
            w.writerow(["Ciss", i, 0, 0, 0, v, 9000.0])


def _good_row(points_rel: str = "points/part/p08_d11.points.csv") -> dict:
    return {
        "part": "TESTFET",
        "diagram": "11",
        "points": points_rel,
        "overlay": "overlays/part/p08_d11.overlay.png",
        "pdf": "/nonexistent/TESTFET.pdf",
        "axis_calibration_trusted": True,
        "trace_validation_status": "pass",
        "trace_validation_reasons": [],
        "qoss_validation_status": "pass",
        "qoss_validation_error": None,
        "qoss_metrics": {"Qoss_pc": 12345.0},
        "anchors": {
            "Ciss": {"value_pf": 9000.0, "vds_v": 40.0},
            "Coss": {"value_pf": _coss(40.0), "vds_v": 40.0},
            "Crss": {"value_pf": _crss(40.0), "vds_v": 40.0},
        },
    }


class CossDslibExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.base = Path(self._tmp.name)
        _write_points_csv(self.base / "points/part/p08_d11.points.csv")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_valid_chart_passes_with_dslib_triples(self) -> None:
        res = export_row(_good_row(), self.base)
        self.assertEqual(res.status, "pass", res.reasons)
        self.assertGreaterEqual(res.knots, 4)   # smooth power law needs few adaptive knots
        self.assertEqual(res.curve[0][0], 0.0)          # explicit Vds=0 knot
        self.assertGreater(res.curve[-1][0], 79.0)      # covers the full chart span
        for v, coss, crss in res.curve[1:]:
            self.assertLess(abs(coss / _coss(v) - 1.0), 0.05)
            self.assertLess(abs(crss / _crss(v) - 1.0), 0.08)
        self.assertLess(abs(res.anchor_check["Coss"]["rel_error"]), 0.02)

    def test_anchor_voltage_is_pinned_as_knot(self) -> None:
        # dslib consumers interpolate linearly, so the spec-table Vds must be a knot —
        # a chord between straddling log-space knots reads the convex knee high.
        res = export_row(_good_row(), self.base)
        self.assertEqual(res.status, "pass", res.reasons)
        at40 = [k for k in res.curve if k[0] == 40.0]
        self.assertEqual(len(at40), 1)
        self.assertLess(abs(at40[0][1] / _coss(40.0) - 1.0), 0.02)
        self.assertLess(abs(at40[0][2] / _crss(40.0) - 1.0), 0.02)

    def test_anchor_pinning_skips_existing_and_out_of_range(self) -> None:
        pts = ([0.05, 40.0, 80.0], [4000.0, 620.0, 450.0])
        crss = ([0.05, 40.0, 80.0], [900.0, 29.0, 21.0])
        curve = [(0.0, 4000.0, 900.0), (40.0, 620.0, 29.0), (80.0, 450.0, 21.0)]
        anchors = {"Coss": {"value_pf": 620.0, "vds_v": 40.0},
                   "Crss": {"value_pf": 29.0, "vds_v": 200.0}}
        out = _pin_anchor_knots(curve, anchors, pts, crss, 0.8, 0.8)
        self.assertEqual(out, curve)   # 40 V already a knot; 200 V outside the range

    def test_untrusted_axis_calibration_is_rejected(self) -> None:
        row = _good_row()
        row["axis_calibration_trusted"] = False
        res = export_row(row, self.base)
        self.assertEqual(res.status, "rejected")
        self.assertIn("axis_calibration_not_trusted", res.reasons)
        self.assertEqual(res.curve, [])

    def test_suspect_trace_validation_is_rejected(self) -> None:
        row = _good_row()
        row["trace_validation_status"] = "suspect"
        row["trace_validation_reasons"] = ["crss_not_bottom"]
        res = export_row(row, self.base)
        self.assertEqual(res.status, "rejected")
        self.assertTrue(any(r.startswith("trace_validation:suspect") for r in res.reasons))

    def test_failed_qoss_validation_is_rejected(self) -> None:
        row = _good_row()
        row["qoss_validation_status"] = "fail"
        row["qoss_validation_error"] = "Qoss off by 40%"
        res = export_row(row, self.base)
        self.assertEqual(res.status, "rejected")
        self.assertTrue(any(r.startswith("qoss_validation:fail") for r in res.reasons))

    def test_missing_anchor_is_rejected_not_skipped(self) -> None:
        # Absence of evidence must never export a curve: no Crss table anchor -> reject.
        row = _good_row()
        del row["anchors"]["Crss"]
        res = export_row(row, self.base)
        self.assertEqual(res.status, "rejected")
        self.assertIn("missing_crss_anchor", res.reasons)

    def test_anchor_disagreement_is_rejected(self) -> None:
        # A 30%-off table anchor means the trace (or axis) is wrong -> reject.
        row = _good_row()
        row["anchors"]["Coss"]["value_pf"] = _coss(40.0) * 1.3
        res = export_row(row, self.base)
        self.assertEqual(res.status, "rejected")
        self.assertTrue(any(r.startswith("coss_anchor_mismatch") for r in res.reasons))

    def test_missing_points_csv_is_rejected(self) -> None:
        row = _good_row("points/part/does_not_exist.points.csv")
        res = export_row(row, self.base)
        self.assertEqual(res.status, "rejected")
        self.assertIn("missing_points_csv", res.reasons)

    def test_rejection_collects_all_reasons(self) -> None:
        row = _good_row()
        row["axis_calibration_trusted"] = False
        row["qoss_validation_status"] = "fail"
        res = export_row(row, self.base)
        self.assertGreaterEqual(len(res.reasons), 2)

    def test_manifest_export_writes_per_part_json(self) -> None:
        manifest = self.base / "capacitance_digitization.json"
        manifest.write_text(json.dumps([_good_row()]))
        out = self.base / "out"
        results = export_manifest(manifest, out)
        self.assertEqual([r.status for r in results], ["pass"])
        payload = json.loads((out / "TESTFET_d11.dslib_coss.json").read_text())
        self.assertEqual(payload["status"], "pass")
        self.assertTrue((out / "dslib_coss_manifest.json").exists())

    # ---- optional Ciss export ------------------------------------------------

    def test_valid_chart_also_exports_ciss_pairs(self) -> None:
        res = export_row(_good_row(), self.base)
        self.assertEqual(res.status, "pass", res.reasons)
        self.assertEqual(res.ciss_status, "pass", res.ciss_reasons)
        self.assertEqual(res.ciss_curve[0][0], 0.0)         # explicit Vds=0 hold knot
        self.assertGreater(res.ciss_curve[-1][0], 79.0)
        for _v, ciss in res.ciss_curve[1:]:
            self.assertLess(abs(ciss / 9000.0 - 1.0), 0.05)
        self.assertTrue(any(k[0] == 40.0 for k in res.ciss_curve))  # anchor V pinned
        self.assertLess(abs(res.anchor_check["Ciss"]["rel_error"]), 0.02)

    def test_ciss_anchor_mismatch_withholds_ciss_but_not_triple(self) -> None:
        # A wrong Ciss anchor must fire the Ciss gate — and ONLY the Ciss gate.
        row = _good_row()
        row["anchors"]["Ciss"]["value_pf"] = 9000.0 * 1.3
        res = export_row(row, self.base)
        self.assertEqual(res.status, "pass", res.reasons)
        self.assertEqual(res.ciss_status, "rejected")
        self.assertEqual(res.ciss_curve, [])
        self.assertTrue(any(r.startswith("ciss_anchor_mismatch") for r in res.ciss_reasons))

    def test_missing_ciss_anchor_rejects_ciss_not_absent(self) -> None:
        # Trace exists but cannot be validated -> rejected, never silently exported.
        row = _good_row()
        del row["anchors"]["Ciss"]
        res = export_row(row, self.base)
        self.assertEqual(res.status, "pass", res.reasons)
        self.assertEqual(res.ciss_status, "rejected")
        self.assertIn("missing_ciss_anchor", res.ciss_reasons)
        self.assertEqual(res.ciss_curve, [])

    def test_chart_without_any_ciss_evidence_is_absent(self) -> None:
        pts = self.base / "points/part/no_ciss.points.csv"
        pts.parent.mkdir(parents=True, exist_ok=True)
        with pts.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["trace", "x_px", "y_px", "x_norm", "y_norm_log_axis",
                        "vds_V", "cap_pF"])
            for i in range(400):
                v = 0.05 + i * (80.0 - 0.05) / 399.0
                w.writerow(["Coss", i, 0, 0, 0, v, _coss(v)])
                w.writerow(["Crss", i, 0, 0, 0, v, _crss(v)])
        row = _good_row("points/part/no_ciss.points.csv")
        del row["anchors"]["Ciss"]
        res = export_row(row, self.base)
        self.assertEqual(res.status, "pass", res.reasons)
        self.assertEqual(res.ciss_status, "absent")
        self.assertEqual(res.ciss_curve, [])

    def test_ciss_below_crss_is_rejected(self) -> None:
        # Cgs = Ciss - Crss must stay positive; a crossing means trace mis-assignment.
        pts = self.base / "points/part/crossing.points.csv"
        with pts.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["trace", "x_px", "y_px", "x_norm", "y_norm_log_axis",
                        "vds_V", "cap_pF"])
            for i in range(400):
                v = 0.05 + i * (80.0 - 0.05) / 399.0
                w.writerow(["Coss", i, 0, 0, 0, v, _coss(v)])
                w.writerow(["Crss", i, 0, 0, 0, v, _crss(v)])
                w.writerow(["Ciss", i, 0, 0, 0, v, _crss(v) * 0.5])  # below Crss
        row = _good_row("points/part/crossing.points.csv")
        row["anchors"]["Ciss"] = {"value_pf": _crss(40.0) * 0.5, "vds_v": 40.0}
        res = export_row(row, self.base)
        self.assertEqual(res.status, "pass", res.reasons)
        self.assertEqual(res.ciss_status, "rejected")
        self.assertIn("ciss_not_above_crss", res.ciss_reasons)

    def test_ciss_disjoint_from_crss_is_rejected_not_trivially_passed(self) -> None:
        # Ciss captured only in a high-V band with no span shared with Crss: the
        # crossing gate would otherwise compare boundary-clamped constants and pass.
        pts = self.base / "points/part/disjoint.points.csv"
        with pts.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["trace", "x_px", "y_px", "x_norm", "y_norm_log_axis",
                        "vds_V", "cap_pF"])
            for i in range(400):
                v = 0.05 + i * (80.0 - 0.05) / 399.0
                w.writerow(["Coss", i, 0, 0, 0, v, _coss(v)])
                w.writerow(["Crss", i, 0, 0, 0, v, _crss(v)])
            for i in range(50):
                v = 90.0 + i * 0.2                      # 90-100 V only: no overlap
                w.writerow(["Ciss", i, 0, 0, 0, v, 9000.0])
        row = _good_row("points/part/disjoint.points.csv")
        row["anchors"]["Ciss"] = {"value_pf": 9000.0, "vds_v": 95.0}
        res = export_row(row, self.base)
        self.assertEqual(res.status, "pass", res.reasons)
        self.assertEqual(res.ciss_status, "rejected")
        self.assertIn("ciss_crss_no_overlap", res.ciss_reasons)
        self.assertEqual(res.ciss_curve, [])

    def test_rejected_chart_marks_ciss_chart_rejected(self) -> None:
        row = _good_row()
        row["axis_calibration_trusted"] = False
        res = export_row(row, self.base)
        self.assertEqual(res.status, "rejected")
        self.assertEqual(res.ciss_status, "rejected")
        self.assertEqual(res.ciss_reasons, ["chart_rejected"])
        self.assertEqual(res.ciss_curve, [])


if __name__ == "__main__":
    unittest.main()
