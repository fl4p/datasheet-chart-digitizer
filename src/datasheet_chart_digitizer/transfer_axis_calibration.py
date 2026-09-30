"""VGS/ID axis calibration for transfer-characteristic panels.

Tick selection uses the transfer label gutters (Renesas prints the VGS numbers
about 0.20 plot-heights below the frame) and the shared fitters. The ID axis
reads the shared current-label grammar (``10m``, ``1k``, ``1E-2``,
``1.0E-03``, ``10⁻²``), so an SI- or E-notation decade ladder is not
"only 0 tick labels".

Sparse axes. Four labels are the default minimum. An axis printed with only
three labels (``0 4 8`` V, ``-1000 -600 -200`` A) is admitted only as a
*sparse* axis, and a sparse axis is served only if:

* its three values are equally stepped (linear steps or log decades) and
  their label pixels equally spaced;
* the frame extends at most about one unlabelled interval beyond either
  outer label (no extrapolation across two or more unseen intervals);
* every one of its labels is seated on an observed rule or tick mark and the
  served mapping lands on those lines (``served_axis_guard`` status
  ``verified``; ``unverified`` refuses, it is never a pass).

The first two gates run here; the seating gate is
``require_seated_sparse_axes``, called after the served-axis grid check.
"""

from __future__ import annotations

import math

import numpy as np

from .breakdown_voltage import NUM_RE, _fit_axis
from .capacitance_types import PlotBox
from .numeric_axis import AxisTick, fit_axis_ticks, parse_tick_text

MIN_TICKS = 4
SPARSE_TICKS = 3
# a printed frame may overhang the outer label by one unlabelled interval
# (numeric_axis._OUTER_FRAME_STEP_TOLERANCE is the shared 18 % slack)
_ONE_INTERVAL = 1.18
_PIXEL_STEP_TOLERANCE = 0.04


def sparse_axis_refusal(name: str, values, pixels, lo: float, hi: float, log: bool) -> str | None:
    """Why a three-label axis is not bounded by its labels, or None."""

    order = np.argsort(pixels)
    px = np.asarray(pixels, dtype=float)[order]
    val = np.asarray(values, dtype=float)[order]
    if log:
        if np.any(val <= 0):
            return f"{name}: log axis with a non-positive label"
        val = np.log10(val)
    steps = np.diff(val)
    if not (np.all(steps > 0) or np.all(steps < 0)) or not math.isclose(steps[0], steps[1], rel_tol=1e-6):
        return f"{name}: 3 labels are not equally stepped in value"
    gaps = np.diff(px)
    pitch = float(np.mean(gaps))
    if abs(gaps[0] - gaps[1]) > _PIXEL_STEP_TOLERANCE * pitch + 1.0:
        return f"{name}: 3 labels are not equally spaced ({gaps[0]:.1f} vs {gaps[1]:.1f} px)"
    overhang = max(px[0] - lo, hi - px[-1])
    if overhang > _ONE_INTERVAL * pitch:
        return (
            f"{name}: frame extends {overhang / pitch:.2f} label intervals beyond the outer "
            "label; serving would extrapolate across unseen intervals"
        )
    return None


def require_seated_sparse_axes(grid_checks, sparse_axes) -> None:
    """Refuse a sparse axis unless each label is seated on an observed rule."""

    for axis in sorted(sparse_axes):
        check = grid_checks.get(axis)
        if check is None or check.status != "verified":
            reason = "grid check not run" if check is None else f"{check.status}: {check.reason}"
            raise RuntimeError(f"{axis} axis has only 3 labels and they are not all seated on observed rules ({reason})")


def select_horizontal_tick_row(
    candidates: list[tuple[float, float, float]], plot: PlotBox, *, min_labels: int = MIN_TICKS
) -> list[tuple[float, float]]:
    """Select one evidenced tick-label row below a transfer plot.

    Conditions such as ``VDS = 5 V`` can sit farther below the frame inside the
    deliberately generous transfer gutter.  A tick row has at least
    ``min_labels`` labels, spans most of the frame, and is monotone in pixel
    order; a lone condition number cannot join it merely because it is numeric.
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
        if len(ordered) < min_labels:
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


def calibrate_transfer(words_px, plot: PlotBox, power_labels=()):
    """Fit the VGS (x) and ID (y) axes; return (x_axis, y_axis, sparse axis names).

    ``power_labels`` are ``(value, cx, cy, bbox)`` crop-pixel ``10^n`` labels
    recovered from base + superscript spans (``axis_calibration``); the plain
    words they are made of are replaced by them on the ID axis.
    """

    sparse: set[str] = set()
    x_candidates = [
        (float(text), cx, cy)
        for text, cx, cy in words_px
        if NUM_RE.fullmatch(text)
        and plot.y1 + 0.005 * plot.height <= cy <= plot.y1 + 0.25 * plot.height
        and plot.x0 - 0.03 * plot.width <= cx <= plot.x1 + 0.14 * plot.width
    ]
    try:
        x_ticks = select_horizontal_tick_row(x_candidates, plot)
        x_ticks = _fit_axis(x_ticks, "X axis (VGS)").ticks
    except RuntimeError as dense_refusal:
        try:
            x_ticks = select_horizontal_tick_row(x_candidates, plot, min_labels=SPARSE_TICKS)
        except RuntimeError:
            raise dense_refusal from None
        if len(x_ticks) != SPARSE_TICKS:
            raise dense_refusal
        reason = sparse_axis_refusal(
            "X axis (VGS)", [v for v, _ in x_ticks], [p for _, p in x_ticks], plot.x0, plot.x1, log=False
        )
        if reason:
            raise RuntimeError(reason) from None
        sparse.add("x")
    def inside(word, b):
        return b[0] - 1 <= word[1] <= b[2] + 1 and b[1] - 1 <= word[2] <= b[3] + 1

    y_words = [w for w in words_px if not any(inside(w, p[3]) for p in power_labels)]
    for value, cx, cy, box in power_labels:
        # centre on the "10" base glyph: the raised exponent is not the tick
        base = [w[2] for w in words_px if w[0] == "10" and inside(w, box)]
        y_words.append((f"10^{round(math.log10(value))}", cx, base[0] if len(base) == 1 else cy))
    y_ticks = list(dict.fromkeys(
        (value, cy)
        for text, cx, cy in y_words
        if (value := parse_tick_text(text, "current_a")) is not None
        and plot.x0 - 0.22 * plot.width <= cx <= plot.x0 - 2
        and plot.y0 - 0.02 * plot.height <= cy <= plot.y1 + 0.02 * plot.height
    ))
    if len(y_ticks) < SPARSE_TICKS:
        raise RuntimeError(f"Y axis (ID): only {len(y_ticks)} tick labels, need >={MIN_TICKS}")
    y_axis = fit_axis_ticks(
        [AxisTick(f"{value:g}", value, pixel) for value, pixel in y_ticks],
        "Y axis (ID)",
        model="auto",
    )
    if len(y_ticks) == SPARSE_TICKS:
        reason = sparse_axis_refusal(
            "Y axis (ID)", [v for v, _ in y_ticks], [p for _, p in y_ticks], plot.y0, plot.y1,
            log=y_axis.model == "log10",
        )
        if reason:
            raise RuntimeError(reason)
        sparse.add("y")
    x_axis = fit_axis_ticks(
        [AxisTick(f"{value:g}", value, pixel) for value, pixel in x_ticks],
        "X axis (VGS)",
        model="linear",
    )
    return x_axis, y_axis, sparse
