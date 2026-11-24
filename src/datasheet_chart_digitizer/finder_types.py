"""Shared source-text types used by chart discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

BBox = tuple[float, float, float, float]


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


class _WordLike(Protocol):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


class _PageLike(Protocol):
    width_pt: float
    height_pt: float
    words: list[_WordLike]


class _TitleLike(Protocol):
    bbox_pt: BBox
