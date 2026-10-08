#!/usr/bin/env python3
"""What a test read, what it passed against, and whether those bytes still stand.

THE DEFECT THIS ANSWERS
-----------------------
「fixture 变化失效」 — a fixture's content changes and the evidence derived from
it stays green. This tree has three things that are *adjacent* to that and none
that is it: `runtime/daemon.py` tracks a `source`/`source_revision` pair for
observations, `runtime/registration.py` reports a `STALE` reading, and
`runtime/workbench_sources.py` renders a claim beside an exit code it just
observed so that "a stale claim is visible as a disagreement". All three are
about a claim ageing. None of them connects a recorded test verdict to the
bytes the verdict was a statement about.

THE TWO QUESTIONS, WHICH TURN OUT TO BE ONE MECHANISM
-----------------------------------------------------
    plan   --changed X        which tests a change to X can affect
    run    --record R         verify a selection and record what each test read
    status --record R         which of R's verdicts its inputs still support

`plan` is 「定向验证优先」: running 101 files because one module changed is not
caution, it is the reason a full suite is deferred to a release boundary.
`status` is the criterion itself: the moment an input moves, the verdict it
produced is INVALIDATED rather than green.

Both read the same thing — the set of files a test file actually read.

WHY THE INPUT SET IS MEASURED AND NOT INFERRED
---------------------------------------------
`tests/loader_graph.py` computes a module closure from the source: loader calls,
division chains, imports. It is the right tool for "what does the shipped
surface need", and it is the wrong tool here, in a way that is easy to state and
was measured before this file was written:

    closure(Path("tests/test_coordination.py")) == {tests/test_coordination.py}

One module. The test loads `runtime/coordination.py` through this tree's
standard test helper — `_load(name, filename)`, which joins the filename with a
module-level directory constant — and the scanner resolves a bare filename only
beside the calling module and at the repository root. 87 of the 112 files named
`test_*.py` in `tests/` define that helper, and for 55 of the 112 the static
closure is the test file alone: the input set a plan would have been built on is
empty, and nothing in its output would have said so. Both numbers re-measured
2026-09-22; they were 91 of the 96 and 52 of the 96. That is the failure
`tests/loader_graph.py`'s own docstring records three times over on the
shipped-surface side, found a fourth time on this side by measuring it before
building on it.

So the input set here is what the test process ACTUALLY OPENED. A census child
installs a `sys.addaudithook` hook, runs one test file under it, and reports
every `open`, `os.listdir` and `os.scandir` under the repository root. A file
reached through a variable, a f-string, a helper or a fixture factory is
captured the same as a literal, because the process is what knows. The static
closure remains available as an explicit LOWER BOUND (`plan` without a record),
and says so in its own output rather than passing as the answer.

THE ONE THING THE CENSUS CANNOT SEE, AND WHY IT DOES NOT MATTER HERE
-------------------------------------------------------------------
A file a test reads in a SUBPROCESS is invisible to the parent's audit hook. In
this tree that case is `scripts/`-launched children and the `test-envs` compose
layer, and both are outside what `tests/` reads today. It is stated rather than
handled: the honest form of an incomplete measurement is a named limit, not a
heuristic that guesses at the remainder.

The other gap closes itself. A test that starts reading a fixture it did not
read before must have been edited to do so — and the test file is in its own
input set, because pytest reads it to collect it. So the edit that adds an
input also invalidates the record that predates it. That is why
`status` reports `completeness: MEASURED` only when the test file is in its own
recorded inputs, and refuses to answer at all when it is not: a record that
cannot make that claim is a lower bound wearing the word "verified".

WHAT A RECORD DELIBERATELY DOES NOT DO
--------------------------------------
It does not invalidate on a revision change. A revision names a whole tree; the
fingerprint names the bytes. Invalidating every record on every commit would
make `status` a second, worse `git status`, and `status` that is red for a
reason unrelated to the question is `status` that gets ignored.

It does not re-run anything. `status` answers a question about a record, and a
tool that quietly ran the tests to answer it would be an actuator in a verb
whose name promises a report.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parent

SCHEMA_RECORD = "serveros/verification-record.v1"
SCHEMA_STATUS = "serveros/verification-status.v1"
SCHEMA_PLAN = "serveros/verification-plan.v1"

EXIT_OK = 0
EXIT_FAULT = 1
EXIT_COULD_NOT_ANSWER = 2

# The namespace this record lives in, and the name the root contract must
# declare for it. Named here rather than composed at each call site: the
# derivation is `runtime/paths.py`'s, and the declaration is the contract's.
NAMESPACE = "verification"
NAMESPACE_REF = f"state/{NAMESPACE}"

# The census child's verb. It is a re-invocation of this same file rather than a
# second program, because a second program is a second place the hook and the
# pytest arguments can drift apart.
CENSUS_VERB = "__census__"

MEASURED = "MEASURED"

# pytest's own exit code for "collected nothing". It is not a failure and it is
# not a pass, and a record that called it either one would be a record a reader
# acts on wrongly: a test file whose collection was broken by an import error in
# a sibling, or one that was renamed out of the pattern, verifies nothing.
NO_TESTS_EXIT_CODE = 5
NO_TESTS = "NO_TESTS"
STATIC_LOWER_BOUND = "STATIC_LOWER_BOUND"

CURRENT = "CURRENT"
INVALIDATED = "INVALIDATED"
NOT_RECORDED = "NOT_RECORDED"

FINGERPRINT_MISSING = "MISSING"


class VerifyError(Exception):
    """The tool cannot answer the question it was asked."""


def _load(name: str, path: Path):
    """Load a module under its canonical name, or return what is loaded.

    Two names for one file is two module objects, and two module objects is an
    identity that silently stops being one. `_HERE` is on the path so that the
    canonical name is the one the module's own directory already gave it.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise VerifyError(f"{path} is not importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _paths_module():
    return _load("serveros_runtime_paths", REPO / "runtime" / "paths.py")


