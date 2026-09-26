#!/usr/bin/env python3
"""Which days this instance broadcasts on, and in which hours.

WHY THIS EXISTS, AND WHY IT IS NOT A CALENDAR IN CODE
-----------------------------------------------------
`docs/MONITORING-SCOPE-RECOVERY-CARDS.md` §9–§11 fixes the daily status card's
rhythm in three sentences that this module exists to make answerable:

  §9  — «当前个人实例使用 Asia/Shanghai / 中国大陆工作日历。日历不能简单按周一
        到周五推导。年度 authority 使用国务院办公厅公布的节假日/调休通知，形成
        versioned BusinessCalendar projection.»
  §10 — «常规资源/状态播报只在 WORKDAY 的 08:00–20:00（含两端）更新…即每个工作日
        最多 13 次 routine update.» and «20:00 之后、08:00 之前、NON_WORKING_DAY
        都不做常规 Broadcast.»
  §11 — «每个 WORKDAY 在首个允许的播报时点创建一张 Daily Status Card… 次一 WORKDAY
        创建新卡。NON_WORKING_DAY 不创建空白 Status Card.»

The first sentence is the one that decides this module's shape. A calendar that
this file *contained* would be a calendar that says Mon–Fri is a workday
whenever nobody updated it, and the design names that failure in its own words:
«calendar 未知不冒充 Mon–Fri 官方工作日» (§15, line 135). So the year's days are
NOT here. They are an instance DECLARATION — a projection of a published
notice, carrying the notice's locator and date — and this module is the reader
of that declaration plus the three answers the two consumers actually ask:

    WORKDAY / NON_WORKING_DAY / UNKNOWN     what kind of day is this instant in
    is this instant inside the window       may a routine update happen now
    which of the 13 ticks is it             the ordinal a card's history needs

THE THREE ANSWERS ARE THREE AND NOT ONE BOOLEAN, because the design keeps
UNKNOWN beside the other two and gives it a different consequence: an unknown
calendar does **not** stop observation and does **not** fabricate a workday, and
the default is «保留观察与配置缺口，不静默新增 routine 群发». An instance may
declare an explicit fallback (§9: «使用显式 instance fallback») and only then is
one applied — and when it is, the answer says so, because a fallback workday and
an official workday are different facts about the same date.

WHAT THIS MODULE DOES NOT DECIDE
--------------------------------
It does not create, patch or send a card. §11's create-vs-patch question is
«has the day's card been sent yet», which is a receipt question and belongs to
`card_delivery`; this module answers only «may a routine update happen at this
instant, and under which day's identity». It also does not decide the incident
quiet hours of §12 — those are P1/P2/P3 policy over a different card — and it
holds no clock: every function takes the instant it is asked about.

AND IT DOES NOT READ A PATH IT WAS NOT GIVEN. `read_declaration` names one
default location because that is where an instance declaration belongs, and
`None` from it is a first-class answer — the same rule `card_source.load_binding`
follows for the service binding, and for the same reason: every available
fallback (the host's own timezone, `/etc/localtime`, "probably Shanghai") is a
guess that renders as a fact.
"""
from __future__ import annotations

import datetime
from pathlib import Path

SCHEMA = "serveros/business-calendar.v1"

#: Where an instance's calendar declaration belongs. `~/.serveros/` is the same
#: directory `card_source.DEFAULT_BINDING_PATH` reads the service binding from:
#: one place for what this node declares about itself, one reader each.
DEFAULT_PATH = Path.home() / ".serveros" / "business-calendar.yaml"

#: The three kinds of day, and the third is not a failure.
#:
#: `UNKNOWN` is «this node cannot say», and §9 defines it as «当前年份官方日历
#: 尚未导入或失效» — a statement about the declaration rather than about the day.
#: It is kept apart from `NON_WORKING_DAY` for the reason §15 gives: a node that
#: read a missing calendar as «a rest day» would go quiet on a real workday and
#: never say why, and a node that read it as «a workday» would broadcast on
#: statutory holidays. Both are the same mistake — answering from the shape of
#: the absence — and the vocabulary exists so neither can be expressed.
DAY_KINDS = ("WORKDAY", "NON_WORKING_DAY", "UNKNOWN")

