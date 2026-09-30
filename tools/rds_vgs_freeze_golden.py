"""Freeze human-verified RDS(on)-vs-VGS panels as golden regression fixtures.

Reads a batch run (its `rdson_gate_voltage.json` plus the reviewer manifest
that names the primary panel per part) and writes, per part,
`tests/fixtures/rds_vgs_golden/<part>/panel.json` and `points_c<N>.csv`.

It only ever CREATES a panel's fixture. An existing fixture is never
rewritten: a deliberate change to a golden panel is recorded as an explicit
re-blessing in `tests/fixtures/rds_vgs_golden/REBLESSED.json`, with a note.

A panel Fab re-verifies after its output changed is RE-FROZEN: `--refreeze DIR`
writes the new output to `<part>/DIR/` beside the untouched original and prints
the REBLESSED.json entry (kind "refreeze") that points the test at it. The entry
pins the SHA-256 of the files it supersedes, so it goes stale if they change.

Usage (from the repo root):
    python tools/rds_vgs_freeze_golden.py RUN_JSON MANIFEST_JSON PART [PART ...]
    python tools/rds_vgs_freeze_golden.py --refreeze DIR RUN_JSON MANIFEST_JSON PART [PART ...]
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "rds_vgs_golden"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def freeze(run: dict, manifest: dict, part: str, refreeze: str | None = None) -> dict | None:
    entry = next(p for p in manifest["parts"] if p["part"] == part)
    row = next(p for p in run["panels"]
               if p["part"] == part and p["page"] == entry["page"] and str(p["diagram"]) == str(entry["figure"]))
    superseded = None
    if refreeze:
        base = GOLDEN / part
        if not (base / "panel.json").is_file():
            raise SystemExit(f"{base} has no frozen panel to re-freeze; freeze it without --refreeze")
        superseded = {f.name: sha256(f) for f in sorted(base.iterdir()) if f.is_file()}
        target = base / refreeze
    else:
        target = GOLDEN / part
    if target.exists():
        raise SystemExit(f"{target} exists: golden fixtures are never rewritten; re-bless in REBLESSED.json")
    target.mkdir(parents=True)
    cal = row["calibration"]
    panel = {
        "part": part,
        "pdf_name": Path(row["pdf"]).name,
        "pdf_sha256": sha256(Path(row["pdf"])),
        "page": row["page"],
        "diagram": row["diagram"],
        "title": row["title"],
        "status": row["status"],
        "validation_verdict": row["validation"]["verdict"],
        "anchor_verdicts": [
            {"vgs_v": a["row"]["vgs_v"], "id_a": a["row"]["id_a"], "temperature_c": a["row"]["temperature_c"],
             "verdict": a["verdict"], "chart_mohm": a.get("chart_mohm")}
            for a in row["validation"]["anchors"]
        ],
        "reasons_at_freeze": row["reasons"],
        "trace_method": row["trace_method"],
        "plot_box_px": row["plot_box_px"],
        "calibration": {
            axis: {"model": cal[axis]["model"], "m": cal[axis]["m"], "b": cal[axis]["b"],
                   "ticks": [{"text": t["text"], "value": t["value"], "pixel": t["pixel"]} for t in cal[axis]["ticks"]]}
            for axis in ("x_axis", "y_axis")
        },
        "y_to_mohm": cal["y_to_mohm"],
        "curves": [
            {
                "curve_index": c["curve_index"],
                "label": c["label"],
                "temperature_c": c["temperature_c"],
                "temperature_kind": c["temperature_kind"],
                "id_a": c["id_a"],
                "usable": c.get("usable", True),
                "n_points": c["n_points"],
                "points_csv": f"points_c{c['curve_index']}.csv",
                "readouts": [{"vgs_v": r["vgs_v"], "rds_mohm": r["rds_mohm"], "status": r["status"]}
                             for r in c.get("readouts", [])],
            }
            for c in row["curves"]
        ],
    }
    (target / "panel.json").write_text(json.dumps(panel, indent=1) + "\n")
    for curve in row["curves"]:
        with (target / f"points_c{curve['curve_index']}.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["vgs_v", "rds_mohm", "crop_x_px", "crop_y_px"])
            for (vgs, rds), (x, y) in zip(curve["points"], curve["points_px"]):
                writer.writerow([vgs, rds, x, y])
    print(f"froze {part}: page {row['page']} fig {row['diagram']}, {len(row['curves'])} curves"
          + (f" -> {target.relative_to(GOLDEN)}" if refreeze else ""))
    if superseded is None:
        return None
    return {"part": part, "kind": "refreeze", "dir": refreeze, "superseded_sha256": superseded}


def main() -> None:
    args = sys.argv[1:]
    refreeze = None
    if args[:1] == ["--refreeze"]:
        refreeze, args = args[1], args[2:]
    run = json.loads(Path(args[0]).read_text())
    manifest = json.loads(Path(args[1]).read_text())
    entries = [freeze(run, manifest, part, refreeze) for part in args[2:]]
    if refreeze:
        print(json.dumps([e for e in entries if e], indent=1))


if __name__ == "__main__":
    main()
