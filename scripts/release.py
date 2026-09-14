#!/usr/bin/env python3
"""Place a built artifact at a stable path and make a node run it.

Why this file exists
--------------------
`scripts/build.py` produces a runtime artifact for a composition. It has one
destination and it is a `current` directory, so before this script existed a
node could only be pointed at whatever directory last received a build — and
when the build was run from a worktree, that is what the systemd unit named.

That is not an installation. A worktree is per-Item and is retired when the
Item closes; a unit pointing at one is a service that stops starting on a day
nobody chose. The pilot unit said so in its own header, which is how this file
came to exist.

What it does instead
--------------------
    <runtime-root>/<composition>/revisions/<revision>/   the artifact, immutable
    <runtime-root>/<composition>/current                 a symlink to one revision
    <runtime-root>/<composition>/previous                a symlink to the last one

A release is a directory that already exists plus one symlink swap. Nothing is
edited in place, so a promotion that fails leaves the previous revision
untouched and running, and a rollback is the same operation in reverse.

Three properties are enforced rather than intended
--------------------------------------------------
1. Atomic promotion. The `current` symlink is replaced with `os.replace` over a
   fully-created temporary link, which is atomic within a filesystem. A reader
   that resolves `current` sees either the whole old revision or the whole new
   one, never a missing link.

2. Verified before activation. A revision is staged, its tree hash is recorded,
   and its entry point is checked to exist and to resolve the runtime it ships
   with — the pairing `bin/` and `runtime/` depend on. A revision that fails is
   left staged and NOT promoted, because the failure mode worth designing
   against is a promotion that half-succeeds.

3. Rollback is retained, not reconstructed. The promoted-away revision stays on
   disk and its identity is written to `previous`. A rollback points at a
   directory that is still there rather than at a hash somebody has to find.

Nothing here deletes. Retiring old revisions is a separate, deliberate act by a
person; a release tool whose failure mode is "the previous version is gone" has
removed the only thing that makes an atomic swap worth having.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# The placeholders the units are written with. They are inert to systemd on
# purpose: systemd expands `%` and `$`, so a `$NAME` left unrendered would
# expand to the empty string and turn an ExecStart into a different, plausible
# path. `@...@` stays literal, so an unrendered unit fails naming the path it
# could not find instead of starting the wrong program.
RUNTIME_PLACEHOLDER = "@SERVEROS_RUNTIME@"
STATE_PLACEHOLDER = "@SERVEROS_STATE_DIR@"
# The alert delivery worker's destination, and the only placeholder whose
# unrendered form is not merely wrong but silently plausible: systemd would
# happily run `alert sync --conversation @SERVEROS_ALERT_CONVERSATION@`, and the
# provider would be asked to send to a chat named after a placeholder. So this
# one is REQUIRED rather than defaulted — `render_unit` raises when the template
# names it and no value is supplied, and `cmd_alert_worker` refuses to install
# without `--conversation`.
CONVERSATION_PLACEHOLDER = "@SERVEROS_ALERT_CONVERSATION@"

UNIT_NAME = "serverosd.service"
# §W's production delivery runtime. A oneshot and its timer, NOT a second
# resident unit — `tests/test_core_freeze.py` holds the distinction by reading
# `Type=` rather than by trusting this comment.
ALERT_SERVICE_NAME = "serveros-alert-sync.service"
ALERT_TIMER_NAME = "serveros-alert-sync.timer"
TREE_MANIFEST = "RELEASE.json"


def default_state_dir() -> Path:
    """The node's state root, read from the project's own path contract.

    This replaced `Path(os.environ.get("DATA_ROOT", home)) / "state/serveros"`,
    which was wrong in a way nothing here could see. `DATA_ROOT` is a documented
    root, not an ambient variable, so on an ordinary shell the lookup misses and
    the expression falls back to `$HOME` — rendering a unit whose
    `SERVEROS_STATE_DIR` is `$HOME/state/serveros`, a path that does not exist.
    The daemon then exits 2 looking for a registry there, and the node is one
    restart away from being down.

    Nothing caught it when it happened. `promote` reloads units but does not
    restart the service, so the node kept running the process it already had;
    `status` reported `agreement AGREE` because it compares the symlink and the
    unit, not whether the state root exists; and `verify_revision` only asked
    that rendering succeed, which a wrong path does. So the defect was invisible
    at promote time and surfaced much later as a service that would not start —
    the worst shape for an installation bug to have.

    `runtime/paths.py` is the qualified resolver for this: the same roots with
    the correct literal fallback, and a refusal for an undeclared name instead
    of an invented one. Asking it means a wrong root cannot be reached by asking
    the environment a question it is permitted not to answer.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
    import paths  # noqa: PLC0415 - resolved from the repo, never installed

    return paths.Paths().root("state")


