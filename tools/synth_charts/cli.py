"""CLI: python -m tools.synth_charts --n 400 --seed 1 --out out/synth-charts/set1"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from .build import generate


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.synth_charts")
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--set-name", default=None, help="case-id prefix (default syn<seed>)")
    ap.add_argument("--classes", nargs="*")
    ap.add_argument("--no-probe", action="store_true", help="skip the probe PDFs (vector check needs them)")
    a = ap.parse_args(argv)
    t = time.time()
    cases = generate(a.out, a.n, a.seed, a.set_name or f"syn{a.seed}", a.classes, probe=not a.no_probe)
    print(f"{len(cases)} cases -> {a.out} ({time.time() - t:.0f} s)")


if __name__ == "__main__":
    main()
