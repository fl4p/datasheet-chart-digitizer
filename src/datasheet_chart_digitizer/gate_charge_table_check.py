"""Cross-check the served Qg axis against the spec table's Total Gate Charge.

The table prints Qg(typ) at a test condition (VGS drive, VDD/VDS). The curve
drawn for that VDD reaches the table's VGS at a charge the served Qg axis
reads; on a correctly calibrated axis that reading is the table's typ up to
the datasheet's own rounding and drawing tolerance (``TOLERANCE_REL``,
measured over Fab's human-verified charts whose Qg axis is already verified on
its rules; see out/gc-qg-axis/README.md).

Tri-state, never a pass by default:

* ``agrees``: |read - typ| <= tolerance.
* ``contradicts``: beyond it.
* ``not_evaluable``: no owned Total Gate Charge row with typ, VGS and a
  drain/supply voltage; no curve bound to that voltage (or the chart's only
  curve, unlabelled or labelled with that voltage); the curve does not reach
  the table's VGS; fewer than two ticks on either axis.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

# |read - typ| <= TOLERANCE_REL * typ + half the printed typ's resolution.
# TOLERANCE_REL: on Fab's human-verified charts whose Qg axis the grid check
# verified (217 evaluable), |read - typ| / typ is <= 0.5 % at the median,
# 1.4 % at p75, 2.7 % at p85, 3.5 % at p90 (table and printed curve are not
# drawn from one dataset; outliers to 19 %). 3 % keeps ~88 % of true axes
# while rejecting the label-glyph mapping of FDMS86500L (+5.4 %), the
# known-bad case. Rounding: an integer typ of 9 nC carries +-0.5 nC (5.6 %)
# by itself.
TOLERANCE_REL = 0.03
# a curve whose top ends this short of the table VGS is read on its last slope
_REACH_V = 0.25


@dataclass(frozen=True)
class TableCheck:
    status: str  # agrees / contradicts / not_evaluable
    reason: str
    typ_nc: float | None = None
    read_nc: float | None = None
    delta_nc: float | None = None
    delta_rel: float | None = None
    tolerance_nc: float | None = None
    vgs_v: float | None = None
    vdrain_v: float | None = None
    curve: str | None = None
    row_text: str | None = None
    page: int | None = None

    def payload(self) -> dict[str, object]:
        out = asdict(self)
        for key in ("read_nc", "delta_nc", "delta_rel", "tolerance_nc"):
            if out[key] is not None:
                out[key] = round(float(out[key]), 4)
        return out


def _fit(ticks):
    if len(ticks) < 2:
        return None
    values = np.asarray([v for v, _ in ticks], dtype=float)
    pixels = np.asarray([p for _, p in ticks], dtype=float)
    if np.ptp(pixels) <= 0:
        return None
    return np.polyfit(pixels, values, 1)


def read_qg_at_vgs(curve_px, x_ticks, y_ticks, vgs: float) -> tuple[float | None, str]:
    """Qg (axis units) where *curve_px* first reaches *vgs*, through the given ticks."""
    fx, fy = _fit(list(x_ticks)), _fit(list(y_ticks))
    if fx is None or fy is None:
        return None, "fewer than 2 ticks on an axis"
    pts = sorted((float(x), float(y)) for x, y in curve_px)
    if len(pts) < 2:
        return None, "no curve points"
    xs = np.asarray([p[0] for p in pts])
    v = np.polyval(fy, np.asarray([p[1] for p in pts]))
    above = np.nonzero(v >= vgs)[0]
    if len(above):
        i = int(above[0])
        if i == 0:
            return None, "curve starts above the table VGS"
        x0, x1, v0, v1 = xs[i - 1], xs[i], v[i - 1], v[i]
        x = x0 if v1 == v0 else x0 + (vgs - v0) * (x1 - x0) / (v1 - v0)
        return float(np.polyval(fx, x)), "interpolated"
    if vgs - float(v.max()) > _REACH_V:
        return None, f"curve tops out at {float(v.max()):.2f} V, below the table VGS {vgs:g} V"
    tail = slice(max(0, len(xs) - 6), len(xs))
    slope, icpt = np.polyfit(v[tail], xs[tail], 1)
    return float(np.polyval(fx, slope * vgs + icpt)), "extrapolated <= %.2f V" % _REACH_V


def _curve_for(per_vdd: dict | None, legacy_curve, vdrain: float):
    """The curve drawn for *vdrain*: bound per-VDD curve, else the only curve."""
    per_vdd = per_vdd or {}
    curves = per_vdd.get("curves") or []
    bound = [
        c for c in curves
        if c.get("vdd_v") is not None and abs(float(c["vdd_v"]) - vdrain) < 1e-6
        and not c.get("gates") and len(c.get("curve_px") or []) >= 2
    ]
    if len(bound) == 1:
        return bound[0]["curve_px"], f"per_vdd VDD={vdrain:g}V"
    if len(bound) > 1:
        return None, "two curves bound to the table voltage"
    # By elimination: every other printed value is bound, the table's value
    # is printed but its label was refused, and exactly one curve is left --
    # in the slot its value takes in the left-to-right VDD order
    # (FDMS86500L p5: 20 V and 40 V bound on the outer curves, the 30 V
    # leader's tip on a shared segment).
    labels = per_vdd.get("labels") or []
    values = sorted({float(l["vdd_v"]) for l in labels if l.get("vdd_v") is not None})
    unbound_curves = [c for c in curves if c.get("vdd_v") is None]
    if (
        any(abs(v - vdrain) < 1e-6 for v in values)
        and len(values) == len(curves)
        and len(unbound_curves) == 1
        and not unbound_curves[0].get("gates")
        and len(unbound_curves[0].get("curve_px") or []) >= 2
        and all(c.get("vdd_v") is not None for c in curves if c is not unbound_curves[0])
    ):
        order = sorted(curves, key=lambda c: c.get("index", 0))
        slot = next(i for i, v in enumerate(values) if abs(v - vdrain) < 1e-6)
        rest = [float(c["vdd_v"]) for c in order if c is not unbound_curves[0]]
        if order[slot] is unbound_curves[0] and rest == [v for v in values if abs(v - vdrain) >= 1e-6]:
            return unbound_curves[0]["curve_px"], f"per_vdd VDD={vdrain:g}V by elimination"
    single = per_vdd.get("binding") == "single_curve" or per_vdd.get("curve_count") == 1
    if single and len(legacy_curve or ()) >= 2:
        labels = [c.get("vdd_v") for c in curves if c.get("vdd_v") is not None]
        if labels and all(abs(float(v) - vdrain) > 1e-6 for v in labels):
            return None, f"the only curve is labelled {labels[0]:g} V, table is {vdrain:g} V"
        return legacy_curve, "only curve"
    return None, "no curve bound to the table voltage"


def table_check(rows, *, per_vdd, legacy_curve, x_ticks, y_ticks) -> TableCheck:
    """Compare the table's Qg typ with the curve read through *x_ticks*."""
    usable = [r for r in rows if r.typ_nc is not None and r.vgs_v is not None and r.vdrain_v is not None]
    if not rows:
        return TableCheck("not_evaluable", "no Total Gate Charge row in the spec table")
    if not usable:
        return TableCheck("not_evaluable", "no Total Gate Charge row with typ (nC), VGS and VDD/VDS")
    reasons = []
    # the highest drive first: the curve runs up to it
    for row in sorted(usable, key=lambda r: -float(r.vgs_v)):
        curve, why = _curve_for(per_vdd, legacy_curve, float(row.vdrain_v))
        if curve is None:
            reasons.append(why)
            continue
        read, how = read_qg_at_vgs(curve, x_ticks, y_ticks, float(row.vgs_v))
        if read is None:
            reasons.append(how)
            continue
        tol = TOLERANCE_REL * float(row.typ_nc) + 0.5 * float(row.typ_resolution_nc or 1.0)
        delta = read - float(row.typ_nc)
        return TableCheck(
            "agrees" if abs(delta) <= tol else "contradicts",
            f"{why}: Qg at VGS {row.vgs_v:g} V reads {read:.2f} nC ({how}); "
            f"table typ {row.typ_nc:g} nC at {row.vdrain_name or 'VD'}={row.vdrain_v:g} V; "
            f"tolerance {tol:.2f} nC",
            float(row.typ_nc), read, delta, delta / float(row.typ_nc), tol,
            float(row.vgs_v), float(row.vdrain_v), why, row.row_text, row.page,
        )
    return TableCheck("not_evaluable", "; ".join(dict.fromkeys(reasons))[:300])


