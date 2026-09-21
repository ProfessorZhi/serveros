"""The board a producer renders, read as the rows the daily profile selects from.

WHY THIS MODULE EXISTS
----------------------
`intake/daily_cycle.select_focus` is written against a row shape carrying
`worker_stopped`, `resources_released`, `focus_disposition`,
`parking_acknowledged`, `scope_digest`, `grant_ref`, `next_action` and
`blocked_by`. Read on 2026-09-22, **no producer on this node emits any of
them**. The two halves had therefore never been in the same room: the policy
had only its own tests, and those tests supply rows from a factory that
hard-codes every one of those fields, so the gap was invisible from both ends.
Pointed at the real 17-row personal board, the policy reported all 17 rows as
unresolved work — 7 CLOSED and 4 DELIVERED among them.

This module is the one place the policy and a real projection meet. It reads
the personal board (`serverosctl work board --json`, composed by
`intake/workchain.py` from `intake/workchain-contract.yaml#personal_board`) and
states field by field what the board answers and what it does not.

WHAT IT REFUSES TO DO
---------------------
It invents nothing. `PRODUCED` maps every key it emits to the producer field
the value came from, and `compose` raises if a row would carry a key outside
it, so a field added here without a producer is a failure rather than a
plausible-looking default. The fields the policy needs and the board does not
have are reported in `not_produced` with the reason, and they are *absent* from
the rows rather than present-and-empty — `daily_cycle.liveness` reads an absent
field as `UNEVALUABLE`, which is what a silent projection deserves.

Two derivations it declines, both because a nearer-looking answer would be a
wronger one:

  * **Run state is not worker liveness.** The board row carries `runs` with
    their states, so `worker_stopped` could be synthesized as "no Run is
    RUNNING". A Run recorded as finished is a database row; whether the process
    it names has stopped is a different fact, and `docs/daily-work-cycle.md`
    §11 is explicit that after `STARTED` the actual effect must be checked
    rather than assumed. Synthesizing the field would put that assumption in
    the one place that is supposed to be evidence.
  * **The projection's age is not the render's age.** `work board` composes
    live, so its own `at` is this second; the `current.json` files it reads were
    last written on 2026-09-10. Freshness is a property of the facts, so
    `observed_at` is the OLDEST underlying projection and not the composition
    time. `read_projections` reads those times; composing a 12-day-old
    projection and calling it fresh is the failure this avoids.
"""
from __future__ import annotations

import json
from pathlib import Path

# Every key this module may put on a focus row, and the producer field it comes
# from. A value of `None` in the producer column would mean a key with no
# producer, which is why there is no such entry: `compose` raises on a row key
# that is not here, so this table is the check rather than a comment about one.
PRODUCED = {
    "id": "board row `id` (canonical Item id, work-engine's own)",
    "project": "board row `project`",
    "state": "board row `state` (work-engine ItemState)",
    "title": "board row `title` — for a person reading `work focus`; no policy reads it",
    "kind": "board row `kind` — same",
    "priority": "board row `priority.personal` then `priority.source`, and only when "
                "the card declared a literal level. UNPRODUCED stays absent; see "
                "`priority_level`.",
}

# The policy's inputs the board does not carry. Each is stated here rather than
# left for a reader to notice, because the absence is what made the policy
# unpointable and a reader of the composed rows cannot tell a field the fabric
# has not built from one this adapter forgot.
NOT_PRODUCED = {
    "worker_stopped": "`runtime/bounded_invocation.invoke_once` DOES record it — "
                      "`worker_stopped: False` on the receipt it writes before it "
                      "spawns — but a receipt is keyed by action key and nothing on "
                      "this node joins a receipt to an Item, so the BOARD has no such "
                      "field and no projection carries it. This adapter does not "
                      "synthesize it from Run state (module docstring).",
    "resources_released": "same field family, same absence: no producer records it "
                          "at all, not even on an invocation receipt",
    "focus_disposition": "the field that would mark an Item PARKED is part of the "
                         "same proposed card extension as the priorities",
    "parking_acknowledged": "same",
    "scope_digest": "a grant-scoped digest; the grant itself has no producer either",
    "grant_ref": "no grant store exists on this node; see `NOT_PRODUCED_ELSEWHERE`",
    "next_action": "`select_focus` defaults to `research` when the row is silent",
    "blocked_by": "the board carries no dependency edge",
}

# Facts the policy needs that are absent for a reason outside this adapter.
NOT_PRODUCED_ELSEWHERE = {
    "grants": "`select_focus(grants=...)` takes grants minted by an authority. "
              "Nothing on this node mints one — there is no grant store under "
              "`state/serveros/` — so every candidate is reported "
              "MISSING_OR_REVOKED_GRANT. That is the true answer: no objective "
              "has been authorized, so none may be admitted.",
    "current_priority": "`intake/workchain-contract.yaml#card_extension` proposes "
                        "`source_priority`/`personal_priority` and is "
                        "PROPOSED_NOT_APPROVED; no card carries either, so every "
                        "live row reads PRIORITY_NOT_ESTABLISHED.",
}

PRIORITY_LEVELS = ("P0", "P1", "P2", "P3", "P4")


class Refused(RuntimeError):
    """The board cannot be read as focus rows, and guessing is not an option."""