def fingerprint(root: Path) -> str:
    """Hash a directory's whole file tree.

    This reproduces, byte for byte, the recipe the release records already
    quote:

        find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum

    Reproducing it rather than merely defining something reasonable is the
    point. A release document that quotes a hash, and a tool that computes a
    different one from the same directory, produce two numbers that are both
    correct and cannot be compared — which is worse than having no hash, because
    it looks like verification.

    The three details that make the output match: paths are rendered relative
    with a `./` prefix (that is what `find .` emits), lines are ordered by that
    path in plain byte order (`sort -z` under LC_ALL=C sorts the paths before
    `sha256sum` ever sees them), and each line is `sha256sum`'s own
    `"<hex>  <path>"` — two spaces.

    Ordering by path rather than by line is the part worth stating. Sorting the
    finished lines would order them by their leading hash, which is a different
    permutation of the same bytes and therefore a different digest — a hash that
    looks like verification and is not.

    What the recorded hash covers, and what it deliberately does not. A staged
    revision contains `RELEASE.json`, and that file holds the hash — so it is
    written after the hash is computed and is not itself covered by it. A file
    cannot contain its own digest, so this is forced rather than chosen, but it
    is worth saying out loud because the natural check gets it wrong:
    `fingerprint(revisions/<rev>)` returns a DIFFERENT number from the one
    recorded in that same directory, and a reader who does not know why will
    read a correct revision as tampered with.

    The hash that has to match is the artifact's. Fingerprinting the built
    artifact, or the published asset after extracting it, gives the recorded
    value; fingerprinting the staged directory gives that value plus one file,
    which is a different tree and correctly a different number.
    """
    entries = [p for p in root.rglob("*") if p.is_file()]
    entries.sort(key=lambda p: f"./{p.relative_to(root).as_posix()}")
    stream = "".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(root).as_posix()}\n"
        for p in entries
    )
    return hashlib.sha256(stream.encode()).hexdigest()


def _symlink_swap(link: Path, target: Path) -> None:
    """Atomically repoint `link` at `target`.

    A symlink is created beside the destination and renamed over it, because
    `os.replace` on a symlink replaces the link itself rather than writing
    through it. Removing the old link first would leave a window in which
    `current` does not exist, and a supervisor reading it in that window sees a
    missing installation rather than an old one.
    """
    tmp = link.with_name(link.name + ".swap")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, link)


def render_unit(template: str, runtime_dir: Path, state_dir: Path, *,
                conversation: str | None = None) -> str:
    """Write a unit template out for THIS node.

    `conversation` is keyword-only and has no default because there is no
    honest one. A unit that names the alert destination and is rendered without
    one would either keep the literal placeholder — and be handed to a provider
    as a chat id — or expand to the empty string, which systemd turns into
    `--conversation` with nothing after it. Both produce a worker that runs,
    fails to send, and reports that as nothing to do. Raising here is the only
    outcome that says what is actually wrong.
    """
    rendered = template.replace(RUNTIME_PLACEHOLDER, str(runtime_dir))
    rendered = rendered.replace(STATE_PLACEHOLDER, str(state_dir))
    if CONVERSATION_PLACEHOLDER in template:
        if not conversation:
            raise ValueError(
                f"unit names {CONVERSATION_PLACEHOLDER} and no conversation was "
                f"supplied; alert delivery has no destination and must not be "
                f"installed as if it did"
            )
        rendered = rendered.replace(CONVERSATION_PLACEHOLDER, conversation)
    left = [p for p in (RUNTIME_PLACEHOLDER, STATE_PLACEHOLDER,
                        CONVERSATION_PLACEHOLDER) if p in rendered]
    if left:
        raise ValueError(f"unit still carries an unrendered placeholder: {left}")
    # And any OTHER placeholder, because the list above is a list of the three
    # this file knows how to fill, and a fourth was added to a unit and not
    # here. The symptom is quiet in exactly the way this project keeps paying
    # for: systemd treats `@...@` as text, so a unit carrying a placeholder
    # nobody registered installs cleanly and runs with the literal in it. The
    # check is a shape rather than a name so that the next placeholder is
    # caught by the person who typo'd it rather than by the person reading a
    # journal six weeks later.
    stray = sorted(set(re.findall(r"@[A-Z][A-Z0-9_]*@", rendered)))
    if stray:
        raise ValueError(
            f"unit carries a placeholder no renderer fills: {stray}. Add it to "
            f"`render_unit` and to the caller that supplies its value, or the "
            f"unit installs with the literal in it and systemd runs it as text"
        )
    return rendered


