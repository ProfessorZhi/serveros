#!/usr/bin/env python3
"""§58 — real acceptance, Linux outbound: the four answers, and not "network error".

THE ACCEPTANCE THE BRIEF STATES
-------------------------------
```text
ServerOS应明确：
local Agent cannot reach OpenAI
required tunnel unavailable
以及：
which runtime is missing
who owns it
whether auto recovery is permitted
不能只得到：network error
```

WHY "NETWORK ERROR" IS NOT A FAILURE OF THE NETWORK LAYER
----------------------------------------------------------
Every step that produced that message worked. The request was made, the dial
was refused, the refusal was caught and reported. What is absent is a layer that
can say WHICH object was missing and whether anyone had ever declared it — and
without that layer the only available answers are "retry" and "check the
network", both of which are wrong here and neither of which converges.

The measurement this case is built on:

    .bashrc:154-156   export HTTP_PROXY/HTTPS_PROXY/ALL_PROXY=http://127.0.0.1:17890
    /proc/net/tcp     no LISTEN on 17890
                      one LISTEN on 127.0.0.1:17891, held by no visible uid
    registry          no resource of kind `tunnel` at all
    ccbridge-bridge.service   inactive (dead); its own comments say not to
                              rebuild an adapter for 17890

That is §38's "cannot confirm the owner" branch, so the required outcome is
`UNKNOWN_OWNER / NEEDS_HUMAN` — not a registration invented to make the report
look complete. A guessed registration is worse than a missing one: the missing
one fails loudly at the next dial, and the guessed one is believed.

WHAT IS ASSERTED
----------------
  1. The four questions are all answered, and the answer is never the word
     "network error".
  2. The two ways a runtime can be missing stay distinguishable: NOT_REGISTERED
     (nobody ever declared it) and REGISTERED_NOT_SERVING (declared, and down).
  3. Ownership is never invented: with no declaration the owner is
     UNKNOWN_OWNER, and the disposition is NEEDS_HUMAN.
  4. Automatic recovery is not permitted for a resource that does not exist,
     and the reason says why rather than reporting a default.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"

PROXY_ENDPOINT = "127.0.0.1:17890"
CANDIDATE = "tunnel:agent-openai-outbound"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry = _load("serveros_runtime_registry", "registry.py")
infra = _load("serveros_runtime_infrastructure", "infrastructure.py")


def the_requirement(**overrides) -> dict:
    """What the Agent depends on, and who said so.

    `named_by` is not decoration. It is the difference between "something in
    the environment points at this endpoint" and "I believe this endpoint
    matters" — and only the first is evidence, which is why the config
    statements are cited by line rather than summarised.
    """
    requirement = {
        "runtime": CANDIDATE,
        "kind": "tunnel",
        "endpoint": PROXY_ENDPOINT,
        "named_by": [
            ".bashrc:154 HTTP_PROXY=http://127.0.0.1:17890",
            ".bashrc:155 HTTPS_PROXY=http://127.0.0.1:17890",
            ".bashrc:156 ALL_PROXY=http://127.0.0.1:17890",
        ],
        "because": (
            "the local Codex/OpenAI path is routed through this proxy, so "
            "every outbound model call from this node depends on it"
        ),
    }
    requirement.update(overrides)
    return requirement


# ---------------------------------------------------------------------------
# A synthetic /proc — the same four methods `readable_host` requires
# ---------------------------------------------------------------------------


def tcp_row(host_hex: str, port: int, inode: str, state: str = "0A") -> str:
    return (f"   0: {host_hex}:{port:04X} 00000000:0000 {state} "
            f"00000000:00000000 00:00000000 00000000  1006        0 {inode} 1 "
            f"0000000000000000 100 0 0 10 0")


def proc_with(listeners, *, held_by=None) -> dict:
    """`{port: inode}` → the /proc files a host would have.

    `held_by` maps inode → pid for sockets a visible process holds. Anything
    not listed there belongs to nobody this uid can read, which is what every
    root-held socket looks like from here.
    """
    held_by = held_by or {}
    rows = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
            "retrnsmt   uid  timeout inode"]
    links, entries = {}, []
    for port, inode in listeners.items():
        rows.append(tcp_row("0100007F", port, inode))
        pid = held_by.get(inode)
        if pid is not None:
            entries.append(str(pid))
            links[f"/proc/{pid}/fd/7"] = f"socket:[{inode}]"
    return {
        "/proc/net/tcp": "\n".join(rows) + "\n",
        "/proc/net/tcp6": "  sl  local_address remote_address st\n",
        # Fields 3..24, all zero except the state. `attribution.parse_stat`
        # indexes by field NUMBER and raises IndexError on a short line, so a
        # fixture that wrote three zeros would fail loudly rather than read as
        # a healthy process — which is the behaviour worth having.
        **{f"/proc/{p}/stat": f"{p} (svc) S " + " ".join(["0"] * 22) + "\n"
           for p in entries},
        **{f"/proc/{p}/status": "Name:\tsvc\nUid:\t1006\t1006\t1006\t1006\n"
           for p in entries},
    }, links


class FakeProcHost:
    """A synthetic /proc, with the four methods `readable_host` requires."""

    name = "fake-proc-host"

    def __init__(self, files=None, links=None, uptime=1000.0):
        self.files = files or {}
        self.links = links or {}
        self._uptime = uptime

    def read_text(self, path):
        return self.files.get(path)

    def read_bytes(self, path):
        value = self.files.get(path)
        return value.encode() if isinstance(value, str) else None

    def listdir(self, path):
        if path == "/proc":
            seen = {}
            for entry in self.files:
                parts = entry.split("/")
                if len(parts) > 2 and parts[1] == "proc" and parts[2].isdigit():
                    seen[parts[2]] = None
            return list(seen)
        # Every other directory is derived from the declared links, so a
        # fixture that names one fd really is a process holding one socket.
        # Returning [] here would make every socket look unheld, which is the
        # answer that reads as "unowned" rather than as "the fake was shallow".
        prefix = path.rstrip("/") + "/"
        return sorted({p[len(prefix):].split("/")[0]
                       for p in self.links if p.startswith(prefix)})

    def readlink(self, path):
        return self.links.get(path)

    def run(self, args, timeout=8.0):
        return 127, "", "not found"

    def uptime(self):
        return self._uptime

    def page_size(self):
        return 4096

    def uid_name(self, uid):
        return f"uid{uid}"


def host_with(listeners, *, held_by=None) -> FakeProcHost:
    files, links = proc_with(listeners, held_by=held_by)
    return FakeProcHost(files=files, links=links)


def an_empty_registry() -> "registry.Registry":
    """The live registry's relevant property: no declared tunnels at all."""
    return registry.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": [],
    })


