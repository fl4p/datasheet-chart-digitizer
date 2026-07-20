"""Digitize MOSFET RDS(on)-versus-temperature charts.

Normalized chart-native curves remain the default contract. A separate,
source-gated path also serves absolute mOhm charts only when exactly two
same-style traces, owned typ/max labels, and one local VGS/ID condition agree.
"""

from __future__ import annotations

import json
import math
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Callable

import cv2
import numpy as np
import pymupdf
from PIL import Image

from .capacitance_types import PlotBox
from .capacitance_vector import (
    _chain_vector_components,
    _filled_path_centerline,
    _resample_vector_trace_pixels,
    _vector_curve_edges,
)
from .crop_transform import CropTransform
from .chart_classifier import is_rdson_chart_title, rdson_formula_direction
from .diode_forward_voltage import (
    _full_span_grid_lines,
    _page_labels,
    _select_axis,
    _snap_axis_to_grid,
)
from .find_charts import (
    _token_norm,
    ChartPanel,
    DiagramTitle,
    PageText,
    box_px_to_pt,
    crop_panel,
    detect_rule_boxes,
    extend_wrapped_titles,
    find_diagram_titles,
    group_words_into_lines,
    find_caption_titles,
    infer_grid_regions_from_h_rules,
    line_bbox,
    line_text,
    render_page,
    run_text_bbox,
    words_in_bbox,
)
from .finder_caption_geometry import caption_axis_direction, caption_leads_nearer_grid
from .numeric_axis import NumericAxis, axis_to_json, tick_aligned_plot
from .overlay import draw_axis_ticks, draw_plot_frame

REFERENCE_TEMPERATURE_C = 25.0
MAX_AXIS_RESIDUAL_PX = 1.5
MIN_TRACE_TEMPERATURE_SPAN_FRACTION = 0.70
MIN_TRACE_NORMALIZED_SPAN_FRACTION = 0.15
MAX_REFERENCE_UNITY_ERROR = 0.06
MAX_MONOTONIC_DROP = 0.03
LEGEND_ROW_TOLERANCE_PT = 4.5
PANEL_TOP_EXPANSION_FRACTION = 0.55

DIAG_AXIS_RESIDUAL = "axis_residual_exceeds_threshold"
DIAG_AXIS_IDENTITY = "temperature_axis_identity_unverified"
DIAG_TEMPERATURE_SPAN = "temperature_span_below_threshold"
DIAG_NORMALIZED_SPAN = "normalized_rds_span_below_threshold"
DIAG_REFERENCE_BRACKET = "reference_temperature_not_bracketed"
DIAG_REFERENCE_UNITY = "normalized_rds_at_reference_outside_tolerance"
DIAG_MONOTONIC = "normalized_rds_not_nondecreasing_with_temperature"
DIAG_CURVE_BINDING = "legend_curve_binding_ambiguous"
DIAG_NO_FULL_SPAN_CURVE = "no_full_span_vector_curve"
DIAG_LEGEND_VGS_MISSING = "legend_vgs_label_missing"
DIAG_LEGEND_TRACE_MISMATCH = "legend_trace_style_mismatch"
DIAG_ABSOLUTE_LIMIT_LABELS = "absolute_rds_typ_max_labels_unverified"
DIAG_ABSOLUTE_LIMIT_ORDER = "absolute_rds_typ_max_curve_order_unverified"
DIAG_ABSOLUTE_SPAN = "absolute_rds_span_below_threshold"

