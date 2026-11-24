#!/usr/bin/env python3
"""Numbered contact sheets of page regions, for labelling a census sample by eye.

Input: a JSON list of items with ``pdf`` (relative to --root), ``page`` and either
``bbox_pt`` or nothing (whole page).  Output: sheet_NN.png files with each tile
captioned by its index in the list.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("items", type=Path)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tile", type=int, default=360, help="tile edge in px")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--rows", type=int, default=4)
    ap.add_argument("--margin-pt", type=float, default=10.0)
    args = ap.parse_args()
    items = json.loads(args.items.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    per = args.cols * args.rows
    for s in range(0, len(items), per):
        sheet = Image.new("RGB", (args.cols * args.tile, args.rows * (args.tile + 16)), "white")
        draw = ImageDraw.Draw(sheet)
        for k, it in enumerate(items[s:s + per]):
            with pymupdf.open(args.root / it["pdf"]) as doc:
                page = doc[it["page"] - 1]
                clip = page.rect
                if it.get("bbox_pt"):
                    m = args.margin_pt
                    clip = (pymupdf.Rect(*it["bbox_pt"]) + (-m, -m, m, m)) & page.rect
                zoom = args.tile / max(clip.width, clip.height, 1.0)
                pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            tile = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            x, y = (k % args.cols) * args.tile, (k // args.cols) * (args.tile + 16)
            sheet.paste(tile, (x, y + 16))
            draw.text((x + 3, y + 2), f"#{s + k} {it.get('vendor', '')} p{it['page']} {it.get('tag', '')}"[:58], fill="red")
        sheet.save(args.out / f"sheet_{s // per:02d}.png")
    print(f"wrote {(len(items) + per - 1) // per} sheets to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
