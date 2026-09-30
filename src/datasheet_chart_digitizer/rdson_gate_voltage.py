"""Digitize MOSFET RDS(on)-versus-VGS charts, validated against the spec table.

Usage::

    dsdig digitize-rds-vgs charts.json --out DIR      # PDFs named in a find index
    dsdig digitize-rds-vgs --pdf a.pdf b.pdf --out DIR

What a panel yields (``rdson_gate_voltage.json``, one CSV and one overlay each):

  * calibrated (VGS [V], RDS(on) [mOhm]) points per curve, each curve carrying
    the Tj / ID its label binds to, or ``None`` when no label binds -- never a
    guess;
  * readouts at VGS = 2.5, 3.3 and 4.5 V per curve, interpolated ALONG the
    curve, or "not_on_chart" when that VGS is outside the curve's plotted
    span (no extrapolation, ever). Every readout is labelled a typical-curve
    reading, not a guaranteed value;
  * a tri-state validation against the datasheet's RDS(on) table rows:
    ``verified`` / ``inconsistent`` / ``not_evaluable``. A missing anchor is
    ``not_evaluable``, never a pass.

Panel status is ``ok`` only when every gate passes AND validation is
``verified``. Otherwise ``review_required`` (data served, with the reasons a
human must look at) or ``refused`` (nothing trustworthy to serve: the axes
could not be calibrated, the RDS unit could not be read, no curve was traced,
or a curve rises with VGS -- which RDS(on) does not do). This is an
unvalidated chart class: ``ok`` means the automated gates passed, not that a
human has verified the overlay.

Guard checklist (~/.claude/CLAUDE.md), answered for this plugin:
  1. Unevaluable input -> refused / review_required / not_evaluable with a
     named reason; no path defaults to ok.
  2. Monotonicity: a larger rise, more clipping, more unbound labels, a
     larger table residual can only move a panel further from ok.
  3. Preconditions: the x axis must be owned (title names VGS, not ID), linear;
     the y unit must be read from the panel itself.
  4./5. No cache.  6. Every result records its trace method (vector / raster),
     tick source (text layer / OCR), label-binding method per parameter and the
     table row it was checked against.
  7./8. Known-bad fixtures are in tests/test_rdson_gate_voltage.py.
"""

from __future__ import annotations

import argparse
import json
import os
import math
import re
import shutil
from dataclasses import asdict, replace
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from .capacitance_types import PlotBox
from .crop_transform import CropTransform
from .diode_forward_voltage import TextLabel, _full_span_grid_lines
from .find_charts import PageText, group_words_into_lines, line_bbox, line_text
from .finder_types import Word
from .numeric_axis import axis_to_json
from .rdson_gate_voltage_axes import (
    CROP_DPI,
    Calibration,
    PanelRefused,
    _BoxedLabel,
    _calibrate,
    _ocr_axis_band_labels,
    _ocr_crop_labels,
    _plot_frame_px,
    _text_labels,
    _y_unit,
    ocr_plot_labels,
)
from .rdson_gate_voltage_labels import ocr_plot_labels_rules_erased, read_legend_boxes
from .rdson_gate_voltage_locate import KIND, LocatedPanel, locate_panels, upright_pdf
from .rdson_gate_voltage_report import (
    READOUT_NOTE,
    READOUT_VGS_V,
    readouts,
    validate_against_table,
    write_overlay,
    write_points,
)
from .rdson_gate_voltage_traces import (
    Label,
    Trace,
    backtrack_px,
    bind_labels,
    PARAM_START_RE,
    parse_label_params,
    temperature_kind,
    raster_leaders,
    raster_traces,
    vector_traces,
)
from .rdson_spec_table import RdsonSpecRow, parse_rdson_spec_rows
from .region_ocr import _tesseract_words

MAX_RISE_FRACTION = 0.04
GAP_PX = 2.5                    # consecutive trace columns further apart than this are a gap
USABLE_MIN_SPAN_FRACTION = 0.30
FRAME_CONTACT_PX = 2.5
FRAME_RUN_FRACTION = 0.03
MAX_BACKTRACK_PX = 3.0
MAX_MERGED_FRACTION = 0.05
MAX_PLAUSIBLE_VGS_AXIS_V = 30.0   # beyond any gate rating: a scale misread (e.g. "05" for 0.5)

# --------------------------------------------------------------------------- driver


def digitize_pdf(pdf: Path, out_dir: Path) -> tuple[list[dict], list[dict]]:
    """Locate and digitize every owned RDS(VGS) panel in ``pdf``."""
    # Tesseract's multi-threaded LSTM returned different digit readings for the
    # same tick image from run to run (measured on RQ3E180AJ, 2026-09-28);
    # one thread makes every OCR-dependent result reproducible.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    work = out_dir / "work"
    source = upright_pdf(pdf, work)
    located, refusals, panel_text = locate_panels(source, work)
    spec_rows = parse_rdson_spec_rows(source)
    results = []
    for panel in located:
        row = digitize_panel(panel, panel_text[(panel.page, panel.diagram)], spec_rows, out_dir)
        if source != pdf:
            row["pdf"] = str(pdf)
            row["rendered_from"] = (f"{source} (upright copy: the PDF stores rotated pages, see "
                                    "rdson_gate_voltage_locate.upright_pdf)")
        results.append(row)
    return results, [r.to_json() | ({"pdf": str(pdf)} if source != pdf else {}) for r in refusals]


def digitize_panel(
    panel: LocatedPanel, words: PageText, spec_rows: list[RdsonSpecRow], out_dir: Path
) -> dict:
    stem = f"p{panel.page:02d}_d{panel.diagram}"
    crop_path = out_dir / "crops" / panel.part / f"{stem}.png"
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(panel.pdf) as document:
        page = document[panel.page - 1]
        clip = pymupdf.Rect(panel.bbox_pt)
        pix = page.get_pixmap(dpi=CROP_DPI, clip=clip, alpha=False)
        pix.save(crop_path)
        image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
        transform = CropTransform.for_chart({"crop_box_pt": list(panel.bbox_pt)}, image.shape)
        row: dict = {
            "kind": KIND,
            "pdf": panel.pdf,
            "part": panel.part,
            "page": panel.page,
            "diagram": panel.diagram,
            "title": panel.title,
            "locator": panel.to_json(),
            "crop_png": str(crop_path.relative_to(out_dir)),
            "crop_box_pt": list(panel.bbox_pt),
            "crop_dpi": CROP_DPI,
            "pixel_mapping": (
                "crop pixel (u, v) <-> page point (crop_box_pt[0] + u / sx, crop_box_pt[1] + v / sy), "
                "sx = crop width px / crop_box width pt, sy likewise (both ~ crop_dpi / 72); "
                "calibration: coordinate = m * pixel + b on each axis (value = coordinate on a "
                "linear axis, 10**coordinate on log10); y values are in the axis's own unit, "
                "times y_to_mohm for mOhm"
            ),
            "readout_note": READOUT_NOTE,
            "spec_rows": [r.to_json() for r in spec_rows],
        }
        try:
            _digitize(panel, page, words, image, transform, spec_rows, row, out_dir, stem)
        except PanelRefused as error:
            row.update({"status": "refused", "reasons": [str(error)], "curves": row.get("curves", [])})
            row.setdefault("validation", {"verdict": "not_evaluable", "reason": "panel refused", "anchors": []})
            write_overlay(image, row, out_dir, panel, stem)
    return row


