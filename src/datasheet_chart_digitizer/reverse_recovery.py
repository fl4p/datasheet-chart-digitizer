"""Digitize diode reverse-recovery charts (Qrr/Irm/trr/S vs IF or di/dt).

Targets the Alpha & Omega datasheet style (AOT414 etc.): four linear-axis
panels ("Figure 17: Diode Reverse Recovery Charge and Peak Current vs.
Conduction Current", ... "Figure 20: ... vs. di/dt"), each carrying two
quantities on dual y axes at two junction temperatures (25/125 C). These are
the only known datasheet data for the Qrr TEMPERATURE axis (dslib
qrr_model.N_TAU), which the two-point di/dt tables cannot validate.

Differences from the capacitance pipeline that justify a plugin:
  * axes are LINEAR (Calibration in axis_calibration.py assumes log-y),
  * two y axes per panel (left/right), four curves (2 quantities x 2 temps),
  * curves are drawn as FILLED outline polygons (Excel->PDF), not strokes —
    capacitance_vector._vector_curve_edges(type=="s") cannot see them; here we
    recover the centerline of each thin filled outline by x-binning
    (top+bottom)/2,
  * curve identity comes from in-plot text labels ("Qrr"/"Irm"/"trr"/"S",
    "25(o)C"/"125(o)C") by nearest-curve assignment.

The AO Qrr/trr-left, Irm/S-right dual-axis layout is an ASSUMPTION. Rather than
trust it, each curve's y-axis side is DERIVED from the printed rotated unit
labels ("(nC)"/"(A)"/"(ns)"); a chart carrying the same labels on the opposite
axes is self-corrected, and a dual-axis chart whose sides cannot be confirmed
from labels is REFUSED (scale="FAIL") instead of scaled through a guessed axis
(see _assign_axis_sides / reverse_recovery_validation.verify_axis_sides).

Usage:
    python -m datasheet_chart_digitizer.reverse_recovery PDF [PDF...] --out DIR

Outputs per panel: an overlay PNG (digitized centerlines + calibration ticks
drawn over the rendered chart), per-curve CSVs in data space, and a
reverse_recovery_digitization.json manifest with calibration diagnostics.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .capacitance_vector import _sample_cubic
from .reverse_recovery_validation import (
    SCALE_TOL, axis_unit_sides, integrity_warnings, scale_verdict, spec_anchors,
    verify_axis_sides, verify_scale,
)
from .find_charts import group_words_into_lines, line_bbox
from .numeric_axis import AxisTick, fit_axis_ticks

RR_CAPTION_RE = re.compile(r"(?i)^figure\s*(\d+)\s*[:.]?\s*(diode\s+reverse\s+recovery.*)$")
TEMP_RE = re.compile(r"(?i)^(\d+)\s*[ºo°]?C$")
QUANTITY_WORDS = ("Qrr", "Irm", "trr", "S")
# which y axis each quantity reads on in the AO layout
QUANTITY_AXIS = {"Qrr": "left", "Irm": "right", "trr": "left", "S": "right"}
QUANTITY_UNIT = {"Qrr": "nC", "Irm": "A", "trr": "ns", "S": ""}


@dataclass
class Axis:
    """Linear pixel->value map fitted from tick labels."""

    m: float
    b: float
    n_ticks: int
    residual: float  # max |fit - label| in value units
    ticks: list = field(default_factory=list)  # (page-space px, parsed value) pairs

    def value(self, px: float) -> float:
        return self.m * px + self.b


@dataclass
class Curve:
    quantity: str
    temp_c: float
    axis: str
    points_pt: list[tuple[float, float]]  # page space
    values: list[tuple[float, float]] = field(default_factory=list)  # data space
    # how c.axis was decided: 'unit-label' (printed (nC)/(A)/(ns) on that side —
    # authoritative), 'elimination' (unitless S took the side its labeled sibling
    # vacated), or 'assumed' (fell back to the AO QUANTITY_AXIS layout, unconfirmed)
    axis_basis: str = "assumed"


@dataclass
class Panel:
    number: int
    title: str
    plot: "object"  # fitz.Rect
    x_axis: Axis
    y_left: Axis | None
    y_right: Axis | None
    x_quantity: str  # 'IF' or 'didt'
    curves: list[Curve]
    conditions: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)


def _fit_linear(ticks: list[tuple[float, float]]) -> Axis | None:
    """Linear pixel->value calibration via the ONE shared numeric_axis fitter.

    Delegates the least-squares fit to ``numeric_axis.fit_axis_ticks`` instead of
    re-deriving the normal equations here (part of the axis-fitter consolidation),
    then keeps RR's own acceptance policy on top: a stricter ``>=3`` min-count and
    the value-space residual gate (max abs error <= 3% of the value span). RR dual
    axes are always linear, so the fit is FORCED linear (``model="linear"``): a
    valid narrow-positive axis is near-indistinguishable from log and the shared
    fitter's "auto" ambiguity gate would false-refuse it. fit_axis_ticks still
    refuses non-monotone / untrusted-residual ticks, so this is strictly more
    fail-closed than the old hand-rolled fit, never less."""
    # AO doubled text layers emit the same (pixel, value) tick twice; the old
    # hand fit absorbed the redundant point, but the shared core rejects the
    # resulting zero-gap as non-monotone. Drop exact duplicates first.
    ticks = list(dict.fromkeys(ticks))
    if len(ticks) < 3:
        return None  # stricter than the shared core's min-2
    try:
        fit = fit_axis_ticks(
            [AxisTick(text=f"{v:g}", value=v, pixel=px) for px, v in ticks],
            "rr-axis",
            model="linear",
        )
    except RuntimeError:
        return None  # non-monotone / untrusted-residual: refuse
    resid = max(abs(fit.value(px) - v) for px, v in ticks)
    span = max(v for _, v in ticks) - min(v for _, v in ticks)
    if span <= 0 or resid > 0.03 * span:
        return None  # non-linear or mis-picked ticks: refuse rather than mis-calibrate
    return Axis(m=fit.m, b=fit.b, n_ticks=len(ticks), residual=resid, ticks=sorted(ticks))


def _num(text: str) -> float | None:
    t = text.strip().replace("−", "-")
    if re.fullmatch(r"-?\d+(?:\.\d+)?", t):
        return float(t)
    return None


def _dedup_consecutive(tokens: list[str]) -> list[str]:
    """Some AO PDFs carry a doubled text layer ('Figure Figure 17: 17: Diode
    Diode ...'); collapse immediate repeats so the caption regex still fires."""
    out: list[str] = []
    for t in tokens:
        if not out or out[-1] != t:
            out.append(t)
    return out


def _find_panels_on_page(page, words) -> tuple[list[dict], list[dict]]:
    """Locate RR chart captions and the gridline plot rect above each.

    Returns (panels, orphan_captions) — a caption with no matching gridline
    cluster is an extraction FAILURE the caller must record, not skip."""
    lines = group_words_into_lines(words)
    captions = []
    for i, ln in enumerate(lines):
        # side-by-side charts put two captions on one text line: split at each
        # "Figure" token and parse the segments independently
        starts = [k for k, w in enumerate(ln) if w.text.lower().startswith("figure")] or [0]
        starts.append(len(ln))
        for s0, s1 in zip(starts, starts[1:]):
            seg = ln[s0:s1]
            text = " ".join(_dedup_consecutive([w.text for w in seg]))
            m = RR_CAPTION_RE.match(text)
            if not m:
                continue
            bx = line_bbox(seg)
            title = m.group(2).strip()
            # captions wrap ("... and Peak / Current vs. Conduction Current"):
            # take the words directly below THIS segment's x-span, wherever
            # group_words_into_lines put them. The wrapped line can OVERLAP the
            # caption's bbox vertically (AO line pitch < glyph height), so gate
            # on "starts below the caption's top" rather than "below its bottom".
            cont = [w for ln2 in lines for w in ln2
                    if w.x1 > bx[0] - 10 and w.x0 < bx[2] + 10
                    and bx[1] + 5 < w.y0 < bx[3] + 14]
            if cont:
                cont.sort(key=lambda w: (w.y0, w.x0))
                title += " " + " ".join(_dedup_consecutive([w.text for w in cont]))
            if any(c["number"] == int(m.group(1)) for c in captions):
                continue  # doubled text layer repeats whole caption lines
            captions.append(dict(number=int(m.group(1)), title=title, bbox=bx))

    # gridlines: thin filled rects (Excel->PDF fills) OR stroked axis-aligned
    # lines (some AO sheets, e.g. AOT418L, stroke their grids instead)
    import fitz

    thin = []
    for d in page.get_drawings():
        if d["type"] == "f":
            for it in d["items"]:
                if it[0] != "re":
                    continue
                r = it[1]
                if min(r.width, r.height) < 1.6 and max(r.width, r.height) > 30:
                    thin.append(r)
        elif d["type"] == "s":
            for it in d["items"]:
                if it[0] != "l":
                    continue
                dx = abs(it[2].x - it[1].x)
                dy = abs(it[2].y - it[1].y)
                if (dx > 30 and dy < 0.3) or (dy > 30 and dx < 0.3):
                    thin.append(fitz.Rect(min(it[1].x, it[2].x), min(it[1].y, it[2].y),
                                          max(it[1].x, it[2].x), max(it[1].y, it[2].y)))

    # cluster gridlines into panel groups (side-by-side charts must not merge:
    # only rects that near-touch belong to the same panel frame)
    clusters: list[list] = []
    for r in thin:
        grown = fitz.Rect(r) + (-12, -12, 12, 12)
        hits = [cl for cl in clusters if any(grown.intersects(o) for o in cl)]
        merged_cl = [r]
        for cl in hits:
            merged_cl += cl
            clusters[:] = [c for c in clusters if c is not cl]
        clusters.append(merged_cl)

    panels = []
    matched: set[int] = set()
    for cl in clusters:
        if len(cl) < 6:
            continue
        plot = fitz.Rect(min(r.x0 for r in cl), min(r.y0 for r in cl),
                         max(r.x1 for r in cl), max(r.y1 for r in cl))
        if plot.width < 60 or plot.height < 60:
            continue
        px = (plot.x0 + plot.x1) / 2
        best = None
        for cap in captions:
            cx = (cap["bbox"][0] + cap["bbox"][2]) / 2
            gap = cap["bbox"][1] - plot.y1
            if gap < -4 or gap > 60 or abs(cx - px) > 110:
                continue
            if best is None or gap < best[0]:
                best = (gap, cap)
        if best:
            matched.add(best[1]["number"])
            panels.append(dict(**best[1], plot=plot))
    orphans = [c for c in captions if c["number"] not in matched]
    return panels, orphans


def _calibrate(panel_plot, words) -> tuple[Axis | None, Axis | None, Axis | None]:
    """x / y-left / y-right linear calibrations from numeric tick words."""
    x_ticks, yl_ticks, yr_ticks = [], [], []
    for w in words:
        v = _num(w[4])
        if v is None:
            continue
        wx, wy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if panel_plot.x0 - 8 <= wx <= panel_plot.x1 + 8 and 0 < w[1] - panel_plot.y1 < 16:
            x_ticks.append((wx, v))
        elif panel_plot.y0 - 6 <= wy <= panel_plot.y1 + 6 and 0 < panel_plot.x0 - w[2] < 26:
            yl_ticks.append((wy, v))
        elif panel_plot.y0 - 6 <= wy <= panel_plot.y1 + 6 and 0 < w[0] - panel_plot.x1 < 26:
            yr_ticks.append((wy, v))
    return _fit_linear(x_ticks), _fit_linear(yl_ticks), _fit_linear(yr_ticks)


def _fill_outline_centerlines(page, plot) -> list[list[tuple[float, float]]]:
    """Centerlines of thin filled curve outlines inside the plot rect.

    Each AO curve is one fill drawing whose outline runs out along the top side
    and back along the bottom; binning outline samples by x and taking
    (min+max)/2 recovers the stroked centerline. Gridline rects and text-like
    small fills are rejected by span/thickness checks.
    """
    fragments: list[list[tuple[float, float]]] = []
    for d in page.get_drawings():
        if d["type"] != "f":
            continue
        pts: list[tuple[float, float]] = []
        kinds = set()
        for it in d["items"]:
            kinds.add(it[0])
            if it[0] == "l":
                pts += [(it[1].x, it[1].y), (it[2].x, it[2].y)]
            elif it[0] == "c":
                pts += _sample_cubic((it[1].x, it[1].y), (it[2].x, it[2].y),
                                     (it[3].x, it[3].y), (it[4].x, it[4].y))
        if len(pts) < 4 or kinds == {"re"}:
            continue  # gridline rects
        # last curve segment's rounded endcap pokes past the plot frame and its
        # Bezier samples cluster there — test against a padded rect
        padded = plot + (-3, -3, 3, 3)
        inside = sum(1 for p in pts if padded.contains(p)) / len(pts)
        if inside < 0.85:
            continue
        # x-bin -> centerline of the thin outline
        bins: dict[int, list[float]] = {}
        step = 0.75
        for x, y in pts:
            bins.setdefault(int(x / step), []).append(y)
        center = []
        ok = True
        for k in sorted(bins):
            ys_b = bins[k]
            if max(ys_b) - min(ys_b) > 12:
                ok = False  # frame path / hatched region, not a stroked curve outline
                break
            center.append(((k + 0.5) * step, (min(ys_b) + max(ys_b)) / 2))
        if ok and len(center) >= 3 and center[-1][0] - center[0][0] > 2:
            fragments.append(center)

    # some AO PDFs paint the same curve segment twice — drop exact re-issues,
    # they would otherwise seed phantom parallel chains
    seen: set[tuple] = set()
    deduped = []
    for c in fragments:
        key = (round(c[0][0], 1), round(c[0][1], 1),
               round(c[-1][0], 1), round(c[-1][1], 1), len(c))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    fragments = deduped

    # chain fragments into curves (AO draws one curve as many short fill
    # segments). Consecutive segments of ONE curve join near-exactly, while
    # sibling curves can run only ~3 pt apart — so first chain with tight
    # continuity, then merge remaining breaks by tail-slope extrapolation.
    fragments.sort(key=lambda c: c[0][0])
    chains: list[list[tuple[float, float]]] = []
    for frag in fragments:
        best = None
        for ch in chains:
            gap = frag[0][0] - ch[-1][0]
            dy = abs(frag[0][1] - ch[-1][1])
            # segments of one curve join near-exactly but can OVERLAP the
            # previous segment by a couple of points (endcap over-draw)
            if -3.5 <= gap < 3 and dy < 1.2 and (best is None or dy < best[0]):
                best = (dy, ch)
        if best is None:
            chains.append(list(frag))
        else:
            best[1].extend(frag)

    changed = True
    while changed:
        changed = False
        chains.sort(key=lambda c: c[0][0])
        for i, a in enumerate(chains):
            best = None
            for j, b in enumerate(chains):
                if i == j:
                    continue
                gap = b[0][0] - a[-1][0]
                if not -3.5 <= gap <= 25:
                    continue
                tail = a[-6:]
                slope = ((tail[-1][1] - tail[0][1]) / (tail[-1][0] - tail[0][0])
                         if tail[-1][0] > tail[0][0] else 0.0)
                err = abs(a[-1][1] + slope * gap - b[0][1])
                if err < 2.5 and (best is None or err < best[0]):
                    best = (err, j)
            if best is not None:
                a.extend(chains[best[1]])
                del chains[best[1]]
                changed = True
                break

    out = []
    for ch in chains:
        ch.sort(key=lambda p: p[0])  # segment overlap makes raw chains non-monotone
        span = ch[-1][0] - ch[0][0]
        # the span gate does the real work; keep the point minimum low — a
        # full-span curve drawn with few Beziers legitimately yields <12 bins
        # (AOD4126 fig19 Qrr@25C: 10 centerline points over the whole chart)
        if span < plot.width * 0.25 or len(ch) < 6:
            continue  # labels, arrows, legend marks, leftovers
        near_edge = sum(1 for _, y in ch
                        if min(abs(y - plot.y0), abs(y - plot.y1)) < 2.0) / len(ch)
        if near_edge > 0.9:
            continue  # axis / frame line
        out.append(ch)
    return out


def _dist_to_curve(pt: tuple[float, float], curve: list[tuple[float, float]]) -> float:
    return min(math.hypot(pt[0] - x, pt[1] - y) for x, y in curve)


def _label_words(words, plot):
    temps, quants = [], []
    seen: set[tuple] = set()  # doubled text layers paint labels twice in place
    for w in words:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if not (plot.x0 - 2 < cx < plot.x1 + 2 and plot.y0 - 2 < cy < plot.y1 + 2):
            continue
        key = (w[4], round(cx), round(cy))
        if key in seen:
            continue
        seen.add(key)
        m = TEMP_RE.match(w[4])
        if m:
            temps.append((float(m.group(1)), (cx, w[3])))  # bottom edge: labels sit above curves
        elif w[4] in QUANTITY_WORDS:
            quants.append((w[4], (cx, cy)))
    return temps, quants


def _classify(centerlines, temps, quants):
    """-> list[Curve]; temp by nearest temp label (greedy), quantity by nearest
    quantity label, remaining curve of each temp gets the remaining quantity."""
    curves = [Curve(quantity="?", temp_c=float("nan"), axis="?", points_pt=c)
              for c in centerlines]
    # greedy label->curve by ascending distance; each label AND each curve
    # is consumed once (two "125C" labels must not both land on the Qrr pair)
    pairs = sorted(
        ((_dist_to_curve(pos, c.points_pt), li, t, ci)
         for li, (t, pos) in enumerate(temps)
         for ci, c in enumerate(curves)),
        key=lambda p: p[0])
    used_c: set[int] = set()
    used_l: set[int] = set()
    for dist, li, t, ci in pairs:
        if ci in used_c or li in used_l or dist > 60:
            continue
        used_c.add(ci)
        used_l.add(li)
        curves[ci].temp_c = t
    qpairs = sorted(
        ((_dist_to_curve(pos, c.points_pt), li, q, ci)
         for li, (q, pos) in enumerate(quants)
         for ci, c in enumerate(curves)),
        key=lambda p: p[0])
    used_c = set()
    used_l = set()
    for dist, li, q, ci in qpairs:
        if ci in used_c or li in used_l or dist > 90:
            continue
        used_c.add(ci)
        used_l.add(li)
        curves[ci].quantity = q
    # fill gaps: quantity labels usually appear once per panel, so the sibling
    # temperature curve stays untagged — it inherits the quantity of the
    # NEAREST tagged curve (same-quantity curves at the two temps run adjacent;
    # the other quantity's pair is far away on the other y band)
    def mean_gap(a: Curve, b: Curve) -> float:
        bx = {round(x * 2): y for x, y in b.points_pt}
        ds = [abs(y - bx[round(x * 2)]) for x, y in a.points_pt if round(x * 2) in bx]
        if ds:
            return sum(ds) / len(ds)
        return min(math.hypot(pa[0] - pb[0], pa[1] - pb[1])
                   for pa in (a.points_pt[0], a.points_pt[-1])
                   for pb in (b.points_pt[0], b.points_pt[-1]))

    for c in curves:
        if c.quantity != "?":
            continue
        tagged = [t for t in curves if t.quantity != "?"]
        if tagged:
            c.quantity = min(tagged, key=lambda t: mean_gap(c, t)).quantity
    for c in curves:
        c.axis = QUANTITY_AXIS.get(c.quantity, "?")
    return curves


COND_IF_RE = re.compile(r"(?i)^I[SF]\s*=\s*([\d.]+)\s*A[,;]?$")
COND_DIDT_RE = re.compile(r"(?i)^di/dt\s*=\s*([\d.]+)\s*A/[µμu]s[,;]?$")
def _panel_conditions(words, plot) -> dict[str, float]:
    """Fixed test conditions printed inside the plot ('Is=20A', 'di/dt=800A/µs')."""
    cond: dict[str, float] = {}
    for w in words:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if not (plot.x0 < cx < plot.x1 and plot.y0 < cy < plot.y1):
            continue
        m = COND_IF_RE.match(w[4])
        if m:
            cond["IF"] = float(m.group(1))
        m = COND_DIDT_RE.match(w[4])
        if m:
            cond["didt"] = float(m.group(1))
    return cond

def _x_quantity(panel_plot, words, title: str, warnings: list[str]) -> str:
    """Independent variable from the x-axis TITLE text (below the tick row);
    caption text is the fallback. Disagreement is recorded, axis title wins."""
    band = [w[4] for w in words
            if panel_plot.x0 - 10 <= (w[0] + w[2]) / 2 <= panel_plot.x1 + 10
            and 12 < w[1] - panel_plot.y1 < 36]
    axis_title = " ".join(band)
    from_axis = None
    if "di/dt" in axis_title:
        from_axis = "didt"
    elif re.search(r"(?i)\bI[SF]?\s*\(?A\)?", axis_title):
        from_axis = "IF"
    from_caption = "didt" if "di/dt" in title.lower() else "IF"
    if from_axis is None:
        return from_caption
    if from_axis != from_caption:
        warnings.append(f"x-axis title says {from_axis!r} but caption implies "
                        f"{from_caption!r} — using the axis title")
    return from_axis


def digitize_pdf(pdf: Path, out_dir: Path, mpn: str | None = None) -> list[dict]:
    """Digitize one PDF's reverse-recovery panels into out_dir.

    mpn overrides the output identity (default: the PDF's stem) — batch callers
    must pass a unique one when two inputs share a filename, or their artifacts
    would overwrite each other."""
    import fitz

    doc = fitz.open(pdf)
    results = []
    mpn = mpn or pdf.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    anchors = spec_anchors(doc)
    panels: list[Panel] = []
    pages: list[tuple[Panel, int]] = []
    saw_rr_text = False
    for pno in range(len(doc)):
        page = doc[pno]
        words = page.get_text("words")
        saw_rr_text = saw_rr_text or any(w[4] == "Recovery" for w in words)
        found, orphans = _find_panels_on_page(page, _as_words(words))
        for o in orphans:
            results.append(dict(pdf=str(pdf), page=pno + 1, number=o["number"],
                                title=o["title"],
                                error="RR caption found but no gridline panel "
                                      "detected above it"))
        for pan in found:
            plot = pan["plot"]
            ax, ayl, ayr = _calibrate(plot, words)
            if ax is None or (ayl is None and ayr is None):
                results.append(dict(pdf=str(pdf), page=pno + 1, number=pan["number"],
                                    title=pan["title"], error="axis calibration failed"))
                continue
            temps, quants = _label_words(words, plot)
            centerlines = _fill_outline_centerlines(page, plot)
            curves = _classify(centerlines, temps, quants)
            if not curves:
                results.append(dict(pdf=str(pdf), page=pno + 1, number=pan["number"],
                                    title=pan["title"],
                                    error="no curves extracted (stroked curves? "
                                          "unexpected drawing style)"))
                continue
            panel = Panel(number=pan["number"], title=pan["title"], plot=plot,
                          x_axis=ax, y_left=ayl, y_right=ayr,
                          x_quantity="IF", curves=curves,
                          conditions=_panel_conditions(words, plot))
            panel.x_quantity = _x_quantity(plot, words, pan["title"], panel.warnings)
            _assign_axis_sides(panel, words)
            _apply_calibration(panel)
            verify_axis_sides(panel, words, QUANTITY_UNIT)
            integrity_warnings(panel)
            panels.append(panel)
            pages.append((panel, pno))
    verify_scale(panels, anchors)
    if pages:
        # emit into a staging dir and swap in only after EVERY panel of this
        # PDF emitted — a crash or partial regression must not eat the previous
        # run's known-good artifacts
        stage = out_dir / f".staging-{mpn}"
        stage.mkdir(parents=True, exist_ok=True)
        emitted = []
        try:
            for panel, pno in pages:
                emitted.append(_emit(panel, doc, pno, pdf, mpn, stage))
        except BaseException:
            for f in stage.glob("*"):
                f.unlink()
            stage.rmdir()
            raise
        for old in out_dir.glob(f"{mpn}_fig*"):
            old.unlink()
        for f in sorted(stage.glob("*")):
            f.rename(out_dir / f.name)
        stage.rmdir()
        for m in emitted:  # manifest paths must point at the final location
            m["overlay"] = m["overlay"].replace(str(stage), str(out_dir))
            for c in m["curves"]:
                if "csv" in c:
                    c["csv"] = c["csv"].replace(str(stage), str(out_dir))
        results += emitted
    if not results:
        # No RR figure captions anywhere (a caption WITH failed extraction
        # would have produced an orphan/error entry above). Most such PDFs
        # only quote reverse recovery in the spec table — benign; carry the
        # text-mention distinction in the note so an unrecognized caption
        # style is still countable in the manifest.
        results.append(dict(
            pdf=str(pdf),
            skip="no reverse-recovery chart captions"
                 + (" (text mentions reverse recovery — table-only datasheet"
                    " or unrecognized caption style)" if saw_rr_text else "")))
    doc.close()
    return results


def _unique_mpns(pdfs: list[Path]) -> dict[Path, str]:
    """Output identities; colliding stems get a parent-dir disambiguator."""
    by_stem: dict[str, list[Path]] = {}
    for p in pdfs:
        by_stem.setdefault(p.stem, []).append(p)
    out = {}
    for stem, group in by_stem.items():
        if len(group) == 1:
            out[group[0]] = stem
        else:
            for p in group:
                out[p] = f"{stem}__{p.resolve().parent.name}"
    return out


def _as_words(words):
    """fitz words tuples pass through; find_charts helpers want .x0 etc."""
    class W:
        __slots__ = ("x0", "y0", "x1", "y1", "text")

        def __init__(s, t):
            s.x0, s.y0, s.x1, s.y1, s.text = t[0], t[1], t[2], t[3], t[4]

    return [W(t) for t in words]


def _assign_axis_sides(panel: Panel, words) -> None:
    """Decide which y-axis each curve reads on FROM the printed rotated unit
    labels, overriding the assumed AO ``QUANTITY_AXIS`` layout.

    ``_classify`` seeds ``c.axis`` from the AO convention (Qrr/trr left,
    Irm/S right). That convention is an ASSUMPTION: a datasheet carrying the same
    Qrr/Irm/trr/S labels on the OPPOSITE dual axes would be scaled through the
    wrong calibration and emit plausible-but-wrong numbers. Here each unit-bearing
    quantity is bound to whichever side actually prints its unit — ``(nC)``→Qrr,
    ``(A)``→Irm, ``(ns)``→trr — which self-corrects a flipped layout. The unitless
    ``S`` takes the side its labeled sibling vacated (elimination). A unit-bearing
    curve whose unit label is absent on a two-axis chart is left on the assumption
    with ``axis_basis='assumed'``; the verdict then refuses to call it verified
    (see ``verify_axis_sides``). Single-axis panels have no side ambiguity and are
    left untouched."""
    if panel.y_left is None or panel.y_right is None:
        return  # not a dual-axis chart: no left/right ambiguity to resolve
    sides = axis_unit_sides(panel.plot, words)
    # a unit maps to a side only if printed on EXACTLY one side (printed on both,
    # or on neither, gives no usable evidence)
    seen: dict[str, str] = {}
    ambiguous: set[str] = set()
    for side in ("left", "right"):
        for u in sides[side]:
            if u in seen and seen[u] != side:
                ambiguous.add(u)
            seen.setdefault(u, side)
    unit_side: dict[str, str] = {u: s for u, s in seen.items() if u not in ambiguous}
    claimed = {"left": False, "right": False}
    for c in panel.curves:
        u = QUANTITY_UNIT.get(c.quantity) or ""
        if u and u in unit_side:
            c.axis = unit_side[u]
            c.axis_basis = "unit-label"
            claimed[c.axis] = True
    # unitless S reads the side its labeled sibling vacated — but only when
    # exactly one side is unit-confirmed, so the other is unambiguous. A
    # unit-bearing quantity with no confirming label stays 'assumed' (refused).
    if claimed["left"] != claimed["right"]:
        free = "right" if claimed["left"] else "left"
        for c in panel.curves:
            if c.axis_basis == "assumed" and not (QUANTITY_UNIT.get(c.quantity) or ""):
                c.axis = free
                c.axis_basis = "elimination"


def _apply_calibration(panel: Panel) -> None:
    for c in panel.curves:
        axis = panel.y_left if c.axis == "left" else panel.y_right if c.axis == "right" else None
        if axis is None:
            continue
        c.values = [(panel.x_axis.value(x), axis.value(y)) for x, y in c.points_pt]


def _j(v: float) -> float | None:
    """JSON-safe number: NaN would make the manifest strict-invalid."""
    return None if v != v else v


def _emit(panel: Panel, doc, pno: int, pdf: Path, mpn: str, out_dir: Path) -> dict:
    import fitz

    out_dir.mkdir(parents=True, exist_ok=True)
    zoom = 4.0
    clip = panel.plot + (-42, -18, 42, 34)
    pix = doc[pno].get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
    png = out_dir / f"{mpn}_fig{panel.number:02d}_overlay.png"
    pix.save(png)

    from PIL import Image, ImageDraw

    img = Image.open(png).convert("RGB")
    dr = ImageDraw.Draw(img)
    colors = {("Qrr", 25.0): (200, 0, 0), ("Qrr", 125.0): (255, 140, 0),
              ("Irm", 25.0): (0, 90, 220), ("Irm", 125.0): (0, 190, 190),
              ("trr", 25.0): (150, 0, 180), ("trr", 125.0): (255, 0, 255),
              ("S", 25.0): (0, 140, 0), ("S", 125.0): (120, 200, 0)}

    def to_px(pt):
        return ((pt[0] - clip.x0) * zoom, (pt[1] - clip.y0) * zoom)

    # scale verification, visually: mark every tick label the calibration READ
    # (green crosshair + parsed value) so a human can spot a mis-picked or
    # mis-parsed tick at a glance — the fit is only as good as these anchors
    GREEN = (0, 150, 60)

    def tick_text(xy, s):
        try:
            dr.text(xy, s, fill=GREEN, font_size=26)
        except TypeError:  # Pillow < 10 has no font_size shortcut
            dr.text(xy, s, fill=GREEN)

    for axis, kind in ((panel.x_axis, "x"), (panel.y_left, "yl"), (panel.y_right, "yr")):
        if axis is None:
            continue
        for pos, val in axis.ticks:
            if kind == "x":
                px, py = to_px((pos, panel.plot.y1))
                dr.line([px, py - 8, px, py + 8], fill=GREEN, width=3)
                tick_text((px + 4, py + 10), f"{val:g}")
            else:
                edge = panel.plot.x0 if kind == "yl" else panel.plot.x1
                px, py = to_px((edge, pos))
                dr.line([px - 8, py, px + 8, py], fill=GREEN, width=3)
                tick_text((px + 11, py - 13) if kind == "yl" else (px - 46, py - 13),
                          f"{val:g}")

    curves_meta = []
    used_paths: set = set()
    for c in panel.curves:
        col = colors.get((c.quantity, c.temp_c), (128, 128, 128))
        for x, y in c.points_pt[:: max(1, len(c.points_pt) // 220)]:
            px, py = to_px((x, y))
            dr.ellipse([px - 1.6, py - 1.6, px + 1.6, py + 1.6], outline=col, width=2)
        temp_tag = f"{c.temp_c:g}C" if c.temp_c == c.temp_c else "unknownT"
        if c.values:
            stem = f"{mpn}_fig{panel.number:02d}_{c.quantity}_{temp_tag}"
            csv_path = out_dir / f"{stem}.points.csv"
            n = 2
            while csv_path in used_paths:  # duplicate identity must not clobber data
                csv_path = out_dir / f"{stem}_{n}.points.csv"
                n += 1
            used_paths.add(csv_path)
            with open(csv_path, "w", newline="") as f:
                wr = csv.writer(f)
                wr.writerow([panel.x_quantity, f"{c.quantity}_{QUANTITY_UNIT[c.quantity]}"])
                wr.writerows([[f"{a:.6g}", f"{b:.6g}"] for a, b in c.values])
            curves_meta.append(dict(quantity=c.quantity, temp_c=_j(c.temp_c), axis=c.axis,
                                    axis_basis=c.axis_basis,
                                    n_points=len(c.values), csv=str(csv_path)))
        else:
            reason = ("quantity unidentified" if c.quantity == "?"
                      else "no y-axis calibration")
            curves_meta.append(dict(quantity=c.quantity, temp_c=_j(c.temp_c), axis=c.axis,
                                    axis_basis=c.axis_basis,
                                    n_points=0, error=reason))
    img.save(png)
    tags = [(c["quantity"], c["temp_c"]) for c in curves_meta]
    warnings = list(panel.warnings)
    warnings += [f"duplicate curve identity {t}" for t in set(tags) if tags.count(t) > 1]
    warnings += [f"unidentified curve ({c['quantity']}, {c['temp_c']})"
                 for c in curves_meta
                 if c["quantity"] == "?" or c["temp_c"] is None]
    checks = panel.checks
    scale = scale_verdict(checks, warnings, curves=curves_meta)
    return dict(pdf=str(pdf), page=pno + 1, number=panel.number, title=panel.title,
                warnings=warnings, scale=scale, scale_checks=checks,
                conditions=panel.conditions,
                x_quantity=panel.x_quantity,
                x_axis=dict(n_ticks=panel.x_axis.n_ticks, residual=panel.x_axis.residual),
                y_left=None if panel.y_left is None else dict(
                    n_ticks=panel.y_left.n_ticks, residual=panel.y_left.residual),
                y_right=None if panel.y_right is None else dict(
                    n_ticks=panel.y_right.n_ticks, residual=panel.y_right.residual),
                overlay=str(png), curves=curves_meta)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdfs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    manifest = []
    mpns = _unique_mpns(args.pdfs)
    for pdf in args.pdfs:
        try:
            manifest += digitize_pdf(pdf, args.out, mpn=mpns[pdf])
        except Exception as e:  # noqa: BLE001 — batch over many PDFs, record and continue
            manifest.append(dict(pdf=str(pdf), error=f"{type(e).__name__}: {e}"))
    args.out.mkdir(parents=True, exist_ok=True)
    mf = args.out / "reverse_recovery_digitization.json"
    mf.write_text(json.dumps(manifest, indent=2))
    n_err = sum(1 for m in manifest if "error" in m)
    n_skip = sum(1 for m in manifest if "skip" in m)
    n_ok = len(manifest) - n_err - n_skip
    print(f"digitized {n_ok} panels, {n_skip} PDFs without RR content, "
          f"{n_err} errors -> {mf}")
    for m in manifest:
        if "skip" in m:
            continue
        if "error" in m:
            print(f"  FAIL {m.get('pdf')} fig{m.get('number', '?')}: {m['error']}")
            continue
        checks = m.get("scale_checks", [])
        worst = max((abs(k["err"]) for k in checks), default=None)
        detail = f"{len(checks)} checks, worst {worst*100:.1f}%" if checks else "no anchor applies"
        print(f"  fig{m['number']:>2} scale={m['scale']:<10} ({detail})"
              + (f"  warnings: {'; '.join(m['warnings'])}" if m.get("warnings") else ""))
        if m["scale"] == "FAIL":
            for k in checks:
                if abs(k["err"]) > SCALE_TOL:
                    print(f"       {k}")
    if n_err:
        raise SystemExit(1)  # scripts/CI must see failed digitization


if __name__ == "__main__":
    main()