#: How a day's kind was arrived at, kept beside the kind.
#:
#: `declared-workday` and `declared-rest` are the official notice's own entries
#: and they OVERRIDE the weekday/weekend derivation §9 spells out. `weekday` is
#: that derivation, applied only inside a year the declaration covers — the
#: design's «普通周一至周五» being a workday is a statement about a covered year,
#: not about Monday. `weekday-fallback` is the instance's own decision, taken
#: when the year is not covered, and it is a different word for the same answer
#: on purpose: a reader must be able to tell an official workday from one this
#: node chose to treat as one.
DAY_AUTHORITIES = ("declared-workday", "declared-rest", "weekday",
                   "weekday-fallback")

#: The routine broadcast window, inclusive at both ends. §10: «08:00–20:00
#: （含两端）», «20:00 之后、08:00 之前…都不做常规 Broadcast».
WINDOW_FIRST_HOUR = 8
WINDOW_LAST_HOUR = 20

#: §10's ceiling, and it is a consequence of the window rather than a second
#: rule: 08:00, 09:00 … 20:00 is thirteen instants. It is published as a number
#: because the acceptance criterion counts it («同一工作日最多13次更新仍只有一张
#: Status Card»), and a count derived in two places is a count that will differ.
ROUTINE_UPDATES_PER_WORKDAY = WINDOW_LAST_HOUR - WINDOW_FIRST_HOUR + 1

#: The reasons a routine update does or does not happen. Closed for the reason
#: every closed list here is: a caller branching on prose is a caller that will
#: branch on a sentence somebody reworded.
WINDOW_REASONS = ("IN_WINDOW", "BEFORE_WINDOW", "AFTER_WINDOW",
                  "NON_WORKING_DAY", "CALENDAR_UNKNOWN")

#: The one fallback a declaration may name. `weekday` is the design's own
#: example («calendar 未知不冒充 Mon–Fri 官方工作日；只有明确 fallback 才执行该
#: fallback»), and it is the ONLY one: a fallback list short enough to type is a
#: fallback list somebody will type, and the point of naming it here is that no
#: other value can be reached by accident.
FALLBACK_RULES = ("weekday",)

#: What a declaration must carry before anything in it is believed. §9:
#: «年度 calendar declaration 至少保存 jurisdiction=CN-Mainland、timezone、
#: source URL/document id、source revision/published date、workdays/non-working-
#: days、imported_at/verified_at». Each key below is one of those, and the
#: refusal for a missing one says which sentence requires it — a declaration
#: without a source is a calendar somebody remembers, and the whole reason the
#: days are not in this file is that «somebody remembers» is not an authority.
REQUIRED_KEYS = ("schema", "jurisdiction", "timezone", "source", "years",
                 "imported_at", "verified_at")


class CalendarError(RuntimeError):
    """A declaration that says something this module will not read as a day."""


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

def _date(value, *, field: str, year: int) -> datetime.date:
    """One `YYYY-MM-DD`, or a refusal naming what was written instead."""
    if not isinstance(value, str):
        raise CalendarError(
            f"{field} holds {value!r}, and a day in a calendar is written as a "
            f"string `YYYY-MM-DD` — {type(value).__name__} has no date in it")
    try:
        day = datetime.date.fromisoformat(value.strip())
    except ValueError as exc:
        raise CalendarError(
            f"{field} holds {value!r}, which is not a date: {exc}. The window "
            f"this calendar is read for is a day, and a day that cannot be "
            f"parsed is a day the reader would have to skip silently") from exc
    if day.year != year:
        raise CalendarError(
            f"{field} holds {value!r} under the year {year}, and that date is "
            f"in {day.year}. A nested year that disagrees with its own dates is "
            f"a declaration whose keys and values were written at different "
            f"times, and only one of them can be right")
    return day


