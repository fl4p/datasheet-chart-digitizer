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
    # "m" + a glyph with no Unicode map (FDP5800's Omega, see page_words).
    # In an RDS(on) row block the only unit spelled "m" + one glyph is mOhm.
    "m\ufffd": ("mohm", 1.0),
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
    unit_source: str = "row"
    id_unit: str = ""
    qualifier: str = ""

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
            rows.extend(_page_rows(index + 1, page_words(document[index])))
    return rows


def page_words(page) -> list[Word]:
    """PyMuPDF's words, with two text-layer defects of Symbol-font tables undone.

    * U+F020 is the Symbol font's SPACE. PyMuPDF does not split on it, so
      International Rectifier's unit cell and the next row's "VGS" come out
      as ONE word ("m\uf057\uf020\uf020VGS", IRLB8314): the unit is then
      unreadable and the VGS lands on the wrong baseline. Such a word is
      split at the U+F020 run, each piece boxed by its own glyphs.
    * A glyph whose font has no Unicode map comes out as a control
      character (FDP5800's Omega is U+0002 in IntDutch801G), which PyMuPDF
      treats as a word break and drops. When one directly follows a word
      (gap under 1 pt, same baseline) the word gets U+FFFD appended, so the
      unit reader can see "m" + an undecodable glyph instead of a bare "m".
    """
    raw = [w for w in page.get_text("words") if str(w[4]).strip()]
    chars = [
        (c["c"], tuple(float(v) for v in c["bbox"]))
        for block in page.get_text("rawdict").get("blocks", [])
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        for c in span.get("chars", [])
    ]
    undecoded = [box for c, box in chars if len(c) == 1 and ord(c) < 0x20 and c not in "\t\n\r"]
    out: list[Word] = []
    for w in raw:
        text, box = str(w[4]), tuple(float(v) for v in w[:4])
        pieces = _split_symbol_spaces(text, box, chars) if "\uf020" in text else [(text, box)]
        for piece, (x0, y0, x1, y1) in pieces:
            if any(-0.5 <= g[0] - x1 <= 1.0 and min(y1, g[3]) - max(y0, g[1]) > 0.5 * (y1 - y0) for g in undecoded):
                piece += "\ufffd"
            out.append(Word(piece, x0, y0, x1, y1))
    return out


