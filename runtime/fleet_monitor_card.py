#!/usr/bin/env python3
"""The Fleet monitor card: one logical card for a fleet's hardware and service
health, and a renderer that turns its view model into Feishu CardKit 2.0 JSON.

Two halves, and the seam between them is the point
--------------------------------------------------
`monitor_model` is PURE: it is handed readings and returns a model. It opens no
file, runs no command and reads no clock — `now` is a parameter. `render_cardkit`
is PURE too: it is handed a model and returns a card. Neither knows where a
number came from, and that is deliberate: a renderer that decides what a field
*means* is a second place the decisions live, which is the rule
`runtime/cards.py` already writes down for itself.

The third piece — the read-only producer adapter — is NOT here. It lives in
`runtime/fleet_monitor_source.py`, the only module in this pair allowed to touch
the machine, and it composes existing readings (`runtime/card_source.py`) rather
than measuring anything of its own. This is the property `不造平行状态权威`
asks for: the card is a *view* of readings others already took, never a second
place they are recorded.

The rules this card will not break
----------------------------------
1. UNKNOWN is never rendered as a healthy value, and never as `0`. A reading
   nobody took renders as a word with a reason; a reading whose last observation
   is older than its staleness bound renders as 已过期 — a third word, not a
   green one. `reading_line` is the single place this spelling lives.
2. A reading with no provenance is not a KNOWN reading. `_staleness` downgrades
   it to UNKNOWN with a reason, following `runtime/health.py`'s first rule.
3. Healthy services appear as COUNTS. An anomalous (or unreachable) one is
   lifted to the top of the card, where a reader who never opens a fold sees it.
4. A service added to the input enters the counts automatically: the counts are
   derived from the list, never authored.
5. Sample data is never presented as live. A model built with `sample=True`
   carries the mark on the header AND an explicit non-live line on the body —
   a deliberate divergence from `cards.py` §2's title-only mark, because §2 was
   protecting a line budget on a card a person asked for, whereas this mark's
   job is to survive a forwarded crop of a card nobody asked for.
6. One logical card. `lifecycle` names the in-place update cadence and the
   rotation API limits force; a physical card rotated on the API limit is a
   transport concern and not a second logical card.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

SCHEMA = "fleet-monitor-card.v1"

#: Normal refresh is ten minutes, in place. A severe alert bypasses it.
DEFAULT_REFRESH_S = 600
#: A reading older than this is 已过期 rather than current.
DEFAULT_STALE_AFTER_S = 1800
#: The byte budget a single card is held under. Feishu's own ceiling is larger;
#: this is the smaller ceiling this product sets, and `validate` enforces it so
#: a card that grew past a screen is caught here rather than in a conversation.
MAX_CARD_BYTES = 30000

SAMPLE_MARK = "[SAMPLE]"
SAMPLE_LINE = "⚠️ 样例数据 · 非实时，不代表生产读数"
CARD_KEY = "fleet-monitor"

STATE_KNOWN = "KNOWN"
STATE_UNKNOWN = "UNKNOWN"
STATE_STALE = "STALE"

#: The four axes of a service's health, MIRRORED from `runtime/health.py`'s
#: `AXES`. The pure module does not import `health`, because `health` loads the
#: registry and its contracts — I/O — and this module must stay I/O-free. The
#: two lists are bound instead by a test that imports both and asserts equality,
#: so the vocabulary cannot drift while the purity holds.
SERVICE_AXES = ("liveness", "transport", "functional", "readiness")

#: A card is read at 390px (`runtime/cards.py`'s `MOBILE_WIDTH`). A markdown
#: line past this many characters wraps into a paragraph nobody reads on that
#: width, so the renderer splits on the budget rather than trusting the source.
MOBILE_LINE_CHARS = 64

#: The attribution a host-share reading must carry to be shown at all. A share
#: that nobody attributed to a cgroup is not this user's usage, and showing it
#: would credit (or charge) the wrong account.
CGROUP_ATTRIBUTION = "cgroup"

#: The element tags this renderer may emit, and nothing else. `validate` walks a
#: rendered card and refuses a tag outside this set — inventing a plausible tag
#: is how a card renders as a blank rectangle in a conversation nobody watches.
VOCABULARY = frozenset({
    "markdown", "hr", "collapsible_panel", "column_set", "column", "chart",
    "plain_text", "standard_icon",
})

#: One vocabulary for the four summary states, copied from `runtime/cards.py`'s
#: own declaration rather than re-derived: the first time the two spellings
#: diverge, a reader who learned the word on one face finds it meaning something
#: else on the next.
FACE_WORDS = {"OK": "正常", "ATTENTION": "建议查看",
              "CRITICAL": "需要处理", "UNCONFIRMED": "无法确认"}
NEUTRAL_WORD = "无法确认"

#: Higher first. `attention` is sorted by this, then by input order, so the
#: ordering is deterministic and the most severe thing is on the first screen.
SEVERITY_RANK = {"CRITICAL": 0, "ATTENTION": 1, "STALE": 2, "UNKNOWN": 3, "OK": 4}

#: Labels the user has ruled out of this card. A reading whose label carries one
#: of these is a defect in the model, not a rendering choice: it is caught in
#: `validate_model` so the exclusion is enforced where the data is built.
FORBIDDEN_LABEL_TOKENS = (
    "WiFi 型号", "wifi 型号", "内网测速", "理论部署", "待采集",
    "部署计划", "未采集",
)


class MonitorCardError(RuntimeError):
    """A model or card that violates a rule this module exists to keep."""


# ---------------------------------------------------------------------------
# The reading — the one atom everything above this line is made of
# ---------------------------------------------------------------------------

def reading(label, value, *, unit="", note="", observed_at=None,
            severity="OK", trend=(), kind="reading", scope=None):
    """A measured reading. `observed_at` is its provenance and is required.

    A KNOWN reading with no `observed_at` is downgraded to UNKNOWN by
    `monitor_model`; the constructor does not enforce it here so that the
    downgrade happens in ONE place (the model step) rather than in two.
    """
    return {
        "kind": kind, "label": label, "value": value, "unit": unit, "note": note,
        "state": STATE_KNOWN, "observed_at": observed_at,
        "severity": severity, "trend": list(trend), "scope": scope,
    }


def unobserved(label, *, reason, kind="reading", scope=None):
    """A reading nobody took. It has a reason and NO value — never `0`.

    `reason` is required and has no default: "we did not measure this" is only
    useful with the cause, and a default would make every unmeasured reading
    cite the same word regardless of which source stayed silent.
    """
    return {
        "kind": kind, "label": label, "value": None, "unit": "", "note": "",
        "state": STATE_UNKNOWN, "reason": reason, "observed_at": None,
        "severity": None, "trend": [], "scope": scope,
    }


def stale(reading_dict, *, reason):
    """The same reading, marked 已过期. Its last value is kept and shown.

    Stale keeps the value on purpose: a stale reading is a real reading that
    has aged, and hiding it would make 已过期 indistinguishable from 无法确认.
    What changes is the word, and the word is what a reader branches on.
    """
    out = dict(reading_dict)
    out["state"] = STATE_STALE
    out["reason"] = reason
    return out


def network_reading(value, *, scope, observed_at=None, cap_bps=None,
                    cap_confirmed=False, unit="Mbps", severity="OK"):
    """A network flow reading, whose LABEL is decided by its scope.

    `scope` is required and there is no default, because the sentence this
    prevents is calling an undifferentiated total 公网. `"public"` is a flow
    measured at the public egress; `"undifferentiated"` is a total that has not
    been split, and it is labelled as such rather than promoted to the stronger
    claim by a default.
    """
    if scope not in ("public", "undifferentiated"):
        raise MonitorCardError(
            f"network scope {scope!r} is not one of public/undifferentiated; "
            "an unlabelled total must not borrow the 公网 claim")
    label = "公网出向" if scope == "public" else "网络总流量（口径未区分）"
    note = ""
    if scope == "public" and cap_confirmed and cap_bps:
        note = f"配额 {cap_bps / 1_000_000:.0f} Mbps（用户已确认）"
    return reading(label, value, unit=unit, note=note, observed_at=observed_at,
                   severity=severity, kind="network", scope=scope)


def quota_reading(bps, *, observed_at, confirmed, label="公网出口配额"):
    """The CONFIRMED egress quota, or `None` when it is not confirmed.

    `None` rather than an unobserved reading, and the difference is the point:
    the user confirmed exactly one quota (Tokyo, 100 Mbps) and confirmed none
    for the other machines. An unobserved quota row on every device would be a
    placeholder spending a card line to say nothing — the space `不拿…待持续采集
    占空间` refuses — and inventing a number would be worse. So an unconfirmed
    quota is simply absent, and `confirmed=True` is the only way a quota exists.
    """
    if not confirmed:
        return None
    return reading(label, bps / 1_000_000, unit="Mbps",
                   note="用户已确认", observed_at=observed_at, kind="network",
                   scope="public")


def share_reading(value, *, attribution, observed_at, note="cgroup 归因",
                  label="用户资源份额"):
    """A user's share of the host, or `None` when no cgroup attributed it.

    §"东京整机/vince 占用只有真实 cgroup 归因才显示" is the rule, and it is a
    rule about omission, not about wording: a share reading with no `cgroup`
    attribution is withheld entirely rather than shown as 无法确认, because a
    share nobody attributed is not an unknown share — it is not this user's
    share at all, and a card that printed it would be reading the wrong account.
    """
    if attribution != CGROUP_ATTRIBUTION:
        return None
    return reading(label, value, note=note, observed_at=observed_at,
                   kind="host-share", scope=CGROUP_ATTRIBUTION)


# ---------------------------------------------------------------------------
# The view model — pure
# ---------------------------------------------------------------------------

def _parse(instant):
    if not instant:
        return None
    try:
        parsed = datetime.fromisoformat(str(instant).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _staleness(reading_dict, now_dt, stale_after_s):
    """Apply rule 1 and rule 2 to one reading. The only place they are applied.

    A KNOWN reading with no parseable `observed_at` becomes UNKNOWN (no
    provenance is not a measurement); one whose age exceeds `stale_after_s`
    becomes STALE. Everything else is returned unchanged.
    """
    if reading_dict.get("state") != STATE_KNOWN:
        return reading_dict
    when = _parse(reading_dict.get("observed_at"))
    if when is None:
        out = dict(reading_dict)
        out["state"] = STATE_UNKNOWN
        out["value"] = None
        out["reason"] = "读数没有可解析的观测时间，不构成一次测量"
        return out
    age = (now_dt - when).total_seconds()
    if age > stale_after_s:
        return stale(reading_dict,
                     reason=f"最后观测 {reading_dict['observed_at']} 距今 "
                            f"{int(age // 60)} 分钟，超过新鲜度上限 "
                            f"{stale_after_s // 60} 分钟")
    return reading_dict


def _reachability(device, now_dt, stale_after_s):
    """A device's reachability as a state word, never as an empty list."""
    raw = device.get("reachability") or {}
    state = raw.get("state")
    if state != STATE_KNOWN:
        return {"state": STATE_UNKNOWN,
                "reason": raw.get("reason") or "没有观测到该设备的可达性",
                "observed_at": raw.get("observed_at")}
    when = _parse(raw.get("observed_at"))
    if when is None:
        return {"state": STATE_UNKNOWN,
                "reason": "可达性没有可解析的观测时间，不构成一次观测",
                "observed_at": raw.get("observed_at")}
    if (now_dt - when).total_seconds() > stale_after_s:
        return {"state": STATE_STALE,
                "reason": f"最后观测 {raw['observed_at']} 已过期",
                "observed_at": raw.get("observed_at")}
    return {"state": STATE_KNOWN, "reason": None,
            "observed_at": raw.get("observed_at")}