def _panel_words(page, ocr_words: PageText) -> PageText:
    """Text-layer words (PyMuPDF) plus the locator's OCR words for this panel."""
    text_layer = [
        Word(str(w[4]), float(w[0]), float(w[1]), float(w[2]), float(w[3]))
        for w in page.get_text("words") if str(w[4]).strip()
    ]
    return PageText(ocr_words.page_num, ocr_words.width_pt, ocr_words.height_pt,
                    [*text_layer, *ocr_words.words], ocr_words.text_source)


def _digitize(panel, page, words, image, transform, spec_rows, row, out_dir, stem) -> None:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    locator_words = words
    words = _panel_words(page, locator_words)
    # Keep each label's ORIGIN (review R3-8): the locator's words are OCR
    # (of the whole page when it has no text layer, or of the panel region),
    # never the text layer, which PyMuPDF supplies separately.
    layer_labels = _text_labels(
        PageText(words.page_num, words.width_pt, words.height_pt,
                 words.words[: len(words.words) - len(locator_words.words)], "text_layer"),
        transform, image.shape)
    locator_labels = _text_labels(locator_words, transform, image.shape)
    locator_name = _LOCATOR_SOURCE_NAMES.get(locator_words.text_source, f"locator_ocr ({locator_words.text_source})")
    labels = layer_labels + locator_labels
    ocr_labels: list[TextLabel] | None = None
    band_labels: list[TextLabel] | None = None

    def ocr() -> list[TextLabel]:
        nonlocal ocr_labels
        if ocr_labels is None:
            ocr_labels = _ocr_crop_labels(out_dir, panel, stem, image.shape)
        return ocr_labels

    hint = _plot_frame_px(panel, transform, gray)

    def bands() -> list[TextLabel]:
        nonlocal band_labels
        if band_labels is None:
            band_labels = _ocr_axis_band_labels(gray, hint, out_dir, panel, stem)
        return band_labels

    try:
        calibration = _calibrate(gray, hint, labels, "text_layer", transform, page)
    except (RuntimeError, ValueError) as first:
        try:
            calibration = _calibrate(gray, hint, labels + ocr(), "text_layer+ocr", transform, page)
        except (RuntimeError, ValueError) as second:
            try:
                calibration = _calibrate(gray, hint, labels + bands(), "text_layer+ocr_axis_bands", transform, page)
            except (RuntimeError, ValueError) as third:
                row["calibration_error"] = f"text layer: {first}; crop OCR: {second}; axis-band OCR: {third}"
                raise PanelRefused(f"axes_uncalibrated: {third}") from third
    calibration, completion = _complete_ticks(calibration, gray, hint, labels, ocr, bands, transform, page)
    sources = [("text_layer", layer_labels), (locator_name, locator_labels),
               ("crop_ocr", ocr_labels or []), ("axis_band_ocr", band_labels or [])]
    origins = {"x_axis": _tick_origins(calibration.x_axis, calibration.plot, "x", sources),
               "y_axis": _tick_origins(calibration.y_axis, calibration.plot, "y", sources)}
    used = [o for axis in origins.values() for o in axis]
    names = [name for name, _ in sources if name in used] + sorted({o for o in used if o not in dict(sources)})
    suffix = calibration.tick_source[calibration.tick_source.index(" ("):] if " (" in calibration.tick_source else ""
    calibration = replace(calibration, tick_source="+".join(names) + suffix)
    row["plot_box_px"] = asdict(calibration.plot)
    row["calibration"] = {
        "x_axis": axis_to_json(calibration.x_axis),
        "y_axis": axis_to_json(calibration.y_axis),
        "tick_source": calibration.tick_source,
        "tick_origins": {
            axis: [{"text": t.text, "value": t.value, "origin": o}
                   for t, o in zip(getattr(calibration, axis).ticks, origins[axis])]
            for axis in ("x_axis", "y_axis")
        },
        "used_tick_span": {axis: _used_span(getattr(calibration, axis)) for axis in ("x_axis", "y_axis")},
        "tick_completion": completion,
        "grid_binding": calibration.grid_binding,
        "x_quantity": "VGS [V]",
    }
    reasons: list[str] = []
    if calibration.x_axis.model != "linear":
        raise PanelRefused("x_axis_not_linear: the VGS axis calibrated as logarithmic")
    residual = calibration.tick_scatter_px
    row["calibration"]["tick_scatter_px_before_frame_anchoring"] = round(residual, 3)
    if residual > calibration.residual_limit_px:
        raise PanelRefused(f"axis_residual_{residual:.2f}px_exceeds_{calibration.residual_limit_px:.2f}px")
    if calibration.grid_binding != "snapped_to_full_span_grid":
        reasons.append(f"axis_ticks_not_bound_to_grid: {calibration.grid_binding}")
    vgs_max = max(t.value for t in calibration.x_axis.ticks)
    if vgs_max > MAX_PLAUSIBLE_VGS_AXIS_V:
        reasons.append(f"vgs_axis_reaches_{vgs_max:g}V_implausible_for_a_gate_drive_axis")

    unit_text, unit, scale = _y_unit(page, words, transform, calibration.plot, image, out_dir, panel, stem)
    row["calibration"]["y_quantity"] = f"RDS(on) [{unit}]" if unit else "RDS(on) [unit unread]"
    row["calibration"]["y_unit_evidence"] = unit_text
    if unit is None:
        raise PanelRefused(f"rdson_unit_unreadable: y-axis title {unit_text!r}")
    row["calibration"]["y_to_mohm"] = scale

    traces, swatches, leaders, method, plot_ocr = _traces(
        page, transform, calibration, image, gray, words, labels, ocr, out_dir, panel, stem, row
    )
    row["trace_method"] = method
    if not traces:
        raise PanelRefused(f"no_curve_traced ({method})")
    extra = None
    if method == "raster":
        extra = (ocr_labels or []) + plot_ocr
    plot_labels = _plot_labels(words, transform, calibration.plot, extra)
    if method == "raster":
        grid = row.get("raster_grid_rules_px", {})
        raster_lines, tail_labels = raster_leaders(
            gray, calibration.plot, traces, grid.get("x_erased", []), grid.get("y_erased", []), plot_labels,
            ocr_tail=lambda tail, head: _ocr_leader_tail(gray, tail, head, out_dir, panel, stem))
        leaders = list(leaders) + raster_lines
        plot_labels = plot_labels + tail_labels
        row["raster_leaders_px"] = [{"tail": [round(v, 1) for v in l.points[0]], "tip": [round(v, 1) for v in l.points[-1]]}
                                    for l in raster_lines]
        row["labels_read_at_arrow_tails"] = [l.text for l in tail_labels]
        plot_labels = _add_condition_labels(
            plot_labels, row,
            read_legend_boxes(gray, calibration.plot, grid.get("x_erased", []), grid.get("y_erased", []), out_dir, panel, stem),
            ocr_plot_labels_rules_erased(gray, calibration.plot, grid.get("x_erased", []), grid.get("y_erased", []),
                                         out_dir, panel, stem))
    binding_notes = bind_labels(traces, plot_labels, swatches, calibration.plot, leaders, transform.scale_x)
    binding_notes.extend(_apply_page_temperature_note(traces, page))
    curves, curve_reasons, refusal = _curves(traces, calibration, scale, gray)
    row["curves"] = curves
    row["label_binding_notes"] = binding_notes
    row["labels_seen"] = [{"text": l.text, "params": l.params} for l in plot_labels if l.params]
    reasons.extend(curve_reasons)
    if refusal:
        raise PanelRefused(refusal)
    reasons.extend(_binding_reasons(curves, plot_labels))
    row["validation"] = validate_against_table(curves, spec_rows, calibration, scale)
    reasons.extend(_flag_calibration_span(curves, row["validation"], calibration, scale))
    for note in row["validation"]["diagnostics"]:
        reasons.append(f"curve_{note['curve_index']}_exceeds_table_max_at_{note['vgs_v']:g}V ({note['text']})")
    if row["validation"]["verdict"] != "verified":
        reasons.append(f"validation_{row['validation']['verdict']}")
    row["status"] = "ok" if not reasons else "review_required"
    row["reasons"] = reasons
    row["points_csv"] = write_points(curves, out_dir, panel, stem)
    write_overlay(image, row, out_dir, panel, stem, calibration, unit)


