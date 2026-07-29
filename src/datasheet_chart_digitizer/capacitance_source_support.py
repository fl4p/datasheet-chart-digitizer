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
# A full-span dark horizontal row is a printed grid rule, not a curve.
GRID_RULE_MIN_OCCUPANCY = 0.80
GRID_RULE_CANDIDATE_MIN_OCCUPANCY = 0.40
GRID_RULE_CAPTURE_MAX_DISTANCE_PX = 1.0
GRID_RULE_CAPTURE_MAX_Y_SPREAD_PX = 2
GRID_RULE_CAPTURE_MIN_COLUMNS = 10
GRID_RULE_CAPTURE_MIN_SPAN_FRACTION = 0.03
GRID_RULE_CAPTURE_MIN_BOUNDARY_STEP_PX = 4.0
GRID_RULE_CAPTURE_BOUNDARY_WINDOW_PX = 3
GRID_RULE_TRACE_EXCLUSION_BAND_PX = 3
GRID_RULE_MIN_EVIDENCE_COLUMNS = 20
# Calibrated on six labelled corpus panels: real captures measured 6.0 / 11.0 /
# 11.9 / 17.0 px, while XR100N20G/H/T -- a flat Ciss correctly seated on its own
# stroke beside a rule -- measured exactly 4.0, which is the noise floor of a
# linear fit through a flat trace.  The floor sits between the two groups.
GRID_RULE_CAPTURE_MIN_APPROACH_DEVIATION_PX = 5.0
GRID_RULE_APPROACH_WINDOW_PX = 14
GRID_RULE_APPROACH_MIN_COLUMNS = 8


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
        "grid_rule_capture": grid_rule_capture_diagnostics(
            gray, plot, traces, shared_collapse_spans
        ),
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


