"""Digitize and fit MOSFET saturation transfer curves versus temperature.

Targets MOSFET ``Typical transfer characteristics`` panels used by the
dcdc-tools curve/recon channel model: linear ``Id=f(Vgs)`` axes, with two or
more temperature curves and a stated saturation condition such as
``|Vds| > 2 |Id| Rds(on)max``.

The fitted temperature law is deliberately relative to an externally supplied
25 C switching-law anchor.  The chart does not replace the exact gate-charge
``(Vpl, Id_gc)`` pivot.  At matched drain currents it fits

    delta_Vgs(I) = dVth + Vov_25_model(I) * (K_ratio**(-1/p) - 1)

which identifies ``dVth/dT`` and ``d(log K)/dT`` without forcing the absolute
25 C chart through a different anchor.  The absolute 25 C curve remains a
reported conflict check.

Every emitted panel is ``overlay-review-required``.  Downstream curation must
not mark coefficients verified until a human has checked the axis guides,
both extracted centerlines, temperature assignment, and fit summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .breakdown_voltage import (
    NUM_RE,
    _fit_axis,
    _vector_plot_frame,
    _words_in_crop_px,
)
from .capacitance_traces import find_plot_box
from .capacitance_plot_box import find_closed_frame_plot_box
from .diode_forward_voltage import _full_span_grid_lines, _snap_axis_to_grid
from .overlay import draw_axis_ticks, draw_plot_frame
from .capacitance_types import PlotBox, VectorEdge
from .numeric_axis import AxisTick, NumericAxis, fit_axis_ticks
from .capacitance_vector import (
    _is_curve_stroke_color,
    _is_neutral_gray_stroke,
    _load_fitz,
    _resample_vector_trace_pixels,
    _sample_cubic,
    _vector_curve_edges,
)
from .capacitance_vector import _chain_vector_components
from .crop_transform import CropTransform
from .chart_classifier import compact_formula_chart_kind
from .transfer_temperature_order import inverse_vgs as _inverse_vgs
from .transfer_temperature_order import bind_opposite_outer_labels, validate_two_curve_order

TEMP_RE = re.compile(
    r"(?<![\w.])([+-]?\d+(?:\.\d+)?)\s*°?\s*C"
    r"(?!\s*(?:=(?!\s*j\b)|C\b)|[A-Za-z])",
    re.IGNORECASE,
)
POSITIONED_TEMP_LINE_RE = re.compile(
    r"^\s*(?:T\s*(?:C|J)\s*=\s*)?"
    r"(?P<temperature>[+-]?\d+(?:\.\d+)?)\s*°?\s*C"
    r"(?P<condition>\s*,\s*V\s*D\s*S\s*=\s*"
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*V)?\s*[;:]?\s*$",
    re.IGNORECASE,
)
POSITIONED_TEMP_PREFIX_RE = re.compile(
    r"^\s*(?:T\s*(?:C|J)\s*=\s*)?"
    r"[+-]?\d+(?:\.\d+)?\s*°?\s*C\s*,?\s*$",
    re.IGNORECASE,
)
MIN_RUN_X_SPAN = 0.30
MAX_TEMPCO_FIT_RMS_V = 0.05
# This is a conflict detector, not a demand that a scalar-anchored surrogate
# reproduce the whole typical transfer chart.  Tighten only after the first
# human-reviewed part set establishes an honest empirical band.
MAX_COLD_CONFLICT_RMS_V = 0.35
MAX_COLD_CONFLICT_ABS_V = 0.75


@dataclass(frozen=True)
class TransferCurve:
    tj_c: float
    points: list[tuple[float, float]]  # (Vgs_V, Id_A)


def _split_monotone_edge_runs(edges: list[VectorEdge], plot_width_pt: float) -> list[list[VectorEdge]]:
    """Split PDF line-segment streams into left-to-right curve runs.

    Infineon emits each short curve segment as a separate drawing.  The two
    curves share the zero-current baseline and cross near ZTC, so graph
    connected-components merge them.  Drawing order is nevertheless stable:
    one complete left-to-right curve, then X resets and the next begins.
    Splitting on that reset preserves crossings without guessing branches.
    """
    runs: list[list[VectorEdge]] = []
    current: list[VectorEdge] = []
    last_mid_x: float | None = None
    reset = max(0.8, 0.05 * plot_width_pt)
    join_tolerance = max(1.0, 0.015 * plot_width_pt)
    for edge in edges:
        mid_x = 0.5 * (edge.p0[0] + edge.p1[0])
        endpoint_gap = min(
            math.hypot(a[0] - b[0], a[1] - b[1])
            for a in (current[-1].p0, current[-1].p1)
            for b in (edge.p0, edge.p1)
        ) if current else 0.0
        if current and last_mid_x is not None and (
            mid_x < last_mid_x - reset or endpoint_gap > join_tolerance
        ):
            runs.append(current)
            current = []
        current.append(edge)
        last_mid_x = mid_x
    if current:
        runs.append(current)
    return runs


def _resample_transfer_pixels(
    raw: list[tuple[int, int]], plot: PlotBox, *, by_current: bool
) -> list[tuple[int, int]]:
    """Resample a transfer stroke along its single-valued physical axis.

    Two-curve Infineon panels use ordinary Id(Vgs) paths and retain the
    established X-bucket behavior. Some 3+ temperature panels contain a
    source-faithful Vgs foldback while current remains monotone. For those,
    bucket on pixel Y (current) so an X foldback cannot be collapsed into a
    diagonal jump or neighboring branch.
    """
    if not by_current:
        return _resample_vector_trace_pixels(raw, plot)
    transposed = [(y, x) for x, y in raw]
    transposed_plot = PlotBox(plot.y0, plot.x0, plot.y1, plot.x1)
    return [
        (x, y)
        for y, x in _resample_vector_trace_pixels(transposed, transposed_plot)
    ]


def _current_resampling_is_safe(
    raw: list[tuple[int, int]], plot: PlotBox
) -> bool:
    """Require source-order current monotonicity before Y-bucket resampling.

    Y-bucketing preserves a genuine Vgs foldback only when the drawing path is
    single-valued in current.  Some ordinary 3-temperature PDFs expose a
    chained component with both upward and downward Y traversal; resampling
    that component by current fabricates a horizontal backtrack at its tail.
    """
    if len(raw) < 2 or raw[-1][1] == raw[0][1]:
        return False
    direction = 1 if raw[-1][1] > raw[0][1] else -1
    backward = [
        abs(y1 - y0)
        for (_x0, y0), (_x1, y1) in zip(raw, raw[1:])
        if direction * (y1 - y0) < 0
    ]
    return max(backward, default=0) <= 2 and sum(backward) <= 0.02 * plot.height


def _run_points(
    run: list[VectorEdge],
    transform: CropTransform,
    plot: PlotBox,
    *,
    by_current: bool = False,
) -> list[tuple[int, int]]:
    raw = _edge_run_raw_pixels(run, transform)
    return _resample_transfer_pixels(
        raw,
        plot,
        by_current=by_current and _current_resampling_is_safe(raw, plot),
    )


def _edge_run_raw_pixels(
    run: list[VectorEdge], transform: CropTransform
) -> list[tuple[int, int]]:
    """Preserve source edge order while converting one vector run to pixels."""

    raw: list[tuple[int, int]] = []
    for edge in run:
        points = edge.points if edge.p1[0] >= edge.p0[0] else list(reversed(edge.points))
        pixels = [tuple(int(round(v)) for v in transform.to_px(x, y)) for x, y in points]
        raw.extend(pixels if not raw else pixels[1:])
    return raw


def _transfer_curve_edges(
    drawings, plot_rect, *, recover_clipped_cubics: bool = True
) -> list[VectorEdge]:
    """Recover transfer strokes clipped by the PDF plot frame.

    A few HXY charts draw each visible branch as a Bezier whose control path
    continues far outside the page and rely on the plot's clipping rectangle
    to expose only the in-frame tail.  The shared C(V) edge selector correctly
    rejects such a segment by its un-clipped midpoint, but doing so truncates a
    transfer branch at its knee.  Add only the contiguous in-frame portion of
    a rejected cubic, at high sampling density, and connect it to the already
    accepted source path.  Lines, grids, and fully accepted cubics are left to
    the shared selector.
    """
    edges = _vector_curve_edges(drawings, plot_rect)
    if not recover_clipped_cubics:
        return edges
    accepted = {
        (
            round(edge.p0[0], 4),
            round(edge.p0[1], 4),
            round(edge.p1[0], 4),
            round(edge.p1[1], 4),
        )
        for edge in edges
    }
    expanded = plot_rect + (-1.5, -1.5, 1.5, 1.5)
    for drawing in drawings:
        if drawing.get("type") != "s" or not _is_curve_stroke_color(
            drawing.get("color")
        ):
            continue
        width = float(drawing.get("width") or 0.0)
        if not 0.8 <= width <= 2.2:
            continue
        same_drawing_edges = _vector_curve_edges([drawing], plot_rect)
        for item in drawing.get("items", []):
            if item[0] != "c":
                continue
            control = [
                (float(point.x), float(point.y)) for point in item[1:]
            ]
            signature = (
                round(control[0][0], 4),
                round(control[0][1], 4),
                round(control[-1][0], 4),
                round(control[-1][1], 4),
            )
            if signature in accepted:
                continue
            sampled = _sample_cubic(*control, steps=1024)
            runs: list[list[tuple[float, float]]] = []
            current: list[tuple[float, float]] = []
            for point in sampled:
                if expanded.contains(point):
                    current.append(point)
                elif current:
                    runs.append(current)
                    current = []
            if current:
                runs.append(current)
            if not runs:
                continue
            in_frame = max(runs, key=len)
            if len(in_frame) < 2:
                continue
            # Only continue an accepted edge from the same source drawing, in
            # its existing tangent direction. Endpoint proximity alone is not
            # enough: an attached annotation/leader can begin exactly on a
            # real stroke and turn away from it.
            if not any(
                _continues_edge_tangent(in_frame, edge)
                for edge in same_drawing_edges
            ):
                continue
            edges.append(
                VectorEdge(p0=in_frame[0], p1=in_frame[-1], points=in_frame)
            )
    return edges


def _continues_edge_tangent(
    candidate: list[tuple[float, float]], edge: VectorEdge, tol: float = 0.8
) -> bool:
    """Require an attached candidate to continue, rather than turn away."""
    if len(candidate) < 2 or len(edge.points) < 2:
        return False
    joint = candidate[0]
    outgoing = next(
        (
            (point[0] - joint[0], point[1] - joint[1])
            for point in candidate[1:]
            if math.hypot(point[0] - joint[0], point[1] - joint[1]) > 1e-6
        ),
        None,
    )
    if outgoing is None:
        return False
    endpoint_sides = ((edge.p0, edge.points[1:]), (edge.p1, reversed(edge.points[:-1])))
    for endpoint, interiors in endpoint_sides:
        if math.hypot(joint[0] - endpoint[0], joint[1] - endpoint[1]) > tol:
            continue
        interior = next(
            (
                point
                for point in interiors
                if math.hypot(point[0] - endpoint[0], point[1] - endpoint[1]) > 1e-6
            ),
            None,
        )
        if interior is None:
            continue
        incoming = (endpoint[0] - interior[0], endpoint[1] - interior[1])
        denominator = math.hypot(*incoming) * math.hypot(*outgoing)
        if denominator and (
            incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
        ) / denominator >= 0.5:
            return True
    return False


def _calibrate_transfer(words_px, plot: PlotBox):
    """Fit VGS/ID axes using transfer-chart label gutters.

    The generic breakdown helper assumes compact tick labels immediately below
    the frame.  Renesas places the VGS numbers about 0.20 plot-heights below the
    frame to leave room for curve labels.  Keep the same strict shared fitter
    and residual gates, but use a transfer-specific evidenced gutter.
    """

    x_candidates = [
        (float(text), cx, cy)
        for text, cx, cy in words_px
        if NUM_RE.fullmatch(text)
        and plot.y1 + 0.005 * plot.height <= cy <= plot.y1 + 0.25 * plot.height
        and plot.x0 - 0.03 * plot.width <= cx <= plot.x1 + 0.14 * plot.width
    ]
    x_ticks = _select_horizontal_tick_row(x_candidates, plot)
    y_ticks = list(dict.fromkeys([
        (float(text), cy)
        for text, cx, cy in words_px
        if NUM_RE.fullmatch(text)
        and plot.x0 - 0.22 * plot.width <= cx <= plot.x0 - 2
        and plot.y0 - 0.02 * plot.height <= cy <= plot.y1 + 0.02 * plot.height
    ]))
    if len(y_ticks) < 4:
        raise RuntimeError(
            f"Y axis (ID): only {len(y_ticks)} tick labels, need >=4"
        )
    y_axis = fit_axis_ticks(
        [AxisTick(f"{value:g}", value, pixel) for value, pixel in y_ticks],
        "Y axis (ID)",
        model="auto",
    )
    strict_x = _fit_axis(x_ticks, "X axis (VGS)")
    x_axis = fit_axis_ticks(
        [AxisTick(f"{value:g}", value, pixel) for value, pixel in strict_x.ticks],
        "X axis (VGS)",
        model="linear",
    )
    return x_axis, y_axis


def _axis_tick_pairs(axis) -> list[tuple[float, float]]:
    """Return selected ticks as (value, pixel) for either shared axis type."""

    if isinstance(axis, NumericAxis):
        return [(tick.value, tick.pixel) for tick in axis.ticks]
    return list(axis.ticks)


def _axis_residual(axis) -> float:
    """Expose one diagnostic residual without changing calibration semantics."""

    return axis.residual_px if isinstance(axis, NumericAxis) else axis.resid


def _select_horizontal_tick_row(
    candidates: list[tuple[float, float, float]], plot: PlotBox
) -> list[tuple[float, float]]:
    """Select one evidenced tick-label row below a transfer plot.

    Conditions such as ``VDS = 5 V`` can sit farther below the frame inside the
    deliberately generous transfer gutter.  A tick row has at least four
    labels, spans most of the frame, and is monotone in pixel order; a lone
    condition number cannot join it merely because it is numeric.
    """

    tolerance = max(3.0, 0.025 * plot.height)
    rows: list[list[tuple[float, float, float]]] = []
    for candidate in sorted(candidates, key=lambda item: item[2]):
        for row in rows:
            if abs(candidate[2] - float(np.median([item[2] for item in row]))) <= tolerance:
                row.append(candidate)
                break
        else:
            rows.append([candidate])

    evidenced: list[tuple[float, list[tuple[float, float]]]] = []
    for row in rows:
        ordered = sorted(row, key=lambda item: item[1])
        if len(ordered) < 4:
            continue
        pixels = [item[1] for item in ordered]
        values = [item[0] for item in ordered]
        diffs = np.diff(values)
        if not (np.all(diffs > 0) or np.all(diffs < 0)):
            continue
        if pixels[-1] - pixels[0] < 0.50 * plot.width:
            continue
        row_y = float(np.median([item[2] for item in ordered]))
        evidenced.append(
            (abs(row_y - plot.y1), [(value, px) for value, px, _cy in ordered])
        )
    if not evidenced:
        raise RuntimeError("X axis (VGS): no monotone full-span tick-label row")
    evidenced.sort(key=lambda item: item[0])
    return evidenced[0][1]


def _extract_curves(
    page,
    transform: CropTransform,
    plot: PlotBox,
    fitz,
    expected_count: int,
) -> list[list[tuple[int, int]]]:
    if expected_count == 2:
        # Preserve the established two-temperature extraction byte-for-byte.
        # The component/Y-resampling path below exists only for source panels
        # that positively evidence three or more temperature labels.
        return _extract_two_curves_legacy(page, transform, plot, fitz)

    p0 = transform.to_pt(plot.x0, plot.y0)
    p1 = transform.to_pt(plot.x1, plot.y1)
    rect = fitz.Rect(p0[0], p0[1], p1[0], p1[1])
    drawings = page.get_drawings()
    edges = _transfer_curve_edges(
        drawings, rect, recover_clipped_cubics=expected_count >= 3
    )
    candidates: list[list[tuple[int, int]]] = []
    for component in _chain_vector_components(edges):
        candidate = _component_transfer_candidate(
            component, transform, plot, rect, expected_count
        )
        if candidate is not None:
            candidates.append(candidate)
    if len(candidates) != expected_count:
        # Infineon emits each short segment as a separate drawing.  Its two
        # branches touch at the baseline/crossing, so connected components
        # merge them.  Preserve the established drawing-order splitter as a
        # fallback; Renesas-style grouped paths use the component result above.
        candidates = []
        for run in _split_monotone_edge_runs(edges, rect.width):
            if len(run) < 8:
                continue
            x_span = max(max(edge.p0[0], edge.p1[0]) for edge in run) - min(
                min(edge.p0[0], edge.p1[0]) for edge in run
            )
            if x_span < MIN_RUN_X_SPAN * rect.width:
                continue
            points = _run_points(
                run, transform, plot, by_current=expected_count >= 3
            )
            if len(points) >= 8:
                candidates.append(points)
    if len(candidates) != expected_count:
        # Source-object rescue: some TI curves converge at the top frame, so
        # pooled endpoint chaining joins distinct temperature paths. Accept
        # only an exact one-candidate-per-drawing proof; two source paths under
        # three labels remain a terminal refusal and no phantom is synthesized.
        isolated: list[list[tuple[int, int]]] = []
        confirmed_widths: list[float] = []
        for drawing in drawings:
            candidate = _source_drawing_transfer_candidate(
                drawing, rect, transform, plot, expected_count
            )
            if candidate is not None:
                isolated.append(candidate)
                confirmed_widths.append(float(drawing.get("width") or 0.0))
        if len(isolated) == expected_count - 1 and confirmed_widths:
            gray_candidates = []
            for drawing in drawings:
                width = float(drawing.get("width") or 0.0)
                if not _is_neutral_gray_stroke(drawing.get("color")) or not any(
                    abs(width - confirmed) <= 0.05
                    for confirmed in confirmed_widths
                ):
                    continue
                candidate = _source_drawing_transfer_candidate(
                    drawing,
                    rect,
                    transform,
                    plot,
                    expected_count,
                    allow_neutral_gray=True,
                )
                if candidate is not None:
                    gray_candidates.append(candidate)
            if len(gray_candidates) == 1:
                isolated.extend(gray_candidates)
        # Preserve the evidenced source-object count in the refusal diagnostic.
        # An inexact count is still rejected below; it is never padded or split
        # to satisfy the number of temperature labels.
        candidates = isolated
    if len(candidates) != expected_count and expected_count >= 3:
        # Some Fairchild/onsemi charts draw data curves below the shared 0.8 pt
        # vector floor. Retry only source-contiguous 0.4..0.8 pt paths after all
        # established paths fail. Dotted grids and leaders cannot qualify: each
        # source object must contain at least eight endpoint-contiguous edges,
        # joined objects must meet at the same endpoint, and the recovered set
        # must exactly match the printed temperature count.
        thin_candidates = _thin_transfer_candidates(
            drawings, rect, transform, plot, expected_count
        )
        if thin_candidates:
            candidates = thin_candidates
    if len(candidates) != expected_count:
        raise RuntimeError(
            f"expected exactly {expected_count} left-to-right transfer curves, "
            f"found {len(candidates)}; "
            "do not guess temperature branches — verify the vector drawing order/crop"
        )
    return candidates


def _source_drawing_transfer_candidate(
    drawing,
    rect,
    transform: CropTransform,
    plot: PlotBox,
    expected_count: int,
    *,
    allow_neutral_gray: bool = False,
) -> list[tuple[int, int]] | None:
    """Return exactly one full transfer path owned by one source drawing."""

    edges = _vector_curve_edges(
        [drawing],
        rect,
        allow_neutral_gray=allow_neutral_gray,
    )
    candidates = [
        candidate
        for component in _chain_vector_components(edges)
        if (
            candidate := _component_transfer_candidate(
                component, transform, plot, rect, expected_count
            )
        ) is not None
    ]
    return candidates[0] if len(candidates) == 1 else None


def _thin_transfer_candidates(
    drawings,
    rect,
    transform: CropTransform,
    plot: PlotBox,
    expected_count: int,
) -> list[list[tuple[int, int]]]:
    """Recover exact-count thin paths without admitting same-width grid dots."""

    source_chunks: list[tuple[float, list[VectorEdge]]] = []
    for drawing in drawings:
        width = float(drawing.get("width") or 0.0)
        if (
            drawing.get("type") != "s"
            or not 0.4 <= width < 0.8
            or not _is_curve_stroke_color(drawing.get("color"))
        ):
            continue
        edges = _vector_curve_edges(
            [drawing], rect, min_stroke_width=0.4
        )
        ordered = _strictly_contiguous_edges(edges)
        if len(ordered) >= 8:
            source_chunks.append((width, ordered))

    runs: list[tuple[float, list[VectorEdge]]] = []
    for width, chunk in source_chunks:
        if runs:
            run_width, run = runs[-1]
            gap = math.hypot(
                run[-1].p1[0] - chunk[0].p0[0],
                run[-1].p1[1] - chunk[0].p0[1],
            )
            if (
                abs(width - run_width) <= 0.02
                and gap <= 0.05
                and chunk[0].p0[0] >= run[-1].p1[0] - 0.05
            ):
                run.extend(chunk)
                continue
        runs.append((width, list(chunk)))

    candidates: list[tuple[float, list[tuple[int, int]]]] = []
    for width, run in runs:
        x_span = run[-1].p1[0] - run[0].p0[0]
        ys = [point[1] for edge in run for point in edge.points]
        if x_span < MIN_RUN_X_SPAN * rect.width:
            continue
        if max(ys) - min(ys) < 0.08 * rect.height:
            continue
        raw = _edge_run_raw_pixels(run, transform)
        if not _current_resampling_is_safe(raw, plot):
            continue
        points = _resample_transfer_pixels(raw, plot, by_current=True)
        if len(points) >= 8:
            candidates.append((width, points))

    if len(candidates) != expected_count:
        return []
    widths = [width for width, _points in candidates]
    if max(widths) - min(widths) > 0.02:
        return []
    return [points for _width, points in candidates]


def _strictly_contiguous_edges(edges: list[VectorEdge]) -> list[VectorEdge]:
    """Orient one source object's edges and reject any dotted/disjoint run."""

    if not edges:
        return []
    ordered: list[VectorEdge] = []
    for edge in edges:
        options = (edge, _reverse_edge(edge))
        if ordered:
            candidate = min(
                options,
                key=lambda item: math.hypot(
                    ordered[-1].p1[0] - item.p0[0],
                    ordered[-1].p1[1] - item.p0[1],
                ),
            )
            gap = math.hypot(
                ordered[-1].p1[0] - candidate.p0[0],
                ordered[-1].p1[1] - candidate.p0[1],
            )
            if gap > 0.05 or candidate.p1[0] < candidate.p0[0] - 0.05:
                return []
        else:
            candidate = edge if edge.p1[0] >= edge.p0[0] else options[1]
        ordered.append(candidate)
    return ordered


