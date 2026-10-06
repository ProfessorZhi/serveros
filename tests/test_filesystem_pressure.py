"""Filesystem pressure uses allocatable bytes and the node's bound data root."""
from types import SimpleNamespace

import pytest

from runtime import inventory


class Host:
    def __init__(self, total=100, used=84, free=11, home_mount="/data"):
        self.usage = SimpleNamespace(total=total, used=used, free=free)
        self.home_mount = home_mount

    def exists(self, path):
        return True

    def statvfs(self, path):
        return self.usage

    def inode_usage(self, path):
        return {"used_percent": 10}

    def read(self, argv, timeout):
        if argv[2] == "TARGET":
            return 0, self.home_mount + "\n", ""
        return 0, "/dev/root ext4 /\n", ""


def test_reserved_blocks_do_not_hide_pressure_and_home_location_is_measured():
    mount = inventory.Inventory(Host())._mount("/", "ROOT_FS")
    assert mount["used_percent"] == 88.4
    assert mount["used_percent"] > 85
    assert mount["total_bytes"] == 100 and mount["free_bytes"] == 11
    assert "note" not in mount
    same_disk = inventory.Inventory(Host(home_mount="/"))._mount("/", "ROOT_FS")
    assert "HOME" in same_disk["note"]


@pytest.mark.parametrize("total,used,free", [(100, -900, 1000), (0, 0, 0), (100, 90, -1)])
def test_inconsistent_quota_or_filesystem_counters_cannot_be_graded_healthy(total, used, free):
    row = inventory.Inventory(Host(total, used, free))._mount("/data", "DATA_FS")
    assert row["state"] == inventory.UNAVAILABLE
    assert "used_percent" not in row


def test_data_role_uses_bound_node_root(monkeypatch):
    inv = inventory.Inventory(Host())
    monkeypatch.setattr(inv, "_root", lambda name: {"data": "/data/home/example", "state": "/data/state", "artifacts": "/data/artifacts"}[name])
    monkeypatch.setattr(inv, "_docker_root_dir", lambda: None)
    roles = {row["role"]: row for row in inv._resolved_roles()}
    assert roles["DATA_FS"]["path"] == "/data/home/example"
    assert "/opt/applications" not in [row["path"] for row in roles.values()]
