"""Qoss/Coss validation helpers for MOSFET capacitance charts."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np

from .capacitance_types import AxisCalibration, OutputChargeReference, PlotBox, Trace

# Ciss/Coss/Crss are monotonically non-increasing in Vds, so a trace whose value
# RISES from low to high Vds is not a capacitance curve -- it is a mis-seat onto a
# non-cap panel misclassified as capacitance (SOA/Zth envelopes rise).  Flag when
# value climbs > this fraction of the plot height (left-fifth to right-fifth
# medians).  Calibrated: 24 good PASS charts top out at +0.011, while the SOA
# (NCE2010E, +0.221) and Zth (FDD6612A, +0.097) leaks sit far above 0.05.
UNPHYSICAL_VALUE_RISE_FRACTION = 0.05
MIN_MATERIAL_TRACE_X_SPAN_FRACTION = 0.65
MAX_CRSS_PEER_X_SPAN_DEFICIT = 0.06
MAX_TRACE_LEFT_EDGE_GAP_FRACTION = 0.03
MAX_VECTOR_TRACE_LEFT_EDGE_GAP_FRACTION = 0.06
MAX_PEER_LEFT_START_DEFICIT = 0.03
MAX_PEER_RIGHT_END_DEFICIT = 0.06
PEER_ENDPOINT_COMPARISON_ABS_TOL = 1e-9
FLAT_GRID_CAPTURE_MAX_Y_RANGE_PX = 1
# Grid-capture is raster-only; vector paths cannot latch onto a gridline.
FLAT_GRID_CAPTURE_RASTER_MIN_X_SPAN_FRACTION = MIN_MATERIAL_TRACE_X_SPAN_FRACTION
FLAT_GRID_CAPTURE_VECTOR_MIN_X_SPAN_FRACTION = 0.90
# Quanta an anchor must span before a comparison against it carries any
# information. On a LINEAR capacitance axis a small trace sits within a pixel or
# two of the axis rule, so a trace that is actually the rule still "agrees" with
# the spec table: GT020N10T's Crss anchor is 110 pF = 2.84 px and the served
# bottom FRAME read -9.0 % against it. Anchor agreement is structurally blind
# there, which is exactly why the chart must not self-report as resolved.
MIN_ANCHOR_RESOLUTION_PX = 4.0
# A Crss this small is corrected by an explicit additive offset at export
# instead of being rejected, so it is reported as unresolved but not as a
# defect. Kept here so the exporter and the chart status share one definition.
TINY_CRSS_ANCHOR_PF = 15.0
# Tiers in DESCENDING strength. Each names exactly the check that ran, so a
# consumer's provenance can never claim a Qoss check that did not happen.
#   pass / pass_vendor_qoss_curve_tail / clipped_chart_completed
#       the Qoss CHARGE integral was compared against a datasheet Qoss.
#   pass_coer_energy
#       no datasheet Qoss exists, but the datasheet quotes Co(er) and the
#       ENERGY integral of the digitized curve matches 1/2*Co(er)*V^2 at the
#       quoted V. This constrains Eoss -- the quantity P_coss consumes --
#       directly, so it is a peer of the Qoss check, not a weaker relative.
#   coss_anchor_only
#       the datasheet carries NO integral reference of either kind. The only
#       independent evidence is the single spec-table Coss point, which pins
#       the curve's VALUE at one voltage and says nothing about its SHAPE.
#       Materially weaker; a consumer must decide separately whether to take
#       it. It is still strictly more evidence than the scalar 1/sqrt(V) guess
#       these parts fall back to today, which is derived from that same point.
QOSS_CHARGE_INTEGRAL_STATUSES = frozenset(
    {
        "pass",
        "pass_vendor_qoss_curve_tail",
        "clipped_chart_completed",
    }
)
COER_ENERGY_INTEGRAL_STATUS = "pass_coer_energy"
COSS_ANCHOR_ONLY_STATUS = "coss_anchor_only"
# The ONLY charge-path outcomes a weaker tier may follow: the ones meaning "there
# was nothing to compare against". This is an ALLOWLIST on purpose. A denylist
# would let any future status fall through by default, and the statuses that must
# not fall through are exactly the ones carrying evidence -- `graph_table_inconsistent`
# (the curve was compared to a table figure and disagreed),
# `unreliable_extrapolation`, `chart_clipped_table_authoritative`. Overwriting one
# of those with a weaker verdict makes the check vanish precisely when it has
# evidence and that evidence is bad. Measured: IPLT60R160CM8 went
# graph_table_inconsistent -> coer_reference_condition_voltage_unavailable and
# dropped out of the regression harness's expected-inconsistent set.
QOSS_NO_REFERENCE_STATUSES = frozenset(
    {
        "reference_unavailable",
        "chart_clipped_reference_unavailable",
    }
)


def weaker_tier_may_follow(qoss_status: str | None) -> bool:
    """True only when the charge path found no reference to compare against.

    `None` means the charge metrics could not be built at all, which is also an
    absence of comparison rather than a failed one.
    """
    return qoss_status is None or qoss_status in QOSS_NO_REFERENCE_STATUSES


def integral_reference_available(output_ref: object) -> bool:
    """Whether ANY output-charge figure exists to validate an integral against.

    Co(tr) counts. It was invisible to the tier selector while `validate_axis`
    checked it, so a Co(tr)-only part whose charge check FAILED (measured: Co_tr
    +69% against a 25% tolerance) fell through to the anchor-only tier and was
    served on one anchor point. Kept here, next to the tier definitions, so the
    call site cannot quietly disagree with the rule.
    """
    return any(
        getattr(output_ref, name, None) is not None
        for name in ("qoss_pc", "coer_pf", "cotr_pf")
    )
QOSS_SERVABLE_STATUSES = frozenset(
    QOSS_CHARGE_INTEGRAL_STATUSES
    | {COER_ENERGY_INTEGRAL_STATUS, COSS_ANCHOR_ONLY_STATUS}
)
# Relative agreement required of the Co(er) energy check. Held at the same 0.25
# the Qoss charge path uses (`validate_axis(tol=0.25)`) so the two tiers are
# equally strict; Co(er) is a derived table figure with the same rounding and
# graph/table-consistency exposure as Qoss, and nothing in the data justifies
# giving it more room.
COER_ENERGY_RELATIVE_TOLERANCE = 0.25
# Agreement required of the single spec-table Coss point for the anchor-only
# tier. This is the ONLY evidence in that tier, so it is held tighter than the
# integral tolerances: a sampled value is read straight off a calibrated axis
# with no integration to average errors out.
COSS_ANCHOR_ONLY_RELATIVE_TOLERANCE = 0.10


def trace_validation_summary(
    diagnostics: dict[str, object],
    extraction_method: str | None = None,
    shared_collapse_spans: list[dict[str, object]] | None = None,
    left_start_fractions: dict[str, float] | None = None,
    source_support_diagnostics: dict[str, object] | None = None,
    right_end_fractions: dict[str, float] | None = None,
) -> dict[str, object]:
    """Fail closed on incomplete or semantically untrusted C(V) traces."""

    flat_span_gate = (
        FLAT_GRID_CAPTURE_VECTOR_MIN_X_SPAN_FRACTION
        if extraction_method == "vector"
        else FLAT_GRID_CAPTURE_RASTER_MIN_X_SPAN_FRACTION
    )
    reasons: list[str] = []
    source_support = source_support_diagnostics or {}
    if extraction_method == "raster" and source_support.get("applicable"):
        trace_support = source_support.get("trace_support")
        if isinstance(trace_support, dict):
            for name in ("Ciss", "Coss", "Crss"):
                item = trace_support.get(name)
                if isinstance(item, dict) and item.get(
                    "material_source_absent_runs"
                ):
                    reasons.append(f"{name}_source_ink_absent_run")
        if source_support.get("material_shared_orphan_source_runs"):
            reasons.append("ciss_coss_shared_trace_orphans_source_branch")
        capture = source_support.get("grid_rule_capture")
        if not isinstance(capture, dict) or not capture.get("evaluated"):
            # An unevaluated grid-capture check is UNVERIFIED, not clean: grid
            # ink satisfies the source-ink check, so nothing else here would
            # notice a trace riding a decade line.
            reasons.append("grid_rule_capture_unevaluated")
        else:
            captured = capture.get("captured_traces")
            if isinstance(captured, dict):
                for name in ("Ciss", "Coss", "Crss"):
                    if captured.get(name):
                        reasons.append(f"{name}_captured_by_grid_rule")
            # A run that met every capture test but whose rule could not be
            # separated from the trace was recorded and then consumed by
            # nobody, so an UNDECIDABLE capture check served as a clean one.
            undecidable = capture.get("undecidable_runs")
            if isinstance(undecidable, dict):
                for name in ("Ciss", "Coss", "Crss"):
                    if undecidable.get(name):
                        reasons.append(f"{name}_grid_rule_capture_undecidable")
    if any(
        span.get("separated_sign_before") is not None
        and span.get("separated_sign_after") is None
        for span in shared_collapse_spans or ()
    ):
        # Normal low-V convergence has no sign_before and later separates.
        reasons.append("ciss_coss_unresolved_shared_collapse")

    upper_spans = [
        float((diagnostics.get(name) or {}).get("x_span_fraction") or 0.0)
        for name in ("Ciss", "Coss")
        if isinstance(diagnostics.get(name), dict)
    ]
    crss_diag = diagnostics.get("Crss")
    if upper_spans and isinstance(crss_diag, dict):
        upper_span = max(upper_spans)
        crss_span = float(crss_diag.get("x_span_fraction") or 0.0)
        vector_tail_is_bounded = min(upper_spans) >= 0.98 and crss_span >= 0.85
        if (
            (extraction_method != "vector" or vector_tail_is_bounded)
            and upper_span >= MIN_MATERIAL_TRACE_X_SPAN_FRACTION
            and upper_span - crss_span > MAX_CRSS_PEER_X_SPAN_DEFICIT
        ):
            # Vector PDFs may intentionally stop Crss early, so only the
            # bounded near-full case fires there: both upper paths reach the
            # frame and Crss alone loses a short tail. Raster tracking has no
            # independent source-owned endpoint proof and uses the full rule.
            reasons.append("Crss_peer_relative_short_x_span")

    left_starts = {
        name: max(0.0, float(value))
        for name, value in (left_start_fractions or {}).items()
        if name in ("Ciss", "Coss", "Crss")
    }
    if len(left_starts) == 3:
        earliest = min(left_starts.values())
        maximum_left_gap = (
            MAX_VECTOR_TRACE_LEFT_EDGE_GAP_FRACTION
            if extraction_method == "vector"
            else MAX_TRACE_LEFT_EDGE_GAP_FRACTION
        )
        if earliest > maximum_left_gap:
            # Exact source-owned vector paths can intentionally begin just
            # inside the first labeled tick (TI 2N7002L starts near 0.25 V on
            # a 0.2 V frame). Raster paths retain the stricter completeness
            # gate because their endpoint ownership is inferred.
            reasons.append("all_traces_left_edge_gap")
        else:
            late_names = [
                name
                for name, start in left_starts.items()
                if start - earliest > MAX_PEER_LEFT_START_DEFICIT
            ]
            # A single lagging trace against two edge-reaching peers is strong
            # differential evidence.  Two traces may legitimately begin later
            # than the third (for example Toshiba Ciss/Crss source strokes), so
            # that pattern needs source-ink proof rather than a pixel-only gate.
            if len(late_names) == 1:
                reasons.append(f"{late_names[0]}_peer_relative_late_x_start")

    right_ends = {
        name: min(1.0, float(value))
        for name, value in (right_end_fractions or {}).items()
        if name in ("Ciss", "Coss", "Crss")
    }
    if extraction_method == "raster" and len(right_ends) == 3:
        fullest = max(right_ends.values())
        for name in ("Ciss", "Coss"):
            if (
                fullest - right_ends[name]
                > MAX_PEER_RIGHT_END_DEFICIT + PEER_ENDPOINT_COMPARISON_ABS_TOL
            ):
                # Raster traces have no source-owned endpoint proof. Across 439
                # frozen panels, every previously passing upper trace more than
                # 6% short of its fullest peer ended before visible source ink.
                # Vector paths are intentionally excluded because vendors may
                # author complete Ciss/Coss paths that stop inside the frame.
                reasons.append(f"{name}_peer_relative_early_x_end")

    for name in ("Ciss", "Coss", "Crss"):
        trace_diag = diagnostics.get(name)
        if not isinstance(trace_diag, dict):
            reasons.append(f"missing_{name}")
            continue
        points = int(trace_diag.get("points") or 0)
        span = float(trace_diag.get("x_span_fraction") or 0.0)
        if points < 8:
            reasons.append(f"{name}_too_few_points")
        # Some complete NXP source strokes intentionally stop around 68% of a
        # 100 V plot, so this is a material-source floor, not a frame-end rule.
        if span < MIN_MATERIAL_TRACE_X_SPAN_FRACTION:
            reasons.append(f"{name}_short_x_span")
        y_range = int(trace_diag.get("y_range_px") or 0)
        if y_range <= FLAT_GRID_CAPTURE_MAX_Y_RANGE_PX and span >= flat_span_gate:
            reasons.append(f"{name}_flat_full_span_unverified")
        if (
            float(trace_diag.get("value_rise_fraction") or 0.0)
            > UNPHYSICAL_VALUE_RISE_FRACTION
        ):
            reasons.append(f"{name}_rises_with_vds_unphysical")

    checks = diagnostics.get("checks")
    if not isinstance(checks, dict):
        reasons.append("missing_semantic_checks")
    else:
        if int(checks.get("common_samples") or 0) < 20:
            reasons.append("too_few_common_samples")
        if int(checks.get("ciss_coss_rank_swap_count") or 0) not in (0, 1):
            reasons.append("ciss_coss_rank_swap_count")
        if float(checks.get("crss_bottom_fraction") or 0.0) < 0.95:
            reasons.append("crss_not_bottom")
        if not bool(checks.get("ciss_flatter_than_coss")):
            reasons.append("ciss_not_flatter_than_coss")

    return {"status": "pass" if not reasons else "suspect", "reasons": reasons}


def unresolved_anchor_traces(
    y_log: object,
    y_scale: object,
    anchor_pf: dict[str, float | None],
    served_min_pf: dict[str, float | None] | None = None,
) -> dict[str, tuple[float, float]]:
    """Traces that fall below the axis's resolution somewhere along their span.

    Only linear capacitance axes can hit this: on a log axis every decade gets
    the same pixel budget.  Returns ``{name: (pixels, pf_per_px)}`` so callers
    can report the measurement, and an EMPTY dict when the axis is logarithmic
    or its scale is unknown -- absence of a linear scale is absence of this
    particular failure mode, not a clean bill of health for the trace.
    """

    if y_log is True:
        return {}
    try:
        pf_per_px = abs(float(y_scale))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}
    if pf_per_px <= 0.0:
        return {}
    unresolved: dict[str, tuple[float, float]] = {}
    for name, value in anchor_pf.items():
        # The anchor is stated at ONE voltage; the served curve keeps falling past
        # it. Judging resolvability on the anchor alone missed traces that are
        # resolvable where the anchor sits and sub-pixel further along -- measured:
        # HYG065N10LS1P's Crss anchor is 7.8 px but its curve reaches 0.57 px,
        # worse than the 0.84 px case this rule was written for, and its anchor
        # residual is -92.7%. So the deciding quantity is the MINIMUM of the two:
        # the anchor when that is all we have, the served curve's own floor when
        # the caller supplies it.
        floor_pf = min(
            (v for v in (value, (served_min_pf or {}).get(name)) if v), default=None
        )
        if not floor_pf:
            continue
        pixels = float(floor_pf) / pf_per_px
        if pixels < MIN_ANCHOR_RESOLUTION_PX:
            unresolved[name] = (pixels, pf_per_px)
    return unresolved


def anchor_resolution_reason(
    name: str, pixels: float, pf_per_px: float, anchor_value_pf: float
) -> str:
    exempt = name == "Crss" and anchor_value_pf <= TINY_CRSS_ANCHOR_PF
    suffix = "_offset_corrected_at_export" if exempt else ""
    return (
        f"{name}_anchor_below_axis_resolution{suffix}:"
        f"{anchor_value_pf:g} pF = {pixels:.2f} px on a linear axis "
        f"({pf_per_px:.2f} pF/px, need {MIN_ANCHOR_RESOLUTION_PX:g})"
    )


def trace_left_start_fractions(
    traces: list[Trace], plot: PlotBox
) -> dict[str, float]:
    """Measure each trace's first served source column in plot pixel space."""

    width = max(1, plot.width - 1)
    return {
        trace.name: (min(x for x, _y in trace.points) - plot.x0) / width
        for trace in traces
        if trace.points
    }


