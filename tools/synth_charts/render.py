"""Matplotlib renderer: datasheet-like pages with 1-4 charts, exact frame geometry.

Every curve is drawn in *axes coordinates*, which are exactly the scorer's normalised frame
coordinates (``u = to_norm(axis, value)``), so the PDF vertex of a GT point is
``frame_origin + u * frame_size`` with no library transform in between. Figures use
``dpi=72``: display units are PDF points.
"""
from __future__ import annotations

import copy
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import transforms as mtrans  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import PathPatch, Polygon  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402

from . import gt as G  # noqa: E402
from .axes import decimals_for, fmt_tick, is_log, major_ticks, minor_ticks, to_norm  # noqa: E402
from .styles import DASHES, PALETTES  # noqa: E402

PAGE_W, PAGE_H = 595.28, 841.89
RC = {
    "pdf.fonttype": 42, "ps.fonttype": 42, "path.simplify": False, "path.snap": False,
    "axes.unicode_minus": False, "lines.solid_capstyle": "butt", "lines.dash_capstyle": "butt",
    "text.antialiased": True, "figure.dpi": 72, "savefig.dpi": 72, "pdf.compression": 6,
}
SIGNED_AXES = {"transfer": "xy", "output": "xy", "rdson_id": "x", "capacitance": "x", "gate_charge": "y"}
NEG_LABEL_CLASSES = {"output", "rdson_id", "gate_charge"}


# ----------------------------------------------------------------------------- spec prep
def prepare(spec: dict, style: dict, rng) -> dict:
    """Apply axis-level style factors to a copy of the spec and fix the drawn geometry.

    Returns a spec with ``major``/``minor`` tick values per axis, ``drawn`` (per curve, the list
    of normalised polyline pieces that will be drawn), and ``applied`` (factors really used).
    """
    sp = copy.deepcopy(spec)
    applied = {}
    # --- signed / reversed axes (P-channel style)
    mode = style.get("signed_axis", "none")
    if mode != "none" and sp["cls"] in SIGNED_AXES:
        for k in SIGNED_AXES[sp["cls"]]:
            ax = sp[k]
            if is_log(ax):
                continue
            lo, hi = ax["min"], ax["max"]
            ax["min"], ax["max"] = (-hi, -lo) if mode == "neg_left" else (-lo, -hi)
            for c in sp["curves"]:
                c[k] = -np.asarray(c[k])
            ax["title"] = ax["title"].replace("VDS", "-VDS", 1) if "VDS" in ax["title"] and k == "x" else ax["title"]
        if sp["cls"] in NEG_LABEL_CLASSES:
            for c in sp["curves"]:
                if isinstance(c["label"], (int, float)):
                    c["label"] = -c["label"]
                    c["printed"] = _neg_printed(c["printed"])
        applied["signed_axis"] = mode
    # --- ticks
    for k in ("x", "y"):
        ax = sp[k]
        ax["major"] = major_ticks(ax)
        if is_log(ax):
            ax["minor"] = minor_ticks(ax, style["minor_per_decade"], ax["major"]) if style["minor_per_decade"] else []
        else:
            ax["minor"] = minor_ticks(ax, style["minor_per_major"], ax["major"])
    # --- frame past the last label (e.g. 0..5.4 V framed with labels to 5.0 V; 345 A top)
    if style.get("frame_past_last_label"):
        cand = [k for k in ("x", "y") if not is_log(sp[k])]
        if cand:
            k = cand[int(rng.integers(len(cand)))]
            ax = sp[k]
            step = ax.get("major_step") or abs(ax["major"][1] - ax["major"][0])
            sub = step / (style["minor_per_major"] or 5)
            nsub = int(rng.integers(1, max(2, int(round(step / sub)))))
            ext = nsub * sub
            ax["max"] = ax["max"] + math.copysign(ext, ax["max"] - ax["min"])
            ax["major"] = major_ticks(ax, step)
            ax["minor"] = minor_ticks(ax, style["minor_per_major"], ax["major"], step)
            applied["frame_past_last_label"] = {"axis": k, "extension": ext}
    # --- drawn geometry
    for c in sp["curves"]:
        uv = G.norm_xy(sp, c["x"], c["y"])
        c["drawn"] = G.prepare_drawn(uv)
    if style.get("curve_on_gridline") and sp["cls"] in ("capacitance", "rdson_id", "reverse_leakage", "zth"):
        cand = [c for c in sp["curves"] if c["drawn"]]
        c = cand[int(rng.integers(len(cand)))]
        pc = c["drawn"][0]
        grid_u = [float(to_norm(sp["y"], v)) for v in sp["y"]["major"] + sp["y"]["minor"]]
        grid_u = [g for g in grid_u if 0.02 < g < 0.98]
        if grid_u and np.all(np.diff(pc[:, 0]) >= 0):
            x_start = max(0.0, pc[0, 0])
            y0 = pc[np.argmin(np.abs(pc[:, 0] - x_start)), 1]
            g = min(grid_u, key=lambda v: abs(v - y0))
            ux = pc[:, 0] - x_start
            w = np.clip((ux - 0.12) / 0.10, 0, 1)
            w = w * w * (3 - 2 * w)
            pc = np.c_[pc[:, 0], g * (1 - w) + pc[:, 1] * w]
            c["drawn"][0] = G.resample_arclength(pc)
            applied["curve_on_gridline"] = {"curve": str(c["label"]), "grid_u": g}
    sp["applied"] = applied
    return sp


