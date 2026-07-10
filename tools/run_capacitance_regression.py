#!/usr/bin/env python3
"""Run the local MOSFET capacitance digitizer regression corpus.

The corpus intentionally points at Fab's local generated chart indexes under
``/Users/fab/dev/pv/ee/out``. It is a regression guard for this workstation,
not a portable package test.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EE_ROOT = REPO_ROOT.parent
CHART_ROOT = EE_ROOT / "out" / "datasheet_charts"


@dataclass(frozen=True)
class RegressionCase:
    name: str
    chart_index: Path
    expected_charts: int
    max_untrusted_axes: int = 0
    expected_untrusted_parts: frozenset[str] = frozenset()
    allowed_qoss_statuses: frozenset[str | None] = frozenset({"pass", None})


CASES = (
    RegressionCase(
        name="focused_label_overlap_and_left_knee",
        chart_index=CHART_ROOT / "debug_raster_coss_left_repair" / "charts.json",
        expected_charts=3,
        allowed_qoss_statuses=frozenset({"pass"}),
    ),
    RegressionCase(
        name="dashed_vector_trace",
        chart_index=CHART_ROOT / "debug_iaucn_dashed" / "charts.json",
        expected_charts=1,
        allowed_qoss_statuses=frozenset({"pass", None}),
    ),
    RegressionCase(
        name="large35_random_manufacturer_cv",
        chart_index=CHART_ROOT / "random_mfr_cv_20260708_large35" / "charts.json",
        expected_charts=35,
        max_untrusted_axes=1,
        expected_untrusted_parts=frozenset({"IMBG75R007M2H"}),
        allowed_qoss_statuses=frozenset({"pass", "graph_table_inconsistent", None}),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        help="Directory for regenerated outputs. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the temporary output directory and print its path.",
    )
    parser.add_argument(
        "--case",
        choices=[case.name for case in CASES],
        action="append",
        help="Run only the named case. May be passed more than once.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    selected = [case for case in CASES if args.case is None or case.name in set(args.case)]
    if not selected:
        raise SystemExit("no regression cases selected")

    if args.out is not None:
        out_root = args.out
        out_root.mkdir(parents=True, exist_ok=True)
        _run_cases(selected, out_root)
        return

    with tempfile.TemporaryDirectory(prefix="dsdig-cap-regression-") as tmp:
        out_root = Path(tmp)
        _run_cases(selected, out_root)
        if args.keep:
            keep_path = Path(tempfile.mkdtemp(prefix="dsdig-cap-regression-keep-"))
            subprocess.run(["cp", "-R", f"{out_root}/.", str(keep_path)], check=True)
            print(f"kept outputs: {keep_path}")


def _run_cases(cases: list[RegressionCase], out_root: Path) -> None:
    failures: list[str] = []
    for case in cases:
        print(f"== {case.name}")
        case_failures = _run_case(case, out_root / case.name)
        if case_failures:
            failures.extend(f"{case.name}: {failure}" for failure in case_failures)
        else:
            print("  PASS")

    if failures:
        print()
        print("Regression failures:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)


def _run_case(case: RegressionCase, out_dir: Path) -> list[str]:
    failures: list[str] = []
    if not case.chart_index.exists():
        return [f"missing chart index {case.chart_index}"]
    out_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    src_path = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = src_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cmd = [
        sys.executable,
        "-m",
        "datasheet_chart_digitizer.mosfet_capacitance",
        str(case.chart_index),
        "--out",
        str(out_dir),
        "--debug-axis-overlays",
    ]
    completed = subprocess.run(cmd, cwd=REPO_ROOT, env=env, text=True, capture_output=True)
    if completed.returncode != 0:
        failures.append(f"digitizer exited {completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}")
        return failures

    manifest_path = out_dir / "capacitance_digitization.json"
    errors_path = out_dir / "capacitance_digitization_errors.json"
    if not manifest_path.exists():
        return [f"missing manifest {manifest_path}"]
    results = json.loads(manifest_path.read_text())
    errors = json.loads(errors_path.read_text()) if errors_path.exists() else []
    if errors:
        failures.append(f"{len(errors)} digitization errors")
    if len(results) != case.expected_charts:
        failures.append(f"expected {case.expected_charts} charts, got {len(results)}")

    trace_statuses = Counter(result.get("trace_validation_status") for result in results)
    qoss_statuses = Counter(result.get("qoss_validation_status") for result in results)
    axis_sources = Counter((result.get("axis_calibration") or {}).get("source") for result in results)
    missing_axis_parts = {
        str(result.get("part"))
        for result in results
        if result.get("axis_calibration") is None
    }
    untrusted_parts = {
        str(result.get("part"))
        for result in results
        if result.get("axis_calibration") is not None and not result.get("axis_calibration_trusted")
    }
    print(f"  charts={len(results)} axis={dict(axis_sources)} trace={dict(trace_statuses)} qoss={dict(qoss_statuses)}")

    if trace_statuses - Counter({"pass": trace_statuses.get("pass", 0)}):
        for result in results:
            if result.get("trace_validation_status") != "pass":
                failures.append(
                    f"{result.get('part')} trace {result.get('trace_validation_status')} "
                    f"{result.get('trace_validation_reasons')}"
                )
    if missing_axis_parts:
        failures.append(f"missing axis calibration: {sorted(missing_axis_parts)}")
    if len(untrusted_parts) > case.max_untrusted_axes:
        failures.append(f"too many untrusted axes: {sorted(untrusted_parts)}")
    unexpected_untrusted = untrusted_parts - set(case.expected_untrusted_parts)
    if unexpected_untrusted:
        failures.append(f"unexpected untrusted axes: {sorted(unexpected_untrusted)}")
    missing_untrusted = set(case.expected_untrusted_parts) - untrusted_parts
    if missing_untrusted:
        failures.append(f"expected untrusted axes no longer present: {sorted(missing_untrusted)}")

    for result in results:
        status = result.get("qoss_validation_status")
        if status not in case.allowed_qoss_statuses:
            failures.append(f"{result.get('part')} unexpected qoss status {status!r}")
        failures.extend(_validate_trace_spans(result))
    return failures


def _validate_trace_spans(result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    diagnostics = result.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return [f"{result.get('part')} missing diagnostics"]
    for name in ("Ciss", "Coss", "Crss"):
        trace_diag = diagnostics.get(name)
        if not isinstance(trace_diag, dict):
            failures.append(f"{result.get('part')} missing {name} diagnostics")
            continue
        points = int(trace_diag.get("points") or 0)
        span = float(trace_diag.get("x_span_fraction") or 0.0)
        if points < 8:
            failures.append(f"{result.get('part')} {name} too few points: {points}")
        if span < 0.75:
            failures.append(f"{result.get('part')} {name} short span: {span:.3f}")
    return failures


if __name__ == "__main__":
    main()
