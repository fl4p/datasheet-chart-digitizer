#!/usr/bin/env python3
"""Compare packaged chart discovery against the legacy dslib Vpl finder.

This is a migration guard for removing the runtime dependency on
``pwr-mosfet-lib``.  The packaged generic finder does not yet cover all
gate-charge panel styles, so known current misses are explicit and should only
shrink as Vpl-specific discovery moves into this repository.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from datasheet_chart_digitizer.find_charts import process_pdf  # noqa: E402
from datasheet_chart_digitizer.gate_charge_samples import (  # noqa: E402
    DEFAULT_DATASHEET_ROOT,
    SAMPLES,
    _sample_pdf_path,
)

LEGACY_VPL_TEST = DEFAULT_DATASHEET_ROOT / "test/test_viz_vpl.py"

EXPECTED_PACKAGED_FINDER_MISSES: set[str] = set()

EXPECTED_LEGACY_UNAVAILABLE = {
    # These entries are present in the legacy Vpl test list but are not useful
    # for finder parity on the current local corpus because the PDF is absent or
    # dslib.viz itself returns no chart without OCR/extra handling.
    "AON6220",
    "SP30N01AGHNP",
    "GSFP1080",
    "IRF150DM115XTMA1",
    "HY3912W",
}

EXPECTED_TOOL_ERRORS: set[str] = set()


def _rect_tuple(rect: Any) -> tuple[float, float, float, float]:
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def _center_inside(bbox: tuple[float, float, float, float], point: tuple[float, float]) -> bool:
    x0, y0, x1, y1 = bbox
    x, y = point
    return x0 <= x <= x1 and y0 <= y <= y1


def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _overlap_fraction(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter = _overlap_1d(ax0, ax1, bx0, bx1) * _overlap_1d(ay0, ay1, by0, by1)
    if inter <= 0.0:
        return 0.0
    area_a = max(1e-9, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1e-9, (bx1 - bx0) * (by1 - by0))
    return inter / min(area_a, area_b)


def _panel_matches_legacy(
    panel_bbox: tuple[float, float, float, float],
    legacy_bbox: tuple[float, float, float, float],
    legacy_center: tuple[float, float],
) -> bool:
    if _center_inside(panel_bbox, legacy_center):
        return True
    # dslib.viz sometimes returns one broad row bbox spanning two side-by-side
    # plots.  A packaged panel that strongly overlaps that row is still a valid
    # finder match even when the broad legacy center falls between the plots.
    return _overlap_fraction(panel_bbox, legacy_bbox) >= 0.35


def _pick_legacy_hit(hits: list[tuple[Any, Any, str | None]], ref_vpl: float | None) -> tuple[Any, Any, str | None] | None:
    usable = [(chart, hit, source) for chart, hit, source in hits if hit is not None]
    if not usable:
        return None
    if ref_vpl is not None:
        return min(
            usable,
            key=lambda item: (
                abs(float(getattr(item[1], "v_pl", float("inf"))) - ref_vpl),
                -float(getattr(item[1], "score", 0.0)),
            ),
        )
    return max(usable, key=lambda item: float(getattr(item[1], "score", 0.0)))


def _normalize_sample_rel(rel: str) -> str:
    return rel[len("datasheets/") :] if rel.startswith("datasheets/") else rel


def _legacy_test_samples(path: Path) -> list[tuple[str, float | None, str]]:
    if not path.exists():
        return []
    module = ast.parse(path.read_text())
    for node in module.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "SAMPLES":
            return [(_normalize_sample_rel(str(rel)), float(ref), "legacy test_viz_vpl") for rel, ref in ast.literal_eval(node.value)]
    return []


def _dedupe_samples(samples: list[tuple[str, float | None, str]]) -> list[tuple[str, float | None, str]]:
    out: list[tuple[str, float | None, str]] = []
    seen: set[str] = set()
    for rel, ref, comment in samples:
        if rel in seen:
            continue
        seen.add(rel)
        out.append((rel, ref, comment))
    return out


def finder_parity_samples(datasheet_root: Path, source: str = "expanded") -> list[tuple[str, float | None, str]]:
    if source == "builtin":
        return list(SAMPLES)
    if source == "legacy-test":
        return _legacy_test_samples(datasheet_root / "test/test_viz_vpl.py")
    if source == "expanded":
        return _dedupe_samples(_legacy_test_samples(datasheet_root / "test/test_viz_vpl.py") + list(SAMPLES)) or list(SAMPLES)
    raise ValueError(f"unknown sample source: {source}")


def run_parity(
    datasheet_root: Path,
    out_json: Path | None = None,
    strict: bool = False,
    sample_source: str = "expanded",
) -> list[str]:
    sys.path.insert(0, str(datasheet_root))
    try:
        from dslib.viz import find_in_pdf
    except ModuleNotFoundError as exc:
        if exc.name != "dslib":
            raise
        return [f"missing dslib checkout at {datasheet_root}"]

    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="vpl-finder-parity-") as tmp:
        tmpdir = Path(tmp)
        for rel, ref_vpl, comment in finder_parity_samples(datasheet_root, sample_source):
            pdf = _sample_pdf_path(datasheet_root, rel)
            mpn = pdf.stem
            if not pdf.exists():
                status = "missing_pdf"
                if mpn not in EXPECTED_LEGACY_UNAVAILABLE:
                    failures.append(f"{mpn}: missing PDF {pdf}")
                rows.append({"mpn": mpn, "rel": rel, "ref_vpl": ref_vpl, "comment": comment, "status": status})
                continue

            try:
                legacy = _pick_legacy_hit(find_in_pdf(str(pdf), enable_raster=True, enable_ocr=False), ref_vpl)
            except subprocess.CalledProcessError as exc:
                # a crashing extraction tool is an environment failure of THIS
                # sample — record it as such; it is not a match, and it must not
                # take the rest of the corpus down with it
                if mpn not in EXPECTED_TOOL_ERRORS:
                    failures.append(f"{mpn}: extraction tool crashed (legacy): {exc}")
                rows.append({"mpn": mpn, "rel": rel, "ref_vpl": ref_vpl, "comment": comment,
                             "status": "tool_error", "error": str(exc)})
                continue
            if legacy is None:
                status = "legacy_missing"
                if mpn not in EXPECTED_LEGACY_UNAVAILABLE:
                    failures.append(f"{mpn}: legacy dslib.viz finder found no Vpl chart")
                rows.append({"mpn": mpn, "rel": rel, "ref_vpl": ref_vpl, "comment": comment, "status": status})
                continue

            legacy_chart, legacy_hit, legacy_source = legacy
            legacy_bbox = _rect_tuple(legacy_chart.bbox)
            legacy_center = (
                0.5 * (legacy_bbox[0] + legacy_bbox[2]),
                0.5 * (legacy_bbox[1] + legacy_bbox[3]),
            )
            legacy_page = int(legacy_chart.page_num) + 1

            try:
                panels = process_pdf(pdf, tmpdir / mpn, dpi=120)
            except subprocess.CalledProcessError as exc:
                if mpn not in EXPECTED_TOOL_ERRORS:
                    failures.append(f"{mpn}: extraction tool crashed (packaged): {exc}")
                rows.append({"mpn": mpn, "rel": rel, "ref_vpl": ref_vpl, "comment": comment,
                             "status": "tool_error", "error": str(exc)})
                continue
            packaged = [panel for panel in panels if panel.kind == "gate_charge"]
            matching = [
                panel
                for panel in packaged
                if panel.page == legacy_page and _panel_matches_legacy(panel.bbox_pt, legacy_bbox, legacy_center)
            ]
            status = "match" if matching else "missing"
            if strict and not matching:
                failures.append(f"{mpn}: packaged finder missed legacy Vpl chart on page {legacy_page}")
            elif not strict and not matching and mpn not in EXPECTED_PACKAGED_FINDER_MISSES:
                failures.append(f"{mpn}: unexpected packaged finder miss on page {legacy_page}")

            rows.append(
                {
                    "mpn": mpn,
                    "rel": rel,
                    "ref_vpl": ref_vpl,
                    "comment": comment,
                    "status": status,
                    "legacy": {
                        "page": legacy_page,
                        "bbox_pt": [round(v, 3) for v in legacy_bbox],
                        "vpl": getattr(legacy_hit, "v_pl", None),
                        "score": getattr(legacy_hit, "score", None),
                        "source": legacy_source,
                    },
                    "packaged_gate_charge_panels": [
                        {
                            "page": panel.page,
                            "diagram": panel.diagram,
                            "title": panel.title,
                            "bbox_pt": list(panel.bbox_pt),
                            "text_source": panel.text_source,
                        }
                        for panel in packaged
                    ],
                }
            )

    if not strict:
        current_misses = {row["mpn"] for row in rows if row["status"] == "missing"}
        current_legacy_unavailable = {row["mpn"] for row in rows if row["status"] in {"missing_pdf", "legacy_missing"}}
        stale_allowlist = EXPECTED_PACKAGED_FINDER_MISSES - current_misses
        if stale_allowlist:
            print(
                "packaged finder improved; remove from EXPECTED_PACKAGED_FINDER_MISSES: "
                + ", ".join(sorted(stale_allowlist))
            )
        stale_legacy_allowlist = EXPECTED_LEGACY_UNAVAILABLE - current_legacy_unavailable
        if stale_legacy_allowlist:
            print(
                "legacy baseline became available; remove from EXPECTED_LEGACY_UNAVAILABLE: "
                + ", ".join(sorted(stale_legacy_allowlist))
            )
        current_tool_errors = {row["mpn"] for row in rows if row["status"] == "tool_error"}
        stale_tool_errors = EXPECTED_TOOL_ERRORS - current_tool_errors
        if stale_tool_errors:
            print(
                "extraction tool recovered; remove from EXPECTED_TOOL_ERRORS: "
                + ", ".join(sorted(stale_tool_errors))
            )

    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(rows, indent=2) + "\n")

    matched = sum(1 for row in rows if row["status"] == "match")
    unavailable = sum(1 for row in rows if row["status"] in {"missing_pdf", "legacy_missing"})
    tool_errors = sum(1 for row in rows if row["status"] == "tool_error")
    print(
        f"Vpl finder parity: matched={matched} "
        f"missing={len(rows) - matched - unavailable - tool_errors} "
        f"legacy_unavailable={unavailable} tool_errors={tool_errors} "
        f"strict={strict} source={sample_source} samples={len(rows)}"
    )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasheet-root", type=Path, default=DEFAULT_DATASHEET_ROOT)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--strict", action="store_true", help="fail on every packaged-finder miss")
    parser.add_argument(
        "--sample-source",
        choices=("builtin", "legacy-test", "expanded"),
        default="expanded",
        help="Vpl finder sample corpus to compare.",
    )
    args = parser.parse_args()

    failures = run_parity(args.datasheet_root, args.out_json, strict=args.strict, sample_source=args.sample_source)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
