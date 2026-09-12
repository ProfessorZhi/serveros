#!/usr/bin/env python3
"""Tests for path governance and the serverosctl surface.

Hermetic: `tmp_path` builds a small root contract, and every root check runs
against it rather than against this machine. The properties under test are
the ones that decide where an agent's output is allowed to land, so they are
tested by refusal as much as by success — a resolver that always finds an
answer is the failure mode, not the goal.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


paths_mod = _load("serveros_runtime_paths", "paths.py")
cli_mod = _load("serveros_runtime_cli", "cli.py")

Paths = paths_mod.Paths
PathRefused = paths_mod.PathRefused
PathError = paths_mod.PathError


@pytest.fixture
def roots(tmp_path):
    """A complete but tiny root contract inside tmp_path."""
    home = tmp_path / "home" / "hwz"
    data = tmp_path / "data"
    workspace = data / "workspace"
    for path in (home, data / "artifacts", data / "toolchains", workspace / "src",
                 workspace / "worktrees", data / "state" / "serveros",
                 data / "logs" / "serveros"):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "home": str(home),
        "data_root": str(data),
        "workspace_root": str(workspace),
        "artifacts_root": str(data / "artifacts"),
        "toolchains_root": str(data / "toolchains"),
    }


@pytest.fixture
def paths(roots):
    return Paths(roots)


# ---------------------------------------------------------------------------
# §46 · path.resolve is deterministic
# ---------------------------------------------------------------------------


def test_resolve_is_deterministic(paths):
    first = paths.resolve("state/runtime/observed")
    for _ in range(5):
        assert paths.resolve("state/runtime/observed") == first


def test_resolve_does_not_depend_on_the_working_directory(paths, tmp_path, monkeypatch):
    """A resolver that consults cwd produces a different answer per shell."""
    import os

    before = paths.resolve("artifacts/work-items/p/I/R")
    monkeypatch.chdir(tmp_path)
    after = paths.resolve("artifacts/work-items/p/I/R")
    assert before == after
    assert before["path"].startswith("/")


def test_resolve_names_a_declared_root(paths, roots):
    result = paths.resolve("workspace/src")
    assert result["root"] == "workspace"
    assert result["path"] == str(Path(roots["workspace_root"]) / "src")


def test_resolve_refuses_an_unknown_root_and_names_the_real_ones(paths):
    with pytest.raises(PathRefused) as excinfo:
        paths.resolve("artefacts/x")
    assert "artefacts" in str(excinfo.value)
    assert "artifacts" in str(excinfo.value)  # the alternatives are listed


def test_resolve_refuses_a_path_that_is_already_absolute(paths):
    """Two ways to name a path is how the CLI and the caller end up reading
    two different worlds."""
    with pytest.raises(PathRefused, match="absolute"):
        paths.resolve("/etc/passwd")


def test_resolve_refuses_traversal(paths):
    with pytest.raises(PathRefused, match="`..`"):
        paths.resolve("artifacts/../../etc")


def test_resolve_refuses_tilde(paths):
    with pytest.raises(PathRefused):
        paths.resolve("~/files")


def test_work_item_run_rejects_a_segment_that_is_a_path(paths):
    with pytest.raises(PathRefused, match="single path segment"):
        paths.work_item_run("litellm", "../LIT-0104", "RUN-1")
    with pytest.raises(PathRefused, match="single path segment"):
        paths.work_item_run("litellm", "LIT-0104", "..")


def test_skill_run_builds_the_canonical_shape(paths, roots):
    result = paths.skill_run("runtime-management", "RUN-001")
    assert result["path"] == str(
        Path(roots["artifacts_root"]) / "skills/runtime-management/RUN-001"
    )


def test_state_runtime_names_a_closed_set(paths):
    assert paths.state_runtime("hygiene")["path"].endswith("state/serveros/runtime/hygiene")
    with pytest.raises(PathRefused, match="not a declared runtime state area"):
        paths.state_runtime("whatever-i-feel-like")


# ---------------------------------------------------------------------------
# admission — inside a root is necessary, not sufficient
# ---------------------------------------------------------------------------


def test_a_relative_path_is_refused(paths):
    """A relative path means somebody is about to write next to the cwd."""
    with pytest.raises(PathRefused, match="not absolute"):
        paths.admit("artifacts/report.json")


def test_a_path_outside_every_root_is_refused(paths):
    with pytest.raises(PathRefused, match="outside every declared root"):
        paths.admit("/etc/passwd")


def test_workspace_artifacts_is_refused_even_though_it_is_inside_a_root(paths, roots):
    """The whole point. `workspace/artifacts` is under the workspace root and
    is exactly the substitute root the layout forbids."""
    with pytest.raises(PathRefused, match="workspace/artifacts"):
        paths.admit(str(Path(roots["workspace_root"]) / "artifacts/report.json"))


def test_no_new_data_root_top_level_area(paths, roots):
    with pytest.raises(PathRefused, match="declared top-level"):
        paths.admit(str(Path(roots["data_root"]) / "newroot/x"))


def test_a_new_top_level_directory_under_home_is_refused(paths, roots):
    with pytest.raises(PathRefused, match="new top-level directory under HOME"):
        paths.admit(str(Path(roots["home"]) / "brandnew/thing.json"))


def test_tilde_files_is_refused_even_though_it_exists(paths, roots):
    files = Path(roots["home"]) / "files"
    files.mkdir()
    with pytest.raises(PathRefused, match="substitute root"):
        paths.admit(str(files / "report.md"))


def test_a_near_canonical_directory_name_is_refused(paths, roots):
    for name in ("legacy", "old-artifacts", "backup"):
        with pytest.raises(PathRefused, match="near-canonical"):
            paths.admit(str(Path(roots["artifacts_root"]) / name / "x.json"))


def test_a_deep_name_that_merely_contains_old_is_allowed(paths, roots):
    """The check is at the first level only. `RUN-old` is a real name."""
    result = paths.admit(str(Path(roots["artifacts_root"]) / "work-items/p/I/RUN-old"))
    assert result["admitted"] is True


def test_workspace_src_refuses_a_write_but_allows_a_read(paths, roots):
    """§33: src is read-mostly. Fetching and inspecting are permitted; a
    normal code change belongs in a worktree."""
    target = str(Path(roots["workspace_root"]) / "src/litellm-bridge/foo.py")
    with pytest.raises(PathRefused, match="read-mostly"):
        paths.admit(target, writing=True)
    result = paths.admit(target, writing=False)
    assert result["admitted"] is True


def test_a_symlink_out_of_a_root_is_refused(paths, roots, tmp_path):
    """A path can be inside a root and point outside it. Containment is
    checked after resolution, not before."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link = Path(roots["artifacts_root"]) / "escape"
    link.symlink_to(outside)
    with pytest.raises(PathRefused, match="outside every declared root"):
        paths.admit(str(link / "report.json"))


