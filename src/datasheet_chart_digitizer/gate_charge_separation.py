"""Separate the individual VDD/VDS curves of a gate-charge chart.

A gate-charge chart with several VDD curves draws them sharing the 0 -> Vth
rise and usually the Miller plateau; above the plateau each curve rises on its
own stroke. The legacy tracer reduces all of them to one column-wise curve
(``gate_charge_trace._trace_vector_gate_curve``), which on a two-curve chart is
the average of both strokes (IPB180N04S4 Figure 15). This module returns each
source curve separately, every one following its own stroke:

* **Vector** (``separate_vector_curves``): the dark stroke segments inside the
  plot form a graph whose nodes are the segment endpoints (and the points where
  one segment ends on another). Edges are oriented left to right (upwards for a
  vertical edge), which makes it a DAG. Every source -> sink path that is a
  physical VGS(Qg) curve (spans the plot, starts at the left edge, never falls)
  is one source curve. A shared segment belongs to every path through it, so the
  shared rise and plateau are in every curve, while each curve's own part is
  its own stroke. Two curves that diverge and meet again cannot be told apart
  at the meeting point: the separation is refused, never guessed.
* **Raster** (``separate_raster_curves``): above the shared plateau each curve
  crosses every image row exactly once. Rows holding the modal number K >= 2 of
  ink runs are sorted left to right (gate-charge curves do not cross), giving
  K tracks; each track is continued down to the plateau, and the shared rise
  and plateau come from the legacy trace. The shared part must really be
  shared (one ink run per row below the plateau) or the separation is refused.

Everything here is identity-free: which VDD a curve carries is
``gate_charge_vdd_labels``' job.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Endpoint snap and "endpoint lies on another segment" tolerance (pt).
NODE_TOLERANCE_PT = 0.6
# Stroke segments thinner than this fraction of the widest curve stroke are
# annotations (leader lines, arrows), not curves.
CURVE_WIDTH_FRACTION = 0.5
# A path is a gate curve only if it spans this much of the plot.
MIN_X_SPAN = 0.30
MIN_Y_SPAN = 0.20
# ... and starts this close to the plot's left edge (same as the legacy
# _curve_missing_initial_ramp gate).
MAX_START_GAP = 0.055
# Path enumeration cap: a denser graph is not a set of curves.
MAX_PATHS = 48
# VGS(Qg) never falls: a path whose y (pt, down) rises by more than this along
# the path is not a gate curve.
FALL_TOLERANCE_PT = 0.8


@dataclass
class SourceCurve:
    """One separated curve: dense crop-px points plus its pt geometry."""

    points_px: list[tuple[int, int]]
    polyline_pt: list[tuple[float, float]]
    half_width_pt: float
    drawings: tuple[int, ...] = ()
    style: tuple | None = None  # (dashes, colour, width) when one style draws the whole curve


@dataclass
class Separation:
    method: str  # "vector_paths" | "vector_pen_strokes" | "raster_tracks"
    curves: list[SourceCurve] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    # pt geometry + style of every dark stroke segment that is NOT part of a
    # curve (leader lines, arrow shafts, legend samples), and filled arrowheads
    annotation_segments_pt: list[tuple[tuple[float, float], tuple[float, float], float, tuple]] = field(default_factory=list)
    arrowheads_pt: list[tuple[tuple[float, float], ...]] = field(default_factory=list)

    @property
    def separated(self) -> bool:
        return len(self.curves) >= 2 and not self.refused

    @property
    def refused(self) -> bool:
        return any(d.startswith("gc_separation_refused") for d in self.diagnostics)


def _bezier(points, steps: int = 8) -> list[tuple[float, float]]:
    p = [(float(pt.x), float(pt.y)) for pt in points]
    out = []
    for step in range(steps + 1):
        t = step / steps
        u = 1.0 - t
        out.append((
            u**3 * p[0][0] + 3 * u * u * t * p[1][0] + 3 * u * t * t * p[2][0] + t**3 * p[3][0],
            u**3 * p[0][1] + 3 * u * u * t * p[1][1] + 3 * u * t * t * p[2][1] + t**3 * p[3][1],
        ))
    return out


def _dark(color) -> bool:
    return color is not None and max(color) <= 0.45


def _ink(color) -> bool:
    """A curve colour: dark, or saturated (a coloured curve). Grey grid and
    frame rules (equal channels, lighter than 0.45) are not curve ink."""
    if color is None or len(color) < 3:
        return _dark(color)
    return _dark(color) or (max(color) - min(color) >= 0.25 and min(color) <= 0.6)


def _style(drawing) -> tuple:
    dashes = " ".join(str(drawing.get("dashes") or "[] 0").split())
    if dashes.startswith("[]"):
        dashes = "solid"
    color = tuple(round(float(c), 2) for c in (drawing.get("color") or ()))
    return dashes, color, round(float(drawing.get("width") or 0.0), 2)


def _clip(a, b, rect):
    """Segment ab clipped to rect (Liang-Barsky), or None when wholly outside.
    A curve whose last segment runs a fraction of a point past the plot box
    keeps the part inside (FDMS86520L's 75 V rise ends 0.2 pt past it)."""

    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - rect.x0), (dx, rect.x1 - a[0]), (-dy, a[1] - rect.y0), (dy, rect.y1 - a[1])):
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
    if t0 == 0.0 and t1 == 1.0:
        return a, b
    pa = (a[0] + t0 * dx, a[1] + t0 * dy)
    pb = (a[0] + t1 * dx, a[1] + t1 * dy)
    return None if math.dist(pa, pb) < 0.05 else (pa, pb)