def a_registered_tunnel(**overrides) -> dict:
    record = {
        "id": "tunnel-agent-openai-outbound",
        "kind": "tunnel",
        # `serveros` with a device ref, which is the live registry's idiom for
        # a resource this fabric runs on a node. A device is not an owner TYPE
        # — ownership is one of project/work-item/serveros/ability, and where
        # the resource physically runs is `origin_device`.
        "owner": {"type": "serveros", "ref": "device:server-beijing"},
        "origin_device": "device:server-beijing",
        "scope": "user",
        "lifecycle": {"class": "persistent"},
        "desired": {"state": "running"},
        "protection": {"level": "normal"},
        "recovery": {"model": "restart"},
        "transport": {"class": "socks5", "local": PROXY_ENDPOINT,
                      "remote": "17891"},
        "probes": [{"id": "egress-liveness", "kind": "process-exists",
                    "axis": "liveness", "pattern": "egress"},
                   {"id": "egress-transport", "kind": "socket-connect",
                    "axis": "transport", "path": PROXY_ENDPOINT}],
        "not_applicable": {"functional": {"because": "a proxy has no callable "
                                                     "surface of its own"}},
    }
    record.update(overrides)
    return registry.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": [record],
    })


def answer(reg=None, host=None, **requirement) -> dict:
    return infra.egress_answer(reg if reg is not None else an_empty_registry(),
                               the_requirement(**requirement),
                               host=host if host is not None else host_with({}))


# ---------------------------------------------------------------------------
# 1 · The four questions, and the word that is not an answer
# ---------------------------------------------------------------------------


