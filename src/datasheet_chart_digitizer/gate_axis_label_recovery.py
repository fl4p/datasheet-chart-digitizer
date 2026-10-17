"""Recover printed gate-charge axis labels the bounded OCR pass missed.

The bounded OCR (``gate_axis_ocr.bounded_gate_axis_ocr``) reads a whole label
column at once; tesseract drops some labels of a raster column (NCE6890K p4:
VGS 10/8/6/4/0 read, the printed 2 lost). The served mapping is right -- it is
fitted on the labels that WERE read and checked on their rules -- but a
subset of the printed labels verifies less, and the review card looks
incomplete.

Recovery works per rule, never per guess:

1. The served ticks of an axis already seated on its rules define a label
   lattice (value step = the smallest served value gap; every served gap must
   be a whole multiple of it).
2. A lattice value with no served label is a candidate only where an
   observed rule lies at the pixel the served mapping implies for it.
3. Control: the label region of every SERVED value is OCRed with the same
   geometry (digits only). It must read the served values on one side of the
   axis; those reads fix the side, the label-to-rule offset and the label
   row/column the candidates are judged against.
4. The candidate region is OCRed the same way. The label is accepted ONLY if
   the word nearest the expected label position reads exactly the lattice
   value and sits within the label tolerance of that position, in the label
   column/row of the served labels (so the shared corner ``0`` of the other
   axis is not taken). Anything else -- empty, another number, off position --
   is refused; nothing is ever inferred.
5. Recovered labels then go through the same seating and grid check as the
   others; the caller keeps the recovery only if that check verifies.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import pymupdf

from .gridline_anchor import detect_axis_lines

# OCR of one label region: page-point rect -> (x0, y0, x1, y1, text) words.
OcrFn = Callable[[pymupdf.Rect], Sequence[tuple[float, float, float, float, str]]]

# (dpi, psm) tried in order on one isolated label until one reads anything:
# single word first; the block layout drops leading digits ("4" for 40,
# NCE0130GA p4) so it only runs when nothing else read. 600 dpi reads bold
# JPEG digits that 900 dpi loses (NCEP025F90D p4 "8", "2"), 900 the thin
# ones 600 misses.
_OCR_ATTEMPTS = ((600.0, 8), (900.0, 8), (600.0, 6), (900.0, 6))
DIGIT_WHITELIST = "0123456789.-"
# Label tolerance along the axis, page points: a tenth of the rule pitch,
# capped -- the same limit gate_axis_ocr._fill_ticks_from_other_reads puts on
# a label filled from another read.
LABEL_TOL_FRACTION = 0.1
LABEL_TOL_MAX_PT = 2.0
# Region size along the axis (half extent): under half a pitch so a
# neighbouring label is never inside, capped at about one label height (Y)
# or a three-digit label width (X).
_HALF_ALONG_FRACTION = 0.45
_Y_HALF_ALONG_MAX_PT = 8.0
_X_HALF_ALONG_MAX_PT = 12.0
# Region across the axis, page points from the frame: the bounded pass's
# band (24 pt beside the frame), stopping 1.5 pt short of it so grid stubs
# never fuse with a glyph (see bounded_gate_axis_ocr).
_Y_BAND_PT = (1.5, 24.0)
_X_BAND_PT = (1.0, 22.0)
_MIN_CONTROL_READS = 2
# Value gaps must be whole multiples of the lattice step to this precision.
_LATTICE_EPS = 1e-6


@dataclass(frozen=True)
class LabelRead:
    value: float
    along_pt: float
    cross_lo: float
    cross_hi: float
    text: str


@dataclass(frozen=True)
class Recovery:
    """Recovered labels of one axis, in the seating's continuous crop px."""

    labels_px: tuple[tuple[float, float], ...]
    record: dict


def _parse_digits(text: str) -> float | None:
    token = text.strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return None
    return float(token)


