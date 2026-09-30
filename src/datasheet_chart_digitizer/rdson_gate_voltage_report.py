"""Readouts, table validation and review outputs for RDS(on)-versus-VGS panels."""

from __future__ import annotations

import csv
import math
import re
import textwrap
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
        tol_left = end_tolerance_v(vgs[0]) if callable(end_tolerance_v) else end_tolerance_v
        tol_right = end_tolerance_v(vgs[-1]) if callable(end_tolerance_v) else end_tolerance_v
        if vgs[0] - tol_left <= target < vgs[0]:
            target_read = vgs[0]
        elif vgs[-1] < target <= vgs[-1] + tol_right:
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


def vgs_per_px(axis):
    """One pixel of the VGS axis, in volts: a constant on a linear axis; on a
    log10 axis it grows with VGS (V * ln 10 * |m|), so a callable of VGS."""
    if axis.model == "log10":
        return lambda vgs: abs(vgs) * math.log(10.0) * abs(axis.m)
    return abs(axis.m)


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
        if getattr(row, "qualifier", ""):
            # IPP100N06S2L05 repeats its rows for the "SMD version"; which
            # package the chart shows is not printed, so neither row anchors it.
            anchor.update({"verdict": "not_evaluable",
                           "reason": f"row is qualified {row.qualifier!r}; the chart's variant is not stated"})
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
            vgs_per_px(calibration.x_axis), targets=(row.vgs_v,),
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
    diagnostics = _max_diagnostics(curves, rows, anchors, calibration, per_px)
    mismatches = condition_mismatch_notes(curves, rows, calibration, per_px)
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
        "diagnostics": diagnostics,
        "condition_mismatch_notes": mismatches,
    }


def _max_diagnostics(curves, rows, anchors, calibration, per_px) -> list[dict]:
    """Curves above a table maximum at the table's own VGS that no anchor judged (review R3-11).

    BRCS020N03RA: at 4.5 V the table max sits BELOW the upper curve, but the
    temperatures are unbound, so no anchor is evaluated and the observation
    was lost. If the upper curve is the hot one it is benign; if it is the
    25 C curve the datasheet contradicts itself (as AO3400A does). Neither
    reading is claimed: the observation is recorded with what is unknown.
    A curve bound to a DIFFERENT temperature (a hot curve over a 25 C max)
    is expected physics and is not listed; the curve an anchor evaluated is
    already judged there.
    """
    judged = {(a["row"]["vgs_v"], a.get("curve_index")) for a in anchors if "curve_index" in a}
    out = []
    for row in rows:
        if row.max_mohm is None:
            continue
        for curve in curves:
            if not curve.get("usable", True) or not curve.get("points") or (row.vgs_v, curve["curve_index"]) in judged:
                continue
            temperature = curve.get("temperature_c")
            if temperature is not None and abs(temperature - row.temperature_c) > TJ_MATCH_C:
                continue
            reading = readouts(curve["points"], calibration.y_axis.model == "log10", curve.get("gaps"),
                               vgs_per_px(calibration.x_axis), targets=(row.vgs_v,))[0]
            if reading["status"] != "read":
                continue
            value = reading["rds_mohm"]
            if value <= row.max_mohm * (1 + MAX_OVERSHOOT_TOLERANCE) + per_px(value):
                continue
            unknown = []
            if temperature is None:
                unknown.append("temperature binding unknown")
            if curve.get("id_a") is None:
                unknown.append("ID binding unknown")
            elif row.id_a is not None and abs(curve["id_a"] / row.id_a - 1.0) > EXACT_ID_TOLERANCE:
                unknown.append(f"chart ID {curve['id_a']:g} A vs table ID {row.id_a:g} A")
            out.append({
                "kind": "curve_exceeds_table_max_at_table_vgs",
                "curve_index": curve["curve_index"],
                "vgs_v": row.vgs_v,
                "chart_mohm": value,
                "table_max_mohm": row.max_mohm,
                "table_temperature_c": row.temperature_c,
                "text": (f"curve {curve['curve_index']} exceeds table max at the table's VGS "
                         f"({value:.3g} > {row.max_mohm:g} mOhm at {row.vgs_v:g} V); "
                         + "; ".join(unknown or ["bindings match the row"])),
            })
    return out


