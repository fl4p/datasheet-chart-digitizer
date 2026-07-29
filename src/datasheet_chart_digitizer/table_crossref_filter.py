"""Semantic guard for caption-like text inside ruled specification rows."""

from __future__ import annotations

import re

try:
    from .finder_caption_geometry import bbox_is_shallow_ruled_row
except ImportError:  # pragma: no cover - direct script compatibility
    from finder_caption_geometry import bbox_is_shallow_ruled_row


def is_ruled_spec_crossref(words, bbox, horizontal_rules) -> bool:
    """Require both shallow-row geometry and neighboring numeric table values."""

    if not bbox_is_shallow_ruled_row(bbox, horizontal_rules):
        return False
    y0, y1 = bbox[1], bbox[3]
    row_tokens = [
        str(word.text).strip()
        for word in words
        if y0 - 5.0 <= 0.5 * (word.y0 + word.y1) <= y1 + 5.0
    ]
    numeric_count = sum(
        re.fullmatch(r"[+-]?\d+(?:\.\d+)?", token) is not None
        for token in row_tokens
    )
    units = {
        re.sub(r"[^a-zωΩΩ]+", "", token.lower())
        for token in row_tokens
    }
    return numeric_count >= 2 and bool(
        units & {"a", "v", "nc", "pc", "pf", "nf", "ω", "Ω", "Ω", "mω", "mΩ", "mΩ"}
    )
