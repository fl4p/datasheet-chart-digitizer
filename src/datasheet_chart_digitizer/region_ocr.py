"""Shared, bounded OCR for source-owned PDF regions."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf
from PIL import Image


def _normalize_token(text: str) -> str:
    return text.strip().strip("|:;").replace(",", ".")


def _tesseract_words(
    png: Path,
    *,
    clip: pymupdf.Rect,
    scale_x: float,
    scale_y: float,
    psm: int,
    timeout: float,
    whitelist: str | None,
    min_confidence: float,
) -> list[tuple[float, float, float, float, str]]:
    executable = shutil.which("tesseract")
    if executable is None:
        raise RuntimeError("tesseract binary not found; cannot OCR raster axis labels")
    command = [
        executable,
        str(png),
        "stdout",
        "--psm",
        str(psm),
    ]
    if whitelist is not None:
        command.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
    command.append("tsv")
    proc = subprocess.run(
        command,
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
        if not text or confidence < min_confidence:
            continue
        x0 = clip.x0 + float(cells[columns["left"]]) / scale_x
        y0 = clip.y0 + float(cells[columns["top"]]) / scale_y
        x1 = x0 + float(cells[columns["width"]]) / scale_x
        y1 = y0 + float(cells[columns["height"]]) / scale_y
        words.append((x0, y0, x1, y1, text))
    return words


def ocr_rotated_text_in_rect(
    pdf: str | Path,
    page_number: int,
    clip_rect,
    *,
    dpi: float = 500.0,
    psm: int = 6,
    timeout: float = 120.0,
) -> str | None:
    """OCR a vertical text strip (e.g. a rotated Y-axis title) as plain text.

    The strip is rendered, rotated upright (bottom-to-top titles read
    clockwise), and OCRed. Returns None when the region is empty or tesseract
    yields nothing; callers must treat None as absent evidence, never as a
    default.
    """
    with pymupdf.open(Path(pdf)) as doc:
        page_index = int(page_number) - 1
        if not 0 <= page_index < len(doc):
            return None
        page = doc[page_index]
        clip = pymupdf.Rect(clip_rect) & page.rect
        if clip.is_empty:
            return None
        scale = dpi / 72.0
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False
        )
        executable = shutil.which("tesseract")
        if executable is None:
            return None
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "ocr-rotated.png"
            pix.save(str(png))
            with Image.open(png) as image:
                image.rotate(-90, expand=True).save(png)
            proc = subprocess.run(
                [executable, str(png), "stdout", "--psm", str(psm)],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        if proc.returncode != 0:
            return None
        text = proc.stdout.strip()
        return text or None


def ocr_words_in_rect(
    pdf: str | Path,
    page_number: int,
    clip_rect,
    *,
    dpi: float = 400.0,
    psm: int = 11,
    timeout: float = 120.0,
    whitelist: str | None = None,
    min_confidence: float = 30.0,
) -> list[tuple[float, float, float, float, str]]:
    """OCR one page region; return word boxes in PDF point coordinates."""

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
            return _tesseract_words(
                png,
                clip=clip,
                scale_x=pix.width / clip.width,
                scale_y=pix.height / clip.height,
                psm=psm,
                timeout=timeout,
                whitelist=whitelist,
                min_confidence=min_confidence,
            )


def ocr_words_on_page_poppler(
    pdf: str | Path,
    page_number: int,
    *,
    dpi: float = 180.0,
    psm: int = 6,
    timeout: float = 120.0,
    min_confidence: float = 0.0,
) -> list[tuple[float, float, float, float, str]]:
    """OCR one full page rendered by Poppler when an embedded chart defeats MuPDF."""

    executable = shutil.which("pdftoppm")
    if executable is None:
        raise RuntimeError("pdftoppm binary not found; cannot render raster axis labels")
    source = Path(pdf)
    with pymupdf.open(source) as doc:
        page_index = int(page_number) - 1
        if not 0 <= page_index < len(doc):
            raise RuntimeError(f"OCR page {page_number} is outside the document")
        page_rect = pymupdf.Rect(doc[page_index].rect)
    private_tmp = Path("/private/tmp")
    temp_root = private_tmp if private_tmp.is_dir() else None
    with tempfile.TemporaryDirectory(prefix="dsdig-poppler-ocr-", dir=temp_root) as tmp:
        prefix = Path(tmp) / "page"
        proc = subprocess.run(
            [
                executable,
                "-r",
                str(dpi),
                "-png",
                "-singlefile",
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                str(source),
                str(prefix),
            ],
            capture_output=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            error = proc.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"pdftoppm failed: {error[:200]}")
        png = prefix.with_suffix(".png")
        if not png.exists():
            raise RuntimeError("pdftoppm produced no page image")
        pix = pymupdf.Pixmap(str(png))
        return _tesseract_words(
            png,
            clip=page_rect,
            scale_x=pix.width / page_rect.width,
            scale_y=pix.height / page_rect.height,
            psm=psm,
            timeout=timeout,
            whitelist=None,
            min_confidence=min_confidence,
        )


def ocr_words_in_poppler_page_rect(
    pdf: str | Path,
    page_number: int,
    clip_rect,
    *,
    render_dpi: float = 180.0,
    upscale: int = 4,
    psm: int = 6,
    timeout: float = 120.0,
    min_confidence: float = 0.0,
) -> list[tuple[float, float, float, float, str]]:
    """OCR an upscaled crop from a Poppler page render."""

    executable = shutil.which("pdftoppm")
    if executable is None:
        raise RuntimeError("pdftoppm binary not found; cannot render raster axis labels")
    source = Path(pdf)
    with pymupdf.open(source) as doc:
        page_index = int(page_number) - 1
        if not 0 <= page_index < len(doc):
            raise RuntimeError(f"OCR page {page_number} is outside the document")
        page_rect = pymupdf.Rect(doc[page_index].rect)
        requested = pymupdf.Rect(clip_rect) & page_rect
    if requested.is_empty:
        raise RuntimeError("OCR clip rect is empty")
    private_tmp = Path("/private/tmp")
    temp_root = private_tmp if private_tmp.is_dir() else None
    with tempfile.TemporaryDirectory(prefix="dsdig-poppler-crop-", dir=temp_root) as tmp:
        prefix = Path(tmp) / "page"
        proc = subprocess.run(
            [
                executable,
                "-r",
                str(render_dpi),
                "-png",
                "-singlefile",
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                str(source),
                str(prefix),
            ],
            capture_output=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            error = proc.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"pdftoppm failed: {error[:200]}")
        page_png = prefix.with_suffix(".png")
        if not page_png.exists():
            raise RuntimeError("pdftoppm produced no page image")
        with Image.open(page_png) as page_image:
            scale_x = page_image.width / page_rect.width
            scale_y = page_image.height / page_rect.height
            x0 = max(0, int(math.floor(requested.x0 * scale_x)))
            y0 = max(0, int(math.floor(requested.y0 * scale_y)))
            x1 = min(page_image.width, int(math.ceil(requested.x1 * scale_x)))
            y1 = min(page_image.height, int(math.ceil(requested.y1 * scale_y)))
            crop = page_image.crop((x0, y0, x1, y1))
            crop = crop.resize(
                (crop.width * upscale, crop.height * upscale),
                Image.Resampling.LANCZOS,
            )
            crop_png = Path(tmp) / "crop.png"
            crop.save(crop_png)
        actual = pymupdf.Rect(
            x0 / scale_x,
            y0 / scale_y,
            x1 / scale_x,
            y1 / scale_y,
        )
        return _tesseract_words(
            crop_png,
            clip=actual,
            scale_x=crop.width / actual.width,
            scale_y=crop.height / actual.height,
            psm=psm,
            timeout=timeout,
            whitelist=None,
            min_confidence=min_confidence,
        )