def trace_right_end_fractions(
    traces: list[Trace], plot: PlotBox
) -> dict[str, float]:
    """Measure each trace's last served source column in plot pixel space."""

    width = max(1, plot.width - 1)
    return {
        trace.name: (max(x for x, _y in trace.points) - plot.x0) / width
        for trace in traces
        if trace.points
    }


def value_rise_fraction(points: list[tuple[int, int]], plot_height: int) -> float:
    """Signed fraction of plot height a trace's value climbs, left-fifth to right.

    Value increases upward (smaller y_px), so positive => value rose with Vds.
    """
    xs = sorted(x for x, _ in points)
    span = xs[-1] - xs[0] if xs else 0
    if span <= 0:
        return 0.0
    lo, hi = xs[0] + 0.2 * span, xs[-1] - 0.2 * span
    left = [y for x, y in points if x <= lo]
    right = [y for x, y in points if x >= hi]
    if not left or not right:
        return 0.0
    return (float(np.median(left)) - float(np.median(right))) / max(1, plot_height)


def coss_metrics_to_json(metrics: object) -> dict[str, float]:
    return {
        "Qoss_pc": float(metrics.Qoss),
        "Eoss_pJ": float(metrics.Eoss),
        "Co_tr_pf": float(metrics.Co_tr),
        "Co_er_pf": float(metrics.Co_er),
        "Qoss_below_first_pc": float(metrics.Qoss_below_first),
        "Qoss_chart_range_pc": float(metrics.Qoss_chart_range),
        "Qoss_above_last_pc": float(metrics.Qoss_above_last),
        "Eoss_below_first_pJ": float(metrics.Eoss_below_first),
        "Eoss_chart_range_pJ": float(metrics.Eoss_chart_range),
        "Eoss_above_last_pJ": float(metrics.Eoss_above_last),
        "C0_pf": float(metrics.C0),
        "phi_v": float(metrics.phi),
        "m": float(metrics.m),
        "first_vds_v": float(metrics.first_vds),
        "first_coss_pf": float(metrics.first_coss),
        "splice_rel_error": float(metrics.splice_rel_error),
        "extrapolated_qoss_fraction": float(metrics.extrapolated_qoss_fraction),
        "clipped_completion_active": bool(metrics.clipped_completion_active),
        "clip_boundary_vds": metrics.clip_boundary_vds,
        "Qoss_clip_completed_pc": float(metrics.Qoss_clip_completed),
        "Qoss_clip_visible_floor_pc": float(metrics.Qoss_clip_visible_floor),
        "Qoss_clip_added_pc": float(metrics.Qoss_clip_added),
        "clipped_completion_fraction": float(metrics.clipped_completion_fraction),
    }


