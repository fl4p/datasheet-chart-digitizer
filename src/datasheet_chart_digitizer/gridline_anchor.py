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

# Rules that render lighter than the default ink threshold (hairline grey
# grids) are retried at this threshold by line_evidence_attempts.
_LIGHT_RULE_INK_THRESHOLD = 225
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
# A registration's end-to-end span may differ from its labels' span by at
# most this fraction (NTMFS015N15MC, the largest legitimate case in the
# human-verified capacitance set: end glyphs pushed inward, 1.7 %).
_MAX_SCALE_DISAGREEMENT = 0.08
# On a log axis whose minor grid is drawn, a registration may leave at most
# this fraction of the observed lines inside the labelled span unexplained by
# its predicted decades + 2..9 minors.
_LOG_MINOR_UNEXPLAINED_MAX = 0.30
# A tick mark must be ink over this fraction of its band beside the frame.
_TICK_MARK_MIN_FILL = 0.80
# Opt-in major-rule evidence for a tied registration: every rule one
# hypothesis binds (and its rival does not) must carry at least this multiple
# of the ink per unit length of every rule the rival binds. Two, not a small
# margin: at 220 dpi an equal hairline straddling two pixel columns can read
# ~1.6x the ink of one seated on a column (set1 0002: minors 110..220 vs
# majors 135..390 -- NOT separable, stays refused). set1 0105 (solid majors
# 89..510 vs dotted minors 4..11) is the kind of evidence this admits.
_MAJOR_RULE_MASS_RATIO = 2.0
# Dotted/dashed rules (opt-in evidence level, see ``line_evidence_attempts``):
# a column is a broken rule when its ink covers at least this fraction of the
# plot span ...
_BROKEN_RULE_MIN_COVERAGE = 0.20
# ... AND that ink is spread over the WHOLE span: at least this share of the
# span's bins hold ink. A dot pitch is a few pixels, so every bin of a rule
# holds dots; a curve crossing, a steep segment or a text column fills a few
# bins only, however dark it is.
_BROKEN_RULE_BINS = 16
_BROKEN_RULE_MIN_BIN_SHARE = 0.9


