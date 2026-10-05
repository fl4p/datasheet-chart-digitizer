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
