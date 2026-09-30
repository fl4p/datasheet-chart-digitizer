"""Physically plausible, parametric, seeded curve families for datasheet-style charts.

Every model returns a *spec*::

    {cls, title, conditions, match, x: axis, y: axis, curves: [{label, printed, x, y}],
     params: {...}, traps: [...]}

``x``/``y`` of a curve are the model's dense samples (unclipped: they may run past the
frame; the renderer clips, and ``gt.py`` computes the exact visible part). ``label`` is what
the scorer binds (a number for ``match='number'``, the identifying token for ``'name'``);
``printed`` is the text printed on the chart for that curve.

``traps`` asks a model for a specific hard case (e.g. ``near_merge``, ``shallow_cross``,
``lin_y_rail``); the model returns the subset it actually produced in ``spec['traps']``.
"""
from __future__ import annotations

import math

import numpy as np

from .axes import linear_range, nice_step

K_B = 8.617e-5  # eV/K


def _u(rng, a, b):
    return float(rng.uniform(a, b))


def _logu(rng, a, b):
    return float(10 ** rng.uniform(math.log10(a), math.log10(b)))


def _choice(rng, seq, p=None):
    return seq[int(rng.choice(len(seq), p=p))]


def softplus(z):
    return np.where(z > 30, z, np.log1p(np.exp(np.minimum(z, 30))))


def lin_axis(lo, hi, n=6, unit="", title="", include_zero=False, exact=False):
    a, b, step = linear_range(lo, hi, n, include_zero=include_zero)
    if exact:
        a, b = lo, hi
    return {"min": a, "max": b, "scale": "linear", "unit": unit, "title": title, "major_step": step}


def log_axis(lo, hi, unit="", title="", snap=True):
    if snap:
        lo, hi = 10 ** math.floor(math.log10(lo) + 1e-9), 10 ** math.ceil(math.log10(hi) - 1e-9)
    return {"min": lo, "max": hi, "scale": "log10", "unit": unit, "title": title}


def _xgrid(ax, n=1500):
    if ax["scale"].startswith("log"):
        return np.logspace(math.log10(ax["min"]), math.log10(ax["max"]), n)
    return np.linspace(ax["min"], ax["max"], n)


def _temps(rng, n, pool=(-55, -40, 25, 100, 125, 150, 175)):
    n = min(n, len(pool))
    idx = sorted(rng.choice(len(pool), n, replace=False))
    t = [pool[i] for i in idx]
    if 25 not in t:
        t[min(1, len(t) - 1)] = 25
        t = sorted(set(t))
    return t


def _deg(t, rng, style=None):
    style = style or _choice(rng, ["Tj = {} °C", "TJ = {}°C", "{} °C", "Tj={}°C", "T = {} °C"])
    s = str(int(t)) if float(t).is_integer() else str(t)
    if t < 0 and rng.random() < 0.5:
        s = "−" + s[1:]
    return style.format(s)


