import os
import unittest
from pathlib import Path

from datasheet_chart_digitizer import gate_charge


class GateChargeBoundedAxisRecoveryTests(unittest.TestCase):
    def test_requested_vpl_corpus_uses_source_backed_axes(self) -> None:
        root = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib",
            )
        ) / "datasheets"
        cases = {
            "infineon/IRFB4410ZGPBF.pdf": 5.14,
            "infineon/IRFB4410ZPBF.pdf": 5.14,
            "infineon/IRFBA90N20DPBF.pdf": 6.19,
            "infineon/IRLB4030PBF.pdf": 3.58,
            "mcc/MCPF90N12A-BP.pdf": 3.88,
            "rohm/RX3P10BBHC16.pdf": 4.01,
            "xnrusemi/XRT90N20T.pdf": 4.67,
            "nce/NCEP080N10.pdf": 5.11,
            "toshiba/TK72E12N1.pdf": 5.62,
            "huayi/HY1710P.pdf": 4.47,
            "huayi/HY1720P.pdf": 5.74,
            "siliup/SP015N15HTQ.pdf": 4.71,
            "siliup/SP012N06GHTQ.pdf": 5.55,
            "siliup/SP010N02AGHTO.pdf": 4.82,
            "siliup/SP010N14HTQ.pdf": 4.23,
            "infineon/IRFP4127PBF.pdf": 4.48,
            "nce/NCE0160G.pdf": 5.16,
            "nce/NCEP25N10AK.pdf": 3.20,
            "siliup/SP010N07AGTQ.pdf": 3.92,
            "siliup/SP010N07AGNK.pdf": 3.92,
            "toshiba/TPH5R60APL,L1Q.pdf": 3.74,
            "st/STH240N10F7-2.pdf": 4.39,
            "st/STP40NF10.pdf": 5.61,
            "st/STP80NF12.pdf": 5.13,
            "toshiba/TPW4R50ANH,L1Q.pdf": 5.93,
        }
        if not all((root / relative).exists() for relative in cases):
            self.skipTest("requested Vpl regression PDFs are not configured")

        for relative, expected_vpl in cases.items():
            with self.subTest(pdf=relative):
                results = gate_charge.digitize_gate_charge(
                    root / relative,
                    finder_dpi=120,
                )
                served = [
                    result
                    for result in results
                    if result.status == "ok" and result.vpl is not None
                ]
                self.assertEqual(len(served), 1)
                result = served[0]
                self.assertAlmostEqual(
                    float(result.vpl), expected_vpl, delta=0.35
                )
                self.assertEqual(result.x_tick_unit, "nC")
                self.assertGreaterEqual(result.y_tick_count, 2)
                self.assertTrue(
                    any(
                        diagnostic.startswith("axis_ocr_bounded")
                        for diagnostic in result.diagnostics
                    )
                )
                self.assertNotIn(
                    "axis_inferred_from_regular_grid", result.diagnostics
                )
                self.assertNotIn("axis_assumed_0_10", result.diagnostics)
                self.assertNotIn(
                    "gate_charge_unit_unresolved", result.diagnostics
                )

    def test_sparse_source_connected_gate_curve_is_accepted(self) -> None:
        pdf = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib",
            )
        ) / "datasheets/goford/GT035N10T.pdf"
        if not pdf.exists():
            self.skipTest("requested GOFORD Vpl regression PDF is not configured")

        result = gate_charge.digitize_gate_charge(
            pdf,
            finder_dpi=120,
        )[0]

        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(float(result.vpl or 0.0), 2.92, delta=0.2)
        self.assertEqual(result.x_tick_unit, "nC")
        self.assertNotIn("low_trace_confidence", result.diagnostics)

    def test_hxy_candidate_is_visible_but_never_served(self) -> None:
        pdf = Path(
            os.environ.get(
                "DSDIG_DATASHEET_ROOT",
                "/Users/fab/dev/pv/pwr-mosfet-lib",
            )
        ) / "datasheets/hxy/HUFA76633P3-HXY.pdf"
        if not pdf.exists():
            self.skipTest("requested HXY Vpl regression PDF is not configured")

        results = gate_charge.digitize_gate_charge(
            pdf,
            finder_dpi=120,
        )

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertAlmostEqual(float(result.vpl or 0.0), 4.74, delta=0.35)
        self.assertEqual(result.status, "source_untrusted")
        self.assertIn(
            "shared_curve_template_provenance_untrusted",
            result.diagnostics,
        )
        self.assertFalse(result.to_manifest()["physical_output_available"])


if __name__ == "__main__":
    unittest.main()