# --------------------------------------------------------------------------- calibration


# --------------------------------------------------------------------------- traces


TICK_COMPLETION_MAX_SHIFT_PX = 1.5

_LOCATOR_SOURCE_NAMES = {
    "tesseract_fallback": "page_ocr",            # image-only page: whole page OCRed
    "text_layer+tesseract_panel": "panel_ocr",   # text page, raster panel: its region OCRed
}


def _coordinates(axis) -> list[float]:
    return sorted(axis.m * t.pixel + axis.b for t in axis.ticks)


def _step(coords: list[float]) -> float:
    steps = [b - a for a, b in zip(coords, coords[1:]) if b - a > 1e-9]
    return float(np.median(steps)) if steps else 0.0


def _used_span(axis) -> dict:
    """The used ticks' extent, in value and in crop px (review R3-9)."""
    ticks = sorted(axis.ticks, key=lambda t: t.value)
    return {"values": [ticks[0].value, ticks[-1].value], "pixels": [round(ticks[0].pixel, 2), round(ticks[-1].pixel, 2)],
            "n_ticks": len(ticks)}


def _uncovered(axis, lo_px: float, hi_px: float) -> list[tuple[float, float]]:
    """Stretches (coordinate) between the used ticks and the frame edges that
    are longer than half a tick step -- printed ticks may be missing there."""
    coords = _coordinates(axis)
    step = _step(coords)
    edges = sorted((axis.m * lo_px + axis.b, axis.m * hi_px + axis.b))
    out = []
    if step and coords[0] - edges[0] > 0.5 * step:
        out.append((edges[0], coords[0]))
    if step and edges[1] - coords[-1] > 0.5 * step:
        out.append((coords[-1], edges[1]))
    return out


def _complete_ticks(calibration: Calibration, gray, hint, labels, ocr, bands, transform, page):
    """Use every printed tick the sources provide (review R3-9).

    RQ3E110AJ's panel OCR read the y labels 10-30 but not the right-aligned
    single digits 0-8, and no x "0": the fit then served 3.3 and 4.5 V
    readouts below its lowest used tick. When the used ticks stop more than
    half a step short of a frame edge, the crop OCR and the axis-band OCR are
    added and the axes refitted. The refit is accepted only if it keeps every
    tick already used, its tick scatter stays within the residual limit, and it
    moves the fit by <= TICK_COMPLETION_MAX_SHIFT_PX at each original tick (the
    shift is recorded: on RQ3E110AJ the 10-30 mOhm ladder alone put 0 mOhm
    0.8 px below the frame; the full 0-30 ladder moves the fit 0.9 px).
    """
    plot = calibration.plot
    short = _uncovered(calibration.x_axis, plot.x0, plot.x1) + _uncovered(calibration.y_axis, plot.y0, plot.y1)
    if not short:
        return calibration, "not needed: the used ticks reach both frame edges on both axes (within half a step)"
    try:
        candidate = _calibrate(gray, hint, labels + ocr() + bands(), "text_layer+ocr+ocr_axis_bands", transform, page)
    except (RuntimeError, ValueError) as error:
        return calibration, f"tried crop and axis-band OCR to reach the frame edges; refit failed ({error}); kept the original ticks"
    moved = 0.0
    for name in ("x_axis", "y_axis"):
        old, new = getattr(calibration, name), getattr(candidate, name)
        if new.model != old.model or not {t.value for t in old.ticks} <= {t.value for t in new.ticks}:
            return calibration, (f"tried crop and axis-band OCR; the refit on {name} dropped or changed ticks "
                                 f"({sorted(t.value for t in old.ticks)} -> {sorted(t.value for t in new.ticks)}); kept the original ticks")
        for tick in old.ticks:
            moved = max(moved, abs((new.m * tick.pixel + new.b) - (old.m * tick.pixel + old.b)) / abs(old.m))
    if moved > TICK_COMPLETION_MAX_SHIFT_PX:
        return calibration, f"tried crop and axis-band OCR; the refit moved the fit {moved:.2f} px at an original tick; kept the original ticks"
    if candidate.tick_scatter_px > candidate.residual_limit_px:
        return calibration, (f"tried crop and axis-band OCR; the refit's tick scatter {candidate.tick_scatter_px:.2f} px "
                             f"exceeds {candidate.residual_limit_px:.2f} px; kept the original ticks")
    added = {name: sorted({t.value for t in getattr(candidate, name).ticks} - {t.value for t in getattr(calibration, name).ticks})
             for name in ("x_axis", "y_axis")}
    if not any(added.values()):
        return calibration, "tried crop and axis-band OCR; they supplied no further consistent tick; kept the original ticks"
    return replace(candidate, tick_source=calibration.tick_source), (
        f"added ticks x {added['x_axis']} y {added['y_axis']} from crop/axis-band OCR to reach the frame edges; "
        f"the fit moved <= {moved:.2f} px at the original ticks")


def _tick_origins(axis, plot: PlotBox, orientation: str, sources) -> list[str]:
    """Which source supplied each used tick's label: the first source holding
    a label of the same text beside that tick (review R3-8)."""
    out = []
    for tick in axis.ticks:
        origin = "unmatched"
        for name, source_labels in sources:
            for label in source_labels:
                if label.text != tick.text:
                    continue
                if orientation == "x":
                    beside = abs(label.cx - tick.pixel) <= 10 and label.cy > plot.y1 - 4
                else:
                    beside = abs(label.cy - tick.pixel) <= 10 and label.cx < plot.x0 + 4
                if beside:
                    origin = name
                    break
            if origin != "unmatched":
                break
        out.append(origin)
    return out


def _span_state(calibration: Calibration, vgs: float, y_value: float) -> dict:
    """Is a reading inside the used ticks' span on both axes? If not, is the
    extrapolation anchored by the frame edge sitting on the fitted tick
    lattice (within 1 px)? (review R3-9)"""
    notes, anchored_all = [], True
    for name, axis, value, (lo_px, hi_px) in (
        ("x", calibration.x_axis, vgs, (calibration.plot.x0, calibration.plot.x1)),
        ("y", calibration.y_axis, y_value, (calibration.plot.y0, calibration.plot.y1)),
    ):
        coordinate = math.log10(value) if axis.model == "log10" else value
        if axis.model == "log10" and value <= 0:
            return {"state": "outside_unanchored", "detail": f"{name}: non-positive value on a log axis"}
        coords = _coordinates(axis)
        if coords[0] - 1e-9 <= coordinate <= coords[-1] + 1e-9:
            continue
        step = _step(coords)
        below = coordinate < coords[0]
        edge_px = min((lo_px, hi_px), key=lambda px: (axis.m * px + axis.b) if below else -(axis.m * px + axis.b))
        edge = axis.m * edge_px + axis.b
        end = coords[0] if below else coords[-1]
        lattice = end + round((edge - end) / step) * step if step else edge
        deviation = abs((lattice - axis.b) / axis.m - edge_px)
        shown = (lambda c: 10 ** c) if axis.model == "log10" else (lambda c: c)
        if step and deviation <= 1.0:
            notes.append(f"{name} {shown(coordinate):.4g} beyond the used ticks ({shown(coords[0]):g}..{shown(coords[-1]):g}); "
                         f"anchored: the frame edge at {edge_px} px is {deviation:.2f} px from the fitted lattice value {shown(lattice):.4g}")
        else:
            anchored_all = False
            notes.append(f"{name} {shown(coordinate):.4g} beyond the used ticks ({shown(coords[0]):g}..{shown(coords[-1]):g}); "
                         f"NOT anchored (frame edge {edge_px} px is {deviation:.2f} px off the fitted lattice)")
    if not notes:
        return {"state": "inside"}
    return {"state": "outside_anchored" if anchored_all else "outside_unanchored", "detail": "; ".join(notes)}


