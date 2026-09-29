"""Seat capacitance tick-label calibrations on the gridlines they name.

``axis_calibration.calibrate_axes`` fits both capacitance axes to tick-LABEL
glyph centres (PDF words or OCR boxes). Two narrower seaters in
``capacitance_axis`` already move an arithmetic pF axis and a regular log VDS
axis onto the grid; the common chart -- linear VDS x, log-decade pF y -- was
served straight from the label centres, with residuals measured against the
same labels. This module re-seats every axis that is still label-served with
``gridline_anchor.anchor_axis_on_grid`` (labels identify values, observed
gridlines/tick marks carry pixels) and then checks the SERVED mapping of both
axes, whichever seater produced it, with ``check_served_on_grid``.

The check verdict is carried on the calibration (``x_grid_check`` /
``y_grid_check``). ``grid_check_error`` turns anything but ``verified`` into a
refusal string for the caller: an axis whose served mapping misses the grid,
or whose grid could not be bound to its labels, is not trusted.
"""

from __future__ import annotations

import math
from dataclasses import replace

import cv2
import numpy as np

from .capacitance_retrace import Frame, suppress_curve_ink
from .capacitance_types import AxisCalibration, PlotBox
from .gridline_anchor import AnchoredAxis, GridCheck, anchor_axis_on_grid, check_served_on_grid
from .numeric_axis import AxisTick, NumericAxis, fit_axis_ticks

# Rules that render lighter than the default ink threshold (hairline grey
# grids) are retried at this threshold before the axis is given up.
_LIGHT_RULE_INK_THRESHOLD = 225
# gridline_anchor reports pixel-INDEX centres (pixel i centred at i); the
# capacitance calibration, its label pixels (CropTransform.to_px), its vector
# traces and its own raster seaters ("row indices name the TOP edge of a pixel
# cell") use CONTINUOUS coordinates (pixel i covers [i, i+1)). Index + 0.5 is
# continuous.
_INDEX_TO_CONTINUOUS = 0.5


def _to_index(axis: NumericAxis) -> NumericAxis:
    """A continuous-coordinate mapping re-expressed for index coordinates."""
    return NumericAxis(axis.model, axis.m, axis.b + axis.m * _INDEX_TO_CONTINUOUS, (), 0.0, ())


def _index_ticks(ticks: list[AxisTick]) -> list[AxisTick]:
    return [AxisTick(t.text, t.value, t.pixel - _INDEX_TO_CONTINUOUS, t.normalized_text) for t in ticks]


def _attempts(gray: np.ndarray, plot: PlotBox):
    """Line-evidence variants, most trustworthy first.

    Curve ink is suppressed first: a steep curve hugging the 0 V frame, or a
    plateau lying on a rule, widens or drags that line's detected run
    (capacitance_retrace.suppress_curve_ink). The raw raster follows for grids
    whose rules are broken by labels (suppression keeps only near-full rules).
    Every labelled tick must sit on a line first; a secondary pass admits
    INTERIOR labels between gridlines as identity-only (AO prints 50 pF labels
    on a 100 pF grid) while the end labels must still sit on lines.
    """
    frame = Frame(float(plot.x0), float(plot.y0), float(plot.x1), float(plot.y1))
    images = (("curve_ink_suppressed", suppress_curve_ink(gray, frame)), ("raw", gray))
    for policy in ("refuse", "identity_only"):
        for threshold in (None, _LIGHT_RULE_INK_THRESHOLD):
            for image_name, image in images:
                kwargs: dict[str, object] = {"unlined_labels": policy}
                if threshold is not None:
                    kwargs["ink_threshold"] = threshold
                yield f"{image_name}/{policy}/ink<{threshold or 200}", image, kwargs


def _gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image


def _label_ticks(calibration: AxisCalibration, axis: str) -> tuple[list[AxisTick], str] | None:
    """(value, label pixel) ticks of *axis* as the calibration consumed them."""
    if axis == "x":
        values = calibration.x_ticks_v
        pixels = calibration.x_tick_label_px
        model = "log10" if calibration.x_log else "linear"
    else:
        if calibration.y_log:
            values = tuple(10.0**d for d in calibration.y_decades)
        else:
            values = calibration.y_ticks_pf
        pixels = calibration.y_tick_label_px
        model = "log10" if calibration.y_log else "linear"
    if not pixels or len(pixels) != len(values):
        return None
    return [AxisTick(f"{v:g}", float(v), float(p)) for v, p in zip(values, pixels)], model


def _index_span(span: tuple[float, float]) -> tuple[float, float]:
    return (span[0] - _INDEX_TO_CONTINUOUS, span[1] - _INDEX_TO_CONTINUOUS)


def _served(calibration: AxisCalibration, axis: str) -> NumericAxis | None:
    if axis == "x":
        scale, offset, log = calibration.x_scale, calibration.x_offset, calibration.x_log
    else:
        scale, offset, log = calibration.y_scale, calibration.y_offset, calibration.y_log
    if scale is None or offset is None:
        return None
    return NumericAxis("log10" if log else "linear", float(scale), float(offset), (), 0.0, ())


def _anchor(gray, plot, ticks, model, orientation, cross_span, name) -> tuple[AnchoredAxis, str]:
    label_axis = fit_axis_ticks(ticks, name, model=model)  # type: ignore[arg-type]
    first: RuntimeError | None = None
    for attempt, image, kwargs in _attempts(gray, plot):
        try:
            return anchor_axis_on_grid(
                image, label_axis, orientation=orientation, cross_span=cross_span,
                name=name, **kwargs,
            ), attempt
        except RuntimeError as exc:
            first = first or exc
    assert first is not None
    raise first


