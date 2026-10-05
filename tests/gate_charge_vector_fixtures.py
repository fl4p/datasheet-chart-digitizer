"""Synthetic vector pages for gate-charge per-VDD tests (pt = px, scale 1)."""

from __future__ import annotations

from types import SimpleNamespace

import pymupdf

PLOT = (0, 0, 400, 400)
RECT = pymupdf.Rect(0, 0, 400, 400)
# three VDD curves sharing the rise and the start of the plateau at y=250
CURVES = [
    [(0, 400), (100, 250), (130, 250), (300, 20)],
    [(0, 400), (100, 250), (160, 250), (330, 20)],
    [(0, 400), (100, 250), (190, 250), (360, 20)],
]


def _pt(x, y):
    return SimpleNamespace(x=x, y=y)


def _stroke(polyline, width=2.0, dashes="[] 0"):
    items = [("l", _pt(*a), _pt(*b)) for a, b in zip(polyline, polyline[1:])]
    return {"type": "s", "color": (0, 0, 0), "fill": None, "width": width, "items": items, "dashes": dashes}


def _page(drawings, words=()):
    return SimpleNamespace(get_drawings=lambda: list(drawings), get_text=lambda kind, *a, **k: list(words))


def _word(text, x0, y0, x1, y1):
    return (x0, y0, x1, y1, text)


def _x_at(poly, y):
    for (ax, ay), (bx, by) in zip(poly, poly[1:]):
        if min(ay, by) <= y <= max(ay, by) and ay != by:
            return ax + (y - ay) / (by - ay) * (bx - ax)
    raise ValueError(y)