def _normalize_service(service, now_dt, stale_after_s):
    """One service's four axes, its derived state, and its freshness.

    The derived `state` is the AUTHORITY's word (whatever produced the service
    health) and is never recomputed here — only DOWNGRADED, and only for the two
    reasons rule 1 covers: a state that is not one of the three carryable words
    becomes UNKNOWN, and a carryable state whose observation is missing or old
    becomes UNKNOWN or STALE. A service is counted by this effective word, so a
    service whose health nobody re-read does not keep counting as healthy.
    """
    axes = {axis: (service.get("axis") or {}).get(axis) for axis in SERVICE_AXES}
    state = str(service.get("state") or "").upper()
    observed_at = service.get("observed_at")
    reason = service.get("reason")
    if state not in ("OK", "ATTENTION", "CRITICAL"):
        state, reason = STATE_UNKNOWN, reason or "没有该服务健康状态的判定"
    elif observed_at is None:
        state, reason = STATE_UNKNOWN, "健康判定没有观测时间，不构成一次观测"
    else:
        when = _parse(observed_at)
        if when is None:
            state, reason = STATE_UNKNOWN, "健康判定的观测时间不可解析"
        elif (now_dt - when).total_seconds() > stale_after_s:
            state = STATE_STALE
            reason = f"健康判定 {observed_at} 已过期，不能当作当前健康"
    return {"name": service.get("name"), "state": state, "axes": axes,
            "observed_at": observed_at, "reason": reason}


