"""Independent printed-rule residuals and conservative tick completion.

The existing calibrated map and traced pixels are immutable here. Additional
ticks can extend its evidenced span only within one pixel of the same map.
Non-affine printed grids remain visible as diagnostic ticks, never a relaxed fit.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace

import numpy as np

from .numeric_axis import AxisTick


def printed_tick_evidence(calibration, labels):
    plot = calibration.plot
    updated, report, reasons = {}, {}, []
    for name, rules in (("x", calibration.grid_x), ("y", calibration.grid_y)):
        axis = getattr(calibration, name + "_axis")
        if not math.isfinite(axis.m) or not math.isfinite(axis.b) or axis.m == 0:
            report[name] = {"ticks": [], "max_residual_px": None, "mapping_unchanged": True, "added_span_ticks": []}
            reasons.append(f"{name}_axis_printed_rule_residual_unverified: invalid mapping")
            continue
        lo, hi = (plot.x0, plot.x1) if name == "x" else (plot.y0, plot.y1)
        pixel = lambda v: ((math.log10(v) if axis.model == "log10" else v) - axis.b) / axis.m
        candidates = [(t.text, t.value, t.pixel, "used_tick") for t in axis.ticks]
        for l in labels:
            if not re.fullmatch(r"\d+(?:\.\d+)?", l.text):
                continue
            v = float(l.text)
            if axis.model == "log10" and v <= 0:
                continue
            pos = l.cx if name == "x" else l.cy
            in_band = (plot.y1 + 4 < l.cy < plot.y1 + 65 if name == "x"
                       else plot.x0 - 100 < l.x1 <= plot.x0 - 4)
            if in_band and lo - 12 <= pos <= hi + 12 and abs(pixel(v) - pos) <= 12:
                candidates.append((l.text, v, pos, "printed_label"))
        observed, additions = [], []
        for value in sorted({v for _, v, _, _ in candidates}):
            group = [c for c in candidates if c[1] == value]
            predicted = pixel(value)
            # Semantic ownership precedes nearest-rule seating. Distant
            # duplicates, missing rules and two possible rules cannot confirm it.
            real = [p for _, _, p, source in group if source == "printed_label"]
            if real and max(real) - min(real) > 6:
                observed.append({"value": value, "state": "unverified", "reason": "conflicting printed positions"})
                continue
            near = [r for r in rules if lo - 2 <= r <= hi + 2 and abs(r - predicted) <= 12
                    and (not real or min(abs(r-p) for p in real) <= 12)]
            # PDF vector rules own their position. Raster projection can also
            # report a thick frame edge or nearby flat curve as a peak; those
            # peaks do not make a unique vector rule ambiguous. Two actual
            # vector rules remain ambiguous, including at a seated tick.
            vector = getattr(calibration, "vector_grid_" + name)
            physical = [r for r in vector if r in near]
            seated = [t.pixel for t in axis.ticks if t.value == value and any(abs(t.pixel-r) < 0.01 for r in near)]
            if axis.model == "log10" and seated:
                # The log-ladder binder has already seated this labelled tick.
                # Nearby minor rules (e.g. 90 next to 100) do not own its label.
                close = [r for r in physical if min(abs(r-p) for p in seated) <= 3]
                near = close if len(close) == 1 else physical
            elif physical:
                near = physical
            elif seated:
                near = seated
            if len(near) != 1:
                observed.append({"value": value, "state": "unverified", "reason": "missing or ambiguous physical rule"})
                continue
            physical = near[0]
            residual = predicted - physical
            used = any(t.value == value for t in axis.ticks)
            accepted = abs(residual) <= 1.0
            observed.append({"value": value, "state": "measured", "rule_pixel": physical,
                             "fitted_pixel": predicted, "residual_px": residual,
                             "role": "used_tick" if used else "span_tick" if accepted else "diagnostic_only"})
            if not used and accepted:
                additions.append(AxisTick(group[-1][0], value, physical))
        updated[name + "_axis"] = replace(axis, ticks=tuple(sorted(axis.ticks + tuple(additions), key=lambda t: t.pixel)))
        measured = [abs(o["residual_px"]) for o in observed if o["state"] == "measured"]
        worst = max(measured) if measured else None
        report[name] = {"ticks": observed, "max_residual_px": worst,
                        "mapping_unchanged": True, "added_span_ticks": [t.value for t in additions]}
        unverified = [o["value"] for o in observed if o["state"] == "unverified"]
        if unverified:
            reasons.append(f"{name}_axis_tick_rule_unverified: {unverified}")
        if worst is None:
            reasons.append(f"{name}_axis_printed_rule_residual_unverified: no uniquely owned rules")
        elif worst > 1.0 + 1e-9:
            reasons.append(f"{name}_axis_printed_rule_residual_{worst:.3f}px: single {axis.model} map does not seat every printed tick")
    return replace(calibration, **updated), report, reasons


def blank_before_source_start(gray, calibration, points):
    """A raster start preceded by a fully blank plot strip is a source end.

    Erase only already verified grid rules. Any other ink in the entire strip
    (including a possible disconnected earlier curve) keeps the end unknown.
    """
    if gray is None or not points:
        return False
    plot = calibration.plot
    end = int(min(x for x, _ in points)) - 3
    start = plot.x0 + 5
    if end - start < 4:
        return False
    ink = gray[plot.y0 + 4:plot.y1 - 3, start:end] < 150
    if not ink.size:
        return False
    ink = ink.copy()
    for y in calibration.grid_y:
        k = int(round(y)) - (plot.y0 + 4)
        ink[max(0, k-3):max(0, k+4)] = False
    for x in calibration.grid_x:
        k = int(round(x)) - start
        ink[:, max(0, k-3):max(0, k+4)] = False
    return not bool(np.any(ink))


def recover_separate_contact_ink(traces, gray):
    """Add only raw, separately resolved curve bands in removed contact columns.

    The neighbour chord selects a band; the served y is that band's measured
    centre. Wide touching ink, competing bands, missing neighbours, and another
    trace in the same band all retain the contact refusal.
    """
    if gray is None:
        return traces
    out = []
    for trace in traces:
        added, unresolved = [], []
        pts = sorted(trace.points_px)
        for x in sorted(set(trace.contact_removed_x)):
            left = [p for p in pts if x-20 <= p[0] < x]
            right = [p for p in pts if x < p[0] <= x+20]
            if len(left) < 3 or len(right) < 3:
                unresolved.append(x)
                continue
            a, b = left[-1], right[0]
            pred = a[1] + (b[1]-a[1]) * (x-a[0]) / (b[0]-a[0])
            column = int(round(x))
            if not 0 <= column < gray.shape[1] or abs((b[1]-a[1]) / (b[0]-a[0])) > 0.5:
                unresolved.append(x)
                continue
            ys = np.flatnonzero(gray[:, column] < 150)
            runs = np.split(ys, np.flatnonzero(np.diff(ys) > 1)+1)
            near = [r for r in runs if len(r) and abs((r[0]+r[-1])/2-pred) <= 1.5 and len(r) <= 5]
            if len(near) != 1:
                unresolved.append(x)
                continue
            y = float((near[0][0]+near[0][-1])/2)
            shared = any(other is not trace and other.points_px and
                         abs(float(np.interp(x, *zip(*sorted(other.points_px))))-y) <= 3 for other in traces)
            if shared:
                unresolved.append(x)
                continue
            added.append((float(x), y))
        if not added:
            out.append(trace)
            continue
        note = {"from_px": list(added[0]), "to_px": list(added[-1]), "mode": "separate_annotation_contact_ink",
                "measured_px": added, "bridged_on_rule_px": []}
        out.append(replace(trace, points_px=trace.points_px[:-1]+added+trace.points_px[-1:],
                           contact_removed_x=unresolved, gap_traced=trace.gap_traced+[note]))
    return out


def single_colored_trace_evidence(image, trace, plot):
    """Prove a single recovered trace covers ALL chromatic ink in its frame.

    This permits a nearby current on a single coloured plot (ME95N03T).
    Black-only plots retain the existing untraced-branch guard. No fraction of
    unexplained colour is forgiven: even a small second branch defeats it.
    """
    import cv2
    crop = image[plot.y0+2:plot.y1-1,plot.x0+2:plot.x1-1]
    if crop.ndim != 3 or not crop.size or len(trace.points_px) < 30:
        return None
    spread = crop.max(axis=2).astype(float)-crop.min(axis=2).astype(float)
    ys,xs = np.nonzero((spread > 40) & (crop.min(axis=2) < 160))
    if len(xs) < 100:
        return None
    mask = ((spread > 40) & (crop.min(axis=2) < 160)).astype(np.uint8)
    if cv2.connectedComponents(mask, 8)[0] != 2:
        return None
    # Distance to the traced polyline, not to sparsely sampled vertices on a
    # steep head. This mask is used only for ownership; no interpolated point
    # is added to the exported curve.
    path = np.ones(image.shape[:2], np.uint8)
    points = np.asarray(sorted(trace.points_px), dtype=float)
    cv2.polylines(path, [np.rint(points).astype(np.int32)], False, 0, 1)
    distances = cv2.distanceTransform(path, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    distance = distances[ys+plot.y0+2,xs+plot.x0+2]
    if not np.isfinite(distance).all() or float(distance.max()) > 10:
        return None
    return {"source": "all_chromatic_ink_in_frame", "pixels": len(xs), "max_distance_to_trace_px": float(distance.max())}
