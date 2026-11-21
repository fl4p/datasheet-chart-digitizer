"""Whole-figure raster panels: curve ink at the embedded image's own resolution.

Some vendors (Toshiba) embed each chart -- frame, grid, curves and every label --
as ONE image.  Such a panel has no vector curve and no native text, so the
vector tracers refuse it by construction.  This module

* re-renders the panel's crop region at the embedded image's native pixel
  density (``render_native``), where the stroke classes separate by width:
  on Toshiba figures minor rules are 2 px, major rules 4 px, the frame 5 px and
  data curves 8 px;
* measures the widest grid rule the crop-level grid detector found and keeps
  only ink strictly thicker than it (``curve_mask``, a square opening).  A curve
  no thicker than the grid leaves nothing and refuses;
* traces exactly one curve by columns (``trace_single_curve``) or N curves by
  rows (``trace_curves_by_rows``), never across ambiguous columns/rows;
* maps every point back to crop-pixel-index coordinates, so the calling class
  keeps its own axis calibration and served-axis grid check unchanged.

Nothing here decides curve identity or serves a value; callers do.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .capacitance_types import PlotBox

# The embedded image must cover the whole plot frame to be "the figure".
_MIN_PLOT_COVERAGE = 0.98
# A rendered density below this is not worth a native pass (crop already as fine).
_MIN_NATIVE_GAIN = 1.25
_INK = 128
# Columns/rows whose run is wider than this multiple of the median stroke are a
# merge, an arrowhead or a horizontal stretch: not a centreline sample.
_MAX_RUN_WIDTH_FACTOR = 1.8


@dataclass(frozen=True)
class NativeRender:
    gray: np.ndarray  # native-density render of the crop region
    scale: float      # native px per crop px
    dpi: float
    page: object = None              # source PDF page (for label re-renders)
    origin_pt: tuple[float, float] = (0.0, 0.0)

    def to_pt(self, x: float, y: float) -> tuple[float, float]:
        """Native pixel-index coordinate -> PDF points (pixel centre)."""
        k = 72.0 / self.dpi_exact
        return self.origin_pt[0] + (x + 0.5) * k, self.origin_pt[1] + (y + 0.5) * k

    @property
    def dpi_exact(self) -> float:
        return self.gray.shape[1] * 72.0 / self._width_pt

    _width_pt: float = 1.0

    def to_crop(self, x: float, y: float) -> tuple[float, float]:
        """Native pixel-index coordinate -> crop pixel-index coordinate."""
        return (x + 0.5) / self.scale - 0.5, (y + 0.5) / self.scale - 0.5

    def to_native(self, v: float) -> float:
        return (v + 0.5) * self.scale - 0.5


def embedded_figure_dpi(page, plot_rect_pt) -> float | None:
    """Pixel density of the embedded image covering the plot, else None."""
    px0, py0, px1, py1 = plot_rect_pt
    area = max(1e-9, (px1 - px0) * (py1 - py0))
    best = None
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        inter = max(0.0, min(x1, px1) - max(x0, px0)) * max(0.0, min(y1, py1) - max(y0, py0))
        if inter / area < _MIN_PLOT_COVERAGE or x1 <= x0 or info.get("width", 0) <= 0:
            continue
        dpi = float(info["width"]) / ((x1 - x0) / 72.0)
        best = dpi if best is None else max(best, dpi)
    return best


def render_native(page, crop_box_pt, crop_shape, plot_rect_pt, fitz) -> NativeRender | None:
    """Render the crop region at the covering image's density; None if no such image."""
    dpi = embedded_figure_dpi(page, plot_rect_pt)
    if dpi is None:
        return None
    x0, y0, x1, y1 = crop_box_pt
    crop_dpi = crop_shape[1] / ((x1 - x0) / 72.0)
    if dpi < _MIN_NATIVE_GAIN * crop_dpi:
        dpi = crop_dpi
    pix = page.get_pixmap(clip=fitz.Rect(x0, y0, x1, y1), dpi=int(round(dpi)), colorspace=fitz.csGRAY)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
    return NativeRender(gray, pix.width / float(crop_shape[1]), float(dpi), page, (x0, y0), _width_pt=x1 - x0)


