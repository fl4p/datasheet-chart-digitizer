"""Position-based axis calibration for vector datasheet C(V) charts.

Unlike a text-ORDER calibration (map normalized pixel extent linearly to
[min_tick, max_tick]), this associates each tick label with its actual pixel
POSITION and fits linear-X / log-Y on the (value, position) pairs. It is
therefore robust whether or not the detected plot frame coincides with the
extreme tick gridlines -- the text-order approach is correct only under that
(often-untrue) alignment assumption.

Tick labels on Infineon vector pages are exact text objects (decades render as
'10' + a unicode superscript), so no OCR is needed. Toshiba/TI pages write
plain decade numbers ('100000') instead, and Toshiba capacitance charts use a
LOG X axis with decimal decade ticks ('0.1 1 10 100'); both are handled here.
The fit residuals are returned so a bad calibration self-flags (x_resid is in
volts for a linear X axis and in DECADES when x_log is set).

The caller supplies chart-local text bands so a multi-chart datasheet page can
be calibrated without OCR or hardcoded datasheet-specific geometry. Pass
x_col_band to restrict X tick candidates to the plot's horizontal span --
required on two-charts-per-row pages where both tick rows share the y band.
"""
from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass

import numpy as np
from . import visible_text

_X_TICK_RE = re.compile(r"[−-]?\d+(?:\.\d+)?")
# Group 4 is spreadsheet scientific notation ("1E+3", "1.0E+02", "1E-01"),
# which Excel-drawn datasheets print on their log axes (HYG292N60NP1D,
# NCE30H12). It is a decade label written differently, not a new unit.
_NUMBER_TOKEN_RE = re.compile(
    r"(?<![\w.])([-+]?\d+(?:\.\d+)?)([⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)?"
    r"([KMG])?(?:[eE]([-+]?\d{1,2}))?(?![\w.])"
)
_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_ENGINEERING_MULTIPLIERS = {"K": 1e3, "M": 1e6, "G": 1e9}
_CAPACITANCE_UNIT_TO_PF = {"pf": 1.0, "nf": 1e3}
_CAPACITANCE_UNIT_TOKEN_RE = re.compile(
    r"(?:(?:\((pf|nf)\))|(?:\[(pf|nf)\])|(pf|nf))", re.I
)
# An axis TITLE carries the same unit ownership as a bare "(pF)" token:
# "Capacitance(pF)", "C-Capacitance(pF)" and "C(pF)" are one word in the
# PyMuPDF word stream, so the standalone-token match above never sees them.
# The prefix must be pure label text -- no digits and no unit of its own --
# so a data annotation ("Ciss=5000pF", "1000pF") can never donate the unit.
_CAPACITANCE_AXIS_TITLE_RE = re.compile(
    r"[A-Za-z][A-Za-zÀ-ɏ\-‐-― /,.]*"
    r"(?:\((pf|nf)\)|\[(pf|nf)\])",
    re.I,
)
# A log Y reading is FALSIFIABLE by its own residual only from three decade
# labels up; a two-point fit has an identically-zero residual and self-reports
# as perfect no matter how wrong it is.  HYG030N10NS1P's 0..10000 pF LINEAR
# ladder contains the decade-VALUED labels 1000 and 10000, and reading only
# those two as a log axis produced `axis_calibration_trusted=True` with
# `y_resid_dec = 8.9e-16` and Ciss +20583 % against the part's own spec table.
#
# Disproving that from the arithmetic side alone is not enough: degrade the
# ladder by one tick and the arithmetic fit stops proving anything while the two
# decades still self-certify, so the guard would vanish exactly as the input got
# worse.  A two-decade reading must therefore be CORROBORATED by every other
# label on the axis.  It is not simply banned -- Toshiba TPCC8105's OCR recovers
# exactly two Y labels (100 and 10000) and nothing else, and there a two-point
# fit is all the page offers and nothing contradicts it.
_MIN_FALSIFIABLE_LOG_Y_DECADES = 3
_MAX_TWO_DECADE_PEER_RESID_PX = 1.5
_MAX_TWO_DECADE_PEER_RESID_FRACTION = 0.02
_MIN_POSITIONED_LOG_Y_LABELS = 4
_MIN_POSITIONED_LOG_Y_SPAN_DEC = math.log10(5.0)
_MAX_POSITIONED_LOG_Y_RESID_DEC = 0.02
_MAX_POSITIONED_LOG_Y_RESID_PX = 1.0
_MAX_LOG_TO_LINEAR_PIXEL_RESID_RATIO = 0.25