# ----------------------------------------------------------------------------- capacitance
def capacitance(rng, traps=frozenset()):
    vmax = _choice(rng, [20, 25, 30, 40, 60, 80, 100, 150, 200, 250, 600, 650])
    cgs = _logu(rng, 300, 15000)                 # pF
    crss_hv = cgs * _logu(rng, 0.002, 0.03)
    crss0 = cgs * _u(rng, 0.08, 0.5)
    vk = vmax * _u(rng, 0.02, 0.12)                    # Crss knee
    pk = _u(rng, 1.2, 3.5)
    cross = "coss_ciss_cross" in traps or rng.random() < 0.35
    cds0 = cgs * (_u(rng, 1.3, 3.0) if cross else _u(rng, 0.2, 0.8))
    merge = "near_merge" in traps
    cds_hv = (crss_hv * _u(rng, 0.02, 0.08)) if merge else cgs * _logu(rng, 0.01, 0.2)
    phi, m = _u(rng, 0.3, 2.0), _u(rng, 0.4, 0.9)
    lin_x = rng.random() < 0.45
    lin_y = "lin_y_rail" in traps
    cgs_slope = _u(rng, 0.02, 0.2)
    unit = "nF" if (cgs > 6000 and rng.random() < 0.6) else "pF"
    k = 1e-3 if unit == "nF" else 1.0

    def curves(v):
        crss = crss_hv + (crss0 - crss_hv) / (1 + (v / vk) ** pk)
        cds = cds_hv + (cds0 - cds_hv) / (1 + v / phi) ** m
        cg = cgs * (1 - cgs_slope * (1 - 1 / (1 + v / (vmax * 0.1))))
        return {"Ciss": (cg + crss) * k, "Coss": (cds + crss) * k, "Crss": crss * k}

    xt = "VDS, Drain-Source Voltage (V)" if rng.random() < 0.5 else "VDS [V]"
    yt = f"C, Capacitance ({unit})" if rng.random() < 0.5 else f"C [{unit}]"
    if lin_x:
        xa = lin_axis(0, vmax, 5, "V", xt)
    else:
        xa = log_axis(_choice(rng, [0.1, 1]) if vmax <= 100 else 1, vmax, "V", xt, snap=rng.random() < 0.6)
        if xa["max"] < vmax:
            xa["max"] = vmax
    v = _xgrid(xa)
    cv = curves(np.maximum(v, 1e-6))
    cmax = max(float(np.nanmax(c)) for c in cv.values())
    cmin = min(float(np.nanmin(c)) for c in cv.values())
    if lin_y:
        ya = lin_axis(0, cmax * _u(rng, 1.02, 1.3), 5, unit, yt, include_zero=True)
    else:
        ya = log_axis(cmin * _u(rng, 0.3, 0.9), cmax * _u(rng, 1.1, 2.5), unit, yt)
    printed = {"Ciss": _choice(rng, ["Ciss", "Ciss", "C iss", "Ciss = CGS + CGD"]),
               "Coss": _choice(rng, ["Coss", "Coss", "C oss", "Coss = CDS + CGD"]),
               "Crss": _choice(rng, ["Crss", "Crss", "C rss", "Crss = CGD"])}
    got = set()
    if cross and np.any(np.diff(np.sign(cv["Ciss"] - cv["Coss"])) != 0):
        got.add("coss_ciss_cross")
    if merge:
        got.add("near_merge")
    if lin_y:
        got.add("lin_y_rail")
    return {
        "cls": "capacitance", "match": "name",
        "title": _choice(rng, ["Typ. capacitances", "Typical Capacitance", "Capacitance Characteristics",
                               "Capacitance vs. Drain-Source Voltage"]),
        "conditions": _choice(rng, ["VGS = 0 V, f = 1 MHz", "VGS = 0 V; f = 1 MHz", "f = 1 MHz"]),
        "x": xa, "y": ya,
        "curves": [{"label": n, "printed": printed[n], "x": v, "y": cv[n]} for n in ("Ciss", "Coss", "Crss")],
        "params": dict(vmax=vmax, cgs_pf=cgs, crss0=crss0, crss_hv=crss_hv, vk=vk, cds0=cds0, cds_hv=cds_hv),
        "traps": sorted(got),
    }


# ----------------------------------------------------------------------------- transfer
def _id_mos(vgs, t, p):
    tk = t + 273.15
    vth = p["vth"] - p["kv"] * (t - 25)
    k = p["k"] * (tk / 298.15) ** (-p["beta"])
    s = p["s"] * tk / 298.15
    return k * (s * softplus((vgs - vth) / s)) ** p["n"]


