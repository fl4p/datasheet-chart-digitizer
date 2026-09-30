"""Regression against the human-verified RDS(on)-vs-VGS golden panels.

Fab checked the v3 overlays by eye on 2026-09-28. The 11 panels he did not flag are
frozen in tests/fixtures/rds_vgs_golden/ (see PROVENANCE.md there). Each test
re-digitizes one panel from its source PDF and compares it with the frozen output,
using the tolerances below. A deliberate change is accepted only through an entry in
REBLESSED.json. A missing PDF, or one whose SHA-256 differs, makes the test SKIP LOUDLY:
it never passes.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np
from unittest.mock import patch

import rds_digitize_cache as dcache

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "rds_vgs_golden"
DS = Path("/Users/fab/dev/ee/solar-charger-eval/ds")
OUT_ROOT = Path(__file__).resolve().parents[1] / "out"

POINT_TOL_PX = 0.5          # nearest-point distance in crop pixels, both directions
READOUT_REL_TOL = 0.002     # 0.2 % relative
TICK_TOL_PX = 0.3           # tick pixel, and fit agreement at every golden tick

_CACHE: dict[str, list[dict]] = {}
_HASH_OK: dict[str, bool] = {}


def golden_parts() -> list[str]:
    return sorted(p.parent.name for p in GOLDEN.glob("*/panel.json"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve(node, path: str):
    *head, last = [int(k) if k.isdigit() else k for k in path.split("/")]
    for key in head:
        node = node[key]
    return node, last


def pending_parts() -> set[str]:
    return set(pending_entries())


def pending_entries() -> dict[str, dict]:
    path = GOLDEN / "PENDING.json"
    return {e["part"]: e for e in json.loads(path.read_text())["entries"]} if path.exists() else {}


def _panel_field(panel: dict, name: str, served: bool):
    """A panel-level field, from the frozen fixture or from the served row."""
    if name == "status":
        return panel["status"]
    if name == "validation_verdict":
        return panel["validation"]["verdict"] if served else panel["validation_verdict"]
    if name == "anchor_verdicts":
        return ([[a["row"]["vgs_v"], a["verdict"]] for a in panel["validation"]["anchors"]] if served
                else [[a["vgs_v"], a["verdict"]] for a in panel["anchor_verdicts"]])
    raise KeyError(name)


def load_golden(part: str) -> dict:
    """The frozen panel with every explicit re-blessing applied.

    A "refreeze" entry (Fab re-verified a changed panel) points at a newer
    fixture in <part>/<dir>/. It is honoured only while the files it
    supersedes still hash as recorded; path entries listed before it applied
    to the superseded fixture and are skipped."""
    entries = [e for e in json.loads((GOLDEN / "REBLESSED.json").read_text())["entries"] if e["part"] == part]
    base = GOLDEN / part
    refreezes = [i for i, e in enumerate(entries) if e.get("kind") == "refreeze"]
    if refreezes:
        last = entries[refreezes[-1]]
        for name, digest in last["superseded_sha256"].items():
            if _sha256(base / name) != digest:
                raise AssertionError(f"refreeze of {part} to {last['dir']}: {name} no longer hashes as superseded "
                                     f"-- the entry is stale")
        base = base / last["dir"]
        entries = entries[refreezes[-1] + 1:]
    panel = json.loads((base / "panel.json").read_text())
    for curve in panel["curves"]:
        rows = (base / curve["points_csv"]).read_text().splitlines()[1:]
        curve["points_px"] = [tuple(float(v) for v in r.split(",")[2:4]) for r in rows]
    for entry in entries:
        parent, key = _resolve(panel, entry["path"])
        if parent[key] != entry["frozen"]:
            raise AssertionError(f"re-blessing {entry['path']} for {part}: frozen value is {parent[key]!r}, "
                                 f"entry says {entry['frozen']!r} -- the entry is stale")
        parent[key] = entry["now"]
    return panel


def _digitize(pdf: Path) -> list[dict]:
    key = str(pdf)
    if key not in _CACHE:
        _CACHE[key], _ = dcache.digitize_pdf(pdf)
    return _CACHE[key]


def _nearest(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """For each row of a, the distance to the nearest row of b."""
    out = np.empty(len(a))
    for start in range(0, len(a), 512):
        chunk = a[start:start + 512]
        out[start:start + 512] = np.sqrt(((chunk[:, None, :] - b[None, :, :]) ** 2).sum(-1)).min(1)
    return out


class GoldenTests(unittest.TestCase):
    """One test per human-verified panel (generated below)."""

    def _check(self, part: str) -> None:
        golden = load_golden(part)
        pdf = DS / golden["pdf_name"]
        if not pdf.is_file():
            message = f"GOLDEN NOT CHECKED: {part}: source PDF missing at {pdf}"
            print("\n*** " + message, file=sys.stderr)
            self.skipTest(message)
        if str(pdf) not in _HASH_OK:
            _HASH_OK[str(pdf)] = _sha256(pdf) == golden["pdf_sha256"]
        if not _HASH_OK[str(pdf)]:
            message = f"GOLDEN NOT CHECKED: {part}: {pdf} SHA-256 differs from the frozen {golden['pdf_sha256']}"
            print("\n*** " + message, file=sys.stderr)
            self.skipTest(message)
        rows = [r for r in _digitize(pdf) if r["page"] == golden["page"] and str(r["diagram"]) == str(golden["diagram"])]
        self.assertEqual(len(rows), 1, f"{part}: panel p{golden['page']} fig {golden['diagram']} not produced")
        row = rows[0]
        self._check_calibration(part, row, golden)
        pending = pending_entries().get(part)
        if pending is not None:
            self._check_ink_only(part, row, golden)
            changed = pending.get("changed_fields")
            if changed is not None:
                self._check_pending_fields(part, row, golden, changed)
            return
        self._check_served(part, row, golden)

        # panel verdicts
        self.assertEqual(row["status"], golden["status"], f"{part}: status")
        self.assertEqual(row["validation"]["verdict"], golden["validation_verdict"], f"{part}: validation")
        self.assertEqual([(a["row"]["vgs_v"], a["verdict"]) for a in row["validation"]["anchors"]],
                         [(a["vgs_v"], a["verdict"]) for a in golden["anchor_verdicts"]], f"{part}: anchors")

    def _check_pending_fields(self, part: str, row: dict, golden: dict, changed: dict) -> None:
        """A pending entry that names its changed fields (batch_all v2 onward)
        pins everything else as for a verified panel: the served curves in
        full unless "curves" is named, and every panel field it does not name.
        A named field must still hold its frozen value in the fixture (else
        the entry is stale) and must now serve exactly the entry's new value."""
        if "curves" not in changed:
            self._check_served(part, row, golden)
        elif changed["curves"].get("kind") == "points_added":
            # only served points were ADDED (F-v2-1 heads): everything else about
            # the curves stays pinned -- count, labels, flags and every readout
            self._check_curves_except_points(part, row, golden)
        for name in ("status", "validation_verdict", "anchor_verdicts"):
            got = _panel_field(row, name, served=True)
            frozen = _panel_field(golden, name, served=False)
            if name not in changed:
                self.assertEqual(got, frozen, f"{part}: {name} changed but PENDING.json does not name it")
                continue
            self.assertEqual(frozen, changed[name]["frozen"], f"{part}: PENDING {name} 'frozen' is stale")
            self.assertEqual(got, changed[name]["now"], f"{part}: {name} is not the PENDING 'now' value")

    def _check_curves_except_points(self, part: str, row: dict, golden: dict) -> None:
        self.assertEqual(len(row["curves"]), len(golden["curves"]), f"{part}: curve count")
        for new, old in zip(row["curves"], golden["curves"]):
            where = f"{part} c{old['curve_index']}"
            for key in ("label", "temperature_c", "temperature_kind", "id_a", "usable"):
                self.assertEqual(new.get(key, True if key == "usable" else None), old[key], f"{where}: {key}")
            new_reads = {r["vgs_v"]: r for r in new["readouts"]}
            for read in old["readouts"]:
                got = new_reads[read["vgs_v"]]
                self.assertEqual(got["status"], read["status"], f"{where}: state at {read['vgs_v']} V")
                if read["rds_mohm"] is not None:
                    self.assertLessEqual(abs(got["rds_mohm"] - read["rds_mohm"]) / abs(read["rds_mohm"]), READOUT_REL_TOL,
                                         f"{where}: {read['vgs_v']} V")

    def _check_calibration(self, part: str, row: dict, golden: dict) -> None:
        for axis in ("x_axis", "y_axis"):
            new, old = row["calibration"][axis], golden["calibration"][axis]
            self.assertEqual(new["model"], old["model"], f"{part}: {axis} model")
            self.assertEqual(sorted(t["value"] for t in new["ticks"]), sorted(t["value"] for t in old["ticks"]),
                             f"{part}: {axis} used ticks")
            new_px = {t["value"]: t["pixel"] for t in new["ticks"]}
            for tick in old["ticks"]:
                self.assertLessEqual(abs(new_px[tick["value"]] - tick["pixel"]), TICK_TOL_PX, f"{part}: {axis} tick {tick}")
                drift = abs((new["m"] * tick["pixel"] + new["b"]) - (old["m"] * tick["pixel"] + old["b"])) / abs(old["m"])
                self.assertLessEqual(drift, TICK_TOL_PX, f"{part}: {axis} fit drifts {drift:.3f} px at {tick}")

    def _check_ink_only(self, part: str, row: dict, golden: dict) -> None:
        """A golden pending re-blessing (PENDING.json): its labels and curve
        grouping may change, its INK may not. Every golden point is still
        served (by some curve); every served point is a golden point or a
        point the row tracker added on a steep head (F4-1, listed per curve
        in row_traced_points_px), at the right frame (round 5,
        frame_traced_points_px) or in an unsampled stretch (F6-1,
        gap_traced_points_px); every read value is a golden read value."""
        old_pts = np.asarray([p for c in golden["curves"] for p in c["points_px"]], dtype=float)
        new_pts = np.asarray([p for c in row["curves"] for p in c["points_px"]], dtype=float)
        added = np.asarray([p for c in row["curves"]
                            for p in c.get("row_traced_points_px", []) + c.get("frame_traced_points_px", [])
                            + c.get("gap_traced_points_px", [])]
                           or np.zeros((0, 2)), dtype=float)
        lost = _nearest(old_pts, new_pts)
        self.assertLessEqual(lost.max(), POINT_TOL_PX, f"{part}: golden point {old_pts[int(lost.argmax())]} is no longer served")
        extra = _nearest(new_pts, old_pts)
        stray = new_pts[extra > POINT_TOL_PX]
        if len(stray):
            self.assertTrue(len(added), f"{part}: {len(stray)} new points and none row-traced")
            unexplained = stray[_nearest(stray, added) > 0.01]
            self.assertEqual(len(unexplained), 0, f"{part}: new points not from the row tracker: {unexplained[:3]}")
        golden_reads = [(r["vgs_v"], r["rds_mohm"]) for c in golden["curves"] for r in c["readouts"] if r["rds_mohm"] is not None]
        for curve in row["curves"]:
            for read in curve["readouts"]:
                if read["rds_mohm"] is None:
                    continue
                self.assertTrue(any(v == read["vgs_v"] and abs(read["rds_mohm"] - g) <= READOUT_REL_TOL * abs(g)
                                    for v, g in golden_reads), f"{part} c{curve['curve_index']}: {read} is not a golden reading")

    def _check_served(self, part: str, row: dict, golden: dict) -> None:
        # curves
        self.assertEqual(len(row["curves"]), len(golden["curves"]), f"{part}: curve count")
        for new, old in zip(row["curves"], golden["curves"]):
            where = f"{part} c{old['curve_index']}"
            for key in ("label", "temperature_c", "temperature_kind", "id_a", "usable"):
                self.assertEqual(new.get(key, True if key == "usable" else None), old[key], f"{where}: {key}")
            a = np.asarray(old["points_px"], dtype=float)
            b = np.asarray(new["points_px"], dtype=float)
            self.assertTrue(len(a) and len(b), f"{where}: empty")
            lost, extra = _nearest(a, b), _nearest(b, a)
            self.assertLessEqual(lost.max(), POINT_TOL_PX,
                                 f"{where}: golden point {a[int(lost.argmax())]} has no new point within {POINT_TOL_PX} px "
                                 f"({int((lost > POINT_TOL_PX).sum())} of {len(a)})")
            self.assertLessEqual(extra.max(), POINT_TOL_PX,
                                 f"{where}: new point {b[int(extra.argmax())]} is not a golden point "
                                 f"({int((extra > POINT_TOL_PX).sum())} of {len(b)})")
            new_reads = {r["vgs_v"]: r for r in new["readouts"]}
            self.assertEqual(sorted(new_reads), sorted(r["vgs_v"] for r in old["readouts"]), f"{where}: readout targets")
            for read in old["readouts"]:
                got = new_reads[read["vgs_v"]]
                self.assertEqual(got["status"], read["status"], f"{where}: state at {read['vgs_v']} V")
                if read["rds_mohm"] is not None:
                    rel = abs(got["rds_mohm"] - read["rds_mohm"]) / abs(read["rds_mohm"])
                    self.assertLessEqual(rel, READOUT_REL_TOL, f"{where}: {read['vgs_v']} V {got['rds_mohm']} vs {read['rds_mohm']}")


