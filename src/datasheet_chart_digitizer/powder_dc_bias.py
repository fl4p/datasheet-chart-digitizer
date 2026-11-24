"""Digitize powder-core "Percent Permeability vs DC Magnetizing Force" panels.

KDM/CSC-style panels: x = H in oersted on a log grid (1..1000), y = percent of
initial permeability on a linear grid (0..100 in steps of 10), one monotone
falling curve per permeability grade, highest grade leftmost. The vendor prints
the law next to the curves,

    %mu = 100 / (a + b * H^c)

with one (a, b, c) row per grade, so as for `powder_core_loss` the job is to
check the drawn curves against the printed rows and to give a free fit of the
same form, not to invent a law.

Two extraction paths share one output:

* **vector** -- the curves are stroked paths (KDM KS184 sheet). Calibration comes
  from the gridline strokes and the field rectangle; curve points are the
  flattened path geometry. Exact up to the vendor's drawing.
* **raster** -- scanned/JPEG pages. Calibration from the white gridline gaps in
  the tinted field; curve points are the ink crossings along each horizontal
  %mu level. Because every curve falls monotonically, each level row meets each
  curve exactly once, so curve identity is the left-to-right ORDER of the
  crossings. Rows where a legend marker or table hides a curve show fewer
  crossings and are skipped, never guessed.

Curve identity (which grade is which curve) is bound by order and then CHECKED
against the printed coefficient rows: each curve's median %-point residual
against its own row must be smaller than against either neighbour row.

This is an unvalidated chart class: every result carries status
``overlay-review-required`` until Fab approves overlays.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

STATUS = "overlay-review-required"


class OverlayRefused(ValueError):
    """The overlay would lack the digitized curves or two labelled ticks on an axis."""
_DEFAULT_LEVELS = [float(v) for v in range(95, 4, -5)]


@dataclass
class AxisFit:
    """pixel = scale * u + offset; u = log10(H) on x, %mu on y."""

    kind: str               # "log" | "linear"
    scale: float
    offset: float
    n_lines: int
    rms_px: float
    knots_u: list[float] = field(default_factory=list)   # matched gridline values (u)
    knots_px: list[float] = field(default_factory=list)  # their measured pixel positions

    def pixel(self, v):
        u = np.log10(v) if self.kind == "log" else np.asarray(v, float)
        return self.scale * u + self.offset

    def value(self, px, piecewise: bool = False):
        px = np.asarray(px, float)
        if piecewise and len(self.knots_u) >= 2:
            order = np.argsort(self.knots_px)
            u = np.interp(px, np.asarray(self.knots_px)[order], np.asarray(self.knots_u)[order])
        else:
            u = (px - self.offset) / self.scale
        return 10.0 ** u if self.kind == "log" else u


def _fit_axis(kind: str, lo_px: float, hi_px: float, v_lo: float, v_hi: float,
              centres: list[float], step: float | None, name: str) -> AxisFit:
    """Match gridline centres (plus the field edges) to the expected ladder, then LSQ."""
    if kind == "log":
        dec = int(round(math.log10(v_hi / v_lo)))
        ladder = sorted({math.log10(v_lo * 10 ** d * m) for d in range(dec) for m in range(1, 10)}
                        | {math.log10(v_hi)})
        u_lo, u_hi = math.log10(v_lo), math.log10(v_hi)
        tol_u = 0.03
    else:
        n = int(round((v_hi - v_lo) / step))
        ladder = [v_lo + k * step for k in range(n + 1)]
        u_lo, u_hi = v_lo, v_hi
        tol_u = 0.2 * step
    seed_scale = (hi_px - lo_px) / (u_hi - u_lo)
    pts = {u_lo: lo_px, u_hi: hi_px}
    for c in centres:
        u = u_lo + (c - lo_px) / seed_scale
        best = min(ladder, key=lambda t: abs(t - u))
        if abs(best - u) < tol_u and best not in (u_lo, u_hi):
            if best not in pts or abs(pts[best] - (lo_px + (best - u_lo) * seed_scale)) > abs(c - (lo_px + (best - u_lo) * seed_scale)):
                pts[best] = c
    if len(pts) < 4:
        raise ValueError(f"{name}: only {len(pts)} gridlines matched the {kind} ladder")
    us = np.array(sorted(pts))
    ps = np.array([pts[u] for u in us])
    scale, offset = np.polyfit(us, ps, 1)
    rms = float(np.sqrt(((scale * us + offset - ps) ** 2).mean()))
    return AxisFit(kind, float(scale), float(offset), len(us), rms, us.tolist(), ps.tolist())


# ----------------------------------------------------------------- vector path

def _flatten(items) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for it in items:
        if it[0] == "l":
            seg = [it[1], it[2]]
        elif it[0] == "c":
            p0, p1, p2, p3 = it[1:5]
            t = np.linspace(0, 1, 64)[:, None]
            arr = ((1 - t) ** 3 * np.array([p0.x, p0.y]) + 3 * (1 - t) ** 2 * t * np.array([p1.x, p1.y])
                   + 3 * (1 - t) * t ** 2 * np.array([p2.x, p2.y]) + t ** 3 * np.array([p3.x, p3.y]))
            seg = [type(p0)(float(a), float(b)) for a, b in arr]
        else:
            continue
        for p in seg:
            q = (float(p.x), float(p.y))
            if not pts or pts[-1] != q:
                pts.append(q)
    return pts


def extract_vector(page, clip, x_range, y_range, y_step):
    """Calibrations (in PDF points) and curve polylines from a vector panel, or None."""
    import fitz
    clip = fitz.Rect(*clip)
    draws = [d for d in page.get_drawings() if clip.contains(d["rect"])]
    fields = [d for d in draws if d["type"] == "f" and d.get("fill") and 0.2 < min(d["fill"]) < 0.98
              and max(d["fill"]) < 0.99 and d["rect"].width > 0.5 * clip.width]
    if not fields:
        return None
    fr = max(fields, key=lambda d: d["rect"].width * d["rect"].height)["rect"]
    light = [d for d in draws if d["type"] == "s" and d.get("color") and min(d["color"]) > 0.9]
    vgrid = [d["rect"].x0 for d in light if d["rect"].width < 0.5 and d["rect"].height > 0.9 * fr.height]
    hgrid = [d["rect"].y0 for d in light if d["rect"].height < 0.5 and d["rect"].width > 0.9 * fr.width]
    curves = []
    for d in draws:
        if d["type"] != "s" or not d.get("color") or max(d["color"]) > 0.4:
            continue
        if d["rect"].width < 0.5 * fr.width or len(d["items"]) < 2:
            continue
        if any(it[0] == "re" for it in d["items"]) or d["items"][0][0] == "qu":
            continue                                    # frame
        curves.append(_flatten(d["items"]))
    if not curves:
        return None
    xc = _fit_axis("log", fr.x0, fr.x1, *x_range, vgrid, None, "x")
    yc = _fit_axis("linear", fr.y1, fr.y0, *y_range, hgrid, y_step, "y")
    return xc, yc, curves, (fr.x0, fr.y0, fr.x1, fr.y1)


def vector_points(curves, xc: AxisFit, yc: AxisFit, levels, piecewise: bool):
    """Per curve, H (Oe) where it crosses each %mu level; NaN if it does not."""
    out = []
    for poly in curves:
        a = np.array(poly)
        H = xc.value(a[:, 0], piecewise)
        P = yc.value(a[:, 1], piecewise)
        row = []
        for lv in levels:
            hit = float("nan")
            for k in range(len(P) - 1):
                if (P[k] - lv) * (P[k + 1] - lv) <= 0 and P[k] != P[k + 1]:
                    f = (lv - P[k]) / (P[k + 1] - P[k])
                    hit = float(10 ** (math.log10(H[k]) + f * (math.log10(H[k + 1]) - math.log10(H[k]))))
                    break
            row.append(hit)
        out.append(row)
    return out


# ----------------------------------------------------------------- raster path

def _field_colour(rgb: np.ndarray) -> np.ndarray:
    a = rgb.reshape(-1, 3).astype(int)
    keep = (a.max(1) < 246) & (a.min(1) > 110)
    q = (a[keep] // 8)
    keys, counts = np.unique(q[:, 0] * 4096 + q[:, 1] * 64 + q[:, 2], return_counts=True)
    k = int(keys[counts.argmax()])
    return np.array([k // 4096, (k // 64) % 64, k % 64]) * 8 + 4


def _gridline_centres(frac: np.ndarray, bright: np.ndarray) -> list[float]:
    """Centres of runs where the tint drops out and the pixels are light: gridlines."""
    med = np.median(frac)
    hit = (frac < 0.6 * med) & (bright > 200)
    out, k = [], 0
    while k < len(hit):
        if hit[k]:
            j = k
            while j + 1 < len(hit) and hit[j + 1]:
                j += 1
            w = med - frac[k:j + 1]
            out.append(float((np.arange(k, j + 1) * w).sum() / w.sum()))
            k = j + 1
        else:
            k += 1
    return out


def extract_raster(rgb: np.ndarray, x_range, y_range, y_step, ink_max: int = 110):
    """Calibrations (pixels of `rgb`) plus the ink mask for a raster panel crop."""
    col = _field_colour(rgb)
    a = rgb.astype(int)
    tint = (np.abs(a - col).max(2) < 28)
    rows = np.nonzero(tint.mean(1) > 0.35)[0]
    cols = np.nonzero(tint.mean(0) > 0.35)[0]
    if not len(rows) or not len(cols):
        raise ValueError("no tinted plot field found")
    r0, r1, c0, c1 = int(rows.min()), int(rows.max()), int(cols.min()), int(cols.max())
    sub_t = tint[r0:r1 + 1, c0:c1 + 1]
    bright = a[r0:r1 + 1, c0:c1 + 1].mean(2)
    # field edges: the dark frame sits just outside the tint; use tint extent +-0.5 px
    xc = _fit_axis("log", c0 - 0.5, c1 + 0.5, *x_range,
                   [c + c0 for c in _gridline_centres(sub_t.mean(0), bright.mean(0))], None, "x")
    yc = _fit_axis("linear", r1 + 0.5, r0 - 0.5, *y_range,
                   [r + r0 for r in _gridline_centres(sub_t.mean(1), bright.mean(1))], y_step, "y")
    ink = a.max(2) < ink_max
    return xc, yc, ink, (c0, r0, c1, r1), col


def raster_points(ink: np.ndarray, box, xc: AxisFit, yc: AxisFit, levels, n_curves: int,
                  blanks, piecewise: bool, margin: int = 4):
    """Per level, crossings ordered left to right; a level is used only if it shows n_curves."""
    c0, r0, c1, r1 = box
    out = [[float("nan")] * len(levels) for _ in range(n_curves)]
    seen = []
    for j, lv in enumerate(levels):
        y = float(yc.pixel(lv)) if not piecewise else float(np.interp(
            lv, yc.knots_u, yc.knots_px)) if yc.knots_u[0] < yc.knots_u[-1] else float(yc.pixel(lv))
        r = int(round(y))
        line = ink[r, c0 + margin:c1 - margin + 1].copy()
        xs = np.arange(c0 + margin, c1 - margin + 1)
        Hs = xc.value(xs, piecewise)
        for hlo, hhi, plo, phi in blanks:
            if plo <= lv <= phi:
                line[(Hs >= hlo) & (Hs <= hhi)] = False
        runs, k = [], 0
        while k < len(line):
            if line[k]:
                m = k
                while m + 1 < len(line) and line[m + 1]:
                    m += 1
                runs.append(((xs[k] + xs[m]) / 2.0, m - k + 1))
                k = m + 1
            else:
                k += 1
        seen.append(len(runs))
        if len(runs) != n_curves:
            continue
        for i, (cx, _w) in enumerate(runs):
            out[i][j] = float(xc.value(cx, piecewise))
    return out, seen


# ----------------------------------------------------------------- law + fit

def law(H, a, b, c):
    return 100.0 / (a + b * np.asarray(H, float) ** c)


def fit_bc(H, P):
    """Free fit of 100/(1 + b H^c): linearised start, then Gauss-Newton on %-points."""
    H = np.asarray(H, float)
    P = np.asarray(P, float)
    m = (P > 5) & (P < 97)
    y = np.log(100.0 / P[m] - 1.0)
    c, lb = np.polyfit(np.log(H[m]), y, 1)
    b = math.exp(lb)
    from scipy.optimize import least_squares
    r = least_squares(lambda q: law(H, 1.0, math.exp(q[0]), q[1]) - P, [math.log(b), c])
    b, c = math.exp(r.x[0]), float(r.x[1])
    res = law(H, 1.0, b, c) - P
    return b, c, float(np.sqrt((res ** 2).mean())), float(np.abs(res).max())


def check_binding(perms, points_H, levels, rows):
    """Each curve must sit closest to its own printed row (median abs %-pt residual)."""
    lv = np.asarray(levels, float)
    table = []
    for i, Hs in enumerate(points_H):
        Hs = np.asarray(Hs, float)
        ok = ~np.isnan(Hs)
        meds = {}
        for p in perms:
            if p in rows:
                meds[p] = float(np.median(np.abs(law(Hs[ok], *rows[p]) - lv[ok]))) if ok.any() else float("nan")
        best = min(meds, key=lambda k: meds[k]) if meds else None
        table.append({"curve": i, "bound_perm": perms[i], "closest_printed_row": best,
                      "median_abs_resid_pct": meds})
    ok = all(t["closest_printed_row"] in (None, t["bound_perm"]) for t in table)
    return ok, table


# ----------------------------------------------------------------- overlay

def render_overlay(img_bgr, to_px, x_range, y_range, y_step, curves_HP, law_rows, perms, title, path,
                   polylines_HP=None):
    """Review overlay: the calibration as a full grid, points (circles), printed law (thin line).

    Every calibrated x value (decades and 2..9 minors) and y value (each `y_step`) is drawn
    as a thin red line across the whole plot at its COMPUTED position and labelled in a white
    margin, so a reviewer sees at a glance whether the calibration sits on the printed
    gridlines. A PNG copy is written next to `path`.

    An overlay is the digitized curves plus at least two labelled calibrated ticks per axis.
    Raises OverlayRefused, writing nothing, if either cannot be drawn inside the image.
    """
    red = (0, 0, 235)
    cols = [(255, 60, 0), (0, 140, 255), (0, 150, 0), (170, 0, 170), (0, 80, 140), (110, 110, 0)]
    f = max(1.0, img_bgr.shape[1] / 1400.0)
    pl, pr, pt, pb = int(120 * f), int(40 * f), int(50 * f), int(80 * f)
    img = cv2.copyMakeBorder(img_bgr, pt, pb, pl, pr, cv2.BORDER_CONSTANT, value=(255, 255, 255))

    def P(H, v):
        x, y = to_px(H, v)
        return int(round(x + pl)), int(round(y + pt))

    x0, y_bot = P(x_range[0], y_range[0])
    x1, y_top = P(x_range[1], y_range[1])
    dec = int(round(math.log10(x_range[1] / x_range[0])))
    xs = sorted({x_range[0] * 10 ** d * m for d in range(dec) for m in range(1, 10)} | {x_range[1]})
    font = cv2.FONT_HERSHEY_SIMPLEX
    h_img, w_img = img.shape[:2]
    x_lab = [H for H in xs if 0 <= P(H, y_range[0])[0] < w_img]
    y_vals = [y_range[0] + k * y_step for k in range(int(round((y_range[1] - y_range[0]) / y_step)) + 1)]
    y_lab = [v for v in y_vals if 0 <= P(x_range[0], v)[1] < h_img]
    if len(x_lab) < 2 or len(y_lab) < 2:
        raise OverlayRefused(f"only {len(x_lab)} x / {len(y_lab)} y labelled ticks drawable")
    lines = polylines_HP or [list(zip(Hs, Ps)) for Hs, Ps in curves_HP]
    if not any(sum(1 for H, _ in ln if not math.isnan(H)) >= 2 for ln in lines):
        raise OverlayRefused("no digitized curve to draw")
    for H in x_lab:
        X = P(H, y_range[0])[0]
        cv2.line(img, (X, y_top), (X, y_bot), red, 1, cv2.LINE_AA)
        major = abs(math.log10(H) - round(math.log10(H))) < 1e-9
        lab = f"{H:g}" if major else f"{int(round(H / 10 ** math.floor(math.log10(H))))}"
        sc, th = (0.8 * f, 2) if major else (0.6 * f, 2)
        (tw, _), _ = cv2.getTextSize(lab, font, sc, th)
        cv2.line(img, (X, y_bot), (X, y_bot + (14 if major else 7)), red, 2)
        cv2.putText(img, lab, (X - tw // 2, img.shape[0] - int((10 if major else 45) * f)), font, sc, red, th, cv2.LINE_AA)
    for v in y_lab:
        Y = P(x_range[0], v)[1]
        cv2.line(img, (x0, Y), (x1, Y), red, 1, cv2.LINE_AA)
        cv2.line(img, (x0 - 12, Y), (x0, Y), red, 2)
        lab = f"{v:g}"
        cv2.putText(img, lab, (8, Y + int(8 * f)), font, 0.8 * f, red, 2, cv2.LINE_AA)
    for i, (Hs, Ps) in enumerate(curves_HP):
        c = cols[i % len(cols)]
        p = perms[i]
        if p in law_rows:
            hh = np.logspace(math.log10(x_range[0]), math.log10(x_range[1]), 600)
            q = [P(h, v2) for h, v2 in zip(hh, law(hh, *law_rows[p]))]
            for k in range(0, len(q) - 4, 8):
                cv2.line(img, q[k], q[k + 4], c, 1, cv2.LINE_AA)
        if i < len(lines):
            q = [P(H, Pv) for H, Pv in lines[i] if not math.isnan(H) and H > 0]
            if len(q) >= 2:
                cv2.polylines(img, [np.array(q, np.int32)], False, c, 2, cv2.LINE_AA)
        for H, Pv in zip(Hs, Ps):
            if not math.isnan(H):
                cv2.circle(img, P(H, Pv), 5, c, 2, cv2.LINE_AA)
        cv2.putText(img, f"{p:g}u", (img.shape[1] - pr - int(90 * f), pt + int((20 + 24 * i) * f)), font, 0.7 * f, c, 2, cv2.LINE_AA)
    cv2.putText(img, title + "  red = calibrated grid, coloured line+circles = digitized curve, dashed = printed law",
                (8, int(24 * f)), font, 0.6 * f, red, 2, cv2.LINE_AA)
    cv2.imwrite(str(path), img, [cv2.IMWRITE_WEBP_QUALITY, 95])
    cv2.imwrite(str(Path(path).with_suffix(".png")), img)


# ----------------------------------------------------------------- CLI

def _parse_rows(spec: str) -> dict[float, tuple[float, float, float]]:
    rows = {}
    for part in filter(None, spec.split(";")):
        k, v = part.split(":")
        a, b, c = (float(t) for t in v.split(","))
        rows[float(k)] = (a, b, c)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dsdig digitize-powder-dc-bias",
        description="Digitize a powder-core %permeability-vs-DC-bias panel and check it against the printed rows.")
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--clip", required=True, help="panel region in PDF points: x0,y0,x1,y1 (include axis labels)")
    ap.add_argument("--perms", required=True, help="grades, LEFTMOST curve first, e.g. 125,90,75,60,40,26")
    ap.add_argument("--printed", default="", help="printed rows 'mu:a,b,c;mu:a,b,c' (as read off the panel)")
    ap.add_argument("--mode", choices=["auto", "vector", "raster"], default="auto")
    ap.add_argument("--dpi", type=int, default=600)
    ap.add_argument("--x-range", default="1,1000")
    ap.add_argument("--y-range", default="0,100")
    ap.add_argument("--y-step", type=float, default=10.0)
    ap.add_argument("--levels", default=",".join(f"{v:g}" for v in _DEFAULT_LEVELS))
    ap.add_argument("--blank", action="append", default=[],
                    help="data-space region to ignore in raster mode: Hlo,Hhi,Plo,Phi (table, legend)")
    ap.add_argument("--ink-max", type=int, default=110)
    ap.add_argument("--out", default="out/powder_dc_bias")
    ap.add_argument("--tag", default=None)
    a = ap.parse_args(argv)

    import fitz
    doc = fitz.open(a.pdf)
    page = doc[a.page - 1]
    clip = [float(v) for v in a.clip.split(",")]
    perms = [float(v) for v in a.perms.split(",")]
    rows = _parse_rows(a.printed)
    xr = tuple(float(v) for v in a.x_range.split(","))
    yr = tuple(float(v) for v in a.y_range.split(","))
    levels = [float(v) for v in a.levels.split(",")]
    blanks = [tuple(float(v) for v in s.split(",")) for s in a.blank]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = a.tag or f"{Path(a.pdf).stem}-p{a.page}"

    pix = page.get_pixmap(dpi=a.dpi, clip=fitz.Rect(*clip))
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
    k = a.dpi / 72.0

    result = {"source": a.pdf, "page": a.page, "clip_pt": clip, "status": STATUS, "perms_left_to_right": perms,
              "printed_rows": {f"{p:g}": rows[p] for p in rows}, "levels_pct": levels, "reasons": []}
    vec = extract_vector(page, clip, xr, yr, a.y_step) if a.mode in ("auto", "vector") else None
    if a.mode == "vector" and vec is None:
        print("REFUSED: no vector curves in clip")
        return 2
    variants = {}
    if vec is not None:
        xc, yc, curves, field_pt = vec
        result["mode"] = "vector"
        if len(curves) != len(perms):
            result["status"] = "refused"
            result["reasons"].append(f"{len(curves)} vector curves, {len(perms)} grades given")
        order = np.argsort([np.interp(50.0, *zip(*sorted(
            (float(yc.value(y)), float(xc.value(x))) for x, y in c))) for c in curves])
        curves = [curves[i] for i in order]
        for pw in (False, True):
            variants["piecewise" if pw else "linear"] = vector_points(curves, xc, yc, levels, pw)

        def to_px(H, P, xc=xc, yc=yc):
            return (float(xc.pixel(H)) - clip[0]) * k, (float(yc.pixel(P)) - clip[1]) * k
        raw = [[(float(xc.value(x)), float(yc.value(y))) for x, y in c] for c in curves]
        result["vector_polylines_HP"] = raw
    else:
        xc, yc, ink, box, colour = extract_raster(rgb, xr, yr, a.y_step, a.ink_max)
        result["mode"] = "raster"
        result["field_colour_rgb"] = colour.tolist()
        seen = None
        for pw in (False, True):
            pts, seen = raster_points(ink, box, xc, yc, levels, len(perms), blanks, pw)
            variants["piecewise" if pw else "linear"] = pts
        result["crossings_per_level"] = dict(zip([f"{v:g}" for v in levels], seen))

        def to_px(H, P, xc=xc, yc=yc):
            return float(xc.pixel(H)), float(yc.pixel(P))
    result["x_calibration"] = asdict(xc)
    result["y_calibration"] = asdict(yc)
    for name, cal in (("x", xc), ("y", yc)):
        if cal.rms_px > (1.5 if result["mode"] == "raster" else 1.5 * k / k):
            result["reasons"].append(f"{name} gridline rms {cal.rms_px:.2f} (px or pt) is high")

    served = variants["linear"]
    used = [lv for j, lv in enumerate(levels) if all(not math.isnan(c[j]) for c in served)]
    if len(used) < 5:
        result["status"] = "refused"
        result["reasons"].append(f"only {len(used)} levels show every curve")
    ok, table = check_binding(perms, served, levels, rows) if rows else (None, [])
    result["binding_check"] = {"ok": ok, "per_curve": table}
    if ok is False:
        result["status"] = "refused"
        result["reasons"].append("curve order disagrees with the printed rows")

    curves_out = []
    for i, p in enumerate(perms):
        Hs = np.array(served[i])
        Hp = np.array(variants["piecewise"][i])
        lv = np.array(levels)
        m = ~np.isnan(Hs)
        entry = {"perm": p, "points": [[float(h), float(v)] for h, v in zip(Hs[m], lv[m])],
                 "points_piecewise_y": [[float(h), float(v)] for h, v in zip(Hp, lv) if not math.isnan(h)]}
        if m.sum() >= 5:
            b, c, rms, mx = fit_bc(Hs[m], lv[m])
            entry["free_fit"] = {"a": 1.0, "b": b, "c": c, "rms_pct": rms, "max_abs_pct": mx}
        if p in rows:
            res = law(Hs[m], *rows[p]) - lv[m]
            entry["printed_row"] = rows[p]
            entry["printed_resid_pct"] = {"rms": float(np.sqrt((res ** 2).mean())), "max_abs": float(np.abs(res).max()),
                                          "per_level": [[float(v), float(r)] for v, r in zip(lv[m], res)]}
        curves_out.append(entry)
    result["curves"] = curves_out

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    ov = out / f"{tag}-dcbias-overlay.webp"
    try:
        render_overlay(bgr, to_px, xr, yr, a.y_step,
                       [([pt[0] for pt in c["points"]], [pt[1] for pt in c["points"]]) for c in curves_out],
                       rows, perms, f"{tag} {result['mode']} {result['status']}", ov,
                       polylines_HP=result.get("vector_polylines_HP"))
        result["overlay"] = str(ov)
    except OverlayRefused as e:
        # no reviewable overlay means nothing to review: the values are not served
        result["overlay"] = None
        result["status"] = "refused"
        result["reasons"].append(f"overlay refused: {e}")
        for stale in (ov, ov.with_suffix(".png")):
            stale.unlink(missing_ok=True)
    (out / f"{tag}-dcbias.json").write_text(json.dumps(result, indent=1))

    print(f"mode {result['mode']}  status {result['status']}  {'; '.join(result['reasons'])}")
    print(f"x: {xc.n_lines} lines rms {xc.rms_px:.3f}   y: {yc.n_lines} lines rms {yc.rms_px:.3f}")
    if result["mode"] == "raster":
        print("crossings per level:", result["crossings_per_level"])
    print(f"{'mu':>5} {'n':>3} {'free b':>10} {'free c':>7} {'fit rms':>8} | {'printed rms':>11} {'max':>6}")
    for c in curves_out:
        ff = c.get("free_fit", {})
        pr = c.get("printed_resid_pct", {})
        print(f"{c['perm']:5g} {len(c['points']):3d} {ff.get('b', float('nan')):10.3e} {ff.get('c', float('nan')):7.4f} "
              f"{ff.get('rms_pct', float('nan')):8.3f} | {pr.get('rms', float('nan')):11.3f} {pr.get('max_abs', float('nan')):6.2f}")
    print(f"binding check: {ok}")
    print(f"OVERLAY: {result['overlay']}")
    print("NOTE: unvalidated chart class; overlays need human review.")
    return 0 if result["status"] != "refused" else 2


if __name__ == "__main__":
    sys.exit(main())
