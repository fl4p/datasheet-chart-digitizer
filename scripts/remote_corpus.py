#!/usr/bin/env python3
"""List the local files outside the repo that the tests read, for scripts/test-remote.sh.

Prints absolute paths, one per line: every corpus file whose name or stem
appears as a token in tests/ (with its X.pdf.* / X.*.pdf sidecars, git-ignored ones included), every
~/dev/... literal (Path.home() / "dev/..."), every absolute path literal in tests/ that
exists outside the datasheet corpus (whole directory when it is one), and the
vendorpull pieces tests/test_mlcc_vector.py extracts from. Over-inclusion is
cheap (rsync skips unchanged files); a file missed here shows up as an extra
skip or a FileNotFoundError on the remote host.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATASHEET_ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", "/Users/fab/dev/pv/pwr-mosfet-lib"))
VENDORPULL_ROOT = Path(os.environ.get("DSDIG_VENDORPULL_ROOT", "/Users/fab/dev/pv/ee/dcdc-tools"))
SUFFIXES = {".py", ".json", ".csv", ".txt", ".md"}


def _git_files(root: Path) -> list[str]:
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "-z"],
                       capture_output=True, check=True)
    return [p for p in r.stdout.decode(errors="surrogateescape").split("\0") if p]


def main() -> int:
    text = "\n".join(p.read_text(errors="replace") for p in (REPO / "tests").rglob("*")
                     if p.is_file() and p.suffix in SUFFIXES)
    tokens = set(re.findall(r"[A-Za-z0-9_#.+\-]+", text))
    for t in re.findall(r"[^\s\"'/()\[\]{}=<>:;`]+", text):
        tokens.update((t, t.strip(",.")))
    out: set[Path] = set()
    datasheets = DATASHEET_ROOT / "datasheets"
    if datasheets.is_dir():
        for rel in _git_files(datasheets):
            p = Path(rel)
            # X.pdf and its sidecars (X.pdf.txt, X.pdf.nop.csv, ...)
            base = p.name.split(".pdf")[0] if ".pdf" in p.name else p.stem
            if {p.name, p.stem, base, p.name.split(".")[0]} & tokens:
                out.add(datasheets / p)
    home = Path("/Users/fab")
    lits = {Path(m) for m in re.findall(r"[\"'](/Users/[^\"']+)[\"']", text)}
    # Path.home() / "dev/..." (the remote job runs with HOME=/Users/fab)
    lits |= {home / m for m in re.findall(r"[\"']((?:dev|tmp|Documents)/[^\"']+)[\"']", text)}
    for p in lits:
        if p.is_file():
            out.add(p)
        elif p.is_dir() and datasheets not in (p, *p.parents) and p not in datasheets.parents:
            out.update(f for f in p.rglob("*") if f.is_file())
    vp = VENDORPULL_ROOT / "vendorpull"
    if vp.is_dir():
        out.update(vp.glob("*.py"))
        for mpn in set(re.findall(r"MPN\s*=\s*[\"']([^\"']+)[\"']", text)):
            for archive in (vp / "store").glob(f"*/{mpn}.tar.zst"):
                out.add(archive)
                if (archive.parent / ".zdict").exists():
                    out.add(archive.parent / ".zdict")
    sys.stdout.write("".join(f"{p}\n" for p in sorted(out) if p.is_file()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
