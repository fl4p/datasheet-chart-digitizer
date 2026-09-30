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
