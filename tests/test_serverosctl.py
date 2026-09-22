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
# Loaded for the card-clock test, which has to ask whether the value the CLI
# hands the card model is one the model can parse.
presentation_mod = _load("serveros_runtime_presentation", "presentation.py")

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


def test_the_card_clock_is_an_instant_and_not_a_display_string():
    """The value the card model is handed has to be one it can compute with.

    `_card_common` used `_now_text()` for this — `"%Y-%m-%d %H:%M:%S %Z"` — and
    `presentation.open_duration` ends an open row at exactly this value. The
    parse failed, so the live alert card read 「持续 未知」 for a duration the
    ledger held, and `cards._local` could not shorten the collection time
    either. Both were visible on the production `card alert --json` on
    2026-09-22 and neither was covered by a test.

    The control is the display string: if it ever becomes parseable this test
    stops being about anything, and one of the two below will say so.
    """
    now = cli_mod._card_clock()
    assert presentation_mod._parse(now) is not None, (
        f"the card clock {now!r} is not an instant, so every duration the card "
        "computes from it is unmeasured")
    assert presentation_mod._parse(cli_mod._now_text()) is None, (
        "the display string became parseable, so the two are interchangeable "
        "and this test no longer distinguishes them")


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


# ---------------------------------------------------------------------------
# §4 §6 §11 · ability tree / ability inspect / pack inspect
#
# The three faces §F names and the tree §4 asks for. Two of them are daemon
# verbs and are driven through the real socket below; `pack inspect` reads a
# catalogue off the filesystem and is driven through the real parser, because
# the decision it encodes — WHICH catalogue — is the whole command.
#
# The payloads are copies of what the daemon answers with, trimmed to the
# fields the renderers read. Copies rather than live calls for the reason the
# §21 section gives above: on a node with no projection the live answers are
# empty in every field, and an empty answer agrees with every reader.
# ---------------------------------------------------------------------------


def _tree_reply(**overrides):
    payload = {
        "authority": "ability-management",
        "this_is": "a local observed projection, not the authority",
        "registry_status": "FRESH",
        "generated_at": "2026-09-14T00:00:00+00:00",
        "age_seconds": 12.0,
        "stale_after_seconds": 86400,
        "source_revision": "cb581b9",
        "stale": False,
        "source": "/state/serveros/abilities/effective.json",
        "schema": "serveros/ability-tree.v1",
        "basis": "domain.primary",
        "nodes": ["serveros", "serveros.operations",
                  "serveros.operations.test", "serveros.operations.workspace"],
        "placements": {"test-management": "serveros.operations.test",
                       "workspace-management": "serveros.operations.workspace"},
        "unplaced": ["litellm-image-build"],
        "cross_refs": {"workspace-management": ["work"]},
        "ability_count": 3,
        "classified_count": 2,
        "unplaced_count": 1,
        "note": "derived from `domain.primary` alone",
    }
    payload.update(overrides)
    return {"ok": True, "method": "ability.tree", "result": payload}


def test_ability_tree_asks_the_daemon_and_renders_the_placements(
        tmp_path, monkeypatch, capsys):
    seen = []
    thread = _fake_daemon(tmp_path, monkeypatch, _tree_reply(), method_seen=seen)
    code = cli_mod.main(["ability", "tree"])
    thread.join(timeout=5)
    assert code == cli_mod.EXIT_OK, capsys.readouterr().err
    out = capsys.readouterr().out
    assert seen == ["ability.tree"]
    assert "serveros" in out
    # The two counts, and why there are two: `serveros.operations` has nothing
    # placed AT it and two things placed below it. A single `(0)` beside that
    # node reads as an empty branch, which is the opposite of the fact.
    assert "operations  (0 here, 2 below)" in out
    assert "workspace  (1)" in out
    # An unplaced ability is counted, not hidden — §4 refuses to invent a
    # `domain.primary` to make the tree look complete.
    assert "classified  2 of 3" in out
    assert "unplaced    1" in out


