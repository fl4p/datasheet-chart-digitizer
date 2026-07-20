# RDS(T) title variants: IRF100B201 and NDB5060L

**Status:** focused implementation in progress; no commit/push and
`human_verified=false`.

## Defect

The finder identifies IRF100B201 Figure 6 as `rds_on`, but the normalized-
temperature plugin rejects `Normalized On-Resistance vs. Temperature` because
its regex accepts `on-state resistance` and `on resistance`, not the equivalent
hyphenated `on-resistance` spelling. NDB5060L Figure 3 is lost for the same
reason plus its equivalent phrase `On-Resistance Variation with Temperature`.
Both charts are omitted without an extraction error.

## Acceptance

- Admit `on-resistance`, and the relation phrase `variation with`, only with
  explicit temperature title/axis evidence and the existing physical gates.
- Figure 6 must pass the 25 °C unity, monotonicity, span, legend-binding, and
  tick-residual checks before embedding.
- Existing on-state/on resistance spellings stay unchanged; NDB5060L Figures 2
  and 4 and all other non-temperature RDS panels remain excluded.
- Freeze a byte-repeat annotated PDF, run `qpdf --check`, and obtain independent
  agent review; never set `human_verified`.
