"""Read Total Gate Charge rows (Qg typ and its test condition) from the spec table.

Reuses ``rdson_spec_table``'s word/line/header machinery and its rule that a
number is a value only when it sits in an evidenced Min/Typ/Max column. A row
is a Total Gate Charge row when its line carries the label (``Total Gate
Charge``, ``QG(tot)``, a bare ``Qg`` symbol) and none of the neighbouring
charges' labels (gate-source, gate-drain, threshold, sync, output, plateau).

The test condition is read from the row's own line and from adjacent
condition-only continuation lines (no owned value, no parameter label) within
``_CONTINUATION_PT``: "VGS = 0 V to 10 V, VDD = 30 V," / "ID = 25 A". A row
whose condition lacks a gate drive or a drain/supply voltage is returned with
those fields ``None``; the caller treats such a row as not evaluable. Merged
condition cells shared with other parameter rows are deliberately NOT
borrowed: which row they belong to is a layout guess.

Values must be in nC: the unit is read from the row line or its continuation
lines (``nC``); a row without it has ``typ_nc = None``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf

from .find_charts import group_words_into_lines
from .rdson_spec_table import (
    _cy,
    _header_for,
    _headers,
    _line,
    _owned_values,
    page_words,
)

_MAX_TABLE_PAGES = 4
_CONTINUATION_PT = 13.0

_QG_LABEL_RE = re.compile(
    r"total\s+gate\s+charge|gate\s+charge\s+total|Q\s*G\s*\(\s*tot|Q\s*g\s*\(\s*\d|(?<![A-Za-z])Q\s*g(?![A-Za-z(])",
    re.IGNORECASE,
)
# Other charges and prose that share words with the Qg row.
_FOREIGN_RE = re.compile(
    r"sync|threshold|Q\s*G\s*\(\s*th|gate[\s\-−–]*to[\s\-−–]*(?:source|drain)|gate[\s\-−–]*(?:source|drain)\s+charge"
    r"|miller|plateau|output\s+charge|Q\s*oss|Q\s*rr|recovery|\bQ\s*g\s*[sd]\b|Q\s*G\s*[SD]\b|Q\s*sw|switch(?:ing)?\s+charge"
    r"|figure|fig\.|\blow\b|•",
    re.IGNORECASE,
)
_OTHER_PARAMETER_RE = re.compile(
    r"\bQ\s*g\s*[sd]\b|Q\s*G\s*[SD]\b|Q\s*oss|Q\s*rr|\bt\s*[dfr]\b|t\s*d\s*\(|C\s*[io]ss|C\s*rss|R\s*g\b|V\s*plateau",
    re.IGNORECASE,
)
_VGS_RANGE_RE = re.compile(
    r"V\s*GS\s*=\s*[+\-−]?\s*0+(?:\.0+)?\s*V?\s*(?:to|~|…|\.\.\.?|-|−|–)\s*\+?\s*(?P<hi>\d+(?:\.\d+)?)\s*V",
    re.IGNORECASE,
)
_VGS_ALONE_RE = re.compile(r"V\s*GS\s*=\s*\+?\s*(?P<v>\d+(?:\.\d+)?)\s*V\b(?!\s*(?:to|~|-|−|–))", re.IGNORECASE)
# a drive printed in the row label: "Qg(4.5V)", "Qg(5)", "at 10 V"
_LABEL_DRIVE_RE = re.compile(
    r"Q\s*g?\s*\(\s*(?P<a>\d+(?:\.\d+)?)\s*V?\s*\)|\bat\s+(?P<b>\d+(?:\.\d+)?)\s*V\b", re.IGNORECASE
)
_VDRAIN_RE = re.compile(r"V\s*(?P<name>DD|DS)\s*(?:=|≈|~)\s*(?P<v>\d+(?:\.\d+)?)\s*V", re.IGNORECASE)
_ID_RE = re.compile(r"I\s*D\s*=\s*(?P<v>\d+(?:\.\d+)?)\s*(?P<u>m?A)", re.IGNORECASE)
_NUMBER_TOKEN_RE = re.compile(r"^[<>≤＜]?\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))$")
_NC_RE = re.compile(r"(?<![A-Za-z])nC\b")


@dataclass(frozen=True)
class QgSpecRow:
    page: int
    typ_nc: float | None
    # resolution of the printed typ ("117" -> 1, "26.5" -> 0.1): its rounding
    typ_resolution_nc: float | None
    vgs_v: float | None
    vdrain_v: float | None
    vdrain_name: str | None
    id_a: float | None
    row_text: str
    condition_text: str
    y_pt: float

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _is_qg_label(text: str) -> bool:
    return bool(_QG_LABEL_RE.search(text)) and not _FOREIGN_RE.search(text)


def _page_qg_rows(page_num: int, page) -> list[QgSpecRow]:
    lines = [_line(group) for group in group_words_into_lines(page_words(page))]
    headers = _headers(lines)
    if not headers:
        return []
    rows: list[QgSpecRow] = []
    for index, line in enumerate(lines):
        if not _is_qg_label(line.text):
            continue
        header = _header_for(line, headers)
        if header is None or "typ" not in header:
            continue
        values, _unparsed = _owned_values(line, header)
        if "typ" not in values:
            continue
        # condition-only continuation lines directly above/below the row
        block = [line]
        for step in (-1, 1):
            j = index + step
            while 0 <= j < len(lines) and abs(_cy(lines[j]) - _cy(line)) <= _CONTINUATION_PT:
                other = lines[j]
                other_header = _header_for(other, headers)
                other_values = _owned_values(other, other_header)[0] if other_header else {}
                if other_values or _is_qg_label(other.text) or _OTHER_PARAMETER_RE.search(other.text):
                    break
                block.append(other)
                j += step
        block.sort(key=_cy)
        condition_text = " ".join(item.text for item in block)
        # The drive printed in the row's own label wins: a merged condition
        # cell ("VGS=10V, VDS=50V" beside both AO Qg(10V) and Qg(4.5V) rows)
        # names the test circuit, not the charge the row reports.
        label_drive = _LABEL_DRIVE_RE.search(line.text)
        vgs = None
        if label_drive:
            vgs = float(label_drive.group("a") or label_drive.group("b"))
        else:
            match = _VGS_RANGE_RE.search(condition_text)
            if match:
                vgs = float(match.group("hi"))
            else:
                alone = [float(m.group("v")) for m in _VGS_ALONE_RE.finditer(condition_text)]
                alone = [v for v in alone if v > 0]
                if len(set(alone)) == 1:
                    vgs = alone[0]
        drains = [(m.group("name").upper(), float(m.group("v"))) for m in _VDRAIN_RE.finditer(condition_text)]
        drains = [(n, v) for n, v in drains if v > 0]
        vdrain = vname = None
        if len({v for _n, v in drains}) == 1:
            vname, vdrain = drains[0][0], drains[0][1]
        currents = [m for m in _ID_RE.finditer(condition_text)]
        id_a = None
        if len(currents) == 1:
            value = float(currents[0].group("v"))
            id_a = value / 1000.0 if currents[0].group("u").lower().startswith("m") else value
        unit_ok = bool(_NC_RE.search(condition_text))
        typ = float(values["typ"])
        token = next(
            (w.text.strip() for w in line.words
             if _NUMBER_TOKEN_RE.match(w.text.strip()) and float(_NUMBER_TOKEN_RE.match(w.text.strip()).group(1)) == typ),
            None,
        )
        resolution = None
        if token is not None:
            digits = _NUMBER_TOKEN_RE.match(token).group(1)
            decimals = len(digits.split(".")[1]) if "." in digits else 0
            resolution = 10.0 ** -decimals
        rows.append(QgSpecRow(
            page=page_num,
            typ_nc=typ if unit_ok else None,
            typ_resolution_nc=resolution if unit_ok else None,
            vgs_v=vgs,
            vdrain_v=vdrain,
            vdrain_name=vname,
            id_a=id_a,
            row_text=line.text,
            condition_text=condition_text,
            y_pt=round(_cy(line), 2),
        ))
    return rows


def parse_qg_spec_rows(pdf: str | Path) -> list[QgSpecRow]:
    """Every Total Gate Charge row this parser can own, in page order."""
    rows: list[QgSpecRow] = []
    with pymupdf.open(Path(pdf)) as document:
        for index in range(min(_MAX_TABLE_PAGES, document.page_count)):
            rows.extend(_page_qg_rows(index + 1, document[index]))
    return rows