# --- Qgs / Qgd: the plateau's start and length --------------------------------
#
# The deciding evidence for an axis bound to its rules by ORDER when the Qg
# check cannot confirm it (FDP045N10A-F102: the curve reaches 10 V at 56.7 nC
# on the rules while the table prints 54; the plateau reads Qgs 16.77 / Qgd
# 12.99 against the table's 17 / 13). An ordinal binding fails in whole steps
# (a one-rule shift moves Qgs by a pitch, a miscounted rule scales it by
# ~1/N, >= 14 % on the 6-7 interval axes this binds), so a Qgs reading within
# a few percent confirms the binding even when the datasheet's own Qg typ and
# curve disagree by a few percent.
#
# Tolerances, measured on 205 of Fab's HV charts whose Qg axis the grid check
# verified and whose curve has a plateau (out/gc-qg-axis/r3/plateau_calib.json):
#   |Qgs read - typ| / typ: p50 0.7 %, p75 1.7 %, p90 4.2 %, p95 5.4 %, max 15 %
#   |Qgd read - typ| / typ: p50 1.6 %, p75 3.4 %, p90 12.6 %, p95 18.5 %, max 25 %
# Qgd's corner (plateau end) is the softer one; it is a sanity bound only.
PLATEAU_QGS_REL = 0.06
PLATEAU_QGD_REL = 0.20
# The Qg typ may disagree with the printed curve by at most this much for the
# plateau to verify an ordinal axis: well under the smallest step failure.
PLATEAU_MAX_QG_REL = 0.10
# Split rows belong to the Qg row's test block when this close on its page.
_SPLIT_WINDOW_PT = 80.0
# A plateau is the flat middle segment: its slope below this fraction of both rises.
_PLATEAU_FLAT_FRACTION = 0.35


