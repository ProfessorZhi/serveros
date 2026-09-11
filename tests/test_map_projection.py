"""§15 -- maps are projections of registries, and projections are checkable.

The tests here are shaped by what went wrong repeatedly this round rather than
by coverage targets:

  * a check that reports "no drift" when it could not read its source (the
    "could not look" == "nothing found" collapse), and
  * a declaration read that silently reads a DIRTY WORKING TREE and reports the
    tree as the architecture -- the trap that misled five readers on this
    machine before it was written down.

Both get an explicit countertest, because a test that only proves the happy path
is exactly how each of them survived review the first time.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "skills" / "serveros"
SCRIPT = SKILL / "scripts" / "map_projection.py"
CONFIG = SKILL / "references" / "map-projections.json"

spec = importlib.util.spec_from_file_location("map_projection", SCRIPT)
mp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mp)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

BUNDLE = {
    "name": "hwz-server-os", "version": "9.9.9", "cachebuster": "cb",
    "skills": [{"name": "one", "source": "local"},
               {"name": "two", "source": "ability-management"}],
}
TOPOLOGY = {
    "workspace_root_children": {"src": "canonical-source"},
    "data_root_children": {"artifacts": "run-output"},
    "roles": {"canonical-source": {"root": "SRC_ROOT"},
              "run-output": {"root": "ARTIFACTS_ROOT"}},
    "data_root_children_note": ["x"],
}


def _cfg(tmp_path, targets, *, sources=None, projections=None):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["targets"] = targets
    if sources is not None:
        cfg["sources"] = sources
    if projections is not None:
        cfg["projections"] = projections
    cfg["_args"] = _ns(tmp_path)
    return cfg


class _ns:
    def __init__(self, tmp_path, **kw):
        self.serveros_repo = kw.get("serveros_repo", tmp_path / "serveros")
        self.ability_repo = kw.get("ability_repo", tmp_path / "am")
        self.ops_repo = kw.get("ops_repo", tmp_path / "ops")
        self.canonical_ref = kw.get("canonical_ref", "HEAD")
        self.ability_ref = kw.get("ability_ref", None)
        self.ops_ref = kw.get("ops_ref", None)


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True,
                                    capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "t")
    return path


def _commit(path: Path, files: dict[str, str], msg="c") -> None:
    for rel, body in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", msg],
                   check=True, capture_output=True)


# --------------------------------------------------------------------------- #
# the renderer is a pure function of the registry
# --------------------------------------------------------------------------- #

def test_rendering_is_deterministic_across_runs():
    """No timestamps, no set iteration order: two renders must be identical."""
    a = mp.render_plugin_composition(BUNDLE)
    b = mp.render_plugin_composition(dict(BUNDLE))
    assert a == b


def test_rendered_members_follow_bundle_order_not_sorted_order():
    """Composition order is a fact; sorting it would destroy one."""
    body = mp.render_plugin_composition(BUNDLE)
    assert body.index("`one`") < body.index("`two`")


def test_topology_children_are_sorted_for_diff_stability():
    topo = dict(TOPOLOGY, data_root_children={"z": "run-output", "a": "run-output"})
    body = mp.render_topology_children(topo)
    assert body.index("`a`") < body.index("`z`")


# --------------------------------------------------------------------------- #
# block extraction / comparison
# --------------------------------------------------------------------------- #

def test_extract_returns_body_between_markers():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    text = f"pre\n{mp._begin(cfg, 'plugin-composition')}\nBODY\n{mp._end(cfg, 'plugin-composition')}\npost"
    assert mp.extract_block(text, cfg, "plugin-composition") == "BODY"


def test_unterminated_begin_is_treated_as_absent_not_guessed():
    """An unmatched BEGIN cannot be bounded; guessing would swallow the file tail."""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    text = f"pre\n{mp._begin(cfg, 'plugin-composition')}\nBODY\n"
    assert mp.extract_block(text, cfg, "plugin-composition") is None


def test_trailing_whitespace_is_not_architectural_drift():
    assert mp.compare("a\nb", "a   \nb\t")["equal"] is True


def test_compare_names_the_first_differing_line():
    cmp = mp.compare("a\nb\nc", "a\nB\nc")
    assert cmp["equal"] is False and cmp["line"] == 2
    assert cmp["generated"] == "b" and cmp["present"] == "B"


# --------------------------------------------------------------------------- #
# end to end: UNBOUND / DRIFT / FRESH
# --------------------------------------------------------------------------- #

def _wire(tmp_path, map_text: str, *, bundle=None, topology=None):
    sr, am, ops = _git_repo(tmp_path / "serveros"), _git_repo(tmp_path / "am"), _git_repo(tmp_path / "ops")
    import yaml
    _commit(sr, {"plugin/bundle.yaml": yaml.safe_dump(bundle or BUNDLE)})
    _commit(am, {"abilities-manifest.yaml": yaml.safe_dump({"abilities": []})})
    _commit(ops, {"maps/X.md": map_text})
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["sources"] = {"bundle": {"repo": "serveros", "path": "plugin/bundle.yaml", "kind": "yaml"}}
    cfg["projections"] = {"plugin-composition": {"source": "bundle", "title": "t",
                                                 "renderer": "plugin_composition"}}
    cfg["targets"] = [{"repo": "hwz-ops", "path": "maps/X.md", "blocks": ["plugin-composition"]}]
    cfg["_args"] = _ns(tmp_path, serveros_repo=sr, ability_repo=am, ops_repo=ops)
    return cfg, sr, ops


def test_map_without_markers_is_unbound(tmp_path):
    cfg, _sr, _ops = _wire(tmp_path, "# X\n\nhwz-server-os is v2.0.1\n")
    rep = mp.check(cfg)
    assert [f["code"] for f in rep["findings"]] == ["MAP_VIEW_UNBOUND"]
    assert rep["targets"][0]["blocks"]["plugin-composition"]["status"] == "MAP_VIEW_UNBOUND"


def test_unbound_probe_names_the_disagreeing_version(tmp_path):
    """'block absent' is weak; the probe says the map claims 2.0.1 and the bundle says 9.9.9."""
    cfg, _sr, _ops = _wire(tmp_path, "# X\n\nhwz-server-os is v2.0.1\n")
    probe = mp.check(cfg)["findings"][0]["probe"]
    assert probe["registry_version"] == "9.9.9"
    assert "2.0.1" in probe["versions_in_map"]
    assert probe["declared_version_present"] is False


def test_correct_block_is_fresh(tmp_path):
    cfg, _sr, _ops = _wire(tmp_path, "placeholder")
    body = mp.render_plugin_composition(BUNDLE)
    cfg, _sr, _ops = _wire(tmp_path / "b", f"# X\n{mp._begin(cfg, 'plugin-composition')}\n{body}\n{mp._end(cfg, 'plugin-composition')}\n")
    rep = mp.check(cfg)
    assert rep["findings"] == []
    assert rep["targets"][0]["blocks"]["plugin-composition"]["status"] == "FRESH"


def test_stale_block_is_drift(tmp_path):
    cfg, _sr, _ops = _wire(tmp_path, "placeholder")
    stale = mp.render_plugin_composition({**BUNDLE, "version": "1.0.0"})
    cfg, _sr, _ops = _wire(tmp_path / "b", f"# X\n{mp._begin(cfg, 'plugin-composition')}\n{stale}\n{mp._end(cfg, 'plugin-composition')}\n")
    rep = mp.check(cfg)
    assert [f["code"] for f in rep["findings"]] == ["MAP_VIEW_DRIFT"]
    assert rep["findings"][0]["severity"] == "HIGH"


def test_countertest_a_changed_registry_must_turn_a_fresh_block_into_drift(tmp_path):
    """The whole mechanism in one assertion: change the SOURCE, not the map."""
    import yaml
    sr, am, ops = _git_repo(tmp_path / "serveros"), _git_repo(tmp_path / "am"), _git_repo(tmp_path / "ops")
    _commit(sr, {"plugin/bundle.yaml": yaml.safe_dump(BUNDLE)})
    _commit(am, {"abilities-manifest.yaml": yaml.safe_dump({"abilities": []})})
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["sources"] = {"bundle": {"repo": "serveros", "path": "plugin/bundle.yaml", "kind": "yaml"}}
    cfg["projections"] = {"plugin-composition": {"source": "bundle", "title": "t",
                                                 "renderer": "plugin_composition"}}
    cfg["targets"] = [{"repo": "hwz-ops", "path": "maps/X.md", "blocks": ["plugin-composition"]}]
    cfg["_args"] = _ns(tmp_path, serveros_repo=sr, ability_repo=am, ops_repo=ops)

    body = mp.render_plugin_composition(BUNDLE)
    _commit(ops, {"maps/X.md": f"# X\n{mp._begin(cfg, 'plugin-composition')}\n{body}\n{mp._end(cfg, 'plugin-composition')}\n"})
    assert mp.check(cfg)["findings"] == []

    _commit(sr, {"plugin/bundle.yaml": yaml.safe_dump({**BUNDLE, "version": "10.0.0"})}, "bump")
    codes = [f["code"] for f in mp.check(cfg)["findings"]]
    assert codes == ["MAP_VIEW_DRIFT"], "a registry change must make the map stale"


# --------------------------------------------------------------------------- #
# provenance: never the working tree, never a silent pass
# --------------------------------------------------------------------------- #

def test_declaration_read_ignores_a_dirty_worktree(tmp_path):
    """§17/§19: the map on disk is edited; the DECLARATION must not change.

    This is the invariant whose absence misled five readers: a dirty checkout
    answers confidently with the wrong architecture instead of refusing.
    """
    cfg, _sr, ops = _wire(tmp_path, "# X\n")
    (ops / "maps/X.md").write_text("# X\nEDITED IN THE WORKING TREE\n", encoding="utf-8")

    rep = mp.check(cfg)
    obs = mp.read_object(ops, "HEAD", "maps/X.md")
    assert "EDITED IN THE WORKING TREE" not in obs["content"]
    assert rep["targets"][0]["worktree_state"]["dirty"] is True
    assert rep["targets"][0]["worktree_state"]["has_active_writer"] is True


def test_unreadable_source_is_a_high_finding_not_a_pass(tmp_path):
    cfg, _sr, _ops = _wire(tmp_path, "# X\n")
    cfg["sources"] = {"bundle": {"repo": "serveros", "path": "plugin/absent.yaml", "kind": "yaml"}}
    rep = mp.check(cfg)
    codes = {f["code"] for f in rep["findings"]}
    assert "MAP_VIEW_SOURCE_UNAVAILABLE" in codes
    assert rep["targets"][0]["blocks"]["plugin-composition"]["status"] == "NO_RENDER"


def test_missing_repo_is_reported_not_raised(tmp_path):
    cfg, _sr, _ops = _wire(tmp_path, "# X\n")
    cfg["_args"] = _ns(tmp_path, serveros_repo=tmp_path / "nope",
                       ability_repo=tmp_path / "am", ops_repo=tmp_path / "ops")
    assert cfg["_args"].serveros_repo.is_dir() is False
    assert any(f["code"] == "MAP_VIEW_SOURCE_UNAVAILABLE" for f in mp.check(cfg)["findings"])


# --------------------------------------------------------------------------- #
# candidates: emitted, never applied
# --------------------------------------------------------------------------- #

def test_candidate_without_anchor_appends_and_says_so(tmp_path):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    text, placed = mp.render_candidate(cfg, "# X\n", {"plugin-composition": "BODY"},
                                       ["plugin-composition"])
    assert placed is False
    assert "NOT APPLIED" in text and "BODY" in text


def test_candidate_with_anchor_replaces_in_place_and_touches_nothing_else(tmp_path):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    text = (f"head\n{mp._begin(cfg, 'plugin-composition')}\nOLD\n"
            f"{mp._end(cfg, 'plugin-composition')}\ntail\n")
    out, placed = mp.render_candidate(cfg, text, {"plugin-composition": "NEW"}, ["plugin-composition"])
    assert placed is True
    assert out.startswith("head\n") and out.endswith("\ntail\n")
    assert "NEW" in out and "OLD" not in out


def test_cli_never_writes_a_map_in_place(tmp_path):
    """The compiler's only output path is --out, and there the map is a NEW file."""
    cfg, _sr, ops = _wire(tmp_path, "# X\nORIGINAL\n")
    cfgfile = tmp_path / "cfg.json"
    cfgfile.write_text(json.dumps({k: v for k, v in cfg.items() if k != "_args"}), encoding="utf-8")
    before = (ops / "maps/X.md").read_text(encoding="utf-8")
    out = tmp_path / "cand"
    rc = mp.main(["--config", str(cfgfile),
                  "--serveros-repo", str(cfg["_args"].serveros_repo),
                  "--ability-repo", str(cfg["_args"].ability_repo),
                  "--ops-repo", str(ops), "--out", str(out)])
    assert rc == 0
    assert (ops / "maps/X.md").read_text(encoding="utf-8") == before
    assert (out / "X.md").is_file()
    manifest = json.loads((out / "candidates.json").read_text(encoding="utf-8"))
    assert manifest["candidates"][0]["applied_in_place"] is False


def test_countertest_a_run_that_cannot_read_its_maps_must_not_exit_clean(tmp_path):
    """Zero candidates because nothing needed doing and zero because the maps
    were unreadable are different outcomes; the exit code says which."""
    cfg, _sr, _ops = _wire(tmp_path, "# X\n")
    cfgfile = tmp_path / "cfg.json"
    cfgfile.write_text(json.dumps({k: v for k, v in cfg.items() if k != "_args"}), encoding="utf-8")
    empty_ops = _git_repo(tmp_path / "empty-ops")
    _commit(empty_ops, {"README.md": "no maps here"})
    rc = mp.main(["--config", str(cfgfile),
                  "--serveros-repo", str(cfg["_args"].serveros_repo),
                  "--ability-repo", str(cfg["_args"].ability_repo),
                  "--ops-repo", str(empty_ops), "--out", str(tmp_path / "cand2")])
    assert rc == 1
    manifest = json.loads((tmp_path / "cand2" / "candidates.json").read_text(encoding="utf-8"))
    assert manifest["candidates"] == []
    assert manifest["unreadable"][0]["map"] == "maps/X.md"
