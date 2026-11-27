"""Transfer characteristics on a whole-figure raster panel (Toshiba stratum).

The vector transfer path needs PDF text for the temperature labels and vector
paths for the curves; a panel that is one embedded image has neither.  Here:

* axes: per-label OCR (``raster_tick_ocr``) bound to the grid rules, snapped
  and checked by ``served_axis_guard`` exactly as the vector path is;
* curves: ``raster_figure.trace_curves_by_rows`` at native resolution, one
  centreline per steep curve where the rows separate them;
* temperatures: ``raster_plot_labels`` reads the in-plot labels and binds each
  by its leader or outer side.  The binding must cover every curve once and
  must agree with the established hot-left order at a shared low current
  (``_assign_temperatures``); a disagreement refuses.  With an incomplete print
  binding the physics order is served and the gap is recorded.

Status is always ``overlay-review-required`` (the transfer class contract).
"""

from __future__ import annotations

import csv
from pathlib import Path

import cv2
import numpy as np

from . import served_axis_guard
from .capacitance_traces import find_plot_box
from .capacitance_vector import _load_fitz
from .crop_transform import CropTransform
from .diode_forward_voltage import _full_span_grid_lines
from .linear_tick_axis import _as_numeric_axis, _snap_axis_ticks_to_grid
from .raster_figure import _runs, curve_mask, render_native, rule_width, trace_curves_by_rows
from .raster_plot_labels import bind_labels, label_ink, rule_bands, temperature_labels, tokens_and_leaders
from .raster_tick_ocr import gutter_label_boxes, raster_linear_axes
from . import visible_text


def _native_render_for(chart: dict, page, transform: CropTransform, shape, plot, fitz):
    p0 = transform.to_pt(plot.x0, plot.y0)
    p1 = transform.to_pt(plot.x1, plot.y1)
    return render_native(page, chart["crop_box_pt"], shape, (p0[0], p0[1], p1[0], p1[1]), fitz)


def is_raster_figure(chart: dict, crop_path: Path) -> bool:
    """True when an embedded image covers the panel's plot frame."""
    fitz = _load_fitz()
    gray = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if fitz is None or gray is None or not chart.get("crop_box_pt"):
        return False
    try:
        plot = find_plot_box(gray)
    except (RuntimeError, ValueError):
        return False
    transform = CropTransform.for_chart(chart, gray.shape)
    with fitz.open(str(chart["pdf"])) as doc:
        page = doc[int(chart["page"]) - 1]
        p0, p1 = transform.to_pt(plot.x0, plot.y0), transform.to_pt(plot.x1, plot.y1)
        if visible_text.words(page, clip=fitz.Rect(*p0, *p1)):
            return False  # native text inside the plot: not a whole-figure raster
        plot_rect = fitz.Rect(*p0, *p1)
        if sum(1 for d in page.get_drawings() if fitz.Rect(d["rect"]).intersects(plot_rect)) > 2:
            return False  # vector ink in the plot: the vector path owns it
        return _native_render_for(chart, page, transform, gray.shape, plot, fitz) is not None


