#!/usr/bin/env python3
"""Find chart panels in datasheet PDFs.

This pass deliberately stops at "find all charts and emit crops + metadata";
chart-specific trace digitizers build on top of this index.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import traceback
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import pymupdf
from PIL import Image

try:
    from .crop_transform import CROP_MARGIN_PT
except ImportError:  # pragma: no cover - direct script compatibility
    from crop_transform import CROP_MARGIN_PT


DIAGRAM_RE = re.compile(r"^Diagram\s+(\d+):?\s*(.*)$", re.IGNORECASE)
FIGURE_RE = re.compile(r"^(?:Figure|Fig\.?)\s+(\d+(?:\.\d+)?)[\.:]?\s*(.*)$", re.IGNORECASE)
COMPACT_FIGURE_RE = re.compile(r"^(?:Figure|Fig\.?)(\d+(?:\.\d+)?)[\.:\\-]?\s*(.*)$", re.IGNORECASE)
CAPACITANCE_WORDS = {"ciss", "coss", "crss", "capacitance", "capacitances"}


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class PageText:
    page_num: int
    width_pt: float
    height_pt: float
    words: list[Word]
    text_source: str = "pdftotext"


@dataclass(frozen=True)
class DiagramTitle:
    number: int
    title: str
    bbox_pt: tuple[float, float, float, float]
    line_text: str


@dataclass
class ChartPanel:
    pdf: str
    part: str
    page: int
    diagram: int
    title: str
    kind: str
    bbox_pt: tuple[float, float, float, float]
    crop_box_pt: tuple[float, float, float, float]
    crop_png: str
    text: str
    formula: str
    mentions: list[str]
    text_source: str = "pdftotext"


def run_text_bbox(pdf: Path) -> list[PageText]:
    proc = subprocess.run(
        ["pdftotext", "-bbox-layout", str(pdf), "-"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    xml_text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", proc.stdout)
    root = ET.fromstring(xml_text)
    primary_pages: list[PageText] = []
    for page_idx, page_el in enumerate(root.iterfind(".//{*}page"), start=1):
        words: list[Word] = []
        for word_el in page_el.iterfind(".//{*}word"):
            text = "".join(word_el.itertext()).strip()
            if not text:
                continue
            words.append(
                Word(
                    text=text,
                    x0=float(word_el.attrib["xMin"]),
                    y0=float(word_el.attrib["yMin"]),
                    x1=float(word_el.attrib["xMax"]),
                    y1=float(word_el.attrib["yMax"]),
                )
            )
        primary_pages.append(
            PageText(
                page_num=page_idx,
                width_pt=float(page_el.attrib["width"]),
                height_pt=float(page_el.attrib["height"]),
                words=words,
            )
        )
    if not any(_page_text_looks_corrupt(page) for page in primary_pages):
        return primary_pages
    try:
        fallback_pages = _run_pymupdf_text(pdf)
    except (RuntimeError, ValueError):
        return primary_pages
    fallback_by_number = {page.page_num: page for page in fallback_pages}
    return [
        _select_page_text(page, fallback_by_number.get(page.page_num))
        for page in primary_pages
    ]


def _run_pymupdf_text(pdf: Path) -> list[PageText]:
    pages: list[PageText] = []
    with pymupdf.open(pdf) as doc:
        for page_idx, page in enumerate(doc, start=1):
            words = [
                Word(
                    text=str(raw[4]),
                    x0=float(raw[0]),
                    y0=float(raw[1]),
                    x1=float(raw[2]),
                    y1=float(raw[3]),
                )
                for raw in page.get_text("words")
                if str(raw[4]).strip()
            ]
            pages.append(
                PageText(
                    page_num=page_idx,
                    width_pt=float(page.rect.width),
                    height_pt=float(page.rect.height),
                    words=_dedupe_overprinted_words(words),
                    text_source="pymupdf_fallback",
                )
            )
    return pages


def _dedupe_overprinted_words(words: list[Word]) -> list[Word]:
    """Collapse near-identical glyph layers emitted as repeated words."""
    buckets: dict[tuple[str, int, int], list[Word]] = {}
    out: list[Word] = []
    for word in words:
        cx = 0.5 * (word.x0 + word.x1)
        cy = 0.5 * (word.y0 + word.y1)
        gx = int(cx)
        gy = int(cy)
        duplicate = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in buckets.get((word.text, gx + dx, gy + dy), []):
                    if max(
                        abs(word.x0 - other.x0),
                        abs(word.y0 - other.y0),
                        abs(word.x1 - other.x1),
                        abs(word.y1 - other.y1),
                    ) <= 0.8:
                        duplicate = True
                        break
                if duplicate:
                    break
            if duplicate:
                break
        if duplicate:
            continue
        out.append(word)
        buckets.setdefault((word.text, gx, gy), []).append(word)
    return out


def _readable_word_count(page: PageText) -> int:
    return sum(bool(re.search(r"[A-Za-z]{2}", word.text)) for word in page.words)


def _page_text_looks_corrupt(page: PageText) -> bool:
    return len(page.words) >= 20 and _readable_word_count(page) / len(page.words) < 0.12


def _select_page_text(primary: PageText, fallback: PageText | None) -> PageText:
    """Use PyMuPDF only when it clearly repairs a corrupted text layer."""
    if fallback is None or not fallback.words:
        return primary
    primary_readable = _readable_word_count(primary)
    fallback_readable = _readable_word_count(fallback)
    fallback_fraction = fallback_readable / len(fallback.words)
    if (
        fallback_readable >= 8
        and fallback_readable >= 2 * max(1, primary_readable)
        and fallback_fraction >= 0.18
    ):
        return fallback
    return primary


def group_words_into_lines(words: Iterable[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.y0, w.x0)):
        center_y = (word.y0 + word.y1) / 2
        for line in reversed(lines[-8:]):
            line_center = sum((w.y0 + w.y1) / 2 for w in line) / len(line)
            if abs(center_y - line_center) <= 3.5:
                line.append(word)
                break
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda w: w.x0)
    lines.sort(key=lambda line: (min(w.y0 for w in line), min(w.x0 for w in line)))
    return lines


def line_text(line: list[Word]) -> str:
    return " ".join(w.text for w in line)


def line_bbox(line: list[Word]) -> tuple[float, float, float, float]:
    return (
        min(w.x0 for w in line),
        min(w.y0 for w in line),
        max(w.x1 for w in line),
        max(w.y1 for w in line),
    )


def find_diagram_titles(page: PageText) -> list[DiagramTitle]:
    titles: list[DiagramTitle] = []
    lines = group_words_into_lines(page.words)
    for line in lines:
        word_level_titles: list[DiagramTitle] = []
        for word in line:
            text = word.text.strip()
            match = DIAGRAM_RE.match(text)
            if not match:
                continue
            word_level_titles.append(
                DiagramTitle(
                    number=int(match.group(1)),
                    title=match.group(2).strip(),
                    bbox_pt=(word.x0, word.y0, word.x1, word.y1),
                    line_text=text,
                )
            )
        if word_level_titles:
            titles.extend(word_level_titles)
            continue

        starts = [idx for idx, word in enumerate(line) if word.text.lower() == "diagram"]
        if not starts:
            continue
        starts.append(len(line))
        for start, end in zip(starts, starts[1:]):
            segment = line[start:end]
            text = line_text(segment)
            match = DIAGRAM_RE.match(text)
            if not match:
                continue
            titles.append(
                DiagramTitle(
                    number=int(match.group(1)),
                    title=match.group(2).strip(),
                    bbox_pt=line_bbox(segment),
                    line_text=text,
                )
            )
    titles.sort(key=lambda t: (t.bbox_pt[1], t.bbox_pt[0], t.number))
    return titles


def _caption_starts(line: list[Word]) -> list[int]:
    starts: list[int] = []
    for idx, word in enumerate(line):
        token = word.text.strip()
        lower = token.lower().rstrip(".:")
        compact_match = COMPACT_FIGURE_RE.match(token)
        if compact_match:
            tail = compact_match.group(2).lower() + " " + " ".join(w.text for w in line[idx + 1 : idx + 5]).lower()
            if any(
                phrase in tail
                for phrase in (
                    "typ",
                    "gate",
                    "charge",
                    "capacitance",
                    "avalanche",
                    "breakdown",
                    "waveforms",
                    "dynamic",
                )
            ):
                starts.append(idx)
            continue
        if lower in {"figure", "fig"} and idx + 1 < len(line):
            if re.match(r"^\d+(?:\.\d+)?[\.:]?$", line[idx + 1].text):
                starts.append(idx)
            continue

        if lower in {"typ", "typical", "typicaly", "typycal"}:
            if idx > 0 and re.match(r"^\d+(?:\.\d+)?[\.:]?$", line[idx - 1].text.strip()):
                continue
            tail = " ".join(w.text for w in line[idx : idx + 5]).lower()
            if any(phrase in tail for phrase in ("gate", "capacitance", "breakdown")):
                starts.append(idx)
            continue

        if lower.startswith("drain-source"):
            if idx > 0 and re.match(r"^\d+(?:\.\d+)?[\.:]?$", line[idx - 1].text.strip()):
                # "15 Drain-source breakdown voltage": the number rule already
                # starts this caption; a second start here would split it.
                continue
            tail = " ".join(w.text for w in line[idx : idx + 5]).lower()
            if "breakdown" in tail:
                starts.append(idx)
            continue

        if lower == "gate" and idx + 1 < len(line):
            tail = " ".join(w.text for w in line[idx : idx + 5]).lower()
            prev = _token_norm(line[idx - 1].text) if idx > 0 else ""
            prev_prev = _token_norm(line[idx - 2].text) if idx > 1 else ""
            follows_figure_number = bool(re.match(r"^\d+$", prev) and prev_prev in {"fig", "figure"})
            if "charge" in tail and "characteristic" in tail and not follows_figure_number:
                starts.append(idx)
            continue

        if not token.isdigit() or idx + 1 >= len(line):
            continue
        tail = " ".join(w.text for w in line[idx + 1 : idx + 5]).lower()
        if any(phrase in tail for phrase in ("typ", "gate", "avalanche", "breakdown", "waveforms")):
            starts.append(idx)
    return starts


def _parse_caption_text(text: str) -> tuple[int | None, str] | None:
    match = FIGURE_RE.match(text)
    if match:
        return int(match.group(1).replace(".", "")), match.group(2).strip()
    match = COMPACT_FIGURE_RE.match(text)
    if match:
        title = match.group(2).strip()
        if title.startswith("-"):
            title = title[1:].strip()
        return int(match.group(1).replace(".", "")), title
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].isdigit():
        return int(parts[0]), parts[1].strip()
    if re.match(r"(?i)^gate\s+charge\b", text):
        return None, text.strip()
    if re.match(r"(?i)^Typ(?:ical|ycal)?\.?\s+", text):
        return None, text.strip()
    return None


def _caption_continuation(lines: list[list[Word]], line_idx: int, segment: list[Word]) -> str:
    """Return a short wrapped caption continuation below a title segment."""
    if line_idx + 1 >= len(lines):
        return ""
    sx0, sy0, sx1, sy1 = line_bbox(segment)
    segment_width = sx1 - sx0
    continuation: list[str] = []
    for next_line in lines[line_idx + 1 : line_idx + 3]:
        nx0, ny0, nx1, _ = line_bbox(next_line)
        if ny0 - sy1 > 18:
            break
        overlap = _overlap_1d(sx0, sx1, nx0, nx1)
        if overlap < max(8.0, min(segment_width, nx1 - nx0) * 0.25):
            continue
        text = line_text(next_line)
        if not re.search(r"[A-Za-z]", text):
            continue
        continuation.append(text)
    return " ".join(continuation).strip()


def find_caption_titles(page: PageText) -> list[DiagramTitle]:
    """Find non-Diagram chart captions used by many gate-charge plots."""
    titles: list[DiagramTitle] = []
    lines = group_words_into_lines(page.words)
    for line_idx, line in enumerate(lines):
        starts = _caption_starts(line)
        if not starts:
            continue
        starts.append(len(line))
        for start, end in zip(starts, starts[1:]):
            segment = line[start:end]
            text = line_text(segment)
            parsed = _parse_caption_text(text)
            if parsed is None:
                continue
            parsed_number, title = parsed
            number = parsed_number if parsed_number is not None else 900 + len(titles) + 1
            title_for_classification = title
            if classify_chart(title_for_classification, "") != "gate_charge" and "gate" in title_for_classification.lower():
                continuation = _caption_continuation(lines, line_idx, segment)
                if continuation:
                    title_for_classification = f"{title_for_classification} {continuation}".strip()
            # Caption-style pages (older Infineon layouts caption charts as
            # "15 Drain-source breakdown voltage" instead of "Diagram 15:").
            # The caption pipeline was originally gate-charge only; breakdown
            # captions are let through so those parts get a BV(Tj) panel too.
            if classify_chart(title_for_classification, "") not in {"gate_charge", "breakdown_voltage"}:
                continue
            title = title_for_classification
            titles.append(
                DiagramTitle(
                    number=number,
                    title=title,
                    bbox_pt=line_bbox(segment),
                    line_text=text,
                )
            )
    titles.sort(key=lambda t: (t.bbox_pt[1], t.bbox_pt[0], t.number))
    return titles


def extend_wrapped_titles(page: PageText, titles: list[DiagramTitle]) -> list[DiagramTitle]:
    """Add short continuation text from the title band when a title wraps."""
    if not titles:
        return titles
    lines = group_words_into_lines(page.words)
    out: list[DiagramTitle] = []
    for title in titles:
        tx0, ty0, tx1, ty1 = title.bbox_pt
        continuation: list[str] = []
        for line in lines:
            if any(word.text.lower() == "diagram" for word in line):
                continue
            lx0, ly0, lx1, ly1 = line_bbox(line)
            line_cx = (lx0 + lx1) / 2
            if ly0 <= ty1 or ly0 > ty1 + 22:
                continue
            if tx0 - 10 <= line_cx <= tx1 + 80:
                text = line_text(line)
                # Reject tick labels and large formula captions.
                if not re.search(r"[A-Za-z]", text):
                    continue
                if "=" in text or "[" in text:
                    continue
                continuation.append(text)
        if continuation:
            title = DiagramTitle(
                number=title.number,
                title=f"{title.title} {' '.join(continuation)}".strip(),
                bbox_pt=title.bbox_pt,
                line_text=f"{title.line_text} {' '.join(continuation)}".strip(),
            )
        out.append(title)
    return out


def render_page(pdf: Path, page_num: int, dpi: int, tmpdir: Path) -> Path:
    prefix = tmpdir / f"page_{page_num}"
    subprocess.run(
        [
            "pdftoppm",
            "-r",
            str(dpi),
            "-png",
            "-f",
            str(page_num),
            "-l",
            str(page_num),
            str(pdf),
            str(prefix),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    matches = sorted(tmpdir.glob(f"page_{page_num}-*.png"))
    if not matches:
        raise RuntimeError(f"pdftoppm produced no page image for page {page_num}")
    return matches[-1]


def _merge_rule_boxes(
    boxes: Iterable[tuple[int, int, int, int]], axis: str, tolerance: int = 5
) -> list[tuple[int, int, int, int]]:
    keyed = []
    for x, y, w, h in boxes:
        center = x + w // 2 if axis == "x" else y + h // 2
        keyed.append((center, (x, y, w, h)))
    keyed.sort(key=lambda item: item[0])

    groups: list[list[tuple[int, int, int, int]]] = []
    centers: list[list[int]] = []
    for center, box in keyed:
        if groups and abs(center - centers[-1][-1]) <= tolerance:
            groups[-1].append(box)
            centers[-1].append(center)
        else:
            groups.append([box])
            centers.append([center])

    merged: list[tuple[int, int, int, int]] = []
    for group in groups:
        if axis == "x":
            group.sort(key=lambda b: b[1])
            chunks: list[list[tuple[int, int, int, int]]] = []
            for box in group:
                if chunks:
                    prev_y1 = max(y + h for _, y, _, h in chunks[-1])
                    if box[1] - prev_y1 <= 8:
                        chunks[-1].append(box)
                        continue
                chunks.append([box])
        else:
            group.sort(key=lambda b: b[0])
            chunks = []
            for box in group:
                if chunks:
                    prev_x1 = max(x + w for x, _, w, _ in chunks[-1])
                    if box[0] - prev_x1 <= 8:
                        chunks[-1].append(box)
                        continue
                chunks.append([box])

        for chunk in chunks:
            x0 = min(x for x, _, _, _ in chunk)
            y0 = min(y for _, y, _, _ in chunk)
            x1 = max(x + w for x, _, w, _ in chunk)
            y1 = max(y + h for _, y, _, h in chunk)
            merged.append((x0, y0, x1 - x0, y1 - y0))
    return merged


def detect_rule_boxes(page_png: Path) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, int, int, int]]]:
    img = cv2.imread(str(page_png), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RuntimeError(f"could not read {page_png}")
    _, bw = cv2.threshold(img, 210, 255, cv2.THRESH_BINARY_INV)
    height, width = bw.shape

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(80, width // 6), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(80, height // 8)))
    h_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, v_kernel)

    h_contours, _ = cv2.findContours(h_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    v_contours, _ = cv2.findContours(v_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    h_boxes: list[tuple[int, int, int, int]] = []
    for c in h_contours:
        x, y, w, h = cv2.boundingRect(c)
        if w >= width * 0.25 and h <= 8:
            h_boxes.append((x, y, w, h))

    v_boxes: list[tuple[int, int, int, int]] = []
    for c in v_contours:
        x, y, w, h = cv2.boundingRect(c)
        if h >= height * 0.20 and w <= 8:
            v_boxes.append((x, y, w, h))

    return _merge_rule_boxes(v_boxes, "x"), _merge_rule_boxes(h_boxes, "y")


def px_to_pt_x(x_px: int, image_width: int, page: PageText) -> float:
    return x_px * page.width_pt / image_width


def px_to_pt_y(y_px: int, image_height: int, page: PageText) -> float:
    return y_px * page.height_pt / image_height


def box_px_to_pt(
    box: tuple[int, int, int, int], image_width: int, image_height: int, page: PageText
) -> tuple[float, float, float, float]:
    x, y, w, h = box
    return (
        px_to_pt_x(x, image_width, page),
        px_to_pt_y(y, image_height, page),
        px_to_pt_x(x + w, image_width, page),
        px_to_pt_y(y + h, image_height, page),
    )


def choose_panel_bbox(
    page: PageText,
    title: DiagramTitle,
    all_titles: list[DiagramTitle],
    v_rules_pt: list[tuple[float, float, float, float]],
    h_rules_pt: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    tx0, ty0, tx1, _ = title.bbox_pt
    title_x = (tx0 + tx1) / 2

    title_band_lines = []
    for x0, y0, x1, y1 in v_rules_pt:
        cx = (x0 + x1) / 2
        if y0 - 2 <= ty0 <= y1 + 2:
            title_band_lines.append(cx)
    title_band_lines = sorted(set(round(v, 2) for v in title_band_lines))

    x_left: float | None = None
    x_right: float | None = None
    for a, b in zip(title_band_lines, title_band_lines[1:]):
        if a - 2 <= title_x <= b + 2 and (b - a) >= page.width_pt * 0.25:
            x_left, x_right = a, b
            break
    if x_left is None:
        return None

    later_titles = [t.bbox_pt[1] for t in all_titles if t.bbox_pt[1] > ty0 + 25]
    next_row_y = min(later_titles) if later_titles else page.height_pt - 55

    panel_width = x_right - x_left
    hline_centers: list[float] = []
    for hx0, hy0, hx1, hy1 in h_rules_pt:
        overlap = min(hx1, x_right) - max(hx0, x_left)
        if overlap < panel_width * 0.75:
            continue
        hline_centers.append((hy0 + hy1) / 2)

    above_or_near = [y for y in hline_centers if y <= ty0 + 8]
    below_row = [y for y in hline_centers if ty0 + 120 <= y < next_row_y - 6]
    if not above_or_near or not below_row:
        return None
    y_top = max(above_or_near)
    y_bottom = max(below_row)

    if y_bottom - y_top < 120:
        return None
    return (x_left, y_top, x_right, y_bottom)


def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def infer_grid_regions_from_h_rules(
    page: PageText,
    h_rules_pt: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Group horizontal plot/grid rules into candidate chart rectangles."""
    candidates: list[tuple[float, float, float, float]] = []
    min_width = page.width_pt * 0.18
    max_width = page.width_pt * 0.92
    for x0, y0, x1, y1 in h_rules_pt:
        width = x1 - x0
        if min_width <= width <= max_width:
            candidates.append((x0, y0, x1, y1))
    candidates.sort(key=lambda b: (((b[0] + b[2]) / 2), b[1]))

    groups: list[list[tuple[float, float, float, float]]] = []
    for box in candidates:
        bx0, _, bx1, _ = box
        bc = (bx0 + bx1) / 2
        bw = bx1 - bx0
        for group in groups:
            gx0 = min(g[0] for g in group)
            gx1 = max(g[2] for g in group)
            gc = (gx0 + gx1) / 2
            gw = gx1 - gx0
            overlap = _overlap_1d(bx0, bx1, gx0, gx1)
            if abs(bc - gc) <= page.width_pt * 0.08 and overlap >= min(bw, gw) * 0.55:
                group.append(box)
                break
        else:
            groups.append([box])

    regions: list[tuple[float, float, float, float]] = []
    for group in groups:
        group.sort(key=lambda g: (g[1] + g[3]) / 2)
        chunks: list[list[tuple[float, float, float, float]]] = []
        for box in group:
            center_y = (box[1] + box[3]) / 2
            if chunks:
                prev_center_y = (chunks[-1][-1][1] + chunks[-1][-1][3]) / 2
                if center_y - prev_center_y <= 28:
                    chunks[-1].append(box)
                    continue
            chunks.append([box])

        for chunk in chunks:
            if len(chunk) < 4:
                continue
            x0 = min(g[0] for g in chunk)
            y0 = min((g[1] + g[3]) / 2 for g in chunk)
            x1 = max(g[2] for g in chunk)
            y1 = max((g[1] + g[3]) / 2 for g in chunk)
            if y1 - y0 < 60:
                continue
            regions.append((x0, y0, x1, y1))
    regions.sort(key=lambda b: (b[1], b[0]))
    return regions


