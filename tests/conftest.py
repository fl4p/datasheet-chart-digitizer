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
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

import cache_env
import render_memo

# Every test, not only the RDS suites that import rds_digitize_cache: memoize tesseract and pdftoppm
# (both keyed on their full input and installation; DSDIG_TEST_CACHE=0 turns them off).
if cache_env.enabled():
    os.environ.setdefault("DSDIG_OCR_CACHE", str(cache_env.cache_root() / "ocr"))
    render_memo.install(cache_env.cache_root() / "render")

RDS_MODULES = {"test_rdson_gate_voltage", "test_rdson_gate_voltage_review", "test_rdson_gate_voltage_golden"}
RDS_CACHE_MODULES = RDS_MODULES | {"test_rdson_gate_voltage_v5"}   # read tests/rds_digitize_cache.py

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


DURATIONS = Path(__file__).with_name("test_durations.txt")


def _durations() -> dict[str, tuple[bool, float]]:
    """node id -> (slow, seconds) from tests/test_durations.txt (scripts/update_test_durations.py)."""
    out = {}
    if DURATIONS.exists():
        for line in DURATIONS.read_text().splitlines():
            if line and not line.startswith("#"):
                kind, seconds, nid = line.split(" ", 2)
                out[nid] = (kind == "slow", float(seconds))
    return out


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: end-to-end / OCR / corpus test, deselected by the default "
                                       "-m 'not slow' (tests/test_durations.txt or @pytest.mark.slow); "
                                       "full suite: -m ''")
    if os.environ.get("DSDIG_PROFILE_SUBPROCESS"):
        _install_spawn_counter()


LEAD_S = 10.0   # tests at least this long start first


def _long_first(items, durations) -> None:
    """Reorder in place: tests of at least LEAD_S start first, longest first, so xdist does not start
    one last; the rest keep their order. A class with its own setUpClass moves as one unit. The RDS
    suites stay where they are: their digitize cache makes concurrent workers wait for one
    digitization per PDF, so starting them all at once serializes the run (measured 2026-11-24)."""
    units: list[list] = []
    for item in items:
        cls = getattr(item, "cls", None)
        if (units and cls is not None and "setUpClass" in vars(cls)
                and getattr(units[-1][0], "cls", None) is cls):
            units[-1].append(item)
        else:
            units.append([item])

    def cost(unit) -> float:
        if any(i.module is not None and i.module.__name__ in RDS_MODULES for i in unit):
            return 0.0
        return sum(durations.get(i.nodeid, (False, 0.0))[1] for i in unit)
    lead = sorted((u for u in units if cost(u) >= LEAD_S), key=lambda u: -cost(u))
    rest = [u for u in units if cost(u) < LEAD_S]
    items[:] = [item for unit in lead + rest for item in unit]


@pytest.hookimpl(tryfirst=True)   # before xdist turns the marks into nodeid suffixes, and before -m
def pytest_collection_modifyitems(config, items):
    durations = _durations()
    slow_classes = set()
    for item in items:
        if durations.get(item.nodeid, (False, 0.0))[0]:
            item.add_marker(pytest.mark.slow)
        module = item.module.__name__ if item.module is not None else None
        if module in RDS_CACHE_MODULES:
            group = rds_group(item)
            if group is not None:
                if module in RDS_MODULES:
                    item.add_marker(pytest.mark.xdist_group(group))
                # digitizes a datasheet: whichever such test runs first pays the (OCR-bound)
                # digitization the others read from the shared cache, so all of them are slow
                item.add_marker(pytest.mark.slow)
        cls = getattr(item, "cls", None)
        if cls is not None and "setUpClass" in vars(cls) and item.get_closest_marker("slow"):
            slow_classes.add(cls)
    for item in items:    # a slow setUpClass is paid by whichever of its tests runs first on a worker
        if getattr(item, "cls", None) in slow_classes:
            item.add_marker(pytest.mark.slow)
    _long_first(items, durations)


# ---- opt-in profiling: DSDIG_PROFILE_SUBPROCESS=<dir> counts the processes each test spawns
_SPAWNS: dict[str, dict[str, int]] = {}
_CURRENT = ["<collection>"]


def _install_spawn_counter():
    real_init = subprocess.Popen.__init__

    def counting_init(self, args, *a, **kw):
        argv0 = args if isinstance(args, (str, bytes)) else (list(args)[:1] or [""])[0]
        name = os.path.basename(os.fsdecode(argv0).split()[0]) if argv0 else "?"
        per_test = _SPAWNS.setdefault(_CURRENT[0], {})
        per_test[name] = per_test.get(name, 0) + 1
        return real_init(self, args, *a, **kw)

    subprocess.Popen.__init__ = counting_init


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _CURRENT[0] = item.nodeid
    try:
        return (yield)
    finally:
        _CURRENT[0] = "<between>"


def pytest_sessionfinish(session):
    """Per process (each xdist worker too): the digitize cache's hit/miss counts, if asked for."""
    import sys
    out = os.environ.get("DSDIG_PROFILE_SUBPROCESS")
    if out and _SPAWNS:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / f"spawns-{os.getpid()}.json").write_text(json.dumps(_SPAWNS))
    cache = sys.modules.get("rds_digitize_cache")
    if cache is not None:
        cache.dump_stats("pytest")
