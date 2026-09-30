"""Generate a synthetic chart set: PDFs (vector, 1-4 charts per page), PNG case crops, exact GT.

Layout of ``--out``::

    pdf/page_NNNN.pdf          final datasheet-like page (what dsdig reads)
    probe/page_NNNN.pdf        same page, curves in unique probe colours (vector check only)
    img/<case_id>.png          case image (single chart crop, possibly degraded)
    cases.json                 case list, scorer schema + factors (image paths relative to --out)
    factors.csv                one row per case, every sampled factor
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from . import gt as G
from . import raster as R
from . import render as RD
from .models import MODEL_TRAPS, MODELS
from .styles import TIERS, sample_page, sample_style

GENERATOR = "tools/synth_charts"
DEFAULT_MIX = {  # class -> share of the set
    "capacitance": 60, "transfer": 50, "output": 30, "rdson_temperature": 30, "rdson_id": 30,
    "gate_charge": 40, "body_diode": 35, "reverse_leakage": 30, "core_loss": 25, "mlcc_impedance": 25,
    "breakdown": 20, "zth": 25,
}
TIER_MIX = {"easy": 0.2, "medium": 0.3, "hard": 0.3, "adversarial": 0.2}


def git_rev():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent).stdout.strip()
    except OSError:
        return "unknown"


def plan_cases(n: int, seed: int, classes=None):
    """Deterministic (class, tier) schedule with the class and tier mixes above."""
    mix = {k: v for k, v in DEFAULT_MIX.items() if not classes or k in classes}
    tot = sum(mix.values())
    cls_list = []
    for k, v in mix.items():
        cls_list += [k] * int(round(n * v / tot))
    rng = np.random.default_rng([seed, 999])
    while len(cls_list) < n:
        cls_list.append(list(mix)[int(rng.integers(len(mix)))])
    cls_list = cls_list[:n]
    rng.shuffle(cls_list)
    tiers = []
    for t, f in TIER_MIX.items():
        tiers += [t] * int(round(n * f))
    tiers = (tiers + ["medium"] * n)[:n]
    rng.shuffle(tiers)
    return list(zip(cls_list, tiers))


def make_case_spec(seed: int, i: int, cls: str, tier: str):
    rng = np.random.default_rng([seed, i, 1])
    style_probe = TIERS.index(tier)
    p = [0.0, 0.2, 0.5, 0.8][style_probe]
    traps = frozenset(t for t in MODEL_TRAPS.get(cls, []) if rng.random() < p)
    if cls == "capacitance" and "near_merge" in traps and "lin_y_rail" in traps:
        traps = traps - {"lin_y_rail"}
    spec = MODELS[cls](rng, traps)
    style = sample_style(rng, tier, spec)
    return spec, style


def _gt_curves(spec_prep, rec):
    """Visible GT per curve from what was actually drawn (clipped to the frame, exact)."""
    out, uv_all, dropped = [], {}, []
    for c in spec_prep["curves"]:
        lab = str(c["label"])
        pieces = [q for pc in rec["drawn"][lab] for q in G.clip_polyline(pc)]
        if not pieces:
            dropped.append(lab)
            continue
        uv = np.vstack([G.douglas_peucker(q) for q in pieces])
        x, y = G.denorm(spec_prep, uv)
        out.append({"label": c["label"], "printed": c["printed"], "x": [float(f"{v:.10g}") for v in x],
                    "y": [float(f"{v:.10g}") for v in y], "n_pieces": len(pieces)})
        uv_all[lab] = uv
    return out, uv_all, dropped


def _axis_out(ax):
    return {"min": ax["min"], "max": ax["max"], "scale": "log10" if ax["scale"].startswith("log") else "linear",
            "unit": ax["unit"], "title": ax["title"]}


def _json_safe(o):
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return _json_safe(o.tolist())
    return o


def render_page(out: Path, seed: int, page_idx: int, items, page, *, probe_dir=True):
    """items: list of (case_index, cls, tier, spec, style). Writes pdf (+ probe). Returns records."""
    part = f"SYN{seed:02d}{page_idx:04d}N"
    cells = RD.layout_cells(page, len(items))
    embed_dpi = 150 + 50 * (page_idx % 2)

    def one_pass(probe=False, embed=None):
        fig = RD.new_page()
        RD.page_furniture(fig, page, part, page_idx + 1)
        if page.get("cell_rules"):
            RD.cell_rules(fig, cells, page["gap_pt"])
        recs = []
        renderer = fig.canvas.get_renderer()
        for j, (ci, cls, tier, spec, style) in enumerate(items):
            sp = RD.prepare(spec, style, np.random.default_rng([seed, ci, 2]))
            number = 3 + j + 4 * (page_idx % 3)
            emb = embed is not None and j in embed
            rec = RD.draw_chart(fig, cells[j], sp, style, number, np.random.default_rng([seed, ci, 3]),
                                probe=probe, caption_only=emb, caption_anchor=embed[j][2]["caption_anchor"] if emb else None)
            if emb:
                RD.embed_image(fig, embed[j][1], embed[j][0])
                rec.update(embed[j][2])
            else:
                rec["boxes"] = RD.text_boxes(rec, renderer)
                rec["art_bbox_pt"] = RD.art_bbox(rec, rec["boxes"])
                rec["boxes"]["caption"] = [RD._bbox_pt(rec["texts"]["caption"], renderer)]
                t = rec["texts"]["caption"]
                rec["caption_anchor"] = (*t.get_position(), t.get_ha(), t.get_va(), t.get_fontsize())
            rec["sp"] = sp
            recs.append(rec)
        return fig, recs

    pdf = out / "pdf" / f"page_{page_idx:04d}.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    fig, recs = one_pass()
    fig.savefig(pdf, dpi=72, metadata={"Producer": "synth_charts", "CreationDate": None, "ModDate": None})
    RD.plt.close(fig)
    embed = {}
    for j, (ci, cls, tier, spec, style) in enumerate(items):
        if style["raster_embed"]:
            ab = recs[j]["art_bbox_pt"]
            img, origin, s = R.render_clip(pdf, 0, ab, embed_dpi)
            eops = None
            if style["degrade"]:  # the PDF's own raster is scan-damaged too (photometric ops only)
                eops = [o for o in R.sample_degradation(np.random.default_rng([seed, ci, 6]), tier, embed_dpi)
                        if o["op"] != "rotate"]
                img, _ = R.apply_degradation(img, eops, seed * 7919 + ci)
            h, w = img.shape[:2]
            bb = [origin[0] / s, origin[1] / s, (origin[0] + w) / s, (origin[1] + h) / s]
            keep = {k: copy.deepcopy(recs[j][k]) for k in ("boxes", "art_bbox_pt", "plot_box_pt", "drawn", "applied_labels",
                                           "probe_colors", "vector_structure", "distractors", "merged_groups",
                                           "caption_anchor", "curve_styles")
                    if k in recs[j]}
            keep["embed"] = {"dpi": embed_dpi, "bbox_pt": bb, "degradation": eops}
            embed[j] = (img, bb, keep)
    if embed:
        fig, recs2 = one_pass(embed=embed)
        for j in embed:
            recs2[j]["boxes"]["caption"] = [RD._bbox_pt(recs2[j]["texts"]["caption"], fig.canvas.get_renderer())]
        fig.savefig(pdf, dpi=72, metadata={"Producer": "synth_charts", "CreationDate": None, "ModDate": None})
        RD.plt.close(fig)
        recs = recs2
    if probe_dir:
        ppdf = out / "probe" / pdf.name
        ppdf.parent.mkdir(parents=True, exist_ok=True)
        fig, _ = one_pass(probe=True, embed=embed or None)
        fig.savefig(ppdf, dpi=72, metadata={"Producer": "synth_charts", "CreationDate": None, "ModDate": None})
        RD.plt.close(fig)
    return pdf, recs, part


def build_case(out: Path, seed: int, set_name: str, pdf: Path, page_idx: int, rec, item, rev: str, page, part):
    ci, cls, tier, spec, style = item
    sp = rec["sp"]
    curves, uv_all, dropped = _gt_curves(sp, rec)
    cid = f"{set_name}_{ci:04d}_{cls}"
    # crop: chart art + caption, with a margin; neighbours may intrude (panel isolation)
    crop_pt = RD._union([rec["art_bbox_pt"]] + rec["boxes"]["caption"])
    m = 5.0
    crop_pt = [crop_pt[0] - m, crop_pt[1] - m, crop_pt[2] + m, crop_pt[3] + m]
    img, origin, s = R.render_clip(pdf, 0, crop_pt, style["dpi"])
    box_px = R.pt_to_px(rec["plot_box_pt"], origin, s)
    deg = None
    if style["degrade"]:
        rng = np.random.default_rng([seed, ci, 4])
        ops = R.sample_degradation(rng, tier, style["dpi"])
        img, A = R.apply_degradation(img, ops, seed * 100003 + ci)
        deg = {"ops": ops, "affine": np.round(A, 8).tolist()}
    ipath = out / "img" / f"{cid}.png"
    ipath.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(ipath, optimize=True)
    stats = G.difficulty_stats(uv_all)
    applied = {**sp.get("applied", {}), **rec.get("applied_labels", {})}
    factors = {k: v for k, v in style.items() if k not in ("model_trap_prob",)}
    factors.update({
        "cls": cls, "n_curves": len(curves), "x_scale": sp["x"]["scale"], "y_scale": sp["y"]["scale"],
        "model_traps": spec["traps"], "vector_structure_drawn": rec.get("vector_structure"),
        "merged_groups": rec.get("merged_groups", []), "distractors": [d["type"] for d in rec.get("distractors", [])],
        "raster_embed_dpi": (rec.get("embed") or {}).get("dpi"), "applied": applied,
        "embed_degraded": bool((rec.get("embed") or {}).get("degradation")),
        "panels_on_page": page["panels"], "page_gap_pt": page["gap_pt"], "cell_rules": bool(page.get("cell_rules")),
        "curve_ends_mid_plot": _ends_mid(uv_all), "hug_rail": _hug(uv_all), "clipped_at_frame": _clipped(sp, rec),
        "dropped_invisible": dropped, "degrade_ops": [o["op"] for o in (deg or {}).get("ops", [])], **stats,
    })
    case = {
        "id": cid, "class": cls, "gt_status": "synthetic_exact",
        "gt_provenance": {
            "generator": GENERATOR, "generator_rev": rev, "seed": seed, "index": ci, "set": set_name,
            "pdf": str(pdf.relative_to(out)), "pdf_page": 0, "part": part, "diagram": rec["caption"],
            "plot_box_pt": [round(v, 4) for v in rec["plot_box_pt"]], "crop_pt": [round(v, 4) for v in crop_pt],
            "render": {"dpi": style["dpi"], "origin_px": list(origin), "engine": f"pymupdf {R.fitz.VersionBind}"},
            "pixel_convention": "continuous, origin at top-left corner of pixel (0,0)",
            "axis_min_is": "value at the left (x) / bottom (y) frame edge",
            "model_params": _json_safe(spec["params"]), "degradation": deg,
            "probe_colors": rec["probe_colors"], "curve_styles": _json_safe(rec["curve_styles"]),
        },
        "axis": {"x": _axis_out(sp["x"]), "y": _axis_out(sp["y"])},
        "match": sp["match"] if len(curves) > 1 or sp["match"] != "number" else "single",
        "curves": curves,
        "image": f"img/{cid}.png",
        "plot_box_px": [round(v, 3) for v in box_px],
        "image_size": [int(img.shape[1]), int(img.shape[0])],
        "tier": tier,
        "factors": _json_safe(factors),
        "tick_label_boxes_px": {k: [[round(v, 2) for v in R.pt_to_px(b, origin, s)] for b in rec["boxes"].get(k, [])]
                                for k in ("xticks", "yticks", "titles")},
        "grid_px": {k: [round(float(q), 3) for q in _grid_px(sp, rec, k, origin, s)] for k in ("x", "y")},
    }
    return case


def _grid_px(sp, rec, k, origin, s):
    x0, y0, x1, y1 = rec["plot_box_pt"]
    vals = [v for v in sp[k]["major"]]
    u = [float(G.to_norm(sp[k], v)) for v in vals]
    if k == "x":
        return [(x0 + q * (x1 - x0)) * s - origin[0] for q in u]
    return [(y1 - q * (y1 - y0)) * s - origin[1] for q in u]


def _ends_mid(uv_all, tol=0.03):
    n = 0
    for uv in uv_all.values():
        for p in (uv[0], uv[-1]):
            if tol < p[0] < 1 - tol and tol < p[1] < 1 - tol:
                n += 1
                break
    return n


def _hug(uv_all, tol=0.005):
    return any(((uv < tol) | (uv > 1 - tol)).any(1).mean() > 0.1 for uv in uv_all.values())


def _clipped(sp, rec):
    n = 0
    for c in sp["curves"]:
        for pc in rec["drawn"][str(c["label"])]:
            if (pc < -1e-9).any() or (pc > 1 + 1e-9).any():
                n += 1
                break
    return n


def generate(out: Path, n: int, seed: int, set_name: str, classes=None, probe=True, log=print):
    out.mkdir(parents=True, exist_ok=True)
    rev = git_rev()
    plan = plan_cases(n, seed, classes)
    items = []
    for i, (cls, tier) in enumerate(plan):
        spec, style = make_case_spec(seed, i, cls, tier)
        items.append((i, cls, tier, spec, style))
    # group into pages by tier (a page shares the tier of its charts)
    pages = []
    for tier in TIERS:
        todo = [it for it in items if it[2] == tier]
        k = 0
        while k < len(todo):
            page = sample_page(np.random.default_rng([seed, 5000 + len(pages)]), tier)
            grp = todo[k:k + page["panels"]]
            # one caption convention per page (a datasheet uses one); Infineon-style "Diagram N:"
            # pages put every chart in a ruled table cell
            cap = grp[0][4]["caption"]
            for it in grp:
                it[4]["caption"] = cap
            page["cell_rules"] = cap == "diagram_above"
            pages.append((page, grp))
            k += page["panels"]
    cases = []
    for p_idx, (page, its) in enumerate(pages):
        page = dict(page, panels=len(its))
        pdf, recs, part = render_page(out, seed, p_idx, its, page, probe_dir=probe)
        for rec, it in zip(recs, its):
            cases.append(build_case(out, seed, set_name, pdf, p_idx, rec, it, rev, page, part))
        if p_idx % 10 == 0:
            log(f"page {p_idx + 1}/{len(pages)}  cases {len(cases)}")
    cases.sort(key=lambda c: c["gt_provenance"]["index"])
    (out / "cases.json").write_text(json.dumps(cases))
    write_factor_table(out / "factors.csv", cases)
    h = hashlib.sha256((out / "cases.json").read_bytes()).hexdigest()
    (out / "MANIFEST.json").write_text(json.dumps({"set": set_name, "seed": seed, "n": len(cases), "generator_rev": rev,
                                                   "cases_sha256": h, "pages": len(pages)}, indent=1))
    return cases


def flat_factors(c):
    f = dict(c["factors"])
    f["id"], f["class"], f["tier"] = c["id"], c["class"], c["tier"]
    for k in ("label_offset_pt",):
        f[k] = max(abs(v) for v in f.get(k, [0, 0]))
    for k, v in list(f.items()):
        if isinstance(v, (list, dict)):
            f[k] = json.dumps(v, sort_keys=True)
    return f


def write_factor_table(path, cases):
    rows = [flat_factors(c) for c in cases]
    keys = sorted({k for r in rows for k in r})
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        w.writerows(rows)