def label_lattice(served: Sequence[tuple[float, float]]) -> tuple[float, float, float] | None:
    """(step, slope px/value, intercept px) of the served ticks, or None.

    None when the served values are not one arithmetic lattice (a gap that is
    not a whole multiple of the smallest gap) or fewer than two ticks.
    """
    ticks = sorted((float(v), float(p)) for v, p in served)
    if len(ticks) < 2:
        return None
    values = np.asarray([v for v, _ in ticks])
    gaps = np.diff(values)
    if np.any(gaps <= 0):
        return None
    step = float(gaps.min())
    ratios = gaps / step
    if np.max(np.abs(ratios - np.round(ratios))) > _LATTICE_EPS * max(1.0, float(ratios.max())):
        return None
    slope, intercept = np.polyfit(values, np.asarray([p for _, p in ticks]), 1)
    if abs(float(slope)) < 1e-9:
        return None
    return step, float(slope), float(intercept)


def lattice_candidates(
    served: Sequence[tuple[float, float]], lo_px: float, hi_px: float, slack_px: float
) -> list[tuple[float, float]]:
    """Lattice values without a served label whose implied px is inside the plot."""
    lattice = label_lattice(served)
    if lattice is None:
        return []
    step, slope, intercept = lattice
    values = [float(v) for v, _ in served]
    anchor = min(values)
    out = []
    ends = [((px - intercept) / slope - anchor) / step
            for px in (min(lo_px, hi_px) - slack_px, max(lo_px, hi_px) + slack_px)]
    k_lo, k_hi = int(np.floor(min(ends))) - 1, int(np.ceil(max(ends))) + 1
    for k in range(max(k_lo, -1000), min(k_hi, 1000) + 1):
        value = round(anchor + k * step, 9)
        if any(abs(value - v) <= _LATTICE_EPS * max(1.0, abs(step)) for v in values):
            continue
        px = slope * value + intercept
        if min(lo_px, hi_px) - slack_px <= px <= max(lo_px, hi_px) + slack_px:
            out.append((value, float(px)))
    return out


def rule_at(gray, px: float, plot_box, orientation: str, tol_px: float) -> float | None:
    """Continuous px of an observed rule within *tol_px* of *px*, else None."""
    x0, y0, x1, y1 = (float(v) for v in plot_box)
    cross = (y0 - 0.5, y1 - 0.5) if orientation == "x" else (x0 - 0.5, x1 - 0.5)
    lines = detect_axis_lines(
        gray, orientation=orientation, along=(px - 0.5 - tol_px - 2, px - 0.5 + tol_px + 2),
        cross_span=cross, max_width=8, tick_band=3,
    )
    near = [
        line.center_px + 0.5 for line in lines
        if line.source in {"gridline", "split_rule", "tick_mark"}
        and abs(line.center_px + 0.5 - px) <= tol_px
    ]
    return min(near, key=lambda c: abs(c - px)) if near else None


def _reads(words, orientation: str) -> list[LabelRead]:
    out = []
    for x0, y0, x1, y1, text in words:
        value = _parse_digits(text)
        if value is None:
            continue
        if orientation == "y":
            out.append(LabelRead(value, 0.5 * (y0 + y1), x0, x1, text))
        else:
            out.append(LabelRead(value, 0.5 * (x0 + x1), y0, y1, text))
    return out


def accept_label(
    reads: Sequence[LabelRead],
    *,
    value: float,
    expected_along: float,
    tol: float,
    cross_ok: Callable[[LabelRead], bool],
) -> tuple[LabelRead | None, str]:
    """The nearest read must BE the label: exact value, on position, in the column."""
    if not reads:
        return None, "no digits read in the label region"
    nearest = min(reads, key=lambda r: abs(r.along_pt - expected_along))
    if abs(nearest.value - value) > 1e-9 * max(1.0, abs(value)):
        return None, f"region reads {nearest.text!r}, lattice implies {value:g}"
    offset = nearest.along_pt - expected_along
    if abs(offset) > tol:
        return None, f"{value:g} read {offset:+.2f} pt off its rule's label position (tolerance {tol:.2f} pt)"
    if not cross_ok(nearest):
        return None, f"{value:g} read outside the served labels' row/column"
    rivals = sorted({
        r.text for r in reads
        if abs(r.along_pt - expected_along) <= tol and abs(r.value - value) > 1e-9 * max(1.0, abs(value))
    })
    if rivals:
        return None, f"{value:g} read, but also {', '.join(rivals)} at the same position"
    return nearest, "read"


