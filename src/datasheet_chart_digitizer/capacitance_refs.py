"""Datasheet table reference parsing for MOSFET capacitance charts."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from .capacitance_types import CapAnchor, OutputChargeReference

_CAP_NAMES = ("Ciss", "Coss", "Crss")
_VDS_RE = re.compile(r"VDS\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*V")
# Signed collection for the repair passes: a p-channel 'VDS=-15 V' must land in the
# distinct-value set (so it can force a refusal), not silently vanish because the
# unsigned pattern cannot see it.
_VDS_SIGNED_RE = re.compile(r"VDS\s*=\s*(-?[0-9]+(?:\.[0-9]+)?)\s*V")
# The capacitance measurement signature on a whitespace-compacted PDF page, in the
# two printed orders (Infineon: VGS first; hxy/diodes style: VDS first). The optional
# groups in the first pattern tolerate degraded text layers that drop subscript
# glyphs entirely ('V =0 V,' / '=50 V, =1 MHz'); the '=0V' ... '=1MHz' bracket with
# exactly one voltage token between keeps false positives out even on compacted
# text. The VDS-first order is matched only fully-labelled — degraded it is
# indistinguishable from the VGS-first order and must refuse.
_PDF_CAP_SIGNATURE_RES = (
    re.compile(r"V(?:GS)?=0V[,;](?:V(?:DS)?)?=(-?[0-9]+(?:\.[0-9]+)?)V[,;](?:f)?=1MHz"),
    re.compile(r"VDS=(-?[0-9]+(?:\.[0-9]+)?)V[,;]VGS=0V[,;]f=1MHz"),
)
_NON_PF_UNIT_RE = re.compile(r"\b[nuµ]F\b")
# The output-charge condition ("VDD=x V, VGS=0 V") on a whitespace-compacted page,
# anchored on the 'Outputcharge' row marker and its nC unit so it can never borrow a
# voltage from a neighboring row. Note the token order is the INVERSE of the
# capacitance signature ('=xV,' BEFORE '=0V') — that order is what still
# disambiguates the two conditions when degraded text layers drop the subscript
# labels entirely ('V =50 V,' / '=0 V'). The range form 'VDS=0...50V' is matched
# alongside.
_PDF_QOSS_VINT_RES = (
    re.compile(r"Outputcharge.{0,60}?nC"
               r"V[A-Z]{0,2}=(-?[0-9]+(?:\.[0-9]+)?)V[,;](?:V[A-Z]{0,2})?=0V"),
    re.compile(r"Outputcharge.{0,60}?nC"
               r"V[A-Z]{0,2}=0(?:\.{2,3}|…)([0-9]+(?:\.[0-9]+)?)V"),
)


# Anchors supplied by the caller (fetlib), keyed by part. When a table is installed it
# is the ONLY anchor source: this module must not run a second, weaker spec-table parser
# over PDFs the caller already parses properly. Its own scrapers stay for standalone use.
#
# Why: the scrapers here are case-sensitive on the printed symbol ("Coss" vs EPC's
# "COSS"), so every EPC part yielded {} while the caller held Coss=557 pF @ 50 V for the
# same device -- reported downstream as `missing_coss_anchor`, i.e. "the datasheet has no
# Coss", which was never true. The value picker also reads a condition cell as a value
# (Ciss=25 pF out of "VDS = 25 V" on hxy/toshiba renders).
_SUPPLIED_ANCHORS: dict[str, dict] | None = None


def install_anchor_table(table: dict[str, dict] | None) -> None:
    """Install (or clear with None) the caller-supplied anchor table.

    A part ABSENT from an installed table has no anchors -- it does NOT fall back to
    scraping. Falling back would reintroduce exactly the silently-wrong values this
    replaces, and a missing anchor is a rejected export, never a guessed curve."""
    global _SUPPLIED_ANCHORS
    _SUPPLIED_ANCHORS = table


def supplied_anchor_table_installed() -> bool:
    """Whether anchor identity is owned by the caller's evidence table."""
    return _SUPPLIED_ANCHORS is not None


def load_anchor_table(path: Path) -> dict[str, dict]:
    import json
    with Path(path).open() as fh:
        return json.load(fh)


