"""Scorer checks (perfect / 1 %-shifted / swapped answers through the unchanged harness scorer) and
known-bad controls (deliberately broken generator runs that the self-checks must catch).

usage:
  python -m tools.synth_charts.checks scorer   [--vlm DIR] [--cases cases_synth.json] [--scorer-python python3]
  python -m tools.synth_charts.checks knownbad OUT_DIR [--n 24] [--seed 11]
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np

from . import build, validate
from .axes import from_norm, to_norm
from .export import MAIN_VLM


def _col_name(case, k):
    return (k["printed"] or str(k["label"])).replace(",", ";")


def _write(path, case, cols):
    lines = ["```csv", f"# Title: {case['id']}, X: x, Y: y", ",".join(["x"] + [n for n, _, _ in cols])]
    for j, (_, xs, ys) in enumerate(cols):
        for a, b in zip(xs, ys):
            row = [repr(float(a))] + [""] * len(cols)
            row[1 + j] = repr(float(b))
            lines.append(",".join(row))
    lines.append("```")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _shift(case, k, d=0.01):
    ax = case["axis"]
    uv = np.c_[to_norm(ax["x"], k["x"]), to_norm(ax["y"], k["y"])]
    t = np.gradient(uv, axis=0)
    n = np.c_[-t[:, 1], t[:, 0]]
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    q = uv + d * n
    return from_norm(ax["x"], q[:, 0]), from_norm(ax["y"], q[:, 1])


def scorer_checks(vlm: Path, cases_name: str, py: str):
    cases = json.loads((vlm / cases_name).read_text())
    for tag in ("_synth_perfect", "_synth_shift1", "_synth_swap"):
        raw = vlm / "raw" / tag
        for c in cases:
            cols = [(_col_name(c, k), k["x"], k["y"]) for k in c["curves"]]
            if tag == "_synth_shift1":
                cols = [(_col_name(c, k), *_shift(c, k)) for k in c["curves"]]
            if tag == "_synth_swap" and len(cols) > 1:
                cols[0], cols[1] = (cols[0][0], cols[1][1], cols[1][2]), (cols[1][0], cols[0][1], cols[0][2])
            _write(raw / f"{c['id']}.txt", c, cols)
    res = {}
    for tag in ("_synth_perfect", "_synth_shift1", "_synth_swap"):
        log = vlm / "raw" / tag / "_score.log"
        with open(log, "w") as f:
            p = subprocess.run([py, "score_cases.py", tag, "--cases", cases_name], cwd=vlm, stdout=f, stderr=subprocess.STDOUT)
        if p.returncode != 0:
            res[tag] = {"error": f"score_cases.py rc={p.returncode}, see {log}"}
            continue
        rows = list(csv.DictReader(open(vlm / f"score_cases_{tag}.csv")))
        m = [r for r in rows if r["matched"] == "True"]
        p95 = np.array([float(r["fwd_p95"]) for r in m])
        multi = [r for r in m if int(r["n_gt"]) > 1]
        res[tag] = {"curves": len(rows), "matched": len(m), "unmatched": len(rows) - len(m),
                    "swaps": sum(r["swap"] == "True" for r in m),
                    "swaps_of_multi_curve": f"{sum(r['swap'] == 'True' for r in multi)}/{len(multi)}",
                    "fwd_p95_median": float(np.median(p95)) if len(p95) else None,
                    "fwd_p95_min": float(p95.min()) if len(p95) else None,
                    "fwd_p95_max": float(p95.max()) if len(p95) else None}
    return res


CONTROLS = {
    "box_px_off_by_one": ["raster"],
    "unclipped_gt": ["inframe", "vector"],
    "crop_cut": ["ticks"],
    "box_pt_off": ["vector"],
    "gt_scale": ["vector"],
}


def _run_checks(root, names):
    cases = json.loads((root / "cases.json").read_text())
    out = {}
    for k in names:
        fn = {"inframe": lambda: validate.inframe_check(cases), "ticks": lambda: validate.tick_check(root, cases),
              "vector": lambda: validate.vector_check(root, cases), "raster": lambda: validate.raster_check(root, cases)}[k]
        recs = fn()
        out[k] = dict(Counter(r["status"] for r in recs))
    return out


def knownbad(out: Path, n: int, seed: int):
    res = {}
    all_checks = sorted({c for v in CONTROLS.values() for c in v})
    build.MUTATE.clear()
    build.generate(out / "clean", n, seed, "kb")
    res["clean"] = _run_checks(out / "clean", all_checks)
    for mut, checks in CONTROLS.items():
        build.MUTATE.clear()
        build.MUTATE.add(mut)
        try:
            build.generate(out / mut, n, seed, "kb", probe=True)
        finally:
            build.MUTATE.clear()
        res[mut] = _run_checks(out / mut, checks)
    (out / "knownbad.json").write_text(json.dumps(res, indent=1))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scorer", "knownbad"])
    ap.add_argument("out", nargs="?", type=Path)
    ap.add_argument("--vlm", type=Path, default=MAIN_VLM)
    ap.add_argument("--cases", default="cases_synth.json")
    ap.add_argument("--scorer-python", default="python3")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args(argv)
    if a.cmd == "scorer":
        print(json.dumps(scorer_checks(a.vlm, a.cases, a.scorer_python), indent=1))
    else:
        print(json.dumps(knownbad(a.out, a.n, a.seed), indent=1))


if __name__ == "__main__":
    main()
