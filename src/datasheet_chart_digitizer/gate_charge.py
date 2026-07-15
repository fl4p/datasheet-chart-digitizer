"""Package-owned gate-charge chart digitization API."""

from __future__ import annotations

import math
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pymupdf
import numpy as np
from PIL import Image

from .find_charts import (
    ChartPanel,
    PageText,
    process_page_texts,
    process_pdf,
    run_tesseract_page_text,
)
from .gate_charge_estimation import (
    _best_x_axis_for_panel,
    _best_y_axis_for_panel,
    _estimate_vpl_from_curve,
    _has_gate_charge_evidence,
    _local_y_ticks_for_plot,
    _non_gate_plot_reason,
    _text_near_rect,
)
from .gate_charge_trace import (
    _curve_score,
    _detect_inner_plot_box,
    _detect_regular_grid_box,
    _mask_page_text,
    _pdf_to_px,
    _smooth_polyline,
    _trace_gate_curve,
    _trace_vector_gate_curve,
)


@dataclass(frozen=True)
class GateChargeResult:
    """One package-native gate-charge chart result.

    Page numbers follow the public chart manifest and are therefore 1-based.
    Pixel coordinates are relative to ``crop_box_pt`` rendered at ``dpi``.
    """

    pdf: str
    panel: ChartPanel
    vpl: float | None
    status: str
    score: float
    trace_source: str
    dpi: int
    crop_box_pt: tuple[float, float, float, float]
    plot_box_px: tuple[int, int, int, int]
    curve_px: tuple[tuple[int, int], ...]
    vpl_y_px: float | None
    y_tick_count: int
    diagnostics: tuple[str, ...] = ()

    def to_manifest(self) -> dict[str, object]:
        payload = asdict(self)
        payload["panel"] = asdict(self.panel)
        return payload


def digitize_gate_charge(
    pdf_path: str | Path,
    *,
    dpi: int = 220,
    finder_dpi: int = 120,
) -> list[GateChargeResult]:
    """Find and digitize every plausible gate-charge chart in ``pdf_path``."""

    pdf = Path(pdf_path).expanduser().resolve()
    if not pdf.exists():
        raise FileNotFoundError(pdf)

    with tempfile.TemporaryDirectory(prefix="dsdig-gate-charge-") as tmp:
        panels, page_text = _discover_gate_panels(pdf, Path(tmp), finder_dpi)

    results: list[GateChargeResult] = []
    with pymupdf.open(pdf) as doc:
        for panel in panels:
            result = _digitize_panel(pdf, doc, panel, dpi, page_text.get(panel.page))
            if result is not None:
                results.append(result)
    return sorted(results, key=_result_sort_key)


def _discover_gate_panels(
    pdf: Path, out_dir: Path, dpi: int
) -> tuple[list[ChartPanel], dict[int, PageText]]:
    """Use OCR only when normal discovery yields no gate-charge panels."""

    panels = [panel for panel in process_pdf(pdf, out_dir, dpi) if panel.kind == "gate_charge"]
    ocr_by_page: dict[int, PageText] = {}
    if not panels:
        ocr_pages = run_tesseract_page_text(pdf)
        if ocr_pages:
            ocr_by_page = {page.page_num: page for page in ocr_pages}
            panels = [
                panel
                for panel in process_page_texts(pdf, out_dir, dpi, ocr_pages)
                if panel.kind == "gate_charge"
            ]
    return [_detach_transient_panel_artifacts(panel) for panel in panels], ocr_by_page


class _PageWordOverride:
    """Delegate page graphics while exposing injected OCR words to text consumers."""

    def __init__(self, page: pymupdf.Page, page_text: PageText):
        self._page = page
        self._words = [
            (word.x0, word.y0, word.x1, word.y1, word.text)
            for word in page_text.words
        ]

    def get_text(self, option: str, *args, **kwargs):
        if option == "words":
            return self._words
        return self._page.get_text(option, *args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._page, name)


