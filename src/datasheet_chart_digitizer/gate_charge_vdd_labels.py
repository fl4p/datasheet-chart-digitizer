"""Bind VDD/VDS labels to separated gate-charge curves, or refuse the identity.

Binding evidence, in order (never guessed):

1. **Leader line / arrow.** A dark stroke (or filled arrowhead) that starts at
   the label and ends on a curve. The tip binds to the curve it touches; a curve
   the shaft merely crosses on its way is excluded (FDB86102LZ, FDS8447: the
   "50 V" arrow crosses the 75 V curve to reach the 50 V one). A tip on a
   segment shared by several curves, between two curves, or near none, is
   refused. A label with a leader is bound by that leader or refused; it never
   falls back to proximity.
2. **Legend.** A label with a short horizontal sample stroke beside it (and
   no curve at the sample's ends) is a legend entry; it binds to the one curve
   drawn in the sample's style (dash pattern, colour, width; ISC040N10NM8
   Diagram 15: solid 20 V, dash-dot 50 V, dashed 80 V). No unique style match
   refuses it.
3. **Outer-side proximity.** A label with no leader, on a row above every
   curve's plateau that every curve crosses, lying entirely left of the bundle
   binds to the leftmost curve, entirely right of it to the rightmost
   (IPB180N04S4: "8 V" left, "32 V" right). A label inside the bundle, below a
   plateau, beyond the curves' ends or far from the bundle is refused. On a
   raster chart, ink leaving the label box (a possible leader the vector layer
   cannot show) refuses the label instead.
4. A single separated curve with exactly one explicit VDD/VDS label on the
   chart carries that label (``sole_label``).

Physics is the cross-check, never the binder: a higher VDD has the longer
plateau and so lies further right above it. Curves arrive sorted left to right,
so bound VDDs must increase with the curve index; when the chart prints exactly
one distinct VDD per curve, each bound VDD's rank must equal its curve index
(this also checks a chart where only one label binds). Two labels on one curve, one
value on two curves, or a VDD order the physics contradicts refuses every
binding on the chart (``vdd_binding_conflict:<why>``).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from .gate_charge_separation import _point_segment, _x_at_level, plateau_run

_NUMBER = re.compile(r"^=?([0-9]+(?:[.,][0-9]+)?)(V)?[,;]?$")
_EXPLICIT = re.compile(r"V_?(DD|DS|PP|OS|PS|D D|D S)", re.I)
_SUBSCRIPT = re.compile(r"^(DS|DD|GS|DSS|D|G|S|max|\(BR\)|BR)\b", re.I)
_FOREIGN = re.compile(r"V_?(GS|G)\b|^I_?D|^ID\b|T_?J|^Q", re.I)
LEADER_REACH_PT = 10.0
TIP_REACH_PT = 2.0
TIP_UNIQUE_PT = 0.8
MAX_OUTER_GAP = 0.25  # of plot width


@dataclass
class Label:
    value: float
    text: str
    bbox_pt: tuple[float, float, float, float]
    explicit: bool
    curve: int | None = None
    rule: str | None = None
    reason: str | None = None

    def payload(self, rect, scale) -> dict:
        x0, y0, x1, y1 = self.bbox_pt
        return {
            "text": self.text,
            "vdd_v": self.value,
            "bbox_px": [round((x0 - rect.x0) * scale, 1), round((y0 - rect.y0) * scale, 1),
                        round((x1 - rect.x0) * scale, 1), round((y1 - rect.y0) * scale, 1)],
            "curve": self.curve,
            "rule": self.rule,
            "reason": self.reason,
        }


@dataclass
class Binding:
    labels: list[Label] = field(default_factory=list)
    physics: str = "not_evaluable"
    diagnostics: list[str] = field(default_factory=list)


def _words(text_page) -> list[tuple[float, float, float, float, str]]:
    try:
        raw = text_page.get_text("words")
    except Exception:
        return []
    seen = set()
    out = []
    for word in raw:
        key = tuple(round(float(v), 1) for v in word[:4]) + (str(word[4]),)
        if key in seen:  # NXP sheets print every label twice
            continue
        seen.add(key)
        x0, y0, x1, y1, text = float(word[0]), float(word[1]), float(word[2]), float(word[3]), str(word[4])
        # "VDS=40V" is one word: split it at "=" (bbox shared by character count)
        if "=" in text and not text.startswith("=") and len(text) > 2:
            head, tail = text.split("=", 1)
            cut = x0 + (x1 - x0) * len(head) / len(text)
            out.append((x0, y0, cut, y1, head))
            out.append((cut, y0, x1, y1, "=" + tail))
            continue
        out.append((x0, y0, x1, y1, text))
    return out


def _phrases(words):
    """Group words into lines (vertical overlap), then phrases (horizontal gap)."""

    lines: list[list[tuple]] = []
    for word in sorted(words, key=lambda w: (0.5 * (w[1] + w[3]), w[0])):
        for line in lines:
            ly0 = min(w[1] for w in line)
            ly1 = max(w[3] for w in line)
            overlap = min(ly1, word[3]) - max(ly0, word[1])
            if overlap >= 0.4 * min(ly1 - ly0, word[3] - word[1]):
                line.append(word)
                break
        else:
            lines.append([word])
    phrases = []
    for line in lines:
        line.sort(key=lambda w: w[0])
        height = max(w[3] - w[1] for w in line)
        current = [line[0]]
        for word in line[1:]:
            if word[0] - current[-1][2] > 1.5 * height:
                phrases.append(current)
                current = [word]
            else:
                current.append(word)
        phrases.append(current)
    return phrases


def parse_labels(text_page, plot_pdf) -> list[Label]:
    """Voltage labels (VDD/VDS = n V, or bare n V) whose centre is in the plot."""

    inner = (plot_pdf.x0 - 2, plot_pdf.y0 - 2, plot_pdf.x1 + 2, plot_pdf.y1 + 2)
    words = [
        w for w in _words(text_page)
        if inner[0] <= 0.5 * (w[0] + w[2]) <= inner[2] and inner[1] <= 0.5 * (w[1] + w[3]) <= inner[3]
    ]
    labels = []
    for phrase in _phrases(words):
        tokens = [w[4] for w in phrase]
        start = 0
        for index, token in enumerate(tokens):
            match = _NUMBER.match(token)
            if not match:
                continue
            unit_index = None
            if match.group(2):
                unit_index = index
            elif index + 1 < len(tokens) and tokens[index + 1].rstrip(",;") == "V":
                unit_index = index + 1
            if unit_index is not None and unit_index + 1 < len(tokens) and _SUBSCRIPT.match(tokens[unit_index + 1]):
                # "0,2 V DS max" is 0.2 x VDS(max), not 0.2 V (BSS83P Figure 16)
                start = unit_index + 2
                continue
            if unit_index is None:
                # a number in another unit (ID = 8.3 A) ends any prefix
                start = index + (2 if index + 1 < len(tokens) and not _NUMBER.match(tokens[index + 1]) else 1)
                continue
            prefix_tokens = tokens[start:index]
            prefix = "".join(prefix_tokens).replace(" ", "")
            if token.startswith("="):
                prefix += "="
            explicit = bool(_EXPLICIT.search(prefix))
            if "=" in prefix and not explicit:
                start = unit_index + 1
                continue
            if _FOREIGN.search(prefix):
                start = unit_index + 1
                continue
            value = float(match.group(1).replace(",", "."))
            if not 0 < value <= 2000:
                start = unit_index + 1
                continue
            members = phrase[start if explicit else index : unit_index + 1]
            bbox = (min(w[0] for w in members), min(w[1] for w in members),
                    max(w[2] for w in members), max(w[3] for w in members))
            labels.append(Label(value, " ".join(w[4] for w in members), bbox, explicit))
            start = unit_index + 1
    return labels


# ----------------------------------------------------------------------------- leaders
def _chains(segments):
    """Connected annotation strokes with exactly two free ends: (end_a, end_b, last segments)."""

    n = len(segments)
    adjacency = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if min(math.dist(p, q) for p in segments[i][:2] for q in segments[j][:2]) <= 0.6:
                adjacency[i].add(j)
                adjacency[j].add(i)
    seen = set()
    chains = []
    for i in range(n):
        if i in seen:
            continue
        stack, members = [i], []
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            members.append(k)
            stack.extend(adjacency[k] - seen)
        ends: list[tuple[tuple[float, float], int]] = []
        for k in members:
            for p in segments[k][:2]:
                shared = sum(
                    1 for m in members for q in segments[m][:2]
                    if (m != k) and math.dist(p, q) <= 0.6
                )
                if shared == 0:
                    ends.append((p, k))
        if len(ends) == 2:
            chains.append(ends)
    return chains


def _arrow_tip(verts):
    """(tip, base centre) of a 3- or 4-corner arrowhead: the tip is the corner
    farthest from the others (a notched head's notch is the nearest)."""

    tip = max(verts, key=lambda v: sum(math.dist(v, w) for w in verts))
    rest = [v for v in verts if v is not tip]
    base = (sum(v[0] for v in rest) / len(rest), sum(v[1] for v in rest) / len(rest))
    return tip, base


def _near_box(p, box, reach) -> bool:
    x0, y0, x1, y1 = box
    dx = max(x0 - p[0], 0.0, p[0] - x1)
    dy = max(y0 - p[1], 0.0, p[1] - y1)
    return math.hypot(dx, dy) <= reach


def _box_distance(p, box) -> float:
    x0, y0, x1, y1 = box
    return math.hypot(max(x0 - p[0], 0.0, p[0] - x1), max(y0 - p[1], 0.0, p[1] - y1))


def leader_candidates(separation, chains):
    """Every (tail, shaft_start, tip) a leader could be: each free-ended stroke
    chain in both orientations (arrowhead tips substituted), plus shaftless
    arrowheads (tail = base centre)."""

    segments = separation.annotation_segments_pt
    out = []
    for chain_id, ends in enumerate(chains):
        (pa, ka), (pb, kb) = ends
        for (near, _k_near), (far, k_far) in (((pa, ka), (pb, kb)), ((pb, kb), (pa, ka))):
            seg = segments[k_far]
            start = seg[0] if math.dist(seg[1], far) < math.dist(seg[0], far) else seg[1]
            tip = far
            for verts in separation.arrowheads_pt:
                if any(math.dist(v, far) <= 3.0 for v in verts):
                    tip = _arrow_tip(verts)[0]
            out.append({"chain": ("c", chain_id), "tail": near, "start": start, "tip": tip})
    for k, verts in enumerate(separation.arrowheads_pt):
        tip, base = _arrow_tip(verts)
        if not any(math.dist(c["tip"], tip) <= 2.5 for c in out):
            out.append({"chain": ("a", k), "tail": base, "start": base, "tip": tip})
    return out


def assign_leaders(labels, candidates, polys_pt, half_width_pt):
    """One-to-one label <-> leader assignment, nearest tail first.

    A candidate counts only if its tail is within LEADER_REACH_PT of the label
    box, its tip is >= 3 pt farther from the box than its tail, and its tip
    lies near some curve. Returns
    (assigned: {label index: candidate}, unexplained: {label index}) where
    unexplained labels have a stroke starting at them that went to no label.
    """

    pairs = []
    stray = {}
    for i, label in enumerate(labels):
        for j, cand in enumerate(candidates):
            tail = _box_distance(cand["tail"], label.bbox_pt)
            tip = _box_distance(cand["tip"], label.bbox_pt)
            # starts at the label, ends clearly away from it (short leaders too:
            # FDS6670A's "15V" stroke is 8 pt long)
            if tail > LEADER_REACH_PT or tip < tail + 3.0 or tip <= 1.0:
                continue
            near_curve = min((_poly_distance(cand["tip"], poly) for poly in polys_pt), default=math.inf)
            if near_curve <= half_width_pt + 3.0:
                pairs.append((tail, i, j))
            else:
                stray.setdefault(i, []).append(j)
    assigned: dict[int, dict] = {}
    used_chains = set()
    for _cost, i, j in sorted(pairs):
        chain = candidates[j]["chain"]
        if i in assigned or chain in used_chains:
            continue
        assigned[i] = candidates[j]
        used_chains.add(chain)
    unexplained = set()
    for _cost, i, j in pairs:
        if i not in assigned and candidates[j]["chain"] not in used_chains:
            unexplained.add(i)
    for i, js in stray.items():
        if any(candidates[j]["chain"] not in used_chains for j in js):
            unexplained.add(i)
    return assigned, unexplained


def _segments_cross(p1, p2, q1, q2):
    """Intersection point of segments p1p2 and q1q2, or None."""

    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (q2[0] - q1[0], q2[1] - q1[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    t = ((q1[0] - p1[0]) * d2[1] - (q1[1] - p1[1]) * d2[0]) / den
    u = ((q1[0] - p1[0]) * d1[1] - (q1[1] - p1[1]) * d1[0]) / den
    if 0 <= t <= 1 and 0 <= u <= 1:
        return (p1[0] + t * d1[0], p1[1] + t * d1[1])
    return None


def _poly_distance(p, poly):
    return min((_point_segment(p, a, b)[0] for a, b in zip(poly, poly[1:])), default=math.inf)


def _nearest_on(p, poly):
    best, point = math.inf, None
    for a, b in zip(poly, poly[1:]):
        d, t = _point_segment(p, a, b)
        if d < best:
            best, point = d, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return point


def bind_by_leader(shaft_start, tip, polys, half_width, scale=1.0) -> tuple[int | None, str]:
    """Curve index a leader tip lands on. Points, polys and *half_width* are in
    one unit system; *scale* converts the pt tolerances into it (px/pt)."""

    unique = TIP_UNIQUE_PT * scale
    reach = TIP_REACH_PT * scale

    candidates = []
    for index, poly in enumerate(polys):
        crossed = False
        for a, b in zip(poly, poly[1:]):
            hit = _segments_cross(shaft_start, tip, a, b)
            if hit is not None and math.dist(hit, tip) > half_width + unique:
                crossed = True
                break
        if not crossed:
            candidates.append((_poly_distance(tip, poly), index))
    if not candidates:
        return None, "leader_crosses_every_curve"
    candidates.sort()
    best, index = candidates[0]
    if best > half_width + reach:
        return None, "leader_tip_on_no_curve"
    # unique: clearly nearest (0.8 pt margin), or on one stroke's ink and off
    # every other stroke's ink by >= 0.5 pt (Panjit's 50 V arrow: 0.23 pt from
    # the 50 V centreline, 0.99 pt from the 80 V one, 0.375 pt half-width)
    if len(candidates) > 1:
        second = candidates[1][0]
        on_one_ink = best <= half_width and second > half_width + 0.5 * scale
        if second < best + unique and not on_one_ink:
            return None, "leader_tip_between_curves"
    nearest = _nearest_on(tip, polys[index])
    if any(_poly_distance(nearest, poly) <= half_width + 0.5 * scale for k, poly in enumerate(polys) if k != index):
        return None, "leader_tip_on_shared_segment"
    return index, "leader"


MIN_RISE_SLOPE = 0.25  # px of VGS rise per px of Qg at an outer-side label row


def _slope_at(curve, x, half_window=6):
    """Upward slope (-dy/dx, px) of a curve around column x."""
    near = [(px, py) for px, py in curve if abs(px - x) <= half_window]
    if len(near) < 3 or max(p[0] for p in near) == min(p[0] for p in near):
        return 0.0
    a, b = min(near), max(near)
    return (a[1] - b[1]) / (b[0] - a[0])


def bind_by_outer_side(box_px, curves_px, plot_box, half_width_px, gap_px) -> tuple[int | None, str]:
    """Leftmost / rightmost curve for a label wholly outside the bundle at its row."""

    x0, y0, x1, y1 = box_px
    row = 0.5 * (y0 + y1)
    plateaus = [plateau_run(c) for c in curves_px]
    if any(p is None for p in plateaus):
        return None, "outer_side_plateau_unresolved"
    height = max(1, plot_box[3] - plot_box[1])
    if row > min(p[2] for p in plateaus) - max(2.0, 2 * half_width_px, 0.03 * height):
        return None, "label_not_above_plateau"
    xs = []
    for curve in curves_px:
        if min(y for _x, y in curve) > row:
            return None, "label_beyond_curve_end"
        x = _x_at_level(curve, row)
        if x is None:
            return None, "label_row_not_crossed"
        # each curve must be rising at the label row, not still on a sloped
        # plateau (synthetic syn7_0000: labels on a 3 %-sloped plateau)
        if _slope_at(curve, x) < MIN_RISE_SLOPE:
            return None, "label_row_on_a_plateau"
        xs.append(x)
    if any(b - a < gap_px for a, b in zip(xs, xs[1:])):
        return None, "curves_not_separated_at_label_row"
    width = max(1, plot_box[2] - plot_box[0])
    if x1 < xs[0] - 1:
        if xs[0] - x1 > MAX_OUTER_GAP * width:
            return None, "label_far_from_curves"
        return 0, "outer_side_left"
    if x0 > xs[-1] + 1:
        if x0 - xs[-1] > MAX_OUTER_GAP * width:
            return None, "label_far_from_curves"
        return len(xs) - 1, "outer_side_right"
    return None, "label_inside_bundle"


def ink_leaves_box(mask, box_px, curves_px, *, ring=(2, 6), min_pixels=3) -> bool:
    """Raster: does ink not on any curve leave the label box (a possible leader)?"""

    if mask is None:
        return False
    h, w = mask.shape
    x0, y0, x1, y1 = (int(round(v)) for v in box_px)
    outer = (max(0, x0 - ring[1]), max(0, y0 - ring[1]), min(w, x1 + ring[1] + 1), min(h, y1 + ring[1] + 1))
    pts = np.array([p for c in curves_px for p in c], dtype=float)
    count = 0
    for yy in range(outer[1], outer[3]):
        for xx in range(outer[0], outer[2]):
            if x0 - ring[0] <= xx <= x1 + ring[0] and y0 - ring[0] <= yy <= y1 + ring[0]:
                continue
            if not mask[yy, xx]:
                continue
            if len(pts) and np.min(np.hypot(pts[:, 0] - xx, pts[:, 1] - yy)) <= 3.0:
                continue
            count += 1
    return count >= min_pixels


LEGEND_REACH_PT = 15.0


def legend_sample(label: Label, separation, polys_pt, half_width_pt):
    """Style of a horizontal legend sample stroke beside *label*, or None."""

    x0, y0, x1, y1 = label.bbox_pt
    row_lo, row_hi = y0 - 1.0, y1 + 1.0
    found = []
    for a, b, _w, style in separation.annotation_segments_pt:
        if abs(a[1] - b[1]) > 0.5 or not (row_lo <= 0.5 * (a[1] + b[1]) <= row_hi):
            continue
        left, right = min(a[0], b[0]), max(a[0], b[0])
        if x0 - LEGEND_REACH_PT <= right <= x0 or x1 <= left <= x1 + LEGEND_REACH_PT:
            if any(_poly_distance(p, poly) <= half_width_pt + 3.0 for p in (a, b) for poly in polys_pt):
                continue  # touches a curve: a leader, not a legend sample
            found.append(style)
    styles = set(found)
    return next(iter(styles)) if len(styles) == 1 else ("ambiguous" if styles else None)


def bind_labels(labels, separation, curves_px, rect, scale, plot_box, mask=None) -> Binding:
    """Bind every label to a curve index or record why not."""

    binding = Binding(labels=labels)
    if not labels:
        return binding
    vector = (
        separation is not None and separation.method.startswith("vector")
        and all(c.polyline_pt for c in separation.curves)
    )
    half_px = (separation.curves[0].half_width_pt * scale) if vector else 1.5
    gap_px = 2 * half_px + 0.5 * scale
    if len(curves_px) == 1:
        explicit = [label for label in labels if label.explicit]
        if len(labels) == 1 and explicit:
            explicit[0].curve, explicit[0].rule = 0, "sole_label"
        else:
            for label in labels:
                label.reason = "single_curve_label_not_unique"
        return binding
    to_px = lambda p: ((p[0] - rect.x0) * scale, (p[1] - rect.y0) * scale)  # noqa: E731
    polys_px = [[to_px(p) for p in c.polyline_pt] for c in separation.curves] if vector else [
        [(float(x), float(y)) for x, y in c] for c in curves_px
    ]
    half_pt = separation.curves[0].half_width_pt if vector else 0.0
    polys_pt = [c.polyline_pt for c in separation.curves] if vector else []
    chains = _chains(separation.annotation_segments_pt) if vector else []
    candidates = leader_candidates(separation, chains) if vector else []
    assigned, unexplained = assign_leaders(labels, candidates, polys_pt, half_pt) if vector else ({}, set())
    for i, label in enumerate(labels):
        box_px = (*to_px(label.bbox_pt[:2]), *to_px(label.bbox_pt[2:]))
        if i in assigned:
            cand = assigned[i]
            label.curve, why = bind_by_leader(to_px(cand["start"]), to_px(cand["tip"]), polys_px, half_px, scale)
            if label.curve is None:
                label.reason = why
            else:
                label.rule = why
            continue
        if vector:
            style = legend_sample(label, separation, polys_pt, half_pt)
            if style == "ambiguous":
                label.reason = "legend_sample_ambiguous"
                continue
            if style is not None:
                matches = [k for k, c in enumerate(separation.curves) if c.style == style]
                if len(matches) == 1:
                    label.curve, label.rule = matches[0], "legend"
                else:
                    label.reason = f"legend_style_matches_{len(matches)}_curves"
                continue
            if i in unexplained:
                label.reason = "unexplained_stroke_at_label"
                continue
        if not vector and ink_leaves_box(mask, box_px, curves_px):
            label.reason = "possible_raster_leader"
            continue
        label.curve, why = bind_by_outer_side(box_px, curves_px, plot_box, half_px, gap_px)
        if label.curve is None:
            label.reason = why
        else:
            label.rule = why
    bound = [label for label in labels if label.curve is not None]
    conflict = None
    if len({label.curve for label in bound}) < len(bound):
        conflict = "two_labels_on_one_curve"
    elif len({label.value for label in bound}) < len(bound):
        conflict = "one_value_on_two_curves"
    else:
        values = sorted({label.value for label in labels})
        if len(curves_px) > 1 and len(values) == len(labels) == len(curves_px):
            # every curve has exactly one printed VDD: a bound label's rank
            # among them must be its curve's left-to-right index, even when
            # it is the only label bound (FDMS4D5N08LC: an arrow from the
            # right lands "40 V" on the rightmost of 30/40/50 V curves)
            if bound and all(values.index(label.value) == label.curve for label in bound):
                binding.physics = "consistent"
            elif bound:
                conflict = "vdd_rank_contradicts_physics"
        elif len(bound) >= 2:
            ordered = sorted(bound, key=lambda label: label.curve)
            if all(a.value < b.value for a, b in zip(ordered, ordered[1:])):
                binding.physics = "consistent"
            else:
                conflict = "vdd_order_contradicts_physics"
    if conflict:
        binding.physics = "contradicted" if "contradicts_physics" in conflict else binding.physics
        binding.diagnostics.append(f"vdd_binding_conflict:{conflict}")
        for label in bound:
            label.reason = f"refused:{conflict} (was {label.rule} -> curve {label.curve})"
            label.curve, label.rule = None, None
    return binding
