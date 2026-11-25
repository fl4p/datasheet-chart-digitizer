"""Leading-segment repairs of raster C(V) traces and their shape guards.

Moved verbatim out of ``capacitance_traces`` (which had grown past the
1500-line limit); ``capacitance_traces`` re-exports every name it used.
"""

from __future__ import annotations

import numpy as np

from .capacitance_types import PlotBox


def _interp_y(points: list[tuple[int, int]], x: int) -> float:
    ordered = sorted(points)
    if x <= ordered[0][0]:
        return float(ordered[0][1])
    if x >= ordered[-1][0]:
        return float(ordered[-1][1])
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return float(y0)
            t = (x - x0) / (x1 - x0)
            return float(y0 + (y1 - y0) * t)
    return float(ordered[-1][1])


def _cluster_row_runs(row: np.ndarray) -> list[float]:
    xs = np.where(row > 0)[0]
    if len(xs) == 0:
        return []

    centers: list[float] = []
    start = int(xs[0])
    prev = int(xs[0])
    for x_raw in xs[1:]:
        x = int(x_raw)
        if x == prev + 1:
            prev = x
            continue
        if prev - start + 1 <= 18:
            centers.append((start + prev) / 2)
        start = x
        prev = x
    if prev - start + 1 <= 18:
        centers.append((start + prev) / 2)

    if not centers:
        return []
    clustered: list[list[float]] = []
    for center in sorted(centers):
        if clustered and center - clustered[-1][-1] <= 10:
            clustered[-1].append(center)
        else:
            clustered.append([center])
    return [float(np.median(group)) for group in clustered]


def _repair_missing_leading_knee(
    mask: np.ndarray, points: list[tuple[int, int]], plot: PlotBox
) -> list[tuple[int, int]] | None:
    """Prepend a near-vertical left-edge knee as one y per x.

    This handles raster Coss traces on high-voltage SiC charts where the first
    tracked column is already below the nearly vertical low-VDS rise. The row
    walk recovers the steep segment; grouping by x with a median center keeps
    the exported curve single-valued.
    """
    if len(points) < 8:
        return None
    ordered = sorted(points)
    anchor = ordered[0]
    anchor_x = anchor[0] - plot.x0
    anchor_y = anchor[1] - plot.y0
    if anchor_x > plot.width * 0.16 or anchor_y < plot.height * 0.10:
        return None

    max_band = int(round(plot.height * 0.45))
    y_min = max(0, anchor_y - max_band)
    left_limit = max(anchor_x + 18.0, plot.width * 0.07)
    row_points: list[tuple[int, int]] = []
    last_x: float | None = None
    for local_y in range(anchor_y - 1, y_min - 1, -1):
        row_centers = _cluster_row_runs(mask[local_y, :])
        candidates = [x for x in row_centers if 0 <= x <= left_limit]
        if not candidates:
            continue
        if last_x is None:
            best = min(candidates, key=lambda x: abs(x - anchor_x))
        else:
            monotone_candidates = [x for x in candidates if x <= last_x + 4.0]
            if not monotone_candidates:
                continue
            best = min(monotone_candidates, key=lambda x: abs(x - last_x))
            if abs(best - last_x) > 12.0:
                continue
        last_x = float(best)
        row_points.append((plot.x0 + int(round(best)), plot.y0 + local_y))

    if len(row_points) < 10:
        return None

    by_x: dict[int, list[int]] = {}
    for x, y in row_points + [anchor]:
        by_x.setdefault(x, []).append(y)
    repaired: list[tuple[int, int]] = []
    running_y: int | None = None
    for x in sorted(by_x):
        # Use the upper envelope for Coss: this repair only covers the missing
        # high-capacitance left-edge knee, and the visible stroke top is the
        # conservative continuation toward VDS=0.
        y = int(min(by_x[x]))
        if running_y is not None and y < running_y:
            y = running_y
        running_y = y
        repaired.append((x, y))
    if len(repaired) < 2:
        return None

    x_cover_min = min(x for x, _ in repaired)
    x_cover_max = max(x for x, _ in repaired)
    merged = {x: y for x, y in ordered if not (x_cover_min <= x <= x_cover_max)}
    for x, y in repaired:
        merged[x] = y
    return sorted(merged.items())