def _ocr_x_ticks_with_zero(
    ticks: list[tuple[float, float]], panel_rect: pymupdf.Rect
) -> list[tuple[float, float]]:
    """Extrapolate an OCR-omitted zero from a short arithmetic x-axis run."""

    ordered = sorted(ticks, key=lambda item: item[1])
    if len(ordered) < 3 or ordered[0][0] <= 0:
        return ordered
    values = np.array([value for value, _x in ordered], dtype=float)
    xs = np.array([x for _value, x in ordered], dtype=float)
    value_steps = np.diff(values)
    x_steps = np.diff(xs)
    if np.any(value_steps <= 0) or np.any(x_steps <= 0):
        return ordered
    value_step = float(np.median(value_steps))
    x_step = float(np.median(x_steps))
    if ordered[0][0] > 1.5 * value_step:
        return ordered
    if np.max(np.abs(value_steps - value_step)) > 0.12 * value_step:
        return ordered
    if np.max(np.abs(x_steps - x_step)) > 0.12 * x_step:
        return ordered
    slope, intercept = np.polyfit(values, xs, 1)
    zero_x = float(intercept)
    if not panel_rect.x0 - 30.0 <= zero_x < xs[0]:
        return ordered
    if xs[0] - zero_x > 1.5 * x_step:
        return ordered
    return [(0.0, zero_x), *ordered]


def _detach_transient_panel_artifacts(panel: ChartPanel) -> ChartPanel:
    """Clear finder paths whose temporary output directory will be deleted."""

    return replace(panel, crop_png="")


def find_vpl_result(
    pdf_path: str | Path,
    *,
    dpi: int = 220,
    finder_dpi: int = 120,
) -> GateChargeResult | None:
    """Return the highest-ranked package-native Vpl result, if one exists.

    The numeric compatibility corpus is accepted. Callers that need provenance
    should still retain the result and inspect ``status`` and ``diagnostics``
    rather than reducing it to a scalar.
    """

    results = digitize_gate_charge(pdf_path, dpi=dpi, finder_dpi=finder_dpi)
    return next((result for result in results if result.vpl is not None), None)


