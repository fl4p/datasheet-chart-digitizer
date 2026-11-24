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
import json
import re
from pathlib import Path

import cv2
import numpy as np

from . import served_axis_guard
from .breakdown_voltage import _draw_overlay, calibrated_temperature_trace
from .capacitance_refs import output_charge_reference_to_json, parse_output_charge_reference

ANCHOR_TOL = 0.05
EFF_INTEGRAL_TOL = 0.03
ORIGIN_V_FRACTION = 0.02
ORIGIN_Q_FRACTION = 0.03
MONOTONE_DROP_FRACTION = 0.01
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


def coss_eff_knots(v: np.ndarray, q_pc: np.ndarray, extra_v: tuple[float, ...] = ()) -> list:
    """(Vds_V, Coss_eff_pF) knots from a monotone Qoss(V) trace.

    The slope at each knot is the derivative of a least-squares polynomial through a uniform resample of the
    trace within +/-1.5% of the span (one-sided at the ends), so polyline corners and
    end caps of the vector path do not become spikes.  Knots are returned at
    KNOT_FRACTIONS of the span plus any explicitly requested voltages.
    """
    v0, v1 = float(v[0]), float(v[-1])
    grid = np.linspace(v0, v1, 4001)
    qg = np.interp(grid, v, q_pc)
    half = 0.015 * (v1 - v0)
    extra = {round(float(x), 6) for x in extra_v if v0 <= x <= v1}
    near = 0.003 * (v1 - v0)
    knots_v = sorted(extra | {
        kv for kv in (round(v0 + f * (v1 - v0), 6) for f in KNOT_FRACTIONS)
        if kv in (v0, v1) or all(abs(kv - x) > near for x in extra)
    })
    out = []
    for kv in knots_v:
        m = (grid >= kv - half) & (grid <= kv + half)
        # Polynomial in (V - kv); its linear coefficient is dQ/dV at kv.  In a
        # symmetric window a line and a quadratic give the same slope.  At the low-V
        # end, where Coss falls fastest, the window is one-sided and a line reads the
        # window's average slope (23% low on a sqrt-law die), so fit a quadratic there.
        # At the high-V end Q is nearly linear and the pixel staircase of the trace
        # makes a quadratic noisy, so keep the line.
        deg = 2 if kv - half < v0 else 1
        coeffs = np.polyfit(grid[m] - kv, qg[m], deg)
        out.append((float(kv), float(coeffs[-2])))
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


def evaluate_trace(v: np.ndarray, q_pc: np.ndarray, reference: dict | None) -> dict:
    """Gate a traced Qoss(V) curve and derive Coss_eff knots. Pure; testable."""
    reasons: list[str] = []
    order = np.argsort(v)
    v, q_pc = np.asarray(v, float)[order], np.asarray(q_pc, float)[order]
    if len(v) < 20 or not np.all(np.isfinite(v)) or not np.all(np.isfinite(q_pc)):
        return {"status": "rejected", "reasons": ["qoss_trace_too_short_or_nonfinite"]}
    span = float(v[-1] - v[0])
    q_max = float(np.max(q_pc))
    if span <= 0 or q_max <= 0:
        return {"status": "rejected", "reasons": ["qoss_trace_degenerate"]}
    if float(v[0]) > ORIGIN_V_FRACTION * float(v[-1]) or float(q_pc[0]) > ORIGIN_Q_FRACTION * q_max:
        reasons.append(
            f"qoss_trace_not_from_origin:first V {v[0]:.3g}, Q {q_pc[0] / q_max:.1%} of max")
    running = np.maximum.accumulate(q_pc)
    worst_drop = float(np.max(running - q_pc)) / q_max
    if worst_drop > MONOTONE_DROP_FRACTION:
        reasons.append(f"qoss_not_monotone:drop {worst_drop:.1%} of max")
    q_mono = running

    anchor: dict[str, object] | None = None
    ref_q = (reference or {}).get("qoss_pc")
    ref_v = (reference or {}).get("vint_v")
    if not ref_q or ref_v is None:
        reasons.append("qoss_table_reference_unavailable")
    elif not (v[0] <= float(ref_v) <= v[-1]):
        reasons.append(f"qoss_reference_vds_outside_trace:{ref_v:g} V")
    else:
        got = float(np.interp(float(ref_v), v, q_mono))
        rel = got / float(ref_q) - 1.0
        anchor = {"vds_v": float(ref_v), "table_pc": float(ref_q),
                  "diagram_pc": round(got, 2), "rel_error": round(rel, 4)}
        if abs(rel) > ANCHOR_TOL:
            reasons.append(f"qoss_diagram_anchor_mismatch:{rel:+.1%} (tol {ANCHOR_TOL:.0%})")

    extra = (float(ref_v),) if ref_v is not None else ()
    knots = coss_eff_knots(v, q_mono, extra)
    kv = np.array([k[0] for k in knots])
    q_knots = np.interp(kv, v, q_mono) - float(np.interp(kv[0], v, q_mono))
    q_int = integrate_knots(knots, kv)
    eff_err = float(np.max(np.abs(q_int - q_knots))) / q_max
    # dQ/dV below what the diagram resolves reads as a flat line (slope ~0).  The
    # small-signal Coss chart is the better evidence there; refuse rather than serve 0.
    resolvable = 0.002 * q_max / span
    flat = [kv_ for kv_, c in knots if c <= resolvable]
    if flat:
        reasons.append(
            f"coss_eff_unresolved_by_diagram:{len(flat)} knots {flat[0]:g}-{flat[-1]:g} V "
            f"below {resolvable:.3g} pF")
    if eff_err > EFF_INTEGRAL_TOL:
        reasons.append(
            f"coss_eff_integral_mismatch:{eff_err:.1%} of Qoss max (tol {EFF_INTEGRAL_TOL:.0%})")
    return {
        "status": "rejected" if reasons else "pass",
        "reasons": reasons,
        "anchor": anchor,
        "coss_eff_curve": [(round(kv_, 4), float(f"{c:.4g}")) for kv_, c in knots],
        "coss_eff_integral_max_error": round(eff_err, 5),
        "vds_range_v": [round(float(v[0]), 4), round(float(v[-1]), 4)],
        "qoss_max_pc": round(q_max, 2),
        "monotone_worst_drop": round(worst_drop, 5),
    }


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
    if unit is None:
        result = {"status": "rejected", "reasons": ["qoss_axis_unit_unresolved"]}
    else:
        result = evaluate_trace(v, q_axis * unit[1], reference)
    if result["status"] == "pass" and (grid_unverified or traced.trace_method != "vector"):
        result["status"] = "unverified"
        if grid_unverified:
            result["reasons"] = ["axis grid check unverified: " + "; ".join(grid_unverified)]
        else:
            result["reasons"] = ["raster-figure trace: review the overlay before use"]

    scale = unit[1] if unit else float("nan")
    csv_path = out_dir / "points" / rel_stem.parent / f"{rel_stem.name}.qoss_points.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["VDS_V", "Qoss_nC"])
        for vv, qq in zip(v, q_axis):
            writer.writerow([f"{vv:.4f}", f"{qq * scale / 1e3:.4f}"])
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
        "calibration": {
            "x_ticks": len(traced.x_axis.ticks), "x_resid": round(traced.x_axis.resid, 6),
            "y_ticks": len(traced.y_axis.ticks), "y_resid": round(traced.y_axis.resid, 6),
            "grid_check": served_axis_guard.payload(traced.grid_checks),
        },
        "n_points": len(data),
        "csv": str(csv_path),
        "overlay": str(overlay_path),
    })
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
