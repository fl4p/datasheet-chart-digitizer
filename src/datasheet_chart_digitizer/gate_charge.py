"""Package-owned gate-charge chart digitization API."""

from __future__ import annotations

import math
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pymupdf
import numpy as np
from PIL import Image

from .find_charts import (
    ChartPanel,
    PageText,
    Word,
    process_page_texts,
    process_pdf,
    run_tesseract_page_text,
)
from .region_ocr import ocr_words_in_rect
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
    _detect_aligned_plot_frame,
    _detect_inner_plot_box,
    _detect_regular_grid_box,
    _mask_page_text,
    _pdf_to_px,
    _smooth_polyline,
    _trace_gate_curve,
    _trace_vector_gate_curve,
)


EDGE_TICK_SNAP_MAX_FRACTION = 0.04
TERMINAL_FLAT_MIN_SPAN_FRACTION = 0.08
TERMINAL_FLAT_MAX_Y_RANGE_PX = 2
TERMINAL_FLAT_MIN_ENTRY_RISE_FRACTION = 0.03
TERMINAL_FLAT_MAX_RIGHT_GAP_FRACTION = 0.03
MAX_CURVE_LEFT_GAP_FRACTION = 0.05
PANEL_CELL_ALIGNMENT_TOLERANCE_PT = 2.5
PANEL_CELL_CONTAINMENT_TOLERANCE_PT = 3.0
PANEL_CELL_MAX_AREA_MULTIPLIER = 4.0
DUAL_Y_AXIS_OCR_DPI = 800.0
DUAL_Y_LIGHT_TRACE_THRESHOLD = 190


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
    x_ticks_px: tuple[tuple[float, float], ...] = ()
    y_ticks_px: tuple[tuple[float, float], ...] = ()
    x_tick_unit: str | None = None
    y_tick_unit: str = "V"

    def to_manifest(self) -> dict[str, object]:
        payload = asdict(self)
        payload["panel"] = asdict(self.panel)
        physical_output_available = self.status == "ok"
        payload["physical_output_available"] = physical_output_available
        if not physical_output_available:
            # Keep the candidate on the in-memory result so review overlays can
            # explain a refusal, but never serialize an untrusted scalar or
            # pixel curve that a status-blind consumer could ingest.
            payload["vpl"] = None
            payload["curve_px"] = ()
            payload["vpl_y_px"] = None
        return payload


def digitize_gate_charge(
    pdf_path: str | Path,
    *,
    dpi: int = 220,
    finder_dpi: int = 120,
    _errors: list[dict[str, object]] | None = None,
) -> list[GateChargeResult]:
    """Find and digitize every plausible gate-charge chart in ``pdf_path``."""

    pdf = Path(pdf_path).expanduser().resolve()
    if not pdf.exists():
        raise FileNotFoundError(pdf)

    with tempfile.TemporaryDirectory(prefix="dsdig-gate-charge-") as tmp:
        panels, page_text = _discover_gate_panels(pdf, Path(tmp), finder_dpi)

    results: list[GateChargeResult] = []
    # The OCR retry below (for panels whose native axis is unreadable) must live
    # in its OWN cache and never mutate ``page_text``.  ``page_text`` is the
    # discovery text source (empty for vector-native panels, discovery-OCR for
    # OCR-discovered ones) and is consulted by EVERY panel's primary extraction.
    # Mutating it while iterating let one panel's retry contaminate another
    # panel's native extraction in page order: a page-1 part-summary "Gate
    # charge" spec-table match (diagram 951, axis_assumed) triggered global OCR
    # that then bled the neighbour Fig.8 normalized 0.9-1.1 axis into the correct
    # page-4 Fig.7 native panel, yielding Vpl=1.0 instead of the native 4.96 V
    # (PSMB050N10NS2 / PSMB055N08NS1 / PSMP050N10NS2).
    retry_ocr: dict[int, PageText] = {}
    ocr_attempted = bool(page_text)
    with pymupdf.open(pdf) as doc:
        for panel in panels:
            try:
                result = _digitize_panel(pdf, doc, panel, dpi, page_text.get(panel.page))
                if result is not None and _needs_dual_y_axis_ocr(panel, result):
                    bounded_ocr = _dual_y_axis_ocr_page(pdf, doc, panel, result)
                    if bounded_ocr is not None:
                        ocr_result = _digitize_panel(pdf, doc, panel, dpi, bounded_ocr)
                        if ocr_result is not None and (
                            ocr_result.status == "rejected_non_gate"
                            or (
                                ocr_result.y_tick_count >= 3
                                and _vpl_is_plausible(ocr_result.vpl)
                                and ocr_result.x_tick_unit is not None
                            )
                        ):
                            result = replace(
                                ocr_result,
                                diagnostics=tuple(
                                    dict.fromkeys(
                                        (*ocr_result.diagnostics, "axis_ocr_bounded_dual_y")
                                    )
                                ),
                            )
                if result is not None and result.status in {"axis_assumed", "axis_grid_inferred"}:
                    # Raster charts often retain accurate numeric labels that the
                    # native PDF text layer omits.  Retry a provisional axis once
                    # with bounded OCR before deciding that no scalar is safe.
                    if not retry_ocr and not ocr_attempted:
                        retry_ocr.update(
                            {page.page_num: page for page in run_tesseract_page_text(pdf, dpi=200)}
                        )
                        ocr_attempted = True
                    ocr_text = retry_ocr.get(panel.page)
                    if ocr_text is not None:
                        ocr_result = _digitize_panel(pdf, doc, panel, dpi, ocr_text)
                        if ocr_result is not None and (
                            ocr_result.status == "rejected_non_gate"
                            or (
                                ocr_result.y_tick_count >= 3
                                and ocr_result.vpl is not None
                                and _vpl_is_plausible(ocr_result.vpl)
                            )
                        ):
                            result = ocr_result
                if result is not None:
                    results.append(result)
            except Exception as error:
                if _errors is None:
                    raise
                _errors.append({
                    "kind": "gate_charge",
                    "page": panel.page,
                    "diagram": panel.diagram,
                    "error": str(error),
                })
    return sorted(results, key=_result_sort_key)