@dataclass(frozen=True)
class PlateauCheck:
    status: str  # agrees / contradicts / not_evaluable
    reason: str
    qgs_typ_nc: float | None = None
    qgs_read_nc: float | None = None
    qgs_tolerance_nc: float | None = None
    qgd_typ_nc: float | None = None
    qgd_read_nc: float | None = None
    qgd_tolerance_nc: float | None = None
    curve: str | None = None

    def payload(self) -> dict[str, object]:
        out = asdict(self)
        for key in ("qgs_read_nc", "qgs_tolerance_nc", "qgd_read_nc", "qgd_tolerance_nc"):
            if out[key] is not None:
                out[key] = round(float(out[key]), 4)
        return out


def plateau_knees(q, v):
    """Breakpoints and slopes of the least-squares continuous 3-segment fit of v(q).

    Breakpoints are searched over the sample abscissae (every ordered pair, at
    least two samples per segment) in one batched solve of the 4x4 normal
    equations; returns ``(k1, k2, rise1, plateau, rise2)`` or None.
    """
    q = np.asarray(q, dtype=float)
    v = np.asarray(v, dtype=float)
    order = np.argsort(q)
    q, v = q[order], v[order]
    n = len(q)
    if n < 8:
        return None
    idx = np.arange(2, n - 2)
    knots = q[idx]
    H = np.maximum(0.0, q[None, :] - knots[:, None])  # (k, n)
    sq, sv = q.sum(), v.sum()
    sh, shq, shv, shh = H.sum(1), H @ q, H @ v, (H * H).sum(1)
    cross = H @ H.T  # (k, k): sum h_i h_j
    i, j = np.triu_indices(len(idx), k=2)
    m = len(i)
    G = np.empty((m, 4, 4))
    G[:, 0, 0] = n
    G[:, 0, 1] = G[:, 1, 0] = sq
    G[:, 1, 1] = (q * q).sum()
    G[:, 0, 2] = G[:, 2, 0] = sh[i]
    G[:, 0, 3] = G[:, 3, 0] = sh[j]
    G[:, 1, 2] = G[:, 2, 1] = shq[i]
    G[:, 1, 3] = G[:, 3, 1] = shq[j]
    G[:, 2, 2] = shh[i]
    G[:, 3, 3] = shh[j]
    G[:, 2, 3] = G[:, 3, 2] = cross[i, j]
    b = np.stack([np.full(m, sv), np.full(m, (q * v).sum()), shv[i], shv[j]], axis=1)
    ok = np.abs(np.linalg.det(G)) > 1e-12 * np.abs(G).max(axis=(1, 2)) ** 4
    if not ok.any():
        return None
    coef = np.full((m, 4), np.nan)
    coef[ok] = np.linalg.solve(G[ok], b[ok][..., None])[..., 0]
    sse = (v * v).sum() - np.einsum("mk,mk->m", coef, b)
    sse[~ok] = np.inf
    best = int(np.argmin(sse))
    c = coef[best]
    return float(knots[i[best]]), float(knots[j[best]]), float(c[1]), float(c[1] + c[2]), float(c[1] + c[2] + c[3])


