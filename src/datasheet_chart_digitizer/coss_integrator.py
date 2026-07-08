#!/usr/bin/env python3
"""Coss charge/energy metrics for validating digitized C(V) curves.

This helper is deliberately independent of the chart extractor. It consumes
data-space arrays, not pixels:

- `vds`: drain-source voltage in V
- `coss`: output capacitance in pF

Returned units follow from pF * V = pC:

- Qoss: pC
- Eoss: pC*V, numerically equal to pJ
- Co(tr): pF
- Co(er): pF
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

try:
    from scipy.optimize import curve_fit
except Exception:  # pragma: no cover - fallback for minimal environments
    curve_fit = None


_trapz = np.trapezoid if hasattr(np, "trapezoid") else np.trapz


@dataclass(frozen=True)
class CossMetrics:
    Qoss: float
    Eoss: float
    Co_tr: float
    Co_er: float
    Qoss_below_first: float
    Qoss_chart_range: float
    Qoss_above_last: float
    Eoss_below_first: float
    Eoss_chart_range: float
    Eoss_above_last: float
    C0: float
    phi: float
    m: float
    first_vds: float
    first_coss: float
    splice_rel_error: float
    extrapolated_qoss_fraction: float
    clipped_completion_active: bool
    clip_boundary_vds: float | None
    Qoss_clip_completed: float
    Qoss_clip_visible_floor: float
    Qoss_clip_added: float
    clipped_completion_fraction: float


def _depletion_from_phi_m(v: np.ndarray, c0: float, phi: float, m: float) -> np.ndarray:
    return c0 / (1.0 + v / phi) ** m


def _fit_low_v_depletion(vds: np.ndarray, coss: np.ndarray) -> tuple[float, float, float]:
    return _fit_depletion_anchored(vds, coss, float(vds[0]), float(coss[0]))


def _fit_depletion_anchored(
    vds: np.ndarray, coss: np.ndarray, first_v: float, first_c: float
) -> tuple[float, float, float]:
    def anchored(v: np.ndarray, phi: float, m: float) -> np.ndarray:
        c0 = first_c * (1.0 + first_v / phi) ** m
        return _depletion_from_phi_m(v, c0, phi, m)

    if curve_fit is None or len(vds) < 3:
        phi, m = 1.0, 0.5
    else:
        # Fit on approximately the first decade of available low-V samples.
        # If the chart starts at V=0, use the first few nonzero samples instead
        # of a degenerate min*10 window.
        if first_v > 0:
            edge = max(first_v * 10.0, float(vds[min(len(vds) - 1, 6)]))
        else:
            edge = float(vds[min(len(vds) - 1, 6)])
        lo = vds <= edge
        if int(np.sum(lo)) < 3:
            lo[: min(len(vds), 6)] = True
        try:
            (phi, m), _ = curve_fit(
                anchored,
                vds[lo],
                coss[lo],
                p0=(1.0, 0.5),
                bounds=([0.3, 0.3], [3.0, 0.6]),
                maxfev=20000,
            )
        except Exception:
            phi, m = 1.0, 0.5

    c0 = first_c * (1.0 + first_v / phi) ** m
    return float(c0), float(phi), float(m)


def coss_metrics(
    vds: np.ndarray,
    coss: np.ndarray,
    vint: float,
    n: int = 4000,
    *,
    clip_ceiling: float | None = None,
    clip_rel_tol: float = 0.02,
) -> CossMetrics:
    vds = np.asarray(vds, dtype=float)
    coss = np.asarray(coss, dtype=float)
    if vds.ndim != 1 or coss.ndim != 1 or len(vds) != len(coss) or len(vds) < 2:
        raise ValueError("vds and coss must be same-length 1D arrays with at least two samples")
    if vint <= 0:
        raise ValueError("vint must be positive")
    if np.any(~np.isfinite(vds)) or np.any(~np.isfinite(coss)):
        raise ValueError("vds and coss must be finite")
    if np.any(coss <= 0):
        raise ValueError("coss must be positive")

    order = np.argsort(vds)
    vds = vds[order]
    coss = coss[order]
    if np.any(np.diff(vds) <= 0):
        raise ValueError("vds samples must be unique")

    c0, phi, m = _fit_low_v_depletion(vds, coss)
    clip_idx = _leading_clip_boundary_index(vds, coss, clip_ceiling, clip_rel_tol)
    clipped_completion_active = clip_idx is not None
    interp_vds = vds
    interp_coss = coss
    clip_boundary_vds: float | None = None
    clip_region: np.ndarray | None = None
    visible_floor_region: np.ndarray | None = None
    if clip_idx is not None:
        clip_boundary_vds = float(vds[clip_idx])
        c0, phi, m = _fit_low_v_depletion(vds[clip_idx:], coss[clip_idx:])
        interp_vds = vds[clip_idx:]
        interp_coss = coss[clip_idx:]

    grid = np.linspace(0.0, float(vint), int(n))
    completion_boundary = float(interp_vds[0])
    below = grid < completion_boundary
    c = np.empty_like(grid)
    c[below] = _depletion_from_phi_m(grid[below], c0, phi, m)
    c[~below] = np.interp(grid[~below], interp_vds, interp_coss)
    if clip_idx is not None:
        clip_region = grid < completion_boundary
        visible_floor_region = np.interp(grid, vds, coss)
        c[clip_region] = np.maximum(c[clip_region], visible_floor_region[clip_region])

    qoss = float(_trapz(c, grid))
    eoss = float(_trapz(c * grid, grid))
    q_below, q_chart, q_above = _partitioned_integral(c, grid, float(vds[0]), float(vds[-1]))
    e_below, e_chart, e_above = _partitioned_integral(c * grid, grid, float(vds[0]), float(vds[-1]))
    splice_value = float(_depletion_from_phi_m(np.array([vds[0]]), c0, phi, m)[0])
    splice_rel_error = abs(splice_value - float(coss[0])) / float(coss[0])
    q_clip_completed = 0.0
    q_clip_visible = 0.0
    if clip_region is not None and visible_floor_region is not None:
        q_clip_completed = _masked_midpoint_integral(c, grid, clip_region)
        q_clip_visible = _masked_midpoint_integral(visible_floor_region, grid, clip_region)
    q_clip_added = q_clip_completed - q_clip_visible
    return CossMetrics(
        Qoss=qoss,
        Eoss=eoss,
        Co_tr=qoss / float(vint),
        Co_er=2.0 * eoss / float(vint) ** 2,
        Qoss_below_first=q_below,
        Qoss_chart_range=q_chart,
        Qoss_above_last=q_above,
        Eoss_below_first=e_below,
        Eoss_chart_range=e_chart,
        Eoss_above_last=e_above,
        C0=float(c0),
        phi=float(phi),
        m=float(m),
        first_vds=float(vds[0]),
        first_coss=float(coss[0]),
        splice_rel_error=float(splice_rel_error),
        extrapolated_qoss_fraction=float(q_below / qoss) if qoss > 0 else 0.0,
        clipped_completion_active=clipped_completion_active,
        clip_boundary_vds=clip_boundary_vds,
        Qoss_clip_completed=float(q_clip_completed),
        Qoss_clip_visible_floor=float(q_clip_visible),
        Qoss_clip_added=float(q_clip_added),
        clipped_completion_fraction=float(max(0.0, q_clip_added) / qoss) if qoss > 0 else 0.0,
    )


def _partitioned_integral(values: np.ndarray, grid: np.ndarray, first_v: float, last_v: float) -> tuple[float, float, float]:
    below = 0.0
    chart = 0.0
    above = 0.0
    for idx in range(len(grid) - 1):
        dv = float(grid[idx + 1] - grid[idx])
        area = 0.5 * float(values[idx] + values[idx + 1]) * dv
        mid = 0.5 * float(grid[idx] + grid[idx + 1])
        if mid < first_v:
            below += area
        elif mid > last_v:
            above += area
        else:
            chart += area
    return below, chart, above


def _masked_midpoint_integral(values: np.ndarray, grid: np.ndarray, mask: np.ndarray) -> float:
    total = 0.0
    for idx in range(len(grid) - 1):
        mid_active = bool(mask[idx] or mask[idx + 1])
        if not mid_active:
            continue
        dv = float(grid[idx + 1] - grid[idx])
        total += 0.5 * float(values[idx] + values[idx + 1]) * dv
    return total


def _leading_clip_boundary_index(
    vds: np.ndarray, coss: np.ndarray, clip_ceiling: float | None, clip_rel_tol: float
) -> int | None:
    if clip_ceiling is None or clip_ceiling <= 0:
        return None
    clipped = coss >= float(clip_ceiling) * (1.0 - clip_rel_tol)
    if not bool(clipped[0]):
        return None
    idx = 0
    while idx < len(clipped) and bool(clipped[idx]):
        idx += 1
    if idx == 0 or idx >= len(coss) - 2:
        return None
    return idx


def validate_axis(
    vds: np.ndarray,
    coss: np.ndarray,
    vint: float,
    *,
    ds_Qoss: float | None = None,
    ds_Coer: float | None = None,
    ds_Cotr: float | None = None,
    tol: float = 0.10,
    max_extrapolated_qoss_fraction: float = 0.20,
    clip_ceiling: float | None = None,
) -> CossMetrics:
    metrics = coss_metrics(vds, coss, vint, clip_ceiling=clip_ceiling)
    failures: list[str] = []
    if metrics.extrapolated_qoss_fraction > max_extrapolated_qoss_fraction:
        failures.append(
            f"Qoss low-V extrapolation is unreliable: "
            f"{100 * metrics.extrapolated_qoss_fraction:.1f}% of extracted Qoss is below "
            f"the first chart point ({metrics.first_vds:.4g} V)"
        )
    for name, got, ref in (
        ("Qoss", metrics.Qoss, ds_Qoss),
        ("Co_er", metrics.Co_er, ds_Coer),
        ("Co_tr", metrics.Co_tr, ds_Cotr),
    ):
        if ref is None:
            continue
        rel = abs(got - ref) / ref
        if rel > tol:
            failures.append(f"{name}: extracted {got:.4g}, datasheet {ref:.4g}, rel {100 * rel:.1f}%")
    if failures:
        raise ValueError("axis/label calibration outside tolerance: " + "; ".join(failures))
    return metrics


def _smoke_test() -> None:
    v = np.linspace(0.1, 80.0, 400)
    coss = 800.0 / np.sqrt(1.0 + v)
    truth = coss_metrics(v, coss, 40.0)
    analytic_q = 1600.0 * (math.sqrt(41.0) - 1.0)
    if abs(truth.Qoss - analytic_q) / analytic_q >= 1e-3:
        raise AssertionError(f"Qoss {truth.Qoss:.3f} != analytic {analytic_q:.3f}")
    if truth.splice_rel_error >= 1e-12:
        raise AssertionError(f"splice discontinuity {truth.splice_rel_error:.3g}")

    # Model-mismatched data: the low-V fit cannot match every sample perfectly,
    # but the anchored C0 formulation must still meet the first chart point.
    warped = coss * (1.0 + 0.05 * np.sin(v / 7.0))
    warped_metrics = coss_metrics(v, warped, 40.0)
    if warped_metrics.splice_rel_error >= 1e-12:
        raise AssertionError(f"warped splice discontinuity {warped_metrics.splice_rel_error:.3g}")

    fine = coss_metrics(v, warped, 40.0, n=200000)
    if abs(warped_metrics.Qoss - fine.Qoss) / fine.Qoss > 1e-3:
        raise AssertionError(f"warped Qoss {warped_metrics.Qoss:.3f} too far from fine reference {fine.Qoss:.3f}")

    print(f"smoke-test PASS: Qoss={truth.Qoss:.1f} vs analytic {analytic_q:.1f} pC")
    print(f"warped splice PASS: rel={warped_metrics.splice_rel_error:.3g}, Qoss={warped_metrics.Qoss:.1f} pC")


if __name__ == "__main__":
    _smoke_test()
