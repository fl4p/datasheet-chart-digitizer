"""Strict temperature-label parsing for transfer-characteristic panels."""

from __future__ import annotations

import re


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
