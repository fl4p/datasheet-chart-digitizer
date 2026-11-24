"""Vector line-with-marker charts in scientific papers (MATLAB "box off" style).

Validated on one paper only (Keil et al. 2016 Fig. 2c, MATLAB export; overlay and
axis calibration passed human review 2026-11-24): review overlays of other sources.

Layout handled: an L-shaped axis (one dark vertical and one dark horizontal axis
line meeting at the lower-left corner), short inward tick marks on both axes,
optional light full-span gridlines, numeric tick labels left of / below the
axes, and series drawn as one stroked polyline each whose vertices are the
plotted samples, with a marker (square, diamond, circle, dot, x, +, *) on every
vertex. An in-plot legend shows, per entry, a short line sample in the series
colour with the series marker at its centre and the label to its right.

Calibration: every tick label is bound to its tick mark (mlcc_vector.calibrate_axis,
tick marks as the major rules); gridlines are checked against the fitted label
lattice. Identity: a series binds to the legend entry with the same colour AND
the same marker signature; the signature of a series is the majority over its
vertices and must be unique, or the chart is ``unverified``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

from .mlcc_vector import (_cell, _chromatic, _colour, _darkness, _fitz, _git_rev, _num, _rotated_lines, _words,
                          calibrate_axis, draw_axis_map, overlay_canvas)

AXIS_DARK_MIN = 0.6          # axis lines / ticks: darkness >= this (MATLAB 0.149 grey -> 0.85)
GRID_DARK_MAX = 0.3          # gridlines are light (MATLAB 0.873 grey -> 0.13)
TICK_MAX_PT = 4.0
MARKER_MAX_PT = 8.0
MARKER_TO_VERTEX_PT = 0.35
SIGNATURE_MAJORITY = 0.6
CHROMA_MIN = 0.08            # MATLAB's muted default colours are less saturated than Excel's


def _chroma(c):
    return _chromatic(c, CHROMA_MIN)


def _seg_tokens(items, centre):
    """Marker geometry tokens: spokes through the centre (x, +, *), closed edges
    (square, diamond), curves (circle/dot)."""
    cx, cy = centre
    toks = set()
    ls = [it for it in items if it[0] == "l"]
    if len(ls) >= 8 and math.hypot(ls[0][1].x - ls[-1][2].x, ls[0][1].y - ls[-1][2].y) < 0.05:
        return {"curve"}                     # a circle drawn as a closed polygon
    for it in items:
        k = it[0]
        if k == "re":
            toks.add("rect")
        elif k == "qu":
            q = it[1]
            toks.add("rect" if abs(q.ul.y - q.ur.y) < 0.05 else "quad-diag")
        elif k == "c":
            toks.add("curve")
        elif k == "l":
            a, b = it[1], it[2]
            dx, dy = b.x - a.x, b.y - a.y
            L = math.hypot(dx, dy)
            if L < 1e-6:
                continue
            ang = math.degrees(math.atan2(dy, dx)) % 180
            o = "h" if ang < 15 or ang > 165 else "v" if 75 < ang < 105 else "d"
            dist = abs(dy * (cx - a.x) - dx * (cy - a.y)) / L
            toks.add(("spoke-" if dist < 0.2 else "edge-") + o)
    return toks


def _marker_kind(fill, toks):
    if "curve" in toks:
        return "dot" if fill else "circle"
    if "rect" in toks or {"edge-h", "edge-v"} <= toks:
        return "square"
    if "quad-diag" in toks or toks == {"edge-d"}:
        return "diamond"
    spokes = {t[6:] for t in toks if t.startswith("spoke-")}
    if spokes == {"d"}:
        return "x"
    if spokes == {"h", "v"}:
        return "+"
    if spokes == {"h", "v", "d"}:
        return "*"
    return "?" + ",".join(sorted(toks))


def _collect(page):
    """Axis-candidate lines, ticks, gridlines, polylines and marker pieces."""
    lines, polys, marks = [], [], []
    for dr in page.get_drawings():
        items = dr.get("items") or []
        stroke, fill = _colour(dr.get("color")), _colour(dr.get("fill"))
        r = dr["rect"]
        if max(r.width, r.height) < MARKER_MAX_PT and (stroke or fill):
            col = fill or stroke
            if _chroma(col) or _darkness(col) >= AXIS_DARK_MIN:
                c = ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
                # a lone axis tick is also small: keep it as a line as well
                marks.append(dict(colour=col, fill=fill is not None, centre=c, items=items))
        if stroke is None or not items:
            continue
        if all(it[0] == "l" for it in items):
            segs = [((it[1].x, it[1].y), (it[2].x, it[2].y)) for it in items]
            chained = all(math.hypot(segs[i][1][0] - segs[i + 1][0][0], segs[i][1][1] - segs[i + 1][0][1]) < 1e-3
                          for i in range(len(segs) - 1))
            if len(segs) == 1:
                lines.append(dict(colour=stroke, a=segs[0][0], b=segs[0][1], width=float(dr.get("width") or 0)))
            elif chained and max(r.width, r.height) >= MARKER_MAX_PT:
                P = np.array([segs[0][0]] + [s[1] for s in segs])
                polys.append(dict(colour=stroke, nodes=P, width=float(dr.get("width") or 0)))
            elif all(abs(a[0] - b[0]) < 0.05 for a, b in segs):
                for a, b in segs:                           # (dashed) vertical lines in one path
                    lines.append(dict(colour=stroke, a=a, b=b, width=float(dr.get("width") or 0)))
    return lines, polys, marks


def _axes(lines):
    """L-shaped axis pairs: a dark vertical and a dark horizontal line sharing the
    lower-left corner."""
    dark = [l for l in lines if _darkness(l["colour"]) >= AXIS_DARK_MIN]
    V = [l for l in dark if abs(l["a"][0] - l["b"][0]) < 0.05 and abs(l["a"][1] - l["b"][1]) > 40]
    H = [l for l in dark if abs(l["a"][1] - l["b"][1]) < 0.05 and abs(l["a"][0] - l["b"][0]) > 40]
    out = []
    for v in V:
        vx, vy0, vy1 = v["a"][0], min(v["a"][1], v["b"][1]), max(v["a"][1], v["b"][1])
        for h in H:
            hy, hx0, hx1 = h["a"][1], min(h["a"][0], h["b"][0]), max(h["a"][0], h["b"][0])
            if abs(hx0 - vx) < 0.3 and abs(hy - vy1) < 0.3:
                out.append((vx, vy0, hx1, hy))
    return out


def _ticks(lines, frame):
    x0, y0, x1, y1 = frame
    xt, yt = [], []
    for l in lines:
        if _darkness(l["colour"]) < AXIS_DARK_MIN:
            continue
        (ax, ay), (bx, by) = l["a"], l["b"]
        L = math.hypot(bx - ax, by - ay)
        if not 0.3 < L <= TICK_MAX_PT:
            continue
        if abs(ax - bx) < 0.05 and (abs(ay - y1) < 0.3 or abs(by - y1) < 0.3) and x0 - 0.3 <= ax <= x1 + 0.3:
            xt.append(ax)
        elif abs(ay - by) < 0.05 and (abs(ax - x0) < 0.3 or abs(bx - x0) < 0.3) and y0 - 0.3 <= ay <= y1 + 0.3:
            yt.append(ay)
    return sorted(set(round(v, 3) for v in xt)), sorted(set(round(v, 3) for v in yt))


def _gridlines(lines, frame):
    """Light full-span rules inside this panel's plot box (stacked or side-by-side
    panels share the span of their neighbours' gridlines, not their position)."""
    x0, y0, x1, y1 = frame
    gh = [l["a"][1] for l in lines if _darkness(l["colour"]) <= GRID_DARK_MAX and abs(l["a"][1] - l["b"][1]) < 0.05
          and y0 - 1 <= l["a"][1] <= y1 + 1
          and min(l["a"][0], l["b"][0]) <= x0 + 1 and max(l["a"][0], l["b"][0]) >= x1 - 1]
    gv = [l["a"][0] for l in lines if _darkness(l["colour"]) <= GRID_DARK_MAX and abs(l["a"][0] - l["b"][0]) < 0.05
          and x0 - 1 <= l["a"][0] <= x1 + 1
          and min(l["a"][1], l["b"][1]) <= y0 + 1 and max(l["a"][1], l["b"][1]) >= y1 - 1]
    return sorted(set(round(v, 3) for v in gh)), sorted(set(round(v, 3) for v in gv))