def digitize_gate_charge_fail_closed(
    pdf_path: str | Path,
    *,
    dpi: int = 220,
    finder_dpi: int = 120,
) -> tuple[list[GateChargeResult], list[dict[str, object]]]:
    """Digitize owned panels independently and retain explicit refusals."""

    errors: list[dict[str, object]] = []
    results = digitize_gate_charge(
        pdf_path,
        dpi=dpi,
        finder_dpi=finder_dpi,
        _errors=errors,
    )
    return results, errors


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
        self.text_source = page_text.text_source
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


def _x_ticks_with_zero(
    ticks: list[tuple[float, float]], panel_rect: pymupdf.Rect
) -> list[tuple[float, float]]:
    """Extrapolate an omitted zero from a short arithmetic x-axis run."""

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


def _y_ticks_with_zero(
    ticks: list[tuple[float, float]], panel_rect: pymupdf.Rect
) -> list[tuple[float, float]]:
    """Extrapolate one omitted edge zero from a proven arithmetic Y run."""

    ordered = sorted(ticks, key=lambda item: item[1])
    if len(ordered) < 3 or abs(ordered[-1][0]) < 1e-9:
        return ordered
    values = np.asarray([value for value, _y in ordered], dtype=float)
    ys = np.asarray([y for _value, y in ordered], dtype=float)
    value_steps = np.diff(values)
    y_steps = np.diff(ys)
    if np.any(y_steps <= 0):
        return ordered
    value_step = float(np.median(value_steps))
    y_step = float(np.median(y_steps))
    if abs(value_step) < 1e-9 or abs(ordered[-1][0] + value_step) > 0.12 * abs(value_step):
        return ordered
    if np.max(np.abs(value_steps - value_step)) > 0.12 * abs(value_step):
        return ordered
    if np.max(np.abs(y_steps - y_step)) > 0.12 * y_step:
        return ordered
    zero_y = float(ys[-1] + y_step)
    if not ys[-1] < zero_y <= panel_rect.y1 + 12.0:
        return ordered
    return [*ordered, (0.0, zero_y)]


def _detach_transient_panel_artifacts(panel: ChartPanel) -> ChartPanel:
    """Clear finder paths whose temporary output directory will be deleted."""

    return replace(panel, crop_png="")


def _vpl_is_plausible(vpl: float | None) -> bool:
    return vpl is not None and 1.0 <= abs(float(vpl)) <= 12.0


def _depletion_vpl_is_source_plausible(
    vpl: float | None,
    vpl_y_px: float | None,
    curve: list[tuple[int, int]],
    plot_box: tuple[int, int, int, int],
    y_ticks: list[tuple[float, float]],
) -> bool:
    """Recognize a low depletion-mode plateau without widening normal limits."""

    if vpl is None or vpl_y_px is None or len(curve) < 20 or len(y_ticks) < 3:
        return False
    values = np.asarray([value for value, _pixel in y_ticks], dtype=float)
    pixels = np.asarray([pixel for _value, pixel in y_ticks], dtype=float)
    if not (np.min(values) < 0.0 < np.max(values)):
        return False
    slope, offset = np.polyfit(pixels, values, 1)
    first_y = min(curve, key=lambda point: point[0])[1]
    if float(slope * first_y + offset) >= -0.25:
        return False
    if not np.min(values) <= float(vpl) <= np.max(values):
        return False

    nearby_x = sorted(
        x
        for x, y in curve
        if abs(float(y) - float(vpl_y_px)) <= 2.0
    )
    longest_span = 0
    if nearby_x:
        start = previous = nearby_x[0]
        for x in nearby_x[1:]:
            if x - previous > 3:
                longest_span = max(longest_span, previous - start)
                start = x
            previous = x
        longest_span = max(longest_span, previous - start)
    plot_width = max(1, plot_box[2] - plot_box[0])
    return longest_span >= 0.05 * plot_width


def _needs_dual_y_axis_ocr(panel: ChartPanel, result: GateChargeResult) -> bool:
    title = re.sub(r"\s+", " ", panel.title.lower()).strip()
    diagnostics = getattr(result, "diagnostics", ())
    axis_problem = result.status in {"axis_assumed", "axis_grid_inferred"} or any(
        item in diagnostics
        for item in ("axis_assumed_0_10", "axis_inferred_from_regular_grid", "gate_charge_unit_unresolved")
    )
    return panel.diagram == 810 and title == "dynamic input/output characteristics" and axis_problem