def _reverse_edge(edge: VectorEdge) -> VectorEdge:
    return VectorEdge(
        p0=edge.p1,
        p1=edge.p0,
        points=list(reversed(edge.points)),
    )


def _component_transfer_candidate(
    component: list[tuple[float, float]],
    transform: CropTransform,
    plot: PlotBox,
    rect,
    expected_count: int,
) -> list[tuple[int, int]] | None:
    """Validate and rasterize one source-proven transfer path component."""

    if len(component) < 8:
        return None
    x_span = max(x for x, _ in component) - min(x for x, _ in component)
    y_span = max(y for _, y in component) - min(y for _, y in component)
    # Transfer curves are intentionally steep: their VGS span can be only
    # 10-25% of the plot width. Multi-temperature charts may also publish
    # successively lower saturation-current branches on the same axis.
    minimum_y_span = 0.08 if expected_count >= 3 else 0.60
    if x_span < 0.08 * rect.width or y_span < minimum_y_span * rect.height:
        return None
    raw = [
        tuple(int(round(value)) for value in transform.to_px(x, y))
        for x, y in component
    ]
    points = _resample_transfer_pixels(
        raw,
        plot,
        by_current=(expected_count >= 3 and _current_resampling_is_safe(raw, plot)),
    )
    return points if len(points) >= 8 else None


