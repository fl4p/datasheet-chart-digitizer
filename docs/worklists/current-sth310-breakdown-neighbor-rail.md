# ST STH310 breakdown neighbor-rail ownership

Status: bounded patch and page 7 Diagram 8 independently AGENT-GREEN.
`human_verified=false`; no commit or push.

## Defect

`st/STH310N10F7-2.pdf` page 7 Diagram 8 is normalized breakdown voltage
versus junction temperature. A loose side-by-side crop retained the left
neighbor chart's full-height rail at crop pixel x=209. The generic raster
finder used that isolated rail as the plot's left edge, placing the real
0.94..1.04 Y-label ladder inside the plot and then reporting the misleading
error "Y axis: only 0 tick labels".

## Bounded repair

The breakdown plugin may re-seat a raster frame only when both facts hold:

- the full crop contains a regular family of at least six vertical grid lines
  after trimming isolated rails; and
- at least four coherent numeric Y labels form a narrow, monotonic ladder in
  the gutter exposed between the old and proposed edges.

The candidate grid is additionally bounded in width and may extend the right
edge by at most 1.5 grid pitches. Geometry without the owned numeric ladder is
a no-op, preserving the existing fail-closed refusal.

## Frozen evidence

Packet: `/private/tmp/dsdig-sth310-breakdown-neighbor-rail-v1`.
Review request SHA-256:
`f02c3ddd4f9ecde881ac0fda4a14561fcc95ffd7d4fd2131fce06dcab620033a`.

- Plot box moves from foreign-rail `[209,55,723,445]` to the source-owned
  `[362,55,758,445]` grid.
- Six Y ticks occupy the recovered left gutter. Exact-center maxima are
  0.000 px on X and 0.447 px on Y.
- The red trace is source-seated without branch drift or grid riding.
- 335 source points are recovered; 333 are served through 175 °C and two
  beyond one unlabeled interval are explicitly withheld.
- `V(BR)DSS(25 °C)=100.014 V`, verified against the page-4 100 V table minimum.
- Candidate/repeat charts, overlay, CSV, and annotated PDF are byte-identical;
  path-normalized result JSON is identical. Both PDFs pass `qpdf --check`.
- All 68 breakdown tests pass, including the ambiguous two-label no-op guard.

Independent review:
`/private/tmp/dsdig-sth310-breakdown-neighbor-rail-v1/reviews/sth310-breakdown-neighbor-rail-independent-review.json`
(SHA-256
`7cd26987e52827bdd45f3b08c09860aa50ad25da64c29ca210eaa1f86ce520bf`).
It found no mechanism or item blocker; `human_verified=false`.

## Out of slice

Other `STH310N10F7-2` transfer, capacitance, diode, and RDS refusals remain
separate extractor gaps and are not upgraded by this breakdown result.
