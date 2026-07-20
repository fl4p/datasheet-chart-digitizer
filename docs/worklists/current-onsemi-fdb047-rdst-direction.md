# onsemi/Fairchild FDB047N10 RDS(Tj) caption direction

Status: bounded target, direction fallback, axis-identity extension, and focused
controls are independently AGENT-GREEN. Full authoritative RDS(Tj) corpus A/B
and human review remain held. `human_verified=false`; no commit or push.

## Defect and scope

Figure 8 is correctly classified as normalized RDS(on) versus temperature. Its
caption sits 10.44 pt above exactly one local plot grid, with no same-column
grid above. Text axis direction and formula evidence are absent, so the old
unresolved branch defaulted to plot-above, discarded the real following grid,
and emitted `panel: no direction-evidenced local RDS grid`.

Positive axis/formula direction remains authoritative. Only unresolved
captions may use the shared `caption_leads_nearer_grid` geometry fallback, and
only with exactly one below candidate, no above candidate, and a following gap
within 28 pt. Both-side and multiple-below candidates refuse. The target's
source spelling `Tj, Junction Temperature [ C ]` is admitted by a separately
bounded temperature-axis regex extension; it does not broaden RDS family
routing.

## Frozen evidence

Packet: `/private/tmp/dsdig-onsemi-fdb047-rdst-direction-v1`.

- Figure 8 now yields one 372-point PDF-vector curve at VGS=10 V and ID=75 A.
  Temperature spans -54.55..175.17 °C and normalized RDS(on) spans
  0.6187..2.3891; the 25 °C value is 0.996474.
- The X axis consumes -100..200 °C in 50 °C increments with 0.398 px
  residual. The normalized Y axis consumes 0..3 in 0.5 increments with
  0.286 px residual. No absolute RDS value is served.
- Synthetic tests preserve positive direction, adopt only a unique following
  grid, and refuse both-side/multiple-below ambiguity. BSC059 formula-below and
  NDB5060L positive-above controls remain OK.
- Forty-one focused RDS tests plus 31 subtests pass. Candidate/repeat PDF, RDS
  JSON, crop, overlay, and chart index are byte-identical; qpdf passes.

Independent review:
`/private/tmp/dsdig-onsemi-fdb047-rdst-direction-v1/reviews/codex-fdb047-rdst-direction-independent-review.json`
(SHA-256
`80f0cd2b645893b11ffebbe409f13c0a523219c39cc40a83397014c7425ac6a7`).
It confirms all 372 points have source ink within two pixels and every consumed
tick is within one pixel. The following Figure 10 caption is real crop/text
context contamination, but its grid is about 224 pt away, outside the 100 pt
candidate gate and selected plot; it does not affect Figure 8 axes, conditions,
or trace. That context caveat is retained explicitly rather than described as
zero neighbor bleed.
