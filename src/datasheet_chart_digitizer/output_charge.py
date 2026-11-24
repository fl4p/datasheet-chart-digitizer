"""Digitize Qoss(VDS) "Typ. output charge" diagrams and derive large-signal Coss_eff(V).

Some datasheets print the output charge as its own diagram (Infineon OptiMOS 8
Diagram 12, CoolMOS S7 / CoolSiC Diagram 17).  On OptiMOS 8 that diagram and the
Qoss table row agree with each other but NOT with the integral of the 1 MHz Coss
chart (about 0.386x on every die; fl4p/datasheet-chart-digitizer#17).  The Coss chart
is a small-signal measurement; the Qoss diagram is the large-signal charge.  Both are
real data and serve different consumers, so this plugin publishes the diagram as its
own evidence:

  * Qoss(V) traced from the vector diagram (linear/linear, single curve);
  * Coss_eff(V) = dQoss/dV as (Vds_V, pF) knots, for charge/energy loss models;
  * a verdict that is "pass" only when every gate below holds.

Gates (unevaluable never passes):

  * vector trace (raster-figure traces are "unverified" until overlays are reviewed);
  * the served axis grid check is not unverified;
  * exactly one printed charge unit (pC/nC/uC) on the panel;
  * the trace starts at the origin (first V <= 2% of span, Q there <= 3% of max);
  * the trace is non-decreasing (no drop larger than 1% of max);
  * the table Qoss reference exists and the diagram agrees at its Vds within 5%;
  * the Coss_eff knots, integrated the way a consumer will (linear between knots),
    reproduce the diagram's Qoss at every knot within 3% of Qoss(max V).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from pathlib import Path

import cv2
import numpy as np

from . import served_axis_guard
from .breakdown_voltage import _draw_overlay, calibrated_temperature_trace
from .capacitance_refs import output_charge_reference_to_json, parse_output_charge_reference

ANCHOR_TOL = 0.05
EFF_INTEGRAL_TOL = 0.03
# Consumed-integral gate: wherever a consumer integrates the served knots from 0 V, its
# charge Q(V) and energy E(V) = integral v*C dv must match the traced diagram within this
# fraction of the LOCAL value plus this fraction of the full-scale value (the floor keeps
# the first few volts, where Q and E are tiny, from refusing on pixel noise).
CONSUMED_LOCAL_TOL = 0.05
CONSUMED_FLOOR = 0.01
ORIGIN_V_FRACTION = 0.02
ORIGIN_Q_FRACTION = 0.03
MONOTONE_DROP_FRACTION = 0.01
# Worst-case slope error allowed: interior knots, and knots whose fit window is cut
# by the end of the trace (one-sided fits are 2-4x more sensitive; those knots carry
# little charge, and the absolute integral gate still bounds the total).
DERIV_REL_TOL = 0.10
DERIV_REL_TOL_EDGE = 0.25
MAX_HALF_WINDOW = 0.10
# Knot voltages as fractions of the traced span: dense at low V, where dQ/dV is
# steepest and a linear chord between sparse knots would misstate the charge.
KNOT_FRACTIONS = (
    0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.125, 0.15, 0.20, 0.25, 0.30,
    0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0,
)

_TITLE_RE = re.compile(r"output\s+charge", re.IGNORECASE)
_EXCLUDE_RE = re.compile(r"gate|reverse|recovery|Q\s*_?\s*(?:g|rr)\b", re.IGNORECASE)
_UNIT_PC = {"pC": 1.0, "nC": 1e3, "uC": 1e6}
_UNIT_RE = re.compile(r"\[\s*([pnuµμ])\s*C\s*\]")


def is_output_charge_chart(chart: dict) -> bool:
    """A Qoss(VDS) diagram: titled "output charge", not a gate/recovery charge panel."""
    title = str(chart.get("title") or "")
    if not _TITLE_RE.search(title) or _EXCLUDE_RE.search(title):
        return False
    return chart.get("kind") in {"chart", "output_charge"}


def charge_unit_scale_pc(text: str) -> tuple[str, float] | None:
    """Exactly one bracketed charge unit, e.g. "Qoss [nC]"; mixed or none -> None."""
    units = {
        {"p": "pC", "n": "nC"}.get(m.group(1), "uC")
        for m in _UNIT_RE.finditer(text)
    }
    if len(units) != 1:
        return None
    unit = units.pop()
    return unit, _UNIT_PC[unit]


def _fit_slope(x: np.ndarray, q: np.ndarray, deg: int) -> tuple[float, np.ndarray]:
    X = np.vander(x, deg + 1, increasing=True)
    w = np.linalg.pinv(X)[1]
    return float(w @ q), w


def _window_slope(vw: np.ndarray, qw: np.ndarray, kv: float, deg: int,
                  q_err: float, v_err: float) -> tuple[float, float, float]:
    """Slope at kv of a least-squares polynomial through the ORIGINAL samples of a
    window, its worst-case quantization error, and a model-bias estimate.

    The slope is a linear functional of the samples, s = w . q.  With every sample
    off by at most q_err in charge and v_err in voltage (half a pixel each), and the
    trace locally of TRUE slope t, each sample's charge is off by at most
    q_err + |t|*v_err, so |s - t| <= (q_err + |t|*v_err) * S with S = sum|w| (the
    fit's real sensitivity: 1.5x the naive 1/W for a line, about 5.9/W at the end of
    a one-sided quadratic).  t is unknown; with |t| <= |s| + e this solves to
    e <= (q_err + |s|*v_err) * S / (1 - v_err*S), infinite when v_err*S >= 1.
    ASSUMPTION: the trace is locally linear within half a pixel of each sample (one
    slope t covers every displacement).  Curvature on that sub-pixel scale is not
    bounded; a drawn datasheet curve has none to speak of, but this is a worst case
    only under that assumption.

    Bias: the same window refitted two degrees higher; the FULL slope difference is
    returned and charged to the uncertainty (never reduced by the refit's own noise
    bound, so more allowed noise cannot make a fit look better).  It catches structure
    the higher-order fit resolves; a feature narrower than the window that neither
    polynomial resolves is averaged, not detected (see coss_eff_knots)."""
    x = vw - kv
    slope, w = _fit_slope(x, qw, deg)
    sens = float(np.sum(np.abs(w)))
    denom = 1.0 - v_err * sens
    bound = (q_err + abs(slope) * v_err) * sens / denom if denom > 0 else math.inf
    bias = 0.0
    if len(x) >= deg + 5:
        s_hi, _ = _fit_slope(x, qw, deg + 2)
        bias = abs(s_hi - slope)
    return slope, bound, bias


def coss_eff_knots(v: np.ndarray, q_pc: np.ndarray, extra_v: tuple[float, ...] = (),
                   q_quantum_pc: float = 0.0, v_quantum_v: float = 0.0) -> list:
    """(Vds_V, Coss_eff_pF, relative_uncertainty, edge_window, half_window_V) knots
    from a monotone Qoss(V) trace.

    Each slope is a least-squares polynomial fit to the traced samples in a window
    around the knot.  Windows are tried from +/-1.5% of the span upward (x1.5 each, to
    at most +/-10%); the knot takes the NARROWEST window whose relative uncertainty,
    (worst-case quantization bound + full high-order disagreement) / slope, is within
    tolerance.  For a FIXED window, the uncertainty only grows with the declared
    quantum, so the set of windows a knot may use only shrinks.  The panel verdict is
    not monotone in the declared quantum: a larger quantum can select a wider window
    whose curve meets the integral gates better.  The quantum is not a free input,
    though: it is one pixel of the calibrated axes and is bound to the points by the
    evidence digest.

    What a knot means: the derivative of Qoss resolved at its window (half_window_V).
    Structure narrower than the window is averaged, not served pointwise.  Local fits
    do NOT by themselves conserve the charge or energy of the assembled curve, so
    evaluate_trace gates what a consumer integrates: Q(V) and E(V) from 0 V at every
    traced sample, within CONSUMED_LOCAL_TOL of the local value plus CONSUMED_FLOOR of
    full scale.

    In a symmetric window a line and a quadratic give the same slope.  Where the
    window is cut by V0 (Coss falls fastest there) a line reads the window's average
    slope, so a quadratic is fitted instead.

    When the trace starts above 0 V a knot at 0 V holds the first slope, the same
    zero-hold convention the dslib exporter applies to every curve, so the knots
    integrate from 0 V like a consumer's.
    """
    v0, v1 = float(v[0]), float(v[-1])
    span = v1 - v0
    q_err = 0.5 * q_quantum_pc
    v_err = 0.5 * v_quantum_v
    extra = {round(float(x), 6) for x in extra_v if v0 <= x <= v1}
    near = 0.003 * span
    knots_v = sorted(extra | {
        kv for kv in (round(v0 + f * span, 6) for f in KNOT_FRACTIONS)
        if kv in (v0, v1) or all(abs(kv - x) > near for x in extra)
    })
    out = []
    for kv in knots_v:
        half = 0.015 * span
        best: tuple[float, float, bool, float] | None = None
        while True:
            deg = 2 if kv - half < v0 else 1
            edge = kv - half < v0 or kv + half > v1
            tol = DERIV_REL_TOL_EDGE if edge else DERIV_REL_TOL
            m = (v >= kv - half) & (v <= kv + half)
            if int(np.sum(m)) >= deg + 3:
                slope, bound, bias = _window_slope(v[m], q_pc[m], kv, deg, q_err, v_err)
                rel = (bound + bias) / slope if slope > 0 else math.inf
                if best is None or rel < best[1]:
                    best = (slope, rel, edge, half)
                if rel <= tol:
                    best = (slope, rel, edge, half)
                    break
            if half >= MAX_HALF_WINDOW * span:
                break
            half = min(half * 1.5, MAX_HALF_WINDOW * span)
        slope, rel, edge, half_used = best if best is not None else (math.nan, math.inf, True, math.nan)
        out.append((float(kv), slope, rel, edge, half_used))
    if v0 > 0:
        out.insert(0, (0.0, out[0][1], out[0][2], True, out[0][4]))
    return out


def integrate_knots(knots: list, v_query: np.ndarray) -> np.ndarray:
    """Charge (pC) of linearly interpolated (V, pF) knots from the first knot to v_query."""
    kv = np.array([k[0] for k in knots])
    kc = np.array([k[1] for k in knots])
    cum = np.concatenate([[0.0], np.cumsum(0.5 * (kc[1:] + kc[:-1]) * np.diff(kv))])
    out = []
    for vq in np.atleast_1d(v_query):
        i = int(np.clip(np.searchsorted(kv, vq, side="right") - 1, 0, len(kv) - 2))
        c_at = np.interp(vq, kv, kc)
        out.append(cum[i] + 0.5 * (kc[i] + c_at) * (vq - kv[i]))
    return np.array(out)


def consumed_integral_errors(knots: list, v: np.ndarray, q_pc: np.ndarray) -> dict:
    """Q(V) and E(V) integrated from 0 V over the served knots vs the traced diagram,
    on a grid holding every knot and every traced sample (C is linear between knots,
    so Q is exact; E = integral v*C dv by trapezoid on that grid).  The trace's own
    E(V) = V*Q(V) - integral_0^V Q dv needs no derivative; below its first sample the
    trace is completed linearly from (0, 0).  Returns the worst error of each, as a
    multiple of its tolerance (<= 1 passes), and where it occurs."""
    kv = np.array([k[0] for k in knots], float)
    kc = np.array([k[1] for k in knots], float)
    tv = np.concatenate([[0.0], v]) if v[0] > 0 else v
    tq = np.concatenate([[0.0], q_pc]) if v[0] > 0 else q_pc
    grid = np.unique(np.concatenate([tv, kv[(kv >= 0) & (kv <= tv[-1])]]))
    dv = np.diff(grid)
    c = np.interp(grid, kv, kc)
    q_k = np.concatenate([[0.0], np.cumsum(0.5 * (c[1:] + c[:-1]) * dv)])
    vc = grid * c
    e_k = np.concatenate([[0.0], np.cumsum(0.5 * (vc[1:] + vc[:-1]) * dv)])
    q_t = np.interp(grid, tv, tq)
    q_t = q_t - q_t[0]
    e_t = grid * q_t - np.concatenate([[0.0], np.cumsum(0.5 * (q_t[1:] + q_t[:-1]) * dv)])
    out = {}
    for name, got, want in (("q", q_k, q_t), ("e", e_k, e_t)):
        tol = CONSUMED_LOCAL_TOL * np.abs(want) + CONSUMED_FLOOR * float(np.max(np.abs(want)))
        ratio = np.abs(got - want) / np.where(tol > 0, tol, np.inf)
        i = int(np.argmax(ratio))
        out[name] = {"worst_over_tol": round(float(ratio[i]), 4), "at_v": round(float(grid[i]), 4),
                     "rel_error": (round(float(got[i] / want[i] - 1.0), 4)
                                   if want[i] != 0 else None)}
    return out


def _finite_positive(x: object) -> float | None:
    try:
        f = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def evaluate_trace(v: np.ndarray, q_pc: np.ndarray, reference: dict | None,
                   q_quantum_pc: float = 0.0, v_quantum_v: float = 0.0) -> dict:
    """Gate a traced Qoss(V) curve and derive Coss_eff knots. Pure; testable.

    Charges are ABSOLUTE: the knots start at 0 V and their integral from 0 V must
    reproduce the diagram's Qoss(V) at every knot and the table Qoss at its Vds."""
    reasons: list[str] = []
    order = np.argsort(v)
    v, q_pc = np.asarray(v, float)[order], np.asarray(q_pc, float)[order]
    if len(v) < 20 or not np.all(np.isfinite(v)) or not np.all(np.isfinite(q_pc)):
        return {"status": "rejected", "reasons": ["qoss_trace_too_short_or_nonfinite"]}
    span = float(v[-1] - v[0])
    q_max = float(np.max(q_pc))
    if span <= 0 or q_max <= 0 or float(v[0]) < 0:
        return {"status": "rejected", "reasons": ["qoss_trace_degenerate"]}
    if float(v[0]) > ORIGIN_V_FRACTION * float(v[-1]) or abs(float(q_pc[0])) > ORIGIN_Q_FRACTION * q_max:
        reasons.append(
            f"qoss_trace_not_from_origin:first V {v[0]:.3g}, Q {q_pc[0] / q_max:+.1%} of max")
    running = np.maximum.accumulate(q_pc)
    worst_drop = float(np.max(running - q_pc)) / q_max
    if worst_drop > MONOTONE_DROP_FRACTION:
        reasons.append(f"qoss_not_monotone:drop {worst_drop:.1%} of max")
    q_mono = running

    ref_q = _finite_positive((reference or {}).get("qoss_pc"))
    ref_v = _finite_positive((reference or {}).get("vint_v"))
    extra = (ref_v,) if ref_v is not None else ()
    knots3 = coss_eff_knots(v, q_mono, extra, q_quantum_pc, v_quantum_v)
    knots = [(k[0], k[1]) for k in knots3]
    kv = np.array([k[0] for k in knots])
    q_target = np.where(kv < v[0], 0.0, np.interp(kv, v, q_mono))
    q_int = integrate_knots(knots, kv)
    eff_err = float(np.max(np.abs(q_int - q_target))) / q_max
    finite_knots = all(math.isfinite(c) and c > 0 for _, c in knots)
    consumed = consumed_integral_errors(knots, v, q_mono) if finite_knots else None
    if consumed is not None:
        for name, label in (("q", "charge"), ("e", "energy")):
            w = consumed[name]
            if not w["worst_over_tol"] <= 1.0:
                reasons.append(
                    f"coss_eff_consumed_{label}_mismatch:{w['rel_error']} at {w['at_v']:g} V "
                    f"({w['worst_over_tol']:.2f}x tol {CONSUMED_LOCAL_TOL:.0%} local + "
                    f"{CONSUMED_FLOOR:.0%} full scale)")

    anchor: dict[str, object] | None = None
    if ref_q is None or ref_v is None:
        reasons.append("qoss_table_reference_unavailable")
    elif not (v[0] <= ref_v <= v[-1]):
        reasons.append(f"qoss_reference_vds_outside_trace:{ref_v:g} V")
    else:
        got = float(np.interp(ref_v, v, q_mono))
        rel = got / ref_q - 1.0
        knots_q = float(integrate_knots(knots, np.array([ref_v]))[0])
        rel_knots = knots_q / ref_q - 1.0
        anchor = {"vds_v": ref_v, "table_pc": ref_q, "diagram_pc": round(got, 2),
                  "rel_error": round(rel, 4), "coss_eff_integral_pc": round(knots_q, 2),
                  "coss_eff_rel_error": round(rel_knots, 4)}
        if not abs(rel) <= ANCHOR_TOL:
            reasons.append(f"qoss_diagram_anchor_mismatch:{rel:+.1%} (tol {ANCHOR_TOL:.0%})")
        if not abs(rel_knots) <= ANCHOR_TOL:
            reasons.append(
                f"coss_eff_anchor_mismatch:{rel_knots:+.1%} from 0 V (tol {ANCHOR_TOL:.0%})")

    unresolved = [kv_ for kv_, c, rel, edge, _ in knots3
                  if not (math.isfinite(c) and c > 0
                          and rel <= (DERIV_REL_TOL_EDGE if edge else DERIV_REL_TOL))]
    if unresolved:
        reasons.append(
            f"coss_eff_unresolved_by_diagram:{len(unresolved)} knots "
            f"{unresolved[0]:g}-{unresolved[-1]:g} V: worst-case quantization error above "
            f"{DERIV_REL_TOL:.0%} ({DERIV_REL_TOL_EDGE:.0%} at the trace ends) in every window")
    if not eff_err <= EFF_INTEGRAL_TOL:
        reasons.append(
            f"coss_eff_integral_mismatch:{eff_err:.1%} of Qoss max (tol {EFF_INTEGRAL_TOL:.0%})")
    return {
        "status": "rejected" if reasons else "pass",
        "reasons": reasons,
        "anchor": anchor,
        "coss_eff_curve": [(round(kv_, 4), float(f"{c:.4g}") if math.isfinite(c) else None)
                           for kv_, c in knots],
        "coss_eff_rel_uncertainty": [round(min(k[2], 9.99), 4) for k in knots3],
        "coss_eff_edge_window": [bool(k[3]) for k in knots3],
        "coss_eff_half_window_v": [round(k[4], 4) if math.isfinite(k[4]) else None
                                   for k in knots3],
        "coss_eff_integral_max_error": round(eff_err, 5),
        "coss_eff_consumed_integrals": consumed,
        "quantum": {"q_pc": q_quantum_pc, "v_v": v_quantum_v},
        "method_gates": {"deriv_rel_tol": DERIV_REL_TOL,
                         "deriv_rel_tol_edge": DERIV_REL_TOL_EDGE},
        "vds_range_v": [round(float(v[0]), 4), round(float(v[-1]), 4)],
        "qoss_max_pc": round(q_max, 2),
        "monotone_worst_drop": round(worst_drop, 5),
    }