def declaration(document) -> dict:
    """One calendar declaration, validated, as this module will read it.

    Raises `CalendarError` for every refusal, and each message says what the
    caller would otherwise have recorded. The declaration is an instance
    artifact — a projection of the published notice — and this function is the
    only place that decides whether a projection is one that may be read.
    """
    if not isinstance(document, dict):
        raise CalendarError(
            f"a calendar declaration is a mapping and this is "
            f"{type(document).__name__}. There is no tolerant reading: a "
            f"declaration that half-parses would be indistinguishable from a "
            f"complete one at every later step, and the days it lost would read "
            f"as days nobody declared")
    missing = [key for key in REQUIRED_KEYS if document.get(key) in (None, "", [], {})]
    if missing:
        raise CalendarError(
            f"the declaration is missing {', '.join(missing)}. §9 of "
            f"MONITORING-SCOPE-RECOVERY-CARDS names what a year's declaration "
            f"must carry — jurisdiction, timezone, the source locator and its "
            f"revision or published date, the days, and when it was imported and "
            f"verified — and a declaration without them is a calendar somebody "
            f"remembers rather than one a notice states")
    if document.get("schema") != SCHEMA:
        raise CalendarError(
            f"the declaration says schema {document.get('schema')!r} and this "
            f"reader understands {SCHEMA!r}. A declaration written for another "
            f"schema is not read leniently here: the fields this reader needs "
            f"would have to be guessed from the ones it happens to find")

    jurisdiction = str(document["jurisdiction"]).strip()
    zone_name = str(document["timezone"]).strip()

    # The zone is checked against the system's own database rather than against
    # a name this file likes: a declaration naming a zone the node cannot
    # resolve would otherwise answer every later question from UTC — the host's
    # zone arriving through the back door as an inferred fact.
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CalendarError(
            f"the declaration names timezone {zone_name!r}, which this node "
            f"cannot resolve ({type(exc).__name__}: {exc}). The window is "
            f"08:00–20:00 in the CALENDAR's zone and not in the reader's: "
            f"reading it in any other zone moves the window by whole hours and "
            f"nobody would see that from the card") from exc

    source = document["source"]
    if not isinstance(source, dict):
        raise CalendarError(
            f"`source` is {type(source).__name__} and a source is the mapping "
            f"that says which notice this projection came from. §9 asks for a "
            f"locator (URL or document id) and its revision or published date; "
            f"a bare string here would drop one of the two without saying so")
    locator = str(source.get("url") or source.get("document_id") or "").strip()
    if not locator:
        raise CalendarError(
            "`source` names neither a `url` nor a `document_id`, so nothing "
            "says which notice these days came from. The declaration is a "
            "projection OF something, and a projection whose original cannot be "
            "found again cannot be re-verified when the next year's notice "
            "arrives")
    stamp = str(source.get("published_at") or source.get("revision") or "").strip()
    if not stamp:
        raise CalendarError(
            "`source` carries neither `published_at` nor `revision`. A notice "
            "is revised — 调休 arrangements have been changed after publication "
            "— so «which version of the notice» is part of the citation and not "
            "decoration")

    years_raw = document["years"]
    if not isinstance(years_raw, dict) or not years_raw:
        raise CalendarError(
            "`years` is where the days live and it is empty or not a mapping. "
            "§9's UNKNOWN is «当前年份官方日历尚未导入»: a declaration covering "
            "no year answers UNKNOWN for every day, so an empty one here would "
            "be a declaration that can never be wrong")
    years: dict[int, dict] = {}
    for key, entry in years_raw.items():
        try:
            year = int(key)
        except (TypeError, ValueError) as exc:
            raise CalendarError(
                f"`years` has the key {key!r}, which is not a year. The year is "
                f"what decides coverage — «当前年份» in §9 — and a key that "
                f"cannot be compared as one makes coverage unanswerable") from exc
        if not isinstance(entry, dict):
            raise CalendarError(
                f"`years[{year}]` is {type(entry).__name__} and a year holds the "
                f"two lists §9 names: the declared workdays and the declared "
                f"non-working days")
        work = [_date(v, field=f"years[{year}].workdays", year=year)
                for v in (entry.get("workdays") or [])]
        rest = [_date(v, field=f"years[{year}].non_working_days", year=year)
                for v in (entry.get("non_working_days") or [])]
        both = sorted(set(work) & set(rest))
        if both:
            raise CalendarError(
                f"years[{year}] declares {', '.join(d.isoformat() for d in both)} "
                f"as BOTH a workday and a non-working day. The notice cannot say "
                f"both, so this is a projection defect — a day merged from two "
                f"revisions of the notice — and picking one of the two here "
                f"would hide it behind a coin toss")
        years[year] = {"workdays": sorted(set(work)),
                       "non_working_days": sorted(set(rest))}

    fallback = document.get("fallback")
    if fallback:
        if not isinstance(fallback, dict):
            raise CalendarError(
                f"`fallback` is {type(fallback).__name__}. §9 allows an "
                f"EXPLICIT instance fallback and nothing else: a fallback that "
                f"can be inferred is a fallback nobody decided, and the design "
                f"forbids deriving one from Mon–Fri")
        rule = str(fallback.get("rule") or "").strip()
        if rule not in FALLBACK_RULES:
            raise CalendarError(
                f"fallback rule {rule or 'is absent'} is not one of "
                f"{list(FALLBACK_RULES)}. This is the whole list of fallbacks "
                f"this reader applies, so a rule outside it would be accepted "
                f"here and silently ignored later")
        if not str(fallback.get("declared_by") or "").strip():
            raise CalendarError(
                "the fallback names no `declared_by`. A fallback decides that "
                "days the notice has not covered are treated as workdays, and "
                "that decision has an author — the record of it is the only "
                "thing that separates «the instance falls back to weekday» from "
                "«a reader invented Mon–Fri»")

    return {
        "schema": SCHEMA,
        "jurisdiction": jurisdiction,
        "timezone": zone_name,
        "zone": zone,
        "source": dict(source),
        "years": years,
        "fallback": ({"rule": fallback["rule"],
                      "declared_by": str(fallback["declared_by"]).strip()}
                     if fallback else None),
        "imported_at": str(document["imported_at"]),
        "verified_at": str(document["verified_at"]),
    }