class RefreezeTests(unittest.TestCase):
    """A refreeze entry must go stale, never silently pass, when a file it
    supersedes changes; and it must actually redirect to the newer fixture."""

    def test_refreeze_redirects_and_goes_stale(self) -> None:
        import shutil
        import tempfile
        global GOLDEN
        refrozen = [e["part"] for e in json.loads((GOLDEN / "REBLESSED.json").read_text())["entries"]
                    if e.get("kind") == "refreeze"]
        self.assertTrue(refrozen, "no refreeze entry to test")
        part = refrozen[0]
        saved = GOLDEN
        OUT_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=OUT_ROOT) as tmp:
            copy = Path(tmp) / "golden"
            shutil.copytree(saved, copy)
            GOLDEN = copy
            try:
                entry = [e for e in json.loads((copy / "REBLESSED.json").read_text())["entries"]
                         if e["part"] == part and e.get("kind") == "refreeze"][-1]
                redirected = load_golden(part)
                self.assertEqual(redirected, _plain(copy / part / entry["dir"], redirected),
                                 f"{part}: refreeze did not load <part>/{entry['dir']}")
                (copy / part / "panel.json").write_text((copy / part / "panel.json").read_text() + " ")
                with self.assertRaisesRegex(AssertionError, "stale"):
                    load_golden(part)
            finally:
                GOLDEN = saved


