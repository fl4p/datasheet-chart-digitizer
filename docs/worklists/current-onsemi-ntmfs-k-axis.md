# Onsemi uppercase engineering-suffix capacitance axis

Status: parser mechanism and page 5 Diagram 8 item independently
AGENT-GREEN. `human_verified=false`; no commit or push.

## Defect and contract

`NTMFS011N15MC.pdf` labels the capacitance axis `1`, `10`, `100`, `1K`,
`10K` pF. The native-text position calibrator previously stripped the `K`
suffix, aliasing the two upper ticks onto `1` and `10`. The inconsistent
decade ladder correctly degraded to pixel-only output, but withheld all
physical C(V) values from an otherwise source-faithful chart.

The numeric-token parser now applies `K`, `M`, and `G` multipliers only to
exact uppercase numeric tokens. Lowercase suffixes and unit- or prose-bearing
tokens remain ineligible, and the existing position-fit residual gate still
fails closed on an inconsistent ladder.

## Frozen evidence

Superseding packet: `/private/tmp/dsdig-onsemi-ntmfs-k-axis-v2`. V1 is
preserved; V2 hardens token boundaries so Unicode unit-bearing forms such as
`10KΩ` and `1MΩ` remain ineligible. Its annotated PDF, overlay, and CSV are
byte-identical to V1.

- Baseline physical columns were blank; the candidate populates all 1,887
  rows without moving trace pixels.
- The owned Y labels map to decades `4/3/2/1/0` with an independently refit
  residual of 0.0196585 decade.
- The log-X range remains 0.1--100 V and log-Y becomes 1--10K pF.
- `Ciss > Coss > Crss`, all three traces are source-seated, and no trace rides
  a gridline.
- Candidate/repeat PDF, overlay, CSV, and finder output are byte-identical;
  both PDFs pass `qpdf --check`.
- The focused 10-test parser suite and the other 73 capacitance tests pass.
  The unrelated Toshiba raster/OCR end-to-end regression remains RED and is
  explicitly outside this parser slice.

Independent review:
`/private/tmp/dsdig-onsemi-ntmfs-k-axis-v2/reviews/ntmfs-k-axis-v2-independent-review.json`
(SHA-256
`fc56b27c307ab0fa60847d6c6bd69ddf586eea74514838390841de0cc93e6f01`).