def _filled_arrow(items, fill, plot_pdf):
    """(tail/tip ends) of a small elongated dark-filled polygon -- an arrow drawn
    as one filled shape, shaft and head (Panjit PSMB050N10NS2 "50 V") -- or None."""

    if not _dark(fill) or not 4 <= len(items) <= 12 or any(item[0] != "l" for item in items):
        return None
    verts = [(float(pt.x), float(pt.y)) for item in items for pt in item[1:3]]
    a, b = max(((p, q) for p in verts for q in verts), key=lambda pq: math.dist(*pq))
    length = math.dist(a, b)
    if not 4.0 <= length <= 0.25 * math.hypot(plot_pdf.width, plot_pdf.height):
        return None
    dx, dy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    thickness = max(abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) for p in verts)
    return (a, b) if thickness <= length / 3.0 else None


def _collect_strokes(page, plot_pdf, plot_pad):
    """Dark stroke pieces in content order, as (a, b, width, drawing, style, pen_break).

    ``pen_break`` is True when a piece does not start where the previous piece
    of the same style ended (a new pen stroke)."""

    plot_w = max(1.0, plot_pdf.width)
    plot_h = max(1.0, plot_pdf.height)
    pieces = []
    arrowheads = []
    last_end, last_style = None, None
    for index, drawing in enumerate(page.get_drawings()):
        items = drawing.get("items", [])
        fill = drawing.get("fill")
        # small closed dark polygons (filled or outlined, 3-4 corners) are arrowheads
        if (_dark(fill) or _dark(drawing.get("color"))) and 2 <= len(items) <= 5 and all(item[0] == "l" for item in items):
            verts = []
            for item in items:
                for pt in item[1:3]:
                    v = (float(pt.x), float(pt.y))
                    if not any(math.hypot(v[0] - w[0], v[1] - w[1]) < 0.05 for w in verts):
                        verts.append(v)
            xs = [v[0] for v in verts]
            ys = [v[1] for v in verts]
            closed = _dark(fill) or math.hypot(
                float(items[0][1].x) - float(items[-1][2].x), float(items[0][1].y) - float(items[-1][2].y)
            ) < 0.1
            if closed and 3 <= len(verts) <= 4 and max(xs) - min(xs) <= 8 and max(ys) - min(ys) <= 8:
                if plot_pad.contains(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)):
                    arrowheads.append(tuple(verts))
                    last_end = None
                    continue
        color = drawing.get("color")
        arrow = _filled_arrow(items, fill, plot_pdf) if not _ink(color) else None
        if arrow is not None and plot_pad.contains(arrow[0]) and plot_pad.contains(arrow[1]):
            # annotation only: never a curve piece (width 0 keeps it below the
            # curve-width cut), pen break so it never joins a curve stroke
            pieces.append((arrow[0], arrow[1], 0.0, index, ("filled_arrow",), True))
            last_end = None
            continue
        if not _ink(color):
            continue
        if _dark(fill) and len(items) > 5:
            # a dark-filled outline is the boundary of a thick stroke, not a
            # centreline (FDP16AN08A0 Figure 14): two edges of one curve
            last_end = None
            continue
        width = float(drawing.get("width") or 0.0)
        style = _style(drawing)
        for item in items:
            if item[0] == "l":
                pts = [(float(item[1].x), float(item[1].y)), (float(item[2].x), float(item[2].y))]
            elif item[0] == "c":
                pts = _bezier(item[1:5])
            else:
                last_end = None
                continue
            continuing = (
                last_end is not None and style == last_style
                and math.hypot(pts[0][0] - last_end[0], pts[0][1] - last_end[1]) <= 0.1
            )
            last_end, last_style = pts[-1], style
            for a, b in zip(pts, pts[1:]):
                dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
                if math.hypot(dx, dy) < 0.05:
                    continue
                if (dy < 0.8 and dx > 0.55 * plot_w) or (dx < 0.8 and dy > 0.55 * plot_h):
                    continuing = False
                    continue
                clipped = _clip(a, b, plot_pad)
                if clipped is None:
                    continuing = False
                    continue
                inside = clipped == (a, b)
                pieces.append((*clipped, width, index, style, not continuing))
                # a stroke leaving the pad ends its pen stroke there
                continuing = inside
    return pieces, arrowheads


