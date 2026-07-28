"""Raster grid-mask helpers for capacitance trace extraction."""

from __future__ import annotations

import numpy as np


FULL_WIDTH_HORIZONTAL_RAIL_MIN_OCCUPANCY = 0.80
FRAME_RESIDUAL_MIN_OCCUPANCY = 0.50
FRAME_RESIDUAL_BAND_PX = 12
BLACK_GRID_MIN_FULL_SPAN_RULES = 3


def _dark_grid_rule_evidence(dark: np.ndarray) -> bool:
    """True when the dark mask itself contains a full black GRID.

    The trace/grid separation (2x2 opening + rail blanking) was gated on
    ``dark.mean() > 0.10`` -- an ink-fraction PROXY for "the grid is drawn in
    trace-dark ink". ST/MCC black-grid charts sit at 5-8% ink and slipped
    under it, so whole gridline sets survived as per-column stroke centers
    and stole trace-band slots (top50-fugu2: a gridline digitized as Ciss
    with every identity shifted one band down).

    The signature, not the proxy: a black GRID shows several distinct
    full-span dark rules in BOTH orientations, well inside the frame. Either
    orientation alone is not a grid -- a flat Ciss riding a printed decade
    line plus the frame reads as 2-3 horizontal "rules" on gray-grid charts
    (XRS200N12T) and must not trigger the opening, which erodes exactly that
    flat trace.
    """

    if dark.ndim != 2 or dark.size == 0:
        return False
    height, width = dark.shape
    edge = max(8, int(round(0.03 * min(height, width))))
    if height <= 2 * edge or width <= 2 * edge:
        return False
    row_occupancy = np.mean(dark > 0, axis=1)
    col_occupancy = np.mean(dark > 0, axis=0)

    def distinct_rules(indices: np.ndarray) -> int:
        count = 0
        previous = None
        for index in indices:
            if previous is None or index - previous > 4:
                count += 1
            previous = index
        return count

    interior_rows = np.flatnonzero(row_occupancy >= 0.80)
    interior_rows = interior_rows[
        (interior_rows >= edge) & (interior_rows < height - edge)
    ]
    interior_cols = np.flatnonzero(col_occupancy >= 0.85)
    interior_cols = interior_cols[
        (interior_cols >= edge) & (interior_cols < width - edge)
    ]
    return (
        distinct_rules(interior_rows) >= BLACK_GRID_MIN_FULL_SPAN_RULES
        and distinct_rules(interior_cols) >= BLACK_GRID_MIN_FULL_SPAN_RULES
    )


def _remove_frame_residual_rails(mask: np.ndarray) -> np.ndarray:
    """Blank partially-eroded frame strokes hugging the plot's top/bottom.

    The 2x2 opening thins the plot frame instead of erasing it; the survivor
    rows sit just inside the blanked margin at ~50-70% occupancy -- under the
    full-width rail bar, yet stable enough to track as a flat phantom trace
    (onsemi FDPF2D3N10C: top-frame residual took a band slot). Only rows
    within a few pixels of the mask's edges are eligible, so a genuinely flat
    mid-plot source trace is untouched.
    """

    if mask.ndim != 2 or mask.size == 0:
        return mask
    occupancy = np.mean(mask > 0, axis=1)
    height = mask.shape[0]
    band = min(FRAME_RESIDUAL_BAND_PX, height // 4)
    rail_rows = occupancy >= FRAME_RESIDUAL_MIN_OCCUPANCY
    eligible = np.zeros(height, dtype=bool)
    eligible[:band] = True
    eligible[height - band:] = True
    rows = rail_rows & eligible
    if not np.any(rows):
        return mask
    cleaned = mask.copy()
    cleaned[rows, :] = 0
    return cleaned


def _remove_full_width_horizontal_rails(mask: np.ndarray) -> np.ndarray:
    """Remove thick black grid rails without erasing sloped source strokes.

    Toshiba whole-figure rasters draw grid and data in the same black ink. Most
    one-pixel rails disappear in the caller's 2x2 opening, but thick major rails
    survive and can be followed as flat traces. A source curve may cross a rail
    but does not occupy eighty percent of a complete plot row, so blank only
    those near-full-width rows. The directional tracker bridges the small gap.
    """

    if mask.ndim != 2 or mask.size == 0:
        return mask
    rail_rows = (
        np.mean(mask > 0, axis=1) >= FULL_WIDTH_HORIZONTAL_RAIL_MIN_OCCUPANCY
    )
    if not np.any(rail_rows):
        return mask
    cleaned = mask.copy()
    cleaned[rail_rows, :] = 0
    return cleaned
