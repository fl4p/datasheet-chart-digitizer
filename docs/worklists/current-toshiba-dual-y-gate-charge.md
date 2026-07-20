# Toshiba dual-Y gate-charge raster-axis calibration

**Status:** v1--v4 rejected as integrated item packets; source-level v5
candidate frozen. Independent microscopic v5 review, authoritative full-corpus
gate-charge A/B, and human scale review remain. No commit/push and
`human_verified=false`.

## Defect

Five sampled Toshiba `Dynamic Input/Output Characteristics` panels now have
finder-proven owned crops but previously refused or assumed a wrong scale. These charts
use total gate charge Qg on X, drain-source voltage VDS on the left Y axis, and
gate-source voltage VGS on the right Y axis. The PDF stores tick numbers and
axis labels as vector outlines, so text extraction sees no Qg/nC/VGS tokens and
no numeric tick run.

The focused set is TK25S06N1L, TJ40S04M3L, TPH3R70APL1,LQ, TPN2R903PL,
and TPHR8504PL1, each page 6 diagram 810. TJ40S04M3L is a signed P-channel chart; its right VGS axis is
negative, so a positive 0..16 template assumption would be a wrong-value bug.

TPH3R70APL1,LQ and TPN2R903PL entered the focused set only after the separate
finder-v3 packet proved that their diagram 810 crops moved from lower-right
Qoss to the owned middle-right Qg frame, with full dual-axis gutters and no
panel addition/removal.

## Guarded design

- Keep the existing text-tick path authoritative. Invoke raster OCR only when
  the owned bottom/right axis regions have no usable text tick evidence.
- OCR bottom Qg ticks/unit and right VGS ticks/unit inside the panel crop. Do
  not infer a physical range from grid count alone.
- On a dual-Y chart, bind Vpl to the right VGS axis. The left VDS scale and
  falling VDS curve must never calibrate the gate-voltage plateau.
- Preserve signed P-channel tick sequences and plateau polarity. Do not take an
  absolute value merely to fit an N-channel range template.
- Require a coherent monotonic tick sequence, unit identity, and pixel/grid
  agreement. Otherwise preserve the current explicit refusal; never fall back
  to assumed 0..10 physical output.
- Share the bounded page-region OCR helper with capacitance-axis OCR rather
  than duplicate Tesseract rendering/TSV parsing.
- When a falling VDS curve crosses the Miller plateau, repair a raster branch
  excursion only if a narrow, materially tall excursion returns to two
  source-equal plateau endpoints. This is scoped to the exact diagram-810 OCR
  fallback; a real rising VGS span cannot satisfy the equal-end proof.
- Treat the full Qg(VGS) curve as the deliverable. For the exact bounded
  dual-Y OCR retry, admit a lighter-stroke raster candidate only when it starts
  at the owned Qg=0, VGS=0 axis origin. If no candidate proves that origin,
  fail closed; a plausible Vpl scalar cannot launder a VDS-branch trace.

## Acceptance

- All five focused panels resolve their printed Qg and right-axis VGS
  scales, including TJ40S04M3L's negative VGS scale, with source-seated VGS
  plateau curves and no branch switch.
- The extractor packet depends explicitly on the agent-GREEN finder-v3 packet;
  it must never be reviewed against the earlier Qoss-misbound crops.
- Single-Y text-backed gate-charge charts remain byte-identical. OCR is a
  missing-text fallback, not a competing calibration path.
- Freeze OCR inputs/settings and candidate/repeat artifacts; verify tick-center
  labels and Vpl microscopically on every changed item.
- Run the authoritative full gate-charge corpus A/B, inspect every status,
  curve, Vpl, box, and overlay delta, and obtain independent review. Never set
  `human_verified` from an agent lane.

## Focused evidence

