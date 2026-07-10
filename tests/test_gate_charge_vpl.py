import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from datasheet_chart_digitizer import gate_charge_vpl as vpl


class GateChargeVplTests(unittest.TestCase):
    def test_samples_from_chart_extraction_strips_datasheet_prefix(self) -> None:
        text = '''
        "datasheets/infineon/Foo.pdf": {"ref": 3.25, "comment": "first"},
        "datasheets/ao/Bar.pdf": {"comment": "no ref"}
        '''
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "chart-extraction.md"
            path.write_text(text)

            samples = vpl._samples_from_chart_extraction(path, start=1, count=2)

        self.assertEqual(samples[0], ("infineon/Foo.pdf", 3.25, "first"))
        self.assertEqual(samples[1], ("ao/Bar.pdf", None, "no ref"))

    def test_sample_pdf_path_resolves_relative_to_datasheets(self) -> None:
        root = Path("/tmp/pwr-mosfet-lib")

        self.assertEqual(
            vpl._sample_pdf_path(root, "infineon/Foo.pdf"),
            root / "datasheets" / "infineon/Foo.pdf",
        )
        self.assertEqual(
            vpl._sample_pdf_path(root, "/tmp/Foo.pdf"),
            Path("/tmp/Foo.pdf"),
        )

    def test_context_filter_accepts_gate_charge_and_rejects_diode(self) -> None:
        self.assertFalse(vpl._reject_non_gate_context("Figure 8 Gate Charge Characteristics"))
        self.assertTrue(vpl._reject_non_gate_context("Figure 6 Source-Drain Diode Forward"))

    def test_trace_component_tracks_monotone_gate_curve(self) -> None:
        import numpy as np

        mask = np.zeros((80, 100), dtype=np.uint8)
        for x in range(5, 95):
            y = int(round(70 - 0.55 * x))
            mask[max(0, y - 1) : min(mask.shape[0], y + 2), x] = 255

        points = vpl._trace_component(mask)

        self.assertGreater(len(points), 70)
        self.assertLess(points[-1][1], points[0][1])

    def test_main_reports_missing_dslib_checkout(self) -> None:
        with TemporaryDirectory() as tmp:
            argv = ["dsdig digitize-vpl", "--datasheet-root", tmp]
            with mock.patch("sys.argv", argv):
                with self.assertRaises(SystemExit) as raised:
                    vpl.main()

        self.assertIn("requires pwr-mosfet-lib's dslib", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
