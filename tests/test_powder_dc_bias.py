"""digitize-powder-dc-bias against KDM's printed Sendust law, three renderings.

The KDM Sendust DC-bias panel prints its own law %mu = 100/(a + b*H^c) with one
(a, b, c) row per grade. Those printed rows are the ground truth here (see
fixtures/kdm_sendust_dc_bias/PROVENANCE.md). The residuals are recomputed in this
test from cases.json, not taken from the module's own report.

The PDFs are not committed. A missing PDF or a SHA-256 mismatch SKIPS LOUDLY; it
never passes. The class is validated (Fab approved the overlays, 2026-11-24): status `ok`.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np

from datasheet_chart_digitizer import powder_dc_bias as M

FIX = Path(__file__).parent / "fixtures" / "kdm_sendust_dc_bias"
CASES = json.loads((FIX / "cases.json").read_text())
TOL = CASES["tolerance_pct_points"]
PERMS = CASES["perms_left_to_right"]
_HASH: dict[str, bool] = {}


def _rows_arg(rows: dict) -> str:
    return ";".join(f"{k}:{a:g},{b:g},{c:g}" for k, (a, b, c) in rows.items())


def _run(case: dict, out: Path, perms=None) -> tuple[int, dict]:
    args = [case["pdf"], "--page", str(case["page"]), "--mode", case["mode"],
            "--perms", ",".join(str(p) for p in (perms or PERMS)),
            "--printed", _rows_arg(case["printed_rows"]), "--out", str(out), "--tag", case["id"],
            *case["args"]]
    for b in case.get("blanks", []):
        args += ["--blank", ",".join(str(v) for v in b)]
    with redirect_stdout(io.StringIO()):
        rc = M.main(args)
    return rc, json.loads((out / f"{case['id']}-dcbias.json").read_text())


def _residuals(points, row) -> np.ndarray:
    a, b, c = row
    pts = np.asarray(points, float)
    return 100.0 / (a + b * pts[:, 0] ** c) - pts[:, 1]


def _closest_row(points, rows: dict) -> str:
    return min(rows, key=lambda k: float(np.median(np.abs(_residuals(points, rows[k])))))


class _SourceCase:
    CASE: dict = {}

    @classmethod
    def setUpClass(cls):
        pdf = Path(cls.CASE["pdf"])
        if not pdf.exists():
            raise unittest.SkipTest(f"REFERENCE NOT CHECKED: {cls.CASE['id']}: {pdf} missing")
        if str(pdf) not in _HASH:
            _HASH[str(pdf)] = hashlib.sha256(pdf.read_bytes()).hexdigest() == cls.CASE["pdf_sha256"]
        if not _HASH[str(pdf)]:
            raise unittest.SkipTest(f"REFERENCE NOT CHECKED: {cls.CASE['id']}: {pdf} SHA-256 differs")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name)
        cls.rc, cls.res = _run(cls.CASE, cls.out)
        cls.curves = {int(c["perm"]): c for c in cls.res["curves"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_status_never_ok_and_mode(self):
        self.assertEqual(self.rc, 0)
        self.assertEqual(self.res["status"], "ok")
        self.assertEqual(self.res["mode"], self.CASE["mode"])

    def test_overlay_written(self):
        ov = Path(self.res["overlay"])
        self.assertTrue(ov.exists() and ov.with_suffix(".png").exists())

    def test_every_curve_within_tolerance_of_its_printed_row(self):
        rows = self.CASE["printed_rows"]
        for p in PERMS:
            pts = self.curves[p]["points"]
            self.assertGreaterEqual(len(pts), self.CASE["min_levels"], f"{p}u levels")
            r = _residuals(pts, rows[str(p)])
            with self.subTest(perm=p):
                self.assertLessEqual(float(np.sqrt((r ** 2).mean())), TOL["rms_max"])
                self.assertLessEqual(float(np.abs(r).max()), TOL["abs_max"])

    def test_curve_to_row_binding(self):
        rows = self.CASE["printed_rows"]
        for p in PERMS:
            with self.subTest(perm=p):
                self.assertEqual(_closest_row(self.curves[p]["points"], rows), str(p))
        self.assertTrue(self.res["binding_check"]["ok"])


def _case(cid):
    return next(c for c in CASES["cases"] if c["id"] == cid)


class KS184Vector(_SourceCase, unittest.TestCase):
    CASE = _case("ks184_vector")


class LjfKSRaster(_SourceCase, unittest.TestCase):
    CASE = _case("ljf_ks_raster")


class CatalogP14Raster(_SourceCase, unittest.TestCase):
    CASE = _case("catalog_p14_raster")


class KnownBad(unittest.TestCase):
    """The checks above must fail on the defects they guard against."""

    def setUp(self):
        self.case = _case("ks184_vector")
        if not Path(self.case["pdf"]).exists():
            self.skipTest(f"REFERENCE NOT CHECKED: {self.case['pdf']} missing")

    def test_wrong_curve_order_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            rc, res = _run(self.case, Path(d), perms=list(reversed(PERMS)))
        self.assertEqual(rc, 2)
        self.assertEqual(res["status"], "refused")
        self.assertFalse(res["binding_check"]["ok"])

    def test_tolerance_catches_a_wrong_row(self):
        with tempfile.TemporaryDirectory() as d:
            _, res = _run(self.case, Path(d))
        pts = next(c for c in res["curves"] if c["perm"] == 125)["points"]
        a, b, c = self.case["printed_rows"]["125"]
        r = _residuals(pts, (a, b * 1.25, c))      # a 25 % error in b: ~3 %-pt mid-curve
        self.assertGreater(float(np.abs(r).max()), TOL["abs_max"])


class OverlayContract(unittest.TestCase):
    """An overlay needs the digitized curves and >= 2 labelled ticks per axis, else none."""

    def _render(self, to_px, curves):
        img = np.full((400, 600, 3), 255, np.uint8)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "o.webp"
            try:
                M.render_overlay(img, to_px, (1.0, 1000.0), (0.0, 100.0), 10.0, curves, {}, [125.0], "t", path)
            finally:
                written = path.exists() or path.with_suffix(".png").exists()
        return written

    @staticmethod
    def _inside(H, P):
        return 50 + 500 * math.log10(H) / 3, 350 - 3 * P

    def test_good_overlay_written(self):
        self.assertTrue(self._render(self._inside, [([5.0, 50.0], [95.0, 40.0])]))

    def test_refused_without_two_x_ticks(self):
        def off_x(H, P):
            x, y = self._inside(H, P)
            return (x if H < 1.5 else x + 5000), y
        with self.assertRaises(M.OverlayRefused):
            self._render(off_x, [([5.0, 50.0], [95.0, 40.0])])

    def test_refused_without_two_y_ticks(self):
        def off_y(H, P):
            x, y = self._inside(H, P)
            return x, (y if P < 5 else y - 5000)
        with self.assertRaises(M.OverlayRefused):
            self._render(off_y, [([5.0, 50.0], [95.0, 40.0])])

    def test_refused_without_a_curve(self):
        with self.assertRaises(M.OverlayRefused):
            self._render(self._inside, [([float("nan")], [50.0])])


if __name__ == "__main__":
    unittest.main()
