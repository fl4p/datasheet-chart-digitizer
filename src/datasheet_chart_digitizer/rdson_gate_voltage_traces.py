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
and the panel goes to review. Physical order (a hotter die, or a higher drain
current, has the higher RDS(on)) CONTRADICTS a binding. Since review F5-1 it
also CREATES one, but only as a last resort under strict guards
(``bind_by_order_rule``: as many printed values as curves, none bound by
other evidence, the other parameter shared, and every pair of curves
measurably and consistently apart), and the binding says so
(``id_order_rule`` / ``temperature_order_rule``).
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
OUTLINE_ROW_GAP_PX = 1.5        # a row this far from every column point is sampled on its own
OUTLINE_ROW_RUN_FACTOR = 3.0     # ... when its run is at most this many stroke widths
OUTLINE_ROW_MIN_RUN_PX = 6.0
OUTLINE_ROW_MAX_FIT_PX = 3.0     # a larger monotone-fit shift means the rows are not one stroke
CHAIN_FOLD_PT = 1.5          # a join may not fold the chain back in VGS by more than this
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
    bridged_columns: int = 0
    contact_removed_x: list = field(default_factory=list)
    dropped_stub_points: list = field(default_factory=list)
    row_traced_points: list = field(default_factory=list)   # F4-1: steep head traced row by row
    tail_from: list = field(default_factory=list)            # F4-4: points taken over from a shared tail
    frame_traced: dict = field(default_factory=dict)         # R5: right end traced to the frame
    gap_traced: list = field(default_factory=list)           # F6-1: unsampled stretches traced on the ink
    stub_decisions: list = field(default_factory=list)       # F6-1: each dropped end stub, served or why not
    source_top_px: tuple | None = None                        # F-v2-1: the source path's highest point in the plot
    row_fit_max_shift_px: float = 0.0                         # F-v2-1: largest monotone-fit shift of a row centre


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
    raster: bool = False     # followed on the pixels (F4-3)
    ambiguous: bool = False  # its tip lies in ink shared by two touching curves: names neither
    filled: bool = False     # a filled arrow shape (F-v2-1): an ambiguous tip names neither


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
    clip = (fx0 - 0.3, fy0 - 0.3, fx1 + 0.3, fy1 + 0.3)
    pieces: dict[tuple, list[list[tuple[float, float]]]] = {}
    fill_groups: list[list] = []  # [fill, last drawing index, union bbox, polygons, last piece bbox]
    for index, drawing in enumerate(page.get_drawings()):
        if drawing.get("type") == "f":
            outline = _filled_outline_polygon(drawing, (fx0, fy0, fx1, fy1))
            if outline is None:
                continue
            fill = tuple(round(float(c), 2) for c in drawing.get("fill") or ())
            box = (min(p[0] for p in outline), min(p[1] for p in outline),
                   max(p[0] for p in outline), max(p[1] for p in outline))
            last = fill_groups[-1] if fill_groups else None
            # touching is tested against the group's LAST piece, not its union
            if last is not None and last[0] == fill and last[1] == index - 1 and _boxes_touch(last[4], box):
                last[1], last[3], last[4] = index, last[3] + [outline], box
                last[2] = (min(last[2][0], box[0]), min(last[2][1], box[1]), max(last[2][2], box[2]), max(last[2][3], box[3]))
            else:
                fill_groups.append([fill, index, box, [outline], box])
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
            for run in _clip_polyline(points, clip):
                if _is_full_span_rule(run, width_pt, height_pt):
                    continue
                # Shorter axis-aligned strokes stay in until chaining: a steep
                # curve's vertical top (CSD17306 25 C, 11.9->16 mOhm) is one,
                # and so is a curve's last flat segment on the right frame.
                pieces.setdefault(style, []).append(run)
    traces: list[Trace] = []
    short: list[tuple[tuple, list[tuple[float, float]]]] = []
    for style, runs in pieces.items():
        for chain in _chain(runs, clip):
            xs = [p[0] for p in chain]
            ys = [p[1] for p in chain]
            if _is_rule(chain, width_pt, height_pt):
                continue  # a chain that is itself one long axis-aligned stroke: a rule
            span = max(xs) - min(xs)
            if span >= MIN_CURVE_SPAN_FRACTION * width_pt:
                px = [transform.to_px(x, y) for x, y in chain]
                top = min(px, key=lambda p: p[1])
                traces.append(Trace(densify(_as_function_of_x(px)), "vector", style, source_top_px=tuple(top)))
            else:
                short.append((style, chain))
    if not traces:
        # One curve may be painted as several consecutive outline pieces
        # (FDP8870: drawings 716+717+718 are the 1 A curve, its last 0.9 V in
        # the second and third piece). Consecutive touching pieces of one fill
        # form one curve; a group must span the usual curve width.
        for fill, _last, box, polygons, _last_box in fill_groups:
            if box[2] - box[0] < MIN_CURVE_SPAN_FRACTION * width_pt:
                continue
            columns, rows, top, fit_shift = _polygon_centerline_px(
                [[transform.to_px(x, y) for x, y in poly] for poly in polygons], plot)
            traces.append(Trace(
                sorted(columns + rows),
                "vector_filled_outline",
                (fill, "fill", ""),
                row_traced_points=rows,
                source_top_px=top,
                row_fit_max_shift_px=fit_shift,
            ))
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
    if any(trace.method == "vector_filled_outline" for trace in traces):
        leaders.extend(_filled_arrows(fill_groups, curve_styles, transform, width_pt))
    return traces, swatches, leaders


FILLED_ARROW_MAX_FRACTION = 0.15   # an arrow is short against the plot width
FILLED_ARROW_MIN_ELONGATION = 1.4  # and longer than wide (a head plus its short shaft)


def _filled_arrows(fill_groups, curve_styles, transform, width_pt: float) -> list[Leader]:
    """Arrows the PDF paints as filled shapes, as leaders from base to tip (F-v2-1).

    DMN4008LFG points its "I_D = 10.0A" / "8.0A" labels at the curve band
    with filled arrowheads, which the stroke-based leader list never saw. A
    fill group that is not a curve, is short (<= FILLED_ARROW_MAX_FRACTION of
    the plot width) and elongated (length >= FILLED_ARROW_MIN_ELONGATION x its
    width across) becomes a Leader between its two farthest vertices; which
    end is the tip is decided by the label, as for a stroked leader. Such a
    leader is ``filled``: when its tip lies in ink shared by two curves it
    names neither and no nearness fallback applies.
    """
    out: list[Leader] = []
    for fill, _last, box, polygons, _last_box in fill_groups:
        if (fill, "fill", "") in curve_styles:
            continue
        pts = np.asarray([p for poly in polygons for p in poly], dtype=float)
        if len(pts) < 3:
            continue
        d = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1])
        i, j = np.unravel_index(int(np.argmax(d)), d.shape)
        length = float(d[i, j])
        if length <= 0 or length > FILLED_ARROW_MAX_FRACTION * width_pt:
            continue
        axis = (pts[j] - pts[i]) / length
        across = float(np.ptp(pts @ np.array([-axis[1], axis[0]])))
        if across <= 0 or length < FILLED_ARROW_MIN_ELONGATION * across:
            continue
        out.append(Leader((transform.to_px(*pts[i]), transform.to_px(*pts[j])), filled=True))
    return out


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
    if not items or any(item[0] not in {"l", "c", "re", "qu"} for item in items):
        return None
    vertices: list[tuple[float, float]] = []
    for item in items:
        if item[0] == "l":
            pts = [(item[1].x, item[1].y), (item[2].x, item[2].y)]
        elif item[0] == "c":
            pts = _bezier(item[1], item[2], item[3], item[4], 6)
        else:
            quad = item[1].quad if item[0] == "re" else item[1]
            pts = [(quad.ul.x, quad.ul.y), (quad.ur.x, quad.ur.y), (quad.lr.x, quad.lr.y), (quad.ll.x, quad.ll.y)]
        for point in pts:
            if not vertices or math.dist(point, vertices[-1]) > 1e-6:
                vertices.append(point)
    if len(vertices) < 3:
        return None
    xs = [p[0] for p in vertices]
    ys = [p[1] for p in vertices]
    width_pt, height_pt = frame[2] - frame[0], frame[3] - frame[1]
    if min(xs) < frame[0] - 1 or max(xs) > frame[2] + 1 or min(ys) < frame[1] - 1 or max(ys) > frame[3] + 1:
        return None
    if max(xs) - min(xs) >= GRID_RULE_MIN_FRACTION * width_pt and max(ys) - min(ys) <= 1.0:
        return None  # a grid rule painted as a thin filled rectangle
    if max(ys) - min(ys) >= GRID_RULE_MIN_FRACTION * height_pt and max(xs) - min(xs) <= 1.0:
        return None
    area = 0.5 * abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(vertices, vertices[1:] + vertices[:1])))
    perimeter = sum(math.dist(a, b) for a, b in zip(vertices, vertices[1:] + vertices[:1]))
    if perimeter <= 0 or 2.0 * area / perimeter > 2.0:
        return None
    return vertices


def _boxes_touch(a, b, gap: float = 0.8) -> bool:
    return a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]


