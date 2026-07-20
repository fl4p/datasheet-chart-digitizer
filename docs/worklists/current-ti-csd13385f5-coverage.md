# TI CSD13385F5 chart coverage

## Scope

Random-loop fixture: `ti/CSD13385F5.pdf`.

The datasheet contains five charts in families currently supported by the
annotator:

1. Figure 5-2 transfer characteristics;
2. Figure 5-4 gate charge;
3. Figure 5-5 capacitance;
4. Figure 5-8 normalized on-state resistance versus temperature; and
5. Figure 5-9 body-diode forward voltage.

Figures 5-1, 5-3, 5-6, 5-7, 5-10, and 5-11 are outside the current supported
family set and must remain unpainted.

## Baseline defects

- The page-3 electrical-characteristics table is falsely emitted as a
  capacitance chart.
- Figure 5-8 is not detected.
- Figure 5-9 owns Figure 5-7 as a neighbour, so its digitizer sees two plot
  frames and refuses for ambiguous grid binding.
- Figure 5-2 is correctly cropped but its axis parser admits non-axis text and
  reports non-monotone VGS ticks.
- Figure 5-5 is correctly cropped but the capacitance plot-box/grid path does
  not accept its sparse full-frame evidence.

## Constraints

- Fix caption/panel ownership with positive local evidence; do not add
  part-number or page-number exceptions.
- A table containing Ciss/Coss/Crss is not a chart without a locally owned plot
  frame and numeric axes.
- Never repair a digitizer by widening its crop into a neighbouring panel.
- Preserve fail-closed behaviour when axis, trace, legend, or plot ownership is
  ambiguous.
- Keep unsupported chart families unpainted.

## Acceptance

- Finder emits exactly the five supported real panels above and no page-3
  table panel.
- Every emitted crop owns one plot and its local axis/title evidence; no
  neighbour bleed.
- Transfer, gate-charge, capacitance, RDS(T), and body-diode overlays follow
  their printed source curves. Unsafe physical calibration remains explicitly
  withheld rather than guessed.
- The annotated PDF embeds every accepted or explicitly review-required
  supported overlay and no unsupported chart.
- Focused positive and negative fixtures pass; output repeats byte-identically.
- Independent agent review is required. Shared finder/digitizer landing remains
  blocked until the authoritative affected-corpus A/B is complete.

## Superseded v1 review

Independent review of the first freeze found the gate-charge curve correct but
the review crop RED: it included the Figure 5-3 title above and a capacitance
axis sliver at right. That packet must not be used for acceptance. The same
review also required extractor commit/source-content hashes in the packet.

## Frozen focused result v2

- Packet: `/private/tmp/csd13385f5-freeze-v2/run1`; same-environment repeat:
  `/private/tmp/csd13385f5-freeze-v2/run2`.
- Annotated PDF SHA-256 (both runs):
  `2803ccc1bd759f74cfc3990cf28fb2652c30a76b9086a4215a70831e7cb0be45`.
- Selected crop/overlay/value manifest SHA-256 (both runs):
  `bb945a5af5ace5f1c39d88fb08d2a31b6417020a2d02b16ddfb15d9f24176eeb`.
- Extractor source SHA-256:
  `bcb730e4ab3fb954576c5ee97a8ccc01bd1a5a493678bed97f3aae0483f17258`;
  source PDF SHA-256:
  `2137d551f01f48bd38b1703a4046c538ef629f14fe5c1b46fb789e2822705ee1`.
- Result: five detected panels, five embedded overlays, zero errors. Page 3 has
  no false capacitance panel; unsupported Figures 5-1, 5-3, 5-6, 5-7, 5-10,
  and 5-11 remain unpainted.
- The gate-charge review crop is now the positively closed four-rail source
  cell: no title from the row above and no neighboring capacitance content.
- Focused tests: 94 passed, 11 skipped, 24 subtests passed, plus two focused
  capacitance-vector tests. `qpdf --check`, `py_compile`, and `git diff
  --check` pass.
- The transfer item remains explicitly `overlay-review-required`; embedding it
  is review presentation, not automatic acceptance or `human_verified`.
- Independent v2 review: focused packet GREEN with the transfer item still
  human-review-required. Review JSON:
  `/private/tmp/csd13385f5-freeze-v2/run1/reviews/agent-csd13385f5-v2-001.codex-ee-hxy-agent-review.json`
  (SHA-256
  `5adebfb711bd7e8fe1a30f8012695e21ae0b44078b76851d7139610caec71a18`).
- Terminal shared-patch acceptance remains blocked on the authoritative
  affected-corpus A/B; `human_verified=false` and no commit/push is authorized.
