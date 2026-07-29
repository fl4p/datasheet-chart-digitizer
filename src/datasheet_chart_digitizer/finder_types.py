"""Shared source-text types used by chart discovery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class PageText:
    page_num: int
    width_pt: float
    height_pt: float
    words: list[Word]
    text_source: str = "pdftotext"
