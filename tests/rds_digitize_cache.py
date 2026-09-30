"""Shared on-disk cache of ``rdson_gate_voltage.digitize_pdf`` results for the RDS(VGS) tests.

The three RDS suites and the mutation harness digitized the same PDFs again
and again (each module had its own in-process ``_CACHE``). This module is the
one place they now get an UNPATCHED digitization from. A result is reused
only when it is provably what ``digitize_pdf`` would return right now:

Key
    the PDF's SHA-256 and path; a SHA-256 over EVERY file of the imported
    package tree (``src/``, not a hand-picked list, so any code change
    misses); the mode (plain / capture) and a fingerprint of each declared
    patch (e.g. a label reader switched off); this helper's own source (the
    capture spies live here); Python, numpy, OpenCV, PyMuPDF, SciPy, Pillow
    versions; the tesseract installation fingerprint; OMP_THREAD_LIMIT.

Runtime check (the mutation harness patches code IN MEMORY, never on disk)
    before any lookup, every loaded ``datasheet_chart_digitizer`` module's
    namespace is compared with a fresh execution of the exact source text
    that was hashed: functions by code object (so a ``patch.object`` or a
    ``_source_mutant`` differs), constants by value and type, classes member
    by member. Any difference, an attribute added, a module loaded from
    outside the hashed tree, a source file changed since it was hashed, or an
    active ``mock.patch(...).start()`` means NOT pristine: the digitization
    runs for real and nothing is read or written. A mutant can therefore
    never read an unmutated entry, and never write one.

Entries
    ``out/test-cache/digitize/<key>/`` holds ``result.pkl`` (pickle: tuples
    stay tuples) and ``tree.tar`` (the whole out tree: crops, overlays,
    CSVs), plus ``meta.json`` with the key and both files' SHA-256, written
    last into a temp dir that is renamed into place. An entry that is
    missing a file, fails its checksums or does not unpickle is deleted and
    recomputed, never trusted. A per-key file lock makes concurrent workers
    wait for one computation instead of repeating it.

Environment
    DSDIG_TEST_CACHE=0      no digitize cache and no OCR memo (everything live)
    DSDIG_TEST_CACHE_DIR    cache root (default: <repo>/out/test-cache)
    DSDIG_OCR_CACHE         the tesseract memo (src/.../tesseract_memo.py);
                            defaulted to <root>/ocr when the cache is on
"""

from __future__ import annotations

import atexit
import builtins
import contextlib
import dataclasses
import dis
import fcntl
import hashlib
import io
import json
import os
import pickle
import re
import shutil
import sys
import tarfile
import tempfile
import types
from pathlib import Path
from unittest import mock

import numpy as np

import datasheet_chart_digitizer as _pkg
from datasheet_chart_digitizer import rdson_gate_voltage as rgv

FORMAT = 1
PKG = _pkg.__name__
PKG_DIR = Path(_pkg.__file__).resolve().parent
SRC_DIR = PKG_DIR.parent
REPO = Path(__file__).resolve().parents[1]
DS = Path("/Users/fab/dev/ee/solar-charger-eval/ds")
OUT_ROOT = REPO / "out"


def enabled() -> bool:
    return os.environ.get("DSDIG_TEST_CACHE", "1").strip().lower() not in {"0", "off", "false", "no"}


def cache_root() -> Path:
    return Path(os.environ.get("DSDIG_TEST_CACHE_DIR") or OUT_ROOT / "test-cache")


if enabled():
    os.environ.setdefault("DSDIG_OCR_CACHE", str(cache_root() / "ocr"))

# statistics for the log: hits/misses/uncached, and why a call was uncached
STATS = {"memory_hit": 0, "disk_hit": 0, "computed_stored": 0, "uncached": 0, "corrupt": 0}
LAST_UNCACHED_REASON: list[str] = []
UNCACHED_REASONS: dict[str, int] = {}     # first reason of each uncached call -> count


def dump_stats(label: str = "") -> None:
    """Append this process's STATS as one JSON line to $DSDIG_TEST_CACHE_STATS (if set), once."""
    path = os.environ.get("DSDIG_TEST_CACHE_STATS")
    if not path or _DUMPED:
        return
    _DUMPED.append(True)
    with open(path, "a") as handle:
        handle.write(json.dumps({"pid": os.getpid(), "label": label, **STATS,
                                 "uncached_reasons": UNCACHED_REASONS}) + "\n")


