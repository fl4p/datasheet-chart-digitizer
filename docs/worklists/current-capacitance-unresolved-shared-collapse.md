# Capacitance unresolved Ciss/Coss shared collapse

Status: minimal fail-closed invariant implemented in the current worktree;
focused unit and bounded positive/control checks pass. Full capacitance-corpus
A/B and independent artifact review remain open. `human_verified=false` for
the implementation packet.

## Defect

Six capacitance results remained `status=ok` with
`trace_validation_status=pass` after Coss joins the Ciss band away from the
normal low-voltage convergence region and never proves a later downward
separation:

| Part | Panel | Recorded unresolved span | Review state |
|---|---:|---|---|
| onsemi `FDMS86200DC` | p6d8 | late span x=371..496, `separated_sign_before=-1`, `separated_sign_after=null` | Fab flagged; Coss snap plus separate Crss truncation |
| Toshiba `TK55S10N1` | p6d88 | spans x=312..413 and 416..476, both with `separated_sign_before=-1`, `separated_sign_after=null` | agent RED pending human review; separate Crss truncation |
| ST `STW70N60DM6-4` | p6d7 | span x=296..406, `separated_sign_before=1`, `separated_sign_after=null` | agent RED pending human review |
| ST `STP60N043DM9` | p6d7 | span x=275..406, `separated_sign_before=1`, `separated_sign_after=null` | Fab human-FLAGGED |
| ST `STW56N65M2-4` | p7d11 | span x=232..377, `separated_sign_before=1`, `separated_sign_after=null` | Fab human-FLAGGED |
| ST `STW75N60M6` | p6d9 | span x=299..406, `separated_sign_before=1`, `separated_sign_after=null` | Fab human-FLAGGED |

The extractor already recorded these regions in `shared_collapse_spans`, and
the review overlay labeled the shared Ciss/Coss band, but the signal did not
previously affect trace validation. Physical Coss values therefore remained
consumable even though the recorded provenance said the two curve identities
never re-separated.

Frozen review evidence:

- `/Users/fab/dev/pv/ee/dsdig-verify-backlog/review-html/human-batch-26-capacitance/`
- `/Users/fab/dev/pv/ee/dsdig-verify-backlog/MANIFEST.opus-cap-batch26.jsonl`
- `/Users/fab/dev/pv/ee/dsdig-verify-backlog/MANIFEST.opus-cap-batch28.jsonl`

The manifest is authoritative about review state. `FDMS86200DC`,
`STP60N043DM9`, `STW56N65M2-4`, and `STW75N60M6` are human-flagged;
`TK55S10N1` and `STW70N60DM6-4` remain agent RED and must not be described as
human-verified.

## Fail-closed contract

- A low-V edge convergence may remain accepted only when the source curves
  subsequently prove their expected distinct ordering.
- A shared Ciss/Coss span that begins after distinct ordering has already been
  established and has `separated_sign_after=null` is unresolved identity.
- Unresolved identity must not retain `trace_validation_status=pass`, physical
  Coss values, Qoss, or any derived scalar. It must either recover Coss from a
  separately source-owned lower band or fail closed with an explicit diagnostic.
- Recovery and refusal are separate phases. The minimal safety patch is to
  withhold first; a lower-band recovery needs its own source-seating proof.
- Crss truncation on `FDMS86200DC`/`TK55S10N1` is independent and must not be
  claimed fixed merely because Coss now fails closed. It is owned by
  [capacitance trace coverage and clipped top decade](current-capacitance-trace-coverage-top-clip.md).

## Required negative controls

These clean low-voltage convergence cases must remain accepted when their
shared region ends with a confirmed later separation:

- Infineon `BSZ086P03NS3_G` p8d11
- ST `STD80N6F7` p5d7
- ST `STL170N4LF8` p7d16
- Toshiba `TPH1R204PB` p6d811
- Infineon `IPB044N15N5` p7d11
- Infineon `IPD055N08NF2S` p8d11
- onsemi `FDMS86202ET120` p5d8

`BSZ086P03NS3_G`, `STD80N6F7`, `STL170N4LF8`, `TPH1R204PB`, and
`FDMS86202ET120` carry edge-origin shared spans with a non-null
`separated_sign_after`; `IPB044N15N5` and `IPD055N08NF2S` emit no shared span.
This is the bounded discriminator and must be asserted directly, not
approximated by a global "any shared span fails" rule.

## Acceptance

1. Freeze per-item candidate/repeat artifacts for the six positives and seven
   negatives at identical source/dependency closure.
2. Assert the six positives cannot serialize `pass` or physical Coss while
   their non-edge unresolved span remains.
3. Assert all seven negatives retain identical curves, values, validation state,
   overlays, and Qoss outputs.
4. Inspect source-vs-overlay at the join, middle, and tail for every recovered
   positive; monotonicity and rank checks alone are insufficient.
5. Run the authoritative full capacitance-corpus A/B and review every changed
   `shared_collapse_spans`, trace status, Coss array, and derived Qoss result.

## Current implementation evidence

`trace_validation_summary` now emits
`ciss_coss_unresolved_shared_collapse` when a shared span has a proved prior
ordering but no proved later separation. Low-V edge convergence with a later
separation remains accepted. A fresh bounded run makes `STW70N60DM6-4` p6d7
`unverified` with physical output withheld, while the same-family clean
`FDMS86202ET120` control stays `ok/pass`.

ST `STB80N20M5` p6d7 is a separate crossing-seating defect: Fab flagged Coss
cutting below its source curve and rejoining, but it emits no shared span and
is not claimed fixed by this invariant.

## Separate trace-fidelity item

Infineon `IPB160N04S2L-03` p6d11 is a distinct bug: Crss leaves the printed
source below roughly 1 V on a linear VDS axis while retaining a full point
count. It has no shared-collapse discriminator and is tracked in
[capacitance trace coverage and clipped top decade](current-capacitance-trace-coverage-top-clip.md).
