# NVB055N60S5F supported-chart coverage

**Status:** requested Figures 2, 5, and 8 are focused agent-GREEN on the current
tree. Full-corpus gates and human review remain; no commit/push and
`human_verified=false`.

## Defects

- Figure 2, `Transfer Characteristics`, is detected but the shared raster
  plot-box detector refuses its valid five-column log grid.  The sparse-frame
  fallback then chooses the minimum/maximum vertical rails, so a long rail
  from the neighboring Figure 1 crop margin prevents positive four-side
  closure even though Figure 2 has its own closed frame.
- Figure 5, `Capacitance Characteristics`, is detected and its vector overlay
  follows all three printed traces.  The source's Crss curve rises strongly
  with VDS, so the physical monotonicity guard correctly refuses trusted
  output, but `--include-review-required` currently hides the source-faithful
  overlay by classifying every physical conflict as terminal `unverified`.
- Figure 8, `On-Resistance Variation vs. Temperature`, was omitted by title
  grammar and split-vector handling; it is covered by the separate RDS(T)
  title-variant packet.

## Acceptance

- Recover Figure 2 only from four mutually closing source rails.  Evaluate
  coherent rail pairs rather than global extrema; a foreign neighboring rail,
  crop border, missing bottom rail, or partial gridline must not qualify.
- Preserve exact VGS/ID tick fits, three temperature identities, extracted
  points, and the transfer review-only contract.  Embed only when
  `--include-review-required` is requested.
- Figure 5 remains non-physical and non-served.  When its axes and vector
  pixels are source-faithful, emit an explicit review-only overlay carrying
  `Crss_rises_with_vds_unphysical`; do not clear, mute, or relabel the guard.
- A/B the shared sparse-frame fallback and capacitance status split across
  their affected corpora, with byte-identical negatives and repeat output.
- Freeze byte-repeat annotated PDFs, run `qpdf --check`, obtain independent
  agent review, and never set `human_verified`.

## Focused current-tree evidence

- Figure 2 is detected with its own sparse closed frame and three source-seated
  -55/25/150 C branches. Its review-required overlay is embedded only with
  `--include-review-required`.
- Figure 5 uses trusted axes and source-faithful vector traces. The source
  itself prints rising Crss, so the result remains review-only with
  `Crss_rises_with_vds_unphysical`; physical columns and Qoss stay withheld.
- Figure 8 is detected by the dedicated RDS(T) route, owns its local frame, and
  yields the normalized 10 V source curve with Rds(25 C)=0.994885.
- Candidate/repeat annotated PDFs are byte-identical (SHA-256
  `b48fbb3782b7878ca5af0751b044053bdf60a8b250eee916c2903e7811ee3894`)
  and `qpdf --check` is clean. Focused coverage tests pass.
- Frozen independent review:
  `/private/tmp/dsdig-nvb055-current/reviews/agent-nvb055-current-001.codex-ee-hxy-review.json`
  (SHA-256
  `8382aab81c4de370566add740eee8e3b8bd0404d12c6ca97c4a1844b56728659`).
  It records focused GREEN, `human_verified=false`, and
  `full_corpus_verified=false`.

Figure 3 RDS-vs-current still refuses ambiguous legend binding and Figure 4
body-diode extraction still finds no source curves. Those are separate future
slices; they do not alter the focused verdict for Figures 2, 5, and 8.
