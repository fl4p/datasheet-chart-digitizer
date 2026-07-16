"""Export digitized Coss/Crss to dslib-style (Vds_V, Coss_pF, Crss_pF) knot triples.

Consumes a `capacitance_digitization.json` manifest (from `dsdig digitize-capacitance`)
and emits, per chart, a compact JSON suitable for machine consumption by downstream
parts DBs (pwr-mosfet-lib `dslib/coss_curves.py` format: knot triples, low V -> high V).

This is the machine-validated path for AUTO-digitized curves, so the acceptance gate is
strict and monotone: a chart that cannot be fully validated is REJECTED with reasons —
missing anchors, an untrusted axis fit, or a failed downstream check never degrade to a
silently-exported curve.  Gates:

  * axis_calibration_trusted is True (position-based axis fit agreed with gridlines)
  * trace_validation_status == "pass" (semantic shape checks: rank order, spans)
  * qoss_validation_status == "pass" (Qoss integral consistency)
  * table anchors for Coss AND Crss exist (from the datasheet `.nop.csv` spec table)
    and the digitized traces agree with them at the anchor Vds within tolerance
    (default 8% Coss / 15% Crss — curated curves historically land within ~2%).

The exported curve keeps the digitizer's values (no snapping); anchor agreement is
reported so a consumer can decide to snap.  Coss knots are selected with the adaptive
log-space model (max_rel_error target), Crss is interpolated onto the same knots.

Anchor voltages are additionally pinned as explicit knots: the adaptive knots are
error-optimal for LOG-space interpolation, but dslib consumers interpolate LINEARLY,
and when the knots merely straddle the spec-table Vds the linear chord reads the convex
knee high (measured +2.4% on IPP040N08NF2S at the 40 V anchor).  Since the anchor V is
exactly the point every downstream cross-check probes, each anchor gets a knot carrying
the digitized (not snapped) values there.

Ciss is exported alongside as OPTIONAL (Vds_V, Ciss_pF) pairs (dslib `CISS_CURVES`
format) with its own tri-state verdict (`ciss_status`: pass/absent/rejected) and its own
anchor gate plus a Ciss>Crss consistency gate (downstream derives Cgs = Ciss - Crss).
Ciss gates can only withhold the Ciss curve — they never rescue or degrade the triple.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .coss_export import (
    build_adaptive_coss_model,
    evaluate_coss_knots,
    load_coss_points_csv,
)

# Tolerated |digitized/anchor - 1| at the anchor Vds. Crss is small (tens of pF) and
# raster-noisier, hence the looser gate.
ANCHOR_TOL_COSS = 0.08
ANCHOR_TOL_CRSS = 0.15
# Ciss is the largest, flattest trace (vector traces land within ~2%), so it gets the
# strict Coss-grade gate.
ANCHOR_TOL_CISS = 0.08


@dataclass
class DslibCossResult:
    """One chart's export verdict. `status` is "pass" only when EVERY gate passed and a
    curve was produced; any other outcome is "rejected" with machine-readable reasons.

    Ciss is exported SEPARATELY (dslib CISS_CURVES pairs) and is strictly optional:
    its gates can only withhold `ciss_curve`, never rescue or degrade the triple.
    `ciss_status` is tri-state — "pass" (curve exported), "absent" (the chart carries
    no Ciss evidence at all), or "rejected" (Ciss evidence exists but failed a gate).
    Absence of evidence never exports a curve."""

    part: str
    diagram: str
    status: str                      # "pass" | "rejected"
    reasons: list = field(default_factory=list)
    curve: list = field(default_factory=list)   # [(Vds_V, Coss_pF, Crss_pF), ...]
    anchor_check: dict = field(default_factory=dict)
    qoss_pc: float | None = None
    knots: int = 0
    source_points: int = 0
    overlay: str | None = None
    points_csv: str | None = None
    pdf: str | None = None
    ciss_status: str = "rejected"    # "pass" | "absent" | "rejected"
    ciss_reasons: list = field(default_factory=lambda: ["chart_rejected"])
    ciss_curve: list = field(default_factory=list)  # [(Vds_V, Ciss_pF), ...]


def _interp_at(vds: np.ndarray, cap: np.ndarray, v: float, v_scale: float) -> float:
    return float(evaluate_coss_knots(np.asarray([v], float), vds, cap, v_scale)[0])


def export_row(row: dict, base_dir: Path, *, max_rel_error: float = 0.02,
               max_knots: int = 48) -> DslibCossResult:
    """Validate one manifest row and build its dslib knot triples.

    Gate-first: every reason is collected (not short-circuited) so a rejection names
    everything wrong with the chart, then the curve is only built on a clean slate.
    """
    part = str(row.get("part") or "?")
    diagram = str(row.get("diagram") or "?")
    res = DslibCossResult(part=part, diagram=diagram, status="rejected",
                          overlay=_abs_or_none(row.get("overlay"), base_dir),
                          points_csv=_abs_or_none(row.get("points"), base_dir),
                          pdf=row.get("pdf"))

    if row.get("axis_calibration_trusted") is not True:
        res.reasons.append("axis_calibration_not_trusted")
    if row.get("trace_validation_status") != "pass":
        res.reasons.append(
            f"trace_validation:{row.get('trace_validation_status')}"
            f":{','.join(row.get('trace_validation_reasons') or [])}")
    if row.get("qoss_validation_status") != "pass":
        res.reasons.append(f"qoss_validation:{row.get('qoss_validation_status')}"
                           f":{row.get('qoss_validation_error')}")

    anchors = row.get("anchors") or {}
    for name in ("Coss", "Crss"):
        a = anchors.get(name)
        if not a or not a.get("value_pf") or a.get("vds_v") is None:
            res.reasons.append(f"missing_{name.lower()}_anchor")

    if res.points_csv is None or not Path(res.points_csv).exists():
        res.reasons.append("missing_points_csv")
        return res

    try:
        v_coss, c_coss = load_coss_points_csv(Path(res.points_csv), "Coss")
        v_crss, c_crss = load_coss_points_csv(Path(res.points_csv), "Crss")
    except ValueError as exc:
        res.reasons.append(f"points_load:{exc}")
        return res

    # Anchor agreement — digitized value at the spec-table Vds vs the table value.
    # Interpolate on the RAW cleaned samples (not the reduced knots) so the check
    # measures the digitization, not the knot reduction.
    v_scale = max(float(v_coss[-1] - v_coss[0]) * 0.01, 1e-6)
    for name, (vv, cc), tol in (("Coss", (v_coss, c_coss), ANCHOR_TOL_COSS),
                                ("Crss", (v_crss, c_crss), ANCHOR_TOL_CRSS)):
        a = anchors.get(name)
        if not a or not a.get("value_pf"):
            continue
        got = _interp_at(vv, cc, float(a["vds_v"]), v_scale)
        rel = got / float(a["value_pf"]) - 1.0
        res.anchor_check[name] = {"vds_v": float(a["vds_v"]),
                                  "spec_pf": float(a["value_pf"]),
                                  "digitized_pf": round(got, 4),
                                  "rel_error": round(rel, 4)}
        if not math.isfinite(rel) or abs(rel) > tol:
            res.reasons.append(f"{name.lower()}_anchor_mismatch:{rel:+.1%} (tol {tol:.0%})")

    if res.reasons:
        return res

    model = build_adaptive_coss_model(v_coss, c_coss, max_rel_error=max_rel_error,
                                      max_knots=max_knots)
    kv = np.asarray(model.vds, float)
    kc = np.asarray(model.coss, float)
    crss_scale = max(float(v_crss[-1] - v_crss[0]) * 0.01, 1e-6)
    kx = evaluate_coss_knots(kv, v_crss, c_crss, crss_scale)
    curve = [(float(v), _sig4(c), _sig4(x)) for v, c, x in zip(kv, kc, kx)]
    curve = _pin_anchor_knots(curve, anchors, (v_coss, c_coss), (v_crss, c_crss),
                              v_scale, crss_scale)
    if curve[0][0] > 0:
        # dslib curves start at Vds=0; consumers integrate Qoss/Eoss from 0 with a
        # flat-C hold below the first knot — make that hold explicit.
        curve.insert(0, (0.0, curve[0][1], curve[0][2]))
    res.curve = curve
    res.knots = len(curve)
    res.source_points = model.source_points
    qm = row.get("qoss_metrics") or {}
    res.qoss_pc = qm.get("Qoss_pc")
    res.status = "pass"
    _export_ciss(res, row, (v_crss, c_crss), crss_scale,
                 max_rel_error=max_rel_error, max_knots=max_knots)
    return res


def _export_ciss(res: DslibCossResult, row: dict, crss_pts, crss_scale: float, *,
                 max_rel_error: float, max_knots: int) -> None:
    """Attempt the optional Ciss export onto a triple that already passed every gate.

    Monotone by construction: every early exit leaves `ciss_curve` empty with an explicit
    tri-state verdict — "absent" only when the chart carries NO Ciss evidence (neither a
    digitized trace nor a spec-table anchor); any partial or failing evidence is
    "rejected" with reasons. A Ciss trace that cannot be anchor-validated is never
    exported. The triple's own status is deliberately untouched: Ciss gates can only
    withhold Ciss."""
    res.ciss_reasons = []
    res.ciss_status = "rejected"
    anchors = row.get("anchors") or {}
    a = anchors.get("Ciss") or {}
    have_anchor = bool(a.get("value_pf")) and a.get("vds_v") is not None
    if res.points_csv is None:      # unreachable post-gate; keep the guard monotone
        res.ciss_reasons.append("ciss_points_load:missing points csv")
        return

    try:
        v_ciss, c_ciss = load_coss_points_csv(Path(res.points_csv), "Ciss")
    except ValueError as exc:
        if have_anchor:
            res.ciss_reasons.append(f"ciss_points_load:{exc}")
        else:
            res.ciss_status = "absent"
            res.ciss_reasons = ["no_ciss_trace", "no_ciss_anchor"]
        return
    if not have_anchor:
        res.ciss_reasons.append("missing_ciss_anchor")
        return

    ciss_scale = max(float(v_ciss[-1] - v_ciss[0]) * 0.01, 1e-6)
    got = _interp_at(v_ciss, c_ciss, float(a["vds_v"]), ciss_scale)
    rel = got / float(a["value_pf"]) - 1.0
    res.anchor_check["Ciss"] = {"vds_v": float(a["vds_v"]),
                                "spec_pf": float(a["value_pf"]),
                                "digitized_pf": round(got, 4),
                                "rel_error": round(rel, 4)}
    if not math.isfinite(rel) or abs(rel) > ANCHOR_TOL_CISS:
        res.ciss_reasons.append(
            f"ciss_anchor_mismatch:{rel:+.1%} (tol {ANCHOR_TOL_CISS:.0%})")
        return

    # Downstream consumers derive Cgs = Ciss - Crss; a crossing (Ciss <= Crss anywhere
    # on the shared span) means at least one trace is mis-assigned — refuse.
    v_crss, c_crss = crss_pts
    lo, hi = max(v_ciss[0], v_crss[0]), min(v_ciss[-1], v_crss[-1])
    if hi <= lo:
        # No shared span at all: the crossing check below would degenerate into
        # comparing two boundary-clamped constants and trivially pass — the exact
        # anti-monotone false PASS this gate exists to prevent. Refuse instead.
        res.ciss_reasons.append("ciss_crss_no_overlap")
        return
    vv = np.linspace(lo, hi, 200)
    crss_i = evaluate_coss_knots(vv, v_crss, c_crss, crss_scale)
    ciss_i = evaluate_coss_knots(vv, v_ciss, c_ciss, ciss_scale)
    if not np.all(ciss_i > crss_i):
        res.ciss_reasons.append("ciss_not_above_crss")
        return

    model = build_adaptive_coss_model(v_ciss, c_ciss, max_rel_error=max_rel_error,
                                      max_knots=max_knots)
    pairs = [(float(v), _sig4(c)) for v, c in zip(model.vds, model.coss)]
    va = float(a["vds_v"])
    span = pairs[-1][0] - pairs[0][0]
    if (v_ciss[0] <= va <= v_ciss[-1]
            and not any(abs(p[0] - va) <= max(0.003 * span, 1e-3) for p in pairs)):
        # Same linear-interp rationale as _pin_anchor_knots: the anchor V is exactly
        # where downstream cross-checks probe, so it must be a knot.
        pairs.append((va, _sig4(got)))
        pairs.sort(key=lambda k: k[0])
    if pairs[0][0] > 0:
        pairs.insert(0, (0.0, pairs[0][1]))
    res.ciss_curve = pairs
    res.ciss_status = "pass"


def export_manifest(manifest_path: Path, out_dir: Path, *, max_rel_error: float = 0.02,
                    max_knots: int = 48) -> list[DslibCossResult]:
    rows = json.loads(manifest_path.read_text())
    if not isinstance(rows, list):
        raise ValueError(f"{manifest_path} is not a capacitance digitization manifest")
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        res = export_row(row, manifest_path.parent, max_rel_error=max_rel_error,
                         max_knots=max_knots)
        results.append(res)
        safe = "".join(ch if ch.isalnum() else "_" for ch in f"{res.part}_d{res.diagram}")
        (out_dir / f"{safe}.dslib_coss.json").write_text(
            json.dumps(asdict(res), indent=2) + "\n")
    (out_dir / "dslib_coss_manifest.json").write_text(
        json.dumps([asdict(r) for r in results], indent=2) + "\n")
    return results


def _pin_anchor_knots(curve: list, anchors: dict, coss_pts, crss_pts,
                      coss_scale: float, crss_scale: float) -> list:
    """Insert a knot at each spec-table anchor Vds (digitized values, not the anchor's).

    dslib consumers interpolate the triples LINEARLY; without a knot AT the anchor V the
    linear chord between straddling log-space knots mis-reads the curve exactly where the
    downstream cross-checks probe it.  Skipped when an existing knot already sits at the
    anchor V (within 0.3% of the span) or the anchor V falls outside the digitized range.
    """
    v_coss, c_coss = coss_pts
    v_crss, c_crss = crss_pts
    span = curve[-1][0] - curve[0][0]
    tol = max(0.003 * span, 1e-3)
    out = list(curve)
    anchor_vs = sorted({float(a["vds_v"]) for name, a in anchors.items()
                        if name in ("Coss", "Crss") and a and a.get("vds_v") is not None})
    for va in anchor_vs:
        if va < v_coss[0] or va > v_coss[-1]:
            continue
        if any(abs(k[0] - va) <= tol for k in out):
            continue
        coss = _sig4(_interp_at(v_coss, c_coss, va, coss_scale))
        crss = _sig4(_interp_at(v_crss, c_crss, va, crss_scale))
        out.append((va, coss, crss))
    out.sort(key=lambda k: k[0])
    return out


def _abs_or_none(rel: object, base_dir: Path) -> str | None:
    if not rel:
        return None
    p = Path(str(rel))
    return str(p if p.is_absolute() else (base_dir / p).resolve())


def _sig4(x: float) -> float:
    if not math.isfinite(x) or x == 0:
        return 0.0
    return float(f"{x:.4g}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export digitized Coss/Crss as validation-gated dslib knot triples.")
    ap.add_argument("manifest", type=Path,
                    help="capacitance_digitization.json (or a directory containing it)")
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--max-rel-error", type=float, default=0.02,
                    help="adaptive Coss knot target relative error")
    ap.add_argument("--max-knots", type=int, default=48, help="maximum Coss knots")
    args = ap.parse_args()
    manifest = args.manifest
    if manifest.is_dir():
        manifest = manifest / "capacitance_digitization.json"
    results = export_manifest(manifest, args.out, max_rel_error=args.max_rel_error,
                              max_knots=args.max_knots)
    ok = 0
    for r in results:
        if r.status == "pass":
            ok += 1
            ac = ", ".join(f"{k} {v['rel_error']:+.1%}" for k, v in r.anchor_check.items())
            if r.ciss_status == "pass":
                ciss = f"Ciss {len(r.ciss_curve)} knots"
            else:
                ciss = f"Ciss {r.ciss_status}: {'; '.join(r.ciss_reasons)}"
            print(f"{r.part} d{r.diagram}: PASS {r.knots} knots "
                  f"(from {r.source_points} samples; anchors: {ac}) [{ciss}]")
        else:
            print(f"{r.part} d{r.diagram}: REJECTED {'; '.join(r.reasons)}")
    if not results:
        raise SystemExit("no capacitance charts in manifest")
    if ok == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
