#!/usr/bin/env python3
"""§18/§26 — the scanner, the contract, and the two directions between them.

WHY THIS FILE EXISTS
--------------------
The gate in `architecture_check.py` says "the product contains no reachable
destroyer". That sentence is only worth reading if something can contradict it,
and the two ways it stays green while being wrong are both boring:

  * the scanner only understands literals, so a command assembled in a variable
    is invisible — `runtime/daemon.py`'s actuator, the single most consequential
    command in the product, is exactly that shape;
  * the contract only gets checked one way, so an exemption written once keeps
    covering a file that has since grown a second call site.

So the tests below are mostly about the scanner being able to FAIL. A scanner
that reports a clean tree is indistinguishable from one that is unplugged, and
the difference is only visible from the cases where it is supposed to fire.

The last section runs the real contract against the real tree. That is the
assertion the whole arrangement is for: not "the scanner works on examples" but
"this tree is covered, in both directions, right now".
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = REPO / "skills" / "serveros" / "scripts"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = _load("serveros_mutation_guard", SKILL_SCRIPTS / "mutation_guard.py")


def hits_of(source: str, relpath="product/example.py") -> list[dict]:
    return guard.scan_file(relpath, source)["hits"]


def kinds(source: str) -> list[tuple]:
    return sorted((h["kind"], h["verb"]) for h in hits_of(source))


def contract_at(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


# The state-root namespaces whose declared producer nothing in this repository
# can resolve — not a path, not an external domain this contract names, not a
# section of the contract itself. Asserted to be exactly this set, so the list
# cannot drift and neither can its absence: closing one of these means editing
# this constant, on purpose, in the same commit.
#
# IT IS EMPTY, AND THAT IS A FINDING RATHER THAN A TIDY STATE.
#
# The audit reported three unresolvable producers before this round. All three
# were the same defect wearing three different sentences, and none of them was
# a missing producer — every one named something that exists:
#
#   abilities    `serverosctl ability refresh` — this repository's own command,
#                added in §2, with a real writer behind it. Unresolvable
#                because a command is a sentence and the resolver reads paths.
#   boards       `serverosctl board render` — the control plane's command, of a
#                distribution that ships a second program under this name.
#   federation   `the federation collectors` — `workengine/federation.py`'s
#                publish paths, named as a group instead of as a thing.
#
# A reader was told, for all three, that the producer was unresolvable, and an
# unresolvable producer reads as an unwritten one. The repair was in the
# contract, not here: each field now names something the three readings can
# answer. This constant stays because the next prose producer must be a
# deliberate entry rather than a new silent UNRESOLVED.
KNOWN_UNRESOLVED: dict = {}


# ---------------------------------------------------------------------------
# It reads code, not text
# ---------------------------------------------------------------------------


class ReadsTheSourceNotTheProse(unittest.TestCase):
    """The false positive that trains people to ignore the gate."""

    def test_a_docstring_naming_a_destroyer_is_not_a_destroyer(self):
        """`mcp/agent_mcp.py` really does have a docstring saying there is no
        `docker rm` in this product, and `grep -rn "docker rm" .` finds it."""
        source = '''
"""This module runs no `docker rm` and no `systemctl stop` anywhere."""
def f():
    """Nor here: `docker prune` is not called."""
'''
        self.assertEqual(hits_of(source), [])

    def test_a_comment_naming_a_destroyer_is_not_a_destroyer(self):
        self.assertEqual(hits_of("# subprocess.run([\"docker\", \"rm\", name])\n"), [])

    def test_a_real_call_is_a_hit(self):
        found = hits_of('import subprocess\nsubprocess.run(["docker", "rm", name])\n')
        self.assertEqual([(h["kind"], h["verb"]) for h in found],
                         [("mutation", "docker rm")])


# ---------------------------------------------------------------------------
# The §26 vocabulary, verb by verb
# ---------------------------------------------------------------------------


class TheDestroyingVerbs(unittest.TestCase):
    def test_every_verb_section_26_names_is_a_mutation(self):
        for verb in ("stop", "kill", "rm", "rmi", "prune", "down"):
            with self.subTest(verb=verb):
                found = hits_of(
                    f'import subprocess\nsubprocess.run(["docker", "{verb}", "x"])\n')
                self.assertEqual([h["kind"] for h in found], ["mutation"])

    def test_volume_and_network_and_system_and_builder_prune(self):
        for argv in ('["docker","volume","rm","v"]', '["docker","network","rm","n"]',
                     '["docker","system","prune","-a"]',
                     '["docker","builder","prune","-f"]',
                     '["docker","image","prune","-a"]',
                     '["docker","container","prune"]'):
            with self.subTest(argv=argv):
                found = hits_of(f"import subprocess\nsubprocess.run({argv})\n")
                self.assertEqual([h["kind"] for h in found], ["mutation"], argv)

    def test_the_other_systemctl_verbs_that_stop_things(self):
        for verb in ("stop", "kill", "restart", "disable", "mask"):
            with self.subTest(verb=verb):
                found = hits_of(
                    f'import subprocess\nsubprocess.run(["systemctl","--user","{verb}","x"])\n')
                self.assertEqual([h["kind"] for h in found], ["mutation"])

    def test_the_process_killers(self):
        for binary in ("pkill", "killall", "shutdown", "reboot", "poweroff"):
            with self.subTest(binary=binary):
                found = hits_of(f'import subprocess\nsubprocess.run(["{binary}","x"])\n')
                self.assertEqual([h["kind"] for h in found], ["mutation"], binary)

    def test_os_kill_and_friends(self):
        for call in ("os.kill(p, 9)", "os.killpg(pg, 15)", "os.remove(p)",
                     "shutil.rmtree(d)", "os.chmod(p, 0o600)",
                     "Path(p).unlink()", "os.replace(a, b)"):
            with self.subTest(call=call):
                found = hits_of(f"import os, shutil\nfrom pathlib import Path\n{call}\n")
                self.assertEqual(len(found), 1, (call, found))

    def test_docker_restart_is_deliberately_not_a_mutation(self):
        """It destroys nothing: the container is the same container and keeps
        its writable layer. Collapsing it into the §26 list is how the list
        stops being read. It still carries a label, because a `docker` in the
        product is a thing §18 requires someone to declare — but the label is
        the reader's, not the destroyer's."""
        found = hits_of('import subprocess\nsubprocess.run(["docker","restart","c"])\n')
        self.assertEqual([(h["kind"], h["verb"]) for h in found],
                         [("host_reader", "docker")])

    def test_a_docker_read_is_a_host_reader_and_not_a_mutation(self):
        """The regression that disarmed §26: with `docker` in HOST_READERS and
        the reader branch tested first, every docker command — `rm`, `prune`,
        all of them — came back as `host_reader`, and the mutating branch below
        it could never run. Both labels have to be reachable, and which one
        applies is decided by the verb."""
        for argv, expected in (('["docker","ps"]', "host_reader"),
                               ('["docker","system","df"]', "host_reader"),
                               ('["docker","rm","c"]', "mutation"),
                               ('["docker","system","prune"]', "mutation"),
                               ('["docker","builder","prune","-f"]', "mutation")):
            with self.subTest(argv=argv):
                found = hits_of(f"import subprocess\nsubprocess.run({argv})\n")
                self.assertEqual(found[0]["kind"], expected, argv)


# ---------------------------------------------------------------------------
# Unreadable is reported, never passed
# ---------------------------------------------------------------------------


class UnresolvedIsAGapNotAPass(unittest.TestCase):
    def test_a_command_from_a_variable_is_unresolved(self):
        source = 'import subprocess\ndef f(command):\n    subprocess.run(command)\n'
        found = hits_of(source)
        self.assertEqual([h["kind"] for h in found], ["unresolved_argv"])

    def test_a_partially_read_argv_keeps_its_positions(self):
        """`["docker", <unknown>, name]` must not collapse to `docker name` —
        a command that does not exist, reported with confidence."""
        source = ('import subprocess\ndef f(verb):\n'
                  '    subprocess.run(["docker", verb, "x"])\n')
        found = [h for h in hits_of(source) if h["kind"] == "unresolved_argv"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["verb"], "docker <verb>")

    def test_a_prefix_before_a_star_is_still_faithful(self):
        """`["git","-C",repo,*args]` really does run git. Ending the list at the
        star keeps the prefix true rather than discarding the whole argv."""
        source = ('import subprocess\n'
                  'def f(repo, args):\n'
                  '    subprocess.run(["git", "-C", repo, *args])\n')
        self.assertEqual([(h["kind"], h["verb"]) for h in hits_of(source)],
                         [("exec", "git")])

    def test_an_argv0_that_is_not_statically_known_is_unresolved(self):
        source = ('import subprocess, sys\n'
                  'def f(entry):\n'
                  '    subprocess.run([sys.executable, entry, "--help"])\n')
        self.assertEqual([h["verb"] for h in hits_of(source)], ["<argv0>"])

    def test_a_shell_string_is_both_read_and_declared_unreadable(self):
        """Two facts about one call, and both are true. The string IS a
        command the scanner can split, so the destroyer in it is reported —
        and the shell string is also a command it cannot read in general, so
        that is reported too. Emitting only the first would present a
        partially-read shell argument as fully understood; emitting only the
        second would lose the destroyer."""
        found = hits_of('import subprocess\nsubprocess.run("docker rm x", shell=True)\n')
        self.assertEqual([(h["kind"], h["verb"]) for h in found],
                         [("mutation", "docker rm"),
                          ("unresolved_argv", "subprocess.run(shell=True)")])

    def test_an_unparseable_file_is_not_a_clean_file(self):
        result = guard.scan_file("broken.py", "def f(:\n")
        self.assertFalse(result["parsed"])
        self.assertIn("SyntaxError", result["error"])


# ---------------------------------------------------------------------------
# The regression that mattered: a command bound inside an `if`
# ---------------------------------------------------------------------------


class TheActuatorIsVisible(unittest.TestCase):
    """`runtime/daemon.py`'s `Actuator.restart` binds its command in two
    branches of one `if` and hands it to a runner. A scanner that reads only a
    function's top-level statements sees neither branch, finds nothing, and
    reports the product as having no actuator at all — the smoke detector
    reporting a clean house because its battery is out."""

    SOURCE = '''
import subprocess
class Runner:
    def run(self, command, timeout): pass
class Actuator:
    def __init__(self, runner):
        self.runner = runner
    def restart(self, unit, container):
        if unit:
            command = ["systemctl", "--user", "restart", unit]
        elif container:
            command = ["docker", "restart", container]
        else:
            return None
        return self.runner.run(command, 60.0)
'''

    def test_a_command_bound_in_a_branch_is_found(self):
        found = [h for h in hits_of(self.SOURCE) if h["kind"] == "unresolved_argv"]
        self.assertEqual(len(found), 1, found)
        self.assertIn("systemctl", found[0]["verb"])
        self.assertIn("docker", found[0]["verb"])

    def test_the_ambiguity_is_named_rather_than_picked(self):
        """"Two possible commands" is a fact a reviewer can act on. Choosing one
        silently would be this scanner asserting which branch runs."""
        found = [h for h in hits_of(self.SOURCE) if h["kind"] == "unresolved_argv"][0]
        self.assertIn("more than one branch", found["detail"])

    def test_a_single_branch_is_read_rather_than_called_ambiguous(self):
        """And read through the flags: the verb is `restart`, behind `--user`,
        which is only visible because the command is resolved rather than
        pattern-matched."""
        source = ('class Runner:\n    def run(self, c, t): pass\n'
                  'class A:\n    def __init__(self, r):\n        self.runner = r\n'
                  '    def go(self, unit):\n'
                  '        command = ["systemctl", "--user", "restart", unit]\n'
                  '        return self.runner.run(command, 60.0)\n')
        self.assertEqual([(h["kind"], h["verb"]) for h in hits_of(source)],
                         [("command_handoff", "systemctl restart")])


# ---------------------------------------------------------------------------
# The two heuristics, and the shapes that must not fire
# ---------------------------------------------------------------------------


class TheHandoffHeuristicDoesNotOverFire(unittest.TestCase):
    def test_a_bare_string_handed_to_a_method_is_not_a_command(self):
        """`key.startswith("docker")` is a predicate. `_static_argv` splits
        strings because `subprocess.run("docker ps", shell=True)` really does
        run one, so the caller's shape is what tells them apart."""
        source = 'def f(keys):\n    return [k for k in keys if k.startswith("docker")]\n'
        self.assertEqual(hits_of(source), [])

    def test_a_builtin_callee_is_never_a_command_runner(self):
        """No Python builtin execs. `set(HOST_READERS)` — a module-level tuple
        of command names — otherwise reads as `docker df free ss du find`
        handed to `set`."""
        source = ('READERS = ("docker", "df", "free")\n'
                  'def f():\n    return set(READERS)\n')
        self.assertEqual(hits_of(source), [])

    def test_an_exception_message_is_not_a_command(self):
        source = 'def f():\n    raise OSError("docker ps failed")\n'
        self.assertEqual(hits_of(source), [])

    def test_a_literal_handed_to_a_runner_is_still_a_hit(self):
        """The guard must not have cost the real coverage it was written to
        keep: a list handed to a named runner is exactly the chokepoint shape."""
        source = ('class H:\n    def read(self, argv, timeout): pass\n'
                  'class I:\n    def __init__(self, host):\n        self.host = host\n'
                  '    def summary(self):\n'
                  '        return self.host.read(["docker", "info"], 10)\n')
        self.assertEqual([(h["kind"], h["verb"]) for h in hits_of(source)],
                         [("command_handoff", "docker")])

    def test_a_reader_the_mcp_layer_may_not_run_is_a_host_reader(self):
        """Named by the read each one actually performs. A bare flag is not a
        read for `docker` — `["docker", "-x"]` has no verb this scanner can
        see, and reporting it as a read is the silent-clean failure the
        `unresolved_argv` kind exists to prevent. So the flag-only shape is
        asserted as the gap it is, in the same test that asserts the reads."""
        reads = {"docker": ["docker", "ps"], "df": ["df", "-h"],
                 "free": ["free", "-m"], "ss": ["ss", "-ltnp"],
                 "du": ["du", "-sh", "/"], "find": ["find", "/tmp"]}
        for binary in guard.HOST_READERS:
            with self.subTest(binary=binary):
                words = ", ".join(f'"{w}"' for w in reads[binary])
                found = hits_of(f"import subprocess\nsubprocess.run([{words}])\n")
                self.assertEqual(found[0]["kind"], "host_reader", binary)

    def test_a_docker_with_no_readable_verb_is_a_gap_not_a_read(self):
        found = hits_of('import subprocess\nsubprocess.run(["docker", "-x"])\n')
        self.assertEqual([(h["kind"], h["verb"]) for h in found],
                         [("unresolved_argv", "docker <verb>")])


