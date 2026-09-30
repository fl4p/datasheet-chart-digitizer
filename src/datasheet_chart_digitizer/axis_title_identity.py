"""Axis-title identity guard: refuse a panel whose own axis titles name another chart.

The finder binds a caption to a plot; when it binds the wrong plot, every
downstream check can pass on the wrong data. Found by the 2026-09-30
astra-review-50 pass: IXFB100N50P p4 bound "Forward Voltage Drop of Intrinsic
Diode" to Fig. 7 Input Admittance (titles "VGS - Volts", "ID - Amperes"), and
its three transfer curves were served as body-diode VSD/IS data, status ok.

The guard reads the PDF text in the title bands just below and left of the
calibrated plot box. It only REFUSES on positive contradiction (a title that
names the other quantity and not the expected one). No title text, or text
that names neither, is "unevaluated": that is recorded by the caller, never
treated as a confirmation.
"""

from __future__ import annotations

import re

import pymupdf

_BODY_DIODE_X_EXPECT = re.compile(r"V\s*_?\s*(SD|DS\s*\(?\s*F|F\b)|source\s*[-‐–]?\s*(to\s*[-‐–]?\s*)?drain|forward|diode", re.I)
_BODY_DIODE_X_OTHER = re.compile(r"V\s*_?\s*GS\b|gate", re.I)
_BODY_DIODE_Y_EXPECT = re.compile(r"I\s*_?\s*(S|F|SD)\b|source\s+current|reverse|diode|forward", re.I)
_BODY_DIODE_Y_OTHER = re.compile(r"I\s*_?\s*D\b|drain\s+current", re.I)


def title_band_texts(pdf: str, page: int, plot: tuple[float, float, float, float]) -> tuple[str, str]:
    """(x-title text below the plot, y-title text left of it), PDF text layer only."""

    x0, y0, x1, y1 = plot
    below = pymupdf.Rect(x0 - 10, y1 + 2, x1 + 10, y1 + 40)
    left = pymupdf.Rect(x0 - 70, y0 - 10, x0 - 1, y1 + 10)
    with pymupdf.open(pdf) as document:
        words = document[page - 1].get_text("words")
    def inside(rect):
        return " ".join(w[4] for w in words if rect.contains(pymupdf.Point(0.5 * (w[0] + w[2]), 0.5 * (w[1] + w[3]))))
    return inside(below), inside(left)


def body_diode_title_contradiction(x_text: str, y_text: str) -> str | None:
    """Reason the titles name a non-body-diode chart, or None (includes unevaluated)."""

    reasons = []
    if _BODY_DIODE_X_OTHER.search(x_text) and not _BODY_DIODE_X_EXPECT.search(x_text):
        reasons.append(f"x title {x_text.strip()[:40]!r}")
    if _BODY_DIODE_Y_OTHER.search(y_text) and not _BODY_DIODE_Y_EXPECT.search(y_text):
        reasons.append(f"y title {y_text.strip()[:40]!r}")
    if len(reasons) < 2:
        # one contradicting title can be a neighbour's text in the band; both
        # together name the other chart
        return None
    return "axis titles name another chart, not body-diode VSD/IS: " + "; ".join(reasons)


def refuse_contradicted_body_diode(panel, calibration, crop_path) -> None:
    """Raise when the panel's own titles contradict the body-diode class."""

    from dataclasses import asdict

    from PIL import Image

    from .crop_transform import CropTransform

    with Image.open(crop_path) as image:
        crop_transform = CropTransform.for_chart(asdict(panel), (image.height, image.width))
    p = calibration.plot
    ax0, ay0 = crop_transform.to_pt(p.x0, p.y0)
    ax1, ay1 = crop_transform.to_pt(p.x1, p.y1)
    x_text, y_text = title_band_texts(panel.pdf, panel.page, (ax0, ay0, ax1, ay1))
    reason = body_diode_title_contradiction(x_text, y_text)
    if reason:
        raise RuntimeError(reason)


