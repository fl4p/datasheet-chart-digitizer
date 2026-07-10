"""Vector-PDF trace extraction for MOSFET capacitance charts."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .capacitance_traces import _smooth_points
from .capacitance_types import PlotBox, Trace, VectorEdge

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