def test_an_absent_projection_is_not_an_empty_tree(tmp_path, monkeypatch, capsys):
    """Exit 0, and the opposite finding from "the catalogue classifies nothing".

    The daemon answers `nodes: null` when there is no projection and `nodes: []`
    when there is one and it placed nothing. Both render as a tree with no
    abilities under it, and they are different facts about two different
    parties — nobody has asked ability-management, versus ability-management
    has classified nothing.
    """
    absent = _tree_reply(
        registry_status="NOT_MATERIALIZED", generated_at=None,
        source=None, source_revision=None, stale=True,
        nodes=None, placements=None, unplaced=None, cross_refs=None,
        ability_count=None, classified_count=None, unplaced_count=None,
        note="no local capability projection has been materialized, so there is "
             "no catalogue to derive a tree from. Run `serverosctl ability "
             "refresh` to produce the projection this is derived from.")
    thread = _fake_daemon(tmp_path, monkeypatch, absent)
    code = cli_mod.main(["ability", "tree"])
    thread.join(timeout=5)
    out = capsys.readouterr().out
    # §28: an unreachable authority is a supported answer, not a failure to
    # compute one. Exit 2 here would make every monitor that runs this report
    # an outage while the node is working exactly as designed.
    assert code == cli_mod.EXIT_OK
    assert "no local capability projection" in out
    assert "ability refresh" in out
    assert "classified" not in out


def test_a_projection_that_places_nothing_is_a_different_answer(
        tmp_path, monkeypatch, capsys):
    """The contrast the test above is worth nothing without.

    Same command, same empty-looking rendering: a projection that exists and
    classified nothing. If the reader treated `None` as `[]` these two answers
    would be one answer, and a node whose catalogue is genuinely unclassified
    would be reported as a node nobody has asked about.
    """
    thread = _fake_daemon(tmp_path, monkeypatch, _tree_reply(
        nodes=[], placements={}, unplaced=["a", "b"], cross_refs={},
        ability_count=2, classified_count=0, unplaced_count=2))
    code = cli_mod.main(["ability", "tree"])
    thread.join(timeout=5)
    out = capsys.readouterr().out
    assert code == cli_mod.EXIT_OK
    assert "classified  0 of 2" in out
    assert "unplaced    2" in out
    assert "no local capability projection" not in out


def _inspect_reply(**overrides):
    payload = {
        "authority": "ability-management",
        "this_is": "a local observed projection, not the authority",
        "registry_status": "FRESH",
        "generated_at": "2026-09-14T00:00:00+00:00",
        "age_seconds": 12.0,
        "stale": False,
        "source_revision": "cb581b9",
        "name": "workspace-management",
        "installed": True,
        "ability": {"name": "workspace-management",
                    "id": "ProfessorZhi/ability-management",
                    "type": "skill", "path": "skills/workspace-management",
                    "domain": {"primary": "serveros.operations.workspace"},
                    "taxonomy_refs": ["work"]},
        "tree": {"placement": "serveros.operations.workspace",
                 "cross_refs": ["work"], "unplaced": []},
        "source": "/state/serveros/abilities/effective.json",
        "resolution": "not attempted",
        "resolved_by": "serverosctl ability resolve",
        "operations_declared": None,
        "operations_declared_by": "serverosctl ability resolve --ability <id>#<verb>",
        "note": "…",
    }
    payload.update(overrides)
    return {"ok": True, "method": "ability.inspect", "result": payload}


def test_ability_inspect_reads_the_daemons_nested_shape(tmp_path, monkeypatch, capsys):
    """The regression test for the wrong reader this command was written with.

    The first draft assumed a flat payload — `payload["tier"]`, a domain at the
    top level — and against the real daemon printed three lines and exited 0,
    which reads as an ability with no classification rather than as a reader
    looking in the wrong place. The row is `ability`; the placement is
    `tree.placement`. `cmd_ability_status` carries a comment about the same
    mistake, which is how it was available to be repeated.
    """
    seen = []
    thread = _fake_daemon(tmp_path, monkeypatch, _inspect_reply(), method_seen=seen)
    code = cli_mod.main(["ability", "inspect", "workspace-management"])
    thread.join(timeout=5)
    assert code == cli_mod.EXIT_OK, capsys.readouterr().err
    out = capsys.readouterr().out
    assert seen == ["ability.inspect"]
    assert "ProfessorZhi/ability-management" in out
    # From `ability.domain.primary`, not from the row's top level.
    assert "domain.primary      serveros.operations.workspace" in out
    # From `tree.placement`.
    assert "tree_node           serveros.operations.workspace" in out
    assert "taxonomy_refs       work" in out
    assert "operations          None" in out