def _point_segment(p, a, b) -> tuple[float, float]:
    """(distance, t) from point p to segment ab."""

    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 <= 1e-12:
        return math.hypot(p[0] - ax, p[1] - ay), 0.0
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy)), t


def points_to_polyline(points, polyline) -> np.ndarray:
    """Distance of every point to a polyline (vectorised, chunked)."""

    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    poly = np.asarray(polyline, dtype=float).reshape(-1, 2)
    if len(poly) < 2 or not len(pts):
        return np.full(len(pts), np.inf)
    a, d = poly[:-1], poly[1:] - poly[:-1]
    length2 = np.maximum((d ** 2).sum(axis=1), 1e-12)
    out = np.empty(len(pts))
    step = max(1, 2_000_000 // max(1, len(a)))
    for i in range(0, len(pts), step):
        chunk = pts[i : i + step]
        rel = chunk[:, None, :] - a[None]
        t = np.clip((rel * d[None]).sum(axis=2) / length2[None], 0.0, 1.0)
        near = a[None] + t[:, :, None] * d[None]
        out[i : i + step] = np.sqrt(((chunk[:, None, :] - near) ** 2).sum(axis=2)).min(axis=1)
    return out


def polyline_distance(p, polyline) -> float:
    return float(points_to_polyline([p], polyline)[0])


def _split_at_junctions(pieces):
    """Split every piece where another piece's endpoint lies on its interior."""

    ends = np.asarray([pt for piece in pieces for pt in piece[:2]], dtype=float).reshape(-1, 2)
    out = []
    for a, b, width, drawing, style, _brk in pieces:
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        lo = np.minimum(a, b) - NODE_TOLERANCE_PT
        hi = np.maximum(a, b) + NODE_TOLERANCE_PT
        near = ends[(ends[:, 0] >= lo[0]) & (ends[:, 0] <= hi[0]) & (ends[:, 1] >= lo[1]) & (ends[:, 1] <= hi[1])]
        cuts = []
        for p in near:
            distance, t = _point_segment(p, a, b)
            if distance <= NODE_TOLERANCE_PT and t * length > NODE_TOLERANCE_PT and (1 - t) * length > NODE_TOLERANCE_PT:
                cuts.append(t)
        points = [a]
        for t in sorted(set(round(t, 6) for t in cuts)):
            points.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
        points.append(b)
        out.extend((p, q, width, drawing, style) for p, q in zip(points, points[1:]))
    return out


class _Nodes:
    """Endpoint snapping within NODE_TOLERANCE_PT, grid-hashed."""

    def __init__(self) -> None:
        self.points: list[tuple[float, float]] = []
        self._grid: dict[tuple[int, int], list[int]] = {}

    def id(self, p) -> int:
        cx, cy = int(math.floor(p[0] / NODE_TOLERANCE_PT)), int(math.floor(p[1] / NODE_TOLERANCE_PT))
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for index in self._grid.get((gx, gy), ()):
                    q = self.points[index]
                    if math.hypot(p[0] - q[0], p[1] - q[1]) <= NODE_TOLERANCE_PT:
                        return index
        self.points.append(p)
        self._grid.setdefault((cx, cy), []).append(len(self.points) - 1)
        return len(self.points) - 1


def _order_key(p) -> tuple[float, float]:
    return (round(p[0], 2), -round(p[1], 2))


def _has_cycle(edges: dict[int, set[int]], nodes: int) -> bool:
    """Kahn's algorithm: True if some node never reaches indegree 0."""

    indegree = [0] * nodes
    for targets in edges.values():
        for nb in targets:
            indegree[nb] += 1
    ready = [n for n in range(nodes) if indegree[n] == 0]
    seen = 0
    while ready:
        node = ready.pop()
        seen += 1
        for nb in edges.get(node, ()):
            indegree[nb] -= 1
            if indegree[nb] == 0:
                ready.append(nb)
    return seen < nodes


def _paths(edges: dict[int, set[int]], indegree: dict[int, int], nodes: int):
    """Every source -> sink path (iterative DFS: dense polylines are deep).

    Stack entries carry a parent pointer, never a copy of the path so far: on a
    deep graph that branches all along its length (a curve drawn twice, split at
    every junction) copies left ~depth entries of ~depth nodes each on the stack.
    Memory is now linear in the nodes visited, which the MAX_PATHS cap bounds at
    about (MAX_PATHS + 1) * nodes -- for a DAG only. On a cycle no sink is ever
    reached and the walk never ends (8 workers at ~20 GB apiece panicked the host,
    2026-10-04), so a cyclic graph is a caller bug and raises.
    """

    if _has_cycle(edges, nodes):
        raise ValueError("_paths needs a DAG; the edge orientation produced a cycle")

    sources = [n for n in range(nodes) if indegree.get(n, 0) == 0 and edges.get(n)]
    found: list[list[int]] = []
    # visited[k] = (node, index of its predecessor in visited, or -1)
    visited: list[tuple[int, int]] = []
    for source in sources:
        stack = [(source, -1)]
        while stack:
            if len(found) > MAX_PATHS:
                return found
            node, parent = stack.pop()
            visited.append((node, parent))
            here = len(visited) - 1
            nexts = edges.get(node)
            if not nexts:
                path = []
                while here >= 0:
                    path.append(visited[here][0])
                    here = visited[here][1]
                found.append(path[::-1])
                continue
            for nxt in sorted(nexts, reverse=True):
                stack.append((nxt, here))
    return found


def _densify(polyline_pt, rect, scale) -> list[tuple[int, int]]:
    """One crop-px point per column (median), as the legacy vector tracer does."""

    by_x: dict[int, list[int]] = {}
    for (ax, ay), (bx, by) in zip(polyline_pt, polyline_pt[1:]):
        x0, y0 = (ax - rect.x0) * scale, (ay - rect.y0) * scale
        x1, y1 = (bx - rect.x0) * scale, (by - rect.y0) * scale
        steps = max(1, int(math.ceil(max(abs(x1 - x0), abs(y1 - y0)))))
        for index in range(steps + 1):
            f = index / steps
            by_x.setdefault(int(round(x0 + f * (x1 - x0))), []).append(int(round(y0 + f * (y1 - y0))))
    return [(x, int(round(float(np.median(ys))))) for x, ys in sorted(by_x.items())]


def simplify(poly, tolerance: float = 0.05) -> list[tuple[float, float]]:
    """Douglas-Peucker (iterative): drops vertices within *tolerance* of the line.
    Geometry checks run on this; dense 0.1 pt polylines become a few vertices."""

    if len(poly) < 3:
        return list(poly)
    pts = np.asarray(poly, dtype=float)
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        d = b - a
        length = float(np.hypot(*d))
        seg = pts[i + 1 : j]
        if length < 1e-12:
            dist = np.hypot(seg[:, 0] - a[0], seg[:, 1] - a[1])
        else:
            dist = np.abs(d[0] * (seg[:, 1] - a[1]) - d[1] * (seg[:, 0] - a[0])) / length
        k = int(np.argmax(dist))
        if dist[k] > tolerance:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return [tuple(p) for p in pts[keep]]


def _resample(poly, step: float = 1.0) -> list[tuple[float, float]]:
    out = [poly[0]]
    for a, b in zip(poly, poly[1:]):
        n = max(1, int(math.ceil(math.dist(a, b) / step)))
        out.extend((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1))
    return out


def _hausdorff(a, b) -> float:
    """Symmetric Hausdorff distance of two pt polylines (sampled every 1 pt)."""
    return float(max(points_to_polyline(_resample(a), b).max(), points_to_polyline(_resample(b), a).max()))


def _dedupe(polys) -> list:
    """Drop polylines that repeat another within 0.3 pt everywhere (a curve
    drawn twice, as BUK753R8-80E Figure 13's 64 V stroke is)."""
    kept = []
    for poly in polys:
        if not any(_hausdorff(poly, other) < 0.3 for other in kept):
            kept.append(poly)
    return kept


def _is_gate_curve(poly, plot_pdf) -> bool:
    """Spans the plot, starts at its left edge, reaches its upper half, and
    VGS never falls (y pt grows down)."""

    plot_w = max(1.0, plot_pdf.width)
    plot_h = max(1.0, plot_pdf.height)
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    if max(xs) - min(xs) < MIN_X_SPAN * plot_w or max(ys) - min(ys) < MIN_Y_SPAN * plot_h:
        return False
    if poly[0][0] - plot_pdf.x0 > MAX_START_GAP * plot_w:
        return False
    # a VGS(Qg) curve rises well past its plateau into the upper half; a path
    # that stops lower is a fragment of one (FDMS86520L), not a VDD curve
    if min(ys) > plot_pdf.y0 + 0.5 * plot_h:
        return False
    lowest = poly[0][1]
    for _x, y in poly[1:]:
        if y - lowest > FALL_TOLERANCE_PT:
            return False
        lowest = min(lowest, y)
    return True


def _drop_spurs(curve_pieces, plot_pdf):
    """Split off curve-weight leader shafts: a lone short stroke from a point
    inside another stroke to a free end below the plot top (FDS8447's 0.86 pt
    "20 V" shaft beside 1.25 pt curves). Returns (curves, spurs)."""

    diag = math.hypot(plot_pdf.width, plot_pdf.height)
    ends = np.asarray([p for piece in curve_pieces for p in piece[:2]], dtype=float).reshape(-1, 2)

    def degree(p):
        return int((np.hypot(ends[:, 0] - p[0], ends[:, 1] - p[1]) <= NODE_TOLERANCE_PT).sum())

    def on_interior(p, own):
        for k, piece in enumerate(curve_pieces):
            if k == own:
                continue
            distance, t = _point_segment(p, piece[0], piece[1])
            length = math.dist(piece[0], piece[1])
            if distance <= NODE_TOLERANCE_PT and NODE_TOLERANCE_PT < t * length < length - NODE_TOLERANCE_PT:
                return True
        return False

    keep, spurs = [], []
    for k, piece in enumerate(curve_pieces):
        a, b = piece[0], piece[1]
        standalone = piece[5] and (k + 1 == len(curve_pieces) or curve_pieces[k + 1][5])
        spur = False
        if standalone and math.dist(a, b) < 0.25 * diag:
            for free, touch in ((a, b), (b, a)):
                if degree(free) == 1 and on_interior(touch, k) and free[1] > plot_pdf.y0 + 0.10 * plot_pdf.height:
                    spur = True
        (spurs if spur else keep).append(piece)
    return keep, spurs


def _graph_curves(curve_pieces, plot_pdf):
    """(list of pt polylines, refusal or None) from source->sink paths."""

    split = _split_at_junctions(curve_pieces)
    nodes = _Nodes()
    edges: dict[int, set[int]] = {}
    for a, b, *_rest in split:
        na, nb = nodes.id(a), nodes.id(b)
        if na == nb:
            continue
        # Orient by the MERGED nodes, not the raw endpoints: two pieces whose
        # ends merge into the same pair of nodes can disagree on raw order, and
        # the resulting 2-cycle sent _paths into an endless walk (2026-10-04,
        # synth gc7 page_0006). (key, id) is a strict total order, so the graph
        # is a DAG by construction.
        if (_order_key(nodes.points[nb]), nb) < (_order_key(nodes.points[na]), na):
            na, nb = nb, na
        edges.setdefault(na, set()).add(nb)
    indegree: dict[int, int] = {}
    for targets in edges.values():
        for nb in targets:
            indegree[nb] = indegree.get(nb, 0) + 1
    paths = _paths(edges, indegree, len(nodes.points))
    if len(paths) > MAX_PATHS:
        return [], "vector_graph_too_dense"
    candidates = [p for p in paths if _is_gate_curve([nodes.points[n] for n in p], plot_pdf)]
    # Two curves that diverge and meet again: identity at the meeting node is
    # not recoverable from the geometry alone.
    incoming: dict[int, set[int]] = {}
    for path in candidates:
        for i in range(len(path) - 1):
            incoming.setdefault(path[i + 1], set()).add(path[i])
    if any(len(sources) >= 2 for sources in incoming.values()):
        return [], "curves_rejoin"
    return [[nodes.points[n] for n in p] for p in candidates], None


def _pen_curves(curve_pieces, plot_pdf):
    """Pen strokes (pieces drawn end to start, one style) that are gate curves.

    A stroke that turns back (NXP redraws the last segment backwards) is cut
    where its x first decreases."""

    strokes: list[tuple[list[tuple[float, float]], tuple]] = []
    for a, b, _w, _d, style, pen_break in curve_pieces:
        if pen_break or not strokes:
            strokes.append(([a, b], style))
        else:
            strokes[-1][0].append(b)
    out = []
    for poly, style in strokes:
        cut = [poly[0]]
        for p in poly[1:]:
            if p[0] < cut[-1][0] - 0.5:
                break
            cut.append(p)
        if _is_gate_curve(cut, plot_pdf):
            out.append((cut, style))
    return out


def _curve_style(index, polys, curve_pieces, half_width):
    """The stroke style drawing >= 90 % of curve *index*'s own (unshared) length, or None."""

    if not curve_pieces:
        return None
    mids = np.asarray([((p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2) for p in curve_pieces])
    lengths = np.asarray([math.dist(p[0], p[1]) for p in curve_pieces])
    on = points_to_polyline(mids, polys[index]) <= 0.3
    for k, other in enumerate(polys):
        if k != index:
            on &= points_to_polyline(mids, other) > 2 * half_width + 0.5
    totals: dict[tuple, float] = {}
    for piece, length, keep in zip(curve_pieces, lengths, on):
        if keep:
            totals[piece[4]] = totals.get(piece[4], 0.0) + float(length)
    total = sum(totals.values())
    if total <= 0:
        return None
    style, length = max(totals.items(), key=lambda item: item[1])
    return style if length >= 0.9 * total else None


def separate_vector_curves(page, rect, scale: float, plot_box) -> Separation:
    """Every physical VGS(Qg) source curve drawn as vector strokes in the plot.

    Two readings of the vector layer: the endpoint graph's source->sink paths
    (shared segments drawn once, curves branching off them) and the pen
    strokes in content order (each curve drawn as one continuous stroke,
    possibly re-drawing the shared part). The graph is used when it is
    consistent (no rejoin) and finds at least as many curves; otherwise the pen
    strokes, when they hold two or more complete curves (IPB180N04S4: the 32 V
    rise starts on the 8 V plateau, so the graph rejoins, but each curve is one
    pen stroke). Neither -> refused, never guessed.
    """

    import pymupdf

    x0, y0, x1, y1 = plot_box
    plot_pdf = pymupdf.Rect(rect.x0 + x0 / scale, rect.y0 + y0 / scale, rect.x0 + x1 / scale, rect.y0 + y1 / scale)
    plot_w = max(1.0, plot_pdf.width)
    plot_h = max(1.0, plot_pdf.height)
    plot_pad = pymupdf.Rect(plot_pdf.x0 - 2.0, plot_pdf.y0 - 0.35 * plot_h, plot_pdf.x1 + 2.0, plot_pdf.y1 + 2.0)
    pieces, arrowheads = _collect_strokes(page, plot_pdf, plot_pad)
    separation = Separation("vector_paths", arrowheads_pt=arrowheads)
    if not pieces:
        separation.diagnostics.append("gc_separation_no_vector_strokes")
        return separation
    # the curve stroke width: the widest stroke among drawings that span the plot
    spans: dict[int, list[tuple[float, float]]] = {}
    for piece in pieces:
        spans.setdefault(piece[3], []).extend(piece[:2])
    spanning = {
        d for d, pts in spans.items()
        if np.ptp([p[0] for p in pts]) >= 0.25 * plot_w or np.ptp([p[1] for p in pts]) >= 0.25 * plot_h
    }
    spanning_widths = [piece[2] for piece in pieces if piece[3] in spanning]
    if not spanning_widths:
        separation.diagnostics.append("gc_separation_no_vector_strokes")
        return separation
    curve_width = max(spanning_widths)
    curve_pieces = [p for p in pieces if p[2] >= CURVE_WIDTH_FRACTION * curve_width]
    thin = [p for p in pieces if p[2] < CURVE_WIDTH_FRACTION * curve_width]
    curve_pieces, spurs = _drop_spurs(curve_pieces, plot_pdf)
    thin += spurs
    separation.annotation_segments_pt = [(p[0], p[1], p[2], p[4]) for p in pieces if math.dist(p[0], p[1]) >= 1.5]

    graph, refusal = _graph_curves(curve_pieces, plot_pdf)
    graph = _dedupe(graph)
    pens = []
    for poly, style in _pen_curves(curve_pieces, plot_pdf):
        if not any(_hausdorff(poly, other) < 0.3 for other, _s in pens):
            pens.append((poly, style))
    if refusal is None and graph and len(graph) >= len(pens):
        polys = graph
    elif len(pens) >= 2:
        polys = [poly for poly, _style in pens]
        separation.method = "vector_pen_strokes"
        if refusal:
            separation.diagnostics.append(f"gc_graph_{refusal}_resolved_by_pen_strokes")
    elif refusal is not None:
        separation.diagnostics.append(f"gc_separation_refused:{refusal}")
        return separation
    else:
        polys = graph or [poly for poly, _style in pens]
    if not polys:
        separation.diagnostics.append("gc_separation_no_vector_gate_curve")
        return separation

    half_width = 0.5 * curve_width
    polys = [simplify(poly) for poly in polys]
    curves = [
        SourceCurve(_densify(poly, rect, scale), poly, half_width, style=_curve_style(i, polys, curve_pieces, half_width))
        for i, poly in enumerate(polys)
    ]
    # Paths that never separate by more than a stroke width (+0.4 pt) cannot be
    # told apart on the page. (Outlined thick strokes are excluded above;
    # Panjit PSMB050N10NS2's 50 V and 80 V strokes are 1.6 pt apart, 0.75 pt
    # wide, with a visible gap: distinct.)
    threshold = curve_width + 0.4
    for i in range(len(curves)):
        for j in range(i + 1, len(curves)):
            if _hausdorff(curves[i].polyline_pt, curves[j].polyline_pt) < threshold:
                separation.diagnostics.append("gc_separation_refused:coincident_paths")
                return separation
    separation.curves = sorted(curves, key=lambda c: _x_at_common_level(curves, c))
    # Leader lines, arrow shafts and legend samples: strokes that are not part
    # of any separated curve.
    # (dots of a dotted grid are not leaders: keep strokes >= 1.5 pt)
    on_curve = np.zeros(len(curve_pieces), dtype=bool)
    if curve_pieces:
        starts = np.asarray([p[0] for p in curve_pieces], dtype=float)
        ends = np.asarray([p[1] for p in curve_pieces], dtype=float)
        for c in curves:
            on_curve |= (points_to_polyline(starts, c.polyline_pt) <= 0.3) & (points_to_polyline(ends, c.polyline_pt) <= 0.3)
    separation.annotation_segments_pt = [(p[0], p[1], p[2], p[4]) for p in thin if math.dist(p[0], p[1]) >= 1.5] + [
        (p[0], p[1], p[2], p[4]) for p, used in zip(curve_pieces, on_curve) if not used
    ]
    return separation


def _x_at_level(points, level: float) -> float | None:
    """First x (crop px) where a rising curve reaches pixel row *level*."""

    for (xa, ya), (xb, yb) in zip(points, points[1:]):
        if ya >= level >= yb and ya != yb:
            return xa + (ya - level) / (ya - yb) * (xb - xa)
        if ya == level:
            return float(xa)
    return None


def common_level(curves: list[SourceCurve]) -> float | None:
    """A pixel row above every curve's plateau that every curve still reaches."""

    tops = [min(y for _x, y in c.points_px) for c in curves if c.points_px]
    plateaus = [plateau_run(c.points_px) for c in curves]
    if len(tops) != len(curves) or any(p is None for p in plateaus):
        return None
    plateau_y = min(p[2] for p in plateaus)
    top = max(tops)
    if plateau_y - top < 6:
        return None
    return top + 0.5 * (plateau_y - top)


def _x_at_common_level(curves, curve) -> float:
    level = common_level(curves)
    if level is not None:
        x = _x_at_level(curve.points_px, level)
        if x is not None:
            return x
    run = plateau_run(curve.points_px)
    return float(run[1]) if run else float(curve.points_px[-1][0])


def x_at_common_level(curves, index: int) -> float | None:
    level = common_level(curves)
    return None if level is None else _x_at_level(curves[index].points_px, level)


def plateau_run(points, *, tol_px: float = 1.0) -> tuple[int, int, float] | None:
    """(x_start, x_end, y) of the first flat run with a rise before and after.

    The run must be at least 4 px long; the curve must rise >= 4 px before it
    and >= 4 px after it. None when the curve has no such run.
    """

    if len(points) < 8:
        return None
    ys = [p[1] for p in points]
    span = max(ys) - min(ys)
    rise = max(4.0, 0.05 * span)
    i = 0
    n = len(points)
    while i < n:
        j = i
        while j + 1 < n and abs(points[j + 1][1] - points[i][1]) <= tol_px:
            j += 1
        length = points[j][0] - points[i][0]
        if length >= 4:
            before = max(ys[: i + 1]) - points[i][1]
            after = points[i][1] - min(ys[j:])
            if before >= rise and after >= rise:
                y = float(np.median(ys[i : j + 1]))
                return points[i][0], points[j][0], y
        i = j + 1
    return None


# ----------------------------------------------------------------------------- raster
def _row_runs(row: np.ndarray, min_len: int = 1, gap: int = 1) -> list[float]:
    xs = np.where(row > 0)[0]
    if len(xs) == 0:
        return []
    groups = [[int(xs[0])]]
    for x in xs[1:]:
        if int(x) - groups[-1][-1] <= gap + 1:
            groups[-1].append(int(x))
        else:
            groups.append([int(x)])
    return [float(np.mean(g)) for g in groups if len(g) >= min_len]


def separate_raster_curves(mask: np.ndarray, plot_box, legacy_curve, plateau_y: float | None) -> Separation:
    """Track K >= 2 rising strokes above a shared plateau in an ink mask (crop px)."""

    separation = Separation("raster_tracks")
    if plateau_y is None or len(legacy_curve) < 20:
        separation.diagnostics.append("gc_separation_no_raster_plateau")
        return separation
    x0, y0, x1, y1 = plot_box
    height = max(1, y1 - y0)
    width = max(1, x1 - x0)
    margin = max(4, int(round(0.03 * height)))
    band_bottom = int(plateau_y) - margin
    band_top = y0 + max(3, int(round(0.01 * height)))
    if band_bottom - band_top < 12:
        separation.diagnostics.append("gc_separation_no_raster_band")
        return separation
    plateau_points = [x for x, y in legacy_curve if abs(y - plateau_y) <= 2]
    if not plateau_points:
        separation.diagnostics.append("gc_separation_no_raster_plateau")
        return separation
    plateau_start = min(plateau_points)
    rows = {}
    for r in range(band_top, band_bottom + 1):
        runs = [x for x in _row_runs(mask[r, x0 : x1 + 1]) if x0 + x > plateau_start]
        rows[r] = [x0 + x for x in runs]
    counts = [len(v) for v in rows.values() if v]
    if not counts:
        separation.diagnostics.append("gc_separation_no_raster_runs")
        return separation
    values, freq = np.unique(counts, return_counts=True)
    k = int(values[np.argmax(freq)])
    share = float(freq.max()) / len(rows)
    if k < 2:
        separation.diagnostics.append("gc_separation_single_raster_track")
        return separation
    if share < 0.5:
        separation.diagnostics.append("gc_separation_refused:raster_track_count_unstable")
        return separation
    tracks: list[list[tuple[float, int]]] = [[] for _ in range(k)]
    for r in sorted(rows):
        if len(rows[r]) == k:
            for index, x in enumerate(sorted(rows[r])):
                tracks[index].append((x, r))
    # each track: one rising stroke (x grows as the row index falls), continuous
    for track in tracks:
        ordered = sorted(track, key=lambda item: -item[1])  # bottom to top
        xs = np.array([x for x, _r in ordered])
        rs = np.array([r for _x, r in ordered])
        if len(xs) < max(8, 0.3 * (band_bottom - band_top)):
            separation.diagnostics.append("gc_separation_refused:raster_track_too_short")
            return separation
        slope, intercept = np.polyfit(rs, xs, 1)
        residual = np.abs(xs - (slope * rs + intercept))
        if np.diff(xs).min() < -2.5 or np.percentile(residual, 90) > max(3.0, 0.02 * width):
            separation.diagnostics.append("gc_separation_refused:raster_track_not_a_stroke")
            return separation
    # The rise and plateau shared below the band must really be one stroke.
    below = [len(_row_runs(mask[r, x0 : int(plateau_start) + 1])) for r in range(int(plateau_y) + margin, y1 - margin)]
    if below and sum(1 for c in below if c == 1) < 0.8 * len(below):
        separation.diagnostics.append("gc_separation_refused:raster_rise_not_shared")
        return separation
    shared = [(x, y) for x, y in legacy_curve if x <= plateau_start]
    curves = []
    for track in tracks:
        ordered = sorted(track, key=lambda item: -item[1])
        rs = np.array([r for _x, r in ordered], dtype=float)
        xs = np.array([x for x, _r in ordered], dtype=float)
        low = rs[: max(4, len(rs) // 5)]
        lx = xs[: max(4, len(xs) // 5)]
        slope, intercept = np.polyfit(low, lx, 1)
        fork_x = float(slope * plateau_y + intercept)
        if fork_x < plateau_start or fork_x > x1:
            separation.diagnostics.append("gc_separation_refused:raster_fork_off_plateau")
            return separation
        # ink along the plateau up to this track's fork
        cols = range(int(plateau_start), int(round(fork_x)) + 1)
        band = mask[max(0, int(plateau_y) - 2) : int(plateau_y) + 3, :]
        inked = sum(1 for c in cols if band[:, c].any()) if len(cols) else 0
        if len(cols) and inked < 0.85 * len(cols):
            separation.diagnostics.append("gc_separation_refused:raster_plateau_not_inked")
            return separation
        pts = list(shared)
        pts += [(x, int(round(plateau_y))) for x in range(int(plateau_start) + 1, int(round(fork_x)) + 1)]
        by_x: dict[int, list[int]] = {}
        for x, r in ordered:
            by_x.setdefault(int(round(x)), []).append(r)
        pts += [(x, int(round(float(np.mean(v))))) for x, v in sorted(by_x.items()) if x > fork_x]
        curves.append(SourceCurve(pts, [], 0.0))
    for i in range(len(curves)):
        for j in range(i + 1, len(curves)):
            a = curves[i].points_px[-1]
            b = curves[j].points_px[-1]
            if math.hypot(a[0] - b[0], a[1] - b[1]) < 3:
                separation.diagnostics.append("gc_separation_refused:coincident_tracks")
                return separation
    separation.curves = curves
    return separation