_DUMPED: list[bool] = []
atexit.register(dump_stats, "atexit")


# ---------------------------------------------------------------- source hash

def _source_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                  and not p.name.endswith((".pyc", ".pyo")) and not any(part.endswith(".egg-info") for part in p.parts))


def source_hash(root: Path = SRC_DIR) -> tuple[str, dict[str, bytes]]:
    """SHA-256 over every file of the source tree (relative path + bytes), and the bytes."""
    digest = hashlib.sha256()
    texts: dict[str, bytes] = {}
    for path in _source_files(root):
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        texts[rel] = data
        digest.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest(), texts


_SNAPSHOT: dict = {}


def _snapshot() -> tuple[str, dict[str, bytes]]:
    """The source as first hashed in this process: the reference for the runtime check."""
    if not _SNAPSHOT:
        _SNAPSHOT["hash"], _SNAPSHOT["texts"] = source_hash()
    return _SNAPSHOT["hash"], _SNAPSHOT["texts"]


# ---------------------------------------------------------------- runtime check

_REFERENCE: dict[str, dict] = {}
_UNSET = object()
# Module-level memo dicts that fill at run time and never change a result:
# compared by type and by identity with the object first seen, not by content.
RUNTIME_STATE = {("datasheet_chart_digitizer.tesseract_memo", "_FINGERPRINT")}
_STATE_SEEN: dict[tuple[str, str], object] = {}


def _reference_namespace(module: types.ModuleType, text: bytes) -> dict:
    if module.__name__ not in _REFERENCE:
        namespace = {"__name__": module.__name__, "__file__": module.__file__, "__builtins__": builtins,
                     "__package__": module.__package__, "__spec__": module.__spec__,
                     "__loader__": getattr(module, "__loader__", None)}
        if hasattr(module, "__path__"):
            namespace["__path__"] = module.__path__
        exec(compile(text, module.__file__, "exec"), namespace)
        _REFERENCE[module.__name__] = namespace
    return _REFERENCE[module.__name__]


def _same_code(a: types.CodeType, b: types.CodeType) -> bool:
    return a == b   # CPython compares bytecode, consts (recursively), names, line table


