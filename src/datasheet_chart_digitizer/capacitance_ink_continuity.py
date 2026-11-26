"""Ink continuity of served raster C(V) traces.

A served trace is a list of samples, and every consumer joins consecutive
samples with a straight line (``np.interp``).  The source-support check in
``capacitance_source_support`` proves only that each SAMPLE sits on ink.  It
says nothing about the line between two samples, which is where two wrong
serves hid:

* a hole in the samples across a steep knee is interpolated as a chord through
  blank paper (goford G6N90F Crss: 39 unsampled columns, ~20 pF served where the
  printed curve is ~140 pF at 6 V);
* a leading repair that climbs onto another curve's stroke and then drops back
  to its own in one column (agmsemi AGMH022N10H Coss/Crss: 58 px through white).

Both are measured here on the raw source raster, with no curve identity and no
physics: only where the page has ink.
"""

from __future__ import annotations

import math
from itertools import combinations

import cv2
import numpy as np

from .capacitance_types import Trace

# Dark-ink threshold shared with the source-support check (same raster rule).
INK_THRESHOLD = 90
# A served line may pass this far from ink and still count as on it: the same
# seating tolerance the source-support check applies to served samples.
CONNECTION_INK_MAX_DISTANCE_PX = 3.0
# Off-ink path length at which a connection is a shortcut, not antialias or a
# smoothing offset.  See `material_off_ink_runs` for the calibration.
CONNECTION_OFF_INK_MIN_PX = 6.0
# A label bridge continues the curve's own straight trajectory.  The worst
# measured label bridge landed 6.2 px off; the closest shortcut 18.0 px.
BRIDGE_MAX_TRAJECTORY_DEVIATION_PX = 8.0
BRIDGE_TRAJECTORY_WINDOW_PX = 14
BRIDGE_TRAJECTORY_MIN_SAMPLES = 8
# Two traces within this many pixels in a column ride one stroke.
SHARED_STROKE_MAX_DY_PX = 1
SHARED_STROKE_MIN_COLUMNS = 3
# The next served column must follow the shared run within this many columns
# for its step to count as the run's exit.
SHARED_STROKE_EXIT_MAX_DX = 3
# Exit step that no stroke split can produce: twice the largest exit step on
# any raster chart of the human-verified set served ``ok`` (4 px).  See
# `shared_stroke_exit_jumps`.
SHARED_STROKE_EXIT_MIN_STEP_PX = 8


def ink_distance_map(gray: np.ndarray) -> np.ndarray | None:
    """Per-pixel distance to the nearest dark source pixel, or None.

    None means the raster cannot testify (empty, not grayscale, or no ink at
    all); the caller must report the check unevaluated, never clean.
    """

    if gray is None or gray.ndim != 2 or gray.size == 0:
        return None
    ink = gray < INK_THRESHOLD
    if not ink.any():
        return None
    return cv2.distanceTransform((~ink).astype(np.uint8), cv2.DIST_L2, 5)


def off_ink_runs(
    distance: np.ndarray,
    points: list[tuple[int, int]],
    max_distance: float = CONNECTION_INK_MAX_DISTANCE_PX,
) -> list[dict[str, float]]:
    """Stretches of the served polyline farther than ``max_distance`` from ink.

    The polyline is the one a consumer interpolates: samples sorted by x,
    joined straight.  A stretch may span several connections (a smoothed jump
    is a staircase of short ones).  A path point outside the raster counts as
    off ink -- it cannot be shown to be on it.
    """

    ordered = sorted(points)
    height, width = distance.shape
    runs: list[dict[str, float]] = []
    length = 0.0
    start: tuple[float, float] | None = None
    last: tuple[float, float] | None = None
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        segment = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(math.ceil(segment * 2.0)))
        for index in range(1, steps + 1):
            t = index / steps
            x = x0 + (x1 - x0) * t
            y = y0 + (y1 - y0) * t
            xi, yi = int(round(x)), int(round(y))
            off = not (0 <= xi < width and 0 <= yi < height) or float(
                distance[yi, xi]
            ) > max_distance
            if off:
                if length == 0.0:
                    start = (x, y)
                length += segment / steps
                last = (x, y)
            elif length > 0.0:
                runs.append(_run_json(start, last, length))
                length = 0.0
    if length > 0.0:
        runs.append(_run_json(start, last, length))
    return runs


