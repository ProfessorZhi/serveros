"""Daily-work policy over trusted Work Engine projections, not another scheduler.

No IO, model calls, Item writes or authorization minting occur here. The caller
must supply authenticated grants and fresh observations from their owners.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

BUSY = frozenset({"ACTIVE", "DELEGATED", "WAITING", "NEEDS_HUMAN", "CANCELLING", "EFFECT_UNKNOWN"})
ELIGIBLE = frozenset({"AVAILABLE", "READY"})
TERMINAL = frozenset({"CLOSED", "DELIVERED", "CANCELLED"})
PROTECTED_EFFECTS = frozenset({"production.cutover", "production.rollback", "production.mutate"})

# The two fields a projection may carry about whether something is still held
# on an Item's behalf. Neither is produced anywhere on this node as of
# 2026-09-22: `intake/focus_source.py` reads the personal board, which carries
# neither, and says so per row. See `liveness` for why their absence is not
# read as evidence.
LIVENESS_FIELDS = ("worker_stopped", "resources_released")
# The accepted priority vocabulary. A row whose `priority` is not one of these
# strings has no established priority — checked with `isinstance` rather than
# with a bare membership test, because the personal board publishes priority as
# a `{personal, source}` object and `{...} in {"P0"}` raises TypeError: a
# projection this policy is pointed at must not crash it.
PRIORITY_LEVELS = frozenset({"P0", "P1", "P2", "P3", "P4"})
# The completion boundary's vocabulary, as the board row publishes it
# (`workchain.completion_of`). Spelled out as literals rather than imported: the
# contract between this module and its caller is the ROW, and importing the
# board composer's constants would make the daily-work policy depend on the
# projection format — the coupling `intake/focus_source.py` exists to avoid.
COMPLETION_OPEN = "OPEN"


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def fresh(observed_at: str, now: datetime, ttl_seconds: int) -> bool:
    if now.tzinfo is None or type(ttl_seconds) is not int or ttl_seconds <= 0:
        raise ValueError("aware now and positive integer TTL required")
    age = (now - instant(observed_at)).total_seconds()
    return 0 <= age <= ttl_seconds


def standing_reason(grant: dict, *, now: datetime) -> str | None:
    """The two judgements about a grant that need no Item and no action.

    Is there a live grant here at all, and is it in force now. Factored out
    verbatim when a second reader needed them: `runtime/cli.py`'s `--grant`
    authority path commits a READING, which has no Item to hand `grant_reason`,
    and a second copy of these comparisons would be a second answer to "is this
    grant live" — the failure this tree keeps naming. `revoked is not False`
    rather than `not revoked` is deliberate and is the same test the original
    written here used: a record that never says whether it was revoked has not
    said it is live.
    """
    if not grant.get("ref") or grant.get("revoked") is not False:
        return "MISSING_OR_REVOKED_GRANT"
    try:
        if not instant(grant["valid_from"]) <= now < instant(grant["expires_at"]):
            return "GRANT_NOT_CURRENT"
    except (KeyError, ValueError, TypeError):
        return "INVALID_GRANT_TIME"
    return None


def grant_reason(grant: dict, *, item: dict, action: str, now: datetime) -> str | None:
    """Validate scope/time/budget, not the authenticity of the supplied grant."""
    standing = standing_reason(grant, now=now)
    if standing is not None:
        return standing
    if item.get("project") not in grant.get("projects", []):
        return "PROJECT_OUT_OF_SCOPE"
    if item.get("id") not in grant.get("items", []):
        return "ITEM_NOT_AUTHORIZED"
    if action not in grant.get("actions", []):
        return "ACTION_NOT_AUTHORIZED"
    if not item.get("scope_digest") or item["scope_digest"] != grant.get("scope_digests", {}).get(item.get("id")):
        return "SCOPE_CHANGED"
    budget = grant.get("budget", {})
    for key in ("wall_seconds", "max_attempts", "max_output_bytes"):
        if type(budget.get(key)) is not int or budget[key] <= 0:
            return "MISSING_BOUNDED_BUDGET"
    if budget.get("exhausted") is not False:
        return "BUDGET_UNAVAILABLE"
    return None


def liveness(row: dict) -> tuple[bool | None, list[str]]:
    """Is something still held on this Item's behalf, and on whose word.

    THREE-valued, and the third value is the point. `True` and `False` are the
    row saying so; `None` is the row not saying. Only fields that are actually
    present are read, so a projection that carries one of the two answers about
    that one.

    The version this replaces was a single expression —
    `row.get("worker_stopped") is not True or row.get("resources_released") is
    not True` — which collapses "the field is absent" into "the field is False"
    and makes a silent projection read as a live worker. Measured against the
    live 17-row personal board, that returned all 17 rows as carryover,
    including the 7 CLOSED and 4 DELIVERED ones, and reported a node with four
    Items in flight as OVER_CAPACITY on the strength of fourteen that had
    finished. `tests/test_focus_source.py` carries that measurement against the
    captured board; the direction is not a matter of taste, because a
    projection that is silent about a worker is not evidence of one.
    """
    present = [(field, row[field]) for field in LIVENESS_FIELDS
               if row.get(field) is not None]
    if not present:
        return None, []
    held = [field for field, value in present if value is not True]
    return bool(held), held


def classify(row: dict, grants: dict[str, dict], *, now: datetime) -> dict:
    """Every judgement about one row that does not depend on WHO is asking.

    ONE COPY, and that is the whole reason it is a function. The recommender
    (`select_focus`) and the continuer (`continue_focus`) ask the same
    questions about the same row — is something still held, is it finished, is
    it authorized, is it blocked — and the tree's own ledger records what two
    copies of one rule cost: a rule implemented in two places cannot be killed
    by a single mutation, and the copy that drifts is the one nobody reads.

    What is NOT here: priority, and what a caller does with the answer. Those
    are exactly the two things the two callers must decide differently, and
    putting them here would be the same defect one level up.

    `disposition` is one of

        HELD      something is still held on this Item's behalf — a worker, a
                  reservation, or the Item's own state. It OCCUPIES a slot.
        FINISHED  a state the fabric considers over, and no field asserts
                  anything is still held. It frees its slot.
        PARKED    acknowledged as parked, and the release is not evidenced.
        ELIGIBLE  may take work, subject to what the caller asks next.
        BLOCKED   a named reason; `reason` is it.

    For a row in a state the fabric considers over, the verdict also carries
    `completion` — the contract's answer to "does this Item still owe
    something", read off the board row. It is NOT part of `disposition`, and
    deliberately so: a `CLOSED` Item that is missing its required report cannot
    take new work either, so the recommender still counts it where it always
    did, while the continuer must not call a person's accepted objective
    finished. One reading, two callers, two answers — the same shape as
    `priority`, and the reason the field is here rather than invented twice.
    """
    held, held_fields = liveness(row)
    state = row.get("state")
    parked = (row.get("focus_disposition") == "PARKED" and
              row.get("parking_acknowledged") is True)
    verdict = {"id": row.get("id"), "held_fields": held_fields,
               "liveness": held, "state": state, "action": None,
               "unevaluated": False}
    if held:
        # BEFORE the state test, and that order is acceptance D: an Item whose
        # Run reached a terminal state while its worker is still writing has
        # released nothing. Reading the state first would free a slot that is
        # still occupied.
        return {**verdict, "disposition": "HELD",
                "reason": f"WORKER_OR_RESERVATION_HELD:{','.join(held_fields)}"}
    if parked and held is not False:
        # Undoing a park needs positive evidence that nothing is held any more.
        # The board carries no liveness field, so on this node a parked row
        # never releases its slot by being read — which is the answer the design
        # asks for: parking is acknowledged, not asserted.
        return {**verdict, "disposition": "PARKED",
                "reason": "PARKED_RELEASE_UNCONFIRMED"}
    if state in TERMINAL:
        # The obligation is REPORTED here and nothing more is decided with it.
        # A terminal row frees its slot either way, because the recommender's
        # question is whether the row can take new work, and `DELIVERED` and
        # `CLOSED` cannot. Whether an objective a person ACCEPTED is finished is
        # the continuer's question, asked of the same field and answered
        # differently — the split `classify`'s docstring already describes for
        # priority, and the reason the reading lives here once.
        verdict["completion"] = row.get("completion")
        return {**verdict, "disposition": "FINISHED", "reason": "TERMINAL_STATE"}
    if parked:
        return {**verdict, "disposition": "BLOCKED",
                "reason": "PARKED_NOT_ELIGIBLE"}
    if state in BUSY:
        return {**verdict, "disposition": "HELD",
                "reason": "STATE_HOLDS_SLOT"}
    if state not in ELIGIBLE:
        return {**verdict, "disposition": "BLOCKED",
                "reason": "STATE_NOT_ELIGIBLE"}
    # From here the row is a candidate for work, and the question is whether it
    # is authorized. `next_action` is asked FIRST and its absence is an exact
    # answer rather than a default: the grant is asked about an action, and
    # answering about one nobody named is a refusal about a fabricated operand.
    # The version this replaces defaulted the action to `research`, so a board
    # that did name a grant with `actions: [publish]` was refused
    # ACTION_NOT_AUTHORIZED — a sentence about an action nobody chose, hiding
    # the real gap (the card field nobody built) behind a plausible one.
    action = row.get("next_action")
    if not (isinstance(action, str) and action.strip()):
        return {**verdict, "disposition": "BLOCKED",
                "reason": "NEXT_ACTION_NOT_ESTABLISHED"}
    verdict["action"] = action
    reason = ("HUMAN_EXECUTED" if action in PROTECTED_EFFECTS else
              grant_reason(grants.get(row.get("grant_ref"), {}), item=row,
                           action=action, now=now))
    if row.get("blocked_by"):
        reason = reason or "DEPENDENCY_BLOCKED"
    if reason:
        return {**verdict, "disposition": "BLOCKED", "reason": reason}
    # Whether liveness could be decided is reported, not swallowed: a row
    # admitted on its state alone is a row admitted on the weaker evidence.
    verdict["unevaluated"] = held is None
    return {**verdict, "disposition": "ELIGIBLE", "reason": None}


def obligation_of(verdict: dict) -> dict:
    """Whether an accepted objective still owes what the contract requires.

    The board row carries its own `completion` verdict; this turns it into the
    word the cycle's `because` is written in, and keeps "assessed, and something
    is owed" apart from "could not be assessed" — the distinction `liveness`
    makes about workers, one field over.

    A terminal state NAME is not an answer to this question, which is the whole
    reason the field is consulted: an Item being called `CLOSED` is what the
    boundary exists to check, not what settles it.

    Returns `{"state", "assessed", "reason", "why"}`. `reason` is None when
    nothing is owed at an assessed state, and the caller falls through to the
    state-based reason it already had.
    """
    completion = verdict.get("completion")
    state = verdict.get("state")
    if not isinstance(completion, dict):
        return {"state": "NOT_ASSESSED", "assessed": False, "reason": None,
                "why": ("the board row carries no `completion` verdict, so whether "
                        "this Item still owes what the contract requires could not "
                        "be read here")}
    if completion.get("state") != COMPLETION_OPEN:
        return {"state": completion.get("state"), "assessed": True,
                "reason": None, "why": str(completion.get("why") or "")}
    # The three gaps are kept as three in the verdict and joined into one word
    # here, because a reason string is what `because` carries to a person.
    missing = (list(completion.get("missing_fields") or ())
               + list(completion.get("missing_documents") or ())
               + list(completion.get("unreported_documents") or ()))
    word = {"DELIVERED": "AWAITING_ACCEPTANCE_EVIDENCE",
            "CLOSED": "CLOSED_UNEVIDENCED"}.get(state, "OPEN_OBLIGATION")
    return {"state": COMPLETION_OPEN, "assessed": True,
            "reason": f"{word}:{','.join(missing)}" if missing else word,
            "why": str(completion.get("why") or "")}


def _order(candidates: list[dict]) -> list[dict]:
    """Sort by the accepted priority — the one operand it cannot be missing.

    `select_focus` filters to rows whose priority IS one of the accepted
    levels, so `r["priority"]` here is safe by construction rather than by a
    guard. That is exactly the kind of safety that stops being true when
    somebody edits three lines away, so the invariant is asserted at the one
    place that depends on it: a candidate without an established priority means
    the filter and this sort disagree, and a silent KeyError in the daily cycle
    is a worse way to find that out.
    """
    missing = [r.get("id") for r in candidates
               if not isinstance(r.get("priority"), str)
               or r["priority"] not in PRIORITY_LEVELS]
    if missing:
        raise ValueError(
            f"candidates without an established priority reached the ordering: "
            f"{missing}. `select_focus` excludes a row for "
            f"PRIORITY_NOT_ESTABLISHED, so this means the filter and this sort "
            f"no longer agree about the same set.")
    return sorted(candidates, key=lambda r: (r["priority"],
                                             r.get("created_at", ""), r["id"]))


def blocked(reason: str, *, because: str = "") -> dict:
    """The decision that could not be made at all — one shape, three callers.

    A decision that was never reached has no selection, no carryover and no
    exclusions, and the three questions that end this way (`STALE_BOARD`,
    `PROJECTION_AGE_UNKNOWN`, `SELECTION_UNREADABLE`) all end it for the same
    kind of reason: an input nobody could read. Building that shape in each of
    them is how they come to differ, and a caller that reads `carryover` from
    one and not another is reading the difference rather than the answer. It
    lives here rather than in `runtime/cli.py` for the same reason
    `admission_plan` does: the shape of a decision belongs to the policy, and a
    caller that authored one would be deciding by constructing.

    The prose goes in `blocked_because` and not in `because`, which is already
    spoken for: in a decision that reached rows, `because` maps one Item id to
    its reason, and a reader who found a sentence there instead would get a
    string where the shape of the answer promised a lookup.
    """
    return {"status": "BLOCKED", "reason": reason, "selected": [],
            "because": {}, "excluded": [], "unevaluated": [],
            "admission_required": False, "blocked_because": because}


def accepted_items(accepted: dict | None) -> dict:
    """A person's authored selection, read without inventing one.

    `accepted` is the board's own `today` document (`intake/workchain.py`'s
    `load_today`, merged across projects by `compose_board`). Absence is not an
    empty selection — that is the distinction the whole of `work today` exists
    to keep, and reading it back as "a person chose nothing" here would undo it
    at the one place it matters most.

    Returns `{state, items, why, by_project}` and never a guess.
    """
    if not isinstance(accepted, dict) or not accepted:
        return {"state": "NOT_ON_THIS_PROJECTION", "items": [], "by_project": {},
                "why": ("the projection this decision was made from carries no "
                        "`today` selection document, so whether a person chose "
                        "anything was not answerable from it. That is not the "
                        "same as nobody having chosen.")}
    items = [str(x) for x in (accepted.get("items") or [])]
    return {"state": str(accepted.get("state") or "UNKNOWN"), "items": items,
            "why": str(accepted.get("why") or ""),
            "by_project": dict(accepted.get("by_project") or {})}


def continue_focus(rows: list[dict], grants: dict[str, dict], accepted: dict | None,
                   *, now: datetime, observed_at: str,
                   ttl_seconds: int = 900, limit: int = 2) -> dict:
    """Continue the objectives a PERSON accepted — and recommend nothing.

    This is the half `select_focus` could not be, and the reason it could not:
    that function fills every spare slot with a candidate of its own choosing,
    so a cycle that reads it can never come back with fewer than `limit`
    objectives. Focus is what a person selected to carry. When the selected
    work finishes and the person selects nothing new, the answer is ZERO — not
    two recommendations wearing the selection's clothes.

    So the two questions are answered by two functions:

        `continue_focus`  what did a person accept, and can it still go on
        `select_focus`    what might be worth proposing for the spare slots

    and the caller may use the second only to PROPOSE. Nothing in this module
    can make a proposal into an acceptance: only a recorded selection can, and
    the only writer of one is a person (`serverosctl work today --select`).

    PRIORITY IS NOT ASKED HERE, and that is the second half of P6's rule. The
    recommender refuses a row with no established priority, because ordering
    proposals requires one and defaulting to P0 would invent a ranking. An
    ACCEPTED objective needs no ranking: it is the objective, not one of
    several competing for a slot. Blocking it for a missing priority would make
    the fabric refuse to continue the very work somebody said to do — which is
    what this function was written to stop.

    A FINISHED state is not the end of the question either. An accepted
    objective whose board row says the contract's boundary is not discharged
    (`obligation_of`: an Item called `DELIVERED` whose required report is not
    there, or a `CLOSED` one closed without it) CONTINUES, and `because` names
    what is missing. What it is waiting for is evidence, which is work — unlike
    a person's acceptance, which is what a discharged-but-not-complete Item
    waits for and which nothing here can start. Where the row carries no
    verdict at all, the objective also continues, with
    `COMPLETION_NOT_ASSESSED` and the unanswered question in `unevaluated`: an
    unasked question is not a favourable answer, the same rule `liveness`
    follows about workers.

    Everything else still applies. An accepted objective with no grant, no
    named action, a dependency that is not met, or a protected production
    effect is reported in `excluded` with the exact reason: a person accepting
    an objective authorizes the OBJECTIVE, not an action the fabric cannot
    find authorized.
    """
    if type(limit) is not int or not 0 < limit <= 2:
        raise ValueError("this profile supports one or two focus objectives")
    selection = accepted_items(accepted)
    # NOT `carryover`. In the recommender that word means "unresolved work
    # already in progress, holding a slot beside the chosen ones" — a set
    # disjoint from `selected`. Here the accepted objectives ARE the selection,
    # so a second list beside it would read as work on top of what a person
    # asked for. The per-Item reason travels in `because` instead.
    base = {"selected": [], "because": {}, "unevaluated": [], "excluded": [],
            "accepted_state": selection["state"], "continuation": True,
            "admission_required": True}
    # `accepted_state` rides on the blocked shape too: the selection was READ
    # before the board turned out to be stale, so what a person chose is known
    # and is worth saying even though the cycle cannot be planned. Dropping it
    # would make a blocker about the BOARD's age read as a blocker about the
    # selection.
    if not fresh(observed_at, now, ttl_seconds):
        return {**blocked("STALE_BOARD"), "accepted_state": selection["state"],
                "selection_items": list(selection["items"])}
    if selection["state"] == "UNREADABLE":
        return {**blocked("SELECTION_UNREADABLE", because=selection["why"]),
                "accepted_state": selection["state"]}
    if selection["state"] in ("NOT_ON_THIS_PROJECTION", "UNPRODUCED"):
        return {**base, "status": "PLANNED", "reason": "NO_SELECTION_AUTHORED",
                "unused_slots": limit, "recommendation_slots": limit,
                "because": selection["why"]}
    if not selection["items"]:
        return {**base, "status": "PLANNED", "reason": "SELECTION_IS_EMPTY",
                "unused_slots": limit, "recommendation_slots": limit,
                "because": selection["why"] or
                           "a selection was recorded and names no Item"}
    ids = [r.get("id") for r in rows]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("missing or duplicate canonical Item ID")
    by_id = {row["id"]: row for row in rows}
    continuing, excluded, unevaluated = [], [], []
    because: dict[str, str] = {}
    for item_id in selection["items"]:
        row = by_id.get(item_id)
        if row is None:
            excluded.append({"id": item_id, "reason": "ACCEPTED_ITEM_NOT_ON_THE_BOARD"})
            continue
        verdict = classify(row, grants, now=now)
        if verdict["disposition"] in ("HELD", "ELIGIBLE"):
            continuing.append(item_id)
            # An eligible accepted objective has no reason STRING because
            # nothing refused it. It still has a reason to be here: a person
            # said so, and the word says that rather than leaving the field
            # empty next to the held rows, which do carry one.
            because[item_id] = verdict["reason"] or "ACCEPTED_AND_ELIGIBLE"
            if verdict["unevaluated"]:
                unevaluated.append({
                    "id": item_id, "fields": list(LIVENESS_FIELDS),
                    "because": "the projection carries neither liveness field, so "
                               "whether a worker still holds this Item was not "
                               "decided here; the Item's own state is what "
                               "continued it"})
            continue
        if verdict["disposition"] == "FINISHED":
            obligation = obligation_of(verdict)
            if obligation["state"] == "OPEN":
                # A terminal state, and the contract's boundary says the Item
                # still owes something: `DELIVERED` sounds finished and is not,
                # and a `CLOSED` Item missing a required document was closed
                # anyway. The SLOT stays — what this objective is now waiting
                # for is the missing evidence, which is work, unlike a person's
                # acceptance, which is not something this module can start.
                continuing.append(item_id)
                because[item_id] = obligation["reason"]
                continue
            if not obligation["assessed"]:
                # The row carries no verdict, so "finished" is not an answer
                # this function has: an unanswered question is not a favourable
                # one. The objective continues and the reason says which
                # question could not be put — the same three-valued reading
                # `liveness` gets, one field over.
                continuing.append(item_id)
                because[item_id] = "COMPLETION_NOT_ASSESSED"
                unevaluated.append({
                    "id": item_id, "fields": ["completion"],
                    "because": obligation["why"]})
                continue
        excluded.append({"id": item_id, "reason": verdict["reason"]})
    over = len(continuing) > limit
    return {**base, "status": "OVER_CAPACITY" if over else "PLANNED",
            "selected": continuing, "because": because,
            "unevaluated": unevaluated, "excluded": excluded,
            "unused_slots": max(0, limit - len(continuing)),
            "recommendation_slots": max(0, limit - len(continuing)),
            "selection_digest": fingerprint({"selected": continuing,
                                             "accepted": selection["items"],
                                             "observed_at": observed_at})}


def select_focus(rows: list[dict], grants: dict[str, dict], *, now: datetime,
                 observed_at: str, ttl_seconds: int = 900, limit: int = 2,
                 accepted: dict | None = None) -> dict:
    """What might be worth PROPOSING for the spare slots — never what to carry.

    Rows are normalized by the board adapter (`intake/focus_source.py`, reached
    as `serverosctl work focus`); absence is not an empty project. PARKED
    requires positive evidence that no worker or reservation is still held
    before it releases its slot.

    THIS IS A RECOMMENDER, and the returned `recommendation_only` is not
    decoration. It fills every spare slot it can, so its `selected` is what the
    fabric would propose if a person asked — and running it as the daily cycle
    is the defect P6 names: a cycle that always comes back with two objectives
    cannot express "the work a person chose is finished and nothing new was
    chosen". `continue_focus` answers that question; this one answers a
    different one, and the two are deliberately not merged.

    Three things it reads in the three-valued way rather than the permissive
    one:

      * `worker_stopped` / `resources_released` — see `liveness`. Where a
        projection is silent, `state` is what decides: work-engine's state
        machine is the authority for whether work is in progress, and these two
        fields are a secondary alarm, read only when the row actually asserts
        them. Rows whose liveness was not decidable are listed in
        `unevaluated`, so a reader is told rather than reassured.
      * `TERMINAL` was declared and never consulted. 11 of the 17 live rows are
        in a terminal state (7 CLOSED, 4 DELIVERED) and each of them used to be
        reported as in flight. A terminal row now leaves the decision with the
        reason `TERMINAL_STATE`: it neither holds a slot nor accepts new work.
        DELIVERED is in that set deliberately — an Item delivered and not yet
        closed has finished its implementation, and what it is waiting for is
        acceptance, which is not something this function can start.
      * `next_action` — see `classify`. The default it used to carry is gone.
    """
    if type(limit) is not int or not 0 < limit <= 2:
        raise ValueError("this profile supports one or two focus objectives")
    if not fresh(observed_at, now, ttl_seconds):
        return {**blocked("STALE_BOARD"), "recommendation_only": True}
    ids = [r.get("id") for r in rows]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("missing or duplicate canonical Item ID")
    already = set(accepted_items(accepted)["items"]) if accepted is not None else set()
    carry, candidates, excluded, unevaluated = [], [], [], []
    carryover_because: dict[str, str] = {}
    for row in rows:
        verdict = classify(row, grants, now=now)
        if verdict["disposition"] == "HELD":
            carry.append(row["id"])
            carryover_because[row["id"]] = verdict["reason"]
            continue
        if verdict["disposition"] == "PARKED":
            carry.append(row["id"])
            carryover_because[row["id"]] = verdict["reason"]
            continue
        if verdict["disposition"] == "FINISHED":
            excluded.append({"id": row["id"], "reason": verdict["reason"]})
            continue
        if verdict["disposition"] == "BLOCKED":
            reason = verdict["reason"]
        elif row["id"] in already:
            # Already the person's objective. `continue_focus` is what carries
            # it; proposing it again would put one Item in two answers.
            reason = "ALREADY_ACCEPTED_FOCUS"
        else:
            priority = row.get("priority")
            reason = (None if isinstance(priority, str) and priority in PRIORITY_LEVELS
                      else "PRIORITY_NOT_ESTABLISHED")
        if reason:
            excluded.append({"id": row["id"], "reason": reason})
            continue
        if verdict["unevaluated"]:
            unevaluated.append({
                "id": row["id"], "fields": list(LIVENESS_FIELDS),
                "because": "this projection carries neither field, so whether a "
                           "worker still holds the Item was not decided here; "
                           "the Item's own state is what admitted it"})
        candidates.append(row)
    # Priority must come from the accepted board, not LLM-generated popularity.
    chosen = _order(candidates)
    slots = max(0, limit - len(carry))
    chosen = chosen[:slots]
    return {"status": "OVER_CAPACITY" if len(carry) > limit else "PLANNED",
            "selected": [r["id"] for r in chosen], "carryover": carry,
            "carryover_because": carryover_because, "unevaluated": unevaluated,
            "unused_slots": max(0, slots-len(chosen)), "excluded": excluded,
            "selection_digest": fingerprint({"selected": chosen, "carryover": carry,
                                              "observed_at": observed_at}),
            "admission_required": True, "recommendation_only": True}


def admission_plan(decision: dict, rows: list[dict], grants: dict[str, dict], *,
                   cycle_ref: str, observed_at: str, admitted_at: str,
                   action_key: str, lease: dict) -> dict:
    """The one record an admission is made of, or the reason there is none.

    P6 asks for the selection, the budget reservation and the lease/dispatch
    intent to be committed in one transaction. This is the half that decides
    what those three ARE; `runtime/agent_control_store.py#admit` is the half
    that commits them. Splitting it here rather than building the record in the
    store is the same split `select_focus` / `work focus` already use: the
    policy is a pure function of documents, and the store is the part with a
    filesystem and a lock.

    `decision` is `select_focus`'s own return value and `rows` the board it was
    given, because `selected` is a list of Item ids and the grant a row runs
    under is a field on the row.

    ONE GRANT PER CYCLE, and this is a refusal rather than a merge. A cycle has
    one action key, one lease intent and therefore one dispatch — and a
    dispatch has one timeout and one output bound. Two grants would be two
    answers to "what bounds this", and the record would have to pick one or
    invent a combination rule. Neither is stated anywhere, so the two-grant
    cycle is refused with both grants named: admitting them as two cycles says
    what actually happened, and it is something the caller can do.

    Returns the record `admit` takes, or `{"refused": <word>, "because": <one
    sentence>}`. A refusal is RETURNED rather than raised because the caller
    is expected to have one — most days this node has no grant at all, and that
    is an answer, not an error.

    `admitted_at` is the instant the admission is made, and it is the caller's
    clock rather than this function's (there is none here) — the same
    arrangement `observed_at` already has. It is IN the record because the
    store checks it against the grant's window: a delegation that expired
    between the board read and the write is one the cycle must not be admitted
    under, and that comparison needs the instant the admission is made at. It
    is not part of the admission's IDENTITY — `runtime/agent_control_store`'s
    `_intent_digest` leaves it out, so a retried admission is still the same
    admission.
    """
    selected = decision.get("selected") or []
    if not selected:
        return {"refused": "NOTHING_SELECTED",
                "because": (f"the decision is {decision.get('status')}"
                            + (f" ({decision['reason']})" if decision.get("reason") else "")
                            + " and selected no objective, so there is nothing to "
                              "admit. An admission with no selection would hold a "
                              "reservation and an action key for no work")}
    by_id = {row.get("id"): row for row in rows}
    named: dict[str, list[str]] = {}
    for item_id in selected:
        row = by_id.get(item_id)
        if row is None:
            return {"refused": "SELECTION_NOT_ON_THE_BOARD",
                    "because": (f"{item_id} was selected and is not among the "
                                f"{len(rows)} rows this decision was made from, so "
                                f"the grant it runs under cannot be read")}
        ref = row.get("grant_ref")
        if not ref:
            # Selected and unsponsored, which `select_focus` cannot produce:
            # it asks `grant_reason` about every row and a row naming no grant
            # draws MISSING_OR_REVOKED_GRANT and is excluded. So this is the
            # decision and the board disagreeing about one row, and it is
            # reported as that rather than as "nothing was selected".
            return {"refused": "SELECTION_CARRIES_NO_GRANT",
                    "because": (f"{item_id} is in this decision's `selected` and "
                                f"the row it was selected from names no "
                                f"`grant_ref`, which `select_focus` excludes a row "
                                f"for. The decision and the board disagree about "
                                f"this row — on a board that names no grant at all "
                                f"(the live one does not), nothing can be admitted "
                                f"until `intake/focus_source.py`'s missing card "
                                f"field is produced")}
        named.setdefault(str(ref), []).append(str(item_id))
    if len(named) > 1:
        pairs = "; ".join(f"{ref} ({', '.join(items)})"
                          for ref, items in sorted(named.items()))
        return {"refused": "MULTIPLE_GRANTS_IN_ONE_CYCLE",
                "because": (f"this cycle's objectives are authorized by {len(named)} "
                            f"different grants — {pairs} — and a cycle has one "
                            f"action key, one lease and therefore one dispatch, "
                            f"which has one timeout and one output bound. Two grants "
                            f"are two answers to what bounds it, and no rule for "
                            f"combining them is declared: admit them as two cycles")}
    grant_ref, items = next(iter(named.items()))
    grant = grants.get(grant_ref, {})
    if grant.get("ref") != grant_ref:
        return {"refused": "GRANT_NOT_RECORDED",
                "because": (f"{items[0]} names grant {grant_ref}, and the envelope "
                            f"this cycle would be admitted under does not contain "
                            f"it — so either nothing recorded that delegation or "
                            f"its own delegation no longer authorizes. "
                            f"`context/delegation.py` reports which")}
    budget = grant.get("budget")
    if not isinstance(budget, dict):
        return {"refused": "MISSING_BOUNDED_BUDGET",
                "because": f"grant {grant_ref} carries no budget to reserve"}
    reservation = {}
    for key in ("wall_seconds", "max_attempts", "max_output_bytes"):
        if type(budget.get(key)) is not int or budget[key] <= 0:
            return {"refused": "MISSING_BOUNDED_BUDGET",
                    "because": (f"grant {grant_ref}'s budget has no positive "
                                f"{key}, so there is no bound to record. "
                                f"`grant_reason` refuses this same grant for the "
                                f"same reason")}
        reservation[key] = budget[key]
    if not str(cycle_ref or "").strip():
        return {"refused": "NO_CYCLE_REF",
                "because": ("an admission is stored under the cycle it is for, and "
                            "this call names none. The ref is what a resume names: "
                            "without it the reservation can never be released")}
    if not str(admitted_at or "").strip():
        # Refused here as well as in the store, and for `NO_CYCLE_REF`'s reason:
        # the record this function returns is the one `admit` takes, and a
        # record with no instant is not one it takes. The store's refusal is
        # for the callers that do not come through here, and its sentence says
        # which operand it checked.
        return {"refused": "NO_ADMISSION_INSTANT",
                "because": ("nothing says when this admission is made, and the "
                            "grant's window is read against that instant: a "
                            "delegation that had already expired would admit the "
                            "cycle and no later reader could tell")}
    return {"cycle_ref": str(cycle_ref),
            "grant_ref": grant_ref,
            "reservation": reservation,
            "lease": dict(lease),
            "action_key": str(action_key),
            "admitted_at": str(admitted_at),
            "selection": {"selection_digest": decision["selection_digest"],
                          "observed_at": observed_at,
                          "selected": list(selected),
                          "carryover": list(decision.get("carryover") or [])}}


def review_gate(packet: dict, review: dict, grant: dict, *, now: datetime) -> dict:
    """Check a receipt from a TRUSTED review adapter, never raw model JSON alone.

    This read-only decision is not a Work Engine admission or filesystem sandbox.
    The authoritative adapter must re-check it under its existing admission lock.
    """
    def stop(reason: str) -> dict:
        return {"decision": "BLOCKED", "reason": reason}
    action = packet.get("action")
    if action in PROTECTED_EFFECTS:
        return {"decision": "HUMAN_EXECUTED", "reason": "PROTECTED_PRODUCTION_EFFECT"}
    reason = grant_reason(grant, item=packet, action=action, now=now)
    if reason:
        return stop(reason)
    if (review.get("adapter_verified") is not True or
            type(review.get("process_exit_code")) is not int or review["process_exit_code"] != 0):
        return stop("REVIEW_RECEIPT_UNVERIFIED")
    if review.get("verdict") != "APPROVE":
        return stop("REVIEW_NOT_APPROVED")
    if not isinstance(packet.get("author_session"), str) or not packet["author_session"].strip():
        return stop("AUTHOR_SESSION_UNRESOLVED")
    if (not isinstance(review.get("reviewer_session"), str) or
            not review["reviewer_session"].strip() or review["reviewer_session"] == packet["author_session"]):
        return stop("REVIEW_NOT_INDEPENDENT")
    if review.get("model") not in packet.get("allowed_review_models", []):
        return stop("REVIEW_MODEL_MISMATCH")
    if review.get("reasoning_effort") not in {"high", "xhigh"}:
        return stop("REVIEW_DEPTH_INSUFFICIENT")
    try:
        if not fresh(review["observed_at"], now, packet["review_ttl_seconds"]):
            return stop("REVIEW_EXPIRED")
    except (KeyError, TypeError, ValueError):
        return stop("INVALID_REVIEW_TIME")
    bindings = ("id", "scope_digest", "source_digest", "design_digest", "plan_digest", "baseline_digest")
    if any(not packet.get(k) or review.get(k) != packet[k] for k in bindings):
        return stop("REVIEW_INPUT_CHANGED")
    if packet.get("binding_ready") is not True:
        return stop("ENVIRONMENT_NOT_READY")
    if packet.get("prior_effect") in {"UNKNOWN", "PARTIAL"}:
        return stop("RECONCILE_PRIOR_EFFECT_FIRST")
    return {"decision": "ELIGIBLE_FOR_ADMISSION", "reason": "CURRENT_SCOPE_AND_REVIEW_MATCH",
            "execution_authorized": False}


def invalidate_claims(claims: list[dict], source_revisions: dict[str, str]) -> list[dict]:
    """Preserve history while preventing superseded/withdrawn facts from recall."""
    result = []
    for claim in claims:
        row = dict(claim)
        refs = row.get("source_revisions", {})
        valid_refs = (isinstance(refs, dict) and bool(refs) and
                      all(isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
                          and k in source_revisions for k, v in refs.items()))
        if not valid_refs or any(source_revisions[k] != v for k, v in refs.items()):
            row.update(recallable=False, verification="STALE_OR_UNRESOLVED_SOURCE")
        elif row.get("verification") == "EVIDENCED" and row.get("withdrawn") is False:
            row["recallable"] = True
        else:
            row["recallable"] = False
        result.append(row)
    return result
