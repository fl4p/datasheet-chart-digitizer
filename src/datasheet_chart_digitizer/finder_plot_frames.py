"""Plot-frame geometry for caption-discovered chart panels.

Embedded-image rectangles, vector plot frames and the bbox overlap measures
used to deduplicate and match them.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

try:
    from .finder_types import BBox, _PageLike, _TitleLike
    from .vector_grid_frames import line_grid_frames
except ImportError:  # pragma: no cover - direct script compatibility
    from finder_types import BBox, _PageLike, _TitleLike
    from vector_grid_frames import line_grid_frames


def bbox_iou(a: BBox, b: BBox) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    overlap_x = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    overlap_y = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = overlap_x * overlap_y
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    return inter / max(1e-9, area_a + area_b - inter)


def bbox_overlap_fraction_of_smaller(a: BBox, b: BBox) -> float:
    """Return intersection area divided by the smaller candidate area."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    overlap_x = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    overlap_y = max(0.0, min(ay1, by1) - max(ay0, by0))
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    smaller = min(area_a, area_b)
    return overlap_x * overlap_y / smaller if smaller > 0.0 else 0.0


def page_image_rects(pdf: Path, page_num: int) -> list[BBox]:
    """Return figure-sized embedded-image rectangles on a page, in points."""
    try:
        with pymupdf.open(pdf) as doc:
            infos = doc[page_num - 1].get_image_info()
    except Exception:
        return []
    rects: list[BBox] = []
    for info in infos:
        x0, y0, x1, y1 = (
            float(value) for value in info.get("bbox", (0.0, 0.0, 0.0, 0.0))
        )
        if x1 - x0 >= 90.0 and y1 - y0 >= 90.0:
            rects.append((x0, y0, x1, y1))
    return rects


def _axis_aligned_quad(quad: object) -> bool:
    points = (quad.ul, quad.ur, quad.ll, quad.lr)
    xs = sorted(float(point.x) for point in points)
    ys = sorted(float(point.y) for point in points)
    return (
        xs[1] - xs[0] <= 0.5 and xs[3] - xs[2] <= 0.5
        and ys[1] - ys[0] <= 0.5 and ys[3] - ys[2] <= 0.5
    )


def page_vector_plot_frames(
    pdf: Path, page_num: int, page: _PageLike, *, include_quadrilaterals: bool = True,
    include_gridded: bool = False,
) -> list[BBox]:
    """Return positively evidenced stroked vector plot frames on one page.

    ``include_gridded`` also accepts a hairline (0.20..0.45 pt) frame and an
    axis-aligned quad with non-standard corner order, but only when at least
    three interior horizontal grid lines span the frame's full width; the grid,
    not the stroke weight or winding, is the frame evidence there.
    """
    try:
        with pymupdf.open(pdf) as doc:
            drawings = doc[page_num - 1].get_drawings()
    except (OSError, RuntimeError, ValueError, IndexError):
        return []

    min_width = page.width_pt * 0.14
    max_width = page.width_pt * 0.48
    min_height = page.height_pt * 0.08
    max_height = page.height_pt * 0.40
    frames: list[BBox] = []
    thin_spans: list[tuple[float, float, float]] = []
    if include_gridded:
        for drawing in drawings:
            if drawing.get("type") not in {"s", "fs"}:
                continue
            for item in drawing.get("items", []):
                if item[0] == "l" and abs(float(item[1].y) - float(item[2].y)) <= 0.5:
                    left, right = sorted((float(item[1].x), float(item[2].x)))
                    thin_spans.append((left, right, 0.5 * (float(item[1].y) + float(item[2].y))))

    def gridded(rect: object) -> bool:
        x0, y0, x1, y1 = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        rows = {
            round(y, 1) for left, right, y in thin_spans
            if abs(left - x0) <= 1.0 and abs(right - x1) <= 1.0 and y0 + 1.0 < y < y1 - 1.0
        }
        return len(rows) >= 3

    for drawing in drawings:
        if drawing.get("type") not in {"s", "fs"}:
            continue
        stroke_width = float(drawing.get("width") or 0.0)
        thin = include_gridded and 0.20 <= stroke_width < 0.45
        if not (0.45 <= stroke_width <= 2.5 or thin):
            continue
        for item in drawing.get("items", []):
            needs_grid = False
            if item[0] == "re":
                rect = item[1]
            elif item[0] == "qu" and include_quadrilaterals and item[1].is_rectangular:
                rect = item[1].rect
            elif item[0] == "qu" and include_gridded and _axis_aligned_quad(item[1]):
                # ST NK frames list corners as ul, ll, ur, lr: an axis-aligned
                # box PyMuPDF reports as non-rectangular (mirrored winding).
                rect = item[1].rect
                needs_grid = True
            else:
                continue
            width = float(rect.width)
            height = float(rect.height)
            if not (
                min_width <= width <= max_width
                and min_height <= height <= max_height
            ):
                continue
            aspect = width / max(height, 1e-9)
            if not 0.55 <= aspect <= 2.4:
                continue
            if (thin or needs_grid) and not gridded(rect):
                continue
            candidate = (
                float(rect.x0),
                float(rect.y0),
                float(rect.x1),
                float(rect.y1),
            )
            if any(bbox_iou(candidate, existing) >= 0.92 for existing in frames):
                continue
            frames.append(candidate)

    recovered = line_grid_frames(
        drawings, min_width=min_width, max_width=max_width,
        min_height=min_height, max_height=max_height, existing=frames,
    )
    frames.extend(recovered)
    return sorted(frames, key=lambda box: (box[1], box[0]))


