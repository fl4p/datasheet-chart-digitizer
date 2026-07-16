"""Digitize normalized MOSFET RDS(on)-versus-temperature charts.

This first direct plugin slice intentionally stops at reviewed chart-native
curves.  Absolute RDS(on) serving, interpolation policy, CLI wiring, and
generic finder integration belong to later consumer/integration changes.
"""

from __future__ import annotations

import json
import math
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

from .capacitance_types import PlotBox
from .capacitance_vector import (
    _chain_vector_components,
    _resample_vector_trace_pixels,
    _vector_curve_edges,
)
from .crop_transform import CropTransform
from .diode_forward_voltage import (
    _full_span_grid_lines,
    _page_labels,
    _select_axis,
    _snap_axis_to_grid,
)
from .find_charts import (
    ChartPanel,
    DiagramTitle,
    PageText,
    box_px_to_pt,
    crop_panel,
    detect_rule_boxes,
    group_words_into_lines,
    infer_grid_regions_from_h_rules,
    line_bbox,
    line_text,
    render_page,
    run_text_bbox,
    words_in_bbox,
)
from .numeric_axis import NumericAxis, axis_to_json, tick_aligned_plot
from .overlay import draw_axis_ticks, draw_plot_frame

REFERENCE_TEMPERATURE_C = 25.0
MAX_AXIS_RESIDUAL_PX = 1.5
MIN_TRACE_TEMPERATURE_SPAN_FRACTION = 0.75
MIN_TRACE_NORMALIZED_SPAN_FRACTION = 0.15
MAX_REFERENCE_UNITY_ERROR = 0.06
MAX_MONOTONIC_DROP = 0.03
MIN_VGS_ORDER_SEPARATION = 0.005
LEGEND_ROW_TOLERANCE_PT = 4.5
PANEL_TOP_EXPANSION_FRACTION = 0.55

DIAG_AXIS_RESIDUAL = "axis_residual_exceeds_threshold"
DIAG_AXIS_IDENTITY = "temperature_axis_identity_unverified"
DIAG_TEMPERATURE_SPAN = "temperature_span_below_threshold"
DIAG_NORMALIZED_SPAN = "normalized_rds_span_below_threshold"
DIAG_REFERENCE_BRACKET = "reference_temperature_not_bracketed"
DIAG_REFERENCE_UNITY = "normalized_rds_at_reference_outside_tolerance"
DIAG_MONOTONIC = "normalized_rds_not_nondecreasing_with_temperature"
DIAG_VGS_ORDER = "legend_vgs_identity_inconsistent_with_low_temperature_rds"
DIAG_CURVE_BINDING = "legend_curve_binding_ambiguous"

_RDS_TITLE_RE = re.compile(
    r"normalized\s+on[- ]state\s+resistance\s+vs\.?\s+temperature", re.I
)
_VGS_RE = re.compile(r"V\s*GS\s*=\s*(\d+(?:\.\d+)?)\s*V", re.I)
_CASE_TEMPERATURE_AXIS_RE = re.compile(
    r"case\s+temperature\s+\((?:°|q)\s*c\)", re.I
)


@dataclass(frozen=True)
class PanelCalibration:
    plot: PlotBox
    x_axis: NumericAxis
    y_axis: NumericAxis
    hint: PlotBox


@dataclass(frozen=True)
class LegendEntry:
    gate_voltage_v: float
    style_key: tuple[float, float, float]
    row_y_pt: float


@dataclass(frozen=True)
class VectorTrace:
    style_key: tuple[float, float, float]
    points_px: tuple[tuple[int, int], ...]


class CurveBindingError(RuntimeError):
    """A calibrated panel whose curve identity cannot be assigned honestly."""