def _bound_split_rows(split_rows, qg_rows, table: TableCheck):
    if table.page is None or table.row_text is None:
        return None, "no Qg row bound by the table check"
    qrow = [r for r in qg_rows if r.page == table.page and r.row_text == table.row_text]
    if len(qrow) != 1:
        return None, "the table check's Qg row is not unique"
    near = [s for s in split_rows if s.page == qrow[0].page and abs(s.y_pt - qrow[0].y_pt) <= _SPLIT_WINDOW_PT]
    qgs = [s for s in near if s.kind == "qgs"]
    qgd = [s for s in near if s.kind == "qgd"]
    if len(qgs) != 1 or len(qgd) != 1:
        return None, f"{len(qgs)} Qgs and {len(qgd)} Qgd rows in the Qg row's block"
    if not all(s.unit_on_row for s in (qgs[0], qgd[0])) and qrow[0].typ_nc is None:
        return None, "Qgs/Qgd unit not printed and the Qg row's is not nC"
    return (qgs[0], qgd[0]), ""


def plateau_check(split_rows, qg_rows, table: TableCheck, *, per_vdd, legacy_curve, x_ticks, y_ticks) -> PlateauCheck:
    """Compare the plateau of the table voltage's curve with the table's Qgs / Qgd."""
    if table.vdrain_v is None or table.vgs_v is None:
        return PlateauCheck("not_evaluable", f"no Qg test condition ({table.status}: {table.reason})"[:300])
    rows, why = _bound_split_rows(split_rows, qg_rows, table)
    if rows is None:
        return PlateauCheck("not_evaluable", why)
    qgs_row, qgd_row = rows
    curve, how = _curve_for(per_vdd, legacy_curve, float(table.vdrain_v))
    if curve is None:
        return PlateauCheck("not_evaluable", how)
    fx, fy = _fit(list(x_ticks)), _fit(list(y_ticks))
    if fx is None or fy is None:
        return PlateauCheck("not_evaluable", "fewer than 2 ticks on an axis")
    pts = np.asarray(sorted((float(x), float(y)) for x, y in curve), dtype=float)
    if len(pts) < 8:
        return PlateauCheck("not_evaluable", "fewer than 8 curve points")
    q = np.polyval(fx, pts[:, 0])
    v = np.polyval(fy, pts[:, 1])
    keep = v <= float(table.vgs_v)
    knees = plateau_knees(q[keep], v[keep])
    if knees is None:
        return PlateauCheck("not_evaluable", "no 3-segment fit")
    k1, k2, rise1, flat, rise2 = knees
    if not (rise1 > 0 and rise2 > 0 and flat < _PLATEAU_FLAT_FRACTION * min(rise1, rise2)):
        return PlateauCheck(
            "not_evaluable",
            f"no plateau: slopes {rise1:.3g}/{flat:.3g}/{rise2:.3g} V/nC", curve=how,
        )
    qgs_read, qgd_read = k1, k2 - k1
    qgs_tol = PLATEAU_QGS_REL * qgs_row.typ_nc + 0.5 * float(qgs_row.typ_resolution_nc or 1.0)
    qgd_tol = PLATEAU_QGD_REL * qgd_row.typ_nc + 0.5 * float(qgd_row.typ_resolution_nc or 1.0)
    agree = abs(qgs_read - qgs_row.typ_nc) <= qgs_tol and abs(qgd_read - qgd_row.typ_nc) <= qgd_tol
    return PlateauCheck(
        "agrees" if agree else "contradicts",
        f"{how}: plateau from {qgs_read:.2f} nC, {qgd_read:.2f} nC long; table Qgs {qgs_row.typ_nc:g} "
        f"(tolerance {qgs_tol:.2f}), Qgd {qgd_row.typ_nc:g} (tolerance {qgd_tol:.2f})",
        qgs_row.typ_nc, qgs_read, qgs_tol, qgd_row.typ_nc, qgd_read, qgd_tol, how,
    )
