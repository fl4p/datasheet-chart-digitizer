"""dsdig baseline on a synthetic set: run every dsdig class on each page PDF, bind dsdig's charts to
the GT charts by page position, and score the served curves with the harness metric.

Metric (identical to out/vlm-chart2table/score_cases.py + frontier/inrange.py, whose functions
are imported, not re-implemented): forward distance dsdig point -> GT polyline in axis-normalised
units (log10 on log axes), p95 as % of span, over the GT curve's x-range.

Chart status per GT case:
  not_detected  no dsdig chart of any class over this plot box
  wrong_class   a dsdig chart covers the box but under another class
  error         the class digitiser raised
  refused       dsdig returned a refusal / fail-closed status, or curves with withheld (None) values
  flagged       curves returned under a non-accepted status (unverified, review-required, ...)
  served        accepted status (ok / pass / verified)
Only served and flagged charts are scored, and they are reported separately. Refusals are
refusals, never passes.

usage:
  python -m tools.synth_charts.baseline run   SET_DIR [-j 8] [--python PY] [--src SRC]
  python -m tools.synth_charts.baseline score SET_DIR [--vlm VLM_DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MAIN = Path("/Users/fab/dev/pv/ee/datasheet-chart-digitizer")
DSDIG_CLASS = {"capacitance": "capacitance", "transfer": "transfer", "rds_on_current": "rdson_id",
               "rds_on_temperature": "rdson_temperature", "body_diode": "body_diode",
               "reverse_leakage": "reverse_leakage", "breakdown": "breakdown", "gate_charge": "gate_charge"}
SUPPORTED = set(DSDIG_CLASS.values())
ACCEPTED = {"ok", "pass", "verified"}


def run(root: Path, jobs: int, python: str, src: str, timeout=900):
    pdfs = sorted((root / "pdf").glob("page_*.pdf"))
    runner = REPO / "tools" / "review_ab" / "run_part.py"
    env = {**os.environ, "PYTHONPATH": "", "OMP_NUM_THREADS": "2",
           "DSDIG_OCR_CACHE": os.environ.get("DSDIG_OCR_CACHE", str(root / "dsdig" / "_ocr_cache"))}

    def one(pdf):
        w = root / "dsdig" / pdf.stem
        if (w / "raw_results.json").exists():
            return pdf.stem, "skip", 0
        w.mkdir(parents=True, exist_ok=True)
        t = time.time()
        try:
            p = subprocess.run([python, str(runner), src, str(pdf), str(w)], capture_output=True, text=True,
                               timeout=timeout, env=env)
            (w / "run.log").write_text((p.stdout + p.stderr)[-4000:])
            rc = p.returncode
        except subprocess.TimeoutExpired:
            (w / "run.log").write_text("timeout")
            rc = "timeout"
        return pdf.stem, rc, time.time() - t

    bad = []
    with ThreadPoolExecutor(jobs) as ex:
        for stem, rc, dt in ex.map(one, pdfs):
            print(stem, rc, f"{dt:.0f}s", flush=True)
            if rc not in (0, "skip"):
                bad.append(stem)
    print("DONE failed_runs", len(bad), bad, flush=True)
    return bad


# ----------------------------------------------------------------------------- scoring
def _import_scorer(vlm: Path):
    sys.path.insert(0, str(vlm))
    import score_cases as S  # noqa: E402
    sys.path.insert(0, str(REPO / "tools" / "review_ab"))
    from normalize import normalize  # noqa: E402
    return S, normalize


def _overlap(a, b):
    """Share of box b covered by box a (pt boxes x0, y0, x1, y1)."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy / max(1e-9, (b[2] - b[0]) * (b[3] - b[1]))


def _chart_box(ch):
    for k in ("plot_box_pt", "crop_box_pt"):
        if ch.get(k):
            return ch[k]
    pts = [p for c in ch.get("curves", []) for p in c.get("pts_pt", []) if p and p[0] is not None]
    if pts:
        a = np.array(pts, float)
        return [a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max()]
    return None


