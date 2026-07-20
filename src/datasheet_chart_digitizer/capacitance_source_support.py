"""Source-ink provenance checks for raster capacitance traces."""

from __future__ import annotations

import math

import numpy as np

from .capacitance_traces import _raster_source_centers_by_x
from .capacitance_types import PlotBox, Trace

SOURCE_INK_THRESHOLD = 90
SOURCE_INK_X_RADIUS_PX = 1
SOURCE_INK_MAX_DISTANCE_PX = 3.0
SOURCE_INK_MIN_ABSENT_COLUMNS = 8
SOURCE_INK_MIN_ABSENT_SPAN_FRACTION = 0.015
ORPHAN_CENTER_MAX_MATCH_DISTANCE_PX = 5.0
ORPHAN_CENTER_MIN_COLUMNS = 12
ORPHAN_CENTER_MIN_SPAN_FRACTION = 0.03


def raster_source_support_diagnostics(
    gray: np.ndarray,
    plot: PlotBox,
    traces: list[Trace],
    shared_collapse_spans: list[dict[str, object]],
) -> dict[str, object]:
    """Measure source-absent runs and source branches orphaned by a merge.

    Raster repairs may interpolate a visually smooth shortcut through a sharp
    Coss cliff.  A served point is source-seated only when dark source ink is
    present within a small two-dimensional neighborhood.  Separately, two
    names may ride one real stroke while a second continuous source branch is
    left unused; that is not genuine low-voltage Ciss/Coss convergence.
    """

    ink_y_by_x = [
        np.flatnonzero(gray[:, x] < SOURCE_INK_THRESHOLD)
        for x in range(gray.shape[1])
    ]
    absent_threshold = max(
        SOURCE_INK_MIN_ABSENT_COLUMNS,
        math.ceil((plot.width - 1) * SOURCE_INK_MIN_ABSENT_SPAN_FRACTION),
    )
    trace_support: dict[str, object] = {}
    for trace in traces:
        absent_x = [
            x
            for x, y in trace.points
            if _nearest_ink_distance(ink_y_by_x, x, y)
            > SOURCE_INK_MAX_DISTANCE_PX
        ]
        runs = _column_runs(absent_x)
        material = [run for run in runs if len(run) >= absent_threshold]
        trace_support[trace.name] = {
            "source_absent_columns": len(absent_x),
            "longest_source_absent_run": max(map(len, runs), default=0),
            "material_source_absent_runs": [
                _run_to_json(run, plot) for run in material
            ],
        }

    orphan_threshold = max(
        ORPHAN_CENTER_MIN_COLUMNS,
        math.ceil((plot.width - 1) * ORPHAN_CENTER_MIN_SPAN_FRACTION),
    )
    orphan_x = _shared_orphan_source_columns(
        gray, plot, traces, shared_collapse_spans
    )
    orphan_runs = _column_runs(orphan_x)
    material_orphans = [run for run in orphan_runs if len(run) >= orphan_threshold]
    return {
        "applicable": True,
        "trace_support": trace_support,
        "shared_orphan_source_columns": len(orphan_x),
        "longest_shared_orphan_source_run": max(
            map(len, orphan_runs), default=0
        ),
        "material_shared_orphan_source_runs": [
            _run_to_json(run, plot) for run in material_orphans
        ],
        "thresholds": {
            "source_ink_max_distance_px": SOURCE_INK_MAX_DISTANCE_PX,
            "source_ink_min_absent_columns": absent_threshold,
            "orphan_center_max_match_distance_px": (
                ORPHAN_CENTER_MAX_MATCH_DISTANCE_PX
            ),
            "orphan_center_min_columns": orphan_threshold,
        },
    }


def _nearest_ink_distance(
    ink_y_by_x: list[np.ndarray], x: int, y: int
) -> float:
    best = float("inf")
    for source_x in range(
        max(0, x - SOURCE_INK_X_RADIUS_PX),
        min(len(ink_y_by_x), x + SOURCE_INK_X_RADIUS_PX + 1),
    ):
        source_ys = ink_y_by_x[source_x]
        if source_ys.size == 0:
            continue
        y_distance = float(np.min(np.abs(source_ys - y)))
        best = min(best, math.hypot(source_x - x, y_distance))
    return best


def _shared_orphan_source_columns(
    gray: np.ndarray,
    plot: PlotBox,
    traces: list[Trace],
    shared_spans: list[dict[str, object]],
) -> list[int]:
    if not shared_spans:
        return []
    _mask, centers_by_x = _raster_source_centers_by_x(gray, plot)
    by_name = {trace.name: dict(trace.points) for trace in traces}
    if not {"Ciss", "Coss", "Crss"}.issubset(by_name):
        return []

    orphan_x: list[int] = []
    for span in shared_spans:
        x0 = int(span.get("x0_px") or 0)
        x1 = int(span.get("x1_px") or -1)
        for x in range(x0, x1 + 1):
            local_x = x - plot.x0
            if not 0 <= local_x < len(centers_by_x):
                continue
            assigned_ys = [
                by_name[name][x]
                for name in ("Ciss", "Coss", "Crss")
                if x in by_name[name]
            ]
            if len(assigned_ys) != 3:
                continue
            source_ys = [plot.y0 + center for center in centers_by_x[local_x]]
            if any(
                min(abs(source_y - assigned_y) for assigned_y in assigned_ys)
                > ORPHAN_CENTER_MAX_MATCH_DISTANCE_PX
                for source_y in source_ys
            ):
                orphan_x.append(x)
    return orphan_x


def _column_runs(xs: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for x in sorted(set(xs)):
        if not runs or x > runs[-1][-1] + 1:
            runs.append([x])
        else:
            runs[-1].append(x)
    return runs


def _run_to_json(run: list[int], plot: PlotBox) -> dict[str, object]:
    return {
        "x0_px": run[0],
        "x1_px": run[-1],
        "sample_count": len(run),
        "span_fraction": (run[-1] - run[0]) / max(1, plot.width - 1),
    }