def _state_root_module():
    return _load("serveros_runtime_state_root", REPO / "runtime" / "state_root.py")


# ---------------------------------------------------------------------------
# Fingerprints. One function per kind of input, because a directory is not a
# file and pretending it is would be the same defect one layer down.
# ---------------------------------------------------------------------------

def fingerprint(root: Path, rel: str, kind: str) -> str:
    """The current identity of one input, or `MISSING`.

    `MISSING` is a value and not an exception: an input that has been deleted
    is the strongest reason to distrust the verdict it produced, and a reader
    that can only compare hashes would report the deletion as "no change".
    """
    path = root / rel
    if kind == "listing":
        if not path.is_dir():
            return FINGERPRINT_MISSING
        try:
            names = sorted(entry.name for entry in path.iterdir())
        except OSError:
            return FINGERPRINT_MISSING
        digest = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()
        return f"listing:{digest[:16]}"
    if not path.is_file():
        return FINGERPRINT_MISSING
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return FINGERPRINT_MISSING
    return f"sha256:{digest.hexdigest()[:16]}"


def fingerprint_all(root: Path, inputs: dict) -> dict:
    """`{rel: {"kind":..., "sha256":...}}` for a recorded input set."""
    return {rel: {"kind": spec.get("kind", "read"),
                  "sha256": fingerprint(root, rel, spec.get("kind", "read"))}
            for rel, spec in sorted(inputs.items())}


# ---------------------------------------------------------------------------
# The census. What actually happened, from the process it happened in.
# ---------------------------------------------------------------------------

