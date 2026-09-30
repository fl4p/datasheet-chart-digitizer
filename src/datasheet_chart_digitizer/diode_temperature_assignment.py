"""Temperature identity for body-diode forward-voltage curves.

Two independent kinds of evidence can name a curve:

* ordering -- at low/mid shared current a hotter body diode conducts at a
  lower VSD, so the VSD rank of each curve names its temperature;
* in-plot label position -- a ``Tj = 25 °C`` label printed next to exactly
  one curve names that curve.

Ordering is used only where it is stable and the curves are separated at the
binding current; label positions are used only when each label sits clearly
nearer one curve than any other.  When both exist and disagree the panel is
refused -- a refusal is always better than a silently swapped curve.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

import numpy as np

from .transfer_temperature_labels import TemperatureLabel

if TYPE_CHECKING:  # pragma: no cover
    from .capacitance_types import PlotBox
    from .crop_transform import CropTransform

# A label is bound to a curve only when that curve passes within this many
# label heights of the label box and every other curve is clearly farther.
_LABEL_NEAR_HEIGHTS = 1.5
_LABEL_RIVAL_RATIO = 2.0
_LABEL_RIVAL_MIN_HEIGHTS = 0.5
# A leader starts within this many label heights of its label box and is at
# least this many label heights long.
_LEADER_START_HEIGHTS = 0.75
_LEADER_MIN_HEIGHTS = 1.0
# A legend swatch sits on the label's row within this many label heights.
_SWATCH_ROW_HEIGHTS = 0.75
_SWATCH_GAP_HEIGHTS = 3.0
_ORDER_SAMPLES = 6
_ORDER_STABLE_SAMPLES = 4
_ORDER_BINDING_SAMPLE = 2

LABEL_BOUND = "temperature_identity_bound_by_in_plot_label_position"
ORDER_BOUND = "temperature_identity_stable_over_low_mid_shared_current"
ORDER_CONFIRMED = "temperature_ordering_confirmed_by_in_plot_label_position"


@dataclass(frozen=True)
class LabelEvidence:
    """Curve index -> temperature from unambiguous in-plot label positions."""

    by_curve: dict[int, float]
    labels_in_plot: int


Segment = tuple[tuple[float, float], tuple[float, float]]


def _box_distance(point: tuple[float, float], box: tuple[float, float, float, float]) -> float:
    x, y = point
    return math.hypot(
        max(box[0] - x, 0.0, x - box[2]), max(box[1] - y, 0.0, y - box[3])
    )


def _leader_tips(label: TemperatureLabel, leaders: Sequence[Segment]) -> list[tuple[float, float]]:
    """Far endpoints of leader strokes that start at this label's box."""

    box = (label.x0, label.y0, label.x1, label.y1)
    height = max(label.y1 - label.y0, 1.0)
    tips = []
    for start, end in leaders:
        for near, far in ((start, end), (end, start)):
            if (
                _box_distance(near, box) <= _LEADER_START_HEIGHTS * height
                and _box_distance(far, box) >= _LEADER_MIN_HEIGHTS * height
            ):
                tips.append(far)
    return tips


def _has_swatch(label: TemperatureLabel, swatches: Sequence[Segment]) -> bool:
    height = max(label.y1 - label.y0, 1.0)
    middle = 0.5 * (label.y0 + label.y1)
    for start, end in swatches:
        near_x = min(
            abs(x - edge) for x in (start[0], end[0]) for edge in (label.x0, label.x1)
        )
        if (
            abs(0.5 * (start[1] + end[1]) - middle) <= _SWATCH_ROW_HEIGHTS * height
            and near_x <= _SWATCH_GAP_HEIGHTS * height
        ):
            return True
    return False