@dataclass
class Calibration:
    mx: float  # V = mx*px_x + bx; log10(V) = mx*px_x + bx when x_log
    bx: float
    my: float  # log10(C) = my*px_y + by (log C[pF])
    by: float
    x_ticks: tuple                # ((V, px_x), ...)
    y_decades: tuple              # ((exponent, px_y), ...)
    x_resid: float                # RMS fit residual: volts, or decades when x_log
    y_resid: float                # RMS fit residual, decades
    x_log: bool = False           # X axis is logarithmic (decade-spaced ticks)
    y_log: bool = True            # Y fit maps pixels to log10(pF); false maps to pF
    x_source_ticks: tuple = ()    # signed source values before a serving transform
    x_value_transform: str | None = None

    def v_of_x(self, px):  return 10.0 ** (self.mx * px + self.bx) if self.x_log else self.mx * px + self.bx
    def x_of_v(self, v):   return ((np.log10(v) if self.x_log else v) - self.bx) / self.mx
    def c_of_y(self, py):  return 10.0 ** (self.my * py + self.by)
    def y_of_c(self, c):   return (np.log10(c) - self.by) / self.my


def _is_plain_decade(value: float) -> bool:
    """True for 1, 10, 100, ... (plain-number decade labels used by TI/Toshiba)."""
    if value < 1.0:
        return False
    exponent = math.log10(value)
    return abs(exponent - round(exponent)) < 1e-9


def _x_ticks_look_log(values: list[float]) -> bool:
    """Decade-spaced positive ticks (0.1, 1, 10, 100) mark a log X axis."""
    if len(values) < 3 or min(values) <= 0.0:
        return False
    ordered = sorted(values)
    ratios = [hi / lo for lo, hi in zip(ordered, ordered[1:]) if lo > 0.0]
    return bool(ratios) and all(abs(r - 10.0) < 0.5 for r in ratios)


def _log_fit_beats_linear(xt: list[tuple[float, float]]) -> bool:
    """Dual-fit fallback for log X axes with sub-decade ticks (1-2-5-10-20...).

    `_x_ticks_look_log` only recognizes pure decade labels; the common 1-2-5
    convention fails its ratio test, the linear fit then has a huge residual,
    and the calibration is rejected. Fit both models on the (value, position)
    pairs and call the axis log only when the log fit is near-exact AND the
    linear fit is clearly worse -- a genuinely linear axis never satisfies
    both. Gated to all-positive ticks spanning >=1.5 decades, which no real
    linear voltage axis in this corpus does (they start at 0).
    """
    values = [v for v, _ in xt]
    if len(values) < 3 or min(values) <= 0.0 or max(values) / min(values) < 30.0:
        return False
    px = np.array([p for _, p in xt], dtype=float)
    lin_v = np.array(values, dtype=float)
    log_v = np.log10(lin_v)
    lin_fit = np.polyfit(px, lin_v, 1)
    log_fit = np.polyfit(px, log_v, 1)
    lin_rel = float(np.sqrt(np.mean((np.polyval(lin_fit, px) - lin_v) ** 2))) / max(1e-12, float(np.ptp(lin_v)))
    log_rel = float(np.sqrt(np.mean((np.polyval(log_fit, px) - log_v) ** 2))) / max(1e-12, float(np.ptp(log_v)))
    return log_rel < 0.02 and log_rel < 0.5 * lin_rel


def _number_value(match: re.Match[str]) -> float:
    base = float(match.group(1))
    superscript = match.group(2)
    if superscript:
        exponent_text = superscript.translate(_SUPERSCRIPT_DIGITS)
        exponent_text = exponent_text.replace("⁻", "-").replace("⁺", "+")
        base **= int(exponent_text)
    multiplier = match.group(3)
    if multiplier:
        base *= _ENGINEERING_MULTIPLIERS[multiplier]
    scientific = match.group(4)
    if scientific:
        if superscript or multiplier:
            return float("nan")  # "10²E+3" / "1KE+3" are not labels
        base *= 10.0 ** int(scientific)
    return base


def _exact_number_token(text: str) -> float | None:
    match = _NUMBER_TOKEN_RE.fullmatch(text.strip())
    return _number_value(match) if match is not None else None


def _number_tokens(text: str) -> list[float]:
    """Parse numbers, superscript powers, and exact uppercase SI suffixes.

    The superscript is semantic, not another digit: ``10²`` is 100 and
    ``10⁻²`` is 0.01. PyMuPDF may already have flattened those glyphs in
    its word stream; native-PDF position fitting repairs that separately from
    span geometry in :func:`_explicit_power_labels`. Uppercase K/M/G suffixes
    are admitted only on clean numeric tokens; lowercase milli/micro and units
    or prose such as ``1MHz`` remain ineligible.
    """
    return [_number_value(match) for match in _NUMBER_TOKEN_RE.finditer(text)]


def _capacitance_unit_token(text: str) -> str | None:
    """Return the pF/nF unit a standalone token or an axis title declares.

    Two shapes own a unit: the bare token ("pF", "(pF)", "[pF]") and a
    wholly-alphabetic axis title that ends in a wrapped unit
    ("Capacitance(pF)", "C-Capacitance(pF)").  Both are refused when anything
    numeric is attached, so a plotted annotation such as "Ciss=5000pF" cannot
    stand in for the axis' own declaration.  Callers still require exactly one
    distinct unit across the label band, so a title/annotation conflict keeps
    failing closed rather than picking a winner.
    """

    stripped = text.strip()
    match = _CAPACITANCE_UNIT_TOKEN_RE.fullmatch(stripped)
    if match is None:
        match = _CAPACITANCE_AXIS_TITLE_RE.fullmatch(stripped)
    if match is None:
        return None
    return next(group.casefold() for group in match.groups() if group is not None)


