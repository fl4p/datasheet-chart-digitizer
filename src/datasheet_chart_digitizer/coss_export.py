"""Compact Coss(V) curve storage and SPICE-oriented export.

The digitizer emits dense Coss(V) samples.  For downstream circuit simulation,
global polynomials are a poor representation: the low-VDS knee is too sharp and
polynomials ring or produce negative derivatives.  This module keeps the
library representation as adaptive knots in log-space, then exports a monotone
Qoss(V) table for simulator-specific SPICE use.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

_trapz = np.trapezoid if hasattr(np, "trapezoid") else np.trapz


@dataclass(frozen=True)
class CossKnotModel:
    """Adaptive piecewise log-space representation of Coss(V).

    `vds` and `coss` are the retained knots.  Interpolation is linear in
    `log10(coss)` versus `log1p(vds / v_scale)`.
    """

    vds: tuple[float, ...]
    coss: tuple[float, ...]
    v_scale: float
    max_rel_error: float
    source_points: int
    achieved_max_rel_error: float


@dataclass(frozen=True)
class QossTable:
    """Dense monotone charge/energy table derived from a Coss knot model."""

    vds: tuple[float, ...]
    coss: tuple[float, ...]
    qoss_c: tuple[float, ...]
    eoss_j: tuple[float, ...]
    qoss_pc: float
    eoss_pj: float
    co_tr_pf: float
    co_er_pf: float


@dataclass(frozen=True)
class ExportJob:
    """One Coss export input discovered from a points CSV or manifest."""

    points_csv: Path
    name: str


@dataclass(frozen=True)
class ExportResult:
    """Summary of files and fit quality written for one exported curve."""

    name: str
    points_csv: str
    model_json: str
    qoss_csv: str
    spice_cir: str
    knots: int
    source_points: int
    achieved_max_rel_error: float
    qoss_pc: float
    co_tr_pf: float
    co_er_pf: float


@dataclass(frozen=True)
class ExportError:
    """One failed Coss export in a batch run."""

    name: str
    points_csv: str
    error: str


SPICE_ENGINE_NOTE = (
    "QSPICE-oriented behavioral charge-current snippet. LTspice can over-count "
    "switching loss with behavioral charge models during fast Coss rings; use "
    "the JSON/CSV data as the portable source for simulator-specific models."
)


def load_coss_points_csv(path: Path, trace: str = "Coss") -> tuple[np.ndarray, np.ndarray]:
    """Load calibrated Coss(V) points from a digitizer `.points.csv` file."""

    vds: list[float] = []
    coss: list[float] = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            if row.get("trace") != trace:
                continue
            try:
                v = float(row["vds_V"])
                c = float(row["cap_pF"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(v) and math.isfinite(c) and c > 0:
                vds.append(max(0.0, v))
                coss.append(c)
    return clean_coss_points(np.asarray(vds, dtype=float), np.asarray(coss, dtype=float))


def clean_coss_points(vds: np.ndarray, coss: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sort, validate, and merge duplicate voltage samples by median Coss."""

    vds = np.asarray(vds, dtype=float)
    coss = np.asarray(coss, dtype=float)
    if vds.ndim != 1 or coss.ndim != 1 or len(vds) != len(coss):
        raise ValueError("vds and coss must be same-length 1D arrays")
    finite = np.isfinite(vds) & np.isfinite(coss) & (coss > 0)
    vds = np.maximum(vds[finite], 0.0)
    coss = coss[finite]
    if len(vds) < 2:
        raise ValueError("need at least two positive Coss samples")

    order = np.argsort(vds)
    vds = vds[order]
    coss = coss[order]
    merged_v: list[float] = []
    merged_c: list[float] = []
    for value in np.unique(np.round(vds, 10)):
        mask = np.isclose(vds, value, atol=1e-10, rtol=0.0)
        merged_v.append(float(value))
        merged_c.append(float(np.median(coss[mask])))
    if len(merged_v) < 2 or merged_v[-1] <= merged_v[0]:
        raise ValueError("need at least two unique VDS samples")
    return np.asarray(merged_v, dtype=float), np.asarray(merged_c, dtype=float)


