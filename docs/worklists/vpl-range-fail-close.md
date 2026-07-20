# Vpl out-of-range fail-close v1

**Status:** frozen candidate is independently patch-GREEN in both agent lanes and is not landed.
Fab's overlay/landing decision remains required. Agents must not set `human_verified`, commit,
or push.

## Bound packet

- Packet: `dsdig-verify-backlog/agent-sweep-reports/fixes/vpl-range-v1/packet.json`
- Packet SHA-256: `eb0332b1e2b08773fbb4f0308f7c0ba87ea24f36162fd98e81294eb8e598e149`
- Baseline full-304 manifest SHA-256:
  `a1c6f78ba8f9829f1fbd1cda4100c5aac761e3b13398d4049d9607cf6267ba87`
- Candidate full-304 manifest SHA-256:
  `6f5fdab45aff81ed1723e4030e2e0d9cd2765ab7bcf7eeeaf19d718b45156700`
- Opus acceptance review SHA-256:
  `1e965524ae3bbb9306ec09f2dfdbc337cda2efc95aa195576a55e630c3ca7f67`
- Codex independent review: `/private/tmp/agent-vpl-range-v1-001.codex-hxy-breakdown-agent-review.json`
- Codex review SHA-256:
  `5aa782ca177bea7e8aaeb15870c9f3868b51e42041cda4fcf08b8bedd14583da`

## Acceptance already demonstrated

The candidate moves exactly the three DI280 variants: native and Ghostscript stop serving
out-of-own-axis Vpl values (16.96 V and 18.01 V) and null all physical output; the raster variant
remains null and becomes an explicit non-gate refusal. The other 301 corpus rows are byte-identical.
Both agent lanes independently verified that no non-DI280 row moves and that the multi-panel
source overlay proves the refused values came from outside the gate-charge panel. This certifies
the patch/contract scope, not a recovered numeric DI280 item.
