"""Deterministic structural clustering for a local datasheet PDF library.

This module is an offline corpus-indexing tool.  Its output is useful for
representative sampling and regression coverage; it is deliberately not an
input to chart detection or extraction.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import fitz

from .chart_classifier import (
    classify_chart,
    is_marketing_feature_title,
    is_spec_table_header_title,
)


SCHEMA_VERSION = 1
SUPPORTED_CHART_KINDS = {
    "body_diode",
    "breakdown_voltage",
    "capacitances",
    "gate_charge",
    "rds_on",
    "transfer",
}
GENERATED_MARKER_RE = re.compile(r"\.pdf\.", re.IGNORECASE)
NUMERIC_RE = re.compile(r"^[+−-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?(?:[kKmMgGuUnNpP])?$")
FIGURE_RE = re.compile(r"(?i)\b(?:figure|fig\.?|diagram)\s*\d+")
TABLE_MARKERS = {
    "symbol",
    "test condition",
    "test conditions",
    "minimum",
    "maximum",
    "min",
    "typ",
    "max",
    "unit",
    "units",
}


@dataclass(frozen=True)
class PageLayoutSignature:
    id: str
    pdf: str
    vendor: str
    page: int
    page_count: int
    role: str
    text_mode: str
    page_size: str
    width_pt: float
    height_pt: float
    tokens: tuple[str, ...]
    fingerprint: str
    metrics: dict[str, Any]

    @property
    def coarse_key(self) -> tuple[str, str, str]:
        return self.role, self.text_mode, self.page_size


@dataclass(frozen=True)
class DocumentLayoutSignature:
    id: str
    pdf: str
    vendor: str
    page_count: int
    tokens: tuple[str, ...]
    fingerprint: str
    metrics: dict[str, Any]

    @property
    def coarse_key(self) -> tuple[str, str, str]:
        return "document", str(self.metrics["page_count_bin"]), str(self.metrics["dominant_size"])


def is_generated_pdf_copy(path: str | Path) -> bool:
    """Return whether *path* has a derived ``.pdf.<transform>.pdf`` name."""
    name = Path(path).name
    return name.casefold().endswith(".pdf") and GENERATED_MARKER_RE.search(name) is not None


def generated_variant_info(path: Path) -> dict[str, Any]:
    match = GENERATED_MARKER_RE.search(path.name)
    if match is None:
        raise ValueError(f"not a generated PDF copy: {path}")
    canonical_name = path.name[: match.start()] + ".pdf"
    canonical = path.with_name(canonical_name)
    transform_chain = path.name[match.end():-4].split(".pdf.")
    return {
        "path": str(path),
        "transform": transform_chain[-1],
        "transform_chain": transform_chain,
        "canonical_path": str(canonical),
        "canonical_exists": canonical.exists(),
    }


def discover_pdfs(
    root: str | Path,
    vendors: Iterable[str] | None = None,
    excluded_dirs: Iterable[str] = ("_samples",),
) -> tuple[list[Path], list[dict[str, Any]]]:
    """Discover canonical PDFs and separately index derived rendered copies."""
    root = Path(root).resolve()
    allowed = {item.casefold() for item in vendors or ()}
    excluded = set(excluded_dirs)
    canonical: list[Path] = []
    variants: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.pdf")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part in excluded for part in relative.parts[:-1]):
            continue
        vendor = relative.parts[0].casefold() if len(relative.parts) > 1 else "_root"
        if allowed and vendor not in allowed:
            continue
        if is_generated_pdf_copy(path):
            info = generated_variant_info(path)
            info["vendor"] = vendor
            variants.append(info)
        else:
            canonical.append(path)
    return canonical, variants


def _bin(value: float, count: int) -> int:
    return max(0, min(count - 1, int(value * count)))


def _count_bin(value: int) -> str:
    if value == 0:
        return "0"
    if value == 1:
        return "1"
    if value <= 3:
        return "2-3"
    if value <= 7:
        return "4-7"
    if value <= 15:
        return "8-15"
    if value <= 31:
        return "16-31"
    return "32+"


def _page_count_bin(value: int) -> str:
    if value <= 4:
        return "1-4"
    if value <= 8:
        return "5-8"
    if value <= 12:
        return "9-12"
    if value <= 16:
        return "13-16"
    if value <= 24:
        return "17-24"
    return "25+"


def _page_size(width: float, height: float) -> str:
    portrait = height >= width
    ratio = max(width, height) / max(1.0, min(width, height))
    if abs(width - 595) < 25 and abs(height - 842) < 35:
        base = "a4"
    elif abs(width - 612) < 25 and abs(height - 792) < 35:
        base = "letter"
    else:
        base = f"r{round(ratio, 1):.1f}"
    return f"{base}-{'p' if portrait else 'l'}"


def _line_groups(words: Sequence[tuple[Any, ...]]) -> list[tuple[str, fitz.Rect]]:
    grouped: dict[tuple[int, int], list[tuple[Any, ...]]] = defaultdict(list)
    for word in words:
        if len(word) >= 8:
            grouped[(int(word[5]), int(word[6]))].append(word)
    lines: list[tuple[str, fitz.Rect]] = []
    for group in grouped.values():
        ordered = sorted(group, key=lambda item: (float(item[0]), int(item[7])))
        text = " ".join(str(item[4]) for item in ordered).strip()
        if text:
            rect = fitz.Rect(
                min(float(item[0]) for item in ordered),
                min(float(item[1]) for item in ordered),
                max(float(item[2]) for item in ordered),
                max(float(item[3]) for item in ordered),
            )
            lines.append((text, rect))
    return sorted(lines, key=lambda item: (item[1].y0, item[1].x0))


def _caption_features(lines: Sequence[tuple[str, fitz.Rect]], width: float, height: float) -> tuple[list[str], int]:
    tokens: list[str] = []
    count = 0
    for text, rect in lines:
        if len(text) > 150 or is_spec_table_header_title(text) or is_marketing_feature_title(text):
            continue
        kind = classify_chart(text, "")
        explicit = FIGURE_RE.search(text) is not None
        if kind not in SUPPORTED_CHART_KINDS and not explicit:
            continue
        if kind == "chart" and not explicit:
            continue
        count += 1
        row = _bin(rect.y0 / height, 6)
        col = _bin(rect.x0 / width, 4)
        tokens.append(f"caption:{kind}:{row}:{col}")
    return tokens, count


def _drawing_features(page: fitz.Page, width: float, height: float) -> tuple[list[str], dict[str, int]]:
    tokens: list[str] = []
    metrics = {"drawings": 0, "closed_frames": 0, "long_h": 0, "long_v": 0}
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    metrics["drawings"] = len(drawings)
    frame_cells: Counter[tuple[int, int]] = Counter()
    line_cells: Counter[tuple[str, int, int]] = Counter()
    for drawing in drawings:
        rect = fitz.Rect(drawing.get("rect", (0, 0, 0, 0)))
        if rect.is_empty:
            continue
        rw, rh = rect.width / width, rect.height / height
        has_rect_item = any(item and item[0] in {"re", "qu"} for item in drawing.get("items", ()))
        if (drawing.get("closePath") or has_rect_item) and 0.12 <= rw <= 0.75 and 0.08 <= rh <= 0.55:
            metrics["closed_frames"] += 1
            frame_cells[(_bin(rect.x0 / width, 4), _bin(rect.y0 / height, 6))] += 1
        for item in drawing.get("items", ()):
            if not item or item[0] != "l":
                continue
            p0, p1 = item[1], item[2]
            dx, dy = abs(float(p1.x - p0.x)), abs(float(p1.y - p0.y))
            if dx >= 0.18 * width and dy <= 1.5:
                metrics["long_h"] += 1
                line_cells[("h", _bin(min(p0.x, p1.x) / width, 4), _bin(p0.y / height, 8))] += 1
            elif dy >= 0.12 * height and dx <= 1.5:
                metrics["long_v"] += 1
                line_cells[("v", _bin(p0.x / width, 6), _bin(min(p0.y, p1.y) / height, 6))] += 1
    for (col, row), value in sorted(frame_cells.items()):
        tokens.append(f"frame:{col}:{row}:{_count_bin(value)}")
    for (direction, col, row), value in sorted(line_cells.items()):
        tokens.append(f"line:{direction}:{col}:{row}:{_count_bin(value)}")
    tokens.append(f"drawing-count:{_count_bin(metrics['drawings'])}")
    tokens.append(f"frame-count:{_count_bin(metrics['closed_frames'])}")
    return tokens, metrics


def _text_features(
    words: Sequence[tuple[Any, ...]], lines: Sequence[tuple[str, fitz.Rect]], width: float, height: float
) -> tuple[list[str], dict[str, int]]:
    tokens: list[str] = []
    occupancy: Counter[tuple[int, int]] = Counter()
    numeric: Counter[tuple[int, int]] = Counter()
    for word in words:
        x0, y0, x1, y1, text = float(word[0]), float(word[1]), float(word[2]), float(word[3]), str(word[4])
        col, row = _bin(((x0 + x1) / 2) / width, 6), _bin(((y0 + y1) / 2) / height, 8)
        occupancy[(col, row)] += 1
        if NUMERIC_RE.match(text.replace("°", "")):
            numeric[(col, row)] += 1
    for (col, row), value in sorted(occupancy.items()):
        if value >= 2:
            tokens.append(f"text:{col}:{row}:{_count_bin(value)}")
    for (col, row), value in sorted(numeric.items()):
        if value >= 2:
            tokens.append(f"numeric:{col}:{row}:{_count_bin(value)}")
    marker_count = 0
    for text, _ in lines:
        normalized = re.sub(r"\s+", " ", text.casefold())
        marker_count += sum(marker in normalized for marker in TABLE_MARKERS)
    metrics = {
        "words": len(words),
        "numeric_words": sum(numeric.values()),
        "table_markers": marker_count,
        "occupied_cells": len(occupancy),
    }
    tokens.extend(
        (
            f"word-count:{_count_bin(len(words))}",
            f"numeric-count:{_count_bin(metrics['numeric_words'])}",
            f"table-markers:{_count_bin(marker_count)}",
        )
    )
    return tokens, metrics


def _image_features(page: fitz.Page, width: float, height: float) -> tuple[list[str], dict[str, int]]:
    try:
        images = page.get_image_info()
    except Exception:
        images = []
    tokens: list[str] = []
    useful = 0
    cells: Counter[tuple[int, int, str]] = Counter()
    for image in images:
        rect = fitz.Rect(image.get("bbox", (0, 0, 0, 0)))
        if rect.is_empty:
            continue
        area = rect.width * rect.height / max(1.0, width * height)
        if area < 0.004:
            continue
        useful += 1
        size = "large" if area >= 0.2 else "medium" if area >= 0.04 else "small"
        cells[(_bin(rect.x0 / width, 4), _bin(rect.y0 / height, 6), size)] += 1
    for (col, row, size), value in sorted(cells.items()):
        tokens.append(f"image:{col}:{row}:{size}:{_count_bin(value)}")
    tokens.append(f"image-count:{_count_bin(useful)}")
    return tokens, {"images": len(images), "useful_images": useful}


def _role_and_mode(metrics: dict[str, int], caption_count: int) -> tuple[str, str]:
    chart_evidence = caption_count > 0 or metrics["closed_frames"] > 0 or (
        metrics["long_h"] >= 5 and metrics["long_v"] >= 5 and metrics["numeric_words"] >= 4
    )
    table_evidence = metrics["table_markers"] >= 3 or (
        metrics["long_h"] >= 6 and metrics["words"] >= 35 and metrics["occupied_cells"] >= 10
    )
    if chart_evidence and table_evidence:
        role = "mixed"
    elif chart_evidence:
        role = "chart"
    elif table_evidence:
        role = "table"
    else:
        role = "other"
    if metrics["words"] == 0 and metrics["useful_images"] > 0:
        mode = "raster"
    elif metrics["words"] < 8 and metrics["drawings"] >= 100:
        mode = "vector_outline"
    elif metrics["useful_images"] > 0 and metrics["words"] >= 8:
        mode = "hybrid"
    elif metrics["words"] >= 8:
        mode = "native"
    else:
        mode = "sparse"
    return role, mode


def _fingerprint(tokens: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(set(tokens))).encode()).hexdigest()[:20]


def _page_signature(page: fitz.Page, pdf: Path, root: Path, page_count: int) -> PageLayoutSignature:
    width, height = float(page.rect.width), float(page.rect.height)
    try:
        words = page.get_text("words", sort=True)
    except Exception:
        words = []
    lines = _line_groups(words)
    caption_tokens, caption_count = _caption_features(lines, width, height)
    drawing_tokens, drawing_metrics = _drawing_features(page, width, height)
    text_tokens, text_metrics = _text_features(words, lines, width, height)
    image_tokens, image_metrics = _image_features(page, width, height)
    metrics = {**drawing_metrics, **text_metrics, **image_metrics, "captions": caption_count}
    role, text_mode = _role_and_mode(metrics, caption_count)
    page_size = _page_size(width, height)
    tokens = tuple(sorted(set(caption_tokens + drawing_tokens + text_tokens + image_tokens + [
        f"role:{role}", f"mode:{text_mode}", f"size:{page_size}"
    ])))
    relative = pdf.relative_to(root)
    vendor = relative.parts[0] if len(relative.parts) > 1 else "_root"
    identity = f"{relative.as_posix()}#p{page.number + 1}"
    return PageLayoutSignature(
        id=identity,
        pdf=relative.as_posix(),
        vendor=vendor,
        page=page.number + 1,
        page_count=page_count,
        role=role,
        text_mode=text_mode,
        page_size=page_size,
        width_pt=round(width, 2),
        height_pt=round(height, 2),
        tokens=tokens,
        fingerprint=_fingerprint(tokens),
        metrics=metrics,
    )


def scan_pdf(path: str | Path, root: str | Path) -> list[PageLayoutSignature]:
    path, root = Path(path).resolve(), Path(root).resolve()
    with fitz.open(path) as document:
        return [_page_signature(page, path, root, document.page_count) for page in document]


def _scan_worker(path: str, root: str) -> tuple[str, list[PageLayoutSignature], str | None]:
    try:
        return path, scan_pdf(path, root), None
    except Exception as exc:
        return path, [], f"{type(exc).__name__}: {exc}"


def scan_corpus(paths: Sequence[Path], root: Path, workers: int) -> tuple[list[PageLayoutSignature], list[dict[str, str]]]:
    signatures: list[PageLayoutSignature] = []
    errors: list[dict[str, str]] = []
    if workers <= 1:
        results = (_scan_worker(str(path), str(root)) for path in paths)
        for path, pages, error in results:
            signatures.extend(pages)
            if error:
                errors.append({"pdf": str(Path(path).relative_to(root)), "error": error})
        return signatures, errors
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(_scan_worker, str(path), str(root)): path for path in paths}
        completed = 0
        for future in as_completed(pending):
            path, pages, error = future.result()
            signatures.extend(pages)
            if error:
                errors.append({"pdf": str(Path(path).relative_to(root)), "error": error})
            completed += 1
            if completed % 100 == 0 or completed == len(paths):
                print(f"layout-scan {completed}/{len(paths)}", flush=True)
    return sorted(signatures, key=lambda item: (item.pdf, item.page)), sorted(errors, key=lambda item: item["pdf"])


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    return len(a & b) / max(1, len(a | b))


def _medoid(members: Sequence[Any]) -> Any:
    if len(members) <= 2:
        return sorted(members, key=lambda item: item.id)[0]
    return max(
        members,
        key=lambda candidate: (
            sum(_jaccard(candidate.tokens, other.tokens) for other in members),
            -len(candidate.tokens),
            candidate.id,
        ),
    )


def cluster_signatures(signatures: Sequence[Any], threshold: float = 0.72) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Greedily cluster signatures behind hard role/mode/page-size boundaries."""
    exact: dict[tuple[tuple[str, str, str], str], list[Any]] = defaultdict(list)
    for signature in signatures:
        exact[(signature.coarse_key, signature.fingerprint)].append(signature)
    groups = sorted(exact.values(), key=lambda items: (-len(items), items[0].id))
    clusters: list[list[Any]] = []
    representatives: list[Any] = []
    by_key: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for group in groups:
        representative = _medoid(group)
        best_index, best_score = None, threshold
        for index in by_key[representative.coarse_key]:
            score = _jaccard(representative.tokens, representatives[index].tokens)
            if score >= best_score:
                best_index, best_score = index, score
        if best_index is None:
            best_index = len(clusters)
            clusters.append([])
            representatives.append(representative)
            by_key[representative.coarse_key].append(best_index)
        clusters[best_index].extend(group)
        representatives[best_index] = _medoid(clusters[best_index])
    ordered = sorted(
        zip(clusters, representatives),
        key=lambda item: (item[1].coarse_key, -len(item[0]), item[1].id),
    )
    records: list[dict[str, Any]] = []
    assignments: dict[str, str] = {}
    counters: Counter[str] = Counter()
    for members, representative in ordered:
        prefix = representative.coarse_key[0].replace("document", "doc")
        counters[prefix] += 1
        cluster_id = f"{prefix}-{counters[prefix]:05d}"
        member_ids = sorted(item.id for item in members)
        for item_id in member_ids:
            assignments[item_id] = cluster_id
        vendors = Counter(item.vendor for item in members)
        records.append({
            "cluster_id": cluster_id,
            "coarse_key": list(representative.coarse_key),
            "member_count": len(members),
            "pdf_count": len({item.pdf for item in members}),
            "vendor_count": len(vendors),
            "vendors": dict(sorted(vendors.items())),
            "medoid": representative.id,
            "medoid_fingerprint": representative.fingerprint,
            "mean_similarity_to_medoid": round(sum(_jaccard(item.tokens, representative.tokens) for item in members) / len(members), 4),
            "members": member_ids,
        })
    return records, assignments