def _flag_calibration_span(curves: list[dict], validation: dict, calibration: Calibration, scale: float) -> list[str]:
    """Mark every served reading by where it lies against the used ticks;
    an unanchored extrapolation is a reason (keeps the panel off ok)."""
    reasons = []
    for curve in curves:
        for reading in curve.get("readouts", []):
            if reading.get("rds_mohm") is None:
                continue
            span = _span_state(calibration, reading["vgs_v"], reading["rds_mohm"] / scale)
            reading["calibration_span"] = span["state"]
            if "detail" in span:
                reading["calibration_span_detail"] = span["detail"]
            if span["state"] == "outside_unanchored":
                reasons.append(f"curve_{curve['curve_index']}_readout_outside_calibrated_span "
                               f"({reading['vgs_v']:g} V: {span['detail']})")
    for anchor in validation.get("anchors", []):
        if anchor.get("chart_mohm") is None:
            continue
        span = _span_state(calibration, anchor["row"]["vgs_v"], anchor["chart_mohm"] / scale)
        anchor["calibration_span"] = span["state"]
        if "detail" in span:
            anchor["calibration_span_detail"] = span["detail"]
        if span["state"] == "outside_unanchored":
            reasons.append(f"table_anchor_{anchor['row']['vgs_v']:g}V_reading_outside_calibrated_span ({span['detail']})")
    return reasons


_LEADER_TAIL_OCR_SIZE = (200, 50)   # px: the text beside an arrow's tail