def _dual_y_axis_ocr_page(
    pdf: Path,
    doc: pymupdf.Document,
    panel: ChartPanel,
    result: GateChargeResult,
) -> PageText | None:
    """OCR only the owned right-VGS and bottom-Qg label bands."""

    page = doc[panel.page - 1]
    crop = pymupdf.Rect(result.crop_box_pt)
    scale = result.dpi / 72.0
    px0, py0, px1, py1 = result.plot_box_px
    plot = pymupdf.Rect(
        crop.x0 + px0 / scale,
        crop.y0 + py0 / scale,
        crop.x0 + px1 / scale,
        crop.y0 + py1 / scale,
    )
    owner = pymupdf.Rect(panel.bbox_pt)
    right_band = pymupdf.Rect(
        plot.x1 + 0.5,
        max(page.rect.y0, owner.y0 - 8.0),
        min(page.rect.x1, plot.x1 + 22.0),
        min(page.rect.y1, owner.y1 + 6.0),
    )
    bottom_band = pymupdf.Rect(
        max(page.rect.x0, plot.x0 - 18.0),
        max(page.rect.y0, plot.y1 - 3.0),
        min(page.rect.x1, plot.x1 + 18.0),
        min(page.rect.y1, owner.y1),
    )
    try:
        right_sparse = ocr_words_in_rect(
            pdf, panel.page, right_band, dpi=DUAL_Y_AXIS_OCR_DPI, psm=11
        )
        right_block = ocr_words_in_rect(
            pdf, panel.page, right_band, dpi=DUAL_Y_AXIS_OCR_DPI, psm=6
        )
        signed_sparse = sum(
            bool(re.fullmatch(r"[-−–—]\d+(?:\.\d+)?", word[4]))
            for word in right_sparse
        )
        raw_words = [
            *(right_sparse if signed_sparse >= 3 else right_block),
            *ocr_words_in_rect(
                pdf, panel.page, bottom_band, dpi=DUAL_Y_AXIS_OCR_DPI, psm=6
            ),
        ]
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
    words = [Word(text, x0, y0, x1, y1) for x0, y0, x1, y1, text in raw_words]
    compact = re.sub(r"[^a-z0-9]", "", " ".join(word.text for word in words).lower())
    if "nc" not in compact:
        return None
    return PageText(
        panel.page,
        float(page.rect.width),
        float(page.rect.height),
        words,
        "tesseract_fallback",
    )


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
    return next(
        (result for result in results if result.status == "ok" and result.vpl is not None),
        None,
    )


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
    finder_rect = pymupdf.Rect(panel.bbox_pt)
    expanded_image_rect = _containing_chart_image(page, panel_rect)
    if expanded_image_rect is not None:
        panel_rect = expanded_image_rect
    panel_axis = _best_y_axis_for_panel(text_page, panel_rect)
    panel_y_ticks, axis_x = panel_axis if panel_axis is not None else ([], None)
    panel_y_ticks = _y_ticks_with_zero(panel_y_ticks, panel_rect)
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
        # Native text and OCR both omit an edge label on some otherwise regular
        # gate-charge axes.  Recover the evidenced zero before the plot box is
        # bound, or the initial rise and Miller plateau can be cropped away.
        panel_x_ticks = _x_ticks_with_zero(panel_x_ticks, panel_rect)
        tick_xs = sorted(x for _value, x in panel_x_ticks)
        spacing = float(np.median(np.diff(tick_xs)))
        pad_x = max(6.0, 0.45 * spacing)
        if axis_x is None:
            panel_rect.x0 = max(page.rect.x0, tick_xs[0] - pad_x)
        # Some charts replace the final numeric tick with the x-axis unit.
        # When the finder panel contains room for another evidenced arithmetic
        # interval, retain that one interval so the terminal rise is not cut at
        # the last printed number. Do not extend when the final label is already
        # at the panel edge.
        right_room = panel_rect.x1 - tick_xs[-1]
        right_pad = 1.15 * spacing if right_room >= 0.75 * spacing else pad_x
        panel_rect.x1 = min(page.rect.x1, tick_xs[-1] + right_pad)

    crop_rect = (panel_rect + (-46, -38, 42, 52)) & page.rect
    crop_rect = _clip_context_at_foreign_text(
        page,
        panel_rect,
        crop_rect,
        panel.diagram,
        caption_owner_rect=finder_rect,
    )
    panel_cell = _enclosing_panel_cell(page, panel_rect)
    if panel_cell is not None:
        panel_rect &= panel_cell
        crop_rect &= panel_cell
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
        detector_used_fallback=detected_plot_box == loose_plot_box,
    )
    aligned_frame = _detect_aligned_plot_frame(
        np.asarray(crop.convert("L")), loose_plot_box
    )
    if aligned_frame is not None and _aligned_frame_improves_axis_binding(
        aligned_frame,
        plot_box,
        crop_rect,
        scale,
        panel_x_ticks,
        panel_y_ticks,
    ):
        plot_box = aligned_frame
    plot_rect = pymupdf.Rect(
        crop_rect.x0 + plot_box[0] / scale,
        crop_rect.y0 + plot_box[1] / scale,
        crop_rect.x0 + plot_box[2] / scale,
        crop_rect.y0 + plot_box[3] / scale,
    )
    tight_context = _text_near_rect(text_page, plot_rect, pad=12.0)
    broad_context = _text_near_rect(text_page, plot_rect, pad=60.0)
    local_non_gate_reason = _strong_local_non_gate_reason(tight_context)
    if local_non_gate_reason is not None:
        return _rejected_non_gate_result(
            pdf, panel, dpi, crop_rect, plot_box, panel_y_ticks, local_non_gate_reason
        )
    non_gate_reason = _non_gate_plot_reason(tight_context, broad_context)
    mixed_gate_context = (
        non_gate_reason is not None
        and non_gate_reason != "spec_table"
        and _has_gate_charge_evidence(broad_context)
    )
    if non_gate_reason is not None and not mixed_gate_context:
        return _rejected_non_gate_result(
            pdf, panel, dpi, crop_rect, plot_box, panel_y_ticks, non_gate_reason
        )
    local_y_ticks = _local_y_ticks_for_plot(text_page, crop_rect, scale, plot_box)
    if len(local_y_ticks) < 2:
        local_y_ticks = panel_y_ticks
    local_y_ticks = _y_ticks_with_zero(local_y_ticks, panel_rect)
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

    bounded_dual_y_trace = _uses_bounded_dual_y_trace(panel, page_text)
    raster_candidates = [
        _smooth_polyline(_trace_gate_curve(trace_crop, plot_box))
        for trace_crop in trace_crops
    ]
    if bounded_dual_y_trace:
        # Toshiba's outlined VGS stroke can be lighter than its VDS family.
        # Add a panel-local light-ink candidate; the normal raster path and its
        # threshold remain byte-identical for every other chart.
        raster_candidates.extend(
            _smooth_polyline(
                _trace_gate_curve(
                    trace_crop,
                    plot_box,
                    gray_threshold=DUAL_Y_LIGHT_TRACE_THRESHOLD,
                )
            )
            for trace_crop in trace_crops
        )
        raster_curve = _select_dual_y_raster_curve(
            raster_candidates, plot_box, crop.height, crop.width
        )
    else:
        raster_curve = _select_raster_curve(
            raster_candidates, crop.height, crop.width
        )
    vector_curve = _smooth_polyline(
        _trace_vector_gate_curve(page, crop_rect, scale, plot_box), stride=1
    )
    choices = [
        (
            raster_curve,
            "raster",
            _curve_score(raster_curve, crop.height, crop.width),
            _score_curve_in_plot(raster_curve, plot_box),
        ),
        (
            vector_curve,
            "vector",
            _curve_score(vector_curve, crop.height, crop.width),
            _score_curve_in_plot(vector_curve, plot_box),
        ),
    ]
    # Preserve the established raster-vs-vector selection ordering, but judge
    # the selected curve's confidence against the calibrated plot rather than
    # unrelated context padding around it.
    curve, trace_source, _selection_score, trace_score = max(
        choices, key=lambda choice: choice[2]
    )
    if (
        trace_source == "raster"
        and bounded_dual_y_trace
    ):
        curve = _repair_narrow_plateau_branch_excursion(curve, plot_box)
        curve = _trim_dual_y_terminal_branch_switch(curve, plot_box)
        curve = _trim_dual_y_terminal_grid_capture(curve, plot_box)
    curve = _trim_terminal_flat_grid_capture(curve, plot_box)
    vpl, vpl_y_px = _estimate_vpl_from_curve(
        curve, panel, crop_rect, scale, plot_box, local_y_ticks
    )
    if vpl is not None and not math.isfinite(vpl):
        vpl = None

    diagnostics: list[str] = []
    vpl_expected_for_source = _vpl_is_plausible(vpl) or _depletion_vpl_is_source_plausible(
        vpl, vpl_y_px, curve, plot_box, local_y_ticks
    )
    low_trace_confidence = trace_score <= -1e8
    missing_initial_ramp = _curve_missing_initial_ramp(curve, plot_box)
    missing_axis_origin = bounded_dual_y_trace and not _curve_starts_at_axis_origin(
        curve, plot_box
    )
    if len(curve) < 20:
        diagnostics.append("insufficient_curve_points")
    elif low_trace_confidence:
        diagnostics.append("low_trace_confidence")
    if missing_initial_ramp:
        diagnostics.append("curve_missing_initial_ramp")
    if missing_axis_origin:
        diagnostics.append("curve_missing_axis_origin")
    if axis_assumed:
        diagnostics.append("axis_assumed_0_10")
    elif axis_grid_inferred:
        diagnostics.append("axis_inferred_from_regular_grid")
    if vpl is None:
        diagnostics.append("vpl_unresolved")
    elif not vpl_expected_for_source:
        diagnostics.append("vpl_outside_expected_range")

    score = trace_score + min(4.0, 0.45 * measured_y_tick_count)
    score += _title_score(panel)
    if vpl is None:
        score -= 30.0
    elif not _vpl_is_plausible(vpl):
        score -= 12.0

    if vpl is None or len(curve) < 20:
        status = "unresolved"
    elif axis_assumed:
        status = "axis_assumed"
    elif axis_grid_inferred:
        status = "axis_grid_inferred"
    elif low_trace_confidence or missing_initial_ramp or missing_axis_origin:
        status = "low_confidence"
    else:
        status = "ok"
    x_tick_unit = _gate_charge_unit(broad_context)
    if x_tick_unit is None:
        diagnostics.append("gate_charge_unit_unresolved")
        status = "unresolved"
    x_ticks_px = _snap_tick_coordinates_to_plot(
        tuple(
        (float(value), float((x - crop_rect.x0) * scale)) for value, x in panel_x_ticks
        ),
        plot_box[0],
        plot_box[2],
    )
    y_ticks_px = _snap_tick_coordinates_to_plot(
        tuple(
        (float(value), float((y - crop_rect.y0) * scale)) for value, y in local_y_ticks
        ),
        plot_box[1],
        plot_box[3],
    )
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
        x_ticks_px=x_ticks_px,
        y_ticks_px=y_ticks_px,
        x_tick_unit=x_tick_unit,
    )
    if non_gate_reason is not None and mixed_gate_context and (
        low_trace_confidence or not _vpl_is_plausible(vpl)
    ):
        return replace(
            result,
            vpl=None,
            status="unresolved",
            score=round(score - 30.0, 6),
            vpl_y_px=None,
            diagnostics=tuple(
                dict.fromkeys(
                    (*diagnostics, f"ambiguous_neighbor:{non_gate_reason}", "vpl_unresolved")
                )
            ),
        )
    return result


