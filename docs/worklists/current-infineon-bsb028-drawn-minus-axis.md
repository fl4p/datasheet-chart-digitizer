# Infineon BSB028 drawn-minus temperature axis

Status: source-geometry target, fail-closed mechanism, and bounded native-signed
control are independently agent-GREEN with scope holds. Full breakdown-corpus
A/B and human review remain held. `human_verified=false`; no commit or push.

## Defect and source ownership

Infineon `BSB028N06NN3_G` page 10 Diagram 15 is a clean absolute
V(BR)DSS-versus-Tj chart. Its printed X ticks are
`-60,-20,20,60,100,140,180`, but the PDF text layer exposes
`60,20,20,60,100,140,180`. The shared strict numeric-axis fitter therefore
correctly refused the non-monotone text values.

The missing signs are source-proven vector glyphs, not inferred values. Page
drawings 181 and 184 are opaque black, filled, closed horizontal rectangles
4.315 by 0.572 pt. Each lies 0.730 pt immediately left of its owned numeric
word and aligns with the corresponding major grid center. No such rectangle
exists beside the positive 20 or later ticks.

## Bounded correction

Breakdown now collects native Tj word boxes locally. A bare positive integer is
made negative only when exactly one source drawing is an opaque dark filled,
closed four-edge horizontal rectangle whose width, height, gap, and vertical
center are constrained relative to that exact word. More than one eligible
glyph refuses as ambiguous. Light, open, vertical, or distant marks do not
qualify. Explicit Unicode minus is accepted.

The shared numeric-axis fitter remains unchanged and strict. There is no
arithmetic-progression sign guess: the fully unsigned target sequence admits
increasing and decreasing mirror solutions, so geometry-free recovery remains
refused. Existing fused tick-run handling is preserved.

## Frozen evidence

Packet: `/private/tmp/dsdig-bsb028-drawn-minus-v1`.

- BSB028 p10d15 is verified with 507 source/served points, no withheld points,
  Tj -39.9..150.1 C over the printed curve extent, V(25 C)=59.991 V against
  the source-owned 60 V minimum, 35.02 mV/K slope, seven X and seven Y ticks,
  zero grid-center fit residual, and no warnings.
- The overlay trace is source-seated throughout; the crop, plot frame, units,
  tick identities, and endpoint extent are correct.
- Native-signed `BSB056N10NN3_G` p9d15 remains verified with 524 points,
  V(25 C)=99.971 V, 55.07 mV/K, and no warnings. Candidate/repeat control CSV
  and overlay are byte-identical.
- Seventy-nine focused breakdown tests pass. Target candidate/repeat PDF,
  overlay, and CSV are byte-identical; both PDFs pass qpdf.

Full authoritative breakdown-corpus A/B and human verification remain landing
gates.

## Independent review

Review:
`/private/tmp/dsdig-bsb028-drawn-minus-v1/reviews/codex-bsb028-drawn-minus-independent-review.json`

- Verdict: `AGENT-GREEN_WITH_SCOPE_HOLDS`.
- SHA-256: `35b18f88aefef5c39c2ebd136e94ceb8a8bcc66ef47c9206d919edfe43f09e69`.
- Target item, bounded fail-closed mechanism, native-signed BSB056 control,
  determinism, qpdf, and 79 focused tests are GREEN.
- The review records the artifact generator source SHA and the formatting-only
  post-freeze source SHA separately.
- Full breakdown-corpus A/B and human verification are held;
  `human_verified=false`.
