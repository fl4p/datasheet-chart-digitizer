"""Axis ranges, tick positions and tick-label formatting for synthetic datasheet charts.

Convention used everywhere in this package: an axis spec is
``{"min", "max", "scale": "linear"|"log10", "unit", "title"}`` where ``min`` is the data value
at the LEFT (x) / BOTTOM (y) frame edge and ``max`` the value at the right / top edge. A
reversed axis therefore has ``min > max``; the scorer's normalisation handles that unchanged.
"""
from __future__ import annotations

import math

import numpy as np

SUPERSCRIPT = str.maketrans("0123456789-+", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺")


def is_log(ax: dict) -> bool:
    return str(ax["scale"]).startswith("log")


def to_norm(ax: dict, v):
    """Data -> [0, 1] frame fraction (log10 first on log axes). Matches score_cases.norm."""
    v = np.asarray(v, float)
    lo, hi = ax["min"], ax["max"]
    if is_log(ax):
        with np.errstate(divide="ignore", invalid="ignore"):
            return (np.log10(np.where(v > 0, v, np.nan)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    return (v - lo) / (hi - lo)


def from_norm(ax: dict, u):
    u = np.asarray(u, float)
    lo, hi = ax["min"], ax["max"]
    if is_log(ax):
        return 10 ** (math.log10(lo) + u * (math.log10(hi) - math.log10(lo)))
    return lo + u * (hi - lo)


def nice_step(span: float, n_target: int) -> float:
    raw = abs(span) / max(1, n_target)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw * 0.999:
            return m * mag
    return 10 * mag


def linear_range(lo: float, hi: float, n_target: int, *, include_zero=False):
    """Nice [a, b] with major step covering [lo, hi]."""
    if include_zero:
        lo = min(lo, 0.0)
        hi = max(hi, 0.0)
    step = nice_step(hi - lo, n_target)
    a = math.floor(lo / step + 1e-9) * step
    b = math.ceil(hi / step - 1e-9) * step
    return a, b, step


def major_ticks(ax: dict, step: float | None = None) -> list[float]:
    """Major tick values inside the frame (inclusive of the edges when they are on a tick)."""
    lo, hi = sorted((ax["min"], ax["max"]))
    if is_log(ax):
        a, b = math.floor(math.log10(lo) - 1e-9), math.ceil(math.log10(hi) + 1e-9)
        ticks = [10.0 ** k for k in range(a, b + 1)]
        return [t for t in ticks if lo * (1 - 1e-9) <= t <= hi * (1 + 1e-9)]
    step = step or ax.get("major_step") or nice_step(hi - lo, 6)
    k0, k1 = math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9)
    return [round(k * step, 12) for k in range(k0, k1 + 1)]


def minor_ticks(ax: dict, per_major: int, major: list[float], step: float | None = None) -> list[float]:
    """Minor gridline values. Log axes: 2..9 per decade (per_major picks the subset)."""
    lo, hi = sorted((ax["min"], ax["max"]))
    out = []
    if is_log(ax):
        subs = {9: range(2, 10), 4: (2, 4, 6, 8), 2: (2, 5), 1: (5,), 8: range(2, 10)}.get(per_major, range(2, 10))
        a, b = math.floor(math.log10(lo)) - 1, math.ceil(math.log10(hi)) + 1
        for k in range(a, b + 1):
            for s in subs:
                v = s * 10.0 ** k
                if lo * (1 + 1e-9) < v < hi * (1 - 1e-9):
                    out.append(v)
        return out
    step = step or ax.get("major_step") or nice_step(hi - lo, 6)
    if per_major <= 1:
        return []
    sub = step / per_major
    k0, k1 = math.ceil(lo / sub - 1e-9), math.floor(hi / sub + 1e-9)
    majors = set(round(m, 9) for m in major)
    for k in range(k0, k1 + 1):
        v = round(k * sub, 12)
        if round(v, 9) not in majors and lo < v < hi:
            out.append(v)
    return out


# ---------------------------------------------------------------- tick-label formats
SI = [(1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p")]


def _trim(v: float, nd: int = 6) -> str:
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def fmt_tick(v: float, style: str, *, log: bool, decimals: int | None = None, minus: str = "-") -> str:
    """Format one tick value. Styles: plain, comma, si, sci_e, sci_e2, pow10, pow10_uni, plain_dec.

    ``pow10`` returns matplotlib mathtext (rendered as a real superscript glyph run in the PDF).
    """
    if style == "pow10" and log:
        e = int(round(math.log10(v)))
        return rf"$10^{{{e}}}$" if e not in (0, 1) else ("1" if e == 0 else "10")
    if style == "pow10_all" and log:
        e = int(round(math.log10(v)))
        return rf"$10^{{{e}}}$"
    if style == "pow10_uni" and log:
        e = int(round(math.log10(v)))
        return "10" + str(e).translate(SUPERSCRIPT)
    if log and style in ("plain", "comma") and not (1e-3 <= abs(v) <= 1e5):
        style = "pow10_all"  # vendors never print 0.000000001 on a decade axis
        e = int(round(math.log10(v)))
        return rf"$10^{{{e}}}$"
    if log and style in ("plain", "comma"):
        e = int(round(math.log10(v)))
        s = _trim(v, max(0, -e))
        return s.replace(".", ",") if style == "comma" else s
    if style in ("sci_e", "sci_e2"):
        if v == 0:
            return "0"
        e = int(math.floor(math.log10(abs(v)) + 1e-9))
        m = v / 10 ** e
        if style == "sci_e":
            return f"{_trim(m, 2)}E{e:+d}"
        return f"{m:.1f}E{e:+03d}"
    if style == "si":
        if v == 0:
            return "0"
        for f, p in SI:
            if abs(v) >= f * 0.999:
                return _trim(v / f, 3) + p
        return _trim(v, 6)
    s = _trim(v, decimals if decimals is not None else 6) if decimals is None else f"{v:.{decimals}f}"
    if style == "comma":
        s = s.replace(".", ",")
    if s.startswith("-"):
        s = minus + s[1:]
    return s


def decimals_for(ticks: list[float]) -> int:
    for d in range(0, 5):
        if all(abs(t * 10 ** d - round(t * 10 ** d)) < 1e-6 for t in ticks):
            return d
    return 4
