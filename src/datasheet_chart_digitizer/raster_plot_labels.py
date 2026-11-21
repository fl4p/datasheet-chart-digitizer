"""Curve labels printed inside a whole-figure raster plot, and their leaders.

Inside the plot the grid, the curves and the condition box defeat a whole-plot
OCR pass (it returned none of the TPH1R306PL "100" / "25" / "Ta = -55 C"
labels).  Here the label ink is isolated first:

* ink minus long rules (frame, grid, box borders: straight runs over 12 % of
  the plot) minus the dilated curve mask;
* compact glyphs (no side over 1.6 glyph heights) merged into word tokens;
  longer thin pieces are leader candidates;
* each token is read twice from PDF re-renders: digits-only by vote
  (``raster_tick_ocr._ocr_label``) and unrestricted at 400 dpi.  Only a token
  both reads call the same number is a number ("Ta" reads "7" digits-only, so
  it is not);
* a number followed on its line by a unit token (V, A, mA, ...) is a condition,
  never a temperature.

``bind_labels`` binds each temperature to a curve by what the page prints: the
curve a leader starting at the label ends on, else a label wholly on one side
of the curve bundle to the outermost curve on that side.  Anything else stays
unbound; nothing is bound by proximity.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import tesseract_memo
from .capacitance_types import PlotBox
from .raster_figure import NativeRender
from .raster_tick_ocr import _ocr_label, render_label

_INK = 160
_UNIT_RE = re.compile(r"^(?:m|u|µ|k)?(?:V|A|W|s|Ω|ohm)$", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_TEMP_RANGE = (-75.0, 225.0)


@dataclass(frozen=True)
class Token:
    box: tuple[int, int, int, int]  # native px
    digits: str | None
    plain: str
    plain_hi: str = ""               # second unrestricted read, 600 dpi


@dataclass(frozen=True)
class TemperatureLabel:
    value: float
    box: tuple[int, int, int, int]  # native px
    degree_unit: bool                # followed by a C / degree token


def _plain_read(native: NativeRender, box, pad: int, dpi: int = 400, bands=()) -> str:
    executable = shutil.which("tesseract")
    if executable is None:
        raise RuntimeError("tesseract binary not found")
    with tempfile.TemporaryDirectory() as tmp:
        png = render_label(native, box, pad, dpi, bands, Path(tmp) / "token.png")
        proc = tesseract_memo.run([executable, str(png), "stdout", "--psm", "7"], png,
                                  capture_output=True, text=True, timeout=60)
    return proc.stdout.strip().replace("−", "-") if proc.returncode == 0 else ""


def rule_bands(native: NativeRender, x_rules, y_rules, rule_w: int, min_run: int) -> list[tuple[str, int, int, int, int]]:
    """Where each detected grid rule's ink actually runs, as native-px rectangles.

    A band is ``(axis, lo, hi, start, end)``: columns (axis "x") or rows ("y")
    lo..hi, over start..end along the rule.  Only stretches whose centre (±1 px)
    is dark for at least *min_run* consecutive px count: a rule interrupted behind a label's white
    patch leaves that label's glyphs alone.
    """
    ink = native.gray < _INK
    half = rule_w // 2 + 1
    bands = []
    for axis, rules in (("x", x_rules), ("y", y_rules)):
        for r in rules:
            c = int(round(native.to_native(r)))
            strip = ink[:, c - 1: c + 2] if axis == "x" else ink[c - 1: c + 2, :].T
            dark = strip.any(axis=1)
            padded = np.concatenate(([False], dark, [False]))
            edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
            for a, b in zip(edges[::2], edges[1::2]):
                if b - a >= min_run:
                    bands.append((axis, c - half, c + half, int(a), int(b)))
    return bands


def label_ink(native: NativeRender, plot: PlotBox, curve_mask: np.ndarray, stroke: float, bands) -> np.ndarray:
    """Ink inside the plot that is neither a detected grid rule nor a traced curve.

    Only the rules the grid detector found are removed: a long straight leader
    is not a rule.
    """
    ink = (native.gray < _INK).astype(np.uint8)
    x0, x1 = int(native.to_native(plot.x0)), int(native.to_native(plot.x1))
    y0, y1 = int(native.to_native(plot.y0)), int(native.to_native(plot.y1))
    keep = np.zeros_like(ink)
    keep[y0 + 4:y1 - 3, x0 + 4:x1 - 3] = 1
    for axis, lo, hi, a, b in bands:
        if axis == "x":
            keep[a:b, max(0, lo):hi + 1] = 0
        else:
            keep[max(0, lo):hi + 1, a:b] = 0
    grow = max(3, int(round(0.5 * stroke)))
    curves = cv2.dilate(curve_mask.astype(np.uint8), np.ones((2 * grow + 1, 2 * grow + 1), np.uint8))
    return (ink & keep & (1 - curves)).astype(bool)


def _components(mask: np.ndarray):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return n, labels, stats


def _adjacent_band(box, bands, glyph_h: float, side: str):
    reach = 0.5 * glyph_h
    for axis, lo, hi, a, b in bands:
        if axis != "x" or b <= box[1] or a >= box[3]:
            continue
        if side == "left" and 0 <= box[0] - hi <= reach:
            return lo, hi
        if side == "right" and 0 <= lo - box[2] <= reach:
            return lo, hi
    return None


def _merge_across_rules(boxes, bands, glyph_h: float):
    """Join same-line boxes separated only by a removed vertical rule; extend a
    box whose edge abuts a rule by half a glyph height past it.

    A glyph printed on or against a grid rule loses pixels with the rule ("1"
    of "100" against the 2 V rule).  The OCR re-render whitens the rule band,
    so the extension adds only glyph ink.
    """
    boxes = sorted(boxes)
    out: list[tuple[int, int, int, int]] = []
    for box in boxes:
        if out:
            prev = out[-1]
            overlap = min(prev[3], box[3]) - max(prev[1], box[1])
            band = _adjacent_band(prev, bands, glyph_h, "right")
            if band and overlap >= 0.5 * min(prev[3] - prev[1], box[3] - box[1]) and box[0] - band[1] <= 0.5 * glyph_h:
                out[-1] = (prev[0], min(prev[1], box[1]), box[2], max(prev[3], box[3]))
                continue
        out.append(box)
    extended = []
    for box in out:
        x0, y0, x1, y1 = box
        left = _adjacent_band(box, bands, glyph_h, "left")
        right = _adjacent_band(box, bands, glyph_h, "right")
        if left:
            x0 = int(left[0] - 0.5 * glyph_h)
        if right:
            x1 = int(right[1] + 0.5 * glyph_h)
        extended.append((x0, y0, x1, y1))
    return extended


def _inside_a_band(xs, ys, bands) -> bool:
    for axis, lo, hi, _a, _b in bands:
        coord = xs if axis == "x" else ys
        if coord.min() >= lo - 1 and coord.max() <= hi + 1:
            return True
    return False


def tokens_and_leaders(native: NativeRender, ink: np.ndarray, bands, glyph_h: float):
    """Word tokens (OCRed), leader components and every ink piece (pixel arrays).

    Pieces include glyph-sized fragments: a leader cut by curves and rules
    continues through short pieces (``_leader_tip``)."""
    n, labels, stats = _components(ink)
    glyph = np.zeros_like(ink, dtype=np.uint8)
    leaders: list[np.ndarray] = []
    pieces: list[np.ndarray] = []
    for i in range(1, n):
        ys, xs = np.nonzero(labels == i)
        if not _inside_a_band(xs, ys, bands):
            pieces.append(np.stack([xs, ys], axis=1))
        w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if max(w, h) <= 1.6 * glyph_h:
            if stats[i, cv2.CC_STAT_AREA] >= 4:
                glyph[labels == i] = 1
        elif min(w, h) <= 0.6 * glyph_h or stats[i, cv2.CC_STAT_AREA] < 0.25 * w * h:
            if _inside_a_band(xs, ys, bands):
                continue  # a rule remnant (short dark stretch), not a leader
            leaders.append(pieces[-1])
    gap = max(2, int(round(0.3 * glyph_h)))
    merged = cv2.dilate(glyph, np.ones((max(1, int(0.3 * glyph_h)), 2 * gap + 1), np.uint8))
    m, mlabels, _mstats = _components(merged)
    boxes = []
    for j in range(1, m):
        ys, xs = np.nonzero((mlabels == j) & (glyph > 0))
        if len(xs):
            boxes.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    tokens: list[Token] = []
    for box in _merge_across_rules(boxes, bands, glyph_h):
        if box[3] - box[1] < 0.4 * glyph_h:
            continue  # stray dot or a lone minus/equals stroke
        pad = max(4, int(round(0.4 * glyph_h)))
        tokens.append(Token(box, _ocr_label(native, box, pad, bands), _plain_read(native, box, pad, 400, bands),
                            _plain_read(native, box, pad, 600, bands)))
    return tokens, leaders, pieces


def _degree_value(text: str) -> float | None:
    from .transfer_temperature_labels import EXPLICIT_DEGREE_TEMP_RE, normalize_temperature_text

    found = EXPLICIT_DEGREE_TEMP_RE.findall(normalize_temperature_text(text))
    return float(found[0]) if len(found) == 1 else None


def temperature_labels(tokens: list[Token], glyph_h: float) -> list[TemperatureLabel]:
    """Temperatures printed in the plot.

    A bare number counts when the digits-only vote and the unrestricted read
    give the same number, it is in range, and it is not followed on its line by
    a unit token (V, A, mA, ...).  A token with an explicit degree ("-55 °C")
    counts when both unrestricted reads (400 and 600 dpi) give the same value.
    """
    out: list[TemperatureLabel] = []
    for tok in tokens:
        degree = _degree_value(tok.plain)
        if degree is not None:
            if degree == _degree_value(tok.plain_hi) and _TEMP_RANGE[0] <= degree <= _TEMP_RANGE[1]:
                out.append(TemperatureLabel(degree, tok.box, True))
            continue
        if tok.digits is None or not _NUMBER_RE.match(tok.digits):
            continue
        # the unrestricted read may add non-alphanumeric marks a leader stub or
        # a degree glyph leaves ("25°", "100 ,"); letters or other digits refuse
        plain = re.match(r"^(-?\d+(?:\.\d+)?)[^\w]*$", tok.plain.replace("—", "-").replace("–", "-").strip())
        if plain is None or plain.group(1) != tok.digits:
            continue
        value = float(tok.digits)
        if not _TEMP_RANGE[0] <= value <= _TEMP_RANGE[1]:
            continue
        cy = 0.5 * (tok.box[1] + tok.box[3])
        right = [
            o for o in tokens
            if o is not tok and abs(0.5 * (o.box[1] + o.box[3]) - cy) < 0.6 * glyph_h
            and 0 <= o.box[0] - tok.box[2] <= 1.5 * glyph_h
        ]
        nxt = min(right, key=lambda o: o.box[0], default=None)
        follow = nxt.plain.strip() if nxt else ""
        if follow and _UNIT_RE.match(follow):
            continue
        out.append(TemperatureLabel(value, tok.box, bool(follow) and _degree_value(f"0 {follow}") is not None))
    return out


def _x_at_row(curve_native: list[tuple[float, float]], y: float) -> float:
    pts = sorted(curve_native, key=lambda p: p[1])
    ys = np.array([p[1] for p in pts])
    xs = np.array([p[0] for p in pts])
    return float(np.interp(y, ys, xs))


def _leader_tip(pix: np.ndarray, label_centre, pieces: list[np.ndarray], max_jump: float):
    """Far end of a leader, continued collinearly across curves its shaft crosses.

    Curve removal cuts a leader where it crosses a curve; the piece beyond
    continues on the same line.  From the current far end, step along the
    leader's principal direction up to *max_jump* px; a piece whose pixels lie
    within 2 px of that ray is joined and the walk repeats.
    """
    used = {id(pix)}
    pts = pix.astype(float)
    for _ in range(8):
        mean = pts.mean(axis=0)
        _u, _s, vt = np.linalg.svd(pts - mean, full_matrices=False)
        direction = vt[0]
        if np.dot(direction, mean - np.asarray(label_centre)) < 0:
            direction = -direction
        proj = (pts - mean) @ direction
        tip = mean + direction * proj.max()
        joined = None
        for other in pieces:
            if id(other) in used:
                continue
            rel = other.astype(float) - tip
            along = rel @ direction
            perp = np.abs(rel @ np.array([-direction[1], direction[0]]))
            if np.any((along > 0) & (along <= max_jump) & (perp <= 2.0)):
                joined = other
                break
        if joined is None:
            return tip, direction
        used.add(id(joined))
        pts = np.vstack([pts, joined.astype(float)])
    return tip, direction


def _walk_raw_ink(ink: np.ndarray, start, direction, max_len: float):
    """Follow *direction* from *start* through raw ink (±1 px across) to the
    first white gap longer than 2 px; return the last inked point.

    The leader's own end is the arrow tip on the target curve: the walk crosses
    every curve and rule the shaft crosses and stops beyond the last inked one.
    """
    normal = np.array([-direction[1], direction[0]])
    last = np.asarray(start, dtype=float)
    gap = 0
    for step in range(1, int(max_len)):
        p = np.asarray(start, dtype=float) + direction * step
        hit = False
        for off in (-1, 0, 1):
            x, y = (p + normal * off).round().astype(int)
            if 0 <= y < ink.shape[0] and 0 <= x < ink.shape[1] and ink[y, x]:
                hit = True
                break
        if hit:
            last, gap = p, 0
        else:
            gap += 1
            if gap > 2:
                break
    return last


def bind_labels(
    labels: list[TemperatureLabel],
    leaders: list[np.ndarray],
    curves_native: list[list[tuple[float, float]]],
    glyph_h: float,
    stroke: float,
    band_px: int = 0,
    pieces: list[np.ndarray] | None = None,
    raw_ink: np.ndarray | None = None,
) -> list[dict]:
    """One record per label: the curve index it binds to and the rule, or a reason.

    *band_px* is the width of a removed rule band: a leader crossing a rule
    between the label and the curves is cut there, so its start may lie that
    much further from the label.
    """
    records = []
    for lab in labels:
        x0, y0, x1, y1 = lab.box
        near = max(3.0, 0.8 * glyph_h) + band_px
        attached = [
            pix for pix in leaders
            if np.any((pix[:, 0] >= x0 - near) & (pix[:, 0] <= x1 + near)
                      & (pix[:, 1] >= y0 - near) & (pix[:, 1] <= y1 + near))
        ]
        record = {"value": lab.value, "box_native": list(lab.box), "curve": None}
        if len(attached) == 1:
            centre = (0.5 * (x0 + x1), 0.5 * (y0 + y1))
            tip, direction = _leader_tip(attached[0], centre, leaders if pieces is None else pieces, 3.0 * stroke + 8)
            if raw_ink is not None:
                tip = _walk_raw_ink(raw_ink, tip, direction, 12.0 * stroke)
            dists = sorted(
                (abs(_x_at_row(curve, float(tip[1])) - float(tip[0])), i) for i, curve in enumerate(curves_native)
            )
            limit = 1.5 * stroke + 4
            tip_rec = [round(float(tip[0]), 1), round(float(tip[1]), 1)]
            if dists[0][0] <= limit and (len(dists) == 1 or dists[1][0] > dists[0][0] + stroke):
                record.update(curve=dists[0][1], rule="leader", tip_native=tip_rec)
            else:
                record.update(reason="leader tip not on exactly one curve", tip_native=tip_rec)
        elif len(attached) > 1:
            record["reason"] = "several leader candidates touch the label"
        else:
            row = 0.5 * (y0 + y1)
            xs = [_x_at_row(curve, row) for curve in curves_native]
            if x1 < min(xs) - 0.5 * stroke:
                record.update(curve=int(np.argmin(xs)), rule="outer_side_left")
            elif x0 > max(xs) + 0.5 * stroke:
                record.update(curve=int(np.argmax(xs)), rule="outer_side_right")
            else:
                record["reason"] = "label inside the curve bundle without a leader"
        records.append(record)
    return records
