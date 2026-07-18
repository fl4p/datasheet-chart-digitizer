# Ciss/Coss/Crss table-anchor parser worklist

**Status:** v1 candidate frozen for review in an isolated worktree; not landed. Review packet:
`cap-anchor-parser/v1/packet.json`, SHA-256
`83ba31f570264911ccf7eaec59c86c1f731b051990a15aed7406466d19a07b1d`. The Qoss worktree and
all earlier gate-charge fixes remain immutable. No commit/push; agents never set
`human_verified`.

## 1. Defect and measured scope

`capacitance_refs.parse_capacitance_anchors` finds `Ciss`/`Coss`/`Crss`, then calls
`_first_number_before_unit` on every cell after the symbol. On many datasheets the first number
belongs to a test condition such as `VDS=50 V`, not the capacitance value column.

Full audit artifact: `/private/tmp/cap-anchor-selected-audit-v4.json`, SHA-256
`1669657c51a98562f5688babe355426f7bdcdbc9eb95811da86533d9a0be3964`.

- 10,225 table CSVs, 0 exceptions.
- 7,327 files / 34,534 anchor rows differ between the legacy first-number helper and the
  evidenced-column parser.
- Ciss: 12,014; Coss: 11,152; Crss: 11,368.

The delta is deliberately split by evidence quality:

| Symbol | Evidenced numeric value | Proven condition capture, column unresolved | No VDS and column unresolved | VDS present, legacy preserved |
|---|---:|---:|---:|---:|
| Ciss | 8,055 | 336 | 3,495 | 128 |
| Coss | 1,314 | 198 | 8,402 | 1,238 |
| Crss | 665 | 585 | 10,044 | 74 |

The last two strata are **not safe to null automatically**. About 21,941 rows have a plausible
legacy number but no VDS/column evidence; blanket fail-closing would destroy high-coverage,
likely-correct anchors. Absence of column evidence is not evidence that the value is wrong.

### Served-anchor scope refinement

The 37,557 figure above is a row-level discovery count, **not** the production blast radius.
`parse_capacitance_anchors` currently emits only rows with a recognized `VDS=...V` condition and
keeps one selected anchor per symbol. A second audit therefore models the served selection,
explicit candidate precedence, and conflicts:

- Audit: `/private/tmp/cap-anchor-selected-audit-v4.json`, SHA-256
  `1669657c51a98562f5688babe355426f7bdcdbc9eb95811da86533d9a0be3964`.
- Tool: `dsdig-verify-backlog/tools/audit_capacitance_anchor_columns.py`, SHA-256
  `40294387b6e34c73c450f7a6748227b6405cbb41169a0f570b89867b01a87a5b`.
- 10,225 files, 0 exceptions.
- 6,014 selected anchors across 5,094 files would change: Ciss 4,682; Coss 938; Crss 394.
- 5,512 become numeric: 5,049 correct an existing anchor and 463 explicitly recover a signed
  P-channel VDS row that production currently ignores. Another 180 become null because strongest
  candidates conflict and 322 become null because every candidate is provably condition-owned.
- Of the existing-anchor numeric corrections, 4,963 legacy values equal VDS; another 86 capture
  some other condition or wrong column (for example `F=1 MHz` becoming a false 1 pF Ciss anchor).
- Exact-token refusals observed: `VDS` 558 rows, frequency 545, plus a small number of ID/Tj and
  OCR-spelled VDS assignments. Numerical equality alone is never used as ownership proof.

The legacy and proposed VDS parsers are deliberately modeled separately. Current production
accepts only unsigned `VDS=+N V`; the proposed path accepts signed P-channel conditions and stores
their magnitude. Treat those 463 from-null anchors as a visible new-output class, not as corrections
to a baseline value that never existed. Their chart assignments receive the same full §9 review.

The selected-output audit, not the row-level discovery count, is the §9 expected-delta oracle.
It is still a design audit rather than an implementation result; production code remains
unchanged until the Qoss packet settles.

Current review-backlog intersection, mapped through the exact production anchor CSV rather than
the backlog suffix (not a substitute for the authoritative production run):

- Artifact: `/private/tmp/cap-anchor-digitized-scope-v4.json`, SHA-256
  `af6250ba6092d622f064014b2d730a420786882cc492e7629b39c4d1d532e023`.
- Tool: `dsdig-verify-backlog/tools/scope_capacitance_anchor_changes.py`, SHA-256
  `a04e41f847631506a1f55145eb7e2cc5c2024003be239e0603738ff4cbfdea64`.