def transfer(rng, traps=frozenset()):
    temps = _temps(rng, int(rng.integers(2, 5)))
    p = dict(vth=_u(rng, 1.2, 4.0), kv=_u(rng, 3e-3, 7e-3), beta=_u(rng, 1.2, 2.0), n=_u(rng, 1.3, 2.0), s=_u(rng, 0.05, 0.12),
             k=_logu(rng, 2, 200))
    if "shallow_cross" in traps:
        p["beta"], p["kv"] = _u(rng, 1.6, 2.0), _u(rng, 2.5e-3, 3.5e-3)
    log_y = "log_y" in traps or rng.random() < 0.15
    vmax_x = math.ceil(p["vth"] + _u(rng, 2.0, 5.0))
    xa = lin_axis(0 if rng.random() < 0.7 else math.floor(p["vth"] * 0.5), vmax_x, 6, "V",
                  _choice(rng, ["VGS, Gate-Source Voltage (V)", "VGS [V]", "Gate-to-Source Voltage, VGS (V)"]))
    v = _xgrid(xa)
    ids = {t: _id_mos(v, t, p) for t in temps}
    top = max(float(i.max()) for i in ids.values())
    yt = _choice(rng, ["ID, Drain Current (A)", "ID [A]", "Drain Current, ID (A)"])
    if log_y:
        ya = log_axis(max(top * 1e-5, 1e-3), top * _u(rng, 0.5, 2.0), "A", yt)
    else:
        ya = lin_axis(0, top * _u(rng, 0.4, 1.05), 5, "A", yt, include_zero=True)
    got = {"log_y"} if log_y else {"lin_y_rail"}
    # ZTC: where the hottest and coldest curves cross
    d = ids[temps[-1]] - ids[temps[0]]
    sc = np.nonzero(np.diff(np.sign(d)))[0]
    ztc = float(ids[temps[0]][sc[0]]) if len(sc) else None
    if ztc is not None and "shallow_cross" in traps:
        got.add("shallow_cross")
    sty = _choice(rng, ["Tj = {} °C", "TJ = {}°C", "{} °C", "Tj={}°C"])
    return {
        "cls": "transfer", "match": "number",
        "title": _choice(rng, ["Typ. transfer characteristics", "Transfer Characteristics",
                               "Typical Transfer Characteristics"]),
        "conditions": _choice(rng, ["VDS = 5 V", "VDS = 10 V", "VDS ≥ 2 x ID x RDS(on)max"]),
        "x": xa, "y": ya,
        "curves": [{"label": t, "printed": _deg(t, rng, sty), "x": v, "y": ids[t]} for t in temps],
        "params": {**p, "temps": temps, "ztc_A": ztc}, "traps": sorted(got),
    }


# ----------------------------------------------------------------------------- output
def output(rng, traps=frozenset()):
    vth = _u(rng, 1.0, 3.5)
    pool = [2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6, 7, 8, 10, 15]
    pool = [g for g in pool if g > vth + 0.4]
    n = int(rng.integers(3, 8))
    vgs = sorted(set(float(x) for x in rng.choice(pool, min(n, len(pool)), replace=False)))
    kk, lam, a = _logu(rng, 5, 200), _u(rng, 0.005, 0.05), _u(rng, 0.4, 0.9)
    xa = lin_axis(0, _choice(rng, [1, 2, 3, 5, 10, 20]), 5, "V",
                  _choice(rng, ["VDS, Drain-Source Voltage (V)", "VDS [V]"]))
    v = _xgrid(xa)
    cur = {}
    for g in vgs:
        vov = g - vth
        isat = 0.5 * kk * vov ** 2
        cur[g] = isat * (1 + lam * v) * np.tanh(v / (a * vov))
    top = max(float(c.max()) for c in cur.values())
    ya = lin_axis(0, top * _u(rng, 0.5, 1.05), 5, "A", _choice(rng, ["ID, Drain Current (A)", "ID [A]"]),
                  include_zero=True)
    fmt = _choice(rng, ["VGS = {} V", "{} V", "{}V", "VGS={}V"])
    return {
        "cls": "output", "match": "number",
        "title": _choice(rng, ["Typ. output characteristics", "Output Characteristics",
                               "Typical Output Characteristics"]),
        "conditions": _choice(rng, ["Tj = 25 °C", "TC = 25°C", "tp = 80 µs, Tj = 25 °C"]),
        "x": xa, "y": ya,
        "curves": [{"label": g, "printed": fmt.format(_g(g)), "x": v, "y": cur[g]} for g in vgs],
        "params": dict(vth=vth, k=kk, lam=lam, vgs=vgs), "traps": ["lin_y_rail"],
    }