# ---------------------------------------------------------------------------
# Arity, not names: `str.replace` is not `Path.replace`
# ---------------------------------------------------------------------------


class ReplaceIsResolvedBySignature(unittest.TestCase):
    def test_the_timestamp_idiom_is_not_a_mutation(self):
        """`stamp.replace("+00:00", "Z")` appears in a dozen formatters. A
        name-based rule reports every one of them as a file write."""
        source = 'def f(stamp):\n    return stamp.replace("+00:00", "Z")\n'
        self.assertEqual(hits_of(source), [])

    def test_path_replace_is_a_mutation(self):
        source = 'from pathlib import Path\ndef f(tmp, dest):\n    tmp.replace(dest)\n'
        self.assertEqual([(h["kind"], h["verb"]) for h in hits_of(source)],
                         [("fs_mutation", ".replace()")])

    def test_a_kill_with_an_argument_is_not_popens_kill(self):
        """`Popen.kill()` takes none. `os.kill(p, 9)` is a different call and is
        caught by name; a `.kill(arg)` on some other object is neither."""
        self.assertEqual(hits_of("def f(x):\n    x.kill()\n")[0]["kind"],
                         "process_termination")
        self.assertEqual(hits_of("def f(x):\n    x.kill(9)\n"), [])


# ---------------------------------------------------------------------------
# The two directions between the scan and the contract
# ---------------------------------------------------------------------------


