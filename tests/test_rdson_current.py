from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from datasheet_chart_digitizer.rdson_current import (
    DIAG_AXIS_IDENTITY,
    DIAG_CURRENT_SPAN,
    DIAG_MONOTONIC,
    DIAG_RDS_SPAN,
    _RDS_CURRENT_TITLE_RE,
    _axis_identity_is_evidenced,
    _is_rdson_current_panel,
    _local_axis_text,
    _rdson_current_direction_is_evidenced,
    digitize_pdf,
)
from datasheet_chart_digitizer.find_charts import process_pdf
from datasheet_chart_digitizer.rdson_temperature import (
    DIAG_LEGEND_VGS_MISSING,
    DIAG_NO_FULL_SPAN_CURVE,
)


class RdsonCurrentTests(unittest.TestCase):
    def test_title_family_is_narrow(self) -> None:
        self.assertIsNotNone(_RDS_CURRENT_TITLE_RE.search("On-resistance vs. Drain Current"))
        self.assertIsNotNone(
            _RDS_CURRENT_TITLE_RE.search(
                "Drain-source on-state resistance as a function of drain current"
            )
        )
        self.assertIsNotNone(
            _RDS_CURRENT_TITLE_RE.search(
                "On-Resistance Variation vs. Drain Current and Gate Voltage"
            )
        )
        self.assertIsNone(
            _RDS_CURRENT_TITLE_RE.search("Normalized on Resistance vs. Junction Temperature")
        )

    def test_compact_formula_title_routes_only_current_direction(self) -> None:
        pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/toshiba/XPW4R10ANB.pdf")
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory(prefix="rdson-formula-") as tmp:
            panel = next(
                row for row in process_pdf(pdf, Path(tmp), dpi=180)
                if row.diagram == 88
            )
        self.assertTrue(_rdson_current_direction_is_evidenced(panel, ""))

    def test_adjacent_normalized_panel_does_not_hide_infineon_current_formula(self) -> None:
        pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon/IPA60R125CFD7.pdf")
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory(prefix="rdson-infineon-formula-") as tmp:
            panel = next(
                row for row in process_pdf(pdf, Path(tmp), dpi=180)
                if row.page == 8 and row.diagram == 7
            )
            self.assertTrue(_is_rdson_current_panel(panel))
            results = digitize_pdf(pdf, Path(tmp), dpi=180)
        result = next(row for row in results if row["panel"]["diagram"] == 7)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["diagnostics"], [DIAG_LEGEND_VGS_MISSING])

    def test_spd03_extracts_source_faithful_current_curve(self) -> None:
        root = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets",
            )
        )
        pdf = root / "hxy/SPD03N50C3ATMA1-HXY.pdf"
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory() as tmp:
            results = digitize_pdf(pdf, Path(tmp), dpi=220)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual((result["panel"]["page"], result["panel"]["diagram"]), (3, 3))
        self.assertEqual(result["point_columns"], ["drain_current_a", "rdson_mohm"])
        self.assertEqual([curve["gate_voltage_v"] for curve in result["curves"]], [10.0])
        points = result["curves"][0]["points"]
        self.assertLess(points[0][0], 1.2)
        self.assertGreater(points[-1][0], 7.8)
        self.assertGreater(points[-1][1] - points[0][1], 900.0)
        self.assertNotIn(DIAG_AXIS_IDENTITY, result["diagnostics"])
        self.assertNotIn(DIAG_CURRENT_SPAN, result["diagnostics"])
        self.assertNotIn(DIAG_RDS_SPAN, result["diagnostics"])
        self.assertNotIn(DIAG_MONOTONIC, result["diagnostics"])

    def test_new_current_directions_route_but_fail_closed_on_unbound_curves(self) -> None:
        root = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets",
            )
        )
        cases = {
            "onsemi/FDD390N15ALZ.pdf": (3, DIAG_NO_FULL_SPAN_CURVE),
            "infineon/IPT020N13NM6.pdf": (6, DIAG_LEGEND_VGS_MISSING),
        }
        if not all((root / relative).exists() for relative in cases):
            self.skipTest("optional current-routing PDFs are not configured")
        for relative, (diagram, diagnostic) in cases.items():
            with self.subTest(pdf=relative), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                panels = process_pdf(root / relative, out, dpi=220)
                panel = next(row for row in panels if row.diagram == diagram)
                self.assertTrue(_is_rdson_current_panel(panel))
                self.assertTrue(_axis_identity_is_evidenced(panel))
                results = digitize_pdf(root / relative, out, dpi=220)
                result = next(row for row in results if row["panel"]["diagram"] == diagram)
                self.assertEqual(result["status"], "refused")
                self.assertEqual(result["diagnostics"], [diagnostic])

    def test_normalized_current_panel_does_not_enter_absolute_mohm_plugin(self) -> None:
        root = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets",
            )
        )
        pdf = root / "onsemi/BSS138.pdf"
        if not pdf.exists():
            self.skipTest("optional normalized-current PDF is not configured")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            panel = next(
                row
                for row in process_pdf(pdf, out, dpi=220)
                if row.page == 3 and row.diagram == 2
            )
            local = _local_axis_text(panel)
            self.assertTrue(_rdson_current_direction_is_evidenced(panel, local))
            self.assertIn("normalized", local.lower())
            self.assertFalse(_is_rdson_current_panel(panel))
            results = digitize_pdf(pdf, out, dpi=220)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
