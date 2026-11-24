"""Vector MLCC characteristic charts (Taiyo Yuden "Electrical Characteristics Data").

TY's per-part Characteristics PDF is one A4 page with up to five Excel charts:
"Capacitance Freq.", "DC Bias Char.", "Impedance,ESR Freq.", "Capacitance Temp."
and "Ripple Current". Two print paths exist and both are vector:

* **Excel direct** (Creator "Microsoft Excel 2016/2021"): gridlines and the frame
  are stroked lines/rects; each curve is ONE stroked path. Smoothed series are
  Bezier chains whose segment end points are the plotted samples; line-with-marker
  series (ripple) are polylines through the samples.
* **PostScript driver** (Creator "PScript5.dll" + Acrobat Distiller): every
  stroke was converted to a filled outline at 600 dpi (0.12 pt quantisation),
  gridlines are thin filled rects, and the page is painted in horizontal bands,
  so a fill or a rule may be split into pieces. A curve is a closed outline:
  one side, a round cap, the other side back, a round cap. The served curve is
  the centre line between the two sides.

Calibration is served from the rules the labels name, never from glyph centres:
each tick label is bound to the nearest full-span gridline (unique within a
tolerance), the axis map (linear or log10) is fitted to the gridline
positions, and every other full-span gridline is then checked against the
label lattice (log: major rules on decades, minor rules on 2..9 x decade;
linear: rules on multiples of the label step or its half). A failed or
unevaluable check makes the chart ``unverified`` -- never ``ok``.
Tick labels typeset as 10 + superscript exponent are read as powers of ten from
span geometry; a numeric label of mixed glyph sizes that is no such power
refuses its axis.

Curve identity: single-series charts (C-f, DC bias, temperature) must carry
exactly one curve colour; multi-series charts (|Z|/ESR, ripple) are bound by
legend swatch colour, one-to-one, or the chart is ``unverified``.

Outputs (per chart): status, reasons, axes (labels, rules, fit residual, grid
check), curves in SI with the point basis, class checks (DC bias at 0 V = 0 %,
temperature at 25 degC = 0 %), and a QA overlay drawn from the served SI values
mapped back through the calibration (so the overlay tests the mapping too).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import __version__
from .axis_calibration import power_tick_words

# --------------------------------------------------------------------------- #
# chart classes and units
# --------------------------------------------------------------------------- #

# normalised title -> (class, default curve label for single-series charts,
#                      x quantity, y quantity)
CHART_CLASSES = (
    ("capacitancefreq", "c_vs_f", "C", "frequency", "capacitance"),
    ("impedance", "impedance_esr_vs_f", None, "frequency", "impedance"),
    ("dcbias", "dc_bias", "dC/C", "dc_voltage", "capacitance_change"),
    ("capacitancetemp", "temperature", "dC/C", "temperature", "capacitance_change"),
    ("ripplecurrent", "ripple_temperature_rise", None, "ripple_current", "temperature_rise"),
    ("acvoltage", "ac_voltage", "dC/C", "ac_voltage", "capacitance_change"),
)

# unit text (as printed between brackets, lower-cased) -> (SI unit, factor)
UNITS = {
    "mhz": ("Hz", 1e6), "khz": ("Hz", 1e3), "hz": ("Hz", 1.0), "ghz": ("Hz", 1e9),
    "uf": ("F", 1e-6), "µf": ("F", 1e-6), "μf": ("F", 1e-6), "nf": ("F", 1e-9),
    "pf": ("F", 1e-12), "f": ("F", 1.0),
    "ohm": ("ohm", 1.0), "Ω": ("ohm", 1.0), "mohm": ("ohm", 1e-3),
    "%": ("%", 1.0),
    "v": ("V", 1.0), "vdc": ("V", 1.0), "vrms": ("Vrms", 1.0), "mvrms": ("Vrms", 1e-3),
    "a": ("A", 1.0), "arms": ("Arms", 1.0), "marms": ("Arms", 1e-3),
    "degreec": ("degC", 1.0), "degc": ("degC", 1.0), "°c": ("degC", 1.0), "℃": ("degC", 1.0),
    "nh": ("H", 1e-9),
}

LABEL_TO_LINE_TOL_PT = 1.5      # a tick label's centre may sit this far from its rule
                                # (measured on 255 TY charts: <= 0.81 pt)
FIT_RESID_MAX_PT = 0.50         # older Excel prints snap rules (measured <= 0.34 pt); a
                                # one-minor-rule mis-binding on a log axis is >= 0.9 pt
GRID_PT_TOL = 1.00              # every full-span rule must sit this close to its lattice value,
GRID_PT_TYP = 0.50              # and >= GRID_TYP_FRAC of them this close. Excel/GDI prints snap
GRID_TYP_FRAC = 0.60            # rules to a ~0.63 pt layout grid: measured worst 0.63 pt (minor
                                # 2x rule, HMK325BJ225KM-T C-f: 68 % within 0.5 pt), labelled rules fit <= 0.44 pt
FRAME_CLIP_PT = 0.5             # points this far outside the frame are still "on" it
                                # (rules are snapped by up to ~0.45 pt on older prints)
INK_PAST_FRAME_PT = 2.5         # visible (unclipped) curve ink may reach this far past the
                                # detected frame. The page's own clip can sit ~1.3 pt outside
                                # the frame (EMK325AC6476MM-T temperature: clip 255.24 pt, frame
                                # 253.98 pt; ink measured 1.59 pt past), plus the 0.5 pt scissor
                                # tolerance and a 0.6 pt stroke's round cap. Corpus worst
                                # (51 TY PDFs, 2026-10-17): 1.59 pt. Further out the page draws
                                # the curve where the detected plot is not -- the frame edge is
                                # wrong (JMK325BJ476MM-T DC bias: the 6 V gridline was taken
                                # for the 6.3 V edge, visible ink ran ~8 pt on) -- and the
                                # chart is not ok: its served curve would be silently truncated.


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #

def _fitz():
    import fitz  # PyMuPDF

    return fitz


def _bezier(p0, p1, p2, p3, n=16):
    t = np.linspace(0.0, 1.0, n + 1)[:, None]
    P = [np.array([p.x, p.y], float) for p in (p0, p1, p2, p3)]
    return ((1 - t) ** 3) * P[0] + 3 * ((1 - t) ** 2) * t * P[1] + 3 * (1 - t) * t * t * P[2] + t ** 3 * P[3]


def _colour(c):
    return None if c is None else tuple(round(float(v), 3) for v in c[:3])


def _chromatic(c, min_spread=0.25) -> bool:
    return c is not None and max(c) - min(c) > min_spread


def _darkness(c) -> float:
    return 1.0 - (sum(c) / 3.0 if c else 0.0)


def _col_dist(a, b) -> float:
    return float(np.abs(np.asarray(a) - np.asarray(b)).max())


@dataclass
class Rule:
    orient: str          # "h" (constant y) or "v" (constant x)
    pos: float
    lo: float
    hi: float
    colour: tuple
    width: float

    @property
    def length(self) -> float:
        return self.hi - self.lo


@dataclass
class Shape:
    """A non-rule vector element: a curve candidate, a legend swatch or a marker."""

    kind: str                      # "stroke" | "outline"
    colour: tuple
    pts: np.ndarray                # drawn polyline (sampled), page pt
    nodes: np.ndarray              # plotted samples (stroke: segment ends) or centreline
    bbox: tuple
    n_items: int
    note: str = ""
    key: tuple = ()
    full_pts: np.ndarray | None = None
    full_nodes: np.ndarray | None = None
    mask_pts: np.ndarray | None = None
    mask_nodes: np.ndarray | None = None


def _rules_and_shapes(page):
    """Split the page's vector drawing into axis-aligned rules and other shapes."""
    rules: list[Rule] = []
    shapes: list[Shape] = []
    small_fills = []
    ring_rules: list[Rule] = []
    stack: list[tuple[int, object]] = []          # (level, scissor rect or None)
    for dr in page.get_drawings(extended=True):
        typ = dr.get("type") or ""
        level = int(dr.get("level") or 0)
        while stack and stack[-1][0] >= level:
            stack.pop()
        if typ in ("clip", "group"):
            stack.append((level, dr.get("scissor") if typ == "clip" else None))
            continue
        scissor = None
        for _, sc in stack:
            if sc is not None:
                scissor = sc if scissor is None else (scissor & sc)
        items = dr.get("items") or []
        stroke = _colour(dr.get("color"))
        fill = _colour(dr.get("fill"))
        width = float(dr.get("width") or 0.0)
        r = dr["rect"]
        # filled thin rects -> rules (PostScript path); stroked 're' -> four rules
        if all(it[0] == "re" for it in items) and items:
            for it in items:
                q = it[1]
                if "f" in typ and fill is not None and (dr.get("fill_opacity", 1) or 1) > 0:
                    if q.height <= 1.3 and q.width > 3:
                        rules.append(Rule("h", (q.y0 + q.y1) / 2, q.x0, q.x1, fill, q.height))
                    elif q.width <= 1.3 and q.height > 3:
                        rules.append(Rule("v", (q.x0 + q.x1) / 2, q.y0, q.y1, fill, q.width))
                    elif max(q.width, q.height) < 8:
                        small_fills.append((fill, q))
                if "s" in typ and stroke is not None and q.width > 3 and q.height > 3:
                    rules += [Rule("h", q.y0, q.x0, q.x1, stroke, width), Rule("h", q.y1, q.x0, q.x1, stroke, width),
                              Rule("v", q.x0, q.y0, q.y1, stroke, width), Rule("v", q.x1, q.y0, q.y1, stroke, width)]
            continue
        if max(r.width, r.height) < 8 and ("f" in typ or "s" in typ) and (fill or stroke):
            small_fills.append((fill or stroke, r))      # a marker
            continue
        if "s" in typ and stroke is not None:
            # a stroked path: axis-aligned straight pieces longer than 3 pt are rules
            # only when EVERY item is one (gridline batches); anything else is a shape
            straight = []
            for it in items:
                if it[0] != "l":
                    straight = None
                    break
                a, b = it[1], it[2]
                if abs(a.y - b.y) < 0.05:
                    straight.append(Rule("h", (a.y + b.y) / 2, min(a.x, b.x), max(a.x, b.x), stroke, width))
                elif abs(a.x - b.x) < 0.05:
                    straight.append(Rule("v", (a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y), stroke, width))
                elif (not _chromatic(stroke) and len(items) == 1
                      and min(abs(a.x - b.x), abs(a.y - b.y)) <= 0.01 * max(abs(a.x - b.x), abs(a.y - b.y))):
                    # an axis line drawn a hair off true (0.7 pt over 120 pt seen,
                    # TMR325B7106KM-T): neither a rule (it is not where the frame
                    # is) nor a curve
                    straight = []
                    break
                else:
                    straight = None
                    break
            if straight is not None and not _chromatic(stroke):
                # gridline batches and axis tick marks; ticks (<= 3 pt) are not rules.
                # A lone short horizontal stroke may be a black legend swatch: keep
                # it as a shape too (the legend binder needs a label right of it).
                rules += [q for q in straight if q.length > 3]
                if len(straight) == 1 and straight[0].orient == "h" and 8 <= straight[0].length <= 30:
                    q = straight[0]
                    P = np.array([[q.lo, q.pos], [q.hi, q.pos]])
                    shp = _scissored(Shape("stroke", stroke, P, P, (q.lo, q.pos, q.hi, q.pos), 1), scissor, r)
                    if shp is not None:
                        shapes.append(shp)
                continue
            pts, nodes = [], []
            for it in items:
                if it[0] == "l":
                    seg = np.array([[it[1].x, it[1].y], [it[2].x, it[2].y]])
                elif it[0] == "c":
                    seg = _bezier(it[1], it[2], it[3], it[4])
                else:
                    continue
                if nodes and np.hypot(*(np.asarray(nodes[-1]) - seg[0])) < 1e-6:
                    pts.extend(seg[1:].tolist())
                else:
                    pts.extend(seg.tolist())
                    nodes.append(seg[0].tolist())
                nodes.append(seg[-1].tolist())
            if len(pts) >= 2:
                shp = _scissored(Shape("stroke", stroke, np.asarray(pts), np.asarray(nodes),
                                       (r.x0, r.y0, r.x1, r.y1), len(items)), scissor, r)
                if shp is not None:
                    shapes.append(shp)
            continue
        if "f" in typ and fill is not None and items:
            if max(r.width, r.height) < 8:
                small_fills.append((fill, r))
                continue
            lines_ = [it for it in items if it[0] == "l" and abs(it[2] - it[1]) > 1.0]   # corner chamfers aside
            if (lines_ and all(abs(it[1].x - it[2].x) < 0.05 or abs(it[1].y - it[2].y) < 0.05 for it in lines_)
                    and any(abs(it[1].x - it[2].x) > 10 for it in lines_)
                    and any(abs(it[1].y - it[2].y) > 10 for it in lines_)):
                # a stroked rectangle (frame, legend box) printed as a filled ring.
                # Not a curve -- but its sides ARE rules: on PScript prints the
                # frame may exist only as this ring (JMK325BJ476MM-T DC bias: the
                # 6.3 V right edge is drawn nowhere else, and without it the
                # 6 V gridline became the panel's right edge).
                ring_rules += _ring_sides(lines_, r, fill)
                continue
            shp = _scissored(_outline_shape(items, fill, r), scissor, r)
            if shp is not None:
                shapes.append(shp)
    # image strips: some PostScript sheets draw rules as sub-point images
    for im in page.get_image_info():
        q = _fitz().Rect(im["bbox"])
        if q.width < 1.0 and q.height > 20:
            rules.append(Rule("v", (q.x0 + q.x1) / 2, q.y0, q.y1, (0.5, 0.5, 0.5), q.width))
        elif q.height < 1.0 and q.width > 20:
            rules.append(Rule("h", (q.y0 + q.y1) / 2, q.x0, q.x1, (0.5, 0.5, 0.5), q.height))
    # a ring side only where no ordinary rule already draws that line: where one
    # does (a gridline on the frame edge), the ordinary rule keeps deciding, so
    # pages that never needed the ring calibrate exactly as before
    for q in ring_rules:
        if not any(r.orient == q.orient and abs(r.pos - q.pos) <= 1.0
                   and r.lo <= q.hi and r.hi >= q.lo for r in rules):
            rules.append(q)
    return _merge_rules(rules), _merge_repaints(shapes), small_fills


