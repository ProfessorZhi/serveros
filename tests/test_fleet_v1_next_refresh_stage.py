"""Refresh an already-staged Next revision WITHOUT starting anything.

The gap this closes: `stage` refuses a populated revision root (correct — it is
a first install), and `install` refuses an unassigned public URL and Plugin ID
(correct — it is a live cutover). Between the two there was no permitted way to
publish a NEWER revision over the staged one, so a staged Next could only be
updated by hand — which is exactly what a non-starting flow must not leave as
the only option.

`refresh-stage` is that flow, and this file pins what makes it safe rather than
merely convenient:

* the Next unit must be **inactive** — repointing a running service's revision
  without restarting it is the one change a non-starting flow must never make;
* the revision root must be **populated** — with nothing there is nothing to
  refresh, and `stage` is the command for that;
* the new revision must **differ** from the one `current` already names;
* the **source tree** must be clean and unheld, because a revision name is a
  claim about bytes;
* the OLD `current` is kept as **`previous`**, so the update is reversible;
* `--no-start` and never `--force` reach the shared installer;
* a failure reports Next-only recovery and never touches V1.

Offline throughout: no systemd, no socket, no credential, no real install.
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.deploy import next_install, next_dual_stack_preflight as gate  # noqa: E402

V1_CONFIG = "/home/operator/.config/old-fleet/config.yaml"
NEXT_CONFIG = "/home/operator/.config/new-fleet/config.yaml"
NEXT_UNIT_PATH = ("/home/operator/.config/systemd/user/"
                  "serveros-fleet-next-mcp.service")
OLD_REV = "aa17512"
OLD_LABEL = f"fleet-mcp-20261015@{OLD_REV}"


def candidate() -> dict:
    """An unpublished bootstrap proposal: URL and Plugin ID still unassigned."""
    return {
        "schema": gate.SCHEMA,
        "v1": {
            "service_unit": gate.V1_UNIT,
            "runtime_root": "/home/operator/runtime-assets/old-ingress/ingress",
            "config_path": V1_CONFIG,
            "oauth_state_root": "/home/operator/.local/state/old-fleet-oauth",
            "local_listen": {"host": "127.0.0.1", "port": 8401},
            "public_mcp_url": "https://v1.example.invalid/mcp",
            "plugin_id": "plugin_asdk_app_existing_v1",
        },
        "next": {
            "service_unit": gate.NEXT_UNIT,
            "runtime_root": "/home/operator/runtime-assets/new-ingress/ingress-next",
            "config_path": NEXT_CONFIG,
            "oauth_state_root": "/home/operator/.local/state/new-fleet-oauth",
            "local_listen": {"host": "127.0.0.1", "port": 8402},
            "public_mcp_url": None,
            "plugin_id": None,
        },
        "limits": dict(next_install.BUDGET),
        "attestations": {
            "v1_identity_observed": True,
            "next_owner_binding_reviewed": True,
            "next_oauth_client_authorized": False,
            "rollback_ready": True,
        },
    }


class FakeMod:
    """Models the shared installer's REAL pointer behaviour, not a stub answer.

    `main()` moves `previous <- current` and `current <- new revision`, which is
    what `install.py::install()` does; a fake that returned fixed pointers could
    not tell a correct wrapper from one that reads the rollback target from the
    wrong place.
    """

    def __init__(self, *, current=None, previous=None, unit_state="inactive",
                 enabled_state="disabled", enabled_error=False,
                 exit_code=0, raise_systemexit=False, revision="b" * 40,
                 extra_receipt=None):
        self.root_current = Path(current) if current else None
        self.root_previous = Path(previous) if previous else None
        self.unit_state = unit_state
        self.enabled_state = enabled_state
        self.enabled_error = enabled_error
        self.exit_code = exit_code
        self.raise_systemexit = raise_systemexit
        self.revision = revision
        self.extra_receipt = dict(extra_receipt or {})
        self.calls: list[list[str]] = []
        self.systemctl_calls: list[tuple] = []

    def binding(self, path):
        old = str(path) == V1_CONFIG
        return {"port": 8401 if old else 8402, "bind": "127.0.0.1",
                "probe": f"http://127.0.0.1:{8401 if old else 8402}"}

    def read_link(self, path):
        if path.name == "current":
            return self.root_current
        if path.name == "previous":
            return self.root_previous
        return None

    def systemctl(self, *args, check=True):
        self.systemctl_calls.append(args)
        if args[:1] == ("is-active",):
            return SimpleNamespace(returncode=0,
                                   stdout=self.unit_state + "\n", stderr="")
        if args[:1] == ("is-enabled",):
            if self.enabled_error:
                raise RuntimeError("systemctl is-enabled could not be read")
            return SimpleNamespace(returncode=0,
                                   stdout=self.enabled_state + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="inactive\n", stderr="")

    def main(self, args):
        self.calls.append(list(args))
        if self.raise_systemexit:
            raise SystemExit(2)
        if self.exit_code != 0:
            return self.exit_code
        self.root_previous = self.root_current
        self.root_current = Path(f"/rev/fleet-mcp-20261015@{self.revision[:7]}")
        payload = {
            "unit_path": NEXT_UNIT_PATH,
            "started": False,
            "revision": self.revision,
            "tree_sha256": "0" * 64,
            "next": f"systemctl --user start {gate.NEXT_UNIT}",
        }
        payload.update(self.extra_receipt)
        print(json.dumps(payload, indent=2))
        return 0


def run(argv: list[str], proposal: dict, mod=None,
        source: str | None = None) -> tuple[int, dict]:
    extra = list(argv[1:])
    if source is not None:
        extra += ["--source", source]
    with tempfile.TemporaryDirectory() as temp:
        p = Path(temp) / "proposal.json"
        p.write_text(json.dumps(proposal), encoding="utf-8")
        stdout = io.StringIO()
        patch = (mock.patch.object(next_install, "_load_legacy_installer",
                                   return_value=mod) if mod is not None else
                 contextlib.nullcontext())
        with patch, contextlib.redirect_stdout(stdout):
            rc = next_install.main([argv[0], "--proposal", str(p)] + extra)
    text = stdout.getvalue()
    payload, problem = next_install.one_receipt(text)
    if problem is not None:
        raise AssertionError(f"receipt contract: {problem}; raw={text[:200]!r}")
    return rc, payload


def gitrepo(*, dirty: bool = False) -> tempfile.TemporaryDirectory:
    """A real repository, because `_source_state` reads real git state."""
    temp = tempfile.TemporaryDirectory(prefix="fleet-refresh-src-")
    root = Path(temp.name)
    run_git = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(root), *a], capture_output=True, text=True, check=True)
    run_git("init", "-q")
    run_git("config", "user.email", "t@example.invalid")
    run_git("config", "user.name", "t")
    (root / "fleet_mcp").mkdir()
    (root / "fleet_mcp" / "marker.py").write_text("x = 1\n", encoding="utf-8")
    (root / "fleet").mkdir()
    run_git("add", "-A")
    run_git("commit", "-q", "-m", "base")
    if dirty:
        (root / "fleet_mcp" / "marker.py").write_text("x = 2\n", encoding="utf-8")
    return temp


def head_of(repo: str) -> str:
    """The full SHA `git rev-parse HEAD` reports for a real repository."""
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


class RefreshStageAcceptsAnIncrementalPublishTest(unittest.TestCase):
    def test_the_dry_run_contract_states_the_enablement_precondition(self):
        """The preview is the artefact an operator approves; it must not be
        looser than the guard. A precondition absent from `requires` is one the
        operator is refused for after treating the preview as the approved plan,
        so the enablement rule and the `--revision` rule must both appear."""
        rc, out = run(["refresh-stage"], candidate())
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["decision"], "DRY_RUN_READY")
        requires = " ".join(out["requires"])
        self.assertIn("DISABLED", requires)
        self.assertIn("byte-for-byte", requires)

    def test_refresh_writes_only_next_paths_and_keeps_the_old_current(self):
        with gitrepo() as repo:
            mod = FakeMod(current=f"/rev/{OLD_LABEL}", revision="c" * 40)
            rc, out = run(["refresh-stage", "--execute", "--owner-approved"],
                          candidate(), mod=mod, source=repo)
        self.assertEqual(rc, 0)
        self.assertEqual(out["decision"], "EXECUTED")
        # Exactly one call, to the Next unit, and it is a non-starting install.
        self.assertEqual(len(mod.calls), 1)
        self.assertEqual(mod.calls[0][0], "install")
        self.assertIn("--no-start", mod.calls[0])
        self.assertNotIn("--force", mod.calls[0])
        self.assertEqual(out["unit"], gate.NEXT_UNIT)
        self.assertEqual(out["executes"], [gate.NEXT_UNIT])
        self.assertFalse(out["v1_action"])

    def test_the_receipt_carries_the_real_pointers_and_is_not_started(self):
        with gitrepo() as repo:
            mod = FakeMod(current=f"/rev/{OLD_LABEL}", revision="c" * 40)
            rc, out = run(["refresh-stage", "--execute", "--owner-approved"],
                          candidate(), mod=mod, source=repo)
        self.assertEqual(rc, 0)
        self.assertEqual(out["started"], False)
        self.assertEqual(out["unit_path"], NEXT_UNIT_PATH)
        self.assertEqual(out["revision"], "c" * 40)
        # `current` moved to the new revision...
        self.assertTrue(out["current"].endswith(f"@{'c' * 7}"))
        # ...and `previous` is the revision that WAS current.
        self.assertTrue(out["previous"].endswith(f"@{OLD_REV}"))

    def test_the_old_previous_is_not_lost_when_one_already_existed(self):
        """`previous` must become the old `current`, not stay stale."""
        with gitrepo() as repo:
            mod = FakeMod(current=f"/rev/{OLD_LABEL}",
                          previous="/rev/fleet-mcp-20261014@b84f1ee",
                          revision="c" * 40)
            rc, out = run(["refresh-stage", "--execute", "--owner-approved"],
                          candidate(), mod=mod, source=repo)
        self.assertEqual(rc, 0)
        self.assertTrue(out["previous"].endswith(f"@{OLD_REV}"))

    def test_dry_run_has_no_effect_and_touches_no_system_state(self):
        with mock.patch.object(next_install, "_load_legacy_installer",
                               side_effect=AssertionError("dry run must not load it")):
            rc, out = run(["refresh-stage"], candidate())
        self.assertEqual(rc, 0)
        self.assertEqual(out["decision"], "DRY_RUN_READY")
        self.assertEqual(out["mode"], "NEXT_ONLY_REFRESH_STAGE_NO_START")
        self.assertEqual(out["writes"], [])
        self.assertEqual(out["executes"], [])

    def test_unpublished_url_and_plugin_id_are_allowed_only_here(self):
        """Same bootstrap concession as `stage` — and only for staging."""
        p = candidate()
        self.assertEqual(next_install.plan(p, "refresh-stage")["decision"],
                         "DRY_RUN_READY")
        self.assertEqual(next_install.plan(p, "install")["decision"], "BLOCKED")

    def test_execute_without_owner_approval_refuses_before_any_effect(self):
        with gitrepo() as repo:
            mod = FakeMod(current=f"/rev/{OLD_LABEL}", revision="c" * 40)
            rc, out = run(["refresh-stage", "--execute"], candidate(),
                          mod=mod, source=repo)
        self.assertEqual(rc, 2)
        self.assertEqual(mod.calls, [])
        self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")
        self.assertEqual(out["writes"], [])


class RefreshStageRefusesWhatItCannotMakeSafeTest(unittest.TestCase):
    def _refuse(self, mod, *, revision="c" * 40, argv_extra=()):
        with gitrepo() as repo:
            p = candidate()
            rc, out = run(["refresh-stage", "--execute", "--owner-approved",
                           *argv_extra], p, mod=mod, source=repo)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertEqual(mod.calls, [], "nothing may reach the installer")
        return out

    def test_an_active_next_unit_is_refused(self):
        out = self._refuse(FakeMod(current=f"/rev/{OLD_LABEL}",
                                   unit_state="active"))
        self.assertIn("active", out["reason"])
        self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")

    def test_a_failed_next_unit_is_refused_rather_than_repointed(self):
        """A unit that already failed is evidence; repointing hides it."""
        out = self._refuse(FakeMod(current=f"/rev/{OLD_LABEL}",
                                   unit_state="failed"))
        self.assertIn("failed", out["reason"])

    def test_an_unknown_unit_state_is_refused(self):
        out = self._refuse(FakeMod(current=f"/rev/{OLD_LABEL}",
                                   unit_state=""))
        self.assertIn("unknown", out["reason"])

    def test_an_empty_revision_root_is_not_a_refresh(self):
        out = self._refuse(FakeMod(current=None))
        self.assertIn("use `stage` for the first one", out["reason"])

    def test_the_revision_already_current_is_refused(self):
        """Re-pointing to the same revision would consume `previous` for nothing."""
        mod = FakeMod(current=f"/rev/{OLD_LABEL}", revision=OLD_REV)
        with gitrepo() as repo:
            # `current` names the revision the clean source already IS, so the
            # derived target equals it and the refusal is about the same-revision
            # repoint rather than about a `--revision` mismatch.
            mod.root_current = Path(f"/rev/fleet-mcp-20261015@{head_of(repo)[:7]}")
            rc, out = run(["refresh-stage", "--execute", "--owner-approved"],
                          candidate(), mod=mod, source=repo)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertEqual(mod.calls, [])
        self.assertIn("already", out["reason"])
        self.assertIn("previous", out["reason"])

    def test_an_enabled_next_unit_is_refused_even_when_inactive(self):
        """Linger is on: an enabled inactive unit starts the new code at boot."""
        out = self._refuse(FakeMod(current=f"/rev/{OLD_LABEL}",
                                   unit_state="inactive",
                                   enabled_state="enabled"))
        self.assertIn("disabled", out["reason"])
        self.assertIn("Linger", out["reason"])
        self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")

    def test_any_non_disabled_enablement_is_refused(self):
        """Fail closed: only the exact string `disabled` is safe."""
        for estate in ("static", "masked", "indirect", "generated", ""):
            with self.subTest(enablement=estate):
                out = self._refuse(FakeMod(current=f"/rev/{OLD_LABEL}",
                                           unit_state="inactive",
                                           enabled_state=estate))
                self.assertIn("disabled", out["reason"])

    def test_an_unreadable_enablement_is_refused(self):
        out = self._refuse(FakeMod(current=f"/rev/{OLD_LABEL}",
                                   unit_state="inactive",
                                   enabled_error=True))
        self.assertIn("enablement", out["reason"])

    def test_an_explicit_revision_that_is_not_head_is_refused(self):
        """An override would stamp these bytes with a name HEAD does not give them."""
        out = self._refuse(FakeMod(current=f"/rev/{OLD_LABEL}"),
                           argv_extra=("--revision", "d" * 40))
        self.assertIn("not the clean source", out["reason"])
        self.assertIn("HEAD", out["reason"])

    def test_an_explicit_revision_equal_to_head_is_accepted(self):
        """`--revision` may RESTATE the clean HEAD; that is the only value it may be."""
        mod = FakeMod(current=f"/rev/{OLD_LABEL}", revision="c" * 40)
        with gitrepo() as repo:
            rc, out = run(["refresh-stage", "--execute", "--owner-approved",
                           "--revision", head_of(repo)],
                          candidate(), mod=mod, source=repo)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["decision"], "EXECUTED")

    def test_a_short_head_sha_is_refused_and_the_message_is_actionable(self):
        """Byte-equality is the rule; a 7-char SHA is not the HEAD, however familiar.

        The refusal must show the two values distinguishably — printing both as
        `[:7]` would print two identical strings for a refusal that is precisely
        about them not being identical.
        """
        mod = FakeMod(current=f"/rev/{OLD_LABEL}")
        with gitrepo() as repo:
            head = head_of(repo)
            rc, out = run(["refresh-stage", "--execute", "--owner-approved",
                           "--revision", head[:7]],
                          candidate(), mod=mod, source=repo)
        self.assertEqual(rc, 2, out)
        self.assertEqual(mod.calls, [])
        self.assertIn("not the clean source", out["reason"])
        self.assertIn(head[:7], out["reason"])
        self.assertIn(head, out["reason"])

    def test_a_dirty_source_is_refused(self):
        """A revision name is a claim about bytes; dirty bytes break the claim."""
        mod = FakeMod(current=f"/rev/{OLD_LABEL}", revision="c" * 40)
        with gitrepo(dirty=True) as repo:
            rc, out = run(["refresh-stage", "--execute", "--owner-approved"],
                          candidate(), mod=mod, source=repo)
        self.assertEqual(rc, 2)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertIn("uncommitted", out["reason"])
        self.assertEqual(mod.calls, [])

    def test_a_source_that_is_not_a_repository_is_refused(self):
        mod = FakeMod(current=f"/rev/{OLD_LABEL}", revision="c" * 40)
        with tempfile.TemporaryDirectory() as plain:
            rc, out = run(["refresh-stage", "--execute", "--owner-approved"],
                          candidate(), mod=mod, source=plain)
        self.assertEqual(rc, 2)
        self.assertIn("git revision", out["reason"])

    def test_a_held_git_index_is_refused(self):
        """Another writer mid-operation means the tree can move under the copy."""
        mod = FakeMod(current=f"/rev/{OLD_LABEL}", revision="c" * 40)
        with gitrepo() as repo:
            gitdir = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"],
                capture_output=True, text=True, check=True).stdout.strip()
            lock = Path(gitdir) / "index.lock"
            lock.write_text("", encoding="utf-8")
            try:
                rc, out = run(["refresh-stage", "--execute", "--owner-approved"],
                              candidate(), mod=mod, source=repo)
            finally:
                lock.unlink()
        self.assertEqual(rc, 2)
        self.assertIn("index.lock", out["reason"])
        self.assertEqual(mod.calls, [])

    def test_next_paths_may_not_resolve_inside_v1(self):
        p = candidate()
        p["next"]["runtime_root"] = p["v1"]["runtime_root"] + "/ingress-next"
        mod = FakeMod(current=f"/rev/{OLD_LABEL}", revision="c" * 40)
        with gitrepo() as repo:
            rc, out = run(["refresh-stage", "--execute", "--owner-approved"],
                          p, mod=mod, source=repo)
        self.assertEqual(rc, 2)
        self.assertEqual(mod.calls, [])


class TheFirstStageIsUnchangedTest(unittest.TestCase):
    """`refresh-stage` must not have widened, narrowed or replaced `stage`."""

    def test_stage_still_demands_an_empty_revision_root(self):
        mod = FakeMod(current=f"/rev/{OLD_LABEL}")
        with gitrepo() as repo:
            rc, out = run(["stage", "--execute", "--owner-approved"],
                          candidate(), mod=mod, source=repo)
        self.assertEqual(rc, 2)
        self.assertIn("empty Next revision root", out["reason"])
        self.assertEqual(mod.calls, [])

    def test_stage_still_works_when_the_root_is_empty(self):
        with gitrepo() as repo:
            mod = FakeMod(current=None, revision="c" * 40)
            rc, out = run(["stage", "--execute", "--owner-approved"],
                          candidate(), mod=mod, source=repo)
        self.assertEqual(rc, 0)
        self.assertEqual(out["decision"], "EXECUTED")
        self.assertTrue(out["current"].endswith(f"@{'c' * 7}"))

    def test_the_unit_state_guard_belongs_to_refresh_and_not_to_stage(self):
        """The contrast, on the SAME running unit.

        `refresh-stage` refuses an active unit; `stage` does not consult it at
        all, because an empty revision root already means no Next process can
        be serving from this tree. Pinning both halves here is what keeps the
        new guard from being quietly extended onto the first-install path.
        """
        with gitrepo() as repo:
            refreshing = FakeMod(current=f"/rev/{OLD_LABEL}", unit_state="active",
                                 revision="c" * 40)
            rc, out = run(["refresh-stage", "--execute", "--owner-approved"],
                          candidate(), mod=refreshing, source=repo)
            self.assertEqual(rc, 2)
            self.assertIn("active", out["reason"])
            self.assertEqual(refreshing.calls, [])

            first = FakeMod(current=None, unit_state="active", revision="c" * 40)
            rc, out = run(["stage", "--execute", "--owner-approved"],
                          candidate(), mod=first, source=repo)
            self.assertEqual(rc, 0, out)
            self.assertEqual(len(first.calls), 1)


class FailureIsReportedAsNextOnlyRecoveryTest(unittest.TestCase):
    def test_a_nonzero_installer_exit_reports_next_only_recovery(self):
        mod = FakeMod(current=f"/rev/{OLD_LABEL}", exit_code=3)
        with gitrepo() as repo:
            rc, out = run(["refresh-stage", "--execute", "--owner-approved"],
                          candidate(), mod=mod, source=repo)
        self.assertEqual(rc, 2)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertEqual(out["effect_state"], "NEXT_ONLY_ACTION_ATTEMPTED")
        self.assertEqual(out["writes"], "POSSIBLE_OR_ROLLED_BACK")
        self.assertEqual(out["executes"], [gate.NEXT_UNIT])
        self.assertFalse(out["v1_action"])

    def test_an_unreadable_receipt_is_unknown_not_a_clean_failure(self):
        mod = FakeMod(current=f"/rev/{OLD_LABEL}", raise_systemexit=True)
        with gitrepo() as repo:
            rc, out = run(["refresh-stage", "--execute", "--owner-approved"],
                          candidate(), mod=mod, source=repo)
        self.assertEqual(rc, 2)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertEqual(out["effect_state"], "NEXT_ONLY_ACTION_ATTEMPTED")
        self.assertFalse(out["v1_action"])
        self.assertEqual(out["executes"], [gate.NEXT_UNIT])

    def test_recovery_addresses_only_the_next_unit_and_never_v1(self):
        mod = FakeMod(current=f"/rev/{OLD_LABEL}", exit_code=3)
        with gitrepo() as repo:
            run(["refresh-stage", "--execute", "--owner-approved"],
                candidate(), mod=mod, source=repo)
        for call in mod.systemctl_calls:
            with self.subTest(call=call):
                self.assertIn(gate.NEXT_UNIT, call)
                self.assertNotIn(gate.V1_UNIT, call)

    def test_no_path_in_the_flow_can_name_the_v1_unit(self):
        """A static guard: the wrapper's own source never addresses V1."""
        source = (ROOT / "fleet_mcp" / "deploy" / "next_install.py").read_text(
            encoding="utf-8")
        # `gate.V1_UNIT` may be READ (the proposal names it); it must never be
        # the target of a systemctl call. Every systemctl in this module is
        # built from the constant `UNIT`, which is `gate.NEXT_UNIT`.
        self.assertIn("UNIT = gate.NEXT_UNIT", source)
        for line in source.splitlines():
            if 'systemctl(' in line and 'def ' not in line:
                with self.subTest(line=line.strip()):
                    self.assertNotIn("V1_UNIT", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
