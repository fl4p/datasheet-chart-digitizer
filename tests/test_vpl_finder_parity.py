import unittest

from tools.regression import run_vpl_finder_parity as parity


class VplFinderParityTests(unittest.TestCase):
    def test_panel_match_accepts_precise_panel_inside_broad_legacy_row(self) -> None:
        legacy_bbox = (92.0, 430.0, 545.0, 563.0)
        legacy_center = (318.5, 496.5)
        precise_panel = (88.0, 388.0, 260.0, 566.0)

        self.assertTrue(parity._panel_matches_legacy(precise_panel, legacy_bbox, legacy_center))

    def test_panel_match_rejects_neighbor_without_overlap(self) -> None:
        legacy_bbox = (33.0, 488.0, 282.0, 709.0)
        legacy_center = (157.5, 598.5)
        right_neighbor = (332.0, 489.0, 546.0, 694.0)

        self.assertFalse(parity._panel_matches_legacy(right_neighbor, legacy_bbox, legacy_center))


if __name__ == "__main__":
    unittest.main()