_RDS_TITLE_RE = re.compile(
    r"(?:normalized\s+(?:drain(?:-|\s+to\s+)source\s+)?)?"
    r"on(?:[-\u2212\u2010-\u2014 ]+state)?[-\u2212\u2010-\u2014 ]+"
    r"resistance(?:\s+factor)?(?:\s*\(\s*normalized\s*\))?\s+"
    r"(?:vs\.?|as\s+a\s+function\s+of|variation\s+(?:with|vs\.?))\s+"
    r"(?:(?:case|junction)\s+)?temperature",
    re.I,
)
_RDS_NORMALIZED_TITLE_STEM_RE = re.compile(
    r"normalized\s+(?:drain(?:-|\s+to\s+)source\s+)?"
    r"on(?:[-\u2212\u2010-\u2014 ]+state)?[-\u2212\u2010-\u2014 ]+resistance",
    re.I,
)
_RDS_TEMPERATURE_FORMULA_RE = re.compile(
    r"r\s*ds\s*\(\s*on\s*\)\s*=\s*f\s*\(\s*t\s*j\s*\)", re.I
)
_RDS_FORMULA_VARIABLE_RE = re.compile(
    r"r\s*ds\s*\(\s*on\s*\)\s*=\s*f\s*\(\s*(i\s*d|t\s*[ajc]|v\s*gs)\s*\)",
    re.I,
)
_RDS_DIRECTION_CLAUSE_RE = re.compile(
    r"\b(?:vs\.?|with|variation|as\s+a\s+function\s+of)\b", re.I
)
_VGS_RE = re.compile(r"V\s*GS\s*=\s*(\d+(?:\.\d+)?)\s*V", re.I)
_ID_RE = re.compile(r"I\s*D\s*=\s*(\d+(?:\.\d+)?)\s*A", re.I)
_ABSOLUTE_MOHM_AXIS_RE = re.compile(
    r"(?:m\s*[ΩΩ]|m(?:illi)?\s*ohms?)", re.I
)
_TEMPERATURE_AXIS_RE = re.compile(
    r"(?:(?:case|junction)\s+temperature\s*(?:\(\s*(?:℃|(?:°|º|q|5)?\s*c)\)|[-:]\s*(?:°|º|q|5)?\s*c)|"
    r"t\s*j\s*(?:\(\s*(?:℃|(?:°|º|q|5)?\s*c)\)|\[\s*(?:℃|(?:°|º|q|5)?\s*c)\])"
    r"(?:\s+junction\s+temperature)?|"
    r"t\s*j\s*,?\s*junction\s+temperature\s*"
    r"[\[(]\s*(?:℃|(?:°|º|q|5)?\s*c)\s*[\])])", re.I
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

    def __init__(self, diagnostic: str, message: str):
        super().__init__(message)
        self.diagnostic = diagnostic


def digitize_pdf(pdf: Path, out_dir: Path, dpi: int = 180) -> list[dict[str, object]]:
    """Digitize every supported RDS(on)-temperature panel in ``pdf``."""
    return _digitize_temperature_pdf(pdf, out_dir, dpi)


def digitize_pdf_fail_closed(
    pdf: Path, out_dir: Path, dpi: int = 180
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Digitize owned panels independently and retain explicit refusals."""

    errors: list[dict[str, object]] = []
    return _digitize_temperature_pdf(pdf, out_dir, dpi, errors=errors), errors


def _digitize_temperature_pdf(
    pdf: Path,
    out_dir: Path,
    dpi: int,
    *,
    errors: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    return _digitize_rds_family(
        pdf,
        out_dir,
        dpi,
        title_selector=_rdson_temperature_titles,
        crop_group="rdson_temperature_crops",
        overlay_group="rdson_temperature_overlays",
        manifest_name="rdson_temperature.json",
        point_columns=["temperature_c", "normalized_rds_on"],
        validation=_validation_reasons,
        overlay_drawer=_draw_overlay,
        success_diagnostics=[
            "vgs_identity_bound_by_local_label_and_trace_geometry",
            "normalized_rds_validated_at_25C",
            "no_absolute_rds_or_temperature_interpolation",
        ],
        thresholds=_thresholds(),
        result_metadata={"reference_temperature_c": REFERENCE_TEMPERATURE_C},
        min_stroke_width=0.4,
        panel_selector=_rdson_temperature_panel_owned,
        error_kind="rds_on_temperature",
        errors=errors,
    )


def _digitize_rds_family(
    pdf: Path,
    out_dir: Path,
    dpi: int,
    *,
    title_selector: Callable[[PageText], list[DiagramTitle]],
    crop_group: str,
    overlay_group: str,
    manifest_name: str,
    point_columns: list[str],
    validation: Callable[[ChartPanel, PanelCalibration, list[dict[str, object]]], list[str]],
    overlay_drawer: Callable[[Path, ChartPanel, PanelCalibration, list[dict[str, object]], str], np.ndarray],
    success_diagnostics: list[str],
    thresholds: dict[str, float],
    error_kind: str,
    result_metadata: dict[str, object] | None = None,
    min_stroke_width: float = 0.8,
    panel_selector: Callable[[ChartPanel], bool] | None = None,
    errors: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Run the shared caption, axis, vector-trace, and fail-closed RDS pipeline."""
    results: list[dict[str, object]] = []
    pages = run_text_bbox(pdf)
    with tempfile.TemporaryDirectory(prefix="rdson-pages-") as tmp:
        tmpdir = Path(tmp)
        for page in pages:
            titles = title_selector(page)
            if not titles:
                continue
            page_png = render_page(pdf, page.page_num, dpi, tmpdir)
            for title in titles:
                try:
                    panel, crop_path, region = _build_panel(
                        pdf, out_dir, page, page_png, title, crop_group=crop_group
                    )
                    if panel_selector is not None and not panel_selector(panel):
                        continue
                    calibration = calibrate_panel(panel, crop_path, region)
                    results.append(_digitize_rds_panel(
                        panel, crop_path, calibration, out_dir,
                        overlay_group=overlay_group, point_columns=point_columns,
                        validation=validation, overlay_drawer=overlay_drawer,
                        success_diagnostics=success_diagnostics, thresholds=thresholds,
                        result_metadata=result_metadata,
                        min_stroke_width=min_stroke_width,
                    ))
                except Exception as error:
                    if errors is None:
                        raise
                    errors.append({
                        "kind": error_kind,
                        "page": page.page_num,
                        "diagram": title.number,
                        "error": str(error),
                    })
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / manifest_name).write_text(
        json.dumps(results, indent=2) + "\n"
    )
    return results


def _digitize_rds_panel(
    panel: ChartPanel,
    crop_path: Path,
    calibration: PanelCalibration,
    out_dir: Path,
    *,
    overlay_group: str,
    point_columns: list[str],
    validation: Callable[[ChartPanel, PanelCalibration, list[dict[str, object]]], list[str]],
    overlay_drawer: Callable[[Path, ChartPanel, PanelCalibration, list[dict[str, object]], str], np.ndarray],
    success_diagnostics: list[str],
    thresholds: dict[str, float],
    result_metadata: dict[str, object] | None = None,
    min_stroke_width: float = 0.8,
) -> dict[str, object]:
    """Digitize one already-owned and calibrated RDS panel."""
    if (
        point_columns == ["temperature_c", "normalized_rds_on"]
        and _ABSOLUTE_MOHM_AXIS_RE.search(panel.text)
    ):
        return _digitize_absolute_temperature_panel(
            panel,
            crop_path,
            calibration,
            out_dir,
            overlay_group=overlay_group,
            min_stroke_width=min_stroke_width,
        )
    binding_error = None
    try:
        traces = _extract_vector_traces(
            panel, crop_path, calibration.plot, min_stroke_width=min_stroke_width
        )
        legend = _legend_entries(panel, traces)
        curves = _bind_and_calibrate_curves(traces, legend, calibration)
        reasons = validation(panel, calibration, curves)
    except CurveBindingError as error:
        curves = []
        reasons = [error.diagnostic]
        binding_error = str(error)
    status = "refused" if reasons else "ok"
    overlay = overlay_drawer(crop_path, panel, calibration, curves, status)
    overlay_path = out_dir / overlay_group / panel.part / f"p{panel.page:02d}_d{panel.diagram}.webp"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(overlay_path), overlay):
        raise RuntimeError(f"could not write overlay: {overlay_path}")
    row = {
        "status": status,
        "diagnostics": reasons or success_diagnostics,
        "point_columns": point_columns,
        "panel": asdict(panel),
        "plot_box_px": asdict(calibration.plot),
        "x_axis": axis_to_json(calibration.x_axis),
        "y_axis": axis_to_json(calibration.y_axis),
        "curves": curves,
        "binding_error": binding_error,
        "thresholds": thresholds,
        "overlay": str(overlay_path.relative_to(out_dir)),
    }
    if result_metadata:
        row.update(result_metadata)
    return row