def _same(live, ref, module_name: str, live_globals: dict, ref_globals: dict, assumed: set | None = None) -> bool:
    """Does the live object behave as the one the source text defines?

    ``assumed`` holds (live, ref) class pairs under comparison, so a class
    whose generated methods close over the class itself compares equal
    coinductively instead of recursing forever.
    """
    if live is ref:
        return True
    assumed = set() if assumed is None else assumed

    def same(a, b):
        return _same(a, b, module_name, live_globals, ref_globals, assumed)

    if type(live) is not type(ref):
        # An instance of a class this module defines (transfer_anchor_batch.ANCHORS:
        # AnchorEvidence records): the reference run made its own class, so compare
        # the classes member by member and then the instances field by field. Any
        # other type difference (a lookalike class, a plain object) still differs.
        live_cls, ref_cls = type(live), type(ref)
        if (live_cls.__module__ == module_name == ref_cls.__module__
                and live_cls.__qualname__ == ref_cls.__qualname__ and same(live_cls, ref_cls)):
            return _same_state(live, ref, same)
        return False

    if isinstance(live, types.FunctionType):
        if not _same_code(live.__code__, ref.__code__):
            return False
        # module-owned on both sides (dataclass-generated methods of the reference
        # class get the LIVE module dict), or privately scoped on both sides;
        # a module function running in some other namespace (a mutant copy) is not
        live_owned = live.__globals__ is live_globals
        ref_owned = ref.__globals__ is ref_globals or ref.__globals__ is live_globals
        if live_owned != ref_owned:
            return False
        if not (same(live.__defaults__, ref.__defaults__) and same(live.__kwdefaults__, ref.__kwdefaults__)):
            return False
        if (live.__closure__ is None) != (ref.__closure__ is None):
            return False
        for a, b in zip(live.__closure__ or (), ref.__closure__ or ()):
            if not same(a.cell_contents, b.cell_contents):
                return False
        wrapped = getattr(live, "__wrapped__", _UNSET), getattr(ref, "__wrapped__", _UNSET)
        return wrapped[0] is wrapped[1] or same(*wrapped)
    if isinstance(live, (staticmethod, classmethod)):
        return same(live.__func__, ref.__func__)
    if isinstance(live, property):
        return all(same(getattr(live, n), getattr(ref, n)) for n in ("fget", "fset", "fdel"))
    if isinstance(live, type):
        if (id(live), id(ref)) in assumed:
            return True
        if live.__module__ != module_name or live.__qualname__ != ref.__qualname__:
            return False
        assumed.add((id(live), id(ref)))
        if len(live.__bases__) != len(ref.__bases__) or not all(same(a, b) for a, b in zip(live.__bases__, ref.__bases__)):
            return False
        skip = {"__dict__", "__weakref__", "_abc_impl"}   # _abc_impl: the ABC subclass-check cache
        live_vars = {k: v for k, v in vars(live).items() if k not in skip}
        ref_vars = {k: v for k, v in vars(ref).items() if k not in skip}
        # __slotnames__ is copyreg's cache, written onto a class the first time
        # one of its instances is pickled or copied (the capture entries pickle
        # Trace/Calibration/...). Accepted only with exactly the value copyreg
        # derives from the class's __slots__; anything else still differs.
        for side, cls in ((live_vars, live), (ref_vars, ref)):
            if "__slotnames__" in side:
                if side.pop("__slotnames__") != _expected_slotnames(cls):
                    return False
        return live_vars.keys() == ref_vars.keys() and all(same(live_vars[k], ref_vars[k]) for k in live_vars)
    if isinstance(live, dataclasses.Field):
        return all(same(getattr(live, n), getattr(ref, n)) for n in live.__slots__)
    if isinstance(live, types.MappingProxyType):
        return same(dict(live), dict(ref))
    if isinstance(live, (bool, int, float, complex, str, bytes, type(None))):
        return live == ref or (isinstance(live, float) and live != live and ref != ref)
    if isinstance(live, re.Pattern):
        return live.pattern == ref.pattern and live.flags == ref.flags
    if isinstance(live, (tuple, list)):
        return len(live) == len(ref) and all(same(a, b) for a, b in zip(live, ref))
    if isinstance(live, (set, frozenset)):
        return live == ref
    if isinstance(live, dict):
        return live.keys() == ref.keys() and all(same(live[k], ref[k]) for k in live)
    if isinstance(live, np.ndarray):
        return live.dtype == ref.dtype and live.shape == ref.shape and bool(np.array_equal(live, ref, equal_nan=True))
    if isinstance(live, types.ModuleType):
        return False                          # a different module object under the same name
    # value objects (enum members, frozen dataclass instances): equal by value and printed the same
    if type(live).__eq__ is not object.__eq__:
        try:
            return bool(live == ref) and repr(live) == repr(ref)
        except Exception:  # noqa: BLE001 - an uncomparable object is not proven pristine
            return False
    text = repr(live)
    return " at 0x" not in text and text == repr(ref)


def _same_state(live, ref, same) -> bool:
    """Instance state, field by field: every __slots__ value and the whole __dict__."""
    names = _expected_slotnames(type(live))
    for name in names:
        a, b = getattr(live, name, _UNSET), getattr(ref, name, _UNSET)
        if (a is _UNSET) != (b is _UNSET) or (a is not _UNSET and not same(a, b)):
            return False
    live_dict, ref_dict = getattr(live, "__dict__", None), getattr(ref, "__dict__", None)
    if (live_dict is None) != (ref_dict is None):
        return False
    return live_dict is None or same(dict(live_dict), dict(ref_dict))


def _expected_slotnames(cls: type) -> list[str]:
    """What copyreg._slotnames computes for ``cls`` (without reading its cache)."""
    names = []
    for c in cls.__mro__:
        if "__slots__" in c.__dict__:
            slots = c.__dict__["__slots__"]
            for name in [slots] if isinstance(slots, str) else slots:
                if name in ("__dict__", "__weakref__"):
                    continue
                if name.startswith("__") and not name.endswith("__"):
                    stripped = c.__name__.lstrip("_")
                    name = f"_{stripped}{name}" if stripped else name
                names.append(name)
    return names


def _package_modules() -> list[types.ModuleType]:
    return [m for name, m in sorted(sys.modules.items())
            if m is not None and (name == PKG or name.startswith(PKG + "."))]