- Packet: `/private/tmp/dsdig-toshiba-dual-y-qg-v1`.
- Same-source baseline disables only the bounded diagram-810 dual-Y OCR retry.
  Baseline statuses/values were wrong or non-physical-output: TK25 1.86 V
  `axis_grid_inferred`; TJ40 +4.39 V `low_confidence` (wrong polarity);
  TPH3 2.14 V, TPN2 2.60 V, and TPHR 1.61 V `unresolved`.
- Candidate results are all `ok`, `x_tick_unit=nC`, and carry the explicit
  `axis_ocr_bounded_dual_y` diagnostic: TK25 3.75 V; TJ40 -3.86 V; TPH3
  3.97 V; TPN2 3.07 V; TPHR 3.18 V.
- Axis OCR and right-VGS calibration are correct on all five panels. TJ40
  preserves the negative right-axis scale.
- Initial TPH3 output was visually RED: the trace briefly climbed the falling
  VDS curve at its plateau crossing and biased Vpl to 4.24 V. The guarded
  endpoint-equal repair removes that source-absent spike and yields the printed
  ~4.0 V plateau (`Vpl=3.97 V`).
- Candidate/repeat overlay PNGs and contact sheets are byte-identical. Their
  JSON differs only in the expected absolute `overlay` output-directory field;
  all physical fields are identical.
- Focused tests: 58 passed, 17 skipped, 4 subtests; five real Toshiba cases
  pass as five subtests. The capacitance suite has 59 passes plus one known
  concurrent finder-bbox failure in the TK100E10N1 end-to-end test; the shared
  OCR helper itself does not touch finder geometry.
- Strict review supersedes the earlier normal-scale agent GREEN:
  `/private/tmp/dsdig-toshiba-dual-y-qg-v1/reviews/agent-toshiba-dual-y-qg-v1-001.codex-ee-hxy-review.json`
  (SHA-256 `a9ffa9605e4a9b046a5fbf6d49a3397359e0360aedb29dcd02d6969e897a3a6c`).
  TK25 is full-curve GREEN. TPH3 and TPHR start on falling VDS branches instead
  of the VGS origin; TJ40 and TPN2 switch branches in their terminal tails.
  Therefore the five-panel item packet is RED even though every Vpl scalar is
  plausible and every axis is correctly calibrated.

## Corrected v2 evidence

- Packet: `/private/tmp/dsdig-toshiba-dual-y-qg-v2`.
- Candidate/repeat canonical physical SHA-256 is
  `cb5a3648f1c770168a01b43a72d7831a08e253ba4903ddcf1e9919535a0190c8`;
  all five overlay PNGs and the contact sheet are byte-identical across the two
  runs.
- A bounded light-ink trace candidate is evaluated only for the exact diagram
  810 OCR retry. The established raster threshold remains the default and all
  non-Toshiba paths retain the old selection path.
- Every selected curve now proves the Qg=0, VGS=0 origin and is physically
  nondecreasing in VGS. Origin bottom gaps are 3--6 px across 416--437 px plot
  heights; no selected curve has a downward-voltage branch switch.
- Corrected values are TK25 3.75 V, TJ40 -3.91 V, TPH3 4.11 V, TPN2 3.07 V,
  and TPHR 3.23 V. TPH3 stops at the printed VGS endpoint rather than riding
  the horizontal gridline to the right frame.
- Focused origin/selection/monotonic/terminal tests pass (5 tests), and the
  real five-PDF regression passes all five subtests. A broader current-tree
  gate-charge run has 73 passes plus 13 failures caused by other in-flight
  finder/axis changes; those are not claimed as this slice's collateral oracle
  and must be resolved or same-host baseline-isolated before landing.
- Strict microscopic review rejected the integrated v2 packet:
  `/private/tmp/dsdig-toshiba-dual-y-qg-v2/reviews/agent-toshiba-dual-y-qg-v2-001.codex-hxy-review.json`
  (SHA-256 `29ff8665e3d6af32c737c9eec493d0c84326037d431676353a8805806aef1af6`).
  The monotonic fit manufactured source-absent horizontal segments on TJ40,
  TPN2, and TPHR. Therefore v2 is immutable RED despite correct axes, origins,
  and Vpl plateaus.

