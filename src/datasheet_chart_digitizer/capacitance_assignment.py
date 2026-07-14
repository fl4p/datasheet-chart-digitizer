"""Use datasheet table anchors to diagnose and stabilize C(V) trace identity."""

from __future__ import annotations

import itertools
import math

import numpy as np

from .capacitance_axis import (
    calibration_log_c_of_y,
    calibration_x_of_v,
)
from .capacitance_traces import _interp_y_in_range
from .capacitance_types import AxisCalibration, CapAnchor, PlotBox, Trace


TRACE_NAMES = ("Ciss", "Coss", "Crss")
RIGHT_EDGE_PRIOR_WEIGHT = 0.08
SHAPE_PRIOR_WEIGHT = 0.08
MIN_ASSIGNMENT_IMPROVEMENT_DEC = 0.12
MAX_ASSIGNMENT_RMS_DEC = 0.18
MAX_ASSIGNMENT_RESIDUAL_DEC = 0.25


def select_trace_assignment(
    traces: list[Trace],
    plot: PlotBox,
    calibration: AxisCalibration | None,
    anchors: dict[str, CapAnchor],
) -> tuple[list[Trace], dict[str, object]]:
    """Score all semantic assignments and conservatively select the best one.

    Anchors are soft evidence: every usable residual is reported, but a changed
    assignment is accepted only when at least two anchors agree, the new fit is
    reasonably close to the table, and it clearly beats the extraction prior.
    This prevents known graph/table inconsistencies from force-fitting traces.
    """
    baseline = _assignment_by_current_name(traces)
    if calibration is None:
        return traces, _unavailable_diagnostics("untrusted_axis", anchors)
    if len(traces) != len(TRACE_NAMES) or set(baseline) != set(TRACE_NAMES):
        return traces, _unavailable_diagnostics("not_three_named_traces", anchors)

    usable_anchors = {name: anchors[name] for name in TRACE_NAMES if name in anchors}
    if not usable_anchors:
        return traces, _unavailable_diagnostics("no_table_anchors", anchors)

    candidates = []
    physical_traces = list(traces)
    for permutation in itertools.permutations(physical_traces):
        assignment = dict(zip(TRACE_NAMES, permutation))
        candidates.append(_score_assignment(assignment, plot, calibration, usable_anchors))
    candidates.sort(key=lambda candidate: float(candidate["total_score_decades"]))

    baseline_key = tuple(baseline[name].name for name in TRACE_NAMES)
    baseline_score = next(
        candidate for candidate in candidates
        if tuple(candidate["source_assignment"][name] for name in TRACE_NAMES) == baseline_key
    )
    if not any(int(candidate["anchors_compared"]) for candidate in candidates):
        diagnostics = _unavailable_diagnostics("anchors_outside_trace_spans", anchors)
        diagnostics["anchor_residuals"] = baseline_score["anchor_residuals"]
        diagnostics["candidates"] = candidates
        return traces, diagnostics
    best = candidates[0]
    best_key = tuple(best["source_assignment"][name] for name in TRACE_NAMES)
    changed = best_key != baseline_key
    reason = "baseline_best"

    if changed:
        improvement = float(baseline_score["total_score_decades"]) - float(best["total_score_decades"])
        residuals = [
            abs(float(item["log10_ratio"]))
            for item in best["anchor_residuals"].values()
            if item.get("log10_ratio") is not None
        ]
        if len(residuals) < 2:
            reason = "insufficient_anchor_coverage"
            changed = False
        elif improvement < MIN_ASSIGNMENT_IMPROVEMENT_DEC:
            reason = "improvement_too_small"
            changed = False
        elif float(best["anchor_rms_decades"]) > MAX_ASSIGNMENT_RMS_DEC:
            reason = "best_anchor_fit_too_poor"
            changed = False
        elif max(residuals) > MAX_ASSIGNMENT_RESIDUAL_DEC:
            reason = "anchor_outlier_prevents_relabel"
            changed = False
        else:
            reason = "anchor_evidence_selected"

    selected = best if changed else baseline_score
    selected_by_name = {
        name: next(trace for trace in traces if trace.name == selected["source_assignment"][name])
        for name in TRACE_NAMES
    }
    assigned = [_renamed_trace(selected_by_name[name], name) for name in TRACE_NAMES]
    return assigned, {
        "status": "scored",
        "assignment_changed": changed,
        "selection_reason": reason,
        "selected_source_assignment": selected["source_assignment"],
        "best_candidate_source_assignment": best["source_assignment"],
        "anchor_residuals": selected["anchor_residuals"],
        "anchor_rms_decades": selected["anchor_rms_decades"],
        "total_score_decades": selected["total_score_decades"],
        "baseline_total_score_decades": baseline_score["total_score_decades"],
        "best_candidate_total_score_decades": best["total_score_decades"],
        "best_candidate_improvement_decades": (
            float(baseline_score["total_score_decades"])
            - float(best["total_score_decades"])
        ),
        "candidates": candidates,
    }


