import csv
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np

from datasheet_chart_digitizer import find_charts
from datasheet_chart_digitizer import mosfet_capacitance as mc


TOSHIBA_ROOT = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/toshiba")
TK100E10N1 = TOSHIBA_ROOT / "TK100E10N1.pdf"
TPCC8105 = TOSHIBA_ROOT / "TPCC8105.pdf"
_HAVE_TESSERACT = shutil.which("tesseract") is not None


@unittest.skipUnless(_HAVE_TESSERACT, "tesseract not available for OCR axis calibration")
class ToshibaRasterEndToEndTests(unittest.TestCase):
    def _digitize_capacitance_panel(
        self, pdf: Path, prefix: str
    ) -> tuple[dict[str, object], list[dict[str, str]], np.ndarray]:
        with TemporaryDirectory(prefix=prefix) as tmp:
            out = Path(tmp)
            panels = find_charts.process_pdf(pdf, out, dpi=180)
            caps = [panel for panel in panels if panel.kind == "capacitances"]
            self.assertEqual(len(caps), 1)
            panel = caps[0]
            chart = {
                "pdf": str(pdf),
                "part": pdf.stem,
                "page": panel.page,
                "bbox_pt": list(panel.bbox_pt),
                "crop_box_pt": list(panel.crop_box_pt),
                "text": "",
            }
            crop_path = out / panel.crop_png
            crop = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
            self.assertIsNotNone(crop)
            result = mc.process_chart(
                chart, crop_path, out, Path(prefix), pdf.parent
            )
            with open(out / result["points"]) as fh:
                rows = list(csv.DictReader(fh))
        return result, rows, crop

    @unittest.skipUnless(TK100E10N1.exists(), "local TK100E10N1 unavailable")
    def test_ocr_calibrated_raster_digitization_matches_nameplate(self) -> None:
        result, rows, _crop = self._digitize_capacitance_panel(
            TK100E10N1, "tk100e10n1-e2e-"
        )
        calibration = result["axis_calibration"]
        self.assertEqual(calibration["source"], "position_ocr")
        self.assertTrue(calibration["x_log"])
        self.assertTrue(result["axis_calibration_trusted"])
        self.assertEqual(result["trace_validation_status"], "pass")

        expected = {"Ciss": 8800.0, "Coss": 1500.0, "Crss": 63.0}
        for name, typ_pf in expected.items():
            points = sorted(
                (float(row["vds_V"]), float(row["cap_pF"]))
                for row in rows
                if row["trace"] == name and row.get("vds_V")
            )
            self.assertTrue(points, f"{name} produced no calibrated points")
            vds, cap = min(points, key=lambda point: abs(point[0] - 50.0))
            self.assertLess(abs(vds - 50.0) / 50.0, 0.1)
            self.assertLess(abs(cap - typ_pf) / typ_pf, 0.15)

    @unittest.skipUnless(TPCC8105.exists(), "local TPCC8105 unavailable")
    def test_pchannel_signed_vds_axis_and_black_grid_rails(self) -> None:
        result, rows, crop = self._digitize_capacitance_panel(
            TPCC8105, "tpcc8105-e2e-"
        )
        calibration = result["axis_calibration"]
        self.assertEqual(calibration["x_ticks_v"], [0.1, 1.0, 10.0, 100.0])
        self.assertEqual(
            calibration["x_source_ticks_v"], [-0.1, -1.0, -10.0, -100.0]
        )
        self.assertEqual(calibration["x_value_transform"], "abs_source_negative_vds")
        self.assertEqual(calibration["x_gridline_px"], [102.5, 229.0, 355.5, 481.5])
        self.assertLessEqual(calibration["x_grid_residual_px"], 1.0)
        self.assertGreater(calibration["x_label_to_grid_max_px"], 2.0)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["physical_output_available"])
        self.assertEqual(result["trace_validation_status"], "pass")
        self.assertGreater(result["diagnostics"]["Coss"]["y_range_px"], 80)
        self.assertGreater(result["diagnostics"]["Crss"]["y_range_px"], 80)

        # The former mask followed the thick 1000-pF major rail. Reject long
        # constant-y runs and prove each colored point remains on source ink.
        for name in ("Ciss", "Coss", "Crss"):
            points = [
                (int(row["x_px"]), int(row["y_px"]))
                for row in rows
                if row["trace"] == name
            ]
            counts = np.unique([y for _x, y in points], return_counts=True)[1]
            self.assertLess(int(counts.max()), 25, name)
            seated = 0
            for x, y in points:
                x0, x1 = max(0, x - 1), min(crop.shape[1], x + 2)
                y0, y1 = max(0, y - 2), min(crop.shape[0], y + 3)
                seated += int(np.any(crop[y0:y1, x0:x1] < 90))
            self.assertGreaterEqual(seated / len(points), 0.99, name)


if __name__ == "__main__":
    unittest.main()
