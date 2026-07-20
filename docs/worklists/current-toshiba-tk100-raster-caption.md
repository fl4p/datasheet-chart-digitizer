# Toshiba TK100 raster caption ownership

Status: finder retry and Figure 8.8 capacitance item independently AGENT-GREEN;
Figure 8.10 gate-charge digitization remains RED. `human_verified=false`; no
commit or push.

## Defect and contract

`TK100E10N1.pdf` uses separately embedded raster images for its chart rows.
The Figure 8.8 capacitance caption could not bind its image after an earlier
caption bbox contaminated the first ownership attempt, so it inherited the
Figure 8.10 gate-charge box. A numbered capacitance caption now retries the
existing image-ownership contract without the contaminated caption bbox. The
retry still requires a same-column image at least 90 pt high directly above
the caption; it is not a generic nearest-image fallback.

## Frozen evidence

Packet: `/private/tmp/dsdig-tk100-raster-caption-retry-v1`.

- The finder has exactly two target deltas: Figure 8.8 rebinds from the gate
  box to its exact capacitance image, and Figure 8.10 is emitted separately.
- Three Toshiba controls (`TK25S06N1L`, `TJ40S04M3L`, `TPHR8504PL1`) are
  unchanged.
- Figure 8.8 recovers a trusted position-OCR log C(V) axis, the three seated
  `Ciss/Coss/Crss` traces, and physical CSV values. Candidate/repeat PDF,
  overlay, CSV, and finder output are deterministic; both PDFs pass qpdf.
- Figure 8.10 ownership is correct, but its gate-charge trace switches from the
  rising VGS branch to falling VDS, rides the zero axis, truncates the source
  160 nC range at 120 nC, and serves an invalid `Vpl=1.33 V`. That item remains
  RED and is not laundered by the finder/capacitance result.
- The unrelated IPI extra synthetic gate panel remains RED and unchanged.

Independent review:
`/private/tmp/dsdig-tk100-raster-caption-retry-v1/reviews/tk100-raster-caption-retry-independent-review.json`
(SHA-256
`3b424ce310b0c2c261e9b2dee02faa667fe65884ae94ff415cd0e9fbbe082ae4`).