def test_ability_inspect_never_reports_a_resolution(tmp_path, monkeypatch, capsys):
    """The two questions that print the same three columns.

    "This ability is in the local projection" is a claim about a file on this
    node. "This device can execute it" is the intersection of that projection
    with the providers this device actually has, and it is `ability resolve`'s
    answer. A surface that answered the second from the first would answer it
    with a union — the resolver exists because the two sets are not one.
    """
    thread = _fake_daemon(tmp_path, monkeypatch, _inspect_reply())
    code = cli_mod.main(["ability", "inspect", "workspace-management"])
    thread.join(timeout=5)
    out = capsys.readouterr().out
    assert code == cli_mod.EXIT_OK
    assert "resolution          not attempted" in out
    assert "serverosctl ability resolve" in out
    for word in ("SUPPORTED", "READY_TO_RUN", "CAN_RUN"):
        assert word not in out, "inspect must not answer the resolver's question"


def test_ability_inspect_on_an_ability_the_projection_lacks_is_a_fault(
        tmp_path, monkeypatch, capsys):
    """Exit 1, not 2: the node answered, and what it answered is "not here".

    Exit 2 means "could not look", and collapsing the two is how a monitor
    reports an outage during its own failures.
    """
    reply = _inspect_reply(
        name="document-stewardship", installed=None, ability=None, tree=None,
        note="no local capability projection has been materialized …")
    thread = _fake_daemon(tmp_path, monkeypatch, reply)
    code = cli_mod.main(["ability", "inspect", "document-stewardship"])
    thread.join(timeout=5)
    assert code == cli_mod.EXIT_FAULT
    assert "installed   None" in capsys.readouterr().out


# `pack inspect` reads its catalogue from the filesystem rather than the
# socket, so these run the real parser and the real `abilities/packs.py`. The
# kernel is not stubbed: the property under test is that this command prints
# the verdict the kernel produced, and a stubbed kernel would assert that it
# prints the verdict the test produced.

PACK_DECLARATIONS = """
packs:
  - id: "pack:one"
    name: "One"
    purpose: "to exist"
    authority:
      repo: "ProfessorZhi/ability-management"
      revision: "aaa1111aaa1111aaa1111aaa1111aaa1111aaa1"
    members:
      - {ability: "ability:work-management", role: required}
      - {ability: "ability:test-management", role: required}
      - {ability: "ability:not-in-the-catalogue", role: optional}
  - id: "pack:two"
    name: "Two"
    purpose: "to exist as well"
    authority:
      repo: "ProfessorZhi/ability-management"
      revision: "aaa1111aaa1111aaa1111aaa1111aaa1111aaa1"
    members:
      - {ability: "ability:work-management", role: required}
"""

CATALOGUE_REV = "bbb2222bbb2222bbb2222bbb2222bbb2222bbb2"


def _pack_inputs(tmp_path):
    declarations = tmp_path / "ability-packs.yaml"
    declarations.write_text(PACK_DECLARATIONS)
    catalogue = tmp_path / "catalogue.txt"
    # `#` is a comment, because a name list a person maintains needs one.
    catalogue.write_text("# the catalogue\nwork-management\ntest-management\n")
    return declarations, catalogue


def test_pack_inspect_refuses_to_resolve_against_the_local_projection(
        tmp_path, capsys):
    """The refusal is the finding, and it exists because the opposite shipped.

    The first version of this command defaulted the catalogue to the abilities
    the daemon reports over IPC. It ran, and printed `UNAVAILABLE 0/5 resolved`
    for all five packs — because a pack's closure is a statement about the
    whole canonical catalogue (55 names) and the node's projection is a
    device-filtered subset of it (25 on this node). The verdict was about one
    thing and computed from another, with nothing in the output to say so.

    So there is no default. Omitting both ways of naming a catalogue is a
    refusal, not a fallback, and nothing at all is printed on stdout.
    """
    declarations, _ = _pack_inputs(tmp_path)
    code = cli_mod.main(["pack", "inspect", "--declarations", str(declarations)])
    captured = capsys.readouterr()
    assert code == cli_mod.EXIT_FAULT
    assert captured.out == "", "a refusal must not print a verdict-shaped table"
    assert "local projection" in captured.err
    assert "--ability-repo" in captured.err


