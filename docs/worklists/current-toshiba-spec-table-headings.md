# Toshiba specification-table headings misdetected as charts

**Status:** focused and snapshot A/B agent-GREEN; a current full-corpus finder
run remains pending. No commit/push and `human_verified=false`.

## Defect

Seven sampled Toshiba families emit a synthetic diagram 901 gate-charge panel
from the section heading `Gate Charge Characteristics` merged with the following
specification-table header (`Symbol`, `Test Condition`, `Min`, `Typ`, `Max`).
The inferred panel owns table rules, not a measured curve. The real Toshiba
gate-charge chart is separately titled `Dynamic Input/Output Characteristics`.

## Guard

- Reject a consumed caption title only when at least three distinct table-header
  markers are present: Symbol, Test Condition(s), Min(imum), Typ(ical),
  Max(imum), Unit(s).
- Never reject on `Gate Charge Characteristics` alone.
- Keep a lone `typ` annotation, a genuine gate-charge title, and the real
  Dynamic Input/Output chart as negatives.

## Acceptance

- The seven sampled diagram-901 false panels disappear.
- Every corresponding real Toshiba gate-charge chart remains detected with the
  same page, diagram, kind, and crop bytes.
- Query all Toshiba and ST finder results for the table-header signature; every
  removed panel must be source-confirmed as a table and every collateral panel
  delta reviewed for ownership.
- Candidate/repeat finder manifests are byte-identical. Obtain independent
  review and keep `human_verified=false`.

## Frozen evidence

- Same-host native-sample A/B: seven Toshiba specification-table panels are
  removed exactly, one diagram 901 per part. Every peer panel row and crop is
  byte-identical, including the real Dynamic Input/Output gate-charge panels.
- A post-hoc query of the 14,359-PDF frozen finder snapshot found six additional
  Toshiba titles and zero ST titles matching the exact header-run predicate.
  Re-running those six PDFs with a baseline that disables only the predicate
  removes exactly six table panels with zero peer row/crop movement.
- Both groups have candidate/repeat byte identity. The frozen roots are
  `/private/tmp/dsdig-toshiba-table-heading-v1/{baseline-causal,candidate,repeat}`
  and `full-snapshot-affected/{baseline,candidate,repeat}`.
- The snapshot is strong affected-set evidence, but it is not a substitute for
  the current authoritative full finder corpus. Terminal shared-finder GREEN
  remains blocked on that run.
- Independent review confirmed all 13 source crops are specification tables,
  zero additions or peer row/crop movement, and byte retention of seven real
  Qg plus five real C(V) panels. Review JSON SHA-256:
  `ec84252bc26fa68628afdb87e09a5734605f203f915cb0272a9d6a1c12358006`.