class AmbiguousRegistration(RuntimeError):
    """Two registrations explain the labels equally well on this raster."""


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
    registration_evidence: str = "label_offset"

    @property
    def max_served_error_px(self) -> float:
        return max(abs(anchor.served_error_px) for anchor in self.anchors)

    def payload(self) -> dict[str, object]:
        return {
            "pixel_source": "observed_gridline_or_tick_mark",
            "label_role": "value_identity_only",
            "residual_basis": "observed_lines",
            "registration_evidence": self.registration_evidence,
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
    broken_rules: bool = False,
    major_rule_weight: bool = False,
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
        broken_rules=broken_rules,
        major_rule_weight=major_rule_weight,
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
    result = AnchoredAxis(
        axis, anchors, match.tolerance_px, match.unlined, match.registration_evidence
    )
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
    registration_evidence: str = "label_offset"


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
    broken_rules: bool = False,
    major_rule_weight: bool = False,
) -> TickLineMatch:
    """Bind each labelled tick to the observed gridline or tick mark it names.

    Label pixels only SEED the search; the binding is one affine registration
    of the whole labelled sequence onto observed lines (with the log minor
    pattern scored), so a label that sits nearer a minor line or a neighbour
    cannot pick it. Raises RuntimeError when the binding is not evidenced: a
    label with no line near it (unless *unlined_labels* admits it as
    identity-only), no registration that seats every tick, two equally good
    registrations, or line order disagreeing with label order.

    *major_rule_weight* (opt-in) lets POSITIVE evidence settle a tie that the
    label offset cannot: if every rule one tied registration binds is heavier
    (ink per unit length, ``_MAJOR_RULE_MASS_RATIO``) than every rule each
    rival binds, the labels name that registration's major rules. The
    ambiguity threshold itself is unchanged; without such a margin, or with a
    tick mark among the disputed lines, the tie still refuses.
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
        broken_rules=broken_rules,
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
    tied = [
        other for other in hypotheses[1:]
        if other[2] != best[2] and other[0] == best[0] and other[1] - best[1] < ambiguity
    ]
    evidence = "label_offset"
    if tied:
        heavier = (
            _heavier_rule_registration(
                gray, lines, [best, *tied], orientation, cross_span
            )
            if major_rule_weight
            else None
        )
        if heavier is None:
            other = tied[0]
            raise AmbiguousRegistration(
                f"{name}: two grid registrations explain the labels equally well "
                f"(mean label offset {best[1]:.1f}px vs {other[1]:.1f}px); refusing "
                "to pick a gridline by proximity alone"
            )
        best, evidence = heavier, "major_rule_weight"
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
        evidence,
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
    ambiguous: bool = False  # unverified because two registrations tie

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
    broken_rules: bool = False,
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
            broken_rules=broken_rules,
        )
    except AmbiguousRegistration as exc:
        return GridCheck("unverified", str(exc), None, (), ambiguous=True)
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


def _rule_mass(
    gray: np.ndarray,
    line: ObservedLine,
    orientation: Orientation,
    cross_span: tuple[float, float],
) -> float:
    """Typical ink (255 - grey) per unit length along one rule's run.

    Summed across the rule's width, so an anti-aliased rule split over two
    pixel columns weighs the same as one seated on a single column.
    """
    image = gray if orientation == "x" else gray.T
    c0 = max(0, int(math.floor(cross_span[0])))
    c1 = min(image.shape[0], int(math.ceil(cross_span[1])) + 1)
    half = int(math.ceil(line.width_px / 2.0)) + 1
    a0 = max(0, int(round(line.center_px)) - half)
    a1 = min(image.shape[1], int(round(line.center_px)) + half + 1)
    band = 255.0 - image[c0:c1, a0:a1].astype(float)
    # median over the run: a curve, label or crossing rule that overlaps a
    # few rows must not make a minor rule look like a major one
    return float(np.median(band.sum(axis=1))) if band.size else 0.0


def _heavier_rule_registration(gray, lines, tied, orientation, cross_span):
    """The one tied registration whose disputed rules all outweigh every rival's.

    Only lines a registration binds and its rival does not are compared, and
    only solid gridlines (a tick mark or dotted rule is not comparable ink);
    each side needs at least two such rules. Returns None (the tie stands)
    unless exactly one registration wins against every other by
    ``_MAJOR_RULE_MASS_RATIO``.
    """

    def masses(indices):
        rules = [i for i in indices if lines[i].source == "gridline"]
        if len(rules) < 2:
            return None
        return [_rule_mass(gray, lines[i], orientation, cross_span) for i in rules]

    winners = []
    for candidate in tied:
        wins = True
        for rival in tied:
            if rival is candidate:
                continue
            own_lines = {i for i in candidate[2] if i >= 0}
            rival_lines = {i for i in rival[2] if i >= 0}
            mine = masses(sorted(own_lines - rival_lines))
            theirs = masses(sorted(rival_lines - own_lines))
            if not mine or not theirs or min(mine) < _MAJOR_RULE_MASS_RATIO * max(theirs):
                wins = False
                break
        if wins:
            winners.append(candidate)
    return winners[0] if len(winners) == 1 else None


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
            label_span = label_px[-1] - label_px[0]
            if abs((b - a) / label_span - 1.0) > _MAX_SCALE_DISAGREEMENT:
                # Labels are typeset at their values plus a glyph offset; that
                # offset cannot grow along the axis. A registration whose span
                # differs from the labels' by more than this has bound them to
                # other lines (TPN19008QM VGS: 0/4/8 V labels on lines 25 %
                # short of the label span, offsets -26.8..+24.8 px).
                continue
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
                # Unexplained lines only count against a LOG registration,
                # whose minors it predicts. On a linear axis unlabelled rules
                # between labels are ordinary, and counting them rewarded a
                # shorter span (IRF644S: "10" bound to a minor 10 px short of
                # its rule); there the label offset alone ranks hypotheses and
                # the ambiguity gate refuses near-ties.
                score = hits - unexplained if model == "log10" else 0
                out.append((score, offset, key))
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
    broken_rules: bool = False,
) -> list[ObservedLine]:
    """Find gridlines (and frame-attached tick marks) crossing one axis.

    For ``orientation="x"`` the lines are vertical and *along* bounds their x
    positions; for ``"y"`` they are horizontal. A line's pixel is the centre
    of its dark run, so a thick frame and a hairline gridline are anchored the
    same way.

    *broken_rules* also admits DOTTED and DASHED rules (source
    ``"broken_rule"``) from per-column ink+span evidence: coverage below the
    solid-gridline floor but ink in nearly every bin of the span. Off by
    default; ``line_evidence_attempts`` enables it only for callers that opt
    in, after every solid-rule level has failed to bind.
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
    if broken_rules:
        lines.extend(_broken_rule_lines(ink[c0:c1, a0:a1], a0, coverage, lines, max_width))

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


