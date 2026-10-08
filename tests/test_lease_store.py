"""SYS-02's durable half: `state/serveros/resource-leases/`.

Hermetic. Every namespace is built under `tmp_path`, and nothing here writes
into this node's live `state/serveros/resource-leases/` — which matters more
than usual, because the module under test is the producer of that namespace.

Three things are asserted here that are about the record and not the verdict:

  * a writer and a reader of one record must resolve the namespace the SAME
    way. They did not, until a daemon-socket comparison caught it, and the
    first test below is that comparison written down;
  * `serveros.db#leases` and this namespace are different facts, so this one
    must never write that file;
  * a crash leaves evidence, and evidence is not garbage to be collected.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
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


environment = _load("serveros_runtime_environment", "environment.py")
lease_store = _load("serveros_runtime_lease_store", "lease_store.py")
paths_mod = _load("serveros_runtime_paths", "paths.py")

NOW = 1_800_000_000.0


def a_store(tmp_path, *, now=NOW):
    """A store over `tmp_path/resource-leases`, addressed the way the CLI does."""
    return lease_store.open_store(tmp_path / "runtime", now=lambda: now)


# ---------------------------------------------------------------------------
# where the namespace is
# ---------------------------------------------------------------------------

class TestNamespaceResolution:
    def test_the_runtime_state_dir_implies_the_sibling_namespace(self, tmp_path):
        """The daemon's `--state-dir` and the CLI's are one value now.

        `default_state_dir()` composes `$SERVEROS_STATE_DIR + /runtime`, and
        `daemon._item_roots` reads `serveros.db` at `<state_dir>/../serveros.db`.
        Both say the runtime directory's parent is the state root. A CLI flag
        that meant the namespace root instead made `lease declare --state-dir X`
        and `lease list --state-dir X` address two directories, both reporting
        success.
        """
        runtime = tmp_path / "state/serveros/runtime"
        assert lease_store.namespace_root(runtime) == \
            tmp_path / "state/serveros/resource-leases"
        assert lease_store.open_store(runtime).root == \
            tmp_path / "state/serveros/resource-leases"

    def test_no_argument_means_the_root_contract(self, tmp_path):
        """And on a configured node that is the same directory as the sibling
        derivation — the two agree because they are one fact from the same two
        inputs, not because somebody keeps them in step."""
        paths = paths_mod.Paths()
        if not paths.bound:
            pytest.skip("this node's roots are unbound")
        assert lease_store.namespace_root() == lease_store.default_root()
        assert lease_store.namespace_root() == \
            Path(paths.root("state")) / "resource-leases"

    def test_an_isolated_state_dir_does_not_reach_the_live_namespace(self, tmp_path):
        """The divergence is the point: a daemon pointed at a directory of its
        own must answer about THAT directory."""
        assert lease_store.namespace_root(tmp_path / "runtime") != \
            lease_store.default_root()

    def test_an_unbound_root_composes_nothing(self, monkeypatch):
        """An unbound root is a template string. Writing into one would create a
        directory that looks like a name and is not a path."""

        class Unbound:
            bound = False
            unconfigured = ("data_root",)

            def resolve(self, ref, *, unbound_ok=False):
                raise AssertionError("must not be reached")

        monkeypatch.setattr(lease_store.paths_mod, "Paths", Unbound)
        with pytest.raises(lease_store.LeaseError):
            lease_store.default_root()

    def test_the_namespace_ref_is_the_contracts(self):
        contract = (REPO / "fleet/state-root-contract.yaml")
        assert lease_store.NAMESPACE_REF == "state/resource-leases"
        text = contract.read_text(encoding="utf-8")
        assert "resource-leases:" in text
        # The contract names a producer for this namespace. Before this module
        # there was one, and `runtime/` held nothing that answered to it.
        assert "producer: runtime/" in text
        # Built from a REF and never joined by hand: the module holds one
        # string, and `paths.py` is the only thing that turns it into a path.
        assert str(lease_store.default_root()).endswith("/resource-leases")


class TestOneResourceOneFile:
    def test_a_resource_name_is_escaped_into_one_injective_filename(self):
        root = Path("/nonexistent")
        assert lease_store.path_for(root, "browser:chromium").name == \
            "browser%3Achromium.json"

    def test_two_resources_never_collide(self):
        """Injective, including for a name that already looks escaped."""
        root = Path("/nonexistent")
        resources = ["browser:chromium", "browser/chromium", "browser%3Achromium",
                     "browser:chromium2"]
        names = {lease_store.path_for(root, r).name for r in resources}
        assert len(names) == len(resources)

    def test_a_name_cannot_escape_the_namespace(self):
        path = lease_store.path_for(Path("/nonexistent"), "../../etc/passwd")
        assert "/" not in path.name
        assert path.parent == Path("/nonexistent")

    @pytest.mark.parametrize("resource", ["", None, " padded ", "padded\t"])
    def test_a_name_that_would_make_a_second_file_is_refused(self, resource):
        with pytest.raises(environment.Rejected):
            lease_store.path_for(Path("/nonexistent"), resource)

    def test_a_reader_never_has_to_unescape_the_filename(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("browser:chromium", scope="device-persistent",
                      holder="conv-a", sharing="shared")
        record = store.records()[0]
        assert record["resource"] == "browser:chromium"


# ---------------------------------------------------------------------------
# declaring and releasing
# ---------------------------------------------------------------------------

class TestDeclare:
    def test_a_declaration_lands_in_the_namespace(self, tmp_path):
        store = a_store(tmp_path)
        result = store.declare("port:9100", scope="work-item",
                               holder="SRV-0011/RUN-1", sharing="partitioned")
        assert result["act"] == "DECLARE"
        assert (tmp_path / "resource-leases/port%3A9100.json").is_file()
        record = store.get("port:9100")
        assert record["scope"] == "work-item"
        assert record["sharing"] == "partitioned"
        assert [row["holder"] for row in record["references"]] == ["SRV-0011/RUN-1"]

    def test_a_second_declaration_by_one_holder_refreshes(self, tmp_path):
        """The property a retried request depends on. A declare that added a
        reference per attempt would inflate the count until a shared resource
        looked like it had eleven owners."""
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="SRV-0011",
                      sharing="partitioned", ttl_s=10)
        second = store.declare("port:9100", scope="work-item", holder="SRV-0011",
                               sharing="partitioned", ttl_s=600)
        assert second["act"] == "REFRESH"
        assert len(second["record"]["references"]) == 1

    def test_two_holders_are_two_references(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="A", sharing="partitioned")
        store.declare("port:9100", scope="work-item", holder="B", sharing="partitioned")
        assert len(store.get("port:9100")["references"]) == 2

    def test_sharing_is_the_resources_and_not_the_holders(self, tmp_path):
        """A caller declaring the browser `exclusive` because its own case
        wants exclusivity would make every other holder's `shared` false."""
        store = a_store(tmp_path)
        store.declare("browser:chromium", scope="device-persistent",
                      holder="A", sharing="shared")
        with pytest.raises(environment.Rejected):
            store.declare("browser:chromium", scope="device-persistent",
                          holder="B", sharing="exclusive")

    def test_scope_is_the_resources_too(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="A", sharing="partitioned")
        with pytest.raises(environment.Rejected):
            store.declare("port:9100", scope="session", holder="A",
                          sharing="partitioned")

    def test_a_holder_is_required(self, tmp_path):
        store = a_store(tmp_path)
        with pytest.raises(environment.Rejected):
            store.declare("port:9100", scope="work-item", holder="",
                          sharing="partitioned")

    def test_a_refused_declaration_writes_nothing(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("browser:chromium", scope="device-persistent",
                      holder="A", sharing="shared")
        before = (tmp_path / "resource-leases/browser%3Achromium.json").read_bytes()
        with pytest.raises(environment.Rejected):
            store.declare("browser:chromium", scope="device-persistent",
                          holder="B", sharing="exclusive")
        assert (tmp_path / "resource-leases/browser%3Achromium.json").read_bytes() \
            == before

    def test_the_transition_record_is_bounded(self, tmp_path):
        """This is authority for CURRENT occupancy, not an audit log."""
        store = a_store(tmp_path)
        for _ in range(lease_store.HISTORY_LIMIT + 5):
            store.declare("port:9100", scope="work-item", holder="A",
                          sharing="partitioned")
        record = store.get("port:9100")
        assert len(record["history"]) == lease_store.HISTORY_LIMIT
        assert record["history"][-1]["act"] == "REFRESH"

    def test_no_temporary_file_survives_a_write(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="A",
                      sharing="partitioned")
        leftovers = [p.name for p in (tmp_path / "resource-leases").iterdir()
                     if not p.name.endswith(".json")]
        assert leftovers == []


class TestRelease:
    def test_the_last_reference_exits_and_the_record_goes(self, tmp_path):
        """Counterexample 1, first half. `absent_means: NO_LEASES_HELD` is the
        claim this removal is what makes TRUE."""
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="A", sharing="partitioned")
        result = store.release("port:9100", scope="work-item", holder="A",
                               protection=False)
        assert result["removed"] is True
        assert result["verdict"]["verdict"] == environment.ALLOW
        assert result["attachment_state"] == environment.UNATTACHED
        assert store.records() == []
        assert not (tmp_path / "resource-leases/port%3A9100.json").exists()

    def test_the_last_reference_leaves_a_record_of_what_happened(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="A", sharing="exclusive")
        store.declare("port:9100", scope="work-item", holder="B", sharing="exclusive")
        first = store.release("port:9100", scope="work-item", holder="A",
                              protection=False)
        assert first["removed"] is False
        assert first["attachment_state"] == environment.HELD
        second = store.release("port:9100", scope="work-item", holder="B",
                               protection=False)
        assert second["removed"] is True

    def test_a_shared_resource_is_kept_when_the_last_reference_exits(self, tmp_path):
        """Counterexample 4, first half, at the record level: nobody wants it
        and it is still running. Deleting the file would hide that from the
        next reader."""
        store = a_store(tmp_path)
        store.declare("browser:chromium", scope="device-persistent",
                      holder="A", sharing="shared")
        result = store.release("browser:chromium", scope="device-persistent",
                               holder="A", protection=False)
        assert result["verdict"]["verdict"] == environment.RETAIN
        assert result["removed"] is False
        kept = store.get("browser:chromium")
        assert kept["references"] == []
        assert kept["state"] == environment.RELEASABLE
        assert kept["release_verdict"]["verdict"] == environment.RETAIN
        assert [row["resource"] for row in store.records()] == ["browser:chromium"]

    def test_a_device_persistent_resource_is_kept(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("venv:playwright", scope="device-persistent",
                      holder="A", sharing="exclusive")
        result = store.release("venv:playwright", scope="device-persistent",
                               holder="A", protection=False)
        assert result["verdict"]["verdict"] == environment.RETAIN

    def test_an_external_owner_is_refused_and_the_record_stays(self, tmp_path):
        """Counterexample 3, ownership half."""
        store = a_store(tmp_path)
        store.declare("hosting_docker:MUC-0101", scope="project",
                      holder="SRV-0011", sharing="exclusive")
        result = store.release("hosting_docker:MUC-0101", scope="project",
                               holder="SRV-0011", owner_class="EXTERNAL")
        assert result["verdict"]["code"] == "OWNER_EXTERNAL"
        assert result["removed"] is False
        assert result["verdict"]["owner_class_source"] == "caller"

    def test_a_protected_resource_is_refused_and_the_record_stays(self, tmp_path):
        """Counterexample 3, protection half."""
        store = a_store(tmp_path)
        store.declare("profile:live", scope="project", holder="SRV-0011",
                      sharing="exclusive")
        result = store.release("profile:live", scope="project", holder="SRV-0011",
                               protection=True)
        assert result["verdict"]["code"] == "PROTECTED_SUBJECT"
        assert result["removed"] is False
        assert result["verdict"]["protection"] == {"protected": True, "declared": True}

    def test_releasing_twice_is_a_finding_and_not_a_fault(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="A", sharing="partitioned")
        store.release("port:9100", scope="work-item", holder="A", protection=False)
        again = store.release("port:9100", scope="work-item", holder="A",
                              protection=False)
        assert again["reason"] == "NO_SUCH_RESOURCE"
        assert again["released"] is False
        assert again["known_holders"] == []

    def test_a_retried_release_of_a_kept_record_is_not_a_missing_reference(self, tmp_path):
        """`ALREADY_RETIRED` is what makes a retried release safe to repeat:
        otherwise a retry is indistinguishable from a typo."""
        store = a_store(tmp_path)
        store.declare("browser:chromium", scope="device-persistent",
                      holder="A", sharing="shared")
        store.release("browser:chromium", scope="device-persistent", holder="A",
                      protection=False)
        again = store.release("browser:chromium", scope="device-persistent",
                              holder="A", protection=False)
        assert again["reason"] == "ALREADY_RETIRED"
        assert again["retired_at"]

    def test_an_absent_holder_is_told_who_does_hold_it(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="A", sharing="partitioned")
        result = store.release("port:9100", scope="work-item", holder="typo",
                               protection=False)
        assert result["reason"] == "NO_SUCH_REFERENCE"
        assert result["known_holders"] == ["work-item:A"]

    def test_a_release_is_never_a_stop(self, tmp_path):
        """It decides and records. `daemon.reconcile` is the only path in this
        product that stops anything, and a second actuator here is what the
        `new-lease-system` non-goal was written about."""
        source = (RUNTIME / "lease_store.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "shutil", "os.kill", "SIGTERM", "rmtree"):
            assert forbidden not in source, forbidden
        assert "reconcile" not in source


class TestRestore:
    """SYS-02's fourth verb, spelled as re-declaration.

    A reference is keyed by `(scope, holder)`. Coming back is therefore not a
    distinct operation with its own state to get wrong: it is the same declare,
    and the transition record is what distinguishes "still here" from "here
    again". This class is the receipt for that claim, so the acceptance row can
    say `restore` is reachable rather than that it is absent.
    """

    def test_a_holder_that_comes_back_holds_it_again(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="SRV-0011",
                      sharing="partitioned")
        store.release("port:9100", scope="work-item", holder="SRV-0011",
                      protection=False)
        assert store.get("port:9100") is None

        again = store.declare("port:9100", scope="work-item", holder="SRV-0011",
                              sharing="partitioned")
        assert again["act"] == "DECLARE"
        assert len(again["record"]["references"]) == 1

    def test_a_fully_resolved_removal_takes_its_history_with_it(self, tmp_path):
        """The boundary of `restore`, written down rather than discovered.

        The record was removed because nothing was left to resolve, which is
        what makes `absent_means: NO_LEASES_HELD` true. The transition record
        went with it, so a holder that comes back gets a fresh record with no
        memory of its earlier claim — and there is nothing left for that memory
        to be about, because the resource had no outstanding state.

        A record is bounded authority for CURRENT occupancy and not an audit
        log (`HISTORY_LIMIT` says the same thing from the other side). What
        keeps the promise honest is that the case is stated: a caller that
        needs "this holder held it last week" is asking a different question,
        and `state/resource-leases/` is not where it is answered.
        """
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="SRV-0011",
                      sharing="partitioned")
        store.release("port:9100", scope="work-item", holder="SRV-0011",
                      protection=False)
        again = store.declare("port:9100", scope="work-item", holder="SRV-0011",
                              sharing="partitioned")
        assert [row["act"] for row in again["record"]["history"]] == ["DECLARE"]

    def test_a_retained_record_remembers_that_the_holder_came_back(self, tmp_path):
        """Where the record stays, "here again" is distinguishable from "never
        left" by reading it rather than by remembering it."""
        store = a_store(tmp_path)
        store.declare("browser:chromium", scope="device-persistent",
                      holder="conv-a", sharing="shared")
        store.release("browser:chromium", scope="device-persistent",
                      holder="conv-a", protection=False)
        again = store.declare("browser:chromium", scope="device-persistent",
                              holder="conv-a", sharing="shared")
        assert [row["holder"] for row in again["record"]["references"]] == ["conv-a"]
        assert [row["act"] for row in again["record"]["history"]] == \
            ["DECLARE", "RELEASE", "DECLARE"]
        assert [row["verdict"] for row in again["record"]["history"]] == \
            [None, environment.RETAIN, None]

    def test_restoring_is_not_a_second_reference_for_the_same_holder(self, tmp_path):
        """The idempotency that makes a retried request safe also makes a
        restore safe to attempt twice."""
        store = a_store(tmp_path)
        for _ in range(3):
            store.declare("port:9100", scope="work-item", holder="SRV-0011",
                          sharing="partitioned")
        assert len(store.get("port:9100")["references"]) == 1

    def test_a_restored_resource_is_held_again_rather_than_releasable(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="A", sharing="partitioned")
        store.release("port:9100", scope="work-item", holder="A", protection=False)
        store.declare("port:9100", scope="work-item", holder="A", sharing="partitioned")

        record = store.get("port:9100")
        rows = [environment._reference_row(r, now=NOW) for r in record["references"]]
        assert environment.attachment_state(rows, attached=None) == environment.HELD


class TestTheCrashCase:
    """Counterexample 2: a holder that stopped backing its claim."""

    def test_the_claim_stays_visible_without_anybody_acting(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("lock:build", scope="work-item", holder="crashed",
                      sharing="exclusive", ttl_s=60)

        later = lease_store.LeaseStore(
            tmp_path / "resource-leases", now=lambda: NOW + 3600)
        record = later.records()[0]
        assert record["references"][0]["holder"] == "crashed"
        assert environment.reference_state(record["references"][0],
                                           now=NOW + 3600) == environment.EXPIRED
        assert (tmp_path / "resource-leases/lock%3Abuild.json").is_file()

    def test_an_expired_reference_is_not_counted_as_held(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("lock:build", scope="work-item", holder="crashed",
                      sharing="exclusive", ttl_s=60)
        later = lease_store.LeaseStore(
            tmp_path / "resource-leases", now=lambda: NOW + 3600)

        desired = {record["resource"]: {"scope": record["scope"],
                                        "sharing": record["sharing"],
                                        "references": record["references"]}
                   for record in later.records()}
        out = environment.effective(desired=desired, observed={}, ownership={},
                                    policy={}, context={}, now=NOW + 3600)
        assert out["counts"]["held"] == 0
        assert out["counts"]["orphaned"] == 1
        assert out["resources"][0]["attachment_state"] == environment.RELEASABLE

    def test_resolving_it_is_an_act_and_is_recorded(self, tmp_path):
        """Retiring the crashed holder's reference is the resolution — and the
        transition record keeps it, so "who released this" is answerable."""
        store = a_store(tmp_path)
        store.declare("lock:build", scope="work-item", holder="crashed",
                      sharing="exclusive", ttl_s=60)
        later = lease_store.LeaseStore(
            tmp_path / "resource-leases", now=lambda: NOW + 3600)
        result = later.release("lock:build", scope="work-item", holder="crashed",
                               protection=False)
        assert result["removed"] is True
        assert result["verdict"]["verdict"] == environment.ALLOW


# ---------------------------------------------------------------------------
# the record itself
# ---------------------------------------------------------------------------

class TestTheRecord:
    def test_an_absent_namespace_is_empty_and_not_an_error(self, tmp_path):
        store = lease_store.LeaseStore(tmp_path / "nothing-here")
        assert store.records() == []
        assert store.get("anything") is None

    def test_a_corrupt_record_is_refused_by_name(self, tmp_path):
        """Returning the rest of the list without it would report the node as
        having one fewer thing in use than it has."""
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="A", sharing="partitioned")
        (tmp_path / "resource-leases/broken.json").write_text("{not json",
                                                             encoding="utf-8")
        with pytest.raises(lease_store.LeaseError) as caught:
            store.records()
        assert "broken.json" in str(caught.value)

    def test_a_file_that_is_not_a_record_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        namespace = tmp_path / "resource-leases"
        namespace.mkdir(parents=True)
        (namespace / "other.json").write_text(
            json.dumps({"schema": "something/else"}), encoding="utf-8")
        with pytest.raises(lease_store.LeaseError):
            store.records()

    def test_the_module_never_writes_work_engines_lease_table(self, tmp_path):
        """`serveros.db#leases` holds exclusion claims, read as `protected` by
        `context/resolver.py`. This namespace holds desired references. Two
        different facts: promoting a `leases` row into a reference here would be
        observation silently becoming desire.

        The prose may name the table — the docstring explains the difference on
        purpose. What must not happen is a connection to it, so the check is
        for the driver and then for the bytes.
        """
        source = (RUNTIME / "lease_store.py").read_text(encoding="utf-8")
        assert "sqlite3" not in source

        sentinel = tmp_path / "serveros.db"
        sentinel.write_bytes(b"not-a-database-this-producer-touches")
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="A", sharing="partitioned")
        store.release("port:9100", scope="work-item", holder="A", protection=False)
        assert sentinel.read_bytes() == b"not-a-database-this-producer-touches"

    def test_the_record_is_readable_json_with_the_declared_schema(self, tmp_path):
        store = a_store(tmp_path)
        store.declare("port:9100", scope="work-item", holder="A", sharing="partitioned")
        raw = json.loads((tmp_path / "resource-leases/port%3A9100.json")
                         .read_text(encoding="utf-8"))
        assert raw["schema"] == lease_store.SCHEMA == "serveros/resource-lease.v1"
        assert set(raw) >= {"resource", "scope", "sharing", "references", "history"}


# ---------------------------------------------------------------------------
# one namespace, one answer, whoever asks
# ---------------------------------------------------------------------------

class TestOneAnswerLayer:
    def test_the_cli_and_the_daemon_resolve_the_same_namespace(self, tmp_path):
        """The regression the daemon socket test caught, without a daemon.

        `view` is the shared answer layer and the CLI's write verbs build their
        store from the same function. These must be one directory.
        """
        view_mod = _load("serveros_runtime_view", "view.py")
        runtime = tmp_path / "state/serveros/runtime"
        assert view_mod._lease_store(runtime).root == lease_store.open_store(runtime).root

    def test_a_declaration_is_visible_to_the_reader_layer(self, tmp_path):
        view_mod = _load("serveros_runtime_view", "view.py")
        runtime = tmp_path / "state/serveros/runtime"
        lease_store.open_store(runtime, now=lambda: NOW).declare(
            "port:9100", scope="work-item", holder="A", sharing="partitioned")

        payload = view_mod.environment_leases(state_dir=runtime, answered_by="test")
        assert payload["recorded"] == 1
        assert payload["root"] == str(tmp_path / "state/serveros/resource-leases")
        assert payload["absent_means"] == "NO_LEASES_HELD"
        assert [row["resource"] for row in payload["leases"]] == ["port:9100"]

    def test_an_empty_namespace_answers_no_leases_held(self, tmp_path):
        view_mod = _load("serveros_runtime_view", "view.py")
        payload = view_mod.environment_leases(state_dir=tmp_path / "runtime",
                                             answered_by="test")
        assert payload["recorded"] == 0
        assert payload["leases"] == []


class TestThroughTheCli:
    """The two verbs a person actually runs, in one namespace, in order."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(REPO / "bin/serverosctl"), *args],
            capture_output=True, text=True, cwd=str(REPO))

    def test_declare_then_list_addresses_one_directory(self, tmp_path):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        declared = self._run(
            "lease", "declare", "port:9100", "--state-dir", str(runtime),
            "--scope", "work-item", "--holder", "SRV-0011/RUN-1",
            "--sharing", "partitioned", "--json")
        assert declared.returncode == 0, declared.stderr
        assert json.loads(declared.stdout)["act"] == "DECLARE"

        listed = self._run("lease", "list", "--state-dir", str(runtime), "--json")
        assert listed.returncode == 0, listed.stderr
        payload = json.loads(listed.stdout)
        assert payload["recorded"] == 1
        assert [row["resource"] for row in payload["leases"]] == ["port:9100"]
        assert payload["root"] == str(tmp_path / "resource-leases")

    def test_a_refused_release_exits_nonzero_and_says_why(self, tmp_path):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        self._run("lease", "declare", "profile:live", "--state-dir", str(runtime),
                  "--scope", "project", "--holder", "SRV-0011",
                  "--sharing", "exclusive", "--json")
        released = self._run(
            "lease", "release", "profile:live", "--state-dir", str(runtime),
            "--scope", "project", "--holder", "SRV-0011", "--protected", "--json")
        assert released.returncode == 0, released.stderr
        payload = json.loads(released.stdout)
        assert payload["released"] is True
        assert payload["removed"] is False
        assert payload["verdict"]["verdict"] == environment.DENY

    def test_an_invented_sharing_mode_is_refused_without_writing(self, tmp_path):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        result = self._run("lease", "declare", "port:9100", "--state-dir",
                           str(runtime), "--scope", "work-item", "--holder", "A",
                           "--sharing", "pooled", "--json")
        assert result.returncode == 1
        assert json.loads(result.stdout)["refused"] == "Rejected"
        assert not (tmp_path / "resource-leases").exists()
