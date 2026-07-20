# Capacitance left-edge trace coverage

Status: agent-GREEN on item, bounded mechanism, determinism, and authoritative
800-panel A/B; `human_verified=false`. The full repository suite is externally
blocked after the local datasheet library was replaced by Git-LFS pointer stubs
during verification. Two Batch 29 positives are human-flagged; the Batch 30
repeat is agent RED.

## Defect

A capacitance trace can begin materially inside the owned plot even though its
printed source stroke continues to the left axis. The current span guard may
still pass because the remaining trace covers enough columns. Physical values
then omit the highest-capacitance low-voltage segment without an explicit
trace-validation reason.

Frozen positives:

| Part | Panel | Pixel evidence | Review state |
|---|---:|---|---|
| Infineon `IPD30N06S2-23` | p6d11 | linear 0--30 V plot; all three vector traces start 23 px into a 560 px plot (`~0.041`) after the steep source rise | Fab human-FLAGGED |
| onsemi `FDB0190N807L` | p5d8 | log 0.1--80 V plot; Ciss starts 22 px into a 518 px plot (`~0.043`) while Coss/Crss reach the left edge | Fab human-FLAGGED |
| Infineon `IRFS3607TRLPBF` | p3d5 | log 1--100 V plot; all three raster traces start 80--83 px into a 414 px plot (`~0.19`) after uncovered black source stubs and labels | Batch 30 agent RED pending human review |

Authoritative evidence:

- `/Users/fab/dev/pv/ee/dsdig-verify-backlog/MANIFEST.opus-cap-batch29.jsonl`
- `/Users/fab/dev/pv/ee/dsdig-verify-backlog/MANIFEST.opus-cap-batch30.jsonl`
- the referenced raw crops, overlays, and `values.verify.json` files.

## Fail-closed contract

- Measure edge reach in source pixel space from trace points and the owned plot
  box. Do not derive it from `vds_V`: linear zero-valued axes and withheld
  physical axes made the old review probe return unknown.
- A uniform material gap means every trace begins more than the bounded left
  margin inside the plot. A differential gap means one trace begins materially
  later than two edge-reaching peers. Either condition is incomplete trace
  provenance.
- A two-of-three late-start pattern is not sufficient pixel-only evidence:
  Toshiba source charts can intentionally begin Ciss and Crss later than Coss.
  That pattern remains accepted unless a separate source-ink mechanism proves
  the missing segments.
- The minimal patch withholds physical capacitance and derived Qoss with an
  explicit reason. It does not extrapolate across labels, reconnect a steep
  branch, or invent a point at the frame.
- Recovery is a separate slice and requires source-owned ink beneath every
  restored point. A plausible monotone extension is not evidence.
- Right-end coverage, shared Ciss/Coss identity, flat-grid capture, and
  top-decade clipping remain independent gates.

## Required controls

- onsemi `NTMFS6D1N08HT1G` p5d7 and `NVMFS6H818NWFT1G` p4d7: linear axes,
  all three traces start at the plot edge, and the pixel-space result is clean.
- A small common rendering inset below the material threshold must remain
  accepted.
- A clean trace family with equal source-owned short right endpoints must not
  be confused with a left-start gap.
- Every newly suspect full-corpus panel requires source-vs-overlay inspection;
  no aggregate count is accepted as proof against over-fire.

## Acceptance

1. The three positives cannot retain `trace_validation_status=pass`; FDB and
   IRFS cannot retain consumable physical values.
2. Both linear-axis controls and the bounded small-inset unit control remain
   unchanged.
3. Candidate equals repeat for points, overlays, values, and annotated PDFs.
4. The authoritative 800-panel capacitance A/B enumerates every status/reason
   delta, and every new refusal is reviewed against the source crop.
5. Independent review binds the final source closure and records
   `human_verified=false`.

## Frozen candidate evidence

Bounded candidate/repeat packet:

- `/private/tmp/dsdig-cap-left-edge-final/{candidate,repeat}`;
- recursive tree hash (both):
  `7952dc6a1843e2e19a0bd0a330d51285d9ff7f3c0ea2050950c61576c925dfae`;
