# Infineon IPD650P06NM body-diode visual dash styles

Status: source-proven diagnosis and bounded recovery contract; not implemented.
The current item remains correctly refused. `human_verified=false`; no commit or
push.

## Defect

Page 7 Diagram 12 contains four reverse-diode curves:
25 °C typical/maximum and 175 °C typical/maximum. The four identities are
printed through solid, long-dash, short-dash, and dotted legend swatches.

PyMuPDF reports every curve and legend fragment with an empty native dash array
(`[] 0`). `_dash_pattern` therefore collapses all source styles to `()`. The
legend parser sees only the 16 pt solid swatch because each patterned swatch is
exported as many 0.7 pt microsegment drawings, individually below its width
gate. All four extracted curves consequently bind to 25 °C typical and the
duplicate-identity gate correctly refuses the panel.

A style-lookup-only repair is unsafe. Both maximum branches also retain
unjoined source tails: a cross-style fragment can be geometrically closer than
the correct successor, so the existing global separation gate refuses rather
than guessing. Serving those partial curves after merely repairing the legend
would launder truncated data.

## Source signatures and required recovery

The panel has an injective visual signature set:

- 25 °C typical: one continuous run;
- 25 °C maximum: approximately 8.4 pt on / 2.8 pt off;
- 175 °C typical: approximately 4.2 pt on / 1.4 pt off;
- 175 °C maximum: approximately 1.4 pt on / 1.4 pt off.

A fallback may run only when repeated roles exist, every native dash array is
empty, and all exact legend rows are present. It must group row-local collinear
legend fragments into one swatch, reconstruct each curve's periodic on/off
signature from its source fragments, and match curve to legend one-to-one.

Tail joining then requires the same reconstructed signature, compatible stroke
width/color, monotone and tangent-contiguous endpoints, a gap consistent with
the signature, and a globally unique successor. All four signatures must be
injective and no same-signature continuation may remain after a served
endpoint. Missing, duplicate, inconsistent, or ambiguous evidence must keep the
panel refused; identities may never be inferred from drawing or left/right
order.

## Required evidence

The positive target must produce exactly `(25,typical)`, `(25,maximum)`,
`(175,typical)`, `(175,maximum)` and both maximum traces must reach their true
right endpoints near crop x=784 on source ink. Controls with real native dash
arrays, including ISC024N08NM7, must remain byte-identical and must not enter
the visual fallback. Unit fixtures must cover clipped edge runs, grouped
microsegment swatches, duplicate/missing signatures, ambiguous successors,
cross-style competitors, inconsistent periods, and leftover continuations.