def _digitize_absolute_temperature_panel(
    panel: ChartPanel,
    crop_path: Path,
    calibration: PanelCalibration,
    out_dir: Path,
    *,
    overlay_group: str,
    min_stroke_width: float,
) -> dict[str, object]:
    """Serve a source-owned two-curve typ/max RDS(Tj) chart in mOhm."""

    binding_error = None
    try:
        traces = _extract_vector_traces(
            panel,
            crop_path,
            calibration.plot,
            min_stroke_width=min_stroke_width,
            preserve_same_style=True,
        )
        curves = _bind_absolute_typ_max_curves(panel, traces, calibration)
        reasons = _absolute_validation_reasons(panel, calibration, curves)
    except CurveBindingError as error:
        curves = []
        reasons = [error.diagnostic]
        binding_error = str(error)
    status = "refused" if reasons else "ok"
    overlay = _draw_overlay(crop_path, panel, calibration, curves, status)
    overlay_path = (
        out_dir / overlay_group / panel.part / f"p{panel.page:02d}_d{panel.diagram}.webp"
    )
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(overlay_path), overlay):
        raise RuntimeError(f"could not write overlay: {overlay_path}")
    return {
        "status": status,
        "diagnostics": reasons or [
            "absolute_rds_axis_unit_mohm",
            "typ_max_identity_bound_by_source_order",
            "vgs_and_id_conditions_bound_by_local_text",
        ],
        "point_columns": ["temperature_c", "rdson_mohm"],
        "axis_kind": "absolute_rds_on",
        "y_unit": "mohm",
        "panel": asdict(panel),
        "plot_box_px": asdict(calibration.plot),
        "x_axis": axis_to_json(calibration.x_axis),
        "y_axis": axis_to_json(calibration.y_axis),
        "curves": curves,
        "binding_error": binding_error,
        "thresholds": {
            "maximum_axis_residual_px": MAX_AXIS_RESIDUAL_PX,
            "minimum_temperature_span_fraction": MIN_TRACE_TEMPERATURE_SPAN_FRACTION,
            "minimum_absolute_rds_span_fraction": MIN_TRACE_NORMALIZED_SPAN_FRACTION,
            "maximum_monotonic_drop_mohm": MAX_MONOTONIC_DROP,
        },
        "reference_temperature_c": REFERENCE_TEMPERATURE_C,
        "overlay": str(overlay_path.relative_to(out_dir)),
    }