def stage(artifact: Path, revisions_root: Path, revision: str,
          state_dir: Path, replace: bool) -> Path:
    """Copy a built artifact into an immutable revision directory."""
    dest = revisions_root / revision
    if dest.exists():
        if not replace:
            raise SystemExit(
                f"revision already staged: {dest}\n"
                f"Releasing the same revision twice is not a no-op — it is either a "
                f"rebuild that produced different bytes, or the same bytes and a "
                f"wasted copy. Pass --replace only if you have checked which."
            )
        shutil.rmtree(dest)

    if not (artifact / "bin" / "serverosd").is_file():
        raise SystemExit(
            f"artifact has no bin/serverosd: {artifact}\n"
            f"The node's entry point is the one file this whole path exists to place. "
            f"An artifact without it was built before `runtime` and `bin` were members "
            f"of the composition, and promoting it would install a node that cannot start."
        )

    staging = revisions_root / f".staging-{revision}"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(artifact, staging, symlinks=False)
    staging.rename(dest)
    return dest


def verify_revision(rev_dir: Path, state_dir: Path) -> dict:
    """Check a staged revision is runnable before anything points at it."""
    problems: list[str] = []

    # The revision is only half of "runnable". The rendered unit tells the
    # daemon to read its registry from `state_dir`, and the daemon exits 2 when
    # it is not there — so a promote onto a state root that does not exist
    # produces a node that cannot start, and this function used to call that a
    # success. Checking the revision and not the state root is precisely the gap
    # that let a HOME-fallback default install a broken unit and report AGREE.
    if not state_dir.is_dir():
        problems.append(
            f"state root does not exist: {state_dir}\n"
            f"  The rendered unit sets SERVEROS_STATE_DIR to this path, and the "
            f"daemon refuses to start without a registry under it. Seed the state "
            f"root before promoting, or pass --state-dir with this node's real root."
        )

    for rel in ("bin/serverosd", "bin/serverosctl", "runtime/daemon.py",
                "runtime/cli.py", f"runtime/{UNIT_NAME}"):
        if not (rev_dir / rel).is_file():
            problems.append(f"missing {rel}")

    template = rev_dir / "runtime" / UNIT_NAME
    if template.is_file():
        try:
            render_unit(template.read_text(), rev_dir, state_dir)
        except ValueError as exc:
            problems.append(str(exc))

    # The delivery worker, checked WHEN PRESENT and not required to be. The
    # split is §24's: the core must start without it, so an artifact that omits
    # the worker is still a runnable node — but an artifact that ships one
    # carrying a placeholder nobody can render is a worker that cannot send, and
    # that is worth failing over before the revision is promoted rather than
    # when the first real alert fires.
    #
    # The conversation here is a sentinel and never reaches a file: this asks
    # whether the TEMPLATE renders, which is a question about the artifact. The
    # value that becomes a real destination is supplied by a person at install
    # time (`alert-worker --conversation`), because it is a fact about a chat
    # and not a fact about a build.
    alert_template = rev_dir / "runtime" / ALERT_SERVICE_NAME
    if alert_template.is_file():
        try:
            render_unit(alert_template.read_text(), rev_dir, state_dir,
                        conversation="template-check")
        except ValueError as exc:
            problems.append(f"runtime/{ALERT_SERVICE_NAME}: {exc}")

    # The entry point must resolve the runtime it ships with. `bin/` and
    # `runtime/` find each other by relative path, so a revision carrying one
    # without the other is a shim that cannot import what it names — a file that
    # exists and still fails, which an existence check alone never catches.
    entry = rev_dir / "bin" / "serverosctl"
    if entry.is_file():
        # PYTHONDONTWRITEBYTECODE, because this probe imports the revision's own
        # modules and would otherwise leave `__pycache__` inside the artifact it
        # is checking. That is not a tidiness problem: `fingerprint` is computed
        # after this runs, so the revision's recorded tree hash was a hash of
        # files the VERIFIER had just created. The clean artifact hashed to
        # 04e9956b and the promoted one to d6ea08ee, both correct, describing
        # the same code — the two-incomparable-numbers failure the fingerprint
        # docstring exists to warn about, produced by the release tool itself.
        #
        # Verification must not alter what it verifies, and an artifact is
        # supposed to be immutable once staged.
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run(
            [sys.executable, str(entry), "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        if proc.returncode != 0:
            problems.append(
                f"bin/serverosctl --help exited {proc.returncode}: "
                f"{proc.stderr.strip()[:400]}"
            )

    return {"ok": not problems, "problems": problems,
            "tree_hash": fingerprint(rev_dir) if not problems else None}


def current_revision(composition_root: Path) -> str | None:
    link = composition_root / "current"
    if not link.is_symlink():
        return None
    return Path(os.readlink(link)).name


def cmd_status(args) -> int:
    root = args.runtime_root / args.composition
    cur, prev = current_revision(root), None
    prev_link = root / "previous"
    if prev_link.is_symlink():
        prev = Path(os.readlink(prev_link)).name

    unit = args.unit_dir / UNIT_NAME
    shim = args.bin_dir / "serverosctl"

    print(f"runtime root      {root}")
    print(f"current           {cur or '(none)'}")
    print(f"previous          {prev or '(none)'}")
    print(f"unit installed    {unit.is_file()}")
    if unit.is_file():
        first = next((l for l in unit.read_text().splitlines()
                      if l.startswith("ExecStart=")), "")
        print(f"  {first}")
    print(f"cli shim          {shim} -> "
          f"{os.readlink(shim) if shim.is_symlink() else '(not a symlink)'}")

    # What a reader actually wants from a status command: do the two entry
    # points agree? A node whose supervisor and whose CLI resolve different
    # revisions is the split brain this tool exists to remove, and it is
    # reported as the headline rather than left for the reader to diff.
    agree = False
    if cur and unit.is_file() and shim.is_symlink():
        agree = (str(root / "current") in unit.read_text()
                 and str(root / "current") in os.readlink(shim))
    print(f"agreement         {'AGREE' if agree else 'DIVERGENT'}")
    return 0 if agree else 1


def cmd_promote(args) -> int:
    root = args.runtime_root / args.composition
    revisions = root / "revisions"

    # A dry run verifies the artifact where it was built and stops. It must not
    # stage first and check afterwards: staging writes a revision directory, so
    # a dry run that staged would be a real run with the last step removed —
    # and the next real run would then refuse its own leftovers with "already
    # staged", which is how this was found. Nothing below `--dry-run` is
    # reached, so `revisions/` is not even created.
    if args.dry_run:
        report = verify_revision(args.artifact, args.state_dir)
        if not report["ok"]:
            for problem in report["problems"]:
                print(f"REFUSED {problem}", file=sys.stderr)
            return 1
        if not (args.artifact / "runtime" / UNIT_NAME).is_file():
            print(f"REFUSED no runtime/{UNIT_NAME}", file=sys.stderr)
            return 1
        print(f"would promote  {args.revision}  tree={report['tree_hash']}")
        return 0

    revisions.mkdir(parents=True, exist_ok=True)
    rev_dir = stage(args.artifact, revisions, args.revision,
                    args.state_dir, replace=args.replace)
    report = verify_revision(rev_dir, args.state_dir)
    if not report["ok"]:
        for problem in report["problems"]:
            print(f"REFUSED {problem}", file=sys.stderr)
        print(f"staged but NOT promoted: {rev_dir}", file=sys.stderr)
        return 1

    # Render before the swap, so that everything which can fail has failed
    # while `current` still points at the revision that is running.
    unit_text = render_unit((rev_dir / "runtime" / UNIT_NAME).read_text(),
                            root / "current", args.state_dir)

    previous = current_revision(root)
    if previous and previous != args.revision:
        _symlink_swap(root / "previous", revisions / previous)
    _symlink_swap(root / "current", rev_dir)

    args.unit_dir.mkdir(parents=True, exist_ok=True)
    (args.unit_dir / UNIT_NAME).write_text(unit_text)

    args.bin_dir.mkdir(parents=True, exist_ok=True)
    _symlink_swap(args.bin_dir / "serverosctl", root / "current" / "bin" / "serverosctl")

    (rev_dir / TREE_MANIFEST).write_text(json.dumps({
        "composition": args.composition,
        "revision": args.revision,
        "tree_hash": report["tree_hash"],
        "runtime_root": str(root / "current"),
        "previous": previous,
    }, indent=2) + "\n")

    print(f"promoted       {args.revision}  tree={report['tree_hash']}")
    print(f"  previous     {previous or '(none)'}")
    print(f"  unit         {args.unit_dir / UNIT_NAME}")
    print(f"  cli shim     {args.bin_dir / 'serverosctl'}")
    if not args.no_reload:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        print("  daemon-reload  done")
    return 0


def cmd_rollback(args) -> int:
    root = args.runtime_root / args.composition
    revisions = root / "revisions"
    prev_link = root / "previous"
    if not prev_link.is_symlink():
        raise SystemExit("no previous revision recorded; nothing to roll back to")

    target = Path(os.readlink(prev_link))
    rev = revisions / target.name
    if not rev.is_dir():
        raise SystemExit(
            f"previous revision is recorded but absent: {rev}\n"
            f"A rollback that cannot resolve its target must say so rather than "
            f"leave `current` pointing wherever the failure landed."
        )

    report = verify_revision(rev, args.state_dir)
    if not report["ok"]:
        for problem in report["problems"]:
            print(f"REFUSED {problem}", file=sys.stderr)
        return 1

    unit_text = render_unit((rev / "runtime" / UNIT_NAME).read_text(),
                            root / "current", args.state_dir)
    current = current_revision(root)
    if args.dry_run:
        print(f"would roll back to {rev.name} (from {current or '(none)'})")
        return 0

    _symlink_swap(root / "current", rev)
    if current:
        _symlink_swap(prev_link, revisions / current)
    (args.unit_dir / UNIT_NAME).write_text(unit_text)
    _symlink_swap(args.bin_dir / "serverosctl", root / "current" / "bin" / "serverosctl")

    print(f"rolled back    {current or '(none)'} -> {rev.name}")
    if not args.no_reload:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return 0


def cmd_alert_worker(args) -> int:
    """Install §W's delivery runtime: a oneshot and the timer that fires it.

    Deliberately NOT part of `promote`, and the reason is the same one that
    keeps `--enable-reconcile` out of the daemon's ExecStart: promote places a
    revision and points the node at it, which must keep working on a node whose
    operator has not yet decided where alerts should go. Folding this in would
    either make every promotion depend on knowing a chat id — blocking the core
    release on a delivery question, which §AM refuses for repo governance and
    this file refuses for the same reason — or let promote install a worker with
    no destination and call it done.

    So it is one explicit act, and the act is the one that cannot be undone by a
    reboot: `enable --now` on the timer. Before it, nothing is delivered and the
    alerts stay pending, visible in `serverosctl alert list`. After it, a
    condition that opens is said out loud within five minutes.

    The unit is rendered from the revision `current` resolves to, not from
    wherever this script happens to run, so the worker runs the same code the
    daemon does — the split this whole file exists to remove, one hop further
    out.
    """
    root = args.runtime_root / args.composition
    current = current_revision(root)
    if not current:
        print(f"REFUSED nothing is promoted: {root / 'current'} is not a symlink.",
              file=sys.stderr)
        print("        The worker runs the revision's own `bin/serverosctl`; "
              "install one first.", file=sys.stderr)
        return 1
    rev_dir = root / "revisions" / current

    service_template = rev_dir / "runtime" / ALERT_SERVICE_NAME
    timer_template = rev_dir / "runtime" / ALERT_TIMER_NAME
    for path in (service_template, timer_template):
        if not path.is_file():
            print(f"REFUSED {path} is missing from revision {current}.", file=sys.stderr)
            print("        An artifact built before the delivery runtime existed "
                  "cannot install one.", file=sys.stderr)
            return 1

    try:
        service_text = render_unit(service_template.read_text(), root / "current",
                                   args.state_dir, conversation=args.conversation)
    except ValueError as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 1
    # The timer carries no placeholder, and running it through the same
    # renderer anyway is what keeps "which files are rendered" from becoming a
    # second list to maintain.
    timer_text = render_unit(timer_template.read_text(), root / "current", args.state_dir)

    if args.dry_run:
        print(f"would install  {args.unit_dir / ALERT_SERVICE_NAME}")
        print(f"would install  {args.unit_dir / ALERT_TIMER_NAME}")
        print(f"  revision     {current}")
        print(f"  destination  {args.conversation}")
        exec_line = next((l for l in service_text.splitlines()
                          if l.startswith("ExecStart=")), "")
        print(f"  {exec_line}")
        if not args.no_enable:
            print(f"would enable   {ALERT_TIMER_NAME}")
        return 0

    args.unit_dir.mkdir(parents=True, exist_ok=True)
    (args.unit_dir / ALERT_SERVICE_NAME).write_text(service_text)
    (args.unit_dir / ALERT_TIMER_NAME).write_text(timer_text)
    print(f"installed      {args.unit_dir / ALERT_SERVICE_NAME}")
    print(f"installed      {args.unit_dir / ALERT_TIMER_NAME}")
    print(f"  revision     {current}")
    print(f"  destination  {args.conversation}")

    if args.no_reload:
        return 0
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    if args.no_enable:
        print("  not enabled. Nothing is delivered until you enable it:")
        print(f"    systemctl --user enable --now {ALERT_TIMER_NAME}")
        return 0
    # `enable --now` starts the timer AND runs nothing immediately: a timer is
    # armed for its next OnCalendar match. The first delivery therefore happens
    # at the next :04/:09/… and not at install time, which is the right default
    # — installing a delivery path should not send a card nobody asked for.
    done = subprocess.run(["systemctl", "--user", "enable", "--now", ALERT_TIMER_NAME],
                          check=False)
    print(f"  enabled      {ALERT_TIMER_NAME} "
          f"({'ok' if done.returncode == 0 else f'exit {done.returncode}'})")
    if done.returncode != 0:
        print("  The unit and the timer are installed and the timer is NOT "
              "running. Alerts stay pending — `serverosctl alert list` shows "
              "them — and nothing was sent.", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    home = Path.home()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--runtime-root", type=Path, required=True,
                       help="distribution root, e.g. $DATA_ROOT/runtime-assets/hwz-server-os")
        p.add_argument("--composition", required=True,
                       help="composition name, e.g. serveros-linux-work")
        # No environment lookup here, deliberately. The previous default read
        # `SERVEROS_STATE_DIR` then `DATA_ROOT` then `$HOME`, so the value an
        # install produced depended on which of those happened to be exported —
        # and the last of them is silently wrong rather than absent. A node's
        # state root is a property of the node, so it comes from the node's own
        # path contract, and an operator who wants a different one says so.
        p.add_argument("--state-dir", type=Path, default=default_state_dir(),
                       help="rendered into SERVEROS_STATE_DIR in the unit; "
                            "defaults to the path contract's `state` root")
        p.add_argument("--unit-dir", type=Path, default=home / ".config/systemd/user")
        p.add_argument("--bin-dir", type=Path, default=home / ".local/bin")
        p.add_argument("--no-reload", action="store_true",
                       help="skip systemctl --user daemon-reload")
        p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("promote", help="stage a built artifact and point the node at it")
    common(p)
    p.add_argument("--artifact", type=Path, required=True)
    p.add_argument("--revision", required=True,
                   help="serveros revision or release label this artifact was built from")
    p.add_argument("--replace", action="store_true",
                   help="restage over an existing revision of the same name")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("rollback", help="point the node back at the previous revision")
    common(p)
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("status", help="report what the node is actually pointed at")
    common(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "alert-worker",
        help="§W · install the alert delivery worker (a oneshot and its timer). "
             "Separate from promote: it needs a destination a person chooses")
    common(p)
    p.add_argument("--conversation", required=True,
                   help="where the worker sends. Required and not defaulted — "
                        "an install without a destination would produce a "
                        "worker that runs, sends nothing, and reports that as "
                        "nothing to do")
    p.add_argument("--no-enable", action="store_true",
                   help="write the units but do not enable or start the timer")
    p.set_defaults(func=cmd_alert_worker)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