def _caption_prefers_plot_above(title: DiagramTitle) -> bool:
    text = title.line_text.strip()
    return bool(FIGURE_RE.match(text) or COMPACT_FIGURE_RE.match(text) or re.match(r"^\d+[\.:]?\s+", text))


def _has_gate_charge_axis_label_above_caption(page: PageText, title: DiagramTitle) -> bool:
    tx0, ty0, tx1, _ = title.bbox_pt
    for line in group_words_into_lines(page.words):
        text = line_text(line)
        if not _is_gate_charge_axis_label(text):
            continue
        lx0, ly0, lx1, ly1 = line_bbox(line)
        if not (ty0 - 70.0 <= ly1 <= ty0 - 4.0):
            continue
        if _overlap_1d(tx0, tx1, lx0, lx1) >= min(tx1 - tx0, lx1 - lx0) * 0.20:
            return True
    return False


def _has_gate_charge_formula_below_caption(page: PageText, title: DiagramTitle) -> bool:
    tx0, ty0, tx1, ty1 = title.bbox_pt
    for line in group_words_into_lines(page.words):
        lx0, ly0, lx1, ly1 = line_bbox(line)
        if not (ty1 <= ly0 <= ty1 + 38.0):
            continue
        if _overlap_1d(tx0, tx1, lx0, lx1) < max(8.0, min(tx1 - tx0, lx1 - lx0) * 0.20):
            continue
        normalized = _token_norm(line_text(line))
        if "fqg" in normalized or "fqgate" in normalized or "vgsfq" in normalized or "vgefq" in normalized:
            return True
    return False


