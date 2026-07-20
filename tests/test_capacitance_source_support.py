import unittest

import cv2
import numpy as np

from datasheet_chart_digitizer.capacitance_source_support import (
    raster_source_support_diagnostics,
)
from datasheet_chart_digitizer.capacitance_types import PlotBox, Trace


class CapacitanceSourceSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gray = np.full((120, 120), 255, dtype=np.uint8)
        self.plot = PlotBox(10, 10, 109, 109)
        self.xs = list(range(15, 105))

    def _line(self, y: int) -> None:
        cv2.line(self.gray, (15, y), (104, y), 0, 2)

    def _trace(self, name: str, points: list[tuple[int, int]]) -> Trace:
        xs = [x for x, _y in points]
        ys = [y for _x, y in points]
        return Trace(
            name=name,
            area=len(points),
            bbox=(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1),
            points=points,
        )

    def test_source_seated_traces_have_no_material_absent_run(self) -> None:
        for y in (25, 50, 85):
            self._line(y)
        traces = [
            self._trace(name, [(x, y) for x in self.xs])
            for name, y in (("Ciss", 25), ("Coss", 50), ("Crss", 85))
        ]

        diagnostics = raster_source_support_diagnostics(
            self.gray, self.plot, traces, []
        )

        self.assertEqual(
            [],
            diagnostics["trace_support"]["Coss"][
                "material_source_absent_runs"
            ],
        )

    def test_diagonal_shortcut_across_sharp_cliff_is_source_absent(self) -> None:
        self._line(25)
        cv2.line(self.gray, (15, 45), (60, 45), 0, 2)
        cv2.line(self.gray, (60, 45), (60, 75), 0, 2)
        cv2.line(self.gray, (60, 75), (104, 75), 0, 2)
        self._line(90)
        coss = []
        for x in self.xs:
            if x < 25:
                y = 45
            elif x > 95:
                y = 75
            else:
                y = int(round(45 + (x - 25) * 30 / 70))
            coss.append((x, y))
        traces = [
            self._trace("Ciss", [(x, 25) for x in self.xs]),
            self._trace("Coss", coss),
            self._trace("Crss", [(x, 90) for x in self.xs]),
        ]

        diagnostics = raster_source_support_diagnostics(
            self.gray, self.plot, traces, []
        )

        self.assertTrue(
            diagnostics["trace_support"]["Coss"][
                "material_source_absent_runs"
            ]
        )

    def test_shared_names_with_orphaned_source_branch_are_rejected(self) -> None:
        for y in (25, 48, 85):
            self._line(y)
        traces = [
            self._trace("Ciss", [(x, 25) for x in self.xs]),
            self._trace("Coss", [(x, 25) for x in self.xs]),
            self._trace("Crss", [(x, 85) for x in self.xs]),
        ]
        shared = [{"x0_px": 15, "x1_px": 104}]

        diagnostics = raster_source_support_diagnostics(
            self.gray, self.plot, traces, shared
        )

        self.assertTrue(diagnostics["material_shared_orphan_source_runs"])

    def test_genuine_shared_source_has_no_orphaned_branch(self) -> None:
        for y in (25, 85):
            self._line(y)
        traces = [
            self._trace("Ciss", [(x, 25) for x in self.xs]),
            self._trace("Coss", [(x, 25) for x in self.xs]),
            self._trace("Crss", [(x, 85) for x in self.xs]),
        ]
        shared = [{"x0_px": 15, "x1_px": 104}]

        diagnostics = raster_source_support_diagnostics(
            self.gray, self.plot, traces, shared
        )

        self.assertEqual([], diagnostics["material_shared_orphan_source_runs"])


if __name__ == "__main__":
    unittest.main()
