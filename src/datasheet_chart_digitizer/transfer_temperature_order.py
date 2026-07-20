"""Fail-closed physical ordering checks for temperature transfer curves."""

from __future__ import annotations

import numpy as np


def bind_opposite_outer_labels(
    pixel_curves: list[list[tuple[int, int]]],
    labels: list[tuple[float, tuple[float, float, float, float]]],
    plot_width: float,
) -> list[int] | None:
    """Bind labels placed outside opposite sides of a two-curve family.

    Crossing curves can make global Euclidean distance nearly tied even when
    each printed label clearly sits outside one branch at the label's own
    current.  Require opposite outer sides, a visible same-row distance
    advantage, and distinct branches.  Labels between curves never qualify.
    """

    if len(pixel_curves) != 2 or len(labels) != 2:
        return None
    margin = max(2.0, 0.01 * plot_width)
    bindings: list[int] = []
    sides: list[int] = []
    for _temperature, rect in labels:
        center_x = 0.5 * (rect[0] + rect[2])
        center_y = 0.5 * (rect[1] + rect[3])
        curve_xs = []
        for curve in pixel_curves:
            by_y: dict[int, list[int]] = {}
            for x, y in curve:
                by_y.setdefault(y, []).append(x)
            ys = sorted(by_y)
            if len(ys) < 2 or not ys[0] <= center_y <= ys[-1]:
                return None
            xs = [float(np.median(by_y[y])) for y in ys]
            curve_xs.append(float(np.interp(center_y, ys, xs)))
        left, right = min(curve_xs), max(curve_xs)
        if center_x <= left - margin:
            sides.append(-1)
        elif center_x >= right + margin:
            sides.append(1)
        else:
            return None
        distances = sorted(
            (abs(center_x - curve_x), index)
            for index, curve_x in enumerate(curve_xs)
        )
        if distances[1][0] - distances[0][0] < margin:
            return None
        bindings.append(distances[0][1])
    return bindings if set(sides) == {-1, 1} and len(set(bindings)) == 2 else None


def inverse_vgs(
    points: list[tuple[float, float]], currents: np.ndarray
) -> np.ndarray:
    """Interpolate VGS at in-range positive drain currents."""

    ordered = sorted((float(i), float(v)) for v, i in points if i > 0)
    ids: list[float] = []
    vgs: list[float] = []
    for current, gate in ordered:
        if ids and current <= ids[-1]:
            if current == ids[-1]:
                vgs[-1] = gate
            continue
        ids.append(current)
        vgs.append(gate)
    if len(ids) < 8:
        raise RuntimeError("transfer curve has too few monotone positive-current points")
    return np.interp(currents, ids, vgs)


def validate_two_curve_order(
    cold: list[tuple[float, float]], hot: list[tuple[float, float]]
) -> bool:
    """Validate label-bound hot-left ordering and at most one robust ZTC.

    Returns whether a source-resolved in-range crossover was observed. Curves
    which only converge below the chart limit remain valid, but missing
    hot-left evidence, a hot-right contradiction, or robust recrossing refuses.
    """

    positive = [[float(i) for _v, i in curve if i > 0] for curve in (cold, hot)]
    if any(len(values) < 8 for values in positive):
        raise RuntimeError("transfer curve has too few monotone positive-current points")
    lo = max(min(values) for values in positive)
    hi = min(max(values) for values in positive)
    if hi <= lo:
        raise RuntimeError("transfer curves have no common positive-current range")
    probes = lo + np.asarray((0.12, 0.18)) * (hi - lo)
    cold_probe, hot_probe = inverse_vgs(cold, probes), inverse_vgs(hot, probes)
    gates = [float(v) for curve in (cold, hot) for v, i in curve if i > 0]
    margin = max(1e-3, 0.005 * (max(gates) - min(gates)))
    if np.any(cold_probe - hot_probe <= margin):
        raise RuntimeError(
            "label-bound hot transfer curve is not visibly left of the cold "
            "curve at shared low current"
        )

    grid = np.linspace(lo, hi, 240)
    delta = inverse_vgs(hot, grid) - inverse_vgs(cold, grid)
    signs = np.where(delta < -margin, -1, np.where(delta > margin, 1, 0))
    robust: list[int] = []
    start = 0
    while start < len(signs):
        end = start + 1
        while end < len(signs) and signs[end] == signs[start]:
            end += 1
        sign = int(signs[start])
        if sign and end - start >= 5 and (not robust or robust[-1] != sign):
            robust.append(sign)
        start = end
    if robust not in ([-1], [-1, 1]):
        raise RuntimeError(
            "label-bound transfer curves have contradictory or multiple robust "
            "temperature-order reversals"
        )
    return robust == [-1, 1]