def parse_capacitance_anchors(part: str, datasheet_root: Path) -> dict[str, CapAnchor]:
    if _SUPPLIED_ANCHORS is not None:
        entry = _SUPPLIED_ANCHORS.get(part) or {}
        out: dict[str, CapAnchor] = {}
        for name, a in (entry.get("anchors") or {}).items():
            if name in _CAP_NAMES and a.get("value_pf") is not None \
                    and a.get("vds_v") is not None:
                out[name] = CapAnchor(name=name, value_pf=float(a["value_pf"]),
                                      vds_v=float(a["vds_v"]))
        return out

    csv_path = _anchor_csv_path(part, datasheet_root)
    if csv_path is None:
        return {}

    with csv_path.open(newline="", errors="replace") as f:
        rows = list(csv.reader(f))

    # Pass 1 — complete in-row anchors (value + pF + VDS on the same row), the
    # classic Infineon N5/NF2S table render. Unchanged semantics: later rows
    # overwrite earlier ones.
    anchors: dict[str, CapAnchor] = {}
    for row in rows:
        row_text = " ".join(cell.strip() for cell in row if cell.strip())
        for name in _CAP_NAMES:
            if name not in row:
                continue
            try:
                symbol_idx = row.index(name)
            except ValueError:
                continue
            tail = row[symbol_idx + 1 :]
            value_pf = _first_number_before_unit(tail, "pF")
            vds_match = _VDS_RE.search(row_text)
            if value_pf is not None and vds_match:
                anchors[name] = CapAnchor(
                    name=name,
                    value_pf=value_pf,
                    vds_v=float(vds_match.group(1)),
                )

    # Pass 2 — block repair for the newer Infineon renders (OptiMOS M5SC/M6/M7/M8,
    # NF2S-AKMA1, IRF100PW): the printed table states the pF unit and the
    # "VGS=0 V, VDS=x V, f=1 MHz" condition ONCE for the contiguous
    # Ciss/Coss/Crss block (or the extraction dropped them from all rows), so the
    # per-row pass above never fires. Unit and condition are inherited strictly
    # WITHIN one contiguous block; a block whose rows disagree on VDS (signed —
    # a p-channel 'VDS=-50 V' counts) refuses rather than picking; a row carrying
    # its OWN non-pF unit (nF/uF) never inherits pF; and a block with no pF
    # evidence contributes nothing. When the block states no VDS at all, the
    # PDF-signature voltage (below) may stand in. A missing anchor downstream is
    # a rejected export, never a guessed one.
    pdf_vds: float | None | str = "unresolved"  # lazy: at most one PDF scan per part

    def _fallback_vds() -> float | None:
        nonlocal pdf_vds
        if pdf_vds == "unresolved":
            pdf_vds = _pdf_capacitance_condition_vds(part, datasheet_root)
        return pdf_vds  # type: ignore[return-value]

    missing = [n for n in _CAP_NAMES if n not in anchors]
    if missing:
        for block in _capacitance_blocks(rows):
            block_text = " ".join(
                cell.strip() for _, _, row in block for cell in row if cell.strip())
            block_has_pf = "pF" in block_text
            if not block_has_pf:
                continue
            block_vds = {m.group(1) for m in _VDS_SIGNED_RE.finditer(block_text)}
            if len(block_vds) > 1:
                continue  # ambiguous block: refuse, never pick
            if len(block_vds) == 1:
                vds_v = float(block_vds.pop())
                if vds_v <= 0:
                    continue
            else:
                vds_v = _fallback_vds()
                if vds_v is None:
                    continue
            for _, name, row in block:
                if name in anchors:
                    continue
                symbol_idx = row.index(name)
                tail = row[symbol_idx + 1 :]
                if any(_NON_PF_UNIT_RE.search(cell) for cell in tail):
                    continue  # the row's own unit contradicts the inherited pF
                value_pf = _typ_value_pick(tail)
                if value_pf is not None:
                    anchors[name] = CapAnchor(name=name, value_pf=value_pf, vds_v=vds_v)
    return anchors


def _capacitance_blocks(rows: list[list[str]]):
    """Contiguous runs of rows whose symbol cell is exactly Ciss/Coss/Crss.

    Rows more than 2 lines apart belong to different blocks, so a summary table
    and the characteristics table (or a diagram legend further down) can never
    lend each other units or conditions.
    """
    block: list[tuple[int, str, list[str]]] = []
    for idx, row in enumerate(rows):
        name = next((n for n in _CAP_NAMES if n in row), None)
        if name is not None:
            if block and idx - block[-1][0] > 2:
                yield block
                block = []
            block.append((idx, name, row))
    if block:
        yield block