def test_traversal_is_refused_before_resolution(paths, roots):
    with pytest.raises(PathRefused, match="`..`"):
        paths.admit(str(Path(roots["artifacts_root"]) / "work-items/../../../etc/passwd"))


def test_a_write_beyond_the_node_root_allowlist_is_refused(paths, roots):
    """Both refusal branches, because they carry different messages.

    `HOME_DOT_SERVEROS_FORBIDDEN` was declared with no consumer anywhere, so the
    names it lists were refused without the message ever naming the rule they
    broke — and a caller who is told "outside the allow-list" edits the list,
    while a caller told "this is a known second-state-root landing spot" moves
    the directory. `reports/` takes the named branch; `README.md` takes the
    general one.
    """
    target = Path(roots["home"]) / ".serveros"
    target.mkdir()
    with pytest.raises(PathRefused, match="refused by name"):
        paths.admit(str(target / "reports/q3.md"))
    with pytest.raises(PathRefused, match="identity and binding"):
        paths.admit(str(target / "README.md"))


def test_the_serveros_allowlist_admits_identity_files(paths, roots):
    target = Path(roots["home"]) / ".serveros/targets.yaml"
    result = paths.admit(str(target))
    assert result["admitted"] is True


def test_the_allowlist_covers_every_file_the_node_root_contract_names(paths, roots):
    """The whole vocabulary, not the one name two versions of it agreed on.

    The allow-list used to read `("identity.yaml", "targets.yaml",
    "profile.yaml", "config.yaml")` while `fleet/node-root-contract.yaml`,
    `fleet/node.py` and the disk all said `node.yaml`. `node.yaml` is the file
    that answers "which device is this host"; `admit()` refused it, and the test
    above passed anyway because it asserted `targets.yaml` — the single entry
    both lists contained.

    Testing the intersection of two disagreeing declarations is how the
    disagreement survives, so this asserts the whole set and both refusals
    around it: the names the authority declares are admitted, and the invented
    ones are not.
    """
    for declared in ("node.yaml", "targets.yaml", "controller.yaml"):
        result = paths.admit(str(Path(roots["home"]) / ".serveros" / declared))
        assert result["admitted"] is True, declared

    # The phantom names are refused rather than quietly allowed to accumulate:
    # a list widened to accept everything stops being a closed vocabulary.
    for invented in ("identity.yaml", "profile.yaml", "config.yaml"):
        with pytest.raises(PathRefused, match="identity and binding"):
            paths.admit(str(Path(roots["home"]) / ".serveros" / invented))