class FourAnswersTests(unittest.TestCase):
    def test_the_report_answers_exactly_the_four_questions(self):
        """The acceptance sentence, mechanically: all four are present, and a
        fifth that replaced them would fail here rather than in a person's
        reading. `EGRESS_QUESTIONS` is asserted against, so adding a question
        to the brief's set fails this test instead of leaving the renderer
        quietly answering the previous set."""
        doc = answer()
        self.assertEqual(sorted(doc["answers"]), sorted(infra.EGRESS_QUESTIONS))
        for question in infra.EGRESS_QUESTIONS:
            self.assertTrue(doc["answers"][question].get("because"),
                            f"{question} carries no reason")
        self.assertEqual(doc["schema"], infra.EGRESS_SCHEMA)

    def test_the_answer_names_an_object_where_a_network_error_names_nothing(self):
        """The brief's whole complaint, asserted the way it is actually
        decidable. "network error" is not a wrong string — it is an accurate
        description of a refused dial, and the report says so in as many words.
        What makes it useless is that it names nothing, so the test is not that
        the phrase is absent; it is that every answer names a specific object a
        person can go and look at.

        The first draft of this test forbade the phrase and failed on the
        report's own explanation of it, which is the right kind of failure: a
        negative assertion about vocabulary cannot tell an answer from an
        apology for not having one.
        """
        doc = answer()
        self.assertFalse(doc["answers"]["reachable"]["value"])
        self.assertIn("cannot reach", doc["answers"]["reachable"]["claim"])

        # One concrete object per answer: the socket, the runtime id, the
        # registry, the desired state.
        self.assertIn(PROXY_ENDPOINT, doc["answers"]["reachable"]["because"])
        self.assertIn(CANDIDATE,
                      doc["answers"]["which_runtime_is_missing"]["because"])
        self.assertIn("registration", doc["answers"]["who_owns_it"]["because"])
        self.assertIn("recover", doc["answers"]["auto_recovery_permitted"]["because"])
        for question in infra.EGRESS_QUESTIONS:
            self.assertGreater(len(doc["answers"][question]["because"]), 80,
                               f"{question} answers in a phrase rather than a sentence")

    def test_the_requirement_itself_is_echoed_with_its_evidence(self):
        """The report is about a claim someone made (`named_by`), and the claim
        travels with the answer. An answer about an unnamed requirement is one
        nobody can check — the reason `.bashrc` is cited by line rather than
        summarised as "the environment"."""
        doc = answer()
        self.assertEqual(doc["requirement"]["endpoint"], PROXY_ENDPOINT)
        self.assertEqual(len(doc["requirement"]["named_by"]), 3)
        self.assertTrue(all(".bashrc:" in one for one in doc["requirement"]["named_by"]))

    def test_an_unanswerable_requirement_is_refused_rather_than_defaulted(self):
        """Every field decides whether a registration covers the endpoint, so a
        missing one silently becomes "nothing serves it" — a measurement
        reported where no measurement happened."""
        for broken in ({"endpoint": None}, {"runtime": ""}, {"endpoint": "17890"}):
            with self.assertRaises(infra.EgressRequirementInvalid):
                answer(**broken)


# ---------------------------------------------------------------------------
# 2 · The two ways a runtime can be missing stay distinguishable
# ---------------------------------------------------------------------------


class WhichRuntimeIsMissingTests(unittest.TestCase):
    def test_nothing_declared_and_nothing_listening_is_not_registered(self):
        """The live case. The object the Agent depends on was never registered
        by anyone, and the report says so in those words — the alternative,
        "UNAVAILABLE", would describe a registration that does not exist."""
        missing = answer()["answers"]["which_runtime_is_missing"]
        self.assertEqual(missing["state"], infra.EGRESS_NOT_REGISTERED)
        self.assertIsNone(missing["id"])
        self.assertEqual(missing["candidate_id"], CANDIDATE)
        self.assertIn("no declaration to be missing FROM", missing["because"])

    def test_declared_and_down_is_a_different_answer(self):
        """The other world, and the test that keeps the two from collapsing.
        Same endpoint, same measurement, one registration — and the answer
        changes in every field that decides what a person does next."""
        doc = answer(reg=a_registered_tunnel())
        missing = doc["answers"]["which_runtime_is_missing"]
        self.assertEqual(missing["state"], infra.EGRESS_REGISTERED_NOT_SERVING)
        self.assertEqual(missing["id"], "tunnel-agent-openai-outbound")
        self.assertNotEqual(missing["state"],
                            answer()["answers"]["which_runtime_is_missing"]["state"])
        self.assertIn("declaration without an implementation", missing["because"])

    def test_a_registration_on_the_same_port_covers_it_under_another_name(self):
        """Coverage is decided by the socket as well as by the id, because what
        `.bashrc` names is a socket. An id-only match reports "no tunnel" for a
        tunnel that is running under a name nobody agreed on."""
        reg = a_registered_tunnel(id="tunnel-some-other-name")
        doc = answer(reg=reg)
        self.assertEqual([r["id"] for r in doc["registrations"]],
                         ["tunnel-some-other-name"])
        self.assertEqual(doc["registrations"][0]["matched_by"], ["transport.local"])

    def test_present_but_undeclared_is_reported_as_undeclared(self):
        """The case that is not in the brief and is the more dangerous of the
        two: something is holding the port and no declaration names it. It
        works today, which is precisely why nothing will find it tomorrow."""
        doc = answer(host=host_with({17890: "9001"}, held_by={"9001": 4242}))
        missing = doc["answers"]["which_runtime_is_missing"]
        self.assertEqual(missing["state"], infra.EGRESS_UNDECLARED_PRESENT)
        self.assertEqual(doc["action"], "NEEDS_HUMAN")

    def test_a_wildcard_listener_serves_a_loopback_endpoint(self):
        """`0.0.0.0:17890` accepts connections to `127.0.0.1:17890`. Reported
        as unserved, the reader concludes the proxy is gone and stops looking,
        which is the expensive direction to be wrong in."""
        self.assertTrue(infra._serves("0.0.0.0:17890", "127.0.0.1:17890"))
        # ...and not the other way round. A loopback-bound socket does not
        # serve a requirement named at a routable address.
        self.assertFalse(infra._serves("127.0.0.1:17890", "10.0.0.5:17890"))
        self.assertFalse(infra._serves("127.0.0.1:17891", "127.0.0.1:17890"))


