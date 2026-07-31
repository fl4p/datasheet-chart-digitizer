import unittest

import numpy as np

from datasheet_chart_digitizer.capacitance_pair_tracking import (
    bridge_flat_ciss_occlusions,
    track_ciss_coss_pair,
)
from datasheet_chart_digitizer.capacitance_traces import (
    _cluster_column_runs,
    _trace_fragment_mask,
)
from datasheet_chart_digitizer.capacitance_types import CapAnchor, PlotBox


class CapacitancePairTrackingTests(unittest.TestCase):
    def setUp(self):
        self.plot = PlotBox(10, 5, 130, 105)
        self.anchors = {
            "Ciss": CapAnchor("Ciss", 2000.0, 50.0),
            "Coss": CapAnchor("Coss", 800.0, 50.0),
        }

    def test_claims_second_upper_branch_as_soon_as_it_separates(self):
        centers = []
        for x in range(121):
            if x <= 20:
                centers.append([20.0, 80.0])
            else:
                centers.append([20.0, 20.0 + 0.2 * (x - 20), 80.0])

        tracked, _ = track_ciss_coss_pair(
            centers, self.plot, self.anchors, seed_x=100
        )
        ciss = dict(tracked["Ciss"])
        coss = dict(tracked["Coss"])

        self.assertEqual(ciss[50], 25)
        self.assertEqual(coss[50], 29)
        self.assertEqual(ciss[20], coss[20])

    def test_preserves_identity_across_a_merged_crossing(self):
        centers = []
        for x in range(121):
            ciss = 40.0
            coss = 25.0 + 0.3 * x
            if 45 <= x <= 55:
                centers.append([40.0, 90.0])
            else:
                centers.append([min(ciss, coss), max(ciss, coss), 90.0])

        tracked, _ = track_ciss_coss_pair(
            centers, self.plot, self.anchors, seed_x=100
        )
        ciss = dict(tracked["Ciss"])
        coss = dict(tracked["Coss"])

        self.assertEqual(ciss[30], 45)
        self.assertEqual(ciss[90], 45)
        self.assertLess(coss[30], ciss[30])
        self.assertGreater(coss[90], ciss[90])

    def test_single_visible_upper_branch_is_not_assigned_to_both_curves(self):
        centers = [
            [45.0, 90.0] if 20 <= x <= 40 else [20.0, 45.0, 90.0]
            for x in range(121)
        ]

        tracked, _ = track_ciss_coss_pair(
            centers, self.plot, self.anchors, seed_x=100
        )
        ciss_x = {x for x, _y in tracked["Ciss"]}
        coss = dict(tracked["Coss"])

        self.assertNotIn(35, ciss_x)
        self.assertEqual(coss[35], 50)

    def test_third_band_disambiguates_close_upper_pair_from_fragments(self):
        column = np.zeros(100, dtype=np.uint8)
        column[20:22] = 1
        column[26:28] = 1
        column[80:82] = 1
        self.assertEqual(
            _cluster_column_runs(column, preserve_close_upper_pair=True),
            [20.5, 26.5, 80.5],
        )

        column = np.zeros(100, dtype=np.uint8)
        column[20:22] = 1
        column[23:25] = 1
        column[80:82] = 1
        self.assertEqual(
            _cluster_column_runs(column, preserve_close_upper_pair=True),
            [22.0, 80.5],
        )

    def test_material_flat_fragment_survives_component_filter(self):
        mask = np.zeros((100, 121), dtype=np.uint8)
        mask[30, 40:91] = 1
        mask[50, 10:30] = 1

        cleaned = _trace_fragment_mask(
            mask, self.plot, preserve_flat_fragments=True
        )

        self.assertEqual(int(cleaned[30, 40:91].sum()), 51)
        self.assertEqual(int(cleaned[50, 10:30].sum()), 0)

    def test_bounded_flat_ciss_occlusion_is_interpolated(self):
        assigned = {
            "Ciss": [(10, 30), (20, 31)],
            "Coss": [(10, 20), (20, 40)],
        }
        repaired = bridge_flat_ciss_occlusions(assigned, self.plot)
        self.assertEqual(dict(repaired["Ciss"])[15], 30)

        steep = {
            "Ciss": [(10, 30), (20, 40)],
            "Coss": assigned["Coss"],
        }
        self.assertIs(bridge_flat_ciss_occlusions(steep, self.plot), steep)


if __name__ == "__main__":
    unittest.main()