def _service_counts(services):
    """Counts over services ALREADY normalized, keyed by effective state."""
    counts = {"total": 0, "ok": 0, "attention": 0, "critical": 0,
              "stale": 0, "unknown": 0}
    for service in services:
        counts["total"] += 1
        key = {"OK": "ok", "ATTENTION": "attention", "CRITICAL": "critical",
               STATE_STALE: "stale"}.get(service.get("state"), "unknown")
        counts[key] += 1
    return counts


def _attention(devices, services):
    """The anomalies, most severe first, deterministically ordered.

    Built from two kinds of finding and never from a score: a device this
    runtime could not reach, and a reading whose severity has left OK, and a
    service that is not OK. STALE and UNKNOWN readings are deliberately NOT
    lifted here — they are not events, and pinning every unmeasured cell would
    turn the top of the card into a copy of the fold. They keep their own words
    where they sit, which is enough to keep them honest.
    """
    found = []
    for index, device in enumerate(devices):
        ref = device.get("device_ref")
        if device["reachability"]["state"] != STATE_KNOWN:
            found.append({
                "scope": "device", "device_ref": ref, "severity": "CRITICAL",
                "text": f"{device.get('display_name') or ref}："
                        f"{'失联' if device['reachability']['state'] == STATE_UNKNOWN else '可达性已过期'}"
                        f"（{device['reachability']['reason']}）",
                "_order": (SEVERITY_RANK["CRITICAL"], index, -1),
            })
        for r_index, r in enumerate(device.get("readings") or []):
            sev = str(r.get("severity") or "OK").upper()
            if sev in ("CRITICAL", "ATTENTION") and r.get("state") == STATE_KNOWN:
                found.append({
                    "scope": "reading", "device_ref": ref, "severity": sev,
                    "text": f"{device.get('display_name') or ref} · {r['label']}："
                            f"{r.get('value')}{r.get('unit') or ''}"
                            + (f"（{r['note']}）" if r.get("note") else ""),
                    "_order": (SEVERITY_RANK[sev], index, r_index),
                })
    for s_index, service in enumerate(services):
        state = str(service.get("state") or "").upper()
        if state in ("CRITICAL", "ATTENTION"):
            found.append({
                "scope": "service", "device_ref": None, "severity": state,
                "text": f"服务 {service.get('name')}："
                        + (service.get("reason") or FACE_WORDS.get(state, NEUTRAL_WORD)),
                "_order": (SEVERITY_RANK[state], len(devices), s_index),
            })
    found.sort(key=lambda item: item["_order"])
    for item in found:
        del item["_order"]
    return found


