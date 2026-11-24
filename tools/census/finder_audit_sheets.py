#!/usr/bin/env python3
"""Page renders with the finder's panel boxes drawn on, for counting finder misses by eye.

Draws a population-weighted, seeded sample of PDFs from a medium-census run.  For
each sampled PDF it renders every page that could hold a chart (a found panel, a
figure-sized image, or enough vector strokes) and draws each found panel's
``crop_box_pt`` with its kind.  A person or a reviewer then counts, per page:
charts present, charts with no box, boxes with a defect, boxes on a non-chart.

The page filter only drops pages with no image and few strokes (text and table
pages); the thresholds are recorded in ``items.json``.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

MIN_STROKES = 60  # a page with fewer stroked paths and no figure image is skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("run", type=Path)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pdfs", type=int, default=30)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--width", type=int, default=900, help="page render width in px")
    args = ap.parse_args()
    rows = [json.loads(line) for line in (args.run / "pdf_rows.jsonl").read_text().splitlines() if line.strip()]
    prov = json.loads((args.run / "provenance.json").read_text())
    ok = [r for r in rows if r["finder_status"] == "ok" and r.get("measure_status") == "ok"]
    done: dict[str, int] = {}
    for r in rows:
        done[r["vendor"]] = done.get(r["vendor"], 0) + 1
    rng = random.Random(f"finder-audit:{args.seed}")
    # weighted sample without replacement (Efraimidis-Spirakis): weight = PDFs each row stands for
    keyed = sorted(ok, key=lambda r: rng.random() ** (1.0 / (prov["population"][r["vendor"]] / done[r["vendor"]])), reverse=True)
    chosen = sorted(keyed[:args.pdfs], key=lambda r: r["pdf"])
    args.out.mkdir(parents=True, exist_ok=True)
    items = []
    for r in chosen:
        by_page: dict[int, list[dict]] = {}
        for p in r["panels"]:
            by_page.setdefault(p["page"], []).append(p)
        image_pages = {pg["page"] for pg in r["pages"] if pg["figure_images"]}
        with pymupdf.open(args.root / r["pdf"]) as doc:
            for index, page in enumerate(doc, start=1):
                strokes = 0
                if index not in by_page and index not in image_pages:
                    strokes = sum(1 for d in page.get_drawings() if d.get("type") in ("s", "fs"))
                    if strokes < MIN_STROKES:
                        continue
                zoom = args.width / page.rect.width
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                draw = ImageDraw.Draw(img)
                for p in by_page.get(index, []):
                    x0, y0, x1, y1 = (v * zoom for v in p["crop_box_pt"])
                    draw.rectangle((x0, y0, x1, y1), outline=(0, 90, 255), width=3)
                    draw.text((x0 + 4, y0 + 3), f"{p['kind']} [{p['curve_medium']}]", fill=(0, 90, 255))
                name = f"{len(items):03d}_{r['vendor']}_{Path(r['pdf']).stem}_p{index:02d}.png".replace(" ", "_")
                draw.text((6, 4), f"#{len(items)} {r['pdf']} p{index}  boxes={len(by_page.get(index, []))}", fill="red")
                img.save(args.out / name)
                items.append({"index": len(items), "vendor": r["vendor"], "pdf": r["pdf"], "page": index, "png": name,
                              "boxes": [{"kind": p["kind"], "curve_medium": p["curve_medium"]} for p in by_page.get(index, [])],
                              "has_figure_image": index in image_pages})
    (args.out / "items.json").write_text(json.dumps({
        "seed": args.seed, "pdfs": [r["pdf"] for r in chosen], "min_strokes": MIN_STROKES,
        "census_commit": prov["dsdig_commit"], "items": items,
    }, indent=1))
    print(f"{len(chosen)} PDFs, {len(items)} pages -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
