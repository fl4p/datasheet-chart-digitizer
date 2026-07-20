"""Digitize drain-source breakdown-voltage charts (V(BR)DSS vs Tj).

Targets the Infineon "Diagram 15: Drain-source breakdown voltage" style: a
single stroked vector line on a linear/linear grid, V(BR)DSS on Y versus
junction temperature on X (typically -75..200 C), condition ID=1 mA.

Differences from the existing pipelines that justify a plugin:
  * both axes are LINEAR and X ticks are NEGATIVE numbers — the C(V)
    position calibration (linear-X digits only / log-Y decades) cannot fit
    them,
  * exactly ONE curve is expected — the C(V) vector extractor demands three
    full-span candidates and refuses this chart,
  * the value downstream consumers need is not the trace itself but the
    fitted line (V at 25 C, slope in mV/K) plus a spec-table anchor verdict.
    A normalized chart uses the V(BR)DSS minimum as its numeric scale; an
    absolute-axis chart is calibrated directly from its printed voltage ticks
    and the table minimum is only a one-sided sanity bound (a typical curve
    may legitimately sit above the guaranteed minimum).

Usage:
    dsdig digitize-breakdown-voltage work/charts/charts.json --out work/bv

Outputs per panel: an overlay PNG (digitized line + calibration ticks), a
calibrated (Tj, V) CSV, and breakdown_voltage_digitization.json with fit,
anchor verdict, and calibration diagnostics.

Anchor verdicts are tri-state and absence never passes:
  * "verified"   — normalized chart matches the parsed spec minimum, or an
    absolute-axis curve has the correct polarity and meets that minimum,
  * "FAIL"       — spec minimum found but the chart contradicts it,
  * "unverified" — no spec minimum found; the values are NOT validated.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .capacitance_traces import find_plot_box
from .capacitance_types import PlotBox
from .capacitance_vector import (
    _chain_vector_components,
    _filled_path_centerline,
    _is_dark_stroke,
    _load_fitz,
    _path_length,
    _resample_vector_trace_pixels,
    _vector_curve_edges,
)
from .crop_transform import CropTransform
from .diode_forward_voltage import _full_span_grid_lines
from .numeric_axis import AxisTick, fit_axis_ticks
from .overlay import draw_axis_ticks, draw_plot_frame
from .region_ocr import ocr_words_in_rect

INT_RE = re.compile(r"^-?\d+$")
NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
# Parameter-table row. Vendors use both V(BR)DSS and BVDSS.  Values occur
# either in a min/typ/max dash slot or as the last standalone voltage after
# test conditions.  Narrative conditions and chart-axis ticks must not be
# mistaken for the rating.
SPEC_ROW_RE = re.compile(
    r"(?:V\s*\(\s*BR\s*\)\s*DSS|BV\s*DSS)(.{0,120})",
    re.IGNORECASE,
)
SPEC_LABEL_ROW_RE = re.compile(
    r"drain[-\s]*source\s+breakdown\s+voltage(?:\s+\d+\))?(.{0,120})",
    re.IGNORECASE,
)
SPEC_DASH_VALUE_RE = re.compile(
    r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*(?:[-–—]+\s*){1,3}V\b",
    re.IGNORECASE,
)
# Hard gates. Vector tick text is exact, so calibration residuals beyond a
# small fraction of the labeled span mean the label-position pairing is wrong,
# not merely noisy — refuse rather than emit shifted values.
MAX_CAL_RESID_FRAC = 0.02
MIN_TICKS_PER_AXIS = 4
MIN_X_SPAN_FRAC = 0.5
ANCHOR_TOL = 0.02


@dataclass
class LinearAxis:
    """value = m * px + b, fitted from tick-label positions."""

    m: float
    b: float
    ticks: list[tuple[float, float]]  # (value, px)
    resid: float                      # max |fit - label| in value units

    def value(self, px: float) -> float:
        return self.m * px + self.b


def _fit_axis(ticks: list[tuple[float, float]], what: str) -> LinearAxis:
    """Linear pixel->value calibration via the ONE shared numeric_axis fitter.

    Delegates the least-squares fit to ``numeric_axis.fit_axis_ticks`` instead of
    re-deriving np.polyfit + the monotonicity check here (axis-fitter
    consolidation), then keeps breakdown's own policy on top: a stricter
    ``>=MIN_TICKS_PER_AXIS`` (4) min-count and the value-space residual gate (max
    abs error <= MAX_CAL_RESID_FRAC of the value span). V(BR)/Tj axes are always
    linear, so the fit is FORCED linear (``model="linear"``) — this is required,
    not cosmetic: a valid narrow-positive V axis is near-indistinguishable from
    log, and the shared fitter's "auto" ambiguity gate would false-refuse it. A
    genuinely non-linear tick set forced to linear still fails the residual gate.
    Ticks are (value, pixel) pairs; breakdown raises on any refusal (transfer
    relies on that)."""
    # dedup exact-duplicate (value, pixel) ticks (doubled text layers) so the
    # shared fitter's strict-monotone gate doesn't reject a zero gap; a
    # same-value/different-pixel or same-pixel/different-value pair is a real
    # conflict and stays fail-closed (fit_axis_ticks raises non-monotone).
    ticks = list(dict.fromkeys(ticks))
    if len(ticks) < MIN_TICKS_PER_AXIS:
        raise RuntimeError(f"{what}: only {len(ticks)} tick labels, need >={MIN_TICKS_PER_AXIS}")
    fit = fit_axis_ticks([AxisTick(f"{v:g}", v, px) for v, px in ticks], what, model="linear")
    values = np.array([v for v, _ in ticks], dtype=float)
    pixels = np.array([p for _, p in ticks], dtype=float)
    order = np.argsort(pixels)
    values, pixels = values[order], pixels[order]
    resid = float(np.max(np.abs(fit.m * pixels + fit.b - values)))
    span = float(np.max(values) - np.min(values))
    if span <= 0 or resid > MAX_CAL_RESID_FRAC * span:
        raise RuntimeError(
            f"{what}: calibration residual {resid:.3g} exceeds {MAX_CAL_RESID_FRAC:.0%} "
            f"of the {span:g}-unit tick span — label/position pairing untrusted"
        )
    return LinearAxis(float(fit.m), float(fit.b), [(float(v), float(p)) for v, p in zip(values, pixels)], resid)


def _calibrate(
    words_px: list[tuple[str, float, float]],
    plot: PlotBox,
    img_h: int,
    x_pattern=INT_RE,
    x_ticks_override: list[tuple[float, float]] | None = None,
) -> tuple[LinearAxis, LinearAxis]:
    """Fit X (Tj) and Y (V) axes from tick-label words in crop-pixel space."""
    # Some caption-above layouts leave a slightly taller label gutter below
    # the frame.  The horizontal own-frame bound still rejects neighboring
    # panels, so admit labels through 8% of crop height rather than clipping a
    # valid tick run a few pixels past the former 5.5% limit.
    x_band = (plot.y1 + 0.004 * img_h, plot.y1 + 0.080 * img_h)
    x_ticks = x_ticks_override
    if x_ticks is None:
        x_ticks = [
            (float(text), cx)
            for text, cx, cy in words_px
            if x_pattern.match(text)
            and x_band[0] <= cy <= x_band[1]
            and plot.x0 - 0.03 * plot.width <= cx <= plot.x1 + 0.03 * plot.width
        ]
    y_ticks = [
        (float(text), cy)
        for text, cx, cy in words_px
        if NUM_RE.match(text)
        and plot.x0 - 0.22 * plot.width <= cx <= plot.x0 - 2
        and plot.y0 - 0.02 * plot.height <= cy <= plot.y1 + 0.02 * plot.height
    ]
    return _fit_axis(x_ticks, "X axis (Tj)"), _fit_axis(y_ticks, "Y axis (V(BR)DSS)")


def _axis_gutter_numeric_counts(
    words_px: list[tuple[str, float, float]], plot: PlotBox, img_h: int
) -> tuple[int, int]:
    """Count native numeric words owned by the two axis gutters."""

    x_band = (plot.y1 + 0.004 * img_h, plot.y1 + 0.080 * img_h)
    x_count = sum(
        bool(INT_RE.fullmatch(text))
        and x_band[0] <= cy <= x_band[1]
        and plot.x0 - 0.03 * plot.width <= cx <= plot.x1 + 0.03 * plot.width
        for text, cx, cy in words_px
    )
    y_count = sum(
        bool(NUM_RE.fullmatch(text))
        and plot.x0 - 0.22 * plot.width <= cx <= plot.x0 - 2
        and plot.y0 - 0.02 * plot.height <= cy <= plot.y1 + 0.02 * plot.height
        for text, cx, cy in words_px
    )
    return x_count, y_count


def _trustworthy_ocr_axis_ticks(
    candidates: list[tuple[float, float]],
    grid_positions: list[float],
    what: str,
) -> list[tuple[float, float]]:
    """Return the unique largest OCR subset proving one source grid scale.

    OCR can lose a minus or split a digit.  Such a token is discarded only
    when at least four other source labels uniquely fit one linear axis to
    within a raster pixel and own distinct observed grid rails.  Values are
    never interpolated from the grid.
    """

    grid = sorted(set(float(pixel) for pixel in grid_positions))
    if len(grid) < MIN_TICKS_PER_AXIS or not candidates:
        raise RuntimeError(f"{what}: OCR has no trustworthy grid-backed tick run")
    pitch = float(np.median(np.diff(grid)))
    owned: list[tuple[float, float, float]] = []
    for value, label_px in candidates:
        rail = min(grid, key=lambda pixel: abs(pixel - label_px))
        if abs(rail - label_px) <= max(3.0, 0.30 * pitch):
            owned.append((float(value), float(label_px), rail))

    passing: list[tuple[LinearAxis, tuple[tuple[float, float, float], ...]]] = []
    for count in range(len(owned), MIN_TICKS_PER_AXIS - 1, -1):
        for subset in itertools.combinations(owned, count):
            rails = [item[2] for item in subset]
            if len(set(rails)) != count:
                continue
            if (max(rails) - min(rails)) < 0.60 * (grid[-1] - grid[0]):
                continue
            try:
                axis = _fit_axis(
                    [(value, label_px) for value, label_px, _rail in subset], what
                )
            except RuntimeError:
                continue
            if abs(axis.m) < 1e-12 or axis.resid / abs(axis.m) > 1.0:
                continue
            passing.append((axis, subset))
        if passing:
            break
    if not passing:
        raise RuntimeError(f"{what}: OCR has no trustworthy grid-backed tick run")

    models = {
        (round(axis.m, 6), round(axis.b, 4))
        for axis, _subset in passing
    }
    if len(models) != 1:
        raise RuntimeError(f"{what}: OCR tick subset is ambiguous")
    _axis, subset = min(passing, key=lambda item: item[0].resid)
    return [(value, label_px) for value, label_px, _rail in subset]


def _ocr_vector_outline_axes(
    chart: dict,
    transform: CropTransform,
    plot: PlotBox,
    x_grid: list[float],
    y_grid: list[float],
) -> tuple[LinearAxis, LinearAxis]:
    """OCR bounded gutters for a vector chart whose glyphs are paths."""

    px0, py0 = transform.to_pt(plot.x0, plot.y0)
    px1, py1 = transform.to_pt(plot.x1, plot.y1)
    width, height = px1 - px0, py1 - py0
    x_clip = (px0, py1 - 0.02 * height, px1, py1 + 0.12 * height)
    y_clip = (px0 - 0.18 * width, py0, px0 + 0.02 * width, py1)
    x_words = ocr_words_in_rect(
        chart["pdf"], int(chart["page"]), x_clip, dpi=400.0, psm=11
    )
    y_words = ocr_words_in_rect(
        chart["pdf"], int(chart["page"]), y_clip, dpi=400.0, psm=11
    )
    x_candidates = []
    for x0, y0, x1, y1, text in x_words:
        normalized = text.replace("\N{MINUS SIGN}", "-")
        if INT_RE.fullmatch(normalized):
            center, _ = transform.to_px((x0 + x1) / 2, (y0 + y1) / 2)
            x_candidates.append((float(normalized), center))
    y_candidates = []
    for x0, y0, x1, y1, text in y_words:
        if NUM_RE.fullmatch(text):
            _, center = transform.to_px((x0 + x1) / 2, (y0 + y1) / 2)
            y_candidates.append((float(text), center))
    x_ticks = _trustworthy_ocr_axis_ticks(
        x_candidates, x_grid, "X axis (Tj) OCR"
    )
    y_ticks = _trustworthy_ocr_axis_ticks(
        y_candidates, y_grid, "Y axis (V(BR)DSS) OCR"
    )
    return _fit_axis(x_ticks, "X axis (Tj)"), _fit_axis(
        y_ticks, "Y axis (V(BR)DSS)"
    )


def _repair_raster_plot_from_owned_y_ticks(
    gray: np.ndarray,
    words_px: list[tuple[str, float, float]],
    plot: PlotBox,
) -> PlotBox:
    """Re-seat a raster plot whose left edge is a foreign neighboring rail.

    Side-by-side ST panels can leave one full-height vertical from the chart to
    the left inside a loose finder crop.  ``find_plot_box`` then treats that
    isolated rail as ``x0`` and moves the real Y tick ladder *inside* the plot,
    so calibration misleadingly reports that no Y labels exist.

    Move the edge only when two independent local facts agree: the full crop
    contains a regular vertical-grid family to the right of the current edge,
    and at least four coherent numeric Y labels occupy the newly exposed
    gutter.  Geometry alone is insufficient; an ambiguous ladder or grid keeps
    the original box and therefore preserves the existing fail-closed path.
    """

    height, width = gray.shape
    full_width_hint = PlotBox(0, plot.y0, width - 1, plot.y1)
    all_x_lines, _ = _full_span_grid_lines(gray, full_width_hint)
    grid = _uniform_grid_family(list(all_x_lines))
    if grid is None or len(grid) < 6:
        return plot

    pitch = float(np.median(np.diff(grid)))
    candidate = PlotBox(
        int(round(grid[0])), plot.y0, int(round(grid[-1])), plot.y1
    )
    if not (
        candidate.x0 >= plot.x0 + 0.5 * pitch
        and candidate.x1 >= plot.x1 - 0.25 * pitch
        and candidate.x1 <= plot.x1 + 1.5 * pitch
        and candidate.width >= 0.45 * width
    ):
        return plot

    ladder = [
        (float(text), cy, cx)
        for text, cx, cy in words_px
        if NUM_RE.match(text)
        and plot.x0 + 2 <= cx <= candidate.x0 - 2
        and plot.y0 - 0.02 * plot.height <= cy <= plot.y1 + 0.02 * plot.height
    ]
    if len(ladder) < MIN_TICKS_PER_AXIS:
        return plot
    label_xs = [cx for _value, _cy, cx in ladder]
    label_ys = [cy for _value, cy, _cx in ladder]
    if (
        max(label_xs) - min(label_xs) > max(5.0, 0.03 * candidate.width)
        or max(label_ys) - min(label_ys) < 0.55 * candidate.height
    ):
        return plot
    try:
        _fit_axis([(value, cy) for value, cy, _cx in ladder], "owned Y tick ladder")
    except RuntimeError:
        return plot
    return candidate


def _snap_axis_ticks_to_grid(
    axis: LinearAxis, grid_positions: list[float], what: str
) -> tuple[LinearAxis, list[dict[str, float]]]:
    """Refit tick values at their observed grid intersections and assert them.

    Label centers are useful for semantic identity but are not the numerical
    tick locations.  Each consumed label must own one distinct nearby major
    gridline; the refit must then invert through every observed center within
    one raster pixel.
    """

    grid = sorted(grid_positions)
    if len(grid) < 2:
        raise RuntimeError(f"{what}: observed grid family is incomplete")
    pitch = float(np.median(np.diff(grid)))
    assignments: list[tuple[float, float, float]] = []
    used: set[float] = set()
    for value, label_pixel in sorted(axis.ticks, key=lambda item: item[1]):
        grid_pixel = min(grid, key=lambda pixel: abs(pixel - label_pixel))
        if grid_pixel in used:
            raise RuntimeError(f"{what}: multiple labels own one observed gridline")
        if abs(grid_pixel - label_pixel) > 0.35 * pitch:
            raise RuntimeError(
                f"{what}: label center is too far from its observed gridline"
            )
        used.add(grid_pixel)
        assignments.append((value, label_pixel, grid_pixel))
    snapped = _fit_axis(
        [(value, grid_pixel) for value, _label_pixel, grid_pixel in assignments],
        what,
    )
    initial_errors = [
        abs((value - snapped.b) / snapped.m - grid_pixel)
        for value, _label_pixel, grid_pixel in assignments
    ]
    if max(initial_errors, default=0.0) > 1.0:
        snapped = _refit_axis_with_center_tolerance(snapped, assignments, what)
    assertions: list[dict[str, float]] = []
    for value, label_pixel, grid_pixel in assignments:
        inverse_pixel = (value - snapped.b) / snapped.m
        inverse_error = abs(inverse_pixel - grid_pixel)
        if inverse_error > 1.0 + 1e-9:
            raise RuntimeError(
                f"{what}: fitted tick misses observed grid center by "
                f"{inverse_error:.2f} px"
            )
        assertions.append(
            {
                "value": value,
                "label_pixel": round(label_pixel, 3),
                "grid_pixel": round(grid_pixel, 3),
                "label_to_grid_px": round(abs(label_pixel - grid_pixel), 3),
                "fit_to_grid_px": round(inverse_error, 3),
            }
        )
    return snapped, assertions


def _refit_axis_with_center_tolerance(
    axis: LinearAxis,
    assignments: list[tuple[float, float, float]],
    what: str,
    tolerance_px: float = 1.0,
) -> LinearAxis:
    """Find a linear calibration whose inverse crosses every owned grid center.

    PDF grid strokes can land on slightly non-uniform raster pixels.  Ordinary
    least squares minimizes total error and can miss one endpoint even when a
    linear fit satisfying the strict one-pixel contract exists.  Intersect the
    exact affine feasibility bounds in pixel(value) space; never widen the
    tolerance, and refuse when their intersection is empty.
    """
    points = [(value, grid_pixel) for value, _label_pixel, grid_pixel in assignments]
    slope_lo, slope_hi = -float("inf"), float("inf")
    for index, (value_a, pixel_a) in enumerate(points):
        for value_b, pixel_b in points[index + 1 :]:
            if value_a == value_b:
                raise RuntimeError(f"{what}: duplicate tick values cannot own distinct gridlines")
            lo_value, lo_pixel, hi_value, hi_pixel = value_a, pixel_a, value_b, pixel_b
            if hi_value < lo_value:
                lo_value, hi_value = hi_value, lo_value
                lo_pixel, hi_pixel = hi_pixel, lo_pixel
            value_span = hi_value - lo_value
            pixel_span = hi_pixel - lo_pixel
            slope_lo = max(slope_lo, (pixel_span - 2.0 * tolerance_px) / value_span)
            slope_hi = min(slope_hi, (pixel_span + 2.0 * tolerance_px) / value_span)
    if slope_lo > slope_hi + 1e-9:
        raise RuntimeError(f"{what}: no linear fit crosses every observed grid center within 1 px")
    pixel_slope = min(max(1.0 / axis.m, slope_lo), slope_hi)
    intercept_lo = max(pixel - tolerance_px - pixel_slope * value for value, pixel in points)
    intercept_hi = min(pixel + tolerance_px - pixel_slope * value for value, pixel in points)
    if intercept_lo > intercept_hi + 1e-9 or abs(pixel_slope) < 1e-12:
        raise RuntimeError(f"{what}: no linear fit crosses every observed grid center within 1 px")
    pixel_intercept = min(max(-axis.b / axis.m, intercept_lo), intercept_hi)
    m = 1.0 / pixel_slope
    b = -pixel_intercept / pixel_slope
    resid = max(abs(m * pixel + b - value) for value, pixel in points)
    return LinearAxis(m, b, points, resid)


def _clip_points_to_one_unlabeled_interval(
    points: list[tuple[int, int]], axis: LinearAxis
) -> tuple[list[tuple[int, int]], int]:
    """Withhold trace pixels beyond one arithmetic interval past a label."""

    tick_pixels = sorted(pixel for _value, pixel in axis.ticks)
    if len(tick_pixels) < 2:
        raise RuntimeError("X axis: cannot bound unlabeled endpoint intervals")
    spacing = float(np.median(np.diff(tick_pixels)))
    lower = tick_pixels[0] - 1.01 * spacing
    upper = tick_pixels[-1] + 1.01 * spacing
    served = [(x, y) for x, y in points if lower <= x <= upper]
    if not served:
        raise RuntimeError("X axis: no source trace remains inside evidenced extent")
    return served, len(points) - len(served)


def _trace_runs_along_horizontal_frame(
    points: list[tuple[int, int]], plot: PlotBox
) -> bool:
    """Return true only for a sustained source run pinned to a Y frame."""

    minimum_span = max(6.0, 0.02 * (plot.x1 - plot.x0))
    for frame_y in (plot.y0, plot.y1):
        near_x = sorted({x for x, y in points if abs(y - frame_y) <= 2})
        if not near_x:
            continue
        start = previous = near_x[0]
        for x in near_x[1:]:
            if x - previous > 2:
                start = x
            if x - start >= minimum_span:
                return True
            previous = x
    return False


def _uniform_grid_family(
    positions: list[float], min_count: int = 5
) -> list[float] | None:
    """Return a uniform-pitch plot-grid family, or ``None``.

    Long strokes in a chart panel are either the gridline family (uniform
    pitch, frame lines included) or stray rules — the outer panel border,
    title/footer separators. Requiring a uniform family separates the two:
    end lines whose gap does not match the grid pitch are trimmed (at most
    two per side), and a family that still is not uniform is rejected.
    """
    merged: list[float] = []
    for pos in sorted(positions):
        if merged and pos - merged[-1] <= 2.0:
            merged[-1] = (merged[-1] + pos) / 2
        else:
            merged.append(pos)
    for _ in range(2):
        if len(merged) < max(min_count, 3):
            return None
        gaps = [b - a for a, b in zip(merged, merged[1:])]
        pitch = sorted(gaps)[len(gaps) // 2]
        if pitch <= 0:
            return None
        if abs(gaps[0] - pitch) > 0.35 * pitch:
            merged = merged[1:]
            continue
        if abs(gaps[-1] - pitch) > 0.35 * pitch:
            merged = merged[:-1]
            continue
        break
    if len(merged) < min_count:
        return None
    gaps = [b - a for a, b in zip(merged, merged[1:])]
    pitch = sorted(gaps)[len(gaps) // 2]
    if any(abs(g - pitch) > 0.35 * pitch for g in gaps):
        return None
    return merged


def _uniform_family_extent(
    positions: list[float], min_count: int = 5
) -> tuple[float, float] | None:
    """Extent of a uniform-pitch line family (a plot GRID), or None."""

    family = _uniform_grid_family(positions, min_count)
    return (family[0], family[-1]) if family is not None else None


def _vector_plot_grid(
    page, transform: CropTransform, image_shape: tuple[int, int]
) -> tuple[PlotBox, list[float], list[float]] | None:
    """Detect the plot frame and observed grid centers from vector evidence.

    The raster find_plot_box rejects frame lines within 8% of the crop edge;
    tight caption-derived crops (older Infineon layouts) put the real frame
    exactly there, which silently CLIPPED the digitized curve at an interior
    gridline. Vector geometry has no such margin assumption. Only a
    uniform-pitch family counts as a grid: layouts whose long strokes are
    just the outer panel border (newer Infineon pages draw the grid another
    way) yield None here and use the raster detector instead.
    """
    height, width = image_shape
    verticals: list[float] = []
    horizontals: list[float] = []
    for drawing in page.get_drawings():
        if drawing.get("type") not in {"s", "fs"}:
            continue
        for item in drawing.get("items", []):
            if item[0] == "l":
                x0, y0 = transform.to_px(float(item[1].x), float(item[1].y))
                x1, y1 = transform.to_px(float(item[2].x), float(item[2].y))
                if not (-2 <= min(x0, x1) and max(x0, x1) <= width + 2 and -2 <= min(y0, y1) and max(y0, y1) <= height + 2):
                    continue
                if abs(x1 - x0) <= 1.5 and abs(y1 - y0) >= height * 0.45:
                    verticals.append((x0 + x1) / 2)
                elif abs(y1 - y0) <= 1.5 and abs(x1 - x0) >= width * 0.45:
                    horizontals.append((y0 + y1) / 2)
            else:
                # A plot's solid outer frame is commonly one PDF rectangle,
                # while its interior grid is emitted as separate lines.  If
                # the rectangle is plot-sized, include all four sides in the
                # uniform-family fit; otherwise the last interior gridline can
                # be mistaken for the axis endpoint.  Small legend/text masks
                # cannot satisfy both span requirements.
                rect = _rectangular_drawing_item(item)
                if rect is None:
                    continue
                x0, y0 = transform.to_px(float(rect.x0), float(rect.y0))
                x1, y1 = transform.to_px(float(rect.x1), float(rect.y1))
                if not (
                    -2 <= min(x0, x1)
                    and max(x0, x1) <= width + 2
                    and -2 <= min(y0, y1)
                    and max(y0, y1) <= height + 2
                ):
                    continue
                rect_width = abs(x1 - x0)
                rect_height = abs(y1 - y0)
                if rect_width >= width * 0.45 and rect_height >= height * 0.40:
                    verticals.extend((x0, x1))
                    horizontals.extend((y0, y1))
    x_grid = _uniform_grid_family(verticals)
    y_grid = _uniform_grid_family(horizontals)
    if x_grid is None or y_grid is None:
        return None
    box = PlotBox(
        x0=int(round(x_grid[0])),
        y0=int(round(y_grid[0])),
        x1=int(round(x_grid[-1])),
        y1=int(round(y_grid[-1])),
    )
    if box.width < width * 0.44 or box.height < height * 0.4:
        return None
    return box, x_grid, y_grid


def _rectangular_drawing_item(item):
    if item[0] == "re":
        return item[1]
    if item[0] == "qu" and item[1].is_rectangular:
        return item[1].rect
    return None


def _closed_vector_outer_frame(
    page, transform: CropTransform, image_shape: tuple[int, int]
) -> PlotBox | None:
    """Return the largest plot-sized closed vector rectangle in this crop."""
    height, width = image_shape
    candidates: list[PlotBox] = []
    for drawing in page.get_drawings():
        if drawing.get("type") not in {"s", "fs"}:
            continue
        for item in drawing.get("items", []):
            rect = _rectangular_drawing_item(item)
            if rect is None:
                continue
            x0, y0 = transform.to_px(float(rect.x0), float(rect.y0))
            x1, y1 = transform.to_px(float(rect.x1), float(rect.y1))
            if not (-2 <= x0 < x1 <= width + 2 and -2 <= y0 < y1 <= height + 2):
                continue
            box = PlotBox(int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
            if box.width >= width * 0.45 and box.height >= height * 0.40:
                candidates.append(box)
    return max(candidates, key=lambda box: box.width * box.height, default=None)


def _complete_raster_plot_with_closed_vector_frame(
    raster_plot: PlotBox,
    outer: PlotBox | None,
    x_grid: list[float],
) -> PlotBox | None:
    """Accept only a one-right-edge completion of an otherwise aligned box."""
    if outer is None or len(x_grid) < 2:
        return None
    edge_tol = max(3.0, 0.02 * min(raster_plot.width, raster_plot.height))
    pitch = float(np.median(np.diff(sorted(x_grid))))
    extension = outer.x1 - raster_plot.x1
    aligned = (
        abs(outer.x0 - raster_plot.x0) <= edge_tol
        and abs(outer.y0 - raster_plot.y0) <= edge_tol
        and abs(outer.y1 - raster_plot.y1) <= edge_tol
    )
    if aligned and edge_tol < extension <= 1.25 * pitch:
        return outer
    return None


def _vector_plot_frame(
    page, transform: CropTransform, image_shape: tuple[int, int]
) -> PlotBox | None:
    """Compatibility wrapper returning only the evidenced vector frame."""

    grid = _vector_plot_grid(page, transform, image_shape)
    return grid[0] if grid is not None else None


def _clip_segment_to_plot(a, b, plot_rect):
    """Liang-Barsky clip of one evidenced source edge to the plot frame."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    enter, leave = 0.0, 1.0
    for p, q in (
        (-dx, a[0] - plot_rect.x0),
        (dx, plot_rect.x1 - a[0]),
        (-dy, a[1] - plot_rect.y0),
        (dy, plot_rect.y1 - a[1]),
    ):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        ratio = q / p
        if p < 0:
            enter = max(enter, ratio)
        else:
            leave = min(leave, ratio)
        if enter > leave:
            return None
    return (
        (a[0] + enter * dx, a[1] + enter * dy),
        (a[0] + leave * dx, a[1] + leave * dy),
    )