def _unambiguous_nearest(
    arrays: list[np.ndarray],
    box: tuple[float, float, float, float],
    height: float,
) -> int | None:
    left, top, right, bottom = box
    distances = sorted(
        (
            float(
                np.min(
                    np.hypot(
                        np.clip(points[:, 0], left, right) - points[:, 0],
                        np.clip(points[:, 1], top, bottom) - points[:, 1],
                    )
                )
            ),
            index,
        )
        for index, points in enumerate(arrays)
    )
    nearest, index = distances[0]
    rival = distances[1][0]
    if nearest > _LABEL_NEAR_HEIGHTS * height:
        return None
    if rival < max(_LABEL_RIVAL_RATIO * nearest, nearest + _LABEL_RIVAL_MIN_HEIGHTS * height):
        return None
    return index


def in_plot_label_evidence(
    labels: Sequence[TemperatureLabel],
    transform: "CropTransform",
    plot: "PlotBox",
    curves_px: Sequence[Sequence[tuple[int, int]]],
    leaders: Sequence[Segment] = (),
    swatches: Sequence[Segment] = (),
) -> LabelEvidence:
    """Bind in-plot temperature labels to the one curve each names.

    A label with a leader stroke names the curve at the leader tip; a label
    without one names the curve it sits next to.  Unevaluable or
    contradictory evidence is dropped, never guessed: a label between two
    curves, a label with several leaders, a curve claimed by two different
    temperatures, or one temperature claimed for two curves gives no evidence
    for those curves.  A label with a legend swatch beside it is a legend
    entry and gives no position evidence.  ``leaders``/``swatches`` are
    straight source strokes in PDF points.
    """

    arrays = [np.asarray(curve, dtype=float) for curve in curves_px if len(curve)]
    if len(arrays) != len(curves_px) or len(arrays) < 2:
        # Nothing to tell apart; the caller's count check refuses.
        return LabelEvidence({}, 0)
    claims: dict[int, set[float]] = {}
    curves_by_value: dict[float, set[int]] = {}
    in_plot = 0
    for label in labels:
        x0, y0 = transform.to_px(label.x0, label.y0)
        x1, y1 = transform.to_px(label.x1, label.y1)
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        cx, cy = 0.5 * (left + right), 0.5 * (top + bottom)
        if not (plot.x0 < cx < plot.x1 and plot.y0 < cy < plot.y1):
            continue
        in_plot += 1
        if _has_swatch(label, swatches):
            continue
        height = max(bottom - top, 1.0)
        tips = _leader_tips(label, leaders)
        if len(tips) > 1:
            continue
        if tips:
            tip_x, tip_y = transform.to_px(*tips[0])
            index = _unambiguous_nearest(arrays, (tip_x, tip_y, tip_x, tip_y), height)
        else:
            index = _unambiguous_nearest(arrays, (left, top, right, bottom), height)
        if index is None:
            continue
        claims.setdefault(index, set()).add(label.value_c)
        curves_by_value.setdefault(label.value_c, set()).add(index)
    by_curve = {
        index: next(iter(values))
        for index, values in claims.items()
        if len(values) == 1 and len(curves_by_value[next(iter(values))]) == 1
    }
    return LabelEvidence(by_curve, in_plot)


def _single_hot_low_crossing(
    cold: list[tuple[float, float]],
    hot: list[tuple[float, float]],
    log_current: bool,
    margin: float,
) -> bool:
    """True when the hotter curve starts at lower VSD and crosses at most once."""

    lo, hi = max(cold[0][0], hot[0][0]), min(cold[-1][0], hot[-1][0])
    if not hi > lo:
        return False
    grid = np.linspace(math.log10(lo), math.log10(hi), 256) if log_current else np.linspace(lo, hi, 256)
    current = 10**grid if log_current else grid

    def voltage(points):
        return np.interp(current, [p[0] for p in points], [p[1] for p in points])

    delta = voltage(cold) - voltage(hot)
    signs = [1 if value > 0 else -1 for value in delta if abs(value) > margin]
    changes = sum(1 for left, right in zip(signs, signs[1:]) if left != right)
    return bool(signs) and signs[0] == 1 and changes <= 1