def _run_containing(profile: np.ndarray, index: int) -> int:
    """Width of the dark run that contains (or is within 2 px of) *index*."""
    n = len(profile)
    hits = [i for i in range(max(0, index - 2), min(n, index + 3)) if profile[i]]
    if not hits:
        return 0
    i = min(hits, key=lambda k: abs(k - index))
    lo = hi = i
    while lo > 0 and profile[lo - 1]:
        lo -= 1
    while hi < n - 1 and profile[hi + 1]:
        hi += 1
    return hi - lo + 1


def rule_width(native: NativeRender, plot: PlotBox, x_rules: list[float], y_rules: list[float]) -> int:
    """Widest interior grid rule, in native px.

    Each rule's width is the 25th percentile of its sampled widths: a steep
    curve running along a rule widens it over part of its length, never over
    most of it.  Rules within 3 crop px of the plot frame are the frame itself
    and are not measured: the frame is outside the traced region anyway.
    """
    ink = native.gray < _INK
    y0, y1 = native.to_native(plot.y0), native.to_native(plot.y1)
    x0, x1 = native.to_native(plot.x0), native.to_native(plot.x1)
    widths: list[int] = []
    for rule in x_rules:
        if min(abs(rule - plot.x0), abs(rule - plot.x1)) <= 3:
            continue
        c = int(round(native.to_native(rule)))
        samples = [_run_containing(ink[int(r), :], c) for r in np.linspace(y0, y1, 41)[1:-1]]
        samples = [w for w in samples if w]
        if samples:
            widths.append(int(np.percentile(samples, 25)))
    for rule in y_rules:
        if min(abs(rule - plot.y0), abs(rule - plot.y1)) <= 3:
            continue
        r = int(round(native.to_native(rule)))
        samples = [_run_containing(ink[:, int(c)], r) for c in np.linspace(x0, x1, 41)[1:-1]]
        samples = [w for w in samples if w]
        if samples:
            widths.append(int(np.percentile(samples, 25)))
    if not widths:
        raise RuntimeError("raster figure: no interior grid rule width could be measured")
    return max(widths)


def curve_mask(native: NativeRender, plot: PlotBox, widest_rule: int) -> tuple[np.ndarray, int]:
    """Ink strictly thicker than every interior rule, inside the plot frame.

    A disk of diameter ``widest_rule + 1`` fits in a stroke of any slope whose
    perpendicular width exceeds it, never in a rule.  Where two rules cross the
    disk can still fit (the inscribed disk of a plus is ~1.4 rule widths), so
    components smaller than four kernel areas are dropped; a curve piece is
    far larger.  Returns the mask and the kernel diameter.
    """
    k = widest_rule + 1
    ink = (native.gray < _INK).astype(np.uint8)
    disk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    opened = cv2.morphologyEx(ink, cv2.MORPH_OPEN, disk)
    mask = np.zeros_like(opened)
    margin = 2 * k
    x0 = int(np.ceil(native.to_native(plot.x0))) + margin
    x1 = int(np.floor(native.to_native(plot.x1))) - margin
    y0 = int(np.ceil(native.to_native(plot.y0))) + margin
    y1 = int(np.floor(native.to_native(plot.y1))) - margin
    mask[y0:y1 + 1, x0:x1 + 1] = opened[y0:y1 + 1, x0:x1 + 1]
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 4 * k * k:
            mask[labels == i] = 0
    if not mask.any():
        raise RuntimeError(
            f"raster figure: no ink thicker than the widest grid rule ({widest_rule} px); "
            "curve strokes cannot be separated from the grid by width"
        )
    return mask.astype(bool), k