def caption_vector_frame_bbox(
    page: _PageLike,
    title: _TitleLike,
    frames: list[BBox],
    *,
    numbered_caption: bool,
    tight: bool = False,
) -> BBox | None:
    """Bind an unnumbered caption to a nearby same-column vector frame."""
    if numbered_caption:
        return None

    tx0, ty0, tx1, ty1 = title.bbox_pt
    tcx = 0.5 * (tx0 + tx1)
    best: tuple[float, BBox, str] | None = None
    for frame in frames:
        x0, y0, x1, y1 = frame
        width = x1 - x0
        center_x = 0.5 * (x0 + x1)
        if not x0 - 0.25 * width <= tcx <= x1 + 0.25 * width:
            continue
        if ty1 <= y0:
            gap = y0 - ty1
            direction = "below"
        elif y1 <= ty0:
            gap = ty0 - y1
            direction = "above"
        else:
            continue
        if gap > 65.0:
            continue
        if tight and not (
            direction == "below" and gap <= 24.0
            or direction == "above" and 24.0 <= gap <= 52.0
        ):
            continue
        score = gap + 0.20 * abs(tcx - center_x)
        if best is None or score < best[0]:
            best = (score, frame, direction)
    if best is None:
        return None

    _, (x0, y0, x1, y1), direction = best
    width = x1 - x0
    height = y1 - y0
    left_pad = min(52.0, 0.36 * width)
    right_pad = min(20.0, 0.15 * width)
    vertical_pad = min(16.0, 0.10 * height) if tight else min(56.0, 0.40 * height)
    if direction == "below":
        crop_y0 = min(ty0 - 2.0, y0 - 8.0)
        crop_y1 = y1 + vertical_pad
    else:
        crop_y0 = y0 - 8.0
        crop_y1 = max(ty1 + 2.0, y1 + vertical_pad)
    return (
        max(0.0, x0 - left_pad),
        max(0.0, crop_y0),
        min(page.width_pt, x1 + right_pad),
        min(page.height_pt, crop_y1),
    )


def numbered_breakdown_vector_frame_bbox(
    page: _PageLike, title: _TitleLike, frames: list[BBox]
) -> BBox | None:
    """Own a directly adjacent closed frame for a numbered breakdown caption.

    ST layouts can place a caption almost equidistant between two raster grids.
    A same-column stroked frame within 30 pt is stronger ownership evidence than
    that unstable raster tie.  Return the frame itself so the shared axis-gutter
    expansion can add only its local ticks and labels afterward.
    """
    tx0, ty0, tx1, ty1 = title.bbox_pt
    title_width = max(1.0, tx1 - tx0)
    candidates: list[tuple[float, int, float, BBox]] = []
    for frame in frames:
        x0, y0, x1, y1 = frame
        overlap = max(0.0, min(tx1, x1) - max(tx0, x0))
        if overlap < min(title_width, x1 - x0) * 0.30:
            continue
        if ty1 <= y0:
            gap, direction_rank = y0 - ty1, 0
        elif y1 <= ty0:
            gap, direction_rank = ty0 - y1, 1
        else:
            continue
        if gap > 30.0:
            continue
        center_delta = abs(0.5 * (tx0 + tx1) - 0.5 * (x0 + x1))
        candidates.append((gap, direction_rank, center_delta, frame))
    if not candidates:
        return None
    nearest_gap = min(item[0] for item in candidates)
    near_tie = [item for item in candidates if item[0] <= nearest_gap + 4.0]
    return min(near_tie, key=lambda item: (item[1], item[0], item[2]))[3]


def caption_image_panel_bbox(
    image_rects: list[BBox], title: _TitleLike, bbox: BBox | None
) -> BBox | None:
    """Bind a caption to the embedded image directly above it, if any."""
    tx0, ty0, tx1, _ = title.bbox_pt
    tcx = (tx0 + tx1) / 2
    best: tuple[float, BBox] | None = None
    for rect in image_rects:
        x0, y0, x1, y1 = rect
        if not x0 <= tcx <= x1:
            continue
        gap = ty0 - y1
        if not -10.0 <= gap <= 70.0:
            continue
        if bbox is not None:
            ix0, iy0 = max(bbox[0], x0), max(bbox[1], y0)
            ix1, iy1 = min(bbox[2], x1), min(bbox[3], y1)
            inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
            area = max(1e-9, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
            if inter / area < 0.6:
                continue
        if best is None or gap < best[0]:
            best = (gap, rect)
    return best[1] if best else None