def material_off_ink_runs(
    distance: np.ndarray | None, traces: list[Trace]
) -> dict[str, object]:
    """Per trace, the served stretches that cross blank paper.

    The 6 px floor is two seating tolerances: a path farther than 3 px from
    ink for 6 px of length has left every stroke, which a smoothing offset on
    a stroke cannot do.

    One legitimate case crosses blank paper: a curve drawn THROUGH a printed
    label ("Coss" in a white box) is interrupted on the page, and the served
    line bridges the box.  Such a bridge continues the curve's own trajectory
    on both sides; a shortcut across a knee or a jump between strokes does
    not.  A stretch is exempt only when the trace's trajectory on BOTH sides
    is measurable and lands on the far bridge end within
    ``BRIDGE_MAX_TRAJECTORY_DEVIATION_PX``; an unmeasurable side never exempts.

    Calibration (main, raster runs, 2026-11-25), off-ink length / worse-side
    trajectory deviation:
      shortcuts   G6N90F Crss 76.0 / 113.7, AGMH022N10H Coss 31.7 / side
                  unmeasurable, PSMN020-30MLC Coss 7.8 / 18.0
      label bridges (charts judged correct)  IRLU8743PBF Coss 17.4 / 0.6,
                  Crss 6.0 / 6.0; BSC320N20NS3 Coss 14.4 / 6.2, Ciss 6.0 / 0.0;
                  IRF2804 Coss 8.4 / 2.9
    """

    if distance is None:
        return {"evaluated": False, "reason": "no_usable_source_ink_raster"}
    by_trace: dict[str, object] = {}
    bridges: dict[str, object] = {}
    longest: dict[str, float] = {}
    for trace in traces:
        runs = off_ink_runs(distance, trace.points)
        longest[trace.name] = max((run["length_px"] for run in runs), default=0.0)
        samples = dict(trace.points)
        material: list[dict[str, object]] = []
        exempt: list[dict[str, object]] = []
        for run in runs:
            if run["length_px"] < CONNECTION_OFF_INK_MIN_PX:
                continue
            deviations = _bridge_trajectory_deviations(samples, run)
            item = {**run, "trajectory_deviation_px": deviations}
            if all(
                value is not None and value <= BRIDGE_MAX_TRAJECTORY_DEVIATION_PX
                for value in deviations
            ):
                exempt.append(item)
            else:
                material.append(item)
        if material:
            by_trace[trace.name] = material
        if exempt:
            bridges[trace.name] = exempt
    return {
        "evaluated": True,
        "off_ink_traces": by_trace,
        "straight_bridges": bridges,
        "longest_off_ink_px": longest,
        "thresholds": {
            "ink_max_distance_px": CONNECTION_INK_MAX_DISTANCE_PX,
            "off_ink_min_px": CONNECTION_OFF_INK_MIN_PX,
            "bridge_max_trajectory_deviation_px": BRIDGE_MAX_TRAJECTORY_DEVIATION_PX,
            "bridge_trajectory_window_px": BRIDGE_TRAJECTORY_WINDOW_PX,
            "bridge_trajectory_min_samples": BRIDGE_TRAJECTORY_MIN_SAMPLES,
        },
    }


def _bridge_trajectory_deviations(
    samples: dict[int, int], run: dict[str, float]
) -> list[float | None]:
    """How far each side's own trajectory lands from the other bridge end.

    The bridge ends are the served samples bracketing the off-ink stretch.  A
    side with fewer than ``BRIDGE_TRAJECTORY_MIN_SAMPLES`` samples in its
    window -- a trace that starts at the bridge -- has no trajectory and
    reports None, which the caller treats as not exempt.
    """

    xs = sorted(samples)
    start_x = min(run["x0_px"], run["x1_px"])
    end_x = max(run["x0_px"], run["x1_px"])
    left = max((x for x in xs if x <= start_x + 1e-6), default=None)
    right = min((x for x in xs if x >= end_x - 1e-6), default=None)
    if left is None or right is None or left == right:
        return [None, None]
    window = BRIDGE_TRAJECTORY_WINDOW_PX
    deviations: list[float | None] = []
    for lo, hi, target in ((left - window, left, right), (right, right + window, left)):
        side = [(x, samples[x]) for x in xs if lo <= x <= hi]
        if len(side) < BRIDGE_TRAJECTORY_MIN_SAMPLES:
            deviations.append(None)
            continue
        side_x = np.array([x for x, _ in side], dtype=float)
        side_y = np.array([y for _, y in side], dtype=float)
        slope, intercept = np.polyfit(side_x, side_y, 1)
        deviations.append(
            round(abs(float(slope * target + intercept) - samples[target]), 1)
        )
    return deviations


