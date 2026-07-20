# Infineon formula-variable RDS routing

Status: routing/fail-closed mechanism independently AGENT-GREEN; page 8
Diagram 8 item AGENT-RED. `human_verified=false`; no commit or push.

## Routing defect and contract

Infineon `IPA60R125CFD7.pdf` titles adjacent page-8 charts only "Typ.
drain-source on-state resistance" and "Drain-source on-state resistance".
The independent variable is carried by local formulas:

- Diagram 7 `RDS(on)=f(ID)` must route to the current plugin.
- Diagram 8 `RDS(on)=f(Tj)` must route to the temperature plugin.
- `f(VGS)` remains unsupported.

Formula-variable routing is restricted to already RDS-classified panels and
uses the formula from the panel's own page column. An adjacent normalized
axis label may not hide an absolute RDS-vs-ID panel. Ambiguous monochrome
curve identities remain an explicit refusal rather than a silent drop.

## Frozen routing evidence

Packet: `/private/tmp/dsdig-inf-ipa60r-rds-routing-v1`.

- Diagram 7 visibly enters `rds_on_current` and refuses only
  `legend_curve_binding_ambiguous`; `curves=[]` and no numeric trace is served.
- Diagram 8 enters `rds_on_temperature`, binds the 10 V trace, and reports
  normalized RDS(on)=0.997134 at 25 °C.
- Candidate/repeat JSON, embedded overlays, and annotated PDF are
  byte-identical; manifest differences are output paths only.
- Both PDFs pass `qpdf --check`.

Independent review:
`/private/tmp/dsdig-inf-ipa60r-rds-routing-v1/reviews/ipa60r-rds-routing-independent-review.json`
(SHA-256
`78abbc0857e2be6acba5c2887665e5c2aa83a048f4757b40d47a6bf617ed548a`).

## Item blocker

The same review correctly keeps Diagram 8 RED: its computed plot box
`[135,0,806,767]` extends about 31 px into the title row and 9 px into the left
tick gutter instead of owning the printed grid near `[144,31,806,768]`.
Although all 644 served points sit on source ink, the trace starts at about
-44.8 °C and omits the visible source segment beginning at -50 °C. Routing is
GREEN; frame/endpoint fidelity is not. The item must not be promoted until a
joint frame and source-endpoint repair is reviewed.