def _signature_at(marks, colour, p, tol=MARKER_TO_VERTEX_PT):
    near = [m for m in marks if m["colour"] == colour
            and math.hypot(m["centre"][0] - p[0], m["centre"][1] - p[1]) <= tol]
    if not near:
        return None
    toks, fill = set(), False
    for m in near:
        toks |= _seg_tokens(m["items"], m["centre"])   # spokes pass through their own centre
        fill = fill or m["fill"]
    return _marker_kind(fill, toks)


def digitize_panel(page, frame, lines, polys, marks, words, rot):
    fx0, fy0, fx1, fy1 = frame
    out = dict(chart_class="paper_line_marker", frame_pt=[round(v, 3) for v in frame], status="refused",
               reasons=[], validation_state="UNVALIDATED chart class (needs human-verified overlays)")
    xt, yt = _ticks(lines, frame)
    gh, gv = _gridlines(lines, frame)
    xl = [w for w in words if _num(w.text) is not None and fy1 < w.bbox[1] + 1 and w.cy < fy1 + 16
          and fx0 - 20 < w.cx < fx1 + 20]
    yl = [w for w in words if _num(w.text) is not None and fx0 - 40 < w.bbox[2] < fx0 + 1
          and fy0 - 15 < w.cy < fy1 + 15]
    if xl:
        row = min(round(w.cy) for w in xl)
        xl = [w for w in xl if abs(w.cy - row) < 3]
    if yl:
        col = max(round(w.bbox[2]) for w in yl)
        yl = [w for w in yl if abs(w.bbox[2] - col) < 5]
    xcal = calibrate_axis("x", [(_num(w.text), w.cx) for w in xl],
                          [(t, 1.0) for t in xt] + [(g, 0.1) for g in gv], fx0, fx1)
    ycal = calibrate_axis("y", [(_num(w.text), w.cy) for w in yl],
                          [(t, 1.0) for t in yt] + [(g, 0.1) for g in gh], fy1, fy0)
    xt_title = " ".join(w.text for w in sorted(words, key=lambda w: w.bbox[0])
                        if fx0 - 10 < w.cx < fx1 + 10 and fy1 + 9 < w.cy < fy1 + 28 and _num(w.text) is None)
    yt_c = [t for t, bb in rot if bb[2] <= fx0 and bb[0] >= fx0 - 60 and bb[1] < fy1 and bb[3] > fy0]
    yt_title = max(yt_c, key=len) if yt_c else ""
    out["axes"] = {k: dict(title=t, scale=c.scale, status=c.status, reasons=c.reasons,
                           ticks_pt=tk, gridlines_pt=g,
                           labels=[dict(value=v, label_centre_pt=round(lc, 3), tick_pt=round(rp, 3))
                                   for v, lc, rp in c.labels],
                           fit_resid_pt=(round(c.resid_pt, 4) if c.resid_pt == c.resid_pt else None),
                           map_pt=dict(a=c.a, b=c.b), grid=c.grid)
                   for k, t, c, tk, g in (("x", xt_title, xcal, xt, gv), ("y", yt_title, ycal, yt, gh))}
    if xcal.status == "refused" or ycal.status == "refused":
        out["reasons"] += [f"x: {r}" for r in xcal.reasons] + [f"y: {r}" for r in ycal.reasons]
        return out, None
    # legend: short horizontal 2-point line samples with a word right of them
    inplot = [w for w in words if fx0 < w.cx < fx1 + 5 and fy0 - 5 < w.cy < fy1]
    legend = []
    for l in lines:
        (ax, ay), (bx, by) = l["a"], l["b"]
        if abs(ay - by) > 0.05 or not 8 <= abs(bx - ax) <= 30:
            continue
        if not (_chroma(l["colour"]) or _darkness(l["colour"]) >= AXIS_DARK_MIN):
            continue
        right = max(ax, bx)
        row = [w for w in inplot if abs(w.cy - ay) < 3 and 0 <= w.bbox[0] - right < 15]
        if not row:
            continue
        lab_words = sorted([w for w in inplot if abs(w.cy - ay) < 3 and w.bbox[0] >= right], key=lambda w: w.bbox[0])
        mid = ((ax + bx) / 2, ay)
        legend.append(dict(label=" ".join(w.text for w in lab_words), colour=l["colour"],
                           marker=_signature_at(marks, l["colour"], mid), swatch=(min(ax, bx), ay, right, ay)))
    # dotted achromatic series are polylines with a dash pattern: the legend sample
    # is then a dashed polyline too
    for p in polys:
        N = p["nodes"]
        if np.ptp(N[:, 1]) > 0.05 or not 8 <= np.ptp(N[:, 0]) <= 30:
            continue
        right = N[:, 0].max()
        lab_words = sorted([w for w in inplot if abs(w.cy - N[0, 1]) < 3 and w.bbox[0] >= right], key=lambda w: w.bbox[0])
        if lab_words and lab_words[0].bbox[0] - right < 15:
            mid = ((N[:, 0].min() + right) / 2, N[0, 1])
            legend.append(dict(label=" ".join(w.text for w in lab_words), colour=p["colour"],
                               marker=_signature_at(marks, p["colour"], mid),
                               swatch=(N[:, 0].min(), N[0, 1], right, N[0, 1])))
    keyset = Counter((e["colour"], e["marker"]) for e in legend)
    dup = [k for k, n in keyset.items() if n > 1]
    if dup:
        out["reasons"].append(f"legend entries share colour+marker: {dup}")
    out["legend"] = [dict(label=e["label"], colour=list(e["colour"]), marker=e["marker"]) for e in legend]
    sw = [e["swatch"] for e in legend]
    series = []
    for p in polys:
        N = p["nodes"]
        if len(N) < 3:
            continue
        if not ((N[:, 0] >= fx0 - 0.5) & (N[:, 0] <= fx1 + 0.5) & (N[:, 1] >= fy0 - 0.5) & (N[:, 1] <= fy1 + 0.5)).all():
            continue
        if any(abs(N[0, 1] - s[1]) < 0.1 and N[:, 0].min() >= s[0] - 0.1 and N[:, 0].max() <= s[2] + 0.1 for s in sw):
            continue
        if not (_chroma(p["colour"]) or _darkness(p["colour"]) >= AXIS_DARK_MIN):
            continue
        if np.ptp(N[:, 0]) < 1.0 or np.ptp(N[:, 1]) > 0.9 * (fy1 - fy0):     # vertical reference lines
            if np.ptp(N[:, 0]) < 1.0:
                out.setdefault("reference_lines", []).append(dict(
                    colour=list(p["colour"]), x_value=float(xcal.to_value(N[0, 0])), note="vertical line"))
                continue
        sigs = [_signature_at(marks, p["colour"], q) for q in N]
        cnt = Counter(s for s in sigs if s is not None)
        top, ntop = cnt.most_common(1)[0] if cnt else (None, 0)
        series.append(dict(poly=p, sigs=sigs, marker=top, marker_frac=ntop / len(N)))
    curves = []
    for s in series:
        col = s["poly"]["colour"]
        cands = [e for e in legend if e["colour"] == col and e["marker"] == s["marker"]]
        reasons = []
        if s["marker_frac"] < SIGNATURE_MAJORITY:
            reasons.append(f"marker signature {s['marker']} on only {s['marker_frac']:.0%} of vertices")
        if len(cands) != 1:
            reasons.append(f"colour {col} + marker {s['marker']} binds {len(cands)} legend entries")
        label = cands[0]["label"] if len(cands) == 1 else f"unbound {col} {s['marker']}"
        N = s["poly"]["nodes"]
        keep = np.r_[True, np.hypot(*np.diff(N, axis=0).T) > 0.01]
        n_dup = int((~keep).sum())
        N = N[keep]
        xs, ys = xcal.to_value(N[:, 0]), ycal.to_value(N[:, 1])
        no_marker = [i for i, g in enumerate(s["sigs"]) if g is None]
        curves.append(dict(label=label, colour=list(col), marker=s["marker"],
                           marker_vertex_fraction=round(s["marker_frac"], 3),
                           vertices_without_marker=no_marker,
                           point_basis="polyline vertices (the plotted samples)",
                           status="ok" if not reasons else "unbound", reasons=reasons,
                           n_points=int(len(N)), repeated_vertices_dropped=n_dup, x=[float(v) for v in xs], y=[float(v) for v in ys],
                           _nodes=N))
    used = Counter(c["label"] for c in curves if c["status"] == "ok")
    for lab, n in used.items():
        if n > 1:
            out["reasons"].append(f"legend {lab!r} bound by {n} series")
    missing = [e["label"] for e in legend if e["label"] not in used]
    if missing:
        out["reasons"].append(f"legend entries without a series: {missing}")
    if any(c["status"] != "ok" for c in curves):
        out["reasons"].append("some series unbound")
    for k, c in (("x", xcal), ("y", ycal)):
        if c.status != "verified":
            out["reasons"] += [f"{k}: {r}" for r in c.reasons]
    for l in lines:
        (ax, ay), (bx, by) = l["a"], l["b"]
        if (abs(ax - bx) < 0.05 and fx0 + 1 < ax < fx1 - 1 and abs(ay - by) > 0.5 * (fy1 - fy0)
                and _darkness(l["colour"]) >= 0.5):
            out.setdefault("reference_lines", []).append(dict(
                colour=list(l["colour"]), x_pt=round(ax, 3), x_value=round(float(xcal.to_value(ax)), 3),
                note="vertical in-plot line (e.g. a marked position); not a data series"))
    out["curves"] = curves
    out["status"] = "unverified"   # unvalidated class: never "ok" until Fab approves overlays
    if not out["reasons"]:
        out["reasons"].append("all automated gates passed; class unvalidated")
    return out, (xcal, ycal)


