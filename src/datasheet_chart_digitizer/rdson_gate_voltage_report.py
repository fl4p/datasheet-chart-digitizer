"""Readouts, table validation and review outputs for RDS(on)-versus-VGS panels."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import cv2
import numpy as np

from .numeric_axis import NumericAxis
from .overlay import draw_axis_ticks, draw_plot_frame
from .rdson_gate_voltage_axes import Calibration
from .rdson_gate_voltage_locate import LocatedPanel
from .rdson_spec_table import RdsonSpecRow

READOUT_VGS_V = (2.5, 3.3, 4.5)
READOUT_NOTE = "typical curve, not a guaranteed value"
MAX_READOUT_GAP_FRACTION = 0.04
TYP_RELATIVE_TOLERANCE = 0.20
MAX_OVERSHOOT_TOLERANCE = 0.05
ID_MATCH_RATIO = (0.75, 1.34)
TJ_MATCH_C = 1.0


def readouts(points, log_y: bool, x_span: float) -> list[dict]:
    """Interpolate RDS at the readout VGS values along the curve; never extrapolate."""
    vgs = np.asarray([p[0] for p in points])
    rds = np.asarray([p[1] for p in points])
    out = []
    for target in READOUT_VGS_V:
        entry: dict = {"vgs_v": target, "note": READOUT_NOTE}
        if not (vgs[0] <= target <= vgs[-1]):
            entry.update({"rds_mohm": None, "status": "not_on_chart",
                          "detail": f"curve spans {vgs[0]:.3g}..{vgs[-1]:.3g} V"})
        else:
            right = int(np.searchsorted(vgs, target))
            left = max(0, right - 1)
            right = min(len(vgs) - 1, right)
            gap = vgs[right] - vgs[left]
            if gap > MAX_READOUT_GAP_FRACTION * x_span:
                entry.update({"rds_mohm": None, "status": "not_on_chart",
                              "detail": f"trace gap {vgs[left]:.3g}..{vgs[right]:.3g} V around this VGS"})
            else:
                if log_y:
                    value = 10 ** float(np.interp(target, vgs, np.log10(rds)))
                else:
                    value = float(np.interp(target, vgs, rds))
                entry.update({"rds_mohm": round(value, 4), "status": "read"})
        out.append(entry)
    return out


def validate_against_table(curves: list[dict], rows: list[RdsonSpecRow], calibration: Calibration, scale: float) -> dict:
    """Tri-state check of typical curves against the RDS(on) table rows."""
    anchors = []
    per_px = _mohm_per_px(calibration, scale)
    for row in rows:
        anchor: dict = {"row": row.to_json()}
        anchors.append(anchor)
        if row.typ_mohm is None and row.max_mohm is None:
            anchor.update({"verdict": "not_evaluable", "reason": "row has no owned typ/max value or unit"})
            continue
        candidates = [c for c in curves if c.get("readouts") and _tj_matches(c, row)]
        if not candidates:
            anchor.update({"verdict": "not_evaluable", "reason": f"no curve bound to Tj={row.tj_c:g}C"})
            continue
        id_known = [c for c in candidates if c.get("id_a") is not None]
        if id_known:
            candidates = [c for c in id_known if row.id_a is not None and ID_MATCH_RATIO[0] <= c["id_a"] / row.id_a <= ID_MATCH_RATIO[1]]
            if not candidates:
                anchor.update({"verdict": "not_evaluable", "reason": f"no curve at ID close to the row's {row.id_a} A"})
                continue
        elif any("id_a" in c["parameter_binding"] for c in curves):
            anchor.update({"verdict": "not_evaluable", "reason": "curves vary by ID but ID labels did not bind"})
            continue
        if len(candidates) != 1:
            anchor.update({"verdict": "not_evaluable", "reason": f"{len(candidates)} curves match the row conditions"})
            continue
        curve = candidates[0]
        value = _value_at(curve, row.vgs_v)
        anchor["curve_index"] = curve["curve_index"]
        anchor["chart_id_a"] = curve.get("id_a")
        if value is None:
            anchor.update({"verdict": "not_evaluable", "reason": f"VGS={row.vgs_v:g} V not on the curve"})
            continue
        slack = per_px(value)
        anchor["chart_mohm"] = round(value, 4)
        anchor["reading_resolution_mohm_per_px"] = round(slack, 5)
        verdict = "consistent"
        if row.typ_mohm is not None:
            anchor["residual_vs_typ_mohm"] = round(value - row.typ_mohm, 4)
            anchor["residual_vs_typ_rel"] = round((value - row.typ_mohm) / row.typ_mohm, 4)
            if abs(value - row.typ_mohm) > TYP_RELATIVE_TOLERANCE * row.typ_mohm + slack:
                verdict = "inconsistent"
        if row.max_mohm is not None:
            anchor["margin_below_max_mohm"] = round(row.max_mohm - value, 4)
            if value > row.max_mohm * (1 + MAX_OVERSHOOT_TOLERANCE) + slack:
                verdict = "inconsistent"
        anchor["verdict"] = verdict
    evaluable = [a for a in anchors if a["verdict"] in {"consistent", "inconsistent"}]
    if not rows:
        overall, reason = "not_evaluable", "no RDS(on) table row owned by the parser"
    elif not evaluable:
        overall, reason = "not_evaluable", "no table row could be matched to a curve"
    elif any(a["verdict"] == "inconsistent" for a in evaluable):
        overall, reason = "inconsistent", "a curve contradicts its table row"
    else:
        overall, reason = "verified", f"{len(evaluable)} table row(s) consistent"
    return {
        "verdict": overall,
        "reason": reason,
        "tolerances": {
            "typ_relative": TYP_RELATIVE_TOLERANCE,
            "max_overshoot_relative": MAX_OVERSHOOT_TOLERANCE,
            "id_match_ratio": list(ID_MATCH_RATIO),
            "plus_one_pixel_of_y_resolution": True,
        },
        "anchors": anchors,
    }


def _tj_matches(curve: dict, row: RdsonSpecRow) -> bool:
    return curve.get("tj_c") is not None and abs(curve["tj_c"] - row.tj_c) <= TJ_MATCH_C


def _value_at(curve: dict, vgs: float) -> float | None:
    points = curve["points"]
    xs = [p[0] for p in points]
    if not xs or not xs[0] <= vgs <= xs[-1]:
        return None
    ys = [p[1] for p in points]
    return float(np.interp(vgs, xs, ys))


def _mohm_per_px(calibration: Calibration, scale: float):
    axis = calibration.y_axis

    def per_px(value: float) -> float:
        if axis.model == "log10":
            return abs(value * (10 ** abs(axis.m) - 1))
        return abs(axis.m) * scale

    return per_px


# --------------------------------------------------------------------------- outputs


def write_points(curves: list[dict], out_dir: Path, panel: LocatedPanel, stem: str) -> str:
    path = out_dir / "points" / panel.part / f"{stem}.rds_vgs_points.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["curve_index", "curve_label", "tj_c", "id_a", "vgs_v", "rds_mohm", "crop_x_px", "crop_y_px"])
        for curve in curves:
            for (vgs, rds), (x, y) in zip(curve["points"], curve["points_px"]):
                writer.writerow([curve["curve_index"], curve["label"], curve.get("tj_c"), curve.get("id_a"),
                                 f"{vgs:.5f}", f"{rds:.5f}", x, y])
    return str(path.relative_to(out_dir))


# BGR; chosen to stay visible over the red/green/blue/black curves vendors print.
_COLORS = ((255, 0, 255), (0, 165, 255), (255, 255, 0), (130, 0, 75), (0, 215, 255), (60, 20, 220))
_ANCHOR_COLOR = (120, 60, 0)


def write_overlay(image, row: dict, out_dir: Path, panel: LocatedPanel, stem: str,
                   calibration: Calibration | None = None, unit: str | None = None) -> None:
    body = image.copy()
    if calibration is not None:
        plot = calibration.plot
        draw_plot_frame(body, plot, (0, 190, 0), 2)
        for curve in row.get("curves", []):
            color = _COLORS[curve["curve_index"] % len(_COLORS)]
            pts = np.asarray(curve["points_px"], dtype=float)
            if len(pts):
                cv2.polylines(body, [np.round(pts).astype(np.int32).reshape(-1, 1, 2)], False, color, 1, cv2.LINE_AA)
                for x, y in pts[:: max(1, len(pts) // 60)]:
                    cv2.circle(body, (int(round(x)), int(round(y))), 3, (0, 0, 0), -1, cv2.LINE_AA)
                    cv2.circle(body, (int(round(x)), int(round(y))), 2, color, -1, cv2.LINE_AA)
            for readout in curve.get("readouts", []):
                if readout["rds_mohm"] is None:
                    continue
                x = _px(calibration.x_axis, readout["vgs_v"])
                y = _px(calibration.y_axis, readout["rds_mohm"] / row["calibration"]["y_to_mohm"])
                cv2.drawMarker(body, (int(round(x)), int(round(y))), color, cv2.MARKER_DIAMOND, 9, 1, cv2.LINE_AA)
        for readout_v in READOUT_VGS_V:
            x = int(round(_px(calibration.x_axis, readout_v)))
            if plot.x0 < x < plot.x1:
                for y in range(plot.y0, plot.y1, 8):
                    cv2.line(body, (x, y), (x, min(plot.y1, y + 3)), (150, 150, 150), 1)
        for anchor in row.get("validation", {}).get("anchors", []):
            spec = anchor["row"]
            x = _px(calibration.x_axis, spec["vgs_v"])
            if not plot.x0 <= x <= plot.x1:
                continue
            for key, marker in (("typ_mohm", cv2.MARKER_CROSS), ("max_mohm", cv2.MARKER_TILTED_CROSS)):
                if spec.get(key) is None:
                    continue
                y = _px(calibration.y_axis, spec[key] / row["calibration"]["y_to_mohm"])
                if plot.y0 - 2 <= y <= plot.y1 + 2:
                    cv2.drawMarker(body, (int(round(x)), int(round(y))), _ANCHOR_COLOR, marker, 14, 2, cv2.LINE_AA)
        draw_axis_ticks(
            body, plot,
            x_ticks=[(t.pixel, t.value) for t in calibration.x_axis.ticks],
            y_ticks=[(t.pixel, t.value) for t in calibration.y_axis.ticks],
            color=(255, 0, 0), font_scale=0.4, marker_size=10, unit_x="V",
            unit_y="mOhm" if unit == "mOhm" else "Ohm" if unit == "Ohm" else "?",
            line_aa=True, halo=True,
        )
    header = _header_lines(row, panel)
    legend = _legend_lines(row)
    line_h = 17
    top = np.full((line_h * len(header) + 6, body.shape[1], 3), 255, dtype=np.uint8)
    bottom = np.full((line_h * len(legend) + 6, body.shape[1], 3), 255, dtype=np.uint8)
    for i, (text, color) in enumerate(header):
        cv2.putText(top, text, (4, 14 + i * line_h), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
    for i, (text, color) in enumerate(legend):
        cv2.putText(bottom, text, (4, 14 + i * line_h), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
    canvas = np.vstack([top, body, bottom])
    path = out_dir / "overlays" / panel.part / f"{stem}.rds_vgs_overlay.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), canvas)
    row["overlay"] = str(path.relative_to(out_dir))


def _px(axis: NumericAxis, value: float) -> float:
    coordinate = math.log10(value) if axis.model == "log10" else value
    return (coordinate - axis.b) / axis.m


def _header_lines(row: dict, panel: LocatedPanel):
    status = row.get("status", "?")
    color = (0, 120, 0) if status == "ok" else (0, 0, 200)
    lines = [
        (f"{panel.part} p{panel.page} fig {panel.diagram}: {panel.title[:70]}", (0, 0, 0)),
        (f"STATUS {status.upper()}  validation={row.get('validation', {}).get('verdict', '?')}  "
         f"trace={row.get('trace_method', '-')}  ticks={row.get('calibration', {}).get('tick_source', '-')}", color),
    ]
    for reason in row.get("reasons", [])[:4]:
        lines.append((f"- {reason[:110]}", color))
    return lines


def _legend_lines(row: dict):
    lines = []
    for curve in row.get("curves", []):
        color = _COLORS[curve["curve_index"] % len(_COLORS)]
        reads = "  ".join(
            f"{r['vgs_v']:g}V:{r['rds_mohm']:.3g}" if r["rds_mohm"] is not None else f"{r['vgs_v']:g}V:n/c"
            for r in curve.get("readouts", [])
        )
        dark = tuple(int(0.6 * c) for c in color)
        lines.append((f"c{curve['curve_index']} {curve['label']}  [{reads}] mOhm (typical curve)", dark))
    for anchor in row.get("validation", {}).get("anchors", []):
        spec = anchor["row"]
        text = (f"table VGS={spec['vgs_v']:g}V ID={spec['id_a']}A Tj={spec['tj_c']:g}C typ={spec['typ_mohm']} "
                f"max={spec['max_mohm']} -> {anchor['verdict']}")
        if "chart_mohm" in anchor:
            text += f" chart={anchor['chart_mohm']:.3g}"
        lines.append((text[:120], _ANCHOR_COLOR))
    lines.append(("markers: + table typ, x table max, diamond = readout at 2.5/3.3/4.5 V (dashed)", (90, 90, 90)))
    return lines
