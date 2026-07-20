import unittest
from pathlib import Path

from datasheet_chart_digitizer.gate_charge import (
    _depletion_vpl_is_source_plausible,
    digitize_gate_charge,
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
        self.assertAlmostEqual(result.vpl, 0.200652, delta=0.001)
        self.assertNotIn("vpl_outside_expected_range", result.diagnostics)
        self.assertLess(min(value for value, _pixel in result.y_ticks_px), 0.0)
        self.assertGreater(max(value for value, _pixel in result.y_ticks_px), 0.0)
        self.assertGreaterEqual(len(result.curve_px), 400)


if __name__ == "__main__":
    unittest.main()
