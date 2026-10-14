#!/usr/bin/env python3
"""A SHADOW orchestration entrypoint for the two surfaces: policy → renderer →
injected transport, with NO code path that reaches the network.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
It IS the wiring `runtime/card_surface.py` was missing: it takes a decision from
the policy, renders the approved native CardKit 2.0 card with
`card_period.render_period_card`, hands it to an INJECTED sender, and commits the
policy's cursors only on the receipt the sender returns.

It is NOT a production sender. There is **no** import of `message_transport`'s
live sender and no branch that runs the lark CLI; the transport is a constructor
argument and the only sender this module defines is a RECORDING fake. A real
sender adapter is a separate, explicitly authorised integration.

    fake      a `RecordingSender` — records every call, returns a receipt you
              configure, never opens a socket
    real      nothing in this module. `--preview` prints a masked summary and
              the component tree and touches NO store and NO transport.

WHAT IS INJECTED
----------------
clock, source, store and transport are all arguments: `tick(at=...)` takes the
instant, `model_source(at=...)` takes the readings, `store` is a
`card_surface.SurfaceStore`, and `sender` is anything with `send_card` /
`update`. Nothing here reads a clock, a file it was not handed, or a socket.

THE RULING IT SERVES
--------------------
TWO surfaces and no third: ONE long-lived LIVE card (30-minute slot heartbeat,
immediate patch on a real alert event) and per-period REPORT cards on their own
message ids. ALERT is a region of LIVE; this module never mints an ALERT card.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

SCHEMA = "serveros/card-surface-runner.v1"


class RunnerError(RuntimeError):
    pass


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


surface = _load("serveros_runtime_card_surface", _HERE / "card_surface.py")
card_period = _load("serveros_runtime_card_period", _HERE / "card_period.py")
alert_digest = _load("serveros_runtime_alert_digest", _HERE / "alert_digest.py")

#: `alert_digest`'s five delta kinds → the LIVE surfaces' four event kinds. The
#: mapping is explicit rather than a pass-through because the two vocabularies
#: are not the same length: a REOPENED alert comes back HARDER (it is an
#: escalation for a reader), and UPDATED is a change WITHIN an episode that a
#: person should still see. A kind outside both tables is UNVERIFIED.
EVENT_KIND_MAP = {
    "NEW": "NEW",
    "ESCALATED": "ESCALATED",
    "RECOVERED": "RECOVERED",
    "REOPENED": "ESCALATED",
    "UPDATED": "HANDLING",
}


# ---------------------------------------------------------------------------
# the typed event adapter — alert_digest rows → surface events
# ---------------------------------------------------------------------------

def digest_version(row: dict) -> str:
    """A stable version token for one alert row: a hash of what it ASSERTS.

    `alert_digest.row_version` freezes severity / state / hold_kind / episode at
    compose time; hashing exactly that means a re-poll of an unchanged row gives
    the SAME token (and dedupes) while a severity or state change gives a NEW one
    (and patches). Returning a made-up token would defeat the cursor.
    """
    frozen = alert_digest.row_version(row if isinstance(row, dict) else {})
    blob = json.dumps(frozen, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def event_from_digest_row(row) -> dict | None:
    """One `alert_digest` row → one surface event, or `None` (UNVERIFIED).

    Fail-closed twice: a row with no identity token (`key` / `alert_ref`) and a
    row whose `kind` is outside the mapping both return `None`, and the policy
    then counts them as unverified rather than letting an event nobody can name
    re-patch the card on every poll.
    """
    if not isinstance(row, dict):
        return None
    identity = str(row.get("key") or row.get("alert_ref") or "").strip()
    if not identity:
        return None
    kind = EVENT_KIND_MAP.get(str(row.get("kind") or "").upper())
    if kind is None:
        return None
    return {"kind": kind, "episode": alert_digest.episode_of(row),
            "version": digest_version(row), "source_row_key": identity}


def events_from_digest(rows) -> tuple[list[dict], int]:
    """A batch of digest rows → (events, unverified count)."""
    events, unverified = [], 0
    for row in rows or ():
        event = event_from_digest_row(row)
        if event is None:
            unverified += 1
        else:
            events.append(event)
    return events, unverified


# ---------------------------------------------------------------------------
# the transport seams — a recording fake, and a preview that does nothing
# ---------------------------------------------------------------------------

def _card_sha(card) -> str:
    blob = json.dumps(card, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


#: The only two recipient shapes this product delivers to. A bare id is refused:
#: `chat:` and `user:` are mutually exclusive CLI flags, and «which one» is not a
#: thing a guess may decide — a report that lands in a group instead of a person
#: cannot be recalled.
RECIPIENT_PREFIXES = ("user:", "chat:")


def verified_recipient(recipient) -> str:
    """The recipient, or a refusal. FAIL-CLOSED: no prefix, no delivery."""
    text = str(recipient or "").strip()
    if not text.startswith(RECIPIENT_PREFIXES) or len(text) <= len("user:"):
        raise RunnerError(
            f"收件人必须是 user:<open_id> 或 chat:<chat_id>，收到的是 {text!r}。"
            f"不加前缀就等于让这里替调用方猜目的地，猜错就收不回来")
    return text


class RecordingSender:
    """A sender that records every call and returns a CONFIGURED receipt.

    It never opens a socket, and it MODELS THE SERVICE'S IDEMPOTENCY WINDOW:
    a `send_card` carrying a key it has already accepted returns the SAME
    message id rather than minting a second one. That is what makes the
    «failed create retried under the same key» test meaningful — a fake that
    always returned the same id regardless would pass it by accident.

    `outcome` + `fail_first` decide what the first calls report, so one sender
    can drive «one failure, then a clean retry».
    """

    def __init__(self, *, outcome: str = "DELIVERED", remote_id: str = "om_fake",
                 version: str | None = None, fail_first: int = 0,
                 fail_calls: tuple[int, ...] = ()):
        self.outcome = outcome
        self.remote_id = remote_id
        self.version = version
        #: Which call INDICES fail. `fail_first=n` is shorthand for the first n;
        #: `fail_calls=(1,)` fails only the second, which is how a round where
        #: LIVE succeeds and the REPORT fails is driven.
        self.fail_calls = set(fail_calls) | set(range(fail_first))
        self.calls: list[dict] = []
        self._by_key: dict[str, str] = {}

    def _failing(self) -> bool:
        return len(self.calls) in self.fail_calls

    def _answer(self, *, op: str, remote_id, version, **extra):
        state = self.outcome if self._failing() else "DELIVERED"
        row = {"op": op, "remote_id": remote_id, "version": version, "state": state}
        row.update(extra)
        self.calls.append(row)
        return {"state": state, "why": f"{op} via RecordingSender (no network)",
                "version": self.version if self.version is not None else version,
                "provider": {"id": "recording-fake", "transport": op,
                             "remote_id": remote_id if state == "DELIVERED" else None}}

    def send_card(self, card, *, idempotency_key: str, version=None, **_) -> dict:
        known = self._by_key.get(idempotency_key)
        if known is None and not self._failing():
            known = f"{self.remote_id}-{len(self._by_key) + 1}"
            self._by_key[idempotency_key] = known
        return self._answer(op="send_card", remote_id=known, version=version,
                            idempotency_key=idempotency_key,
                            card_sha256_12=_card_sha(card))

    def update(self, request: dict) -> dict:
        return self._answer(op="update", remote_id=request.get("message_id"),
                            version=request.get("version"),
                            card_sha256_12=_card_sha(request.get("card")))


class PreviewSender:
    """A sender that refuses to be used. `--preview` must not carry anything."""

    def send_card(self, card, **kwargs):
        raise RunnerError("the preview mode must not send; it renders and decides only")

    def update(self, request):
        raise RunnerError("the preview mode must not patch; it renders and decides only")


# ---------------------------------------------------------------------------
# the sources — injected, never fetched here
# ---------------------------------------------------------------------------

def local_model_source(declaration=None, *, device_ref="device:tokyo-shared-dev-01",
                       display_name="东京共享开发机", platform="linux",
                       unknown=()):
    """A READ-ONLY model source: one real local reading + explicitly unknown peers.

    Composes `card_source.gather/snapshot` and `fleet_monitor_source.fleet_model`
    — both read-only — for THIS host only, and carries the peers named in
    `unknown` as UNKNOWN devices (never a zero, never an invented value). It
    reads nothing about another account. The returned `rows` is empty for a
    single sample: the report then says the trend is UNKNOWN rather than drawing
    a curve over one point.
    """
    card_source = _load("serveros_runtime_card_source", _HERE / "card_source.py")
    fleet_source = _load("serveros_runtime_fleet_monitor_source",
                         _HERE / "fleet_monitor_source.py")

    def source(*, at):
        snapshot = card_source.snapshot(card_source.gather(), at=at)
        peers = [(ref, None) for ref in unknown]
        names = {device_ref: display_name}
        platforms = {device_ref: platform}
        for ref, peer_name in unknown:
            names[ref] = peer_name
        model = fleet_source.fleet_model(
            snapshots=[(device_ref, snapshot)] + peers, now=at, sample=False,
            names=names, platforms=platforms)
        return model, []

    return source


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------

def _slot_key(decision: dict, at: str, *, rotations: int = 0) -> str:
    """The idempotency key for one LIVE step — STABLE ACROSS RETRIES.

    This is the whole crash-safety story for CREATE: a failed create must be
    retried under the SAME key, because the send service deduplicates on it
    inside its window. A key that carried the instant would make every retry a
    new message — a second live card, which is the one thing a single-LIVE rule
    forbids. A patch keys on the SLOT for the same reason.
    """
    if decision.get("action") == surface.PATCH:
        slot = decision.get("slot")
        return (f"card-surface-live-slot-{slot}" if slot is not None
                else f"card-surface-live-patch-{at}")
    if decision.get("action") == surface.SUCCESSOR:
        return f"card-surface-live-successor-{rotations + 1}"
    return f"card-surface-live-create-{rotations}"


class ShadowRunner:
    """Decide → render → send (injected) → commit. NO network, ever.

    Every collaborator is injected: `store` (a `card_surface.SurfaceStore`),
    `sender` (a `RecordingSender` or anything with the same two methods),
    `model_source(at) -> (model, rows)` and `period_source(at) -> period`. The
    `recipient` is validated by PREFIX here, so a bare id — which could be a
    chat instead of a person — is refused before anything is rendered.
    """

    def __init__(self, *, store, sender, model_source, period_source,
                 recipient="user:ou_verified_placeholder",
                 test_mark="[灰度测试]", declaration=None):
        self.store = store
        self.sender = sender
        self.model_source = model_source
        self.period_source = period_source
        self.recipient = verified_recipient(recipient)
        self.test_mark = test_mark
        #: SINGLE-WRITER, in this process only. A reentrant tick would race the
        #: pending intent it is halfway through staging, so it is refused.
        #: THIS IS NOT CROSS-PROCESS MUTUAL EXCLUSION — an atomic file replace is
        #: not a compare-and-swap, and two processes (a timer and an alert) can
        #: still interleave. Serialising them belongs to whatever owns the work.
        self._busy = False

    @property
    def evidence(self) -> str:
        """Whose word this runner's receipts are. FAKE senders are `synthetic`."""
        if isinstance(self.sender, (RecordingSender, PreviewSender)):
            return surface.EVIDENCE_SYNTHETIC
        return surface.EVIDENCE_REMOTE

    # -- rendering ----------------------------------------------------------

    def render_live(self, *, model) -> dict:
        return card_period.render_period_card(model, test_mark=self.test_mark)

    def render_report(self, *, model, period, rows) -> tuple[dict, dict]:
        view = card_period.archive(model, period=period, rows=rows)
        card = card_period.render_archive(view, model=model, test_mark=self.test_mark)
        return view, card

    # -- preview: decide + render, touch NOTHING ---------------------------

    def preview(self, *, at, events=(), closing=None) -> dict:
        """The decisions and the component tree — no store write, no send."""
        model, rows = self.model_source(at=at)
        live = self.store.decide_live(at=at, events=events)
        period = self.period_source(at=at)
        report = self.store.decide_report(period=period, closing=closing)
        tree = {"live": _tree(self.render_live(model=model)),
                "report": None}
        if report["action"] in (surface.CREATE, surface.PATCH, surface.FINAL):
            _, card = self.render_report(model=model, period=period, rows=rows)
            tree["report"] = _tree(card)
        return {"schema": SCHEMA, "at": at, "live": _masked(live),
                "report": _masked(report), "components": tree,
                "store_touched": False, "transport_calls": 0}

    # -- the simulated round ---------------------------------------------

    def tick(self, *, at, events=(), closing=None, rows=()) -> dict:
        """One shadow round, under a single-writer guard. See `_round`."""
        if self._busy:
            raise RunnerError(
                "a tick is already in progress in this process: the surfaces are "
                "single-writer, and a reentrant call would race the pending intent "
                "this one is halfway through staging")
        self._busy = True
        try:
            return self._round(at=at, events=events, closing=closing, rows=rows)
        finally:
            self._busy = False

    def _round(self, *, at, events=(), closing=None, rows=()) -> dict:
        """LIVE then REPORT, each committing only on a DELIVERED receipt."""
        trace = {"schema": SCHEMA, "at": at, "live": None, "report": None,
                 "transport_calls": 0, "store_writes": 0,
                 "evidence": self.evidence}
        model, source_rows = self.model_source(at=at)
        report_rows = list(rows) if rows else source_rows

        live = self.store.decide_live(at=at, events=events)
        trace["live"] = {"action": live["action"], "reasons": live.get("reasons"),
                         "slot": live.get("slot"), "events": live.get("events"),
                         "resume": bool(live.get("resume")),
                         "reason": live.get("reason")}
        if live["action"] in (surface.CREATE, surface.PATCH, surface.SUCCESSOR):
            card = self.render_live(model=model)
            key = (live.get("idempotency_key")
                   or _slot_key(live, at, rotations=self._rotations()))
            self.store.stage_live(decision=live, at=at, idempotency_key=key,
                                  recipient=self.recipient, payload_digest=_card_sha(card))
            trace["store_writes"] += 1
            if live["action"] == surface.PATCH:
                receipt = self.sender.update({
                    "message_id": live["remote_id"], "card": card,
                    "sent_at": self._bound_at(), "version": None})
            else:
                receipt = self.sender.send_card(card, idempotency_key=key)
            self._tag(receipt)
            trace["transport_calls"] += 1
            trace["live"]["receipt"] = _receipt_view(receipt)
            if receipt.get("state") == "DELIVERED":
                self.store.confirm_live(at=at, receipt=receipt, evidence=self.evidence)
                trace["store_writes"] += 1
                trace["live"]["committed"] = True
            else:
                # FAIL-CLOSED: the intent stays pending; the cursors do not move
                # and the next tick retries the SAME message.
                trace["live"]["committed"] = False
                trace["live"]["pending_retained"] = True

        period = self.period_source(at=at)
        report = self.store.decide_report(period=period, closing=closing, at=at)
        trace["report"] = {"action": report["action"],
                           "period_ref": report.get("period_ref"),
                           "resume": bool(report.get("resume")),
                           "reason": report.get("reason")}
        if report["action"] in (surface.CREATE, surface.PATCH, surface.FINAL):
            ref = report["period_ref"]
            view, card = self.render_report(model=model, period=period, rows=report_rows)
            key = report.get("idempotency_key") or f"card-surface-report-{ref}"
            if report["action"] == surface.CREATE:
                # THE INTENT IS PERSISTED BEFORE THE SEND. A process that dies
                # between the send and the id being recorded must find the intent
                # on disk and resume the SAME message, never create a second one.
                self.store.stage_report(period_ref=ref, at=at, idempotency_key=key,
                                        payload_digest=_card_sha(card),
                                        label=(period or {}).get("label"),
                                        day_ref=(period or {}).get("day_ref"))
                trace["store_writes"] += 1
                receipt = self.sender.send_card(card, idempotency_key=key)
                self._tag(receipt)
                trace["transport_calls"] += 1
                trace["report"]["receipt"] = _receipt_view(receipt)
                remote_id = (receipt.get("provider") or {}).get("remote_id")
                if receipt.get("state") == "DELIVERED" and remote_id:
                    self.store.bind_report(period_ref=ref, at=at, remote_id=remote_id,
                                           evidence=self.evidence)
                    trace["store_writes"] += 1
                    trace["report"]["committed"] = True
                else:
                    trace["report"]["committed"] = False
                    trace["report"]["pending_retained"] = True
            elif report["action"] == surface.PATCH:
                # an OBSERVATION, not a confirmation — the cursor moves on the ack
                self.store.apply_report(period=period, decision=report, at=at, archive=view)
                trace["store_writes"] += 1
                receipt = self.sender.update({
                    "message_id": self._report_ref(ref), "card": card,
                    "sent_at": self._report_bound_at(ref), "version": None})
                self._tag(receipt)
                trace["transport_calls"] += 1
                trace["report"]["receipt"] = _receipt_view(receipt)
                if receipt.get("state") == "DELIVERED":
                    try:
                        self.store.ack_report_patch(period_ref=ref, at=at, receipt=receipt)
                        trace["store_writes"] += 1
                        trace["report"]["committed"] = True
                    except surface.CardSurfaceError as exc:
                        trace["report"]["committed"] = False
                        trace["report"]["refused"] = str(exc)
                else:
                    trace["report"]["committed"] = False
                    trace["report"]["pending_retained"] = True
            else:  # FINAL — PREPARE, then commit only on an acknowledgement
                frozen = card_period.frozen_block(card=card, at=at, period_ref=ref)
                self.store.apply_report(period=period, decision=report, at=at,
                                        archive=view, prepared_version=frozen["version"])
                trace["store_writes"] += 1
                receipt = self.sender.update({
                    "message_id": self._report_ref(ref), "card": card,
                    "sent_at": self._report_bound_at(ref), "version": frozen["version"]})
                self._tag(receipt)
                trace["transport_calls"] += 1
                trace["report"]["receipt"] = _receipt_view(receipt)
                if receipt.get("state") == "DELIVERED":
                    try:
                        self.store.confirm_report(period_ref=ref, at=at, receipt=receipt,
                                                  evidence=self.evidence)
                        trace["store_writes"] += 1
                        trace["report"]["committed"] = True
                    except surface.CardSurfaceError as exc:
                        trace["report"]["committed"] = False
                        trace["report"]["refused"] = str(exc)
                else:
                    trace["report"]["committed"] = False
        trace["live_state"] = _masked(self.store.summary().get("live") or {})
        return trace

    # -- small store accessors ---------------------------------------------

    def _tag(self, receipt) -> dict:
        """Label a receipt with WHOSE word it is, so no trace can over-claim."""
        if isinstance(receipt, dict):
            receipt.setdefault("evidence", self.evidence)
        return receipt

    def _bound_at(self):
        return (self.store.read().get("live") or {}).get("bound_at")

    def _rotations(self):
        return int((self.store.read().get("live") or {}).get("rotations") or 0)

    def _report_ref(self, period_ref):
        row = next((r for r in self.store.read().get("reports") or []
                    if r.get("period_ref") == period_ref), None)
        return (row or {}).get("remote_id")

    def _report_bound_at(self, period_ref):
        row = next((r for r in self.store.read().get("reports") or []
                    if r.get("period_ref") == period_ref), None)
        return (row or {}).get("bound_at")


