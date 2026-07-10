#!/usr/bin/env python3
from __future__ import annotations

import math
import re
import sys
import argparse
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont


DEFAULT_DATASHEET_ROOT = Path("/Users/fab/dev/pv/pwr-mosfet-lib")
DEFAULT_OUT = Path("/Users/fab/dev/pv/ee/out/vpl_human_refs_first5_dsdig_fullcurve")
DEFAULT_DPI = 220

SAMPLES = [
    ("agmsemi/AGM15T13D.pdf", 4.2, "line has a bright blue"),
    ("ao/AOMR62818.pdf", 3.0, "plateau starts smoothly"),
    ("ao/AOT286L.pdf", 4.2, "line has noise"),
    ("infineon/IPW65R019C7.pdf", 5.4, ""),
    ("infineon/IRF540NL.pdf", 4.6, 'overlapping text box "FOR TEST..."'),
    ("nxp/PSMN1R2-55SLH.pdf", 2.4, ""),
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


def _pdf_to_px(rect: pymupdf.Rect, scale: float, x: float, y: float) -> tuple[float, float]:
    return (x - rect.x0) * scale, (y - rect.y0) * scale


def _cluster_runs(col: np.ndarray, min_len: int = 2, gap: int = 1) -> list[float]:
    ys = np.where(col > 0)[0]
    if len(ys) == 0:
        return []
    groups: list[list[int]] = [[int(ys[0])]]
    for y in ys[1:]:
        if int(y) - groups[-1][-1] <= gap + 1:
            groups[-1].append(int(y))
        else:
            groups.append([int(y)])
    return [float(np.mean(g)) for g in groups if len(g) >= min_len]


def _mask_page_text(
    crop: Image.Image,
    page: pymupdf.Page,
    crop_rect: pymupdf.Rect,
    plot_box_pdf: pymupdf.Rect,
    scale: float,
) -> Image.Image:
    """Erase text whose bbox centre is inside the plot area.

    This removes QGS/QGD labels, VDS labels, and in-chart test-condition text
    before raster tracing. Axis labels remain because their centres are outside
    the plot frame.
    """
    out = crop.copy()
    draw = ImageDraw.Draw(out)
    try:
        words = page.get_text("words")
    except Exception:
        return out
    pad = max(2, int(round(scale * 1.4)))
    for word in words:
        x0, y0, x1, y1 = [float(v) for v in word[:4]]
        cx = 0.5 * (x0 + x1)
        cy = 0.5 * (y0 + y1)
        if not plot_box_pdf.contains((cx, cy)):
            continue
        px0, py0 = _pdf_to_px(crop_rect, scale, x0, y0)
        px1, py1 = _pdf_to_px(crop_rect, scale, x1, y1)
        draw.rectangle(
            [px0 - pad, py0 - pad, px1 + pad, py1 + pad],
            fill=(255, 255, 255),
        )
    return out


def _trace_component(mask: np.ndarray) -> list[tuple[int, int]]:
    h, w = mask.shape
    centers_by_x = [_cluster_runs(mask[:, x]) for x in range(w)]
    valid_cols = [x for x, centers in enumerate(centers_by_x) if centers]
    if not valid_cols:
        return []

    seed_x = min(valid_cols)
    seed_choices = centers_by_x[seed_x]
    seed_y = max(seed_choices)  # lowest voltage = largest pixel y
    points: list[tuple[int, int]] = [(seed_x, int(round(seed_y)))]
    cur_y = seed_y
    last_dy = 0.0
    max_jump = max(8.0, 0.10 * h)

    for x in range(seed_x + 1, w):
        centers = centers_by_x[x]
        if not centers:
            continue
        scored = []
        for cy in centers:
            dy = cy - cur_y
            continuity = abs(dy)
            # VGS must not decrease materially as Qg increases. In image
            # coordinates this means y should be flat or move upward on the page
            # (dy <= 0), with a few px of raster jitter tolerated.
            direction_penalty = max(0.0, dy - 2.0) * 8.0
            curvature = abs(dy - last_dy) * 0.8
            scored.append((continuity + direction_penalty + curvature, cy, dy))
        score, cy, dy = min(scored, key=lambda item: item[0])
        if abs(cy - cur_y) > max_jump:
            continue
        points.append((x, int(round(cy))))
        last_dy = 0.8 * last_dy + 0.2 * dy
        cur_y = cy
    return points


def _curve_score(points: list[tuple[int, int]], h: int, w: int) -> float:
    if len(points) < max(20, int(0.12 * w)):
        return -1e9
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    x_span = (xs.max() - xs.min()) / max(1.0, w)
    y_span = (ys.max() - ys.min()) / max(1.0, h)
    start_x = xs[0] / max(1.0, w)
    end_x = xs[-1] / max(1.0, w)
    start_y = ys[0] / max(1.0, h)
    end_y = ys[-1] / max(1.0, h)
    dy = np.diff(ys)
    monotone_violation = float(np.mean(np.maximum(0.0, dy - 2.0))) if len(dy) else 0.0
    roughness = float(np.median(np.abs(np.diff(dy)))) if len(dy) > 2 else 0.0
    return (
        8.0 * x_span
        + 5.0 * y_span
        + 3.0 * (1.0 - min(1.0, start_x * 4.0))
        + 3.0 * start_y
        + 2.0 * end_x
        + 2.0 * (1.0 - end_y)
        - 2.0 * monotone_violation
        - 0.05 * roughness
    )


def _candidate_masks(mask: np.ndarray) -> list[np.ndarray]:
    h, w = mask.shape
    n, labels, stats, _centroids = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    out: list[np.ndarray] = []
    for label in range(1, n):
        x, y, bw, bh, area = stats[label]
        if area < max(25, 0.0008 * h * w):
            continue
        if bw < 0.08 * w or bh < 0.08 * h:
            continue
        comp = np.zeros_like(mask)
        comp[labels == label] = 255
        out.append(comp)
    # Also include the whole mask as a fallback for charts whose true curve is
    # split into several nearby stroke components by antialiasing or grid removal.
    out.append(mask)
    return out


def _trace_gate_curve(crop: Image.Image, plot_box: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    rgb = np.asarray(crop.convert("RGB"))
    x0, y0, x1, y1 = plot_box
    roi = rgb[y0 : y1 + 1, x0 : x1 + 1]
    if roi.size == 0:
        return []

    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
    # Include dark black traces and saturated colored traces; exclude light grid/background.
    dark = gray < 115
    colored = (hsv[:, :, 1] > 45) & (hsv[:, :, 2] < 245)
    mask = ((dark | colored).astype(np.uint8)) * 255

    h, w = mask.shape
    # Remove chart frame, long gridlines, and long dimension lines. Gate plateaus are
    # shorter than the full plot width, so this preserves the actual curve.
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(35, int(0.55 * w)), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(35, int(0.55 * h))))
    long_lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN, h_kernel)
    long_lines |= cv2.morphologyEx(mask, cv2.MORPH_OPEN, v_kernel)
    mask = cv2.bitwise_and(mask, cv2.bitwise_not(long_lines))

    # Dark gridlines are sometimes emitted as broken segments rather than one
    # full-width line. Remove rows/columns with broad coverage; real Miller
    # plateaus in this sample stay below this coverage.
    row_frac = (mask > 0).sum(axis=1) / max(1, w)
    col_frac = (mask > 0).sum(axis=0) / max(1, h)
    mask[row_frac > 0.35, :] = 0
    mask[:, col_frac > 0.45] = 0

    # Light cleanup: connect antialias gaps, but keep text glyphs mostly separate.
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )

    candidates: list[tuple[float, list[tuple[int, int]]]] = []
    for candidate_mask in _candidate_masks(mask):
        pts = _trace_component(candidate_mask)
        score = _curve_score(pts, h, w)
        if score > -1e8:
            candidates.append((score, pts))
    if not candidates:
        return []
    _score, points = max(candidates, key=lambda item: item[0])
    return [(x0 + x, y0 + y) for x, y in points]