def _score_assignment(
    assignment: dict[str, Trace],
    plot: PlotBox,
    calibration: AxisCalibration,
    anchors: dict[str, CapAnchor],
) -> dict[str, object]:
    residuals = {
        name: _anchor_residual(assignment[name], anchors[name], plot, calibration)
        for name in TRACE_NAMES
        if name in anchors
    }
    values = [
        float(item["log10_ratio"])
        for item in residuals.values()
        if item.get("log10_ratio") is not None
    ]
    anchor_rms = math.sqrt(sum(value * value for value in values) / len(values)) if values else math.inf
    missing_anchor_penalty = 0.5 * (len(anchors) - len(values))
    right_edge_penalty = _right_edge_order_penalty(assignment)
    shape_penalty = _shape_penalty(assignment)
    return {
        "source_assignment": {name: assignment[name].name for name in TRACE_NAMES},
        "anchor_residuals": residuals,
        "anchors_compared": len(values),
        "anchor_rms_decades": anchor_rms if values else None,
        "missing_anchor_penalty_decades": missing_anchor_penalty,
        "right_edge_prior_decades": right_edge_penalty,
        "shape_prior_decades": shape_penalty,
        "total_score_decades": (
            (anchor_rms if values else 0.0)
            + missing_anchor_penalty
            + right_edge_penalty
            + shape_penalty
        ),
    }


def _anchor_residual(
    trace: Trace,
    anchor: CapAnchor,
    plot: PlotBox,
    calibration: AxisCalibration,
) -> dict[str, object]:
    x = calibration_x_of_v(calibration, plot, anchor.vds_v)
    y = _interp_y_in_range(trace.points, int(round(x)))
    if y is None:
        return {
            "vds_v": anchor.vds_v,
            "table_pf": anchor.value_pf,
            "sampled_pf": None,
            "log10_ratio": None,
            "relative_error": None,
            "reason": "anchor_outside_trace_span",
        }
    sampled_pf = 10.0 ** calibration_log_c_of_y(calibration, plot, y)
    log_ratio = math.log10(sampled_pf / anchor.value_pf)
    return {
        "vds_v": anchor.vds_v,
        "table_pf": anchor.value_pf,
        "sampled_pf": sampled_pf,
        "log10_ratio": log_ratio,
        "relative_error": sampled_pf / anchor.value_pf - 1.0,
        "reason": None,
    }


def _right_edge_order_penalty(assignment: dict[str, Trace]) -> float:
    order = sorted(TRACE_NAMES, key=lambda name: _right_edge_y(assignment[name]))
    displacement = sum(abs(order.index(name) - TRACE_NAMES.index(name)) for name in TRACE_NAMES)
    return RIGHT_EDGE_PRIOR_WEIGHT * displacement / 4.0


def _shape_penalty(assignment: dict[str, Trace]) -> float:
    ciss_range = _y_range(assignment["Ciss"])
    coss_range = _y_range(assignment["Coss"])
    flatness_penalty = SHAPE_PRIOR_WEIGHT if ciss_range >= coss_range else 0.0

    samples = 0
    bottom = 0
    for x, y in assignment["Crss"].points:
        peer_ys = [
            _interp_y_in_range(assignment[name].points, x)
            for name in ("Ciss", "Coss")
        ]
        if any(peer_y is None for peer_y in peer_ys):
            continue
        samples += 1
        if y >= max(float(peer_y) for peer_y in peer_ys if peer_y is not None):
            bottom += 1
    bottom_fraction = bottom / samples if samples else 0.0
    bottom_penalty = SHAPE_PRIOR_WEIGHT * max(0.0, 0.95 - bottom_fraction) / 0.95
    return flatness_penalty + bottom_penalty


def _right_edge_y(trace: Trace) -> float:
    max_x = max(x for x, _ in trace.points)
    values = [y for x, y in trace.points if x >= max_x - 20]
    return float(np.median(values or [trace.points[-1][1]]))


def _y_range(trace: Trace) -> int:
    ys = [y for _, y in trace.points]
    return max(ys) - min(ys)


def _assignment_by_current_name(traces: list[Trace]) -> dict[str, Trace]:
    return {trace.name: trace for trace in traces}


def _renamed_trace(trace: Trace, name: str) -> Trace:
    return Trace(name=name, area=trace.area, bbox=trace.bbox, points=trace.points)


def _unavailable_diagnostics(reason: str, anchors: dict[str, CapAnchor]) -> dict[str, object]:
    return {
        "status": "unavailable",
        "assignment_changed": False,
        "selection_reason": reason,
        "selected_source_assignment": None,
        "anchor_residuals": {
            name: {
                "vds_v": anchor.vds_v,
                "table_pf": anchor.value_pf,
                "sampled_pf": None,
                "log10_ratio": None,
                "relative_error": None,
                "reason": reason,
            }
            for name, anchor in anchors.items()
        },
        "candidates": [],
    }