def _classify_opened(repo: Path, observed: list, test: str) -> dict:
    """Audit records → `{repo-relative path: kind}`, or nothing.

    Reads only. A file the test WROTE is not an input to it, and a temporary
    file is not an input to anything. `__pycache__` is mapped back to the
    source it was compiled from: a process that read the compiled artifact
    depends on the source, and the source is the thing a change moves.

    The mode arrives in two shapes, because there are two ways to open a file.
    `open()` reports a mode string; `os.open` reports the integer flags, and
    an integer rendered as a string contains none of the letters below. The
    two are read apart rather than guessed at: a sidecar opened
    `O_CREAT|O_RDWR` by a store is a write target, and recording it as an
    input to the test would make every store test invalidate on its own lock.

    Directory LISTINGS are kept, because "the fixture directory gained a file"
    is a change to what a test would read next, and no hash of what it read
    before can see it. Two of them are dropped, and both were MEASURED rather
    than guessed: running pytest on an empty test file in an empty checkout
    produces listings of the checkout root and of the directory the test file
    is in, and of nothing else. Those two are the collector walking its own
    roots — `tests/` is scanned whether or not the test cares what is in it —
    and keying a verdict to them would invalidate every record whenever a test
    file was added anywhere. Every other directory is the test's own.
    """
    collection_roots = {Path("."), Path(test).parent}
    found: dict[str, str] = {}
    for event, raw, mode in observed:
        if event == "open":
            if mode.isdigit():
                # `os.open`: keep only the pure reads. O_RDONLY is 0 and the
                # access mode is the low two bits.
                if int(mode) & 0o3:
                    continue
            elif any(flag in mode for flag in "wax") and "+" not in mode:
                continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            continue
        if candidate.suffix == ".pyc" or "__pycache__" in candidate.parts:
            if "__pycache__" not in candidate.parts:
                continue
            candidate = candidate.parent.parent / (candidate.name.split(".")[0] + ".py")
            if not candidate.is_file():
                continue
        try:
            rel = candidate.resolve().relative_to(repo)
        except (ValueError, OSError):
            continue
        if ".git" in rel.parts:
            continue
        listing = event in ("os.listdir", "os.scandir")
        if listing and rel in collection_roots:
            continue
        kind = "listing" if listing else "read"
        previous = found.get(str(rel))
        if previous is None or (previous == "listing" and kind == "read"):
            found[str(rel)] = kind
    return found