- 586 digitized capacitance parts exist in `dsdig-verify-backlog`; 165 exact source-PDF rows / 183
  selected anchors intersect the proposed delta set.
- Symbols: Ciss 164, Coss 19, Crss 0. Kinds: 167 numeric corrections, 7 strongest-candidate
  conflicts to null, 9 proven-condition-only/no-candidate anchors to null. Seven numeric outputs
  are signed P-channel anchors recovered from a true baseline null.
- The four additional rows versus the legacy logical-part scope are transformed PDFs that consume
  an affected base production table: MCAC100N10YHE3-TP.cups (Ciss+Coss), FDB047N10.gs,
  FDP047N10.gs, and FDS86267P.gs (Ciss). They must not be lost to suffix aliasing.
- Refusal visibility does not expand this frozen scope: 27 of the 165 affected exact-PDF rows
  consume a table with a proven condition-owned token, and zero of the 421 negative rows do.
  Thus every refusal/conflict diagnostic belongs to an already-affected PDF; there is no reason
  to add boilerplate diagnostics to byte-identity negatives merely to expose the real refusals.
- The other 421 backlog rows are byte-identity negatives. Section 9 still re-extracts the full
  authoritative capacitance corpus; this intersection only bounds the existing overlay-review
  queue after the causal A/B is frozen.

The exact 586-source PDF list is now resolved with no missing files, including the production
finder/table identities required by C14:

- Corpus manifest `/private/tmp/capacitance-corpus-v2.json`, SHA-256
  `cf2480598a3f752a28094a36013bccea412025e1c84908c32a01d6c92e4b5b2a`.
- PDF list `/private/tmp/capacitance-corpus-v2.txt`, SHA-256
  `47b2fc9e232bb5768393d4aea917a53d0adcd8e4d1d2bc3051419c36a4eab222`.
- 586 resolved paths / 0 missing: 564 native, 14 Ghostscript, 5 CUPS, 3 unicoded.
- 558 unique content hashes. Keep all 586 exact part/variant rows; byte-identical source PDFs are
  not corruption and must not be content-deduplicated because each path has distinct review and
  provenance identity.
- 10 base finder identities collide between a native and transformed path. The manifest carries
  exact logical part, finder part, PDF path/hash, production anchor-CSV path/hash, and transformed
  source-text CSV path/hash so those rows cannot be conflated in the acceptance report.
- Production anchor CSVs exist for 296/586 rows and for 18/22 transformed rows. The four honest
  missing cases are STP120NF10.gs under AO plus three HXY unicoded PDFs; the harness preserves those
  no-anchor inputs on both sides rather than substituting a transformed OCR CSV.

The one-time production finder input set is frozen and complete:

- Chart index `/private/tmp/cap-anchor-frozen-index-v1/charts.json`, SHA-256
  `3ccf98f2b359ee7c0011ef9db6e0f38af664f0dedfdcdc217940ec3b63e470b3`.
- Finder source SHA-256 `c307757f551353763a75febcf6b7b9280f26c3803244d0730a29b75ab7625a57`;
  command used the 586-path PDF list at 220 DPI with `OMP_THREAD_LIMIT=1`.
- 2,761 total panels, including 800 capacitance panels. All 586 PDFs have at least one capacitance
  panel; hard scan errors 0. Frozen crop/input manifest
  `/private/tmp/cap-anchor-frozen-inputs-v2.json`, SHA-256
  `69e8fe137a455d67477fa019fbb0ea770b8855ebeab91fd1368b8bfb615d4602`,
  crop-set SHA-256 `3675c9e75730c838b32be8b757c5c2f2bdc3fcfe835401ea8057729a43967df1`.
  The canonical crop record binds exact crop bytes, page/diagram, panel/crop bboxes, and 220-DPI
  render settings; paths alone are not treated as provenance.
- Exact panel scope `/private/tmp/cap-anchor-frozen-scope-v4.json`, SHA-256
  `653403f4d481a82882378ce38159af5a6e3c322b9ceeb003e7bd6a428686d90f`:
  203 affected panels across 165 PDFs and 597 byte-identity-negative panels across 421 PDFs.
  PSMNR70-30YLH and PSMN2R4-30YLD are affected crossing gates; PSMN5R3-25MLD and
  PSMN6R1-25MLD are frozen negative crossing gates.
- Hash-verifying runner `dsdig-verify-backlog/tools/run_capacitance_collateral.py`, SHA-256
  `09b8787d6e5316f756d55f71b3e1d6b7586dc588e2b590ff9481f9f884e17590`.
  It verifies the chart index, every crop, every source PDF, and every production anchor table
  before invoking the real CLI from a pinned isolated worktree.
