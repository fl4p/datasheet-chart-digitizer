"""Curve-anchored saturation-tempco batch over the human-verified transfer packet.

This replaces the charge-partition Vth derivation of
:mod:`transfer_anchor_batch` (which needs a datasheet-explicit QGS(th) split
that only Nexperia specs) with the curvefet design-record method: ``Vth_eff``
at 25 C is fitted from the HUMAN-VERIFIED 25 C transfer curve under the exact
plateau constraint ``K = Id_pl / (Vpl - Vth_eff)**p`` — the law reproduces the
(Vpl, Id_pl) gate-charge pivot BY CONSTRUCTION for every candidate Vth, so the
single free scalar is identified by the curve while the anchor stays exact.

Contracts kept visible:

* (Vpl, Id_pl) comes from ``gate_anchors.json`` — ``vpl_id_a`` is the CHART
  current, which may differ from the table ``Id_gc`` (DIT095N08: 50 vs 30 A).
* ``collapsed=1`` points are merged-stroke centerlines carrying NO
  per-temperature information: excluded from every fit and check.
* Non-Si gate drives (GaN logic-level, SiC negative rails) are refused by the
  drive gate rather than force-fitted through Si plateau semantics.
* No result is attached; a clean part remains ``fit-review-required``.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .transfer_anchor_batch import _ztc_guards
from .transfer_characteristics import (
    MAX_COLD_CONFLICT_ABS_V,
    MAX_COLD_CONFLICT_RMS_V,
    TransferCurve,
    _inverse_vgs,
    fit_saturation_tempco,
)

P_FIXED = 2.0
# Drive strings that transfer to the Si 0/10V plateau semantics unchanged.
SI_DRIVE_MIN_V = 8.0
LOGIC_DRIVE_MIN_V = 4.0
# The absolute cold-conflict bounds (0.35/0.75 V) are calibrated for 10V-drive
# parts. A logic-level device compresses its whole active range into <1 V, so
# the anchor-vs-curve conflict must ALSO be judged relative to the fitted
# overdrive span or it silently stops guarding (RJK0853: 121 mV RMS "passed"
# while being ~16% of the span — the pivot sat 0.25 V right of the curve).
MAX_COLD_CONFLICT_SPAN_FRACTION = 0.10


def load_flagged_curves(path: Path) -> tuple[list[TransferCurve], dict[str, int]]:
    """Load 25 C + hottest curve, excluding collapsed (no-T-info) points."""
    grouped: dict[float, list[tuple[float, float]]] = {}
    excluded: dict[float, int] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            temp = float(row["temperature_c"])
            if row.get("collapsed", "0") == "1":
                excluded[temp] = excluded.get(temp, 0) + 1
                continue
            grouped.setdefault(temp, []).append((float(row["Vgs_V"]), float(row["Id_A"])))
    if 25.0 not in grouped:
        raise RuntimeError(f"{path.name}: no 25 C curve")
    hot = max((t for t in grouped if t > 25.0), default=None)
    if hot is None:
        raise RuntimeError(f"{path.name}: no curve hotter than 25 C")
    stats = {
        "excluded_collapsed_25c": excluded.get(25.0, 0),
        "excluded_collapsed_hot": excluded.get(hot, 0),
    }
    return [TransferCurve(25.0, grouped[25.0]), TransferCurve(hot, grouped[hot])], stats


def fit_vth_from_cold_curve(
    cold: TransferCurve, vpl_v: float, id_pl_a: float, p: float = P_FIXED
) -> dict[str, Any]:
    """Identify Vth_eff from the 25 C curve with the (Vpl, Id_pl) pivot exact.

    For each candidate Vth the pivot fixes K, so the model gate voltage at a
    matched current is ``Vth + (I/K)**(1/p)``.  The residual is evaluated in
    Vgs-space over the same style of matched-current window the temperature
    fit uses; the minimizing Vth is returned together with the residual so the
    caller can refuse an anchor-vs-curve conflict instead of force-fitting.
    """
    max_i = max(i for _v, i in cold.points)
    i_lo = max(0.05 * id_pl_a, 0.02 * max_i)
    i_hi = min(2.0 * id_pl_a, 0.85 * max_i)
    if i_hi <= 1.5 * i_lo:
        raise RuntimeError(
            f"insufficient 25 C current span for Vth identification: {i_lo:g}..{i_hi:g} A"
        )
    currents = np.linspace(i_lo, i_hi, 160)
    v_curve = _inverse_vgs(cold.points, currents)

    def rms(vth: float) -> float:
        k = id_pl_a / (vpl_v - vth) ** p
        model = vth + np.power(currents / k, 1.0 / p)
        return float(np.sqrt(np.mean((model - v_curve) ** 2)))

    lo, hi = 0.05 * vpl_v, 0.95 * vpl_v
    # Golden-section over the single well-behaved scalar.
    invphi = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c = b - invphi * (b - a)
    d = a + invphi * (b - a)
    for _ in range(80):
        if rms(c) < rms(d):
            b = d
        else:
            a = c
        c = b - invphi * (b - a)
        d = a + invphi * (b - a)
    vth = 0.5 * (a + b)
    return {
        "vth_eff_v": vth,
        "k_a_per_vp": id_pl_a / (vpl_v - vth) ** p,
        "cold_fit_rms_v": rms(vth),
        "fit_window_a": [i_lo, i_hi],
    }


def drive_gate(anchor: dict[str, Any]) -> str | None:
    drive = anchor.get("vgs_drive_v")
    if isinstance(drive, str):
        return f"non-Si gate drive '{drive}': plateau/charge-partition semantics do not transfer"
    if drive is None:
        return "gate drive unresolved"
    if drive < LOGIC_DRIVE_MIN_V:
        return f"logic drive {drive:g} V below {LOGIC_DRIVE_MIN_V:g} V support floor"
    note = (anchor.get("note") or "").lower()
    if "gan" in note and drive < SI_DRIVE_MIN_V:
        return f"GaN {drive:g} V drive: Si plateau semantics gated by design record"
    return None


def evaluate_part(
    anchor: dict[str, Any], manifest_entry: dict[str, Any], batch_dir: Path
) -> dict[str, Any]:
    guards: list[str] = []
    fit: dict[str, Any] | None = None
    vth_fit: dict[str, Any] | None = None
    fit_error: str | None = None
    gate = drive_gate(anchor)
    if gate:
        guards.append(f"drive-gate: {gate}")
    else:
        try:
            curves, collapse_stats = load_flagged_curves(
                batch_dir / manifest_entry["points_csv"]
            )
            id_pl = float(anchor.get("vpl_id_a") or anchor["id_gc_a"])
            vth_fit = fit_vth_from_cold_curve(curves[0], float(anchor["vpl_v"]), id_pl)
            model_anchor = {
                "tref_c": 25.0,
                "vth_eff_v": vth_fit["vth_eff_v"],
                "k_a_per_vp": vth_fit["k_a_per_vp"],
                "p": P_FIXED,
                "id_gc_a": id_pl,
                "vpl_v": float(anchor["vpl_v"]),
            }
            fit = fit_saturation_tempco(curves, model_anchor)
            fit["collapse_exclusions"] = collapse_stats
            fit["vth_identification"] = vth_fit
            overdrive_span = max(
                v for v, _i in curves[0].points
            ) - vth_fit["vth_eff_v"]
            span_fraction = (
                fit["cold_anchor_check_rms_v"] / overdrive_span
                if overdrive_span > 0
                else math.inf
            )
            fit["cold_conflict_span_fraction"] = span_fraction
            if fit["cold_anchor_conflict"]:
                guards.append(
                    "anchor-curve-conflict: p=2 law cannot meet both the exact "
                    "(Vpl,Id) pivot and the verified 25 C curve within "
                    f"RMS {MAX_COLD_CONFLICT_RMS_V:g} V / max {MAX_COLD_CONFLICT_ABS_V:g} V"
                )
            elif span_fraction > MAX_COLD_CONFLICT_SPAN_FRACTION:
                guards.append(
                    "anchor-curve-conflict(span-relative): cold RMS is "
                    f"{span_fraction:.0%} of the fitted overdrive span "
                    f"(> {MAX_COLD_CONFLICT_SPAN_FRACTION:.0%}) — the gate-charge "
                    "pivot and the transfer curve disagree beyond digitization "
                    "uncertainty at this part's voltage scale"
                )
            guards.extend(_ztc_guards(fit, curves))
        except Exception as exc:  # noqa: BLE001 - guard bucket, not silent pass
            guards.append("temperature-fit-failed")
            fit_error = str(exc)
    status = "fit-review-required" if not guards else "guard-refusal"
    return {
        "manufacturer": anchor["manufacturer"],
        "part": anchor["part"],
        "status": status,
        "eligible_for_attachment": False,
        "guard_reasons": guards,
        "anchor": {
            key: anchor.get(key)
            for key in (
                "id_gc_a", "vpl_v", "vpl_id_a", "vgs_drive_v", "vds_cond_v",
                "vpl_source", "vpl_note", "status",
            )
        },
        "transfer_points_csv": manifest_entry["points_csv"],
        "transfer_overlay": manifest_entry["overlay"],
        "fit": fit,
        "fit_error": fit_error,
    }


def run_batch(packet_dir: Path, out_dir: Path) -> dict[str, Any]:
    anchors = json.loads((packet_dir / "gate_anchors.json").read_text())["parts"]
    manifest = json.loads((packet_dir / "manifest.json").read_text())
    by_part = {entry["part"]: entry for entry in manifest}
    candidates = [a for a in anchors if a.get("vpl_v") is not None]
    missing = [a["part"] for a in candidates if a["part"] not in by_part]
    if missing:
        raise RuntimeError(f"transfer manifest is missing: {', '.join(missing)}")
    results = [evaluate_part(a, by_part[a["part"]], packet_dir) for a in candidates]
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "contract": {
            "verified_or_attached": 0,
            "p_fixed": P_FIXED,
            "vth_source": "25C-curve fit under exact (Vpl, vpl_id_a) pivot; no charge partition",
            "collapsed_points": "excluded (no per-temperature information)",
        },
        "packet": str(packet_dir),
        "results": results,
    }
    (out_dir / "saturation-tempco17.json").write_text(
        json.dumps(payload, indent=1, allow_nan=False) + "\n"
    )
    return payload