# ---------------------------------------------------------------------------
# 3 · Ownership is never invented
# ---------------------------------------------------------------------------


class WhoOwnsItTests(unittest.TestCase):
    def test_nothing_declared_nothing_serving_is_unknown_owner(self):
        """§38's branch. The disposition is NEEDS_HUMAN because a human is the
        only party who can supply the fact the report refuses to invent."""
        doc = answer()
        owner = doc["answers"]["who_owns_it"]
        self.assertEqual(owner["state"], infra.OWNER_UNDECLARED)
        self.assertIsNone(owner["ref"])
        self.assertIsNone(owner["owner_type"])
        self.assertEqual(owner["candidates"], [])
        self.assertEqual(doc["action"], "NEEDS_HUMAN")
        self.assertIn("never guessed", owner["because"])

    def test_an_unreadable_listener_is_unattributed_and_not_unowned(self):
        """The distinction hidepid forces. A root-held socket serving the port
        means something IS there and this uid may not look at it — so the
        report says UNATTRIBUTED, keeps the inode, and does not claim the
        socket is unowned. Claiming otherwise is how a live forward gets
        cleaned up as litter."""
        doc = answer(host=host_with({17890: "9001"}))
        owner = doc["answers"]["who_owns_it"]
        self.assertEqual(owner["state"], infra.OWNER_UNATTRIBUTED)
        self.assertEqual(owner["candidates"], [{"local": "127.0.0.1:17890",
                                                "inode": "9001"}])
        self.assertIn("hidepid=invisible", owner["because"])

    def test_a_declared_owner_is_named_from_the_registration(self):
        """The one case where an owner is knowable, and it is knowable because
        somebody wrote it down — not because a process happened to be running."""
        owner = answer(reg=a_registered_tunnel())["answers"]["who_owns_it"]
        self.assertEqual(owner["state"], infra.OWNER_DECLARED)
        self.assertEqual(owner["ref"], "device:server-beijing")
        self.assertEqual(owner["owner_type"], "serveros")

    def test_a_visible_holder_is_named_as_a_fact_and_still_not_as_an_owner(self):
        """A pid holding the socket is a measurement; it is not a declaration.
        The registry decides what is ours, and a process that happens to be
        forwarding does not register itself by forwarding — the rule
        `tunnel_inventory` already applies to undeclared forwarders."""
        doc = answer(host=host_with({17890: "9001"}, held_by={"9001": 4242}))
        self.assertEqual(doc["answers"]["reachable"]["measured"]["listeners"],
                         [{"local": "127.0.0.1:17890", "holders": [4242],
                           "attributed": True}])
        self.assertEqual(doc["answers"]["who_owns_it"]["state"],
                         infra.OWNER_UNDECLARED)


# ---------------------------------------------------------------------------
# 4 · Automatic recovery, and the reason it is not permitted
# ---------------------------------------------------------------------------