## Source-preserving v3 evidence (rejected)

- Packet: `/private/tmp/dsdig-toshiba-dual-y-qg-v3`.
- The monotonic fitter is removed from this path. It is replaced by a bounded
  terminal discontinuity gate: after the first owned VGS branch ends, a
  material downward-voltage jump may terminate the curve but may never be
  averaged into invented pixels. An interior notch is retained when the same
  source branch makes material later progress.
- Candidate/repeat canonical physical JSON SHA-256 is
  `97c66f107bb538c9e093484050140ea880dc76ca03a5e86600f587d6cd95e183`;
  the earlier `89f543...` value used a stale field-normalization and is
  superseded;
  every overlay and the contact sheet are byte-identical. Values are TK25
  3.75 V, TJ40 -3.91 V, TPH3 4.09 V, TPN2 3.07 V, and TPHR 3.23 V.
- TJ40 now stops at `(701,287)`, TPN2 at `(676,272)`, and TPHR at `(683,391)`,
  before their v2 source-absent horizontal joins. TPH3 retains the separate
  flat-grid termination guard and stops at `(678,377)`.
- Focused terminal/origin tests pass and the real five-PDF test passes all five
  subtests. Current source SHA-256 is
  `924ffd8300c19468b271f0a8b0c4c9a4d8fc2104102afb26e02727716c4cc0cf`;
  focused test SHA-256 is
  `e6376de0f269e6d2800c93570f9dc5e8ded56d1945527b0d6e33feb6f7707d59`.

- Strict microscopic review:
  `/private/tmp/dsdig-toshiba-dual-y-qg-v3/reviews/agent-toshiba-dual-y-qg-v3-001.codex-hxy-review.json`
  (SHA-256 `59e21345ed2dc9fa557a7f96eac8c505b3b3bd349e28082f26d21cc12565be6a`).
  Terminal trimming is GREEN at the four stated endpoints, but removing the
  v2 monotonic fit exposed 4--6 px source-absent VDS-crossing excursions on
  TJ40, TPH3, and TPHR. The integrated v3 packet is therefore RED; TK25 and
  TPN2 alone were item-GREEN in that packet.

## Bounded crossing-repair v4 evidence

- Packet: `/private/tmp/dsdig-toshiba-dual-y-qg-v4`.
- Candidate/repeat canonical physical JSON SHA-256 is
  `891c6f1c5ff977616eea8801cbd7275f5e3212174e938edc9c06126a16e965a4`.
  The JSON files differ only in absolute output paths. Every individual overlay
  PNG and the five-panel contact sheet is byte-identical between runs.
- The v3 terminal discontinuity trim is retained unchanged. A separate
  iterative crossing repair is admitted only on the exact dual-Y OCR path,
  before 55% of the plot width, for spans at most 10% of the plot width whose
  two endpoints are source-equal within the bounded tolerance. It repairs
  deviations in either pixel direction and may not flatten an actual rising
  VGS segment lacking equal endpoints.
- Candidate values/point counts are TK25 3.7477 V/114, TJ40 -3.7210 V/101,
  TPH3 4.0875 V/95, TPN2 3.0686 V/97, and TPHR 3.0621 V/97. The proven
  terminal endpoints remain TJ40 `(701,287)`, TPN2 `(676,272)`, TPHR
  `(683,391)`, and TPH3 `(678,377)`.
- Focused crossing, real-rising-segment, terminal-discontinuity, origin, and
  real five-PDF regressions pass (six focused tests plus five real-PDF
  subtests). Source SHA-256 is
  `793c9b310e809e13357bc3653905813af19b593c4e3f41718e1cb133ab8ba68a`;
  focused-test SHA-256 is
  `69598c664badfbd0c92bd45aac4fdd8ab51c0e7c4872988234a928c2f416b691`.

