"""Qoss/Coss validation helpers for MOSFET capacitance charts."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from .capacitance_types import AxisCalibration, OutputChargeReference, PlotBox, Trace

# Ciss/Coss/Crss are monotonically non-increasing in Vds, so a trace whose value
# RISES from low to high Vds is not a capacitance curve -- it is a mis-seat onto a
# non-cap panel misclassified as capacitance (SOA/Zth envelopes rise).  Flag when
# value climbs > this fraction of the plot height (left-fifth to right-fifth
# medians).  Calibrated: 24 good PASS charts top out at +0.011, while the SOA
# (NCE2010E, +0.221) and Zth (FDD6612A, +0.097) leaks sit far above 0.05.
UNPHYSICAL_VALUE_RISE_FRACTION = 0.05
MIN_MATERIAL_TRACE_X_SPAN_FRACTION = 0.65
MAX_CRSS_PEER_X_SPAN_DEFICIT = 0.06
MAX_TRACE_LEFT_EDGE_GAP_FRACTION = 0.03
MAX_PEER_LEFT_START_DEFICIT = 0.03
FLAT_GRID_CAPTURE_MAX_Y_RANGE_PX = 1
# Grid-capture is raster-only; vector paths cannot latch onto a gridline.
FLAT_GRID_CAPTURE_RASTER_MIN_X_SPAN_FRACTION = MIN_MATERIAL_TRACE_X_SPAN_FRACTION
FLAT_GRID_CAPTURE_VECTOR_MIN_X_SPAN_FRACTION = 0.90
QOSS_SERVABLE_STATUSES = frozenset(
    {
        "pass",
        "pass_vendor_qoss_curve_tail",
        "clipped_chart_completed",
    }
)


def trace_validation_summary(
    diagnostics: dict[str, object],
    extraction_method: str | None = None,
    shared_collapse_spans: list[dict[str, object]] | None = None,
    left_start_fractions: dict[str, float] | None = None,
) -> dict[str, object]:
    """Fail closed on incomplete or semantically untrusted C(V) traces."""

    flat_span_gate = (
        FLAT_GRID_CAPTURE_VECTOR_MIN_X_SPAN_FRACTION
        if extraction_method == "vector"
        else FLAT_GRID_CAPTURE_RASTER_MIN_X_SPAN_FRACTION
    )
    reasons: list[str] = []
    if any(
        span.get("separated_sign_before") is not None
        and span.get("separated_sign_after") is None
        for span in shared_collapse_spans or ()
    ):
        # Normal low-V convergence has no sign_before and later separates.
        reasons.append("ciss_coss_unresolved_shared_collapse")

    upper_spans = [
        float((diagnostics.get(name) or {}).get("x_span_fraction") or 0.0)
        for name in ("Ciss", "Coss")
        if isinstance(diagnostics.get(name), dict)
    ]
    crss_diag = diagnostics.get("Crss")
    if upper_spans and isinstance(crss_diag, dict):
        upper_span = max(upper_spans)
        crss_span = float(crss_diag.get("x_span_fraction") or 0.0)
        vector_tail_is_bounded = min(upper_spans) >= 0.98 and crss_span >= 0.85
        if (
            (extraction_method != "vector" or vector_tail_is_bounded)
            and upper_span >= MIN_MATERIAL_TRACE_X_SPAN_FRACTION
            and upper_span - crss_span > MAX_CRSS_PEER_X_SPAN_DEFICIT
        ):
            # Vector PDFs may intentionally stop Crss early, so only the
            # bounded near-full case fires there: both upper paths reach the
            # frame and Crss alone loses a short tail. Raster tracking has no
            # independent source-owned endpoint proof and uses the full rule.
            reasons.append("Crss_peer_relative_short_x_span")

    left_starts = {
        name: max(0.0, float(value))
        for name, value in (left_start_fractions or {}).items()
        if name in ("Ciss", "Coss", "Crss")
    }
    if len(left_starts) == 3:
        earliest = min(left_starts.values())
        if earliest > MAX_TRACE_LEFT_EDGE_GAP_FRACTION:
            reasons.append("all_traces_left_edge_gap")
        else:
            late_names = [
                name
                for name, start in left_starts.items()
                if start - earliest > MAX_PEER_LEFT_START_DEFICIT
            ]
            # A single lagging trace against two edge-reaching peers is strong
            # differential evidence.  Two traces may legitimately begin later
            # than the third (for example Toshiba Ciss/Crss source strokes), so
            # that pattern needs source-ink proof rather than a pixel-only gate.
            if len(late_names) == 1:
                reasons.append(f"{late_names[0]}_peer_relative_late_x_start")

    for name in ("Ciss", "Coss", "Crss"):
        trace_diag = diagnostics.get(name)
        if not isinstance(trace_diag, dict):
            reasons.append(f"missing_{name}")
            continue
        points = int(trace_diag.get("points") or 0)
        span = float(trace_diag.get("x_span_fraction") or 0.0)
        if points < 8:
            reasons.append(f"{name}_too_few_points")
        # Some complete NXP source strokes intentionally stop around 68% of a
        # 100 V plot, so this is a material-source floor, not a frame-end rule.
        if span < MIN_MATERIAL_TRACE_X_SPAN_FRACTION:
            reasons.append(f"{name}_short_x_span")
        y_range = int(trace_diag.get("y_range_px") or 0)
        if y_range <= FLAT_GRID_CAPTURE_MAX_Y_RANGE_PX and span >= flat_span_gate:
            reasons.append(f"{name}_flat_full_span_unverified")
        if (
            float(trace_diag.get("value_rise_fraction") or 0.0)
            > UNPHYSICAL_VALUE_RISE_FRACTION
        ):
            reasons.append(f"{name}_rises_with_vds_unphysical")

    checks = diagnostics.get("checks")
    if not isinstance(checks, dict):
        reasons.append("missing_semantic_checks")
    else:
        if int(checks.get("common_samples") or 0) < 20:
            reasons.append("too_few_common_samples")
        if int(checks.get("ciss_coss_rank_swap_count") or 0) not in (0, 1):
            reasons.append("ciss_coss_rank_swap_count")
        if float(checks.get("crss_bottom_fraction") or 0.0) < 0.95:
            reasons.append("crss_not_bottom")
        if not bool(checks.get("ciss_flatter_than_coss")):
            reasons.append("ciss_not_flatter_than_coss")

    return {"status": "pass" if not reasons else "suspect", "reasons": reasons}


def trace_left_start_fractions(
    traces: list[Trace], plot: PlotBox
) -> dict[str, float]:
    """Measure each trace's first served source column in plot pixel space."""

    width = max(1, plot.width - 1)
    return {
        trace.name: (min(x for x, _y in trace.points) - plot.x0) / width
        for trace in traces
        if trace.points
    }


