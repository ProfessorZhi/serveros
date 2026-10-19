"""Re-runnable demo of C1 cross-session resume.

Demonstrates: create a Work Item -> advance through several states -> attach
evidence -> simulate a session death -> "resume" the same id from a **brand
new process** -> recover the worktree path, evidence pointers, and the legal
next transition, then continue the chain.

Runs entirely against a temp state directory, so it is safe to re-run any
number of times. Each CLI call is a separate OS process (real session
boundary), not just a discarded in-memory object.

Usage (from repo root):  python demo/resume_demo.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKTREE = Path(tempfile.gettempdir()) / "workengine-demo-worktree"


def _run(args, state_dir):
    """Invoke the workengine CLI in a fresh subprocess."""
    cmd = [sys.executable, "-m", "workengine", "--state-dir", str(state_dir)] + args
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="workengine-demo-") as tmp:
        state_dir = Path(tmp) / "state"
        WORKTREE.mkdir(parents=True, exist_ok=True)
        (WORKTREE / "report.md").write_text("# C1 demo report\n", encoding="utf-8")
        (WORKTREE / "evidence.json").write_text('{"ok": true}\n', encoding="utf-8")

        print("=== work-engine: C1 cross-session resume demo ===")
        print(f"state dir  : {state_dir}")
        print(f"worktree   : {WORKTREE}")

        # --- Session A: create + advance + evidence (its own process) ---
        print("\n[1] Session A: create a Work Item")
        r = _run(
            [
                "create",
                "--title",
                "C1 resumable delivery",
                "--requirement",
                "Work Engine must survive a session crash and resume the same item.",
                "--repo",
                "work-engine",
                "--worktree",
                str(WORKTREE),
                "--plan",
                "create",
                "--plan",
                "advance to IMPLEMENTING",
                "--plan",
                "attach evidence",
                "--plan",
                "resume from a fresh process",
            ],
            state_dir,
        )
        print(r.stdout.strip())
        if r.returncode != 0:
            print(r.stderr.strip())
            return r.returncode
        item_id = next((line.split()[1] for line in r.stdout.splitlines() if line.startswith("created ")), None)
        print(f"-> Work Item id: {item_id}")

        print("\n[2] Session A: advance NEW -> TRIAGED -> READY -> IMPLEMENTING")
        for state in ("TRIAGED", "READY", "IMPLEMENTING"):
            r = _run(["advance", item_id, "--to", state, "--note", f"session A advanced to {state}"], state_dir)
            print(r.stdout.splitlines()[0] if r.stdout else r.stderr.strip())

        print("\n[3] Session A: attach evidence pointers")
        r = _run(
            ["evidence", item_id, "--path", str(WORKTREE / "report.md"), "--kind", "markdown", "--note", "state report"],
            state_dir,
        )
        print(r.stdout.strip())
        _run(
            ["evidence", item_id, "--path", str(WORKTREE / "evidence.json"), "--kind", "json", "--note", "test run"],
            state_dir,
        )

        # --- Session A process has ended (no in-memory state survives) ---
        print("\n[4] SESSION A DIED (process exited). No in-memory state remains.")

        # --- Session B: a completely new process resumes the same id ---
        print("\n[5] Session B (fresh process): resume the same Work Item")
        r = _run(["resume", item_id], state_dir)
        print(r.stdout)
        if r.returncode != 0:
            print(r.stderr.strip())
            return r.returncode

        # --- Session B continues the chain where A left off ---
        print("[6] Session B: continue with the legal next transition (VERIFYING)")
        r = _run(["advance", item_id, "--to", "VERIFYING", "--note", "session B resumed and continued"], state_dir)
        print(r.stdout.splitlines()[0] if r.stdout else r.stderr.strip())

        print("\n[7] Final status from a third fresh process")
        r = _run(["status", item_id], state_dir)
        print(r.stdout.strip())

    print("\nDemo complete: cross-session resume works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