def render_overlay(page, chart, cals, out_png, dpi=600):
    import cv2

    head = 40 + 26 * (len(chart.get("curves", [])) // 3 + 1)
    img, px = overlay_canvas(page, chart["frame_pt"], (34, 14, 14, 30), dpi, head, ink=0.55)
    xcal, ycal = cals
    font = cv2.FONT_HERSHEY_SIMPLEX
    draw_axis_map(img, px, cals, chart["frame_pt"], (0, 150, 0), tick_pt=4, crosses=False)
    palette = [(255, 0, 255), (0, 0, 0), (0, 200, 255), (255, 128, 0), (0, 0, 255), (128, 0, 128),
               (0, 160, 160), (90, 90, 255), (200, 80, 0)]
    cv2.putText(img, f"{chart['id']} status={chart['status']} (UNVALIDATED class); green = fitted axis map at each label",
                (8, 24), font, 0.65, (0, 0, 160), 1, cv2.LINE_AA)
    for k, c in enumerate(chart.get("curves", [])):
        col = palette[k % len(palette)]
        P = [px(float(xcal.to_pt(x)), float(ycal.to_pt(y))) for x, y in zip(c["x"], c["y"])]
        for a_, b_ in zip(P[:-1], P[1:]):
            cv2.line(img, a_, b_, col, 1, cv2.LINE_AA)
        for p_ in P:
            cv2.drawMarker(img, p_, col, cv2.MARKER_CROSS, 22, 2, cv2.LINE_AA)
        t = f"+ {c['label']} [{c['marker']}] ({len(P)})"
        cv2.putText(img, t, (8 + 520 * (k % 3), 56 + 26 * (k // 3)), font, 0.6, col, 2, cv2.LINE_AA)
    cv2.imwrite(str(out_png), img)
    return out_png


def digitize_pdf(pdf, page_no):
    fitz = _fitz()
    doc = fitz.open(str(pdf))
    page = doc[page_no - 1]
    lines, polys, marks = _collect(page)
    words, rot = _words(page), _rotated_lines(page)
    charts, cals = [], []
    for frame in _axes(lines):
        ch, cal = digitize_panel(page, frame, lines, polys, marks, words, rot)
        charts.append(ch)
        cals.append(cal)
    return dict(pdf=str(pdf), page=page_no, dsdig_rev=_git_rev(), charts=charts), page, cals


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dsdig digitize-paper-lines",
                                 description="Digitize vector line-with-marker charts from papers "
                                             "(MATLAB box-off axes). Validated on one paper; best status unverified.")
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--out", default="out/paper-lines")
    a = ap.parse_args(argv)
    res, page, cals = digitize_pdf(a.pdf, a.page)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{Path(a.pdf).stem.replace(' ', '_')}_p{a.page}"
    for i, (ch, cal) in enumerate(zip(res["charts"], cals)):
        ch["id"] = f"{stem}_panel{i}"
        if cal is not None and ch.get("curves"):
            with (out / f"{ch['id']}.csv").open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["series", "marker", "x", "y"])
                for c in ch["curves"]:
                    for x, y in zip(c["x"], c["y"]):
                        w.writerow([_cell(c["label"]), c["marker"], f"{x:.4f}", f"{y:.5f}"])
            render_overlay(page, ch, cal, out / f"{ch['id']}_overlay.png")
        print(f"{ch['id']}\tframe={ch['frame_pt']}\t{ch['status']}\t"
              f"{','.join(_cell(c['label']) + ':' + str(c['n_points']) for c in ch.get('curves', []))}\t"
              f"{_cell('; '.join(ch['reasons']))[:300]}")
    for ch in res["charts"]:
        for c in ch.get("curves", []):
            c.pop("_nodes", None)
    (out / f"{stem}.json").write_text(json.dumps(res, indent=1, default=float))
    return 0
