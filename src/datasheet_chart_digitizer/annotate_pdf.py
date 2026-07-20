"""Detect supported charts, digitize them, and overlay curves inside a PDF copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw

from . import (
    breakdown_voltage,
    diode_forward_voltage,
    mosfet_capacitance,
    rdson_current,
    rdson_temperature,
    transfer_characteristics,
)
from .find_charts import ChartPanel, process_pdf, write_outputs
from .gate_charge import digitize_gate_charge_fail_closed
from .overlay import PLOT_FRAME_THICKNESS_PX, draw_axis_ticks


EMBEDDABLE_STATUSES = frozenset({"ok", "pass", "verified"})
REVIEW_ONLY_STATUSES = frozenset({"overlay-review-required"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extractor_provenance() -> dict[str, object]:
    """Bind review artifacts to the exact package source that produced them."""

    package_root = Path(__file__).resolve().parent
    files = sorted(package_root.rglob("*.py"))
    source_files = [
        {
            "path": path.relative_to(package_root).as_posix(),
            "sha256": _sha256(path),
        }
        for path in files
    ]
    canonical = hashlib.sha256()
    for row in source_files:
        canonical.update(str(row["path"]).encode("utf-8"))
        canonical.update(b"\0")
        canonical.update(str(row["sha256"]).encode("ascii"))
        canonical.update(b"\n")

    commit: str | None = None
    dirty: bool | None = None
    repo_root = package_root.parents[1]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                "src/datasheet_chart_digitizer",
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        dirty = bool(status.strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    return {
        "extractor_git_commit": commit,
        "extractor_git_dirty": dirty,
        "extractor_source_sha256": canonical.hexdigest(),
        "extractor_source_files": source_files,
    }


def _gate_overlay(pdf: Path, result, out_dir: Path) -> Path:
    """Render one gate-charge result in its exact source crop coordinate space."""
    with pymupdf.open(pdf) as document:
        page = document[result.panel.page - 1]
        scale = result.dpi / 72.0
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale),
            clip=pymupdf.Rect(result.crop_box_pt),
            alpha=False,
        )
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        result.plot_box_px,
        outline=(255, 176, 0),
        width=PLOT_FRAME_THICKNESS_PX,
    )
    if len(result.curve_px) >= 2:
        draw.line(result.curve_px, fill=(20, 90, 255), width=5, joint="curve")
    for x, y in result.curve_px[:: max(1, len(result.curve_px) // 35)]:
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(0, 40, 220))
    image = _draw_gate_tick_evidence(image, result)
    draw = ImageDraw.Draw(image)
    _draw_vpl_annotation(draw, result)
    out_path = out_dir / "gate_charge_overlays" / f"p{result.panel.page:02d}_d{result.panel.diagram}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path


def _draw_gate_tick_evidence(image: Image.Image, result) -> Image.Image:
    """Render the gate extractor's consumed tick/value pairs for scale review."""

    overlay = np.asarray(image.convert("RGB")).copy()
    plot = pymupdf.Rect(result.plot_box_px)
    draw_axis_ticks(
        overlay,
        plot,
        x_ticks=[(pixel, value) for value, pixel in result.x_ticks_px],
        y_ticks=[(pixel, value) for value, pixel in result.y_ticks_px],
        color=(196, 0, 196),
        marker_size=8,
        font_scale=0.35,
        unit_x=result.x_tick_unit or "",
        unit_y=result.y_tick_unit or "",
    )
    return Image.fromarray(overlay)


def _draw_vpl_annotation(draw: ImageDraw.ImageDraw, result) -> None:
    """Draw a review-visible Vpl guide only for a served gate result."""

    if result.status != "ok" or result.vpl is None or result.vpl_y_px is None:
        return
    vpl = float(result.vpl)
    y = float(result.vpl_y_px)
    if not math.isfinite(vpl) or not math.isfinite(y):
        return
    x0, y0, x1, y1 = result.plot_box_px
    if not y0 <= y <= y1:
        return
    guide = (196, 0, 196)
    y_px = int(round(y))
    for start in range(int(x0), int(x1) + 1, 22):
        draw.line(
            (start, y_px, min(start + 13, int(x1)), y_px),
            fill=guide,
            width=3,
        )
    label = f"Vpl = {vpl:.3g} V"
    text_box = draw.textbbox((0, 0), label)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    label_x = max(int(x0) + 4, int(x1) - text_width - 10)
    label_y = max(int(y0) + 4, min(y_px - text_height - 8, int(y1) - text_height - 6))
    draw.rectangle(
        (
            label_x - 3,
            label_y - 3,
            label_x + text_width + 3,
            label_y + text_height + 3,
        ),
        fill=(255, 255, 255),
        outline=guide,
        width=2,
    )
    draw.text((label_x, label_y), label, fill=guide)


