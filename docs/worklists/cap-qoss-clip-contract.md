# Capacitance Qoss clipped-chart contract worklist

**Status:** design/diagnosis only. Build on the cap-anchor parser plus
closed-bottom-frame candidate. Do not mix this into either frozen packet,
commit, push, or set `human_verified`.

## Defect

`qoss_validation_status()` currently returns
`chart_clipped_table_authoritative` whenever clipped completion is active
and *any* validation error exists. That is semantically false when the error is
`Qoss table reference unavailable`: there is no authoritative table value.
FDPF190N15A therefore emits clipped Qoss metrics (17.2% completion) beside a
status claiming table authority that does not exist.

The C(V) curve and Qoss scalar have separate contracts. A source-faithful C(V)
curve may remain available while Qoss is refused.

## Required direction

1. Give Qoss its own explicit availability flag. It is true only for a passing
   graph/table or vendor-tail validation, or for a clipped completion whose
   required reference actually exists and validates.
2. When Qoss is unavailable, do not expose its derived scalar bundle as served
   `qoss_metrics`. Preserve diagnostics under an explicitly diagnostic-only
   field if needed; never make a consumer infer safety from a status string.
3. Replace the impossible `chart_clipped_table_authoritative` +
   `Qoss table reference unavailable` combination with a fail-closed reason.
4. Do not null source-faithful Ciss/Coss/Crss points merely because Qoss is
   unavailable.
5. Keep the genuine graph/table inconsistency guard active; fixing status
   wording must not mute disagreement detection.

## Acceptance

- Unit fixtures: clipped+valid-table, clipped+missing-table,
  unclipped+missing-table, vendor-tail pass, genuine disagreement, and
  unreliable extrapolation.
- Full frozen 800-chart same-environment A/B using the authoritative harness.
- Expected changes are limited to Qoss status/availability/served-metric
  serialization. Axis, plot box, trace points, identities, shared spans, and
  anchor diagnostics must be byte-identical.
- Every newly refused Qoss item receives a concrete per-item reason. Any newly
  accepted Qoss item is RED unless independently source-verified.
- FDPF190N15A must keep its recovered vector C(V) points while its clipped Qoss
  result fails closed.