- Strict microscopic v4 review:
  `/private/tmp/dsdig-toshiba-dual-y-qg-v4/reviews/agent-toshiba-dual-y-qg-v4-001.codex-hxy-review.json`
  (SHA-256 `09d28c7e1b0e85dd72c2ef17228a1b682ece442efab28844d497d50dcc1c4526`).
  Reproducibility, axes, origins, terminal endpoints, TK25, TPH3, and TPN2 are
  GREEN. Integrated v4 is RED: TPHR retains a roughly 6 px source-absent snap
  onto its first falling VDS curve, while TJ40's local endpoint repair creates
  a wrong-level `y=528` shelf ahead of the printed `y=524` plateau and moves
  Vpl from -3.905 V to -3.721 V. The more permissive channel review is
  superseded on those two source-seating findings.

## Source-level plateau v5 evidence

- Packet: `/private/tmp/dsdig-toshiba-dual-y-qg-v5`.
- Candidate/repeat canonical physical JSON SHA-256 is
  `08aeaf5a1a839335084206f38d3685ae054ee3dc8fb888b56412fcef4d0f2fe6`;
  all five overlay PNGs and the contact sheet are byte-identical across runs.
- The v4 local endpoint search is replaced on the exact bounded Toshiba path.
  V5 proves one plateau level from the flattest source-supported window,
  extends it only through nearby same-level samples across thick VDS
  intersections, and repairs an isolated approach/exit outlier only by
  interpolation between source points on both sides. A continuously rising
  trace with no flat window is returned byte-for-byte unchanged.
- TJ40 now reaches the single printed `y=524` plateau at x=329, stays on that
  level through x=401, and exits on the printed rising branch; Vpl returns to
  the source-consistent -3.9053 V. TPHR's first approach is smooth
  `(359,547)->(363,544)->(367,542)` instead of snapping to y=538, while its
  printed `y=528` plateau and v3 terminal endpoint are retained; Vpl returns to
  3.2302 V.
- Candidate values are TK25 3.7477 V, TJ40 -3.9053 V, TPH3 4.0875 V, TPN2
  3.0686 V, and TPHR 3.2302 V. Proven terminal endpoints remain TJ40
  `(701,287)`, TPN2 `(676,272)`, TPHR `(683,391)`, and TPH3 `(678,377)`.
- Focused plateau-crossing, both-direction crossing, real-rising-segment,
  terminal-discontinuity, interior-notch, and real five-PDF tests pass (six
  tests plus five real-PDF subtests). Source SHA-256 is
  `740397bbc0d6988f65151de8d86a9227466b08cea1a9a433f08fa9eb60c6a6fd`;
  unchanged trace-helper SHA-256 is
  `7c084d4a1b8711863023be1b731753f747cfe4c4ccad8a75b10804b0f160aed1`;
  focused-test SHA-256 is
  `69598c664badfbd0c92bd45aac4fdd8ab51c0e7c4872988234a928c2f416b691`.

V5 is focused-item GREEN at agent level. Independent strict reviews are
`/private/tmp/dsdig-toshiba-dual-y-qg-v5/reviews/agent-toshiba-dual-y-qg-v5-001.codex-hxy-review.json`
(SHA-256 `4328abc1f4b59a1ff168de737091bcb6a1592b4d12ec9980806407004cf63342`)
and `/private/tmp/dsdig-toshiba-dual-y-qg-v5/reviews/opus-strict-v5-review.json`.
Both independently accepted the one-level TJ40 plateau, TPHR first approach,
all crossings, genuine plateau exits/rising diagonals, origins, and unchanged
terminal endpoints on all five. `human_verified` remains false. Single-Y byte
identity and the full-corpus gate-charge A/B remain mandatory before shared
acceptance or landing.