def _overlay_record(
    panel: ChartPanel,
    kind: str,
    status: str,
    overlay: Path,
    diagnostics: object,
    crop_box_pt: tuple[float, float, float, float] | None = None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "page": panel.page,
        "diagram": panel.diagram,
        "title": panel.title,
        "status": status,
        "diagnostics": diagnostics,
        "crop_box_pt": list(crop_box_pt or panel.crop_box_pt),
        "overlay": str(overlay),
        "overlay_sha256": _sha256(overlay),
    }


def _pdf_compatible_overlay(
    path: Path,
    work_dir: Path,
    crop_box_pt: list[float],
    dpi: int,
) -> tuple[Path, tuple[int, int], tuple[int, int]]:
    """Remove review-only footer bands and preserve the source crop coordinate space."""
    target_width = round((crop_box_pt[2] - crop_box_pt[0]) * dpi / 72.0)
    target_height = round((crop_box_pt[3] - crop_box_pt[1]) * dpi / 72.0)
    with Image.open(path) as source:
        image = source.convert("RGB")
        original_size = image.size
        width_delta = abs(image.width - target_width)
        footer_height = image.height - target_height
        if width_delta <= 2 and 3 <= footer_height <= 64:
            image = image.crop((0, 0, image.width, target_height))
        elif width_delta > 2 or abs(image.height - target_height) > 2:
            raise ValueError(
                f"overlay/crop coordinate mismatch: overlay={original_size} "
                f"crop≈{(target_width, target_height)}"
            )
    converted = work_dir / "pdf_overlays" / f"{path.stem}.png"
    converted.parent.mkdir(parents=True, exist_ok=True)
    image.save(converted)
    return converted, original_size, image.size


def _process_indexed_panel(
    panel: ChartPanel, out_dir: Path, pdf: Path
) -> dict[str, object]:
    chart = asdict(panel)
    crop_rel = Path(panel.crop_png)
    crop_path = out_dir / crop_rel
    rel_stem = crop_rel.with_suffix("")
    if panel.kind == "capacitances":
        result = mosfet_capacitance.process_chart(
            chart, crop_path, out_dir, rel_stem, pdf.parent
        )
        overlay = out_dir / str(result["overlay"])
        diagnostics = result.get("status_reasons", result.get("diagnostics", []))
    elif panel.kind == "transfer":
        result = transfer_characteristics.process_chart(
            chart, crop_path, out_dir, rel_stem, None
        )
        overlay = Path(str(result["overlay"]))
        diagnostics = result.get("warnings", [])
    elif panel.kind == "breakdown_voltage":
        result = breakdown_voltage.process_chart(chart, crop_path, out_dir, rel_stem)
        overlay = Path(str(result["overlay"]))
        diagnostics = result.get("warnings", [])
    else:
        raise ValueError(f"not an indexed-panel digitizer: {panel.kind}")
    return _overlay_record(
        panel, panel.kind, str(result.get("status", "ok")), overlay, diagnostics
    )