def _repair_leading_steep_crss(
    mask: np.ndarray,
    assigned: dict[str, list[tuple[int, int]]],
    plot: PlotBox,
) -> dict[str, list[tuple[int, int]]]:
    """Recover the near-vertical low-VDS Crss knee in raster charts.

    Column sampling deliberately ignores tall runs so grid lines and merged
    strokes do not collapse multiple traces into one center. That also drops
    the left-edge Crss knee on SiC capacitance plots, where the trace is almost
    vertical. Repair that local segment by sampling row runs near the left edge
    and splicing them before the first column-tracked Crss point.
    """
    crss = assigned.get("Crss")
    if not crss or len(crss) < 8:
        return assigned

    crss_by_path = list(crss)
    left_candidates = [point for point in crss_by_path if point[0] - plot.x0 <= plot.width * 0.16]
    if not left_candidates:
        return assigned
    anchor = min(left_candidates, key=lambda point: (point[0], point[1]))
    anchor_x = anchor[0] - plot.x0
    anchor_y = anchor[1] - plot.y0
    if anchor_x > plot.width * 0.16 or anchor_y < plot.height * 0.25:
        return assigned

    # Only repair the local missing knee. Extending too far upward can steal the
    # overlapping Coss/Ciss low-VDS rise, so cap the search to a modest vertical
    # band above the first stable Crss point.
    max_band = int(round(plot.height * 0.45))
    y_min = max(0, anchor_y - max_band)
    left_limit = max(anchor_x + 18.0, plot.width * 0.07)

    row_points: list[tuple[int, int]] = []
    last_x: float | None = None
    for local_y in range(anchor_y - 1, y_min - 1, -1):
        row_centers = _cluster_row_runs(mask[local_y, :])
        candidates = [x for x in row_centers if 0 <= x <= left_limit]
        if not candidates:
            continue
        if last_x is None:
            monotone_candidates = [x for x in candidates if x <= anchor_x + 4.0]
            if not monotone_candidates:
                continue
            best = min(monotone_candidates, key=lambda x: abs(x - anchor_x))
        else:
            # Crss is a single-valued decreasing C(VDS) curve. Walking toward
            # higher capacitance (smaller pixel-y) must not move the trace to
            # larger VDS, otherwise we are following a different left-edge
            # branch and will create a loop in the overlay/data.
            monotone_candidates = [x for x in candidates if x <= last_x + 4.0]
            if not monotone_candidates:
                continue
            best = min(monotone_candidates, key=lambda x: abs(x - last_x))
            if abs(best - last_x) > 12.0:
                continue
        last_x = float(best)
        row_points.append((plot.x0 + int(round(best)), plot.y0 + local_y))

    if len(row_points) < 12:
        return assigned

    # Convert the row-walk back to a function y(x). Raster strokes can be nearly
    # vertical, but the digitized C(V) curve must still have one capacitance per
    # VDS. Use the upper envelope for each x, then enforce nondecreasing y as x
    # increases so the repaired Crss knee cannot fold back on itself.
    by_x: dict[int, int] = {}
    for x, y in row_points + [anchor]:
        old = by_x.get(x)
        if old is None or y < old:
            by_x[x] = y
    repaired: list[tuple[int, int]] = []
    running_y: int | None = None
    for x in sorted(by_x):
        y = by_x[x]
        if running_y is not None and y < running_y:
            y = running_y
        running_y = y
        repaired.append((x, y))
    if len(repaired) < 2:
        return assigned

    x_cover_min = min(x for x, _ in repaired)
    x_cover_max = max(x for x, _ in repaired)
    remainder = [
        point
        for point in crss_by_path
        if not (
            x_cover_min <= point[0] <= x_cover_max
            and point != anchor
        )
    ]

    out = dict(assigned)
    merged_by_x = {x: y for x, y in remainder}
    for x, y in repaired:
        merged_by_x[x] = y
    repaired_crss = sorted(merged_by_x.items())
    peers = {name: points for name, points in assigned.items() if name in ("Ciss", "Coss")}
    if not _repair_shape_guard(repaired_crss, crss_by_path, plot, peers=peers, require_bottom=True):
        return assigned
    out["Crss"] = repaired_crss
    return out


def _repair_shape_guard(
    repaired: list[tuple[int, int]],
    original: list[tuple[int, int]],
    plot: PlotBox,
    *,
    peers: dict[str, list[tuple[int, int]]] | None = None,
    require_bottom: bool = False,
) -> bool:
    if not repaired or not _single_valued_by_x(repaired):
        return False
    if not _low_v_nonfolding(repaired, plot):
        return False
    if not _splice_continuity_ok(repaired, original, plot):
        return False
    repair_segment = _changed_repair_segment(repaired, original) or repaired
    if peers and _overlaps_peer_for_too_long(repair_segment, peers):
        return False
    if require_bottom and peers and not _is_bottom_branch(repair_segment, peers):
        return False
    return True