class BothDirections(unittest.TestCase):
    PRODUCT = """
import subprocess
def f():
    subprocess.run(["docker", "ps"])
"""

    def audit(self, *, product=None, tests=None, contract=None):
        sources = {"product/p.py": product if product is not None else self.PRODUCT}
        sources.update(tests or {})
        return guard.audit(
            scan=guard.scan_sources(sources),
            contract=contract if contract is not None else
            {"actuators": [{"id": "x", "kind": "host_reader", "verb": "docker",
                            "sites": [{"file": "product/p.py",
                                       "symbols": ["f"]}]}]})

    def test_a_declared_site_is_not_undeclared(self):
        report = self.audit()
        self.assertEqual(report["undeclared"], [])
        self.assertEqual(report["stale"], [])

    def test_an_undeclared_site_is_reported(self):
        report = self.audit(contract={"actuators": []})
        self.assertEqual(len(report["undeclared"]), 1)
        self.assertEqual(report["undeclared"][0]["verb"], "docker")

    def test_a_declaration_that_matches_nothing_is_stale(self):
        report = self.audit(product="def f():\n    pass\n")
        self.assertEqual(len(report["stale"]), 1)
        self.assertEqual(report["stale"][0]["id"], "x")

    def test_a_declaration_naming_no_site_covers_nothing(self):
        report = self.audit(contract={"actuators": [{"id": "x", "kind": "exec",
                                                     "verb": "git"}]})
        self.assertEqual(len(report["stale"]), 1)
        self.assertIn("names no site", report["stale"][0]["why"])

    def test_a_stale_declaration_can_say_it_is_allowed_to_be_absent(self):
        report = self.audit(product="def f():\n    pass\n",
                            contract={"actuators": [
                                {"id": "x", "may_be_absent": True,
                                 "kind": "exec", "verb": "git",
                                 "sites": [{"file": "product/p.py", "symbols": ["f"]}]}]})
        self.assertEqual(report["stale"], [])

    def test_a_per_site_verb_override_narrows_one_site_only(self):
        """The capability is one permission; the command varies by site. The
        override is written where it applies, and a site that carries the wrong
        verb is undeclared rather than waved through."""
        product = ('import subprocess\n'
                   'def git_one():\n    subprocess.run(["git", "rev-parse", "HEAD"])\n'
                   'def other():\n    subprocess.run(["docker", "ps"])\n')
        contract = {"actuators": [{
            "id": "x", "kind": "exec", "verb": "git",
            "sites": [{"file": "product/p.py", "symbols": ["git_one"]},
                      {"file": "product/p.py", "symbols": ["other"],
                       "verb": "docker", "kind": "host_reader"}]}]}
        report = self.audit(product=product, contract=contract)
        self.assertEqual(report["undeclared"], [])
        self.assertEqual(report["stale"], [])

    def test_a_site_with_the_wrong_verb_is_undeclared(self):
        contract = {"actuators": [{"id": "x", "kind": "exec", "verb": "git",
                                   "sites": [{"file": "product/p.py",
                                              "symbols": ["f"]}]}]}
        report = self.audit(contract=contract)
        self.assertEqual(len(report["undeclared"]), 1)