def _extract_two_curves_legacy(
    page, transform: CropTransform, plot: PlotBox, fitz
) -> list[list[tuple[int, int]]]:
    """Original two-curve drawing-order extractor.

    This deliberately does not use the multi-temperature component recovery:
    changing a shared two-curve path would move previously reviewed raw
    points even when the new capability is not needed.
    """

    p0 = transform.to_pt(plot.x0, plot.y0)
    p1 = transform.to_pt(plot.x1, plot.y1)
    rect = fitz.Rect(p0[0], p0[1], p1[0], p1[1])
    edges = _vector_curve_edges(page.get_drawings(), rect)
    candidates: list[list[tuple[int, int]]] = []
    for run in _split_monotone_edge_runs(edges, rect.width):
        points = _run_points(run, transform, plot)
        if len(points) < 8:
            continue
        span = max(x for x, _ in points) - min(x for x, _ in points)
        if span >= MIN_RUN_X_SPAN * plot.width:
            candidates.append(points)
    if len(candidates) != 2:
        raise RuntimeError(
            f"expected exactly 2 left-to-right transfer curves, found {len(candidates)}; "
            "do not guess temperature branches — verify the vector drawing order/crop"
        )
    return candidates


def _extract_two_curves(page, transform: CropTransform, plot: PlotBox, fitz):
    """Backward-compatible two-curve wrapper for focused callers/tests."""

    return _extract_curves(page, transform, plot, fitz, 2)


