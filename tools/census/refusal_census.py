#!/usr/bin/env python3
"""Run every chart class on a seeded sub-sample of a medium-census sample; keep one row per chart.

Uses the existing all-classes runner ``tools/review_ab/run_part.py`` (production entry
points, one subprocess per PDF) and reduces each ``raw_results.json`` to compact rows:
class, status, the class's own reason fields, and what the trace was read from.
A PDF whose run fails or times out is a row of its own; it is never dropped.

The classes are those ``run_part.py`` runs: capacitance, transfer, breakdown, body
diode, RDS(on) vs I_D, RDS(on) vs Tj, reverse leakage, gate charge, reverse recovery.
RDS(on) vs VGS, Vth, MLCC, core loss and paper plots are not run here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REASON_FIELDS = (
    "status_reasons", "trace_validation_status", "trace_validation_reasons", "qoss_validation_status",
    "qoss_validation_error", "qoss_metrics_status_reasons", "axis_error", "axis_warning", "axis_position_error",
    "axis_grid_error", "axis_ocr_error", "vector_error", "diagnostics", "binding_error", "warnings",
    "qg_axis_status", "verdict", "refusal_reason", "reason", "reasons", "error",
)
MEDIUM_FIELDS = ("extraction_method", "trace_source", "method", "frame_method", "plot_box_method")


def _small(value):
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (list, tuple)):
        return [str(v)[:300] for v in value][:40]
    if isinstance(value, dict):
        return None  # nested diagnostics are not reasons
    return str(value)[:600]


def compact(raw: dict) -> list[dict]:
    rows: list[dict] = []
    for r in raw.get("results", []):
        res = r.get("result") if isinstance(r.get("result"), dict) else {}
        panel = r.get("panel") or res.get("panel") or {}
        row = {
            "outcome": "result", "class": r.get("class"), "page": r.get("page"), "diagram": r.get("diagram"),
            "status": res.get("status") or res.get("verdict"),
            "text_source": panel.get("text_source") if isinstance(panel, dict) else None,
            "title": (panel.get("title") or "")[:80] if isinstance(panel, dict) else "",
            "kind": panel.get("kind") if isinstance(panel, dict) else None,
            "crop_box_pt": panel.get("crop_box_pt") if isinstance(panel, dict) else None,
            "reasons": {k: v for k in REASON_FIELDS if (v := _small(res.get(k))) is not None},
            "medium": {k: res.get(k) for k in MEDIUM_FIELDS if isinstance(res.get(k), str)},
        }
        rows.append(row)
    for e in raw.get("errors", []):
        rows.append({
            "outcome": "error", "class": e.get("class"), "page": e.get("page"), "diagram": e.get("diagram"),
            "status": "error", "error_kind": e.get("kind"), "reasons": {"error": str(e.get("error"))[:600]},
            "text_source": None, "title": "", "medium": {},
        })
    return rows


def run_one(repo: Path, root: Path, vendor: str, rel: str, timeout_s: int, keep: Path | None) -> dict:
    t0 = time.time()
    row: dict = {"vendor": vendor, "pdf": rel}
    with tempfile.TemporaryDirectory(prefix="census-part-") as tmp:
        work = Path(tmp) / "w"
        try:
            proc = subprocess.run(
                [sys.executable, str(repo / "tools/review_ab/run_part.py"), str(repo / "src"), str(root / rel), str(work)],
                capture_output=True, text=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            row.update(run_status="timeout", charts=[], panels=[], seconds=round(time.time() - t0, 1))
            return row
        raw_path = work / "raw_results.json"
        if proc.returncode != 0 or not raw_path.is_file():
            row.update(run_status="failed", run_error=(proc.stderr or proc.stdout)[-800:], charts=[], panels=[],
                       seconds=round(time.time() - t0, 1))
            return row
        raw = json.loads(raw_path.read_text())
        if keep is not None:
            dest = keep / (rel.replace("/", "__") + ".raw_results.json")
            dest.write_text(raw_path.read_text())
    row.update(
        run_status="ok", charts=compact(raw), rr_skip=raw.get("rr_skip"),
        panels=[{"page": p["page"], "diagram": p["diagram"], "kind": p["kind"], "text_source": p.get("text_source"),
                 "title": p["title"][:80], "crop_box_pt": p.get("crop_box_pt")} for p in raw.get("panels", [])],
        seconds=round(time.time() - t0, 1),
    )
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--sample-from", type=Path, required=True, help="medium-census run directory (sample.tsv, provenance.json)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--per-vendor", type=int, default=10)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--dsdig-commit", default="")
    ap.add_argument("--keep-raw", action="store_true", help="keep each raw_results.json under OUT/raw")
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    prior = json.loads((args.sample_from / "provenance.json").read_text())
    by_vendor: dict[str, list[str]] = {}
    for line in (args.sample_from / "sample.tsv").read_text().splitlines():
        if line:
            vendor, rel = line.split("\t")
            by_vendor.setdefault(vendor, []).append(rel)
    sample: list[tuple[str, str]] = []
    for vendor, rels in sorted(by_vendor.items()):
        rng = random.Random(f"refusal:{args.seed}:{vendor}")
        chosen = rels if len(rels) <= args.per_vendor else rng.sample(sorted(rels), args.per_vendor)
        sample.extend((vendor, rel) for rel in sorted(chosen))
    args.out.mkdir(parents=True, exist_ok=True)
    listing = "\n".join(f"{v}\t{p}" for v, p in sample)
    (args.out / "sample.tsv").write_text(listing + "\n")
    repo = Path(__file__).resolve().parents[2]
    (args.out / "provenance.json").write_text(json.dumps({
        "tool": "tools/census/refusal_census.py", "runner": "tools/review_ab/run_part.py",
        "dsdig_commit": args.dsdig_commit or subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "parent_sample_sha256": prior["sample_list_sha256"], "population": prior["population"],
        "seed": args.seed, "per_vendor": args.per_vendor, "sample_size": len(sample),
        "sample_list_sha256": hashlib.sha256(listing.encode()).hexdigest(),
        "host": platform.node(), "platform": platform.platform(), "argv": sys.argv[1:],
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }, indent=1))
    rows_path = args.out / "pdf_rows.jsonl"
    done = {json.loads(line)["pdf"] for line in rows_path.read_text().splitlines()} if rows_path.exists() else set()
    todo = [(v, p) for v, p in sample if p not in done]
    print(f"sub-sample {len(sample)} PDFs; done {len(done)}; to do {len(todo)}", flush=True)
    if args.list_only:
        return 0
    keep = None
    if args.keep_raw:
        keep = args.out / "raw"
        keep.mkdir(exist_ok=True)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    with rows_path.open("a") as fh, ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_one, repo, args.root, v, p, args.timeout, keep) for v, p in todo]
        for n, fut in enumerate(as_completed(futures), start=1):
            fh.write(json.dumps(fut.result()) + "\n")
            fh.flush()
            if n % 10 == 0 or n == len(todo):
                print(f"{n}/{len(todo)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