def _explicit_power_labels(
    page,
) -> list[tuple[float, float, float, tuple[float, float, float, float]]]:
    """Recover ``10^N`` labels that PyMuPDF flattens to words like ``102``.

    The PDF dictionary retains the base and its smaller superscript as separate
    adjacent spans. Return ``(value, cx, cy, bbox)`` so both X values and Y
    exponents use authoritative glyph geometry without guessing from a bare
    integer such as 102, which could be a legitimate linear-axis value.
    """
    try:
        payload = visible_text.payload(page, "dict")
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    labels: list[tuple[float, float, float, tuple[float, float, float, float]]] = []
    for block in payload.get("blocks", []):
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("text")]
            for base_span, exponent_span in zip(spans, spans[1:]):
                if base_span["text"].strip() != "10":
                    continue
                exponent_text = exponent_span["text"].strip().replace(" ", "")
                exponent_text = exponent_text.replace("−", "-")
                if not re.fullmatch(r"[+-]?\d+", exponent_text):
                    continue
                if float(exponent_span["size"]) >= 0.95 * float(base_span["size"]):
                    continue
                base_box = tuple(float(value) for value in base_span["bbox"])
                exponent_box = tuple(float(value) for value in exponent_span["bbox"])
                if exponent_box[0] - base_box[2] > 2.0:
                    continue
                bbox = (
                    min(base_box[0], exponent_box[0]),
                    min(base_box[1], exponent_box[1]),
                    max(base_box[2], exponent_box[2]),
                    max(base_box[3], exponent_box[3]),
                )
                labels.append(
                    (
                        10.0 ** int(exponent_text),
                        (bbox[0] + bbox[2]) / 2.0,
                        (bbox[1] + bbox[3]) / 2.0,
                        bbox,
                    )
                )
    return labels


# Text prefix a caller may put on an unread power label to keep it visible.
UNREAD_POWER = "unread-10^n:"
_FLAT_NUMBER_RE = re.compile(r"[+-]?\d[\d.,]*[+-]?\d*")
_DIGITS = "0123456789"


def _page_chars(page):
    """Distinct non-space horizontal glyphs ``(char, bbox, baseline_y, size,
    (span id, index))``, by x0."""
    try:
        payload = visible_text.payload(page, "rawdict")
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    seen, out = set(), []
    for b, block in enumerate(payload.get("blocks", [])):
        for n, line in enumerate(block.get("lines", [])):
            if abs(line.get("dir", (1.0, 0.0))[1]) > 1e-3:
                continue
            for m, span in enumerate(line.get("spans", [])):
                for k, c in enumerate(span.get("chars", [])):
                    if c["c"].isspace():
                        continue
                    key = (c["c"], round(c["bbox"][0], 1), round(c["origin"][1], 1))
                    if key not in seen:
                        seen.add(key)
                        out.append((c["c"], tuple(float(v) for v in c["bbox"]), float(c["origin"][1]),
                                    float(span["size"]), ((b, n, m), k)))
    return sorted(out, key=lambda c: c[1][0])


def _chars_from(chars, x_lo, x_hi):
    lo = bisect.bisect_left(chars, x_lo, key=lambda c: c[1][0])
    hi = bisect.bisect_right(chars, x_hi, key=lambda c: c[1][0])
    return chars[lo:hi]