def _neg_printed(s: str) -> str:
    import re
    return re.sub(r"(?<![\d.])(\d+(?:\.\d+)?)", r"-\1", s, count=1)


# ----------------------------------------------------------------------------- curve styles
def curve_styles(style: dict, n: int):
    base = style["stroke_pt"]
    pal = PALETTES[style["palette"]]
    out = []
    for i in range(n):
        col = pal[i % len(pal)] if (style["color_mode"] == "color" and style["distinguish"] == "color") else \
            ("#000000" if style["color_mode"] == "bw" else pal[0])
        ls = DASHES[i % len(DASHES)] if style["distinguish"] == "dash" else "solid"
        lw = min(3.0, max(base, 0.5) * (1 + 0.8 * i)) if style["distinguish"] == "width" else base
        out.append({"color": col, "ls": ls, "lw": round(lw, 3)})
    if style["color_mode"] == "color" and style["distinguish"] != "color":
        for o in out:
            o["color"] = pal[0]
    return out


def probe_color(i: int) -> str:
    return "#%02x%02x%02x" % (1 + (i * 53) % 250, 3 + (i * 29) % 250, 7 + (i * 71) % 240)


# ----------------------------------------------------------------------------- layout
def layout_cells(page: dict, n: int):
    """Chart cells (x0, y0, x1, y1) in pt, origin top-left."""
    m, top, bot = 50.0, 80.0, 60.0
    gap = page["gap_pt"]
    W = PAGE_W - 2 * m
    if n == 1:
        w = W * 0.78
        h = w * 0.8
        x0 = (PAGE_W - w) / 2
        return [(x0, top, x0 + w, top + h)]
    cols = 2
    rows = math.ceil(n / cols)
    w = (W - gap * (cols - 1)) / cols
    h = min(w * 0.95, (PAGE_H - top - bot - gap * (rows - 1)) / rows)
    cells = []
    for i in range(n):
        r, c = divmod(i, cols)
        x0 = m + c * (w + gap)
        y0 = top + r * (h + gap)
        cells.append((x0, y0, x0 + w, y0 + h))
    return cells


def _est_label_w(sp, style, k):
    labs = [fmt_tick(v, style[f"{k}_tick_format"], log=is_log(sp[k])) for v in sp[k]["major"]]
    L = max((len(s.replace("$", "").replace("^", "").replace("{", "").replace("}", "")) for s in labs), default=2)
    return L * 0.6 * style["tick_font_pt"]


def axes_rect(cell, sp, style):
    """Frame rect in pt (x0, y0, x1, y1, top-left origin) inside a chart cell."""
    fs = style["font_pt"]
    x0, y0, x1, y1 = cell
    left = _est_label_w(sp, style, "y") + 2.2 * fs + 6 + max(0.0, -style["label_offset_pt"][1])
    right = 10 + (0.5 * _est_label_w(sp, style, "x"))
    top = 8 + (2.6 * fs if style["caption"].endswith("above") else 0) + (1.6 * fs if style["axis_title_pos"] == "top" else 0)
    bottom = 2.6 * fs + 10 + (2.0 * fs if style["caption"].endswith("below") else 0)
    return (x0 + left, y0 + top, x1 - right, y1 - bottom)


# ----------------------------------------------------------------------------- drawing
def _fig_rect(r):
    x0, y0, x1, y1 = r
    return [x0 / PAGE_W, 1 - y1 / PAGE_H, (x1 - x0) / PAGE_W, (y1 - y0) / PAGE_H]


def _bbox_pt(artist, renderer):
    """Artist extent in pt, top-left origin."""
    b = artist.get_window_extent(renderer)
    return [float(b.x0), float(PAGE_H - b.y1), float(b.x1), float(PAGE_H - b.y0)]


