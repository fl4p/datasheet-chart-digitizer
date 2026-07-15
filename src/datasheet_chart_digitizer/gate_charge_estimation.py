from __future__ import annotations

import re

import numpy as np
import pymupdf

def _v_from_y_pixel(chart, rect: pymupdf.Rect, scale: float, y_px: float) -> float | None:
    ticks = getattr(chart, "y_ticks", None) or []
    if len(ticks) < 2:
        return None
    vals = np.array([float(v) for v, _ in ticks], dtype=float)
    ys_pdf = np.array([float(y) for _, y in ticks], dtype=float)
    if np.nanmax(vals) <= 0:
        vals = -vals
    m, b = np.polyfit(ys_pdf, vals, 1)
    y_pdf = rect.y0 + float(y_px) / scale
    return float(m * y_pdf + b)


def _parse_numeric_label(text: str) -> float | None:
    text = text.strip().replace("−", "-").replace("–", "-")
    text = text.replace("O", "0").replace("o", "0")
    if re.search(r"[A-Za-z]", text):
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _local_y_ticks_for_plot(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    scale: float,
    plot_box: tuple[int, int, int, int],
) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = plot_box
    px0 = rect.x0 + x0 / scale
    py0 = rect.y0 + y0 / scale
    px1 = rect.x0 + x1 / scale
    py1 = rect.y0 + y1 / scale
    height = max(1.0, py1 - py0)
    candidates: dict[str, list[tuple[float, float, float]]] = {"left": [], "right": []}
    try:
        words = page.get_text("words")
    except Exception:
        return candidates
    for word in words:
        wx0, wy0, wx1, wy1 = [float(v) for v in word[:4]]
        text = str(word[4])
        cx = 0.5 * (wx0 + wx1)
        cy = 0.5 * (wy0 + wy1)
        if cy < py0 - 0.04 * height or cy > py1 + 0.04 * height:
            continue
        left_band = px0 - 82 <= cx <= px0 - 1
        right_band = px1 + 1 <= cx <= px1 + 82
        if not (left_band or right_band):
            continue
        value = _parse_numeric_label(text)
        if value is None:
            continue
        if value < -20 or value > 30:
            continue
        candidates["left" if left_band else "right"].append((value, cy, cx))

    axes = [
        axis
        for side in candidates.values()
        for axis in _cluster_y_tick_columns(side)
    ]
    axes = [axis for axis in axes if len(axis) >= 2]
    if not axes:
        return []
    return max(axes, key=len)


def _cluster_y_tick_columns(
    candidates: list[tuple[float, float, float]],
) -> list[list[tuple[float, float]]]:
    """Split numeric labels into distinct columns and stacked axis runs."""

    columns: list[list[tuple[float, float, float]]] = []
    for item in sorted(candidates, key=lambda candidate: candidate[2]):
        if columns and item[2] - columns[-1][-1][2] <= 6.0:
            columns[-1].append(item)
        else:
            columns.append([item])
    axes: list[list[tuple[float, float]]] = []
    for column in columns:
        rows = sorted(column, key=lambda row: row[1])
        gaps = np.diff([row[1] for row in rows])
        typical_gap = float(np.median(gaps)) if len(gaps) else 0.0
        split_gap = max(48.0, 1.6 * typical_gap)
        start = 0
        for stop in range(1, len(rows) + 1):
            at_end = stop == len(rows)
            separated = not at_end and rows[stop][1] - rows[stop - 1][1] > split_gap
            if not (at_end or separated):
                continue
            axis = _normalize_y_tick_candidates(
                [(value, y) for value, y, _x in rows[start:stop]]
            )
            if axis:
                axes.append(axis)
            start = stop
    return axes


def _best_y_ticks_for_panel(
    page: pymupdf.Page,
    panel_rect: pymupdf.Rect,
) -> list[tuple[float, float]]:
    """Find the most plausible numeric y-axis column near a chart panel."""

    axis = _best_y_axis_for_panel(page, panel_rect)
    return axis[0] if axis is not None else []