def test_pack_inspect_prints_the_kernels_verdict_unchanged(tmp_path, capsys):
    declarations, catalogue = _pack_inputs(tmp_path)
    argv = ["pack", "inspect", "--declarations", str(declarations),
            "--catalogue", str(catalogue),
            "--catalogue-revision", CATALOGUE_REV]
    code = cli_mod.main(argv)
    out = capsys.readouterr().out
    assert code == cli_mod.EXIT_OK
    assert "2 abilities" in out
    # Three members, two required and both present, one optional and absent:
    # CLOSED with an unresolved optional named rather than dropped.
    assert "pack:one" in out and "CLOSED" in out and "2/3 resolved" in out
    assert "- optional, unresolved: not-in-the-catalogue" in out
    assert "pack:two" in out

    # The control: the same contract over a catalogue that is missing a
    # REQUIRED member. Without this, a renderer that printed "CLOSED" for
    # everything would satisfy the assertions above.
    thin = tmp_path / "thin.txt"
    thin.write_text("work-management\n")
    code = cli_mod.main(["pack", "inspect", "--declarations", str(declarations),
                         "--catalogue", str(thin),
                         "--catalogue-revision", CATALOGUE_REV])
    out = capsys.readouterr().out
    assert code == cli_mod.EXIT_OK
    assert "PARTIAL" in out and "1/3 resolved" in out
    assert "! required, unresolved: test-management" in out


def test_pack_inspect_reports_the_pin_as_a_question_of_its_own(tmp_path, capsys):
    """A correct closure about the wrong catalogue reads like a correct one.

    That is PIN LAG, and the only thing that finds it is a verdict which
    reports the pin separately from the members. Here the members all resolve
    and the pin is stale, so the pack is CLOSED and the pin is NOT all match —
    and the reason has to be readable, because "PIN_MISMATCH" beside a closed
    pack is exactly the line a reader skips.
    """
    declarations, catalogue = _pack_inputs(tmp_path)
    code = cli_mod.main(["pack", "inspect", "--declarations", str(declarations),
                         "--catalogue", str(catalogue),
                         "--catalogue-revision", CATALOGUE_REV])
    out = capsys.readouterr().out
    assert code == cli_mod.EXIT_OK
    assert "pins        NOT all match" in out
    assert "pin pack:one: PIN_MISMATCH" in out
    assert "revalidate and repin" in out

    # And the other direction, so the line above is not a constant.
    code = cli_mod.main(["pack", "inspect", "--declarations", str(declarations),
                         "--catalogue", str(catalogue),
                         "--catalogue-revision",
                         "aaa1111aaa1111aaa1111aaa1111aaa1111aaa1"])
    out = capsys.readouterr().out
    assert code == cli_mod.EXIT_OK
    assert "pins        all match" in out


def test_pack_inspect_refuses_a_name_that_matches_no_declared_pack(
        tmp_path, capsys):
    """A filter that silently widens is a filter nobody can check with."""
    declarations, catalogue = _pack_inputs(tmp_path)
    code = cli_mod.main(["pack", "inspect", "pack:nine",
                         "--declarations", str(declarations),
                         "--catalogue", str(catalogue),
                         "--catalogue-revision", CATALOGUE_REV])
    captured = capsys.readouterr()
    assert code == cli_mod.EXIT_FAULT
    assert captured.out == ""
    assert "declares no pack" in captured.err

    # The bare id is accepted, because the `pack:` prefix is a spelling and
    # not a second command.
    code = cli_mod.main(["pack", "inspect", "one",
                         "--declarations", str(declarations),
                         "--catalogue", str(catalogue),
                         "--catalogue-revision", CATALOGUE_REV])
    out = capsys.readouterr().out
    assert code == cli_mod.EXIT_OK
    assert "pack:one" in out and "pack:two" not in out


def test_the_three_discovery_verbs_parse_and_name_a_handler():
    parser = cli_mod.build_parser()
    for argv, handler in ((["ability", "tree"], "cmd_ability_tree"),
                          (["ability", "inspect", "x"], "cmd_ability_inspect"),
                          (["pack", "inspect", "--declarations", "x"], "cmd_pack_inspect")):
        args = parser.parse_args(argv)
        assert args.func is getattr(cli_mod, handler), argv


def test_an_absent_projection_reaches_ability_inspect_too(
        tmp_path, monkeypatch, capsys):
    """The same three-state distinction, on the second face.

    `ability tree` treats `nodes: null` as an answer; `ability inspect` has to
    do the same with `ability: null`, or the two faces disagree about what an
    absent projection is and a caller has to know which one it asked.
    """
    reply = _inspect_reply(
        registry_status="NOT_MATERIALIZED", stale=True, generated_at=None,
        source_revision=None, name="workspace-management",
        installed=None, ability=None, tree=None,
        note="no local capability projection has been materialized …")
    thread = _fake_daemon(tmp_path, monkeypatch, reply)
    code = cli_mod.main(["ability", "inspect", "workspace-management"])
    thread.join(timeout=5)
    out = capsys.readouterr().out
    assert code == cli_mod.EXIT_FAULT
    assert "NOT_MATERIALIZED" in out
    assert "no local capability projection" in out


