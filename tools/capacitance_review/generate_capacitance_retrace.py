#!/usr/bin/env python3
"""Re-trace 15 capacitance charts whose human-GREEN dsdig overlays were wrong.

Approved by Fab 2026-09-29.  For each chart:

* the production extractor (``dsdig find`` + ``dsdig digitize-capacitance``)
  is re-run at HEAD; its plot box is only the HINT for the chart's own frame,
  and its traces are compared with the retrace (reported, not served);
* the frame is the outermost full-span rule on each side (``own_frame``);
* both axes come from ``gridline_anchor``: printed labels identify values,
  observed gridlines carry every served pixel, and the served mapping is
  asserted at every consumed tick;
* vector charts are served from their own PDF paths; raster charts are tracked
  stroke by stroke and stop where a stroke cannot be isolated
  (``capacitance_retrace``);
* Crss is the curve lowest everywhere, Ciss/Coss are bound by the printed
  labels (leader lines / arrow tips honoured);
* the OLD reviewed overlay is registered back onto the page and its traces are
  mapped through the NEW calibration for the old-vs-new deltas.

usage: .venv/bin/python tools/capacitance_review/generate_capacitance_retrace.py --output <dir>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

from datasheet_chart_digitizer.capacitance_retrace import (
    CurveLabel,
    Frame,
    below_resolution_mask,
    bind_labels,
    curve_crossings,
    leader_lines,
    own_frame,
    pdf_curve_labels,
    pdf_tick_labels,
    suppress_curve_ink,
    track_raster_curves,
    vector_curve_paths,
)
from datasheet_chart_digitizer.gridline_anchor import anchor_axis_on_grid, served_pixel
from datasheet_chart_digitizer.numeric_axis import AxisTick, fit_axis_ticks
from datasheet_chart_digitizer.transfer_retrace import ink_label_centers

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import retrace_cv_render as render  # noqa: E402

DSDIG = REPO / ".venv" / "bin" / "dsdig"
DATASHEETS = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets")
BACKLOG = Path("/Users/fab/dev/pv/ee/dsdig-verify-backlog")
OLD_DPI = 180  # the reviewed dsdig crops/overlays were 180 dpi page renders
TICK_TOLERANCE_PX = 1.5
NAMES = ("Ciss", "Coss", "Crss")


@dataclass(frozen=True)
class Part:
    stem: str
    mfr: str
    part: str
    page: int
    diagram: int
    manifest_row: str
    kind: str  # "vector" | "raster"
    margin_pt: tuple[float, float, float, float]  # left, top, right, bottom around the frame
    defect: str
    dpi: int = 180
    y_unit: str = "pF"
    unit_flag: str | None = None
    # raster only: reviewer-read label values (low -> high pixel order) and bands
    x_values: tuple[float, ...] = ()
    y_values: tuple[float, ...] = ()  # top -> bottom
    x_model: str = "linear"
    y_model: str = "linear"
    x_band_pt: tuple[float, float, float, float] = (-4.4, 1.6, 10.0, 9.6)  # dx0, dy0 below frame, dx1, dy1
    y_band_pt: tuple[float, float, float, float] = (-19.2, -4.0, -1.2, 4.0)
    x_gap_pt: float = 3.6
    y_gap_pt: float = 1.2
    grid_ink: int = 200
    # raster curve labels (reviewer-measured glyph boxes, px at ``dpi``)
    labels_px: tuple = ()  # (name, glyph box[, arrow tip]) px at ``dpi``
    evidence: tuple[tuple[str, float, float, str], ...] = ()  # (curve, vds, cap, caption)
    notes: tuple[str, ...] = field(default=())


def _ao(stem, part, row, defect, evidence, notes=()):
    return Part(stem, "ao", part, 4, 8, row, "vector", (34.0, 12.0, 12.0, 36.0), defect, evidence=evidence, notes=notes)


def _nxp(stem, part, page, row, defect, evidence, notes=()):
    return Part(stem, "nxp", part, page, 14, row, "vector", (30.0, 16.0, 30.0, 36.0), defect, evidence=evidence, notes=notes)


def _nce(stem, part, page, diagram, row, defect, xv, yv, labels, evidence, margin_pt=(62.0, 12.0, 12.0, 34.0), **kw):
    return Part(stem, "nce", part, page, diagram, row, "raster", margin_pt, defect,
                x_values=xv, y_values=yv, labels_px=labels, evidence=evidence, **kw)


def _lin(lo, hi, step):
    return tuple(float(v) for v in np.arange(lo, hi + step / 2, step))


PARTS = [
    _ao("01_ao_AOL1454", "AOL1454", "MANIFEST.codex-ee-8ae6.ao.jsonl:41",
        "Crss traced along the 0 pF rail to ~24 V (real Crss never traced); plot box takes in the gate-charge "
        "panel to the left; Coss hooks onto the Crss label arrow at ~9 V",
        (("Crss", 5.0, 100.0, "old Crss on the 0 pF rail vs the real Crss stroke"),
         ("Coss", 9.0, 420.0, "old Coss hook onto the Crss label arrow at ~9 V"))),
    _ao("02_ao_AON6226", "AON6226", "MANIFEST.codex-ee-8ae6.ao.jsonl:49",
        "Crss traced along the 0 pF rail (real Crss never traced); plot box takes in the gate-charge panel",
        (("Crss", 10.0, 120.0, "old Crss on the 0 pF rail vs the real Crss stroke"),)),
    _ao("03_ao_AON6276", "AON6276", "MANIFEST.codex-ee-8ae6.ao.jsonl:53",
        "Crss traced along the 0 pF rail (real Crss never traced); plot box takes in the gate-charge panel",
        (("Crss", 8.0, 400.0, "old Crss on the 0 pF rail vs the real Crss stroke"),)),
    _ao("04_ao_AOT2610L", "AOT2610L", "MANIFEST.codex-ee-8ae6.ao.jsonl:89",
        "Crss traced along the 0 pF rail (real Crss never traced); plot box takes in the gate-charge panel; "
        "Coss misses the low-VDS rise",
        (("Crss", 3.0, 150.0, "old Crss on the 0 pF rail vs the real Crss stroke"),
         ("Coss", 2.0, 1000.0, "old Coss starts late: the low-VDS rise is untraced"))),
    _nce("05_nce_NCE4080", "NCE4080", 5, 7, "MANIFEST.codex-ee-8ae6.nce.jsonl:27",
         "Crss traced along the 0 pF rail; Coss steps at 16 V; plot box stops at 35 V of the 40 V frame",
         _lin(0, 40, 5), _lin(0, 7000, 1000)[::-1],
         (("Ciss", (449, 388, 477, 407)), ("Coss", (447, 570, 480, 590)), ("Crss", (219, 608, 249, 627))),
         (("Crss", 20.0, 450.0, "old Crss on the 0 pF rail vs the real Crss stroke"),
          ("Coss", 16.0, 700.0, "old Coss step at 16 V"),
          ("Coss", 37.5, 450.0, "old plot box ends at 35 V; the frame and curves run to 40 V"))),
    _nce("06_nce_NCE6008AS", "NCE6008AS", 5, 7, "MANIFEST.codex-ee-8ae6.nce.jsonl:35",
         "plot box starts at 5 V; Crss traced along the 0 axis rail; Coss and Crss merged onto one stroke",
         _lin(0, 30, 5), _lin(0, 3500, 500)[::-1],
         (("Ciss", (465, 424, 497, 444)), ("Coss", (230, 491, 268, 513)), ("Crss", (302, 538, 336, 558), (241.0, 589.0))),
         (("Crss", 10.0, 160.0, "old Crss on the 0 rail; old Coss on the Crss stroke"),
          ("Coss", 3.0, 450.0, "old plot box starts at 5 V: the 0-5 V region is untraced")),
         y_unit="nF (as printed)",
         unit_flag="y axis printed 'C Capacitance (nF)' 0..3500; almost certainly a datasheet typo for pF. "
                   "Values are served in the PRINTED unit, NOT rescaled -- Fab to decide."),
    _nce("07_nce_NCE60P28AK", "NCE60P28AK", 5, 7, "MANIFEST.codex-ee-8ae6.nce.jsonl:43",
         "Crss traced along the 0 pF rail below ~6 V; Coss jumps at 9 V; plot box stops at 25 V of the 30 V frame",
         _lin(0, 30, 5), _lin(0, 2400, 400)[::-1],
         (("Ciss", (387, 299, 412, 313)), ("Coss", (301, 518, 330, 529)), ("Crss", (391, 518, 411, 529), (298.0, 575.0))),
         (("Crss", 3.0, 150.0, "old Crss on the 0 pF rail below ~6 V"),
          ("Coss", 9.0, 250.0, "old Coss jump at 9 V (onto the Crss label arrow)"),
          ("Coss", 27.5, 110.0, "old plot box ends at 25 V; the frame and curves run to 30 V"))),
    _nce("08_nce_NCEP050N12D", "NCEP050N12D", 4, 7, "MANIFEST.codex-ee-8ae6.nce.jsonl:76",
         "Crss spikes at the left edge; plot box stops at 90 V of the 100 V frame",
         _lin(0, 100, 20), (1e4, 1e3, 1e2, 1e1),
         (("Ciss", (240, 240, 278, 251)), ("Coss", (285, 340, 330, 352)), ("Crss", (290, 475, 330, 488))),
         (("Crss", 1.0, 800.0, "old Crss spike at the left edge"),
          ("Coss", 95.0, 370.0, "old plot box ends at 90 V; the frame and curves run to 100 V")),
         y_model="log10", grid_ink=235),
    _nce("09_nce_NCES090P100T4", "NCES090P100T4", 8, 901, "MANIFEST.codex-ee-8ae6.nce.jsonl:107",
         "at low VDS Coss rides Ciss; the stored crop has no axis labels",
         (0.1, 1.0, 10.0, 100.0, 1000.0), (1e4, 1e3, 1e2, 1e1, 1.0), (),
         (("Coss", 0.5, 1150.0, "low-VDS band: Ciss and Coss strokes overlap (touching/shared, flagged)"),
          ("Coss", 2.5, 800.0, "old Coss rides the Ciss stroke after the split (~1.5-4 V); old Ciss absent below ~4 V")),
         dpi=300, x_model="log10", y_model="log10", x_band_pt=(-4.4, 4.5, 10.0, 12.0), x_gap_pt=7.0,
         margin_pt=(62.0, 12.0, 12.0, 42.0)),
    _nxp("10_nxp_PSMN6R1-25MLD", "PSMN6R1-25MLD", 9, "MANIFEST.codex-ee-8ae6.nxp.jsonl:74",
         "Ciss/Coss swap: the old traces put Ciss on the steep curve and Coss on the flat one below 1.4 V, then jump",
         (("Coss", 1.4, 800.0, "old full swap at 1.4 V (red/blue jump between the curves)"),)),
    _nxp("11_nxp_PSMN5R3-25MLD", "PSMN5R3-25MLD", 9, "MANIFEST.codex-ee-8ae6.nxp.jsonl:70",
         "old traces jump between Ciss and Coss where they cross (~2-3 V)",
         (("Coss", 2.5, 1000.0, "old traces around the Ciss/Coss crossing"),)),
    _nxp("12_nxp_PSMN2R4-30YLD", "PSMN2R4-30YLD", 9, "MANIFEST.codex-ee-8ae6.nxp.jsonl:50",
         "old traces jump between Ciss and Coss where they cross (~1-3 V)",
         (("Coss", 2.0, 2500.0, "old traces around the Ciss/Coss crossing"),)),
    _nxp("13_nxp_PSMNR70-30YLH", "PSMNR70-30YLH", 8, "MANIFEST.codex-ee-8ae6.nxp.jsonl:88",
         "old traces jump between Ciss and Coss where they cross (~1-3 V)",
         (("Coss", 2.0, 6500.0, "old traces around the Ciss/Coss crossing"),)),
    _nxp("14_nxp_PSMN1R5-50YLH", "PSMN1R5-50YLH", 8, "MANIFEST.codex-ee-8ae6.nxp.jsonl:40",
         "old traces jump between Ciss and Coss near their low-VDS approach; Ciss starts late",
         (("Ciss", 0.15, 8000.0, "old Ciss starts late at low VDS"),)),
    _nxp("15_nxp_PSMN1R9-40YSB", "PSMN1R9-40YSB", 8, "MANIFEST.codex-ee-8ae6.nxp.jsonl:43",
         "old traces jump between Ciss and Coss where they cross (~1 V); Ciss starts late",
         (("Ciss", 0.15, 4500.0, "old Ciss starts late at low VDS"),)),
]


# ------------------------------------------------------------------ helpers


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def _render(page, dpi: int, gray: bool = False) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, *(() if gray else (3,))).copy()


def _dsdig(part: Part, pdf: Path, cache: Path) -> dict:
    """Production extractor at HEAD: its box is the frame hint; its traces are compared."""
    run = cache / part.part
    run.mkdir(parents=True, exist_ok=True)
    if not (run / "charts.json").exists():
        subprocess.run([str(DSDIG), "find", "--out", str(run), str(pdf)], capture_output=True, check=False)
    charts = [c for c in json.loads((run / "charts.json").read_text()) if c["page"] == part.page and c["diagram"] == part.diagram]
    if len(charts) != 1:
        raise RuntimeError(f"{part.part}: dsdig find returns {len(charts)} panels for p{part.page} d{part.diagram}")
    chart = dict(charts[0], kind="capacitances")
    (run / "one.json").write_text(json.dumps([chart]))
    if not (run / "dig" / "capacitance_digitization.json").exists():
        subprocess.run([str(DSDIG), "digitize-capacitance", "--out", str(run / "dig"), str(run / "one.json")],
                       capture_output=True, check=False)
    values = json.loads((run / "dig" / "capacitance_digitization.json").read_text())[0]
    points = list(csv.DictReader(open(run / "dig" / values["points"])))
    return {"chart": chart, "values": values, "points": points}


def _labels_to_axis(part: Part, page, gray, frame: Frame, s: float, orientation: str):
    frame_pt = _frame_pt(frame, s)
    if part.kind == "vector":
        labels = pdf_tick_labels(page, frame_pt, orientation)
        ticks = [AxisTick(t, v, (cx if orientation == "x" else cy) * s - 0.5) for t, v, cx, cy in labels]
        model = "log10" if any(t.startswith("10^") for t, *_ in labels) else "linear"
        source = "PDF text layer (value) + glyph centre (identity only)"
    else:
        k = s  # band offsets are in pt
        if orientation == "x":
            d0, t0, d1, t1 = part.x_band_pt
            band = (int(frame.x0 + d0 * k), int(frame.y1 + t0 * k), int(frame.x1 + d1 * k), int(frame.y1 + t1 * k))
            centres = ink_label_centers(gray, band, "x", min_gap_px=int(part.x_gap_pt * k))
            values, model = part.x_values, part.x_model
        else:
            d0, t0, d1, t1 = part.y_band_pt
            band = (int(frame.x0 + d0 * k), int(frame.y0 + t0 * k), int(frame.x0 + d1 * k), int(frame.y1 + t1 * k))
            centres = ink_label_centers(gray, band, "y", min_gap_px=int(part.y_gap_pt * k))
            values, model = part.y_values, part.y_model
        if len(centres) != len(values):
            raise RuntimeError(f"{part.part} {orientation}: {len(centres)} label glyph groups for {len(values)} read values")
        ticks = [AxisTick(f"{v:g}", float(v), c) for v, c in zip(values, centres)]
        source = "values read by the reviewer; positions measured from glyph ink (ink_label_centers)"
    label_axis = fit_axis_ticks(ticks, orientation, model=model)  # type: ignore[arg-type]
    cross = (frame.y0, frame.y1) if orientation == "x" else (frame.x0, frame.x1)
    anchored = anchor_axis_on_grid(suppress_curve_ink(gray, frame), label_axis, orientation=orientation, cross_span=cross,
                                   name=f"{part.part}.{orientation}", ink_threshold=part.grid_ink)
    payload = anchored.payload()
    payload["label_source"] = source
    payload["model"] = anchored.axis.model
    edges = (frame.x0, frame.x1) if orientation == "x" else (frame.y1, frame.y0)
    payload["frame_values"] = [anchored.axis.value(p) for p in edges]
    # the frame may run at most one labelled interval past the outermost consumed tick
    line_px = sorted(a.line_px for a in anchored.anchors)
    interval = float(np.median(np.diff(line_px)))
    lo, hi = sorted(edges)
    payload["frame_past_last_tick_intervals"] = [round((line_px[0] - lo) / interval, 3), round((hi - line_px[-1]) / interval, 3)]
    if max(payload["frame_past_last_tick_intervals"]) > 1.05:
        raise RuntimeError(f"{part.part} {orientation}: frame runs more than one label interval past the last tick")
    return anchored, payload


def _frame_pt(frame: Frame, s: float):
    """The frame (pixel-index coordinates) in PDF points."""
    return pymupdf.Rect((frame.x0 + 0.5) / s, (frame.y0 + 0.5) / s, (frame.x1 + 0.5) / s, (frame.y1 + 0.5) / s)


def _densify(points, step=1.0):
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        n = max(1, int(math.ceil(math.dist(a, b) / step)))
        out += [(a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n) for t in range(1, n + 1)]
    return out


def _curves(part: Part, page, gray, frame: Frame, s: float):
    """Source curves in px at ``part.dpi``: list of (points, statuses), plus provenance."""
    if part.kind == "vector":
        frame_pt = _frame_pt(frame, s)
        paths = vector_curve_paths(page, frame_pt)
        if len(paths) != 3:
            raise RuntimeError(f"{part.part}: {len(paths)} vector curve paths inside the frame, need 3")
        curves = []
        for path in paths:
            # PDF points -> pixel-INDEX coordinates (the gridline anchor's convention)
            pts = [(x * s - 0.5, y * s - 0.5) for x, y in path.points]
            pts = _densify(pts, 1.0) if path.source == "vector_stroke" else pts[:: max(1, round(1 / (0.1 * s)))]
            curves.append((pts, ["ok"] * len(pts)))
        return curves, {"kind": sorted({p.source for p in paths}), "stop_reasons": {}}
    tracked, seed = track_raster_curves(gray, frame)
    curves = [([(float(x), y) for x, y in zip(t.xs, t.ys)], list(t.status)) for t in tracked]
    return curves, {"kind": ["raster_track"], "seed_column_px": seed,
                    "stop_reasons": {i: t.stop_reasons for i, t in enumerate(tracked)}}


def _identity(part: Part, page, curves, frame: Frame, s: float):
    served = [[p for p, st in zip(pts, sts) if st != "unresolved"] for pts, sts in curves]
    if part.labels_px:
        labels = [CurveLabel(entry[0], entry[1], entry[2] if len(entry) > 2 else None) for entry in part.labels_px]
        leaders = []
        source = "reviewer-measured label glyph boxes (raster chart)"
    else:
        frame_pt = _frame_pt(frame, s)

        def px(p):
            return (p[0] * s - 0.5, p[1] * s - 0.5)

        labels = [
            CurveLabel(lab.name, tuple(v * s - 0.5 for v in lab.bbox), None if lab.arrow_tip is None else px(lab.arrow_tip))
            for lab in pdf_curve_labels(page, frame_pt)
        ]
        leaders = [(px(a), px(b)) for a, b in leader_lines(page, frame_pt)]
        source = "PDF text layer labels (+ leader lines / arrow tips)"
    binding, evidence = bind_labels(served, labels, leaders, attach=1.5 * s)
    evidence["label_source"] = source
    return binding, evidence


def _registration(page_gray180: np.ndarray, old_rgb: np.ndarray):
    """Locate the old reviewed overlay (a 180 dpi crop) in the 180 dpi page render.

    Grey-level template matching alone is ambiguous on log grids (every decade
    looks alike; NCES090P100T4 matched one decade off), so the best few
    grey-level candidates are re-ranked by how much of the old trace ink lands
    on dark source ink -- an old trace was drawn over the page's curves, even
    where it followed the wrong one.
    """
    old = cv2.cvtColor(old_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    mx, mn = old_rgb.max(axis=2).astype(int), old_rgb.min(axis=2).astype(int)
    colour = (mx - mn) > 60
    old[colour] = 255.0
    res = cv2.matchTemplate(page_gray180.astype(np.float32), old, cv2.TM_CCOEFF_NORMED)
    h, w = old.shape
    dark = cv2.dilate((page_gray180 < 110).astype(np.uint8), np.ones((5, 5), np.uint8))
    candidates = []
    work = res.copy()
    for _ in range(8):
        _, score, _, (x, y) = cv2.minMaxLoc(work)
        window = dark[y:y + h, x:x + w]
        hit = float(window[colour].mean()) if colour.any() else 0.0
        candidates.append((hit, float(score), x, y))
        work[max(0, y - 8):y + 9, max(0, x - 8):x + 9] = -1.0
    best_score = max(c[1] for c in candidates)
    viable = [c for c in candidates if c[1] >= best_score - 0.15]
    hit, score, x, y = max(viable)
    window = page_gray180[y:y + h, x:x + w].astype(np.float32)
    mean_abs = float(np.abs(window - old)[~colour].mean())
    return (x, y), score, mean_abs, hit


def _old_traces(old_rgb: np.ndarray, offset, scale: float):
    """Old trace pixels by the dsdig overlay palette, long components only, in part px."""
    r, g, b = (old_rgb[..., i].astype(int) for i in range(3))
    masks = {
        "Ciss": (r > 180) & (g < 110) & (b < 110),
        "Coss": (b > 180) & (r < 110) & (g < 170),
        "Crss": (g > 140) & (r < 110) & (b < 110),
    }
    width = old_rgb.shape[1]
    out = {}
    for name, mask in masks.items():
        n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
        keep = np.zeros_like(mask)
        for i in range(1, n):
            # traces only: in-plot name labels ("Ciss" in red), anchor diamonds and their
            # captions are narrower than 12 % of the crop
            if stats[i, cv2.CC_STAT_WIDTH] >= 0.12 * width and stats[i, cv2.CC_STAT_HEIGHT] < 0.9 * old_rgb.shape[0]:
                keep |= lab == i
        pts = []
        for x in range(width):
            ys = np.flatnonzero(keep[:, x])
            if len(ys):
                pts.append(((offset[0] + x + 0.5) * scale - 0.5, (offset[1] + float(np.median(ys)) + 0.5) * scale - 0.5))
        out[name] = pts
    return out


def _value_rows(curves, binding, xa, ya, part: Part, zero_px):
    names = {index: name for name, index in binding.items()}
    rows = []
    for index, (pts, sts) in enumerate(curves):
        name = names[index]
        flags = below_resolution_mask([y for _, y in pts], zero_px, minimum_px=4.0 * part.dpi / 180) if zero_px is not None else [False] * len(pts)
        for (x, y), status, low in zip(pts, sts, flags):
            if low and status != "unresolved":
                status = "sub_resolution" if part.kind == "vector" else "unresolved"
            rows.append({"curve": name, "px": (x, y), "vds_V": xa.axis.value(x), "cap": ya.axis.value(y), "status": status})
    return rows


def _interp_new(rows, name, x_px):
    mine = sorted((r["px"][0], r["cap"]) for r in rows if r["curve"] == name and r["status"] != "unresolved")
    if not mine or x_px < mine[0][0] or x_px > mine[-1][0]:
        return None
    xs, cs = zip(*mine)
    return float(np.interp(x_px, xs, cs))


def _old_vs_new(old, rows, ya, zero_px, part: Part, xa_value):
    out = {}
    log = ya.axis.model == "log10"
    for name, pts in old.items():
        if not pts:
            out[name] = {"status": "no old trace pixels found"}
            continue
        rail_note = None
        if zero_px is not None:
            tol = 3.0 * part.dpi / 180
            on_rail = [abs(y - zero_px) <= tol for _, y in pts]
            frac = sum(on_rail) / len(on_rail)
            if frac >= 0.5:
                out[name] = {"status": "invalid: old curve traced on the 0 rail",
                             "fraction_of_old_columns_on_rail": round(frac, 3)}
                continue
            if frac > 0:
                rail_x = [p[0] for p, r in zip(pts, on_rail) if r]
                rail_note = {"fraction_of_old_columns_on_rail": round(frac, 3),
                             "on_rail_vds_V": [round(xa_value(min(rail_x)), 3), round(xa_value(max(rail_x)), 3)]}
                pts = [p for p, r in zip(pts, on_rail) if not r]
        best = None
        compared = 0
        for x, y in pts:
            new = _interp_new(rows, name, x)
            old_c = ya.axis.value(y)
            if new is None:
                continue
            if not log and zero_px is not None and abs(served_pixel(ya.axis, new) - zero_px) < 4.0 * part.dpi / 180:
                continue
            if log:
                if old_c <= 0 or new <= 0:
                    continue
                d = math.log10(old_c / new)
            else:
                if new == 0:
                    continue
                d = 100.0 * (old_c - new) / new
            compared += 1
            if best is None or abs(d) > abs(best[0]):
                best = (d, x, old_c, new)
        if best is None:
            out[name] = {"status": "no shared VDS with the new served curve"}
            continue
        # a sustained stretch of the old trace on another stroke is the defect; report it with the delta
        out[name] = {
            "status": "compared",
            "unit": "dex" if log else "percent_of_new",
            "max_delta": round(best[0], 4 if log else 2),
            "at_vds_V": round(float(served_value(best[1], rows)), 4),
            "old_value": round(best[2], 4),
            "new_value": round(best[3], 4),
            "compared_columns": compared,
            "old_vds_span_V": None,
        }
        if rail_note:
            out[name]["partly_invalid_on_rail"] = rail_note
    return out


def served_value(x_px, rows):
    near = min(rows, key=lambda r: abs(r["px"][0] - x_px))
    return near["vds_V"]


def _production_vs_retrace(dsd, rows, s: float, crop_box_pt):
    """Per curve: vertical px distance of the production trace from the retrace (same identity)."""
    out = {}
    k = s / 2.5  # production crops are 180 dpi (2.5 px/pt)
    ox, oy = crop_box_pt[0] * s, crop_box_pt[1] * s
    for name in NAMES:
        pts = [(ox + float(p["x_px"]) * k, oy + float(p["y_px"]) * k) for p in dsd["points"] if p["trace"] == name]
        mine = sorted((r["px"][0], r["px"][1]) for r in rows if r["curve"] == name and r["status"] != "unresolved")
        if not pts or not mine:
            out[name] = {"status": "missing"}
            continue
        xs, ys = zip(*mine)
        d = [abs(y - float(np.interp(x, xs, ys))) for x, y in pts if xs[0] <= x <= xs[-1]]
        out[name] = {
            "production_points": len(pts),
            "production_vds_px_span": [round(min(p[0] for p in pts), 1), round(max(p[0] for p in pts), 1)],
            "max_px": round(max(d), 2) if d else None,
            "p95_px": round(float(np.percentile(d, 95)), 2) if d else None,
        }
    return out


def _fmt(v):
    return f"{v:.4g}"


def _caption(part, xa_p, ya_p, rows, crossings, binding_ev, frame_vals, head):
    def axis_line(tag, p):
        errs = [abs(t["served_error_px"]) for t in p["ticks"]]
        return (f"{tag}: {p['model']}, frame {_fmt(min(frame_vals[tag]))}..{_fmt(max(frame_vals[tag]))}; "
                f"{len(p['ticks'])} ticks consumed, SERVED calibration max |err| {max(errs):.2f}px on gridlines")
    counts = {n: sum(1 for r in rows if r["curve"] == n and r["status"] not in ("unresolved",)) for n in NAMES}
    lines = [
        f"{part.part} p{part.page} d{part.diagram} -- C(V) retrace 2026-09-29 -- human_verified: false (pending Fab)",
        axis_line("x", xa_p) + "  [VDS, V]",
        axis_line("y", ya_p) + f"  [C, {part.y_unit}]",
        "identity: Crss = lowest curve at every VDS; Ciss/Coss by printed labels "
        f"(assignment cost {binding_ev['assignment_costs'][0]} vs {binding_ev['assignment_costs'][1]})",
        "served points: " + ", ".join(f"{n} {counts[n]}" for n in NAMES)
        + f"; Ciss/Coss crossings: {len(crossings)} (5x crops in crops/)",
        "blue crosshairs = SERVED calibration at every consumed printed tick, drawn on the frame edge",
        f"production dsdig at HEAD: {head}",
    ]
    if part.unit_flag:
        lines.append("UNIT FLAG: " + part.unit_flag)
    return lines


def _write_points(path: Path, rows, part: Part):
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["curve", "vds_V", f"cap_{part.y_unit.split()[0]}", "status", "x_px", "y_px", "render_dpi"])
        for r in rows:
            if r["status"] == "unresolved":
                continue
            writer.writerow([r["curve"], f"{r['vds_V']:.6g}", f"{r['cap']:.6g}", r["status"],
                             f"{r['px'][0]:.2f}", f"{r['px'][1]:.2f}", part.dpi])


def _served_summary(rows):
    out = {}
    for name in NAMES:
        mine = [r for r in rows if r["curve"] == name and r["status"] != "unresolved"]
        if not mine:
            out[name] = None
            continue
        out[name] = {
            "vds_V": [round(min(r["vds_V"] for r in mine), 4), round(max(r["vds_V"] for r in mine), 4)],
            "points": len(mine),
            "by_status": {st: sum(1 for r in mine if r["status"] == st) for st in sorted({r["status"] for r in mine})},
        }
    return out


# --------------------------------------------------------------------- main


def process(part: Part, output: Path, cache: Path) -> dict:
    pdf = DATASHEETS / part.mfr / f"{part.part}.pdf"
    s = part.dpi / 72.0
    up = 2 if part.dpi <= 200 else 1
    with pymupdf.open(pdf) as document:
        page = document[part.page - 1]
        rgb = _render(page, part.dpi)
        gray = _render(page, part.dpi, gray=True)
        big = _render(page, part.dpi * up)
        gray180 = _render(page, OLD_DPI, gray=True)
        dsd = _dsdig(part, pdf, cache)
        cb = dsd["chart"]["crop_box_pt"]
        pb = dsd["values"]["plot_box_px"]
        k = s / 2.5
        hint = (cb[0] * s + pb[0] * k, cb[1] * s + pb[1] * k, cb[0] * s + pb[2] * k, cb[1] * s + pb[3] * k)  # +-1 px is fine for a hint
        frame = own_frame(gray, hint, search_px=int(round(14 * part.dpi / 180)))
        xa, xa_p = _labels_to_axis(part, page, gray, frame, s, "x")
        ya, ya_p = _labels_to_axis(part, page, gray, frame, s, "y")
        curves, curve_prov = _curves(part, page, gray, frame, s)
        binding, binding_ev = _identity(part, page, curves, frame, s)
    zero_px = served_pixel(ya.axis, 0.0) if ya.axis.model == "linear" else None
    rows = _value_rows(curves, binding, xa, ya, part, zero_px)
    served = {n: [r["px"] for r in rows if r["curve"] == n and r["status"] != "unresolved"] for n in NAMES}
    crossings = []
    for a, b in (("Ciss", "Coss"),):
        for x, y in curve_crossings(served[a], served[b]):
            crossings.append({"curves": [a, b], "px": (x, y), "vds_V": xa.axis.value(x), "cap": ya.axis.value(y)})
    # ------------------------------------------------ old reviewed overlay
    old_row = BACKLOG / part.mfr / part.part / "digitized" / "capacitance"
    old_rgb = np.asarray(Image.open(old_row / "overlay.webp").convert("RGB"))
    (ox, oy), score, mean_abs, ink_hit = _registration(gray180, old_rgb)
    old_values = json.loads((old_row / "values.json").read_text())
    old_values = old_values[0] if isinstance(old_values, list) else old_values
    scale = part.dpi / OLD_DPI
    old = _old_traces(old_rgb, (ox, oy), scale)
    opb = old_values["plot_box_px"]
    old_box = ((ox + opb[0] + 0.5) * scale - 0.5, (oy + opb[1] + 0.5) * scale - 0.5,
               (ox + opb[2] + 0.5) * scale - 0.5, (oy + opb[3] + 0.5) * scale - 0.5)
    old_vs_new = _old_vs_new(old, rows, ya, zero_px, part, xa.axis.value)
    for name, pts in old.items():
        if pts and isinstance(old_vs_new.get(name), dict):
            old_vs_new[name]["old_vds_span_V"] = [round(xa.axis.value(min(p[0] for p in pts)), 4), round(xa.axis.value(max(p[0] for p in pts)), 4)]
    old_box_values = {"vds_V": [round(xa.axis.value(old_box[0]), 3), round(xa.axis.value(old_box[2]), 3)],
                      "cap": [round(ya.axis.value(old_box[3]), 4), round(ya.axis.value(old_box[1]), 4)]}
    # ------------------------------------------------ artifacts
    L, T, R, B = part.margin_pt
    crop_box = (max(0, int(frame.x0 - L * s)), max(0, int(frame.y0 - T * s)),
                min(rgb.shape[1], int(frame.x1 + R * s)), min(rgb.shape[0], int(frame.y1 + B * s)))
    src_path = render.base.save_webp(Image.fromarray(big).crop(tuple(round(v * up) for v in crop_box)), output / "sources" / f"{part.stem}.webp")
    prod = dsd["values"]
    head = (f"status={prod.get('status')}, trace_validation={prod.get('trace_validation_status')} "
            f"{prod.get('trace_validation_reasons')}, axis_trusted={prod.get('axis_calibration_trusted')} (reported, not served)")
    frame_vals = {"x": xa_p["frame_values"], "y": ya_p["frame_values"]}
    caption = _caption(part, xa_p, ya_p, rows, crossings, binding_ev, frame_vals, head)
    overlay_path = render.overlay(big, up, crop_box, frame, {"x": (xa.axis, xa_p), "y": (ya.axis, ya_p)}, rows,
                                  crossings, caption, output / "overlays" / f"{part.stem}.retrace.webp")
    crop_dir = output / "crops" / part.stem
    crops = render.zoom_crops(rgb, frame, {"x": (xa.axis, xa_p), "y": (ya.axis, ya_p)}, rows, crossings, crop_dir, part.stem)
    # the old box may reach into a neighbouring panel: show all of it
    ev_box = (max(0, min(crop_box[0], int(old_box[0]) - 12)), max(0, min(crop_box[1], int(old_box[1]) - 12)),
              min(rgb.shape[1], max(crop_box[2], int(old_box[2]) + 12)), min(rgb.shape[0], max(crop_box[3], int(old_box[3]) + 12)))
    evidence = [render.old_overlay_evidence(
        big, up, ev_box, old, old_box, frame,
        f"{part.part} OLD human-GREEN overlay, registered on the PDF page (180 dpi match score {score:.3f}, "
        f"mean |diff| {mean_abs:.1f} grey): red/blue/green = old Ciss/Coss/Crss, orange = old plot box, "
        f"magenta dashes = the chart's own frame. Old defect: {part.defect}",
        output / "evidence" / f"{part.stem}.old-overlay.webp")]
    for number, (name, vds, cap, text) in enumerate(part.evidence, 1):
        cx, cy = served_pixel(xa.axis, vds), served_pixel(ya.axis, cap)
        evidence.append(render.defect_zoom(
            rgb, crop_box, (cx, cy), (int(60 * part.dpi / 180), int(40 * part.dpi / 180)), old, rows,
            f"{part.part}: {text}. Left: the chart with the zoom window (red box) and the OLD traces; right: 4x "
            f"zoom around VDS {vds:g} V / C {cap:g}. Hollow squares = OLD trace (red Ciss, blue Coss, green Crss); "
            "haloed markers = NEW retrace (magenta Ciss, azure Coss, orange Crss).",
            output / "evidence" / f"{part.stem}.defect{number}.webp"))
    points_path = output / "points" / f"{part.stem}.csv"
    _write_points(points_path, rows, part)
    tick_check = {}
    for name, axis, payload in (("x", xa.axis, xa_p), ("y", ya.axis, ya_p)):
        rendered = []
        for t in payload["ticks"]:
            p = served_pixel(axis, t["value"])
            origin = crop_box[0] if name == "x" else crop_box[1]
            drawn = (round((p - origin) * up + (up - 1) / 2.0) - (up - 1) / 2.0) / up + origin
            rendered.append(abs(drawn - t["line_px"]))
        tick_check[name] = {
            "tolerance_px": TICK_TOLERANCE_PX,
            "per_tick_error_px": {t["text"]: t["served_error_px"] for t in payload["ticks"]},
            "max_abs_error_px": max(abs(t["served_error_px"]) for t in payload["ticks"]),
            "rendered_marker_max_abs_error_px": round(max(rendered), 3),
            "passed": max(abs(t["served_error_px"]) for t in payload["ticks"]) <= TICK_TOLERANCE_PX,
        }
    return {
        "stem": part.stem,
        "manufacturer": part.mfr,
        "part": part.part,
        "supersedes": f"{part.manifest_row} ({part.mfr}/{part.part}/digitized/capacitance; human_verified=true, green)",
        "old_defect": part.defect,
        "source_pdf": str(pdf),
        "source_pdf_sha256": _sha256(pdf),
        "page": part.page,
        "diagram": part.diagram,
        "render_dpi": part.dpi,
        "overlay_upscale": up,
        "frame_page_px": [frame.x0, frame.y0, frame.x1, frame.y1],
        "frame_hint_from_production_plot_box_px": [round(v, 1) for v in hint],
        "source": str(src_path.relative_to(output)),
        "source_crop_page_px": list(crop_box),
        "extractor_commit": _git_head(),
        "extractor_sha256": {
            name: _sha256(REPO / name)
            for name in (
                "src/datasheet_chart_digitizer/capacitance_retrace.py",
                "src/datasheet_chart_digitizer/gridline_anchor.py",
                "tools/capacitance_review/generate_capacitance_retrace.py",
                "tools/capacitance_review/retrace_cv_render.py",
            )
        },
        "y_unit": part.y_unit,
        "unit_flag": part.unit_flag,
        "axis": {"x": xa_p, "y": ya_p},
        "tick_check": tick_check,
        "curves": curve_prov,
        "identity": {"binding_curve_index": binding, "evidence": binding_ev},
        "served": _served_summary(rows),
        "crossings": [{"curves": c["curves"], "vds_V": round(c["vds_V"], 4), "cap": round(c["cap"], 4),
                       "px": [round(c["px"][0], 1), round(c["px"][1], 1)]} for c in crossings],
        "old_registration": {"page_px_at_180dpi": [ox, oy], "match_score": round(score, 4), "mean_abs_grey": round(mean_abs, 2),
                             "old_trace_ink_on_source_ink": round(ink_hit, 3)},
        "old_plot_box_values": old_box_values,
        "old_vs_new": old_vs_new,
        "production_dsdig_head": {
            "status": prod.get("status"),
            "status_reasons": prod.get("status_reasons"),
            "trace_validation_status": prod.get("trace_validation_status"),
            "trace_validation_reasons": prod.get("trace_validation_reasons"),
            "axis_calibration_trusted": prod.get("axis_calibration_trusted"),
            "plot_box_px_180dpi_crop": pb,
            "vs_retrace": _production_vs_retrace(dsd, rows, s, cb),
        },
        "human_verified": False,
        "status": "digitized-review-required" if not part.unit_flag else "digitized-review-required-unit-flagged",
        "overlay": str(overlay_path.relative_to(output)),
        "overlay_sha256": _sha256(overlay_path),
        "points_csv": str(points_path.relative_to(output)),
        "points_sha256": _sha256(points_path),
        "zoom_crops": crops,
        "evidence": [str(p.relative_to(output)) for p in evidence],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dsdig-cache", type=Path, default=REPO / "out" / "capacitance-retrace-2026-09-29" / "dsdig")
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()
    output = args.output.resolve()
    for sub in ("sources", "overlays", "points", "crops", "evidence"):
        (output / sub).mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    previous = {r["part"]: r for r in json.loads(manifest_path.read_text())} if manifest_path.exists() else {}
    records = []
    for part in PARTS:
        if args.only and part.part not in args.only:
            if part.part in previous:
                records.append(previous[part.part])
            continue
        try:
            record = process(part, output, args.dsdig_cache)
        except Exception as exc:  # fail closed, visibly
            record = {"stem": part.stem, "part": part.part, "manufacturer": part.mfr, "human_verified": False,
                      "status": "refused", "refusal": f"{type(exc).__name__}: {exc}", "old_defect": part.defect,
                      "supersedes": part.manifest_row}
        records.append(record)
        print(f"{part.part}: {record['status']}" + (f" ({record.get('refusal')})" if record["status"] == "refused" else ""), flush=True)
    manifest_path.write_text(json.dumps(records, indent=1, default=float) + "\n")
    review_state = {
        "created": "2026-09-29",
        "approved_by": "Fab 2026-09-29 (re-trace of 15 human-GREEN capacitance charts with wrong dsdig overlays)",
        "reviewer": None,
        "note": "agent review never sets human_verified; every item is pending Fab's review",
        "parts": [
            {
                "part": r["part"],
                "human_verified": False,
                "status": "pending-review" if r["status"] != "refused" else "pending-review-refused",
                "retrace_status": r["status"],
                "supersedes": r["supersedes"],
                "overlay": r.get("overlay"),
                "overlay_sha256": r.get("overlay_sha256"),
                "points_sha256": r.get("points_sha256"),
                "source_pdf_sha256": r.get("source_pdf_sha256"),
                "page": r.get("page"),
                "frame_page_px": r.get("frame_page_px"),
                "extractor_commit": r.get("extractor_commit"),
                "extractor_sha256": r.get("extractor_sha256"),
                "verdict": None,
            }
            for r in records
        ],
    }
    (output / "review-state.json").write_text(json.dumps(review_state, indent=1) + "\n")


if __name__ == "__main__":
    main()
