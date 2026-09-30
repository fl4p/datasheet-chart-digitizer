"""Printed curve-condition text inside raster RDS(on)-versus-VGS plots (review F5-3).

Two readers that the plot-wide OCR (``ocr_plot_labels``) misses, both used
ONLY for curve labels -- never for the text boxes erased from the ink mask,
so no traced point depends on them:

  * FRAMED BOXES. A condition box printed inside the plot ("Ta=25°C /
    Pulsed", Rohm) is read as a unit: its white interior is OCRed on its
    own, 4x, as a text block. v5 read RQ3E180AJ's box as "7.225%" and so no
    temperature at all. The kind is read from the subscript glyph after the
    "T" on its own (``_read_subscript``); the block OCR drops it ("T.=25°C").
  * TEXT CROSSED BY A GRID RULE. BRCS020N03RA prints "125°  C" and "25°  C"
    with a vertical rule running between "°" and "C"; the plot OCR read
    neither label. The plot is OCRed again with the verified rules painted
    white.

Every reading is only a candidate label: it binds to a curve only through
the conservative binder, and a line that does not parse as a curve
parameter is dropped. A misread costs a binding, never a value.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from .capacitance_types import PlotBox
from .rdson_gate_voltage_traces import Label, parse_label_params
from .region_ocr import _tesseract_words
from .rdson_gate_voltage_axes import _text_blobs

BOX_MIN_W_PX, BOX_MIN_H_PX = 60, 25       # at 300 dpi: two short text lines
BOX_MAX_FRACTION = 0.6                    # of the plot, per side: larger is not a legend box
BOX_FILL_MIN = 0.97                       # interior (text holes included) / bounding box: a rectangle
BOX_FRAME_DARK_MIN = 0.9                  # each side's outline dark along >= 90 % of its length
BOX_RULE_TOLERANCE_PX = 4.0               # an edge this close to a verified rule lies on the grid
BOX_OCR_SCALE = 4.0
RULE_ERASE_HALF_PX = 2                    # a verified rule is painted white +-2 px for the rule-free OCR
SUBSCRIPT_KINDS = {"a": "Ta", "j": "Tj", "c": "Tc"}


def isolated_condition_labels(gray, plot, out_dir, panel, stem):
    """Read isolated text components after tracking, so OCR never moves ink.

    Large connected strokes (curves/grid) are excluded before grouping glyphs.
    Both line and word OCR must independently parse the same current. No part
    names, expected current values or temperature defaults enter this reader.
    """
    if shutil.which("tesseract") is None:
        return []
    ink = (gray < 150).astype(np.uint8)
    count, components, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    keep = np.zeros(count, np.uint8)
    for i, (x, y, w, h, area) in enumerate(stats[1:], 1):
        if 4 <= h <= 60 and 2 <= w <= 100 and area >= 4 and plot.x0 < x and x + w < plot.x1 and plot.y0 < y and y + h < plot.y1:
            keep[i] = 1
    labels = []
    for n, (x0, y0, x1, y1) in enumerate(_text_blobs(keep[components])):
        if x1 - x0 < 30 or y1 - y0 < 8:
            continue
        pad = 4
        region = gray[max(0, y0-pad):y1+pad, max(0, x0-pad):x1+pad]
        up = cv2.resize(region, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        up = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        target = out_dir / "work" / "condition_ocr" / panel.part / f"{stem}_{n}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(target), cv2.copyMakeBorder(up, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255))
        votes = []
        for psm in (7, 8):
            words = _tesseract_words(target, clip=pymupdf.Rect(0, 0, up.shape[1]+48, up.shape[0]+48),
                                     scale_x=1, scale_y=1, psm=psm, timeout=30, whitelist=None, min_confidence=0)
            text = " ".join(t for *_box, t in words).rstrip("_")
            votes.append((text, parse_label_params(text)))
        if votes[0][1].get("id_a") is not None and votes[0][1] == votes[1][1]:
            labels.append(Label(votes[0][0] + " [isolated glyph OCR agreement]", x0, y0, x1, y1, votes[0][1]))
    return labels


def refine_temperature_subscripts(gray, labels, out_dir, panel, stem):
    """Resolve an unread T subscript from its own glyph, never from another T."""
    out = []
    for n, label in enumerate(labels):
        if label.params.get("temperature_kind") != "T (subscript unread)":
            out.append(label)
            continue
        x0, y0 = max(0, int(label.x0)-4), max(0, int(label.y0)-4)
        region = gray[y0:int(label.y1)+12, x0:int(label.x1)+4]
        up = cv2.resize(region, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        # The thin lowered J can be much paler than the full-size T; an
        # Otsu threshold removes its vertical stem on WSR3090.
        binary = cv2.threshold(up, 220, 255, cv2.THRESH_BINARY)[1]
        target = out_dir / "work" / "subscript_ocr" / panel.part / f"{stem}_{n}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(target), binary)
        # OCR boxes can include a leader prefix. Look for the T among the
        # first three components; the existing geometric subscript test and
        # single-glyph OCR still have to agree, and ambiguity leaves it unread.
        count, _, stats, _ = cv2.connectedComponentsWithStats((binary < 128).astype(np.uint8), 8)
        comps = sorted((s for s in stats[1:] if s[4] >= 12), key=lambda s: s[0])
        readings = []
        for x, y, w, h, area in comps[:3]:
            kind, evidence = _read_subscript(binary, (x, y, binary.shape[1]-1, min(binary.shape[0]-4, y + 1.4*h)), target)
            if kind:
                readings.append((kind, evidence))
        if len({k for k, _ in readings}) == 1:
            kind, evidence = readings[0]
            label = Label(label.text + f" [subscript read alone: {evidence!r}]", label.x0, label.y0, label.x1, label.y1,
                          dict(label.params, temperature_kind=kind))
        out.append(label)
    return out


def _ink(gray) -> np.ndarray:
    return gray < 160


def legend_boxes(gray, plot: PlotBox, rules_x, rules_y) -> list[tuple[int, int, int, int]]:
    """Framed boxes printed inside the plot, as (x0, y0, x1, y1) of their white interior (crop px).

    A white 4-connected region whose outline (text holes filled) is a
    rectangle of at least BOX_MIN_W_PX x BOX_MIN_H_PX and at most
    BOX_MAX_FRACTION of the plot per side, that holds text (>= 2 ink holes),
    whose four sides are dark lines, and that is not a grid cell: at least
    one vertical and one horizontal side lie off every verified rule.
    """
    x_lo, y_lo = plot.x0 + 3, plot.y0 + 3
    sub = gray[y_lo:plot.y1 - 2, x_lo:plot.x1 - 2]
    if sub.size == 0:
        return []
    ink = _ink(sub)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats((~ink).astype(np.uint8), connectivity=4)
    height, width = ink.shape
    out = []
    for index in range(1, count):
        x, y, w, h, _area = (int(v) for v in stats[index])
        if w < BOX_MIN_W_PX or h < BOX_MIN_H_PX or w > BOX_MAX_FRACTION * width or h > BOX_MAX_FRACTION * height:
            continue
        region = labels[y:y + h, x:x + w] == index
        holes_n, holes, hole_stats, _c = cv2.connectedComponentsWithStats((~region).astype(np.uint8), connectivity=8)
        inner = [k for k in range(1, holes_n)
                 if hole_stats[k, 0] > 0 and hole_stats[k, 1] > 0
                 and hole_stats[k, 0] + hole_stats[k, 2] < w and hole_stats[k, 1] + hole_stats[k, 3] < h]
        filled = region.sum() + sum(int(hole_stats[k, 4]) for k in inner)
        if filled < BOX_FILL_MIN * w * h or len(inner) < 2:
            continue
        if not _framed(ink, x, y, w, h):
            continue
        X0, Y0, X1, Y1 = x_lo + x, y_lo + y, x_lo + x + w, y_lo + y + h

        def off(value: float, rules) -> bool:
            return all(abs(value - r) > BOX_RULE_TOLERANCE_PX for r in rules)

        if not ((off(X0, rules_x) or off(X1, rules_x)) and (off(Y0, rules_y) or off(Y1, rules_y))):
            continue   # bounded by rules on a whole axis: a grid cell, not a box
        out.append((X0, Y0, X1, Y1))
    return out


def _framed(ink: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
    """Is each side of the white region bounded by a dark line (within 3 px outside it)?"""
    sides = (
        ink[max(0, y - 3):y, x:x + w].any(axis=0),
        ink[y + h:y + h + 3, x:x + w].any(axis=0),
        ink[y:y + h, max(0, x - 3):x].any(axis=1),
        ink[y:y + h, x + w:x + w + 3].any(axis=1),
    )
    return all(side.size and side.mean() >= BOX_FRAME_DARK_MIN for side in sides)


def read_legend_boxes(gray, plot: PlotBox, rules_x, rules_y, out_dir: Path, panel, stem: str) -> list[tuple[tuple, list[Label]]]:
    """OCR each framed box as a text block; one Label per line that parses as a curve parameter.

    Returns [(box, labels)] so the caller can let the box reading replace the
    words other sources read inside the same box.
    """
    if shutil.which("tesseract") is None:
        return []
    out = []
    for n, box in enumerate(legend_boxes(gray, plot, rules_x, rules_y)):
        x0, y0, x1, y1 = box
        window = gray[y0 + 1:y1 - 1, x0 + 1:x1 - 1]
        up = cv2.resize(window, None, fx=BOX_OCR_SCALE, fy=BOX_OCR_SCALE, interpolation=cv2.INTER_CUBIC)
        _thr, binary = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        target = out_dir / "work" / "legend_box_ocr" / panel.part / f"{stem}_box{n}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(target), binary)
        words = _tesseract_words(target, clip=pymupdf.Rect(0, 0, binary.shape[1], binary.shape[0]), scale_x=1.0,
                                 scale_y=1.0, psm=6, timeout=60.0, whitelist=None, min_confidence=0.0)
        labels = []
        for line in _lines(words):
            text = " ".join(t for *_b, t in line)
            params = parse_label_params(text)
            if not params:
                continue
            lx0 = min(w[0] for w in line); ly0 = min(w[1] for w in line)
            lx1 = max(w[2] for w in line); ly1 = max(w[3] for w in line)
            if params.get("temperature_kind") == "T (subscript unread)":
                kind, sub_text = _read_subscript(binary, (lx0, ly0, lx1, ly1), target)
                if kind is not None:
                    params["temperature_kind"] = kind
                    text = f"{text} [subscript read alone: {sub_text!r}]"
            s = 1.0 / BOX_OCR_SCALE
            labels.append(Label(text, x0 + 1 + lx0 * s, y0 + 1 + ly0 * s, x0 + 1 + lx1 * s, y0 + 1 + ly1 * s, params))
        out.append((box, labels))
    return out


def _lines(words) -> list[list]:
    """Group OCR words (x0, y0, x1, y1, text) into lines by vertical overlap, left to right."""
    lines: list[list] = []
    for word in sorted(words, key=lambda w: (0.5 * (w[1] + w[3]), w[0])):
        cy = 0.5 * (word[1] + word[3])
        for line in lines:
            if min(w[1] for w in line) <= cy <= max(w[3] for w in line):
                line.append(word)
                break
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w[0]) for line in lines]


_TEMP_START_RE = re.compile(r"^\W*T")


def _read_subscript(binary, line_box, stem_path: Path) -> tuple[str | None, str]:
    """The kind letter printed as a subscript after "T", read on its own.

    Block OCR reads "Ta=25°C" as "T.=25°C": the lowered small "a" is lost.
    Here the line's ink components are ordered left to right; the first is
    the "T" (at least 60 % of the line height). The subscript is the next
    component if it starts within half a "T" width of it, is at most 75 % of
    the "T"'s height, and reaches at least 10 % of that height below the
    "T"'s baseline. It is OCRed alone (single character, no whitelist), and
    only a, j or c (either case) is accepted as a kind -- anything else
    leaves the kind unread.
    """
    x0, y0, x1, y1 = (int(round(v)) for v in line_box)
    pad = 6
    top, left = max(0, y0 - pad), max(0, x0 - pad)
    region = binary[top:y1 + pad, left:x1 + pad]
    ink = (region < 128).astype(np.uint8)
    count, _labels, stats, _c = cv2.connectedComponentsWithStats(ink, connectivity=8)
    comps = sorted((tuple(int(v) for v in stats[k][:4]) for k in range(1, count) if stats[k][4] >= 12), key=lambda c: c[0])
    line_h = max(1, y1 - y0)
    if len(comps) < 2:
        return None, ""
    tx, ty, tw, th = comps[0]
    if th < 0.6 * line_h:
        return None, ""
    sx, sy, sw, sh = comps[1]
    starts_near = sx - (tx + tw) <= 0.5 * tw
    small = sh <= 0.75 * th
    lowered = (sy + sh) - (ty + th) >= 0.10 * th
    if not (starts_near and small and lowered):
        return None, ""
    glyph = region[max(0, sy - 8):sy + sh + 8, max(0, sx - 8):sx + sw + 8]
    scale = max(1.0, 80.0 / max(1, glyph.shape[0]))
    glyph = cv2.resize(glyph, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    glyph = cv2.copyMakeBorder(glyph, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    target = stem_path.with_name(f"{stem_path.stem}_sub_{x0}_{y0}.png")
    cv2.imwrite(str(target), glyph)
    words = _tesseract_words(target, clip=pymupdf.Rect(0, 0, glyph.shape[1], glyph.shape[0]), scale_x=1.0,
                             scale_y=1.0, psm=10, timeout=30.0, whitelist=None, min_confidence=0.0)
    text = "".join(t for *_b, t in words).strip()
    kind = SUBSCRIPT_KINDS.get(text.lower()) if len(text) == 1 else None
    return kind, text


def ocr_plot_labels_rules_erased(gray, plot: PlotBox, rules_x, rules_y, out_dir: Path, panel, stem: str) -> list[Label]:
    """The plot OCRed with every verified grid rule painted white; parsed label lines only.

    For labels a rule runs through (BRCS020N03RA: "125°  C", "25°  C"). The
    words are grouped into lines here, and a line is kept only if it parses
    as a curve parameter.
    """
    if shutil.which("tesseract") is None:
        return []
    clean = gray.copy()
    for x in rules_x:
        c = int(round(x))
        clean[plot.y0:plot.y1 + 1, max(0, c - RULE_ERASE_HALF_PX):c + RULE_ERASE_HALF_PX + 1] = 255
    for y in rules_y:
        c = int(round(y))
        clean[max(0, c - RULE_ERASE_HALF_PX):c + RULE_ERASE_HALF_PX + 1, plot.x0:plot.x1 + 1] = 255
    x_lo, y_lo = plot.x0 + 3, plot.y0 + 3
    sub = clean[y_lo:plot.y1 - 2, x_lo:plot.x1 - 2]
    if sub.size == 0:
        return []
    scale = 2.0
    up = cv2.resize(sub, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _thr, binary = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    target = out_dir / "work" / "plot_ocr_rules_erased" / panel.part / f"{stem}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(target), binary)
    words = _tesseract_words(target, clip=pymupdf.Rect(0, 0, binary.shape[1], binary.shape[0]), scale_x=1.0,
                             scale_y=1.0, psm=11, timeout=90.0, whitelist=None, min_confidence=40.0)
    labels = []
    for line in _lines(words):
        for part in _gap_split(line):
            text = " ".join(t for *_b, t in part)
            params = parse_label_params(text)
            if not params:
                continue
            labels.append(Label(text, x_lo + min(w[0] for w in part) / scale, y_lo + min(w[1] for w in part) / scale,
                                x_lo + max(w[2] for w in part) / scale, y_lo + max(w[3] for w in part) / scale, params))
    return labels


def _gap_split(line) -> list[list]:
    """Split a line where the gap between words exceeds 3x the word height (two labels on one row)."""
    parts, current = [], [line[0]]
    for word in line[1:]:
        if word[0] - current[-1][2] > 3.0 * max(1.0, current[-1][3] - current[-1][1]):
            parts.append(current)
            current = [word]
        else:
            current.append(word)
    parts.append(current)
    return parts
