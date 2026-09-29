"""Review-grade retrace of MOSFET C(V) charts (Ciss/Coss/Crss vs VDS).

Fifteen capacitance charts were human-GREEN on overlays that the production
extractor had traced wrongly (re-examined 2026-09-29):

* Crss traced along the 0 pF frame rail while the real Crss was never traced
  (AO, NCE linear panels);
* Ciss and Coss swapped where they genuinely cross (NXP log panels);
* a plot box that took in the neighbouring gate-charge panel, or stopped one
  or two gridlines short of the frame;
* curves that stop short of the frame, hook onto a label arrow, or step onto
  a neighbouring curve.

This module rebuilds a chart from source evidence instead of reusing those
decisions:

* **Geometry.**  Vector curves are read from their own PDF paths: stroked
  polylines are served as drawn; curves drawn as filled outlines are served on
  the centre of their outline at every x (``vector_curve_paths``).  Raster
  curves are tracked stroke by stroke from a seed column where every curve is
  isolated, and tracking stops -- it never guesses -- where a stroke merges
  with the frame, a label or another curve (``track_raster_curves``).  A
  sustained merge of two curves is kept only as an explicitly *shared* span.
* **Frame.**  The frame is the outermost full-span rule on each side of a
  hint box (``own_frame``); a side without such a rule refuses.
* **Identity.**  Crss is the curve that lies lowest at every shared VDS
  (Coss = Cds + Cgd and Ciss = Cgs + Cgd both exceed Crss = Cgd).  Ciss and
  Coss may cross, so they are bound by the printed labels: a label joined to
  a curve by a leader line binds to it, otherwise to the curve nearest the
  label, and the binding must be one-to-one and decisive
  (``bind_labels``).
* **Resolution.**  On a linear capacitance axis a curve that runs within a
  few pixels of the 0 pF rail cannot be told apart from that rail
  (``below_resolution_mask``); such points are flagged, and raster points
  there are not served.

Axis calibration is done by :mod:`gridline_anchor` (labels identify values,
observed gridlines carry every served pixel).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


CURVE_NAMES = ("Ciss", "Coss", "Crss")
# A curve closer than this to the 0 pF rail of a linear axis is not resolved
# by the raster (CHART-REVIEW-CHECKLIST.md section 2: "below ~4 px").
MIN_RESOLVABLE_PX = 4.0


# --------------------------------------------------------------------- frame


@dataclass(frozen=True)
class Frame:
    """The chart's own frame in image pixels (rule centres)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def _line_centres(coverage: np.ndarray, minimum: float) -> list[tuple[float, int]]:
    centres: list[tuple[float, int]] = []
    start = None
    for index, flag in enumerate(list(coverage >= minimum) + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            centres.append(((start + index - 1) / 2.0, index - start))
            start = None
    return centres


def own_frame(
    gray: np.ndarray,
    hint: tuple[float, float, float, float],
    *,
    search_px: int = 14,
    ink_threshold: int = 225,
    min_coverage: float = 0.85,
    max_rule_width: int = 8,
) -> Frame:
    """Locate the frame rules nearest each edge of ``hint`` (x0, y0, x1, y1).

    A frame side is a rule that covers at least ``min_coverage`` of the hint's
    span on the other axis, within ``search_px`` of the hint edge.  The
    outermost such rule wins, so a hint that stops one gridline short of the
    frame is extended to the frame -- but never across the whitespace that
    separates a neighbouring panel (its rules do not span this hint).
    """
    x0, y0, x1, y1 = (int(round(v)) for v in hint)
    ink = gray < ink_threshold
    height, width = ink.shape

    def side(vertical: bool, edge: int, outward: int) -> float:
        lo = max(0, edge - search_px)
        hi = min((width if vertical else height) - 1, edge + search_px)
        if vertical:
            coverage = ink[y0:y1 + 1, lo:hi + 1].mean(axis=0)
        else:
            coverage = ink[lo:hi + 1, x0:x1 + 1].mean(axis=1)
        rules = [
            (lo + centre, run) for centre, run in _line_centres(coverage, min_coverage)
            if run <= max_rule_width
        ]
        if not rules:
            raise RuntimeError(
                f"no frame rule within {search_px}px of the {'vertical' if vertical else 'horizontal'} "
                f"hint edge at {edge}px"
            )
        return float(max(rules)[0] if outward > 0 else min(rules)[0])

    return Frame(
        x0=side(True, x0, -1),
        y0=side(False, y0, -1),
        x1=side(True, x1, +1),
        y1=side(False, y1, +1),
    )


def suppress_curve_ink(
    gray: np.ndarray, frame: Frame, *, dark: int = 90, rule_coverage: float = 0.8, pad: int = 3
) -> np.ndarray:
    """A copy of ``gray`` with curve ink removed inside the frame, rules kept.

    A flat curve lying along a gridline for a large part of the plot joins
    that gridline's detected run and drags its centre by a pixel or more
    (AON6276: the Ciss plateau on the 5000 pF rule moved it 2.5 px).  Curves
    are darker than grey gridlines; dark rows or columns that span the frame
    (black frame rails, black gridlines) are kept, every other dark pixel in
    the frame is painted white before the gridlines are measured.
    """
    out = gray.copy()
    x0, x1 = int(math.floor(frame.x0)) - pad, int(math.ceil(frame.x1)) + pad
    y0, y1 = int(math.floor(frame.y0)) - pad, int(math.ceil(frame.y1)) + pad
    sub = out[y0:y1 + 1, x0:x1 + 1]
    is_dark = sub < dark
    keep = np.zeros_like(is_dark)
    keep[is_dark.mean(axis=1) >= rule_coverage, :] = True
    keep[:, is_dark.mean(axis=0) >= rule_coverage] = True
    sub[is_dark & ~keep] = 255
    return out


# ------------------------------------------------------------ raster curves


@dataclass
class TrackedCurve:
    """A raster curve tracked column by column (image pixels).

    Coordinates are pixel-INDEX coordinates (pixel ``i`` has its centre at
    ``i``), the convention of :mod:`gridline_anchor`'s line centres.  A PDF
    point ``p`` at ``s`` px/pt is at index coordinate ``p * s - 0.5``.

    ``status`` per column: ``"ok"`` (an isolated stroke), ``"shared"`` (one
    stroke shared with ``shared_with``), ``"touching"`` (two strokes touching;
    the centre is half a stroke inside the curve's own edge) or
    ``"unresolved"`` (tracking continued by prediction only; NOT served).
    """

    xs: list[int] = field(default_factory=list)
    ys: list[float] = field(default_factory=list)
    status: list[str] = field(default_factory=list)
    shared_with: list[int | None] = field(default_factory=list)
    stop_reasons: dict[str, str] = field(default_factory=dict)

    def served(self) -> list[tuple[int, float, str]]:
        return [
            (x, y, s) for x, y, s in zip(self.xs, self.ys, self.status) if s != "unresolved"
        ]


def column_runs(mask: np.ndarray, x: int, y0: int, y1: int, bridge: int = 0) -> list[tuple[int, int]]:
    """Dark runs (first row, last row) in column ``x`` between y0 and y1."""
    col = mask[y0:y1 + 1, x]
    rows = np.flatnonzero(col)
    if not len(rows):
        return []
    runs = []
    start = prev = int(rows[0])
    for r in rows[1:]:
        r = int(r)
        if r - prev > bridge + 1:
            runs.append((y0 + start, y0 + prev))
            start = r
        prev = r
    runs.append((y0 + start, y0 + prev))
    return runs


def _seed_columns(mask, frame: Frame, count: int, max_run: float, min_gap: float) -> list[int]:
    fx0, fx1 = int(math.ceil(frame.x0)) + 3, int(math.floor(frame.x1)) - 3
    fy0, fy1 = int(math.ceil(frame.y0)) + 3, int(math.floor(frame.y1)) - 3
    good = []
    for x in range(fx0, fx1 + 1):
        runs = column_runs(mask, x, fy0, fy1)
        if len(runs) != count or any(b - a + 1 > max_run for a, b in runs):
            continue
        if any(runs[i + 1][0] - runs[i][1] < min_gap for i in range(len(runs) - 1)):
            continue
        good.append(x)
    return good


def track_raster_curves(
    gray: np.ndarray,
    frame: Frame,
    *,
    count: int = 3,
    ink_threshold: int = 100,
    stroke_px: float | None = None,
    seed_x: int | None = None,
    max_gap: int = 12,
    frame_margin_px: int = 2,
    min_span_fraction: float = 0.4,
) -> tuple[list[TrackedCurve], int]:
    """Track ``count`` dark strokes left and right from an isolating seed column.

    Curves are indexed top-to-bottom at the seed.  In each next column a
    curve takes the run that continues its previous run (overlap, or a
    one-pixel gap).  Where two curves continue into the same run and the run
    is no taller than a single stroke would be, the span is ``shared``; where
    the continuing run is too tall for the stroke (a label, arrow, frame or
    a second curve entering) or no run continues, the column is
    ``unresolved`` and after ``max_gap`` such columns the curve stops on that
    side.  Columns within ``frame_margin_px`` of a frame rule are never read:
    a curve that meets the frame has ended.
    """
    mask = gray < ink_threshold
    fy0 = int(math.ceil(frame.y0)) + frame_margin_px
    fy1 = int(math.floor(frame.y1)) - frame_margin_px
    xmin = int(math.ceil(frame.x0)) + frame_margin_px
    xmax = int(math.floor(frame.x1)) - frame_margin_px
    if stroke_px is None:
        widths = []
        for x in range(xmin, xmax + 1, 3):
            widths += [b - a + 1 for a, b in column_runs(mask, x, fy0, fy1)]
        if not widths:
            raise RuntimeError("no curve ink inside the frame")
        stroke_px = float(np.median(widths))
    max_run = 2.5 * stroke_px + 1.0  # seed columns only
    geometry = (mask, xmin, xmax, fy0, fy1, stroke_px, max_run, max_gap, count)
    if seed_x is not None:
        return _track_from_seed(geometry, seed_x), seed_x
    seeds = _seed_columns(mask, frame, count, max_run, min_gap=2.0 * stroke_px)
    if not seeds:
        raise RuntimeError(f"no column isolates {count} curve strokes; refusing to seed identities")
    # A seed column can hold a label glyph instead of a curve.  Take the
    # isolating column nearest the plot centre whose strokes ALL track across
    # at least ``min_span_fraction`` of the frame -- a glyph does not.
    centre = (frame.x0 + frame.x1) / 2.0
    for candidate in sorted(seeds, key=lambda x: abs(x - centre)):
        curves = _track_from_seed(geometry, candidate)
        if min(max(c.xs) - min(c.xs) for c in curves) >= min_span_fraction * frame.width:
            return curves, candidate
    raise RuntimeError("no seed column yields curves that all span the plot; refusing to guess identities")


def _track_from_seed(geometry, seed_x: int) -> list[TrackedCurve]:
    mask, xmin, xmax, fy0, fy1, stroke_px, max_run, max_gap, count = geometry
    runs = column_runs(mask, seed_x, fy0, fy1)
    if len(runs) != count:
        raise RuntimeError(f"seed column {seed_x} holds {len(runs)} strokes, need {count}")
    curves = [TrackedCurve() for _ in range(count)]
    for curve, run in zip(curves, runs):
        curve.xs.append(seed_x)
        curve.ys.append((run[0] + run[1]) / 2.0)
        curve.status.append("ok")
        curve.shared_with.append(None)

    for direction in (-1, +1):
        side = "low" if direction < 0 else "high"
        last_run = {i: runs[i] for i in range(count)}
        last_y = {i: (runs[i][0] + runs[i][1]) / 2.0 for i in range(count)}
        slope = {i: 0.0 for i in range(count)}
        raw_slope = {i: 0.0 for i in range(count)}
        heights = {i: [runs[i][1] - runs[i][0] + 1] for i in range(count)}
        gap = {i: 0 for i in range(count)}
        alive = set(range(count))
        x = seed_x
        while alive:
            x += direction
            if x < xmin or x > xmax:
                for i in alive:
                    curves[i].stop_reasons.setdefault(side, "reached the frame")
                break
            column = column_runs(mask, x, fy0, fy1)
            claims: dict[int, list[int]] = {}
            for i in sorted(alive):
                a, b = last_run[i]
                predicted = last_y[i] + slope[i]
                cands = [k for k, (c, d) in enumerate(column) if c <= b + 2 and d >= a - 2]
                if not cands:
                    cands = [k for k, (c, d) in enumerate(column) if c - 1 <= predicted <= d + 1]
                if len(cands) > 1:
                    cands = [min(cands, key=lambda k: abs((column[k][0] + column[k][1]) / 2 - predicted))]
                if cands:
                    claims.setdefault(cands[0], []).append(i)
            taken = {i for owners in claims.values() for i in owners}
            for i in sorted(alive):
                if i in taken:
                    continue
                gap[i] += 1
                _unresolved(curves[i], x, last_y[i] + slope[i] * gap[i])
            for k, owners in claims.items():
                c, d = column[k]
                height = d - c + 1
                centres = _owner_centres(owners, c, d, last_y, stroke_px)
                for i in owners:
                    # A stroke crossing a column at slope s is about
                    # w + |s| tall; a label, arrowhead or second curve
                    # merged into the run makes it taller than that.
                    steepness = max(abs(slope[i]), abs(raw_slope[i]))
                    allowed = 1.3 * stroke_px + 1.0 + 1.5 * steepness
                    # Also compare with the stroke's own recent thickness: an
                    # arrow tip that adds 2-3 px is below the global bound.
                    recent = float(np.median(heights[i][-8:])) if heights[i] else stroke_px
                    allowed = min(allowed, max(recent + max(2.0, 0.4 * stroke_px) + 1.5 * steepness, stroke_px + 2.0))
                    tall = len(owners) == 1 and height > allowed and not _is_steep_continuation(
                        mask, x, direction, (c, d), last_y[i], last_run[i], stroke_px, fy0, fy1, xmin, xmax
                    )
                    # a run cut by the unread margin rows continues into a frame
                    # rail: the curve is merged with the rail there (Crss -> 0 pF)
                    on_rail = c <= fy0 or d >= fy1
                    if centres is None or tall or on_rail:
                        gap[i] += 1
                        _unresolved(curves[i], x, last_y[i] + slope[i] * gap[i])
                        continue
                    centre, status = centres[i]
                    new_slope = (centre - last_y[i]) / (gap[i] + 1)
                    gap[i] = 0
                    raw_slope[i] = new_slope
                    if len(owners) == 1:
                        heights[i].append(height)
                    slope[i] = 0.7 * slope[i] + 0.3 * new_slope
                    last_y[i] = centre
                    last_run[i] = (c, d)
                    curves[i].xs.append(x)
                    curves[i].ys.append(centre)
                    others = [j for j in owners if j != i]
                    curves[i].status.append(status)
                    curves[i].shared_with.append(others[0] if others else None)
            for i in list(alive):
                if gap[i] > max_gap:
                    alive.discard(i)
                    curves[i].stop_reasons[side] = (
                        f"stroke lost at x={x - direction * gap[i]}px: no isolated continuation "
                        f"for {gap[i]} columns (merge with frame/label/other curve)"
                    )
                    _trim_trailing_unresolved(curves[i])
    for curve in curves:
        order = np.argsort(curve.xs)
        for attr in ("xs", "ys", "status", "shared_with"):
            setattr(curve, attr, [getattr(curve, attr)[k] for k in order])
    return curves


def _is_steep_continuation(mask, x, direction, run, last_y, last_run, stroke_px, fy0, fy1, xmin, xmax,
                           lookahead: int = 10) -> bool:
    """Tell a steep stretch of the curve from ink merged into it.

    A run taller than the stroke's own slope explains is either the curve
    turning steep (a knee, a step, a low-VDS head) or a label, arrowhead or
    other curve touching it.  Follow the run's continuation for up to
    ``lookahead`` columns until the stroke is stroke-thin again: a steep
    stretch resumes AWAY from where the curve was, merged ink lets the curve
    resume where it was.  With no thin resumption in reach, accept only a
    run that grew gradually from the previous one (a steepening head).
    """
    normal = 1.4 * stroke_px + 1.0
    top, bottom = run
    previous_height = last_run[1] - last_run[0] + 1
    for step in range(1, lookahead + 1):
        xx = x + direction * step
        if xx < xmin or xx > xmax:
            break
        nxt = [r for r in column_runs(mask, xx, fy0, fy1) if r[0] <= bottom + 1 and r[1] >= top - 1]
        if len(nxt) != 1:
            break
        top, bottom = nxt[0]
        if bottom - top + 1 <= normal:
            resumed = (top + bottom) / 2.0
            return abs(resumed - last_y) > max(2.0 * stroke_px, 1.5 * step)
    return (run[1] - run[0] + 1) <= 1.3 * previous_height + 2.0


def _owner_centres(owners, top, bottom, last_y, stroke_px):
    """Centre of each curve inside one dark run, or ``None`` when unknowable.

    One owner: the run centre.  Two owners: a run no taller than ~1.4 strokes
    is ONE stroke both curves share (``shared``); a run up to ~2.6 strokes
    tall is two strokes touching, so each centre sits half a stroke inside
    its own edge (``touching``, the upper curve on the upper edge).  Anything
    taller, or three owners, cannot be split.
    """
    height = bottom - top + 1
    centre = (top + bottom) / 2.0
    if len(owners) == 1:
        return {owners[0]: (centre, "ok")}
    if len(owners) != 2:
        return None
    if height <= 1.4 * stroke_px:
        return {i: (centre, "shared") for i in owners}
    if height <= 2.6 * stroke_px + 1.0:
        upper, lower = sorted(owners, key=lambda i: last_y[i])
        half = stroke_px / 2.0
        return {upper: (top + half - 0.5, "touching"), lower: (bottom - half + 0.5, "touching")}
    return None


def _unresolved(curve: TrackedCurve, x: int, y: float) -> None:
    curve.xs.append(x)
    curve.ys.append(float(y))
    curve.status.append("unresolved")
    curve.shared_with.append(None)


def _trim_trailing_unresolved(curve: TrackedCurve) -> None:
    while curve.status and curve.status[-1] == "unresolved":
        for attr in ("xs", "ys", "status", "shared_with"):
            getattr(curve, attr).pop()


# ---------------------------------------------------------------- resolution


def below_resolution_mask(
    ys_px: Sequence[float], zero_px: float, *, minimum_px: float = MIN_RESOLVABLE_PX
) -> list[bool]:
    """True where a curve lies within ``minimum_px`` of the 0-value rail.

    Only meaningful on a LINEAR capacitance axis whose frame bottom is 0 pF:
    a stroke that close to the rail merges with it, so a raster cannot say
    where the curve is, and a raster "trace" there is the rail itself.
    """
    return [abs(zero_px - y) < minimum_px for y in ys_px]
