"""Generator self-checks. Every check returns per-case records; an unevaluable case is reported
as such (never as a pass).

vector   the probe PDF (curves in unique colours, otherwise identical) must be geometrically
         identical to the production PDF, and its curve paths -- extracted with PyMuPDF, mapped
         through the known frame transform, clipped to the frame -- must lie on the GT and cover
         it: p95 and max <= 0.01 % of span both ways.
raster   clean case images: share of GT samples with ink within 1 px (>= 0.95, solid curves),
         the centre-line offset of the ink across the GT (median signed offset < 0.5 px), and the
         frame lines measured in the image vs plot_box_px (main-peak centroid, < 0.5 px).
ticks    every tick label / axis title box lies inside the case image (>= 1 px margin) and
         actually contains ink.
inframe  every GT point is inside the frame (u, v in [0, 1]).

usage: python -m tools.synth_charts.validate SET_DIR [--only vector,raster,ticks,inframe]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

from . import gt as G
from . import raster as R
from .axes import to_norm
from .contact import gt_px

VEC_TOL = 0.01      # % of span
INK_MIN = 0.95
OFFSET_MAX = 0.5    # px, curve centre-line offset (median over samples)
FRAME_MAX = 0.5     # px, frame-line centroid vs plot_box_px (clean max 0.30, 1-px mutation min 0.95)


# ----------------------------------------------------------------------------- vector
def _paths(drawings):
    """drawing -> list of polylines (pt), beziers densely evaluated."""
    out = []
    for d in drawings:
        polys, cur = [], []
        for it in d["items"]:
            if it[0] == "l":
                p, q = np.array([it[1].x, it[1].y]), np.array([it[2].x, it[2].y])
                seg = [p, q]
            elif it[0] == "c":
                P = [np.array([pt.x, pt.y]) for pt in it[1:5]]
                t = np.linspace(0, 1, 24)[:, None]
                seg = list((1 - t) ** 3 * P[0] + 3 * (1 - t) ** 2 * t * P[1] + 3 * (1 - t) * t ** 2 * P[2] + t ** 3 * P[3])
            else:
                continue
            if cur and np.abs(cur[-1] - seg[0]).max() < 1e-6:
                cur += seg[1:]
            else:
                if len(cur) > 1:
                    polys.append(np.array(cur))
                cur = list(seg)
        if len(cur) > 1:
            polys.append(np.array(cur))
        out.append(polys)
    return out


def _geom_sig(d):
    return [(it[0], tuple(round(v, 4) for p in it[1:] for v in ((p.x, p.y) if hasattr(p, "x") else
                                                                  (p.x0, p.y0, p.x1, p.y1) if hasattr(p, "x0") else ())))
            for it in d["items"]]


def _hex(c):
    return None if c is None else "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in c[:3])


def vector_check(root: Path, cases):
    by_pdf = defaultdict(list)
    for c in cases:
        by_pdf[c["gt_provenance"]["pdf"]].append(c)
    out = []
    for pdf, cs in by_pdf.items():
        prod = fitz.open(root / pdf)[0].get_drawings()
        probe_path = root / "probe" / Path(pdf).name
        if not probe_path.exists():
            out += [{"id": c["id"], "status": "unevaluable", "why": "no probe pdf"} for c in cs]
            continue
        probe = fitz.open(probe_path)[0].get_drawings()
        same = len(prod) == len(probe) and all(_geom_sig(a) == _geom_sig(b) for a, b in zip(prod, probe))
        polys = _paths(probe)
        for c in cs:
            r = {"id": c["id"], "cls": c["class"], "tier": c["tier"], "geometry_identical": same,
                 "vector_structure": c["factors"]["vector_structure_drawn"]}
            if c["factors"].get("raster_embed"):
                r.update(status="skip", why="raster-embedded chart (no curve paths)")
                out.append(r)
                continue
            X0, Y0, X1, Y1 = c["gt_provenance"]["plot_box_pt"]
            cmap = c["factors"].get("_probe_colors") or c["gt_provenance"]["probe_colors"]
            gt = {str(k["label"]): np.c_[to_norm(c["axis"]["x"], k["x"]), to_norm(c["axis"]["y"], k["y"])]
                  for k in c["curves"]}
            fwd, cov, n_paths = [], [], 0
            owned = defaultdict(list)
            filled = c["factors"]["vector_structure_drawn"] == "filled"
            # filled outlines are compared in pt space (the outline sits w/2 pt off the centre line;
            # points within one stroke width of a curve end are the end caps and are excluded)
            S = np.array([X1 - X0, Y1 - Y0])
            gpt = {lab: g * S for lab, g in gt.items()}
            sty = c["gt_provenance"].get("curve_styles") or {}
            halfs = {lab: sty.get(lab, {}).get("lw", c["factors"]["stroke_pt"]) / 2 for lab in gt}
            for d, pl in zip(probe, polys):
                col = _hex(d.get("fill") if filled else d.get("color"))
                if col not in cmap or (filled and d.get("fill") is None) or (not filled and d.get("color") is None):
                    continue
                labs = [lab for lab in cmap[col] if lab in gt]
                if not labs:
                    continue
                n_paths += 1
                for p in pl:
                    uv = np.c_[(p[:, 0] - X0) / (X1 - X0), (Y1 - p[:, 1]) / (Y1 - Y0)]
                    if filled:
                        q = G.resample_arclength(uv * S, per_unit=4, min_n=2) / S
                        q = q[(q >= -1e-6).all(1) & (q <= 1 + 1e-6).all(1)]
                        if not len(q):
                            continue
                        half = max(halfs[lab] for lab in labs)
                        dd = np.min([np.abs(G.seg_dist(q * S, gpt[lab]) - halfs[lab]) for lab in labs], axis=0)
                        ends = np.min([np.linalg.norm(q * S - e, axis=1) for lab in labs for e in (gpt[lab][0], gpt[lab][-1])],
                                      axis=0)
                        dd = dd[ends > 2 * half + 0.5]
                        fwd.append(dd / S.min() * 100)
                        for lab in labs:
                            owned[lab].append(q * S)
                        continue
                    for q in G.clip_polyline(uv, -1e-6, 1 + 1e-6):  # PDF coords carry ~1e-17 noise on the frame
                        # forward: dense samples along the path; coverage: the exact path (resampling
                        # would cut corners such as an MLCC resonance tip)
                        qs = np.vstack([G.resample_arclength(q, per_unit=2000, min_n=2), q])
                        fwd.append(np.min([G.seg_dist(qs, gt[lab]) for lab in labs], axis=0) * 100)
                        for lab in labs:
                            owned[lab].append(q)
            for lab, g in gt.items():
                if not owned.get(lab):
                    cov.append(np.array([np.inf]))
                elif filled:
                    gp = G.resample_arclength(gpt[lab], per_unit=4, min_n=2)
                    half = halfs[lab]
                    # one-sided: a GT point must lie inside the filled stroke (inner side of a tight
                    # bend is closer than w/2 to the outline, which is fine)
                    dd = np.maximum(np.min([G.seg_dist(gp, q) for q in owned[lab]], axis=0) - half, 0.0)
                    ends = np.minimum(np.linalg.norm(gp - gp[0], axis=1), np.linalg.norm(gp - gp[-1], axis=1))
                    cov.append(dd[ends > 2 * half + 0.5] / S.min() * 100)
                else:
                    cov.append(np.min([G.seg_dist(g, q) for q in owned[lab]], axis=0) * 100)
            if not fwd or not sum(len(v) for v in fwd) or not sum(len(v) for v in cov):
                r.update(status="unevaluable", why="no probe-coloured curve path found")
            else:
                f = np.concatenate(fwd)
                cv = np.concatenate(cov)
                r.update(n_paths=n_paths, fwd_p95=float(np.percentile(f, 95)), fwd_max=float(f.max()),
                         cov_p95=float(np.percentile(cv, 95)), cov_max=float(cv.max()))
                ok = same and max(r["fwd_p95"], r["cov_p95"], r["fwd_max"], r["cov_max"]) <= VEC_TOL
                r["status"] = "pass" if ok else "FAIL"
            out.append(r)
    return out


# ----------------------------------------------------------------------------- raster
def _load(root, c):
    return np.asarray(Image.open(root / c["image"]).convert("RGB")).astype(np.float32)


def _bilinear(img, x, y):
    h, w = img.shape[:2]
    x = np.clip(x - 0.5, 0, w - 1.001)
    y = np.clip(y - 0.5, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x0 + 1] * fx * (1 - fy) + img[y0 + 1, x0] * (1 - fx) * fy
            + img[y0 + 1, x0 + 1] * fx * fy)


def _samples(p, step=0.5):
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    s = np.r_[0, np.cumsum(seg)]
    if s[-1] == 0:
        return p[:1], np.zeros((1, 2))
    t = np.arange(0, s[-1], step)
    q = np.c_[np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])]
    d = np.gradient(q, axis=0)
    n = np.c_[-d[:, 1], d[:, 0]]
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    return q, n


def raster_check(root: Path, cases, gate_only_clean=True):
    out = []
    for c in cases:
        f = c["factors"]
        r = {"id": c["id"], "cls": c["class"], "tier": c["tier"], "dpi": f["dpi"], "degraded": bool(f["degrade"]),
             "embedded": bool(f["raster_embed"])}
        img = _load(root, c)
        dark = 255 - img.min(2)
        x0, y0, x1, y1 = c["plot_box_px"]
        pcs = {str(k["label"]): gt_px(c, k) for k in c["curves"]}
        dashed_labels = _dashed(c)
        fr, offs, abs_offs, per = [], [], [], {}
        for lab, p in pcs.items():
            q, n = _samples(p)
            ink = np.zeros(len(q), bool)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    xx = np.clip((q[:, 0] + dx).astype(int), 0, img.shape[1] - 1)
                    yy = np.clip((q[:, 1] + dy).astype(int), 0, img.shape[0] - 1)
                    ink |= dark[yy, xx] > 25
            # centre-line offset: samples away from other curves and the frame
            others = [pp for ll, pp in pcs.items() if ll != lab]
            far = np.ones(len(q), bool)
            for pp in others:
                far &= G.seg_dist(q, pp) > 5
            far &= (q[:, 0] > x0 + 4) & (q[:, 0] < x1 - 4) & (q[:, 1] > y0 + 4) & (q[:, 1] < y1 - 4)
            col = _curve_rgb(c, lab)
            t = np.arange(-4, 4.01, 0.25)
            o = []
            if far.sum() >= 10 and col is not None:
                qs, ns = q[far][::2], n[far][::2]
                P = qs[:, None, :] + t[None, :, None] * ns[:, None, :]
                pix = _bilinear(img, P[..., 0], P[..., 1])
                wv = np.array([255.0, 255, 255]) - col
                alpha = np.clip(((255 - pix) * wv).sum(-1) / max((wv ** 2).sum(), 1.0), 0, 1)
                alpha = np.clip(alpha - np.median(alpha, axis=1, keepdims=True), 0, None)
                sw = alpha.sum(1)
                good = sw > 0.3
                o = ((alpha * t).sum(1) / np.maximum(sw, 1e-9))[good]
            per[lab] = {"ink_frac": float(ink.mean()), "dashed": lab in dashed_labels,
                        "offset_med": float(np.median(o)) if len(o) else None, "n_off": int(len(o))}
            if lab not in dashed_labels:
                fr.append(ink.mean())
            if len(o) >= 10 and lab not in dashed_labels:
                offs.append(float(np.median(o)))
                abs_offs.append(float(np.median(np.abs(o))))
        r["frame_offsets_px"] = _frame_offsets(img, c, pcs)
        fo = [abs(v) for v in r["frame_offsets_px"].values() if v is not None]
        r["frame_offset_max_abs"] = max(fo) if fo else None
        r["per_curve"] = per
        r["ink_frac_min_solid"] = float(min(fr)) if fr else None
        r["offset_med_max_abs"] = float(max(abs(v) for v in offs)) if offs else None
        clean = not r["degraded"] and not r["embedded"]
        r["gated"] = clean or not gate_only_clean
        if not r["gated"]:
            r["status"] = "report"
        elif r["ink_frac_min_solid"] is None and r["offset_med_max_abs"] is None:
            r["status"] = "unevaluable"
        else:
            ok = (r["ink_frac_min_solid"] is None or r["ink_frac_min_solid"] >= INK_MIN) and \
                 (r["offset_med_max_abs"] is None or r["offset_med_max_abs"] < OFFSET_MAX) and \
                 (r["frame_offset_max_abs"] is None or r["frame_offset_max_abs"] < FRAME_MAX)
            r["status"] = "pass" if ok else "FAIL"
            if r["offset_med_max_abs"] is None:
                r["status"] += "(ink-only)"
        out.append(r)
    return out


def _frame_offsets(img, c, pcs=None):
    """Measured frame-line position minus plot_box_px, per visible spine (centroid of the dark
    profile across the spine, median over the middle 60 % of its length)."""
    x0, y0, x1, y1 = c["plot_box_px"]
    dark = 255.0 - img.min(2)
    t = np.arange(-3, 3.01, 0.25)
    sides = {"left": (x0, "v"), "bottom": (y1, "h")}
    if c["factors"]["frame"] != "open":
        sides.update({"right": (x1, "v"), "top": (y0, "h")})
    out = {}
    for name, (pos, o) in sides.items():
        if o == "v":
            along = np.linspace(y0 + 0.2 * (y1 - y0), y1 - 0.2 * (y1 - y0), 60)
            P = _bilinear(dark[..., None], pos + t[None, :], along[:, None])[..., 0]
        else:
            along = np.linspace(x0 + 0.2 * (x1 - x0), x1 - 0.2 * (x1 - x0), 60)
            P = _bilinear(dark[..., None], along[:, None], pos + t[None, :])[..., 0]
        if pcs:  # a curve running along this spine (0 A rail) makes the side unevaluable
            allp = np.vstack(list(pcs.values()))
            near = np.abs(allp[:, 0 if o == "v" else 1] - pos) < 4
            span = along.max() - along.min()
            inside = (allp[:, 1 if o == "v" else 0] >= along.min()) & (allp[:, 1 if o == "v" else 0] <= along.max())
            q = allp[near & inside][:, 1 if o == "v" else 0]
            if len(q) and np.ptp(q) > 0.2 * span:
                out[name] = None
                continue
        prof = np.median(P, axis=0)  # median over the spine length: crossing curves / labels drop out
        k = int(np.argmax(prof))
        if prof[k] - prof.min() < 30 or k in (0, len(t) - 1):
            out[name] = None
            continue
        half = prof.min() + 0.5 * (prof[k] - prof.min())
        lo, hi = k, k
        while lo > 0 and prof[lo - 1] > half:
            lo -= 1
        while hi < len(t) - 1 and prof[hi + 1] > half:
            hi += 1
        w = prof[lo:hi + 1] - half  # centroid of the main peak only (a nearby gridline is a separate peak)
        out[name] = float((w * t[lo:hi + 1]).sum() / w.sum())
    return out


def _dashed(c):
    return {lab for lab, st in (c["gt_provenance"].get("curve_styles") or {}).items() if st.get("ls") != "solid"}


def _curve_rgb(c, lab):
    st = (c["gt_provenance"].get("curve_styles") or {}).get(lab)
    if not st:
        return None
    h = st["color"].lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float)


# ----------------------------------------------------------------------------- ticks / in-frame
def tick_check(root: Path, cases):
    out = []
    for c in cases:
        img = _load(root, c)
        H, W = img.shape[:2]
        dark = 255 - img.min(2)
        A = (c["gt_provenance"].get("degradation") or {}).get("affine")
        bad, empty, n = [], [], 0
        for k, boxes in c["tick_label_boxes_px"].items():
            for b in boxes:
                n += 1
                cr = np.array([[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]])
                if A:
                    cr = R.apply_affine(A, cr)
                if cr[:, 0].min() < 1 or cr[:, 1].min() < 1 or cr[:, 0].max() > W - 1 or cr[:, 1].max() > H - 1:
                    bad.append((k, [round(v, 1) for v in b]))
                    continue
                xa, xb = int(cr[:, 0].min()), int(np.ceil(cr[:, 0].max()))
                ya, yb = int(cr[:, 1].min()), int(np.ceil(cr[:, 1].max()))
                if dark[ya:yb, xa:xb].max(initial=0) < 40:
                    empty.append((k, [round(v, 1) for v in b]))
        status = "unevaluable" if n == 0 else ("pass" if not bad and not empty else "FAIL")
        out.append({"id": c["id"], "cls": c["class"], "n_boxes": n, "cut": bad, "no_ink": empty, "status": status})
    return out


def inframe_check(cases, tol=1e-9):
    out = []
    for c in cases:
        worst = 0.0
        for k in c["curves"]:
            u = to_norm(c["axis"]["x"], k["x"])
            v = to_norm(c["axis"]["y"], k["y"])
            uv = np.c_[u, v]
            if not np.isfinite(uv).all():
                worst = np.inf
                break
            worst = max(worst, float(np.max(np.maximum(-uv, uv - 1))))
        out.append({"id": c["id"], "cls": c["class"], "max_outside": worst,
                    "status": "pass" if worst <= tol else "FAIL"})
    return out


def summarise(name, recs):
    st = defaultdict(int)
    for r in recs:
        st[r["status"]] += 1
    return f"{name}: " + ", ".join(f"{k}={v}" for k, v in sorted(st.items()))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--only", default="vector,raster,ticks,inframe")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    cases = json.loads((a.root / "cases.json").read_text())
    res = {}
    todo = a.only.split(",")
    if "inframe" in todo:
        res["inframe"] = inframe_check(cases)
    if "ticks" in todo:
        res["ticks"] = tick_check(a.root, cases)
    if "vector" in todo:
        res["vector"] = vector_check(a.root, cases)
    if "raster" in todo:
        res["raster"] = raster_check(a.root, cases)
    outp = a.out or (a.root / "validation.json")
    outp.write_text(json.dumps(res, indent=0, default=float))
    for k, v in res.items():
        print(summarise(k, v))
    print("->", outp)


if __name__ == "__main__":
    main()