def value_rise_fraction(points: list[tuple[int, int]], plot_height: int) -> float:
    """Signed fraction of plot height a trace's value climbs, left-fifth to right.

    Value increases upward (smaller y_px), so positive => value rose with Vds.
    """
    xs = sorted(x for x, _ in points)
    span = xs[-1] - xs[0] if xs else 0
    if span <= 0:
        return 0.0
    lo, hi = xs[0] + 0.2 * span, xs[-1] - 0.2 * span
    left = [y for x, y in points if x <= lo]
    right = [y for x, y in points if x >= hi]
    if not left or not right:
        return 0.0
    return (float(np.median(left)) - float(np.median(right))) / max(1, plot_height)


def coss_metrics_to_json(metrics: object) -> dict[str, float]:
    return {
        "Qoss_pc": float(metrics.Qoss),
        "Eoss_pJ": float(metrics.Eoss),
        "Co_tr_pf": float(metrics.Co_tr),
        "Co_er_pf": float(metrics.Co_er),
        "Qoss_below_first_pc": float(metrics.Qoss_below_first),
        "Qoss_chart_range_pc": float(metrics.Qoss_chart_range),
        "Qoss_above_last_pc": float(metrics.Qoss_above_last),
        "Eoss_below_first_pJ": float(metrics.Eoss_below_first),
        "Eoss_chart_range_pJ": float(metrics.Eoss_chart_range),
        "Eoss_above_last_pJ": float(metrics.Eoss_above_last),
        "C0_pf": float(metrics.C0),
        "phi_v": float(metrics.phi),
        "m": float(metrics.m),
        "first_vds_v": float(metrics.first_vds),
        "first_coss_pf": float(metrics.first_coss),
        "splice_rel_error": float(metrics.splice_rel_error),
        "extrapolated_qoss_fraction": float(metrics.extrapolated_qoss_fraction),
        "clipped_completion_active": bool(metrics.clipped_completion_active),
        "clip_boundary_vds": metrics.clip_boundary_vds,
        "Qoss_clip_completed_pc": float(metrics.Qoss_clip_completed),
        "Qoss_clip_visible_floor_pc": float(metrics.Qoss_clip_visible_floor),
        "Qoss_clip_added_pc": float(metrics.Qoss_clip_added),
        "clipped_completion_fraction": float(metrics.clipped_completion_fraction),
    }


def qoss_validation_status(
    metrics: object | None,
    validation_error: str | None,
    vendor_tail_validation: dict[str, object] | None = None,
    *,
    table_reference_available: bool | None = None,
) -> str | None:
    if metrics is None:
        return None
    if vendor_tail_validation and vendor_tail_validation.get("status") == "pass":
        return "pass_vendor_qoss_curve_tail"
    if float(metrics.extrapolated_qoss_fraction) > 0.20:
        return "unreliable_extrapolation"
    if validation_error == "Qoss table reference unavailable":
        return (
            "chart_clipped_reference_unavailable"
            if bool(metrics.clipped_completion_active)
            else "reference_unavailable"
        )
    if bool(metrics.clipped_completion_active):
        if validation_error is not None:
            return (
                "chart_clipped_table_authoritative"
                if table_reference_available
                else "chart_clipped_reference_unavailable"
            )
        return "clipped_chart_completed"
    if validation_error is None:
        return "pass"
    return "graph_table_inconsistent"


