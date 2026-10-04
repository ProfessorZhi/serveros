#!/usr/bin/env python3
"""Whether saying something is due — decided here, not inside a sender.

WHY THIS MODULE EXISTS
----------------------
Until now the answer lived in the sender: `ops/scripts/tokyo-monitor/
monitor_card.py` decided when to call `send`, so «when does a person get told»
was a property of a script in another repository rather than of the node. Two
consumers could only agree by running the same file, and a reader who wanted to
know why the 14:00 update did or did not arrive had nowhere to look.

The fact that forced the split is a transport fact, and it is worth stating
because the whole document is shaped by it:

    **a patched message does not raise a new notification.**

On 2026-09-30 this node patched one Feishu message seven times between 08:00 and
14:00. Every patch was delivered. The reader's phone was silent for all seven,
which from the phone is indistinguishable from a monitor that had died. The old
rule — «当天只维护一张 Status Card，之后每小时只 PATCH，不新增聊天记录» — was
written to keep ONE authoritative view of «现在怎么样», and that half is right.
What it got wrong is treating «one card» and «one message» as the same claim.
They are not: the card is a view, the message is a delivery, and only the second
one is what a notification is attached to.

So this module answers one question about the delivery half:

    given the tick, the day's card and how it changed, is a Status Update
    Notice due, and what may it ask of the transport?

THE NOTICE OWNS NOTHING
-----------------------
A notice is not a second status card, and the difference is not a matter of
keeping it short. It is that a notice cannot state a fact the card does not:
`notice()` REFUSES to compose one that does not name the card version it is
about, because a notice pointing at nothing is a card, and a card whose cadence
is hourly is exactly what the invariant being defended forbids. The words
themselves are not composed here — they are `cards.notice_text`, because the
human vocabulary belongs to the one render authority this tree has. What this
module produces is the decision: due or not, why, under which identity, at which
attention bucket, and what the transport is allowed to do about it.

WHAT IS DELIBERATELY ABSENT
---------------------------
No timer, no queue, no retry. The cadence is the caller's clock and always was
(§10: the window is a recommendation, and a tick a minute late is the same
tick). A scheduler in here would be a second, quieter clock with its own opinion
about how many times a person should be told.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent

CONTRACT = _HERE / "monitoring-policy.yaml"
SCHEMA = "serveros/monitoring-policy/v1"

#: The notice's own document schema. Separate from the policy's because one is
#: a declaration about this node and the other is one tick's answer, and a
#: caller that confused them would file a decision where the policy lives.
NOTICE_SCHEMA = "serveros/monitoring-notice.v1"
NOTICE_KIND = "status-update"

#: Closed, like every vocabulary in this tree. A cadence this module has not
#: been taught is a cadence nobody can hold the clock to, and the failure mode
#: of an open one is silent: the policy says `quarter-hourly`, the clock keeps
#: running hourly, and the two documents disagree without either being wrong.
#: `hourly` is the only value because it is the only one
#: `business_calendar.ROUTINE_UPDATES_PER_WORKDAY` implements — one tick per
#: hour of the 08:00–20:00 window, thirteen a workday.
CADENCES = ("hourly",)
PERSISTENT_CARDS = ("one-per-workday",)
ON_CREATE = ("card-message", "lightweight-notice", "silent")
ON_PATCH = ("lightweight-notice", "silent")
MODES = ("always", "changes-only", "attention-only")

#: The three buckets a notice can be in, and they are NOT severity.
#:
#: `severity` is a property of the finding (`notification-contract.yaml` owns
#: it), and it is the wrong axis for «may this buzz»: a watermark crossing is a
#: real finding that nobody needs to act on tonight, and a routine-looking
#: reading that stops a service needs somebody immediately. The bucket answers
#: the question the transport asks — how loudly — and `prompt` below is its
#: closed mapping onto what the transport may do.
#:
#: UNCONFIRMED — «有项目尚未读到» — lands in ATTENTION rather than in a bucket
#: of its own. It is not ROUTINE (an incomplete reading is not a good one, §6),
#: and it is not ACTION_REQUIRED (nobody is stopped). It wants a person to look,
#: which is exactly what ATTENTION says.
BUCKETS = ("ROUTINE", "ATTENTION", "ACTION_REQUIRED")

#: What may be asked of the transport. `urgent_app` is Feishu's phone buzz, and
#: the policy's default mapping keeps it for ACTION_REQUIRED alone — see the
#: contract for why thirteen buzzes a workday is the failure mode, not the goal.
PROMPTS = ("none", "urgent_app")

#: How many characters the transport will accept as a dedupe key.
#:
#: `lark-cli im +messages-send --idempotency-key` caps at 50 and does not say
#: so when it is handed more: it truncates, and a truncated key is a key for a
#: different message. `identity` below is built to fit this number rather than
#: to be trimmed to it, because the trimming is what the cap silently does.
TRANSPORT_KEY_MAX = 50

#: Why the answer was what it was. Closed, and each one names a different fix:
#: «the day's first card already announced itself», «this tick was answered
#: already», «the policy says quieter than this» and «the state moved» are four
#: different findings and a single «not due» would hide three of them.
ON_CREATE_ITSELF = "ON_CREATE_CARD_MESSAGE"
ALREADY_DELIVERED = "ALREADY_DELIVERED"
NOT_A_WORKDAY = "NOT_A_WORKDAY"
OUTSIDE_WINDOW = "OUTSIDE_WINDOW"
CALENDAR_UNKNOWN = "CALENDAR_UNKNOWN"
#: The precondition §7 says must be enforced rather than described: the notice
#: is safe to compose only after the card it points at has ARRIVED. Two ways it
#: can fail, kept apart because they are fixed by different people — a send that
#: did not arrive is a transport fault, and a send of a different version is a
#: notice about to make a claim about a card nobody has.
CARD_NOT_DELIVERED = "CARD_NOT_DELIVERED"
CARD_VERSION_NOT_DELIVERED = "CARD_VERSION_NOT_DELIVERED"
MODE_ALWAYS = "MODE_ALWAYS"
CHANGED = "CHANGED"
UNCHANGED = "UNCHANGED"
NO_PREVIOUS_ROW = "NO_PREVIOUS_ROW"
ATTENTION_PRESENT = "ATTENTION_PRESENT"
ATTENTION_ABSENT = "ATTENTION_ABSENT"
REASONS = (ON_CREATE_ITSELF, ALREADY_DELIVERED, NOT_A_WORKDAY, OUTSIDE_WINDOW,
           CALENDAR_UNKNOWN, CARD_NOT_DELIVERED, CARD_VERSION_NOT_DELIVERED,
           MODE_ALWAYS, CHANGED, UNCHANGED, NO_PREVIOUS_ROW,
           ATTENTION_PRESENT, ATTENTION_ABSENT)

#: The one outcome of a card send that makes a notice safe to compose. Spelled
#: here rather than imported: this module imports no sibling — it is loaded by
#: path, so `card_delivery.OUTCOMES` is not reachable from it — and a second
#: spelling of a closed-set value is exactly the kind of copy that drifts. What
#: keeps this one honest is a test that holds the two equal, rather than a
#: comment promising it; see `test_monitoring_policy.TheCardHasToHaveArrived`.
DELIVERED = "DELIVERED"

#: One sentence per reason, for the person reading the answer. `card notice` is
#: read by a human as often as by the timer — «为什么 14:00 没有提醒» is the
#: question this document exists to make answerable — and a reason code alone
#: answers it the way an exit status does. Closed and total: a reason added
#: without a sentence fails `check_documents` below rather than printing an
#: empty line, because a blank explanation reads as «nothing to say».
SAYS = {
    ON_CREATE_ITSELF:
        "当天卡本身是这一小时的新消息，已经提醒过了，不补第二条",
    ALREADY_DELIVERED:
        "这一小时的提醒已经发出过，重跑不产生第二条",
    NOT_A_WORKDAY:
        "今天不是工作日，不创建当天卡，也不提醒；观察与记录照常",
    OUTSIDE_WINDOW:
        "现在不在例行播报窗口内，当天卡不因普通采样新增，也就不提醒",
    CALENDAR_UNKNOWN:
        "不知道今天是不是工作日，因此不新增提醒；观察与记录照常，"
        "这不是「不需要提醒」",
    CARD_NOT_DELIVERED:
        "投递台账里最新一条 status 回执不是 DELIVERED：这条提醒要指的那一版"
        "卡片没有到达，因此不能发一条说「当天卡已更新」的话。要处理的是那次"
        "投递失败，不是提醒",
    CARD_VERSION_NOT_DELIVERED:
        "到达过的那一版卡片不是这条提醒指名的这一版：提醒会把「当天卡已更新 · "
        "<版本>」说成已经发生的事，而这一版没有到过任何人手里",
    MODE_ALWAYS:
        "每次更新都提醒（notification.mode: always）",
    CHANGED:
        "这一小时的状态与上一小时不同",
    UNCHANGED:
        "这一小时的状态与上一小时相同，changes-only 因此不提醒",
    NO_PREVIOUS_ROW:
        "没有上一小时的读数可比，按 changes-only 提醒，"
        "而不是把「没比过」说成「没变化」",
    ATTENTION_PRESENT:
        "这一小时有需要查看的项目",
    ATTENTION_ABSENT:
        "这一小时没有需要查看的项目，attention-only 因此不提醒",
}

REQUIRED = ("schema", "status_card", "notification", "incident")


class MonitoringPolicyError(RuntimeError):
    pass


def load(path=None) -> dict:
    """The policy, checked. No default is invented for a missing key.

    The same rule `testing_contract` follows: a document that does not say what
    the cadence is has not decided one, and a loader that filled in `always`
    would make a decision nobody made look like one the node had always held.
    """
    target = Path(path) if path else CONTRACT
    try:
        document = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise MonitoringPolicyError(
            f"读不到监控交互政策 {target}（{type(exc).__name__}: {exc}）。没有它"
            f"就不知道这一拍该不该提醒，而「不知道」不能被读成「不用提醒」") from exc
    except yaml.YAMLError as exc:
        raise MonitoringPolicyError(f"{target} 不是合法的 YAML：{exc}") from exc
    return check(document)


def check_vocabulary() -> None:
    """This module can speak about every answer it is able to give.

    Checked when the policy is read rather than in a test, because the failure
    it prevents is a blank line on a face: a reason added to `REASONS` without a
    sentence in `SAYS` prints an empty explanation, and «the reason is nothing»
    reads exactly like «there is nothing wrong». Both maps are closed sets, so
    the check is a comparison and not a guess.
    """
    missing = [reason for reason in REASONS if not SAYS.get(reason)]
    if missing:
        raise MonitoringPolicyError(
            f"这些 reason 没有对应的说法：{', '.join(missing)}。空白的解释和"
            f"「没什么可说」读起来一样，而这两件事在这里必须分开")
    extra = sorted(set(SAYS) - set(REASONS))
    if extra:
        raise MonitoringPolicyError(
            f"SAYS 里有词表之外的 reason：{', '.join(extra)}。没有任何答案会"
            f"用到它们，所以它们只会腐坏")


def check(document) -> dict:
    """The policy's shape, enforced so that a typo cannot silence a node."""
    check_vocabulary()
    if not isinstance(document, dict):
        raise MonitoringPolicyError(
            f"监控交互政策是一个对象，不是 {type(document).__name__}")
    missing = [key for key in REQUIRED if key not in document]
    if missing:
        raise MonitoringPolicyError(
            f"监控交互政策缺少 {', '.join(missing)}：一份没有声明节奏或没有声明"
            f"提醒方式的政策，会让「没提醒」和「不需要提醒」读起来一样")
    if document.get("schema") != SCHEMA:
        raise MonitoringPolicyError(
            f"schema 是 {document.get('schema')!r}，本模块读的是 {SCHEMA!r}")

    status_card = _section(document, "status_card")
    if status_card.get("cadence") not in CADENCES:
        raise MonitoringPolicyError(
            f"status_card.cadence 是 {status_card.get('cadence')!r}，不是 "
            f"{'、'.join(CADENCES)} 之一。词表是闭的：一个没被教过的节奏，"
            f"没有任何东西能把它和真正在跑的钟对上")
    if status_card.get("persistent_card") not in PERSISTENT_CARDS:
        raise MonitoringPolicyError(
            f"status_card.persistent_card 是 "
            f"{status_card.get('persistent_card')!r}，不是 "
            f"{'、'.join(PERSISTENT_CARDS)} 之一")

    notification = _section(document, "notification")
    if notification.get("on_create") not in ON_CREATE:
        raise MonitoringPolicyError(
            f"notification.on_create 是 {notification.get('on_create')!r}，不是 "
            f"{'、'.join(ON_CREATE)} 之一")
    if notification.get("on_patch") not in ON_PATCH:
        raise MonitoringPolicyError(
            f"notification.on_patch 是 {notification.get('on_patch')!r}，不是 "
            f"{'、'.join(ON_PATCH)} 之一")
    mode = notification.get("mode")
    if mode not in MODES:
        raise MonitoringPolicyError(
            f"notification.mode 是 {mode!r}，不是 {'、'.join(MODES)} 之一。"
            f"一个拼错的安静档位会静默地把节点变成哑的")
    modes = notification.get("modes")
    if not isinstance(modes, list) or tuple(modes) != MODES:
        raise MonitoringPolicyError(
            f"notification.modes 声明了 {modes!r}，而本模块认识的是 "
            f"{list(MODES)}。两者必须逐字相等：这份列表的用处就是让「档位表"
            f"在别处又写了一份」这件事有人拦得住")
    prompt = notification.get("prompt")
    if not isinstance(prompt, dict) or set(prompt) != set(BUCKETS):
        raise MonitoringPolicyError(
            f"notification.prompt 必须给 {list(BUCKETS)} 三个桶各一个答复，"
            f"现在给的是 {sorted(prompt) if isinstance(prompt, dict) else prompt!r}")
    for bucket, value in prompt.items():
        if value not in PROMPTS:
            raise MonitoringPolicyError(
                f"notification.prompt.{bucket} 是 {value!r}，不是 "
                f"{'、'.join(PROMPTS)} 之一")
    # Read at load rather than only where it is used, and for the reason the
    # whole `check` exists: a misspelled bucket here is not a validation error
    # in any format, it is a string that matches nothing, and the message it
    # would silence is the one that says the reader was not told anything.
    fault_notice(document)

    incident = _section(document, "incident")
    for key in ("on_open", "on_change", "on_recovery"):
        if incident.get(key) not in ("notify", "silent"):
            raise MonitoringPolicyError(
                f"incident.{key} 是 {incident.get(key)!r}，不是 notify / silent")
    return document


