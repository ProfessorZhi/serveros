"""Bounded Run observations over accepted owner receipts; never dispatches work."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
import time

from .events import validate_event
from .errors import FleetRefusal
from .identity import SessionKey
from .cloudcli_native import validate_delivery

_BACKOFF = (10, 30, 120, 600)
_TERMINAL = {"completed": "COMPLETED", "succeeded": "COMPLETED",
             "failed": "FAILED", "cancelled": "CANCELLED", "canceled": "CANCELLED",
             "aborted": "CANCELLED"}


class RunWatcher:
    def __init__(self, service, events, attempts, *, clock=time.time):
        self.service, self.events, self.attempts, self.clock = service, events, attempts, clock
        self._stop = threading.Event()
        self._thread = None
        self._tick_lock = threading.Lock()
        self._recovery_after = None
        self.last_result = None

    def _recover(self, now, result):
        if not callable(getattr(self.attempts, "dispatch_attempts", None)):
            result["recovery_status"] = "UNSUPPORTED"
            return
        try:
            records = self.attempts.dispatch_attempts()
            if not isinstance(records, dict):
                raise ValueError("unsupported owner listing")
        except Exception:
            result["recovery_status"] = "UNAVAILABLE"
            return
        result["recovery_status"] = "AVAILABLE"
        # Owner listing is a snapshot API; projection work is bounded per tick.
        keys = sorted(records)
        remaining = [key for key in keys if self._recovery_after is None or key > self._recovery_after]
        if not remaining:
            remaining = keys
        batch = remaining[:100]
        self._recovery_after = batch[-1] if batch else None
        result["recovery_has_more"] = len(remaining) > len(batch)
        for token in batch:
            record = records[token]
            if not isinstance(record, dict):
                result["malformed"] += 1
                continue
            if record.get("phase") != "ACCEPTED":
                continue
            if record.get("verb") == "project.create":
                continue
            if record.get("verb") not in {"session.send", "session.create"}:
                result["unsupported"] += 1
                continue
            try:
                receipt = record["result"]
                if (not isinstance(receipt, dict) or receipt.get("accepted") is not True
                        or set(receipt) - {"accepted", "session_ref", "run_id", "delivery_id", "queued_message_uuid"}):
                    raise ValueError("malformed receipt")
                for field in ("run_id", "delivery_id", "queued_message_uuid"):
                    value = receipt.get(field)
                    if value is not None and (not isinstance(value, str) or not value.strip()
                                              or len(value) > 512 or any(ord(c) < 32 for c in value)):
                        raise ValueError("malformed execution identifier")
                key = SessionKey.parse(receipt["session_ref"])
                if (key.device_ref != record["device_ref"] or key.provider != record["runtime"]
                        or record.get("session_ref", key.ref) != key.ref):
                    raise ValueError("receipt target differs from intent")
                run_id = receipt.get("run_id")
                delivery_id = receipt.get("delivery_id")
                if run_id is None and delivery_id is None:
                    result["unresolved"] += 1
                    continue
                # Preserve IDs of existing run watches; delivery namespace cannot collide.
                identity = [token, key.ref, "delivery", delivery_id] if delivery_id else [token, key.ref, run_id]
                watch_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
                template = {"event_id": watch_id + ":unknown", "kind": "run.unknown", "level": "decide",
                            "device_ref": key.device_ref, "runtime": key.provider, "session_ref": key.ref,
                            "run_id": run_id, "dispatcher": record["principal"], "occurred_at": now,
                            "summary": {"status": "UNKNOWN"}}
                if delivery_id is not None:
                    template["delivery_id"] = delivery_id
                for field in ("project", "work_ref", "parent_ref", "agent_ref", "principal_subject", "principal_kind"):
                    if record.get(field) is not None:
                        template[field] = record[field]
                validate_event(template)
                first_seen = now
                recorded_at = record.get("recorded_at")
                if recorded_at is not None:
                    if not isinstance(recorded_at, str) or not recorded_at.strip():
                        raise ValueError("malformed receipt timestamp")
                    first_seen = dt.datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).timestamp()
                    if first_seen < 0 or first_seen > now:
                        first_seen = now
                self.events.discover_watch(watch_id, template, first_seen=first_seen, now=now)
                result["recovered"] += 1
            except (FleetRefusal, KeyError, TypeError, ValueError, OverflowError):
                result["malformed"] += 1

    def _observe(self, watch):
        template = json.loads(watch["payload"])
        key = SessionKey.parse(template["session_ref"])
        provider = self.service._session_provider(key)
        self.service._verified_family_detail(key, provider)
        run_id = template["run_id"]
        delivery_id = template.get("delivery_id")
        boot_id = None
        if delivery_id is not None:
            family = (self.service.config.session_binding(key).family
                      if key.provider not in {"claude", "codex"} else key.provider)
            payload = validate_delivery(provider.get_delivery(key, delivery_id), key, delivery_id, family=family)
            if payload.get("provider") is not None:
                binding = self.service.config.session_binding(key)
                if payload["provider"] not in {key.provider, binding.family}:
                    raise ValueError("delivery family mismatch")
            bound = payload.get("runId")
            if run_id is not None and bound is not None and run_id != bound:
                raise ValueError("delivery contradicts acknowledged Run")
            if watch.get("bound_run_id") is not None and bound != watch["bound_run_id"]:
                raise ValueError("delivery binding changed")
            if bound is not None:
                boot_id = payload.get("bootId")
                self.events.bind_delivery(watch["watch_id"], bound, boot_id)
                run_id = bound
                watch["resolved_run_id"] = bound
            if payload.get("status") == "cancelled":
                return "CANCELLED"
            if bound is None:
                return None
        payload = provider.get_run(key, run_id, wait=False, wait_seconds=0)
        if not isinstance(payload, dict):
            raise ValueError("unsupported run response")
        if delivery_id is not None and (payload.get("idKind") != "run"
                or payload.get("runId") != run_id or payload.get("sessionId") != key.native_session_id
                or boot_id is not None and payload.get("bootId") != boot_id):
            raise ValueError("missing or mismatched bound Run proof")
        if payload.get("idKind") is not None and payload["idKind"] != "run":
            raise ValueError("wrong execution namespace")
        expected = {**key.as_dict(), "run_id": run_id, "runId": run_id,
                    "id": run_id, "sessionId": key.native_session_id}
        for field in ("device_ref", "session_ref", "native_session_id", "run_id", "runId", "id", "sessionId"):
            if payload.get(field) is not None and payload[field] != expected[field]:
                raise ValueError("run identity mismatch")
        if payload.get("provider") is not None:
            binding = self.service.config.session_binding(key)
            if payload["provider"] not in {key.provider, binding.family}:
                raise ValueError("run family mismatch")
        statuses = [payload[k].lower() for k in ("state", "status") if isinstance(payload.get(k), str)]
        mapped = {_TERMINAL.get(status) for status in statuses}
        if len(mapped) > 1:
            raise ValueError("contradictory run state")
        status = next(iter(mapped), None)
        if status == "COMPLETED":
            # completed describes process lifecycle; explicit result failure is
            # stronger evidence than that lifecycle word. Keep codes only.
            nodes = [payload]
            if isinstance(payload.get("result"), dict):
                nodes.append(payload["result"])
            failed = False
            for node in nodes:
                for flag in ("success", "is_error", "isError"):
                    if flag in node and node[flag] is not None and not isinstance(node[flag], bool):
                        raise ValueError("malformed run result flag")
                failed |= (node.get("success") is False or node.get("is_error") is True
                           or node.get("isError") is True or bool(node.get("error")))
                for field in ("exitCode", "exit_code"):
                    value = node.get(field)
                    if value is not None:
                        if type(value) is not int:
                            raise ValueError("malformed run exit code")
                        failed |= value != 0
            if failed:
                status = "FAILED"
        return status

    def tick(self) -> dict:
        with self._tick_lock:
            now = self.clock()
            result = {"recovered": 0, "observed": 0, "terminal": 0, "unknown": 0,
                      "errors": 0, "malformed": 0, "unsupported": 0, "unresolved": 0}
            self._recover(now, result)
            for watch in self.events.due_watches(now, limit=100):
                if self._stop.is_set():
                    break
                status = None
                try:
                    status = self._observe(watch)
                except Exception:
                    # Provider exceptions may contain credentials or transcripts.
                    result["errors"] += 1
                result["observed"] += 1
                event = None
                unknown = False
                if status is not None:
                    event = json.loads(watch["payload"])
                    event.update(event_id=watch["watch_id"] + ":terminal:" + status,
                                 kind="run.corrected" if watch["unknown_emitted"] else "run." + status.lower(),
                                 level="decide" if status == "FAILED" else "notify", occurred_at=now,
                                 summary={"status": status})
                    if "resolved_run_id" in watch:
                        event["run_id"] = watch["resolved_run_id"]
                    if watch["unknown_emitted"]:
                        event["summary"]["previous_status"] = "UNKNOWN"
                    result["terminal"] += 1
                elif now - watch["first_seen"] >= 86400 and not watch["unknown_emitted"]:
                    event = json.loads(watch["payload"])
                    event["occurred_at"] = now
                    unknown = True
                    result["unknown"] += 1
                delay = _BACKOFF[min(watch["polls"], len(_BACKOFF) - 1)]
                self.events.record_observation(watch["watch_id"], now=now, delay=delay,
                                               event=event, terminal=status is not None, unknown=unknown)
            self.last_result = result
            return result

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                self.last_result = {"status": "UNAVAILABLE"}
            self._stop.wait(1)

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fleet-run-watcher", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=60)
            if self._thread.is_alive():
                raise RuntimeError("run observer did not stop within its shutdown bound")
            self._thread = None
