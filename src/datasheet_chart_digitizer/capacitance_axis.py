"""Axis calibration helpers for MOSFET capacitance charts."""

from __future__ import annotations

import math
import re
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from .axis_calibration import _number_tokens, _x_ticks_look_log, calibrate_axes

from .capacitance_traces import _interp_y
from .capacitance_types import AxisCalibration, GridlineFit, PlotBox, Trace
from .capacitance_vector import _load_fitz
from .crop_transform import CropTransform
from .numeric_axis import AxisTick, fit_axis_ticks
from .region_ocr import ocr_words_in_rect

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
    x_tick_label_px = tuple(
        float(transform.to_px(pixel, plot_rect.y1)[0])
        for _value, pixel in pos_cal.x_ticks
    )
    y_log = bool(getattr(pos_cal, "y_log", True))
    y_coordinates = tuple(float(e) for e, _ in pos_cal.y_decades)
    if y_log:
        y_ticks_pf: tuple[float, ...] = ()
        y_decades = y_coordinates
        y_min_decade = min(y_decades)
        y_max_decade = max(y_decades)
        y_resid_dec: float | None = float(pos_cal.y_resid)
        y_resid_pf: float | None = None
    else:
        y_ticks_pf = tuple(sorted(set(y_coordinates)))
        y_tick_label_px = tuple(
            float(transform.to_px(plot_rect.x0, pixel)[1])
            for _value, pixel in pos_cal.y_decades
        )
        positive_ticks = [value for value in y_ticks_pf if value > 0.0]
        if len(positive_ticks) < 3:
            raise RuntimeError("linear Y calibration needs >=3 positive capacitance ticks")
        top_pf = float(pos_cal.my * plot_rect.y0 + pos_cal.by)
        bottom_pf = float(pos_cal.my * plot_rect.y1 + pos_cal.by)
        frame_positive = [value for value in (top_pf, bottom_pf) if value > 0.0]
        y_min_decade = math.log10(min(positive_ticks))
        y_max_decade = math.log10(max(positive_ticks + frame_positive))
        y_decades = tuple(math.log10(value) for value in positive_ticks)
        y_resid_dec = None
        y_resid_pf = float(pos_cal.y_resid)
    if y_log:
        y_tick_label_px = ()
    return AxisCalibration(
        x_min_v=min(x_ticks),
        x_max_v=max(x_ticks),
        y_min_decade=y_min_decade,
        y_max_decade=y_max_decade,
        source=source,
        x_ticks_v=x_ticks,
        y_decades=tuple(sorted(set(y_decades))),
        x_log=bool(getattr(pos_cal, "x_log", False)),
        y_log=y_log,
        y_ticks_pf=y_ticks_pf,
        x_resid_v=float(pos_cal.x_resid),
        y_resid_dec=y_resid_dec,
        y_resid_pf=y_resid_pf,
        y_tick_label_px=y_tick_label_px,
        x_scale=x_scale,
        x_offset=x_offset,
        y_scale=y_scale,
        y_offset=y_offset,
        x_source=source,
        y_source=source,
        x_source_ticks_v=tuple(
            float(value) for value in getattr(pos_cal, "x_source_ticks", ())
        ),
        x_value_transform=getattr(pos_cal, "x_value_transform", None),
        x_tick_label_px=x_tick_label_px,
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
    calibration = _fit_position_calibration(page, transform, plot_rect, "position_text")
    return _seat_linear_y_ticks_on_grid(
        calibration, image, plot, page=page, transform=transform
    )


class _OcrWordsPage:
    """Duck-typed stand-in for a PyMuPDF page backed by OCR word boxes."""

    def __init__(self, words: list[tuple[float, float, float, float, str]]):
        self._words = words

    def get_text(self, kind: str):
        return list(self._words)


def _ocr_words_in_rect(
    chart: dict[str, object], clip_rect, dpi: float = 400.0
) -> list[tuple[float, float, float, float, str]]:
    return ocr_words_in_rect(
        str(chart["pdf"]), int(chart["page"]), clip_rect, dpi=dpi, psm=11
    )


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
    calibration = _fit_position_calibration(
        _OcrWordsPage(words), transform, plot_rect, "position_ocr"
    )
    doc = fitz.open(Path(str(chart["pdf"])))
    page = doc[int(chart["page"]) - 1]
    calibration = _seat_linear_y_ticks_on_grid(
        calibration, image, plot, page=page, transform=transform
    )
    return _seat_signed_log_x_ticks_on_grid(calibration, image, plot)


def _endpoint_tick_coverage_error(
    tick_pixels: list[float], start: float, end: float, axis_name: str
) -> str | None:
    """Reject fits that serve multiple unseen intervals beyond labeled ticks."""

    pixels = sorted(set(float(pixel) for pixel in tick_pixels))
    if len(pixels) < 2:
        return f"{axis_name} endpoint coverage needs >=2 distinct tick centers"
    left_step = pixels[1] - pixels[0]
    right_step = pixels[-1] - pixels[-2]
    if left_step <= 0 or right_step <= 0:
        return f"{axis_name} tick centers are not strictly increasing"
    endpoint_intervals = (
        max(0.0, (pixels[0] - start) / left_step),
        max(0.0, (end - pixels[-1]) / right_step),
    )
    side, unseen = max(
        (("left", endpoint_intervals[0]), ("right", endpoint_intervals[1])),
        key=lambda item: item[1],
    )
    if unseen > 1.25:
        return (
            f"{axis_name} {side} endpoint leaves {unseen:.2f} unlabeled "
            "tick intervals; maximum is one"
        )
    return None


def reject_bad_position_calibration(
    calibration: AxisCalibration, plot: PlotBox | None = None
) -> str | None:
    if calibration.x_log:
        # Log-X fits carry their residual in decades, like the Y axis.
        if calibration.x_resid_v is not None and calibration.x_resid_v > 0.05:
            return f"position x residual {calibration.x_resid_v:.4g} decades exceeds 0.05"
        residual_error = None
    else:
        x_span = abs(calibration.x_max_v - calibration.x_min_v)
        max_x_resid = max(0.5, 0.02 * x_span)
        residual_error = None
        if calibration.x_resid_v is not None and calibration.x_resid_v > max_x_resid:
            residual_error = (
                f"position x residual {calibration.x_resid_v:.4g} V "
                f"exceeds {max_x_resid:.4g} V"
            )
    if residual_error is None:
        if calibration.y_log:
            if calibration.y_resid_dec is not None and calibration.y_resid_dec > 0.05:
                residual_error = (
                    f"position y residual {calibration.y_resid_dec:.4g} decades "
                    "exceeds 0.05"
                )
        else:
            y_span_pf = max(calibration.y_ticks_pf, default=0.0) - min(
                calibration.y_ticks_pf, default=0.0
            )
            max_y_resid_pf = max(1e-6, 0.02 * y_span_pf)
            if (
                calibration.y_resid_pf is None
                or calibration.y_resid_pf > max_y_resid_pf
            ):
                value = calibration.y_resid_pf
                rendered = "missing" if value is None else f"{value:.4g} pF"
                residual_error = (
                    f"position linear-y residual {rendered} exceeds "
                    f"{max_y_resid_pf:.4g} pF"
                )
    if residual_error is not None or plot is None:
        return residual_error

    if (
        calibration.x_scale is None
        or calibration.x_offset is None
        or calibration.y_scale is None
        or calibration.y_offset is None
        or calibration.x_scale == 0
        or calibration.y_scale == 0
    ):
        return "position calibration lacks invertible axis coefficients"
    x_values = [
        math.log10(value) if calibration.x_log else value
        for value in calibration.x_ticks_v
        if not calibration.x_log or value > 0
    ]
    x_pixels = [
        (value - calibration.x_offset) / calibration.x_scale
        for value in x_values
    ]
    x_coverage_error = _endpoint_tick_coverage_error(
        x_pixels, plot.x0, plot.x1, "X axis"
    )
    if x_coverage_error is not None:
        return x_coverage_error
    if calibration.y_log:
        y_pixels = [
            (value - calibration.y_offset) / calibration.y_scale
            for value in calibration.y_decades
        ]
    else:
        y_pixels = [
            (value - calibration.y_offset) / calibration.y_scale
            for value in calibration.y_ticks_pf
        ]
    return _endpoint_tick_coverage_error(y_pixels, plot.y0, plot.y1, "Y axis")


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


def _horizontal_gridline_candidates(image: np.ndarray, plot: PlotBox) -> list[float]:
    """Return source horizontal-line centers crossing most of the plot width."""

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
    return sorted(candidates)


def _vertical_gridline_candidates(image: np.ndarray, plot: PlotBox) -> list[float]:
    """Return source vertical-line centers by reusing the horizontal detector."""

    transposed = np.transpose(image, (1, 0, 2)) if image.ndim == 3 else image.T
    transposed_plot = PlotBox(plot.y0, plot.x0, plot.y1, plot.x1)
    return _horizontal_gridline_candidates(transposed, transposed_plot)


_SIGNED_LOG_X_LABEL_GRID_MAX_PX = 6.0
_SIGNED_LOG_X_FIT_GRID_MAX_PX = 1.0


def _seat_signed_log_x_ticks_on_grid(
    calibration: AxisCalibration, image: np.ndarray, plot: PlotBox
) -> AxisCalibration:
    """Refit signed raster VDS magnitudes at observed source grid centers.

    Toshiba P-channel figures print negative VDS ticks whose OCR glyph centers
    sit several pixels right of the vertical source rails.  OCR establishes
    tick identity; serving uses the unique nearby rail, with the signed source
    values and absolute-value transform retained explicitly for review.
    """

    if calibration.x_value_transform != "abs_source_negative_vds":
        return calibration
    values = calibration.x_ticks_v
    source_values = calibration.x_source_ticks_v
    label_pixels = calibration.x_tick_label_px
    if not calibration.x_log:
        raise RuntimeError("signed VDS magnitude grid seating requires a log X axis")
    if len(values) < 3 or len(label_pixels) != len(values):
        raise RuntimeError("signed log X grid seating lacks one label center per tick")
    if len(source_values) != len(values) or not all(value < 0.0 for value in source_values):
        raise RuntimeError("signed log X grid seating lacks all-negative source ticks")
    log_values = np.log10(np.asarray(values, dtype=float))
    log_steps = np.diff(log_values)
    if np.any(log_steps <= 0.0) or np.max(np.abs(log_steps - np.median(log_steps))) > 0.05:
        raise RuntimeError("signed log X ticks do not form a regular logarithmic ladder")

    candidates = _vertical_gridline_candidates(image, plot)
    assignments: list[tuple[float, float, float]] = []
    used: set[float] = set()
    for value, label_pixel in zip(values, label_pixels):
        owned = [
            pixel
            for pixel in candidates
            if pixel not in used
            and abs(pixel - label_pixel) <= _SIGNED_LOG_X_LABEL_GRID_MAX_PX
        ]
        if len(owned) != 1:
            raise RuntimeError(
                "signed log X tick does not own exactly one source gridline within "
                f"{_SIGNED_LOG_X_LABEL_GRID_MAX_PX:g} px"
            )
        grid_pixel = owned[0]
        used.add(grid_pixel)
        assignments.append((value, label_pixel, grid_pixel))

    grid_pixels = np.asarray([item[2] for item in assignments], dtype=float)
    if np.any(np.diff(grid_pixels) <= 0.0):
        raise RuntimeError("signed log X source grid centers do not follow tick-value order")
    axis = fit_axis_ticks(
        [
            AxisTick(f"{value:g}", value, grid_pixel)
            for value, _label_pixel, grid_pixel in assignments
        ],
        "capacitance signed log X grid",
        model="log10",
    )
    inverse_errors = [
        abs((math.log10(value) - axis.b) / axis.m - grid_pixel)
        for value, _label_pixel, grid_pixel in assignments
    ]
    max_inverse_error = max(inverse_errors, default=float("inf"))
    if max_inverse_error > _SIGNED_LOG_X_FIT_GRID_MAX_PX:
        raise RuntimeError(
            "signed log X fit misses a source grid center by "
            f"{max_inverse_error:.3f} px; maximum is "
            f"{_SIGNED_LOG_X_FIT_GRID_MAX_PX:g} px"
        )
    value_residual_dec = float(
        np.sqrt(
            np.mean(
                [
                    (axis.m * grid_pixel + axis.b - math.log10(value)) ** 2
                    for value, _label_pixel, grid_pixel in assignments
                ]
            )
        )
    )
    return replace(
        calibration,
        x_resid_v=value_residual_dec,
        x_scale=float(axis.m),
        x_offset=float(axis.b),
        x_source=f"{calibration.x_source}_grid_seated",
        x_gridline_px=tuple(float(item[2]) for item in assignments),
        x_grid_candidate_count=len(candidates),
        x_grid_span_fraction=float(
            (max(grid_pixels) - min(grid_pixels)) / max(1, plot.width - 1)
        ),
        x_grid_residual_px=float(max_inverse_error),
        x_label_to_grid_max_px=max(
            abs(label_pixel - grid_pixel)
            for _value, label_pixel, grid_pixel in assignments
        ),
    )


_LINEAR_Y_LABEL_GRID_MAX_PX = 3.0
_LINEAR_Y_FIT_GRID_MAX_PX = 1.0


def _vector_horizontal_gridline_candidates(
    page, transform: CropTransform, plot: PlotBox
) -> list[float]:
    """Return full-width horizontal source strokes in crop-pixel coordinates."""

    positions: list[float] = []
    for drawing in page.get_drawings():
        if drawing.get("type") not in {"s", "fs"}:
            continue
        for item in drawing.get("items", []):
            if item[0] != "l":
                continue
            x0, y0 = transform.to_px(float(item[1].x), float(item[1].y))
            x1, y1 = transform.to_px(float(item[2].x), float(item[2].y))
            if abs(y1 - y0) > 1.0:
                continue
            if min(x0, x1) > plot.x0 + 3.0 or max(x0, x1) < plot.x1 - 3.0:
                continue
            center = (y0 + y1) / 2.0
            if plot.y0 - 3.0 <= center <= plot.y1 + 3.0:
                positions.append(center)

    merged: list[float] = []
    for position in sorted(positions):
        if merged and position - merged[-1] <= 1.0:
            merged[-1] = (merged[-1] + position) / 2.0
        else:
            merged.append(position)
    return merged


def _seat_linear_y_ticks_on_grid(
    calibration: AxisCalibration,
    image: np.ndarray,
    plot: PlotBox,
    *,
    page=None,
    transform: CropTransform | None = None,
) -> AxisCalibration:
    """Refit an arithmetic capacitance ladder at observed source grid centers."""

    if calibration.y_log:
        return calibration
    values = calibration.y_ticks_pf
    label_pixels = calibration.y_tick_label_px
    if len(values) < 4 or len(label_pixels) != len(values):
        raise RuntimeError("linear Y grid seating lacks one label center per tick")

    candidates = (
        _vector_horizontal_gridline_candidates(page, transform, plot)
        if page is not None and transform is not None
        else []
    )
    if len(candidates) < len(values):
        candidates = _horizontal_gridline_candidates(image, plot)

    assignments: list[tuple[float, float, float]] = []
    used: set[float] = set()
    for value, label_pixel in zip(values, label_pixels):
        owned = [
            pixel
            for pixel in candidates
            if pixel not in used
            and abs(pixel - label_pixel) <= _LINEAR_Y_LABEL_GRID_MAX_PX
        ]
        if len(owned) != 1:
            raise RuntimeError(
                "linear Y tick does not own exactly one source gridline within "
                f"{_LINEAR_Y_LABEL_GRID_MAX_PX:g} px"
            )
        grid_pixel = owned[0]
        used.add(grid_pixel)
        assignments.append((value, label_pixel, grid_pixel))

    grid_pixels = np.asarray([item[2] for item in assignments], dtype=float)
    if np.any(np.diff(grid_pixels) >= 0.0):
        raise RuntimeError("linear Y source grid centers do not follow tick-value order")
    axis = fit_axis_ticks(
        [
            AxisTick(f"{value:g}", value, grid_pixel)
            for value, _label_pixel, grid_pixel in assignments
        ],
        "capacitance linear Y grid",
        model="linear",
    )
    inverse_errors = [
        abs((value - axis.b) / axis.m - grid_pixel)
        for value, _label_pixel, grid_pixel in assignments
    ]
    max_inverse_error = max(inverse_errors, default=float("inf"))
    if max_inverse_error > _LINEAR_Y_FIT_GRID_MAX_PX:
        raise RuntimeError(
            "linear Y fit misses a source grid center by "
            f"{max_inverse_error:.3f} px; maximum is {_LINEAR_Y_FIT_GRID_MAX_PX:g} px"
        )
    value_residual_pf = float(
        np.sqrt(
            np.mean(
                [
                    (axis.m * grid_pixel + axis.b - value) ** 2
                    for value, _label_pixel, grid_pixel in assignments
                ]
            )
        )
    )
    frame_values = [axis.value(float(plot.y0)), axis.value(float(plot.y1))]
    positive_frame_values = [value for value in frame_values if value > 0.0]
    return replace(
        calibration,
        y_min_decade=math.log10(min(value for value in values if value > 0.0)),
        y_max_decade=math.log10(max(list(values) + positive_frame_values)),
        y_resid_pf=value_residual_pf,
        y_scale=float(axis.m),
        y_offset=float(axis.b),
        y_source=f"{calibration.y_source}_grid_seated",
        y_gridline_px=tuple(float(item[2]) for item in assignments),
        y_grid_candidate_count=len(candidates),
        y_grid_span_fraction=float(
            (max(grid_pixels) - min(grid_pixels)) / max(1, plot.height - 1)
        ),
        y_grid_residual_px=float(max_inverse_error),
        y_label_to_grid_max_px=max(
            abs(label_pixel - grid_pixel)
            for _value, label_pixel, grid_pixel in assignments
        ),
    )


def _major_horizontal_gridline_fit(image: np.ndarray, plot: PlotBox, count: int) -> GridlineFit:
    candidates = _horizontal_gridline_candidates(image, plot)
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
        value = float(calibration.y_scale * y + calibration.y_offset)
        return value if calibration.y_log else float(math.log10(max(value, 1e-12)))
    y_norm = _clip01((plot.y1 - y) / max(1, plot.height - 1))
    return float(calibration.y_min_decade + y_norm * (calibration.y_max_decade - calibration.y_min_decade))


def calibration_y_of_log_c(calibration: AxisCalibration, plot: PlotBox, log_c: float) -> float:
    if calibration.y_scale is not None and calibration.y_offset is not None and abs(calibration.y_scale) > 1e-12:
        value = log_c if calibration.y_log else 10.0 ** log_c
        return float((value - calibration.y_offset) / calibration.y_scale)
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
    payload = {
        "source": calibration.source,
        "x_source": calibration.x_source,
        "y_source": calibration.y_source,
        "x_min_v": calibration.x_min_v,
        "x_max_v": calibration.x_max_v,
        "y_min_decade": calibration.y_min_decade,
        "y_max_decade": calibration.y_max_decade,
        "x_ticks_v": list(calibration.x_ticks_v),
        "y_decades": list(calibration.y_decades),
        "y_log": calibration.y_log,
        "y_ticks_pf": list(calibration.y_ticks_pf),
        "x_log": calibration.x_log,
        "x_resid_v": calibration.x_resid_v,
        "y_resid_dec": calibration.y_resid_dec,
        "y_resid_pf": calibration.y_resid_pf,
        "y_tick_label_px": list(calibration.y_tick_label_px),
        "y_label_to_grid_max_px": calibration.y_label_to_grid_max_px,
        "x_scale": calibration.x_scale,
        "x_offset": calibration.x_offset,
        "y_scale": calibration.y_scale,
        "y_offset": calibration.y_offset,
        "y_gridline_px": list(calibration.y_gridline_px),
        "y_grid_candidate_count": calibration.y_grid_candidate_count,
        "y_grid_span_fraction": calibration.y_grid_span_fraction,
        "y_grid_residual_px": calibration.y_grid_residual_px,
    }
    if calibration.x_source_ticks_v:
        payload["x_source_ticks_v"] = list(calibration.x_source_ticks_v)
    if calibration.x_value_transform is not None:
        payload["x_value_transform"] = calibration.x_value_transform
    if calibration.x_gridline_px:
        payload.update(
            {
                "x_tick_label_px": list(calibration.x_tick_label_px),
                "x_label_to_grid_max_px": calibration.x_label_to_grid_max_px,
                "x_gridline_px": list(calibration.x_gridline_px),
                "x_grid_candidate_count": calibration.x_grid_candidate_count,
                "x_grid_span_fraction": calibration.x_grid_span_fraction,
                "x_grid_residual_px": calibration.x_grid_residual_px,
            }
        )
    return payload


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
