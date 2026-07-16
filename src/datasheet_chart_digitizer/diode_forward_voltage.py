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
    _resample_vector_trace_pixels,
    _vector_curve_edges,
)
from .crop_transform import CropTransform
from .find_charts import ChartPanel, process_pdf
from .gate_charge_trace import _detect_regular_grid_box, _projection_line_centers
from .numeric_axis import AxisTick, NumericAxis, fit_numeric_axis, tick_aligned_plot

_NUMERIC_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_GRID_SEARCH_MARGIN_PX = 12
_AUTHORITATIVE_LOG_GRID_TOLERANCE_PX = 5.0
_AUTHORITATIVE_ENDPOINT_GRID_TOLERANCE_PX = 10.0
_LINEAR_FRAME_ANCHOR_TOLERANCE_PX = 10.0


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


def digitize_pdf(pdf: Path, out_dir: Path, dpi: int = 180) -> list[dict[str, object]]:
    """Digitize every body-diode panel in *pdf* and write review artifacts."""
    results = []
    panels = [panel for panel in process_pdf(pdf, out_dir, dpi) if panel.kind == "body_diode"]
    for panel in panels:
        crop_path = out_dir / panel.crop_png
        calibration = calibrate_panel(panel, crop_path)
        curves_px = _extract_vector_curves(panel, crop_path, calibration.plot)
        temperatures = _temperatures(panel.text)
        assigned, crossover = _assign_temperatures(curves_px, calibration, temperatures)
        overlay = _draw_overlay(crop_path, calibration, assigned, panel)
        overlay_path = out_dir / "overlays" / panel.part / f"p{panel.page:02d}_d{panel.diagram}.png"
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(overlay_path), overlay)
        diagnostics = ["temperature_identity_stable_over_low_mid_shared_current"]
        diagnostics.append(
            "no_verified_in_range_crossover"
            if crossover is None
            else f"verified_high_current_crossover_at_{crossover:.6g}_A"
        )
        results.append(
            {
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
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "diode_forward_voltage.json").write_text(json.dumps(results, indent=2) + "\n")
    return results


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
    raw_x, raw_y = _select_axis(labels, hint, "x"), _select_axis(labels, hint, "y")
    grid_search = _expanded_grid_search_box(hint, raw_x, raw_y, image.shape)
    major_x, major_y = _full_span_grid_lines(image, grid_search, hint)
    x_axis = _snap_axis_to_grid(raw_x, major_x, "X axis", authoritative=True)
    y_axis = _snap_axis_to_grid(raw_y, major_y, "Y axis", authoritative=True)
    x_axis = _snap_axis_to_grid(raw_x, periodic_x, "X axis") if x_axis is raw_x else x_axis
    y_axis = _snap_axis_to_grid(raw_y, periodic_y, "Y axis") if y_axis is raw_y else y_axis
    plot = tick_aligned_plot(x_axis, y_axis, hint)
    if x_axis is raw_x:
        x_axis = _anchor_linear_axis_to_plot_frame(x_axis, plot, "x")
    if y_axis is raw_y:
        y_axis = _anchor_linear_axis_to_plot_frame(y_axis, plot, "y")
    plot = tick_aligned_plot(x_axis, y_axis, plot)
    if plot.y0 <= 8:
        raise RuntimeError("plot: tick-aligned frame overlaps the panel title band")
    return PanelCalibration(plot, x_axis, y_axis, hint, hint_source)


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
    dark = gray[hint.y0 : hint.y1 + 1, hint.x0 : hint.x1 + 1] < 210
    x_counts = np.zeros(gray.shape[1], dtype=int)
    y_counts = np.zeros(gray.shape[0], dtype=int)
    x_counts[hint.x0 : hint.x1 + 1] = dark.sum(axis=0)
    y_counts[hint.y0 : hint.y1 + 1] = dark.sum(axis=1)
    x_minimum, y_minimum = 0.55 * dark.shape[0], 0.55 * dark.shape[1]
    edge_box = preferred_edges or hint
    xs = _prefer_strong_plot_edges(
        _projection_line_centers(x_counts, x_minimum),
        x_counts,
        (edge_box.x0, edge_box.x1),
        x_minimum,
    )
    ys = _prefer_strong_plot_edges(
        _projection_line_centers(y_counts, y_minimum),
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
            selected = [center for center in selected if abs(center - edge) > 2]
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

    snapped = []
    used = set()
    for tick_index, tick in enumerate(ordered_ticks):
        predicted = (coordinate(tick) - axis.b) / axis.m
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
                raise RuntimeError(f"{name}: ambiguous full-span grid binding")
            return axis
        line = nearby[0]
        if line in used:
            if authoritative:
                raise RuntimeError(f"{name}: duplicate full-span grid binding")
            return axis
        used.add(line)
        snapped.append((tick.text, float(line)))
    if any(b[1] <= a[1] for a, b in zip(snapped, snapped[1:])):
        if authoritative:
            raise RuntimeError(f"{name}: non-monotone full-span grid binding")
        return axis
    try:
        candidate = fit_numeric_axis(snapped, name)
    except RuntimeError as exc:
        if authoritative:
            raise RuntimeError(f"{name}: untrusted full-span grid binding") from exc
        return axis
    if authoritative:
        if candidate.residual_px > 1.5:
            raise RuntimeError(f"{name}: full-span grid residual exceeds 1.5px")
        return candidate
    return candidate if candidate.residual_px + 0.5 < axis.residual_px else axis


def _page_labels(page, transform: CropTransform) -> list[TextLabel]:
    labels = []
    for word in page.get_text("words"):
        x0, y0 = transform.to_px(word[0], word[1])
        x1, y1 = transform.to_px(word[2], word[3])
        labels.append(TextLabel(word[4].strip(), (x0 + x1) / 2, (y0 + y1) / 2, x0, x1))

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


def _select_axis(labels: list[TextLabel], hint: PlotBox, orientation: str) -> NumericAxis:
    numeric = [
        label
        for label in labels
        if _NUMERIC_RE.fullmatch(label.text) or re.fullmatch(r"10\^[+-]?\d+", label.text)
    ]
    if orientation == "x":
        nearby = [
            label
            for label in numeric
            if hint.x0 - 0.12 * hint.width <= label.cx <= hint.x1 + 0.12 * hint.width
            and abs(label.cy - hint.y1) <= max(100.0, 0.25 * hint.height)
        ]
        cluster_attr, position_attr, tolerance, edge_value = "cy", "cx", 5.0, hint.y1
    else:
        nearby = [
            label
            for label in numeric
            if hint.x0 - 0.28 * hint.width <= label.x1 <= hint.x0 + 0.08 * hint.width
            and hint.y0 - 0.12 * hint.height <= label.cy <= hint.y1 + 0.12 * hint.height
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
    image = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    transform = CropTransform.for_chart(asdict(panel), image.shape)
    with pymupdf.open(panel.pdf) as doc:
        page = doc[panel.page - 1]
        top_left = transform.to_pt(plot.x0, plot.y0)
        bottom_right = transform.to_pt(plot.x1, plot.y1)
        rect = pymupdf.Rect(*top_left, *bottom_right)
        paths = []
        for drawing in page.get_drawings():
            edges = _vector_curve_edges([drawing], rect)
            components = _chain_vector_components(edges)
            if components:
                paths.append(max(components, key=len))

    groups: list[list[tuple[float, float]]] = []
    for path in paths:
        if not groups:
            groups.append(list(path))
            continue
        current = groups[-1]
        options = (
            (math.dist(current[-1], path[0]), path),
            (math.dist(current[-1], path[-1]), list(reversed(path))),
        )
        distance, oriented = min(options, key=lambda item: item[0])
        current_y_span = max(y for _, y in current) - min(y for _, y in current)
        path_y_span = max(y for _, y in path) - min(y for _, y in path)
        both_full = min(current_y_span, path_y_span) >= 0.60 * rect.height
        if distance <= 8.0 and not both_full:
            current.extend(oriented)
        else:
            groups.append(list(path))

    curves = []
    for group in groups:
        xs, ys = zip(*group)
        if max(ys) - min(ys) < 0.75 * rect.height or max(xs) - min(xs) < 0.08 * rect.width:
            continue
        raw = [tuple(round(value) for value in transform.to_px(x, y)) for x, y in group]
        transposed = [(y, x) for x, y in raw]
        transposed_plot = PlotBox(plot.y0, plot.x0, plot.y1, plot.x1)
        points = [(x, y) for y, x in _resample_vector_trace_pixels(transposed, transposed_plot)]
        if points:
            curves.append(points)
    return curves


def _temperatures(text: str) -> list[float]:
    normalized = text.replace("−", "-").replace("º", "°").replace("℃", "°C")
    values = {float(value) for value in re.findall(r"(-?\d+)\s*°?\s*C", normalized, re.I)}
    return sorted(value for value in values if -100 <= value <= 250)


def _assign_temperatures(
    curves_px: list[list[tuple[int, int]]],
    calibration: PanelCalibration,
    temperatures: list[float],
) -> tuple[list[dict[str, object]], float | None]:
    if len(curves_px) != len(temperatures) or len(curves_px) < 2:
        raise RuntimeError(
            f"curve/temperature mismatch: {len(curves_px)} curves, {len(temperatures)} labels"
        )
    x_bounds = sorted(tick.value for tick in calibration.x_axis.ticks)[:: len(calibration.x_axis.ticks) - 1]
    y_bounds = sorted(tick.value for tick in calibration.y_axis.ticks)[:: len(calibration.y_axis.ticks) - 1]
    data = []
    for curve in curves_px:
        points = []
        for x, y in curve:
            current, voltage = calibration.y_axis.value(y), calibration.x_axis.value(x)
            if x_bounds[0] <= voltage <= x_bounds[1] and y_bounds[0] <= current <= y_bounds[1]:
                points.append((current, voltage))
        data.append(sorted(points))
    if any(not points for points in data):
        raise RuntimeError("curve contains no calibrated points")
    lo = max(points[0][0] for points in data)
    hi = min(points[-1][0] for points in data)
    if not hi > lo:
        raise RuntimeError("curves have no shared current range")
    log_current = calibration.y_axis.model == "log10"
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
    return results, _verified_crossover(results, log_current, x_bounds[1] - x_bounds[0])


def _verified_crossover(curves: list[dict[str, object]], log_current: bool, voltage_span: float):
    by_temp = {float(curve["temperature_c"]): curve["points"] for curve in curves}
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


def _draw_tick_label(image, text: str, origin: tuple[int, int]) -> None:
    for color, thickness in (((255, 255, 255), 3), ((255, 0, 0), 1)):
        cv2.putText(
            image,
            text,
            origin,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.32,
            color,
            thickness,
            cv2.LINE_AA,
        )


def _draw_overlay(
    crop_path: Path,
    calibration: PanelCalibration,
    curves: list[dict[str, object]],
    panel: ChartPanel,
):
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    assert image is not None
    plot = calibration.plot
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
    cv2.putText(image, "AXES: IF/IS (A) versus VSD (V)", (5, header_y + 27), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 1)
    cv2.rectangle(image, (plot.x0, plot.y0), (plot.x1, plot.y1), (0, 180, 0), 2)
    colors = ((0, 0, 255), (255, 80, 0), (0, 170, 255), (180, 0, 180), (0, 150, 0), (255, 0, 100))
    for index, curve in enumerate(curves):
        color = colors[index % len(colors)]
        emphasized = float(curve["temperature_c"]) in (25.0, 175.0)
        for x, y in curve["points_px"]:
            cv2.circle(image, (int(x), int(y)), 2 if emphasized else 1, color, -1)
        label = f'{"*" if emphasized else " "}{curve["temperature_c"]:g}C'
        cv2.putText(image, label, (plot.x1 - 70, plot.y0 + 32 + 14 * index), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 2 if emphasized else 1)
    tick_color = (255, 0, 0)
    for tick in calibration.x_axis.ticks:
        x = int(round(tick.pixel))
        cv2.drawMarker(image, (x, plot.y1), tick_color, cv2.MARKER_CROSS, 8, 1, cv2.LINE_AA)
        text = f"{tick.value:g} V"
        width = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.32, 1)[0][0]
        label_x = min(max(plot.x0 + 2, x - width // 2), plot.x1 - width - 2)
        _draw_tick_label(image, text, (label_x, plot.y1 - 10))
    for tick in calibration.y_axis.ticks:
        y = int(round(tick.pixel))
        cv2.drawMarker(image, (plot.x0, y), tick_color, cv2.MARKER_CROSS, 8, 1, cv2.LINE_AA)
        label_y = min(max(plot.y0 + 7, y + 4), plot.y1 - 3)
        _draw_tick_label(image, f"{tick.value:g} A", (plot.x0 + 20, label_y))
    return image


def _axis_payload(axis: NumericAxis) -> dict[str, object]:
    from .numeric_axis import axis_to_json

    return axis_to_json(axis)
