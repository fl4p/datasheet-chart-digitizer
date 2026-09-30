"""Exact ground-truth geometry: resampling, frame clipping, decimation, difficulty statistics.

All geometry here is in *normalised frame coordinates* ``u = to_norm(axis, value)``, the unit
the scorer measures in (log10 first on log axes; ``u in [0, 1]`` is inside the frame).
"""
from __future__ import annotations

import numpy as np

from .axes import from_norm, to_norm


def norm_xy(spec, x, y):
    return np.c_[to_norm(spec["x"], x), to_norm(spec["y"], y)]


def denorm(spec, uv):
    return np.asarray(from_norm(spec["x"], uv[:, 0])), np.asarray(from_norm(spec["y"], uv[:, 1]))


def _clip_seg(p, q, lo, hi):
    """Liang-Barsky clip of segment p->q to the box [lo, hi]^2. Returns (t0, t1) or None."""
    t0, t1 = 0.0, 1.0
    d = q - p
    for k in range(2):
        for pk, qk in ((-d[k], p[k] - lo), (d[k], hi - p[k])):
            if pk == 0:
                if qk < 0:
                    return None
            else:
                t = qk / pk
                if pk < 0:
                    t0 = max(t0, t)
                else:
                    t1 = min(t1, t)
    return (t0, t1) if t0 <= t1 else None


def clip_polyline(uv, lo=0.0, hi=1.0):
    """Exact clip of a polyline to a box; returns the list of visible pieces (each (n, 2))."""
    uv = np.asarray(uv, float)
    ok = np.isfinite(uv).all(1)
    pieces, cur = [], []
    for i in range(len(uv) - 1):
        if not (ok[i] and ok[i + 1]):
            if len(cur) > 1:
                pieces.append(np.array(cur))
            cur = []
            continue
        p, q = uv[i], uv[i + 1]
        r = _clip_seg(p, q, lo, hi)
        if r is None:
            if len(cur) > 1:
                pieces.append(np.array(cur))
            cur = []
            continue
        a, b = p + r[0] * (q - p), p + r[1] * (q - p)
        if not cur:
            cur = [a]
        elif np.abs(cur[-1] - a).max() > 1e-12:
            if len(cur) > 1:
                pieces.append(np.array(cur))
            cur = [a]
        cur.append(b)
        if r[1] < 1.0:  # leaves the box inside this segment
            pieces.append(np.array(cur))
            cur = []
    if len(cur) > 1:
        pieces.append(np.array(cur))
    return [pc for pc in pieces if len(pc) > 1 and np.linalg.norm(np.diff(pc, axis=0), axis=1).sum() > 1e-9]


def resample_arclength(uv, per_unit=450, min_n=60):
    """Arc-length resample (in normalised space) of the finite part of a dense polyline."""
    uv = uv[np.isfinite(uv).all(1)]
    seg = np.linalg.norm(np.diff(uv, axis=0), axis=1)
    s = np.r_[0, np.cumsum(seg)]
    if s[-1] <= 0:
        return uv[:1]
    n = max(min_n, int(s[-1] * per_unit))
    t = np.linspace(0, s[-1], n)
    keep = np.r_[True, seg > 0]
    s, uvk = s[keep], uv[keep]
    return np.c_[np.interp(t, s, uvk[:, 0]), np.interp(t, s, uvk[:, 1])]


def prepare_drawn(uv_dense, margin=0.08):
    """The polyline that is actually drawn: dense model curve, restricted to a margin box
    (exact clip, so everything inside the frame is unchanged), then arc-length resampled."""
    pieces = clip_polyline(uv_dense, -margin, 1 + margin)
    if not pieces:
        return []
    return [resample_arclength(pc) for pc in pieces]


def douglas_peucker(uv, eps=2e-6):
    """Decimate a polyline; every dropped vertex is within eps of the kept polyline."""
    n = len(uv)
    if n < 3:
        return uv
    keep = np.zeros(n, bool)
    keep[[0, -1]] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = uv[a], uv[b]
        d = q - p
        L = float(d @ d)
        seg = uv[a + 1:b]
        if L == 0:
            dist = np.linalg.norm(seg - p, axis=1)
        else:
            t = np.clip(((seg - p) @ d) / L, 0, 1)
            dist = np.linalg.norm(seg - (p + t[:, None] * d), axis=1)
        k = int(np.argmax(dist))
        if dist[k] > eps:
            m = a + 1 + k
            keep[m] = True
            stack += [(a, m), (m, b)]
    return uv[keep]


def seg_dist(p, poly):
    """Distance from points p (N,2) to polyline poly (M,2) -- same as score_cases.seg_dist."""
    if len(poly) == 1:
        return np.linalg.norm(p - poly[0], axis=1)
    out = np.empty(len(p))
    a, b = poly[:-1], poly[1:]
    ab = b - a
    L = (ab ** 2).sum(1)
    L[L == 0] = 1e-12
    for i0 in range(0, len(p), 2048):
        pp = p[i0:i0 + 2048]
        ap = pp[:, None, :] - a[None]
        t = np.clip((ap * ab[None]).sum(2) / L[None], 0, 1)
        out[i0:i0 + 2048] = np.linalg.norm(ap - t[..., None] * ab[None], axis=2).min(1)
    return out


def _intersections(A, B):
    """Proper intersections of two polylines: list of (point, angle_deg)."""
    out = []
    a0, a1 = A[:-1], A[1:]
    b0, b1 = B[:-1], B[1:]
    # bbox prefilter
    axl, axh = np.minimum(a0[:, 0], a1[:, 0]), np.maximum(a0[:, 0], a1[:, 0])
    ayl, ayh = np.minimum(a0[:, 1], a1[:, 1]), np.maximum(a0[:, 1], a1[:, 1])
    for j in range(len(b0)):
        p, q = b0[j], b1[j]
        m = (axl <= max(p[0], q[0])) & (axh >= min(p[0], q[0])) & (ayl <= max(p[1], q[1])) & (ayh >= min(p[1], q[1]))
        for i in np.nonzero(m)[0]:
            r, s = a1[i] - a0[i], q - p
            den = r[0] * s[1] - r[1] * s[0]
            if abs(den) < 1e-15:
                continue
            w = p - a0[i]
            t = (w[0] * s[1] - w[1] * s[0]) / den
            u = (w[0] * r[1] - w[1] * r[0]) / den
            if 0 <= t < 1 and 0 <= u < 1:
                ang = abs(np.degrees(np.arctan2(abs(den), float(r @ s))))
                out.append((a0[i] + t * r, min(ang, 180 - ang)))
    return out


def difficulty_stats(gt_uv: dict, merge_tol=0.004):
    """Crossings, crossing angles, closest approach between curves (normalised units)."""
    labs = list(gt_uv)
    n_cross, angles, min_sep, merged_frac = 0, [], np.inf, 0.0
    for i in range(len(labs)):
        for j in range(i + 1, len(labs)):
            A, B = gt_uv[labs[i]], gt_uv[labs[j]]
            if len(A) < 2 or len(B) < 2:
                continue
            xs = _intersections(A, B)
            n_cross += len(xs)
            angles += [a for _, a in xs]
            dab = seg_dist(A, B)
            if not xs:
                min_sep = min(min_sep, float(dab.min()))
            merged_frac = max(merged_frac, float((dab < merge_tol).mean()))
    return {
        "n_crossings": int(n_cross),
        "min_crossing_angle_deg": float(min(angles)) if angles else None,
        "min_separation_norm": None if not np.isfinite(min_sep) else float(min_sep),
        "max_merged_fraction": merged_frac,  # share of a curve within 0.4 % span of another
    }
