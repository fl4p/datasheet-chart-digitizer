"""Source-owned colored temperature-legend binding for diode charts."""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path

import cv2
import pymupdf

from .crop_transform import CropTransform
from .finder_types import Word
from .source_color_binding import (
    ColorPredicate,
    DrawingCandidate,
    bind_two_source_color_legend,
)


def temperatures_from_source_words(words: list[Word]) -> list[float]:
    """Parse complete or tightly adjacent Celsius tokens without joining rows.

    A generic line grouper can place a logarithmic-axis tick such as ``10`` on
    the same visual row as a nearby ``°C`` label. Source word geometry keeps
    that tick separate while still supporting PDFs that split ``175``, ``o``,
    and ``C`` into individual words.
    """

    def normalized(text: str) -> str:
        return (
            text.strip()
            .replace("−", "-")
            .replace("º", "°")
            .replace("˚", "°")
            .replace("℃", "°C")
        )

    def signed_value(word: Word, value: float) -> float:
        middle_y = 0.5 * (word.y0 + word.y1)
        minuses = [
            other
            for other in words
            if normalized(other.text) == "-"
            and -0.75 <= word.x0 - other.x1 <= 5.0
            and abs(0.5 * (other.y0 + other.y1) - middle_y)
            <= 0.6 * max(word.y1 - word.y0, other.y1 - other.y0)
        ]
        return -abs(value) if len(minuses) == 1 else value

    values: set[float] = set()
    numeric_words: list[tuple[Word, float]] = []
    unit_words: list[Word] = []
    for word in words:
        text = normalized(word.text)
        complete = re.fullmatch(
            r"(-?\d+(?:\.\d+)?)\s*(?:°|[oO])\s*C", text, re.I
        )
        if complete is not None:
            values.add(signed_value(word, float(complete.group(1))))
            continue
        number = re.fullmatch(r"(-?\d+(?:\.\d+)?)", text)
        if number is not None:
            numeric_words.append((word, float(number.group(1))))
        if re.fullmatch(r"(?:°|[oO])?C", text, re.I):
            unit_words.append(word)

    for number_word, value in numeric_words:
        number_mid_y = 0.5 * (number_word.y0 + number_word.y1)
        if any(
            -0.75 <= unit.x0 - number_word.x1 <= 5.0
            and abs(0.5 * (unit.y0 + unit.y1) - number_mid_y)
            <= 0.6 * max(number_word.y1 - number_word.y0, unit.y1 - unit.y0)
            for unit in unit_words
        ):
            values.add(signed_value(number_word, value))
    return sorted(value for value in values if -100 <= value <= 250)


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
        for word in page.get_text("words"):
            text = (
                str(word[4])
                .strip()
                .replace("−", "-")
                .replace("º", "°")
                .replace("˚", "°")
                .replace("℃", "°C")
            )
            match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*°\s*C", text, re.I)
            if match is None:
                continue
            x0, y0 = transform.to_px(float(word[0]), float(word[1]))
            x1, y1 = transform.to_px(float(word[2]), float(word[3]))
            rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            cx = 0.5 * (rect[0] + rect[2])
            cy = 0.5 * (rect[1] + rect[3])
            if (
                calibration.hint.x0 <= cx <= calibration.hint.x1
                and calibration.hint.y0 <= cy <= calibration.hint.y1
            ):
                labels.append((float(match.group(1)), rect))
        return bind_two_source_color_legend(
            page,
            transform,
            calibration.plot,
            pixel_curves,
            list(dict.fromkeys(labels)),
            drawing_candidate=drawing_candidate,
            is_curve_color=is_curve_color,
        )