def read_declaration(path=None) -> dict:
    """The instance's declaration as a READING: state, value and reason.

    Three states rather than a value-or-None, because §9's UNKNOWN has two
    causes that are repaired differently: a year that was never imported
    (`ABSENT`) and one whose projection does not validate (`INVALID`). Both are
    UNKNOWN about the day, and the person asked to fix one needs to know which
    they have. Nothing here raises: a missing or broken declaration is an
    ordinary answer about this node, not an exception in the reader.
    """
    target = Path(path) if path else DEFAULT_PATH
    if not target.is_file():
        return {"state": "ABSENT", "declaration": None, "path": str(target),
                "reason": (f"{target} 不存在，本年度的官方工作日历尚未导入"),
                "detail": ""}
    try:
        import yaml
        document = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - an unreadable declaration is a reading
        return {"state": "INVALID", "declaration": None, "path": str(target),
                "reason": f"{target} 存在但无法解析，本年度工作日历不可用",
                "detail": f"{type(exc).__name__}: {exc}"}
    try:
        return {"state": "READ", "declaration": declaration(document),
                "path": str(target), "reason": "", "detail": ""}
    except CalendarError as exc:
        return {"state": "INVALID", "declaration": None, "path": str(target),
                "reason": (f"{target} 存在但不是一份可读的日历声明，"
                           f"本年度工作日历不可用"),
                "detail": str(exc)}


# ---------------------------------------------------------------------------
# The two questions
# ---------------------------------------------------------------------------