def _unit_factor(case, cls):
    """dsdig's documented served units -> the GT axis unit: capacitance in pF (GT may be nF),
    reverse leakage in A (GT printed in µA / nA)."""
    fx = fy = 1.0
    u = case["axis"]["y"]["unit"]
    if cls == "capacitance" and u == "nF":
        fy = 1e-3
    if cls == "reverse_leakage":
        fy = {"µA": 1e6, "nA": 1e9, "mA": 1e3}.get(u, 1.0)
    return fx, fy


def collect(root: Path, cases, normalize):
    by_pdf = defaultdict(list)
    for c in cases:
        by_pdf[c["gt_provenance"]["pdf"]].append(c)
    out = {}
    for pdf, cs in by_pdf.items():
        w = root / "dsdig" / Path(pdf).stem
        if not (w / "raw_results.json").exists():
            for c in cs:
                out[c["id"]] = {"status": "no_run"}
            continue
        raw, charts, _ = normalize(w)
        errs = raw.get("errors", [])
        for c in cs:
            box = c["gt_provenance"]["plot_box_pt"]
            cand = []
            for ch in charts:
                b = _chart_box(ch)
                if b is None:
                    continue
                ov = _overlap(b, box)
                if ov > 0.4:
                    cand.append((ov, ch))
            same = [x for x in cand if DSDIG_CLASS.get(x[1]["class"]) == c["class"]]
            rec = {"dsdig_candidates": [(round(o, 2), ch["class"], ch["status"]) for o, ch in cand]}
            if same:
                ov, ch = max(same, key=lambda x: (bool(x[1].get("curves")), x[0]))
                st = str(ch["status"])
                # curves whose physical values dsdig withheld (None) are not an answer
                has = any(sum(a is not None and b is not None for a, b in (k.get("data") or [])) >= 2
                          for k in ch.get("curves", []))
                rec.update(dsdig_class=ch["class"], dsdig_status=st, overlap=round(ov, 3),
                           status="served" if (st in ACCEPTED and has) else ("flagged" if has else "refused"),
                           chart=ch)
            else:
                # an error for this class on this page, near this chart's caption number?
                num = int(c["gt_provenance"]["diagram"].split()[1].rstrip(":.").strip() or -1) \
                    if c["gt_provenance"]["diagram"].split()[1].rstrip(":.").isdigit() else None
                e = [x for x in errs if DSDIG_CLASS.get(x.get("class")) == c["class"] and x.get("diagram") == num]
                if e:
                    rec.update(status="error", error=str(e[0].get("error"))[:300])
                elif cand:
                    rec.update(status="wrong_class")
                else:
                    rec.update(status="not_detected")
            out[c["id"]] = rec
    return out


def score_case(S, case, ch, raw_dir: Path | None):
    """Score dsdig's curves against GT with the harness functions. Writes the dsdig answer as a
    model CSV (raw_dir/<id>.txt) so score_cases.py can re-score it unchanged."""
    ax = case["axis"]
    gt = {str(k["label"]): np.c_[S.norm(ax["x"], k["x"]), S.norm(ax["y"], k["y"])] for k in case["curves"]}
    fx, fy = _unit_factor(case, case["class"])
    series = {}
    for k in ch.get("curves", []):
        d = np.array([(a, b) for a, b in (k.get("data") or []) if a is not None and b is not None], float)
        if len(d) < 2:
            continue
        d = d * [fx, fy]
        pts = np.c_[S.norm(ax["x"], d[:, 0]), S.norm(ax["y"], d[:, 1])]
        ok = np.isfinite(pts).all(1)
        series[str(k["label"])] = (pts[ok][np.argsort(pts[ok][:, 0])], d[ok][np.argsort(pts[ok][:, 0])])
    match = case["match"]
    proximity = False
    if case["class"] == "gate_charge" or (match == "name" and case["class"] != "capacitance") or \
            (match == "number" and all(not S.numbers_in(n) for n in series)):
        match, proximity = "single_each", True
    if match == "single_each":  # dsdig claims no identity: bind each series to its nearest GT curve
        binding = {}
        for col, (p, _) in series.items():
            lab = min(gt, key=lambda k: np.median(S.seg_dist(p, gt[k])))
            if lab not in binding:
                binding[lab] = col
    else:
        binding = S.bind({"match": match}, {k: v[0] for k, v in series.items()}, gt)
    rows = []
    for lab, g in gt.items():
        r = {"label": lab, "matched": False}
        col = binding.get(lab)
        if col is not None and len(series[col][0]) >= 2:
            p = series[col][0]
            fwd = S.seg_dist(p, g) * 100
            cov = S.seg_dist(g, p) * 100
            nearest = min(gt, key=lambda k: np.median(S.seg_dist(p, gt[k])))
            inr = (p[:, 0] >= g[:, 0].min() - 1e-3) & (p[:, 0] <= g[:, 0].max() + 1e-3)
            p95_in = float(np.percentile(S.seg_dist(p[inr], g) * 100, 95)) if inr.sum() >= 2 else 100.0
            r.update(matched=True, col=col, n_pts=len(p), fwd_p50=float(np.median(fwd)),
                     fwd_p95=float(np.percentile(fwd, 95)), p95_inrange=p95_in, n_out=int((~inr).sum()),
                     cov_p95=float(np.percentile(cov, 95)), swap=bool(nearest != lab and len(gt) > 1),
                     bound_by_proximity=proximity)
        rows.append(r)
    if raw_dir is not None:
        _write_csv(raw_dir / f"{case['id']}.txt", case, series, binding, proximity)
    return rows