def choose_caption_panel_bbox(
    page: PageText,
    title: DiagramTitle,
    grid_regions: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    tx0, ty0, tx1, ty1 = title.bbox_pt
    tcx = (tx0 + tx1) / 2
    best: tuple[float, tuple[float, float, float, float]] | None = None
    candidates = []
    has_formula_below = _has_gate_charge_formula_below_caption(page, title)
    max_vertical_gap = 220.0 if has_formula_below else 85.0
    for region in grid_regions:
        x0, y0, x1, y1 = region
        rcx = (x0 + x1) / 2
        width = x1 - x0
        horizontal_penalty = abs(tcx - rcx)
        if horizontal_penalty > max(width * 0.85, page.width_pt * 0.10):
            continue
        if ty1 < y0:
            vertical_gap = y0 - ty1
        elif ty0 > y1:
            vertical_gap = ty0 - y1
        else:
            vertical_gap = 0.0
        if vertical_gap > max_vertical_gap:
            continue
        candidates.append((horizontal_penalty, vertical_gap, region))

    plot_width_candidates = [item for item in candidates if item[2][2] - item[2][0] <= page.width_pt * 0.62]
    if plot_width_candidates:
        candidates = plot_width_candidates

    if _caption_prefers_plot_above(title) and _has_gate_charge_axis_label_above_caption(page, title):
        above = [item for item in candidates if item[2][3] <= ty0]
        if above:
            candidates = above
    elif has_formula_below:
        below = [item for item in candidates if item[2][1] >= ty1]
        if below:
            candidates = below

    for horizontal_penalty, vertical_gap, region in candidates:
        score = vertical_gap + 0.25 * horizontal_penalty
        if best is None or score < best[0]:
            best = (score, region)
    if best is None:
        return None

    x0, y0, x1, y1 = best[1]
    pad_x = min(12.0, (x1 - x0) * 0.04)
    pad_y = min(12.0, (y1 - y0) * 0.06)
    return (
        max(0.0, x0 - pad_x),
        max(0.0, y0 - pad_y),
        min(page.width_pt, x1 + pad_x),
        min(page.height_pt, y1 + pad_y),
    )


def _is_gate_charge_axis_label(text: str) -> bool:
    normalized = text.lower().replace("‑", "-").replace("–", "-").replace("_", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    has_qg = bool(re.search(r"\bq\s*g\b|\bqg\b|\bqgate\b", normalized))
    has_charge_unit = "charge" in normalized or "nc" in normalized
    return has_qg and has_charge_unit


def _token_norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower().replace("‑", "-").replace("–", "-"))


def _is_qg_token_pair(line: list[Word], idx: int) -> bool:
    token = _token_norm(line[idx].text)
    if token in {"qg", "qgate", "qgtot", "qtotal"}:
        return True
    if token == "q" and idx + 1 < len(line):
        return _token_norm(line[idx + 1].text) in {"g", "gate", "gtot", "total"}
    return False


def gate_charge_axis_label_spans(page: PageText) -> list[tuple[float, float, float, float]]:
    """Return local Qg/Gate-Charge axis-label spans.

    Many datasheets put two charts on the same row and pdftotext emits both
    x-axis labels as one line.  The broad line bbox is not precise enough to
    decide which plot is the gate-charge plot, so this keeps only the local
    token span around ``QG ... (nC)`` / ``QG ... Gate Charge``.
    """
    spans: list[tuple[float, float, float, float]] = []
    for line in group_words_into_lines(page.words):
        for idx, word in enumerate(line):
            if not _is_qg_token_pair(line, idx):
                continue
            selected = [word]
            saw_charge_or_unit = False
            for next_word in line[idx + 1 : idx + 9]:
                norm = _token_norm(next_word.text)
                if selected and next_word.x0 - selected[-1].x1 > 58.0:
                    break
                if norm in {"vds", "vgs", "vdd", "tj", "tc", "id"} and saw_charge_or_unit:
                    break
                selected.append(next_word)
                if "charge" in norm or norm in {"nc", "nanocoulomb", "nanocoulombs"}:
                    saw_charge_or_unit = True
                    if norm in {"nc", "nanocoulomb", "nanocoulombs"}:
                        break
            text = " ".join(w.text for w in selected)
            if not _is_gate_charge_axis_label(text):
                continue
            spans.append(line_bbox(selected))
    return spans


def choose_caption_axis_label_bbox(
    page: PageText,
    title: DiagramTitle,
) -> tuple[float, float, float, float] | None:
    """Synthesize a caption panel from the nearby Qg/Gate-Charge axis label.

    This is a fallback for small or light raster/vector plot frames whose grid
    rules are not recovered by morphology.  It intentionally requires an axis
    label near the caption so generic "gate charge" table text is not enough.
    """
    tx0, ty0, tx1, ty1 = title.bbox_pt
    tcx = 0.5 * (tx0 + tx1)
    tcy = 0.5 * (ty0 + ty1)
    best: tuple[float, tuple[float, float, float, float]] | None = None
    for line in group_words_into_lines(page.words):
        text = line_text(line)
        if not _is_gate_charge_axis_label(text):
            continue
        lx0, ly0, lx1, ly1 = line_bbox(line)
        lcx = 0.5 * (lx0 + lx1)
        lcy = 0.5 * (ly0 + ly1)
        vertical_gap = abs(lcy - tcy)
        if vertical_gap > 280:
            continue
        horizontal_penalty = abs(lcx - tcx)
        # Axis-label lines often span two side-by-side charts.  Do not reject
        # those only because their center is pulled toward the neighboring plot.
        if horizontal_penalty > page.width_pt * 0.45:
            continue
        score = vertical_gap + 0.15 * horizontal_penalty
        if best is None or score < best[0]:
            best = (score, (lx0, ly0, lx1, ly1))
    if best is None:
        return None

    _, (lx0, ly0, lx1, ly1) = best
    line_cy = 0.5 * (ly0 + ly1)
    half_width = min(max(145.0, (tx1 - tx0) * 0.95), page.width_pt * 0.32)
    x0 = max(0.0, tcx - half_width)
    x1 = min(page.width_pt, tcx + half_width)
    if line_cy < tcy:
        # Caption sits below the plot; the x-axis label is just above it.
        y0 = max(0.0, ly0 - 185.0)
        y1 = min(page.height_pt, ty0 - 2.0)
    else:
        # Header sits above the plot; the x-axis label is below it.
        y0 = max(0.0, ty1 + 2.0)
        y1 = min(page.height_pt, ly1 + 18.0)
    if y1 - y0 < 90:
        return None
    return (x0, y0, x1, y1)


def choose_axis_label_grid_bbox(
    page: PageText,
    axis_label_bbox: tuple[float, float, float, float],
    grid_regions: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    """Bind a local Qg axis label to the plot grid directly above it."""
    lx0, ly0, lx1, ly1 = axis_label_bbox
    lcx = 0.5 * (lx0 + lx1)
    best: tuple[float, tuple[float, float, float, float]] | None = None
    for region in grid_regions:
        x0, y0, x1, y1 = region
        width = x1 - x0
        if not (x0 - 18.0 <= lcx <= x1 + 18.0):
            continue
        if ly0 >= y1:
            vertical_gap = ly0 - y1
        elif y0 <= ly0 <= y1:
            vertical_gap = 0.0
        else:
            continue
        if vertical_gap > 45.0:
            continue
        horizontal_penalty = abs(lcx - 0.5 * (x0 + x1))
        score = vertical_gap + 0.10 * horizontal_penalty
        if best is None or score < best[0]:
            best = (score, region)
    if best is None:
        return None
    x0, y0, x1, y1 = best[1]
    pad_x = min(12.0, (x1 - x0) * 0.04)
    pad_y = min(12.0, (y1 - y0) * 0.06)
    return (
        max(0.0, x0 - pad_x),
        max(0.0, y0 - pad_y),
        min(page.width_pt, x1 + pad_x),
        min(page.height_pt, y1 + pad_y),
    )


def choose_axis_label_synthetic_bbox(
    page: PageText,
    axis_label_bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    """Fallback panel around a Qg axis label when plot rules are too light."""
    lx0, ly0, lx1, ly1 = axis_label_bbox
    lcx = 0.5 * (lx0 + lx1)
    label_width = lx1 - lx0
    half_width = min(max(155.0, label_width * 1.25), page.width_pt * 0.38)
    x0 = max(0.0, lcx - half_width)
    x1 = min(page.width_pt, lcx + half_width)
    y0 = max(0.0, ly0 - 190.0)
    y1 = min(page.height_pt, ly1 + 22.0)
    if y1 - y0 < 105.0 or x1 - x0 < 130.0:
        return None
    return (x0, y0, x1, y1)


def choose_caption_synthetic_bbox(page: PageText, title: DiagramTitle) -> tuple[float, float, float, float] | None:
    """Fallback from a gate-charge caption/header when plot rules are absent."""
    tx0, ty0, tx1, ty1 = title.bbox_pt
    tcx = 0.5 * (tx0 + tx1)
    title_width = tx1 - tx0
    half_width = min(max(170.0, title_width * 0.80), page.width_pt * 0.40)
    x0 = max(0.0, tcx - half_width)
    x1 = min(page.width_pt, tcx + half_width)
    if _caption_prefers_plot_above(title) and ty0 > page.height_pt * 0.40:
        y0 = max(0.0, ty0 - 215.0)
        y1 = max(0.0, ty0 - 4.0)
    elif ty0 < page.height_pt * 0.35:
        y0 = min(page.height_pt, ty1 + 4.0)
        y1 = min(page.height_pt, y0 + 215.0)
    else:
        y0 = max(0.0, ty1 + 4.0)
        y1 = min(page.height_pt, y0 + 235.0)
    if y1 - y0 < 105.0:
        return None
    return (x0, y0, x1, y1)


def words_in_bbox(words: list[Word], bbox: tuple[float, float, float, float]) -> list[Word]:
    x0, y0, x1, y1 = bbox
    selected = []
    for word in words:
        cx = (word.x0 + word.x1) / 2
        cy = (word.y0 + word.y1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            selected.append(word)
    return selected


def _bbox_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter = _overlap_1d(ax0, ax1, bx0, bx1) * _overlap_1d(ay0, ay1, by0, by1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    return inter / max(1e-9, area_a + area_b - inter)


def _append_panel(
    panels: list[ChartPanel],
    pdf: Path,
    page: PageText,
    page_png: Path,
    out_dir: Path,
    lines: list[list[Word]],
    title: DiagramTitle,
    bbox: tuple[float, float, float, float],
) -> None:
    text_words = words_in_bbox(page.words, bbox)
    text = " ".join(w.text for w in sorted(text_words, key=lambda w: (w.y0, w.x0)))
    mentions = sorted(
        {
            w.text
            for w in text_words
            if w.text.lower().replace("‑", "-").strip(" ,;:()[]") in CAPACITANCE_WORDS
            or w.text in {"Ciss", "Coss", "Crss"}
        }
    )
    kind = classify_chart(title.title, text)
    rel_crop = Path(pdf.stem) / f"p{page.page_num:02d}_diagram_{title.number:02d}.png"
    crop_box = crop_panel(page_png, page, bbox, out_dir / "crops" / rel_crop)
    panels.append(
        ChartPanel(
            pdf=str(pdf.resolve()),
            part=pdf.name.split(".pdf")[0],
            page=page.page_num,
            diagram=title.number,
            title=title.title,
            kind=kind,
            bbox_pt=tuple(round(v, 3) for v in bbox),
            crop_box_pt=tuple(round(v, 3) for v in crop_box),
            crop_png=str(Path("crops") / rel_crop),
            text=text,
            formula=formula_from_text(lines, bbox),
            mentions=mentions,
            text_source=page.text_source,
        )
    )


def classify_chart(title: str, text: str) -> str:
    haystack = f"{title} {text}".lower().replace("‑", "-").replace("–", "-")
    haystack = re.sub(r"[-_/]+", " ", haystack)
    haystack = re.sub(r"\s+", " ", haystack)
    if any(word in haystack for word in CAPACITANCE_WORDS):
        return "capacitances"
    if "gate charge" in haystack:
        return "gate_charge"
    if "dynamic input output" in haystack:
        return "gate_charge"
    if "safe operating" in haystack:
        return "safe_operating_area"
    if "thermal impedance" in haystack or "zth" in haystack:
        return "thermal_impedance"
    if "forward characteristics" in haystack or "diode" in haystack:
        return "body_diode"
    if "breakdown voltage" in haystack:
        return "breakdown_voltage"
    if "transfer characteristics" in haystack:
        return "transfer"
    if "output characteristics" in haystack:
        return "output"
    if "on resistance" in haystack or "rds" in haystack:
        return "rds_on"
    return "chart"


def formula_from_text(lines: list[list[Word]], bbox: tuple[float, float, float, float]) -> str:
    x0, y0, x1, y1 = bbox
    bottom_words: list[Word] = []
    # Formula captions are usually in the bottom strip of the panel.
    for line in lines:
        for word in line:
            cx = (word.x0 + word.x1) / 2
            cy = (word.y0 + word.y1) / 2
            if x0 <= cx <= x1 and y0 + 0.80 * (y1 - y0) <= cy <= y1:
                bottom_words.append(word)
    candidates: list[str] = []
    for line in group_words_into_lines(bottom_words):
        txt = line_text(line)
        if "=" in txt or "parameter" in txt.lower():
            candidates.append(txt)
    return " ".join(candidates)


def crop_panel(
    page_png: Path,
    page: PageText,
    bbox_pt: tuple[float, float, float, float],
    out_png: Path,
) -> tuple[float, float, float, float]:
    """Crop the panel image; return the EFFECTIVE crop region in PDF points.

    The crop adds a margin and truncates to whole pixels, so the saved image
    does not cover bbox_pt exactly. Digitizers that map PDF coordinates onto
    crop pixels must use the returned region, not bbox_pt — the mismatch is
    ~5 px at the panel edges at 180 dpi, enough to visibly misplace overlay
    tick markers.
    """
    img = Image.open(page_png).convert("RGB")
    width_px, height_px = img.size
    x0, y0, x1, y1 = bbox_pt
    crop = (
        max(0, int((x0 - CROP_MARGIN_PT) * width_px / page.width_pt)),
        max(0, int((y0 - CROP_MARGIN_PT) * height_px / page.height_pt)),
        min(width_px, int((x1 + CROP_MARGIN_PT) * width_px / page.width_pt)),
        min(height_px, int((y1 + CROP_MARGIN_PT) * height_px / page.height_pt)),
    )
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.crop(crop).save(out_png)
    return (
        crop[0] * page.width_pt / width_px,
        crop[1] * page.height_pt / height_px,
        crop[2] * page.width_pt / width_px,
        crop[3] * page.height_pt / height_px,
    )


def process_pdf(pdf: Path, out_dir: Path, dpi: int) -> list[ChartPanel]:
    pages = run_text_bbox(pdf)
    panels: list[ChartPanel] = []
    with tempfile.TemporaryDirectory(prefix="chart-pages-") as tmp:
        tmpdir = Path(tmp)
        for page in pages:
            titles = find_diagram_titles(page)
            caption_titles = find_caption_titles(page)
            axis_label_spans = gate_charge_axis_label_spans(page)
            if not titles and not caption_titles and not axis_label_spans:
                continue
            page_png = render_page(pdf, page.page_num, dpi, tmpdir)
            with Image.open(page_png) as rendered:
                width_px, height_px = rendered.size
            v_rules_px, h_rules_px = detect_rule_boxes(page_png)
            v_rules_pt = [box_px_to_pt(box, width_px, height_px, page) for box in v_rules_px]
            h_rules_pt = [box_px_to_pt(box, width_px, height_px, page) for box in h_rules_px]
            grid_regions = infer_grid_regions_from_h_rules(page, h_rules_pt)

            lines = group_words_into_lines(page.words)
            for title in titles:
                bbox = choose_panel_bbox(page, title, titles, v_rules_pt, h_rules_pt)
                if bbox is None:
                    continue
                _append_panel(panels, pdf, page, page_png, out_dir, lines, title, bbox)

            for title in caption_titles:
                bbox = choose_caption_panel_bbox(page, title, grid_regions)
                axis_label_bbox = choose_caption_axis_label_bbox(page, title)
                if bbox is None:
                    bbox = axis_label_bbox
                if bbox is None and page.text_source != "pymupdf_fallback":
                    bbox = choose_caption_synthetic_bbox(page, title)
                elif (
                    axis_label_bbox is not None
                    and _caption_prefers_plot_above(title)
                    and _bbox_iou(bbox, axis_label_bbox) < 0.25
                ):
                    bbox = axis_label_bbox
                if bbox is None:
                    continue
                if any(panel.page == page.page_num and _bbox_iou(panel.bbox_pt, bbox) > 0.45 for panel in panels):
                    continue
                _append_panel(panels, pdf, page, page_png, out_dir, lines, title, bbox)

            for axis_idx, axis_label_bbox in enumerate(axis_label_spans, start=1):
                bbox = choose_axis_label_grid_bbox(page, axis_label_bbox, grid_regions)
                if bbox is None:
                    bbox = choose_axis_label_synthetic_bbox(page, axis_label_bbox)
                if bbox is None:
                    continue
                if any(panel.page == page.page_num and _bbox_iou(panel.bbox_pt, bbox) > 0.45 for panel in panels):
                    continue
                title = DiagramTitle(
                    number=950 + axis_idx,
                    title="Gate charge",
                    bbox_pt=axis_label_bbox,
                    line_text=" ".join(w.text for w in words_in_bbox(page.words, axis_label_bbox)),
                )
                _append_panel(panels, pdf, page, page_png, out_dir, lines, title, bbox)
    return panels


def default_sample_pdfs() -> list[Path]:
    return []


def write_outputs(out_dir: Path, panels: list[ChartPanel], errors: list[dict[str, str]] | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = [asdict(panel) for panel in sorted(panels, key=lambda p: (p.part, p.page, p.diagram))]
    (out_dir / "charts.json").write_text(json.dumps(payload, indent=2) + "\n")
    if errors is not None:
        (out_dir / "scan_errors.json").write_text(json.dumps(errors, indent=2) + "\n")

    rows = ["part,page,diagram,kind,title,crop_png,formula,mentions"]
    for panel in payload:
        def csv_field(value: object) -> str:
            text = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value)
            return '"' + text.replace('"', '""') + '"'

        rows.append(
            ",".join(
                [
                    csv_field(panel["part"]),
                    str(panel["page"]),
                    str(panel["diagram"]),
                    csv_field(panel["kind"]),
                    csv_field(panel["title"]),
                    csv_field(panel["crop_png"]),
                    csv_field(panel["formula"]),
                    csv_field(panel["mentions"]),
                ]
            )
        )
    (out_dir / "charts.csv").write_text("\n".join(rows) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="*", type=Path, help="Datasheet PDFs to scan")
    parser.add_argument("--out", type=Path, default=Path("out/datasheet_charts"), help="Output directory")
    parser.add_argument("--dpi", type=int, default=180, help="Render DPI for panel crops/detection")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdfs = args.pdfs or default_sample_pdfs()
    if not pdfs:
        raise SystemExit("no PDFs provided")

    all_panels: list[ChartPanel] = []
    errors: list[dict[str, str]] = []
    for pdf in pdfs:
        if not pdf.exists():
            print(f"skip missing: {pdf}")
            errors.append({"pdf": str(pdf), "error": "missing"})
            continue
        print(f"scan {pdf}")
        try:
            panels = process_pdf(pdf, args.out, args.dpi)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors.append({"pdf": str(pdf), "error": str(exc), "traceback": traceback.format_exc()})
            continue
        else:
            print(f"  found {len(panels)} chart panels")
            all_panels.extend(panels)
    write_outputs(args.out, all_panels, errors)
    print(f"wrote {args.out / 'charts.json'}")
    if errors:
        print(f"wrote {args.out / 'scan_errors.json'} with {len(errors)} errors")


if __name__ == "__main__":
    main()