def build_adaptive_coss_model(
    vds: np.ndarray,
    coss: np.ndarray,
    *,
    max_rel_error: float = 0.02,
    v_scale: float | None = None,
    max_knots: int = 256,
) -> CossKnotModel:
    """Build an adaptive piecewise log-space model for Coss(V).

    The refinement criterion is max relative error against the supplied samples.
    It starts with endpoints, repeatedly adds the source sample with the largest
    interpolation error, and stops once the target is met.
    """

    if max_rel_error <= 0:
        raise ValueError("max_rel_error must be positive")
    vds, coss = clean_coss_points(vds, coss)
    if v_scale is None:
        span = float(vds[-1] - vds[0])
        v_scale = max(span * 0.01, 1e-6)
    if v_scale <= 0:
        raise ValueError("v_scale must be positive")

    selected = {0, len(vds) - 1}
    achieved = float("inf")
    while True:
        idx = sorted(selected)
        pred = evaluate_coss_knots(vds, vds[idx], coss[idx], v_scale)
        rel = np.abs(pred / coss - 1.0)
        achieved = float(np.max(rel))
        if achieved <= max_rel_error or len(selected) >= min(max_knots, len(vds)):
            break
        selected.add(int(np.argmax(rel)))

    idx = sorted(selected)
    return CossKnotModel(
        vds=tuple(float(v) for v in vds[idx]),
        coss=tuple(float(c) for c in coss[idx]),
        v_scale=float(v_scale),
        max_rel_error=float(max_rel_error),
        source_points=int(len(vds)),
        achieved_max_rel_error=achieved,
    )


def evaluate_coss_model(model: CossKnotModel, vds: np.ndarray | Iterable[float]) -> np.ndarray:
    return evaluate_coss_knots(
        np.asarray(list(vds) if not isinstance(vds, np.ndarray) else vds, dtype=float),
        np.asarray(model.vds, dtype=float),
        np.asarray(model.coss, dtype=float),
        model.v_scale,
    )


def evaluate_coss_knots(vds: np.ndarray, knot_vds: np.ndarray, knot_coss: np.ndarray, v_scale: float) -> np.ndarray:
    if len(knot_vds) < 2:
        raise ValueError("need at least two knots")
    z = _z_of_v(np.asarray(vds, dtype=float), v_scale)
    knot_z = _z_of_v(np.asarray(knot_vds, dtype=float), v_scale)
    log_c = np.interp(z, knot_z, np.log10(knot_coss), left=np.log10(knot_coss[0]), right=np.log10(knot_coss[-1]))
    return np.power(10.0, log_c)


def build_qoss_table(model: CossKnotModel, *, v_max: float | None = None, samples: int = 256) -> QossTable:
    """Sample the Coss model and integrate it to Qoss/Eoss tables."""

    if samples < 2:
        raise ValueError("samples must be >= 2")
    if v_max is None:
        v_max = float(model.vds[-1])
    if v_max <= 0:
        raise ValueError("v_max must be positive")
    vds = _table_grid(float(v_max), int(samples), model.v_scale)
    coss_pf = evaluate_coss_model(model, vds)

    q_pc = np.zeros_like(vds)
    e_pj = np.zeros_like(vds)
    for idx in range(1, len(vds)):
        dv = float(vds[idx] - vds[idx - 1])
        c_mid = 0.5 * float(coss_pf[idx] + coss_pf[idx - 1])
        vc_mid = 0.5 * float(coss_pf[idx] * vds[idx] + coss_pf[idx - 1] * vds[idx - 1])
        q_pc[idx] = q_pc[idx - 1] + c_mid * dv
        e_pj[idx] = e_pj[idx - 1] + vc_mid * dv

    qoss_pc = float(q_pc[-1])
    eoss_pj = float(e_pj[-1])
    return QossTable(
        vds=tuple(float(v) for v in vds),
        coss=tuple(float(c) for c in coss_pf),
        qoss_c=tuple(float(q) * 1e-12 for q in q_pc),
        eoss_j=tuple(float(e) * 1e-12 for e in e_pj),
        qoss_pc=qoss_pc,
        eoss_pj=eoss_pj,
        co_tr_pf=qoss_pc / float(v_max),
        co_er_pf=2.0 * eoss_pj / float(v_max) ** 2,
    )