def _ring_sides(lines_, r, fill):
    """The four sides of a rectangle printed as a filled ring, as centre-line
    rules. A side drawn by two parallel long lines (outer and inner edge) sits
    midway between them; a side with only its outer edge drawn (the inner one
    can collapse into corner chamfers) sits half the median ring width inside
    the bounding box. Only sides >= 10 pt long are returned."""
    hs = sorted({round((it[1].y + it[2].y) / 2, 3) for it in lines_
                 if abs(it[1].y - it[2].y) < 0.05 and abs(it[1].x - it[2].x) > 10})
    vs = sorted({round((it[1].x + it[2].x) / 2, 3) for it in lines_
                 if abs(it[1].x - it[2].x) < 0.05 and abs(it[1].y - it[2].y) > 10})

    widths = []
    for vals, lo, hi in ((hs, r.y0, r.y1), (vs, r.x0, r.x1)):
        for edge in (lo, hi):
            near = [v for v in vals if abs(v - edge) <= 1.3]
            if len(near) >= 2:
                widths.append(max(near) - min(near))
    if not widths:
        return []
    t = float(np.median(widths))
    out = []
    for orient, vals, lo, hi, a, b in (("h", hs, r.y0, r.y1, r.x0, r.x1), ("v", vs, r.x0, r.x1, r.y0, r.y1)):
        if hi - lo < 10 or b - a < 10:
            continue
        for edge, sign in ((lo, +1), (hi, -1)):
            near = [v for v in vals if abs(v - edge) <= 1.3]
            pos = (max(near) + min(near)) / 2 if len(near) >= 2 else edge + sign * t / 2
            out.append(Rule(orient, pos, a + t / 2, b - t / 2, fill, t))
    return out