def _ocr_leader_tail(gray, tail, head, out_dir: Path, panel, stem: str) -> Label | None:
    """Read the label at an arrow's tail that the plot OCR missed (F4-3:
    WSR3090's "TJ=25C"): one line, upscaled 3x, beside the tail on the side
    away from the tip."""
    if shutil.which("tesseract") is None:
        return None
    w, h = _LEADER_TAIL_OCR_SIZE
    away = np.asarray(tail, float) - np.asarray(head, float)
    x0 = int(tail[0]) - 10 if away[0] >= 0 else int(tail[0]) - w + 10
    if abs(away[1]) < 0.5 * abs(away[0]):
        y0 = int(tail[1]) - h // 2              # a level leader: the text is centred on it
    else:
        y0 = int(tail[1]) - 5 if away[1] >= 0 else int(tail[1]) - h + 5
    x0, y0 = max(0, x0), max(0, y0)
    window = gray[y0:y0 + h, x0:x0 + w]
    if window.size == 0:
        return None
    up = cv2.resize(window, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    _thr, binary = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    target = out_dir / "work" / "leader_ocr" / panel.part / f"{stem}_{int(tail[0])}_{int(tail[1])}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(target), binary)
    words = _tesseract_words(target, clip=pymupdf.Rect(0, 0, binary.shape[1], binary.shape[0]), scale_x=1.0,
                             scale_y=1.0, psm=7, timeout=60.0, whitelist=None, min_confidence=0.0)
    # any confidence: the text is used only if it parses as a curve label, and
    # the arrow it sits at must still point unambiguously at one curve
    text = " ".join(t for *_box, t in words).strip()
    params = parse_label_params(text)
    if not params:
        return None
    return Label(text, float(x0), float(y0), float(x0 + window.shape[1]), float(y0 + window.shape[0]), params)


def _traces(page, transform, calibration: Calibration, image, gray, words, labels, ocr, out_dir, panel, stem,
            row: dict | None = None):
    traces, swatches, leaders = vector_traces(page, transform, calibration.plot)
    if traces:
        return traces, swatches, leaders, "vector", []
    plot = calibration.plot
    x_candidates, y_candidates = _full_span_grid_lines(gray, plot)
    xs = _verified_rules(gray, x_candidates, plot, [t.pixel for t in calibration.x_axis.ticks], vertical=True)
    ys = _verified_rules(gray, y_candidates, plot, [t.pixel for t in calibration.y_axis.ticks], vertical=False)
    if row is not None:
        # which projection peaks were erased as grid rules, and which were
        # refused as not-a-rule (review R3-4) -- provenance for the reviewer
        row["raster_grid_rules_px"] = {
            "x_erased": [round(float(v), 1) for v in xs],
            "x_refused_not_a_rule": [round(float(v), 1) for v in x_candidates if v not in xs],
            "y_erased": [round(float(v), 1) for v in ys],
            "y_refused_not_a_rule": [round(float(v), 1) for v in y_candidates if v not in ys],
        }
    plot_ocr = ocr_plot_labels(gray, plot, out_dir, panel, stem)
    boxes = [b for b, text in _text_boxes_px(words, transform, plot) if _is_texty(text, b, plot)]
    boxes += [
        (l.x0, l.y0, l.x1, l.y1) for l in [*ocr(), *plot_ocr]
        if isinstance(l, _BoxedLabel) and plot.x0 < l.cx < plot.x1 and plot.y0 < l.cy < plot.y1
        and _is_texty(l.text, (l.x0, l.y0, l.x1, l.y1), plot)
    ]
    return raster_traces(image, plot, xs, ys, boxes), [], [], "raster", plot_ocr


RULE_DARK_FRACTION = 0.9
RULE_LATTICE_DARK_FRACTION = 0.75


def _verified_rules(gray, lines, plot: PlotBox, tick_pixels, *, vertical: bool) -> tuple[float, ...]:
    """Keep a rule candidate only if it IS a rule: dark end to end, or on the grid lattice.

    The projection detector counts a steep curve as a vertical rule (review
    R3-4: RQ3E180AJ's 18 A branch at x=460 px, dark in 60 % of the rows,
    while the real 1.5 V rule at 466 px is dark in all). Erasing it cut the
    branch. A candidate is kept when it is dark (< 200 within +-1 px) in
    >= 90 % of the plot's rows (columns); or when it is dark in >= 75 % and
    sits within 2 px of a calibrated tick or of the regular lattice the
    fully dark rules form (a legend box can hide part of a real rule:
    RQ3E180AJ's 4.5 V rule is dark in 88 %).
    """
    def darkness(line: float) -> float:
        c = int(round(line))
        if vertical:
            band = gray[plot.y0 + 3 : plot.y1 - 2, max(0, c - 1) : c + 2]
            return float((band < 200).any(axis=1).mean()) if band.size else 0.0
        band = gray[max(0, c - 1) : c + 2, plot.x0 + 3 : plot.x1 - 2]
        return float((band < 200).any(axis=0).mean()) if band.size else 0.0

    dark = {line: darkness(line) for line in lines}
    solid = sorted(line for line in lines if dark[line] >= RULE_DARK_FRACTION)
    steps = [b - a for a, b in zip(solid, solid[1:]) if b - a > 5]
    pitch = float(np.median(steps)) if steps else None

    def on_lattice(line: float) -> bool:
        if any(abs(line - tick) <= 2.0 for tick in tick_pixels):
            return True
        if pitch is None or not solid:
            return False
        # whole pitches from the NEAREST solid rule: a far anchor would let
        # pitch jitter accumulate into a false match
        distance = abs(line - min(solid, key=lambda a: abs(line - a)))
        return abs(distance - round(distance / pitch) * pitch) <= 2.0

    return tuple(
        line for line in lines
        if dark[line] >= RULE_DARK_FRACTION or (dark[line] >= RULE_LATTICE_DARK_FRACTION and on_lattice(line))
    )


def _is_texty(text: str, box, plot: PlotBox) -> bool:
    """An OCR word that is plausibly printed text, not a curve stroke read as one.

    Tesseract reads curve strokes as short junk ("l", "\\", "NX"): erasing
    such a box erased RQ3E180AJ's knee (review finding 2). A label word here
    carries a digit or "=", or at least four letters, and is no taller than
    max(45 px, 6 % of the plot) at 300 dpi.
    """
    letters = sum(ch.isalpha() for ch in text)
    looks_like_label = any(ch.isdigit() for ch in text) or "=" in text or letters >= 4
    return looks_like_label and (box[3] - box[1]) <= max(45.0, 0.06 * (plot.y1 - plot.y0))


def _text_boxes_px(words: PageText, transform, plot: PlotBox):
    boxes = []
    for w in words.words:
        x0, y0 = transform.to_px(w.x0, w.y0)
        x1, y1 = transform.to_px(w.x1, w.y1)
        if plot.x0 <= 0.5 * (x0 + x1) <= plot.x1 and plot.y0 <= 0.5 * (y0 + y1) <= plot.y1:
            boxes.append(((x0, y0, x1, y1), w.text))
    return boxes


def _plot_labels(words: PageText, transform, plot: PlotBox, ocr_labels) -> list[Label]:
    """Label lines inside the plot (legends included) with their parameters.

    Words are grouped into lines in PAGE POINTS (the shared line grouper's
    tolerance is in points), and an OCR word is dropped where a word from a
    better source already covers it, so one label is never read twice.
    """
    x0p, y0p = transform.to_pt(plot.x0 - 4, plot.y0 - 4)
    x1p, y1p = transform.to_pt(plot.x1 + 4, plot.y1 + 4)

    def inside(w) -> bool:
        return x0p <= 0.5 * (w.x0 + w.x1) <= x1p and y0p <= 0.5 * (w.y0 + w.y1) <= y1p

    selected: list = [w for w in words.words if inside(w)]
    for l in ocr_labels or []:
        if not isinstance(l, _BoxedLabel):
            continue
        if not any(ch.isalnum() for ch in l.text):
            # an OCR "=" or "~" read off a curve or arrow stroke: grouped into
            # a label line it stretched WSR3090's "T=100" box over half the plot
            continue
        ax, ay = transform.to_pt(l.x0, l.y0)
        bx, by = transform.to_pt(l.x1, l.y1)
        word = Word(l.text, ax, ay, bx, by)
        if inside(word):
            selected.append(word)
    selected = _drop_overlapping_duplicates(selected)
    out = []
    for line in group_words_into_lines(selected):
        for segment in (piece for gap_part in _split_gaps(line) for piece in _split_parameters(gap_part)):
            text = line_text(segment)
            ax, ay, bx, by = line_bbox(segment)
            px0, py0 = transform.to_px(ax, ay)
            px1, py1 = transform.to_px(bx, by)
            out.append(Label(text, px0, py0, px1, py1, parse_label_params(text)))
    return out


def _add_condition_labels(plot_labels: list[Label], row: dict, boxes, rule_free: list[Label]) -> list[Label]:
    """Merge the F5-3 readings into the plot labels (raster panels).

    - A framed box that yielded a parsed line is read as a unit: every
      other source's word centred inside it is replaced by the box reading
      (RQ3E110AJ: "T.=25°" from the plot OCR -> "Ta=25°C" from the box).
    - A rule-free reading is added only where no parsed label already
      overlaps it: it fills what the other sources missed and never
      overrides them.
    Both are recorded on the row, for provenance.
    """
    out = list(plot_labels)
    read_boxes = [(box, labels) for box, labels in boxes if labels]
    for box, labels in read_boxes:
        x0, y0, x1, y1 = box
        out = [l for l in out if not (x0 <= 0.5 * (l.x0 + l.x1) <= x1 and y0 <= 0.5 * (l.y0 + l.y1) <= y1)]
        out.extend(labels)
    row["legend_boxes_read"] = [{"box_px": [int(v) for v in box], "lines": [l.text for l in labels]} for box, labels in boxes]
    added = []
    for label in rule_free:
        clash = any(
            other.params and min(label.x1, other.x1) > max(label.x0, other.x0)
            and min(label.y1, other.y1) > max(label.y0, other.y0)
            for other in out
        )
        if not clash:
            out.append(label)
            added.append(label.text)
    row["labels_read_with_grid_rules_erased"] = added
    return out


def _drop_overlapping_duplicates(words: list) -> list:
    """Keep the first of two words whose boxes overlap by more than 40 %."""
    kept: list = []
    for word in words:
        area = max(1e-6, (word.x1 - word.x0) * (word.y1 - word.y0))
        clash = False
        for other in kept:
            ix = max(0.0, min(word.x1, other.x1) - max(word.x0, other.x0))
            iy = max(0.0, min(word.y1, other.y1) - max(word.y0, other.y0))
            other_area = max(1e-6, (other.x1 - other.x0) * (other.y1 - other.y0))
            if ix * iy > 0.4 * min(area, other_area):
                clash = True
                break
        if not clash:
            kept.append(word)
    return kept


_PAGE_TEMPERATURE_NOTE_RE = re.compile(
    r"T\s*(?P<sub>[JCA])?\s*=\s*(?P<value>[+-]?\d{1,3})\s*(?:°|º|o)?\s*C\s*,?\s*unless\s+otherwise\s+(?:noted|specified|stated)",
    re.IGNORECASE,
)


def _apply_page_temperature_note(traces: list[Trace], page) -> list[str]:
    """Use a page-level "TC = 25°C unless otherwise noted" when NO curve names a temperature.

    Only when exactly one such note is on the page and no label on the panel
    printed a temperature at all; a panel that prints its own temperatures
    never falls back to the page note.
    """
    if any("temperature_c" in trace.params for trace in traces):
        return []
    notes = {
        (float(m.group("value")), temperature_kind(m.group("sub"), True))
        for m in _PAGE_TEMPERATURE_NOTE_RE.finditer(" ".join(page.get_text("text").split()))
    }
    if len(notes) != 1:
        return []
    value, kind = notes.pop()
    for trace in traces:
        trace.params["temperature_c"] = value
        trace.params["temperature_kind"] = kind
        trace.binding["temperature_c"] = "page_note_unless_otherwise_noted"
    return [f"temperature_c={value:g}_{kind}_from_page_note"]


def _split_parameters(words: list) -> list[list]:
    """Split one text line where a second "T..=" / "I..=" assignment begins.

    Two curve labels printed on one baseline ("TJ=100C   TJ=125C") must stay
    two labels; parsed as one they name two temperatures and bind to nothing.
    """
    parts: list[list] = [[]]
    for word in words:
        if parts[-1] and PARAM_START_RE.match(word.text) and parse_label_params(line_text(parts[-1])):
            parts.append([])
        parts[-1].append(word)
    return [part for part in parts if part]


def _split_gaps(line):
    parts, current = [], [line[0]]
    for word in line[1:]:
        if word.x0 - current[-1].x1 > 3.0 * max(1.0, current[-1].y1 - current[-1].y0):
            parts.append(current)
            current = [word]
        else:
            current.append(word)
    parts.append(current)
    return parts


# --------------------------------------------------------------------------- curves


def _curves(traces: list[Trace], calibration: Calibration, scale: float, gray=None):
    plot = calibration.plot
    reasons: list[str] = []
    curves: list[dict] = []
    y_ticks = [t.value * scale for t in calibration.y_axis.ticks]
    log_y = calibration.y_axis.model == "log10"
    y_span = (math.log10(max(y_ticks)) - math.log10(min(y_ticks))) if log_y else (max(y_ticks) - min(y_ticks))
    x_values = [t.value for t in calibration.x_axis.ticks]
    x_span = max(x_values) - min(x_values)
    refusal = None
    # numbered top to bottom by the median height of the ink the trackers
    # found; the points added at the right frame (round 5) and in unsampled
    # stretches (F6-1) are left out
    # of the key so they never renumber a near-tied coincident pair
    def height(t: Trace) -> float:
        added = {tuple(p) for p in t.frame_traced.get("measured_px", []) + t.frame_traced.get("across_frame_stroke_px", [])}
        added |= {tuple(p) for n in t.gap_traced for p in n.get("measured_px", []) + n.get("bridged_on_rule_px", [])}
        return float(np.median([p[1] for p in t.points_px if tuple(p) not in added] or [p[1] for p in t.points_px]))

    ordered = sorted(traces, key=height)
    for index, trace in enumerate(ordered):
        # Only the top and bottom rails clip an RDS(VGS) curve (it leaves the
        # RDS range there). A curve ending on the left/right frame has simply
        # reached the end of the VGS axis; those points are data.
        at_frame = [
            (x, y) for x, y in trace.points_px
            if y <= plot.y0 + FRAME_CONTACT_PX or y >= plot.y1 - FRAME_CONTACT_PX
            or x < plot.x0 - FRAME_CONTACT_PX or x > plot.x1 + FRAME_CONTACT_PX
        ]
        interior = [p for p in trace.points_px if p not in set(at_frame)]
        runs_along = _longest_frame_run(at_frame, plot)
        points = [
            (calibration.x_axis.value(x), calibration.y_axis.value(y) * scale, x, y)
            for x, y in interior
        ]
        # by VGS; at equal VGS (a near-vertical stroke traced row by row,
        # F4-1) the higher RDS first, as the curve runs
        points.sort(key=lambda p: (p[0], -p[1]))
        curve = {
            "curve_index": index,
            "trace_method": trace.method,
            "temperature_c": trace.params.get("temperature_c"),
            "temperature_kind": trace.params.get("temperature_kind"),
            "id_a": trace.params.get("id_a"),
            "parameter_binding": dict(trace.binding),
            "label": _curve_label(trace),
            "stroke_style": list(trace.style) if trace.style else None,
            "n_points": len(points),
            "frame_contact_points_dropped": len(at_frame),
            "columns_interpolated_across_erased_grid_rules": trace.bridged_columns,
            "points": [[round(v, 5), round(r, 5)] for v, r, _x, _y in points],
            "points_px": [[round(x, 2), round(y, 2)] for _v, _r, x, y in points],
        }
        if not points:
            reasons.append(f"curve_{index}_has_no_interior_points")
            curves.append(curve)
            continue
        curve["vgs_range_v"] = [round(points[0][0], 4), round(points[-1][0], 4)]
        open_left, open_right = _open_ends(trace, plot)
        curve["trace_complete"] = {"left_end_at_frame": not open_left, "right_end_at_frame": not open_right}
        if open_left or open_right:
            ends = [f"starts at {points[0][0]:.2f} V inside the plot"] * open_left + [
                f"stops at {points[-1][0]:.2f} V inside the plot"] * open_right
            reasons.append(f"curve_{index}_partial_raster_trace ({'; '.join(ends)}; the source curve may continue)")
        if open_left and trace.method == "raster" and _ink_reaches_frame(gray, trace.points_px[0], plot, "left"):
            # F4-1: say it plainly when the printed stroke runs on to the frame
            head_y = trace.points_px[0][1]
            top = calibration.y_axis.value(plot.y0) * scale
            here = calibration.y_axis.value(head_y) * scale
            reasons.append(f"curve_{index}_head_not_traced_to_frame (printed ink continues to the frame; "
                           f"not traced from {here:.3g} to {top:.3g} mOhm)")
        curve["row_traced_points_px"] = [[round(x, 2), round(y, 2)] for x, y in trace.row_traced_points]
        # round 5: right end followed to the frame (measured columns, and
        # columns inside the frame stroke bridged to the ink beyond it)
        frame = trace.frame_traced
        curve["frame_traced_points_px"] = [[round(x, 2), round(y, 2)] for x, y in
                                           frame.get("measured_px", []) + frame.get("across_frame_stroke_px", [])]
        curve["frame_tracing"] = {
            "measured_px": [[round(x, 2), round(y, 2)] for x, y in frame.get("measured_px", [])],
            "across_frame_stroke_px": [[round(x, 2), round(y, 2)] for x, y in frame.get("across_frame_stroke_px", [])],
            "far_side_ink_px": [round(v, 2) for v in frame["far_side_ink_px"]] if frame.get("far_side_ink_px") else None,
            "frame_stroke_px": frame.get("frame_stroke_px"),
        } if frame else None
        curve["shared_tail"] = [dict(note) for note in trace.tail_from]
        if runs_along > FRAME_RUN_FRACTION * (plot.x1 - plot.x0):
            reasons.append(f"curve_{index}_runs_along_the_frame_{runs_along:.0f}px_(clipped)")
        if trace.method == "vector" and backtrack_px(trace.points_px) > MAX_BACKTRACK_PX:
            reasons.append(f"curve_{index}_not_single_valued_in_vgs")
        if trace.merged_columns > MAX_MERGED_FRACTION * (plot.x1 - plot.x0):
            reasons.append(f"curve_{index}_shares_{trace.merged_columns}_columns_with_another_curve")
        rise = _max_rise([p[1] for p in points], log_y)
        curve["max_rise_fraction_of_axis"] = round(rise / y_span, 4) if y_span else None
        if y_span and rise / y_span > MAX_RISE_FRACTION:
            refusal = refusal or (
                f"curve_{index}_rdson_rises_with_vgs_by_{rise / y_span:.1%}_of_axis "
                "(RDS(on) does not increase with VGS: the trace is not a curve of this chart)"
            )
        # Three kinds of unread stretch, all listed in `gaps` and never read
        # across; they differ in cause (review R2-7, R2-8):
        # - annotation_contact: points removed where an arrow/label touches;
        # - untraced_section: the ink is continuous between the ends, but the
        #   column tracker did not sample it (steep or obstructed section);
        # - gap: no curve ink between the ends in the tracked band.
        removed = trace.contact_removed_x
        kinds: dict[str, list] = {"gap": [], "untraced_section": [], "annotation_contact": []}
        untraced_why: list[str] = []
        for a, b in zip(points, points[1:]):
            contact = any(a[2] < x < b[2] for x in removed)
            if b[2] - a[2] <= GAP_PX and not contact:
                continue
            # A removed contact point ALWAYS opens an unread interval between
            # its surviving neighbours, however close they are (review R3-2:
            # WSR3090 c1 at 8.4419 V had neighbours 2 px apart and was read
            # through).
            span = [round(a[0], 4), round(b[0], 4)]
            if contact:
                kinds["annotation_contact"].append(span)
            elif trace.method == "raster" and _ink_connects(gray, a[2:], b[2:]):
                kinds["untraced_section"].append(span)
                untraced_why.append(_untraced_reason(trace, a[2:], b[2:]))
            else:
                kinds["gap"].append(span)
        curve["gaps"] = sorted(span for spans in kinds.values() for span in spans)
        curve["gap_kinds"] = kinds
        curve["gap_tracing"] = [_gap_note_json(n, calibration, scale) for n in trace.gap_traced]
        curve["gap_traced_points_px"] = [[round(x, 2), round(y, 2)] for n in trace.gap_traced
                                         for x, y in n.get("measured_px", []) + n.get("bridged_on_rule_px", [])]
        curve["untraced_section_reasons"] = untraced_why
        wording = {
            "gap": "no curve ink traced there",
            "annotation_contact": "points pulled off the curve by a touching arrow/label were removed",
        }
        for kind in ("gap", "annotation_contact"):
            spans = kinds[kind]
            if spans:
                listed = ", ".join(f"{g0:.2f}..{g1:.2f} V" for g0, g1 in spans[:6])
                more = f" +{len(spans) - 6} more" if len(spans) > 6 else ""
                reasons.append(f"curve_{index}_{kind}s ({listed}{more}; {wording[kind]}; no readout inside)")
        # F6-1: an unsampled stretch left after tracing says concretely why
        for (g0, g1), why in zip(kinds["untraced_section"], untraced_why):
            reasons.append(f"curve_{index}_untraced_section ({g0:.2f}..{g1:.2f} V: {why}; no readout inside)")
        curve["points_removed_as_annotation_contact"] = len(removed)
        curve["annotation_contact_removed_vgs_v"] = [round(calibration.x_axis.value(x), 5) for x in removed]
        stubs = [
            [round(calibration.x_axis.value(x), 5), round(calibration.y_axis.value(y) * scale, 5), round(x, 2), round(y, 2)]
            for x, y in trace.dropped_stub_points
        ]
        curve["dropped_end_stub_points"] = stubs
        curve["end_stub_decisions"] = [dict(d) for d in trace.stub_decisions]
        if stubs:
            # F6-1: each dropped stub says concretely what it is on the page
            whys = {(round(d["stub_px"][0], 2), round(d["stub_px"][1], 2)): d["why"] for d in trace.stub_decisions}
            where = "; ".join(f"{v:.3g} V/{r:.3g} mOhm at ({x:.0f}, {y:.0f}) px: "
                              + whys.get((round(x, 2), round(y, 2)), "not examined") for v, r, x, y in stubs)
            reasons.append(f"curve_{index}_end_stub_points_dropped ({where}; not served)")
        ink_left = trace.method != "raster" or not open_left or _ink_reaches_frame(gray, trace.points_px[0], plot, "left")
        ink_right = trace.method != "raster" or not open_right or _ink_reaches_frame(gray, trace.points_px[-1], plot, "right")
        curve["trace_complete"]["left_ink_reaches_frame"] = ink_left
        curve["trace_complete"]["right_ink_reaches_frame"] = ink_right
        # Usability is judged from the INK, not from where the column tracker's
        # first sample happened to land (review R3-6: RQ6E080AJ's twin steep
        # branches both reach the top frame; one sample started 3.5 px below
        # it, the other 9 px, and they got opposite flags).
        fragment = (not ink_left and not ink_right
                    and (points[-1][0] - points[0][0]) < USABLE_MIN_SPAN_FRACTION * x_span)
        curve["usable"] = not fragment
        if fragment:
            curve["not_usable_reason"] = (
                "raster fragment with both ends inside the plot, spanning under 30 % of the "
                "VGS axis: which curve it belongs to, and whether all of it is curve ink, is unverified"
            )
            reasons.append(f"curve_{index}_not_usable (fragment)")
            curve["readouts"] = [
                {"vgs_v": v, "note": READOUT_NOTE, "rds_mohm": None, "status": "curve_not_usable",
                 "detail": curve["not_usable_reason"]}
                for v in READOUT_VGS_V
            ]
        else:
            curve["readouts"] = readouts(points, log_y, curve["gaps"], abs(calibration.x_axis.m), open_left, open_right)
        curves.append(curve)
    reasons.extend(_mark_coincident(curves, calibration))
    return curves, reasons, refusal


COINCIDENT_PX = 0.75        # two curves closer than this in every shared column ...
COINCIDENT_MIN_PX = 20      # ... over at least this many px of VGS are drawn on top of each other


def _mark_coincident(curves: list[dict], calibration: Calibration) -> list[str]:
    """Stretches where two served curves lie on top of each other (review R3-13).

    FDP8870: from ~4.5 V the 1 A and 35 A outlines (PDF drawings 716 and 719)
    have their vertices at the same heights (e.g. 621.07/623.11 pt at 6 V),
    0.36 pt apart in x: the source draws the tails coincident, so both
    curves serve the same values there. That is stated, per curve, instead
    of serving two curves that silently share data.
    """
    columns = []
    for curve in curves:
        by_x: dict[int, list[float]] = {}
        for x, y in curve.get("points_px", []):
            by_x.setdefault(int(round(x)), []).append(y)
        columns.append({x: float(np.mean(ys)) for x, ys in by_x.items()})
    reasons = []
    for curve in curves:
        curve.setdefault("coincident_with", [])
    for i in range(len(curves)):
        for j in range(i + 1, len(curves)):
            shared = sorted(set(columns[i]) & set(columns[j]))
            # separation, median-filtered over 9 shared columns: two outlines
            # rasterized from the same heights still differ by 1 px here and
            # there (FDP8870's pair is offset 0.36 pt in x)
            raw = np.asarray([abs(columns[i][x] - columns[j][x]) for x in shared])
            smooth = [float(np.median(raw[max(0, k - 4):k + 5])) for k in range(len(raw))]
            runs, current = [], []
            for x, separation in zip(shared, smooth):
                close = separation <= COINCIDENT_PX
                if close and (not current or x - current[-1] <= 3):
                    current.append(x)
                else:
                    if len(current) > 1:
                        runs.append(current)
                    current = [x] if close else []
            if len(current) > 1:
                runs.append(current)
            for run in runs:
                if run[-1] - run[0] < COINCIDENT_MIN_PX:
                    continue
                v0, v1 = calibration.x_axis.value(run[0]), calibration.x_axis.value(run[-1])
                sep = float(np.median([abs(columns[i][x] - columns[j][x]) for x in run]))
                worst = max(abs(columns[i][x] - columns[j][x]) for x in run)
                for a, b in ((i, j), (j, i)):
                    curves[a]["coincident_with"].append({
                        "curve_index": curves[b]["curve_index"], "from_vgs_v": round(v0, 4), "to_vgs_v": round(v1, 4),
                        "median_separation_px": round(sep, 2), "max_separation_px": round(worst, 2)})
                reasons.append(
                    f"curve_{curves[i]['curve_index']}_coincident_with_curve_{curves[j]['curve_index']} "
                    f"({v0:.2f}..{v1:.2f} V, median separation {sep:.2f} px, max {worst:.2f} px: drawn on top of "
                    "each other there, so both serve the same values; not separable in that range)")
    return reasons


def _untraced_reason(trace: Trace, a, b) -> str:
    """The concrete reason F6-1's tracer left the stretch a..b (crop px)."""
    for note in trace.gap_traced:
        if "refused" in note and abs(note["from_px"][0] - a[0]) <= 0.5 and abs(note["to_px"][0] - b[0]) <= 0.5:
            return note["refused"]
    return "not followed: this trace did not pass through the F6-1 stretch tracer (raster_traces)"


def _gap_note_json(note: dict, calibration: Calibration, scale: float) -> dict:
    out = {"from_px": [round(v, 2) for v in note["from_px"]], "to_px": [round(v, 2) for v in note["to_px"]],
           "mode": note["mode"],
           "vgs_v": [round(calibration.x_axis.value(note["from_px"][0]), 4), round(calibration.x_axis.value(note["to_px"][0]), 4)]}
    if "refused" in note:
        out["refused"] = note["refused"]
    else:
        out["measured_px"] = [[round(x, 2), round(y, 2)] for x, y in note["measured_px"]]
        out["bridged_on_rule_px"] = [[round(x, 2), round(y, 2)] for x, y in note["bridged_on_rule_px"]]
    return out


def _ink_reaches_frame(gray, end, plot: PlotBox, side: str) -> bool:
    """Does the curve's ink run on from this trace end to the top or bottom rail?

    Followed row by row (towards the top rail from a left end, towards the
    bottom rail or right frame from a right end) through dark pixels within
    3 px of the previous row's ink.
    """
    if gray is None:
        return False
    x, y = int(round(end[0])), int(round(end[1]))
    step = -1 if side == "left" else 1
    stop = plot.y0 + 3 if side == "left" else plot.y1 - 3
    cx = x
    for row in range(y, stop, step):
        lo, hi = max(plot.x0 + 1, cx - 3), min(plot.x1 - 1, cx + 4)
        dark = np.flatnonzero(gray[row, lo:hi] < 150)
        if dark.size == 0:
            return False
        cx = lo + int(round(float(np.median(dark))))
    return True


def _ink_connects(gray, a, b) -> bool:
    """True when every column between two trace points holds dark ink between their heights."""
    if gray is None:
        return False
    (x0, y0), (x1, y1) = a, b
    top, bottom = int(min(y0, y1)) - 3, int(max(y0, y1)) + 4
    for x in range(int(round(x0)) + 1, int(round(x1))):
        if not (gray[max(0, top):bottom, x] < 150).any():
            return False
    return True


def _open_ends(trace: Trace, plot: PlotBox) -> tuple[bool, bool]:
    """Raster ends that stop inside the plot: the source may continue past them.

    A vector trace is the whole source path, so its ends are the source's ends.
    A raster trace that starts below the top rail and right of the left axis,
    or stops short of the right frame, may simply have lost the rest.
    """
    if trace.method != "raster":
        return False, False
    (x_first, y_first), (x_last, y_last) = trace.points_px[0], trace.points_px[-1]
    margin = 3 * FRAME_CONTACT_PX
    open_left = y_first > plot.y0 + margin and x_first > plot.x0 + margin
    open_right = x_last < plot.x1 - margin and y_last < plot.y1 - margin
    return open_left, open_right


def _longest_frame_run(points, plot: PlotBox) -> float:
    """Longest horizontal stretch of a trace hugging the top or bottom frame rail."""
    best = 0.0
    for rail in (plot.y0, plot.y1):
        xs = sorted(x for x, y in points if abs(y - rail) <= FRAME_CONTACT_PX)
        start = prev = None
        for x in xs:
            if prev is None or x - prev > 3:
                start = x
            prev = x
            best = max(best, prev - start)
    return best


def _max_rise(rds: list[float], log_y: bool) -> float:
    values = [math.log10(r) for r in rds if r > 0] if log_y else rds
    worst, running = 0.0, math.inf
    for value in values:
        running = min(running, value)
        worst = max(worst, value - running)
    return worst


def _curve_label(trace: Trace) -> str:
    parts = []
    if "temperature_c" in trace.params:
        value = trace.params["temperature_c"]
        kind = trace.params.get("temperature_kind") or "T"
        kind = "T" if kind in {"unspecified", "conflicting", "T (subscript unread)"} else kind
        parts.append(f"{kind}={value:g}C" if value is not None else "T=unknown")
    if "id_a" in trace.params:
        current = trace.params["id_a"]
        parts.append(f"ID={current:g}A" if current is not None else "ID=unknown")
    return ", ".join(parts) or "unlabelled"


def _binding_reasons(curves: list[dict], labels: list[Label]) -> list[str]:
    """Every curve without a temperature (or, where IDs label curves, an ID) is named.

    A temperature is a required parameter of this chart class: a curve with no
    temperature is reported whether its label was unbound, contradicted, or
    never read at all (review R2-1: RQ3E180AJ's unread "Ta=25C" box gave no
    reason). ID is required only where the chart labels curves by ID.
    """
    reasons = []
    keys = {k for c in curves for k in ("temperature_c", "id_a") if k in c["parameter_binding"]}
    if len(curves) > 1 and not keys:
        reasons.append("curve_parameters_unlabelled: several curves, no Tj/ID label binds to any")
    for curve in curves:
        for key in sorted(keys | {"temperature_c"}):
            if curve.get(key) is None:
                why = curve["parameter_binding"].get(key, "no temperature label read on the panel or page")
                reasons.append(f"curve_{curve['curve_index']}_{key}_unknown ({why})")
    return reasons


# --------------------------------------------------------------------------- CLI


def _pdfs_from_index(charts_json: Path) -> list[Path]:
    charts = json.loads(charts_json.read_text())
    seen: list[Path] = []
    for chart in charts:
        path = Path(chart["pdf"])
        if path not in seen:
            seen.append(path)
    return seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Digitize MOSFET RDS(on)-versus-VGS charts")
    parser.add_argument("charts_json", nargs="?", type=Path, help="charts.json from `dsdig find` (its PDFs are scanned)")
    parser.add_argument("--pdf", nargs="+", type=Path, default=[], help="datasheet PDFs to scan directly")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    pdfs = list(args.pdf)
    if args.charts_json is not None:
        pdfs.extend(p for p in _pdfs_from_index(args.charts_json) if p not in pdfs)
    if not pdfs:
        parser.error("give a charts.json or --pdf")
    args.out.mkdir(parents=True, exist_ok=True)
    panels, refusals, errors = [], [], []
    for pdf in pdfs:
        print(f"scan {pdf}")
        try:
            results, refused = digitize_pdf(pdf, args.out)
        except Exception as error:  # noqa: BLE001 - serialized, not swallowed
            errors.append({"pdf": str(pdf), "error": f"{type(error).__name__}: {error}"})
            print(f"  ERROR {error}")
            continue
        panels.extend(results)
        refusals.extend(refused)
        for result in results:
            print(f"  p{result['page']} fig {result['diagram']}: {result['status']} "
                  f"validation={result.get('validation', {}).get('verdict')} overlay: {args.out / result['overlay']}")
        for refusal in refused:
            print(f"  located-but-refused p{refusal['page']} fig {refusal['diagram']}: {refusal['reason']}")
    manifest = {"kind": KIND, "panels": panels, "locator_refusals": refusals, "errors": errors}
    path = args.out / "rdson_gate_voltage.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {path}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