def digitize_pdf(pdf: Path, out_dir: Path, dpi: int = 180) -> list[dict[str, object]]:
    """Digitize every normalized RDS(on)-temperature panel in ``pdf``."""
    results: list[dict[str, object]] = []
    pages = run_text_bbox(pdf)
    with tempfile.TemporaryDirectory(prefix="rdson-temperature-pages-") as tmp:
        tmpdir = Path(tmp)
        for page in pages:
            titles = _rdson_temperature_titles(page)
            if not titles:
                continue
            page_png = render_page(pdf, page.page_num, dpi, tmpdir)
            for title in titles:
                panel, crop_path, region = _build_panel(
                    pdf, out_dir, page, page_png, title
                )
                calibration = calibrate_panel(panel, crop_path, region)
                binding_error = None
                try:
                    traces = _extract_vector_traces(
                        panel, crop_path, calibration.plot
                    )
                    legend = _legend_entries(panel)
                    curves = _bind_and_calibrate_curves(
                        traces, legend, calibration
                    )
                    reasons = _validation_reasons(panel, calibration, curves)
                except CurveBindingError as error:
                    curves = []
                    reasons = [DIAG_CURVE_BINDING]
                    binding_error = str(error)
                status = "refused" if reasons else "ok"
                overlay = _draw_overlay(crop_path, panel, calibration, curves, status)
                overlay_path = (
                    out_dir
                    / "rdson_temperature_overlays"
                    / panel.part
                    / f"p{panel.page:02d}_d{panel.diagram}.webp"
                )
                overlay_path.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(overlay_path), overlay):
                    raise RuntimeError(f"could not write overlay: {overlay_path}")
                results.append(
                    {
                        "status": status,
                        "diagnostics": reasons
                        or [
                            "vgs_identity_bound_by_local_legend_stroke_geometry",
                            "normalized_rds_validated_at_25C",
                            "no_absolute_rds_or_temperature_interpolation",
                        ],
                        "point_columns": ["temperature_c", "normalized_rds_on"],
                        "reference_temperature_c": REFERENCE_TEMPERATURE_C,
                        "panel": asdict(panel),
                        "plot_box_px": asdict(calibration.plot),
                        "x_axis": axis_to_json(calibration.x_axis),
                        "y_axis": axis_to_json(calibration.y_axis),
                        "curves": curves,
                        "binding_error": binding_error,
                        "thresholds": _thresholds(),
                        "overlay": str(overlay_path.relative_to(out_dir)),
                    }
                )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rdson_temperature.json").write_text(
        json.dumps(results, indent=2) + "\n"
    )
    return results


def _thresholds() -> dict[str, float]:
    return {
        "maximum_axis_residual_px": MAX_AXIS_RESIDUAL_PX,
        "minimum_temperature_span_fraction": MIN_TRACE_TEMPERATURE_SPAN_FRACTION,
        "minimum_normalized_rds_span_fraction": MIN_TRACE_NORMALIZED_SPAN_FRACTION,
        "maximum_reference_unity_error": MAX_REFERENCE_UNITY_ERROR,
        "maximum_monotonic_drop": MAX_MONOTONIC_DROP,
        "minimum_vgs_order_separation": MIN_VGS_ORDER_SEPARATION,
    }


def _rdson_temperature_titles(page: PageText) -> list[DiagramTitle]:
    """Split merged side-by-side Figure captions and keep RDS(T) only."""
    titles: list[DiagramTitle] = []
    for line in group_words_into_lines(page.words):
        starts = [
            index
            for index, word in enumerate(line)
            if word.text.lower().rstrip(".:") == "figure"
            and index + 1 < len(line)
        ]
        if not starts:
            continue
        starts.append(len(line))
        for start, end in zip(starts, starts[1:]):
            segment = line[start:end]
            text = line_text(segment)
            match = re.match(r"(?i)^Figure\s+(\d+)[\.:]?\s+(.+)$", text)
            if match is None or _RDS_TITLE_RE.search(match.group(2)) is None:
                continue
            titles.append(
                DiagramTitle(
                    number=int(match.group(1)),
                    title=match.group(2).strip(),
                    bbox_pt=line_bbox(segment),
                    line_text=text,
                )
            )
    return sorted(titles, key=lambda title: (title.bbox_pt[1], title.bbox_pt[0]))


