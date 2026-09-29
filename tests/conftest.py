"""pytest-only: xdist groups for the RDS(on)-vs-VGS suites (plain unittest never loads this).

Run them in parallel with

    OMP_THREAD_LIMIT=1 PYTHONPATH=src:tests pytest -n auto --dist loadgroup \\
        tests/test_rdson_gate_voltage.py tests/test_rdson_gate_voltage_review.py \\
        tests/test_rdson_gate_voltage_golden.py

Each test is put in the xdist group of the most expensive datasheet it
digitizes, read from its own source (and the source of the ``self._helper``
methods it calls), so one worker digitizes each PDF once and serves every
test that needs it from memory; the shared on-disk cache
(tests/rds_digitize_cache.py) covers the rest, and its per-key lock makes a
second worker wait for, not repeat, a digitization in flight. A class with
its own ``setUpClass`` stays on one worker. New tests written in the
existing style are grouped automatically: no marks to maintain.
"""

from __future__ import annotations

import inspect
import re

import pytest

RDS_MODULES = {"test_rdson_gate_voltage", "test_rdson_gate_voltage_review", "test_rdson_gate_voltage_golden"}

# Single-process digitization cost, batch-15 run 2026-09-29 (s, 5 parallel jobs,
# no OCR memo); unlisted datasheets are vector charts at ~1 s.
COST = {"RQ3E110AJ_Rohm": 59, "BRCS020N03RA_LCSC_C22449012": 24, "WSR3090_LCSC_C719278": 21,
        "RQ3E180AJ_Rohm": 11, "RQ6E080AJ_Rohm": 8, "CSD18502KCS_TI": 3}

_STEMS: set[str] | None = None


def _stems() -> set[str]:
    global _STEMS
    if _STEMS is None:
        import rds_digitize_cache
        _STEMS = rds_digitize_cache.pdf_stems()
    return _STEMS


def _source(obj) -> str:
    try:
        return inspect.getsource(obj)
    except (OSError, TypeError):
        return ""


def rds_group(item) -> str | None:
    cls = getattr(item, "cls", None)
    if cls is not None and "setUpClass" in vars(cls):
        return f"class:{cls.__qualname__}"
    if item.name.startswith("test_golden_"):
        return item.name[len("test_golden_"):]
    text = _source(getattr(item, "obj", None))
    if cls is not None:
        for name in set(re.findall(r"self\.(_\w+)\(", text)):
            text += _source(getattr(cls, name, None))
    # case tables: self.RASTER, F6_PANELS, ...
    for name in set(re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", text)):
        value = getattr(cls, name, None) if cls is not None else None
        text += repr(value if value is not None else vars(item.module).get(name))
    found = {s for s in re.findall(r"[\"']([A-Za-z0-9_]+)(?:\.pdf)?[\"']", text) if s in _stems()}
    if not found:
        return None
    return max(sorted(found), key=lambda s: COST.get(s, 1))


@pytest.hookimpl(tryfirst=True)   # before xdist turns the marks into nodeid suffixes
def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.module is None or item.module.__name__ not in RDS_MODULES:
            continue
        group = rds_group(item)
        if group is not None:
            item.add_marker(pytest.mark.xdist_group(group))
