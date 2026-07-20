"""Digitize MOSFET body-diode forward-current charts.

The extraction API emits calibrated VSD/IF points and diagnostics.  Physical
diode fitting is deliberately separate: a plausible fit must never validate a
wrong panel, axis, or temperature assignment.
"""

from __future__ import annotations

import re
import json
import math
from dataclasses import asdict, dataclass
from operator import attrgetter
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

from .capacitance_traces import find_plot_box
from .capacitance_types import PlotBox
from .capacitance_vector import (
    _chain_vector_components,
    _is_neutral_gray_stroke,
    _resample_vector_trace_pixels,
    _vector_curve_edges,
)
from .crop_transform import CropTransform
from .find_charts import ChartPanel, process_pdf
from .gate_charge_trace import _detect_regular_grid_box, _projection_line_centers
from .numeric_axis import (
    AxisTick,
    NumericAxis,
    fit_axis_ticks,
    fit_numeric_axis,
    tick_aligned_plot,
)
from .overlay import draw_axis_ticks, draw_plot_frame

_NUMERIC_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_SCIENTIFIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)[Ee][+-]?\d+$")
_GRID_SEARCH_MARGIN_PX = 16
_AXIS_LABEL_OUTER_MARGIN_FRACTION = 0.18
_AUTHORITATIVE_LOG_GRID_TOLERANCE_PX = 5.0
_AUTHORITATIVE_ENDPOINT_GRID_TOLERANCE_PX = 10.0
_AUTHORITATIVE_GRID_AMBIGUITY_MARGIN_PX = 0.75
_AUTHORITATIVE_LOG_EDGE_BIND_TOLERANCE_PX = 3.0
_AUTHORITATIVE_LOG_SEQUENCE_RESIDUAL_PX = 2.0
_AUTHORITATIVE_LINEAR_SEQUENCE_RESIDUAL_PX = 2.0
_AUTHORITATIVE_LINEAR_DENSE_SEQUENCE_RESIDUAL_PX = 2.25
_AUTHORITATIVE_LINEAR_DENSE_MIN_TICKS = 5
_AUTHORITATIVE_LINEAR_DENSE_MAX_REFIT_RESIDUAL_PX = 1.5
_LINEAR_FRAME_ANCHOR_TOLERANCE_PX = 10.0
_VECTOR_JOIN_MAX_GAP_PT = 3.0
_VECTOR_JOIN_MAX_BACKTRACK_PT = 0.75
_VECTOR_JOIN_SEPARATION_FRACTION = 0.45
_VECTOR_JOIN_WIDTH_ABS_TOLERANCE_PT = 0.12
_VECTOR_JOIN_WIDTH_REL_TOLERANCE = 0.15
_SOURCE_BOUND_EXIT_MIN_X_SPAN = 0.50


@dataclass(frozen=True)
class TextLabel:
    text: str
    cx: float
    cy: float
    x0: float
    x1: float


@dataclass(frozen=True)
class PanelCalibration:
    plot: PlotBox
    x_axis: NumericAxis
    y_axis: NumericAxis
    hint: PlotBox
    hint_source: str


@dataclass(frozen=True)
class ExtractedVectorCurve:
    points_px: list[tuple[int, int]]
    dash_pattern: tuple[float, ...] | None
    source_bound_top_exit: bool = False


@dataclass(frozen=True)
class CurveIdentity:
    temperature_c: float
    role: str


@dataclass
class _JoinedVectorPath:
    points: list[tuple[float, float]]
    dash_pattern: tuple[float, ...]
    stroke_widths: tuple[float, ...]
    source_indices: tuple[int, ...]


def digitize_pdf(pdf: Path, out_dir: Path, dpi: int = 180) -> list[dict[str, object]]:
    """Digitize every body-diode panel in *pdf* and write review artifacts."""
    panels = [panel for panel in process_pdf(pdf, out_dir, dpi) if panel.kind == "body_diode"]
    # The standalone extractor stays atomic: any failed chart prevents an OK
    # manifest. Annotation uses the explicitly fail-closed per-panel wrapper
    # below so one refusal cannot abort unrelated chart families.
    results = [_digitize_panel(panel, out_dir) for panel in panels]
    _write_results(out_dir, results)
    return results


