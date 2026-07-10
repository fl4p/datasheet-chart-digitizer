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
from PIL import Image


DIAGRAM_RE = re.compile(r"^Diagram\s+(\d+):?\s*(.*)$", re.IGNORECASE)
FIGURE_RE = re.compile(r"^(?:Figure|Fig\.?)\s+(\d+)[\.:]?\s*(.*)$", re.IGNORECASE)
COMPACT_FIGURE_RE = re.compile(r"^(?:Figure|Fig\.?)(\d+)[\.:\\-]?\s*(.*)$", re.IGNORECASE)
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
    crop_png: str
    text: str
    formula: str
    mentions: list[str]


def run_text_bbox(pdf: Path) -> list[PageText]:
    proc = subprocess.run(
        ["pdftotext", "-bbox-layout", str(pdf), "-"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    xml_text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", proc.stdout)
    root = ET.fromstring(xml_text)
    pages: list[PageText] = []
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
        pages.append(
            PageText(
                page_num=page_idx,
                width_pt=float(page_el.attrib["width"]),
                height_pt=float(page_el.attrib["height"]),
                words=words,
            )
        )
    return pages


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
            if re.match(r"^\d+[\.:]?$", line[idx + 1].text):
                starts.append(idx)
            continue

        if not token.isdigit() or idx + 1 >= len(line):
            continue
        tail = " ".join(w.text for w in line[idx + 1 : idx + 5]).lower()
        if any(phrase in tail for phrase in ("typ", "gate", "avalanche", "breakdown", "waveforms")):
            starts.append(idx)
    return starts


def _parse_caption_text(text: str) -> tuple[int, str] | None:
    match = FIGURE_RE.match(text)
    if match:
        return int(match.group(1)), match.group(2).strip()
    match = COMPACT_FIGURE_RE.match(text)
    if match:
        title = match.group(2).strip()
        if title.startswith("-"):
            title = title[1:].strip()
        return int(match.group(1)), title
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].isdigit():
        return int(parts[0]), parts[1].strip()
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
            number, title = parsed
            title_for_classification = title
            if classify_chart(title_for_classification, "") != "gate_charge" and "gate" in title_for_classification.lower():
                continuation = _caption_continuation(lines, line_idx, segment)
                if continuation:
                    title_for_classification = f"{title_for_classification} {continuation}".strip()
            if classify_chart(title_for_classification, "") != "gate_charge":
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


def choose_caption_panel_bbox(
    page: PageText,
    title: DiagramTitle,
    grid_regions: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    tx0, ty0, tx1, ty1 = title.bbox_pt
    tcx = (tx0 + tx1) / 2
    best: tuple[float, tuple[float, float, float, float]] | None = None
    candidates = []
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
        if vertical_gap > 85:
            continue
        candidates.append((horizontal_penalty, vertical_gap, region))

    plot_width_candidates = [item for item in candidates if item[2][2] - item[2][0] <= page.width_pt * 0.62]
    if plot_width_candidates:
        candidates = plot_width_candidates

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
    crop_panel(page_png, page, bbox, out_dir / "crops" / rel_crop)
    panels.append(
        ChartPanel(
            pdf=str(pdf.resolve()),
            part=pdf.name.split(".pdf")[0],
            page=page.page_num,
            diagram=title.number,
            title=title.title,
            kind=kind,
            bbox_pt=tuple(round(v, 3) for v in bbox),
            crop_png=str(Path("crops") / rel_crop),
            text=text,
            formula=formula_from_text(lines, bbox),
            mentions=mentions,
        )
    )


def classify_chart(title: str, text: str) -> str:
    haystack = f"{title} {text}".lower().replace("‑", "-").replace("–", "-")
    haystack = re.sub(r"[-_]+", " ", haystack)
    haystack = re.sub(r"\s+", " ", haystack)
    if any(word in haystack for word in CAPACITANCE_WORDS):
        return "capacitances"
    if "gate charge" in haystack:
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
) -> None:
    img = Image.open(page_png).convert("RGB")
    width_px, height_px = img.size
    x0, y0, x1, y1 = bbox_pt
    margin_pt = 2.0
    crop = (
        max(0, int((x0 - margin_pt) * width_px / page.width_pt)),
        max(0, int((y0 - margin_pt) * height_px / page.height_pt)),
        min(width_px, int((x1 + margin_pt) * width_px / page.width_pt)),
        min(height_px, int((y1 + margin_pt) * height_px / page.height_pt)),
    )
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.crop(crop).save(out_png)


def process_pdf(pdf: Path, out_dir: Path, dpi: int) -> list[ChartPanel]:
    pages = run_text_bbox(pdf)
    panels: list[ChartPanel] = []
    with tempfile.TemporaryDirectory(prefix="chart-pages-") as tmp:
        tmpdir = Path(tmp)
        for page in pages:
            titles = find_diagram_titles(page)
            caption_titles = find_caption_titles(page)
            if not titles and not caption_titles:
                continue
            page_png = render_page(pdf, page.page_num, dpi, tmpdir)
            rendered = Image.open(page_png)
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
                if bbox is None:
                    continue
                if any(panel.page == page.page_num and _bbox_iou(panel.bbox_pt, bbox) > 0.45 for panel in panels):
                    continue
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