def _g(g):
    return str(int(g)) if float(g).is_integer() else str(g)


# ----------------------------------------------------------------------------- RDS(on)(Tj)
def rdson_temperature(rng, traps=frozenset()):
    alpha = _u(rng, 1.4, 2.6)
    tmin, tmax = _choice(rng, [(-50, 150), (-55, 175), (-60, 180), (-75, 175), (-50, 200), (0, 150)])
    absolute = rng.random() < 0.4
    xa = lin_axis(tmin, tmax, 6, "°C", _choice(rng, ["Tj, Junction Temperature (°C)", "Tj [°C]",
                                                        "TJ, Junction Temperature (°C)"]), exact=True)
    xa["major_step"] = nice_step(tmax - tmin, 6)
    t = _xgrid(xa)
    r = ((t + 273.15) / 298.15) ** alpha
    curves, match = [], "single"
    if absolute:
        r25 = _logu(rng, 0.8, 80)
        if rng.random() < 0.6:
            match = "number"
            for g, f in ((4.5, _u(rng, 1.2, 1.6)), (10, 1.0)):
                curves.append({"label": g, "printed": f"VGS = {_g(g)} V", "x": t, "y": r * r25 * f})
        else:
            curves.append({"label": "RDS(on)", "printed": "", "x": t, "y": r * r25})
        top = max(float(c["y"].max()) for c in curves)
        ya = lin_axis(0, top * _u(rng, 1.05, 1.4), 5, "mΩ", _choice(rng, ["RDS(on) [mΩ]", "RDS(on), On-Resistance (mΩ)"]),
                      include_zero=True)
    else:
        curves.append({"label": "RDS(on)", "printed": "", "x": t, "y": r})
        ya = lin_axis(math.floor(float(r.min()) * 5) / 5 if rng.random() < 0.6 else 0,
                      float(r.max()) * _u(rng, 1.02, 1.2), 5, "", _choice(rng, [
                          "RDS(on), normalized", "Normalized On-Resistance", "RDS(on) (Normalized)"]))
    return {
        "cls": "rdson_temperature", "match": match,
        "title": _choice(rng, ["Drain-source on-state resistance", "Normalized On-Resistance vs. Temperature",
                               "On-Resistance vs. Junction Temperature"]),
        "conditions": _choice(rng, ["ID = 20 A, VGS = 10 V", "VGS = 10 V, ID = 50 A", "ID = 10 A"]),
        "x": xa, "y": ya, "curves": curves, "params": dict(alpha=alpha, absolute=absolute),
        "traps": ["signed_axis"] if tmin < 0 else [],
    }


# ----------------------------------------------------------------------------- RDS(on)(ID)
def rdson_id(rng, traps=frozenset()):
    vth = _u(rng, 1.0, 2.5)
    pool = [g for g in (2.5, 3.3, 4.5, 6, 7, 8, 10) if g > vth + 0.8]
    vgs = sorted(set(float(x) for x in rng.choice(pool, min(int(rng.integers(2, 5)), len(pool)), replace=False)))
    rfix, rch = _logu(rng, 0.3, 20), 0.0
    rch = rfix * _u(rng, 0.5, 3.0)
    imax = _choice(rng, [10, 20, 40, 50, 80, 100, 150, 200, 300])
    flat = "flat_curves" in traps or rng.random() < 0.35
    xa = lin_axis(0, imax, 5, "A", _choice(rng, ["ID, Drain Current (A)", "ID [A]"]))
    i = _xgrid(xa)
    curves = []
    for g in vgs:
        r0 = rfix + rch / (g - vth)
        isc = imax * _u(rng, 0.8, 3.0) * (g - vth) / (max(vgs) - vth)
        c2 = (_u(rng, 0.02, 0.08) if flat else _u(rng, 0.1, 0.8))
        r = r0 * (1 + c2 * i / imax + (i / isc) ** 3 * (0.3 if flat else 1.0))
        end = imax * (_u(rng, 0.5, 1.0) if rng.random() < 0.4 else 1.3)
        keep = i <= end
        curves.append({"label": g, "printed": _choice(rng, ["VGS = {} V", "{} V", "VGS={}V"]).format(_g(g)),
                       "x": i[keep], "y": r[keep]})
    top = max(float(c["y"].max()) for c in curves)
    lo = min(float(c["y"].min()) for c in curves)
    ya = lin_axis(0 if rng.random() < 0.6 else lo * 0.8, min(top, lo * 6) * _u(rng, 1.05, 1.3), 5, "mΩ",
                  _choice(rng, ["RDS(on) [mΩ]", "RDS(on), Drain-Source On-Resistance (mΩ)"]), include_zero=False)
    if ya["min"] < 0:
        ya["min"] = 0.0
    return {
        "cls": "rdson_id", "match": "number",
        "title": _choice(rng, ["Typ. drain-source on-state resistance", "On-Resistance vs. Drain Current",
                               "Drain-Source On-Resistance"]),
        "conditions": _choice(rng, ["Tj = 25 °C", "TJ = 25°C, tp = 80 µs"]),
        "x": xa, "y": ya, "curves": curves, "params": dict(vth=vth, vgs=vgs, flat=flat),
        "traps": ["flat_curves"] if flat else [],
    }


