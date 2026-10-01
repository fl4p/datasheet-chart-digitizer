"""Conservative, per-PDF duplicate suppression after RDS(VGS) extraction.

This changes panel selection only. Pixels, calibration, curves and verdicts of
the winner are untouched. See docs/rds-vgs-duplicates.md for calibration and
the subpixel allowance needed on almost vertical curve heads.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import time
from pathlib import Path

import cv2
import numpy as np

VISUAL_MIN = 0.86
INK_THRESHOLD = 180
IMAGE_SIZE = 256
BLUR_SIGMA = 1.0
VALUE_REL_TOL = 0.005
X_SPAN_TOL = 0.0005
AXIS_RANGE_TOL = 0.0005
MIN_SAMPLES = 5


class Unevaluable(ValueError):
    """Missing evidence must never become an affirmative duplicate decision."""


class Conflict(ValueError):
    """The two evaluable panels disagree."""


def _finite(value):
    result = np.asarray(value, dtype=float)
    if not np.isfinite(result).all():
        raise Unevaluable("nonfinite_numeric_evidence")
    return result


def _identity(row):
    return {k: row.get(k) for k in ("pdf", "part", "page", "diagram", "crop_png")}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _plot_ink(row, root):
    path = root / row["crop_png"]
    raw = path.read_bytes()
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise Unevaluable("crop_not_decodable")
    box = row["plot_box_px"]
    x0, y0, x1, y1 = _finite([box[k] for k in ("x0", "y0", "x1", "y1")])
    if not (0 <= x0 < x1 < image.shape[1] and 0 <= y0 < y1 < image.shape[0]
            and min(x1-x0, y1-y0) >= 32):
        raise Unevaluable("plot_box_outside_crop_or_too_small")
    # Two source pixels exclude the frame; no free translation, warping or
    # best-match search can align a genuinely different curve into a match.
    xs = np.linspace(x0+2, x1-2, IMAGE_SIZE, dtype=np.float32)
    ys = np.linspace(y0+2, y1-2, IMAGE_SIZE, dtype=np.float32)
    crop = cv2.remap(image, *np.meshgrid(xs, ys), cv2.INTER_LINEAR)
    ink = cv2.GaussianBlur((crop < INK_THRESHOLD).astype(np.float32), (0, 0), BLUR_SIGMA)
    if not 0.002 < float(ink.mean()) < 0.8 or float(ink.std()) < 0.01:
        raise Unevaluable("blank_or_degenerate_plot_ink")
    return ink, hashlib.sha256(raw).hexdigest()


def visual_evidence(a, b, root):
    left, lhash = _plot_ink(a, root)
    right, rhash = _plot_ink(b, root)
    left = left.astype(float).ravel() - left.mean()
    right = right.astype(float).ravel() - right.mean()
    score = float(np.sum(left*right) / np.sqrt(np.sum(left*left)*np.sum(right*right)))
    _finite(score)
    return {"visual_score": score, "crop_sha256": [lhash, rhash]}


def _axes(row):
    cal, box = row["calibration"], row["plot_box_px"]
    if cal["grid_binding"] != "snapped_to_full_span_grid":
        raise Unevaluable("calibration_not_bound_to_grid")
    scale = float(_finite(cal["y_to_mohm"]))
    if scale <= 0 or cal["x_quantity"] != "VGS [V]" or "unit unread" in cal["y_quantity"]:
        raise Unevaluable("axis_units_unresolved")
    result = []
    for name, edges in (("x_axis", ("x0", "x1")), ("y_axis", ("y1", "y0"))):
        axis = cal[name]
        if axis["model"] not in ("linear", "log10"):
            raise Unevaluable("axis_model_unknown")
        ticks = _finite([t["value"] for t in axis["ticks"]])
        if len(np.unique(ticks)) < 2:
            raise Unevaluable("too_few_calibration_ticks")
        m, b = _finite([axis["m"], axis["b"]])
        values = m*_finite([box[e] for e in edges])+b
        if axis["model"] == "log10":
            values = 10.0**values
        _finite(values)
        if m == 0 or values[1] <= values[0]:
            raise Unevaluable("degenerate_axis_range")
        result.append((axis["model"], np.sort(ticks), values))
    return result, scale


def _curve_key(curve):
    for key in ("id_a", "temperature_c"):
        if curve.get(key) is None or not curve.get("parameter_binding", {}).get(key):
            raise Unevaluable(f"unbound_{key}")
        _finite(curve[key])
    if curve.get("temperature_kind") not in ("Tc", "Tj", "Ta"):
        raise Unevaluable("unbound_temperature_kind")
    return tuple(curve[k] for k in ("id_a", "temperature_c", "temperature_kind"))


def _points(curve):
    points = _finite(curve["points"])
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < MIN_SAMPLES:
        raise Unevaluable("too_few_curve_samples")
    if not curve["usable"] or curve.get("gaps") or curve.get("untraced_section_reasons"):
        raise Unevaluable("curve_unusable_or_incomplete")
    if np.any(np.diff(points[:, 0]) < 0) or np.any(points[:, 1] <= 0):
        raise Unevaluable("invalid_curve_coordinates")
    if points[-1, 0] <= points[0, 0]:
        raise Unevaluable("degenerate_curve_domain")
    return points


def _directed_value_diff(p, q, dx):
    """Every sample vs the other polyline, within a bounded x interval.

    Segment clipping handles vertical runs and arbitrary y direction. No
    extrapolation, percentile trimming, sparse resampling or skipped heads.
    """
    q0, q1 = q[:-1], q[1:]
    step = q1[:, 0]-q0[:, 0]
    safe = np.where(step > 0, step, 1.0)
    worst = 0.0
    for chunk in np.array_split(p, max(1, (len(p)+127)//128)):
        low, high = chunk[:, 0, None]-dx, chunk[:, 0, None]+dx
        valid = (q1[:, 0] >= low) & (q0[:, 0] <= high)
        t0 = np.clip((low-q0[:, 0])/safe, 0, 1)
        t1 = np.clip((high-q0[:, 0])/safe, 0, 1)
        t0[:, step == 0], t1[:, step == 0] = 0, 1
        y0 = q0[:, 1]+t0*(q1[:, 1]-q0[:, 1])
        y1 = q0[:, 1]+t1*(q1[:, 1]-q0[:, 1])
        y = chunk[:, 1, None]
        nearest = np.clip(y, np.minimum(y0, y1), np.maximum(y0, y1))
        error = np.abs(y-nearest)/np.maximum(np.abs(y), np.abs(nearest))
        error[~valid] = np.inf
        best = error.min(axis=1)
        if not np.isfinite(best).all():
            raise Conflict("curve_domain_differs")
        worst = max(worst, float(best.max()))
    return worst


def _readout_diff(c, d):
    left, right = c["readouts"], d["readouts"]
    if not left or not right:
        raise Unevaluable("readouts_missing")
    if len(left) != len(right):
        raise Conflict("readout_count_differs")
    worst, measured = 0.0, 0
    for a, b in zip(left, right):
        _finite([a["vgs_v"], b["vgs_v"]])
        if any(a.get(k) != b.get(k) for k in ("vgs_v", "status", "calibration_span")):
            raise Conflict("readout_target_or_state_differs")
        x, y = a["rds_mohm"], b["rds_mohm"]
        if x is None or y is None:
            if x != y:
                raise Conflict("readout_availability_differs")
            if a["status"] == "read":
                raise Unevaluable("readout_value_missing")
            continue
        _finite([x, y])
        if min(x, y) <= 0:
            raise Unevaluable("readout_value_invalid")
        measured += 1
        worst = max(worst, abs(x-y)/max(abs(x), abs(y)))
    if measured == 0:
        raise Unevaluable("no_comparable_readouts")
    return worst


def data_evidence(a, b):
    if any(r.get("status") not in ("ok", "review_required") for r in (a, b)):
        raise Unevaluable("panel_refused_or_status_unknown")
    if a["status"] != b["status"] or a["validation"]["verdict"] != b["validation"]["verdict"]:
        raise Conflict("panel_status_or_verdict_differs")
    aa, sa = _axes(a)
    bb, sb = _axes(b)
    if sa != sb:
        raise Conflict("axis_units_differ")
    for (am, at, ar), (bm, bt, br) in zip(aa, bb):
        if am != bm:
            raise Conflict("axis_models_differ")
        if len(at) != len(bt) or not np.allclose(at, bt, rtol=1e-9, atol=1e-10):
            raise Conflict("calibrated_ticks_differ")
        if np.max(np.abs(ar-br)) > AXIS_RANGE_TOL*min(np.ptp(ar), np.ptp(br)):
            raise Conflict("calibrated_ranges_differ")
    left, right = a["curves"], b["curves"]
    if not left or not right:
        raise Unevaluable("curves_missing")
    if len(left) != len(right):
        raise Conflict("curve_count_differs")
    lc, rc = {_curve_key(c): c for c in left}, {_curve_key(c): c for c in right}
    if len(lc) != len(left) or len(rc) != len(right):
        raise Unevaluable("ambiguous_curve_identity")
    if lc.keys() != rc.keys():
        raise Conflict("bound_labels_differ")
    dx = X_SPAN_TOL*min(np.ptp(aa[0][2]), np.ptp(bb[0][2]))
    curve_diff = read_diff = 0.0
    for key in sorted(lc):
        c, d = lc[key], rc[key]
        p, q = _points(c), _points(d)
        curve_diff = max(curve_diff, _directed_value_diff(p, q, dx), _directed_value_diff(q, p, dx))
        read_diff = max(read_diff, _readout_diff(c, d))
    return {"max_value_diff": max(curve_diff, read_diff), "max_curve_value_diff": curve_diff,
            "max_readout_diff": read_diff, "curve_x_tolerance_v": float(dx)}


def compare_panels(a, b, root):
    evidence = {"panels": [_identity(a), _identity(b)], "panel_sha256": [_digest(a), _digest(b)],
                "visual_score": None, "max_value_diff": None,
                "visual_threshold": VISUAL_MIN, "value_relative_tolerance": VALUE_REL_TOL,
                "curve_x_span_tolerance": X_SPAN_TOL}
    if not a.get("pdf") or not b.get("pdf") or Path(a["pdf"]).resolve() != Path(b["pdf"]).resolve():
        return evidence | {"decision": "distinct", "reasons": ["different_pdf"]}
    states, reasons = {}, []
    for name, check in (("visual", lambda: visual_evidence(a, b, root)),
                        ("data", lambda: data_evidence(a, b))):
        try:
            evidence.update(check())
            states[name] = "evaluated"
        except Conflict as error:
            states[name] = "conflict"
            reasons.append(str(error))
        except Exception as error:  # fail closed, including corrupt assets and unexpected probe failures
            states[name] = "unevaluable"
            reasons.append(f"{name}: {type(error).__name__}: {error}")
    if states["data"] == "evaluated" and evidence["max_value_diff"] > VALUE_REL_TOL:
        states["data"] = "conflict"
        reasons.append("curve_or_readout_value_differs")
    if "unevaluable" in states.values():
        decision = "unevaluable"
    elif evidence["visual_score"] < VISUAL_MIN:
        decision = "distinct"
        reasons.append("plot_ink_differs")
    elif states["data"] == "conflict":
        decision = "conflict"
    else:
        decision = "duplicate"
    return evidence | {"decision": decision, "reasons": reasons, "checks": states}


def _preference(row):
    numbered = bool(re.fullmatch(r"\d+(?:[.\-]\d+)*[a-z]?", str(row.get("diagram", "")), re.I))
    box = row.get("plot_box_px") or {}
    try:
        area = float(_finite((box.get("x1", 0)-box.get("x0", 0))*(box.get("y1", 0)-box.get("y0", 0))))
    except (TypeError, ValueError):
        area = 0
    return (not numbered, -area, row.get("page", 0), str(row.get("diagram", "")), _digest(row))


def deduplicate_pdf(rows, pdf, root):
    start = time.perf_counter()
    checks, lookup = [], {}
    for i, j in itertools.combinations(range(len(rows)), 2):
        check = compare_panels(rows[i], rows[j], root)
        checks.append(check)
        lookup[frozenset((i, j))] = check
    groups, discarded = [], []
    for i in sorted(range(len(rows)), key=lambda n: _preference(rows[n])):
        # Complete linkage: similarity is not transitive. Every member of a
        # group must independently pass against every other member.
        group = next((g for g in groups if all(lookup[frozenset((i, j))]["decision"] == "duplicate"
                                               for j in g)), None)
        if group is None:
            groups.append([i])
            continue
        winner = rows[group[0]]
        proof = lookup[frozenset((i, group[0]))]
        note = _identity(rows[i]) | {k: v for k, v in proof.items() if k != "panels"}
        winner.setdefault("also_printed_at", []).append(note)
        discarded.append({"kept": _identity(winner), "discarded": _identity(rows[i]), "evidence": proof})
        group.append(i)
    kept = {g[0] for g in groups}
    audit = {"pdf": str(pdf), "duplicate_checks": checks, "discarded_duplicates": discarded,
             "seconds": time.perf_counter()-start}
    return [r for i, r in enumerate(rows) if i in kept], audit


def write_audit(audit, pdf, root):
    """No decision cache: each invocation probes the actual output and crops."""
    code = hashlib.sha256()
    package = Path(__file__).parent
    for path in sorted(package.rglob("*.py")):
        code.update(str(path.relative_to(package)).encode()+b"\0"+path.read_bytes())
    audit["source_sha256"] = code.hexdigest()
    audit["pdf_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    folder = root / "duplicate_checks"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{pdf.name}.{_digest(str(pdf.resolve()))[:12]}.json"
    # Wall time is run telemetry, not reproducible evidence. Keep it in the
    # optional caller audit; cached and live artifact trees must match.
    target.write_text(json.dumps({k: v for k, v in audit.items() if k != "seconds"}, indent=2)+"\n")
