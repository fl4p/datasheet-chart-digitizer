from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from datasheet_chart_digitizer.annotate_pdf import annotate_pdf
from datasheet_chart_digitizer.chart_classifier import classify_chart
from datasheet_chart_digitizer.diode_forward_voltage import _temperatures as diode_temperatures
from datasheet_chart_digitizer.transfer_characteristics import (
    _temperatures as transfer_temperatures,
)


DATASHEETS = Path(os.environ.get("DSDIG_DATASHEET_ROOT", "tests/fixtures/datasheets"))
SUP90140E = DATASHEETS / "vishay/SUP90140E.pdf"
EPC2022 = DATASHEETS / "epc/EPC2022.pdf"
EPC2023 = DATASHEETS / "epc/EPC2023.pdf"


class SupEpcParserRegressionTests(unittest.TestCase):
    def test_vendor_temperature_glyphs_and_separated_signs(self) -> None:
        self.assertEqual(diode_temperatures("25˚C 125˚C"), [25.0, 125.0])
        self.assertEqual(
            transfer_temperatures("TC = - 55 °C; TC = 25 °C; TC = 125 °C"),
            [-55.0, 25.0, 125.0],
        )
        self.assertEqual(
            transfer_temperatures("500 25˚C 125˚C = 3 V V DS"),
            [25.0, 125.0],
        )

    def test_supported_vendor_titles_route_to_existing_families(self) -> None:
        self.assertEqual(
            classify_chart(
                "Drain Source Breakdown vs. Junction Temperature", ""
            ),
            "breakdown_voltage",
        )
        self.assertEqual(
            classify_chart("Typical Reverse Drain-Source Characteristics", ""),
            "body_diode",
        )


@unittest.skipUnless(
    SUP90140E.exists() and EPC2022.exists() and EPC2023.exists(),
    "local SUP90140E/EPC2022/EPC2023 datasheets unavailable",
)
class SupEpcAnnotatedPdfRegressionTests(unittest.TestCase):
    def test_all_detected_supported_panels_are_embedded_without_errors(self) -> None:
        expected = {
            SUP90140E: {
                "Transfer Characteristics",
                "On-Resistance vs. Drain Current",
                "Capacitance",
                "Gate Charge",
                "Drain Source Breakdown vs. Junction Temperature",
                "Source Drain Diode Forward Voltage",
                "On-Resistance vs. Junction Temperature",
            },
            EPC2022: {
                "Typical Transfer Characteristics",
                "Typical Gate Charge",
                "Typical Reverse Drain-Source Characteristics",
                "Normalized On-State Resistance vs. Temperature",
                "Typical Capacitance (Linear Scale)",
                "Typical Capacitance (Log Scale)",
            },
            EPC2023: {
                "Transfer Characteristics",
                "Gate Charge",
                "Reverse Drain-Source Characteristics",
                "Normalized On-State Resistance vs. Temperature",
                "Capacitance (Linear Scale)",
                "Capacitance (Log Scale)",
            },
        }
        for pdf, expected_titles in expected.items():
            with self.subTest(pdf=pdf.name), tempfile.TemporaryDirectory(
                prefix=f"{pdf.stem.lower()}-supported-"
            ) as tmp:
                root = Path(tmp)
                manifest = annotate_pdf(
                    pdf,
                    root / f"{pdf.stem}.annotated.pdf",
                    work_dir=root / "work",
                    dpi=180,
                    include_review_required=True,
                )
            self.assertEqual(manifest["errors"], [])
            self.assertEqual(
                {record["title"] for record in manifest["overlays"]},
                expected_titles,
            )
            self.assertTrue(
                all(record["embedded"] for record in manifest["overlays"])
            )
            self.assertNotIn(
                "refused",
                {record["status"] for record in manifest["overlays"]},
            )