- Manifest comparator `dsdig-verify-backlog/tools/compare_capacitance_collateral.py`, SHA-256
  `924dad40a30b89b1dd67c1be52bc1a6072466dfbe02f0f83dc3f3124346bad31`.
  It requires both runs to account for the exact same 800 crop keys, hashes raw point/overlay/axis
  artifacts, compares their normalized manifest paths independently from their content, verifies
  affected membership by exact source-PDF path **and content hash**, and diffs every selected-result
  field. A scope/frozen hash mismatch fails before comparison; a change in any of the 597 negative
  panels is a hard nonzero result rather than an assumed-neutral scalar delta.
- The sequential baseline and its independent repeat are byte-reproducible on the immutable
  Qoss-v1 source: 455 selected results plus 345 explicit extraction errors account for all 800
  frozen panels; result manifest SHA-256 `4f796b86844f7027c10def50ce9986b58cf852e4cd90dcbddd8a2d6a3467bbcc`
  and error manifest SHA-256 `3836fd0f9153cec3be11f76978a194cfa4d6fa2a12254be8c9bfd33f7f0c5a71`
  match exactly across both runs. The artifact-level repeat comparison
  `/private/tmp/cap-anchor-baseline-repeat-compare.json`, SHA-256
  `01f01fb48470fd6b05b19cbb8c13600f969d963d356a3e3658ac1a20db85693a`, reports zero
  manifest, raw-point, overlay, or axis-debug deltas. If Qoss-v1 changes during review, discard
  this baseline and refreeze it from the revised source rather than carrying it forward.
  Frozen baseline record: `cap-anchor-parser/baseline-v1.json`, SHA-256
  `410ccfb30c1b2674c74516b4fb91247017f1b4c50c75fd9fcfb38bd0a4967bad`.
  The baseline partition is 135 affected results + 68 affected errors and 320 negative results +
  277 negative errors; candidate acceptance must preserve full accounting across result/error
  transitions, not compare only the 455 successful selections.

## 2. Required fix direction

Use a preservation-first, evidence-tiered parser:

1. If the table header and row structure identify a value/typ column, use that evidenced value.
2. If the legacy number can be localized to a condition cell (for example, the exact token inside
   `VDS=50 V`) and no value column is recoverable, return no anchor for that row.
3. Otherwise preserve the legacy anchor. Equality `legacy == VDS` alone is only an audit signal,
   not proof: the real capacitance may coincidentally equal the condition number.
4. Persist parsing provenance per anchor (`evidenced_value_column`,
   `proven_condition_capture_refused`, or `legacy_preserved_no_column_evidence`).
5. Select candidates explicitly: evidenced value-column candidates outrank preserved legacy
   candidates; a refused condition row contributes no candidate; conflicting strongest
   candidates fail closed. Never retain the current accidental "last matching CSV row wins"
   behavior.

Do not share the Qoss rule “ambiguity => null” blindly. Qoss is a sparse structured table
reference; Ciss/Coss/Crss anchors are broad trace-assignment hints with much looser source layout.

## 3. Load-bearing challenges

- **C1 — prove token ownership.** A refusal must identify the exact cell and token from which the
  legacy number came. A numerical equality check is insufficient.
- **C2 — no blanket nulling.** The 22,294 no-VDS/unresolved rows must remain byte-identical unless
  a separate source-backed defect is demonstrated.
- **C3 — coincidence negative.** A row where the real capacitance equals VDS must still parse the
  capacitance from its distinct value column; the condition audit must not erase it.
- **C4 — trace assignment is the output gate.** Anchor changes may alter Ciss/Coss/Crss identity,
  crossings, shared-collapse spans, and curve points. Every changed chart needs source-overlay
  review, including the mandatory microscopic intersection check.
- **C5 — preserve known fixed crossings.** PSMN5R3-25MLD, PSMN6R1-25MLD,
  PSMNR70-30YLH, and PSMN2R4-30YLD are regression fixtures. Their post-fix points/identity and
  no-neighbor-snap behavior must not regress. The exact frozen production PSMN5R3 crop differs
  from the specialized Class-C v5 crop and is an extraction error in both reproducible baselines;
  for this A/B its enforceable invariant is the identical error plus unchanged external v5 packet,
  not a claim that the frozen crop produced new points.
- **C6 — graph/label arbiter stays independent.** Printed curve labels, right-tail ordering,
  flatness, and graph integration decide identity. Do not mute an identity/rank-swap diagnostic
  merely because a table anchor changed.
