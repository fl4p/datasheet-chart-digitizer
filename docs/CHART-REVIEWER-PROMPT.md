# Chart reviewer prompt — digitized datasheet curves

A reusable brief for an agent (Codex, Claude, any model) that reviews
`dsdig` output against the source PDF. Paste everything below the line as
the task, then fill in the `<…>` fields.

Why it exists: on 2026-09-28 two independent reviewers (Codex xhigh and
Opus) passed the RDS(on)-vs-VGS batch v3 of 15 panels and missed six defect
classes that Fab found in minutes by looking at the overlays. Each is now a
mandatory check below (marked **[F]**) with the real panel that exhibited
it. Reviewers had judged numbers against numbers; Fab judged what the
overlay shows against what the page prints.

On 2026-09-29 (v5) Fab found three more classes that the brief itself had
let through, and that Claude had seen and reported as acceptable:

- three panels with a printed I_D left "unknown";
- two panels with a printed temperature left "unknown";
- the source curves hidden under the traces.

The reviewers had treated an honest refusal ("unknown (unbound)",
`not_evaluable`, `review_required`) as correct behaviour. They had also
checked a round's fix only against the failure it fixed, never against the
opposite failure. Checks 12, 13, 17 and 22 and rules 4 and 5 below come from
that round.

---

## Task

Review the digitized chart panels in `<manifest path>` (overlays in
`<overlay dir>`, points in `<points dir>`, source PDFs in `<pdf dir>`,
produced by `<repo>@<commit>`). Read-only: write only under `<scratch dir>`.
Complete in one pass; never stop to ask. If one check is blocked, mark that
claim `unverified` and continue — never go offline silently.

### How to work

1. **Blind first.** Judge every panel yourself before reading any earlier
   review, changelog, or the tool's own status. The tool's `ok` is a claim
   under review, not evidence.
2. **Two views of every panel, both mandatory:**
   - **The picture:** the overlay, viewed as a human would, including
     zoomed crops of the regions in "Where defects hide" below.
   - **The numbers:** points, readouts, and calibration re-derived
     *independently of the tool's code* — vector charts from the PDF stroke
     paths, raster charts by pixel measurement on a fresh render at known
     DPI.

   A panel is not reviewed until both views agree. Most of the misses on
   2026-09-28 were things the numbers could not show and the picture made
   obvious.
3. **Report facts, not grades.** For each defect say *where* (panel, curve,
   VGS/value range, pixel range), *what the print shows*, *what the tool
   produced*, and *what a consumer would get wrong*.
4. **An honest refusal is still a defect when the page answers it.**
   `unknown`, `unbound`, `not_evaluable`, `not traced` and `review_required`
   are claims that the information is not available. Test every one of them
   against the page: if a careful human can read the answer, the refusal is
   a finding.

   Honesty only makes the defect visible; it does not excuse it. Never
   list such a refusal under "limitations", "still unknown" or "survived".
   The test is what a human can read, not what the tool's reader can
   extract. *Real case:* v5 RQ3E110AJ, RQ6E080AJ, FDP8870, RQ3E180AJ and
   BRCS020N03RA were all reported with printed parameters "unknown", and
   were accepted by the reviewer and by Claude until Fab rejected all five.

   "Cannot be read" is earned by climbing this ladder, in order. Stop at
   the first rung that gives a definite answer:

   1. **Read it by hand.**
      - Text layer: `pdftotext -bbox` on the page.
      - Vector glyphs and paths in the PDF.
      - Re-render the page at 600 dpi or more; crop at 4× and 8×.
      - Follow every leader and arrow to its tip.
      - Bind by placement (which side of which curve the label sits) and
        by physics (check 12).
   2. **Ask other frontier models, independently.**
      - Send each model the same source crop, not the overlay: plot plus
        labels, at high resolution. Ask the same neutral question.
      - Never include your own reading, the tool's output, or another
        model's answer.
      - Ask all three:
        - **GPT Astra (Codex):**
          `codex exec -m gpt-6-astra -i <crop.png> - < question.txt`
        - **DeepSeek V4.1 Flash (pi):**
          `pi -p --no-session --model fireworks/accounts/fireworks/models/deepseek-v4p1-flash @<crop.png> "<question>"`
        - **Opus 5.5:** a Claude subagent with the crop and the question.
      - The model you are running as counts as one of the three only if
        it did not form the reading under review.
   3. **Only then `unknown`.**
      - Record the rungs tried and each model's verbatim answer.
      - If the models agree with each other and with the page, the value
        is readable: the refusal is a finding, and your finding cites
        their answers.
      - If they disagree, `unknown` stands, with the disagreement
        recorded.

   A reviewer who writes "cannot be read" without the rung-2 answers has
   not finished the check.