def _resample_connected_breakdown_component(component, plot_rect, transform, plot):
    """Clip and densify actual connected PDF edges without inventing bridges."""
    by_x: dict[int, list[int]] = {}
    for a, b in zip(component, component[1:]):
        clipped = _clip_segment_to_plot(a, b, plot_rect)
        if clipped is None:
            continue
        start = transform.to_px(*clipped[0])
        end = transform.to_px(*clipped[1])
        dx, dy = end[0] - start[0], end[1] - start[1]
        steps = max(1, int(round(max(abs(dx), abs(dy)))))
        for index in range(steps + 1):
            fraction = index / steps
            x = int(round(start[0] + fraction * dx))
            y = int(round(start[1] + fraction * dy))
            if plot.x0 <= x <= plot.x1 and plot.y0 <= y <= plot.y1:
                by_x.setdefault(x, []).append(y)
    return [
        (x, int(round(float(np.median(by_x[x])))))
        for x in sorted(by_x)
    ]


def _extract_single_trace(page, transform: CropTransform, plot: PlotBox, fitz) -> list[tuple[int, int]]:
    """Return the one full-span stroked curve inside the plot, in crop pixels."""
    px0, py0 = transform.to_pt(plot.x0, plot.y0)
    px1, py1 = transform.to_pt(plot.x1, plot.y1)
    plot_rect = fitz.Rect(px0, py0, px1, py1)
    edges = _vector_curve_edges(
        page.get_drawings(), plot_rect, min_stroke_width=0.4
    )
    components = _chain_vector_components(edges)
    candidates: list[tuple[float, list[tuple[int, int]]]] = []
    for component in components:
        if len(component) < 2:
            continue
        points = _resample_connected_breakdown_component(
            component, plot_rect, transform, plot
        )
        if len(points) < plot.width * MIN_X_SPAN_FRAC:
            continue
        if max(y for _x, y in points) - min(y for _x, y in points) < 0.02 * plot.height:
            # Full-span internal gridlines can survive the shared vector-edge
            # filter as connected paths. A V(BR)DSS(Tj) response must move in
            # Y; do not count a physically flat plot rail as another curve.
            continue
        candidates.append((_path_length(component), points))
    if not candidates:
        # ST plots sometimes encode the only curve as a filled ribbon (``f``)
        # or a filled-and-stroked ribbon (``fs``). Recover that
        # ribbon's centerline only after the strict stroke path found none;
        # plot frames and filled grid masks fail the span/shape guards in the
        # shared helper.
        for drawing in page.get_drawings():
            if drawing.get("type") not in {"f", "fs"}:
                continue
            if not _is_dark_stroke(drawing.get("fill")):
                continue
            filled = _filled_path_centerline({**drawing, "type": "f"}, plot_rect)
            if not filled:
                continue
            raw = [
                tuple(int(round(value)) for value in transform.to_px(x, y))
                for x, y in filled
            ]
            points = _resample_vector_trace_pixels(raw, plot)
            if len(points) < plot.width * MIN_X_SPAN_FRAC:
                continue
            candidates.append((_path_length(filled), points))
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly 1 full-span breakdown curve, found {len(candidates)} — "
            "not silently picking one; check the crop"
        )
    return candidates[0][1]


