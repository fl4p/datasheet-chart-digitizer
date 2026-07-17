"""Review-grade extraction of one or more MOSFET transfer curves.

This module is intentionally separate from :mod:`transfer_characteristics`.
The latter has strict, model-fitting semantics for a particular two-curve
Infineon chart.  Here we digitize an arbitrary number of monotone ``Id(Vgs)``
curves, including charts with a logarithmic current axis, without assigning a
compact-model meaning to them.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

import cv2
import numpy as np

from .capacitance_types import PlotBox


@dataclass(frozen=True)
class TransferAxis:
    vgs_min_v: float
    vgs_max_v: float
    id_min_a: float
    id_max_a: float
    id_scale: str = "linear"

    def __post_init__(self) -> None:
        if self.vgs_max_v <= self.vgs_min_v:
            raise ValueError("Vgs axis must increase")
        if self.id_max_a <= self.id_min_a:
            raise ValueError("Id axis must increase")
        if self.id_scale not in {"linear", "log10"}:
            raise ValueError(f"unsupported Id scale: {self.id_scale}")
        if self.id_scale == "log10" and self.id_min_a <= 0:
            raise ValueError("log10 Id axis requires a positive minimum")


@dataclass(frozen=True)
class ReviewTrace:
    pixels: list[tuple[int, int]]
    points: list[tuple[float, float]]
    y_span_fraction: float
    monotone_violation_fraction: float
    maximum_source_gap_fraction: float = 0.0


def review_trace_from_pixels(
    pixels: list[tuple[int, int]], plot: PlotBox, axis: TransferAxis
) -> ReviewTrace:
    """Build review diagnostics and calibrated points for a pixel trace."""
    by_y: dict[int, list[int]] = {}
    for x, y in pixels:
        by_y.setdefault(y, []).append(x)
    source_y = sorted(by_y)
    if not source_y:
        raise ValueError("cannot build an empty review trace")
    source_x = [float(np.mean(by_y[y])) for y in source_y]
    dense_y = np.arange(source_y[0], source_y[-1] + 1)
    dense_x = np.interp(dense_y, source_y, source_x)
    if len(dense_x) >= 9:
        half_window = 4
        padded = np.pad(dense_x, (half_window, half_window), mode="edge")
        dense_x = np.convolve(padded, np.ones(9) / 9.0, mode="valid")
    ordered = [
        (int(round(x)), int(y)) for x, y in zip(dense_x, dense_y, strict=True)
    ]
    local_x = np.array([x - plot.x0 for x, _y in ordered], dtype=float)
    local_y = np.array([y - plot.y0 for _x, y in ordered], dtype=float)
    dx = np.diff(local_x)
    source_gaps = np.diff(source_y)
    return ReviewTrace(
        pixels=ordered,
        points=calibrate_pixels(ordered, plot, axis),
        y_span_fraction=float(
            (local_y.max() - local_y.min()) / max(1, plot.height - 1)
        ),
        monotone_violation_fraction=float(np.mean(dx > 2.5)) if len(dx) else 1.0,
        maximum_source_gap_fraction=(
            float(source_gaps.max() / max(1, plot.height - 1))
            if len(source_gaps)
            else 0.0
        ),
    )


def exchange_two_trace_identities_below(
    traces: list[ReviewTrace],
    split_y: int,
    plot: PlotBox,
    axis: TransferAxis,
) -> list[ReviewTrace]:
    """Continue two physical curve identities through a visible crossing.

    Ordered multi-curve tracking labels left/right branches rather than their
    physical identities. At a known crossing, exchange the lower portions so
    each returned trace follows one physical curve.
    """
    if len(traces) != 2:
        raise ValueError("identity exchange requires exactly two traces")
    first, second = traces
    exchanged = [
        review_trace_from_pixels(
            [point for point in first.pixels if point[1] <= split_y]
            + [point for point in second.pixels if point[1] > split_y],
            plot,
            axis,
        ),
        review_trace_from_pixels(
            [point for point in second.pixels if point[1] <= split_y]
            + [point for point in first.pixels if point[1] > split_y],
            plot,
            axis,
        ),
    ]
    source_gap = max(trace.maximum_source_gap_fraction for trace in traces)
    return [
        replace(trace, maximum_source_gap_fraction=source_gap)
        for trace in exchanged
    ]


def maximum_pairwise_collapse_fraction(
    traces: list[ReviewTrace], tolerance_px: float = 1.5
) -> float:
    """Return the largest interpolated span fraction occupied by two traces."""
    maximum = 0.0
    for first, second in itertools.combinations(traces, 2):
        first_x = {y: x for x, y in first.pixels}
        second_x = {y: x for x, y in second.pixels}
        first_y = sorted(first_x)
        second_y = sorted(second_x)
        if not first_y or not second_y:
            continue
        lo = max(first_y[0], second_y[0])
        hi = min(first_y[-1], second_y[-1])
        if hi < lo:
            continue
        rows = np.arange(lo, hi + 1, dtype=float)
        first_interp = np.interp(rows, first_y, [first_x[y] for y in first_y])
        second_interp = np.interp(rows, second_y, [second_x[y] for y in second_y])
        collapsed = float(
            np.mean(np.abs(first_interp - second_interp) <= tolerance_px)
        )
        maximum = max(maximum, collapsed)
    return maximum


@dataclass(frozen=True)
class CollapseSpan:
    """One sustained merge between two traces, in image rows."""

    first_index: int
    second_index: int
    y_lo: int
    y_hi: int
    reorders_after: bool


def sustained_collapse_spans(
    traces: list[ReviewTrace],
    tolerance_px: float = 1.5,
    min_run_rows: int = 12,
) -> tuple[list[CollapseSpan], list[set[int]]]:
    """Locate sustained merges and the rows they occupy on EVERY trace.

    A pair of traces is *collapsed* over a maximal run of consecutive rows
    where their interpolated centerlines sit within ``tolerance_px`` (about
    one stroke width) — provided the run is sustained (``min_run_rows`` or
    longer).  A transient ZTC-style crossing also touches zero spread, but
    the curves re-diverge immediately, so its run stays short and is NOT
    reported; those rows keep their independent identities.

    Collapsed rows are returned for BOTH traces of a pair, including any
    retained "primary" one: within a merge the extracted centerline is the
    shared stroke, not either temperature's true value, so neither side may
    present those points as measured evidence.
    """
    spans: list[CollapseSpan] = []
    collapsed_rows: list[set[int]] = [set() for _ in traces]
    for (first_index, first), (second_index, second) in itertools.combinations(
        enumerate(traces), 2
    ):
        first_x = {y: x for x, y in first.pixels}
        second_x = {y: x for x, y in second.pixels}
        first_y = sorted(first_x)
        second_y = sorted(second_x)
        if not first_y or not second_y:
            continue
        lo = max(first_y[0], second_y[0])
        hi = min(first_y[-1], second_y[-1])
        if hi < lo:
            continue
        rows = np.arange(lo, hi + 1, dtype=float)
        delta = np.interp(rows, first_y, [first_x[y] for y in first_y]) - np.interp(
            rows, second_y, [second_x[y] for y in second_y]
        )
        within = np.abs(delta) <= tolerance_px
        indexes = np.flatnonzero(within)
        if not len(indexes):
            continue
        for run in np.split(indexes, np.flatnonzero(np.diff(indexes) > 1) + 1):
            if len(run) < min_run_rows:
                continue
            run_lo = int(rows[run[0]])
            run_hi = int(rows[run[-1]])
            before = delta[run[0] - 1] if run[0] > 0 else 0.0
            after = delta[run[-1] + 1] if run[-1] + 1 < len(delta) else 0.0
            spans.append(
                CollapseSpan(
                    first_index=first_index,
                    second_index=second_index,
                    y_lo=run_lo,
                    y_hi=run_hi,
                    reorders_after=bool(before * after < 0),
                )
            )
            marked = set(range(run_lo, run_hi + 1))
            collapsed_rows[first_index] |= marked
            collapsed_rows[second_index] |= marked
    return spans, collapsed_rows


def calibrate_pixels(
    pixels: list[tuple[int, int]], plot: PlotBox, axis: TransferAxis
) -> list[tuple[float, float]]:
    """Convert full-image pixel coordinates to ``(Vgs [V], Id [A])``."""
    out: list[tuple[float, float]] = []
    for x, y in sorted(pixels, key=lambda point: point[1], reverse=True):
        fx = np.clip((x - plot.x0) / max(1, plot.x1 - plot.x0), 0.0, 1.0)
        fy = np.clip((plot.y1 - y) / max(1, plot.y1 - plot.y0), 0.0, 1.0)
        vgs = axis.vgs_min_v + float(fx) * (axis.vgs_max_v - axis.vgs_min_v)
        if axis.id_scale == "log10":
            lo = np.log10(axis.id_min_a)
            hi = np.log10(axis.id_max_a)
            current = 10.0 ** (lo + float(fy) * (hi - lo))
        else:
            current = axis.id_min_a + float(fy) * (axis.id_max_a - axis.id_min_a)
        out.append((vgs, current))
    return out


def transfer_ink_mask(
    rgb: np.ndarray,
    plot: PlotBox,
    erase_boxes: list[tuple[int, int, int, int]] | None = None,
) -> np.ndarray:
    """Return plot-local ink with long grid lines and supplied text removed."""
    roi = rgb[plot.y0 : plot.y1 + 1, plot.x0 : plot.x1 + 1]
    if roi.size == 0:
        raise ValueError("empty transfer plot ROI")
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
    dark = gray < 135
    colored = (hsv[:, :, 1] > 45) & (hsv[:, :, 2] < 252)
    mask = ((dark | colored).astype(np.uint8)) * 255

    height, width = mask.shape
    horizontal = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(35, width // 5), 1)),
    )
    vertical = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(35, height // 2))),
    )
    mask &= cv2.bitwise_not(horizontal | vertical)

    for x0, y0, x1, y1 in erase_boxes or []:
        lx0 = max(0, x0 - plot.x0 - 3)
        ly0 = max(0, y0 - plot.y0 - 3)
        lx1 = min(width - 1, x1 - plot.x0 + 3)
        ly1 = min(height - 1, y1 - plot.y0 + 3)
        if lx0 <= lx1 and ly0 <= ly1:
            mask[ly0 : ly1 + 1, lx0 : lx1 + 1] = 0

    row_fraction = (mask > 0).sum(axis=1) / max(1, width)
    col_fraction = (mask > 0).sum(axis=0) / max(1, height)
    mask[row_fraction > 0.45, :] = 0
    mask[:, col_fraction > 0.55] = 0
    return cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )


def _run_centers(values: np.ndarray, split_wide_count: int = 0) -> list[float]:
    indexes = np.flatnonzero(values)
    if not len(indexes):
        return []
    split = np.flatnonzero(np.diff(indexes) > 1) + 1
    centers: list[float] = []
    for run in np.split(indexes, split):
        if not len(run):
            continue
        if split_wide_count > 1 and len(run) >= 5:
            # Close thick strokes often rasterize as one connected run. Keep
            # their stroke-envelope positions available to the ordered group
            # tracker instead of reducing the run to one false centerline.
            inset = min(1.0, 0.2 * (len(run) - 1))
            centers.extend(
                float(value)
                for value in np.linspace(
                    run[0] + inset, run[-1] - inset, split_wide_count
                )
            )
        else:
            centers.append(float(np.mean(run)))
    return sorted(set(centers))


def _row_centers(mask: np.ndarray, split_wide_count: int = 0) -> list[list[float]]:
    return [
        _run_centers(mask[y] > 0, split_wide_count) for y in range(mask.shape[0])
    ]


def _track_direction(
    centers_by_y: list[list[float]],
    seed_y: int,
    seed_x: float,
    direction: int,
    width: int,
    max_gap_fraction: float,
) -> list[tuple[int, float]]:
    points = [(seed_y, seed_x)]
    misses = 0
    # Temperature callouts often occlude 10–15% of a curve.  Preserve the
    # predicted branch across that gap, but still refuse after a fifth of the
    # plot height rather than latching onto unrelated ink.
    max_misses = max(16, int(len(centers_by_y) * max_gap_fraction))
    max_jump = max(7.0, 0.035 * width)
    for y in range(seed_y + direction, len(centers_by_y) if direction > 0 else -1, direction):
        history = points[-8:]
        if len(history) >= 3:
            hs_y = np.array([p[0] for p in history], dtype=float)
            hs_x = np.array([p[1] for p in history], dtype=float)
            prediction = float(np.polyval(np.polyfit(hs_y, hs_x, 1), y))
        else:
            prediction = points[-1][1]
        choices = centers_by_y[y]
        if not choices:
            misses += 1
            if misses > max_misses:
                break
            continue
        scored: list[tuple[float, float]] = []
        for x in choices:
            delta = x - prediction
            # Vgs normally increases with Id: moving down the image should not
            # move substantially right, and moving up should not move left.
            wrong_way = max(0.0, delta * direction)
            score = abs(delta) + 2.5 * wrong_way
            scored.append((score, x))
        score, chosen = min(scored)
        tolerance = max_jump + 0.7 * misses
        if score > tolerance:
            misses += 1
            if misses > max_misses:
                break
            continue
        points.append((y, chosen))
        misses = 0
    return points


def _trace_from_seed(
    centers_by_y: list[list[float]],
    seed_y: int,
    seed_x: float,
    width: int,
    max_gap_fraction: float,
) -> list[tuple[int, int]]:
    upward = _track_direction(centers_by_y, seed_y, seed_x, -1, width, max_gap_fraction)
    downward = _track_direction(centers_by_y, seed_y, seed_x, 1, width, max_gap_fraction)
    merged = {y: x for y, x in reversed(upward)}
    merged.update({y: x for y, x in downward})
    return [(int(round(x)), y) for y, x in sorted(merged.items())]


def _track_group_direction(
    centers_by_y: list[list[float]],
    seed_y: int,
    seed_xs: list[float],
    direction: int,
    width: int,
    max_gap_fraction: float,
) -> list[list[tuple[int, float]]]:
    histories = [[(seed_y, x)] for x in sorted(seed_xs)]
    misses = 0
    max_misses = max(16, int(len(centers_by_y) * max_gap_fraction))
    base_tolerance = max(7.0, 0.035 * width)
    for y in range(seed_y + direction, len(centers_by_y) if direction > 0 else -1, direction):
        predictions = []
        for history in histories:
            tail = history[-8:]
            if len(tail) >= 3:
                hy = np.array([point[0] for point in tail], dtype=float)
                hx = np.array([point[1] for point in tail], dtype=float)
                predictions.append(float(np.polyval(np.polyfit(hy, hx, 1), y)))
            else:
                predictions.append(history[-1][1])
        tolerance = base_tolerance + 0.35 * misses
        nearby = [
            x
            for x in centers_by_y[y]
            if min(predictions) - tolerance <= x <= max(predictions) + tolerance
        ]
        best: tuple[float, tuple[float, ...]] | None = None
        for choices in itertools.combinations(nearby, len(histories)):
            deltas = [choice - prediction for choice, prediction in zip(choices, predictions)]
            if any(abs(delta) > tolerance for delta in deltas):
                continue
            wrong_way = sum(max(0.0, delta * direction) for delta in deltas)
            score = sum(abs(delta) for delta in deltas) + 2.0 * wrong_way
            if best is None or score < best[0]:
                best = (score, choices)
        if best is None:
            misses += 1
            if misses > max_misses:
                break
            continue
        for history, x in zip(histories, best[1]):
            history.append((y, x))
        misses = 0
    return histories


def _trace_seed_group(
    centers_by_y: list[list[float]],
    seed_y: int,
    seed_xs: list[float],
    width: int,
    max_gap_fraction: float,
) -> list[list[tuple[int, int]]]:
    upward = _track_group_direction(
        centers_by_y, seed_y, seed_xs, -1, width, max_gap_fraction
    )
    downward = _track_group_direction(
        centers_by_y, seed_y, seed_xs, 1, width, max_gap_fraction
    )
    traces = []
    for upper, lower in zip(upward, downward):
        merged = {y: x for y, x in reversed(upper)}
        merged.update({y: x for y, x in lower})
        traces.append([(int(round(x)), y) for y, x in sorted(merged.items())])
    return traces


def _candidate_score(
    points: list[tuple[int, int]], height: int, width: int, min_span_fraction: float = 0.18
) -> float:
    if len(points) < max(3, int(min_span_fraction * height)):
        return -1e9
    ordered = sorted(points, key=lambda point: point[1])
    xs = np.array([p[0] for p in ordered], dtype=float)
    ys = np.array([p[1] for p in ordered], dtype=float)
    y_span = float((ys.max() - ys.min()) / max(1, height - 1))
    x_span = float((xs.max() - xs.min()) / max(1, width - 1))
    dx = np.diff(xs)
    violation = float(np.mean(dx > 2.5)) if len(dx) else 1.0
    roughness = float(np.median(np.abs(np.diff(dx)))) if len(dx) > 2 else 0.0
    return 12.0 * y_span + 3.0 * min(0.4, x_span) - 5.0 * violation - 0.04 * roughness


def _same_trace(a: list[tuple[int, int]], b: list[tuple[int, int]], width: int) -> bool:
    ax = {y: x for x, y in a}
    bx = {y: x for x, y in b}
    common = sorted(set(ax) & set(bx))
    if len(common) < 20:
        return False
    distance = np.median([abs(ax[y] - bx[y]) for y in common])
    return bool(distance < max(5.0, 0.018 * width))


def _duplicate_trace(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    """Detect genuinely duplicated seeded paths without rejecting close curves."""
    ax = {y: x for x, y in a}
    bx = {y: x for x, y in b}
    common = sorted(set(ax) & set(bx))
    if len(common) < 20:
        return False
    distances = np.array([abs(ax[y] - bx[y]) for y in common], dtype=float)
    return bool(np.median(distances) < 1.5 and np.quantile(distances, 0.9) < 3.0)


def extract_transfer_traces(
    rgb: np.ndarray,
    plot: PlotBox,
    axis: TransferAxis,
    count: int,
    erase_boxes: list[tuple[int, int, int, int]] | None = None,
    seed_pixels: list[tuple[int, int]] | None = None,
    max_gap_fraction: float = 0.2,
    min_span_fraction: float = 0.18,
    grouped_seeded: bool = False,
    max_monotone_violation_fraction: float = 0.05,
) -> list[ReviewTrace]:
    """Extract ``count`` curve centerlines or fail instead of inventing curves."""
    if count < 1:
        raise ValueError("curve count must be positive")
    mask = transfer_ink_mask(rgb, plot, erase_boxes)
    height, width = mask.shape
    centers_by_y = _row_centers(mask, count if grouped_seeded else 0)
    selected: list[list[tuple[int, int]]] = []
    if seed_pixels is not None:
        if len(seed_pixels) != count:
            raise ValueError(f"received {len(seed_pixels)} seeds for {count} curves")
        local_seeds = [(float(x - plot.x0), int(y - plot.y0)) for x, y in seed_pixels]
        if len({y for _x, y in local_seeds}) != 1 and grouped_seeded:
            raise ValueError("grouped curve seeds must share one image row")
        for local_x, local_y in local_seeds:
            if not (0 <= local_y < height and 0 <= local_x < width):
                raise ValueError("curve seed lies outside plot")
        seeded = (
            _trace_seed_group(
                centers_by_y,
                local_seeds[0][1],
                [x for x, _y in local_seeds],
                width,
                max_gap_fraction,
            )
            if grouped_seeded
            else [
                _trace_from_seed(centers_by_y, y, x, width, max_gap_fraction)
                for x, y in local_seeds
            ]
        )
        for seed, points in zip(seed_pixels, seeded):
            if _candidate_score(points, height, width, min_span_fraction) <= -1e8:
                raise RuntimeError(f"curve seed {seed} did not produce a credible trace")
            selected.append(points)
    else:
        candidates: list[tuple[float, list[tuple[int, int]]]] = []
        seed_rows = np.linspace(int(0.18 * height), int(0.82 * height), 13).astype(int)
        for seed_y in seed_rows:
            for seed_x in centers_by_y[seed_y]:
                if not 0.12 * width <= seed_x <= 0.96 * width:
                    continue
                points = _trace_from_seed(
                    centers_by_y, seed_y, seed_x, width, max_gap_fraction
                )
                score = _candidate_score(points, height, width, min_span_fraction)
                if score > -1e8:
                    candidates.append((score, points))
        candidates.sort(key=lambda item: item[0], reverse=True)
        for _score, points in candidates:
            if any(_same_trace(points, prior, width) for prior in selected):
                continue
            selected.append(points)
            if len(selected) == count:
                break
    if len(selected) != count:
        raise RuntimeError(f"found {len(selected)} credible transfer curves, expected {count}")
    for index, points in enumerate(selected):
        if any(_duplicate_trace(points, prior) for prior in selected[:index]):
            raise RuntimeError("seeded extraction collapsed two requested branches onto one trace")

    traces: list[ReviewTrace] = []
    for local in selected:
        full = [(plot.x0 + x, plot.y0 + y) for x, y in local]
        traces.append(review_trace_from_pixels(full, plot, axis))
    for trace in traces:
        if trace.monotone_violation_fraction > max_monotone_violation_fraction:
            raise RuntimeError(
                "transfer trace violates monotone Id(Vgs) in "
                f"{trace.monotone_violation_fraction:.1%} of sampled rows"
            )
    if grouped_seeded:
        for left, right in zip(traces, traces[1:]):
            left_x = {y: x for x, y in left.pixels}
            right_x = {y: x for x, y in right.pixels}
            common = sorted(set(left_x) & set(right_x))
            if len(common) < 20:
                continue
            collapsed = float(np.mean([right_x[y] - left_x[y] <= 1 for y in common]))
            if collapsed > 0.15:
                raise RuntimeError(
                    f"adjacent seeded branches collapse for {collapsed:.1%} of common rows"
                )
    traces.sort(key=lambda trace: np.median([x for x, _y in trace.pixels]))
    return traces