def _best_y_axis_for_panel(
    page: pymupdf.Page,
    panel_rect: pymupdf.Rect,
) -> tuple[list[tuple[float, float]], float] | None:
    """Return the best nearby y-axis as ``(ticks, column_x)``."""

    x_axis = _best_x_axis_for_panel(page, panel_rect)
    x_axis_edges: tuple[float, float] | None = None
    if x_axis is not None:
        x_tick_positions = [x for _value, x in x_axis[0]]
        x_axis_edges = min(x_tick_positions), max(x_tick_positions)
    candidates: list[tuple[float, float, float]] = []
    try:
        words = page.get_text("words")
    except Exception:
        return None
    for word in words:
        wx0, wy0, wx1, wy1 = [float(value) for value in word[:4]]
        value = _parse_numeric_label(str(word[4]))
        if value is None or value < -20 or value > 30:
            continue
        candidates.append((value, 0.5 * (wy0 + wy1), 0.5 * (wx0 + wx1)))

    best: tuple[float, list[tuple[float, float]], float] | None = None
    for ticks in _cluster_y_tick_columns(candidates):
        if len(ticks) < 2:
            continue
        ys = [y for _value, y in ticks]
        y_span = max(ys) - min(ys)
        if y_span < 20.0:
            continue
        matching = [
            row
            for row in candidates
            if any(abs(row[0] - value) < 1e-9 and abs(row[1] - y) < 1e-9 for value, y in ticks)
        ]
        if not matching:
            continue
        axis_x = float(np.median([row[2] for row in matching]))
        if axis_x < panel_rect.x0 - 130.0 or axis_x > panel_rect.x1 + 130.0:
            continue
        if x_axis_edges is not None:
            x_span = x_axis_edges[1] - x_axis_edges[0]
            cross_axis_gap = min(abs(axis_x - edge) for edge in x_axis_edges)
            if cross_axis_gap > max(72.0, 0.2 * x_span):
                continue
        overlap = max(0.0, min(max(ys), panel_rect.y1) - max(min(ys), panel_rect.y0))
        reference_span = max(1.0, min(y_span, panel_rect.height))
        if overlap / reference_span < 0.25:
            continue
        horizontal_gap = min(abs(axis_x - panel_rect.x0), abs(axis_x - panel_rect.x1))
        value_span = max(value for value, _y in ticks) - min(value for value, _y in ticks)
        score = (
            20.0 * len(ticks)
            + 4.0 * value_span
            + 0.03 * y_span
            - 0.12 * horizontal_gap
        )
        if best is None or score > best[0]:
            best = (score, ticks, axis_x)
    return (best[1], best[2]) if best is not None else None


