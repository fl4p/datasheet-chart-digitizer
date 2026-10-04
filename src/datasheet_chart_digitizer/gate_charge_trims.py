"""Gate-charge trace trims: where a traced VGS(Qg) curve stops.

Moved verbatim from gate_charge.py (file-size limit); re-exported there.
"""

from __future__ import annotations

TERMINAL_FLAT_MIN_SPAN_FRACTION = 0.08
TERMINAL_FLAT_MAX_Y_RANGE_PX = 2
TERMINAL_FLAT_MIN_ENTRY_RISE_FRACTION = 0.03
TERMINAL_FLAT_MAX_RIGHT_GAP_FRACTION = 0.03


def _trim_terminal_flat_grid_capture(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> list[tuple[int, int]]:
    """Stop where a rising curve starts riding a horizontal gridline to the frame."""

    if len(curve) < 8:
        return curve
    x0, y0, x1, y1 = plot_box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    if x1 - curve[-1][0] > TERMINAL_FLAT_MAX_RIGHT_GAP_FRACTION * width:
        return curve

    tail_y = curve[-1][1]
    start = len(curve) - 1
    while start > 0 and abs(curve[start - 1][1] - tail_y) <= TERMINAL_FLAT_MAX_Y_RANGE_PX:
        start -= 1
    if start == 0:
        return curve
    flat_span = curve[-1][0] - curve[start][0]
    if flat_span <= TERMINAL_FLAT_MIN_SPAN_FRACTION * width:
        return curve
    entry_rise = curve[start - 1][1] - curve[start][1]
    if entry_rise < TERMINAL_FLAT_MIN_ENTRY_RISE_FRACTION * height:
        return curve
    return curve[: start + 1]


def _trim_after_upper_axis_reach(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> list[tuple[int, int]]:
    """Stop a gate curve when its selected branch first reaches the plot ceiling.

    Several VDS gate-charge curves can share the initial rise and plateau, then
    terminate at the same VGS ceiling at different Qg values.  A vector envelope
    may otherwise drop from the first completed branch onto the next and climb
    back to the ceiling, producing source-discontinuous teeth in the exported
    full curve.  Once a rising branch reaches the owned upper axis there is no
    physical in-frame continuation to preserve.
    """

    if len(curve) < 8:
        return curve
    x0, y0, x1, y1 = plot_box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    search_start = x0 + 0.55 * width
    ceiling_tolerance = max(3.0, 0.012 * height)
    for index, (x, y) in enumerate(curve):
        if x >= search_start and y <= y0 + ceiling_tolerance:
            return curve[: index + 1]
    return curve


def _trim_dual_y_terminal_branch_switch(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> list[tuple[int, int]]:
    """Stop at the first finished VGS branch instead of joining its neighbors.

    The Toshiba VGS bundle has several rising strokes that end separately at
    the same upper voltage.  Raster continuity can jump downward to a later
    branch after the first one ends.  A monotonic fit is unsafe here because it
    turns that source discontinuity into a horizontal, source-absent segment.
    """

    if len(curve) < 8:
        return curve
    x0, y0, x1, y1 = plot_box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    search_start = x0 + 0.55 * width
    reverse_jump = max(5.0, 0.012 * height)
    required_future_progress = max(6.0, 0.02 * height)
    for index in range(1, len(curve)):
        x, y = curve[index]
        previous_y = curve[index - 1][1]
        if x < search_start or y - previous_y < reverse_jump:
            continue
        future_min_y = min(point_y for _point_x, point_y in curve[index:])
        if previous_y - future_min_y < required_future_progress:
            return curve[:index]
    return curve


def _trim_dual_y_terminal_grid_capture(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> list[tuple[int, int]]:
    """Stop a Toshiba VGS trace where it reaches and then rides a gridline."""

    if len(curve) < 8:
        return curve
    x0, _y0, x1, _y1 = plot_box
    width = max(1, x1 - x0)
    if x1 - curve[-1][0] > TERMINAL_FLAT_MAX_RIGHT_GAP_FRACTION * width:
        return curve
    tail_y = curve[-1][1]
    start = len(curve) - 1
    while start > 0 and abs(curve[start - 1][1] - tail_y) <= 1:
        start -= 1
    if curve[-1][0] - curve[start][0] < 0.06 * width:
        return curve
    return curve[: start + 1]