- IPD and IRFS refuse with `all_traces_left_edge_gap`; FDB refuses with
  `Ciss_peer_relative_late_x_start`;
- both linear-axis controls retain `trace_validation_status=pass` and their
  existing `overlay-review-required` state.

Authoritative same-host sequential A/B:

- frozen input: `/private/tmp/cap-anchor-frozen-inputs-v2.json`, SHA-256
  `69e8fe137a455d67477fa019fbb0ea770b8855ebeab91fd1368b8bfb615d4602`;
- runner: `dsdig-verify-backlog/tools/run_capacitance_collateral.py` at the
  source closure used by both sides, SHA-256
  `c900c0a43646dd0451fec5cf2ed52f990c551fb9049dc8d3bec4f89c9369c294`;
- baseline: `/private/tmp/dsdig-left-edge-full-baseline`, 459 rows / 341
  recorded errors, manifest
  `b399f878f5bed1a5f517c65988fd438e4df2083de18d966d782806aab8e73c8f`;
- candidate: `/private/tmp/dsdig-left-edge-full-candidate-v2`, 459 rows / 341
  recorded errors, manifest
  `0d32f693b93e01eb91cf23e865cf4fc8362c5a488cd0821c039b38f09f6b823a`;
- error manifests are byte-identical; 110 result rows gain only status-contract
  fields, with no axis, plot-box, trace, identity, or extraction-method delta;
- 19 rows newly move from trace-validation pass to suspect. Twelve already had
  physical output unavailable and visibly have a uniform owned-plot/trace
  left gap: `AOB2144L`, `AOB470L`, `AON6226`, `AON6276`, `AOT2610L`,
  `AOT2910L`, `AOTF240L`, `FDS6680A`, `FDS8447`, `FDS8638`,
  `NCES090P100T4`, and `NTMFS4926NET3G`;
- seven previously consumable Fairchild/onsemi rows visibly contain a black
  Ciss source stub before the extracted red trace and now correctly withhold
  physical values: `FDB0170N607L`, `FDB075N15A-F085`, `FDB86360-F085`,
  `FDB86366-F085`, `FDD86252`, `FDD86369-F085`, and `FDMS7660`;
- the seven changed point CSVs retain identical trace names, pixel points,
  normalized coordinates, and shared-collapse flags; only `vds_V` and
  `cap_pF` are blanked by the fail-closed state;
- all 459 overlays are byte-identical as a tree
  (`3d6c7fd00da3a551f8b103c52e76aa08c377a1a6f39e5f8a0996ac05bc89f982`),
  as are all axis-debug overlays
  (`4b016b97ff355d1898e92a49059e20f8513720919a67135048095ac98555a897`).

The first candidate also caught two source-authored Toshiba late-start traces;
that overreach was removed before the frozen `candidate-v2` run. The final rule
requires exactly one lagging trace for peer-relative refusal.

The final code closure passed 84 focused tests before an external process
replaced local source PDFs with 131-byte Git-LFS pointer text. A current
independent unit rerun passes 75 tests. The post-replacement full suite is not a
code verdict: 560 passed / 39 skipped / 72 failed / 46 errored, with the broad
unrelated failures rooted in `pymupdf.FileDataError: no objects found` while
opening pointer files such as `IPP040N08NF2S.pdf` and `STK295N10F8AG.pdf`.
Restoring the binary datasheet materialization is an external test-environment
gate; it does not change the frozen crop-based A/B above.

Independent review:

- `/private/tmp/dsdig-cap-left-edge-final/reviews/independent-review.json`;
- verdict `GREEN_AGENT_LEVEL`, `human_verified=false`;
- SHA-256
  `5998674a5bb2f92f02ee72d4925dcaf1390619d34832587f3ac4b672cfa9331b`;
- all 19 changed overlays were inspected at original resolution; the reviewer
  reproduced the bounded tree hash, corpus row contract, exact seven physical
  withdrawals, unchanged CSV geometry, and the TPH3R70 two-of-three no-fire.