def _section(document: dict, name: str) -> dict:
    value = document.get(name)
    if not isinstance(value, dict):
        raise MonitoringPolicyError(
            f"监控交互政策的 {name} 是一个对象，不是 {type(value).__name__}")
    return value


def mode_of(policy: dict) -> str:
    return (policy.get("notification") or {}).get("mode") or MODES[0]


def prompt_for(policy: dict, bucket: str) -> str:
    """What the transport may do about this bucket. Never more than declared."""
    prompt = (policy.get("notification") or {}).get("prompt") or {}
    value = prompt.get(bucket)
    if value not in PROMPTS:
        raise MonitoringPolicyError(
            f"{bucket!r} 不在 notification.prompt 里，因此这一条提醒能请求什么"
            f"没有答案。默认成 none 会把一条需要人的提醒静默降级；默认成 "
            f"urgent_app 会让一个没被想过的桶去 buzz 手机")
    return value


def fault_notice(policy: dict) -> dict:
    """The delivery-failure message's bucket, and what it may ask for.

    The one message that is not about a card's contents but about the card not
    arriving. `card fault` used to spell both words itself — `"ACTION_REQUIRED"
    if due else "ROUTINE"` and a hard-coded `"none"` — and a bucket named inside
    a sender is a bucket nothing else can be held to: the policy above could be
    read, edited and validated without this path ever being consulted, and the
    two answers to «may this buzz» had no reason to stay one answer.

    `prompt_for` is deliberately NOT used here. It answers per bucket for a
    NOTICE, and this path's request is `none` for a reason that belongs to the
    path: `urgent_app` travels over the transport whose last status send is what
    the finding reports as failed, so the louder option is the one in doubt.
    That reasoning is written beside the declaration in the contract; this
    function only refuses to let it be missing.
    """
    fault = (policy.get("notification") or {}).get("fault")
    if not isinstance(fault, dict):
        raise MonitoringPolicyError(
            f"notification.fault 是一个对象，不是 {type(fault).__name__}。"
            f"投递失败那条消息的桶与提醒方式必须由政策声明：由发送方自己拼出来"
            f"的桶词，没有任何读者能查，也没有任何别的消费者受它约束")
    bucket = fault.get("bucket")
    prompt = fault.get("prompt")
    if bucket not in BUCKETS or prompt not in PROMPTS:
        raise MonitoringPolicyError(
            f"notification.fault 声明的是 bucket={bucket!r} prompt={prompt!r}，"
            f"而本模块认识的是 bucket ∈ {list(BUCKETS)} 且 prompt ∈ "
            f"{list(PROMPTS)}。投递失败是这一套里唯一真的会走到 ACTION_REQUIRED "
            f"的消息，它的桶词拼错会静默地把「没人在被通知」降成一条普通提醒")
    return {"bucket": bucket, "prompt": prompt}