def _region(orientation: str, side: str, along_pt: float, half: float, frame_pt) -> pymupdf.Rect:
    fx0, fy0, fx1, fy1 = frame_pt
    if orientation == "y":
        if side == "left":
            return pymupdf.Rect(fx0 - _Y_BAND_PT[1], along_pt - half, fx0 - _Y_BAND_PT[0], along_pt + half)
        return pymupdf.Rect(fx1 + _Y_BAND_PT[0], along_pt - half, fx1 + _Y_BAND_PT[1], along_pt + half)
    return pymupdf.Rect(along_pt - half, fy1 + _X_BAND_PT[0], along_pt + half, fy1 + _X_BAND_PT[1])


# Ink isolation of a label region (crop grey, page points): runs closer than
# this merge; the run nearest the frame is the label (a rotated axis title or
# the X title beyond it otherwise reaches the band and garbles tesseract --
# NCE6890K p4: "54" for the printed 4 with the "Vgs" title in the band).
_INK = 200
_Y_MERGE_GAP_PT = 2.0
_X_MERGE_GAP_PT = 1.0
_PAD_PT = 1.5
# Along the axis, glyphs of one label sit closer than this (digit spacing).
_ALONG_MERGE_GAP_PT = 1.2
# A run thinner (X) or shorter than this is a tick mark / stub, not a digit.
_X_MIN_RUN_WIDTH_PT = 1.0
_MIN_DIGIT_HEIGHT_PT = 2.5


def _runs(mask: np.ndarray, merge: int) -> list[tuple[int, int]]:
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    runs = [[int(idx[0]), int(idx[0])]]
    for i in idx[1:]:
        if i - runs[-1][1] <= merge:
            runs[-1][1] = int(i)
        else:
            runs.append([int(i), int(i)])
    return [(a, b) for a, b in runs]


def _nearest_run(runs, centre: float) -> int:
    return min(range(len(runs)), key=lambda i: max(runs[i][0] - centre, centre - runs[i][1], 0.0))


def _clip(runs, i: int, limit: tuple[float, float], pad: float) -> tuple[float, float]:
    """Run *i* padded by *pad*, never past half the gap to a neighbouring run."""
    a, b = runs[i]
    lo = a - pad if i == 0 else max(a - pad, 0.5 * (runs[i - 1][1] + a))
    hi = b + 1 + pad if i == len(runs) - 1 else min(b + 1 + pad, 0.5 * (b + 1 + runs[i + 1][0]))
    return max(limit[0], lo), min(limit[1], hi)


