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
            "huayi/HY1710P.pdf": 4.47,
            # served again since 2026-10-16: the bounded OCR now seats 15/12/9/3/0
            # on their rules (grid check verified) and reads 5.651 V, the value
            # the frame rules give (15 V at 137 px, 0 V at 612 px, plateau 433 px).
            "huayi/HY1720P.pdf": 5.65,
            "siliup/SP015N15HTQ.pdf": 4.71,
            "siliup/SP012N06GHTQ.pdf": 5.55,
            "siliup/SP010N02AGHTO.pdf": 4.82,
            "siliup/SP010N14HTQ.pdf": 4.23,
            "infineon/IRFP4127PBF.pdf": 4.48,
            "nce/NCEP25N10AK.pdf": 3.20,
            "siliup/SP010N07AGTQ.pdf": 3.92,
            "siliup/SP010N07AGNK.pdf": 3.92,
            "toshiba/TPH5R60APL,L1Q.pdf": 3.74,
            "st/STP40NF10.pdf": 5.61,
            "st/STP80NF12.pdf": 5.13,
            "toshiba/TPW4R50ANH,L1Q.pdf": 5.93,
            "crmicro/CR6N70FA9K.pdf": 4.83,
            "good_ark/GSFT7R515.pdf": 5.48,
            "toshiba/XPQ1R00AQB.pdf": 5.58,
        }
        # HY1720P was withheld 2026-09-29 to 2026-10-16 (now in cases above):
        # the bounded OCR ticks of HY1720P read a "0" 11 px
        # below the 0 V frame and a stray "2", so no registration binds them to
        # the dotted 1.5 V grid. That OCR line served Vpl 5.886 V where the
        # frame rules (15 V at 137 px, 0 V at 612 px) put the plateau at
        # 433 px = 5.65 V.
        # Withheld since the served curve is checked (2026-09-30, astra-review-50):
        # NCE0160G p5: box top on the 8 V rule (frame 10 V), the trace stops at
        #   8 V and picks up the "VDS = 50 V" leader; plot_box_clips_source_curve.
        # STH240N10F7-2 p5: box top ~11 V, stroke continues to 12 V; same.
        # TK72E12N1 p6: the bounded-OCR result's box ends at 120 of 160 nC and cuts
        #   the VGS stroke (not served); the first-pass fallback climbs VGS to
        #   ~45 nC and then rides the falling VDS stroke to 0 V:
        #   non_monotone_gate_curve.
        # Their Vpl reads were right; the served curves were not.
        withheld = {
            "nce/NCE0160G.pdf": "plot_box_clips_source_curve",
            "st/STH240N10F7-2.pdf": "plot_box_clips_source_curve",
            "toshiba/TK72E12N1.pdf": "non_monotone_gate_curve",
        }
        if not all((root / relative).exists() for relative in (*cases, *withheld)):
            self.skipTest("requested Vpl regression PDFs are not configured")
        for relative, diagnostic in withheld.items():
            with self.subTest(pdf=relative):
                results = gate_charge.digitize_gate_charge(root / relative, finder_dpi=120)
                self.assertEqual(
                    [r for r in results if r.status == "ok" and r.vpl is not None], []
                )
                self.assertTrue(any(diagnostic in r.diagnostics for r in results))

        # native PDF text carries these VGS columns (5..0 V / 12..0 V at x 366 pt)
        native_axis = {
            "infineon/IRLB4030PBF.pdf",
            "infineon/IRFB4410ZPBF.pdf",
            "infineon/IRFB4410ZGPBF.pdf",
        }
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
                if relative in native_axis:
                    # The first pass now reads the native-text VGS column
                    # itself; bounded OCR is not needed. Until 2026-10-16 the
                    # first pass took a column whose values RISE downward
                    # ("-20" at 482 pt over "20" at 713 pt, x 410 pt) and only
                    # the bounded-OCR retry recovered the axis; such a run is
                    # now accepted only when it is all non-positive. Same Vpl.
                    self.assertNotIn("axis_ocr_bounded", result.diagnostics)
                    self.assertGreaterEqual(result.y_tick_count, 6)
                else:
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
                if relative.endswith("XPQ1R00AQB.pdf"):
                    self.assertEqual(result.panel.diagram, 814)
                    self.assertTrue(
                        gate_charge._gate_curve_is_monotone(
                            list(result.curve_px), result.plot_box_px
                        )
                    )
                    self.assertLessEqual(
                        max(
                            current[0] - previous[0]
                            for previous, current in zip(
                                result.curve_px, result.curve_px[1:]
                            )
                        ),
                        8,
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
        # Since 2026-10-16 the bounded OCR also reads the printed "4.0" label
        # (one read drops it, another has it on the 4 V row). The JPEG rules
        # are not evenly spaced: the 4 V rule is at crop row 403-404 where the
        # 12/10/6/2 V rules (136/204/336/468) put it at 402, so the 1.5 px
        # label-to-grid registration now refuses (y_axis_grid_check_unverified)
        # and the status is low_confidence instead of source_untrusted. Either
        # way it is unserved and keeps the provenance diagnostic.
        self.assertIn(result.status, {"source_untrusted", "low_confidence"})
        self.assertNotEqual(result.status, "ok")
        self.assertIn(
            "shared_curve_template_provenance_untrusted",
            result.diagnostics,
        )
        self.assertFalse(result.to_manifest()["physical_output_available"])


if __name__ == "__main__":
    unittest.main()