def _raised_power_pairs(chars):
    """``(label, base_bbox, exponent_bbox, strict)`` for each lone "10" glyph
    pair (an adjacent sign glyph included) followed by a raised, or smaller,
    signed-integer glyph run; *label* is the flattened text ("-10-3").
    *strict* is False for a smaller run too far right to be read with trust."""
    def same_line(a, b):
        return abs(a[2] - b[2]) <= 0.15 * a[3]

    def adjacent(left, right, gap, overlap=0.8):
        return -overlap <= right[1][0] - left[1][2] <= gap

    out = []
    for one in chars:
        if one[0] != "1":
            continue
        size = one[3]
        near = _chars_from(chars, one[1][0] - 3 * size, one[1][2] + 3 * size)
        zero = next((c for c in near if c[0] == "0" and same_line(one, c) and abs(c[3] - size) <= 0.1 * size
                     and adjacent(one, c, 0.3 * size)), None)
        if zero is None:
            continue
        left = [c for c in near if c is not one and same_line(one, c) and adjacent(c, one, 0.3 * size)]
        sign = left[0] if len(left) == 1 and left[0][0] in "-−–+" else None
        if (left and sign is None) or any(c is not zero and c[0] in _DIGITS + ".," and same_line(one, c)
                                          and adjacent(zero, c, 0.3 * size) for c in near) or (
                sign is not None and any(same_line(one, c) and adjacent(c, sign, 0.3 * size) for c in near)):
            continue

        def raised(c):
            rise = zero[2] - c[2]
            return c[3] <= 1.05 * size and rise < 0.9 * size and (
                rise >= 0.15 * size or (c[3] < 0.95 * size and rise >= -0.05 * size))

        first = [c for c in near if c[0] in _DIGITS + "-−–+" and raised(c) and (
            adjacent(zero, c, 0.5 * size + 0.5, 0.3 * size)
            or (c[3] < 0.95 * size and adjacent(zero, c, 1.5 * size, 0.3 * size)))]
        if not first:
            continue
        # nearest glyph first; overlapping steps only within one span: an
        # overprinted copy of the label sits about one glyph to the right
        run = [min(first, key=lambda c: abs(c[1][0] - zero[1][2]))]
        while True:
            last = run[-1]
            nxt = [c for c in _chars_from(chars, last[1][0] + 1e-6, last[1][2] + last[3])
                   if c[0] in _DIGITS and same_line(last, c) and adjacent(
                       last, c, 0.3 * last[3],
                       0.8 if c[4][0] == last[4][0] and 0 < c[4][1] - last[4][1] <= 2 else 0.25)]
            if not nxt:
                break
            run.append(min(nxt, key=lambda c: abs(c[1][0] - run[-1][1][2])))
        exp = "".join(c[0] for c in run).replace("−", "-").replace("–", "-")
        if not re.fullmatch(r"[+-]?\d{1,3}", exp):
            continue
        head = [c for c in (sign, one, zero) if c is not None]
        base = (head[0][1][0], min(c[1][1] for c in head), zero[1][2], max(c[1][3] for c in head))
        ebox = (run[0][1][0], min(c[1][1] for c in run), run[-1][1][2], max(c[1][3] for c in run))
        gap = run[0][1][0] - zero[1][2]
        label = ("-" if sign is not None and sign[0] != "+" else "") + "10" + exp
        out.append((label, base, ebox, gap <= 0.5 * size + 0.5 or (run[0][3] < 0.95 * size and gap <= 0.8 * size + 0.5)))
    return out


def power_tick_words(page, words=None):
    """Read tick labels typeset as "10" + raised exponent as one ``10^n`` word.

    *words* are PyMuPDF word tuples ``(x0, y0, x1, y1, text, ...)`` (default:
    ``page.get_text("words")``). Glyph geometry decides, not the word text: the
    exponent is a signed integer run right of a lone "10" whose baseline is
    raised (or, on the base line, whose glyphs are smaller). The word stream
    may carry it flattened ("102", "10-1"), split ("10" + "2", also across
    lines) or flattened at the base glyph size; each form returns one word
    ``10^n`` (``-10^n`` with a sign glyph), the other words of the label
    consumed. A flattened word keeps its box; a split label spans its words
    in x and keeps the base word's y extent.

    Returns ``(words, unread)``: *unread* are numeric words carrying such a
    raised or mixed-size glyph run that read as no ``10^n`` (removed from
    *words*); a caller must refuse any axis they fall on. Plain single-size
    numbers ("102", glued "100010000") pass unchanged.
    """
    words = [tuple(w) for w in (visible_text.words(page) if words is None else words)]
    chars = _page_chars(page)

    def holds(w, box, pad=0.5):
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        return w[0] - pad <= cx <= w[2] + pad and w[1] - pad <= cy <= w[3] + pad

    def norm(t):
        return t.strip().replace("−", "-").replace("–", "-")

    repl, drop, unread = {}, set(), set()
    for label, base, ebox, strict in _raised_power_pairs(chars):
        text = f"{label[:label.index('10')]}10^{int(label[label.index('10') + 2:])}"
        ends = [(ebox[0] + 0.3, ebox[1], ebox[0] + 0.3, ebox[3]), (ebox[2] - 0.3, ebox[1], ebox[2] - 0.3, ebox[3])]
        group = [i for i, w in enumerate(words) if i not in repl and (
            holds(w, base) or holds(w, ebox) or any(holds(w, b, 0.0) for b in ends))]
        rest = []
        for i in group:
            if strict and norm(words[i][4]) == label and holds(words[i], base) and holds(words[i], ebox):
                repl[i] = (*words[i][:4], text, *words[i][5:])
            else:
                rest.append(i)
        heads = [i for i in rest if holds(words[i], base)]
        seen = {}
        for i in sorted(rest, key=lambda i: words[i][0]):
            seen.setdefault((norm(words[i][4]), round(words[i][0], 1), round(words[i][1], 1)), i)
        joined = "".join(key[0] for key in seen)
        if not rest:
            continue
        if strict and heads and joined == label:
            # x spans the whole label, y stays on the base glyphs (the tick)
            x0, x1 = min(words[i][0] for i in rest), max(words[i][2] for i in rest)
            for i in heads:
                repl[i] = (x0, words[i][1], x1, words[i][3], text, *words[i][5:])
            drop.update(i for i in rest if i not in heads)
        elif not heads and all(norm(words[i][4]) == label[label.index("10") + 2:] for i in rest):
            drop.update(rest)
        else:
            unread.update(rest)
    for i, w in enumerate(words):
        if i in repl or i in drop or i in unread or not _FLAT_NUMBER_RE.fullmatch(norm(w[4])):
            continue
        inside = sorted((c for c in _chars_from(chars, w[0] - 0.5, w[2]) if c[1][2] <= w[2] + 0.5
                         and w[1] - 0.5 <= c[1][1] and c[1][3] <= w[3] + 0.5), key=lambda c: c[1][0])
        if not inside:
            continue
        # a superscript the pairing did not read: trailing digits raised or smaller
        digits = [c for c in inside if c[0] in _DIGITS]
        head = digits[0]
        k = next((k for k, c in enumerate(digits) if abs(c[2] - head[2]) > 0.15 * head[3]
                  or abs(c[3] - head[3]) > 0.05 * head[3]), len(digits))
        if k < len(digits) and all(c[2] <= head[2] - 0.15 * head[3] or (
                c[3] < 0.95 * head[3] and c[2] <= head[2] + 0.05 * head[3]) for c in digits[k:]):
            unread.add(i)
    unread -= set(repl)
    drop -= set(repl)
    kept = [repl.get(i, w) for i, w in enumerate(words) if i not in drop and i not in unread]
    return kept, [words[i] for i in sorted(unread)]