def _ordering_binding(
    data: list[list[tuple[float, float]]],
    temperatures: list[float],
    log_current: bool,
    margin: float,
) -> tuple[dict[int, float] | None, str]:
    """VSD-rank binding, or ``None`` with the reason it is unevaluable."""

    lo = max(points[0][0] for points in data)
    hi = min(points[-1][0] for points in data)
    if not hi > lo:
        raise RuntimeError("curves have no shared current range")
    lo_c, hi_c = (math.log10(lo), math.log10(hi)) if log_current else (lo, hi)
    sample_currents = [
        10**value if log_current else value
        for value in np.linspace(lo_c, hi_c, _ORDER_SAMPLES)
    ]
    orders, voltages_at = [], []
    for current in sample_currents:
        voltages = [
            float(np.interp(current, [p[0] for p in points], [p[1] for p in points]))
            for points in data
        ]
        voltages_at.append(sorted(voltages))
        orders.append(tuple(np.argsort(voltages)))
    if len(set(orders[:_ORDER_STABLE_SAMPLES])) != 1:
        return None, "temperature ordering is unstable over low/mid shared current"
    # Near a crossing the rank at the binding current is decided by
    # sub-margin differences; that is not ordering evidence.
    binding = voltages_at[_ORDER_BINDING_SAMPLE]
    if any(right - left <= margin for left, right in zip(binding, binding[1:])):
        return None, "temperature curves are not separated at the ordering current"
    ordered_curves = orders[_ORDER_BINDING_SAMPLE]
    return (
        {
            int(curve_index): temp
            for curve_index, temp in zip(ordered_curves, sorted(temperatures, reverse=True))
        },
        "",
    )


def assign_temperatures(
    curves_px: list[list[tuple[int, int]]],
    calibration,
    temperatures: list[float],
    *,
    voltage_on_y: bool = False,
    curve_identities: list | None = None,
    label_evidence: LabelEvidence | None = None,
) -> tuple[list[dict[str, object]], float | None, str]:
    """Return (curves, verified crossover current, identity diagnostic)."""

    style_bound = curve_identities is not None
    if style_bound:
        if len(curve_identities) != len(curves_px) or any(item is None for item in curve_identities):
            raise RuntimeError("not every extracted curve has a source-legend identity")
        identities = [item for item in curve_identities if item is not None]
        if len(set(identities)) != len(identities):
            raise RuntimeError("source legend contains duplicate curve identities")
        if {item.temperature_c for item in identities} != set(temperatures):
            raise RuntimeError("source-legend temperatures disagree with panel labels")
    elif len(curves_px) != len(temperatures) or len(curves_px) < 2:
        raise RuntimeError(
            f"curve/temperature mismatch: {len(curves_px)} curves, {len(temperatures)} labels"
        )
    voltage_axis = calibration.y_axis if voltage_on_y else calibration.x_axis
    current_axis = calibration.x_axis if voltage_on_y else calibration.y_axis
    voltage_bounds = sorted(tick.value for tick in voltage_axis.ticks)[
        :: len(voltage_axis.ticks) - 1
    ]
    current_bounds = sorted(tick.value for tick in current_axis.ticks)[
        :: len(current_axis.ticks) - 1
    ]
    voltage_span = voltage_bounds[1] - voltage_bounds[0]
    log_current = current_axis.model == "log10"
    data = []
    for curve in curves_px:
        points = []
        for x, y in curve:
            if voltage_on_y:
                current, voltage = current_axis.value(x), voltage_axis.value(y)
            else:
                current, voltage = current_axis.value(y), voltage_axis.value(x)
            if (
                voltage_bounds[0] <= voltage <= voltage_bounds[1]
                and current_bounds[0] <= current <= current_bounds[1]
            ):
                points.append((current, voltage))
        data.append(sorted(points))
    if any(not points for points in data):
        raise RuntimeError("curve contains no calibrated points")
    if style_bound:
        results = []
        for identity, points, points_px in zip(identities, data, curves_px):
            results.append(
                {
                    "temperature_c": identity.temperature_c,
                    "curve_role": identity.role,
                    "points": [[round(vsd, 6), round(current, 6)] for current, vsd in points],
                    "points_px": [[x, y] for x, y in points_px],
                }
            )
        results.sort(key=lambda item: (float(item["temperature_c"]), item["curve_role"] != "typical"))
        return results, verified_crossover(results, log_current, voltage_span), ""

    margin = max(0.005, 0.01 * voltage_span)
    by_labels = label_evidence.by_curve if label_evidence is not None else {}
    by_order, order_refusal = _ordering_binding(data, temperatures, log_current, margin)
    if by_order is not None:
        contradicted = sorted(
            index for index, value in by_labels.items() if by_order[index] != value
        )
        if contradicted:
            raise RuntimeError(
                "in-plot label positions contradict the VSD temperature ordering "
                f"for curves {contradicted}"
            )
        assigned_temp = by_order
        diagnostic = ORDER_CONFIRMED if by_labels else ORDER_BOUND
    elif (
        len(by_labels) == len(data)
        and sorted(by_labels.values()) == sorted(temperatures)
        and all(
            _single_hot_low_crossing(data[cold], data[hot], log_current, margin)
            for cold in by_labels
            for hot in by_labels
            if by_labels[hot] > by_labels[cold]
        )
    ):
        # Every curve carries its own label and every hotter/colder pair
        # obeys the low-current physics (hot conducts first, one crossover).
        assigned_temp = dict(by_labels)
        diagnostic = LABEL_BOUND
    else:
        raise RuntimeError(order_refusal)
    results = []
    for index, points in enumerate(data):
        results.append(
            {
                "temperature_c": assigned_temp[index],
                "points": [[round(vsd, 6), round(current, 6)] for current, vsd in points],
                "points_px": [[x, y] for x, y in curves_px[index]],
            }
        )
    results.sort(key=lambda item: float(item["temperature_c"]))
    return results, verified_crossover(results, log_current, voltage_span), diagnostic


