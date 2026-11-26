"""Does each served raster C(V) trace start where its printed ink starts?

The span checks in ``capacitance_validation`` compare a trace's first sample
with the plot FRAME (``all_traces_left_edge_gap``: earliest start within 3 % of
the plot width) and with its PEERS (``<name>_peer_relative_late_x_start``: one
trace alone more than 3 % behind the other two).  Neither looks at the page:

* AGMH022N10H (0-80 V, linear): all three traces start 14-17 px (2.4-3.0 %,
  ~1.9 V) inside the frame, together, so neither check fires; the printed
  Coss/Crss rise 34 / 63 px over those missing columns (the steep knee).
* goford 4435 (0-30 V, linear): Ciss and Crss start 25 px (~1.2 V) late, Coss
  at the frame.  Two late traces are exempt from the peer check by design
  (Toshiba draws Ciss/Crss starting later), and the earliest start is Coss's.

Here the reference is the curve's own ink.  From the first served sample the
stroke is followed leftwards over the raw source raster (grey < ``INK_THRESHOLD``),
column by column, through ink runs that touch the previous column's run.  The
column where it ends is where the printed curve starts.  A trace whose ink goes
on further left than the tracer could ever serve (its blanked frame margin)
plus ``LOW_V_START_SLACK_PX`` has an unserved low-voltage start.

Legitimate starts need no special case, because the reference is the ink:
a curve printed from 1 V on a 0.1 V log axis ends its ink at 1 V; a curve
hidden behind the frame ends at the frame, which the margin covers; two curves
fused into one stroke at the left edge are both served on that stroke.
Rules are not ink of any curve: rows and columns that are >= 80 % dark across
the plot (black grids, the frame) are removed, and a column the walk can only
cross on a tall run (a vertical rule broken by a legend box) is crossed, not
followed, for at most ``MAX_NEUTRAL_COLUMNS``.
"""

from __future__ import annotations

import numpy as np

from .capacitance_grid_mask import (
    FULL_WIDTH_HORIZONTAL_RAIL_MIN_OCCUPANCY,
    _column_runs,
    trace_frame_margin_px,
)
from .capacitance_ink_continuity import CONNECTION_INK_MAX_DISTANCE_PX, INK_THRESHOLD
from .capacitance_types import PlotBox, Trace

# Ink beyond the blanked frame margin a served start may leave unserved.
# Calibration (parent fead2e3 raster runs on vup, 2026-11-25; unserved px
# minus the frame margin): over the 32 raster charts of the human-verified
# set served ok and the 12 raster ok charts of the benchmark, every trace is
# <= +0 except BSC320N20NS3 Crss (+10: its printed steep 0-~2 V segment is
# unserved) and the benchmark's missing starts, AGMH022N10H Ciss/Coss/Crss
# +8/+8/+11 and 4435 Ciss/Crss +19/+19.
LOW_V_START_SLACK_PX = 3
# The walk may continue through runs touching the previous run within this
# many rows (a removed 1-2 px rule row inside the stroke).
WALK_ROW_GAP_PX = 2
# The walk is seeded on the first of these served samples that has ink in its
# own column within the seating tolerance shared with the continuity check.
SEED_MAX_SAMPLES = 6
# A run this tall (fraction of plot height) is a rule, not a stroke step.
TALL_RUN_HEIGHT_FRACTION = 0.15
MAX_NEUTRAL_COLUMNS = 4


def _rule_free_ink(gray: np.ndarray, plot: PlotBox) -> tuple[np.ndarray, np.ndarray]:
    """Raw ink inside the plot minus full-span rules; plus the rule-column flags."""

    roi = gray[plot.y0 : plot.y1 + 1, plot.x0 : plot.x1 + 1] < INK_THRESHOLD
    rule_rows = roi.mean(axis=1) >= FULL_WIDTH_HORIZONTAL_RAIL_MIN_OCCUPANCY
    rule_cols = roi.mean(axis=0) >= FULL_WIDTH_HORIZONTAL_RAIL_MIN_OCCUPANCY
    ink = roi.copy()
    ink[rule_rows, :] = False
    ink[:, rule_cols] = False
    return ink, rule_cols


def _seed(ink: np.ndarray, plot: PlotBox, ordered: list[tuple[int, int]]):
    """(local x, lo, hi) of the first early served sample sitting on ink."""

    for x, y in ordered[:SEED_MAX_SAMPLES]:
        lx, ly = x - plot.x0, y - plot.y0
        if not 0 <= lx < ink.shape[1]:
            continue
        near = [
            run
            for run in _column_runs(ink[:, lx])
            if run[0] - CONNECTION_INK_MAX_DISTANCE_PX
            <= ly
            <= run[1] + CONNECTION_INK_MAX_DISTANCE_PX
        ]
        if near:
            return lx, min(r[0] for r in near), max(r[1] for r in near)
    return None


