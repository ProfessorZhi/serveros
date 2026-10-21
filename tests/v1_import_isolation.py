"""Put a package's import-time objects back after a test swapped them.

WHY THIS EXISTS
---------------
Two test families legitimately need two different copies of `workengine`:

  * the T10 suites run against a **pinned archive** (`git archive <WE_HEAD>`),
    because the compatibility claim is about that exact source; and
  * the finalize/receipt suites run against the **in-tree** `workengine`,
    because the entry route they test is the one the tree ships.

The archive fixtures load their copy by deleting every `workengine*` entry from
`sys.modules` and importing from a temporary `sys.path` entry. That is a
process-wide mutation, and nothing puts the original objects back. A later
module's names were bound at **collection** time to the in-tree classes, while
the store it calls resolves its lazy `from .authority import ReceiptError`
against the archive copy now in `sys.modules` — one store, two `ReceiptError`
classes, and `assertRaises` sees an exception that is not the class it holds.

Re-importing is not the fix: a fresh import creates *new* class objects, so the
mismatch survives. The fix is to restore the **same module objects** that were
present when the consumer module was imported, and to remove only the extra
package roots a fixture added to `sys.path`. That is what this helper does.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType


def _names(prefix: str) -> list[str]:
    return [name for name in sys.modules
            if name == prefix or name.startswith(prefix + ".")]


def _is_package_root(path: str, prefix: str) -> bool:
    """True when `path` is a directory from which `prefix` would import."""
    try:
        return (Path(path) / prefix / "__init__.py").is_file()
    except OSError:
        return False


def snapshot(prefix: str = "workengine") -> tuple[dict[str, ModuleType],
                                                  tuple[str, ...]]:
    """Capture the package modules and the path list as they are now.

    Call this at module import time, before any fixture has a chance to swap
    the package. The captured objects are the ones the importing module's names
    are bound to.
    """
    return ({name: sys.modules[name] for name in _names(prefix)},
            tuple(sys.path))


def restore(snapshot_value: tuple[dict[str, ModuleType], tuple[str, ...]],
            prefix: str = "workengine") -> None:
    """Re-insert the captured modules and drop foreign package roots.

    Modules loaded from a different copy are removed; captured modules are put
    back by identity. Unrelated paths added after the snapshot are left alone —
    only a path that would itself serve `prefix` is removed, so a fixture's
    temporary archive root cannot shadow the in-tree package later.
    """
    modules, original_path = snapshot_value
    for name in _names(prefix):
        if name not in modules:
            del sys.modules[name]
    sys.modules.update(modules)

    seen = set()
    for path in tuple(sys.path):
        if path in original_path or path in seen:
            continue
        seen.add(path)
        if _is_package_root(path, prefix):
            sys.path.remove(path)


def state(prefix: str = "workengine") -> dict:
    """The observable state, for a regression test or a diagnostic print."""
    return {
        "modules": {name: getattr(sys.modules[name], "__file__", None)
                    for name in _names(prefix)},
        "paths": [path for path in sys.path if _is_package_root(path, prefix)],
        "classes": {name: id(sys.modules[name].__dict__[symbol])
                    for name, symbol in (
                        ("workengine.authority", "ReceiptError"),
                        ("workengine.store", "ServerOSStore"),
                    )
                    if name in sys.modules and symbol in sys.modules[name].__dict__},
    }
