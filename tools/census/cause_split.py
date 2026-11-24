#!/usr/bin/env python3
"""Seeded sample of axis-calibration stops and margin renders for classifying their cause by eye.

``sample``: from the refusal census's ``charts.jsonl``, draw a stratified sample of
charts whose stage is ``axis calibration``, separately per chart medium.  Rows are
shuffled with a seed per medium and accepted in that order while every cap holds
(per class x vendor, per vendor, per class, per PDF).  The caps, the seed, the
population and the list hash go to ``sample.json``; nothing is hand-picked.

``render``: for each sampled chart, render the page region around its recorded
``crop_box_pt`` with a margin (so tick labels and titles outside the crop are
visible), draw the crop in red, and write the native PyMuPDF words of that region
next to it.  Whether the labels exist as text is read from those words, not
inferred from the reason string.

``table``: join ``sample.json`` with the hand-made ``labels.tsv`` (one row per
sample id: cause, sub-cause, boxes_suffice, ocr_suffices, crop_secondary, note) and
print the cause x medium, cause x class and sufficiency tables as Markdown.  It
also counts, over the whole axis-calibration population, the rows whose reasons
also match a later stage rule (the stage is assigned by rule priority).

Nothing here classifies a cause; that is done by looking at the renders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from chart_medium_census import _is_numeric
from refusal_report import _RULES, reason_texts

STAGE = "axis calibration"
CAUSES = ("crop_wrong", "label_ownership", "labels_not_text", "ocr_misread", "grid_or_frame",
          "genuinely_unreadable", "other")
PHANTOM_SUBS = {"wrong_class", "not_a_chart"}
_STAGES = [stage for stage, _ in _RULES]
LATER_RULES = _RULES[_STAGES.index(STAGE) + 1:]


def _key(c: dict) -> str:
    return f"{c['pdf']}|{c['class']}|p{c.get('page')}|d{c.get('diagram')}"


def draw(rows: list[dict], medium: str, n: int, seed: int, caps: dict[str, int]) -> list[dict]:
    pool = sorted((c for c in rows if c.get("stage") == STAGE and c.get("medium_census") == medium), key=_key)
    random.Random(f"{seed}:{medium}").shuffle(pool)
    used: dict[str, Counter] = {k: Counter() for k in caps}
    out: list[dict] = []
    for c in pool:
        keys = {"class_vendor": (c["class"], c["vendor"]), "vendor": c["vendor"], "class": c["class"], "pdf": c["pdf"]}
        if any(used[k][keys[k]] >= caps[k] for k in caps):
            continue
        for k in caps:
            used[k][keys[k]] += 1
        out.append(c)
        if len(out) == n:
            break
    return out


def cmd_sample(args: argparse.Namespace) -> int:
    rows = [json.loads(line) for line in args.charts.read_text().splitlines() if line.strip()]
    caps = {"class_vendor": args.cap_class_vendor, "vendor": args.cap_vendor, "class": args.cap_class, "pdf": args.cap_pdf}
    result: dict = {"source": str(args.charts), "source_sha256": hashlib.sha256(args.charts.read_bytes()).hexdigest(),
                    "seed": args.seed, "caps": caps, "samples": {}}
    for medium in ("vector", "raster"):
        pop = [c for c in rows if c.get("stage") == STAGE and c.get("medium_census") == medium]
        chosen = draw(rows, medium, args.n, args.seed, caps)
        for i, c in enumerate(chosen):
            c["sample_id"] = f"{medium[0]}{i:02d}"
        listing = "\n".join(_key(c) for c in chosen)
        result["samples"][medium] = {"population": len(pop), "drawn": len(chosen),
                                     "list_sha256": hashlib.sha256(listing.encode()).hexdigest(), "charts": chosen}
        print(f"{medium}: population {len(pop)}, drawn {len(chosen)}, "
              f"classes {dict(Counter(c['class'] for c in chosen))}, vendors {len({c['vendor'] for c in chosen})}")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "sample.json").write_text(json.dumps(result, indent=1))
    return 0


def render_one(c: dict, root: Path, out: Path, margin: float, px: int) -> dict:
    crop = pymupdf.Rect(*c["crop_box_pt"])
    with pymupdf.open(root / c["pdf"]) as doc:
        page = doc[c["page"] - 1]
        m = max(margin, 0.25 * max(crop.width, crop.height))
        clip = (crop + (-m, -m, m, m)) & page.rect
        zoom = px / max(clip.width, clip.height, 1.0)
        pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        words = [w for w in page.get_text("words") if clip.contains(pymupdf.Rect(w[:4]))]
        images = [i["bbox"] for i in page.get_image_info() if pymupdf.Rect(i["bbox"]).intersects(crop)]
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    d = ImageDraw.Draw(img)
    d.rectangle([(crop.x0 - clip.x0) * zoom, (crop.y0 - clip.y0) * zoom, (crop.x1 - clip.x0) * zoom,
                 (crop.y1 - clip.y0) * zoom], outline="red", width=3)
    name = c["sample_id"]
    img.save(out / f"{name}.png")

    def where(w) -> str:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if crop.contains(pymupdf.Point(cx, cy)):
            return "in"
        return "below" if cy > crop.y1 else "above" if cy < crop.y0 else "left" if cx < crop.x0 else "right"

    info = {k: c.get(k) for k in ("sample_id", "pdf", "vendor", "class", "page", "diagram", "title", "status",
                                  "stage_reason", "reasons", "crop_box_pt", "medium_census")}
    info.update(clip_pt=[round(v, 1) for v in clip], zoom=round(zoom, 2), images_over_crop=len(images),
                numeric_words=[[w[4], where(w), round(w[0], 1), round(w[1], 1)] for w in words if _is_numeric(w[4])],
                words_in_region=len(words))
    (out / f"{name}.json").write_text(json.dumps(info, indent=1))
    return info


def cmd_render(args: argparse.Namespace) -> int:
    data = json.loads(args.sample.read_text())
    out = args.out or args.sample.parent / "renders"
    out.mkdir(parents=True, exist_ok=True)
    for medium, s in data["samples"].items():
        for c in s["charts"]:
            if args.only and c["sample_id"] not in args.only.split(","):
                continue
            info = render_one(c, args.root, out, args.margin_pt, args.px)
            print(f"{info['sample_id']} {medium} {c['class']} {c['pdf']} p{c['page']}: "
                  f"{len(info['numeric_words'])} numeric words, {info['images_over_crop']} images")
    return 0


def _md(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def _crosstab(labels: list[dict], row_key: str, cols: list[str], col_key: str) -> str:
    keys = sorted({lab[row_key] for lab in labels}, key=lambda k: (CAUSES.index(k) if k in CAUSES else 99, k))
    rows = []
    for k in keys:
        counts = [sum(1 for lab in labels if lab[row_key] == k and lab[col_key] == c) for c in cols]
        rows.append([k, *counts, sum(counts)])
    rows.append(["total", *[sum(1 for lab in labels if lab[col_key] == c) for c in cols], len(labels)])
    return _md([row_key, *cols, "total"], rows)


def later_stages(chart: dict) -> list[str]:
    """Stages after axis calibration whose rule also matches one of the chart's reasons."""
    texts = [t.split(": ", 1)[-1] for t in reason_texts(chart)]
    return [s for s, rx in LATER_RULES if any(rx.search(t) for t in texts)]