# ---------------------------------------------------------------------------
# The MCP layer rule, and the tests scope
# ---------------------------------------------------------------------------


class TheMCPLayerRuleIsAbsolute(unittest.TestCase):
    def test_a_host_reader_in_the_mcp_layer_is_reported(self):
        scan = guard.scan_sources(
            {"mcp/server.py": 'import subprocess\nsubprocess.run(["docker","ps"])\n'})
        report = guard.audit(scan=scan, contract={
            "actuators": [], "mcp_layer_forbidden_commands": list(guard.HOST_READERS)})
        self.assertEqual(len(report["mcp_layer_host_reach"]), 1)

    def test_a_declaration_cannot_cover_an_mcp_host_read(self):
        """There is no exemption table entry that makes this legal. The rule is
        that the MCP process holds no host knowledge of its own, so a
        declaration covering it must not turn the finding off."""
        scan = guard.scan_sources(
            {"mcp/server.py": 'import subprocess\nsubprocess.run(["docker","ps"])\n'})
        report = guard.audit(scan=scan, contract={
            "actuators": [{"id": "x", "kind": "host_reader", "verb": "docker",
                           "sites": [{"file": "mcp/server.py", "symbols": ["<module>"]}]}],
            "mcp_layer_forbidden_commands": list(guard.HOST_READERS)})
        self.assertEqual(report["undeclared"], [], "it IS declared")
        self.assertEqual(len(report["mcp_layer_host_reach"]), 1,
                         "and it is still a breach")

    def test_the_forbidden_list_is_checked_against_the_vocabulary(self):
        report = guard.audit(scan=guard.scan_sources({}),
                             contract={"actuators": [],
                                       "mcp_layer_forbidden_commands": ["docker"]})
        self.assertIn("df", report["mcp_layer_list_drift"])

    def test_no_test_may_run_a_state_changing_command(self):
        scan = guard.scan_sources(
            {"tests/test_x.py": 'import subprocess\nsubprocess.run(["docker","rm","c"])\n'})
        report = guard.audit(scan=scan, contract={
            "actuators": [], "scopes": [{"scope": "tests",
                                         "forbidden_kinds": ["mutation"]}]})
        self.assertEqual(report["counts"]["tests_breach"], 1)

    def test_the_tests_scope_is_inventoried_rather_than_site_declared(self):
        """A test hit is not an UNDECLARED_ACTUATOR — that would demand a
        hundred name entries — but it is not dropped either. It appears in the
        tests inventory."""
        scan = guard.scan_sources(
            {"tests/test_x.py": 'import subprocess\nsubprocess.run(["git","status"])\n'})
        report = guard.audit(scan=scan, contract={"actuators": []})
        self.assertEqual(report["undeclared"], [])
        self.assertEqual(len(report["tests"]), 1)