def _digitize_panel(
    pdf: Path,
    doc: pymupdf.Document,
    panel: ChartPanel,
    dpi: int,
    page_text: PageText | None = None,
) -> GateChargeResult | None:
    page_index = panel.page - 1
    if not 0 <= page_index < len(doc):
        return None
    page = doc[page_index]
    text_page = _PageWordOverride(page, page_text) if page_text is not None else page
    panel_rect = pymupdf.Rect(panel.bbox_pt)
    expanded_image_rect = _containing_chart_image(page, panel_rect)
    if expanded_image_rect is not None:
        panel_rect = expanded_image_rect
    panel_axis = _best_y_axis_for_panel(text_page, panel_rect)
    panel_y_ticks, axis_x = panel_axis if panel_axis is not None else ([], None)
    panel_x_ticks: list[tuple[float, float]] = []
    if panel_y_ticks:
        tick_ys = [y for _value, y in panel_y_ticks]
        if len(tick_ys) >= 3:
            spacing = float(np.median(np.diff(sorted(tick_ys))))
            pad_y = max(9.0, 0.6 * spacing)
            panel_rect.y0 = max(page.rect.y0, min(tick_ys) - pad_y)
            panel_rect.y1 = min(page.rect.y1, max(tick_ys) + pad_y)
        else:
            panel_rect.y0 = max(page.rect.y0, min(panel_rect.y0, min(tick_ys) - 14.0))
            panel_rect.y1 = min(page.rect.y1, max(panel_rect.y1, max(tick_ys) + 14.0))
        if axis_x is not None:
            distance_to_left = abs(axis_x - panel_rect.x0)
            distance_to_right = abs(axis_x - panel_rect.x1)
            if distance_to_left <= distance_to_right:
                panel_rect.x0 = max(panel_rect.x0, axis_x + 3.0)
            else:
                panel_rect.x1 = min(panel_rect.x1, axis_x - 3.0)
    panel_x_axis = _best_x_axis_for_panel(text_page, panel_rect)
    if panel_x_axis is not None:
        panel_x_ticks, _axis_y = panel_x_axis
        if page_text is not None:
            panel_x_ticks = _ocr_x_ticks_with_zero(panel_x_ticks, panel_rect)
        tick_xs = sorted(x for _value, x in panel_x_ticks)
        spacing = float(np.median(np.diff(tick_xs)))
        pad_x = max(6.0, 0.45 * spacing)
        if axis_x is None:
            panel_rect.x0 = max(page.rect.x0, tick_xs[0] - pad_x)
        panel_rect.x1 = min(page.rect.x1, tick_xs[-1] + pad_x)

    crop_rect = (panel_rect + (-46, -38, 42, 52)) & page.rect
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=crop_rect, alpha=False)
    crop = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    trace_crops = [_mask_page_text(crop, page, crop_rect, panel_rect, scale)]
    if page_text is not None:
        trace_crops.append(
            _mask_page_text(crop, text_page, crop_rect, panel_rect, scale)
        )

    bx0, by0 = _pdf_to_px(crop_rect, scale, panel_rect.x0, panel_rect.y0)
    bx1, by1 = _pdf_to_px(crop_rect, scale, panel_rect.x1, panel_rect.y1)
    loose_plot_box = (int(round(bx0)), int(round(by0)), int(round(bx1)), int(round(by1)))
    detected_plot_box = _detect_inner_plot_box(crop, loose_plot_box)
    raster_grid = None
    if not panel_x_ticks and not panel_y_ticks:
        candidate_grid = _detect_regular_grid_box(crop, loose_plot_box)
        if candidate_grid is not None and _regular_grid_matches_panel(
            candidate_grid[0],
            detected_plot_box,
            loose_plot_box,
            allow_neighbor_split=_overlapping_image_count(page, pymupdf.Rect(panel.bbox_pt)) >= 2,
        ):
            raster_grid = candidate_grid
    plot_box = raster_grid[0] if raster_grid is not None else detected_plot_box
    plot_box = _bind_plot_box_to_axes(
        plot_box,
        crop_rect,
        scale,
        panel_x_ticks,
        panel_y_ticks,
        crop.size,
    )
    plot_rect = pymupdf.Rect(
        crop_rect.x0 + plot_box[0] / scale,
        crop_rect.y0 + plot_box[1] / scale,
        crop_rect.x0 + plot_box[2] / scale,
        crop_rect.y0 + plot_box[3] / scale,
    )
    tight_context = _text_near_rect(text_page, plot_rect, pad=12.0)
    broad_context = _text_near_rect(text_page, plot_rect, pad=60.0)
    non_gate_reason = _non_gate_plot_reason(tight_context, broad_context)
    mixed_gate_context = (
        non_gate_reason is not None
        and non_gate_reason != "spec_table"
        and _has_gate_charge_evidence(broad_context)
    )
    if non_gate_reason is not None and not mixed_gate_context:
        return GateChargeResult(
            pdf=str(pdf),
            panel=panel,
            vpl=None,
            status="rejected_non_gate",
            score=-1e9,
            trace_source="none",
            dpi=dpi,
            crop_box_pt=tuple(float(value) for value in crop_rect),
            plot_box_px=plot_box,
            curve_px=(),
            vpl_y_px=None,
            y_tick_count=len(panel_y_ticks),
            diagnostics=(f"non_gate_plot:{non_gate_reason}",),
        )
    local_y_ticks = _local_y_ticks_for_plot(text_page, crop_rect, scale, plot_box)
    if len(local_y_ticks) < 2:
        local_y_ticks = panel_y_ticks
    axis_grid_inferred = False
    if len(local_y_ticks) < 2 and raster_grid is not None:
        grid_ys = raster_grid[1]
        intervals = len(grid_ys) - 1
        if 2 <= intervals <= 12:
            v_max = float(intervals * (2 if intervals <= 5 else 1))
            local_y_ticks = [
                (v_max * (intervals - index) / intervals, crop_rect.y0 + y / scale)
                for index, y in enumerate(grid_ys)
            ]
            axis_grid_inferred = True
    measured_y_tick_count = len(local_y_ticks)
    axis_assumed = measured_y_tick_count < 2
    if axis_assumed:
        _x0, y0, _x1, y1 = plot_box
        local_y_ticks = [
            (10.0, crop_rect.y0 + y0 / scale),
            (0.0, crop_rect.y0 + y1 / scale),
        ]

    raster_curve = _select_raster_curve(
        [
            _smooth_polyline(_trace_gate_curve(trace_crop, plot_box))
            for trace_crop in trace_crops
        ],
        crop.height,
        crop.width,
    )
    vector_curve = _smooth_polyline(
        _trace_vector_gate_curve(page, crop_rect, scale, plot_box), stride=1
    )
    choices = [
        (raster_curve, "raster", _curve_score(raster_curve, crop.height, crop.width)),
        (vector_curve, "vector", _curve_score(vector_curve, crop.height, crop.width)),
    ]
    curve, trace_source, trace_score = max(choices, key=lambda choice: choice[2])
    vpl, vpl_y_px = _estimate_vpl_from_curve(
        curve, panel, crop_rect, scale, plot_box, local_y_ticks
    )
    if vpl is not None and not math.isfinite(vpl):
        vpl = None

    diagnostics: list[str] = []
    low_trace_confidence = trace_score <= -1e8
    if len(curve) < 20:
        diagnostics.append("insufficient_curve_points")
    elif low_trace_confidence:
        diagnostics.append("low_trace_confidence")
    if axis_assumed:
        diagnostics.append("axis_assumed_0_10")
    elif axis_grid_inferred:
        diagnostics.append("axis_inferred_from_regular_grid")
    if vpl is None:
        diagnostics.append("vpl_unresolved")
    elif not 1.0 <= vpl <= 12.0:
        diagnostics.append("vpl_outside_expected_range")

    score = trace_score + min(4.0, 0.45 * measured_y_tick_count)
    score += _title_score(panel)
    if vpl is None:
        score -= 30.0
    elif not 1.0 <= vpl <= 12.0:
        score -= 12.0

    if vpl is None or len(curve) < 20:
        status = "unresolved"
    elif axis_assumed:
        status = "axis_assumed"
    elif axis_grid_inferred:
        status = "axis_grid_inferred"
    elif low_trace_confidence:
        status = "low_confidence"
    else:
        status = "ok"
    result = GateChargeResult(
        pdf=str(pdf),
        panel=panel,
        vpl=vpl,
        status=status,
        score=round(score, 6),
        trace_source=trace_source,
        dpi=dpi,
        crop_box_pt=tuple(float(value) for value in crop_rect),
        plot_box_px=plot_box,
        curve_px=tuple(curve),
        vpl_y_px=vpl_y_px,
        y_tick_count=measured_y_tick_count,
        diagnostics=tuple(diagnostics),
    )
    if non_gate_reason is not None and mixed_gate_context and (
        low_trace_confidence or vpl is None or not 1.0 <= vpl <= 12.0
    ):
        return replace(
            result,
            vpl=None,
            status="unresolved",
            score=round(score - 30.0, 6),
            vpl_y_px=None,
            diagnostics=tuple(
                dict.fromkeys((*diagnostics, f"ambiguous_neighbor:{non_gate_reason}", "vpl_unresolved"))
            ),
        )
    return result