def short_device(device_ref: str | None) -> str:
    """`device:tokyo-shared-dev-01` → `tokyo-shared-dev-01`.

    The notice's identity is read by a person in a receipt and typed into a
    dedupe key the service caps at 50 characters. The `device:` prefix is this
    tree's namespace and neither reader needs it; the full ref travels in the
    document beside the key, so nothing is lost by shortening it here.
    """
    text = str(device_ref or "").strip()
    if not text:
        raise MonitoringPolicyError(
            "一条提醒的身份里有设备名，而这次没给。没有设备名的身份会让两台机器"
            "的同一小时撞成同一条")
    return text.split(":", 1)[1] if text.startswith("device:") else text


def identity(device_ref: str | None, day_ref: str, hour: int) -> str:
    """`status-notice:<device>/<workday>/<hour>` — the same shape §11 uses for
    the day's card, one level finer.

    The hour is in the identity rather than the tick because the hour is what a
    person reads and what a re-run of the timer reproduces; a tick ordinal is
    this module's derivation and a second derivation of it must not be able to
    produce a second send.

    **It fits the transport, and it is built to, not trimmed to.** The key goes
    to `lark-cli --idempotency-key`, which caps at 50 characters, and the cap is
    applied by the transport as a silent truncation. A truncated key is not a
    shortened name for the same message — it is the name of a different one.
    The device segment is the only part that can grow (this host's real ref
    yields 59 characters), so when the whole does not fit, the DEVICE is
    replaced by a digest sized to the room that is left. `day_ref` and `hour`
    are never touched: they are what tells one hour from the next, and a key
    that lost the hour would make every hour of a workday collide into one —
    the service would take 15:00 for a repeat of 14:00 and deliver one notice a
    day while every layer reported success. That is the exact failure this
    function exists to make impossible.

    A digest rather than a truncation of the device, for the same reason one
    level down: `tokyo-shared-dev-01` and `tokyo-shared-dev-02` share their
    first nineteen characters, and a truncated name would make two machines'
    same hour into one notice. Nothing is lost to a reader — the full ref
    travels in the document beside the key.
    """
    if not isinstance(hour, int) or isinstance(hour, bool):
        raise MonitoringPolicyError(
            f"提醒的身份需要小时，拿到的是 {hour!r}。没有小时的键会让同一小时的"
            f"两次重跑各发一条")
    device = short_device(device_ref)
    tail = f"/{day_ref}/{hour:02d}"
    room = TRANSPORT_KEY_MAX - len("status-notice:") - len(tail)
    if room < 8:
        raise MonitoringPolicyError(
            f"这一天的身份装不进传输层的 {TRANSPORT_KEY_MAX} 字符："
            f"`status-notice:` 和 `{tail}` 就占掉了 "
            f"{TRANSPORT_KEY_MAX - room} 个，设备名只剩 {room} 个字符可用，"
            f"少于一个摘要的 8 个。截断会切掉小时，让同一工作日的每一小时共用"
            f"一个幂等键")
    if len(device) > room:
        digest = hashlib.sha256(device.encode("utf-8")).hexdigest()
        # 摘要占 8 个字符，其余留给可读的设备名前缀。前缀短于 4 个字符时不留 ——
        # `t-5ca34bbb` 里那个 `t` 认不出是哪台机器，而这 4 个字符换成摘要多出
        # 16 bit 的区分度。前缀够长时它有意义：人在回执里能认出设备名开头。
        device = (f"{device[:room - 9]}-{digest[:8]}" if room >= 13
                  else digest[:room])
    return f"status-notice:{device}{tail}"


