# TI CSD13201W10 supported-chart coverage

**Status:** implementation in progress; no commit/push and `human_verified=false`.

## Source inventory

The source publishes Figures 1–11.  Current digitizers cover transfer, gate charge,
capacitance, normalized RDS(on)-versus-temperature, and body-diode charts.  They do
not yet cover transient thermal impedance, saturation/output characteristics,
threshold-versus-temperature, RDS(on)-versus-gate-voltage, SOA, or maximum-current
charts.  Those six unsupported families remain untouched rather than receiving
invented generic traces.

## Defects in supported families

1. Figure 3 transfer has explicit VGS/IDS axes and three temperature branches, but
   TI writes axis units as `... - V` and pdftotext leaves duplicate subscript `C`
   glyphs after two temperature labels.  Direction/temperature ownership therefore
   refuses a valid panel.
2. Figure 5 capacitance loses its upper plot rows because one occluded horizontal
   grid row creates a 49 pt gap and splits the grid region.  The clipped crop forces
   vector extraction to two candidates and raster fallback onto grid rails.
3. Figure 8 uses the short title `On Resistance vs Temperature`; the plot itself
   positively says `Normalized On-State Resistance` and `TC - Case Temperature - °C`,
   but the RDS(T) title/axis recognizers require the longer spelling.
4. Figure 3's linear transfer frame has four solid vertical rails.  The shared
   six-rail detector refuses it even though the source frame closes on all four
   sides; after recovery, disconnected colored legend samples must not join a
   temperature curve.
5. The page-1 and page-5 Gate Charge charts both digitize successfully, but the
   annotation pass selects only one preferred result and leaves the other bare.
6. Figure 8's red and green normalized-RDS traces legitimately converge/cross at
   low temperature.  Their PDF colors bind exactly to the printed VGS legend, so
   a universal physical ordering heuristic is not valid identity evidence.

## Required direction

1. Merge same-column horizontal-grid chunks across one bounded missing-row gap only
   when no Figure/Fig/Diagram caption lies between them.  A caption boundary remains
   a hard panel boundary.
2. Accept hyphen-delimited units only on a local semantic axis phrase.  Equals-sign
   condition callouts such as `VGS=5V` and `TC=25°C` must remain non-evidence.
3. Normalize only the evidenced duplicate `°C C` glyph sequence before extracting
   transfer temperatures; never use arbitrary nearby numbers.
4. Admit the short RDS(T) caption only when the existing normalized-axis, 25 °C unity,
   span, monotonicity, legend binding, and tick-residual gates all pass.
5. For Figure 5's linear-nF axis, keep calibrated physical values unavailable.  If
   and only if PDF-vector Ciss/Coss/Crss extraction and pixel-shape validation pass,
   expose an `overlay-review-required` pixel overlay under the user's explicit
   `--include-review-required` flag.  No pF/VDS values or Qoss metrics may leak.
6. A sparse transfer grid may use the shared closed-frame fallback only when its
   top, bottom, left, and right rails terminate on the same rectangle.  Split
   vector runs at disconnected endpoints so legend samples cannot become curve
   tails.
7. Gate-charge annotation is per detected result.  Each panel retains its own
   status and crop; one preferred result must not suppress a valid peer.
8. RDS(T) identity stays bound to source style/color and local legend text.  A
   VGS-based vertical ordering is neither an assignment rule nor a refusal gate;
   ambiguity in the actual style/legend binding still fails closed.

## Acceptance

- The exact user command detects and embeds source-faithful overlays for the page-1
  Gate Charge summary and Figures 3, 4, 5, 8, and 9; Figure 5 remains explicitly
  axis-untrusted/pixel-only.
- Figures 1, 2, 6, 7, 10, and 11 remain unchanged and unpainted.
- Fixtures cover the missing-grid-row recovery and caption-boundary negative, TI
  hyphenated-axis positive and equals-condition negative, duplicate-temperature
  normalization, and pixel-only C(V) physical-output refusal.
- Freeze a byte-repeat target packet, run `qpdf --check`, and obtain an independent
  agent review.  Shared finder/capacitance changes remain blocked on their frozen
  full-corpus A/B; any unrelated crop, points, identity, status, or overlay move is
  RED.  Agents never set `human_verified`.
