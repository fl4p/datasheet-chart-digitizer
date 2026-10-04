#!/usr/bin/env python3
"""A/B of `_split_touching_bottom_pair` over the reviewed capacitance panels.

Every capacitance panel in dsdig-verify-backlog's MANIFEST.*cap*.jsonl is
digitized twice from the same detected crop: with the split (B) and with it
disabled (A). A panel is reported when its served points, status, reasons or
trace-validation verdict differ; for raster panels a zoomed A|B overlay is
written for each change so every one can be inspected by eye.

    PYTHONPATH=src python tools/capacitance_split_ab.py OUT_DIR [--jobs N]
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
import tempfile
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

BACKLOG = Path("/Users/fab/dev/pv/ee/dsdig-verify-backlog")


NO_PANEL: list[str] = []  # manifest rows with no page: the finder emitted no panel


def _panels() -> dict[str, set[tuple[int, str]]]:
    wanted: dict[str, set[tuple[int, str]]] = {}
    for path in glob.glob(str(BACKLOG / "MANIFEST.*cap*.jsonl")):
        for line in open(path):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("chart_type") != "capacitance":
                continue
            if row.get("page") is None or row.get("diagram") is None:
                NO_PANEL.append(row["review_id"])
            else:
                wanted.setdefault(row["pdf"], set()).add((int(row["page"]), str(row["diagram"])))
    return wanted


def _run_pdf(pdf: str, wanted: list[tuple[int, str]], out_dir: str) -> list[dict]:
    import cv2
    import numpy as np

    from datasheet_chart_digitizer import capacitance_traces as ct
    from datasheet_chart_digitizer import find_charts
    from datasheet_chart_digitizer import mosfet_capacitance as mc

    split = ct._split_touching_bottom_pair
    grays: list[np.ndarray] = []
    source = ct._raster_source_centers_by_x

    def spy(gray, plot, **kwargs):
        grays.append(gray)
        return source(gray, plot, **kwargs)

    ct._raster_source_centers_by_x = spy
    results = []
    with tempfile.TemporaryDirectory(prefix="cap-ab-") as tmp:
        work = Path(tmp)
        try:
            panels = find_charts.process_pdf(Path(pdf), work, dpi=180)  # the CLI default
        except Exception as exc:  # noqa: BLE001 -- reported, never counted as unchanged
            return [{"pdf": pdf, "error": f"find_charts: {exc!r}"}]
        for panel in panels:
            key = (int(panel.page), str(panel.diagram))
            if panel.kind != "capacitances" or key not in wanted:
                continue
            chart = {"pdf": pdf, "part": Path(pdf).stem, "page": panel.page,
                     "bbox_pt": list(panel.bbox_pt), "crop_box_pt": list(panel.crop_box_pt), "text": ""}
            side: dict[str, dict] = {}
            for mode in ("A", "B"):
                ct._split_touching_bottom_pair = split if mode == "B" else (lambda mask, centers: centers)
                grays.clear()
                try:
                    r = mc.process_chart(chart, work / panel.crop_png, work / mode,
                                         Path(f"p{key[0]}d{key[1]}"), Path(pdf).parent)
                    pts: dict[str, list[tuple[float, float]]] = {}
                    with open(work / mode / r["points"]) as fh:
                        for row in csv.DictReader(fh):
                            pts.setdefault(row["trace"], []).append((float(row["x_px"]), float(row["y_px"])))
                    side[mode] = {"status": r.get("status"), "reasons": r.get("status_reasons"),
                                  "trace_validation": r.get("trace_validation_status"),
                                  "method": r.get("extraction_method"), "points": pts,
                                  "gray": grays[-1] if grays else None}
                except Exception as exc:  # noqa: BLE001
                    side[mode] = {"error": repr(exc), "trace": traceback.format_exc(limit=3)}
            ct._split_touching_bottom_pair = split
            a, b = side["A"], side["B"]
            row = {"pdf": pdf, "page": key[0], "diagram": key[1]}
            if "error" in a or "error" in b:
                row.update(changed=a.get("error") != b.get("error"), error_a=a.get("error"), error_b=b.get("error"))
                results.append(row)
                continue
            changed_traces = {name: {"a_n": len(a["points"].get(name, [])), "b_n": len(b["points"].get(name, [])),
                                     "a_x": _span(a["points"].get(name)), "b_x": _span(b["points"].get(name))}
                              for name in sorted(set(a["points"]) | set(b["points"]))
                              if a["points"].get(name) != b["points"].get(name)}
            verdict_changed = any(a[k] != b[k] for k in ("status", "reasons", "trace_validation"))
            row.update(method=b["method"], changed=bool(changed_traces or verdict_changed),
                       traces=changed_traces,
                       a={k: a[k] for k in ("status", "reasons", "trace_validation")},
                       b={k: b[k] for k in ("status", "reasons", "trace_validation")})
            if row["changed"] and b["gray"] is not None:
                name = f"{Path(pdf).stem}_p{key[0]}d{key[1]}.png"
                _overlay(b["gray"], a["points"], b["points"], Path(out_dir) / "overlays" / name)
                row["overlay"] = name
            results.append(row)
    return results


def _span(points):
    if not points:
        return None
    xs = [x for x, _ in points]
    return [min(xs), max(xs)]


def _overlay(gray, a_pts, b_pts, path: Path) -> None:
    import cv2
    import numpy as np

    colors = {"Ciss": (255, 0, 0), "Coss": (0, 170, 0), "Crss": (0, 0, 255)}
    panes = []
    for pts in (a_pts, b_pts):
        img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        for name, points in pts.items():
            for x, y in points:
                cv2.circle(img, (int(x * 2 + 1), int(y * 2 + 1)), 1, colors.get(name, (0, 0, 0)), -1)
        panes.append(img)
    sep = np.full((panes[0].shape[0], 8, 3), 128, np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.hstack([panes[0], sep, panes[1]]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--jobs", type=int, default=6)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    wanted = _panels()
    rows: list[dict] = []
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(_run_pdf, pdf, sorted(keys), str(args.out)) for pdf, keys in sorted(wanted.items())]
        for future in as_completed(futures):
            rows.extend(future.result())
    expected = sum(len(v) for v in wanted.values())
    produced = [r for r in rows if "page" in r]
    summary = {
        "manifest_panels": expected,
        "manifest_rows_without_panel": NO_PANEL,
        "digitized_panels": len(produced),
        "not_detected": expected - len(produced),
        "pdf_errors": [r for r in rows if "page" not in r],
        "changed": [r for r in produced if r.get("changed")],
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(f"manifest rows without a panel {len(NO_PANEL)}; manifest panels {expected}, digitized {len(produced)}, not detected {expected - len(produced)}, "
          f"pdf errors {len(summary['pdf_errors'])}, changed {len(summary['changed'])}")
    for r in summary["changed"]:
        print(" ", Path(r["pdf"]).stem, f"p{r['page']}d{r['diagram']}", r.get("a"), "->", r.get("b"),
              {k: (v["a_x"], v["b_x"]) for k, v in (r.get("traces") or {}).items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
