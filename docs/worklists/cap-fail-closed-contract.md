# Capacitance fail-closed point-serialization contract

**Status:** implemented and full-800 A/B complete; independent terminal review
pending. Build on the frozen Qoss-clipped-contract baseline, keep the tick-center
calibration slice separate, and do not commit, push, or set `human_verified`.

## Defect and measured scope

The standing project contract says a refused chart cannot expose derived physical
scalars or curves merely because a consumer forgot to inspect `status`.  The frozen
Qoss-clipped-contract baseline violates that rule for capacitance points:

- 102 selected rows have `status=unverified` and
  `physical_output_available=false`;
- their points CSVs still contain 164,554 rows with numeric `vds_V`/`cap_pF`;
- none has served `qoss_metrics`, so this is specifically a C(V) point-contract leak;
- the current tick-center candidate incidentally blanks 38 of the 102 by finding new
  axis refusals, but 64 leaking rows remain.  That incidental subset is not a general
  contract fix.

This is the capacitance equivalent of the previously fixed GAN7R0/Vpl leaks: a
plausible physical curve remains ingestible beside a fail-closed flag.

## Required direction

1. Centralize the output decision: when `physical_output_available` is false, retain
   raw pixel provenance (`trace`, `x_px`, `y_px`, normalized coordinates and diagnostic
   counts) but serialize `vds_V` and `cap_pF` as blank/null for every point.
2. Do not erase the raw diagnostic trace.  Reviewers still need pixel geometry to
   diagnose why the item refused; the unsafe part is the calibrated physical mapping.
3. Apply the rule to every fail-closed status, not a list of current reason strings.
   A new refusal reason must inherit the contract automatically.
4. Keep C(V) availability separate from the Qoss-metric sub-contract.  This slice does
   not change `qoss_diagnostic_metrics`, Qoss validation, or any served row.
5. Emit an explicit contract/provenance reason so blank calibrated cells cannot be
   mistaken for missing extraction output.

## Acceptance

- Full frozen 800-panel same-environment A/B on the Qoss-clipped-contract baseline.
- Expected delta: exactly the 102 fail-closed points artifacts lose calibrated
  `vds_V`/`cap_pF`; every status, reason, physical flag, raw pixel, trace identity,
  plot box, axis calibration, anchor, Qoss field, and served-row points artifact is
  byte-identical.
- Conservation assertions: 102 leaking rows before, zero after; 164,554 numeric
  point rows demoted; no raw point row lost; no `physical_output_available=true` row
  changes.
- Fixtures cover each major refusal family (`*_flat_full_span_unverified`, short span,
  rank swap / shape-order refusal), plus an `ok` served negative that must retain
  calibrated values.
- Candidate and independent repeat manifests/artifacts are byte-identical.  Dual-agent
  patch GREEN, then Fab's gate.  Item verdicts remain whatever their source review says;
  sealing a leak does not make a refused chart GREEN.

## Integrated-source A/B evidence

The current integrated extractor contains newer flat/rising-trace guards, so its
authoritative frozen-input scope is 112 leaking artifacts / 180,096 calibrated
point rows rather than the earlier 102 / 164,554 estimate. The causal A/B at
`/private/tmp/dsdig-cap-failclosed-v1` reports:

- 477 results and 323 byte-identical errors;
- 112 leaking artifacts before, zero after;
- 180,096 calibrated point rows demoted with every raw point retained;
- zero row-field differences outside the explicit point contract;
- zero overlay, axis-debug, raw-point, or served-point differences;
- candidate and repeat manifests byte-identical.

This count increase is expected scope growth from the newer refusal guards, not
a serialization regression. Independent review and Fab's gate remain required.