def join_digit_groups(words):
    """Join a label printed with digit-group spaces ("10 000") into one word.

    The word stream splits it at the space, leaving "10" (a false duplicate of
    the real 10 label) and "000". A 1-3 digit word followed on its line, within
    about a space (0.8 digit width), by a 3-digit word is one number. Only for
    a stacked label column: dense X tick rows ("100 150") are as close.
    """
    words = [tuple(w) for w in words]
    order = sorted(range(len(words)), key=lambda i: (round(words[i][1], 1), words[i][0]))
    out, used = {}, set()
    for i in order:
        if i in used or not re.fullmatch(r"[1-9]\d{0,2}", words[i][4].strip()):
            continue
        head = words[i]
        height = head[3] - head[1]
        text, x1 = head[4].strip(), head[2]
        digit = (head[2] - head[0]) / len(text)
        while True:
            nxt = next((j for j in order if j not in used and j != i
                        and re.fullmatch(r"\d{3}", words[j][4].strip())
                        and abs(words[j][1] - head[1]) <= 0.15 * height
                        and abs(words[j][3] - head[3]) <= 0.15 * height
                        and 0.0 < words[j][0] - x1 <= 0.8 * digit), None)
            if nxt is None:
                break
            used.add(nxt)
            text, x1 = text + words[nxt][4].strip(), words[nxt][2]
        if x1 != head[2]:
            out[i] = (head[0], head[1], x1, head[3], text, *head[5:])
    return [out.get(i, w) for i, w in enumerate(words) if i not in used]


def _inside_box(cx: float, cy: float, bbox: tuple[float, float, float, float]) -> bool:
    return bbox[0] - 1.0 <= cx <= bbox[2] + 1.0 and bbox[1] - 1.0 <= cy <= bbox[3] + 1.0


def _arithmetic_ladder_fit(
    labels: list[tuple[float, float]], multiplier: float = 1.0
) -> tuple[float, float, tuple[tuple[float, float], ...], float] | None:
    """Fit an evenly-stepped, monotone tick ladder; None when it is not one.

    Pure geometry, deliberately free of any unit reasoning: at least four
    unique non-negative labels, a near-uniform arithmetic step, strictly
    top-to-bottom pixel order, and a tight linear residual.  `multiplier`
    rescales the fitted values; the accept/reject decision is scale-invariant
    except in the absolute-floor regime of the residual gate
    (``max(1e-6, 0.03 * step * multiplier)``), where a sub-1e-6 product admits
    slightly more than the relative term alone would.
    """

    if len(labels) < 4:
        return None
    ordered = sorted((float(value), float(pixel)) for value, pixel in labels)
    # Duplicate numeric values are not a single owned tick ladder.
    if len({value for value, _ in ordered}) != len(ordered):
        return None
    values = np.asarray([value for value, _ in ordered], dtype=float)
    if np.any(values < 0.0):
        return None
    diffs = np.diff(values)
    step = float(np.median(diffs))
    if step <= 0.0 or float(np.max(np.abs(diffs - step))) > max(1e-9, 0.05 * step):
        return None
    # Values increase while page Y decreases.  Sorting by value must therefore
    # produce a strictly descending pixel sequence.
    pixels = np.asarray([pixel for _, pixel in ordered], dtype=float)
    if np.any(np.diff(pixels) >= 0.0):
        return None
    scaled = values * multiplier
    my, by = np.polyfit(pixels, scaled, 1)
    residual = float(np.sqrt(np.mean((my * pixels + by - scaled) ** 2)))
    if residual > max(1e-6, 0.03 * step * multiplier):
        return None
    ticks = tuple((float(value), float(pixel)) for value, pixel in zip(scaled, pixels))
    return float(my), float(by), ticks, residual


