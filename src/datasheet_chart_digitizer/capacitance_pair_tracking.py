"""Joint raster tracking for the two upper capacitance curves."""

from __future__ import annotations

import numpy as np

from .capacitance_types import CapAnchor, PlotBox


PAIR_MAX_STEP_PX = 32.0
PAIR_REACQUIRE_MAX_STEP_PX = 40.0
PAIR_SHARED_PREDICTION_DISTANCE_PX = 10.0
PAIR_MAX_MISSES = 80


def track_ciss_coss_pair(
    centers_by_x: list[list[float]],
    plot: PlotBox,
    anchors: dict[str, CapAnchor],
    seed_x: int | None = None,
) -> tuple[dict[str, list[tuple[int, int]]], int]:
    """Track Ciss/Coss as an exclusive pair, preserving identity through gaps."""

    chosen_seed = _pair_seed_x(centers_by_x, anchors, seed_x)
    seed_pair = centers_by_x[chosen_seed][:2]
    if _ciss_is_upper_at_anchor(anchors):
        seed_y = {"Ciss": seed_pair[0], "Coss": seed_pair[1]}
    else:
        seed_y = {"Ciss": seed_pair[1], "Coss": seed_pair[0]}

    local = {
        name: [(chosen_seed, float(y))]
        for name, y in seed_y.items()
    }
    for direction in (-1, 1):
        directional = _track_pair_direction(
            centers_by_x, chosen_seed, seed_y, direction
        )
        for name in local:
            local[name].extend(directional[name])

    tracked: dict[str, list[tuple[int, int]]] = {}
    for name, points in local.items():
        by_x = {x: y for x, y in points if x >= 3}
        tracked[name] = [
            (plot.x0 + x, plot.y0 + int(round(by_x[x])))
            for x in sorted(by_x)
        ]
    return tracked, chosen_seed


def _pair_seed_x(
    centers_by_x: list[list[float]],
    anchors: dict[str, CapAnchor],
    requested: int | None,
) -> int:
    # Imported lazily: capacitance_traces imports this module at load time.
    from .capacitance_traces import stable_three_center_columns

    candidates = stable_three_center_columns(centers_by_x) or [
        x for x, centers in enumerate(centers_by_x) if len(centers) >= 3
    ]
    if not candidates:
        raise RuntimeError("could not find a three-trace seed column")
    if requested is None:
        fraction = 0.50 if anchors else 0.55
        requested = int(round(fraction * (len(centers_by_x) - 1)))
    return min(candidates, key=lambda x: abs(x - requested))


def _ciss_is_upper_at_anchor(anchors: dict[str, CapAnchor]) -> bool:
    ciss = anchors.get("Ciss")
    coss = anchors.get("Coss")
    return ciss is None or coss is None or ciss.value_pf >= coss.value_pf


def _track_pair_direction(
    centers_by_x: list[list[float]],
    seed_x: int,
    seed_y: dict[str, float],
    direction: int,
) -> dict[str, list[tuple[int, float]]]:
    names = ("Ciss", "Coss")
    histories = {name: [(seed_x, seed_y[name])] for name in names}
    out: dict[str, list[tuple[int, float]]] = {name: [] for name in names}
    misses = 0
    x = seed_x + direction
    while 0 <= x < len(centers_by_x):
        centers = centers_by_x[x]
        # A two-stroke column does NOT imply the vanished stroke was Crss's.
        # Charts whose Crss decays into the axis lose the BOTTOM stroke first,
        # so `centers[:1]` handed the pair a single observation, the shared-
        # merge test rejected it (the two predictions are far apart), the
        # nearest name -- Ciss -- took it, and Coss starved into
        # PAIR_MAX_MISSES and truncated mid-chart (GT048N10T Coss ended at
        # 38 V of 100 V). Offer both strokes and let the parallel/crossed
        # assignment below decide identity, which is what it exists for.
        observations = centers[:2] if len(centers) >= 2 else []
        predictions = {name: _pair_prediction(histories[name], x) for name in names}
        limit = PAIR_REACQUIRE_MAX_STEP_PX if misses else PAIR_MAX_STEP_PX
        accepted = False

        if len(observations) == 2:
            parallel = sum(
                abs(predictions[name] - observations[index])
                for index, name in enumerate(names)
            )
            crossed = sum(
                abs(predictions[name] - observations[1 - index])
                for index, name in enumerate(names)
            )
            assignment = (
                {"Ciss": observations[1], "Coss": observations[0]}
                if crossed < parallel
                else {"Ciss": observations[0], "Coss": observations[1]}
            )
            if max(
                abs(predictions[name] - assignment[name]) for name in names
            ) <= limit:
                for name in names:
                    histories[name].append((x, assignment[name]))
                    out[name].append((x, assignment[name]))
                accepted = True
        if not accepted and observations:
            # Single-stroke salvage. Reached either when the column holds one
            # stroke, or when the joint two-stroke assignment above was
            # rejected -- the joint test is all-or-nothing, so without this
            # fallback widening `observations` to two would STARVE a column
            # that the old one-observation branch used to feed (IRFB4110G lost
            # the left half of both upper traces that way).
            observation = min(
                observations,
                key=lambda y: min(abs(predictions[name] - y) for name in names),
            )
            prediction_gap = abs(predictions["Ciss"] - predictions["Coss"])
            distances = {
                name: abs(predictions[name] - observation) for name in names
            }
            if prediction_gap <= PAIR_SHARED_PREDICTION_DISTANCE_PX and max(
                distances.values()
            ) <= limit:
                # A true one-stroke merge belongs to both identities, but it
                # must not overwrite their distinct incoming trajectories.
                for name in names:
                    out[name].append((x, observation))
                accepted = True
            else:
                name = min(names, key=lambda item: distances[item])
                if distances[name] <= limit:
                    histories[name].append((x, observation))
                    out[name].append((x, observation))
                    accepted = True

        misses = 0 if accepted else misses + 1
        if misses > PAIR_MAX_MISSES:
            break
        x += direction
    return out


def _pair_prediction(points: list[tuple[int, float]], x: int) -> float:
    window = points[-16:]
    if len(window) < 3 or window[-1][0] == window[0][0]:
        if len(window) < 2:
            return window[-1][1]
        (x0, y0), (x1, y1) = window[-2:]
        return y1 + (y1 - y0) * (x - x1) / (x1 - x0)
    xs = np.asarray([point[0] for point in window], dtype=float)
    ys = np.asarray([point[1] for point in window], dtype=float)
    slope, intercept = np.polyfit(xs, ys, 1)
    return float(slope * x + intercept)


def bridge_flat_ciss_occlusions(
    assigned: dict[str, list[tuple[int, int]]], plot: PlotBox
) -> dict[str, list[tuple[int, int]]]:
    """Interpolate a bounded flat Ciss gap erased by a grid rail or label."""

    points = sorted(assigned.get("Ciss", []))
    if len(points) < 2:
        return assigned
    maximum_gap = max(12, int(round(0.25 * plot.width)))
    repaired = dict(points)
    changed = False
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        gap = x1 - x0
        if gap <= 1 or gap > maximum_gap or abs(y1 - y0) > 4:
            continue
        for x in range(x0 + 1, x1):
            fraction = (x - x0) / gap
            repaired[x] = int(round(y0 + fraction * (y1 - y0)))
        changed = True
    if not changed:
        return assigned
    out = dict(assigned)
    out["Ciss"] = sorted(repaired.items())
    return out