def coer_integration_voltage(output_ref: object) -> float | None:
    """The voltage Co(er)'s energy integral is taken to -- from Co(er)'s OWN stated
    condition, or None.

    Deliberately a named seam rather than an inline expression at the call site, so the
    rule can be pinned by a test. It replaced `output_ref.coer_vint_v or
    output_ref.vint_v`, which borrowed QOSS's condition voltage when Co(er)'s had not
    parsed. They are different numbers on the same datasheet -- Infineon states Coss and
    Qoss at 50 V but Co(er) over 0->80 V (80% of BVdss); onsemi states Co(er) at 50 V --
    and energy goes as V^2, so the borrow compares the curve against a reference 2.56x
    off and returns `pass_coer_energy`. Absence of the condition must fail closed, not
    reach for the nearest available number.
    """
    v = getattr(output_ref, "coer_vint_v", None)
    return float(v) if v is not None and float(v) > 0.0 else None


def coer_energy_validation(
    metrics: object | None,
    coer_pf: float | None,
    coer_vint_v: float | None,
) -> tuple[str, dict[str, object]]:
    """Compare the digitized ENERGY integral against a datasheet Co(er).

    Co(er) is DEFINED by the energy integral -- Eoss = 1/2*Co(er)*V^2 at the
    quoted V -- so `metrics.Co_er` (the energy-equivalent capacitance the
    integrator already derives) is directly comparable to the table value.

    Every branch that cannot evaluate the comparison returns an explicit
    non-servable status naming what was missing. None of them returns a
    passing tier, and none of them returns ``None``: absence of the reference,
    of its condition voltage, or of the integral itself is reported as such,
    so a consumer can never read "not evaluated" as "evaluated and fine".
    """

    diagnostics: dict[str, object] = {
        "coer_pf": coer_pf,
        "coer_vint_v": coer_vint_v,
        "tolerance": COER_ENERGY_RELATIVE_TOLERANCE,
    }
    if coer_pf is None or not float(coer_pf) > 0.0:
        return "coer_reference_unavailable", diagnostics
    if coer_vint_v is None or not float(coer_vint_v) > 0.0:
        # The value is useless without the voltage it is quoted at, and
        # guessing one would fabricate the very reference being validated.
        return "coer_reference_condition_voltage_unavailable", diagnostics
    if metrics is None:
        return "coer_energy_integral_unavailable", diagnostics
    extrapolated = float(getattr(metrics, "extrapolated_qoss_fraction", 1.0))
    diagnostics["extrapolated_fraction"] = extrapolated
    if extrapolated > 0.20:
        # Same ceiling the charge path uses: too much of the integral comes
        # from below the first digitized point to call the comparison a check.
        return "coer_unreliable_extrapolation", diagnostics
    if bool(getattr(metrics, "clipped_completion_active", False)):
        # The top decade was reconstructed rather than read; the energy
        # integral is then partly modelled and cannot referee the table.
        return "coer_chart_clipped", diagnostics
    extracted = float(getattr(metrics, "Co_er", float("nan")))
    diagnostics["extracted_coer_pf"] = extracted
    if not math.isfinite(extracted) or extracted <= 0.0:
        return "coer_energy_integral_unavailable", diagnostics
    relative = abs(extracted - float(coer_pf)) / float(coer_pf)
    diagnostics["relative_residual"] = relative
    if relative > COER_ENERGY_RELATIVE_TOLERANCE:
        return "coer_graph_table_inconsistent", diagnostics
    return COER_ENERGY_INTEGRAL_STATUS, diagnostics