def _polygon_centerline_px(polygons_px, plot: PlotBox):
    """Centre line of ONE curve's filled outline piece(s), rasterised on their own.

    Returns (column points, row points, source top, row-fit shift). One group
    of pieces is one curve, so no tracking is involved: each column's ink is
    that curve's cross-section, and its centre is the curve there.

    A steep stretch crosses a column as one tall run, so the column centres
    leave most of it unsampled: DMN4008LFG's heads run 220 px up to the top
    frame and were served as a few points half-way up (F-v2-1). Every row of
    the outline that no column point lies within ``OUTLINE_ROW_GAP_PX`` of
    (among columns near that row's ink), whose own run is narrow (a crossing
    of the stroke), and which lies inside a tall column run (a steep
    stretch, not a flat one or an end cap), is ADDED at its run's
    centre, on a 4x supersampled mask so the centres follow the outline to a
    quarter pixel: the head row by row, as F4-1 does for raster heads, but on
    the curve's own outline. The column points are served exactly as before
    (never moved or dropped). The row centres are fitted monotone
    (``_monotone_rows``); if that needs a shift larger than
    ``OUTLINE_ROW_MAX_FIT_PX`` or one stroke width, the rows are not one
    monotone stroke and none is served. ``source top`` is the outline's
    highest row inside the plot, so the served extent can be checked.
    """
    mask = np.zeros((plot.y1 + 4, plot.x1 + 4), dtype=np.uint8)
    fine = np.zeros((4 * (plot.y1 + 4), 4 * (plot.x1 + 4)), dtype=np.uint8)
    for polygon_px in polygons_px:
        cv2.fillPoly(mask, [np.round(np.asarray(polygon_px) * 4).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=2)
        cv2.fillPoly(fine, [np.round((np.asarray(polygon_px) + 0.5) * 16 - 2).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=2)
    columns: list[tuple[float, float]] = []
    heights: list[int] = []
    spans: list[tuple[float, int, int]] = []
    for x in range(plot.x0, plot.x1 + 1):
        rows = np.flatnonzero(mask[:, x])
        if rows.size:
            columns.append((float(x), float(0.5 * (rows[0] + rows[-1]))))
            heights.append(int(rows[-1] - rows[0] + 1))
            spans.append((float(x), int(rows[0]), int(rows[-1])))
    if not columns:
        return [], [], None, 0.0
    stroke = float(np.percentile(heights, 25))
    narrow = max(OUTLINE_ROW_MIN_RUN_PX, OUTLINE_ROW_RUN_FACTOR * stroke)
    # the steep stretches: columns the stroke crosses as a tall run
    steep = [(x, top, bottom) for x, top, bottom in spans if bottom - top + 1 > narrow]
    cxs = np.asarray([x for x, _y in columns])
    cys = np.asarray([y for _x, y in columns])
    rows_out: list[tuple[float, float]] = []
    x_lo, x_hi = 4 * plot.x0, 4 * (plot.x1 + 1)
    for y in range(plot.y0, plot.y1 + 1):
        cols = np.flatnonzero(fine[4 * y:4 * y + 4, x_lo:x_hi].any(axis=0))
        if not cols.size or (cols[-1] - cols[0] + 1) / 4.0 > narrow:
            continue
        centre = plot.x0 + (0.5 * (cols[0] + cols[-1]) + 0.5) / 4.0 - 0.5
        if not any(abs(x - centre) <= narrow and top <= y <= bottom for x, top, bottom in steep):
            continue   # only rows of a steep stretch; a flat stretch is the columns' job
        near = np.abs(cxs - centre) <= narrow
        if near.any() and np.min(np.abs(cys[near] - y)) <= OUTLINE_ROW_GAP_PX:
            continue
        rows_out.append((centre, float(y)))
    fit_shift = 0.0
    if rows_out:
        rows_out, shift = _monotone_rows(rows_out)
        fit_shift = round(shift, 3)
        if shift > max(OUTLINE_ROW_MAX_FIT_PX, stroke):
            rows_out = []   # not one monotone stroke: serve none (the extent check names the gap)
    inside = mask[plot.y0:plot.y1 + 1, plot.x0:plot.x1 + 1]
    top_rows = np.flatnonzero(inside.any(axis=1))
    top = None
    if top_rows.size:
        cols = np.flatnonzero(inside[top_rows[0]])
        top = (float(plot.x0 + 0.5 * (cols[0] + cols[-1])), float(plot.y0 + top_rows[0]))
    return columns, rows_out, top, fit_shift


def _monotone_rows(rows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Row centres fitted as x non-decreasing in y (least squares, pool-adjacent-violators).

    An RDS(on) curve falls with VGS, so down a steep stretch x never moves
    left. The measured row centres wobble where the PDF's outline pieces
    overlap (DMN4008LFG: the next piece starts 0.5-0.9 pt left of the last),
    and sorted by VGS that wobble reads as RDS rising. The fit moves a centre
    only within that wobble; the largest shift is kept on each point's
    trace as ``row_fit_max_shift_px`` (returned with the rows).
    """
    rows = sorted(rows, key=lambda p: p[1])
    blocks: list[list[float]] = []   # [sum, count]
    for x, _y in rows:
        blocks.append([x, 1.0])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            total, count = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += count
    fitted: list[float] = []
    for total, count in blocks:
        fitted += [total / count] * int(count)
    shift = max((abs(fx - x) for fx, (x, _y) in zip(fitted, rows)), default=0.0)
    return [(round(fx, 3), y) for fx, (_x, y) in zip(fitted, rows)], shift


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


def _is_full_span_rule(run, width_pt: float, height_pt: float) -> bool:
    """An axis-aligned stroke across >= 90 % of the frame: a grid rule or frame side."""
    xs = [p[0] for p in run]
    ys = [p[1] for p in run]
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)
    return (dy <= AXIS_ALIGNED_TOLERANCE_PT and dx >= 0.9 * width_pt) or (
        dx <= AXIS_ALIGNED_TOLERANCE_PT and dy >= 0.9 * height_pt
    )


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


def _on_clip_edge(point: tuple[float, float], clip) -> bool:
    """A point the frame clip produced: exactly on the clip rectangle's edge."""
    if clip is None:
        return False
    x, y = point
    return min(abs(x - clip[0]), abs(x - clip[2]), abs(y - clip[1]), abs(y - clip[3])) <= 1e-6


def _chain(runs: list[list[tuple[float, float]]], clip=None) -> list[list[tuple[float, float]]]:
    """Join same-style pieces whose endpoints meet; ambiguous joins stay apart.

    Two pieces are never joined at a point the frame clip cut them at: that
    point is where each LEAVES the frame, not where one continues the other.
    AON7524's 125 C and 25 C curves are two paths in one style that both
    leave through the top frame 0.3 pt apart; joined there, they became one
    trace running up one curve and back down the other (F-all-1).

    Nor is a join made that folds the chain back on itself in VGS by more
    than CHAIN_FOLD_PT beyond what either piece already does: DMN3023L's
    3.5 A and 4.0 A curves are two paths that END at the same point on the
    right frame (0.12 pt apart); joined tail to tail they ran right along one
    curve and back left along the other.
    """
    chains = [list(run) for run in runs]
    merged = True
    while merged:
        merged = False
        for i in range(len(chains)):
            for j in range(len(chains)):
                if i == j or not chains[i] or not chains[j]:
                    continue
                a, b = chains[i], chains[j]
                if _on_clip_edge(a[-1], clip) and (_on_clip_edge(b[0], clip) or _on_clip_edge(b[-1], clip)):
                    continue
                if math.dist(a[-1], b[0]) <= CHAIN_JOIN_PT:
                    joined = a + b[1:]
                elif math.dist(a[-1], b[-1]) <= CHAIN_JOIN_PT:
                    joined = a + list(reversed(b))[1:]
                else:
                    continue
                if _x_fold_pt(joined) > max(_x_fold_pt(a), _x_fold_pt(b)) + CHAIN_FOLD_PT:
                    continue
                chains[i] = joined
                chains[j] = []
                merged = True
        chains = [c for c in chains if c]
    return chains


def _x_fold_pt(points: list[tuple[float, float]]) -> float:
    """How far a polyline runs back against its own overall x direction (0 when monotone in x)."""
    xs = [p[0] for p in points]
    if len(xs) < 2:
        return 0.0
    sign = 1.0 if xs[-1] >= xs[0] else -1.0
    worst, running = 0.0, -math.inf
    for x in xs:
        running = max(running, sign * x)
        worst = max(worst, running - sign * x)
    return worst


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
    erased_cols = _erased_rows(mask.shape[1], grid_x, mask.T)
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
    tracks = [piece for track in tracks for piece in _split_leader_runs(track)]
    kept = _admit_tracks(tracks, erased_cols, width, height, plot)
    out = []
    for t in kept:
        points, filled = _bridge_erased_rules(t["points"], erased_cols)
        out.append(Trace([(float(px), float(py)) for px, py in points], "raster", None, t["merged"],
                         bridged_columns=filled, contact_removed_x=t.get("contact_removed_x", []),
                         dropped_stub_points=list(t.get("dropped_stubs", []))))
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if image_bgr.ndim == 3 else image_bgr
    # branches take over the tail they merge into FIRST, so a tail's head is
    # never extended up a branch that already has its own trace
    out = group_branches(out, plot)
    out = extend_steep_heads(out, gray, plot, erased_cols, erased_rows)
    out = extend_tails_to_frame(out, gray, plot)
    return fill_unsampled_stretches(out, gray, plot, erased_cols, erased_rows)


def _admit_tracks(tracks: list[dict], erased_cols: np.ndarray, width: int, height: int, plot: PlotBox) -> list[dict]:
    """Clean each track, THEN decide whether it is a curve (review R3-3).

    Order matters: a track must pass the admission tests on the points it
    will actually serve, not on ink that a later step removes.
    """
    for track in tracks:
        _remove_contact_bumps(track)
        # Stubs go BEFORE the admission tests (review R3-3): on RQ3E180AJ two
        # steep tracks reached the 25 %-height rule only through their final
        # point on the 30.3 mOhm ID-leader junction, which the stub rule then
        # removed. Every dropped point is recorded, not silently lost (R3-5).
        track["points"], track["dropped_stubs"] = _drop_orphan_ends(track["points"], erased_cols)
    kept = [
        t for t in tracks
        if len(t["points"]) >= 3 and _long_enough(t, width, height)
        and not _is_straight_rule(t) and not _floating_flat_fragment(t, plot)
    ]
    kept = _drop_duplicates(kept)
    return kept


STUB_MAX_POINTS = 2   # an end piece this short, cut off by a real gap, is a stub
STUB_KEEP_MIN = 3     # ... dropped only if at least this many points remain


def _drop_orphan_ends(points, erased_cols: np.ndarray):
    """Drop 1-2 point stubs cut off from the track's end by a real gap.

    (Through v3 the code dropped up to 3-point stubs while this docstring and
    the reason text said 1-2; round 3 made them agree at 1-2. On the 15 real
    panels every dropped stub is 1 point, so no output changed.)

    On RQ3E180AJ both steep branches ended in one point at 30.3 mOhm, a
    column after a gap: the point where the ID leader meets the branch
    (review round 2). A stub that short carries no curve shape, only the
    risk of being annotation ink.
    """
    def real_gap(a, b) -> bool:
        lo, hi = sorted((a[0], b[0]))
        missing = range(int(lo) + 1, int(hi))
        return hi - lo > 2.5 and not all(0 <= c < erased_cols.shape[0] and erased_cols[c] for c in missing)

    points = list(points)
    dropped: list[tuple[float, float]] = []
    for _end in range(2):
        cut = [i for i in range(1, min(len(points), STUB_MAX_POINTS + 1)) if real_gap(points[i - 1], points[i])]
        if cut and len(points) - cut[-1] >= STUB_KEEP_MIN:
            dropped.extend(points[: cut[-1]])
            points = points[cut[-1]:]
        points = points[::-1]
    return points, dropped


def _bridge_erased_rules(points, erased_cols: np.ndarray):
    """Fill the columns WE erased under a vertical grid rule, and only those.

    Removing a rule leaves the curve a few columns short where it crosses it;
    those columns are known to hold the curve under the rule, so they are
    interpolated (and counted). Any other missing column stays a gap, which is
    reported and never read across (review finding A).
    """
    out: list[tuple[float, float]] = []
    filled = 0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        out.append((x0, y0))
        missing = range(int(x0) + 1, int(x1))
        if 0 < len(missing) <= 10 and all(0 <= c < erased_cols.shape[0] and erased_cols[c] for c in missing):
            for c in missing:
                out.append((float(c), y0 + (y1 - y0) * (c - x0) / (x1 - x0)))
                filled += 1
    if points:
        out.append(points[-1])
    return out, filled


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


def _floating_flat_fragment(track: dict, plot: PlotBox) -> bool:
    """A short, flat raster fragment with both ends inside the plot.

    Un-OCRed label text is traced as exactly this: on WSR3090 the ink of
    "TJ=25C" became a 4.23 mOhm "curve" from 6.9 to 8.6 V. A genuine curve
    piece like it cannot be told apart from text, so it is dropped (a loss of
    coverage, never wrong data). Steep fragments -- real branches near
    threshold -- and anything spanning >= 30 % of the plot width are kept.
    """
    xs = np.asarray([p[0] for p in track["points"]], dtype=float)
    ys = np.asarray([p[1] for p in track["points"]], dtype=float)
    margin = 8.0
    starts_inside = ys[0] > plot.y0 + margin and xs[0] > plot.x0 + margin
    ends_inside = xs[-1] < plot.x1 - margin and ys[-1] < plot.y1 - margin
    if not (starts_inside and ends_inside):
        return False
    if xs[-1] - xs[0] >= 0.30 * (plot.x1 - plot.x0):
        return False
    # net slope over 25-point windows: letter strokes jitter over a few
    # pixels, a real near-threshold branch falls steadily
    w = min(25, len(xs) - 1)
    steepest = max(
        ((ys[i + w] - ys[i]) / max(1.0, xs[i + w] - xs[i]) for i in range(len(xs) - w)), default=0.0
    )
    return steepest < 1.0


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


LEADER_STEEP_SLOPE = 1.0     # px/px just before the run: the curve was steep
LEADER_FLAT_SLOPE = 0.15     # px/px along the run: a (near) horizontal rule
LEADER_MIN_RUN_PX = 8


BUMP_WINDOW_PX = 16
BUMP_CORE_PX = 3.0      # a thin anti-aliased line stair-steps +-2 px; an arrow pulls 3-5 px
BUMP_EDGE_PX = 1.5


def _remove_contact_bumps(track: dict) -> None:
    """Drop points where an arrow or label stroke touching the curve pulls the trace.

    Where an annotation arrow meets a flat stretch of curve, the column's ink
    run widens and its centre shifts off the curve (WSR3090: +4 px at the
    125 C arrowhead near 7.98 V, -5 px on its shaft near 8.33 V; review R2-7).
    Away from the steep region an RDS(VGS) curve is smooth over +-16 px. A
    point more than 3 px off a quadratic fitted to its neighbours (its own
    +-3 px excluded; local slope under 1 px/px) is a contact core; neighbours
    within 5 px deviating the same way by 1.5 px or more go with it. The
    points are removed, leaving an explicit gap; nothing is interpolated.
    """
    for _round in range(3):  # refit after each removal: pulled points bias their own fit
        if not _remove_contact_bumps_once(track):
            return


def _remove_contact_bumps_once(track: dict) -> bool:
    points = track["points"]
    n = len(points)
    if n < 2 * BUMP_WINDOW_PX:
        return False
    xs = np.asarray([p[0] for p in points], dtype=float)
    ys = np.asarray([p[1] for p in points], dtype=float)
    deviation = np.zeros(n)
    flat = np.zeros(n, dtype=bool)
    for i in range(n):
        window = (np.abs(xs - xs[i]) <= BUMP_WINDOW_PX) & (np.abs(xs - xs[i]) > 3)
        if window.sum() < 12:
            continue
        coeffs = np.polyfit(xs[window], ys[window], 2)
        flat[i] = abs(np.polyval(np.polyder(coeffs), xs[i])) < 1.0
        deviation[i] = ys[i] - np.polyval(coeffs, xs[i])
    core = flat & (np.abs(deviation) >= BUMP_CORE_PX)
    drop = core.copy()
    for i in np.flatnonzero(core):
        near = flat & (np.abs(xs - xs[i]) <= 5) & (np.sign(deviation) == np.sign(deviation[i])) & (
            np.abs(deviation) >= BUMP_EDGE_PX
        )
        drop |= near
    if drop.any():
        track["points"] = [p for p, bad in zip(points, drop) if not bad]
        track["contact_removed_x"] = track.get("contact_removed_x", []) + [float(x) for x in xs[drop]]
        return True
    return False


def _split_leader_runs(track: dict) -> list[dict]:
    """Remove flat straight runs a steep curve jumps onto: those are leaders.

    An RDS(VGS) curve bends gradually from steep to flat. A track that is
    steep over the last 8 px and then runs dead flat and straight for >= 12 px
    has left its curve along a label leader (RQ6E080AJ: onto the 4.0 A leader
    at 26.3 mOhm). A flat run FOLLOWED by a steep stretch is one too, since a
    curve never steepens after flattening (RQ3E180AJ: a 30 mOhm jog from one
    branch onto the other; review finding 2). The run is dropped and the
    track split around it.
    """
    points = track["points"]
    n = len(points)
    if n < LEADER_MIN_RUN_PX + 8:
        return [track]
    xs = np.asarray([p[0] for p in points], dtype=float)
    ys = np.asarray([p[1] for p in points], dtype=float)

    def slope(a: int, b: int) -> float:
        return (ys[b] - ys[a]) / max(1.0, xs[b] - xs[a])

    i = 0
    while i < n - 3:
        j = i + 1
        while j < n and abs(ys[j] - ys[i]) <= 1.5 and xs[j] - xs[j - 1] <= 3:
            j += 1
        run_x = xs[j - 1] - xs[i]
        flat = run_x >= LEADER_MIN_RUN_PX and abs(ys[j - 1] - ys[i]) / max(1.0, run_x) <= LEADER_FLAT_SLOPE
        # over the last (up to) 8 points; a steep branch may be only a few
        # points long before it meets its leader (RQ3E180AJ 9 A top: 4 points)
        steep_before = i >= 2 and slope(max(0, i - 8), i) >= LEADER_STEEP_SLOPE
        # a curve never steepens again after flattening (it is convex), so a
        # flat run followed by a steep stretch is a leader too (jog at the head)
        steep_after = j + 8 < n and slope(j, j + 8) >= LEADER_STEEP_SLOPE
        if flat and (steep_before or steep_after):
            pieces = []
            if i >= 3:
                # the run's first point is already on the leader: not kept
                pieces.append(dict(track, points=points[:i], own=i,
                                   leader_cut=track.get("leader_cut", 0) + (j - i)))
            if n - j >= 3:
                pieces.extend(_split_leader_runs(dict(track, points=points[j:], own=n - j, converged=False)))
            return pieces
        i = j if flat else i + 1
    return [track]


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
    # A steep near-threshold branch may cover only a few columns (RQ6E080AJ's
    # 8 A branch: 8 px wide, 1.75-1.87 V, 40 -> 29 mOhm; review R2-3), so the
    # steep test counts columns, not a fraction of the width.
    return x_span >= MIN_CURVE_SPAN_FRACTION * width or (
        y_span >= 0.25 * height and len(xs) >= 5
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
    r"(?:\bT\s*(?P<sub>[JjCcAa.,_])?\s*[=:]\s*)?(?P<value>[+\-−]?\d{1,3}(?:\.\d+)?)\s*"
    r"(?:[°º˚*]\s*C|℃|\s?deg\s*C|[°º˚](?!\s*F))",
)
_TEMP_PREFIXED_RE = re.compile(r"\bT\s*(?P<sub>[JjCcAa.,_])?\s*[=:]\s*(?P<value>[+\-−]?\d{1,3}(?:\.\d+)?)")
_TEMPERATURE_KINDS = {"j": "Tj", "c": "Tc", "a": "Ta"}
# OCR spells the subscripted "ID" as "Ip", "lD", "1D" or "[p".
_ID_RE = re.compile(r"(?:\bI\s*D|(?:\b|(?<=\[)|^)[Il1\[|]\s*[DdPp]|\bID)\s*[=:]\s*(\d+(?:\.\d+)?)\s*(m?A)\b")
PARAM_START_RE = re.compile(r"^[~\[(]?(?:T\s*[JjCcAa.,_]?|[Il1\[|]\s*[DdPp])\s*[=:]")


def temperature_kind(subscript: str | None, prefixed: bool) -> str:
    """Tj / Tc / Ta as printed; "T (subscript unread)" when OCR lost it."""
    if not prefixed:
        return "unspecified"
    if subscript and subscript.lower() in _TEMPERATURE_KINDS:
        return _TEMPERATURE_KINDS[subscript.lower()]
    return "T (subscript unread)"


def parse_label_params(text: str) -> dict:
    """Temperature (with the kind the chart prints) and ID named by one label line.

    The kind is kept apart from the value: TI prints Tc, Rohm Ta, IR Tj, and
    treating them as one quantity is an assumption the validation must state
    (review finding 4).
    """
    params: dict = {}
    temps: set[float] = set()
    kinds: set[str] = set()
    for pattern in (_TEMP_RE, _TEMP_PREFIXED_RE):
        for match in pattern.finditer(text):
            temps.add(float(match.group("value").replace("−", "-")))
            prefixed = match.group(0).lstrip()[:1] in {"T"}
            kinds.add(temperature_kind(match.group("sub"), prefixed))
    if len(temps) == 1:
        params["temperature_c"] = temps.pop()
        named = kinds - {"unspecified"}
        params["temperature_kind"] = named.pop() if len(named) == 1 else ("unspecified" if not named else "conflicting")
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
            if key == "temperature_kind":
                continue
            values.setdefault(key, set()).add(value)
    varying = [key for key, seen in values.items() if len(seen) > 1]
    limit = LABEL_BIND_MAX_PX_FRACTION * max(plot.x1 - plot.x0, plot.y1 - plot.y0)
    if len(traces) == 1:
        for key, seen in values.items():
            carriers = [label for label in labeled if key in label.params]
            touching = any(_distance_to_trace(0, 0, label, traces[0]) <= 2.0 * limit for label in carriers)
            if len(seen) == 1 and touching and traces[0].method == "raster":
                # One traced line with a curve label beside it: on a raster
                # chart the other labelled curves may simply be untraced
                # (RQ3E180AJ: "ID=9A" beside a line that is the 18 A branch).
                traces[0].params[key] = None
                traces[0].binding[key] = "single_label_may_belong_to_an_untraced_curve"
                diagnostics.append(f"{key}_label_on_single_raster_line_not_bound")
            elif len(seen) == 1:
                traces[0].params[key] = next(iter(seen))
                traces[0].binding[key] = "single_curve_panel_condition"
            else:
                traces[0].params[key] = None
                traces[0].binding[key] = "conflicting_labels_single_curve"
                diagnostics.append(f"{key}_labels_conflict_on_single_curve")
        # the single-curve path must carry the printed kind too (review R2-1:
        # RQ3E110AJ's "Ta=25C" lost its kind here)
        _attach_temperature_kinds(traces, labeled)
        return diagnostics
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
                if other_key == "temperature_kind":
                    continue
                if other_key != key and other_key in varying:
                    continue
                if other_key != key and target.params.get(other_key) is None:
                    target.params[other_key] = other
                    target.binding[other_key] = how
        diagnostics.extend(_bind_by_elimination(key, traces, labeled))
    for key in varying:
        diagnostics.extend(bind_by_order_rule(key, traces, labeled, values, varying))
    diagnostics.extend(_id_order_check(traces, values, varying))
    diagnostics.extend(_temperature_order_check(traces, values.get("temperature_c", set())))
    _attach_temperature_kinds(traces, labeled)
    return diagnostics


def _attach_temperature_kinds(traces: list[Trace], labeled: list[Label]) -> None:
    """Give each bound temperature the kind printed with that value."""
    for trace in traces:
        value = trace.params.get("temperature_c")
        if value is None:
            if "temperature_c" in trace.params:
                trace.params["temperature_kind"] = None
            continue
        kinds = {
            label.params.get("temperature_kind", "unspecified")
            for label in labeled if label.params.get("temperature_c") == value
        }
        trace.params["temperature_kind"] = kinds.pop() if len(kinds) == 1 else "conflicting"


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


# F5-1 / F5-3: binding by physical order ---------------------------------------------
#
# Where no leader, swatch or placement names a curve, the physics of the chart
# does: at equal VGS (and equal temperature) the curve at the higher drain
# current has the higher RDS(on) -- VDS/ID rises with ID along every output
# characteristic -- and, at VGS well above threshold (and equal ID), the hotter
# die has the higher RDS(on). Two curves are ordered by the chart itself: the
# per-column heights of their traces over the shared VGS range.
#
# ORDER_MARGIN_PX: a column counts as "apart" when the traces differ by >= 3 px.
#   Measured on the batch-15 panels, two traces of ONE printed stroke differ by
#   at most 1.75 px (RQ3E110AJ's coincident stretch, raster) and 1.0 px
#   (FDP8870's coincident tails, vector); 3 px is above both with margin.
# ORDER_MIN_RUN: ... in >= 5 consecutive shared columns. The steep heads of the
#   golden panels show 1-3-column flips where one curve is still near-vertical
#   (CSD17306Q5A 1-2, IRLB8743/8748 2, CSD18502KCS 3 columns); those are not
#   evidence either way. RQ6E080AJ's two heads are apart over 9 columns.
ORDER_MARGIN_PX = 3.0
ORDER_MIN_RUN = 5
ORDER_RULE_BINDING = {"id_a": "id_order_rule", "temperature_c": "temperature_order_rule"}
_OTHER_KEY = {"id_a": "temperature_c", "temperature_c": "id_a"}


def _column_heights(trace: Trace) -> dict[int, float]:
    by_x: dict[int, list[float]] = {}
    for x, y in trace.points_px:
        by_x.setdefault(int(round(x)), []).append(y)
    return {x: float(np.mean(ys)) for x, ys in by_x.items()}


def separated_runs(a: Trace, b: Trace) -> list[tuple[int, int, int, int, float]]:
    """Runs of shared columns where the traces are >= ORDER_MARGIN_PX apart.

    Each run: (sign, first_x, last_x, n_columns, max_separation_px); sign +1
    where ``a`` is ABOVE ``b`` (higher RDS), -1 where below. Columns more than
    3 px apart break a run. Every run is returned, short ones included.
    """
    ca, cb = _column_heights(a), _column_heights(b)
    runs: list[list] = []
    current: list | None = None
    for x in sorted(set(ca) & set(cb)):
        d = cb[x] - ca[x]                   # > 0: a is higher on the chart
        sign = 1 if d >= ORDER_MARGIN_PX else -1 if d <= -ORDER_MARGIN_PX else 0
        if current is not None and sign == current[0] and x - current[2] <= 3:
            current[2], current[3], current[4] = x, current[3] + 1, max(current[4], abs(d))
            continue
        if current is not None and current[0] != 0:
            runs.append(current)
        current = [sign, x, x, 1, abs(d)]
    if current is not None and current[0] != 0:
        runs.append(current)
    return [tuple(r) for r in runs]


def pair_order(a: Trace, b: Trace, key: str) -> tuple[int | None, str]:
    """+1 if ``a`` has the higher RDS(on), -1 if ``b``, None if the chart does not say.

    Only runs of >= ORDER_MIN_RUN columns count. For ID any two such runs of
    opposite sign mean the traces cross, which ID curves at one temperature
    do not: undecided. For temperature one crossing is physics (below the
    zero-temperature-coefficient point the hot curve lies LOWER), so the run
    at the highest VGS decides, provided the order changes at most once.
    Assumes only that VGS increases with crop x (true of a linear and a log VGS axis alike).
    """
    runs = [r for r in separated_runs(a, b) if r[3] >= ORDER_MIN_RUN]
    if not runs:
        return None, f"never >= {ORDER_MARGIN_PX:g} px apart over >= {ORDER_MIN_RUN} columns"
    signs = [r[0] for r in runs]
    changes = sum(1 for s, t in zip(signs, signs[1:]) if s != t)
    evidence = ", ".join(f"{'above' if s > 0 else 'below'} x={x0}..{x1} ({n} cols, <= {m:.1f} px)" for s, x0, x1, n, m in runs)
    if key == "id_a" and changes:
        return None, f"the traces cross ({evidence})"
    if changes > 1:
        return None, f"the order changes {changes} times ({evidence})"
    return signs[-1], evidence


def _shares_other_parameter(key: str, traces: list[Trace], values: dict, varying: list[str]) -> str | None:
    """None when every curve shares the other parameter (or it is not printed); else why not."""
    other = _OTHER_KEY[key]
    if other in varying:
        return f"{other} varies between the printed labels"
    if other in values and len({t.params.get(other) for t in traces}) > 1:
        return f"the curves carry different {other}"
    return None


def bind_by_order_rule(key: str, traces: list[Trace], labeled: list[Label], values: dict,
                       varying: list[str]) -> list[str]:
    """Bind ``key`` (id_a or temperature_c) by the physical order of the curves.

    Guards -- any failing one leaves every curve's value unknown:
      * the panel prints exactly as many distinct values as there are curves;
      * no curve is bound for ``key`` by other evidence (all "unbound"; a
        conflicting or contradicted binding is not overridden);
      * the other parameter is shared by all curves or not printed;
      * every pair of curves is ordered by pair_order (a measurable,
        consistent separation), and the order is total.
    The highest printed value goes to the curve with the highest RDS(on).
    """
    if key not in ORDER_RULE_BINDING:
        return []
    printed = sorted({label.params[key] for label in labeled if key in label.params})
    if len(printed) != len(traces) or len(traces) < 2:
        return []
    if any(t.params.get(key) is not None or t.binding.get(key) != "unbound" for t in traces):
        return []
    why = _shares_other_parameter(key, traces, values, varying)
    if why:
        return [f"{key}_order_rule_not_applied ({why})"]
    above = {id(t): 0 for t in traces}
    evidence = []
    for i, a in enumerate(traces):
        for b in traces[i + 1:]:
            sign, detail = pair_order(a, b, key)
            if sign is None:
                return [f"{key}_order_rule_not_applied ({detail})"]
            above[id(a if sign > 0 else b)] += 1
            evidence.append((a, b, detail))
    ranks = sorted(above.values())
    if ranks != list(range(len(traces))):
        return [f"{key}_order_rule_not_applied (the pairwise order is not total)"]
    ordered = sorted(traces, key=lambda t: above[id(t)])        # lowest RDS first
    for trace, value in zip(ordered, printed):
        trace.params[key] = value
        trace.binding[key] = ORDER_RULE_BINDING[key]
    unit = "A" if key == "id_a" else "C"
    chain = " > ".join(f"{t.params[key]:g} {unit}" for t in reversed(ordered))
    pairs = "; ".join(f"{a.params[key]:g} {unit} vs {b.params[key]:g} {unit}: {detail}" for a, b, detail in evidence)
    return [f"{key}_bound_by_order_rule (RDS order {chain}; higher {'ID' if key == 'id_a' else 'temperature'} "
            f"-> higher RDS(on); separation: {pairs})"]


def _id_order_check(traces: list[Trace], values: dict, varying: list[str]) -> list[str]:
    """IDs bound by other evidence that the curves' order contradicts are unbound.

    The ID analogue of _temperature_order_check: it only ever REMOVES a
    binding, and only where pair_order decides (so never on curves that do
    not separate). A swapped pair of leader-bound IDs is caught here.
    """
    if "id_a" not in varying or _shares_other_parameter("id_a", traces, values, varying):
        return []
    bound = [t for t in traces if t.params.get("id_a") is not None]
    diagnostics = []
    for i, a in enumerate(bound):
        for b in bound[i + 1:]:
            if a.params["id_a"] == b.params["id_a"]:
                continue
            sign, _detail = pair_order(a, b, "id_a")
            if sign is None:
                continue
            higher_id = a if a.params["id_a"] > b.params["id_a"] else b
            higher_rds = a if sign > 0 else b
            if higher_id is not higher_rds:
                for trace in (a, b):
                    trace.params["id_a"] = None
                    trace.binding["id_a"] = "contradicted_by_id_order"
                diagnostics.append("id_binding_contradicts_rdson_order (the higher-ID curve has the lower RDS(on))")
    return diagnostics


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
    unreadable = False
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
                if leader.filled:
                    unreadable = True   # an arrow into a shared band: resolved by neither tip nor nearness
                continue
            if leader.ambiguous:
                unreadable = True
                continue
            hits.append((distances[0][1], "leader_line"))
    targets = {index for index, _ in hits}
    if len(targets) == 1:
        return traces[targets.pop()], "leader_line"
    if unreadable and not targets:
        # its leader ends where two curves touch: stated, and no fallback
        # to nearness (the nearest line is the one the leader passed through)
        return None, "leader_tip_between_touching_curves"
    return None


def _box_distance(point, label: Label) -> float:
    dx = max(label.x0 - point[0], 0.0, point[0] - label.x1)
    dy = max(label.y0 - point[1], 0.0, point[1] - label.y1)
    return math.hypot(dx, dy)


def _point_to_trace(point, trace: Trace) -> float:
    if trace.method == "raster" and len(trace.points_px) > 1:
        # one sample per column leaves a steep stroke's samples far apart:
        # measure to the drawn polyline, not the samples (F4-3, RQ3E180AJ)
        return _polyline_distance(point, trace.points_px)
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
    bound = [t for t in traces if t.params.get("temperature_c") is not None]
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
            trace.params["temperature_c"] = None
            trace.binding["temperature_c"] = "contradicted_by_temperature_order"

    for i, a in enumerate(bound):
        for b in bound[i + 1:]:
            if a.params["temperature_c"] == b.params["temperature_c"]:
                continue
            hot, cold = (a, b) if a.params["temperature_c"] > b.params["temperature_c"] else (b, a)
            offset = median_offset(hot, cold)
            if offset is not None and offset > 0:
                unbind(hot, cold)
                diagnostics.append("temperature_binding_contradicts_rdson_order")
    if printed and len(printed) > 1:
        hottest, coldest = max(printed), min(printed)
        for trace in [t for t in traces if t.params.get("temperature_c") in (hottest, coldest)]:
            for other in traces:
                if other is trace:
                    continue
                offset = median_offset(other, trace)
                if offset is None:
                    continue
                above = offset < 0
                if (trace.params["temperature_c"] == hottest and above) or (trace.params["temperature_c"] == coldest and not above):
                    unbind(trace)
                    diagnostics.append("extreme_temperature_binding_has_a_curve_beyond_it")
                    break
    return diagnostics


# ---------------------------------------------------------------------------
# F4-1: steep heads traced row by row

ROW_INK_GRAY = 150          # ink for row tracking (the column tracker's mask is eroded at rules)
ROW_MAX_MISS = 3            # rows without ink (other than rule rows) before a head stops
ROW_WINDOW_PX = 3.0         # a row's run must lie within this of the predicted x
ROW_MIN_STEEPNESS = 1.5     # dy/dx at the head: flatter heads are the column tracker's job
ROW_SPLIT_ROWS = 3          # a band must show two dark cores this many rows running to fork


def _row_runs(gray, y: int, lo: int, hi: int, erased_cols) -> list[dict]:
    """Ink runs in one row between lo and hi; erased rule columns are
    bridged (a run continues across them) and never start a run."""
    row = gray[y, lo:hi + 1]
    runs, start = [], None
    for i, value in enumerate(row):
        x = lo + i
        rule = 0 <= x < len(erased_cols) and erased_cols[x]
        dark = value < ROW_INK_GRAY and not rule
        if dark and start is None:
            start = x
        elif not dark and not rule and start is not None:
            runs.append((start, x - 1))
            start = None
    if start is not None:
        runs.append((start, hi))
    out = []
    for a, b in runs:
        profile = gray[y, a:b + 1].astype(float)
        out.append({"x0": a, "x1": b, "centre": 0.5 * (a + b), "cores": _dark_cores(profile, a)})
    return out


def _dark_cores(profile, offset: int) -> list[float]:
    """Centres of the separate dark cores of a run: two lines printed side by
    side read as one run with a lighter column between them (RQ3E110AJ's
    11.0 A / 5.5 A pair: two 5 px cores, 100-150 gray between)."""
    if len(profile) < 8:
        return [offset + 0.5 * (len(profile) - 1)]
    core = profile < 100
    cores, start = [], None
    for i, flag in enumerate(core):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            cores.append((start, i - 1))
            start = None
    if start is not None:
        cores.append((start, len(profile) - 1))
    cores = [c for c in cores if c[1] - c[0] >= 1]
    if len(cores) == 2 and cores[1][0] - cores[0][1] >= 1:
        gap = profile[cores[0][1] + 1:cores[1][0]]
        if gap.size and gap.max() - max(profile[cores[0][0]:cores[0][1] + 1].min(), profile[cores[1][0]:cores[1][1] + 1].min()) >= 30:
            return [offset + 0.5 * (a + b) for a, b in cores]
    return [offset + 0.5 * (len(profile) - 1)]


def _track_up(gray, x: float, y: float, slope: float, plot: PlotBox, erased_cols, erased_rows,
              allow_fork: bool = True, side: int = 0, others=None) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]:
    """Follow a steep stroke upward, one row at a time, from (x, y).

    Returns (path, forks): the rows traced, and -- when the stroke is two
    lines printed side by side that separate higher up -- one further path
    per line from the split on. Rule rows are crossed on the prediction.
    """
    path: list[tuple[float, float]] = []
    miss, split_rows = 0, 0
    cy = int(round(y)) - 1
    top = plot.y0 + 1
    while cy > top:
        pred = x + slope * (cy - y)
        if 0 <= cy < len(erased_rows) and erased_rows[cy]:
            cy -= 1
            continue
        lo, hi = max(plot.x0 + 1, int(pred - ROW_WINDOW_PX - 8)), min(plot.x1 - 1, int(pred + ROW_WINDOW_PX + 8))
        runs = [r for r in _row_runs(gray, cy, lo, hi, erased_cols)
                if r["x0"] - ROW_WINDOW_PX <= pred <= r["x1"] + ROW_WINDOW_PX and r["x1"] - r["x0"] <= 16]
        if not runs:
            miss += 1
            if miss > ROW_MAX_MISS:
                break
            cy -= 1
            continue
        run = min(runs, key=lambda r: abs(r["centre"] - pred))
        if allow_fork and len(run["cores"]) == 2:
            split_rows += 1
            if split_rows >= ROW_SPLIT_ROWS:
                forks = [_track_up(gray, core, cy, slope, plot, erased_cols, erased_rows, allow_fork=False, side=side_)[0]
                         for core, side_ in zip(run["cores"], (-1, 1))]
                forks = [[(core, float(cy))] + f for core, f in zip(run["cores"], forks)]
                return path, forks
        else:
            split_rows = 0
        # after a split each line follows its own dark core, not the band
        beside = None
        if others is not None and len(others) and run["x1"] - run["x0"] + 1 >= BAND_MIN_WIDTH_PX:
            row_pts = others[np.abs(others[:, 1] - cy) <= 1.0]
            inside = row_pts[(row_pts[:, 0] >= run["x0"] - 1) & (row_pts[:, 0] <= run["x1"] + 1)]
            if len(inside):
                beside = float(inside[:, 0].mean())
        if beside is not None:
            # another traced curve shares this band (BRCS020N03RA's 25 C and
            # 125 C lines touch near the top): this line is the other half
            centre = run["x1"] - LINE_HALF_WIDTH_PX if beside < run["centre"] else run["x0"] + LINE_HALF_WIDTH_PX
        elif allow_fork:
            centre = run["centre"]
        elif len(run["cores"]) == 1 and run["x1"] - run["x0"] >= 8:
            # the pair reads as one band in this row: this line is its left or
            # right half, not the band's middle
            centre = run["x0"] + 2.5 if side < 0 else run["x1"] - 2.5
        else:
            centre = min(run["cores"], key=lambda c: abs(c - pred))
        nx = min(centre, x)                       # a decreasing curve: x does not grow upward
        if abs(nx - pred) > ROW_WINDOW_PX + 1:
            miss += 1
            if miss > ROW_MAX_MISS:
                break
            cy -= 1
            continue
        path.append((float(nx), float(cy)))
        miss = 0
        if len(path) >= 4:
            ys = np.asarray([p[1] for p in path[-12:]])
            xs = np.asarray([p[0] for p in path[-12:]])
            if ys.max() - ys.min() >= 3:
                slope = float(np.clip(np.polyfit(ys, xs, 1)[0], 0.0, 1.0 / ROW_MIN_STEEPNESS))
        x, y = nx, float(cy)
        cy -= 1
    return path, []


def extend_steep_heads(traces: list[Trace], gray, plot: PlotBox, erased_cols, erased_rows) -> list[Trace]:
    """Trace each raster curve's steep head up to the frame, row by row (F4-1).

    The column tracker samples one point per column, so a near-vertical
    stroke crossing grid rules loses its top (RQ3E110AJ stopped at 23.4 mOhm,
    RQ3E180AJ's branches at 39.7 / 48.2 mOhm, all printed up to the top
    frame). A head that is steep (dy/dx >= 1.5) and below the top frame is
    followed upward on the ink, across rule rows. A band of two lines printed
    side by side that separates higher up yields one trace per line, sharing
    the band below the split (served as coincident, F4-4).
    """
    out: list[Trace] = []
    for trace in traces:
        pts = trace.points_px
        hx, hy = pts[0]
        if hy <= plot.y0 + 3 or len(pts) < 4:
            out.append(trace)
            continue
        head = [p for p in pts[:12]]
        xs = np.asarray([p[0] for p in head])
        ys = np.asarray([p[1] for p in head])
        span_x = max(1.0, xs.max() - xs.min())
        if (ys.max() - ys.min()) / span_x < ROW_MIN_STEEPNESS:
            out.append(trace)
            continue
        slope = float(np.clip(np.polyfit(ys, xs, 1)[0], 0.0, 1.0 / ROW_MIN_STEEPNESS)) if ys.max() > ys.min() else 0.0
        others = [np.asarray(t.points_px, float) for t in traces if t is not trace]
        others = np.concatenate(others) if others else np.zeros((0, 2))
        path, forks = _track_up(gray, hx, hy, slope, plot, erased_cols, erased_rows, others=others)
        if len(forks) == 2:
            tops = [f[-1] for f in forks]
            others = [t.points_px for t in traces if t is not trace]
            if abs(tops[0][0] - tops[1][0]) <= 3 or any(
                    min(_polyline_distance(f[-1], o) for o in others) <= 3 for f in forks if others):
                # the "split" converged again, or runs onto a curve that has
                # its own trace: not a second printed line
                forks = [min(forks, key=lambda f: min((_polyline_distance(f[-1], o) for o in others), default=0.0) * -1)]
        if len(forks) == 2:
            out.extend(_split_band(trace, forks, gray, plot, erased_cols, erased_rows))
            continue
        extension = list(reversed(path + (forks[0] if forks else [])))
        if not extension:
            out.append(trace)
            continue
        out.append(replace_trace(trace, extension + list(pts), row_traced_points=extension))
    return out


# ---------------------------------------------------------------------------
# Round 5: flat right ends traced to the frame
#
# The column tracker keeps RASTER_EDGE_MARGIN_PX clear of the frame, so every
# raster curve that runs to the right frame stopped 3 px short of it
# (BRCS020N03RA: 9.965 V of a 10 V frame, so its 10 V table row was
# not_evaluable). The ink is followed on from the tracker's last point,
# column by column, up to the frame. The frame stroke itself is dark in every
# row, so a curve cannot be measured inside it; where the curve's stroke shows
# again just OUTSIDE the frame (its end pokes past the frame's outer edge),
# the frame columns are bridged between the ink measured on both sides -- the
# same as an erased grid rule is bridged. Without ink on the far side nothing
# is added inside the frame stroke: no extrapolation.

FRAME_TAIL_START_PX = RASTER_EDGE_MARGIN_PX + 1   # the tracker stopped at its margin, not on its own
FRAME_COLUMN_DARK = 0.9       # a column dark in >= 90 % of the plot rows is the frame stroke
FRAME_SEARCH_PX = 4           # the frame stroke is looked for within +-4 px of plot.x1
FRAME_FAR_SIDE_PX = 3         # the curve's end is looked for this far past the frame's outer edge
FRAME_TAIL_WINDOW_PX = 3.0    # a column's run must centre within this of the previous centre
FRAME_TAIL_MAX_RUN_PX = 25    # longer runs are not one curve stroke


def _frame_columns(gray, plot: PlotBox) -> list[int]:
    cols = []
    for x in range(plot.x1 - FRAME_SEARCH_PX, plot.x1 + FRAME_SEARCH_PX + 1):
        if 0 <= x < gray.shape[1] and (gray[plot.y0 + 3:plot.y1 - 2, x] < ROW_INK_GRAY).mean() >= FRAME_COLUMN_DARK:
            cols.append(x)
    return cols


def _run_near(gray, x: int, y: float, plot: PlotBox) -> float | None:
    """Centre of the dark run in column x nearest y, if it centres within the window."""
    if not 0 <= x < gray.shape[1]:
        return None
    lo, hi = plot.y0 + 3, plot.y1 - 2
    dark = gray[lo:hi, x] < ROW_INK_GRAY
    best = None
    start = None
    for i, d in enumerate(list(dark) + [False]):
        if d and start is None:
            start = i
        elif not d and start is not None:
            centre, length = lo + 0.5 * (start + i - 1), i - start
            if length <= FRAME_TAIL_MAX_RUN_PX and abs(centre - y) <= FRAME_TAIL_WINDOW_PX:
                if best is None or abs(centre - y) < abs(best - y):
                    best = centre
            start = None
    return best


def extend_tails_to_frame(traces: list[Trace], gray, plot: PlotBox) -> list[Trace]:
    """Follow each raster curve's right end from the tracker's margin to the frame."""
    from dataclasses import replace
    frame = _frame_columns(gray, plot)
    if not frame or frame != list(range(frame[0], frame[-1] + 1)):
        return traces
    inner, outer = frame[0], frame[-1]
    out = []
    for trace in traces:
        if trace.method != "raster" or not trace.points_px:
            out.append(trace)
            continue
        x_last, y_last = max(trace.points_px, key=lambda p: (p[0], -p[1]))
        if x_last < plot.x1 - FRAME_TAIL_START_PX or not plot.y0 + 3 < y_last < plot.y1 - 3:
            out.append(trace)
            continue
        measured, y = [], y_last
        x = int(round(x_last)) + 1
        while x < min(inner, plot.x1 + 1):
            centre = _run_near(gray, x, y, plot)
            if centre is None:
                break
            measured.append((float(x), centre))
            y = centre
            x += 1
        bridged, far = [], None
        if x == inner and inner <= plot.x1:
            for xf in range(outer + 1, outer + 1 + FRAME_FAR_SIDE_PX):
                centre = _run_near(gray, xf, y, plot)
                if centre is not None:
                    far = (float(xf), centre)
                    break
            if far is not None:
                xa, ya = measured[-1] if measured else (x_last, y_last)
                bridged = [(float(xb), ya + (far[1] - ya) * (xb - xa) / (far[0] - xa))
                           for xb in range(inner, plot.x1 + 1)]
        if not measured and not bridged:
            out.append(trace)
            continue
        note = {"measured_px": measured, "across_frame_stroke_px": bridged,
                "far_side_ink_px": far, "frame_stroke_px": [inner, outer]}
        out.append(replace(trace, points_px=list(trace.points_px) + measured + bridged, frame_traced=note))
    return out


BAND_MIN_WIDTH_PX = 8       # a steep run this wide is two lines side by side
LINE_HALF_WIDTH_PX = 2.5


# ---------------------------------------------------------------------------
# F6-1: stretches the trackers did not sample, traced on the curve's own ink
#
# Between two consecutive samples more than GAP_PX apart (the gate module's
# 2.5 px), the column and row trackers left stretches where the ink runs on
# (RQ3E110AJ's steep band below the ID leader tips, grid-rule crossings on
# WSR3090, merge points on RQ6E080AJ / RQ3E180AJ / BRCS020N03RA). v6 served
# them as "ink is continuous but was not sampled". Here the ink is followed
# from one sample to the next, both of which are on the curve:
#   * steep (|dy| > |dx|): row by row, like F4-1. Rule rows are crossed on the
#     prediction, never sampled. In a band of two lines side by side (a run
#     >= BAND_MIN_WIDTH_PX) the curve takes its own half -- the half its ink
#     is on at the end(s) of the stretch that lie in the band; if its two ends
#     disagree, or neither end tells, the stretch is refused and says so.
#   * flat: column by column. A column on a grid rule (the rule within
#     +-ROW_WINDOW_PX of the prediction, or an erased rule column) cannot be
#     measured: it is interpolated between the measured columns beside it, as
#     erased rules are elsewhere; the points say so (bridged).
# Each step must find ink within ROW_WINDOW_PX + 1 of the path predicted
# toward the far sample, and the followed path must arrive within
# GAP_ARRIVAL_PX of it; otherwise nothing is added and the concrete cause is
# recorded. Points are only ever ADDED: no existing sample moves.

GAP_MIN_PX = 2.5              # the same threshold the gate module lists gaps by (rdson_gate_voltage.GAP_PX)
GAP_ARRIVAL_PX = 3.0          # the followed ink must end this close to the far sample
GAP_MAX_RUN_PX = 16           # a longer run is a rule, a leader or a label touching the stroke
GAP_CHORD_MAX_PX = 6.0        # a stretch this short may be joined straight if every chord pixel is ink
GAP_MAX_MISS = 3              # rows/columns without the stroke before a stretch is refused (as ROW_MAX_MISS)
STUB_ON_STROKE_PX = 1.5       # a dropped end stub this close to the curve's own stroke continues it
_NO_RULES = np.zeros(0, dtype=bool)


def _gap_pairs(trace: Trace):
    pts = sorted(trace.points_px, key=lambda p: (p[0], p[1]))
    removed = list(trace.contact_removed_x)
    for a, b in zip(pts, pts[1:]):
        if b[0] - a[0] <= GAP_MIN_PX:
            continue
        if any(a[0] < x < b[0] for x in removed):
            continue          # an annotation contact (arrow tip): a different, stated kind
        yield a, b


def _on_rule_cols(run, erased_cols) -> bool:
    return all(0 <= x < len(erased_cols) and erased_cols[x] for x in range(run["x0"], run["x1"] + 1))


def _band_side(gray, point, erased_cols) -> int | None:
    """-1 / +1 when ``point`` is on the left / right half of a two-line band
    in its row (measured on the raw ink, rules included), 0 when the run is
    one line wide or the point sits at the band's middle (a coincident, shared
    sample), None when its row has no run there."""
    x, y = point
    runs = [r for r in _row_runs(gray, int(round(y)), int(x) - 14, int(x) + 14, _NO_RULES)
            if r["x0"] - 1 <= x <= r["x1"] + 1]
    if not runs:
        return None
    run = runs[0]
    if run["x1"] - run["x0"] + 1 < BAND_MIN_WIDTH_PX or abs(x - run["centre"]) <= 1.0:
        return 0
    return -1 if x < run["centre"] else 1


def _interpolate_skipped(skipped, anchors, axis: int):
    """Points for skipped rule rows (axis 1) or rule columns (axis 0),
    interpolated between the measured anchors on either side."""
    out = []
    for v in skipped:
        before = [p for p in anchors if p[axis] < v]
        after = [p for p in anchors if p[axis] > v]
        if not before or not after:
            continue
        lo, hi = max(before, key=lambda p: p[axis]), min(after, key=lambda p: p[axis])
        t = (v - lo[axis]) / (hi[axis] - lo[axis])
        other = lo[1 - axis] + t * (hi[1 - axis] - lo[1 - axis])
        out.append((float(v), float(other)) if axis == 0 else (float(other), float(v)))
    return out


def _follow_rows(gray, a, b, plot: PlotBox, erased_cols, erased_rows):
    """Steep stretch: one point per row on the curve's own ink (or half of a two-line band)."""
    sides = {s for s in (_band_side(gray, a, erased_cols), _band_side(gray, b, erased_cols)) if s}
    measured, skipped, (lx, ly), miss = [], [], a, 0
    for cy in range(int(round(a[1])) + 1, int(round(b[1]))):
        if 0 <= cy < len(erased_rows) and erased_rows[cy]:
            skipped.append(cy)
            continue
        pred = lx + (b[0] - lx) * (cy - ly) / max(1e-6, b[1] - ly)
        runs = [r for r in _row_runs(gray, cy, max(plot.x0 + 1, int(pred) - 14), min(plot.x1 - 1, int(pred) + 14), _NO_RULES)
                if r["x0"] - ROW_WINDOW_PX <= pred <= r["x1"] + ROW_WINDOW_PX and r["x1"] - r["x0"] + 1 <= GAP_MAX_RUN_PX]
        runs = [r for r in runs if not _on_rule_cols(r, erased_cols)]
        if not runs:
            miss += 1
            if miss > GAP_MAX_MISS:
                return None, None, f"no ink of one stroke within {ROW_WINDOW_PX:g} px of the path in rows {cy - miss + 1}..{cy}"
            continue
        run = min(runs, key=lambda r: abs(r["centre"] - pred))
        wide = run["x1"] - run["x0"] + 1 >= BAND_MIN_WIDTH_PX
        if len(run["cores"]) == 2:
            centre = min(run["cores"], key=lambda c: abs(c - pred))
        elif wide:
            if len(sides) != 1:
                why = "its two ends lie on different halves" if len(sides) > 1 else "neither end shows which half is its own"
                return None, None, (f"two touching lines print as one {run['x1'] - run['x0'] + 1} px band at row {cy}, "
                                    f"and {why}: the ink cannot be assigned to this curve")
            centre = run["x0"] + LINE_HALF_WIDTH_PX if next(iter(sides)) < 0 else run["x1"] - LINE_HALF_WIDTH_PX
        else:
            centre = run["centre"]
        if abs(centre - pred) > ROW_WINDOW_PX + 1:
            miss += 1
            if miss > GAP_MAX_MISS:
                return None, None, f"the ink leaves the path toward the next sample at row {cy}"
            continue
        nx = max(centre, lx)                      # a decreasing curve: x does not shrink downward
        measured.append((float(nx), float(cy)))
        lx, ly, miss = nx, float(cy), 0
    return measured, _interpolate_skipped(skipped, [a] + measured + [b], 1), None


def _follow_columns(gray, a, b, plot: PlotBox, erased_cols, erased_rows):
    """Flat stretch: one point per column on the curve's ink; columns on a rule are bridged."""
    measured, skipped, (lx, ly), miss = [], [], a, 0
    rule_rows = np.flatnonzero(erased_rows) if len(erased_rows) else np.zeros(0)
    for cx in range(int(round(a[0])) + 1, int(round(b[0]))):
        pred = ly + (b[1] - ly) * (cx - lx) / max(1e-6, b[0] - lx)
        on_rule = (0 <= cx < len(erased_cols) and erased_cols[cx]) or bool(
            len(rule_rows) and np.min(np.abs(rule_rows - pred)) <= ROW_WINDOW_PX + 1)
        if on_rule:
            skipped.append(cx)
            continue
        column = gray[plot.y0 + 3:plot.y1 - 2, cx] < ROW_INK_GRAY
        best, start = None, None
        for i, dark in enumerate(list(column) + [False]):
            if dark and start is None:
                start = i
            elif not dark and start is not None:
                centre, length = plot.y0 + 3 + 0.5 * (start + i - 1), i - start
                if length <= GAP_MAX_RUN_PX and abs(centre - pred) <= ROW_WINDOW_PX + 1:
                    if best is None or abs(centre - pred) < abs(best - pred):
                        best = centre
                start = None
        if best is None:
            miss += 1
            if miss > GAP_MAX_MISS:
                return None, None, f"no ink of one stroke within {ROW_WINDOW_PX + 1:g} px of the path in columns {cx - miss + 1}..{cx}"
            continue
        measured.append((float(cx), float(best)))
        lx, ly, miss = float(cx), float(best), 0
    return measured, _interpolate_skipped(skipped, [a] + measured + [b], 0), None


def _chord_on_ink(gray, a, b):
    """Points at every column along the straight chord a..b, if every chord pixel is ink."""
    length = float(np.hypot(b[0] - a[0], b[1] - a[1]))
    if length > GAP_CHORD_MAX_PX:
        return None
    for t in np.linspace(0.0, 1.0, int(4 * length) + 2):
        x, y = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
        if gray[int(round(y)), int(round(x))] >= ROW_INK_GRAY:
            return None
    return [(float(x), float(a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])))
            for x in range(int(np.floor(a[0])) + 1, int(np.ceil(b[0])))]


