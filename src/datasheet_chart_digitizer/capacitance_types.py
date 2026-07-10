"""Shared data structures for MOSFET capacitance chart digitization."""

from __future__ import annotations

from dataclasses import dataclass

TRACE_COLORS_BGR = {
    "Ciss": (40, 40, 255),
    "Coss": (255, 90, 20),
    "Crss": (30, 180, 30),
}

@dataclass(frozen=True)
class PlotBox:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def height(self) -> int:
        return self.y1 - self.y0 + 1


@dataclass(frozen=True)
class Trace:
    name: str
    area: int
    bbox: tuple[int, int, int, int]
    points: list[tuple[int, int]]


@dataclass(frozen=True)
class CapAnchor:
    name: str
    value_pf: float
    vds_v: float


@dataclass(frozen=True)
class AxisCalibration:
    x_min_v: float
    x_max_v: float
    y_min_decade: float
    y_max_decade: float
    source: str
    x_ticks_v: tuple[float, ...]
    y_decades: tuple[float, ...]
    x_resid_v: float | None = None
    y_resid_dec: float | None = None
    x_scale: float | None = None
    x_offset: float | None = None
    y_scale: float | None = None
    y_offset: float | None = None
    x_source: str | None = None
    y_source: str | None = None
    y_gridline_px: tuple[float, ...] = ()
    y_grid_candidate_count: int | None = None
    y_grid_span_fraction: float | None = None
    y_grid_residual_px: float | None = None


@dataclass(frozen=True)
class GridlineFit:
    centers: list[float]
    candidate_count: int
    span_fraction: float
    residual_px: float


@dataclass(frozen=True)
class OutputChargeReference:
    qoss_pc: float | None
    vint_v: float | None
    coer_pf: float | None
    cotr_pf: float | None


@dataclass(frozen=True)
class VectorEdge:
    p0: tuple[float, float]
    p1: tuple[float, float]
    points: list[tuple[float, float]]