def _build_panel(
    pdf: Path,
    out_dir: Path,
    page: PageText,
    page_png: Path,
    title: DiagramTitle,
) -> tuple[ChartPanel, Path, tuple[float, float, float, float]]:
    """Bind a numbered RDS(T) caption to the local grid strictly above it."""
    with Image.open(page_png) as image:
        width_px, height_px = image.size
    _, horizontal = detect_rule_boxes(page_png)
    horizontal_pt = [
        box_px_to_pt(box, width_px, height_px, page) for box in horizontal
    ]
    regions = infer_grid_regions_from_h_rules(page, horizontal_pt)
    tcx = 0.5 * (title.bbox_pt[0] + title.bbox_pt[2])
    candidates = [
        region
        for region in regions
        if region[3] <= title.bbox_pt[1]
        and title.bbox_pt[1] - region[3] <= 100.0
        and abs(0.5 * (region[0] + region[2]) - tcx) <= 100.0
    ]
    if not candidates:
        raise RuntimeError("panel: no local grid above RDS-temperature caption")
    region = min(
        candidates,
        key=lambda item: (title.bbox_pt[1] - item[3])
        + 0.2 * abs(0.5 * (item[0] + item[2]) - tcx),
    )
    height = region[3] - region[1]
    bbox = (
        max(0.0, region[0] - 8.0),
        max(0.0, region[1] - PANEL_TOP_EXPANSION_FRACTION * height),
        min(page.width_pt, region[2] + 8.0),
        min(title.bbox_pt[1] - 2.0, region[3] + 8.0),
    )
    crop_path = (
        out_dir
        / "rdson_temperature_crops"
        / pdf.stem
        / f"p{page.page_num:02d}_d{title.number}.png"
    )
    effective = crop_panel(page_png, page, bbox, crop_path)
    identity_bbox = (bbox[0], bbox[1], bbox[2], title.bbox_pt[1])
    local_text = " ".join(
        word.text for word in words_in_bbox(page.words, identity_bbox)
    )
    text = f"{title.title} {local_text}"
    panel = ChartPanel(
        pdf=str(pdf),
        part=pdf.stem,
        page=page.page_num,
        diagram=title.number,
        title=title.title,
        kind="rds_on",
        bbox_pt=bbox,
        crop_box_pt=effective,
        crop_png=str(crop_path.relative_to(out_dir)),
        text=text,
        formula="",
        mentions=[],
        text_source=page.text_source,
    )
    return panel, crop_path, region


def calibrate_panel(
    panel: ChartPanel,
    crop_path: Path,
    grid_region_pt: tuple[float, float, float, float],
) -> PanelCalibration:
    """Calibrate generic numeric axes using the local grid only as a locator."""
    gray = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    transform = CropTransform.for_chart(asdict(panel), gray.shape)
    x0, _ = transform.to_px(grid_region_pt[0], panel.bbox_pt[1])
    x1, y1 = transform.to_px(grid_region_pt[2], grid_region_pt[3])
    hint = PlotBox(round(x0), 0, round(x1), round(y1))
    with pymupdf.open(panel.pdf) as document:
        labels = _page_labels(document[panel.page - 1], transform)
    raw_x = _select_axis(labels, hint, "x")
    raw_y = _select_axis(labels, hint, "y")
    major_x, major_y = _full_span_grid_lines(gray, hint)
    x_axis = _snap_axis_to_grid(raw_x, major_x, "X axis", authoritative=True)
    y_axis = _snap_axis_to_grid(raw_y, major_y, "Y axis", authoritative=True)
    plot = tick_aligned_plot(x_axis, y_axis, hint)
    return PanelCalibration(plot, x_axis, y_axis, hint)


def _style_key(color: object) -> tuple[float, float, float] | None:
    if not isinstance(color, tuple) or len(color) < 3:
        return None
    rgb = tuple(round(float(value), 4) for value in color[:3])
    if sum(rgb) < 0.15 or max(rgb) - min(rgb) > 0.45:
        return rgb
    return None


def _extract_vector_traces(
    panel: ChartPanel,
    crop_path: Path,
    plot: PlotBox,
) -> list[VectorTrace]:
    gray = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    transform = CropTransform.for_chart(asdict(panel), gray.shape)
    top_left = transform.to_pt(plot.x0, plot.y0)
    bottom_right = transform.to_pt(plot.x1, plot.y1)
    rect = pymupdf.Rect(*top_left, *bottom_right)
    best: dict[tuple[float, float, float], tuple[float, VectorTrace]] = {}
    with pymupdf.open(panel.pdf) as document:
        for drawing in document[panel.page - 1].get_drawings():
            style = _style_key(drawing.get("color"))
            if style is None:
                continue
            normalized = dict(drawing)
            normalized["color"] = (0.0, 0.0, 0.0)
            for component in _chain_vector_components(
                _vector_curve_edges([normalized], rect)
            ):
                xs, ys = zip(*component)
                x_fraction = (max(xs) - min(xs)) / rect.width
                y_fraction = (max(ys) - min(ys)) / rect.height
                if (
                    x_fraction < MIN_TRACE_TEMPERATURE_SPAN_FRACTION
                    or y_fraction < MIN_TRACE_NORMALIZED_SPAN_FRACTION
                ):
                    continue
                raw = [
                    tuple(round(value) for value in transform.to_px(x, y))
                    for x, y in component
                ]
                points = tuple(_resample_vector_trace_pixels(raw, plot))
                if not points:
                    continue
                score = x_fraction + y_fraction
                trace = VectorTrace(style, points)
                if style not in best or score > best[style][0]:
                    best[style] = (score, trace)
    traces = [item[1] for item in best.values()]
    if len(traces) < 1:
        raise CurveBindingError("trace: no full-span vector curves")
    return traces


