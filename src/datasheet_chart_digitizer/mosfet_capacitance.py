#!/usr/bin/env python3
"""Digitize capacitance chart traces from find_charts.py output.

This plugin extracts the three dominant dark trace components from each
`capacitances` chart crop and overlays colored centerlines on the original crop:

- red: Ciss
- blue: Coss
- green: Crss
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

try:
    from .coss_integrator import coss_metrics, validate_axis
except Exception:  # pragma: no cover - optional when this file is copied alone
    try:
        from coss_integrator import coss_metrics, validate_axis
    except Exception:
        coss_metrics = None  # type: ignore
        validate_axis = None  # type: ignore

try:
    from .axis_calibration import calibrate_axes
except Exception:  # pragma: no cover - optional standalone use
    try:
        from axis_calibration import calibrate_axes
    except Exception:
        calibrate_axes = None  # type: ignore


TRACE_COLORS_BGR = {
    "Ciss": (40, 40, 255),
    "Coss": (255, 90, 20),
    "Crss": (30, 180, 30),
}


@dataclass(frozen=True)
class PlotBox:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def height(self) -> int:
        return self.y1 - self.y0 + 1


@dataclass(frozen=True)
class Trace:
    name: str
    area: int
    bbox: tuple[int, int, int, int]
    points: list[tuple[int, int]]


@dataclass(frozen=True)
class CapAnchor:
    name: str
    value_pf: float
    vds_v: float


@dataclass(frozen=True)
class AxisCalibration:
    x_min_v: float
    x_max_v: float
    y_min_decade: float
    y_max_decade: float
    source: str
    x_ticks_v: tuple[float, ...]
    y_decades: tuple[float, ...]
    x_resid_v: float | None = None
    y_resid_dec: float | None = None
    x_scale: float | None = None
    x_offset: float | None = None
    y_scale: float | None = None
    y_offset: float | None = None
    x_source: str | None = None
    y_source: str | None = None
    y_gridline_px: tuple[float, ...] = ()
    y_grid_candidate_count: int | None = None
    y_grid_span_fraction: float | None = None
    y_grid_residual_px: float | None = None


@dataclass(frozen=True)
class GridlineFit:
    centers: list[float]
    candidate_count: int
    span_fraction: float
    residual_px: float


@dataclass(frozen=True)
class OutputChargeReference:
    qoss_pc: float | None
    vint_v: float | None
    coer_pf: float | None
    cotr_pf: float | None


@dataclass(frozen=True)
class VectorEdge:
    p0: tuple[float, float]
    p1: tuple[float, float]
    points: list[tuple[float, float]]


def find_plot_box(gray: np.ndarray) -> PlotBox:
    height, width = gray.shape
    _, bw = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)

    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(80, height // 5)))
    v_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, v_kernel)
    contours, _ = cv2.findContours(v_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    v_boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 8 and h >= height * 0.45 and width * 0.08 <= x <= width * 0.96:
            v_boxes.append((x, y, w, h))

    if len(v_boxes) < 6:
        raise RuntimeError(f"could not find plot grid verticals; found {len(v_boxes)}")

    centers = np.array([x + w / 2 for x, _, w, _ in v_boxes])
    y_starts = np.array([y for _, y, _, _ in v_boxes])
    y_ends = np.array([y + h - 1 for _, y, _, h in v_boxes])
    x0 = int(round(float(centers.min())))
    x1 = int(round(float(centers.max())))
    y0 = int(round(float(np.median(y_starts))))
    y1 = int(round(float(np.median(y_ends))))

    if x1 - x0 < width * 0.5 or y1 - y0 < height * 0.45:
        raise RuntimeError(f"implausible plot box: {(x0, y0, x1, y1)} for image {(width, height)}")

    return PlotBox(x0=x0, y0=y0, x1=x1, y1=y1)


def extract_trace_components(
    gray: np.ndarray, plot: PlotBox, anchors: dict[str, CapAnchor] | None = None
) -> list[Trace]:
    roi = gray[plot.y0 : plot.y1 + 1, plot.x0 : plot.x1 + 1]

    # The Infineon traces are black; gridlines are gray. Keeping only very dark
    # pixels separates traces from the log grid. Work column-by-column instead
    # of relying on connected components: on some low-voltage parts Ciss and
    # Coss touch at the left edge and become one connected component.
    mask = _trace_fragment_mask((roi < 90).astype(np.uint8), plot)
    centers_by_x = [_cluster_column_runs(mask[:, x]) for x in range(mask.shape[1])]

    band_samples = [[], [], []]
    for centers in centers_by_x:
        if len(centers) >= 3:
            band_samples[0].append(centers[0])
            band_samples[1].append(centers[len(centers) // 2])
            band_samples[2].append(centers[-1])
    if any(len(samples) < plot.width * 0.15 for samples in band_samples):
        raise RuntimeError(
            "could not establish three stable trace bands: "
            + ", ".join(str(len(samples)) for samples in band_samples)
        )

    assigned = _track_directional_traces(centers_by_x, plot, anchors or {})
    assigned = _repair_leading_steep_coss(mask, centers_by_x, assigned, plot)
    assigned = _repair_leading_steep_crss(mask, assigned, plot)

    traces: list[Trace] = []
    for name in ["Ciss", "Coss", "Crss"]:
        points = _smooth_points(assigned[name])
        if len(points) < plot.width * 0.25:
            raise RuntimeError(f"{name} has too few sampled columns: {len(points)}")
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        bbox = (min(xs) - plot.x0, min(ys) - plot.y0, max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
        traces.append(Trace(name=name, area=len(points), bbox=bbox, points=points))
    return traces


def extract_vector_trace_components(
    chart: dict[str, object], image: np.ndarray, plot: PlotBox
) -> list[Trace]:
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")

    pdf_path = Path(str(chart["pdf"]))
    bbox = chart.get("bbox_pt")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise RuntimeError("chart bbox_pt missing")

    height, width = image.shape[:2]
    chart_x0, chart_y0, chart_x1, chart_y1 = [float(v) for v in bbox]
    scale_x = width / max(1e-9, chart_x1 - chart_x0)
    scale_y = height / max(1e-9, chart_y1 - chart_y0)
    plot_rect = fitz.Rect(
        chart_x0 + plot.x0 / scale_x,
        chart_y0 + plot.y0 / scale_y,
        chart_x0 + plot.x1 / scale_x,
        chart_y0 + plot.y1 / scale_y,
    )

    doc = fitz.open(pdf_path)
    page = doc[int(chart["page"]) - 1]
    edges = _vector_curve_edges(page.get_drawings(), plot_rect)
    components = _chain_vector_components(edges)
    candidates: list[tuple[float, list[tuple[int, int]]]] = []
    min_x_span = plot_rect.width * 0.35
    for component in components:
        if len(component) < 8:
            continue
        xs = [p[0] for p in component]
        if max(xs) - min(xs) < min_x_span:
            continue
        if not _mostly_inside_plot(component, plot_rect):
            continue
        raw_points = [
            (
                int(round((x - chart_x0) * scale_x)),
                int(round((y - chart_y0) * scale_y)),
            )
            for x, y in component
        ]
        points = _smooth_points(_resample_vector_trace_pixels(raw_points, plot), window=9)
        if len(points) < 8:
            continue
        px_span = max(x for x, _ in points) - min(x for x, _ in points)
        if px_span < plot.width * 0.35:
            continue
        candidates.append((_path_length(component), points))

    if len(candidates) < 3:
        raise RuntimeError(f"found only {len(candidates)} vector curve candidates")

    candidates = sorted(candidates, key=lambda candidate: candidate[0], reverse=True)[:3]
    span_fractions = [
        (max(x for x, _ in points) - min(x for x, _ in points)) / max(1, plot.width)
        for _, points in candidates
    ]
    if min(span_fractions) < 0.9:
        raise RuntimeError(
            "vector candidates do not span full plot: "
            + ", ".join(f"{span:.2f}" for span in sorted(span_fractions))
        )
    ordered = sorted((points for _, points in candidates), key=_right_edge_y_pixels)
    names = ["Ciss", "Coss", "Crss"]
    traces: list[Trace] = []
    for name, points in zip(names, ordered):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        bbox_local = (min(xs) - plot.x0, min(ys) - plot.y0, max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
        traces.append(Trace(name=name, area=len(points), bbox=bbox_local, points=points))
    return traces


def _load_fitz():
    try:
        import fitz  # type: ignore

        return fitz
    except ImportError:
        return None


def _vector_curve_edges(drawings: list[dict[str, object]], plot_rect) -> list[VectorEdge]:
    edges: list[VectorEdge] = []
    expanded = plot_rect + (-1.5, -1.5, 1.5, 1.5)
    for drawing in drawings:
        if drawing.get("type") != "s":
            continue
        color = drawing.get("color")
        if not _is_curve_stroke_color(color):
            continue
        width = float(drawing.get("width") or 0.0)
        if width < 0.8 or width > 2.2:
            continue
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "l":
                p0 = (float(item[1].x), float(item[1].y))
                p1 = (float(item[2].x), float(item[2].y))
                points = [p0, p1]
            elif kind == "c":
                p0 = (float(item[1].x), float(item[1].y))
                c1 = (float(item[2].x), float(item[2].y))
                c2 = (float(item[3].x), float(item[3].y))
                p1 = (float(item[4].x), float(item[4].y))
                points = _sample_cubic(p0, c1, c2, p1)
            else:
                continue
            if not _segment_relevant(points, expanded):
                continue
            if _is_long_orthogonal_segment(points[0], points[-1], plot_rect):
                continue
            edges.append(VectorEdge(p0=points[0], p1=points[-1], points=points))
    return edges


def _is_dark_stroke(color: object) -> bool:
    if not isinstance(color, tuple) or len(color) < 3:
        return False
    return sum(float(c) for c in color[:3]) < 0.15


def _is_curve_stroke_color(color: object) -> bool:
    if not isinstance(color, tuple) or len(color) < 3:
        return False
    rgb = [float(c) for c in color[:3]]
    if sum(rgb) < 0.15:
        return True
    # Some datasheets draw capacitance curves in a dark teal with solid/dashed
    # style classes. Gray gridlines have very low saturation; accept saturated
    # dark colors as curve strokes while rejecting gray axes/grid.
    return max(rgb) < 0.65 and (max(rgb) - min(rgb)) > 0.12


def _sample_cubic(
    p0: tuple[float, float],
    c1: tuple[float, float],
    c2: tuple[float, float],
    p1: tuple[float, float],
    steps: int = 12,
) -> list[tuple[float, float]]:
    points = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        x = u**3 * p0[0] + 3 * u**2 * t * c1[0] + 3 * u * t**2 * c2[0] + t**3 * p1[0]
        y = u**3 * p0[1] + 3 * u**2 * t * c1[1] + 3 * u * t**2 * c2[1] + t**3 * p1[1]
        points.append((x, y))
    return points


def _segment_relevant(points: list[tuple[float, float]], rect) -> bool:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mid_x = (min(xs) + max(xs)) / 2
    mid_y = (min(ys) + max(ys)) / 2
    return rect.contains((mid_x, mid_y))


def _is_long_orthogonal_segment(p0: tuple[float, float], p1: tuple[float, float], plot_rect) -> bool:
    dx = abs(p1[0] - p0[0])
    dy = abs(p1[1] - p0[1])
    # Do not reject internal long horizontals: flat Ciss/Coss plateaus are
    # drawn exactly like that. Only remove the plot frame/axes at the boundary.
    boundary_pad = 1.5
    if dy < 0.1 and dx > plot_rect.width * 0.45:
        y = (p0[1] + p1[1]) / 2
        return abs(y - plot_rect.y0) <= boundary_pad or abs(y - plot_rect.y1) <= boundary_pad
    if dx < 0.1 and dy > plot_rect.height * 0.45:
        x = (p0[0] + p1[0]) / 2
        return abs(x - plot_rect.x0) <= boundary_pad or abs(x - plot_rect.x1) <= boundary_pad
    return False


def _chain_vector_components(edges: list[VectorEdge], tol: float = 0.8) -> list[list[tuple[float, float]]]:
    key_to_edges: dict[tuple[int, int], list[int]] = {}
    for idx, edge in enumerate(edges):
        for point in (edge.p0, edge.p1):
            key_to_edges.setdefault(_point_key(point, tol), []).append(idx)

    components: list[list[int]] = []
    seen: set[int] = set()
    for start in range(len(edges)):
        if start in seen:
            continue
        stack = [start]
        comp: list[int] = []
        seen.add(start)
        while stack:
            idx = stack.pop()
            comp.append(idx)
            for point in (edges[idx].p0, edges[idx].p1):
                for neighbor in key_to_edges.get(_point_key(point, tol), []):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
        components.append(comp)

    return [_order_edge_component([edges[idx] for idx in comp], tol) for comp in components]


def _point_key(point: tuple[float, float], tol: float) -> tuple[int, int]:
    return (int(round(point[0] / tol)), int(round(point[1] / tol)))


def _order_edge_component(edges: list[VectorEdge], tol: float) -> list[tuple[float, float]]:
    adjacency: dict[tuple[int, int], list[tuple[int, bool]]] = {}
    for idx, edge in enumerate(edges):
        k0 = _point_key(edge.p0, tol)
        k1 = _point_key(edge.p1, tol)
        adjacency.setdefault(k0, []).append((idx, False))
        adjacency.setdefault(k1, []).append((idx, True))

    endpoint_keys = [key for key, value in adjacency.items() if len(value) == 1]
    start_key = min(endpoint_keys or adjacency.keys(), key=lambda key: key[0])
    out: list[tuple[float, float]] = []
    used: set[int] = set()
    current_key = start_key
    while True:
        choices = [(idx, rev) for idx, rev in adjacency.get(current_key, []) if idx not in used]
        if not choices:
            break
        idx, reverse = choices[0]
        used.add(idx)
        edge_points = list(reversed(edges[idx].points)) if reverse else edges[idx].points
        if out and _distance(out[-1], edge_points[0]) < tol * 2:
            out.extend(edge_points[1:])
        else:
            out.extend(edge_points)
        current_key = _point_key(edge_points[-1], tol)

    return out


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _mostly_inside_plot(points: list[tuple[float, float]], plot_rect) -> bool:
    inside_points = [(x, y) for x, y in points if plot_rect.contains((x, y))]
    inside = len(inside_points)
    # Some Infineon vector curves intentionally protrude a little beyond the
    # detected frame or include an off-frame legend continuation in the same
    # path. Still require enough in-frame point ratio and visible x coverage so
    # long annotations do not become curve candidates.
    if inside < len(points) * 0.65:
        return False
    x_span = max(x for x, _ in inside_points) - min(x for x, _ in inside_points)
    return x_span >= plot_rect.width * 0.55


def _path_length(points: list[tuple[float, float]]) -> float:
    return sum(_distance(a, b) for a, b in zip(points, points[1:]))


def _right_edge_y(points: list[tuple[float, float]]) -> float:
    max_x = max(p[0] for p in points)
    band = [p[1] for p in points if p[0] >= max_x - 5.0]
    return float(np.median(band or [points[-1][1]]))


def _right_edge_y_pixels(points: list[tuple[int, int]]) -> float:
    max_x = max(x for x, _ in points)
    band = [y for x, y in points if x >= max_x - 20]
    return float(np.median(band or [points[-1][1]]))


def _dedupe_adjacent_points(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for point in points:
        if not out or point != out[-1]:
            out.append(point)
    return out


def _resample_vector_trace_pixels(points: list[tuple[int, int]], plot: PlotBox) -> list[tuple[int, int]]:
    """Convert exact PDF path points into a dense single-valued trace.

    PyMuPDF often returns long line segments as two endpoints. That is exact
    geometry, but it is not a digitized curve: overlays look dotted and flat
    segments disappear across label gaps. Resample each visual segment, bucket
    by x, and keep the high-capacitance envelope for near-vertical low-VDS
    knees so the exported C(V) remains one capacitance per VDS.
    """
    if len(points) < 2:
        return points

    by_x: dict[int, list[int]] = {}
    for a, b in zip(points, points[1:]):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        if dx == 0 and dy == 0:
            continue
        if abs(dx) > plot.width * 0.08 and abs(dy) > plot.height * 0.25:
            # _order_edge_component intentionally walks one connected branch.
            # If the source still contains a large diagonal jump, treat it as a
            # discontinuity between fragments instead of inventing data.
            continue
        steps = max(1, int(round(max(abs(dx), abs(dy)))))
        for idx in range(steps + 1):
            t = idx / steps
            x = int(round(a[0] + dx * t))
            y = int(round(a[1] + dy * t))
            if plot.x0 - 4 <= x <= plot.x1 + 4 and plot.y0 - 4 <= y <= plot.y1 + 4:
                by_x.setdefault(x, []).append(y)

    if not by_x:
        return _dedupe_adjacent_points(points)

    dense: list[tuple[int, int]] = []
    for x in sorted(by_x):
        ys = by_x[x]
        if len(ys) >= 4 and x <= plot.x0 + plot.width * 0.12:
            # Near-vertical low-VDS segments are legitimate C(V) knees. A
            # one-y-per-x representation should keep the upper envelope rather
            # than the visual midpoint, otherwise the chart loses most of the
            # low-voltage capacitance.
            y = min(ys)
        else:
            y = int(round(float(np.median(ys))))
        dense.append((x, y))
    return dense


def trace_semantic_diagnostics(traces: list[Trace], plot: PlotBox) -> dict[str, object]:
    by_name = {trace.name: trace.points for trace in traces}
    diagnostics: dict[str, object] = {}
    for name, points in by_name.items():
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        diagnostics[name] = {
            "points": len(points),
            "x_span_fraction": (max(xs) - min(xs)) / max(1, plot.width - 1),
            "y_range_px": max(ys) - min(ys),
        }

    if all(name in by_name for name in ("Ciss", "Coss", "Crss")):
        x_min = max(min(x for x, _ in by_name[name]) for name in ("Ciss", "Coss", "Crss"))
        x_max = min(max(x for x, _ in by_name[name]) for name in ("Ciss", "Coss", "Crss"))
        samples = list(range(x_min, x_max + 1, max(1, (x_max - x_min) // 200 or 1)))
        ciss = np.array([_interp_y(by_name["Ciss"], x) for x in samples])
        coss = np.array([_interp_y(by_name["Coss"], x) for x in samples])
        crss = np.array([_interp_y(by_name["Crss"], x) for x in samples])
        signs = np.sign(ciss - coss)
        nonzero = signs[signs != 0]
        swap_count = int(np.sum(nonzero[1:] != nonzero[:-1])) if len(nonzero) > 1 else 0
        crss_bottom = float(np.mean(crss >= np.maximum(ciss, coss))) if len(samples) else 0.0
        ciss_range = int(max(y for _, y in by_name["Ciss"]) - min(y for _, y in by_name["Ciss"]))
        coss_range = int(max(y for _, y in by_name["Coss"]) - min(y for _, y in by_name["Coss"]))
        diagnostics["checks"] = {
            "common_samples": len(samples),
            "ciss_coss_rank_swap_count": swap_count,
            "crss_bottom_fraction": crss_bottom,
            "ciss_y_range_px": ciss_range,
            "coss_y_range_px": coss_range,
            "ciss_flatter_than_coss": ciss_range < coss_range,
        }
    return diagnostics


def _interp_y(points: list[tuple[int, int]], x: int) -> float:
    ordered = sorted(points)
    if x <= ordered[0][0]:
        return float(ordered[0][1])
    if x >= ordered[-1][0]:
        return float(ordered[-1][1])
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return float(y0)
            t = (x - x0) / (x1 - x0)
            return float(y0 + (y1 - y0) * t)
    return float(ordered[-1][1])


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
        x_source="text_order_normalized_plot_extent",
        y_source="text_order_normalized_plot_extent",
    )


def infer_position_axis_calibration(
    chart: dict[str, object], image: np.ndarray, plot: PlotBox
) -> AxisCalibration:
    if calibrate_axes is None:
        raise RuntimeError("axis_calibration.calibrate_axes is not available")
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")
    bbox = chart.get("bbox_pt")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise RuntimeError("chart bbox_pt missing")

    height, width = image.shape[:2]
    chart_x0, chart_y0, chart_x1, chart_y1 = [float(v) for v in bbox]
    scale_x = width / max(1e-9, chart_x1 - chart_x0)
    scale_y = height / max(1e-9, chart_y1 - chart_y0)
    plot_rect = fitz.Rect(
        chart_x0 + plot.x0 / scale_x,
        chart_y0 + plot.y0 / scale_y,
        chart_x0 + plot.x1 / scale_x,
        chart_y0 + plot.y1 / scale_y,
    )
    doc = fitz.open(Path(str(chart["pdf"])))
    page = doc[int(chart["page"]) - 1]
    pos_cal = calibrate_axes(
        page,
        x_row_band=(plot_rect.y1 + 2.0, plot_rect.y1 + 24.0),
        y_label_x_band=(plot_rect.x0 - 42.0, plot_rect.x0 - 1.0),
        plot_y_band=(plot_rect.y0 - 8.0, plot_rect.y1 + 8.0),
    )

    # Convert page-coordinate fits to crop-pixel-coordinate fits, because trace
    # points are stored in crop pixels.
    x_scale = float(pos_cal.mx) / scale_x
    x_offset = float(pos_cal.mx) * chart_x0 + float(pos_cal.bx)
    y_scale = float(pos_cal.my) / scale_y
    y_offset = float(pos_cal.my) * chart_y0 + float(pos_cal.by)
    x_ticks = tuple(float(v) for v, _ in pos_cal.x_ticks)
    y_decades = tuple(float(e) for e, _ in pos_cal.y_decades)
    return AxisCalibration(
        x_min_v=min(x_ticks),
        x_max_v=max(x_ticks),
        y_min_decade=min(y_decades),
        y_max_decade=max(y_decades),
        source="position_text",
        x_ticks_v=x_ticks,
        y_decades=tuple(sorted(set(y_decades))),
        x_resid_v=float(pos_cal.x_resid),
        y_resid_dec=float(pos_cal.y_resid),
        x_scale=x_scale,
        x_offset=x_offset,
        y_scale=y_scale,
        y_offset=y_offset,
        x_source="position_text",
        y_source="position_text",
    )


def reject_bad_position_calibration(calibration: AxisCalibration) -> str | None:
    x_span = abs(calibration.x_max_v - calibration.x_min_v)
    max_x_resid = max(0.5, 0.02 * x_span)
    if calibration.x_resid_v is not None and calibration.x_resid_v > max_x_resid:
        return f"position x residual {calibration.x_resid_v:.4g} V exceeds {max_x_resid:.4g} V"
    if calibration.y_resid_dec is not None and calibration.y_resid_dec > 0.05:
        return f"position y residual {calibration.y_resid_dec:.4g} decades exceeds 0.05"
    return None


def infer_gridline_axis_calibration(chart: dict[str, object], image: np.ndarray, plot: PlotBox) -> AxisCalibration:
    text_calibration = infer_text_order_axis_calibration(chart)
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


def _number_tokens(text: str) -> list[float]:
    superscript_map = str.maketrans("\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079", "0123456789")
    normalized = text.translate(superscript_map)
    return [float(raw) for raw in re.findall(r"(?<![A-Za-z])[-+]?[0-9]+(?:\.[0-9]+)?", normalized)]


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
        return float(calibration.x_scale * x + calibration.x_offset)
    x_norm = _clip01((x - plot.x0) / max(1, plot.width - 1))
    return float(calibration.x_min_v + x_norm * (calibration.x_max_v - calibration.x_min_v))


def calibration_x_of_v(calibration: AxisCalibration, plot: PlotBox, vds: float) -> float:
    if calibration.x_scale is not None and calibration.x_offset is not None and abs(calibration.x_scale) > 1e-12:
        return float((vds - calibration.x_offset) / calibration.x_scale)
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


def _track_directional_traces(
    centers_by_x: list[list[float]], plot: PlotBox, anchors: dict[str, CapAnchor]
) -> dict[str, list[tuple[int, int]]]:
    seed_x = _seed_x_from_anchors(centers_by_x, anchors)
    specs = {
        "Ciss": {"seed_index": 0, "candidate": "upper"},
        "Coss": {"seed_index": 1, "candidate": "upper"},
        "Crss": {"seed_index": -1, "candidate": "bottom"},
    }
    tracked: dict[str, list[tuple[int, int]]] = {}
    for name, spec in specs.items():
        tracked[name] = _track_one_trace(
            centers_by_x,
            seed_x=seed_x,
            seed_index=int(spec["seed_index"]),
            candidate_kind=str(spec["candidate"]),
            plot=plot,
        )
    return tracked


def _repair_leading_steep_coss(
    mask: np.ndarray,
    centers_by_x: list[list[float]],
    assigned: dict[str, list[tuple[int, int]]],
    plot: PlotBox,
) -> dict[str, list[tuple[int, int]]]:
    if not all(name in assigned for name in ("Ciss", "Coss")):
        return assigned
    ciss_by_x = {x: y for x, y in assigned["Ciss"]}
    coss = sorted(assigned["Coss"])
    if len(coss) < 8:
        return assigned

    repaired_coss = _repair_missing_leading_knee(mask, coss, plot)
    if repaired_coss is not None and _repair_shape_guard(repaired_coss, coss, plot, peers={"Ciss": assigned["Ciss"]}):
        out = dict(assigned)
        out["Coss"] = repaired_coss
        return out

    leading_overlap = [(x, y) for x, y in coss[:8] if x in ciss_by_x and abs(y - ciss_by_x[x]) <= 8]
    if not leading_overlap:
        return assigned

    stable: tuple[int, int] | None = None
    for x, y in coss:
        if x in ciss_by_x and y - ciss_by_x[x] >= 45 and x - plot.x0 <= plot.width * 0.20:
            stable = (x, y)
            break
    if stable is None:
        return assigned

    anchor_x = stable[0] - plot.x0
    anchor_y = stable[1] - plot.y0
    known: list[tuple[float, float]] = []
    for local_x in range(max(0, anchor_x - 24), anchor_x + 1):
        centers = centers_by_x[local_x]
        if len(centers) < 2:
            continue
        upper = centers[0]
        for center in centers[1:]:
            if center - upper >= 20 and center <= anchor_y + 20:
                known.append((float(local_x), float(center)))
                break
    if len(known) < 3:
        return assigned

    known.append((float(anchor_x), float(anchor_y)))
    known = sorted(set(known), key=lambda point: point[1])
    min_y = int(round(min(y for _, y in known)))
    max_y = int(round(anchor_y))
    if max_y - min_y < 20:
        return assigned

    repaired_by_x: dict[int, list[float]] = {}
    known_y = np.array([y for _, y in known], dtype=float)
    known_x = np.array([x for x, _ in known], dtype=float)
    for local_y in range(min_y, max_y + 1):
        expected_x = float(np.interp(local_y, known_y, known_x))
        row_centers = _cluster_row_runs(mask[local_y, :])
        candidates = [x for x in row_centers if abs(x - expected_x) <= 8.0 and x <= anchor_x + 4]
        if not candidates:
            continue
        local_x = int(round(min(candidates, key=lambda x: abs(x - expected_x))))
        repaired_by_x.setdefault(local_x, []).append(float(local_y))

    if len(repaired_by_x) < 5:
        return assigned

    repaired_points = [
        (plot.x0 + local_x, plot.y0 + int(round(float(np.median(ys)))))
        for local_x, ys in sorted(repaired_by_x.items())
    ]
    first_repair_x = repaired_points[0][0]
    repaired_coss = [
        point
        for point in coss
        if not (point[0] < stable[0] and point[0] >= first_repair_x and point[0] in ciss_by_x and abs(point[1] - ciss_by_x[point[0]]) <= 12)
    ]
    by_x = {x: y for x, y in repaired_coss}
    for x, y in repaired_points:
        if x < stable[0]:
            by_x[x] = y
    out = dict(assigned)
    repaired_coss = sorted(by_x.items())
    if not _repair_shape_guard(repaired_coss, coss, plot, peers={"Ciss": assigned["Ciss"]}):
        return assigned
    out["Coss"] = repaired_coss
    return out


def _repair_missing_leading_knee(
    mask: np.ndarray, points: list[tuple[int, int]], plot: PlotBox
) -> list[tuple[int, int]] | None:
    """Prepend a near-vertical left-edge knee as one y per x.

    This handles raster Coss traces on high-voltage SiC charts where the first
    tracked column is already below the nearly vertical low-VDS rise. The row
    walk recovers the steep segment; grouping by x with a median center keeps
    the exported curve single-valued.
    """
    if len(points) < 8:
        return None
    ordered = sorted(points)
    anchor = ordered[0]
    anchor_x = anchor[0] - plot.x0
    anchor_y = anchor[1] - plot.y0
    if anchor_x > plot.width * 0.08 or anchor_y < plot.height * 0.10:
        return None

    max_band = int(round(plot.height * 0.16))
    y_min = max(0, anchor_y - max_band)
    left_limit = max(anchor_x + 18.0, plot.width * 0.07)
    row_points: list[tuple[int, int]] = []
    last_x: float | None = None
    for local_y in range(anchor_y - 1, y_min - 1, -1):
        row_centers = _cluster_row_runs(mask[local_y, :])
        candidates = [x for x in row_centers if 0 <= x <= left_limit]
        if not candidates:
            continue
        if last_x is None:
            best = min(candidates, key=lambda x: abs(x - anchor_x))
        else:
            monotone_candidates = [x for x in candidates if x <= last_x + 4.0]
            if not monotone_candidates:
                continue
            best = min(monotone_candidates, key=lambda x: abs(x - last_x))
            if abs(best - last_x) > 12.0:
                continue
        last_x = float(best)
        row_points.append((plot.x0 + int(round(best)), plot.y0 + local_y))

    if len(row_points) < 10:
        return None

    by_x: dict[int, list[int]] = {}
    for x, y in row_points + [anchor]:
        by_x.setdefault(x, []).append(y)
    repaired: list[tuple[int, int]] = []
    running_y: int | None = None
    for x in sorted(by_x):
        # Use the upper envelope for Coss: this repair only covers the missing
        # high-capacitance left-edge knee, and the visible stroke top is the
        # conservative continuation toward VDS=0.
        y = int(min(by_x[x]))
        if running_y is not None and y < running_y:
            y = running_y
        running_y = y
        repaired.append((x, y))
    if len(repaired) < 2:
        return None

    x_cover_min = min(x for x, _ in repaired)
    x_cover_max = max(x for x, _ in repaired)
    merged = {x: y for x, y in ordered if not (x_cover_min <= x <= x_cover_max)}
    for x, y in repaired:
        merged[x] = y
    return sorted(merged.items())


def _repair_leading_steep_crss(
    mask: np.ndarray,
    assigned: dict[str, list[tuple[int, int]]],
    plot: PlotBox,
) -> dict[str, list[tuple[int, int]]]:
    """Recover the near-vertical low-VDS Crss knee in raster charts.

    Column sampling deliberately ignores tall runs so grid lines and merged
    strokes do not collapse multiple traces into one center. That also drops
    the left-edge Crss knee on SiC capacitance plots, where the trace is almost
    vertical. Repair that local segment by sampling row runs near the left edge
    and splicing them before the first column-tracked Crss point.
    """
    crss = assigned.get("Crss")
    if not crss or len(crss) < 8:
        return assigned

    crss_by_path = list(crss)
    left_candidates = [point for point in crss_by_path if point[0] - plot.x0 <= plot.width * 0.12]
    if not left_candidates:
        return assigned
    anchor = min(left_candidates, key=lambda point: (point[0], point[1]))
    anchor_x = anchor[0] - plot.x0
    anchor_y = anchor[1] - plot.y0
    if anchor_x > plot.width * 0.08 or anchor_y < plot.height * 0.25:
        return assigned

    # Only repair the local missing knee. Extending too far upward can steal the
    # overlapping Coss/Ciss low-VDS rise, so cap the search to a modest vertical
    # band above the first stable Crss point.
    max_band = int(round(plot.height * 0.22))
    y_min = max(0, anchor_y - max_band)
    left_limit = max(anchor_x + 18.0, plot.width * 0.07)

    row_points: list[tuple[int, int]] = []
    last_x: float | None = None
    for local_y in range(anchor_y - 1, y_min - 1, -1):
        row_centers = _cluster_row_runs(mask[local_y, :])
        candidates = [x for x in row_centers if 0 <= x <= left_limit]
        if not candidates:
            continue
        if last_x is None:
            monotone_candidates = [x for x in candidates if x <= anchor_x + 4.0]
            if not monotone_candidates:
                continue
            best = min(monotone_candidates, key=lambda x: abs(x - anchor_x))
        else:
            # Crss is a single-valued decreasing C(VDS) curve. Walking toward
            # higher capacitance (smaller pixel-y) must not move the trace to
            # larger VDS, otherwise we are following a different left-edge
            # branch and will create a loop in the overlay/data.
            monotone_candidates = [x for x in candidates if x <= last_x + 4.0]
            if not monotone_candidates:
                continue
            best = min(monotone_candidates, key=lambda x: abs(x - last_x))
            if abs(best - last_x) > 12.0:
                continue
        last_x = float(best)
        row_points.append((plot.x0 + int(round(best)), plot.y0 + local_y))

    if len(row_points) < 12:
        return assigned

    # Convert the row-walk back to a function y(x). Raster strokes can be nearly
    # vertical, but the digitized C(V) curve must still have one capacitance per
    # VDS. Use the upper envelope for each x, then enforce nondecreasing y as x
    # increases so the repaired Crss knee cannot fold back on itself.
    by_x: dict[int, int] = {}
    for x, y in row_points + [anchor]:
        old = by_x.get(x)
        if old is None or y < old:
            by_x[x] = y
    repaired: list[tuple[int, int]] = []
    running_y: int | None = None
    for x in sorted(by_x):
        y = by_x[x]
        if running_y is not None and y < running_y:
            y = running_y
        running_y = y
        repaired.append((x, y))
    if len(repaired) < 2:
        return assigned

    x_cover_min = min(x for x, _ in repaired)
    x_cover_max = max(x for x, _ in repaired)
    remainder = [
        point
        for point in crss_by_path
        if not (
            x_cover_min <= point[0] <= x_cover_max
            and point != anchor
        )
    ]

    out = dict(assigned)
    merged_by_x = {x: y for x, y in remainder}
    for x, y in repaired:
        merged_by_x[x] = y
    repaired_crss = sorted(merged_by_x.items())
    peers = {name: points for name, points in assigned.items() if name in ("Ciss", "Coss")}
    if not _repair_shape_guard(repaired_crss, crss_by_path, plot, peers=peers, require_bottom=True):
        return assigned
    out["Crss"] = repaired_crss
    return out


def _repair_shape_guard(
    repaired: list[tuple[int, int]],
    original: list[tuple[int, int]],
    plot: PlotBox,
    *,
    peers: dict[str, list[tuple[int, int]]] | None = None,
    require_bottom: bool = False,
) -> bool:
    if not repaired or not _single_valued_by_x(repaired):
        return False
    if not _low_v_nonfolding(repaired, plot):
        return False
    if not _splice_continuity_ok(repaired, original, plot):
        return False
    repair_segment = _changed_repair_segment(repaired, original) or repaired
    if peers and _overlaps_peer_for_too_long(repair_segment, peers):
        return False
    if require_bottom and peers and not _is_bottom_branch(repair_segment, peers):
        return False
    return True


def _single_valued_by_x(points: list[tuple[int, int]]) -> bool:
    return len({x for x, _ in points}) == len(points)


def _low_v_nonfolding(points: list[tuple[int, int]], plot: PlotBox) -> bool:
    low_v_limit = plot.x0 + int(round(plot.width * 0.20))
    low_v_points = [(x, y) for x, y in sorted(points) if x <= low_v_limit]
    if len(low_v_points) < 3:
        return True
    ys = np.array([y for _, y in low_v_points], dtype=float)
    return bool(np.all(np.diff(ys) >= -3.0))


def _splice_continuity_ok(
    repaired: list[tuple[int, int]], original: list[tuple[int, int]], plot: PlotBox
) -> bool:
    changed_runs = _changed_repair_runs(repaired, original)
    if not changed_runs:
        return True
    repaired_sorted = sorted(repaired)
    for changed in changed_runs:
        changed_min_x = min(x for x, _ in changed)
        changed_max_x = max(x for x, _ in changed)
        first_changed = min(changed, key=lambda point: point[0])
        last_changed = max(changed, key=lambda point: point[0])
        prev_tail = [point for point in repaired_sorted if point[0] < changed_min_x]
        next_tail = [point for point in repaired_sorted if point[0] > changed_max_x]
        if prev_tail and not _splice_pair_continuous(prev_tail[-1], first_changed, plot):
            return False
        if next_tail and not _splice_pair_continuous(last_changed, next_tail[0], plot):
            return False
    return True


def _changed_repair_segment(
    repaired: list[tuple[int, int]], original: list[tuple[int, int]], y_tol: int = 3
) -> list[tuple[int, int]]:
    original_by_x = dict(original)
    return [(x, y) for x, y in sorted(repaired) if x not in original_by_x or abs(y - original_by_x[x]) > y_tol]


def _changed_repair_runs(
    repaired: list[tuple[int, int]], original: list[tuple[int, int]], y_tol: int = 3
) -> list[list[tuple[int, int]]]:
    changed = _changed_repair_segment(repaired, original, y_tol=y_tol)
    if not changed:
        return []
    runs: list[list[tuple[int, int]]] = [[changed[0]]]
    for point in changed[1:]:
        if point[0] <= runs[-1][-1][0] + 1:
            runs[-1].append(point)
        else:
            runs.append([point])
    return runs


def _splice_pair_continuous(a: tuple[int, int], b: tuple[int, int], plot: PlotBox) -> bool:
    dx = b[0] - a[0]
    dy = abs(b[1] - a[1])
    return 0 < dx <= max(8, plot.width * 0.04) and dy <= max(24, plot.height * 0.08)


def _overlaps_peer_for_too_long(
    points: list[tuple[int, int]], peers: dict[str, list[tuple[int, int]]]
) -> bool:
    shared = 0
    close = 0
    for x, y in points:
        for peer_points in peers.values():
            peer_y = _interp_y_in_range(peer_points, x)
            if peer_y is None:
                continue
            shared += 1
            if abs(y - peer_y) <= 4:
                close += 1
    return shared >= 6 and close / shared > 0.45


def _is_bottom_branch(points: list[tuple[int, int]], peers: dict[str, list[tuple[int, int]]]) -> bool:
    samples = 0
    bottom = 0
    for x, y in points:
        peer_ys = [
            peer_y
            for peer_points in peers.values()
            for peer_y in [_interp_y_in_range(peer_points, x)]
            if peer_y is not None
        ]
        if not peer_ys:
            continue
        samples += 1
        if y >= max(peer_ys) - 8:
            bottom += 1
    return samples < 4 or bottom / samples >= 0.80


def _interp_y_in_range(points: list[tuple[int, int]], x: int) -> float | None:
    if not points:
        return None
    ordered = sorted(points)
    if x < ordered[0][0] or x > ordered[-1][0]:
        return None
    return _interp_y(ordered, x)


def _seed_x_from_anchors(centers_by_x: list[list[float]], anchors: dict[str, CapAnchor]) -> int:
    vds_values = [anchor.vds_v for anchor in anchors.values() if anchor.vds_v > 0]
    if not vds_values:
        return _seed_x_from_middle(centers_by_x)

    # Infineon capacitance tables quote the characteristic point halfway along
    # these plots: 15 V on 0..30 V charts, 30 V on 0..60 V, 40 V on 0..80 V.
    anchor_vds = float(np.median(vds_values))
    axis_max_v = anchor_vds * 2.0
    target = int(round((anchor_vds / axis_max_v) * (len(centers_by_x) - 1)))
    candidates = [x for x, centers in enumerate(centers_by_x) if len(centers) >= 3]
    if not candidates:
        return _seed_x_from_middle(centers_by_x)
    return min(candidates, key=lambda x: abs(x - target))


def _seed_x_from_middle(centers_by_x: list[list[float]]) -> int:
    target = int(len(centers_by_x) * 0.55)
    candidates = [x for x, centers in enumerate(centers_by_x) if len(centers) >= 3]
    if not candidates:
        raise RuntimeError("could not find a three-trace seed column")
    return min(candidates, key=lambda x: abs(x - target))


def _track_one_trace(
    centers_by_x: list[list[float]],
    seed_x: int,
    seed_index: int,
    candidate_kind: str,
    plot: PlotBox,
) -> list[tuple[int, int]]:
    seed_centers = centers_by_x[seed_x]
    if len(seed_centers) < 3:
        raise RuntimeError(f"seed column {seed_x} has only {len(seed_centers)} centers")
    seed_y = float(seed_centers[seed_index])
    local_points = [(seed_x, seed_y)]
    local_points.extend(
        _track_direction(centers_by_x, seed_x, seed_y, -1, candidate_kind)
    )
    local_points.extend(
        _track_direction(centers_by_x, seed_x, seed_y, 1, candidate_kind)
    )

    by_x: dict[int, float] = {}
    for x, y in local_points:
        if x < 3:
            continue
        old = by_x.get(x)
        if old is None or abs(y - seed_y) < abs(old - seed_y):
            by_x[x] = y

    return [(plot.x0 + x, plot.y0 + int(round(by_x[x]))) for x in sorted(by_x)]


def _track_direction(
    centers_by_x: list[list[float]],
    seed_x: int,
    seed_y: float,
    direction: int,
    candidate_kind: str,
) -> list[tuple[int, float]]:
    points: list[tuple[int, float]] = [(seed_x, seed_y)]
    out: list[tuple[int, float]] = []
    misses = 0
    max_misses = 80
    max_step = 70.0
    x = seed_x + direction
    while 0 <= x < len(centers_by_x):
        candidates = _trace_candidates(centers_by_x[x], candidate_kind)
        pred = _predict_y(points, x)
        if candidates:
            best = min(candidates, key=lambda y: abs(y - pred))
            if abs(best - pred) <= max_step:
                points.append((x, best))
                out.append((x, best))
                misses = 0
            else:
                misses += 1
        else:
            misses += 1
        if misses > max_misses:
            break
        x += direction
    return out


def _trace_candidates(centers: list[float], candidate_kind: str) -> list[float]:
    if not centers:
        return []
    if candidate_kind == "upper":
        if len(centers) >= 3:
            return centers[:2]
        if len(centers) == 2:
            return [centers[0]]
        return []
    if candidate_kind == "bottom":
        return [centers[-1]]
    return []


def _predict_y(points: list[tuple[int, float]], x: int) -> float:
    if len(points) < 2:
        return points[-1][1]
    x1, y1 = points[-1]
    x0, y0 = points[-2]
    if x1 == x0:
        return y1
    return y1 + (y1 - y0) * ((x - x1) / (x1 - x0))


def _trace_fragment_mask(mask: np.ndarray, plot: PlotBox) -> np.ndarray:
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    min_width = max(50, int(plot.width * 0.12))
    for component in range(1, num):
        _, _, w, _, area = stats[component]
        if area >= 80 and w >= min_width:
            cleaned[labels == component] = 1
    return cleaned


def _cluster_column_runs(column: np.ndarray) -> list[float]:
    ys = np.where(column > 0)[0]
    if len(ys) == 0:
        return []

    centers: list[float] = []
    start = int(ys[0])
    prev = int(ys[0])
    for y_raw in ys[1:]:
        y = int(y_raw)
        if y == prev + 1:
            prev = y
            continue
        if prev - start + 1 <= 12:
            centers.append((start + prev) / 2)
        start = y
        prev = y
    if prev - start + 1 <= 12:
        centers.append((start + prev) / 2)

    if not centers:
        return []

    clustered: list[list[float]] = []
    for center in sorted(centers):
        if clustered and center - clustered[-1][-1] <= 14:
            clustered[-1].append(center)
        else:
            clustered.append([center])
    return [float(np.median(group)) for group in clustered]


def _cluster_row_runs(row: np.ndarray) -> list[float]:
    xs = np.where(row > 0)[0]
    if len(xs) == 0:
        return []

    centers: list[float] = []
    start = int(xs[0])
    prev = int(xs[0])
    for x_raw in xs[1:]:
        x = int(x_raw)
        if x == prev + 1:
            prev = x
            continue
        if prev - start + 1 <= 18:
            centers.append((start + prev) / 2)
        start = x
        prev = x
    if prev - start + 1 <= 18:
        centers.append((start + prev) / 2)

    if not centers:
        return []
    clustered: list[list[float]] = []
    for center in sorted(centers):
        if clustered and center - clustered[-1][-1] <= 10:
            clustered[-1].append(center)
        else:
            clustered.append([center])
    return [float(np.median(group)) for group in clustered]


def _smooth_points(points: list[tuple[int, int]], window: int = 7) -> list[tuple[int, int]]:
    if len(points) < window:
        return points
    half = window // 2
    smoothed: list[tuple[int, int]] = []
    for idx, (x, y) in enumerate(points):
        lo = max(0, idx - half)
        hi = min(len(points), idx + half + 1)
        smoothed.append((x, int(round(float(np.median([py for _, py in points[lo:hi]]))))))
    return smoothed


def draw_trace_overlay(image: np.ndarray, plot: PlotBox, traces: list[Trace]) -> np.ndarray:
    overlay = image.copy()
    cv2.rectangle(overlay, (plot.x0, plot.y0), (plot.x1, plot.y1), (0, 180, 255), 2)

    for trace in traces:
        color = TRACE_COLORS_BGR[trace.name]
        pts = trace.points
        for a, b in zip(pts, pts[1:]):
            dx = abs(b[0] - a[0])
            dy = abs(b[1] - a[1])
            if dx <= max(8, int(plot.width * 0.06)) and dy <= max(60, int(plot.height * 0.18)):
                cv2.line(overlay, a, b, color, 3, lineType=cv2.LINE_AA)
        for point in pts[:: max(1, len(pts) // 80)]:
            cv2.circle(overlay, point, 2, color, -1, lineType=cv2.LINE_AA)

        label_at = pts[min(len(pts) - 1, max(0, int(len(pts) * 0.78)))]
        cv2.putText(
            overlay,
            trace.name,
            (label_at[0] + 5, max(18, label_at[1] - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            lineType=cv2.LINE_AA,
        )

    return overlay


def draw_axis_debug_overlay(
    image: np.ndarray,
    plot: PlotBox,
    calibration: AxisCalibration,
    title: str,
) -> np.ndarray:
    overlay = image.copy()
    cv2.rectangle(overlay, (plot.x0, plot.y0), (plot.x1, plot.y1), (0, 180, 255), 3)

    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(
        overlay,
        title[:120],
        (max(5, plot.x0 - 90), max(26, plot.y0 - 42)),
        font,
        0.82,
        (0, 0, 0),
        3,
        lineType=cv2.LINE_AA,
    )
    subtitle1 = (
        f"axis={calibration.source} x={calibration.x_source or 'n/a'} "
        f"y={calibration.y_source or 'n/a'} "
        f"x_resid={_fmt_optional(calibration.x_resid_v)} "
        f"y_resid={_fmt_optional(calibration.y_resid_dec)}"
    )
    subtitle2 = (
        f"grid_n={calibration.y_grid_candidate_count if calibration.y_grid_candidate_count is not None else 'n/a'} "
        f"grid_span={_fmt_optional(calibration.y_grid_span_fraction)} "
        f"grid_resid_px={_fmt_optional(calibration.y_grid_residual_px)}"
    )
    cv2.putText(
        overlay,
        subtitle1[:140],
        (max(5, plot.x0 - 90), max(52, plot.y0 - 16)),
        font,
        0.62,
        (0, 0, 0),
        2,
        lineType=cv2.LINE_AA,
    )
    cv2.putText(
        overlay,
        subtitle2[:140],
        (max(5, plot.x0 - 90), max(78, plot.y0 + 10)),
        font,
        0.62,
        (0, 0, 0),
        2,
        lineType=cv2.LINE_AA,
    )

    for tick in calibration.x_ticks_v:
        x = int(round(calibration_x_of_v(calibration, plot, float(tick))))
        if plot.x0 - 3 <= x <= plot.x1 + 3:
            cv2.line(overlay, (x, plot.y0), (x, plot.y1), (255, 230, 0), 3, lineType=cv2.LINE_AA)
            cv2.circle(overlay, (x, plot.y1), 8, (255, 230, 0), -1, lineType=cv2.LINE_AA)
            cv2.putText(
                overlay,
                f"{tick:g}",
                (x - 16, min(image.shape[0] - 6, plot.y1 + 32)),
                font,
                0.70,
                (180, 125, 0),
                2,
                lineType=cv2.LINE_AA,
            )
    for exponent in calibration.y_decades:
        y = int(round(calibration_y_of_log_c(calibration, plot, float(exponent))))
        if plot.y0 - 3 <= y <= plot.y1 + 3:
            cv2.line(overlay, (plot.x0, y), (plot.x1, y), (255, 0, 255), 3, lineType=cv2.LINE_AA)
            cv2.circle(overlay, (plot.x0, y), 8, (255, 0, 255), -1, lineType=cv2.LINE_AA)
            cv2.putText(
                overlay,
                f"10^{int(round(exponent))}",
                (max(2, plot.x0 - 82), y + 8),
                font,
                0.70,
                (180, 0, 180),
                2,
                lineType=cv2.LINE_AA,
            )
    for y_raw in calibration.y_gridline_px:
        y = int(round(y_raw))
        if plot.y0 - 3 <= y <= plot.y1 + 3:
            cv2.line(overlay, (plot.x0, y), (plot.x1, y), (160, 0, 160), 1, lineType=cv2.LINE_AA)
            cv2.drawMarker(
                overlay,
                (plot.x0 + 14, y),
                (160, 0, 160),
                markerType=cv2.MARKER_TILTED_CROSS,
                markerSize=16,
                thickness=2,
                line_type=cv2.LINE_AA,
            )
    return overlay


def _fmt_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4g}"


def parse_capacitance_anchors(part: str, datasheet_root: Path) -> dict[str, CapAnchor]:
    csv_path = _anchor_csv_path(part, datasheet_root)
    if csv_path is None:
        return {}

    anchors: dict[str, CapAnchor] = {}
    with csv_path.open(newline="", errors="replace") as f:
        for row in csv.reader(f):
            row_text = " ".join(cell.strip() for cell in row if cell.strip())
            for name in ("Ciss", "Coss", "Crss"):
                if name not in row:
                    continue
                try:
                    symbol_idx = row.index(name)
                except ValueError:
                    continue
                tail = row[symbol_idx + 1 :]
                value_pf = _first_number_before_unit(tail, "pF")
                vds_match = re.search(r"VDS\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*V", row_text)
                if value_pf is not None and vds_match:
                    anchors[name] = CapAnchor(
                        name=name,
                        value_pf=value_pf,
                        vds_v=float(vds_match.group(1)),
                    )
    return anchors


def parse_output_charge_reference(part: str, datasheet_root: Path) -> OutputChargeReference:
    csv_path = _anchor_csv_path(part, datasheet_root)
    if csv_path is None:
        return OutputChargeReference(qoss_pc=None, vint_v=None, coer_pf=None, cotr_pf=None)

    qoss_candidates: list[tuple[int, float, float | None]] = []
    vint_v: float | None = None
    coer_pf: float | None = None
    cotr_pf: float | None = None
    with csv_path.open(newline="", errors="replace") as f:
        for row in csv.reader(f):
            row_text = " ".join(cell.strip() for cell in row if cell.strip())
            compact = row_text.replace(" ", "")
            row_vint = _extract_reference_vint(row_text)
            if row_vint is not None:
                vint_v = row_vint
            if "Qoss" in row_text and "nC" in row_text:
                value_nc = _first_number_after_symbol_before_unit(row, "Qoss", "nC")
                if value_nc is not None:
                    score = 0
                    if row_vint is not None:
                        score += 10
                    if "Output charge" in row_text:
                        score += 3
                    if "calculation based on Coss" in row_text:
                        score += 1
                    qoss_candidates.append((score, value_nc * 1000.0, row_vint))
            if coer_pf is None and ("Co(er)" in row_text or "Co(er)" in compact) and "pF" in row_text:
                coer_pf = _first_number_after_symbol_before_unit(row, "Co(er)", "pF")
            if cotr_pf is None and ("Co(tr)" in row_text or "Co(tr)" in compact) and "pF" in row_text:
                cotr_pf = _first_number_after_symbol_before_unit(row, "Co(tr)", "pF")
            if qoss_candidates and vint_v is not None and coer_pf is not None and cotr_pf is not None:
                break

    qoss_pc: float | None = None
    if qoss_candidates:
        score, qoss_pc, candidate_vint = max(qoss_candidates, key=lambda item: item[0])
        if candidate_vint is not None:
            vint_v = candidate_vint
    return OutputChargeReference(qoss_pc=qoss_pc, vint_v=vint_v, coer_pf=coer_pf, cotr_pf=cotr_pf)


def _extract_reference_vint(row_text: str) -> float | None:
    compact = row_text.replace(" ", "")
    range_match = re.search(r"VDS=0(?:\.{2,3}|\u2026)([0-9]+(?:\.[0-9]+)?)V", compact)
    if range_match:
        return float(range_match.group(1))
    eq_match = re.search(r"V(?:DS|DD)=([0-9]+(?:\.[0-9]+)?)V", compact)
    if eq_match:
        return float(eq_match.group(1))
    at_match = re.search(r"@\s*([0-9]+(?:\.[0-9]+)?)\s*V", row_text)
    if at_match:
        return float(at_match.group(1))
    return None


def _anchor_csv_path(part: str, datasheet_root: Path) -> Path | None:
    candidates = [part]
    suffix_stripped = re.sub(r"(?:A?KMA|A?KSA|XKSA)[0-9]+$", "", part)
    if suffix_stripped != part:
        candidates.append(suffix_stripped)
    for candidate in candidates:
        path = datasheet_root / f"{candidate}.pdf.nop.csv"
        if path.exists():
            return path
    return None


def _first_number_after_symbol_before_unit(cells: list[str], symbol: str, unit: str) -> float | None:
    text = " ".join(cells)
    symbol_pos = _symbol_position(text, symbol)
    if symbol_pos >= 0:
        text = text[symbol_pos + len(symbol) :]
    text = re.sub(r"@\s*[0-9]+(?:\.[0-9]+)?\s*V", " ", text)
    unit_pos = text.find(unit)
    if unit_pos >= 0:
        text = text[:unit_pos]
    return _first_positive_number(text)


def _symbol_position(text: str, symbol: str) -> int:
    pos = text.find(symbol)
    if pos >= 0:
        return pos
    if symbol == "Co(tr)":
        return text.replace(" ", "").find(symbol)
    return -1


def _first_number_before_unit(cells: list[str], unit: str) -> float | None:
    text = " ".join(cells)
    unit_pos = text.find(unit)
    if unit_pos >= 0:
        text = text[:unit_pos]
    return _first_positive_number(text)


def _first_positive_number(text: str) -> float | None:
    numbers = re.findall(r"(?<![A-Za-z])[-+]?[0-9]+(?:\.[0-9]+)?", text)
    for raw in numbers:
        value = float(raw)
        if value > 0:
            return value
    return None


def output_charge_reference_to_json(ref: OutputChargeReference) -> dict[str, float | None]:
    return {
        "qoss_pc": ref.qoss_pc,
        "vint_v": ref.vint_v,
        "coer_pf": ref.coer_pf,
        "cotr_pf": ref.cotr_pf,
    }


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


def coss_metrics_to_json(metrics: object) -> dict[str, float]:
    return {
        "Qoss_pc": float(metrics.Qoss),
        "Eoss_pJ": float(metrics.Eoss),
        "Co_tr_pf": float(metrics.Co_tr),
        "Co_er_pf": float(metrics.Co_er),
        "Qoss_below_first_pc": float(metrics.Qoss_below_first),
        "Qoss_chart_range_pc": float(metrics.Qoss_chart_range),
        "Qoss_above_last_pc": float(metrics.Qoss_above_last),
        "Eoss_below_first_pJ": float(metrics.Eoss_below_first),
        "Eoss_chart_range_pJ": float(metrics.Eoss_chart_range),
        "Eoss_above_last_pJ": float(metrics.Eoss_above_last),
        "C0_pf": float(metrics.C0),
        "phi_v": float(metrics.phi),
        "m": float(metrics.m),
        "first_vds_v": float(metrics.first_vds),
        "first_coss_pf": float(metrics.first_coss),
        "splice_rel_error": float(metrics.splice_rel_error),
        "extrapolated_qoss_fraction": float(metrics.extrapolated_qoss_fraction),
        "clipped_completion_active": bool(metrics.clipped_completion_active),
        "clip_boundary_vds": metrics.clip_boundary_vds,
        "Qoss_clip_completed_pc": float(metrics.Qoss_clip_completed),
        "Qoss_clip_visible_floor_pc": float(metrics.Qoss_clip_visible_floor),
        "Qoss_clip_added_pc": float(metrics.Qoss_clip_added),
        "clipped_completion_fraction": float(metrics.clipped_completion_fraction),
    }


def qoss_validation_status(
    metrics: object | None,
    validation_error: str | None,
    vendor_tail_validation: dict[str, object] | None = None,
) -> str | None:
    if metrics is None:
        return None
    if vendor_tail_validation and vendor_tail_validation.get("status") == "pass":
        return "pass_vendor_qoss_curve_tail"
    if float(metrics.extrapolated_qoss_fraction) > 0.20:
        return "unreliable_extrapolation"
    if bool(metrics.clipped_completion_active):
        if validation_error is not None:
            return "chart_clipped_table_authoritative"
        return "clipped_chart_completed"
    if validation_error is None:
        return "pass"
    return "graph_table_inconsistent"


def vendor_qoss_tail_validation(
    part: str,
    metrics: object | None,
    output_ref: OutputChargeReference,
    tol: float,
) -> dict[str, object] | None:
    if metrics is None or output_ref.vint_v is None:
        return None
    curve_path = _vendor_qoss_curve_path(part)
    if curve_path is None:
        return None
    rows: list[tuple[float, float]] = []
    with curve_path.open(newline="", errors="replace") as f:
        for row in csv.DictReader(f):
            try:
                rows.append((float(row["VDS_V"]), float(row["Qoss_nC"]) * 1000.0))
            except (KeyError, TypeError, ValueError):
                continue
    if len(rows) < 2:
        return None
    rows.sort()
    vds = np.array([v for v, _ in rows], dtype=float)
    qoss = np.array([q for _, q in rows], dtype=float)
    first_v = float(metrics.first_vds)
    vint = float(output_ref.vint_v)
    if first_v < vds[0] or first_v > vds[-1] or vint < vds[0] or vint > vds[-1]:
        return {
            "tail_source": "vendor_qoss_curve",
            "status": "out_of_range",
            "curve_csv": str(curve_path),
        }
    vendor_tail = float(np.interp(first_v, vds, qoss))
    vendor_total = float(np.interp(vint, vds, qoss))
    qoss_with_vendor_tail = float(metrics.Qoss_chart_range + metrics.Qoss_above_last + vendor_tail)
    ref = output_ref.qoss_pc if output_ref.qoss_pc is not None else vendor_total
    rel_to_ref = abs(qoss_with_vendor_tail - float(ref)) / float(ref) if ref else None
    rel_to_vendor = abs(qoss_with_vendor_tail - vendor_total) / vendor_total if vendor_total else None
    status = "pass" if rel_to_ref is not None and rel_to_ref <= tol else "fail"
    return {
        "tail_source": "vendor_qoss_curve",
        "status": status,
        "curve_csv": str(curve_path),
        "first_vds_v": first_v,
        "vint_v": vint,
        "vendor_tail_pc": vendor_tail,
        "vendor_total_pc": vendor_total,
        "chart_range_pc": float(metrics.Qoss_chart_range),
        "qoss_with_vendor_tail_pc": qoss_with_vendor_tail,
        "reference_qoss_pc": ref,
        "rel_error_to_reference": rel_to_ref,
        "rel_error_to_vendor_curve": rel_to_vendor,
    }


def _vendor_qoss_curve_path(part: str) -> Path | None:
    here = Path(__file__).resolve().parent
    candidates = [
        here / f"{part.lower()}_qoss_reference.csv",
        here / f"{part.lower()}_qoss_diagram17_reference.csv",
    ]
    if part == "IMZA75R050M2H":
        candidates.append(here / "imza_qoss_diagram17_reference.csv")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def top_decade_clip_diagnostic(
    trace_data: dict[str, list[tuple[float, float]]],
    calibration: AxisCalibration | None,
) -> dict[str, object] | None:
    if calibration is None or "Coss" not in trace_data:
        return None
    data = sorted(trace_data["Coss"])
    if not data:
        return None
    axis_top_pf = 10.0 ** calibration.y_max_decade
    low_v_limit = calibration.x_min_v + 0.05 * (calibration.x_max_v - calibration.x_min_v)
    low_v_caps = [cap for vds, cap in data if vds <= low_v_limit]
    max_low_v_coss = max(low_v_caps or [data[0][1]])
    return {
        "axis_top_pf": axis_top_pf,
        "max_low_v_coss_pf": max_low_v_coss,
        "low_v_limit_v": low_v_limit,
        "near_axis_top": max_low_v_coss >= axis_top_pf * 0.70,
    }


def process_chart(
    chart: dict[str, object],
    crop_path: Path,
    out_dir: Path,
    rel_stem: Path,
    datasheet_root: Path,
    debug_axis_overlays: bool = False,
) -> dict[str, object]:
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"could not read crop {crop_path}")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    plot = find_plot_box(gray)
    anchors = parse_capacitance_anchors(str(chart["part"]), datasheet_root)
    output_ref = parse_output_charge_reference(str(chart["part"]), datasheet_root)
    axis_text_order: AxisCalibration | None = None
    axis_text_order_error: str | None = None
    axis_grid_error: str | None = None
    axis_position_error: str | None = None
    axis_error: str | None = None
    try:
        axis_text_order = infer_text_order_axis_calibration(chart)
    except Exception as text_exc:
        axis_text_order_error = str(text_exc)
    try:
        axis_calibration = infer_position_axis_calibration(chart, image, plot)
        rejection = reject_bad_position_calibration(axis_calibration)
        if rejection is not None:
            axis_position_error = rejection
            try:
                axis_calibration = infer_gridline_axis_calibration(chart, image, plot)
            except Exception as grid_exc:
                axis_grid_error = str(grid_exc)
                axis_calibration = axis_text_order
    except Exception as position_exc:
        axis_position_error = str(position_exc)
        try:
            axis_calibration = infer_gridline_axis_calibration(chart, image, plot)
        except Exception as grid_exc:
            axis_grid_error = str(grid_exc)
            axis_calibration = axis_text_order
    if axis_calibration is None:
        axis_error = f"position: {axis_position_error}; grid: {axis_grid_error}; text_order: {axis_text_order_error}"
    extraction_method = "vector"
    try:
        traces = extract_vector_trace_components(chart, image, plot)
    except Exception as vector_exc:
        extraction_method = "raster"
        traces = extract_trace_components(gray, plot, anchors)
        vector_error = str(vector_exc)
    else:
        vector_error = None
    overlay = draw_trace_overlay(image, plot, traces)

    overlay_path = out_dir / "overlays" / rel_stem.with_suffix(".overlay.png")
    points_path = out_dir / "points" / rel_stem.with_suffix(".points.csv")
    axis_debug_path: Path | None = None
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    points_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)
    if debug_axis_overlays and axis_calibration is not None:
        axis_debug_path = out_dir / "axis_debug_overlays" / rel_stem.with_suffix(".axis.png")
        axis_debug_path.parent.mkdir(parents=True, exist_ok=True)
        axis_overlay = draw_axis_debug_overlay(
            image,
            plot,
            axis_calibration,
            f"{chart.get('part', '')} {chart.get('diagram', '')}",
        )
        cv2.imwrite(str(axis_debug_path), axis_overlay)

    trace_data: dict[str, list[tuple[float, float]]] = {}
    if axis_calibration is not None:
        trace_data = {trace.name: trace_data_points(trace, plot, axis_calibration) for trace in traces}

    with points_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["trace", "x_px", "y_px", "x_norm", "y_norm_log_axis", "vds_V", "cap_pF"])
        for trace in traces:
            data_points = trace_data.get(trace.name)
            for idx, (x, y) in enumerate(trace.points):
                vds, cap = data_points[idx] if data_points is not None else ("", "")
                writer.writerow(
                    [
                        trace.name,
                        x,
                        y,
                        (x - plot.x0) / max(1, plot.width - 1),
                        (plot.y1 - y) / max(1, plot.height - 1),
                        vds,
                        cap,
                    ]
                )

    qoss_metrics: dict[str, float] | None = None
    qoss_validation_error: str | None = None
    qoss_vendor_tail_validation: dict[str, object] | None = None
    metrics = None
    coss_clip_diag = top_decade_clip_diagnostic(trace_data, axis_calibration)
    if axis_calibration is not None and "Coss" in trace_data and output_ref.vint_v:
        try:
            vds, coss = arrays_for_trace_data(trace_data["Coss"])
            if coss_metrics is None or validate_axis is None:
                raise RuntimeError("coss_integrator is not available")
            clip_ceiling = None
            if coss_clip_diag and coss_clip_diag.get("near_axis_top"):
                clip_ceiling = float(coss_clip_diag["axis_top_pf"])
            metrics = coss_metrics(vds, coss, output_ref.vint_v, clip_ceiling=clip_ceiling)
            qoss_metrics = coss_metrics_to_json(metrics)
            qoss_vendor_tail_validation = vendor_qoss_tail_validation(
                str(chart["part"]),
                metrics,
                output_ref,
                tol=0.25,
            )
            try:
                validate_axis(
                    vds,
                    coss,
                    output_ref.vint_v,
                    ds_Qoss=output_ref.qoss_pc,
                    ds_Coer=output_ref.coer_pf,
                    ds_Cotr=output_ref.cotr_pf,
                    tol=0.25,
                    clip_ceiling=clip_ceiling,
                )
            except Exception as validation_exc:
                qoss_validation_error = str(validation_exc)
        except Exception as exc:
            qoss_validation_error = str(exc)

    return {
        "crop": str(crop_path),
        "overlay": str(overlay_path.relative_to(out_dir)),
        "axis_debug_overlay": str(axis_debug_path.relative_to(out_dir)) if axis_debug_path is not None else None,
        "points": str(points_path.relative_to(out_dir)),
        "plot_box_px": [plot.x0, plot.y0, plot.x1, plot.y1],
        "extraction_method": extraction_method,
        "vector_error": vector_error,
        "axis_calibration": axis_calibration_to_json(axis_calibration) if axis_calibration is not None else None,
        "axis_text_order_calibration": axis_calibration_to_json(axis_text_order) if axis_text_order is not None else None,
        "axis_calibration_delta_vs_text_order": calibration_delta_to_json(axis_calibration, axis_text_order, plot),
        "axis_position_error": axis_position_error,
        "axis_grid_error": axis_grid_error,
        "axis_text_order_error": axis_text_order_error,
        "axis_error": axis_error,
        "output_charge_reference": output_charge_reference_to_json(output_ref),
        "qoss_metrics": qoss_metrics,
        "qoss_vendor_tail_validation": qoss_vendor_tail_validation,
        "qoss_validation_status": qoss_validation_status(metrics, qoss_validation_error, qoss_vendor_tail_validation),
        "qoss_validation_error": qoss_validation_error,
        "coss_top_decade_clip": coss_clip_diag,
        "diagnostics": trace_semantic_diagnostics(traces, plot),
        "anchors": {
            name: {"value_pf": anchor.value_pf, "vds_v": anchor.vds_v}
            for name, anchor in anchors.items()
        },
        "traces": [
            {
                "name": trace.name,
                "area": trace.area,
                "bbox_local_px": list(trace.bbox),
                "points": len(trace.points),
            }
            for trace in traces
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chart_index", type=Path, help="charts.json from find_charts.py")
    parser.add_argument("--out", type=Path, help="Output dir; defaults to chart index directory")
    parser.add_argument(
        "--datasheet-root",
        type=Path,
        help="Directory containing <part>.pdf.nop.csv anchor tables; defaults to each chart PDF's directory",
    )
    parser.add_argument(
        "--debug-axis-overlays",
        action="store_true",
        help="Write axis calibration overlays with selected ticks/gridlines and fit residuals.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    index_path = args.chart_index
    base_dir = index_path.parent
    out_dir = args.out or base_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    charts = json.loads(index_path.read_text())

    results: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for chart in charts:
        if chart.get("kind") != "capacitances":
            continue
        chart = dict(chart)
        if chart.get("pdf"):
            pdf_path = Path(str(chart["pdf"]))
            if not pdf_path.is_absolute():
                indexed_pdf = base_dir / pdf_path
                pdf_path = indexed_pdf if indexed_pdf.exists() else pdf_path
            chart["pdf"] = str(pdf_path.resolve())
        crop_rel = Path(chart["crop_png"])
        crop_path = base_dir / crop_rel
        rel_stem = crop_rel.with_suffix("")
        if args.datasheet_root is not None:
            datasheet_root = args.datasheet_root
        elif chart.get("pdf"):
            datasheet_root = Path(str(chart["pdf"])).parent
        else:
            datasheet_root = base_dir
        print(f"digitize {chart['part']} diagram {chart['diagram']}: {crop_rel}")
        try:
            result = process_chart(
                chart,
                crop_path,
                out_dir,
                rel_stem,
                datasheet_root,
                debug_axis_overlays=args.debug_axis_overlays,
            )
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors.append({"crop": str(crop_path), "error": str(exc)})
        else:
            result.update({"part": chart["part"], "page": chart["page"], "diagram": chart["diagram"]})
            results.append(result)
            for trace in result["traces"]:
                print(f"  {trace['name']}: {trace['points']} sampled columns")

    (out_dir / "capacitance_digitization.json").write_text(json.dumps(results, indent=2) + "\n")
    (out_dir / "capacitance_digitization_errors.json").write_text(json.dumps(errors, indent=2) + "\n")
    print(f"wrote {out_dir / 'capacitance_digitization.json'}")
    if errors:
        print(f"wrote {out_dir / 'capacitance_digitization_errors.json'} with {len(errors)} errors")


if __name__ == "__main__":
    main()
