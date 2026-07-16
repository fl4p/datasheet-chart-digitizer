"""Regression tests for the reverse-recovery digitizer plugin.

Pinned to AOT414 (Alpha & Omega, Rev 1 May 2012), the first HUMAN-VERIFIED
sample (overlays + values reviewed 2026-07-13). If the extraction drifts,
these numbers move — do not relax bands without re-verifying overlays.

The end-to-end tests need the local datasheet library; they skip cleanly when
it is absent so the unit-level tests still run anywhere.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from datasheet_chart_digitizer import reverse_recovery as rr
from datasheet_chart_digitizer import reverse_recovery_validation as rrv

AOT414 = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/ao/AOT414.pdf")
AOB414 = AOT414.with_name("AOB414.pdf")     # doubled text layer variant
AOT418L = AOT414.with_name("AOT418L.pdf")   # stroked-grid variant (unsupported)


def _digitize(pdf: Path):
    out = Path(tempfile.mkdtemp(prefix="rr-test-"))
    manifest = rr.digitize_pdf(pdf, out)
    return manifest, out


@unittest.skipUnless(AOT414.exists(), "local datasheet library not available")
class Aot414EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, cls.out = _digitize(AOT414)
        cls.by_fig = {m["number"]: m for m in cls.manifest if "number" in m}

    def curve(self, fig: int, quantity: str, temp: float):
        m = self.by_fig[fig]
        for c in m["curves"]:
            if c["quantity"] == quantity and c["temp_c"] == temp:
                rows = [ln.split(",") for ln in
                        Path(c["csv"]).read_text().splitlines()[1:]]
                return [(float(a), float(b)) for a, b in rows]
        raise AssertionError(f"fig{fig} {quantity}@{temp}C not found")

    def test_four_panels_with_four_curves(self):
        self.assertEqual(sorted(self.by_fig), [17, 18, 19, 20])
        for fig, m in self.by_fig.items():
            self.assertEqual(len(m["curves"]), 4, (fig, m["curves"]))

    def test_x_quantities(self):
        self.assertEqual(self.by_fig[17]["x_quantity"], "IF")
        self.assertEqual(self.by_fig[19]["x_quantity"], "didt")

    def test_scale_verdicts(self):
        # fig17/19 anchor + cross-panel checks pass; fig18/20 FAIL loudly on
        # the known S-softness identity defect (curves overlap ~exactly)
        self.assertEqual(self.by_fig[17]["scale"], "verified")
        self.assertEqual(self.by_fig[19]["scale"], "verified")
        self.assertEqual(self.by_fig[18]["scale"], "FAIL")
        self.assertEqual(self.by_fig[20]["scale"], "FAIL")

    def test_table_anchor_agreement(self):
        # spec table: Qrr(IF=20A, 500A/us) = 82 nC typ; chart within 25%
        checks = [k for k in self.by_fig[19]["scale_checks"]
                  if k["kind"] == "table" and k["quantity"] == "Qrr"]
        self.assertTrue(checks)
        self.assertLess(max(abs(k["err"]) for k in checks), 0.25)

    def test_pinned_qrr_values(self):
        """Human-verified data points (2026-07-13 review)."""
        q25 = self.curve(19, "Qrr", 25.0)
        q125 = self.curve(19, "Qrr", 125.0)
        self.assertAlmostEqual(rrv.interp_curve(q25, 500), 76.8, delta=3.0)
        self.assertAlmostEqual(rrv.interp_curve(q125, 500), 101.4, delta=3.0)
        f17 = self.curve(17, "Qrr", 125.0)
        self.assertAlmostEqual(rrv.interp_curve(f17, 30.0), 155.0, delta=5.0)

    def test_temperature_ratio_band(self):
        """The N_TAU-relevant measurement: Qrr(125)/Qrr(25) ~ 1.2-1.5, NOT ~2."""
        q25 = self.curve(19, "Qrr", 25.0)
        q125 = self.curve(19, "Qrr", 125.0)
        for dd in (300, 500, 700):
            r = rrv.interp_curve(q125, dd) / rrv.interp_curve(q25, dd)
            self.assertTrue(1.15 < r < 1.5, (dd, r))

    def test_manifest_is_strict_json(self):
        s = json.dumps(self.manifest)
        json.loads(s)  # would raise on NaN with a strict parser downstream
        self.assertNotIn("NaN", s)


@unittest.skipUnless(AOB414.exists(), "local datasheet library not available")
class SiblingDatasheets(unittest.TestCase):
    def test_aob414_doubled_text_layer_recovers(self):
        manifest, _ = _digitize(AOB414)
        ok = [m for m in manifest if m.get("scale") == "verified"]
        self.assertGreaterEqual(len(ok), 2, manifest)

    def test_aot418l_stroked_grid_fails_loud(self):
        """Unsupported drawing style must produce ERRORS, not silence."""
        manifest, _ = _digitize(AOT418L)
        self.assertTrue(manifest)
        self.assertTrue(all("error" in m for m in manifest), manifest)


class VerdictUnit(unittest.TestCase):
    FULL = [dict(quantity=q, temp_c=t, n_points=10)
            for q in ("Qrr", "Irm") for t in (25.0, 125.0)]

    def test_verdict_states(self):
        self.assertEqual(rrv.scale_verdict([], []), "unverified")
        self.assertEqual(rrv.scale_verdict([dict(err=0.1)], []), "verified")
        self.assertEqual(rrv.scale_verdict([dict(err=0.3)], []), "FAIL")
        self.assertEqual(
            rrv.scale_verdict([dict(err=0.05)],
                              ["temp order violates physics for Qrr: ..."]),
            "FAIL")

    def test_verdict_requires_structural_completeness(self):
        ok = rrv.scale_verdict([dict(err=0.1)], [], curves=self.FULL)
        self.assertEqual(ok, "verified")
        # missing curve -> incomplete even with clean anchors
        self.assertEqual(
            rrv.scale_verdict([dict(err=0.1)], [], curves=self.FULL[:3]),
            "incomplete")
        # duplicate identity
        dup = self.FULL[:3] + [dict(self.FULL[0])]
        self.assertEqual(rrv.scale_verdict([dict(err=0.1)], [], curves=dup),
                         "incomplete")
        # unidentified / uncalibrated curve
        broken = self.FULL[:3] + [dict(quantity="?", temp_c=None, n_points=0)]
        self.assertEqual(rrv.scale_verdict([dict(err=0.1)], [], curves=broken),
                         "incomplete")
        # a failing anchor still outranks incompleteness
        self.assertEqual(rrv.scale_verdict([dict(err=0.4)], [], curves=self.FULL[:2]),
                         "FAIL")

    def test_interp_refuses_extrapolation(self):
        self.assertIsNone(rrv.interp_curve([(1.0, 1.0), (2.0, 2.0)], 3.0))


class AxisSideResolution(unittest.TestCase):
    """The dual-axis quantity->side mapping must be DERIVED from printed unit
    labels and REFUSED when unconfirmable — not silently taken from the assumed
    AO QUANTITY_AXIS layout (which would scale a flipped/unknown chart through the
    wrong calibration and emit plausible-but-wrong values)."""

    class _Rect:
        def __init__(self):
            self.x0, self.y0, self.x1, self.y1 = 100.0, 50.0, 300.0, 250.0

    class _Ax:
        def value(self, p):  # noqa: D401 - identity stand-in; scaling not under test
            return p

    @staticmethod
    def _word(text, x0, x1, y0=140.0, y1=160.0):
        return (x0, y0, x1, y1, text, 0, 0, 0)

    def _panel(self, quantities=("Qrr", "Irm"), dual=True):
        plot = self._Rect()
        curves = [rr.Curve(quantity=q, temp_c=t, axis=rr.QUANTITY_AXIS[q],
                           points_pt=[(1.0, 1.0)])
                  for q in quantities for t in (25.0, 125.0)]
        return rr.Panel(number=1, title="t", plot=plot, x_axis=self._Ax(),
                        y_left=self._Ax(), y_right=self._Ax() if dual else None,
                        x_quantity="IF", curves=curves)

    def _verdict(self, panel):
        cmeta = [dict(quantity=c.quantity, temp_c=c.temp_c, n_points=1)
                 for c in panel.curves]
        return rrv.scale_verdict([dict(err=0.05)], panel.warnings, curves=cmeta)

    # left band: label right-edge just left of plot.x0 (100); right band: label
    # left-edge just right of plot.x1 (300)
    def _left(self, text):
        return self._word(text, 60.0, 90.0)

    def _right(self, text):
        return self._word(text, 310.0, 340.0)

    def test_flipped_labels_self_correct(self):
        p = self._panel()
        # (A) on the LEFT, (nC) on the RIGHT — opposite of the AO layout
        rr._assign_axis_sides(p, [self._left("(A)"), self._right("(nC)")])
        got = {c.quantity: (c.axis, c.axis_basis) for c in p.curves}
        self.assertEqual(got["Qrr"], ("right", "unit-label"))
        self.assertEqual(got["Irm"], ("left", "unit-label"))
        rrv.verify_axis_sides(p, [self._left("(A)"), self._right("(nC)")],
                              rr.QUANTITY_UNIT)
        self.assertEqual(self._verdict(p), "verified")  # confirmed, just flipped

    def test_absent_labels_refuse(self):
        """The core hole: a dual-axis chart with NO unit labels must NOT be
        trusted on the assumed layout — it fails loudly."""
        p = self._panel()
        rr._assign_axis_sides(p, [])
        self.assertTrue(all(c.axis_basis == "assumed" for c in p.curves))
        rrv.verify_axis_sides(p, [], rr.QUANTITY_UNIT)
        self.assertTrue(any("axis side unverified" in w for w in p.warnings))
        self.assertEqual(self._verdict(p), "FAIL")

    def test_partial_labels_refuse_unconfirmed_quantity(self):
        # only (A) present (left): Irm binds by label; Qrr has no (nC) -> refused
        p = self._panel()
        rr._assign_axis_sides(p, [self._left("(A)")])
        bases = {c.quantity: c.axis_basis for c in p.curves}
        self.assertEqual(bases["Irm"], "unit-label")
        self.assertEqual(bases["Qrr"], "assumed")
        rrv.verify_axis_sides(p, [self._left("(A)")], rr.QUANTITY_UNIT)
        self.assertEqual(self._verdict(p), "FAIL")

    def test_unitless_S_resolves_by_elimination(self):
        # trr labeled (ns) left -> S takes the vacated right side, confirmed enough
        p = self._panel(quantities=("trr", "S"))
        rr._assign_axis_sides(p, [self._left("(ns)")])
        bases = {c.quantity: (c.axis, c.axis_basis) for c in p.curves}
        self.assertEqual(bases["trr"], ("left", "unit-label"))
        self.assertEqual(bases["S"], ("right", "elimination"))
        rrv.verify_axis_sides(p, [self._left("(ns)")], rr.QUANTITY_UNIT)
        self.assertFalse(any("axis side unverified" in w for w in p.warnings))

    def test_single_axis_panel_not_refused(self):
        # no left/right ambiguity when only one y-axis exists
        p = self._panel(dual=False)
        rr._assign_axis_sides(p, [])
        rrv.verify_axis_sides(p, [], rr.QUANTITY_UNIT)
        self.assertFalse(any("axis side unverified" in w for w in p.warnings))


class BatchHygiene(unittest.TestCase):
    def test_unique_mpns_disambiguates_colliding_stems(self):
        a = Path("/x/ao/PART1.pdf")
        b = Path("/x/mirror/PART1.pdf")
        c = Path("/x/ao/PART2.pdf")
        mpns = rr._unique_mpns([a, b, c])
        self.assertEqual(mpns[c], "PART2")
        self.assertNotEqual(mpns[a], mpns[b])
        self.assertTrue(mpns[a].startswith("PART1__"))

    @unittest.skipUnless(AOT418L.exists(), "local datasheet library not available")
    def test_cli_exits_nonzero_on_errors(self):
        import subprocess
        import sys
        out = tempfile.mkdtemp(prefix="rr-exit-")
        r = subprocess.run(
            [sys.executable, "-m", "datasheet_chart_digitizer.reverse_recovery",
             str(AOT418L), "--out", out],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    @unittest.skipUnless(AOT414.exists(), "local datasheet library not available")
    def test_rerun_keeps_artifacts_and_no_staging_left(self):
        manifest, out = _digitize(AOT414)
        first = sorted(p.name for p in out.glob("AOT414_fig*"))
        manifest2 = rr.digitize_pdf(AOT414, out)
        self.assertEqual(sorted(p.name for p in out.glob("AOT414_fig*")), first)
        self.assertFalse(list(out.glob(".staging-*")))
        for m in manifest2:
            self.assertNotIn(".staging-", m.get("overlay", ""))


if __name__ == "__main__":
    unittest.main()