def _legend_entries(panel: ChartPanel) -> list[LegendEntry]:
    """Bind each local VGS legend row to its adjacent stroke style."""
    page_text = run_text_bbox(Path(panel.pdf))[panel.page - 1]
    lines = group_words_into_lines(words_in_bbox(page_text.words, panel.bbox_pt))
    rows: list[tuple[float, float, float]] = []
    for line in lines:
        for index in range(len(line) - 4):
            words = line[index : index + 5]
            text = " ".join(word.text for word in words)
            match = _VGS_RE.fullmatch(text)
            if match is None:
                continue
            bbox = line_bbox(words)
            rows.append(
                (float(match.group(1)), bbox[0], 0.5 * (bbox[1] + bbox[3]))
            )
    if not rows:
        raise CurveBindingError("legend: no local VGS labels")

    entries: list[LegendEntry] = []
    with pymupdf.open(panel.pdf) as document:
        drawings = document[panel.page - 1].get_drawings()
        for gate_voltage, label_x0, row_y in rows:
            matches = []
            for drawing in drawings:
                style = _style_key(drawing.get("color"))
                if style is None:
                    continue
                for item in drawing.get("items", []):
                    if item[0] != "l":
                        continue
                    x0, y0 = float(item[1].x), float(item[1].y)
                    x1, y1 = float(item[2].x), float(item[2].y)
                    length = math.hypot(x1 - x0, y1 - y0)
                    if not 5.0 <= length <= 30.0:
                        continue
                    if abs(0.5 * (y0 + y1) - row_y) > LEGEND_ROW_TOLERANCE_PT:
                        continue
                    if x1 > label_x0 + 2.0 or label_x0 - x1 > 35.0:
                        continue
                    matches.append(style)
            unique = sorted(set(matches))
            if len(unique) != 1:
                raise CurveBindingError(
                    f"legend: ambiguous stroke binding for VGS={gate_voltage:g} V"
                )
            entries.append(LegendEntry(gate_voltage, unique[0], row_y))
    if len({entry.style_key for entry in entries}) != len(entries):
        raise CurveBindingError(
            "legend: stroke style reused by multiple VGS labels"
        )
    return entries


def _bind_and_calibrate_curves(
    traces: list[VectorTrace],
    legend: list[LegendEntry],
    calibration: PanelCalibration,
) -> list[dict[str, object]]:
    by_style = {trace.style_key: trace for trace in traces}
    if set(by_style) != {entry.style_key for entry in legend}:
        raise CurveBindingError(
            f"legend/trace mismatch: {len(legend)} labels, {len(traces)} styled traces"
        )
    curves = []
    for entry in sorted(legend, key=lambda item: item.gate_voltage_v):
        trace = by_style[entry.style_key]
        calibrated = [
            [
                float(calibration.x_axis.value(x)),
                float(calibration.y_axis.value(y)),
            ]
            for x, y in trace.points_px
        ]
        curves.append(
            {
                "gate_voltage_v": entry.gate_voltage_v,
                "style_rgb": list(entry.style_key),
                "trace_source": "pdf_vector",
                "points_px": [list(point) for point in trace.points_px],
                "points": calibrated,
            }
        )
    return curves