def coss_anchor_only_validation(
    anchor_diagnostics: dict[str, object] | None,
    *,
    integral_reference_available: bool,
    trace_validation_status: str | None,
) -> tuple[str, dict[str, object]]:
    """Weakest tier: the single spec-table Coss point, and nothing else.

    `integral_reference_available` is load-bearing and is checked FIRST. A part
    that HAS a Qoss or Co(er) reference and fails it must keep failing; letting
    it fall through to this tier would make the check disappear exactly when it
    has evidence and that evidence is bad.
    """

    diagnostics: dict[str, object] = {
        "tolerance": COSS_ANCHOR_ONLY_RELATIVE_TOLERANCE,
        "integral_reference_available": integral_reference_available,
    }
    if integral_reference_available:
        return "integral_reference_present_anchor_only_not_applicable", diagnostics
    if trace_validation_status != "pass":
        return "anchor_only_trace_validation_not_pass", diagnostics
    residuals = ((anchor_diagnostics or {}).get("anchor_residuals") or {})
    coss = residuals.get("Coss") if isinstance(residuals, dict) else None
    if not isinstance(coss, dict):
        return "anchor_only_no_coss_anchor", diagnostics
    relative = coss.get("relative_error")
    diagnostics["coss_anchor_relative_error"] = relative
    diagnostics["coss_anchor_vds_v"] = coss.get("vds_v")
    diagnostics["coss_anchor_table_pf"] = coss.get("table_pf")
    diagnostics["coss_anchor_sampled_pf"] = coss.get("sampled_pf")
    diagnostics["coss_anchor_reason"] = coss.get("reason")
    if coss.get("reason"):
        # The sampler itself refused this anchor (e.g. `anchor_inside_trace_gap`).
        # An anchor that could not be read cannot be the tier's sole evidence.
        return "anchor_only_coss_anchor_unusable", diagnostics
    if relative is None or not math.isfinite(float(relative)):
        return "anchor_only_coss_anchor_unusable", diagnostics
    if abs(float(relative)) > COSS_ANCHOR_ONLY_RELATIVE_TOLERANCE:
        return "anchor_only_coss_anchor_inconsistent", diagnostics
    return COSS_ANCHOR_ONLY_STATUS, diagnostics