# ---------------------------------------------------------------------------
# Geometric axis-title ownership.
#
# A panel's own axis titles often sit outside the finder's tight crop: a
# rotated y title left of the tick-label gutter, an x title under the tick
# row, or an Infineon-style horizontal y title above the top-left corner.
# Ownership is decided by geometry, never by proximity alone:
#   * y title (beside): left of the frame's left edge, vertically centred on
#     the frame;
#   * y title (above): a left-anchored line just above the frame top, not
#     centred on the frame (a panel-above's x title is);
#   * x title: below the frame, horizontally centred within it;
# and for all three: no long rule and no figure caption between it and the
# frame, and clearly nearer this frame (measured from its own outermost tick
# label) than the next panel (its frame rule, or that panel's tick labels in
# front of the rule); a border boxed around this panel is not a neighbour.
# Ambiguous placement is unowned.
# Absent titles give empty strings: unevaluated, never a confirmation.

_CAPTION_RE = re.compile(r"^\s*(fig(ure)?|diagram|graph)\b\.?\s*\d*", re.I)
_RULE_FRACTION = 0.3
# a transfer panel's own titles: x names the gate-source voltage, y the drain
# current (rotated PDF text can expose I_D as "D I")
TRANSFER_X_TITLE_RE = re.compile(r"(?<![a-z])V\s*_?\s*GS(?![a-z])|gate[\s\-‐–−]*(to[\s\-‐–−]*)?source\s+voltage", re.I)
TRANSFER_Y_TITLE_RE = re.compile(r"(?<![a-z])(I\s*_?\s*D|D\s+I)(?![a-z])|drain(\s*[-‐–−]?\s*(to\s*[-‐–−]?\s*)?source)?\s+current", re.I)


def _text_segments(words) -> list[tuple[str, float, float, float, float]]:
    """PDF words grouped into (text, x0, y0, x1, y1) line segments.

    Words of one PDF line are split at wide horizontal gaps so a line that
    runs across two side-by-side panels never becomes one title.
    """

    lines: dict[tuple[int, int], list] = {}
    for word in words:
        lines.setdefault((int(word[5]), int(word[6])), []).append(word)
    segments = []
    for members in lines.values():
        vertical = all(w[3] - w[1] > 1.5 * (w[2] - w[0]) for w in members)
        members = sorted(members, key=(lambda w: -w[3]) if vertical else (lambda w: w[0]))
        current = [members[0]]
        for word in members[1:]:
            prev = current[-1]
            gap = prev[1] - word[3] if vertical else word[0] - prev[2]
            size = prev[2] - prev[0] if vertical else prev[3] - prev[1]
            if gap > 3.0 * max(size, 4.0):
                segments.append(current)
                current = []
            current.append(word)
        segments.append(current)
    return [
        (
            " ".join(str(w[4]) for w in seg),
            min(w[0] for w in seg), min(w[1] for w in seg),
            max(w[2] for w in seg), max(w[3] for w in seg),
        )
        for seg in segments
    ]


def page_rules(drawings, min_length: float) -> list[tuple]:
    """Long straight stroked source rules: ("h", y, x0, x1, box) / ("v", x, y0, y1, box).

    ``box`` is the source rectangle an edge came from (None for a line), so a
    figure border drawn around a panel can be told from a neighbour's frame.
    """

    from .breakdown_voltage import _rectangular_drawing_item

    rules = []

    def add(ax, ay, bx, by, box=None):
        if abs(ay - by) <= 1.0 and abs(ax - bx) >= min_length:
            rules.append(("h", 0.5 * (ay + by), min(ax, bx), max(ax, bx), box))
        elif abs(ax - bx) <= 1.0 and abs(ay - by) >= min_length:
            rules.append(("v", 0.5 * (ax + bx), min(ay, by), max(ay, by), box))

    for drawing in drawings:
        if drawing.get("type") not in {"s", "fs"}:
            continue
        for item in drawing.get("items", []):
            if item[0] == "l":
                add(item[1].x, item[1].y, item[2].x, item[2].y)
                continue
            rect = _rectangular_drawing_item(item)
            if rect is None:
                continue
            for a, b in (((rect.x0, rect.y0), (rect.x1, rect.y0)), ((rect.x0, rect.y1), (rect.x1, rect.y1)),
                         ((rect.x0, rect.y0), (rect.x0, rect.y1)), ((rect.x1, rect.y0), (rect.x1, rect.y1))):
                add(a[0], a[1], b[0], b[1], (rect.x0, rect.y0, rect.x1, rect.y1))
    return rules