def test_admission_classifies_canonical_from_merely_permitted(paths, roots):
    canonical = paths.admit(str(Path(roots["artifacts_root"]) / "work-items/litellm/LIT-1/RUN-1"))
    assert canonical["canonical"] == "CANONICAL_WORK_ITEM_RUN"

    permitted = paths.admit(str(Path(roots["artifacts_root"]) / "scratch/notes.json"))
    assert permitted["canonical"] == "ADMITTED_NON_CANONICAL"
    # Both are admitted; only one is where new work goes. The distinction is
    # in the record so a caller can tell them apart without a second lookup.
    assert permitted["admitted"] is True


def test_admit_writes_nothing(paths, roots):
    """It is a decision. The component that acts on it is elsewhere."""
    target = Path(roots["artifacts_root"]) / "skills/x/RUN-1"
    paths.admit(str(target))
    assert not target.exists()


# ---------------------------------------------------------------------------
# §31–§42 · `path conformance`
# ---------------------------------------------------------------------------


def test_path_conformance_exit_codes_separate_three_facts(monkeypatch, capsys):
    """Clean, open-and-blocking, and could-not-answer are three answers.

    Collapsing the third into the first is the expensive mistake: a checker
    that could not read the authority it compares against would report the
    same exit status as a node that conforms, and the run that says "all
    clear" is exactly the run nobody re-reads.
    """
    # The summary keys are the module's contract with this command, so the
    # fixture spells out all of them rather than only the two the exit code
    # reads. A stub that carried just the fields under test would let the
    # renderer and the collector drift apart without either noticing.
    def report(findings):
        counts = {}
        for name, fid in (("undeclared", "UNDECLARED_ENTRY"),
                          ("exempt", "EXEMPT_ENTRY"),
                          ("absent", "DECLARED_ENTRY_ABSENT")):
            counts[name] = sum(1 for f in findings if f["id"] == fid)
        return {"schema": "serveros/directory-conformance.v1",
                "summary": {"findings": len(findings), "blocking_open": sum(
                    1 for f in findings
                    if f["severity"] == "HIGH" and f["status"] == "OPEN"), **counts},
                "findings": findings}

    high_open = [{"id": "DECLARATION_CONFLICT", "severity": "HIGH", "status": "OPEN",
                  "class": "~/.serveros", "entry": ""}]
    unreadable = [{"id": "DECLARATION_AUTHORITY_UNREADABLE", "severity": "MEDIUM",
                   "status": "OPEN", "class": "~/.serveros", "entry": ""}]

    for findings, expected in (([], cli_mod.EXIT_OK),
                               (high_open, cli_mod.EXIT_FAULT),
                               (unreadable, cli_mod.EXIT_COULD_NOT_ANSWER)):
        monkeypatch.setattr(cli_mod.conformance_mod, "collect",
                            lambda *a, _f=findings, **k: report(_f))
        code = cli_mod.main(["path", "conformance"])
        assert code == expected, (findings, code, expected)
        capsys.readouterr()


