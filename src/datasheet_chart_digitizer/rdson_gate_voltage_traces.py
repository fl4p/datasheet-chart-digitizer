"""Curve traces and curve-parameter labels for RDS(on)-versus-VGS panels.

Two trace sources, tried in order, the same contract as the other chart
classes:

  * VECTOR: stroked source paths inside the plot frame. Grid rules and tick
    marks are recognised by geometry (axis-aligned, long or edge-bound), never
    by colour, and dropped; what remains is grouped by stroke style and chained
    end-to-end into polylines. Short near-horizontal chains are legend
    swatches, not data, and are returned separately so a legend can bind a
    label to a style.
  * RASTER: when the page carries no usable stroked curve (charts embedded as
    images, or vendors that fill their "lines" as thin polygons), curves are
    tracked column by column through an ink mask of the rendered crop with
    grid rows/columns and owned text boxes removed.

Label binding (``bind_labels``) is deliberately conservative. A parameter
(Tj, or ID) is bound to a curve by a legend swatch of the curve's own stroke
style, or by proximity when the label is clearly nearer to one curve than to
any other. Anything else leaves the curve's parameter ``None`` -- "unknown" --
and the panel goes to review. Physical order (a hotter die has the higher
RDS(on)) is used only to CONTRADICT a binding, never to create one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from .capacitance_types import PlotBox
from .crop_transform import CropTransform

MIN_CURVE_SPAN_FRACTION = 0.18
SWATCH_MAX_SPAN_FRACTION = 0.14
AXIS_ALIGNED_TOLERANCE_PT = 0.25
GRID_RULE_MIN_FRACTION = 0.45
CHAIN_JOIN_PT = 0.8
RASTER_INK_GRAY = 135
RASTER_COLOR_SATURATION = 80
RASTER_EDGE_MARGIN_PX = 3
RASTER_MAX_GAP_COLUMNS = 14
RASTER_RUN_TOLERANCE_PX = 3
RASTER_RUN_BRIDGE_PX = 5          # an erased 3-px grid rule must not split a curve
RASTER_RULE_FLATNESS_PX = 1.5
LABEL_BIND_MAX_PX_FRACTION = 0.12
LABEL_BIND_RATIO = 1.6


@dataclass
class Trace:
    points_px: list[tuple[float, float]]
    method: str
    style: tuple | None = None
    merged_columns: int = 0
    params: dict[str, float | None] = field(default_factory=dict)
    binding: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Swatch:
    style: tuple
    x0: float
    x1: float
    y: float


@dataclass(frozen=True)
class Leader:
    """A thin non-data stroke (label leader line or arrow), crop pixels."""

    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Label:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    params: dict


# --------------------------------------------------------------------------- vector


def vector_traces(
    page, transform: CropTransform, plot: PlotBox
) -> tuple[list[Trace], list[Swatch], list[Leader]]:
    """Stroked source paths inside the plot frame, as crop-pixel polylines.

    Returns the curves, the legend swatches drawn in a curve's own stroke
    style, and every other short stroke (label leader lines and arrowheads).
    """
    fx0, fy0 = transform.to_pt(plot.x0, plot.y0)
    fx1, fy1 = transform.to_pt(plot.x1, plot.y1)
    width_pt, height_pt = fx1 - fx0, fy1 - fy0
    pieces: dict[tuple, list[list[tuple[float, float]]]] = {}
    filled: list[Trace] = []
    for drawing in page.get_drawings():
        if drawing.get("type") == "f":
            outline = _filled_outline_polygon(drawing, (fx0, fy0, fx1, fy1))
            if outline is not None:
                filled.append(Trace(
                    _polygon_centerline_px([transform.to_px(x, y) for x, y in outline], plot),
                    "vector_filled_outline",
                    (tuple(round(float(c), 2) for c in drawing.get("fill") or ()), "fill", ""),
                ))
            continue
        if drawing.get("type") not in {"s", "fs"} or drawing.get("color") is None:
            continue
        color = tuple(round(float(c), 2) for c in drawing["color"])
        if min(color) >= 0.9:
            continue
        style = (color, round(float(drawing.get("width") or 0.0), 2), str(drawing.get("dashes") or ""))
        for item in drawing.get("items", []):
            if item[0] == "l":
                points = [(item[1].x, item[1].y), (item[2].x, item[2].y)]
            elif item[0] == "c":
                points = _bezier(item[1], item[2], item[3], item[4])
            else:
                continue
            for run in _clip_polyline(points, (fx0 - 0.3, fy0 - 0.3, fx1 + 0.3, fy1 + 0.3)):
                if _is_rule(run, width_pt, height_pt):
                    continue  # tick marks are left in: alone they chain into short non-curves
                pieces.setdefault(style, []).append(run)
    traces: list[Trace] = []
    short: list[tuple[tuple, list[tuple[float, float]]]] = []
    for style, runs in pieces.items():
        for chain in _chain(runs):
            xs = [p[0] for p in chain]
            span = max(xs) - min(xs)
            if span >= MIN_CURVE_SPAN_FRACTION * width_pt:
                px = [transform.to_px(x, y) for x, y in chain]
                traces.append(Trace(densify(_as_function_of_x(px)), "vector", style))
            else:
                short.append((style, chain))
    if not traces and filled:
        traces = filled
    curve_styles = {trace.style for trace in traces}
    swatches: list[Swatch] = []
    leaders: list[Leader] = []
    for style, chain in short:
        xs = [p[0] for p in chain]
        ys = [p[1] for p in chain]
        if style in curve_styles and max(ys) - min(ys) <= 0.6 and max(xs) - min(xs) <= SWATCH_MAX_SPAN_FRACTION * width_pt:
            x0p, yp = transform.to_px(min(xs), float(np.mean(ys)))
            x1p, _ = transform.to_px(max(xs), float(np.mean(ys)))
            swatches.append(Swatch(style, x0p, x1p, yp))
        elif style not in curve_styles:
            leaders.append(Leader(tuple(transform.to_px(x, y) for x, y in chain)))
    return traces, swatches, leaders


def densify(points: list[tuple[float, float]], step_px: float = 1.0) -> list[tuple[float, float]]:
    """Sample a vector polyline at <= step_px along its drawn straight segments.

    The segments ARE the source curve (the PDF draws straight lines between
    its vertices), so sampling along them adds no information and loses none;
    it only lets the readout-gap guard, which exists for raster traces, see a
    continuous trace.
    """
    out: list[tuple[float, float]] = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        n = max(1, int(math.ceil(math.hypot(x1 - x0, y1 - y0) / step_px)))
        for i in range(n):
            t = i / n
            out.append((round(x0 + t * (x1 - x0), 3), round(y0 + t * (y1 - y0), 3)))
    if points:
        out.append(points[-1])
    return out


def _filled_outline_polygon(drawing, frame) -> list[tuple[float, float]] | None:
    """Vertices of a thick line that the PDF paints as a filled outline polygon.

    Only thin, long, non-white polygons inside the frame qualify (mean
    thickness = 2 * area / perimeter <= 2 pt); rectangles (grid rules painted
    as fills) and white label backgrounds do not.
    """
    fill = drawing.get("fill")
    if fill is None or min(fill) >= 0.9:
        return None
    items = drawing.get("items", [])
    if not items or any(item[0] not in {"l", "c"} for item in items):
        return None
    vertices: list[tuple[float, float]] = []
    for item in items:
        pts = [(item[1].x, item[1].y), (item[2].x, item[2].y)] if item[0] == "l" else _bezier(item[1], item[2], item[3], item[4], 6)
        for point in pts:
            if not vertices or math.dist(point, vertices[-1]) > 1e-6:
                vertices.append(point)
    if len(vertices) < 12:
        return None
    xs = [p[0] for p in vertices]
    ys = [p[1] for p in vertices]
    width_pt = frame[2] - frame[0]
    if min(xs) < frame[0] - 1 or max(xs) > frame[2] + 1 or min(ys) < frame[1] - 1 or max(ys) > frame[3] + 1:
        return None
    if max(xs) - min(xs) < MIN_CURVE_SPAN_FRACTION * width_pt:
        return None
    area = 0.5 * abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(vertices, vertices[1:] + vertices[:1])))
    perimeter = sum(math.dist(a, b) for a, b in zip(vertices, vertices[1:] + vertices[:1]))
    if perimeter <= 0 or 2.0 * area / perimeter > 2.0:
        return None
    return vertices


def _polygon_centerline_px(polygon_px: list[tuple[float, float]], plot: PlotBox) -> list[tuple[float, float]]:
    """Column centres of ONE filled line polygon, rasterised on its own.

    One polygon is one curve, so no tracking is involved: each column's ink is
    that curve's cross-section, and its centre is the curve at that column.
    """
    mask = np.zeros((plot.y1 + 4, plot.x1 + 4), dtype=np.uint8)
    cv2.fillPoly(mask, [np.round(np.asarray(polygon_px) * 4).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=2)
    points = []
    for x in range(plot.x0, plot.x1 + 1):
        rows = np.flatnonzero(mask[:, x])
        if rows.size:
            points.append((float(x), float(0.5 * (rows[0] + rows[-1]))))
    return points


def _bezier(p0, p1, p2, p3, steps: int = 12) -> list[tuple[float, float]]:
    out = []
    for i in range(steps + 1):
        t = i / steps
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
        out.append((a * p0.x + b * p1.x + c * p2.x + d * p3.x, a * p0.y + b * p1.y + c * p2.y + d * p3.y))
    return out


def _clip_polyline(points, rect) -> list[list[tuple[float, float]]]:
    """Clip a polyline to the frame, KEEPING the in-frame part of crossing segments.

    Each segment is clipped with Liang-Barsky, so a segment that enters (or
    leaves) the frame contributes the piece between the frame edge and its
    inside end. Dropping the whole segment instead cost IRLB8743's 125 C curve
    everything between its first in-frame vertex and the top frame, including
    its 3.3 V point (review finding 1, 2026-09-28).
    """
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for a, b in zip(points, points[1:]):
        piece = _clip_segment(a, b, rect)
        if piece is None:
            if current:
                runs.append(current)
                current = []
            continue
        start, end = piece
        if current and math.dist(current[-1], start) > 1e-6:
            runs.append(current)
            current = []
        if not current:
            current = [start]
        current.append(end)
        if end != b:  # the segment left the frame
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return [run for run in runs if len(run) >= 2 and math.dist(run[0], run[-1]) > 1e-6]


def _clip_segment(a, b, rect):
    """Liang-Barsky clip of segment a-b to rect; None when it misses the rect."""
    x0, y0, x1, y1 = rect
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    start = a if t0 == 0.0 else (a[0] + t0 * dx, a[1] + t0 * dy)
    end = b if t1 == 1.0 else (a[0] + t1 * dx, a[1] + t1 * dy)
    return start, end


def _is_rule(run, width_pt: float, height_pt: float) -> bool:
    """A long axis-aligned stroke: a grid rule, never part of a curve.

    Short axis-aligned strokes at the frame are NOT dropped here: a curve's
    last flat segment ending on the right frame looks exactly like a tick
    mark until it is chained to the rest of the curve (IRLB8748 lost its
    9.8..10 V end that way).
    """
    xs = [p[0] for p in run]
    ys = [p[1] for p in run]
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)
    if dy <= AXIS_ALIGNED_TOLERANCE_PT and dx >= GRID_RULE_MIN_FRACTION * width_pt:
        return True
    return dx <= AXIS_ALIGNED_TOLERANCE_PT and dy >= GRID_RULE_MIN_FRACTION * height_pt


def _chain(runs: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join same-style pieces whose endpoints meet; ambiguous joins stay apart."""
    chains = [list(run) for run in runs]
    merged = True
    while merged:
        merged = False
        for i in range(len(chains)):
            for j in range(len(chains)):
                if i == j or not chains[i] or not chains[j]:
                    continue
                a, b = chains[i], chains[j]
                if math.dist(a[-1], b[0]) <= CHAIN_JOIN_PT:
                    chains[i] = a + b[1:]
                    chains[j] = []
                    merged = True
                elif math.dist(a[-1], b[-1]) <= CHAIN_JOIN_PT:
                    chains[i] = a + list(reversed(b))[1:]
                    chains[j] = []
                    merged = True
        chains = [c for c in chains if c]
    return chains


