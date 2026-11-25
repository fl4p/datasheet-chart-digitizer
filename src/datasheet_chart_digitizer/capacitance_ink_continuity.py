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

Measured here on the raw source raster, with no curve identity and no physics:
only where the page has ink.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

# Dark-ink threshold shared with the source-support check (same raster rule).
INK_THRESHOLD = 90
# A served line may pass this far from ink and still count as on it: the same
# seating tolerance the source-support check applies to served samples.
CONNECTION_INK_MAX_DISTANCE_PX = 3.0
# Off-ink path length at which a connection is a shortcut, not antialias or a
# smoothing offset: two seating tolerances.  Calibration: the leading-repair
# jumps measured 31.7 px (AGMH022N10H) and 55-60 px (AON6226, AON6276, NCE4080).
CONNECTION_OFF_INK_MIN_PX = 6.0


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