def test_path_conformance_is_a_read_only_verb():
    """It inspects and reports; it is not a lifecycle verb, and it does not
    repair anything it finds missing."""
    verbs = _all_subcommands(cli_mod.build_parser())
    assert "conformance" in verbs


# ---------------------------------------------------------------------------
# §47 · the probe verb, rendered end to end
# ---------------------------------------------------------------------------


def test_the_probe_verb_renders_a_probe_that_succeeded(tmp_path, capsys, monkeypatch):
    """A verb is not covered because it parses; it is covered when its happy
    path renders.

    `serverosctl probe <id>` raised `KeyError: 'axis'` for every probe that
    answered — the shape of the record was asserted nowhere, and no test called
    this verb at all, so a broken verb sat in `--help` looking shipped. The
    renderer reads `result['axis']` directly, which is why an arm that omits it
    is a crash rather than a blank column.
    """

    class Host:
        def now(self):
            return "2026-09-12T00:00:00Z"

        def docker_inspect(self, name):
            return {"State": {"Status": "running", "StartedAt": "2026-09-12T00:00:00Z",
                              "OOMKilled": False},
                    "RestartCount": 0}

    monkeypatch.setattr(cli_mod.probes_mod, "LiveHost", Host)

    registry_file = tmp_path / "resources.yaml"
    registry_file.write_text(
        "schema: serveros/managed-runtime-registry.v1\n"
        "device_ref: device:test\n"
        "resources:\n"
        "  - id: thing\n"
        "    kind: container\n"
        "    owner: {type: project, ref: 'project:x'}\n"
        "    scope: user\n"
        "    lifecycle: {class: persistent}\n"
        "    desired: {state: running}\n"
        "    protection: {level: protected}\n"
        "    probes:\n"
        "      - {id: p-liveness, kind: container-state, axis: liveness, container: c}\n"
    )
    code = cli_mod.main(["--registry", str(registry_file), "probe", "thing"])
    assert code == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "p-liveness" in out
    assert "liveness" in out
    assert "RUNNING" in out


def test_the_probe_verb_reports_an_unknown_resource_rather_than_crashing(tmp_path, capsys):
    code = cli_mod.main(["--registry", str(tmp_path / "absent.yaml"), "probe", "nope"])
    assert code == cli_mod.EXIT_COULD_NOT_ANSWER
    assert "no discovery fallback" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# the CLI contract
# ---------------------------------------------------------------------------


def _all_subcommands(parser) -> set[str]:
    """Every leaf verb the CLI accepts, walked from the real parser."""
    import argparse

    verbs: set[str] = set()

    def walk(p):
        for action in p._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    verbs.add(name)
                    walk(sub)

    walk(parser)
    return verbs


def test_no_subcommand_mutates_anything():
    """This round implements no stop, no remove, no prune and no reconcile,
    and a command that is absent is a clearer fact than one that exists and
    refuses."""
    verbs = _all_subcommands(cli_mod.build_parser())
    for forbidden in ("stop", "remove", "rm", "prune", "delete", "kill", "restart",
                      "reconcile", "register"):
        assert forbidden not in verbs, forbidden
    assert "hygiene" in verbs and "status" in verbs


def test_the_forbidden_verbs_are_absent_from_the_module_too():
    """Not just unexposed — unimplemented."""
    for name in ("cmd_runtime_stop", "cmd_runtime_remove", "cmd_runtime_prune",
                 "cmd_runtime_reconcile"):
        assert not hasattr(cli_mod, name), name