def _extract_panel_curves(
    page,
    transform: CropTransform,
    gray: np.ndarray,
    words: list[tuple[str, float, float]],
    fitz,
    expected_count: int,
):
    """Calibrate transfer axes and preserve the legacy two-curve ordering.

    Curve ordering remains intentionally split by temperature count, but axis
    semantics do not: a two-temperature transfer chart can use the same log ID
    axis as a three-temperature chart.  The old two-curve branch called the
    breakdown-voltage linear fitter, which both mislabeled the diagnostic and
    refused valid 1/10/100/1000 A ticks.
    """
    plot = _vector_plot_frame(page, transform, gray.shape) or find_closed_frame_plot_box(gray)
    x_axis, y_axis = _calibrate_transfer(words, plot)
    major_x, major_y = _full_span_grid_lines(gray, plot, plot)
    x_axis = _snap_axis_to_grid(x_axis, major_x, "X axis (VGS)", authoritative=True)
    y_axis = _snap_axis_to_grid(y_axis, major_y, "Y axis (ID)", authoritative=True)
    pixel_curves = _extract_curves(page, transform, plot, fitz, expected_count)
    return plot, x_axis, y_axis, pixel_curves


def _normalize_temperature_text(text: str) -> str:
    normalized = (
        text.replace("−", "-")
        .replace("–", "-")
        .replace("‑", "-")
        # Some TI PDFs encode the printed degree sign as a private-use glyph.
        # Normalize that exact glyph before applying the strict label grammar.
        .replace("\uf0b0", "°")
    )
    # pdftotext can place the subscript C from ``TC`` after its temperature,
    # yielding ``25°C C``. Collapse only that duplicated unit glyph.
    return re.sub(r"(°?\s*C)\s+C\b", r"\1", normalized, flags=re.I)


