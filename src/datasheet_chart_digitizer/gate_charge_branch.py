"""Keep a traced VGS(Qg) curve on ONE VDD branch.

Multi-VDD gate-charge charts share the initial ramp and the Miller plateau; after
the plateau each VDD leaves on its own, nearly straight and nearly parallel,
rising branch. A column-wise nearest-centre tracer hops from one branch to the
next wherever its own branch is missing from the mask: under an erased in-plot
label ("40 V", "VDS = 5 V"), across an annotation arrow, or where the branch
ends at the top and a longer neighbour keeps going. The served curve is then a
staircase through several physical curves, and the hop's back-and-forth even
fakes a flat "plateau" for the Vpl estimator (synth set1 syn1_0025: 6.8 V
served "ok" for a 4.44 V plateau).

Two layers:

* ``follow_branch_through_hops`` (raw trace, per column): where the tracer
  leaves a rising straight segment with a step, follow the source ink on the
  extrapolated segment across the gap. Bridges occlusions only when the ink is
  really there; it never shortens a trace.
* ``cut_at_branch_hop`` (final curve, any trace source): a remaining step
  between two parallel rising segments, or a lift off the plateau onto a
  rising branch above it, is a hop to another branch. The curve
  is cut before it, and a trailing fragment isolated by a wide x gap is
  dropped. The caller flags a curve whose kept branch stops short.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

# a step off the segment larger than this (fraction of plot height) is not the
# same stroke; branch separations on multi-VDD charts are 3-15 % of the height.
# The raw-trace repair may use a lower bar: it only acts where the source ink
# continues on the segment, so a false trigger changes nothing.
HOP_FRACTION = 0.035
FOLLOW_HOP_FRACTION = 0.025
# only straight rising segments can hop: slope in px/px (y down), VGS rising
MIN_RISE_SLOPE = 0.15
# two sides of a hop are the same family of parallel branches
PARALLEL_SLOPE_RATIO = 0.35
# a kept branch that stops below this share of the plot height is not the curve
MIN_KEPT_RISE_FRACTION = 0.6
# ... nor one that ends on (or just past) the plateau: the Vpl estimator needs the
# rise after the plateau, and without it falls back to a guess (IRLB4030 2.30 V
# for a 3.6 V plateau when a knee was mistaken for a hop)
MIN_POST_PLATEAU_RISE_FRACTION = 0.1
BRANCH_HOP_DIAGNOSTIC = "curve_branch_hop_cut_short"


def _plateau_x(points: list[tuple[int, int]], width: int, height: int) -> float | None:
    """x where the shared Miller plateau begins on the trace.

    Branches only separate after the plateau, so a step before it is not a
    branch hop (it is a gap on the shared ramp) and is left to the other
    guards. The plateau is the first flat stretch of >= 3 % of the width, or
    the first x gap of that size across which the trace rises by less than half
    of the mean ramp slope before it (an erased label under the plateau leaves
    only the gap). Neither found: None, nothing is judged.
    """

    span = max(6.0, 0.03 * width)
    flat = max(3.0, 0.01 * height)
    ordered = sorted(points)
    lo = 0
    for hi in range(len(ordered)):
        if hi and ordered[hi][0] - ordered[hi - 1][0] >= span:
            (xa, ya), (xb, yb) = ordered[hi - 1], ordered[hi]
            ramp = (ordered[0][1] - ya) / max(1.0, xa - ordered[0][0])
            if ramp > 0 and (ya - yb) < 0.5 * ramp * (xb - xa):
                return float(xa)
        while ordered[hi][0] - ordered[lo][0] > span:
            lo += 1
        ys = [y for _x, y in ordered[lo : hi + 1]]
        if ordered[hi][0] - ordered[lo][0] >= 0.6 * span and hi - lo >= 3 and max(ys) - min(ys) <= flat:
            return float(ordered[lo][0])
    return None


def _line(
    points: list[tuple[int, int]], span: int, min_inliers: float = 0.7
) -> tuple[float, float] | None:
    """Straight y = m x + b over the last *span* px of *points*, or None.

    Robust to a marker or a merged run on the stroke: points more than 2.5 px
    off the first fit are dropped and the rest refitted, and the segment only
    counts as straight when at least *min_inliers* of its points are inliers.
    """

    if not points:
        return None
    last_x = points[-1][0]
    seg = [(x, y) for x, y in points if x >= last_x - span]
    if len(seg) < 5:
        return None
    xs = np.array([p[0] for p in seg], float)
    ys = np.array([p[1] for p in seg], float)
    if np.ptp(xs) < 0.5 * span:
        return None
    m, b = np.polyfit(xs, ys, 1)
    inliers = np.abs(ys - (m * xs + b)) <= 2.5
    if inliers.sum() < max(5, min_inliers * len(xs)) or np.ptp(xs[inliers]) < 0.5 * span:
        return None  # not a straight segment (knee, noise): no prediction
    m, b = np.polyfit(xs[inliers], ys[inliers], 1)
    return float(m), float(b)


def _walk_segment(
    centers_by_x: list[list[float]],
    start: list[tuple[int, int]],
    spans: tuple[int, int],
    tol: float,
    max_gap: int,
) -> list[tuple[int, int]]:
    """Follow source centres along the extrapolated segment, refitting as it goes.

    The segment is fitted over the longest straight stretch available (*spans*
    long, then short) so a wide erased label can be crossed; the acceptance
    band widens by 2 % of the distance bridged.
    """

    def fit_of(path):
        return _line(path, spans[0], 0.6) or _line(path, spans[1])

    path = list(start)
    walked: list[tuple[int, int]] = []
    fit = fit_of(path)
    x = path[-1][0] + 1
    while fit is not None and x < len(centers_by_x) and x - path[-1][0] <= max_gap:
        m, b = fit
        pred = m * x + b
        band = tol + 0.02 * (x - path[-1][0])
        near = [c for c in centers_by_x[x] if abs(c - pred) <= band]
        if near:
            point = (x, int(round(min(near, key=lambda c: abs(c - pred)))))
            path.append(point)
            walked.append(point)
            fit = fit_of(path) or fit
        x += 1
    return walked


def _stroke_runs(walked: list[tuple[int, int]], min_walk: int) -> list[tuple[int, int]]:
    """The walked points that are a stroke, not specks that happen to sit on the line.

    Runs are split at column gaps > 2; runs of < 3 columns are noise. The walk
    counts only if one run has >= *min_walk* columns, and ends with the last
    such run (anything after it is past the branch's end).
    """

    runs: list[list[tuple[int, int]]] = []
    for point in walked:
        if runs and point[0] - runs[-1][-1][0] <= 2:
            runs[-1].append(point)
        else:
            runs.append([point])
    long_runs = [i for i, run in enumerate(runs) if len(run) >= min_walk]
    if not long_runs:
        return []
    return [p for run in runs[: long_runs[-1] + 1] if len(run) >= 3 for p in run]


def _climbs_like_a_branch(
    walked: list[tuple[int, int]], replaced: list[tuple[int, int]], height: int
) -> bool:
    """The walked continuation is a VGS branch, not a plateau or a leader line.

    It must rise (end >= 5 % of the height above where it started) and climb to
    within 10 % of the height of where the replaced trace climbed: a hop swaps
    one rising branch for a parallel one of about the same reach, while a walk
    that stops on a sloped plateau's other branches (IRFBA90N20D: the trace
    left the 5.7-6.6 V sloped plateau at its own knee, the walk rode the
    neighbours' plateau and would have deleted the rise) or runs down a leader
    line (IRLB4030) is not the curve.
    """

    rise = walked[0][1] - walked[-1][1]
    top_walked = min(y for _x, y in walked)
    top_replaced = min(y for _x, y in replaced)
    return rise >= 0.05 * height and top_walked <= top_replaced + 0.10 * height


def follow_branch_through_hops(
    points: list[tuple[int, int]],
    mask: np.ndarray,
    column_centers: Callable[[np.ndarray], list[float]],
) -> list[tuple[int, int]]:
    """Replace a hop onto another branch by the traced branch's own continuation.

    *points* is the per-column trace (roi coordinates), *mask* the full ink mask
    it was traced from, *column_centers* the tracer's stroke-centre finder. A
    hop is a step of more than FOLLOW_HOP_FRACTION of the height off a straight rising
    segment between consecutive trace points after the plateau. The
    continuation is accepted only when source ink lies on the extrapolated
    segment for at least ``min_walk`` columns beyond the hop; otherwise the
    trace is left as it was (cut_at_branch_hop judges it later).
    """

    if len(points) < 12 or mask.size == 0:
        return points
    h, w = mask.shape
    span = max(12, int(0.03 * w))
    spans = (max(24, int(0.08 * w)), span)
    hop = max(6.0, FOLLOW_HOP_FRACTION * h)
    tol = max(3.0, 0.01 * h)
    max_gap = max(15, int(0.20 * w))
    min_walk = max(8, int(0.02 * w))
    centers_by_x: list[list[float]] | None = None
    ordered = sorted(points)
    plateau_x = _plateau_x(ordered, w, h)
    if plateau_x is None:
        return ordered
    for index in range(1, len(ordered)):
        prev, cur = ordered[index - 1], ordered[index]
        if prev[0] <= plateau_x:
            continue
        fit = _line(ordered[:index], spans[0], 0.6) or _line(ordered[:index], span)
        if fit is None or fit[0] > -MIN_RISE_SLOPE:
            continue
        step = cur[1] - (fit[0] * cur[0] + fit[1])
        if abs(step) <= hop:
            continue
        if centers_by_x is None:
            centers_by_x = [column_centers(mask[:, x]) for x in range(w)]
        walked = _stroke_runs(
            _walk_segment(centers_by_x, ordered[:index], spans, tol, max_gap), min_walk
        )
        if not walked or not _climbs_like_a_branch(walked, ordered[index:], h):
            continue
        # the branch's own continuation replaces everything the tracer did after
        # leaving it; those later points belong to the neighbour it hopped onto
        return ordered[:index] + walked
    return ordered


def _fit(points: np.ndarray) -> tuple[float, float, float] | None:
    """(slope, intercept, max |residual|) of a least-squares line, or None."""

    if len(points) < 2 or np.ptp(points[:, 0]) <= 0:
        return None
    m, b = np.polyfit(points[:, 0], points[:, 1], 1)
    return float(m), float(b), float(np.max(np.abs(points[:, 1] - (m * points[:, 0] + b))))


def _first_branch_hop(curve: np.ndarray, width: int, height: int, k: int = 4) -> int | None:
    """Index of the first point after a step onto another branch.

    A jump of more than HOP_FRACTION of the height at the junction of the
    k-point segments either side, onto a rising segment, where either

    * both segments rise with slopes within PARALLEL_SLOPE_RATIO (the tracer
      crossed from one post-plateau branch to its parallel neighbour), or
    * the junction is after the plateau and has no x gap: a continuous curve
      cannot jump, whatever the slope before it (syn1_0213: the tracer rode
      the dashed plateau of a higher VDD past its own knee, then lifted onto
      the solid lower-VDD branch through a dash gap). A real knee is
      continuous; its two fits meet at the corner.

    Both k-point sides must be straight (max residual <= 0.8 % of the height):
    a side that contains the knee is not a segment and proves nothing.
    """

    hop = max(6.0, HOP_FRACTION * height)
    straight = max(2.5, 0.008 * height)
    plateau_x = _plateau_x([(int(x), int(y)) for x, y in curve], width, height)
    contiguous = max(6.0, 0.03 * width)
    for index in range(k, len(curve) - k + 1):
        before, after = curve[index - k : index], curve[index : index + k]
        gap = after[0, 0] - before[-1, 0]
        post_plateau = plateau_x is not None and before[0, 0] > plateau_x
        # a step across a wide gap before the plateau is the shared ramp/plateau
        # seen through an erased label, not a hop; a step with no gap is a hop
        if gap > contiguous and not post_plateau:
            continue
        fa, fb = _fit(before), _fit(after)
        if fa is None or fb is None or fb[0] > -MIN_RISE_SLOPE:
            continue
        # each side must be ONE straight stroke: a window straddling a sharp
        # knee (piecewise-linear vector charts, IRLB4030) fits a line that
        # misses the corner and fakes an offset
        if max(fa[2], fb[2]) > straight:
            continue
        (ma, ba, _ra), (mb, bb, _rb) = fa, fb
        xm = 0.5 * (before[-1, 0] + after[0, 0])
        ya, yb = ma * xm + ba, mb * xm + bb
        if abs(yb - ya) <= hop:
            continue
        if ma <= -MIN_RISE_SLOPE and abs(ma - mb) <= PARALLEL_SLOPE_RATIO * max(abs(ma), abs(mb)):
            return index
        if post_plateau and gap <= contiguous:
            return index
    return None


def cut_at_branch_hop(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> tuple[list[tuple[int, int]], bool]:
    """Keep the curve up to its first hop onto another branch.

    Returns (curve, cut_short). A trailing fragment of fewer than 4 points that
    sits beyond an x gap of more than 8 % of the width is not a continuation
    either (a gridline or tick stub the trace reached after its branch ended).
    ``cut_short`` is True when something was removed and the kept curve no
    longer rises to MIN_KEPT_RISE_FRACTION of the plot height, or no longer
    rises MIN_POST_PLATEAU_RISE_FRACTION above the plateau: a single branch
    that stops that early is not the chart's curve, and the caller must not
    serve it as "ok". Too few points to judge are returned unchanged and not
    flagged here (the caller's point-count guard owns that case).
    """

    if len(curve) < 10:
        return curve, False
    x0, y0, x1, y1 = plot_box
    width, height = max(1, x1 - x0), max(1, y1 - y0)
    kept = sorted(curve)
    gap = 0.08 * width
    for index in range(max(1, len(kept) - 3), len(kept)):
        if kept[index][0] - kept[index - 1][0] > gap:
            kept = kept[:index]
            break
    hop = _first_branch_hop(np.array(kept, float), width, height)
    if hop is not None:
        kept = kept[:hop]
    if len(kept) == len(curve):
        return curve, False
    top = min(y for _x, y in kept)
    plateau_x = _plateau_x(kept, width, height)
    plateau_y = None if plateau_x is None else min(
        (abs(x - plateau_x), y) for x, y in kept
    )[1]
    no_rise = plateau_y is not None and plateau_y - top < MIN_POST_PLATEAU_RISE_FRACTION * height
    return kept, no_rise or (y1 - top) < MIN_KEPT_RISE_FRACTION * height