def condition_mismatch_notes(curves, rows, calibration, per_px) -> list[dict]:
    """Curves above a table maximum at the table's VGS whose BOUND temperature differs from the row's (F5-3).

    _max_diagnostics leaves these out: a hot curve over a 25 C maximum is
    expected physics, not a contradiction. They are still recorded, so that
    an observation a reviewer can see on the overlay (a "x" below a curve)
    is resolved in writing instead of vanishing. BRCS020N03RA: v5 raised
    "curve 0 exceeds table max at 4.5 V" with the temperatures unbound;
    curve 0 is the 125 C curve and the row is 10 A at 25 C.
    """
    out = []
    for row in rows:
        if row.max_mohm is None:
            continue
        for curve in curves:
            temperature = curve.get("temperature_c")
            if (not curve.get("usable", True) or not curve.get("points") or temperature is None
                    or abs(temperature - row.temperature_c) <= TJ_MATCH_C):
                continue
            reading = readouts(curve["points"], calibration.y_axis.model == "log10", curve.get("gaps"),
                               vgs_per_px(calibration.x_axis), targets=(row.vgs_v,))[0]
            if reading["status"] != "read":
                continue
            value = reading["rds_mohm"]
            if value <= row.max_mohm * (1 + MAX_OVERSHOOT_TOLERANCE) + per_px(value):
                continue
            differs = [f"curve {temperature:g} C vs row {row.temperature_c:g} C"]
            if curve.get("id_a") is not None and row.id_a is not None and abs(curve["id_a"] / row.id_a - 1.0) > EXACT_ID_TOLERANCE:
                differs.append(f"curve ID {curve['id_a']:g} A vs row ID {row.id_a:g} A")
            out.append({
                "kind": "curve_above_table_max_at_other_conditions",
                "curve_index": curve["curve_index"],
                "vgs_v": row.vgs_v,
                "chart_mohm": value,
                "table_max_mohm": row.max_mohm,
                "table_temperature_c": row.temperature_c,
                "curve_temperature_binding": curve.get("parameter_binding", {}).get("temperature_c"),
                "text": (f"curve {curve['curve_index']} lies above the table max at the table's VGS "
                         f"({value:.3g} > {row.max_mohm:g} mOhm at {row.vgs_v:g} V), but the conditions differ ("
                         + "; ".join(differs) + "): a condition mismatch, not a datasheet contradiction"),
            })
    return out


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


# Curve palette, BGR (review R3-14): Okabe-Ito without its black -- bright,
# colour-blind safe, none dark against black datasheet ink. The most-used
# indices come first and are the most distinct: vermillion, sky blue,
# bluish green, then orange, reddish purple, blue, yellow.
PALETTE_HEX = ("#D55E00", "#56B4E9", "#009E73", "#E69F00", "#CC79A7", "#0072B2", "#F0E442")
_COLORS = tuple((int(h[5:7], 16), int(h[3:5], 16), int(h[1:3], 16)) for h in PALETTE_HEX)
MIN_CONTRAST_VS_BLACK = 4.0      # WCAG contrast ratio of every curve colour against black ink
_UNUSABLE = (165, 165, 165)      # grey for a not-usable curve (not in the palette)
_MARK = (0, 0, 0)                # ticks, table markers: black on a white halo
_ANCHOR_COLOR = (20, 20, 20)
_TEXT = (20, 20, 20)
_FONT, _FONT_SCALE = cv2.FONT_HERSHEY_SIMPLEX, 0.42
LABEL_CLEARANCE_PX = 8           # a direct curve label keeps this far from every traced point


def relative_luminance(bgr) -> float:
    def channel(c: int) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    b, g, r = bgr
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_vs_black(bgr) -> float:
    return (relative_luminance(bgr) + 0.05) / 0.05


def curve_color(curve: dict):
    return _COLORS[curve["curve_index"] % len(_COLORS)] if curve.get("usable", True) else _UNUSABLE


def _text_px(text: str, scale: float = _FONT_SCALE) -> int:
    return cv2.getTextSize(text, _FONT, scale, 1)[0][0]


def _put(img, text, org, color, scale=_FONT_SCALE, outline=False):
    if outline:
        cv2.putText(img, text, org, _FONT, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, org, _FONT, scale, color, 1, cv2.LINE_AA)


def temperature_text(curve: dict) -> str:
    t, kind = curve.get("temperature_c"), curve.get("temperature_kind")
    if t is None:
        return "T unknown"
    if kind in ("Tj", "Tc", "Ta"):
        return f"{kind}={t:g}C"
    if kind == "unspecified":
        return f"T={t:g}C (kind not printed)"
    if kind == "T (subscript unread)":
        return f"T(subscript unread)={t:g}C"
    return f"T={t:g}C ({kind})"


