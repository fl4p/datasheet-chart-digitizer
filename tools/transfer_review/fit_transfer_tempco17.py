#!/usr/bin/env python3
"""Run the curve-anchored saturation-tempco batch over a verified packet.

Usage: fit_transfer_tempco17.py <packet_dir> [out_dir]
The packet must contain gate_anchors.json, manifest.json and points/ CSVs with
the per-point collapsed column. Fit-review plots are rendered separately (the
dsdig venv has no matplotlib; use the ee venv helper in the packet history).
"""
from __future__ import annotations

import sys
from pathlib import Path

from datasheet_chart_digitizer.transfer_tempco_curve_anchor import run_batch


def main() -> None:
    packet = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else packet / "tempco17"
    payload = run_batch(packet, out)
    results = payload["results"]
    candidates = sum(r["status"] == "fit-review-required" for r in results)
    print(f"{candidates} fit-review-required, {len(results) - candidates} refused")
    for r in results:
        print(f"  {r['part']:26s} {r['status']}"
              + (f"  [{'; '.join(r['guard_reasons'])}]" if r["guard_reasons"] else ""))


if __name__ == "__main__":
    main()