5. **Every fix has an opposite failure; check both.** For each change since
   the previous round, name what it was meant to fix. Then check the
   opposite way it can fail:
   - traces made more visible → can the print still be seen?
   - curves split → did any get merged wrongly?
   - labels bound more often → did any get bound wrongly?

   *Real case:* the v3 "no black-on-black" fix made the v5 traces thick
   enough to hide every thin source curve (WSR3090 v5: "i dont see the
   original curves").

### Overlay legend (learn it before judging)

<Describe the overlay's marks for this chart class. For RDS(on)-vs-VGS:
green frame = detected plot box; blue "+" with label = a calibration tick
the fit USED; coloured line with dots = a traced curve (dots are samples,
not readouts); diamond on a grey dashed vertical = a readout at that VGS;
dark-blue "+" = table typical, "×" = table maximum, placed at the table
row's VGS; header/legend strips = status, reasons, curve labels, readout
states.>

Mistaking one mark for another is itself a finding against the overlay if
a careful reader could make the same mistake.

### Where defects hide — crop and inspect each of these on every panel

- the four frame edges and the four corners;
- the top of every steep curve where it meets the frame;
- every place two curves touch, merge into one ink band, or cross;
- both ends of every traced curve;
- every label, leader line, arrow, legend box, and text block inside the
  plot;
- every readout VGS line, and every table "+"/"×" marker;
- the lowest and highest *used* calibration tick on each axis;
- for each of the above, the same region of the *source* crop beside the
  overlay crop, to see whether the print is still visible under the trace
  (check 17).

### Mandatory checks

**Calibration**

1. Tick values, linear vs log, and unit (mΩ vs Ω, V) against the printed
   axis; hunt specifically for ×10 errors from lost decimal points.
2. **[F] Used vs printed ticks.** List every tick label *printed* on each
   axis and every tick the fit *used*. Any printed tick not used is a
   finding unless the manifest explains why. Any served readout outside
   the used-tick span is **extrapolated** — a finding unless the manifest
   flags it and anchors it independently (e.g. the frame edge within
   1 px). *Real case:* RQ3E110AJ v3 used only 10–30 mΩ and 1–10 V; the
   printed 0–8 mΩ and 0 V labels were unused; its 3.3 V and 4.5 V readouts
   (9.56, 8.62 mΩ) lay below the lowest used tick, unflagged.
3. **[F] Provenance is claimed, not proven — prove it.** Check every
   provenance field against the source. If `tick_source` says
   `text_layer`, confirm with `pdftotext -bbox` that the labels exist in
   the text layer. *Real case:* RQ3E110AJ claimed `text_layer`, but its
   page's text layer holds 2 numbers in total; the chart is an image.
4. **[F] Ticks on their rules, measured.** Recompute the fit residual at
   every used tick. For every labelled tick, measure the pixel distance
   between where the fit puts that value and where its printed gridline or
   frame edge is. Report the worst distance per axis in pixels and as a
   value error at a typical readout.

   A trace lying on the ink proves nothing here: a correct trace on a
   shifted or squeezed axis reads wrong values, and the overlay still looks
   right. Any calibration warning the tool emits (e.g.
   `axis_ticks_not_bound_to_grid`, `label_centroids_only`) is rule-4
   material. Measure it and report the number. Never file it under "needs
   a human decision" without that measurement.

   *Real case:* DMN4008LFG batch_all v2/v3. The y ticks sat on label
   centroids, 0.04 at 59.6 px and 0 at 792.2 px, while the printed rules
   are the frame edges at 50 and 802 px. The axis was squeezed 2.6 %, and
   values near 7 mΩ read about 0.35 mΩ low. The tool flagged it; the batch
   agent filed it as "possibly not a defect, the traces lie on the print";
   Claude carried that forward unmeasured through two rounds. Fab found it
   by eye: "y-axis ticks are off".

**Curves**

5. Every traced line follows the printed curve over its whole length — not
   a gridline, leader line, arrow, legend box, or text.
6. Every printed curve is traced, or its absence is stated. Look for curves
   that exist in the print with no trace, and trace ends short of where
   the ink ends (including clipping at the frame).
7. **[F] Merged ink bands.** Where two curves share one thick stroke,
   decide from the print which curve owns which part of the band, and
   whether the tool assigned it that way. An untraced part of either curve
   inside the band must be stated. *Real cases:* BRCS020N03RA (the upper
   part of one curve runs inside the other's band); RQ3E110AJ (the two ID
   curves print as one band).
8. **[F] Coincident data.** For every pair of curves, compare their values
   along the shared VGS range. Values identical to the last digit over a
   long span mean either that the source draws one shared path or that the
   tool reused the same ink for both — determine which from the PDF paths.
   *Real case:* FDP8870 v3, c0 and c1 identical (0.0 px) from 4.5 to 10 V.
9. Gaps: a real gap is shown as a break and never interpolated across; an
   untraced but continuous stretch is not called a gap.
10. Leader lines, arrows, and label boxes never become curve data; points
    removed at an annotation leave a recorded unread interval, whatever the
    spacing of their neighbours.

**Labels and anchors**

11. Every curve's parameter (temperature *and* its kind Tj/Tc/Ta, ID)
    against the printed legend, leader, or box. "Unknown" is correct only
    after the rule-4 ladder has been climbed: you read it by hand, then
    GPT Astra, DeepSeek V4.1 Flash and Opus 5.5 were each asked
    independently, and none gave a definite, agreeing answer. It must
    never be guessed. It must never be accepted merely because the tool
    could not read it.
12. **[F] Printed-text inventory.** For every panel, list every piece of
    text printed inside or beside the plot:
    - legends, boxes and free-standing labels;
    - labels with leaders or arrows;
    - axis-condition notes such as "I_D=20A", "Ta=25°C Pulsed",
      "125°  C", "I_D = 35A".

    For each one, say which curve parameter it sets and what the tool
    served. A printed value the tool did not bind is a finding, including
    one that sets only part of a parameter (the value read but not the kind,
    e.g. "T (subscript unread)" where "Ta" is printed).

    Where leaders are missing or end in a shared band, a human binds by:
    - placement (the label sits beside or above its curve);
    - physics: at equal VGS the higher I_D, and at VGS well above threshold
      the higher temperature, gives the higher RDS(on).

    Check whether the tool's binding matches. *Real cases (v5):*
    - RQ3E110AJ (11.0 A / 5.5 A), RQ6E080AJ (8.0 A / 4.0 A) and FDP8870
      (35 A / 1 A, no leaders): I_D unbound;
    - RQ3E180AJ: box "Ta=25°C" read as no temperature at all;
    - BRCS020N03RA: free labels "125°  C" / "25°  C" (space before C)
      unbound.
13. **[F] Every `not_evaluable` has a cause; judge the cause.** For each
    table row the tool did not evaluate, name the missing binding. If that
    binding is printed (check 12), the `not_evaluable` is a finding. If the
    row is not comparable (different I_D or temperature than any printed
    curve), confirm the tool says so, rather than raising or dropping a
    diagnostic. *Real case:* BRCS020N03RA v5 raised "curve 0 exceeds table
    max at 4.5 V". Curve 0 is the 125 °C curve and the row is 10 A at
    25 °C, a condition mismatch hidden by the unbound temperature labels.
14. **[F] Table markers the tool could not evaluate are still data.**
    Wherever a table "×" (maximum) sits *below* any typical curve at the
    table's VGS, report it, even when the tool says `not_evaluable`. It is
    either a datasheet contradiction or a mis-binding, and the tool must
    record it. *Real case:* BRCS020N03RA v3 at 4.5 V, lost because the
    temperatures were unbound.
15. Validation claims: an anchor compared at a different drain current or
    temperature must not make a panel "verified".

**The overlay as a deliverable**

16. **[F] Trace legibility.** Every trace must be visible against the source
    ink: no dark colours on black strokes, no 1-px lines hidden inside a
    thick stroke, no black-rimmed dots on black. Calibration labels must be
    readable, not drawn on top of gridlines. *Real case:* RQ3E180AJ v3 drew
    a curve in dark purple on black ink; RQ3E110AJ's "10mOhm" label sat on
    the 10 mΩ rule.
17. **[F] Source visibility, the opposite of 16.** Along every trace, the
    printed curve must still be visible, under or beside the trace, so that
    a human can see how far the trace deviates from the print. Crop at 1:1
    and at 4× at a steep head, at a flat tail, and wherever two curves run
    close. If the source crop shows a stroke that you cannot find in the
    overlay crop, that is a finding. *Real case:* WSR3090 v5 drew thick
    coloured dotted traces that completely covered its thin black curves
    ("i dont see the original curves"); it applied to every panel.
18. **[F] Occlusion.** A curve must never look as though it ends when it
    only runs underneath another. Compare each curve's drawn extent with
    its data extent. *Real case:* FDP8870 v3 — c0 looked as if it ended
    near 4.4 V; its data ran to 10 V under c1.
19. **[F] Every curve identifiable.** A reader must be able to match each
    drawn curve to its label (legend with colour swatch, or a direct label
    at the curve) without reading the manifest. If you cannot, that is a
    finding.
20. Header and legend text complete: no mid-word wraps, no cut-off reasons,
    and states distinguishable (not on chart / not traced / not usable).

**Status honesty**

21. No panel is `ok` with gaps, a partial trace, an unusable curve, an
    extrapolated readout, an approximate anchor, or an unstated coincidence.
22. Every limitation a consumer needs to know is stated as a reason on the
    panel, not only in a changelog. A stated reason is not a pass. For
    each reason, ask whether the page resolves it; if it does, it is a
    finding (rule 4).

**Tests (when reviewing a code change)**

23. Run the mutation harness if one exists, then write at least three
    mutants of your own targeting boundaries and kinds the harness does not
    cover (off-by-one interval bounds, a wrong kind label, reading across a
    typed interval, a swapped label binding). Report which survive.

### Image budget

Subagents die at the provider's image limit. View at most `<N, e.g. 40>`
images in total. Spend them on the overlay crops of "Where defects hide"
and on the matching source crops for check 17. Do bulk comparison in code.

### Output

1. **Access:** what you ran, what you fetched, and the image count.
2. **Per-panel table:** panel | defects found (each with checks 1–23
   references) | readouts you confirm or dispute, with your measured value.
3. **For each [F] check (2, 3, 4, 7, 8, 12, 13, 14, 16, 17, 18, 19):** an
   explicit line per panel — "checked, clean" or the finding. For check 12,
   the line is the inventory itself: each printed text item → the parameter
   it sets → what the tool served. A blank means "not done", and the review
   is incomplete.
4. **Refusals:** every `unknown`, `unbound`, `not_evaluable` and
   `not traced` the tool served, each with your verdict:
   - "the page resolves it as X", which is a finding (rule 4); or
   - "the page does not resolve it", with the ladder evidence: what you
     read by hand, and the verbatim answers from GPT Astra, DeepSeek V4.1
     Flash and Opus 5.5.
5. **Opposite failures:** for each change since the previous round, the
   opposite failure you checked and the result (rule 5).
6. **Findings:** severity-ranked; each tagged (a) digitization or
   arithmetic error, (b) unstated assumption that happens to hold, or (c)
   defect in the tool; and NEW or CARRIED relative to `<previous review
   paths>`.
7. **Survived:** what you checked and found correct, with numbers. A
   refusal is never "survived".

Do not invent findings. If a panel is clean, say so, with the measurements
that show it.

### Self-test before trusting a new reviewer setup

Run the brief on the v3 batch (`rds-vgs/batch15_v3_manifest.json`, repo
commit `b99938a`). A competent run must report all of the real cases
above:

- RQ3E110AJ: unused ticks, extrapolated readouts, false `text_layer`
  provenance, label on a gridline;
- BRCS020N03RA: maximum below a curve, the merged band;
- FDP8870: identical tails, the hidden curve;
- RQ3E180AJ: an invisible dark trace.

Then run it on the v5 batch (`rds-vgs/batch15_v5_manifest.json`, repo
commit `e74f698`). A competent run must report:

- I_D unbound although printed: RQ3E110AJ, RQ6E080AJ, FDP8870;
- temperature unbound although printed: RQ3E180AJ (box "Ta=25°C"),
  BRCS020N03RA (free labels "125°  C" / "25°  C");
- source curves hidden under the traces: WSR3090, and the same rendering on
  every panel.

Then run it on batch_all v3 (`rds-vgs/batch_all_v3/rdson_gate_voltage.json`,
repo commit `934f46a`). A competent run must report DMN4008LFG's y axis as
off its rules by about 10 px at both ends (check 4).

A setup that misses any of them is not ready for new batches.
