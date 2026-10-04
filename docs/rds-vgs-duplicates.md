# RDS(on)-versus-VGS duplicate panels

Implemented on `feat/rds-vgs`, base `daa61ac`, 2026-10-01. This is panel selection
inside `digitize_pdf`, after extraction and before the public manifest is built.
It never changes a curve, calibration, readout, status or validation verdict.

## Decision contract

A pair must refer to the same resolved PDF path. Sharing bytes or a drawing across
parts is insufficient: IRLB4132 and IRLB8743 have visual score 1.000000 and remain
separate. All pair checks have explicit `duplicate`, `distinct`, `conflict` or
`unevaluable` decisions and reasons. Only `duplicate` can suppress a panel.

The visual score is normalized correlation of ink in the calibrated plot boxes.
Exclude two source pixels at each edge to remove the frame, resample both boxes to
256 x 256, binarize grayscale below 180, and blur with sigma 1 common-size pixel.
The blur tolerates antialiasing and stroke-width differences caused by the printed
scale. Mean subtraction makes white background agreement insufficient. Blank,
solid, very small, out-of-image, undecodable and nonfinite inputs are unevaluable.
No translation search or image warping maximizes the score. Threshold: **0.86**.

Both panels must have usable calibration bound to the grid, matching linear/log10
models, tick values, units and calibrated frame ranges (within 0.05% of span).
Both must serve curves with explicit current, temperature and temperature kind;
curve counts and those identities must match one-to-one. Unknown labels, ambiguous
identities, fewer than five points, gaps, unusable curves, refusals and missing
readouts keep both. Status and validation verdict must also match.

Readout targets, states and availability must match, with every numeric readout
within **0.5% relative**, `abs(a-b)/max(abs(a),abs(b))`. Null readouts retain their
matching explicit state and each curve must have at least one comparable readout.

Every curve sample, in both directions, must have a value within **0.5% relative**
on a segment of the other curve, at most **0.05% of the VGS axis span** away in x.
That is 0.005 V on the seven 0-10 V charts and 0.010 V on the two 0-20 V KCS
charts, approximately 0.3-0.4 source pixels.
Segments are clipped to that x window, including vertical segments; every sample
is checked without extrapolation, percentile trimming, sparse sampling or omitted
heads. This is a bounded two-coordinate comparison, not equality at exactly the
same VGS. Endpoint coverage outside that allowance fails too.

This x allowance is necessary and disclosed: the same-vector print copies have
subpixel calibration offsets. At nearly vertical heads they cause 18.36% and
21.18% raw same-VGS resistance differences. Readout agreement alone would not
justify ignoring those samples. The separate tight x allowance bounds their
geometric displacement while keeping the 0.5% resistance bound. A 0.02 V shift
of a real curve, one altered endpoint, or a shifted interior sample is rejected.

## Calibration (real v5 output)

`tools/rds_vgs_duplicate_calibration.py` reproduces the scores from the v5 manifest
and its existing dsdig crops. Numeric columns below are percentages. The bounded
curve column uses the x allowance above; the final column intentionally does not.

| TI part | Plot NCC | Max readout % | Max bounded curve % | Raw same-VGS % |
|---|---:|---:|---:|---:|
| CSD17302Q5A | 0.882643 | 0.1357 | 0.1282 | 0.6152 |
| CSD17304Q3 | 0.918597 | 0.0548 | 0.0741 | 0.0741 |
| CSD17306Q5A | 0.891519 | 0.2027 | 0.0886 | 18.3572 |
| CSD17307Q5A | 0.988705 | 0.2148 | 0.2612 | 0.2618 |
| CSD17309Q3 | 0.913949 | 0.1624 | 0.0790 | 0.7836 |
| CSD17310Q5A | 0.915709 | 0.2422 | 0.2070 | 21.1821 |
| CSD17318Q2 | 0.919358 | 0.1479 | 0.1307 | 7.2968 |
| CSD18502KCS | 0.950132 | 0.3846 | 0.4592 | 1.1596 |
| CSD18536KCS | 0.968941 | 0.1004 | 0.0184 | 2.5558 |

The weakest true pair is 0.882643. Among 852 cross-PDF visual controls, excluding
the explicitly shared IRLB drawing, the strongest is SIS176LDN versus SISS76LDN
at 0.668652; IRLB8721 versus IRLB8748 is 0.618279. The 0.86 threshold has margin
from both the observed true-pair minimum and these different-chart controls.
This is calibration on the stated corpus, not a universal false-positive bound.
The data gate remains mandatory even at score 1.

Known-bad calibration includes an actual copy of the CSD17310Q5A PDF whose p1
`125°C` label is replaced by `150°C`. Re-extraction finds 150/25°C on p1 and
125/25°C on p4; visual score **0.904813**, decision **conflict**, both kept.
This PDF and its extraction are under `work/v6/known-bads/` in the run directory.
The test constructs the PDF afresh. Real-panel data mutations of current,
temperature and kind each score 0.915709 and are conflicts. A horizontal reflection
of the real plot, with unchanged numeric data, scores **0.058844** and is rejected
by appearance. Perturbed samples are tested near the limit and at 2x/10000x.