def _thresholds() -> dict[str, float]:
    return {
        "maximum_axis_residual_px": MAX_AXIS_RESIDUAL_PX,
        "minimum_temperature_span_fraction": MIN_TRACE_TEMPERATURE_SPAN_FRACTION,
        "minimum_normalized_rds_span_fraction": MIN_TRACE_NORMALIZED_SPAN_FRACTION,
        "maximum_reference_unity_error": MAX_REFERENCE_UNITY_ERROR,
        "maximum_monotonic_drop": MAX_MONOTONIC_DROP,
    }


def _rdson_temperature_titles(page: PageText) -> list[DiagramTitle]:
    """Split merged side-by-side Figure captions and keep RDS(T) only."""
    strict = _rdson_titles_matching(page, _RDS_TITLE_RE)
    relaxed = [
        title
        for title in _rdson_titles_matching(page, _RDS_NORMALIZED_TITLE_STEM_RE)
        if _RDS_DIRECTION_CLAUSE_RE.search(title.title) is None
    ]
    relaxed.extend(
        title for title in find_caption_titles(page)
        if rdson_formula_direction(title.title) == "temperature"
    )
    all_titles = extend_wrapped_titles(page, find_diagram_titles(page)) + find_caption_titles(page)
    relaxed.extend(
        title for title in all_titles
        if is_rdson_chart_title(title.title)
        and _nearby_rdson_formula_evidence(page, title)[0] == "temperature"
    )
    unique = {title.number: title for title in relaxed}
    unique.update({title.number: title for title in strict})
    return sorted(unique.values(), key=lambda title: (title.bbox_pt[1], title.bbox_pt[0]))


def _rdson_temperature_panel_owned(panel: ChartPanel) -> bool:
    """Route a title-only fallback only with owned Tj-axis and formula proof."""

    if panel.kind != "rds_on":
        return False
    if _RDS_TITLE_RE.search(panel.title):
        return True
    if rdson_formula_direction(panel.title) == "temperature":
        return True
    if _RDS_TEMPERATURE_FORMULA_RE.search(panel.text):
        return True
    local = panel.text.replace("º", "°")
    return bool(
        _RDS_NORMALIZED_TITLE_STEM_RE.search(panel.title)
        and _TEMPERATURE_AXIS_RE.search(local)
        and _RDS_TEMPERATURE_FORMULA_RE.search(local)
    )


def _nearby_rdson_formula_evidence(
    page: PageText, title: DiagramTitle
) -> tuple[str | None, str | None]:
    """Return formula variable and chart side for the title's own page column."""
    tcx = 0.5 * (title.bbox_pt[0] + title.bbox_pt[2])
    tcy = 0.5 * (title.bbox_pt[1] + title.bbox_pt[3])
    split = 0.5 * page.width_pt
    x0, x1 = (0.0, split) if tcx < split else (split, page.width_pt)
    candidates: list[tuple[float, str, str]] = []
    for line in group_words_into_lines(page.words):
        local = [word for word in line if x0 <= 0.5 * (word.x0 + word.x1) <= x1]
        if not local:
            continue
        bbox = line_bbox(local)
        cy = 0.5 * (bbox[1] + bbox[3])
        if abs(cy - tcy) > 340.0:
            continue
        match = _RDS_FORMULA_VARIABLE_RE.search(line_text(local))
        if match is None:
            continue
        variable = re.sub(r"[^a-z]", "", match.group(1).lower())
        direction = "current" if variable in {"id", "ids"} else (
            "temperature" if variable in {"ta", "tj", "tc"} else "gate_voltage"
        )
        side = "below" if cy > tcy else "above"
        candidates.append((abs(cy - tcy), direction, side))
    if not candidates:
        return None, None
    _distance, direction, side = min(candidates)
    return direction, side


