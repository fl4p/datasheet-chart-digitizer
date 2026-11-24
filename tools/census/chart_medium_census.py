#!/usr/bin/env python3
"""Census of chart media in a datasheet corpus: vector or raster curves, text or no text.

Read-only over the corpus.  For a seeded, vendor-stratified sample of canonical
PDFs it runs the production finder (``find_charts.process_pdf``) and records, per
found panel, what its curves and tick labels are made of:

* ``curve_medium``: ``vector`` (stroked PDF paths in the crop), ``raster`` (an
  embedded image covers the crop and there are no such paths), ``both`` (paths
  over an image, in practice a watermark), ``vector_filled`` (no strokes, no
  image, but many filled paths: curves drawn as outlines) or ``none``.
* ``label_medium``: ``text`` when the crop holds a native text layer with
  numeric tokens, else ``no_text`` (labels are raster pixels or outlined glyphs).

Panels the finder does not return are invisible here, and the finder is weaker
on raster pages.  So each PDF row also lists, per page, every figure-sized
embedded image with the number of found panels inside it (``claimed`` is false
when no panel lies in or over it) and whether the page has a native text layer.  Those are candidates only; what share of them are
charts has to be measured by looking at a sample (``--sheet`` in
``census_report.py``).

Nothing here is a verdict about a chart.  Every threshold below is a census
classification rule, recorded in ``provenance.json`` with the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pymupdf

from datasheet_chart_digitizer.find_charts import process_pdf

BBox = tuple[float, float, float, float]

RULES = {
    # an embedded image counts as figure-sized from this edge length (pt); same
    # floor as finder_plot_frames.page_image_rects
    "figure_image_min_pt": 90.0,
    # a crop is image-backed when images cover at least this share of it
    "raster_min_image_cover": 0.5,
    # a crop has vector curves/grid when stroked path length inside it is at
    # least this many crop diagonals (a frame alone is ~2.8 diagonals)
    "vector_min_stroke_diagonals": 4.0,
    # with no stroked paths and no image, a crop still has vector curves when it
    # holds at least this many filled-path items (curves and grids drawn as
    # filled outlines); fewer is a table, a waveform sketch or an empty crop
    "vector_filled_min_items": 40,
    # native numeric tokens needed in the crop to call its labels "text"
    "text_min_numeric_words": 4,
    # a page has a native text layer from this many words
    "page_text_min_words": 20,
    # an image no panel centre lies in is still claimed when a panel crop
    # covers at least this share of it
    "unclaimed_max_overlap": 0.2,
}


def canonical_pdfs(root: Path) -> dict[str, list[Path]]:
    """Canonical PDFs per vendor directory; generated ``*.pdf.<x>.pdf`` copies excluded."""
    by_vendor: dict[str, list[Path]] = {}
    for vendor_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        if vendor_dir.name.startswith("_") or vendor_dir.name == "datasheets":
            continue
        pdfs = sorted(
            p for p in vendor_dir.iterdir()
            if p.is_file() and p.name.lower().endswith(".pdf") and ".pdf." not in p.name[:-4].lower()
        )
        if pdfs:
            by_vendor[vendor_dir.name] = pdfs
    return by_vendor


def draw_sample(by_vendor: dict[str, list[Path]], per_vendor: int, seed: int) -> list[tuple[str, Path]]:
    sample: list[tuple[str, Path]] = []
    for vendor, pdfs in by_vendor.items():
        rng = random.Random(f"{seed}:{vendor}")
        chosen = pdfs if len(pdfs) <= per_vendor else rng.sample(pdfs, per_vendor)
        sample.extend((vendor, p) for p in sorted(chosen))
    return sample


def _area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _clip(a: BBox, b: BBox) -> BBox:
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _cover(crop: BBox, rects: list[BBox]) -> float:
    """Share of ``crop`` covered by ``rects`` (union, on a 40x40 grid)."""
    if not rects or _area(crop) <= 0:
        return 0.0
    n = 40
    hit = 0
    for i in range(n):
        x = crop[0] + (i + 0.5) * (crop[2] - crop[0]) / n
        for j in range(n):
            y = crop[1] + (j + 0.5) * (crop[3] - crop[1]) / n
            if any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in rects):
                hit += 1
    return hit / (n * n)


def _inside(x: float, y: float, b: BBox) -> bool:
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def stroke_length_in(drawings: list[dict], crop: BBox) -> tuple[float, int]:
    """Total length (pt) and count of stroked segments lying inside ``crop``."""
    total = 0.0
    count = 0
    for d in drawings:
        if d.get("type") not in ("s", "fs") or d.get("color") is None:
            continue
        for item in d.get("items", ()):
            op = item[0]
            if op == "l":
                pts = [item[1], item[2]]
            elif op == "c":
                pts = [item[1], item[2], item[3], item[4]]
            elif op == "re":
                r = item[1]
                pts = [r.tl, r.tr, r.br, r.bl, r.tl]
            elif op == "qu":
                q = item[1]
                pts = [q.ul, q.ur, q.lr, q.ll, q.ul]
            else:
                continue
            if not all(_inside(p.x, p.y, crop) for p in pts):
                continue
            total += sum(math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(pts, pts[1:]))
            count += 1
    return total, count


def fill_items_in(drawings: list[dict], crop: BBox, slack: float = 2.0) -> int:
    """Number of items of fill-only paths whose rectangle lies inside ``crop``."""
    n = 0
    for d in drawings:
        if d.get("type") != "f":
            continue
        r = d["rect"]
        if r.x0 >= crop[0] - slack and r.x1 <= crop[2] + slack and r.y0 >= crop[1] - slack and r.y1 <= crop[3] + slack:
            n += len(d.get("items", ()))
    return n


def classify_medium(stroke_diagonals: float, image_cover: float, fill_items: int) -> str:
    has_vec = stroke_diagonals >= RULES["vector_min_stroke_diagonals"]
    has_img = image_cover >= RULES["raster_min_image_cover"]
    if has_vec and has_img:
        return "both"
    if has_vec:
        return "vector"
    if has_img:
        return "raster"
    return "vector_filled" if fill_items >= RULES["vector_filled_min_items"] else "none"


def _is_numeric(token: str) -> bool:
    t = token.strip().replace(",", ".").replace("−", "-")
    try:
        float(t)
    except ValueError:
        return False
    return True


class _Timeout(Exception):
    pass


def _alarm(_signum, _frame):
    raise _Timeout()


def census_pdf(vendor: str, root: Path, rel: str, dpi: int, timeout_s: int) -> dict:
    pdf = root / rel
    row: dict = {"vendor": vendor, "pdf": rel, "part": pdf.stem}
    t0 = time.time()
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(timeout_s)
    try:
        with tempfile.TemporaryDirectory(prefix="census-find-") as tmp:
            panels = process_pdf(pdf, Path(tmp), dpi)
        row["finder_status"] = "ok"
    except _Timeout:
        signal.alarm(0)
        row.update(finder_status="timeout", panels=[], pages=[], seconds=round(time.time() - t0, 1))
        return row
    except Exception as exc:  # the finder failing is a census result, not a crash
        signal.alarm(0)
        row.update(
            finder_status="error", finder_error=f"{type(exc).__name__}: {exc}"[:300],
            traceback=traceback.format_exc()[-1500:], panels=[], pages=[],
            seconds=round(time.time() - t0, 1),
        )
        return row
    try:
        row["panels"], row["pages"] = _measure(pdf, panels)
        row["measure_status"] = "ok"
    except _Timeout:
        row.update(measure_status="timeout", panels=[], pages=[])
    except Exception as exc:
        row.update(measure_status="error", measure_error=f"{type(exc).__name__}: {exc}"[:300], panels=[], pages=[])
    finally:
        signal.alarm(0)
    row["seconds"] = round(time.time() - t0, 1)
    return row


def _measure(pdf: Path, panels: list) -> tuple[list[dict], list[dict]]:
    by_page: dict[int, list] = {}
    for p in panels:
        by_page.setdefault(int(p.page), []).append(p)
    out_panels: list[dict] = []
    out_pages: list[dict] = []
    with pymupdf.open(pdf) as doc:
        for index, page in enumerate(doc, start=1):
            words = page.get_text("words")
            images = [
                tuple(float(v) for v in info["bbox"])
                for info in page.get_image_info()
            ]
            fig_images = [
                b for b in images
                if b[2] - b[0] >= RULES["figure_image_min_pt"] and b[3] - b[1] >= RULES["figure_image_min_pt"]
            ]
            page_panels = by_page.get(index, [])
            crops = [tuple(float(v) for v in p.crop_box_pt) for p in page_panels]
            drawings = page.get_drawings() if page_panels else []
            for p, crop in zip(page_panels, crops):
                length, n_seg = stroke_length_in(drawings, crop)
                diag = math.hypot(crop[2] - crop[0], crop[3] - crop[1]) or 1.0
                cover = _cover(crop, fig_images)
                in_crop = [w[4] for w in words if _inside((w[0] + w[2]) / 2, (w[1] + w[3]) / 2, crop)]
                n_num = sum(1 for w in in_crop if _is_numeric(w))
                fills = fill_items_in(drawings, crop)
                medium = classify_medium(length / diag, cover, fills)
                out_panels.append({
                    "page": index, "diagram": p.diagram, "kind": p.kind, "title": p.title[:80],
                    "text_source": p.text_source, "crop_box_pt": [round(v, 1) for v in crop],
                    "curve_medium": medium,
                    "label_medium": "text" if n_num >= RULES["text_min_numeric_words"] else "no_text",
                    "image_cover": round(cover, 3), "stroke_diagonals": round(length / diag, 2),
                    "stroke_segments": n_seg, "fill_items": fills, "native_words": len(in_crop), "native_numeric_words": n_num,
                })
            image_rows = []
            for b in fig_images:
                overlap = max((_area(_clip(b, c)) for c in crops), default=0.0) / max(_area(b), 1e-9)
                inside = sum(1 for c in crops if _inside((c[0] + c[2]) / 2, (c[1] + c[3]) / 2, b))
                image_rows.append({
                    "bbox_pt": [round(v, 1) for v in b], "panels_inside": inside,
                    "claimed": inside > 0 or overlap >= RULES["unclaimed_max_overlap"],
                })
            if page_panels or fig_images:
                out_pages.append({
                    "page": index, "native_words": len(words),
                    "has_text_layer": len(words) >= RULES["page_text_min_words"],
                    "figure_images": image_rows,
                    "page_size_pt": [round(page.rect.width, 1), round(page.rect.height, 1)],
                    "panels": len(page_panels),
                })
    return out_panels, out_pages


def _versions() -> dict[str, str]:
    """Versions of what the finder's raster and OCR paths depend on (host-sensitive)."""
    out = {"python": platform.python_version(), "pymupdf": pymupdf.__version__}
    for name, cmd in (("tesseract", ["tesseract", "--version"]), ("pdftotext", ["pdftotext", "-v"])):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            out[name] = (proc.stdout + proc.stderr).strip().splitlines()[0]
        except (OSError, subprocess.TimeoutExpired, IndexError) as exc:
            out[name] = f"unavailable: {type(exc).__name__}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--root", type=Path, required=True, help="datasheet library root with one directory per vendor")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--per-vendor", type=int, default=120)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--dpi", type=int, default=180, help="finder render dpi (CLI default 180)")
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=300, help="seconds per PDF")
    ap.add_argument("--vendors", default="", help="comma list; default all")
    ap.add_argument("--dsdig-commit", default="", help="commit of the synced tree, for a host without the git repo")
    ap.add_argument("--list-only", action="store_true", help="draw the sample, write sample.tsv and provenance.json, stop")
    ap.add_argument("--sample-from", type=Path, help="run directory whose sample.tsv and population to reuse "
                    "(for running on a host that holds only the sampled PDFs)")
    args = ap.parse_args()

    if args.sample_from:
        prior = json.loads((args.sample_from / "provenance.json").read_text())
        population = prior["population"]
        sample = [tuple(line.split("\t")) for line in (args.sample_from / "sample.tsv").read_text().splitlines() if line]
        if hashlib.sha256("\n".join(f"{v}\t{p}" for v, p in sample).encode()).hexdigest() != prior["sample_list_sha256"]:
            raise SystemExit("sample.tsv does not match the recorded sample_list_sha256")
    else:
        by_vendor = canonical_pdfs(args.root)
        if args.vendors:
            keep = set(args.vendors.split(","))
            by_vendor = {k: v for k, v in by_vendor.items() if k in keep}
        population = {v: len(p) for v, p in by_vendor.items()}
        sample = [(v, str(p.relative_to(args.root))) for v, p in draw_sample(by_vendor, args.per_vendor, args.seed)]
    missing = [rel for _, rel in sample if not (args.root / rel).is_file()]
    if missing:
        raise SystemExit(f"{len(missing)} sampled PDFs are missing under {args.root}, e.g. {missing[:3]}")
    args.out.mkdir(parents=True, exist_ok=True)
    rows_path = args.out / "pdf_rows.jsonl"
    done = set()
    if rows_path.exists():
        for line in rows_path.read_text().splitlines():
            done.add(json.loads(line)["pdf"])
    todo = [(v, p) for v, p in sample if p not in done]

    repo = Path(__file__).resolve().parents[2]
    if args.dsdig_commit:
        commit, dirty = args.dsdig_commit, None  # synced tree: cleanliness is the caller's claim
    else:
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", "src"], capture_output=True, text=True).stdout.strip())
    listing = "\n".join(f"{v}\t{p}" for v, p in sample)
    (args.out / "provenance.json").write_text(json.dumps({
        "tool": "tools/census/chart_medium_census.py", "dsdig_commit": commit, "src_dirty": dirty,
        "root": str(args.root), "seed": prior["seed"] if args.sample_from else args.seed,
        "per_vendor": prior["per_vendor"] if args.sample_from else args.per_vendor, "dpi": args.dpi,
        "rules": RULES, "argv": sys.argv[1:],
        "population": population, "host": platform.node(), "platform": platform.platform(),
        "versions": _versions(),
        "sample_size": len(sample), "sample_list_sha256": hashlib.sha256(listing.encode()).hexdigest(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }, indent=1))
    (args.out / "sample.tsv").write_text(listing + "\n")
    print(f"population {sum(population.values())} canonical PDFs in {len(population)} vendors; "
          f"sample {len(sample)}; already done {len(done)}; to do {len(todo)}", flush=True)
    if args.list_only:
        return 0

    with rows_path.open("a") as fh, ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(census_pdf, v, args.root, p, args.dpi, args.timeout): (v, p) for v, p in todo}
        for n, fut in enumerate(as_completed(futures), start=1):
            vendor, pdf = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:  # worker died: keep the PDF in the denominator
                row = {"vendor": vendor, "pdf": pdf, "part": Path(pdf).stem, "finder_status": "worker_died",
                       "finder_error": f"{type(exc).__name__}: {exc}"[:300], "panels": [], "pages": []}
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if n % 25 == 0 or n == len(todo):
                print(f"{n}/{len(todo)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