## Winner, evidence and retirement

Prefer a numbered figure (including section-style `4-7`), then the larger plot
in source pixels, then page/diagram/content digest. Complete-link grouping requires
every pair in a group to pass; a similarity chain cannot discard its far endpoint.
Input order does not choose the winner. Raw artifacts of discarded copies remain
available, but those copies are absent from the served `panels` list.

The kept row gets `also_printed_at` entries with PDF/page/diagram/crop identity,
score, tolerances, all-sample and readout differences, and hashes of both crops
and extraction rows. This field is **informational, not numerically pinned** by
the old golden fixtures: renderer-dependent scores should not become numerical
chart truth. Retirement tests separately validate finite passing evidence and the
exact source/twin relationship. Every previously pinned served field stays pinned.

CLI manifests include `duplicate_checks` and `discarded_duplicates`. Direct
`digitize_pdf` callers may pass `duplicate_audit={}` without changing its two-item
return contract. A per-PDF `duplicate_checks/*.json` sidecar is always written,
with input-PDF and full package Python-source SHA-256. Run timing is returned to
the caller but omitted from reproducible sidecar files. There is no decision cache.

A reviewed `REBLESSED.json` retirement has `kind: retired`, `fixture`, `reason`,
`duplicate_of`, `evidence`, `date`, and `fixture_sha256`/`duplicate_of_sha256` over
the effective frozen panels. `part` is retained for compatibility with existing
index consumers. The loader continues to load the original fixture without edits.
The golden check requires an absent copy to occur exactly once in its specified
kept twin's `also_printed_at`, and rechecks that twin's full golden contract.
Changing either frozen fixture or the served twin fails; approved copies that
reappear fail. Retirement chains and self-retirement are forbidden.

The nine entries were proposed in PENDING.json with
`pending: "retire: duplicate of <fixture>"`. Pending permits the old copy to remain
only under its full old golden contract, or permits absence only with the checked
twin note. It never permits a copy both to be served and reported discarded. Fab
approved all nine on 2026-10-04 ("all green" on the v6 packet); they are now
approved retirements in REBLESSED.json, and no fixture directory was edited.

## Guard review checklist

1. **Unevaluable input:** missing calibration/crops/labels/samples, refusal, blank
   ink, corrupt images, NaN/infinity and probe exceptions retain both panels and
   record a non-duplicate reason. No default score means a match.
2. **Monotonicity:** maximum errors over every sample/readout, both directions;
   no percentile cutoffs. Tests alter first/interior/final samples at near and far
   offsets and readouts at 1.006x through 1000000x. Nonfinite tails cannot pass.
   Fixed NCC and bounded x windows do not optimize away growing displacement.
3. **Preconditions:** the production CLI is tested with the real PDF. Plot bounds,
   nondegenerate ink/axis ranges, recognized models/units, binding and sufficient
   samples are checked. The real relabelled PDF exercises extraction, not only a
   fabricated score. Capture now binds each row before suppression, and tests prove
   the retired and kept copies retain their own captured calibrations.
4. **Source of truth:** pair records hash actual crops and extracted rows; audit
   sidecars hash the actual source PDF and the full Python package deriving values.
   The existing extraction-test cache fingerprints source, inputs, dependencies and
   runtime patches; its capture helper is included in its fingerprint.
5. **Persistence:** no deduplication cache was added. Every pair is evaluated on
   this run. Unevaluable/conflict results remain explicit in the manifest and
   sidecar; cached/live extraction artifact equality is tested.
6. **Provenance:** status and numeric data are untouched. Each disappearance has
   its winner, evidence, tolerances and original location in the run manifest.
   The proposed retirements are clearly pending, not human approval.
7. **Known-bad calibration:** different real plots, identical cross-PDF plots,
   a physically relabelled PDF, real-sample/current/kind mutations, corrupt assets
   and exceptions all have tests. The mutation harness disables 28 individual
   selection conditions and exercises their tests plus all existing goldens.
8. **Fix versus mute:** the served panel count changes; no warning is removed or
   verdict improved. All retained numeric data and assets are compared with v5.
   Review cards and the FET table disclose the suppressed print locations.

Runtime is recorded by the calibration script and per-PDF batch telemetry. Initial
uncontended checks took about 0.08-0.20 seconds per TI pair; a calibration run while
regression workers were active measured 0.214 s median, 0.474 s maximum, 2.271 s
summed over nine pairs. Final run results and artifact paths are in
`CHANGELOG-batch_all-v6.md` in `/Users/fab/dev/ee/solar-charger-eval/rds-vgs/`.

After the regression workers finished, three trials per pair **including source/PDF
fingerprinting and audit writes** measured median 0.174 s, maximum 0.255 s
(`work/v6/runtime.json`). The final required suite passed 297 tests plus 11 subtests;
targeted mutants: 28 killed; full harness: 270 killed, 2 equivalents preserved
(3689 s, exit 0). All 42 golden checks pass, including the nine pending retirements.