def _rdson_titles_matching(
    page: PageText, title_pattern: re.Pattern[str]
) -> list[DiagramTitle]:
    """Split numbered captions and retain only titles matching one RDS family."""
    titles: list[DiagramTitle] = []
    lines = group_words_into_lines(page.words)
    for line_index, line in enumerate(lines):
        starts = [
            index
            for index, word in enumerate(line)
            if re.match(
                r"(?i)^(?:Figure|Fig\.?|Diagram)\b", word.text.strip()
            )
        ]
        if not starts:
            continue
        starts.append(len(line))
        for start, end in zip(starts, starts[1:]):
            segment = line[start:end]
            segment_bbox = line_bbox(segment)
            text = line_text(segment)
            match = re.match(
                r"(?i)^(?:Figure|Fig\.?|Diagram)\s+(\d+(?:[.-]\d+)?)[\.:]?\s+(.+)$",
                text,
            )
            if match is None or title_pattern.search(match.group(2)) is None:
                continuation = []
                for following in lines[line_index + 1 : line_index + 3]:
                    following_bbox = line_bbox(following)
                    if following_bbox[1] - segment_bbox[3] > 18.0:
                        break
                    local = [
                        word
                        for word in following
                        if segment_bbox[0] - 8.0
                        <= 0.5 * (word.x0 + word.x1)
                        <= segment_bbox[2] + 8.0
                    ]
                    if local:
                        continuation.extend(local)
                        local_bbox = line_bbox(local)
                        segment_bbox = (
                            min(segment_bbox[0], local_bbox[0]),
                            segment_bbox[1],
                            max(segment_bbox[2], local_bbox[2]),
                            local_bbox[3],
                        )
                text = " ".join(
                    filter(None, (line_text(segment), line_text(continuation)))
                )
                match = re.match(
                    r"(?i)^(?:Figure|Fig\.?|Diagram)\s+(\d+(?:[.-]\d+)?)[\.:]?\s+(.+)$",
                    text,
                )
            if match is None or title_pattern.search(match.group(2)) is None:
                continue
            titles.append(
                DiagramTitle(
                    number=int(re.sub(r"[.-]", "", match.group(1))),
                    title=match.group(2).strip(),
                    bbox_pt=segment_bbox,
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
    *,
    crop_group: str = "rdson_temperature_crops",
) -> tuple[ChartPanel, Path, tuple[float, float, float, float]]:
    """Bind an RDS caption to its local grid using own-axis direction evidence."""
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
        if min(
            abs(title.bbox_pt[1] - region[3]),
            abs(region[1] - title.bbox_pt[3]),
        ) <= 100.0
        and abs(0.5 * (region[0] + region[2]) - tcx) <= 100.0
    ]
    direction = caption_axis_direction(page, title, "rds_on", _token_norm)
    formula_direction, formula_side = _nearby_rdson_formula_evidence(page, title)
    if direction is None and formula_direction is not None:
        direction = formula_side
    if direction is None and _RDS_NORMALIZED_TITLE_STEM_RE.search(title.title):
        for region in candidates:
            if region[1] < title.bbox_pt[3]:
                continue
            local_text = " ".join(
                word.text
                for word in words_in_bbox(
                    page.words,
                    (
                        region[0] - 42.0,
                        title.bbox_pt[1],
                        region[2] + 8.0,
                        min(page.height_pt, region[3] + 42.0),
                    ),
                )
            )
            if (
                _TEMPERATURE_AXIS_RE.search(local_text.replace("º", "°"))
                and _RDS_TEMPERATURE_FORMULA_RE.search(local_text)
            ):
                direction = "below"
                break
    candidates, direction = _directional_grid_candidates(
        candidates, title, direction
    )
    if not candidates:
        raise RuntimeError("panel: no direction-evidenced local RDS grid")
    region = min(
        candidates,
        key=lambda item: min(
            abs(title.bbox_pt[1] - item[3]),
            abs(item[1] - title.bbox_pt[3]),
        )
        + 0.2 * abs(0.5 * (item[0] + item[2]) - tcx),
    )
    if direction == "below":
        bbox = (
            max(0.0, region[0] - 42.0), max(title.bbox_pt[3] + 2.0, region[1] - 8.0),
            min(page.width_pt, region[2] + 8.0), min(page.height_pt, region[3] + 42.0),
        )
    else:
        height = region[3] - region[1]
        expanded_top = max(0.0, region[1] - PANEL_TOP_EXPANSION_FRACTION * height)
        above_text = " ".join(
            word.text for word in words_in_bbox(
                page.words, (region[0] - 8.0, expanded_top, region[2] + 8.0, region[1])
            )
        )
        top = expanded_top if _VGS_RE.search(above_text) else max(0.0, region[1] - 8.0)
        bbox = (
            max(0.0, region[0] - 8.0), top, min(page.width_pt, region[2] + 8.0),
            min(title.bbox_pt[1] - 2.0, region[3] + 8.0),
        )
    crop_path = (
        out_dir
        / crop_group
        / pdf.stem
        / f"p{page.page_num:02d}_d{title.number}.png"
    )
    effective = crop_panel(page_png, page, bbox, crop_path)
    identity_bbox = bbox if direction == "below" else (bbox[0], bbox[1], bbox[2], title.bbox_pt[1])
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