def _select_raster_curve(
    curves: list[list[tuple[int, int]]], height: int, width: int
) -> list[tuple[int, int]]:
    """Choose between native- and OCR-text masking with the normal trace score."""

    return max(curves, key=lambda curve: _curve_score(curve, height, width), default=[])


def _bind_plot_box_to_axes(
    detected: tuple[int, int, int, int],
    crop_rect: pymupdf.Rect,
    scale: float,
    x_ticks: list[tuple[float, float]],
    y_ticks: list[tuple[float, float]],
    crop_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Bind detected plot edges to calibrated tick spans when available."""

    x0, y0, x1, y1 = detected
    if len(x_ticks) >= 3:
        tick_xs = [x for _value, x in x_ticks]
        tick_x0 = int(round((min(tick_xs) - crop_rect.x0) * scale))
        tick_x1 = int(round((max(tick_xs) - crop_rect.x0) * scale))
        if tick_x0 < x0 - 4:
            x0 = tick_x0
        if tick_x1 > x1 + 4:
            x1 = tick_x1
    if len(y_ticks) >= 3:
        tick_ys = [y for _value, y in y_ticks]
        tick_y0 = int(round((min(tick_ys) - crop_rect.y0) * scale))
        tick_y1 = int(round((max(tick_ys) - crop_rect.y0) * scale))
        if tick_y0 < y0 - 4:
            y0 = tick_y0
        if tick_y1 > y1 + 4:
            y1 = tick_y1
    width, height = crop_size
    x0 = min(max(0, x0), width - 2)
    x1 = min(max(x0 + 1, x1), width - 1)
    y0 = min(max(0, y0), height - 2)
    y1 = min(max(y0 + 1, y1), height - 1)
    return x0, y0, x1, y1


def _regular_grid_matches_panel(
    grid_box: tuple[int, int, int, int],
    detected_box: tuple[int, int, int, int],
    loose_box: tuple[int, int, int, int],
    *,
    allow_neighbor_split: bool = False,
) -> bool:
    """Reject partial neighboring grids before using their interval scale."""

    gx0, gy0, gx1, gy1 = grid_box
    dx0, dy0, dx1, dy1 = detected_box
    fx0, fy0, fx1, fy1 = loose_box
    grid_width = max(1, gx1 - gx0)
    grid_height = max(1, gy1 - gy0)
    detected_width = max(1, dx1 - dx0)
    detected_height = max(1, dy1 - dy0)
    loose_width = max(1, fx1 - fx0)
    loose_height = max(1, fy1 - fy0)
    if grid_width >= 0.9 * detected_width and grid_height >= 0.9 * detected_height:
        return True
    return (
        allow_neighbor_split
        and gx0 - fx0 >= 0.2 * loose_width
        and grid_width >= 0.4 * loose_width
        and grid_height >= 0.5 * loose_height
    )


def _overlapping_image_count(page: pymupdf.Page, panel_rect: pymupdf.Rect) -> int:
    """Count raster panels that materially intersect a finder rectangle."""

    count = 0
    placements: set[tuple[float, float, float, float]] = set()
    try:
        images = page.get_images(full=True)
    except Exception:
        return 0
    for image in images:
        try:
            rects = page.get_image_rects(image[0])
        except Exception:
            continue
        for raw_rect in rects:
            rect = pymupdf.Rect(raw_rect)
            placement = tuple(round(float(value), 3) for value in rect)
            if placement in placements:
                continue
            placements.add(placement)
            intersection = rect & panel_rect
            if intersection.is_empty:
                continue
            if intersection.get_area() >= 0.05 * panel_rect.get_area():
                count += 1
    return count


def _containing_chart_image(
    page: pymupdf.Page, panel_rect: pymupdf.Rect
) -> pymupdf.Rect | None:
    """Expand a severely clipped finder box to its containing chart image."""

    panel_area = panel_rect.get_area()
    if panel_area <= 0:
        return None
    candidates: list[pymupdf.Rect] = []
    try:
        images = page.get_images(full=True)
    except Exception:
        return None
    for image in images:
        try:
            rects = page.get_image_rects(image[0])
        except Exception:
            continue
        for raw_rect in rects:
            rect = pymupdf.Rect(raw_rect)
            if rect.get_area() >= 0.5 * page.rect.get_area():
                continue
            intersection = rect & panel_rect
            if intersection.get_area() < 0.8 * panel_area:
                continue
            if rect.get_area() < 2.0 * panel_area:
                continue
            candidates.append(rect)
    return min(candidates, key=lambda rect: rect.get_area()) if candidates else None


def _title_score(panel: ChartPanel) -> float:
    title = re.sub(r"\s+", " ", panel.title.lower())
    penalty = 0.0
    if "test circuit" in title or "waveform definition" in title:
        penalty -= 35.0
    elif "waveform" in title and "versus" not in title and " vs" not in title:
        penalty -= 18.0
    if "behavior" in title:
        penalty -= 12.0
    if "characteristic" in title or " vs" in title or "versus" in title:
        penalty += 4.0
    if panel.diagram >= 950:
        penalty -= 1.0
    return penalty


def _result_sort_key(result: GateChargeResult) -> tuple[int, float, int, int]:
    finite = result.vpl is not None
    plausible = finite and 1.0 <= float(result.vpl) <= 12.0
    return (0 if plausible else 1 if finite else 2, -result.score, result.panel.page, result.panel.diagram)
