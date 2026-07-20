# Digitizer worklist index

This directory is the canonical worklist source. It mixes active implementation slices,
frozen review packets, future coverage work, and retained design studies. A file's status
line is authoritative; same-named `WORKLIST.md` files inside frozen review-backlog packet
directories are historical packet snapshots and must not override this directory. The table
below is the navigation and dependency-order view. None of these documents authorizes an agent
to set `human_verified`, commit, or push.

## Current sampling loop

The [datasheet layout clustering](current-layout-clustering.md) index groups
canonical PDFs by page and document structure before choosing vendor-series
samples. Generated `*.pdf.<transform>.pdf` copies are excluded and indexed as
variants; cluster labels are sampling metadata, never runtime detector input.

Fab stopped the earlier five-vendor random sampler, then explicitly authorized
an unsupervised defect-closing loop on 2026-07-20. Sampling remains
layout-driven: choose canonical medoids/outliers from the structural index,
presently emphasizing Nexperia, ROHM, Vishay, and IXYS/Littelfuse. Each newly
flagged item must enter a bounded worklist and must not be laundered by later
sampling. SPD03 remains a historical open packet; its gates are not waived.

Current focused slices include [Toshiba dual-Y gate charge](current-toshiba-dual-y-gate-charge.md),
[ST normalized breakdown](current-st-normalized-breakdown-caption.md),
[Onsemi frameless cover prose](current-onsemi-frameless-cover-prose.md), and
[NDB5060L capacitance right frame](current-onsemi-ndb5060l-cap-right-frame.md),
plus [Onsemi shared-endpoint capacitance source paths](current-onsemi-shared-cap-source-paths.md),
[capacitance unresolved shared-collapse fail-close](current-capacitance-unresolved-shared-collapse.md),
[capacitance trace coverage and clipped top decade](current-capacitance-trace-coverage-top-clip.md),
[capacitance left-edge trace coverage](current-capacitance-left-edge-coverage.md),
[capacitance Coss source-support fail-close](current-capacitance-coss-source-support.md),
[capacitance symmetric side-frame coverage](current-capacitance-side-frame-coverage.md),
[Infineon IPD50N10S3L-16 capacitance right-frame coverage](current-infineon-ipd50-cap-right-frame.md),
[Infineon BSC normalized RDS(on) temperature routing](current-infineon-bsc059-rdst.md),
[TI CSD19537Q3 converging transfer paths](current-ti-csd19537q3-transfer.md), and
[ST MDmesh M9 thin vector strokes](current-st8l65-thin-vector-strokes.md), plus
[compact formula caption ownership](current-compact-formula-captions.md) and
[STH310 breakdown neighbor-rail ownership](current-sth310-breakdown-neighbor-rail.md).
NDB5060L is human-GREEN across all charts as of 2026-07-20; its shared landing
gate was completed in `7f893fe`.
The [STP15NK50Z detached caption](current-stp15nk50z-detached-caption.md)
finder is GREEN while its outlined-tick extraction remains blocked. The
[Toshiba TK100 raster caption](current-toshiba-tk100-raster-caption.md) finder
and capacitance item are GREEN while the separately owned gate-charge item
remains RED.
The [Infineon formula-variable RDS routing](current-infineon-ipa60r-rds-routing.md)
mechanism is GREEN while its first served temperature item remains RED. The
[Onsemi uppercase engineering-suffix capacitance axis](current-onsemi-ntmfs-k-axis.md)
parser and recovered item are independently GREEN; its unrelated Toshiba
raster/OCR control remains RED.
The [IRF6644 two-temperature log transfer](current-infineon-irf6644-log-transfer.md)
V3 target and bounded controls are independently GREEN; the authoritative full
transfer-corpus A/B remains a landing gate.
The [TI linear capacitance axis](current-ti-csd16342-linear-cap-axis.md) V1 RED
is superseded by an independently GREEN V2 target and focused mechanism; the
authoritative full capacitance-corpus A/B remains held.
The [TI CSD86330Q3D subunit log capacitance axes](current-ti-csd86330-subunit-log-axis.md)
recover only complete, positioned pF/nF power-of-ten ladders; both items,
the bounded mechanism, and controls are independently GREEN while Qoss,
full-corpus, and human review remain held.
The [TI 2N7002L dense non-decade capacitance axis](current-ti-2n7002-dense-log-capacitance.md)
and its 0.25--6 V source-path recovery are independently GREEN through the
mandatory Ciss/Coss crossing microscope. Five controls are byte-identical;
Qoss, full-corpus generalization, and human review remain held.
The [onsemi NTMFS6H864NLT1G dense linear grid seating](current-onsemi-ntmfs6h-linear-grid-residual.md)
admits only dense, coherently refitted linear ladders in the narrow 2.0--2.25 px
frame-quantization band; the target and bounded mechanism remain independently
GREEN. Its stale V1 first-rail centering evidence is superseded by the
[IRF3205 projection-center correction](current-infineon-irf3205-spaced-celsius.md),
whose target and fresh NTMFS6H control are independently scoped-GREEN. Both
authoritative full-corpus and human reviews remain held.
The [STP38 split-half body-diode recovery](current-stp38-body-diode-split-halves.md)
is independently GREEN on the target and bounded fail-closed pairing mechanism;
the authoritative full body-diode corpus A/B remains held.
The [STL135 bbox-process fallback](current-stl135-pymupdf-bbox-fallback.md) V2
is mechanism-GREEN with corrected suite evidence; it is explicitly a
defense-in-depth path because the originally reported Poppler abort is not
currently reproducible.
The [FDB035 thin transfer recovery](current-onsemi-fdb035-thin-transfer.md) V1
mechanism-GREEN/axis-RED is superseded by an independently GREEN V2 target and
bounded source-grid snap mechanism; full transfer-corpus A/B remains held.
The [TI neutral-gray temperature-curve recovery](current-ti-gray-temperature-curves.md)
is independently GREEN for two transfer items, one body-diode item, and the
bounded fallback mechanism; authoritative full transfer/body-diode corpus A/B
remains held.
The [STP45 thin RDS(Tj) trace and structured diagnostics](current-stp45-rdst-thin-diagnostics.md)
are independently GREEN on the bounded mechanism, while the physical item
correctly remains refused on separate temperature-axis identity evidence.
The [IPT007N06N absolute RDS(Tj) curves](current-infineon-ipt007-absolute-rdst.md)
serve only a source-owned mΩ axis with exactly two noncrossing typ/max traces
and one local VGS/ID condition. The item and bounded mechanism are independently
GREEN; generalized absolute axes, full RDS(Tj) corpus A/B, and human review
remain held.
The [BSZ018 below-ZTC two-temperature transfer](current-infineon-bsz018-below-ztc-transfer.md)
target, fail-closed order mechanism, and bounded crossing/log-axis controls are
independently GREEN; full transfer-corpus A/B and human review remain held.
The [IRF100PW219 crossing-transfer label binding](current-infineon-irf100pw219-transfer-binding.md)
admits only source labels proven outside opposite sides of the local two-curve
envelope after the existing distance binder refuses. The item and bounded
mechanism are independently GREEN; full transfer-corpus A/B and human review
remain held.
The [FDB047 unique below-caption RDS(Tj) recovery](current-onsemi-fdb047-rdst-direction.md)
target and bounded direction/axis-identity controls are independently GREEN,
with nonblocking following-caption context contamination recorded; full
RDS(Tj) corpus A/B and human review remain held.
The [breakdown raster frame-warning correction](current-breakdown-frame-warning.md)
is frozen as a diagnostic-only candidate with STD14 unlabeled-tail and AIMDQ
verified-vector controls; full breakdown-corpus A/B and human review remain
held.
The [Infineon BSB028 drawn-minus temperature-axis correction](current-infineon-bsb028-drawn-minus-axis.md)
is frozen with a native-signed BSB056 control; it restores only uniquely owned
source glyphs and deliberately refuses geometry-free mirror-ambiguous sign
inference. Full breakdown-corpus A/B and human review remain held.
The [Toshiba TPCC8105 signed capacitance-axis grid seating](current-toshiba-tpcc8105-signed-capacitance-axis.md)
V2 item and bounded mechanism are independently GREEN and explicitly supersede
the V1 tick-centering RED; full capacitance-corpus A/B and human review remain
held.
The [STW46NF30 transposed body-diode Figure 13](current-stw46nf30-transposed-body-diode.md)
target, atomic current-axis finder, bounded Y-tick retry, determinism, and three
controls are independently GREEN; full finder/body-diode corpus A/B and human
review remain held.
The [Infineon BSP135 depletion-mode Vpl diagnostic](current-infineon-bsp135-depletion-vpl.md)
removes only an enhancement-tuned false warning after a signed axis, negative
trace start, in-range scalar, and source plateau all agree. The item and bounded
diagnostic mechanism are independently GREEN; full gate-charge corpus A/B and
human review remain held.
The [STL52DN4LF7AG vector-outline breakdown axis](current-stl52-vector-outline-breakdown.md)
uses OCR only after empty native gutters, a zero-image page, owned closed grid,
and one full-span curve all agree. Figure 11 and the bounded mechanism are
independently GREEN; other outlined-axis plugins, full breakdown corpus, and
human review remain held.