def _trace_vector_gate_curve(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    scale: float,
    plot_box: tuple[int, int, int, int],
) -> list[tuple[int, int]]:
    """Fallback for very light vector strokes that raster thresholding misses."""
    x0, y0, x1, y1 = plot_box
    px0 = rect.x0 + x0 / scale
    py0 = rect.y0 + y0 / scale
    px1 = rect.x0 + x1 / scale
    py1 = rect.y0 + y1 / scale
    plot_pdf = pymupdf.Rect(px0, py0, px1, py1)
    plot_w = max(1.0, plot_pdf.width)
    plot_h = max(1.0, plot_pdf.height)
    pad = 2.0
    # Some vector gate-charge curves intentionally run above the labelled VGS
    # frame; keep x tight but allow a modest top overshoot for the final drive
    # ramp.
    plot_pad = pymupdf.Rect(px0 - pad, py0 - 0.35 * plot_h, px1 + pad, py1 + pad)
    min_len = 0.035 * math.hypot(plot_w, plot_h)

    points: list[tuple[float, float]] = []
    for drawing in page.get_drawings():
        color = drawing.get("color")
        if color is None:
            continue
        # Keep dark-ish strokes. Vector curves in some Vishay PDFs are not black
        # but are still much darker than the pale grid.
        if max(color) > 0.45:
            continue
        for item in drawing.get("items", []):
            if item[0] != "l":
                continue
            p0, p1 = item[1], item[2]
            ax, ay = float(p0.x), float(p0.y)
            bx, by = float(p1.x), float(p1.y)
            if not (plot_pad.contains((ax, ay)) and plot_pad.contains((bx, by))):
                continue
            dx = abs(bx - ax)
            dy = abs(by - ay)
            length = math.hypot(dx, dy)
            if length < min_len:
                continue
            # Full-span axis/grid rules are not curve data. Short horizontal
            # Miller plateaus are preserved by chaining adjacent segment endpoints.
            if dy < 0.8 and dx > 0.55 * plot_w:
                continue
            if dx < 0.8 and dy > 0.55 * plot_h:
                continue
            points.append((ax, ay))
            points.append((bx, by))

    if len(points) < 4:
        return []

    # De-duplicate nearby endpoints and connect them by charge order. This is
    # enough for the intended fallback: drawing a human-verification overlay when
    # the PDF already stores the curve as exact vector line segments.
    points.sort(key=lambda p: (p[0], p[1]))
    dedup: list[tuple[float, float]] = []
    for px, py in points:
        if dedup and abs(px - dedup[-1][0]) < 0.6 and abs(py - dedup[-1][1]) < 0.6:
            dedup[-1] = ((dedup[-1][0] + px) * 0.5, (dedup[-1][1] + py) * 0.5)
        else:
            dedup.append((px, py))
    out: list[tuple[int, int]] = []
    for px, py in dedup:
        cx, cy = _pdf_to_px(rect, scale, px, py)
        out.append((int(round(cx)), int(round(cy))))
    return out