def priority_level(row: dict) -> tuple[str | None, dict]:
    """The declared level, or `None` with the board's own words for why not.

    The board publishes `priority` as a two-cell structure — `personal` and
    `source` — each either a level or the state `UNPRODUCED` with a `why`. This
    returns the level only when a cell is a literal level, and otherwise
    `None` plus that `why`, so the reason travels with the absence instead of
    being replaced by one of this module's own.
    """
    cell = row.get("priority")
    if not isinstance(cell, dict):
        return None, {"state": "NOT_A_PRIORITY_CELL",
                      "why": f"the row's `priority` is {type(cell).__name__}, not the "
                             f"board's `{{personal, source}}` structure"}
    if not any(name in cell for name in ("personal", "source")):
        return None, {"state": "NOT_A_PRIORITY_CELL",
                      "why": f"the row's `priority` object has neither the "
                             f"`personal` nor the `source` cell: {sorted(cell)}"}
    for name in ("personal", "source"):
        part = cell.get(name)
        if part is not None and not isinstance(part, dict):
            return None, {"state": "NOT_A_PRIORITY_CELL",
                          "why": f"the row's `priority.{name}` is "
                                 f"{type(part).__name__}, not the board's cell"}
        if isinstance(part, dict) and part.get("state") in PRIORITY_LEVELS:
            return part["state"], {"state": "PRODUCED", "cell": name}
    reasons = {name: (cell.get(name) or {}).get("why", "")
               for name in ("personal", "source")}
    return None, {"state": "UNPRODUCED", "why": " | ".join(
        f"{name}: {text}" for name, text in reasons.items() if text)}


def rows_from_board(board: dict) -> list[dict]:
    """One focus row per board row, carrying only what the producer answered."""
    composed = board.get("sections")
    if not isinstance(composed, list):
        raise Refused("not a personal board: no `sections` list")
    rows: list[dict] = []
    for section in composed:
        for row in section.get("rows", []):
            for field in ("id", "project", "state"):
                if not row.get(field):
                    raise Refused(
                        f"a board row has no `{field}` ({sorted(row)[:8]}…); the "
                        f"canonical id is what a selection is recorded against, "
                        f"and a row without one cannot be selected or reported")
            item = {k: row[k] for k in ("id", "project", "state", "title", "kind")
                    if row.get(k) is not None}
            level, _why = priority_level(row)
            if level is not None:
                item["priority"] = level
            unexpected = set(item) - set(PRODUCED)
            if unexpected:
                raise Refused(
                    f"this adapter would emit {sorted(unexpected)}, which "
                    f"`PRODUCED` does not name a producer for; add the producer "
                    f"or drop the field")
            rows.append(item)
    return rows


def read_projections(projects: list[dict]) -> dict:
    """Each project's own `generated_at`, keyed by project — the module's only IO.

    An unreadable or undated projection is reported as `None` rather than
    omitted, because a project whose age is unknown makes the whole
    composition's age unknown, and a caller that treats an absent key as
    "fine" would be reading silence as freshness.
    """
    times: dict[str, str | None] = {}
    for entry in projects or []:
        name = entry.get("project") or entry.get("path") or "?"
        path = entry.get("path")
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
            times[name] = document.get("generated_at")
        except (OSError, ValueError, TypeError):
            times[name] = None
    return times


def oldest(times: dict) -> str | None:
    """The earliest of the projections' ages, compared as instants, or `None`.

    Compared as parsed instants and not as strings, because the projections are
    written by different runs and an offset other than the first one's would
    make a lexicographic minimum pick the wrong one — `2026-09-10T07:28+00:00`
    sorts before `2026-09-10T15:00+08:00` and names an earlier instant, and the
    pair only has to differ once for the comparison to be silently inverted.
    """
    parsed: list[tuple[object, str]] = []
    for name, stamp in (times or {}).items():
        if not stamp:
            return None
        try:
            when = instant_time(stamp)
        except ValueError:
            raise Refused(
                f"projection `{name}` has generated_at {stamp!r}, which is not a "
                f"timestamp with an offset; an age that cannot be read is not an "
                f"age, and reading it as one would report a stale board as fresh")
        parsed.append((when, stamp))
    if not parsed:
        return None
    return min(parsed, key=lambda pair: pair[0])[1]


def instant_time(value: str):
    """`daily_cycle.instant`'s rule, kept local so the adapter stays loadable alone."""
    from datetime import datetime
    when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError("timestamp requires an explicit timezone")
    return when


def compose(board: dict, *, projection_times: dict | None = None) -> dict:
    """The board as focus rows, with the age of the FACTS it was composed from.

    `observed_at` is the oldest `generated_at` among the projections, and it is
    `None` when any projection's age is unknown. Both are answers a caller must
    handle: `select_focus` reads `None` as not fresh, which is the right
    outcome for an age nobody can state.
    """
    if projection_times is None:
        projection_times = read_projections(board.get("projects") or [])
    observed_at = oldest(projection_times)
    return {
        "rows": rows_from_board(board),
        "observed_at": observed_at,
        "board_at": board.get("at"),
        "projection_times": projection_times,
        "projects": [p.get("project") for p in (board.get("projects") or [])],
        "not_produced": dict(NOT_PRODUCED),
        "not_produced_elsewhere": dict(NOT_PRODUCED_ELSEWHERE),
    }