def monitor_model(*, devices, services=(), network=None, now, sample=False,
                  refresh_seconds=DEFAULT_REFRESH_S,
                  stale_after_s=DEFAULT_STALE_AFTER_S):
    """Compose a fleet monitor view model from readings others already took.

    PURE. It is handed readings — this function measures nothing, opens nothing
    and reads no clock. `now` is an ISO instant, and it is what staleness is
    measured against, so two callers with the same `now` get byte-identical
    models, which is what makes the render deterministic and testable.
    """
    now_dt = _parse(now)
    if now_dt is None:
        raise MonitorCardError(f"now={now!r} is not a parseable instant")

    built = []
    for device in devices:
        readings = [_staleness(r, now_dt, stale_after_s)
                    for r in (device.get("readings") or [])]
        built.append({
            "device_ref": device.get("device_ref"),
            "display_name": device.get("display_name") or device.get("device_ref"),
            "platform": device.get("platform") or "未知",
            "reachability": _reachability(device, now_dt, stale_after_s),
            "readings": readings,
        })

    services = [_normalize_service(s, now_dt, stale_after_s)
                for s in (services or [])]
    service_counts = _service_counts(services)
    unreached = sum(1 for d in built if d["reachability"]["state"] != STATE_KNOWN)
    reached = len(built) - unreached

    summary = {
        "services": service_counts,
        "devices": {"total": len(built), "reached": reached,
                    "unreached": unreached},
    }
    attention = _attention(built, services)

    model = {
        "schema": SCHEMA,
        "card_key": CARD_KEY,
        "sample": bool(sample),
        "generated_at": now,
        "summary": summary,
        "attention": attention,
        "services": services,
        "devices": built,
        # Rule 6: one logical card. The refresh cadence, the urgent path and the
        # physical rotation are metadata about THIS card, not about a second one.
        "lifecycle": {
            "card_key": CARD_KEY,
            "single_card": True,
            "mode": "in_place",
            "normal_refresh_seconds": refresh_seconds,
            "urgent_refresh": "on_critical_immediately",
            "physical_rotation": "on_api_limit",
        },
    }
    if network is not None:
        net = _staleness(network, now_dt, stale_after_s)
        model["network"] = net
    return model


