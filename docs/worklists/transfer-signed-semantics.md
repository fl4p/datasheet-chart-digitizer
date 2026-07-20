# Transfer signed-temperature and P-channel semantics

**Status:** separate bounded future work; pre-existing findings do not block the frozen HXY run
when authoritative A/B proves they are unchanged. Agents must not set `human_verified`.

## Slice A — signed temperature binding

BSS138LT1G/BSS138LT3G print a `−55 °C` transfer curve that can be parsed as `+55 °C`, corrupting
temperature identity even when curve geometry looks plausible.

Required fix:

- preserve the sign from the exact label token through temperature assignment and provenance;
- distinguish Unicode minus, ASCII hyphen-minus, and OCR variants without inventing a sign;
- fail closed if the sign is ambiguous;
- validate identity using the printed label, expected Vth temperature direction, and crossover
  geometry.

## Slice B — P-channel magnitude provenance

FDN302P, FDS4435BZ, and XR20P09L2 require explicit provenance when negative P-channel axes are
represented as positive magnitudes. Every transformed axis/value must record the source sign and
the magnitude transform; silent sign loss is not acceptable.

## Acceptance

The two slices are independently landable. Each needs a frozen affected-corpus A/B, source overlays,
signed positive/negative fixtures, an ambiguous-sign refusal fixture, and zero movement outside its
evidenced class. P-channel magnitude output must round-trip to the printed signed convention.
