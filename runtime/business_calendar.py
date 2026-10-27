#!/usr/bin/env python3
"""Compatibility facade. The module itself is `runtime/reports/business_calendar.py`.

D3 moves the product layer out of `runtime/` into `reports/`. The move happens
in two stages for the same reason D2's does — a new TOP-LEVEL directory has to
be registered in `docs/architecture/layers.yaml` and in `scripts/build.py`'s
`CORE_MODULES`, and both are held by in-flight changes. Stage 1 puts the file in
`runtime/reports/`, which travels with `runtime/` (already a `CORE_MODULES`
entry, copied recursively), so no packaging list has to learn a new name.

WHY A FACADE HERE, WHEN THE AUDIENCE MODULE DELIBERATELY HAS NONE
-----------------------------------------------------------------
The audience rule (`fleet_hub/fleet_audience.py`, moved by D2) has no
forwarder, because a verdict is an HMAC over a per-import key and a second copy
would issue verdicts the readers refuse.
This module has no such state: it is constants and pure functions over a
declaration document, and two objects would be interchangeable.

So the facade is safe here, and it is what makes the move reversible: an
out-of-tree caller that knows the old path keeps working while the tree moves.

WHAT MAKES IT ONE MODULE AND NOT TWO
------------------------------------
Attribute access proxies to the single implementation object, which is
registered under the unchanged `serveros_runtime_business_calendar`. So
`facade.CalendarError is implementation.CalendarError` — the classes and
functions are the same objects on both routes, and an `except` written against
one route catches a raise from the other. Only the stub's own module object
differs, which is unobservable here: nothing in this tree compares this
module's identity (unlike the audience rule, where that comparison IS the
security property).

`__dir__` is provided so `from business_calendar import X` and introspection
see the implementation's names rather than this file's four.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

#: Unchanged on purpose. Both this facade and every in-tree caller register the
#: implementation under this name, so a process that reaches the module from
#: either direction holds one object.
CANONICAL_NAME = "serveros_runtime_business_calendar"

IMPLEMENTATION = Path(__file__).resolve().parent / "reports" / "business_calendar.py"


def _implementation():
    """The single implementation object, loading it if this facade got here first."""
    module = sys.modules.get(CANONICAL_NAME)
    current = Path(getattr(module, "__file__", "") or "").resolve()
    if module is not None and current == IMPLEMENTATION:
        return module
    if not IMPLEMENTATION.is_file():
        raise FileNotFoundError(
            f"{IMPLEMENTATION} is missing: the business calendar moved into "
            "runtime/reports/ and this facade is the only thing left at the "
            "old path. A checkout without it is not a version this file "
            "supports")
    spec = importlib.util.spec_from_file_location(CANONICAL_NAME, IMPLEMENTATION)
    module = importlib.util.module_from_spec(spec)
    sys.modules[CANONICAL_NAME] = module
    spec.loader.exec_module(module)
    return module


_IMPLEMENTATION = _implementation()

#: `__doc__` is the ONE attribute `__getattr__` cannot forward.
#:
#: A module's docstring lives in its own `__dict__`, and attribute fallback is
#: only consulted when normal lookup fails — so a facade that defines just
#: `__getattr__` answers every old reader with its OWN text and the
#: implementation's design sentences silently disappear. Nothing about the
#: forwarder looks wrong while that happens, which is why this is composed
#: rather than left to a comment.
#:
#: This is a superset and not a swap: whoever opens the old path needs both the
#: reason the file exists and, below it, the module it forwards to.
__doc__ = "\n\n".join(
    part for part in (__doc__, getattr(_IMPLEMENTATION, "__doc__", "")) if part)


def __getattr__(name: str):
    """Every public name of the implementation, including ones added later.

    A facade that copied names explicitly would go stale silently: a function
    added to the implementation would simply not exist at the old path, and the
    only reader that noticed would be the one still using it.
    """
    return getattr(_IMPLEMENTATION, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_IMPLEMENTATION)))
