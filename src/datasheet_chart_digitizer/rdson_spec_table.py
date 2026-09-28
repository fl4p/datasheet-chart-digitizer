"""Read RDS(on) rows from a MOSFET datasheet's electrical-characteristics table.

The anchors feed ``rdson_gate_voltage``'s tri-state validation, so the rule
that governs every other table reference in this library applies here too
(memory: dsdig table-reference column ownership): a number is a value only when
it sits in an evidenced Min/Typ/Max column. The first number after the symbol
is NOT safe -- test conditions (VGS = 4.5 V, ID = 20 A) come before the value
cells in half of the layouts this was written against, and a condition can be
numerically equal to a value.

How a row is recognised, from the words' positions rather than from a
flattened text dump:

  * A row is an RDS(on) row when its test condition names a non-zero gate
    voltage AND a drain current, and names none of VDS / VDD / RG / IS / f.
    That condition signature is what separates RDS(on) from its neighbours in
    the table (VGS(th) has VDS = VGS, gfs has VDS and ID, ID(on) has VDS,
    VSD has VGS = 0 and IS, Qg and the switching rows have VDD or VDS and RG).
    The page must also carry an RDS(on) symbol or "on-resistance" label within
    ``_LABEL_WINDOW_PT`` of the row, so a stray condition in a chart legend
    cannot become an anchor.
  * Value cells are the numeric words on the row's baseline, outside every
    condition span, whose centres fall within half a column pitch of a
    ``Min`` / ``Typ`` / ``Max`` header above them. A number that is not owned by
    a header column is not read at all.
  * International Rectifier prints the second RDS(on) row's values and its
    condition on different baselines. Such orphans are paired only when exactly
    one value-only line and one condition-only line sit within
    ``_ORPHAN_PAIR_WINDOW_PT`` of each other; the pairing is recorded.
  * The unit is read from the row block (mΩ / Ω, including the Symbol-font
    spellings ``m:``, ``mW`` and U+F057 that PDF text extraction produces for
    Ω). A row whose unit cannot be read carries ``unit=None`` and cannot anchor
    anything -- mΩ against Ω is a thousandfold error no other check catches.

Nothing here guesses: a row the parser cannot own is simply not returned.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from .find_charts import group_words_into_lines, line_bbox
from .finder_types import Word

_MAX_TABLE_PAGES = 4
_LABEL_WINDOW_PT = 48.0
_UNIT_WINDOW_PT = 22.0
_TEMPERATURE_LINE_WINDOW_PT = 12.0
_ORPHAN_PAIR_WINDOW_PT = 34.0
_HEADER_LOOKBACK_PT = 520.0

_NUMBER_RE = re.compile(r"^[<>≤＜]?\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))$")
_CONDITION_RE = re.compile(
    r"(?P<name>V\s*GS|V\s*DS|V\s*DD|I\s*D|I\s*S|I\s*SD|I\s*F|R\s*G(?:EN)?|R\s*L|"
    r"T\s*[JjCcAa]|f)\s*=\s*"
    r"(?P<value>[+-−±]?\s*(?:\d+(?:\.\d*)?|\.\d+))\s*"
    r"(?P<unit>m?[AV]|[°º]?\s*C|℃|[kKM]?Hz|Ω|W)?",
)
_RDS_LABEL_RE = re.compile(
    r"R\s*DS\s*\(?\s*on\s*\)?|on[-\s]*(?:state\s*)?resistance|R\s*DS\s*\(\s*ON",
    re.IGNORECASE,
)
_KIND_BY_CONDITION = {"TJ": "Tj", "TC": "Tc", "TA": "Ta"}
# "(Ta = 25°C unless ...)", "@ TJ = 25°C", "Electrical Characteristics(Ta=25℃)"
_HEADING_TEMPERATURE_RE = re.compile(r"\bT\s*([JjCcAa])\s*=\s*25\s*(?:°|º|o)?\s*(?:C\b|℃)")
_EXCLUDED_CONDITIONS = {"VDS", "VDD", "RG", "RGEN", "IS", "ISD", "IF", "F", "RL"}
_HEADER_TOKENS = {
    "min": re.compile(r"^min(?:imum)?\.?$", re.I),
    "typ": re.compile(r"^typ(?:ical)?\.?$", re.I),
    "max": re.compile(r"^max(?:imum)?\.?$", re.I),
}
_UNIT_TOKENS = {
    "mΩ": ("mohm", 1.0),
    "mohm": ("mohm", 1.0),
    "m:": ("mohm", 1.0),
    "mw": ("mohm", 1.0),
    "m": ("mohm", 1.0),
    "Ω": ("ohm", 1000.0),
    "ohm": ("ohm", 1000.0),
    ":": ("ohm", 1000.0),
    "w": ("ohm", 1000.0),
    "": ("ohm", 1000.0),
}


@dataclass(frozen=True)
class RdsonSpecRow:
    """One owned RDS(on) table row, values already converted to mΩ."""

    page: int
    vgs_v: float
    id_a: float | None
    temperature_c: float
    temperature_kind: str
    temperature_source: str
    min_mohm: float | None
    typ_mohm: float | None
    max_mohm: float | None
    unit_token: str
    pairing: str
    row_text: str
    bbox_pt: tuple[float, float, float, float]
    unparsed_cells: dict = field(default_factory=dict)
    temperature_evidence: str = ""

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class _Line:
    words: tuple[Word, ...]
    text: str
    bbox: tuple[float, float, float, float]
    conditions: tuple[tuple[str, float, str, int, int], ...]
    spans: tuple[tuple[int, int], ...]


def parse_rdson_spec_rows(pdf: Path) -> list[RdsonSpecRow]:
    """Return every RDS(on) row this parser can own, in page order."""
    rows: list[RdsonSpecRow] = []
    with pymupdf.open(pdf) as document:
        for index in range(min(_MAX_TABLE_PAGES, document.page_count)):
            words = [
                Word(str(w[4]), float(w[0]), float(w[1]), float(w[2]), float(w[3]))
                for w in document[index].get_text("words")
                if str(w[4]).strip()
            ]
            rows.extend(_page_rows(index + 1, words))
    return rows


def _page_rows(page_num: int, words: list[Word]) -> list[RdsonSpecRow]:
    lines = [_line(group) for group in group_words_into_lines(words)]
    headers = _headers(lines)
    if not headers:
        return []
    labels = [
        line.bbox for line in lines if _RDS_LABEL_RE.search(line.text)
    ]
    headings = [
        (_cy(line), _KIND_BY_CONDITION["T" + match.group(1).upper()], line.text)
        for line in lines
        for match in [_HEADING_TEMPERATURE_RE.search(line.text)] if match
    ]
    rows: list[RdsonSpecRow] = []
    value_only: list[tuple[_Line, dict[str, float]]] = []
    condition_only: list[_Line] = []
    for line in lines:
        header = _header_for(line, headers)
        values, unparsed = _owned_values(line, header) if header is not None else ({}, {})
        if _is_rds_condition(line) and _near_rds_label(line, labels):
            if values:
                row = _row(page_num, line, line, values, lines, header, "same_baseline", unparsed, headings)
                if row is not None:
                    rows.append(row)
            else:
                condition_only.append(line)
        elif values and not line.conditions and _near_rds_label(line, labels):
            value_only.append((line, values, unparsed))
    temperature_lines = [
        line for line in lines
        if line.conditions
        and _condition_names(line) <= {"TJ", "TC", "TA"}
    ]
    for condition in condition_only:
        near = [
            (value_line, values, unparsed)
            for value_line, values, unparsed in value_only
            if abs(_cy(value_line) - _cy(condition)) <= _ORPHAN_PAIR_WINDOW_PT
        ]
        claimants = [
            other for other in condition_only
            if any(abs(_cy(v) - _cy(other)) <= _ORPHAN_PAIR_WINDOW_PT for v, _, _u in near)
        ]
        if len(near) != 1 or len(claimants) != 1:
            continue
        value_line, values, unparsed = near[0]
        header = _header_for(value_line, headers)
        row = _row(
            page_num, condition, value_line, values, lines, header,
            "values_and_condition_on_separate_baselines", unparsed, headings,
        )
        if row is not None:
            rows.append(row)
    return _attach_temperature_lines(rows, temperature_lines)


def _attach_temperature_lines(
    rows: list[RdsonSpecRow], temperature_lines: list[_Line]
) -> list[RdsonSpecRow]:
    """Bind a temperature printed on its own baseline to exactly one row.

    Fairchild sets "TJ = 175oC" one baseline below its "ID = 35A, VGS = 10V,"
    condition, and the values between the two.  Leaving that row at the table
    default of 25 C would anchor a 175 C maximum against a 25 C curve.  A
    temperature line is attached to the nearest row only when it is clearly
    nearer to it than to any other row; otherwise every row it could belong to
    is dropped rather than left at an assumed temperature.
    """
    drop: set[int] = set()
    updates: dict[int, tuple[float, str, str]] = {}
    for line in temperature_lines:
        ly = _cy(line)
        near = sorted(
            (abs(0.5 * (row.bbox_pt[1] + row.bbox_pt[3]) - ly), index)
            for index, row in enumerate(rows)
            if abs(0.5 * (row.bbox_pt[1] + row.bbox_pt[3]) - ly) <= _TEMPERATURE_LINE_WINDOW_PT
        )
        if not near:
            continue
        temps = [value for name, value, *_ in line.conditions]
        names = {_KIND_BY_CONDITION.get(name, "unspecified") for name, *_ in line.conditions}
        if len(near) > 1 and near[1][0] - near[0][0] < 3.0 or len(set(temps)) != 1 or len(names) != 1:
            drop.update(index for _, index in near)
            continue
        updates[near[0][1]] = (temps[0], names.pop(), line.text)
    out = []
    for index, row in enumerate(rows):
        if index in drop:
            continue
        if index in updates:
            if row.temperature_source == "row_condition":
                continue
            value, kind, evidence = updates[index]
            row = RdsonSpecRow(**{
                **asdict(row), "temperature_c": value, "temperature_kind": kind,
                "temperature_source": "adjacent_baseline_condition", "temperature_evidence": evidence,
            })
        out.append(row)
    return out


def _line(group: list[Word]) -> _Line:
    text_parts: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for word in group:
        if text_parts:
            cursor += 1
        spans.append((cursor, cursor + len(word.text)))
        text_parts.append(word.text)
        cursor += len(word.text)
    text = " ".join(text_parts)
    conditions = []
    for match in _CONDITION_RE.finditer(text):
        name = re.sub(r"\s+", "", match.group("name")).upper()
        raw = re.sub(r"[\s±]", "", match.group("value")).replace("−", "-")
        try:
            value = float(raw)
        except ValueError:
            continue
        conditions.append((name, value, match.group("unit") or "", match.start(), match.end()))
    return _Line(tuple(group), text, line_bbox(group), tuple(conditions), tuple(spans))


def _cy(line: _Line) -> float:
    return 0.5 * (line.bbox[1] + line.bbox[3])


def _headers(lines: list[_Line]) -> list[tuple[float, dict[str, float]]]:
    headers = []
    for line in lines:
        columns: dict[str, float] = {}
        for word in line.words:
            for name, pattern in _HEADER_TOKENS.items():
                if pattern.match(word.text.strip()) and name not in columns:
                    columns[name] = 0.5 * (word.x0 + word.x1)
        if "typ" in columns and ("max" in columns or "min" in columns):
            headers.append((_cy(line), columns))
    return headers


def _header_for(line: _Line, headers) -> dict[str, float] | None:
    above = [
        (y, columns) for y, columns in headers
        if y < _cy(line) and _cy(line) - y <= _HEADER_LOOKBACK_PT
    ]
    return max(above, key=lambda item: item[0])[1] if above else None


def _owned_values(line: _Line, header: dict[str, float]) -> tuple[dict[str, float], dict[str, str]]:
    """Numbers in header-owned value columns and outside condition spans.

    A cell that holds digits but is not a number ("12..8" in CSD17302Q5A's
    3 V max) is returned separately and never read as a value.
    """
    positions = sorted(header.values())
    pitch = min((b - a for a, b in zip(positions, positions[1:])), default=40.0)
    tolerance = 0.5 * pitch
    values: dict[str, float] = {}
    unparsed: dict[str, str] = {}
    for word, (start, end) in zip(line.words, line.spans):
        if any(start < c_end and end > c_start for *_, c_start, c_end in line.conditions):
            continue
        token = word.text.strip()
        match = _NUMBER_RE.match(token)
        if match is None and not re.search(r"\d", token):
            continue
        center = 0.5 * (word.x0 + word.x1)
        ranked = sorted((abs(center - x), name) for name, x in header.items())
        distance, column = ranked[0]
        if distance > tolerance:
            continue
        if len(ranked) > 1 and ranked[1][0] - distance < 0.15 * pitch:
            continue
        if match is None:
            unparsed[column] = token
            continue
        if column in values:
            return {}, {}
        values[column] = float(match.group(1))
    return values, unparsed


def _condition_names(line: _Line) -> set[str]:
    return {name for name, *_ in line.conditions}


def _is_rds_condition(line: _Line) -> bool:
    names = _condition_names(line)
    vgs = [value for name, value, *_ in line.conditions if name == "VGS"]
    return (
        bool(vgs)
        and any(abs(value) > 0.0 for value in vgs)
        and "ID" in names
        and not names & _EXCLUDED_CONDITIONS
    )


def _near_rds_label(line: _Line, labels) -> bool:
    return any(abs(0.5 * (bbox[1] + bbox[3]) - _cy(line)) <= _LABEL_WINDOW_PT for bbox in labels)


def _row(
    page_num: int,
    condition: _Line,
    value_line: _Line,
    values: dict[str, float],
    lines: list[_Line],
    header: dict[str, float] | None,
    pairing: str,
    unparsed: dict[str, str] | None = None,
    headings: list | None = None,
) -> RdsonSpecRow | None:
    unit = _row_unit(condition, value_line, lines)
    vgs = next(value for name, value, *_ in condition.conditions if name == "VGS")
    current = next((value for name, value, *_ in condition.conditions if name == "ID"), None)
    temps = [(value, name) for name, value, *_ in condition.conditions if name in {"TJ", "TC", "TA"}]
    if len(temps) > 1:
        return None
    evidence = ""
    if temps:
        temperature, kind, source = temps[0][0], _KIND_BY_CONDITION[temps[0][1]], "row_condition"
    else:
        above = [h for h in (headings or []) if h[0] < _cy(condition)]
        if above:
            _y, kind, evidence = max(above, key=lambda h: h[0])
            temperature, source = 25.0, "table_heading"
        else:
            temperature, kind, source = 25.0, "unspecified", "table_default_25C_assumed"
    scale = unit[1] if unit is not None else None

    def convert(key: str) -> float | None:
        if key not in values or scale is None:
            return None
        return round(values[key] * scale, 6)

    return RdsonSpecRow(
        page=page_num,
        vgs_v=abs(vgs),
        id_a=abs(current) if current is not None else None,
        temperature_c=temperature,
        temperature_kind=kind,
        temperature_source=source,
        min_mohm=convert("min"),
        typ_mohm=convert("typ"),
        max_mohm=convert("max"),
        unit_token=unit[0] if unit is not None else "",
        pairing=pairing,
        row_text=condition.text if condition is value_line else f"{value_line.text} || {condition.text}",
        bbox_pt=tuple(round(v, 2) for v in condition.bbox),  # type: ignore[arg-type]
        unparsed_cells=dict(unparsed or {}),
        temperature_evidence=evidence,
    )


def _normalize_ohm(text: str) -> str:
    """Fold the OHM SIGN (U+2126) into GREEK CAPITAL OMEGA before matching."""
    return text.replace("\u2126", "\u03a9")


def _row_unit(condition: _Line, value_line: _Line, lines: list[_Line]) -> tuple[str, float] | None:
    """The row block's resistance unit, nearest line first; None if unreadable."""
    anchor_y = 0.5 * (_cy(condition) + _cy(value_line))
    candidates: list[tuple[float, str, float]] = []
    for line in lines:
        distance = abs(_cy(line) - anchor_y)
        if distance > _UNIT_WINDOW_PT:
            continue
        for word in line.words:
            token = _normalize_ohm(word.text.strip().rstrip(".,"))
            key = token if token in _UNIT_TOKENS else token.lower()
            if key in _UNIT_TOKENS:
                candidates.append((distance, token, _UNIT_TOKENS[key][1]))
    if not candidates:
        return None
    candidates.sort()
    nearest = [c for c in candidates if c[0] - candidates[0][0] <= 0.5]
    if len({scale for _, _, scale in nearest}) != 1:
        return None
    return nearest[0][1], nearest[0][2]