# ---------------------------------------------------------------------------
# Rendering — pure
# ---------------------------------------------------------------------------

def reading_line(r):
    """One reading as the ONE sentence its state earns. No colour, no guess.

    This is the only function that spells 无法确认 / 已过期 / a value, so the
    three can never drift apart. An UNKNOWN reading never carries a number and
    a STALE reading never reads as current — `test_...` walks the three states
    and asserts exactly that.
    """
    label = r.get("label")
    if r.get("state") == STATE_UNKNOWN:
        return f"{label} **无法确认**（{r.get('reason') or '原因未记录'}）"
    if r.get("state") == STATE_STALE:
        return (f"{label} **已过期** — 最后读数 {_value(r)}"
                f"（{r.get('observed_at') or '时间未知'}）")
    return f"{label} {_value(r)}" + (f"（{r['note']}）" if r.get("note") else "")


def _value(r):
    value = r.get("value")
    if value is None:
        return "—"
    return f"{value}{r.get('unit') or ''}"


def _md(content):
    return {"tag": "markdown", "content": content}


def _hr():
    return {"tag": "hr"}


def _panel(element_id, title, blocks, *, expanded=False):
    """A fold, matching the shape the live cards send (`runtime/cards.py`)."""
    return {
        "tag": "collapsible_panel",
        "element_id": element_id,
        "expanded": expanded,
        "header": {
            "title": {"tag": "markdown", "content": f"**{title}**"},
            "vertical_align": "center",
            "icon": {"tag": "standard_icon",
                     "token": "down-small-ccm_outlined", "size": "16px 16px"},
            "icon_position": "follow_text",
            "icon_expanded_angle": -180,
        },
        "border": {"color": "grey", "corner_radius": "5px"},
        "padding": "8px 8px 8px 8px",
        "vertical_spacing": "8px",
        "elements": [_md("\n".join(blocks))],
    }