def _value_residual(anchored: AnchoredAxis) -> float:
    """RMS of the anchored fit at the line centres, in the axis coordinate
    (volts / pF, or decades on a log axis) -- the unit the reject gate reads."""
    axis = anchored.axis
    errors = []
    for anchor in anchored.anchors:
        coordinate = math.log10(anchor.value) if axis.model == "log10" else anchor.value
        errors.append(axis.m * anchor.line_px + axis.b - coordinate)
    return float(math.sqrt(sum(e * e for e in errors) / len(errors)))


def anchor_capacitance_axes_on_grid(
    calibration: AxisCalibration, image: np.ndarray, plot: PlotBox
) -> AxisCalibration:
    """Re-seat still-label-served axes on the grid, then check both axes."""
    gray = _gray(image)
    spans = {"x": (float(plot.y0), float(plot.y1)), "y": (float(plot.x0), float(plot.x1))}
    updates: dict[str, object] = {}
    for axis in ("x", "y"):
        source = calibration.x_source if axis == "x" else calibration.y_source
        if source is None:
            continue
        labelled = _label_ticks(calibration, axis)
        if labelled is None:
            continue
        ticks, model = labelled
        # Every axis with per-label pixels is anchored here, including one an
        # earlier seater already moved: those seaters bind by nearest line
        # (the linear-Y one on VECTOR rules, which on AOT2610L sit up to 1.8 px
        # off the rendered rules the raster curves are traced against). If
        # this anchor cannot bind the grid, the earlier seater's result stays
        # and the guard below judges it.
        try:
            anchored, attempt = _anchor(
                gray, plot, _index_ticks(ticks), model, axis, _index_span(spans[axis]),
                f"capacitance {axis}",
            )
        except RuntimeError as exc:
            updates[f"{axis}_grid_anchor_error"] = str(exc)
            continue
        base_source = source.replace("_grid_seated", "")
        # served in continuous coordinates: coord = m*index + b, index = px - 0.5
        updates[f"{axis}_scale"] = anchored.axis.m
        updates[f"{axis}_offset"] = anchored.axis.b - anchored.axis.m * _INDEX_TO_CONTINUOUS
        updates[f"{axis}_source"] = f"{base_source}_grid_anchored"
        updates[f"{axis}_grid_anchor_attempt"] = attempt
        payload = anchored.payload()
        payload["pixel_convention"] = "index (add 0.5 for this calibration's continuous crop px)"
        updates[f"{axis}_grid_anchoring"] = payload
        # one rule per consumed tick, in the calibration's own tick order
        # (as the earlier seaters stored it); identity-only ticks carry None
        line_by_value = {a.value: a.line_px + _INDEX_TO_CONTINUOUS for a in anchored.anchors}
        updates[f"{axis}_gridline_px"] = tuple(line_by_value.get(t.value) for t in ticks)
        updates[f"{axis}_label_to_grid_max_px"] = max(abs(a.label_offset_px) for a in anchored.anchors)
        updates[f"{axis}_grid_residual_px"] = anchored.axis.residual_px
        residual = _value_residual(anchored)
        if axis == "x":
            updates["x_resid_v"] = residual
        elif calibration.y_log:
            updates["y_resid_dec"] = residual
        else:
            updates["y_resid_pf"] = residual
    if updates:
        calibration = replace(calibration, **updates)
    checks = {axis: check_axis_on_grid(calibration, gray, plot, axis) for axis in ("x", "y")}
    return replace(
        calibration,
        x_grid_check=checks["x"].payload(),
        y_grid_check=checks["y"].payload(),
    )


def check_axis_on_grid(
    calibration: AxisCalibration, gray: np.ndarray, plot: PlotBox, axis: str
) -> GridCheck:
    """The guard on the SERVED scale/offset of one axis, at every label."""
    labelled = _label_ticks(calibration, axis)
    served = _served(calibration, axis)
    if labelled is None or served is None:
        return GridCheck(
            "unverified",
            f"capacitance {axis}: no per-tick label pixels or no served mapping to check",
            None,
            (),
        )
    ticks, _model = labelled
    span = (float(plot.y0), float(plot.y1)) if axis == "x" else (float(plot.x0), float(plot.x1))
    # the guard works on the raster in index coordinates: move the served
    # continuous mapping and the label pixels there, never the lines
    served = _to_index(served)
    ticks = _index_ticks(ticks)
    span = _index_span(span)
    # The first line-evidence variant that can BIND the labels to lines gives
    # the verdict (verified or failed); only if none can is it unverified.
    first: GridCheck | None = None
    for attempt, image, kwargs in _attempts(gray, plot):
        check = check_served_on_grid(
            image, served, ticks, orientation=axis, cross_span=span,
            name=f"capacitance {axis}", **kwargs,
        )
        if check.status != "unverified":
            return GridCheck(check.status, f"{check.reason} [{attempt}]", check.tolerance_px, check.ticks)
        first = first or check
    assert first is not None
    return first


def grid_check_problems(calibration: AxisCalibration | None) -> tuple[list[str], list[str]]:
    """(failed, unverified) axis descriptions for a served calibration.

    ``failed``: the served mapping misses the line a labelled tick names --
    the caller must not trust the axis. ``unverified``: the check could not
    bind labels to lines, or never ran on this calibration path -- not a
    pass; the caller must not report the chart ok.
    """
    failed: list[str] = []
    unverified: list[str] = []
    if calibration is None:
        return failed, unverified
    for axis in ("x", "y"):
        check = getattr(calibration, f"{axis}_grid_check")
        if check is None:
            unverified.append(f"{axis}: grid check not run on the {calibration.source} path")
        elif check["status"] == "failed":
            failed.append(f"{axis}: {check['reason']}")
        elif check["status"] != "verified":
            unverified.append(f"{axis}: {check['reason']}")
    return failed, unverified
