"""Seat label-identified axis ticks on the gridlines or tick marks they label.

A printed tick label names a VALUE; it does not mark a PIXEL. Datasheets set
labels beside their gridline, not centred on it: the glyph is shifted for
legibility, a two-digit label is wider than a one-digit one, and a decade label
sits a few pixels above or below its rule. Fitting the axis to label centres
therefore calibrates the typography, and a residual measured against those same
centres certifies the fit to itself -- it cannot see that the served mapping
misses the grid. On the Vishay BAT54W-G leakage panel the label-centre fit put
the 25 V tick 12 px right of its gridline and missed the 100 uA decade by 5.6 px
while reporting a 1.4 px residual.

This module keeps the labels for IDENTITY only and takes every served pixel from
the raster:

1. Observed lines are long thin dark runs across the plot (gridlines, including
   the frame, whose centre is used however thick it is), plus short tick marks
   attached to the frame on the label side when the chart has no gridline there.
2. Each labelled tick must have an observed line within a fraction of the label
   pitch, or the axis is refused: a label with nothing under it is not a tick.
3. The labelled sequence is registered onto the observed lines as ONE affine
   hypothesis (end ticks choose the hypothesis, every interior tick must then
   land on a line). On a log axis the hypothesis must also explain the minor
   gridlines of each labelled decade, so the 90 % minor line 0.046 decade from a
   decade cannot stand in for it even when a label sits closer to the minor.
4. The axis is re-fitted on the matched line centres and the SERVED mapping is
   asserted at every consumed tick; a miss beyond tolerance fails closed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from .numeric_axis import AxisTick, NumericAxis, fit_axis_ticks

Orientation = Literal["x", "y"]

# Pixels darker than this count as ink. Gridlines are often mid-grey, so the
# threshold is well above black but below the anti-aliased paper background.
_INK_THRESHOLD = 200
# A gridline covers most of the plot span; curves, annotations and text do not.
_GRIDLINE_MIN_COVERAGE = 0.40
# A labelled tick may sit this fraction of the label pitch from its line.
_LABEL_TO_LINE_PITCH_FRACTION = 0.30
# A registered hypothesis must place every labelled tick on a line within this.
_MATCH_TOLERANCE_FRACTION = 0.015
_MATCH_TOLERANCE_MIN_PX = 1.5
# A tick mark must be ink over this fraction of its band beside the frame.
_TICK_MARK_MIN_FILL = 0.80


@dataclass(frozen=True)
class ObservedLine:
    center_px: float
    width_px: int
    source: str  # "gridline" or "tick_mark"


@dataclass(frozen=True)
class TickAnchor:
    text: str
    value: float
    label_px: float
    line_px: float
    source: str
    served_px: float

    @property
    def label_offset_px(self) -> float:
        return self.label_px - self.line_px

    @property
    def served_error_px(self) -> float:
        return self.served_px - self.line_px


@dataclass(frozen=True)
class AnchoredAxis:
    axis: NumericAxis
    anchors: tuple[TickAnchor, ...]
    tolerance_px: float

    @property
    def max_served_error_px(self) -> float:
        return max(abs(anchor.served_error_px) for anchor in self.anchors)

    def payload(self) -> dict[str, object]:
        return {
            "pixel_source": "observed_gridline_or_tick_mark",
            "label_role": "value_identity_only",
            "residual_basis": "observed_lines",
            "tolerance_px": round(self.tolerance_px, 3),
            "max_served_error_px": round(self.max_served_error_px, 3),
            "ticks": [
                {
                    "text": anchor.text,
                    "value": anchor.value,
                    "label_px": round(anchor.label_px, 3),
                    "line_px": round(anchor.line_px, 3),
                    "line_source": anchor.source,
                    "served_px": round(anchor.served_px, 3),
                    "served_error_px": round(anchor.served_error_px, 3),
                    "label_offset_px": round(anchor.label_offset_px, 3),
                }
                for anchor in self.anchors
            ],
        }


def served_pixel(axis: NumericAxis, value: float) -> float:
    """Invert the served calibration: the pixel the mapping assigns to *value*."""
    coordinate = math.log10(value) if axis.model == "log10" else value
    return (coordinate - axis.b) / axis.m


def anchor_axis_on_grid(
    gray: np.ndarray,
    label_axis: NumericAxis,
    *,
    orientation: Orientation,
    cross_span: tuple[float, float],
    name: str,
    ink_threshold: int = _INK_THRESHOLD,
) -> AnchoredAxis:
    """Re-seat *label_axis*'s ticks on observed lines, re-fit, and assert.

    *orientation* ``"x"`` means the ticks are vertical lines at x pixels with
    labels below the plot; ``"y"`` means horizontal lines at y pixels with
    labels left of the plot. *cross_span* is the plot's extent along the other
    axis, used to measure how much of the plot a candidate line covers.
    *ink_threshold* is the grey level below which a pixel counts as ink; raise
    it for charts whose hairline rules render lighter than the default (an
    anti-aliased half-pixel frame rule renders at ~210).
    """
    ticks = sorted(label_axis.ticks, key=lambda tick: tick.pixel)
    if len(ticks) < 2:
        raise RuntimeError(f"{name}: need >=2 labelled ticks to anchor on the grid")
    label_px = np.asarray([tick.pixel for tick in ticks], dtype=float)
    pitch = float(np.median(np.diff(label_px)))
    search = _LABEL_TO_LINE_PITCH_FRACTION * pitch
    match_tol = max(_MATCH_TOLERANCE_MIN_PX, _MATCH_TOLERANCE_FRACTION * pitch)

    lines = detect_axis_lines(
        gray,
        orientation=orientation,
        along=(float(label_px[0]) - search, float(label_px[-1]) + search),
        cross_span=cross_span,
        max_width=max(8, int(round(0.08 * pitch))),
        tick_band=max(3, int(round(0.05 * pitch))),
        ink_threshold=ink_threshold,
    )
    centers = np.asarray([line.center_px for line in lines], dtype=float)

    candidates: list[list[int]] = []
    for tick in ticks:
        near = [i for i, c in enumerate(centers) if abs(c - tick.pixel) <= search]
        if not near:
            raise RuntimeError(
                f"{name}: label {tick.text!r} ({tick.value:g}) at {tick.pixel:.1f}px has "
                f"no gridline or tick mark within {search:.1f}px; refusing to calibrate "
                "on the label glyph"
            )
        candidates.append(near)

    coords = np.asarray(
        [math.log10(t.value) if label_axis.model == "log10" else t.value for t in ticks]
    )
    hypotheses = _register(
        centers, coords, label_px, candidates, match_tol, label_axis.model
    )
    if not hypotheses:
        raise RuntimeError(
            f"{name}: no single linear registration places every labelled tick on an "
            f"observed line within {match_tol:.1f}px; the labels and the grid disagree"
        )
    hypotheses.sort(key=lambda h: (-h[0], h[1]))
    best = hypotheses[0]
    ambiguity = max(2.0, 0.1 * pitch)
    for other in hypotheses[1:]:
        if other[2] != best[2] and other[0] == best[0] and other[1] - best[1] < ambiguity:
            raise RuntimeError(
                f"{name}: two grid registrations explain the labels equally well "
                f"(mean label offset {best[1]:.1f}px vs {other[1]:.1f}px); refusing "
                "to pick a gridline by proximity alone"
            )
    matched = best[2]

    anchored_ticks = [
        AxisTick(tick.text, tick.value, float(centers[i]), tick.normalized_text)
        for tick, i in zip(ticks, matched)
    ]
    line_px = np.asarray([centers[i] for i in matched])
    label_order = np.sign(np.diff(label_px))
    if np.any(np.sign(np.diff(line_px)) != label_order):
        raise RuntimeError(f"{name}: matched gridlines disagree with the label order")

    axis = fit_axis_ticks(anchored_ticks, name, model=label_axis.model)  # type: ignore[arg-type]
    anchors = tuple(
        TickAnchor(
            tick.text,
            tick.value,
            float(tick.pixel),
            float(centers[i]),
            lines[i].source,
            served_pixel(axis, tick.value),
        )
        for tick, i in zip(ticks, matched)
    )
    result = AnchoredAxis(axis, anchors, match_tol)
    if result.max_served_error_px > match_tol:
        worst = max(anchors, key=lambda anchor: abs(anchor.served_error_px))
        raise RuntimeError(
            f"{name}: served calibration misses the {worst.text!r} gridline by "
            f"{worst.served_error_px:+.2f}px (tolerance {match_tol:.2f}px)"
        )
    return result


def _register(
    centers: np.ndarray,
    coords: np.ndarray,
    label_px: np.ndarray,
    candidates: list[list[int]],
    match_tol: float,
    model: str,
) -> list[tuple[int, float, tuple[int, ...]]]:
    """Enumerate affine label->line registrations; return (score, offset, lines).

    The end ticks' candidate lines define each hypothesis. Every labelled tick
    must then land on an observed line. The score rewards predicted minor
    decade lines that exist and penalises observed lines inside the labelled
    span that the hypothesis cannot explain, so a registration shifted onto a
    family of minor lines loses to the one seated on the decades.
    """
    span = coords[-1] - coords[0]
    seen: set[tuple[int, ...]] = set()
    out: list[tuple[int, float, tuple[int, ...]]] = []
    for first in candidates[0]:
        for last in candidates[-1]:
            a, b = centers[first], centers[last]
            if b == a or np.sign(b - a) != np.sign(label_px[-1] - label_px[0]):
                continue
            scale = (b - a) / span
            predicted = a + (coords - coords[0]) * scale
            matched: list[int] = []
            for p in predicted:
                i = int(np.argmin(np.abs(centers - p)))
                if abs(centers[i] - p) > match_tol:
                    break
                matched.append(i)
            else:
                key = tuple(matched)
                if key in seen or len(set(key)) != len(key):
                    continue
                seen.add(key)
                expected = list(predicted)
                hits = 0
                if model == "log10":
                    for lo, hi in zip(coords, coords[1:]):
                        if not math.isclose(abs(hi - lo), 1.0, abs_tol=1e-9):
                            continue
                        base = min(lo, hi)
                        for j in range(2, 10):
                            p = a + (base + math.log10(j) - coords[0]) * scale
                            expected.append(p)
                            if np.min(np.abs(centers - p)) <= match_tol:
                                hits += 1
                lo_px, hi_px = sorted((a, b))
                expected_arr = np.asarray(expected)
                unexplained = sum(
                    1
                    for c in centers
                    if lo_px + match_tol < c < hi_px - match_tol
                    and np.min(np.abs(expected_arr - c)) > match_tol
                )
                offset = float(np.mean(np.abs(label_px - centers[list(key)])))
                out.append((hits - unexplained, offset, key))
    return out


def detect_axis_lines(
    gray: np.ndarray,
    *,
    orientation: Orientation,
    along: tuple[float, float],
    cross_span: tuple[float, float],
    max_width: int,
    tick_band: int,
    ink_threshold: int = _INK_THRESHOLD,
) -> list[ObservedLine]:
    """Find gridlines (and frame-attached tick marks) crossing one axis.

    For ``orientation="x"`` the lines are vertical and *along* bounds their x
    positions; for ``"y"`` they are horizontal. A line's pixel is the centre
    of its dark run, so a thick frame and a hairline gridline are anchored the
    same way.
    """
    ink = gray < ink_threshold
    if orientation == "y":
        ink = ink.T  # rows become columns: lines are always "vertical" below
    height, width = ink.shape
    c0 = max(0, int(math.floor(cross_span[0])))
    c1 = min(height, int(math.ceil(cross_span[1])) + 1)
    a0 = max(0, int(math.floor(along[0])))
    a1 = min(width, int(math.ceil(along[1])) + 1)
    if c1 - c0 < 4 or a1 - a0 < 2:
        return []

    coverage = ink[c0:c1, a0:a1].mean(axis=0)
    lines = [
        ObservedLine(a0 + center, run_width, "gridline")
        for center, run_width in _runs(coverage >= _GRIDLINE_MIN_COVERAGE)
        if run_width <= max_width
    ]

    # x labels sit below the plot (high rows); y labels sit left of it, which
    # after the transpose is the low end of the cross span.
    label_end = c1 - 1 if orientation == "x" else c0
    frame = _frame_on_label_side(ink, label_end, (a0, a1), max_width)
    if frame is not None:
        top, bottom = frame
        bands = [ink[max(0, top - tick_band):top, a0:a1],
                 ink[bottom + 1:min(height, bottom + 1 + tick_band), a0:a1]]
        filled = np.zeros(a1 - a0, dtype=bool)
        for band in bands:
            if band.shape[0] == tick_band:
                filled |= band.mean(axis=0) >= _TICK_MARK_MIN_FILL
        for center, run_width in _runs(filled):
            position = a0 + center
            if run_width <= max_width and all(
                abs(position - line.center_px) > max_width for line in lines
            ):
                lines.append(ObservedLine(position, run_width, "tick_mark"))
    return sorted(lines, key=lambda line: line.center_px)


def _frame_on_label_side(
    ink: np.ndarray, label_end: int, along: tuple[int, int], max_width: int
) -> tuple[int, int] | None:
    """Return the (first, last) row of the frame rule at the label-side end.

    Tick marks hang off this rule; a chart with no rule there has no tick
    marks this detector can attribute, and returns ``None``.
    """
    a0, a1 = along
    row_coverage = ink[:, a0:a1].mean(axis=1)
    rules = [
        (center, run_width)
        for center, run_width in _runs(row_coverage >= 0.6)
        if run_width <= max_width
    ]
    if not rules:
        return None
    center, run_width = min(rules, key=lambda rule: abs(rule[0] - label_end))
    if abs(center - label_end) > 3 * max_width:
        return None
    first = int(round(center - (run_width - 1) / 2))
    return first, first + run_width - 1


def _runs(mask: np.ndarray) -> list[tuple[float, int]]:
    """Centres and widths of consecutive True runs in a 1-D mask."""
    runs: list[tuple[float, int]] = []
    start = None
    for index, flag in enumerate(list(mask) + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append(((start + index - 1) / 2.0, index - start))
            start = None
    return runs