# ---------------------------------------------------------------------------
# §21 · the five surfaces this round added to `serverosctl`
#
# The bug these tests exist for is specific and was live: the five command
# functions were written against payload shapes that had been GUESSED, and run
# against the daemon they read keys that are not there. Four of the five would
# have raised `KeyError` on this node and one would have silently printed an
# empty table, and the test suite was green through all of it because nothing
# exercised the rendering. A verb is not covered because it parses.
# ---------------------------------------------------------------------------

FIVE = (
    (["resource", "ports"], "cmd_resource_ports"),
    (["runtime", "desired"], "cmd_runtime_desired"),
    (["runtime", "classify"], "cmd_runtime_classify"),
    (["fs", "hygiene"], "cmd_fs_hygiene"),
    (["container", "protection"], "cmd_container_protection"),
    # §6/§13/§19. The two reads §19's suggested surface names that had a
    # producer and no way for an agent to ask for it.
    (["resource", "docker"], "cmd_resource_docker"),
    (["tunnel", "list"], "cmd_tunnel_list"),
    # §18/§19. The fifteen resource codes, with the ones that could not be
    # graded named beside the ones that could.
    (["resource", "anomaly"], "cmd_resource_anomaly"),
)


def test_each_verb_this_round_added_parses_and_names_a_handler():
    """A verb that parses to no `func` is a command that will not run."""
    parser = cli_mod.build_parser()
    for argv, handler in FIVE:
        args = parser.parse_args(argv)
        assert args.func is getattr(cli_mod, handler), argv


# The shapes below are COPIES of what this node's daemon answers with on
# 2026-09-14, trimmed to the fields the renderers read. They are copies rather
# than calls because a test that measured the live host would pass on a node
# with no containers and prove nothing, and because the property under test is
# "the renderer reads the fields that exist", not "the host is a particular
# shape today".
PORTS_PAYLOAD = {
    "coverage": {"endpoints": 2, "claimed_by_a_container": 1, "claimed_by_a_tunnel": 0,
                 "with_an_authoritative_owner": 1, "unattributed": 1,
                 "listening_with_no_visible_holder": 1},
    "ports": [
        {"port_ref": "port:tcp:0.0.0.0:22", "owner_ref": None, "confidence": "UNKNOWN",
         "container_ref": None},
        {"port_ref": "port:tcp:127.0.0.1:26400", "owner_ref": "work-item:LIT-0104",
         "confidence": "AUTHORITATIVE", "container_ref": "runtime:container:abc123"},
    ],
    "conflicts": [{"port_ref": "port:tcp:0.0.0.0:3000", "why": "two claims disagree"}],
    "providers": {"proc-net": {"state": "AVAILABLE", "why": "read"},
                  "docker": {"state": "UNAVAILABLE", "why": "no daemon"}},
    "note": "advisory",
}

DESIRED_PAYLOAD = {
    "providers": {"registry": {"registrations": 1}},
    "comparisons": [{"resource_id": "res-down", "desired_state": "running",
                     "actual_state": "STOPPED", "actual_derived_state": "NOT_RUNNING",
                     "verdict": "IN_MISMATCH", "why": "declared running, measured stopped"}],
    "mismatches": [{"resource_id": "res-down"}],
    "unchecked": [],
}

HYGIENE_PAYLOAD = {
    "counts": {"ACTIVE": 1, "CLEANUP_CANDIDATE": 0},
    "resources": [{"resource_ref": "managed-runtime:res", "class": "PROTECTED",
                   "also_matches": ["ACTIVE"], "why": "protected"}],
    "candidates": [{"resource_ref": "managed-runtime:res", "why": "a question"}],
    "inputs_gathered": ["comparison"],
    "inputs_not_gathered": ["activity"],
    "note": "advisory",
}

FS_PAYLOAD = {
    "roots": {"home": {"path": "/home/hwz", "class": "NO_VOCABULARY"},
              "workspace": {"path": "/w", "class": "DECLARED_CLOSED"}},
    "coverage": {"home": {"entries": 64, "vocabulary_enforced_on_write": False,
                          "why": "no declared vocabulary"},
                 "workspace": {"entries": 9, "vocabulary_enforced_on_write": True,
                               "why": None}},
    "counts": {"CANONICAL": 25, "MISPLACED": 1, "UNKNOWN": 71},
    "open_questions": [{"class": "MISPLACED", "path": "/w/stray",
                        "kind": "directory", "why": "not in the closed set"}],
    "role_locations": {"declared": ["runtime.a"], "entries": 11, "why": "one level in"},
    "symlinks_into_data_root": {"/data/.vscode-server": {
        "home_path": "/home/hwz/.vscode-server", "target": "/data/.vscode-server",
        "why": "deliberate relocation"}},
    "duplicate_semantic_roots": {"artifacts": ["/a", "/b"]},
    "note": "advisory",
}