## Ready-for-landing dependency chain

| Order | Worklist | State | Remaining gate |
|---:|---|---|---|
| 1 | [Vpl range fail-close v1](vpl-range-fail-close.md) | dual-agent patch GREEN, not landed | Fab overlay/landing decision |
| 2 | [Recovery v2](recovery-v2.md) | dual-agent item GREEN, not landed | Fab overlay/landing decision |
| 3 | [Qoss reference parser v1](qoss-ref-parser.md) | dual-agent patch GREEN, not landed | Fab/landing decision |
| 4 | [Capacitance anchor parser v1](cap-anchor-parser.md) | dual-agent patch GREEN, not landed | Fab microscopic PSMN2R4/PSMNR70 gate |
| 5 | [FDPF closed-frame trace v2](fdpf190-cap-trace.md) | dual-agent patch GREEN, not landed | Three recovered items remain UNVERIFIED pending exact tick-center and near-axis-top gates |
| 6 | [Qoss clipped-chart contract v1](cap-qoss-clip-contract.md) | dual-agent patch GREEN, not landed | Fab/landing decision; FDPF item remains UNVERIFIED |

The order is causal, not a request to squash changes together. Each slice keeps its
own source diff, full authoritative A/B, review packet, and verdict.  If an earlier
slice changes, invalidate and rerun later A/Bs rather than carrying stale hashes.