def _overlaps(a0, a1, b0, b1) -> bool:
    return min(a1, b1) - max(a0, b0) > 0.0


def _encloses(box, inner) -> bool:
    return box is not None and box[0] < inner[0] and box[1] < inner[1] and box[2] > inner[2] and box[3] > inner[3]


def _container_side(rule, rules, inner) -> bool:
    """True when ``rule`` is one side of a closed border drawn around ``inner``.

    The border is a source rectangle, or four separate long rules: this one
    plus the opposite side and both perpendicular sides, each spanning inner.
    """

    if _encloses(rule[4], inner):
        return True
    ix0, iy0, ix1, iy1 = inner
    if rule[0] == "v":
        lo, hi, a0, a1, other = rule[1] if rule[1] < ix0 else ix0, rule[1] if rule[1] > ix1 else ix1, iy0, iy1, "h"
        opposite = [r for r in rules if r[0] == "v" and (r[1] > ix1 if rule[1] < ix0 else r[1] < ix0)]
    else:
        lo, hi, a0, a1, other = rule[1] if rule[1] < iy0 else iy0, rule[1] if rule[1] > iy1 else iy1, ix0, ix1, "v"
        opposite = [r for r in rules if r[0] == "h" and (r[1] > iy1 if rule[1] < iy0 else r[1] < iy0)]
    if not (rule[2] <= a0 and rule[3] >= a1):
        return False
    if not any(r[2] <= a0 and r[3] >= a1 for r in opposite):
        return False
    perpendicular = [r for r in rules if r[0] == other and r[2] <= lo + 1.0 and r[3] >= hi - 1.0]
    return any(r[1] < a0 for r in perpendicular) and any(r[1] > a1 for r in perpendicular)


_NUMERIC_RE = re.compile(r"[+\-−]?(\d+([.,]\d+)?([eE][+\-−]?\d+)?[a-zA-Zµ]?|10[⁻⁰¹²³⁴⁵⁶⁷⁸⁹]+|10[−-]\d+|\d[\d.,eE+\-−]*)")


def _directions(frame):
    """Per outward direction: (name, edge, span, out, near, far, cross)."""

    fx0, fy0, fx1, fy1 = frame
    return (
        ("left", "v", lambda p: fx0 - p, lambda s: s[3], lambda s: s[1], lambda s: (s[2], s[4])),
        ("up", "h", lambda p: fy0 - p, lambda s: s[4], lambda s: s[2], lambda s: (s[1], s[3])),
        ("down", "h", lambda p: p - fy1, lambda s: s[2], lambda s: s[4], lambda s: (s[1], s[3])),
    )


