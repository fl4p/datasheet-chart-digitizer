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

from .axis_calibration import _exact_number_token, power_tick_words
from .capacitance_retrace import Frame
from .capacitance_types import AxisCalibration, PlotBox
from .gridline_anchor import (
    AnchoredAxis,
    GridCheck,
    anchor_axis_on_grid_attempts,
    check_served_on_grid_attempts,
)
from .numeric_axis import AxisTick, NumericAxis, fit_axis_ticks

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


def _frame(plot: PlotBox) -> Frame:
    return Frame(float(plot.x0), float(plot.y0), float(plot.x1), float(plot.y1))


def _anchor(gray, plot, ticks, model, orientation, cross_span, name) -> tuple[AnchoredAxis, str]:
    label_axis = fit_axis_ticks(ticks, name, model=model)  # type: ignore[arg-type]
    return anchor_axis_on_grid_attempts(
        gray, label_axis, frame=_frame(plot), orientation=orientation,
        cross_span=cross_span, name=name, broken_rules=True,
    )


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
    return check_served_on_grid_attempts(
        gray, served, ticks, frame=_frame(plot), orientation=axis, cross_span=span,
        name=f"capacitance {axis}", broken_rules=True,
    )


def _hidden_boxes(page) -> list[tuple[float, float, float, float]]:
    """Boxes of text a reader cannot see: white fill, zero alpha, or neither
    filled nor stroked (render mode 3)."""
    boxes = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                flags = span.get("char_flags")
                if (span.get("color") == 0xFFFFFF and not (flags or 0) & 32) or span.get("alpha") == 0 or (
                    flags is not None and not flags & 48
                ):
                    boxes.append(tuple(span["bbox"]))
    return boxes


def _label_position(words, unread, hidden, band, accepts) -> float | None:
    """Centre (pt, along the axis) of the one visible label *accepts* names in *band*."""
    (x0, x1), (y0, y1), along = band
    found = set()
    for w, is_unread in [(w, False) for w in words] + [(w, True) for w in unread]:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if not (x0 < cx < x1 and y0 < cy < y1) or not accepts(w[4].strip(), is_unread):
            continue
        if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in hidden):
            continue
        found.add(round(cx if along == "x" else cy, 1))
    return found.pop() if len(found) == 1 else None


def check_count_bound_axes(
    calibration: AxisCalibration, image: np.ndarray, plot: PlotBox, page, transform, plot_rect
) -> AxisCalibration:
    """Check a calibration whose tick values were bound to the plot by count or
    order, not by label position: find every consumed tick's visible printed
    label (the position tier's label bands) and check the SERVED mapping at it.
    An axis with a label not found once stays ``unverified``."""
    words, unread = power_tick_words(page)
    hidden = _hidden_boxes(page)
    y_band = ((plot_rect.x0 - 42.0, plot_rect.x0 - 1.0), (plot_rect.y0 - 8.0, plot_rect.y1 + 8.0), "y")
    x_band = ((plot_rect.x0 - 24.0, plot_rect.x1 + 12.0), (plot_rect.y1 + 2.0, plot_rect.y1 + 24.0), "x")

    def y_accepts(d):
        def accepts(text, is_unread):
            if is_unread:
                return text == f"10{int(d)}"
            return text == f"10^{int(d)}" or _exact_number_token(text) == 10.0**d
        return accepts

    def x_accepts(v):
        return lambda text, is_unread: not is_unread and _exact_number_token(text) == v

    y_pt = [_label_position(words, unread, hidden, y_band, y_accepts(d)) for d in calibration.y_decades]
    x_pt = [_label_position(words, unread, hidden, x_band, x_accepts(v)) for v in calibration.x_ticks_v]
    updates: dict[str, object] = {}
    if None not in y_pt:
        updates["y_tick_label_px"] = tuple(transform.to_px(plot_rect.x0, y)[1] for y in y_pt)
    if None not in x_pt:
        updates["x_tick_label_px"] = tuple(transform.to_px(x, plot_rect.y1)[0] for x in x_pt)
    calibration = replace(calibration, **updates)
    gray = _gray(image)
    missing = {
        "x": [f"{v:g}" for v, p in zip(calibration.x_ticks_v, x_pt) if p is None],
        "y": [f"1e{d:g}" for d, p in zip(calibration.y_decades, y_pt) if p is None],
    }
    checks = {
        axis: GridCheck("unverified", f"capacitance {axis}: no single visible label for {missing[axis]}", None, ())
        if missing[axis] else check_axis_on_grid(calibration, gray, plot, axis)
        for axis in ("x", "y")
    }
    return replace(calibration, x_grid_check=checks["x"].payload(), y_grid_check=checks["y"].payload())


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