def _scissored(shp, scissor, raw_rect, tol=0.5):
    """Record what the page's clip lets through. A curve may run on under the clip
    into a neighbouring panel or past the axis range; band-painted pages paint
    one path several times under different band clips, so visibility is a mask
    over the full path, OR-ed across repaints (see _merge_repaints)."""
    if shp is None:
        return None
    shp.key = (shp.kind, shp.colour, shp.n_items, tuple(round(v, 1) for v in raw_rect))
    shp.full_pts, shp.full_nodes = shp.pts, shp.nodes
    if scissor is None:
        shp.mask_pts = np.ones(len(shp.pts), bool)
        shp.mask_nodes = np.ones(len(shp.nodes), bool)
        return shp

    def inside(P):
        return ((P[:, 0] >= scissor.x0 - tol) & (P[:, 0] <= scissor.x1 + tol)
                & (P[:, 1] >= scissor.y0 - tol) & (P[:, 1] <= scissor.y1 + tol))
    shp.mask_pts, shp.mask_nodes = inside(shp.pts), inside(shp.nodes)
    return shp


def _merge_repaints(shapes):
    merged: dict[tuple, Shape] = {}
    for s in shapes:
        m = merged.get(s.key)
        if m is None:
            merged[s.key] = s
            continue
        m.mask_pts = m.mask_pts | s.mask_pts
        m.mask_nodes = m.mask_nodes | s.mask_nodes
        m.note = (m.note + "; " if m.note else "") + "repainted (band clips OR-ed)"
    out = []
    for s in merged.values():
        if s.mask_pts.sum() < 2:
            continue
        hidden_p, hidden_n = int((~s.mask_pts).sum()), int((~s.mask_nodes).sum())
        if hidden_p:
            s.note = (s.note + "; " if s.note else "") + f"clip hides {hidden_p} drawn / {hidden_n} sample points"
        s.pts, s.nodes = s.full_pts[s.mask_pts], s.full_nodes[s.mask_nodes]
        P = s.pts
        s.bbox = (float(P[:, 0].min()), float(P[:, 1].min()), float(P[:, 0].max()), float(P[:, 1].max()))
        out.append(s)
    return out


def _merge_rules(rules):
    """Join collinear pieces (band-painted PostScript pages split a rule)."""
    out = []
    for orient in ("h", "v"):
        rs = sorted((r for r in rules if r.orient == orient), key=lambda r: (round(r.pos, 1), r.lo))
        cur = None
        for r in rs:
            if (cur is not None and abs(r.pos - cur.pos) < 0.15 and r.lo <= cur.hi + 0.6
                    and _col_dist(r.colour, cur.colour) < 0.05):
                cur.hi = max(cur.hi, r.hi)
                cur.lo = min(cur.lo, r.lo)
            else:
                if cur is not None:
                    out.append(cur)
                cur = Rule(r.orient, r.pos, r.lo, r.hi, r.colour, r.width)
        if cur is not None:
            out.append(cur)
    return out


def _outline_shape(items, fill, r):
    """Centre line of a filled stroke outline: side A, a cap, side B back, a cap.

    The outline is a closed loop (a fill closes implicitly). A cap is a run of
    curve items -- or one short straight item (butt/square caps) -- across which
    the outline reverses direction within <= 2.5 pt. Round joins are curve runs
    too, but the outline does not reverse across them. Exactly two caps or no
    shape (the caller then has no curve, which refuses the chart).
    """
    segs = []
    for it in items:
        if it[0] == "l":
            segs.append((np.array([[it[1].x, it[1].y], [it[2].x, it[2].y]]), False))
        elif it[0] == "c":
            segs.append((_bezier(it[1], it[2], it[3], it[4], n=4), True))
        else:
            return None
    m = len(segs)
    if m < 6:
        return None
    start, pts = [], []
    for seg, _ in segs:
        start.append(len(pts))
        pts.extend(seg[:-1].tolist())
    P = np.asarray(pts)
    n = len(P)
    if n < 8:
        return None
    # cap candidates, as (first item, last item) runs, cyclic
    curve = [c for _, c in segs]
    runs, i = [], 0
    while i < m:
        if curve[i]:
            j = i
            while j + 1 < m and curve[j + 1]:
                j += 1
            runs.append([i, j])
            i = j + 1
        else:
            seg = segs[i][0]
            if np.hypot(*(seg[-1] - seg[0])) <= 2.0:
                runs.append([i, i])
            i += 1
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == m - 1 and curve[0] and curve[-1]:
        runs[0] = [runs[-1][0], runs[0][1] + m]      # wraps
        runs.pop()

    def direction(at, sign):
        k, acc, d = at, 0.0, np.zeros(2)
        for _ in range(n):
            k2 = (k + sign) % n
            step = (P[k2] - P[k]) if sign > 0 else (P[k] - P[k2])
            d += step
            acc += float(np.hypot(*step))
            k = k2
            if acc >= 1.5:
                break
        nrm = float(np.hypot(*d))
        return d / nrm if nrm > 0 else None

    caps = []
    for a_, b_ in runs:
        p_in = start[a_ % m]
        p_out = start[(b_ + 1) % m]
        chord = float(np.hypot(*(P[p_out] - P[p_in])))
        din, dout = direction(p_in, -1), direction(p_out, +1)
        if din is None or dout is None:
            continue
        dot = float(din @ dout)
        if 0.2 <= chord <= 2.5 and dot < -0.85:
            caps.append((dot, p_in, p_out, (P[p_in] + P[p_out]) / 2))
    # a short straight item next to a cap reverses across it too: one cap per place
    kept = []
    for c in sorted(caps, key=lambda c: c[0]):
        if all(np.hypot(*(c[3] - k[3])) > 3.0 for k in kept):
            kept.append(c)
    if len(kept) < 2:
        return None

    def arc(i0, i1):
        idx = [i0]
        while idx[-1] != i1:
            idx.append((idx[-1] + 1) % n)
            if len(idx) > n:
                break
        return P[idx]

    def length(Q):
        return float(np.hypot(*np.diff(Q, axis=0).T).sum()) if len(Q) > 1 else 0.0

    # the two true caps split the outline into two sides of (nearly) equal length;
    # the inner side of a sharp bend can also reverse, so test every pair
    best = None
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            (_, a_in, a_out, _), (_, b_in, b_out, _) = sorted((kept[i], kept[j]), key=lambda c: c[1])
            A, B = arc(a_out, b_in), arc(b_out, a_in)[::-1]
            la, lb = length(A), length(B)
            mis = abs(la - lb) / max(la, lb, 1e-9)
            if best is None or mis < best[0]:
                best = (mis, A, B)
    if best is None or best[0] > 0.10:
        return None
    _, A, B = best
    return _centreline_shape(A, B, fill, r, len(items), caps=len(kept))


def _densify(P, step=0.25):
    out = [P[0]]
    for a, b in zip(P[:-1], P[1:]):
        L = float(np.hypot(*(b - a)))
        k = max(1, int(math.ceil(L / step)))
        for t in np.linspace(0, 1, k + 1)[1:]:
            out.append(a + t * (b - a))
    return np.asarray(out)


def _nearest_on_polyline(p, P):
    a, b = P[:-1], P[1:]
    ab = b - a
    L2 = (ab ** 2).sum(1)
    t = np.clip(((p - a) * ab).sum(1) / np.where(L2 > 0, L2, 1), 0, 1)
    q = a + t[:, None] * ab
    d = np.hypot(*(q - p).T)
    k = int(np.argmin(d))
    return q[k], float(d[k])


