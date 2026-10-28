"""A named bridge from #490's observation protocol to #493's source seam.

The monitor's README claims #490 can replace its source *unchanged*:
"`core.observe_watch` only needs those two method names and a mapping". The
methods exist, but the signatures do not agree:

*   #493 calls ``observe_run(..., expected_boot_id=, expected_source_sha=)``
    (``fleet_next_agent_monitor/sources.py:494-504``);
*   #490 accepts ``source_sha`` and has no watch-level boot parameter
    (``fleet_next_monitor_source_adapter/adapter.py:272-275``);
*   #490 returns an ``Observation`` dataclass, not the mapping #493's core
    reads; its ``as_dict()`` is the mapping.

This module is the smallest honest adapter: rename one keyword, drop the one
#490 cannot honour, and call ``as_dict()``. It is QA scaffolding, not a fix to
either PR, and every test that uses it says so. ``DIRECT_SUBSTITUTION`` records
the failure the test asserts when no bridge is used.
"""
from __future__ import annotations

#: The exact failure observed when #490's adapter is handed to #493's
#: ``observe_watch`` without this bridge.
DIRECT_SUBSTITUTION = (
    "TypeError: LegacyReadOnlySourceAdapter.observe_run() got an unexpected "
    "keyword argument 'expected_boot_id'"
)

#: #490 has no watch-level boot binding. It compares the answer's bootId with
#: the *client's* declared ``boot_id``; a caller cannot pass an expectation.
BOOT_BINDING = "client.boot_id (no expected_boot_id parameter)"


class BridgedLegacySource:
    """Expose #490's adapter under the two names #493's core calls."""

    def __init__(self, adapter):
        self.adapter = adapter

    @property
    def readings_used(self) -> int:
        return self.adapter.readings_used

    def observe_run(self, *, device_ref: str, session_ref: str,
                    run_id: str | None = None, delivery_id: str | None = None,
                    project: str | None = None,
                    expected_boot_id: str | None = None,
                    expected_source_sha: str | None = None) -> dict:
        # ``expected_boot_id`` is deliberately not translated: #490's adapter
        # has no parameter for it. Dropping it silently is the interface gap the
        # QA report names; inventing a post-hoc check here would hide it.
        observation = self.adapter.observe_run(
            device_ref=device_ref, session_ref=session_ref,
            run_id=run_id, delivery_id=delivery_id, project=project,
            source_sha=expected_source_sha)
        return observation.as_dict()

    def observe_ci(self, *, source_sha: str, project: str | None = None) -> dict:
        observation = self.adapter.observe_ci(source_sha=source_sha,
                                              project=project)
        return observation.as_dict()
