# Nexperia specification-table figure cross-references

Status: LANDED

## Scope

Layout cluster `doc-00072` contains ten canonical Nexperia PSMN 40 V
datasheets. Generated PDF transforms are excluded by the canonical layout
index. In this layout, electrical-characteristics table cells contain
cross-references such as `Fig. 13` and `Fig. 14`. The generic numbered-caption
finder currently treats two of those cells as chart captions:

- page 2: `QG(tot) total gate charge` / `Fig. 13`;
- page 6: `Coss output capacitance` / `Fig. 14`.

The resulting crops are specification-table rows, not plots. Page 8 contains a
real `Fig. 14. Input, output and reverse transfer capacitances` caption, so
figure number and chart-family wording are not sufficient rejection evidence.

## Guard contract

Reject a numbered chart-caption candidate only when its text box is locally
enclosed by two source rules that own one shallow row and the same row contains
at least two numeric values plus an engineering unit. Once that evidence proves
the page is a specification table, suppress overlapping or open-bottom
gate-charge axis fallbacks that follow a long row rule.

The semantic evidence is required because some Infineon chart pages deliberately
place genuine `Typ. transfer`, `Typ. gate charge`, and `Typ. capacitances`
captions inside ruled plot tables. Those captions have no same-row numeric/unit
run and remain eligible. The guard does not blacklist `Fig. 13`, `Fig. 14`,
`gate charge`, or `output capacitance`.

The following controls must remain:

- genuine page 7 transfer, RDS(on), and gate-charge panels;
- genuine page 8 capacitance and body-diode panels;
- captions adjacent to a plot frame but not enclosed by a shallow ruled row;
- synthetic gate-charge recovery outside a ruled table row.

## Bounded evidence

Baseline source is `5f31a5d`. The generated-copy-free cluster members are:

- `PSMN1R5-40YSD`
- `PSMN1R9-40YSB`
- `PSMN1R9-40YSD`
- `PSMN2R2-40YSB`
- `PSMN2R2-40YSD`
- `PSMN2R5-40YLD`
- `PSMN2R8-40YSB`
- `PSMN2R8-40YSD` (medoid)
- `PSMN3R2-40YLD`
- `PSMN3R5-40YSD`

The baseline emits ten false page-6 capacitance panels and five false page-2
gate-charge panels. The bounded V4 candidate removes exactly those fifteen
panels: 78 baseline rows become 63, with zero additions and zero changed
survivors. The repeated output tree is byte-identical, including JSON, CSV,
scan metadata, and crops. `charts.json` SHA-256:
`7d85c9d21f8f934bb124b519d70fe498bd3096d0df073983a1427bae4d1d51a1`.

## Canonical Nexperia collateral

The authoritative A/B uses all 386 canonical `nxp/` PDFs from the frozen
layout index. Generated PDF transforms remain excluded. Baseline source is
`5f31a5d`; all 2,877 baseline panel rows are present and there are no baseline
scan errors.

The V4 candidate emits 2,423 panels:

- 454 source-table panels removed;
- zero panels added;
- zero surviving panel dictionaries changed;
- zero scan errors.

The removals are 284 capacitance parameter rows and 170 gate-charge rows:
149 `Coss`, 135 `Ciss`, 21 `QG(tot)`, and 149 synthetic `Gate charge` panels.
All 149 synthetic panels are diagram `951`, contain the `Dynamic
characteristics` specification table and a total-gate-charge parameter, and
contain no `Qg (nC)` chart-axis label. Representative BUK, PSMN, PXN, PH, and
GaN crops were inspected as specification tables. Candidate `charts.json`
SHA-256:
`a11f87eb2caa64b607076cdd7292bbbeebd036caee01f1da390d11e831453bb3`.

Frozen V4 source SHA-256 values:

- `find_charts.py`: `586d11b8ab159a4ca5e27a353d3cdd07078a337257c165f13b40de697be17e53`
- `finder_caption_geometry.py`: `5aa2787b41db5d1037fff487078baef8529c1f07eb18625b0533d040ce131997`
- `table_crossref_filter.py`: `fd2d5679eb61d9ae1c3499c51ade0593eb00023bb54bc1da2780f503b0a8bc0d`

## Verification

The relevant finder suite passes 153 tests with 8 skips:

- `tests.test_nexperia_spec_table_crossrefs`
- `tests.test_find_charts`
- `tests.test_finder_review_blockers`
- `tests.test_littelfuse_detached_captions`
- `tests.test_body_diode_finder`

The Infineon corrupt-glyph end-to-end fixture specifically confirms that genuine
captions inside ruled plot tables remain detected. `human_verified` remains
false.

## Landing gates — complete

1. Focused geometry and production-path tests pass.
2. The ten-member cluster A/B removes exactly the fifteen table-row panels.
3. Every other bounded panel row is byte-identical.
4. The full canonical Nexperia finder A/B has no additions or survivor changes.
5. Finder output is deterministic on a repeated candidate run.
6. Detector provenance is recorded separately from downstream item fidelity;
   `human_verified` remains false.
