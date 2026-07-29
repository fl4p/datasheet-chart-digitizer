from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from datasheet_chart_digitizer import find_charts
from datasheet_chart_digitizer.find_charts import (
    PageText,
    detect_raster_grid_frames,
    _raster_image_caption_recovery,
)


DATASHEETS = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets")
SILIUP_BELOW_CAPTION = DATASHEETS / "siliup/SP010N02GHTQ.pdf"


def _page(width_pt: float = 595.0, height_pt: float = 842.0) -> PageText:
    return PageText(page_num=1, width_pt=width_pt, height_pt=height_pt, words=[])


def _write_png(tmp: Path, image: np.ndarray) -> Path:
    path = tmp / "page.png"
    cv2.imwrite(str(path), image)
    return path


def _frame_image(*, closed_bottom: bool = True) -> np.ndarray:
    """A white page with one grid plot frame at px (300,300)-(800,750)."""
    image = np.full((2100, 1500), 255, np.uint8)
    left, top, right, bottom = 300, 300, 800, 750
    for y in range(top, bottom - 60, 90):  # top border + interior grid lines
        cv2.line(image, (left, y), (right, y), 0, 2)
    if closed_bottom:
        cv2.line(image, (left, bottom), (right, bottom), 0, 2)
    cv2.line(image, (left, top), (left, bottom), 0, 2)
    cv2.line(image, (right, top), (right, bottom), 0, 2)
    return image


class DetectRasterGridFramesTests(unittest.TestCase):
    def test_closed_grid_frame_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            png = _write_png(Path(tmp), _frame_image())
            frames = detect_raster_grid_frames(png, _page())
            self.assertEqual(len(frames), 1)
            x0, y0, x1, y1 = frames[0]
            # px (300,300)-(800,750) at 1500x2100 on a 595x842pt page
            self.assertAlmostEqual(x0, 300 / 1500 * 595, delta=3.0)
            self.assertAlmostEqual(x1, 800 / 1500 * 595, delta=3.0)
            self.assertAlmostEqual(y0, 300 / 2100 * 842, delta=3.0)
            self.assertAlmostEqual(y1, 750 / 2100 * 842, delta=3.0)

    def test_open_bottom_frame_is_refused(self) -> None:
        # Anti-monotone guard: a frame the detector cannot positively close
        # must yield nothing, not a guessed extent.
        with tempfile.TemporaryDirectory() as tmp:
            png = _write_png(Path(tmp), _frame_image(closed_bottom=False))
            self.assertEqual(detect_raster_grid_frames(png, _page()), [])

    def test_blank_page_yields_no_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            png = _write_png(Path(tmp), np.full((2100, 1500), 255, np.uint8))
            self.assertEqual(detect_raster_grid_frames(png, _page()), [])

    def test_unreadable_image_yields_no_frames(self) -> None:
        self.assertEqual(
            detect_raster_grid_frames(Path("/nonexistent/page.png"), _page()), []
        )


class RasterImageCaptionRecoveryGateTests(unittest.TestCase):
    def test_low_image_coverage_page_is_not_ocr_scanned(self) -> None:
        """Text pages without captions must not enter the raster stratum."""
        with (
            mock.patch.object(
                find_charts, "_page_image_rects", return_value=[(0.0, 0.0, 100.0, 100.0)]
            ),
            mock.patch.object(
                find_charts, "render_page", side_effect=AssertionError("must not render")
            ),
        ):
            result = _raster_image_caption_recovery(
                Path("sample.pdf"), _page(), 180, Path("/tmp")
            )
        self.assertIsNone(result)

    def test_frameless_raster_page_is_refused_before_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            png = _write_png(Path(tmp), np.full((2100, 1500), 255, np.uint8))
            with (
                mock.patch.object(
                    find_charts, "_page_image_rects",
                    return_value=[(0.0, 0.0, 595.0, 842.0)],
                ),
                mock.patch.object(find_charts, "render_page", return_value=png),
                mock.patch.object(
                    find_charts, "_tesseract_tsv",
                    side_effect=AssertionError("must not OCR"),
                ),
            ):
                result = _raster_image_caption_recovery(
                    Path("sample.pdf"), _page(), 180, Path(tmp)
                )
        self.assertIsNone(result)


@unittest.skipUnless(
    SILIUP_BELOW_CAPTION.exists(), "local SP010N02GHTQ datasheet unavailable"
)
class SiliupRasterPanelRecoveryTests(unittest.TestCase):
    def test_raster_only_capacitance_panel_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            panels = find_charts.process_pdf(SILIUP_BELOW_CAPTION, Path(tmp), 180)
        by_kind = {panel.kind: panel for panel in panels}
        self.assertIn("capacitances", by_kind)
        capacitance = by_kind["capacitances"]
        self.assertEqual(capacitance.page, 4)
        self.assertEqual(capacitance.title, "Capacitance Characteristics")
        self.assertEqual(capacitance.text_source, "tesseract_fallback")
        # The owned frame, not the page-wide JPEG: the raster spans several
        # panels, so a whole-image bbox would be a multi-panel mis-crop.
        x0, y0, x1, y1 = capacitance.bbox_pt
        self.assertLess(x1 - x0, 0.55 * 595.0)
        self.assertLess(y1 - y0, 0.45 * 842.0)
        # Spec/package pages (1, 2, 5) must contribute nothing.
        self.assertEqual({panel.page for panel in panels} - {3, 4}, set())


if __name__ == "__main__":
    unittest.main()
