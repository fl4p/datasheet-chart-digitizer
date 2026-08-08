---
name: cv-coer-energy-and-anchor-only-tiers
description: Coss validation gained two non-Qoss tiers (pass_coer_energy, coss_anchor_only) because 26/30 fugu2 HS parts print no charge reference at all
metadata:
  type: project
---

Added 2026-08-08 (uncommitted at the time of writing) in
`capacitance_validation.py` + `mosfet_capacitance.py`.

**Why.** `qoss_validation_status` keyed solely on the Qoss CHARGE integral and
returned `None` when metrics could not be built, so `coss_dslib` rejected every
Coss trace with `qoss_validation:None:None`. It was not a parser gap: 26 of 30
fugu2 HS datasheets print no Qoss/Eoss/Co(er)/Co(tr) at all.

**The tier ladder**, descending, each naming exactly the check that ran:

| status | evidence |
|---|---|
| `pass` / `pass_vendor_qoss_curve_tail` / `clipped_chart_completed` | Qoss charge integral vs datasheet Qoss |
| `pass_coer_energy` | energy integral vs `1/2*Co(er)*V^2` at the quoted V |
| `coss_anchor_only` | ONE spec-table Coss point; pins value, not shape |

`QOSS_CHARGE_INTEGRAL_STATUSES` is the charge-only subset;
`QOSS_SERVABLE_STATUSES` is all three tiers. Consumers must branch on the
string, never on servability alone -- `coss_anchor_only` says nothing about
the curve's SHAPE, which is what P_coss integrates.

**Ordering is load-bearing.** The fallbacks are tried only when the charge
integral did not produce a servable verdict, AND `coss_anchor_only_validation`
refuses outright when `integral_reference_available`. A part that HAS a Qoss or
Co(er) reference and FAILS it keeps failing. Without that, the check would
disappear exactly when it has evidence and the evidence is bad.

**Every unevaluable branch returns its own explicit non-servable status**, never
`None` and never a passing tier: `coer_reference_unavailable`,
`coer_reference_condition_voltage_unavailable`,
`coer_energy_integral_unavailable`, `coer_unreliable_extrapolation`,
`coer_chart_clipped`, `coer_graph_table_inconsistent`;
`anchor_only_trace_validation_not_pass`, `anchor_only_no_coss_anchor`,
`anchor_only_coss_anchor_unusable`, `anchor_only_coss_anchor_inconsistent`,
`integral_reference_present_anchor_only_not_applicable`.

**Blocker for the Co(er) tier: fetlib supplies no condition voltage.**
`anchors.json` carries `output_charge.coer_pf` but no voltage, and Co(er) is
meaningless without the V it is quoted at. A new optional
`OutputChargeReference.coer_vint_v` (from `output_charge.coer_vint_v`, falling
back to `vint_v`) is read but nothing populates it today, so all 8 Co(er) parts
in the fugu2 corpora report `coer_reference_condition_voltage_unavailable`.
Guessing the voltage would fabricate the reference being validated, so the tier
refuses instead. Populate `coer_vint_v` in fetlib to switch it on.

Gotcha: the anchor residual key in `anchor_diagnostics.anchor_residuals[name]`
is `relative_error`, not `relative_residual`, and it carries a `reason` field
the sampler sets when it refused the anchor (`anchor_inside_trace_gap`). Both
must be honoured or the tier passes on an anchor that was never read.