def _column_set(element_id, cells):
    """A face grid. `flex_mode: "none"` is what this product sets, and it is
    load-bearing: under `"none"` a phone narrows the columns, it does not stack
    them — see `runtime/cards.py`'s `_flex`, which had this wrong once."""
    return {
        "tag": "column_set",
        "element_id": element_id,
        "flex_mode": "none",
        "background_style": "default",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1,
             "elements": [_md(cell)]}
            for cell in cells
        ],
    }


def _chart(element_id, *, title, points, axis_label="值"):
    """A trend chart. GATED — see `render_cardkit`'s `charts_verified`.

    The `chart` element's exact schema is NOT verified against the SDK in this
    repository, so this builder is only ever reached when a caller declares
    `charts_verified=True`. Until that declaration is backed by a real client
    reading a real card, a trend renders as a text line instead (see
    `_trend_line`), and the gate is the honest default rather than an invented
    schema shipped because it looked plausible.
    """
    return {
        "tag": "chart",
        "element_id": element_id,
        "chart_spec": {
            "type": "line",
            "title": {"text": title},
            "xField": "x",
            "yField": "y",
            "axis": {"y": {"title": {"text": axis_label}}},
        },
        "data": [{"x": str(i), "y": float(v)} for i, v in enumerate(points)],
    }


def _trend_line(r):
    blocks = "▁▂▃▄▅▆▇█"
    points = [float(v) for v in (r.get("trend") or [])]
    if len(points) < 2:
        return None
    low, high = min(points), max(points)
    span = (high - low) or 1.0
    spark = "".join(blocks[min(7, int((v - low) / span * 7))] for v in points)
    return f"趋势 {r['label']} {spark} （{points[0]:g}→{points[-1]:g}）"


def _device_title(device):
    """A fold header that says the state, not just the name."""
    reach = device["reachability"]["state"]
    word = {"KNOWN": "可达", "STALE": "可达性过期"}.get(reach, "失联")
    readings = device.get("readings") or []
    bad = [r for r in readings
           if str(r.get("severity") or "").upper() in ("CRITICAL", "ATTENTION")]
    mark = {"CRITICAL": "🚨", "ATTENTION": "⚠️"}.get(
        max((str(r.get("severity")) for r in bad), default=""), "")
    unknowns = sum(1 for r in readings if r.get("state") != STATE_KNOWN)
    tail = f" · 无法确认 {unknowns}" if unknowns else ""
    return f"{mark} {device['display_name']} · {word}{tail}".strip()


def _device_elements(device, *, rich, charts_verified, index):
    ref = device.get("device_ref") or str(index)
    slug = _slug(ref)
    elements = []
    readings = device.get("readings") or []
    known = [r for r in readings
             if r.get("state") == STATE_KNOWN and r.get("value") is not None]
    if known and rich:
        cells = [f"**{r['label']}**\n{_value(r)}"
                 + (f"\n{r['note']}" if r.get("note") else "") for r in known[:4]]
        elements.append(_column_set(f"dev-{slug}-face", cells))
    elif known:
        # The safe variant: the same readings as ONE markdown line, built from
        # `markdown`, which every card this node sends already uses.
        face = " · ".join(f"**{r['label']}** {_value(r)}" for r in known[:6])
        elements.append(_md(_wrap_lines(face)))
    elif device["reachability"]["state"] != STATE_KNOWN:
        elements.append(_md("_未联系到本机，没有读数_"))
    lines = [reading_line(r) for r in readings]
    for r in readings:
        trend = _trend_line(r)
        if trend:
            lines.append(trend)
    if lines:
        elements.append(_md(_wrap_lines("\n".join(lines))))
    if rich and charts_verified:
        for r in readings:
            if len(r.get("trend") or []) >= 2:
                elements.append(_chart(f"dev-{slug}-trend-{_slug(r['label'])}",
                                       title=f"{device['display_name']} {r['label']}",
                                       points=r["trend"]))
    return elements