def process_raster_transfer(chart: dict, crop_path: Path, out_dir: Path, rel_stem: Path, anchor=None) -> dict:
    from .transfer_characteristics import _assign_temperatures, _draw_overlay, fit_saturation_tempco, TransferCurve

    fitz = _load_fitz()
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    transform = CropTransform.for_chart(chart, gray.shape)
    plot = find_plot_box(gray)
    x_grid, y_grid = _full_span_grid_lines(gray, plot, plot)
    with fitz.open(str(chart["pdf"])) as doc:
        page = doc[int(chart["page"]) - 1]
        native = _native_render_for(chart, page, transform, gray.shape, plot, fitz)
        if native is None:
            raise RuntimeError("raster transfer: no embedded image covers the plot")
        x_lab, y_lab = raster_linear_axes(native, plot, x_grid, y_grid, "X axis (VGS)", "Y axis (ID)")
        x_axis, x_assert = _snap_axis_ticks_to_grid(x_lab, x_grid, "X axis (VGS)")
        y_axis, y_assert = _snap_axis_ticks_to_grid(y_lab, y_grid, "Y axis (ID)")
        checks = served_axis_guard.check_axes(
            gray, plot,
            {"x": _as_numeric_axis(x_axis), "y": _as_numeric_axis(y_axis)},
            {"x": _as_numeric_axis(x_lab), "y": _as_numeric_axis(y_lab)},
            "transfer",
        )
        failed, unverified = served_axis_guard.problems(checks)
        if failed:
            raise RuntimeError("served axis misses its gridlines: " + "; ".join(failed))
        served_axis_guard.refuse_unevaluable(checks, "transfer")
        widest = rule_width(native, plot, x_grid, y_grid)
        mask, kernel = curve_mask(native, plot, widest)
        _boxes, glyph_h = gutter_label_boxes(native, plot, "y")
        if glyph_h <= 0:
            raise RuntimeError("raster transfer: no tick-label glyph height")
        # provisional stroke for label isolation; the row tracer measures its own
        rows_probe = [b - a for r in np.flatnonzero(mask.any(axis=1)) for a, b in _runs(mask[r])]
        stroke = float(np.median(rows_probe))
        bands = rule_bands(native, x_grid, y_grid, widest, int(2 * glyph_h))
        ink = label_ink(native, plot, mask, stroke, bands)
        tokens, leaders, pieces = tokens_and_leaders(native, ink, bands, glyph_h)
    labels = temperature_labels(tokens, glyph_h)
    values = sorted({lab.value for lab in labels})
    if len(values) != len(labels) or not 2 <= len(values) <= 6:
        raise RuntimeError(
            f"raster transfer: expected 2..6 distinct temperature labels, read "
            f"{[lab.value for lab in labels]} (tokens: {[(t.plain, t.digits) for t in tokens]})"
        )
    rows = trace_curves_by_rows(native, mask, len(values))
    curves_native = [[(native.to_native(x), native.to_native(y)) for x, y in c] for c in rows.curves_crop]
    bindings = bind_labels(labels, leaders, curves_native, glyph_h, rows.stroke_px, widest // 2 * 2 + 3, pieces,
                           native.gray < 160)

    calibrated = [
        sorted(((x_axis.value(x), max(0.0, y_axis.value(y))) for x, y in c), key=lambda p: p[1])
        for c in rows.curves_crop
    ]
    physics = _assign_temperatures(calibrated, values) if len(values) > 2 else None
    bound = {b["curve"]: b["value"] for b in bindings if b.get("curve") is not None}
    print_complete = len(bound) == len(values) and len(set(bound.values())) == len(values)
    if print_complete:
        curves = [TransferCurve(bound[i], calibrated[i]) for i in range(len(values))]
        if physics is not None:
            by_id = {id(c.points): c.tj_c for c in physics}
            if any(by_id[id(c.points)] != c.tj_c for c in curves):
                raise RuntimeError("printed label binding contradicts the hot-left order at low current")
        binding_rule = "print" + ("+physics_agrees" if physics is not None else "")
    elif physics is not None:
        curves = physics
        binding_rule = "physics_rank (print binding incomplete)"
    else:
        raise RuntimeError("two-curve raster transfer needs a complete printed label binding")
    curves = sorted(curves, key=lambda c: c.tj_c)
    pixel_by_id = {id(c): p for c, p in zip(calibrated, rows.curves_crop)}
    assigned_crop = [pixel_by_id[id(c.points)] for c in curves]
    fit = fit_saturation_tempco(curves, anchor) if anchor is not None else None

    csv_path = out_dir / "points" / rel_stem.parent / f"{rel_stem.name}.transfer_points.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Tj_C", "Vgs_V", "Id_A"])
        for curve in curves:
            for vgs, current in curve.points:
                writer.writerow([f"{curve.tj_c:.2f}", f"{vgs:.5f}", f"{current:.5f}"])
    draw = [[(int(round(x)), int(round(y))) for x, y in c] for c in assigned_crop]
    overlay = _draw_overlay(image, plot, draw, curves, _as_numeric_axis(x_axis), _as_numeric_axis(y_axis), fit)
    overlay_path = out_dir / "overlays" / rel_stem.parent / f"{rel_stem.name}.transfer_overlay.png"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)

    warnings = [
        "HUMAN REVIEW REQUIRED: raster-figure transfer (OCR axes and labels, native-resolution "
        "row trace); verify axes, every centreline and the temperature binding"
    ]
    if not print_complete:
        warnings.append("printed label binding incomplete; temperatures served by the hot-left order")
    if unverified:
        warnings.append("AXIS GRID CHECK UNVERIFIED: " + "; ".join(unverified))
    status = "guard-refusal-cold-anchor-conflict" if fit and fit["cold_anchor_conflict"] else "overlay-review-required"
    return {
        "status": status,
        "method": "raster_native_rows",
        "temperatures_c": [c.tj_c for c in curves],
        "temperature_binding": {"rule": binding_rule, "labels": bindings},
        "n_points": {f"{c.tj_c:g}C": len(c.points) for c in curves},
        "calibration": {
            "x_ticks": len(x_axis.ticks), "y_ticks": len(y_axis.ticks),
            "x_axis": {"model": "linear", "m": x_axis.m, "b": x_axis.b},
            "y_axis": {"model": "linear", "m": y_axis.m, "b": y_axis.b},
            "exact_center_assertions": {"x": x_assert, "y": y_assert},
            "grid_check": served_axis_guard.payload(checks),
        },
        "raster_figure": {
            "native_dpi": round(native.dpi, 1), "widest_rule_px": widest, "opening_kernel_px": kernel,
            "rows_traced": rows.rows_traced, "rows_skipped": rows.rows_skipped,
            "stroke_px": rows.stroke_px, "glyph_h_px": glyph_h,
        },
        "fit": fit,
        "warnings": warnings,
        "csv": str(csv_path),
        "overlay": str(overlay_path),
    }