def _union(bbs):
    bbs = [b for b in bbs if b is not None]
    return [min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs)]


def draw_chart(fig, cell, sp, style, number, rng, *, probe=False, caption_only=False, caption_anchor=None):
    """Draw one chart. Returns geometry/record dict. ``rng`` drives label placement only."""
    renderer = fig.canvas.get_renderer()
    fs, tfs = style["font_pt"], style["tick_font_pt"]
    fam = style["font"]
    rect = axes_rect(cell, sp, style)
    rec = {"number": number, "cell_pt": list(cell), "plot_box_pt": list(rect), "texts": {}, "probe_colors": {}}
    cap = _caption_text(sp, style, number)
    rec["caption"] = cap
    if caption_only:
        rec["texts"]["caption"] = _draw_caption(fig, rect, sp, style, cap, renderer, anchor=caption_anchor)
        return rec
    ax = fig.add_axes(_fig_rect(rect))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_facecolor("none")
    # gridlines and tick marks drawn at normalised positions of the real tick values
    xu = {k: [float(to_norm(sp["x"], v)) for v in sp["x"][k]] for k in ("major", "minor")}
    yu = {k: [float(to_norm(sp["y"], v)) for v in sp["y"][k]] for k in ("major", "minor")}
    ax.set_xticks([u for u in xu["major"] if -1e-9 <= u <= 1 + 1e-9])
    ax.set_yticks([u for u in yu["major"] if -1e-9 <= u <= 1 + 1e-9])
    ax.set_xticks([u for u in xu["minor"] if 0 < u < 1], minor=True)
    ax.set_yticks([u for u in yu["minor"] if 0 < u < 1], minor=True)
    tl = 0 if style["tick_dir"] == "none" else 3.5
    ax.tick_params(which="major", direction=style["tick_dir"] if tl else "in", length=tl, width=style["frame_lw"] * 0.8,
                   labelbottom=False, labelleft=False, top=style["frame"] != "open", right=style["frame"] != "open")
    ax.tick_params(which="minor", direction=style["tick_dir"] if tl else "in", length=tl * 0.55,
                   width=style["frame_lw"] * 0.6, top=style["frame"] != "open", right=style["frame"] != "open")
    g = str(style["grid_grey"])
    gls = {"solid": "-", "dotted": ":", "dashed": "--"}.get(style["grid_major"])
    if gls:
        ax.grid(True, which="major", ls=gls, lw=style["grid_lw"], color=g, zorder=0.5)
        if style["grid_minor"] != "none":
            ax.grid(True, which="minor", ls=gls if gls != "-" else "-", lw=style["grid_lw"] * 0.7,
                    color=str(min(0.92, style["grid_grey"] + 0.1)), zorder=0.4)
    ax.set_axisbelow(True)
    for name, spn in ax.spines.items():
        spn.set_linewidth(style["frame_lw"])
        spn.set_zorder(4)
        if style["frame"] == "open" and name in ("top", "right"):
            spn.set_visible(False)
    rec["texts"]["xticks"], rec["applied_labels"] = _tick_labels(ax, sp, style, "x", rng)
    rec["texts"]["yticks"], lab_y = _tick_labels(ax, sp, style, "y", rng)
    rec["applied_labels"].update(lab_y)
    xt_bb = _union([_bbox_pt(t, renderer) for t in rec["texts"]["xticks"]]) if rec["texts"]["xticks"] else [rect[0], rect[3], rect[2], rect[3] + 2]
    yt_bb = _union([_bbox_pt(t, renderer) for t in rec["texts"]["yticks"]]) if rec["texts"]["yticks"] else [rect[0] - 2, rect[1], rect[0], rect[3]]
    rec["texts"]["titles"] = _axis_titles(ax, sp, style, rect, xt_bb, yt_bb, fam, fs)
    # curves
    stys = curve_styles(style, len(sp["curves"]))
    handles = []
    merged_groups = _merge_groups(sp, style, rng)
    rec["vector_structure"] = style["vector_structure"] if not (style["vector_structure"] == "merged" and not merged_groups) else "single"
    drawn_final = {}
    in_group = {i: grp for grp in merged_groups for i in grp}
    vs = rec["vector_structure"]
    for i, c in enumerate(sp["curves"]):
        st = dict(stys[min(in_group[i])]) if i in in_group else dict(stys[i])
        c["_style"] = st
        pieces = c["drawn"]
        if vs == "bezier":
            pieces = [_bezier_pieces(pc) for pc in pieces]
        elif style["pdf_clip"] == "pre_clipped":
            pieces = [q for pc in pieces for q in G.clip_polyline(pc)]
        drawn_final[i] = pieces
        handles.append(Line2D([], [], color=st["color"], ls=st["ls"], lw=st["lw"]))
        if i in in_group:
            continue
        rec["probe_colors"][probe_color(i)] = [str(c["label"])]
        kw = dict(color=probe_color(i) if probe else st["color"], ls=st["ls"], lw=st["lw"], transform=ax.transAxes,
                  zorder=3, clip_on=style["pdf_clip"] != "pre_clipped" or vs == "bezier")
        _draw_pieces(ax, pieces, vs, kw, st, rng, fig)
    for grp in merged_groups:
        segs = []
        for i in grp:
            for pc in drawn_final[i]:
                segs += [pc, np.full((1, 2), np.nan)]
        st = dict(stys[min(grp)])
        rec["probe_colors"][probe_color(min(grp))] = [str(sp["curves"][i]["label"]) for i in grp]
        uv = np.vstack(segs[:-1])
        ax.add_line(Line2D(uv[:, 0], uv[:, 1], color=probe_color(min(grp)) if probe else st["color"], ls=st["ls"],
                           lw=st["lw"], transform=ax.transAxes, zorder=3, clip_on=style["pdf_clip"] != "pre_clipped"))
    rec["merged_groups"] = [[str(sp["curves"][i]["label"]) for i in grp] for grp in merged_groups]
    rec["curve_styles"] = {str(c["label"]): {k: (list(v) if isinstance(v, tuple) else v) for k, v in c["_style"].items()}
                           for c in sp["curves"]}
    if style["markers"]:
        _markers(ax, sp, drawn_final, stys, probe)
    rec["drawn"] = {str(sp["curves"][i]["label"]): [np.asarray(pc["dense"] if isinstance(pc, dict) else pc)
                                                  for pc in drawn_final[i]] for i in drawn_final}
    # identity
    rec["texts"]["curve_labels"] = []
    if style["identity"] == "legend" and len(sp["curves"]) > 1 or (style["identity"] == "legend" and sp["curves"][0]["printed"]):
        rec["texts"]["legend"] = _legend(ax, sp, style, handles, drawn_final, fam, fs)
    elif style["identity"] in ("inplot", "inplot_leader") and sp["curves"][0]["printed"]:
        rec["texts"]["curve_labels"] = _inplot_labels(ax, sp, style, drawn_final, rng, renderer, fam, fs)
    rec["distractors"] = _distractors(ax, sp, style, rng, fam, fs)
    if style.get("conditions_in_plot") and sp.get("conditions"):
        leg = rec["texts"].get("legend")
        rec["texts"]["conditions"] = _conditions(ax, sp, drawn_final, fam, fs, avoid=getattr(leg, "_synth_loc", None))
    t = _draw_caption(fig, rect, sp, style, cap, renderer, xt_bb=xt_bb, titles=rec["texts"]["titles"], cell=cell)
    _fit_width(t, cell, renderer)
    rec["texts"]["caption"] = t
    return rec


