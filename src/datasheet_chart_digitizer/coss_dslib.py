"""Export digitized Coss/Crss to dslib-style (Vds_V, Coss_pF, Crss_pF) knot triples.

Consumes a `capacitance_digitization.json` manifest (from `dsdig digitize-capacitance`)
and emits, per chart, a compact JSON suitable for machine consumption by downstream
parts DBs (pwr-mosfet-lib `dslib/coss_curves.py` format: knot triples, low V -> high V).

This is the machine-validated path for AUTO-digitized curves. Coss, Crss, and Ciss each
have an independent verdict: a curve that cannot be validated is REJECTED with reasons,
without withholding another trace that passed its own gates. Gates:

  * axis_calibration_trusted is True (position-based axis fit agreed with gridlines)
  * semantic shape checks are routed to the trace(s) they cover
  * qoss_validation_status == "pass" gates Coss (Qoss integral consistency)
  * each trace has its own table anchor and agrees at the anchor Vds within tolerance
    (default 8% Coss / 15% Crss — curated curves historically land within ~2%).

The exported curve keeps the digitizer's values (no snapping); anchor agreement is
reported so a consumer can decide to snap. Each trace gets its own adaptive log-space
model (max_rel_error target). The legacy combined curve interpolates Crss onto Coss knots.

Anchor voltages are additionally pinned as explicit knots: the adaptive knots are
error-optimal for LOG-space interpolation, but dslib consumers interpolate LINEARLY,
and when the knots merely straddle the spec-table Vds the linear chord reads the convex
knee high (measured +2.4% on IPP040N08NF2S at the 40 V anchor).  Since the anchor V is
exactly the point every downstream cross-check probes, each anchor gets a knot carrying
the digitized (not snapped) values there.

Coss, Crss, and Ciss are exported as independent (Vds_V, capacitance_pF) pairs. For
backward compatibility, `curve` and aggregate `status` still carry a combined
(Vds_V, Coss_pF, Crss_pF) curve only when both Coss and Crss pass.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .output_charge import ANCHOR_TOL as OUTPUT_CHARGE_ANCHOR_TOL
from .output_charge import (
    evaluate_trace, evidence_sha256, integrate_knots, producer_sha256, read_points_csv,
)
from .coss_export import (
    build_adaptive_coss_model,
    evaluate_coss_knots,
    load_coss_points_csv,
)

# Tolerated |digitized/anchor - 1| at the anchor Vds. Crss is small (tens of pF) and
# raster-noisier, hence the looser gate.
ANCHOR_TOL_COSS = 0.08
ANCHOR_TOL_CRSS = 0.15
TINY_CRSS_ANCHOR_PF = 15.0
# See the resolution gate below: quanta needed before an anchor
# comparison on a LINEAR capacitance axis carries information at all.
MIN_ANCHOR_RESOLUTION_PX = 4.0
# Ciss is the largest, flattest trace (vector traces land within ~2%), so it gets the
# strict Coss-grade gate.
ANCHOR_TOL_CISS = 0.08


@dataclass
class DslibCossResult:
    """Independent trace verdicts plus the legacy combined Coss/Crss verdict.

    `status` remains "pass" only when both Coss and Crss pass and `curve` was produced.
    Consumers that support partial imports use the three per-trace statuses and pair
    curves. `absent` is only used when a chart carries no evidence for that trace."""

    part: str
    diagram: str
    status: str                      # "pass" | "rejected"
    reasons: list = field(default_factory=list)
    curve: list = field(default_factory=list)   # [(Vds_V, Coss_pF, Crss_pF), ...]
    coss_status: str = "rejected"    # "pass" | "rejected"
    coss_reasons: list = field(default_factory=list)
    coss_curve: list = field(default_factory=list)  # [(Vds_V, Coss_pF), ...]
    crss_status: str = "rejected"    # "pass" | "rejected"
    crss_reasons: list = field(default_factory=list)
    crss_curve: list = field(default_factory=list)  # [(Vds_V, Crss_pF), ...]
    anchor_check: dict = field(default_factory=dict)
    qoss_pc: float | None = None
    knots: int = 0
    source_points: int = 0
    overlay: str | None = None
    points_csv: str | None = None
    pdf: str | None = None
    ciss_status: str = "rejected"    # "pass" | "absent" | "rejected"
    ciss_reasons: list = field(default_factory=list)
    ciss_curve: list = field(default_factory=list)  # [(Vds_V, Ciss_pF), ...]
    # coss_curve is the small-signal chart (typically f = 1 MHz).  Use it for ring
    # tanks; use coss_eff_curve (large-signal dQoss/dV from a Qoss(VDS) diagram) for
    # Qoss/Eoss losses when it passes.  qoss_check says how Coss's integral was gated.
    coss_basis: str = "small_signal_chart"
    qoss_check: dict = field(default_factory=dict)
    coss_eff_status: str = "absent"  # "pass" | "absent" | "unverified" | "rejected"
    coss_eff_reasons: list = field(default_factory=list)
    coss_eff_curve: list = field(default_factory=list)  # [(Vds_V, Coss_eff_pF), ...]
    coss_eff_source: dict = field(default_factory=dict)


def _interp_at(vds: np.ndarray, cap: np.ndarray, v: float, v_scale: float) -> float:
    return float(evaluate_coss_knots(np.asarray([v], float), vds, cap, v_scale)[0])


def _trace_gate_reasons(row: dict) -> dict[str, list[str]]:
    """Route semantic trace failures to the trace(s) they actually invalidate."""
    out = {name: [] for name in ("Coss", "Crss", "Ciss")}
    status = row.get("trace_validation_status")
    if status == "pass":
        return out
    raw_reasons = list(row.get("trace_validation_reasons") or [])
    if not raw_reasons:
        raw_reasons = ["unspecified"]
    for raw in raw_reasons:
        reason = str(raw)
        lower = reason.lower()
        if (lower.startswith("ciss_coss_")
                or lower in {"ciss_not_flatter_than_coss", "ciss_coss_rank_swap_count"}):
            names = ("Ciss", "Coss")
        elif lower.startswith(("crss_", "missing_crss")):
            names = ("Crss",)
        elif lower.startswith(("ciss_", "missing_ciss")):
            names = ("Ciss",)
        elif lower.startswith(("coss_", "missing_coss", "qoss_")):
            names = ("Coss",)
        else:
            names = ("Coss", "Crss", "Ciss")
        rendered = f"trace_validation:{status}:{reason}"
        for name in names:
            out[name].append(rendered)
    return out


def _finite_positive(x: object) -> float | None:
    try:
        f = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _matching_output_charge(row: dict, panels: list[dict]) -> dict | None:
    """The Qoss(VDS) diagram digitized from the same PDF CONTENT as this C(V) row.

    Both sides record the sha256 of the PDF they were digitized from (the C(V) row
    since this change; older manifests carry none and match nothing).  Same part and
    same digest; more than one matching panel is ambiguous and matches nothing."""
    if not panels:
        return None
    sha = row.get("source_pdf_sha256")
    if not isinstance(sha, str) or len(sha) != 64:
        return None
    found = [p for p in panels if isinstance(p, dict) and p.get("part") == row.get("part")
             and (p.get("provenance") or {}).get("pdf_sha256") == sha]
    return found[0] if len(found) == 1 else None


def _served_curve_problems(curve: object, ref: dict) -> list[str]:
    """Validate the exact Coss_eff knots that will be served, against THIS row's
    table Qoss: at least 3 finite knots, strictly increasing from 0 V, positive
    capacitance, covering the table Vds, and integrating (linearly, from 0 V, as a
    consumer does) to the table Qoss within the diagram anchor tolerance."""
    try:
        kv = np.asarray([float(k[0]) for k in curve], float)  # type: ignore[union-attr]
        kc = np.asarray([float(k[1]) for k in curve], float)  # type: ignore[union-attr]
    except (TypeError, ValueError, IndexError):
        return ["coss_eff_curve_invalid:malformed"]
    if (len(kv) < 3 or not np.all(np.isfinite(kv)) or not np.all(np.isfinite(kc))
            or kv[0] != 0.0 or np.any(np.diff(kv) <= 0) or np.any(kc <= 0)):
        return ["coss_eff_curve_invalid:need >=3 finite knots, increasing from 0 V, C > 0"]
    ref_q, ref_v = _finite_positive(ref.get("qoss_pc")), _finite_positive(ref.get("vint_v"))
    if ref_q is None or ref_v is None:
        return ["coss_eff_table_reference_unavailable"]
    if ref_v > kv[-1]:
        return [f"coss_eff_curve_does_not_reach_table_vds:{ref_v:g} V"]
    q = float(integrate_knots(list(zip(kv, kc)), np.array([ref_v]))[0])
    rel = q / ref_q - 1.0
    if not abs(rel) <= OUTPUT_CHARGE_ANCHOR_TOL:
        return [f"coss_eff_integral_vs_table:{rel:+.1%} at {ref_v:g} V "
                f"(tol {OUTPUT_CHARGE_ANCHOR_TOL:.0%})"]
    return []


def _recompute_diagram(diagram: dict, row: dict) -> tuple[list[str], dict | None]:
    """Re-derive the diagram verdict instead of trusting anything it stored.

    Checked against the record: the producing code (producer_sha256), the vector
    trace and BOTH verified axis grids.  The evidence digest binds the points CSV bytes
    to the recorded quanta, calibration (incl. grid checks) and method, so none can be
    edited or mixed in alone.  Then evaluate_trace runs again on the points with the
    recorded quanta and THIS row's table Qoss.  Its
    verdict, uncertainties and integral gates are the ones used; the stored curve must
    equal the recomputed one.  Returns (problems, recomputed result).

    Integrity, not authentication: a consistently rewritten points CSV plus its
    digest is not detected; stale, mixed, edited or partially rewritten artifacts are."""
    problems = []
    prov = diagram.get("provenance") or {}
    if prov.get("producer_sha256") != producer_sha256():
        problems.append("output_charge_stale:produced by different digitizer code")
    if diagram.get("method") != "vector":
        problems.append(f"output_charge_method:{diagram.get('method')}")
    grid = (diagram.get("calibration") or {}).get("grid_check")
    if not (isinstance(grid, dict) and all(
            isinstance(grid.get(ax), dict) and grid[ax].get("status") == "verified"
            for ax in ("x", "y"))):
        problems.append("output_charge_grid_check_not_verified:need verified x and y")
    quantum = diagram.get("quantum") or {}
    q_q, q_v = _finite_positive(quantum.get("q_pc")), _finite_positive(quantum.get("v_v"))
    if q_q is None or q_v is None:
        problems.append("output_charge_quantum_missing")
    csv_path = _panel_points_path(diagram)
    digest = diagram.get("evidence_sha256")
    if not (isinstance(digest, str) and len(digest) == 64 and csv_path is not None
            and evidence_sha256(csv_path, diagram) == digest):
        problems.append("output_charge_evidence_missing_or_changed:points, quanta, "
                        "calibration or method differ from what was digitized")
    if problems:
        return problems, None
    try:
        v, q_pc = read_points_csv(csv_path)
    except (OSError, ValueError, KeyError) as exc:
        return [f"output_charge_points_unreadable:{exc}"], None
    redo = evaluate_trace(v, q_pc, row.get("output_charge_reference") or {}, q_q, q_v)
    if redo["status"] != "pass":
        return [f"output_charge_recheck:{r}" for r in redo["reasons"]], redo
    stored, fresh = diagram.get("coss_eff_curve"), redo["coss_eff_curve"]
    try:
        same = (isinstance(stored, list) and len(stored) == len(fresh) and all(
            float(a[0]) == float(b[0]) and float(a[1]) == float(b[1])
            for a, b in zip(stored, fresh)))
    except (TypeError, ValueError, IndexError):
        same = False
    if not same:
        return ["output_charge_curve_differs_from_recomputation"], redo
    problems.extend(_served_curve_problems(fresh, row.get("output_charge_reference") or {}))
    return problems, redo


def _panel_points_path(diagram: dict) -> Path | None:
    """The panel's points CSV: relative paths resolve against the directory of the
    output-charge manifest it was loaded from (so a moved bundle stays valid);
    absolute paths from older manifests are used as recorded."""
    raw = diagram.get("csv")
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        base = diagram.get("_manifest_dir")
        if not isinstance(base, str) or not base:
            return None
        path = Path(base) / path
    return path


def _apply_coss_eff(res: DslibCossResult, diagram: dict | None,
                    row: dict) -> tuple[list[str], dict | None]:
    """Fill the Coss_eff verdict.  Returns the problems that bar the diagram as
    evidence, and the recomputed diagram result (None when not recomputed)."""
    if diagram is None:
        return ["no_output_charge_diagram"], None
    res.coss_eff_source = {
        "diagram": diagram.get("diagram"), "page": diagram.get("page"),
        "overlay": diagram.get("overlay"), "provenance": diagram.get("provenance"),
    }
    status = str(diagram.get("status"))
    if status != "pass":
        res.coss_eff_status = "unverified" if status == "unverified" else "rejected"
        res.coss_eff_reasons = list(diagram.get("reasons") or [f"output_charge_status:{status}"])
        return res.coss_eff_reasons, None
    problems, redo = _recompute_diagram(diagram, row)
    if redo is not None:
        res.coss_eff_source.update({
            "anchor": redo.get("anchor"),
            "integral_max_error": redo.get("coss_eff_integral_max_error"),
            "rel_uncertainty": redo.get("coss_eff_rel_uncertainty"),
            "half_window_v": redo.get("coss_eff_half_window_v"),
        })
    if problems:
        res.coss_eff_status = "rejected"
        res.coss_eff_reasons = problems
        return problems, None
    res.coss_eff_status = "pass"
    res.coss_eff_curve = [(float(v), float(c)) for v, c in redo["coss_eff_curve"]]
    return [], redo


def _source_contradiction(row: dict, redo: dict | None) -> dict | None:
    """qoss_check payload when a passing Qoss diagram, RECOMPUTED at export against the
    SAME table Qoss this row was checked against, explains a graph/table inconsistency."""
    if redo is None or redo.get("status") != "pass":
        return None
    anchor = redo.get("anchor") or {}
    ref = row.get("output_charge_reference") or {}
    ref_q, ref_v = _finite_positive(ref.get("qoss_pc")), _finite_positive(ref.get("vint_v"))
    if (ref_q is None or ref_v is None
            or _finite_positive(anchor.get("table_pc")) != ref_q
            or _finite_positive(anchor.get("vds_v")) != ref_v):
        return None
    diagram_pc = _finite_positive(anchor.get("diagram_pc"))
    if diagram_pc is None or not abs(diagram_pc / ref_q - 1.0) <= OUTPUT_CHARGE_ANCHOR_TOL:
        return None
    chart_pc = _finite_positive(
        (row.get("qoss_metrics") or row.get("qoss_diagnostic_metrics") or {}).get("Qoss_pc"))
    if chart_pc is None:
        return None
    return {
        "status": "source_contradiction",
        "basis": "qoss_diagram_matches_table",
        "vds_v": ref_v,
        "table_qoss_pc": ref_q,
        "diagram_qoss_pc": anchor.get("diagram_pc"),
        "coss_chart_integral_pc": round(chart_pc, 2),
        "coss_chart_integral_over_table": round(chart_pc / ref_q, 4),
        "note": "integrate coss_eff_curve, not coss_curve, for Qoss/Eoss",
    }


def export_row(row: dict, base_dir: Path, *, max_rel_error: float = 0.02,
               max_knots: int = 48, output_charge: list[dict] | None = None) -> DslibCossResult:
    """Validate one manifest row and build independently gated trace curves."""
    part = str(row.get("part") or "?")
    diagram = str(row.get("diagram") or "?")
    res = DslibCossResult(part=part, diagram=diagram, status="rejected",
                          overlay=_abs_or_none(row.get("overlay"), base_dir),
                          points_csv=_abs_or_none(row.get("points"), base_dir),
                          pdf=row.get("pdf"))

    shared_reasons = []
    if row.get("axis_calibration_trusted") is not True:
        shared_reasons.append("axis_calibration_not_trusted")
    res.coss_reasons.extend(shared_reasons)
    res.crss_reasons.extend(shared_reasons)
    res.ciss_reasons.extend(shared_reasons)
    trace_reasons = _trace_gate_reasons(row)
    res.coss_reasons.extend(trace_reasons["Coss"])
    res.crss_reasons.extend(trace_reasons["Crss"])
    res.ciss_reasons.extend(trace_reasons["Ciss"])

    diagram = _matching_output_charge(row, output_charge or [])
    _, redo = _apply_coss_eff(res, diagram, row)
    if diagram is None and any(isinstance(p, dict) and p.get("part") == part
                               for p in output_charge or []):
        # a diagram of this part exists but is not provably from this row's PDF
        res.coss_eff_reasons = ["output_charge_diagram_not_bound_to_this_pdf"]
    qoss_status = row.get("qoss_validation_status")
    explained = (_source_contradiction(row, redo)
                 if qoss_status == "graph_table_inconsistent" else None)
    if qoss_status == "pass":
        res.qoss_check = {"status": "pass", "basis": "chart_integral_vs_table"}
    elif explained is not None:
        # The sheet's own Qoss diagram matches its Qoss table, and the small-signal Coss
        # chart integrates to something else: a contradiction in the source, not a
        # digitization fault.  Coss stays the small-signal curve (still anchor- and
        # shape-gated below); the charge it integrates to must not be used as Qoss.
        res.qoss_check = explained
    else:
        res.coss_reasons.append(
            f"qoss_validation:{qoss_status}"
            f":{row.get('qoss_validation_error')}")
        res.qoss_check = {"status": str(qoss_status)}

    anchors = row.get("anchors") or {}
    for name, reasons in (("Coss", res.coss_reasons), ("Crss", res.crss_reasons)):
        a = anchors.get(name)
        if not a or not a.get("value_pf") or a.get("vds_v") is None:
            reasons.append(f"missing_{name.lower()}_anchor")

    # On a linear-C chart, a tiny Crss trace can have a useful SHAPE while its absolute
    # vertical position is below the chart's resolution. Anchor the whole trace with one
    # additive offset at the table value. Larger unresolved traces, Coss, and Ciss remain
    # rejected: this correction is deliberately narrow and explicit in anchor_check.
    cal = row.get("axis_calibration") or {}
    below_resolution = set()
    if cal.get("y_log") is not True:
        pf_per_px = abs(float(cal.get("y_scale") or 0.0))
        if pf_per_px > 0.0:
            for name, reasons in (
                    ("Coss", res.coss_reasons),
                    ("Crss", res.crss_reasons),
                    ("Ciss", res.ciss_reasons)):
                a = anchors.get(name)
                spec = (a or {}).get("value_pf")
                if not spec:
                    continue
                px = float(spec) / pf_per_px
                if px < MIN_ANCHOR_RESOLUTION_PX:
                    below_resolution.add(name)
                    if name != "Crss" or float(spec) > TINY_CRSS_ANCHOR_PF:
                        reasons.append(
                            f"{name.lower()}_below_axis_resolution:"
                            f"{spec:g} pF = {px:.2f} px on a linear axis "
                            f"({pf_per_px:.2f} pF/px, need "
                            f"{MIN_ANCHOR_RESOLUTION_PX:g})")

    if res.points_csv is None or not Path(res.points_csv).exists():
        reason = "missing_points_csv"
        res.coss_reasons.append(reason)
        res.crss_reasons.append(reason)
        res.ciss_reasons.append(reason)
        _finish_aggregate(res)
        return res

    traces = {}
    for name, reasons in (
            ("Coss", res.coss_reasons),
            ("Crss", res.crss_reasons),
            ("Ciss", res.ciss_reasons)):
        try:
            traces[name] = load_coss_points_csv(Path(res.points_csv), name)
        except ValueError as exc:
            if name == "Ciss" and not anchors.get("Ciss"):
                res.ciss_status = "absent"
                res.ciss_reasons = ["no_ciss_trace", "no_ciss_anchor"]
            else:
                reasons.append(f"{name.lower()}_points_load:{exc}")

    # Anchor agreement — digitized value at the spec-table Vds vs the table value.
    # Interpolate on the RAW cleaned samples (not the reduced knots) so the check
    # measures the digitization, not the knot reduction.
    scales = {
        name: max(float(vv[-1] - vv[0]) * 0.01, 1e-6)
        for name, (vv, _cc) in traces.items()
    }
    for name, tol, reasons in (
            ("Coss", ANCHOR_TOL_COSS, res.coss_reasons),
            ("Crss", ANCHOR_TOL_CRSS, res.crss_reasons)):
        if name not in traces:
            continue
        vv, cc = traces[name]
        a = anchors.get(name)
        if not a or not a.get("value_pf"):
            continue
        got = _interp_at(vv, cc, float(a["vds_v"]), scales[name])
        spec = float(a["value_pf"])
        rel = got / spec - 1.0
        check: dict[str, object] = {"vds_v": float(a["vds_v"]),
                                    "spec_pf": spec,
                                    "digitized_pf": round(got, 4),
                                    "rel_error": round(rel, 4)}
        offset_pf = 0.0
        if name == "Crss" and name in below_resolution and 0.0 < spec <= TINY_CRSS_ANCHOR_PF:
            offset_pf = spec - got
        elif not math.isfinite(rel) or abs(rel) > tol:
            if name == "Crss" and 0.0 < spec <= TINY_CRSS_ANCHOR_PF and got <= spec:
                offset_pf = spec - got
            else:
                reasons.append(
                    f"{name.lower()}_anchor_mismatch:{rel:+.1%} (tol {tol:.0%})")
        if offset_pf:
            traces[name] = (vv, np.maximum(cc + offset_pf, 1e-12))
            check.update({
                "offset_pf": round(offset_pf, 4),
                "corrected_digitized_pf": round(spec, 4),
                "rel_error": 0.0,
                "correction": "tiny_crss_anchor_offset",
            })
        elif name == "Crss" and name in below_resolution:
            # The table anchor happens to equal the quantised trace exactly. Record that
            # the unobservable absolute position was nevertheless anchored deliberately.
            check.update({
                "offset_pf": 0.0,
                "corrected_digitized_pf": round(spec, 4),
                "rel_error": 0.0,
                "correction": "tiny_crss_anchor_offset",
            })
        res.anchor_check[name] = check

    if not res.coss_reasons and "Coss" in traces:
        res.coss_curve, res.source_points = _build_pair_curve(
            traces["Coss"], anchors.get("Coss"),
            max_rel_error=max_rel_error, max_knots=max_knots)
        res.coss_status = "pass"
    if not res.crss_reasons and "Crss" in traces:
        res.crss_curve, _ = _build_pair_curve(
            traces["Crss"], anchors.get("Crss"),
            max_rel_error=max_rel_error, max_knots=max_knots)
        res.crss_status = "pass"

    qm = row.get("qoss_metrics") or {}
    res.qoss_pc = qm.get("Qoss_pc")
    _export_ciss(res, row, traces.get("Ciss"), traces.get("Crss"),
                 max_rel_error=max_rel_error, max_knots=max_knots)

    if res.coss_status == "pass" and res.crss_status == "pass":
        v_coss, c_coss = traces["Coss"]
        v_crss, c_crss = traces["Crss"]
        coss_scale, crss_scale = scales["Coss"], scales["Crss"]
        kv = np.asarray([p[0] for p in res.coss_curve], float)
        kc = np.asarray([p[1] for p in res.coss_curve], float)
        kx = evaluate_coss_knots(kv, v_crss, c_crss, crss_scale)
        curve = [(float(v), _sig4(c), _sig4(x)) for v, c, x in zip(kv, kc, kx)]
        res.curve = _pin_anchor_knots(
            curve, anchors, (v_coss, c_coss), (v_crss, c_crss),
            coss_scale, crss_scale)
        res.knots = len(res.curve)
        res.status = "pass"

    _finish_aggregate(res)
    return res


def _export_ciss(res: DslibCossResult, row: dict, ciss_pts, crss_pts, *,
                 max_rel_error: float, max_knots: int) -> None:
    """Finish the independent optional Ciss verdict."""
    anchors = row.get("anchors") or {}
    a = anchors.get("Ciss") or {}
    have_anchor = bool(a.get("value_pf")) and a.get("vds_v") is not None
    if res.ciss_status == "absent":
        return
    if ciss_pts is None:
        return
    if not have_anchor:
        res.ciss_reasons.append("missing_ciss_anchor")
        return

    v_ciss, c_ciss = ciss_pts
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

    # Ciss remains independently exportable if Crss failed. Apply the physical
    # Ciss>Crss cross-check when (and only when) Crss itself is accepted evidence.
    if res.crss_status == "pass" and crss_pts is not None:
        v_crss, c_crss = crss_pts
        crss_scale = max(float(v_crss[-1] - v_crss[0]) * 0.01, 1e-6)
        lo, hi = max(v_ciss[0], v_crss[0]), min(v_ciss[-1], v_crss[-1])
        if hi <= lo:
            res.ciss_reasons.append("ciss_crss_no_overlap")
            return
        vv = np.linspace(lo, hi, 200)
        crss_i = evaluate_coss_knots(vv, v_crss, c_crss, crss_scale)
        ciss_i = evaluate_coss_knots(vv, v_ciss, c_ciss, ciss_scale)
        if not np.all(ciss_i > crss_i):
            res.ciss_reasons.append("ciss_not_above_crss")
            return

    if res.ciss_reasons:
        return
    res.ciss_curve, _ = _build_pair_curve(
        ciss_pts, a, max_rel_error=max_rel_error, max_knots=max_knots)
    res.ciss_status = "pass"


def _build_pair_curve(points, anchor, *, max_rel_error: float,
                      max_knots: int) -> tuple[list, int]:
    """Adaptive pair curve with an explicit zero hold and table-anchor knot."""
    vv, cc = points
    model = build_adaptive_coss_model(
        vv, cc, max_rel_error=max_rel_error, max_knots=max_knots)
    pairs = [(float(v), _sig4(c)) for v, c in zip(model.vds, model.coss)]
    if anchor and anchor.get("vds_v") is not None:
        va = float(anchor["vds_v"])
        span = pairs[-1][0] - pairs[0][0]
        if (vv[0] <= va <= vv[-1]
                and not any(abs(p[0] - va) <= max(0.003 * span, 1e-3)
                            for p in pairs)):
            scale = max(float(vv[-1] - vv[0]) * 0.01, 1e-6)
            pairs.append((va, _sig4(_interp_at(vv, cc, va, scale))))
            pairs.sort(key=lambda k: k[0])
    if pairs[0][0] > 0:
        pairs.insert(0, (0.0, pairs[0][1]))
    return pairs, model.source_points


def _finish_aggregate(res: DslibCossResult) -> None:
    """Maintain legacy aggregate reasons without letting them drive trace verdicts."""
    res.reasons = list(dict.fromkeys(res.coss_reasons + res.crss_reasons))


def load_output_charge(path: Path | None) -> list[dict]:
    """Panels from `dsdig digitize-output-charge`; a missing file means no diagrams."""
    if path is None or not path.exists():
        return []
    data = json.loads(path.read_text())
    panels = data.get("panels") if isinstance(data, dict) else None
    if not isinstance(panels, list):
        raise ValueError(f"{path} is not an output-charge digitization manifest")
    base = str(path.resolve().parent)
    for p in panels:
        if isinstance(p, dict):
            p["_manifest_dir"] = base  # resolves the panel's relative artifact paths
    return panels


def export_manifest(manifest_path: Path, out_dir: Path, *, max_rel_error: float = 0.02,
                    max_knots: int = 48,
                    output_charge_path: Path | None = None) -> list[DslibCossResult]:
    rows = json.loads(manifest_path.read_text())
    if not isinstance(rows, list):
        raise ValueError(f"{manifest_path} is not a capacitance digitization manifest")
    if output_charge_path is None:
        output_charge_path = manifest_path.parent / "output_charge_digitization.json"
    output_charge = load_output_charge(output_charge_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        res = export_row(row, manifest_path.parent, max_rel_error=max_rel_error,
                         max_knots=max_knots, output_charge=output_charge)
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
    ap.add_argument("--output-charge", type=Path, default=None,
                    help="output_charge_digitization.json from `dsdig digitize-output-charge` "
                         "(default: next to the manifest, if present)")
    args = ap.parse_args()
    manifest = args.manifest
    if manifest.is_dir():
        manifest = manifest / "capacitance_digitization.json"
    results = export_manifest(manifest, args.out, max_rel_error=args.max_rel_error,
                              max_knots=args.max_knots, output_charge_path=args.output_charge)
    ok = 0
    for r in results:
        passed = [name for name in ("Coss", "Crss", "Ciss")
                  if getattr(r, f"{name.lower()}_status") == "pass"]
        if passed:
            ok += 1
            ac = ", ".join(f"{k} {v['rel_error']:+.1%}" for k, v in r.anchor_check.items())
            rejected = [
                f"{name}: {'; '.join(getattr(r, f'{name.lower()}_reasons'))}"
                for name in ("Coss", "Crss", "Ciss")
                if getattr(r, f"{name.lower()}_status") == "rejected"
            ]
            suffix = f"; rejected {' | '.join(rejected)}" if rejected else ""
            if r.qoss_check.get("status") == "source_contradiction":
                suffix += (f"; Qoss: Coss chart integrates to "
                           f"{r.qoss_check['coss_chart_integral_over_table']:.3f}x the table, "
                           "Qoss diagram matches it (source contradiction)")
            if r.coss_eff_status == "pass":
                passed.append("Coss_eff")
            print(f"{r.part} d{r.diagram}: PASS {'/'.join(passed)} "
                  f"(anchors: {ac}){suffix}")
        else:
            rejected = r.reasons + r.ciss_reasons
            print(f"{r.part} d{r.diagram}: REJECTED {'; '.join(rejected)}")
    if not results:
        raise SystemExit("no capacitance charts in manifest")
    if ok == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