def _as_function_of_x(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Order a polyline left to right (the curve is single-valued in VGS)."""
    if points and points[0][0] > points[-1][0]:
        points = list(reversed(points))
    return [(round(x, 3), round(y, 3)) for x, y in points]


def backtrack_px(points: list[tuple[float, float]]) -> float:
    """Largest leftward step of a left-to-right polyline (0 when single-valued)."""
    worst = 0.0
    running = -math.inf
    for x, _y in points:
        worst = max(worst, running - x)
        running = max(running, x)
    return worst


# --------------------------------------------------------------------------- raster


def raster_traces(
    image_bgr: np.ndarray,
    plot: PlotBox,
    grid_x: tuple[float, ...],
    grid_y: tuple[float, ...],
    text_boxes_px: list[tuple[float, float, float, float]],
) -> list[Trace]:
    """Track dark or coloured curves column by column through the plot.

    Each column's ink runs are assigned ONE-TO-ONE to the active tracks whose
    last run they continue (nearest prediction first). A track that finds no
    run of its own waits up to ``RASTER_MAX_GAP_COLUMNS`` (a grid rule or a
    label box erased from the mask) and is counted as merged while another
    track holds the run it would have taken. Fragments are then joined when
    exactly one continuation fits, and near-duplicate tracks are dropped.
    """
    mask = raster_ink_mask(image_bgr, plot, grid_x, grid_y, text_boxes_px)
    erased_rows = _erased_rows(mask.shape[0], grid_y, mask)
    width = plot.x1 - plot.x0
    tracks: list[dict] = []
    active: list[dict] = []
    for x in range(plot.x0 + RASTER_EDGE_MARGIN_PX, plot.x1 - RASTER_EDGE_MARGIN_PX + 1):
        runs = _column_runs(mask[:, x], erased_rows)
        pairs = []
        for t_index, track in enumerate(active):
            for r_index, run in enumerate(runs):
                distance = _continues(track, run, x)
                if distance is not None:
                    pairs.append((distance, t_index, r_index))
        pairs.sort()
        taken_tracks: set[int] = set()
        taken_runs: dict[int, dict] = {}
        for distance, t_index, r_index in pairs:
            if t_index in taken_tracks:
                continue
            if r_index in taken_runs:
                if active[t_index].get("blocked_by") is None:
                    active[t_index]["blocked_by"] = taken_runs[r_index]
                active[t_index]["merged"] += 1
                taken_tracks.add(t_index)
                continue
            taken_tracks.add(t_index)
            taken_runs[r_index] = active[t_index]
            _extend(active[t_index], runs[r_index], x)
            active[t_index]["blocked_by"] = None
        for r_index, (r0, r1) in enumerate(runs):
            if r_index in taken_runs:
                continue
            y = 0.5 * (r0 + r1)
            track = {"points": [(x, y)], "x": x, "y": y, "run": (r0, r1), "slope": 0.0, "merged": 0}
            tracks.append(track)
            active.append(track)
        active = [t for t in active if x - t["x"] <= RASTER_MAX_GAP_COLUMNS]
    for track in tracks:
        track["own"] = len(track["points"])
    _extend_converged(tracks)
    tracks = _join_fragments([t for t in tracks if len(t["points"]) >= 3])
    height = plot.y1 - plot.y0
    for track in tracks:
        _cut_leader_ends(track, text_boxes_px)
    kept = [t for t in tracks if len(t["points"]) >= 3 and _long_enough(t, width, height) and not _is_straight_rule(t)]
    kept = _drop_duplicates(kept)
    return [Trace([(float(px), float(py)) for px, py in t["points"]], "raster", None, t["merged"]) for t in kept]


def _extend_converged(tracks: list[dict]) -> None:
    """A track that ended while another track held its run has run INTO that curve.

    Two curves whose lines meet within a stroke width leave one ink run; the
    tracker gives it to one of them. The other is extended with a copy of the
    holder's later points and the copied columns are counted as merged, so the
    shared stretch is served for both curves AND reported as not separable.
    """
    for track in tracks:
        holder = track.get("blocked_by")
        if holder is None or holder is track:
            continue
        last_x, last_y = track["points"][-1]
        tail = [p for p in holder["points"] if p[0] > last_x]
        if not tail or abs(tail[0][1] - last_y) > 4.0 + 0.5 * (tail[0][0] - last_x):
            continue
        track["points"].extend(tail)
        track["merged"] += len(tail)
        track["converged"] = True
        track["x"], track["y"], track["run"], track["slope"] = holder["x"], holder["y"], holder["run"], holder["slope"]


def _cut_leader_ends(track: dict, text_boxes_px) -> None:
    """Cut a straight end that runs into a label box: that is its leader line.

    A leader drawn from a label to its curve joins the curve's ink, and the
    column tracker can follow it off the curve (RQ6E080AJ: the "ID = 4.0A"
    leader). A leader is straight; a curve end is not. An end is cut when its
    last >= 15 px fit one straight line within 0.8 px and finish within 25 px
    of a label box.
    """
    for reverse in (False, True):
        points = track["points"][::-1] if reverse else track["points"]
        if len(points) < 20:
            return
        end = points[-1]
        if not any(
            box[0] - 25 <= end[0] <= box[2] + 25 and box[1] - 25 <= end[1] <= box[3] + 25
            for box in text_boxes_px
        ):
            continue
        n = 15
        while n < len(points) and _straight(points[-(n + 1):]):
            n += 1
        if n >= 15 and _straight(points[-n:]):
            kept = points[:-n]
            track["points"] = kept[::-1] if reverse else kept
            track["leader_cut"] = track.get("leader_cut", 0) + n


def _straight(points) -> bool:
    xs = np.asarray([p[0] for p in points], dtype=float)
    ys = np.asarray([p[1] for p in points], dtype=float)
    if xs.max() - xs.min() < 10:
        return False
    slope, intercept = np.polyfit(xs, ys, 1)
    return float(np.max(np.abs(slope * xs + intercept - ys))) <= 0.8


def _is_straight_rule(track: dict) -> bool:
    """A dead-flat horizontal track is a box edge or rule, not an RDS(VGS) curve."""
    ys = [p[1] for p in track["points"]]
    return max(ys) - min(ys) <= RASTER_RULE_FLATNESS_PX


def _long_enough(track: dict, width: int, height: int) -> bool:
    """Wide enough to be a curve, or a steep sub-threshold branch spanning the height."""
    xs = [p[0] for p in track["points"]]
    ys = [p[1] for p in track["points"]]
    x_span, y_span = max(xs) - min(xs), max(ys) - min(ys)
    return x_span >= MIN_CURVE_SPAN_FRACTION * width or (
        y_span >= 0.25 * height and x_span >= 0.02 * width
    )


def _continues(track: dict, run: tuple[int, int], x: int) -> float | None:
    gap = x - track["x"]
    shift = track["slope"] * gap
    tolerance = RASTER_RUN_TOLERANCE_PX + 1.0 * (gap - 1) + 0.3 * abs(shift)
    last0, last1 = track["run"]
    r0, r1 = run
    if r0 - tolerance <= last1 + shift and r1 + tolerance >= last0 + shift:
        return abs(0.5 * (r0 + r1) - (track["y"] + shift))
    return None


def _extend(track: dict, run: tuple[int, int], x: int) -> None:
    y = 0.5 * (run[0] + run[1])
    step = x - track["x"]
    track["slope"] = 0.6 * track["slope"] + 0.4 * (y - track["y"]) / max(1, step)
    track["points"].append((x, y))
    track["x"], track["y"], track["run"] = x, y, run


def _join_fragments(tracks: list[dict]) -> list[dict]:
    """Join fragments that continue each other, including curves that converge.

    A->B is joined when B is A's unique continuation and A B's unique source.
    When k >= 2 tracks all end into the same unique continuation B (two curves
    whose lines run together within a stroke width), EACH of them is extended
    with a copy of B and the copied columns are counted as merged, which the
    caller reports: the served data is then honest that the curves were not
    separable there.
    """
    def fits(a: dict, b: dict) -> bool:
        (ax, ay), (bx, by) = a["points"][-1], b["points"][0]
        dx = bx - ax
        if not -2 <= dx <= 2 * RASTER_MAX_GAP_COLUMNS:
            return False
        if b["points"][-1][0] <= ax + 2:
            return False
        if by < ay - 4.0:
            # RDS(on) does not rise with VGS: in pixels a continuation never climbs.
            return False
        predicted = ay + a["slope"] * max(dx, 0)
        return abs(by - predicted) <= 6.0 + 1.2 * abs(a["slope"]) * max(dx, 1)

    changed = True
    while changed:
        changed = False
        for b in tracks:
            sources = [a for a in tracks if a is not b and fits(a, b)]
            if not sources:
                continue
            if any(len([c for c in tracks if c is not a and fits(a, c)]) != 1 for a in sources):
                continue
            for a in sources:
                tail = [p for p in b["points"] if p[0] > a["points"][-1][0]]
                a["points"].extend(tail)
                a["merged"] += b["merged"] + (len(tail) if len(sources) > 1 else 0)
                if len(sources) > 1:
                    a["converged"] = True
                elif not a.get("converged"):
                    a["own"] = len(a["points"])
                a["x"], a["y"], a["run"], a["slope"] = b["x"], b["y"], b["run"], b["slope"]
            tracks = [t for t in tracks if t is not b]
            changed = True
            break
    return tracks


def _drop_duplicates(tracks: list[dict]) -> list[dict]:
    """Drop a track that runs within 2 px of a longer one over most of its span."""
    ordered = sorted(tracks, key=lambda t: -len(t["points"]))
    kept: list[dict] = []
    for track in ordered:
        own = track["points"][: track.get("own", len(track["points"]))] if track.get("converged") else track["points"]
        xs = np.asarray([p[0] for p in own], dtype=float)
        ys = np.asarray([p[1] for p in own], dtype=float)
        duplicate = False
        for other in kept:
            ox = np.asarray([p[0] for p in other["points"]], dtype=float)
            oy = np.asarray([p[1] for p in other["points"]], dtype=float)
            inside = (xs >= ox[0]) & (xs <= ox[-1])
            if inside.sum() < 0.8 * len(xs):
                continue
            close = np.abs(np.interp(xs[inside], ox, oy) - ys[inside]) <= 2.0
            if close.mean() >= 0.9:
                duplicate = True
                break
        if not duplicate:
            kept.append(track)
    return kept


def raster_ink_mask(image_bgr, plot: PlotBox, grid_x, grid_y, text_boxes_px) -> np.ndarray:
    """Curve ink inside the plot: grid rules, label boxes and owned text removed.

    The darkness threshold sits below the measured grid-rule core so a grey
    grid never enters the mask; a grid as dark as the curves is removed by
    erasing its rule positions instead.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    grid_core = _grid_core_gray(gray, plot, grid_x, grid_y)
    threshold = min(RASTER_INK_GRAY, 0.8 * grid_core) if grid_core >= 110 else RASTER_INK_GRAY
    ink = (gray < threshold) | ((hsv[:, :, 1] > RASTER_COLOR_SATURATION) & (hsv[:, :, 2] < 240))
    mask = np.zeros_like(ink)
    m = RASTER_EDGE_MARGIN_PX
    mask[plot.y0 + m : plot.y1 - m + 1, plot.x0 + m : plot.x1 - m + 1] = ink[
        plot.y0 + m : plot.y1 - m + 1, plot.x0 + m : plot.x1 - m + 1
    ]
    for box in _enclosing_text_boxes(ink, text_boxes_px, plot, grid_x, grid_y):
        bx0, by0, bx1, by1 = box
        mask[max(0, by0 - 3) : by1 + 4, max(0, bx0 - 3) : bx1 + 4] = False
    for gx in grid_x:
        column = int(round(gx))
        _erase_rule(mask, column, vertical=True)
    for gy in grid_y:
        row = int(round(gy))
        _erase_rule(mask, row, vertical=False)
    for x0, y0, x1, y1 in text_boxes_px:
        mask[max(0, int(y0) - 2) : int(y1) + 3, max(0, int(x0) - 2) : int(x1) + 3] = False
    _erase_boxes(mask, plot)
    return mask


def _enclosing_text_boxes(ink: np.ndarray, text_boxes_px, plot: PlotBox, grid_x=(), grid_y=()) -> list[tuple[int, int, int, int]]:
    """Rectangles drawn around in-plot text (condition legends such as "Ta=25C / Pulsed").

    Text boxes within one line height of each other are merged first; a
    rectangle is accepted only when an ink rule is found on ALL four sides
    within 90 px of the text, each covering >= 85 % of that side.
    """
    boxes = [list(map(int, b)) for b in text_boxes_px]
    merged: list[list[int]] = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        for other in merged:
            height = max(box[3] - box[1], other[3] - other[1])
            overlap = min(box[2], other[2]) - max(box[0], other[0])
            narrow = min(box[2] - box[0], other[2] - other[0])
            if -2 <= box[1] - other[3] <= 0.8 * height and overlap >= 0.3 * narrow:
                other[0], other[1] = min(other[0], box[0]), min(other[1], box[1])
                other[2], other[3] = max(other[2], box[2]), max(other[3], box[3])
                break
        else:
            merged.append(list(box))
    found = []
    reach = 90
    for x0, y0, x1, y1 in merged:
        if not (plot.x0 <= x0 and x1 <= plot.x1 and plot.y0 <= y0 and y1 <= plot.y1):
            continue

        def row_rule(rows):
            for r in rows:
                if 0 <= r < ink.shape[0] and ink[r, max(0, x0 - 2) : x1 + 3].mean() >= 0.85:
                    return r, any(abs(r - g) <= 4 for g in grid_y)
            return None, False

        def col_rule(cols, top, bottom):
            for c in cols:
                if 0 <= c < ink.shape[1] and ink[top : bottom + 1, c].mean() >= 0.85:
                    return c, any(abs(c - g) <= 4 for g in grid_x)
            return None, False

        top, top_grid = row_rule(range(y0 - 1, y0 - reach, -1))
        bottom, bottom_grid = row_rule(range(y1 + 1, y1 + reach))
        if top is None or bottom is None:
            continue
        left, left_grid = col_rule(range(x0 - 1, x0 - reach, -1), top, bottom)
        right, right_grid = col_rule(range(x1 + 1, x1 + reach), top, bottom)
        if left is None or right is None:
            continue
        if top_grid + bottom_grid + left_grid + right_grid > 1:
            continue  # a grid cell around a label, not a drawn box
        found.append((left, top, right, bottom))
    return found


def _grid_core_gray(gray, plot: PlotBox, grid_x, grid_y) -> float:
    """Median darkest value across interior grid rules (255 when there are none)."""
    samples = []
    for gx in grid_x:
        x = int(round(gx))
        if plot.x0 + 5 < x < plot.x1 - 5:
            band = gray[plot.y0 + 5 : plot.y1 - 5, max(0, x - 3) : x + 4]
            samples.extend(np.percentile(band.min(axis=1), [50]).tolist())
    for gy in grid_y:
        y = int(round(gy))
        if plot.y0 + 5 < y < plot.y1 - 5:
            band = gray[max(0, y - 3) : y + 4, plot.x0 + 5 : plot.x1 - 5]
            samples.extend(np.percentile(band.min(axis=0), [50]).tolist())
    return float(np.median(samples)) if samples else 255.0


def _erase_boxes(mask: np.ndarray, plot: PlotBox) -> None:
    """Erase closed rectangular boxes (condition legends) and their contents."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    width, height = plot.x1 - plot.x0, plot.y1 - plot.y0
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if w < 0.08 * width or h < 0.04 * height:
            continue
        component = labels[y : y + h, x : x + w] == index
        border = np.zeros_like(component)
        border[:4, :] = border[-4:, :] = True
        border[:, :4] = border[:, -4:] = True
        on_border = (component & border).sum() / max(1, component.sum())
        rows_filled = component[:4, :].any(axis=0).mean(), component[-4:, :].any(axis=0).mean()
        cols_filled = component[:, :4].any(axis=1).mean(), component[:, -4:].any(axis=1).mean()
        if on_border >= 0.8 and min(rows_filled + cols_filled) >= 0.9:
            mask[y : y + h, x : x + w] = False


def _erase_rule(mask: np.ndarray, index: int, *, vertical: bool) -> None:
    """Erase a grid rule over its measured thickness (the rows/columns it fills)."""
    profile = mask.mean(axis=0) if vertical else mask.mean(axis=1)
    size = profile.shape[0]
    if not 0 <= index < size:
        return
    lo = hi = index
    while lo - 1 >= 0 and index - lo < 4 and profile[lo - 1] >= 0.35:
        lo -= 1
    while hi + 1 < size and hi - index < 4 and profile[hi + 1] >= 0.35:
        hi += 1
    lo, hi = min(lo, index - 1), max(hi, index + 1)
    if vertical:
        mask[:, max(0, lo) : hi + 1] = False
    else:
        mask[max(0, lo) : hi + 1, :] = False


def _erased_rows(height: int, grid_y, mask: np.ndarray) -> np.ndarray:
    """Rows emptied by horizontal rule erasure (fully blank inside the plot)."""
    rows = np.zeros(height, dtype=bool)
    blank = ~mask.any(axis=1)
    for gy in grid_y:
        y = int(round(gy))
        lo = hi = y
        while lo - 1 >= 0 and y - lo < 6 and blank[lo - 1]:
            lo -= 1
        while hi + 1 < height and hi - y < 6 and blank[hi + 1]:
            hi += 1
        if 0 <= y < height and blank[y]:
            rows[lo : hi + 1] = True
    return rows


def _column_runs(column: np.ndarray, erased_rows: np.ndarray | None = None) -> list[tuple[int, int]]:
    ys = np.flatnonzero(column)
    if ys.size == 0:
        return []
    runs = []
    start = prev = int(ys[0])
    for y in ys[1:]:
        y = int(y)
        bridged = erased_rows is not None and bool(erased_rows[prev + 1 : y].all())
        if y - prev > RASTER_RUN_BRIDGE_PX and not bridged:
            runs.append((start, prev))
            start = y
        prev = y
    runs.append((start, prev))
    return runs


# --------------------------------------------------------------------------- labels

_TEMP_RE = re.compile(
    r"(?:\bT\s*[JjCcAa]?\s*[=:]\s*)?([+\-−]?\d{1,3}(?:\.\d+)?)\s*(?:[°º˚*]\s*C|℃|\s?deg\s*C)",
)
_TEMP_PREFIXED_RE = re.compile(r"\bT\s*[JjCcAa.,_]?\s*[=:]\s*([+\-−]?\d{1,3}(?:\.\d+)?)")
# OCR spells the subscripted "ID" as "Ip", "lD", "1D" or "[p".
_ID_RE = re.compile(r"(?:\bI\s*D|(?:\b|(?<=\[)|^)[Il1\[|]\s*[DdPp]|\bID)\s*[=:]\s*(\d+(?:\.\d+)?)\s*(m?A)\b")
PARAM_START_RE = re.compile(r"^[~\[(]?(?:T\s*[JjCcAa.,_]?|[Il1\[|]\s*[DdPp])\s*[=:]")


def parse_label_params(text: str) -> dict[str, float]:
    """Tj (or Tc/Ta) in C and ID in A named by one label line."""
    params: dict[str, float] = {}
    temps = {float(m.group(1).replace("−", "-")) for m in _TEMP_RE.finditer(text)}
    temps |= {float(m.group(1).replace("−", "-")) for m in _TEMP_PREFIXED_RE.finditer(text)}
    if len(temps) == 1:
        params["tj_c"] = temps.pop()
    currents = {
        float(m.group(1)) * (1e-3 if m.group(2).lower() == "ma" else 1.0)
        for m in _ID_RE.finditer(text)
    }
    if len(currents) == 1:
        params["id_a"] = currents.pop()
    return params


def bind_labels(
    traces: list[Trace],
    labels: list[Label],
    swatches: list[Swatch],
    plot: PlotBox,
    leaders: list[Leader] | None = None,
    px_per_pt: float = 1.0,
) -> list[str]:
    """Bind label parameters to curves; return binding diagnostics.

    The VARYING key is the parameter whose value differs between labels; it
    is what identifies curves. Keys whose value is the same everywhere (or
    appears once while another key varies) are panel conditions and apply to
    every curve.
    """
    diagnostics: list[str] = []
    labeled = [label for label in labels if label.params]
    values: dict[str, set[float]] = {}
    for label in labeled:
        for key, value in label.params.items():
            values.setdefault(key, set()).add(value)
    varying = [key for key, seen in values.items() if len(seen) > 1]
    if len(traces) == 1:
        for key, seen in values.items():
            if len(seen) == 1:
                traces[0].params[key] = next(iter(seen))
                traces[0].binding[key] = "single_curve_panel_condition"
            else:
                traces[0].params[key] = None
                traces[0].binding[key] = "conflicting_labels_single_curve"
                diagnostics.append(f"{key}_labels_conflict_on_single_curve")
        return diagnostics
    limit = LABEL_BIND_MAX_PX_FRACTION * max(plot.x1 - plot.x0, plot.y1 - plot.y0)
    for key, seen in list(values.items()):
        if key in varying:
            continue
        carriers = [label for label in labeled if key in label.params]
        detached = all(
            min(_distance_to_trace(0, 0, label, trace) for trace in traces) > 2.0 * limit
            for label in carriers
        )
        if not (varying or detached):
            # One printed value, several curves, the label sitting on a curve:
            # it may name only that curve, so it is bound like a curve label.
            varying.append(key)
            continue
        for trace in traces:
            trace.params[key] = next(iter(seen))
            trace.binding[key] = "panel_condition_shared_by_all_curves"
    for key in varying:
        for trace in traces:
            trace.params.setdefault(key, None)
            trace.binding.setdefault(key, "unbound")
        for label in labeled:
            if key not in label.params:
                continue
            target, how = _bind_one(label, traces, swatches, plot, leaders or [], px_per_pt)
            if target is None:
                diagnostics.append(f"{key}={label.params[key]:g}_label_unbound_{how}")
                continue
            if target.params.get(key) is not None and target.params[key] != label.params[key]:
                target.params[key] = None
                target.binding[key] = "conflicting_labels"
                diagnostics.append(f"{key}_two_labels_bound_to_one_curve")
                continue
            target.params[key] = label.params[key]
            target.binding[key] = how
            for other_key, other in label.params.items():
                if other_key != key and other_key in varying:
                    continue
                if other_key != key and target.params.get(other_key) is None:
                    target.params[other_key] = other
                    target.binding[other_key] = how
        diagnostics.extend(_bind_by_elimination(key, traces, labeled))
    diagnostics.extend(_temperature_order_check(traces, values.get("tj_c", set())))
    return diagnostics


def _bind_by_elimination(key: str, traces: list[Trace], labeled: list[Label]) -> list[str]:
    """Deduce the LAST label: one distinct label value per curve, all others bound.

    Only when the panel prints exactly as many distinct values of the key as
    there are curves, every other curve is already bound with evidence, and one
    label value and one curve remain. For temperature the result must then
    still pass the physical-order check that follows.
    """
    printed = {label.params[key] for label in labeled if key in label.params}
    if len(printed) != len(traces):
        return []
    bound = {trace.params.get(key) for trace in traces if trace.params.get(key) is not None}
    remaining = printed - bound
    free = [t for t in traces if t.params.get(key) is None and t.binding.get(key) == "unbound"]
    if len(remaining) != 1 or len(free) != 1:
        return []
    free[0].params[key] = remaining.pop()
    free[0].binding[key] = "elimination_last_label_last_curve"
    return [f"{key}_bound_by_elimination"]


def _bind_one(label: Label, traces: list[Trace], swatches: list[Swatch], plot: PlotBox,
              leaders: list[Leader], px_per_pt: float):
    cy = 0.5 * (label.y0 + label.y1)
    led = _leader_target(label, traces, leaders, px_per_pt)
    if led is not None:
        return led
    swatch = [
        s for s in swatches
        if abs(s.y - cy) <= max(4.0, 0.6 * (label.y1 - label.y0)) and -4.0 <= label.x0 - s.x1 <= 40.0
    ]
    if len(swatch) == 1:
        matches = [t for t in traces if t.style == swatch[0].style]
        if len(matches) == 1:
            return matches[0], "legend_swatch_style"
        return None, "swatch_style_matches_%d_curves" % len(matches)
    cx = 0.5 * (label.x0 + label.x1)
    distances = sorted(
        (_distance_to_trace(cx, cy, label, trace), index) for index, trace in enumerate(traces)
    )
    limit = LABEL_BIND_MAX_PX_FRACTION * max(plot.x1 - plot.x0, plot.y1 - plot.y0)
    if all(trace.method == "raster" for trace in traces):
        # A raster chart's arrows cannot be followed: a label is bound by
        # proximity only when it sits against its curve.
        limit = min(limit, 1.0 * (label.y1 - label.y0))
    best, index = distances[0]
    if best > limit:
        return None, "too_far_from_any_curve"
    if len(distances) > 1 and distances[1][0] < LABEL_BIND_RATIO * best + 4.0:
        if len(distances) == 2 or distances[2][0] >= LABEL_BIND_RATIO * best + 4.0:
            near, far = traces[index], traces[distances[1][1]]
            if _occluded_by(label, far, near) and not _occluded_by(label, near, far):
                return near, "proximity_far_curve_behind_near_curve"
        return None, "not_clearly_nearest_one_curve"
    return traces[index], "proximity"


def _occluded_by(label: Label, far: Trace, near: Trace) -> bool:
    """True when the straight path from the label to ``far`` crosses ``near``.

    A label printed beside two parallel lines names the line it touches, not
    the one lying behind it; this is that visual rule made explicit. The
    path runs from the label box's point nearest ``far`` to ``far``'s point
    nearest the label.
    """
    pts = np.asarray(far.points_px, dtype=float)
    cx = np.clip(pts[:, 0], label.x0, label.x1)
    cy = np.clip(pts[:, 1], label.y0, label.y1)
    k = int(np.argmin(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)))
    start = np.array([cx[k], cy[k]])
    end = pts[k]
    samples = start + np.linspace(0.08, 0.92, 40)[:, None] * (end - start)
    near_pts = np.asarray(near.points_px, dtype=float)
    distances = np.min(np.hypot(near_pts[None, :, 0] - samples[:, None, 0], near_pts[None, :, 1] - samples[:, None, 1]), axis=1)
    return bool(np.min(distances) <= 2.0)


def _leader_target(label: Label, traces: list[Trace], leaders: list[Leader], px_per_pt: float):
    """Follow a leader line from the label to the curve its far end touches.

    The near end must start within 6 pt of the label box; the far end (or any
    vertex of an arrowhead touching it within 3 pt) must lie within 4 pt of exactly one curve and
    at least twice as far from every other curve.
    """
    near_limit = 6.0 * px_per_pt
    hits: list[tuple[int, str]] = []
    for leader in leaders:
        ends = (leader.points[0], leader.points[-1])
        for near, far in (ends, ends[::-1]):
            if _box_distance(near, label) > near_limit or _box_distance(far, label) <= near_limit:
                continue
            tips = [far] + [
                p for other in leaders if other is not leader
                and any(math.dist(q, far) <= 3.0 * px_per_pt for q in other.points)
                for p in other.points
            ]
            distances = sorted(
                (min(_point_to_trace(tip, trace) for tip in tips), index)
                for index, trace in enumerate(traces)
            )
            if distances[0][0] > 4.0 * px_per_pt:
                continue
            if len(distances) > 1 and distances[1][0] < 2.0 * distances[0][0] + 1.0:
                continue
            hits.append((distances[0][1], "leader_line"))
    targets = {index for index, _ in hits}
    if len(targets) == 1:
        return traces[targets.pop()], "leader_line"
    return None


def _box_distance(point, label: Label) -> float:
    dx = max(label.x0 - point[0], 0.0, point[0] - label.x1)
    dy = max(label.y0 - point[1], 0.0, point[1] - label.y1)
    return math.hypot(dx, dy)


def _point_to_trace(point, trace: Trace) -> float:
    pts = np.asarray(trace.points_px, dtype=float)
    return float(np.min(np.hypot(pts[:, 0] - point[0], pts[:, 1] - point[1])))


def _distance_to_trace(cx: float, cy: float, label: Label, trace: Trace) -> float:
    pts = np.asarray(trace.points_px, dtype=float)
    dx = np.maximum(0.0, np.maximum(label.x0 - pts[:, 0], pts[:, 0] - label.x1))
    dy = np.maximum(0.0, np.maximum(label.y0 - pts[:, 1], pts[:, 1] - label.y1))
    return float(np.min(np.hypot(dx, dy)))


def _temperature_order_check(traces: list[Trace], printed: set[float] | None = None) -> list[str]:
    """A hotter die has the higher RDS(on): bindings that say otherwise are unbound.

    Compared by the median pixel offset over the shared VGS span, so the
    low-VGS crossing below the zero-temperature-coefficient point (where a hot
    curve legitimately sits LOWER) cannot flip the verdict. Two checks, both
    only ever REMOVE a binding: a bound pair in the wrong order, and a curve
    bound to the hottest (coldest) printed temperature with any other traced
    curve above (below) it.
    """
    bound = [t for t in traces if t.params.get("tj_c") is not None]
    diagnostics: list[str] = []

    def median_offset(upper: Trace, lower: Trace) -> float | None:
        lo = max(min(p[0] for p in upper.points_px), min(p[0] for p in lower.points_px))
        hi = min(max(p[0] for p in upper.points_px), max(p[0] for p in lower.points_px))
        if hi - lo < 5:
            return None
        xs = np.linspace(lo, hi, 25)
        up_y = np.interp(xs, *zip(*sorted(upper.points_px)))
        low_y = np.interp(xs, *zip(*sorted(lower.points_px)))
        return float(np.median(up_y - low_y))  # < 0: "upper" really is higher

    def unbind(*items: Trace) -> None:
        for trace in items:
            trace.params["tj_c"] = None
            trace.binding["tj_c"] = "contradicted_by_temperature_order"

    for i, a in enumerate(bound):
        for b in bound[i + 1:]:
            if a.params["tj_c"] == b.params["tj_c"]:
                continue
            hot, cold = (a, b) if a.params["tj_c"] > b.params["tj_c"] else (b, a)
            offset = median_offset(hot, cold)
            if offset is not None and offset > 0:
                unbind(hot, cold)
                diagnostics.append("temperature_binding_contradicts_rdson_order")
    if printed and len(printed) > 1:
        hottest, coldest = max(printed), min(printed)
        for trace in [t for t in traces if t.params.get("tj_c") in (hottest, coldest)]:
            for other in traces:
                if other is trace:
                    continue
                offset = median_offset(other, trace)
                if offset is None:
                    continue
                above = offset < 0
                if (trace.params["tj_c"] == hottest and above) or (trace.params["tj_c"] == coldest and not above):
                    unbind(trace)
                    diagnostics.append("extreme_temperature_binding_has_a_curve_beyond_it")
                    break
    return diagnostics