def _row_vbrdss_value(tail: str) -> float | None:
    """Return a source-owned min/typ/max table value.

    A free-text ``... breakdown voltage ... 100 V`` sequence can be a chart
    caption, axis annotation, or narrative condition.  Only the table-shaped
    dash-slot form is strong enough ownership evidence here; ambiguous direct
    prose fails closed instead of becoming a circular normalized-axis anchor.
    """
    matches = list(SPEC_DASH_VALUE_RE.finditer(tail))
    for match in matches:
        value = float(match.group(1))
        if 10.0 <= abs(value) <= 2000.0:
            return value
    return None


def _positioned_min_vbrdss(page) -> float | None:
    """Read a V(BR)DSS value owned by the table's Min and Unit columns."""

    try:
        words = [tuple(word) for word in page.get_text("words")]
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    min_headers = [
        word
        for word in words
        if len(word) >= 5
        and re.sub(r"[^a-z]", "", str(word[4]).lower()) == "min"
    ]
    unit_headers = [
        word
        for word in words
        if len(word) >= 5
        and re.sub(r"[^a-z]", "", str(word[4]).lower()) == "unit"
    ]
    for min_word in min_headers:
        min_center = 0.5 * (float(min_word[0]) + float(min_word[2]))
        header_y = 0.5 * (float(min_word[1]) + float(min_word[3]))
        for unit_word in unit_headers:
            unit_header_y = 0.5 * (float(unit_word[1]) + float(unit_word[3]))
            unit_center = 0.5 * (float(unit_word[0]) + float(unit_word[2]))
            if abs(unit_header_y - header_y) > 3.0 or unit_center <= min_center:
                continue
            for word in words:
                if len(word) < 5 or NUM_RE.fullmatch(str(word[4])) is None:
                    continue
                center_x = 0.5 * (float(word[0]) + float(word[2]))
                center_y = 0.5 * (float(word[1]) + float(word[3]))
                min_width = max(8.0, float(min_word[2]) - float(min_word[0]))
                if abs(center_x - min_center) > 0.6 * min_width:
                    continue
                if not 2.0 <= center_y - header_y <= 140.0:
                    continue
                row_words = [
                    candidate
                    for candidate in words
                    if len(candidate) >= 5
                    and abs(
                        0.5 * (float(candidate[1]) + float(candidate[3])) - center_y
                    )
                    <= 8.0
                ]
                row_text = " ".join(str(candidate[4]) for candidate in row_words)
                compact = re.sub(r"[^a-z0-9]", "", row_text.lower())
                if (
                    "vbrdss" not in compact
                    and "drainsourcebreakdownvoltage" not in compact
                ):
                    continue
                unit_width = max(8.0, float(unit_word[2]) - float(unit_word[0]))
                unit_owned = any(
                    str(candidate[4]).lower() == "v"
                    and abs(
                        0.5 * (float(candidate[0]) + float(candidate[2]))
                        - unit_center
                    )
                    <= 0.6 * unit_width
                    for candidate in row_words
                )
                value = float(word[4])
                if unit_owned and 10.0 <= abs(value) <= 2000.0:
                    return value
    return None


