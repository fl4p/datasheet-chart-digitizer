"""Recover plot frames whose PDF source contains separate grid-line objects."""

from __future__ import annotations


BBox = tuple[float, float, float, float]


def _iou(left: BBox, right: BBox) -> float:
    intersection = max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )
    union = (
        (left[2] - left[0]) * (left[3] - left[1])
        + (right[2] - right[0]) * (right[3] - right[1])
        - intersection
    )
    return intersection / max(union, 1e-9)


def line_grid_frames(
    drawings: list[dict],
    *,
    min_width: float,
    max_width: float,
    min_height: float,
    max_height: float,
    existing: list[BBox],
) -> list[BBox]:
    """Return complete two-dimensional line-grid extents not already present."""

    horizontal: dict[tuple[float, float], list[float]] = {}
    vertical: dict[tuple[float, float], list[float]] = {}
    for drawing in drawings:
        if drawing.get("type") not in {"s", "fs"}:
            continue
        stroke_width = float(drawing.get("width") or 0.0)
        color = drawing.get("color")
        if not 0.20 <= stroke_width <= 2.5:
            continue
        if (
            isinstance(color, tuple)
            and len(color) >= 3
            and max(float(value) for value in color[:3])
            - min(float(value) for value in color[:3])
            > 0.08
        ):
            continue
        for item in drawing.get("items", []):
            if item[0] != "l":
                continue
            x0, y0 = float(item[1].x), float(item[1].y)
            x1, y1 = float(item[2].x), float(item[2].y)
            if abs(y1 - y0) <= 0.75 and min_width <= abs(x1 - x0) <= max_width:
                left, right = sorted((x0, x1))
                horizontal.setdefault((round(left, 1), round(right, 1)), []).append(
                    0.5 * (y0 + y1)
                )
            if abs(x1 - x0) <= 0.75 and min_height <= abs(y1 - y0) <= max_height:
                top, bottom = sorted((y0, y1))
                vertical.setdefault((round(top, 1), round(bottom, 1)), []).append(
                    0.5 * (x0 + x1)
                )

    def merged(values: list[float]) -> list[float]:
        result: list[float] = []
        for value in sorted(values):
            if result and value - result[-1] <= 1.0:
                result[-1] = 0.5 * (result[-1] + value)
            else:
                result.append(value)
        return result

    recovered: list[BBox] = []
    for (left, right), raw_ys in horizontal.items():
        ys = merged(raw_ys)
        if len(ys) < 3:
            continue
        top, bottom = ys[0], ys[-1]
        if not min_height <= bottom - top <= max_height:
            continue
        for (vtop, vbottom), raw_xs in vertical.items():
            xs = merged(raw_xs)
            if len(xs) < 3:
                continue
            if max(
                abs(vtop - top),
                abs(vbottom - bottom),
                abs(xs[0] - left),
                abs(xs[-1] - right),
            ) > 1.5:
                continue
            candidate = (left, top, right, bottom)
            if not any(_iou(candidate, frame) >= 0.92 for frame in [*existing, *recovered]):
                recovered.append(candidate)
            break
    return recovered