def _temperatures(text: str) -> list[float]:
    normalized = _normalize_temperature_text(text)
    contextual = {
        float(value)
        for value in re.findall(
            r"\bT\s*(?:C|J)?\s*=\s*([+-]?\d+(?:\.\d+)?)\s*°?\s*C?",
            normalized,
            flags=re.I,
        )
    }
    if 2 <= len(contextual) <= 6:
        return sorted(contextual)
    values = sorted({float(v) for v in TEMP_RE.findall(normalized)})
    if not 2 <= len(values) <= 6:
        raise RuntimeError(f"expected 2..6 temperature labels, found {values}")
    return values


def _positioned_temperature_labels(
    page,
    transform: CropTransform,
    plot: PlotBox,
) -> list[tuple[float, tuple[float, float, float, float]]]:
    """Return strict, owned temperature-label rectangles in crop pixels.

    Curve labels must be standalone source-text lines such as ``Tj=150°C`` or
    ``25 °C`` whose centers lie inside the owned plot.  This intentionally does
    not follow adjacent horizontal strokes: on IRF6644 those apparent leaders
    are full-width log-grid lines, not label-to-curve provenance.
    """

    lines: dict[tuple[int, int], list[tuple]] = {}
    for word in page.get_text("words"):
        if len(word) < 8:
            continue
        lines.setdefault((int(word[5]), int(word[6])), []).append(word)

    candidates: list[
        tuple[float, tuple[float, float, float, float], str]
    ] = []
    for words in lines.values():
        ordered = sorted(words, key=lambda word: int(word[7]))
        line_text = _normalize_temperature_text(
            " ".join(str(word[4]) for word in ordered)
        )
        match = POSITIONED_TEMP_LINE_RE.fullmatch(line_text)
        if match is None:
            continue
        # A shared operating condition such as ``, VDS = 4.5 V`` is evidence
        # that both labels belong to the same curve family, but it must not
        # shift the label rectangle toward one branch.  Own only the shortest
        # word prefix that contains the complete temperature label.
        prefix_count = next(
            (
                count
                for count in range(1, len(ordered) + 1)
                if POSITIONED_TEMP_PREFIX_RE.fullmatch(
                    _normalize_temperature_text(
                        " ".join(str(word[4]) for word in ordered[:count])
                    )
                )
            ),
            None,
        )
        if prefix_count is None:
            continue
        label_words = ordered[:prefix_count]
        x0_pt = min(float(word[0]) for word in label_words)
        y0_pt = min(float(word[1]) for word in label_words)
        x1_pt = max(float(word[2]) for word in label_words)
        y1_pt = max(float(word[3]) for word in label_words)
        x0, y0 = transform.to_px(x0_pt, y0_pt)
        x1, y1 = transform.to_px(x1_pt, y1_pt)
        rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        cx = 0.5 * (rect[0] + rect[2])
        cy = 0.5 * (rect[1] + rect[3])
        if plot.x0 <= cx <= plot.x1 and plot.y0 <= cy <= plot.y1:
            condition = re.sub(
                r"\s+", "", str(match.group("condition") or "")
            ).lower()
            candidates.append(
                (float(match.group("temperature")), rect, condition)
            )
    conditions = {condition for _temperature, _rect, condition in candidates}
    if len(conditions) > 1:
        # Mixing bare labels with conditioned labels, or using different
        # conditions, leaves curve-family ownership ambiguous.
        return []
    return [
        (temperature, rect) for temperature, rect, _condition in candidates
    ]


def _label_curve_distance(
    rect: tuple[float, float, float, float],
    curve: list[tuple[int, int]],
) -> float:
    """Minimum distance from a label center to a curve path.

    Curve ink can pass through a text knockout's outer rectangle, making both
    branches have zero rectangle distance.  The printed label center retains
    the intended branch separation while the bounded-distance and assignment
    margin gates still reject a label placed between two curves.
    """

    x0, y0, x1, y1 = rect
    center_x = 0.5 * (x0 + x1)
    center_y = 0.5 * (y0 + y1)
    return min(
        math.hypot(center_x - x, center_y - y)
        for x, y in curve
    )


def _bind_two_temperature_labels(
    pixel_curves: list[list[tuple[int, int]]],
    labels: list[tuple[float, tuple[float, float, float, float]]],
    temperatures: list[float],
    plot: PlotBox,
) -> list[tuple[float, int]]:
    """Bind exactly two owned source labels to two curves or refuse.

    Both label-to-curve matches must be individually nearest, close to source
    ink, and jointly better than the swapped assignment by a visible margin.
    An ambiguous label layout is terminal; the high-VGS physical heuristic is
    deliberately not used as a fallback.
    """

    if len(pixel_curves) != 2 or len(labels) != 2:
        raise RuntimeError(
            "two-curve temperature assignment requires exactly 2 owned "
            f"source labels and 2 curves; found {len(labels)} labels and "
            f"{len(pixel_curves)} curves"
        )
    if sorted(value for value, _rect in labels) != sorted(temperatures):
        raise RuntimeError(
            "two-curve positioned temperature labels do not match the "
            "panel temperature values"
        )

    costs = [
        [_label_curve_distance(rect, curve) for curve in pixel_curves]
        for _temperature, rect in labels
    ]
    direct = costs[0][0] + costs[1][1]
    swapped = costs[0][1] + costs[1][0]
    if direct <= swapped:
        curve_indices = [0, 1]
        best, alternative = direct, swapped
    else:
        curve_indices = [1, 0]
        best, alternative = swapped, direct

    total_margin = max(4.0, 0.04 * plot.width)
    per_label_margin = max(2.0, 0.01 * plot.width)
    maximum_distance = 0.20 * min(plot.width, plot.height)
    ambiguous = False
    for label_index, curve_index in enumerate(curve_indices):
        chosen = costs[label_index][curve_index]
        other = costs[label_index][1 - curve_index]
        if chosen > maximum_distance or other - chosen < per_label_margin:
            ambiguous = True
    if alternative - best < total_margin:
        ambiguous = True
    if ambiguous:
        outer = bind_opposite_outer_labels(pixel_curves, labels, plot.width)
        if outer is not None:
            curve_indices = outer
        else:
            raise RuntimeError(
                "two-curve temperature label/curve binding is ambiguous; "
                "refusing physical branch inference"
            )
    return sorted(
        (
            (temperature, curve_indices[label_index])
            for label_index, (temperature, _rect) in enumerate(labels)
        ),
        key=lambda item: item[0],
    )