def _spec_min_vbrdss(doc) -> tuple[float, int] | None:
    """Parse the parameter-table V(BR)DSS minimum; None means NOT verified."""
    for page_num in range(min(len(doc), 6)):
        positioned = _positioned_min_vbrdss(doc[page_num])
        if positioned is not None:
            return positioned, page_num + 1
        text = re.sub(r"\s+", " ", doc[page_num].get_text("text"))
        text = text.replace("‑", "-").replace("–", "-").replace("—", "-")
        for pattern in (SPEC_ROW_RE, SPEC_LABEL_ROW_RE):
            for row in pattern.finditer(text):
                value = _row_vbrdss_value(row.group(1))
                if value is not None:
                    return value, page_num + 1
    return None


def _max_vds_rating(doc) -> tuple[float, int] | None:
    """Parse the absolute-maximum drain-source voltage when explicitly owned."""
    pattern = re.compile(
        r"\bVDS\s+Drain[-\s]*Source\s+Voltage\s+([+-]?\d+(?:\.\d+)?)\s*V\b",
        re.IGNORECASE,
    )
    for page_num in range(min(len(doc), 4)):
        text = re.sub(r"\s+", " ", doc[page_num].get_text("text"))
        match = pattern.search(text)
        if match:
            return float(match.group(1)), page_num + 1
    return None


