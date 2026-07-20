# Capacitance trace coverage and clipped top decade

Status: peer-relative Crss tail refusal implemented in the current worktree;
focused and bounded checks pass. Local full-count source-seating and clipped
top-decade recovery remain open. Fab's final Batch 27 verdict marks `FDMS2572`
human-FLAGGED. Full corpus A/B and independent review remain open.

## Defect families

This worklist owns capacitance trace-fidelity failures that do not involve
Ciss/Coss shared identity:

1. **High-V Crss truncation.** The extracted Crss stops before the source trace
   reaches the chart's high-voltage endpoint while Ciss/Coss continue.
2. **Clipped top decade.** The source Ciss/Coss enters above the calibrated
   plot ceiling while the output remains `status=ok` and
   `trace_validation_status=pass`.
3. **Full-count source divergence.** A trace can have a plausible point count
   and x-span yet leave its source stroke locally, so count-only validation is
   insufficient.

These are separate from
[unresolved Ciss/Coss shared collapse](current-capacitance-unresolved-shared-collapse.md).
A chart may carry both defects, but fixing one does not clear the other.

## Frozen positives

| Part | Panel | Evidence | Review state |
|---|---:|---|---|
| onsemi `FDMS2572` | p5d8 | Crss 389 points versus 427 for Ciss/Coss; x-span `0.9087`; `axis_top_pf=1000`, `max_low_v_coss_pf=1528.01`, `near_axis_top=true`; source result was `ok/pass` | Fab human-FLAGGED; high-V Crss truncation confirmed |
| onsemi `FDMS86200DC` | p6d8 | Crss 341 points versus 466 and x-span `0.7173`; human report also flags high-V truncation | Fab flagged, co-occurs with shared-collapse RED |
| Toshiba `TK55S10N1` | p6d88 | reviewer reports Crss truncation near 40 V | agent RED pending human review, co-occurs with shared-collapse RED |
| Toshiba `TPH3R10AQM` | p6d811 | Coss stops near 40 V (`x_span~0.47`) while Ciss/Crss and the printed Coss source continue toward 100 V (`x_span~0.94`) | Fab human-FLAGGED |
| Infineon `IPB160N04S2L-03` | p6d11 | Crss leaves the printed curve below roughly 1 V on a linear VDS axis despite a full point count | Fab flagged; distinct local-seating subtype |

Frozen evidence:

- `/Users/fab/dev/pv/ee/dsdig-verify-backlog/MANIFEST.opus-cap-batch26.jsonl`
- `/Users/fab/dev/pv/ee/dsdig-verify-backlog/MANIFEST.opus-cap-batch27.jsonl`
- `/Users/fab/dev/pv/ee/dsdig-verify-backlog/MANIFEST.opus-cap-batch29.jsonl`
- their corresponding `values.verify.json`, raw crop, and overlay artifacts.

## Fail-closed contract

- A per-curve endpoint-coverage check must compare the served trace with the
  owned source/plot extent. Do not use raw point-count equality as the sole
  test because steep valid curves may occupy fewer x-columns.
- A curve that stops materially before its source endpoint must be recovered
  from source-owned ink or have its physical values withheld with a specific
  diagnostic.
- `near_axis_top=true` with source values above `axis_top_pf` cannot silently
  disappear behind a generic full-curve pass. The clipped interval must remain
  explicit and non-consumable unless independently recovered.
- Local source seating remains mandatory even when point count and x-span are
  complete. The `IPB160N04S2L-03` low-V fixture must catch off-source Crss
  without broadly rejecting steep, source-seated Crss curves.
- A shared-collapse repair does not clear any Crss or top-axis diagnostic; all
  active defects must pass independently before physical output is consumable.

## Required controls

- onsemi `FDMS86202ET120` p5d8: clean same-family panel; all three curves have
  417 points and x-span `0.9811`. Its Ciss/Coss low-V convergence correctly
  re-separates (`separated_sign_after=-1`). Curves and values must remain
  byte-identical.
- At least one clean steep-Crss chart with nonuniform column density but full
  source endpoint coverage, proving the new guard is based on source extent
  rather than equal point counts.
- At least one intentionally chart-clipped capacitance panel whose existing
  partial/Qoss refusal remains unchanged.

## Acceptance

1. Freeze candidate/repeat artifacts for every positive and control at one
   source/dependency closure.
2. Assert `FDMS2572` cannot serialize `pass` or consumable full-span Crss while
   the confirmed high-V tail truncation remains.
3. Assert every recovered endpoint and the `IPB160N04S2L-03` low-V segment is
   source-seated under microscopic overlay review.
4. Keep `FDMS86202ET120` and all previously GREEN complete traces physically
   byte-identical.
5. Run the authoritative full capacitance-corpus A/B; inspect every changed
   curve endpoint, physical value range, trace status, and Qoss output.

## Current implementation evidence

Raster Crss is now suspect when it ends more than six percent of plot width
before a materially complete Ciss/Coss peer. The same bounded near-full rule
also covers vector output only when both upper paths reach at least 98% and
Crss itself reaches at least 85%; this catches `FDMS2572` without rejecting the
reviewed vector charts whose printed Crss source intentionally ends much
earlier. Fresh results:

- `FDMS2572` p5d8: `unverified`, reason
  `Crss_peer_relative_short_x_span`, physical output withheld.
- `TK55S10N1` p6d88: `unverified` with the same reason.
- `TPH3R10AQM` p6d811: already fails the existing material-span guard because
  Coss reaches only about 47% of the plot; it is a frozen positive for later
  source-owned tail recovery, not a new validation hole.
- `FDMS86202ET120` p5d8: remains `ok/pass` with equal 98% spans.

This is a safety refusal, not a tail reconstruction. `IPB160N04S2L-03` local
low-V source seating and the clipped-top-decade policy are still unresolved.
