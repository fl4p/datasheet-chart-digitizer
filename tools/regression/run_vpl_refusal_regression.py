#!/usr/bin/env python3
"""Replay original-source gate-charge PDFs that previously refused Vpl."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from datasheet_chart_digitizer.gate_charge import (
    digitize_gate_charge_fail_closed,
)


def _paths_from_file(path: Path) -> list[Path]:
    return [
        Path(line.strip()).expanduser().resolve()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="*", type=Path)
    parser.add_argument("--paths-file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dpi", default=220, type=int)
    parser.add_argument("--finder-dpi", default=120, type=int)
    args = parser.parse_args()
    pdfs = [path.expanduser().resolve() for path in args.pdfs]
    if args.paths_file is not None:
        pdfs.extend(_paths_from_file(args.paths_file))
    pdfs = list(dict.fromkeys(pdfs))
    if not pdfs:
        parser.error("provide PDFs or --paths-file")

    rows = []
    failures = []
    for index, pdf in enumerate(pdfs, start=1):
        results, errors = digitize_gate_charge_fail_closed(
            pdf,
            dpi=args.dpi,
            finder_dpi=args.finder_dpi,
        )
        served = [
            result
            for result in results
            if result.status == "ok"
            and result.vpl is not None
            and result.x_tick_unit is not None
        ]
        if not served or errors:
            failures.append(str(pdf))
        row = {
            "pdf": str(pdf),
            "served": [
                {
                    "page": result.panel.page,
                    "diagram": result.panel.diagram,
                    "vpl": result.vpl,
                    "unit": result.x_tick_unit,
                    "y_tick_count": result.y_tick_count,
                    "diagnostics": list(result.diagnostics),
                }
                for result in served
            ],
            "other_results": [
                {
                    "page": result.panel.page,
                    "diagram": result.panel.diagram,
                    "status": result.status,
                    "diagnostics": list(result.diagnostics),
                }
                for result in results
                if result not in served
            ],
            "errors": errors,
        }
        rows.append(row)
        print(
            f"[{index}/{len(pdfs)}] {pdf.name}: "
            f"served={len(served)} other={len(results) - len(served)} "
            f"errors={len(errors)}",
            flush=True,
        )

    payload = {
        "contract": (
            "At least one status=ok, numeric Vpl, locally evidenced charge-unit "
            "result per replayed original-source non-HXY PDF; panel errors are "
            "failures. Generated text-repair derivatives are integration inputs, "
            "not independent datasheets."
        ),
        "dpi": args.dpi,
        "finder_dpi": args.finder_dpi,
        "pdf_count": len(pdfs),
        "passed_count": len(pdfs) - len(failures),
        "failed_count": len(failures),
        "failures": failures,
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(
        f"summary: passed={payload['passed_count']} "
        f"failed={payload['failed_count']} output={args.output}"
    )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