def digitize_panels_fail_closed(
    panels: list[ChartPanel], out_dir: Path
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Digitize owned panels independently and serialize explicit refusals."""

    results: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for panel in panels:
        if panel.kind != "body_diode":
            continue
        try:
            results.append(_digitize_panel(panel, out_dir))
        except Exception as error:
            errors.append(
                {
                    "kind": "body_diode",
                    "page": panel.page,
                    "diagram": panel.diagram,
                    "error": str(error),
                }
            )
    _write_results(out_dir, results)
    return results, errors


def _digitize_panel(panel: ChartPanel, out_dir: Path) -> dict[str, object]:
    """Digitize one already-owned body-diode panel."""

    crop_path = out_dir / panel.crop_png
    calibration = calibrate_panel(panel, crop_path)
    voltage_on_y = _voltage_on_y_axis(calibration)
    temperatures = _temperatures(panel.text)
    extracted = _extract_vector_curve_series(
        panel,
        crop_path,
        calibration.plot,
        curve_spans_x=voltage_on_y,
        expected_curve_count=len(temperatures),
    )
    curves_px = [curve.points_px for curve in extracted]
    repeated_roles = len(extracted) > len(temperatures)
    style_identities = (
        _legend_temperature_styles(panel, crop_path, calibration.plot)
        if repeated_roles
        else {}
    )
    identities = [style_identities.get(curve.dash_pattern) for curve in extracted]
    assigned, crossover = _assign_temperatures(
        curves_px,
        calibration,
        temperatures,
        voltage_on_y=voltage_on_y,
        curve_identities=identities if style_identities else None,
    )
    overlay = _draw_overlay(crop_path, calibration, assigned, panel)
    overlay_path = (
        out_dir / "overlays" / panel.part / f"p{panel.page:02d}_d{panel.diagram}.png"
    )
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)
    diagnostics = [
        "temperature_and_limit_identity_bound_by_source_legend_style"
        if style_identities
        else "temperature_identity_stable_over_low_mid_shared_current"
    ]
    if voltage_on_y:
        diagnostics.append("source_axes_current_x_voltage_y")
    if any(curve.source_bound_top_exit for curve in extracted):
        diagnostics.append("source_curve_exits_labeled_voltage_range_without_extrapolation")
    diagnostics.append(
        "no_verified_in_range_crossover"
        if crossover is None
        else f"verified_high_current_crossover_at_{crossover:.6g}_A"
    )
    return {
        "status": "ok",
        "diagnostics": diagnostics,
        "point_columns": ["vsd_v", "current_a"],
        "panel": asdict(panel),
        "plot_box_px": asdict(calibration.plot),
        "hint_source": calibration.hint_source,
        "x_axis": _axis_payload(calibration.x_axis),
        "y_axis": _axis_payload(calibration.y_axis),
        "curves": assigned,
        "crossing_detected_high_current": crossover is not None,
        "crossover_current_a": crossover,
        "overlay": str(overlay_path.relative_to(out_dir)),
    }


def _write_results(out_dir: Path, results: list[dict[str, object]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "diode_forward_voltage.json").write_text(json.dumps(results, indent=2) + "\n")


def calibrate_panel(panel: ChartPanel, crop_path: Path) -> PanelCalibration:
    """Calibrate both axes from structured PDF ticks in finder-crop pixels."""
    image = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    transform = CropTransform.for_chart(asdict(panel), image.shape)
    with pymupdf.open(panel.pdf) as doc:
        page = doc[panel.page - 1]
        labels = _page_labels(page, transform)
    hint, hint_source, periodic_x, periodic_y = _plot_hint(image)
    raw_x = _select_axis(labels, hint, "x")
    try:
        raw_y = _select_axis(labels, hint, "y")
    except RuntimeError:
        # A transposed diode chart can put its X-origin immediately below the
        # detected frame but inside the left Y-label gutter.  Retry only after
        # the normal selector fails, excluding centers below the evidenced
        # frame so that origin cannot corrupt an otherwise coherent ladder.
        raw_y = _select_axis(labels, hint, "y", clamp_to_hint_frame=True)
        hint_source = f"{hint_source}_y_tick_frame_retry"
    grid_search = _expanded_grid_search_box(hint, raw_x, raw_y, image.shape)
    major_x, major_y = _full_span_grid_lines(image, grid_search, hint)
    x_axis = _snap_axis_to_grid(raw_x, major_x, "X axis", authoritative=True)
    y_axis = _snap_axis_to_grid(raw_y, major_y, "Y axis", authoritative=True)
    x_axis = _snap_axis_to_grid(raw_x, periodic_x, "X axis") if x_axis is raw_x else x_axis
    y_axis = _snap_axis_to_grid(raw_y, periodic_y, "Y axis") if y_axis is raw_y else y_axis
    physical_hint = _physical_plot_hint(hint, x_axis, y_axis, major_x, major_y)
    if x_axis is raw_x:
        x_axis = _anchor_linear_axis_to_plot_frame(x_axis, physical_hint, "x")
    if y_axis is raw_y:
        y_axis = _anchor_linear_axis_to_plot_frame(y_axis, physical_hint, "y")
    plot = tick_aligned_plot(x_axis, y_axis, physical_hint)
    if plot.y0 <= 8:
        raise RuntimeError("plot: tick-aligned frame overlaps the panel title band")
    return PanelCalibration(plot, x_axis, y_axis, hint, hint_source)


def _voltage_on_y_axis(calibration: PanelCalibration) -> bool:
    """Recognize an unambiguous current-X / voltage-Y body-diode layout.

    Most body-diode plots put VSD on X and current on Y. Some ST plots reverse
    those axes. Only opt into the reversed interpretation when X has a
    materially large current-like range and Y has a compact, positive,
    linear voltage-like range; ambiguous small-signal panels keep the legacy
    orientation and therefore retain its existing fail-closed behavior.
    """

    x_values = sorted(tick.value for tick in calibration.x_axis.ticks)
    y_values = sorted(tick.value for tick in calibration.y_axis.ticks)
    if len(x_values) < 2 or len(y_values) < 2:
        return False
    x_span = x_values[-1] - x_values[0]
    y_span = y_values[-1] - y_values[0]
    return (
        calibration.y_axis.model == "linear"
        and x_values[0] >= -0.05 * max(1.0, x_values[-1])
        and x_values[-1] >= 10.0
        and 0.0 <= y_values[0] < y_values[-1] <= 5.0
        and 0.1 <= y_span <= 3.0
        and x_span >= 5.0 * y_span
    )


def _physical_plot_hint(
    hint: PlotBox,
    x_axis: NumericAxis,
    y_axis: NumericAxis,
    x_lines: tuple[float, ...],
    y_lines: tuple[float, ...],
) -> PlotBox:
    """Keep detector extensions only when the source geometry supports them.

    A logarithmic chart may end above its highest labelled decade without a
    horizontal frame line; that partial top interval is real source area and
    is bounded later by :func:`tick_aligned_plot`.  The lower edge is not
    symmetric: captions and axis labels below the plot often fool the grid-box
    detector, so an extension there still requires a full-span physical line.
    """

    def supported(edge: int, lines: tuple[float, ...], fallback: float) -> int:
        nearby = [line for line in lines if abs(line - edge) <= 2.0]
        return int(round(min(nearby, key=lambda line: abs(line - edge)))) if nearby else int(round(fallback))

    xs = sorted(tick.pixel for tick in x_axis.ticks)
    ys = sorted(tick.pixel for tick in y_axis.ticks)
    y0 = hint.y0 if y_axis.model == "log10" else supported(hint.y0, y_lines, ys[0])
    return PlotBox(
        supported(hint.x0, x_lines, xs[0]),
        y0,
        supported(hint.x1, x_lines, xs[-1]),
        supported(hint.y1, y_lines, ys[-1]),
    )


def _anchor_linear_axis_to_plot_frame(
    axis: NumericAxis,
    plot: PlotBox,
    orientation: str,
) -> NumericAxis:
    """Use verified frame endpoints when label centroids are the only binding.

    Axis-label text is not guaranteed to be centered on its physical tick.  A
    trustworthy linear fit may therefore retain visibly displaced marker
    pixels even though its value mapping is sound.  When both fitted outer
    values already predict the detected frame within a tight tolerance, anchor
    them exactly and retain the centroid scatter as the reported residual.
    """
    if axis.model != "linear":
        return axis
    ordered = sorted(axis.ticks, key=lambda tick: tick.pixel)
    if len(ordered) < 2:
        return axis
    edge0, edge1 = (plot.x0, plot.x1) if orientation == "x" else (plot.y0, plot.y1)
    predicted0 = (ordered[0].value - axis.b) / axis.m
    predicted1 = (ordered[-1].value - axis.b) / axis.m
    if max(abs(predicted0 - edge0), abs(predicted1 - edge1)) > _LINEAR_FRAME_ANCHOR_TOLERANCE_PX:
        return axis
    m = (ordered[-1].value - ordered[0].value) / (edge1 - edge0)
    if abs(m) < 1e-12:
        return axis
    b = ordered[0].value - m * edge0
    anchored = tuple(
        AxisTick(tick.text, tick.value, (tick.value - b) / m)
        for tick in ordered
    )
    residual = float(
        np.sqrt(np.mean([(bound.pixel - raw.pixel) ** 2 for raw, bound in zip(ordered, anchored)]))
    )
    return NumericAxis(axis.model, float(m), float(b), anchored, residual, axis.candidate_residuals_px)


def _expanded_grid_search_box(
    hint: PlotBox,
    x_axis: NumericAxis,
    y_axis: NumericAxis,
    image_shape: tuple[int, ...],
) -> PlotBox:
    """Extend a detector hint to every consumed outer tick before line scans.

    A detector line inside the true frame must not hide the physical endpoint
    line from the authoritative grid scan.  The bounded margin accommodates
    text-centroid offsets while keeping neighboring panels out of the search.
    """
    height, width = image_shape[:2]
    xs = [tick.pixel for tick in x_axis.ticks]
    ys = [tick.pixel for tick in y_axis.ticks]
    return PlotBox(
        max(0, int(math.floor(min(hint.x0, min(xs) - _GRID_SEARCH_MARGIN_PX)))),
        max(0, int(math.floor(min(hint.y0, min(ys) - _GRID_SEARCH_MARGIN_PX)))),
        min(width - 1, int(math.ceil(max(hint.x1, max(xs) + _GRID_SEARCH_MARGIN_PX)))),
        min(height - 1, int(math.ceil(max(hint.y1, max(ys) + _GRID_SEARCH_MARGIN_PX)))),
    )


def _plot_hint(gray, /) -> tuple[PlotBox, str, tuple[int, ...], tuple[int, ...]]:
    try:
        return find_plot_box(gray), "capacitance_grid", (), ()
    except RuntimeError:
        height, width = gray.shape
        found = _detect_regular_grid_box(
            Image.fromarray(gray),
            (0, 0, width - 1, height - 1),
        )
        if found is None:
            raise RuntimeError("plot: no usable grid hint")
        box, y_lines, x_lines = found
        return PlotBox(*box), "regular_grid", x_lines, y_lines


def _full_span_grid_lines(
    gray,
    hint: PlotBox,
    preferred_edges: PlotBox | None = None,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    # Include a tiny halo when measuring full-span rails.  A detector edge can
    # land inside a thick rasterized frame; clipping the projection at that
    # edge biases the measured center toward the plot interior.
    halo = 3
    x0 = max(0, hint.x0 - halo)
    x1 = min(gray.shape[1] - 1, hint.x1 + halo)
    y0 = max(0, hint.y0 - halo)
    y1 = min(gray.shape[0] - 1, hint.y1 + halo)
    x_dark = gray[hint.y0 : hint.y1 + 1, x0 : x1 + 1] < 210
    y_dark = gray[y0 : y1 + 1, hint.x0 : hint.x1 + 1] < 210
    x_counts = np.zeros(gray.shape[1], dtype=int)
    y_counts = np.zeros(gray.shape[0], dtype=int)
    x_counts[x0 : x1 + 1] = x_dark.sum(axis=0)
    y_counts[y0 : y1 + 1] = y_dark.sum(axis=1)
    x_minimum = 0.55 * (hint.y1 - hint.y0 + 1)
    y_minimum = 0.55 * (hint.x1 - hint.x0 + 1)
    edge_box = preferred_edges or hint
    xs = _prefer_strong_plot_edges(
        _projection_line_centers(x_counts, x_minimum, maximum_index_gap=1),
        x_counts,
        (edge_box.x0, edge_box.x1),
        x_minimum,
    )
    ys = _prefer_strong_plot_edges(
        _projection_line_centers(y_counts, y_minimum, maximum_index_gap=1),
        y_counts,
        (edge_box.y0, edge_box.y1),
        y_minimum,
    )
    return tuple(map(float, xs)), tuple(map(float, ys))


def _prefer_strong_plot_edges(
    centers: list[int], counts: np.ndarray, edges: tuple[int, int], minimum: float
) -> list[int]:
    selected = list(centers)
    for edge in edges:
        if counts[edge] >= minimum:
            # A thick frame rasterizes into a short run of full-height dark
            # columns.  The projection center is the best integer estimate of
            # the source stroke center; the detector edge can be the outside
            # of that run.  Preserve a nearby measured center and use the
            # detector edge only when projection did not find the rail.
            if not any(abs(center - edge) <= 2 for center in selected):
                selected.append(edge)
    return sorted(selected)


def _snap_axis_to_grid(
    axis: NumericAxis,
    lines: tuple[float, ...],
    name: str,
    authoritative: bool = False,
) -> NumericAxis:
    """Prefer trustworthy full-span grid positions; cautiously use fallbacks."""
    if len(lines) < 2:
        return axis
    if authoritative:
        # Log minors can sit only a few pixels from a decade; linear ticks do
        # not have that ambiguity and may have wider endpoint-label centroids.
        tolerance = _AUTHORITATIVE_LOG_GRID_TOLERANCE_PX if axis.model == "log10" else 10.0
    else:
        tolerance = max(5.0, 0.30 * min(b - a for a, b in zip(lines, lines[1:])))
    ordered_ticks = sorted(axis.ticks, key=lambda tick: tick.pixel)

    def coordinate(tick):
        return math.log10(tick.value) if axis.model == "log10" else tick.value

    frame_axis: tuple[float, float] | None = None
    if authoritative and len(ordered_ticks) >= 2:
        first_coordinate = coordinate(ordered_ticks[0])
        last_coordinate = coordinate(ordered_ticks[-1])
        first_edge, last_edge = min(lines), max(lines)
        raw_first = (first_coordinate - axis.b) / axis.m
        raw_last = (last_coordinate - axis.b) / axis.m
        if max(abs(first_edge - raw_first), abs(last_edge - raw_last)) <= _AUTHORITATIVE_LOG_EDGE_BIND_TOLERANCE_PX:
            edge_m = (last_coordinate - first_coordinate) / (last_edge - first_edge)
            frame_axis = edge_m, first_coordinate - edge_m * first_edge

    snapped = []
    used = set()
    sequence_residuals: list[float] = []
    for tick_index, tick in enumerate(ordered_ticks):
        fit_m, fit_b = frame_axis or (axis.m, axis.b)
        predicted = (coordinate(tick) - fit_b) / fit_m
        nearby = [line for line in lines if abs(line - predicted) <= tolerance]
        if (
            not nearby
            and authoritative
            and axis.model == "log10"
            and tick_index in (0, len(ordered_ticks) - 1)
        ):
            nearby = [
                line
                for line in lines
                if abs(line - predicted) <= _AUTHORITATIVE_ENDPOINT_GRID_TOLERANCE_PX
            ]
        if not nearby:
            return axis
        if len(nearby) != 1:
            if authoritative:
                edge_line = None
                if axis.model == "log10" and tick_index == 0:
                    edge_line = min(lines)
                elif axis.model == "log10" and tick_index == len(ordered_ticks) - 1:
                    edge_line = max(lines)
                if (
                    edge_line in nearby
                    and abs(edge_line - predicted)
                    <= _AUTHORITATIVE_LOG_EDGE_BIND_TOLERANCE_PX
                ):
                    nearby = [edge_line]
                else:
                    ranked = sorted((abs(line - predicted), line) for line in nearby)
                    if ranked[1][0] - ranked[0][0] <= _AUTHORITATIVE_GRID_AMBIGUITY_MARGIN_PX:
                        raise RuntimeError(f"{name}: ambiguous full-span grid binding")
                    nearby = [ranked[0][1]]
            else:
                return axis
        line = nearby[0]
        if line in used:
            if authoritative:
                raise RuntimeError(f"{name}: duplicate full-span grid binding")
            return axis
        used.add(line)
        snapped.append((tick.text, float(line)))
        if frame_axis is not None:
            sequence_residuals.append(abs(line - predicted))
    if any(b[1] <= a[1] for a, b in zip(snapped, snapped[1:])):
        if authoritative:
            raise RuntimeError(f"{name}: non-monotone full-span grid binding")
        return axis
    try:
        candidate = fit_axis_ticks(
            [
                AxisTick(tick.text, tick.value, pixel, tick.normalized_text)
                for tick, (_text, pixel) in zip(ordered_ticks, snapped)
            ],
            name,
            model=axis.model,
        )
    except RuntimeError as exc:
        if authoritative:
            raise RuntimeError(f"{name}: untrusted full-span grid binding") from exc
        return axis
    if authoritative:
        sequence_limit = (
            _AUTHORITATIVE_LOG_SEQUENCE_RESIDUAL_PX
            if axis.model == "log10"
            else _AUTHORITATIVE_LINEAR_SEQUENCE_RESIDUAL_PX
        )
        sequence_exceeds = bool(
            sequence_residuals and max(sequence_residuals) > sequence_limit
        )
        if sequence_exceeds and axis.model == "linear":
            sequence_max = max(sequence_residuals)
            refit_max = max(
                abs(tick.pixel - (tick.value - candidate.b) / candidate.m)
                for tick in candidate.ticks
            )
            sequence_exceeds = not (
                len(candidate.ticks) >= _AUTHORITATIVE_LINEAR_DENSE_MIN_TICKS
                and sequence_max <= _AUTHORITATIVE_LINEAR_DENSE_SEQUENCE_RESIDUAL_PX
                and refit_max
                <= _AUTHORITATIVE_LINEAR_DENSE_MAX_REFIT_RESIDUAL_PX
            )
        if candidate.residual_px > 1.5 or sequence_exceeds:
            raise RuntimeError(f"{name}: full-span grid residual exceeds tolerance")
        return candidate
    return candidate if candidate.residual_px + 0.5 < axis.residual_px else axis


def _page_labels(page, transform: CropTransform) -> list[TextLabel]:
    labels = []
    for word in page.get_text("words"):
        x0, y0 = transform.to_px(word[0], word[1])
        x1, y1 = transform.to_px(word[2], word[3])
        text = _normalize_numeric_text(word[4].strip())
        labels.append(TextLabel(text, (x0 + x1) / 2, (y0 + y1) / 2, x0, x1))

    # PyMuPDF word text flattens 10 + superscript 0 into "100".  Preserve the
    # decisive span metadata as explicit 10^0 before generic axis fitting.
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("text")]
            if len(spans) != 2 or spans[0]["text"] != "10":
                continue
            exponent = spans[1]["text"].strip()
            if not re.fullmatch(r"[+-]?\d+", exponent):
                continue
            if float(spans[1]["size"]) >= 0.85 * float(spans[0]["size"]):
                continue
            bbox = (
                min(spans[0]["bbox"][0], spans[1]["bbox"][0]),
                min(spans[0]["bbox"][1], spans[1]["bbox"][1]),
                max(spans[0]["bbox"][2], spans[1]["bbox"][2]),
                max(spans[0]["bbox"][3], spans[1]["bbox"][3]),
            )
            x0, y0 = transform.to_px(bbox[0], bbox[1])
            x1, y1 = transform.to_px(bbox[2], bbox[3])
            raw = f"10{exponent}"
            matches = [
                (
                    abs(label.cx - (x0 + x1) / 2) + abs(label.cy - (y0 + y1) / 2),
                    index,
                )
                for index, label in enumerate(labels)
                if label.text == raw
            ]
            distance, nearest = min(matches, default=(float("inf"), None))
            if nearest is not None and distance <= 12.0:
                labels[nearest] = TextLabel(
                    f"10^{exponent}",
                    (x0 + x1) / 2,
                    (y0 + y1) / 2,
                    x0,
                    x1,
                )
    return labels


def _normalize_numeric_text(text: str) -> str:
    """Convert PDF scientific tick text to the decimal form the shared fitter accepts."""
    text = text.replace("−", "-")
    if not _SCIENTIFIC_RE.fullmatch(text):
        return text
    return np.format_float_positional(float(text), trim="-")


def _select_axis(
    labels: list[TextLabel],
    hint: PlotBox,
    orientation: str,
    *,
    clamp_to_hint_frame: bool = False,
) -> NumericAxis:
    numeric = [
        label
        for label in labels
        if _NUMERIC_RE.fullmatch(_normalize_numeric_text(label.text))
        or re.fullmatch(r"10\^[+-]?\d+", _normalize_numeric_text(label.text))
    ]
    if orientation == "x":
        nearby = [
            label
            for label in numeric
            if hint.x0 - _AXIS_LABEL_OUTER_MARGIN_FRACTION * hint.width
            <= label.cx
            <= hint.x1 + _AXIS_LABEL_OUTER_MARGIN_FRACTION * hint.width
            and abs(label.cy - hint.y1) <= max(100.0, 0.25 * hint.height)
        ]
        cluster_attr, position_attr, tolerance, edge_value = "cy", "cx", 5.0, hint.y1
    else:
        nearby = [
            label
            for label in numeric
            if hint.x0 - 0.28 * hint.width <= label.x1 <= hint.x0 + 0.08 * hint.width
            and hint.y0 - _AXIS_LABEL_OUTER_MARGIN_FRACTION * hint.height
            <= label.cy
            <= hint.y1 + _AXIS_LABEL_OUTER_MARGIN_FRACTION * hint.height
            and (not clamp_to_hint_frame or label.cy <= hint.y1 + 2.0)
        ]
        cluster_attr, position_attr, tolerance, edge_value = "x1", "cy", 9.0, hint.x0

    groups = _cluster(nearby, attrgetter(cluster_attr), tolerance)

    def position(label: TextLabel) -> float:
        return float(getattr(label, position_attr))

    def edge_distance(group: list[TextLabel]) -> float:
        return abs(sum(float(getattr(label, cluster_attr)) for label in group) / len(group) - edge_value)

    candidates = []
    for group in groups:
        deduped = _dedupe_positions(group, position)
        if len(deduped) < 2:
            continue
        span = max(position(label) for label in deduped) - min(position(label) for label in deduped)
        required_span = (hint.width if orientation == "x" else hint.height) * 0.35
        if span < required_span:
            continue
        try:
            axis = fit_numeric_axis(
                [(label.text, position(label)) for label in deduped],
                f"{orientation.upper()} axis",
            )
        except RuntimeError:
            continue
        candidates.append((len(deduped), span, -edge_distance(group), axis))
    if not candidates:
        raise RuntimeError(f"{orientation.upper()} axis: no trustworthy numeric tick run")
    return max(candidates, key=lambda item: item[:3])[3]


def _cluster(labels: list[TextLabel], key, tolerance: float) -> list[list[TextLabel]]:
    groups: list[list[TextLabel]] = []
    for label in sorted(labels, key=key):
        if groups and abs(key(label) - sum(key(item) for item in groups[-1]) / len(groups[-1])) <= tolerance:
            groups[-1].append(label)
        else:
            groups.append([label])
    return groups


def _dedupe_positions(labels: list[TextLabel], position) -> list[TextLabel]:
    out = []
    for label in sorted(labels, key=position):
        if out and abs(position(label) - position(out[-1])) < 3.0:
            continue
        out.append(label)
    return out


def _extract_vector_curves(
    panel: ChartPanel,
    crop_path: Path,
    plot: PlotBox,
) -> list[list[tuple[int, int]]]:
    return [
        curve.points_px
        for curve in _extract_vector_curve_series(panel, crop_path, plot)
    ]


def _dash_pattern(drawing: dict[str, object]) -> tuple[float, ...]:
    text = str(drawing.get("dashes") or "")
    match = re.search(r"\[([^]]*)\]", text)
    if not match:
        return ()
    return tuple(round(float(value), 5) for value in re.findall(r"[0-9]+(?:\.[0-9]+)?", match.group(1)))


def _extract_vector_curve_series(
    panel: ChartPanel,
    crop_path: Path,
    plot: PlotBox,
    *,
    curve_spans_x: bool = False,
    expected_curve_count: int | None = None,
) -> list[ExtractedVectorCurve]:
    image = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    transform = CropTransform.for_chart(asdict(panel), image.shape)
    with pymupdf.open(panel.pdf) as doc:
        page = doc[panel.page - 1]
        top_left = transform.to_pt(plot.x0, plot.y0)
        bottom_right = transform.to_pt(plot.x1, plot.y1)
        rect = pymupdf.Rect(*top_left, *bottom_right)
        paths: list[
            tuple[list[tuple[float, float]], tuple[float, ...], float]
        ] = []
        gray_paths: list[
            tuple[list[tuple[float, float]], tuple[float, ...], float]
        ] = []
        for drawing in page.get_drawings():
            edges = _vector_curve_edges([drawing], rect, min_stroke_width=0.4)
            components = _chain_vector_components(edges)
            if components:
                paths.append(
                    (
                        max(components, key=len),
                        _dash_pattern(drawing),
                        float(drawing.get("width") or 0.0),
                    )
                )
            elif _is_neutral_gray_stroke(drawing.get("color")):
                gray_edges = _vector_curve_edges(
                    [drawing],
                    rect,
                    min_stroke_width=0.4,
                    allow_neutral_gray=True,
                )
                gray_components = _chain_vector_components(gray_edges)
                if gray_components:
                    gray_paths.append(
                        (
                            max(gray_components, key=len),
                            _dash_pattern(drawing),
                            float(drawing.get("width") or 0.0),
                        )
                    )

    groups = _join_vector_path_records(paths, rect.height)
    normal_curve_groups = [
        group
        for group, _styles in groups
        if _vector_group_span_status(
            group, rect, curve_spans_x, expected_curve_count
        )[0]
    ]
    if expected_curve_count and len(normal_curve_groups) == expected_curve_count - 1:
        confirmed_widths = [width for _path, _dash, width in paths]
        matched_gray = [
            record
            for record in gray_paths
            if any(abs(record[2] - width) <= 0.05 for width in confirmed_widths)
            and _vector_group_span_status(
                record[0], rect, curve_spans_x, expected_curve_count
            )[0]
        ]
        if len(matched_gray) == 1:
            groups = _join_vector_path_records(
                [*paths, matched_gray[0]], rect.height
            )

    curves: list[ExtractedVectorCurve] = []
    for group, styles in groups:
        full_span, source_bound_top_exit = _vector_group_span_status(
            group, rect, curve_spans_x, expected_curve_count
        )
        if not full_span:
            continue
        raw = [tuple(round(value) for value in transform.to_px(x, y)) for x, y in group]
        if curve_spans_x:
            points = _resample_vector_trace_pixels(raw, plot)
        else:
            transposed = [(y, x) for x, y in raw]
            transposed_plot = PlotBox(plot.y0, plot.x0, plot.y1, plot.x1)
            points = [
                (x, y)
                for y, x in _resample_vector_trace_pixels(
                    transposed, transposed_plot
                )
            ]
        if points:
            curves.append(
                ExtractedVectorCurve(
                    points,
                    next(iter(styles)) if len(styles) == 1 else None,
                    source_bound_top_exit,
                )
            )
    return curves


def _vector_group_span_status(
    group: list[tuple[float, float]],
    rect: pymupdf.Rect,
    curve_spans_x: bool,
    expected_curve_count: int | None,
) -> tuple[bool, bool]:
    """Apply the same source-span gate before and after gray-path rescue."""

    xs, ys = zip(*group)
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    if not curve_spans_x:
        return y_span >= 0.75 * rect.height and x_span >= 0.08 * rect.width, False
    if x_span >= 0.75 * rect.width and y_span >= 0.08 * rect.height:
        return True, False
    source_exit = _source_bound_top_exit_curve(
        group,
        rect,
        expected_curve_count=expected_curve_count,
    )
    return source_exit, source_exit


def _source_bound_top_exit_curve(
    points: list[tuple[float, float]],
    rect: pymupdf.Rect,
    *,
    expected_curve_count: int | None,
) -> bool:
    """Accept one three-label curve that provably exits the served Y range.

    A reversed-axis diode curve can leave through the labeled top boundary
    before reaching the right frame.  That is a source-clipped trace, not a
    short interior fragment.  Admit it only when its source points begin on the
    left frame, progress monotonically right/up, cross outside the calibrated
    top, and span at least half the owned X range.  The caller resamples only
    those source points; no continuation is synthesized beyond the exit.
    """

    if expected_curve_count != 3 or len(points) < 3:
        return False
    oriented = _left_to_right(points)
    xs = [point[0] for point in oriented]
    ys = [point[1] for point in oriented]
    if max(xs) - min(xs) < _SOURCE_BOUND_EXIT_MIN_X_SPAN * rect.width:
        return False
    left_tolerance = max(2.5, 0.025 * rect.width)
    if oriented[0][0] > rect.x0 + left_tolerance:
        return False
    if abs(oriented[0][0] - min(xs)) > 0.75:
        return False
    if any(right + 0.5 < left for left, right in zip(xs, xs[1:])):
        return False
    if any(right > left + 0.75 for left, right in zip(ys, ys[1:])):
        return False
    if oriented[-1][1] != min(ys):
        return False
    return oriented[-1][1] < rect.y0


def _join_vector_paths(
    paths: list[list[tuple[float, float]]],
    plot_height: float,
) -> list[list[tuple[float, float]]]:
    """Globally join generic strokes at unambiguous contiguous endpoints."""

    groups = [(list(path), (index,)) for index, path in enumerate(paths)]
    while len(groups) >= 2:
        candidates = []
        for first in range(len(groups)):
            first_path = groups[first][0]
            for second in range(first + 1, len(groups)):
                second_path = groups[second][0]
                first_y_span = max(y for _, y in first_path) - min(
                    y for _, y in first_path
                )
                second_y_span = max(y for _, y in second_path) - min(
                    y for _, y in second_path
                )
                if min(first_y_span, second_y_span) >= 0.60 * plot_height:
                    continue
                options = (
                    (
                        math.dist(first_path[-1], second_path[0]),
                        (first, "end"),
                        (second, "start"),
                        first_path + second_path[1:],
                    ),
                    (
                        math.dist(first_path[-1], second_path[-1]),
                        (first, "end"),
                        (second, "end"),
                        first_path + list(reversed(second_path[:-1])),
                    ),
                    (
                        math.dist(first_path[0], second_path[-1]),
                        (first, "start"),
                        (second, "end"),
                        second_path + first_path[1:],
                    ),
                    (
                        math.dist(first_path[0], second_path[0]),
                        (first, "start"),
                        (second, "start"),
                        list(reversed(second_path)) + first_path[1:],
                    ),
                )
                candidates.extend(
                    (distance, first_endpoint, second_endpoint, first, second, merged)
                    for distance, first_endpoint, second_endpoint, merged in options
                )
        tight = []
        for candidate in candidates:
            distance, first_endpoint, second_endpoint, *_ = candidate
            if distance > _VECTOR_JOIN_MAX_GAP_PT:
                continue
            alternatives = [
                other[0]
                for other in candidates
                if other is not candidate
                and (
                    other[1] == first_endpoint
                    or other[2] == second_endpoint
                    or other[1] == second_endpoint
                    or other[2] == first_endpoint
                )
            ]
            if alternatives and not distance < _VECTOR_JOIN_SEPARATION_FRACTION * min(
                alternatives
            ):
                continue
            tight.append(candidate)
        if not tight:
            break
        _, _, _, first, second, merged = min(
            tight,
            key=lambda item: (
                item[0],
                min(groups[item[3]][1]),
                min(groups[item[4]][1]),
            ),
        )
        source_indices = tuple(sorted(groups[first][1] + groups[second][1]))
        groups = [
            group
            for index, group in enumerate(groups)
            if index not in {first, second}
        ]
        groups.append((merged, source_indices))
        groups.sort(key=lambda group: min(group[1]))
    return [path for path, _ in groups]


def _left_to_right(
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    if len(points) >= 2 and points[-1][0] < points[0][0]:
        return list(reversed(points))
    return list(points)


def _stroke_widths_compatible(
    first: tuple[float, ...], second: tuple[float, ...]
) -> bool:
    widths = first + second
    if not widths:
        return True
    tolerance = max(
        _VECTOR_JOIN_WIDTH_ABS_TOLERANCE_PT,
        _VECTOR_JOIN_WIDTH_REL_TOLERANCE * max(widths),
    )
    return max(widths) - min(widths) <= tolerance


def _path_join_candidate(
    first_index: int,
    second_index: int,
    groups: list[_JoinedVectorPath],
    plot_height: float,
) -> tuple[float, int, int] | None:
    for left_index, right_index in (
        (first_index, second_index),
        (second_index, first_index),
    ):
        left, right = groups[left_index], groups[right_index]
        if left.dash_pattern != right.dash_pattern or not _stroke_widths_compatible(
            left.stroke_widths, right.stroke_widths
        ):
            continue
        left_x1 = max(point[0] for point in left.points)
        right_x0 = min(point[0] for point in right.points)
        if abs(left.points[-1][0] - left_x1) > 0.75:
            continue
        if abs(right.points[0][0] - right_x0) > 0.75:
            continue
        x_gap = right_x0 - left_x1
        if not -_VECTOR_JOIN_MAX_BACKTRACK_PT <= x_gap <= _VECTOR_JOIN_MAX_GAP_PT:
            continue
        left_y_span = max(y for _, y in left.points) - min(y for _, y in left.points)
        right_y_span = max(y for _, y in right.points) - min(y for _, y in right.points)
        if min(left_y_span, right_y_span) >= 0.60 * plot_height:
            continue
        return math.dist(left.points[-1], right.points[0]), left_index, right_index
    return None


def _globally_unambiguous_joins(
    groups: list[_JoinedVectorPath], plot_height: float
) -> list[tuple[float, int, int]]:
    candidates = [
        candidate
        for first in range(len(groups))
        for second in range(first + 1, len(groups))
        if (candidate := _path_join_candidate(first, second, groups, plot_height))
        is not None
    ]
    by_left: dict[int, list[tuple[float, int]]] = {}
    by_right: dict[int, list[tuple[float, int]]] = {}
    for index, (distance, left_index, right_index) in enumerate(candidates):
        by_left.setdefault(left_index, []).append((distance, index))
        by_right.setdefault(right_index, []).append((distance, index))

    def nearest_other(index: int, left_index: int, right_index: int) -> float | None:
        alternatives = [
            distance
            for distance, candidate_index in (
                by_left.get(left_index, []) + by_right.get(right_index, [])
            )
            if candidate_index != index
        ]
        return min(alternatives) if alternatives else None

    tight = []
    for index, candidate in enumerate(candidates):
        distance, left_index, right_index = candidate
        if distance > _VECTOR_JOIN_MAX_GAP_PT:
            continue
        alternative = nearest_other(index, left_index, right_index)
        if alternative is not None and not distance < (
            _VECTOR_JOIN_SEPARATION_FRACTION * alternative
        ):
            continue
        tight.append(candidate)
    return sorted(
        tight,
        key=lambda item: (
            item[0],
            min(groups[item[1]].source_indices),
            min(groups[item[2]].source_indices),
        ),
    )


def _join_vector_path_records(
    paths: list[tuple],
    plot_height: float,
) -> list[tuple[list[tuple[float, float]], set[tuple[float, ...]]]]:
    """Globally pair only source-contiguous, unambiguous split strokes.

    PDF drawing order is not curve order.  Compare every compatible left/right
    endpoint pair, then accept only a tight mutual separation winner.  A join
    must preserve dash style and stroke width, and it must be materially closer
    than any competing curve endpoint at the same split boundary.
    """

    groups = []
    for index, record in enumerate(paths):
        path, style = record[:2]
        width = record[2] if len(record) >= 3 else None
        groups.append(
            _JoinedVectorPath(
                _left_to_right(path),
                style,
                () if width is None else (float(width),),
                (index,),
            )
        )
    while len(groups) >= 2:
        candidates = _globally_unambiguous_joins(groups, plot_height)
        selected = []
        used: set[int] = set()
        for candidate in candidates:
            if candidate[1] in used or candidate[2] in used:
                continue
            selected.append(candidate)
            used.update(candidate[1:])
        if not selected:
            break
        merged_groups = []
        for distance, left_index, right_index in selected:
            left, right = groups[left_index], groups[right_index]
            right_points = right.points[1:] if distance <= 0.01 else right.points
            merged_groups.append(
                _JoinedVectorPath(
                    left.points + right_points,
                    left.dash_pattern,
                    left.stroke_widths + right.stroke_widths,
                    tuple(sorted(left.source_indices + right.source_indices)),
                )
            )
        groups = [
            group
            for index, group in enumerate(groups)
            if index not in used
        ]
        groups.extend(merged_groups)
        groups.sort(key=lambda group: min(group.source_indices))
    return [
        (group.points, {group.dash_pattern})
        for group in sorted(groups, key=lambda group: min(group.source_indices))
    ]


def _legend_temperature_styles(
    panel: ChartPanel,
    crop_path: Path,
    plot: PlotBox,
) -> dict[tuple[float, ...], CurveIdentity]:
    """Bind temperature/limit labels to their source legend dash patterns."""
    image = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    transform = CropTransform.for_chart(asdict(panel), image.shape)
    with pymupdf.open(panel.pdf) as doc:
        page = doc[panel.page - 1]
        rect = pymupdf.Rect(
            *transform.to_pt(plot.x0, plot.y0),
            *transform.to_pt(plot.x1, plot.y1),
        )
        lines: dict[tuple[int, int], list[tuple[float, float, float, float, str]]] = {}
        for word in page.get_text("words"):
            x0, y0, x1, y1, text, block, line, _ = word
            if rect.x0 <= x0 <= rect.x1 and rect.y0 <= y0 <= rect.y1:
                lines.setdefault((block, line), []).append((x0, y0, x1, y1, text))
        legend_lines = []
        for words in lines.values():
            words.sort()
            text = " ".join(word[4] for word in words)
            match = re.fullmatch(r"\s*(-?\d+)\s*°?\s*C\s*(?:,\s*(max))?\s*", text, re.I)
            if not match:
                continue
            temperature = float(match.group(1))
            if not -100 <= temperature <= 250:
                continue
            legend_lines.append(
                (
                    min(word[0] for word in words),
                    sum((word[1] + word[3]) / 2 for word in words) / len(words),
                    CurveIdentity(temperature, "maximum" if match.group(2) else "typical"),
                )
            )
        drawings = list(page.get_drawings())

    identities: dict[tuple[float, ...], CurveIdentity] = {}
    for label_x, label_y, identity in legend_lines:
        candidates = []
        for drawing in drawings:
            bounds = drawing.get("rect")
            if bounds is None:
                continue
            width, height = float(bounds.width), float(bounds.height)
            if not 0.04 * rect.width <= width <= 0.20 * rect.width or height > 3.0:
                continue
            if bounds.x1 > label_x + 2.0 or label_x - bounds.x1 > 0.20 * rect.width:
                continue
            distance = abs((bounds.y0 + bounds.y1) / 2 - label_y)
            if distance <= 2.5:
                candidates.append((distance, _dash_pattern(drawing)))
        if not candidates:
            continue
        style = min(candidates)[1]
        previous = identities.get(style)
        if previous is not None and previous != identity:
            raise RuntimeError("legend dash pattern maps to multiple curve identities")
        identities[style] = identity
    return identities


def _temperatures(text: str) -> list[float]:
    normalized = text.replace("−", "-").replace("º", "°").replace("℃", "°C")
    values = {
        float(value)
        for value in re.findall(r"(-?\d+)\s*°?\s*C\b", normalized, re.I)
    }
    return sorted(value for value in values if -100 <= value <= 250)


def _assign_temperatures(
    curves_px: list[list[tuple[int, int]]],
    calibration: PanelCalibration,
    temperatures: list[float],
    *,
    voltage_on_y: bool = False,
    curve_identities: list[CurveIdentity | None] | None = None,
) -> tuple[list[dict[str, object]], float | None]:
    style_bound = curve_identities is not None
    if style_bound:
        if len(curve_identities) != len(curves_px) or any(item is None for item in curve_identities):
            raise RuntimeError("not every extracted curve has a source-legend identity")
        identities = [item for item in curve_identities if item is not None]
        if len(set(identities)) != len(identities):
            raise RuntimeError("source legend contains duplicate curve identities")
        if {item.temperature_c for item in identities} != set(temperatures):
            raise RuntimeError("source-legend temperatures disagree with panel labels")
    elif len(curves_px) != len(temperatures) or len(curves_px) < 2:
        raise RuntimeError(
            f"curve/temperature mismatch: {len(curves_px)} curves, {len(temperatures)} labels"
        )
    voltage_axis = calibration.y_axis if voltage_on_y else calibration.x_axis
    current_axis = calibration.x_axis if voltage_on_y else calibration.y_axis
    voltage_bounds = sorted(tick.value for tick in voltage_axis.ticks)[
        :: len(voltage_axis.ticks) - 1
    ]
    current_bounds = sorted(tick.value for tick in current_axis.ticks)[
        :: len(current_axis.ticks) - 1
    ]
    data = []
    for curve in curves_px:
        points = []
        for x, y in curve:
            if voltage_on_y:
                current, voltage = current_axis.value(x), voltage_axis.value(y)
            else:
                current, voltage = current_axis.value(y), voltage_axis.value(x)
            if (
                voltage_bounds[0] <= voltage <= voltage_bounds[1]
                and current_bounds[0] <= current <= current_bounds[1]
            ):
                points.append((current, voltage))
        data.append(sorted(points))
    if any(not points for points in data):
        raise RuntimeError("curve contains no calibrated points")
    if style_bound:
        results = []
        for identity, points, points_px in zip(identities, data, curves_px):
            results.append(
                {
                    "temperature_c": identity.temperature_c,
                    "curve_role": identity.role,
                    "points": [[round(vsd, 6), round(current, 6)] for current, vsd in points],
                    "points_px": [[x, y] for x, y in points_px],
                }
            )
        results.sort(key=lambda item: (float(item["temperature_c"]), item["curve_role"] != "typical"))
        return results, _verified_crossover(
            results,
            current_axis.model == "log10",
            voltage_bounds[1] - voltage_bounds[0],
        )
    lo = max(points[0][0] for points in data)
    hi = min(points[-1][0] for points in data)
    if not hi > lo:
        raise RuntimeError("curves have no shared current range")
    log_current = current_axis.model == "log10"
    lo_c, hi_c = (math.log10(lo), math.log10(hi)) if log_current else (lo, hi)
    sample_currents = [10**value if log_current else value for value in np.linspace(lo_c, hi_c, 6)]
    orders = []
    for current in sample_currents:
        voltages = [float(np.interp(current, [p[0] for p in points], [p[1] for p in points])) for points in data]
        orders.append(tuple(np.argsort(voltages)))
    if len(set(orders[:4])) != 1:
        raise RuntimeError("temperature ordering is unstable over low/mid shared current")
    ordered_curves = orders[2]
    assigned_temp = {curve_index: temp for curve_index, temp in zip(ordered_curves, sorted(temperatures, reverse=True))}
    results = []
    for index, points in enumerate(data):
        results.append(
            {
                "temperature_c": assigned_temp[index],
                "points": [[round(vsd, 6), round(current, 6)] for current, vsd in points],
                "points_px": [[x, y] for x, y in curves_px[index]],
            }
        )
    results.sort(key=lambda item: float(item["temperature_c"]))
    return results, _verified_crossover(
        results, log_current, voltage_bounds[1] - voltage_bounds[0]
    )


def _verified_crossover(curves: list[dict[str, object]], log_current: bool, voltage_span: float):
    by_temp = {
        float(curve["temperature_c"]): curve["points"]
        for curve in curves
        if curve.get("curve_role", "typical") == "typical"
    }
    if 25.0 not in by_temp or 175.0 not in by_temp:
        return None
    cold, hot = by_temp[25.0], by_temp[175.0]
    lo, hi = max(cold[0][1], hot[0][1]), min(cold[-1][1], hot[-1][1])
    coordinate = np.linspace(math.log10(lo), math.log10(hi), 256) if log_current else np.linspace(lo, hi, 256)
    current = 10**coordinate if log_current else coordinate
    def voltage(points):
        return np.interp(current, [p[1] for p in points], [p[0] for p in points])

    delta = voltage(cold) - voltage(hot)
    margin = max(0.005, 0.01 * voltage_span)
    significant = [(index, 1 if value > 0 else -1) for index, value in enumerate(delta) if abs(value) > margin]
    states = [item for index, item in enumerate(significant) if index == 0 or item[1] != significant[index - 1][1]]
    if not states or states[0][1] != 1:
        raise RuntimeError("temperature ordering reverses in the low-current band")
    if len(states) == 1:
        return None
    if len(states) != 2 or states[1][0] < 0.60 * (len(current) - 1):
        raise RuntimeError("temperature curves have repeated or low/mid-current crossings")
    left = max(item for item in significant if item[1] == 1)[0]
    right = min(item for item in significant if item[1] == -1)[0]
    fraction = delta[left] / (delta[left] - delta[right])
    return float(10 ** (coordinate[left] + fraction * (coordinate[right] - coordinate[left])) if log_current else current[left] + fraction * (current[right] - current[left]))


def _draw_overlay(
    crop_path: Path,
    calibration: PanelCalibration,
    curves: list[dict[str, object]],
    panel: ChartPanel,
):
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    assert image is not None
    plot = calibration.plot
    voltage_on_y = _voltage_on_y_axis(calibration)
    header_y = 0
    if plot.y0 <= 32:
        source_height = image.shape[0]
        canvas = np.full((source_height + 33, image.shape[1], 3), 255, dtype=np.uint8)
        canvas[:source_height] = image
        image = canvas
        header_y = source_height
    cv2.rectangle(
        image,
        (0, header_y),
        (image.shape[1] - 1, header_y + 32),
        (220, 255, 255),
        -1,
    )
    cv2.putText(image, f"SELECTED p{panel.page} FIGURE/DIAGRAM {panel.diagram}: {panel.title}", (5, header_y + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (0, 0, 0), 1)
    axes_text = (
        "AXES: VSD (V) versus IF/IS (A)"
        if voltage_on_y
        else "AXES: IF/IS (A) versus VSD (V)"
    )
    cv2.putText(image, axes_text, (5, header_y + 27), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 1)
    draw_plot_frame(image, plot, (0, 180, 0))
    colors = ((0, 0, 255), (255, 80, 0), (0, 170, 255), (180, 0, 180), (0, 150, 0), (255, 0, 100))
    for index, curve in enumerate(curves):
        color = colors[index % len(colors)]
        emphasized = float(curve["temperature_c"]) in (25.0, 175.0)
        for x, y in curve["points_px"]:
            cv2.circle(image, (int(x), int(y)), 2 if emphasized else 1, color, -1)
        role = " max" if curve.get("curve_role") == "maximum" else ""
        label = f'{"*" if emphasized else " "}{curve["temperature_c"]:g}C{role}'
        cv2.putText(image, label, (plot.x1 - 70, plot.y0 + 32 + 14 * index), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 2 if emphasized else 1)
    draw_axis_ticks(
        image,
        plot,
        x_ticks=[(t.pixel, t.value) for t in calibration.x_axis.ticks],
        y_ticks=[(t.pixel, t.value) for t in calibration.y_axis.ticks],
        color=(255, 0, 0),
        marker_size=8,
        font_scale=0.32,
        unit_x=" A" if voltage_on_y else " V",
        unit_y=" V" if voltage_on_y else " A",
        line_aa=True,
        halo=True,
    )
    return image


def _axis_payload(axis: NumericAxis) -> dict[str, object]:
    from .numeric_axis import axis_to_json

    return axis_to_json(axis)