def _linear_capacitance_y_fit(
    labels: list[tuple[float, float]], units: set[str]
) -> tuple[float, float, tuple[tuple[float, float], ...], float] | None:
    """Fit a guarded arithmetic capacitance ladder in physical pF.

    This is deliberately narrower than the decade path: at least four tick
    labels, one locally-owned pF/nF unit, strict top-to-bottom ordering, and a
    near-uniform arithmetic step are all required.  A partial or irregular
    ladder stays uncalibrated instead of inventing a physical scale.
    """

    if len(units) != 1:
        return None
    return _arithmetic_ladder_fit(labels, _CAPACITANCE_UNIT_TO_PF[next(iter(units))])


def _subunit_log_capacitance_y_fit(
    labels: list[tuple[float, float]], units: set[str]
) -> tuple[tuple[float, float], ...] | None:
    """Recover a positioned log ladder containing labels below one unit.

    The established plain-decade path interprets its labels as pF and therefore
    deliberately starts at 1.  TI also prints log capacitance axes in nF, for
    example ``.001,.01,.1,1,10 nF``.  Convert only a complete, locally-owned
    subunit ladder to pF; never infer it from free-text order.
    """

    if len(units) != 1 or not any(0.0 < value < 1.0 for value, _ in labels):
        return None
    multiplier = _CAPACITANCE_UNIT_TO_PF[next(iter(units))]
    candidates: list[tuple[float, float]] = []
    for value, pixel in labels:
        if value <= 0.0:
            return None
        exponent = math.log10(value * multiplier)
        if abs(exponent - round(exponent)) > 1e-9:
            return None
        candidates.append((float(round(exponent)), float(pixel)))
    if len(candidates) < 3 or len({value for value, _ in candidates}) != len(candidates):
        return None

    candidates.sort(key=lambda item: item[1])
    exponents = np.asarray([value for value, _ in candidates], dtype=float)
    pixels = np.asarray([pixel for _, pixel in candidates], dtype=float)
    if np.any(np.abs(np.diff(exponents) + 1.0) > 1e-9):
        return None
    my, by = np.polyfit(pixels, exponents, 1)
    residual = float(np.sqrt(np.mean((my * pixels + by - exponents) ** 2)))
    if residual > 0.03:
        return None
    return tuple(candidates)


def _positioned_log_capacitance_y_fit(
    labels: list[tuple[float, float]], units: set[str]
) -> tuple[tuple[float, float], ...] | None:
    """Fit a dense positive log-Y ladder such as ``2..10 pF``.

    Exact decades and arithmetic ladders are handled before this fallback.
    This tier requires one owned unit, at least four unique positive labels,
    a useful physical span, strict top-to-bottom value order, and a pixel-space
    log fit that is both near-exact and decisively better than a linear fit.
    """

    if len(units) != 1 or len(labels) < _MIN_POSITIONED_LOG_Y_LABELS:
        return None
    multiplier = _CAPACITANCE_UNIT_TO_PF[next(iter(units))]
    ordered = sorted((float(value) * multiplier, float(pixel)) for value, pixel in labels)
    if len({value for value, _ in ordered}) != len(ordered):
        return None
    values_pf = np.asarray([value for value, _ in ordered], dtype=float)
    if np.any(values_pf <= 0.0):
        return None
    if math.log10(float(values_pf[-1] / values_pf[0])) < _MIN_POSITIONED_LOG_Y_SPAN_DEC:
        return None
    pixels = np.asarray([pixel for _, pixel in ordered], dtype=float)
    if np.any(np.diff(pixels) >= 0.0):
        return None

    log_values = np.log10(values_pf)
    log_pixel_fit = np.polyfit(log_values, pixels, 1)
    linear_pixel_fit = np.polyfit(values_pf, pixels, 1)
    log_pixel_resid = float(
        np.sqrt(np.mean((np.polyval(log_pixel_fit, log_values) - pixels) ** 2))
    )
    linear_pixel_resid = float(
        np.sqrt(np.mean((np.polyval(linear_pixel_fit, values_pf) - pixels) ** 2))
    )
    if log_pixel_resid > _MAX_POSITIONED_LOG_Y_RESID_PX:
        return None
    if linear_pixel_resid <= 0.0 or (
        log_pixel_resid >= _MAX_LOG_TO_LINEAR_PIXEL_RESID_RATIO * linear_pixel_resid
    ):
        return None

    my, by = np.polyfit(pixels, log_values, 1)
    residual_dec = float(
        np.sqrt(np.mean((my * pixels + by - log_values) ** 2))
    )
    if residual_dec > _MAX_POSITIONED_LOG_Y_RESID_DEC:
        return None
    return tuple(
        (float(exponent), float(pixel))
        for exponent, pixel in zip(log_values, pixels)
    )


def _decade_ladder_is_contradicted(
    yd: list[tuple[float, float]], yd_numeric: list[tuple[float, float]]
) -> bool:
    """True when the full label set proves the Y axis is arithmetic, not log.

    Decade-VALUED labels also occur ON an arithmetic ladder: a 0..10000 pF
    linear axis prints 1000 and 10000 like any other tick.  Reading just those
    two as a log axis produced a silently wrong calibration -- the overlay
    looked perfect while the served capacitances were orders of magnitude out
    (HYG030N10NS1P: Ciss +20583 % against its own spec table).

    The disproof has to be positive evidence, not merely a peer that misses the
    log fit: a single OCR fragment lands anywhere and must never veto a real
    decade axis.  So the whole label set must itself pass the evenly-stepped,
    monotone, tight-residual ladder test, and it must contain labels the decade
    subset does not -- i.e. an arithmetic reading explains strictly more of the
    printed axis than the log reading does.
    """

    if len(yd) < 2:
        return False
    ladder = _arithmetic_ladder_fit(yd_numeric)
    if ladder is None:
        return False
    _my, _by, ticks, _residual = ladder
    return len(ticks) > len(yd)


