"""Review-grade retrace of MOSFET C(V) charts (Ciss/Coss/Crss vs VDS).

Fifteen capacitance charts were human-GREEN on overlays that the production
extractor had traced wrongly (re-examined 2026-09-29):

* Crss traced along the 0 pF frame rail while the real Crss was never traced
  (AO, NCE linear panels);
* Ciss and Coss swapped where they genuinely cross (NXP log panels);
* a plot box that took in the neighbouring gate-charge panel, or stopped one
  or two gridlines short of the frame;
* curves that stop short of the frame, hook onto a label arrow, or step onto
  a neighbouring curve.

This module rebuilds a chart from source evidence instead of reusing those
decisions:

* **Geometry.**  Vector curves are read from their own PDF paths: stroked
  polylines are served as drawn; curves drawn as filled outlines are served on
  the centre of their outline at every x (``vector_curve_paths``).  Raster
  curves are tracked stroke by stroke from a seed column where every curve is
  isolated, and tracking stops -- it never guesses -- where a stroke merges
  with the frame, a label or another curve (``track_raster_curves``).  A
  sustained merge of two curves is kept only as an explicitly *shared* span.
* **Frame.**  The frame is the outermost full-span rule on each side of a
  hint box (``own_frame``); a side without such a rule refuses.
* **Identity.**  Crss is the curve that lies lowest at every shared VDS
  (Coss = Cds + Cgd and Ciss = Cgs + Cgd both exceed Crss = Cgd).  Ciss and
  Coss may cross, so they are bound by the printed labels: a label joined to
  a curve by a leader line binds to it, otherwise to the curve nearest the
  label, and the binding must be one-to-one and decisive
  (``bind_labels``).
* **Resolution.**  On a linear capacitance axis a curve that runs within a
  few pixels of the 0 pF rail cannot be told apart from that rail
  (``below_resolution_mask``); such points are flagged, and raster points
  there are not served.

Axis calibration is done by :mod:`gridline_anchor` (labels identify values,
observed gridlines carry every served pixel).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from .capacitance_vector import _is_dark_stroke, _sample_cubic
from .gridline_anchor import suppress_curve_ink  # noqa: F401  (moved; re-exported)
from .transfer_retrace import _clip_polyline

CURVE_NAMES = ("Ciss", "Coss", "Crss")
# A curve closer than this to the 0 pF rail of a linear axis is not resolved
# by the raster (CHART-REVIEW-CHECKLIST.md section 2: "below ~4 px").
MIN_RESOLVABLE_PX = 4.0
# Label binding must be decisive: the runner-up curve must be this much
# further from the label than the bound curve.
_LABEL_MIN_SEPARATION = 1.6
_LEADER_ATTACH_PT = 1.5
# Vendor curve strokes are 0.8..2.2 pt; gridlines/leaders are thinner.
_MIN_CURVE_WIDTH_PT = 0.8
_MAX_CURVE_WIDTH_PT = 2.2
_JOIN_PT = 0.05


# --------------------------------------------------------------------- frame


@dataclass(frozen=True)
class Frame:
    """The chart's own frame in image pixels (rule centres)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def _line_centres(coverage: np.ndarray, minimum: float) -> list[tuple[float, int]]:
    centres: list[tuple[float, int]] = []
    start = None
    for index, flag in enumerate(list(coverage >= minimum) + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            centres.append(((start + index - 1) / 2.0, index - start))
            start = None
    return centres


def own_frame(
    gray: np.ndarray,
    hint: tuple[float, float, float, float],
    *,
    search_px: int = 14,
    ink_threshold: int = 225,
    min_coverage: float = 0.85,
    max_rule_width: int = 8,
) -> Frame:
    """Locate the frame rules nearest each edge of ``hint`` (x0, y0, x1, y1).

    A frame side is a rule that covers at least ``min_coverage`` of the hint's
    span on the other axis, within ``search_px`` of the hint edge.  The
    outermost such rule wins, so a hint that stops one gridline short of the
    frame is extended to the frame -- but never across the whitespace that
    separates a neighbouring panel (its rules do not span this hint).
    """
    x0, y0, x1, y1 = (int(round(v)) for v in hint)
    ink = gray < ink_threshold
    height, width = ink.shape

    def side(vertical: bool, edge: int, outward: int) -> float:
        lo = max(0, edge - search_px)
        hi = min((width if vertical else height) - 1, edge + search_px)
        if vertical:
            coverage = ink[y0:y1 + 1, lo:hi + 1].mean(axis=0)
        else:
            coverage = ink[lo:hi + 1, x0:x1 + 1].mean(axis=1)
        rules = [
            (lo + centre, run) for centre, run in _line_centres(coverage, min_coverage)
            if run <= max_rule_width
        ]
        if not rules:
            raise RuntimeError(
                f"no frame rule within {search_px}px of the {'vertical' if vertical else 'horizontal'} "
                f"hint edge at {edge}px"
            )
        return float(max(rules)[0] if outward > 0 else min(rules)[0])

    return Frame(
        x0=side(True, x0, -1),
        y0=side(False, y0, -1),
        x1=side(True, x1, +1),
        y1=side(False, y1, +1),
    )


# ------------------------------------------------------------ raster curves


@dataclass
class TrackedCurve:
    """A raster curve tracked column by column (image pixels).

    Coordinates are pixel-INDEX coordinates (pixel ``i`` has its centre at
    ``i``), the convention of :mod:`gridline_anchor`'s line centres.  A PDF
    point ``p`` at ``s`` px/pt is at index coordinate ``p * s - 0.5``.

    ``status`` per column: ``"ok"`` (an isolated stroke), ``"shared"`` (one
    stroke shared with ``shared_with``), ``"touching"`` (two strokes touching;
    the centre is half a stroke inside the curve's own edge) or
    ``"unresolved"`` (tracking continued by prediction only; NOT served).
    """

    xs: list[int] = field(default_factory=list)
    ys: list[float] = field(default_factory=list)
    status: list[str] = field(default_factory=list)
    shared_with: list[int | None] = field(default_factory=list)
    stop_reasons: dict[str, str] = field(default_factory=dict)

    def served(self) -> list[tuple[int, float, str]]:
        return [
            (x, y, s) for x, y, s in zip(self.xs, self.ys, self.status) if s != "unresolved"
        ]


def column_runs(mask: np.ndarray, x: int, y0: int, y1: int, bridge: int = 0) -> list[tuple[int, int]]:
    """Dark runs (first row, last row) in column ``x`` between y0 and y1."""
    col = mask[y0:y1 + 1, x]
    rows = np.flatnonzero(col)
    if not len(rows):
        return []
    runs = []
    start = prev = int(rows[0])
    for r in rows[1:]:
        r = int(r)
        if r - prev > bridge + 1:
            runs.append((y0 + start, y0 + prev))
            start = r
        prev = r
    runs.append((y0 + start, y0 + prev))
    return runs


def _seed_columns(mask, frame: Frame, count: int, max_run: float, min_gap: float) -> list[int]:
    fx0, fx1 = int(math.ceil(frame.x0)) + 3, int(math.floor(frame.x1)) - 3
    fy0, fy1 = int(math.ceil(frame.y0)) + 3, int(math.floor(frame.y1)) - 3
    good = []
    for x in range(fx0, fx1 + 1):
        runs = column_runs(mask, x, fy0, fy1)
        if len(runs) != count or any(b - a + 1 > max_run for a, b in runs):
            continue
        if any(runs[i + 1][0] - runs[i][1] < min_gap for i in range(len(runs) - 1)):
            continue
        good.append(x)
    return good


def track_raster_curves(
    gray: np.ndarray,
    frame: Frame,
    *,
    count: int = 3,
    ink_threshold: int = 100,
    stroke_px: float | None = None,
    seed_x: int | None = None,
    max_gap: int = 12,
    frame_margin_px: int = 2,
    min_span_fraction: float = 0.4,
) -> tuple[list[TrackedCurve], int]:
    """Track ``count`` dark strokes left and right from an isolating seed column.

    Curves are indexed top-to-bottom at the seed.  In each next column a
    curve takes the run that continues its previous run (overlap, or a
    one-pixel gap).  Where two curves continue into the same run and the run
    is no taller than a single stroke would be, the span is ``shared``; where
    the continuing run is too tall for the stroke (a label, arrow, frame or
    a second curve entering) or no run continues, the column is
    ``unresolved`` and after ``max_gap`` such columns the curve stops on that
    side.  Columns within ``frame_margin_px`` of a frame rule are never read:
    a curve that meets the frame has ended.
    """
    mask = gray < ink_threshold
    fy0 = int(math.ceil(frame.y0)) + frame_margin_px
    fy1 = int(math.floor(frame.y1)) - frame_margin_px
    xmin = int(math.ceil(frame.x0)) + frame_margin_px
    xmax = int(math.floor(frame.x1)) - frame_margin_px
    if stroke_px is None:
        widths = []
        for x in range(xmin, xmax + 1, 3):
            widths += [b - a + 1 for a, b in column_runs(mask, x, fy0, fy1)]
        if not widths:
            raise RuntimeError("no curve ink inside the frame")
        stroke_px = float(np.median(widths))
    max_run = 2.5 * stroke_px + 1.0  # seed columns only
    geometry = (mask, xmin, xmax, fy0, fy1, stroke_px, max_run, max_gap, count)
    if seed_x is not None:
        return _track_from_seed(geometry, seed_x), seed_x
    seeds = _seed_columns(mask, frame, count, max_run, min_gap=2.0 * stroke_px)
    if not seeds:
        raise RuntimeError(f"no column isolates {count} curve strokes; refusing to seed identities")
    # A seed column can hold a label glyph instead of a curve.  Take the
    # isolating column nearest the plot centre whose strokes ALL track across
    # at least ``min_span_fraction`` of the frame -- a glyph does not.
    centre = (frame.x0 + frame.x1) / 2.0
    for candidate in sorted(seeds, key=lambda x: abs(x - centre)):
        curves = _track_from_seed(geometry, candidate)
        if min(max(c.xs) - min(c.xs) for c in curves) >= min_span_fraction * frame.width:
            return curves, candidate
    raise RuntimeError("no seed column yields curves that all span the plot; refusing to guess identities")


def _track_from_seed(geometry, seed_x: int) -> list[TrackedCurve]:
    mask, xmin, xmax, fy0, fy1, stroke_px, max_run, max_gap, count = geometry
    runs = column_runs(mask, seed_x, fy0, fy1)
    if len(runs) != count:
        raise RuntimeError(f"seed column {seed_x} holds {len(runs)} strokes, need {count}")
    curves = [TrackedCurve() for _ in range(count)]
    for curve, run in zip(curves, runs):
        curve.xs.append(seed_x)
        curve.ys.append((run[0] + run[1]) / 2.0)
        curve.status.append("ok")
        curve.shared_with.append(None)

    for direction in (-1, +1):
        side = "low" if direction < 0 else "high"
        last_run = {i: runs[i] for i in range(count)}
        last_y = {i: (runs[i][0] + runs[i][1]) / 2.0 for i in range(count)}
        slope = {i: 0.0 for i in range(count)}
        raw_slope = {i: 0.0 for i in range(count)}
        heights = {i: [runs[i][1] - runs[i][0] + 1] for i in range(count)}
        gap = {i: 0 for i in range(count)}
        alive = set(range(count))
        x = seed_x
        while alive:
            x += direction
            if x < xmin or x > xmax:
                for i in alive:
                    curves[i].stop_reasons.setdefault(side, "reached the frame")
                break
            column = column_runs(mask, x, fy0, fy1)
            claims: dict[int, list[int]] = {}
            for i in sorted(alive):
                a, b = last_run[i]
                predicted = last_y[i] + slope[i]
                cands = [k for k, (c, d) in enumerate(column) if c <= b + 2 and d >= a - 2]
                if not cands:
                    cands = [k for k, (c, d) in enumerate(column) if c - 1 <= predicted <= d + 1]
                if len(cands) > 1:
                    cands = [min(cands, key=lambda k: abs((column[k][0] + column[k][1]) / 2 - predicted))]
                if cands:
                    claims.setdefault(cands[0], []).append(i)
            taken = {i for owners in claims.values() for i in owners}
            for i in sorted(alive):
                if i in taken:
                    continue
                gap[i] += 1
                _unresolved(curves[i], x, last_y[i] + slope[i] * gap[i])
            for k, owners in claims.items():
                c, d = column[k]
                height = d - c + 1
                centres = _owner_centres(owners, c, d, last_y, stroke_px)
                for i in owners:
                    # A stroke crossing a column at slope s is about
                    # w + |s| tall; a label, arrowhead or second curve
                    # merged into the run makes it taller than that.
                    steepness = max(abs(slope[i]), abs(raw_slope[i]))
                    allowed = 1.3 * stroke_px + 1.0 + 1.5 * steepness
                    # Also compare with the stroke's own recent thickness: an
                    # arrow tip that adds 2-3 px is below the global bound.
                    recent = float(np.median(heights[i][-8:])) if heights[i] else stroke_px
                    allowed = min(allowed, max(recent + max(2.0, 0.4 * stroke_px) + 1.5 * steepness, stroke_px + 2.0))
                    tall = len(owners) == 1 and height > allowed and not _is_steep_continuation(
                        mask, x, direction, (c, d), last_y[i], last_run[i], stroke_px, fy0, fy1, xmin, xmax
                    )
                    # a run cut by the unread margin rows continues into a frame
                    # rail: the curve is merged with the rail there (Crss -> 0 pF)
                    on_rail = c <= fy0 or d >= fy1
                    if centres is None or tall or on_rail:
                        gap[i] += 1
                        _unresolved(curves[i], x, last_y[i] + slope[i] * gap[i])
                        continue
                    centre, status = centres[i]
                    new_slope = (centre - last_y[i]) / (gap[i] + 1)
                    gap[i] = 0
                    raw_slope[i] = new_slope
                    if len(owners) == 1:
                        heights[i].append(height)
                    slope[i] = 0.7 * slope[i] + 0.3 * new_slope
                    last_y[i] = centre
                    last_run[i] = (c, d)
                    curves[i].xs.append(x)
                    curves[i].ys.append(centre)
                    others = [j for j in owners if j != i]
                    curves[i].status.append(status)
                    curves[i].shared_with.append(others[0] if others else None)
            for i in list(alive):
                if gap[i] > max_gap:
                    alive.discard(i)
                    curves[i].stop_reasons[side] = (
                        f"stroke lost at x={x - direction * gap[i]}px: no isolated continuation "
                        f"for {gap[i]} columns (merge with frame/label/other curve)"
                    )
                    _trim_trailing_unresolved(curves[i])
    for curve in curves:
        order = np.argsort(curve.xs)
        for attr in ("xs", "ys", "status", "shared_with"):
            setattr(curve, attr, [getattr(curve, attr)[k] for k in order])
    return curves


def _is_steep_continuation(mask, x, direction, run, last_y, last_run, stroke_px, fy0, fy1, xmin, xmax,
                           lookahead: int = 10) -> bool:
    """Tell a steep stretch of the curve from ink merged into it.

    A run taller than the stroke's own slope explains is either the curve
    turning steep (a knee, a step, a low-VDS head) or a label, arrowhead or
    other curve touching it.  Follow the run's continuation for up to
    ``lookahead`` columns until the stroke is stroke-thin again: a steep
    stretch resumes AWAY from where the curve was, merged ink lets the curve
    resume where it was.  With no thin resumption in reach, accept only a
    run that grew gradually from the previous one (a steepening head).
    """
    normal = 1.4 * stroke_px + 1.0
    top, bottom = run
    previous_height = last_run[1] - last_run[0] + 1
    for step in range(1, lookahead + 1):
        xx = x + direction * step
        if xx < xmin or xx > xmax:
            break
        nxt = [r for r in column_runs(mask, xx, fy0, fy1) if r[0] <= bottom + 1 and r[1] >= top - 1]
        if len(nxt) != 1:
            break
        top, bottom = nxt[0]
        if bottom - top + 1 <= normal:
            resumed = (top + bottom) / 2.0
            return abs(resumed - last_y) > max(2.0 * stroke_px, 1.5 * step)
    return (run[1] - run[0] + 1) <= 1.3 * previous_height + 2.0


def _owner_centres(owners, top, bottom, last_y, stroke_px):
    """Centre of each curve inside one dark run, or ``None`` when unknowable.

    One owner: the run centre.  Two owners: a run no taller than ~1.4 strokes
    is ONE stroke both curves share (``shared``); a run up to ~2.6 strokes
    tall is two strokes touching, so each centre sits half a stroke inside
    its own edge (``touching``, the upper curve on the upper edge).  Anything
    taller, or three owners, cannot be split.
    """
    height = bottom - top + 1
    centre = (top + bottom) / 2.0
    if len(owners) == 1:
        return {owners[0]: (centre, "ok")}
    if len(owners) != 2:
        return None
    if height <= 1.4 * stroke_px:
        return {i: (centre, "shared") for i in owners}
    if height <= 2.6 * stroke_px + 1.0:
        upper, lower = sorted(owners, key=lambda i: last_y[i])
        half = stroke_px / 2.0
        return {upper: (top + half - 0.5, "touching"), lower: (bottom - half + 0.5, "touching")}
    return None


def _unresolved(curve: TrackedCurve, x: int, y: float) -> None:
    curve.xs.append(x)
    curve.ys.append(float(y))
    curve.status.append("unresolved")
    curve.shared_with.append(None)


def _trim_trailing_unresolved(curve: TrackedCurve) -> None:
    while curve.status and curve.status[-1] == "unresolved":
        for attr in ("xs", "ys", "status", "shared_with"):
            getattr(curve, attr).pop()


# ---------------------------------------------------------------- resolution


def below_resolution_mask(
    ys_px: Sequence[float], zero_px: float, *, minimum_px: float = MIN_RESOLVABLE_PX
) -> list[bool]:
    """True where a curve lies within ``minimum_px`` of the 0-value rail.

    Only meaningful on a LINEAR capacitance axis whose frame bottom is 0 pF:
    a stroke that close to the rail merges with it, so a raster cannot say
    where the curve is, and a raster "trace" there is the rail itself.
    """
    return [abs(zero_px - y) < minimum_px for y in ys_px]


# ------------------------------------------------------------- tick labels

_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")


def pdf_tick_labels(
    page, frame_pt, orientation: str, *, band_pt: float = 14.0, gap_pt: float = 8.0
) -> list[tuple[str, float, float, float]]:
    """Numeric tick labels printed beside ``frame_pt`` in the PDF text layer.

    Returns ``(text, value, centre_x_pt, centre_y_pt)``.  An x label's top
    must lie within ``gap_pt`` below the frame, a y label's right edge within
    ``gap_pt`` left of it, so a neighbouring panel's axis cannot contribute.  A decade
    printed as ``10`` with a raised, smaller exponent span is read as
    ``10**exponent`` (``10`` + ``-1`` -> 0.1; never the decimal 10-1 or 102);
    the label's position is the centre of its base ``10`` so the exponent's
    width does not shift it.
    """
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                text = span["text"].strip()
                if text:
                    spans.append((text, span["size"], tuple(float(v) for v in span["bbox"])))

    def inside(b):
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if orientation == "x":
            return 0.0 <= b[1] - frame_pt.y1 <= gap_pt and frame_pt.x0 - band_pt <= cx <= frame_pt.x1 + band_pt
        return 0.0 <= frame_pt.x0 - b[2] <= gap_pt and frame_pt.y0 - band_pt / 2 <= cy <= frame_pt.y1 + band_pt / 2

    # Pair every "10" with its raised exponent FIRST, so an exponent is never
    # read as a number of its own; then keep the labels owned by this frame.
    near = frame_pt + (-6 * band_pt, -band_pt, band_pt, 2 * band_pt)
    spans = [sp for sp in spans if near.contains(fitz_rect_centre(sp[2]))]
    out, used = [], set()
    for i, (text, size, bbox) in enumerate(spans):
        if text.translate(_SUPERSCRIPT) != "10":
            continue
        for j, (etext, esize, ebox) in enumerate(spans):
            exponent = etext.translate(_SUPERSCRIPT)
            if (
                j != i and j not in used
                and esize < size
                and abs(ebox[0] - bbox[2]) < 1.0
                and ebox[1] < bbox[1] + 0.5
                and bbox[1] - 0.5 * (bbox[3] - bbox[1]) <= (ebox[1] + ebox[3]) / 2 <= (bbox[1] + bbox[3]) / 2
                and exponent.lstrip("-").isdigit()
            ):
                used.update((i, j))
                combined = (bbox[0], min(bbox[1], ebox[1]), ebox[2], max(bbox[3], ebox[3]))
                if inside(combined):
                    out.append((f"10^{exponent}", 10.0 ** int(exponent), (bbox[0] + bbox[2]) / 2.0,
                                (bbox[1] + bbox[3]) / 2.0))
                break
    for i, (text, _size, bbox) in enumerate(spans):
        if i in used or not inside(bbox):
            continue
        try:
            value = float(text.translate(_SUPERSCRIPT))
        except ValueError:
            continue
        out.append((text, value, (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0))
    return sorted(out, key=lambda label: label[2] if orientation == "x" else label[3])


_SUBSCRIPT_NAMES = {"iss": "Ciss", "oss": "Coss", "rss": "Crss"}


def pdf_curve_labels(page, frame_pt) -> list[CurveLabel]:
    """Printed ``Ciss``/``Coss``/``Crss`` labels inside (or just right of) the frame.

    Read from the text layer as one word (``Ciss``) or as a ``C`` span
    followed by a subscript span (``iss``).  Duplicate text runs (some PDFs
    draw every label twice) collapse to one label per name only when their
    boxes coincide; two different boxes for one name refuse.
    """
    region = frame_pt + (0.0, 0.0, 40.0, 0.0)
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                text = span["text"].strip()
                if text and region.contains(fitz_rect_centre(span["bbox"])):
                    spans.append((text, tuple(float(v) for v in span["bbox"])))
    found: dict[str, tuple[float, float, float, float]] = {}

    def add(name, bbox):
        if name in found and max(abs(a - b) for a, b in zip(found[name], bbox)) > 0.5:
            raise RuntimeError(f"two different printed {name} labels")
        found[name] = bbox

    for i, (text, bbox) in enumerate(spans):
        key = text.lower()
        if key in ("ciss", "coss", "crss"):
            add("C" + key[1:], bbox)
        elif text == "C":
            for other, obox in spans:
                name = _SUBSCRIPT_NAMES.get(other.lower())
                if name and abs(obox[0] - bbox[2]) < 1.0 and abs(obox[3] - bbox[3]) < 3.0:
                    add(name, (bbox[0], min(bbox[1], obox[1]), obox[2], max(bbox[3], obox[3])))
    arrows = [
        d for d in page.get_drawings()
        if d.get("type") == "f" and _is_dark_stroke(d.get("fill"))
        and 3 <= len(d.get("items", [])) <= 30 and max(d["rect"].width, d["rect"].height) <= 60
        and region.contains(d["rect"])
    ]
    labels = []
    for name, bbox in sorted(found.items()):
        centre = fitz_rect_centre(bbox)
        tip = None
        for arrow in arrows:
            vertices = [
                (float(p.x), float(p.y)) for item in arrow["items"] for p in item[1:] if hasattr(p, "x")
            ]
            if not vertices or min(_bbox_distance(bbox, q) for q in vertices) > 3.0:
                continue
            if tip is not None:
                raise RuntimeError(f"two arrows start at the {name} label")
            tip = max(vertices, key=lambda q: math.dist(q, centre))
        labels.append(CurveLabel(name, bbox, tip))
    return labels


def fitz_rect_centre(bbox) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def leader_lines(page, frame_pt, *, max_width_pt: float = 0.6, max_length_pt: float = 40.0):
    """Thin straight strokes in/near the frame: label leader lines and arrows."""
    region = frame_pt + (-2.0, -2.0, 40.0, 2.0)
    out = []
    for drawing in page.get_drawings():
        width = drawing.get("width")
        if drawing.get("type") not in ("s", "fs") or width is None or float(width) > max_width_pt:
            continue
        for item in drawing.get("items", []):
            if item[0] != "l":
                continue
            a = (float(item[1].x), float(item[1].y))
            b = (float(item[2].x), float(item[2].y))
            if region.contains(a) and region.contains(b) and 0 < math.dist(a, b) <= max_length_pt:
                out.append((a, b))
    return out


# ------------------------------------------------------------ vector curves


@dataclass(frozen=True)
class VectorCurve:
    """One source curve in PDF points, ordered by increasing x."""

    points: tuple[tuple[float, float], ...]
    source: str  # "vector_stroke" or "vector_fill_centerline"


def _flatten_items(items) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments = []
    for item in items:
        if item[0] == "l":
            segments.append(((float(item[1].x), float(item[1].y)), (float(item[2].x), float(item[2].y))))
        elif item[0] == "c":
            pts = _sample_cubic(*((float(p.x), float(p.y)) for p in item[1:5]), steps=16)
            segments.extend(zip(pts, pts[1:]))
    return segments


def filled_outline_centerline(
    items, x_from: float, x_to: float, step: float
) -> list[tuple[float, float]]:
    """Centre of a filled curve outline: midpoint of its extent at every x.

    A thick curve drawn as a filled polygon (or as overlapping filled wedges)
    has an upper and a lower edge at every x inside its span; the stroke's
    centreline is the midpoint of the extent.  This is exact geometry -- no
    bucketing of vertices, no smoothing -- so a steep low-VDS head keeps its
    height instead of being averaged away.
    """
    segments = _flatten_items(items)
    out: list[tuple[float, float]] = []
    for x in np.arange(x_from, x_to + 1e-9, step):
        ys = []
        for (ax, ay), (bx, by) in segments:
            if (ax - x) * (bx - x) > 0 or ax == bx:
                continue
            t = (x - ax) / (bx - ax)
            ys.append(ay + t * (by - ay))
        if ys:
            out.append((float(x), (min(ys) + max(ys)) / 2.0))
    return out


def _stroke_subpaths(drawing) -> list[list[tuple[float, float]]]:
    """A stroked drawing's items in drawing order, split where the pen jumps."""
    paths: list[list[tuple[float, float]]] = []
    for item in drawing.get("items", []):
        if item[0] == "l":
            seg = [(float(item[1].x), float(item[1].y)), (float(item[2].x), float(item[2].y))]
        elif item[0] == "c":
            seg = _sample_cubic(*((float(p.x), float(p.y)) for p in item[1:5]), steps=16)
        else:
            continue
        if paths and math.dist(paths[-1][-1], seg[0]) < _JOIN_PT:
            paths[-1].extend(seg[1:])
        else:
            paths.append(list(seg))
    return paths


def join_paths(paths: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join source paths whose end and start coincide (one curve split by the vendor).

    Only an unambiguous join is made: an endpoint with two candidate partners
    refuses rather than choosing (two curves meeting at one vertex must not be
    spliced into one).
    """
    paths = [list(p) for p in paths]
    while True:
        joins = []
        for i, a in enumerate(paths):
            for j, b in enumerate(paths):
                if i != j and math.dist(a[-1], b[0]) < _JOIN_PT:
                    joins.append((i, j))
        if not joins:
            return paths
        heads = [i for i, _ in joins]
        tails = [j for _, j in joins]
        if len(set(heads)) != len(heads) or len(set(tails)) != len(tails):
            raise RuntimeError("several source paths meet at one endpoint; refusing to choose a join")
        i, j = joins[0]
        paths[i] = paths[i] + paths[j][1:]
        del paths[j]


def vector_curve_paths(
    page,
    frame_pt,
    *,
    min_x_span_fraction: float = 0.3,
    fill_step_pt: float = 0.1,
) -> list[VectorCurve]:
    """Every dark source curve drawn inside ``frame_pt`` (a ``fitz.Rect``).

    Stroked curves are chained across drawings by shared endpoints (a vendor
    may split one curve into several paths) and must not branch.  Filled
    outlines are reduced to their centreline.  Frames, gridlines, label boxes
    and page-wide glyph groups are rejected by position, span and item count.
    """
    drawings = page.get_drawings()
    padded = frame_pt + (-2.0, -2.0, 2.0, 2.0)
    min_span = min_x_span_fraction * frame_pt.width
    curves: list[VectorCurve] = []

    strokes: list[list[tuple[float, float]]] = []
    for drawing in drawings:
        width = float(drawing.get("width") or 0.0)
        if drawing.get("type") != "s" or not _is_dark_stroke(drawing.get("color")):
            continue
        if not _MIN_CURVE_WIDTH_PT <= width <= _MAX_CURVE_WIDTH_PT:
            continue
        strokes += [p for p in _stroke_subpaths(drawing) if any(padded.contains(q) for q in p)]
    for joined in join_paths(strokes):
        # A vendor may draw a curve past the frame and clip it at render time
        # (NXP PSMN2R4-30YLD starts at x=74 pt); serve only the in-frame run.
        runs = _clip_polyline(joined, frame_pt)
        if len(runs) != 1:
            if runs:
                raise RuntimeError(f"a curve path leaves and re-enters the frame ({len(runs)} runs)")
            continue
        path = runs[0]
        xs = [p[0] for p in path]
        if len(path) < 4 or max(xs) - min(xs) < min_span:
            continue
        ordered = path if path[0][0] <= path[-1][0] else list(reversed(path))
        curves.append(VectorCurve(tuple((float(x), float(y)) for x, y in ordered), "vector_stroke"))

    for drawing in drawings:
        if drawing.get("type") != "f" or not _is_dark_stroke(drawing.get("fill")):
            continue
        rect = drawing["rect"]
        if not padded.contains(rect) or rect.width < min_span:
            continue
        items = [item for item in drawing.get("items", []) if item[0] in ("l", "c")]
        if len(items) < 8:
            continue
        x_from = max(rect.x0, frame_pt.x0)
        x_to = min(rect.x1, frame_pt.x1)
        centre = filled_outline_centerline(items, x_from, x_to, fill_step_pt)
        if len(centre) >= 8:
            curves.append(VectorCurve(tuple(centre), "vector_fill_centerline"))
    return curves


# ------------------------------------------------------------------ identity


def _interp(points: Sequence[tuple[float, float]], x: float) -> float | None:
    xs = np.array([p[0] for p in points])
    if x < xs.min() or x > xs.max():
        return None
    ys = np.array([p[1] for p in points])
    order = np.argsort(xs, kind="stable")
    return float(np.interp(x, xs[order], ys[order]))


def lowest_curve(curves: Sequence[Sequence[tuple[float, float]]], *, samples: int = 200) -> int:
    """Index of the curve that is lowest (largest image y) at EVERY shared x.

    Crss = Cgd is below Coss = Cds + Cgd and Ciss = Cgs + Cgd everywhere, so
    the Crss identity is the physically lowest curve -- but only if one curve
    is lowest throughout; otherwise the chart contradicts the physics (or a
    trace sits on the wrong stroke) and this refuses.
    """
    lo = max(min(p[0] for p in c) for c in curves)
    hi = min(max(p[0] for p in c) for c in curves)
    if hi <= lo:
        raise RuntimeError("the curves share no VDS range; cannot rank them")
    winners = set()
    for x in np.linspace(lo, hi, samples):
        ys = [_interp(c, float(x)) for c in curves]
        winners.add(int(np.argmax(ys)))
    if len(winners) != 1:
        raise RuntimeError(f"no single curve is lowest over the shared range (winners {sorted(winners)})")
    return winners.pop()


@dataclass(frozen=True)
class CurveLabel:
    name: str  # "Ciss" / "Coss" / "Crss"
    bbox: tuple[float, float, float, float]
    # Tip of an arrow drawn from the label to its curve, when there is one:
    # the label then names the curve at the tip, not the curve beside it.
    arrow_tip: tuple[float, float] | None = None

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.bbox[0] + self.bbox[2]) / 2.0, (self.bbox[1] + self.bbox[3]) / 2.0)


def _distance_to_polyline(points: Sequence[tuple[float, float]], p: tuple[float, float]) -> float:
    best = float("inf")
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
        best = min(best, math.hypot(ax + t * dx - p[0], ay + t * dy - p[1]))
    return best


def _bbox_distance(bbox, p) -> float:
    dx = max(bbox[0] - p[0], 0.0, p[0] - bbox[2])
    dy = max(bbox[1] - p[1], 0.0, p[1] - bbox[3])
    return math.hypot(dx, dy)


def label_distances(
    curves: Sequence[Sequence[tuple[float, float]]],
    label: CurveLabel,
    leaders: Sequence[tuple[tuple[float, float], tuple[float, float]]] = (),
    *,
    attach: float = _LEADER_ATTACH_PT,
) -> list[float]:
    """Distance from a label to each curve (0 when a leader line joins them).

    Without a leader, the distance is the vertical offset between the label
    centre (or its arrow tip) and the curve at that x; a label printed beyond
    a curve's end is compared with the end's height.
    """
    out = []
    for curve in curves:
        joined = any(
            (_distance_to_polyline(curve, a) <= attach and _bbox_distance(label.bbox, b) <= 1.4 * attach)
            or (_distance_to_polyline(curve, b) <= attach and _bbox_distance(label.bbox, a) <= 1.4 * attach)
            for a, b in leaders
        )
        anchor = label.arrow_tip if label.arrow_tip is not None else label.centre
        if joined:
            out.append(0.0)
            continue
        # Vertical offset from the curve at the label's x (clamped to the
        # curve's span): labels sit above/below their curve, or beyond its
        # end at the end's height.
        xs = [p[0] for p in curve]
        x = min(max(anchor[0], min(xs)), max(xs))
        out.append(abs(anchor[1] - float(_interp(curve, x))))
    return out


def bind_labels(
    curves: Sequence[Sequence[tuple[float, float]]],
    labels: Sequence[CurveLabel],
    leaders: Sequence[tuple[tuple[float, float], tuple[float, float]]] = (),
    *,
    attach: float = _LEADER_ATTACH_PT,
) -> tuple[dict[str, int], dict[str, object]]:
    """Bind curve identities: Crss by physics, Ciss/Coss by their printed labels.

    ``attach`` is how close a leader end must be to a curve (and, x1.4, to
    its label) to join them, in the caller's units (1.5 pt by default).

    Crss is the curve lowest at every shared VDS.  The two remaining curves
    take the Ciss/Coss labels by the joint assignment with the smaller total
    label-to-curve distance (a leader line counts as distance 0); the other
    assignment must cost at least ``_LABEL_MIN_SEPARATION`` times more, so two
    labels printed between converging curves cannot bind by a coin toss.  The
    Crss label, when it is not on an arrow the text layer cannot see, must
    agree with the physics.  Returns ``{name: index}`` and the evidence.
    """
    if len(curves) != 3:
        raise RuntimeError(f"need exactly three curves, got {len(curves)}")
    crss = lowest_curve(curves)
    by_name = {label.name: label for label in labels}
    missing = {"Ciss", "Coss"} - set(by_name)
    if missing:
        raise RuntimeError(f"printed labels missing for {sorted(missing)}")
    distances = {name: label_distances(curves, by_name[name], leaders, attach=attach) for name in by_name}
    u, v = [i for i in range(3) if i != crss]
    cost_a = distances["Ciss"][u] + distances["Coss"][v]
    cost_b = distances["Ciss"][v] + distances["Coss"][u]
    best, other = (cost_a, cost_b) if cost_a <= cost_b else (cost_b, cost_a)
    decisive = other > 0 and (best == 0 or other >= _LABEL_MIN_SEPARATION * best)
    evidence: dict[str, object] = {
        "Crss": {"rule": "lowest curve at every shared VDS", "curve": crss},
        "label_distances": {name: [round(d, 2) for d in ds] for name, ds in distances.items()},
        "leaders": len(leaders),
        "assignment_costs": [round(best, 2), round(other, 2)],
        "decisive": decisive,
    }
    if not decisive:
        raise RuntimeError(
            f"Ciss/Coss labels do not decide the two curves (assignment costs {best:.2f} vs {other:.2f})"
        )
    binding = {"Crss": crss, "Ciss": u, "Coss": v} if cost_a <= cost_b else {"Crss": crss, "Ciss": v, "Coss": u}
    if "Crss" in by_name:
        d = distances["Crss"]
        ranked = sorted(range(3), key=lambda i: d[i])
        evidence["Crss"]["label_nearest"] = ranked[0]  # type: ignore[index]
        evidence["Crss"]["label_agrees"] = ranked[0] == crss  # type: ignore[index]
    return binding, evidence


# ---------------------------------------------------------------- crossings


def curve_crossings(
    a: Sequence[tuple[float, float]], b: Sequence[tuple[float, float]], *, step: float = 0.5
) -> list[tuple[float, float]]:
    """Points where two single-valued curves change vertical order."""
    lo = max(min(p[0] for p in a), min(p[0] for p in b))
    hi = min(max(p[0] for p in a), max(p[0] for p in b))
    out = []
    previous = None
    for x in np.arange(lo, hi + 1e-9, step):
        ya, yb = _interp(a, float(x)), _interp(b, float(x))
        if ya is None or yb is None:
            continue
        delta = ya - yb
        if previous is not None and delta != 0 and previous[1] != 0 and np.sign(delta) != np.sign(previous[1]):
            t = previous[1] / (previous[1] - delta)
            xc = previous[0] + t * (x - previous[0])
            out.append((float(xc), float(_interp(a, float(xc)))))
        if delta != 0:
            previous = (float(x), delta)
    return out
