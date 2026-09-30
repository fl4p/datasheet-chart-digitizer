"""Source-owned colored temperature-legend binding for diode charts."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import cv2
import pymupdf

from .crop_transform import CropTransform
from .finder_types import Word
from .transfer_temperature_labels import temperature_labels, temperature_values
from .source_color_binding import (
    ColorPredicate,
    DrawingCandidate,
    bind_two_source_color_legend,
)


def temperatures_from_source_words(words: list[Word]) -> list[float]:
    """Distinct Celsius labels owned by the panel's source words.

    Delegates to the shared positioned-word parser, which joins only touching
    words on one row: a logarithmic-axis tick such as ``10`` stays apart from a
    nearby ``°C`` label while ``Tj`` ``=`` ``-`` ``55`` ``°C`` still parses.
    """

    return temperature_values(words)


def colored_temperature_bindings(
    panel,
    crop_path: Path,
    calibration,
    pixel_curves: list[list[tuple[int, int]]],
    *,
    drawing_candidate: DrawingCandidate,
    is_curve_color: ColorPredicate,
) -> list[tuple[float, int]] | None:
    """Bind a strict two-row colored temperature legend to colored curves."""

    image = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    transform = CropTransform.for_chart(asdict(panel), image.shape)
    labels: list[tuple[float, tuple[float, float, float, float]]] = []
    with pymupdf.open(panel.pdf) as document:
        page = document[panel.page - 1]
        source_words = [
            Word(str(word[4]), *(float(value) for value in word[:4]))
            for word in page.get_text("words")
        ]
        for label in temperature_labels(source_words):
            x0, y0 = transform.to_px(label.x0, label.y0)
            x1, y1 = transform.to_px(label.x1, label.y1)
            rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            cx = 0.5 * (rect[0] + rect[2])
            cy = 0.5 * (rect[1] + rect[3])
            if (
                calibration.hint.x0 <= cx <= calibration.hint.x1
                and calibration.hint.y0 <= cy <= calibration.hint.y1
            ):
                labels.append((label.value_c, rect))
        return bind_two_source_color_legend(
            page,
            transform,
            calibration.plot,
            pixel_curves,
            list(dict.fromkeys(labels)),
            drawing_candidate=drawing_candidate,
            is_curve_color=is_curve_color,
        )