# ---------------------------------------------------------------------------
# Producers: one fact, one producer
# ---------------------------------------------------------------------------


class ProducersResolve(unittest.TestCase):
    def resolve(self, namespaces, *, external=(), contract_extra=None,
                existing=("fleet/", "runtime/health.py")):
        contract = {"namespaces": namespaces, "external_producers": list(external)}
        contract.update(contract_extra or {})
        return guard.resolve_producers(
            contract, exists=lambda p: p in existing)

    def rows(self, report):
        return {r["namespace"]: r for r in report["namespaces"]}

    def test_a_path_producer_that_exists_is_present(self):
        report = self.resolve({"health": {"producer": "runtime/health.py"}})
        self.assertEqual(self.rows(report)["health"]["resolution"], "PRESENT")

    def test_a_path_producer_that_is_absent_names_the_path(self):
        report = self.resolve({"gone": {"producer": "runtime/nope.py"}})
        row = self.rows(report)["gone"]
        self.assertEqual(row["resolution"], "MISSING")
        self.assertIn("runtime/nope.py", row["why"])

    def test_an_external_domain_is_resolved_before_it_is_read_as_a_path(self):
        """`workspace-management` has no space in it, so the path reading would
        take it for a relative directory and call it absent. A skill that lives
        in another tree reported as a missing path is a false accusation, and a
        contract that makes false accusations stops being read."""
        report = self.resolve({"source-index": {"producer": "workspace-management"}},
                              external=["workspace-management"])
        row = self.rows(report)["source-index"]
        self.assertEqual(row["resolution"], "external")
        self.assertEqual(row["named_domains"], ["workspace-management"])

    def test_a_domain_can_be_named_inside_a_sentence(self):
        report = self.resolve({"abilities": {
            "producer": "ability-management (writes), this node (observes)"}},
            external=["ability-management"])
        self.assertEqual(self.rows(report)["abilities"]["resolution"], "external")

    def test_prose_that_names_nothing_is_unresolved(self):
        """The fixture is deliberately a sentence that names nothing, and NOT
        a copy of a real contract entry.

        It used to read `{"boards": {"producer": "serverosctl board render"}}` —
        which was, at the time, a faithful copy of what the live contract said.
        That is exactly the shape that made it worthless: when the contract was
        corrected, this fixture kept asserting the old world and kept passing,
        and the only thing it was still testing was a string no clause in this
        repository contains any more. A consumer test that hand-writes the
        producer's shape is immune to the producer changing.
        """
        report = self.resolve({"boards": {"producer": "the boards renderer"}})
        row = self.rows(report)["boards"]
        self.assertEqual(row["resolution"], "UNRESOLVED")
        self.assertIn("no path, no declared external domain", row["why"])

    def test_a_pointer_into_the_contract_resolves(self):
        report = self.resolve({"projections": {"producer": "projections.manifest"}},
                              contract_extra={"projections": {"manifest": {}}})
        self.assertEqual(self.rows(report)["projections"]["resolution"], "CONTRACT")

    def test_a_pointer_that_names_nothing_is_still_a_failure(self):
        report = self.resolve({"projections": {"producer": "projections.manifest"}})
        self.assertEqual(self.rows(report)["projections"]["resolution"], "MISSING")

    def test_children_are_walked_and_named_by_their_path(self):
        report = self.resolve({"runtime": {"producer": "runtime/", "children": {
            "health": {"producer": "runtime/health.py"}}}})
        self.assertIn("runtime/health", self.rows(report))

    def test_unresolvable_producers_do_not_block_but_absent_ones_do(self):
        """The calibration, asserted rather than described. A sentence that
        names nothing is a documentation defect; a path that is not there is an
        absence. Rating both HIGH would make this checker's first honest run
        fail a gate that was green before it existed."""
        unresolved = guard.findings(
            audit_report={"undeclared": [], "stale": [], "mcp_layer_host_reach": [],
                          "mcp_layer_list_drift": [], "tests_breach": [],
                          "unparsed_files": []},
            producer_report={"unresolved": [{"namespace": "x"}],
                             "namespaces": []})
        absent = guard.findings(
            audit_report={"undeclared": [], "stale": [], "mcp_layer_host_reach": [],
                          "mcp_layer_list_drift": [], "tests_breach": [],
                          "unparsed_files": []},
            producer_report={"unresolved": [],
                             "namespaces": [{"namespace": "x", "producer": "p",
                                             "resolution": "MISSING", "why": "w"}]})
        self.assertEqual([(f["id"], f["severity"]) for f in unresolved],
                         [("PRODUCER_UNRESOLVABLE", "MEDIUM")])
        self.assertEqual([(f["id"], f["severity"]) for f in absent],
                         [("PRODUCER_ABSENT", "HIGH")])


