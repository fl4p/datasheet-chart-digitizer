#!/usr/bin/env python3
"""Full-corpus caption prepass plus exact affected-PDF A/B for RDS finder changes."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scan_pdf(pdf_text: str) -> dict[str, object]:
    from datasheet_chart_digitizer import find_charts
    from datasheet_chart_digitizer.finder_caption_geometry import caption_axis_direction

    pdf = Path(pdf_text)
    try:
        pages = find_charts.run_text_bbox(pdf)
        titles: list[dict[str, object]] = []
        refused: list[dict[str, object]] = []
        for page in pages:
            for title in find_charts.find_caption_titles(page):
                if find_charts.classify_chart(title.title, "") != "rds_on":
                    continue
                row = {
                "page": page.page_num,
                "diagram": title.number,
                "title": title.title,
                "bbox_pt": list(title.bbox_pt),
                }
                direction = caption_axis_direction(
                    page, title, "rds_on", find_charts._token_norm
                )
                row["axis_direction"] = direction
                (titles if direction is not None else refused).append(row)
        return {
            "pdf": pdf_text,
            "error": None,
            "rds_titles": titles,
            "rds_titles_without_axis_evidence": refused,
        }
    except Exception as error:  # fail-closed corpus accounting
        return {
            "pdf": pdf_text,
            "error": f"{type(error).__name__}: {error}",
            "rds_titles": [],
            "rds_titles_without_axis_evidence": [],
        }


def _panel_payload(panel, root: Path) -> dict[str, object]:
    row = asdict(panel)
    crop = root / row["crop_png"]
    row["crop_sha256"] = _sha256(crop)
    return row


def _affected_ab(pdf_text: str, page_nums: list[int], dpi: int) -> dict[str, object]:
    from datasheet_chart_digitizer import find_charts

    pdf = Path(pdf_text)
    try:
        pages = [
            page for page in find_charts.run_text_bbox(pdf)
            if page.page_num in set(page_nums)
        ]
        original = find_charts.find_caption_titles

        def without_rds(page):
            return [
                title
                for title in original(page)
                if find_charts.classify_chart(title.title, "") != "rds_on"
            ]

        with tempfile.TemporaryDirectory(prefix="rds-finder-ab-") as tmp:
            root = Path(tmp)
            find_charts.find_caption_titles = without_rds
            try:
                baseline = find_charts.process_page_texts(pdf, root / "baseline", dpi, pages)
            finally:
                find_charts.find_caption_titles = original
            candidate = find_charts.process_page_texts(pdf, root / "candidate", dpi, pages)
            baseline_rows = [_panel_payload(panel, root / "baseline") for panel in baseline]
            candidate_rows = [_panel_payload(panel, root / "candidate") for panel in candidate]
        return {
            "pdf": pdf_text,
            "page_nums": page_nums,
            "error": None,
            "baseline": baseline_rows,
            "candidate": candidate_rows,
            "changed": baseline_rows != candidate_rows,
        }
    except Exception as error:
        return {"pdf": pdf_text, "error": f"{type(error).__name__}: {error}"}


def _pdfs_from_manifest(path: Path) -> list[str]:
    rows = json.loads(path.read_text())
    return sorted({str(row["pdf"]) for row in rows})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--prepass-only", action="store_true")
    parser.add_argument("--reuse-prepass", action="store_true")
    args = parser.parse_args()
    pdfs = _pdfs_from_manifest(args.baseline_manifest)
    source_root = Path(__file__).resolve().parents[2] / "src" / "datasheet_chart_digitizer"
    source_sha256 = {
        name: _sha256(source_root / name)
        for name in ("find_charts.py", "finder_caption_geometry.py", "chart_classifier.py")
    }
    prepass_path = args.out.with_name("caption-prepass.json")
    baseline_sha256 = _sha256(args.baseline_manifest)
    if args.reuse_prepass:
        prepass = json.loads(prepass_path.read_text())
        if (
            prepass["baseline_manifest_sha256"] != baseline_sha256
            or prepass["source_sha256"] != source_sha256
        ):
            raise RuntimeError("prepass provenance does not match current inputs/source")
        scan_rows = prepass["scan_rows"]
    else:
        scan_rows: list[dict[str, object]] = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_scan_pdf, pdf): pdf for pdf in pdfs}
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                scan_rows.append(future.result())
                if index % 250 == 0 or index == len(futures):
                    print(f"caption-prepass {index}/{len(futures)}", flush=True)
        scan_rows.sort(key=lambda row: str(row["pdf"]))
        prepass = {
            "baseline_manifest": str(args.baseline_manifest.resolve()),
            "baseline_manifest_sha256": baseline_sha256,
            "source_sha256": source_sha256,
            "corpus_count": len(pdfs),
            "scan_rows": scan_rows,
        }
        prepass_path.parent.mkdir(parents=True, exist_ok=True)
        prepass_path.write_text(json.dumps(prepass, indent=2) + "\n")
    affected = [
        (
            str(row["pdf"]),
            sorted({int(title["page"]) for title in row["rds_titles"]}),
        )
        for row in scan_rows if row["rds_titles"]
    ]
    print(f"evidenced-affected {len(affected)}/{len(pdfs)}", flush=True)
    print(prepass_path)
    print(_sha256(prepass_path))
    if args.prepass_only:
        return
    ab_rows: list[dict[str, object]] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_affected_ab, pdf, pages, args.dpi): pdf
            for pdf, pages in affected
        }
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            ab_rows.append(future.result())
            if index % 25 == 0 or index == len(futures):
                print(f"affected-ab {index}/{len(futures)}", flush=True)
    ab_rows.sort(key=lambda row: str(row["pdf"]))
    report = {
        "baseline_manifest": str(args.baseline_manifest.resolve()),
        "baseline_manifest_sha256": baseline_sha256,
        "dpi": args.dpi,
        "corpus_count": len(pdfs),
        "caption_prepass_errors": sum(row["error"] is not None for row in scan_rows),
        "affected_count": len(affected),
        "affected_ab_errors": sum(row.get("error") is not None for row in ab_rows),
        "source_sha256": source_sha256,
        "scan_rows": scan_rows,
        "affected_ab": ab_rows,
        "human_verified": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(args.out)
    print(_sha256(args.out))


if __name__ == "__main__":
    main()
