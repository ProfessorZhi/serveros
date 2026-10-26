"""Required Linux proof using real /tmp <-> /dev/shm cross-device moves.

Windows cannot provide this Linux mount contract. On Linux, missing mounts,
permissions or distinct devices FAIL the proof gate; no capability skip or
mocked EXDEV/fsync/locking stands in for actual migration behavior.
"""
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import sys
import tempfile

import pytest

if sys.platform != "linux":
    pytest.skip("Linux /tmp and /dev/shm proof runs in the required hosted gate", allow_module_level=True)

from workengine.migration import MigrationManager
from workengine.model import WorkItem
from workengine.store import ServerOSStore
from test_owner_work_store_fence import _enroll_fixture


@pytest.fixture
def cross_device_world(tmp_path):
    roots = (Path("/tmp"), Path("/dev/shm"))
    assert all(root.is_dir() and os.access(root, os.W_OK | os.X_OK) for root in roots), \
        "REQUIRED_EXDEV_PROOF_UNAVAILABLE: writable /tmp and /dev/shm are required"
    assert roots[0].stat().st_dev != roots[1].stat().st_dev, \
        "REQUIRED_EXDEV_PROOF_UNAVAILABLE: /tmp and /dev/shm must be distinct devices"
    with ExitStack() as cleanup:
        left = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix="owner-exdev-source-", dir=roots[0])))
        right = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix="owner-exdev-target-", dir=roots[1])))
        store = ServerOSStore(tmp_path / "separate-control-state")
        assert not store.state_dir.is_relative_to(left) and not store.state_dir.is_relative_to(right)
        store.register_project("demo", "DEM")
        store.create_item(WorkItem("DEM-0001", "demo", "cross-device fixture", "feature", "fixture"))
        source, target = left / "source", right / "target"
        assert source.parent.stat().st_dev != target.parent.stat().st_dev
        source.write_bytes(b"verified source content\n")
        manager = MigrationManager(store)
        manager.plan({"project":"demo", "operations":[{"type":"move", "source":str(source), "target":str(target)}]})
        yield manager, store, source, target


@pytest.mark.parametrize("operation", ["apply", "compensate"])
@pytest.mark.parametrize("temporary_state", ["unknown_partial", "verified_copy"])
def test_real_exdev_resume_preserves_unknown_copies_and_recovers_verified_ones(cross_device_world, operation, temporary_state):
    manager, store, source, target = cross_device_world
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if operation == "compensate":
        assert manager.apply("demo")["status"] == "COMPLETED"
        assert not source.exists() and target.read_bytes() == content
        destination, origin = source, target
    else:
        destination, origin = target, source
    assert origin.exists() and not destination.exists()
    temporary = destination.with_name(f".{destination.name}.serveros-migrate-{digest[:12]}.tmp")
    temporary.write_bytes(b"unknown interrupted copy" if temporary_state == "unknown_partial" else content)
    active = "APPLYING" if operation == "apply" else "COMPENSATING"
    terminal = "COMPLETED" if operation == "apply" else "COMPENSATED"
    if temporary_state == "unknown_partial":
        before = temporary.read_bytes()
        with pytest.raises(ValueError, match="ambiguous partial copy"):
            getattr(manager, operation)("demo")
        assert temporary.read_bytes() == before, "unknown protocol bytes must survive refusal"
        assert origin.read_bytes() == content and not destination.exists()
        assert manager.inspect("demo")["status"] == active
        with pytest.raises(Exception, match="OWNER_MIGRATION_ACTIVE"):
            _enroll_fixture(store, "DEM-0001")
        # Explicit fixture reconciliation, not an automatic production rewrite.
        temporary.write_bytes(content)
    assert getattr(manager, operation)("demo")["status"] == terminal
    assert destination.read_bytes() == content and not origin.exists()
    assert not temporary.exists()
    _enroll_fixture(store, "DEM-0001")