def _validate_transfer_panel_semantics(
    chart: dict, source_words: list[tuple[str, float, float]] | None = None
) -> None:
    """Fail closed unless title and owned panel text describe Id(Vgs)."""

    title = str(chart.get("title", "")).lower()
    source_text = " ".join(word[0] for word in source_words or [])
    text = f"{chart.get('text', '')} {source_text}".lower()
    compact = re.sub(r"[^a-z0-9]+", "", text)
    alpha_compact = re.sub(r"[^a-z]+", "", text)
    formula_owned = compact_formula_chart_kind(title) == "transfer"
    if not formula_owned and ("transfer" not in title or "characteristic" not in title):
        raise RuntimeError("source title does not identify transfer characteristics")
    if any(token in compact for token in ("ciss", "coss", "crss", "capacitance")):
        raise RuntimeError("capacitance semantics contradict transfer-panel ownership")
    has_gate_axis = formula_owned or "vgs" in compact or "gatetosourcevoltage" in alpha_compact
    # PDF text order commonly exposes a vertical ``I_D`` label as ``D I``.
    # Accept that exact axis glyph ordering, not arbitrary curve shape.
    has_current_axis = (
        formula_owned
        or "id" in compact
        or "di" in compact
        or "draincurrent" in alpha_compact
        or "draintosourcecurrent" in alpha_compact
        or "currentdraintosource" in alpha_compact
    )
    if not (has_gate_axis and has_current_axis):
        raise RuntimeError(
            "transfer panel lacks owned VGS and ID axis evidence; refusing curve-shape inference"
        )


def _assign_temperatures(
    curves: list[list[tuple[float, float]]],
    temperatures: list[float],
    *,
    pixel_curves: list[list[tuple[int, int]]] | None = None,
    positioned_labels: list[
        tuple[float, tuple[float, float, float, float]]
    ] | None = None,
    plot: PlotBox | None = None,
) -> list[TransferCurve]:
    """Assign temperature identities from source labels, then validate ZTC."""
    if len(curves) != len(temperatures):
        raise RuntimeError(
            f"curve/temperature count mismatch: {len(curves)} curves, "
            f"{len(temperatures)} labels"
        )
    if len(curves) > 2:
        # Below the ZTC current, a hotter MOSFET's lower threshold places its
        # transfer curve to the LEFT.  Rank all branches at one shared low
        # current, then bind left-to-right to hot-to-cold.  This supports the
        # common -25/25/75 C family without selecting by PDF drawing order.
        common_hi = min(max(current for _, current in points) for points in curves)
        if common_hi <= 0:
            raise RuntimeError("transfer curves have no common positive-current range")
        rank_currents = np.array(
            [max(0.5, 0.12 * common_hi), max(0.75, 0.18 * common_hi)]
        )
        ranks: list[list[list[tuple[float, float]]]] = []
        for rank_current in rank_currents:
            at_current = sorted(
                curves,
                key=lambda points: float(
                    _inverse_vgs(points, np.array([rank_current]))[0]
                ),
            )
            gates = [
                float(_inverse_vgs(points, np.array([rank_current]))[0])
                for points in at_current
            ]
            if any(right <= left for left, right in zip(gates, gates[1:])):
                raise RuntimeError(
                    "multi-temperature transfer branches are not strictly separated "
                    "at a shared low current; temperature assignment is untrusted"
                )
            ranks.append(at_current)
        ranked = ranks[0]
        if any(id(left) != id(right) for left, right in zip(ranked, ranks[1])):
            raise RuntimeError(
                "multi-temperature transfer branches change low-current order; "
                "temperature assignment is untrusted"
            )
        assigned = [
            TransferCurve(tj_c, points)
            for tj_c, points in zip(sorted(temperatures, reverse=True), ranked)
        ]
        return sorted(assigned, key=lambda curve: curve.tj_c)

    if pixel_curves is None or positioned_labels is None or plot is None:
        raise RuntimeError(
            "two-curve temperature assignment requires owned positioned labels"
        )
    bindings = _bind_two_temperature_labels(
        pixel_curves, positioned_labels, temperatures, plot
    )
    assigned = [
        TransferCurve(temperature, curves[curve_index])
        for temperature, curve_index in bindings
    ]
    cold, hot = assigned
    validate_two_curve_order(cold.points, hot.points)
    return assigned