def runtime_deviations(excluded: set[tuple[str, str]] = frozenset()) -> list[str]:
    """Why the live package differs from its hashed source ([] = pristine).

    ``excluded`` names (module, attribute) pairs a caller patches itself and
    keys on; everything else must match the source exactly.
    """
    deviations: list[str] = []
    if mock._patch._active_patches:           # patch(...).start() anywhere in the process
        deviations.append(f"{len(mock._patch._active_patches)} active mock patch(es)")
    snap_hash, texts = _snapshot()
    now_hash, _ = source_hash()
    if now_hash != snap_hash:
        deviations.append("source tree changed on disk since it was hashed")
    for module in _package_modules():
        path = Path(module.__file__).resolve()
        try:
            rel = path.relative_to(SRC_DIR).as_posix()
        except ValueError:
            deviations.append(f"{module.__name__} loaded from outside {SRC_DIR}")
            continue
        if rel not in texts:
            deviations.append(f"{module.__name__}: {rel} not in the hashed tree")
            continue
        ref = _reference_namespace(module, texts[rel])
        live = vars(module)
        submodules = {k for k, v in live.items() if isinstance(v, types.ModuleType) and v.__name__.startswith(PKG + ".")}
        names = {k for k in set(live) | set(ref) if not (k.startswith("__") and k.endswith("__"))} - submodules
        for name in sorted(names):
            if (module.__name__, name) in excluded:
                continue
            a, b = live.get(name, _UNSET), ref.get(name, _UNSET)
            if (module.__name__, name) in RUNTIME_STATE and a is not _UNSET and b is not _UNSET:
                if type(a) is not type(b) or _STATE_SEEN.setdefault((module.__name__, name), a) is not a:
                    deviations.append(f"{module.__name__}.{name} replaced")
                continue
            if a is _UNSET or b is _UNSET:
                deviations.append(f"{module.__name__}.{name} {'added' if b is _UNSET else 'missing'}")
            elif not _same(a, b, module.__name__, live, ref):
                deviations.append(f"{module.__name__}.{name} differs from its source")
    return deviations


# ---------------------------------------------------------------- declared patches

def _code_fingerprint(code: types.CodeType):
    consts = tuple(_code_fingerprint(c) if isinstance(c, types.CodeType) else (type(c).__name__, repr(c))
                   for c in code.co_consts)
    return (code.co_code.hex(), consts, code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars,
            code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount, code.co_flags)


def _globals_used(code: types.CodeType) -> set[str]:
    names = {i.argval for i in dis.get_instructions(code) if i.opname in ("LOAD_GLOBAL", "LOAD_NAME")}
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _globals_used(const)
    return names


def replacement_fingerprint(fn) -> str | None:
    """A stable fingerprint of a self-contained replacement function, or None.

    Only a plain function with no closure, no non-builtin globals and
    literal defaults is fingerprintable: its behaviour is then fully given
    by its code. Anything else cannot be keyed and is never cached.
    """
    if not isinstance(fn, types.FunctionType) or fn.__closure__:
        return None
    if any(not hasattr(builtins, name) for name in _globals_used(fn.__code__)):
        return None
    for value in (fn.__defaults__ or ()) + tuple((fn.__kwdefaults__ or {}).values()):
        if not isinstance(value, (bool, int, float, str, bytes, type(None))):
            return None
    return hashlib.sha256(repr((_code_fingerprint(fn.__code__), fn.__defaults__,
                                sorted((fn.__kwdefaults__ or {}).items()))).encode()).hexdigest()


# ---------------------------------------------------------------- key

def _versions() -> dict:
    import cv2
    import PIL
    import pymupdf
    import scipy
    from datasheet_chart_digitizer import tesseract_memo
    tess = shutil.which("tesseract")
    return {"python": sys.version, "numpy": np.__version__, "cv2": cv2.__version__,
            "pymupdf": getattr(pymupdf, "VersionBind", "?") + "/" + getattr(pymupdf, "mupdf_version", "?"),
            "scipy": scipy.__version__, "PIL": PIL.__version__,
            "tesseract": tesseract_memo.tesseract_fingerprint(tess) if tess else None,
            "OMP_THREAD_LIMIT": os.environ.get("OMP_THREAD_LIMIT", "1 (digitize_pdf default)")}