def _segments(curve: dict):
    pts = np.asarray(curve.get("points_px") or [], dtype=float)
    if not len(pts):
        return pts, []
    breaks = [0] + [i + 1 for i in range(len(pts) - 1) if _breaks_between(curve, i, pts)] + [len(pts)]
    return pts, [(a, b) for a, b in zip(breaks, breaks[1:]) if b > a]


TRACE_CORE_PX = 5             # F5-2: the centre of every trace shows the source crop, unchanged
TRACE_RAIL_PX = 2             # colour on each side of the core, per nesting level
NESTED_BASE_WIDTH_PX = TRACE_CORE_PX + 2 * TRACE_RAIL_PX   # the last (top) curve's outer width
NESTED_STEP_PX = 2 * TRACE_RAIL_PX   # each earlier curve is this much wider underneath (one rail per side, crisp edges)


def line_widths(curves: list[dict]) -> dict[int, int]:
    """Nested outer widths (review F4-5): c0 widest, each later curve
    narrower and drawn on top, so every colour shows as a rail wherever
    curves overlap. Every width includes the see-through core (F5-2)."""
    order = sorted(c["curve_index"] for c in curves)
    return {index: NESTED_BASE_WIDTH_PX + NESTED_STEP_PX * (len(order) - 1 - rank) for rank, index in enumerate(order)}


