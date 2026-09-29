"""Tick-evidenced calibration and vector-path retrace for review-grade transfer charts.

``transfer_review`` maps pixels to values through a plot box whose corners are
ASSUMED to be the axis minimum and maximum.  That assumption silently produced
wrong human-verified ground truth (transfer-review25, re-examined 2026-09-28):

* a frame that ends between printed ticks (EPC7018GSH: 345 A, not 350 A;
  CRSS052N08N: 5.4 V, not 5.5 V) is read as ending ON a tick;
* a box drawn a few pixels off the real frame (CRST065N08N: 8 px) shifts
  every value;
* printed labels that contradict the chart's own gridlines (EPC7018GSH's
  ``2.0``/``2.5`` sit on the frame edge and mid-gap, while ``3.0``..``5.0`` sit
  on a uniform grid) were consumed as if they were ticks.

This module calibrates from evidence instead: every printed label is seated on
the chart's own gridline, the axis is fitted through the SEATED gridline
centres with the shared :func:`numeric_axis.fit_axis_ticks` core, labels that
contradict the consensus are reported as conflicts (never silently dropped),
and the SERVED range stops where the evidence stops.  Vector charts are traced
from their own PDF stroke paths and bound to temperatures by the printed legend
colour, not by curve order.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from .capacitance_vector import _color_distance, _sample_cubic
from .numeric_axis import AxisTick, NumericAxis, fit_axis_ticks

# A label binds to its nearest gridline only when it sits within this fraction
# of the gridline pitch AND that gridline is clearly nearer than the next one.
_SEAT_MAX_OFFSET_FRACTION = 0.35
_SEAT_MIN_MARGIN_FRACTION = 0.2
# Printed labels are placed systematically (centred, or shifted by a constant
# font offset).  Offsets that scatter by more than this are not tick evidence.
_MAX_OFFSET_SPREAD_FRACTION = 0.12
_MAX_OFFSET_SPREAD_FLOOR_PX = 2.5
# Legend-colour binding (same decisiveness contract as the C(V) legend binder).
_LEGEND_MAX_COLOR_DISTANCE = 0.40
_LEGEND_MIN_COLOR_SEPARATION = 1.8
_LEGEND_MAX_SWATCH_GAP_PT = 12.0
_TEMPERATURE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:°\s*C|℃)")


@dataclass(frozen=True)
class SeatedTick:
    """One printed label bound to the gridline it names."""

    text: str
    value: float
    label_px: float
    grid_px: float

    @property
    def offset_px(self) -> float:
        return self.label_px - self.grid_px


@dataclass(frozen=True)
class TickConflict:
    """A printed label that is NOT consumed, and why.

    ``grid_px`` is the gridline the label was seated on, or for an unseated /
    ambiguous label the NEAREST gridline (so the report can show how far the
    served calibration puts the label's value from it).
    """

    text: str
    value: float
    label_px: float
    grid_px: float | None
    reason: str


@dataclass(frozen=True)
class ReviewAxis:
    """A linear axis fitted through seated gridline centres, with its served span."""

    name: str
    axis: NumericAxis
    inliers: tuple[SeatedTick, ...]
    conflicts: tuple[TickConflict, ...]
    frame_px: tuple[float, float]
    served_px: tuple[float, float]
    label_interval_px: float
    label_offset_spread_px: float
    unserved_reasons: tuple[str, ...] = field(default=())
    gridlines_px: tuple[float, ...] = field(default=())

    def value(self, pixel: float) -> float:
        return self.axis.value(pixel)

    def pixel(self, value: float) -> float:
        return (value - self.axis.b) / self.axis.m

    def is_served(self, pixel: float, tolerance_px: float = 0.5) -> bool:
        lo, hi = self.served_px
        return lo - tolerance_px <= pixel <= hi + tolerance_px

    @property
    def served_values(self) -> tuple[float, float]:
        a, b = (self.value(p) for p in self.served_px)
        return (min(a, b), max(a, b))

    @property
    def frame_values(self) -> tuple[float, float]:
        a, b = (self.value(p) for p in self.frame_px)
        return (min(a, b), max(a, b))

    def tick_hits(self) -> list[dict[str, object]]:
        """Served-calibration pixel error at every printed label (consumed or not)."""
        rows: list[dict[str, object]] = []
        for tick in self.inliers:
            rows.append(
                {
                    "text": tick.text,
                    "value": tick.value,
                    "consumed": True,
                    "grid_px": round(tick.grid_px, 2),
                    "label_px": round(tick.label_px, 2),
                    "served_px": round(self.pixel(tick.value), 2),
                    "error_px": round(self.pixel(tick.value) - tick.grid_px, 2),
                }
            )
        for conflict in self.conflicts:
            rows.append(
                {
                    "text": conflict.text,
                    "value": conflict.value,
                    "consumed": False,
                    "grid_px": None if conflict.grid_px is None else round(conflict.grid_px, 2),
                    "label_px": round(conflict.label_px, 2),
                    "served_px": round(self.pixel(conflict.value), 2),
                    "error_px": None
                    if conflict.grid_px is None
                    else round(self.pixel(conflict.value) - conflict.grid_px, 2),
                    "reason": conflict.reason,
                }
            )
        return sorted(rows, key=lambda row: float(row["value"]))  # type: ignore[arg-type]

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "model": self.axis.model,
            "value_per_px": self.axis.m,
            "value_at_px0": self.axis.b,
            "fit_residual_px": round(self.axis.residual_px, 3),
            "frame_px": [round(v, 2) for v in self.frame_px],
            "frame_values": [round(v, 4) for v in self.frame_values],
            "served_px": [round(v, 2) for v in self.served_px],
            "served_values": [round(v, 4) for v in self.served_values],
            "label_interval_px": round(self.label_interval_px, 3),
            "label_offset_spread_px": round(self.label_offset_spread_px, 3),
            "unserved_reasons": list(self.unserved_reasons),
            "ticks": self.tick_hits(),
        }


def seat_labels_on_gridlines(
    labels: Sequence[tuple[str, float, float]],
    gridlines: Sequence[float],
    *,
    max_offset_fraction: float = _SEAT_MAX_OFFSET_FRACTION,
) -> tuple[list[SeatedTick], list[TickConflict]]:
    """Bind ``(text, value, label_px)`` labels to the nearest gridline or refuse.

    ``gridlines`` must include the frame edges.  A label seats only when its
    nearest gridline is within ``max_offset_fraction`` of the local pitch and
    clearly nearer than the runner-up; two labels on one gridline are both
    conflicts.  Nothing is snapped by regularity: a missing gridline leaves
    its label unseated.
    """
    grid = np.sort(np.asarray(list(gridlines), dtype=float))
    if len(grid) < 3:
        raise RuntimeError("need at least three gridlines (frame edges included) to seat labels")
    pitch = float(np.median(np.diff(grid)))
    seated: list[SeatedTick] = []
    conflicts: list[TickConflict] = []
    for text, value, label_px in labels:
        distances = np.abs(grid - label_px)
        order = np.argsort(distances)
        nearest, runner_up = float(distances[order[0]]), float(distances[order[1]])
        grid_px = float(grid[order[0]])
        if nearest > max_offset_fraction * pitch:
            conflicts.append(
                TickConflict(text, value, label_px, grid_px,
                             f"unseated: nearest gridline {nearest:.1f}px away (pitch {pitch:.1f}px)")
            )
        elif runner_up - nearest < _SEAT_MIN_MARGIN_FRACTION * pitch:
            conflicts.append(
                TickConflict(text, value, label_px, grid_px,
                             f"ambiguous: two gridlines {nearest:.1f}/{runner_up:.1f}px away")
            )
        else:
            seated.append(SeatedTick(text, value, label_px, grid_px))
    by_grid: dict[float, list[SeatedTick]] = {}
    for tick in seated:
        by_grid.setdefault(tick.grid_px, []).append(tick)
    unique: list[SeatedTick] = []
    for grid_px, ticks in by_grid.items():
        if len(ticks) == 1:
            unique.append(ticks[0])
            continue
        for tick in ticks:
            conflicts.append(
                TickConflict(tick.text, tick.value, tick.label_px, grid_px,
                             "shared gridline: several labels seat on one line")
            )
    return sorted(unique, key=lambda t: t.grid_px), conflicts


def _consensus(seated: list[SeatedTick], tolerance_px: float) -> list[SeatedTick]:
    """Largest set of seated ticks consistent with ONE linear map (exhaustive)."""
    best: list[SeatedTick] = []
    best_rms = float("inf")
    for first, second in itertools.combinations(seated, 2):
        if first.value == second.value:
            continue
        slope = (second.grid_px - first.grid_px) / (second.value - first.value)
        members = [
            tick
            for tick in seated
            if abs(first.grid_px + slope * (tick.value - first.value) - tick.grid_px) <= tolerance_px
        ]
        values = np.array([t.value for t in members])
        pixels = np.array([t.grid_px for t in members])
        if len(members) >= 2:
            m, b = np.polyfit(values, pixels, 1)
            rms = float(np.sqrt(np.mean((m * values + b - pixels) ** 2)))
        else:
            rms = float("inf")
        if len(members) > len(best) or (len(members) == len(best) and rms < best_rms):
            best, best_rms = members, rms
    return sorted(best, key=lambda t: t.grid_px)


def fit_review_axis(
    name: str,
    labels: Sequence[tuple[str, float, float]],
    gridlines: Sequence[float],
    frame_px: tuple[float, float],
    *,
    tolerance_px: float | None = None,
    min_inliers: int = 3,
    max_offset_spread_px: float | None = None,
    source_px: float = 1.0,
) -> ReviewAxis:
    """Fit a linear review axis from labels seated on the chart's own gridlines.

    Fails closed (``RuntimeError``) when fewer than ``min_inliers`` labels agree,
    when the consumed labels sit inconsistently on their gridlines (tampered or
    garbled label layers), or when the shared fitter rejects the residual.  The
    served span covers the consumed labels and extends to a frame edge only
    across at most one label interval with no conflicting label on that side.

    ``source_px`` is the size of one SOURCE raster pixel in these image pixels
    (an upscaled embedded chart quantises label and gridline positions to it).
    """
    frame_lo, frame_hi = sorted(float(v) for v in frame_px)
    # A rule drawn on (within a pixel of) the frame edge IS that edge; keeping
    # both would make every label printed at the frame look ambiguous.
    grid = sorted(
        {frame_lo, frame_hi}
        | {float(g) for g in gridlines if min(abs(g - frame_lo), abs(g - frame_hi)) > 1.0}
    )
    pitch = float(np.median(np.diff(grid)))
    tolerance = max(2.0, 0.1 * pitch) if tolerance_px is None else float(tolerance_px)
    seated, conflicts = seat_labels_on_gridlines(labels, grid)
    inliers = _consensus(seated, tolerance)
    if len(inliers) < min_inliers:
        raise RuntimeError(
            f"{name}: only {len(inliers)} printed labels agree on one linear map "
            f"(need {min_inliers}); seated={len(seated)} conflicts={len(conflicts)}"
        )
    inlier_ids = {id(t) for t in inliers}
    for tick in seated:
        if id(tick) not in inlier_ids:
            conflicts.append(
                TickConflict(tick.text, tick.value, tick.label_px, tick.grid_px,
                             "off-consensus: seated on a gridline the other labels place elsewhere")
            )
    axis = fit_axis_ticks(
        [AxisTick(t.text, t.value, t.grid_px) for t in inliers], name, model="linear"
    )
    ordered = sorted(inliers, key=lambda t: t.grid_px)
    interval = float(np.median(np.diff([t.grid_px for t in ordered])))
    served_lo, served_hi = ordered[0].grid_px, ordered[-1].grid_px
    reasons: list[str] = []
    for side in ("low", "high"):
        end = served_lo if side == "low" else served_hi
        edge = frame_lo if side == "low" else frame_hi
        gap = abs(edge - end)
        if gap <= 0.5:
            continue
        beyond = [
            c for c in conflicts
            if (c.label_px < end if side == "low" else c.label_px > end)
        ]
        if beyond:
            reasons.append(
                f"{side}-pixel side not served beyond {axis.value(end):g}: conflicting labels "
                + ", ".join(repr(c.text) for c in beyond)
            )
            continue
        if gap > interval + tolerance:
            reasons.append(
                f"{side}-pixel side not served beyond {axis.value(end):g}: frame extends "
                f"{gap / interval:.2f} label intervals past the last consumed label"
            )
            continue
        if side == "low":
            served_lo = frame_lo
        else:
            served_hi = frame_hi
    # Every label whose value falls inside the served span -- consumed or
    # not -- must sit on the fitted position with the SAME systematic offset.
    # Checking only the consumed ones is not monotone: more scatter unseats
    # more labels and the survivors can look consistent.
    in_span = [(t.text, t.label_px - (t.value - axis.b) / axis.m) for t in inliers]
    for conflict in conflicts:
        predicted = (conflict.value - axis.b) / axis.m
        if served_lo - 0.5 <= predicted <= served_hi + 0.5:
            in_span.append((conflict.text, conflict.label_px - predicted))
    offsets = [offset for _text, offset in in_span]
    spread = float(max(offsets) - min(offsets))
    spread_limit = (
        max(_MAX_OFFSET_SPREAD_FLOOR_PX, _MAX_OFFSET_SPREAD_FRACTION * pitch, 2.0 * source_px)
        if max_offset_spread_px is None
        else float(max_offset_spread_px)
    )
    if spread > spread_limit:
        raise RuntimeError(
            f"{name}: printed labels sit inconsistently on the chart's gridlines "
            f"(offset spread {spread:.1f}px > {spread_limit:.1f}px: "
            + ", ".join(f"{text}:{offset:+.1f}" for text, offset in sorted(in_span, key=lambda i: i[1]))
            + ")"
        )
    return ReviewAxis(
        name=name,
        axis=axis,
        inliers=tuple(inliers),
        conflicts=tuple(sorted(conflicts, key=lambda c: c.label_px)),
        frame_px=(frame_lo, frame_hi),
        served_px=(served_lo, served_hi),
        label_interval_px=interval,
        label_offset_spread_px=spread,
        unserved_reasons=tuple(reasons),
        gridlines_px=tuple(grid),
    )


def assert_ticks_hit(axis: ReviewAxis, tolerance_px: float) -> list[dict[str, object]]:
    """Certify the SERVED calibration at every consumed tick; raise on any miss."""
    rows = axis.tick_hits()
    misses = [r for r in rows if r["consumed"] and abs(float(r["error_px"])) > tolerance_px]  # type: ignore[arg-type]
    if misses:
        raise RuntimeError(
            f"{axis.name}: served calibration misses printed ticks: "
            + ", ".join(f"{r['text']} by {r['error_px']}px" for r in misses)
        )
    return rows


def ink_label_centers(
    gray: np.ndarray,
    band: tuple[int, int, int, int],
    along: str,
    *,
    min_gap_px: int,
    ink_threshold: int = 160,
) -> list[float]:
    """Centres of printed label groups inside ``band`` (x0, y0, x1, y1), in image px.

    For raster charts whose OCR is unreliable the label VALUES are read by the
    reviewer, but their POSITIONS must still be measured: glyph ink is
    projected onto the axis direction (``along`` = ``"x"`` or ``"y"``) and split
    into groups at blank runs of at least ``min_gap_px``.  The caller must
    check that the group count equals the number of values it supplies.
    """
    x0, y0, x1, y1 = band
    ink = gray[y0:y1, x0:x1] < ink_threshold
    profile = ink.sum(axis=0 if along == "x" else 1)
    columns = np.flatnonzero(profile > 0)
    if not len(columns):
        return []
    groups = np.split(columns, np.flatnonzero(np.diff(columns) > min_gap_px) + 1)
    origin = x0 if along == "x" else y0
    return [origin + (float(g[0]) + float(g[-1]) + 1.0) / 2.0 for g in groups if len(g)]


def calibrate_review_points(
    pixels: Sequence[tuple[float, float]], x_axis: ReviewAxis, y_axis: ReviewAxis
) -> list[tuple[float, float, bool]]:
    """Map pixels through both review axes; flag points outside the served span."""
    return [
        (x_axis.value(x), y_axis.value(y), x_axis.is_served(x) and y_axis.is_served(y))
        for x, y in pixels
    ]


# ---------------------------------------------------------------- vector charts


@dataclass(frozen=True)
class VectorStroke:
    color: tuple[float, float, float]
    width: float
    points_pt: tuple[tuple[float, float], ...]


def vector_gridlines(page, frame) -> tuple[list[float], list[float]]:
    """Full-span vertical/horizontal rules inside ``frame`` (PDF pt), frame edges included."""
    xs = {float(frame.x0), float(frame.x1)}
    ys = {float(frame.y0), float(frame.y1)}
    padded = frame + (-0.6, -0.6, 0.6, 0.6)
    for drawing in page.get_drawings():
        if drawing.get("type") not in ("s", "fs"):
            continue
        for item in drawing.get("items", []):
            if item[0] != "l":
                continue
            p0, p1 = item[1], item[2]
            # A rule belongs to THIS chart only when both ends lie on its frame:
            # a neighbouring panel's gridlines share rows/columns with it.
            if not (padded.contains(p0) and padded.contains(p1)):
                continue
            if abs(p0.x - p1.x) < 0.05 and abs(p0.y - p1.y) >= 0.9 * frame.height:
                xs.add(round(float(p0.x), 3))
            elif abs(p0.y - p1.y) < 0.05 and abs(p0.x - p1.x) >= 0.9 * frame.width:
                ys.add(round(float(p0.y), 3))
    return _merge_close(sorted(xs)), _merge_close(sorted(ys))


def _merge_close(values: list[float], tolerance: float = 0.3) -> list[float]:
    merged: list[list[float]] = []
    for value in values:
        if merged and value - merged[-1][-1] <= tolerance:
            merged[-1].append(value)
        else:
            merged.append([value])
    return [float(np.mean(group)) for group in merged]


def _clip_polyline(points: list[tuple[float, float]], frame, pad: float = 0.3):
    """Keep the parts of a polyline inside ``frame``; interpolate the exits."""
    x0, y0, x1, y1 = frame.x0 - pad, frame.y0 - pad, frame.x1 + pad, frame.y1 + pad

    def inside(p: tuple[float, float]) -> bool:
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    def crossing(a, b):
        best = None
        for t_num, t_den in (
            (x0 - a[0], b[0] - a[0]), (x1 - a[0], b[0] - a[0]),
            (y0 - a[1], b[1] - a[1]), (y1 - a[1], b[1] - a[1]),
        ):
            if abs(t_den) < 1e-12:
                continue
            t = t_num / t_den
            if 0.0 <= t <= 1.0:
                p = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
                if inside((min(max(p[0], x0), x1), min(max(p[1], y0), y1))) and (
                    best is None or t < best[0]
                ):
                    best = (t, p)
        return None if best is None else best[1]

    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for index, point in enumerate(points):
        if inside(point):
            if not current and index > 0:
                entry = crossing(point, points[index - 1])
                if entry is not None:
                    current.append(entry)
            current.append(point)
        elif current:
            exit_point = crossing(current[-1], point)
            if exit_point is not None:
                current.append(exit_point)
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def vector_curve_strokes(page, frame, *, min_items: int = 8, min_width: float = 0.8) -> list[VectorStroke]:
    """Chromatic stroke paths that draw curves inside ``frame``, clipped to it."""
    strokes: list[VectorStroke] = []
    for drawing in page.get_drawings():
        if drawing.get("type") != "s":
            continue
        color = drawing.get("color")
        if not isinstance(color, (tuple, list)) or max(color) - min(color) < 0.15:
            continue
        if float(drawing.get("width") or 0.0) < min_width:
            continue
        items = [item for item in drawing.get("items", []) if item[0] in ("l", "c")]
        if len(items) < min_items or not drawing["rect"].intersects(frame):
            continue
        points: list[tuple[float, float]] = []
        for item in items:
            if item[0] == "l":
                segment = [(float(item[1].x), float(item[1].y)), (float(item[2].x), float(item[2].y))]
            else:
                segment = _sample_cubic(
                    *((float(p.x), float(p.y)) for p in item[1:5]), steps=16
                )
            if points and np.hypot(points[-1][0] - segment[0][0], points[-1][1] - segment[0][1]) < 1e-6:
                points.extend(segment[1:])
            elif points:
                raise RuntimeError("curve stroke path is discontinuous; refusing to join sub-paths")
            else:
                points.extend(segment)
        runs = _clip_polyline(points, frame)
        if len(runs) != 1:
            raise RuntimeError(f"curve stroke leaves and re-enters the frame ({len(runs)} runs)")
        strokes.append(
            VectorStroke(tuple(float(c) for c in color[:3]), float(drawing.get("width") or 0.0), tuple(runs[0]))
        )
    return strokes


def _span_color(page, bbox) -> tuple[float, float, float] | None:
    colors = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                sx0, sy0, sx1, sy1 = span["bbox"]
                if sx1 < bbox[0] or sx0 > bbox[2] or sy1 < bbox[1] or sy0 > bbox[3]:
                    continue
                if not span["text"].strip():
                    continue
                value = int(span["color"])
                colors.append(((value >> 16 & 255) / 255.0, (value >> 8 & 255) / 255.0, (value & 255) / 255.0))
    chromatic = {c for c in colors if max(c) - min(c) >= 0.15}
    return next(iter(chromatic)) if len(chromatic) == 1 else None


def legend_temperature_colors(page, clip) -> dict[float, tuple[float, float, float]]:
    """Read ``temperature -> printed colour`` from the chart's own legend.

    A label's colour is its own chromatic text colour (Panjit) or the single
    stroke swatch immediately left of it on the same row (EPC).  Two different
    swatch colours eligible for one label, a label with no colour, or two
    labels sharing a colour make the legend unreadable -> ``RuntimeError``.
    """
    words = [
        w for w in page.get_text("words")
        if clip.x0 <= (w[0] + w[2]) / 2 <= clip.x1 and clip.y0 <= (w[1] + w[3]) / 2 <= clip.y1
    ]
    drawings = [d for d in page.get_drawings() if d.get("type") == "s"]
    bindings: dict[float, tuple[float, float, float]] = {}
    for word in words:
        match = _TEMPERATURE_RE.search(word[4])
        if match is None:
            continue
        temperature = float(match.group(1))
        color = _span_color(page, word[:4])
        if color is None:
            cy = (word[1] + word[3]) / 2
            swatches = {
                tuple(round(float(c), 3) for c in d["color"][:3])
                for d in drawings
                if isinstance(d.get("color"), (tuple, list))
                and max(d["color"]) - min(d["color"]) >= 0.15
                and d["rect"].height <= 3.0
                and d["rect"].width >= 5.0
                and d["rect"].y0 - 2.0 <= cy <= d["rect"].y1 + 2.0
                and 0.0 <= word[0] - d["rect"].x1 <= _LEGEND_MAX_SWATCH_GAP_PT
            }
            if len(swatches) != 1:
                raise RuntimeError(
                    f"legend label {word[4]!r}: {len(swatches)} candidate swatch colours"
                )
            color = next(iter(swatches))  # type: ignore[assignment]
        if temperature in bindings and _color_distance(bindings[temperature], color) > 0.05:
            raise RuntimeError(f"legend temperature {temperature:g} printed in two colours")
        bindings[temperature] = color  # type: ignore[assignment]
    colors = list(bindings.values())
    for a, b in itertools.combinations(colors, 2):
        if _color_distance(a, b) < 0.15:
            raise RuntimeError("two legend temperatures share one colour")
    return bindings


def bind_strokes_to_legend(
    strokes: Sequence[VectorStroke], legend: dict[float, tuple[float, float, float]]
) -> dict[float, VectorStroke]:
    """One-to-one ``temperature -> stroke`` by decisive nearest legend colour."""
    bound: dict[float, VectorStroke] = {}
    for stroke in strokes:
        ranked = sorted((_color_distance(stroke.color, c), t) for t, c in legend.items())
        if not ranked or ranked[0][0] > _LEGEND_MAX_COLOR_DISTANCE:
            raise RuntimeError(f"stroke colour {stroke.color} matches no legend entry")
        if len(ranked) > 1 and ranked[0][0] > 0 and ranked[1][0] / ranked[0][0] < _LEGEND_MIN_COLOR_SEPARATION:
            raise RuntimeError(f"stroke colour {stroke.color} binds ambiguously to the legend")
        temperature = ranked[0][1]
        if temperature in bound:
            raise RuntimeError(f"two curve strokes bind to legend temperature {temperature:g}")
        bound[temperature] = stroke
    missing = set(legend) - set(bound)
    if missing:
        raise RuntimeError(f"legend temperatures without a curve stroke: {sorted(missing)}")
    return bound