def _detect_inner_plot_box(crop: Image.Image, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Find the actual axes rectangle inside a looser chart/panel bbox.

    The Vpl chart locator often returns a bbox that includes axis titles and
    nearby panels. Full-curve tracing must start at the plot frame, not at the
    y-axis label text. Detect long grid/frame rules and use their outer extent.
    """
    gray_full = np.asarray(crop.convert("L"))
    fx0, fy0, fx1, fy1 = fallback
    fx0 = max(0, fx0)
    fy0 = max(0, fy0)
    fx1 = min(gray_full.shape[1] - 1, fx1)
    fy1 = min(gray_full.shape[0] - 1, fy1)
    roi = gray_full[fy0 : fy1 + 1, fx0 : fx1 + 1]
    if roi.shape[0] < 40 or roi.shape[1] < 40:
        return fallback

    _, bw = cv2.threshold(roi, 238, 255, cv2.THRESH_BINARY_INV)
    h, w = bw.shape
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, int(0.28 * w)), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, int(0.28 * h))))
    h_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, v_kernel)

    def centers(img: np.ndarray, orient: str) -> list[float]:
        contours, _ = cv2.findContours(img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        vals: list[float] = []
        for contour in contours:
            x, y, ww, hh = cv2.boundingRect(contour)
            if orient == "h":
                if ww < 0.35 * w or hh > 10:
                    continue
                # Ignore title separators outside the plot; real grid/frame
                # lines lie well inside the loose chart bbox.
                if y < 0.01 * h or y > 0.99 * h:
                    continue
                vals.append(y + 0.5 * hh)
            else:
                if hh < 0.35 * h or ww > 10:
                    continue
                if x < 0.01 * w or x > 0.99 * w:
                    continue
                vals.append(x + 0.5 * ww)
        vals.sort()
        grouped: list[list[float]] = []
        for val in vals:
            if grouped and val - grouped[-1][-1] <= 4:
                grouped[-1].append(val)
            else:
                grouped.append([val])
        return [float(np.median(group)) for group in grouped]

    hs = centers(h_lines, "h")
    vs = centers(v_lines, "v")
    if len(hs) < 2 or len(vs) < 2:
        return fallback

    # A loose bbox may contain stacked plots. Split horizontal grid/frame lines
    # into plot bands and use the lowest band; gate-charge plots often sit below
    # an Rds/on-state-resistance plot in NXP datasheets.
    h_groups: list[list[float]] = [[hs[0]]]
    diffs = np.diff(hs)
    typical_gap = float(np.median(diffs[diffs > 2])) if np.any(diffs > 2) else 0.0
    split_gap = max(26.0, 2.2 * typical_gap)
    for val in hs[1:]:
        if val - h_groups[-1][-1] > split_gap:
            h_groups.append([val])
        else:
            h_groups[-1].append(val)
    plausible_h_groups = [group for group in h_groups if len(group) >= 2]
    if plausible_h_groups:
        hs = plausible_h_groups[-1]

    # A loose bbox may also contain side-by-side plots. Split vertical
    # grid/frame lines into plot columns and use the rightmost plausible band;
    # gate-charge charts in these multi-plot panels are commonly the right
    # column. This also avoids tracing from a neighbouring transfer/diode plot.
    v_groups: list[list[float]] = [[vs[0]]]
    v_diffs = np.diff(vs)
    v_typical_gap = float(np.median(v_diffs[v_diffs > 2])) if np.any(v_diffs > 2) else 0.0
    v_split_gap = max(32.0, 2.4 * v_typical_gap)
    for val in vs[1:]:
        if val - v_groups[-1][-1] > v_split_gap:
            v_groups.append([val])
        else:
            v_groups[-1].append(val)
    plausible_v_groups = [group for group in v_groups if len(group) >= 2]
    if plausible_v_groups:
        vs = plausible_v_groups[-1]

    x0 = int(round(fx0 + min(vs)))
    x1 = int(round(fx0 + max(vs)))
    y0 = int(round(fy0 + min(hs)))
    y1 = int(round(fy0 + max(hs)))
    if x1 - x0 < 0.35 * (fx1 - fx0) or y1 - y0 < 0.35 * (fy1 - fy0):
        return fallback
    return (x0, y0, x1, y1)


def _smooth_polyline(points: list[tuple[int, int]], stride: int = 4) -> list[tuple[int, int]]:
    if len(points) < 5:
        return points
    out = []
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    for i in range(len(points)):
        lo = max(0, i - 3)
        hi = min(len(points), i + 4)
        out.append((int(round(xs[i])), int(round(np.median(ys[lo:hi])))))
    return out[::stride]


def _v_from_y_pixel(chart, rect: pymupdf.Rect, scale: float, y_px: float) -> float | None:
    ticks = getattr(chart, "y_ticks", None) or []
    if len(ticks) < 2:
        return None
    vals = np.array([float(v) for v, _ in ticks], dtype=float)
    ys_pdf = np.array([float(y) for _, y in ticks], dtype=float)
    if np.nanmax(vals) <= 0:
        vals = -vals
    m, b = np.polyfit(ys_pdf, vals, 1)
    y_pdf = rect.y0 + float(y_px) / scale
    return float(m * y_pdf + b)


def _parse_numeric_label(text: str) -> float | None:
    text = text.strip().replace("−", "-").replace("–", "-")
    text = text.replace("O", "0").replace("o", "0")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _local_y_ticks_for_plot(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    scale: float,
    plot_box: tuple[int, int, int, int],
) -> list[tuple[float, float]]:
    """Read y-axis tick labels next to the detected inner plot frame.

    ``dslib.viz`` may return a loose chart bbox spanning stacked plots; in
    that case ``chart.y_ticks`` can belong to the wrong panel. The overlay
    estimator uses the inner plot frame, so calibrate from text labels whose
    centres sit directly to the left/right of that frame.
    """
    x0, y0, x1, y1 = plot_box
    px0 = rect.x0 + x0 / scale
    py0 = rect.y0 + y0 / scale
    px1 = rect.x0 + x1 / scale
    py1 = rect.y0 + y1 / scale
    height = max(1.0, py1 - py0)
    candidates: list[tuple[float, float]] = []
    try:
        words = page.get_text("words")
    except Exception:
        return candidates
    for word in words:
        wx0, wy0, wx1, wy1 = [float(v) for v in word[:4]]
        text = str(word[4])
        cx = 0.5 * (wx0 + wx1)
        cy = 0.5 * (wy0 + wy1)
        if cy < py0 - 0.04 * height or cy > py1 + 0.04 * height:
            continue
        # Tick labels may be on either side for dual-axis charts. Keep labels
        # close to the frame, reject axis titles and in-chart annotations.
        left_band = px0 - 82 <= cx <= px0 - 1
        right_band = px1 + 1 <= cx <= px1 + 82
        if not (left_band or right_band):
            continue
        value = _parse_numeric_label(text)
        if value is None:
            continue
        # MOSFET VGS chart labels in this benchmark are small integer volts;
        # this rejects figure numbers, caption numbers, and charge labels.
        if value < -20 or value > 30:
            continue
        candidates.append((value, cy))

    # De-duplicate split glyphs / nearby labels by y position.
    candidates.sort(key=lambda item: item[1])
    grouped: list[list[tuple[float, float]]] = []
    for item in candidates:
        if grouped and abs(item[1] - grouped[-1][-1][1]) <= 3.0:
            grouped[-1].append(item)
        else:
            grouped.append([item])
    ticks: list[tuple[float, float]] = []
    for group in grouped:
        # Prefer the numerically smallest absolute text in the group when a
        # minus sign was split; otherwise median is fine for duplicate sides.
        values = [g[0] for g in group]
        ys = [g[1] for g in group]
        ticks.append((float(np.median(values)), float(np.median(ys))))
    if len(ticks) < 2:
        return []

    ticks.sort(key=lambda item: item[1])
    vals = np.array([v for v, _ in ticks], dtype=float)
    if vals[0] < vals[-1]:
        return []
    # Reject axis sets that are probably logarithmic current/capacitance
    # labels, not VGS volts.
    if len(vals) >= 3:
        diffs = np.diff(vals)
        if np.any(diffs >= 0):
            return []
        med = float(np.median(np.abs(diffs)))
        if med > 0 and float(np.max(np.abs(np.abs(diffs) - med))) > max(1.5, 0.45 * med):
            return []
    return ticks


def _text_near_rect(page: pymupdf.Page, bbox: pymupdf.Rect, pad: float = 26.0) -> str:
    rect = (bbox + (-pad, -pad, pad, pad)) & page.rect
    parts: list[str] = []
    try:
        words = page.get_text("words")
    except Exception:
        return ""
    for word in words:
        x0, y0, x1, y1 = [float(v) for v in word[:4]]
        cx = 0.5 * (x0 + x1)
        cy = 0.5 * (y0 + y1)
        if rect.contains((cx, cy)):
            parts.append(str(word[4]))
    return " ".join(parts)


def _reject_non_gate_context(text: str, *, broad: bool = False) -> bool:
    tl = re.sub(r"\s+", " ", text.lower())
    absolute_stop = (
        "source-drain diode",
        "source drain diode",
        "source- drain diode",
        "source - drain diode",
        "source-drain voltage",
        "source drain voltage",
        "reverse drain current",
        "diode forward",
        "reverse diode",
        "vsd source-drain",
        "vsd source drain",
    )
    if any(stop in tl for stop in absolute_stop):
        return True
    table_stop = ("test condition", "min", "typ", "max", "unit", "symbol")
    if "gate charge" in tl or "qg" in tl or "qgate" in tl:
        if all(tok in tl for tok in table_stop):
            return True
        return False
    return broad and all(tok in tl for tok in table_stop)


def _reject_ambiguous_broad_context(tight_text: str, broad_text: str) -> bool:
    if tight_text.strip():
        return False
    tl = re.sub(r"\s+", " ", broad_text.lower())
    return ("reverse drain current" in tl and ("source-drain" in tl or "source drain" in tl))


def _v_from_local_ticks(y_ticks: list[tuple[float, float]], y_px: float, rect: pymupdf.Rect, scale: float) -> float | None:
    if len(y_ticks) < 2:
        return None
    vals = np.array([float(v) for v, _ in y_ticks], dtype=float)
    ys_pdf = np.array([float(y) for _, y in y_ticks], dtype=float)
    if np.ptp(vals) < 1e-9 or np.ptp(ys_pdf) < 1e-9:
        return None
    m, b = np.polyfit(ys_pdf, vals, 1)
    y_pdf = rect.y0 + float(y_px) / scale
    return float(m * y_pdf + b)


def _y_pixel_from_local_ticks(y_ticks: list[tuple[float, float]], v: float, rect: pymupdf.Rect, scale: float) -> float | None:
    if len(y_ticks) < 2:
        return None
    vals = np.array([float(val) for val, _ in y_ticks], dtype=float)
    ys_pdf = np.array([float(y) for _, y in y_ticks], dtype=float)
    if np.ptp(vals) < 1e-9 or np.ptp(ys_pdf) < 1e-9:
        return None
    m, b = np.polyfit(vals, ys_pdf, 1)
    y_pdf = float(m * float(v) + b)
    return (y_pdf - rect.y0) * scale


def _v_from_plot_axis(plot_box: tuple[int, int, int, int], y_px: float, v_min: float, v_max: float) -> float:
    _x0, y0, _x1, y1 = plot_box
    return float(v_min + (v_max - v_min) * (y1 - y_px) / max(1.0, y1 - y0))


def _line_sse(xs: np.ndarray, ys: np.ndarray, i: int, j: int) -> float:
    if j - i < 2:
        return 1e18
    x = xs[i:j]
    y = ys[i:j]
    if np.ptp(x) < 1e-9:
        return 1e18
    m, b = np.polyfit(x, y, 1)
    r = y - (m * x + b)
    return float(np.dot(r, r))


def _middle_slope_y(points: list[tuple[int, int]], plot_box: tuple[int, int, int, int]) -> float | None:
    """Mean y of the Miller-slope section from a 3-line gate-charge fit."""
    if len(points) < 30:
        return None
    x0, _y0, x1, _y1 = plot_box
    pts = sorted(points)
    xs = np.array([p[0] for p in pts], dtype=float)
    ys = np.array([p[1] for p in pts], dtype=float)
    n = len(xs)
    min_len = max(8, n // 12)
    best: tuple[float, int, int] | None = None
    # Keep the middle segment in the plausible Miller region, not the first few
    # pixels or the final gate-drive tail.
    for i in range(min_len, n - 2 * min_len):
        rel_i = (xs[i] - x0) / max(1.0, x1 - x0)
        if rel_i < 0.05 or rel_i > 0.60:
            continue
        for j in range(i + min_len, n - min_len):
            rel_j = (xs[j] - x0) / max(1.0, x1 - x0)
            if rel_j < 0.18 or rel_j > 0.88:
                continue
            cost = _line_sse(xs, ys, 0, i) + _line_sse(xs, ys, i, j) + _line_sse(xs, ys, j, n)
            if best is None or cost < best[0]:
                best = (cost, i, j)
    if best is None:
        return None
    _cost, i, j = best
    if j - i < min_len:
        return None
    return float(np.mean(ys[i:j]))


def _estimate_vpl_from_curve(
    curve: list[tuple[int, int]],
    chart,
    rect: pymupdf.Rect,
    scale: float,
    plot_box: tuple[int, int, int, int],
    local_y_ticks: list[tuple[float, float]] | None = None,
) -> tuple[float | None, float | None]:
    """Return (Vpl, y_px) from the traced VGS(Qg) curve.

    Pick the longest/strongest low-slope window after the initial ramp and
    before the final drive ramp. This deliberately ignores the old scalar
    plateau detector so the overlay validates the full-curve digitizer itself.
    """
    if len(curve) < 20:
        return None, None
    x0, y0, x1, y1 = plot_box
    pts = sorted(curve)
    xs = np.array([p[0] for p in pts], dtype=float)
    ys = np.array([p[1] for p in pts], dtype=float)
    span = max(1.0, xs[-1] - xs[0])
    middle_y = _middle_slope_y(pts, plot_box)
    win = max(8, int(0.07 * len(pts)))
    candidates: list[tuple[float, float, float, float, int, int]] = []
    for i in range(0, len(pts) - win):
        j = i + win
        x_mid = 0.5 * (xs[i] + xs[j - 1])
        rel_x = (x_mid - x0) / max(1.0, x1 - x0)
        if rel_x < 0.04 or rel_x > 0.78:
            continue
        yr = float(np.percentile(ys[i:j], 90) - np.percentile(ys[i:j], 10))
        xr = float(xs[j - 1] - xs[i])
        before = ys[:i]
        after = ys[j:]
        if len(before) < 4 or len(after) < 4:
            continue
        # Need visible charge ramp before and after: image y decreases as VGS rises.
        pre_rise = float(np.percentile(before, 90) - np.median(ys[i:j]))
        post_rise = float(np.median(ys[i:j]) - np.percentile(after, 10))
        if pre_rise < 0.05 * (y1 - y0):
            continue
        if post_rise < 0.05 * (y1 - y0):
            continue
        flatness = yr / max(1.0, y1 - y0)
        xfrac = xr / max(1.0, x1 - x0)
        # Prefer the earliest bracketing flat section. A later drive ramp can
        # produce many low-slope windows, but it is not the Miller plateau.
        score = 3.0 * xfrac / (flatness + 0.01) + min(pre_rise, post_rise) / max(1.0, y1 - y0) - 8.0 * rel_x
        candidates.append((score, rel_x, float(np.median(ys[i:j])), yr, i, j))
    if not candidates:
        if middle_y is None:
            return None, None
        y_px = middle_y
        if local_y_ticks:
            local_v = _v_from_local_ticks(local_y_ticks, y_px, rect, scale)
            if local_v is not None:
                return local_v, y_px
        return _v_from_y_pixel(chart, rect, scale, y_px), y_px
    # Do not let a marginally higher score on the final ramp beat an earlier
    # true plateau. Among plausible candidates, take the leftmost one whose
    # score is within 70% of the best.
    best_score = max(c[0] for c in candidates)
    plausible = [c for c in candidates if c[0] >= 0.70 * best_score]
    if not plausible:
        plausible = candidates
    _score, _rel_x, y_px, candidate_yr, _i, _j = min(plausible, key=lambda c: c[1])
    # If the middle segment is visibly sloped, use its average level rather
    # than the earliest low-slope point. Do not override a genuinely flat
    # Miller plateau, as dynamic input/output charts often have a true flat VGS
    # segment and an unrelated steep VDS trace in the same plot.
    if (
        middle_y is not None
        and candidate_yr > 0.010 * max(1, y1 - y0)
        and abs(middle_y - y_px) > 0.035 * max(1, y1 - y0)
    ):
        y_px = middle_y
    if local_y_ticks:
        local_v = _v_from_local_ticks(local_y_ticks, y_px, rect, scale)
        if local_v is not None:
            return local_v, y_px
    return _v_from_y_pixel(chart, rect, scale, y_px), y_px


def _save_sheet(images: list[Image.Image], path: Path) -> None:
    width = max(im.width for im in images)
    height = sum(im.height for im in images) + 16 * (len(images) - 1)
    sheet = Image.new("RGB", (width, height), "white")
    y = 0
    for im in images:
        sheet.paste(im, (0, y))
        y += im.height + 16
    sheet.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Digitize MOSFET gate-charge curves and estimate Vpl.")
    parser.add_argument(
        "pdfs",
        nargs="*",
        help="Optional datasheet PDFs to process. Relative paths are resolved under --datasheet-root/datasheets.",
    )
    parser.add_argument(
        "--datasheet-root",
        type=Path,
        default=DEFAULT_DATASHEET_ROOT,
        help="pwr-mosfet-lib checkout containing datasheets/ and dslib. Defaults to Fab's local checkout.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Output directory for overlays/contact sheets.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help="PDF render DPI for raster fallback and overlays.",
    )
    parser.add_argument("--start", type=int, default=None, help="1-based start index in docs/vibes/chart-extraction.md")
    parser.add_argument("--count", type=int, default=None, help="number of chart-extraction.md samples")
    parser.add_argument(
        "--chart-extraction-md",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--reference-assisted",
        action="store_true",
        help="Use human reference Vpl values to choose among ambiguous chart/estimator candidates. "
        "This is only for legacy overlay audits, not normal digitization.",
    )
    args = parser.parse_args()
    root = args.datasheet_root
    out = args.out
    dpi = args.dpi
    chart_extraction_md = args.chart_extraction_md or root / "docs/vibes/chart-extraction.md"

    samples = SAMPLES
    batch_name = None
    if args.pdfs:
        samples = [(pdf, None, "") for pdf in args.pdfs]
        batch_name = f"pdfs_{len(samples)}"
    elif args.start is not None or args.count is not None:
        start = args.start or 1
        count = args.count or 25
        samples = _samples_from_chart_extraction(chart_extraction_md, start, count)
        batch_name = f"chart_extraction_{start}_{start + len(samples) - 1}"

    sys.path.insert(0, str(root))
    try:
        from dslib.viz import find_in_pdf
    except ModuleNotFoundError as exc:
        if exc.name != "dslib":
            raise
        raise SystemExit(
            "digitize-vpl currently requires pwr-mosfet-lib's dslib chart finder. "
            f"Pass --datasheet-root pointing at a checkout that contains dslib/ (got {root})."
        ) from exc

    out.mkdir(parents=True, exist_ok=True)
    images = []
    rows = []
    had_errors = False

    for rel, ref_vpl, comment in samples:
        pdf = _sample_pdf_path(root, rel)
        mpn = pdf.stem
        if ref_vpl is None and not args.pdfs:
            img = Image.new("RGB", (900, 180), "white")
            text = f"{mpn}: no human Vpl reference"
            if comment:
                text += f" ({comment})"
            ImageDraw.Draw(img).text((12, 20), text, fill=(0, 0, 0), font=_font(18))
            images.append(img)
            rows.append((mpn, out / f"{mpn}.fullcurve.overlay.png", 0, text))
            continue
        if not pdf.exists():
            img = Image.new("RGB", (900, 180), "white")
            ImageDraw.Draw(img).text((12, 20), f"{mpn}: missing", fill=(0, 0, 0), font=_font(18))
            images.append(img)
            rows.append((mpn, out / f"{mpn}.fullcurve.overlay.png", 0, "missing"))
            if args.pdfs:
                had_errors = True
            continue

        hits = find_in_pdf(str(pdf), enable_raster=True, enable_ocr=False)
        usable = [(c, h, s) for c, h, s in hits if h is not None]
        if usable:
            filtered = []
            doc_filter = pymupdf.open(str(pdf))
            try:
                for c, h, s in usable:
                    page_filter = doc_filter[c.page_num]
                    tight_ctx = _text_near_rect(page_filter, c.bbox, pad=4.0)
                    broad_ctx = _text_near_rect(page_filter, c.bbox, pad=90.0)
                    if not _reject_non_gate_context(tight_ctx) and not _reject_ambiguous_broad_context(tight_ctx, broad_ctx):
                        filtered.append((c, h, s))
                usable = filtered
            finally:
                doc_filter.close()
        if usable and ref_vpl is not None and args.reference_assisted:
            # Legacy verification mode: several datasheets contain more than
            # one gate-charge-like plot, so human-audit runs can request the
            # chart closest to the known reference. Normal digitization must
            # not use the reference value to choose the output.
            chart, hit, source = min(
                usable,
                key=lambda t: (
                    abs(float(getattr(t[1], "v_pl", float("inf"))) - ref_vpl),
                    -float(getattr(t[1], "score", 0.0)),
                ),
            )
        elif usable:
            chart, hit, source = max(usable, key=lambda t: getattr(t[1], "score", 0.0))
        else:
            img = Image.new("RGB", (900, 180), "white")
            ImageDraw.Draw(img).text((12, 20), f"{mpn}: no Vpl chart hit", fill=(0, 0, 0), font=_font(18))
            images.append(img)
            rows.append((mpn, out / f"{mpn}.fullcurve.overlay.png", 0, "no Vpl chart hit"))
            if args.pdfs:
                had_errors = True
            continue

        doc = pymupdf.open(str(pdf))
        try:
            page = doc[chart.page_num]
            rect = (chart.bbox + (-46, -38, 42, 52)) & page.rect
            scale = dpi / 72.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False)
            crop = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            trace_crop = _mask_page_text(crop, page, rect, chart.bbox, scale)

            bx0, by0 = _pdf_to_px(rect, scale, chart.bbox.x0, chart.bbox.y0)
            bx1, by1 = _pdf_to_px(rect, scale, chart.bbox.x1, chart.bbox.y1)
            loose_plot_box = (int(round(bx0)), int(round(by0)), int(round(bx1)), int(round(by1)))
            plot_box = _detect_inner_plot_box(crop, loose_plot_box)
            local_y_ticks = _local_y_ticks_for_plot(page, rect, scale, plot_box)
            # If the detected frame starts at an interior gridline, tracing
            # loses the initial VGS ramp. Expand left to the loose bbox when it
            # is close enough to be the real axis rather than a stacked-panel
            # wrapper. This is common in simple ST-style vector plots.
            if (
                (source or "").startswith("vector")
                and plot_box[0] - loose_plot_box[0] > 10
                and plot_box[0] - loose_plot_box[0] < 0.22 * max(1, loose_plot_box[2] - loose_plot_box[0])
            ):
                plot_box = (loose_plot_box[0], plot_box[1], plot_box[2], plot_box[3])
            curve = _smooth_polyline(_trace_gate_curve(trace_crop, plot_box))
            if len(curve) < 10 and (source or "").startswith("vector"):
                curve = _smooth_polyline(_trace_vector_gate_curve(page, rect, scale, plot_box), stride=1)
        finally:
            doc.close()

        loose_w0 = max(1, loose_plot_box[2] - loose_plot_box[0])
        loose_h0 = max(1, loose_plot_box[3] - loose_plot_box[1])
        plot_w0 = max(1, plot_box[2] - plot_box[0])
        plot_h0 = max(1, plot_box[3] - plot_box[1])
        visual_plot_box = plot_box
        # Some vector PDFs expose only interior gridlines to the line detector
        # because the true frame is broken by titles or lies on the loose bbox
        # edge. Keep the inner box for tracing/calibration, but draw and crop
        # against the outer frame so verification overlays show the full chart.
        if (
            (source or "").startswith("vector")
            and plot_h0 < 0.75 * loose_h0
            and plot_box[1] - loose_plot_box[1] > 0.08 * loose_h0
            and loose_plot_box[3] - plot_box[3] > 0.08 * loose_h0
        ):
            visual_plot_box = loose_plot_box

        draw = ImageDraw.Draw(crop)
        draw.rectangle(list(loose_plot_box), outline=(255, 220, 128), width=2)
        draw.rectangle(list(visual_plot_box), outline=(255, 176, 0), width=3)
        if len(curve) >= 2:
            draw.line(curve, fill=(20, 90, 255), width=5, joint="curve")
            for x, y in curve[:: max(1, len(curve) // 35)]:
                draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(0, 40, 220))

        old_est = getattr(hit, "v_pl", None) if hit is not None else None
        curve_est, y_curve_vpl = _estimate_vpl_from_curve(curve, chart, rect, scale, plot_box, local_y_ticks)
        loose_h = max(1, loose_plot_box[3] - loose_plot_box[1])
        stacked_inner_plot = (plot_box[1] - loose_plot_box[1]) > 0.25 * loose_h
        est_source = "dslib"
        if ref_vpl is not None and args.reference_assisted:
            candidates: list[tuple[float, float | None, str]] = []
            if isinstance(old_est, (int, float)):
                y_pdf = getattr(hit, "y_pdf", None) if hit is not None else None
                y_old = _pdf_to_px(rect, scale, chart.bbox.x0, float(y_pdf))[1] if y_pdf is not None else None
                candidates.append((float(old_est), y_old, "dslib"))
            if curve_est is not None and y_curve_vpl is not None:
                candidates.append((float(curve_est), y_curve_vpl, "curve"))
                candidates.append((_v_from_plot_axis(plot_box, y_curve_vpl, -5.0, 20.0), y_curve_vpl, "curve/axis_-5_20"))
                candidates.append((_v_from_plot_axis(plot_box, y_curve_vpl, 0.0, 15.0), y_curve_vpl, "curve/axis_0_15"))
            if candidates:
                est, y_vpl, est_source = min(candidates, key=lambda item: abs(item[0] - ref_vpl))
            else:
                est, y_vpl, est_source = None, None, "none"
        elif curve_est is not None and y_curve_vpl is not None:
            est = curve_est
            y_vpl = y_curve_vpl
            est_source = "curve"
        elif stacked_inner_plot and curve_est is not None:
            est = curve_est
            y_vpl = y_curve_vpl
            est_source = "curve/local-axis"
        elif isinstance(old_est, (int, float)):
            est = old_est
            y_pdf = getattr(hit, "y_pdf", None) if hit is not None else None
            y_vpl = _pdf_to_px(rect, scale, chart.bbox.x0, float(y_pdf))[1] if y_pdf is not None else None
        else:
            est = curve_est
            y_vpl = y_curve_vpl
            est_source = "curve"
        est_s = f"{est:.2f}" if isinstance(est, (int, float)) else "none"
        err_s = f"{est - ref_vpl:+.2f}" if isinstance(est, (int, float)) and ref_vpl is not None else ""
        ok = isinstance(est, (int, float)) and ref_vpl is not None and abs(est - ref_vpl) <= 0.5
        guide_color = (20, 170, 40) if ok else (255, 40, 40)
        if y_vpl is not None:
            draw.line([(visual_plot_box[0], y_vpl), (visual_plot_box[2], y_vpl)], fill=guide_color, width=2)
        old_s = f" old={old_est:.2f}" if isinstance(old_est, (int, float)) and est is not None and abs(old_est - est) > 0.05 else ""
        ref_s = f"{ref_vpl:.2f}" if ref_vpl is not None else "n/a"
        label = f"{mpn}  ref={ref_s}  Vpl={est_s} {err_s}{old_s}  chart={source or '-'}  vpl_src={est_source}  curve_pts={len(curve)}"
        if local_y_ticks:
            label += f"  ytick=local/{len(local_y_ticks)}"
        if comment:
            label += f"  ({comment})"
        loose_h = max(1, loose_plot_box[3] - loose_plot_box[1])
        plot_h = max(1, visual_plot_box[3] - visual_plot_box[1])
        loose_w = max(1, loose_plot_box[2] - loose_plot_box[0])
        plot_w = max(1, visual_plot_box[2] - visual_plot_box[0])
        if loose_h < 1.45 * plot_h and loose_w < 1.65 * plot_w:
            px0 = min(visual_plot_box[0], loose_plot_box[0])
            py0 = min(visual_plot_box[1], loose_plot_box[1])
            px1 = max(visual_plot_box[2], loose_plot_box[2])
            py1 = max(visual_plot_box[3], loose_plot_box[3])
        else:
            px0, py0, px1, py1 = visual_plot_box
        display_box = (
            max(0, px0 - 90),
            max(0, py0 - 44),
            min(crop.width, px1 + 58),
            min(crop.height, py1 + 110),
        )
        crop = crop.crop(display_box)
        pad_h = 58
        annotated = Image.new("RGB", (crop.width, crop.height + pad_h), "white")
        annotated.paste(crop, (0, pad_h))
        ImageDraw.Draw(annotated).text((8, 8), label, fill=(0, 0, 0), font=_font(15))

        out_path = out / f"{mpn}.fullcurve.overlay.png"
        annotated.save(out_path)
        images.append(annotated)
        rows.append((mpn, out_path, len(curve), label))

    if batch_name:
        batch_path = out / f"{batch_name}_fullcurve_contact_sheet.png"
        _save_sheet(images, batch_path)
    else:
        first5_path = out / "first5_fullcurve_contact_sheet.png"
        next10_path = out / "next10_fullcurve_contact_sheet.png"
        all15_path = out / "all15_fullcurve_contact_sheet.png"
        _save_sheet(images[:5], first5_path)
        _save_sheet(images[5:15], next10_path)
        _save_sheet(images, all15_path)

    for mpn, path, npts, label in rows:
        print(f"{label}: {path}")
    if batch_name:
        print(f"BATCH_CONTACT_SHEET: {batch_path}")
    else:
        print(f"FIRST5_CONTACT_SHEET: {first5_path}")
        print(f"NEXT10_CONTACT_SHEET: {next10_path}")
        print(f"ALL15_CONTACT_SHEET: {all15_path}")
    return 1 if had_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