def _broken_rule_lines(
    ink: np.ndarray,
    offset: int,
    coverage: np.ndarray,
    solid: list[ObservedLine],
    max_width: int,
) -> list[ObservedLine]:
    """Dotted/dashed rules in *ink* (cross span x along), not near a solid line."""
    bins = np.array_split(np.arange(ink.shape[0]), _BROKEN_RULE_BINS)
    if min(len(b) for b in bins) < 2:
        return []
    per_bin = np.stack([ink[b].any(axis=0) for b in bins])
    spread = per_bin.mean(axis=0) >= _BROKEN_RULE_MIN_BIN_SHARE
    candidate = (coverage >= _BROKEN_RULE_MIN_COVERAGE) & spread
    found: list[ObservedLine] = []
    for center, run_width in _runs(candidate):
        position = offset + center
        # a column run adjacent to a solid run is the same rule's fringe;
        # anything further away is a distinct rule (a dotted decade sits one
        # 90 % minor, ~8 px, from a solid minor on a dense log grid)
        if run_width <= max_width and all(
            abs(position - line.center_px) > (line.width_px + run_width) / 2 + 1
            for line in solid
        ):
            found.append(ObservedLine(position, run_width, "broken_rule"))
    return found


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


def suppress_curve_ink(
    gray: np.ndarray, frame, *, dark: int = 90, rule_coverage: float = 0.8, pad: int = 3
) -> np.ndarray:
    """A copy of ``gray`` with curve ink removed inside the frame, rules kept.

    *frame* is anything with ``x0/y0/x1/y1`` pixel attributes (the plot frame).

    A flat curve lying along a gridline for a large part of the plot joins
    that gridline's detected run and drags its centre by a pixel or more
    (AON6276: the Ciss plateau on the 5000 pF rule moved it 2.5 px).  Curves
    are darker than grey gridlines; dark rows or columns that span the frame
    (black frame rails, black gridlines) are kept, every other dark pixel in
    the frame is painted white before the gridlines are measured.
    """
    out = gray.copy()
    x0, x1 = int(math.floor(frame.x0)) - pad, int(math.ceil(frame.x1)) + pad
    y0, y1 = int(math.floor(frame.y0)) - pad, int(math.ceil(frame.y1)) + pad
    sub = out[y0:y1 + 1, x0:x1 + 1]
    is_dark = sub < dark
    keep = np.zeros_like(is_dark)
    keep[is_dark.mean(axis=1) >= rule_coverage, :] = True
    keep[:, is_dark.mean(axis=0) >= rule_coverage] = True
    sub[is_dark & ~keep] = 255
    return out

def line_evidence_attempts(gray: np.ndarray, frame, *, broken_rules: bool = False):
    """Line-evidence levels for anchoring, most trustworthy first.

    Yields lists of ``(name, image, kwargs)`` variants for
    ``anchor_axis_on_grid`` / ``check_served_on_grid``; the variants of one
    level are two rasters of the same chart. Curve ink suppressed: a steep
    curve hugging a frame, or a plateau lying on a rule, widens or drags that
    line's detected run. Raw: suppression keeps only near-full dark rules, so
    a black rule broken by label boxes is erased with the curves (IRF644S's
    10^1 rule). Neither is trusted alone when both bind (see
    ``anchor_axis_on_grid_attempts``). The first levels require every
    labelled tick to sit on a line; the later ones admit INTERIOR labels
    between gridlines as identity-only while the end labels must still sit on
    lines.

    *broken_rules* (opt-in) appends the same levels again with dotted/dashed
    rules admitted as line evidence. They come LAST, so a chart that binds on
    solid rules today is decided exactly as before; only a chart with no
    solid-rule binding can be seated on its dotted rules.
    """
    images = (("curve_ink_suppressed", suppress_curve_ink(gray, frame)), ("raw", gray))
    for broken in (False, True) if broken_rules else (False,):
        suffix = "/broken_rules" if broken else ""
        for policy in ("refuse", "identity_only"):
            for threshold in (_INK_THRESHOLD, _LIGHT_RULE_INK_THRESHOLD):
                kwargs: dict[str, object] = {"unlined_labels": policy, "ink_threshold": threshold}
                if broken:
                    kwargs["broken_rules"] = True
                yield [
                    (f"{image_name}/{policy}/ink<{threshold}{suffix}", image, kwargs)
                    for image_name, image in images
                ]


def _joined(errors: list[tuple[str, str]]) -> str:
    """Distinct refusal reasons, each with the first attempt that gave it."""
    seen: dict[str, str] = {}
    for attempt, message in errors:
        seen.setdefault(message, attempt)
    return "; ".join(f"[{attempt}] {message}" for message, attempt in seen.items())


