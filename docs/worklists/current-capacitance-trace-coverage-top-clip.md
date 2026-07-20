# Capacitance trace coverage and clipped top decade

Status: peer-relative endpoint refusals, raster source-seating validation, and
inset-side plot-frame recovery are implemented on `main`. Qoss clipped-region
completion now keys off the calibrated plot ceiling rather than the highest
labeled tick. Focused tests and frozen full-corpus post-extraction A/B checks
pass. Fresh full-source re-extraction remains held because the local canonical
PDFs are unresolved Git-LFS pointers.

## Defect families

This worklist owns capacitance trace-fidelity failures that do not involve
Ciss/Coss shared identity:

1. **High-V Crss truncation.** The extracted Crss stops before the source trace
   reaches the chart's high-voltage endpoint while Ciss/Coss continue.
2. **False clipped-top completion.** The old Qoss gate treated Coss above the
   highest labeled tick as clipped even when the source trace remained fully
   visible below the calibrated plot frame.
3. **Full-count source divergence.** A trace can have a plausible point count
   and x-span yet leave its source stroke locally, so count-only validation is
   insufficient.
4. **Peer-relative Ciss/Coss early endpoint.** A raster trace may stay seated
   on source ink but stop materially before the visible source endpoint while
   one or two peers continue.

These are separate from
[unresolved Ciss/Coss shared collapse](current-capacitance-unresolved-shared-collapse.md).
A chart may carry both defects, but fixing one does not clear the other.

## Frozen positives

| Part | Panel | Evidence | Review state |
|---|---:|---|---|
| onsemi `FDMS2572` | p5d8 | Crss 389 points versus 427 for Ciss/Coss; x-span `0.9087`; `axis_top_pf=1000`, `max_low_v_coss_pf=1528.01`, `near_axis_top=true`; source result was `ok/pass` | Fab human-FLAGGED; high-V Crss truncation confirmed |
| onsemi `FDMS86200DC` | p6d8 | Crss 341 points versus 466 and x-span `0.7173`; human report also flags high-V truncation | Fab flagged, co-occurs with shared-collapse RED |
| Toshiba `TK55S10N1` | p6d88 | reviewer reports Crss truncation near 40 V | agent RED pending human review, co-occurs with shared-collapse RED |
| Toshiba `TPH3R10AQM` | p6d811 | Coss stops near 40 V while Ciss/Crss and the printed Coss source continue toward 100 V; pixel-space right-end deficit is 10.0% | Fab human-FLAGGED |
| Toshiba `TK7R2E15Q5` | p6d811 | Coss ends 7.1% of plot width before Ciss/Crss and its visible source tail | agent source-verified RED |
| Infineon `BSC120N12LS_G` | p8d11 | Ciss/Coss end 6.5%/7.8% before Crss and the common 120 V source endpoint | agent source-verified RED |
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
- `near_axis_top=true` means only that Coss approaches or exceeds the highest
  consumed label. It must not activate clipped completion unless Coss also
  reaches the independently calibrated plot ceiling (`near_plot_top=true`).
- A genuinely plot-top-clipped interval remains explicit and non-consumable
  unless the existing referenced completion contract validates it.
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
- A synthetic plot-top-clipped capacitance control whose completion path stays
  active, because no source-proven positive was present in the frozen corpus.

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

Raster Crss is suspect when it ends more than six percent of plot width
before a materially complete Ciss/Coss peer. The same bounded near-full rule
also covers vector output only when both upper paths reach at least 98% and
Crss itself reaches at least 85%; this catches `FDMS2572` without rejecting the
reviewed vector charts whose printed Crss source intentionally ends much
earlier. Fresh results:

- `FDMS2572` p5d8: `unverified`, reason
  `Crss_peer_relative_short_x_span`, physical output withheld.
- `TK55S10N1` p6d88: `unverified` with the same reason.
- `TPH3R10AQM` p6d811 and `TK7R2E15Q5` p6d811: the new raster right-end guard
  refuses the early Coss endpoint relative to two fuller peers.
- `BSC120N12LS_G` p8d11: the same guard refuses both early upper traces
  relative to Crss.
- `FDMS86202ET120` p5d8: remains `ok/pass` with equal 98% spans.

The right-end threshold was calibrated over 439 frozen historical panels. Its
11 previously passing raster matches were all visually source-proven defects.
On the 459-panel authoritative frozen packet, it changes 57 reason bundles but
only four statuses (`pass` to `suspect`); all four stop before visible source
ink ends. Vector panels are excluded because their source-owned paths may
intentionally terminate inside the frame. This is a safety refusal, not tail
reconstruction.

The Qoss clip gate now records both `highest_labeled_tick_pf` (with the legacy
`axis_top_pf` alias) and `plot_top_pf`. Completion activates only within two
percent of the latter. In the 459-panel authoritative frozen packet, all 49
old `near_axis_top` signals remain useful review metadata, but none reaches the
actual plot ceiling. Only five Qoss computations change: `FDD86250`, `FDS2672`,
`NTMFS6H824NLT1G`, `NTMFSC012N15MC`, and `NVTFS6H850NLWFTAG`. Each source
overlay shows visible, non-clipped Coss; the old path fabricated 1.2%--32.5%
clipped completion by comparing against an interior labeled tick. Trace points,
axis calibration, and chart availability are unchanged; missing-reference Qoss
remains diagnostic-only.
