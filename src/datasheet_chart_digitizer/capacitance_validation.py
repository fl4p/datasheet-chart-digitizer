"""Qoss/Coss validation helpers for MOSFET capacitance charts."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from .capacitance_types import AxisCalibration, OutputChargeReference

# Ciss/Coss/Crss are monotonically non-increasing in Vds, so a trace whose value
# RISES from low to high Vds is not a capacitance curve -- it is a mis-seat onto a
# non-cap panel misclassified as capacitance (SOA/Zth envelopes rise).  Flag when
# value climbs > this fraction of the plot height (left-fifth to right-fifth
# medians).  Calibrated: 24 good PASS charts top out at +0.011, while the SOA
# (NCE2010E, +0.221) and Zth (FDD6612A, +0.097) leaks sit far above 0.05.
UNPHYSICAL_VALUE_RISE_FRACTION = 0.05


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
) -> str | None:
    if metrics is None:
        return None
    if vendor_tail_validation and vendor_tail_validation.get("status") == "pass":
        return "pass_vendor_qoss_curve_tail"
    if float(metrics.extrapolated_qoss_fraction) > 0.20:
        return "unreliable_extrapolation"
    if bool(metrics.clipped_completion_active):
        if validation_error is not None:
            return "chart_clipped_table_authoritative"
        return "clipped_chart_completed"
    if validation_error is None:
        return "pass"
    return "graph_table_inconsistent"


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