def _normalize_y_tick_candidates(
    candidates: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Collapse labels on one side of a plot into a monotone y-axis."""

    candidates.sort(key=lambda item: item[1])
    grouped: list[list[tuple[float, float]]] = []
    for item in candidates:
        if grouped and abs(item[1] - grouped[-1][-1][1]) <= 3.0:
            grouped[-1].append(item)
        else:
            grouped.append([item])
    ticks: list[tuple[float, float]] = []
    for group in grouped:
        values = [g[0] for g in group]
        ys = [g[1] for g in group]
        ticks.append((float(np.median(values)), float(np.median(ys))))
    if len(ticks) < 2:
        return []

    ticks.sort(key=lambda item: item[1])
    best: tuple[float, list[tuple[float, float]]] | None = None
    for start in range(len(ticks) - 1):
        for stop in range(start + 2, len(ticks) + 1):
            run = ticks[start:stop]
            vals = np.array([value for value, _y in run], dtype=float)
            ys = np.array([y for _value, y in run], dtype=float)
            if np.any(np.diff(vals) >= 0):
                continue
            value_span = float(vals[0] - vals[-1])
            if len(run) == 2 and value_span < 4.0:
                continue
            if len(run) >= 3:
                slope, intercept = np.polyfit(ys, vals, 1)
                residual = float(np.max(np.abs(vals - (slope * ys + intercept))))
                if residual > max(0.35, 0.08 * value_span):
                    continue
            score = 10.0 * len(run) + 0.01 * float(ys[-1] - ys[0])
            if best is None or score > best[0]:
                best = (score, run)
    return best[1] if best is not None else []


def _best_x_axis_for_panel(
    page: pymupdf.Page,
    panel_rect: pymupdf.Rect,
) -> tuple[list[tuple[float, float]], float] | None:
    """Return the best nearby x-axis as ``(ticks, row_y)``."""

    candidates: list[tuple[float, float, float]] = []
    try:
        words = page.get_text("words")
    except Exception:
        return None
    for word in words:
        wx0, wy0, wx1, wy1 = [float(value) for value in word[:4]]
        value = _parse_numeric_label(str(word[4]))
        if value is None or value < 0 or value > 2000:
            continue
        candidates.append((value, 0.5 * (wx0 + wx1), 0.5 * (wy0 + wy1)))

    rows: list[list[tuple[float, float, float]]] = []
    for item in sorted(candidates, key=lambda candidate: candidate[2]):
        if rows and abs(item[2] - float(np.median([entry[2] for entry in rows[-1]]))) <= 4.0:
            rows[-1].append(item)
        else:
            rows.append([item])

    best: tuple[float, list[tuple[float, float]], float] | None = None
    for row in rows:
        ticks = _normalize_x_tick_candidates(
            [
                (value, x)
                for value, x, _y in row
                if panel_rect.x0 - 20.0 <= x <= panel_rect.x1 + 20.0
            ]
        )
        if len(ticks) < 3:
            continue
        xs = [x for _value, x in ticks]
        x_span = max(xs) - min(xs)
        overlap = max(0.0, min(max(xs), panel_rect.x1) - max(min(xs), panel_rect.x0))
        if x_span < 45.0 or overlap / x_span < 0.35:
            continue
        row_y = float(np.median([entry[2] for entry in row]))
        vertical_gap = min(abs(row_y - panel_rect.y0), abs(row_y - panel_rect.y1))
        if vertical_gap > 180.0:
            continue
        score = 18.0 * len(ticks) + 0.05 * x_span - 0.12 * vertical_gap
        if best is None or score > best[0]:
            best = (score, ticks, row_y)
    return (best[1], best[2]) if best is not None else None


def _normalize_x_tick_candidates(
    candidates: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    candidates.sort(key=lambda item: item[1])
    best: tuple[float, list[tuple[float, float]]] | None = None
    for start in range(len(candidates) - 2):
        for stop in range(start + 3, len(candidates) + 1):
            run = candidates[start:stop]
            vals = np.array([value for value, _x in run], dtype=float)
            xs = np.array([x for _value, x in run], dtype=float)
            if np.any(np.diff(vals) <= 0) or np.ptp(xs) < 1e-9:
                continue
            slope, intercept = np.polyfit(xs, vals, 1)
            value_span = float(vals[-1] - vals[0])
            residual = float(np.max(np.abs(vals - (slope * xs + intercept))))
            if residual > max(0.5, 0.06 * value_span):
                continue
            score = 10.0 * len(run) + 0.01 * float(xs[-1] - xs[0])
            if best is None or score > best[0]:
                best = (score, run)
    return best[1] if best is not None else []


def _text_near_rect(page: pymupdf.Page, bbox: pymupdf.Rect, pad: float = 26.0) -> str:
    rect = (bbox + (-pad, -pad, pad, pad)) & page.rect
    parts: list[str] = []
    try:
        words = page.get_text("words")
    except Exception:
        return ""
    for word in words:
        x0, y0, x1, y1 = [float(v) for v in word[:4]]
        cx = 0.5 * (x0 + x1)
        cy = 0.5 * (y0 + y1)
        if rect.contains((cx, cy)):
            parts.append(str(word[4]))
    return " ".join(parts)


def _reject_non_gate_context(text: str, *, broad: bool = False) -> bool:
    tl = re.sub(r"\s+", " ", text.lower())
    absolute_stop = (
        "source-drain diode",
        "source drain diode",
        "source- drain diode",
        "source - drain diode",
        "source-drain voltage",
        "source drain voltage",
        "reverse drain current",
        "diode forward",
        "reverse diode",
        "vsd source-drain",
        "vsd source drain",
    )
    if any(stop in tl for stop in absolute_stop):
        return True
    table_stop = ("test condition", "min", "typ", "max", "unit", "symbol")
    if "gate charge" in tl or "qg" in tl or "qgate" in tl:
        if all(tok in tl for tok in table_stop):
            return True
        return False
    return broad and all(tok in tl for tok in table_stop)


def _reject_ambiguous_broad_context(tight_text: str, broad_text: str) -> bool:
    if tight_text.strip():
        return False
    tl = re.sub(r"\s+", " ", broad_text.lower())
    return ("reverse drain current" in tl and ("source-drain" in tl or "source drain" in tl))


def _non_gate_plot_reason(tight_text: str, broad_text: str) -> str | None:
    """Classify a refined plot region that belongs to a different chart.

    Finder panels can overlap neighboring plots. The final, calibrated plot
    box must therefore carry local gate-charge evidence when its surrounding
    title/axes identify a different MOSFET characteristic.
    """

    tight = re.sub(r"\s+", " ", tight_text.lower())
    broad = re.sub(r"\s+", " ", broad_text.lower())
    table_markers = (
        "drain leakage current",
        "gate leakage current",
        "gate resistance",
        "static characteristics",
        "dynamic characteristics",
        "pinning information",
    )
    if (
        "symbol parameter conditions" in broad
        and ("static characteristics" in broad or "pinning information" in broad)
    ) or sum(marker in broad for marker in table_markers) >= 3:
        return "spec_table"
    if _has_gate_charge_evidence(tight):
        return None
    if (
        "normalized d-s breakdown voltage" in broad
        or "normalized d-s breakdown" in broad
        or ("breakdown voltage" in broad and "tj" in broad)
    ):
        return "breakdown_voltage"
    if (
        "drain-source on-state resistance" in broad
        or "on-resistance vs. drain current" in broad
        or "rdson" in re.sub(r"[^a-z0-9]+", "", broad)
    ):
        return "on_resistance"
    if "output characteristics" in broad or (
        "drain current" in broad and "drain-to-source voltage" in broad
    ):
        return "output_characteristics"
    if _reject_non_gate_context(broad, broad=True):
        return "diode"
    return None


def _has_gate_charge_evidence(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.lower())
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    return (
        "gate charge" in normalized
        or "total gate charge" in normalized
        or "qgate" in compact
        or "qg" in compact
    )


def _v_from_local_ticks(y_ticks: list[tuple[float, float]], y_px: float, rect: pymupdf.Rect, scale: float) -> float | None:
    if len(y_ticks) < 2:
        return None
    vals = np.array([float(v) for v, _ in y_ticks], dtype=float)
    ys_pdf = np.array([float(y) for _, y in y_ticks], dtype=float)
    if np.ptp(vals) < 1e-9 or np.ptp(ys_pdf) < 1e-9:
        return None
    m, b = np.polyfit(ys_pdf, vals, 1)
    y_pdf = rect.y0 + float(y_px) / scale
    return float(m * y_pdf + b)


def _y_pixel_from_local_ticks(y_ticks: list[tuple[float, float]], v: float, rect: pymupdf.Rect, scale: float) -> float | None:
    if len(y_ticks) < 2:
        return None
    vals = np.array([float(val) for val, _ in y_ticks], dtype=float)
    ys_pdf = np.array([float(y) for _, y in y_ticks], dtype=float)
    if np.ptp(vals) < 1e-9 or np.ptp(ys_pdf) < 1e-9:
        return None
    m, b = np.polyfit(vals, ys_pdf, 1)
    y_pdf = float(m * float(v) + b)
    return (y_pdf - rect.y0) * scale


def _v_from_plot_axis(plot_box: tuple[int, int, int, int], y_px: float, v_min: float, v_max: float) -> float:
    _x0, y0, _x1, y1 = plot_box
    return float(v_min + (v_max - v_min) * (y1 - y_px) / max(1.0, y1 - y0))


def _line_sse(xs: np.ndarray, ys: np.ndarray, i: int, j: int) -> float:
    if j - i < 2:
        return 1e18
    x = xs[i:j]
    y = ys[i:j]
    if np.ptp(x) < 1e-9:
        return 1e18
    m, b = np.polyfit(x, y, 1)
    r = y - (m * x + b)
    return float(np.dot(r, r))


def _middle_slope_y(points: list[tuple[int, int]], plot_box: tuple[int, int, int, int]) -> float | None:
    """Mean y of the Miller-slope section from a 3-line gate-charge fit."""
    if len(points) < 30:
        return None
    x0, _y0, x1, _y1 = plot_box
    pts = sorted(points)
    xs = np.array([p[0] for p in pts], dtype=float)
    ys = np.array([p[1] for p in pts], dtype=float)
    n = len(xs)
    min_len = max(8, n // 12)
    best: tuple[float, int, int] | None = None
    for i in range(min_len, n - 2 * min_len):
        rel_i = (xs[i] - x0) / max(1.0, x1 - x0)
        if rel_i < 0.05 or rel_i > 0.60:
            continue
        for j in range(i + min_len, n - min_len):
            rel_j = (xs[j] - x0) / max(1.0, x1 - x0)
            if rel_j < 0.18 or rel_j > 0.88:
                continue
            cost = _line_sse(xs, ys, 0, i) + _line_sse(xs, ys, i, j) + _line_sse(xs, ys, j, n)
            if best is None or cost < best[0]:
                best = (cost, i, j)
    if best is None:
        return None
    _cost, i, j = best
    if j - i < min_len:
        return None
    return float(np.mean(ys[i:j]))


def _estimate_vpl_from_curve(
    curve: list[tuple[int, int]],
    chart,
    rect: pymupdf.Rect,
    scale: float,
    plot_box: tuple[int, int, int, int],
    local_y_ticks: list[tuple[float, float]] | None = None,
) -> tuple[float | None, float | None]:
    """Return (Vpl, y_px) from the traced VGS(Qg) curve."""
    if len(curve) < 20:
        return None, None
    x0, y0, x1, y1 = plot_box
    pts = sorted(curve)
    xs = np.array([p[0] for p in pts], dtype=float)
    ys = np.array([p[1] for p in pts], dtype=float)
    middle_y = _middle_slope_y(pts, plot_box)
    win = max(8, int(0.07 * len(pts)))
    candidates: list[tuple[float, float, float, float, int, int]] = []
    for i in range(0, len(pts) - win):
        j = i + win
        x_mid = 0.5 * (xs[i] + xs[j - 1])
        rel_x = (x_mid - x0) / max(1.0, x1 - x0)
        if rel_x < 0.04 or rel_x > 0.78:
            continue
        yr = float(np.percentile(ys[i:j], 90) - np.percentile(ys[i:j], 10))
        xr = float(xs[j - 1] - xs[i])
        before = ys[:i]
        after = ys[j:]
        if len(before) < 4 or len(after) < 4:
            continue
        pre_rise = float(np.percentile(before, 90) - np.median(ys[i:j]))
        post_rise = float(np.median(ys[i:j]) - np.percentile(after, 10))
        if pre_rise < 0.05 * (y1 - y0):
            continue
        if post_rise < 0.05 * (y1 - y0):
            continue
        flatness = yr / max(1.0, y1 - y0)
        xfrac = xr / max(1.0, x1 - x0)
        score = 3.0 * xfrac / (flatness + 0.01) + min(pre_rise, post_rise) / max(1.0, y1 - y0) - 8.0 * rel_x
        candidates.append((score, rel_x, float(np.median(ys[i:j])), yr, i, j))
    if not candidates:
        if middle_y is None:
            return None, None
        y_px = middle_y
        if local_y_ticks:
            local_v = _v_from_local_ticks(local_y_ticks, y_px, rect, scale)
            if local_v is not None:
                return local_v, y_px
        return _v_from_y_pixel(chart, rect, scale, y_px), y_px
    best_score = max(c[0] for c in candidates)
    plausible = [c for c in candidates if c[0] >= 0.70 * best_score]
    if not plausible:
        plausible = candidates
    _score, _rel_x, y_px, candidate_yr, _i, _j = min(plausible, key=lambda c: c[1])
    if (
        middle_y is not None
        and candidate_yr > 0.010 * max(1, y1 - y0)
        and abs(middle_y - y_px) > 0.035 * max(1, y1 - y0)
    ):
        y_px = middle_y
    if local_y_ticks:
        local_v = _v_from_local_ticks(local_y_ticks, y_px, rect, scale)
        if local_v is not None:
            return local_v, y_px
    return _v_from_y_pixel(chart, rect, scale, y_px), y_px
