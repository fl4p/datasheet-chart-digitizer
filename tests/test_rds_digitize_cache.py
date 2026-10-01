"""The RDS(VGS) test digitize cache (tests/rds_digitize_cache.py) must never serve a stale result.

Known-bad calibration for each guard: a changed source byte, every mutant
of the mutation harness, a corrupt entry, an unkeyable patch. Scratch output
goes under the repo's gitignored ``out/`` -- never /tmp.
"""

from __future__ import annotations

import contextlib
import filecmp
import os
import pickle
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rds_digitize_cache as dcache
from datasheet_chart_digitizer import rdson_gate_voltage as rgv
from datasheet_chart_digitizer import rdson_gate_voltage_traces as traces_mod

REPO = Path(__file__).resolve().parents[1]
OUT_ROOT = REPO / "out"
FAST_PDF = dcache.DS / "CSD17306Q5A_TI.pdf"      # vector, ~1 s


def _scratch(prefix: str) -> tempfile.TemporaryDirectory:
    OUT_ROOT.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=OUT_ROOT)


class SourceHashTests(unittest.TestCase):
    def test_one_changed_source_byte_changes_the_hash(self):
        with _scratch("dcache-src-") as tmp:
            copy = Path(tmp) / "src"
            shutil.copytree(dcache.SRC_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
            base, _ = dcache.source_hash(copy)
            self.assertEqual(base, dcache.source_hash(dcache.SRC_DIR)[0])   # content, not location
            target = copy / dcache.PKG / "rdson_gate_voltage_traces.py"
            data = bytearray(target.read_bytes())
            index = data.index(b"GAP_MAX_MISS")
            data[index] = ord("g")
            target.write_bytes(bytes(data))
            self.assertNotEqual(dcache.source_hash(copy)[0], base)
            target.write_bytes(bytes(data).replace(b"gAP_MAX_MISS", b"GAP_MAX_MISS", 1))
            self.assertEqual(dcache.source_hash(copy)[0], base)
            (copy / dcache.PKG / "new_module.py").write_text("X = 1\n")   # an added file counts too
            self.assertNotEqual(dcache.source_hash(copy)[0], base)


class RuntimeCheckTests(unittest.TestCase):
    """The mutation harness patches in memory; the cache must see every mutant."""

    def test_the_unpatched_package_is_pristine(self):
        self.assertEqual(dcache.runtime_deviations(), [])

    def test_every_harness_mutant_is_seen(self):
        sys.path.insert(0, str(REPO / "tools"))
        try:
            import rds_vgs_mutation_check as harness
        finally:
            sys.path.remove(str(REPO / "tools"))
        mutants = list(harness.MUTANTS.items()) + list(harness.EQUIVALENT_ON_REAL_DATA.items())
        self.assertGreater(len(mutants), 100)
        unseen = []
        for label, (patches, _names) in mutants:
            with contextlib.ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                if not dcache.runtime_deviations():
                    unseen.append(label)
        self.assertEqual(unseen, [])
        self.assertEqual(dcache.runtime_deviations(), [])

    def test_constant_function_class_and_started_patches_are_seen(self):
        cases = [
            patch.object(rgv, "GAP_PX", rgv.GAP_PX + 1),
            patch.object(rgv, "GAP_PX", float(rgv.GAP_PX) if isinstance(rgv.GAP_PX, int) else int(rgv.GAP_PX)),
            patch.object(traces_mod, "_boxes_touch", lambda *a, **k: False),
            patch.object(traces_mod.Trace, "__repr__", lambda self: "x"),
            patch.object(rgv, "brand_new_name", 1, create=True),
        ]
        for case in cases:
            with case:
                self.assertTrue(dcache.runtime_deviations(), case)
        started = patch.object(os, "getcwd", os.getcwd)      # not even the package: any .start()
        started.start()
        try:
            self.assertTrue(dcache.runtime_deviations())
        finally:
            started.stop()
        self.assertEqual(dcache.runtime_deviations(), [])


class ModuleLevelInstanceTests(unittest.TestCase):
    """transfer_anchor_batch.ANCHORS holds instances of a dataclass the module
    defines; the reference execution builds its own class, so the instances are
    compared field by field (seen 2026-09-30 on the merge with main: every
    lookup refused). A changed field, an extra attribute or a lookalike class
    must still differ."""

    def setUp(self):
        from datasheet_chart_digitizer import transfer_anchor_batch
        self.module = transfer_anchor_batch

    def test_every_package_module_is_pristine_when_imported(self):
        import importlib
        import pkgutil
        import datasheet_chart_digitizer as package
        for info in pkgutil.iter_modules(package.__path__):
            importlib.import_module(f"{package.__name__}.{info.name}")
        self.assertEqual(dcache.runtime_deviations(), [])

    def _with_anchors(self, anchors):
        with patch.object(self.module, "ANCHORS", anchors):
            return dcache.runtime_deviations()

    def test_an_equal_copy_is_pristine(self):
        import copy
        self.assertEqual(self._with_anchors(copy.deepcopy(self.module.ANCHORS)), [])

    def test_a_changed_field_differs(self):
        import dataclasses
        first, *rest = self.module.ANCHORS
        for field, value in (("vpl_v", first.vpl_v + 0.1), ("part", first.part + "X"),
                             ("qg_th_nc", 1.0 if first.qg_th_nc is None else None)):
            changed = dataclasses.replace(first, **{field: value})
            self.assertTrue(self._with_anchors((changed, *rest)), field)
        self.assertTrue(self._with_anchors(tuple(rest)))                       # one dropped
        self.assertTrue(self._with_anchors((*rest, first)))                    # reordered

    def test_an_extra_attribute_or_a_lookalike_differs(self):
        import copy
        import dataclasses
        import types as types_mod
        first, *rest = self.module.ANCHORS
        extra = copy.copy(first)
        object.__setattr__(extra, "note", "forged")
        self.assertTrue(self._with_anchors((extra, *rest)))
        lookalike = types_mod.SimpleNamespace(**dataclasses.asdict(first))
        self.assertTrue(self._with_anchors((lookalike, *rest)))
        fields = [(f.name, f.type, f) for f in dataclasses.fields(first)]
        Clone = dataclasses.make_dataclass("AnchorEvidence", fields, frozen=True)
        Clone.__module__ = self.module.__name__
        self.assertTrue(self._with_anchors((Clone(**dataclasses.asdict(first)), *rest)))
        self.assertEqual(dcache.runtime_deviations(), [])


class PicklingTests(unittest.TestCase):
    """Capture entries pickle Trace/Calibration/PlotBox/...; copyreg then writes
    __slotnames__ onto those classes. Seen 2026-09-29: once a worker had pickled a
    capture, the runtime check called five classes changed and every later
    lookup ran live."""

    def test_pickling_and_copying_package_objects_keeps_it_pristine(self):
        import copy
        from datasheet_chart_digitizer.capacitance_types import PlotBox
        pickle.dumps(PlotBox(0, 0, 1, 1))
        copy.deepcopy(traces_mod.Trace([(0.0, 0.0)], "raster"))
        self.assertIn("__slotnames__", vars(PlotBox))
        self.assertEqual(dcache.runtime_deviations(), [])

    def test_a_forged_slotnames_still_differs(self):
        from datasheet_chart_digitizer.capacitance_types import PlotBox
        pickle.dumps(PlotBox(0, 0, 1, 1))
        saved = vars(PlotBox)["__slotnames__"]
        PlotBox.__slotnames__ = ["x0"]
        try:
            self.assertTrue(dcache.runtime_deviations())
        finally:
            PlotBox.__slotnames__ = saved
        self.assertEqual(dcache.runtime_deviations(), [])


class DeclaredPatchTests(unittest.TestCase):
    def test_self_contained_replacements_are_keyed_by_code(self):
        a = dcache.replacement_fingerprint(lambda *a, **k: [])
        self.assertIsNotNone(a)
        self.assertEqual(a, dcache.replacement_fingerprint(lambda *a, **k: []))
        self.assertNotEqual(a, dcache.replacement_fingerprint(lambda *a, **k: [0]))
        self.assertNotEqual(a, dcache.replacement_fingerprint(lambda *a, **k: None))
        self.assertIsNotNone(dcache.replacement_fingerprint(lambda traces_, *a, **k: traces_))

    def test_replacements_that_depend_on_outside_state_are_not_keyed(self):
        depth = 3.0
        self.assertIsNone(dcache.replacement_fingerprint(lambda: depth))                 # closure
        self.assertIsNone(dcache.replacement_fingerprint(lambda *a: rgv.GAP_PX))          # a global
        self.assertIsNone(dcache.replacement_fingerprint(lambda x=[]: x))                # mutable default
        self.assertIsNone(dcache.replacement_fingerprint(rgv.readouts.__call__))         # not a function


@unittest.skipUnless(FAST_PDF.is_file(), f"needs {FAST_PDF}")
class EntryTests(unittest.TestCase):
    # Plain save/restore, not patch.dict(...).start(): a started patch makes the
    # cache (rightly) refuse to serve anything.
    def setUp(self):
        self._tmp = _scratch("dcache-root-")
        self.root = Path(self._tmp.name)
        self._env = {k: os.environ.get(k) for k in ("DSDIG_TEST_CACHE_DIR", "DSDIG_TEST_CACHE")}
        os.environ.update({"DSDIG_TEST_CACHE_DIR": str(self.root), "DSDIG_TEST_CACHE": "1"})
        self._memory = dict(dcache._MEMORY)
        dcache._MEMORY.clear()                             # every lookup below goes to disk

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        dcache._MEMORY.clear()
        dcache._MEMORY.update(self._memory)
        self._tmp.cleanup()

    def _entries(self):
        return sorted(p for p in (self.root / "digitize").glob("*/*") if not p.name.startswith(".tmp"))

    def test_a_hit_equals_a_live_run_including_the_output_tree(self):
        with _scratch("dcache-live-") as live_dir, _scratch("dcache-a-") as a_dir, _scratch("dcache-b-") as b_dir:
            live = rgv.digitize_pdf(FAST_PDF, Path(live_dir))
            before = dict(dcache.STATS)
            first = dcache.digitize_pdf(FAST_PDF, Path(a_dir))
            dcache._MEMORY.clear()
            second = dcache.digitize_pdf(FAST_PDF, Path(b_dir))
            self.assertEqual(dcache.STATS["computed_stored"] - before["computed_stored"], 1)
            self.assertEqual(dcache.STATS["disk_hit"] - before["disk_hit"], 1)
            self.assertEqual(pickle.dumps(first), pickle.dumps(live))
            self.assertEqual(pickle.dumps(second), pickle.dumps(live))
            for out in (a_dir, b_dir):
                cmp = filecmp.dircmp(live_dir, out)
                self.assertEqual((cmp.left_only, cmp.right_only), ([], []))
                for sub in [live_dir] + [str(p) for p in Path(live_dir).rglob("*") if p.is_dir()]:
                    rel = Path(sub).relative_to(live_dir)
                    names = [p.name for p in Path(sub).iterdir() if p.is_file()]
                    _match, mismatch, errors = filecmp.cmpfiles(sub, Path(out) / rel, names, shallow=False)
                    self.assertEqual((mismatch, errors), ([], []), rel)
        # every call returns fresh objects: a test mutating a row cannot leak into another
        again = dcache.digitize_pdf(FAST_PDF)
        again[0][0]["status"] = "tampered"
        self.assertNotEqual(dcache.digitize_pdf(FAST_PDF)[0][0]["status"], "tampered")

    def test_lookups_after_a_pickled_capture_still_hit(self):
        dcache.captured(FAST_PDF)                          # pickles Trace, Calibration, ...
        dcache.digitize_pdf(FAST_PDF)
        before = dict(dcache.STATS)
        dcache.digitize_pdf(FAST_PDF)
        dcache._MEMORY.clear()
        dcache.digitize_pdf(FAST_PDF)
        self.assertEqual(dcache.STATS["uncached"], before["uncached"], dcache.LAST_UNCACHED_REASON)
        self.assertEqual(dcache.STATS["memory_hit"] - before["memory_hit"], 1)
        self.assertEqual(dcache.STATS["disk_hit"] - before["disk_hit"], 1)

    def test_capture_binds_both_copies_before_duplicate_selection(self):
        captured = dcache.captured(FAST_PDF)
        self.assertEqual(set(captured), {(1, "t519"), (4, "7")})
        for identity, cap in captured.items():
            self.assertEqual((cap["row"]["page"], cap["row"]["diagram"]), identity)
            plot = cap["calibration"].plot
            self.assertEqual(cap["row"]["plot_box_px"], {k: getattr(plot, k) for k in ("x0", "y0", "x1", "y1")})

    def test_corrupt_or_partial_entries_are_recomputed(self):
        dcache.digitize_pdf(FAST_PDF)
        (entry,) = self._entries()
        good = pickle.dumps(dcache.digitize_pdf(FAST_PDF))
        damages = [
            lambda: (entry / "result.pkl").write_bytes((entry / "result.pkl").read_bytes()[:-100]),
            lambda: (entry / "tree.tar").write_bytes(b""),
            lambda: (entry / "meta.json").unlink(),
            lambda: (entry / "meta.json").write_text("{"),
        ]
        for damage in damages:
            dcache._MEMORY.clear()
            damage()
            before = dict(dcache.STATS)
            self.assertEqual(pickle.dumps(dcache.digitize_pdf(FAST_PDF)), good)
            self.assertEqual(dcache.STATS["corrupt"] - before["corrupt"], 1)
            self.assertEqual(dcache.STATS["computed_stored"] - before["computed_stored"], 1)
            self.assertEqual(len(self._entries()), 1)

    def test_a_patched_run_neither_reads_nor_writes(self):
        dcache.digitize_pdf(FAST_PDF)                      # an unpatched entry exists
        self.assertEqual(len(self._entries()), 1)
        before = dict(dcache.STATS)
        with patch.object(rgv, "vector_traces", lambda page, transform, plot: ([], [], [])):
            results, _ = dcache.digitize_pdf(FAST_PDF)
        self.assertEqual(dcache.STATS["uncached"] - before["uncached"], 1)
        self.assertEqual(dcache.STATS["disk_hit"], before["disk_hit"])
        self.assertEqual(len(self._entries()), 1)
        panel = next(r for r in results if r["diagram"] == "7")
        self.assertNotEqual(panel["status"], "ok")        # the mutant really ran

    def test_a_declared_patch_is_its_own_entry(self):
        off = (traces_mod, "bind_by_order_rule", lambda *a, **k: [])
        dcache.digitize_pdf(FAST_PDF)
        dcache.digitize_pdf(FAST_PDF, patches=[off])
        self.assertEqual(len(self._entries()), 2)
        before = dict(dcache.STATS)
        depth = 1
        dcache.digitize_pdf(FAST_PDF, patches=[(traces_mod, "bind_by_order_rule", lambda *a, **k: [depth])])
        self.assertEqual(dcache.STATS["uncached"] - before["uncached"], 1)
        self.assertEqual(len(self._entries()), 2)

    def test_the_environment_switch_turns_it_off(self):
        os.environ["DSDIG_TEST_CACHE"] = "0"
        before = dict(dcache.STATS)
        dcache.digitize_pdf(FAST_PDF)
        self.assertEqual(dcache.STATS["uncached"] - before["uncached"], 1)
        self.assertEqual(self._entries(), [])


if __name__ == "__main__":
    unittest.main()
