#!/usr/bin/env python3
"""The six situations, loaded and turned into models — one source for both the
tests and the previews a person reviews.

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

SCHEMA = "serveros/card-scenarios.v1"


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
        snapshot = dict(scenario.get("snapshot") or {})
        snapshot["at"] = at
        binding = scenario.get("binding") or None
        return presentation.status_model(snapshot, at=at, binding=binding,
                                         sample=sample)
    digest = dict(scenario.get("digest") or {})
    digest.setdefault("at", at)
    digest.setdefault("device_ref", document().get("device_ref"))
    if kind == "alert":
        return presentation.alert_model(digest, at=at, sample=sample)
    if kind == "recovery":
        return presentation.recovery_model(digest, at=at, sample=sample)
    raise ValueError(f"scenario {scenario.get('name')!r} names card {kind!r}, "
                     "which is not one of status/alert/recovery")


def rendered(*, sample: bool = True) -> list[dict]:
    """Every scenario, as `cards.preview` output plus its own provenance."""
    cards = _load("serveros_runtime_cards", "cards.py")
    document_ = document()
    at = document_.get("at") or ""
    out = []
    for scenario in scenarios():
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
        "这六个文件由同一个 renderer 从 runtime/card-scenarios.yaml 生成。\n"
        "每个场景一对：.html 是人看的预览（含 390px 窄屏与桌面两帧），\n"
        ".json 是同一张卡实际会发出去的 payload。\n\n"
        "它们全部带 示例 / 尚未发送 标识。预览证明 renderer 产出什么，\n"
        "不证明飞书怎么显示，也不代表任何东西已被发送。\n",
        encoding="utf-8")
    written.append(index)
    return written


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - hand check
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True,
                        help="directory to write the six previews into")
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
