"""Per-VDD gate-charge curves: the ``per_vdd`` block of a gate-charge result.

Output contract (``GateChargeResult.per_vdd``, serialized by ``to_manifest``)::

    per_vdd = {
      "method": "vector_paths" | "raster_tracks" | "legacy_single" | None,
      "curve_count": K | None,          # None: the curves could not be separated
      "binding": "single_curve" | "all_bound" | "partly_bound" | "refused" | "unseparated",
      "physics": "consistent" | "contradicted" | "not_evaluable",
      "labels": [{text, vdd_v, bbox_px, curve, rule, reason}],
      "plateau": {"shared": bool | None, "vpl": V | None, "vpl_y_px": px | None,
                  "status": "ok" | <legacy status> | "not_shared" | "unverified"},
      "shared_curve_px": [[x, y], ...],  # the part every curve shares (rise, plateau)
      "curves": [{index, vdd_v, label, binding_rule, status, curve_px, vpl, vpl_y_px,
                  plateau_x_px, plateau_qg, gates}],
      "legacy_curve": {"index": i | None, "rule": ...},
      "diagnostics": [...],
    }

Curves are ordered left to right above the plateau (lowest VDD first by physics).
A curve's ``status`` is ``ok`` only when the chart is ``ok``, its own curve
gates pass and its VDD is bound; ``unbound`` when everything but the identity
passes (its points are then withheld: identity is refused, never guessed);
``low_confidence`` when a curve gate fails; ``withheld`` when the chart is not
ok. Only ``ok`` curves serialize points and Vpl.

**Legacy single curve** (``curve_px``/``vpl``/``status``, unchanged meaning):
the curve the class has always selected while it lies on a source curve
(coincident strokes included). A selection on no single source curve -- a
vector blend of two strokes (``served_trace_blends_source_strokes``), or a
trace that switches strokes after a plateau every curve shares -- is replaced
by the leftmost separated curve above the plateau: the lowest VDD by physics,
the branch ``_terminal_bundle_upper_branch`` already follows on bundles it
recognises. A switching trace keeps its own Vpl reading (same shared plateau);
with per-curve plateaus it is kept and flagged
``legacy_curve_switches_source_curves``. The choice needs no label, so it holds
when identity is refused. Alternatives (the curve at the datasheet's Qg test
VDD; withholding the legacy curve) are in out/gc-per-vdd/README.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .gate_charge_separation import (
    Separation,
    plateau_run,
    separate_raster_curves,
    separate_vector_curves,
)
from .gate_charge_vdd_labels import bind_labels, parse_labels

ON_CURVE_FRACTION = 0.9
SHARED_PLATEAU_PX = 2.0
SHARED_PLATEAU_FRACTION = 0.006


@dataclass
class Analysis:
    separation: Separation | None
    curves: list[list[tuple[int, int]]] = field(default_factory=list)
    cut_short: list[bool] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

    @property
    def separated(self) -> bool:
        return self.separation is not None and self.separation.separated and len(self.curves) >= 2

    @property
    def half_width_px(self) -> float:
        if self.separation is not None and self.separation.curves and self.separation.curves[0].half_width_pt:
            return self._scale * self.separation.curves[0].half_width_pt
        return 1.0

    _scale: float = 1.0


def analyse(page, rect, scale, plot_box, trace_crop, legacy_curve, trim, ink_mask) -> Analysis:
    """Separate the source curves and apply the legacy trims to each.

    ``trim(curve) -> (curve, cut_short)`` is the class's own trim chain;
    ``ink_mask(crop, plot_box)`` the raster tracer's ink mask (ROI coords).
    """

    separation = separate_vector_curves(page, rect, scale, plot_box)
    if not separation.curves and not separation.refused:
        ink = ink_mask(trace_crop, plot_box)
        if ink is not None:
            roi, _gray = ink
            mask = np.zeros((trace_crop.height, trace_crop.width), dtype=np.uint8)
            x0, y0 = plot_box[0], plot_box[1]
            mask[y0 : y0 + roi.shape[0], x0 : x0 + roi.shape[1]] = roi
            run = plateau_run(legacy_curve)
            raster = separate_raster_curves(mask > 0, plot_box, legacy_curve, None if run is None else run[2])
            raster.diagnostics = separation.diagnostics + raster.diagnostics
            separation = raster
            separation.mask = mask > 0  # type: ignore[attr-defined]
    analysis = Analysis(separation, _scale=scale)
    analysis.diagnostics.extend(separation.diagnostics)
    if separation.separated:
        for source in separation.curves:
            curve, cut = trim(list(source.points_px))
            analysis.curves.append(curve)
            analysis.cut_short.append(cut)
    return analysis


def on_fraction(points, curve, tol) -> float:
    if not points or len(curve) < 2:
        return 0.0
    p = np.asarray(points, dtype=float)
    c = np.asarray(curve, dtype=float)
    a, b = c[:-1], c[1:]
    d = b - a
    length2 = np.maximum((d**2).sum(axis=1), 1e-12)
    out = 0
    for chunk in np.array_split(p, max(1, len(p) // 400)):
        rel = chunk[:, None, :] - a[None, :, :]
        t = np.clip((rel * d[None]).sum(axis=2) / length2[None], 0.0, 1.0)
        near = a[None] + t[:, :, None] * d[None]
        dist = np.sqrt(((chunk[:, None, :] - near) ** 2).sum(axis=2)).min(axis=1)
        out += int((dist <= tol).sum())
    return out / len(p)


def lowest_curve(analysis: Analysis) -> int:
    """Index of the leftmost curve above the plateau (curves arrive sorted)."""

    return 0


def plateau_shared(analysis: Analysis, plot_box) -> bool:
    """Every separated curve has a plateau, all at one height."""

    runs = [plateau_run(c) for c in analysis.curves]
    if not runs or any(r is None for r in runs):
        return False
    tolerance = max(SHARED_PLATEAU_PX, SHARED_PLATEAU_FRACTION * max(1, plot_box[3] - plot_box[1]))
    return max(r[2] for r in runs) - min(r[2] for r in runs) <= tolerance


def legacy_fit(analysis: Analysis, legacy_curve) -> tuple[int | None, str]:
    """(index, state): state "on_one" (index set), "on_several" (the curves it
    lies on coincide where it runs: identity unknown, but it IS a source curve)
    or "off_curve" (on no single source curve: a blend or a stroke switch)."""

    tol = analysis.half_width_px + 2.0
    fractions = [on_fraction(legacy_curve, curve, tol) for curve in analysis.curves]
    if not fractions:
        return None, "off_curve"
    best = int(np.argmax(fractions))
    if fractions[best] < ON_CURVE_FRACTION:
        return None, "off_curve"
    if any(k != best and f > fractions[best] - 0.05 for k, f in enumerate(fractions)):
        return None, "on_several"
    return best, "on_one"


def legacy_index(analysis: Analysis, legacy_curve) -> int | None:
    return legacy_fit(analysis, legacy_curve)[0]


def _shared_prefix(curves, tol) -> list[tuple[int, int]]:
    first = curves[0]
    others = [np.asarray(c, dtype=float) for c in curves[1:]]
    shared = []
    for x, y in first:
        if all(np.min(np.hypot(o[:, 0] - x, o[:, 1] - y)) <= tol for o in others):
            shared.append((x, y))
        elif shared:
            break
    return shared


def build_payload(
    analysis: Analysis,
    *,
    legacy_curve,
    legacy_vpl_y_px,
    legacy_rule: str,
    gates,
    text_page,
    rect,
    scale,
    plot_box,
) -> dict:
    """The JSON-safe per_vdd block (values in px; finalize() adds VGS/Qg)."""

    import pymupdf

    sep = analysis.separation
    x0, y0, x1, y1 = plot_box
    plot_pdf = pymupdf.Rect(rect.x0 + x0 / scale, rect.y0 + y0 / scale, rect.x0 + x1 / scale, rect.y0 + y1 / scale)
    labels = parse_labels(text_page, plot_pdf)
    payload: dict = {
        "method": None,
        "curve_count": None,
        "binding": "unseparated",
        "physics": "not_evaluable",
        "labels": [],
        "plateau": {"shared": None, "vpl": None, "vpl_y_px": None, "status": "unverified"},
        "shared_curve_px": [],
        "curves": [],
        "legacy_curve": {"index": None, "rule": legacy_rule},
        "diagnostics": list(dict.fromkeys(analysis.diagnostics)),
    }
    if sep is not None and sep.refused:
        payload["labels"] = [label.payload(rect, scale) for label in labels]
        return payload
    if analysis.separated:
        curves = analysis.curves
        cut_short = analysis.cut_short
        payload["method"] = sep.method
    elif len(legacy_curve) >= 20:
        # one source curve: the legacy trace is that curve
        curves = [list(legacy_curve)]
        cut_short = [False]
        payload["method"] = "legacy_single"
    else:
        payload["labels"] = [label.payload(rect, scale) for label in labels]
        return payload
    k = len(curves)
    payload["curve_count"] = k
    index, state = (0, "on_one") if k == 1 else legacy_fit(analysis, legacy_curve)
    payload["legacy_curve"]["index"] = index
    if state == "off_curve":
        payload["diagnostics"].append("legacy_curve_on_no_single_source_curve")
    elif state == "on_several":
        payload["diagnostics"].append("legacy_curve_on_coincident_source_curves")

    runs = [plateau_run(c) for c in curves]
    height = max(1, y1 - y0)
    tolerance = max(SHARED_PLATEAU_PX, SHARED_PLATEAU_FRACTION * height)
    if all(r is not None for r in runs):
        ys = [r[2] for r in runs]
        shared = max(ys) - min(ys) <= tolerance
        if shared and legacy_vpl_y_px is not None and abs(float(np.median(ys)) - legacy_vpl_y_px) > tolerance + 1.0:
            payload["diagnostics"].append("legacy_vpl_off_shared_plateau")
            shared = None
    else:
        shared = None if k > 1 else (legacy_vpl_y_px is not None)
        if k > 1:
            payload["diagnostics"].append("per_curve_plateau_unresolved")
    payload["plateau"]["shared"] = shared
    if shared:
        payload["plateau"]["vpl_y_px"] = legacy_vpl_y_px

    mask = getattr(sep, "mask", None) if sep is not None else None
    binding = bind_labels(labels, sep if analysis.separated else None, curves, rect, scale, plot_box, mask)
    payload["labels"] = [label.payload(rect, scale) for label in binding.labels]
    payload["physics"] = binding.physics
    payload["diagnostics"].extend(binding.diagnostics)
    by_curve = {label.curve: label for label in binding.labels if label.curve is not None}
    bound = len(by_curve)
    if k == 1:
        payload["binding"] = "single_curve"
    else:
        payload["binding"] = "all_bound" if bound == k else ("partly_bound" if bound else "refused")
        payload["shared_curve_px"] = [list(p) for p in _shared_prefix(curves, analysis.half_width_px + 1.5)]

    for i, curve in enumerate(curves):
        label = by_curve.get(i)
        run = runs[i]
        own_y = None if run is None else run[2]
        payload["curves"].append({
            "index": i,
            "vdd_v": None if label is None else label.value,
            "label": None if label is None else label.text,
            "binding_rule": None if label is None else label.rule,
            "curve_px": [list(p) for p in curve],
            "vpl_y_px": legacy_vpl_y_px if shared else own_y,
            "plateau_x_px": None if run is None else [run[0], run[1]],
            "gates": gates(curve, cut_short[i]),
            "x_at_common_level_px": None,
            "status": None,
        })
    if k > 1 and analysis.separated:
        from .gate_charge_separation import x_at_common_level

        for i, entry in enumerate(payload["curves"]):
            x = x_at_common_level(analysis.separation.curves, i)
            entry["x_at_common_level_px"] = None if x is None else round(float(x), 2)
    return payload


def _fit(ticks):
    if len(ticks) < 2:
        return None
    values = np.asarray([v for v, _ in ticks], dtype=float)
    pixels = np.asarray([p for _, p in ticks], dtype=float)
    if np.ptp(pixels) <= 0:
        return None
    return np.polyfit(pixels, values, 1)


def finalize(result) -> dict | None:
    """Statuses and physical values from the result's FINAL ticks and status."""

    payload = result.per_vdd
    if not payload:
        return payload
    payload = {**payload, "curves": [dict(c) for c in payload["curves"]], "plateau": dict(payload["plateau"])}
    chart_ok = result.status == "ok"
    y_fit = _fit(result.y_ticks_px)
    x_fit = _fit(result.x_ticks_px) if result.x_tick_unit else None
    plateau = payload["plateau"]
    if plateau["shared"]:
        plateau["vpl"] = result.vpl
        plateau["status"] = result.status
    elif plateau["shared"] is False:
        plateau["status"] = "not_shared"
    else:
        plateau["status"] = "unverified"
    for entry in payload["curves"]:
        if plateau["shared"]:
            entry["vpl"] = result.vpl
        elif entry["vpl_y_px"] is not None and y_fit is not None:
            entry["vpl"] = float(np.polyval(y_fit, entry["vpl_y_px"]))
        else:
            entry["vpl"] = None
        entry["plateau_qg"] = (
            [float(np.polyval(x_fit, v)) for v in entry["plateau_x_px"]]
            if x_fit is not None and entry["plateau_x_px"] else None
        )
        if not chart_ok:
            entry["status"] = "withheld"
        elif entry["gates"]:
            entry["status"] = "low_confidence"
        elif payload["curve_count"] and payload["curve_count"] > 1 and entry["vdd_v"] is None:
            entry["status"] = "unbound"
        else:
            entry["status"] = "ok"
    return payload