def _centreline_shape(A, B, fill, r, n_items, caps):
    if len(A) < 2 or len(B) < 2:
        return None
    Ad = _densify(A)
    mids, widths, dropped = [], [], 0
    for p in Ad:
        q, d = _nearest_on_polyline(p, B)
        if 0.2 <= d <= 2.5:
            mids.append((p + q) / 2)
            widths.append(d)
        else:
            dropped += 1
    if len(mids) < 2:
        return None
    M = np.asarray(mids)
    note = f"outline centreline: cap candidates={caps} stroke width median {np.median(widths):.2f} pt, {dropped} side points unpaired"
    return Shape("outline", fill, M, M, (r.x0, r.y0, r.x1, r.y1), n_items, note)


# --------------------------------------------------------------------------- #
# panels
# --------------------------------------------------------------------------- #

@dataclass
class Panel:
    frame: tuple                      # x0, y0, x1, y1 (page pt)
    hrules: list = field(default_factory=list)
    vrules: list = field(default_factory=list)


def find_panels(rules, min_side=60.0):
    """Plot frames: connected sets of long horizontal + vertical rules whose
    bounding box is closed by full-span rules on all four sides."""
    long_ = [r for r in rules if r.length >= min_side]
    H = [r for r in long_ if r.orient == "h"]
    V = [r for r in long_ if r.orient == "v"]
    parent = list(range(len(H) + len(V)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, h in enumerate(H):
        for j, v in enumerate(V):
            if h.lo - 1 <= v.pos <= h.hi + 1 and v.lo - 1 <= h.pos <= v.hi + 1:
                parent[find(i)] = find(len(H) + j)
    groups: dict[int, list] = {}
    for k in range(len(H) + len(V)):
        groups.setdefault(find(k), []).append(k)
    panels = []
    for ks in groups.values():
        hs = [H[k] for k in ks if k < len(H)]
        vs = [V[k - len(H)] for k in ks if k >= len(H)]
        if len(hs) < 2 or len(vs) < 2:
            continue
        x0, x1 = min(v.pos for v in vs), max(v.pos for v in vs)
        y0, y1 = min(h.pos for h in hs), max(h.pos for h in hs)
        if x1 - x0 < min_side or y1 - y0 < min_side:
            continue
        panels.append(Panel((x0, y0, x1, y1)))
    # keep each panel's full-span rules (any length class, inside the frame)
    for p in panels:
        x0, y0, x1, y1 = p.frame
        p.hrules = [r for r in rules if r.orient == "h" and y0 - 0.5 <= r.pos <= y1 + 0.5
                    and r.lo <= x0 + 1.5 and r.hi >= x1 - 1.5]
        p.vrules = [r for r in rules if r.orient == "v" and x0 - 0.5 <= r.pos <= x1 + 0.5
                    and r.lo <= y0 + 1.5 and r.hi >= y1 - 1.5]
    return sorted(panels, key=lambda p: (round(p.frame[1] / 20), p.frame[0]))


# --------------------------------------------------------------------------- #
# text
# --------------------------------------------------------------------------- #

_NUM = re.compile(r"^[-−–]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$")


def _num(s):
    s = s.strip().replace("−", "-").replace("–", "-")
    if not _NUM.match(s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class Word:
    text: str
    bbox: tuple

    @property
    def cx(self):
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self):
        return (self.bbox[1] + self.bbox[3]) / 2


def _split_glued_number(chars):
    """Excel prints touching log-axis labels without a space ("1000" + "10000"
    -> one word "100010000", TMR325B7106KM-T). Only a word made of two powers of
    ten is split, at the '1' whose glyph step from the previous digit deviates
    most (> 0.25 pt) from the word's median digit advance."""
    text = "".join(c["c"] for c in chars)
    if not re.fullmatch(r"10*10*", text) or len(text) < 3:
        return [chars]
    steps = [chars[i + 1]["bbox"][0] - chars[i]["bbox"][0] for i in range(len(chars) - 1)]
    med = float(np.median(steps))
    cand = [(abs(steps[i - 1] - med), i) for i in range(1, len(chars)) if text[i] == "1"]
    if not cand:
        return [chars]
    dev, i = max(cand)
    if dev <= 0.25:
        return [chars]
    return [chars[:i], chars[i:]]


def _words(page):
    """Page words (band-painted pages repeat text: kept once), with glued numeric
    labels split at their glyph-advance break (see _split_glued_number)."""
    chars = []
    for blk in page.get_text("rawdict")["blocks"]:
        for ln in blk.get("lines", []):
            for sp in ln["spans"]:
                chars += [c for c in sp["chars"] if not c["c"].isspace()]
    seen, out = set(), []
    for w in page.get_text("words"):
        parts = [(w[4], tuple(w[:4]))]
        if re.fullmatch(r"10*10*", w[4]) and len(w[4]) >= 3:
            inside = [c for c in chars
                      if w[0] - 0.2 <= (c["bbox"][0] + c["bbox"][2]) / 2 <= w[2] + 0.2
                      and w[1] - 0.5 <= (c["bbox"][1] + c["bbox"][3]) / 2 <= w[3] + 0.5]
            # one copy of the glyphs (band repaints duplicate them)
            uniq = []
            for c in sorted(inside, key=lambda c: c["bbox"][0]):
                if not uniq or abs(c["bbox"][0] - uniq[-1]["bbox"][0]) > 0.05 or c["c"] != uniq[-1]["c"]:
                    uniq.append(c)
            if "".join(c["c"] for c in uniq) == w[4]:
                groups = _split_glued_number(uniq)
                texts = ["".join(c["c"] for c in g) for g in groups]
                if len(groups) > 1 and all(_num(t) is not None for t in texts):
                    parts = [(t, (min(c["bbox"][0] for c in g), w[1], max(c["bbox"][2] for c in g), w[3]))
                             for t, g in zip(texts, groups)]
        for t, bb in parts:
            key = (t, round(bb[0]), round(bb[1]))
            if key in seen:
                continue
            seen.add(key)
            out.append(Word(t, bb))
    return out


def _power_words(page, words):
    """*words* with ``10``+superscript tick labels re-read as ``1e<exp>``
    (axis_calibration.power_tick_words), and the numeric words carrying a
    superscript or mixed glyph sizes that read as no such power (dropped)."""
    kept, unread = power_tick_words(page, [(*w.bbox, w.text) for w in words])
    out = [Word(f"1e{t[4][3:]}" if t[4].startswith("10^") else t[4], tuple(t[:4])) for t in kept]
    return out, [Word(t[4], tuple(t[:4])) for t in unread]


def _rotated_lines(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            if abs(ln["dir"][0]) < 0.1:
                out.append(("".join(s["text"] for s in ln["spans"]), tuple(ln["bbox"])))
    return out


def _unit_of(text):
    m = re.search(r"\[([^\]]+)\]", text)
    return m.group(1).strip() if m else None


def _si(unit_text):
    if unit_text is None:
        return None
    return UNITS.get(unit_text.strip().lower().replace(" ", ""))


# --------------------------------------------------------------------------- #
# calibration
# --------------------------------------------------------------------------- #

@dataclass
class AxisCal:
    name: str
    scale: str = ""
    a: float = 0.0          # pos = a + b * f(value)
    b: float = 0.0
    labels: list = field(default_factory=list)   # (value, label_centre, rule_pos)
    resid_pt: float = float("nan")
    grid: dict = field(default_factory=dict)
    status: str = "unverified"
    reasons: list = field(default_factory=list)

    def f(self, v):
        v = np.asarray(v, float)
        return np.log10(v) if self.scale == "log10" else v

    def to_pt(self, v):
        return self.a + self.b * self.f(v)

    def to_value(self, pt):
        u = (np.asarray(pt, float) - self.a) / self.b
        return 10.0 ** u if self.scale == "log10" else u


def calibrate_axis(name, labels, rule_pos, frame_lo, frame_hi):
    """labels: [(value, centre_pt)], rule_pos: [(pos, darkness)] full-span rules."""
    cal = AxisCal(name)
    if len(labels) < 3:
        cal.reasons.append(f"{len(labels)} numeric tick labels (need >= 3)")
        cal.status = "refused"
        return cal
    # labels name the major (darkest) rules; minor rules are lighter
    dmax = max((dk for _, dk in rule_pos), default=0.0)
    pos = np.array([p for p, dk in rule_pos if dk >= dmax - 0.15])
    if len(pos) == 0:
        cal.reasons.append("no full-span rules")
        cal.status = "refused"
        return cal
    bound = []
    for v, c in labels:
        d = np.abs(pos - c)
        k = np.argsort(d)
        if d[k[0]] > LABEL_TO_LINE_TOL_PT:
            cal.reasons.append(f"label {v:g} at {c:.2f} pt has no rule within {LABEL_TO_LINE_TOL_PT} pt")
            continue
        if len(k) > 1 and d[k[1]] - d[k[0]] < 1.0:
            cal.reasons.append(f"label {v:g}: two rules equally near ({d[k[0]]:.2f}/{d[k[1]]:.2f} pt)")
            continue
        bound.append((v, c, float(pos[k[0]])))
    if len(bound) < len(labels):
        # every printed tick label must name a rule: a label left over means the
        # row is bound off by a pitch (all labels shifted onto the next rules,
        # one falling off the end) or the row holds a stray number
        cal.reasons.append(f"{len(labels) - len(bound)} of {len(labels)} tick labels bound to no rule")
        cal.status = "refused"
        return cal
    if len({b[2] for b in bound}) != len(bound):
        cal.reasons.append("two labels bound to one rule")
        cal.status = "refused"
        return cal
    if len(bound) < 3:
        cal.reasons.append(f"only {len(bound)} labels bound to rules")
        cal.status = "refused"
        return cal
    vals = np.array([b[0] for b in bound])
    rp = np.array([b[2] for b in bound])
    fits = {}
    for scale in ("linear", "log10"):
        if scale == "log10" and (vals <= 0).any():
            continue
        u = np.log10(vals) if scale == "log10" else vals
        A = np.c_[np.ones_like(u), u]
        coef, *_ = np.linalg.lstsq(A, rp, rcond=None)
        res = float(np.abs(A @ coef - rp).max())
        fits[scale] = (coef, res)
    ok = {s: f for s, f in fits.items() if f[1] <= FIT_RESID_MAX_PT}
    if len(ok) != 1:
        cal.reasons.append("axis map ambiguous or not fitted: "
                           + ", ".join(f"{s} resid {f[1]:.3f} pt" for s, f in fits.items()))
        cal.status = "refused"
        return cal
    cal.scale, (coef, cal.resid_pt) = next(iter(ok.items()))
    cal.a, cal.b = float(coef[0]), float(coef[1])
    cal.labels = bound
    # grid lattice check over every full-span rule inside the frame
    lo, hi = sorted((frame_lo, frame_hi))
    # the frame edges are the axis min/max, which need not be on the lattice
    # (a 6.3 V part's DC-bias axis ends at 6.3); they are reported, not checked
    inner = [(p, dk) for p, dk in rule_pos if lo + 0.6 < p < hi - 0.6]
    bad, n, worst, errs = [], 0, 0.0, []
    dmax_all = max((dk for _, dk in inner), default=0.0)
    if cal.scale == "log10":
        for p, dk in inner:
            u = float((p - cal.a) / cal.b)
            dec = math.floor(u + 1e-9)
            # nearest k x 10^dec (k = 1..10) in pt
            err, k_best = min((abs(float(cal.to_pt(k * 10.0 ** dec)) - p), k) for k in range(1, 11))
            n += 1
            worst = max(worst, err)
            two_classes = any(d < dmax_all - 0.15 for _, d in inner)
            major = two_classes and dk >= dmax_all - 0.15
            errs.append(err)
            if err > GRID_PT_TOL or (major and k_best not in (1, 10)):
                bad.append(round(10 ** u, 6))
    else:
        steps = np.diff(np.sort(vals))
        step = float(np.min(steps[steps > 0])) if (steps > 0).any() else None
        for p, dk in inner:
            v = float((p - cal.a) / cal.b)
            n += 1
            if step is None:
                bad.append(v)
                continue
            q = round((v - vals.min()) / (step / 2))
            err = abs(float(cal.to_pt(vals.min() + q * step / 2)) - p)
            worst = max(worst, err)
            errs.append(err)
            if err > GRID_PT_TOL:
                bad.append(round(v, 6))
    frac_typ = float(np.mean(np.asarray(errs) <= GRID_PT_TYP)) if errs else 0.0
    cal.grid = dict(rules_checked=n, rules_off_lattice=bad, worst_rule_error_pt=round(worst, 4),
                    fraction_within_typ=round(frac_typ, 3))
    if n < len([b for b in bound if lo + 0.6 < b[2] < hi - 0.6]):
        cal.reasons.append("fewer inner full-span rules than inner bound labels")
    elif bad:
        cal.reasons.append(f"{len(bad)} rules off the label lattice by > {GRID_PT_TOL} pt: {bad[:5]}")
    elif frac_typ < GRID_TYP_FRAC:
        cal.reasons.append(f"only {frac_typ:.0%} of rules within {GRID_PT_TYP} pt of the label lattice")
    else:
        cal.status = "verified"
    # the frame edges must map inside the labelled range or onto a lattice point
    return cal


# --------------------------------------------------------------------------- #
# one chart
# --------------------------------------------------------------------------- #

def _classify(title):
    t = re.sub(r"[^a-z]", "", title.lower())
    for key, cls, single, xq, yq in CHART_CLASSES:
        if key in t:
            return cls, single, xq, yq
    return None, None, None, None


def _clip(P, frame, tol=FRAME_CLIP_PT):
    x0, y0, x1, y1 = frame
    m = (P[:, 0] >= x0 - tol) & (P[:, 0] <= x1 + tol) & (P[:, 1] >= y0 - tol) & (P[:, 1] <= y1 + tol)
    return m


def _legend(shapes_in, small_fills, words, frame):
    """colour -> label from swatches (short horizontal shapes) left of an in-plot word."""
    x0, y0, x1, y1 = frame
    inplot = [w for w in words if x0 < w.cx < x1 and y0 < w.cy < y1 and _num(w.text) is None]
    bind, swatches = {}, []
    for w in inplot:
        best = None
        for s in shapes_in:
            bx0, by0, bx1, by1 = s.bbox
            if bx1 - bx0 > 30 or by1 - by0 > 2.5:
                continue
            if abs((by0 + by1) / 2 - w.cy) < 3.0 and 0 <= w.bbox[0] - bx1 < 15:
                best = s
        if best is not None:
            if w.text in bind:
                return None, f"legend label {w.text!r} printed twice"
            bind[w.text] = best.colour
            swatches.append(best)
    return (bind, swatches), None


def _value_at(x, y, xq, scale_x="linear", reach=0.01):
    """Interpolate y at x on a curve given in SI (x ascending not assumed). A query
    up to `reach` x span outside the served range takes the end value (a sample
    on the frame edge maps a hair inside it); further out it is not evaluable."""
    order = np.argsort(x)
    xs, ys = np.asarray(x)[order], np.asarray(y)[order]
    if scale_x == "log10":
        lo, hi, q = math.log10(xs[0]), math.log10(xs[-1]), math.log10(xq) if xq > 0 else -1e9
    else:
        lo, hi, q = xs[0], xs[-1], xq
    span = max(hi - lo, 1e-12)
    if q < lo - reach * span or q > hi + reach * span:
        return None
    if q < lo:
        return float(ys[0])
    if q > hi:
        return float(ys[-1])
    if scale_x == "log10":
        return float(np.interp(math.log10(xq), np.log10(xs), ys))
    return float(np.interp(xq, xs, ys))


def digitize_chart(page, panel, words, rot_lines, shapes, small_fills):
    fx0, fy0, fx1, fy1 = panel.frame
    out = dict(frame_pt=[round(v, 3) for v in panel.frame], status="refused", reasons=[])
    pw, mixed = _power_words(page, words)
    # title: words above the frame, within its x range, up to 32 pt (a top
    # tick label's raised exponent is consumed by its 10^n word)
    tw = [w for w in pw if fx0 - 40 <= w.cx <= fx1 + 10 and fy0 - 34 <= w.cy < fy0 - 1]
    lines = {}
    for w in sorted(tw, key=lambda w: (round(w.cy), w.bbox[0])):
        lines.setdefault(round(w.cy), []).append(w.text)
    title_lines = [" ".join(v) for _, v in sorted(lines.items())]
    title = title_lines[-1] if title_lines else ""
    out["title_lines"] = title_lines
    cls, single, xq, yq = _classify(title)
    out["chart_class"] = cls
    if cls is None:
        nums_below = sum(1 for w in words if _num(w.text) is not None and fx0 - 15 < w.cx < fx1 + 15
                         and fy1 < w.cy < fy1 + 16)
        nums_left = sum(1 for w in words if _num(w.text) is not None and fx0 - 40 < w.bbox[2] < fx0 + 1
                        and fy0 - 8 < w.cy < fy1 + 8)
        if nums_below < 3 and nums_left < 3:
            out["status"] = "not_a_chart"     # page decoration: a ruled box without axes
            out["reasons"].append("ruled box without numeric axes")
            return out, None
        out["reasons"].append(f"unsupported chart title {title!r}")
        return out, None
    # tick labels: the numeric row right below the frame, the numeric column left of it
    # the windows reach well past the frame ends on purpose: a label row bound
    # one pitch off pushes its last label past the frame, and that label must be
    # seen (it then binds to no rule and the axis is refused)
    in_x = lambda w: fy1 < w.bbox[1] + 1 and w.cy < fy1 + 16 and fx0 - 40 < w.cx < fx1 + 40
    in_y = lambda w: w.bbox[2] < fx0 + 1 and w.bbox[2] > fx0 - 40 and fy0 - 25 < w.cy < fy1 + 25
    xl = [w for w in pw if _num(w.text) is not None and in_x(w)]
    yl = [w for w in pw if _num(w.text) is not None and in_y(w)]
    if xl:
        row = min(round(w.cy) for w in xl)
        xl = [w for w in xl if abs(w.cy - row) < 3]
    if yl:
        col = max(round(w.bbox[2]) for w in yl)
        yl = [w for w in yl if abs(w.bbox[2] - col) < 5]
    xcal = calibrate_axis("x", [(_num(w.text), w.cx) for w in xl],
                          [(r.pos, _darkness(r.colour)) for r in panel.vrules], fx0, fx1)
    ycal = calibrate_axis("y", [(_num(w.text), w.cy) for w in yl],
                          [(r.pos, _darkness(r.colour)) for r in panel.hrules], fy0, fy1)
    for cal, band in ((xcal, in_x), (ycal, in_y)):
        bad = [w.text for w in mixed if band(w)]
        if bad:
            cal.status = "refused"
            cal.reasons.append(f"tick labels {bad} mix glyph sizes (superscript?) and read as no 10^n")
    # axis titles and units
    xt = " ".join(w.text for w in sorted(words, key=lambda w: w.bbox[0])
                  if fx0 - 10 < w.cx < fx1 + 10 and fy1 + 8 < w.cy < fy1 + 28 and _num(w.text) is None)
    yt_cands = [t for t, bb in rot_lines if bb[2] <= fx0 and bb[0] >= fx0 - 60 and bb[1] < fy1 and bb[3] > fy0]
    yt = max(yt_cands, key=len) if yt_cands else ""
    xu, yu = _unit_of(xt), _unit_of(yt)
    xsi, ysi = _si(xu), _si(yu)
    out["axes"] = {
        k: dict(title=t, unit_printed=u, unit_si=(s[0] if s else None), factor_to_si=(s[1] if s else None),
                scale=c.scale, status=c.status, reasons=c.reasons,
                labels=[dict(value=v, label_centre_pt=round(lc, 3), rule_pt=round(rp, 3)) for v, lc, rp in c.labels],
                fit_resid_pt=(round(c.resid_pt, 4) if c.resid_pt == c.resid_pt else None), grid=c.grid,
                frame_range=([float(c.to_value(lo)), float(c.to_value(hi))] if c.scale else None))
        for k, t, u, s, c, lo, hi in (("x", xt, xu, xsi, xcal, fx0, fx1), ("y", yt, yu, ysi, ycal, fy1, fy0))
    }
    for k, s in (("x", xsi), ("y", ysi)):
        if s is None:
            out["reasons"].append(f"{k} unit not read (title {out['axes'][k]['title']!r})")
    if xcal.status == "refused" or ycal.status == "refused" or xsi is None or ysi is None:
        out["reasons"] += [f"x: {r}" for r in xcal.reasons] + [f"y: {r}" for r in ycal.reasons]
        return out, None
    # curve candidates: shapes that reach into the frame
    frame = panel.frame
    cand = []
    for s in shapes:
        m = _clip(s.pts, frame)
        if m.sum() < 2:
            continue
        P = s.pts[m]
        if np.ptp(P[:, 0]) + np.ptp(P[:, 1]) < 3:
            continue
        cand.append(s)
    leg, err = _legend(cand, small_fills, words, frame)
    if err:
        out["reasons"].append(err)
        return out, None
    bind, swatches = leg
    sw_ids = {id(s) for s in swatches}
    curves_raw = [s for s in cand if id(s) not in sw_ids
                  and not (s.bbox[2] - s.bbox[0] <= 30 and s.bbox[3] - s.bbox[1] <= 2.5)]
    # de-duplicate (PostScript pages paint some fills twice)
    uniq = []
    for s in curves_raw:
        if any(_col_dist(s.colour, u.colour) < 0.03 and s.pts.shape == u.pts.shape
               and np.abs(s.pts - u.pts).max() < 0.2 for u in uniq):
            continue
        uniq.append(s)
    groups: list[list[Shape]] = []
    for s in uniq:
        for g in groups:
            if _col_dist(g[0].colour, s.colour) < 0.03:
                g.append(s)
                break
        else:
            groups.append([s])
    labels = {}
    if single is not None:
        if bind:
            out["reasons"].append(f"legend {sorted(bind)} on a single-series chart")
        if len(groups) != 1:
            out["reasons"].append(f"{len(groups)} curve colours on a single-series chart")
            return out, None
        labels[0] = single
        binding = "single series"
    else:
        if not bind:
            out["reasons"].append("multi-series chart without a readable legend")
            return out, None
        for gi, g in enumerate(groups):
            names = [n for n, c in bind.items() if _col_dist(c, g[0].colour) < 0.03]
            if len(names) != 1:
                out["reasons"].append(f"curve colour {g[0].colour} binds legend {names}")
                return out, None
            labels[gi] = names[0]
        missing = set(bind) - set(labels.values())
        if missing:
            out["reasons"].append(f"legend entries without a curve: {sorted(missing)}")
            return out, None
        binding = "legend swatch colour"
    curves = []
    for gi, g in enumerate(groups):
        if len(g) > 1:
            out["reasons"].append(f"curve {labels[gi]!r}: {len(g)} separate paths of one colour")
            return out, None
        s = g[0]
        m = _clip(s.nodes, frame)
        N = s.nodes[m]
        Pm = _clip(s.pts, frame)
        xs = xcal.to_value(N[:, 0]) * xsi[1]
        ys = ycal.to_value(N[:, 1]) * ysi[1]
        # markers of the same colour (line-with-marker series): bbox centres
        def _near_swatch(cx, cy):
            return any(sw.bbox[0] - 4 <= cx <= sw.bbox[2] + 4 and sw.bbox[1] - 4 <= cy <= sw.bbox[3] + 4
                       for sw in swatches)
        mk = [((q.x0 + q.x1) / 2, (q.y0 + q.y1) / 2) for c, q in small_fills
              if _col_dist(c, s.colour) < 0.03
              and _clip(np.array([[(q.x0 + q.x1) / 2, (q.y0 + q.y1) / 2]]), frame).all()
              and not _near_swatch((q.x0 + q.x1) / 2, (q.y0 + q.y1) / 2)]
        mk_check = None
        basis = ("path segment end points (the plotted samples)" if s.kind == "stroke"
                 else "centre line of the filled stroke outline, 0.25 pt steps")
        if mk:
            # a marker belongs to this series only if it sits on its line; repaints
            # and fill+outline marker pairs collapse to one centre
            on = []
            for p in mk:
                d = _nearest_on_polyline(np.asarray(p), s.pts)[1]
                if d <= 1.0 and all(np.hypot(p[0] - q[0], p[1] - q[1]) > 1.5 for q, _ in on):
                    on.append((p, d))
            mk_check = dict(n_marker_shapes=len(mk), n_markers_on_line=len(on),
                            max_marker_to_line_pt=(round(max(d for _, d in on), 3) if on else None))
            if s.kind == "outline" and len(on) >= 2:
                # line-with-marker series printed as outlines: the samples are the
                # marker centres (a centre line cuts corners), ordered along the line
                def along(p):
                    d = np.hypot(*(s.pts - np.asarray(p)).T)
                    return int(np.argmin(d))
                Q = np.array(sorted((p for p, _ in on), key=along))
                N = Q[_clip(Q, frame)]
                xs = xcal.to_value(N[:, 0]) * xsi[1]
                ys = ycal.to_value(N[:, 1]) * ysi[1]
                basis = "marker centres on the outline centre line (line-with-marker series)"
        m = _clip(s.nodes, frame) if basis.startswith(("path", "centre")) else np.ones(len(N), bool)
        # visible ink past the detected frame: s.pts is what the page's own clip
        # lets through, so ink out here is DRAWN where our plot is not. The
        # samples there are cut off by _clip above; a curve silently shortened by
        # a wrong frame edge is exactly what this catches.
        V = s.pts
        past = dict(left=float(fx0 - V[:, 0].min()), right=float(V[:, 0].max() - fx1),
                    top=float(fy0 - V[:, 1].min()), bottom=float(V[:, 1].max() - fy1))
        worst_side = max(past, key=past.get)
        ink_past = round(max(0.0, past[worst_side]), 3)
        if ink_past > INK_PAST_FRAME_PT:
            out["reasons"].append(
                f"curve {labels[gi]!r}: visible ink runs {ink_past:.2f} pt past the detected frame's "
                f"{worst_side} edge (> {INK_PAST_FRAME_PT} pt) -- the plot does not end where the "
                f"frame was found; the served curve would be truncated")
        curves.append(dict(
            label=labels[gi], colour=list(s.colour), source=s.kind, n_items=s.n_items,
            point_basis=basis,
            note=s.note, n_points=int(len(N)), n_points_outside_frame=int((~m).sum()),
            x_si=[float(v) for v in xs], y_si=[float(v) for v in ys],
            x_unit=xsi[0], y_unit=ysi[0], markers=mk_check,
            ink_past_frame_pt=dict(worst=ink_past, side=worst_side, limit=INK_PAST_FRAME_PT),
            _drawn=s.pts[Pm], _nodes=N))
    out["curves"] = curves
    out["binding"] = binding
    # class checks
    checks = {}
    for c in curves:
        if cls == "dc_bias" and c["x_si"]:
            v0 = _value_at(c["x_si"], c["y_si"], 0.0)
            checks["dc_bias_at_0V_pct"] = dict(value=v0, expect=0.0, tol=1.0,
                                               status=("not_evaluable" if v0 is None else
                                                       "verified" if abs(v0) <= 1.0 else "inconsistent"))
        if cls in ("temperature",) and c["x_si"]:
            v25 = _value_at(c["x_si"], c["y_si"], 25.0)
            checks["temperature_at_25C_pct"] = dict(value=v25, expect=0.0, tol=1.0,
                                                    status=("not_evaluable" if v25 is None else
                                                            "verified" if abs(v25) <= 1.0 else "inconsistent"))
    if cls == "impedance_esr_vs_f":
        z = next((c for c in curves if c["label"].strip().upper() == "Z"), None)
        e = next((c for c in curves if c["label"].strip().upper() == "ESR"), None)
        if z and e:
            xs = np.array(e["x_si"])
            zi = np.array([_value_at(z["x_si"], z["y_si"], x, "log10") or np.nan for x in xs])
            ok = ~np.isnan(zi)
            # at the SRF ESR = |Z|; a 0.4 pt calibration error is ~4.5 % on a 6-decade axis
            frac = float((np.array(e["y_si"])[ok] <= zi[ok] * 1.05).mean()) if ok.any() else None
            checks["esr_le_z"] = dict(fraction_of_esr_points=frac,
                                      status=("not_evaluable" if frac is None else
                                              "verified" if frac >= 0.99 else "inconsistent"))
    out["checks"] = checks
    reasons = []
    if xcal.status != "verified":
        reasons += [f"x: {r}" for r in xcal.reasons]
    if ycal.status != "verified":
        reasons += [f"y: {r}" for r in ycal.reasons]
    if any(c["n_points"] < 2 for c in curves):
        reasons.append("a curve has < 2 points inside the frame")
    out["reasons"] += reasons
    out["status"] = "ok" if not out["reasons"] else "unverified"
    return out, (xcal, ycal)


# --------------------------------------------------------------------------- #
# page driver, outputs, overlay
# --------------------------------------------------------------------------- #

def _git_rev():
    try:
        return subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def digitize_pdf(pdf, page_no=1):
    fitz = _fitz()
    doc = fitz.open(str(pdf))
    page = doc[page_no - 1]
    rules, shapes, small_fills = _rules_and_shapes(page)
    words = _words(page)
    rot = _rotated_lines(page)
    charts, cals = [], []
    for panel in find_panels(rules):
        try:
            res, cal = digitize_chart(page, panel, words, rot, shapes, small_fills)
        except Exception as ex:  # an exception is a refusal, never a pass
            res, cal = dict(frame_pt=list(panel.frame), status="refused",
                            reasons=[f"exception: {type(ex).__name__}: {ex}"]), None
        charts.append(res)
        cals.append(cal)
    # page cross-check: |Z| at the lowest common frequency vs the C-f chart
    cf = next((c for c in charts if c.get("chart_class") == "c_vs_f" and c.get("curves")), None)
    zc = next((c for c in charts if c.get("chart_class") == "impedance_esr_vs_f" and c.get("curves")), None)
    page_checks = {}
    if cf and zc:
        z = next((c for c in zc["curves"] if c["label"].strip().upper() == "Z"), None)
        C = cf["curves"][0]
        if z:
            f0 = max(min(z["x_si"]), min(C["x_si"]))
            c0 = _value_at(C["x_si"], C["y_si"], f0, "log10")
            z0 = _value_at(z["x_si"], z["y_si"], f0, "log10")
            if c0 and z0:
                zc_ = 1 / (2 * math.pi * f0 * c0)
                r = z0 / zc_
                page_checks["z_vs_c_lowest_common_f"] = dict(
                    f_hz=f0, z_ohm=z0, c_f=c0, xc_ohm=zc_, ratio=r,
                    status="verified" if 0.95 <= r <= 1.05 else "inconsistent")
    meta = dict(tool="dsdig digitize-mlcc", dsdig_version=__version__, dsdig_commit=_git_rev(),
                pdf=str(pdf), page=page_no, creator=doc.metadata.get("creator"),
                producer=doc.metadata.get("producer"), n_rules=len(rules), n_shapes=len(shapes))
    return dict(meta=meta, charts=charts, page_checks=page_checks), page, cals


def overlay_canvas(page, frame, margins, dpi, head, ink=1.0):
    """The page rendered at `dpi` around `frame` (margins left, top, right, bottom
    in pt), as BGR below a white `head`-px band; `ink` < 1 fades the print.
    Returns the image and a pt -> px mapper."""
    import cv2

    fitz = _fitz()
    fx0, fy0, fx1, fy1 = frame
    ml, mt, mr, mb = margins
    clip = fitz.Rect(fx0 - ml, fy0 - mt, fx1 + mr, fy1 + mb)
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    src = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
    src = cv2.cvtColor(src, cv2.COLOR_RGB2BGR)
    if ink != 1.0:
        src = (255 - (255 - src.astype(np.int32)) * ink).astype(np.uint8)
    img = np.full((src.shape[0] + head, src.shape[1], 3), 255, np.uint8)
    img[head:] = src

    def px(x, y):
        return int(round((x - clip.x0) * zoom)), int(round((y - clip.y0) * zoom)) + head

    return img, px


def draw_axis_map(img, px, cals, frame, colour, tick_pt=5, crosses=True):
    """A line across the frame at every bound tick label, placed by the FITTED
    map (a mis-bound or mis-scaled tick shows off its printed rule)."""
    import cv2

    fx0, fy0, fx1, fy1 = frame
    xcal, ycal = cals
    for v, _, _ in xcal.labels:
        x = float(xcal.to_pt(v))
        cv2.line(img, px(x, fy0), px(x, fy1 + tick_pt), colour, 1, cv2.LINE_AA)
        if crosses:
            cv2.drawMarker(img, px(x, fy1), colour, cv2.MARKER_TILTED_CROSS, 12, 2, cv2.LINE_AA)
    for v, _, _ in ycal.labels:
        y = float(ycal.to_pt(v))
        cv2.line(img, px(fx0 - tick_pt, y), px(fx1, y), colour, 1, cv2.LINE_AA)
        if crosses:
            cv2.drawMarker(img, px(fx0, y), colour, cv2.MARKER_TILTED_CROSS, 12, 2, cv2.LINE_AA)


def render_overlay(page, chart, cals, out_png, dpi=300):
    """Chart crop with margin (title, both axes' tick labels and titles), the
    digitized axis -- a green crosshair at every bound tick label, placed by the
    FITTED map, so a mis-bound or mis-scaled tick shows as a line off its printed
    rule -- and every served curve, drawn from the served SI values mapped back
    through the calibration (so the overlay tests the unit factor and the map)."""
    import cv2

    img, px = overlay_canvas(page, chart["frame_pt"], (52, 40, 24, 36), dpi, 84)
    xcal, ycal = cals
    font = cv2.FONT_HERSHEY_SIMPLEX
    green = (0, 160, 0)
    draw_axis_map(img, px, cals, chart["frame_pt"], green, tick_pt=5, crosses=True)
    palette = [(0, 120, 255), (200, 0, 160), (0, 200, 220), (40, 40, 220)]
    keys = []
    for k, c in enumerate(chart.get("curves", [])):
        col = palette[k % len(palette)]
        xs = xcal.to_pt(np.asarray(c["x_si"]) / chart["axes"]["x"]["factor_to_si"])
        ys = ycal.to_pt(np.asarray(c["y_si"]) / chart["axes"]["y"]["factor_to_si"])
        P = [px(x, y) for x, y in zip(xs, ys)]
        for a_, b_ in zip(P[:-1], P[1:]):
            cv2.line(img, a_, b_, col, 1, cv2.LINE_AA)
        step = max(1, len(P) // 40)          # sparse rings: the ink must stay visible
        for p_ in P[::step]:
            cv2.circle(img, p_, 5, col, 1, cv2.LINE_AA)
        keys.append((c["label"], col, len(P)))
    st = chart.get("status")
    cv2.putText(img, f"{chart.get('id', chart.get('chart_class'))}  dsdig status={st}  "
                f"x:{xcal.scale} y:{ycal.scale}", (8, 22), font, 0.6, (0, 0, 180), 1, cv2.LINE_AA)
    xo = 8
    cv2.putText(img, "green x/line = digitized axis (fitted map at each label);", (xo, 48), font, 0.5, green, 1, cv2.LINE_AA)
    xo = 8
    for lab, col, n in keys:
        t = f"o {lab} ({n} pts)"
        cv2.putText(img, t, (xo, 74), font, 0.5, col, 2, cv2.LINE_AA)
        xo += 11 * len(t) + 14
    cv2.imwrite(str(out_png), img)
    return out_png


def write_outputs(result, page, cals, out_dir, stem, overlays=True):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for i, (ch, cal) in enumerate(zip(result["charts"], cals)):
        tag = ch.get("chart_class") or f"panel{i}"
        ch["id"] = f"{stem}_{tag}"
        if ch.get("curves"):
            path = out_dir / f"{stem}_{tag}.csv"
            with path.open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["curve", f"x_{ch['curves'][0]['x_unit']}", f"y_{ch['curves'][0]['y_unit']}"])
                for c in ch["curves"]:
                    for x, y in zip(c["x_si"], c["y_si"]):
                        w.writerow([_cell(c["label"]), f"{x:.6g}", f"{y:.6g}"])
            ch["csv"] = str(path)
            written.append(path)
        if overlays and cal is not None and ch.get("curves"):
            png = out_dir / f"{stem}_{tag}_overlay.png"
            render_overlay(page, ch, cal, png)
            ch["overlay"] = str(png)
            written.append(png)
    for ch in result["charts"]:
        for c in ch.get("curves", []):
            c.pop("_drawn", None)
            c.pop("_nodes", None)
    j = out_dir / f"{stem}_mlcc.json"
    j.write_text(json.dumps(result, indent=1, default=float))
    written.append(j)
    return written


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _cell(text) -> str:
    """A PDF-derived string made safe for a CSV cell and a terminal line: control
    characters (incl. ESC sequences) dropped, and a leading = + - @ (a
    spreadsheet formula) defused with a quote. Legend labels come from the
    vendor's PDF, so they are untrusted input."""
    t = _CONTROL.sub("", str(text))
    return "'" + t if t[:1] in ("=", "+", "-", "@") else t


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dsdig digitize-mlcc",
                                 description="Digitize vector MLCC characteristic charts (Taiyo Yuden "
                                             "Electrical Characteristics Data PDFs, Excel or PScript5 print).")
    ap.add_argument("pdf", nargs="+")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--out", default="out/mlcc")
    ap.add_argument("--no-overlay", action="store_true")
    a = ap.parse_args(argv)
    worst = 0
    for pdf in a.pdf:
        stem = Path(pdf).stem
        res, page, cals = digitize_pdf(pdf, a.page)
        write_outputs(res, page, cals, Path(a.out) / stem, stem, overlays=not a.no_overlay)
        for ch in res["charts"]:
            if ch["status"] == "not_a_chart":
                continue
            print(f"{stem}\t{ch.get('chart_class')}\t{ch['status']}\t"
                  f"{','.join(_cell(c['label']) + ':' + str(c['n_points']) for c in ch.get('curves', []))}\t"
                  f"{_cell('; '.join(ch['reasons']))[:200]}")
            worst = max(worst, {"ok": 0, "unverified": 1, "not_a_chart": 0}.get(ch["status"], 2))
        for k, v in res["page_checks"].items():
            print(f"{stem}\tpage_check {k}\t{v['status']}\tratio={v.get('ratio'):.4f}")
    return 0 if worst < 2 else 1