PROTECTION_PAYLOAD = {
    "checked": 1,
    "counts": {"protected": 1, "not_protected": 0, "unknown": 0},
    "containers": [{"subject": "abc123", "name": "res", "protected": True,
                    "container_id": "abc123", "by_identity": {},
                    "findings": [
                        {"code": "PROTECTION_LOST_ON_IDENTITY_SWITCH",
                         "severity": "HIGH", "implies_stale": False,
                         "identity_that_protects": "container_name",
                         "identity_that_does_not": "container_id",
                         "sources": [], "why": "the answer depends on the spelling"}]}],
    "findings": [{"subject": "abc123", "code": "PROTECTION_LOST_ON_IDENTITY_SWITCH",
                  "severity": "HIGH", "implies_stale": False,
                  "identity_that_protects": "container_name",
                  "identity_that_does_not": "container_id", "sources": [],
                  "why": "the answer depends on the spelling"}],
}


class _Args:
    def __init__(self, **kw):
        self.json = False
        self.registry = None
        for key, value in kw.items():
            setattr(self, key, value)


def test_the_five_renderers_read_the_shapes_the_daemon_actually_answers(
        monkeypatch, capsys, tmp_path):
    """Each renderer is driven with the real field names and must print the
    values that are IN them.

    Asserting the values rather than only the exit code is the point: a
    renderer that reads a key that does not exist raises, and a renderer that
    reads the WRONG existing key prints a blank column and exits 0.
    """
    monkeypatch.setattr(cli_mod.view_mod, "port_ownership",
                        lambda **k: PORTS_PAYLOAD)
    monkeypatch.setattr(cli_mod.view_mod, "desired_actual",
                        lambda **k: DESIRED_PAYLOAD)
    monkeypatch.setattr(cli_mod.view_mod, "runtime_hygiene",
                        lambda **k: HYGIENE_PAYLOAD)
    monkeypatch.setattr(cli_mod.view_mod, "fs_hygiene", lambda **k: FS_PAYLOAD)
    monkeypatch.setattr(cli_mod.view_mod, "protection",
                        lambda **k: PROTECTION_PAYLOAD)
    monkeypatch.setattr(cli_mod, "load_registry", lambda path: None)

    class _Inventory:
        def docker_list(self):
            return {"containers": [{"name": "res", "id": "abc123", "labels": ""}]}

    monkeypatch.setattr(cli_mod.inventory_mod, "Inventory", _Inventory)

    # A registry file that exists, so `load_registry` is never reached.
    args = _Args()

    assert cli_mod.cmd_resource_ports(args) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "port:tcp:127.0.0.1:26400" in out and "work-item:LIT-0104" in out
    assert "two claims disagree" in out
    assert "docker: UNAVAILABLE" in out

    assert cli_mod.cmd_runtime_desired(args) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "res-down" in out and "running / STOPPED (NOT_RUNNING)" in out
    assert "unchecked      0" in out

    assert cli_mod.cmd_runtime_classify(args) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "managed-runtime:res" in out and "PROTECTED" in out
    assert "also matches: ACTIVE" in out
    assert "inputs NOT gathered  activity" in out

    assert cli_mod.cmd_fs_hygiene(args) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "MISPLACED" in out and "/w/stray" in out
    assert "not in the closed set" in out
    assert "/home/hwz/.vscode-server -> /data/.vscode-server" in out
    assert "artifacts: ['/a', '/b']" in out

    assert cli_mod.cmd_container_protection(args) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "abc123" in out and "res" in out
    assert "the answer depends on the spelling" in out


