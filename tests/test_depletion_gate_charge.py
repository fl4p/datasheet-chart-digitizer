import unittest
from pathlib import Path

import pymupdf
import pytest

from datasheet_chart_digitizer.gate_charge import (
    _depletion_vpl_is_source_plausible,
    digitize_gate_charge,
    digitize_gate_charge_fail_closed,
)


BSP135 = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon/BSP135.pdf"
)


class DepletionGateChargeTests(unittest.TestCase):
    def test_enhancement_axis_cannot_relax_low_vpl_warning(self):
        curve = [(x, 50) for x in range(10, 80)]
        self.assertFalse(
            _depletion_vpl_is_source_plausible(
                0.2,
                50.0,
                curve,
                (0, 0, 100, 100),
                [(10.0, 0.0), (5.0, 50.0), (0.0, 100.0)],
            )
        )

    @unittest.skipUnless(BSP135.exists(), "local BSP135 unavailable")
    def test_source_seated_depletion_plateau_drops_enhancement_warning(self):
        results = digitize_gate_charge(BSP135, dpi=220, finder_dpi=180)
        result = next(item for item in results if item.panel.diagram == 15)

        self.assertEqual(result.status, "ok")
        # 0.200652 V on the label-centre VGS fit; the grid-seated fit (labels
        # -0.72..+0.07 px from their rules, all 7 verified) serves 0.204702 V.
        self.assertAlmostEqual(result.vpl, 0.204702, delta=0.001)
        self.assertNotIn("vpl_outside_expected_range", result.diagnostics)
        self.assertLess(min(value for value, _pixel in result.y_ticks_px), 0.0)
        self.assertGreater(max(value for value, _pixel in result.y_ticks_px), 0.0)
        self.assertGreaterEqual(len(result.curve_px), 400)


def _zero_spanning_chart(path, vmin, vmax, curve):
    """VGS vmin..vmax (six labels), Qg 0..25 nC, *curve* (nC, V) points."""
    x0, y0, x1, y1 = 150.0, 300.0, 400.0, 500.0
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((150, y0 - 20), "Figure 7. Gate Charge", fontsize=9)
    for i in range(6):
        x = x0 + i * (x1 - x0) / 5
        page.draw_line((x, y0), (x, y1), color=(0, 0, 0), width=1.0 if i in (0, 5) else 0.4)
        t = str(5 * i)
        page.insert_text((x - pymupdf.get_text_length(t, fontsize=8) / 2, y1 + 11), t, fontsize=8)
    page.insert_text((215, y1 + 24), "Qg, Total Gate Charge (nC)", fontsize=8)
    for j in range(6):
        y = y1 - j * (y1 - y0) / 5
        page.draw_line((x0, y), (x1, y), color=(0, 0, 0), width=1.0 if j in (0, 5) else 0.4)
        t = f"{vmin + j * (vmax - vmin) / 5:g}"
        page.insert_text((x0 - 20 - pymupdf.get_text_length(t, fontsize=8), y + 3), t, fontsize=8)
    page.insert_text((x0 - 40, y0 - 12), "VGS (V)", fontsize=8)
    page.insert_text((300, y0 + 30), "VDS = 40 V, ID = 20 A", fontsize=7)

    def p(q, v):
        return (x0 + q / 25 * (x1 - x0), y1 - (v - vmin) / (vmax - vmin) * (y1 - y0))

    page.draw_polyline([p(q, v) for q, v in curve], color=(0, 0, 0), width=1.5)
    doc.save(path)
    return path


# Ticks are page points, the curve crop pixels: mixed, the start VGS read
# depended on dpi and page position (110 dpi rejected a real depletion
# plateau, 220 dpi accepted a plateau on a curve starting at 0 V).
@pytest.mark.parametrize(
    "dpi, vmin, vmax, curve, depletion",
    [
        (110, -4, 6, [(0, -4.0), (6, 0.5), (12, 0.5), (22, 6.0)], True),
        (220, -2, 8, [(0, 0.0), (6, 0.8), (12, 0.8), (22, 8.0)], False),
    ],
)
def test_depletion_start_read_in_one_unit(tmp_path, dpi, vmin, vmax, curve, depletion):
    pdf = _zero_spanning_chart(tmp_path / "chart.pdf", vmin, vmax, curve)
    results, errors = digitize_gate_charge_fail_closed(pdf, dpi=dpi, finder_dpi=dpi)
    assert not errors and len(results) == 1
    result = results[0]
    assert result.vpl == pytest.approx(curve[1][1], abs=0.1)
    assert ("vpl_outside_expected_range" in result.diagnostics) is not depletion
    assert (result.status == "ok") is depletion


if __name__ == "__main__":
    unittest.main()