def _fit_width(t, cell, renderer):
    """Shrink a caption until it stays inside its own cell (it must not run under a neighbour)."""
    for _ in range(12):
        b = _bbox_pt(t, renderer)
        if b[0] >= cell[0] - 2 and b[2] <= cell[2] + 2:
            return
        if t.get_ha() == "left" and b[2] > cell[2] + 2:
            t.set_x(max(cell[0], b[0] - (b[2] - cell[2] - 2)) / PAGE_W)
        t.set_fontsize(t.get_fontsize() * 0.93)


def _draw_pieces(ax, pieces, vs, kw, st, rng, fig):
    for pc in pieces:
        if vs == "split":
            k = int(rng.integers(2, 9))
            for a in range(0, len(pc) - 1, k - 1):
                seg = pc[a:a + k]
                if len(seg) > 1:
                    ax.add_line(Line2D(seg[:, 0], seg[:, 1], **kw))
        elif vs == "filled":
            poly = _outline(ax, pc, st["lw"])
            p = Polygon(poly, closed=True, facecolor=kw["color"], edgecolor="none", transform=ax.transAxes, zorder=3)
            if kw["clip_on"]:
                p.set_clip_path(ax.patch)
            else:
                p.set_clip_on(False)
            ax.add_patch(p)
        elif vs == "bezier":
            verts, codes = pc["verts"], pc["codes"]
            pp = PathPatch(MPath(verts, codes), facecolor="none", edgecolor=kw["color"], lw=kw["lw"], ls=kw["ls"],
                           transform=ax.transAxes, zorder=3)
            pp.set_clip_path(ax.patch)
            ax.add_patch(pp)
        else:
            ax.add_line(Line2D(pc[:, 0], pc[:, 1], **kw))