def qoss_validation_status(
    metrics: object | None,
    validation_error: str | None,
    vendor_tail_validation: dict[str, object] | None = None,
    *,
    table_reference_available: bool | None = None,
) -> str | None:
    if metrics is None:
        return None
    if vendor_tail_validation and vendor_tail_validation.get("status") == "pass":
        return "pass_vendor_qoss_curve_tail"
    if float(metrics.extrapolated_qoss_fraction) > 0.20:
        return "unreliable_extrapolation"
    if validation_error == "Qoss table reference unavailable":
        return (
            "chart_clipped_reference_unavailable"
            if bool(metrics.clipped_completion_active)
            else "reference_unavailable"
        )
    if bool(metrics.clipped_completion_active):
        if validation_error is not None:
            return (
                "chart_clipped_table_authoritative"
                if table_reference_available
                else "chart_clipped_reference_unavailable"
            )
        return "clipped_chart_completed"
    if validation_error is None:
        return "pass"
    return "graph_table_inconsistent"


def partition_qoss_metrics(
    metrics: dict[str, object] | None,
    validation_status: str | None,
    *,
    chart_physical_output_available: bool,
) -> tuple[dict[str, object] | None, dict[str, object] | None, bool]:
    """Separate served Qoss scalars from explicitly diagnostic-only metrics."""

    available = bool(
        metrics is not None
        and chart_physical_output_available
        and validation_status in QOSS_SERVABLE_STATUSES
    )
    if available:
        return metrics, None, True
    return None, metrics, False