def test_global_flags_work_on_either_side_of_the_subcommand(tmp_path, capsys):
    """`--json` after the verb is what people actually type."""
    registry_file = tmp_path / "resources.yaml"
    registry_file.write_text(
        "schema: serveros/managed-runtime-registry.v1\n"
        "device_ref: device:test\nresources: []\n"
    )
    import json

    assert cli_mod.main(["runtime", "list", "--json", "--registry", str(registry_file)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli_mod.main(["--json", "--registry", str(registry_file), "runtime", "list"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert first == second


def test_exit_codes_distinguish_broken_from_could_not_look():
    """A monitor that cannot tell those apart reports outages during its own
    failures."""
    assert cli_mod.EXIT_OK == 0
    assert cli_mod.EXIT_FAULT == 1
    assert cli_mod.EXIT_COULD_NOT_ANSWER == 2
    assert cli_mod.EXIT_OK != cli_mod.EXIT_FAULT != cli_mod.EXIT_COULD_NOT_ANSWER


def test_an_unknown_resource_exits_could_not_answer(tmp_path, capsys, monkeypatch):
    registry_file = tmp_path / "resources.yaml"
    registry_file.write_text(
        "schema: serveros/managed-runtime-registry.v1\n"
        "device_ref: device:test\n"
        "resources:\n"
        "  - id: known\n"
        "    kind: container\n"
        "    owner: {type: project, ref: 'project:x'}\n"
        "    scope: user\n"
        "    lifecycle: {class: persistent}\n"
        "    desired: {state: running}\n"
        "    protection: {level: protected}\n"
    )
    code = cli_mod.main(["--registry", str(registry_file), "runtime", "status", "nope"])
    assert code == cli_mod.EXIT_COULD_NOT_ANSWER
    assert "never by being noticed" in capsys.readouterr().err


def test_a_missing_registry_is_an_error_not_an_empty_one(tmp_path, capsys):
    code = cli_mod.main(["--registry", str(tmp_path / "absent.yaml"), "runtime", "list"])
    assert code == cli_mod.EXIT_COULD_NOT_ANSWER
    assert "no discovery fallback" in capsys.readouterr().err


def test_runtime_list_reports_protection_and_why(tmp_path, capsys):
    registry_file = tmp_path / "resources.yaml"
    registry_file.write_text(
        "schema: serveros/managed-runtime-registry.v1\n"
        "device_ref: device:test\n"
        "resources:\n"
        "  - id: baseline\n"
        "    kind: container\n"
        "    owner: {type: project, ref: 'project:x'}\n"
        "    scope: user\n"
        "    lifecycle: {class: persistent}\n"
        "    desired: {state: running}\n"
        "    protection: {level: protected}\n"
    )
    assert cli_mod.main(["--registry", str(registry_file), "runtime", "list"]) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "baseline" in out
    assert "protected" in out


def test_path_admit_exits_fault_on_a_refusal(capsys):
    code = cli_mod.main(["path", "admit", "/etc/passwd"])
    assert code == cli_mod.EXIT_FAULT
    assert "REFUSED" in capsys.readouterr().out


def test_path_resolve_refusal_is_not_a_fault_but_an_error(capsys):
    """An unresolvable name is a caller mistake (2), not a broken thing (1)."""
    code = cli_mod.main(["path", "resolve", "artefacts/x"])
    assert code == cli_mod.EXIT_FAULT  # PathRefused is a refusal, reported as 1
    capsys.readouterr()


def test_json_output_is_a_view_of_the_same_facts(tmp_path, capsys):
    """The human form and the machine form must not be able to disagree."""
    import json

    code = cli_mod.main(["path", "contract", "--json"])
    assert code == cli_mod.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_top_level"] == list(paths_mod.WORKSPACE_TOP_LEVEL)
    assert payload["artifact_namespaces"] == ["work-items", "skills"]
    assert "no default root" in payload["note"]


# ---------------------------------------------------------------------------
# §52 · `serverosctl status` — the one verb that asks the daemon
# ---------------------------------------------------------------------------


def _fake_daemon(tmp_path, monkeypatch, reply, *, method_seen=None):
    """A one-shot Unix socket that answers the way `serverosd` does."""
    import json
    import socket as socketlib
    import threading

    sock_path = tmp_path / "serveros.sock"
    server = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)

    def serve():
        connection, _ = server.accept()
        try:
            buffer = b""
            while b"\n" not in buffer:
                chunk = connection.recv(1 << 20)
                if not chunk:
                    break
                buffer += chunk
            request = json.loads(buffer.split(b"\n")[0])
            if method_seen is not None:
                method_seen.append(request["method"])
            connection.sendall((json.dumps(reply) + "\n").encode())
        finally:
            connection.close()
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(cli_mod, "daemon_socket_path", lambda: sock_path)
    return thread


def _status_reply(**overrides):
    payload = {
        "device_ref": "device:server-beijing", "pid": 4242,
        "started_at": "2026-09-13T00:00:00+00:00", "uptime_seconds": 3600.0,
        "ticks": 60, "tick_interval_seconds": 60.0,
        "last_tick_at": "2026-09-13T01:00:00+00:00",
        "seconds_since_last_tick": 1.0, "stale_after_seconds": 180.0,
        "answer_is_stale": False, "tick_failures": [], "tick_failure_count": 0,
        "registry_count": 7, "reconcile_enabled": False,
        "state_dir": "/state", "socket": "/run/serveros.sock", "note": "…",
    }
    payload.update(overrides)
    return {"ok": True, "method": "node.status", "result": payload}


def test_the_status_verb_asks_the_daemon_rather_than_computing_locally(
        tmp_path, monkeypatch, capsys):
    """A status command that answers from somewhere else reports a healthy
    node while the daemon is dead."""
    seen = []
    thread = _fake_daemon(tmp_path, monkeypatch, _status_reply(), method_seen=seen)
    code = cli_mod.main(["status"])
    thread.join(timeout=5)
    assert code == cli_mod.EXIT_OK, capsys.readouterr().err
    out = capsys.readouterr().out
    assert seen == ["node.status"]
    assert "RUNNING" in out and "4242" in out
    assert "reconcile   disabled" in out


def test_the_status_verb_cannot_answer_without_a_daemon_and_does_not_guess(
        tmp_path, monkeypatch, capsys):
    """Exit 2, not 0 and not 1. "Could not look" is its own answer.

    The failure this prevents is the one the CLI's exit contract exists for: a
    monitor that reports "everything is fine" during its own outage.
    """
    monkeypatch.setattr(cli_mod, "daemon_socket_path",
                        lambda: tmp_path / "absent.sock")
    code = cli_mod.main(["status"])
    assert code == cli_mod.EXIT_COULD_NOT_ANSWER
    assert "no node runtime at" in capsys.readouterr().err


def test_the_status_verb_cannot_answer_when_the_daemon_refuses(
        tmp_path, monkeypatch, capsys):
    """A daemon that is up and does not know the method is still not an answer."""
    reply = {"ok": False, "method": "node.status",
             "error": {"code": "UNKNOWN_METHOD", "message": "not served"}}
    thread = _fake_daemon(tmp_path, monkeypatch, reply)
    code = cli_mod.main(["status"])
    thread.join(timeout=5)
    assert code == cli_mod.EXIT_COULD_NOT_ANSWER
    assert "UNKNOWN_METHOD" in capsys.readouterr().err


def test_a_runtime_that_stopped_looking_is_a_fault_not_a_success(
        tmp_path, monkeypatch, capsys):
    """The daemon is answering; its answer is about a node it stopped looking
    at. Exit 1 so a shell can tell that from a healthy run."""
    thread = _fake_daemon(tmp_path, monkeypatch,
                          _status_reply(answer_is_stale=True,
                                        seconds_since_last_tick=900.0))
    import json

    code = cli_mod.main(["status", "--json"])
    thread.join(timeout=5)
    assert code == cli_mod.EXIT_FAULT
    payload = json.loads(capsys.readouterr().out)
    assert payload["answer_is_stale"] is True


def test_runtime_tree_without_a_ref_draws_one_and_says_it_was_chosen(
        monkeypatch, capsys):
    """§52 lists `runtime tree` with no ref.

    A subject picked for the reader is acceptable only if the reader can see
    it was picked, so the default is drawn from the same ordering `runtime
    groups` already uses and named in the output.
    """
    calls = []
    real = cli_mod.view_mod.runtime_tree

    def fake_tree(ref, **kw):
        calls.append(ref)
        return real(ref, **kw)

    monkeypatch.setattr(cli_mod.view_mod, "runtime_tree", fake_tree)
    code = cli_mod.main(["runtime", "tree"])
    out = capsys.readouterr().out
    if code == cli_mod.EXIT_COULD_NOT_ANSWER:
        pytest.skip("no runtime group is visible to this uid")
    assert code == cli_mod.EXIT_OK
    assert calls and calls[0].startswith("runtime-group:")
    assert "no ref given" in out


# ---------------------------------------------------------------------------
# §55 item 14 · ability discovery
# ---------------------------------------------------------------------------


def _ability_reply(**overrides):
    payload = {
        "authority": "ability-management",
        "registry_status": "FRESH",
        "projection_path": "/state/serveros/abilities/effective.json",
        # The projection's real row shape, copied from a live
        # `state/serveros/abilities/effective.json`. The first version of this
        # reply used `revision`/`state`, which no projection emits — so it
        # agreed with a reader that printed `?` for every column.
        "abilities": [
            {"id": "ProfessorZhi/ability-management", "name": "workspace-management",
             "type": "skill", "path": "skills/workspace-management",
             "version": None, "revisions_installed": 1,
             "agents": ["claude-code", "codex"], "fingerprint": "sha256:aaaa"},
            {"id": "ProfessorZhi/ability-management", "name": "test-management",
             "type": "skill", "path": "skills/test-management",
             "version": None, "revisions_installed": 1,
             "agents": ["codex"], "fingerprint": "sha256:bbbb"},
        ],
        "note": "local observed projection",
    }
    payload.update(overrides)
    return {"ok": True, "method": "ability.list", "result": payload}


def test_ability_list_asks_the_daemon_for_the_projection(tmp_path, monkeypatch, capsys):
    """§55 item 14 was implemented and unreachable: `ability.list` existed on
    the socket and there was no way to ask for it but to write JSON by hand."""
    seen = []
    thread = _fake_daemon(tmp_path, monkeypatch, _ability_reply(), method_seen=seen)
    code = cli_mod.main(["ability", "list"])
    thread.join(timeout=5)
    assert code == cli_mod.EXIT_OK
    assert seen == ["ability.list"]
    out = capsys.readouterr().out
    assert "workspace-management" in out
    assert "FRESH" in out
    # A reader that names columns the projection does not carry prints a
    # table of question marks and reads as an empty registry.
    assert "?" not in out
    assert "claude-code,codex" in out


def test_an_unreadable_projection_is_not_an_empty_installation(
        tmp_path, monkeypatch, capsys):
    """Three states used to print as one empty list.

    `registry_status: STALE` means there is a projection and it is old;
    an empty `abilities` list means nothing is installed. A CLI that read the
    file itself could not tell those apart, which is why this one asks.
    """
    reply = _ability_reply(registry_status="STALE", abilities=[],
                           note="the projection is 2 days old")
    thread = _fake_daemon(tmp_path, monkeypatch, reply)
    code = cli_mod.main(["ability", "list"])
    thread.join(timeout=5)
    out = capsys.readouterr().out
    assert code == cli_mod.EXIT_OK
    assert "STALE" in out
    assert "2 days old" in out


def test_ability_status_for_an_ability_the_projection_does_not_carry(
        tmp_path, monkeypatch, capsys):
    """Exit 1, not 2: the node answered. Absence from a local projection is
    not proof of absence on the authority, so this is a state to distinguish
    rather than a failure to report."""
    reply = {"ok": True, "method": "ability.status",
             "result": _ability_reply()["result"] | {
                 "name": "document-stewardship", "installed": None,
                 "note": "'document-stewardship' is not in the local projection."}}
    thread = _fake_daemon(tmp_path, monkeypatch, reply)
    code = cli_mod.main(["ability", "status", "document-stewardship"])
    thread.join(timeout=5)
    assert code == cli_mod.EXIT_FAULT
    assert "not in the local projection" in capsys.readouterr().out


def test_ability_without_a_daemon_says_so_rather_than_listing_nothing(
        tmp_path, monkeypatch, capsys):
    """No daemon is 'could not answer'. An empty list would read as 'no
    abilities are installed', which is the opposite finding.

    The socket path is redirected even though nothing is listening there: on a
    node where `serverosd` IS running, a test that used the real path would
    pass by talking to the daemon and prove nothing about the no-daemon case.
    """
    monkeypatch.setattr(cli_mod, "daemon_socket_path",
                        lambda: tmp_path / "nothing-here.sock")
    code = cli_mod.main(["ability", "list"])
    captured = capsys.readouterr()
    assert code == cli_mod.EXIT_COULD_NOT_ANSWER
    assert "daemon" in captured.err
    assert captured.out == ""
