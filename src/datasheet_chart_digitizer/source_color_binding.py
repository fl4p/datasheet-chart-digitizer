"""Fail-closed binding of two colored legend rows to source vector curves."""

from __future__ import annotations

import math
from collections.abc import Callable

import pymupdf

from .capacitance_types import PlotBox
from .crop_transform import CropTransform


DrawingCandidate = Callable[
    [dict, pymupdf.Rect, CropTransform, PlotBox],
    list[tuple[int, int]] | None,
]
ColorPredicate = Callable[[object], bool]


def _same_resampled_curve(
    candidate: list[tuple[int, int]],
    curve: list[tuple[int, int]],
) -> bool:
    """Allow raster-rounding noise while preserving one-to-one source identity."""

    candidate_by_x = {x: y for x, y in candidate}
    curve_by_x = {x: y for x, y in curve}
    common = sorted(candidate_by_x.keys() & curve_by_x.keys())
    if len(common) < 0.90 * max(len(candidate_by_x), len(curve_by_x)):
        return False
    differences = sorted(
        abs(candidate_by_x[x] - curve_by_x[x]) for x in common
    )
    if not differences:
        return False
    percentile_95 = differences[min(len(differences) - 1, int(0.95 * len(differences)))]
    mean_difference = sum(differences) / len(differences)
    return mean_difference <= 1.0 and percentile_95 <= 2


def bind_two_source_color_legend(
    page,
    transform: CropTransform,
    plot: PlotBox,
    pixel_curves: list[list[tuple[int, int]]],
    labels: list[tuple[float, tuple[float, float, float, float]]],
    *,
    drawing_candidate: DrawingCandidate,
    is_curve_color: ColorPredicate,
) -> list[tuple[float, int]] | None:
    """Bind two labels through native swatch and curve colors, or return None."""

    if len(pixel_curves) != 2 or len(labels) != 2:
        return None
    p0 = transform.to_pt(plot.x0, plot.y0)
    p1 = transform.to_pt(plot.x1, plot.y1)
    rect = pymupdf.Rect(p0[0], p0[1], p1[0], p1[1])
    drawings = page.get_drawings()
    styled_curves: list[tuple[tuple[float, float, float], int]] = []
    for drawing in drawings:
        color = drawing.get("color")
        if not is_curve_color(color):
            continue
        candidate = drawing_candidate(drawing, rect, transform, plot)
        if candidate is None:
            continue
        matches = [
            index
            for index, curve in enumerate(pixel_curves)
            if _same_resampled_curve(candidate, curve)
        ]
        if len(matches) == 1:
            styled_curves.append(
                (tuple(float(value) for value in color[:3]), matches[0])
            )
    if (
        len(styled_curves) != 2
        or len({index for _color, index in styled_curves}) != 2
    ):
        return None

    bindings: list[tuple[float, int]] = []
    for identity, label_rect in labels:
        lx0, ly0, _lx1, ly1 = label_rect
        swatches: list[tuple[float, float, float]] = []
        for drawing in drawings:
            color = drawing.get("color")
            if not is_curve_color(color):
                continue
            for item in drawing.get("items", []):
                if item[0] != "l":
                    continue
                x0, y0 = transform.to_px(float(item[1].x), float(item[1].y))
                x1, y1 = transform.to_px(float(item[2].x), float(item[2].y))
                length = math.hypot(x1 - x0, y1 - y0)
                center_y = 0.5 * (y0 + y1)
                right = max(x0, x1)
                if (
                    abs(y1 - y0) <= 1.0
                    and 10.0 <= length <= 60.0
                    and ly0 <= center_y <= ly1
                    and lx0 - 55.0 <= right <= lx0 - 2.0
                ):
                    swatches.append(tuple(float(value) for value in color[:3]))
        if not swatches:
            return None
        ranked: list[tuple[float, int]] = []
        for curve_color, curve_index in styled_curves:
            distance = min(
                math.sqrt(
                    sum(
                        (left - right) ** 2
                        for left, right in zip(curve_color, swatch)
                    )
                )
                for swatch in swatches
            )
            ranked.append((distance, curve_index))
        ranked.sort()
        if ranked[0][0] > 0.12 or ranked[1][0] - ranked[0][0] < 0.08:
            return None
        bindings.append((identity, ranked[0][1]))
    if len({index for _identity, index in bindings}) != 2:
        return None
    return sorted(bindings)