def leftward_ink_start(
    ink: np.ndarray, rule_cols: np.ndarray, plot: PlotBox, points: list[tuple[int, int]]
) -> dict[str, object]:
    """Follow one served trace's stroke left from its first served sample."""

    ordered = sorted(points)
    if not ordered:
        return {"evaluated": False, "reason": "no_served_samples"}
    seed = _seed(ink, plot, ordered)
    if seed is None:
        return {"evaluated": False, "reason": "served_start_not_on_ink"}
    x, lo, hi = seed
    top = lo
    start = x
    neutral = 0
    tall = TALL_RUN_HEIGHT_FRACTION * plot.height
    while x > 0:
        x -= 1
        if rule_cols[x]:
            neutral += 1
            if neutral > MAX_NEUTRAL_COLUMNS:
                break
            continue
        touching = [
            run
            for run in _column_runs(ink[:, x])
            if run[1] >= lo - WALK_ROW_GAP_PX and run[0] <= hi + WALK_ROW_GAP_PX
        ]
        if not touching:
            break
        new_lo = min(r[0] for r in touching)
        new_hi = max(r[1] for r in touching)
        if new_hi - new_lo + 1 > tall:
            neutral += 1
            if neutral > MAX_NEUTRAL_COLUMNS:
                break
            continue
        neutral = 0
        lo, hi = new_lo, new_hi
        start = x
        top = min(top, lo)
    first_x, first_y = ordered[0]
    return {
        "evaluated": True,
        "served_start_x_px": first_x,
        "ink_start_x_px": start + plot.x0,
        "unserved_px": max(0, first_x - (start + plot.x0)),
        "unserved_rise_px": max(0, first_y - (top + plot.y0)),
    }


def low_v_start_diagnostics(
    gray: np.ndarray | None, plot: PlotBox | None, traces: list[Trace]
) -> dict[str, object]:
    """Per trace, the printed ink the served curve leaves unserved at low VDS."""

    if (
        gray is None
        or plot is None
        or gray.ndim != 2
        or plot.x0 < 0
        or plot.y0 < 0
        or plot.x1 >= gray.shape[1]
        or plot.y1 >= gray.shape[0]
        or plot.width < 2
        or plot.height < 2
    ):
        return {"applicable": True, "evaluated": False, "reason": "no_usable_plot_raster"}
    ink, rule_cols = _rule_free_ink(gray, plot)
    if not ink.any():
        return {"applicable": True, "evaluated": False, "reason": "no_source_ink"}
    margin = trace_frame_margin_px(plot.width, plot.height)
    tolerance = margin + LOW_V_START_SLACK_PX
    by_trace: dict[str, object] = {}
    unserved: dict[str, object] = {}
    unevaluated: list[str] = []
    for trace in traces:
        item = leftward_ink_start(ink, rule_cols, plot, trace.points)
        by_trace[trace.name] = item
        if not item["evaluated"]:
            unevaluated.append(trace.name)
        elif int(item["unserved_px"]) > tolerance:
            unserved[trace.name] = item
    return {
        "applicable": True,
        "evaluated": True,
        "traces": by_trace,
        "unserved_traces": unserved,
        "unevaluated_traces": unevaluated,
        "thresholds": {
            "frame_margin_px": margin,
            "slack_px": LOW_V_START_SLACK_PX,
            "max_unserved_px": tolerance,
        },
    }


def low_v_start_reasons(diagnostics: dict[str, object] | None) -> list[str]:
    """Validation reasons; an unevaluable check is a reason, never clean.

    ``None`` or ``applicable`` false (vector extraction, unit callers testing
    something else) adds nothing, like the continuity checks.
    """

    if not isinstance(diagnostics, dict) or not diagnostics.get("applicable"):
        return []
    if not diagnostics.get("evaluated"):
        return ["low_v_start_unevaluated"]
    reasons: list[str] = []
    unserved = diagnostics.get("unserved_traces")
    unevaluated = diagnostics.get("unevaluated_traces")
    if not isinstance(unserved, dict) or not isinstance(unevaluated, list):
        return ["low_v_start_unevaluated"]
    for name in ("Ciss", "Coss", "Crss"):
        if name in unserved:
            reasons.append(f"{name}_low_v_start_unserved")
        if name in unevaluated:
            reasons.append(f"{name}_low_v_start_unevaluated")
    return reasons
