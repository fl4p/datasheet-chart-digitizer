#!/usr/bin/env python3
"""Find chart panels in datasheet PDFs.
This pass deliberately stops at "find all charts and emit crops + metadata";
chart-specific trace digitizers build on top of this index.
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import shutil
import subprocess
import tempfile
import traceback
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
import cv2
import pymupdf
from PIL import Image
try:
    from .chart_classifier import CAPACITANCE_WORDS, classify_chart, compact_formula_chart_kind, is_marketing_feature_title, is_spaced_figure_start, is_spec_table_header_title, rdson_formula_direction, repair_spaced_caption_text, title_owns_chart_kind
    from .crop_transform import CROP_MARGIN_PT
    from .finder_caption_geometry import (
        bbox_iou as _bbox_iou,
        bbox_evidences_breakdown,
        bbox_overlap_fraction_of_smaller as _bbox_overlap_fraction_of_smaller,
        bbox_looks_like_spec_table,
        bound_caption_bbox_to_caption_row,
        bound_caption_bbox_to_own_column as _bound_caption_bbox_to_own_column,
        caption_axis_direction,
        caption_continuation as _caption_continuation,
        caption_leads_nearer_grid, compact_formula_caption_direction, breakdown_symbol_caption_direction,
        caption_leading_plot_bbox,
        caption_image_panel_bbox as _caption_image_panel_bbox,
        caption_vector_frame_bbox as _caption_vector_frame_bbox,
        expand_breakdown_bbox_to_axis_label, expand_numbered_dual_y_gate_bbox,
        expand_caption_bbox_to_axis_labels as _expand_caption_bbox,
        extend_wrapped_caption_titles, detached_numbered_caption_title,
        frame_bound_short_caption_segments,
        grid_rows_belong_to_same_panel, grid_rule_widths_are_compatible,
        numbered_breakdown_vector_frame_bbox as _numbered_breakdown_vector_frame_bbox, page_image_rects as _page_image_rects,
        revision_history_region, synthetic_bbox_has_plot_evidence,
        page_vector_plot_frames as _page_vector_plot_frames,
        trim_adjacent_chart_caption,
        words_in_bbox,
    )
except ImportError:  # pragma: no cover - direct script compatibility
    from chart_classifier import CAPACITANCE_WORDS, classify_chart, compact_formula_chart_kind, is_marketing_feature_title, is_spaced_figure_start, is_spec_table_header_title, rdson_formula_direction, repair_spaced_caption_text, title_owns_chart_kind
    from crop_transform import CROP_MARGIN_PT
    from finder_caption_geometry import (
        bbox_iou as _bbox_iou,
        bbox_evidences_breakdown,
        bbox_overlap_fraction_of_smaller as _bbox_overlap_fraction_of_smaller,
        bbox_looks_like_spec_table,
        bound_caption_bbox_to_caption_row,
        bound_caption_bbox_to_own_column as _bound_caption_bbox_to_own_column,
        caption_axis_direction,
        caption_continuation as _caption_continuation,
        caption_leads_nearer_grid, compact_formula_caption_direction, breakdown_symbol_caption_direction,
        caption_leading_plot_bbox,
        caption_image_panel_bbox as _caption_image_panel_bbox,
        caption_vector_frame_bbox as _caption_vector_frame_bbox,
        expand_breakdown_bbox_to_axis_label, expand_numbered_dual_y_gate_bbox,
        expand_caption_bbox_to_axis_labels as _expand_caption_bbox,
        extend_wrapped_caption_titles, detached_numbered_caption_title,
        frame_bound_short_caption_segments,
        grid_rows_belong_to_same_panel, grid_rule_widths_are_compatible,
        numbered_breakdown_vector_frame_bbox as _numbered_breakdown_vector_frame_bbox, page_image_rects as _page_image_rects,
        revision_history_region, synthetic_bbox_has_plot_evidence,
        page_vector_plot_frames as _page_vector_plot_frames,
        trim_adjacent_chart_caption,
        words_in_bbox,
    )
DIAGRAM_RE = re.compile(r"^Diagram\s+(\d+):?\s*(.*)$", re.IGNORECASE)
# Figure numbers: Toshiba "Fig. 8.8", TI "Figure 4-5." (section-hyphenated).
FIGURE_RE = re.compile(r"^(?:Figure|Fig\.?)\s+(\d+(?:[.\-]\d+)?)[\.,:]?\s*(.*)$", re.IGNORECASE)
COMPACT_FIGURE_RE = re.compile(r"^(?:Figure|Fig\.?)(\d+(?:[.\-]\d+)?)[\.,:\-]?\s*(.*)$", re.IGNORECASE)
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
    try:
        proc = subprocess.run(["pdftotext", "-bbox-layout", str(pdf), "-"], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if not proc.stdout.strip(): raise ValueError("pdftotext -bbox-layout returned empty output")
        root = ET.fromstring(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", proc.stdout))
    except (OSError, subprocess.CalledProcessError, ET.ParseError, ValueError):
        return _run_pymupdf_text(pdf, "pymupdf_bbox_fallback")
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
    if not primary_pages or not any(page.words for page in primary_pages): return _run_pymupdf_text(pdf, "pymupdf_bbox_fallback")
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
def _tesseract_tsv(page_png: Path, timeout: float = 20.0) -> str | None:
    """Return sparse-layout OCR TSV, degrading cleanly when OCR is unavailable."""
    executable = shutil.which("tesseract")
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, str(page_png), "stdout", "--psm", "11", "tsv"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return completed.stdout
def _page_text_from_tesseract_tsv(
    tsv: str,
    *,
    page_num: int,
    width_pt: float,
    height_pt: float,
    width_px: int,
    height_px: int,
) -> PageText:
    """Map word-level Tesseract pixel boxes into PDF-point coordinates."""
    words: list[Word] = []
    for row in csv.DictReader(tsv.splitlines(), delimiter="\t"):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            confidence = float(row.get("conf") or -1)
            left = float(row["left"])
            top = float(row["top"])
            word_width = float(row["width"])
            word_height = float(row["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if confidence < 10:
            continue
        x_scale = width_pt / max(1, width_px)
        y_scale = height_pt / max(1, height_px)
        words.append(
            Word(
                text=text,
                x0=left * x_scale,
                y0=top * y_scale,
                x1=(left + word_width) * x_scale,
                y1=(top + word_height) * y_scale,
            )
        )
    return PageText(
        page_num=page_num,
        width_pt=width_pt,
        height_pt=height_pt,
        words=words,
        text_source="tesseract_fallback",
    )
def run_tesseract_page_text(
    pdf: Path,
    *,
    dpi: int = 160,
    timeout: float = 20.0,
) -> list[PageText]:
    """OCR every page for gate-only fallback discovery."""

    if shutil.which("tesseract") is None:
        return []
    private_tmp = Path("/private/tmp")
    temp_root = private_tmp if private_tmp.is_dir() else None
    pages: list[PageText] = []
    with pymupdf.open(pdf) as doc, tempfile.TemporaryDirectory(
        prefix="dsdig-ocr-", dir=temp_root
    ) as tmp:
        scale = dpi / 72.0
        for page_num, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            page_png = Path(tmp) / f"page-{page_num}.png"
            pix.save(page_png)
            tsv = _tesseract_tsv(page_png, timeout=timeout)
            if tsv is None:
                continue
            pages.append(
                _page_text_from_tesseract_tsv(
                    tsv,
                    page_num=page_num,
                    width_pt=float(page.rect.width),
                    height_pt=float(page.rect.height),
                    width_px=pix.width,
                    height_px=pix.height,
                )
            )
    return pages

def _run_pymupdf_text(pdf: Path, text_source: str = "pymupdf_fallback") -> list[PageText]:
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
                    text_source=text_source,
                )
            )
    if not pages or not any(page.words for page in pages):
        raise RuntimeError(f"both bbox text paths returned no words for {pdf}")
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
            segment = trim_adjacent_chart_caption(
                line[start:end], page.width_pt, classify_chart
            )
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
        if is_spaced_figure_start([item.text for item in line], idx):
            starts.append(idx)
            continue
        lower = token.lower().rstrip(".:,")
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
                    "transfer",
                    "waveforms",
                    "dynamic",
                )
            ):
                starts.append(idx)
            continue
        if lower in {"figure", "fig"} and idx + 1 < len(line):
            if re.match(r"^\d+(?:[.\-]\d+)?[\.,:]?$", line[idx + 1].text):
                starts.append(idx)
            continue

        if lower in {"typ", "typical", "typicaly", "typycal"}:
            if idx > 0 and re.match(r"^\d+(?:\.\d+)?[\.,:]?$", line[idx - 1].text.strip()):
                continue
            tail = " ".join(w.text for w in line[idx : idx + 5]).lower()
            if any(phrase in tail for phrase in ("gate", "capacitance", "breakdown", "transfer")):
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

        if not re.fullmatch(r"[0-9]+", token) or int(token) > 50 or idx + 1 >= len(line):
            continue
        tail = " ".join(w.text for w in line[idx + 1 : idx + 5]).lower()
        if any(
            phrase in tail
            for phrase in ("typ", "gate", "avalanche", "breakdown", "transfer", "waveforms")
        ):
            starts.append(idx)
    return starts


def _parse_caption_text(text: str) -> tuple[int | None, str] | None:
    text = repair_spaced_caption_text(text)
    match = FIGURE_RE.match(text)
    if match:
        return int(re.sub(r"[.\-]", "", match.group(1))), match.group(2).strip()
    match = COMPACT_FIGURE_RE.match(text)
    if match:
        title = match.group(2).strip()
        if title.startswith("-"):
            title = title[1:].strip()
        return int(re.sub(r"[.\-]", "", match.group(1))), title
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and re.fullmatch(r"[0-9]+", parts[0]) and int(parts[0]) <= 50:
        return int(parts[0]), parts[1].strip()
    if re.match(r"(?i)^gate\s+charge\b", text):
        return None, text.strip()
    if re.match(r"(?i)^Typ(?:ical|ycal)?\.?\s+", text):
        return None, text.strip()
    return None
def find_caption_titles(page: PageText) -> list[DiagramTitle]:
    """Find non-Diagram chart captions used by many gate-charge plots."""
    titles: list[DiagramTitle] = []
    lines = group_words_into_lines(page.words)
    claimed_title_lines: set[int] = set()
    for line_idx, line in enumerate(lines):
        if line_idx in claimed_title_lines:
            continue
        starts = _caption_starts(line)
        if not starts:
            continue
        starts.append(len(line))
        for start, end in zip(starts, starts[1:]):
            segment = trim_adjacent_chart_caption(
                line[start:end], page.width_pt, classify_chart
            )
            text = line_text(segment)
            parsed = _parse_caption_text(text)
            if parsed is None:
                continue
            parsed_number, title = parsed
            number = parsed_number if parsed_number is not None else 900 + len(titles) + 1
            detached_bbox = None
            detached_line_idx = None
            if not title:
                detached = detached_numbered_caption_title(
                    page, lines, line_idx, segment
                )
                if detached is not None:
                    title, detached_bbox, detached_line_idx = detached
            title_for_classification = title
            title_tail = title_for_classification.lower().rstrip(" .:")
            if detached_bbox is None and ("gate" in title_tail or title_tail.endswith((" vs", " versus"))):
                continuation = _caption_continuation(lines, line_idx, segment)
                initial_kind = classify_chart(title, "")
                continued_kind = classify_chart(f"{title} {continuation}", "")
                if continuation and (initial_kind == "chart" or continued_kind in {initial_kind, "chart"}):
                    title_for_classification = f"{title_for_classification} {continuation}".strip()
            # Caption-style pages cover a narrow set of chart families that do
            # not use the Infineon ``Diagram N`` convention.  Do not admit all
            # numbered figures: that would turn ordinary datasheet prose into
            # panel candidates.
            if classify_chart(title_for_classification, "") not in {
                "gate_charge",
                "breakdown_voltage",
                "body_diode",
                "transfer", "capacitances", "rds_on",
            }:
                continue
            title = title_for_classification
            if detached_line_idx is not None:
                claimed_title_lines.add(detached_line_idx)
            titles.append(
                DiagramTitle(
                    number=number,
                    title=title,
                    bbox_pt=detached_bbox or line_bbox(segment),
                    line_text=(f"{text} {title}" if detached_bbox else text),
                )
            )
    titles.sort(key=lambda t: (t.bbox_pt[1], t.bbox_pt[0], t.number))
    return titles


def extend_wrapped_titles(page: PageText, titles: list[DiagramTitle]) -> list[DiagramTitle]:
    """Add short continuation text from the title band when a title wraps."""
    return extend_wrapped_caption_titles(
        page, titles, group_words_into_lines(page.words)
    )


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
            if (abs(bc - gc) <= page.width_pt * 0.08
                    and overlap >= min(bw, gw) * 0.55
                    and grid_rule_widths_are_compatible(bw, gw)):
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
                if grid_rows_belong_to_same_panel(
                    page.words,
                    prev_center_y,
                    center_y,
                    min(box[0], chunks[-1][-1][0]),
                    max(box[2], chunks[-1][-1][2]),
                ):
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
def _caption_requires_plot_above(title: DiagramTitle) -> bool:
    """Default numbered captions above; own-axis evidence may reverse it."""
    normalized = re.sub(r"\s+", " ", title.title.lower()).strip()
    kind = classify_chart(title.title, "")
    compact = compact_formula_chart_kind(title.title)
    above = bool(compact and re.match(r"(?i)^fig(?:ure)?\.?\s*\d+[.\-]\d+", title.line_text.strip())) or compact is None and (kind in {"body_diode", "transfer"} or "gate charge waveform definitions" in normalized or normalized == "dynamic input/output characteristics")
    return _caption_prefers_plot_above(title) and above
def _has_gate_charge_axis_label_above_caption(page: PageText, title: DiagramTitle) -> bool:
    tx0, ty0, tx1, _ = title.bbox_pt
    for line in group_words_into_lines(page.words):
        text = line_text(line)
        if not _is_gate_charge_axis_label(text, ocr_tolerant=page.text_source == "tesseract_fallback"):
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
    kind = classify_chart(title.title, "")
    semantic_candidates = []
    if kind == "breakdown_voltage":
        for item in candidates:
            if bbox_evidences_breakdown(page.words, item[2], _token_norm):
                semantic_candidates.append(item)
        if semantic_candidates:
            candidates = semantic_candidates
    requires_plot_above = _caption_requires_plot_above(title)
    evidenced_direction = ("above" if _caption_requires_plot_above(title) else compact_formula_caption_direction(candidates, title)) if compact_formula_chart_kind(title.title) is not None else (None if semantic_candidates else (
        breakdown_symbol_caption_direction(candidates, title) or caption_axis_direction(page, title, kind, _token_norm)))
    if kind not in {"gate_charge", "breakdown_voltage"} and evidenced_direction is None and not requires_plot_above and caption_leads_nearer_grid(candidates, ty0, ty1):
        evidenced_direction = "below"
    if (
        kind == "breakdown_voltage"
        and not semantic_candidates
        and evidenced_direction is None
        and len(candidates) > 1
    ):
        # A breakdown caption between two same-column grids is ambiguous
        # without its own temperature/breakdown axis evidence.  Nearest-grid
        # selection would silently bind a neighboring transfer/gate plot.
        return None
    if evidenced_direction is not None or requires_plot_above or (
        _caption_prefers_plot_above(title) and _has_gate_charge_axis_label_above_caption(page, title)
    ):
        below = [item for item in candidates if item[2][1] >= ty1]
        above = [item for item in candidates if item[2][3] <= ty0]
        if evidenced_direction == "below" and below:
            candidates = below
        elif evidenced_direction == "above" and above:
            candidates = above
        elif evidenced_direction is not None:
            return None
        elif above:
            candidates = above
        elif requires_plot_above and len(below) == 1 and below[0][1] <= 36.0:
            candidates = below
        elif requires_plot_above:
            # A numbered body-diode or transfer caption describes the plot
            # above it.  A plausible grid below is a neighboring chart, not a
            # fallback.
            return None
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

def _is_gate_charge_axis_label(text: str, *, ocr_tolerant: bool = False) -> bool:
    normalized = text.lower().replace("‑", "-").replace("–", "-").replace("_", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    has_qg = bool(re.search(r"\bq\s*g\b|\bqg(?:tot|total)?\b|\bqgate\b", normalized))
    has_ocr_qg = (
        ocr_tolerant
        and bool(re.search(r"\bq[qces]\b", normalized))
        and "gate charge" in normalized
        and "nc" in normalized
    )
    has_charge_unit = "charge" in normalized or "nc" in normalized
    return (has_qg or has_ocr_qg) and has_charge_unit
def _token_norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower().replace("‑", "-").replace("–", "-"))
def _is_qg_token_pair(line: list[Word], idx: int, *, ocr_tolerant: bool = False) -> bool:
    token = _token_norm(line[idx].text)
    if token in {"qg", "qgate", "qgtot", "qgtotal", "qtotal"}:
        return True
    if ocr_tolerant and token in {"qc", "qe", "qq", "qs"}:
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
    ocr_tolerant = page.text_source == "tesseract_fallback"
    for line in group_words_into_lines(page.words):
        for idx, word in enumerate(line):
            if not _is_qg_token_pair(line, idx, ocr_tolerant=ocr_tolerant):
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
            if not _is_gate_charge_axis_label(text, ocr_tolerant=ocr_tolerant):
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
        if not _is_gate_charge_axis_label(text, ocr_tolerant=page.text_source == "tesseract_fallback"):
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


def choose_caption_axis_label_bbox_for_kind(
    page: PageText,
    title: DiagramTitle,
) -> tuple[float, float, float, float] | None:
    """Use the Qg-axis fallback only for a gate-charge caption.
    Other families would replace their local grid with a neighboring Qg plot.
    """
    if classify_chart(title.title, "") != "gate_charge":
        return None
    return choose_caption_axis_label_bbox(page, title)


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
    evidenced_bbox = caption_leading_plot_bbox(
        page, title, classify_chart(title.title, ""), _token_norm
    )
    if evidenced_bbox is not None:
        return evidenced_bbox
    tx0, ty0, tx1, ty1 = title.bbox_pt
    tcx = 0.5 * (tx0 + tx1)
    title_width = tx1 - tx0
    half_width = min(max(90.0, title_width * 0.80), page.width_pt * 0.22) if compact_formula_chart_kind(title.title) is not None else min(max(170.0, title_width * 0.80), page.width_pt * 0.40)
    x0 = max(0.0, tcx - half_width)
    x1 = min(page.width_pt, tcx + half_width)
    if classify_chart(title.title, "") == "breakdown_voltage":
        options = (
            (x0, max(0.0, ty0 - 215.0), x1, max(0.0, ty0 - 4.0)),
            (x0, min(page.height_pt, ty1 + 4.0), x1, min(page.height_pt, ty1 + 239.0)),
        )
        for option in options:
            if bbox_evidences_breakdown(page.words, option, _token_norm):
                return option
        return None
    if _caption_requires_plot_above(title) or (
        _caption_prefers_plot_above(title) and ty0 > page.height_pt * 0.40
    ):
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
_AXIS_NUM_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")
def choose_caption_vector_frame_bbox(page: PageText, title: DiagramTitle, frames):
    return _caption_vector_frame_bbox(
        page, title, frames, numbered_caption=_caption_prefers_plot_above(title)
    )
def _expand_caption_bbox_to_axis_labels(page: PageText, bbox, kind="capacitances"):
    expanded = _expand_caption_bbox(page, bbox, _AXIS_NUM_RE, kind)
    return expand_breakdown_bbox_to_axis_label(page.words, expanded, _token_norm) if kind == "breakdown_voltage" else expanded
def _bbox_looks_like_spec_table(
    page: PageText,
    bbox: tuple[float, float, float, float],
    own_families: frozenset[str] = frozenset(),
) -> bool:
    """True when a candidate panel region reads like a ruled spec table.

    Two independent signals, either suffices:
    - column headers (PARAMETER / MIN / TYP / MAX / UNIT ...) that never appear
      together inside a real chart panel's axis text;
    - >=4 distinct parameter families (a bbox clipped to the row-name column of
      a table carries no headers, but no single chart mixes leakage, threshold,
      capacitance, charge and resistance).

    `own_families` names the families the CANDIDATE itself was found by (e.g.
    "charge" for a gate-charge axis-label panel). Those are guaranteed present
    on a legitimate chart and must not count toward the table signal, else a
    real chart with a few condition callouts (RDS(on), Vth, ...) trips it.
    """
    return bbox_looks_like_spec_table(page.words, bbox, own_families, _token_norm)


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
    kind_from_title = title_owns_chart_kind(title.title, title.number, text)
    if kind_from_title is not None:
        # An explicit numbered caption/diagram title is stronger evidence than
        # the panel text, which can bleed in from an adjacent chart when a
        # caption binds across columns.
        kind = kind_from_title
    else:
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
    return process_page_texts(pdf, out_dir, dpi, run_text_bbox(pdf))
def process_page_texts(
    pdf: Path,
    out_dir: Path,
    dpi: int,
    pages: list[PageText],
) -> list[ChartPanel]:
    """Run normal panel discovery against an injected page-text source."""
    panels: list[ChartPanel] = []
    ocr_by_page: dict[int, PageText] = {}
    with tempfile.TemporaryDirectory(prefix="chart-pages-") as tmp:
        tmpdir = Path(tmp)
        for page in pages:
            revision_bbox = revision_history_region(page.words, _token_norm)
            titles = extend_wrapped_titles(page, find_diagram_titles(page))
            caption_titles = find_caption_titles(page)
            axis_label_spans = gate_charge_axis_label_spans(page)
            page_vector_frames: list[tuple[float, float, float, float]] | None = None
            recovered_numbers: set[int] = set()
            if revision_bbox is not None:
                rx0, ry0, rx1, ry1 = revision_bbox

                def outside_revision(title: DiagramTitle) -> bool:
                    tx0, ty0, tx1, ty1 = title.bbox_pt
                    tcx, tcy = 0.5 * (tx0 + tx1), 0.5 * (ty0 + ty1)
                    return not (rx0 <= tcx <= rx1 and ry0 <= tcy <= ry1)
                titles = [title for title in titles if outside_revision(title)]
                caption_titles = [
                    title for title in caption_titles if outside_revision(title)
                ]
                axis_label_spans = [
                    bbox for bbox in axis_label_spans
                    if not (rx0 <= 0.5 * (bbox[0] + bbox[2]) <= rx1 and ry0 <= 0.5 * (bbox[1] + bbox[3]) <= ry1)
                ]
            if not titles and not caption_titles and not axis_label_spans:
                page_vector_frames = _page_vector_plot_frames(pdf, page.page_num, page)
                recovered = frame_bound_short_caption_segments(
                    page, group_words_into_lines(page.words), page_vector_frames,
                    classify_chart, is_spec_table_header_title,
                )
                caption_titles = [DiagramTitle(901 + index, text, bbox, text) for index, (text, bbox) in enumerate(recovered)]
                recovered_numbers = {title.number for title in caption_titles}
                if not caption_titles: continue
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
            page_image_rects: list[tuple[float, float, float, float]] | None = None
            for title in caption_titles:
                if is_spec_table_header_title(title.title) or is_marketing_feature_title(title.title): continue
                kind = classify_chart(title.title, "")
                direction = caption_axis_direction(page, title, kind, _token_norm)
                if kind == "rds_on" and direction is None and rdson_formula_direction(title.title) is None and title.number not in recovered_numbers: continue
                use_breakdown_frame = kind == "breakdown_voltage" and title.number < 900
                if use_breakdown_frame and page_vector_frames is None: page_vector_frames = _page_vector_plot_frames(pdf, page.page_num, page)
                bbox = _numbered_breakdown_vector_frame_bbox(page, title, page_vector_frames or []) if use_breakdown_frame else None
                if bbox is None and title.number not in recovered_numbers: bbox = choose_caption_panel_bbox(page, title, grid_regions)
                if bbox is None and title.number >= 900:
                    if page_vector_frames is None:
                        page_vector_frames = _page_vector_plot_frames(pdf, page.page_num, page)
                    bbox = _caption_vector_frame_bbox(
                        page, title, page_vector_frames, numbered_caption=False,
                        tight=title.number in recovered_numbers,
                    )
                axis_label_bbox = choose_caption_axis_label_bbox_for_kind(page, title)
                if kind == "gate_charge" and axis_label_bbox is None and title.number < 900 and page.text_source != "tesseract_fallback":
                    tsv = _tesseract_tsv(page_png) if page.page_num not in ocr_by_page else None
                    if tsv is not None:
                        ocr_by_page[page.page_num] = _page_text_from_tesseract_tsv(tsv, page_num=page.page_num, width_pt=page.width_pt, height_pt=page.height_pt, width_px=width_px, height_px=height_px)
                    axis_label_bbox = choose_caption_axis_label_bbox_for_kind(ocr_by_page.get(page.page_num, page), title)
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
                if bbox is None: continue
                bbox = expand_numbered_dual_y_gate_bbox(page, title, bbox)
                if kind == "capacitances":
                    # Scoped to capacitance captions so gate-charge crops (and
                    # the Vpl parity corpus built on them) stay byte-identical.
                    if page_image_rects is None:
                        page_image_rects = _page_image_rects(pdf, page.page_num)
                    image_bbox = _caption_image_panel_bbox(page_image_rects, title, bbox)
                    if image_bbox is None and title.number < 900:
                        image_bbox = _caption_image_panel_bbox(page_image_rects, title, None)
                    if image_bbox is not None:
                        # A whole-figure raster is the exact label-complete panel.
                        bbox = image_bbox
                if kind in {"capacitances", "breakdown_voltage", "body_diode", "rds_on", "transfer"}:
                    bbox = _expand_caption_bbox_to_axis_labels(page, bbox, kind)
                preserve_left = caption_leading_plot_bbox(page, title, kind, _token_norm) == bbox or (kind in {"transfer", "body_diode"} and direction is not None)
                bbox = _bound_caption_bbox_to_own_column(
                    page, title, bbox,
                    preserve_evidenced_left_gutter=preserve_left,
                )
                bbox = bound_caption_bbox_to_caption_row(
                    page, title, bbox, caption_titles,
                    0.5 * (bbox[1] + bbox[3]) < 0.5 * (title.bbox_pt[1] + title.bbox_pt[3]),
                ) if bbox else None
                if bbox is None:
                    continue
                if _bbox_looks_like_spec_table(page, bbox): continue
                if any(panel.page == page.page_num and _bbox_iou(panel.bbox_pt, bbox) > 0.45 for panel in panels):
                    continue
                _append_panel(panels, pdf, page, page_png, out_dir, lines, title, bbox)
            for axis_idx, axis_label_bbox in enumerate(axis_label_spans, start=1):
                bbox = choose_axis_label_grid_bbox(page, axis_label_bbox, grid_regions)
                if bbox is None:
                    candidate = choose_axis_label_synthetic_bbox(page, axis_label_bbox)
                    if candidate is not None and synthetic_bbox_has_plot_evidence(
                        page, pdf, page.page_num, candidate, grids=grid_regions,
                        horizontal_rules=h_rules_pt, vertical_rules=v_rules_pt,
                    ):
                        bbox = candidate
                if bbox is None:
                    continue
                if _bbox_looks_like_spec_table(page, bbox):
                    continue
                if any(
                    panel.page == page.page_num and (
                        _bbox_iou(panel.bbox_pt, bbox) > 0.45
                        or _bbox_overlap_fraction_of_smaller(panel.bbox_pt, bbox) >= 0.60
                    ) for panel in panels
                ):
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