def _write_csv(path, case, series, binding, proximity):
    inv = {v: k for k, v in binding.items()}
    lab2printed = {str(k["label"]): (k["printed"] or str(k["label"])) for k in case["curves"]}
    cols = []
    for col, (_, d) in series.items():
        name = col
        if proximity and col in inv:
            name = lab2printed[inv[col]]
        cols.append((name.replace(",", ";"), d))
    lines = ["```csv", f"# dsdig served output, {case['id']}", ",".join(["x"] + [n for n, _ in cols])]
    for j, (n, d) in enumerate(cols):
        for a, b in d:
            row = [repr(float(a))] + [""] * len(cols)
            row[1 + j] = repr(float(b))
            lines.append(",".join(row))
    lines.append("```")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def score(root: Path, vlm: Path, tag: str):
    S, normalize = _import_scorer(vlm)
    cases = json.loads((root / "cases.json").read_text())
    got = collect(root, cases, normalize)
    raw_dir = vlm / "raw" / tag
    per_case, per_curve = [], []
    for c in cases:
        g = got.get(c["id"], {"status": "no_run"})
        rec = {"id": c["id"], "cls": c["class"], "tier": c["tier"], "supported": c["class"] in SUPPORTED,
               "status": g["status"], "dsdig_status": g.get("dsdig_status"), "error": g.get("error"),
               "candidates": g.get("dsdig_candidates")}
        if g["status"] in ("served", "flagged"):
            rows = score_case(S, c, g["chart"], raw_dir)
            m = [r for r in rows if r["matched"]]
            rec.update(n_gt=len(rows), n_matched=len(m), swaps=sum(r["swap"] for r in m),
                       worst_p95_inrange=max((r["p95_inrange"] for r in m), default=None),
                       med_p95_inrange=float(np.median([r["p95_inrange"] for r in m])) if m else None)
            for r in rows:
                per_curve.append({"id": c["id"], "cls": c["class"], "tier": c["tier"], "status": g["status"], **r})
        per_case.append(rec)
    out = root / "dsdig_baseline"
    out.mkdir(exist_ok=True)
    (out / "per_case.json").write_text(json.dumps(per_case, indent=0, default=str))
    (out / "per_curve.json").write_text(json.dumps(per_curve, indent=0, default=str))
    return per_case, per_curve


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "score"])
    ap.add_argument("root", type=Path)
    ap.add_argument("-j", type=int, default=8)
    ap.add_argument("--python", default=str(MAIN / ".venv" / "bin" / "python"))
    ap.add_argument("--src", default=str(REPO / "src"))
    ap.add_argument("--vlm", type=Path, default=MAIN / "out" / "vlm-chart2table")
    ap.add_argument("--tag", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        bad = run(a.root, a.j, a.python, a.src)
        sys.exit(2 if bad else 0)
    tag = a.tag or f"dsdig-{a.root.name}"
    pc, cur = score(a.root, a.vlm, tag)
    from .report import report
    print(report(a.root, pc, cur))


if __name__ == "__main__":
    main()
