"""Shared PDF-point <-> chart-crop pixel transform."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from . import visible_text


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


_TICK_TEXT = re.compile(r"[-+−]?\d+(?:[.,]\d+)?(?:[eE][-+−]?\d+)?")


def blank_rotated_words(gray, page, transform: CropTransform):
    """Copy of *gray* with the rotated (tall, >=2-letter) PDF words of the left
    title gutter whitened; *gray* if there are none.

    A word is in that gutter when its centre lies left of every numeric word
    in the crop (the y tick labels); rotated words elsewhere, such as curve
    labels inside the plot, are kept, and without numeric words nothing is
    blanked."""
    words = []
    for word in visible_text.words(page):
        x0, y0 = transform.to_px(word[0], word[1])
        x1, y1 = transform.to_px(word[2], word[3])
        words.append((x0, y0, x1, y1, word[4]))
    ticks = [w[0] for w in words if _TICK_TEXT.fullmatch(w[4].strip())
             and 0 <= (w[0] + w[2]) / 2 < gray.shape[1] and 0 <= (w[1] + w[3]) / 2 < gray.shape[0]]
    gutter = min(ticks, default=-math.inf)
    out = None
    for x0, y0, x1, y1, text in words:
        if y1 - y0 <= 1.5 * (x1 - x0) or not re.search(r"[A-Za-z]{2}", text) or (x0 + x1) / 2 >= gutter:
            continue
        c0, c1 = max(0, int(x0) - 1), min(gray.shape[1], math.ceil(x1) + 1)
        r0, r1 = max(0, int(y0) - 1), min(gray.shape[0], math.ceil(y1) + 1)
        if c0 >= c1 or r0 >= r1:
            continue
        if out is None:
            out = gray.copy()
        out[r0:r1, c0:c1] = 255
    return gray if out is None else out