def _two_decade_reading_is_corroborated(
    yd: list[tuple[float, float]], yd_numeric: list[tuple[float, float]]
) -> bool:
    """Do the axis' other labels agree with a two-point log fit?

    Only reached when the log reading rests on exactly two decade labels, where
    the fit's own residual is identically zero and proves nothing.  Every other
    positive label in the band is then the only available evidence: on a real
    log axis they land on the fit, on an arithmetic ladder they do not (dropping
    one tick from HYG030N10NS1P leaves seven peers that miss it by 26 px).

    With no peers at all the reading is uncorroborated but also uncontradicted,
    and the page simply offers nothing else -- Toshiba TPCC8105's OCR recovers
    only 100 and 10000.  Refusing there would discard the sole available reading
    on evidence that does not exist, so it is admitted; every peer that DOES
    exist must agree.
    """

    if len(yd) < 2:
        # No log reading to corroborate; the decade path is not taken anyway.
        return True
    pixels = [pixel for _exponent, pixel in yd]
    exponents = [exponent for exponent, _pixel in yd]
    span = max(pixels) - min(pixels)
    if span <= 0.0:
        return False
    decade_pixels = set(pixels)
    peers = [
        (value, pixel)
        for value, pixel in yd_numeric
        if value > 0.0 and pixel not in decade_pixels
    ]
    if not peers:
        return True
    my, by = np.polyfit(pixels, exponents, 1)
    if my == 0.0:
        return False
    tolerance = max(
        _MAX_TWO_DECADE_PEER_RESID_PX,
        _MAX_TWO_DECADE_PEER_RESID_FRACTION * span,
    )
    return all(
        abs((math.log10(value) - by) / my - pixel) <= tolerance
        for value, pixel in peers
    )


