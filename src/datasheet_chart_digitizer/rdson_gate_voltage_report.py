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
EXACT_ID_TOLERANCE = 0.02
TYP_RELATIVE_TOLERANCE = 0.20
MAX_OVERSHOOT_TOLERANCE = 0.05
ID_MATCH_RATIO = (0.75, 1.34)
TJ_MATCH_C = 1.0


def readouts(
    points, log_y: bool, gaps: list[tuple[float, float]] | None = None, end_tolerance_v: float = 0.0,
    open_left: bool = False, open_right: bool = False, targets=READOUT_VGS_V,
) -> list[dict]:
    """Interpolate RDS at the readout VGS values along the curve.

    Never extrapolates past a curve end, and never interpolates across a gap
    in the trace: a target inside a gap (a stretch the trace did not cover,
    review finding A) is reported as not in the extracted trace.
    """
    vgs = np.asarray([p[0] for p in points])
    rds = np.asarray([p[1] for p in points])
    out = []
    for target in targets:
        entry: dict = {"vgs_v": target, "note": READOUT_NOTE}
        # A target within one pixel of a curve end is read AT that end (the
        # trace's last point sits a rounding error short of, e.g., the 10 V
        # frame); anything farther out is off the chart.
        if vgs[0] - end_tolerance_v <= target < vgs[0]:
            target_read = vgs[0]
        elif vgs[-1] < target <= vgs[-1] + end_tolerance_v:
            target_read = vgs[-1]
        else:
            target_read = target
        inside_gap = [g for g in gaps or [] if g[0] < target_read < g[1]]
        if not (vgs[0] <= target_read <= vgs[-1]):
            # Past an end the trace LOST (raster, stopped inside the plot) the
            # source may still have the curve: say so, never "not on chart".
            lost = (target_read < vgs[0] and open_left) or (target_read > vgs[-1] and open_right)
            entry.update({"rds_mohm": None, "status": "not_in_extracted_trace" if lost else "not_on_chart",
                          "detail": f"curve spans {vgs[0]:.3g}..{vgs[-1]:.3g} V"})
        elif inside_gap:
            g0, g1 = inside_gap[0]
            entry.update({"rds_mohm": None, "status": "not_in_extracted_trace",
                          "detail": f"trace gap {g0:.3g}..{g1:.3g} V around this VGS"})
        else:
            if log_y:
                value = 10 ** float(np.interp(target_read, vgs, np.log10(rds)))
            else:
                value = float(np.interp(target_read, vgs, rds))
            entry.update({"rds_mohm": round(value, 4), "status": "read"})
        out.append(entry)
    return out


def validate_against_table(curves: list[dict], rows: list[RdsonSpecRow], calibration: Calibration, scale: float) -> dict:
    """Tri-state check of typical curves against the RDS(on) table rows.

    Verdicts: ``verified`` needs at least one consistent anchor at the table's
    OWN drain current (within 2 %); consistent anchors only at a nearby current
    give ``consistent_at_approximate_conditions`` (review finding 5). Any
    inconsistent anchor gives ``inconsistent``; nothing evaluable gives
    ``not_evaluable``. Every anchor records the temperature kinds on both
    sides and any equivalence it assumed (chart Tc or Ta taken as table Tj,
    review finding 4).
    """
    anchors = []
    per_px = _mohm_per_px(calibration, scale)
    usable = [c for c in curves if c.get("usable", True)]
    for row in rows:
        anchor: dict = {"row": row.to_json(), "assumptions": []}
        anchors.append(anchor)
        if row.typ_mohm is None and row.max_mohm is None:
            anchor.update({"verdict": "not_evaluable", "reason": "row has no owned typ/max value or unit"})
            continue
        candidates = [c for c in usable if c.get("readouts") and _temperature_matches(c, row)]
        if not candidates:
            anchor.update({"verdict": "not_evaluable",
                           "reason": f"no usable curve bound to {row.temperature_c:g} C"})
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
        anchor["curve_index"] = curve["curve_index"]
        anchor["chart_id_a"] = curve.get("id_a")
        anchor["chart_temperature_kind"] = curve.get("temperature_kind")
        anchor["table_temperature_kind"] = row.temperature_kind
        anchor["condition_match"] = _condition_match(curve, row)
        chart_kind, table_kind = curve.get("temperature_kind"), row.temperature_kind
        if chart_kind != table_kind or chart_kind in {None, "unspecified", "conflicting", "T (subscript unread)"}:
            anchor["assumptions"].append(
                f"chart {chart_kind or 'unspecified'} taken as table {table_kind} "
                f"(both {row.temperature_c:g} C); equivalence not established by the datasheet"
            )
        reading = readouts(
            curve["points"], calibration.y_axis.model == "log10", curve.get("gaps"),
            abs(calibration.x_axis.m), targets=(row.vgs_v,),
        )[0]
        if reading["status"] != "read":
            anchor.update({"verdict": "not_evaluable", "reason": f"VGS={row.vgs_v:g} V: {reading['status']} ({reading.get('detail', '')})"})
            continue
        value = reading["rds_mohm"]
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
        else:
            anchor["max_status"] = (
                f"table max cell unreadable ({row.unparsed_cells['max']!r}); not checked"
                if "max" in row.unparsed_cells else "no table max; not checked"
            )
        anchor["verdict"] = verdict
    evaluable = [a for a in anchors if a["verdict"] in {"consistent", "inconsistent"}]
    exact = [a for a in evaluable if a.get("condition_match") == "exact"]
    if not rows:
        overall, reason = "not_evaluable", "no RDS(on) table row owned by the parser"
    elif not evaluable:
        overall, reason = "not_evaluable", "no table row could be matched to a curve"
    elif any(a["verdict"] == "inconsistent" for a in evaluable):
        overall, reason = "inconsistent", "a curve contradicts its table row"
    elif exact:
        overall, reason = "verified", f"{len(exact)} table row(s) consistent at the table's own drain current"
    else:
        overall, reason = (
            "consistent_at_approximate_conditions",
            "consistent only at a drain current that differs from the table's",
        )
    return {
        "verdict": overall,
        "reason": reason,
        "assumptions": sorted({text for a in anchors for text in a.get("assumptions", [])}),
        "tolerances": {
            "typ_relative": TYP_RELATIVE_TOLERANCE,
            "max_overshoot_relative": MAX_OVERSHOOT_TOLERANCE,
            "id_match_ratio_for_evaluation": list(ID_MATCH_RATIO),
            "id_match_for_verified": EXACT_ID_TOLERANCE,
            "plus_one_pixel_of_y_resolution": True,
        },
        "anchors": anchors,
    }


