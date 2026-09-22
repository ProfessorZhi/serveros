"""B8 — the compatibility facade, and the four things it is still carrying.

设计 §8 把 B8 写成一句话：``feishu-report`` 保留兼容入口，移除复制的测试执行、space
规则、默认 whole overwrite 和强制 Milestone；**先保留兼容入口再迁 consumer**，而且
**只有实际需要才独立新 Skill 身份**。

这句话里的三件事必须能被分开读，因为它们会在交付时被压成一件：

    保留兼容入口      这一轮不动，且路由只能有一条权威
    移除四处职责      每一处都被一个 consumer 的进度挡着
    不新建 Skill      设计原话是「只有实际需要才独立」，所以没有 `reporting`

这个模块的存在理由是第三件事最容易被做错：**只做完第一件的交付，读起来和做完两件是一样
的**。所以它拒绝一份说漏了任一条的契约（``contract()``），把还欠着的部分说出来
（``delegations()``），并把它被谁挡住说出来（``waiting()``）。

它**不**写正文、不发布、不投递，也不定义路由：路由的权威是
``skills/serveros/references/serveros-contract.json`` 和 ``context/resolver.py``，
这里只读它们，然后把读到的结果和契约逐字比对。本仓最容易长出来的东西是同一事实的第二份
定义，而这份契约里唯一一处「第二份定义」就是 ``facade.route`` —— 一个故意留成**被检查
的副本**的字段。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read the publication contract\n")
    raise SystemExit(2)

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent

CONTRACT = _HERE / "report-publication-contract.yaml"
CONTRACT_SCHEMA = "serveros/report-publication-contract.v1"

# 设计 §8 逐字点名的四处。名单在这里闭合，是为了让「加进第五处」和「悄悄少一处」都必须
# 由人改一个字，而不是由一次编辑顺手发生。
DESIGN_DELEGATIONS = (
    "copied-test-execution",
    "space-rules",
    "default-whole-overwrite",
    "compulsory-milestone",
)

# 一处职责的迁移只有两种真实状态。`MIGRATED` 不在这里，因为这份契约描述的正是**还没迁**
# 的那些：一处已经迁完的职责应当从 `delegations` 里消失，而不是留在原地换一个词。
DELEGATION_STATES = ("PENDING_CONSUMER",)

# 一个 consumer 只有两种处置。`MIGRATED` 同样不在名单上，理由与上面相同——已迁完的
# consumer 从表里消失。
CONSUMER_DISPOSITIONS = ("MIGRATION_WAITING_ON_ACTIVE_CONSUMER",)


class PublicationError(Exception):
    """一份在某个具体地方与它自己的声明不符的契约。"""


def contract(path: Path | str = CONTRACT) -> dict:
    """读并校验契约。任何一条不成立都拒绝，并说明是哪一条。"""
    path = Path(path)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PublicationError(f"{path} 读不出来：{exc}") from exc
    if not isinstance(document, dict):
        raise PublicationError(f"{path} 不是一份映射；一份读不出字段的契约不是契约")
    declared = document.get("schema")
    if declared != CONTRACT_SCHEMA:
        raise PublicationError(
            f"{path} 声明的是 {declared!r}，不是 {CONTRACT_SCHEMA!r}。"
            f"认不出的 schema 会被当成一份空契约读，而空契约里每一条约束都为真")

    _check_compose(document, path)
    facade = _check_facade(document, path)
    _check_route(facade)
    _check_delegations(document, path)
    _check_consumers(document, path)
    return document


def _check_compose(document: dict, path: Path) -> None:
    compose = document.get("compose") or {}
    for key in ("module", "contract"):
        rel = compose.get(key)
        if not rel:
            raise PublicationError(
                f"{path}#compose.{key} 是空的。兼容门面必须说出真正的 producer 是谁——"
                f"否则「兼容」两个字会被读成「还没有实现」")
        if not (ROOT / rel).is_file():
            raise PublicationError(
                f"{path}#compose.{key} 指向 {rel!r}，而它不在这个 revision 里。"
                f"一份指不到 producer 的契约把读者留在了原地")


def _check_facade(document: dict, path: Path) -> dict:
    facade = document.get("facade")
    if not isinstance(facade, dict):
        raise PublicationError(
            f"{path}#facade 缺失。这是这份契约的主语：保留的是哪个兼容入口")
    if not facade.get("ability"):
        raise PublicationError(f"{path}#facade.ability 是空的")
    if facade.get("new_skill_identity") is not False:
        raise PublicationError(
            f"{path}#facade.new_skill_identity 是 {facade.get('new_skill_identity')!r}。"
            f"设计 §8 的原话是「只有实际需要才独立新 Skill 身份」，而当时判定不需要；"
            f"改成 true 就要连带说出那个「实际需要」是什么，不能只改一个布尔值")
    return facade


def _router_file(rel: str) -> str:
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except OSError as exc:
        raise PublicationError(
            f"路由权威 {rel} 读不出来：{exc}。契约里的 route 无法被核对，"
            f"而一个没被核对的副本就是一个会漂移的副本") from exc


def _check_route(facade: dict) -> None:
    """契约里的路由必须和**每一处**本地权威逐字一致。

    这一条有牙齿是因为它有反例：`report.publish` 有两处定义，而第二处是本仓历史上真的
    长出来过的东西。改了一处而漏了另一处，读者的两个界面就会指两个不同的 Skill。
    """
    claimed = facade.get("route")
    authorities = facade.get("route_authorities") or []
    if not claimed or not authorities:
        raise PublicationError(
            "facade.route 与 facade.route_authorities 都必须给出：一个没有被核对过的"
            "路由声明，和一个没被声明过的权威，是同一件事的两面")

    for entry in authorities:
        rel = entry.get("file")
        form = entry.get("form")
        if not rel or form not in ("route-string", "capability-list"):
            raise PublicationError(
                f"route_authorities 的每一条都要有 file 和 form"
                f"（route-string / capability-list）：{entry!r}")
        text = _router_file(rel)
        if form == "route-string":
            try:
                routes = (json.loads(text) or {}).get("routes") or {}
            except json.JSONDecodeError as exc:
                raise PublicationError(f"{rel} 不是合法 JSON：{exc}") from exc
            observed = routes.get(claimed)
        else:
            observed = _capability_list(text, entry.get("section"), rel)
        if observed != facade.get("ability"):
            raise PublicationError(
                f"{rel} 把 {claimed!r} 路由到 {observed!r}，而契约说兼容入口是 "
                f"{facade['ability']!r}。两处定义必须一致——不一致时读者会拿到两份"
                f"互相矛盾的正确回答")


def _capability_list(text: str, section: str | None, rel: str) -> str | None:
    """从 `context/resolver.py` 的 `KIND_CAPABILITIES` 里取一个 kind 的第一个 capability。

    解析而不是导入：导入那个模块会执行它的 root 解析，而这里只想知道一个字符串。取的
    是**第一个**元素，因为那份 mapping 的读法就是「这个 kind 的活主要归谁」。
    """
    if not section:
        raise PublicationError(f"{rel} 声明为 capability-list，却没有 section")
    key = section.rsplit(".", 1)[-1]
    match = re.search(rf'["\']{re.escape(key)}["\']\s*:\s*\[([^\]]*)\]', text)
    if not match:
        raise PublicationError(
            f"{rel} 里找不到 {section!r}。契约指着一处不存在的权威，"
            f"核对就会退化成对一个 None 的核对——而 None 与任何名字都不相等，"
            f"于是这条检查会永远红，或者永远绿")
    names = re.findall(r'["\']([^"\']+)["\']', match.group(1))
    return names[0] if names else None


def _check_delegations(document: dict, path: Path) -> None:
    delegations = document.get("delegations")
    if not isinstance(delegations, list) or not delegations:
        raise PublicationError(
            f"{path}#delegations 是空的。B8 现在**不是**「没有欠账」，而是欠着四处；"
            f"空名单会让「还没做」读成「不用做」")
    ids = [entry.get("id") for entry in delegations]
    if sorted(ids) != sorted(DESIGN_DELEGATIONS):
        missing = sorted(set(DESIGN_DELEGATIONS) - set(ids))
        extra = sorted(set(ids) - set(DESIGN_DELEGATIONS))
        raise PublicationError(
            f"{path}#delegations 的 id 是 {ids!r}，设计 §8 逐字点名的是 "
            f"{list(DESIGN_DELEGATIONS)!r}（少 {missing}，多 {extra}）。"
            f"少一处会把一条还没做的移除读成已经不存在，多一处则是替设计加了一条"
            f"没人决定过的移除")

    for entry in delegations:
        where = f"{path}#delegations[{entry.get('id')}]"
        if entry.get("state") not in DELEGATION_STATES:
            raise PublicationError(
                f"{where}.state 是 {entry.get('state')!r}，不在 {list(DELEGATION_STATES)} "
                f"里。已经迁完的职责应当从这份契约里消失，而不是换一个词留在原地")
        owners = entry.get("owners")
        if not isinstance(owners, list):
            raise PublicationError(
                f"{where}.owners 缺失。空名单是一个答案（这条要求被取消了），"
                f"但缺字段只是一个没人写过的字段——两者必须能分开读")
        if not owners and not entry.get("why"):
            raise PublicationError(
                f"{where} 没有接手方，却没有 `why` 说清楚为什么。"
                f"一个空的 owners 与一个忘了填的 owners 长得完全一样")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise PublicationError(
                f"{where}.evidence 是空的。一条声称「现在还在哪」的条目必须有坐标")
        for item in evidence:
            for key in ("path", "line", "text"):
                if item.get(key) in (None, ""):
                    raise PublicationError(f"{where}.evidence 有一条缺 {key!r}：{item!r}")
        _check_evidence_revision(entry, where)


def _check_evidence_revision(entry: dict, where: str) -> None:
    """行号必须带 revision。

    本仓已经因为读工作树上的行号得到过「引错了」的假结论一次。一处改动移一行，一份没有
    revision 的证据就会安静地指向另一段文字——而它读起来仍然像一个坐标。
    """
    revision = entry.get("at_revision")
    if not revision or not re.fullmatch(r"[0-9a-f]{40}", str(revision)):
        raise PublicationError(
            f"{where}.at_revision 是 {revision!r}，不是一个完整 sha。行号只在指定 revision "
            f"时才有意义；没有它，这条证据在任何人动一下那个文件之后都会指向别处")


def _check_consumers(document: dict, path: Path) -> None:
    consumers = document.get("consumers")
    if not isinstance(consumers, list) or not consumers:
        raise PublicationError(
            f"{path}#consumers 是空的。四处移除被同一个 consumer 挡着；"
            f"不写下它，等待就没有对象，而一次没有对象的等待无法被复核")
    for entry in consumers:
        where = f"{path}#consumers[{entry.get('id')}]"
        disposition = entry.get("disposition")
        if disposition not in CONSUMER_DISPOSITIONS:
            raise PublicationError(
                f"{where}.disposition 是 {disposition!r}，不在 "
                f"{list(CONSUMER_DISPOSITIONS)} 里")
        for key in ("repository", "branch", "why"):
            if not entry.get(key):
                raise PublicationError(f"{where} 缺 {key!r}")
        for key in ("paths", "pending_interfaces"):
            if not entry.get(key):
                raise PublicationError(
                    f"{where}.{key} 是空的。等待一个活动 consumer 时要记的正是这两样："
                    f"它占着哪些路径，以及合并之后要按哪张表逐条落")


def delegations(path: Path | str = CONTRACT) -> list[dict]:
    """还欠着的移除，连同它们各自的接手方与证据。"""
    return list(contract(path).get("delegations") or [])


def waiting(path: Path | str = CONTRACT) -> list[dict]:
    """被活动 consumer 挡住的那些条目。"""
    return [entry for entry in contract(path).get("consumers") or []
            if entry.get("disposition") == "MIGRATION_WAITING_ON_ACTIVE_CONSUMER"]


def summary(path: Path | str = CONTRACT) -> dict:
    """一份给人和给机器读的同一份形状的摘要。"""
    document = contract(path)
    kept = document["facade"]
    return {
        "schema": CONTRACT_SCHEMA,
        "compose": document["compose"],
        "facade": {"ability": kept["ability"], "route": kept["route"],
                   "new_skill_identity": kept["new_skill_identity"]},
        "delegations": [{"id": entry["id"], "owners": entry["owners"],
                         "state": entry["state"],
                         "evidence": len(entry["evidence"])}
                        for entry in document["delegations"]],
        "waiting": [{"id": entry["id"], "branch": entry["branch"],
                     "head": entry.get("head"),
                     "pending_interfaces": len(entry["pending_interfaces"])}
                    for entry in waiting(path)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--contract", type=Path, default=CONTRACT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = summary(args.contract)
    except PublicationError as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"兼容入口      {report['facade']['ability']}  "
          f"({report['facade']['route']})")
    print(f"新 Skill 身份 {report['facade']['new_skill_identity']}")
    for entry in report["delegations"]:
        owners = ",".join(entry["owners"]) or "(取消，无接手方)"
        print(f"  待移除      {entry['id']:<26} -> {owners}  "
              f"[{entry['state']}] {entry['evidence']} 处证据")
    for entry in report["waiting"]:
        print(f"  被挡住      {entry['id']}  {entry['branch']}@{entry['head']}  "
              f"{entry['pending_interfaces']} 个待落接口")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