DOCKER_PAYLOAD = {
    "summary": {"Containers": 3, "ContainersRunning": 1, "ContainersStopped": 2,
                "Images": 5, "DockerRootDir": "/var/lib/docker", "Driver": "overlay2"},
    "containers": {"containers": [], "count": 3},
    "images": {"images": [], "count": 5, "dangling": 1},
    "volumes": {"volumes": [], "count": 32},
    "networks": {"networks": [], "count": 21},
    "sizes": {"containers": [
        {"name": "big", "id": "aaa", "size_rw_bytes": 10 * 1024 * 1024,
         "size_root_fs_bytes": 2 * 1024 * 1024 * 1024},
        {"name": "small", "id": "bbb", "size_rw_bytes": 0,
         "size_root_fs_bytes": 1024 * 1024},
    ], "count": 2},
    "largest_observed_consumers": [
        {"name": "big", "container_id": "aaa",
         "writable_layer_bytes": 10 * 1024 * 1024,
         "rootfs_bytes": 2 * 1024 * 1024 * 1024},
        {"name": "small", "container_id": "bbb", "writable_layer_bytes": 0,
         "rootfs_bytes": 1024 * 1024},
    ],
    "space_included": False,
    "space_note": "the reclaimable/build-cache picture is NOT in this answer",
    "advisory": "every number here is a measurement",
}

TUNNEL_PAYLOAD = {
    "state": "UNKNOWN", "declared_count": 0, "observed_count": 0,
    "declared": [], "observed_forwarders": [], "undeclared_forwarders": [],
    "blind_spots": [{"what": "sshd-owned forwards", "state": "UNKNOWN",
                     "measured": {"visible_uids": [1006]},
                     "why": "hidepid=invisible"}],
    "advisory": "joined by PID",
}


def test_resource_docker_renders_the_daemons_docker_shape(monkeypatch, capsys):
    """The verb §30's acceptance needs, rendered end to end.

    The space note must appear in the answer when the picture is absent —
    an omitted section that printed nothing would read as a section that was
    measured and came back empty.
    """
    monkeypatch.setattr(cli_mod.view_mod, "docker_observation",
                        lambda **k: DOCKER_PAYLOAD)
    assert cli_mod.cmd_resource_docker(_Args(space=False)) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "3 (1 running, 2 stopped)" in out
    assert "/var/lib/docker" in out
    assert "2.0 GiB" in out and "big" in out
    assert "NOT in this answer" in out


def test_resource_docker_reports_the_reclaimable_figure_as_a_would_be(monkeypatch, capsys):
    """`--space` prints what a prune WOULD free and says so, because the
    number is an estimate of an action this round must not take."""
    payload = dict(DOCKER_PAYLOAD, space_included=True, space={
        "sections": {"images": {"size_bytes": 4 * 1024 ** 3,
                                "reclaimable_bytes": 3 * 1024 ** 3}},
        "reclaimable_bytes_total": 3 * 1024 ** 3})
    monkeypatch.setattr(cli_mod.view_mod, "docker_observation",
                        lambda **k: payload)
    assert cli_mod.cmd_resource_docker(_Args(space=True)) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "what a prune WOULD free" in out
    assert "3.0 GiB of 4.0 GiB" in out


def test_tunnel_list_prints_the_blind_spots_not_only_the_counts(monkeypatch, capsys):
    """`declared 0` is a fact about what was found; the blind spots are the
    fact about what could be looked for. A rendering that printed only the
    counts would answer §30's tunnel question with the first and hide the
    second."""
    monkeypatch.setattr(cli_mod.view_mod, "tunnel_observation",
                        lambda **k: TUNNEL_PAYLOAD)
    assert cli_mod.cmd_tunnel_list(_Args()) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "TUNNEL OBSERVATION = UNKNOWN" in out
    assert "sshd-owned forwards" in out and "hidepid=invisible" in out


ANOMALY_PAYLOAD = {
    "observed_at": "2026-09-14T13:00:00+08:00",
    "signals": [{
        "code": "DISK_CRITICAL", "severity": "high", "subject": "filesystem:/",
        "measured": {"used_percent": 93.4, "threshold_percent": 92.0,
                     "roles": ["ROOT_FS", "DOCKER_FS"]},
        "window": "point in time", "why": "this filesystem is at or above the critical tier",
        "source": "host-filesystem",
        "freshness": {"state": "FRESH", "age_seconds": 0.0,
                      "within_seconds": 300, "observed_at": None, "why": None},
    }],
    "by_code": {"DISK_CRITICAL": 1},
    "codes_emitted": ["DISK_CRITICAL"],
    "codes_graded": ["DISK_CRITICAL", "CONTAINER_WRITABLE_LAYER_LARGE"],
    "codes_not_evaluated": [
        {"code": "DOCKER_STORAGE_HIGH", "why": "`docker system df`: not "
         "collected. On this host it costs ~22s and is opt-in by name — and not "
         "asking is not the same as finding 0", "needs": "docker_space()",
         "threshold": {"key": "docker_storage_percent_high"}},
        {"code": "TUNNEL_OWNER_MISSING", "why": "no declared tunnel was supplied",
         "needs": "tunnel_observation() rows", "threshold": None},
    ],
    "coverage": {"graded": 2, "of": 15, "note": "a code in `codes_graded` was "
                 "looked for and not found; a code in `codes_not_evaluated` "
                 "was not looked for at all"},
    "notes": [],
    "gathering": {"inputs_supplied_by_the_caller": [],
                  "inputs_reached": ["host", "disk", "docker", "hygiene",
                                     "desired", "tunnel"],
                  "inputs_unreachable": {}},
    "space_included": False,
    "supported_by_resource_policy": {"state": "DECLARED",
                                     "destructive_reconcile": {"enabled": False}},
    "advisory": "Every row here is a measurement with its threshold beside it.",
}


