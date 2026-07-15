from __future__ import annotations

import os
import re
from pathlib import Path

from PIL import Image, ImageFont


DEFAULT_DATASHEET_ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", "."))
DEFAULT_OUT = Path(os.environ.get("DSDIG_OUT", "out/vpl"))
DEFAULT_DPI = 220

SAMPLES = [
    ("agmsemi/AGM15T13D.pdf", 4.2, "line has a bright blue"),
    ("ao/AOMR62818.pdf", 3.0, "plateau starts smoothly"),
    ("ao/AOT286L.pdf", 4.2, "line has noise"),
    ("infineon/IPW65R019C7.pdf", 5.4, ""),
    ("infineon/IRF540NL.pdf", 4.6, 'overlapping text box "FOR TEST..."'),
    ("nxp/PSMN1R2-55SLH.pdf", 2.4, "three VDS curves"),
    ("onsemi/NVMFS5C468NLT1G.pdf", 3.5, "dimension lines with Qgs,Qgd labels"),
    ("onsemi/NVMYS029N08LHTWG.pdf", 3.0, "dimension lines with Qgs,Qgd labels"),
    ("onsemi/NVTFWS010N10MCLTAG.pdf", 2.6, "dimension lines with Qgs,Qgd labels"),
    ("agmsemi/AGM025N13LL.pdf", 4.3, "rasterized"),
    ("agmsemi/AGM150P10AP.pdf", 3.1, "rasterized"),
    ("hxy/R6509KND3TL1-HXY.pdf", 8.0, "rasterized"),
    ("hxy/SIHD6N65ET4-GE3-HXY.pdf", 2.9, "rasterized"),
    ("infineon/IAUC28N08S5L230ATMA1.pdf", 3.1, "rasterized"),
    ("infineon/F3L3MR12W3M1HH11BPSA1.pdf", 7.25, ""),
]


def _samples_from_chart_extraction(path: Path, start: int, count: int) -> list[tuple[str, float | None, str]]:
    text = path.read_text()
    items: list[tuple[str, float | None, str]] = []
    for match in re.finditer(r'"(datasheets/[^"]+\.pdf)"\s*:\s*\{([^{}]*)\}', text, re.S):
        rel = match.group(1)
        body = match.group(2)
        ref_match = re.search(r'"ref"\s*:\s*([-+]?[0-9]*\.?[0-9]+)', body)
        comment_match = re.search(r'"comment"\s*:\s*"([^"]*)"', body)
        if rel.startswith("datasheets/"):
            rel = rel[len("datasheets/") :]
        ref = float(ref_match.group(1)) if ref_match else None
        comment = comment_match.group(1).replace("\\", "") if comment_match else ""
        items.append((rel, ref, comment))
    lo = max(0, start - 1)
    hi = lo + count
    return items[lo:hi]


def _sample_pdf_path(datasheet_root: Path, rel_or_path: str) -> Path:
    path = Path(rel_or_path).expanduser()
    if path.is_absolute():
        return path
    return datasheet_root / "datasheets" / path


def _font(size: int):
    for name in ("Arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _save_sheet(images: list[Image.Image], path: Path) -> None:
    width = max(im.width for im in images)
    height = sum(im.height for im in images) + 16 * (len(images) - 1)
    sheet = Image.new("RGB", (width, height), "white")
    y = 0
    for im in images:
        sheet.paste(im, (0, y))
        y += im.height + 16
    sheet.save(path)
