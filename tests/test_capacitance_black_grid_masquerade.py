"""Black-grid rails must never masquerade as capacitance trace bands.

ST/goford/MCC draw grid and curves in the same dark ink at 5-8% total
coverage, under the historical 10% ink trigger for grid separation; whole
gridline sets then survived as per-column stroke centers and stole band
slots (top50-fugu2 review: a gridline digitized as Ciss with every identity
shifted one band down). Separation now also triggers on the structural
signature -- several full-span dark rules -- and partially-eroded frame
strokes near the mask edge are blanked.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from datasheet_chart_digitizer.capacitance_grid_mask import (
    _dark_grid_rule_evidence,
    _remove_frame_residual_rails,
)


class DarkGridRuleEvidenceTests(unittest.TestCase):
    def test_gray_grid_mask_shows_no_rules(self) -> None:
        # Infineon-style: only curves enter the dark mask; no full-span rows.
        mask = np.zeros((200, 400), dtype=np.uint8)
        for x in range(400):
            mask[50 + x // 20, x] = 1
        self.assertFalse(_dark_grid_rule_evidence(mask))

    def test_full_grid_in_both_orientations_triggers(self) -> None:
        mask = np.zeros((200, 400), dtype=np.uint8)
        for y in (40, 100, 160):
            mask[y, :] = 1
        for x in (80, 200, 320):
            mask[:, x] = 1
        self.assertTrue(_dark_grid_rule_evidence(mask))

    def test_horizontal_rules_alone_do_not_trigger(self) -> None:
        # XRS200N12T: a flat Ciss riding the top decade line plus two printed
        # horizontal rules read as 3 row "rules" on a gray-grid chart; the
        # opening would erode exactly that flat trace. One orientation alone
        # is not a grid.
        mask = np.zeros((200, 400), dtype=np.uint8)
        for y in (40, 100, 160):
            mask[y, :] = 1
        self.assertFalse(_dark_grid_rule_evidence(mask))

    def test_frame_adjacent_rules_are_not_interior(self) -> None:
        mask = np.zeros((200, 400), dtype=np.uint8)
        for y in (0, 2, 197, 199):
            mask[y, :] = 1
        for x in (0, 2, 397, 399):
            mask[:, x] = 1
        self.assertFalse(_dark_grid_rule_evidence(mask))

    def test_dotted_rules_stay_below_occupancy(self) -> None:
        # Huayi-style dotted grids never reach 80% row occupancy.
        mask = np.zeros((200, 400), dtype=np.uint8)
        for y in (40, 100, 160):
            mask[y, ::3] = 1
        self.assertFalse(_dark_grid_rule_evidence(mask))

    def test_empty_mask_is_no_evidence(self) -> None:
        self.assertFalse(_dark_grid_rule_evidence(np.zeros((0, 0), dtype=np.uint8)))


class FrameResidualRailTests(unittest.TestCase):
    def test_edge_residual_is_blanked(self) -> None:
        # Onsemi FDPF2D3N10C: eroded top-frame stroke at ~50-70% occupancy
        # just inside the blanked margin.
        mask = np.zeros((200, 400), dtype=np.uint8)
        mask[6, : int(400 * 0.6)] = 1
        cleaned = _remove_frame_residual_rails(mask)
        self.assertEqual(int(cleaned[6].sum()), 0)

    def test_mid_plot_flat_trace_survives(self) -> None:
        # A genuinely flat source trace in the plot interior is NOT frame
        # residue and must never be blanked by this pass.
        mask = np.zeros((200, 400), dtype=np.uint8)
        mask[90, :] = 1
        cleaned = _remove_frame_residual_rails(mask)
        self.assertEqual(int(cleaned[90].sum()), 400)

    def test_sparse_edge_row_survives(self) -> None:
        # A curve clipping the top corner covers only a small fraction of the
        # row; below 50% occupancy nothing is blanked.
        mask = np.zeros((200, 400), dtype=np.uint8)
        mask[6, :100] = 1
        cleaned = _remove_frame_residual_rails(mask)
        self.assertEqual(int(cleaned[6].sum()), 100)


_REFRESH = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/out/fugu2-100v-LS1p/coss-review-top50-2026-07-28-refresh"
)


@unittest.skipUnless(_REFRESH.exists(), "local refresh packet not available")
class BlackGridEndToEndTests(unittest.TestCase):
    @staticmethod
    def _extract(part: str, diagram: int):
        from PIL import Image
        from datasheet_chart_digitizer.capacitance_traces import (
            extract_trace_components,
            find_plot_box,
        )

        charts = json.loads((_REFRESH / "charts.json").read_text())
        chart = next(
            c for c in charts if c["part"] == part and c["diagram"] == diagram
        )
        gray = np.asarray(Image.open(_REFRESH / chart["crop_png"]).convert("L"))
        plot = find_plot_box(gray)
        return extract_trace_components(gray, plot), plot

    def test_mcp100n10y_recovers_ordered_identities(self) -> None:
        traces, _plot = self._extract("MCP100N10Y-TP", 5)
        by_name = {t.name: t for t in traces}
        self.assertEqual(set(by_name), {"Ciss", "Coss", "Crss"})
        # Correct identity ordering at a shared x: Ciss above Coss above Crss
        # (smaller y is higher on the page).
        def y_at(name, x):
            pts = by_name[name].points
            return min(pts, key=lambda p: abs(p[0] - x))[1]
        x_probe = min(p[0] for p in by_name["Coss"].points) + 20
        self.assertLess(y_at("Ciss", x_probe), y_at("Coss", x_probe))
        self.assertLess(y_at("Coss", x_probe), y_at("Crss", x_probe))

    def test_stp310n10f7_refuses_instead_of_serving_a_gridline(self) -> None:
        # Before: the 14000 pF gridline was digitized as Ciss and every
        # identity shifted one band down. The separation now erases the black
        # grid; the thin ST curves do not survive it either, so this must
        # refuse loudly -- never emit three "traces" led by a gridline.
        with self.assertRaises(RuntimeError):
            self._extract("STP310N10F7", 8)


if __name__ == "__main__":
    unittest.main()