def calibrate_axes(page, x_row_band, y_label_x_band, plot_y_band, x_col_band=None):
    """Fit calibration from tick-label text positions on a PyMuPDF page.

    x_row_band   : (y0, y1) pixel band containing the X (VDS) tick-number row
    y_label_x_band: (x0, x1) pixel band containing the left Y decade labels
    plot_y_band  : (y0, y1) pixel span over which Y decade labels may appear
    x_col_band   : optional (x0, x1) span for X tick candidates; keeps the
                   neighbor chart's tick row out on two-charts-per-row pages
    Bands isolate one chart on a multi-chart page; get them from the plot bbox.
    """
    xt, yd_explicit, yd_plain, yd_numeric = [], [], [], []
    y_units: set[str] = set()
    words, unread = power_tick_words(page)

    def in_x(cx, cy):
        return x_row_band[0] < cy < x_row_band[1] and (x_col_band is None or x_col_band[0] < cx < x_col_band[1])

    def in_y(cx, cy):
        return y_label_x_band[0] < cx < y_label_x_band[1] and plot_y_band[0] < cy < plot_y_band[1]

    y_words = [w for w in words if in_y((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)]
    words = [w for w in words if w not in y_words] + join_digit_groups(y_words)

    bad = [w[4] for w in unread if in_x((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) or in_y((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)]
    if bad:
        raise RuntimeError(f"tick labels {bad} carry a superscript read as no 10^n")
    for w in words:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        t = w[4].strip()
        power = re.fullmatch(r"10\^(-?\d+)", t)
        if power is not None:
            if in_x(cx, cy):
                xt.append((10.0 ** int(power.group(1)), cx))
            if in_y(cx, cy):
                yd_explicit.append((float(int(power.group(1))), cy))
            continue
        if in_x(cx, cy) and _X_TICK_RE.fullmatch(t):
            xt.append((float(t.replace("−", "-")), cx))
        if in_y(cx, cy):
            value = _exact_number_token(t)
            if value is not None:
                yd_numeric.append((value, cy))
                if _is_plain_decade(value):
                    yd_plain.append((math.log10(value), cy))
            unit = _capacitance_unit_token(t)
            if unit is not None:
                y_units.add(unit)
    numeric_values = [value for value, _pixel in yd_numeric]
    if len(set(numeric_values)) != len(numeric_values):
        raise RuntimeError("duplicate Y tick values cannot own distinct positions")
    yd = yd_explicit + yd_plain
    subunit_decades = None
    if len(yd_explicit) < 2:
        subunit_decades = _subunit_log_capacitance_y_fit(yd_numeric, y_units)
        if subunit_decades is not None:
            yd = list(subunit_decades)
    if len(xt) < 2:
        raise RuntimeError("need >=2 X tick labels for a position fit")
    x_source_ticks: tuple[float, ...] = ()
    x_value_transform: str | None = None
    if all(value >= 0.0 for value, _pixel in xt):
        by_pixel = sorted(xt, key=lambda item: item[1])
        prefix = by_pixel[:-1]
        prefix_values = np.asarray([value for value, _pixel in prefix], dtype=float)
        prefix_steps = np.diff(prefix_values)
        if (
            len(prefix) >= 4
            and all(right[0] > left[0] for left, right in zip(prefix, prefix[1:]))
            and prefix_values[0] == 0.0
            and len(prefix_steps) > 0
            and float(np.std(prefix_steps)) <= 0.05 * float(np.median(prefix_steps))
            and prefix_values[-1] >= 80.0
            and by_pixel[-1][0] == 1.0
        ):
            # A crop may clip the final multi-digit tick ("100" -> "1").
            # Drop only one non-monotone trailing fragment after a proven
            # four-label increasing prefix; interior ambiguity still refuses
            # through the normal residual gate.
            xt = prefix
    if any(value < 0.0 for value, _pixel in xt):
        if not all(value < 0.0 for value, _pixel in xt):
            raise RuntimeError("mixed-sign VDS ticks cannot define a magnitude axis")
        source_by_pixel = sorted(xt, key=lambda item: item[1])
        if len({value for value, _pixel in source_by_pixel}) < 3:
            raise RuntimeError("negative VDS magnitude axis needs >=3 distinct ticks")
        magnitudes = [abs(value) for value, _pixel in source_by_pixel]
        if any(right <= left for left, right in zip(magnitudes, magnitudes[1:])):
            raise RuntimeError("negative VDS ticks are not monotone by source position")
        x_source_ticks = tuple(value for value, _pixel in source_by_pixel)
        xt = [(abs(value), pixel) for value, pixel in source_by_pixel]
        x_value_transform = "abs_source_negative_vds"
    xt.sort()
    x_log = _x_ticks_look_log([v for v, _ in xt]) or _log_fit_beats_linear(xt)
    x_fit_vals = [math.log10(v) if x_log else v for v, _ in xt]
    mx, bx = np.polyfit([px for _, px in xt], x_fit_vals, 1)
    x_resid = float(np.sqrt(np.mean([(mx * px + bx - v) ** 2 for v, px in zip(x_fit_vals, [px for _, px in xt])])))
    if _decade_ladder_is_contradicted(yd, yd_numeric):
        # Decade-VALUED labels on an arithmetic ladder are not a log axis.
        # Drop the (disproven) log reading and let the narrower arithmetic and
        # positioned-log tiers below prove a model on the full label set.
        yd = []
    if (
        len(yd) < _MIN_FALSIFIABLE_LOG_Y_DECADES
        and not _two_decade_reading_is_corroborated(yd, yd_numeric)
    ):
        # Too few decades for the fit's own residual to mean anything, and the
        # rest of the axis disagrees with it.  Fall through to the tiers that
        # must prove a model on the FULL label set.
        yd = []
    if len(yd) >= 2:
        # Preserve the established decade path exactly when it has enough
        # evidence.  Linear recovery is fallback-only.
        yd.sort(key=lambda z: z[1])
        my, by = np.polyfit([py for _, py in yd], [e for e, _ in yd], 1)
        y_resid = float(np.sqrt(np.mean([(my * py + by - e) ** 2 for e, py in yd])))
        return Calibration(
            mx, bx, my, by, tuple(xt), tuple(yd), x_resid, y_resid,
            x_log=x_log, y_log=True, x_source_ticks=x_source_ticks,
            x_value_transform=x_value_transform,
        )

    linear_fit = _linear_capacitance_y_fit(yd_numeric, y_units)
    if linear_fit is not None:
        my, by, y_ticks_pf, y_resid_pf = linear_fit
        return Calibration(
            mx, bx, my, by, tuple(xt), y_ticks_pf, x_resid, y_resid_pf,
            x_log=x_log, y_log=False, x_source_ticks=x_source_ticks,
            x_value_transform=x_value_transform,
        )

    positioned_log_fit = _positioned_log_capacitance_y_fit(yd_numeric, y_units)
    if positioned_log_fit is not None:
        my, by = np.polyfit(
            [pixel for _exponent, pixel in positioned_log_fit],
            [exponent for exponent, _pixel in positioned_log_fit],
            1,
        )
        y_resid = float(np.sqrt(np.mean([
            (my * pixel + by - exponent) ** 2
            for exponent, pixel in positioned_log_fit
        ])))
        return Calibration(
            mx, bx, float(my), float(by), tuple(xt), positioned_log_fit,
            x_resid, y_resid, x_log=x_log, y_log=True,
            x_source_ticks=x_source_ticks, x_value_transform=x_value_transform,
        )

    raise RuntimeError(
        "need >=2 Y decade labels or >=4 owned arithmetic capacitance labels "
        "or a guarded owned log ladder for a position fit"
    )


if __name__ == "__main__":
    raise SystemExit("axis_calibration is a library module; use the package CLI to digitize charts")