def _condition_match(curve: dict, row: RdsonSpecRow) -> str:
    if curve.get("id_a") is None or row.id_a is None:
        return "drain_current_not_stated"
    if abs(curve["id_a"] / row.id_a - 1.0) <= EXACT_ID_TOLERANCE:
        return "exact"
    return "approximate_drain_current"


def _temperature_matches(curve: dict, row: RdsonSpecRow) -> bool:
    return curve.get("temperature_c") is not None and abs(curve["temperature_c"] - row.temperature_c) <= TJ_MATCH_C


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
        writer.writerow(["curve_index", "curve_label", "usable", "temperature_c", "temperature_kind", "id_a",
                         "vgs_v", "rds_mohm", "crop_x_px", "crop_y_px"])
        for curve in curves:
            for (vgs, rds), (x, y) in zip(curve["points"], curve["points_px"]):
                writer.writerow([curve["curve_index"], curve["label"], curve.get("usable", True),
                                 curve.get("temperature_c"), curve.get("temperature_kind"), curve.get("id_a"),
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
            if not curve.get("usable", True):
                color = (150, 150, 150)
            if len(pts):
                # one polyline per gap-free segment: a gap is never drawn as data
                breaks = [0] + [i + 1 for i in range(len(pts) - 1) if pts[i + 1, 0] - pts[i, 0] > 2.5] + [len(pts)]
                for a, b in zip(breaks, breaks[1:]):
                    segment = np.round(pts[a:b]).astype(np.int32).reshape(-1, 1, 2)
                    cv2.polylines(body, [segment], False, color, 1, cv2.LINE_AA)
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
        if not curve.get("usable", True):
            lines.append((f"c{curve['curve_index']} NOT USABLE (grey): {curve.get('not_usable_reason', '')[:80]}", (90, 90, 90)))
            continue
        lines.append((f"c{curve['curve_index']} {curve['label']}  [{reads}] mOhm (typical curve)", dark))
    for anchor in row.get("validation", {}).get("anchors", []):
        spec = anchor["row"]
        text = (f"table VGS={spec['vgs_v']:g}V ID={spec['id_a']}A {spec['temperature_kind']}={spec['temperature_c']:g}C typ={spec['typ_mohm']} "
                f"max={spec['max_mohm']} -> {anchor['verdict']}")
        if "chart_mohm" in anchor:
            text += f" chart={anchor['chart_mohm']:.3g}"
        lines.append((text[:120], _ANCHOR_COLOR))
    lines.append(("markers: + table typ, x table max, diamond = readout at 2.5/3.3/4.5 V (dashed)", (90, 90, 90)))
    return lines
