"""Raster grid-mask helpers for capacitance trace extraction."""

from __future__ import annotations

import numpy as np


FULL_WIDTH_HORIZONTAL_RAIL_MIN_OCCUPANCY = 0.80


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
