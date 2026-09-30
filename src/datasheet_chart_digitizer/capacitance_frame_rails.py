"""Plot box from the chart's own axis rails when it has no solid gridlines.

``find_closed_frame_plot_box`` locates a plot from solid vertical gridlines, or
from four mutually closing rails. A chart whose major grid is DOTTED, DASHED or
absent leaves neither: morphological opening with a long kernel erases the
dotted rules, an OPEN frame (left + bottom axis only) never closes, and outward
tick marks lengthen the bottom rail past the left one so a closed box fails the
endpoint tolerance. Such charts were refused with "could not find plot grid
verticals" although their axes are drawn as solid strokes.

This module is a capacitance-only LAST RESORT, called after the shared
detectors refused. It measures rails per row/column (longest ink run, not a
morphology contour, so a thick frame and a thin axis are treated alike and a
wide crop does not disqualify a rail by a crop-relative kernel) and accepts a
box only on a bottom-left CORNER: a left rail whose lower end meets a bottom
rail that starts at it. That corner is the one structure every plotted axis
pair has; gridlines, curve plateaus and legend boxes do not form it at the
rail ends. The far edges come from a closing right/top rail when one exists,
else from the rails' own extents (an open frame's axes span the plot).

The box only bounds extraction. Axis calibration is still seated on observed
rules or frame tick marks by ``check_served_on_grid``; nothing here uses a
label position.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .capacitance_types import PlotBox

# Pixels darker than the paper by this much are ink. Paper is taken as a high
# percentile of the crop so grey scanned backgrounds are handled.
_PAPER_PERCENTILE = 90
_INK_BELOW_PAPER = 40
_MAX_INK_LEVEL = 225
# A run may bridge this many non-ink pixels (JPEG/anti-alias dropouts).
_RUN_GAP_PX = 2
# A rail's longest run must cover this fraction of the crop dimension.
_MIN_RAIL_FRACTION = 0.30
# Adjacent rail columns/rows merge into one rail up to this thickness.
_MAX_RAIL_THICKNESS_PX = 10
# Columns of a rail group whose run is at least this share of the longest
# form the rail's core.
_CORE_LENGTH_FRACTION = 0.97
# Tick marks may extend a rail past the corner by up to this fraction of the
# plot dimension (outward ticks on the bottom-left of both axes).
_TICK_FRACTION = 0.03
_TICK_MIN_PX = 6.0
# The recovered box must be plot sized.
_MIN_BOX_WIDTH_FRACTION = 0.35
_MIN_BOX_HEIGHT_FRACTION = 0.40
# Crop-border rails (finder or caption-cell rules) are not plot rails.
_BORDER_FRACTION = 0.02


@dataclass(frozen=True)
class Rail:
    center: float  # column (vertical) or row (horizontal) centre, pixel index
    start: int     # first pixel of the run along the rail
    end: int       # last pixel of the run along the rail


def _ink_mask(gray: np.ndarray) -> np.ndarray:
    paper = float(np.percentile(gray, _PAPER_PERCENTILE))
    return gray < min(_MAX_INK_LEVEL, paper - _INK_BELOW_PAPER)


def _longest_runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(start, end) of the longest gap-bridged True run in each COLUMN."""
    rows, cols = mask.shape
    best_start = np.full(cols, -1)
    best_end = np.full(cols, -2)
    for c in range(cols):
        idx = np.flatnonzero(mask[:, c])
        if idx.size == 0:
            continue
        breaks = np.flatnonzero(np.diff(idx) > _RUN_GAP_PX + 1)
        starts = np.concatenate(([idx[0]], idx[breaks + 1]))
        ends = np.concatenate((idx[breaks], [idx[-1]]))
        k = int(np.argmax(ends - starts))
        best_start[c], best_end[c] = starts[k], ends[k]
    return best_start, best_end


def _rails(mask: np.ndarray, min_length: float) -> list[Rail]:
    """Rails running down the columns of *mask* (transpose for rows).

    Adjacent qualifying columns form one rail. A curve lying along a rail (a
    near-zero Crss on the bottom axis) thickens the group and shortens the
    run of its outer columns, so the rail's centre is taken from its CORE --
    the columns whose run is within a few percent of the group's longest --
    and its extent from the longest run.
    """
    starts, ends = _longest_runs(mask)
    lengths = ends - starts + 1
    good = lengths >= min_length
    rails: list[Rail] = []
    c = 0
    n = mask.shape[1]
    while c < n:
        if not good[c]:
            c += 1
            continue
        d = c
        while d + 1 < n and good[d + 1]:
            d += 1
        group = np.arange(c, d + 1)
        core = group[lengths[group] >= _CORE_LENGTH_FRACTION * lengths[group].max()]
        if core[-1] - core[0] + 1 <= _MAX_RAIL_THICKNESS_PX:
            rails.append(
                Rail(
                    center=float(core.mean()),
                    start=int(starts[core].min()),
                    end=int(ends[core].max()),
                )
            )
        c = d + 1
    return rails