def isolate_label(gray, rect: pymupdf.Rect, crop: pymupdf.Rect, scale: float,
                  orientation: str, side: str) -> pymupdf.Rect | None:
    """The part of *rect* holding one label's ink; None if there is no ink.

    Across the axis: the ink run nearest the frame (Y: a rotated title farther
    out is dropped; X: tick stubs are skipped). Along the axis: the run
    nearest the region's centre, so a neighbouring label's edge never reaches
    tesseract (NCE0130GA p4: "6300" for the printed 30 from a 22 pt window).
    """
    h, w = gray.shape
    c0 = int(np.floor((rect.x0 - crop.x0) * scale))
    c1 = int(np.ceil((rect.x1 - crop.x0) * scale))
    r0 = int(np.floor((rect.y0 - crop.y0) * scale))
    r1 = int(np.ceil((rect.y1 - crop.y0) * scale))
    if c0 < 0 or r0 < 0 or c1 > w or r1 > h:
        return rect  # outside the rendered crop: OCR the region as is
    ink = gray[r0:r1, c0:c1] < _INK
    if not ink.any():
        return None
    if orientation == "x":
        ink = ink.T  # rows: along the axis; columns: across it
    # across the axis
    if orientation == "y":
        runs = _runs(ink.any(axis=0), int(round(_Y_MERGE_GAP_PT * scale)))
        # nearest the frame, skipping grid stubs (a rule's ink is one row
        # tall; a digit is a label height -- NCEP025F90D p4 stubs left of the frame)
        order = range(len(runs) - 1, -1, -1) if side == "left" else range(len(runs))
        pick = next((
            i for i in order
            if np.ptp(np.flatnonzero(ink[:, runs[i][0]:runs[i][1] + 1].any(axis=1))) + 1
            >= _MIN_DIGIT_HEIGHT_PT * scale
        ), None)
        if pick is None:
            return None
    else:
        runs = _runs(ink.any(axis=0), int(round(_X_MERGE_GAP_PT * scale)))
        pick = next((
            i for i, (a, b) in enumerate(runs)
            if (b + 1 - a) / scale >= _MIN_DIGIT_HEIGHT_PT
            and np.ptp(np.flatnonzero(ink[:, a:b + 1].any(axis=1))) + 1 >= _X_MIN_RUN_WIDTH_PT * scale
        ), None)
        if pick is None:
            return None
    cross = _clip(runs, pick, (0, ink.shape[1]), _PAD_PT * scale)
    a, b = runs[pick]
    # along the axis, inside the chosen cross run
    along_runs = _runs(ink[:, a:b + 1].any(axis=1), int(round(_ALONG_MERGE_GAP_PT * scale)))
    if not along_runs:
        return None
    i = _nearest_run(along_runs, 0.5 * ink.shape[0])
    along = _clip(along_runs, i, (0, ink.shape[0]), _PAD_PT * scale)
    if orientation == "y":
        cols, rows = cross, along
    else:
        cols, rows = along, cross
    return pymupdf.Rect(
        crop.x0 + (c0 + cols[0]) / scale, crop.y0 + (r0 + rows[0]) / scale,
        crop.x0 + (c0 + cols[1]) / scale, crop.y0 + (r0 + rows[1]) / scale,
    )