def verified_crossover(curves: list[dict[str, object]], log_current: bool, voltage_span: float):
    by_temp = {
        float(curve["temperature_c"]): curve["points"]
        for curve in curves
        if curve.get("curve_role", "typical") == "typical"
    }
    if 25.0 not in by_temp or 175.0 not in by_temp:
        return None
    cold, hot = by_temp[25.0], by_temp[175.0]
    lo, hi = max(cold[0][1], hot[0][1]), min(cold[-1][1], hot[-1][1])
    coordinate = np.linspace(math.log10(lo), math.log10(hi), 256) if log_current else np.linspace(lo, hi, 256)
    current = 10**coordinate if log_current else coordinate
    def voltage(points):
        return np.interp(current, [p[1] for p in points], [p[0] for p in points])

    delta = voltage(cold) - voltage(hot)
    margin = max(0.005, 0.01 * voltage_span)
    significant = [(index, 1 if value > 0 else -1) for index, value in enumerate(delta) if abs(value) > margin]
    states = [item for index, item in enumerate(significant) if index == 0 or item[1] != significant[index - 1][1]]
    if not states or states[0][1] != 1:
        raise RuntimeError("temperature ordering reverses in the low-current band")
    if len(states) == 1:
        return None
    if len(states) != 2 or states[1][0] < 0.60 * (len(current) - 1):
        raise RuntimeError("temperature curves have repeated or low/mid-current crossings")
    left = max(item for item in significant if item[1] == 1)[0]
    right = min(item for item in significant if item[1] == -1)[0]
    fraction = delta[left] / (delta[left] - delta[right])
    return float(10 ** (coordinate[left] + fraction * (coordinate[right] - coordinate[left])) if log_current else current[left] + fraction * (current[right] - current[left]))