def _strong_local_non_gate_reason(tight_context: str) -> str | None:
    """Return a terminal local plot identity that a remote gate caption cannot override."""

    tight = re.sub(r"\s+", " ", tight_context.lower())
    if "power dissipation" in tight and (
        "case temperature" in tight or "safe operating area" in tight
    ):
        return "power_dissipation"
    if "safe operating area" in tight and (
        "drain current" in tight or "drain-source voltage" in tight
    ):
        return "safe_operating_area"
    return None


def _rejected_non_gate_result(
    pdf: Path,
    panel: ChartPanel,
    dpi: int,
    crop_rect: pymupdf.Rect,
    plot_box: tuple[int, int, int, int],
    panel_y_ticks: list[tuple[float, float]],
    reason: str,
) -> GateChargeResult:
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
        diagnostics=(f"non_gate_plot:{reason}",),
    )


def _gate_charge_unit(context: str) -> str | None:
    """Return the locally evidenced charge unit without assuming nC."""

    normalized = context.lower().replace("μ", "u").replace("µ", "u")
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    if "nc" in compact or "nanocoulomb" in compact:
        return "nC"
    if "uc" in compact:
        return "uC"
    return None


def _score_curve_in_plot(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> float:
    """Score crop-coordinate points against the calibrated plot, not context padding."""

    x0, y0, x1, y1 = plot_box
    local = [(x - x0, y - y0) for x, y in curve]
    return _curve_score(local, max(1, y1 - y0 + 1), max(1, x1 - x0 + 1))


def _curve_missing_initial_ramp(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> bool:
    """Refuse a gate-charge trace whose first source point starts too far inboard."""

    if not curve:
        return True
    x0, _y0, x1, _y1 = plot_box
    width = max(1, x1 - x0)
    return curve[0][0] - x0 > MAX_CURVE_LEFT_GAP_FRACTION * width


def _trim_terminal_flat_grid_capture(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> list[tuple[int, int]]:
    """Stop where a rising curve starts riding a horizontal gridline to the frame."""

    if len(curve) < 8:
        return curve
    x0, y0, x1, y1 = plot_box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    if x1 - curve[-1][0] > TERMINAL_FLAT_MAX_RIGHT_GAP_FRACTION * width:
        return curve

    tail_y = curve[-1][1]
    start = len(curve) - 1
    while start > 0 and abs(curve[start - 1][1] - tail_y) <= TERMINAL_FLAT_MAX_Y_RANGE_PX:
        start -= 1
    if start == 0:
        return curve
    flat_span = curve[-1][0] - curve[start][0]
    if flat_span <= TERMINAL_FLAT_MIN_SPAN_FRACTION * width:
        return curve
    entry_rise = curve[start - 1][1] - curve[start][1]
    if entry_rise < TERMINAL_FLAT_MIN_ENTRY_RISE_FRACTION * height:
        return curve
    return curve[: start + 1]


def _repair_narrow_plateau_branch_excursion(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> list[tuple[int, int]]:
    """Recover one source-evidenced VGS plateau through VDS crossings.

    Toshiba dual-Y raster plots draw falling VDS curves through the VGS Miller
    plateau.  A local endpoint search can choose two points on the same wrong
    VDS stroke and manufacture a second plateau level.  Instead, prove the
    printed plateau from the flattest source-supported window, extend only
    through nearby same-level samples, and repair isolated approach/exit
    crossings by interpolation between source points on both sides.  This is
    bounded to the pre-terminal half and never touches the separately guarded
    terminal bundle.
    """

    ordered = sorted(curve)
    if len(ordered) < 12:
        return ordered
    x0, y0, x1, y1 = plot_box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    repair_limit_x = x0 + 0.55 * width
    plateau_search_start = x0 + 0.08 * width
    search_indexes = [
        index
        for index, (x, _y) in enumerate(ordered)
        if plateau_search_start <= x <= repair_limit_x
    ]
    if len(search_indexes) < 7:
        return ordered

    x_steps = np.diff([ordered[index][0] for index in search_indexes])
    stride = max(1.0, float(np.median(x_steps)))
    window_points = max(7, int(round(0.07 * width / stride)))
    if len(search_indexes) < window_points:
        return ordered

    best: tuple[tuple[float, float, float, int], int, int, int] | None = None
    first = search_indexes[0]
    last = search_indexes[-1]
    for start in range(first, last - window_points + 2):
        stop = start + window_points - 1
        window = ordered[start : stop + 1]
        if window[-1][0] > repair_limit_x:
            break
        values = np.array([y for _x, y in window], dtype=float)
        median = float(np.median(values))
        score = (
            float(np.median(np.abs(np.diff(values)))),
            float(np.median(np.abs(values - median))),
            float(np.ptp(values)),
            start,
        )
        candidate = (score, start, stop, int(round(median)))
        if best is None or candidate[0] < best[0]:
            best = candidate
    if best is None or best[0][0] > 1.5:
        # A continuously rising source has no Miller plateau to repair.
        return ordered

    _score, window_start, window_stop, plateau_y = best
    level_tolerance = 2
    near_level = [
        index
        for index in range(first, last + 1)
        if abs(ordered[index][1] - plateau_y) <= level_tolerance
    ]
    groups: list[list[int]] = []
    for index in near_level:
        # Up to four stride-sampled columns can be swallowed by one thick VDS
        # crossing; keep the same-level source samples on both sides in one
        # plateau group without extending the boundary to unrelated levels.
        if groups and index - groups[-1][-1] <= 5:
            groups[-1].append(index)
        else:
            groups.append([index])
    plateau_group = next(
        (
            group
            for group in groups
            if any(window_start <= index <= window_stop for index in group)
        ),
        None,
    )
    if plateau_group is None:
        return ordered

    plateau_start = plateau_group[0]
    plateau_stop = plateau_group[-1]
    repaired = list(ordered)
    for index in range(plateau_start, plateau_stop + 1):
        repaired[index] = (repaired[index][0], plateau_y)

    minimum_excursion = max(4.0, 0.009 * height)
    approach = range(2, max(2, plateau_start - 1))
    exit_segment = range(
        min(len(repaired) - 2, plateau_stop + 2), len(repaired) - 2
    )
    for _iteration in range(3):
        changed = False
        for index in (*approach, *exit_segment):
            x, y = repaired[index]
            if x > repair_limit_x:
                continue
            xa, ya = repaired[index - 2]
            xb, yb = repaired[index + 2]
            if xb <= xa:
                continue
            expected_y = ya + (x - xa) * (yb - ya) / (xb - xa)
            if abs(y - expected_y) < minimum_excursion:
                continue
            repaired[index] = (x, int(round(expected_y)))
            changed = True
        if not changed:
            break
    return repaired


def _uses_bounded_dual_y_trace(
    panel: ChartPanel, page_text: PageText | None
) -> bool:
    """Limit the light-stroke trace path to the owned Toshiba OCR retry."""

    return (
        page_text is not None
        and page_text.text_source == "tesseract_fallback"
        and panel.diagram == 810
        and re.sub(r"\s+", " ", panel.title.lower()).strip()
        == "dynamic input/output characteristics"
    )


def _trim_dual_y_terminal_branch_switch(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> list[tuple[int, int]]:
    """Stop at the first finished VGS branch instead of joining its neighbors.

    The Toshiba VGS bundle has several rising strokes that end separately at
    the same upper voltage.  Raster continuity can jump downward to a later
    branch after the first one ends.  A monotonic fit is unsafe here because it
    turns that source discontinuity into a horizontal, source-absent segment.
    """

    if len(curve) < 8:
        return curve
    x0, y0, x1, y1 = plot_box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    search_start = x0 + 0.55 * width
    reverse_jump = max(5.0, 0.012 * height)
    required_future_progress = max(6.0, 0.02 * height)
    for index in range(1, len(curve)):
        x, y = curve[index]
        previous_y = curve[index - 1][1]
        if x < search_start or y - previous_y < reverse_jump:
            continue
        future_min_y = min(point_y for _point_x, point_y in curve[index:])
        if previous_y - future_min_y < required_future_progress:
            return curve[:index]
    return curve


def _trim_dual_y_terminal_grid_capture(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> list[tuple[int, int]]:
    """Stop a Toshiba VGS trace where it reaches and then rides a gridline."""

    if len(curve) < 8:
        return curve
    x0, _y0, x1, _y1 = plot_box
    width = max(1, x1 - x0)
    if x1 - curve[-1][0] > TERMINAL_FLAT_MAX_RIGHT_GAP_FRACTION * width:
        return curve
    tail_y = curve[-1][1]
    start = len(curve) - 1
    while start > 0 and abs(curve[start - 1][1] - tail_y) <= 1:
        start -= 1
    if curve[-1][0] - curve[start][0] < 0.06 * width:
        return curve
    return curve[: start + 1]


def _curve_starts_at_axis_origin(
    curve: list[tuple[int, int]], plot_box: tuple[int, int, int, int]
) -> bool:
    """Require Qg=0 to meet the right-axis VGS=0 origin."""

    if not curve:
        return False
    x0, _y0, x1, y1 = plot_box
    width = max(1, x1 - x0)
    height = max(1, y1 - _y0)
    start_x, start_y = curve[0]
    return (
        start_x - x0 <= MAX_CURVE_LEFT_GAP_FRACTION * width
        and y1 - start_y <= 0.04 * height
    )


def _select_dual_y_raster_curve(
    curves: list[list[tuple[int, int]]],
    plot_box: tuple[int, int, int, int],
    height: int,
    width: int,
) -> list[tuple[int, int]]:
    """Prefer the source-owned VGS branch proven to start at Qg=VGS=0."""

    origin_curves = [
        curve for curve in curves if _curve_starts_at_axis_origin(curve, plot_box)
    ]
    return _select_raster_curve(origin_curves or curves, height, width)


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
    *,
    detector_used_fallback: bool = False,
) -> tuple[int, int, int, int]:
    """Bind detected plot edges to calibrated tick spans when available."""

    x0, y0, x1, y1 = detected
    if len(x_ticks) >= 3:
        tick_xs = [x for _value, x in x_ticks]
        tick_x0 = int(round((min(tick_xs) - crop_rect.x0) * scale))
        tick_x1 = int(round((max(tick_xs) - crop_rect.x0) * scale))
        if detector_used_fallback:
            x0, x1 = tick_x0, tick_x1
        elif tick_x0 < x0 - 4:
            x0 = tick_x0
        if not detector_used_fallback and tick_x1 > x1 + 4:
            x1 = tick_x1
    if len(y_ticks) >= 3:
        tick_ys = [y for _value, y in y_ticks]
        tick_y0 = int(round((min(tick_ys) - crop_rect.y0) * scale))
        tick_y1 = int(round((max(tick_ys) - crop_rect.y0) * scale))
        if detector_used_fallback:
            y0, y1 = tick_y0, tick_y1
        elif tick_y0 < y0 - 4:
            y0 = tick_y0
        if not detector_used_fallback and tick_y1 > y1 + 4:
            y1 = tick_y1
    width, height = crop_size
    x0 = min(max(0, x0), width - 2)
    x1 = min(max(x0 + 1, x1), width - 1)
    y0 = min(max(0, y0), height - 2)
    y1 = min(max(y0 + 1, y1), height - 1)
    return x0, y0, x1, y1


def _aligned_frame_improves_axis_binding(
    frame: tuple[int, int, int, int],
    current: tuple[int, int, int, int],
    crop_rect: pymupdf.Rect,
    scale: float,
    x_ticks: list[tuple[float, float]],
    y_ticks: list[tuple[float, float]],
) -> bool:
    """Accept a closed frame only when both calibrated axes corroborate it."""

    def axis_edge_errors(
        start: int,
        end: int,
        coordinates: list[float],
        origin: float,
    ) -> tuple[float, float] | None:
        ordered = sorted((coordinate - origin) * scale for coordinate in coordinates)
        if len(ordered) < 2:
            return None
        spacing = float(np.median(np.diff(ordered)))
        if not math.isfinite(spacing) or spacing <= 1.0:
            return None
        # A real frame may sit one unlabeled interval beyond an extreme tick.
        # Compare against both evidenced possibilities, but never against two
        # or more guessed intervals.
        start_error = min(abs(start - ordered[0]), abs(start - (ordered[0] - spacing))) / spacing
        end_error = min(abs(end - ordered[-1]), abs(end - (ordered[-1] + spacing))) / spacing
        return start_error, end_error

    def fit_errors(box: tuple[int, int, int, int]) -> tuple[float, ...] | None:
        x_errors = axis_edge_errors(
            box[0], box[2], [x for _value, x in x_ticks], crop_rect.x0
        )
        y_errors = axis_edge_errors(
            box[1], box[3], [y for _value, y in y_ticks], crop_rect.y0
        )
        if x_errors is None or y_errors is None:
            return None
        return (*x_errors, *y_errors)

    frame_errors = fit_errors(frame)
    current_errors = fit_errors(current)
    if frame_errors is None or current_errors is None:
        return False
    if max(frame_errors) > 0.18:
        return False
    if sum(frame_errors) + 0.10 < sum(current_errors):
        return True

    # Axis geometry alone cannot choose between a frame at the final labeled
    # tick and one exactly one unlabeled interval beyond it: both have zero edge
    # error by construction.  In that tie, accept direct closed-frame evidence
    # only when it CONSTRICTS the current loose box.  This closes side-by-side
    # neighbor capture (2N7002K/AGM056N10C) and dead-whitespace overshoot
    # (FDB120N10) without relaxing the existing evidence requirement for any
    # outward expansion.  Genuine unlabeled terminal intervals still use the
    # original strict-improvement path above (DI110N15PQ/HY1001D/IXFB/IXTH).
    edge_tolerance_px = 2.0
    frame_is_contained = (
        frame[0] >= current[0] - edge_tolerance_px
        and frame[1] >= current[1] - edge_tolerance_px
        and frame[2] <= current[2] + edge_tolerance_px
        and frame[3] <= current[3] + edge_tolerance_px
    )
    frame_constricts = (
        frame[0] > current[0] + edge_tolerance_px
        or frame[1] > current[1] + edge_tolerance_px
        or frame[2] < current[2] - edge_tolerance_px
        or frame[3] < current[3] - edge_tolerance_px
    )
    return (
        frame_is_contained
        and frame_constricts
        and sum(frame_errors) <= sum(current_errors) + 0.05
    )


def _snap_tick_coordinates_to_plot(
    ticks: tuple[tuple[float, float], ...], start: int, end: int
) -> tuple[tuple[float, float], ...]:
    """Map a full linear tick run onto evidenced plot edges."""

    if len(ticks) < 2 or end <= start:
        return ticks
    ordered = sorted(ticks, key=lambda item: item[1])
    tolerance = EDGE_TICK_SNAP_MAX_FRACTION * (end - start)
    start_matches = abs(ordered[0][1] - start) <= tolerance
    end_matches = abs(ordered[-1][1] - end) <= tolerance
    if not start_matches and not end_matches:
        return ticks
    if not (start_matches and end_matches):
        snapped_edges = dict(ordered)
        if start_matches:
            snapped_edges[ordered[0][0]] = float(start)
        if end_matches:
            snapped_edges[ordered[-1][0]] = float(end)
        return tuple((value, snapped_edges[value]) for value, _coordinate in ticks)
    first_value, last_value = ordered[0][0], ordered[-1][0]
    if first_value == last_value:
        return ticks
    value_steps = np.diff([value for value, _coordinate in ordered])
    if not (np.all(value_steps > 0) or np.all(value_steps < 0)):
        return ticks
    return tuple(
        (
            value,
            start + (value - first_value) / (last_value - first_value) * (end - start),
        )
        for value, _coordinate in ticks
    )


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


def _enclosing_panel_cell(
    page: pymupdf.Page, panel_rect: pymupdf.Rect
) -> pymupdf.Rect | None:
    """Return a positively closed table cell around one chart panel.

    Some datasheets place two plots in bordered cells.  The gate digitizer's
    review-context padding must stop at those source-owned rails: otherwise it
    can repaint a neighbouring axis or a title from the row above even though
    the selected gate curve is correct.  A pair of matching horizontals is not
    enough; require both full-height side rails to close the same rectangle.
    """

    horizontals: list[tuple[float, float, float]] = []
    verticals: list[tuple[float, float, float]] = []
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            if item[0] != "l":
                continue
            start, end = item[1], item[2]
            x0, x1 = sorted((float(start.x), float(end.x)))
            y0, y1 = sorted((float(start.y), float(end.y)))
            if y1 - y0 <= 0.5 and x1 - x0 >= 0.70 * panel_rect.width:
                horizontals.append((0.5 * (y0 + y1), x0, x1))
            if x1 - x0 <= 0.5 and y1 - y0 >= 0.70 * panel_rect.height:
                verticals.append((0.5 * (x0 + x1), y0, y1))

    alignment = PANEL_CELL_ALIGNMENT_TOLERANCE_PT
    containment = PANEL_CELL_CONTAINMENT_TOLERANCE_PT
    candidates: list[pymupdf.Rect] = []
    for top, top_x0, top_x1 in horizontals:
        for bottom, bottom_x0, bottom_x1 in horizontals:
            if bottom <= top:
                continue
            if abs(top_x0 - bottom_x0) > alignment:
                continue
            if abs(top_x1 - bottom_x1) > alignment:
                continue
            x0 = 0.5 * (top_x0 + bottom_x0)
            x1 = 0.5 * (top_x1 + bottom_x1)
            cell = pymupdf.Rect(x0, top, x1, bottom)
            if cell.get_area() > PANEL_CELL_MAX_AREA_MULTIPLIER * panel_rect.get_area():
                continue
            if (
                cell.x0 > panel_rect.x0 + containment
                or cell.y0 > panel_rect.y0 + containment
                or cell.x1 < panel_rect.x1 - containment
                or cell.y1 < panel_rect.y1 - containment
            ):
                continue
            sides = [
                x
                for x, y0, y1 in verticals
                if y0 <= top + alignment and y1 >= bottom - alignment
            ]
            if not any(abs(x - cell.x0) <= alignment for x in sides):
                continue
            if not any(abs(x - cell.x1) <= alignment for x in sides):
                continue
            candidates.append(cell)
    return min(candidates, key=lambda rect: rect.get_area()) if candidates else None


def _clip_context_at_foreign_text(
    page: pymupdf.Page,
    panel_rect: pymupdf.Rect,
    crop_rect: pymupdf.Rect,
    diagram: int,
    *,
    caption_owner_rect: pymupdf.Rect | None = None,
) -> pymupdf.Rect:
    """Stop gate review padding at positively identified neighbour content."""

    words = list(page.get_text("words"))
    lines: dict[tuple[int, int], list[tuple[object, ...]]] = {}
    for word in words:
        if len(word) >= 7:
            lines.setdefault((int(word[5]), int(word[6])), []).append(word)
    clipped = pymupdf.Rect(crop_rect)
    caption_rect = caption_owner_rect or panel_rect
    for line_words in lines.values():
        ordered = sorted(line_words, key=lambda word: float(word[0]))
        text = " ".join(str(word[4]) for word in ordered)
        match = re.search(r"\bFigure\s+(\d+)\b", text, re.I)
        if match is None or int(match.group(1)) == diagram:
            continue
        x0 = min(float(word[0]) for word in ordered)
        y1 = max(float(word[3]) for word in ordered)
        x1 = max(float(word[2]) for word in ordered)
        overlap = max(0.0, min(x1, caption_rect.x1) - max(x0, caption_rect.x0))
        if overlap < 0.20 * min(x1 - x0, caption_rect.width):
            continue
        if y1 <= caption_rect.y0 + 2.0 and caption_rect.y0 - y1 <= 0.50 * caption_rect.height:
            clipped.y0 = max(clipped.y0, y1 + 1.0)

    foreign_axis_words = [
        word
        for word in words
        if str(word[4]).lower() in {"capacitance", "breakdown", "normalized"}
        and float(word[1]) <= panel_rect.y1
        and float(word[3]) >= panel_rect.y0
    ]
    right = [float(word[0]) for word in foreign_axis_words if float(word[0]) > panel_rect.x1]
    left = [float(word[2]) for word in foreign_axis_words if float(word[2]) < panel_rect.x0]
    if right:
        clipped.x1 = min(clipped.x1, 0.5 * (panel_rect.x1 + min(right)))
    if left:
        clipped.x0 = max(clipped.x0, 0.5 * (panel_rect.x0 + max(left)))
    return clipped


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
    plausible = finite and _vpl_is_plausible(result.vpl)
    return (0 if plausible else 1 if finite else 2, -result.score, result.panel.page, result.panel.diagram)