_PDF_SHA: dict[tuple, str] = {}
_VERSIONS: dict = {}


def _pdf_sha(pdf: Path) -> str:
    stat = pdf.stat()
    marker = (str(pdf), stat.st_size, stat.st_mtime_ns)
    if marker not in _PDF_SHA:
        _PDF_SHA[marker] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    return _PDF_SHA[marker]


def cache_key(pdf: Path, mode: str, patches: list[tuple[str, str, str]]) -> str:
    """The key for digitizing ``pdf`` in ``mode`` with the declared, fingerprinted patches."""
    if not _VERSIONS:
        _VERSIONS.update(_versions())
    payload = {
        "format": FORMAT,
        "pdf": str(pdf), "pdf_sha256": _pdf_sha(pdf),
        "src_sha256": _snapshot()[0],
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "mode": mode,
        "patches": sorted(patches),
        "versions": _VERSIONS,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------- entries

_MEMORY: dict[str, tuple[bytes, bytes]] = {}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry_dir(key: str) -> Path:
    return cache_root() / "digitize" / key[:2] / key


def _read_entry(key: str) -> tuple[bytes, bytes] | None:
    """(result pickle, tree tar) of a verified entry, or None; a bad entry is deleted."""
    folder = _entry_dir(key)
    if not folder.exists():
        return None
    try:
        meta = json.loads((folder / "meta.json").read_text())
        result = (folder / "result.pkl").read_bytes()
        tree = (folder / "tree.tar").read_bytes()
        if meta["key"] != key or meta["result_sha256"] != _sha(result) or meta["tree_sha256"] != _sha(tree):
            raise ValueError("checksum mismatch")
        pickle.loads(result)
        return result, tree
    except (OSError, ValueError, KeyError, TypeError, pickle.UnpicklingError, EOFError, AttributeError):
        STATS["corrupt"] += 1
        shutil.rmtree(folder, ignore_errors=True)
        return None


def _write_entry(key: str, result: bytes, tree: bytes) -> None:
    folder = _entry_dir(key)
    folder.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".tmp-{key[:12]}-", dir=folder.parent))
    (tmp / "result.pkl").write_bytes(result)
    (tmp / "tree.tar").write_bytes(tree)
    (tmp / "meta.json").write_text(json.dumps({"key": key, "result_sha256": _sha(result),
                                               "tree_sha256": _sha(tree)}))
    try:
        os.rename(tmp, folder)
    except OSError:                            # another worker won the race, or a bad entry sits there
        if _read_entry(key) is None:
            shutil.rmtree(folder, ignore_errors=True)
            os.rename(tmp, folder)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def _tar(folder: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path in sorted(folder.rglob("*")):
            tar.add(path, arcname=path.relative_to(folder).as_posix(), recursive=False)
    return buffer.getvalue()


def _untar(tree: bytes, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tree), mode="r") as tar:
        tar.extractall(out_dir, filter="data")


@contextlib.contextmanager
def _lock(key: str):
    folder = cache_root() / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"{key}.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# ---------------------------------------------------------------- capture spies

def _run_capture(pdf: Path, out_dir: Path):
    """digitize_pdf with `_curves` and `readouts` instrumented (the review suite's `_captured`).

    Returns (rows, refusals, {(page, diagram): {"traces", "calibration",
    "scale", "gray", "readout_calls", "row"}}): the REAL inputs of `_curves`
    per panel and the gap lists production handed to `readouts`. Wraps the
    live module attributes, so a mutation harness patch on either is honoured.
    """
    panels: dict = {}
    current: dict = {}
    real_curves, real_readouts = rgv._curves, rgv.readouts

    def curves_spy(traces, calibration, scale, gray=None):
        current.clear()
        current.update({"traces": traces, "calibration": calibration, "scale": scale, "gray": gray,
                        "readout_calls": []})
        result = real_curves(traces, calibration, scale, gray)
        panels[len(panels)] = dict(current)
        return result

    def readouts_spy(points, log_y, gaps=None, *args, **kwargs):
        if "readout_calls" in current:
            current["readout_calls"].append({"first_vgs": points[0][0] if len(points) else None,
                                             "gaps": [list(g) for g in gaps or []]})
        return real_readouts(points, log_y, gaps, *args, **kwargs)

    with mock.patch.object(rgv, "_curves", curves_spy), mock.patch.object(rgv, "readouts", readouts_spy):
        rows, refusals = rgv.digitize_pdf(pdf, out_dir)
    keyed = {}
    for index, row in enumerate(r for r in rows if "curves" in r):
        keyed[(row["page"], row["diagram"])] = dict(panels[index], row=row)
    return rows, refusals, keyed


