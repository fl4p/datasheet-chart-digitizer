#!/usr/bin/env python3
"""Build the guarded eight-part saturation-tempco evidence batch."""

from __future__ import annotations

import argparse
from pathlib import Path

from datasheet_chart_digitizer.transfer_anchor_batch import run_batch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", type=Path)
    parser.add_argument(
        "--datasheet-root",
        type=Path,
        default=Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets"),
    )
    args = parser.parse_args()
    payload = run_batch(args.batch_dir.resolve(), args.datasheet_root.resolve())
    candidates = sum(result["status"] == "fit-review-required" for result in payload["results"])
    print(f"wrote {args.batch_dir / 'saturation-tempco8.json'}")
    print(f"outcome: {candidates} fit-review-required, {len(payload['results']) - candidates} refused")


if __name__ == "__main__":
    main()