def fill_unsampled_stretches(traces: list[Trace], gray, plot: PlotBox, erased_cols, erased_rows) -> list[Trace]:
    """Trace every unsampled stretch of every raster curve on its own ink (F6-1).

    All or nothing per stretch: the followed points (measured, plus rule rows
    or columns bridged between them) must leave no step wider than GAP_MIN_PX
    and must arrive at the far sample; otherwise nothing is added and the
    concrete cause is recorded. A stretch no longer than GAP_CHORD_MAX_PX
    whose straight chord is ink at every pixel is joined along the chord.
    """
    from dataclasses import replace
    out = []
    for trace in traces:
        if trace.method != "raster" or len(trace.points_px) < 2:
            out.append(trace)
            continue
        added, notes = [], []
        for a, b in list(_gap_pairs(trace)):
            steep = abs(b[1] - a[1]) > abs(b[0] - a[0])
            follow = _follow_rows if steep else _follow_columns
            measured, bridged, why = follow(gray, a, b, plot, erased_cols, erased_rows)
            mode = "rows" if steep else "columns"
            if why is None:
                off_ink = [p for p in bridged if not _on_ink(gray, p)]
                if off_ink:
                    why = (f"a rule crossing bridged from ({a[0]:.1f}, {a[1]:.1f}) to ({b[0]:.1f}, {b[1]:.1f}) would "
                           f"put {len(off_ink)} point(s) off the ink")
            if why is None:
                tail = _chord_on_ink(gray, max(measured + bridged + [a], key=lambda p: (p[0], p[1])), b)
                if tail:
                    measured = measured + tail          # the last step into the far sample, on ink
                path = sorted([a] + measured + bridged + [b], key=lambda p: (p[0], p[1]))
                step = max(q[0] - p[0] for p, q in zip(path, path[1:]))
                last = max(measured + bridged + [a], key=lambda p: p[1] if steep else p[0])
                off = abs(last[0] - b[0]) if steep else abs(last[1] - b[1])
                if step > GAP_MIN_PX or off > GAP_ARRIVAL_PX:
                    why = (f"the ink followed from ({a[0]:.1f}, {a[1]:.1f}) does not reach the next sample "
                           f"({b[0]:.1f}, {b[1]:.1f}): it ends {off:.1f} px off it, largest step {step:.1f} px")
            if why is not None:
                chord = _chord_on_ink(gray, a, b)
                if chord is not None:
                    measured, bridged, mode, why = chord, [], "chord_on_ink", None
            note = {"from_px": [float(a[0]), float(a[1])], "to_px": [float(b[0]), float(b[1])], "mode": mode}
            if why is not None:
                note["refused"] = why
            else:
                note["measured_px"], note["bridged_on_rule_px"] = measured, bridged
                added.extend(measured + bridged)
            notes.append(note)
        stubs, kept_stubs, decisions = list(trace.dropped_stub_points), [], []
        path = list(trace.points_px) + added
        for stub in stubs:
            decision = _stub_decision(gray, stub, path, erased_cols, erased_rows)
            decisions.append(decision)
            if decision["served"]:
                added.append((float(stub[0]), float(stub[1])))
            else:
                kept_stubs.append(stub)
        if not notes and not decisions:
            out.append(trace)
            continue
        # interior points go between the trace's first and last points: the
        # end checks (_open_ends, _ink_reaches_frame) read points_px[0] / [-1]
        pts = list(trace.points_px)
        out.append(replace(trace, points_px=pts[:-1] + added + pts[-1:], gap_traced=notes,
                           dropped_stub_points=kept_stubs, stub_decisions=decisions))
    return out


