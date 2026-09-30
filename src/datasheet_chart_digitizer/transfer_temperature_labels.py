"""Strict temperature-label parsing shared by transfer and body-diode panels.

``temperatures`` reads flattened panel text (transfer).  ``temperature_labels``
reads positioned source words and keeps each label's geometry, so callers can
use label positions as curve-identity evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence


TEMP_RE = re.compile(
    r"(?<![\w.])([+-]?\d+(?:\.\d+)?)\s*°?\s*C"
    r"(?!\s*(?:=(?!\s*j\b)|C\b)|[A-Za-z])",
    re.IGNORECASE,
)
EXPLICIT_DEGREE_TEMP_RE = re.compile(
    r"(?<![\w.])([+-]?\d+(?:\.\d+)?)\s*°\s*C(?![A-Za-z])",
    re.IGNORECASE,
)


def normalize_temperature_text(text: str) -> str:
    normalized = (
        text.replace("−", "-")
        .replace("–", "-")
        .replace("‑", "-")
        # EPC legends use U+02DA RING ABOVE instead of U+00B0 DEGREE SIGN.
        .replace("˚", "°")
        # U+00BA MASCULINE ORDINAL and U+2103 DEGREE CELSIUS stand in for "°C".
        .replace("º", "°")
        .replace("℃", "°C")
        # Some TI PDFs encode the printed degree sign as a private-use glyph.
        .replace("\uf0b0", "°")
    )
    # Some Vishay text streams separate a printed unary sign from its digits.
    normalized = re.sub(r"([+-])\s+(?=\d)", r"\1", normalized)
    # pdftotext can yield ``25°C C`` after reordering the TC subscript.
    return re.sub(r"(°?\s*C)\s+C\b", r"\1", normalized, flags=re.I)


def temperatures(text: str) -> list[float]:
    normalized = normalize_temperature_text(text)
    contextual = {
        float(value)
        for value in re.findall(
            r"\bT\s*(?:C|J)?\s*=\s*([+-]?\d+(?:\.\d+)?)\s*°?\s*C?",
            normalized,
            flags=re.I,
        )
    }
    if 2 <= len(contextual) <= 6:
        return sorted(contextual)
    # An explicit degree glyph is stronger evidence than trailing context.
    # EPC can reorder ``VDS = ...`` after ``25°C 125°C``; retain the stricter
    # guard for bare formula text such as ``5 C = Coss``.
    values = sorted({
        float(value)
        for matcher in (TEMP_RE, EXPLICIT_DEGREE_TEMP_RE)
        for value in matcher.findall(normalized)
    })
    if not 2 <= len(values) <= 6:
        raise RuntimeError(f"expected 2..6 temperature labels, found {values}")
    return values


# One Celsius label token: optional ``T``/``Tj``/``TJ``/``T_J``/``Tc``/``Ta``
# prefix with ``=``, a signed number, and a Celsius unit.  With a degree mark
# (``°``, ``o``, ``deg``) the unit may abut the next word (``°CT = 25`` where
# two legend entries touch); a bare ``C`` must stand alone and must not start a
# formula such as ``5 C = Coss``.
_LABEL_RE = re.compile(
    r"(?<![\w.])(?P<prefix>T\s*_?\s*[JjCcAa]?\s*=\s*)?"
    r"(?P<value>(?:[+-] ?)?\d+(?:\.\d+)?)\s*"
    r"(?:(?:°|deg\.?|[oO])\s*C(?![a-z])"
    r"|C(?![A-Za-z0-9])(?!\s*=))"
)
# A whole legend row: one label, optionally ``, max`` (typical/maximum pairs).
LEGEND_LINE_RE = re.compile(
    r"\s*" + _LABEL_RE.pattern + r"\s*(?:,\s*(?P<role>max))?\s*", re.I
)
_MIN_LABEL_C = -100.0
_MAX_LABEL_C = 250.0
# Source words chain into one label only when they touch on one text row;
# this keeps a log-axis tick such as ``10`` apart from a nearby ``°C`` label.
_WORD_GAP_MIN_PT = -0.75
_WORD_GAP_MAX_PT = 5.0
_ROW_MID_TOLERANCE = 0.6


class _SourceWord(Protocol):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class TemperatureLabel:
    value_c: float
    x0: float
    y0: float
    x1: float
    y1: float
    prefixed: bool

    @property
    def center(self) -> tuple[float, float]:
        return (0.5 * (self.x0 + self.x1), 0.5 * (self.y0 + self.y1))


def temperature_values_in_text(text: str) -> list[float]:
    """Distinct Celsius label values in flattened text, in the label range."""

    values = {
        float(match.group("value"))
        for match in _LABEL_RE.finditer(normalize_temperature_text(text))
    }
    return sorted(v for v in values if _MIN_LABEL_C <= v <= _MAX_LABEL_C)


def _same_row(left: _SourceWord, right: _SourceWord) -> bool:
    gap = right.x0 - left.x1
    height = max(left.y1 - left.y0, right.y1 - right.y0)
    mid_offset = abs((left.y0 + left.y1) - (right.y0 + right.y1)) / 2
    return _WORD_GAP_MIN_PT <= gap <= _WORD_GAP_MAX_PT and (
        mid_offset <= _ROW_MID_TOLERANCE * height
    )


def _word_chains(words: Sequence[_SourceWord]) -> list[list[_SourceWord]]:
    """Link each word to its nearest touching right neighbour on its row."""

    ordered = sorted(words, key=lambda word: (word.x0, word.y0))
    successor: dict[int, int] = {}
    predecessor: set[int] = set()
    for index, word in enumerate(ordered):
        candidates = [
            (other.x0 - word.x1, other_index)
            for other_index, other in enumerate(ordered)
            if other_index != index
            and other_index not in predecessor
            and other.x0 > word.x0
            and _same_row(word, other)
        ]
        if candidates:
            successor[index] = min(candidates)[1]
            predecessor.add(successor[index])
    chains = []
    for index in range(len(ordered)):
        if index in predecessor:
            continue
        chain = [ordered[index]]
        while index in successor:
            index = successor[index]
            chain.append(ordered[index])
        chains.append(chain)
    return chains


def temperature_labels(words: Sequence[_SourceWord]) -> list[TemperatureLabel]:
    """Parse Celsius labels from positioned source words, keeping geometry.

    Handles complete tokens (``Tj=25°C``), word-split tokens (``Tj`` ``=``
    ``-`` ``55`` ``°C``; ``175`` ``o`` ``C``) and ``℃``/``º``/``˚``/``deg C``.
    A number becomes a temperature only next to its Celsius unit, so axis
    ticks, ``VGS = 0 V`` and ``10 A`` are never labels.  Duplicate labels are
    all returned; callers decide whether a repeated value is one curve.
    """

    labels: list[TemperatureLabel] = []
    for chain in _word_chains(words):
        spans = []
        text = ""
        for word in chain:
            if text:
                text += " "
            piece = normalize_temperature_text(word.text.strip())
            spans.append((len(text), len(text) + len(piece), word))
            text += piece
        for match in _LABEL_RE.finditer(text):
            value = float(match.group("value").replace(" ", ""))
            if not _MIN_LABEL_C <= value <= _MAX_LABEL_C:
                continue
            owned = [
                word
                for start, end, word in spans
                if start < match.end() and end > match.start()
            ]
            labels.append(
                TemperatureLabel(
                    value,
                    min(word.x0 for word in owned),
                    min(word.y0 for word in owned),
                    max(word.x1 for word in owned),
                    max(word.y1 for word in owned),
                    match.group("prefix") is not None,
                )
            )
    return labels


def temperature_values(words: Sequence[_SourceWord]) -> list[float]:
    """Distinct Celsius label values parsed from positioned source words."""

    return sorted({label.value_c for label in temperature_labels(words)})
