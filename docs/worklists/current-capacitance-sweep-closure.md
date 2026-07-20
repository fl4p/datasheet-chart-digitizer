# Capacitance sweep safety closure

Status: the 2026-07-20 discovery sweep is closed at the fail-closed safety
layer. All reviewed trace/frame families have a landed guard or evidenced frame
recovery on `main`, and the subsequent Qoss ceiling defect is also closed.
Fresh source-to-result corpus replay remains held by unavailable Git-LFS PDF
objects; `human_verified=false` for agent-only items.

## Frozen scope

The autonomous review covered 200 unique capacitance charts in batches 26--39.
It produced 178 agent-clean results and 22 defect rows. The authoritative
catalog and manifests remain in `dsdig-verify-backlog`; generated PDF copies
were not used as canonical inputs.

Random capacitance discovery reached saturation after two consecutive all-clean
batches. More random sampling is therefore lower value than fixing or reviewing
the known classes.

## Landed safety map

| Defect family | Safety behavior | Landing |
|---|---|---|
| Coss joins Ciss and never proves separation | explicit unresolved-identity refusal | `7f893fe` |
| Coss corner-cut or another raster trace leaves source ink | source-support refusal | `20ee7bf` |
| plot box is inset from an owned left/right frame | recover only from closed rail evidence | `fd9b21c` |
| all traces or one peer start late at the left edge | explicit left-coverage refusal | `8597346` |
| Crss ends early relative to complete Ciss/Coss peers | explicit peer-endpoint refusal | `7f893fe` |
| raster Ciss/Coss ends early relative to fuller peers | explicit peer-endpoint refusal | `5554396` |
| flat/grid-latched or locally off-source raster trace | source-support refusal | `20ee7bf` |
| Qoss treats an interior labeled tick as a clipped ceiling | completion keyed to calibrated plot top | `b0079bb` |

The guards do not invent replacement points. Except for the positive-evidence
frame recovery, they retain the diagnostic pixels while withholding physical
capacitance and derived Qoss values.

## Verification closure

- The incomplete-trace, left-edge, source-support, frame-recovery, endpoint,
  and Qoss slices each have focused tests and frozen candidate/repeat evidence.
- Frozen corpus checks cover 459 post-extraction results; the left-edge run also
  records its earlier 800-panel review contract.
- The source-support A/B inspected every new refusal against source crops and
  overlays. The endpoint and Qoss A/Bs likewise inspected every status or
  derived-value delta.
- The broad capacitance suite at `b0079bb` has 105 passing tests and exactly
  five expected failures caused by 131--132-byte Git-LFS pointer inputs.

## Next work

1. Prefer source-seated recovery for the repeated Coss cliff/snap and Crss-tail
   families only when every restored pixel is owned by the source curve.
2. Keep every new refusal as the fallback when curve identity or source support
   remains ambiguous.
3. Pivot layout sampling and implementation toward wrong-panel ownership bugs;
   these outrank improving already-safe capacitance refusals.
4. Re-run the exact source-to-result corpus once the canonical PDF objects are
   materialized. Generated annotation copies are not substitutes.