def qoss_metrics_status_reasons(
    metrics: dict[str, object] | None,
    validation_status: str | None,
    *,
    chart_physical_output_available: bool,
) -> list[str]:
    """Explain every reason the derived Qoss bundle is not consumer-safe."""

    reasons: list[str] = []
    if metrics is None:
        reasons.append("qoss_metrics_unavailable")
    elif validation_status not in QOSS_SERVABLE_STATUSES:
        reasons.append(
            f"qoss_validation_status:{validation_status or 'unavailable'}"
        )
    if not chart_physical_output_available:
        reasons.append("chart_physical_output_unavailable")
    return reasons


def vendor_qoss_tail_validation(
    part: str,
    metrics: object | None,
    output_ref: OutputChargeReference,
    tol: float,
) -> dict[str, object] | None:
    if metrics is None or output_ref.vint_v is None:
        return None
    curve_path = _vendor_qoss_curve_path(part)
    if curve_path is None:
        return None
    rows: list[tuple[float, float]] = []
    with curve_path.open(newline="", errors="replace") as f:
        for row in csv.DictReader(f):
            try:
                rows.append((float(row["VDS_V"]), float(row["Qoss_nC"]) * 1000.0))
            except (KeyError, TypeError, ValueError):
                continue
    if len(rows) < 2:
        return None
    rows.sort()
    vds = np.array([v for v, _ in rows], dtype=float)
    qoss = np.array([q for _, q in rows], dtype=float)
    first_v = float(metrics.first_vds)
    vint = float(output_ref.vint_v)
    if first_v < vds[0] or first_v > vds[-1] or vint < vds[0] or vint > vds[-1]:
        return {
            "tail_source": "vendor_qoss_curve",
            "status": "out_of_range",
            "curve_csv": str(curve_path),
        }
    vendor_tail = float(np.interp(first_v, vds, qoss))
    vendor_total = float(np.interp(vint, vds, qoss))
    qoss_with_vendor_tail = float(metrics.Qoss_chart_range + metrics.Qoss_above_last + vendor_tail)
    ref = output_ref.qoss_pc if output_ref.qoss_pc is not None else vendor_total
    rel_to_ref = abs(qoss_with_vendor_tail - float(ref)) / float(ref) if ref else None
    rel_to_vendor = abs(qoss_with_vendor_tail - vendor_total) / vendor_total if vendor_total else None
    status = "pass" if rel_to_ref is not None and rel_to_ref <= tol else "fail"
    return {
        "tail_source": "vendor_qoss_curve",
        "status": status,
        "curve_csv": str(curve_path),
        "first_vds_v": first_v,
        "vint_v": vint,
        "vendor_tail_pc": vendor_tail,
        "vendor_total_pc": vendor_total,
        "chart_range_pc": float(metrics.Qoss_chart_range),
        "qoss_with_vendor_tail_pc": qoss_with_vendor_tail,
        "reference_qoss_pc": ref,
        "rel_error_to_reference": rel_to_ref,
        "rel_error_to_vendor_curve": rel_to_vendor,
    }


