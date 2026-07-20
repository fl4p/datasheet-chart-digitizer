from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from datasheet_chart_digitizer.annotate_pdf import annotate_pdf


class LittelfuseAnnotateTests(unittest.TestCase):
    def test_detached_gate_and_capacitance_panels_are_embedded(self) -> None:
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/littelfuse/"
            "275-101N30A-00.pdf"
        )
        if not pdf.is_file():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = annotate_pdf(
                pdf,
                root / "annotated.pdf",
                work_dir=root / "artifacts",
                dpi=220,
                include_review_required=True,
            )

        self.assertEqual(manifest["detected_panels"], 3)
        embedded = {
            (row["diagram"], row["kind"])
            for row in manifest["overlays"]
            if row["embedded"]
        }
        self.assertIn((3, "gate_charge"), embedded)
        self.assertIn((5, "capacitances"), embedded)
        self.assertEqual(
            [error["diagram"] for error in manifest["errors"]],
            [1],
        )
        self.assertIn("temperature labels", manifest["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()