def _split_symbol_spaces(text: str, box, chars) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Split a word at its U+F020 runs; box each piece by its own glyphs.

    The word's glyphs are found as the contiguous run of rawdict characters
    that spells the word inside its box. If they cannot be found, the word
    is split with its x range shared out by character count and the word's
    own y range (no glyph-level evidence, so no baseline claim is made).
    """
    inside = [(c, b) for c, b in chars if b[0] >= box[0] - 0.5 and b[2] <= box[2] + 0.5
              and b[1] >= box[1] - 0.5 and b[3] <= box[3] + 0.5]
    seq = "".join(c for c, _ in inside)
    start = seq.find(text)
    out = []
    if start >= 0:
        glyphs = inside[start:start + len(text)]
        piece: list = []
        for c, b in glyphs + [("\uf020", None)]:
            if c == "\uf020" or c.isspace():
                if piece:
                    out.append(("".join(ch for ch, _ in piece), (
                        min(g[0] for _, g in piece), min(g[1] for _, g in piece),
                        max(g[2] for _, g in piece), max(g[3] for _, g in piece))))
                piece = []
            else:
                piece.append((c, b))
        return out
    step = (box[2] - box[0]) / max(len(text), 1)
    for match in re.finditer(r"[^\uf020\s]+", text):
        out.append((match.group(0), (box[0] + step * match.start(), box[1], box[0] + step * match.end(), box[3])))
    return out


def _page_rows(page_num: int, words: list[Word]) -> list[RdsonSpecRow]:
    lines = [_line(group) for group in group_words_into_lines(words)]
    headers = _headers(lines)
    if not headers:
        return []
    labels = [
        line.bbox for line in lines if _RDS_LABEL_RE.search(line.text)
    ]
    # A heading states the table's default temperature and nothing else: a
    # row such as "BVDSS ... ID = 250 uA, VGS = 0 V, TJ = 25 C" (FDP5800)
    # names its OWN test temperature, not the table's.
    headings = [
        (_cy(line), _KIND_BY_CONDITION["T" + match.group(1).upper()], line.text)
        for line in lines
        for match in [_HEADING_TEMPERATURE_RE.search(line.text)] if match
        and _condition_names(line) <= {"TJ", "TC", "TA"}
    ]
    specs: list[dict] = []
    value_only: list[tuple[_Line, dict[str, float]]] = []
    condition_only: list[_Line] = []
    for line in lines:
        if _foreign_parameter(line):
            continue
        header = _header_for(line, headers)
        values, unparsed = _owned_values(line, header) if header is not None else ({}, {})
        if _is_rds_condition(line) and _near_rds_label(line, labels):
            if values:
                specs.append({"condition": line, "value_line": line, "values": values, "header": header,
                              "pairing": "same_baseline", "unparsed": unparsed})
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
        specs.append({"condition": condition, "value_line": value_line, "values": values,
                      "header": _header_for(value_line, headers),
                      "pairing": "values_and_condition_on_separate_baselines", "unparsed": unparsed})
    _resolve_units(specs, lines, headers)
    rows: list[RdsonSpecRow] = []
    for spec in specs:
        row = _row(page_num, spec, headings, _qualifier(spec, specs, lines))
        if row is not None:
            rows.append(row)
    return _attach_temperature_lines(rows, temperature_lines)


# Rows of OTHER parameters whose test condition also names VGS and ID:
# Goford's "Forward Transconductance gFS VGS = 5V, ID = 50A" (unit S),
# Toshiba's "V(BR)DSX ... ID = 10 mA, VGS = -20 V", and Microchip's
# "Change in RDS(ON) with Temperature  dRDS(ON) ... %/C  VGS = 10V, ID = 1A".
# A line naming one of these is never an RDS(on) row, whatever its condition.
_FOREIGN_PARAMETER_RE = re.compile(
    r"\bg\s*fs\b|transconductance|V\s*\(\s*BR\s*\)|\bBV\s*DSS|breakdown|[\u0394\uf044]|\bchange\s+in\b"
    r"|coefficient|%\s*/\s*[°º]?\s*[CK]\b|threshold|V\s*GS\s*\(\s*th|\bI\s*[DG]SS\b|leakage|I\s*D\s*\(\s*on\s*\)",
    re.IGNORECASE,
)
_BLOCK_GAP_PT = 50.0
_QUALIFIER_GAP_PT = 8.0


def _foreign_parameter(line: _Line) -> bool:
    return _FOREIGN_PARAMETER_RE.search(line.text) is not None


def _spec_y(spec: dict) -> float:
    return 0.5 * (_cy(spec["condition"]) + _cy(spec["value_line"]))


def _resolve_units(specs: list[dict], lines: list[_Line], headers) -> None:
    """Each spec's unit: its own row block's reading, else its RDS(on) block's.

    onsemi (FDP5800) and Infineon (IPP100N06S2L05) print the unit ONCE for
    the whole RDS(on) block, on its first row; the later rows sit farther
    than ``_UNIT_WINDOW_PT`` from it. A row without a unit of its own
    inherits the block's unit only when (a) the block is a run of RDS(on)
    rows under the same header with no other parameter's value line between
    consecutive rows and no gap over ``_BLOCK_GAP_PT``, and (b) every unit
    read inside the block has the same scale. Otherwise it stays unreadable.
    """
    for spec in specs:
        spec["unit"] = _row_unit(spec["condition"], spec["value_line"], lines)
        spec["unit_source"] = "row" if spec["unit"] is not None else "unreadable"
    ordered = sorted(specs, key=_spec_y)
    own = {id(line) for spec in specs for line in (spec["condition"], spec["value_line"])}
    blocks: list[list[dict]] = []
    for spec in ordered:
        if blocks:
            previous = blocks[-1][-1]
            y0, y1 = _spec_y(previous), _spec_y(spec)
            between = [
                line for line in lines
                if id(line) not in own and y0 < _cy(line) < y1
                and (spec["header"] is not None and _owned_values(line, spec["header"])[0] or _foreign_parameter(line))
            ]
            if previous["header"] is spec["header"] and y1 - y0 <= _BLOCK_GAP_PT and not between:
                blocks[-1].append(spec)
                continue
        blocks.append([spec])
    for block in blocks:
        read = [s for s in block if s["unit"] is not None]
        if not read or len({s["unit"][1] for s in read}) != 1:
            continue
        donor = read[0]
        for spec in block:
            if spec["unit"] is None:
                spec["unit"] = donor["unit"]
                spec["unit_source"] = (
                    f"row_block: unit printed once for the RDS(on) block, on the row at y={_spec_y(donor):.1f} pt"
                )


def _qualifier(spec: dict, specs: list[dict], lines: list[_Line]) -> str:
    """A text line printed under a row's condition that qualifies it ("SMD version").

    Infineon repeats the RDS(on) rows for the SMD package with the words
    "SMD version" under the condition. Without that qualifier the second
    4.5 V row reads as a contradiction of the first. The line must sit
    within ``_QUALIFIER_GAP_PT`` below the row, inside the condition's
    column, and carry no condition, no value and no RDS(on) label.
    """
    condition = spec["condition"]
    bottom = max(spec["condition"].bbox[3], spec["value_line"].bbox[3])
    # the condition CELL: the words the condition matches cover, not the whole
    # line (which also holds the parameter label: "Resistance" is not a qualifier)
    cell = [
        word for word, (start, end) in zip(condition.words, condition.spans)
        if any(start < c_end and end > c_start for *_, c_start, c_end in condition.conditions)
    ]
    if not cell:
        return ""
    x0, x1 = min(w.x0 for w in cell) - 5.0, max(w.x1 for w in cell) + 5.0
    taken = {id(line) for other in specs for line in (other["condition"], other["value_line"])}
    out = []
    for line in lines:
        if id(line) in taken or line.conditions or _RDS_LABEL_RE.search(line.text):
            continue
        if not (0.0 <= line.bbox[1] - bottom <= _QUALIFIER_GAP_PT or 0.0 <= _cy(line) - _cy(spec["value_line"]) <= _QUALIFIER_GAP_PT):
            continue
        if line.bbox[0] < x0 or line.bbox[2] > x1 or not re.search(r"[A-Za-z]{3,}", line.text):
            continue
        if spec["header"] is not None and _owned_values(line, spec["header"])[0]:
            continue
        out.append(line.text)
    return " ".join(out)


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


def _row(page_num: int, spec: dict, headings: list | None, qualifier: str = "") -> RdsonSpecRow | None:
    condition, value_line, values = spec["condition"], spec["value_line"], spec["values"]
    unit = spec["unit"]
    vgs = next(value for name, value, *_ in condition.conditions if name == "VGS")
    current = next(((value, unit_text) for name, value, unit_text, *_ in condition.conditions if name == "ID"), None)
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

    id_a = None
    if current is not None:
        # "ID = 25mA" (BS107P, Microchip TN/VN): the printed unit decides.
        id_a = abs(current[0]) / (1000.0 if current[1] == "mA" else 1.0)
        id_a = round(id_a, 9)
    return RdsonSpecRow(
        page=page_num,
        vgs_v=abs(vgs),
        id_a=id_a,
        temperature_c=temperature,
        temperature_kind=kind,
        temperature_source=source,
        min_mohm=convert("min"),
        typ_mohm=convert("typ"),
        max_mohm=convert("max"),
        unit_token=unit[0] if unit is not None else "",
        pairing=spec["pairing"],
        row_text=condition.text if condition is value_line else f"{value_line.text} || {condition.text}",
        bbox_pt=tuple(round(v, 2) for v in condition.bbox),  # type: ignore[arg-type]
        unparsed_cells=dict(spec["unparsed"] or {}),
        temperature_evidence=evidence,
        unit_source=spec.get("unit_source", "row"),
        id_unit=(current[1] or "A") if current is not None else "",
        qualifier=qualifier,
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