class AutoRecoveryTests(unittest.TestCase):
    def test_recovery_is_not_permitted_for_a_resource_that_does_not_exist(self):
        """The third of the three questions §58 asks, and the one whose wrong
        answer is an action. `permitted: false` with no resource behind it is
        not a limitation: it is the statement that any automatic recovery here
        would be ServerOS starting something on behalf of an owner it does not
        know."""
        recovery = answer()["answers"]["auto_recovery_permitted"]
        self.assertFalse(recovery["permitted"])
        self.assertIsNone(recovery["model"])
        self.assertFalse(recovery["declared"])
        self.assertIn("no resource to recover", recovery["because"])
        self.assertIn("invent", recovery["because"])

    def test_a_declared_resource_reports_its_own_recovery_model(self):
        """The other direction, so the `false` above is a reading and not the
        constant this function always returns. Here the model is declared and
        `persistent`, and the answer comes from `recovery_report` — the one
        place that decides `automatic` for every resource in the runtime."""
        doc = answer(reg=a_registered_tunnel())
        recovery = doc["answers"]["auto_recovery_permitted"]
        self.assertEqual(recovery["model"], "restart")
        self.assertTrue(recovery["declared"])
        self.assertIn(recovery["protection"], ("normal", "protected"))

    def test_a_protected_registration_is_never_automatically_recovered(self):
        """`recovery_report` already refuses this, and this asserts the egress
        path inherits the refusal rather than re-deciding it. A second copy of
        the protection rule is how a protected resource ends up on an
        automatic path that the first copy forbids."""
        reg = a_registered_tunnel(protection={"level": "protected"},
                                  recovery={"model": "restart"})
        recovery = answer(reg=reg)["answers"]["auto_recovery_permitted"]
        self.assertFalse(recovery["permitted"])
        self.assertEqual(recovery["protection"], "protected")

    def test_the_action_uses_the_runtime_s_vocabulary(self):
        """`NONE · DEFER · NEEDS_HUMAN · RECONCILE`, the same four every other
        resource is answered with. A separate vocabulary for egress would be a
        second decision procedure, and the second one is the one that drifts."""
        declared_healthy = answer(reg=a_registered_tunnel(),
                                  host=host_with({17890: "9001"},
                                                 held_by={"9001": 4242}))
        self.assertEqual(declared_healthy["action"], "NONE")
        self.assertEqual(answer()["action"], "NEEDS_HUMAN")


# ---------------------------------------------------------------------------
# The live node, read only
# ---------------------------------------------------------------------------


class LiveNodeTests(unittest.TestCase):
    """Not an assertion about today's network state — a service coming back is
    not a test failure. What is asserted is the invariant that must hold in
    every one of those worlds: the report never names an owner it cannot
    evidence."""

    def test_the_live_node_answers_without_inventing_an_owner(self):
        live = Path("/opt/applications/data/home-hwz/state/serveros/runtime/"
                    "registry/resources.yaml")
        if not live.exists():
            self.skipTest(f"no live registry at {live}")
        reg = registry.Registry(path=live)
        doc = infra.egress_answer(reg, the_requirement())
        ids = {r.id for r in reg.all()}

        owner = doc["answers"]["who_owns_it"]
        if owner["state"] == infra.OWNER_DECLARED:
            self.assertIn(owner["ref"], {r.record.get("owner", {}).get("ref")
                                         for r in reg.all()})
        else:
            # An owner that was not declared has no ref, and the reason says
            # which of the two undeclared states it is in.
            self.assertIsNone(owner["ref"])
            self.assertTrue(owner["because"])
        for covering in doc["registrations"]:
            self.assertIn(covering["id"], ids,
                          "the report named a registration the registry does "
                          "not contain")
        self.assertEqual(sorted(doc["answers"]), sorted(infra.EGRESS_QUESTIONS))


class CliTests(unittest.TestCase):
    """The §58 answer has to be reachable by a person, or it is a function
    nobody calls — which this round has already found eight times and named."""

    LIVE = Path("/opt/applications/data/home-hwz/state/serveros/runtime/"
                "registry/resources.yaml")

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(RUNTIME / "cli.py"), "runtime", "egress",
             "--registry", str(self.LIVE), *args],
            capture_output=True, text=True, cwd=str(REPO))

    def setUp(self):
        if not self.LIVE.exists():
            self.skipTest(f"no live registry at {self.LIVE}")

    def test_the_command_answers_the_four_questions_against_the_live_node(self):
        done = self._run("--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        doc = json.loads(done.stdout)
        self.assertEqual(sorted(doc["answers"]), sorted(infra.EGRESS_QUESTIONS))
        self.assertEqual(doc["answers"]["reachable"]["endpoint"], PROXY_ENDPOINT)
        # Measured live, so only the shape is asserted — a tunnel coming back is
        # not a test failure.
        self.assertIn(doc["action"], ("NONE", "DEFER", "NEEDS_HUMAN", "RECONCILE"))

    def test_the_human_form_names_the_owner_state_and_the_action(self):
        done = self._run()
        self.assertEqual(done.returncode, 0, done.stderr)
        for expected in ("endpoint", "missing", "owner", "auto recovery", "action"):
            self.assertIn(expected, done.stdout)
        self.assertIn(".bashrc:", done.stdout,
                      "the human form does not say who declared the endpoint, "
                      "which is the evidence the whole answer rests on")


if __name__ == "__main__":
    unittest.main(verbosity=2)