def serialize(payload: dict | None, chart_ok: bool) -> dict | None:
    """Withhold every untrusted number and point (fail-closed manifest)."""

    if not payload:
        return payload
    out = {**payload, "plateau": dict(payload["plateau"]), "curves": []}
    if not chart_ok or out["plateau"]["status"] != "ok":
        out["plateau"]["vpl"] = None
        out["plateau"]["vpl_y_px"] = None
    if not chart_ok:
        out["shared_curve_px"] = []
    for entry in payload["curves"]:
        entry = dict(entry)
        if entry.get("status") != "ok":
            entry["curve_px"] = []
            entry["vpl"] = None
            entry["vpl_y_px"] = None
            entry["plateau_qg"] = None
        out["curves"].append(entry)
    return out


def curve_gates(curve, cut_short, *, plot_box, gray, frame, monotone, missing_ramp, clipped) -> list[str]:
    """The legacy curve-fidelity gates, applied to one separated curve."""

    failed = []
    if len(curve) < 20:
        failed.append("insufficient_curve_points")
    if missing_ramp(curve, plot_box):
        failed.append("curve_missing_initial_ramp")
    if not monotone(curve, plot_box):
        failed.append("non_monotone_gate_curve")
    if clipped(gray, curve, plot_box, frame):
        failed.append("plot_box_clips_source_curve")
    if cut_short:
        failed.append("branch_cut_short")
    return failed


def unseparated(reason: str, legacy_rule: str) -> dict:
    return {
        "method": None, "curve_count": None, "binding": "unseparated", "physics": "not_evaluable",
        "labels": [], "plateau": {"shared": None, "vpl": None, "vpl_y_px": None, "status": "unverified"},
        "shared_curve_px": [], "curves": [], "legacy_curve": {"index": None, "rule": legacy_rule},
        "diagnostics": [reason],
    }


def build_safely(analysis, error, **kwargs) -> dict:
    """build_payload, with an exception recorded -- never read as a result."""

    if error is None and analysis is not None:
        try:
            return build_payload(analysis, **kwargs)
        except Exception as exc:
            error = f"per_vdd_error:{type(exc).__name__}:{exc}"[:200]
    return unseparated(error or "per_vdd_not_analysed", kwargs.get("legacy_rule", "unchanged"))