def erase_boxes(mask: np.ndarray, native: NativeRender, boxes_crop, pad_native: int) -> None:
    """Clear crop-pixel boxes (e.g. OCR word boxes) from a native mask in place."""
    for x0, y0, x1, y1 in boxes_crop:
        a = max(0, int(native.to_native(x0)) - pad_native)
        b = min(mask.shape[1], int(native.to_native(x1)) + pad_native + 1)
        c = max(0, int(native.to_native(y0)) - pad_native)
        d = min(mask.shape[0], int(native.to_native(y1)) + pad_native + 1)
        mask[c:d, a:b] = False


def _runs(line: np.ndarray) -> list[tuple[int, int]]:
    padded = np.concatenate(([False], line, [False]))
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]


def stroke_width(mask: np.ndarray) -> float:
    """Median vertical run of the curve mask (its stroke width along columns)."""
    widths = [b - a for col in mask.T for a, b in _runs(col)]
    if not widths:
        raise RuntimeError("raster figure: empty curve mask")
    return float(np.median(widths))


@dataclass(frozen=True)
class SingleTrace:
    points_crop: list[tuple[float, float]]
    columns_traced: int
    columns_ambiguous: int
    columns_wide: int


def trace_single_curve(native: NativeRender, mask: np.ndarray, *, max_ambiguous_frac: float = 0.05) -> SingleTrace:
    """Centreline of the ONE curve, column by column.

    A column with two or more runs is ambiguous and not sampled; more
    ambiguous than sampled columns overall, or more than *max_ambiguous_frac*
    of the traced span ambiguous, refuses (a second curve,
    a legend line or label ink survived the width filter).  The served trace is
    the longest run of sampled columns with no jump above two stroke widths.
    """
    width = stroke_width(mask)
    samples: dict[int, float] = {}
    ambiguous = wide = 0
    cols = np.flatnonzero(mask.any(axis=0))
    for c in cols:
        runs = _runs(mask[:, c])
        if len(runs) != 1:
            ambiguous += 1
            continue
        a, b = runs[0]
        if b - a > _MAX_RUN_WIDTH_FACTOR * width * 3:
            wide += 1
            continue
        samples[int(c)] = 0.5 * (a + b - 1)
    if not samples:
        raise RuntimeError("raster figure: no column holds exactly one curve run")
    if ambiguous > len(samples):
        raise RuntimeError(
            f"raster figure: {ambiguous} of {len(cols)} inked columns hold more than one thick run; "
            "refusing to pick one"
        )
    keys = sorted(samples)
    segments: list[list[int]] = [[keys[0]]]
    for c in keys[1:]:
        prev = segments[-1][-1]
        if c - prev <= 3 * width and abs(samples[c] - samples[prev]) <= 2 * width + (c - prev):
            segments[-1].append(c)
        else:
            segments.append([c])
    best = max(segments, key=len)
    span = best[-1] - best[0] + 1
    amb_in_span = sum(1 for c in cols if best[0] <= c <= best[-1] and int(c) not in samples)
    if amb_in_span > max_ambiguous_frac * span:
        raise RuntimeError(
            f"raster figure: {amb_in_span} of {span} columns in the curve span hold "
            "more than one thick run; refusing to pick one"
        )
    if len(best) < 0.8 * len(keys):
        raise RuntimeError(
            "raster figure: thick ink is split into separate pieces "
            f"({len(best)} of {len(keys)} sampled columns connected)"
        )
    points = [native.to_crop(float(c), samples[c]) for c in best]
    return SingleTrace(points, len(best), ambiguous, wide)


@dataclass(frozen=True)
class RowTraces:
    curves_crop: list[list[tuple[float, float]]]  # left to right, each sorted by row
    rows_traced: int
    rows_skipped: int
    stroke_px: float


