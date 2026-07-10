from __future__ import annotations

import math

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw


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
    """Erase text whose bbox centre is inside the plot area."""
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
    seed_y = max(seed_choices)
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
        _x, _y, bw, bh, area = stats[label]
        if area < max(25, 0.0008 * h * w):
            continue
        if bw < 0.08 * w or bh < 0.08 * h:
            continue
        comp = np.zeros_like(mask)
        comp[labels == label] = 255
        out.append(comp)
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
    dark = gray < 115
    colored = (hsv[:, :, 1] > 45) & (hsv[:, :, 2] < 245)
    mask = ((dark | colored).astype(np.uint8)) * 255

    h, w = mask.shape
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(35, int(0.55 * w)), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(35, int(0.55 * h))))
    long_lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN, h_kernel)
    long_lines |= cv2.morphologyEx(mask, cv2.MORPH_OPEN, v_kernel)
    mask = cv2.bitwise_and(mask, cv2.bitwise_not(long_lines))

    row_frac = (mask > 0).sum(axis=1) / max(1, w)
    col_frac = (mask > 0).sum(axis=0) / max(1, h)
    mask[row_frac > 0.35, :] = 0
    mask[:, col_frac > 0.45] = 0

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
    plot_pad = pymupdf.Rect(px0 - pad, py0 - 0.35 * plot_h, px1 + pad, py1 + pad)
    min_len = 0.035 * math.hypot(plot_w, plot_h)

    points: list[tuple[float, float]] = []
    for drawing in page.get_drawings():
        color = drawing.get("color")
        if color is None:
            continue
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
            if dy < 0.8 and dx > 0.55 * plot_w:
                continue
            if dx < 0.8 and dy > 0.55 * plot_h:
                continue
            points.append((ax, ay))
            points.append((bx, by))

    if len(points) < 4:
        return []

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
    """Find the actual axes rectangle inside a looser chart/panel bbox."""
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
