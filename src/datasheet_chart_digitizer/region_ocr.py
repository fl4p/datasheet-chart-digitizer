"""Shared, bounded OCR for source-owned PDF regions."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf


def _normalize_token(text: str) -> str:
    return text.strip().strip("|:;").replace(",", ".")


def ocr_words_in_rect(
    pdf: str | Path,
    page_number: int,
    clip_rect,
    *,
    dpi: float = 400.0,
    psm: int = 11,
    timeout: float = 120.0,
) -> list[tuple[float, float, float, float, str]]:
    """OCR one page region; return word boxes in PDF point coordinates."""

    executable = shutil.which("tesseract")
    if executable is None:
        raise RuntimeError("tesseract binary not found; cannot OCR raster axis labels")
    with pymupdf.open(Path(pdf)) as doc:
        page_index = int(page_number) - 1
        if not 0 <= page_index < len(doc):
            raise RuntimeError(f"OCR page {page_number} is outside the document")
        page = doc[page_index]
        clip = pymupdf.Rect(clip_rect) & page.rect
        if clip.is_empty:
            raise RuntimeError("OCR clip rect is empty")
        scale = dpi / 72.0
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False
        )
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "ocr-region.png"
            pix.save(str(png))
            proc = subprocess.run(
                [
                    executable,
                    str(png),
                    "stdout",
                    "--psm",
                    str(psm),
                    "tsv",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract failed: {proc.stderr.strip()[:200]}")
    lines = proc.stdout.splitlines()
    if not lines:
        raise RuntimeError("tesseract returned no TSV output")
    header = lines[0].split("\t")
    columns = {name: index for index, name in enumerate(header)}
    words: list[tuple[float, float, float, float, str]] = []
    for row in lines[1:]:
        cells = row.split("\t")
        if len(cells) != len(header):
            continue
        text = _normalize_token(cells[columns["text"]])
        try:
            confidence = float(cells[columns["conf"]])
        except (KeyError, ValueError):
            continue
        if not text or confidence < 30.0:
            continue
        x0 = clip.x0 + float(cells[columns["left"]]) / scale
        y0 = clip.y0 + float(cells[columns["top"]]) / scale
        x1 = x0 + float(cells[columns["width"]]) / scale
        y1 = y0 + float(cells[columns["height"]]) / scale
        words.append((x0, y0, x1, y1, text))
    return words