def shared_stroke_exit_jumps(traces: list[Trace]) -> dict[str, object]:
    """Traces that share a stroke with a peer and then leave it by a jump.

    Two names on one stroke is normal where Ciss and Coss converge at low VDS:
    the strokes touch, the column holds one fused run, and when they part
    each name moves by about half a stroke width.  A name whose OWN stroke was
    elsewhere all along leaves instead by a jump to that stroke (SP010N02GHTF
    Coss: 13 px; PSMN020-30MLC Coss: 18 px), and its samples inside the shared
    run were on the peer's ink.  The step is measured from the run's last
    shared column to the next column where both names are served.

    Calibration (main, raster runs, 2026-11-25): over the 32 raster charts of
    the human-verified capacitance set served ``ok``, the largest exit step
    was 4 px; the known edge swaps measured 13 (SP010N02GHTF), 18
    (PSMN020-30MLC) and 64 px (AGMH022N10H).
    """

    by_name = {trace.name: dict(trace.points) for trace in traces if trace.points}
    jumps: dict[str, list[dict[str, object]]] = {}
    steps: list[float] = []
    for first, second in combinations(sorted(by_name), 2):
        a, b = by_name[first], by_name[second]
        common = sorted(set(a) & set(b))
        shared = [x for x in common if abs(a[x] - b[x]) <= SHARED_STROKE_MAX_DY_PX]
        for run in _shared_runs(shared, common):
            if len(run) < SHARED_STROKE_MIN_COLUMNS:
                continue
            for edge, neighbour in _run_neighbours(run, common):
                if abs(neighbour - edge) > SHARED_STROKE_EXIT_MAX_DX:
                    continue
                step_a = abs(a[neighbour] - a[edge])
                step_b = abs(b[neighbour] - b[edge])
                step = float(max(step_a, step_b))
                steps.append(step)
                if step < SHARED_STROKE_EXIT_MIN_STEP_PX:
                    continue
                leaver = first if step_a >= step_b else second
                jumps.setdefault(leaver, []).append(
                    {
                        "peer": second if leaver == first else first,
                        "shared_x0_px": run[0],
                        "shared_x1_px": run[-1],
                        "shared_columns": len(run),
                        "edge_x_px": edge,
                        "next_x_px": neighbour,
                        "step_px": step,
                    }
                )
    return {
        "evaluated": True,
        "jumping_traces": jumps,
        "largest_exit_step_px": max(steps, default=0.0),
        "thresholds": {
            "shared_max_dy_px": SHARED_STROKE_MAX_DY_PX,
            "shared_min_columns": SHARED_STROKE_MIN_COLUMNS,
            "exit_min_step_px": SHARED_STROKE_EXIT_MIN_STEP_PX,
        },
    }


def _shared_runs(shared: list[int], common: list[int]) -> list[list[int]]:
    """Runs of shared columns, consecutive in the both-served column order."""

    position = {x: index for index, x in enumerate(common)}
    runs: list[list[int]] = []
    for x in shared:
        if runs and position[x] == position[runs[-1][-1]] + 1:
            runs[-1].append(x)
        else:
            runs.append([x])
    return runs


def _run_neighbours(run: list[int], common: list[int]) -> list[tuple[int, int]]:
    """(run edge, adjacent both-served column) on each side that has one."""

    index_first = common.index(run[0])
    index_last = common.index(run[-1])
    pairs: list[tuple[int, int]] = []
    if index_first > 0:
        pairs.append((run[0], common[index_first - 1]))
    if index_last + 1 < len(common):
        pairs.append((run[-1], common[index_last + 1]))
    return pairs


def _run_json(
    start: tuple[float, float] | None, last: tuple[float, float] | None, length: float
) -> dict[str, float]:
    assert start is not None and last is not None
    return {
        "x0_px": round(start[0], 1),
        "y0_px": round(start[1], 1),
        "x1_px": round(last[0], 1),
        "y1_px": round(last[1], 1),
        "length_px": round(length, 1),
    }


def raster_continuity_diagnostics(
    gray: np.ndarray, traces: list[Trace]
) -> dict[str, object]:
    """Both continuity checks for one raster chart, as recorded in its result."""

    return {
        "applicable": True,
        "off_ink": material_off_ink_runs(ink_distance_map(gray), traces),
        "shared_stroke_exits": shared_stroke_exit_jumps(traces),
    }


def continuity_reasons(diagnostics: dict[str, object] | None) -> list[str]:
    """Validation reasons for a raster chart's continuity diagnostics.

    ``None`` or ``applicable`` false means the caller did not run the checks
    (vector extraction, or a unit caller testing something else) and adds
    nothing.  A check that RAN but could not evaluate is a reason of its own:
    unevaluable is never clean.
    """

    if not isinstance(diagnostics, dict) or not diagnostics.get("applicable"):
        return []
    reasons: list[str] = []
    for key, flagged_key, suffix in (
        ("off_ink", "off_ink_traces", "served_connection_off_ink"),
        ("shared_stroke_exits", "jumping_traces", "leaves_shared_stroke_by_jump"),
    ):
        check = diagnostics.get(key)
        if not isinstance(check, dict) or not check.get("evaluated"):
            reasons.append(f"{suffix}_unevaluated")
            continue
        flagged = check.get(flagged_key)
        if not isinstance(flagged, dict):
            reasons.append(f"{suffix}_unevaluated")
            continue
        for name in ("Ciss", "Coss", "Crss"):
            if flagged.get(name):
                reasons.append(f"{name}_{suffix}")
    return reasons