def _on_ink(gray, point) -> bool:
    x, y = int(round(point[0])), int(round(point[1]))
    return bool((gray[max(0, y - 1):y + 2, max(0, x - 1):x + 2] < ROW_INK_GRAY).any())


def _stub_decision(gray, stub, path, erased_cols, erased_rows) -> dict:
    """Serve a dropped end stub that continues the curve's own stroke; else say concretely what it is."""
    x, y = float(stub[0]), float(stub[1])
    distance = _polyline_distance((x, y), sorted(path, key=lambda p: (p[1], p[0]))) if len(path) > 1 else float("inf")
    row = _row_runs(gray, int(round(y)), max(0, int(x) - 30), min(gray.shape[1] - 1, int(x) + 30), _NO_RULES)
    run = next((r for r in row if r["x0"] <= x <= r["x1"]), None)
    decision = {"stub_px": [x, y], "distance_to_own_stroke_px": round(distance, 2), "served": False}
    if distance <= STUB_ON_STROKE_PX and _on_ink(gray, (x, y)):
        decision["served"] = True
        decision["why"] = f"on this curve's own stroke ({distance:.1f} px from it)"
        return decision
    what = []
    # a rule's anti-aliased edge rows lie outside its erased core: +-2 px
    rows = [r for r in range(int(round(y)) - 2, int(round(y)) + 3) if 0 <= r < len(erased_rows) and erased_rows[r]]
    cols = [c for c in range(int(round(x)) - 2, int(round(x)) + 3) if 0 <= c < len(erased_cols) and erased_cols[c]]
    if rows:
        what.append(f"it lies on the horizontal grid rule at rows {rows[0]}..{rows[-1]} where the curve crosses it")
    if cols:
        what.append(f"it lies on the vertical grid rule at columns {cols[0]}..{cols[-1]} where the curve crosses it")
    if run is not None and run["x1"] - run["x0"] + 1 > GAP_MAX_RUN_PX:
        what.append(f"its row is a {run['x1'] - run['x0'] + 1} px run (a leader or rule joining the stroke)")
    if not _on_ink(gray, (x, y)):
        what.append("it is not on ink")
    decision["why"] = ("; ".join(what) or "it is off the stroke") + f"; {distance:.1f} px from this curve's own stroke"
    return decision


