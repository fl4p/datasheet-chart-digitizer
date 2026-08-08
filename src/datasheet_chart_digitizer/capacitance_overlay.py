"""Overlay rendering for MOSFET capacitance chart extraction."""

from __future__ import annotations

from collections.abc import Mapping

import cv2
import numpy as np

from .capacitance_axis import calibration_x_of_v, calibration_y_of_log_c
from .capacitance_types import AxisCalibration, PlotBox, TRACE_COLORS_BGR, Trace
from .overlay import draw_axis_ticks, draw_plot_frame


def draw_trace_overlay(
    image: np.ndarray,
    plot: PlotBox,
    traces: list[Trace],
    calibration: AxisCalibration | None = None,
    shared_spans: list[dict[str, object]] | None = None,
    anchors: Mapping[str, object] | None = None,
) -> np.ndarray:
    overlay = image.copy()
    draw_plot_frame(overlay, plot, color=(0, 180, 255))

    for trace in traces:
        color = _trace_color_bgr(trace)
        pts = trace.points
        for a, b in zip(pts, pts[1:]):
            dx = abs(b[0] - a[0])
            dy = abs(b[1] - a[1])
            if dx <= max(8, int(plot.width * 0.06)) and dy <= max(
                60, int(plot.height * 0.18)
            ):
                # Keep the printed source stroke visible under the extraction;
                # a thick opaque polyline hid neighbor-snaps at Ciss/Coss
                # intersections during human review.
                cv2.line(overlay, a, b, color, 1, lineType=cv2.LINE_AA)
        for point in pts[:: max(1, len(pts) // 140)]:
            cv2.circle(overlay, point, 1, color, -1, lineType=cv2.LINE_AA)

        label_at = pts[min(len(pts) - 1, max(0, int(len(pts) * 0.78)))]
        cv2.putText(
            overlay,
            trace.name,
            (label_at[0] + 5, max(18, label_at[1] - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            lineType=cv2.LINE_AA,
        )

    _draw_shared_ciss_coss_spans(overlay, traces, shared_spans or [])
    _draw_anchors(overlay, plot, calibration, anchors or {}, traces)

    if calibration is not None:
        source_x_ticks = (
            calibration.x_source_ticks_v
            if calibration.x_source_ticks_v
            else calibration.x_ticks_v
        )
        x_ticks = [
            (calibration_x_of_v(calibration, plot, float(value)), float(source_value))
            for value, source_value in zip(calibration.x_ticks_v, source_x_ticks)
        ]
        y_ticks = [
            (
                calibration_y_of_log_c(calibration, plot, float(exponent)),
                10.0 ** float(exponent),
            )
            for exponent in calibration.y_decades
        ]
        draw_axis_ticks(
            overlay,
            plot,
            x_ticks,
            y_ticks,
            color=(255, 0, 0),
            font_scale=0.38,
            thickness=1,
            marker_size=8,
            unit_x="V",
            unit_y="pF",
        )
        if calibration.x_value_transform == "abs_source_negative_vds":
            cv2.putText(
                overlay,
                "SOURCE X: negative VDS; served X: |VDS|",
                (max(5, plot.x0), max(18, plot.y0 - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (130, 0, 130),
                1,
                lineType=cv2.LINE_AA,
            )
    return overlay


def _draw_anchors(
    overlay: np.ndarray,
    plot: PlotBox,
    calibration: AxisCalibration | None,
    anchors: Mapping[str, object],
    traces: list[Trace],
) -> None:
    """Mark every spec-table anchor on the chart it was compared against.

    The anchor is the only independent evidence most of these charts carry, and
    the residual it produces is the number a reviewer trusts -- but the residual
    is a scalar, so it cannot show WHERE the comparison happened or how much
    room the axis had there. Drawing the anchor makes both visible: on
    SUP70060E the Crss anchor lands 5 px off the frame with the trace beneath
    it, which reads instantly as "this comparison had no room" where "+1.4 %"
    reads as agreement.

    Every supplied anchor is accounted for. One that cannot be PLACED -- no
    trusted axis, a non-positive value, a position off the plot -- is listed in
    a corner note instead of being dropped, because a missing marker is
    indistinguishable from a chart that carried no anchor at all, and that is
    the reading that lets a bad comparison pass review unseen.
    """

    if not anchors:
        return
    trace_colors = {trace.name: _trace_color_bgr(trace) for trace in traces}
    unplaced: list[str] = []
    for name in ("Ciss", "Coss", "Crss"):
        anchor = anchors.get(name)
        if anchor is None:
            continue
        value_pf = getattr(anchor, "value_pf", None)
        vds_v = getattr(anchor, "vds_v", None)
        label = "%s %s pF @%s V" % (
            name,
            "?" if value_pf is None else "%g" % float(value_pf),
            "?" if vds_v is None else "%g" % float(vds_v),
        )
        if calibration is None:
            unplaced.append("%s (axis untrusted)" % label)
            continue
        if not value_pf or float(value_pf) <= 0.0 or vds_v is None:
            unplaced.append("%s (unusable value)" % label)
            continue
        x = int(round(calibration_x_of_v(calibration, plot, float(vds_v))))
        y = int(round(
            calibration_y_of_log_c(calibration, plot, np.log10(float(value_pf)))
        ))
        if not (plot.x0 <= x <= plot.x1 and plot.y0 <= y <= plot.y1):
            unplaced.append("%s (off-chart)" % label)
            continue
        color = trace_colors.get(name, TRACE_COLORS_BGR[name])
        _draw_anchor_marker(overlay, x, y, color, label, plot)
    if unplaced:
        _draw_unplaced_anchor_note(overlay, plot, unplaced)


# The marker is a hollow diamond with a crosshair: no extraction glyph on these
# overlays uses either, so it cannot be misread as a digitized sample.
ANCHOR_MARKER_RADIUS_PX = 7


def _draw_anchor_marker(
    overlay: np.ndarray,
    x: int,
    y: int,
    color: tuple[int, int, int],
    label: str,
    plot: PlotBox,
) -> None:
    r = ANCHOR_MARKER_RADIUS_PX
    diamond = np.array([[x, y - r], [x + r, y], [x, y + r], [x - r, y]], dtype=np.int32)
    # White underlay first: these charts run light and a saturated hue alone
    # vanishes against its own trace.
    cv2.polylines(overlay, [diamond], True, (255, 255, 255), 3, lineType=cv2.LINE_AA)
    cv2.polylines(overlay, [diamond], True, color, 1, lineType=cv2.LINE_AA)
    cv2.line(overlay, (x - r - 3, y), (x + r + 3, y), color, 1, lineType=cv2.LINE_AA)
    cv2.line(overlay, (x, y - r - 3), (x, y + r + 3), color, 1, lineType=cv2.LINE_AA)
    # Label below-right by default, flipped near the right edge so it stays on
    # the crop rather than being clipped away. Flipped ABOVE the marker in the
    # bottom of the plot, where the label would otherwise land on the x-axis
    # tick row -- and the bottom is exactly where the anchors worth reading sit,
    # because that is where the axis has run out of resolution.
    text_x = x + r + 4
    if text_x > plot.x1 - 90:
        text_x = max(plot.x0, x - r - 92)
    if y > plot.y1 - max(18, int(plot.height * 0.12)):
        text_y = max(plot.y0 + 10, y - r - 5)
    else:
        text_y = min(plot.y1 - 3, y + r + 12)
    for shade, thickness in (((255, 255, 255), 3), (color, 1)):
        cv2.putText(
            overlay, label, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX,
            0.38, shade, thickness, lineType=cv2.LINE_AA,
        )


def _draw_unplaced_anchor_note(
    overlay: np.ndarray, plot: PlotBox, unplaced: list[str]
) -> None:
    lines = ["ANCHORS NOT PLACED:"] + unplaced
    y = max(12, plot.y0 + 14)
    for line in lines:
        for shade, thickness in (((255, 255, 255), 3), ((0, 0, 200), 1)):
            cv2.putText(
                overlay, line, (max(4, plot.x0 + 6), y), cv2.FONT_HERSHEY_SIMPLEX,
                0.40, shade, thickness, lineType=cv2.LINE_AA,
            )
        y += 14


def _draw_shared_ciss_coss_spans(
    overlay: np.ndarray,
    traces: list[Trace],
    spans: list[dict[str, object]],
) -> None:
    """Show a merged source stroke without pretending two curves were visible."""

    traces_by_name = {trace.name: trace for trace in traces}
    by_name = {
        name: {x: y for x, y in trace.points}
        for name, trace in traces_by_name.items()
    }
    if not {"Ciss", "Coss"}.issubset(by_name):
        return
    ciss = by_name["Ciss"]
    coss = by_name["Coss"]
    for span in spans:
        x0 = int(span["x0_px"])
        x1 = int(span["x1_px"])
        points = [
            (x, int(round((ciss[x] + coss[x]) / 2)))
            for x in sorted(ciss.keys() & coss.keys())
            if x0 <= x <= x1
        ]
        for index, (a, b) in enumerate(zip(points, points[1:])):
            color = (
                _trace_color_bgr(traces_by_name["Ciss"])
                if (index // 6) % 2 == 0
                else _trace_color_bgr(traces_by_name["Coss"])
            )
            cv2.line(overlay, a, b, color, 4, lineType=cv2.LINE_AA)
        if points:
            x, y = points[len(points) // 2]
            cv2.putText(
                overlay,
                "Ciss=Coss shared",
                (x + 5, max(18, y - 7)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (150, 0, 150),
                1,
                lineType=cv2.LINE_AA,
            )


def _trace_color_bgr(trace: Trace) -> tuple[int, int, int]:
    """Use a legend-bound source hue; otherwise keep the review palette."""

    if trace.source_color_rgb is None:
        return TRACE_COLORS_BGR[trace.name]
    rgb = tuple(
        max(0, min(255, int(round(255.0 * channel))))
        for channel in trace.source_color_rgb
    )
    return rgb[2], rgb[1], rgb[0]


def draw_axis_debug_overlay(
    image: np.ndarray,
    plot: PlotBox,
    calibration: AxisCalibration,
    title: str,
) -> np.ndarray:
    overlay = image.copy()
    draw_plot_frame(overlay, plot, color=(0, 180, 255))

    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(
        overlay,
        title[:120],
        (max(5, plot.x0 - 90), max(26, plot.y0 - 42)),
        font,
        0.82,
        (0, 0, 0),
        3,
        lineType=cv2.LINE_AA,
    )
    subtitle1 = (
        f"axis={calibration.source} x={calibration.x_source or 'n/a'} "
        f"y={calibration.y_source or 'n/a'} "
        f"x_resid={_fmt_optional(calibration.x_resid_v)} "
        f"y_resid={_fmt_optional(calibration.y_resid_dec if calibration.y_log else calibration.y_resid_pf)}"
    )
    subtitle2 = (
        f"grid_n={calibration.y_grid_candidate_count if calibration.y_grid_candidate_count is not None else 'n/a'} "
        f"grid_span={_fmt_optional(calibration.y_grid_span_fraction)} "
        f"grid_resid_px={_fmt_optional(calibration.y_grid_residual_px)}"
    )
    if calibration.x_value_transform is not None:
        subtitle2 += f" x_transform={calibration.x_value_transform}"
    cv2.putText(
        overlay,
        subtitle1[:140],
        (max(5, plot.x0 - 90), max(52, plot.y0 - 16)),
        font,
        0.62,
        (0, 0, 0),
        2,
        lineType=cv2.LINE_AA,
    )
    cv2.putText(
        overlay,
        subtitle2[:140],
        (max(5, plot.x0 - 90), max(78, plot.y0 + 10)),
        font,
        0.62,
        (0, 0, 0),
        2,
        lineType=cv2.LINE_AA,
    )

    source_x_ticks = (
        calibration.x_source_ticks_v
        if calibration.x_source_ticks_v
        else calibration.x_ticks_v
    )
    for tick, source_tick in zip(calibration.x_ticks_v, source_x_ticks):
        x = int(round(calibration_x_of_v(calibration, plot, float(tick))))
        if plot.x0 - 3 <= x <= plot.x1 + 3:
            cv2.line(overlay, (x, plot.y0), (x, plot.y1), (255, 230, 0), 3, lineType=cv2.LINE_AA)
            cv2.circle(overlay, (x, plot.y1), 8, (255, 230, 0), -1, lineType=cv2.LINE_AA)
            cv2.putText(
                overlay,
                f"{source_tick:g}",
                (x - 16, min(image.shape[0] - 6, plot.y1 + 32)),
                font,
                0.70,
                (180, 125, 0),
                2,
                lineType=cv2.LINE_AA,
            )
    for exponent in calibration.y_decades:
        y = int(round(calibration_y_of_log_c(calibration, plot, float(exponent))))
        if plot.y0 - 3 <= y <= plot.y1 + 3:
            cv2.line(overlay, (plot.x0, y), (plot.x1, y), (255, 0, 255), 3, lineType=cv2.LINE_AA)
            cv2.circle(overlay, (plot.x0, y), 8, (255, 0, 255), -1, lineType=cv2.LINE_AA)
            label = _axis_debug_y_label(calibration, float(exponent))
            cv2.putText(
                overlay,
                label,
                (max(2, plot.x0 - 82), y + 8),
                font,
                0.70,
                (180, 0, 180),
                2,
                lineType=cv2.LINE_AA,
            )
    for y_raw in calibration.y_gridline_px:
        y = int(round(y_raw))
        if plot.y0 - 3 <= y <= plot.y1 + 3:
            cv2.line(overlay, (plot.x0, y), (plot.x1, y), (160, 0, 160), 1, lineType=cv2.LINE_AA)
            cv2.drawMarker(
                overlay,
                (plot.x0 + 14, y),
                (160, 0, 160),
                markerType=cv2.MARKER_TILTED_CROSS,
                markerSize=16,
                thickness=2,
                line_type=cv2.LINE_AA,
            )
    for x_raw in calibration.x_gridline_px:
        x = int(round(x_raw))
        if plot.x0 - 3 <= x <= plot.x1 + 3:
            cv2.line(overlay, (x, plot.y0), (x, plot.y1), (160, 160, 0), 1, lineType=cv2.LINE_AA)
            cv2.drawMarker(
                overlay,
                (x, plot.y1 - 14),
                (160, 160, 0),
                markerType=cv2.MARKER_TILTED_CROSS,
                markerSize=16,
                thickness=2,
                line_type=cv2.LINE_AA,
            )
    return overlay


def _fmt_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4g}"


def _axis_debug_y_label(calibration: AxisCalibration, exponent: float) -> str:
    """Keep established decade labels while showing dense log ticks physically."""

    nearest_integer = round(exponent)
    if calibration.y_log and abs(exponent - nearest_integer) < 1e-9:
        return f"10^{int(nearest_integer)}"
    return f"{10.0 ** exponent:g}pF"
