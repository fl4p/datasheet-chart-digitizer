"""Vector-stroke helpers for body-diode curve extraction.

Stroke filtering, source-contiguous stroke merging and the unambiguous
end-to-end joining of dashed/segmented vector paths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pymupdf

from .capacitance_vector import _chain_vector_components, _vector_curve_edges

_VECTOR_JOIN_MAX_GAP_PT = 3.0
_VECTOR_JOIN_MAX_BACKTRACK_PT = 0.75
_VECTOR_JOIN_SEPARATION_FRACTION = 0.45
_VECTOR_JOIN_WIDTH_ABS_TOLERANCE_PT = 0.12
_VECTOR_JOIN_WIDTH_REL_TOLERANCE = 0.15
_SOURCE_BOUND_EXIT_MIN_X_SPAN = 0.50
# capacitance_vector._vector_curve_edges default stroke-width ceiling
_VECTOR_MAX_STROKE_PT = 2.2


@dataclass
class _JoinedVectorPath:
    points: list[tuple[float, float]]
    dash_pattern: tuple[float, ...]
    stroke_widths: tuple[float, ...]
    source_indices: tuple[int, ...]


def _stroke_endpoints(
    drawing: dict, *, min_segments: int = 2
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    items = drawing.get("items") or []
    # One-segment-per-drawing producers (Infineon IPP024N08NF2S) are already
    # chained by the generic joiner; merging them here changes which component
    # is kept.  Only multi-segment drawings count as split strokes (the
    # default); every stroke counts as a competitor (min_segments=1).
    if drawing.get("type") != "s" or len(items) < min_segments:
        return None
    if any(item[0] not in ("l", "c") for item in items):
        return None
    start, end = items[0][1], items[-1][-1]
    return (round(float(start.x), 2), round(float(start.y), 2)), (
        round(float(end.x), 2),
        round(float(end.y), 2),
    )


def _merge_source_contiguous_strokes(drawings: list[dict]) -> list[dict]:
    """Rejoin one stroke the PDF producer split into consecutive drawings.

    Some producers (onsemi FDA032N08 Fig. 4) emit one curve as several
    drawings where drawing N ends at exactly the point drawing N+1 starts.
    Edge chaining later trims sub-tolerance segments, so after extraction the
    pieces no longer touch and two converging curves make the generic join
    ambiguous.  Merge here only on source evidence: identical (0.01 pt) end ->
    start point, consecutive in drawing order, the same stroke style, and no
    other stroke drawing with an endpoint at that point.
    """

    endpoints = [_stroke_endpoints(drawing) for drawing in drawings]
    # Competing continuations are counted over ALL strokes, including the
    # one-segment drawings that are never merged themselves: a single-segment
    # alternative starting at the junction makes it ambiguous.
    touching: dict[tuple[float, float], int] = {}
    for record in (_stroke_endpoints(d, min_segments=1) for d in drawings):
        if record is None:
            continue
        for point in set(record):
            touching[point] = touching.get(point, 0) + 1

    def style(drawing: dict) -> tuple:
        return (
            round(float(drawing.get("width") or 0.0), 3),
            tuple(drawing.get("color") or ()),
            str(drawing.get("dashes")),
            drawing.get("stroke_opacity"),
        )

    merged: list[dict] = []
    index = 0
    while index < len(drawings):
        current = drawings[index]
        record = endpoints[index]
        following = index + 1
        while (
            record is not None
            and following < len(drawings)
            and (nxt := endpoints[following]) is not None
            and record[1] == nxt[0]
            and record[1] != record[0]
            and touching.get(record[1]) == 2
            and style(drawings[following]) == style(current)
        ):
            current = {
                **current,
                "items": [*current["items"], *drawings[following]["items"]],
                "rect": pymupdf.Rect(current["rect"]) | drawings[following]["rect"],
            }
            record = (record[0], nxt[1])
            following += 1
        merged.append(current)
        index = following
    return merged


def _excluded_wide_curve(
    drawing: dict,
    rect: pymupdf.Rect,
    curve_spans_x: bool,
    expected_curve_count: int | None,
) -> bool:
    if float(drawing.get("width") or 0.0) <= _VECTOR_MAX_STROKE_PT:
        return False
    components = _chain_vector_components(
        _vector_curve_edges(
            [drawing], rect, min_stroke_width=0.4, max_stroke_width=math.inf
        )
    )
    return any(
        _vector_group_span_status(component, rect, curve_spans_x, expected_curve_count)[0]
        for component in components
        if len(component) >= 2
    )


def _vector_group_span_status(
    group: list[tuple[float, float]],
    rect: pymupdf.Rect,
    curve_spans_x: bool,
    expected_curve_count: int | None,
) -> tuple[bool, bool]:
    """Apply the same source-span gate before and after gray-path rescue."""

    xs, ys = zip(*group)
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    if not curve_spans_x:
        return y_span >= 0.75 * rect.height and x_span >= 0.08 * rect.width, False
    if x_span >= 0.75 * rect.width and y_span >= 0.08 * rect.height:
        return True, False
    source_exit = _source_bound_top_exit_curve(
        group,
        rect,
        expected_curve_count=expected_curve_count,
    )
    return source_exit, source_exit


def _source_bound_top_exit_curve(
    points: list[tuple[float, float]],
    rect: pymupdf.Rect,
    *,
    expected_curve_count: int | None,
) -> bool:
    """Accept one three-label curve that provably exits the served Y range.

    A reversed-axis diode curve can leave through the labeled top boundary
    before reaching the right frame.  That is a source-clipped trace, not a
    short interior fragment.  Admit it only when its source points begin on the
    left frame, progress monotonically right/up, cross outside the calibrated
    top, and span at least half the owned X range.  The caller resamples only
    those source points; no continuation is synthesized beyond the exit.
    """

    if expected_curve_count != 3 or len(points) < 3:
        return False
    oriented = _left_to_right(points)
    xs = [point[0] for point in oriented]
    ys = [point[1] for point in oriented]
    if max(xs) - min(xs) < _SOURCE_BOUND_EXIT_MIN_X_SPAN * rect.width:
        return False
    left_tolerance = max(2.5, 0.025 * rect.width)
    if oriented[0][0] > rect.x0 + left_tolerance:
        return False
    if abs(oriented[0][0] - min(xs)) > 0.75:
        return False
    if any(right + 0.5 < left for left, right in zip(xs, xs[1:])):
        return False
    if any(right > left + 0.75 for left, right in zip(ys, ys[1:])):
        return False
    if oriented[-1][1] != min(ys):
        return False
    return oriented[-1][1] < rect.y0


def _join_vector_paths(
    paths: list[list[tuple[float, float]]],
    plot_height: float,
) -> list[list[tuple[float, float]]]:
    """Globally join generic strokes at unambiguous contiguous endpoints."""

    groups = [(list(path), (index,)) for index, path in enumerate(paths)]
    while len(groups) >= 2:
        candidates = []
        for first in range(len(groups)):
            first_path = groups[first][0]
            for second in range(first + 1, len(groups)):
                second_path = groups[second][0]
                first_y_span = max(y for _, y in first_path) - min(
                    y for _, y in first_path
                )
                second_y_span = max(y for _, y in second_path) - min(
                    y for _, y in second_path
                )
                if min(first_y_span, second_y_span) >= 0.60 * plot_height:
                    continue
                options = (
                    (
                        math.dist(first_path[-1], second_path[0]),
                        (first, "end"),
                        (second, "start"),
                        first_path + second_path[1:],
                    ),
                    (
                        math.dist(first_path[-1], second_path[-1]),
                        (first, "end"),
                        (second, "end"),
                        first_path + list(reversed(second_path[:-1])),
                    ),
                    (
                        math.dist(first_path[0], second_path[-1]),
                        (first, "start"),
                        (second, "end"),
                        second_path + first_path[1:],
                    ),
                    (
                        math.dist(first_path[0], second_path[0]),
                        (first, "start"),
                        (second, "start"),
                        list(reversed(second_path)) + first_path[1:],
                    ),
                )
                candidates.extend(
                    (distance, first_endpoint, second_endpoint, first, second, merged)
                    for distance, first_endpoint, second_endpoint, merged in options
                )
        tight = []
        for candidate in candidates:
            distance, first_endpoint, second_endpoint, *_ = candidate
            if distance > _VECTOR_JOIN_MAX_GAP_PT:
                continue
            alternatives = [
                other[0]
                for other in candidates
                if other is not candidate
                and (
                    other[1] == first_endpoint
                    or other[2] == second_endpoint
                    or other[1] == second_endpoint
                    or other[2] == first_endpoint
                )
            ]
            if alternatives and not distance < _VECTOR_JOIN_SEPARATION_FRACTION * min(
                alternatives
            ):
                continue
            tight.append(candidate)
        if not tight:
            break
        _, _, _, first, second, merged = min(
            tight,
            key=lambda item: (
                item[0],
                min(groups[item[3]][1]),
                min(groups[item[4]][1]),
            ),
        )
        source_indices = tuple(sorted(groups[first][1] + groups[second][1]))
        groups = [
            group
            for index, group in enumerate(groups)
            if index not in {first, second}
        ]
        groups.append((merged, source_indices))
        groups.sort(key=lambda group: min(group[1]))
    return [path for path, _ in groups]


def _left_to_right(
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    if len(points) >= 2 and points[-1][0] < points[0][0]:
        return list(reversed(points))
    return list(points)


def _stroke_widths_compatible(
    first: tuple[float, ...], second: tuple[float, ...]
) -> bool:
    widths = first + second
    if not widths:
        return True
    tolerance = max(
        _VECTOR_JOIN_WIDTH_ABS_TOLERANCE_PT,
        _VECTOR_JOIN_WIDTH_REL_TOLERANCE * max(widths),
    )
    return max(widths) - min(widths) <= tolerance


def _path_join_candidate(
    first_index: int,
    second_index: int,
    groups: list[_JoinedVectorPath],
    plot_height: float,
) -> tuple[float, int, int] | None:
    for left_index, right_index in (
        (first_index, second_index),
        (second_index, first_index),
    ):
        left, right = groups[left_index], groups[right_index]
        if left.dash_pattern != right.dash_pattern or not _stroke_widths_compatible(
            left.stroke_widths, right.stroke_widths
        ):
            continue
        left_x1 = max(point[0] for point in left.points)
        right_x0 = min(point[0] for point in right.points)
        if abs(left.points[-1][0] - left_x1) > 0.75:
            continue
        if abs(right.points[0][0] - right_x0) > 0.75:
            continue
        x_gap = right_x0 - left_x1
        if not -_VECTOR_JOIN_MAX_BACKTRACK_PT <= x_gap <= _VECTOR_JOIN_MAX_GAP_PT:
            continue
        left_y_span = max(y for _, y in left.points) - min(y for _, y in left.points)
        right_y_span = max(y for _, y in right.points) - min(y for _, y in right.points)
        if min(left_y_span, right_y_span) >= 0.60 * plot_height:
            continue
        return math.dist(left.points[-1], right.points[0]), left_index, right_index
    return None


def _globally_unambiguous_joins(
    groups: list[_JoinedVectorPath], plot_height: float
) -> list[tuple[float, int, int]]:
    candidates = [
        candidate
        for first in range(len(groups))
        for second in range(first + 1, len(groups))
        if (candidate := _path_join_candidate(first, second, groups, plot_height))
        is not None
    ]
    by_left: dict[int, list[tuple[float, int]]] = {}
    by_right: dict[int, list[tuple[float, int]]] = {}
    for index, (distance, left_index, right_index) in enumerate(candidates):
        by_left.setdefault(left_index, []).append((distance, index))
        by_right.setdefault(right_index, []).append((distance, index))

    def nearest_other(index: int, left_index: int, right_index: int) -> float | None:
        alternatives = [
            distance
            for distance, candidate_index in (
                by_left.get(left_index, []) + by_right.get(right_index, [])
            )
            if candidate_index != index
        ]
        return min(alternatives) if alternatives else None

    tight = []
    for index, candidate in enumerate(candidates):
        distance, left_index, right_index = candidate
        if distance > _VECTOR_JOIN_MAX_GAP_PT:
            continue
        alternative = nearest_other(index, left_index, right_index)
        if alternative is not None and not distance < (
            _VECTOR_JOIN_SEPARATION_FRACTION * alternative
        ):
            continue
        tight.append(candidate)
    return sorted(
        tight,
        key=lambda item: (
            item[0],
            min(groups[item[1]].source_indices),
            min(groups[item[2]].source_indices),
        ),
    )


def _join_vector_path_records(
    paths: list[tuple],
    plot_height: float,
) -> list[tuple[list[tuple[float, float]], set[tuple[float, ...]]]]:
    """Globally pair only source-contiguous, unambiguous split strokes.

    PDF drawing order is not curve order.  Compare every compatible left/right
    endpoint pair, then accept only a tight mutual separation winner.  A join
    must preserve dash style and stroke width, and it must be materially closer
    than any competing curve endpoint at the same split boundary.
    """

    groups = []
    for index, record in enumerate(paths):
        path, style = record[:2]
        width = record[2] if len(record) >= 3 else None
        groups.append(
            _JoinedVectorPath(
                _left_to_right(path),
                style,
                () if width is None else (float(width),),
                (index,),
            )
        )
    while len(groups) >= 2:
        candidates = _globally_unambiguous_joins(groups, plot_height)
        selected = []
        used: set[int] = set()
        for candidate in candidates:
            if candidate[1] in used or candidate[2] in used:
                continue
            selected.append(candidate)
            used.update(candidate[1:])
        if not selected:
            break
        merged_groups = []
        for distance, left_index, right_index in selected:
            left, right = groups[left_index], groups[right_index]
            right_points = right.points[1:] if distance <= 0.01 else right.points
            merged_groups.append(
                _JoinedVectorPath(
                    left.points + right_points,
                    left.dash_pattern,
                    left.stroke_widths + right.stroke_widths,
                    tuple(sorted(left.source_indices + right.source_indices)),
                )
            )
        groups = [
            group
            for index, group in enumerate(groups)
            if index not in used
        ]
        groups.extend(merged_groups)
        groups.sort(key=lambda group: min(group.source_indices))
    return [
        (group.points, {group.dash_pattern})
        for group in sorted(groups, key=lambda group: min(group.source_indices))
    ]
