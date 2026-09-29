"""Seat label-identified axis ticks on the gridlines or tick marks they label.

A printed tick label names a VALUE; it does not mark a PIXEL. Datasheets set
labels beside their gridline, not centred on it: the glyph is shifted for
legibility, a two-digit label is wider than a one-digit one, and a decade label
sits a few pixels above or below its rule. Fitting the axis to label centres
therefore calibrates the typography, and a residual measured against those same
centres certifies the fit to itself -- it cannot see that the served mapping
misses the grid. On the Vishay BAT54W-G leakage panel the label-centre fit put
the 25 V tick 12 px right of its gridline and missed the 100 uA decade by 5.6 px
while reporting a 1.4 px residual.

This module keeps the labels for IDENTITY only and takes every served pixel from
the raster:

1. Observed lines are long thin dark runs across the plot (gridlines, including
   the frame, whose centre is used however thick it is), plus short tick marks
   attached to the frame on the label side when the chart has no gridline there.
2. Each labelled tick must have an observed line within a fraction of the label
   pitch, or the axis is refused: a label with nothing under it is not a tick.
3. The labelled sequence is registered onto the observed lines as ONE affine
   hypothesis (end ticks choose the hypothesis, every interior tick must then
   land on a line). On a log axis the hypothesis must also explain the minor
   gridlines of each labelled decade, so the 90 % minor line 0.046 decade from a
   decade cannot stand in for it even when a label sits closer to the minor.
4. The axis is re-fitted on the matched line centres and the SERVED mapping is
   asserted at every consumed tick; a miss beyond tolerance fails closed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np

from .numeric_axis import AxisTick, NumericAxis, fit_axis_ticks

Orientation = Literal["x", "y"]
UnlinedPolicy = Literal["refuse", "identity_only"]

# Pixels darker than this count as ink. Gridlines are often mid-grey, so the
# threshold is well above black but below the anti-aliased paper background.
_INK_THRESHOLD = 200
# A gridline covers most of the plot span; curves, annotations and text do not.
_GRIDLINE_MIN_COVERAGE = 0.40
# A labelled tick may sit this fraction of the label pitch from its line.
_LABEL_TO_LINE_PITCH_FRACTION = 0.30
# A registered hypothesis must place every labelled tick on a line within this.
_MATCH_TOLERANCE_FRACTION = 0.015
_MATCH_TOLERANCE_MIN_PX = 1.5
# An identity-only (unlined interior) label must sit this close, as a fraction
# of the label pitch, to where the registration puts its value. Tighter than
# the line search: with nothing under it, its glyph is the only evidence that
# the registration's scale is right. It must also have NO observed line within
# this distance of its glyph (STP150N10F7AG: a 0->69 / 100->359 px
# registration, 7 % short of the label span, put the 20..80 V labels 0.19
# pitch off their predicted pixels).
_IDENTITY_ONLY_PITCH_FRACTION = 0.12
# On a log axis whose minor grid is drawn, a registration may leave at most
# this fraction of the observed lines inside the labelled span unexplained by
# its predicted decades + 2..9 minors.
_LOG_MINOR_UNEXPLAINED_MAX = 0.30
# A tick mark must be ink over this fraction of its band beside the frame.
_TICK_MARK_MIN_FILL = 0.80


@dataclass(frozen=True)
class ObservedLine:
    center_px: float
    width_px: int
    source: str  # "gridline" or "tick_mark"


@dataclass(frozen=True)
class TickAnchor:
    text: str
    value: float
    label_px: float
    line_px: float
    source: str
    served_px: float

    @property
    def label_offset_px(self) -> float:
        return self.label_px - self.line_px

    @property
    def served_error_px(self) -> float:
        return self.served_px - self.line_px


@dataclass(frozen=True)
class AnchoredAxis:
    axis: NumericAxis
    anchors: tuple[TickAnchor, ...]
    tolerance_px: float
    unlined: tuple[AxisTick, ...] = ()

    @property
    def max_served_error_px(self) -> float:
        return max(abs(anchor.served_error_px) for anchor in self.anchors)

    def payload(self) -> dict[str, object]:
        return {
            "pixel_source": "observed_gridline_or_tick_mark",
            "label_role": "value_identity_only",
            "residual_basis": "observed_lines",
            "tolerance_px": round(self.tolerance_px, 3),
            "max_served_error_px": round(self.max_served_error_px, 3),
            "identity_only_labels": [
                {"text": tick.text, "value": tick.value, "label_px": round(tick.pixel, 3)}
                for tick in self.unlined
            ],
            "ticks": [
                {
                    "text": anchor.text,
                    "value": anchor.value,
                    "label_px": round(anchor.label_px, 3),
                    "line_px": round(anchor.line_px, 3),
                    "line_source": anchor.source,
                    "served_px": round(anchor.served_px, 3),
                    "served_error_px": round(anchor.served_error_px, 3),
                    "label_offset_px": round(anchor.label_offset_px, 3),
                }
                for anchor in self.anchors
            ],
        }


def served_pixel(axis: NumericAxis, value: float) -> float:
    """Invert the served calibration: the pixel the mapping assigns to *value*."""
    coordinate = math.log10(value) if axis.model == "log10" else value
    return (coordinate - axis.b) / axis.m


def anchor_axis_on_grid(
    gray: np.ndarray,
    label_axis: NumericAxis,
    *,
    orientation: Orientation,
    cross_span: tuple[float, float],
    name: str,
    ink_threshold: int = _INK_THRESHOLD,
    unlined_labels: UnlinedPolicy = "refuse",
) -> AnchoredAxis:
    """Re-seat *label_axis*'s ticks on observed lines, re-fit, and assert.

    *orientation* ``"x"`` means the ticks are vertical lines at x pixels with
    labels below the plot; ``"y"`` means horizontal lines at y pixels with
    labels beside the plot. *cross_span* is the plot's extent along the other
    axis, used to measure how much of the plot a candidate line covers.
    *ink_threshold* is the grey level below which a pixel counts as ink; raise
    it for charts whose hairline rules render lighter than the default (an
    anti-aliased half-pixel frame rule renders at ~210).

    *unlined_labels* ``"refuse"`` (default) refuses the axis when any label has
    no observed line under it. ``"identity_only"`` admits an axis whose
    INTERIOR labels print values between gridlines (a secondary axis with half
    steps and no tick marks): those labels stay identity evidence and are not
    served; the two end labels and at least two labels overall must still sit
    on observed lines, so nothing is extrapolated beyond an anchored line.
    """
    match = identify_tick_lines(
        gray,
        label_axis.ticks,
        model=label_axis.model,
        orientation=orientation,
        cross_span=cross_span,
        name=name,
        ink_threshold=ink_threshold,
        unlined_labels=unlined_labels,
    )
    anchored_ticks = [
        AxisTick(tick.text, tick.value, line_px, tick.normalized_text)
        for tick, line_px in zip(match.ticks, match.line_px)
    ]
    axis = fit_axis_ticks(anchored_ticks, name, model=label_axis.model)  # type: ignore[arg-type]
    anchors = tuple(
        TickAnchor(
            tick.text,
            tick.value,
            float(tick.pixel),
            line_px,
            source,
            served_pixel(axis, tick.value),
        )
        for tick, line_px, source in zip(match.ticks, match.line_px, match.sources)
    )
    result = AnchoredAxis(axis, anchors, match.tolerance_px, match.unlined)
    if result.max_served_error_px > match.tolerance_px:
        worst = max(anchors, key=lambda anchor: abs(anchor.served_error_px))
        raise RuntimeError(
            f"{name}: served calibration misses the {worst.text!r} gridline by "
            f"{worst.served_error_px:+.2f}px (tolerance {match.tolerance_px:.2f}px)"
        )
    return result


@dataclass(frozen=True)
class TickLineMatch:
    """Which observed line each labelled tick names (identity, not fit)."""

    ticks: tuple[AxisTick, ...]  # the labelled ticks that sit on a line
    line_px: tuple[float, ...]
    sources: tuple[str, ...]
    tolerance_px: float
    unlined: tuple[AxisTick, ...]  # identity-only labels with no line under them


def identify_tick_lines(
    gray: np.ndarray,
    label_ticks: Sequence[AxisTick],
    *,
    model: str,
    orientation: Orientation,
    cross_span: tuple[float, float],
    name: str,
    ink_threshold: int = _INK_THRESHOLD,
    unlined_labels: UnlinedPolicy = "refuse",
) -> TickLineMatch:
    """Bind each labelled tick to the observed gridline or tick mark it names.

    Label pixels only SEED the search; the binding is one affine registration
    of the whole labelled sequence onto observed lines (with the log minor
    pattern scored), so a label that sits nearer a minor line or a neighbour
    cannot pick it. Raises RuntimeError when the binding is not evidenced: a
    label with no line near it (unless *unlined_labels* admits it as
    identity-only), no registration that seats every tick, two equally good
    registrations, or line order disagreeing with label order.
    """
    ticks = sorted(label_ticks, key=lambda tick: tick.pixel)
    if len(ticks) < 2:
        raise RuntimeError(f"{name}: need >=2 labelled ticks to anchor on the grid")
    all_px = np.asarray([tick.pixel for tick in ticks], dtype=float)
    pitch = float(np.median(np.diff(all_px)))
    search = _LABEL_TO_LINE_PITCH_FRACTION * pitch
    match_tol = max(_MATCH_TOLERANCE_MIN_PX, _MATCH_TOLERANCE_FRACTION * pitch)

    lines = detect_axis_lines(
        gray,
        orientation=orientation,
        along=(float(all_px[0]) - search, float(all_px[-1]) + search),
        cross_span=cross_span,
        max_width=max(8, int(round(0.08 * pitch))),
        tick_band=max(3, int(round(0.05 * pitch))),
        ink_threshold=ink_threshold,
    )
    centers = np.asarray([line.center_px for line in lines], dtype=float)

    identity_only = unlined_labels == "identity_only"
    candidates: list[list[int]] = []
    for index, tick in enumerate(ticks):
        near = [i for i, c in enumerate(centers) if abs(c - tick.pixel) <= search]
        interior = 0 < index < len(ticks) - 1
        if not near and not (identity_only and interior):
            raise RuntimeError(
                f"{name}: label {tick.text!r} ({tick.value:g}) at {tick.pixel:.1f}px has "
                f"no gridline or tick mark within {search:.1f}px; refusing to calibrate "
                "on the label glyph"
            )
        candidates.append(near)

    label_px = np.asarray([tick.pixel for tick in ticks], dtype=float)
    coords = np.asarray(
        [math.log10(t.value) if model == "log10" else t.value for t in ticks]
    )
    # identity-only: an INTERIOR label the hypothesis does not put on a line
    # is admitted only if its glyph sits where the hypothesis puts its value
    optional = [identity_only and 0 < i < len(ticks) - 1 for i in range(len(ticks))]
    hypotheses = _register(
        centers, coords, label_px, candidates, match_tol, model,
        optional=optional, label_search=_IDENTITY_ONLY_PITCH_FRACTION * pitch,
    )
    if not hypotheses:
        raise RuntimeError(
            f"{name}: no single linear registration places every labelled tick on an "
            f"observed line within {match_tol:.1f}px; the labels and the grid disagree"
        )
    hypotheses.sort(key=lambda h: (-h[0], h[1]))
    best = hypotheses[0]
    ambiguity = max(2.0, 0.1 * pitch)
    for other in hypotheses[1:]:
        if other[2] != best[2] and other[0] == best[0] and other[1] - best[1] < ambiguity:
            raise RuntimeError(
                f"{name}: two grid registrations explain the labels equally well "
                f"(mean label offset {best[1]:.1f}px vs {other[1]:.1f}px); refusing "
                "to pick a gridline by proximity alone"
            )
    kept = [tick for tick, i in zip(ticks, best[2]) if i >= 0]
    unlined = [tick for tick, i in zip(ticks, best[2]) if i < 0]
    matched = [i for i in best[2] if i >= 0]
    if len(kept) < 2:
        raise RuntimeError(f"{name}: fewer than 2 labelled ticks sit on observed lines")
    line_px = np.asarray([centers[i] for i in matched])
    kept_label_px = np.asarray([tick.pixel for tick in kept])
    if np.any(np.sign(np.diff(line_px)) != np.sign(np.diff(kept_label_px))):
        raise RuntimeError(f"{name}: matched gridlines disagree with the label order")
    return TickLineMatch(
        tuple(kept),
        tuple(float(c) for c in line_px),
        tuple(lines[i].source for i in matched),
        match_tol,
        tuple(unlined),
    )


GridVerdict = Literal["verified", "failed", "unverified"]


@dataclass(frozen=True)
class GridCheck:
    """Does a SERVED axis mapping land on the line each labelled tick names?

    ``verified``: every labelled tick that sits on an observed line is served
    within tolerance of that line. ``failed``: at least one misses. And
    ``unverified``: the lines could not be bound (no grid, too few ticks,
    ambiguous registration) -- never a pass; the caller must refuse, or carry
    the verdict and downgrade its own status.
    """

    status: GridVerdict
    reason: str
    tolerance_px: float | None
    ticks: tuple[dict[str, object], ...]

    @property
    def max_abs_error_px(self) -> float | None:
        errors = [abs(float(t["served_error_px"])) for t in self.ticks]
        return max(errors) if errors else None

    def payload(self) -> dict[str, object]:
        worst = self.max_abs_error_px
        return {
            "status": self.status,
            "reason": self.reason,
            "tolerance_px": None if self.tolerance_px is None else round(self.tolerance_px, 3),
            "max_abs_error_px": None if worst is None else round(worst, 3),
            "ticks": list(self.ticks),
        }

    def require(self, name: str) -> "GridCheck":
        """Raise unless verified: the fail-closed use of the verdict."""
        if self.status != "verified":
            raise RuntimeError(f"{name}: served calibration grid check {self.status}: {self.reason}")
        return self


def check_served_on_grid(
    gray: np.ndarray,
    served: NumericAxis,
    label_ticks: Sequence[AxisTick],
    *,
    orientation: Orientation,
    cross_span: tuple[float, float],
    name: str,
    ink_threshold: int = _INK_THRESHOLD,
    unlined_labels: UnlinedPolicy = "refuse",
) -> GridCheck:
    """Assert the SERVED value->pixel mapping at every labelled tick.

    *served* must be the mapping the caller actually serves values through
    (its ``model``/``m``/``b``; ticks are ignored), AFTER every later
    transform. *label_ticks* carry each consumed tick's VALUE and the pixel of
    its printed label; the label pixel is used only to identify which observed
    line the value names (``identify_tick_lines``), never as the reference.
    """
    try:
        match = identify_tick_lines(
            gray,
            label_ticks,
            model=served.model,
            orientation=orientation,
            cross_span=cross_span,
            name=name,
            ink_threshold=ink_threshold,
            unlined_labels=unlined_labels,
        )
    except (RuntimeError, ValueError) as exc:
        return GridCheck("unverified", str(exc), None, ())
    rows = []
    for tick, line_px, source in zip(match.ticks, match.line_px, match.sources):
        try:
            px = served_pixel(served, tick.value)
        except (ValueError, ZeroDivisionError) as exc:
            return GridCheck("unverified", f"{name}: served mapping cannot place {tick.value:g}: {exc}", match.tolerance_px, ())
        rows.append({
            "text": tick.text,
            "value": tick.value,
            "label_px": round(float(tick.pixel), 3),
            "line_px": round(line_px, 3),
            "line_source": source,
            "served_px": round(px, 3),
            "served_error_px": round(px - line_px, 3),
        })
    if not all(math.isfinite(float(r["served_error_px"])) for r in rows):
        return GridCheck("unverified", f"{name}: non-finite served pixel", match.tolerance_px, tuple(rows))
    worst = max(rows, key=lambda r: abs(float(r["served_error_px"])))
    unlined_note = (
        f"; identity-only labels without a line: {[t.text for t in match.unlined]}"
        if match.unlined else ""
    )
    if abs(float(worst["served_error_px"])) > match.tolerance_px:
        return GridCheck(
            "failed",
            f"{name}: served calibration misses the {worst['text']!r} line by "
            f"{float(worst['served_error_px']):+.2f}px (tolerance {match.tolerance_px:.2f}px)"
            + unlined_note,
            match.tolerance_px,
            tuple(rows),
        )
    return GridCheck(
        "verified",
        f"{name}: {len(rows)} labelled ticks served within {match.tolerance_px:.2f}px of their lines"
        + unlined_note,
        match.tolerance_px,
        tuple(rows),
    )


def _register(
    centers: np.ndarray,
    coords: np.ndarray,
    label_px: np.ndarray,
    candidates: list[list[int]],
    match_tol: float,
    model: str,
    *,
    optional: list[bool] | None = None,
    label_search: float = 0.0,
) -> list[tuple[int, float, tuple[int, ...]]]:
    """Enumerate affine label->line registrations; return (score, offset, lines).

    The end ticks' candidate lines define each hypothesis. Every labelled tick
    must then land on an observed line -- except an *optional* (identity-only)
    tick, which may land between lines (index -1) provided its label glyph
    sits within *label_search* of the predicted pixel. The score rewards predicted minor
    decade lines that exist and penalises observed lines inside the labelled
    span that the hypothesis cannot explain, so a registration shifted onto a
    family of minor lines loses to the one seated on the decades.
    """
    span = coords[-1] - coords[0]
    seen: set[tuple[int, ...]] = set()
    out: list[tuple[int, float, tuple[int, ...]]] = []
    for first in candidates[0]:
        for last in candidates[-1]:
            a, b = centers[first], centers[last]
            if b == a or np.sign(b - a) != np.sign(label_px[-1] - label_px[0]):
                continue
            scale = (b - a) / span
            predicted = a + (coords - coords[0]) * scale
            matched: list[int] = []
            for k, p in enumerate(predicted):
                i = int(np.argmin(np.abs(centers - p)))
                if abs(centers[i] - p) > match_tol:
                    # identity-only: the glyph sits where its value belongs AND
                    # no observed line is near the glyph -- a label with a rule
                    # right beside it that the registration does not use is
                    # evidence against the registration, not an unlined label
                    # (STP150N10F7AG: a decorative 22-division grid under
                    # 20 V labels)
                    if (
                        optional is not None
                        and optional[k]
                        and abs(label_px[k] - p) <= label_search
                        and float(np.min(np.abs(centers - label_px[k]))) > label_search
                    ):
                        matched.append(-1)
                        continue
                    break
                matched.append(i)
            else:
                key = tuple(matched)
                lined = [i for i in key if i >= 0]
                if key in seen or len(set(lined)) != len(lined):
                    continue
                seen.add(key)
                expected = list(predicted)
                hits = 0
                if model == "log10":
                    # the 2..9 minors of EVERY decade inside the labelled
                    # span, including one whose label was not read (an OCR
                    # gap such as TK100E08N1's missing "1"), so a gap does not
                    # leave its minor rules looking unexplained
                    c_lo, c_hi = float(np.min(coords)), float(np.max(coords))
                    for base in range(math.floor(c_lo + 1e-9), math.ceil(c_hi - 1e-9)):
                        for j in range(2, 10):
                            c = base + math.log10(j)
                            if not c_lo - 1e-9 <= c <= c_hi + 1e-9:
                                continue
                            p = a + (c - coords[0]) * scale
                            expected.append(p)
                            if np.min(np.abs(centers - p)) <= match_tol:
                                hits += 1
                        if base > c_lo + 1e-9 and not np.any(np.isclose(coords, base)):
                            expected.append(a + (base - coords[0]) * scale)
                lo_px, hi_px = sorted((a, b))
                expected_arr = np.asarray(expected)
                inside = [c for c in centers if lo_px + match_tol < c < hi_px - match_tol]
                unexplained = sum(
                    1 for c in inside if np.min(np.abs(expected_arr - c)) > match_tol
                )
                if (
                    model == "log10"
                    and len(inside) > 2 * len(coords)
                    and unexplained > _LOG_MINOR_UNEXPLAINED_MAX * len(inside)
                ):
                    # A minor grid is drawn, and this registration leaves most
                    # of it unexplained: it has seated the decades on a
                    # near-affine chain of minors (NTMFS3D0N08XT1G with its
                    # 1000 pF rule lost: 6000/700/80/9/1), not on the decades.
                    continue
                on_line = [k for k, i in enumerate(key) if i >= 0]
                offset = float(np.mean(np.abs(label_px[on_line] - centers[[key[k] for k in on_line]])))
                out.append((hits - unexplained, offset, key))
    return out


def detect_axis_lines(
    gray: np.ndarray,
    *,
    orientation: Orientation,
    along: tuple[float, float],
    cross_span: tuple[float, float],
    max_width: int,
    tick_band: int,
    ink_threshold: int = _INK_THRESHOLD,
) -> list[ObservedLine]:
    """Find gridlines (and frame-attached tick marks) crossing one axis.

    For ``orientation="x"`` the lines are vertical and *along* bounds their x
    positions; for ``"y"`` they are horizontal. A line's pixel is the centre
    of its dark run, so a thick frame and a hairline gridline are anchored the
    same way.
    """
    ink = gray < ink_threshold
    if orientation == "y":
        ink = ink.T  # rows become columns: lines are always "vertical" below
    height, width = ink.shape
    c0 = max(0, int(math.floor(cross_span[0])))
    c1 = min(height, int(math.ceil(cross_span[1])) + 1)
    a0 = max(0, int(math.floor(along[0])))
    a1 = min(width, int(math.ceil(along[1])) + 1)
    if c1 - c0 < 4 or a1 - a0 < 2:
        return []

    coverage = ink[c0:c1, a0:a1].mean(axis=0)
    lines = [
        ObservedLine(a0 + center, run_width, "gridline")
        for center, run_width in _runs(coverage >= _GRIDLINE_MIN_COVERAGE)
        if run_width <= max_width
    ]

    # x labels sit below the plot (high rows); y labels sit left of it, which
    # after the transpose is the low end of the cross span.
    label_end = c1 - 1 if orientation == "x" else c0
    frame = _frame_on_label_side(ink, label_end, (a0, a1), max_width)
    if frame is not None:
        top, bottom = frame
        bands = [ink[max(0, top - tick_band):top, a0:a1],
                 ink[bottom + 1:min(height, bottom + 1 + tick_band), a0:a1]]
        filled = np.zeros(a1 - a0, dtype=bool)
        for band in bands:
            if band.shape[0] == tick_band:
                filled |= band.mean(axis=0) >= _TICK_MARK_MIN_FILL
        for center, run_width in _runs(filled):
            position = a0 + center
            if run_width <= max_width and all(
                abs(position - line.center_px) > max_width for line in lines
            ):
                lines.append(ObservedLine(position, run_width, "tick_mark"))
    return sorted(lines, key=lambda line: line.center_px)


def _frame_on_label_side(
    ink: np.ndarray, label_end: int, along: tuple[int, int], max_width: int
) -> tuple[int, int] | None:
    """Return the (first, last) row of the frame rule at the label-side end.

    Tick marks hang off this rule; a chart with no rule there has no tick
    marks this detector can attribute, and returns ``None``.
    """
    a0, a1 = along
    row_coverage = ink[:, a0:a1].mean(axis=1)
    rules = [
        (center, run_width)
        for center, run_width in _runs(row_coverage >= 0.6)
        if run_width <= max_width
    ]
    if not rules:
        return None
    center, run_width = min(rules, key=lambda rule: abs(rule[0] - label_end))
    if abs(center - label_end) > 3 * max_width:
        return None
    first = int(round(center - (run_width - 1) / 2))
    return first, first + run_width - 1


def _runs(mask: np.ndarray) -> list[tuple[float, int]]:
    """Centres and widths of consecutive True runs in a 1-D mask."""
    runs: list[tuple[float, int]] = []
    start = None
    for index, flag in enumerate(list(mask) + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append(((start + index - 1) / 2.0, index - start))
            start = None
    return runs
