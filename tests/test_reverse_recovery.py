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
    def test_verdict_states(self):
        self.assertEqual(rrv.scale_verdict([], []), "unverified")
        self.assertEqual(rrv.scale_verdict([dict(err=0.1)], []), "verified")
        self.assertEqual(rrv.scale_verdict([dict(err=0.3)], []), "FAIL")
        self.assertEqual(
            rrv.scale_verdict([dict(err=0.05)],
                              ["temp order violates physics for Qrr: ..."]),
            "FAIL")

    def test_interp_refuses_extrapolation(self):
        self.assertIsNone(rrv.interp_curve([(1.0, 1.0), (2.0, 2.0)], 3.0))


if __name__ == "__main__":
    unittest.main()