# ---------------------------------------------------------------- public API

def _compute(pdf: Path, mode: str, patches: list[tuple]) -> tuple[object, Path, tempfile.TemporaryDirectory]:
    OUT_ROOT.mkdir(exist_ok=True)
    tmp = tempfile.TemporaryDirectory(prefix="rdsvgs-cache-", dir=OUT_ROOT)
    out = Path(tmp.name)
    with contextlib.ExitStack() as stack:
        for module, name, replacement in patches:
            stack.enter_context(mock.patch.object(module, name, replacement))
        value = _run_capture(pdf, out) if mode == "capture" else rgv.digitize_pdf(pdf, out)
    return value, out, tmp


def _get(pdf: Path, mode: str, patches: list[tuple], out_dir: Path | None):
    pdf = Path(pdf)
    declared, excluded = [], set()
    for module, name, replacement in patches:
        fingerprint = replacement_fingerprint(replacement)
        declared.append((module.__name__, name, fingerprint))
        excluded.add((module.__name__, name))
    reasons = [] if enabled() else ["DSDIG_TEST_CACHE=0"]
    if not reasons:
        reasons += [f"patch {m}.{n} is not fingerprintable" for m, n, f in declared if f is None]
        reasons += runtime_deviations(excluded)
    if reasons:
        STATS["uncached"] += 1
        LAST_UNCACHED_REASON[:] = reasons
        UNCACHED_REASONS[reasons[0]] = UNCACHED_REASONS.get(reasons[0], 0) + 1
        value, out, tmp = _compute(pdf, mode, patches)
        with tmp:
            if out_dir is not None:
                shutil.copytree(out, out_dir, dirs_exist_ok=True)
        return value
    key = cache_key(pdf, mode, declared)
    entry = _MEMORY.get(key)
    if entry is not None:
        STATS["memory_hit"] += 1
    else:
        entry = _read_entry(key)
        if entry is not None:
            STATS["disk_hit"] += 1
        else:
            with _lock(key):
                entry = _read_entry(key)                    # another worker may have finished it
                if entry is not None:
                    STATS["disk_hit"] += 1
                else:
                    value, out, tmp = _compute(pdf, mode, patches)
                    with tmp:
                        entry = (pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL), _tar(out))
                        # modules imported lazily during the run are checked too
                        late = runtime_deviations(excluded)
                        if late:
                            STATS["uncached"] += 1
                            LAST_UNCACHED_REASON[:] = late
                            UNCACHED_REASONS[late[0]] = UNCACHED_REASONS.get(late[0], 0) + 1
                            if out_dir is not None:
                                shutil.copytree(out, out_dir, dirs_exist_ok=True)
                            return value
                        _write_entry(key, *entry)
                        STATS["computed_stored"] += 1
        _MEMORY[key] = entry
    if out_dir is not None:
        _untar(entry[1], Path(out_dir))
    return pickle.loads(entry[0])


def digitize_pdf(pdf: Path, out_dir: Path | None = None, patches: list[tuple] = ()) -> tuple[list[dict], list[dict]]:
    """``rgv.digitize_pdf(pdf, out_dir)``, from the cache when provably identical.

    ``patches``: [(module, attribute, replacement)] applied for this run and
    keyed on (a self-contained replacement only; otherwise uncached).
    ``out_dir``: when given, receives the full output tree, as a live run
    would write it. Every call returns fresh objects.
    """
    return _get(pdf, "plain", list(patches), out_dir)


def captured(pdf: Path) -> dict:
    """The per-panel `_curves` inputs and `readouts` gap lists of a digitization (see `_run_capture`)."""
    return _get(pdf, "capture", [], None)[2]


# ---------------------------------------------------------------- xdist grouping

def pdf_stems() -> set[str]:
    return {p.stem for p in DS.glob("*.pdf")} if DS.is_dir() else set()