# ----------------------------------------------------------------------------- gate charge
def gate_charge(rng, traps=frozenset()):
    vmax = _choice(rng, [30, 40, 60, 80, 100, 150, 200, 600])
    vdds = sorted({round(vmax * f / 5) * 5 or 5 for f in rng.choice([0.2, 0.25, 0.5, 0.8], int(rng.integers(1, 4)),
                                                                        replace=False)})
    vpl = _u(rng, 2.2, 5.5)
    qgs = _logu(rng, 3, 60)
    qgd0 = qgs * _u(rng, 0.4, 1.8)
    vend = _choice(rng, [v for v in (10, 10, 12, 4.5, 8) if v >= vpl + 1.5])
    slope_after = _u(rng, 0.25, 0.6) * vpl / qgs
    plateau_slope = _u(rng, 0.0, 0.03) * vpl / qgs
    curves = []
    qtot_max = 0.0
    for vdd in vdds:
        qgd = qgd0 * (vdd / vmax) ** _u(rng, 0.55, 0.75) * 1.6
        q = np.linspace(0, (qgs + qgd + (vend - vpl) / slope_after) * 1.15, 2500)
        pre = vpl * q / qgs
        plat = vpl + plateau_slope * (q - qgs)
        post = vpl + plateau_slope * qgd + slope_after * (q - qgs - qgd)
        w = 0.03 * vpl  # corner smoothing width in volts
        hi = plat + w * softplus((post - plat) / w)      # smooth max(plateau, post-plateau)
        vg = pre - w * softplus((pre - hi) / w)          # smooth min(pre-plateau, that)
        vg = np.maximum(vg, 0.0)
        keep = vg <= vend * 1.0000001
        curves.append({"label": vdd, "printed": _choice(rng, ["VDS = {} V", "VDD = {} V", "{} V", "VDS={}V"]).format(vdd),
                       "x": q[keep], "y": vg[keep]})
        qtot_max = max(qtot_max, float(q[keep][-1]))
    xa = lin_axis(0, qtot_max * _u(rng, 1.0, 1.3), 5, "nC", _choice(rng, ["Qg, Total Gate Charge (nC)", "QG [nC]",
                                                                       "Gate Charge, Qg (nC)"]))
    ya = lin_axis(0, vend * _u(rng, 1.0, 1.25), 5, "V", _choice(rng, ["VGS, Gate-Source Voltage (V)", "VGS [V]"]),
                  include_zero=True)
    return {
        "cls": "gate_charge", "match": "number",
        "title": _choice(rng, ["Typ. gate charge", "Gate Charge Characteristics", "Gate Charge"]),
        "conditions": _choice(rng, ["ID = 20 A", "ID = 50 A, Tj = 25 °C", "ID = 10 A pulsed"]),
        "x": xa, "y": ya, "curves": curves, "params": dict(vpl=vpl, qgs=qgs, qgd0=qgd0, vdds=vdds, vend=vend),
        "traps": ["shared_segment"] if len(vdds) > 1 else [],
    }


