"""Vector-PDF trace extraction for MOSFET capacitance charts."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .capacitance_traces import _smooth_points, find_plot_box
from .capacitance_types import PlotBox, Trace, VectorEdge
from .capacitance_plot_box import _sparse_closed_frame
from .crop_transform import CropTransform

MIN_EXACT_COLOR_SOURCE_X_SPAN_FRACTION = 0.80

def extract_vector_trace_components(
    chart: dict[str, object], image: np.ndarray, plot: PlotBox
) -> list[Trace]:
    traces, _selection_method = extract_vector_trace_components_with_provenance(
        chart, image, plot
    )
    return traces


def extract_vector_trace_components_with_provenance(
    chart: dict[str, object], image: np.ndarray, plot: PlotBox
) -> tuple[list[Trace], str]:
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")

    pdf_path = Path(str(chart["pdf"]))
    transform = CropTransform.for_chart(chart, image.shape)
    plot_x0, plot_y0 = transform.to_pt(plot.x0, plot.y0)
    plot_x1, plot_y1 = transform.to_pt(plot.x1, plot.y1)
    plot_rect = fitz.Rect(
        plot_x0,
        plot_y0,
        plot_x1,
        plot_y1,
    )

    doc = fitz.open(pdf_path)
    page = doc[int(chart["page"]) - 1]
    drawings = page.get_drawings()

    def _build_candidates(
        components: list[list[tuple[float, float]]],
    ) -> list[tuple[float, list[tuple[int, int]]]]:
        built: list[tuple[float, list[tuple[int, int]]]] = []
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
                tuple(int(round(value)) for value in transform.to_px(x, y))
                for x, y in component
            ]
            points = _smooth_points(_resample_vector_trace_pixels(raw_points, plot), window=9)
            if len(points) < 8:
                continue
            px_span = max(x for x, _ in points) - min(x for x, _ in points)
            if px_span < plot.width * 0.35:
                continue
            built.append((_path_length(component), points))
        return built

    # First chain per stroke color: when a vendor colors each trace (TI red/
    # green/blue), color IS the trace identity, and color-blind endpoint
    # chaining merges curves where they converge or cross at low VDS. If that
    # yields fewer than three full candidates (a single curve drawn in mixed
    # shades would fragment), fall back to the original color-blind chaining.
    by_color: dict[tuple[float, ...], list[dict[str, object]]] = {}
    for drawing in drawings:
        color = drawing.get("color")
        key = tuple(round(float(c), 2) for c in color[:3]) if isinstance(color, (tuple, list)) else ()
        by_color.setdefault(key, []).append(drawing)
    per_color_candidate_groups: list[
        list[tuple[float, list[tuple[int, int]]]]
    ] = []
    for group in by_color.values():
        edges = _vector_curve_edges(group, plot_rect)
        if edges:
            group_candidates = _build_candidates(_chain_vector_components(edges))
            if group_candidates:
                per_color_candidate_groups.append(group_candidates)
    candidates = [
        candidate
        for group_candidates in per_color_candidate_groups
        for candidate in group_candidates
    ]
    selection_method = "color_components"
    if len(candidates) < 3:
        edges = _vector_curve_edges(drawings, plot_rect)
        candidates = _build_candidates(_chain_vector_components(edges))
        selection_method = "pooled_components"

    # Rescue complete source paths only after both chaining strategies fail.
    # Some Onsemi charts draw Ciss and Coss as separate black paths with an
    # exactly shared endpoint, so pooled chaining turns them into one branched
    # component. Require exactly three independently full-span, materially
    # non-horizontal source objects; otherwise retain the fail-closed result.
    if len(candidates) < 3:
        candidates_by_drawing: list[
            list[tuple[float, list[tuple[int, int]]]]
        ] = []
        for drawing in drawings:
            edges = _vector_curve_edges([drawing], plot_rect)
            components = [
                component
                for component in _chain_vector_components(edges)
                if _has_material_vertical_response(component, plot_rect.height)
            ]
            drawing_candidates = _build_candidates(components)
            # One PDF drawing must prove one complete curve. A drawing that
            # contains multiple plausible full-span paths is ambiguous and is
            # not allowed to contribute any candidate to this rescue.
            candidates_by_drawing.append(drawing_candidates)
        rescued = _select_exact_source_drawing_rescue(candidates_by_drawing)
        if rescued:
            candidates = rescued
            selection_method = "source_drawing_rescue"

    # Infineon also encodes each thick curve as one black filled polygon, then
    # paints a white inline-label box over the rendered stroke. Raster tracing
    # stops at that box although the owned vector path continues underneath.
    # Admit this only when exactly three independent dark filled objects each
    # prove one near-full-span centerline; frames/grid groups do not have the
    # required vertex density and a fourth candidate makes the rescue refuse.
    if len(candidates) < 3 and _has_right_extent_recovery(image, plot):
        filled_candidates: list[tuple[float, list[tuple[int, int]]]] = []
        for drawing in drawings:
            if not _is_dark_stroke(drawing.get("fill")):
                continue
            centerline = _filled_path_centerline(
                drawing,
                plot_rect,
                minimum_y_span_fraction=0.01,
                outside_x_margin_fraction=0.05,
            )
            built = _build_candidates([centerline]) if centerline else []
            if len(built) == 1:
                length, points = built[0]
                points = [
                    point
                    for point in points
                    if plot.x0 <= point[0] <= plot.x1
                    and plot.y0 <= point[1] <= plot.y1
                ]
                if len(points) >= 8:
                    filled_candidates.append((length, points))
        if len(filled_candidates) == 3:
            candidates = filled_candidates
            selection_method = "exact_filled_source_paths"

    if len(candidates) < 3:
        raise RuntimeError(f"found only {len(candidates)} vector curve candidates")

    candidates = sorted(candidates, key=lambda candidate: candidate[0], reverse=True)[:3]
    span_fractions = [
        (max(x for x, _ in points) - min(x for x, _ in points)) / max(1, plot.width)
        for _, points in candidates
    ]
    if min(span_fractions) < 0.9 and not (
        selection_method == "color_components"
        and _short_color_source_span_is_proven(
            per_color_candidate_groups, span_fractions
        )
    ):
        raise RuntimeError(
            "vector candidates do not span full plot: "
            + ", ".join(f"{span:.2f}" for span in sorted(span_fractions))
        )
    if min(span_fractions) < 0.9:
        selection_method = "exact_color_components_short_source_span"
    ordered = sorted((points for _, points in candidates), key=_right_edge_y_pixels)
    names = ["Ciss", "Coss", "Crss"]
    traces: list[Trace] = []
    for name, points in zip(names, ordered):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        bbox_local = (min(xs) - plot.x0, min(ys) - plot.y0, max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
        traces.append(Trace(name=name, area=len(points), bbox=bbox_local, points=points))
    return traces, selection_method


def _load_fitz():
    try:
        import fitz  # type: ignore

        return fitz
    except ImportError:
        return None


def _has_right_extent_recovery(image: np.ndarray, plot: PlotBox) -> bool:
    """Prove the capacitance-specific detector extended an interior right rail."""

    gray = image if image.ndim == 2 else np.min(image, axis=2).astype(np.uint8)
    if _sparse_closed_frame(gray) is not None:
        return False
    try:
        generic = find_plot_box(gray)
    except RuntimeError:
        return False
    return plot.x1 - generic.x1 >= 0.02 * image.shape[1]


def _vector_curve_edges(
    drawings: list[dict[str, object]],
    plot_rect,
    *,
    min_stroke_width: float = 0.8,
    allow_neutral_gray: bool = False,
) -> list[VectorEdge]:
    edges: list[VectorEdge] = []
    expanded = plot_rect + (-1.5, -1.5, 1.5, 1.5)
    for drawing in drawings:
        if drawing.get("type") != "s":
            continue
        color = drawing.get("color")
        if not _is_curve_stroke_color(color) and not (
            allow_neutral_gray and _is_neutral_gray_stroke(color)
        ):
            continue
        width = float(drawing.get("width") or 0.0)
        if width < min_stroke_width or width > 2.2:
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


def _filled_path_centerline(
    drawing: dict[str, object],
    plot_rect,
    *,
    bin_width: float = 0.8,
    minimum_y_span_fraction: float = 0.1,
    outside_x_margin_fraction: float = 0.0,
) -> list[tuple[float, float]]:
    """Recover the centerline of a filled polygon used as a thick curve.

    Some vector datasheets encode a curve as a sequence of overlapping filled
    wedges instead of a stroked path.  Grouping the polygon vertices by x and
    taking their median preserves the drawn center without tracing either edge.
    Filled rectangles and plot frames are rejected by the required x/y span.
    """

    if (
        drawing.get("type") != "f"
        or bin_width <= 0
        or minimum_y_span_fraction < 0
        or outside_x_margin_fraction < 0
    ):
        return []
    buckets: dict[int, list[tuple[float, float]]] = {}
    x_margin = max(1.5, outside_x_margin_fraction * plot_rect.width)
    expanded = plot_rect + (-x_margin, -1.5, x_margin, 1.5)
    for item in drawing.get("items", []):
        if item[0] == "l":
            vertices = (item[1], item[2])
        elif item[0] == "c":
            vertices = (item[1], item[2], item[3], item[4])
        else:
            continue
        for vertex in vertices:
            point = (float(vertex.x), float(vertex.y))
            if not expanded.contains(point):
                continue
            key = int(round(point[0] / bin_width))
            buckets.setdefault(key, []).append(point)
    centerline = []
    for values in buckets.values():
        centerline.append(
            (
                float(np.median([point[0] for point in values])),
                float(np.median([point[1] for point in values])),
            )
        )
    centerline.sort()
    if len(centerline) < 8:
        return []
    x_span = centerline[-1][0] - centerline[0][0]
    ys = [point[1] for point in centerline]
    if (
        x_span < 0.35 * plot_rect.width
        or max(ys) - min(ys) < minimum_y_span_fraction * plot_rect.height
    ):
        return []
    return centerline


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
    # Renesas and similar vector datasheets use the same dark neutral ink for
    # curves and grids.  Stroke width and boundary/full-span checks downstream
    # reject their thin gridlines and frame; color alone must not discard the
    # 1 pt curve before those structural guards can run.
    if max(rgb) < 0.22:
        return True
    # Strongly saturated strokes are curves regardless of brightness: TI draws
    # Ciss/Coss/Crss in pure red/green/blue, which the darkness cap below would
    # reject. Gridlines are always near-gray (max-min ~ 0), so saturation alone
    # separates them.
    if max(rgb) - min(rgb) > 0.5:
        return True
    # TI also uses a neutral light-gray stroke for Crss.  Its plot grid is much
    # thinner and is rejected by the width/orthogonality gates downstream;
    # admitting this bounded neutral tone preserves the actual full-span curve.
    if max(rgb) - min(rgb) < 0.03 and 0.68 <= sum(rgb) / 3 <= 0.80:
        return True
    # Some datasheets draw capacitance curves in a dark teal with solid/dashed
    # style classes. Gray gridlines have very low saturation; accept saturated
    # dark colors as curve strokes while rejecting gray axes/grid.
    return max(rgb) < 0.65 and (max(rgb) - min(rgb)) > 0.12


def _is_neutral_gray_stroke(color: object) -> bool:
    """Recognize a neutral gray only for a caller's panel-local rescue path."""

    if not isinstance(color, tuple) or len(color) < 3:
        return False
    rgb = [float(channel) for channel in color[:3]]
    mean = sum(rgb) / 3
    return 0.22 <= mean <= 0.80 and max(rgb) - min(rgb) <= 0.03


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


