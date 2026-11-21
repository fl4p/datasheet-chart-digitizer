"""Tick labels of a whole-figure raster panel, OCRed one label at a time.

Tesseract reading a whole gutter strip drops isolated one-digit labels ("0",
"1", "2", "3") and mixes in the rotated axis title.  Here each label is
segmented first: glyph clusters in the gutter next to the frame (the label
column/row nearest the plot), one OCR call per cluster on a numeric
whitelist.  The text is only a value claim; ownership of a grid rule and the
axis fit are still decided by ``linear_tick_axis`` (or the caller's log fit).
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np

from . import tesseract_memo
from .capacitance_types import PlotBox
from .raster_figure import NativeRender

_NUMBER_RE = re.compile(r"^-?(?:\d+(?:\.\d+)?|\.\d+)$")
_GLYPH_INK = 160
_WHITELIST = "0123456789.-"
_VOTE_DPI = (300, 400, 500, 600, 800)


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return [
        (int(s[cv2.CC_STAT_LEFT]), int(s[cv2.CC_STAT_TOP]),
         int(s[cv2.CC_STAT_LEFT] + s[cv2.CC_STAT_WIDTH]), int(s[cv2.CC_STAT_TOP] + s[cv2.CC_STAT_HEIGHT]))
        for s in stats[1:n]
    ]


def _tesseract_digits(png: Path, psm: int) -> str:
    executable = shutil.which("tesseract")
    if executable is None:
        raise RuntimeError("tesseract binary not found; cannot OCR raster tick labels")
    proc = tesseract_memo.run(
        [executable, str(png), "stdout", "--psm", str(psm), "-c", f"tessedit_char_whitelist={_WHITELIST}"],
        png, capture_output=True, text=True, timeout=60,
    )
    return proc.stdout.strip().replace(" ", "") if proc.returncode == 0 else ""


def render_label(native: NativeRender, box, pad: int, dpi: int, bands=(), path: Path | None = None) -> Path:
    """Render a native-px box from the PDF; whiten rule *bands* (``raster_plot_labels.rule_bands``)."""
    import pymupdf

    x0, y0, x1, y1 = box
    p0 = native.to_pt(x0 - pad, y0 - pad)
    p1 = native.to_pt(x1 + pad, y1 + pad)
    pix = native.page.get_pixmap(clip=pymupdf.Rect(*p0, *p1), dpi=dpi, colorspace=pymupdf.csGRAY)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
    k = dpi / native.dpi_exact
    def span(v0, v1, origin):
        return max(0, int(np.floor((v0 - origin) * k))), max(0, int(np.ceil((v1 + 1 - origin) * k)))

    for axis, lo, hi, start, end in bands:
        if axis == "x":
            (a, b), (c, d) = span(lo, hi, x0 - pad), span(start, end - 1, y0 - pad)
            gray[c:d, a:b] = 255
        else:
            (a, b), (c, d) = span(lo, hi, y0 - pad), span(start, end - 1, x0 - pad)
            gray[a:b, c:d] = 255
    cv2.imwrite(str(path), gray)
    return path


def _ocr_label(native: NativeRender, box: tuple[int, int, int, int], pad: int, bands=()) -> str | None:
    """Vote over PDF re-renders of one label; None unless near-unanimous.

    Upscaled native pixels misread this class of glyphs badly (Arial "72" as
    "12", "56" as "96"); the PDF renderer's own resampling reads them, but not
    at every density.  Five single-line reads at five densities must agree on
    one string (a trailing full stop ignored) at least four times.
    """
    votes: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "tick.png"
        for dpi in _VOTE_DPI:
            render_label(native, box, pad, dpi, bands, png)
            votes.append(_tesseract_digits(png, 7).rstrip("."))
    best = max(set(votes), key=votes.count)
    if not best or votes.count(best) < len(votes) - 1:
        return None
    return best


def gutter_label_boxes(native: NativeRender, plot: PlotBox, axis: str) -> tuple[list[tuple[int, int, int, int]], float]:
    """Native-px boxes of the labels beside the frame, and their glyph height."""
    # *axis* "y": the right-aligned label column left of the frame; "x": the
    # label row under the frame.  Ink touching the frame side of the gutter
    # (outward tick marks) is not a label.
    gray = native.gray
    ink = gray < _GLYPH_INK
    X0, X1 = native.to_native(plot.x0), native.to_native(plot.x1)
    Y0, Y1 = native.to_native(plot.y0), native.to_native(plot.y1)
    W, H = X1 - X0, Y1 - Y0
    pad = max(4, int(0.012 * W))
    if axis == "y":
        r0, r1 = int(max(0, Y0 - 0.06 * H)), int(min(gray.shape[0], Y1 + 0.06 * H))
        c0, c1 = int(max(0, X0 - 0.22 * W)), int(X0 - pad)
    else:
        r0, r1 = int(Y1 + pad), int(min(gray.shape[0], Y1 + 0.12 * H))
        c0, c1 = int(max(0, X0 - 0.08 * W)), int(min(gray.shape[1], X1 + 0.08 * W))
    if r1 <= r0 or c1 <= c0:
        return [], 0.0
    region = ink[r0:r1, c0:c1]
    glyphs = [b for b in _components(region) if (b[3] - b[1]) >= 3]
    # drop ink touching the frame side (tick marks, frame overhang)
    if axis == "y":
        glyphs = [b for b in glyphs if b[2] < region.shape[1] - 1]
    else:
        glyphs = [b for b in glyphs if b[1] > 0]
    if not glyphs:
        return [], 0.0
    heights = sorted(b[3] - b[1] for b in glyphs)
    glyph_h = float(np.median(heights))
    gap = max(2, int(round(0.45 * glyph_h)))
    merged = cv2.dilate(region.astype(np.uint8), np.ones((1, 2 * gap + 1), np.uint8))
    labels = [b for b in _components(merged) if (b[3] - b[1]) <= 2.5 * glyph_h and (b[3] - b[1]) >= 0.5 * glyph_h]
    if not labels:
        return [], glyph_h
    if axis == "y":
        right = max(b[2] for b in labels)
        labels = [b for b in labels if b[2] >= right - 1.0 * glyph_h]
    else:
        top = min(b[1] for b in labels)
        labels = [b for b in labels if b[1] <= top + 0.6 * glyph_h]
    return [(x0 + c0 + gap, y0 + r0, x1 + c0 - gap, y1 + r0) for x0, y0, x1, y1 in labels], glyph_h


def gutter_labels(native: NativeRender, plot: PlotBox, axis: str) -> list[tuple[str, float]]:
    """(text, crop-px centre along the axis) for each label beside the frame."""
    boxes, glyph_h = gutter_label_boxes(native, plot, axis)
    out: list[tuple[str, float]] = []
    for box in boxes:
        text = _ocr_label(native, box, max(4, int(round(0.5 * glyph_h))))
        if text is None:
            continue
        cx, cy = native.to_crop(0.5 * (box[0] + box[2] - 1), 0.5 * (box[1] + box[3] - 1))
        out.append((text, cy if axis == "y" else cx))
    return out


def numeric_candidates(labels: list[tuple[str, float]]) -> list[tuple[float, float]]:
    """(value, pixel) for labels whose whole text is one number."""
    return [(float(t), px) for t, px in labels if _NUMBER_RE.match(t)]


def raster_linear_axes(native: NativeRender, plot: PlotBox, x_grid, y_grid, x_name: str, y_name: str):
    """Linear X/Y axes from per-label OCR, each value owning a grid rule."""
    from .linear_tick_axis import _fit_axis, _trustworthy_ocr_axis_ticks

    x_ticks = _trustworthy_ocr_axis_ticks(
        numeric_candidates(gutter_labels(native, plot, "x")), x_grid, f"{x_name} OCR", fit_on_rails=True)
    y_ticks = _trustworthy_ocr_axis_ticks(
        numeric_candidates(gutter_labels(native, plot, "y")), y_grid, f"{y_name} OCR", fit_on_rails=True)
    return _fit_axis(x_ticks, x_name), _fit_axis(y_ticks, y_name)

