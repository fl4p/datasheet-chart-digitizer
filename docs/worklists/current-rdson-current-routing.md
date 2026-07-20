# RDS(on)-versus-current routing and axis ownership

**Status:** corrected focused routing packet v2 frozen. Extractor follow-up,
corpus audit, independent v2 review, and human review remain. No commit/push
and `human_verified=false`.

## Defect

The generic finder owns supported RDS(on)-versus-ID panels, but the specialized
digitizer silently skips common title/formula forms:

- Fairchild/onsemi `On-Resistance Variation vs. Drain Current and Gate Voltage`;
- Infineon `Typ. drain-source on resistance` with formula
  `RDS(on)=f(ID); parameter: VGS`.

The same family also contains normalized RDS(on)-versus-ID charts whose output
is dimensionless, not mOhm. Those must not enter the absolute-mOhm plugin.

## Guarded design

- Route by explicit drain-current direction in the title or by the owned
  `RDS(on)=f(ID)` formula. Do not infer direction from the generic `rds_on`
  kind alone.
- Require local `ID [A]`/drain-current and `RDS(on) [mOhm]` evidence before an
  absolute-mOhm result can pass validation.
- Exclude normalized RDS(on)-versus-ID panels from this plugin until a
  dimensionless-output contract exists.
- A routed panel whose curves cannot be source-bound must produce an explicit
  refused result/overlay rather than disappearing from annotation.

## Acceptance

- FDD390N15ALZ Figure 3 and IPT020N13NM6 Diagram 6 enter the current plugin;
  BSS138 Figure 2 stays outside because its Y axis is normalized.
- Existing SPD03 Figure 3 remains source-faithful and byte-stable.
- Routing GREEN is separate from item GREEN. Both new panels currently expose
  a same-black-style curve/legend binding limitation; no numeric output may be
  served until that extractor blocker is resolved and overlays are reviewed.

## Rejected/insufficient v1 evidence

Packet: `/private/tmp/dsdig-rdson-current-routing-v1`.

- Candidate and repeat directory manifests are byte-identical, canonical tree
  SHA-256 `05ba2964f99c9193094e3bbe9a31065b68693dd1a1b8697f6462e3525bd474b4`.
- FDD390N15ALZ Figure 3 and IPT020N13NM6 Diagram 6 both emit an explicit
  `refused` result with only `legend_curve_binding_ambiguous`.
- BSS138 emits no absolute-mOhm result, but strict review proved that v1 did
  not exercise the normalized-axis guard. The finder truncated its title before
  `Current`, so direction routing failed first. V1 therefore proves safe output
  but not the claimed normalized rejection mechanism.
- Focused tests: 4 passed plus 2 PDF subtests. The two routed panels additionally
  assert local absolute `ID [A]` and `RDS(on) [mOhm]` axis evidence.
- The root `SHA256SUMS` file contains a bad self-entry and is not authoritative.
  Strict review is
  `/private/tmp/dsdig-rdson-current-routing-v1/reviews/agent-rdson-current-routing-v1-001.codex-hxy-review.json`
  (SHA-256 `56f1f2d79c23e0c47d16f5e145a1c03680ee416ee2cd6dcc98f5c9a0691a59e5`).

This packet proves routing and fail-closed behavior only. It does not clear the
curve/legend binding blocker and does not authorize numeric serving.

## Corrected v2 focused evidence

Packet: `/private/tmp/dsdig-rdson-current-routing-v2`.

- When the finder title is wrapped/truncated, owned panel-local axis text may
  prove the RDS-versus-ID direction. Absolute routing still requires the local
  current and mOhm axis identities, and any `Normalized` evidence in the owned
  title/formula/panel/local-axis text excludes the panel.
- The BSS138 test now proves all three steps on its production finder panel:
  direction is evidenced by local `ID, Drain Current (A)` plus `RDS(on)`;
  local Y-axis text contains `Normalized`; absolute routing returns false.
- FDD390N15ALZ Figure 3 and IPT020N13NM6 Diagram 6 remain explicit refusals
  with only `legend_curve_binding_ambiguous`; BSS138 remains empty. Focused
  tests pass: 4 tests plus 2 PDF subtests.
- `candidate.sha256` and `repeat.sha256` are sorted relative-path content
  manifests created with
  `(cd RUN && find . -type f -print0 | sort -z | xargs -0 shasum -a 256)`.
  They are byte-identical and each hashes to
  `05ba2964f99c9193094e3bbe9a31065b68693dd1a1b8697f6462e3525bd474b4`.
- Source SHA-256: `rdson_current.py`
  `7da4ee738feaa77a5423d58ff73c9a50335e1e30d8cf472059a4957d23331af0`;
  focused test
  `dc1715d7421249cd379b3310dacf58d9713799dae2544fe04fbee96adb7ebc6a`.

V2 still proves routing and fail-closed behavior only. It does not clear the
same-style curve/legend binding blocker or authorize any numeric item.