def _directional_grid_candidates(
    candidates: list[tuple[float, float, float, float]],
    title: DiagramTitle,
    direction: str | None,
) -> tuple[list[tuple[float, float, float, float]], str | None]:
    """Apply positive direction evidence or a unique following-grid fallback."""

    above = [region for region in candidates if region[3] <= title.bbox_pt[1]]
    below = [region for region in candidates if region[1] >= title.bbox_pt[3]]
    if direction == "above":
        return above, direction
    if direction == "below":
        return below, direction
    if above and below:
        return [], None
    scored = [
        (
            abs(0.5 * (region[0] + region[2]) - 0.5 * (title.bbox_pt[0] + title.bbox_pt[2])),
            min(
                abs(title.bbox_pt[1] - region[3]),
                abs(region[1] - title.bbox_pt[3]),
            ),
            region,
        )
        for region in candidates
    ]
    if not above and caption_leads_nearer_grid(
        scored, title.bbox_pt[1], title.bbox_pt[3]
    ):
        return below, "below"
    return above, "above" if above else None


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
    if sum(rgb) < 0.15 or max(rgb) < 0.4 or max(rgb) - min(rgb) > 0.45:
        return rgb
    return None


def _extract_vector_traces(
    panel: ChartPanel,
    crop_path: Path,
    plot: PlotBox,
    *,
    min_stroke_width: float = 0.8,
    preserve_same_style: bool = False,
) -> list[VectorTrace]:
    gray = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    transform = CropTransform.for_chart(asdict(panel), gray.shape)
    top_left = transform.to_pt(plot.x0, plot.y0)
    bottom_right = transform.to_pt(plot.x1, plot.y1)
    rect = pymupdf.Rect(*top_left, *bottom_right)
    best: dict[tuple[float, float, float], tuple[float, VectorTrace]] = {}
    retained: list[tuple[float, VectorTrace]] = []
    stroked_by_style: dict[tuple[float, float, float], list] = {}
    filled_components: list[
        tuple[tuple[float, float, float], list[tuple[float, float]]]
    ] = []
    with pymupdf.open(panel.pdf) as document:
        for drawing in document[panel.page - 1].get_drawings():
            style = _style_key(drawing.get("color") or drawing.get("fill"))
            if style is None:
                continue
            if drawing.get("type") == "f":
                filled_components.append(
                    (style, _filled_path_centerline(drawing, rect))
                )
            else:
                normalized = dict(drawing)
                normalized["color"] = (0.0, 0.0, 0.0)
                stroked_by_style.setdefault(style, []).extend(
                    _vector_curve_edges(
                        [normalized], rect, min_stroke_width=min_stroke_width
                    )
                )

    components = list(filled_components)
    for style, edges in stroked_by_style.items():
        components.extend(
            (style, component)
            for component in _chain_vector_components(edges)
        )
    for style, component in components:
        if not component:
            continue
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
        if preserve_same_style:
            retained.append((score, trace))
        elif style not in best or score > best[style][0]:
            best[style] = (score, trace)
    traces = (
        [
            item[1]
            for item in sorted(
                retained,
                key=lambda item: (
                    item[1].style_key,
                    float(np.median([point[1] for point in item[1].points_px])),
                    -item[0],
                ),
            )
        ]
        if preserve_same_style
        else [item[1] for item in best.values()]
    )
    if len(traces) < 1:
        raise CurveBindingError(
            DIAG_NO_FULL_SPAN_CURVE, "trace: no full-span vector curves"
        )
    return traces


def _trace_y_at_x(trace: VectorTrace, xs: np.ndarray) -> np.ndarray:
    """Interpolate one resampled trace in pixel space without extrapolation."""

    by_x: dict[int, list[int]] = {}
    for x, y in trace.points_px:
        by_x.setdefault(x, []).append(y)
    ordered_x = np.asarray(sorted(by_x), dtype=float)
    ordered_y = np.asarray(
        [float(np.median(by_x[int(x)])) for x in ordered_x], dtype=float
    )
    if len(ordered_x) < 8 or xs[0] < ordered_x[0] or xs[-1] > ordered_x[-1]:
        raise CurveBindingError(
            DIAG_ABSOLUTE_LIMIT_ORDER,
            "absolute RDS trace lacks shared full-span X support",
        )
    return np.interp(xs, ordered_x, ordered_y)


