"""Axis calibration helpers for MOSFET capacitance charts."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from .axis_calibration import _number_tokens, _x_ticks_look_log, calibrate_axes

from .capacitance_traces import _interp_y
from .capacitance_types import AxisCalibration, GridlineFit, PlotBox, Trace
from .capacitance_vector import _load_fitz
from .crop_transform import CropTransform

def infer_text_order_axis_calibration(chart: dict[str, object]) -> AxisCalibration:
    text = str(chart.get("text") or "")
    x_ticks, x_start_index = _parse_x_ticks_from_chart_text(text)
    y_decades = _parse_y_decades_from_chart_text(text, x_start_index)
    if len(x_ticks) < 2:
        raise RuntimeError("could not infer x-axis ticks from chart text")
    if len(y_decades) < 2:
        raise RuntimeError("could not infer y-axis decades from chart text")
    return AxisCalibration(
        x_min_v=float(x_ticks[0]),
        x_max_v=float(x_ticks[-1]),
        y_min_decade=float(min(y_decades)),
        y_max_decade=float(max(y_decades)),
        source="chart_text",
        x_ticks_v=tuple(float(v) for v in x_ticks),
        y_decades=tuple(float(v) for v in sorted(set(y_decades))),
        # chart_text is never trusted, but the normalized v_of_x branch honors
        # x_log -- keep the untrusted debug output on the right scale too.
        x_log=bool(_x_ticks_look_log is not None and _x_ticks_look_log([float(v) for v in x_ticks])),
        x_source="text_order_normalized_plot_extent",
        y_source="text_order_normalized_plot_extent",
    )


def _plot_rect_pt(chart: dict[str, object], image: np.ndarray, plot: PlotBox):
    """Detected plot frame in page-pt coordinates (as a fitz.Rect)."""
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")
    transform = CropTransform.for_chart(chart, image.shape)
    plot_x0, plot_y0 = transform.to_pt(plot.x0, plot.y0)
    plot_x1, plot_y1 = transform.to_pt(plot.x1, plot.y1)
    return transform, fitz.Rect(plot_x0, plot_y0, plot_x1, plot_y1)


def _fit_position_calibration(page_like, transform: CropTransform, plot_rect, source: str) -> AxisCalibration:
    """Position-fit tick labels from any words source (PDF text or OCR).

    `page_like` only needs `get_text("words")` returning (x0, y0, x1, y1, text)
    tuples in page-pt coordinates -- a real PyMuPDF page or an OCR adapter.
    """
    if calibrate_axes is None:
        raise RuntimeError("axis_calibration.calibrate_axes is not available")
    pos_cal = calibrate_axes(
        page_like,
        x_row_band=(plot_rect.y1 + 2.0, plot_rect.y1 + 24.0),
        y_label_x_band=(plot_rect.x0 - 42.0, plot_rect.x0 - 1.0),
        plot_y_band=(plot_rect.y0 - 8.0, plot_rect.y1 + 8.0),
        # Two-charts-per-row pages (TI) put both tick rows in the same y band;
        # keep only labels under THIS plot. The margin must still admit our own
        # origin '0' label (a few pt left of the frame) while excluding the
        # neighbor chart's rightmost tick (tens of pt away).
        x_col_band=(plot_rect.x0 - 24.0, plot_rect.x1 + 12.0),
    )

    # Convert page-coordinate fits to crop-pixel-coordinate fits, because trace
    # points are stored in crop pixels.
    x_scale = float(pos_cal.mx) / transform.scale_x
    x_offset = float(pos_cal.mx) * transform.x0_pt + float(pos_cal.bx)
    y_scale = float(pos_cal.my) / transform.scale_y
    y_offset = float(pos_cal.my) * transform.y0_pt + float(pos_cal.by)
    x_ticks = tuple(float(v) for v, _ in pos_cal.x_ticks)
    y_decades = tuple(float(e) for e, _ in pos_cal.y_decades)
    return AxisCalibration(
        x_min_v=min(x_ticks),
        x_max_v=max(x_ticks),
        y_min_decade=min(y_decades),
        y_max_decade=max(y_decades),
        source=source,
        x_ticks_v=x_ticks,
        y_decades=tuple(sorted(set(y_decades))),
        x_log=bool(getattr(pos_cal, "x_log", False)),
        x_resid_v=float(pos_cal.x_resid),
        y_resid_dec=float(pos_cal.y_resid),
        x_scale=x_scale,
        x_offset=x_offset,
        y_scale=y_scale,
        y_offset=y_offset,
        x_source=source,
        y_source=source,
    )


def infer_position_axis_calibration(
    chart: dict[str, object], image: np.ndarray, plot: PlotBox
) -> AxisCalibration:
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")
    transform, plot_rect = _plot_rect_pt(chart, image, plot)
    doc = fitz.open(Path(str(chart["pdf"])))
    page = doc[int(chart["page"]) - 1]
    return _fit_position_calibration(page, transform, plot_rect, "position_text")


class _OcrWordsPage:
    """Duck-typed stand-in for a PyMuPDF page backed by OCR word boxes."""

    def __init__(self, words: list[tuple[float, float, float, float, str]]):
        self._words = words

    def get_text(self, kind: str):
        return list(self._words)


def _normalize_ocr_token(text: str) -> str:
    # Tesseract reads decimal points as commas on some rasters; tick parsing
    # downstream expects '.' and bare digits.
    return text.strip().strip("|:;").replace(",", ".")


def _ocr_words_in_rect(
    chart: dict[str, object], clip_rect, dpi: float = 400.0
) -> list[tuple[float, float, float, float, str]]:
    """OCR a page region with tesseract; word boxes returned in page pt."""
    exe = shutil.which("tesseract")
    if exe is None:
        raise RuntimeError("tesseract binary not found; cannot OCR raster axis labels")
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")
    doc = fitz.open(Path(str(chart["pdf"])))
    page = doc[int(chart["page"]) - 1]
    clip = fitz.Rect(clip_rect) & page.rect
    if clip.is_empty:
        raise RuntimeError("OCR clip rect is empty")
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "ocr-region.png"
        pix.save(str(png))
        # --psm 11 (sparse text): tick labels are isolated words, not a block.
        proc = subprocess.run(
            [exe, str(png), "stdout", "--psm", "11", "tsv"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract failed: {proc.stderr.strip()[:200]}")
    lines = proc.stdout.splitlines()
    if not lines:
        raise RuntimeError("tesseract returned no TSV output")
    header = lines[0].split("\t")
    col = {name: i for i, name in enumerate(header)}
    words: list[tuple[float, float, float, float, str]] = []
    for row in lines[1:]:
        cells = row.split("\t")
        if len(cells) != len(header):
            continue
        text = _normalize_ocr_token(cells[col["text"]])
        try:
            conf = float(cells[col["conf"]])
        except (KeyError, ValueError):
            continue
        if not text or conf < 30.0:
            continue
        x0 = clip.x0 + float(cells[col["left"]]) / scale
        y0 = clip.y0 + float(cells[col["top"]]) / scale
        x1 = x0 + float(cells[col["width"]]) / scale
        y1 = y0 + float(cells[col["height"]]) / scale
        words.append((x0, y0, x1, y1, text))
    return words


def infer_ocr_position_axis_calibration(
    chart: dict[str, object], image: np.ndarray, plot: PlotBox
) -> AxisCalibration:
    """Position calibration for raster-image charts with no PDF text.

    Some vendors (Toshiba) embed the whole figure -- gridlines, traces AND
    tick labels -- as one raster image; `page.get_text("words")` is empty over
    the chart, so `infer_position_axis_calibration` cannot fit. Here the label
    bands are OCRed (tesseract) into page-pt word boxes and fed through the
    same position fit; the shared residual gates then decide trust.
    """
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")
    transform, plot_rect = _plot_rect_pt(chart, image, plot)
    # Cover the label bands used by _fit_position_calibration (left decade
    # column and the tick row under the frame), with margin.
    clip = fitz.Rect(
        plot_rect.x0 - 60.0,
        plot_rect.y0 - 12.0,
        plot_rect.x1 + 16.0,
        plot_rect.y1 + 30.0,
    )
    words = _ocr_words_in_rect(chart, clip)
    if not words:
        raise RuntimeError("OCR found no words in the axis label bands")
    return _fit_position_calibration(_OcrWordsPage(words), transform, plot_rect, "position_ocr")


def reject_bad_position_calibration(calibration: AxisCalibration) -> str | None:
    if calibration.x_log:
        # Log-X fits carry their residual in decades, like the Y axis.
        if calibration.x_resid_v is not None and calibration.x_resid_v > 0.05:
            return f"position x residual {calibration.x_resid_v:.4g} decades exceeds 0.05"
        if calibration.y_resid_dec is not None and calibration.y_resid_dec > 0.05:
            return f"position y residual {calibration.y_resid_dec:.4g} decades exceeds 0.05"
        return None
    x_span = abs(calibration.x_max_v - calibration.x_min_v)
    max_x_resid = max(0.5, 0.02 * x_span)
    if calibration.x_resid_v is not None and calibration.x_resid_v > max_x_resid:
        return f"position x residual {calibration.x_resid_v:.4g} V exceeds {max_x_resid:.4g} V"
    if calibration.y_resid_dec is not None and calibration.y_resid_dec > 0.05:
        return f"position y residual {calibration.y_resid_dec:.4g} decades exceeds 0.05"
    return None


def infer_gridline_axis_calibration(chart: dict[str, object], image: np.ndarray, plot: PlotBox) -> AxisCalibration:
    text_calibration = infer_text_order_axis_calibration(chart)
    # This tier maps px->V LINEARLY between the extreme tick values and is
    # reported as trusted. On a log X axis that mapping is silently, severely
    # wrong (mid-plot reads ~16x high on a 0.1-100 V axis) -- refuse instead.
    if _x_ticks_look_log is not None and _x_ticks_look_log(list(text_calibration.x_ticks_v)):
        raise RuntimeError("log-spaced X ticks: grid-tier linear X mapping would mis-scale; refusing")
    y_fit = _major_horizontal_gridline_fit(image, plot, len(text_calibration.y_decades))
    y_positions = y_fit.centers
    if len(y_positions) != len(text_calibration.y_decades):
        raise RuntimeError("could not match Y decade labels to horizontal gridlines")

    y_values = np.array(sorted(text_calibration.y_decades, reverse=True), dtype=float)
    y_pixels = np.array(sorted(y_positions), dtype=float)
    y_scale, y_offset = np.polyfit(y_pixels, y_values, 1)
    y_resid = float(np.sqrt(np.mean((y_scale * y_pixels + y_offset - y_values) ** 2)))
    if y_resid > 0.05:
        raise RuntimeError(f"Y gridline fit residual {y_resid:.4g} decades exceeds 0.05")

    x_scale = (text_calibration.x_max_v - text_calibration.x_min_v) / max(1, plot.x1 - plot.x0)
    x_offset = text_calibration.x_min_v - x_scale * plot.x0
    return AxisCalibration(
        x_min_v=text_calibration.x_min_v,
        x_max_v=text_calibration.x_max_v,
        y_min_decade=text_calibration.y_min_decade,
        y_max_decade=text_calibration.y_max_decade,
        source="grid_text",
        x_ticks_v=text_calibration.x_ticks_v,
        y_decades=text_calibration.y_decades,
        x_resid_v=None,
        y_resid_dec=y_resid,
        x_scale=float(x_scale),
        x_offset=float(x_offset),
        y_scale=float(y_scale),
        y_offset=float(y_offset),
        x_source="plot_box_endpoints_from_text_ticks",
        y_source="gridline_fit_from_text_decades",
        y_gridline_px=tuple(float(y) for y in y_positions),
        y_grid_candidate_count=y_fit.candidate_count,
        y_grid_span_fraction=y_fit.span_fraction,
        y_grid_residual_px=y_fit.residual_px,
    )


def _major_horizontal_gridline_centers(image: np.ndarray, plot: PlotBox, count: int) -> list[float]:
    return _major_horizontal_gridline_fit(image, plot, count).centers


def _major_horizontal_gridline_fit(image: np.ndarray, plot: PlotBox, count: int) -> GridlineFit:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    _, bw = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(80, gray.shape[1] // 5), 1))
    hlines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(hlines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    by_y: list[tuple[float, list[tuple[int, int]]]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < plot.width * 0.20 or h > 8:
            continue
        center_y = y + h / 2.0
        if not (plot.y0 - 4 <= center_y <= plot.y1 + 4):
            continue
        start = max(plot.x0, x)
        end = min(plot.x1, x + w - 1)
        if end <= start:
            continue
        for idx, (existing_y, intervals) in enumerate(by_y):
            if abs(center_y - existing_y) <= 3.0:
                intervals.append((start, end))
                ys = [existing_y] * (len(intervals) - 1) + [center_y]
                by_y[idx] = (float(np.median(ys)), intervals)
                break
        else:
            by_y.append((center_y, [(start, end)]))

    candidates: list[float] = []
    for center_y, intervals in by_y:
        if _interval_coverage_fraction(intervals, plot.x0, plot.x1) >= 0.65:
            candidates.append(center_y)
    candidates = sorted(candidates)
    if len(candidates) < count:
        raise RuntimeError(f"found only {len(candidates)} horizontal gridline candidates")

    best: tuple[float, float, float, list[float]] | None = None
    best_rejected: tuple[float, float, float, list[float]] | None = None
    for first_idx in range(len(candidates)):
        for last_idx in range(first_idx + count - 1, len(candidates)):
            first = candidates[first_idx]
            last = candidates[last_idx]
            span = last - first
            if span < plot.height * 0.94:
                continue
            expected = np.linspace(first, last, count)
            chosen = []
            used: set[int] = set()
            for target in expected:
                idx = min(
                    (i for i in range(len(candidates)) if i not in used),
                    key=lambda i: abs(candidates[i] - target),
                )
                chosen.append(candidates[idx])
                used.add(idx)
            residual = float(np.sqrt(np.mean((np.array(chosen) - expected) ** 2)))
            # A log-axis decade set should cover the whole plotted axis. Dense
            # minor log gridlines can form many very uniform but shifted
            # sequences; choosing by residual alone can pick an internal
            # sequence and mis-scale every capacitance. Prefer full-height
            # sequences first, then use residual as the tie-breaker.
            score = -span + residual * 0.05
            candidate = (score, residual, span, chosen)
            if residual <= 3.0:
                if best is None or score < best[0]:
                    best = candidate
            elif best_rejected is None or score < best_rejected[0]:
                best_rejected = candidate

    if best is None:
        residual = best_rejected[1] if best_rejected is not None else float("nan")
        raise RuntimeError(f"could not find a uniform major-grid sequence; residual {residual:.4g}")
    return GridlineFit(
        centers=sorted(best[3]),
        candidate_count=len(candidates),
        span_fraction=float(best[2] / max(1, plot.height - 1)),
        residual_px=float(best[1]),
    )


def _interval_coverage_fraction(intervals: list[tuple[int, int]], start: int, end: int) -> float:
    if not intervals:
        return 0.0
    clipped = sorted((max(start, lo), min(end, hi)) for lo, hi in intervals if hi >= start and lo <= end)
    if not clipped:
        return 0.0
    total = 0
    cur_lo, cur_hi = clipped[0]
    for lo, hi in clipped[1:]:
        if lo <= cur_hi + 1:
            cur_hi = max(cur_hi, hi)
        else:
            total += cur_hi - cur_lo + 1
            cur_lo, cur_hi = lo, hi
    total += cur_hi - cur_lo + 1
    return total / max(1, end - start + 1)


def _parse_x_ticks_from_chart_text(text: str) -> tuple[list[float], int]:
    prefix = re.split(r"\bV\s*\[\s*V\s*\]", text, maxsplit=1)[0]
    tokens = _number_tokens(prefix)
    best: tuple[list[float], int] = ([], -1)
    for idx, value in enumerate(tokens):
        if abs(value) > 1e-9:
            continue
        run = [value]
        last = value
        for candidate in tokens[idx + 1 :]:
            if candidate <= last:
                break
            run.append(candidate)
            last = candidate
        if len(run) >= 3 and _is_uniform_tick_run(run):
            if len(run) > len(best[0]) or (len(run) == len(best[0]) and idx > best[1]):
                best = (run, idx)
    return best


def _is_uniform_tick_run(values: list[float]) -> bool:
    if len(values) < 3:
        return False
    diffs = np.diff(np.asarray(values, dtype=float))
    if np.any(diffs <= 0):
        return False
    return float(np.std(diffs)) <= max(0.05, float(np.median(diffs)) * 0.15)


def _parse_y_decades_from_chart_text(text: str, x_start_index: int) -> list[float]:
    prefix = re.split(r"\bV\s*\[\s*V\s*\]", text, maxsplit=1)[0]
    tokens = _number_tokens(prefix)
    if x_start_index > 0:
        tokens = tokens[:x_start_index]

    decades: list[float] = []
    for a, b in zip(tokens, tokens[1:]):
        if _is_power_ten_exponent(a) and abs(b - 10.0) < 1e-9:
            decades.append(a)
        elif abs(a - 10.0) < 1e-9 and _is_power_ten_exponent(b):
            decades.append(b)

    # Preserve the useful values and discard duplicate pair hits from adjacent
    # labels such as "10 4 10 3".
    out: list[float] = []
    for value in decades:
        if not out or abs(value - out[-1]) > 1e-9:
            out.append(value)
    return out


def _is_power_ten_exponent(value: float) -> bool:
    return 0.0 <= value <= 6.0 and abs(value - round(value)) < 1e-9


def trace_data_points(
    trace: Trace, plot: PlotBox, calibration: AxisCalibration
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for x, y in trace.points:
        vds = calibration_v_of_x(calibration, plot, x)
        log_c = calibration_log_c_of_y(calibration, plot, y)
        out.append((float(vds), float(10.0**log_c)))
    return out


def calibration_v_of_x(calibration: AxisCalibration, plot: PlotBox, x: float) -> float:
    if calibration.x_scale is not None and calibration.x_offset is not None:
        fitted = float(calibration.x_scale * x + calibration.x_offset)
        return float(10.0 ** fitted) if calibration.x_log else fitted
    x_norm = _clip01((x - plot.x0) / max(1, plot.width - 1))
    if calibration.x_log:
        lo, hi = math.log10(calibration.x_min_v), math.log10(calibration.x_max_v)
        return float(10.0 ** (lo + x_norm * (hi - lo)))
    return float(calibration.x_min_v + x_norm * (calibration.x_max_v - calibration.x_min_v))


def calibration_x_of_v(calibration: AxisCalibration, plot: PlotBox, vds: float) -> float:
    if calibration.x_scale is not None and calibration.x_offset is not None and abs(calibration.x_scale) > 1e-12:
        value = math.log10(vds) if calibration.x_log and vds > 0.0 else vds
        return float((value - calibration.x_offset) / calibration.x_scale)
    if calibration.x_log:
        lo, hi = math.log10(calibration.x_min_v), math.log10(calibration.x_max_v)
        x_norm = (math.log10(max(vds, 1e-12)) - lo) / max(1e-12, hi - lo)
    else:
        x_norm = (vds - calibration.x_min_v) / max(1e-12, calibration.x_max_v - calibration.x_min_v)
    return float(plot.x0 + _clip01(x_norm) * max(1, plot.width - 1))


def calibration_log_c_of_y(calibration: AxisCalibration, plot: PlotBox, y: float) -> float:
    if calibration.y_scale is not None and calibration.y_offset is not None:
        return float(calibration.y_scale * y + calibration.y_offset)
    y_norm = _clip01((plot.y1 - y) / max(1, plot.height - 1))
    return float(calibration.y_min_decade + y_norm * (calibration.y_max_decade - calibration.y_min_decade))


def calibration_y_of_log_c(calibration: AxisCalibration, plot: PlotBox, log_c: float) -> float:
    if calibration.y_scale is not None and calibration.y_offset is not None and abs(calibration.y_scale) > 1e-12:
        return float((log_c - calibration.y_offset) / calibration.y_scale)
    y_norm = (log_c - calibration.y_min_decade) / max(1e-12, calibration.y_max_decade - calibration.y_min_decade)
    return float(plot.y1 - _clip01(y_norm) * max(1, plot.height - 1))


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def arrays_for_trace_data(data_points: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    by_v: dict[float, list[float]] = {}
    for vds, cap in data_points:
        by_v.setdefault(float(vds), []).append(float(cap))
    vds = np.array(sorted(by_v), dtype=float)
    cap = np.array([float(np.median(by_v[v])) for v in vds], dtype=float)
    return vds, cap


def axis_calibration_to_json(calibration: AxisCalibration) -> dict[str, object]:
    return {
        "source": calibration.source,
        "x_source": calibration.x_source,
        "y_source": calibration.y_source,
        "x_min_v": calibration.x_min_v,
        "x_max_v": calibration.x_max_v,
        "y_min_decade": calibration.y_min_decade,
        "y_max_decade": calibration.y_max_decade,
        "x_ticks_v": list(calibration.x_ticks_v),
        "y_decades": list(calibration.y_decades),
        "x_log": calibration.x_log,
        "x_resid_v": calibration.x_resid_v,
        "y_resid_dec": calibration.y_resid_dec,
        "x_scale": calibration.x_scale,
        "x_offset": calibration.x_offset,
        "y_scale": calibration.y_scale,
        "y_offset": calibration.y_offset,
        "y_gridline_px": list(calibration.y_gridline_px),
        "y_grid_candidate_count": calibration.y_grid_candidate_count,
        "y_grid_span_fraction": calibration.y_grid_span_fraction,
        "y_grid_residual_px": calibration.y_grid_residual_px,
    }


def calibration_delta_to_json(
    primary: AxisCalibration | None, baseline: AxisCalibration | None, plot: PlotBox
) -> dict[str, float] | None:
    if primary is None or baseline is None:
        return None
    return {
        "left_v_delta": calibration_v_of_x(primary, plot, plot.x0) - calibration_v_of_x(baseline, plot, plot.x0),
        "right_v_delta": calibration_v_of_x(primary, plot, plot.x1) - calibration_v_of_x(baseline, plot, plot.x1),
        "top_dec_delta": calibration_log_c_of_y(primary, plot, plot.y0)
        - calibration_log_c_of_y(baseline, plot, plot.y0),
        "bottom_dec_delta": calibration_log_c_of_y(primary, plot, plot.y1)
        - calibration_log_c_of_y(baseline, plot, plot.y1),
    }


def axis_calibration_is_trusted(calibration: AxisCalibration | None) -> bool:
    if calibration is None:
        return False
    if calibration.source == "chart_text":
        return False
    return calibration.x_scale is not None and calibration.y_scale is not None
