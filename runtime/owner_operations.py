"""Closed owner operations on the existing control ledger and Work database.

Lock order: AgentControlStore._mutation, then Work BEGIN IMMEDIATE. Owner
policy/context/grant mutations all take the former; every managed Work row
write requires the latter's exact connection scope. Work effects and receipts
commit together. No request carries a callback, SQL, actor, uid, clock or owner.
"""
from __future__ import annotations

import datetime
import hashlib
import json


class Refused(ValueError):
    def __init__(self, reason, detail):
        self.reason, self.detail = reason, detail
        super().__init__(f"{reason}: {detail}")


def _modules():
    import sys
    return (sys.modules["serveros_runtime_agent_control_store"],
            sys.modules["serveros_runtime_owner_policy"])


def _digest(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()


def _iso(now):
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat()


def _closed(request, required, optional=()):
    if not isinstance(request, dict) or not set(required) <= set(request) or set(request) - set(required) - set(optional):
        raise Refused("INVALID_OWNER_OPERATION", "exact operation fields are required")


def _ref(value):
    _, policy = _modules()
    try:
        return policy._reference(value, "reference")
    except ValueError as exc:
        raise Refused("INVALID_OWNER_OPERATION", str(exc)) from exc


def _context(data, context_ref, now):
    control, _ = _modules()
    actor = control._actor_module()
    context = data["contexts"].get(context_ref)
    try:
        actor.validate(context)
        verdict = actor.authorizes(context, now)
    except (ValueError, TypeError, KeyError) as exc:
        raise Refused("UNTRUSTED_ACTOR", "no usable trusted context") from exc
    if not verdict.get("authorizes"):
        raise Refused("UNTRUSTED_ACTOR", "fresh FACT/VERIFIED context required")
    return context


def _issuer(issuer, data, context_ref, peer, now):
    _, policy = _modules()
    if not isinstance(issuer, policy.OwnerPolicyIssuer):
        raise Refused("BLOCKED_OWNER_ISSUER", "no explicit trusted owner issuer is configured")
    context = _context(data, context_ref, now)
    if (policy.os_principal_of(peer) != issuer.os_principal
            or context["actor_ref"] != issuer.actor_ref
            or context["human_control_provider"] != issuer.human_control_provider):
        raise Refused("UNTRUSTED_ACTOR", "kernel peer and trusted context do not match configured issuer")
    return context


def policy_mutate(store, issuer, *, verb, request, peer, now):
    """One JSON mutation, including receipt; no Work database is changed."""
    _, policy = _modules()
    base = {"context_ref", "operation_id"}
    fields = {"issue": {"policy"}, "reissue": {"policy", "expected_epoch"},
              "revoke": {"policy_ref", "expected_epoch"}}
    if verb not in fields:
        raise Refused("INVALID_OWNER_OPERATION", "unknown policy verb")
    _closed(request, base | fields[verb])
    operation_id = _ref(request["operation_id"])
    digest = _digest({"verb": verb, **request})
    with store._mutation():
        data = store._data
        _issuer(issuer, data, request["context_ref"], peer, now())
        receipts = data.setdefault("owner_policy_receipts", {})
        policies = data.setdefault("owner_policies", {})
        if operation_id in receipts:
            existing = receipts[operation_id]
            if existing["digest"] != digest:
                raise Refused("IDEMPOTENCY_CONFLICT", "operation key has different policy intent")
            # Historical policy receipts never reinstate old live authority.
            return {**existing["answer"], "replayed": True, "historical": True}
        if verb == "revoke":
            ref = _ref(request["policy_ref"])
            payload = None
        else:
            payload = policy.validate_policy_request(request["policy"], now=now())
            ref = payload["policy_ref"]
        old = policies.get(ref)
        if verb == "issue":
            if old is not None:
                raise Refused("POLICY_EXISTS", "reissue requires the current policy epoch")
            epoch = 1
        else:
            expected = request["expected_epoch"]
            if type(expected) is not int or expected < 1 or old is None or old["epoch"] != expected:
                raise Refused("POLICY_EPOCH_STALE", "current policy epoch must match")
            epoch = old["epoch"] + 1
        stamped = now()
        record = {**(old if verb == "revoke" else payload),
                  "revoked": verb == "revoke", "epoch": epoch,
                  "issued_by": issuer.actor_ref, "issued_at": stamped}
        _issuer(issuer, data, request["context_ref"], peer, now())
        policies[ref] = record
        answer = {"committed": True, "replayed": False, "policy": record,
                  "operation_id": operation_id, "historical": False}
        receipts[operation_id] = {"digest": digest, "answer": answer,
                                  "os_principal": issuer.os_principal}
        store._flush()
        return json.loads(json.dumps(answer))


class LedgerPolicyResolver:
    """Read only the policies already issued in this existing control ledger."""
    def __init__(self, store):
        self.store = store

    def resolve(self, *, policy_ref, grant, context, peer, project, item_ref, action, now):
        _, policy = _modules()
        store = self.store() if callable(self.store) else self.store
        records = store._read().get("owner_policies", {})
        if policy_ref:
            row = records.get(policy_ref)
            return policy.OwnerPolicy(**row) if row is not None else None
        matches = []
        for row in records.values():
            candidate = policy.OwnerPolicy(**row)
            if policy.check_issue_policy(grant=grant, context=context, peer=peer, now=now,
                                         policy=candidate) is None:
                matches.append(candidate)
        # An ambiguous mapping never chooses whichever policy happened to be first.
        return matches[0] if len(matches) == 1 else None


class OwnerWorkOperations:
    """Trusted daemon-side producer. Inputs select references, never identity."""
    BASE = {"context_ref", "grant_ref", "project", "item_ref", "expected_epoch", "operation_id", "verb"}
    ARGUMENTS = {"lease.acquire": {"ttl_s"}, "lease.renew": {"token", "ttl_s"},
                 "lease.release": {"token"}, "item.transition": {"token", "target"},
                 "item.reopen": {"token", "reason", "source"}, "item.outcome": {"token", "outcome"},
                 "activity.add": {"token", "activity_ref", "kind"},
                 "activity.transition": {"token", "activity_ref", "target"},
                 "context.attach": {"token", "activity_ref", "repo", "mode", "checkpoint_ref"},
                 "run.begin": {"token", "activity_ref", "idempotency_key", "worker_ref"},
                 "run.advance": {"token", "run_ref", "target"},
                 "run.terminate": {"token", "run_ref", "reason"}}
    OPTIONAL = {"item.transition": {"note", "reason", "return_state", "unblock_condition"},
                "activity.add": {"needs"},
                "context.attach": {"expected_revision", "expected_worktree", "expected_dirty"},
                "run.terminate": {"reason_code", "target", "evidence", "operation", "acknowledge_active_worker", "dry_run"}}

    def __init__(self, control_store, work_store):
        self.control = control_store
        self.work = work_store

    def _authorize(self, data, request, peer):
        control, policy = _modules()
        now = _iso(self.work.clock())
        context = _context(data, request["context_ref"], now)
        action = request["verb"] if request["verb"].startswith("lease.") else "work.commit"
        verified = control.verify_live_grant(
            data["grants"], context=context, peer=peer, grant_ref=request["grant_ref"],
            project=request["project"], item_ref=request["item_ref"], action=action,
            expected_epoch=request["expected_epoch"], now=now)
        if not verified.get("verified"):
            raise Refused(verified["reason"], verified["because"])
        grant = data["grants"][request["grant_ref"]]
        row = data.get("owner_policies", {}).get(grant.get("owner_policy_ref"))
        owned = policy.OwnerPolicy(**row) if row is not None else None
        refusal = policy.check_policy_binding(grant=grant, context=context, peer=peer,
            project=request["project"], item_ref=request["item_ref"], action=action,
            now=now, policy=owned)
        if refusal is not None:
            raise Refused(refusal["reason"], refusal["because"])
        return grant, context, owned

    def _lease(self, conn, request, grant):
        row = self.work._require_live_owner_lease(conn, item_ref=request["item_ref"],
                                                principal=grant["actor_ref"], token=request["token"])
        acquired = conn.execute("SELECT receipt_json FROM owner_operation_receipts "
                                "WHERE item_ref=? AND verb='lease.acquire' ORDER BY rowid DESC LIMIT 1",
                                (request["item_ref"],)).fetchone()
        if acquired is None:
            raise Refused("OWNER_LEASE_BINDING_UNAVAILABLE", "this lease has no owner acquisition receipt")
        receipt = json.loads(acquired[0])
        if (receipt["grant_ref"] != grant["grant_ref"] or receipt["grant_epoch"] != grant["epoch"]
                or receipt["policy_epoch"] != grant["owner_policy_epoch"]
                or receipt["result"]["token"] != request["token"]):
            raise Refused("STALE_OWNER_LEASE", "lease acquisition belongs to another authority generation")
        return dict(row)

    def inspect_target(self, request, *, peer):
        """Authenticated routing read for the exact intended operation scope."""
        _closed(request, self.BASE - {"operation_id"})
        if self.work is None:
            raise Refused("BLOCKED_OWNER_COMMIT_SEAM", "no explicit Work store is bound")
        if request["verb"] not in self.ARGUMENTS:
            raise Refused("UNSUPPORTED_OWNER_OPERATION", "unsupported intended operation")
        with self.control._mutation():
            self._authorize(self.control._data, request, peer)
            with self.work.transaction(immediate=True) as conn:
                self._authorize(self.control._data, request, peer)
                item = conn.execute("SELECT project FROM items_index WHERE item_id=?", (request["item_ref"],)).fetchone()
                if item is None or item[0] != request["project"]:
                    raise Refused("ITEM_MISMATCH", "Item/project mismatch")
                return {"verified": True, **self.work.owner_target_info()}

    def mutate(self, request, *, peer):
        if self.work is None:
            raise Refused("BLOCKED_OWNER_COMMIT_SEAM", "no explicit Work store is bound")
        verb = request.get("verb") if isinstance(request, dict) else None
        if verb not in self.ARGUMENTS:
            raise Refused("UNSUPPORTED_OWNER_OPERATION", "no protected implementation for this operation")
        _closed(request, self.BASE | self.ARGUMENTS[verb] | {"expected_store_binding"}, self.OPTIONAL.get(verb, ()))
        for key in self.BASE - {"expected_epoch"}:
            _ref(request[key])
        if type(request["expected_epoch"]) is not int or request["expected_epoch"] < 1:
            raise Refused("INVALID_OWNER_OPERATION", "positive grant epoch required")
        for field in ("dry_run", "acknowledge_active_worker", "expected_dirty"):
            if field in request and type(request[field]) is not bool:
                raise Refused("INVALID_OWNER_OPERATION", field + " must be boolean")
        digest = _digest({k: v for k, v in request.items() if k != "expected_store_binding"})
        # No Work mutation or authority decision can escape this lock order.
        with self.control._mutation():
            data = self.control._data
            grant, context, policy = self._authorize(data, request, peer)
            if self.work.owner_target_info()["store_binding"] != request["expected_store_binding"]:
                raise Refused("WORK_STORE_MISMATCH", "owner target binding differs")
            with self.work.transaction(immediate=True) as conn:
                # Re-age after waiting for SQLite. A long lock wait cannot
                # turn an expired context/grant/policy into a valid writer.
                grant, context, policy = self._authorize(data, request, peer)
                if self.work.owner_target_info()["store_binding"] != request["expected_store_binding"]:
                    raise Refused("WORK_STORE_MISMATCH", "owner target changed while acquiring transaction")
                item = conn.execute("SELECT project FROM items_index WHERE item_id=?", (request["item_ref"],)).fetchone()
                if item is None or item["project"] != request["project"]:
                    raise Refused("ITEM_MISMATCH", "Work Item does not belong to the named project")
                dry_run = verb == "run.terminate" and request.get("dry_run") is True
                old = None if dry_run else conn.execute("SELECT digest,receipt_json FROM owner_operation_receipts WHERE operation_id=?",
                                   (request["operation_id"],)).fetchone()
                if old is not None:
                    if old["digest"] != digest:
                        raise Refused("IDEMPOTENCY_CONFLICT", "operation key has different intent")
                    receipt = json.loads(old["receipt_json"])
                    if receipt["os_principal"] != grant["os_principal"]:
                        raise Refused("UNTRUSTED_ACTOR", "receipt belongs to another kernel principal")
                    # Historical reconciliation deliberately precedes _lease:
                    # released/expired/superseded tokens can read their old
                    # result under CURRENT context/grant/policy/scope checks.
                    # This returns no current writer capability. Control-only
                    # receipts below retain their current-lease requirement.
                    return {"committed": True, "replayed": True, "historical": True,
                            "result": receipt["result"], "receipt": receipt}
                if verb != "lease.acquire":
                    self._lease(conn, request, grant)
                if dry_run:
                    arguments = {key: value for key, value in request.items() if key not in self.BASE | {"expected_store_binding"}}
                    result = self.work._apply_owner_operation(conn, verb=verb, item_ref=request["item_ref"],
                                                              principal=grant["actor_ref"], arguments=arguments)
                    self._authorize(data, request, peer)
                    self._lease(conn, request, grant)
                    return {"committed": False, "dry_run": True, "result": result, "operation_id": request["operation_id"]}
                conn.create_function("owner_commit_scope", 1,
                                     lambda item: int(item == request["item_ref"]))
                with self.work._owner_scope(conn, request["item_ref"]):
                    arguments = {key: value for key, value in request.items() if key not in self.BASE | {"expected_store_binding"}}
                    if verb == "run.begin":
                        for key in ("activity_ref", "idempotency_key", "worker_ref"):
                            _ref(request[key])
                        arguments.update(artifact_root=getattr(self.work, "owner_artifacts_root", None),
                                         allocation={"operation_id": request["operation_id"], "digest": digest})
                    if verb == "context.attach":
                        arguments = {"token": request["token"], "context": self.work.owner_execution_context(request, data, grant)}
                    result = self.work._apply_owner_operation(conn, verb=verb, item_ref=request["item_ref"],
                                                              principal=grant["actor_ref"], arguments=arguments)
                    receipt = {"operation_id": request["operation_id"], "verb": verb,
                               "item_ref": request["item_ref"], "project": request["project"],
                               "context_ref": context["context_ref"], "grant_ref": grant["grant_ref"],
                               "grant_epoch": grant["epoch"], "policy_ref": policy.policy_ref,
                               "policy_epoch": policy.epoch, "os_principal": grant["os_principal"],
                               "actor_ref": grant["actor_ref"], "committed_at": _iso(self.work.clock()),
                               "result": result}
                    conn.execute("INSERT INTO owner_operation_receipts VALUES (?,?,?,?,?)",
                                 (request["operation_id"], request["item_ref"], verb, digest,
                                  json.dumps(receipt, sort_keys=True)))
                    self._authorize(data, request, peer)
                    if verb != "lease.release":
                        token = result["token"] if "token" in result else request["token"]
                        self._lease(conn, {**request, "token": token}, grant)
                # Both changes commit on exiting this one SQLite transaction;
                # the control ledger is read while locked, never dual-written.
            return {"committed": True, "replayed": False, "historical": False,
                    "result": result, "receipt": receipt}

    def control_commit(self, request, *, peer, mutation):
        """In-process control-only producer seam for Task 4.

        `mutation` is trusted owner code, NEVER deserialized or sent over RPC.
        It receives a private copy of the current ledger; only admissions and
        checkpoints may change. The producer must validate its specific
        binding/dependency semantics. It receives no SQLite connection.

        Binding and receipt flush together in JSON while the Work transaction
        holds lease stability. SQLite is not written; a failed/restarted caller
        reconciles the same JSON receipt. This is not a cross-store commit or
        permission to send an external message after the locks are released.
        """
        if self.work is None:
            raise Refused("BLOCKED_OWNER_COMMIT_SEAM", "no explicit Work store is bound")
        required = (self.BASE - {"verb"}) | {"token", "intent"}
        _closed(request, required)
        for key in ("context_ref", "grant_ref", "project", "item_ref", "operation_id"):
            _ref(request[key])
        if not isinstance(request["intent"], dict) or len(json.dumps(request["intent"])) > 16384:
            raise Refused("INVALID_OWNER_OPERATION", "bounded binding intent required")
        digest = _digest(request)
        authority_request = {**request, "verb": "item.transition"}
        with self.control._mutation():
            data = self.control._data
            grant, context, policy = self._authorize(data, authority_request, peer)
            with self.work.transaction(immediate=True) as conn:
                grant, context, policy = self._authorize(data, authority_request, peer)
                item = conn.execute("SELECT project FROM items_index WHERE item_id=?", (request["item_ref"],)).fetchone()
                if item is None or item["project"] != request["project"]:
                    raise Refused("ITEM_MISMATCH", "Work Item does not belong to the named project")
                self._lease(conn, request, grant)
                receipts = data.setdefault("owner_control_receipts", {})
                old = receipts.get(request["operation_id"])
                if old is not None:
                    if old["digest"] != digest:
                        raise Refused("IDEMPOTENCY_CONFLICT", "binding operation key has different intent")
                    return {"committed": True, "replayed": True, "historical": True,
                            "result": old["result"], "receipt": old}
                working = json.loads(json.dumps(data))
                result = mutation(working)
                if any(working.get(key) != data.get(key) for key in set(data) | set(working)
                       if key not in {"admissions", "checkpoints"}):
                    raise Refused("UNSUPPORTED_DUAL_WRITE", "control producer may change only admissions/checkpoints")
                if not all(isinstance(working.get(key), dict) for key in ("admissions", "checkpoints")):
                    raise Refused("INVALID_OWNER_OPERATION", "control collections must remain mappings")
                self._authorize(data, authority_request, peer)
                self._lease(conn, request, grant)
                receipt = {"operation_id": request["operation_id"], "digest": digest,
                           "item_ref": request["item_ref"], "grant_ref": grant["grant_ref"],
                           "grant_epoch": grant["epoch"], "policy_epoch": policy.epoch,
                           "token": request["token"], "result": result,
                           "committed_at": _iso(self.work.clock())}
                data["admissions"] = working["admissions"]
                data["checkpoints"] = working["checkpoints"]
                receipts[request["operation_id"]] = receipt
                self.control._flush()
            return {"committed": True, "replayed": False, "historical": False,
                    "result": result, "receipt": receipt}


def refusal_answer(exc):
    if isinstance(exc, Refused):
        return {"committed": False, "reason": exc.reason, "detail": exc.detail}
    if hasattr(exc, "reason_code") and hasattr(exc, "detail"):
        return {"committed": False, "reason": exc.reason_code, "detail": exc.detail}
    return {"committed": False, "reason": "INVALID_OWNER_OPERATION", "detail": str(exc)}
