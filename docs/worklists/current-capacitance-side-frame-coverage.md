# Capacitance symmetric side-frame coverage

Status: **scoped landing-GREEN; fresh
full re-extraction held by unavailable Git-LFS source objects**

## Scope

Recover a capacitance plot box when the established grid detector stops at an
interior vertical but the owned horizontal rails prove a farther left, right,
or two-sided frame. This generalizes the reviewed NDB5060L/IPD50 right-edge
recovery without treating a tick label, crop border, or neighboring panel rail
as frame evidence.

Catalog positives:

- Infineon `IPD50N10S3L-16` Figure 10: right edge stops near 85 V instead of
  the owned 100 V endpoint.
- onsemi `NVMFS027N10MCLT1G` Figure 7: right edge stops at the 80 V gridline
  while the frame and all source curves continue to 100 V.
- Infineon `BSC004NE2LS5` Figure 11: the detector starts near 3 V and ends one
  interval early although the horizontal rails prove the 0--25 V frame.

The NDB5060L Figure 9 box is a locked human-GREEN control. Its independently
closed frame and all other coordinates must remain unchanged.

## Positive-evidence contract

The existing detector still establishes the top, bottom, and interior grid.
A side may move only when all of the following hold:

1. at least five owned horizontal rows span the detected plot;
2. at least 60% share the same farther endpoint;
3. the support includes both the top and bottom plot rows;
4. at least six full-height interior verticals form a regular grid;
5. the grid vertical nearest the candidate side agrees with the detected
   boundary, so the evidence is edge-specific;
6. the extension is between 2% of crop width and 30% of the detected box; and
7. the candidate remains inside the inner 1.5%--98.5% crop range.

Text is not frame evidence. If either endpoint or grid ownership is ambiguous,
the established box is retained. The unaffected sides remain byte-for-coordinate
identical, except for the pre-existing independently closed-frame path used by
NDB5060L.

## Bounded evidence

Frozen packet: `/private/tmp/dsdig-cap-frame-inset-v1/`.

Native crop changes:

| Part | Before | Candidate |
|---|---|---|
| `IPD50N10S3L-16` | `[60,32,472,620]` | `[60,32,538,620]` |
| `NVMFS027N10MCLT1G` | `[28,19,438,380]` | `[28,19,542,380]` |
| `BSC004NE2LS5` | `[172,71,603,614]` | `[110,71,623,614]` |

The IPD crop is from the sweep-resolution packet; its separately reviewed
end-to-end packet resolves the same endpoint at its native resolution. The
NDB5060L control remains `[77,31,664,448]`.

Candidate and repeat BSC/NVM result JSON is byte-identical at SHA-256
`9f51da4670f83cb44bed000e14adedd199e5c716ddb46172c382c4c6472bcd4e`.
Both recovered overlays and point CSVs are also byte-identical. The canonical
frame payload digest is
`95e784b07ada8b5c2f4eccf613be5bb9d21864dbb4447a25a52554e065d1985a`.

Because the canonical BSC/NVM PDFs are unavailable Git-LFS objects, the bounded
run falls back to raster extraction over the exact frozen crops. It remains
fail-closed for independent trace-completeness reasons (`all_traces_left_edge_gap`
and `Ciss_short_x_span` respectively). This slice proves the owned frame; it
does not launder those trace defects into physical output.

## A/B and tests

- Recomputed all 459 results in the prior authoritative corpus from their exact
  frozen crops: zero plot-box deltas and zero detector errors.
- Recomputed 370 unique frozen sweep crops: exactly the three catalog positives
  above changed, with no unexplained extension.
- Candidate and repeat A/B reports are byte-identical at SHA-256
  `3f7ceec5a903eb23b805db6f62b02d21fac0afd53144612c32b8344cd953bf76`.
- 70 focused plot-box/capacitance tests pass, including symmetric two-edge
  recovery, a 25%-width right extension, missing top/bottom support, foreign
  rails, crop borders, sparse frames, and NDB-style right recovery.
- The broader capacitance selection reached 95 passed with five PDF-dependent
  failures. All five stop on the 131--132-byte Git-LFS pointer inputs before
  exercising this detector; that run is not called green.

Independent review verdict:
`SCOPED_GREEN_FRESH_FULL_SOURCE_REEXTRACTION_HELD`. The reviewer found no code
defect, confirmed both new frames against their source and prior overlays,
verified the IPD and locked NDB controls, reproduced both A/B counts and
digests, and reran the 70 focused tests. Review SHA-256:
`edbba613fd1dc63fc109dc415f7458ee16603e619570ad9453d830bfeb3b71e6`;
`human_verified=false`.

## Remaining gate

- Re-run the source-to-result authoritative harness when the exact Git-LFS PDF
  binaries are available. `human_verified=false` for the new BSC/NVM items.
