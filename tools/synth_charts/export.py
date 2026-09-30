"""Export a set into out/vlm-chart2table (cases_<name>.json + images under cases/synth/<set>/).

The hard subset is drawn from the hard and adversarial tiers, stratified round-robin over classes,
preferring cases with the most difficulty factors; deterministic for a given set.

usage: python -m tools.synth_charts.export SET_DIR [--vlm DIR] [--name cases_synth.json]
                                          [--hard-n 60] [--hard-name cases_synth_hard.json]
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path

MAIN_VLM = Path("/Users/fab/dev/pv/ee/datasheet-chart-digitizer/out/vlm-chart2table")


def difficulty_score(c):
    f = c["factors"]
    s = 0.0
    s += (f.get("n_crossings") or 0) > 0
    s += (f.get("min_crossing_angle_deg") or 90) < 10
    s += (f.get("max_merged_fraction") or 0) > 0.1
    s += max(abs(v) for v in f["label_offset_pt"]) > 0
    s += bool(f.get("frame_past_last_label")) + bool(f.get("contradicting_label")) + bool(f.get("curve_on_gridline"))
    s += bool(f.get("raster_embed")) + bool(f.get("degrade")) + bool(f.get("annotation_cross"))
    s += f["stroke_pt"] <= 0.45
    s += f["identity"] == "inplot_leader"
    s += f["distinguish"] == "none" and c["factors"]["n_curves"] > 1
    s += f["vector_structure_drawn"] in ("split", "merged", "filled")
    s += len(f.get("model_traps") or []) * 0.5
    return s


def hard_subset(cases, n):
    pool = [c for c in cases if c["tier"] in ("hard", "adversarial")]
    by = defaultdict(list)
    for c in pool:
        by[c["class"]].append(c)
    for k in by:
        by[k].sort(key=lambda c: (-difficulty_score(c), c["id"]))
    out, classes = [], sorted(by)
    while len(out) < n and any(by.values()):
        for k in classes:
            if by[k] and len(out) < n:
                out.append(by[k].pop(0))
    return sorted(out, key=lambda c: c["id"])


def export(root: Path, vlm: Path, name: str, hard_n: int, hard_name: str):
    cases = json.loads((root / "cases.json").read_text())
    dst = vlm / "cases" / "synth" / root.name
    dst.mkdir(parents=True, exist_ok=True)
    out = []
    for c in cases:
        shutil.copy2(root / c["image"], dst / Path(c["image"]).name)
        e = dict(c)
        e["image"] = str((dst / Path(c["image"]).name).relative_to(vlm))
        e["gt_provenance"] = dict(c["gt_provenance"], set_dir=str(root), pdf_abs=str(root / c["gt_provenance"]["pdf"]))
        out.append(e)
    (vlm / name).write_text(json.dumps(out))
    hard = hard_subset(out, hard_n)
    (vlm / hard_name).write_text(json.dumps(hard))
    return out, hard


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--vlm", type=Path, default=MAIN_VLM)
    ap.add_argument("--name", default="cases_synth.json")
    ap.add_argument("--hard-n", type=int, default=60)
    ap.add_argument("--hard-name", default="cases_synth_hard.json")
    a = ap.parse_args(argv)
    out, hard = export(a.root, a.vlm, a.name, a.hard_n, a.hard_name)
    print(f"{len(out)} -> {a.vlm / a.name}; hard {len(hard)} -> {a.vlm / a.hard_name}")


if __name__ == "__main__":
    main()
