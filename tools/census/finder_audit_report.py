#!/usr/bin/env python3
"""Tabulate by-eye labels of ``finder_audit_sheets.py`` pages (labels_*.json next to items.json).

Label fields per page: charts, boxed_ok, boxed_defect, missed, false_boxes, title_cut,
chart_medium, and missed_kinds / defect_kinds (one printed chart kind per chart).
Pages without a label are reported, not assumed empty.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

# kinds with a dsdig digitizer on main (validated or not); the rest are located but not digitized
TARGET = {"transfer", "rds_on_vs_id", "rds_on_vs_vgs", "rds_on_vs_tj", "capacitance", "gate_charge", "body_diode",
          "breakdown_voltage", "threshold_voltage"}


def table(header: list[str], body: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(header))) + "|"]
    return "\n".join(out + ["| " + " | ".join(r) + " |" for r in body])


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("audit", type=Path)
    args = ap.parse_args()
    meta = json.loads((args.audit / "items.json").read_text())
    items = {i["index"]: i for i in meta["items"]}
    labels: dict[int, dict] = {}
    for f in sorted(args.audit.glob("labels_*.json")):
        for row in json.loads(f.read_text()):
            labels[row["index"]] = row
    bad = [i for i, lab in labels.items() if lab["charts"] != lab["boxed_ok"] + lab["boxed_defect"] + lab["missed"]
           or len(lab.get("missed_kinds", [])) != lab["missed"] or len(lab.get("defect_kinds", [])) != lab["boxed_defect"]]
    L = [f"{len(meta['pdfs'])} PDFs, {len(items)} pages rendered, {len(labels)} labelled, "
         f"{len(set(items) - set(labels))} unlabelled, {len(bad)} rows with inconsistent counts {bad[:10]}."]
    fields = ["charts", "boxed_ok", "boxed_defect", "missed", "false_boxes", "title_cut"]

    def row(name: str, ls: list[dict]) -> list[str]:
        t = {f: sum(lab[f] for lab in ls) for f in fields}
        n = max(t["charts"], 1)
        return [name, str(len(ls))] + [str(t[f]) for f in fields] + [f"{100 * t['boxed_ok'] / n:.0f} %", f"{100 * t['missed'] / n:.0f} %"]

    groups = defaultdict(list)
    for i, lab in labels.items():
        groups["medium: " + lab["chart_medium"]].append(lab)
        groups["vendor: " + items[i]["vendor"]].append(lab)
    body = [row("**all pages**", list(labels.values()))] + [row(k, v) for k, v in sorted(groups.items())]
    L.append("\n" + table(["group", "pages"] + fields + ["boxed ok share", "missed share"], body))

    miss: Counter = Counter()
    defect: Counter = Counter()
    for lab in labels.values():
        miss.update(lab.get("missed_kinds", []))
        defect.update(lab.get("defect_kinds", []))
    body = []
    for k in sorted(set(miss) | set(defect), key=lambda k: (k not in TARGET, -(miss[k] + defect[k]))):
        body.append([k + ("" if k in TARGET else " (no digitizer)"), str(miss[k]), str(defect[k])])
    tm, td = sum(miss[k] for k in TARGET), sum(defect[k] for k in TARGET)
    body.append(["**kinds with a digitizer**", str(tm), str(td)])
    body.append(["**all kinds**", str(sum(miss.values())), str(sum(defect.values()))])
    L.append("\nKinds of the missed and defective charts (kinds of the correctly boxed charts were not recorded):\n")
    L.append(table(["printed chart kind", "missed", "box defective"], body))
    text = "\n".join(L) + "\n"
    (args.audit / "report.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
