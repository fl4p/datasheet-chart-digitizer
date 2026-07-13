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


@dataclass
class DslibCossResult:
    """One chart's export verdict. `status` is "pass" only when EVERY gate passed and a
    curve was produced; any other outcome is "rejected" with machine-readable reasons."""

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
    return res


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
            print(f"{r.part} d{r.diagram}: PASS {r.knots} knots "
                  f"(from {r.source_points} samples; anchors: {ac})")
        else:
            print(f"{r.part} d{r.diagram}: REJECTED {'; '.join(r.reasons)}")
    if not results:
        raise SystemExit("no capacitance charts in manifest")
    if ok == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