def _stroke(canvas, curve: dict, color, width: int) -> None:
    """The curve's served segments (breaks at gaps), ``width`` px wide."""
    pts, segments = _segments(curve)
    for a, b in segments:
        for k in range(a, b - 1):
            p0 = (int(round(pts[k, 0])), int(round(pts[k, 1])))
            p1 = (int(round(pts[k + 1, 0])), int(round(pts[k + 1, 1])))
            cv2.line(canvas, p0, p1, color, width, cv2.LINE_8)
        if b - a == 1:
            cv2.circle(canvas, (int(round(pts[a, 0])), int(round(pts[a, 1]))), max(1, width // 2), color, -1, cv2.LINE_8)


def _draw_curves(body, curves: list[dict]) -> None:
    """One rule on every overlay (review F4-5, F5-2): every trace is a
    coloured tube whose centre shows the source crop unchanged.

    - F5-2 ("i dont see the original curves", WSR3090 v5): the v5 solid
      lines on a white halo covered the thin black print. Now each trace is
      two coloured rails around a TRACE_CORE_PX see-through core: the
      printed stroke shows inside the tube where the trace is on it, and
      beside or under a rail where the trace is off it.
    - F4-5: nested widths -- c0 widest, later curves narrower on top -- so
      on a coincident stretch every colour still shows as its own rail and
      no curve seems to end under another. No style change along a curve;
      coincidence is stated in the data and the legend only.
    - R3-14: the rails are palette colours bright against black ink, with a
      1 px white rim on the outside; sample rings (hollow, in the curve
      colour, cut open at the core) mark samples where no other curve is
      within 8 px.
    Rendering only: no traced point, readout or status depends on it.
    """
    source = body.copy()
    order = sorted(curves, key=lambda c: c["curve_index"])
    widths = line_widths(order)
    all_pts = {c["curve_index"]: np.asarray(c.get("points_px") or np.zeros((0, 2)), dtype=float) for c in order}
    for curve in order:
        _stroke(body, curve, (255, 255, 255), widths[curve["curve_index"]] + 2)
    for curve in order:
        pts, _unused = _segments(curve)
        color, width = curve_color(curve), widths[curve["curve_index"]]
        _stroke(body, curve, color, width)
        others = [v for i, v in all_pts.items() if i != curve["curve_index"] and len(v)]
        step = max(1, len(pts) // 60)
        for k in range(0, len(pts), step):
            x, y = pts[k]
            if any((np.hypot(o[:, 0] - x, o[:, 1] - y) < 8).any() for o in others):
                continue
            cv2.circle(body, (int(round(x)), int(round(y))), width // 2 + 3, color, 1, cv2.LINE_AA)
    core = np.zeros(body.shape[:2], dtype=np.uint8)
    for curve in order:
        _stroke(core, curve, 255, TRACE_CORE_PX)
    body[core > 0] = source[core > 0]


def _place_curve_labels(body, curves: list[dict], plot) -> list[dict]:
    """A small direct label per curve, e.g. "c1 Tc=25C" (R3-12): white box,
    curve-coloured border and text, offset from the ink and other labels."""
    placed: list[tuple[int, int, int, int]] = []
    all_pts = np.concatenate([np.asarray(c["points_px"], dtype=float) for c in curves if c.get("points_px")] or [np.zeros((0, 2))])
    out = []
    for curve in sorted(curves, key=lambda c: c["curve_index"]):
        pts, segments = _segments(curve)
        if not segments:
            continue
        a, b = max(segments, key=lambda s: s[1] - s[0])
        text = f"c{curve['curve_index']} {temperature_text(curve)}"
        w, h = _text_px(text) + 8, 16
        anchors = [pts[b - 1], pts[a], pts[(a + b) // 2], pts[a + (b - a) // 4], pts[a + 3 * (b - a) // 4]]
        choice = None
        for ax_, ay in anchors:
            for dx, dy in ((10, -26), (10, 10), (-w - 10, -26), (-w - 10, 10), (10, -46), (-w - 10, -46)):
                x0, y0 = int(ax_ + dx), int(ay + dy)
                box = (x0, y0, x0 + w, y0 + h)
                if box[0] < plot.x0 + 2 or box[2] > plot.x1 - 2 or box[1] < plot.y0 + 2 or box[3] > plot.y1 - 2:
                    continue
                if any(not (box[2] < o[0] or box[0] > o[2] or box[3] < o[1] or box[1] > o[3]) for o in placed):
                    continue
                if len(all_pts) and ((all_pts[:, 0] >= box[0] - LABEL_CLEARANCE_PX) & (all_pts[:, 0] <= box[2] + LABEL_CLEARANCE_PX)
                                     & (all_pts[:, 1] >= box[1] - LABEL_CLEARANCE_PX)
                                     & (all_pts[:, 1] <= box[3] + LABEL_CLEARANCE_PX)).any():
                    continue
                choice = (box, (int(ax_), int(ay)))
                break
            if choice:
                break
        free = choice is not None
        if choice is None:  # nowhere free inside the plot: top-left corner stack, still drawn
            y0 = plot.y0 + 4 + 20 * len(placed)
            choice = ((plot.x0 + 4, y0, plot.x0 + 4 + w, y0 + h), None)
        box, anchor = choice
        placed.append(box)
        color = curve_color(curve)
        if anchor is not None:
            edge = (min(max(anchor[0], box[0]), box[2]), box[3] if anchor[1] > box[3] else box[1])
            cv2.line(body, anchor, edge, (255, 255, 255), 3, cv2.LINE_AA)
            cv2.line(body, anchor, edge, color, 1, cv2.LINE_AA)
        cv2.rectangle(body, box[:2], box[2:], (255, 255, 255), -1)
        cv2.rectangle(body, box[:2], box[2:], color, 2)
        _put(body, text, (box[0] + 4, box[3] - 4), color, outline=True)
        out.append({"curve_index": curve["curve_index"], "text": text, "box_px": list(box),
                    "placement": "beside its trace, clear of all ink" if free else "corner stack (no free spot)"})
    return out


def write_overlay(image, row: dict, out_dir: Path, panel: LocatedPanel, stem: str,
                   calibration: Calibration | None = None, unit: str | None = None) -> None:
    body = image.copy()
    if calibration is not None:
        plot = calibration.plot
        draw_plot_frame(body, plot, (0, 190, 0), 2)
        for readout_v in READOUT_VGS_V:
            x = int(round(_px(calibration.x_axis, readout_v)))
            if plot.x0 < x < plot.x1:
                for y in range(plot.y0, plot.y1, 8):
                    cv2.line(body, (x, y), (x, min(plot.y1, y + 3)), (170, 170, 170), 1)
        _draw_curves(body, row.get("curves", []))
        for curve in row.get("curves", []):
            color = curve_color(curve)
            for readout in curve.get("readouts", []):
                if readout["rds_mohm"] is None:
                    continue
                x = _px(calibration.x_axis, readout["vgs_v"])
                y = _px(calibration.y_axis, readout["rds_mohm"] / row["calibration"]["y_to_mohm"])
                centre = (int(round(x)), int(round(y)))
                cv2.drawMarker(body, centre, (255, 255, 255), cv2.MARKER_DIAMOND, 15, 4, cv2.LINE_AA)
                cv2.drawMarker(body, centre, color, cv2.MARKER_DIAMOND, 13, 2, cv2.LINE_AA)
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
                    centre = (int(round(x)), int(round(y)))
                    cv2.drawMarker(body, centre, (255, 255, 255), marker, 16, 5, cv2.LINE_AA)
                    cv2.drawMarker(body, centre, _ANCHOR_COLOR, marker, 14, 2, cv2.LINE_AA)
        row["overlay_curve_labels"] = _place_curve_labels(body, row.get("curves", []), plot)
        # the v3 tick rendering (review F4-2: the white axis bands of R3-10
        # were an unrequested inference and are reverted); every USED tick
        # is marked, with its value
        draw_axis_ticks(
            body, plot,
            x_ticks=[(t.pixel, t.value) for t in calibration.x_axis.ticks],
            y_ticks=[(t.pixel, t.value) for t in calibration.y_axis.ticks],
            color=(255, 0, 0), font_scale=0.4, marker_size=10, unit_x="V",
            unit_y="mOhm" if unit == "mOhm" else "Ohm" if unit == "Ohm" else "?",
            line_aa=True, halo=True,
        )
    width_px = body.shape[1] - 8
    header = _header_lines(row, panel, width_px)
    legend = _legend_lines(row, width_px - 44)
    line_h = 17
    top = np.full((line_h * len(header) + 6, body.shape[1], 3), 255, dtype=np.uint8)
    bottom = np.full((line_h * len(legend) + 10, body.shape[1], 3), 255, dtype=np.uint8)
    for i, (text, color) in enumerate(header):
        cv2.putText(top, text, (4, 14 + i * line_h), _FONT, _FONT_SCALE, color, 1, cv2.LINE_AA)
    curves = {c["curve_index"]: c for c in row.get("curves", [])}
    widths = line_widths(list(curves.values()))
    for i, (text, color) in enumerate(legend):
        y = 14 + i * line_h
        match = re.match(r"c(\d+) ", text)
        if match and int(match.group(1)) in curves:
            # the curve's swatch: its line on a white halo, as drawn on the chart
            index = int(match.group(1))
            # (F5-2: a tube with the print -- a thin black stroke -- in its core)
            swatch, width = curve_color(curves[index]), min(widths[index], line_h - 4)
            cv2.line(bottom, (6, y - 4), (36, y - 4), swatch, width, cv2.LINE_8)
            cv2.line(bottom, (6, y - 4), (36, y - 4), (255, 255, 255), TRACE_CORE_PX, cv2.LINE_8)
            cv2.line(bottom, (6, y - 4), (36, y - 4), (0, 0, 0), 1, cv2.LINE_8)
        cv2.putText(bottom, text, (44, y), _FONT, _FONT_SCALE, color, 1, cv2.LINE_AA)
    canvas = np.vstack([top, body, bottom])
    row["overlay_body_offset_px"] = [0, int(top.shape[0])]   # the crop sits here, unscaled
    path = out_dir / "overlays" / panel.part / f"{stem}.rds_vgs_overlay.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), canvas)
    row["overlay"] = str(path.relative_to(out_dir))


def _breaks_between(curve: dict, i: int, pts) -> bool:
    if pts[i + 1, 0] - pts[i, 0] > 2.5:
        return True
    points = curve.get("points") or []
    if i + 1 >= len(points):
        return False
    a, b = points[i][0], points[i + 1][0]
    return any(g0 <= a + 1e-4 and b - 1e-4 <= g1 for g0, g1 in curve.get("gaps", []))


def _px(axis: NumericAxis, value: float) -> float:
    coordinate = math.log10(value) if axis.model == "log10" else value
    return (coordinate - axis.b) / axis.m


# F5-1 / F5-3: a parameter bound by the physical order of the curves says so
_ORDER_RULE_NOTE = {
    "id_order_rule": " (bound by the ID order rule: higher ID, higher RDS)",
    "temperature_order_rule": " (bound by the temperature order rule: hotter, higher RDS)",
}
_STATE_SHORT = {"not_on_chart": "n/c", "not_in_extracted_trace": "not traced", "curve_not_usable": "unusable"}
_HEADER_WRAP = 118


def _wrapped(text: str, color, width_px: int | None = None) -> list:
    """Word-wrap one overlay line; nothing is clipped (review R3-7).

    Breaks only at spaces, to the rendered pixel width when it is known (the
    overlay canvas) and to _HEADER_WRAP characters otherwise. A single token
    longer than the width stays whole on its own line rather than being cut
    mid-word.
    """
    if width_px is None:
        parts = textwrap.wrap(text, width=_HEADER_WRAP, subsequent_indent="    ",
                              break_long_words=False, break_on_hyphens=False)
        return [(part, color) for part in parts] or [("", color)]
    out, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}" if line else (("    " if out else "") + word)
        if line and _text_px(candidate) > width_px:
            out.append(line)
            line = "    " + word
        else:
            line = candidate
    out.append(line)
    return [(part, color) for part in out]


def _header_lines(row: dict, panel: LocatedPanel, width_px: int | None = None):
    """Title, status, and EVERY reason, word-wrapped; nothing is dropped or clipped (R2-5, R3-7)."""
    status = row.get("status", "?")
    color = (0, 120, 0) if status == "ok" else (0, 0, 200)
    lines = _wrapped(f"{panel.part} p{panel.page} fig {panel.diagram}: {panel.title}", (0, 0, 0), width_px)
    lines += _wrapped(
        f"STATUS {status.upper()}  validation={row.get('validation', {}).get('verdict', '?')}  "
        f"trace={row.get('trace_method', '-')}  ticks={row.get('calibration', {}).get('tick_source', '-')}  "
        f"reasons: {len(row.get('reasons', []))}", color, width_px)
    for reason in row.get("reasons", []):
        lines += _wrapped(f"- {reason}", color, width_px)
    return lines


def _legend_lines(row: dict, width_px: int | None = None):
    """One row per curve (its swatch is drawn beside it): index, temperature
    with its kind, ID, usable or NOT USABLE, the readout states, and any
    coincidence with another curve (R3-12, R3-13). Never truncated."""
    lines = []
    for curve in row.get("curves", []):
        reads = " ".join(
            f"{r['vgs_v']:g}V:{r['rds_mohm']:.3g}" if r["rds_mohm"] is not None
            else f"{r['vgs_v']:g}V:{_STATE_SHORT.get(r['status'], r['status'])}"
            for r in curve.get("readouts", [])
        )
        binding = curve.get("parameter_binding", {})
        ident = (f"c{curve['curve_index']} {temperature_text(curve)}{_ORDER_RULE_NOTE.get(binding.get('temperature_c'), '')} "
                 f"{'ID=%gA' % curve['id_a'] if curve.get('id_a') is not None else 'ID unknown'}"
                 f"{_ORDER_RULE_NOTE.get(binding.get('id_a'), '')}")
        together = "".join(
            f"; coincident with c{c['curve_index']} {c['from_vgs_v']:.2f}..{c['to_vgs_v']:.2f} V (drawn nested)"
            for c in curve.get("coincident_with", []))
        if not curve.get("usable", True):
            lines += _wrapped(f"{ident} NOT USABLE (grey): {curve.get('not_usable_reason', '')}{together}", (90, 90, 90), width_px)
            continue
        lines += _wrapped(f"{ident} usable [{reads}] mOhm (typical curve){together}", _TEXT, width_px)
    for anchor in row.get("validation", {}).get("anchors", []):
        spec = anchor["row"]
        text = (f"table VGS={spec['vgs_v']:g}V ID={spec['id_a']}A {spec['temperature_kind']}={spec['temperature_c']:g}C typ={spec['typ_mohm']} "
                f"max={spec['max_mohm']} -> {anchor['verdict']}")
        if "chart_mohm" in anchor:
            text += f" chart={anchor['chart_mohm']:.3g}"
        lines += _wrapped(text, _ANCHOR_COLOR, width_px)
    for note in row.get("validation", {}).get("diagnostics", []):
        lines += _wrapped(f"diagnostic: {note['text']}", (0, 0, 160), width_px)
    for note in row.get("validation", {}).get("condition_mismatch_notes", []):
        lines += _wrapped(f"resolved: {note['text']}", (90, 90, 90), width_px)
    lines += _wrapped("trace = coloured tube whose centre shows the print unchanged (the printed stroke should run inside it); "
                      "rings = samples; markers: + table typ, x table max (black), diamond = readout at 2.5/3.3/4.5 V "
                      "(dashed grey lines)", (90, 90, 90), width_px)
    lines += _wrapped("readout states: n/c = not on chart (off the source curve's span); not traced = outside/inside a gap "
                      "of the extracted trace (source may continue)", (90, 90, 90), width_px)
    return lines