def fit_saturation_tempco(curves: list[TransferCurve], anchor: dict[str, float]) -> dict:
    """Fit dVth_eff/dT and d(log K)/dT around an exact 25 C anchor."""
    curves = sorted(curves, key=lambda curve: curve.tj_c)
    tref = float(anchor.get("tref_c", 25.0))
    reference = next(
        (curve for curve in curves if abs(curve.tj_c - tref) <= 1e-6), None
    )
    if reference is None:
        raise RuntimeError(f"no transfer curve matches anchor Tref={tref:g} C")
    hotter = [curve for curve in curves if curve.tj_c > tref + 1e-6]
    if not hotter:
        raise RuntimeError(f"no transfer curve is hotter than anchor Tref={tref:g} C")
    hot = hotter[-1]
    dt = hot.tj_c - reference.tj_c
    if dt <= 0:
        raise RuntimeError("transfer temperatures are not increasing")
    required = ("vth_eff_v", "k_a_per_vp", "p", "id_gc_a", "vpl_v")
    missing = [key for key in required if key not in anchor]
    if missing:
        raise RuntimeError(f"temperature fit anchor missing: {', '.join(missing)}")
    vth = float(anchor["vth_eff_v"])
    k = float(anchor["k_a_per_vp"])
    p = float(anchor["p"])
    id_gc = float(anchor["id_gc_a"])
    vpl = float(anchor["vpl_v"])
    if not (k > 0 and p > 1 and id_gc > 0 and vpl > vth):
        raise RuntimeError(f"nonphysical channel anchor: {anchor}")
    pivot_err = abs(k * (vpl - vth) ** p - id_gc) / id_gc
    if pivot_err > 1e-6:
        raise RuntimeError(
            f"25 C anchor does not reproduce (Vpl,Id_gc): relative error {pivot_err:.3g}"
        )

    max_common_i = min(max(i for _, i in reference.points), max(i for _, i in hot.points))
    i_lo = max(5.0, 0.10 * id_gc)
    i_hi = min(2.0 * id_gc, 0.85 * max_common_i)
    if i_hi <= 2.0 * i_lo:
        raise RuntimeError(
            f"insufficient matched-current span for temperature fit: {i_lo:g}..{i_hi:g} A"
        )
    currents = np.linspace(i_lo, i_hi, 120)
    v_cold = _inverse_vgs(reference.points, currents)
    v_hot = _inverse_vgs(hot.points, currents)
    delta = v_hot - v_cold
    ov_model = np.power(currents / k, 1.0 / p)
    design = np.column_stack((np.ones_like(ov_model), ov_model))
    d_vth, shape = np.linalg.lstsq(design, delta, rcond=None)[0]
    scale_root = 1.0 + float(shape)
    if scale_root <= 0:
        raise RuntimeError("temperature fit implies a nonpositive K scaling root")
    k_ratio = scale_root ** (-p)
    prediction = design @ np.array([d_vth, shape])
    residual = prediction - delta
    fit_rms = float(np.sqrt(np.mean(residual**2)))
    fit_max = float(np.max(np.abs(residual)))
    if fit_rms > MAX_TEMPCO_FIT_RMS_V:
        raise RuntimeError(
            f"compact Vth_eff/K temperature law misses matched-current gate shifts: "
            f"RMS {fit_rms:.3f} V > {MAX_TEMPCO_FIT_RMS_V:.3f} V"
        )

    cold_model_vgs = vth + ov_model
    cold_residual = cold_model_vgs - v_cold
    cold_rms = float(np.sqrt(np.mean(cold_residual**2)))
    cold_max = float(np.max(np.abs(cold_residual)))
    conflict = cold_rms > MAX_COLD_CONFLICT_RMS_V or cold_max > MAX_COLD_CONFLICT_ABS_V

    ztc_model = None
    if abs(shape) > 1e-12 and -float(d_vth) / float(shape) > 0:
        ztc_model = k * (-float(d_vth) / float(shape)) ** p
    # Locate the chart ZTC over the full reliable common-current span, not only
    # the operating-point fit window.  Several parts cross above 2*Id_gc; that
    # crossing is still valuable validation of the two-parameter law.
    ztc_currents = np.linspace(max(1.0, 0.02 * id_gc), 0.95 * max_common_i, 400)
    ztc_delta = _inverse_vgs(hot.points, ztc_currents) - _inverse_vgs(
        reference.points, ztc_currents
    )
    sign = np.sign(ztc_delta)
    crossings = np.flatnonzero(sign[:-1] * sign[1:] <= 0)
    ztc_chart = None
    if len(crossings):
        j = int(crossings[0])
        pair_delta = [ztc_delta[j], ztc_delta[j + 1]]
        pair_current = [ztc_currents[j], ztc_currents[j + 1]]
        if pair_delta[0] > pair_delta[1]:
            pair_delta.reverse()
            pair_current.reverse()
        ztc_chart = float(np.interp(0.0, pair_delta, pair_current))

    return {
        "tref_c": tref,
        "thot_c": hot.tj_c,
        "fit_current_range_a": [round(i_lo, 3), round(i_hi, 3)],
        "d_vth_eff_v_per_k": float(d_vth) / dt,
        "d_log_k_per_k": math.log(k_ratio) / dt,
        "k_ratio_hot_to_ref": k_ratio,
        "matched_shift_fit_rms_v": fit_rms,
        "matched_shift_fit_max_v": fit_max,
        "cold_anchor_check_rms_v": cold_rms,
        "cold_anchor_check_max_v": cold_max,
        "cold_anchor_conflict": conflict,
        "ztc_chart_a": ztc_chart,
        "ztc_model_a": ztc_model,
        "anchor": dict(anchor),
    }


