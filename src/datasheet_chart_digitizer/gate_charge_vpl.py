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

from .gate_charge_estimation import (
    _estimate_vpl_from_curve,
    _line_sse,
    _local_y_ticks_for_plot,
    _middle_slope_y,
    _parse_numeric_label,
    _reject_ambiguous_broad_context,
    _reject_non_gate_context,
    _text_near_rect,
    _v_from_local_ticks,
    _v_from_plot_axis,
    _v_from_y_pixel,
    _y_pixel_from_local_ticks,
)
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
from .gate_charge_trace import (
    _candidate_masks,
    _cluster_runs,
    _curve_score,
    _detect_inner_plot_box,
    _mask_page_text,
    _pdf_to_px,
    _smooth_polyline,
    _trace_component,
    _trace_gate_curve,
    _trace_vector_gate_curve,
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
        help="pwr-mosfet-lib checkout containing datasheets/ and dslib. Defaults to Fab's local checkout.",
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

    sys.path.insert(0, str(root))
    try:
        from dslib.viz import find_in_pdf
    except ModuleNotFoundError as exc:
        if exc.name != "dslib":
            raise
        raise SystemExit(
            "digitize-vpl currently requires pwr-mosfet-lib's dslib chart finder. "
            f"Pass --datasheet-root pointing at a checkout that contains dslib/ (got {root})."
        ) from exc

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

        hits = find_in_pdf(str(pdf), enable_raster=True, enable_ocr=False)
        usable = [(c, h, s) for c, h, s in hits if h is not None]
        if usable:
            filtered = []
            doc_filter = pymupdf.open(str(pdf))
            try:
                for c, h, s in usable:
                    page_filter = doc_filter[c.page_num]
                    tight_ctx = _text_near_rect(page_filter, c.bbox, pad=4.0)
                    broad_ctx = _text_near_rect(page_filter, c.bbox, pad=90.0)
                    if not _reject_non_gate_context(tight_ctx) and not _reject_ambiguous_broad_context(tight_ctx, broad_ctx):
                        filtered.append((c, h, s))
                usable = filtered
            finally:
                doc_filter.close()
        if usable and ref_vpl is not None and args.reference_assisted:
            chart, hit, source = min(
                usable,
                key=lambda t: (
                    abs(float(getattr(t[1], "v_pl", float("inf"))) - ref_vpl),
                    -float(getattr(t[1], "score", 0.0)),
                ),
            )
        elif usable:
            chart, hit, source = max(usable, key=lambda t: getattr(t[1], "score", 0.0))
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
            page = doc[chart.page_num]
            rect = (chart.bbox + (-46, -38, 42, 52)) & page.rect
            scale = dpi / 72.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False)
            crop = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            trace_crop = _mask_page_text(crop, page, rect, chart.bbox, scale)

            bx0, by0 = _pdf_to_px(rect, scale, chart.bbox.x0, chart.bbox.y0)
            bx1, by1 = _pdf_to_px(rect, scale, chart.bbox.x1, chart.bbox.y1)
            loose_plot_box = (int(round(bx0)), int(round(by0)), int(round(bx1)), int(round(by1)))
            plot_box = _detect_inner_plot_box(crop, loose_plot_box)
            local_y_ticks = _local_y_ticks_for_plot(page, rect, scale, plot_box)
            if (
                (source or "").startswith("vector")
                and plot_box[0] - loose_plot_box[0] > 10
                and plot_box[0] - loose_plot_box[0] < 0.22 * max(1, loose_plot_box[2] - loose_plot_box[0])
            ):
                plot_box = (loose_plot_box[0], plot_box[1], plot_box[2], plot_box[3])
            curve = _smooth_polyline(_trace_gate_curve(trace_crop, plot_box))
            if len(curve) < 10 and (source or "").startswith("vector"):
                curve = _smooth_polyline(_trace_vector_gate_curve(page, rect, scale, plot_box), stride=1)
        finally:
            doc.close()

        loose_w0 = max(1, loose_plot_box[2] - loose_plot_box[0])
        loose_h0 = max(1, loose_plot_box[3] - loose_plot_box[1])
        plot_w0 = max(1, plot_box[2] - plot_box[0])
        plot_h0 = max(1, plot_box[3] - plot_box[1])
        visual_plot_box = plot_box
        if (
            (source or "").startswith("vector")
            and plot_h0 < 0.75 * loose_h0
            and plot_box[1] - loose_plot_box[1] > 0.08 * loose_h0
            and loose_plot_box[3] - plot_box[3] > 0.08 * loose_h0
        ):
            visual_plot_box = loose_plot_box

        draw = ImageDraw.Draw(crop)
        draw.rectangle(list(loose_plot_box), outline=(255, 220, 128), width=2)
        draw.rectangle(list(visual_plot_box), outline=(255, 176, 0), width=3)
        if len(curve) >= 2:
            draw.line(curve, fill=(20, 90, 255), width=5, joint="curve")
            for x, y in curve[:: max(1, len(curve) // 35)]:
                draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(0, 40, 220))

        old_est = getattr(hit, "v_pl", None) if hit is not None else None
        curve_est, y_curve_vpl = _estimate_vpl_from_curve(curve, chart, rect, scale, plot_box, local_y_ticks)
        loose_h = max(1, loose_plot_box[3] - loose_plot_box[1])
        stacked_inner_plot = (plot_box[1] - loose_plot_box[1]) > 0.25 * loose_h
        est_source = "dslib"
        if ref_vpl is not None and args.reference_assisted:
            candidates: list[tuple[float, float | None, str]] = []
            if isinstance(old_est, (int, float)):
                y_pdf = getattr(hit, "y_pdf", None) if hit is not None else None
                y_old = _pdf_to_px(rect, scale, chart.bbox.x0, float(y_pdf))[1] if y_pdf is not None else None
                candidates.append((float(old_est), y_old, "dslib"))
            if curve_est is not None and y_curve_vpl is not None:
                candidates.append((float(curve_est), y_curve_vpl, "curve"))
                candidates.append((_v_from_plot_axis(plot_box, y_curve_vpl, -5.0, 20.0), y_curve_vpl, "curve/axis_-5_20"))
                candidates.append((_v_from_plot_axis(plot_box, y_curve_vpl, 0.0, 15.0), y_curve_vpl, "curve/axis_0_15"))
            if candidates:
                est, y_vpl, est_source = min(candidates, key=lambda item: abs(item[0] - ref_vpl))
            else:
                est, y_vpl, est_source = None, None, "none"
        elif curve_est is not None and y_curve_vpl is not None:
            est = curve_est
            y_vpl = y_curve_vpl
            est_source = "curve"
        elif stacked_inner_plot and curve_est is not None:
            est = curve_est
            y_vpl = y_curve_vpl
            est_source = "curve/local-axis"
        elif isinstance(old_est, (int, float)):
            est = old_est
            y_pdf = getattr(hit, "y_pdf", None) if hit is not None else None
            y_vpl = _pdf_to_px(rect, scale, chart.bbox.x0, float(y_pdf))[1] if y_pdf is not None else None
        else:
            est = curve_est
            y_vpl = y_curve_vpl
            est_source = "curve"
        est_s = f"{est:.2f}" if isinstance(est, (int, float)) else "none"
        err_s = f"{est - ref_vpl:+.2f}" if isinstance(est, (int, float)) and ref_vpl is not None else ""
        ok = isinstance(est, (int, float)) and ref_vpl is not None and abs(est - ref_vpl) <= 0.5
        guide_color = (20, 170, 40) if ok else (255, 40, 40)
        if y_vpl is not None:
            draw.line([(visual_plot_box[0], y_vpl), (visual_plot_box[2], y_vpl)], fill=guide_color, width=2)
        old_s = f" old={old_est:.2f}" if isinstance(old_est, (int, float)) and est is not None and abs(old_est - est) > 0.05 else ""
        ref_s = f"{ref_vpl:.2f}" if ref_vpl is not None else "n/a"
        label = f"{mpn}  ref={ref_s}  Vpl={est_s} {err_s}{old_s}  chart={source or '-'}  vpl_src={est_source}  curve_pts={len(curve)}"
        if local_y_ticks:
            label += f"  ytick=local/{len(local_y_ticks)}"
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


if __name__ == "__main__":
    raise SystemExit(main())
