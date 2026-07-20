"""Overlay rendering for MOSFET capacitance chart extraction."""

from __future__ import annotations

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
) -> np.ndarray:
    overlay = image.copy()
    draw_plot_frame(overlay, plot, color=(0, 180, 255))

    for trace in traces:
        color = TRACE_COLORS_BGR[trace.name]
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


def _draw_shared_ciss_coss_spans(
    overlay: np.ndarray,
    traces: list[Trace],
    spans: list[dict[str, object]],
) -> None:
    """Show a merged source stroke without pretending two curves were visible."""

    by_name = {trace.name: {x: y for x, y in trace.points} for trace in traces}
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
                TRACE_COLORS_BGR["Ciss"]
                if (index // 6) % 2 == 0
                else TRACE_COLORS_BGR["Coss"]
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
