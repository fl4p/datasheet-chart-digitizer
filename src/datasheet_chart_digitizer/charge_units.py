"""Strict recognition of locally printed gate-charge units."""

from __future__ import annotations

import re


_NANOCoulomb_RE = re.compile(
    r"(?<![a-z])n\s*c(?![a-z])|\bnano(?:\s|-)?coulombs?\b",
    re.IGNORECASE,
)
_MICROCoulomb_RE = re.compile(
    r"(?<![a-z])u\s*c(?![a-z])|\bmicro(?:\s|-)?coulombs?\b",
    re.IGNORECASE,
)


def gate_charge_unit(text: str) -> str | None:
    """Return one explicit charge unit; refuse substrings and mixed units."""

    normalized = text.replace("μ", "u").replace("µ", "u")
    units: set[str] = set()
    if _NANOCoulomb_RE.search(normalized):
        units.add("nC")
    if _MICROCoulomb_RE.search(normalized):
        units.add("uC")
    return next(iter(units)) if len(units) == 1 else None