def cmd_table(args: argparse.Namespace) -> int:
    data = json.loads(args.sample.read_text())
    charts = {c["sample_id"]: (medium, c) for medium, s in data["samples"].items() for c in s["charts"]}
    raw = [line.split("\t") for line in args.labels.read_text().splitlines() if line.strip()]
    header, body = raw[0], raw[1:]
    labels = []
    for row in body:
        lab = dict(zip(header, row, strict=True))
        if lab["id"] not in charts:
            raise SystemExit(f"label {lab['id']} is not in the sample")
        if lab["cause"] not in CAUSES:
            raise SystemExit(f"{lab['id']}: unknown cause {lab['cause']!r}")
        medium, c = charts[lab["id"]]
        lab.update(medium=medium, cls=c["class"], phantom="yes" if lab["sub"] in PHANTOM_SUBS else "no")
        labels.append(lab)
    missing = sorted(set(charts) - {lab["id"] for lab in labels})
    if missing:
        raise SystemExit(f"unlabelled sample ids: {missing}")
    media = ["vector", "raster"]
    classes = sorted({lab["cls"] for lab in labels})
    print("## cause x medium\n\n" + _crosstab(labels, "cause", media, "medium"))
    print("\n## cause x class (both media)\n\n" + _crosstab(labels, "cause", classes, "cls"))
    for medium in media:
        sub = [lab for lab in labels if lab["medium"] == medium]
        print(f"\n## sub-cause, {medium}\n\n" + _md(
            ["cause", "sub", "n"], [[*k, n] for k, n in sorted(Counter((lab["cause"], lab["sub"]) for lab in sub).items())]))
    rows = []
    for col in ("boxes_suffice", "ocr_suffices", "crop_secondary"):
        for medium in media:
            for scope, keep in (("all", lambda lab: True), ("excl. phantom", lambda lab: lab["phantom"] == "no")):
                sel = [lab for lab in labels if lab["medium"] == medium and keep(lab)]
                n = Counter(lab[col] for lab in sel)
                rows.append([col, medium, scope, len(sel), n["yes"], n["no"], n["unsure"]])
    print("\n## sufficiency\n\n" + _md(["column", "medium", "scope", "n", "yes", "no", "unsure"], rows))
    print(f"\nphantoms: {dict(Counter(lab['medium'] for lab in labels if lab['phantom'] == 'yes'))}")
    if args.charts:
        pop = [json.loads(line) for line in args.charts.read_text().splitlines() if line.strip()]
        pop = [c for c in pop if c.get("stage") == STAGE]
        rows = []
        for medium in ("vector", "raster", "all"):
            sel = [c for c in pop if medium == "all" or c.get("medium_census") == medium]
            hits = [later_stages(c) for c in sel]
            per = Counter(s for h in hits for s in set(h))
            rows.append([medium, len(sel), sum(1 for h in hits if h), *[per[s] for s, _ in LATER_RULES]])
        print("\n## axis-calibration rows whose reasons also match a later stage\n\n"
              + _md(["medium", "rows", "any later stage", *[s for s, _ in LATER_RULES]], rows))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("charts", type=Path, help="charts.jsonl of a refusal_report.py run")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--n", type=int, default=50, help="charts per medium")
    s.add_argument("--seed", type=int, default=19)
    s.add_argument("--cap-class-vendor", type=int, default=2)
    s.add_argument("--cap-vendor", type=int, default=5)
    s.add_argument("--cap-class", type=int, default=12)
    s.add_argument("--cap-pdf", type=int, default=2)
    s.set_defaults(fn=cmd_sample)
    r = sub.add_parser("render")
    r.add_argument("sample", type=Path, help="sample.json written by 'sample'")
    r.add_argument("--root", type=Path, required=True, help="datasheet library root")
    r.add_argument("--out", type=Path)
    r.add_argument("--margin-pt", type=float, default=36.0, help="minimum margin; at least 25 %% of the crop's long edge")
    r.add_argument("--px", type=int, default=1400, help="long edge of each render")
    r.add_argument("--only", default="", help="comma list of sample ids")
    r.set_defaults(fn=cmd_render)
    t = sub.add_parser("table")
    t.add_argument("sample", type=Path, help="sample.json written by 'sample'")
    t.add_argument("labels", type=Path, help="labels.tsv: id, cause, sub, boxes_suffice, ocr_suffices, crop_secondary, note")
    t.add_argument("--charts", type=Path, help="charts.jsonl, for the later-stage count over the population")
    t.set_defaults(fn=cmd_table)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