def _validate_rating_consistency(
    spec: tuple[float, int] | None, rating: tuple[float, int] | None
) -> None:
    """Refuse a normalized scale when two source-owned ratings disagree."""
    if spec is None or rating is None:
        return
    spec_v, spec_page = spec
    rating_v, rating_page = rating
    relative_gap = abs(abs(spec_v) - abs(rating_v)) / max(abs(spec_v), abs(rating_v))
    if spec_v * rating_v <= 0 or relative_gap > 0.05:
        raise RuntimeError(
            "conflicting source-owned breakdown ratings: "
            f"parameter table {spec_v:g} V (page {spec_page}) vs "
            f"absolute maximum {rating_v:g} V (page {rating_page})"
        )


def _breakdown_value_scale(
    chart: dict,
    spec: tuple[float, int] | None,
    y_axis: LinearAxis | None = None,
) -> tuple[float, str]:
    """Return the source-owned scale for an absolute or normalized chart."""
    owned_text = " ".join(
        str(chart.get(field, "")) for field in ("title", "text", "formula")
    ).lower()
    tick_values = [value for value, _pixel in y_axis.ticks] if y_axis else []
    # A normalized BVDSS axis is often omitted from the finder title and can
    # sit outside the conservative crop-text bbox.  Its owned tick sequence is
    # nevertheless decisive: ratios around unity cannot be absolute volts for
    # a source table rating in the tens or hundreds of volts.
    normalized_ticks = bool(
        spec
        and tick_values
        and abs(spec[0]) >= 10.0
        and min(tick_values) >= 0.25
        and max(tick_values) <= 2.5
    )
    normalized_chart = "normalized" in owned_text or normalized_ticks
    if normalized_chart and spec is None:
        raise RuntimeError(
            "normalized breakdown chart has no source-owned V(BR)DSS table minimum; "
            "absolute values are unverified"
        )
    if normalized_chart:
        if spec[0] < 0:
            return abs(float(spec[0])), "normalized_to_spec_min_magnitude"
        return float(spec[0]), "normalized_to_spec_min"
    if spec and spec[0] < 0 and tick_values and min(tick_values) >= 0:
        return 1.0, "absolute_magnitude_axis"
    return 1.0, "absolute_axis"