def _bind_absolute_typ_max_curves(
    panel: ChartPanel,
    traces: list[VectorTrace],
    calibration: PanelCalibration,
) -> list[dict[str, object]]:
    """Bind two same-style absolute curves from owned typ/max semantics."""

    labels = {
        token.lower()
        for token in re.findall(r"(?<!\w)(typ|max)(?!\w)", panel.text, re.I)
    }
    if labels != {"typ", "max"} or len(traces) != 2:
        raise CurveBindingError(
            DIAG_ABSOLUTE_LIMIT_LABELS,
            "absolute RDS serving requires exactly two traces and owned typ/max labels",
        )
    gate_voltages = sorted({float(value) for value in _VGS_RE.findall(panel.text)})
    drain_currents = sorted({float(value) for value in _ID_RE.findall(panel.text)})
    if len(gate_voltages) != 1 or len(drain_currents) != 1:
        raise CurveBindingError(
            DIAG_ABSOLUTE_LIMIT_LABELS,
            "absolute RDS typ/max curves lack one owned VGS and ID condition",
        )

    lo = max(min(x for x, _y in trace.points_px) for trace in traces)
    hi = min(max(x for x, _y in trace.points_px) for trace in traces)
    if hi <= lo:
        raise CurveBindingError(
            DIAG_ABSOLUTE_LIMIT_ORDER,
            "absolute RDS typ/max traces have no common temperature span",
        )
    xs = np.linspace(lo, hi, 96)
    sampled = [_trace_y_at_x(trace, xs) for trace in traces]
    order = sorted(range(2), key=lambda index: float(np.median(sampled[index])))
    maximum_trace, typical_trace = traces[order[0]], traces[order[1]]
    separation = sampled[order[1]] - sampled[order[0]]
    visible_margin = max(2.0, 0.005 * calibration.plot.height)
    if float(np.min(separation)) < visible_margin:
        raise CurveBindingError(
            DIAG_ABSOLUTE_LIMIT_ORDER,
            "absolute RDS max trace is not visibly above typ over the shared span",
        )

    curves = []
    for limit, trace in (("typ", typical_trace), ("max", maximum_trace)):
        curves.append(
            {
                "limit": limit,
                "gate_voltage_v": gate_voltages[0],
                "drain_current_a": drain_currents[0],
                "style_rgb": list(trace.style_key),
                "trace_source": "pdf_vector",
                "points_px": [list(point) for point in trace.points_px],
                "points": [
                    [
                        float(calibration.x_axis.value(x)),
                        float(calibration.y_axis.value(y)),
                    ]
                    for x, y in trace.points_px
                ],
            }
        )
    return curves