def census_child(argv: list) -> int:
    """Run ONE test file under an audit hook and report what it opened."""
    parser = argparse.ArgumentParser(prog=f"verify.py {CENSUS_VERB}")
    parser.add_argument("--test", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--junit", required=True)
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    observed: list[tuple[str, str, str]] = []

    def hook(event: str, payload: tuple) -> None:
        try:
            if event == "open":
                observed.append(("open", str(payload[0]), str(payload[1] or "")))
            elif event in ("os.listdir", "os.scandir"):
                observed.append((event, str(payload[0]), ""))
        except Exception:  # noqa: BLE001 - a hook that raises cancels the event
            return

    sys.addaudithook(hook)
    # The census is about sources, so it refuses to let a compiled artifact
    # stand in for one.
    sys.dont_write_bytecode = True

    # `pytest.main()` runs in THIS process, so `sys.path[0]` is this script's own
    # directory and NOT the repository root. A developer runs `python -m pytest`
    # from the repository root, and `-m` is what puts that root on the path — so a
    # test module that imports a top-level package (`from intake.daily_cycle
    # import ...`) collects there and does not collect here. The difference is
    # invisible in the record, which is what made it worth pinning down: a
    # collection error arrives as ONE failing case named after the module, which
    # reads exactly like a test that ran and failed. Measured 2026-09-26: three of
    # 136 files (`test_bounded_invocation_paths`, `test_daily_cycle`,
    # `test_focus_source`) failed this way and all three pass under `python -m
    # pytest`. The path the child runs under is the path the suite is written for.
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    try:
        import pytest
    except ImportError as exc:  # pragma: no cover - environment guarantee
        raise SystemExit(f"the census child needs pytest: {exc}")

    code = pytest.main(["-q", "--no-header", "-p", "no:cacheprovider",
                        "--junitxml", args.junit, args.test])

    # Classified before anything else this function opens, so that the census
    # is about the test and not about the reporting of it.
    inputs = _classify_opened(repo, observed, args.test)

    cases: list[dict] = []
    xml_path = Path(args.junit)
    if xml_path.is_file():
        for case in ET.parse(xml_path).getroot().iter("testcase"):
            outcome = "PASS"
            for child in case:
                if child.tag in ("failure", "error"):
                    outcome = "FAIL"
                elif child.tag == "skipped":
                    outcome = "SKIP"
            cases.append({"name": case.get("name"), "classname": case.get("classname"),
                          "outcome": outcome})

    report = {
        "test": args.test,
        "exit_code": code,
        "cases": cases,
        "inputs": inputs,
    }
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    return 0


def measure(repo: Path, test: str, *, python: str) -> dict:
    """One test file, measured in its own process.

    A fresh process per test file, deliberately. `pytest.main` may be called
    more than once in one interpreter, and the second call is served partly
    from modules the first already imported — so the second file's census would
    be missing every module the first had touched, silently. One process per
    file is the cheap way to make each census about its own file.
    """
    with tempfile.TemporaryDirectory(prefix="serveros-census-") as scratch:
        out = Path(scratch) / "census.json"
        junit = Path(scratch) / "junit.xml"
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Not `python script.py`'s habit of putting the script's own directory
        # on `sys.path[0]`. The child would then have `scripts/` on the import
        # path, and `importlib.metadata` walks every path entry looking for
        # distributions — which arrived in the census as a listing of
        # `scripts/`, measured, in every record. The tool's own footprint is not
        # the test's input.
        env["PYTHONSAFEPATH"] = "1"
        result = subprocess.run(
            [python, str(Path(__file__).resolve()), CENSUS_VERB,
             "--test", test, "--repo", str(repo),
             "--out", str(out), "--junit", str(junit)],
            cwd=str(repo), env=env, capture_output=True, text=True,
        )
        if not out.is_file():
            raise VerifyError(
                f"the census child for {test} wrote no report "
                f"(exit {result.returncode}): {result.stderr.strip()[-400:]}")
        return json.loads(out.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The static lower bound. Reused, not reimplemented: `tests/loader_graph.py` is
# this tree's one scanner of loader forms, and a second one that claimed to scan
# the same forms is the defect that file's docstring records three times.
# ---------------------------------------------------------------------------

def static_inputs(test: str) -> set[str] | None:
    """The loader closure of one test file, or None if it cannot be computed.

    `None` and an empty set are different answers: "nobody looked" and "this
    test loads nothing" must not render the same. The set being the test file
    alone is the common real answer here — 55 of the 112, re-measured
    2026-09-22, up from 52 of the 96 — and it is exactly why this is a fallback
    and never the record: a static answer of "this test depends on itself" is
    not a small answer, it is a blind one.
    """
    try:
        loader_graph = _load("loader_graph", REPO / "tests" / "loader_graph.py")
    except (VerifyError, OSError, SyntaxError):
        return None
    try:
        return {str(path) for path in loader_graph.closure(Path(test))}
    except (OSError, SyntaxError, ValueError):
        return None


def discovered_tests(repo: Path) -> list[str]:
    return sorted(str(path.relative_to(repo))
                  for path in (repo / "tests").glob("test_*.py"))


# ---------------------------------------------------------------------------
# The record and where it lives.
# ---------------------------------------------------------------------------

def verify_root(*, repo: Path, state_root: str | None = None) -> Path:
    """Where a record goes, from the resolver that owns the answer.

    With `state_root` given, that is the answer — an isolated run, a test, a
    verification of a checkout other than this one. Without it, the state root
    is `runtime/paths.py`'s and the namespace must be one the root contract
    DECLARES. A producer that writes a namespace the contract does not know
    about has created a directory that reads as state and is not: the same
    shape as `~/files`, one root over.

    The declaration is checked BEFORE the node's binding, and the order is
    deliberate: the contract is a fact about the checkout being verified and is
    always readable, while the binding is a fact about the machine. A tool asked
    about a checkout should name the checkout's own defect first, and the
    refusal it prints should not depend on which node ran it.
    """
    if state_root is not None:
        return Path(state_root)

    declared = declared_namespaces(repo)
    if NAMESPACE not in declared:
        raise VerifyError(
            f"{(repo / 'fleet' / 'state-root-contract.yaml')} declares no "
            f"{NAMESPACE!r} namespace, so a record written there would be "
            f"undeclared state. Declared: {sorted(declared)}")

    paths = _paths_module().Paths()
    if not paths.bound:
        raise VerifyError(
            "this node's roots are UNBOUND, so there is no state root to write "
            f"a record into: {list(paths.unconfigured)}. An unbound root is a "
            "template string, and writing into one creates a directory that "
            "looks like a name.")
    return Path(paths.resolve(NAMESPACE_REF)["path"])


def declared_namespaces(repo: Path) -> set[str]:
    """Every namespace this repository's root contract declares.

    Read through `runtime/state_root.py` rather than by parsing the YAML here:
    that module owns the flattening of nested declarations, and a second
    reading of one contract is a second answer to what it says.
    """
    state_root = _state_root_module()
    contract = state_root.load_contract(repo_root=repo)
    return set(state_root.namespace_classes(contract))


def record_path(record: str, *, repo: Path, state_root: str | None = None) -> Path:
    _safe_name(record)
    return verify_root(repo=repo, state_root=state_root) / f"{record}.json"


def _safe_name(name: str) -> str:
    if not name or "/" in name or name in (".", "..") or name.startswith("."):
        raise VerifyError(
            f"{name!r} is not a record name. A record is a file in one "
            "namespace, and a name that needs sanitising is a request to write "
            "somewhere it did not name.")
    return name


def load_record(record: str, *, repo: Path, state_root: str | None = None) -> dict:
    path = record_path(record, repo=repo, state_root=state_root)
    if not path.is_file():
        raise VerifyError(f"no verification record named {record!r} at {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifyError(f"{path} could not be read as a record: {exc}") from exc
    if document.get("schema") != SCHEMA_RECORD:
        raise VerifyError(f"{path} is not a {SCHEMA_RECORD}: {document.get('schema')!r}")
    return document


def write_record(document: dict, *, repo: Path, state_root: str | None = None) -> Path:
    """Write a record atomically, under the namespace the contract names.

    The temp-file-then-`os.replace` idiom is the one the four state stores in
    `runtime/` use. It is NOT imported from them: `_atomic_write` is private in
    each, and one public shared writer would be a change to four stores and
    their tests. Recorded as a gap rather than papered over with a fifth
    spelling of the same six lines.
    """
    path = record_path(document["record"], repo=repo, state_root=state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False)
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)
    return path


def revision_of(repo: Path) -> str:
    """The revision the run happened at, for a reader. Not for invalidation."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True)
    except OSError:
        return "UNKNOWN"
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else "UNKNOWN"


# ---------------------------------------------------------------------------
# The verbs.
# ---------------------------------------------------------------------------

def cmd_run(args) -> int:
    repo = Path(args.repo).resolve()
    tests = args.test or (discovered_tests(repo) if args.all else [])
    if not tests:
        raise VerifyError(
            "no tests named. `run` verifies a selection: pass --test PATH, or "
            "--all for the whole suite, which belongs at an integration or "
            "release boundary rather than in a change loop.")

    known = set(discovered_tests(repo))
    unknown = [test for test in tests if test not in known]
    if unknown:
        raise VerifyError(f"not test files in {repo}/tests: {unknown}")

    entries: dict[str, dict] = {}
    failures: list[str] = []
    for test in tests:
        measured = measure(repo, test, python=args.python)
        cases = measured.get("cases", [])
        failed = [case["name"] for case in cases if case["outcome"] == "FAIL"]
        if measured["exit_code"] == NO_TESTS_EXIT_CODE:
            outcome = NO_TESTS
        elif measured["exit_code"] == 0 and not failed:
            outcome = "PASS"
        else:
            outcome = "FAIL"
        if outcome != "PASS":
            failures.append(test)
        inputs = measured.get("inputs", {})
        if test not in inputs:
            # The test file is read by the process that collects it. If the
            # census did not see that, the census is not about this run, and
            # saying MEASURED would be the false completeness this file exists
            # to prevent.
            entries[test] = {
                "outcome": outcome,
                "cases": len(cases),
                "failed_cases": failed,
                "inputs_from": STATIC_LOWER_BOUND,
                "inputs": {rel: {"kind": kind, "sha256": fingerprint(repo, rel, kind)}
                           for rel, kind in sorted(inputs.items())},
                "why_lower_bound": "the census did not observe this test file being read",
            }
            continue
        entries[test] = {
            "outcome": outcome,
            "cases": len(cases),
            "failed_cases": failed,
            "inputs_from": MEASURED,
            "inputs": {rel: {"kind": kind, "sha256": fingerprint(repo, rel, kind)}
                       for rel, kind in sorted(inputs.items())},
        }

    document = {
        "schema": SCHEMA_RECORD,
        "record": args.record,
        "repo": str(repo),
        "revision": args.revision if args.revision is not None else revision_of(repo),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "interpreter": args.python,
        "tests": entries,
    }
    path = write_record(document, repo=repo, state_root=args.state_root)

    lower = sorted(test for test, entry in entries.items()
                   if entry["inputs_from"] != MEASURED)
    print(json.dumps({
        "schema": SCHEMA_RECORD,
        "record": args.record,
        "written_to": str(path),
        "tests": len(entries),
        "passed": len(entries) - len(failures),
        # Not "failed": a file that collected nothing did not fail, and the two
        # call for different repairs.
        "not_passed": sorted(failures),
        "outcomes": {test: entry["outcome"] for test, entry in sorted(entries.items())
                     if entry["outcome"] != "PASS"},
        "inputs_unmeasured": lower,
    }, indent=2, ensure_ascii=False))
    return EXIT_FAULT if failures else EXIT_OK


def cmd_status(args) -> int:
    repo = Path(args.repo).resolve()
    try:
        document = load_record(args.record, repo=repo, state_root=args.state_root)
    except VerifyError as exc:
        print(json.dumps({"schema": SCHEMA_STATUS, "record": args.record,
                          "error": str(exc)}, indent=2, ensure_ascii=False))
        return EXIT_COULD_NOT_ANSWER

    reported: dict[str, dict] = {}
    for test, entry in sorted(document.get("tests", {}).items()):
        changed: list[dict] = []
        for rel, spec in sorted(entry.get("inputs", {}).items()):
            kind = spec.get("kind", "read")
            now = fingerprint(repo, rel, kind)
            if now != spec.get("sha256"):
                changed.append({
                    "path": rel,
                    "kind": kind,
                    "recorded": spec.get("sha256"),
                    "now": now,
                    "because": "removed" if now == FINGERPRINT_MISSING else "content moved",
                })
        state = INVALIDATED if changed else CURRENT
        reported[test] = {
            "state": state,
            "outcome_recorded": entry.get("outcome"),
            "completeness": entry.get("inputs_from", STATIC_LOWER_BOUND),
            "inputs": len(entry.get("inputs", {})),
            "changed": changed,
        }

    incomplete = sorted(test for test, row in reported.items()
                        if row["completeness"] != MEASURED)
    invalidated = sorted(test for test, row in reported.items()
                         if row["state"] == INVALIDATED)
    document_out = {
        "schema": SCHEMA_STATUS,
        "record": args.record,
        "recorded_at": document.get("recorded_at"),
        "revision": document.get("revision"),
        "tests": reported,
        "summary": {
            CURRENT: len(reported) - len(invalidated),
            INVALIDATED: len(invalidated),
            "cannot_answer_for": incomplete,
            "note": (
                "a verdict is CURRENT when nothing it was a statement about has "
                "moved. It is not a claim that the code is correct, and it is "
                "not a claim that the test still covers what changed — a new "
                "input arrives with an edit to the test file, and that edit "
                "invalidates this record."
            ),
        },
    }
    print(json.dumps(document_out, indent=2, ensure_ascii=False))

    if not reported:
        return EXIT_COULD_NOT_ANSWER
    if invalidated:
        return EXIT_FAULT
    if incomplete:
        # A record that cannot say what a test read cannot support the verdict
        # it holds. Answering "CURRENT" here would be the green light this
        # whole mechanism exists to make impossible.
        return EXIT_COULD_NOT_ANSWER
    return EXIT_OK


def cmd_plan(args) -> int:
    repo = Path(args.repo).resolve()
    changed: list[str] = []
    for raw in args.changed:
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                changed.append(str(candidate.resolve().relative_to(repo)))
                continue
            except ValueError:
                changed.append(str(candidate))
                continue
        changed.append(str(candidate))

    source = STATIC_LOWER_BOUND
    inputs_by_test: dict[str, set[str]] = {}
    unknown: list[str] = []
    coverage: dict | None = None

    if args.record:
        document = load_record(args.record, repo=repo, state_root=args.state_root)
        for test, entry in document.get("tests", {}).items():
            if entry.get("inputs_from") != MEASURED:
                unknown.append(test)
                continue
            inputs_by_test[test] = set(entry.get("inputs", {}))
        source = MEASURED
        # A record covering three tests answers a question about three tests,
        # and `tests_to_run` must not be read as "only these can be affected".
        # The suite's size is here rather than in the docstring for the reason
        # the whole mechanism exists: a reader acts on the answer, not on the
        # paragraph behind it.
        suite = discovered_tests(repo)
        coverage = {
            "tests_in_record": len(document.get("tests", {})),
            "tests_in_record_with_measured_inputs": len(inputs_by_test),
            "tests_in_suite": len(suite),
            "not_in_record": sorted(set(suite) - set(document.get("tests", {}))),
        }
    else:
        for test in discovered_tests(repo):
            found = static_inputs(test)
            if found is None:
                unknown.append(test)
                continue
            inputs_by_test[test] = found

    def affected(rel_input: str) -> bool:
        for path in changed:
            if rel_input == path:
                return True
            if rel_input.startswith(path.rstrip("/") + "/"):
                return True
        return False

    to_run = sorted(test for test, inputs in inputs_by_test.items()
                    if any(affected(rel) for rel in inputs))
    covered = {rel for inputs in inputs_by_test.values() for rel in inputs}
    not_read = sorted(path for path in changed
                      if not any(affected(rel) for rel in covered))

    if source == MEASURED:
        limits = (
            "measured inputs, for the tests this record covers and no others — "
            "see `coverage`. A file a test reads in a subprocess is not in this "
            "set; no test in this tree does that today.")
    else:
        limits = (
            "STATIC LOWER BOUND from tests/loader_graph.py, and a low one: the "
            "scanner resolves a loader argument beside the calling module or at "
            "the repository root, so this tree's `_load(name, filename)` helper "
            "— defined in 87 of the 112 test files — is not an edge, and for 55 "
            "of them the static input set is the test file alone. An empty "
            "`tests_to_run` here means 'this scan found nothing', never 'nothing "
            "is affected'. Run `verify.py run --record <name>` to measure.")

    print(json.dumps({
        "schema": SCHEMA_PLAN,
        "inputs_from": source,
        "changed": sorted(changed),
        "tests_to_run": to_run,
        "not_read_by_any_test": not_read,
        "tests_with_no_known_inputs": sorted(unknown),
        "coverage": coverage,
        "limits": limits,
    }, indent=2, ensure_ascii=False))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify.py", description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(REPO),
                        help="the checkout to answer about (default: this one)")
    parser.add_argument("--state-root", default=None,
                        help=f"the directory holding records (default: the "
                             f"{NAMESPACE} namespace of this node's state root)")
    subparsers = parser.add_subparsers(dest="verb", required=True)

    plan = subparsers.add_parser(
        "plan", help="which tests a change can affect")
    plan.add_argument("--changed", nargs="+", required=True, metavar="PATH")
    plan.add_argument("--record", default=None,
                      help="answer from a measured record instead of the "
                           "static lower bound")
    plan.set_defaults(handler=cmd_plan)

    run = subparsers.add_parser(
        "run", help="verify a selection and record what each test read")
    run.add_argument("--record", required=True)
    run.add_argument("--test", nargs="+", metavar="PATH", default=None)
    run.add_argument("--all", action="store_true",
                     help="the whole suite — an integration or release "
                          "boundary activity, not a change loop")
    run.add_argument("--python", default=sys.executable,
                     help="the interpreter that runs the tests (and therefore "
                          "the one that has pytest)")
    run.add_argument("--revision", default=None,
                     help="the revision to record against (default: HEAD)")
    run.set_defaults(handler=cmd_run)

    status = subparsers.add_parser(
        "status", help="which recorded verdicts their inputs still support")
    status.add_argument("--record", required=True)
    status.set_defaults(handler=cmd_status)

    census = subparsers.add_parser(CENSUS_VERB, help=argparse.SUPPRESS)
    census.set_defaults(handler=lambda _args: census_child(sys.argv[2:]))
    return parser


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == CENSUS_VERB:
        return census_child(argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except VerifyError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return EXIT_COULD_NOT_ANSWER


if __name__ == "__main__":
    sys.exit(main())