def annotate_pdf(
    pdf: Path,
    output_pdf: Path,
    *,
    work_dir: Path,
    dpi: int = 220,
    include_review_required: bool = False,
) -> dict[str, object]:
    """Run every supported digitizer and write an overlay-annotated PDF copy."""
    pdf = pdf.expanduser().resolve()
    output_pdf = output_pdf.expanduser().resolve()
    work_dir = work_dir.expanduser().resolve()
    if not pdf.exists():
        raise FileNotFoundError(pdf)
    work_dir.mkdir(parents=True, exist_ok=True)

    panels = process_pdf(pdf, work_dir, dpi)
    write_outputs(work_dir, panels)
    records: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for panel in panels:
        if panel.kind not in {"capacitances", "transfer", "breakdown_voltage"}:
            continue
        try:
            records.append(_process_indexed_panel(panel, work_dir, pdf))
        except Exception as error:
            errors.append({"kind": panel.kind, "page": panel.page, "diagram": panel.diagram, "error": str(error)})

    diode_results, diode_errors = diode_forward_voltage.digitize_panels_fail_closed(
        panels, work_dir
    )
    errors.extend(diode_errors)
    for result in diode_results:
        panel = ChartPanel(**result["panel"])
        records.append(_overlay_record(
            panel, "body_diode", str(result["status"]),
            work_dir / str(result["overlay"]), result.get("diagnostics", []),
        ))
    for family, runner in (
        ("rds_on_current", rdson_current.digitize_pdf_fail_closed),
        ("rds_on_temperature", rdson_temperature.digitize_pdf_fail_closed),
    ):
        try:
            family_results, family_errors = runner(pdf, work_dir, dpi)
        except Exception as error:
            errors.append({
                "kind": family,
                "page": None,
                "diagram": None,
                "error": str(error),
            })
            continue
        errors.extend(family_errors)
        for result in family_results:
            panel = ChartPanel(**result["panel"])
            records.append(_overlay_record(
                panel, family, str(result["status"]),
                work_dir / str(result["overlay"]), result.get("diagnostics", []),
            ))

    # Reuse the annotation pass's finder resolution. A second, lower-resolution
    # discovery can lose light/small grids that the authoritative panel index
    # above already evidenced, leaving a detected gate-charge chart unpainted.
    try:
        gate_results, gate_errors = digitize_gate_charge_fail_closed(
            pdf, dpi=dpi, finder_dpi=dpi
        )
    except Exception as error:
        gate_results = []
        gate_errors = [{
            "kind": "gate_charge",
            "page": None,
            "diagram": None,
            "error": str(error),
        }]
    errors.extend(gate_errors)
    for gate in gate_results:
        try:
            gate_overlay = _gate_overlay(pdf, gate, work_dir)
        except Exception as error:
            errors.append({
                "kind": "gate_charge",
                "page": gate.panel.page,
                "diagram": gate.panel.diagram,
                "error": str(error),
            })
            continue
        record = _overlay_record(
            gate.panel, "gate_charge", gate.status, gate_overlay, list(gate.diagnostics),
            crop_box_pt=gate.crop_box_pt,
        )
        served = gate.status == "ok"
        record["vpl_v"] = gate.vpl if served else None
        record["vpl_y_px"] = gate.vpl_y_px if served else None
        record["x_ticks_px"] = gate.x_ticks_px
        record["y_ticks_px"] = gate.y_ticks_px
        record["x_tick_unit"] = gate.x_tick_unit
        record["y_tick_unit"] = gate.y_tick_unit
        records.append(record)

    unique: dict[tuple[int, int, str], dict[str, object]] = {}
    for record in records:
        unique[(int(record["page"]), int(record["diagram"]), str(record["kind"]))] = record
    records = sorted(unique.values(), key=lambda row: (int(row["page"]), int(row["diagram"]), str(row["kind"])))

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.unlink(missing_ok=True)
    with pymupdf.open(pdf) as document:
        for record in records:
            status = str(record["status"])
            eligible = status in EMBEDDABLE_STATUSES or (
                include_review_required and status in REVIEW_ONLY_STATUSES
            )
            record["embedded"] = False
            record["placed_rect_pt"] = None
            record["embedding_reason"] = (
                "accepted_status"
                if status in EMBEDDABLE_STATUSES
                else "explicit_review_overlay"
                if eligible
                else f"status_not_embeddable:{status}"
            )
            if not eligible:
                continue
            overlay, original_size, embedded_size = _pdf_compatible_overlay(
                Path(str(record["overlay"])),
                work_dir,
                list(record["crop_box_pt"]),
                dpi,
            )
            placed_rect = pymupdf.Rect(record["crop_box_pt"])
            document[int(record["page"]) - 1].insert_image(
                placed_rect,
                filename=str(overlay),
                overlay=True,
                keep_proportion=False,
            )
            record["embedded"] = True
            record["placed_rect_pt"] = list(placed_rect)
            record["overlay_size_px"] = list(original_size)
            record["embedded_overlay_size_px"] = list(embedded_size)
            record["embedded_overlay_sha256"] = _sha256(overlay)
        document.save(output_pdf, garbage=4, deflate=True, no_new_id=True)

    manifest = {
        "source_pdf": str(pdf),
        "source_pdf_sha256": _sha256(pdf),
        "annotated_pdf": str(output_pdf),
        "annotated_pdf_sha256": _sha256(output_pdf),
        "dpi": dpi,
        "detected_panels": len(panels),
        "overlays": records,
        "errors": errors,
        "human_verified": False,
        **_extractor_provenance(),
    }
    (work_dir / "annotated_pdf_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="input datasheet PDF")
    parser.add_argument("--out", type=Path, default=None, help="annotated PDF path")
    parser.add_argument("--work-dir", type=Path, default=None, help="crops, overlays, and manifest directory")
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument(
        "--include-review-required",
        action="store_true",
        help="also paint explicitly review-required overlays; fail-closed statuses remain excluded",
    )
    args = parser.parse_args()
    out = args.out or args.pdf.with_name(f"{args.pdf.stem}-with-digitized-curves.pdf")
    work = args.work_dir or out.with_suffix("").with_name(f"{out.stem}-artifacts")
    manifest = annotate_pdf(
        args.pdf,
        out,
        work_dir=work,
        dpi=args.dpi,
        include_review_required=args.include_review_required,
    )
    print(manifest["annotated_pdf"])
    print(work / "annotated_pdf_manifest.json")
    if manifest["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