def _typ_value_pick(tail: list[str]) -> float | None:
    """The typ value from a repair-pass row's value cells.

    Renders: '-, typ, max' and glued 'typ max' put typ first; the ao/nxp
    'min, typ, max' render (three ascending numbers) puts it in the middle —
    first-number there silently served the min. More than three numbers is an
    unrecognized render: refuse rather than guess.
    """
    text = " ".join(tail)
    unit_pos = text.find("pF")
    if unit_pos >= 0:
        text = text[:unit_pos]
    numbers = [float(raw) for raw in
               re.findall(r"(?<![A-Za-z0-9.])[0-9]+(?:\.[0-9]+)?", text)]
    numbers = [n for n in numbers if n > 0]
    if not numbers or len(numbers) > 3:
        return None
    if len(numbers) == 3:
        return numbers[1] if numbers[0] < numbers[1] < numbers[2] else None
    return numbers[0]


def _pdf_capacitance_condition_vds(part: str, datasheet_root: Path) -> float | None:
    """The single capacitance-signature VDS from the PDF text layer, or None.

    Matching runs on whitespace-compacted page text because degraded text layers
    scatter 'VGS=0 V, VDS=50 V, f=1 MHz' across lines with the subscript glyphs
    dropped ('V =0 V,' / '=50 V, =1 MHz'). Values are collected SIGNED and the
    result must be one distinct, strictly positive voltage — a p-channel sheet
    whose second signature is 'VDS=-15 V' refuses instead of anchoring at the
    leftover value. A PDF that cannot be opened refuses (missing anchor) rather
    than raising out of the digitization of an otherwise-good chart.
    """
    pdf_path = _datasheet_file(part, datasheet_root, ".pdf")
    if pdf_path is None:
        return None
    import pymupdf

    values: set[str] = set()
    try:
        with pymupdf.open(pdf_path) as doc:
            for page in doc:
                text = page.get_text().replace("\x03", " ")
                if "apacitance" not in text:
                    continue
                compact = re.sub(r"\s+", "", text)
                for pattern in _PDF_CAP_SIGNATURE_RES:
                    for m in pattern.finditer(compact):
                        values.add(m.group(1))
    except Exception:
        return None
    if len(values) != 1:
        return None
    vds_v = float(values.pop())
    return vds_v if vds_v > 0 else None


def parse_output_charge_reference(part: str, datasheet_root: Path) -> OutputChargeReference:
    if _SUPPLIED_ANCHORS is not None:
        oc = (_SUPPLIED_ANCHORS.get(part) or {}).get("output_charge") or {}
        return OutputChargeReference(
            qoss_pc=oc.get("qoss_pc"), vint_v=oc.get("vint_v"),
            coer_pf=oc.get("coer_pf"), cotr_pf=oc.get("cotr_pf"))

    csv_path = _anchor_csv_path(part, datasheet_root)
    if csv_path is None:
        return OutputChargeReference(qoss_pc=None, vint_v=None, coer_pf=None, cotr_pf=None)

    qoss_candidates: list[tuple[int, float, float | None]] = []
    vint_v: float | None = None
    coer_pf: float | None = None
    cotr_pf: float | None = None
    with csv_path.open(newline="", errors="replace") as f:
        for row in csv.reader(f):
            row_text = " ".join(cell.strip() for cell in row if cell.strip())
            compact = row_text.replace(" ", "")
            row_vint = _extract_reference_vint(row_text)
            if row_vint is not None:
                vint_v = row_vint
            if "Qoss" in row_text and "nC" in row_text:
                value_nc = _first_number_after_symbol_before_unit(row, "Qoss", "nC")
                if value_nc is not None:
                    score = 0
                    if row_vint is not None:
                        score += 10
                    if "Output charge" in row_text:
                        score += 3
                    if "calculation based on Coss" in row_text:
                        score += 1
                    qoss_candidates.append((score, value_nc * 1000.0, row_vint))
            if coer_pf is None and ("Co(er)" in row_text or "Co(er)" in compact) and "pF" in row_text:
                coer_pf = _first_number_after_symbol_before_unit(row, "Co(er)", "pF")
            if cotr_pf is None and ("Co(tr)" in row_text or "Co(tr)" in compact) and "pF" in row_text:
                cotr_pf = _first_number_after_symbol_before_unit(row, "Co(tr)", "pF")
            if qoss_candidates and vint_v is not None and coer_pf is not None and cotr_pf is not None:
                break

    qoss_pc: float | None = None
    if qoss_candidates:
        score, qoss_pc, candidate_vint = max(qoss_candidates, key=lambda item: item[0])
        if candidate_vint is not None:
            vint_v = candidate_vint
    if qoss_pc is not None and vint_v is None:
        # Same extraction-loss disease as the capacitance anchors: the Qoss VALUE row
        # survives in the CSV but its "VDD=x V, VGS=0 V" condition column was lost.
        # Recover the integration voltage from the PDF text with the same refusal
        # semantics (exactly one distinct positive value, else stay None).
        vint_v = _pdf_output_charge_vint(part, datasheet_root)
    return OutputChargeReference(qoss_pc=qoss_pc, vint_v=vint_v, coer_pf=coer_pf, cotr_pf=cotr_pf)


