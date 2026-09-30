"""Markdown report of a dsdig baseline: status by class, accuracy by class, accuracy by factor."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

FACTORS = [
    "tier", "x_scale", "y_scale", "grid_major", "grid_minor", "tick_dir", "frame", "x_tick_format", "y_tick_format",
    "label_offset", "label_jitter", "drop_edge_label", "frame_past_last_label", "contradicting_label",
    "axis_title_pos", "caption", "identity", "legend_opaque", "color_mode", "distinguish", "stroke_bin", "markers",
    "vector_structure_drawn", "pdf_clip", "raster_embed", "embed_degraded", "annotation_cross", "reference_line",
    "curve_on_gridline", "signed_axis", "panels_on_page", "tight_page", "n_curves", "crossings", "shallow_angle",
    "near_merged", "hug_rail", "curve_ends_mid_plot", "font_serif", "font_bin", "model_trap", "label_fmt",
    "cell_rules",
]


def case_factors(c):
    f = dict(c["factors"])
    f["tier"] = c["tier"]
    f["label_offset"] = max(abs(v) for v in f["label_offset_pt"]) > 0
    f["label_jitter"] = f["label_jitter_pt"] > 0
    f["stroke_bin"] = "<=0.45pt" if f["stroke_pt"] <= 0.45 else ("<=1pt" if f["stroke_pt"] <= 1.0 else ">1pt")
    f["tight_page"] = f["page_gap_pt"] < 20 and f["panels_on_page"] > 1
    f["crossings"] = "0" if not f["n_crossings"] else ("1-2" if f["n_crossings"] <= 2 else "3+")
    a = f["min_crossing_angle_deg"]
    f["shallow_angle"] = "none" if a is None else ("<5deg" if a < 5 else ("5-15deg" if a < 15 else ">=15deg"))
    f["near_merged"] = f["max_merged_fraction"] > 0.1
    f["curve_ends_mid_plot"] = f["curve_ends_mid_plot"] > 0
    f["font_bin"] = "<7pt" if f["tick_font_pt"] < 7 else ("7-8.5pt" if f["tick_font_pt"] < 8.5 else ">=8.5pt")
    f["model_trap"] = ",".join(t for t in f["model_traps"] if t not in ("lin_y_rail", "signed_axis", "clip_top",
                                                                         "resonance_dip", "shared_segment")) or "none"
    f["n_curves"] = str(f["n_curves"])
    import re
    pr = [k["printed"] for k in c["curves"] if k.get("printed")]
    f["label_fmt"] = re.sub(r"[−-]?\d+(?:\.\d+)?", "N", pr[0]) if pr else "none"
    if pr and any("−" in p for p in pr):
        f["label_fmt"] += " (U+2212 minus)"
    return f


def _fmt(v):
    return "-" if v is None or (isinstance(v, float) and not np.isfinite(v)) else (f"{v:.2f}" if isinstance(v, float) else str(v))


def report(root: Path, per_case, per_curve, cases=None):
    cases = cases or {c["id"]: c for c in json.loads((root / "cases.json").read_text())}
    L = [f"# dsdig baseline on `{root.name}`", ""]
    sup = [r for r in per_case if r["supported"]]
    L.append(f"{len(per_case)} cases; {len(sup)} in classes dsdig supports. dsdig reads the PDF (vector, or the "
             "embedded raster for `raster_embed` charts); the case PNG degradations do not reach it.")
    L += ["", "## Chart status by class (supported classes)", "",
          "| class | n | served | flagged | refused | error | wrong_class | not_detected |", "|---|---|---|---|---|---|---|---|"]
    by = defaultdict(Counter)
    for r in sup:
        by[r["cls"]][r["status"]] += 1
    tot = Counter()
    for k in sorted(by):
        cnt = by[k]
        tot += cnt
        L.append(f"| {k} | {sum(cnt.values())} | " + " | ".join(str(cnt[s]) for s in
                 ("served", "flagged", "refused", "error", "wrong_class", "not_detected")) + " |")
    L.append(f"| **all** | {sum(tot.values())} | " + " | ".join(str(tot[s]) for s in
             ("served", "flagged", "refused", "error", "wrong_class", "not_detected")) + " |")
    uns = [r for r in per_case if not r["supported"]]
    extra = Counter(r["status"] for r in uns)
    L += ["", f"Unsupported classes ({len(uns)} cases): {dict(extra)} (a served chart here is dsdig binding the panel "
          "as another class)."]
    # accuracy
    cur = [r for r in per_curve if r["matched"]]
    L += ["", "## Accuracy of scored curves (in-range forward p95, % of span)", "",
          "Per GT curve. `served` = accepted status; `flagged` = curves returned under a non-accepted status.", "",
          "| class | status | GT curves | matched | swaps | p95 median | p95 p90 | <=0.25% | <=0.5% | <=1% |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    grp = defaultdict(list)
    for r in per_curve:
        grp[(r["cls"], r["status"])].append(r)
    for (k, st), rs in sorted(grp.items()):
        m = [r for r in rs if r["matched"]]
        p = np.array([r["p95_inrange"] for r in m]) if m else np.array([])
        L.append(f"| {k} | {st} | {len(rs)} | {len(m)} | {sum(r['swap'] for r in m)} | "
                 f"{_fmt(float(np.median(p)) if len(p) else None)} | {_fmt(float(np.percentile(p, 90)) if len(p) else None)} | "
                 f"{int((p <= 0.25).sum())} | {int((p <= 0.5).sum())} | {int((p <= 1).sum())} |")
    # refusal / error causes
    import re
    L += ["", "## Why charts were not served (supported classes, top causes)", "",
          "| class | status | n | cause (numbers masked) |", "|---|---|---|---|"]
    causes = defaultdict(Counter)
    for r in sup:
        if r["status"] in ("error", "refused", "not_detected", "wrong_class"):
            msg = r.get("error") or (f"dsdig status {r.get('dsdig_status')}: {r.get('refusal_reason') or '-'}"
                                     if r["status"] == "refused" else r["status"])
            causes[(r["cls"], r["status"])][re.sub(r"[-+]?\d+(?:\.\d+)?", "N", msg)[:110]] += 1
    for (k, st), cnt in sorted(causes.items()):
        for msg, n in cnt.most_common(4):
            L.append(f"| {k} | {st} | {n} | {msg.replace('|', '/')} |")
    # factor breakdown: per supported case, outcome = served & worst curve p95 <= 1 % & no swap & all matched
    L += ["", "## Breakdown by factor (supported classes)", "",
          "`good` = served, every GT curve matched, no swap, worst in-range p95 <= 1 % of span. "
          "`p95 med` = median over served charts of the worst-curve p95. Levels with n < 5 are omitted.", ""]
    rows = []
    for r in sup:
        c = cases[r["id"]]
        f = case_factors(c)
        # gate charge: dsdig serves one VGS(Qg) curve per chart by design (no VDD identity)
        need = 1 if r["cls"] == "gate_charge" else r.get("n_gt")
        good = r["status"] == "served" and (r.get("n_matched") or 0) >= (need or 1) and not r.get("swaps") and \
            (r.get("worst_p95_inrange") or 99) <= 1.0
        rows.append((f, r, good))
    base = np.mean([g for _, _, g in rows]) if rows else 0
    # class-adjusted: compare each case with its own class's base rate (class mix confounds raw rates)
    cls_served = defaultdict(list)
    cls_good = defaultdict(list)
    for f, r, g in rows:
        cls_served[r["cls"]].append(r["status"] == "served")
        cls_good[r["cls"]].append(g)
    bs = {k: np.mean(v) for k, v in cls_served.items()}
    bg = {k: np.mean(v) for k, v in cls_good.items()}
    L.append(f"Overall good rate {base:.2f} over {len(rows)} charts.")
    L += ["", "`adj` columns: mean over the level's charts of (outcome - that chart's class base rate), so a factor "
          "is not credited or blamed for the classes it happens to co-occur with.", "",
          "| factor | level | n | served | good | adj served | adj good | p95 med (served) |",
          "|---|---|---|---|---|---|---|---|"]
    worst = []
    for fac in FACTORS:
        lv = defaultdict(list)
        for f, r, g in rows:
            v = f.get(fac)
            lv[json.dumps(v) if isinstance(v, (list, dict)) else str(v)].append((r, g))
        if len(lv) < 2:
            continue
        for v, items in sorted(lv.items()):
            if len(items) < 5:
                continue
            served = [r for r, _ in items if r["status"] == "served"]
            gr = np.mean([g for _, g in items])
            p = [r["worst_p95_inrange"] for r in served if r.get("worst_p95_inrange") is not None]
            adj_s = np.mean([(r["status"] == "served") - bs[r["cls"]] for r, _ in items])
            adj_g = np.mean([g - bg[r["cls"]] for r, g in items])
            L.append(f"| {fac} | {v} | {len(items)} | {len(served) / len(items):.2f} | {gr:.2f} | {adj_s:+.2f} | "
                     f"{adj_g:+.2f} | {_fmt(float(np.median(p)) if p else None)} |")
            worst.append((adj_s, adj_g, fac, v, len(items), len(served) / len(items)))
    worst.sort()
    L += ["", "## Weakest factor levels (class-adjusted served-rate deficit, n >= 10)", ""]
    for a_s, a_g, fac, v, n, sr in [w for w in worst if w[4] >= 10][:20]:
        L.append(f"- `{fac}={v}`: served {sr:.2f}, class-adjusted {a_s:+.2f} (good {a_g:+.2f}), n={n}")
    txt = "\n".join(L) + "\n"
    (root / "dsdig_baseline" / "REPORT.md").write_text(txt)
    return f"-> {root / 'dsdig_baseline' / 'REPORT.md'}\n" + "\n".join(L[:30])
