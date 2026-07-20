# Annotation per-panel refusal isolation

**Status:** implementation in progress; no commit/push and `human_verified=false`.

## Defect

`dsdig annotate` aborts the entire PDF when one detected body-diode panel cannot
calibrate an axis. HYG050N13NS1W page 5 Figure 8 raises
`X axis: no trustworthy numeric tick run`, so its valid gate-charge and
capacitance overlays are never written even though those digitizers are independent.

## Required direction

1. Keep the standalone body-diode extractor atomic: a failed extraction must not
   write an all-OK manifest.
2. Annotation processes already-owned body-diode panels independently, records each
   refusal with kind/page/diagram/error provenance, and continues unrelated panels.
3. A refused panel is never embedded and never serialized with physical points.
4. Successful peer panels remain byte-identical; no exception may be silently dropped.

## Acceptance

- HYG050N13NS1W produces a valid annotated PDF and explicit Figure 8 refusal while
  independently valid gate-charge and capacitance panels still embed.
- A two-panel fixture proves the first refusal cannot suppress the accepted peer.
- Existing standalone failure tests retain their raise/no-manifest contract.
- Run annotation regression and inspect all changed error/overlay records. Freeze a
  byte-reproducible target PDF, run `qpdf --check`, obtain independent agent review,
  and require Fab's human verdict. Agents never set `human_verified`.