# ----------------------------------------------------------------------------- body diode
def body_diode(rng, traps=frozenset()):
    temps = _temps(rng, int(rng.integers(2, 4)), pool=(-55, 25, 125, 150, 175))
    is25, nid, rs25 = _logu(rng, 1e-12, 1e-8), _u(rng, 1.0, 1.6), _logu(rng, 5e-4, 2e-2)
    eg = 1.12
    log_y = rng.random() < 0.6
    imax = _choice(rng, [10, 30, 100, 200, 300, 1000])
    curves = []
    for t in temps:
        tk = t + 273.15
        is_t = is25 * (tk / 298.15) ** 3 * np.exp(-eg / K_B * (1 / tk - 1 / 298.15) / nid)
        i = np.logspace(math.log10(imax * 1e-5), math.log10(imax * 3), 2000)
        vd = nid * K_B * tk * np.log(i / is_t + 1) + i * rs25 * (tk / 298.15) ** 1.5
        curves.append({"label": t, "printed": _deg(t, rng), "x": vd, "y": i})
    vmax = max(float(np.interp(imax, c["y"], c["x"])) for c in curves)
    xa = lin_axis(0 if rng.random() < 0.5 else 0.2, min(vmax * _u(rng, 1.05, 1.4), 2.0), 5, "V",
                  _choice(rng, ["VSD, Source-Drain Voltage (V)", "VSD [V]", "Body Diode Forward Voltage, VSD (V)"]))
    yt = _choice(rng, ["IS, Source Current (A)", "IF [A]", "Reverse Drain Current, IS (A)"])
    ya = log_axis(imax * _choice(rng, [1e-3, 1e-2, 1e-4]), imax, "A", yt) if log_y else \
        lin_axis(0, imax, 5, "A", yt, include_zero=True)
    return {
        "cls": "body_diode", "match": "number",
        "title": _choice(rng, ["Body diode forward characteristics", "Source-Drain Diode Forward Voltage",
                               "Typ. forward characteristics of reverse diode"]),
        "conditions": _choice(rng, ["VGS = 0 V", "tp = 300 µs"]),
        "x": xa, "y": ya, "curves": curves, "params": dict(is25=is25, n=nid, rs25=rs25, temps=temps),
        "traps": ([] if log_y else ["lin_y_rail"]),
    }


# ----------------------------------------------------------------------------- reverse leakage
def reverse_leakage(rng, traps=frozenset()):
    temps = _temps(rng, int(rng.integers(2, 5)), pool=(25, 50, 75, 100, 125, 150))
    vrrm = _choice(rng, [40, 60, 100, 150, 200, 400, 600])
    i0, p, dbl = _logu(rng, 1e-9, 1e-6), _u(rng, 0.2, 0.8), _u(rng, 8, 12)
    xa = lin_axis(0, vrrm, 5, "V", _choice(rng, ["VR, Reverse Voltage (V)", "Reverse Voltage VR [V]"]))
    v = _xgrid(xa)
    curves = []
    for t in temps:
        ir = i0 * 2 ** ((t - 25) / dbl) * (v / vrrm + 0.02) ** p * np.exp(v / vrrm * _u(rng, 0.3, 1.2))
        curves.append({"label": t, "printed": _deg(t, rng), "x": v, "y": ir})
    lo = min(float(c["y"].min()) for c in curves)
    hi = max(float(c["y"].max()) for c in curves)
    # vendors pick the unit so the decade labels print as plain decimals
    unit, f = ("µA", 1e6) if hi >= 1e-7 else ("nA", 1e9)
    for c in curves:
        c["y"] = c["y"] * f
    ya = log_axis(lo * f * _u(rng, 0.2, 0.9), hi * f * _u(rng, 1.1, 5), unit,
                  _choice(rng, [f"IR, Reverse Current ({unit})", f"Reverse Current IR [{unit}]"]))
    return {
        "cls": "reverse_leakage", "match": "number",
        "title": _choice(rng, ["Typical Reverse Current vs. Reverse Voltage", "Reverse Current vs. Reverse Voltage"]),
        "conditions": "", "x": xa, "y": ya, "curves": curves,
        "params": dict(i0=i0, p=p, doubling_K=dbl, temps=temps), "traps": [],
    }