def fault_key(device_ref: str | None, at: str) -> str:
    """`delivery-fault:<device>/<instant>` — one key per FAILED attempt.

    The instant is the failed receipt's own `at`, and it is the right identity
    for a reason that is easy to get backwards: this message is about ONE failed
    send and not about the hour it happened in. Keying it by the hour would make
    the second failure of one hour look like a re-run of the first — the
    transport would drop a real second failure as a duplicate — while a key
    built from the attempt itself lets a genuinely new failure be a genuinely
    new message and makes a re-run of the SAME attempt a duplicate, which is
    what an idempotency key is for.

    Unlike `identity`, nothing here is derived from a calendar: there is no hour
    to name and no workday to be inside. A failure is a fact of the transport
    and happens whenever it happens.

    The device segment is compressed and the INSTANT is not, which is the same
    rule `identity` follows for `day_ref` and `hour` and it is not a
    preference: the instant is what tells one failed attempt from the next, and
    a key that lost it would make two failures in one hour share a name — the
    transport would drop the second one as a re-run of the first. The measured
    need is real rather than theoretical: this host's own ref and a verbatim
    ISO-8601 instant come to 60 characters against the transport's 50, so the
    first version of this function refused on the node it was written for.
    """
    instant = str(at or "").strip()
    if not instant:
        raise MonitoringPolicyError(
            "这条投递失败要一个身份，而这一拍没给出失败发生在哪一刻。没有时刻"
            "的键会让同一小时里的第二次失败被当成第一次的重跑，而它是一条新的"
            "失败")
    device = short_device(device_ref)
    prefix, tail = "delivery-fault:", f"/{instant}"
    room = TRANSPORT_KEY_MAX - len(prefix) - len(tail)
    if room < 8:
        raise MonitoringPolicyError(
            f"这一刻太长，装不进传输层的 {TRANSPORT_KEY_MAX} 字符：`{prefix}` 和 "
            f"`{tail}` 就占掉了 {TRANSPORT_KEY_MAX - room} 个，设备名只剩 {room} "
            f"个，少于一个摘要的 8 个。切掉时刻会让同一小时里的两次失败撞成一"
            f"个，所以这里不给键，而不是给一个会吞掉第二次失败的键")
    if len(device) > room:
        digest = hashlib.sha256(device.encode("utf-8")).hexdigest()
        # Same shape and the same 8-character minimum as `identity`: a device
        # prefix shorter than four characters names nothing a person could
        # recognise, and those characters buy 16 more bits of separation.
        device = (f"{device[:room - 9]}-{digest[:8]}" if room >= 13
                  else digest[:room])
    return f"{prefix}{device}{tail}"