def _track_band_down(gray, x: float, y: float, plot: PlotBox, erased_cols, erased_rows) -> list[tuple[float, int, int]]:
    """Rows (y, x0, x1) of a two-line band, from its split downward while it
    stays wider than one line (>= BAND_MIN_WIDTH_PX) and steep. Rows where a
    rule or a label leader touches the band (run > 12 px) are crossed, not
    measured."""
    rows: list[tuple[float, int, int]] = []
    narrow = 0
    cy = int(round(y)) + 1
    while cy < plot.y1 - 1:
        if 0 <= cy < len(erased_rows) and erased_rows[cy]:
            cy += 1
            continue
        lo, hi = max(plot.x0 + 1, int(x - 12)), min(plot.x1 - 1, int(x + 12))
        runs = [r for r in _row_runs(gray, cy, lo, hi, erased_cols) if r["x0"] - 3 <= x <= r["x1"] + 3]
        if not runs:
            break
        run = min(runs, key=lambda r: abs(r["centre"] - x))
        if run["x1"] - run["x0"] + 1 > 12:
            cy += 1                                # rule or leader contact: cross it
            continue
        edge_on_rule = any(0 <= c < len(erased_cols) and erased_cols[c] for c in (run["x0"] - 1, run["x1"] + 1))
        if edge_on_rule:
            cy += 1                                # an edge hidden in an erased rule: width unknown here
            continue
        if run["centre"] < x - 1.0:
            break                                  # the curve turned: not the steep band
        if run["x1"] - run["x0"] + 1 < BAND_MIN_WIDTH_PX:
            narrow += 1
            if narrow >= 3:
                break                              # one line's width: the pair has merged
        else:
            narrow = 0
            rows.append((float(cy), run["x0"], run["x1"]))
        x = run["centre"]
        if len(rows) >= 10:
            ys = np.asarray([r[0] for r in rows[-10:]])
            xs = np.asarray([0.5 * (r[1] + r[2]) for r in rows[-10:]])
            if np.polyfit(ys, xs, 1)[0] > 1.0 / ROW_MIN_STEEPNESS:
                break                              # no longer steep: the column tracker's part
        cy += 1
    return rows


