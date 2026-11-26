"""Shared loader for the cap_wrong_serve crop fixtures (see fixtures/cap_wrong_serve)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from datasheet_chart_digitizer import mosfet_capacitance as mc
from datasheet_chart_digitizer.capacitance_types import CapAnchor, PlotBox, Trace

FIXTURES = Path(__file__).parent / "fixtures" / "cap_wrong_serve"

# Spec-table anchors main parsed from each PDF (none for G6N90F, DMN39M1, 4435).
ANCHORS: dict[str, dict[str, tuple[float, float]]] = {
    "G6N90F": {},
    "AGMH022N10H": {"Ciss": (11500.0, 50.0), "Coss": (1480.0, 50.0), "Crss": (75.0, 50.0)},
    "DMN39M1LFVW-7-HXY.unicoded": {},
    "PSMN020-30MLC": {"Ciss": (430.0, 15.0), "Coss": (120.0, 15.0), "Crss": (70.0, 15.0)},
    "SP010N02GHTF": {"Ciss": (13420.0, 50.0), "Coss": (2034.0, 50.0), "Crss": (48.0, 50.0)},
    "4435": {},
    "BSC320N20NS3GATMA1": {"Ciss": (1770.0, 100.0), "Coss": (135.0, 100.0), "Crss": (4.0, 100.0)},
}


def load_gray(name: str) -> np.ndarray:
    gray = cv2.imread(str(FIXTURES / f"{name}.webp"), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise FileNotFoundError(FIXTURES / f"{name}.webp")
    return gray


def served_traces(name: str) -> tuple[np.ndarray, PlotBox, list[Trace]]:
    """The raster chain process_chart runs, minus axis calibration."""

    gray = load_gray(name)
    plot, _method = mc.find_capacitance_plot_box_with_method(gray)
    anchors = {
        trace: CapAnchor(trace, value, vds)
        for trace, (value, vds) in ANCHORS[name].items()
    }
    traces = mc.extract_trace_components(gray, plot, anchors)
    traces, _ = mc.select_trace_assignment(traces, plot, None, anchors)
    traces, _ = mc.repair_merged_ciss_coss_identity(traces, plot)
    return gray, plot, traces