def _vendor_qoss_curve_path(part: str) -> Path | None:
    here = Path(__file__).resolve().parent
    candidates = [
        here / f"{part.lower()}_qoss_reference.csv",
        here / f"{part.lower()}_qoss_diagram17_reference.csv",
    ]
    if part == "IMZA75R050M2H":
        candidates.append(here / "imza_qoss_diagram17_reference.csv")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def top_decade_clip_diagnostic(
    trace_data: dict[str, list[tuple[float, float]]],
    calibration: AxisCalibration | None,
    plot: PlotBox,
) -> dict[str, object] | None:
    if calibration is None or "Coss" not in trace_data:
        return None
    data = sorted(trace_data["Coss"])
    if not data:
        return None
    axis_top_pf = 10.0 ** calibration.y_max_decade
    low_v_limit = calibration.x_min_v + 0.05 * (calibration.x_max_v - calibration.x_min_v)
    low_v_caps = [cap for vds, cap in data if vds <= low_v_limit]
    max_low_v_coss = max(low_v_caps or [data[0][1]])
    plot_top_pf: float | None = None
    if (
        calibration.y_scale is not None
        and calibration.y_scale < 0.0
        and calibration.y_offset is not None
    ):
        calibrated_top = calibration.y_scale * plot.y0 + calibration.y_offset
        candidate_top_pf = (
            10.0 ** calibrated_top if calibration.y_log else calibrated_top
        )
        if np.isfinite(candidate_top_pf) and candidate_top_pf > 0.0:
            plot_top_pf = float(candidate_top_pf)
    return {
        "highest_labeled_tick_pf": axis_top_pf,
        # Retained for compatibility with frozen review packets. This is the
        # highest consumed label, not necessarily the calibrated plot ceiling.
        "axis_top_pf": axis_top_pf,
        "plot_top_pf": plot_top_pf,
        "max_low_v_coss_pf": max_low_v_coss,
        "low_v_limit_v": low_v_limit,
        "near_axis_top": max_low_v_coss >= axis_top_pf * 0.70,
        "near_plot_top": bool(
            plot_top_pf is not None and max_low_v_coss >= plot_top_pf * 0.98
        ),
    }
