# NDB5060L capacitance right-frame recovery

Status: all NDB5060L charts human-GREEN by Fab on 2026-07-20, after focused
dual review and authoritative 800-input capacitance A/B. Target human review is
closed; shared landing remains pending. `human_verified=true` for NDB5060L;
no commit or push.

## Defect

In `onsemi/NDB5060L.pdf` Figure 9, the finder crop is complete but the generic
capacitance plot-box detector discards vertical rails beyond 96% of crop width.
The true 50 V frame is x=664/690 (96.23%), so the successful legacy detector
mistakes the 40 V gridline at x=630 for the right frame. The yellow overlay and
all three digitized C(V) curves therefore stop near 40.58 V.

## Bounded contract

- Keep the shared legacy detector and its crop-edge exclusion unchanged.
- In the capacitance-only wrapper, independently recover a four-rail closed
  frame even after the legacy detector succeeds.
- Prefer it only when left/top/bottom agree with the legacy box and exactly the
  right rail extends by a bounded amount. The new rail and top/bottom must
  mutually close; a foreign neighbor rail, missing closure, or crop border is
  ineligible.
- Preserve finder bbox/crop, axis calibration/ticks, trace identities, status,
  physical availability, and Qoss contract. Only the proven plot extent,
  source points, derived C(V) values, and overlay may move.
- Run the authoritative capacitance corpus A/B; review every strict one-edge
  extension microscopically and require byte identity elsewhere.

## Current evidence

The target changes from plot `[78,31,630,449]` to the independently closed
`[77,31,664,448]`. Status stays `ok`, vector extraction and trusted axis
calibration stay unchanged, and each trace grows from 557 to 587 source-seated
points through approximately 49.65 V. Six focused closed-frame positives and
negatives pass. The broader capacitance file currently has 60 passes and one
unrelated TK100E10N1 OCR calibration failure that reproduces through the old
plot-box path; it is not accepted as a clean corpus oracle.

Frozen target packet: `/private/tmp/dsdig-ndb5060l-cap-right-v1`. Candidate
and repeat annotated PDFs, overlays, and CSV are byte-identical. Two independent
focused reviews find the true 50 V frame, all three traces source-seated through
the endpoint, unchanged finder crop/axis/identity/status, and no grid ride:

- `reviews/opus-ndb5060l-cap-right-review.json`
- `reviews/agent-ndb5060l-cap-right-v1-001.codex-hxy-review.json`

The authoritative frozen 800-input A/B has 477 selected results and 323
byte-identical errors. Exactly two additional unserved raster panels change
plot box: NCE4080 recovers its final 40 V frame and NCE60P28AK recovers its
final 30 V frame. Both newly admitted tails are source-seated; all status,
physical-availability, axis, and serving-contract fields are identical, and
no physical values leak. NCE4080 remains UNVERIFIED for its untrusted axis;
NCE60P28AK remains fail-closed RED for a pre-existing Coss annotation-leader
capture in both baseline and candidate. This is not item laundering.
Independent full-A/B review:
`reviews/agent-ndb5060l-cap-right-v1-fullab-001.codex-ee-hxy-review.json`
(SHA-256 `4ffd39e6f871eba4122e18d7991f7fb27200bc268c5209b6c2d8a06b07265854`).

## Human verification

On 2026-07-20 Fab explicitly verified that all charts in NDB5060L are GREEN.
This closes the target/item human-review gate, including Figure 9 through its
true 50 V right frame. It does not by itself land the shared change or upgrade
the unrelated NCE corpus controls.