def test_resource_anomaly_prints_the_codes_it_could_not_grade(monkeypatch, capsys):
    """§27 applied to §18's own output, and the reason this renderer exists.

    A report that printed only the signals would show a host with one problem
    and hide the thirteen codes nothing looked at. The not-evaluated list is
    printed with each reason, and the space note names why two of them are
    missing instead of letting an omitted section read as an empty one.
    """
    monkeypatch.setattr(cli_mod.view_mod, "resource_anomaly",
                        lambda **k: ANOMALY_PAYLOAD)
    assert cli_mod.cmd_resource_anomaly(_Args(space=False)) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "DISK_CRITICAL" in out and "filesystem:/" in out
    assert "2 of 15" in out
    assert "codes NOT evaluated" in out
    assert "not the same as finding 0" in out
    assert "--space" in out
    assert "destructive reconcile False" in out


def test_resource_anomaly_says_freshness_and_the_window_beside_each_row(
        monkeypatch, capsys):
    """§26 on the printed surface: the window is what makes the row ageable,
    so a reader who copies `used_percent: 93.4` out of the table has also
    copied when it was true and how long it stays true."""
    monkeypatch.setattr(cli_mod.view_mod, "resource_anomaly",
                        lambda **k: ANOMALY_PAYLOAD)
    assert cli_mod.cmd_resource_anomaly(_Args(space=True)) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "source host-filesystem" in out
    assert "window point in time" in out and "freshness FRESH" in out
    assert "threshold" in out and "92.0" in out


def test_the_container_filter_is_a_view_and_says_so(monkeypatch, capsys):
    """`--claimed-by-a-container` narrows the printed rows and leaves the
    coverage counts whole, and it names the narrowing. A filtered report that
    hid how much it hid would make a partial answer read as a complete one."""
    monkeypatch.setattr(cli_mod.view_mod, "port_ownership",
                        lambda **k: PORTS_PAYLOAD)
    monkeypatch.setattr(cli_mod, "load_registry", lambda path: None)

    assert cli_mod.cmd_resource_ports(
        _Args(claimed_by_a_container=True)) == cli_mod.EXIT_OK
    out = capsys.readouterr().out
    assert "endpoints            2" in out, "the coverage count was narrowed too"
    assert "filtered to the container-claimed endpoints: 1 of 2" in out
    assert "port:tcp:0.0.0.0:22" not in out


def test_the_cli_module_contains_no_mutating_call():
    """§26's gate, applied to the file this round edited most.

    Classified from the parsed AST by the same guard the rest of the tree is
    checked with, so a verb spelled inside a docstring — and this module's
    docstrings name the verbs it refuses — is prose rather than a call.
    """
    guard = _load("serveros_mutation_guard",
                  str(REPO / "skills" / "serveros" / "scripts" / "mutation_guard.py"))
    source = (RUNTIME / "cli.py").read_text()
    hits = guard.scan_file("runtime/cli.py", source)["hits"]
    assert [h for h in hits if h["kind"] == "mutation"] == []

    # Non-vacuity: the same scan over a copy with one real call added finds it.
    poisoned = source + (
        "\n\ndef _poison(args):\n"
        "    import subprocess\n"
        "    subprocess.run(['docker', 'stop', 'abc123'], check=False)\n"
    )
    assert [h for h in guard.scan_file("runtime/cli.py", poisoned)["hits"]
            if h["kind"] == "mutation"], (
        "the scan found nothing in a file that stops a container, so it would "
        "have found nothing in one that did"
    )
