"""The reachability audit's entry points still exist.

`scripts/reachability-roots.yaml` lists what runs on the devices. A root that
was renamed or deleted would silently shrink what the audit counts as live.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import reachability  # noqa: E402


def test_every_root_exists():
    groups = reachability.load_roots(ROOT / "scripts" / "reachability-roots.yaml")
    assert groups, "no roots"
    missing = [p for paths in groups.values() for p in paths if not (ROOT / p).exists()]
    assert missing == []


def test_prose_naming_a_file_is_not_an_edge(tmp_path):
    files = reachability.tracked()
    index = reachability.Index(files)
    probe = tmp_path / "probe.py"
    probe.write_text('MSG = "release.py promote reloads units"\nPATH = "runtime/cli.py"\n')
    edges = {p.relative_to(ROOT).as_posix() for p in reachability.edges_of_python(probe, index)}
    assert "runtime/cli.py" in edges
    assert "scripts/release.py" not in edges