# ---------------------------------------------------------------------------
# The live tree and the live contracts
# ---------------------------------------------------------------------------


class TheLiveTreeIsCovered(unittest.TestCase):
    """The assertion the arrangement exists for, run against the real thing."""

    @classmethod
    def setUpClass(cls):
        cls.actuators = contract_at(REPO / "runtime" / "actuator-contract.yaml")
        cls.state_root = contract_at(REPO / "fleet" / "state-root-contract.yaml")
        cls.report = guard.check(root=REPO, actuators=cls.actuators,
                                 state_root=cls.state_root)

    def test_every_product_call_site_is_declared(self):
        undeclared = self.report["audit"]["undeclared"]
        self.assertEqual(
            [], [f"{h['file']}:{h['line']} {h['kind']} {h['verb']} {h['symbol']}"
                 for h in undeclared])

    def test_every_declaration_still_matches_a_call_site(self):
        self.assertEqual([], self.report["audit"]["stale"])

    def test_the_mcp_layer_runs_no_host_command(self):
        self.assertEqual([], self.report["audit"]["mcp_layer_host_reach"])

    def test_the_forbidden_list_has_not_drifted(self):
        self.assertEqual([], self.report["audit"]["mcp_layer_list_drift"])

    def test_no_test_runs_a_state_changing_command(self):
        self.assertEqual([], self.report["audit"]["tests_breach"])

    def test_every_source_parsed(self):
        self.assertEqual([], self.report["audit"]["unparsed_files"])

    def test_the_scan_found_something(self):
        """A gate that passes on an empty scan is vacuous. This asserts the
        scanner is looking at a tree with reachability in it."""
        self.assertGreater(self.report["audit"]["counts"]["product_hits"], 20)
        self.assertGreater(self.report["audit"]["counts"]["declared"], 15)

    def test_the_product_has_no_reachable_destroyer(self):
        """§26's headline claim, and the only one a reader actually wants: no
        product code path runs a mutating docker verb, anywhere."""
        mutating = [h for h in self.report["audit"]["tests"] + []
                    if h["kind"] == "mutation"]
        scan = guard.scan_sources({
            str(p.relative_to(REPO)): p.read_text(errors="replace")
            for p in sorted(REPO.rglob("*.py"))
            if ".git" not in p.parts and not str(p.relative_to(REPO)).startswith("tests/")})
        offenders = [f"{h['file']}:{h['line']} {h['verb']}"
                     for h in scan["hits"] if h["kind"] == "mutation"]
        self.assertEqual([], offenders + mutating)

    def test_boards_names_a_producer_that_resolves(self):
        """`boards` used to be this file's headline UNRESOLVED finding, and the
        finding was wrong in the direction that costs the most: the producer
        existed, in another distribution, spelled with a name this repository
        also answers to. A checker that had said "unresolved, and here is who
        owns it" would have sent a reader to the control plane in a minute.

        So the assertion is now the positive one, against the live contract —
        and it is the assertion that was missing before. `boards` must resolve,
        and it must resolve to the domain the contract declares for it, so that
        the next person to edit this field gets a red test rather than a field
        that quietly reads as unwritable.
        """
        row = next(r for r in self.report["producers"]["namespaces"]
                   if r["namespace"] == "boards")
        self.assertNotEqual("UNRESOLVED", row["resolution"], row["why"])
        self.assertEqual(row["resolution"], "external")
        self.assertEqual(row["named_domains"], ["work-management"])

    def test_every_unresolved_producer_is_recorded_by_name(self):
        """The general form, and the repair of a real failure mode in this file.

        The test this replaced was conditional — it checked that `boards` was
        both UNRESOLVED here and exempted over there, `if "boards" in names`.
        When the resolution changed, the condition went false and the test
        stopped checking anything at all, silently, which is the one outcome a
        cross-record check must never have. Two records that agree by both
        being absent from each other's view are not two records.

        Now the unresolved namespaces must appear in KNOWN_UNRESOLVED below
        with a reason. A new one fails this test; closing one fails it too,
        until the entry is deleted — the same shape as `KNOWN_ABSENT` in
        `test_declared_commands.py`, and for the same reason."""
        names = {r["namespace"] for r in self.report["producers"]["namespaces"]
                 if r["resolution"] == "UNRESOLVED"}
        self.assertEqual(self.report["producers"]["counts"]["unresolved"], len(names))
        self.assertEqual(
            sorted(names), sorted(KNOWN_UNRESOLVED),
            "the set of producers nothing can resolve has changed; "
            "add or remove the entry in KNOWN_UNRESOLVED with a reason")
        for name, entry in KNOWN_UNRESOLVED.items():
            self.assertTrue(entry["because"], f"{name} has no because")
            self.assertIn(entry["disposition"],
                          {"NEEDS_HUMAN", "REDECLARED", "DEFER"}, name)


if __name__ == "__main__":
    unittest.main()