- **C7 — selected output, not row count.** The acceptance delta is the final selected anchor per
  file/symbol. Repeated tables and duplicate rows must not multiply the claimed blast radius.
- **C8 — deterministic candidate precedence.** Reordering duplicate CSV rows must not change the
  selected anchor. Conflicting equal-strength candidates return no anchor with explicit
  provenance; a later low-confidence row must not overwrite an earlier evidenced value. Refusal
  and conflict diagnostics must canonicalize duplicate source rows as well—do not make diagnostic
  identity depend on whichever duplicate happened to appear last.
- **C9 — all condition tokens.** Token-ownership logic covers VDS, VGS, ID, frequency, and
  temperature assignments by exact source span. A VDS-only guard would leave the observed
  `F=1 MHz -> 1 pF` failure class alive.
- **C10 — refusals stay visible.** `CapAnchor` cannot carry provenance when no anchor is emitted.
  Add `anchor_parse_diagnostics`, a structured per-symbol parse diagnostic alongside the existing
  anchor dictionary (canonical source-row cells/token, evidence tier, refusal/conflict reason,
  and raw/distinct candidate counts). Do not silently drop a refused symbol or encode a refusal
  as a fake zero-valued anchor. Exact duplicate source rows must deduplicate by content rather than
  expose unstable row numbers. Emit this event provenance for selected corrections, from-null
  recoveries, refusals, and conflicts; do not attach a new empty/boilerplate field to every
  unchanged chart. Keep compatibility for consumers that only need the accepted anchor dictionary.
- **C11 — table anchors feed curve identity.** `select_trace_assignment` scores semantic
  Ciss/Coss/Crss permutations against these anchors using log residuals. The load-bearing §9
  output is therefore the selected curve identity, raw points, and crossing behavior, not merely
  the corrected table scalar. Any mandatory crossing chart whose anchor or assignment changes
  requires Fab to re-verify the microscopic intersection artifact; prior human verification was
  against the old anchor inputs and does not automatically carry.
- **C12 — do not silently add cross-row condition inheritance.** The current production parser
  requires VDS in the same row as the symbol. For example, the selected-output audit changes only
  Ciss on PSMNR70-30YLH; its Coss/Crss row-level corrections have no row-local VDS and are not
  currently emitted. Propagating a grouped table's VDS into later rows would create a separate
  from-null anchor set and may unlock two-anchor reassignment. Keep it out of this slice unless it
  receives its own explicit audit, fixtures, expected-delta set, and crossing review.
- **C13 — full chart harness before implementation acceptance.** The existing
  `tools/regression/run_capacitance_regression.py` covers only 39 focused charts and cannot bound
  a 161-part changed-overlay set. Build/freeze the exact production-generated capacitance chart
  index and crop inputs for the full authoritative PDF list once, then run baseline and candidate
  digitizers sequentially against that identical index/crop set in isolated same-environment
  worktrees. Do not regenerate finder selections independently per side or substitute the 39-chart
  focused regression suite for the full causal A/B.
- **C14 — preserve production identity while disambiguating transformed PDFs.** Production
  `find_charts` names `.gs`/`.cups` panels with the base PDF part and the anchor parser therefore
  looks up the base `<finder_part>.pdf.nop.csv`; this is intentional current behavior and must be
  identical on both A/B sides. Do not rewrite the chart `part` to a backlog suffix or silently
  substitute the transformed PDF's `.gs.csv`/`.cups.csv`, because either would change table inputs
  in addition to the parser. Instead key the frozen harness and all verdicts by exact source-PDF
  path/hash plus crop path/content hash, page/diagram, bbox, and render settings; carry the backlog
  logical part as external provenance. Record every base-part collision, the canonical crop-set
  hash, and both the production anchor CSV and transformed source-text CSV hashes.
  A later variant-table-binding change needs its own audit, fixtures, expected-delta set, and §9
  run; it is out of scope here.
- **C15 — signed P-channel recovery is an explicit output class.** Baseline production's unsigned
  VDS regex emits no anchor for `VDS=-N V`; the proposed parser may bind the evidenced table value
  at `abs(VDS)` but must report it as `from_null_signed_vds`, not disguise it as a legacy-value
  correction. The audit oracle contains 463 such selected anchors corpus-wide and seven in the
  frozen backlog scope. Review their curve assignments for over-fire just like any other newly
  served input; a signed condition without value-column evidence still yields no new anchor.

## 4. Required fixtures

Positive fixtures:

