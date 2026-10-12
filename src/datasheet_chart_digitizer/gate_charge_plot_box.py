"""Bind the gate-charge plot box to the axis rules the served ticks sit on.

The box is first derived from frame detection and label glyphs
(``gate_charge._bind_plot_box_to_axes``). When frame detection falls back to
the loose panel rectangle, its edges come from whatever label run the page's
text offered: on onsemi FDP020N06B-F102 p4 that run was (0.4, 6, 30) -- a
body-diode label and two stray numbers -- and the box spanned the body-diode
chart above (top 179 px above the 10 V rule) and ended on the Qg labels below
(19 px under the 0 V rule). Every box-relative check (upper-axis trim, clip
check, monotone bins, height-scaled thresholds) then ran on the wrong frame,
and the legacy trace dipped 15 px below the 0 V rule.

Once an axis's labels are seated on their rules (``gate_charge_grid_anchor``),
those rules are the evidence of where the plot is. Each box edge across that
axis is therefore bound to the outermost served rule on its side, unless an
observed rule sits at the current edge within one unlabelled interval beyond
it (a frame one pitch past the last printed label, e.g. a Qg axis whose final
label is replaced by the unit) AND the frame line across the other axis runs
continuously from the outermost rule to it (a neighbouring chart's frame is
separated by paper). An edge already within ``_KEEP_TOLERANCE_PX`` of the rule
is left untouched. An axis that is not seated (no rule binding evidenced)
leaves its edges as they were: the box is never bound to label glyphs here.
"""

from __future__ import annotations

import math

import numpy as np

from .gridline_anchor import detect_axis_lines

# An edge this close to its outermost rule already agrees with it.
_KEEP_TOLERANCE_PX = 1.5
# A frame may sit at most this many label pitches beyond the outermost rule.
_FRAME_BEYOND_MAX_PITCH = 1.15
# Matching tolerance (px) for a frame rule at the box edge.
_FRAME_MATCH_PX = 2.5
# Beyond one pitch, a frame rule must sit on the label lattice (k pitches out,
# within this fraction of a pitch) and at most this many pitches out.
_LATTICE_FRACTION = 0.03
_MAX_UNLABELLED_STEPS = 3
# Share of the perpendicular frame line that must be ink between the
# outermost rule and a frame kept beyond it.
_FRAME_CONTINUITY_MIN = 0.9
_FRAME_SEARCH_PX = 6
_INK = 200


def _frame_continuous(gray, *, orientation, frame_px, start, end) -> bool:
    """Is there a frame line at *frame_px* (across the other axis) from start to end?

    For a y edge (orientation ``"y"``) the frame is a column (the left box
    edge) and the span is rows; for an x edge it is a row (the bottom edge).
    The best line within ``_FRAME_SEARCH_PX`` of *frame_px* counts: when the
    other axis is not seated, *frame_px* is the box edge, which can sit a few
    px inside the drawn frame (NTMFS5C612NT1G: box x0 151, frame at 146).
    """
    lo, hi = sorted((int(math.floor(start)), int(math.ceil(end))))
    if hi - lo < 2:
        return True
    best = 0.0
    for d in range(-_FRAME_SEARCH_PX, _FRAME_SEARCH_PX + 1):
        i = int(round(frame_px)) + d
        if orientation == "y":
            if not 0 <= i < gray.shape[1]:
                continue
            line = gray[max(0, lo):min(gray.shape[0], hi + 1), i]
        else:
            if not 0 <= i < gray.shape[0]:
                continue
            line = gray[i, max(0, lo):min(gray.shape[1], hi + 1)]
        if len(line):
            best = max(best, float((line < _INK).mean()))
    return best >= _FRAME_CONTINUITY_MIN


