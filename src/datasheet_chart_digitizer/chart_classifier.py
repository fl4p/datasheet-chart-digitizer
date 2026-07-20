"""Pure chart-family classification shared by finder paths."""

from __future__ import annotations

import re


CAPACITANCE_WORDS = {"ciss", "coss", "crss", "capacitance", "capacitances"}


def _normalized_chart_text(text: str) -> str:
    normalized = text.lower().replace("‑", "-").replace("–", "-")
    normalized = re.sub(r"[-_/]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def is_rdson_chart_title(title: str) -> bool:
    """Recognize RDS(on) chart titles without substring-matching ordinary words."""
    normalized = _normalized_chart_text(title)
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    return (
        "drainsourceonresistance" in compact
        or "drainsourceonstateresistance" in compact
        or "onstateresistance" in compact
        or "onresistance" in compact
        or re.search(r"\br\s*d\s*s\s*(?:\(\s*on\s*\)|on)(?=\W|$)", normalized) is not None
    )


def rdson_formula_direction(title: str) -> str | None:
    """Return the axis named by a compact ``RDS(on)-X`` formula title."""
    compact = re.sub(r"[^a-z0-9]+", "", _normalized_chart_text(title))
    if re.fullmatch(r"rds(?:on)?i(?:d|ds)", compact):
        return "current"
    if re.fullmatch(r"rds(?:on)?t(?:a|j|c)", compact):
        return "temperature"
    return None


def compact_formula_chart_kind(title: str) -> str | None:
    """Classify an exact quantity-versus-quantity caption formula."""
    compact = re.sub(r"[^a-z0-9]+", "", _normalized_chart_text(title))
    if re.fullmatch(r"i(?:d|ds)vgs", compact):
        return "transfer"
    if rdson_formula_direction(title) is not None:
        return "rds_on"
    if re.fullmatch(r"i(?:dr|s)v(?:ds|sd)", compact):
        return "body_diode"
    if re.fullmatch(r"(?:normalized)?vbrdsst(?:a|j|c)", compact):
        return "breakdown_voltage"
    return None


def _is_body_diode_chart_text(text: str) -> bool:
    if "recovery" in text or "test circuit" in text:
        return False
    has_context = "diode" in text or "source drain" in text or "drain source" in text
    return has_context and (
        "forward characteristics" in text
        or "diode forward" in text
        or "forward voltage" in text
        or "body diode characteristics" in text
        or "body diode transfer characteristics" in text
    )


def is_spaced_figure_start(tokens: list[str], index: int) -> bool:
    """Recognize OCR-split ``Fi g u r e N`` caption starts."""
    return [token.lower().strip(".") for token in tokens[index:index + 5]] == ["fi", "g", "u", "r", "e"]


def repair_spaced_caption_text(text: str) -> str:
    """Repair the narrow OCR spelling used by some two-column captions."""
    return re.sub(
        r"(?i)^Fi\s+g\s+u\s+r\s+e\s+(\d+)\s+\.?G\s+a\s+te\b",
        r"Figure \1. Gate",
        text,
    )


def is_spec_table_header_title(title: str) -> bool:
    """Reject chart captions contaminated by a specification-table header."""
    normalized = _normalized_chart_text(title)
    markers = (
        r"\bsymbol\b",
        r"\btest conditions?\b",
        r"\bmin(?:imum)?\b",
        r"\btyp(?:ical)?\b",
        r"\bmax(?:imum)?\b",
        r"\bunits?\b",
    )
    return sum(re.search(marker, normalized) is not None for marker in markers) >= 3


def is_marketing_feature_title(title: str) -> bool:
    """Reject numbered feature-list prose that happens to name chart terms."""
    normalized = _normalized_chart_text(title)
    return (
        len(normalized.split()) >= 5
        and re.search(r"\b(?:to|for)\b", normalized) is not None
        and re.search(r"\b(?:loss(?:es)?|efficien\w*|faster)\b", normalized) is not None
    )


def classify_chart(title: str, text: str) -> str:
    normalized_title = _normalized_chart_text(title)
    if "test circuit" in normalized_title:
        return "chart"
    formula_kind = compact_formula_chart_kind(title)
    if formula_kind is not None:
        return formula_kind
    if _is_body_diode_chart_text(normalized_title):
        return "body_diode"
    if (
        ("coss" in normalized_title or "output capacitance" in normalized_title)
        and "energy" in normalized_title
    ):
        return "coss_energy"

    haystack = _normalized_chart_text(f"{title} {text}")
    if any(word in haystack for word in CAPACITANCE_WORDS):
        return "capacitances"
    if "gate charge" in haystack or "dynamic input output" in haystack:
        return "gate_charge"
    if "safe operating" in haystack:
        return "safe_operating_area"
    if "thermal impedance" in haystack or "zth" in haystack:
        return "thermal_impedance"
    if _is_body_diode_chart_text(haystack):
        return "body_diode"
    compact = re.sub(r"[^a-z0-9]+", "", haystack)
    if "breakdown voltage" in haystack or (
        "vbrdss" in compact and "temperature" in haystack
    ):
        return "breakdown_voltage"
    if "transfer characteristics" in haystack:
        return "transfer"
    if "output characteristics" in haystack:
        return "output"
    if is_rdson_chart_title(haystack):
        return "rds_on"
    return "chart"


def title_owns_chart_kind(title: str, number: int) -> str | None:
    """Return an explicit caption's family when adjacent text cannot override it."""
    kind = classify_chart(title, "")
    if kind == "chart":
        return None
    if number < 900 or "characteristics" in title.lower():
        return kind
    return None