- One clear condition-as-Ciss capture and one each for Coss/Crss.
- One evidenced min/typ/max table and one OCR-collapsed value cell.
- At least one affected chart per vendor family in the changed chart corpus. Seed examples from
  the frozen backlog intersection: AO3422 (25->214 pF), NVMFS5C673NLAFT1G-HXY (25->930),
  MCAC80N10Y-TP (50->3375), BUK753R8-80E,127 (25->9020), FDB0170N607L (30->13750),
  RD3P07BBHTL1 (50->2410), TJ60S06M3L (1 MHz capture 1->7760), IRFP150 (25->2800), and
  DMTH83M2SPSWQ-13 as the Diodes fail-closed/refusal fixture.

Negative fixtures:

- Real value numerically equal to VDS in a distinct value column.
- P-channel or frequency-bearing row where the legacy helper skips a negative VDS and captures
  `F=1 MHz`; the evidenced pF value must win.
- No-header row with one plausible legacy value and no condition token: preserve it.
- A strong inner/neighbor number that is not the value column: refuse it.
- Duplicate-row permutation and conflicting-strongest-candidate fixtures, proving selection is
  order-independent and conflicts fail closed.
- Known fixed crossing charts listed in C5, with point hashes and 5x/8x intersection crops.
- PSMN5R3-25MLD specifically: the frozen production crop SHA-256
  `3aa90ddf0cc1bcf8077eab13b5f972fe888050f80d50d2beb75e7075ba6d8adb` must remain the same
  extraction error, while the distinct superscript-corrected raw-points SHA-256
  `c3c419524e8ef74908952a86ab7f73b6dc8891f48087b1c4432ee598a865ad82` and Class-C v5
  intersection SHA-256 `dd16ef2fa298de986c3b9741da9698ea8bd526e213fb92016f564e1e0c845523`
  remain external no-anchor golden artifacts. Do not substitute the older Class-C points CSV,
  whose VDS values predate the `10^2` superscript-axis correction, or compare either specialized
  crop geometry as if it were the frozen causal A/B input.
- Each crossing has its own golden; hashes are not interchangeable:

  | Part | Superscript-corrected points SHA-256 | Microscopic intersection SHA-256 |
  |---|---|---|
  | PSMN2R4-30YLD | `74d55ad527aeb58e7674681cef4d9fddf71c3f5c1a7e11ba49f24d7f7cad737e` | `ea85d2ca98a1921482bafba3225b2a2223de56880e1c7f408258badfac186741` |
  | PSMN5R3-25MLD | `c3c419524e8ef74908952a86ab7f73b6dc8891f48087b1c4432ee598a865ad82` | `dd16ef2fa298de986c3b9741da9698ea8bd526e213fb92016f564e1e0c845523` |
  | PSMN6R1-25MLD | `d6f9de69decfc12de7217126d01866dc1030924b520403adf3d6dab34c96091f` | `df12d6fbe92ea321b822df56306ae4aa05f0397b316d2a5484ea7d8b550c3add` |
  | PSMNR70-30YLH | `d9cabd89091dfcaa55fb167f147e018203c484705fa18caba2fabcfd08505df8` | `386fdfd733852cf2ea7848ea3ae17501203dd3c620c418f70758153456924f90` |

- PSMNR70-30YLH and PSMN2R4-30YLD selected-anchor deltas with human re-verification if any
  assignment/point/intersection artifact moves. PSMN5R3-25MLD and PSMN6R1-25MLD currently have
  no `.nop.csv` anchor table and are byte-identical negative fixtures. Do not conflate them with
  the distinct table-backed PSMN2R4-30MLD and PSMN6R1-30YLD parts.

## 5. Section 9 acceptance

This changes trace-assignment inputs, so acceptance requires the authoritative full capacitance
chart corpus, same-host/sequential A/B:

- Freeze the pre-change row audit **and** selected-anchor audit; use the latter as the exact
  high-confidence expected-delta set.
- Record the authoritative PDF-list SHA, frozen production chart-index SHA, crop-set hash
  manifest, chart count, duplicate part/figure policy, transformed-PDF alias/collision map,
  production anchor-CSV hashes, and finder no-result/exception counts.
- Re-extract every affected capacitance chart with the production selector.
- Diff anchors, provenance, selected trace identities, curve points, shared-collapse spans,
  per-symbol anchor-parse diagnostics, status/diagnostics, axes, and plot boxes.
- Every curve/identity/point delta receives checklist sections 0–7 review; every crossing gets the
  mandatory microscopic point-fidelity inspection.
- Zero unexplained changes in the legacy-preserved strata; zero contract leaks; exact exception
  and no-result counts.
- Dual independent agent GREEN, then Fab's human gate. Agents never set `human_verified`.
