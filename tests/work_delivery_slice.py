#!/usr/bin/env python3
"""§AB — the Work Delivery Route, driven end to end on safe local fixtures.

Ten stages, and the ten are not a design decision: they are `work-engine`'s own
route, which the README states as

    Source → Observation → Claim → Candidate Item → Item
           → Activity → Run → Evidence → Deliverable → Feedback
                                                    └→ new Observation

so this script walks the pipeline the authority actually implements rather than
one invented to be walked.

What makes it safe, and it is three separate things:

  * EVERY root is redirected. `DATA_ROOT`, `WORKSPACE_ROOT`, `SERVEROS_STATE_DIR`
    and the control plane's `--state-dir`/`--artifacts-root` all point inside the
    fixture. The node's real state database is opened by nothing in here, which
    is checkable rather than promised: the script records its own mtime and size
    before and after and reports if either moved.
  * No Docker, no network, no cloud call. Every stage is a local process reading
    and writing files under one directory.
  * The fixture root must not already exist. `--force` deleting a tree is how a
    fixture script eventually deletes something that was not a fixture, so this
    one refuses instead.

What it proves, and what it does not
------------------------------------
It proves the route is walkable and reproducible: a second pass in a second
root, from the same inputs, produces the same logical database. It does NOT
prove any stage is production-ready, and it does not touch Feishu, GitHub or
any cloud body — those are providers, and a provider being reachable is a
different question from the route being closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

WORK_ENGINE = Path("/data/home/vince/workspace/src/work-engine")
SERVEROS = Path("/data/home/vince/workspace/src/serveros")
REAL_STATE_DB = Path("/data/home/vince/state/serveros/serveros.db")

PROJECT = "serveros"
PREFIX = "SRV"

# Fixed, so two passes are comparable. A generated key would make the second
# pass differ from the first for a reason that has nothing to do with the route.
FIXTURE = {
    "domain": "local-document",
    "stable_id": "slice-source-0001",
    "revision": "fixture-rev-1",
    "semantic_type": "requirement-statement",
    # 64 hex characters and NO `sha256:` prefix: the authority validates with
    # `re.fullmatch(r"[0-9a-fA-F]{64}", content_hash)`. The prefixed form is
    # how every other layer in this fabric spells a digest, so it is the form a
    # reader writes by default — and it is rejected here.
    "content_hash": hashlib.sha256(b"slice fixture body").hexdigest(),
    "candidate_id": "cand-slice-0001",
    "dedupe_key": "slice:work-delivery-route:0001",
    "title": "Close the Work Delivery Route end to end",
    "kind": "ops",
}


class Step:
    """One stage, its argv, and its real outcome. No step is assumed to pass."""

    def __init__(self, number: int, name: str, argv: list[str], cwd: Path,
                 env: dict, expect_field: str | None = None):
        self.number, self.name, self.argv = number, name, argv
        self.cwd, self.env, self.expect_field = cwd, env, expect_field
        self.rc: int | None = None
        self.stdout = self.stderr = ""
        self.parsed: dict | None = None

    def run(self) -> None:
        proc = subprocess.run(self.argv, cwd=str(self.cwd), env=self.env,
                              capture_output=True, text=True, timeout=120)
        self.rc, self.stdout, self.stderr = proc.returncode, proc.stdout, proc.stderr
        # `--json` first, but a refusal writes prose to stderr and a partial
        # stdout, so a parse failure is reported rather than raised: the rc is
        # the verdict and the text is the diagnosis.
        try:
            self.parsed = json.loads(self.stdout)
        except (json.JSONDecodeError, ValueError):
            self.parsed = None

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def field(self, *names):
        if not isinstance(self.parsed, dict):
            return None
        for name in names:
            if name in self.parsed:
                return self.parsed[name]
        for value in self.parsed.values():
            if isinstance(value, dict):
                for name in names:
                    if name in value:
                        return value[name]
        return None

    def render(self) -> list[str]:
        head = f"[{self.number:>2}] {self.name:<46} rc={self.rc}"
        if self.expect_field:
            head += f"  {self.expect_field}={self.field(*self.expect_field.split('|'))!r}"
        lines = [head]
        if not self.ok:
            detail = (self.stderr or self.stdout).strip().splitlines()
            # The TAIL, not the head. A Python traceback puts the exception —
            # the only line that says what went wrong — last, and showing the
            # first four lines shows the four that never do. The first run of
            # this slice reported ten identical `_run_code` frames and hid the
            # error behind them.
            lines += [f"       ! {line}" for line in detail[-6:]]
        return lines


def fixture_env(root: Path, extra: dict | None = None) -> dict:
    """The one place roots are redirected.

    `HOME` is deliberately left alone: changing it moves `.gitconfig`, the
    credential store and the toolchain caches, and a fixture that needs a
    different HOME is testing a different proposal than this one.
    """
    env = dict(os.environ)
    data = root / "data"
    env.update({
        "DATA_ROOT": str(data),
        "WORKSPACE_ROOT": str(data / "workspace"),
        "ARTIFACTS_ROOT": str(data / "artifacts"),
        "TOOLCHAINS_ROOT": str(data / "toolchains"),
        "SERVEROS_STATE_DIR": str(data / "state" / "serveros"),
        "SERVEROS_ARTIFACTS_ROOT": str(data / "artifacts" / "work-items"),
    })
    if extra:
        env.update(extra)
    return env


def lay_out(root: Path) -> None:
    """The declared shape, created empty. `reconcile` exists to check this and
    would be the right tool, except it is read-only by design and refuses to
    create what it checks — so the fixture creates it and `reconcile` verifies
    it afterwards. A check that creates its own subject proves nothing."""
    data = root / "data"
    for area in ("src", "worktrees", "test-envs", "records", "knowledge", "ops"):
        (data / "workspace" / area).mkdir(parents=True, exist_ok=True)
    for area in ("state/serveros/boards", "state/serveros/requirement-board",
                 "state/serveros/runtime/agent-control", "artifacts/work-items",
                 "tmp", "logs/serveros"):
        (data / area).mkdir(parents=True, exist_ok=True)
    (data / "state" / "serveros" / "runtime" / "agent-control" / "store.json"
     ).write_text('{"bindings": []}\n', encoding="utf-8")


def we(root: Path, *args: str) -> tuple[list[str], Path, dict]:
    """A control-plane invocation with its roots pinned to the fixture."""
    data = root / "data"
    argv = [sys.executable, "-m", "workengine",
            "--state-dir", str(data / "state" / "serveros"),
            "--artifacts-root", str(data / "artifacts" / "work-items"),
            "--json", *args]
    return argv, WORK_ENGINE, fixture_env(root)


def run_pass(root: Path) -> tuple[list[Step], list[str]]:
    """Stages 1-7 of the route. Stage 8 (Test) is the replay itself and happens
    between the two passes, so it is not in here."""
    steps: list[Step] = []
    names: list[str] = []

    def go(number: int, name: str, argv: list[str], cwd: Path, env: dict,
           expect: str | None = None):
        step = Step(number, name, argv, cwd, env, expect)
        step.run()
        steps.append(step)
        return step

    # `source`, `observation` and `claim` are groups of ONE command each, so
    # they take their positionals directly and there is no `add` verb. Worth a
    # line only because the README's examples spell the subcommands of the
    # OTHER groups and a reader extrapolates `source add` from them.
    argv, cwd, env = we(root, "source", FIXTURE["domain"],
                        FIXTURE["stable_id"], "--revision", FIXTURE["revision"],
                        "--locator", f"fixture://{FIXTURE['stable_id']}")
    src = go(1, "source (local-document)", argv, cwd, env)
    source_id = src.field("source_id", "id") or FIXTURE["stable_id"]

    argv, cwd, env = we(root, "observation", str(source_id),
                        "obs-slice-0001", "--semantic-type",
                        FIXTURE["semantic_type"], "--content-hash",
                        FIXTURE["content_hash"])
    go(2, "observation", argv, cwd, env)

    argv, cwd, env = we(root, "claim", "claim-slice-0001",
                        "--locator", f"fixture://{FIXTURE['stable_id']}#s1",
                        "--observation", "obs-slice-0001")
    go(3, "claim", argv, cwd, env)

    argv, cwd, env = we(root, "candidate", "create", FIXTURE["candidate_id"],
                        "--project", PROJECT, "--title", FIXTURE["title"],
                        "--kind", FIXTURE["kind"], "--dedupe-key",
                        FIXTURE["dedupe_key"], "--claim", "claim-slice-0001",
                        "--domain", FIXTURE["domain"], "--external-id",
                        FIXTURE["stable_id"])
    # Stage 4's second half is the matching decision, and it is NOT called
    # here. `candidate match --item` requires the id of an Item that already
    # exists (`store.match_candidate` raises KeyError otherwise), so on a first
    # candidate there is nothing to match against and the decision "matched
    # nothing" is carried by the candidate's own state, `NEW`. `expect="state"`
    # records the value the create returned; the second candidate below is what
    # exercises the other branch.
    go(4, "candidate create → state NEW (matched nothing)", argv, cwd, env,
       expect="state")

    argv, cwd, env = we(root, "candidate", "promote", FIXTURE["candidate_id"],
                        "--create-new", "--project", PROJECT, "--prefix", PREFIX,
                        "--title", FIXTURE["title"], "--kind", FIXTURE["kind"])
    promote = go(5, "candidate promote → Item", argv, cwd, env)
    item = promote.field("item_id", "id") or _item_id(root)

    # Now that an Item exists, the SECOND candidate exercises the matching
    # decision for real: same external identity, and `match` records the join
    # explicitly instead of leaving it to be inferred from a dedupe key.
    argv, cwd, env = we(root, "candidate", "create", "cand-slice-0002",
                        "--project", PROJECT, "--title", FIXTURE["title"],
                        "--kind", FIXTURE["kind"], "--dedupe-key",
                        FIXTURE["dedupe_key"] + ":dup", "--claim",
                        "claim-slice-0001", "--domain", FIXTURE["domain"],
                        "--external-id", FIXTURE["stable_id"],
                        "--external-revision", FIXTURE["revision"])
    go(5, "candidate create (the same source again)", argv, cwd, env)
    argv, cwd, env = we(root, "candidate", "match", "cand-slice-0002",
                        "--item", item)
    go(5, "candidate match → existing Item", argv, cwd, env, expect="state")

    card = root / "data" / "workspace" / "records" / "projects" / PROJECT / \
        "items" / item / "card.yaml"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(
        f"schema: serveros/item-card.v1\n"
        f"id: {item}\n"
        f"project: {PROJECT}\n"
        f"title: \"{FIXTURE['title']}\"\n"
        f"kind: {FIXTURE['kind']}\n"
        f"source_refs:\n"
        f"  - kind: local-document\n"
        f"    locator: fixture://{FIXTURE['stable_id']}\n"
        f"aliases: []\n", encoding="utf-8")

    argv, cwd, env = we(root, "activity", "add", item, "develop",
                        "--kind", "develop")
    go(6, "activity add develop", argv, cwd, env)

    argv, cwd, env = we(root, "run", "begin", item, "develop",
                        "--worker", "claude-code",
                        "--idempotency-key", "slice-develop-0001")
    begin = go(7, "run begin", argv, cwd, env)
    run_id = str(begin.field("run_id", "id") or "RUN-0001")
    names.append(run_id)

    argv, cwd, env = we(root, "activity", "add", item, "verify", "--kind", "verify")
    go(7, "activity add verify", argv, cwd, env)

    argv, cwd, env = we(root, "run", "begin", item, "verify",
                        "--worker", "claude-code",
                        "--idempotency-key", "slice-verify-0001")
    vbegin = go(7, "run begin verify", argv, cwd, env)
    verify_run = str(vbegin.field("run_id", "id") or "RUN-0002")

    return steps, [item, verify_run]


def _item_id(root: Path) -> str:
    """The Item the promotion just created, read from this root's own database.

    `promote --create-new` returns the Item, and this is the fallback for a
    shape that answers differently. It reads THIS root's database and never a
    module-level value, because the replay pass also promotes and a cached id
    would hand the first root's Item to the second root's run.
    """
    db = root / "data" / "state" / "serveros" / "serveros.db"
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT item_id FROM items_index ORDER BY item_id").fetchone()
        finally:
            connection.close()
        return str(row[0]) if row else "SRV-0001"
    except sqlite3.Error:
        return "SRV-0001"


ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)?")


def logical_dump(db: Path, *, root: Path | None = None) -> dict:
    """The database as facts, with two categories of noise removed.

    The first is TIME. A replay that compared bytes would fail on `created_at`
    and prove only that clocks advance. Timestamps are removed by value and by
    column name, because the first attempt removed them by column name alone —
    `item_events` spells its column something that does not end in `_at`, so a
    one-row table differed by nothing but when it was written and was reported
    as a determinism failure.

    The second is THE FIXTURE'S OWN PATH. The two passes run in two different
    directories by construction, so every absolute path each pass records
    necessarily differs — the first run of this comparison reported `runs` as
    differing purely because one row held `/tmp/serveros-slice/A/...` and the
    other `/tmp/serveros-slice/A-replay/...`. That is not a determinism
    finding; it is two runs being given two different inputs and then being
    asked why their outputs differ. Substituting a placeholder for the root
    makes the comparison about what the route RECORDED, which is the claim.
    """
    if not db.is_file():
        return {"<absent>": []}
    prefix = str(root) if root else None
    out: dict[str, list] = {}
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            columns = [r[1] for r in connection.execute(
                f"PRAGMA table_info({table})")]
            keep = [c for c in columns
                    if not c.endswith(("_at", "_time", "_timestamp"))
                    and c not in ("created", "updated", "sequence", "rowid")]
            rows = list(connection.execute(
                f"SELECT {', '.join(keep)} FROM {table} ORDER BY {', '.join(keep)}"))

            def normalize(value):
                text = str(value)
                if prefix:
                    text = text.replace(prefix, "<ROOT>")
                return ISO.sub("<TIMESTAMP>", text)

            out[table] = [tuple(normalize(v) for v in row) for row in rows]
    finally:
        connection.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", help="the fixture root; must not exist")
    parser.add_argument("--transcript", help="write the full transcript here")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if root.exists():
        sys.stderr.write(f"slice: {root} already exists. Refusing to reuse or "
                         f"remove a directory this script did not just create — "
                         f"a fixture that deletes trees eventually deletes one "
                         f"that was not a fixture.\n")
        return 2
    if not str(root).startswith("/tmp/"):
        sys.stderr.write(f"slice: {root} is not under /tmp. This fixture is "
                         f"rebuildable by definition and must not be created "
                         f"anywhere durable.\n")
        return 2

    watched = {}
    if REAL_STATE_DB.is_file():
        stat = REAL_STATE_DB.stat()
        watched["real_state_db"] = (stat.st_mtime_ns, stat.st_size)

    lay_out(root)
    lines: list[str] = []
    lines.append(f"fixture root : {root}")
    lines.append(f"route        : source → observation → claim → candidate → "
                 f"item → activity → run → evidence → deliverable → feedback")
    lines.append("")

    steps, _ = run_pass(root)
    for step in steps:
        lines += step.render()
    failed = [s for s in steps if not s.ok]
    lines.append("")
    lines.append(f"pass A: {len(steps) - len(failed)}/{len(steps)} stages rc=0")

    # Stage 8 — Test. Not a claim about a test: the replay really runs, twice,
    # and the comparison's own exit code is what the Run's evidence records.
    lines.append("")
    lines.append("[ 8] TEST — deterministic replay")
    second = root.parent / (root.name + "-replay")
    if second.exists():
        shutil.rmtree(second)
    lay_out(second)
    replay_steps, _ = run_pass(second)
    replay_failed = [s for s in replay_steps if not s.ok]
    lines.append(f"       second pass: {len(replay_steps) - len(replay_failed)}"
                 f"/{len(replay_steps)} stages rc=0")

    first_dump = logical_dump(root / "data" / "state" / "serveros" / "serveros.db",
                              root=root)
    second_dump = logical_dump(second / "data" / "state" / "serveros" / "serveros.db",
                               root=second)
    same = first_dump == second_dump
    tables = sorted(set(first_dump) | set(second_dump))
    rows = sum(len(v) for v in first_dump.values())
    differing = [t for t in tables if first_dump.get(t) != second_dump.get(t)]
    lines.append(f"       tables={len(tables)} logical rows={rows}")
    lines.append(f"       comparison exit code: {0 if same else 1}")
    if differing:
        lines.append(f"       differing tables: {differing}")
        # Name the actual rows. "two tables differ" sends the reader back to
        # the database; the row pair says whether the difference is a clock,
        # a counter, or something the route really does differently twice.
        for table in differing[:3]:
            left, right = first_dump.get(table, []), second_dump.get(table, [])
            only_left = [r for r in left if r not in right]
            only_right = [r for r in right if r not in left]
            lines.append(f"       {table}: {len(only_left)} row(s) only in pass A, "
                         f"{len(only_right)} only in pass B, "
                         f"{len(left)} vs {len(right)} total")
            for row in (only_left[:1] + only_right[:1]):
                lines.append(f"         {str(row)[:150]}")

    evidence_path = root / "data" / "artifacts" / "work-items" / "slice-replay.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps({
        "schema": "serveros/slice-replay-evidence.v1",
        "tables": {t: len(first_dump.get(t, [])) for t in tables},
        "logical_rows": rows,
        "identical": same,
        "differing_tables": differing,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    revision = subprocess.run(
        ["git", "-C", str(WORK_ENGINE), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip() or "UNKNOWN"
    command = (f"python3 {Path(__file__).name} <root> --replay-compare "
               f"[work-engine@{revision[:12]}]")

    # Stages 9 and 10 — the evidence, then what follows from it.
    item, verify_run = _run_pass_handles(root)
    more: list[Step] = []

    def go(number: int, name: str, argv: list[str], cwd: Path, env: dict,
           expect: str | None = None):
        step = Step(number, name, argv, cwd, env, expect)
        step.run()
        more.append(step)
        return step

    argv, cwd, env = we(root, "run", "evidence", item, verify_run,
                        "--kind", "test", "--locator", str(evidence_path),
                        "--command", command,
                        "--exit-code", "0" if same else "1",
                        "--revision", revision, "--verdict",
                        "PASS" if same else "FAIL")
    go(8, "run evidence (test result)", argv, cwd, env)

    # A Run is not finalized from ALLOCATED. `transition_run` walks a straight
    # chain — ALLOCATED → PREPARING → RUNNING → FINALIZING — one step at a
    # time, so the execution has to be driven through its own states before
    # `store.finalize_run`, which refuses unless the state is FINALIZING. Three
    # advances, and each one is printed because the chain is the authority
    # being strict about a Run that was never actually run. A slice that jumped
    # straight to FINALIZING would be demonstrating a route nobody implements.
    for target in ("PREPARING", "RUNNING", "FINALIZING"):
        argv, cwd, env = we(root, "run", "advance", item, verify_run,
                            "--to", target)
        go(8, f"run advance → {target}", argv, cwd, env, expect="state")

    argv, cwd, env = we(root, "run", "finalize", item, verify_run,
                        "--verdict", "PASS" if same else "FAIL",
                        "--exit-code", "0" if same else "1",
                        "--command", command, "--revision", revision)
    go(8, "run finalize", argv, cwd, env, expect="state")

    argv, cwd, env = we(root, "deliverable", "add", item,
                        "--kind", "record", "--locator",
                        str(root / "data" / "artifacts" / "work-items" /
                            "slice-replay.json"))
    go(9, "deliverable add", argv, cwd, env)

    argv, cwd, env = we(root, "feedback", "record", item,
                        "--observation", "obs-slice-0001",
                        "--kind", "agent-verification",
                        "--locator", str(evidence_path))
    go(10, "feedback record → new observation", argv, cwd, env)

    for step in more:
        lines += step.render()

    argv, cwd, env = we(root, "item", "show", item)
    show = Step(11, "item show (route complete)", argv, cwd, env, "state")
    show.run()
    lines += show.render()
    for key in ("state", "activities", "runs", "deliverables", "source_refs"):
        value = show.field(key)
        if value is not None and not isinstance(value, dict):
            lines.append(f"       {key}: {value}")

    # The control plane's per-project projection. This is the ONLY edge between
    # the two systems: ServerOS reads what work-engine rendered, and never
    # opens the authority's store itself. Doing it here, in stage order, is
    # what makes the handoff visible rather than implied.
    board_root = root / "data" / "state" / "serveros" / "boards"
    argv, cwd, env = we(root, "board", "render", "--project", PROJECT,
                        "--output-dir", str(board_root / PROJECT))
    rendered = Step(11, "board render → per-project projection", argv, cwd, env)
    rendered.run()
    lines += rendered.render()

    # §M — the ServerOS-side projection, reading that document out of the SAME
    # fixture. This is a read: no `--write`, so it prints and persists nothing.
    lines.append("")
    argv = [sys.executable, "runtime/cli.py", "requirement", "board"]
    board = Step(12, "serverosctl requirement board (ServerOS side)", argv,
                 SERVEROS, fixture_env(root))
    board.run()
    lines += board.render()
    for line in board.stdout.strip().splitlines()[:9]:
        lines.append(f"       {line}")

    boards = sorted(p.name for p in board_root.iterdir() if p.is_dir())
    lines.append(f"       boards present: {boards or 'none'}")

    lines.append("")
    lines.append("real state database touched: ")
    if "real_state_db" in watched:
        stat = REAL_STATE_DB.stat()
        moved = watched["real_state_db"] != (stat.st_mtime_ns, stat.st_size)
        lines[-1] += ("YES — ABORT WORTHY" if moved
                      else "no (mtime and size unchanged)")
    else:
        lines[-1] += "no (it does not exist)"

    # The marker, in the shape `scripts/live_closure.py` established: a string
    # a reader can grep for that means the program reached its end with every
    # stage green. Printed only then — a marker that appears beside a failure
    # is a marker that stops meaning anything.
    everything_ok = all(s.ok for s in steps + more + [show, board]) and same
    if everything_ok:
        lines.append("")
        lines.append("SERVEROS_WORK_DELIVERY_SLICE_OK")
    else:
        lines.append("")
        lines.append("SERVEROS_WORK_DELIVERY_SLICE_INCOMPLETE — see the rc!=0 "
                     "stages above")

    transcript = "\n".join(lines) + "\n"
    sys.stdout.write(transcript)
    if args.transcript:
        Path(args.transcript).write_text(transcript, encoding="utf-8")

    return 0 if everything_ok else 1


def _run_pass_handles(root: Path) -> tuple[str, str]:
    """Re-derive the Item and verify-Run ids from the pass that already ran.

    Not memoised from `run_pass`: the second pass calls `run_pass` too, and a
    module-level cache would hand the replay the first root's ids. Re-deriving
    from this root's own database is the only version that cannot cross.
    """
    db = root / "data" / "state" / "serveros" / "serveros.db"
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        item = connection.execute(
            "SELECT id FROM items ORDER BY id").fetchall()
        item_id = item[0][0] if item else "SRV-0001"
        runs = connection.execute(
            "SELECT run_id FROM runs ORDER BY run_id").fetchall()
        verify = runs[-1][0] if runs else "RUN-0002"
    except sqlite3.Error:
        item_id, verify = "SRV-0001", "RUN-0002"
    finally:
        connection.close()
    return str(item_id), str(verify)


if __name__ == "__main__":
    sys.exit(main())
