"""OCR fallback helpers for chart-panel discovery."""

from __future__ import annotations

import csv
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

try:
    from .finder_types import PageText, Word
except ImportError:  # pragma: no cover - direct script compatibility
    from finder_types import PageText, Word


def tesseract_tsv(page_png: Path, timeout: float = 20.0) -> str | None:
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


def page_text_from_tesseract_tsv(
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
            tsv = tesseract_tsv(page_png, timeout=timeout)
            if tsv is None:
                continue
            pages.append(
                page_text_from_tesseract_tsv(
                    tsv,
                    page_num=page_num,
                    width_pt=float(page.rect.width),
                    height_pt=float(page.rect.height),
                    width_px=pix.width,
                    height_px=pix.height,
                )
            )
    return pages


def dedupe_overprinted_words(words: list[Word]) -> list[Word]:
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