# ---------------------------------------------------------------------------
# masking / summaries — a trace must not leak an identifier
# ---------------------------------------------------------------------------

def _mask(value) -> str | None:
    text = str(value or "")
    if not text:
        return None
    if len(text) <= 8:
        return text[:2] + "…"
    return text[:4] + "…" + text[-4:]


def _receipt_view(receipt: dict) -> dict:
    provider = (receipt or {}).get("provider") or {}
    return {"state": (receipt or {}).get("state"),
            "transport": provider.get("transport"),
            "remote_id": _mask(provider.get("remote_id")),
            "version": (receipt or {}).get("version")}


def _masked(decision: dict) -> dict:
    out = {k: v for k, v in (decision or {}).items()
           if k not in ("says", "remote_id")}
    if "remote_id" in (decision or {}):
        out["remote_id"] = _mask(decision.get("remote_id"))
    return out


def _tree(card: dict) -> dict:
    """The component tree, with counts — never the card's content."""
    tags: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("tag"), str):
                tags.append(node["tag"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(card.get("body"))
    counts: dict[str, int] = {}
    for tag in tags:
        counts[tag] = counts.get(tag, 0) + 1
    columns = [len(e.get("columns") or []) for e in card["body"]["elements"]
               if e.get("tag") == "column_set"]
    return {"schema": card.get("schema"),
            "header_template": (card.get("header") or {}).get("template"),
            "top_level": [e.get("tag") for e in card["body"]["elements"]],
            "tag_counts": counts, "kpi_columns_per_row": columns,
            "has_chart": "chart" in counts, "has_table": "table" in counts}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - hand check
    """`--preview` prints a masked summary and the component tree. NO send path."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preview", action="store_true", default=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--at", default="")
    parser.add_argument("--events", default="0", type=int,
                        help="how many synthetic NEW events to inject (mock)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    at = args.at or datetime.datetime.now(
        datetime.timezone.utc).astimezone().isoformat()
    # An IN-MEMORY store and a sender that refuses: preview cannot touch state
    # and cannot carry anything.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = surface.SurfaceStore(Path(tmp) / "surfaces.json")
        runner = ShadowRunner(
            store=store, sender=PreviewSender(),
            model_source=local_model_source(
                unknown=(("device:work-fj", "FJPCOS 工作站"),
                         ("device:llm-lab-dell", "A6000 推理机"))),
            period_source=lambda *, at: _preview_period(at))
        events = [{"kind": surface.NEW, "episode": f"mock-{i}", "version": 1}
                  for i in range(args.events)]
        trace = runner.preview(at=at, events=events)
    if args.json:
        print(json.dumps(trace, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"preview @ {at}")
        print("  LIVE  :", trace["live"].get("action"),
              "·", trace["live"].get("reasons"))
        print("  REPORT:", trace["report"].get("action"),
              "·", trace["report"].get("period_ref"))
        for name, tree in trace["components"].items():
            if tree:
                print(f"  {name} components: {tree['top_level']} "
                      f"kpi_rows={tree['kpi_columns_per_row']} chart={tree['has_chart']}")
        print("  store_touched:", trace["store_touched"],
              "transport_calls:", trace["transport_calls"])
    return 0


def _preview_period(at):  # pragma: no cover - hand check helper
    """The current period from the real calendar, or an unnamed one.

    `read_declaration` answers with a READING — state, reason, path and the
    declaration — and `period_at` wants the DECLARATION inside it. Passing the
    reading whole makes every day UNKNOWN, which is a silent way to lose the
    period identity, so it is unwrapped here.
    """
    calendar = _load("serveros_runtime_business_calendar", _HERE / "business_calendar.py")
    try:
        reading = calendar.read_declaration()
        return card_period.period_at(
            at, declaration=reading.get("declaration") if isinstance(reading, dict) else None)
    except Exception:
        return {"period_ref": None, "in_period": False}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