def _draw_overlay(
    image: np.ndarray,
    plot: PlotBox,
    pixel_curves: list[list[tuple[int, int]]],
    curves: list[TransferCurve],
    x_axis,
    y_axis,
    fit: dict | None,
) -> np.ndarray:
    overlay = image.copy()
    draw_plot_frame(overlay, plot, (0, 180, 0))
    by_temperature = sorted(zip(curves, pixel_curves), key=lambda item: item[0].tj_c)
    colors = (
        [(0, 0, 255), (255, 80, 0)]
        if len(by_temperature) == 2
        else [
            (255, 80, 0),
            (0, 155, 0),
            (0, 0, 255),
            (180, 0, 180),
            (0, 150, 220),
            (120, 80, 20),
        ]
    )
    for (curve, points), color in zip(by_temperature, colors):
        for a, b in zip(points, points[1:]):
            cv2.line(overlay, a, b, color, 2, cv2.LINE_AA)
        cv2.putText(
            overlay,
            f"{curve.tj_c:g} C extracted",
            (plot.x0 + 10, plot.y0 + 24 + 22 * colors.index(color)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    # Self-contained calibration evidence: label every selected tick directly on
    # the plot so the reviewer needn't infer which glyph each guide used.
    # LinearAxis.ticks are (value, pixel); the shared renderer wants (pixel, value).
    tick_color = (180, 0, 180)
    draw_axis_ticks(
        overlay,
        plot,
        x_ticks=[(px, value) for value, px in _axis_tick_pairs(x_axis)],
        y_ticks=[(py, value) for value, py in _axis_tick_pairs(y_axis)],
        color=tick_color,
        marker_size=8,
        font_scale=0.35,
    )

    def boxed_axis_label(text: str, origin: tuple[int, int]) -> None:
        scale = 0.60
        thickness = 2
        (width, height), baseline = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness
        )
        x, y = origin
        cv2.rectangle(
            overlay,
            (x - 4, y - height - 4),
            (x + width + 4, y + baseline + 4),
            (255, 255, 255),
            cv2.FILLED,
        )
        cv2.rectangle(
            overlay,
            (x - 4, y - height - 4),
            (x + width + 4, y + baseline + 4),
            tick_color,
            1,
        )
        cv2.putText(
            overlay,
            text,
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            tick_color,
            thickness,
            cv2.LINE_AA,
        )

    boxed_axis_label("VGS [V]", (plot.x1 - 91, plot.y1 - 24))
    boxed_axis_label("ID [A]", (plot.x0 + 8, plot.y0 + 68))
    if fit is not None:
        text = (
            f"dVth/dT={fit['d_vth_eff_v_per_k']*1e3:+.2f}mV/K  "
            f"dlnK/dT={fit['d_log_k_per_k']*1e3:+.3f}e-3/K  "
            f"shift RMS={fit['matched_shift_fit_rms_v']:.3f}V"
        )
        cv2.putText(
            overlay,
            text,
            (plot.x0 + 8, max(18, plot.y0 - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (0, 90, 0),
            1,
            cv2.LINE_AA,
        )
    return overlay


def process_chart(
    chart: dict,
    crop_path: Path,
    out_dir: Path,
    rel_stem: Path,
    anchor: dict[str, float] | None = None,
) -> dict:
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"could not read crop {crop_path}")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    transform = CropTransform.for_chart(chart, gray.shape)
    doc = fitz.open(str(chart["pdf"]))
    try:
        page = doc[int(chart["page"]) - 1]
        words = _words_in_crop_px(page, transform, gray.shape)
        source_semantics_recovered = False
        semantic_refusal: RuntimeError | None = None
        try:
            _validate_transfer_panel_semantics(chart)
        except RuntimeError as original_semantic_error:
            semantic_refusal = original_semantic_error
            try:
                temperatures = _temperatures(str(chart.get("text", "")))
                _validate_transfer_panel_semantics(chart, words)
            except RuntimeError:
                raise semantic_refusal from None
            source_semantics_recovered = True
        else:
            try:
                temperatures = _temperatures(str(chart.get("text", "")))
            except RuntimeError as temperature_refusal:
                # The established two-curve path checked plot/calibration/trace
                # integrity before temperature labels. Preserve that refusal
                # precedence only for its source-proven grid refusal class;
                # newer calibration probes must not replace a terminal label
                # refusal on unrelated/misclassified panels.
                try:
                    _extract_panel_curves(page, transform, gray, words, fitz, 2)
                except RuntimeError as legacy_refusal:
                    if str(legacy_refusal).startswith(
                        "could not find plot grid verticals"
                    ):
                        raise
                raise temperature_refusal
        try:
            plot, x_axis, y_axis, pixel_curves = _extract_panel_curves(
                page, transform, gray, words, fitz, len(temperatures)
            )
            positioned_labels = (
                _positioned_temperature_labels(page, transform, plot)
                if len(temperatures) == 2
                else None
            )
        except RuntimeError:
            if source_semantics_recovered:
                assert semantic_refusal is not None
                raise semantic_refusal from None
            raise
    finally:
        doc.close()

    try:
        calibrated = []
        for points in pixel_curves:
            values = [
                (x_axis.value(x), max(0.0, y_axis.value(y))) for x, y in points
            ]
            calibrated.append(
                sorted(values, key=lambda point: point[1])
                if len(temperatures) >= 3
                else sorted(values)
            )
        curves = _assign_temperatures(
            calibrated,
            temperatures,
            pixel_curves=pixel_curves,
            positioned_labels=positioned_labels,
            plot=plot,
        )
    except RuntimeError:
        if source_semantics_recovered:
            assert semantic_refusal is not None
            raise semantic_refusal from None
        raise
    # _assign_temperatures preserves each points-list object, so retain that
    # identity instead of matching by sampled current values.  Both traces can
    # legitimately clip at the same top-frame current.
    pixel_by_identity = {id(data): pixels for data, pixels in zip(calibrated, pixel_curves)}
    assigned_pixels = [pixel_by_identity[id(curve.points)] for curve in curves]
    fit = fit_saturation_tempco(curves, anchor) if anchor is not None else None

    csv_path = out_dir / "points" / rel_stem.parent / f"{rel_stem.name}.transfer_points.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Tj_C", "Vgs_V", "Id_A"])
        for curve in curves:
            for vgs, current in curve.points:
                writer.writerow([f"{curve.tj_c:.2f}", f"{vgs:.5f}", f"{current:.5f}"])

    overlay = _draw_overlay(image, plot, assigned_pixels, curves, x_axis, y_axis, fit)
    overlay_path = out_dir / "overlays" / rel_stem.parent / f"{rel_stem.name}.transfer_overlay.png"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)

    warnings = [
        "HUMAN REVIEW REQUIRED: verify axis guides, all temperature centerlines, "
        "temperature assignment, and fit summary before curating coefficients"
    ]
    if fit and fit["cold_anchor_conflict"]:
        warnings.append(
            "GUARD REFUSAL: exact 25 C (Vpl,Id_gc) law and absolute 25 C transfer chart "
            "disagree beyond the provisional residual bound; do not curate this fit"
        )
    status = "guard-refusal-cold-anchor-conflict" if fit and fit["cold_anchor_conflict"] else (
        "overlay-review-required"
    )
    return {
        "status": status,
        "temperatures_c": [curve.tj_c for curve in curves],
        "n_points": {f"{curve.tj_c:g}C": len(curve.points) for curve in curves},
        "calibration": {
            "x_ticks": len(x_axis.ticks),
            "x_resid": round(_axis_residual(x_axis), 5),
            "y_ticks": len(y_axis.ticks),
            "y_resid": round(_axis_residual(y_axis), 5),
        },
        "fit": fit,
        "warnings": warnings,
        "csv": str(csv_path),
        "overlay": str(overlay_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("charts_json", type=Path, help="charts.json from `dsdig find`")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--anchors-json",
        type=Path,
        help="optional {part:{vth_eff_v,k_a_per_vp,p,id_gc_a,vpl_v,tref_c}} map",
    )
    args = parser.parse_args()
    base_dir = args.charts_json.parent
    out_dir = args.out or base_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    charts = json.loads(args.charts_json.read_text())
    anchors = json.loads(args.anchors_json.read_text()) if args.anchors_json else {}
    results: list[dict] = []
    errors: list[dict] = []
    for chart in charts:
        if chart.get("kind") != "transfer":
            continue
        crop_rel = Path(chart["crop_png"])
        print(f"digitize {chart['part']} diagram {chart['diagram']}: {crop_rel}")
        try:
            result = process_chart(
                chart,
                base_dir / crop_rel,
                out_dir,
                crop_rel.with_suffix(""),
                anchors.get(chart["part"]),
            )
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors.append({"part": chart.get("part"), "crop": str(crop_rel), "error": str(exc)})
        else:
            result.update(
                {
                    "part": chart["part"],
                    "page": chart["page"],
                    "diagram": chart["diagram"],
                    "pdf": chart["pdf"],
                }
            )
            results.append(result)
            if result["fit"]:
                f = result["fit"]
                print(
                    f"  dVth/dT={f['d_vth_eff_v_per_k']*1e3:+.3f} mV/K, "
                    f"dlnK/dT={f['d_log_k_per_k']:+.6g}/K, "
                    f"shift RMS={f['matched_shift_fit_rms_v']:.4f} V"
                )
            print(f"  STATUS: {result['status']}")
    manifest = {"panels": results, "errors": errors}
    path = out_dir / "transfer_characteristics_digitization.json"
    path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    print(f"wrote {path}")
    if errors or not results:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
