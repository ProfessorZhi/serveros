"""19:20 备好的待决，20:00 必须以一次真实投递交到本人手上。

WHY THIS MODULE EXISTS AT ALL

`intake/daily_reading.py#alignment` 已经把「机器不能自决的那些判断」写成了
`alignment.json`。那是一份**记录**，不是一次**告知**，而两者的磁盘形态完全一样：
一份没人看到的待决，和一份从来没被准备过的待决，字节上无法区分。这正是 §P5 给日报
点名的那个形状——「没投出去的日报和不存在的一模一样」——晚报是同一个失败换了文件名。
实测印证：到 2026-10-06 为止 `alignment.json` 全仓只有一个读者，就是写它的那个函数。

所以这里只做两件事，不做第三件：

  * `compose` 把 19:20 的记录压成一份四段简报，写进一个**指明自身版本**的目录
    （`alignment-brief.md` + `version.json`）。它不发送任何东西；
  * 投递本身**不在这里重写**。`runtime/report_delivery.py` 已经持有
    intent → 单次尝试 → 回执 的完整纪律，而它的 `held_version` 认这个目录的
    `version.json`。于是 `serverosctl report request` / `report deliver` 原样把简报送出去：
    幂等键、失败不谎报成功、重试不重复投递、被中断的尝试要去对账而不是重发，
    全部沿用同一条路径，一行都不用复制。

第二点是这个模块存在的主要理由。为简报另写一套投递，就是同一套纪律的第二份拷贝；
两份拷贝会在其中一份多出一个状态的那天开始悄悄分叉，而分叉的方向没人会注意到。

WHY EXACTLY FOUR SECTIONS

对齐卡只显示四类，因为每一类对应一个**不同的动作**，混在一起会让人无法行动：

    自动更新    机器已按既有授权自己提交的。人不需要做任何事，只需要知道
    需要人决定  机器不能自决的。人不点头它就永远停在那里
    冲突        新读解与卡片现状、或与另一次读解互相矛盾。不解决则两边都不可信
    理解缺口    这一轮**没能读到**的来源。它不是「没有变化」，是「不知道」

一条「无」必须被写出来，不能省略：空的一节和没人检查过的一节，在卡片上长得一样。

WHAT IT DELIBERATELY DOES NOT DO

它不确认任何 claim、不写卡片、不提升 Requirement、不选 Focus、不建 Work Item。
`alignment.json` 的每一行仍然要在语义 writer 的版本检查下重新验证之后才能被接受——
这份简报只是把「有什么在等人」讲清楚，它自己不是那个人的回答。它也不把「没读到的
来源」说成「没有变化」：拿不到运行记录时，缺口那一节写的是「本次没有量过」。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import tempfile
from pathlib import Path

#: The brief and the statement that names its version. `version.json` is
#: deliberately the same filename `runtime/report_delivery.py#VERSION_FILE`
#: reads: one resolver covers a report's publication receipt and this brief's
#: version statement, so the delivery discipline never learns there are two
#: kinds of directory.
BRIEF_FILE = "alignment-brief.md"
VERSION_FILE = "version.json"

SCHEMA = "serveros/alignment-brief.v1"
VERSION_SCHEMA = "serveros/alignment-brief-version.v1"
ALIGNMENT_SCHEMA = "serveros/reading-alignment.v1"
RESOLUTION_SCHEMA = "serveros/understanding-semantic.v1"

#: The four sections, in the order a person reads them: what already happened,
#: then what is asked of them, then what contradicts something, then what is
#: unknown. The order is load-bearing rather than cosmetic — it runs from "no
#: action needed" to "action required" to "two things cannot both be true" to
#: "we do not know", and a reader who stops after the first line still learns
#: the most that could be said without them.
SECTIONS = ("auto", "decision", "conflict", "gap")

HEADINGS = {
    "auto": "自动更新",
    "decision": "需要人决定",
    "conflict": "冲突",
    "gap": "理解缺口",
}

#: How much of a verbatim source excerpt a card carries before it is cut. Long
#: enough to recognise the passage, short enough that the card stays readable on
#: a phone — and the cut is always marked, because an excerpt that silently ends
#: early reads as though the source ended there.
EXCERPT_LIMIT = 160

#: Beijing, because every time a person reads in this system is reported in
#: their clock. A UTC timestamp on a 19:20 card would make the reader convert.
BEIJING = datetime.timezone(datetime.timedelta(hours=8))

#: The stop codes that mean the run reached where it was going: it read what it
#: was going to read and then had a reason to stop. Everything else means there
#: were ranges it never reached — `BUDGET_EXHAUSTED` and `SOURCE_UNAVAILABLE`
#: are the two that exist today — and those ranges are absent from `unread`
#: rather than recorded in it, because nothing ever tried them and failed.
COMPLETE_STOP_CODES = ("NO_CHANGE", "NEEDS_DECISION")


class Refused(Exception):
    """The brief must not be composed, with the reason naming itself."""


# ────────────────────────────────────────────────────────────────────────────
# reading and writing, kept small on purpose
# ────────────────────────────────────────────────────────────────────────────

def _read(path: Path) -> dict | None:
    """A mapping, or `None` for "there is no readable record here".

    An unreadable file is its own case rather than being flattened into "absent":
    a resolution that exists and cannot be parsed is evidence that a reading
    happened, and treating it as never-having-happened is how a claim that is
    waiting for a person quietly leaves the brief.
    """
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"{path} 存在但读不出来：{exc}") from exc
    if not isinstance(loaded, dict):
        raise Refused(f"{path} 不是一个映射，读不出任何一条已知的字段")
    return loaded


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_record(path) -> dict | None:
    """A run record, or `None` when there is none to read.

    Public because the caller is the one that decides whether a missing record is
    an error. Here it is not: a brief composed without the 19:20 run record
    reports its gap section as "not measured", which is a different and honest
    sentence — and the CLI is where that choice is made, not this module.
    """
    return _read(Path(path))


def beijing_day(at=None) -> str:
    """The Beijing day `at` names, as `YYYY-MM-DD`. The wall clock if absent.

    Exposed because the 20:00 entry has to ask the same question three times —
    which day's preparation, which day's run record, which directory to deliver
    into — and three private calls to `_moment` would be three chances to answer
    it differently inside one evening's act.
    """
    return _moment(at).strftime("%Y-%m-%d")


def select_run_record(record_dir, day: str) -> tuple[dict | None, str]:
    """The perception run that finished on `day`, and the sentence saying which.

    Dated by the record's own `finished_at`, never by the file's modification
    time. A record restored from a backup, copied by hand, or left behind by a
    `retake` carries the time it finished and not the time it was touched — and
    mtime would let last night's run answer for tonight's. The gap section would
    then be filled from a pass that read a different day's sources, which looks
    exactly like a measured answer.

    The second return value is the reason, and it is returned rather than logged
    because the card is where it has to end up: "there is no record" and "the
    newest record is from another day" are different sentences to the person
    reading the 理解缺口 section, and only the caller can print either.
    """
    directory = Path(record_dir)
    if not directory.is_dir():
        return None, f"{directory} 不存在——这一轮没有可读的 perception 运行记录"
    newest = None
    newest_at = None
    skipped = 0
    for path in sorted(directory.glob("*.json")):
        record = read_record(path)          # raises on a file that exists and is unreadable
        if record is None:                  # a glob hit that is not a readable file
            skipped += 1
            continue
        stamp = record.get("finished_at") or record.get("claimed_at")
        try:
            finished = datetime.datetime.fromisoformat(str(stamp))
        except (TypeError, ValueError):
            skipped += 1
            continue
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=datetime.timezone.utc)
        finished = finished.astimezone(BEIJING)
        if finished.strftime("%Y-%m-%d") != day:
            continue
        if newest_at is None or finished > newest_at:
            newest, newest_at = record, finished
    if newest is None:
        return None, (f"{directory} 里没有 {day}（北京）完成的运行记录——后面缺口那一节"
                      f"因此写的是「没有量过」，不是「没有缺口」")
    note = f"{newest_at:%Y-%m-%d %H:%M}（北京）完成"
    if skipped:
        note += f"；另有 {skipped} 份没有可解析的完成时间，没有被算进来"
    return newest, note


def _atomic_text(path: Path, text: str) -> None:
    """Write, or refuse. A half-written brief is one that names a version of itself.

    The symlink refusal is `intake/daily_reading.py#_write`'s, for its reason: a
    brief written through a symlink lands somewhere the version statement does
    not name, and the delivery below would then hash one file while a reader
    read another.
    """
    if path.is_symlink():
        raise Refused(f"{path} 是一个符号链接。简报会写到它指向的地方，"
                      f"而版本声明记的是这条路径——投出去的东西和记下来的就不是同一份")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".alignment-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_json(path: Path, payload: dict) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                  indent=2) + "\n")


def _now(at=None) -> str:
    if at:
        return str(at)
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


def _moment(at) -> datetime.datetime:
    """The instant `at` names, on the reader's clock. The wall clock if absent.

    A naive stamp is read as UTC rather than as local time, matching `_now`,
    which writes UTC — so a round trip through this module never moves the day.
    """
    if not at:
        return datetime.datetime.now(datetime.timezone.utc).astimezone(BEIJING)
    try:
        moment = datetime.datetime.fromisoformat(str(at))
    except ValueError:
        return datetime.datetime.now(datetime.timezone.utc).astimezone(BEIJING)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(BEIJING)


def _beijing(stamp: str) -> str:
    """The same instant on the reader's clock, or the input unchanged.

    Falling back to the raw string rather than raising: a stamp this module
    cannot parse is still the stamp the caller supplied, and replacing it with
    the wall clock would put a time on the card that nothing recorded.
    """
    try:
        datetime.datetime.fromisoformat(str(stamp))
    except ValueError:
        return str(stamp)
    return _moment(stamp).strftime("%Y-%m-%d %H:%M")


# ────────────────────────────────────────────────────────────────────────────
# gathering: which claims fall in which of the four
# ────────────────────────────────────────────────────────────────────────────

def _excerpt(claim: dict) -> str | None:
    text = ((claim.get("support") or {}).get("excerpt") or "").strip()
    if not text:
        return None
    if len(text) <= EXCERPT_LIMIT:
        return text
    return text[:EXCERPT_LIMIT] + "…（已截断）"


def _subject(claim: dict) -> str:
    return str(claim.get("subject") or claim.get("id") or "（未命名）")


def _movement(claim: dict) -> str:
    """What the claim says NOW, and what it replaced when it says so.

    `was` is shown only when it is there: printing "原：（无）" for every first
    observation would bury the handful of claims that actually changed something.
    """
    assertion = claim.get("assertion") or {}
    now = str(assertion.get("now") or "").strip()
    was = assertion.get("was")
    if was is None or str(was).strip() == "":
        return now
    return f"{now}（原：{was}）"


def _decision_row(reading: str, card, source: dict, claim: dict) -> dict:
    return {"id": claim.get("id"), "kind": claim.get("kind"),
            "subject": _subject(claim), "movement": _movement(claim),
            "because": claim.get("confirmation_why"),
            "excerpt": _excerpt(claim),
            "positions": list((claim.get("support") or {}).get("positions") or []),
            "card": card or claim.get("card"), "reading": reading,
            "source_manifest": (source or {}).get("manifest")}


def _conflict_row(reading: str, card, claim: dict, *, settled: bool) -> dict:
    return {"id": claim.get("id"), "subject": _subject(claim),
            "movement": _movement(claim),
            "conflicts": list(claim.get("conflicts") or []),
            "supersedes": list(claim.get("supersedes") or []),
            "settled": settled, "card": card or claim.get("card"),
            "reading": reading}


def gather(directory, *, run_record=None) -> dict:
    """Sort this run's own records into the four sections, and count them.

    Two files are read and neither is trusted further than it goes:

      * `alignment.json` is the authority for **what is still pending**, because
        it is already filtered against the semantic writer's receipts — a claim a
        person has since accepted must not appear here as though it still waits;
      * each `readings/*/resolution.json` is the authority for **what committed
        on the machine's own authority**, which `alignment.json` does not carry
        at all: it lists only the pending ones.

    The join between them is where a claim can fall through. One that was
    `awaiting` when it was resolved, and is not listed as pending now, was
    settled by a receipt in between — the person is not being asked for it, so it
    belongs under 自动更新, and it is put there **labelled** rather than dropped,
    because "I expected to be asked and was not" is exactly the thing a brief
    that silently omits it cannot answer.
    """
    directory = Path(directory)
    alignment = _read(directory / "alignment.json")
    if alignment is None:
        raise Refused(
            f"{directory} 里没有 alignment.json：19:20 的准备没有留下待决记录，"
            f"所以没有东西可以对齐。把它当成「今天没有问题」正是在最需要证据的时候"
            f"报了一个好看的空数字")
    if alignment.get("schema") != ALIGNMENT_SCHEMA:
        raise Refused(f"alignment.json 声明的 schema 是 "
                      f"{alignment.get('schema')!r}，不是 {ALIGNMENT_SCHEMA!r}。"
                      f"认不出的待决清单会被读成一份空的")
    pending = alignment.get("pending")
    if not isinstance(pending, list):
        raise Refused(f"alignment.json 的 pending 是 {type(pending).__name__}，不是列表")

    decisions: list[dict] = []
    pending_ids: set[tuple] = set()
    for row in pending:
        reading = str(row.get("reading") or "")
        for claim in row.get("claims") or []:
            pending_ids.add((reading, claim.get("id")))
            decisions.append(_decision_row(reading, row.get("read", {}).get("card")
                                           if isinstance(row.get("read"), dict) else None,
                                           row.get("source") or {}, claim))

    auto: list[dict] = []
    conflicts: list[dict] = []
    retracted: list[dict] = []
    sources: list[str] = []
    unresolved: list[dict] = []
    resolved_keys: set[str] = set()

    for path in sorted((directory / "readings").glob("*/resolution.json")):
        resolution = _read(path)
        if resolution is None:
            continue
        if resolution.get("schema") != RESOLUTION_SCHEMA:
            raise Refused(f"{path} 声明的 schema 是 {resolution.get('schema')!r}，"
                          f"不是 {RESOLUTION_SCHEMA!r}")
        read = resolution.get("read") if isinstance(resolution.get("read"), dict) else {}
        source = resolution.get("reading") if isinstance(resolution.get("reading"), dict) else {}
        card = read.get("card")
        manifest = source.get("manifest")
        if manifest and manifest not in sources:
            sources.append(str(manifest))
        # The join key is the READING path, not this resolution's path.
        # `alignment.json` names each pending reading by its `reading.json`, and
        # resolution.json carries no path of its own at all — so the key has to
        # be derived from the sibling name. Taking `str(path)` here would make
        # every claim look like it had been settled by a receipt, which is the
        # worst possible failure: the brief would report 21 commits and 21
        # decisions off the same 21 claims, and the two numbers agreeing would
        # look like corroboration.
        reading_key = str(path.with_name("reading.json"))
        resolved_keys.add(reading_key)

        for claim in resolution.get("committed") or []:
            row = _decision_row(str(path), card, source, claim)
            row["confirmation"] = claim.get("confirmation")
            row["why"] = claim.get("confirmation_why")
            auto.append(row)
            if claim.get("conflicts") or claim.get("supersedes"):
                conflicts.append(_conflict_row(str(path), card, claim, settled=True))

        for claim in resolution.get("awaiting") or []:
            if (reading_key, claim.get("id")) not in pending_ids:
                row = _decision_row(str(path), card, source, claim)
                row["confirmation"] = None
                row["why"] = ("它在本轮判定时仍在等待，而 alignment.json 已不再列它——"
                              "中间有一份回执把它提交掉了。人不需要为它做什么，"
                              "但它出现在这里，是为了让「本来要问我、结果没问」这件事有据可查")
                auto.append(row)
            elif claim.get("conflicts") or claim.get("supersedes"):
                conflicts.append(_conflict_row(str(path), card, claim, settled=False))

        for claim_id in resolution.get("retracted") or []:
            retracted.append({"retracts": claim_id, "card": card,
                              "manifest": manifest, "reading": str(path)})

    # A join that matches nothing must not read as a join that matched. If
    # alignment.json names readings and not one of them has a resolution beside
    # it, the two records are not about the same run — and the symptom of
    # carrying on would be a brief reporting every pending claim as already
    # settled, which is the exact opposite of the truth and looks tidy.
    pending_paths = {reading for reading, _ in pending_ids}
    if pending_paths and resolved_keys and pending_paths.isdisjoint(resolved_keys):
        raise Refused(
            f"alignment.json 列的 reading 与 {directory}/readings/ 下的 resolution.json "
            f"没有一处对得上：前者说的是 {sorted(pending_paths)[:2]}，后者是 "
            f"{sorted(resolved_keys)[:2]}。这两份记录描述的不是同一次运行。"
            f"接着算下去，每一条待决都会被读成「已经被回执提交过」——"
            f"那正好是事实的反面，而且数字看起来很整齐")

    for child in sorted((directory / "readings").glob("*")):
        if (child / "reading.json").is_file() and not (child / "resolution.json").is_file():
            unresolved.append({
                "id": child.name,
                "why": "这一次读解留下了 reading.json 却没有 resolution.json："
                       "读到了，但没有人判定它是什么"})

    measured = run_record is not None
    gaps: list[dict] = []
    if measured:
        outcomes = run_record.get("outcomes") if isinstance(run_record.get("outcomes"), dict) else {}
        for row in outcomes.get("unread") or []:
            if isinstance(row, dict):
                gaps.append({"id": row.get("id"), "why": row.get("why")})
        code = run_record.get("stopped_because")
        if code and code not in COMPLETE_STOP_CODES:
            # A pass that stopped early has not covered what it was going to
            # cover, and `unread` is empty in exactly that case: the ranges that
            # were never reached never failed, so nothing recorded them as
            # unread. Reporting "no gaps" here would be reporting the coverage
            # of a pass that gave up — the measured 2026-10-06 19:20 run reads
            # `BUDGET_EXHAUSTED` with `outcomes.unread == []`.
            gaps.append({"id": f"run:{code}",
                         "why": f"19:20 的这一次没有把区间读完就停了（{code}）："
                                f"{run_record.get('stopped_why')}。"
                                f"「未读到的来源」那一栏因此是空的——"
                                f"不是因为都读到了，而是因为后面的还没走到"})
    gaps.extend(unresolved)
    for row in retracted:
        gaps.append({"id": row["retracts"],
                     "why": f"本轮读到一次撤回/取代声明（来源 {row.get('manifest')}）："
                            f"被撤掉的那条现在不成立，而这属于来源侧的事实，"
                            f"不是本轮新提出的判断"})

    return {"brief": {"sources": sources, "measured_gaps": measured},
            "auto": auto, "decision": decisions, "conflict": conflicts, "gap": gaps,
            "counts": {"auto": len(auto), "decision": len(decisions),
                       "conflict": len(conflicts), "gap": len(gaps)}}


# ────────────────────────────────────────────────────────────────────────────
# the four sections, as a card
# ────────────────────────────────────────────────────────────────────────────

def render(brief: dict, *, at=None) -> str:
    """The brief as markdown, in the order the sections are declared.

    Short lines and no tables: this is read on a phone. Every section prints its
    own count and says so when it is empty, because a section that is absent and
    a section that was checked and came out empty look identical to a reader,
    and only one of them means "nothing needs you".
    """
    stamp = _beijing(_now(at))
    counts = brief["counts"]
    lines = [f"# 对齐 · {stamp}", ""]
    sources = brief["brief"]["sources"]
    lines.append("来源：" + ("、".join(f"`{s}`" for s in sources) if sources
                             else "本轮没有可复述的来源"))
    lines.append("")

    lines.append(f"## {HEADINGS['auto']}（{counts['auto']}）")
    if not brief["auto"]:
        lines.append("本轮没有由既有授权自动提交的内容。")
    for row in brief["auto"]:
        lines.append(f"- **{row['subject']}**：{row['movement']}")
        if row.get("why"):
            lines.append(f"  {row['why']}")
    lines.append("")

    lines.append(f"## {HEADINGS['decision']}（{counts['decision']}）")
    if not brief["decision"]:
        lines.append("本轮没有需要人点头的内容。")
    for row in brief["decision"]:
        lines.append(f"- **{row['subject']}**：{row['movement']}")
        if row.get("excerpt"):
            lines.append(f"  依据：{row['excerpt']}")
        if row.get("positions"):
            lines.append("  引用位置：" + "、".join(str(p) for p in row["positions"]))
        if row.get("because"):
            lines.append(f"  为什么需要人：{row['because']}")
    lines.append("")

    lines.append(f"## {HEADINGS['conflict']}（{counts['conflict']}）")
    if not brief["conflict"]:
        lines.append("本轮没有读到互相矛盾的内容。")
    for row in brief["conflict"]:
        lines.append(f"- **{row['subject']}**：{row['movement']}")
        if row["conflicts"]:
            lines.append("  与以下内容冲突：" + "、".join(f"`{c}`" for c in row["conflicts"]))
        if row["supersedes"]:
            lines.append("  取代：" + "、".join(f"`{s}`" for s in row["supersedes"]))
        lines.append("  状态：" + ("已提交" if row["settled"] else "仍在等待决定"))
    lines.append("")

    lines.append(f"## {HEADINGS['gap']}（{counts['gap']}）")
    if not brief["gap"]:
        lines.append("本次没有量到未完成的来源。" if brief["brief"]["measured_gaps"]
                     else "本次没有拿到 19:20 的运行记录，所以「有没有没读到的来源」这一栏"
                          "**没有量过**——它不是「没有」。")
    for row in brief["gap"]:
        lines.append(f"- `{row.get('id')}`：{row.get('why')}")
    lines.append("")
    lines.append(f"—— 本节{_prepared_sentence(brief)}。它只说明有什么在等人，"
                 "不代替任何人的答复；是否接受、是否成为需求，是下一步。")
    return "\n".join(lines) + "\n"


def _prepared_sentence(brief: dict) -> str:
    """The clause the footer uses to say where these rows came from.

    It used to say "由 19:20 的准备结果生成" — a claim about a schedule, not about
    a file. On this node the first real card was composed from a preparation that
    finished at 14:17 Beijing, and the 运行记录 line a few rows above said so
    while the footer said 19:20; the reader had a provenance line they could not
    check against anything. `compose` reads the stamp off `alignment.json` before
    rendering, so this names a time that exists. A caller rendering a gathered
    brief with no composition behind it has no stamp to name, and says that
    rather than falling back to the schedule — "which pass was this?" is a
    question a person can act on, "19:20" is not.
    """
    prepared = (brief.get("brief") or {}).get("prepared_at")
    if not prepared:
        return "由一次准备结果生成，但没有读出它是什么时候写下的"
    return f"由 {_beijing(prepared)}（北京）写出的准备结果生成"


# ────────────────────────────────────────────────────────────────────────────
# the composition, which is where the version comes from
# ────────────────────────────────────────────────────────────────────────────

def _refuse_a_stale_preparation(directory: Path, at):
    """Tonight's brief must be composed from tonight's preparation.

    Returns when the preparation was written (Beijing), so the eventual footer
    names the stamp this guard just compared rather than the time it was
    scheduled for. `None` when there is no preparation to date at all.

    This is the one failure that would be invisible in the card. If the 19:20
    pass did not run — the timer was off, the service failed, the host was down
    — then `alignment.json` is yesterday's, and composing from it produces a card
    that looks exactly like a fresh one and says the same calm thing: nothing
    needs you. The person reading it has no way to tell that the answer is a day
    old, and "no news" would be believed precisely when there could be news.

    Compared by Beijing day rather than by elapsed hours: the alignment is about
    a day's reading, and a preparation finished at 23:50 belongs to that day even
    if the brief is composed just past midnight. A caller that means a different
    day states it through `at`, which is why the guard compares against the stamp
    it was given rather than against the wall clock.
    """
    prepared = _read(directory / "alignment.json")
    if prepared is None:
        return None  # `gather` names this case precisely; no need to say it twice
    try:
        age = (directory / "alignment.json").stat().st_mtime
    except OSError as exc:
        raise Refused(f"读不出 {directory / 'alignment.json'} 的修改时间：{exc}") from exc
    written = datetime.datetime.fromtimestamp(age, tz=datetime.timezone.utc).astimezone(BEIJING)
    moment = _moment(at)
    if written.date() != moment.date():
        raise Refused(
            f"这次对齐用的 alignment.json 是 {written:%Y-%m-%d %H:%M}（北京）写的，"
            f"而这份简报说的是 {moment:%Y-%m-%d} 这一天。19:20 的准备没有在当天跑成——"
            f"把上一次准备的结果当作今晚的对齐发出去，收件人会收到一张和平时一样平静的卡，"
            f"而它说的其实是昨天的事。要么让 19:20 的那一步真正跑起来，"
            f"要么用 --at 指明这份简报到底属于哪一天")
    return written


def compose(directory, out_dir, *, run_record=None, period=None, at=None) -> dict:
    """Write the brief, then the statement that names this version of it.

    The order is the point. `runtime/report_delivery.py#held_version` re-hashes
    the file and compares it to what the version statement claims, and a
    statement written first would have nothing to claim. So the brief lands, its
    bytes are read back off disk, and only then is `version.json` written around
    the hash of what is actually there — never the hash of what was in memory.

    Sending nothing is not an accident of this function being short. Composing
    and delivering are separated so that a composed brief can sit through a quiet
    period and be re-checked — receiver, version, authorization — at the moment
    it actually goes out.
    """
    directory = Path(directory)
    out = Path(out_dir)
    if not out.is_absolute():
        raise Refused(
            f"out_dir 是 {out_dir!r}，不是绝对路径。投递回执必须能被人按同一条路径找回"
            f"来，而相对路径的含义取决于谁在什么时候从哪个目录调用它")
    prepared = _refuse_a_stale_preparation(directory, at)
    brief = gather(directory, run_record=run_record)
    if prepared is not None:
        brief["brief"]["prepared_at"] = prepared.isoformat()
    text = render(brief, at=at)
    target = out / BRIEF_FILE
    _atomic_text(target, text)
    body = target.read_bytes()
    version = {"schema": VERSION_SCHEMA, "file": str(target), "sha256": _sha(body),
               "bytes": len(body), "period": period,
               "generated_at": _now(at), "counts": brief["counts"],
               "from_run": str(directory),
               "prepared_at": prepared.isoformat() if prepared is not None else None}
    _atomic_json(out / VERSION_FILE, version)
    return {"brief": str(target), "version": str(out / VERSION_FILE),
            "sha256": version["sha256"], "bytes": version["bytes"],
            "counts": brief["counts"],
            "why": ("写下了这一版简报和指明它的版本声明。这一步没有发送任何东西——"
                    "投递仍要走 report request / report deliver")}