def trace_curves_by_rows(native: NativeRender, mask: np.ndarray, count: int, *, min_rows_frac: float = 0.25) -> RowTraces:
    """Centrelines of *count* steep curves, row by row, left to right.

    A row is sampled only when it holds exactly *count* runs, each no wider than
    ``_MAX_RUN_WIDTH_FACTOR`` stroke widths (merges, arrowheads and near-flat
    stretches are skipped, never split).  Each curve's samples must move
    continuously between sampled rows, else the chart refuses: an identity swap
    between rows cannot be ruled out.
    """
    rows = np.flatnonzero(mask.any(axis=1))
    if len(rows) == 0:
        raise RuntimeError("raster figure: empty curve mask")
    # horizontal run width of a steep stroke
    widths = [b - a for r in rows for a, b in _runs(mask[r]) ]
    width = float(np.median(widths))
    sampled: list[tuple[int, list[float]]] = []
    skipped = 0
    for r in rows:
        runs = _runs(mask[r])
        if len(runs) != count or any(b - a > _MAX_RUN_WIDTH_FACTOR * width for a, b in runs):
            skipped += 1
            continue
        sampled.append((int(r), [0.5 * (a + b - 1) for a, b in runs]))
    if len(sampled) < min_rows_frac * len(rows):
        raise RuntimeError(
            f"raster figure: only {len(sampled)} of {len(rows)} curve rows hold exactly "
            f"{count} separated runs"
        )
    for (r0, xs0), (r1, xs1) in zip(sampled, sampled[1:]):
        limit = 2 * width + 3 * (r1 - r0)
        if any(abs(a - b) > limit for a, b in zip(xs0, xs1)):
            # A gap between sampled rows is fine only if every curve stays put.
            raise RuntimeError(
                f"raster figure: a curve jumps between rows {r0} and {r1}; "
                "row-to-row identity is not proven"
            )
    curves = [
        [native.to_crop(xs[i], float(r)) for r, xs in sampled]
        for i in range(count)
    ]
    return RowTraces(curves, len(sampled), skipped, width)


def ocr_plot_words(chart: dict, transform, plot: PlotBox) -> list[tuple[str, tuple[float, float, float, float]]]:
    """OCR words inside the plot frame, as (text, crop-px box)."""
    from .region_ocr import ocr_words_in_rect

    p0 = transform.to_pt(plot.x0, plot.y0)
    p1 = transform.to_pt(plot.x1, plot.y1)
    words = ocr_words_in_rect(chart["pdf"], int(chart["page"]), (p0[0], p0[1], p1[0], p1[1]), dpi=400.0, psm=11)
    out = []
    for x0, y0, x1, y1, text in words:
        a = transform.to_px(x0, y0)
        b = transform.to_px(x1, y1)
        out.append((text, (a[0], a[1], b[0], b[1])))
    return out


@dataclass(frozen=True)
class RasterSingleCurve:
    x_axis: object  # linear_tick_axis.LinearAxis, label-centre fit (caller snaps)
    y_axis: object
    points_crop: list[tuple[float, float]]
    diagnostics: dict


def single_curve_raster_figure(
    chart: dict, page, transform, crop_shape, plot: PlotBox,
    x_grid: list[float], y_grid: list[float], fitz, x_name: str, y_name: str,
) -> RasterSingleCurve | None:
    """OCR axes and trace the one curve of a whole-figure raster panel.

    Returns None when no embedded image covers the plot (not this stratum);
    raises on every refusal inside it.
    """
    from .raster_tick_ocr import raster_linear_axes

    p0 = transform.to_pt(plot.x0, plot.y0)
    p1 = transform.to_pt(plot.x1, plot.y1)
    native = render_native(page, chart["crop_box_pt"], crop_shape, (p0[0], p0[1], p1[0], p1[1]), fitz)
    if native is None:
        return None
    x_axis, y_axis = raster_linear_axes(native, plot, x_grid, y_grid, x_name, y_name)
    widest = rule_width(native, plot, x_grid, y_grid)
    mask, kernel = curve_mask(native, plot, widest)
    trace = trace_single_curve(native, mask)
    return RasterSingleCurve(
        x_axis, y_axis, trace.points_crop,
        {
            "native_dpi": round(native.dpi, 1),
            "native_scale": round(native.scale, 4),
            "widest_rule_px": widest,
            "opening_kernel_px": kernel,
            "curve_stroke_px": stroke_width(mask),
            "columns_traced": trace.columns_traced,
            "columns_ambiguous": trace.columns_ambiguous,
        },
    )
