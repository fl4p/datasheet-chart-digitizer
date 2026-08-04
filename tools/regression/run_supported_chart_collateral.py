#!/usr/bin/env python3
"""Run and compare full-corpus A/B for the supported chart digitizers.

The runner deliberately mirrors the production annotation contract without
building the final annotated PDF: one shared finder pass feeds capacitance,
transfer, breakdown-voltage, and body-diode extraction; the two RDS plugins use
their production fail-closed entry points.  Every source PDF, finder crop,
result, and generated artifact is hash-locked for a same-host sequential A/B.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


FAMILIES = (
    "capacitances",
    "transfer",
    "breakdown_voltage",
    "body_diode",
    "rds_on_current",
    "rds_on_temperature",
)
INDEXED_FAMILIES = frozenset(FAMILIES[:3])
DEPENDENCIES = ("numpy", "opencv-python", "pillow", "pymupdf", "scipy")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def _pdfs_from_manifest(path: Path) -> list[Path]:
    payload = json.loads(path.read_text())
    rows = payload.get("rows", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("corpus manifest must be a list or contain a rows list")
    pdfs: list[Path] = []
    for row in rows:
        raw = row.get("pdf") if isinstance(row, dict) else row
        if raw is None:
            raise ValueError("corpus row has no pdf")
        pdfs.append(Path(str(raw)).expanduser().resolve())
    if len(set(pdfs)) != len(pdfs):
        raise ValueError("corpus contains duplicate exact PDF paths")
    missing = [str(pdf) for pdf in pdfs if not pdf.is_file()]
    if missing:
        raise FileNotFoundError(f"missing corpus PDFs: {missing[:5]}")
    return pdfs


def _source_manifest(source_root: Path) -> list[dict[str, object]]:
    package = source_root / "src" / "datasheet_chart_digitizer"
    return [
        {"path": path.relative_to(package).as_posix(), "sha256": _sha256(path)}
        for path in sorted(package.rglob("*.py"))
    ]


def _git_revision(source_root: Path) -> dict[str, object]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--", "src"],
                cwd=source_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def _environment() -> dict[str, object]:
    versions = {}
    for dependency in DEPENDENCIES:
        try:
            versions[dependency] = importlib.metadata.version(dependency)
        except importlib.metadata.PackageNotFoundError:
            versions[dependency] = None
    return {
        "python": sys.version,
        "executable": str(Path(sys.executable).resolve()),
        "platform": platform.platform(),
        "dependencies": versions,
        "omp_thread_limit": os.environ.get("OMP_THREAD_LIMIT"),
    }


def _normalize(value: Any, output_root: Path) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize(item, output_root)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize(item, output_root) for item in value]
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        try:
            path = Path(value)
            if path.is_absolute() and path.is_relative_to(output_root):
                return f"<OUT>/{path.relative_to(output_root).as_posix()}"
        except (OSError, ValueError):
            pass
    return value


def _artifact_manifest(root: Path) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        row: dict[str, object] = {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        if path.suffix.lower() == ".json":
            try:
                normalized = _normalize(json.loads(path.read_text()), root)
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            else:
                row["normalized_sha256"] = _canonical_sha256(normalized)
        artifacts.append(row)
    return artifacts


def _indexed_result(panel, root: Path, pdf: Path):
    from datasheet_chart_digitizer import (
        breakdown_voltage,
        mosfet_capacitance,
        transfer_characteristics,
    )

    chart = asdict(panel)
    crop_rel = Path(panel.crop_png)
    crop_path = root / crop_rel
    rel_stem = crop_rel.with_suffix("")
    if panel.kind == "capacitances":
        return mosfet_capacitance.process_chart(
            chart, crop_path, root, rel_stem, pdf.parent
        )
    if panel.kind == "transfer":
        return transfer_characteristics.process_chart(
            chart, crop_path, root, rel_stem, None
        )
    if panel.kind == "breakdown_voltage":
        return breakdown_voltage.process_chart(chart, crop_path, root, rel_stem)
    raise ValueError(panel.kind)


def _run_pdf(pdf: Path, root: Path, dpi: int) -> dict[str, object]:
    from datasheet_chart_digitizer import (
        diode_forward_voltage,
        rdson_current,
        rdson_temperature,
    )
    from datasheet_chart_digitizer.find_charts import process_pdf, write_outputs

    root.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, dict[str, object]] = {
        family: {"results": [], "errors": []} for family in FAMILIES
    }
    try:
        panels = process_pdf(pdf, root, dpi)
    except Exception as error:
        return {
            "pdf": str(pdf),
            "source_pdf_sha256": _sha256(pdf),
            "terminal_error": f"{type(error).__name__}: {error}",
            "input_panels": [],
            "outputs": outputs,
            "artifacts": _artifact_manifest(root),
        }
    write_outputs(root, panels)
    input_panels = [
        {
            "panel": asdict(panel),
            "crop_sha256": _sha256(root / panel.crop_png),
        }
        for panel in panels
        if panel.kind in INDEXED_FAMILIES or panel.kind == "body_diode"
    ]
    for panel in panels:
        if panel.kind not in INDEXED_FAMILIES:
            continue
        try:
            outputs[panel.kind]["results"].append(
                _normalize(_indexed_result(panel, root, pdf), root)
            )
        except Exception as error:  # full fail-closed corpus accounting
            outputs[panel.kind]["errors"].append(
                {
                    "page": panel.page,
                    "diagram": panel.diagram,
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    diode_results, diode_errors = diode_forward_voltage.digitize_panels_fail_closed(
        panels, root
    )
    outputs["body_diode"] = {
        "results": _normalize(diode_results, root),
        "errors": _normalize(diode_errors, root),
    }
    rds_runners = [
        ("rds_on_temperature", rdson_temperature.digitize_pdf_fail_closed),
    ]
    # The production current runner performs the same global finder pass and
    # applies this exact selector.  If the already-frozen pass has no owned
    # current panel, calling it again can only repeat expensive rendering and
    # return an empty result.
    if any(rdson_current._is_rdson_current_panel(panel) for panel in panels):
        rds_runners.insert(
            0, ("rds_on_current", rdson_current.digitize_pdf_fail_closed)
        )
    for family, runner in rds_runners:
        try:
            results, errors = runner(pdf, root, dpi)
        except Exception as error:  # terminal runner failure stays visible
            results = []
            errors = [
                {
                    "kind": family,
                    "page": None,
                    "diagram": None,
                    "error": f"{type(error).__name__}: {error}",
                }
            ]
        outputs[family] = {
            "results": _normalize(results, root),
            "errors": _normalize(errors, root),
        }

    return {
        "pdf": str(pdf),
        "source_pdf_sha256": _sha256(pdf),
        "terminal_error": None,
        "input_panels": input_panels,
        "outputs": outputs,
        "artifacts": _artifact_manifest(root),
    }


def _corpus_identity(pdfs: list[Path]) -> tuple[list[dict[str, str]], str]:
    rows = [{"pdf": str(pdf), "sha256": _sha256(pdf)} for pdf in pdfs]
    return rows, _canonical_sha256(rows)


def _run(args: argparse.Namespace) -> None:
    source_root = args.source_root.expanduser().resolve()
    sys.path.insert(0, str(source_root / "src"))
    pdfs = _pdfs_from_manifest(args.corpus_manifest)
    corpus_rows, corpus_sha256 = _corpus_identity(pdfs)
    source_files = _source_manifest(source_root)
    args.out.mkdir(parents=True, exist_ok=True)
    row_dir = args.out / "rows"
    rows: list[dict[str, object]] = []
    for index, pdf in enumerate(pdfs, 1):
        row_path = row_dir / f"{index:04d}.json"
        if args.resume and row_path.is_file():
            row = json.loads(row_path.read_text())
            expected = corpus_rows[index - 1]
            if (
                row.get("pdf") != expected["pdf"]
                or row.get("source_pdf_sha256") != expected["sha256"]
            ):
                raise RuntimeError(f"resume row identity mismatch: {row_path}")
        else:
            row = _run_pdf(pdf, args.out / "artifacts" / f"{index:04d}", args.dpi)
            _write_json(row_path, row)
        rows.append(row)
        if index % 10 == 0 or index == len(pdfs):
            print(f"supported-collateral {index}/{len(pdfs)}", flush=True)

    family_counts = {
        family: {
            "results": sum(
                len(row["outputs"][family]["results"]) for row in rows
            ),
            "errors": sum(len(row["outputs"][family]["errors"]) for row in rows),
            "no_result": sum(
                row["terminal_error"] is None
                and not row["outputs"][family]["results"]
                and not row["outputs"][family]["errors"]
                for row in rows
            ),
        }
        for family in FAMILIES
    }
    machine = {
        "schema": "supported-chart-collateral-v1",
        "command": " ".join(sys.argv),
        "dpi": args.dpi,
        "corpus_manifest": str(args.corpus_manifest.resolve()),
        "corpus_manifest_sha256": _sha256(args.corpus_manifest),
        "corpus_sha256": corpus_sha256,
        "corpus": corpus_rows,
        "source_root": str(source_root),
        "source_files": source_files,
        "source_sha256": _canonical_sha256(source_files),
        "git": _git_revision(source_root),
        "environment": _environment(),
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "row_count": len(rows),
        "terminal_exception_count": sum(
            row["terminal_error"] is not None for row in rows
        ),
        "family_counts": family_counts,
        "rows": rows,
        "human_verified": False,
    }
    machine_path = args.out / "machine.json"
    _write_json(machine_path, machine)
    print(machine_path)
    print(_sha256(machine_path))


def _diff_values(left: Any, right: Any, path: str = "") -> list[dict[str, object]]:
    if type(left) is not type(right):
        return [{"path": path, "baseline": left, "candidate": right}]
    if isinstance(left, dict):
        diffs: list[dict[str, object]] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}/{key}"
            if key not in left:
                diffs.append({"path": child, "baseline": None, "candidate": right[key]})
            elif key not in right:
                diffs.append({"path": child, "baseline": left[key], "candidate": None})
            else:
                diffs.extend(_diff_values(left[key], right[key], child))
        return diffs
    if isinstance(left, list):
        if len(left) != len(right):
            return [{"path": path, "baseline": left, "candidate": right}]
        diffs = []
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            diffs.extend(_diff_values(left_item, right_item, f"{path}/{index}"))
        return diffs
    return [] if left == right else [{"path": path, "baseline": left, "candidate": right}]


def _comparable_artifacts(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    comparable = []
    for row in rows:
        item = dict(row)
        if "normalized_sha256" in item:
            item["sha256"] = item["normalized_sha256"]
        item.pop("normalized_sha256", None)
        comparable.append(item)
    return comparable


def _compare(args: argparse.Namespace) -> None:
    baseline = json.loads(args.baseline.read_text())
    candidate = json.loads(args.candidate.read_text())
    required_equal = (
        "schema",
        "dpi",
        "corpus_manifest_sha256",
        "corpus_sha256",
        "corpus",
        "environment",
        "runner_sha256",
        "row_count",
    )
    mismatches = {
        key: {"baseline": baseline.get(key), "candidate": candidate.get(key)}
        for key in required_equal
        if baseline.get(key) != candidate.get(key)
    }
    baseline_rows = {row["pdf"]: row for row in baseline["rows"]}
    candidate_rows = {row["pdf"]: row for row in candidate["rows"]}
    if set(baseline_rows) != set(candidate_rows):
        mismatches["row_pdf_keys"] = {
            "baseline": sorted(baseline_rows),
            "candidate": sorted(candidate_rows),
        }
    deltas = []
    input_mismatches = []
    for pdf in sorted(set(baseline_rows) & set(candidate_rows)):
        left = baseline_rows[pdf]
        right = candidate_rows[pdf]
        input_diff = _diff_values(
            {
                "source_pdf_sha256": left["source_pdf_sha256"],
                "input_panels": left["input_panels"],
            },
            {
                "source_pdf_sha256": right["source_pdf_sha256"],
                "input_panels": right["input_panels"],
            },
        )
        if input_diff:
            input_mismatches.append({"pdf": pdf, "differences": input_diff})
        output_diff = _diff_values(
            {
                "terminal_error": left["terminal_error"],
                "outputs": left["outputs"],
            },
            {
                "terminal_error": right["terminal_error"],
                "outputs": right["outputs"],
            },
        )
        artifact_diff = _diff_values(
            _comparable_artifacts(left["artifacts"]),
            _comparable_artifacts(right["artifacts"]),
        )
        if output_diff or artifact_diff:
            deltas.append(
                {
                    "pdf": pdf,
                    "output_differences": output_diff,
                    "artifact_differences": artifact_diff,
                }
            )
    comparison = {
        "schema": "supported-chart-collateral-comparison-v1",
        "baseline": str(args.baseline.resolve()),
        "baseline_sha256": _sha256(args.baseline),
        "candidate": str(args.candidate.resolve()),
        "candidate_sha256": _sha256(args.candidate),
        "required_identity_mismatches": mismatches,
        "input_mismatches": input_mismatches,
        "delta_count": len(deltas),
        "deltas": deltas,
        "human_verified": False,
    }
    _write_json(args.out, comparison)
    print(
        json.dumps(
            {
                "identity_mismatches": len(mismatches),
                "input_mismatches": len(input_mismatches),
                "delta_count": len(deltas),
                "comparison": str(args.out),
                "sha256": _sha256(args.out),
            }
        )
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--corpus-manifest", type=Path, required=True)
    run.add_argument("--source-root", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--dpi", type=int, default=220)
    run.add_argument("--resume", action="store_true")
    compare = subparsers.add_parser("compare")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    if args.action == "run":
        _run(args)
    else:
        _compare(args)


if __name__ == "__main__":
    main()
