"""The import-isolation helper restores identity, not just a name.

The sequence this protects is in `test_runner_receipt.py` and
`test_finalize_entry.py`: a T10 session fixture loads a pinned `workengine`
archive into `sys.modules`, and a later module's names were bound to the
in-tree classes at collection time. Re-importing would not help — the classes
would still be different objects — so the helper must put the captured module
objects back. This test states that property directly, without the heavy
archive fixture.
"""
from __future__ import annotations

import sys
from types import ModuleType

import v1_import_isolation

PREFIX = "x1_import_isolation_fixture"


def _module(name: str) -> ModuleType:
    module = ModuleType(name)
    module.__file__ = f"/fixture/{name}.py"
    return module


def test_restore_reinstates_the_same_objects_and_drops_a_foreign_root(tmp_path):
    original = {
        PREFIX: _module(PREFIX),
        f"{PREFIX}.authority": _module(f"{PREFIX}.authority"),
    }
    original_path = list(sys.path)
    sys.modules.update(original)
    try:
        captured = v1_import_isolation.snapshot(PREFIX)

        foreign = tmp_path / "foreign"
        (foreign / PREFIX).mkdir(parents=True)
        (foreign / PREFIX / "__init__.py").write_text("", encoding="utf-8")
        sys.path.insert(0, str(foreign))
        sys.modules[PREFIX] = _module(PREFIX)
        sys.modules[f"{PREFIX}.authority"] = _module(f"{PREFIX}.authority")

        v1_import_isolation.restore(captured, PREFIX)

        assert sys.modules[PREFIX] is original[PREFIX]
        assert sys.modules[f"{PREFIX}.authority"] is original[f"{PREFIX}.authority"]
        assert str(foreign) not in sys.path
    finally:
        for name in list(sys.modules):
            if name == PREFIX or name.startswith(PREFIX + "."):
                del sys.modules[name]
        sys.path[:] = original_path