def _bindings_disagree(first: dict[float, float], second: dict[float, float], tol: float) -> bool:
    shared = set(first) & set(second)
    return any(abs(first[v] - second[v]) > tol for v in shared) or not shared


def anchor_axis_on_grid_attempts(
    gray: np.ndarray,
    label_axis: NumericAxis,
    *,
    frame,
    orientation: Orientation,
    cross_span: tuple[float, float],
    name: str,
    broken_rules: bool = False,
) -> tuple[AnchoredAxis, str]:
    """``anchor_axis_on_grid`` over ``line_evidence_attempts``.

    The first level at which a raster variant binds the labels decides. If
    both variants of that level bind but put a labelled value on different
    lines, the raster evidence is contradictory and the axis is refused;
    raises with every distinct refusal reason when nothing binds.
    """
    errors: list[tuple[str, str]] = []
    for level in line_evidence_attempts(gray, frame, broken_rules=broken_rules):
        bound: list[tuple[AnchoredAxis, str]] = []
        ambiguous: list[str] = []
        for attempt, image, kwargs in level:
            try:
                bound.append((anchor_axis_on_grid(
                    image, label_axis, orientation=orientation, cross_span=cross_span,
                    name=name, **kwargs,  # type: ignore[arg-type]
                ), attempt))
            except AmbiguousRegistration as exc:
                ambiguous.append(f"[{attempt}] {exc}")
                errors.append((attempt, str(exc)))
            except RuntimeError as exc:
                errors.append((attempt, str(exc)))
        if not bound:
            continue
        if ambiguous:
            # One raster binds uniquely only because the other shows a second,
            # equally good registration: the unique one lost evidence (a black
            # rule broken by label boxes is erased by curve-ink suppression).
            raise AmbiguousRegistration(
                f"{name}: {bound[0][1]} binds the labels, but " + "; ".join(ambiguous)
            )
        if len(bound) == 2:
            (first, first_name), (second, second_name) = bound
            lines = [{a.value: a.line_px for a in b.anchors} for b, _ in bound]
            if _bindings_disagree(lines[0], lines[1], first.tolerance_px):
                raise RuntimeError(
                    f"{name}: {first_name} and {second_name} bind the labels to different "
                    f"lines ({lines[0]} vs {lines[1]}); the raster evidence is contradictory"
                )
        return bound[0]
    raise RuntimeError(_joined(errors))


def check_served_on_grid_attempts(
    gray: np.ndarray,
    served: NumericAxis,
    label_ticks: Sequence[AxisTick],
    *,
    frame,
    orientation: Orientation,
    cross_span: tuple[float, float],
    name: str,
    broken_rules: bool = False,
) -> GridCheck:
    """``check_served_on_grid`` over ``line_evidence_attempts``.

    The first level at which a raster variant can BIND the labels gives the
    verdict; if both variants bind but to different lines the result is
    unverified (contradictory evidence). Only if nothing binds is it
    unverified for lack of lines; the reason then lists every refusal.
    """
    errors: list[tuple[str, str]] = []
    for level in line_evidence_attempts(gray, frame, broken_rules=broken_rules):
        bound: list[tuple[GridCheck, str]] = []
        ambiguous: list[str] = []
        for attempt, image, kwargs in level:
            check = check_served_on_grid(
                image, served, label_ticks, orientation=orientation, cross_span=cross_span,
                name=name, **kwargs,  # type: ignore[arg-type]
            )
            if check.status == "unverified":
                errors.append((attempt, check.reason))
                if check.ambiguous:
                    ambiguous.append(f"[{attempt}] {check.reason}")
            else:
                bound.append((check, attempt))
        if not bound:
            continue
        if ambiguous:
            return GridCheck(
                "unverified",
                f"{name}: {bound[0][1]} binds the labels, but " + "; ".join(ambiguous),
                None,
                (),
            )
        if len(bound) == 2:
            lines = [{float(t["value"]): float(t["line_px"]) for t in c.ticks} for c, _ in bound]
            if _bindings_disagree(lines[0], lines[1], float(bound[0][0].tolerance_px or 0.0)):
                return GridCheck(
                    "unverified",
                    f"{name}: {bound[0][1]} and {bound[1][1]} bind the labels to different "
                    f"lines ({lines[0]} vs {lines[1]}); the raster evidence is contradictory",
                    None,
                    (),
                )
        check, attempt = bound[0]
        return GridCheck(check.status, f"{check.reason} [{attempt}]", check.tolerance_px, check.ticks)
    return GridCheck("unverified", _joined(errors), None, ())