STACK_MAX_SLOPE = 1.0        # columns split a stacked pair only where the curve is this flat
STACK_MARGIN_PX = 4.0        # a two-line column run exceeds one line's height by this much
STACK_MIN_RUN = 15           # ... in this many consecutive samples (no toggling on noise)


def _local_slopes(points) -> list[float]:
    """|dy/dx| at each point, from its neighbours four places away."""
    pts = np.asarray(points, dtype=float)
    out = []
    for k in range(len(pts)):
        a, b = pts[max(0, k - 4)], pts[min(len(pts) - 1, k + 4)]
        out.append(abs(b[1] - a[1]) / max(1.0, abs(b[0] - a[0])))
    return out


def _column_halves(gray, points, upper: int, erased_rows) -> list[tuple[float, float]]:
    """Each point's half of a stacked two-line run, applied only over
    stretches of >= STACK_MIN_RUN consecutive two-line samples."""
    halves = [_column_half(gray, p, upper, erased_rows, slope) for p, slope in zip(points, _local_slopes(points))]
    split = [h != tuple(p) and h != p for h, p in zip(halves, points)]
    out, k = list(points), 0
    while k < len(points):
        if not split[k]:
            k += 1
            continue
        j = k
        while j < len(points) and split[j]:
            j += 1
        if j - k >= STACK_MIN_RUN:
            out[k:j] = halves[k:j]
        k = j
    return out