def partition_qoss_metrics(
    metrics: dict[str, object] | None,
    validation_status: str | None,
    *,
    chart_physical_output_available: bool,
) -> tuple[dict[str, object] | None, dict[str, object] | None, bool]:
    """Separate served Qoss scalars from explicitly diagnostic-only metrics."""

    available = bool(
        metrics is not None
        and chart_physical_output_available
        and validation_status in QOSS_SERVABLE_STATUSES
    )
    if available:
        return metrics, None, True
    return None, metrics, False


def qoss_metrics_status_reasons(
    metrics: dict[str, object] | None,
    validation_status: str | None,
    *,
    chart_physical_output_available: bool,
) -> list[str]:
    """Explain every reason the derived Qoss bundle is not consumer-safe."""

    reasons: list[str] = []
    if metrics is None:
        reasons.append("qoss_metrics_unavailable")
    elif validation_status not in QOSS_SERVABLE_STATUSES:
        reasons.append(
            f"qoss_validation_status:{validation_status or 'unavailable'}"
        )
    if not chart_physical_output_available:
        reasons.append("chart_physical_output_unavailable")
    return reasons


def vendor_qoss_tail_validation(
    part: str,
    metrics: object | None,
    output_ref: OutputChargeReference,
    tol: float,
) -> dict[str, object] | None:
    if metrics is None or output_ref.vint_v is None:
        return None
    curve_path = _vendor_qoss_curve_path(part)
    if curve_path is None:
        return None
    rows: list[tuple[float, float]] = []
    with curve_path.open(newline="", errors="replace") as f:
        for row in csv.DictReader(f):
            try:
                rows.append((float(row["VDS_V"]), float(row["Qoss_nC"]) * 1000.0))
            except (KeyError, TypeError, ValueError):
                continue
    if len(rows) < 2:
        return None
    rows.sort()
    vds = np.array([v for v, _ in rows], dtype=float)
    qoss = np.array([q for _, q in rows], dtype=float)
    first_v = float(metrics.first_vds)
    vint = float(output_ref.vint_v)
    if first_v < vds[0] or first_v > vds[-1] or vint < vds[0] or vint > vds[-1]:
        return {
            "tail_source": "vendor_qoss_curve",
            "status": "out_of_range",
            "curve_csv": str(curve_path),
        }
    vendor_tail = float(np.interp(first_v, vds, qoss))
    vendor_total = float(np.interp(vint, vds, qoss))
    qoss_with_vendor_tail = float(metrics.Qoss_chart_range + metrics.Qoss_above_last + vendor_tail)
    ref = output_ref.qoss_pc if output_ref.qoss_pc is not None else vendor_total
    rel_to_ref = abs(qoss_with_vendor_tail - float(ref)) / float(ref) if ref else None
    rel_to_vendor = abs(qoss_with_vendor_tail - vendor_total) / vendor_total if vendor_total else None
    status = "pass" if rel_to_ref is not None and rel_to_ref <= tol else "fail"
    return {
        "tail_source": "vendor_qoss_curve",
        "status": status,
        "curve_csv": str(curve_path),
        "first_vds_v": first_v,
        "vint_v": vint,
        "vendor_tail_pc": vendor_tail,
        "vendor_total_pc": vendor_total,
        "chart_range_pc": float(metrics.Qoss_chart_range),
        "qoss_with_vendor_tail_pc": qoss_with_vendor_tail,
        "reference_qoss_pc": ref,
        "rel_error_to_reference": rel_to_ref,
        "rel_error_to_vendor_curve": rel_to_vendor,
    }


def _vendor_qoss_curve_path(part: str) -> Path | None:
    here = Path(__file__).resolve().parent
    candidates = [
        here / f"{part.lower()}_qoss_reference.csv",
        here / f"{part.lower()}_qoss_diagram17_reference.csv",
    ]
    if part == "IMZA75R050M2H":
        candidates.append(here / "imza_qoss_diagram17_reference.csv")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def top_decade_clip_diagnostic(
    trace_data: dict[str, list[tuple[float, float]]],
    calibration: AxisCalibration | None,
) -> dict[str, object] | None:
    if calibration is None or "Coss" not in trace_data:
        return None
    data = sorted(trace_data["Coss"])
    if not data:
        return None
    axis_top_pf = 10.0 ** calibration.y_max_decade
    low_v_limit = calibration.x_min_v + 0.05 * (calibration.x_max_v - calibration.x_min_v)
    low_v_caps = [cap for vds, cap in data if vds <= low_v_limit]
    max_low_v_coss = max(low_v_caps or [data[0][1]])
    return {
        "axis_top_pf": axis_top_pf,
        "max_low_v_coss_pf": max_low_v_coss,
        "low_v_limit_v": low_v_limit,
        "near_axis_top": max_low_v_coss >= axis_top_pf * 0.70,
    }