def read_points_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """(VDS_V, Qoss_pC) exactly as written by process_chart; the verdict is computed
    from these values so the exporter can recompute it bit for bit."""
    with Path(path).open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return (np.array([float(r["VDS_V"]) for r in rows]),
            np.array([float(r["Qoss_nC"]) * 1e3 for r in rows]))


def file_sha256(path: Path | None) -> str | None:
    if path is None or not Path(path).is_file():
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evidence_sha256(points_path: Path, record: dict) -> str | None:
    """Digest binding the traced points to the metadata the verdict is recomputed
    with: the points CSV bytes plus the pixel quanta, the axis calibration (incl. the
    grid-check payload) and the trace method, as canonical JSON.  Editing any one of
    them alone breaks the digest.  None when the points file is missing."""
    path = Path(points_path)
    if not path.is_file():
        return None
    meta = {k: record.get(k) for k in ("quantum", "calibration", "method")}
    h = hashlib.sha256(path.read_bytes())
    h.update(json.dumps(meta, sort_keys=True, separators=(",", ":"), default=str).encode())
    return h.hexdigest()


def producer_sha256() -> str:
    """Fingerprint of the digitizer code: every module of this package (the tracer,
    axis calibration and table parsers all feed the verdict).  The exporter refuses
    a verdict produced by different code."""
    here = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for path in sorted(here.glob("*.py")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def process_chart(chart: dict, crop_path: Path, out_dir: Path, rel_stem: Path,
                  datasheet_root: Path) -> dict:
    traced = calibrated_temperature_trace(chart, crop_path, "Y axis (Qoss)", "X axis (VDS)")
    _, grid_unverified = served_axis_guard.problems(traced.grid_checks)
    unit = charge_unit_scale_pc(str(chart.get("text") or ""))
    data = sorted((traced.x_axis.value(x), traced.y_axis.value(y)) for x, y in traced.points_px)
    v = np.array([p[0] for p in data])
    q_axis = np.array([p[1] for p in data])
    reference = output_charge_reference_to_json(
        parse_output_charge_reference(str(chart["part"]), datasheet_root))
    # one pixel of the traced (integer-pixel) points, in charge and voltage units
    q_px = abs(traced.y_axis.value(1.0) - traced.y_axis.value(0.0))
    v_px = abs(traced.x_axis.value(1.0) - traced.x_axis.value(0.0))
    scale = unit[1] if unit else float("nan")
    csv_path = out_dir / "points" / rel_stem.parent / f"{rel_stem.name}.qoss_points.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["VDS_V", "Qoss_nC"])
        for vv, qq in zip(v, q_axis):
            writer.writerow([f"{vv:.4f}", f"{qq * scale / 1e3:.4f}"])
    if unit is None:
        result = {"status": "rejected", "reasons": ["qoss_axis_unit_unresolved"]}
    else:
        # evaluate the points as persisted, so the exporter's recomputation is identical
        v_csv, q_csv = read_points_csv(csv_path)
        result = evaluate_trace(v_csv, q_csv, reference,
                                q_quantum_pc=q_px * unit[1], v_quantum_v=v_px)
    if result["status"] == "pass" and (grid_unverified or traced.trace_method != "vector"):
        result["status"] = "unverified"
        if grid_unverified:
            result["reasons"] = ["axis grid check unverified: " + "; ".join(grid_unverified)]
        else:
            result["reasons"] = ["raster-figure trace: review the overlay before use"]
    overlay = _draw_overlay(traced.image, traced.plot, traced.points_px,
                            traced.x_axis, traced.y_axis,
                            x_labels_below=traced.axis_text_source == "native")
    overlay_path = out_dir / "overlays" / rel_stem.parent / f"{rel_stem.name}.qoss_overlay.png"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)

    result.update({
        "method": traced.trace_method,
        "frame_method": traced.frame_method,
        "axis_unit": unit[0] if unit else None,
        "output_charge_reference": reference,
        "provenance": {
            "pdf_sha256": file_sha256(Path(str(chart["pdf"]))),
            "producer_sha256": producer_sha256(),
        },
        "calibration": {
            "x_ticks": len(traced.x_axis.ticks), "x_resid": round(traced.x_axis.resid, 6),
            "y_ticks": len(traced.y_axis.ticks), "y_resid": round(traced.y_axis.resid, 6),
            "grid_check": served_axis_guard.payload(traced.grid_checks),
        },
        "n_points": len(data),
        # relative to the manifest directory (out_dir), so a moved bundle stays valid
        "csv": csv_path.relative_to(out_dir).as_posix(),
        "overlay": str(overlay_path),
    })
    result["evidence_sha256"] = evidence_sha256(csv_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("charts_json", type=Path, help="charts.json from `dsdig find`")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: alongside charts.json)")
    parser.add_argument("--datasheet-root", type=Path, default=None,
                        help="directory holding <part>.pdf.nop.csv anchor tables "
                             "(default: each chart's PDF directory)")
    args = parser.parse_args()

    base_dir = args.charts_json.parent
    out_dir = args.out or base_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    charts = json.loads(args.charts_json.read_text())
    results: list[dict] = []
    errors: list[dict] = []
    for chart in charts:
        if not is_output_charge_chart(chart):
            continue
        crop_rel = Path(chart["crop_png"])
        print(f"digitize {chart['part']} diagram {chart['diagram']}: {crop_rel}")
        root = args.datasheet_root or Path(str(chart["pdf"])).parent
        try:
            result = process_chart(chart, base_dir / crop_rel, out_dir,
                                   crop_rel.with_suffix(""), root)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors.append({"part": chart.get("part"), "crop": str(crop_rel), "error": str(exc)})
            continue
        result.update({"part": chart["part"], "page": chart["page"],
                       "diagram": chart["diagram"], "pdf": chart["pdf"],
                       "crop": str((base_dir / crop_rel).resolve())})
        results.append(result)
        a = result.get("anchor") or {}
        anchor_txt = (f"{a['diagram_pc'] / 1e3:.1f} nC vs table {a['table_pc'] / 1e3:.1f} nC "
                      f"({a['rel_error']:+.1%}) at {a['vds_v']:g} V") if a else "no table anchor"
        print(f"  {result['status'].upper()}: {anchor_txt}"
              + (f"; {'; '.join(result['reasons'])}" if result["reasons"] else ""))
    path = out_dir / "output_charge_digitization.json"
    path.write_text(json.dumps({"panels": results, "errors": errors}, indent=2) + "\n")
    print(f"wrote {path}")
    if errors:
        raise SystemExit(1)
    if not results:
        print("ERROR: no output-charge (Qoss vs VDS) panels in the index — nothing digitized")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