## Blocked / deferred implementation

- [Capacitance fail-closed point contract](cap-fail-closed-contract.md): scoped,
  not implemented; it precedes any further shared capacitance calibration work.
- [Capacitance tick centers](cap-axis-tick-centers.md): BLOCKED on deterministic
  byte-repeat and the point contract. It is deliberately not a terminal SPD03 gate.

## Future bounded work

- [Qoss inline min/typ/max recovery v2](qoss-inline-recovery-v2.md): recover 13
  source-proven NXP values after Qoss parser v1; no existing value may move.
- [NCE SOA panel misbinding](nce-soa-panel-misbinding.md): finder rejects three
  SOA panels currently fail-closed as capacitance and fixes `NCE2010E`, which currently
  serves SOA/body-diode-derived C(V) points as if they were capacitance curves.
- [Finder panel-ownership follow-ups](finder-panel-ownership-followups.md): source-bound
  caption/crop fixes for the HXY and Toshiba finder defects discovered during the random loop.
- [Gate-charge definition-waveform audit](gate-charge-definition-waveforms.md): source-gated
  review of standalone waveform captions; never blanket-reject the word `waveform`.
- [Review-risk triage](review-risk-triage.md): display-queue filter only; hidden
  never means verified or consumable.
- [Transfer signed semantics](transfer-signed-semantics.md): signed-temperature
  binding and explicit P-channel magnitude-transform provenance.
- [Recovered transfer trace fidelity](transfer-recovered-trace-fidelity.md): repair the
  independently reviewed 22 NXP identity swaps and 6 branch/grid/leader captures without
  reopening the 141-panel ownership recovery.
- [Breakdown fail-closed contract](breakdown-fail-closed-contract.md): an
  unverified/FAIL anchor must not leave calibrated curves or derived scalars
  consumable merely because extraction succeeded.
- [Infineon IPD650P06NM visual dash-style recovery](current-infineon-ipd650-body-diode-dash-styles.md):
  reconstruct four typ/max curve styles and their fragmented tails together;
  a legend-lookup-only repair is explicitly unsafe and remains deferred.

## Completed

- Gate-box v3 was reviewed, landed, and pushed as `5ab33ff`. Only its reviewed
  finder source and regression test were published.

## Historical / shelved

- [Wrong-value sweep](wrong-value.md) is the historical 23-item input audit.  Its
  fixes are tracked by the frozen class/retro packets; do not use it as an active
  queue.
- [Auto-acceptance oracle](auto-acceptance-oracle.md) and
  [agent orchestration](agent-orchestration.md) are shelved design studies.  They
  remain only for their guard and failure-mode analysis.

## Shared review contract

[CHART-REVIEW-CHECKLIST.md](CHART-REVIEW-CHECKLIST.md) is byte-synchronized with
the review-backlog checklist.  Update both copies together and compare content
hashes; paths alone are not synchronization evidence.

Each random-PDF iteration has a frozen scope and a terminal packet. New defects
discovered during that iteration become separately tracked future work unless
they are caused by the candidate itself. Findings do not recursively expand the
frozen run that found them. A subsequent seed begins only on Fab's explicit instruction.

Lifecycle is explicit: **scoped** -> **frozen** -> **patch-GREEN** -> **item-GREEN** ->
**human-GREEN/landed**. Patch-GREEN certifies only causal code behavior; it does not
upgrade a refused or defective item. Agents never advance a worklist merely because an
earlier one ended, and they never set `human_verified`.
