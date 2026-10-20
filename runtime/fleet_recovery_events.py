#!/usr/bin/env python3
"""The Core's bounded-recovery receipt becomes LIVE/REPORT events, and nothing
else — typed, deduped, and honest about what the receipt does NOT say.

WHAT THE RECEIPT ACTUALLY CARRIES
---------------------------------
`serveros/native-core-receipt.v1` (the shape `native/node-core/supervisor`'s
`receipt.cpp` writes) carries, and only carries:

    schema · mode(observe-only|active) · node · principal · trigger
    started_at · finished_at
    manifest{schema, generation, sha256}
    plan{ok, order[], actions[{service_id,unit,verb,reason}],
         refusals[{scope,code,reason}]}
    executed[{service_id,unit,verb,ok,code,reason,exit_code}]
    summary{actions,refusals,executed,failed}

It does NOT carry the recovery budget, the attempt count or the backoff ladder —
those live in the recovery module's own persisted state (`recovery.cpp`), and a
reader that invented them would be publishing a number nobody measured. So this
module reads what is there, reports `budget`/`backoff` as UNKNOWN unless a
producer puts them in an optional `recovery` block, and refuses rather than
filling the gap.

WHY A SUCCESSFUL VERB IS NOT A RECOVERY
---------------------------------------
`executed[i].ok == true` with `exit_code == 0` says *the Core asked systemd and
systemd said yes*. It does not say the SERVICE is working: a unit can start and
fail its own health check a second later, and «it came back» is a claim about
the service, not about the verb. So an executed row becomes a HANDLING event —
the loop is working on it — and **RECOVERED is emitted only from a readiness
TRANSITION** (`recovery_transitions`), where a service that was not READY is
READY on a LATER observation. The two are kept in two functions so neither can
borrow the other's evidence.

WHAT IS REFUSED (each raising `RecoveryRefused`)
------------------------------------------------
a wrong schema; a `node` that is not this device; a `principal` the caller did
not expect, or none at all (an unknown owner is not an owner); a stamp that
cannot be parsed or that is IN THE FUTURE; and a receipt older than the caller's
bound. A refusal here is a refusal to put these events on a card — it is never a
verdict that the service is fine.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


surface = _load("serveros_runtime_card_surface", _HERE / "card_surface.py")

RECEIPT_SCHEMA = "serveros/native-core-receipt.v1"

#: The mode words the receipt may carry, mirrored from the producer. A mode this
#: reader does not know is a receipt from a version it does not read, and
#: inferring «observe-only» from an empty action list would be the collapse the
#: producer's own comment warns against (an empty list means two different
#: things and the word is what tells them apart).
MODES = ("observe-only", "active")

#: The refusal codes, in the order the Core binds them, mirrored from
#: `bounded-recovery.v1.md`. The ORDER matters to a reader deciding what to fix
#: first, so it is kept here and a code outside this list is UNVERIFIED rather
#: than passed through as a word nobody defined.
REFUSAL_CODES = ("NO_LEASE", "MASKED", "INSTALL_STATE_UNKNOWN", "NOT_INSTALLED",
                 "LINK_DOWN", "AGENT_STATE_UNKNOWN", "AGENT_BUSY")

#: A refusal that is the Core CORRECTLY WAITING, versus one that is a finding
#: about the machine. The first is HANDLING — nothing to fix, the loop will try
#: again — and the second is ESCALATED, because a service that should exist and
#: does not is something a person has to look at.
TRANSIENT_REFUSALS = ("NO_LEASE", "AGENT_BUSY")

#: Readiness words, from `bounded-recovery.v1.md` §3: READY only for systemd's
#: own Active::Active; everything else — including a word this version does not
#: know — is NOT_READY, and an unreadable observation is UNMEASURED.
READINESS_WORDS = ("READY", "NOT_READY", "UNMEASURED")


class RecoveryRefused(RuntimeError):
    """A receipt this module will not turn into events."""


def _parse(value):
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _version(*parts) -> str:
    blob = json.dumps([str(p) for p in parts], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _event(kind: str, episode: str, version: str, identity: str, **extra) -> dict:
    """One surface event, in the shape `card_surface_runner` already carries.

    The same four required fields the digest adapter produces, so a Core event
    enters LIVE through the same door as an alert event and the card cannot tell
    (or need to tell) which producer it came from.
    """
    event = {"kind": kind, "episode": episode, "version": version,
             "source_row_key": identity}
    event.update(extra)
    return event


def admit_receipt(receipt, *, device_ref, now, max_age_s,
                  expected_owner=None) -> dict:
    """Validate one receipt against this device, or raise. PURE.

    Returns `(started, finished, mode, owner, trigger)` as a small dict so the
    two callers below share ONE admission rather than each re-checking a
    different subset — the failure mode being a second, weaker check.
    """
    if not isinstance(receipt, dict):
        raise RecoveryRefused(f"a receipt is an object, got {type(receipt).__name__}")
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise RecoveryRefused(
            f"schema {receipt.get('schema')!r} is not {RECEIPT_SCHEMA!r}")
    if receipt.get("node") != device_ref:
        raise RecoveryRefused(
            "the receipt names a different node than the device it would be "
            "shown under; a recovery filed against the wrong machine is worse "
            "than none, because it reads as that machine's")
    owner = str(receipt.get("principal") or "").strip()
    if not owner:
        raise RecoveryRefused(
            "the receipt names no principal; an unknown owner is not an owner, "
            "and attributing a recovery to nobody is how one account's action "
            "reaches another's card")
    if expected_owner is not None and owner != str(expected_owner):
        raise RecoveryRefused(
            "the receipt's principal is not the owner this surface is for")

    mode = receipt.get("mode")
    if mode not in MODES:
        raise RecoveryRefused(
            f"mode {mode!r} is not one of {MODES}; whether the Core was willing "
            f"to act is a word, not something to infer from an empty list")

    started, finished = _parse(receipt.get("started_at")), _parse(receipt.get("finished_at"))
    clock = _parse(now)
    if started is None or finished is None or clock is None:
        raise RecoveryRefused(
            "the receipt's started_at/finished_at or the caller's now is not a "
            "parseable zoned instant; a run that cannot be placed on a clock "
            "cannot be placed on a card")
    if started > clock or finished > clock:
        raise RecoveryRefused(
            "the receipt finishes in the future; a run that has not happened is "
            "not evidence about now, and clamping it to now would make any "
            "receipt fresh forever")
    age = (clock - finished).total_seconds()
    if age > max_age_s:
        raise RecoveryRefused(
            f"the receipt finished {int(age // 60)} 分钟前，超过上限 "
            f"{int(max_age_s // 60)} 分钟")
    return {"started": started, "finished": finished, "mode": mode,
            "owner": owner, "trigger": str(receipt.get("trigger") or "UNKNOWN")}


def recovery_events(receipt, *, device_ref, now, max_age_s,
                    expected_owner=None) -> tuple:
    """One admitted receipt -> `(events, unverified_count)` for LIVE.

    One event per REFUSAL and per EXECUTED row. Every version is derived from
    what the row ASSERTS (service, verb, code, exit status), so re-polling an
    unchanged receipt dedupes and a changed outcome patches — the same discipline
    `digest_version` keeps for alert rows. `budget`/`backoff` are reported as
    UNKNOWN because the receipt does not carry them; a producer that later adds a
    `recovery` block has its values carried through instead.
    """
    admitted = admit_receipt(receipt, device_ref=device_ref, now=now,
                             max_age_s=max_age_s, expected_owner=expected_owner)
    recovery = receipt.get("recovery") if isinstance(receipt.get("recovery"), dict) else {}
    events, unverified = [], 0

    def loop_fields(run_kind, name, code):
        return {"device_ref": device_ref, "run": run_kind, "service": name,
                "code": code, "mode": admitted["mode"],
                "trigger": admitted["trigger"], "owner": admitted["owner"],
                "started_at": receipt.get("started_at"),
                "finished_at": receipt.get("finished_at"),
                "budget": recovery.get("budget", "UNKNOWN"),
                "backoff_seconds": recovery.get("backoff_seconds", "UNKNOWN")}

    plan = receipt.get("plan") if isinstance(receipt.get("plan"), dict) else {}
    for refusal in (plan.get("refusals") or []):
        if not isinstance(refusal, dict):
            unverified += 1
            continue
        code = str(refusal.get("code") or "").upper()
        scope = str(refusal.get("scope") or "").strip()
        if not scope or code not in REFUSAL_CODES:
            # A refusal nobody can name cannot be deduped, and passing an
            # unknown code through would put a word on the card that no
            # contract defines.
            unverified += 1
            continue
        kind = surface.HANDLING if code in TRANSIENT_REFUSALS else surface.ESCALATED
        events.append(_event(
            kind, f"core-recovery:{device_ref}:{scope}",
            _version("refused", device_ref, scope, code, admitted["finished"]),
            f"core-refusal:{device_ref}:{scope}",
            **loop_fields("refused", scope, code)))

    for row in (receipt.get("executed") or []):
        if not isinstance(row, dict):
            unverified += 1
            continue
        service = str(row.get("service_id") or "").strip()
        verb = str(row.get("verb") or "").strip()
        if not service or not verb:
            unverified += 1
            continue
        # The verb's own words are the version: a re-run that came back
        # differently is a new fact, and a re-poll of the same one dedupes.
        events.append(_event(
            surface.HANDLING, f"core-recovery:{device_ref}:{service}",
            _version("executed", device_ref, service, verb, row.get("code"),
                     row.get("exit_code"), row.get("ok")),
            f"core-executed:{device_ref}:{service}:{verb}",
            **loop_fields("executed", service, str(row.get("code") or ""))))
    return events, unverified


def recovery_transitions(previous, current, *, device_ref) -> tuple:
    """Readiness across two observations -> `(events, unverified)`.

    THE ONLY SOURCE OF «RECOVERED». `previous` and `current` are
    `{service_id: readiness_word}` maps taken from two Core readings — not from
    a verb's exit code, which is a fact about the attempt and not about the
    service. A service that was NOT_READY and is now READY is RECOVERED; one
    that was READY and is not is ESCALATED; a word outside the closed set, or a
    service present in only one map, is UNVERIFIED rather than assumed either
    way.
    """
    events, unverified = [], 0
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise RecoveryRefused("both observations must be {service_id: word} maps")
    for service, now_word in current.items():
        word = str(now_word or "").upper()
        if word not in READINESS_WORDS:
            unverified += 1
            continue
        before = str(previous.get(service) or "").upper()
        if before not in READINESS_WORDS:
            # A service we did not see before has not «recovered»; it has
            # simply been seen. Saying RECOVERED would claim a crossing that
            # nobody observed.
            continue
        if before == word:
            continue
        kind = surface.RECOVERED if word == "READY" else surface.ESCALATED
        events.append(_event(
            kind, f"core-recovery:{device_ref}:{service}",
            _version("readiness", device_ref, service, before, word),
            f"core-readiness:{device_ref}:{service}",
            device_ref=device_ref, service=service, run="readiness",
            readiness=word, was=before))
    return events, unverified
