"""Fail-closed policy for datasheet collections with non-part-specific curves."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path


_UNTRUSTED_CURVE_COLLECTIONS = {"hxy"}
_SHARED_TEMPLATE_DIAGNOSTIC = "shared_curve_template_provenance_untrusted"


def enforce_curve_provenance(pdf: Path, result):
    """Keep review geometry while preventing shared templates from being served.

    A full-corpus audit of the HXY collection found exact pixel-identical chart
    bodies in 3,674 of 4,063 PDFs (90.4%), across unrelated part numbers.  That
    establishes that those plots are not independent part-specific measurements.
    The raw candidate remains attached for annotation, while every serialized or
    package-facing physical output stays fail-closed.
    """

    if pdf.parent.name.casefold() not in _UNTRUSTED_CURVE_COLLECTIONS:
        return result
    status = "source_untrusted" if result.status == "ok" else result.status
    return replace(
        result,
        status=status,
        diagnostics=tuple(
            dict.fromkeys((*result.diagnostics, _SHARED_TEMPLATE_DIAGNOSTIC))
        ),
    )
