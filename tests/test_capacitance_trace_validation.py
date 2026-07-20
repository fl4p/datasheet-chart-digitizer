import unittest

from datasheet_chart_digitizer.capacitance_validation import (
    trace_validation_summary,
)


def _diagnostics(*, ciss_span: float, coss_span: float, crss_span: float):
    return {
        "Ciss": {"points": 427, "x_span_fraction": ciss_span, "y_range_px": 20},
        "Coss": {"points": 427, "x_span_fraction": coss_span, "y_range_px": 80},
        "Crss": {"points": 389, "x_span_fraction": crss_span, "y_range_px": 120},
        "checks": {
            "common_samples": 200,
            "ciss_coss_rank_swap_count": 0,
            "crss_bottom_fraction": 1.0,
            "ciss_flatter_than_coss": True,
        },
    }


class CapacitanceTraceValidationTests(unittest.TestCase):
    def test_crss_tail_materially_shorter_than_upper_curves_refuses(self) -> None:
        summary = trace_validation_summary(
            _diagnostics(ciss_span=0.998, coss_span=0.998, crss_span=0.909),
            "raster",
        )

        self.assertEqual("suspect", summary["status"])
        self.assertIn("Crss_peer_relative_short_x_span", summary["reasons"])

    def test_near_full_vector_crss_tail_deficit_refuses(self) -> None:
        summary = trace_validation_summary(
            _diagnostics(ciss_span=1.0, coss_span=1.0, crss_span=0.909),
            "vector",
        )

        self.assertEqual("suspect", summary["status"])
        self.assertIn("Crss_peer_relative_short_x_span", summary["reasons"])

    def test_equal_short_source_extents_do_not_trigger_peer_guard(self) -> None:
        summary = trace_validation_summary(
            _diagnostics(ciss_span=0.685, coss_span=0.685, crss_span=0.680),
            "vector",
        )

        self.assertEqual("pass", summary["status"])
        self.assertEqual([], summary["reasons"])

    def test_one_short_upper_vector_does_not_prove_a_crss_tail_loss(self) -> None:
        summary = trace_validation_summary(
            _diagnostics(ciss_span=1.0, coss_span=0.65, crss_span=0.90),
            "vector",
        )

        self.assertEqual("pass", summary["status"])
        self.assertNotIn("Crss_peer_relative_short_x_span", summary["reasons"])

    def test_late_shared_ciss_coss_without_reseparation_refuses(self) -> None:
        shared = [{
            "curves": ["Ciss", "Coss"],
            "x0_px": 300,
            "x1_px": 410,
            "separated_sign_before": -1,
            "separated_sign_after": None,
        }]

        summary = trace_validation_summary(
            _diagnostics(ciss_span=0.95, coss_span=0.95, crss_span=0.95),
            "vector",
            shared,
        )

        self.assertEqual("suspect", summary["status"])
        self.assertIn("ciss_coss_unresolved_shared_collapse", summary["reasons"])

    def test_low_v_shared_ciss_coss_with_later_separation_passes(self) -> None:
        shared = [{
            "curves": ["Ciss", "Coss"],
            "x0_px": 20,
            "x1_px": 150,
            "separated_sign_before": None,
            "separated_sign_after": -1,
        }]

        summary = trace_validation_summary(
            _diagnostics(ciss_span=0.95, coss_span=0.95, crss_span=0.95),
            "vector",
            shared,
        )

        self.assertEqual("pass", summary["status"])
        self.assertEqual([], summary["reasons"])


if __name__ == "__main__":
    unittest.main()