# ----------------------------------------------------------------------------- powder-core loss
def core_loss(rng, traps=frozenset()):
    k, a, b = _logu(rng, 0.5, 20), _u(rng, 1.2, 1.5), _u(rng, 2.0, 2.6)
    fs = sorted(float(x) for x in rng.choice([10, 25, 50, 100, 200, 300, 500], int(rng.integers(3, 6)), replace=False))
    kg = rng.random() < 0.25
    bu = "kG" if kg else "mT"
    xa = log_axis(_choice(rng, [1, 10]) if not kg else 0.01, 1000 if not kg else 10, bu,
                  f"Peak AC Flux Density ({bu})" if rng.random() < 0.5 else f"B [{bu}]")
    bb = _xgrid(xa)
    bmt = bb * (100 if kg else 1)
    curves = [{"label": f, "printed": f"{_g(f)} kHz", "x": bb, "y": k * (f / 100) ** a * (bmt / 100) ** b * 100}
              for f in fs]
    ya = log_axis(1, 10 ** math.ceil(math.log10(max(float(c["y"][len(bb) // 2]) for c in curves)) + 1), "mW/cm³",
                  _choice(rng, ["Core Loss (mW/cm³)", "Pv [mW/cm³]", "Core Loss Density (mW/cm³)"]))
    return {
        "cls": "core_loss", "match": "number",
        "title": _choice(rng, ["Core Loss vs. Flux Density", "Core loss density", "Typical Core Loss"]),
        "conditions": "", "x": xa, "y": ya, "curves": curves, "params": dict(k=k, a=a, b=b, f_khz=fs),
        "traps": ["clip_top"],
    }


# ----------------------------------------------------------------------------- MLCC |Z|/ESR
def mlcc_impedance(rng, traps=frozenset()):
    c = _choice(rng, [0.01e-6, 0.1e-6, 1e-6, 4.7e-6, 10e-6, 22e-6])
    esl, r0, dd = _logu(rng, 0.2e-9, 1.5e-9), _logu(rng, 0.002, 0.03), _u(rng, 0.01, 0.05)
    xa = log_axis(_choice(rng, [100, 1e3, 1e4]), _choice(rng, [1e8, 1e9, 1e10]), "Hz",
                  _choice(rng, ["Frequency [Hz]", "Frequency (Hz)", "f [Hz]"]))
    f = _xgrid(xa)
    w = 2 * np.pi * f
    esr = r0 * (1 + np.sqrt(f / 1e7)) + dd / (w * c)
    z = np.sqrt(esr ** 2 + (w * esl - 1 / (w * c)) ** 2)
    ya = log_axis(float(esr.min()) * _u(rng, 0.2, 0.8), float(z.max()) * _u(rng, 1.2, 5), "Ω",
                  _choice(rng, ["Impedance / ESR [Ω]", "|Z|, ESR (Ω)", "Impedance (Ω)"]))
    return {
        "cls": "mlcc_impedance", "match": "name",
        "title": _choice(rng, ["Impedance - Frequency Characteristics", "|Z|, ESR vs. Frequency"]),
        "conditions": "", "x": xa, "y": ya,
        "curves": [{"label": "|Z|", "printed": _choice(rng, ["|Z|", "|Z|", "Impedance |Z|"]), "x": f, "y": z},
                   {"label": "ESR", "printed": _choice(rng, ["ESR", "ESR", "ESR (R)"]), "x": f, "y": esr}],
        "params": dict(c=c, esl=esl, r0=r0, d=dd, srf=1 / (2 * np.pi * math.sqrt(esl * c))),
        "traps": ["resonance_dip"],
    }


# ----------------------------------------------------------------------------- V(BR)DSS(Tj)
def breakdown(rng, traps=frozenset()):
    a = _u(rng, 0.7e-3, 1.2e-3)
    tmin, tmax = _choice(rng, [(-50, 150), (-55, 175), (-75, 175), (-60, 180)])
    xa = lin_axis(tmin, tmax, 6, "°C", _choice(rng, ["Tj, Junction Temperature (°C)", "Tj [°C]"]), exact=True)
    xa["major_step"] = nice_step(tmax - tmin, 6)
    t = _xgrid(xa)
    norm = rng.random() < 0.5
    vb = _choice(rng, [30, 40, 60, 80, 100, 150, 200])
    y = (1 + a * (t - 25) + 1e-6 * _u(rng, -1, 1) * (t - 25) ** 2) * (1 if norm else vb * 1.08)
    lo, hi = float(y.min()), float(y.max())
    ya = lin_axis(lo * 0.97, hi * 1.03, 5, "" if norm else "V",
                  "Normalized V(BR)DSS" if norm else "V(BR)DSS [V]")
    return {
        "cls": "breakdown", "match": "single",
        "title": _choice(rng, ["Drain-source breakdown voltage", "Normalized Breakdown Voltage vs. Temperature"]),
        "conditions": _choice(rng, ["ID = 1 mA", "ID = 250 µA", "VGS = 0 V, ID = 1 mA"]),
        "x": xa, "y": ya, "curves": [{"label": "V(BR)DSS", "printed": "", "x": t, "y": y}],
        "params": dict(tc=a, normalized=norm), "traps": ["signed_axis"],
    }


# ----------------------------------------------------------------------------- Zth
def zth(rng, traps=frozenset()):
    n = 4
    rth = _logu(rng, 0.2, 3.0)
    r = rng.dirichlet(np.ones(n)) * rth
    tau = np.sort(10 ** rng.uniform(-5.5, -0.5, n))
    ds = sorted(rng.choice([0.5, 0.2, 0.1, 0.05, 0.02, 0.01], int(rng.integers(2, 6)), replace=False), reverse=True)
    xa = log_axis(_choice(rng, [1e-6, 1e-5]), _choice(rng, [1, 10]), "s",
                  _choice(rng, ["tp, Pulse Width (s)", "tp [s]", "Rectangular Pulse Duration (s)"]))
    tp = _xgrid(xa)
    zs = (r[None, :] * (1 - np.exp(-tp[:, None] / tau[None, :]))).sum(1)
    curves = [{"label": f"D={_g(float(d))}", "printed": f"D = {_g(float(d))}", "x": tp,
               "y": float(d) * rth + (1 - float(d)) * zs} for d in ds]
    curves.append({"label": "Single pulse", "printed": _choice(rng, ["Single pulse", "Single Pulse"]), "x": tp, "y": zs})
    ya = log_axis(float(zs.min()) * _u(rng, 0.3, 0.9), rth * _u(rng, 1.2, 4), "K/W",
                  _choice(rng, ["ZthJC [K/W]", "Zth(j-c), Thermal Impedance (K/W)"]))
    return {
        "cls": "zth", "match": "name",
        "title": _choice(rng, ["Max. transient thermal impedance", "Transient Thermal Impedance"]),
        "conditions": "D = tp/T", "x": xa, "y": ya, "curves": curves,
        "params": dict(rth=rth, r=r.tolist(), tau=tau.tolist()), "traps": [],
    }


MODELS = {
    "capacitance": capacitance, "transfer": transfer, "output": output,
    "rdson_temperature": rdson_temperature, "rdson_id": rdson_id, "gate_charge": gate_charge,
    "body_diode": body_diode, "reverse_leakage": reverse_leakage, "core_loss": core_loss,
    "mlcc_impedance": mlcc_impedance, "breakdown": breakdown, "zth": zth,
}
# Classes a hard/adversarial tier may ask a model to force (model-level traps).
MODEL_TRAPS = {
    "capacitance": ["coss_ciss_cross", "near_merge", "lin_y_rail"],
    "transfer": ["shallow_cross", "log_y"],
    "rdson_id": ["flat_curves"],
}
