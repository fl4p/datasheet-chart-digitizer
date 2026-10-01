"""Coverage guard: does a served vector curve stop where its source curve stops?

A served trace can be accurate where it exists and still be silently short.
TMB160N08A Figure 6 (body diode, log I_S axis 1e-4..1e2 A) was served ``ok``
up to 10 A because the plot box stopped at the 10^1 gridline: both source
curves continue, visibly, up to the 10^2 frame, so the top decade was missing.

For every served curve end that lies on a plot-box edge, this follows the
source PDF stroke through that end. If the same stroke continues outward past
the edge, the continuation is inside the panel crop, and the rendered crop
shows ink along it, the served curve is truncated. A stroke the vendor draws
past the frame under a clip path is invisible in the render and is not counted.

Tri-state verdict per panel: ``verified`` (every edge end was checked and
none continues), ``truncated`` (with reasons), ``unverified`` (an edge end
whose source stroke could not be found, so coverage was not checked).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .capacitance_types import PlotBox
from .crop_transform import CropTransform

# A served end within this many pixels of a plot edge is an "edge end".  The
# vector extractors clip strokes to the plot rect grown by 1.5 pt, so the
# tolerance also covers that margin at the crop's scale.
EDGE_TOLERANCE_PX = 2.5
EXTRACTOR_CLIP_MARGIN_PT = 1.5
# Source segment must pass within this distance of the served end (pt),
# on top of half its stroke width.
SEGMENT_MATCH_PT = 1.25
# Segments of one stroke chain when their endpoints meet within this (pt).
CHAIN_JOIN_PT = 0.6
# A continuation counts when it reaches this far beyond the edge: at least
# MIN_EXTENT_PX, and at least this fraction of the plot side it crosses.
MIN_EXTENT_PX = 10.0
MIN_EXTENT_FRACTION = 0.04
# Fraction of sampled continuation points that must show rendered ink.
MIN_INK_FRACTION = 0.6
# The matched stroke must reach this fraction of the crossed side inside the plot.
MIN_INWARD_FRACTION = 0.10
INK_GRAY = 200


@dataclass
class CoverageVerdict:
    status: str  # verified | truncated | unverified
    reasons: list[str] = field(default_factory=list)
    checked_ends: int = 0
    # continuations that stay short of every printed tick beyond the edge:
    # the class does not extrapolate past its last label, so these are
    # reported, not counted as truncation
    notes: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "notes": list(self.notes),
            "checked_ends": self.checked_ends,
        }


def _flatten(drawing: dict) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for item in drawing.get("items", []):
        if item[0] == "l":
            segments.append(((item[1].x, item[1].y), (item[2].x, item[2].y)))
        elif item[0] == "c":
            p = [(pt.x, pt.y) for pt in item[1:5]]
            previous = p[0]
            for step in range(1, 9):
                t = step / 8.0
                u = 1.0 - t
                point = (
                    u**3 * p[0][0] + 3 * u * u * t * p[1][0] + 3 * u * t * t * p[2][0] + t**3 * p[3][0],
                    u**3 * p[0][1] + 3 * u * u * t * p[1][1] + 3 * u * t * t * p[2][1] + t**3 * p[3][1],
                )
                segments.append((previous, point))
                previous = point
    return segments


def _point_segment_distance(point, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = point
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 <= 1e-12 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _chain(segments, seeds: list[int]) -> list[int]:
    def key(point):
        return (round(point[0] / CHAIN_JOIN_PT), round(point[1] / CHAIN_JOIN_PT))

    by_key: dict[tuple[int, int], list[int]] = {}
    for index, (a, b) in enumerate(segments):
        for point in (a, b):
            kx, ky = key(point)
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    by_key.setdefault((kx + ox, ky + oy), []).append(index)
    seen = set(seeds)
    stack = list(seeds)
    while stack:
        index = stack.pop()
        for point in segments[index]:
            for neighbour in by_key.get(key(point), []):
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
    return sorted(seen)


def _edges_of(point: tuple[float, float], plot: PlotBox, tol_x: float, tol_y: float) -> list[str]:
    x, y = point
    edges = []
    if abs(x - plot.x0) <= tol_x:
        edges.append("left")
    if abs(x - plot.x1) <= tol_x:
        edges.append("right")
    if abs(y - plot.y0) <= tol_y:
        edges.append("top")
    if abs(y - plot.y1) <= tol_y:
        edges.append("bottom")
    return edges


def _outward(edge: str, point_px: tuple[float, float], plot: PlotBox) -> float:
    x, y = point_px
    return {
        "left": plot.x0 - x,
        "right": x - plot.x1,
        "top": plot.y0 - y,
        "bottom": y - plot.y1,
    }[edge]


def served_curve_coverage(
    page,
    transform: CropTransform,
    gray: np.ndarray,
    plot: PlotBox,
    curves_px: list[list[tuple[float, float]]],
    label_beyond: Callable[[str, float], bool] | None = None,
) -> CoverageVerdict:
    """Check that no served curve stops at a plot edge its source stroke crosses.

    *label_beyond(edge, extent_px)* says whether a printed tick label of the
    axis lies beyond *edge* within the continuation's reach. A continuation
    that passes no printed tick (the unlabelled end interval of a linear
    axis, which the digitizers deliberately do not extrapolate into) is a
    note, not a truncation. Without the callback every continuation counts.
    """

    height, width = gray.shape[:2]
    strokes = []
    for drawing in page.get_drawings():
        if drawing.get("type") not in ("s", "fs"):
            continue
        segments = _flatten(drawing)
        if segments:
            strokes.append((segments, 0.5 * float(drawing.get("width") or 0.0)))
    verdict = CoverageVerdict("verified")
    tol_x = EDGE_TOLERANCE_PX + EXTRACTOR_CLIP_MARGIN_PT * transform.scale_x
    tol_y = EDGE_TOLERANCE_PX + EXTRACTOR_CLIP_MARGIN_PT * transform.scale_y
    for curve_index, curve in enumerate(curves_px):
        if len(curve) < 2:
            continue
        for end in (curve[0], curve[-1]):
            edges = _edges_of(end, plot, tol_x, tol_y)
            if not edges:
                continue
            verdict.checked_ends += 1
            end_pt = transform.to_pt(*end)
            matched = False
            for segments, half_width in strokes:
                seeds = [
                    index
                    for index, (a, b) in enumerate(segments)
                    if _point_segment_distance(end_pt, a, b) <= SEGMENT_MATCH_PT + half_width
                ]
                if not seeds:
                    continue
                matched = True
                chain = _chain(segments, seeds)
                for edge in edges:
                    side = plot.height if edge in ("top", "bottom") else plot.width
                    needed = max(MIN_EXTENT_PX, MIN_EXTENT_FRACTION * side)
                    samples: list[tuple[float, float]] = []
                    inside: list[tuple[float, float]] = []
                    for index in chain:
                        a, b = segments[index]
                        for t in np.linspace(0.0, 1.0, 6):
                            pt = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
                            px = transform.to_px(*pt)
                            if not (0 <= px[0] < width and 0 <= px[1] < height):
                                continue
                            if _outward(edge, px, plot) > 1.5:
                                samples.append(px)
                            elif plot.x0 <= px[0] <= plot.x1 and plot.y0 <= px[1] <= plot.y1:
                                inside.append(px)
                    if not samples or not inside:
                        continue
                    # The stroke must be the served curve itself: it reaches
                    # well inside the plot from this edge (a tick mark or a
                    # gridline lying along the edge does not).
                    if max(-_outward(edge, px, plot) for px in inside) < MIN_INWARD_FRACTION * side:
                        continue
                    extent = max(_outward(edge, px, plot) for px in samples)
                    if extent < needed:
                        continue
                    inked = 0
                    for x, y in samples:
                        xi, yi = int(round(x)), int(round(y))
                        window = gray[max(0, yi - 1): yi + 2, max(0, xi - 1): xi + 2]
                        inked += int(window.size > 0 and int(window.min()) < INK_GRAY)
                    ink_fraction = inked / len(samples)
                    if ink_fraction < MIN_INK_FRACTION:
                        continue
                    if label_beyond is not None and not label_beyond(edge, extent):
                        verdict.notes.append(
                            f"source_curve_continues_past_last_label_{edge}: curve {curve_index} "
                            f"continues {extent:.0f}px past the {edge} edge without passing a "
                            "printed tick; not extrapolated"
                        )
                        continue
                    verdict.status = "truncated"
                    verdict.reasons.append(
                        f"served_curve_truncated_at_plot_{edge}: curve {curve_index} ends on the "
                        f"{edge} plot edge but its source stroke continues {extent:.0f}px "
                        f"beyond it (need {needed:.0f}px; ink {ink_fraction:.2f})"
                    )
            if not matched and verdict.status != "truncated":
                verdict.status = "unverified"
                verdict.reasons.append(
                    f"coverage_unverified: no source stroke found through curve {curve_index}'s "
                    f"end on the {'/'.join(edges)} plot edge"
                )
    if verdict.status == "unverified" and any(r.startswith("served_curve_truncated") for r in verdict.reasons):
        verdict.status = "truncated"
    return verdict