def _slug(text):
    """A stable, collision-safe id fragment from any label.

    A CJK label has no ASCII to keep, so the cleaned form is empty and two
    different labels ("内存", "磁盘 /") would both fall back to the same word —
    which is how two elements share an `element_id`. The fallback is therefore a
    digest of the ORIGINAL text, so distinct labels stay distinct and the same
    label stays stable across renders.
    """
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "-", str(text)).strip("-")
    if cleaned:
        return cleaned
    return "u" + hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:8]


def _summary_line(model):
    s = model["summary"]["services"]
    d = model["summary"]["devices"]
    return (f"**服务** 共 {s['total']} · 正常 {s['ok']} · 需关注 "
            f"{s['attention']} · 需处理 {s['critical']} · 已过期 {s['stale']}"
            f" · 无法确认 {s['unknown']}"
            f"\n**设备** 共 {d['total']} · 可达 {d['reached']} · 不可达 "
            f"{d['unreached']}")


def _wrap_lines(text, *, limit=MOBILE_LINE_CHARS):
    """Split any line past the 390px budget at the last space before it.

    A card is read on a phone and this renderer cannot see the phone. What it
    can do is refuse to emit a line longer than the width the tree already
    measured for a card, so a long reading wraps where the source put a space
    rather than wherever the client happens to break it.
    """
    out = []
    for line in text.split("\n"):
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            out.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        out.append(line)
    return "\n".join(out)


def _fleet_verdict(model):
    services = model["summary"]["services"]
    if services["critical"] or any(a["severity"] == "CRITICAL"
                                   for a in model["attention"]):
        return "CRITICAL"
    if services["attention"] or model["attention"]:
        return "ATTENTION"
    # Every service OK but something unmeasured is NOT OK: rule 1 says an
    # unmeasured reading may not be rounded to a healthy one. STALE counts with
    # UNKNOWN here — a health word nobody re-read is not a health word.
    if (services["unknown"] or services["stale"]
            or model["summary"]["devices"]["unreached"]):
        return "UNCONFIRMED"
    return "OK"


def is_urgent(model):
    """True when a CRITICAL finding is present, so the next send skips the wait.

    Asked of the MODEL and never of a clock: «severe enough to be immediate» is
    a property of what is on the card, and the cadence belongs to the caller
    (`runtime/fleet_monitor_plan.plan_update`).
    """
    return any(item.get("severity") == "CRITICAL"
               for item in (model.get("attention") or []))


_TEMPLATES = {"OK": "green", "ATTENTION": "orange",
              "CRITICAL": "red", "UNCONFIRMED": "grey"}


