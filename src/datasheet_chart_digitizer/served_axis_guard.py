"""The served-calibration grid guard for NumericAxis-based digitizers.

Body-diode, RDS(on)(Tj), RDS(on)(Id), transfer and breakdown calibrations
already snap label-fitted ticks onto raster gridlines
(``diode_forward_voltage._snap_axis_to_grid`` / breakdown's
``_snap_axis_ticks_to_grid``), but those snaps return the label-centre axis
silently when they find too few lines or no nearby line, and later steps
(frame anchoring, unit scaling) can move the mapping again. This module
checks the mapping each class finally SERVES, at every labelled tick, with
``gridline_anchor.check_served_on_grid_attempts`` -- the label pixel only
identifies which rule a value names -- and turns the verdicts into the
class's own refusal vocabulary.

These digitizers compare their served mappings to raster line centres in
pixel-index coordinates already (``_projection_line_centers``), so no
coordinate shift is applied here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .gridline_anchor import GridCheck, check_served_on_grid_attempts
from .numeric_axis import AxisTick, NumericAxis


@dataclass(frozen=True)
class _Frame:
    x0: float
    y0: float
    x1: float
    y1: float


def _paired_label_ticks(served: NumericAxis, labels: NumericAxis) -> list[AxisTick] | None:
    """Label pixels with the SERVED axis's values (units may be rescaled)."""
    if len(served.ticks) != len(labels.ticks) or not labels.ticks:
        return None
    by_value_served = sorted(served.ticks, key=lambda t: t.value)
    by_value_label = sorted(labels.ticks, key=lambda t: t.value)
    return [
        AxisTick(label.text, served_tick.value, label.pixel, label.normalized_text)
        for served_tick, label in zip(by_value_served, by_value_label)
    ]


def check_axes(
    gray: np.ndarray,
    plot,
    served: dict[str, NumericAxis],
    labels: dict[str, NumericAxis],
    name: str,
) -> dict[str, GridCheck]:
    """Check each served axis against the rules its labels name.

    *plot* is anything with x0/y0/x1/y1 in the same pixels as the axes.
    *labels* are the label-fitted axes BEFORE any snapping (their tick pixels
    are label centres). An axis whose label ticks cannot be paired with the
    served ticks is unverified.
    """
    frame = _Frame(float(plot.x0), float(plot.y0), float(plot.x1), float(plot.y1))
    spans = {"x": (frame.y0, frame.y1), "y": (frame.x0, frame.x1)}
    checks: dict[str, GridCheck] = {}
    for axis in ("x", "y"):
        ticks = _paired_label_ticks(served[axis], labels[axis])
        if ticks is None:
            checks[axis] = GridCheck(
                "unverified",
                f"{name} {axis}: served and labelled ticks cannot be paired one to one",
                None,
                (),
            )
            continue
        mapping = NumericAxis(served[axis].model, served[axis].m, served[axis].b, (), 0.0, ())
        checks[axis] = check_served_on_grid_attempts(
            gray, mapping, ticks, frame=frame, orientation=axis, cross_span=spans[axis],
            name=f"{name} {axis}",
        )
    return checks


def payload(checks: dict[str, GridCheck] | None) -> dict[str, object] | None:
    if checks is None:
        return None
    return {axis: check.payload() for axis, check in checks.items()}


def problems(checks: dict[str, GridCheck] | None) -> tuple[list[str], list[str]]:
    """(failed, unverified) reasons; a check that never ran is unverified."""
    if checks is None:
        return [], ["served axis grid check not run"]
    failed = [c.reason for c in checks.values() if c.status == "failed"]
    unverified = [c.reason for c in checks.values() if c.status == "unverified"]
    return failed, unverified
