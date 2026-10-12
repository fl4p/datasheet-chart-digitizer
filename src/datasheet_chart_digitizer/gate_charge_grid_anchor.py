"""Seat gate-charge VGS and Qg ticks on the gridlines their labels name.

The Vpl scalar is read through a straight line fitted to the VGS tick-LABEL
centres (``gate_charge_estimation._v_from_local_ticks`` on word-box centres,
and ``gate_axis_ocr.recalibrate_gate_result`` on OCR boxes). A label is set
beside its rule, not on it, so that fit is biased by the typography. Here the
labels only identify values: ``gridline_anchor.anchor_axis_on_grid`` binds each
to its observed rule (or frame tick mark) and the ticks are re-expressed at the
rule, then ``check_served_on_grid`` checks the mapping that will be SERVED.

Pixels here are continuous crop pixels (``(pt - crop.y0) * scale``, the
convention of the gate-charge ticks, plot box and ``vpl_y_px``);
gridline_anchor works in pixel-index coordinates (index = continuous - 0.5).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .gridline_anchor import (
    GridCheck,
    Orientation,
    anchor_axis_on_grid_attempts,
    check_served_on_grid_attempts,
)
from .numeric_axis import AxisTick, NumericAxis, fit_axis_ticks

_INDEX_TO_CONTINUOUS = 0.5


@dataclass(frozen=True)
class _Frame:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class GateAxisGrid:
    """Served ticks (value, continuous px) of one axis plus the grid evidence."""

    ticks_px: tuple[tuple[float, float], ...]
    anchoring: dict | None
    anchor_attempt: str | None
    anchor_error: str | None
    check: GridCheck

    @property
    def anchored(self) -> bool:
        """The served ticks are the rules the labels name (not label glyphs)."""
        return self.anchoring is not None

    def payload(self) -> dict[str, object]:
        return {
            "anchoring": self.anchoring,
            "anchor_attempt": self.anchor_attempt,
            "anchor_error": self.anchor_error,
            "grid_check": self.check.payload(),
        }


# Kept for callers that name the VGS axis explicitly.
GateYGrid = GateAxisGrid


def _index_ticks(ticks_px) -> list[AxisTick]:
    return [
        AxisTick(f"{value:g}", float(value), float(px) - _INDEX_TO_CONTINUOUS)
        for value, px in ticks_px
    ]


def _served_index_axis(ticks_px) -> NumericAxis:
    """The straight line the caller serves (value vs continuous px), in index px."""
    values = np.asarray([v for v, _ in ticks_px], dtype=float)
    pixels = np.asarray([p for _, p in ticks_px], dtype=float)
    m, b = np.polyfit(pixels, values, 1)
    return NumericAxis("linear", float(m), float(b + m * _INDEX_TO_CONTINUOUS), (), 0.0, ())


def _frame_and_cross(plot_box, orientation: Orientation):
    x0, y0, x1, y1 = (float(v) for v in plot_box)
    frame = _Frame(x0, y0, x1, y1)
    lo, hi = (y0, y1) if orientation == "x" else (x0, x1)
    return frame, (lo - _INDEX_TO_CONTINUOUS, hi - _INDEX_TO_CONTINUOUS)


def seat_gate_ticks(
    gray: np.ndarray,
    label_ticks_px: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    plot_box: tuple[int, int, int, int],
    *,
    orientation: Orientation,
    name: str,

) -> GateAxisGrid:
    """Anchor one axis's labelled ticks on their rules; check the served mapping.

    *label_ticks_px* are (value, continuous crop px of the label centre);
    *orientation* ``"y"`` is the VGS axis (horizontal rules), ``"x"`` the Qg
    axis (vertical rules). If no rule binding is evidenced the label ticks are
    returned unchanged and the check says why (``unverified``) -- the caller
    must not serve anything read through that axis as verified.
    """
    frame, cross = _frame_and_cross(plot_box, orientation)
    label_ticks = _index_ticks(label_ticks_px)
    served_ticks = tuple((float(v), float(p)) for v, p in label_ticks_px)
    anchoring = attempt_name = anchor_error = None
    try:
        label_axis = fit_axis_ticks(label_ticks, name, model="linear")
    except RuntimeError as exc:
        label_axis = None
        anchor_error = str(exc)
    if label_axis is not None:
        try:
            anchored, attempt_name = anchor_axis_on_grid_attempts(
                gray, label_axis, frame=frame, orientation=orientation, cross_span=cross, name=name
            )
        except RuntimeError as exc:
            anchor_error = str(exc)
        else:
            served_ticks = tuple(
                (a.value, a.line_px + _INDEX_TO_CONTINUOUS) for a in anchored.anchors
            )
            anchoring = anchored.payload()
            anchoring["pixel_convention"] = "index (add 0.5 for continuous crop px)"
    if anchoring is None and anchor_error is not None:
        ordinal, ordinal_reason = ordinal_rule_ticks(
            gray, label_ticks_px, plot_box, orientation=orientation
        )
        if ordinal is not None:
            served_ticks = ordinal
            anchoring = {
                "binding": "ordinal_rules",
                "registration_error": anchor_error[:400],
                "pixel_convention": "continuous crop px",
                "ticks": [{"value": v, "line_px": round(p, 3)} for v, p in ordinal],
                "note": ordinal_reason,
            }
            attempt_name = "ordinal_rules"
        else:
            anchor_error = f"{anchor_error}; ordinal: {ordinal_reason}"
    check = check_gate_on_grid(
        gray, served_ticks, label_ticks_px, plot_box, orientation=orientation, name=name
    )
    return GateAxisGrid(served_ticks, anchoring, attempt_name, anchor_error, check)


# Ordinal rule binding (fallback when the affine registration fails).
# Rules and labels must each be this uniform: max residual from their own
# line (rules: a raster grid's source-pixel quantisation; labels: glyph
# centres of digits of different widths, and a shared corner "0").
_ORDINAL_UNIFORM_MIN_PX = 2.5
_ORDINAL_RULE_UNIFORM_FRACTION = 0.03
_ORDINAL_LABEL_UNIFORM_FRACTION = 0.06
# A rule must be ink over this share of the plot span (solid, not a dotted minor).
_ORDINAL_RULE_COVERAGE = 0.6
# The label run's pitch may differ from the rules' by at most this fraction
# (FDMS86500L: 26.3 pt labels on a 28.4 pt grid, 7.4 %) ...
_ORDINAL_PITCH_RATIO = 0.10
# ... and no label may sit half a rule pitch or more from its own rule.
_ORDINAL_LABEL_FRACTION = 0.5
_ORDINAL_MIN_LABELS = 4


def _line_coverage(gray, centre_px: float, orientation, cross) -> float:
    lo, hi = int(max(0, cross[0] + 3)), int(cross[1] - 3)
    best = 0.0
    for d in (-1, 0, 1):
        i = int(centre_px - 0.5) + d
        if orientation == "x":
            if not 0 <= i < gray.shape[1]:
                continue
            line = gray[lo:hi, i]
        else:
            if not 0 <= i < gray.shape[0]:
                continue
            line = gray[i, lo:hi]
        if len(line):
            best = max(best, float((line < 200).mean()))
    return best


def ordinal_rule_ticks(gray, label_ticks_px, plot_box, *, orientation: Orientation):
    """Bind the k-th label to the k-th solid full-span rule, when unambiguous.

    The affine registration needs every label on a line within ~1.5 px of ONE
    straight line; a raster chart's grid is quantised to its source pixels
    and misses that by a pixel or two (onsemi NTMFS4C022NT1G p4: 11 Qg rules,
    pitches 61..64 px, 1.7 px off a straight line), and some printed label
    runs are spaced unlike their own grid (FDMS86500L: 26.3 pt label pitch on
    a 28.4 pt grid). Identity then comes from COUNT and ORDER: exactly one
    solid rule per label in the labelled span, rules and labels each uniform,
    their pitches within ``_ORDINAL_PITCH_RATIO``, every label less than half
    a pitch from its own rule, arithmetic label values.

    Returns ``(ticks, note)`` -- served ticks (value, continuous px) on the
    rules -- or ``(None, reason)``. Identity evidence only: the gate class
    serves an ordinal axis as verified only when the spec table's Qg agrees.
    """
    from .gridline_anchor import detect_axis_lines

    ticks = sorted(((float(v), float(p)) for v, p in label_ticks_px), key=lambda t: t[1])
    if len(ticks) < _ORDINAL_MIN_LABELS:
        return None, f"fewer than {_ORDINAL_MIN_LABELS} labels"
    values = np.asarray([v for v, _ in ticks])
    pixels = np.asarray([p for _, p in ticks])
    steps = np.diff(values)
    if not (np.all(steps > 0) or np.all(steps < 0)) or np.ptp(steps) > 1e-6 * max(1.0, float(np.abs(steps).max())):
        return None, "label values are not one arithmetic run"
    pitch = float(np.median(np.abs(np.diff(pixels))))
    if pitch <= 2.0:
        return None, "label pitch too small"
    x0, y0, x1, y1 = (float(v) for v in plot_box)
    cross = (y0 - 0.5, y1 - 0.5) if orientation == "x" else (x0 - 0.5, x1 - 0.5)
    lo, hi = pixels.min() - 0.6 * pitch, pixels.max() + 0.6 * pitch
    lines = detect_axis_lines(
        gray, orientation=orientation, along=(lo - 0.5, hi - 0.5), cross_span=cross,
        max_width=max(8, int(round(0.08 * pitch))), tick_band=max(3, int(round(0.05 * pitch))),
    )
    centres = np.asarray(sorted(
        line.center_px + 0.5 for line in lines
        if line.source in {"gridline", "split_rule"} and lo <= line.center_px + 0.5 <= hi
        and _line_coverage(gray, line.center_px + 0.5, orientation, cross) >= _ORDINAL_RULE_COVERAGE
    ))
    if len(centres) != len(ticks):
        return None, f"{len(centres)} solid rules for {len(ticks)} labels"
    k = np.arange(len(centres))
    rule_fit = np.polyfit(k, centres, 1)
    label_fit = np.polyfit(k, pixels, 1)
    rule_pitch = abs(float(rule_fit[0]))
    rule_residual = float(np.abs(centres - np.polyval(rule_fit, k)).max())
    label_residual = float(np.abs(pixels - np.polyval(label_fit, k)).max())
    if rule_residual > max(_ORDINAL_UNIFORM_MIN_PX, _ORDINAL_RULE_UNIFORM_FRACTION * rule_pitch):
        return None, f"rules not uniform (residual {rule_residual:.1f}px)"
    if label_residual > max(_ORDINAL_UNIFORM_MIN_PX, _ORDINAL_LABEL_UNIFORM_FRACTION * rule_pitch):
        return None, f"labels not uniform (residual {label_residual:.1f}px)"
    ratio = abs(float(label_fit[0])) / rule_pitch
    if abs(ratio - 1.0) > _ORDINAL_PITCH_RATIO:
        return None, f"label pitch {ratio:.3f}x the rule pitch"
    offsets = pixels - centres
    if float(np.abs(offsets).max()) >= _ORDINAL_LABEL_FRACTION * rule_pitch:
        return None, f"a label sits {float(np.abs(offsets).max()):.1f}px from its ordinal rule"
    served = tuple((float(v), float(c)) for (v, _p), c in zip(ticks, centres))
    return served, (
        f"{len(served)} labels bound in order to {len(served)} solid rules (rule residual "
        f"{rule_residual:.1f}px, label/rule pitch {ratio:.3f}, max label offset "
        f"{float(np.abs(offsets).max()):.1f}px)"
    )


def seat_gate_y_ticks(gray, label_ticks_px, plot_box) -> GateAxisGrid:
    """VGS: ``seat_gate_ticks`` on the horizontal rules."""
    return seat_gate_ticks(gray, label_ticks_px, plot_box, orientation="y", name="VGS")


def seat_gate_x_ticks(gray, label_ticks_px, plot_box) -> GateAxisGrid:
    """Qg: ``seat_gate_ticks`` on the vertical rules (or bottom-frame tick marks)."""
    return seat_gate_ticks(gray, label_ticks_px, plot_box, orientation="x", name="Qg")


def check_gate_on_grid(
    gray: np.ndarray,
    served_ticks_px,
    label_ticks_px,
    plot_box: tuple[int, int, int, int],
    *,
    orientation: Orientation,
    name: str,
) -> GridCheck:
    """The guard: the SERVED line of one axis against the rule each label names.

    *served_ticks_px* is what the caller serves values through (after every
    transform); *label_ticks_px* only identify which rule each value names.
    """
    if len(served_ticks_px) < 2 or len(label_ticks_px) < 2:
        return GridCheck("unverified", f"{name}: fewer than 2 ticks to check", None, ())
    served_px = [float(p) for _v, p in served_ticks_px]
    if len({round(p, 6) for p in served_px}) < 2:
        return GridCheck("unverified", f"{name}: served ticks collapse to one pixel", None, ())
    frame, cross = _frame_and_cross(plot_box, orientation)
    served = _served_index_axis(served_ticks_px)
    labels = _index_ticks(label_ticks_px)
    return check_served_on_grid_attempts(
        gray, served, labels, frame=frame, orientation=orientation, cross_span=cross, name=name
    )


def check_gate_y_on_grid(gray, served_ticks_px, label_ticks_px, plot_box) -> GridCheck:
    """The guard: the served VGS line against the rule each label names."""
    return check_gate_on_grid(
        gray, served_ticks_px, label_ticks_px, plot_box, orientation="y", name="VGS"
    )


def check_gate_x_on_grid(gray, served_ticks_px, label_ticks_px, plot_box) -> GridCheck:
    """The guard: the served Qg line against the rule each label names."""
    return check_gate_on_grid(
        gray, served_ticks_px, label_ticks_px, plot_box, orientation="x", name="Qg"
    )


def served_value(ticks_px, y_px: float) -> float:
    """VGS at continuous crop pixel *y_px* through the served ticks."""
    values = np.asarray([v for v, _ in ticks_px], dtype=float)
    pixels = np.asarray([p for _, p in ticks_px], dtype=float)
    m, b = np.polyfit(pixels, values, 1)
    return float(m * y_px + b)