def render_cardkit(model, *, rich=False, charts_verified=False):
    """The Fleet monitor card as Feishu CardKit 2.0 JSON. PURE.

    The envelope matches `runtime/cards.py`'s `_card`: `schema: 2.0`, a header
    with `template`/`title`/`subtitle`, and a `body` with `elements`.

    THE SAFE VARIANT IS THE DEFAULT, and the two flags are the whole reason.
    `rich=False` emits only `markdown`, `hr` and `collapsible_panel` — the
    vocabulary every card this node already sends uses, so it is known to render
    in production. `column_set` was REMOVED from the live status card by
    `runtime/cards.py` (`_flex`: under `flex_mode: "none"` a phone narrows the
    columns rather than stacking them) and `chart`'s schema is not verified
    against the SDK here, so `rich=True` (which restores `column_set`) and
    `charts_verified=True` are BOTH opt-in and BOTH unverified against a real
    client. Until a real phone reads a real card, the default must not be the
    one nobody has seen.
    """
    verdict = _fleet_verdict(model)
    title = "Fleet 监控"
    if model.get("sample"):
        title = f"{title} · {SAMPLE_MARK}"

    elements = []
    if model.get("sample"):
        elements.append(_md(SAMPLE_LINE))
    elements.append(_md(_wrap_lines(_summary_line(model))))

    if model["attention"]:
        lines = [f"**⚠️ 需要关注（{len(model['attention'])}）**"]
        lines += [f"- {item['text']}" for item in model["attention"]]
        elements.append(_md(_wrap_lines("\n".join(lines))))

    elements.append(_hr())
    for index, device in enumerate(model["devices"]):
        fold = _panel(f"dev-{_slug(device.get('device_ref') or index)}-fold",
                      _device_title(device), [], expanded=False)
        fold["elements"] = _device_elements(
            device, rich=rich, charts_verified=charts_verified, index=index)
        elements.append(fold)

    if model.get("network"):
        elements.append(_md(reading_line(model["network"])))

    elements.append(_hr())
    elements.append(_md("只读 · 本卡不会停止、重启或清理任何东西 · "
                        f"每 {model['lifecycle']['normal_refresh_seconds'] // 60} "
                        "分钟原位更新"))

    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _TEMPLATES.get(verdict, "grey"),
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text",
                         "content": f"{model.get('generated_at')} 更新"},
        },
        "body": {"direction": "vertical", "elements": elements},
    }
    validate(card)
    return card


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _walk_tags(node, out):
    if isinstance(node, dict):
        if isinstance(node.get("tag"), str):
            out.append(node["tag"])
        for value in node.values():
            _walk_tags(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_tags(value, out)
    return out


def _walk_element_ids(node, out):
    if isinstance(node, dict):
        if "element_id" in node:
            out.append(node["element_id"])
        for value in node.values():
            _walk_element_ids(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_element_ids(value, out)
    return out


def validate(card):
    """Refuse a card that breaks a structural rule. Raises, never warns.

    Checks, in order: the schema version, that every tag is in the declared
    vocabulary, that every `element_id` is unique, that the card serialises,
    and that it fits `MAX_CARD_BYTES`. A card that fails any of these is not a
    card this module hands to a transport.
    """
    if card.get("schema") != "2.0":
        raise MonitorCardError(f"schema {card.get('schema')!r} is not '2.0'")
    unknown = sorted(set(_walk_tags(card, [])) - VOCABULARY)
    if unknown:
        raise MonitorCardError(
            f"card emits undeclared element tag(s) {unknown}; the vocabulary "
            f"is {sorted(VOCABULARY)} and an invented tag renders as a blank")
    ids = _walk_element_ids(card, [])
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise MonitorCardError(f"duplicate element_id(s) {duplicates}")
    blob = json.dumps(card, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(blob) > MAX_CARD_BYTES:
        raise MonitorCardError(
            f"card is {len(blob)} bytes, over the {MAX_CARD_BYTES} budget")
    return card


def validate_model(model):
    """Refuse a model whose readings break a content rule. Raises, never warns."""
    for device in model.get("devices") or []:
        for r in device.get("readings") or []:
            text = f"{r.get('label') or ''} {r.get('note') or ''}"
            for token in FORBIDDEN_LABEL_TOKENS:
                if token in text:
                    raise MonitorCardError(
                        f"reading {r.get('label')!r} carries the excluded "
                        f"topic {token!r}; it must not be on this card")
            if r.get("state") == STATE_KNOWN and not r.get("observed_at"):
                raise MonitorCardError(
                    f"KNOWN reading {r.get('label')!r} has no observed_at; a "
                    "measurement without provenance is not a measurement")
            if r.get("state") in (STATE_UNKNOWN, STATE_STALE) and not r.get("reason"):
                raise MonitorCardError(
                    f"reading {r.get('label')!r} is {r.get('state')} with no "
                    "reason; an unestablished fact must say why")
            # A host share is this user's share only under a real cgroup
            # attribution; `share_reading` withholds one without it, and this
            # refuses a model that smuggled one in by another door.
            if r.get("kind") == "host-share" and r.get("scope") != CGROUP_ATTRIBUTION:
                raise MonitorCardError(
                    f"host-share reading {r.get('label')!r} carries "
                    f"attribution {r.get('scope')!r}, not "
                    f"{CGROUP_ATTRIBUTION!r}; a share nobody attributed is not "
                    "this user's share")
    return model