def _extract_reference_vint(row_text: str) -> float | None:
    compact = row_text.replace(" ", "")
    range_match = re.search(r"VDS=0(?:\.{2,3}|\u2026)([0-9]+(?:\.[0-9]+)?)V", compact)
    if range_match:
        return float(range_match.group(1))
    eq_match = re.search(r"V(?:DS|DD)=([0-9]+(?:\.[0-9]+)?)V", compact)
    if eq_match:
        return float(eq_match.group(1))
    at_match = re.search(r"@\s*([0-9]+(?:\.[0-9]+)?)\s*V", row_text)
    if at_match:
        return float(at_match.group(1))
    return None


def _pdf_output_charge_vint(part: str, datasheet_root: Path) -> float | None:
    """The single output-charge integration voltage from the PDF text, or None.

    Same contract as ``_pdf_capacitance_condition_vds``: whitespace-compacted page
    scan, values collected signed, accepted only as one distinct strictly positive
    voltage; unopenable PDFs refuse instead of raising.
    """
    pdf_path = _datasheet_file(part, datasheet_root, ".pdf")
    if pdf_path is None:
        return None
    import pymupdf

    values: set[str] = set()
    try:
        with pymupdf.open(pdf_path) as doc:
            for page in doc:
                text = page.get_text().replace("\x03", " ")
                if "utput" not in text or "harge" not in text:
                    continue
                compact = re.sub(r"\s+", "", text).replace("‑", "-")
                for pattern in _PDF_QOSS_VINT_RES:
                    for m in pattern.finditer(compact):
                        values.add(m.group(1))
    except Exception:
        return None
    if len(values) != 1:
        return None
    vint_v = float(values.pop())
    return vint_v if vint_v > 0 else None


def _anchor_csv_path(part: str, datasheet_root: Path) -> Path | None:
    return _datasheet_file(part, datasheet_root, ".pdf.nop.csv")


def _datasheet_file(part: str, datasheet_root: Path, ext: str) -> Path | None:
    candidates = [part]
    suffix_stripped = re.sub(r"(?:A?KMA|A?KSA|XKSA)[0-9]+$", "", part)
    if suffix_stripped != part:
        candidates.append(suffix_stripped)
    for candidate in candidates:
        path = datasheet_root / f"{candidate}{ext}"
        if path.exists():
            return path
    return None


def _first_number_after_symbol_before_unit(cells: list[str], symbol: str, unit: str) -> float | None:
    text = " ".join(cells)
    symbol_pos = _symbol_position(text, symbol)
    if symbol_pos >= 0:
        text = text[symbol_pos + len(symbol) :]
    text = re.sub(r"@\s*[0-9]+(?:\.[0-9]+)?\s*V", " ", text)
    unit_pos = text.find(unit)
    if unit_pos >= 0:
        text = text[:unit_pos]
    return _first_positive_number(text)


def _symbol_position(text: str, symbol: str) -> int:
    pos = text.find(symbol)
    if pos >= 0:
        return pos
    if symbol == "Co(tr)":
        return text.replace(" ", "").find(symbol)
    return -1


def _first_number_before_unit(cells: list[str], unit: str) -> float | None:
    text = " ".join(cells)
    unit_pos = text.find(unit)
    if unit_pos >= 0:
        text = text[:unit_pos]
    return _first_positive_number(text)


def _first_positive_number(text: str) -> float | None:
    numbers = re.findall(r"(?<![A-Za-z])[-+]?[0-9]+(?:\.[0-9]+)?", text)
    for raw in numbers:
        value = float(raw)
        if value > 0:
            return value
    return None


def output_charge_reference_to_json(ref: OutputChargeReference) -> dict[str, float | None]:
    return {
        "qoss_pc": ref.qoss_pc,
        "vint_v": ref.vint_v,
        "coer_pf": ref.coer_pf,
        "cotr_pf": ref.cotr_pf,
    }
