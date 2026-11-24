#!/usr/bin/env python3
"""Tabulate ``refusal_census.py`` rows by chart class, pipeline stage and chart medium.

Stage assignment is a reading aid, not a diagnosis.  Each chart that is not a pass
gets the stage of the reason that stopped it, by the first matching rule in
``STAGE_RULES`` over the class's own reason fields.  A reason no rule matches is
reported as ``unclassified`` and listed; it is never folded into another stage.

The stage says where the pipeline stopped, which is not always where the cause
is: a crop that includes a neighbouring panel usually stops at axis calibration.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import chart_medium_census as M

PASS = {"ok", "verified", "pass"}
# statuses that serve a value the class itself marks as needing review or unverified
SOFT = {"unverified", "overlay-review-required", "review_required", "low_confidence", "axis_assumed",
        "axis_grid_inferred", "source_untrusted", "suspect"}

# (stage, regex) in priority order; matched against "field: text" for every reason
STAGE_RULES: list[tuple[str, str]] = [
    ("by design: human review", r"HUMAN REVIEW REQUIRED"),
    ("finder / crop / routing", r"no gridline panel|no usable grid hint|not_routed|not routed|no direction-evidenced local|"
     r"no local grid above|axis titles name another chart|non_gate_plot|rejected_non_gate|could not find plot grid|"
     r"no_rdson_.*panel|emitted zero|plot box|plot_box|frame"),
    ("axis calibration", r"axis|tick|calibration|decade|unit_unresolved|misses its gridlines|grid residual"),
    ("label / curve identity", r"legend|label|temperature mismatch|binding|identit|typ/max|vdd|curve/temperature"),
    ("curve tracing", r"trace bands|full[- _]span|curve_points|sampled columns|non_monotone|missing_initial_ramp|"
     r"captured_by_grid_rule|grid_rule|short_x_span|x_span|x_start|x_end|edge_gap|branch_hop|low_trace_confidence|"
     r"ink|vector curve|curve candidates|trace_validation|shared_trace|rescue|expected exactly"),
    ("gates / validation", r"qoss|table|unphysical|outside|tolerance|anchor|vpl_unresolved|vpl_|template|not_flatter|conflict"),
]
_RULES = [(stage, re.compile(rx, re.I)) for stage, rx in STAGE_RULES]
# fields that only restate another field or are informational on a passing trace
SKIP_FIELDS = {"trace_validation_status", "qoss_validation_status", "qg_axis_status", "qoss_metrics_status_reasons"}
# informational on charts whose raster path then served: the vector attempt failing is not the stop reason
INFO_ONLY = {"vector_error"}

def reason_texts(chart: dict) -> list[str]:
    out: list[str] = []
    for field, value in chart.get("reasons", {}).items():
        if field in SKIP_FIELDS:
            continue
        for text in value if isinstance(value, list) else [value]:
            out.append(f"{field}: {text}")
    return out


def stage_of(chart: dict) -> tuple[str, str]:
    """(stage, the reason that set it) for a non-pass chart."""
    texts = reason_texts(chart)
    primary = [t for t in texts if t.split(":", 1)[0] not in INFO_ONLY] or texts
    for stage, rx in _RULES:
        for text in primary:
            # match the reason text only: a field name such as binding_error is not evidence
            if rx.search(text.split(": ", 1)[-1]) or (text.startswith("axis_") and stage == "axis calibration"):
                return stage, text
    return "unclassified", (primary[0] if primary else "(no reason given)")


def outcome_of(chart: dict) -> str:
    status = chart.get("status")
    if chart.get("outcome") == "error":
        return "refused"
    if status in PASS:
        return "pass"
    if status in SOFT:
        return "served, unverified"
    return "refused"


def medium_for(root: Path, rel: str, page_no: int | None, crop: list[float] | None, cache: dict) -> str:
    """Curve medium of a crop, by the medium census's own rule and helpers."""
    if not crop or page_no is None:
        return "unknown"
    import pymupdf

    key = (rel, page_no)
    if key not in cache:
        with pymupdf.open(root / rel) as doc:
            page = doc[page_no - 1]
            floor = M.RULES["figure_image_min_pt"]
            imgs = [tuple(float(v) for v in i["bbox"]) for i in page.get_image_info()]
            cache[key] = ([b for b in imgs if b[2] - b[0] >= floor and b[3] - b[1] >= floor], page.get_drawings())
    imgs, drawings = cache[key]
    box = tuple(float(v) for v in crop)
    length, _ = M.stroke_length_in(drawings, box)
    diag = math.hypot(box[2] - box[0], box[3] - box[1]) or 1.0
    medium = M.classify_medium(length / diag, M._cover(box, imgs), M.fill_items_in(drawings, box))
    return {"vector_filled": "vector", "both": "vector"}.get(medium, medium)