def build_document_signatures(
    pages: Sequence[PageLayoutSignature], page_assignments: dict[str, str]
) -> list[DocumentLayoutSignature]:
    grouped: dict[str, list[PageLayoutSignature]] = defaultdict(list)
    for page in pages:
        grouped[page.pdf].append(page)
    documents: list[DocumentLayoutSignature] = []
    for pdf, pdf_pages in sorted(grouped.items()):
        ordered = sorted(pdf_pages, key=lambda item: item.page)
        count = len(ordered)
        tokens: list[str] = [f"pages:{_page_count_bin(count)}"]
        role_counts = Counter(item.role for item in ordered)
        mode_counts = Counter(item.text_mode for item in ordered)
        size_counts = Counter(item.page_size for item in ordered)
        for role, value in sorted(role_counts.items()):
            tokens.append(f"role-count:{role}:{_count_bin(value)}")
        for mode, value in sorted(mode_counts.items()):
            tokens.append(f"mode-count:{mode}:{_count_bin(value)}")
        for index, page in enumerate(ordered):
            position = min(4, int(index * 5 / max(1, count)))
            tokens.append(f"page-role:{position}:{page.role}:{page.text_mode}")
            if page.role in {"chart", "table", "mixed"}:
                tokens.append(f"layout:{page_assignments[page.id]}")
        unique_tokens = tuple(sorted(set(tokens)))
        documents.append(DocumentLayoutSignature(
            id=pdf,
            pdf=pdf,
            vendor=ordered[0].vendor,
            page_count=count,
            tokens=unique_tokens,
            fingerprint=_fingerprint(unique_tokens),
            metrics={
                "page_count_bin": _page_count_bin(count),
                "dominant_size": size_counts.most_common(1)[0][0],
                "roles": dict(sorted(role_counts.items())),
                "text_modes": dict(sorted(mode_counts.items())),
            },
        ))
    return documents


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def write_outputs(
    out: Path,
    root: Path,
    pages: Sequence[PageLayoutSignature],
    page_clusters: Sequence[dict[str, Any]],
    page_assignments: dict[str, str],
    documents: Sequence[DocumentLayoutSignature],
    document_clusters: Sequence[dict[str, Any]],
    document_assignments: dict[str, str],
    variants: Sequence[dict[str, Any]],
    errors: Sequence[dict[str, str]],
    threshold: float,
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    page_rows = []
    with (out / "layout-pages.jsonl").open("w", encoding="utf-8") as handle:
        for page in pages:
            row = {**asdict(page), "cluster_id": page_assignments[page.id]}
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            page_rows.append(row)
    document_rows = []
    with (out / "layout-documents.jsonl").open("w", encoding="utf-8") as handle:
        for document in documents:
            row = {**asdict(document), "cluster_id": document_assignments[document.id]}
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            document_rows.append(row)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "algorithm": "structural-token-jaccard-medoid",
        "threshold": threshold,
        "runtime_detector_authority": False,
        "generated_pdf_copies_excluded": True,
        "summary": {
            "canonical_pdfs": len(documents),
            "pages": len(pages),
            "page_clusters": len(page_clusters),
            "document_clusters": len(document_clusters),
            "generated_variants": len(variants),
            "scan_errors": len(errors),
        },
        "page_clusters": list(page_clusters),
        "document_clusters": list(document_clusters),
    }
    (out / "layout-clusters.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (out / "generated-pdf-variants.json").write_text(json.dumps(list(variants), indent=2, sort_keys=True) + "\n")
    (out / "scan-errors.json").write_text(json.dumps(list(errors), indent=2, sort_keys=True) + "\n")
    _write_csv(out / "layout-pages.csv", page_rows, ("cluster_id", "vendor", "pdf", "page", "role", "text_mode", "page_size", "fingerprint"))
    _write_csv(out / "layout-documents.csv", document_rows, ("cluster_id", "vendor", "pdf", "page_count", "fingerprint"))
    _write_csv(out / "layout-page-clusters.csv", page_clusters, ("cluster_id", "coarse_key", "member_count", "pdf_count", "vendor_count", "medoid", "mean_similarity_to_medoid"))
    _write_csv(out / "layout-document-clusters.csv", document_clusters, ("cluster_id", "coarse_key", "member_count", "pdf_count", "vendor_count", "medoid", "mean_similarity_to_medoid"))
    return manifest


def _parse_vendors(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="datasheet corpus root (vendor directories below it)")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--vendors", help="comma-separated top-level vendor directories")
    parser.add_argument("--workers", type=int, default=max(1, min(8, os.cpu_count() or 1)))
    parser.add_argument("--threshold", type=float, default=0.72, help="Jaccard similarity threshold")
    parser.add_argument("--include-other", action="store_true", help="include non-chart/table pages in page clustering")
    parser.add_argument("--max-pdfs", type=int, help="deterministic bounded debug run")
    parser.add_argument("--fail-on-errors", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1")
    root = args.root.resolve()
    paths, variants = discover_pdfs(root, _parse_vendors(args.vendors))
    if args.max_pdfs is not None:
        paths = paths[: max(0, args.max_pdfs)]
    print(f"canonical-pdfs {len(paths)} generated-variants-excluded {len(variants)}", flush=True)
    pages, errors = scan_corpus(paths, root, max(1, args.workers))
    cluster_pages = pages if args.include_other else [page for page in pages if page.role != "other"]
    page_clusters, page_assignments = cluster_signatures(cluster_pages, args.threshold)
    documents = build_document_signatures(pages, page_assignments)
    document_clusters, document_assignments = cluster_signatures(documents, args.threshold)
    manifest = write_outputs(
        args.out, root, cluster_pages, page_clusters, page_assignments,
        documents, document_clusters, document_assignments, variants, errors, args.threshold,
    )
    print(json.dumps(manifest["summary"], sort_keys=True), flush=True)
    return 1 if args.fail_on_errors and errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
