#!/usr/bin/env python3
"""The fifteen situations, loaded and turned into models — one source for both
the tests and the previews a person reviews.

Why one source and not two
--------------------------
The previews are an approval artifact. Somebody will look at them and decide
whether the cards are fit to send. If the tests asserted against one set of
inputs and the previews had been generated from another, the artifact being
approved would be a card the suite never ran — and nothing would look wrong,
because both would be cards. So the documents live in `card-scenarios.yaml` and
both consumers read them from there.

**A preview is not delivery evidence.** §6 says a rendered card proves what the
renderer produces and not what a conversation looks like. This module is on the
"what the renderer produces" side, and it says so in the output it writes.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
SCENARIOS = _HERE / "card-scenarios.yaml"
READINGS = _HERE / "card-scenario-reads.json"

SCHEMA = "serveros/card-scenarios.v1"
READINGS_SCHEMA = "serveros/card-scenario-reads.v1"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def document() -> dict:
    import yaml
    doc = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8")) or {}
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"{SCENARIOS} declares schema {doc.get('schema')!r}, "
                         f"not {SCHEMA!r}")
    return doc


def scenarios() -> list[dict]:
    return list(document().get("scenarios") or [])


def readings() -> dict:
    """The captured read payloads, one named set at a time.

    WHY THEY LIVE IN THEIR OWN FILE
    -------------------------------
    A status scenario's `mine` and `host` blocks are DERIVED from the four
    documents the live path reads (`resource summary`, `resource top`,
    `resource user-services`, `runtime list`) by the same
    `card_source.snapshot` call production makes, so no fixture writes a
    sentence the producer would not have written. That needs the documents
    themselves, and the documents are output: the honest form of them is the
    bytes the command printed.

    They are kept here rather than transcribed into `card-scenarios.yaml` for
    the reason transcription is always worse: a re-serialised reading is a
    second copy of a measurement, and the copy is where a number quietly
    becomes a different number. It is also why these files are big enough to
    bury the fifty lines of a fixture under five hundred lines of payload —
    the file a person reads is the scenarios file.
    """
    raw = json.loads(READINGS.read_text(encoding="utf-8"))
    if raw.get("schema") != READINGS_SCHEMA:
        raise ValueError(f"{READINGS} declares schema {raw.get('schema')!r}, "
                         f"not {READINGS_SCHEMA!r}")
    return raw


def read_set(name: str, *, mutations=()) -> dict:
    """One named set's payloads, with declared mutations applied.

    A MUTATION IS A READING REPLACED BY ANOTHER READING, NEVER A NUMBER
    INVENTED HERE
    ------------------------------------------------------------------
    `mutations` exists for the scenarios this node has not been in this week —
    a swap watermark above its threshold, say — and it is deliberately the same
    shape the Semantics V2 evidence used: `before`, `after`, and a `why`. The
    `before` value is not decoration. It is checked, so a mutation that has
    stopped describing the capture it edits fails here instead of silently
    doing nothing, which is what a path typo does in a fixture that only ever
    writes.
    """
    sets = readings().get("sets") or {}
    if name not in sets:
        raise ValueError(f"no read set named {name!r} in {READINGS}; "
                         f"it holds: {', '.join(sorted(sets))}")
    entry = sets[name]
    # Flattened to the payloads FIRST, so a mutation path names the field the way
    # a person would: `summary.host.memory.swap.used_percent`, not
    # `summary.payload.host...`. What a mutation edits is the reading, and the
    # command and the time beside it are provenance rather than reading.
    payloads = {read: read_entry.get("payload")
                for read, read_entry in (entry.get("reads") or {}).items()}
    payloads = json.loads(json.dumps(payloads))
    for mutation in mutations:
        _apply(payloads, mutation, set_name=name)
    return payloads


def _apply(payloads: dict, mutation: dict, *, set_name: str) -> None:
    path = str(mutation.get("path") or "")
    if "after" not in mutation:
        raise ValueError(f"mutation {path!r} in {set_name!r} declares no `after`")
    parts = path.split(".")
    if len(parts) < 2:
        raise ValueError(f"mutation path {path!r} must start with a read name "
                         f"and name a field, e.g. 'summary.host.load.load1'")
    node = payloads
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"mutation path {path!r} does not exist in "
                             f"{set_name!r}")
        node = node[part]
    leaf = parts[-1]
    if not isinstance(node, dict) or leaf not in node:
        raise ValueError(f"mutation path {path!r} does not exist in {set_name!r}")
    if "before" in mutation and node[leaf] != mutation["before"]:
        raise ValueError(
            f"mutation {path!r} expects {mutation['before']!r} but the capture "
            f"{set_name!r} holds {node[leaf]!r} — the mutation and the reading "
            f"have drifted apart")
    node[leaf] = mutation["after"]


def _fixture_store(rows: list) -> object:
    """A register the shape `alert_digest` reads, built from a fixture's rows.

    Only `alerts()` is here, because that is the whole of what `current` asks
    of a store — it reads the register and nothing else, which its own docstring
    says is deliberate. A fuller stand-in would let a fixture reach a surface the
    live path does not, and the preview would then be a picture of a store that
    does not exist.

    The rows are keyed by `alert_ref` the way the register keys them, and a row
    without one is keyed by its code and subject so that a fixture cannot make
    two rows collide silently. Nothing is defaulted into a row: a fixture that
    omits `state` gets a row with no state, which `current` reads as «not OPEN»
    and leaves out — visible in the preview as a row that did not appear rather
    than as one that appeared with a state nobody wrote.
    """
    alerts = {}
    for index, row in enumerate(rows or []):
        if not isinstance(row, dict):
            raise ValueError(f"problems[{index}] 不是一行登记记录：{row!r}")
        entry = dict(row)
        key = entry.get("alert_ref") or ":".join(
            str(entry.get(field) or "") for field in
            ("code", "device_ref", "subject_ref")) or f"row-{index}"
        alerts[str(key)] = entry
    return _Store(alerts)


class _Store:
    def __init__(self, alerts: dict):
        self._alerts = alerts

    def alerts(self) -> dict:
        return self._alerts


def model_for(scenario: dict, *, at: str | None = None,
              sample: bool = True) -> dict:
    """One scenario's card model, ready for the renderer.

    `sample` defaults to True and the default is the safe direction: a model
    built here becomes a preview, and a preview that is not marked is a picture
    of a machine state that does not exist, presented as one that does.
    """
    presentation = _load("serveros_runtime_presentation", "presentation.py")
    at = at or scenario.get("at") or document().get("at") or ""
    kind = scenario.get("card")
    if kind == "status":
        # THE BLOCKS ARE DERIVED, AND THE STATE WORDS WITH THEM
        # ---------------------------------------------------
        # `mine` / `host` / the day / the schedule are all built here by the
        # functions the live path calls — `card_source.snapshot` over the four
        # read documents, `card_day.summary` over the day's store,
        # `business_calendar.routine_window` over the declaration. Nothing about
        # a card's content is written into the fixture.
        #
        # This is not tidiness. A hand-written block is the author's belief about
        # what the producer says, and the two places it had already gone wrong
        # are the two nobody would have looked for: the host block had lost the
        # 交换区 line the producer always writes, and no block carried `metrics`
        # at all — so every preview showed a card with no two-column face while
        # the live card had three rows of cells. Worse is the state word: written
        # by hand, a fixture can claim ATTENTION without any rule in the tree
        # agreeing. Derived, `host_attention` is ⚠️ only because
        # `card_source._host_section` reached that verdict on the reading it was
        # handed, which is the property that makes the preview worth looking at.
        source = _load("serveros_runtime_card_source", "card_source.py")
        card_day = _load("serveros_runtime_card_day", "card_day.py")
        payloads: dict = {}
        name = scenario.get("reads")
        if isinstance(name, str):
            payloads = read_set(name, mutations=scenario.get("mutations") or ())
        # A gap the fixture declares names the read and the reason; the command
        # it prints beside them is the one `DEFAULT_READS` says that read runs,
        # so the sentence on the preview and the sentence in the log are built
        # from one list.
        commands = dict(source.DEFAULT_READS)
        declared_gaps = []
        for gap in scenario.get("gaps") or ():
            read = gap.get("read")
            if read not in commands:
                raise ValueError(f"scenario {scenario.get('name')!r} declares a "
                                 f"gap on read {read!r}, which is not one of "
                                 f"{', '.join(sorted(commands))}")
            declared_gaps.append({"read": read,
                                  "command": " ".join(commands[read]),
                                  "why": str(gap.get("why") or "")})
        collected = {
            "schema": source.SCHEMA,
            "reads": payloads,
            "gaps": declared_gaps,
            "read_names": [n for n, _ in source.DEFAULT_READS],
        }
        # A fixture's `day_store` is the STORE's document — rows of
        # tick/hhmm/verdict/observation/sections/gaps, and the message the day's
        # card went out in — handed over as the READING the live path takes of
        # it. The summary the card renders is derived from that reading here, by
        # the same function the live path calls. Writing the summary into the
        # YAML instead would hand every preview a timeline nobody derived: a
        # second author of «what counts as a change», which is the one thing the
        # timeline must have exactly one of. It also makes the fixture unable to
        # state a transition its own rows do not show, which is the property
        # that keeps a preview from being a picture of a day that never happened.
        day_reading = None
        if scenario.get("day_store") is not None:
            day_reading = {"state": "READ",
                           "day": card_day.summary(scenario["day_store"]),
                           "path": "", "reason": "", "detail": ""}
        # A fixture's `calendar` is a DECLARATION, not a schedule: the block the
        # card prints — the day, the window, the tick — is computed from it by
        # `business_calendar.routine_window()`, called inside `snapshot`, so the
        # sentence on the preview is the producer's sentence and not a copy of
        # one. A fixture that wrote that sentence by hand would be readable
        # prose asserting what its author believed the producer says, which is
        # the one thing a fixture may not do.
        #
        # A scenario may carry its own declaration and usually does not: the
        # calendar is one fact about the instance, so it lives at the document
        # level and this is the same «scenario, else document» rule `at` follows
        # above. It is validated by the same function the live path uses before
        # a declaration is believed, so a fixture that has drifted from the
        # shape production requires fails here with the producer's own refusal
        # rather than rendering a day nobody would have accepted.
        calendar_reading = None
        declared = scenario.get("calendar") or document().get("calendar")
        if declared is not None:
            calendar = _load("serveros_runtime_business_calendar",
                             "business_calendar.py")
            calendar_reading = {"state": "READ",
                                "declaration": calendar.declaration(declared),
                                "reason": "", "detail": "", "path": ""}
        # §3.4's fourth block. A fixture states the REGISTER ROWS and the
        # reading is DERIVED from them by `alert_digest.current` — the grader the
        # live path runs — for the reason written above the day store: a fixture
        # that wrote «1 项需要处理» by hand would be prose asserting what its
        # author believed the contract says. What counts as a current problem is
        # a contract read (`alerts.attention.on_the_face`) and this is exactly
        # the file a preview must not be free to disagree with production about.
        #
        # Three shapes, and the third is why it is not just a list:
        #   * a LIST of register rows — graded here;
        #   * a STRING — the reason there is no reading, which renders the block
        #     the way `alert_digest.unreadable` does. «no reading» and «no
        #     problems» are the two statements §3.4 keeps apart, so a fixture has
        #     to be able to say the first one on purpose;
        #   * absent at both levels — no register in view at all, which is what
        #     a fixture written before this block existed produces, and
        #     `status_model` renders that as 无法确认 on the same rule.
        #
        # «scenario, else document», like the calendar: which rows are open is
        # one fact about this node, so a scenario that does not care about the
        # block does not restate it.
        problems_reading = None
        declared_problems = scenario.get("problems", document().get("problems"))
        if isinstance(declared_problems, str):
            digest = _load("serveros_runtime_alert_digest", "alert_digest.py")
            problems_reading = digest.unreadable(declared_problems, at=at)
        elif declared_problems is not None:
            digest = _load("serveros_runtime_alert_digest", "alert_digest.py")
            problems_reading = digest.current(_fixture_store(declared_problems),
                                              at=at)
        binding = scenario.get("binding") or None
        snapshot = source.snapshot(
            collected, at=at, node_label=document().get("device_ref"),
            binding=binding, calendar=calendar_reading, day=day_reading,
            problems=problems_reading)
        return presentation.status_model(snapshot, at=at, binding=binding,
                                         sample=sample)
    digest = dict(scenario.get("digest") or {})
    digest.setdefault("at", at)
    digest.setdefault("device_ref", document().get("device_ref"))
    if kind == "alert":
        # The name index is BUILT HERE from the scenario's own evidence rather
        # than written into the fixture. A hand-written index would be a
        # preview that shows a readable name while proving nothing about
        # whether the resolver can produce one — and the resolver is the thing
        # a reader is being asked to approve.
        names = None
        if scenario.get("attribution") is not None or scenario.get("registry") is not None:
            names = presentation.name_index(
                attribution=scenario.get("attribution"),
                registry=scenario.get("registry"))
        return presentation.alert_model(digest, at=at, sample=sample, names=names)
    if kind == "recovery":
        return presentation.recovery_model(digest, at=at, sample=sample)
    raise ValueError(f"scenario {scenario.get('name')!r} names card {kind!r}, "
                     "which is not one of status/alert/recovery")


def rendered(*, sample: bool = True) -> list[dict]:
    """Every scenario, as `cards.preview` output plus its own provenance."""
    cards = _load("serveros_runtime_cards", "cards.py")
    document_ = document()
    out = []
    for scenario in scenarios():
        # The document's instant is the default and a scenario may fix its own.
        # A status fixture needs one: its `at` decides which HOUR the reading is
        # and whether the day it carries could have had a routine card at all,
        # and the document's instant is a Sunday — a day on which «已更新 7/13
        # 个时点» is a sentence about a card that cannot exist. Reproducibility
        # is what the fixed instant is for, and a per-scenario instant is just
        # as fixed.
        at = scenario.get("at") or document_.get("at") or ""
        model = model_for(scenario, at=at, sample=sample)
        caption = (f"{scenario.get('title')} · 来源："
                   f"{' '.join(str(scenario.get('source') or '').split())}\n"
                   f"{' '.join(str(document_.get('preview_note') or '').split())}")
        result = cards.preview(model, caption=caption)
        result["scenario"] = scenario.get("name")
        result["title"] = scenario.get("title")
        out.append(result)
    return out


def write(out_dir, *, sample: bool = True) -> list[Path]:
    """The JSON and the HTML for every scenario, under one directory.

    Both files per scenario, always. The JSON is what a reviewer diffs and the
    HTML is what a reviewer reads; writing only the HTML would make the pair
    uncheckable against each other, which is the one thing having one renderer
    is supposed to guarantee.
    """
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for item in rendered(sample=sample):
        stem = f"{item['scenario']}"
        path_html = target / f"{stem}.html"
        path_json = target / f"{stem}.json"
        path_html.write_text(item["html"], encoding="utf-8")
        path_json.write_text(item["json"], encoding="utf-8")
        written += [path_html, path_json]
    index = target / "README.txt"
    index.write_text(
        "这十五个文件由同一个 renderer 从 runtime/card-scenarios.yaml 生成。\n"
        "每个场景一对：.html 是人看的预览（含 390px 窄屏与桌面两帧），\n"
        ".json 是同一张卡实际会发出去的 payload。\n\n"
        "它们全部带「示例」标识，且均未发送。预览证明 renderer 产出什么，\n"
        "不证明飞书怎么显示，也不代表任何东西已被发送。\n",
        encoding="utf-8")
    written.append(index)
    return written


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - hand check
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True,
                        help="directory to write the fifteen previews into")
    parser.add_argument("--unsampled", action="store_true",
                        help="omit the SAMPLE mark. For looking at layout "
                             "only; never for a file somebody reviews")
    parser.add_argument("--print-model", metavar="NAME",
                        help="print one scenario's model as JSON instead")
    args = parser.parse_args(argv)

    if args.print_model:
        for scenario in scenarios():
            if scenario.get("name") == args.print_model:
                model = model_for(scenario, sample=not args.unsampled)
                json.dump(model, sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
                return 0
        sys.stderr.write(f"no scenario named {args.print_model!r}\n")
        return 1

    for path in write(args.out, sample=not args.unsampled):
        print(path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