def table(header: list[str], body: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(header))) + "|"]
    return "\n".join(out + ["| " + " | ".join(r) + " |" for r in body])


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("run", type=Path)
    ap.add_argument("--root", type=Path, required=True, help="datasheet library root, to read each crop's medium")
    args = ap.parse_args()
    rows = [json.loads(line) for line in (args.run / "pdf_rows.jsonl").read_text().splitlines() if line.strip()]
    prov = json.loads((args.run / "provenance.json").read_text())
    cache: dict = {}
    charts: list[dict] = []
    for r in rows:
        crops = {(p["page"], p["diagram"]): p.get("crop_box_pt") for p in r.get("panels", [])}
        for c in r.get("charts", []):
            c = dict(c, vendor=r["vendor"], pdf=r["pdf"])
            if not c.get("crop_box_pt"):  # error rows name the finder panel by page and diagram
                c["crop_box_pt"] = crops.get((c.get("page"), c.get("diagram")))
            c["outcome3"] = outcome_of(c)
            c["stage"], c["stage_reason"] = ("", "") if c["outcome3"] == "pass" else stage_of(c)
            try:
                c["medium_census"] = medium_for(args.root, r["pdf"], c.get("page"), c.get("crop_box_pt"), cache)
            except Exception as exc:  # unreadable page: say so, do not guess
                c["medium_census"] = f"unknown ({type(exc).__name__})"
            charts.append(c)
    (args.run / "charts.jsonl").write_text("".join(json.dumps(c) + "\n" for c in charts))

    L: list[str] = []
    L.append(f"Run: commit {prov['dsdig_commit'][:7]}, host {prov['host']}, {len(rows)} of {prov['sample_size']} PDFs "
             f"({prov['per_vendor']} per vendor, seed {prov['seed']}); run status {dict(Counter(r['run_status'] for r in rows))}. "
             f"{len(charts)} chart rows.")
    done = Counter(r["vendor"] for r in rows)
    weight = {v: prov["population"][v] / n for v, n in done.items()}
    classes = sorted({c["class"] for c in charts})
    stages = [s for s, _ in STAGE_RULES] + ["unclassified"]

    body = []
    for k in classes:
        cc = [c for c in charts if c["class"] == k]
        o = Counter(c["outcome3"] for c in cc)
        wn = sum(weight[c["vendor"]] for c in cc)
        wp = sum(weight[c["vendor"]] for c in cc if c["outcome3"] == "pass")
        wr = sum(weight[c["vendor"]] for c in cc if c["outcome3"] == "refused")
        body.append([k, str(len(cc)), str(o["pass"]), str(o["served, unverified"]), str(o["refused"]),
                     f"{100 * o['pass'] / len(cc):.0f} %", f"{100 * wp / wn:.0f} %", f"{100 * wr / wn:.0f} %"])
    o = Counter(c["outcome3"] for c in charts)
    wn = sum(weight[c["vendor"]] for c in charts)
    body.append(["**all**", str(len(charts)), str(o["pass"]), str(o["served, unverified"]), str(o["refused"]),
                 f"{100 * o['pass'] / max(len(charts), 1):.0f} %",
                 f"{100 * sum(weight[c['vendor']] for c in charts if c['outcome3'] == 'pass') / wn:.0f} %",
                 f"{100 * sum(weight[c['vendor']] for c in charts if c['outcome3'] == 'refused') / wn:.0f} %"])
    L.append("\n### Outcome by class (chart rows; weighted columns scale each vendor to its share of the corpus)\n")
    L.append(table(["class", "charts", "pass", "served, unverified", "refused / error", "pass share",
                    "pass share, weighted", "refused share, weighted"], body))

    body = []
    for k in classes:
        cc = [c for c in charts if c["class"] == k and c["outcome3"] != "pass"]
        s = Counter(c["stage"] for c in cc)
        body.append([k, str(len(cc))] + [str(s[x]) if s[x] else "" for x in stages])
    s = Counter(c["stage"] for c in charts if c["outcome3"] != "pass")
    body.append(["**all**", str(sum(s.values()))] + [str(s[x]) for x in stages])
    L.append("\n### Stage that stopped each non-pass chart, by class\n")
    L.append(table(["class", "non-pass"] + stages, body))

    media = ["vector", "raster", "none", "unknown"]
    body = []
    for x in ["pass"] + stages:
        cc = [c for c in charts if (c["outcome3"] == "pass" if x == "pass" else c["stage"] == x and c["outcome3"] != "pass")]
        m = Counter(c["medium_census"].split(" ")[0] for c in cc)
        body.append([x, str(len(cc))] + [str(m[k]) if m[k] else "" for k in media])
    L.append("\n### Stage by chart medium (crop of the panel each chart row names)\n")
    L.append(table(["outcome / stage", "charts"] + media, body))

    body = []
    for m in media:
        cc = [c for c in charts if c["medium_census"].split(" ")[0] == m]
        if not cc:
            continue
        o = Counter(c["outcome3"] for c in cc)
        body.append([m, str(len(cc)), str(o["pass"]), str(o["served, unverified"]), str(o["refused"]),
                     f"{100 * o['pass'] / len(cc):.0f} %"])
    L.append("\n### Outcome by chart medium\n")
    L.append(table(["medium", "charts", "pass", "served, unverified", "refused / error", "pass share"], body))

    top = defaultdict(Counter)
    for c in charts:
        if c["outcome3"] != "pass":
            top[c["stage"]][(c["class"], re.sub(r"[-+]?\d+(\.\d+)?", "#", c["stage_reason"])[:120])] += 1
    L.append("\n### Most frequent stop reasons per stage\n")
    for x in stages:
        if top[x]:
            L.append(f"\n**{x}** ({sum(top[x].values())})\n")
            L += [f"- {n} × {k}: `{t}`" for (k, t), n in top[x].most_common(8)]
    text = "\n".join(L) + "\n"
    (args.run / "report.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