def write_model_json(path: Path, model: CossKnotModel, table: QossTable) -> None:
    payload = {
        "model": asdict(model),
        "qoss_table": {
            "vds": list(table.vds),
            "coss": list(table.coss),
            "qoss_c": list(table.qoss_c),
            "eoss_j": list(table.eoss_j),
            "qoss_pc": table.qoss_pc,
            "eoss_pj": table.eoss_pj,
            "co_tr_pf": table.co_tr_pf,
            "co_er_pf": table.co_er_pf,
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def write_qoss_csv(path: Path, table: QossTable) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["VDS_V", "Coss_pF", "Qoss_C", "Qoss_pC", "Eoss_J", "Eoss_pJ"])
        for v, c, q_c, e_j in zip(table.vds, table.coss, table.qoss_c, table.eoss_j):
            writer.writerow([v, c, q_c, q_c * 1e12, e_j, e_j * 1e12])


def spice_qoss_table(model_name: str, table: QossTable, *, drain: str = "d", source: str = "s") -> str:
    """Return a generic SPICE behavioral current source using Qoss(V).

    The emitted expression follows QSPICE/LTspice-style `table()` syntax:
    `I = ddt(q(V(d,s)))`.  It is intended for QSPICE-oriented transient use and
    Qoss/Eoss extraction.  LTspice has known switching-loss accounting issues
    with behavioral charge models during fast Coss rings, so the JSON/CSV output
    remains the portable source for LTspice-specific or primitive fitted models.
    """

    safe = _safe_name(model_name)
    pairs = ", ".join(f"{v:.9g}, {q:.9e}" for v, q in zip(table.vds, table.qoss_c))
    return "\n".join(
        [
            f"* Coss charge table for {model_name}",
            f"* Qoss={table.qoss_pc:.6g} pC  Eoss={table.eoss_pj:.6g} pJ  Co(tr)={table.co_tr_pf:.6g} pF  Co(er)={table.co_er_pf:.6g} pF",
            f"* {SPICE_ENGINE_NOTE}",
            f".func qoss_{safe}(v) table(max(v,0), {pairs})",
            f"B_COSS_{safe} {drain} {source} I = ddt(qoss_{safe}(V({drain},{source})))",
            "",
        ]
    )


def write_spice(path: Path, model_name: str, table: QossTable, *, drain: str = "d", source: str = "s") -> None:
    path.write_text(spice_qoss_table(model_name, table, drain=drain, source=source))


def discover_export_jobs(input_path: Path) -> list[ExportJob]:
    """Discover Coss export jobs from a points CSV, manifest JSON, or output dir."""

    if input_path.is_dir():
        manifest = input_path / "capacitance_digitization.json"
        if manifest.exists():
            return _jobs_from_manifest(manifest)
        jobs = [ExportJob(path, _default_name_for_points(path)) for path in sorted(input_path.rglob("*.points.csv"))]
        return _dedupe_job_names(jobs)
    if input_path.suffix.lower() == ".json":
        return _jobs_from_manifest(input_path)
    return [ExportJob(input_path, _single_file_default_name(input_path))]


def export_coss_points(
    points_csv: Path,
    out_dir: Path,
    *,
    name: str,
    trace: str = "Coss",
    max_rel_error: float = 0.02,
    max_knots: int = 256,
    table_points: int = 256,
    vmax: float | None = None,
    drain: str = "d",
    source: str = "s",
) -> ExportResult:
    """Export one calibrated points CSV to JSON, Qoss CSV, and SPICE files."""

    vds, coss = load_coss_points_csv(points_csv, trace)
    model = build_adaptive_coss_model(
        vds,
        coss,
        max_rel_error=max_rel_error,
        max_knots=max_knots,
    )
    table = build_qoss_table(model, v_max=vmax, samples=table_points)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(name)
    model_json = out_dir / f"{safe}.coss_model.json"
    qoss_csv = out_dir / f"{safe}.qoss_table.csv"
    spice_cir = out_dir / f"{safe}.qoss_table.cir"
    write_model_json(model_json, model, table)
    write_qoss_csv(qoss_csv, table)
    write_spice(spice_cir, name, table, drain=drain, source=source)
    return ExportResult(
        name=name,
        points_csv=str(points_csv),
        model_json=str(model_json),
        qoss_csv=str(qoss_csv),
        spice_cir=str(spice_cir),
        knots=len(model.vds),
        source_points=model.source_points,
        achieved_max_rel_error=model.achieved_max_rel_error,
        qoss_pc=table.qoss_pc,
        co_tr_pf=table.co_tr_pf,
        co_er_pf=table.co_er_pf,
    )


def write_export_manifest(path: Path, results: list[ExportResult]) -> None:
    path.write_text(json.dumps([asdict(result) for result in results], indent=2) + "\n")


def write_export_errors(path: Path, errors: list[ExportError]) -> None:
    path.write_text(json.dumps([asdict(error) for error in errors], indent=2) + "\n")


def _z_of_v(vds: np.ndarray, v_scale: float) -> np.ndarray:
    return np.log1p(np.maximum(vds, 0.0) / float(v_scale))


def _table_grid(v_max: float, samples: int, v_scale: float) -> np.ndarray:
    # Uniform in log1p(V/Vscale), with exact endpoints, keeps enough density in
    # the low-voltage knee without exploding the table size.
    z = np.linspace(0.0, math.log1p(v_max / v_scale), samples)
    v = v_scale * np.expm1(z)
    v[0] = 0.0
    v[-1] = v_max
    return v


def _safe_name(name: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in name)
    return safe.strip("_") or "coss"


def _jobs_from_manifest(manifest_path: Path) -> list[ExportJob]:
    payload = json.loads(manifest_path.read_text())
    if not isinstance(payload, list):
        raise ValueError(f"{manifest_path} is not a capacitance digitization manifest")
    root = manifest_path.parent
    jobs: list[ExportJob] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        if row.get("axis_calibration_trusted") is False:
            continue
        points = row.get("points")
        if not points:
            continue
        points_path = Path(str(points))
        if not points_path.is_absolute():
            points_path = root / points_path
        part = str(row.get("part") or points_path.parent.name)
        diagram = str(row.get("diagram") or points_path.stem.replace(".points", ""))
        jobs.append(ExportJob(points_path, _manifest_job_name(part, diagram)))
    return _dedupe_job_names(jobs)


def _manifest_job_name(part: str, diagram: str) -> str:
    diagram = diagram.strip()
    if not diagram:
        return part
    return f"{part}_{diagram}"


def _default_name_for_points(points_csv: Path) -> str:
    stem = points_csv.stem.replace(".points", "")
    part = points_csv.parent.name
    if part and part != "points":
        return f"{part}_{stem}"
    return stem or points_csv.parent.name


def _single_file_default_name(points_csv: Path) -> str:
    return points_csv.parent.name or points_csv.stem.replace(".points", "")


def _dedupe_job_names(jobs: list[ExportJob]) -> list[ExportJob]:
    used_safe: set[str] = set()
    deduped: list[ExportJob] = []
    for job in jobs:
        name = job.name
        suffix = 2
        while _safe_name(name) in used_safe:
            name = f"{job.name}_{suffix}"
            suffix += 1
        used_safe.add(_safe_name(name))
        deduped.append(ExportJob(job.points_csv, name))
    return deduped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export digitized Coss(V) as adaptive knots and a SPICE Qoss table.")
    parser.add_argument(
        "input_path",
        type=Path,
        help="Digitizer .points.csv, capacitance_digitization.json manifest, or digitizer output directory.",
    )
    parser.add_argument("--out", type=Path, required=True, help="Output directory for JSON/CSV/SPICE files.")
    parser.add_argument("--name", default=None, help="Model name for single points CSV input.")
    parser.add_argument("--trace", default="Coss", help="Trace name to export, default Coss.")
    parser.add_argument("--max-rel-error", type=float, default=0.02, help="Adaptive Coss knot target relative error.")
    parser.add_argument("--max-knots", type=int, default=256, help="Maximum adaptive Coss knots.")
    parser.add_argument("--table-points", type=int, default=256, help="Qoss table sample count.")
    parser.add_argument("--vmax", type=float, default=None, help="Qoss table max voltage; defaults to max digitized VDS.")
    parser.add_argument("--drain", default="d", help="Drain node name used in SPICE snippet.")
    parser.add_argument("--source", default="s", help="Source node name used in SPICE snippet.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    batch_input = args.input_path.is_dir() or args.input_path.suffix.lower() == ".json"
    jobs = discover_export_jobs(args.input_path)
    if args.name is not None and batch_input:
        raise SystemExit("--name can only be used with a single points CSV input")
    if not jobs:
        raise SystemExit(f"no .points.csv inputs found in {args.input_path}")
    results: list[ExportResult] = []
    errors: list[ExportError] = []
    for job in jobs:
        name = args.name or job.name
        try:
            result = export_coss_points(
                job.points_csv,
                args.out,
                name=name,
                trace=args.trace,
                max_rel_error=args.max_rel_error,
                max_knots=args.max_knots,
                table_points=args.table_points,
                vmax=args.vmax,
                drain=args.drain,
                source=args.source,
            )
        except Exception as exc:
            if not batch_input:
                raise
            errors.append(ExportError(name=name, points_csv=str(job.points_csv), error=str(exc)))
            print(f"{name}: ERROR {exc}")
            continue
        results.append(result)
        print(
            f"{name}: knots={result.knots} source_points={result.source_points} "
            f"max_err={100 * result.achieved_max_rel_error:.2f}% "
            f"Qoss={result.qoss_pc:.3g} pC Co(tr)={result.co_tr_pf:.3g} pF"
        )
    if batch_input:
        write_export_manifest(args.out / "coss_export_manifest.json", results)
        print(f"wrote {args.out / 'coss_export_manifest.json'}")
        if errors:
            write_export_errors(args.out / "coss_export_errors.json", errors)
            print(f"wrote {args.out / 'coss_export_errors.json'}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
