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


def _trace_gate_curve(
    crop: Image.Image,
    plot_box: tuple[int, int, int, int],
    *,
    gray_threshold: int = 115,
) -> list[tuple[int, int]]:
    rgb = np.asarray(crop.convert("RGB"))
    x0, y0, x1, y1 = plot_box
    roi = rgb[y0 : y1 + 1, x0 : x1 + 1]
    if roi.size == 0:
        return []

    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
    dark = gray < gray_threshold
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


def _connected_segment_components(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    tolerance: float = 1.5,
) -> list[list[tuple[tuple[float, float], tuple[float, float]]]]:
    """Group vector strokes by shared endpoints without bridging nearby curves."""

    remaining = set(range(len(segments)))
    components: list[list[tuple[tuple[float, float], tuple[float, float]]]] = []
    while remaining:
        pending = [remaining.pop()]
        indexes: list[int] = []
        while pending:
            index = pending.pop()
            indexes.append(index)
            endpoints = segments[index]
            joined = [
                other
                for other in remaining
                if min(
                    math.hypot(ax - bx, ay - by)
                    for ax, ay in endpoints
                    for bx, by in segments[other]
                )
                <= tolerance
            ]
            for other in joined:
                remaining.remove(other)
                pending.append(other)
        components.append([segments[index] for index in indexes])
    return components


def _densify_vector_component(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    rect: pymupdf.Rect,
    scale: float,
    *,
    reducer: str = "median",
) -> list[tuple[int, int]]:
    """Sample connected vector segments at approximately one-pixel cadence."""

    points_by_x: dict[int, list[int]] = {}
    for (ax, ay), (bx, by) in segments:
        x0, y0 = _pdf_to_px(rect, scale, ax, ay)
        x1, y1 = _pdf_to_px(rect, scale, bx, by)
        steps = max(1, int(math.ceil(max(abs(x1 - x0), abs(y1 - y0)))))
        for index in range(steps + 1):
            fraction = index / steps
            x = int(round(x0 + fraction * (x1 - x0)))
            y = int(round(y0 + fraction * (y1 - y0)))
            points_by_x.setdefault(x, []).append(y)
    if reducer == "minimum":
        return [(x, min(ys)) for x, ys in sorted(points_by_x.items())]
    if reducer != "median":
        raise ValueError(f"unsupported vector reducer: {reducer}")
    return [(x, int(round(float(np.median(ys))))) for x, ys in sorted(points_by_x.items())]


def _terminal_bundle_upper_branch(
    median: list[tuple[int, int]],
    upper: list[tuple[int, int]],
    plot_box: tuple[int, int, int, int],
) -> list[tuple[int, int]]:
    """Follow one upper terminal branch instead of median-switching a bundle."""

    if len(median) != len(upper) or len(median) < 20:
        return median
    x0, y0, x1, y1 = plot_box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    bundle_start = x0 + 0.55 * width
    material_spread = max(5.0, 0.03 * height)
    bundled_columns = sum(
        1
        for (mx, my), (ux, uy) in zip(median, upper, strict=True)
        if mx == ux and mx >= bundle_start and my - uy >= material_spread
    )
    if bundled_columns < max(8, int(round(0.04 * width))):
        return median

    # The upper envelope follows the first/leftmost VDS branch. Once that
    # branch terminates, the envelope jumps down to a neighboring branch;
    # stop before that source-discontinuous switch rather than splicing them.
    termination_jump = max(5.0, 0.04 * height)
    for index in range(1, len(upper)):
        x, y = upper[index]
        if x < bundle_start:
            continue
        if y - upper[index - 1][1] >= termination_jump:
            return upper[:index]
    return upper


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

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
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
            segments.append(((ax, ay), (bx, by)))

    if not segments:
        return []
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    candidates: list[tuple[float, list[tuple[int, int]]]] = []
    for component in _connected_segment_components(segments):
        median_points = _densify_vector_component(component, rect, scale)
        upper_points = _densify_vector_component(
            component, rect, scale, reducer="minimum"
        )
        points = _terminal_bundle_upper_branch(median_points, upper_points, plot_box)
        point_xs = [x for x, _y in points]
        point_ys = [y for _x, y in points]
        if np.ptp(point_xs) < 0.30 * width or np.ptp(point_ys) < 0.20 * height:
            continue
        local = [(x - x0, y - y0) for x, y in points]
        score = _curve_score(local, height, width)
        if score > -1e8:
            candidates.append((score, points))
    return max(candidates, key=lambda item: item[0])[1] if candidates else []


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

    h_components, v_components = _axis_line_components(roi)
    h, w = roi.shape

    def centers(components: list[tuple[int, int, int, int]], orient: str) -> list[float]:
        vals: list[float] = []
        for x, y, ww, hh in components:
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

    hs = centers(h_components, "h")
    vs = centers(v_components, "v")
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


