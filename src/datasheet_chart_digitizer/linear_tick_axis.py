"""Linear tick-label axes shared by the single-curve temperature charts.

Breakdown voltage and threshold voltage versus temperature (and the transfer
axis fallback) fit LINEAR axes from (value, pixel) tick pairs, snap them onto
the grid rules the labels name, and can OCR the tick gutters of a panel that
has no native tick text.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

import numpy as np

from .capacitance_types import PlotBox
from .crop_transform import CropTransform
from .numeric_axis import AxisTick, NumericAxis, fit_axis_ticks
from .region_ocr import ocr_words_in_rect

INT_RE = re.compile(r"^-?\d+$")
NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
# Tick text is exact, so calibration residuals beyond a small fraction of the
# labeled span mean the label-position pairing is wrong, not merely noisy.
MAX_CAL_RESID_FRAC = 0.02
MIN_TICKS_PER_AXIS = 4


@dataclass
class LinearAxis:
    """value = m * px + b, fitted from tick-label positions."""

    m: float
    b: float
    ticks: list[tuple[float, float]]  # (value, px)
    resid: float                      # max |fit - label| in value units

    def value(self, px: float) -> float:
        return self.m * px + self.b


def _as_numeric_axis(axis: LinearAxis) -> NumericAxis:
    return NumericAxis(
        "linear", axis.m, axis.b,
        tuple(AxisTick(f"{value:g}", float(value), float(px)) for value, px in axis.ticks),
        float(axis.resid), (),
    )


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


def _trustworthy_ocr_axis_ticks(
    candidates: list[tuple[float, float]],
    grid_positions: list[float],
    what: str,
    *,
    fit_on_rails: bool = False,
) -> list[tuple[float, float]]:
    """Return the unique largest OCR subset proving one source grid scale.

    OCR can lose a minus or split a digit.  Such a token is discarded only
    when at least four other source labels uniquely fit one linear axis to
    within a raster pixel and own distinct observed grid rails.  Values are
    never interpolated from the grid.

    ``fit_on_rails`` tests each subset on the rails its labels own instead of
    the label centres: raster glyph centres scatter ~2 px around their rule,
    which the 1 px test (made for exact vector text) rejects.  The returned
    pairs still carry the label centres.
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
                    [(value, rail if fit_on_rails else label_px) for value, label_px, rail in subset], what
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


def ocr_gutter_linear_axes(
    chart: dict,
    transform: CropTransform,
    plot: PlotBox,
    x_grid: list[float],
    y_grid: list[float],
    x_name: str = "X axis (Tj)",
    y_name: str = "Y axis (V(BR)DSS)",
) -> tuple[LinearAxis, LinearAxis]:
    """OCR the bounded tick gutters of a panel that has no native tick text.

    Used where the glyphs are vector paths or the whole figure is one embedded
    image.  Each OCR value must own a grid rule (``_trustworthy_ocr_axis_ticks``).
    """

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
    x_ticks = _trustworthy_ocr_axis_ticks(x_candidates, x_grid, f"{x_name} OCR")
    y_ticks = _trustworthy_ocr_axis_ticks(y_candidates, y_grid, f"{y_name} OCR")
    return _fit_axis(x_ticks, x_name), _fit_axis(y_ticks, y_name)


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
