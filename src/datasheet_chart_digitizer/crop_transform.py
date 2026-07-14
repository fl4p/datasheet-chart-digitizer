"""Shared PDF-point <-> chart-crop pixel transform."""

from __future__ import annotations

from dataclasses import dataclass


CROP_MARGIN_PT = 2.0


@dataclass(frozen=True)
class CropTransform:
    """Map PDF page coordinates to pixels in a finder-produced chart crop.

    Fresh chart indexes carry the exact effective crop region in
    ``crop_box_pt``. Older indexes only have the nominal panel ``bbox_pt``;
    their crops still included the finder margin, so reconstruct that region
    as ``bbox_pt +/- CROP_MARGIN_PT`` (accurate except for lost page-edge clamp
    and sub-pixel truncation metadata).
    """

    x0_pt: float
    y0_pt: float
    scale_x: float  # pixels per PDF point
    scale_y: float

    @classmethod
    def for_chart(
        cls,
        chart: dict[str, object],
        image_shape: tuple[int, ...],
    ) -> "CropTransform":
        height, width = image_shape[:2]
        box = chart.get("crop_box_pt")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            bbox = chart.get("bbox_pt")
            if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
                raise RuntimeError("chart bbox_pt missing")
            bx0, by0, bx1, by1 = [float(value) for value in bbox]
            box = (
                bx0 - CROP_MARGIN_PT,
                by0 - CROP_MARGIN_PT,
                bx1 + CROP_MARGIN_PT,
                by1 + CROP_MARGIN_PT,
            )
        x0, y0, x1, y1 = [float(value) for value in box]
        return cls(
            x0_pt=x0,
            y0_pt=y0,
            scale_x=width / max(1e-9, x1 - x0),
            scale_y=height / max(1e-9, y1 - y0),
        )

    def to_px(self, x_pt: float, y_pt: float) -> tuple[float, float]:
        return (
            (x_pt - self.x0_pt) * self.scale_x,
            (y_pt - self.y0_pt) * self.scale_y,
        )

    def to_pt(self, x_px: float, y_px: float) -> tuple[float, float]:
        return (
            self.x0_pt + x_px / self.scale_x,
            self.y0_pt + y_px / self.scale_y,
        )
