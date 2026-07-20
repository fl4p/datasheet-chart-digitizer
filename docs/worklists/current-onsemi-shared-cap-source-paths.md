# Onsemi shared-endpoint capacitance source paths

Status: independent patch review GREEN; all rescued items remain held without
physical values. `human_verified=false`; no commit or push.

## Defect

Onsemi capacitance charts such as `NVMFS5C460NLWFT3G` encode Ciss, Coss, and
Crss as three complete black PDF drawings. Ciss and Coss share an exact source
endpoint, so color and pooled endpoint chaining merge the drawings and report
only two vector candidates. Raster fallback may then ride grid lines or refuse
an otherwise source-proven chart.

This source-drawing endpoint rescue is distinct from
[unresolved Ciss/Coss shared collapse](current-capacitance-unresolved-shared-collapse.md):
that later slice concerns an already emitted Coss trace joining Ciss away from
the low-V edge and never proving re-separation. Do not treat the source-drawing
rescue or its physical-output hold as fixing that trace-validation defect.

## Bounded rescue contract

- Run the established per-color and pooled component paths first.
- Inspect individual source drawings only when both paths produce fewer than
  three candidates.
- A drawing contributes only when it contains exactly one complete, full-span,
  materially non-horizontal curve candidate. An ambiguous drawing contributes
  none.
- Accept the rescue only when exactly three distinct drawings prove exactly
  three candidates. Never synthesize or split a missing curve.
- Record `vector_selection_method=source_drawing_rescue` as serialized
  provenance.

## Physical-output hold

The rescue proves trace geometry, not axis calibration. Independent corpus
review found inverse-fit x markers displaced roughly 1--8 px from printed grid
centers, including negative VDS values at the plotted 0 V frame. Therefore
every rescued panel is `overlay-review-required`, all calibrated C(V) columns
and Qoss/reference values are withheld, and the result carries
`source_drawing_rescue_axis_center_review_required`. Physical output stays
disabled until [capacitance tick centers](cap-axis-tick-centers.md) supplies an
exact served-calibration contract.

This hold also contains the known `SIL03N10-TP` false classification: its output
curves can satisfy the structural rescue, but it remains nonphysical with blank
values. The classifier fix is a separate detector slice.

## Frozen corpus and oracle

Use the hash-locked 800-panel input set at
`dsdig-verify-backlog/agent-sweep-reports/fixes/cap-qoss-clip-contract/v1/inputs/frozen-inputs.json`
with `tools/run_capacitance_collateral.py`. The causal A/B must use identical
current source bytes on both sides except for the isolated rescue block, then
run candidate and repeat sequentially under the same OCR environment.

Acceptance requires:

- candidate equals repeat physically and no OCR/axis drift appears;
- zero new physical-output promotions;
- every rescued trace is source-ink seated at origin, crossings, interior, and
  tail, with no branch switch or grid ride;
- the one-source-object predicate and ambiguous-drawing negative remain pinned;
- `SIL03N10-TP` remains value-withheld;
- all unrelated rows are physically unchanged.

Frozen v3: `/private/tmp/dsdig-cap-source-path-v3`. Candidate and repeat
manifest SHA-256 are both
`2de1ab75fcd0622ed94f1b28857b553d767cadc5a99f55a4174c39ddeb6e98a7`;
all 1,170 non-metadata artifacts are byte-identical. The independent review is
`full-candidate/reviews/agent-cap-source-path-v3-001.hxy-transfer-ab-review.json`
(SHA-256
`3129892a78cdfae7d464305fedeffffcff5a2c513fff42bf357696806ecfe39b`).
It verifies 71 rescues, 133,840 source-ink distance checks (p95 0 px, max 1
px), 47 microscopic crossing checks, blank calibrated columns/references on
all rescues, zero promotions, and only three intentional physical demotions.
The 70 real capacitance items remain UNVERIFIED solely because their axes are
held; `SIL03N10-TP` remains a safely null wrong-panel detection.

The later ST thin-stroke slice changed the shared helper signature to expose a
keyword-only `min_stroke_width=0.8`; capacitance still omits that keyword. This
invalidated the source hash without changing the cap path, so the exact packet
was re-frozen as `/private/tmp/dsdig-cap-source-path-v4`. Independent review
confirmed that v3 candidate, v4 candidate, and v4 repeat each contain the same
1,170 non-metadata artifacts with zero byte differences (canonical set SHA-256
`4a76b29ef1d7d4f8bbd1b31ca4f2c572d6d677bc7c9ab282d19e1b72c4e7bdb7`).
The only source-closure change is `capacitance_vector.py`, from SHA-256
`668964e111aabada5943e435ad0caab37ad9e6eea62a944d397ddff7ae622fa4`
to `4d3900519c43e815674752c6093ce531045af31e40b2eeee8751fb74e95b3e30`;
all three capacitance call sites retain the 0.8 pt default. Review:
`/private/tmp/dsdig-cap-source-path-v4/reviews/agent-cap-source-path-v4-001.hxy-transfer-ab-review.json`
(SHA-256
`a704b1e81ae16596964b9ea027e7636b6deec508af63affa171366328fee8867`).
V3's strict trace/physical-output verdict carries unchanged under exact output
identity; `human_verified=false`.

## Landing dependency

The rescue is unsafe if separated from its provenance hold. A landing packet
must include, as one reviewed dependency set:

- `src/datasheet_chart_digitizer/capacitance_vector.py`;
- `src/datasheet_chart_digitizer/mosfet_capacitance.py`;
- `src/datasheet_chart_digitizer/capacitance_axis.py` endpoint-coverage guard;
- `tests/test_capacitance_source_path_rescue.py`;
- focused endpoint/ambiguous-drawing tests in `tests/test_mosfet_capacitance.py`.

The current slice does not authorize `human_verified`, commit, push, or physical
value publication.