def tick_key(device_ref: str | None, schedule: dict) -> str | None:
    """`identity` for a tick, or `None` when the calendar left no tick to name.

    Both the decision and the ledger lookup need this key, and the lookup has to
    come FIRST — `notice` is told what the ledger already holds, so the value it
    passes through is a key somebody else had to compose. This is that one
    composition, called from both sides, rather than a second copy in the caller
    that would agree with this one until the day one of them changed.

    `None` is an answer and not a failure: `routine_window` sets `hour` only
    after it knows which day this is, so a non-workday and a calendar that has
    not decided both arrive here without one — and a key made of whatever was at
    hand would dedupe two different hours into one. A missing DEVICE is a
    different thing and stays loud: `identity` raises for it, because there is
    no honest key at all without one, and two machines' same hour would collide.
    """
    sched = schedule or {}
    day_ref = sched.get("day_ref")
    hour = sched.get("hour")
    if not day_ref or not isinstance(hour, int) or isinstance(hour, bool):
        return None
    return identity(device_ref, day_ref, hour)


def state_of(row: dict) -> tuple:
    """What a row SAYS, as one comparable value.

    The reading and not the numbers: `verdict`, the three section states and how
    many gaps the collection had. Two hours that differ only in a CPU percentage
    are the same reading — that is the whole reason `changes-only` is worth
    having as a mode at all, and it is why the numbers never reach here: a row
    has no room for them (`card_day.ROW_FIELDS` is the reason, and
    `check_row` refuses a field it does not know).

    `observation` is deliberately NOT in the tuple even though it is in the row.
    It is prose, and it moves for reasons that are not a change of state — a
    formatter edit, a rounded second, a re-worded sentence — so including it
    would make `changes-only` fire on exactly the hours when nothing happened,
    which is the failure mode that mode exists to avoid.
    """
    row = row or {}
    sections = row.get("sections") or {}
    return (row.get("verdict"), tuple(sorted(
        (name, sections.get(name)) for name in ("mine", "host", "gateway"))),
        row.get("gaps"))