def _column_half(gray, point, upper: int, erased_rows, slope: float = 0.0) -> tuple[float, float]:
    """This line's half of a two-line column run at ``point``; the point
    itself where the run is one line thick (a sloped line's column run is
    taller: allowed for), where the curve is steeper than STACK_MAX_SLOPE,
    or where the run touches a rule row."""
    if slope > STACK_MAX_SLOPE:
        return point
    x, y = int(round(point[0])), int(round(point[1]))
    if not (0 <= x < gray.shape[1] and 0 <= y < gray.shape[0]) or gray[y, x] >= ROW_INK_GRAY:
        return point
    y0 = y1 = y
    while y0 - 1 >= 0 and gray[y0 - 1, x] < ROW_INK_GRAY:
        y0 -= 1
    while y1 + 1 < gray.shape[0] and gray[y1 + 1, x] < ROW_INK_GRAY:
        y1 += 1
    height = y1 - y0 + 1
    one_line = 2 * LINE_HALF_WIDTH_PX * math.sqrt(1.0 + slope * slope)
    if height < one_line + STACK_MARGIN_PX or height > 16 or erased_rows[max(0, y0 - 1):y1 + 2].any():
        return point
    return (point[0], float(y0 + LINE_HALF_WIDTH_PX if upper > 0 else y1 - LINE_HALF_WIDTH_PX))


