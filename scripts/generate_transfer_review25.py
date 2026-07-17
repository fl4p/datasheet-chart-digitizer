#!/usr/bin/env python3
"""Digitize the fixed 25-manufacturer MOSFET transfer review batch."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont

from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.crop_transform import CropTransform
from datasheet_chart_digitizer.capacitance_vector import _vector_curve_edges
from datasheet_chart_digitizer.transfer_review import (
    ReviewTrace,
    TransferAxis,
    calibrate_pixels,
    exchange_two_trace_identities_below,
    extract_transfer_traces,
    maximum_pairwise_collapse_fraction,
    review_trace_from_pixels,
    sustained_collapse_spans,
)
from datasheet_chart_digitizer.transfer_characteristics import (
    _extract_two_curves,
    _run_points,
    _split_monotone_edge_runs,
    _vector_plot_frame,
)


@dataclass(frozen=True)
class Sample:
    stem: str
    part: str
    manufacturer: str
    plot: tuple[int, int, int, int]
    axis: tuple[float, float, float, float, str]
    curves: int
    seeds: tuple[tuple[int, int], ...] = ()
    colors_rgb: tuple[tuple[int, int, int], ...] = ()
    erases: tuple[tuple[int, int, int, int], ...] = ()
    max_gap_fraction: float = 0.2
    secondary_seeds: tuple[tuple[int, int], ...] = ()
    secondary_grouped_seeded: bool | None = None
    secondary_max_gap_fraction: float | None = None
    secondary_use_ocr_masks: bool = True
    split_y: int | None = None
    grouped_seeded: bool = False
    identity_crossing_y: int | None = None
    upper_extensions: tuple[tuple[int, int, int, int], ...] = ()
    allowed_source_gap_fraction: float = 0.05
    minimum_span_fraction: float = 0.65
    anchor_repairs: tuple[tuple[int, tuple[tuple[int, int], ...]], ...] = ()


SAMPLES = [
    Sample("01_ao_AOD442", "AOD442", "Alpha & Omega", (118, 173, 560, 501), (2, 4.5, 0, 50, "linear"), 2),
    Sample("02_crmicro_CRSQ155N20N3", "CRSQ155N20N3", "CR Micro", (296, 167, 803, 558), (0, 7, 0, 100, "linear"), 2, ((642, 401), (653, 401)), grouped_seeded=True),
    Sample("03_crmicro_CRSS052N08N", "CRSS052N08N", "CR Micro", (350, 169, 890, 554), (0, 5.5, 0, 100, "linear"), 2),
    Sample(
        "04_crmicro_CRST065N08N",
        "CRST065N08N",
        "CR Micro",
        (122, 176, 650, 565),
        (3, 10, 0, 300, "linear"),
        2,
        ((397, 356), (426, 356)),
        grouped_seeded=True,
        identity_crossing_y=500,
        upper_extensions=((0, 544, 240, 260),),
        allowed_source_gap_fraction=0.1,
    ),
    Sample("05_diotec_DIT095N08", "DIT095N08", "Diotec", (340, 228, 656, 613), (3, 7, 0, 100, "linear"), 2),
    Sample("06_epc_space_EPC7018GSH", "EPC7018GSH", "EPC Space", (121, 179, 672, 597), (2, 5, 0, 350, "linear"), 3, colors_rgb=((0, 115, 185), (235, 35, 35), (65, 160, 120))),
    Sample("07_epc_space_FBG10N30BC", "FBG10N30BC", "EPC Space", (121, 35, 605, 359), (0.5, 5, 0, 100, "linear"), 3, colors_rgb=((0, 115, 185), (235, 35, 35), (65, 160, 120))),
    Sample(
        "08_good_ark_GSFH08140",
        "GSFH08140",
        "Good-Ark",
        (215, 197, 688, 568),
        (2, 6, 0, 140, "linear"),
        2,
        ((492, 409), (515, 409)),
        secondary_seeds=((421, 540), (452, 540)),
        secondary_grouped_seeded=False,
        secondary_max_gap_fraction=0.1,
        secondary_use_ocr_masks=False,
        split_y=520,
        grouped_seeded=True,
        anchor_repairs=(
            (
                0,
                (
                    (502, 409), (508, 395), (513, 382), (519, 369),
                    (524, 356), (529, 342), (534, 329), (538, 316),
                    (543, 303), (548, 289), (552, 276), (555, 270),
                    (557, 265), (564, 255),
                ),
            ),
            (
                1,
                (
                    (518, 409), (523, 395), (528, 382), (532, 369),
                    (536, 356), (540, 342), (544, 329), (548, 316),
                    (553, 303), (557, 289), (562, 276), (564, 270),
                    (565, 265), (564, 255),
                ),
            ),
        ),
    ),
    Sample("09_hxy_IMT65R020M2HXUMA1-HXY", "IMT65R020M2HXUMA1-HXY", "HXY", (276, 215, 815, 613), (0, 15, 0, 200, "linear"), 2),
    Sample("10_hxy_IPD65R380E6ATMA1-HXY", "IPD65R380E6ATMA1-HXY", "HXY", (218, 256, 709, 637), (0, 16, 0, 20, "linear"), 2),
    Sample("11_hxy_IPT65R033G7XTMA1-HXY", "IPT65R033G7XTMA1-HXY", "HXY", (123, 177, 673, 581), (0, 15, 0, 140, "linear"), 2),
    # The hot curve passes under the erased "TJ" callout (rows ~424-455): the
    # bridged span is interpolation over a text occlusion, not missing curve.
    Sample("12_littelfuse_MTI145WX100GD-SMD", "MTI145WX100GD-SMD", "Littelfuse", (118, 174, 567, 520), (2, 6, 0, 300, "linear"), 2, allowed_source_gap_fraction=0.12),
    Sample("13_mcc_MCACL170N08Y-TP", "MCACL170N08Y-TP", "MCC", (120, 178, 626, 553), (0, 6, 0, 250, "linear"), 2, ((531, 403), (539, 403)), grouped_seeded=True, identity_crossing_y=328, allowed_source_gap_fraction=0.1),
    Sample("14_nce_NCEP1520BK", "NCEP1520BK", "NCE", (147, 201, 653, 566), (0, 3, 0, 20, "linear"), 2),
    Sample("15_nce_NCEP15T14", "NCEP15T14", "NCE", (126, 172, 622, 563), (0, 8, 0, 200, "linear"), 2, ((419, 490), (428, 490)), grouped_seeded=True, identity_crossing_y=446),
    Sample("16_nxp_PSMN0R9-30YLD", "PSMN0R9-30YLD", "Nexperia", (115, 170, 516, 569), (0, 3.5, 0, 200, "linear"), 2),
    Sample(
        "17_nxp_PXN017-30QL",
        "PXN017-30QL",
        "Nexperia",
        (115, 178, 516, 579),
        (0, 4, 0, 30, "linear"),
        2,
        ((434, 220), (440, 220)),
        erases=((270, 415, 375, 500),),
        secondary_seeds=((379, 400), (386, 400)),
        split_y=320,
        grouped_seeded=True,
    ),
    Sample("18_panjit_PSMB055N08NS1_R2_00601", "PSMB055N08NS1_R2_00601", "Panjit", (120, 65, 621, 370), (0, 10, 0, 300, "linear"), 3, colors_rgb=((80, 145, 215), (105, 170, 70), (240, 110, 30)), allowed_source_gap_fraction=0.09),
    Sample("19_panjit_PSMP075N15NS1_T0_00601", "PSMP075N15NS1_T0_00601", "Panjit", (120, 174, 612, 499), (0, 10, 0, 300, "linear"), 3, colors_rgb=((80, 145, 215), (105, 170, 70), (240, 110, 30))),
    Sample(
        "20_renesas_RJK0853DPB-00_J5",
        "RJK0853DPB-00#J5",
        "Renesas",
        (346, 173, 700, 528),
        (0, 5, 0, 50, "linear"),
        3,
        ((527, 393), (534, 393), (542, 393)),
        erases=((390, 440, 625, 510),),
        secondary_seeds=((508, 513), (517, 513), (526, 513)),
        split_y=475,
        grouped_seeded=True,
        allowed_source_gap_fraction=0.25,
    ),
    Sample(
        "21_renesas_RJK0856DPB-00_J5",
        "RJK0856DPB-00#J5",
        "Renesas",
        (346, 173, 700, 528),
        (0, 5, 0, 50, "linear"),
        3,
        ((631, 353), (640, 353), (648, 353)),
        erases=((490, 390, 624, 500),),
        secondary_seeds=((587, 513), (598, 513), (609, 513)),
        split_y=445,
        grouped_seeded=True,
        allowed_source_gap_fraction=0.35,
    ),
    Sample(
        "22_renesas_RJK1001DPP-A0_T2",
        "RJK1001DPP-A0#T2",
        "Renesas",
        (350, 169, 700, 520),
        (0, 8, 0, 100, "linear"),
        3,
        ((539, 249), (544, 249), (549, 249)),
        erases=((400, 335, 625, 490),),
        max_gap_fraction=0.5,
        secondary_seeds=((504, 509), (513, 509), (521, 509)),
        split_y=412,
        grouped_seeded=True,
        allowed_source_gap_fraction=0.55,
    ),
    Sample(
        "23_rohm_RS6N120BHTB1",
        "RS6N120BHTB1",
        "ROHM",
        (303, 249, 750, 712),
        (0, 5, 0.001, 100, "log10"),
        4,
        ((538, 569), (559, 569), (579, 569), (598, 569)),
        grouped_seeded=True,
    ),
    Sample(
        "24_st_STK295N10F8AG",
        "STK295N10F8AG",
        "ST",
        (115, 178, 491, 553),
        (0, 7, 0, 800, "linear"),
        3,
        ((389, 438), (401, 438), (408, 438)),
        grouped_seeded=True,
        allowed_source_gap_fraction=0.06,
        minimum_span_fraction=0.6,
    ),
    Sample("25_yageo_xsemi_XP10N3R8IT", "XP10N3R8IT", "Yageo/Xsemi", (348, 239, 756, 609), (0, 10, 0, 150, "linear"), 3, ((534, 499), (544, 499), (551, 499)), grouped_seeded=True),
]


COLORS = [(230, 35, 50), (0, 115, 230), (0, 160, 85), (235, 130, 20)]
VECTOR_PARTS = {"PXN017-30QL", "STK295N10F8AG", "XP10N3R8IT"}
VECTOR_SOURCES = {
    "PXN017-30QL": {
        "kind": "pdf-vector-paths",
        "pdf": "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/nxp/PXN017-30QL.pdf",
        "page": 8,
    },
    "STK295N10F8AG": {
        "kind": "pdf-vector-paths",
        "pdf": "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st/STK295N10F8AG.pdf",
        "page": 5,
    },
    "XP10N3R8IT": {
        "kind": "pdf-vector-paths",
        "pdf": "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/yageo_xsemi/XP10N3R8IT.pdf",
        "page": 4,
    },
}

TEMPERATURE_LABELS = {
    "AOD442": [25, 125], "CRSQ155N20N3": [25, 125], "CRSS052N08N": [25, 125],
    "CRST065N08N": [25, 125], "DIT095N08": [25, 125], "EPC7018GSH": [-55, 25, 125],
    "FBG10N30BC": [-55, 25, 125], "GSFH08140": [25, 125], "IMT65R020M2HXUMA1-HXY": [25, 175],
    "IPD65R380E6ATMA1-HXY": [25, 175], "IPT65R033G7XTMA1-HXY": [25, 175],
    "MTI145WX100GD-SMD": [25, 125], "MCACL170N08Y-TP": [25, 125], "NCEP1520BK": [25, 125],
    "NCEP15T14": [25, 175], "PSMN0R9-30YLD": [25, 150], "PXN017-30QL": [25, 150],
    "PSMB055N08NS1_R2_00601": [-40, 25, 125], "PSMP075N15NS1_T0_00601": [-40, 25, 125],
    "RJK0853DPB-00#J5": [-25, 25, 75], "RJK0856DPB-00#J5": [-25, 25, 75],
    "RJK1001DPP-A0#T2": [-25, 25, 75], "RS6N120BHTB1": [-25, 25, 75, 125],
    "STK295N10F8AG": [25, 55, 175], "XP10N3R8IT": [-55, 25, 150],
}

TEMPERATURE_BY_CURVE = {
    "AOD442": [125, 25],
    "CRSQ155N20N3": [125, 25],
    "CRSS052N08N": [125, 25],
    "CRST065N08N": [25, 125],
    "DIT095N08": [125, 25],
    "EPC7018GSH": [25, 125, -55],
    "FBG10N30BC": [25, 125, -55],
    "GSFH08140": [125, 25],
    "IMT65R020M2HXUMA1-HXY": [175, 25],
    "IPD65R380E6ATMA1-HXY": [175, 25],
    "IPT65R033G7XTMA1-HXY": [175, 25],
    "MTI145WX100GD-SMD": [125, 25],
    "MCACL170N08Y-TP": [25, 125],
    "NCEP1520BK": [125, 25],
    "NCEP15T14": [25, 175],
    "PSMN0R9-30YLD": [150, 25],
    "PXN017-30QL": [25, 150],
    "PSMB055N08NS1_R2_00601": [-40, 25, 125],
    "PSMP075N15NS1_T0_00601": [-40, 25, 125],
    "RJK0853DPB-00#J5": [75, 25, -25],
    "RJK0856DPB-00#J5": [75, 25, -25],
    "RJK1001DPP-A0#T2": [75, 25, -25],
    "RS6N120BHTB1": [125, 75, 25, -25],
    # Source callouts identify the left, middle, and right branches respectively.
    "STK295N10F8AG": [25, 175, 55],
    "XP10N3R8IT": [150, 25, -55],
}


def _ocr_boxes(path: Path, plot: PlotBox) -> list[tuple[int, int, int, int]]:
    result = subprocess.run(
        ["tesseract", path.name, "stdout", "--psm", "11", "tsv"],
        cwd=path.parent,
        check=False,
        capture_output=True,
        text=True,
    )
    boxes = []
    rows = csv.DictReader(result.stdout.splitlines(), delimiter="\t")
    for row in rows:
        if not (row.get("text") or "").strip():
            continue
        if float(row.get("conf") or -1) < 20:
            continue
        x = int(row["left"])
        y = int(row["top"])
        w = int(row["width"])
        h = int(row["height"])
        # Tesseract occasionally classifies a colored curve segment as one
        # giant low-confidence "word".  Only erase plausible text boxes.
        if w > 0.35 * plot.width or h > 0.16 * plot.height:
            continue
        if x < plot.x1 and x + w > plot.x0 and y < plot.y1 and y + h > plot.y0:
            boxes.append((x, y, x + w, y + h))
    return boxes


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _colored_traces(rgb: np.ndarray, sample: Sample, plot: PlotBox, axis: TransferAxis):
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    traces = []
    for target_rgb in sample.colors_rgb:
        target = np.uint8([[target_rgb]])
        target_hue = int(cv2.cvtColor(target, cv2.COLOR_RGB2HSV)[0, 0, 0])
        hue = hsv[:, :, 0].astype(int)
        distance = np.minimum(abs(hue - target_hue), 180 - abs(hue - target_hue))
        keep = (distance <= 14) & (hsv[:, :, 1] >= 45) & (hsv[:, :, 2] >= 60)
        isolated = np.full_like(rgb, 255)
        isolated[keep] = rgb[keep]
        extracted = extract_transfer_traces(isolated, plot, axis, 1)
        traces.extend(extracted)
    return traces


def _vector_curves_to_review_traces(
    vector_curves, vector_plot: PlotBox, plot: PlotBox, axis: TransferAxis
):
    traces = []
    for vector_pixels in vector_curves:
        points = calibrate_pixels(vector_pixels, vector_plot, axis)
        review_pixels = []
        for vgs, current in points:
            fx = (vgs - axis.vgs_min_v) / (axis.vgs_max_v - axis.vgs_min_v)
            fy = (current - axis.id_min_a) / (axis.id_max_a - axis.id_min_a)
            review_pixels.append(
                (
                    min(
                        plot.x1,
                        max(plot.x0, int(round(plot.x0 + fx * plot.width))),
                    ),
                    min(
                        plot.y1,
                        max(plot.y0, int(round(plot.y1 - fy * plot.height))),
                    ),
                )
            )
        traces.append(review_trace_from_pixels(review_pixels, plot, axis))
    return traces


def _pxn_vector_traces(plot: PlotBox, axis: TransferAxis):
    """Preserve both crossing PXN branches using their PDF drawing streams."""
    pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/nxp/PXN017-30QL.pdf")
    chart = pymupdf.Rect(87.613, 82.857, 261.093, 262.268)
    clip = pymupdf.Rect(chart.x0 - 15, chart.y0 - 15, chart.x1 + 15, chart.y1 + 15)
    scale = 3.0
    with pymupdf.open(pdf) as document:
        page = document[7]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
        transform = CropTransform(clip.x0, clip.y0, scale, scale)
        vector_plot = _vector_plot_frame(page, transform, (pixmap.height, pixmap.width))
        if vector_plot is None:
            raise RuntimeError("PXN vector plot frame was not found")
        vector_curves = _extract_two_curves(page, transform, vector_plot, pymupdf)
    return _vector_curves_to_review_traces(vector_curves, vector_plot, plot, axis)


def _xp_vector_traces(plot: PlotBox, axis: TransferAxis):
    """Keep all three XP10 temperature identities from its compound PDF path."""
    pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/yageo_xsemi/XP10N3R8IT.pdf")
    rect = pymupdf.Rect(333.72, 558.36, 496.44, 706.32)
    vector_plot = PlotBox(0, 0, plot.width, plot.height)
    transform = CropTransform(
        rect.x0,
        rect.y0,
        vector_plot.width / rect.width,
        vector_plot.height / rect.height,
    )
    with pymupdf.open(pdf) as document:
        page = document[3]
        edges = _vector_curve_edges(page.get_drawings(), rect)
        vector_curves = []
        for run in _split_monotone_edge_runs(edges, rect.width):
            points = _run_points(run, transform, vector_plot)
            if len(points) < 40:
                continue
            y_span = max(y for _x, y in points) - min(y for _x, y in points)
            if y_span >= 0.9 * vector_plot.height:
                vector_curves.append(points)
    if len(vector_curves) != 3:
        raise RuntimeError(f"expected 3 XP10 vector curves, found {len(vector_curves)}")
    vector_curves.sort(
        key=lambda points: min(
            (point for point in points if point[1] == max(y for _x, y in points)),
            key=lambda point: point[0],
        )[0]
    )
    return _vector_curves_to_review_traces(vector_curves, vector_plot, plot, axis)


def _stk_vector_traces(plot: PlotBox, axis: TransferAxis):
    """Retain the three independent ST paths through their close ZTC region."""
    pdf = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st/STK295N10F8AG.pdf")
    frame = pymupdf.Rect(350.0787, 590.2717, 500.3149, 740.5079)
    with pymupdf.open(pdf) as document:
        drawings = document[4].get_drawings()
        curves = [
            drawing
            for drawing in drawings
            if 0.45 <= float(drawing.get("width") or 0) <= 0.55
            and len(drawing["items"]) >= 20
            and abs(drawing["rect"].x0 - frame.x0) < 0.1
            and abs(drawing["rect"].y1 - frame.y1) < 0.1
            and drawing["rect"].x1 > 478
            and drawing["rect"].y0 < 650
        ]
    if len(curves) != 3:
        raise RuntimeError(f"expected 3 ST vector curves, found {len(curves)}")

    traces = []
    for drawing in curves:
        pdf_points = []
        for item in drawing["items"]:
            if item[0] == "l":
                controls = item[1:]
                if controls[0] == controls[1]:
                    continue
                steps = 12
                for raw_t in np.linspace(0.0, 1.0, steps, endpoint=True):
                    t = float(raw_t)
                    pdf_points.append(
                        pymupdf.Point(
                            controls[0].x + t * (controls[1].x - controls[0].x),
                            controls[0].y + t * (controls[1].y - controls[0].y),
                        )
                    )
            elif item[0] == "c":
                p0, p1, p2, p3 = item[1:]
                for raw_t in np.linspace(0.0, 1.0, 32, endpoint=True):
                    t = float(raw_t)
                    weights = (
                        (1 - t) ** 3,
                        3 * (1 - t) ** 2 * t,
                        3 * (1 - t) * t**2,
                        t**3,
                    )
                    point = pymupdf.Point(
                        sum(
                            weight * point.x
                            for weight, point in zip(weights, (p0, p1, p2, p3))
                        ),
                        sum(
                            weight * point.y
                            for weight, point in zip(weights, (p0, p1, p2, p3))
                        ),
                    )
                    pdf_points.append(point)
        baseline = [point for point in pdf_points if point.y >= frame.y1 - 0.25]
        rising = [point for point in pdf_points if point.y < frame.y1 - 0.25]
        if baseline:
            pdf_points = [max(baseline, key=lambda point: point.x)] + rising
        pixels = [
            (
                int(round(plot.x0 + (point.x - frame.x0) / frame.width * plot.width)),
                int(round(plot.y0 + (point.y - frame.y0) / frame.height * plot.height)),
            )
            for point in pdf_points
        ]
        traces.append(review_trace_from_pixels(pixels, plot, axis))

    traces.sort(
        key=lambda trace: min(trace.points, key=lambda point: abs(point[1] - 100))[0]
    )
    return traces


def _merge_seeded_segments(upper, lower, plot: PlotBox, axis: TransferAxis, split_y: int):
    merged = []
    for high_trace, low_trace in zip(upper, lower):
        pixels = [point for point in high_trace.pixels if point[1] <= split_y]
        pixels += [point for point in low_trace.pixels if point[1] > split_y]
        trace = review_trace_from_pixels(pixels, plot, axis)
        merged.append(
            replace(
                trace,
                maximum_source_gap_fraction=max(
                    high_trace.maximum_source_gap_fraction,
                    low_trace.maximum_source_gap_fraction,
                ),
            )
        )
    return merged


def _replace_upper_segment(
    original: ReviewTrace,
    extension: ReviewTrace,
    split_y: int,
    plot: PlotBox,
    axis: TransferAxis,
) -> ReviewTrace:
    pixels = [point for point in extension.pixels if point[1] <= split_y]
    pixels += [point for point in original.pixels if point[1] > split_y]
    trace = review_trace_from_pixels(pixels, plot, axis)
    return replace(
        trace,
        maximum_source_gap_fraction=max(
            original.maximum_source_gap_fraction,
            extension.maximum_source_gap_fraction,
        ),
    )


def _repair_trace_span_from_anchors(
    trace: ReviewTrace,
    anchors: tuple[tuple[int, int], ...],
    plot: PlotBox,
    axis: TransferAxis,
) -> ReviewTrace:
    """Replace one unreliable span with source-read centerline anchors."""
    ordered = sorted(anchors, key=lambda point: point[1])
    anchor_y = np.array([y for _x, y in ordered], dtype=float)
    anchor_x = np.array([x for x, _y in ordered], dtype=float)
    lo = int(anchor_y[0])
    hi = int(anchor_y[-1])
    rows = np.arange(lo, hi + 1)
    repaired = [
        (int(round(x)), int(y))
        for x, y in zip(np.interp(rows, anchor_y, anchor_x), rows, strict=True)
    ]
    pixels = [point for point in trace.pixels if not lo <= point[1] <= hi]
    pixels.extend(repaired)
    result = review_trace_from_pixels(pixels, plot, axis)
    return replace(
        result,
        maximum_source_gap_fraction=trace.maximum_source_gap_fraction,
    )


CALIBRATION_INK = (0, 90, 200)
COLLAPSE_GRAY = (105, 105, 105)


def _label(draw: ImageDraw.ImageDraw, xy, text: str, anchor: str, size: int = 15) -> None:
    font = _font(size)
    x, y = xy
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            draw.text((x + dx, y + dy), text, font=font, fill=(255, 255, 255), anchor=anchor)
    draw.text((x, y), text, font=font, fill=CALIBRATION_INK, anchor=anchor)


def _draw_calibration_marks(draw: ImageDraw.ImageDraw, plot: PlotBox, axis: TransferAxis) -> None:
    """Crosshairs + endpoint values at the exact pixels the calibration uses."""
    for cx, cy in ((plot.x0, plot.y1), (plot.x1, plot.y1), (plot.x0, plot.y0), (plot.x1, plot.y0)):
        draw.line((cx - 9, cy, cx + 9, cy), fill=CALIBRATION_INK, width=2)
        draw.line((cx, cy - 9, cx, cy + 9), fill=CALIBRATION_INK, width=2)
        draw.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), outline=CALIBRATION_INK, width=2)
    _label(draw, (plot.x0, plot.y1 + 12), f"{axis.vgs_min_v:g}", "ma")
    _label(draw, (plot.x1, plot.y1 + 12), f"{axis.vgs_max_v:g} V", "ma")
    _label(draw, (plot.x0 - 12, plot.y1), f"{axis.id_min_a:g}", "rm")
    _label(draw, (plot.x0 - 12, plot.y0), f"{axis.id_max_a:g} A", "rm")
    _label(
        draw,
        (plot.x0, plot.y0 - 24),
        f"CAL x: Vgs {axis.vgs_min_v:g}..{axis.vgs_max_v:g} V (linear)   "
        f"y: Id {axis.id_min_a:g}..{axis.id_max_a:g} A ({axis.id_scale})",
        "la",
        16,
    )


def _trace_segments(
    pixels: list[tuple[int, int]], collapsed: set[int]
) -> list[tuple[bool, list[tuple[int, int]]]]:
    """Split an ordered pixel trace into alternating clean/collapsed runs.

    Adjacent runs share their boundary point so the drawn polyline stays
    connected.
    """
    segments: list[tuple[bool, list[tuple[int, int]]]] = []
    for point in pixels:
        flag = point[1] in collapsed
        if segments and segments[-1][0] == flag:
            segments[-1][1].append(point)
        else:
            if segments:
                segments[-1][1].append(point)
            segments.append((flag, [point]))
    return [(flag, run) for flag, run in segments if len(run) >= 2]


def _overlay(
    image: Image.Image,
    sample: Sample,
    traces,
    target: Path,
    collapsed_rows: list[set[int]] | None = None,
) -> None:
    source = image.convert("RGB")
    footer_height = 78
    canvas = Image.new("RGB", (source.width, source.height + footer_height), "white")
    canvas.paste(source, (0, 0))
    draw = ImageDraw.Draw(canvas)
    plot = PlotBox(*sample.plot)
    axis = TransferAxis(*sample.axis)
    draw.rectangle(sample.plot, outline=(225, 0, 190), width=3)
    collapsed_rows = collapsed_rows or [set() for _ in traces]
    any_collapse = any(collapsed_rows)
    for trace in traces:
        draw.line(trace.pixels, fill=(255, 255, 255), width=5, joint="curve")
    for index, trace in enumerate(traces):
        for collapsed, run in _trace_segments(trace.pixels, collapsed_rows[index]):
            draw.line(
                run,
                fill=COLLAPSE_GRAY if collapsed else COLORS[index],
                width=3,
                joint="curve",
            )
    _draw_calibration_marks(draw, plot, axis)
    pad = 8
    legend_h = 36 + 25 * len(traces) + (25 if any_collapse else 0)
    legend_right = min(plot.x1 - pad, plot.x0 + 292)
    draw.rectangle((plot.x0 + pad, plot.y0 + pad, legend_right, plot.y0 + pad + legend_h), fill=(255, 255, 255), outline=(20, 20, 20))
    draw.text((plot.x0 + 16, plot.y0 + 14), f"DIGITIZED • {len(traces)} curves", fill=(0, 0, 0), font=_font(15))
    for index in range(len(traces)):
        y = plot.y0 + 42 + 25 * index
        draw.line((plot.x0 + 18, y + 7, plot.x0 + 51, y + 7), fill=COLORS[index], width=4)
        temperature = TEMPERATURE_BY_CURVE[sample.part][index]
        draw.text(
            (plot.x0 + 60, y),
            f"curve_{index + 1} — Tj={temperature:g}°C",
            fill=(0, 0, 0),
            font=_font(14),
        )
    if any_collapse:
        y = plot.y0 + 42 + 25 * len(traces)
        draw.line((plot.x0 + 18, y + 7, plot.x0 + 51, y + 7), fill=COLLAPSE_GRAY, width=4)
        draw.text(
            (plot.x0 + 60, y),
            "merged strokes — no per-T info",
            fill=(0, 0, 0),
            font=_font(14),
        )
    footer_y = source.height
    draw.rectangle((0, footer_y, canvas.width, canvas.height), fill=(255, 255, 255))
    draw.line((0, footer_y, canvas.width, footer_y), fill=(210, 0, 180), width=3)
    draw.text(
        (14, footer_y + 9),
        f"X AXIS — Gate-source voltage  VGS [V]   {axis.vgs_min_v:g} … {axis.vgs_max_v:g}   linear",
        fill=(100, 0, 90),
        font=_font(16),
    )
    draw.text(
        (14, footer_y + 40),
        f"Y AXIS — Drain current  ID [A]   {axis.id_min_a:g} … {axis.id_max_a:g}   {axis.id_scale}",
        fill=(100, 0, 90),
        font=_font(16),
    )
    canvas.save(target)


def _contact_sheet(output: Path, manifest: list[dict]) -> None:
    """Build a verification sheet whose semantic labels survive overview scaling."""
    card_width, card_height = 960, 940
    columns = 2
    rows = (len(SAMPLES) + columns - 1) // columns
    header_height = 110
    sheet = Image.new("RGB", (columns * card_width, header_height + rows * card_height), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text(
        (24, 16),
        "25 MOSFET SATURATION-TRANSFER DIGITIZATIONS — ID = f(VGS)",
        fill=(20, 20, 20),
        font=_font(32),
    )
    draw.text(
        (24, 60),
        "Each card states curve temperature identity plus both axis quantities, units, ranges, and scales.",
        fill=(90, 0, 80),
        font=_font(22),
    )
    for index, (sample, record) in enumerate(zip(SAMPLES, manifest)):
        column = index % columns
        row = index // columns
        x0 = column * card_width
        y0 = header_height + row * card_height
        draw.rectangle((x0 + 6, y0 + 6, x0 + card_width - 7, y0 + card_height - 7), outline=(180, 180, 180), width=2)
        draw.text(
            (x0 + 22, y0 + 18),
            f"{index + 1:02d}  {sample.manufacturer} / {sample.part}",
            fill=(15, 15, 15),
            font=_font(27),
        )
        draw.text(
            (x0 + 22, y0 + 55),
            "Curve identification:",
            fill=(40, 40, 40),
            font=_font(21),
        )
        chip_x = x0 + 230
        chip_y = y0 + 53
        for curve_index, temperature in enumerate(TEMPERATURE_BY_CURVE[sample.part]):
            text_value = f"curve_{curve_index + 1}=Tj {temperature:g}°C"
            text_width = draw.textbbox((0, 0), text_value, font=_font(20))[2]
            if chip_x + text_width + 42 > x0 + card_width - 18:
                chip_x = x0 + 22
                chip_y += 30
            draw.line((chip_x, chip_y + 12, chip_x + 24, chip_y + 12), fill=COLORS[curve_index], width=5)
            draw.text((chip_x + 31, chip_y), text_value, fill=(30, 30, 30), font=_font(20))
            chip_x += text_width + 60

        overlay = Image.open(output / str(record["overlay"])).convert("RGB")
        overlay.thumbnail((910, 700), Image.Resampling.LANCZOS)
        image_x = x0 + (card_width - overlay.width) // 2
        image_y = y0 + 115
        sheet.paste(overlay, (image_x, image_y))

        axis = record["axis"]
        x_axis = axis["x"]
        y_axis = axis["y"]
        axis_y = y0 + 835
        draw.rectangle((x0 + 15, axis_y - 9, x0 + card_width - 15, y0 + 925), fill=(250, 245, 250))
        draw.text(
            (x0 + 24, axis_y),
            f"X AXIS — Gate-source voltage VGS [{x_axis['unit']}]   {x_axis['min']:g} … {x_axis['max']:g}   {x_axis['scale']}",
            fill=(95, 0, 85),
            font=_font(22),
        )
        draw.text(
            (x0 + 24, axis_y + 38),
            f"Y AXIS — Drain current ID [{y_axis['unit']}]   {y_axis['min']:g} … {y_axis['max']:g}   {y_axis['scale']}",
            fill=(95, 0, 85),
            font=_font(22),
        )
        draw.text(
            (x0 + 24, axis_y + 74),
            f"Classification: MOSFET saturation transfer • {record['status']}",
            fill=(50, 50, 50),
            font=_font(18),
        )
    sheet.save(output / "contact25.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/curvefet-random25/digitized25"),
        help="self-contained output directory",
    )
    args = parser.parse_args()
    source = Path("/tmp/curvefet-random25/review25")
    output = args.output.resolve()
    overlays = output / "overlays"
    points_dir = output / "points"
    sources = output / "sources"
    overlays.mkdir(parents=True, exist_ok=True)
    points_dir.mkdir(parents=True, exist_ok=True)
    sources.mkdir(parents=True, exist_ok=True)
    manifest = []
    for sample in SAMPLES:
        path = source / f"{sample.stem}.png"
        delivered_source = sources / path.name
        shutil.copy2(path, delivered_source)
        rgb = np.asarray(Image.open(path).convert("RGB"))
        plot = PlotBox(*sample.plot)
        axis = TransferAxis(*sample.axis)
        collapse = None
        span_records: list[dict] = []
        try:
            if sample.part == "PXN017-30QL":
                traces = _pxn_vector_traces(plot, axis)
            elif sample.part == "XP10N3R8IT":
                traces = _xp_vector_traces(plot, axis)
            elif sample.part == "STK295N10F8AG":
                traces = _stk_vector_traces(plot, axis)
            elif sample.colors_rgb:
                traces = _colored_traces(rgb, sample, plot, axis)
            else:
                traces = extract_transfer_traces(
                    rgb,
                    plot,
                    axis,
                    sample.curves,
                    _ocr_boxes(path, plot) + list(sample.erases),
                    list(sample.seeds) or None,
                    sample.max_gap_fraction,
                    grouped_seeded=sample.grouped_seeded,
                )
                if sample.secondary_seeds:
                    lower = extract_transfer_traces(
                        rgb,
                        plot,
                        axis,
                        sample.curves,
                        (
                            _ocr_boxes(path, plot) + list(sample.erases)
                            if sample.secondary_use_ocr_masks
                            else list(sample.erases)
                        ),
                        list(sample.secondary_seeds),
                        (
                            min(sample.max_gap_fraction, 0.05)
                            if sample.secondary_max_gap_fraction is None
                            else sample.secondary_max_gap_fraction
                        ),
                        0.02,
                        grouped_seeded=(
                            sample.grouped_seeded
                            if sample.secondary_grouped_seeded is None
                            else sample.secondary_grouped_seeded
                        ),
                        max_monotone_violation_fraction=1.0,
                    )
                    traces = _merge_seeded_segments(
                        traces, lower, plot, axis, int(sample.split_y)
                    )
                for curve_index, anchors in sample.anchor_repairs:
                    traces[curve_index] = _repair_trace_span_from_anchors(
                        traces[curve_index], anchors, plot, axis
                    )
                if sample.identity_crossing_y is not None:
                    traces = exchange_two_trace_identities_below(
                        traces, int(sample.identity_crossing_y), plot, axis
                    )
                for curve_index, seed_x, seed_y, split_y in sample.upper_extensions:
                    extension = extract_transfer_traces(
                        rgb,
                        plot,
                        axis,
                        1,
                        _ocr_boxes(path, plot) + list(sample.erases),
                        [(seed_x, seed_y)],
                        sample.max_gap_fraction,
                    )[0]
                    traces[curve_index] = _replace_upper_segment(
                        traces[curve_index], extension, split_y, plot, axis
                    )
            collapse = maximum_pairwise_collapse_fraction(traces)
            vector_identity = sample.part in VECTOR_PARTS
            if collapse > 0.1 and not vector_identity:
                raise RuntimeError(
                    f"digitized branches collapse over {collapse:.1%} of common rows"
                )
            collapse_spans, collapsed_rows = sustained_collapse_spans(traces)
            minimum_span = min(trace.y_span_fraction for trace in traces)
            if minimum_span < sample.minimum_span_fraction:
                raise RuntimeError(
                    f"digitized branch covers only {minimum_span:.1%} of the current axis"
                )
            maximum_source_gap = max(
                trace.maximum_source_gap_fraction for trace in traces
            )
            if maximum_source_gap > sample.allowed_source_gap_fraction:
                raise RuntimeError(
                    "digitized branch has a source-evidence gap of "
                    f"{maximum_source_gap:.1%} of the current axis"
                )
            csv_path = points_dir / f"{sample.stem}.csv"
            with csv_path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["curve_id", "temperature_c", "Vgs_V", "Id_A", "collapsed"])
                assigned = TEMPERATURE_BY_CURVE[sample.part]
                for index, trace in enumerate(traces, 1):
                    temperature = assigned[index - 1]
                    # calibrate_pixels orders points by descending image row;
                    # walk the pixels the same way so flags stay aligned.
                    ordered = sorted(trace.pixels, key=lambda point: point[1], reverse=True)
                    for (_x, y), (vgs, current) in zip(ordered, trace.points, strict=True):
                        writer.writerow(
                            [
                                f"curve_{index}",
                                temperature,
                                f"{vgs:.8g}",
                                f"{current:.8g}",
                                int(y in collapsed_rows[index - 1]),
                            ]
                        )
            overlay_path = overlays / f"{sample.stem}.digitized.png"
            _overlay(Image.open(path), sample, traces, overlay_path, collapsed_rows)
            status = "digitized-review-required"

            def _row_values(trace: ReviewTrace, row: int) -> tuple[float, float]:
                by_row = {y: x for x, y in trace.pixels}
                vgs, current = calibrate_pixels([(by_row[row], row)], plot, axis)[0]
                return vgs, current

            span_records = []
            for span in collapse_spans:
                trace = traces[span.first_index]
                vgs_hi, id_lo = _row_values(trace, span.y_hi)
                vgs_lo, id_hi = _row_values(trace, span.y_lo)
                span_records.append(
                    {
                        "curves": [
                            f"curve_{span.first_index + 1}",
                            f"curve_{span.second_index + 1}",
                        ],
                        "y_px": [span.y_lo, span.y_hi],
                        "vgs_v": [round(vgs_hi, 4), round(vgs_lo, 4)],
                        "id_a": [round(id_lo, 4), round(id_hi, 4)],
                        "kind": "sustained-merge",
                        "reorders_after": span.reorders_after,
                    }
                )
            diagnostics = [
                {
                    "curve_id": f"curve_{i + 1}",
                    "points": len(trace.points),
                    "y_span_fraction": round(trace.y_span_fraction, 4),
                    "monotone_violation_fraction": round(trace.monotone_violation_fraction, 4),
                    "maximum_source_gap_fraction": round(
                        trace.maximum_source_gap_fraction, 4
                    ),
                    "collapsed_point_fraction": round(
                        sum(
                            1
                            for _x, y in trace.pixels
                            if y in collapsed_rows[i]
                        )
                        / max(1, len(trace.pixels)),
                        4,
                    ),
                }
                for i, trace in enumerate(traces)
            ]
        except Exception as exc:
            csv_path = None
            overlay_path = None
            status = "refused"
            diagnostics = {"error": str(exc)}
        manifest.append(
            {
                "manufacturer": sample.manufacturer,
                "part": sample.part,
                "source": str(delivered_source.relative_to(output)),
                "overlay": str(overlay_path.relative_to(output)) if overlay_path else None,
                "points_csv": str(csv_path.relative_to(output)) if csv_path else None,
                "calibration_provenance": "manual-review-anchor",
                "plot_box_px": list(sample.plot),
                "source_anchor_repairs": len(sample.anchor_repairs),
                "axis": {
                    "x": {"quantity": "Vgs", "unit": "V", "min": axis.vgs_min_v, "max": axis.vgs_max_v, "scale": "linear"},
                    "y": {"quantity": "Id", "unit": "A", "min": axis.id_min_a, "max": axis.id_max_a, "scale": axis.id_scale},
                },
                "expected_curves": sample.curves,
                "allowed_source_gap_fraction": sample.allowed_source_gap_fraction,
                "maximum_pairwise_collapse_fraction": (
                    round(collapse, 4) if collapse is not None else None
                ),
                "collapse_spans": span_records,
                "identity_provenance": (
                    "independent-pdf-vector-paths"
                    if sample.part in VECTOR_PARTS
                    else "raster-branch-tracking"
                ),
                "identity_source": (
                    VECTOR_SOURCES[sample.part]
                    if sample.part in VECTOR_PARTS
                    else {
                        "kind": "delivered-raster",
                        "image": str(delivered_source.relative_to(output)),
                    }
                ),
                "temperature_labels_c": TEMPERATURE_LABELS[sample.part],
                "curve_identification": {
                    f"curve_{index + 1}": {"temperature_c": temperature}
                    for index, temperature in enumerate(TEMPERATURE_BY_CURVE[sample.part])
                },
                "temperature_assignment": (
                    "color-legend-mapped"
                    if sample.colors_rgb
                    else "pdf-vector-and-label-mapped"
                    if sample.part in VECTOR_PARTS
                    else "manual-branch-label-mapped"
                ),
                "status": status,
                "diagnostics": diagnostics,
            }
        )
        print(f"{sample.part}: {status}")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if all(record["overlay"] for record in manifest):
        _contact_sheet(output, manifest)


if __name__ == "__main__":
    main()