def grid_rule_capture_diagnostics(
    gray: np.ndarray,
    plot: PlotBox,
    traces: list[Trace],
    shared_collapse_spans: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Find trace segments pinned onto a printed full-span horizontal rule.

    ``source_absent`` proves only that SOME dark ink sits under a served point.
    A grid rule is dark ink, so a trace that abandons its curve and rides a
    decade line stays fully "source supported" (CRMicro CRSM038N10N4 served a
    flat 99.7 pF Crss across 30-35 V, entered by a 13 px step, because the
    100 pF rule is drawn in trace-dark ink while the vertical grid is light --
    so the whole-grid separation gate never triggered).

    A genuinely flat trace lying along a decade line is the reason this needs
    more than "pinned to a rule".  Two independent discriminators are required:
    the printed rule must be evidenced in columns where this trace is NOT, and
    the run must depart the trace's own approach trajectory.  Columns already
    inside a shared-collapse span are skipped -- there the served y is a merge
    artefact owned by the shared-collapse gate, not evidence of grid capture.
    """

    if gray.ndim != 2 or gray.size == 0 or plot.width <= 1 or plot.height <= 1:
        # Never report "no capture" when the mask could not be read: the
        # caller must treat an unevaluated check as unverified, not as clean.
        return {"evaluated": False, "reason": "unusable_plot_raster"}

    interior = gray[plot.y0 : plot.y1, plot.x0 : plot.x1] < SOURCE_INK_THRESHOLD
    if interior.size == 0:
        return {"evaluated": False, "reason": "empty_plot_interior"}
    occupancy = interior.mean(axis=1)
    # Prefilter only. A rule the trace rides loses occupancy over the ridden
    # columns, so the decisive test below re-measures each candidate row
    # EXCLUDING the run under study.
    candidate_rows = [
        plot.y0 + int(index)
        for index in np.flatnonzero(occupancy >= GRID_RULE_CANDIDATE_MIN_OCCUPANCY)
    ]
    minimum_columns = max(
        GRID_RULE_CAPTURE_MIN_COLUMNS,
        math.ceil((plot.width - 1) * GRID_RULE_CAPTURE_MIN_SPAN_FRACTION),
    )
    merged_columns = _shared_span_columns(shared_collapse_spans)

    captures: dict[str, object] = {}
    undecidable_by_trace: dict[str, object] = {}
    for trace in traces:
        if not trace.points:
            continue
        y_by_x = dict(sorted(trace.points))
        xs = sorted(y_by_x)
        found: list[dict[str, object]] = []
        undecidable: list[dict[str, object]] = []
        for rule_y in candidate_rows:
            on_rule = [
                x
                for x in xs
                if x not in merged_columns
                and abs(y_by_x[x] - rule_y) <= GRID_RULE_CAPTURE_MAX_DISTANCE_PX
            ]
            for run in _column_runs(on_rule):
                if len(run) < minimum_columns:
                    continue
                run_ys = [y_by_x[x] for x in run]
                if max(run_ys) - min(run_ys) > GRID_RULE_CAPTURE_MAX_Y_SPREAD_PX:
                    continue
                step = _boundary_step_px(y_by_x, run)
                # A flat trace may legitimately COINCIDE with a printed rule
                # (Toshiba draws grid and data in one black ink).  Ink cannot
                # separate that from capture; the trace's own trajectory can.
                # A real curve arrives where it was heading, a captured one
                # leaves its approach to sit on the rule.
                deviation = _approach_deviation_px(
                    y_by_x, run, rule_y, merged_columns
                )
                if deviation < GRID_RULE_CAPTURE_MIN_APPROACH_DEVIATION_PX:
                    continue
                # A flat full-span trace makes its OWN row look like a rule
                # (Toshiba TPH2R70AR5 Ciss: 223 columns over rows 89-92).  The
                # printed rule must be evidenced where this trace is NOT.
                elsewhere, eligible = _rule_evidence_outside_trace(
                    interior, plot, rule_y, y_by_x
                )
                if eligible < GRID_RULE_MIN_EVIDENCE_COLUMNS:
                    # The trace covers this row almost everywhere, so rule and
                    # trace cannot be told apart here.  Record it rather than
                    # letting an unevaluable run read as a clean one; the
                    # flat-span gate owns the fully-flat shape.
                    undecidable.append(
                        {
                            **_run_to_json(run, plot),
                            "rule_y_px": rule_y,
                            "reason": "rule_indistinguishable_from_trace",
                            "rule_evidence_columns": eligible,
                        }
                    )
                    continue
                if elsewhere < GRID_RULE_MIN_OCCUPANCY:
                    continue
                found.append(
                    {
                        **_run_to_json(run, plot),
                        "rule_y_px": rule_y,
                        "boundary_step_px": step,
                        "approach_deviation_px": deviation,
                        "rule_occupancy_off_trace": elsewhere,
                        "rule_evidence_columns": eligible,
                    }
                )
        if found:
            captures[trace.name] = found
        if undecidable:
            undecidable_by_trace[trace.name] = undecidable

    return {
        "evaluated": True,
        "candidate_row_count": len(candidate_rows),
        "captured_traces": captures,
        # Runs that met every capture test but whose rule could not be told
        # apart from the trace itself.  Reported, never silently dropped.
        "undecidable_runs": undecidable_by_trace,
        "thresholds": {
            "grid_rule_min_occupancy": GRID_RULE_MIN_OCCUPANCY,
            "capture_max_distance_px": GRID_RULE_CAPTURE_MAX_DISTANCE_PX,
            "capture_max_y_spread_px": GRID_RULE_CAPTURE_MAX_Y_SPREAD_PX,
            "capture_min_columns": minimum_columns,
            "capture_min_approach_deviation_px": (
                GRID_RULE_CAPTURE_MIN_APPROACH_DEVIATION_PX
            ),
        },
    }


def _rule_evidence_outside_trace(
    interior: np.ndarray,
    plot: PlotBox,
    rule_y: int,
    y_by_x: dict[int, int],
) -> tuple[float, int]:
    """Dark fraction of a plot row over columns where this trace is elsewhere.

    Excluding only the flagged run is not enough: a flat trace darkens its row
    across the WHOLE width, so the run's complement is still its own ink
    (Toshiba TPH2R70AR5 Ciss measured 1.000 outside its run).  Every column in
    which the trace sits near the row is therefore excluded.  A trace that
    covers the row everywhere leaves no eligible columns and cannot be judged
    here -- that is the fully-flat case, owned by the flat-span gate.
    """

    row_index = rule_y - plot.y0
    if not 0 <= row_index < interior.shape[0]:
        return 0.0, 0
    row = interior[row_index]
    keep = np.ones(row.shape[0], dtype=bool)
    for x, y in y_by_x.items():
        if abs(y - rule_y) > GRID_RULE_TRACE_EXCLUSION_BAND_PX:
            continue
        column = x - plot.x0
        if 0 <= column < keep.shape[0]:
            keep[column] = False
    eligible = int(np.count_nonzero(keep))
    if eligible < GRID_RULE_MIN_EVIDENCE_COLUMNS:
        return 0.0, eligible
    return float(row[keep].mean()), eligible


def _shared_span_columns(
    shared_collapse_spans: list[dict[str, object]] | None,
) -> set[int]:
    """Columns where two names ride one stroke, owned by the collapse gate."""

    columns: set[int] = set()
    for span in shared_collapse_spans or ():
        try:
            x0 = int(span.get("x0_px"))  # type: ignore[arg-type]
            x1 = int(span.get("x1_px"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        columns.update(range(x0, x1 + 1))
    return columns


def _approach_deviation_px(
    y_by_x: dict[int, int],
    run: list[int],
    rule_y: int,
    merged_columns: set[int],
) -> float:
    """How far the rule sits from where the trace's approach was heading.

    The fit is extrapolated only to the ADJACENT run edge, never across the
    run: a window that ends in a steep stretch predicts wildly at a distant
    midpoint (Toshiba TPH2R70AR5 read 48 px that way).  Columns inside a
    shared-collapse span are excluded from the fit because a merged y is not
    this trace's own path.  Returns 0.0 when neither side has enough columns,
    which declines to flag rather than inventing an unmeasured trajectory.
    """

    xs = sorted(y_by_x)
    if not xs:
        return 0.0
    window = GRID_RULE_APPROACH_WINDOW_PX
    best = 0.0
    for (lo, hi), target in (
        ((run[0] - window, run[0] - 1), run[0]),
        ((run[-1] + 1, run[-1] + window), run[-1]),
    ):
        sample = [
            (x, y_by_x[x])
            for x in xs
            if lo <= x <= hi and x not in merged_columns
        ]
        if len(sample) < GRID_RULE_APPROACH_MIN_COLUMNS:
            continue
        sample_xs = np.array([x for x, _ in sample], dtype=float)
        sample_ys = np.array([y for _, y in sample], dtype=float)
        if float(sample_xs.max() - sample_xs.min()) < 1.0:
            continue
        slope, intercept = np.polyfit(sample_xs, sample_ys, 1)
        best = max(best, abs(float(slope * target + intercept) - rule_y))
    return best


def _boundary_step_px(y_by_x: dict[int, int], run: list[int]) -> float:
    """Largest y discontinuity where the run is entered or left."""

    step = 0.0
    for edge_x, direction in ((run[0], -1), (run[-1], 1)):
        for offset in range(1, GRID_RULE_CAPTURE_BOUNDARY_WINDOW_PX + 1):
            neighbour = edge_x + direction * offset
            if neighbour in y_by_x:
                step = max(step, abs(y_by_x[neighbour] - y_by_x[edge_x]))
                break
    return step


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