def _split_band(trace: Trace, forks, gray, plot: PlotBox, erased_cols, erased_rows) -> list[Trace]:
    """Two printed lines that run side by side as one band (RQ3E110AJ's
    11.0 A / 5.5 A pair) become two traces: each line's own top from the
    split, then its half of the band row by row down to where the band
    narrows to one line, then the shared single line (coincident, F4-4)."""
    split_x, split_y = forks[0][0][0], forks[0][0][1]
    band = _track_band_down(gray, split_x, split_y, plot, erased_cols, erased_rows)
    below = band[-1][0] if band else split_y
    rest = [p for p in trace.points_px if p[1] > below + 0.5]
    out = []
    for fork, side in zip(forks, (-1, 1)):
        halves, last = [], -1e9
        for y, x0, x1 in band:
            last = max(last, (x0 + LINE_HALF_WIDTH_PX) if side < 0 else (x1 - LINE_HALF_WIDTH_PX))
            halves.append((last, y))               # x never decreases downward on a falling curve
        head = list(reversed(fork))
        extension = head + halves
        # below the steep band the pair runs on stacked in each column: the
        # left line is the LOWER half there (less RDS at the same VGS)
        tail = _column_halves(gray, rest, side, erased_rows)
        new = replace_trace(trace, extension + tail, row_traced_points=extension)
        new.tail_from = new.tail_from + [{"kind": "band_split", "side": "left" if side < 0 else "right",
                                          "split_px": [float(split_x), float(split_y)],
                                          "band_rows": len(band),
                                          "band_ends_px_y": float(below)}]
        out.append(new)
    return out


def replace_trace(trace: Trace, points, **extra) -> Trace:
    new = Trace(list(points), trace.method, trace.style, trace.merged_columns, dict(trace.params),
                dict(trace.binding), trace.bridged_columns, list(trace.contact_removed_x),
                list(trace.dropped_stub_points), list(extra.get("row_traced_points", trace.row_traced_points)),
                list(extra.get("tail_from", trace.tail_from)))
    return new


# ---------------------------------------------------------------------------
# F4-4: serve exactly the printed curves

BRANCH_JOIN_PX = 6.0        # a branch end this close to another trace flows into it


def _polyline_distance(point, points) -> float:
    p = np.asarray(point, dtype=float)
    q = np.asarray(points, dtype=float)
    if len(q) == 1:
        return float(np.hypot(*(q[0] - p)))
    a, b = q[:-1], q[1:]
    ab = b - a
    t = np.clip(((p - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0.0, 1.0)
    closest = a + t[:, None] * ab
    return float(np.sqrt(((closest - p) ** 2).sum(1)).min())


def group_branches(traces: list[Trace], plot: PlotBox) -> list[Trace]:
    """Each printed curve = its own branch + the tail it merges into (F4-4).

    The column tracker follows two steep branches as separate tracks and the
    merged stretch below them as a third (RQ6E080AJ: 4.0 A and 8.0 A branches
    and their shared tail). A trace that stops inside the plot on another
    trace flows into it and takes over its continuation; a trace whose head
    only repeats such a branch is a tail, and a tail is never served as a
    curve of its own. The shared stretch is then coincident (recorded by
    the digitizer), never silently duplicated.
    """
    margin = 7.5
    joins: dict[int, tuple[int, float]] = {}
    for i, trace in enumerate(traces):
        end = trace.points_px[-1]
        if not (end[0] < plot.x1 - margin and end[1] < plot.y1 - margin):
            continue
        best = None
        for j, other in enumerate(traces):
            if j == i or not any(p[0] > end[0] + 0.5 for p in other.points_px):
                continue
            distance = _polyline_distance(end, other.points_px)
            if distance <= BRANCH_JOIN_PX and (best is None or distance < best[0]):
                best = (distance, j)
        if best is not None:
            joins[i] = (best[1], end[0])
    tails = {j for i, (j, _x) in joins.items()
             if _polyline_distance(traces[j].points_px[0], traces[i].points_px) <= BRANCH_JOIN_PX}

    def full_points(i: int, seen: frozenset) -> list:
        own = list(traces[i].points_px)
        if i not in joins or joins[i][0] in seen:
            return own
        j, _x_end = joins[i]
        tail = full_points(j, seen | {i})
        # continue from the tail point nearest the junction, in the tail's own
        # order: a steep tail is still above the junction for some columns
        end = np.asarray(own[-1], dtype=float)
        k = int(np.argmin([np.hypot(p[0] - end[0], p[1] - end[1]) for p in tail]))
        # a dropped tail's head re-sampled this branch: its samples are real
        # ink of this line and are kept (RQ6E080AJ: 381 px, 243.5-262 on the
        # 8 A line), not lost with the tail
        head = [p for p in tail[:k + 1] if p not in own and _polyline_distance(p, own) <= 3.0] \
            if j in tails else []
        merged = sorted(own + head, key=lambda p: (p[0], p[1])) if head else own
        return merged + [p for p in tail[k + 1:] if p[0] > end[0] - 0.5 and p[1] >= end[1] - 1.0]

    out = []
    for i, trace in enumerate(traces):
        if i in tails:
            continue
        if i in joins:
            j, x_end = joins[i]
            out.append(replace_trace(trace, full_points(i, frozenset({i})),
                                     tail_from=trace.tail_from + [{"kind": "shared_tail", "from_x_px": float(x_end),
                                                                   "tail_was_separate": j in tails}]))
        else:
            out.append(trace)
    return out


# ---------------------------------------------------------------------------
# F4-3: raster label arrows and leader lines, followed to their tip

LEADER_MIN_LENGTH_PX = 25.0
LEADER_MIN_ELONGATION = 8.0
LEADER_LABEL_REACH_PX = 25.0     # the tail starts this close to its label box
LEADER_MAX_MISS = 4              # steps without ink before the line has ended
LEADER_TIP_CLEAR_PX = 8          # the ink just before a tip may touch only the tip's curve


def _line_ink(raw, p, d) -> np.ndarray | None:
    perp = np.array([-d[1], d[0]])
    for offset in (0.0, -1.0, 1.0, -2.0, 2.0):
        q = p + offset * perp
        xi, yi = int(round(q[0])), int(round(q[1]))
        if 0 <= yi < raw.shape[0] and 0 <= xi < raw.shape[1] and raw[yi, xi]:
            return q
    return None


def _follow_straight(raw, start, through, seed_points, max_len: float = 500.0) -> np.ndarray:
    """From ``start`` through ``through``, follow a straight stroke to its end.

    The line is refitted on the ink accepted so far, so it stays on the
    stroke across curve and rule crossings (a crossing is ink ON the line);
    it ends after LEADER_MAX_MISS steps without ink. Returns the last ink
    point (the tip) and the unbroken stretch of ink that ends there."""
    accepted = [tuple(p) for p in seed_points]
    direction = np.asarray(through, float) - np.asarray(start, float)
    direction /= max(np.linalg.norm(direction), 1e-9)
    origin = np.asarray(through, float)
    tip, t, miss = origin.copy(), 0.0, 0
    last_run: list = [origin.copy()]
    while miss <= LEADER_MAX_MISS and t < max_len:
        pts = np.asarray(accepted[-300:], float)
        centre = pts.mean(0)
        _u, _s, vt = np.linalg.svd(pts - centre, full_matrices=False)
        axis = vt[0] if vt[0] @ direction > 0 else -vt[0]
        t += 1.0
        p = centre + ((origin - centre) @ axis + t) * axis
        q = _line_ink(raw, p, axis)
        if q is None:
            miss += 1
        else:
            if miss:
                last_run = []               # white on the line: a new stretch of ink begins
            tip, miss = q, 0
            last_run.append(q)
            accepted.append(tuple(q))
    return tip, last_run


def raster_leaders(gray, plot: PlotBox, traces: list[Trace], grid_x, grid_y, labels: list[Label],
                   ocr_tail=None) -> tuple[list[Leader], list[Label]]:
    """Label arrows and leader lines of a raster chart (review F4-3).

    WSR3090 points each "TJ=..." label at its curve with an arrow; the 125 C
    arrow crosses the 100 C curve on its way to the top one, so nearness
    binds it wrongly. A thin straight stroke that starts at a label box (or,
    with ``ocr_tail``, at text read there) is followed to where the stroke
    ends -- its tip -- across any curve or rule it crosses. The binder then
    names the curve at the tip (``_leader_target``). Returns the leaders and
    any labels read at a tail that the plot OCR had missed.
    """
    raw = gray < ROW_INK_GRAY
    ink = np.zeros_like(raw)
    ink[plot.y0 + 3:plot.y1 - 2, plot.x0 + 3:plot.x1 - 2] = raw[plot.y0 + 3:plot.y1 - 2, plot.x0 + 3:plot.x1 - 2]
    ink = ink.astype(np.uint8)
    for x in grid_x:
        ink[:, max(0, int(round(x)) - 2):int(round(x)) + 3] = 0
    for y in grid_y:
        ink[max(0, int(round(y)) - 2):int(round(y)) + 3, :] = 0
    for trace in traces:
        pts = np.round(np.asarray(trace.points_px, float)).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(ink, [pts], False, 0, 7)
    count, comp, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    curve_pts = np.concatenate([np.asarray(t.points_px, float) for t in traces]) if traces else np.zeros((0, 2))
    leaders: list[Leader] = []
    found: list[Label] = []
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] < 20:
            continue
        ys, xs = np.nonzero(comp == index)
        pts = np.c_[xs, ys].astype(float)
        centre = pts.mean(0)
        _u, sv, vt = np.linalg.svd(pts - centre, full_matrices=False)
        if len(sv) < 2:
            continue
        length = sv[0] / math.sqrt(len(pts)) * math.sqrt(12.0)
        if length < LEADER_MIN_LENGTH_PX or sv[0] < LEADER_MIN_ELONGATION * max(sv[1], 1e-6):
            continue
        along = (pts - centre) @ vt[0]
        ends = (pts[int(along.argmin())], pts[int(along.argmax())])
        if not len(curve_pts):
            continue
        # the tip is at a curve, so the tail is the end farther from the curves
        to_curve = [float(np.hypot(*(curve_pts - end).T).min()) for end in ends]
        tail, head = (ends[0], ends[1]) if to_curve[0] >= to_curve[1] else (ends[1], ends[0])
        if max(to_curve) < 8.0:
            continue                               # both ends on curves: curve ink, not a leader
        on_line = pts[np.abs((pts - centre) @ np.array([-vt[0][1], vt[0][0]])) <= 1.5]
        # the component may stop at an erased grid rule short of its label
        # (RQ6E080AJ's 8.0 A leader): follow it back to where the line starts
        tail = _follow_straight(raw, head, tail, on_line, max_len=120.0)[0]
        if min((_box_distance(tail, label) for label in labels if label.params), default=1e9) > LEADER_LABEL_REACH_PX:
            if ocr_tail is None:
                continue
            label = ocr_tail(tail, head)
            if label is None or not label.params:
                continue
            found.append(label)
        tip, last_run = _follow_straight(raw, tail, head, on_line)
        # the last LEADER_TIP_CLEAR_PX of unbroken ink before the tip must
        # touch ONE curve: lines that touch side by side (RQ3E110AJ's 11.0 A /
        # 5.5 A band) leave the tip unreadable. A curve crossed further back
        # (WSR3090's 125 C arrow over the 100 C curve, RQ3E180AJ's 9 A leader
        # over the 18 A branch, 16 px before its tip) does not.
        end = last_run[-LEADER_TIP_CLEAR_PX:]
        touched = {k for k, trace in enumerate(traces)
                   if any(_polyline_distance(q, trace.points_px) <= 2.0 for q in end)}
        leaders.append(Leader((tuple(map(float, tail)), tuple(map(float, tip))), raster=True,
                              ambiguous=len(touched) > 1))
    return leaders, found
