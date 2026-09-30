"""GT overlays and a webp contact sheet (for Fab: webp; tick labels always inside the tile)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import raster as R
from .axes import to_norm

COLS = [(255, 0, 255), (0, 170, 255), (255, 140, 0), (0, 200, 90), (220, 0, 0), (120, 60, 255), (0, 0, 0)]


def gt_px(case, curve):
    """GT curve -> case-image pixel coordinates (continuous), incl. the degradation affine."""
    x0, y0, x1, y1 = case["plot_box_px"]
    u = to_norm(case["axis"]["x"], curve["x"])
    v = to_norm(case["axis"]["y"], curve["y"])
    p = np.c_[x0 + u * (x1 - x0), y1 - v * (y1 - y0)]
    deg = case["gt_provenance"].get("degradation")
    if deg:
        p = R.apply_affine(deg["affine"], p)
    return p


def overlay(case, root: Path, scale=2):
    img = Image.open(root / case["image"]).convert("RGB")
    W, H = img.size
    img = img.resize((W * scale, H * scale), Image.NEAREST)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = case["plot_box_px"]
    box = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]])
    deg = case["gt_provenance"].get("degradation")
    if deg:
        box = R.apply_affine(deg["affine"], box)
    d.line([tuple(q * scale) for q in box], fill=(0, 200, 255), width=1)
    for k, c in enumerate(case["curves"]):
        p = gt_px(case, c) * scale
        d.line([tuple(q) for q in p], fill=COLS[k % len(COLS)], width=1)
    return img


def contact_sheet(cases, root: Path, out: Path, ncol=4, tile=520):
    tiles = []
    font = ImageFont.load_default()
    for c in cases:
        im = overlay(c, root)
        im.thumbnail((tile, tile), Image.LANCZOS)
        t = Image.new("RGB", (tile, tile + 28), "white")
        t.paste(im, ((tile - im.width) // 2, 0))
        f = c["factors"]
        d = ImageDraw.Draw(t)
        d.text((4, tile + 2), f"{c['id']} [{c['tier']}] dpi {f['dpi']} {f['vector_structure_drawn']}", fill="black",
               font=font)
        d.text((4, tile + 14), f"{f['identity']} {f['color_mode']}/{f['distinguish']} deg={','.join(f['degrade_ops'])}"[:90],
               fill="black", font=font)
        tiles.append(t)
    rows = (len(tiles) + ncol - 1) // ncol
    sheet = Image.new("RGB", (ncol * tile, rows * (tile + 28)), "white")
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % ncol) * tile, (i // ncol) * (tile + 28)))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "WEBP", quality=88)
    return out


def main(argv=None):
    a = argv or sys.argv[1:]
    root = Path(a[0])
    cases = json.loads((root / "cases.json").read_text())
    ids = a[2:] if len(a) > 2 else None
    sel = [c for c in cases if not ids or c["id"] in ids]
    print(contact_sheet(sel, root, Path(a[1])))


if __name__ == "__main__":
    main()


def overlay_answer(case, root: Path, answer_txt: Path, scale=2):
    """GT (thin colour) plus a model/dsdig answer CSV (dots) on the case image."""
    import io

    import pandas as pd
    img = overlay(case, root, scale)
    d = ImageDraw.Draw(img)
    body = answer_txt.read_text().split("```csv")[-1].split("```")[0]
    df = pd.read_csv(io.StringIO("\n".join(l for l in body.splitlines() if l.strip() and not l.startswith("#"))))
    x0, y0, x1, y1 = case["plot_box_px"]
    for k, col in enumerate(df.columns[1:]):
        ok = df[col].notna()
        u = to_norm(case["axis"]["x"], df.iloc[:, 0][ok].values)
        v = to_norm(case["axis"]["y"], df[col][ok].values)
        for a, b in zip(x0 + u * (x1 - x0), y1 - v * (y1 - y0)):
            if np.isfinite(a) and np.isfinite(b):
                d.ellipse([a * scale - 2, b * scale - 2, a * scale + 2, b * scale + 2], outline=(255, 0, 0))
    return img
