"""Positive-evidence plot-box recovery for sparse chart grids."""

from __future__ import annotations

import cv2
import numpy as np

from .capacitance_traces import find_plot_box
from .capacitance_types import PlotBox


def find_closed_frame_plot_box(gray: np.ndarray) -> PlotBox:
    """Use the established detector, then recover a sparse closed frame.

    Some charts draw only the frame and a few gridlines as solid strokes;
    dotted or occluded gridlines disappear under ``find_plot_box`` morphology.
    Four mutually closing rails are still strong plot evidence, but the shared
    detector's six-vertical minimum rejects them.
    Keep that shared contract intact and admit the sparse case only when the
    top, bottom, left, and right source rails close the same rectangle.
    """

    try:
        detected = find_plot_box(gray)
    except RuntimeError as exc:
        if "could not find plot grid verticals" not in str(exc):
            raise
        recovered = _sparse_closed_frame(gray)
        if recovered is None:
            raise
        return recovered
    recovered = _sparse_closed_frame(gray)
    if recovered is None:
        return _open_right_grid_extent(gray, detected) or detected
    height, width = gray.shape
    same_left_and_rows = (
        abs(recovered.x0 - detected.x0) <= max(4, round(0.02 * width))
        and abs(recovered.y0 - detected.y0) <= max(4, round(0.02 * height))
        and abs(recovered.y1 - detected.y1) <= max(4, round(0.02 * height))
    )
    right_extension = recovered.x1 - detected.x1
    if (
        same_left_and_rows
        and 0.02 * width <= right_extension <= 0.20 * detected.width
    ):
        # The generic detector deliberately excludes the crop's outer 4%; a
        # real chart frame can live there. Four mutually closing source rails
        # prove that the last admitted inner gridline is not the right frame.
        return recovered
    open_right = _open_right_grid_extent(gray, detected)
    if open_right is not None:
        return open_right
    return detected


def find_capacitance_plot_box(gray: np.ndarray) -> PlotBox:
    """Compatibility name for capacitance callers."""
    return find_closed_frame_plot_box(gray)