def _instant(value) -> datetime.datetime | None:
    """One ISO instant, or None. Same tolerance as `continuation._instant`."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment


def _weekly(day: datetime.date) -> str:
    """Mon–Fri is the derivation, and it is only ever applied inside a covered
    year — §9's «普通周一至周五» is a statement about the notice's years."""
    return "WORKDAY" if day.weekday() < 5 else "NON_WORKING_DAY"


def day(instant, *, declaration: dict | None = None,
        timezone: str | None = None) -> dict:
    """What kind of day this instant falls in, and on whose authority.

    The order is the design's: an official entry overrides the weekday/weekend
    derivation (§9: «官方调休日优先覆盖 weekday/weekend 推导»), the derivation is
    applied only inside a year the declaration covers, and outside that the
    answer is UNKNOWN — or the instance's declared fallback, said out loud as
    one.

    `timezone` is only consulted when there is no declaration, and it is never
    taken from the host: naming the day needs a zone, and the zone the window is
    read in is the calendar's, not the reader's.
    """
    moment = _instant(instant)
    if moment is None:
        return {"date": None, "day_ref": None, "kind": "UNKNOWN",
                "authority": None, "jurisdiction": None, "timezone": None,
                "says": (f"无法把 {instant!r} 读成一个时刻，因此不判断这是哪一种日子")}

    zone_name = declaration["timezone"] if declaration else (
        str(timezone).strip() if timezone else None)
    if zone_name:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            local = moment.astimezone(ZoneInfo(zone_name))
        except (ZoneInfoNotFoundError, ValueError):
            return {"date": None, "day_ref": None, "kind": "UNKNOWN",
                    "authority": None, "jurisdiction": None,
                    "timezone": str(zone_name),
                    "says": (f"声明的时区 {zone_name} 在本机无法解析，"
                             f"因此不判断这是哪一种日子")}
    else:
        return {"date": None, "day_ref": None, "kind": "UNKNOWN",
                "authority": None, "jurisdiction": None, "timezone": None,
                "says": ("没有日历声明，也没有被指定的时区，因此不判断这是哪一种"
                         "日子——本机不拿自己的时区替它作答")}

    date = local.date()
    iso = date.isoformat()
    jurisdiction = declaration["jurisdiction"] if declaration else None
    day_ref = f"{jurisdiction}/{iso}" if jurisdiction else iso
    entry = (declaration or {}).get("years", {}).get(date.year)

    def answer(kind, authority, says):
        return {"date": iso, "day_ref": day_ref, "kind": kind,
                "authority": authority, "jurisdiction": jurisdiction,
                "timezone": zone_name, "local": local.isoformat(),
                "weekday": date.weekday(), "says": says}

    if entry:
        if date in entry["workdays"]:
            return answer("WORKDAY", "declared-workday",
                          f"{iso} 是官方日历明确的调休上班日（{jurisdiction}），"
                          f"按工作日处理")
        if date in entry["non_working_days"]:
            return answer("NON_WORKING_DAY", "declared-rest",
                          f"{iso} 是官方日历明确的休息日（{jurisdiction}）")
        derived = _weekly(date)
        if derived == "WORKDAY":
            says = (f"{iso} 是工作日（{jurisdiction} 官方日历覆盖 {date.year} 年，"
                    f"该日既不在休息日也不在调休上班名单里，按普通周一至周五推导）")
        else:
            says = (f"{iso} 是休息日（{jurisdiction} 官方日历覆盖 {date.year} 年，"
                    f"该日不在调休上班名单里，按普通周末推导）")
        return answer(derived, "weekday", says)

    fallback = (declaration or {}).get("fallback")
    if fallback and fallback["rule"] == "weekday":
        kind = _weekly(date)
        return answer(kind, "weekday-fallback",
                      f"calendar=UNKNOWN：{jurisdiction or '本实例'} 的官方日历没有 "
                      f"覆盖 {date.year} 年。实例已显式声明 fallback=weekday"
                      f"（由 {fallback['declared_by']} 声明），因此 {iso} 按"
                      f"{'工作日' if kind == 'WORKDAY' else '休息日'}处理——"
                      f"这不是官方工作日")

    return answer("UNKNOWN", None,
                  f"calendar=UNKNOWN：{'官方日历没有覆盖 ' + str(date.year) + ' 年' if declaration else '本机没有读过任何日历声明'}"
                  f"，因此不判断 {iso} 是哪一种日子，也不按周一至周五推算")


def routine_window(instant, *, reading: dict | None = None,
                   timezone: str | None = None) -> dict:
    """Whether a routine broadcast may happen at this instant, and under which
    day's identity.

    `reading` is `read_declaration`'s answer — the state, not just the value —
    because §15's default is the point: with no calendar the node keeps
    observing and DOES NOT start a routine group send, and the reason a person
    is shown has to distinguish «not imported» from «does not validate».

    The window is inclusive at both ends (§10: «含两端»), so 08:00:00 and
    20:00:00 are inside it and 20:00:01 is not. `tick` is the ordinal of the
    hour within the day's thirteen, and it is reported rather than required:
    §10 recommends the update happen on the hour, and a recommendation is not a
    rule — a tick a minute late is still the same tick.
    """
    reading = reading or {"state": "ABSENT", "declaration": None,
                          "reason": "没有读过日历声明", "detail": "", "path": ""}
    decl = reading.get("declaration")
    facts = day(instant, declaration=decl, timezone=timezone)
    zone_name = facts.get("timezone")

    base = {k: facts.get(k) for k in
            ("date", "day_ref", "kind", "authority", "jurisdiction",
             "timezone")}
    base.update({
        "calendar": reading.get("state"),
        "calendar_reason": reading.get("reason") or "",
        "window": [WINDOW_FIRST_HOUR, WINDOW_LAST_HOUR],
        "ticks_per_workday": ROUTINE_UPDATES_PER_WORKDAY,
        "hour": None, "minute": None, "in_window": None, "on_the_hour": None,
        "tick": None, "reason": None, "route": "NO_ROUTINE_BROADCAST",
    })
    # `day` already resolved the instant into the calendar's zone and kept the
    # offset in `local`; the hour used below is therefore the calendar's hour,
    # and no second zone lookup happens here that could disagree with the first.
    base["local"] = facts.get("local")
    local = _instant(base["local"]) if base.get("local") else None

    if facts["kind"] == "UNKNOWN":
        base["reason"] = "CALENDAR_UNKNOWN"
        # The token is put on the face HERE rather than left to whichever
        # branch of `day` produced the sentence. It has to be there on every
        # unknown answer — the acceptance criterion reads the Diagnostic for
        # `calendar=UNKNOWN` — and only some of those branches say it: a node
        # with no declaration and no named zone gets a sentence about zones,
        # which is true and silent about the calendar. One place guarantees it;
        # three would guarantee it twice and forget it once.
        says = facts["says"]
        if "calendar=UNKNOWN" not in says:
            says = f"calendar=UNKNOWN：{says}"
        base["says"] = (
            f"{says}。默认不新增例行播报，观察与记录照常"
            + (f"（{reading.get('reason')}）" if reading.get("reason") else "")
            + "；事故卡另按 P1/P2 策略")
        return base

    if facts["kind"] == "NON_WORKING_DAY":
        base["reason"] = "NON_WORKING_DAY"
        base["in_window"] = False
        base["route"] = "NO_ROUTINE_BROADCAST"
        base["says"] = (f"{facts['date']} 是休息日，不创建例行状态卡，也不补发"
                        f"；事故卡另按 P1/P2 策略")
        return base

    if local is None:
        base["reason"] = "CALENDAR_UNKNOWN"
        base["says"] = f"无法把 {instant!r} 读成 {zone_name} 的时刻"
        return base

    hour, minute, second = local.hour, local.minute, local.second
    base.update({"hour": hour, "minute": minute,
                 "on_the_hour": minute == 0 and second == 0})
    first, last = WINDOW_FIRST_HOUR, WINDOW_LAST_HOUR
    morning = local.replace(hour=first, minute=0, second=0, microsecond=0)
    evening = local.replace(hour=last, minute=0, second=0, microsecond=0)
    where = f"{local.strftime('%H:%M')}（{zone_name}）"

    if local < morning:
        base.update({"reason": "BEFORE_WINDOW", "in_window": False})
        base["says"] = (f"{facts['date']} 是工作日，但现在是 {where}，早于常规播报"
                        f"窗口 {first:02d}:00–{last:02d}:00，不新增。错过 {first:02d}:00 "
                        f"不追发，进入窗口后的第一个时点建当天卡")
    elif local > evening:
        base.update({"reason": "AFTER_WINDOW", "in_window": False})
        base["says"] = (f"{facts['date']} 是工作日，但现在是 {where}，晚于常规播报"
                        f"窗口 {first:02d}:00–{last:02d}:00，当日卡不再因普通采样"
                        f"变化，不新增")
    else:
        base.update({"reason": "IN_WINDOW", "in_window": True,
                     "route": "ROUTINE_UPDATE", "tick": hour - first + 1})
        base["says"] = (f"{facts['date']} 是工作日，现在是 {where}，在常规播报窗口 "
                        f"{first:02d}:00–{last:02d}:00 内 · 今天第 {base['tick']}/"
                        f"{ROUTINE_UPDATES_PER_WORKDAY} 个时点")
    if facts["authority"] == "weekday-fallback":
        base["says"] += "；注意这一天不是官方工作日，是实例声明的 fallback"
    return base


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - hand check
    """Read the declaration and answer for one instant, on stdout."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calendar", default="")
    parser.add_argument("--at", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    at = args.at or datetime.datetime.now(
        datetime.timezone.utc).astimezone().isoformat()
    answer = routine_window(at, reading=read_declaration(args.calendar or None))
    if args.json:
        print(json.dumps(answer, ensure_ascii=False, indent=2,
                         default=str))
    else:
        print(answer["says"])
        print(f"calendar={answer['calendar']}  day={answer['day_ref']}  "
              f"kind={answer['kind']}  reason={answer['reason']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