def _validate_breakdown_panel_semantics(chart: dict) -> None:
    """Reject physical-unit evidence for a foreign avalanche-energy panel."""

    owned_text = " ".join(
        str(chart.get(field, "")) for field in ("title", "text", "formula")
    ).lower()
    compact = re.sub(r"[^a-z0-9]+", "", owned_text)
    if (
        "singlepulseavalancheenergy" in compact
        or "avalancheenergy" in compact
        or "eas" in compact and "mj" in compact
    ):
        raise RuntimeError(
            "source panel is avalanche energy, not V(BR)DSS versus temperature"
        )


def _split_fused_numeric_run(text: str) -> list[float]:
    """Split a whitespace-lost arithmetic tick run such as 100120...180."""
    if not text.isdigit() or len(text) < 6:
        return []
    candidates: list[list[float]] = []
    for width in (2, 3):
        if len(text) % width:
            continue
        values = [float(text[index : index + width]) for index in range(0, len(text), width)]
        if len(values) < 3:
            continue
        gaps = [right - left for left, right in zip(values, values[1:])]
        if gaps[0] > 0 and all(abs(gap - gaps[0]) < 1e-9 for gap in gaps):
            candidates.append(values)
    return max(candidates, key=len, default=[])


def _anchor_breakdown_curve(
    v25_data: float,
    value_basis: str,
    spec: tuple[float, int] | None,
) -> tuple[str, dict, list[str]]:
    """Validate a calibrated curve without equating typical and minimum.

    Normalized plots use the table minimum as their numeric scale, so the
    25 C point must remain close to that anchor.  An absolute-axis plot is
    independently calibrated from its printed voltage ticks; its typical
    curve may legitimately sit above the guaranteed minimum.  For that case
    the table is a one-sided sanity bound, not an equality target.
    """
    if spec is None:
        note = "no V(BR)DSS minimum found in the parameter table — chart values NOT validated"
        return "unverified", {"verdict": "unverified", "note": note}, [
            "spec anchor unavailable: V(BR)DSS(25 C) could not be checked against the table minimum"
        ]

    spec_min, spec_page = spec
    magnitude_margin = (abs(v25_data) - abs(spec_min)) / abs(spec_min)
    same_polarity = v25_data * spec_min > 0
    magnitude_basis = value_basis in {
        "normalized_to_spec_min_magnitude",
        "absolute_magnitude_axis",
    }
    if value_basis.startswith("normalized_to_spec_min"):
        verified = (magnitude_basis or same_polarity) and abs(magnitude_margin) <= ANCHOR_TOL
        comparison = "normalized_matches_spec_min"
    else:
        verified = (magnitude_basis or same_polarity) and magnitude_margin >= -ANCHOR_TOL
        comparison = "absolute_axis_meets_spec_min"
    verdict = "verified" if verified else "FAIL"
    anchor = {
        "verdict": verdict,
        "comparison": comparison,
        "spec_min_v": spec_min,
        "spec_page": spec_page,
        "chart_v25_v": round(v25_data, 3),
        "err": round(magnitude_margin, 5),
    }
    if verified:
        return verdict, anchor, []
    return verdict, anchor, [
        f"chart V(25 C)={v25_data:.2f} V does not satisfy the table minimum "
        f"{spec_min:g} V ({magnitude_margin:+.1%} magnitude margin)"
    ]


