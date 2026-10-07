#!/usr/bin/env python3
"""The operator's trust root for durable delegation: author it, check it, and
say whether the durable route is reachable.

`context/actor.verify_delegation` already decides whether a signed scope is
acceptable. What it cannot do is tell its caller *why* the durable route is
closed on a given node, because the answer is usually "nobody has written the
binding yet" — a state, not a refusal. This module is that answer, plus the
one writer for the file the verifier reads.

**The private half is the whole security property.** The binding holds public
keys. A signature under one of them is evidence that an owner approved an exact
scope only while the key that produced it is one *this execution cannot use*.
A key stored somewhere the execution can read is a key the execution can sign
with, and a signature the execution can produce is not evidence that a human
approved anything. That single sentence is why this module

  * has no key generator and no code path that reads a private key,
  * refuses a custodial claim that resolves to the uid this process runs as,
    rather than merely recording the claim, and
  * reports `SAME_UID_AS_EXECUTION` as a reachability outcome rather than as a
    warning attached to a working configuration.

None of that makes the file trustworthy on its own. On a host where every
participant runs as one uid, the tool can only *name* the custody; it cannot
manufacture the separation. Naming it is the point: an unreachable durable
route is a fact a person can act on, and an unstated one is the fact that gets
mistaken for "one human turn away".

Read `context/actor-context-contract.yaml` first; the freshness numbers and the
grammar of a provider report belong to it and are not repeated here.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import pwd
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# The schema string lives in `actor.py` next to the verifier that requires it.
# Read from there rather than written again here, so a binding this module
# accepts and a binding `verify_delegation` reads cannot drift apart.
TRUST_SCHEMA = "serveros/human-control-trust.v1"

# What a custodian may be. Closed, so a typo is refused rather than recorded as
# a kind nothing reads.
CUSTODY_KINDS = ("unix-user", "external-system", "hardware-token")

# Resolution outcomes. `UNVERIFIED` is not a failure and not a success: it is
# what a claim about a principal off this host can honestly become when nothing
# here can look it up.
SEPARATE = "SEPARATE_PRINCIPAL"
SAME_UID = "SAME_UID_AS_EXECUTION"
UNVERIFIED = "UNVERIFIED"

# The fields each level may carry. Unknown ones are refused rather than
# ignored: `audiance` is one keystroke from `audience` and would otherwise
# produce a binding that verifies nothing while looking complete.
TOP_FIELDS = {"schema", "audience", "providers"}
PROVIDER_FIELDS = {"keys"}
KEY_FIELDS = {"enabled", "public_key", "custodian"}
CUSTODIAN_FIELDS = {"kind", "name"}


def _actor_module():
    """`context/actor.py`, loaded by path — the same idiom its siblings use."""
    name = "serveros_context_actor"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / "actor.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def trust_path() -> Path:
    """Where the binding lives, from the verifier's own constant.

    Not a second copy of the path: a writer and a reader that disagree about
    the location would produce a file that verifies nothing and a verifier that
    reports an absent binding, with both processes behaving correctly.
    """
    return Path(_actor_module().DELEGATION_TRUST)


class Refused(ValueError):
    """This module will not do what was asked, and the message says which rule."""


def _load(module, path: Path):
    """The verifier's own reader, called for its refusals rather than its value.

    `delegation_trust()` already refuses an absent file with
    `DELEGATION_PROVIDER_UNBOUND` and a wrong schema with its own sentence. A
    second implementation of "is this file valid" here is the copy that drifts,
    so the verdict below asks the verifier and reports what it said.

    The path is passed through rather than assumed: a verb that reads one file
    and writes another would report a state about neither. `verify_delegation`
    reads the operator's own location, so a fixture path is a shape reading —
    which is exactly what this verb answers.
    """
    try:
        return module.delegation_trust(path), None
    except module.Rejected as exc:
        return None, str(exc)


def resolve_custody(custodian: dict) -> tuple[str, str]:
    """(state, because) for one custodial claim.

    A `unix-user` claim is checkable here and therefore IS checked: the same
    uid is reported as shared even though the operator wrote the claim down,
    because a claim that contradicts what this host measures is exactly the
    claim that must not be recorded as satisfied. Everything else is
    `UNVERIFIED` — not out of caution, but because "an external signer really
    holds that key" is not a fact this process can look up.
    """
    kind = custodian["kind"]
    name = custodian["name"]
    if kind != "unix-user":
        return UNVERIFIED, (
            f"custodian is {kind} {name!r}, which nothing on this host can look "
            f"up — the separation is claimed and cannot be measured here")
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return UNVERIFIED, (
            f"custodian names unix user {name!r}, which does not exist on this "
            f"host — an unresolvable custodian is not a separate one")
    if entry.pw_uid == os.getuid():
        return SAME_UID, (
            f"custodian {name!r} is uid {entry.pw_uid}, the uid this execution "
            f"runs as: a key that user can read, this process can sign with, so "
            f"a signature under it is not evidence of human approval")
    return SEPARATE, (
        f"custodian {name!r} is uid {entry.pw_uid}, distinct from this "
        f"execution's uid {os.getuid()}")


def _keys(document: dict) -> list[dict]:
    rows = []
    providers = document.get("providers")
    for provider_id in sorted(providers or {}):
        keys = (providers[provider_id] or {}).get("keys") or {}
        for key_id in sorted(keys):
            entry = keys[key_id] or {}
            custodian = entry.get("custodian") or {}
            state, because = resolve_custody(custodian) if set(custodian) == CUSTODIAN_FIELDS \
                else (UNVERIFIED, "no custodian is recorded for this key, so nothing "
                                  "states who holds the private half")
            rows.append({"provider_id": provider_id, "key_id": key_id,
                         "enabled": entry.get("enabled") is True,
                         "custody": state, "custody_because": because})
    return rows


def verdict(path: Path | None = None) -> dict:
    """Everything an operator needs to decide what to do next, as a state word.

    Absence is a state (`ABSENT`), not an exception: the common reading on a
    fresh node is "no binding has been written", and a stack trace reports that
    as a fault. Malformation is also a state (`INVALID`) carried with the
    verifier's own sentence, so the reader learns which rule the file broke.
    """
    module = _actor_module()
    target = Path(path) if path is not None else trust_path()
    if not target.exists():
        document, because, binding = None, f"no trust binding at {target}", "ABSENT"
    else:
        document, because = _load(module, target)
        binding = "PRESENT" if document is not None else "INVALID"
        if document is not None:
            because = (f"the binding parses under the verifier's own reader; "
                       f"whether a signature under it means anything is the "
                       f"custody question below")

    rows = _keys(document) if document is not None else []
    enabled = [row for row in rows if row["enabled"]]

    # Blocking conditions are separated from the conditional one, because
    # "measured to be the execution's own uid" and "nothing here can measure
    # this" are different answers and a reader who got one word for both would
    # act on the wrong one. `missing` names everything standing between the
    # binding and an unconditional route; the state word below says which of
    # them is hard.
    missing = []
    if binding == "ABSENT":
        missing.append("TRUST_BINDING_ABSENT")
    elif binding == "INVALID":
        missing.append("TRUST_BINDING_INVALID")
    if binding == "PRESENT" and not enabled:
        missing.append("NO_ENABLED_KEY")
    if enabled and not any(row["custody"] == SEPARATE for row in enabled):
        missing.append("CUSTODY_SHARED_WITH_EXECUTION" if any(
            row["custody"] == SAME_UID for row in enabled) else "CUSTODY_UNMEASURED")

    if set(missing) - {"CUSTODY_UNMEASURED"}:
        route = "UNREACHABLE"
    elif missing:
        # The machinery would accept a signature under this key; whether the
        # signature means anything is the part nobody here measured.
        route = "REACHABLE_IF_CUSTODY_HOLDS"
    else:
        route = "REACHABLE"

    contract = module.contract()["freshness"]
    return {
        "path": str(target),
        "binding": binding,
        "because": because,
        "audience": (document or {}).get("audience", ""),
        "keys": rows,
        "durable_route": route,
        "missing": missing,
        "execution_uid": os.getuid(),
        # The reachable alternative, from the contract that owns the numbers.
        # Reported beside the durable route so that "unreachable" is never read
        # as "nothing can be authorized right now".
        "legacy_route": {
            "basis": "trusted-context",
            "authorizes_for_seconds": int(contract["stale_after_seconds"]),
            "displayable_for_seconds": int(contract["window_seconds"]),
            "because": ("a grant with no signed issuance derives from a context "
                        "and is re-judged at read time, so it is in force only "
                        "while that context is FRESH"),
        },
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _public_key_bytes(value: str, *, origin: str) -> bytes:
    """32 raw Ed25519 public-key bytes, or a named refusal.

    The refusals here are the module's one hard line. A private key reaches
    this function only by accident or by someone trying to make the binding
    self-sufficient, and both are worth a sentence rather than a stack trace:
    the first teaches, the second is recorded.
    """
    text = str(value).strip()
    if "PRIVATE" in text.upper() or "BEGIN" in text.upper():
        raise Refused(
            f"{origin} carries PEM text. This verb binds a PUBLIC key and has "
            f"no path that reads a private half: a binding this tool could "
            f"complete from a secret it was handed is a binding this tool could "
            f"mint for itself.")
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise Refused(f"{origin} is not base64: {exc}") from exc
    if len(raw) != 32:
        raise Refused(
            f"{origin} decodes to {len(raw)} bytes; an Ed25519 public key is "
            f"exactly 32. A 64-byte value is a libsodium secret key and will "
            f"not be accepted as a public one.")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - environment guarantee
        raise Refused(f"Ed25519 support is required: {exc}") from exc
    try:
        Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise Refused(f"{origin} is not a usable Ed25519 point: {exc}") from exc
    return raw


def _custodian(kind: str, name: str) -> dict:
    if kind not in CUSTODY_KINDS:
        raise Refused(f"{kind!r} is not a custodian kind; known: " + ", ".join(CUSTODY_KINDS))
    if not str(name).strip():
        raise Refused("a custodian needs a name: a kind with nobody attached "
                      "records a claim about no one")
    custodian = {"kind": kind, "name": str(name)}
    state, because = resolve_custody(custodian)
    if state == SAME_UID:
        raise Refused(
            f"refusing to bind a key whose custodian is this execution's own "
            f"uid. {because}. The durable route would accept signatures under "
            f"this key and they would not mean what §12 says they mean, which "
            f"is worse than the route staying closed. Bind a key held by a "
            f"separate principal, or use the trusted-context route.")
    return custodian


def _read_existing(target: Path) -> dict | None:
    if not target.exists():
        return None
    module = _actor_module()
    document, because = _load(module, target)
    if document is None:
        raise Refused(
            f"{target} exists and does not verify ({because}). Refusing to "
            f"rewrite a file a person wrote: repair or move it first.")
    unknown = set(document) - TOP_FIELDS
    if unknown:
        raise Refused(f"{target} carries unknown top-level fields: "
                      + ", ".join(sorted(unknown)))
    return document


def installed(provider_id: str, key_id: str, *, path: Path | None = None) -> dict:
    """One key entry, on its way into the document. Shape only."""
    for label, value in (("provider", provider_id), ("key id", key_id)):
        if not str(value).strip():
            raise Refused(f"a {label} is required: a key with no name cannot be "
                          f"selected by a signed payload, and an unnamed provider "
                          f"cannot be matched against one")
    document = _read_existing(Path(path) if path is not None else trust_path())
    providers = dict((document or {}).get("providers") or {})
    return {"document": document or {}, "providers": providers,
            "keys": dict((providers.get(provider_id) or {}).get("keys") or {})}


def bind(provider_id: str, key_id: str, public_key: str, *, audience: str,
         custodian_kind: str, custodian_name: str, replace: bool = False,
         path: Path | None = None) -> dict:
    """Merge one trusted key into the binding, atomically, and read it back.

    Merges rather than replaces: a second provider added by a later call must
    not silently drop the first, and a verb that rewrote the whole document
    would make every binding call a destructive one. `replace` governs exactly
    one thing — whether an entry that is already there may be overwritten — and
    is required even then.
    """
    target = Path(path) if path is not None else trust_path()
    raw = _public_key_bytes(public_key, origin="--public-key")
    custodian = _custodian(custodian_kind, custodian_name)
    state = installed(provider_id, key_id, path=target)
    document, providers, keys = state["document"], state["providers"], state["keys"]

    held_audience = (document or {}).get("audience", "")
    if held_audience and held_audience != audience:
        raise Refused(
            f"this binding's audience is {held_audience!r} and {audience!r} was "
            f"given. The audience is what a signed payload must name, so "
            f"changing it silently unbinds every key already trusted here.")
    if not str(audience).strip():
        raise Refused("an audience is required: a signed payload carrying one "
                      "that matches nothing here would be refused by the "
                      "verifier with no way to tell why")
    if key_id in keys and not replace:
        raise Refused(
            f"provider {provider_id!r} already has key {key_id!r}. Replacing a "
            f"trusted key is how one gets rotated and also how one gets "
            f"substituted; pass --replace to say which of the two you mean.")

    keys[key_id] = {"enabled": True,
                    "public_key": base64.b64encode(raw).decode(),
                    "custodian": custodian}
    providers[provider_id] = {"keys": keys}
    document = {"schema": TRUST_SCHEMA, "audience": audience, "providers": providers}
    _write(target, document)

    result = verdict(target)
    if result["binding"] != "PRESENT":
        raise Refused(f"the binding written to {target} does not verify: "
                      f"{result['because']}")
    return result


def disable(provider_id: str, key_id: str, *, path: Path | None = None) -> dict:
    """Turn one key off without touching anything else in the file.

    Present so that the answer to "this key may have leaked" is a named verb
    rather than a hand edit — a hand edit to a security file is the change
    nobody can review afterwards.
    """
    target = Path(path) if path is not None else trust_path()
    state = installed(provider_id, key_id, path=target)
    keys = state["keys"]
    if key_id not in keys:
        raise Refused(f"no key {key_id!r} under provider {provider_id!r} in {target}")
    keys[key_id]["enabled"] = False
    providers = state["providers"]
    providers[provider_id] = {"keys": keys}
    document = {"schema": TRUST_SCHEMA,
                "audience": state["document"].get("audience", ""),
                "providers": providers}
    _write(target, document)
    return verdict(target)


def _write(target: Path, document: dict) -> None:
    """Atomic, same-directory, 0644. Public material, so world-readable is right.

    The temp file is created in the target's own directory so the replace is a
    rename within one filesystem; a partial write elsewhere would leave the
    verifier reading a file that is half of two bindings.
    """
    parent = target.parent
    if not parent.exists():
        parent.mkdir(parents=True, mode=0o700)
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    handle, temporary = tempfile.mkstemp(dir=str(parent), prefix=".trust-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# CLI — `serverosctl work trust {status,bind,disable}`
# ---------------------------------------------------------------------------

def render(result: dict) -> list[str]:
    lines = [f"binding       {result['binding']} — {result['because']}",
             f"path          {result['path']}",
             f"durable route {result['durable_route']}"]
    if result["missing"]:
        lines.append("missing       " + ", ".join(result["missing"]))
    if result["audience"]:
        lines.append(f"audience      {result['audience']}")
    for row in result["keys"]:
        lines.append(f"  key         {row['provider_id']} / {row['key_id']} "
                     f"enabled={'yes' if row['enabled'] else 'no'} "
                     f"custody={row['custody']}")
        lines.append(f"    because   {row['custody_because']}")
    legacy = result["legacy_route"]
    lines.append(f"legacy route  {legacy['basis']}: authorizes for "
                 f"{legacy['authorizes_for_seconds']}s after the human turn, "
                 f"displayable for {legacy['displayable_for_seconds']}s")
    return lines


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    show = sub.add_parser("status", help="read the binding and say whether the durable route is open")
    show.add_argument("--path", type=Path, default=None)
    show.add_argument("--json", action="store_true")
    both = sub.add_parser("bind", help="merge one trusted provider key into the binding")
    both.add_argument("--provider", required=True)
    both.add_argument("--key-id", required=True, dest="key_id")
    both.add_argument("--public-key", required=True, dest="public_key")
    both.add_argument("--audience", required=True)
    both.add_argument("--custodian-kind", required=True, dest="custodian_kind",
                      choices=CUSTODY_KINDS)
    both.add_argument("--custodian-name", required=True, dest="custodian_name")
    both.add_argument("--replace", action="store_true")
    both.add_argument("--path", type=Path, default=None)
    both.add_argument("--json", action="store_true")
    off = sub.add_parser("disable", help="turn one trusted key off, changing nothing else")
    off.add_argument("--provider", required=True)
    off.add_argument("--key-id", required=True, dest="key_id")
    off.add_argument("--path", type=Path, default=None)
    off.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.verb == "status":
            result = verdict(args.path)
        elif args.verb == "bind":
            result = bind(args.provider, args.key_id, args.public_key,
                          audience=args.audience, custodian_kind=args.custodian_kind,
                          custodian_name=args.custodian_name, replace=args.replace,
                          path=args.path)
        else:
            result = disable(args.provider, args.key_id, path=args.path)
    except Refused as exc:
        sys.stderr.write(f"trust_binding: {exc}\n")
        return 3
    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        sys.stdout.write("\n".join(render(result)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