def _orthogonal_line_boxes(
    bw: np.ndarray, *, vertical: bool
) -> list[tuple[int, int, int, int]]:
    height, width = bw.shape
    if vertical:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (1, max(80, height // 5))
        )
    else:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(80, width // 5), 1)
        )
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(
        lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if vertical:
            if box_width <= 8 and box_height >= height * 0.45:
                boxes.append((x, y, box_width, box_height))
        elif box_height <= 8 and box_width >= width * 0.45:
            boxes.append((x, y, box_width, box_height))
    return boxes


def _sparse_closed_frame(gray: np.ndarray) -> PlotBox | None:
    """Return a four-rail plot rectangle, or ``None`` on any ambiguity."""

    height, width = gray.shape
    _, bw = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)
    verticals = _orthogonal_line_boxes(bw, vertical=True)
    horizontals = _orthogonal_line_boxes(bw, vertical=False)
    if len(verticals) < 2 or len(horizontals) < 2:
        return None

    # Reject page/crop borders up front. The established detector deliberately
    # excludes them; this fallback may only recover a source-owned inner frame.
    verticals = [
        box
        for box in verticals
        if width * 0.02 <= box[0] + (box[2] - 1) / 2 <= width * 0.985
    ]
    if len(verticals) < 2:
        return None

    rails = sorted(
        (
            box[0] + (box[2] - 1) / 2,
            float(box[1]),
            float(box[1] + box[3] - 1),
        )
        for box in verticals
    )
    candidates: set[PlotBox] = set()
    for index, left in enumerate(rails):
        for right in rails[index + 1 :]:
            x0, x1 = left[0], right[0]
            y0 = float(np.median((left[1], right[1])))
            y1 = float(np.median((left[2], right[2])))
            plot_width = x1 - x0
            plot_height = y1 - y0
            if plot_width < width * 0.50 or plot_height < height * 0.45:
                continue
            if y0 < height * 0.02 or y1 > height * 0.985:
                continue

            x_tolerance = max(5.0, plot_width * 0.015)
            y_tolerance = max(5.0, plot_height * 0.02)
            if any(
                abs(rail[1] - y0) > y_tolerance
                or abs(rail[2] - y1) > y_tolerance
                for rail in (left, right)
            ):
                continue

            matching = []
            for x, y, box_width, box_height in horizontals:
                center_y = y + (box_height - 1) / 2
                rail_right = x + box_width - 1
                if (
                    abs(x - x0) <= x_tolerance
                    and abs(rail_right - x1) <= x_tolerance
                ):
                    matching.append(center_y)
            top = next(
                (center for center in matching if abs(center - y0) <= y_tolerance),
                None,
            )
            bottom = next(
                (center for center in matching if abs(center - y1) <= y_tolerance),
                None,
            )
            if top is None or bottom is None:
                continue
            candidates.add(
                PlotBox(
                    int(round(x0)),
                    int(round(top)),
                    int(round(x1)),
                    int(round(bottom)),
                )
            )

    if not candidates:
        return None
    return max(candidates, key=lambda box: box.width * box.height)


def _open_right_grid_extent(gray: np.ndarray, detected: PlotBox) -> PlotBox | None:
    """Recover an occluded right rail from repeated owned grid-row endpoints.

    A few Infineon charts clip the drawn right frame but retain many horizontal
    rails through the true endpoint. Inline curve labels merge the final
    vertical gridlines into wide contours, so the generic detector stops at an
    interior line. Require a dominant common row endpoint, top/bottom closure,
    and a regular owned vertical grid before extending; text is not evidence.
    """

    height, width = gray.shape
    _, bw = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)
    horizontals = _orthogonal_line_boxes(bw, vertical=False)
    left_tolerance = max(5.0, detected.width * 0.015)
    row_tolerance = max(5.0, detected.height * 0.02)
    rows = [
        (y + (box_height - 1) / 2, x + box_width - 1)
        for x, y, box_width, box_height in horizontals
        if abs(x - detected.x0) <= left_tolerance
        and detected.y0 - row_tolerance <= y <= detected.y1 + row_tolerance
    ]
    if len(rows) < 5:
        return None

    endpoint_tolerance = max(4.0, width * 0.008)
    minimum_extension = 0.02 * width
    endpoint_groups = [
        [row for row in rows if abs(row[1] - endpoint) <= endpoint_tolerance]
        for _center_y, endpoint in rows
        if endpoint >= detected.x1 + minimum_extension
    ]
    if not endpoint_groups:
        return None
    support = max(endpoint_groups, key=len)
    if len(support) < max(5, int(np.ceil(0.60 * len(rows)))):
        return None
    right = float(np.median([endpoint for _y, endpoint in support]))
    support_rows = [center_y for center_y, _endpoint in support]
    if (
        not any(abs(center_y - detected.y0) <= row_tolerance for center_y in support_rows)
        or not any(abs(center_y - detected.y1) <= row_tolerance for center_y in support_rows)
    ):
        return None

    verticals = _orthogonal_line_boxes(bw, vertical=True)
    centers = sorted(
        x + (box_width - 1) / 2
        for x, y, box_width, box_height in verticals
        if detected.x0 - left_tolerance <= x <= detected.x1 + left_tolerance
        and y <= detected.y0 + row_tolerance
        and y + box_height - 1 >= detected.y1 - row_tolerance
    )
    if len(centers) < 6:
        return None
    gaps = np.diff(np.asarray(centers, dtype=float))
    pitch = float(np.median(gaps))
    if pitch <= 0 or float(np.mean(np.abs(gaps - pitch) <= 0.20 * pitch)) < 0.80:
        return None
    if abs(centers[-1] - detected.x1) > max(5.0, 0.20 * pitch):
        return None

    extension = right - detected.x1
    if not (minimum_extension <= extension <= 0.20 * detected.width):
        return None
    if right > width * 0.985:
        return None
    return PlotBox(detected.x0, detected.y0, int(round(right)), detected.y1)
