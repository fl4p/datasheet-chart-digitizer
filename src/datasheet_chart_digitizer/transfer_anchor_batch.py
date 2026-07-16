"""Guarded cross-vendor saturation-temperature fits from reviewed transfer CSVs.

The 25-device transfer batch is curve evidence, not permission to invent a
channel anchor.  This module joins eight curves to their local-datasheet gate-
charge conditions and keeps three separate questions visible:

* Is ``Id_gc`` the gate-charge test current (never the continuous rating)?
* Is the pre-threshold/post-threshold Qgs split explicit, or merely estimated?
* Does the compact temperature law survive the cold-curve and ZTC holdouts?

Diagnostic fits are useful even when a guard refuses promotion.  No result is
marked verified here; a clean result remains ``fit-review-required``.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .transfer_characteristics import TransferCurve, fit_saturation_tempco

ESTIMATED_QGTH_FRACTION = 0.45
MAX_ZTC_RELATIVE_ERROR = 0.20


@dataclass(frozen=True)
class AnchorEvidence:
    manufacturer: str
    part: str
    pdf_rel: str
    table_page: int
    gate_charge_page: int
    gate_charge_figure: str
    id_gc_a: float
    vds_gc_v: float
    vgs_drive_v: float
    vpl_v: float
    qgs_nc: float
    qg_th_nc: float | None
    partition_basis: str
    condition_note: str
    vpl_basis: str = "reviewed gate-charge curve"

    @property
    def partition_is_explicit(self) -> bool:
        return (
            self.partition_basis == "datasheet-explicit"
            and self.qg_th_nc is not None
            and math.isfinite(self.qg_th_nc)
            and math.isfinite(self.qgs_nc)
            and 0.0 < self.qg_th_nc < self.qgs_nc
        )

    def resolved_qg_th_nc(self) -> float:
        if self.qg_th_nc is not None:
            return self.qg_th_nc
        return ESTIMATED_QGTH_FRACTION * self.qgs_nc

    def model_anchor(self) -> dict[str, float]:
        qg_th = self.resolved_qg_th_nc()
        values = (self.id_gc_a, self.vds_gc_v, self.vgs_drive_v, self.vpl_v, self.qgs_nc, qg_th)
        if not all(math.isfinite(value) and value > 0.0 for value in values):
            raise RuntimeError(f"nonpositive or nonfinite gate-charge anchor for {self.part}")
        if not qg_th < self.qgs_nc:
            raise RuntimeError(f"Qg_th must be below Qgs for {self.part}")
        vth = self.vpl_v * qg_th / self.qgs_nc
        p = 2.0
        k = self.id_gc_a / (self.vpl_v - vth) ** p
        return {
            "tref_c": 25.0,
            "vth_eff_v": vth,
            "k_a_per_vp": k,
            "p": p,
            "id_gc_a": self.id_gc_a,
            "vpl_v": self.vpl_v,
        }


# Values are transcribed from the stated pages of the local PDFs.  For the six
# parts without QGS(th)/QGS(th-pl), qg_th_nc intentionally remains None: the
# 45/55 split is produced only for diagnostic fitting and is a promotion guard.
ANCHORS: tuple[AnchorEvidence, ...] = (
    AnchorEvidence(
        "Alpha & Omega", "AOD442", "ao/AOD442.pdf", 2, 4, "Figure 7",
        20.0, 30.0, 10.0, 3.7, 6.0, None, "estimated-45pct-of-Qgs",
        "VDS=30 V, ID=20 A; PDF identifies AOD442/AOI442 (not AOD442G)",
    ),
    AnchorEvidence(
        "EPC Space", "EPC7018GSH", "epc_space/EPC7018GSH.pdf", 2, 5, "Figure 10",
        40.0, 50.0, 5.0, 2.54, 4.0, None, "estimated-45pct-of-Qgs",
        "VDS=50 V, VGS=5 V, ID=40 A",
    ),
    AnchorEvidence(
        "EPC Space", "FBG10N30BC", "epc_space/FBG10N30BC.pdf", 2, 6, "Figure 14",
        30.0, 50.0, 5.0, 3.05, 2.4, None, "estimated-45pct-of-Qgs",
        "Uses the 30 A Qgs row and the plotted ID=30 A curve; not the 15 A Qgs row",
    ),
    AnchorEvidence(
        "HXY", "IPT65R033G7XTMA1-HXY", "hxy/IPT65R033G7XTMA1-HXY.pdf", 3, 7,
        "Figure 10", 40.0, 400.0, 18.0, 5.4, 29.0, None,
        "estimated-45pct-of-Qgs",
        "Same HXY PDF supplies QGS=29 nC; no fuzzy substitution of Infineon QGS=27 nC",
    ),
    AnchorEvidence(
        "Littelfuse", "MTI145WX100GD-SMD", "littelfuse/MTI145WX100GD-SMD.pdf", 2, 6,
        "Figure 7", 100.0, 50.0, 10.0, 4.45, 48.0, None,
        "estimated-45pct-of-Qgs", "VGS=10 V, VDS=50 V, ID=100 A",
    ),
    AnchorEvidence(
        "NCE", "NCEP1520BK", "nce/NCEP1520BK.pdf", 2, 4, "Figure 5",
        10.0, 75.0, 4.5, 2.78, 3.3, None, "estimated-45pct-of-Qgs",
        "VDS=75 V, ID=10 A, VGS=4.5 V",
    ),
    AnchorEvidence(
        "Nexperia", "PSMN0R9-30YLD", "nxp/PSMN0R9-30YLD.pdf", 6, 9,
        "Figure 13", 25.0, 15.0, 4.5, 2.4, 15.3, 10.5,
        "datasheet-explicit",
        "QGS(th)=10.5 nC and QGS(th-pl)=4.8 nC; ID=25 A, VDS=15 V",
    ),
    AnchorEvidence(
        "Nexperia", "PXN017-30QL", "nxp/PXN017-30QL.pdf", 6, 9, "Figure 14",
        6.8, 15.0, 4.5, 2.5, 0.9, 0.5, "datasheet-explicit",
        "QGS(th)=0.5 nC and QGS(th-pl)=0.4 nC; plotted ID=6.8 A condition",
    ),
)


def _load_curves(path: Path) -> list[TransferCurve]:
    grouped: dict[float, list[tuple[float, float]]] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            temp = float(row["temperature_c"])
            grouped.setdefault(temp, []).append((float(row["Vgs_V"]), float(row["Id_A"])))
    if 25.0 not in grouped:
        raise RuntimeError(f"{path.name}: no 25 C curve")
    hot = max((temp for temp in grouped if temp > 25.0), default=None)
    if hot is None:
        raise RuntimeError(f"{path.name}: no curve hotter than 25 C")
    return [TransferCurve(25.0, grouped[25.0]), TransferCurve(hot, grouped[hot])]


def _ztc_guards(fit: dict[str, Any], curves: list[TransferCurve]) -> list[str]:
    model = fit["ztc_model_a"]
    chart = fit["ztc_chart_a"]
    common_max = min(max(i for _, i in curve.points) for curve in curves)
    reliable = [max(1.0, 0.02 * fit["anchor"]["id_gc_a"]), 0.95 * common_max]
    fit["ztc_reliable_current_range_a"] = reliable
    reasons: list[str] = []
    if model is not None and reliable[0] <= model <= reliable[1] and chart is None:
        reasons.append("ztc-model-crossing-inside-chart-range-but-chart-has-no-crossing")
    if chart is not None and model is None:
        reasons.append("ztc-chart-crossing-inside-reliable-range-but-model-has-no-crossing")
    if model is not None and chart is not None:
        rel = abs(model - chart) / max(abs(chart), 1e-12)
        fit["ztc_relative_error"] = rel
        if rel > MAX_ZTC_RELATIVE_ERROR:
            reasons.append(f"ztc-model-chart-relative-error>{MAX_ZTC_RELATIVE_ERROR:.0%}")
    else:
        fit["ztc_relative_error"] = None
    return reasons


def _anchor_record(evidence: AnchorEvidence) -> dict[str, Any]:
    record = asdict(evidence)
    record["qg_th_nc_resolved"] = evidence.resolved_qg_th_nc()
    record["partition_is_explicit"] = evidence.partition_is_explicit
    record["model_anchor"] = evidence.model_anchor()
    return record


def evaluate_part(
    evidence: AnchorEvidence,
    manifest_entry: dict[str, Any],
    batch_dir: Path,
) -> dict[str, Any]:
    points_path = batch_dir / manifest_entry["points_csv"]
    curves = _load_curves(points_path)
    guards: list[str] = []
    if not evidence.partition_is_explicit:
        guards.append("estimated-charge-partition")
    try:
        fit = fit_saturation_tempco(curves, evidence.model_anchor())
    except Exception as exc:
        guards.append("temperature-fit-failed")
        fit: dict[str, Any] | None = None
        fit_error = str(exc)
    else:
        fit_error = None
        if fit["cold_anchor_conflict"]:
            guards.append("cold-anchor-conflict")
        guards.extend(_ztc_guards(fit, curves))
    status = "fit-review-required" if not guards else "guard-refusal"
    return {
        "manufacturer": evidence.manufacturer,
        "part": evidence.part,
        "status": status,
        "eligible_for_attachment": False,
        "guard_reasons": guards,
        "anchor_evidence": _anchor_record(evidence),
        "transfer_axis": manifest_entry["axis"],
        "transfer_temperatures_used_c": [curve.tj_c for curve in curves],
        "transfer_points_csv": manifest_entry["points_csv"],
        "transfer_overlay": manifest_entry["overlay"],
        "fit": fit,
        "fit_error": fit_error,
    }


def _render_gate_charge_pages(
    datasheet_root: Path, out_dir: Path
) -> dict[str, dict[str, str]]:
    import fitz

    target = out_dir / "gate-charge-evidence"
    target.mkdir(parents=True, exist_ok=True)
    rendered: dict[str, dict[str, str]] = {}
    for index, evidence in enumerate(ANCHORS, 1):
        pdf = datasheet_root / evidence.pdf_rel
        with fitz.open(pdf) as doc:
            pages: dict[str, str] = {}
            for label, page_number in (
                ("table", evidence.table_page),
                ("figure", evidence.gate_charge_page),
            ):
                path = target / f"{index:02d}_{evidence.part}.{label}.page{page_number}.png"
                page = doc[page_number - 1]
                pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                pix.save(path)
                pages[label] = str(path.relative_to(out_dir))
        rendered[evidence.part] = pages
    return rendered


def _contact_sheet(results: list[dict[str, Any]], batch_dir: Path, out_path: Path) -> None:
    cells: list[tuple[dict[str, Any], Image.Image]] = []
    width = 620
    for result in results:
        image = Image.open(batch_dir / result["transfer_overlay"]).convert("RGB")
        image.thumbnail((width, 760), Image.Resampling.LANCZOS)
        cells.append((result, image.copy()))
    margin, header, gap = 22, 74, 18
    row_heights = [max(cells[i][1].height for i in range(row, min(row + 2, len(cells))))
                   for row in range(0, len(cells), 2)]
    canvas = Image.new("RGB", (2 * width + 3 * margin, sum(row_heights) + len(row_heights) * (header + gap) + margin), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=16)
    y = margin
    for row_index, row in enumerate(range(0, len(cells), 2)):
        for column, (result, image) in enumerate(cells[row:row + 2]):
            x = margin + column * (width + margin)
            axis = result["transfer_axis"]
            decision = result["status"]
            draw.text((x, y), f"{result['part']} — {decision}", fill="black", font=font)
            draw.text(
                (x, y + 25),
                f"X: {axis['x']['quantity']} [{axis['x']['unit']}] {axis['x']['min']:g}..{axis['x']['max']:g} {axis['x']['scale']}",
                fill=(130, 0, 130), font=font,
            )
            draw.text(
                (x, y + 47),
                f"Y: {axis['y']['quantity']} [{axis['y']['unit']}] {axis['y']['min']:g}..{axis['y']['max']:g} {axis['y']['scale']}",
                fill=(130, 0, 130), font=font,
            )
            canvas.paste(image, (x, y + header))
        y += header + row_heights[row_index] + gap
    canvas.save(out_path)


def _fmt(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:.{digits}g}"


def _write_report(
    results: list[dict[str, Any]],
    evidence_pages: dict[str, dict[str, str]],
    out_dir: Path,
) -> None:
    candidates = sum(result["status"] == "fit-review-required" for result in results)
    lines = [
        "# Cross-vendor saturation temperature-coefficient batch",
        "",
        f"Outcome: **{candidates} fit-review-required, {len(results) - candidates} guard-refused, 0 verified/attached.**",
        "",
        "`Id_gc` is the gate-charge test current, never the continuous current rating. Diagnostic fits are retained for refused parts so the failure is inspectable. A 45/55 charge split is an explicit estimate and blocks promotion.",
        "",
        "## Gate-charge anchors",
        "",
        "| Part | Id_gc | VDS | Vpl | Qgs | Qg_th used | Partition | Local evidence |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for result in results:
        evidence = result["anchor_evidence"]
        pages = evidence_pages[result["part"]]
        lines.append(
            f"| {result['part']} | {evidence['id_gc_a']:g} A | {evidence['vds_gc_v']:g} V | "
            f"{evidence['vpl_v']:g} V | {evidence['qgs_nc']:g} nC | "
            f"{evidence['qg_th_nc_resolved']:g} nC | {evidence['partition_basis']} | "
            f"[table p{evidence['table_page']}]({pages['table']}); "
            f"[{evidence['gate_charge_figure']} p{evidence['gate_charge_page']}]({pages['figure']}) |"
        )
    lines += [
        "",
        "Gate-charge evidence axes: **X = Qg [nC], Y = Vgs [V]** (see the linked full-resolution local pages).",
        "",
        "## Saturation temperature fits and holdouts",
        "",
        "| Part | T curves | dVth/dT | dlnK/dT | shift RMS | cold RMS / max | ZTC chart / model | Decision |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        fit = result["fit"]
        if fit is None:
            values = ["—"] * 5
        else:
            values = [
                f"{fit['d_vth_eff_v_per_k'] * 1e3:+.3f} mV/K",
                f"{fit['d_log_k_per_k'] * 1e3:+.3f}e-3/K",
                f"{fit['matched_shift_fit_rms_v']:.4f} V",
                f"{fit['cold_anchor_check_rms_v']:.3f} / {fit['cold_anchor_check_max_v']:.3f} V",
                f"{_fmt(fit['ztc_chart_a'])} / {_fmt(fit['ztc_model_a'])} A",
            ]
        reason = ", ".join(result["guard_reasons"]) or "human fit review pending"
        lines.append(
            f"| {result['part']} | {' / '.join(f'{t:g}°C' for t in result['transfer_temperatures_used_c'])} | "
            f"{' | '.join(values)} | **{result['status']}** — {reason} |"
        )
    lines += [
        "",
        "## Axis-labelled transfer evidence",
        "",
        "[Open the 8-panel contact sheet](saturation-tempco8-contact.png). Every panel repeats the complete calibrated axis identity, unit, range, and scale above its source overlay.",
        "",
        "| Part | X axis | Y axis | Overlay |",
        "|---|---|---|---|",
    ]
    for result in results:
        axis = result["transfer_axis"]
        x, y = axis["x"], axis["y"]
        lines.append(
            f"| {result['part']} | {x['quantity']} [{x['unit']}] {x['min']:g}..{x['max']:g} {x['scale']} | "
            f"{y['quantity']} [{y['unit']}] {y['min']:g}..{y['max']:g} {y['scale']} | "
            f"[axis-labelled overlay]({result['transfer_overlay']}) |"
        )
    lines += [
        "",
        "## Promotion rule",
        "",
        "A coefficient is attachable only after: explicit same-condition charge partition; no cold-anchor conflict; a consistent ZTC holdout whenever the crossing lies in the reliable chart range; and human review of the axis-labelled overlay. This batch performs no library attachment.",
    ]
    (out_dir / "saturation-tempco8.md").write_text("\n".join(lines) + "\n")


def run_batch(batch_dir: Path, datasheet_root: Path) -> dict[str, Any]:
    manifest = json.loads((batch_dir / "curated-manifest.json").read_text())
    by_part = {entry["part"]: entry for entry in manifest}
    missing = [evidence.part for evidence in ANCHORS if evidence.part not in by_part]
    if missing:
        raise RuntimeError(f"curated transfer manifest is missing: {', '.join(missing)}")
    results = [evaluate_part(evidence, by_part[evidence.part], batch_dir) for evidence in ANCHORS]
    evidence_pages = _render_gate_charge_pages(datasheet_root, batch_dir)
    _contact_sheet(results, batch_dir, batch_dir / "saturation-tempco8-contact.png")
    _write_report(results, evidence_pages, batch_dir)
    payload = {
        "contract": {
            "verified_or_attached": 0,
            "estimated_qg_th_fraction": ESTIMATED_QGTH_FRACTION,
            "maximum_ztc_relative_error": MAX_ZTC_RELATIVE_ERROR,
        },
        "anchors": [_anchor_record(evidence) for evidence in ANCHORS],
        "results": results,
    }
    (batch_dir / "saturation-tempco8.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n"
    )
    return payload