def _single_valued_by_x(points: list[tuple[int, int]]) -> bool:
    return len({x for x, _ in points}) == len(points)


def _low_v_nonfolding(points: list[tuple[int, int]], plot: PlotBox) -> bool:
    low_v_limit = plot.x0 + int(round(plot.width * 0.20))
    low_v_points = [(x, y) for x, y in sorted(points) if x <= low_v_limit]
    if len(low_v_points) < 3:
        return True
    ys = np.array([y for _, y in low_v_points], dtype=float)
    return bool(np.all(np.diff(ys) >= -3.0))


def _splice_continuity_ok(
    repaired: list[tuple[int, int]], original: list[tuple[int, int]], plot: PlotBox
) -> bool:
    changed_runs = _changed_repair_runs(repaired, original)
    if not changed_runs:
        return True
    repaired_sorted = sorted(repaired)
    for changed in changed_runs:
        changed_min_x = min(x for x, _ in changed)
        changed_max_x = max(x for x, _ in changed)
        first_changed = min(changed, key=lambda point: point[0])
        last_changed = max(changed, key=lambda point: point[0])
        prev_tail = [point for point in repaired_sorted if point[0] < changed_min_x]
        next_tail = [point for point in repaired_sorted if point[0] > changed_max_x]
        if prev_tail and not _splice_pair_continuous(prev_tail[-1], first_changed, plot):
            return False
        if next_tail and not _splice_pair_continuous(last_changed, next_tail[0], plot):
            return False
    return True


def _changed_repair_segment(
    repaired: list[tuple[int, int]], original: list[tuple[int, int]], y_tol: int = 3
) -> list[tuple[int, int]]:
    original_by_x = dict(original)
    return [(x, y) for x, y in sorted(repaired) if x not in original_by_x or abs(y - original_by_x[x]) > y_tol]


def _changed_repair_runs(
    repaired: list[tuple[int, int]], original: list[tuple[int, int]], y_tol: int = 3
) -> list[list[tuple[int, int]]]:
    changed = _changed_repair_segment(repaired, original, y_tol=y_tol)
    if not changed:
        return []
    runs: list[list[tuple[int, int]]] = [[changed[0]]]
    for point in changed[1:]:
        if point[0] <= runs[-1][-1][0] + 1:
            runs[-1].append(point)
        else:
            runs.append([point])
    return runs


def _splice_pair_continuous(a: tuple[int, int], b: tuple[int, int], plot: PlotBox) -> bool:
    dx = b[0] - a[0]
    dy = abs(b[1] - a[1])
    return 0 < dx <= max(8, plot.width * 0.04) and dy <= max(24, plot.height * 0.08)


def _overlaps_peer_for_too_long(
    points: list[tuple[int, int]], peers: dict[str, list[tuple[int, int]]]
) -> bool:
    shared = 0
    close = 0
    for x, y in points:
        for peer_points in peers.values():
            peer_y = _interp_y_in_range(peer_points, x)
            if peer_y is None:
                continue
            shared += 1
            if abs(y - peer_y) <= 4:
                close += 1
    return shared >= 6 and close / shared > 0.45


def _is_bottom_branch(points: list[tuple[int, int]], peers: dict[str, list[tuple[int, int]]]) -> bool:
    samples = 0
    bottom = 0
    for x, y in points:
        peer_ys = [
            peer_y
            for peer_points in peers.values()
            for peer_y in [_interp_y_in_range(peer_points, x)]
            if peer_y is not None
        ]
        if not peer_ys:
            continue
        samples += 1
        if y >= max(peer_ys) - 8:
            bottom += 1
    return samples < 4 or bottom / samples >= 0.80


def _interp_y_in_range(
    points: list[tuple[int, int]],
    x: int,
    max_gap: int | None = None,
) -> float | None:
    if not points:
        return None
    ordered = sorted(points)
    if x < ordered[0][0] or x > ordered[-1][0]:
        return None
    if max_gap is not None:
        for (x0, _), (x1, _) in zip(ordered, ordered[1:]):
            if x0 <= x <= x1:
                if x not in (x0, x1) and x1 - x0 > max_gap:
                    return None
                break
    return _interp_y(ordered, x)