def rail_plot_box(gray: np.ndarray) -> PlotBox | None:
    """Return the plot box closed by a bottom-left axis corner, or ``None``.

    ``None`` on no corner, on a box that is not plot sized, and on two
    distinct plot-sized corners (a crop holding two panels): ambiguity is a
    refusal, never a pick.
    """
    height, width = gray.shape
    ink = _ink_mask(gray)
    verticals = [
        r for r in _rails(ink, _MIN_RAIL_FRACTION * height)
        if _BORDER_FRACTION * width <= r.center <= (1 - _BORDER_FRACTION) * width
    ]
    horizontals = [
        r for r in _rails(ink.T, _MIN_RAIL_FRACTION * width)
        if _BORDER_FRACTION * height <= r.center <= (1 - _BORDER_FRACTION) * height
    ]
    candidates: list[PlotBox] = []
    for left in verticals:
        for bottom in horizontals:
            tick_x = max(_TICK_MIN_PX, _TICK_FRACTION * (bottom.end - left.center))
            tick_y = max(_TICK_MIN_PX, _TICK_FRACTION * (bottom.center - left.start))
            # the bottom rail starts at the left rail (or an outward y tick
            # before it); the left rail ends at the bottom rail (or an
            # outward x tick below it)
            if not left.center - tick_x <= bottom.start <= left.center + _TICK_MIN_PX:
                continue
            if not bottom.center - _TICK_MIN_PX <= left.end <= bottom.center + tick_y:
                continue
            x1, y0 = _far_edges(left, bottom, verticals, horizontals, tick_x, tick_y)
            box = PlotBox(
                int(round(left.center)), int(round(y0)),
                int(round(x1)), int(round(bottom.center)),
            )
            if (
                box.width >= _MIN_BOX_WIDTH_FRACTION * width
                and box.height >= _MIN_BOX_HEIGHT_FRACTION * height
            ):
                candidates.append(box)
    distinct = list(dict.fromkeys(candidates))
    if not distinct:
        return None
    best = max(distinct, key=lambda b: b.width * b.height)
    for other in distinct:
        if other != best and _iou(other, best) < 0.8:
            return None
    return best


def _far_edges(
    left: Rail,
    bottom: Rail,
    verticals: list[Rail],
    horizontals: list[Rail],
    tick_x: float,
    tick_y: float,
) -> tuple[float, float]:
    """(x1, y0) of the plot whose bottom-left corner is (left, bottom).

    A closed frame is taken only as a MUTUALLY closing right + top pair: the
    right rail spans from the top rail down to the bottom one and the top
    rail spans from the left rail across to the right one, each within an
    outward-tick allowance of the corner rails' ends. A single rail near the
    end is not enough -- on an open frame the last dense minor gridline sits
    a few pixels inside the axis end and would shrink the box. Without a
    closing pair the open frame's own axes give the extent.
    """
    tol = _TICK_MIN_PX
    pairs: list[tuple[float, float]] = []
    for right in verticals:
        if right is left or not -tol <= bottom.end - right.center <= tick_x:
            continue
        for top in horizontals:
            if top is bottom or not -tol <= top.center - left.start <= tick_y:
                continue
            if not (
                top.center - tick_y <= right.start <= top.center + tol
                and right.end >= bottom.center - tol
                and top.start <= left.center + tol
                and right.center - tol <= top.end <= right.center + tick_x
            ):
                continue
            pairs.append((right.center, top.center))
    if pairs:
        # the outermost closing pair is the frame; inner pairs are grid rules
        return max(pairs, key=lambda p: (p[0], -p[1]))
    return float(bottom.end), float(left.start)


def _iou(a: PlotBox, b: PlotBox) -> float:
    ix = max(0, min(a.x1, b.x1) - max(a.x0, b.x0))
    iy = max(0, min(a.y1, b.y1) - max(a.y0, b.y0))
    inter = ix * iy
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union > 0 else 0.0
