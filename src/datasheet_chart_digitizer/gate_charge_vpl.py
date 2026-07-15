#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "datasheet_chart_digitizer"

from .gate_charge import digitize_gate_charge
from .gate_charge_samples import (
    DEFAULT_DATASHEET_ROOT,
    DEFAULT_DPI,
    DEFAULT_OUT,
    SAMPLES,
    _font,
    _sample_pdf_path,
    _samples_from_chart_extraction,
    _save_sheet,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Digitize MOSFET gate-charge curves and estimate Vpl.")
    parser.add_argument(
        "pdfs",
        nargs="*",
        help="Optional datasheet PDFs to process. Relative paths are resolved under --datasheet-root/datasheets.",
    )
    parser.add_argument(
        "--datasheet-root",
        type=Path,
        default=DEFAULT_DATASHEET_ROOT,
        help="Root containing datasheets/ for relative PDF arguments and local regression samples.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Output directory for overlays/contact sheets.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help="PDF render DPI for raster fallback and overlays.",
    )
    parser.add_argument("--start", type=int, default=None, help="1-based start index in docs/vibes/chart-extraction.md")
    parser.add_argument("--count", type=int, default=None, help="number of chart-extraction.md samples")
    parser.add_argument(
        "--chart-extraction-md",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--reference-assisted",
        action="store_true",
        help="Use human reference Vpl values to choose among ambiguous chart/estimator candidates. "
        "This is only for legacy overlay audits, not normal digitization.",
    )
    args = parser.parse_args()
    root = args.datasheet_root
    out = args.out
    dpi = args.dpi
    chart_extraction_md = args.chart_extraction_md or root / "docs/vibes/chart-extraction.md"

    samples = SAMPLES
    batch_name = None
    if args.pdfs:
        samples = [(pdf, None, "") for pdf in args.pdfs]
        batch_name = f"pdfs_{len(samples)}"
    elif args.start is not None or args.count is not None:
        start = args.start or 1
        count = args.count or 25
        samples = _samples_from_chart_extraction(chart_extraction_md, start, count)
        batch_name = f"chart_extraction_{start}_{start + len(samples) - 1}"

    out.mkdir(parents=True, exist_ok=True)
    images = []
    rows = []
    had_errors = False

    for rel, ref_vpl, comment in samples:
        pdf = _sample_pdf_path(root, rel)
        mpn = pdf.stem
        if ref_vpl is None and not args.pdfs:
            img = Image.new("RGB", (900, 180), "white")
            text = f"{mpn}: no human Vpl reference"
            if comment:
                text += f" ({comment})"
            ImageDraw.Draw(img).text((12, 20), text, fill=(0, 0, 0), font=_font(18))
            images.append(img)
            rows.append((mpn, out / f"{mpn}.fullcurve.overlay.png", 0, text))
            continue
        if not pdf.exists():
            img = Image.new("RGB", (900, 180), "white")
            ImageDraw.Draw(img).text((12, 20), f"{mpn}: missing", fill=(0, 0, 0), font=_font(18))
            images.append(img)
            rows.append((mpn, out / f"{mpn}.fullcurve.overlay.png", 0, "missing"))
            if args.pdfs:
                had_errors = True
            continue

        results = digitize_gate_charge(pdf, dpi=dpi)
        usable = [result for result in results if result.vpl is not None]
        if usable and ref_vpl is not None and args.reference_assisted:
            result = min(usable, key=lambda candidate: abs(float(candidate.vpl) - ref_vpl))
        elif usable:
            result = usable[0]
        elif results:
            result = min(results, key=_review_overlay_key)
            if args.pdfs:
                had_errors = True
        else:
            img = Image.new("RGB", (900, 180), "white")
            ImageDraw.Draw(img).text((12, 20), f"{mpn}: no Vpl chart hit", fill=(0, 0, 0), font=_font(18))
            images.append(img)
            rows.append((mpn, out / f"{mpn}.fullcurve.overlay.png", 0, "no Vpl chart hit"))
            if args.pdfs:
                had_errors = True
            continue

        doc = pymupdf.open(str(pdf))
        try:
            page = doc[result.panel.page - 1]
            rect = pymupdf.Rect(result.crop_box_pt)
            scale = dpi / 72.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False)
            crop = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        finally:
            doc.close()

        plot_box = result.plot_box_px
        loose_plot_box = plot_box
        curve = list(result.curve_px)
        visual_plot_box = plot_box

        draw = ImageDraw.Draw(crop)
        draw.rectangle(list(loose_plot_box), outline=(255, 220, 128), width=2)
        draw.rectangle(list(visual_plot_box), outline=(255, 176, 0), width=3)
        if len(curve) >= 2:
            draw.line(curve, fill=(20, 90, 255), width=5, joint="curve")
            for x, y in curve[:: max(1, len(curve) // 35)]:
                draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(0, 40, 220))

        est = result.vpl
        y_vpl = result.vpl_y_px
        est_s = f"{est:.2f}" if isinstance(est, (int, float)) else "none"
        err_s = f"{est - ref_vpl:+.2f}" if isinstance(est, (int, float)) and ref_vpl is not None else ""
        ok = isinstance(est, (int, float)) and ref_vpl is not None and abs(est - ref_vpl) <= 0.5
        guide_color = (20, 170, 40) if ok else (255, 40, 40)
        if y_vpl is not None:
            draw.line([(visual_plot_box[0], y_vpl), (visual_plot_box[2], y_vpl)], fill=guide_color, width=2)
        ref_s = f"{ref_vpl:.2f}" if ref_vpl is not None else "n/a"
        label = (
            f"{mpn}  ref={ref_s}  Vpl={est_s} {err_s}  status={result.status} "
            f"trace={result.trace_source} score={result.score:.2f} curve_pts={len(curve)}"
        )
        if result.y_tick_count:
            label += f"  ytick={result.y_tick_count}"
        if result.diagnostics:
            label += "  diag=" + ",".join(result.diagnostics)
        if comment:
            label += f"  ({comment})"
        loose_h = max(1, loose_plot_box[3] - loose_plot_box[1])
        plot_h = max(1, visual_plot_box[3] - visual_plot_box[1])
        loose_w = max(1, loose_plot_box[2] - loose_plot_box[0])
        plot_w = max(1, visual_plot_box[2] - visual_plot_box[0])
        if loose_h < 1.45 * plot_h and loose_w < 1.65 * plot_w:
            px0 = min(visual_plot_box[0], loose_plot_box[0])
            py0 = min(visual_plot_box[1], loose_plot_box[1])
            px1 = max(visual_plot_box[2], loose_plot_box[2])
            py1 = max(visual_plot_box[3], loose_plot_box[3])
        else:
            px0, py0, px1, py1 = visual_plot_box
        display_box = (
            max(0, px0 - 90),
            max(0, py0 - 44),
            min(crop.width, px1 + 58),
            min(crop.height, py1 + 110),
        )
        crop = crop.crop(display_box)
        pad_h = 58
        annotated = Image.new("RGB", (crop.width, crop.height + pad_h), "white")
        annotated.paste(crop, (0, pad_h))
        ImageDraw.Draw(annotated).text((8, 8), label, fill=(0, 0, 0), font=_font(15))

        out_path = out / f"{mpn}.fullcurve.overlay.png"
        annotated.save(out_path)
        images.append(annotated)
        rows.append((mpn, out_path, len(curve), label))

    if batch_name:
        batch_path = out / f"{batch_name}_fullcurve_contact_sheet.png"
        _save_sheet(images, batch_path)
    else:
        first5_path = out / "first5_fullcurve_contact_sheet.png"
        next10_path = out / "next10_fullcurve_contact_sheet.png"
        all15_path = out / "all15_fullcurve_contact_sheet.png"
        _save_sheet(images[:5], first5_path)
        _save_sheet(images[5:15], next10_path)
        _save_sheet(images, all15_path)

    for _mpn, path, _npts, label in rows:
        print(f"{label}: {path}")
    if batch_name:
        print(f"BATCH_CONTACT_SHEET: {batch_path}")
    else:
        print(f"FIRST5_CONTACT_SHEET: {first5_path}")
        print(f"NEXT10_CONTACT_SHEET: {next10_path}")
        print(f"ALL15_CONTACT_SHEET: {all15_path}")
    return 1 if had_errors else 0


def _review_overlay_key(result) -> tuple[int, int]:
    """Prefer a useful unresolved chart over rejected tables in overlays."""

    diagnostics = " ".join(result.diagnostics)
    if "ambiguous_neighbor" in diagnostics:
        return 0, result.panel.page
    reasons = ("breakdown_voltage", "output_characteristics", "on_resistance", "diode", "spec_table")
    priority = next((index for index, reason in enumerate(reasons) if reason in diagnostics), len(reasons))
    return priority + 1, result.panel.page


if __name__ == "__main__":
    raise SystemExit(main())