def _validation_reasons(
    panel: ChartPanel,
    calibration: PanelCalibration,
    curves: list[dict[str, object]],
) -> list[str]:
    if not curves:
        return [DIAG_CURVE_BINDING]
    reasons: list[str] = []
    x_ticks = [tick.value for tick in calibration.x_axis.ticks]
    y_ticks = [tick.value for tick in calibration.y_axis.ticks]
    if not (
        calibration.x_axis.model == "linear"
        and min(x_ticks) < 0 < max(x_ticks)
        and _RDS_TITLE_RE.search(panel.title)
        and _CASE_TEMPERATURE_AXIS_RE.search(panel.text.replace("º", "°"))
    ):
        reasons.append(DIAG_AXIS_IDENTITY)
    if max(calibration.x_axis.residual_px, calibration.y_axis.residual_px) > MAX_AXIS_RESIDUAL_PX:
        reasons.append(DIAG_AXIS_RESIDUAL)

    x_span = max(x_ticks) - min(x_ticks)
    y_span = max(y_ticks) - min(y_ticks)
    for curve in curves:
        points = np.asarray(curve["points"], dtype=float)
        order = np.argsort(points[:, 0])
        temperature = points[order, 0]
        normalized = points[order, 1]
        if (temperature[-1] - temperature[0]) / x_span < MIN_TRACE_TEMPERATURE_SPAN_FRACTION:
            reasons.append(DIAG_TEMPERATURE_SPAN)
        if (normalized.max() - normalized.min()) / y_span < MIN_TRACE_NORMALIZED_SPAN_FRACTION:
            reasons.append(DIAG_NORMALIZED_SPAN)
        if not temperature[0] < REFERENCE_TEMPERATURE_C < temperature[-1]:
            reasons.append(DIAG_REFERENCE_BRACKET)
        else:
            at_reference = float(
                np.interp(REFERENCE_TEMPERATURE_C, temperature, normalized)
            )
            curve["normalized_rds_on_at_25c"] = at_reference
            if abs(at_reference - 1.0) > MAX_REFERENCE_UNITY_ERROR:
                reasons.append(DIAG_REFERENCE_UNITY)
        if float(np.min(np.diff(normalized))) < -MAX_MONOTONIC_DROP:
            reasons.append(DIAG_MONOTONIC)

    if len(curves) >= 2:
        lo = max(float(np.min(np.asarray(curve["points"])[:, 0])) for curve in curves)
        hi = min(float(np.max(np.asarray(curve["points"])[:, 0])) for curve in curves)
        probe = lo + 0.10 * (hi - lo)
        ordered = []
        for curve in sorted(curves, key=lambda item: float(item["gate_voltage_v"])):
            points = np.asarray(curve["points"], dtype=float)
            order = np.argsort(points[:, 0])
            ordered.append(float(np.interp(probe, points[order, 0], points[order, 1])))
        if any(
            left - right < MIN_VGS_ORDER_SEPARATION
            for left, right in zip(ordered, ordered[1:])
        ):
            reasons.append(DIAG_VGS_ORDER)
    return list(dict.fromkeys(reasons))


def _draw_overlay(
    crop_path: Path,
    panel: ChartPanel,
    calibration: PanelCalibration,
    curves: list[dict[str, object]],
    status: str,
) -> np.ndarray:
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    plot = calibration.plot
    cv2.rectangle(
        image,
        (0, 0),
        (image.shape[1] - 1, max(0, plot.y0 - 2)),
        (255, 255, 255),
        -1,
    )
    draw_plot_frame(image, plot, (0, 190, 0), 2)
    palette = ((255, 80, 0), (180, 0, 220), (0, 120, 255), (0, 170, 80))
    for index, curve in enumerate(curves):
        points = np.asarray(curve["points_px"], dtype=np.int32).reshape((-1, 1, 2))
        color = palette[index % len(palette)]
        cv2.polylines(image, [points], False, color, 2, cv2.LINE_AA)
        x, y = map(int, points[-1, 0])
        cv2.putText(
            image,
            f"{float(curve['gate_voltage_v']):g}V",
            (min(x + 3, image.shape[1] - 40), max(12, y)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.34,
            color,
            1,
            cv2.LINE_AA,
        )

    draw_axis_ticks(
        image,
        plot,
        x_ticks=[(t.pixel, t.value) for t in calibration.x_axis.ticks],
        y_ticks=[(t.pixel, t.value) for t in calibration.y_axis.ticks],
        color=(255, 0, 0),
        font_scale=0.23,
        marker_size=5,
        unit_x="°C",
        unit_y="x",
    )
    color = (0, 0, 0) if status == "ok" else (0, 0, 190)
    cv2.putText(
        image,
        f"SELECTED p{panel.page} FIGURE {panel.diagram} RDS(on)/RDS(on,25C) vs T [C]  {status.upper()}",
        (4, 13),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.30,
        color,
        1,
        cv2.LINE_AA,
    )
    return image