def _has_material_vertical_response(
    points: list[tuple[float, float]], plot_height: float
) -> bool:
    """Reject a full-width grid or flat annotation from path-isolation rescue."""

    if not points or plot_height <= 0:
        return False
    ys = [point[1] for point in points]
    return max(ys) - min(ys) >= plot_height * 0.01


def _select_exact_source_drawing_rescue(
    candidates_by_drawing: list[list[tuple[float, list[tuple[int, int]]]]],
) -> list[tuple[float, list[tuple[int, int]]]]:
    """Return exactly three candidates proven by three unambiguous drawings."""

    proven = [candidates[0] for candidates in candidates_by_drawing if len(candidates) == 1]
    return proven if len(proven) == 3 else []


def _exact_color_source_ownership(
    candidate_groups: list[list[tuple[float, list[tuple[int, int]]]]],
) -> bool:
    """Require exactly one complete candidate from each of three colors."""

    return len(candidate_groups) == 3 and all(
        len(group) == 1 for group in candidate_groups
    )


def _short_color_source_span_is_proven(
    candidate_groups: list[list[tuple[float, list[tuple[int, int]]]]],
    span_fractions: list[float],
) -> bool:
    """Admit a short printed trace only with three exact color owners."""

    return (
        _exact_color_source_ownership(candidate_groups)
        and len(span_fractions) == 3
        and min(span_fractions) >= MIN_EXACT_COLOR_SOURCE_X_SPAN_FRACTION
    )


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