def _outline(ax, pc, lw_pt):
    """Filled-outline stroke: offset the centreline by +-lw/2 pt, in axes units."""
    bb = ax.get_position()
    sx, sy = bb.width * PAGE_W, bb.height * PAGE_H
    p = pc * [sx, sy]
    d = np.gradient(p, axis=0)
    nrm = np.c_[-d[:, 1], d[:, 0]]
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    h = lw_pt / 2
    left, right = (p + h * nrm) / [sx, sy], (p - h * nrm) / [sx, sy]
    return np.vstack([left, right[::-1]])


def _bezier_pieces(pc, n_knots=None):
    """Catmull-Rom -> cubic Bezier through knots taken from the drawn polyline. The GT for the
    curve is then the exact Bezier (evaluated densely), not the polyline it was seeded from."""
    n = len(pc)
    k = n_knots or max(6, min(40, n // 12))
    idx = np.unique(np.linspace(0, n - 1, k).round().astype(int))
    P = pc[idx]
    verts, codes = [P[0]], [MPath.MOVETO]
    dense = [P[:1]]
    t = np.linspace(0, 1, 40)[1:, None]
    for i in range(len(P) - 1):
        p0, p1, p2, p3 = P[max(i - 1, 0)], P[i], P[i + 1], P[min(i + 2, len(P) - 1)]
        c1, c2 = p1 + (p2 - p0) / 6, p2 - (p3 - p1) / 6
        verts += [c1, c2, p2]
        codes += [MPath.CURVE4] * 3
        dense.append((1 - t) ** 3 * p1 + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * p2)
    return {"verts": np.array(verts), "codes": codes, "dense": np.vstack(dense)}


def _merge_groups(sp, style, rng):
    if style["vector_structure"] != "merged" or len(sp["curves"]) < 2:
        return []
    n = len(sp["curves"])
    if n == 2 or rng.random() < 0.5:
        return [list(range(n))]
    i = int(rng.integers(n - 1))
    return [[i, i + 1]]


def _markers(ax, sp, drawn, stys, probe):
    marks = ["o", "s", "^", "v", "D", "x", "+"]
    for i, pieces in drawn.items():
        for pc in pieces:
            pc = pc["dense"] if isinstance(pc, dict) else pc
            vis = [q for q in G.clip_polyline(pc)]
            for q in vis:
                idx = np.linspace(0, len(q) - 1, max(3, min(12, len(q) // 25))).round().astype(int)
                ax.plot(q[idx, 0], q[idx, 1], ls="none", marker=marks[i % len(marks)], ms=3.2,
                        mfc="none" if i % 2 else stys[i]["color"], mec=stys[i]["color"], mew=0.6,
                        transform=ax.transAxes, zorder=3.2)


# ----------------------------------------------------------------------------- texts
def _txt(fs, fam):
    return {"fontsize": fs, "fontfamily": fam}


def _tick_labels(ax, sp, style, k, rng):
    """Manual tick labels (so offsets, formats, dropped / contradicting labels are controlled)."""
    axd = sp[k]
    vals = [v for v in axd["major"]]
    log = is_log(axd)
    fmt = style[f"{k}_tick_format"]
    dec = decimals_for(vals) if not log else None
    every = style["label_every"]
    labs = list(enumerate(vals))[::every] if not log else list(enumerate(vals))
    if style["drop_edge_label"] == "first" and len(labs) > 3:
        labs = labs[1:]
    if style["drop_edge_label"] == "last" and len(labs) > 3:
        labs = labs[:-1]
    off = style["label_offset_pt"][0 if k == "x" else 1]
    applied = {}
    contra = None
    if style.get("contradicting_label") and len(labs) >= 4 and k == ("x" if rng.random() < 0.5 else "y"):
        contra = int(rng.integers(1, len(labs) - 1))
    pad = 3.0 + (3.5 if style["tick_dir"] in ("out", "inout") else 0)
    out = []
    for j, (i, v) in enumerate(labs):
        s = fmt_tick(v, fmt, log=log, decimals=dec)
        if contra == j:
            step = (vals[1] - vals[0]) if not log else None
            wrong = (v - step / 2) if step else v * 3
            s = fmt_tick(wrong, fmt, log=log, decimals=None if log else dec + 1)
            applied[f"contradicting_label_{k}"] = {"true": v, "printed": s}
        u = float(to_norm(axd, v))
        jit = float(rng.uniform(-1, 1)) * style["label_jitter_pt"] if style["label_jitter_pt"] else 0.0
        if k == "x":
            t = ax.annotate(s, xy=(u, 0), xycoords="axes fraction", xytext=(off + jit, -pad), textcoords="offset points",
                            ha="center", va="top", **_txt(style["tick_font_pt"], style["font"]))
        else:
            t = ax.annotate(s, xy=(0, u), xycoords="axes fraction", xytext=(-pad, off + jit), textcoords="offset points",
                            ha="right", va="center", **_txt(style["tick_font_pt"], style["font"]))
        t.set_annotation_clip(False)
        out.append(t)
    if off:
        applied[f"label_offset_{k}_pt"] = off
    return out, applied


def _axis_titles(ax, sp, style, rect, xt_bb, yt_bb, fam, fs):
    x0, y0, x1, y1 = rect
    out = []
    xt, yt = sp["x"]["title"], sp["y"]["title"]
    below = xt_bb[3] - y1 + 3
    left = x0 - yt_bb[0] + 3
    pos = style["axis_title_pos"]
    if pos == "end":
        out.append(ax.annotate(xt, xy=(1, 0), xycoords="axes fraction", xytext=(0, -below), textcoords="offset points",
                               ha="right", va="top", **_txt(fs, fam)))
    else:
        out.append(ax.annotate(xt, xy=(0.5, 0), xycoords="axes fraction", xytext=(0, -below), textcoords="offset points",
                               ha="center", va="top", **_txt(fs, fam)))
    if pos == "top":
        out.append(ax.annotate(yt, xy=(0, 1), xycoords="axes fraction", xytext=(-min(left, 20), 4), textcoords="offset points",
                               ha="left", va="bottom", **_txt(fs, fam)))
    else:
        out.append(ax.annotate(yt, xy=(0, 0.5 if pos == "center" else 1.0), xycoords="axes fraction", xytext=(-left, 0),
                               textcoords="offset points", ha="right", va="center" if pos == "center" else "top",
                               rotation=90, **_txt(fs, fam)))
    for t in out:
        t.set_annotation_clip(False)
    return out


def _caption_text(sp, style, number):
    c = style["caption"]
    if c == "diagram_above":
        return f"Diagram {number}: {sp['title']}"
    if c == "fig_below":
        return f"Fig. {number} {sp['title']}"
    return f"Figure {number}. {sp['title']}"


def _draw_caption(fig, rect, sp, style, cap, renderer, xt_bb=None, titles=None, anchor=None, cell=None):
    x0, y0, x1, y1 = rect
    fs = style["font_pt"] * 1.05
    if anchor is None and style["caption"] == "diagram_above" and cell is not None:
        anchor = ((cell[0] + 2) / PAGE_W, 1 - (cell[1] + 2) / PAGE_H, "left", "top", fs)
    if anchor is not None:
        t = fig.text(anchor[0], anchor[1], cap, ha=anchor[2], va=anchor[3], fontsize=anchor[4], fontfamily=style["font"],
                     fontweight="bold")
    elif style["caption"].endswith("above"):
        y = y0 - 5 - (1.6 * style["font_pt"] if style["axis_title_pos"] == "top" else 0)
        t = fig.text(x0 / PAGE_W, 1 - y / PAGE_H, cap, ha="left", va="bottom", fontsize=fs, fontfamily=style["font"],
                     fontweight="bold")
    else:
        if not titles:
            yb = y1 + 2.6 * style["font_pt"] + 8
        else:
            yb = max(_bbox_pt(t, renderer)[3] for t in titles)
        t = fig.text((x0 + x1) / 2 / PAGE_W, 1 - (yb + 4) / PAGE_H, cap, ha="center", va="top", fontsize=fs,
                     fontfamily=style["font"], fontweight="bold")
    return t


def _occupancy(drawn, corner_boxes):
    pts = np.vstack([(pc["dense"] if isinstance(pc, dict) else pc) for pieces in drawn.values() for pc in pieces])
    return [int(((pts[:, 0] > a) & (pts[:, 0] < c) & (pts[:, 1] > b) & (pts[:, 1] < d)).sum()) for a, b, c, d in corner_boxes]


CORNERS = {"upper right": (0.55, 0.6, 1, 1), "upper left": (0, 0.6, 0.45, 1), "lower right": (0.55, 0, 1, 0.4),
           "lower left": (0, 0, 0.45, 0.4), "center right": (0.55, 0.3, 1, 0.7), "upper center": (0.3, 0.6, 0.7, 1)}


def _legend(ax, sp, style, handles, drawn, fam, fs):
    occ = _occupancy(drawn, list(CORNERS.values()))
    loc = list(CORNERS)[int(np.argmin(occ))]
    labels = [c["printed"] or str(c["label"]) for c in sp["curves"]]
    leg = ax.legend(handles, labels, loc=loc, fontsize=fs * 0.95, frameon=True,
                    framealpha=1.0 if style["legend_opaque"] else 0.0, edgecolor="black" if style["legend_opaque"] else "none",
                    fancybox=False, prop={"family": fam, "size": fs * 0.95}, handlelength=2.2)
    leg.set_zorder(2.5)
    leg._synth_loc = loc
    return leg


def _inplot_labels(ax, sp, style, drawn, rng, renderer, fam, fs):
    out = []
    placed = []
    bb_ax = ax.get_window_extent(renderer)
    all_vis = {i: [q for pc in drawn[i] for q in G.clip_polyline(pc["dense"] if isinstance(pc, dict) else pc)]
               for i in drawn}
    allpts = np.vstack([G.resample_arclength(q, per_unit=300, min_n=2) for v in all_vis.values() for q in v]) \
        if any(all_vis.values()) else np.zeros((0, 2))
    for i, c in enumerate(sp["curves"]):
        vis = all_vis[i]
        if not vis:
            continue
        pc = max(vis, key=len)
        others = [q for j, v in all_vis.items() if j != i for q in v]
        best = None
        for _ in range(40):
            k = int(rng.integers(int(len(pc) * 0.15), max(int(len(pc) * 0.15) + 1, int(len(pc) * 0.92))))
            p = pc[k]
            d_other = min((G.seg_dist(p[None], q)[0] for q in others), default=1.0)
            leader = style["identity"] == "inplot_leader"
            ang = float(rng.uniform(0, 2 * np.pi))
            r = float(rng.uniform(18, 45)) if leader else float(rng.uniform(4, 8))
            if style.get("annotation_cross") and leader and others:
                q = others[int(rng.integers(len(others)))]
                tgt = q[int(rng.integers(len(q)))]
                v = tgt - p
                ang = math.atan2(v[1] * bb_ax.height, v[0] * bb_ax.width) + float(rng.uniform(-0.2, 0.2))
                r = float(rng.uniform(25, 55))
            dx, dy = r * math.cos(ang), r * math.sin(ang)
            t = ax.annotate(c["printed"], xy=(p[0], p[1]), xycoords="axes fraction", xytext=(dx, dy),
                            textcoords="offset points", ha="center", va="center", zorder=2.6,
                            arrowprops=dict(arrowstyle="-|>" if leader else "-", lw=0.5, color="black",
                                            shrinkA=0, shrinkB=0, mutation_scale=6) if leader else None,
                            bbox=dict(boxstyle="square,pad=0.1", fc="white", ec="none"), **_txt(fs * 0.95, fam))
            b = t.get_window_extent(renderer)
            inside = b.x0 > bb_ax.x0 + 1 and b.x1 < bb_ax.x1 - 1 and b.y0 > bb_ax.y0 + 1 and b.y1 < bb_ax.y1 - 1
            clash = any(b.overlaps(o) for o in placed)
            fx0, fx1 = (b.x0 - bb_ax.x0) / bb_ax.width, (b.x1 - bb_ax.x0) / bb_ax.width
            fy0, fy1 = (b.y0 - bb_ax.y0) / bb_ax.height, (b.y1 - bb_ax.y0) / bb_ax.height
            covered = int(((allpts[:, 0] > fx0) & (allpts[:, 0] < fx1) & (allpts[:, 1] > fy0) & (allpts[:, 1] < fy1)).sum())
            score = (inside and not clash, -covered, d_other)
            if best is None or score > best[0]:
                if best is not None:
                    best[1].remove()
                best = (score, t, b)
            else:
                t.remove()
            if inside and not clash and covered == 0 and d_other > 0.06:
                break
        placed.append(best[2])
        out.append(best[1])
    return out


def _distractors(ax, sp, style, rng, fam, fs):
    rec = []
    if style.get("reference_line"):
        u = float(rng.uniform(0.25, 0.75))
        horiz = rng.random() < 0.6
        (ax.axhline if horiz else ax.axvline)(u, ls="--", lw=0.8, color="#cc0000", zorder=2.8)
        ax.annotate("ref.", xy=(0.02 if horiz else u, u if horiz else 0.03), xycoords="axes fraction",
                    xytext=(2, 2), textcoords="offset points", fontsize=fs * 0.8, color="#cc0000", fontfamily=fam)
        rec.append({"type": "reference_line", "horizontal": horiz, "u": u})
    if style.get("annotation_cross") and style["identity"] != "inplot_leader":
        a = np.array([float(rng.uniform(0.1, 0.4)), float(rng.uniform(0.6, 0.9))])
        b = np.array([float(rng.uniform(0.5, 0.9)), float(rng.uniform(0.1, 0.4))])
        ax.annotate(str(rng.choice(["Operation limited", "Region A", "Pulsed", "typ."])), xy=b, xycoords="axes fraction",
                    xytext=a, textcoords="axes fraction", fontsize=fs * 0.85, fontfamily=fam, zorder=2.7,
                    arrowprops=dict(arrowstyle="->", lw=0.6, color="black"))
        rec.append({"type": "annotation_arrow", "from": a.tolist(), "to": b.tolist()})
    return rec


def _conditions(ax, sp, drawn, fam, fs, avoid=None):
    boxes = {"lower right": (0.6, 0.02, 0.98, 0.18), "upper left": (0.02, 0.82, 0.4, 0.98),
             "lower left": (0.02, 0.02, 0.4, 0.18), "upper right": (0.6, 0.82, 0.98, 0.98)}
    if avoid:
        vert = "upper" if "upper" in avoid else ("lower" if "lower" in avoid else "")
        hor = "right" if "right" in avoid else ("left" if "left" in avoid else "")
        boxes = {k: v for k, v in boxes.items() if vert and vert not in k} or \
            {k: v for k, v in boxes.items() if not ((not vert or vert in k) and (not hor or hor in k))}
    occ = _occupancy(drawn, list(boxes.values()))
    k = list(boxes)[int(np.argmin(occ))]
    x = 0.97 if "right" in k else 0.03
    y = 0.04 if "lower" in k else 0.96
    return ax.text(x, y, sp["conditions"], transform=ax.transAxes, ha="right" if "right" in k else "left",
                   va="bottom" if "lower" in k else "top", fontsize=fs * 0.85, fontfamily=fam, zorder=2.6,
                   bbox=dict(boxstyle="square,pad=0.15", fc="white", ec="none"))


# ----------------------------------------------------------------------------- page
def new_page():
    plt.rcParams.update(RC)
    return plt.figure(figsize=(PAGE_W / 72, PAGE_H / 72), dpi=72)


def cell_rules(fig, cells, gap):
    """Infineon-style table grid: every chart cell boxed by thin rules."""
    from matplotlib.patches import Rectangle
    for x0, y0, x1, y1 in cells:
        e = min(6.0, gap / 2 - 1)
        fig.add_artist(Rectangle(((x0 - e) / PAGE_W, 1 - (y1 + e) / PAGE_H), (x1 - x0 + 2 * e) / PAGE_W,
                                 (y1 - y0 + 2 * e) / PAGE_H, transform=fig.transFigure, fill=False, lw=0.6,
                                 edgecolor="black"))


def page_furniture(fig, page, part, page_no):
    if page["header"]:
        fig.text(50 / PAGE_W, 1 - 40 / PAGE_H, f"{part}   N-Channel Power MOSFET", fontsize=9, fontweight="bold",
                 ha="left", va="bottom", fontfamily="DejaVu Sans")
        fig.text(1 - 50 / PAGE_W, 1 - 40 / PAGE_H, "Datasheet  Rev. 1.2", fontsize=8, ha="right", va="bottom",
                 fontfamily="DejaVu Sans")
    if page["footer"]:
        fig.text(0.5, 25 / PAGE_H, f"Page {page_no}      www.example-semiconductor.com      © 2026",
                 fontsize=7, ha="center", va="bottom", fontfamily="DejaVu Sans")


def text_boxes(rec, renderer):
    """All text extents of a chart (pt, top-left), for crop and tick-label checks."""
    out = {}
    for k, v in rec["texts"].items():
        if v is None:
            continue
        items = v if isinstance(v, list) else [v]
        out[k] = [_bbox_pt(t, renderer) for t in items]
    return out


def art_bbox(rec, boxes):
    x0, y0, x1, y1 = rec["plot_box_pt"]
    bbs = [[x0 - 4, y0 - 4, x1 + 4, y1 + 4]] + [b for k, v in boxes.items() if k != "caption" for b in v]
    return _union(bbs)


def embed_image(fig, bbox_pt, img):
    x0, y0, x1, y1 = bbox_pt
    ax = fig.add_axes(_fig_rect((x0, y0, x1, y1)))
    ax.imshow(img, extent=(0, 1, 0, 1), aspect="auto", interpolation="nearest", transform=ax.transAxes)
    ax.set_axis_off()
    return ax


def to_mtrans():  # keep the import referenced for linters; transforms are used implicitly
    return mtrans.IdentityTransform()
