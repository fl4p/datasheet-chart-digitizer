"""A finder RDS(on) panel never vanishes without a status (bench round 1, bug 2).

LSIC1MO120E0160 p5 Figure 11 ("On-resistance vs. Drain Current", y axis
"Normalized On-resistance") was filed ``rds_on`` by the finder. The mOhm
RDS(on)(I_D) plugin skips normalized axes and RDS(on)(Tj) does not match the
caption, so it ended with no result and no refusal. Likewise a caption that
RDS(on)(Tj) matched but whose panel failed its ownership check was skipped
silently.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from datasheet_chart_digitizer import rdson_current, rdson_temperature
from datasheet_chart_digitizer.find_charts import ChartPanel, DiagramTitle, PageText

LSIC = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/littelfuse/LSIC1MO120E0160.pdf")
ISC040 = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon/ISC040N10NM8.pdf")


class UnroutedRdsPanelTests(unittest.TestCase):
    def test_normalized_rdson_vs_id_is_refused_explicitly(self) -> None:
        if not LSIC.exists():
            self.skipTest(f"missing local corpus fixture: {LSIC}")
        with tempfile.TemporaryDirectory() as tmp:
            results, errors = rdson_current.digitize_pdf_fail_closed(LSIC, Path(tmp), dpi=180)
        self.assertEqual(results, [])
        records = [e for e in errors if (e["page"], e["diagram"]) == (5, 11)]
        self.assertEqual(len(records), 1, errors)
        self.assertEqual(records[0]["status"], "refused")
        self.assertEqual(records[0]["reason"], rdson_current.UNROUTED_RDS_PANEL)
        self.assertIn("normalized RDS(on) vs I_D", records[0]["error"])

    def test_every_finder_rds_panel_ends_with_a_status(self) -> None:
        # Infineon Diagram 8 (RDS(on) vs VGS, no "vs VGS" caption) is owned by
        # no digitizer: it must surface as a refusal, never vanish.
        if not ISC040.exists():
            self.skipTest(f"missing local corpus fixture: {ISC040}")
        with tempfile.TemporaryDirectory() as tmp:
            results, errors = rdson_current.digitize_pdf_fail_closed(ISC040, Path(tmp), dpi=180)
            t_results, t_errors = rdson_temperature.digitize_pdf_fail_closed(ISC040, Path(tmp), dpi=180)
        seen = {(r["panel"]["page"], r["panel"]["diagram"]) for r in [*results, *t_results]}
        seen |= {(e["page"], e["diagram"]) for e in [*errors, *t_errors]}
        for key in [(8, 6), (8, 8), (9, 9)]:
            self.assertIn(key, seen)

    def test_rdson_temperature_ownership_failure_is_a_refusal_not_a_skip(self) -> None:
        title = DiagramTitle(9, "Normalized drain-source on resistance", (45, 100, 260, 108), "x")
        panel = ChartPanel(
            "x.pdf", "x", 1, 9, title.title, "rds_on", (0, 0, 1, 1), (0, 0, 1, 1),
            "crop.png", title.title, "", [],
        )
        page = PageText(1, 612, 792, [])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(rdson_temperature, "run_text_bbox", return_value=[page]), \
                patch.object(rdson_temperature, "render_page", return_value=Path(tmp) / "p.png"), \
                patch.object(rdson_temperature, "_build_panel", return_value=(panel, None, None)):
            errors: list[dict[str, object]] = []
            results = rdson_temperature._digitize_rds_family(
                Path("x.pdf"), Path(tmp), 180,
                title_selector=lambda _page: [title],
                crop_group="c", overlay_group="o", manifest_name="m.json",
                point_columns=[], validation=lambda *a: [], overlay_drawer=lambda *a: None,
                success_diagnostics=[], thresholds={}, error_kind="rds_on_temperature",
                panel_selector=lambda _panel: False, errors=errors,
            )
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["reason"], rdson_temperature.PANEL_OWNERSHIP_UNPROVEN)
        self.assertEqual((errors[0]["page"], errors[0]["diagram"]), (1, 9))


if __name__ == "__main__":
    unittest.main()


class SubscriptSplitFormulaTests(unittest.TestCase):
    """ISC040N10NM8 / IPP050N03LF2S Diagram 9 print R_DS(on)=f(T_j) with the
    subscripts on a lower baseline, just below the panel box."""

    WORDS = [
        ("T", 174.6, 385.1, 180.0, 401.9), ("j", 180.4, 390.5, 183.0, 402.2),
        ("[°C]", 185.8, 385.1, 200.0, 401.9),
        ("R", 45.4, 409.9, 51.0, 425.9), ("DS(on)", 51.6, 416.5, 70.0, 426.5),
        ("=f(", 71.6, 410.2, 83.0, 425.8), ("T", 83.6, 409.9, 89.0, 425.9),
        ("j", 89.5, 416.5, 91.0, 426.5), ("),", 91.3, 410.2, 98.0, 425.8),
        ("I", 99.6, 409.9, 102.0, 425.9), ("D", 102.5, 416.5, 106.0, 426.5),
        ("=25", 106.8, 410.2, 124.0, 425.8), ("A,", 125.4, 410.2, 135.0, 425.8),
    ]

    def test_formula_line_reassembles_subscripts(self) -> None:
        from datasheet_chart_digitizer.find_charts import Word

        words = [Word(*w) for w in self.WORDS]
        text = rdson_temperature._subscript_merged_formula_text(words, (40, 160, 300, 432))
        self.assertEqual(text, "R DS(on) =f( T j ), I D =25 A,")
        self.assertIsNotNone(rdson_temperature._RDS_TEMPERATURE_FORMULA_RE.search(text))

    def test_isc040_diagram9_is_digitized_not_dropped(self) -> None:
        if not ISC040.exists():
            self.skipTest(f"missing local corpus fixture: {ISC040}")
        with tempfile.TemporaryDirectory() as tmp:
            results, errors = rdson_temperature.digitize_pdf_fail_closed(ISC040, Path(tmp), dpi=180)
        d9 = [r for r in results if (r["panel"]["page"], r["panel"]["diagram"]) == (9, 9)]
        self.assertEqual(len(d9), 1, errors)
        self.assertIn("=f(", d9[0]["panel"]["formula"])
        self.assertFalse(any(e.get("reason") == rdson_temperature.PANEL_OWNERSHIP_UNPROVEN for e in errors))