def _legend_entries(
    panel: ChartPanel, traces: list[VectorTrace]
) -> list[LegendEntry]:
    """Bind each local VGS legend row to its adjacent stroke style."""
    page_text = run_text_bbox(Path(panel.pdf))[panel.page - 1]
    lines = group_words_into_lines(words_in_bbox(page_text.words, panel.bbox_pt))
    rows: list[tuple[float, float, float]] = []
    for line in lines:
        before = len(rows)
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
        if len(rows) == before:
            text = line_text(line)
            for match in _VGS_RE.finditer(text):
                bbox = line_bbox(line)
                rows.append(
                    (float(match.group(1)), bbox[0], 0.5 * (bbox[1] + bbox[3]))
                )
    rows = list(dict.fromkeys(rows))
    if not rows:
        raise CurveBindingError(
            DIAG_LEGEND_VGS_MISSING, "legend: no local VGS labels"
        )
    if len(rows) == 1 and len(traces) == 1:
        gate_voltage, _label_x0, row_y = rows[0]
        return [LegendEntry(gate_voltage, traces[0].style_key, row_y)]

    entries: list[LegendEntry] = []
    with pymupdf.open(panel.pdf) as document:
        drawings = document[panel.page - 1].get_drawings()
        for gate_voltage, label_x0, row_y in rows:
            matches = []
            for drawing in drawings:
                style = _style_key(drawing.get("color") or drawing.get("fill"))
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
                    DIAG_CURVE_BINDING,
                    f"legend: ambiguous stroke binding for VGS={gate_voltage:g} V"
                )
            entries.append(LegendEntry(gate_voltage, unique[0], row_y))
    if len({entry.style_key for entry in entries}) != len(entries):
        raise CurveBindingError(
            DIAG_CURVE_BINDING,
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
            DIAG_LEGEND_TRACE_MISMATCH,
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


def _absolute_validation_reasons(
    panel: ChartPanel,
    calibration: PanelCalibration,
    curves: list[dict[str, object]],
) -> list[str]:
    """Validate absolute mOhm typ/max curves without a normalized-unity test."""

    if not curves:
        return [DIAG_NO_FULL_SPAN_CURVE]
    reasons: list[str] = []
    x_ticks = [tick.value for tick in calibration.x_axis.ticks]
    y_ticks = [tick.value for tick in calibration.y_axis.ticks]
    title_identity = bool(
        _RDS_TITLE_RE.search(panel.title)
        or _RDS_TEMPERATURE_FORMULA_RE.search(panel.text)
    )
    if not (
        calibration.x_axis.model == "linear"
        and min(x_ticks) < 0 < max(x_ticks)
        and title_identity
        and _TEMPERATURE_AXIS_RE.search(panel.text.replace("º", "°"))
    ):
        reasons.append(DIAG_AXIS_IDENTITY)
    if max(calibration.x_axis.residual_px, calibration.y_axis.residual_px) > MAX_AXIS_RESIDUAL_PX:
        reasons.append(DIAG_AXIS_RESIDUAL)

    x_span = max(x_ticks) - min(x_ticks)
    y_span = max(y_ticks) - min(y_ticks)
    at_reference: dict[str, float] = {}
    for curve in curves:
        points = np.asarray(curve["points"], dtype=float)
        order = np.argsort(points[:, 0])
        temperature = points[order, 0]
        resistance = points[order, 1]
        if (temperature[-1] - temperature[0]) / x_span < MIN_TRACE_TEMPERATURE_SPAN_FRACTION:
            reasons.append(DIAG_TEMPERATURE_SPAN)
        if (resistance.max() - resistance.min()) / y_span < MIN_TRACE_NORMALIZED_SPAN_FRACTION:
            reasons.append(DIAG_ABSOLUTE_SPAN)
        if not temperature[0] < REFERENCE_TEMPERATURE_C < temperature[-1]:
            reasons.append(DIAG_REFERENCE_BRACKET)
        else:
            value = float(
                np.interp(REFERENCE_TEMPERATURE_C, temperature, resistance)
            )
            curve["rdson_mohm_at_25c"] = value
            at_reference[str(curve["limit"])] = value
        if float(np.min(np.diff(resistance))) < -MAX_MONOTONIC_DROP:
            reasons.append(DIAG_MONOTONIC)
    if set(at_reference) == {"typ", "max"} and not (
        at_reference["max"] > at_reference["typ"]
    ):
        reasons.append(DIAG_ABSOLUTE_LIMIT_ORDER)
    return list(dict.fromkeys(reasons))


def _validation_reasons(
    panel: ChartPanel,
    calibration: PanelCalibration,
    curves: list[dict[str, object]],
) -> list[str]:
    if not curves:
        return [DIAG_NO_FULL_SPAN_CURVE]
    reasons: list[str] = []
    x_ticks = [tick.value for tick in calibration.x_axis.ticks]
    y_ticks = [tick.value for tick in calibration.y_axis.ticks]
    title_identity = bool(
        _RDS_TITLE_RE.search(panel.title)
        or _RDS_TEMPERATURE_FORMULA_RE.search(panel.text)
    )
    if not (
        calibration.x_axis.model == "linear"
        and min(x_ticks) < 0 < max(x_ticks)
        and title_identity
        and _TEMPERATURE_AXIS_RE.search(panel.text.replace("º", "°"))
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
    absolute_mohm = bool(_ABSOLUTE_MOHM_AXIS_RE.search(panel.text))
    draw_plot_frame(image, plot, (0, 190, 0))
    palette = ((255, 80, 0), (180, 0, 220), (0, 120, 255), (0, 170, 80))
    for index, curve in enumerate(curves):
        points = np.asarray(curve["points_px"], dtype=np.int32).reshape((-1, 1, 2))
        color = palette[index % len(palette)]
        cv2.polylines(image, [points], False, color, 2, cv2.LINE_AA)
        x, y = map(int, points[-1, 0])
        cv2.putText(
            image,
            str(curve.get("limit") or f"{float(curve['gate_voltage_v']):g}V"),
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
        unit_y="mOhm" if absolute_mohm else "x",
    )
    color = (0, 0, 0) if status == "ok" else (0, 0, 190)
    heading = (
        "RDS(on) [mOhm] vs Tj [C]"
        if absolute_mohm
        else "RDS(on)/RDS(on,25C) vs T [C]"
    )
    cv2.putText(
        image,
        f"SELECTED p{panel.page} FIGURE {panel.diagram} {heading}  {status.upper()}",
        (4, 13),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.30,
        color,
        1,
        cv2.LINE_AA,
    )
    return image