def bucket_of(verdict: str | None) -> str:
    """The attention bucket a row's verdict puts the notice in.

    An unrecognised verdict is ATTENTION, never ROUTINE. Same rule
    `presentation.env_health` applies to a section it could not read: the
    absence of a fault signal is not the presence of a good one, and a word
    added later must not be able to make the node quieter than it was.
    """
    word = str(verdict or "")
    if word == "OK":
        return "ROUTINE"
    return "ATTENTION"


def notice(*, policy: dict, schedule: dict, latest: dict | None,
           previous: dict | None, card_version: str, device_ref: str,
           at: str, created: bool, already_delivered: bool = False,
           status_receipt: dict | None = None) -> dict:
    """Is a Status Update Notice due at this tick, and what may it ask for?

    Every input is handed in: this function reads no clock, no store and no
    ledger, because each of those lives somewhere that already answers for it
    and a second reader would be a second answer. `created` is whether THIS tick
    is the one that built the day's card, `already_delivered` is what the
    delivery ledger says about `identity()` below, and `status_receipt` is that
    same ledger's newest `status` row — what became of the CARD itself, which
    is a different question from what became of this hour's notice.

    The order of the refusals is the order a reader would fix them, and the
    refusal that matters most is the first: a notice that cannot name the card
    version it is about is refused outright rather than composed, because the
    one thing that separates a notice from a second status card is that it
    points at one.

    THE ORDER THAT IS NOT ONLY A SENTENCE
    -------------------------------------
    §7 requires «PATCH DELIVERED, and only then this notice» to be ENFORCED and
    not merely described, and the enforcement is here. The claim at stake is the
    notice's own line — «当天卡已更新 · <version>» — and when the newest `status`
    receipt is a send that did not arrive, or one that carried a different
    version, that sentence is false about the card the reader will open. So the
    tick is answered «not due» and the reason says which half failed. The day's
    card is the thing being pointed at; a notice is safe only because a person
    can follow the pointer.

    `status_receipt` defaults to `None`, which is «the ledger holds no status
    receipt at all», and that refuses too. A default of «assume it arrived»
    would make the enforcement something a caller opts into by remembering an
    argument, and the caller that forgets is the one whose patch just failed.
    What this cannot do is stop a caller that never asks: an answer of
    `due: false` is a refusal to state, not a lock on the transport. A caller
    that sends anyway has not been misled by this module — it has not read it.

    NOTE WHAT THE READING IS HERE
    -----------------------------
    `latest` is passed through into the answer field for field, and the notice
    QUOTES it rather than producing it. That is the difference between a notice
    and a card, and it is not a stylistic one: the row goes out beside the card
    version it came from, so every word the notice says is checkable against the
    card a reader can open. A notice that computed a verdict of its own would be
    a second place the state is decided, and the two would disagree on the hour
    that mattered.
    """
    version = str(card_version or "").strip()
    if not version:
        raise MonitoringPolicyError(
            "一条提醒必须指名它所讲的那一版卡片（`card status --with-frozen` "
            "给出的 `version`），而这次没有。没有版本的提醒就是第二张状态卡："
            "它说的话没有任何东西可以核对，读者也就无法知道它讲的是不是卡上"
            "那一版")
    mode = mode_of(policy)
    sched = schedule or {}
    day_ref = sched.get("day_ref")
    hour = sched.get("hour")
    quoted = latest or {}
    # The card's own send, reduced to the pair this decision is checked against.
    # `state` and `version` are read off the row rather than summarised into a
    # boolean by the caller, because a refusal has to be able to SAY what it
    # found: «nothing arrived» and «a different version arrived» send a reader
    # to two different places, and an answer that could only report «no» would
    # leave them guessing which.
    reached = status_receipt or {}
    state = str(reached.get("state") or "").strip()
    arrived = str(reached.get("version") or "").strip()
    base = {
        "schema": NOTICE_SCHEMA,
        "kind": NOTICE_KIND,
        "at": at,
        "day_ref": day_ref,
        "hour": hour,
        "hhmm": quoted.get("hhmm"),
        "tick": sched.get("tick"),
        # The denominator, which is the calendar's and not this module's — §10's
        # window decides it and `routine_window` computes it. Carried so that
        # «7/13» on the face comes from one place rather than from a reader
        # counting the hours of a window they had to be told about.
        "ticks_per_workday": sched.get("ticks_per_workday"),
        "policy_mode": mode,
        "card_version": version,
        "device_ref": device_ref,
        # The row, quoted and not re-derived. `sections` is the same three
        # states the card's blocks show and `gaps` is the same count of them, so
        # a reader who follows the version to the card finds the same numbers.
        "verdict": quoted.get("verdict"),
        "observation": quoted.get("observation"),
        "sections": quoted.get("sections"),
        "gaps": quoted.get("gaps"),
        # What the card this notice points at did on the way to the reader.
        # In every answer, including the refusals, and not only in the ones that
        # fail: a notice that agreed to be sent carries the pair it was cleared
        # against, so «this message was allowed because a card with this version
        # arrived at this time» is readable rather than asserted.
        "status_receipt": ({"state": state or None, "version": arrived or None,
                            "at": reached.get("at")} if reached else None),
    }

    def answer(due: bool, reason: str, *, bucket: str | None = None) -> dict:
        doc = dict(base)
        doc["due"] = due
        doc["reason"] = reason
        doc["says"] = SAYS.get(reason) or ""
        key = tick_key(device_ref, sched)
        if key is None:
            # No identity without a day and an hour. Reported rather than
            # derived: a key made of whatever was at hand would dedupe two
            # different hours into one, and the second hour would go unsent
            # while the ledger said it had been told. This is the state the
            # calendar leaves a non-workday and an unknown calendar in —
            # `routine_window` sets `hour` only after it knows the day, so the
            # absence of an hour here is the calendar's answer, not a gap. The
            # null key is what says so; `says` already carries the reason and
            # does not repeat it.
            doc["idempotency_key"] = None
            doc["bucket"] = None
            doc["prompt"] = "none"
            return doc
        doc["idempotency_key"] = key
        bucket = bucket or bucket_of((latest or {}).get("verdict"))
        doc["bucket"] = bucket
        doc["prompt"] = prompt_for(policy, bucket)
        return doc

    # The calendar's own words, compared verbatim rather than paraphrased.
    # `routine_window` reports `reason: None` when it never got far enough to
    # say which kind of day this is — that is «I do not know», and it is grouped
    # with `CALENDAR_UNKNOWN` rather than with the two window answers: a node
    # that does not know what day it is has not decided the notice is not due.
    weekday = sched.get("reason")
    if weekday is None or weekday == "CALENDAR_UNKNOWN":
        return answer(False, CALENDAR_UNKNOWN)
    if weekday == "NON_WORKING_DAY":
        return answer(False, NOT_A_WORKDAY)
    if not sched.get("in_window"):
        return answer(False, OUTSIDE_WINDOW)

    # §7's order, enforced. Asked AFTER the calendar and BEFORE everything else,
    # and both halves of that placement are the point.
    #
    # After the calendar, because a rest day and a calendar that has not decided
    # are ticks at which no status card is sent at all — `routine_window` sets
    # no `tick` on them — and asking about a delivery nobody attempted would
    # report a fault on every rest day. The question «did the card arrive» is
    # only well-formed for a tick that has a card.
    #
    # Before `created`, because a notice that would be «not due» anyway is still
    # the place a reader looks when the hour is silent: on the tick that builds
    # the day's card, a send that FAILED leaves no message in the chat and no
    # notice either, and «the card itself is the new message» would describe a
    # message that does not exist as the reason nothing else was said.
    if state != DELIVERED:
        return answer(False, CARD_NOT_DELIVERED)
    if arrived != version:
        # A card did arrive; it was not this one. Reachable in the ordinary run
        # — a tick that patched nothing and recorded no receipt leaves the
        # previous hour's version as the newest row — and in the hand-run where
        # somebody passes a version from the card they happen to have open.
        return answer(False, CARD_VERSION_NOT_DELIVERED)

    if created:
        # The day's first tick. The card message itself arrives new, so the
        # notification a person would get is already there; a notice here would
        # make 08:00 the one hour with two messages about it.
        return answer(False, ON_CREATE_ITSELF)
    if already_delivered:
        # Per-tick idempotency, and it is the ledger that answers it rather than
        # a hope that the service dedupes. A timer that fires twice at 14:00
        # must produce one message; `card notice` saying `due: true` twice and
        # trusting the far side to collapse them is a duplicate waiting for the
        # day the far side changes.
        return answer(False, ALREADY_DELIVERED)

    changed = previous is None or state_of(previous) != state_of(latest)
    missing_previous = previous is None

    if mode == "always":
        return answer(True, MODE_ALWAYS)
    if mode == "changes-only":
        if missing_previous:
            # There is no previous reading to compare against. Reporting «no
            # change» would be a claim about a comparison nobody made; the
            # honest answer is that the state moved into view and the reader
            # has not been told about it.
            return answer(True, NO_PREVIOUS_ROW)
        return answer(True, CHANGED) if changed else answer(False, UNCHANGED)
    # attention-only
    bucket = bucket_of((latest or {}).get("verdict"))
    return (answer(True, ATTENTION_PRESENT) if bucket != "ROUTINE"
            else answer(False, ATTENTION_ABSENT))


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - hand check
    """Read the policy. A diagnostic, not the product surface."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--policy", default="", help="override the contract path")
    args = parser.parse_args(argv)
    json.dump(load(args.policy or None), sys.stdout, ensure_ascii=False,
              indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - hand check
    import sys
    raise SystemExit(main())