def recover_axis_labels(
    gray: np.ndarray,
    *,
    orientation: str,
    served_px: Sequence[tuple[float, float]],
    label_px: Sequence[tuple[float, float]],
    plot_box,
    crop_box_pt,
    scale: float,
    tol_px: float,
    ocr: OcrFn,
) -> Recovery | None:
    """Labels read on lattice rules the served axis has no label for.

    *served_px* / *label_px*: (value, continuous crop px) of the served rule
    ticks and their label centres (the seating's identity input). Returns
    None when nothing was recovered; ``Recovery.record`` lists every
    candidate with its verdict (refusals included) for provenance.
    """
    if len(served_px) < 2 or len(label_px) < 2:
        return None
    lattice = label_lattice(served_px)
    if lattice is None:
        return None
    step, slope, _ = lattice
    pitch_px = abs(slope) * step
    crop = pymupdf.Rect(crop_box_pt)
    if orientation == "y":
        lo, hi = float(plot_box[1]), float(plot_box[3])
        to_pt = lambda px: crop.y0 + px / scale  # noqa: E731
    else:
        lo, hi = float(plot_box[0]), float(plot_box[2])
        to_pt = lambda px: crop.x0 + px / scale  # noqa: E731
    candidates = lattice_candidates(served_px, lo, hi, tol_px)
    if not candidates:
        return None
    frame_pt = (
        crop.x0 + plot_box[0] / scale, crop.y0 + plot_box[1] / scale,
        crop.x0 + plot_box[2] / scale, crop.y0 + plot_box[3] / scale,
    )
    pitch_pt = pitch_px / scale
    half = min(_HALF_ALONG_FRACTION * pitch_pt, _Y_HALF_ALONG_MAX_PT if orientation == "y" else _X_HALF_ALONG_MAX_PT)
    tol_pt = min(LABEL_TOL_FRACTION * pitch_pt, LABEL_TOL_MAX_PT)
    labels = {round(float(v), 9): float(p) for v, p in label_px}
    served = sorted((float(v), float(p)) for v, p in served_px)

    def read_region(rect: pymupdf.Rect, side: str) -> list[LabelRead]:
        tight = isolate_label(gray, rect, crop, scale, orientation, side)
        return [] if tight is None else _reads(ocr(tight), orientation)

    # Control: the served labels, same region geometry, must read back.
    best = None
    for side in (("left", "right") if orientation == "y" else ("bottom",)):
        controls = []
        for value, rule in served:
            label = labels.get(round(value, 9), rule)
            reads = read_region(_region(orientation, side, to_pt(label), half, frame_pt), side)
            hit = [r for r in reads if abs(r.value - value) <= 1e-9 * max(1.0, abs(value))]
            if hit:
                read = min(hit, key=lambda r: abs(r.along_pt - to_pt(label)))
                controls.append((value, rule, read))
        if best is None or len(controls) > len(best[1]):
            best = (side, controls)
    side, controls = best
    need = max(_MIN_CONTROL_READS, (len(served) + 1) // 2)
    record = {
        "method": "lattice_rule_ocr",
        "side": side,
        "control_reads": f"{len(controls)}/{len(served)}",
        "tolerance_pt": round(tol_pt, 3),
        "candidates": [],
    }
    if len(controls) < need:
        record["refused"] = f"control reads {len(controls)}/{len(served)} < {need}: label geometry not established"
        return Recovery((), record)
    offsets = np.asarray([read.along_pt - to_pt(rule) for _v, rule, read in controls])
    offset = float(np.median(offsets))
    if float(np.max(np.abs(offsets - offset))) > tol_pt:
        record["refused"] = (
            f"control label offsets scatter {float(np.ptp(offsets)):.2f} pt (> tolerance {tol_pt:.2f} pt)"
        )
        return Recovery((), record)
    cross_lo = min(read.cross_lo for *_x, read in controls)
    cross_hi = max(read.cross_hi for *_x, read in controls)
    if orientation == "x":
        centre = float(np.median([0.5 * (r.cross_lo + r.cross_hi) for *_x, r in controls]))
        height = float(np.median([r.cross_hi - r.cross_lo for *_x, r in controls]))

        def cross_ok(read: LabelRead) -> bool:
            return abs(0.5 * (read.cross_lo + read.cross_hi) - centre) <= 0.5 * height + 0.5
    else:
        def cross_ok(read: LabelRead) -> bool:
            return read.cross_hi >= cross_lo - 1.0 and read.cross_lo <= cross_hi + 1.0
    # Label positions handed to the seating: the original label centres'
    # offset from their rules, plus this read's measured deviation.
    orig_offsets = [labels[round(v, 9)] - rule for v, rule in served if round(v, 9) in labels]
    orig_offset_px = float(np.median(orig_offsets)) if orig_offsets else 0.0
    recovered = []
    for value, implied in candidates:
        entry = {"value": value, "implied_px": round(implied, 3)}
        rule = rule_at(gray, implied, plot_box, orientation, tol_px)
        if rule is None:
            entry["verdict"] = f"no observed rule within {tol_px:.2f}px of {implied:.1f}px"
            record["candidates"].append(entry)
            continue
        expected = to_pt(rule) + offset
        reads = read_region(_region(orientation, side, expected, half, frame_pt), side)
        read, verdict = accept_label(reads, value=value, expected_along=expected, tol=tol_pt, cross_ok=cross_ok)
        entry.update(rule_px=round(rule, 3), verdict=verdict)
        if read is not None:
            deviation_px = (read.along_pt - expected) * scale
            label = rule + orig_offset_px + deviation_px
            entry["label_px"] = round(label, 3)
            recovered.append((value, float(label)))
        record["candidates"].append(entry)
    return Recovery(tuple(recovered), record)


def page_ocr(pdf, page_number: int) -> OcrFn:
    """Digits-only OCR of one isolated label region (see ``_OCR_ATTEMPTS``)."""
    from .region_ocr import ocr_words_in_rect

    def run(rect: pymupdf.Rect):
        if rect.is_empty or rect.width < 2 or rect.height < 2:
            return []
        # Confidence is not
        # filtered: tesseract scores a lone raster digit low even when right
        # (NCE6890K p4 "8" below 30); the exact value + position decide.
        for dpi, psm in _OCR_ATTEMPTS:
            words = ocr_words_in_rect(
                pdf, page_number, rect, dpi=dpi, psm=psm,
                whitelist=DIGIT_WHITELIST, min_confidence=0.0,
            )
            if words:
                return words
        return []

    return run
