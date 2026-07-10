"""Datasheet table reference parsing for MOSFET capacitance charts."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from .capacitance_types import CapAnchor, OutputChargeReference

def parse_capacitance_anchors(part: str, datasheet_root: Path) -> dict[str, CapAnchor]:
    csv_path = _anchor_csv_path(part, datasheet_root)
    if csv_path is None:
        return {}

    anchors: dict[str, CapAnchor] = {}
    with csv_path.open(newline="", errors="replace") as f:
        for row in csv.reader(f):
            row_text = " ".join(cell.strip() for cell in row if cell.strip())
            for name in ("Ciss", "Coss", "Crss"):
                if name not in row:
                    continue
                try:
                    symbol_idx = row.index(name)
                except ValueError:
                    continue
                tail = row[symbol_idx + 1 :]
                value_pf = _first_number_before_unit(tail, "pF")
                vds_match = re.search(r"VDS\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*V", row_text)
                if value_pf is not None and vds_match:
                    anchors[name] = CapAnchor(
                        name=name,
                        value_pf=value_pf,
                        vds_v=float(vds_match.group(1)),
                    )
    return anchors


def parse_output_charge_reference(part: str, datasheet_root: Path) -> OutputChargeReference:
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


def _anchor_csv_path(part: str, datasheet_root: Path) -> Path | None:
    candidates = [part]
    suffix_stripped = re.sub(r"(?:A?KMA|A?KSA|XKSA)[0-9]+$", "", part)
    if suffix_stripped != part:
        candidates.append(suffix_stripped)
    for candidate in candidates:
        path = datasheet_root / f"{candidate}.pdf.nop.csv"
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

