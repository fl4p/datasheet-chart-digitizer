#!/usr/bin/env python3
"""Audit and promote the fixed 25-manufacturer MOSFET transfer batch."""

from __future__ import annotations

import argparse
from pathlib import Path

from datasheet_chart_digitizer.transfer_closure import (
    audit_transfer_batch,
    write_closure_report,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, help="digitized batch directory")
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="skip OCR only for controlled tests; production closure must use OCR",
    )
    args = parser.parse_args()
    root = args.output.resolve()
    closure, promoted = audit_transfer_batch(root, use_ocr=not args.no_ocr)
    write_closure_report(root, closure, promoted)
    accepted = sum(record["decision"] == "accepted" for record in closure)
    rejected = len(closure) - accepted
    print(f"accepted={accepted} rejected={rejected}")
    if rejected:
        for record in closure:
            if record["decision"] == "rejected":
                failures = [gate["name"] for gate in record["gates"] if not gate["passed"]]
                print(f"REJECT {record['part']}: {', '.join(failures)}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
