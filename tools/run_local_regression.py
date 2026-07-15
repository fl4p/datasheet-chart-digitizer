#!/usr/bin/env python3
"""Run local regression checks for all wired chart digitizers.

This is Fab-workstation specific. It combines the in-repo MOSFET C(V)
regression corpus with the packaged Vpl full-curve verification harness.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import run_capacitance_regression
import run_vpl_finder_parity


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
VPL_EXPECTED_FAILURES = {
    # pwr-mosfet-lib/test/test_viz_vpl.py tracks this as still
    # off/reference-disputed. Keep it visible in the overlay run, but do not
    # block the local regression suite on it.
    "SIHD6N65ET4-GE3-HXY": "reference-disputed in pwr-mosfet-lib Vpl tests",
    "R6509KND3TL1-HXY": "raster axis is assumed pending overlay review",
}
DEFAULT_VPL_SAMPLE_COUNT = 15


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        help="Output root for C(V) regeneration. Vpl currently writes to its established local output directory.",
    )
    parser.add_argument(
        "--skip-cv",
        action="store_true",
        help="Skip MOSFET capacitance regression.",
    )
    parser.add_argument(
        "--skip-vpl",
        action="store_true",
        help="Skip Vpl gate-charge regression.",
    )
    parser.add_argument(
        "--skip-vpl-finder",
        action="store_true",
        help="Skip Vpl finder parity regression.",
    )
    parser.add_argument(
        "--vpl-start",
        type=int,
        default=None,
        help="Optional 1-based start index in chart-extraction.md for Vpl samples.",
    )
    parser.add_argument(
        "--vpl-count",
        type=int,
        default=None,
        help=f"Optional Vpl sample count. Defaults to the {DEFAULT_VPL_SAMPLE_COUNT} built-in human-verified samples.",
    )
    parser.add_argument(
        "--vpl-tol",
        type=float,
        default=0.5,
        help="Maximum allowed abs(Vpl-ref) error in volts.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    failures: list[str] = []

    if not args.skip_cv:
        cv_out = args.out / "capacitance" if args.out is not None else None
        try:
            if cv_out is not None:
                run_capacitance_regression._run_cases(list(run_capacitance_regression.CASES), cv_out)
            else:
                import tempfile

                with tempfile.TemporaryDirectory(prefix="dsdig-cap-regression-") as tmp:
                    run_capacitance_regression._run_cases(list(run_capacitance_regression.CASES), Path(tmp))
        except SystemExit as exc:
            if exc.code:
                failures.append(f"C(V) regression failed with exit {exc.code}")

    if not args.skip_vpl:
        failures.extend(_run_vpl_regression(args.vpl_tol, args.vpl_start, args.vpl_count))
    if not args.skip_vpl_finder:
        print("== vpl_finder_parity")
        failures.extend(run_vpl_finder_parity.run_parity(run_vpl_finder_parity.DEFAULT_DATASHEET_ROOT))

    if failures:
        print()
        print("Local regression failures:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("ALL LOCAL REGRESSIONS PASS")


def _run_vpl_regression(tol: float, start: int | None, count: int | None) -> list[str]:
    cmd = [sys.executable, "-m", "datasheet_chart_digitizer.gate_charge_vpl", "--reference-assisted"]
    if start is not None:
        cmd.extend(["--start", str(start)])
    if count is not None:
        cmd.extend(["--count", str(count)])

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    completed = subprocess.run(cmd, cwd=REPO_ROOT, env=env, text=True, capture_output=True)
    print("== vpl_gate_charge_fullcurve")
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.returncode != 0:
        return [f"Vpl script exited {completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}"]

    rows = _parse_vpl_output(completed.stdout)
    if not rows:
        return ["Vpl script produced no parsable sample rows"]
    if count is not None:
        expected_count = count
    elif start is not None:
        expected_count = DEFAULT_VPL_SAMPLE_COUNT
    else:
        expected_count = DEFAULT_VPL_SAMPLE_COUNT
    if len(rows) != expected_count:
        return [f"Vpl script produced {len(rows)} parsable sample rows, expected {expected_count}"]

    failures: list[str] = []
    xfails: list[str] = []
    for row in rows:
        mpn = str(row["mpn"])
        if row["curve_pts"] <= 0:
            failures.append(f"{mpn} traced no curve points")
            continue
        if row["err_v"] is None:
            failures.append(f"{mpn} produced no numeric Vpl")
            continue
        if abs(row["err_v"]) > tol:
            if mpn in VPL_EXPECTED_FAILURES:
                xfails.append(f"{mpn} {row['err_v']:+.2f} V ({VPL_EXPECTED_FAILURES[mpn]})")
            else:
                failures.append(f"{mpn} Vpl error {row['err_v']:+.2f} V exceeds {tol:.2f} V")
    for xfail in xfails:
        print(f"  XFAIL {xfail}")
    if not failures:
        print(f"  PASS samples={len(rows)} tol={tol:.2f} V xfail={len(xfails)}")
    return failures


def _parse_vpl_output(text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    pattern = re.compile(
        r"^(?P<mpn>\S+)\s+ref=(?P<ref>[-+]?\d+(?:\.\d+)?)\s+"
        r"Vpl=(?P<vpl>none|[-+]?\d+(?:\.\d+)?)\s*(?P<err>[-+]\d+(?:\.\d+)?)?"
        r".*?\bstatus=(?P<status>\S+).*?\bcurve_pts=(?P<curve_pts>\d+)",
        re.M,
    )
    for match in pattern.finditer(text):
        rows.append(
            {
                "mpn": match.group("mpn"),
                "ref_v": float(match.group("ref")),
                "vpl_v": None if match.group("vpl") == "none" else float(match.group("vpl")),
                "err_v": float(match.group("err")) if match.group("err") else None,
                "status": match.group("status"),
                "curve_pts": int(match.group("curve_pts")),
            }
        )
    return rows


if __name__ == "__main__":
    main()