class PendingFieldsTests(unittest.TestCase):
    """A PENDING entry that names its changed fields must pin them, and pin the rest."""

    def test_named_fields_must_serve_the_new_value_and_the_rest_stay(self) -> None:
        golden = {"status": "review_required", "validation_verdict": "not_evaluable",
                  "anchor_verdicts": [{"vgs_v": 4.5, "verdict": "not_evaluable"}]}
        changed = {"validation_verdict": {"frozen": "not_evaluable", "now": "consistent"}}
        served = {"status": "review_required",
                  "validation": {"verdict": "consistent", "anchors": [{"row": {"vgs_v": 4.5}, "verdict": "not_evaluable"}]}}
        case = GoldenTests()
        with patch.object(GoldenTests, "_check_served", lambda *a: None):
            case._check_pending_fields("X", served, golden, {**changed, "curves": {}})        # passes
            for bad in ({"validation": {**served["validation"], "verdict": "inconsistent"}},    # not the "now" value
                        {"status": "ok"}):                                                         # an unnamed field moved
                with self.assertRaises(AssertionError):
                    case._check_pending_fields("X", {**served, **bad}, golden, {**changed, "curves": {}})
            with self.assertRaises(AssertionError):                                                # stale "frozen"
                case._check_pending_fields("X", served, golden,
                                           {"validation_verdict": {"frozen": "verified", "now": "consistent"}, "curves": {}})


def _plain(base: Path, like: dict) -> dict:
    panel = json.loads((base / "panel.json").read_text())
    for curve in panel["curves"]:
        rows = (base / curve["points_csv"]).read_text().splitlines()[1:]
        curve["points_px"] = [tuple(float(v) for v in r.split(",")[2:4]) for r in rows]
    return panel


for _part in golden_parts():
    setattr(GoldenTests, f"test_golden_{_part}", lambda self, part=_part: self._check(part))


if __name__ == "__main__":
    unittest.main()
