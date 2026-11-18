"""Device directory governance (R-1..R-3): scan, plan, migrate, reclaim.

Built on throwaway homes with real git repositories and worktrees: what the
tool decides about a checkout depends on what git says is in it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ws_under_test", ROOT / "distribution/workspace/ws.py")
ws = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ws
_spec.loader.exec_module(ws)

DAY = 86400


def git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def age(path, days):
    then = time.time() - days * DAY
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.utime(os.path.join(root, name), (then, then), follow_symlinks=False)
            except OSError:
                pass
    os.utime(path, (then, then))


class Home:
    """A device home with a remote, old-layout clones and worktrees."""

    def __init__(self, tmp: Path):
        self.tmp, self.home = tmp, tmp / "home"
        self.home.mkdir()
        self.remote = tmp / "remotes" / "Owner" / "app.git"
        self.remote.parent.mkdir(parents=True)
        git(tmp, "init", "-q", "--bare", str(self.remote))
        seed = tmp / "seed"
        self.url = f"file://{self.remote}"
        git(tmp, "clone", "-q", self.url, str(seed))
        (seed / "a.txt").write_text("a")
        git(seed, "add", ".")
        git(seed, "commit", "-qm", "a")
        git(seed, "push", "-q", "origin", "HEAD:main")
        git(seed, "push", "-q", "origin", "HEAD:feature/LIT-0226-x")
        git(seed, "push", "-q", "origin", "HEAD:old")
        self.layout = {
            "schema": "serveros/device-layout.v1", "device": "test", "principal": "t",
            "home": str(self.home), "work_root": str(self.home / "work"),
            "scan_roots": [str(self.home)],
            "zones": [{"path": "work/src", "kind": "managed"},
                      {"path": "work/worktrees", "kind": "managed", "retain_days": 14},
                      {"path": "work/scratch", "kind": "managed", "retain_days": 30}],
            "registered": [{"path": "projects", "purpose": "personal"}],
            "protected": [{"path": "srv", "purpose": "service"}],
            "legacy": [{"from": "workspace/src/*", "as": "repo"},
                       {"from": "src/*", "as": "repo"},
                       {"from": "workspace/worktrees/*/*", "as": "worktree"},
                       {"from": "claude-task.*", "as": "scratch"},
                       {"from": "backups", "as": "report"},
                       {"from": "old-artifacts", "to": "work/artifacts/{name}"}]}

    def clone(self, rel, branch="main"):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        git(self.tmp, "clone", "-q", "-b", branch, self.url, str(path))
        return path


def build(tmp: Path) -> Home:
    h = Home(tmp)
    main = h.clone("workspace/src/app")                       # the main clone, worked recently
    (main / "b.txt").write_text("b")
    git(main, "add", ".")
    git(main, "commit", "-qm", "b")
    git(main, "push", "-q", "origin", "HEAD:main")
    old = h.clone("src/app-copy")                             # a duplicate with nothing of its own
    git(old, "reset", "-q", "--hard", "HEAD~1")
    git(old, "branch", "-q", "--set-upstream-to=origin/main")
    age(old, 40)
    mine = h.clone("src/app-mine")                            # a duplicate with a local commit
    (mine / "c.txt").write_text("c")
    git(mine, "add", ".")
    git(mine, "commit", "-qm", "local only")
    age(mine, 40)
    wt = h.home / "workspace/worktrees/app"
    git(main, "worktree", "add", "-q", "-b", "feature/LIT-0226-x", str(wt / "feature-x"),
        "origin/feature/LIT-0226-x")
    git(wt / "feature-x", "branch", "-q", "--set-upstream-to=origin/feature/LIT-0226-x")
    git(main, "worktree", "add", "-q", str(wt / "stale"), "origin/old")
    git(main, "worktree", "add", "-q", "-b", "wip", str(wt / "dirty"), "origin/old")
    (wt / "dirty" / "wip.txt").write_text("not committed")
    age(wt / "stale", 30)
    age(wt / "dirty", 30)
    scratch = h.home / "claude-task.ABC"
    scratch.mkdir()
    (scratch / "note").write_text("x")
    age(scratch, 45)
    (h.home / "claude-task.NEW").mkdir()
    (h.home / "backups").mkdir()
    (h.home / "old-artifacts").mkdir()
    (h.home / "projects" / "mine").mkdir(parents=True)
    (h.home / "srv").mkdir()
    (h.home / "mystery").mkdir()
    (h.home / ".config").mkdir()
    return h


class TheToolUnderstandsTheOldLayout(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = build(Path(self._td.name))
        self.scan = ws.scan(self.h.layout, refs=[])
        self.plan = ws.plan(self.scan)
        self.by_from = {s["from"]: s for s in self.plan["steps"]}

    def tearDown(self):
        self._td.cleanup()

    def step(self, rel):
        return self.by_from[str(self.h.home / rel)]

    def test_every_old_directory_is_understood_or_reported(self):
        classes = {Path(i["path"]).name: i["class"] for i in self.scan["items"]}
        self.assertEqual(classes["projects"], "registered")
        self.assertEqual(classes["srv"], "protected")
        self.assertEqual(classes[".config"], "registered")
        self.assertEqual(classes["mystery"], "unregistered")
        self.assertEqual(self.step("mystery")["action"], "REPORT")
        for rel in ("projects", "srv", ".config"):
            self.assertNotIn(str(self.h.home / rel), self.by_from, f"{rel} gets no step")

    def test_one_main_clone_per_remote_moves_to_work_src(self):
        main = self.step("workspace/src/app")
        self.assertEqual((main["action"], main["to"]), ("MOVE_REPO", str(self.h.home / "work/src/app")))

    def test_a_duplicate_with_nothing_of_its_own_is_removed(self):
        self.assertEqual(self.step("src/app-copy")["action"], "REMOVE_CLONE")

    def test_a_duplicate_with_a_local_commit_is_moved_not_removed(self):
        mine = self.step("src/app-mine")
        self.assertEqual(mine["action"], "MOVE_REPO")
        self.assertIn("work of its own", mine["reason"])
        self.assertTrue(mine["to"].startswith(str(self.h.home / "work/src/app.")))

    def test_worktrees_move_by_item_id_or_go_when_stale_and_empty(self):
        active = self.step("workspace/worktrees/app/feature-x")
        self.assertEqual((active["action"], Path(active["to"]).name), ("MOVE_WORKTREE", "LIT-0226"))
        self.assertEqual(Path(active["to"]).parent, self.h.home / "work/worktrees/app")
        self.assertEqual(self.step("workspace/worktrees/app/stale")["action"], "REMOVE_WORKTREE")
        dirty = self.step("workspace/worktrees/app/dirty")
        self.assertEqual(dirty["action"], "MOVE_WORKTREE", "uncommitted work is never removed")

    def test_scratch_goes_past_its_limit_and_moves_before_it(self):
        self.assertEqual(self.step("claude-task.ABC")["action"], "REMOVE_DIR")
        self.assertEqual(self.step("claude-task.NEW")["action"], "MOVE_DIR")
        self.assertEqual(self.step("backups")["action"], "REPORT")
        self.assertEqual(self.step("old-artifacts")["to"], str(self.h.home / "work/artifacts/old-artifacts"))

    def test_a_path_a_service_uses_is_deferred(self):
        refs = [{"by": "unit app.service", "text": f"ExecStart={self.h.home}/workspace/src/app/run.sh"}]
        plan = ws.plan(ws.scan(self.h.layout, refs=refs))
        step = next(s for s in plan["steps"] if s["from"] == str(self.h.home / "workspace/src/app"))
        self.assertEqual(step["action"], "DEFER")
        self.assertIn("app.service", step["reason"])

    def test_every_step_says_how_to_undo_it(self):
        for step in self.plan["steps"]:
            if step["action"].startswith(("MOVE", "REMOVE")):
                self.assertTrue(step.get("undo"), step)


class ApplyingIsAskedForAndChecked(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.h = build(Path(self._td.name))
        self.plan = ws.plan(ws.scan(self.h.layout, refs=[]))

    def tearDown(self):
        self._td.cleanup()

    def test_nothing_happens_without_the_plans_own_id(self):
        with self.assertRaises(ws.LayoutError):
            ws.apply(self.plan, approve="someone-elses", refs=[])

    def test_a_dry_run_changes_nothing(self):
        before = sorted(p.name for p in self.h.home.iterdir())
        receipt = ws.apply(self.plan, approve=self.plan["plan_id"], refs=[])
        self.assertFalse(receipt["applied"])
        self.assertEqual(sorted(p.name for p in self.h.home.iterdir()), before)
        self.assertTrue(all(r["result"] in ("WOULD_DO", "SKIPPED") for r in receipt["results"]))

    def test_the_migration_moves_everything_and_git_still_works(self):
        receipt = ws.apply(self.plan, approve=self.plan["plan_id"], refs=[], really=True)
        failed = [r for r in receipt["results"] if r["result"] == "FAILED"]
        self.assertEqual(failed, [])
        work = self.h.home / "work"
        main = work / "src/app"
        self.assertTrue((main / ".git").is_dir())
        self.assertTrue((work / "worktrees/app/LIT-0226").is_dir())
        self.assertTrue((work / "worktrees/app/dirty/wip.txt").is_file(), "uncommitted work kept")
        self.assertFalse((self.h.home / "src/app-copy").exists())
        self.assertFalse((self.h.home / "claude-task.ABC").exists())
        self.assertTrue((work / "scratch/claude-task.NEW").is_dir())
        # The old paths are links to the new ones.
        self.assertTrue((self.h.home / "workspace/src/app").is_symlink())
        # git: the main clone knows its worktrees at their new places, and they work.
        listing = git(main, "worktree", "list", "--porcelain")
        self.assertIn(str(work / "worktrees/app/LIT-0226"), listing)
        self.assertEqual(git(work / "worktrees/app/LIT-0226", "rev-parse", "--is-inside-work-tree"), "true")
        self.assertIn("wip.txt", git(work / "worktrees/app/dirty", "status", "--porcelain"))
        # A receipt is kept.
        receipts = list((work / ".governance/receipts").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        self.assertTrue(json.loads(receipts[0].read_text())["applied"])

    def test_a_checkout_that_gained_work_since_the_plan_is_not_removed(self):
        copy = self.h.home / "src/app-copy"
        (copy / "new.txt").write_text("written after the plan")
        receipt = ws.apply(self.plan, approve=self.plan["plan_id"], refs=[], really=True,
                           only={"REMOVE_CLONE"})
        (row,) = receipt["results"]
        self.assertEqual(row["result"], "SKIPPED")
        self.assertIn("unique", row["why"])
        self.assertTrue(copy.exists())

    def test_a_protected_place_is_never_touched(self):
        plan = dict(self.plan)
        plan["steps"] = [{"id": 1, "action": "REMOVE_DIR", "from": str(self.h.home / "srv"), "to": None}]
        receipt = ws.apply(plan, approve=plan["plan_id"], refs=[], really=True)
        self.assertEqual(receipt["results"][0]["why"], "protected")
        self.assertTrue((self.h.home / "srv").exists())


class TheDevicesHaveLayouts(unittest.TestCase):
    def test_each_device_has_a_valid_layout_with_work_as_its_managed_root(self):
        for device in ("tokyo", "a6000", "fjpcos"):
            with self.subTest(device=device):
                layout = ws.load_layout(device)
                self.assertTrue(layout["work_root"].rstrip("\\/").endswith("work"))
                names = {Path(z["path"].replace("\\", "/")).name for z in layout["zones"]}
                self.assertLessEqual({"src", "worktrees", "scratch", "artifacts"}, names)

    def test_role_zones_follow_the_device_roles(self):
        names = {d: {Path(z["path"].replace("\\", "/")).name for z in ws.load_layout(d)["zones"]}
                 for d in ("tokyo", "a6000", "fjpcos")}
        self.assertIn("records", names["tokyo"])
        self.assertIn("logs", names["a6000"])
        self.assertLessEqual({"client-tests", "logs-pull"}, names["fjpcos"])

    def test_llm_lab_is_protected_on_a6000(self):
        protected = [p["path"] for p in ws.load_layout("dell")["protected"]]
        self.assertIn("/srv/llm-lab", protected)

    def test_the_cli_refuses_an_unknown_device(self):
        self.assertEqual(ws.main(["check", "--device", "nowhere"]), 2)


if __name__ == "__main__":
    unittest.main()
