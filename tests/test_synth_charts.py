"""tools/synth_charts: GT geometry, tick formats, determinism, and the self-checks on a tiny set."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("matplotlib")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.synth_charts import build, validate  # noqa: E402
from tools.synth_charts import gt as G  # noqa: E402
from tools.synth_charts.axes import fmt_tick  # noqa: E402

pytestmark = pytest.mark.xdist_group("synth_charts")


def test_clip_is_exact_at_the_frame():
    uv = np.array([[-0.5, 0.5], [0.5, 0.5], [0.5, 1.5]])
    (piece,) = G.clip_polyline(uv)
    assert np.allclose(piece, [[0, 0.5], [0.5, 0.5], [0.5, 1.0]])


def test_clip_splits_reentering_curve():
    uv = np.array([[0.1, 0.5], [0.5, 1.5], [0.9, 0.5]])
    assert len(G.clip_polyline(uv)) == 2


def test_douglas_peucker_error_bound():
    x = np.linspace(0, 1, 2000)
    uv = np.c_[x, 0.5 + 0.3 * np.sin(7 * x)]
    d = G.douglas_peucker(uv, 2e-6)
    assert len(d) < len(uv) / 2
    assert G.seg_dist(uv, d).max() <= 2e-6 + 1e-12


@pytest.mark.parametrize("v,style,log,want", [
    (10, "plain", True, "10"), (100, "plain", True, "100"), (0.01, "plain", True, "0.01"),
    (1e4, "si", True, "10k"), (1e-9, "plain", True, "$10^{-9}$"), (0.5, "comma", False, "0,5"),
    (100, "sci_e", True, "1E+2"), (0.01, "sci_e2", True, "1.0E-02"), (20, "plain", False, "20"),
])
def test_tick_formats(v, style, log, want):
    assert fmt_tick(v, style, log=log) == want


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    out = tmp_path_factory.mktemp("synth")
    build.generate(out / "a", 6, 21, "t", log=lambda *_: None)
    build.generate(out / "b", 6, 21, "t", log=lambda *_: None)
    return out


def _strip(cases):
    for c in cases:
        c["gt_provenance"].pop("generator_rev", None)
    return cases


def test_generation_is_deterministic(tiny):
    a = _strip(json.loads((tiny / "a" / "cases.json").read_text()))
    b = _strip(json.loads((tiny / "b" / "cases.json").read_text()))
    assert a == b
    ia = sorted((tiny / "a" / "img").glob("*.png"))
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in ia] == \
        [hashlib.sha256((tiny / "b" / "img" / p.name).read_bytes()).hexdigest() for p in ia]


def test_self_checks_pass_on_clean_set(tiny):
    root = tiny / "a"
    cases = json.loads((root / "cases.json").read_text())
    assert all(r["status"] == "pass" for r in validate.inframe_check(cases))
    assert all(r["status"] == "pass" for r in validate.tick_check(root, cases))
    vec = validate.vector_check(root, cases)
    assert all(r["status"] in ("pass", "skip") for r in vec)
    assert any(r["status"] == "pass" for r in vec)


def test_vector_check_catches_shifted_frame(tiny):
    root = tiny / "a"
    cases = json.loads((root / "cases.json").read_text())
    for c in cases:
        b = c["gt_provenance"]["plot_box_pt"]
        c["gt_provenance"]["plot_box_pt"] = [b[0] + 0.5, b[1], b[2] + 0.5, b[3]]
    vec = [r for r in validate.vector_check(root, cases) if r["status"] != "skip"]
    assert vec and all(r["status"] == "FAIL" for r in vec)