def _axis_line_components(gray: np.ndarray) -> tuple[
    list[tuple[int, int, int, int]], list[tuple[int, int, int, int]]
]:
    """Return horizontal and vertical long-stroke component rectangles."""

    _, bw = cv2.threshold(gray, 238, 255, cv2.THRESH_BINARY_INV)
    h, w = bw.shape
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, int(0.22 * w)), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, int(0.22 * h))))
    h_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, v_kernel)

    def components(image: np.ndarray) -> list[tuple[int, int, int, int]]:
        contours, _ = cv2.findContours(image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return [cv2.boundingRect(contour) for contour in contours]

    return components(h_lines), components(v_lines)


def _detect_aligned_plot_frame(
    gray: np.ndarray,
    fallback: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    """Find a closed plot frame from aligned grid endpoints.

    The search covers the rendered crop rather than only ``fallback`` because
    OCR/finder bboxes can end at the last readable tick while the real frame
    extends one unlabeled interval farther.  ``fallback`` remains the locality
    anchor used to reject a neighbouring panel.
    """

    height, width = gray.shape
    h_components, v_components = _axis_line_components(gray)
    min_h_width = 0.25 * width
    horizontal = [
        component
        for component in h_components
        if component[2] >= min_h_width and component[3] <= 10
    ]
    vertical = [
        component
        for component in v_components
        if component[3] >= 0.25 * height and component[2] <= 10
    ]
    if len(horizontal) < 2 or len(vertical) < 2:
        return None

    span_tolerance = max(6, int(round(0.012 * width)))
    groups: list[list[tuple[int, int, int, int]]] = []
    for component in sorted(horizontal, key=lambda item: (item[0], item[0] + item[2])):
        start = component[0]
        end = component[0] + component[2] - 1
        for group in groups:
            group_starts = [item[0] for item in group]
            group_ends = [item[0] + item[2] - 1 for item in group]
            if (
                abs(start - float(np.median(group_starts))) <= span_tolerance
                and abs(end - float(np.median(group_ends))) <= span_tolerance
            ):
                group.append(component)
                break
        else:
            groups.append([component])

    fx0, fy0, fx1, fy1 = fallback
    fallback_width = max(1, fx1 - fx0)
    fallback_height = max(1, fy1 - fy0)
    fallback_center = (0.5 * (fx0 + fx1), 0.5 * (fy0 + fy1))
    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    for group in groups:
        y_centers = sorted({int(round(y + 0.5 * hh)) for _x, y, _ww, hh in group})
        if len(y_centers) < 2:
            continue
        x_start = int(round(float(np.median([x for x, _y, _ww, _hh in group]))))
        x_end = int(
            round(float(np.median([x + ww - 1 for x, _y, ww, _hh in group])))
        )
        candidate_width = x_end - x_start
        if candidate_width < 0.30 * fallback_width:
            continue

        edge_tolerance = max(6, int(round(0.015 * candidate_width)))
        left_edges: list[tuple[float, tuple[int, int, int, int]]] = []
        right_edges: list[tuple[float, tuple[int, int, int, int]]] = []
        for component in vertical:
            x, y, ww, hh = component
            center_x = x + 0.5 * ww
            if abs(center_x - x_start) <= edge_tolerance:
                left_edges.append((center_x, component))
            if abs(center_x - x_end) <= edge_tolerance:
                right_edges.append((center_x, component))
        if not left_edges or not right_edges:
            continue
        for left_center, left in left_edges:
            for right_center, right in right_edges:
                vertical_start = max(left[1], right[1])
                vertical_end = min(left[1] + left[3] - 1, right[1] + right[3] - 1)
                supported_ys = [
                    y
                    for y in y_centers
                    if vertical_start - edge_tolerance <= y <= vertical_end + edge_tolerance
                ]
                if len(supported_ys) < 2:
                    continue
                y_start, y_end = supported_ys[0], supported_ys[-1]
                candidate_height = y_end - y_start
                if candidate_height < 0.30 * fallback_height:
                    continue
                if (
                    abs(y_start - vertical_start) > edge_tolerance
                    or abs(y_end - vertical_end) > edge_tolerance
                ):
                    continue
                frame = (
                    int(round(left_center)),
                    y_start,
                    int(round(right_center)),
                    y_end,
                )

                ix0 = max(frame[0], fx0)
                iy0 = max(frame[1], fy0)
                ix1 = min(frame[2], fx1)
                iy1 = min(frame[3], fy1)
                intersection = max(0, ix1 - ix0) * max(0, iy1 - iy0)
                frame_area = max(1, (frame[2] - frame[0]) * (frame[3] - frame[1]))
                overlap_fraction = intersection / frame_area
                if overlap_fraction < 0.35:
                    continue
                center_distance = math.hypot(
                    0.5 * (frame[0] + frame[2]) - fallback_center[0],
                    0.5 * (frame[1] + frame[3]) - fallback_center[1],
                ) / max(fallback_width, fallback_height)
                line_support = min(10, len(supported_ys)) / 10.0
                score = 4.0 * overlap_fraction + line_support - center_distance
                candidates.append((score, frame))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _detect_regular_grid_box(
    crop: Image.Image,
    fallback: tuple[int, int, int, int],
) -> tuple[tuple[int, int, int, int], tuple[int, ...], tuple[int, ...]] | None:
    """Detect a regular raster grid, including dotted manufacturer grids."""

    gray = np.asarray(crop.convert("L"))
    fx0, fy0, fx1, fy1 = fallback
    fx0, fy0 = max(0, fx0), max(0, fy0)
    fx1 = min(gray.shape[1] - 1, fx1)
    fy1 = min(gray.shape[0] - 1, fy1)
    roi = gray[fy0 : fy1 + 1, fx0 : fx1 + 1]
    if roi.shape[0] < 40 or roi.shape[1] < 40:
        return None
    dark = roi < 210
    x_candidates = _projection_line_centers(dark.sum(axis=0), 0.16 * roi.shape[0])
    y_candidates = _projection_line_centers(dark.sum(axis=1), 0.16 * roi.shape[1])
    xs = _longest_regular_run(x_candidates)
    ys = _longest_regular_run(y_candidates)
    if len(xs) < 3 or len(ys) < 3:
        return None
    box = (fx0 + xs[0], fy0 + ys[0], fx0 + xs[-1], fy0 + ys[-1])
    if box[2] - box[0] < 0.35 * (fx1 - fx0) or box[3] - box[1] < 0.35 * (fy1 - fy0):
        return None
    return box, tuple(fy0 + y for y in ys), tuple(fx0 + x for x in xs)


def _projection_line_centers(
    counts: np.ndarray,
    minimum: float,
    maximum_index_gap: int = 3,
) -> list[int]:
    indexes = np.flatnonzero(counts >= minimum)
    groups: list[list[int]] = []
    for index in indexes:
        value = int(index)
        if groups and value - groups[-1][-1] <= maximum_index_gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [int(round(float(np.median(group)))) for group in groups]


def _longest_regular_run(values: list[int]) -> list[int]:
    best: tuple[int, int, list[int]] | None = None
    for start in range(len(values) - 2):
        for second in range(start + 1, len(values) - 1):
            gap = values[second] - values[start]
            if gap < 18:
                continue
            tolerance = max(4, int(round(0.08 * gap)))
            run = [values[start]]
            target = values[start] + gap
            while target <= values[-1] + tolerance:
                nearest = min(values, key=lambda value: abs(value - target))
                if abs(nearest - target) <= tolerance and nearest > run[-1]:
                    run.append(nearest)
                target += gap
            if len(run) < 3:
                continue
            if any(abs(delta - gap) > tolerance for delta in np.diff(run)):
                continue
            score = (len(run), run[-1] - run[0])
            if best is None or score > best[:2]:
                best = (*score, run)
    return best[2] if best is not None else []


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
