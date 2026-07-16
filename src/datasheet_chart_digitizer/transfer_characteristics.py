"""Digitize and fit MOSFET saturation transfer curves versus temperature.

Targets the Infineon ``Typ. transfer characteristics`` panels used by the
dcdc-tools curve/recon channel model: linear ``Id=f(Vgs)`` axes, normally two
curves at 25 C and 175 C, with the stated saturation condition
``|Vds| > 2 |Id| Rds(on)max``.

The fitted temperature law is deliberately relative to an externally supplied
25 C switching-law anchor.  The chart does not replace the exact gate-charge
``(Vpl, Id_gc)`` pivot.  At matched drain currents it fits

    delta_Vgs(I) = dVth + Vov_25_model(I) * (K_ratio**(-1/p) - 1)

which identifies ``dVth/dT`` and ``d(log K)/dT`` without forcing the absolute
25 C chart through a different anchor.  The absolute 25 C curve remains a
reported conflict check.

Every emitted panel is ``overlay-review-required``.  Downstream curation must
not mark coefficients verified until a human has checked the axis guides,
both extracted centerlines, temperature assignment, and fit summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .breakdown_voltage import NUM_RE, _calibrate, _vector_plot_frame, _words_in_crop_px
from .capacitance_traces import find_plot_box
from .capacitance_types import PlotBox, VectorEdge
from .capacitance_vector import _load_fitz, _resample_vector_trace_pixels, _vector_curve_edges
from .crop_transform import CropTransform

TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?\s*C", re.IGNORECASE)
MIN_RUN_X_SPAN = 0.30
MAX_TEMPCO_FIT_RMS_V = 0.05
# This is a conflict detector, not a demand that a scalar-anchored surrogate
# reproduce the whole typical transfer chart.  Tighten only after the first
# human-reviewed part set establishes an honest empirical band.
MAX_COLD_CONFLICT_RMS_V = 0.35
MAX_COLD_CONFLICT_ABS_V = 0.75


@dataclass(frozen=True)
class TransferCurve:
    tj_c: float
    points: list[tuple[float, float]]  # (Vgs_V, Id_A)


def _split_monotone_edge_runs(edges: list[VectorEdge], plot_width_pt: float) -> list[list[VectorEdge]]:
    """Split PDF line-segment streams into left-to-right curve runs.

    Infineon emits each short curve segment as a separate drawing.  The two
    curves share the zero-current baseline and cross near ZTC, so graph
    connected-components merge them.  Drawing order is nevertheless stable:
    one complete left-to-right curve, then X resets and the next begins.
    Splitting on that reset preserves crossings without guessing branches.
    """
    runs: list[list[VectorEdge]] = []
    current: list[VectorEdge] = []
    last_mid_x: float | None = None
    reset = max(0.8, 0.05 * plot_width_pt)
    for edge in edges:
        mid_x = 0.5 * (edge.p0[0] + edge.p1[0])
        if current and last_mid_x is not None and mid_x < last_mid_x - reset:
            runs.append(current)
            current = []
        current.append(edge)
        last_mid_x = mid_x
    if current:
        runs.append(current)
    return runs


def _run_points(run: list[VectorEdge], transform: CropTransform, plot: PlotBox) -> list[tuple[int, int]]:
    raw: list[tuple[int, int]] = []
    for edge in run:
        points = edge.points if edge.p1[0] >= edge.p0[0] else list(reversed(edge.points))
        pixels = [tuple(int(round(v)) for v in transform.to_px(x, y)) for x, y in points]
        raw.extend(pixels if not raw else pixels[1:])
    return _resample_vector_trace_pixels(raw, plot)


def _extract_two_curves(page, transform: CropTransform, plot: PlotBox, fitz) -> list[list[tuple[int, int]]]:
    p0 = transform.to_pt(plot.x0, plot.y0)
    p1 = transform.to_pt(plot.x1, plot.y1)
    rect = fitz.Rect(p0[0], p0[1], p1[0], p1[1])
    edges = _vector_curve_edges(page.get_drawings(), rect)
    candidates: list[list[tuple[int, int]]] = []
    for run in _split_monotone_edge_runs(edges, rect.width):
        points = _run_points(run, transform, plot)
        if len(points) < 8:
            continue
        span = max(x for x, _ in points) - min(x for x, _ in points)
        if span >= MIN_RUN_X_SPAN * plot.width:
            candidates.append(points)
    if len(candidates) != 2:
        raise RuntimeError(
            f"expected exactly 2 left-to-right transfer curves, found {len(candidates)}; "
            "do not guess temperature branches — verify the vector drawing order/crop"
        )
    return candidates


def _temperatures(text: str) -> list[float]:
    values = sorted({float(v) for v in TEMP_RE.findall(text)})
    if len(values) != 2:
        raise RuntimeError(f"expected exactly 2 temperature labels, found {values}")
    return values


def _assign_temperatures(
    curves: list[list[tuple[float, float]]], temperatures: list[float]
) -> list[TransferCurve]:
    """Assign cold/hot by the high-current branch, then require a ZTC crossing."""
    common_lo = max(points[0][0] for points in curves)
    common_hi = min(points[-1][0] for points in curves)
    if common_hi <= common_lo:
        raise RuntimeError("transfer curves have no common Vgs range")
    # Curves can end at the same top-frame current but at different Vgs.  Ranking
    # their last samples would then depend on clipping, not temperature.  Compare
    # at one shared high-Vgs coordinate instead.
    rank_vgs = common_lo + 0.90 * (common_hi - common_lo)
    ranked = sorted(
        curves,
        key=lambda pts: float(np.interp(rank_vgs, [v for v, _ in pts], [i for _, i in pts])),
        reverse=True,
    )
    cold, hot = ranked[0], ranked[1]
    lo = max(cold[0][0], hot[0][0])
    hi = min(cold[-1][0], hot[-1][0])
    grid = np.linspace(lo, hi, 200)
    cold_i = np.interp(grid, [v for v, _ in cold], [i for _, i in cold])
    hot_i = np.interp(grid, [v for v, _ in hot], [i for _, i in hot])
    diff = hot_i - cold_i
    meaningful = np.maximum(cold_i, hot_i) > 0.02 * max(float(cold_i.max()), float(hot_i.max()))
    diff = diff[meaningful]
    if len(diff) < 10 or not (np.any(diff > 0) and np.any(diff < 0)):
        raise RuntimeError(
            "the two transfer curves do not show the expected temperature-order reversal "
            "around ZTC; temperature assignment is untrusted"
        )
    return [TransferCurve(temperatures[0], cold), TransferCurve(temperatures[1], hot)]


def _inverse_vgs(points: list[tuple[float, float]], currents: np.ndarray) -> np.ndarray:
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


def fit_saturation_tempco(curves: list[TransferCurve], anchor: dict[str, float]) -> dict:
    """Fit dVth_eff/dT and d(log K)/dT around an exact 25 C anchor."""
    curves = sorted(curves, key=lambda curve: curve.tj_c)
    cold, hot = curves[0], curves[-1]
    tref = float(anchor.get("tref_c", 25.0))
    if abs(cold.tj_c - tref) > 1e-6:
        raise RuntimeError(f"cold transfer curve is {cold.tj_c:g} C, expected anchor Tref={tref:g} C")
    dt = hot.tj_c - cold.tj_c
    if dt <= 0:
        raise RuntimeError("transfer temperatures are not increasing")
    required = ("vth_eff_v", "k_a_per_vp", "p", "id_gc_a", "vpl_v")
    missing = [key for key in required if key not in anchor]
    if missing:
        raise RuntimeError(f"temperature fit anchor missing: {', '.join(missing)}")
    vth = float(anchor["vth_eff_v"])
    k = float(anchor["k_a_per_vp"])
    p = float(anchor["p"])
    id_gc = float(anchor["id_gc_a"])
    vpl = float(anchor["vpl_v"])
    if not (k > 0 and p > 1 and id_gc > 0 and vpl > vth):
        raise RuntimeError(f"nonphysical channel anchor: {anchor}")
    pivot_err = abs(k * (vpl - vth) ** p - id_gc) / id_gc
    if pivot_err > 1e-6:
        raise RuntimeError(
            f"25 C anchor does not reproduce (Vpl,Id_gc): relative error {pivot_err:.3g}"
        )

    max_common_i = min(max(i for _, i in cold.points), max(i for _, i in hot.points))
    i_lo = max(5.0, 0.10 * id_gc)
    i_hi = min(2.0 * id_gc, 0.85 * max_common_i)
    if i_hi <= 2.0 * i_lo:
        raise RuntimeError(
            f"insufficient matched-current span for temperature fit: {i_lo:g}..{i_hi:g} A"
        )
    currents = np.linspace(i_lo, i_hi, 120)
    v_cold = _inverse_vgs(cold.points, currents)
    v_hot = _inverse_vgs(hot.points, currents)
    delta = v_hot - v_cold
    ov_model = np.power(currents / k, 1.0 / p)
    design = np.column_stack((np.ones_like(ov_model), ov_model))
    d_vth, shape = np.linalg.lstsq(design, delta, rcond=None)[0]
    scale_root = 1.0 + float(shape)
    if scale_root <= 0:
        raise RuntimeError("temperature fit implies a nonpositive K scaling root")
    k_ratio = scale_root ** (-p)
    prediction = design @ np.array([d_vth, shape])
    residual = prediction - delta
    fit_rms = float(np.sqrt(np.mean(residual**2)))
    fit_max = float(np.max(np.abs(residual)))
    if fit_rms > MAX_TEMPCO_FIT_RMS_V:
        raise RuntimeError(
            f"compact Vth_eff/K temperature law misses matched-current gate shifts: "
            f"RMS {fit_rms:.3f} V > {MAX_TEMPCO_FIT_RMS_V:.3f} V"
        )

    cold_model_vgs = vth + ov_model
    cold_residual = cold_model_vgs - v_cold
    cold_rms = float(np.sqrt(np.mean(cold_residual**2)))
    cold_max = float(np.max(np.abs(cold_residual)))
    conflict = cold_rms > MAX_COLD_CONFLICT_RMS_V or cold_max > MAX_COLD_CONFLICT_ABS_V

    ztc_model = None
    if abs(shape) > 1e-12 and -float(d_vth) / float(shape) > 0:
        ztc_model = k * (-float(d_vth) / float(shape)) ** p
    # Locate the chart ZTC over the full reliable common-current span, not only
    # the operating-point fit window.  Several parts cross above 2*Id_gc; that
    # crossing is still valuable validation of the two-parameter law.
    ztc_currents = np.linspace(max(1.0, 0.02 * id_gc), 0.95 * max_common_i, 400)
    ztc_delta = _inverse_vgs(hot.points, ztc_currents) - _inverse_vgs(cold.points, ztc_currents)
    sign = np.sign(ztc_delta)
    crossings = np.flatnonzero(sign[:-1] * sign[1:] <= 0)
    ztc_chart = None
    if len(crossings):
        j = int(crossings[0])
        pair_delta = [ztc_delta[j], ztc_delta[j + 1]]
        pair_current = [ztc_currents[j], ztc_currents[j + 1]]
        if pair_delta[0] > pair_delta[1]:
            pair_delta.reverse()
            pair_current.reverse()
        ztc_chart = float(np.interp(0.0, pair_delta, pair_current))

    return {
        "tref_c": tref,
        "thot_c": hot.tj_c,
        "fit_current_range_a": [round(i_lo, 3), round(i_hi, 3)],
        "d_vth_eff_v_per_k": float(d_vth) / dt,
        "d_log_k_per_k": math.log(k_ratio) / dt,
        "k_ratio_hot_to_ref": k_ratio,
        "matched_shift_fit_rms_v": fit_rms,
        "matched_shift_fit_max_v": fit_max,
        "cold_anchor_check_rms_v": cold_rms,
        "cold_anchor_check_max_v": cold_max,
        "cold_anchor_conflict": conflict,
        "ztc_chart_a": ztc_chart,
        "ztc_model_a": ztc_model,
        "anchor": dict(anchor),
    }


def _draw_overlay(
    image: np.ndarray,
    plot: PlotBox,
    pixel_curves: list[list[tuple[int, int]]],
    curves: list[TransferCurve],
    x_axis,
    y_axis,
    fit: dict | None,
) -> np.ndarray:
    overlay = image.copy()
    cv2.rectangle(overlay, (plot.x0, plot.y0), (plot.x1, plot.y1), (0, 180, 0), 1)
    by_temperature = sorted(zip(curves, pixel_curves), key=lambda item: item[0].tj_c)
    colors = [(0, 0, 255), (255, 80, 0)]
    for (curve, points), color in zip(by_temperature, colors):
        for a, b in zip(points, points[1:]):
            cv2.line(overlay, a, b, color, 2, cv2.LINE_AA)
        cv2.putText(
            overlay,
            f"{curve.tj_c:g} C extracted",
            (plot.x0 + 10, plot.y0 + 24 + 22 * colors.index(color)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    # Self-contained calibration evidence: do not make the reviewer infer which
    # original glyph each magenta guide used. Label every selected tick and name
    # both physical axes directly on the plot.
    tick_color = (180, 0, 180)
    for value, px in x_axis.ticks:
        x = int(round(px))
        cv2.drawMarker(overlay, (x, plot.y1), tick_color, cv2.MARKER_CROSS, 8, 1)
        label = f"{value:g}"
        (tw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.35, 1)
        cv2.putText(
            overlay,
            label,
            (max(plot.x0, min(x - tw // 2, plot.x1 - tw)), plot.y1 - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            tick_color,
            1,
            cv2.LINE_AA,
        )
    for value, py in y_axis.ticks:
        y = int(round(py))
        cv2.drawMarker(overlay, (plot.x0, y), tick_color, cv2.MARKER_CROSS, 8, 1)
        cv2.putText(
            overlay,
            f"{value:g}",
            (plot.x0 + 5, max(plot.y0 + 12, y - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            tick_color,
            1,
            cv2.LINE_AA,
        )
    def boxed_axis_label(text: str, origin: tuple[int, int]) -> None:
        scale = 0.60
        thickness = 2
        (width, height), baseline = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness
        )
        x, y = origin
        cv2.rectangle(
            overlay,
            (x - 4, y - height - 4),
            (x + width + 4, y + baseline + 4),
            (255, 255, 255),
            cv2.FILLED,
        )
        cv2.rectangle(
            overlay,
            (x - 4, y - height - 4),
            (x + width + 4, y + baseline + 4),
            tick_color,
            1,
        )
        cv2.putText(
            overlay,
            text,
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            tick_color,
            thickness,
            cv2.LINE_AA,
        )

    boxed_axis_label("VGS [V]", (plot.x1 - 91, plot.y1 - 24))
    boxed_axis_label("ID [A]", (plot.x0 + 8, plot.y0 + 68))
    if fit is not None:
        text = (
            f"dVth/dT={fit['d_vth_eff_v_per_k']*1e3:+.2f}mV/K  "
            f"dlnK/dT={fit['d_log_k_per_k']*1e3:+.3f}e-3/K  "
            f"shift RMS={fit['matched_shift_fit_rms_v']:.3f}V"
        )
        cv2.putText(
            overlay,
            text,
            (plot.x0 + 8, max(18, plot.y0 - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (0, 90, 0),
            1,
            cv2.LINE_AA,
        )
    return overlay


def process_chart(
    chart: dict,
    crop_path: Path,
    out_dir: Path,
    rel_stem: Path,
    anchor: dict[str, float] | None = None,
) -> dict:
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"could not read crop {crop_path}")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    transform = CropTransform.for_chart(chart, gray.shape)
    doc = fitz.open(str(chart["pdf"]))
    try:
        page = doc[int(chart["page"]) - 1]
        plot = _vector_plot_frame(page, transform, gray.shape) or find_plot_box(gray)
        words = _words_in_crop_px(page, transform, gray.shape)
        x_axis, y_axis = _calibrate(words, plot, gray.shape[0], x_pattern=NUM_RE)
        pixel_curves = _extract_two_curves(page, transform, plot, fitz)
    finally:
        doc.close()

    calibrated = [
        sorted((x_axis.value(x), max(0.0, y_axis.value(y))) for x, y in points)
        for points in pixel_curves
    ]
    curves = _assign_temperatures(calibrated, _temperatures(str(chart.get("text", ""))))
    # _assign_temperatures preserves each points-list object, so retain that
    # identity instead of matching by sampled current values.  Both traces can
    # legitimately clip at the same top-frame current.
    pixel_by_identity = {id(data): pixels for data, pixels in zip(calibrated, pixel_curves)}
    assigned_pixels = [pixel_by_identity[id(curve.points)] for curve in curves]
    fit = fit_saturation_tempco(curves, anchor) if anchor is not None else None

    csv_path = out_dir / "points" / rel_stem.parent / f"{rel_stem.name}.transfer_points.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Tj_C", "Vgs_V", "Id_A"])
        for curve in curves:
            for vgs, current in curve.points:
                writer.writerow([f"{curve.tj_c:.2f}", f"{vgs:.5f}", f"{current:.5f}"])

    overlay = _draw_overlay(image, plot, assigned_pixels, curves, x_axis, y_axis, fit)
    overlay_path = out_dir / "overlays" / rel_stem.parent / f"{rel_stem.name}.transfer_overlay.png"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)

    warnings = [
        "HUMAN REVIEW REQUIRED: verify axis guides, both temperature centerlines, "
        "temperature assignment, and fit summary before curating coefficients"
    ]
    if fit and fit["cold_anchor_conflict"]:
        warnings.append(
            "GUARD REFUSAL: exact 25 C (Vpl,Id_gc) law and absolute 25 C transfer chart "
            "disagree beyond the provisional residual bound; do not curate this fit"
        )
    status = "guard-refusal-cold-anchor-conflict" if fit and fit["cold_anchor_conflict"] else (
        "overlay-review-required"
    )
    return {
        "status": status,
        "temperatures_c": [curve.tj_c for curve in curves],
        "n_points": {f"{curve.tj_c:g}C": len(curve.points) for curve in curves},
        "calibration": {
            "x_ticks": len(x_axis.ticks),
            "x_resid": round(x_axis.resid, 5),
            "y_ticks": len(y_axis.ticks),
            "y_resid": round(y_axis.resid, 5),
        },
        "fit": fit,
        "warnings": warnings,
        "csv": str(csv_path),
        "overlay": str(overlay_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("charts_json", type=Path, help="charts.json from `dsdig find`")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--anchors-json",
        type=Path,
        help="optional {part:{vth_eff_v,k_a_per_vp,p,id_gc_a,vpl_v,tref_c}} map",
    )
    args = parser.parse_args()
    base_dir = args.charts_json.parent
    out_dir = args.out or base_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    charts = json.loads(args.charts_json.read_text())
    anchors = json.loads(args.anchors_json.read_text()) if args.anchors_json else {}
    results: list[dict] = []
    errors: list[dict] = []
    for chart in charts:
        if chart.get("kind") != "transfer":
            continue
        crop_rel = Path(chart["crop_png"])
        print(f"digitize {chart['part']} diagram {chart['diagram']}: {crop_rel}")
        try:
            result = process_chart(
                chart,
                base_dir / crop_rel,
                out_dir,
                crop_rel.with_suffix(""),
                anchors.get(chart["part"]),
            )
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors.append({"part": chart.get("part"), "crop": str(crop_rel), "error": str(exc)})
        else:
            result.update(
                {
                    "part": chart["part"],
                    "page": chart["page"],
                    "diagram": chart["diagram"],
                    "pdf": chart["pdf"],
                }
            )
            results.append(result)
            if result["fit"]:
                f = result["fit"]
                print(
                    f"  dVth/dT={f['d_vth_eff_v_per_k']*1e3:+.3f} mV/K, "
                    f"dlnK/dT={f['d_log_k_per_k']:+.6g}/K, "
                    f"shift RMS={f['matched_shift_fit_rms_v']:.4f} V"
                )
            print(f"  STATUS: {result['status']}")
    manifest = {"panels": results, "errors": errors}
    path = out_dir / "transfer_characteristics_digitization.json"
    path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    print(f"wrote {path}")
    if errors or not results:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