def owned_axis_titles(words, rules, frame) -> tuple[str, str]:
    """(x title, y title) text this frame geometrically owns, PDF points."""

    fx0, fy0, fx1, fy1 = frame
    fw, fh = fx1 - fx0, fy1 - fy0
    segments = _text_segments(words)
    captions = [s for s in segments if _CAPTION_RE.match(s[0])]
    numerics = [s for s in segments if all(_NUMERIC_RE.fullmatch(t) for t in s[0].split())]
    long_rules = {
        "v": [r for r in rules if r[0] == "v" and r[3] - r[2] >= _RULE_FRACTION * fh],
        "h": [r for r in rules if r[0] == "h" and r[3] - r[2] >= _RULE_FRACTION * fw],
    }
    caps = {"left": min(max(0.4 * fw, 45.0), 80.0), "up": min(max(0.12 * fh, 18.0), 30.0),
            "down": min(max(0.3 * fh, 40.0), 60.0)}
    x_parts, y_parts = [], []
    for seg in segments:
        text, x0, y0, x1, y1 = seg
        if _CAPTION_RE.match(text) or "=" in text or seg in numerics:
            # captions, condition call-outs ("VGS = 10 V") and tick labels are not titles
            continue
        cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
        horizontal = (x1 - x0) >= (y1 - y0)
        placed = {
            "left": fy0 <= cy <= fy1,
            "up": horizontal and fx0 - 0.3 * fw <= x0 <= fx0 + 0.1 * fw and cx <= fx0 + 0.25 * fw,
            "down": horizontal and fx0 <= cx <= fx1,
        }
        for name, axis, out, near, far, cross in _directions(frame):
            gap = out(near(seg))
            if not (placed[name] and 0.0 <= gap <= caps[name]):
                continue
            c0, c1 = cross(seg)
            rules_here = [r for r in long_rules[axis] if _overlaps(c0, c1, r[2], r[3])]
            # nothing but this frame's own gutter between title and frame
            if any(1.0 < out(r[1]) < gap for r in rules_here) or any(
                out(far(c)) >= 0.0 and out(near(c)) <= gap and _overlaps(c0, c1, *cross(c)) for c in captions
            ):
                continue
            # nearer this frame than the next panel. Each side's tick-label
            # gutter (anywhere along the frame side) counts as part of its
            # panel: measure from this frame's outermost own tick label, and
            # to the next panel's rule or the tick label in front of it.
            side = (fy0, fy1) if name == "left" else (fx0, fx1)
            ticks = [n for n in numerics if _overlaps(*side, *cross(n))]
            own_gutter = [out(far(n)) for n in ticks if 0.0 <= out(near(n)) and out(far(n)) <= gap + 0.5]
            gap = max(0.0, gap - max(own_gutter, default=0.0))
            # a border boxed around this panel (and the title) is not a neighbour
            beyond = [
                r for r in rules_here
                if out(r[1]) >= out(far(seg)) and not _container_side(r, rules, (min(x0, fx0), min(y0, fy0), max(x1, fx1), max(y1, fy1)))
            ]
            fronts = [out(r[1]) for r in beyond] + [
                out(near(n)) for n in ticks
                if out(near(n)) >= out(far(seg)) and any(out(far(n)) <= out(r[1]) for r in beyond)
            ]
            # clearly nearer, else ambiguous and unowned (fail closed)
            if all(front - out(far(seg)) > 1.5 * gap + 1.0 for front in fronts):
                (y_parts if name in ("left", "up") else x_parts).append(text)
                break
    return " ".join(x_parts), " ".join(y_parts)


def frame_owned_axis_titles(page, transform, find_frame) -> tuple[str, str]:
    """Axis titles owned by a crop's detected plot frame; ("", "") without a frame.

    ``find_frame()`` returns the plot box in crop pixels (or raises/returns
    None); ``transform`` maps crop pixels to PDF points.
    """

    try:
        plot = find_frame()
    except (RuntimeError, ValueError):
        return "", ""
    if plot is None:
        return "", ""
    x0, y0 = transform.to_pt(plot.x0, plot.y0)
    x1, y1 = transform.to_pt(plot.x1, plot.y1)
    frame = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    rules = page_rules(page.get_drawings(), _RULE_FRACTION * min(frame[2] - frame[0], frame[3] - frame[1]))
    return owned_axis_titles(page.get_text("words"), rules, frame)
