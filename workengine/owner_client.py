"""Explicit original-process mutation routing shared by CLI, facade and MCP.

This module reads only an existing local database before contacting the owner.
It neither constructs schemas nor discovers sockets/grants. A refusal is final;
None is returned only for an unmanaged call with no owner binding/envelope.
"""
from contextlib import closing
from pathlib import Path
import sqlite3

from .authority import AuthorityError
from .owner_fence import file_identity


class OwnerRouter:
    FIELDS = {"context_ref", "grant_ref", "expected_epoch", "token", "operation_id"}

    def __init__(self, *, state_dir, owner_client=None, envelope=None, artifacts_root=None):
        self.path = Path(state_dir) / "serveros.db"
        self.client = owner_client
        self.envelope = dict(envelope or {})
        self.artifacts_root = artifacts_root
        self.binding = None
        self.identity = None

    def mutate(self, item_ref, verb, **arguments):
        explicit = self.client is not None or bool(self.envelope)
        if not self.path.is_file() and not explicit:
            return None
        identity = file_identity(self.path)
        if self.identity is not None and identity != self.identity:
            raise AuthorityError("WORK_STORE_MISMATCH", "selected file changed; explicit client rebind required")
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT project FROM items_index WHERE item_id=?", (item_ref,)).fetchone()
            if row is None:
                raise KeyError(f"unknown Item: {item_ref}")
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='owner_operation_receipts'").fetchone()
            managed = bool(exists and conn.execute("SELECT 1 FROM owner_operation_receipts WHERE item_ref=? AND verb='lease.acquire' LIMIT 1", (item_ref,)).fetchone())
        if not explicit and not managed:
            return None
        required = self.FIELDS - ({"token"} if verb == "lease.acquire" else set())
        if set(self.envelope) - self.FIELDS or any(self.envelope.get(k) is None or self.envelope.get(k) == "" for k in required):
            raise AuthorityError("OWNER_CONTEXT_REQUIRED", "complete context/grant/epoch/token and retained operation_id required")
        if self.client is None:
            raise AuthorityError("OWNER_CLIENT_REQUIRED", "explicit owner socket required for this mutation")
        request = {**self.envelope, "project": row[0], "item_ref": item_ref, "verb": verb}
        inspect = {k: request[k] for k in ("context_ref", "grant_ref", "project", "item_ref", "expected_epoch", "verb")}
        target = self.client.inspect_target(**inspect)
        if target.get("verified") is not True:
            raise AuthorityError(target.get("reason", "OWNER_TARGET_REFUSED"), target.get("detail", "owner refused target inspection"))
        if identity != target.get("file_identity") or file_identity(self.path) != identity:
            raise AuthorityError("WORK_STORE_MISMATCH", "selected local file differs from owner file")
        if self.binding is not None and target.get("store_binding") != self.binding:
            raise AuthorityError("WORK_STORE_MISMATCH", "owner binding changed; explicit rebind required")
        if self.artifacts_root is not None and str(Path(self.artifacts_root).resolve()) != target.get("owner_artifacts_root"):
            raise AuthorityError("WORK_STORE_MISMATCH", "selected artifact root differs from owner assembly")
        self.identity, self.binding = identity, target["store_binding"]
        answer = self.client.mutate(**request, **arguments, expected_store_binding=self.binding)
        if answer.get("state") in {"UNKNOWN", "NOT_SENT"}:
            return answer
        if answer.get("committed") is not True and answer.get("dry_run") is not True:
            raise AuthorityError(answer.get("reason", "OWNER_MUTATION_REFUSED"), answer.get("detail", "owner refused mutation"))
        return answer