def _words_in_crop_px(page, transform: CropTransform, image_shape: tuple[int, int]) -> list[tuple[str, float, float]]:
    height, width = image_shape
    out = []
    for w in page.get_text("words"):
        cx, cy = transform.to_px((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
        if -width * 0.05 <= cx <= width * 1.05 and -height * 0.05 <= cy <= height * 1.05:
            text = w[4].strip()
            fused = _split_fused_numeric_run(text)
            if fused:
                px0, _ = transform.to_px(w[0], (w[1] + w[3]) / 2)
                px1, _ = transform.to_px(w[2], (w[1] + w[3]) / 2)
                step = (px1 - px0) / len(fused)
                out.extend(
                    (f"{value:g}", px0 + (index + 0.5) * step, cy)
                    for index, value in enumerate(fused)
                )
            else:
                out.append((text, cx, cy))
    return out


def _is_closed_horizontal_minus_glyph(
    drawing: dict, word_rect: tuple[float, float, float, float]
) -> bool:
    """Return whether one filled path is a source-owned drawn minus sign.

    Some vector PDFs encode the digits of negative ticks as text but paint the
    minus as a tiny filled rectangle.  Keep the recognition deliberately local
    and typographic: dark opaque fill, four closed axis-aligned edges, and a
    short horizontal rectangle immediately to the left of the numeric word.
    """

    items = drawing.get("items", [])
    rect = drawing.get("rect")
    fill = drawing.get("fill")
    if (
        drawing.get("type") != "f"
        or rect is None
        or fill is None
        or drawing.get("fill_opacity", 1.0) < 0.8
        or max(fill) > 0.25
        or len(items) != 4
        or any(item[0] != "l" for item in items)
    ):
        return False
    segments = [(item[1], item[2]) for item in items]
    if any(
        not (
            abs(start.x - end.x) < 1e-3
            or abs(start.y - end.y) < 1e-3
        )
        for start, end in segments
    ):
        return False
    if any(
        abs(left.x - right.x) > 1e-3 or abs(left.y - right.y) > 1e-3
        for (_start, left), (right, _end) in zip(segments, segments[1:] + segments[:1])
    ):
        return False

    word_x0, word_y0, _word_x1, word_y1 = word_rect
    word_h = float(word_y1 - word_y0)
    glyph_w = float(rect.x1 - rect.x0)
    glyph_h = float(rect.y1 - rect.y0)
    gap = float(word_x0 - rect.x1)
    center_offset = abs(float((rect.y0 + rect.y1 - word_y0 - word_y1) / 2))
    return bool(
        word_h > 0
        and 0.15 * word_h <= glyph_w <= 0.75 * word_h
        and 0 < glyph_h <= 0.15 * word_h
        and glyph_w >= 4.0 * glyph_h
        and 0 <= gap <= 0.25 * word_h
        and center_offset <= 0.15 * word_h
    )


def _owned_temperature_x_ticks(
    page,
    transform: CropTransform,
    image_shape: tuple[int, int],
    plot: PlotBox,
) -> list[tuple[float, float]]:
    """Collect Tj ticks while restoring only uniquely proven drawn minuses.

    The ordinary shared fitter remains strict.  In particular, no arithmetic
    progression is guessed from unsigned magnitudes: a fully unsigned sequence
    permits increasing and decreasing mirror solutions.  A missing sign is
    restored only from an adjacent source drawing owned by that exact word.
    """

    img_h, _img_w = image_shape
    x_band = (plot.y1 + 0.004 * img_h, plot.y1 + 0.080 * img_h)
    drawings = page.get_drawings()
    ticks: list[tuple[float, float]] = []
    for word in page.get_text("words"):
        text = str(word[4]).strip().replace("\N{MINUS SIGN}", "-")
        fused = _split_fused_numeric_run(text)
        if fused:
            _cx, cy = transform.to_px(
                (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
            )
            px0, _ = transform.to_px(word[0], (word[1] + word[3]) / 2)
            px1, _ = transform.to_px(word[2], (word[1] + word[3]) / 2)
            step = (px1 - px0) / len(fused)
            for index, value in enumerate(fused):
                cx = px0 + (index + 0.5) * step
                if (
                    x_band[0] <= cy <= x_band[1]
                    and plot.x0 - 0.03 * plot.width
                    <= cx
                    <= plot.x1 + 0.03 * plot.width
                ):
                    ticks.append((value, cx))
            continue
        if INT_RE.fullmatch(text) is None:
            continue
        cx, cy = transform.to_px((word[0] + word[2]) / 2, (word[1] + word[3]) / 2)
        if not (
            x_band[0] <= cy <= x_band[1]
            and plot.x0 - 0.03 * plot.width <= cx <= plot.x1 + 0.03 * plot.width
        ):
            continue
        value = float(text)
        if value >= 0:
            word_rect = tuple(float(item) for item in word[:4])
            candidates = [
                drawing
                for drawing in drawings
                if _is_closed_horizontal_minus_glyph(drawing, word_rect)
            ]
            if len(candidates) > 1:
                raise RuntimeError(
                    "X axis (Tj): ambiguous drawn-minus glyph ownership"
                )
            if candidates:
                glyph = candidates[0]["rect"]
                value = -abs(value)
                cx, _ = transform.to_px((glyph.x0 + word[2]) / 2, (word[1] + word[3]) / 2)
        ticks.append((value, cx))
    return ticks


def _draw_overlay(
    image: np.ndarray,
    plot: PlotBox,
    points: list[tuple[int, int]],
    x_axis: LinearAxis,
    y_axis: LinearAxis,
    *,
    x_labels_below: bool = True,
) -> np.ndarray:
    overlay = image.copy()
    draw_plot_frame(overlay, plot, (0, 180, 0))
    for x, y in points:
        cv2.circle(overlay, (x, y), 1, (0, 0, 255), -1)
    # LinearAxis.ticks are (value, pixel); the shared renderer wants (pixel, value)
    draw_axis_ticks(
        overlay,
        plot,
        x_ticks=[(px, value) for value, px in x_axis.ticks],
        y_ticks=[(py, value) for value, py in y_axis.ticks],
        color=(255, 0, 0),
        marker_size=10,
        font_scale=0.35,
        x_labels_below=x_labels_below,
    )
    return overlay


def process_chart(chart: dict, crop_path: Path, out_dir: Path, rel_stem: Path) -> dict:
    _validate_breakdown_panel_semantics(chart)
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
        words_px = _words_in_crop_px(page, transform, gray.shape)
        frame_method = "vector"
        vector_grid = _vector_plot_grid(page, transform, gray.shape)
        if vector_grid is None:
            frame_method = "raster"
            plot = find_plot_box(gray)
            repaired_plot = _repair_raster_plot_from_owned_y_ticks(
                gray, words_px, plot
            )
            if repaired_plot != plot:
                plot = repaired_plot
                frame_method = "raster_y_tick_owned"
            x_grid, y_grid = _full_span_grid_lines(gray, plot, plot)
            completed = _complete_raster_plot_with_closed_vector_frame(
                plot, _closed_vector_outer_frame(page, transform, gray.shape), x_grid
            )
            if completed is not None:
                plot, frame_method = completed, "vector"
        else:
            plot, x_grid, y_grid = vector_grid
        x_ticks = _owned_temperature_x_ticks(
            page, transform, gray.shape, plot
        )
        axis_text_source = "native"
        source_points_px: list[tuple[int, int]] | None = None
        native_axis_counts = _axis_gutter_numeric_counts(
            words_px, plot, gray.shape[0]
        )
        if (
            native_axis_counts == (0, 0)
            and not page.get_images(full=True)
            and _closed_vector_outer_frame(page, transform, gray.shape) is not None
            and len(x_grid) >= MIN_TICKS_PER_AXIS
            and len(y_grid) >= MIN_TICKS_PER_AXIS
        ):
            # A vector page may paint every glyph as a path.  Prove that this
            # is a real one-curve chart before invoking OCR on its owned axis
            # gutters; prose/table regions cannot pass these geometry gates.
            source_points_px = _extract_single_trace(page, transform, plot, fitz)
            x_axis, y_axis = _ocr_vector_outline_axes(
                chart, transform, plot, x_grid, y_grid
            )
            axis_text_source = "ocr_vector_outlined_glyphs"
        else:
            x_axis, y_axis = _calibrate(
                words_px, plot, gray.shape[0], x_ticks_override=x_ticks
            )
        center_assertions: dict[str, list[dict[str, float]]] = {}
        if len(x_grid) >= len(x_axis.ticks) and len(y_grid) >= len(y_axis.ticks):
            x_axis, center_assertions["x"] = _snap_axis_ticks_to_grid(
                x_axis, x_grid, "X axis (Tj)"
            )
            y_axis, center_assertions["y"] = _snap_axis_ticks_to_grid(
                y_axis, y_grid, "Y axis (V(BR)DSS)"
            )
        if source_points_px is None:
            source_points_px = _extract_single_trace(page, transform, plot, fitz)
        points_px, withheld_points = _clip_points_to_one_unlabeled_interval(
            source_points_px, x_axis
        )
        spec = _spec_min_vbrdss(doc)
        _validate_rating_consistency(spec, _max_vds_rating(doc))
    finally:
        doc.close()

    y_multiplier, value_basis = _breakdown_value_scale(chart, spec, y_axis)
    data = sorted(
        ((x_axis.value(x), y_multiplier * y_axis.value(y)) for x, y in points_px),
        key=lambda p: p[0],
    )
    tj = np.array([p[0] for p in data])
    v = np.array([p[1] for p in data])
    tj_min, tj_max = float(tj.min()), float(tj.max())
    if not (tj_min <= 25.0 <= tj_max):
        raise RuntimeError(f"digitized Tj range [{tj_min:.0f}, {tj_max:.0f}] C does not cover 25 C")

    slope, intercept = np.polyfit(tj, v, 1)
    fit_rms = float(np.sqrt(np.mean((slope * tj + intercept - v) ** 2)))
    v25_data = float(np.interp(25.0, tj, v))
    v25_fit = float(slope * 25.0 + intercept)

    warnings: list[str] = []
    px_xs = [x for x, _ in source_points_px]
    trace_touches_x_frame = min(px_xs) <= plot.x0 + 2 or max(px_xs) >= plot.x1 - 2
    trace_rides_y_frame = _trace_runs_along_horizontal_frame(source_points_px, plot)
    if trace_rides_y_frame and frame_method != "vector":
        warnings.append(
            "digitized curve runs along a horizontal plot frame — the trace may be "
            "CLIPPED by a mis-detected Y extent; verify the overlay before using "
            "the end points"
        )
    if withheld_points:
        warnings.append(
            f"withheld {withheld_points} source points beyond one unlabeled "
            "X-axis interval"
        )
    if fit_rms > 0.01 * abs(float(np.mean(v))):
        warnings.append(
            f"line fit RMS {fit_rms:.3g} V is large — curve is visibly non-linear; "
            "use the CSV, not the slope/intercept summary"
        )

    anchor_verdict, anchor, anchor_warnings = _anchor_breakdown_curve(
        v25_data, value_basis, spec
    )
    warnings.extend(anchor_warnings)

    csv_path = out_dir / "points" / rel_stem.parent / f"{rel_stem.name}.bv_points.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Tj_C", "VBR_DSS_V"])
        for t, vv in data:
            writer.writerow([f"{t:.2f}", f"{vv:.4f}"])

    overlay = _draw_overlay(
        image,
        plot,
        points_px,
        x_axis,
        y_axis,
        x_labels_below=axis_text_source == "native",
    )
    overlay_path = out_dir / "overlays" / rel_stem.parent / f"{rel_stem.name}.bv_overlay.png"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)

    result = {
        "method": "vector",
        "frame_method": frame_method,
        "source_trace_clipped_to_verified_vector_frame": bool(
            trace_touches_x_frame and frame_method == "vector"
        ),
        "source_trace_points": len(source_points_px),
        "served_trace_clipped_to_one_unlabeled_interval": bool(withheld_points),
        "withheld_source_points": withheld_points,
        "value_basis": value_basis,
        "n_points": len(data),
        "tj_range_c": [round(tj_min, 1), round(tj_max, 1)],
        "v_at_25c": round(v25_data, 3),
        "v_at_25c_linefit": round(v25_fit, 3),
        "slope_mv_per_k": round(float(slope) * 1e3, 2),
        "line_fit_rms_v": round(fit_rms, 4),
        "calibration": {
            "x_ticks": len(x_axis.ticks), "x_resid": round(x_axis.resid, 4),
            "y_ticks": len(y_axis.ticks), "y_resid": round(y_axis.resid, 4),
            "exact_center_assertions": center_assertions,
            "x_exact_center_max_px": round(
                max(
                    (item["fit_to_grid_px"] for item in center_assertions.get("x", [])),
                    default=0.0,
                ),
                3,
            ),
            "y_exact_center_max_px": round(
                max(
                    (item["fit_to_grid_px"] for item in center_assertions.get("y", [])),
                    default=0.0,
                ),
                3,
            ),
        },
        "anchor": anchor,
        "status": anchor_verdict,
        "warnings": warnings,
        "csv": str(csv_path),
        "overlay": str(overlay_path),
    }
    if axis_text_source != "native":
        result["axis_text_source"] = axis_text_source
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("charts_json", type=Path, help="charts.json from `dsdig find`")
    parser.add_argument("--out", type=Path, default=None, help="output directory (default: alongside charts.json)")
    args = parser.parse_args()

    base_dir = args.charts_json.parent
    out_dir = args.out or base_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    charts = json.loads(args.charts_json.read_text())

    results: list[dict] = []
    errors: list[dict] = []
    for chart in charts:
        if chart.get("kind") != "breakdown_voltage":
            continue
        crop_rel = Path(chart["crop_png"])
        rel_stem = crop_rel.with_suffix("")
        print(f"digitize {chart['part']} diagram {chart['diagram']}: {crop_rel}")
        try:
            result = process_chart(chart, base_dir / crop_rel, out_dir, rel_stem)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors.append({"part": chart.get("part"), "crop": str(crop_rel), "error": str(exc)})
        else:
            result.update({"part": chart["part"], "page": chart["page"], "diagram": chart["diagram"],
                           "pdf": chart["pdf"]})
            results.append(result)
            print(
                f"  V(25C)={result['v_at_25c']:.2f} V, slope={result['slope_mv_per_k']:.1f} mV/K, "
                f"anchor={result['anchor']['verdict']}"
            )
            for warning in result["warnings"]:
                print(f"  WARNING: {warning}")

    manifest = {"panels": results, "errors": errors}
    (out_dir / "breakdown_voltage_digitization.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {out_dir / 'breakdown_voltage_digitization.json'}")
    if errors:
        raise SystemExit(1)
    if not results:
        print("ERROR: no breakdown_voltage panels in the index — nothing digitized")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
