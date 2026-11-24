#!/usr/bin/env python3
"""Tabulate ``chart_medium_census.py`` rows; optionally draw image samples to look at.

Two estimators, kept apart because they answer different questions:

* per found panel: curve medium and label medium by chart class and by vendor;
* per sampled PDF: does it hold any raster chart panel, any figure-sized image no
  panel claims, pages without a text layer.

Vendor strata are sampled at different rates, so corpus-level shares weight each
vendor by population / completed sample.  A PDF whose finder run failed or timed
out stays in the denominator as its own column; it is never counted as vector.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

CLASS_ORDER = [
    "capacitances", "gate_charge", "transfer", "rds_on", "body_diode", "breakdown_voltage",
    "threshold_voltage", "output", "safe_operating_area", "thermal_impedance", "chart",
]


VECTOR = ("vector", "vector_filled", "both")  # "both" is a vector chart over a background image


def _vec(counter) -> float:
    return sum(counter[k] for k in VECTOR)


def load(run: Path) -> tuple[list[dict], dict]:
    rows = [json.loads(line) for line in (run / "pdf_rows.jsonl").read_text().splitlines() if line.strip()]
    return rows, json.loads((run / "provenance.json").read_text())


def pct(n: float, d: float) -> str:
    return "n/a" if d <= 0 else f"{100.0 * n / d:.1f} %"


def table(header: list[str], body: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(header))) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def report(rows: list[dict], prov: dict) -> str:
    pop = prov["population"]
    done = Counter(r["vendor"] for r in rows)
    weight = {v: pop[v] / done[v] for v in done}
    ok = [r for r in rows if r["finder_status"] == "ok" and r.get("measure_status") == "ok"]
    lines: list[str] = []
    lines.append(f"Run: commit {prov['dsdig_commit'][:7]} (src dirty: {prov['src_dirty']}), seed {prov['seed']}, "
                 f"{prov['per_vendor']} per vendor, dpi {prov['dpi']}, sample list sha256 {prov['sample_list_sha256'][:12]}.")
    lines.append(f"Completed {len(rows)} of {prov['sample_size']} sampled PDFs ({sum(pop.values())} canonical in "
                 f"{len(pop)} vendors). Finder status: {dict(Counter(r['finder_status'] for r in rows))}; "
                 f"measure status: {dict(Counter(str(r.get('measure_status')) for r in rows))}.")

    # --- per vendor, PDF level
    body = []
    tot = defaultdict(float)
    for v in sorted(done, key=lambda k: -pop[k]):
        vr = [r for r in rows if r["vendor"] == v]
        vok = [r for r in vr if r["finder_status"] == "ok" and r.get("measure_status") == "ok"]
        n_fail = len(vr) - len(vok)
        n_none = sum(1 for r in vok if not r["panels"])
        pan = [p for r in vok for p in r["panels"]]
        med = Counter(p["curve_medium"] for p in pan)
        any_raster = sum(1 for r in vok if any(p["curve_medium"] == "raster" for p in r["panels"]))
        unclaimed = sum(1 for r in vok for pg in r["pages"] for im in pg["figure_images"] if not im["claimed"])
        notext = sum(1 for p in pan if p["label_medium"] == "no_text")
        body.append([
            v, str(pop[v]), str(len(vr)), str(n_fail), str(n_none), str(len(pan)),
            pct(_vec(med), len(pan)), pct(med["raster"], len(pan)), pct(med["none"], len(pan)),
            pct(notext, len(pan)), pct(any_raster, len(vok)), f"{unclaimed / max(len(vok), 1):.1f}",
        ])
        w = weight[v]
        for key, value in (("pdf", len(vr)), ("fail", n_fail), ("none", n_none), ("pan", len(pan)), ("vec", _vec(med)),
                           ("ras", med["raster"]), ("oth", med["none"]), ("vfill", med["vector_filled"]),
                           ("both", med["both"]), ("notext", notext), ("anyr", any_raster), ("okpdf", len(vok)),
                           ("uncl", unclaimed)):
            tot[key] += w * value
    body.append([
        "**corpus (weighted)**", str(sum(pop.values())), str(len(rows)), pct(tot["fail"], tot["pdf"]),
        pct(tot["none"], tot["okpdf"]), f"{tot['pan']:.0f}", pct(tot["vec"], tot["pan"]), pct(tot["ras"], tot["pan"]),
        pct(tot["oth"], tot["pan"]), pct(tot["notext"], tot["pan"]), pct(tot["anyr"], tot["okpdf"]),
        f"{tot['uncl'] / max(tot['okpdf'], 1):.1f}",
    ])
    lines.append("\n### By vendor\n")
    lines.append(table(
        ["vendor", "canonical PDFs", "sampled", "finder failed", "PDFs with no panel", "panels found",
         "vector", "raster", "neither", "labels not text", "PDFs with a raster panel", "unclaimed images per PDF"],
        body))

    lines.append(f"\nOf the weighted vector share, stroked paths are {pct(tot['vec'] - tot['vfill'] - tot['both'], tot['pan'])} of panels, "
                 f"filled outlines {pct(tot['vfill'], tot['pan'])}, paths over a background image {pct(tot['both'], tot['pan'])}. "
                 "\"neither\" panels have no image, no strokes and fewer than the filled-path minimum.")
    # --- per class, panel level (weighted)
    cls = defaultdict(lambda: defaultdict(float))
    raw = Counter()
    for r in ok:
        w = weight[r["vendor"]]
        for p in r["panels"]:
            c = cls[p["kind"]]
            c["n"] += w
            c[p["curve_medium"]] += w
            c["notext"] += w * (p["label_medium"] == "no_text")
            c["ocr"] += w * (p["text_source"] == "tesseract_fallback")
            raw[p["kind"]] += 1
    kinds = [k for k in CLASS_ORDER if k in cls] + sorted(k for k in cls if k not in CLASS_ORDER)
    body = [[k, str(raw[k]), f"{cls[k]['n']:.0f}", pct(_vec(cls[k]), cls[k]["n"]), pct(cls[k]["raster"], cls[k]["n"]),
             pct(cls[k]["none"], cls[k]["n"]), pct(cls[k]["notext"], cls[k]["n"]),
             pct(cls[k]["ocr"], cls[k]["n"])] for k in kinds]
    lines.append("\n### By chart class (finder `kind`; shares weighted to the corpus)\n")
    lines.append(table(["class", "panels in sample", "est. panels in corpus", "vector", "raster", "neither",
                        "labels not text", "found via OCR captions"], body))

    # --- class x vendor raster share for the larger vendors
    big = [v for v in sorted(done, key=lambda k: -pop[k]) if pop[v] >= 300]
    main = ["capacitances", "gate_charge", "transfer", "rds_on", "body_diode"]
    body = []
    for v in big:
        cells = [v]
        for k in main:
            pan = [p for r in ok if r["vendor"] == v for p in r["panels"] if p["kind"] == k]
            n_r = sum(1 for p in pan if p["curve_medium"] == "raster")
            cells.append(f"{n_r}/{len(pan)}")
        body.append(cells)
    lines.append("\n### Raster panels / panels found, class by vendor (vendors with 300+ PDFs, unweighted counts)\n")
    lines.append(table(["vendor"] + main, body))

    # --- page level
    pg = Counter()
    for r in ok:
        w = weight[r["vendor"]]
        for page in r["pages"]:
            if page["panels"] or page["figure_images"]:
                pg["pages"] += w
                pg["no_text_layer"] += w * (not page["has_text_layer"])
                pg["unclaimed_pages"] += w * any(not im["claimed"] for im in page["figure_images"])
    lines.append("\n### Pages\n")
    lines.append(f"Pages with a found panel or a figure-sized image (weighted): {pg['pages']:.0f}; of these without a "
                 f"native text layer: {pct(pg['no_text_layer'], pg['pages'])}; with an image no panel claims: "
                 f"{pct(pg['unclaimed_pages'], pg['pages'])}.")
    return "\n".join(lines) + "\n"


def unclaimed_sample(rows: list[dict], n: int, seed: int) -> list[dict]:
    """Seeded sample of unclaimed figure images, drawn per vendor in turn."""
    by_vendor: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        for page in r.get("pages", []):
            for im in page["figure_images"]:
                if not im["claimed"]:
                    by_vendor[r["vendor"]].append({"vendor": r["vendor"], "pdf": r["pdf"], "page": page["page"],
                                                   "bbox_pt": im["bbox_pt"], "has_text_layer": page["has_text_layer"]})
    rng = random.Random(seed)
    for items in by_vendor.values():
        rng.shuffle(items)
    out: list[dict] = []
    vendors = sorted(by_vendor)
    while len(out) < n and any(by_vendor[v] for v in vendors):
        for v in vendors:
            if by_vendor[v] and len(out) < n:
                out.append(by_vendor[v].pop())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("run", type=Path)
    ap.add_argument("--unclaimed-sample", type=int, default=0, help="write this many unclaimed images as PNGs to look at")
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--root", type=Path, help="datasheet library root (needed for --unclaimed-sample)")
    args = ap.parse_args()
    rows, prov = load(args.run)
    text = report(rows, prov)
    (args.run / "report.md").write_text(text)
    print(text)
    if args.unclaimed_sample:
        import pymupdf

        out = args.run / "unclaimed-sample"
        out.mkdir(exist_ok=True)
        items = unclaimed_sample(rows, args.unclaimed_sample, args.seed)
        for i, it in enumerate(items):
            with pymupdf.open(args.root / it["pdf"]) as doc:
                page = doc[it["page"] - 1]
                clip = pymupdf.Rect(*it["bbox_pt"]) + (-12, -12, 12, 12)
                pix = page.get_pixmap(clip=clip & page.rect, dpi=72)
                it["png"] = f"{i:03d}_{it['vendor']}.png"
                pix.save(out / it["png"])
        (out / "items.json").write_text(json.dumps(items, indent=1))
        print(f"wrote {len(items)} images to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