def _bind_axis(gray, box, served_px, *, orientation, frame_px):
    """New (lo, hi) box edges across one axis, and a note per edge.

    *frame_px* is where the frame line across the other axis runs (the left
    edge for a y edge, the bottom for an x edge), used for the continuity test.
    """
    x0, y0, x1, y1 = box
    lo_edge, hi_edge = (y0, y1) if orientation == "y" else (x0, x1)
    cross = (x0, x1) if orientation == "y" else (y0, y1)
    pixels = sorted(float(p) for p in served_px)
    pitch = float(np.median(np.diff(pixels)))
    out, notes = [], {}
    for side, edge, rule, outward in (
        ("lo", lo_edge, pixels[0], -1.0),
        ("hi", hi_edge, pixels[-1], 1.0),
    ):
        beyond = (edge - rule) * outward  # > 0: edge lies outside the rule
        if abs(edge - rule) <= _KEEP_TOLERANCE_PX:
            out.append(edge)
            notes[side] = "on_rule"
            continue
        if beyond > 0.0:
            # Unlabelled terminal intervals (a final label replaced by the
            # unit, or labels the text layer/OCR missed: STP40NF10 p7 reads
            # 0..9 V of a 0..15 V frame). Kept up to a full-span rule between
            # the outermost labelled rule and the current edge, when the frame
            # across the other axis runs continuously out to it (a stacked
            # neighbour chart is separated by paper: FDP020N06B-F102), and the
            # rule is within one pitch or on the label lattice. The box never
            # grows past its input edge here.
            reach = beyond + _FRAME_MATCH_PX
            limit = rule + outward * reach
            lines = detect_axis_lines(
                gray,
                orientation=orientation,
                along=(min(rule, limit) - 0.5, max(rule, limit) - 0.5),
                cross_span=(cross[0] - 0.5, cross[1] - 0.5),
                max_width=8,
                tick_band=3,
            )

            def admissible(px: float) -> bool:
                out_by = (px - rule) * outward
                # past the rule itself (its own run), up to the input edge
                if not _FRAME_MATCH_PX + 1.0 < out_by <= reach:
                    return False
                steps = out_by / pitch
                on_lattice = abs(steps - round(steps)) * pitch <= max(
                    _FRAME_MATCH_PX, _LATTICE_FRACTION * pitch
                )
                if not (steps <= _FRAME_BEYOND_MAX_PITCH or (on_lattice and steps <= _MAX_UNLABELLED_STEPS + 0.5)):
                    return False
                return _frame_continuous(
                    gray, orientation=orientation, frame_px=frame_px, start=rule, end=px
                )

            frames = sorted(
                (
                    line.center_px + 0.5 for line in lines
                    if line.source in {"gridline", "split_rule", "recovered_rule"}
                    and admissible(line.center_px + 0.5)
                ),
                key=lambda px: (px - rule) * outward,
            )
            if frames:
                frame = frames[-1]
                if abs(edge - frame) <= _KEEP_TOLERANCE_PX:
                    out.append(edge)
                    notes[side] = "frame_beyond_last_rule"
                else:
                    out.append(int(round(frame)))
                    notes[side] = f"bound_to_frame_beyond_last_rule ({edge - frame:+.1f}px)"
                continue
        out.append(int(round(rule)))
        notes[side] = f"bound_to_rule ({edge - rule:+.1f}px)"
    return out, notes


def bind_plot_box_to_rules(gray, box, *, y_ticks_px=None, x_ticks_px=None):
    """The plot box with each seated axis's edges on its rules.

    *y_ticks_px* / *x_ticks_px*: the SERVED ticks of an axis whose labels were
    seated on their rules (value, continuous crop px), or None when that axis
    is not seated. Returns ``(box, binding)``; *binding* records the input box
    and what happened at each edge.
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    binding: dict[str, object] = {"input_box_px": [x0, y0, x1, y1]}
    y_seated = y_ticks_px is not None and len(y_ticks_px) >= 2
    x_seated = x_ticks_px is not None and len(x_ticks_px) >= 2
    # The continuity frame across the other axis: its seated outermost rule
    # (the Qg-origin rule for a y edge, the lowest VGS rule for an x edge),
    # else the box edge. Taken from the INPUT so the passes are independent.
    left_frame = min(p for _v, p in x_ticks_px) if x_seated else x0
    bottom_frame = max(p for _v, p in y_ticks_px) if y_seated else y1
    if y_seated:
        (ny0, ny1), binding["y"] = _bind_axis(
            gray, (x0, y0, x1, y1), [p for _v, p in y_ticks_px],
            orientation="y", frame_px=left_frame,
        )
    else:
        ny0, ny1 = y0, y1
        binding["y"] = "not_seated"
    if x_seated:
        (x0, x1), binding["x"] = _bind_axis(
            gray, (x0, y0, x1, y1), [p for _v, p in x_ticks_px],
            orientation="x", frame_px=bottom_frame,
        )
    else:
        binding["x"] = "not_seated"
    y0, y1 = ny0, ny1
    new = (int(x0), int(y0), int(x1), int(y1))
    if new[2] <= new[0] or new[3] <= new[1]:
        binding["refused"] = "rules give an empty box"
        return tuple(int(v) for v in box), binding
    binding["changed"] = new != tuple(int(v) for v in box)
    return new, binding
