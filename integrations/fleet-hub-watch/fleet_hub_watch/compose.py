"""A resource card composed for the question, from a closed set of blocks.

Owner, 2026-11-12: «飞书资源监控卡片单一 … 问资源情况只会给内存缺口 … 多种花样的
表、图，监控 agent 自己聪明地组合». Every 「资源」 answer used to be the same
template with one memory curve. Here a card is a short list of BLOCKS — a
comparison table, bars, rings, a radar, curves, a ranking, number tiles — and
what goes on it is chosen per question:

* by the butler's model, which already reads the sentence (`intent.py`): it
  returns a `layout`, a list of at most `MAX_BLOCKS` block names with an
  optional reading and device each. It chooses the SHAPE only — every block
  is checked against the closed set below, and every number is drawn here from
  the hub's own readings and history, never from the model's text;
* when there is no model, or its layout is unusable, by `auto_layout`, which
  looks at the readings: what is wrong leads, then the comparison, then the
  reading under the most pressure as a curve.

PURE: views and histories in, card JSON out.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import cards
from .levels import ALERT, OK, TEMPLATE, UNCONFIRMED, WARN, WORD, worst

#: The readings a block can be about -> the word on the card.
METRICS = {"cpu": "CPU", "memory": "内存", "swap": "交换区", "disk": "磁盘", "gpu": "显存",
           "network": "网络丢包"}
#: The usage readings: what a ring or a radar of "how full" can show.
USAGE = ("cpu", "memory", "swap", "disk", "gpu")
#: The blocks, as the model is told about them.
BLOCKS = {
    "table": "多台设备各项读数的对比表",
    "bar": "柱状图：各台设备的某一项（不填 metric 就并排比较 CPU、内存、磁盘）",
    "trend": "近 6 小时曲线：填 metric 画各台的这一项；填 device 画这台的各项",
    "ring": "环形图：占用了多少、还剩多少（填 metric；填 device 只画这台）",
    "radar": "雷达图：几台设备各项读数的整体形状，一眼看出谁偏科",
    "rank": "最吃紧的几项读数排行（跨设备）",
    "tiles": "某一台设备的几个大数字（填 device）",
    "network": "网络质量：每台设备各条线路的往返时间、丢包，东京读它的快慢，以及今天走哪条更好",
}
MAX_BLOCKS = 4
MAX_RINGS = 3
LEVEL_ORDER = {ALERT: 0, WARN: 1, UNCONFIRMED: 2, OK: 3}
LEVEL_INK = {ALERT: "red", WARN: "orange"}
RING_COLOR = {OK: "#3370FF", WARN: "#F5A623", ALERT: "#F54A45", UNCONFIRMED: "#8F959E"}


# -- reading the layout -------------------------------------------------------

def read_layout(raw: Any, devices: dict[str, str]) -> list[dict[str, Any]]:
    """The blocks the model asked for, each one checked; [] when none survive.

    `devices` is ref -> display name; the model names a device by its name.
    Anything unknown is dropped rather than guessed at.
    """
    if not isinstance(raw, list):
        return []
    by_name = {name: ref for ref, name in devices.items() if name}
    out: list[dict[str, Any]] = []
    seen = set()
    for item in raw:
        if isinstance(item, str):
            item = {"block": item}
        if not isinstance(item, dict) or item.get("block") not in BLOCKS:
            continue
        metric = item.get("metric") if item.get("metric") in METRICS else None
        device = by_name.get(item.get("device")) if isinstance(item.get("device"), str) else None
        if item["block"] == "tiles" and device is None:
            continue
        key = (item["block"], metric, device)
        if key in seen:
            continue
        seen.add(key)
        out.append({"block": item["block"], "metric": metric, "device": device})
        if len(out) == MAX_BLOCKS:
            break
    return out


def auto_layout(views: list[dict], *, focus: str | None = None,
                device: str | None = None) -> list[dict[str, Any]]:
    """What a person would put on the card, from the readings alone."""
    if device is not None and focus == "network":
        return [{"block": "network", "metric": None, "device": device}]
    if device is not None:
        view = _view(views, device)
        hot = focus if focus in USAGE else _hottest(view)
        return [{"block": "tiles", "metric": None, "device": device},
                {"block": "ring", "metric": hot, "device": device},
                {"block": "trend", "metric": None, "device": device}]
    if focus == "network":
        return [{"block": "network", "metric": None, "device": None}]
    flagged = any(v.get("level") in (WARN, ALERT) for v in views)
    blocks: list[dict[str, Any]] = []
    if flagged:
        blocks.append({"block": "rank", "metric": None, "device": None})
    if any(r.get("level") == WARN and (r.get("key") == "link" or r.get("key", "").startswith("net:"))
           for v in views for r in v.get("readings") or []):
        blocks.append({"block": "network", "metric": None, "device": None})
    blocks.append({"block": "table", "metric": None, "device": None})
    if focus:
        blocks += [{"block": "bar", "metric": focus, "device": None},
                   {"block": "trend", "metric": focus, "device": None}]
    else:
        blocks += [{"block": "bar", "metric": None, "device": None},
                   {"block": "trend", "metric": _hottest_fleet(views), "device": None}]
    return blocks[:MAX_BLOCKS]


# -- facts ---------------------------------------------------------------------

def _view(views: list[dict], ref: str | None) -> dict | None:
    return next((v for v in views if v.get("device_ref") == ref), None)


def _name(view: dict) -> str:
    return view.get("name") or view.get("device_ref") or "?"


def current(view: dict | None, metric: str) -> dict | None:
    """The device's reading for `metric` with a number; for disks and GPUs the
    fullest one."""
    found = [r for r in (view or {}).get("readings") or []
             if cards._is_focus(r.get("key", ""), metric)
             and isinstance(r.get("value"), (int, float)) and not isinstance(r.get("value"), bool)]
    return max(found, key=lambda r: r["value"]) if found else None


def _hottest(view: dict | None) -> str:
    best, metric = -1.0, "memory"
    for name in USAGE:
        r = current(view, name)
        if r is not None:
            weight = r["value"] + (200 if r.get("level") in (WARN, ALERT) else 0)
            if weight > best:
                best, metric = weight, name
    return metric


def _hottest_fleet(views: list[dict]) -> str:
    best, metric = -1.0, "memory"
    for view in views:
        name = _hottest(view)
        r = current(view, name)
        weight = (r["value"] + (200 if r.get("level") in (WARN, ALERT) else 0)) if r else -1
        if weight > best:
            best, metric = weight, name
    return metric


def _pct(value: float) -> str:
    return f"{value:.0f}%"


def _ink(reading: dict) -> str:
    text = _pct(reading["value"])
    color = LEVEL_INK.get(reading.get("level"))
    return f"<font color='{color}'>{text}</font>" if color else text


# -- blocks --------------------------------------------------------------------

def _table(views: list[dict], i: int) -> list[dict]:
    shown = [m for m in USAGE if any(current(v, m) for v in views)]
    columns = [{"name": "device", "display_name": "设备", "data_type": "lark_md", "width": "auto"},
               {"name": "state", "display_name": "状态", "data_type": "lark_md", "width": "auto"}]
    columns += [{"name": m, "display_name": METRICS[m], "data_type": "lark_md", "width": "auto",
                 "horizontal_align": "right"} for m in shown]
    rows = []
    for view in sorted(views, key=lambda v: LEVEL_ORDER.get(v.get("level"), 4)):
        level = view.get("level") or UNCONFIRMED
        state = WORD.get(level, "无法确认")
        row = {"device": f"**{_name(view)}**",
               "state": f"<font color='{LEVEL_INK[level]}'>{state}</font>" if level in LEVEL_INK
               else state}
        for m in shown:
            r = current(view, m)
            row[m] = _ink(r) if r else "—"
        rows.append(row)
    if not rows:
        return []
    return [{"tag": "table", "element_id": cards._eid("tbl", i), "page_size": 10,
             "row_height": "low", "freeze_first_column": True,
             "header_style": {"background_style": "grey", "bold": True, "lines": 1,
                              "text_size": "normal", "text_align": "left"},
             "columns": columns, "rows": rows}]


def _bar(views: list[dict], metric: str | None, i: int) -> list[dict]:
    if metric:
        values = [{"device": _name(v), "value": round(r["value"], 1), "type": _name(v)}
                  for v in views if (r := current(v, metric))]
        spec_fields = {"xField": "device", "seriesField": "type"}
        title = f"{METRICS[metric]} · 各台现在（%）"
    else:
        values = [{"device": _name(v), "value": round(r["value"], 1), "type": METRICS[m]}
                  for v in views for m in ("cpu", "memory", "disk") if (r := current(v, m))]
        spec_fields = {"xField": ["device", "type"], "seriesField": "type"}
        title = "CPU / 内存 / 磁盘 · 各台现在（%）"
    if not values:
        return []
    return [{"tag": "chart", "element_id": cards._eid("bar", i, metric), "aspect_ratio": "2:1",
             "chart_spec": {
                 "type": "bar", "title": {"text": title}, "data": {"values": values},
                 "yField": "value", **spec_fields,
                 "label": {"visible": True, "position": "top"},
                 "bar": {"style": {"cornerRadius": [4, 4, 0, 0]}},
                 "axes": [{"orient": "left", "min": 0, "max": 100, "tick": {"tickCount": 3}},
                          {"orient": "bottom", "label": {"autoRotate": False, "autoHide": True}}],
                 "legends": {"visible": metric is None, "orient": "bottom"},
                 "color": cards.CHART_COLORS}}]


def _ring(view: dict, metric: str, eid: str) -> dict | None:
    r = current(view, metric) if metric in USAGE else None
    if r is None:
        return None
    used = round(r["value"], 1)
    return {"tag": "chart", "element_id": eid, "aspect_ratio": "1:1",
            "chart_spec": {
                "type": "pie", "title": {"text": f"{_name(view)} · {METRICS[metric]}"},
                "data": {"values": [{"type": "已用", "value": used},
                                    {"type": "空闲", "value": round(100 - used, 1)}]},
                "valueField": "value", "categoryField": "type",
                "outerRadius": 0.85, "innerRadius": 0.62, "padAngle": 0.6,
                "label": {"visible": False}, "legends": {"visible": False},
                "indicator": {"visible": True, "fixed": True,
                              "title": {"style": {"text": _pct(used), "fontSize": 20}},
                              "content": [{"style": {"text": METRICS[metric], "fontSize": 12}}]},
                "color": [RING_COLOR.get(r.get("level"), RING_COLOR[OK]), "#E5E6EB"]}}


def _rings(views: list[dict], metric: str | None, device: str | None, i: int) -> list[dict]:
    if device is not None:
        view = _view(views, device)
        metrics = [metric] if metric else [m for m in ("cpu", "memory", "disk") if current(view, m)]
        charts = [_ring(view, m, cards._eid("ring", i, device, m)) for m in metrics[:MAX_RINGS]]
    else:
        metric = metric or _hottest_fleet(views)
        charts = [_ring(v, metric, cards._eid("ring", i, v.get("device_ref"), metric))
                  for v in views[:MAX_RINGS]]
    charts = [c for c in charts if c is not None]
    if not charts:
        return []
    if len(charts) == 1:
        return charts
    return [cards._box(cards._eid("rings", i), [[c] for c in charts], background="default")]


def _radar(views: list[dict], i: int) -> list[dict]:
    shown = [m for m in USAGE if sum(1 for v in views if current(v, m)) >= 1]
    values = [{"key": METRICS[m], "value": round(current(v, m)["value"], 1), "type": _name(v)}
              for v in views for m in shown if current(v, m)]
    if len(shown) < 3 or not values:
        return []
    return [{"tag": "chart", "element_id": cards._eid("radar", i), "aspect_ratio": "4:3",
             "chart_spec": {
                 "type": "radar", "title": {"text": "各台设备的整体形状（%）"},
                 "data": {"values": values},
                 "categoryField": "key", "valueField": "value", "seriesField": "type",
                 "area": {"visible": True, "style": {"fillOpacity": 0.12}},
                 "point": {"visible": False},
                 "axes": [{"orient": "radius", "min": 0, "max": 100, "label": {"visible": False}}],
                 "legends": {"visible": True, "orient": "bottom"},
                 "color": cards.CHART_COLORS}}]


def _trend(views: list[dict], histories: dict[str, dict], metric: str | None,
           device: str | None, i: int) -> list[dict]:
    if device is not None:
        view = _view(views, device) or {}
        labels = {r["key"]: r["label"] for r in view.get("readings") or []}
        history = histories.get(device) or {}
        if metric:
            series = [(METRICS[metric], cards._focus_points(history, metric))]
            chart = cards.line_chart(cards._eid("trend", i, device, metric), series,
                                     title=f"{_name(view)} · {METRICS[metric]} · 近 6 小时（%）")
        else:
            chart = cards.trend_chart(cards._eid("trend", i, device), history, labels,
                                      title=f"{_name(view)} · 近 6 小时（%）", every=True)
    else:
        metric = metric or "memory"
        chart = cards.line_chart(
            cards._eid("trend", i, metric),
            [(_name(v), cards._focus_points(histories.get(v.get("device_ref")) or {}, metric))
             for v in views], title=f"{METRICS[metric]} · 近 6 小时（%）")
    return [chart] if chart is not None else []


def _rank(views: list[dict], i: int, limit: int = 5) -> list[dict]:
    rows = [(v, r) for v in views for r in v.get("readings") or []
            if isinstance(r.get("value"), (int, float)) and not isinstance(r.get("value"), bool)]
    rows.sort(key=lambda vr: (LEVEL_ORDER.get(vr[1].get("level"), 4), -vr[1]["value"]))
    lines = [f"**{_name(v)}** {r['label']}　`{cards._bar(r['value'])}` {_ink(r)}"
             for v, r in rows[:limit]]
    if not lines:
        return []
    return [cards._box(cards._eid("rank", i),
                       [[cards._caption("最吃紧的几项")] + [cards._text(line) for line in lines]])]


def _ms(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "连不上"
    return f"{value:.0f} ms" if value < 1000 else f"{value / 1000:.1f} s"


PATH_WORD = {"tokyo-public": "公网", "tokyo-tailnet": "tailnet", "local": "本地出口"}


def _path(key: str) -> str:
    name = key.removeprefix("net:")
    return PATH_WORD.get(name, name.removeprefix("tokyo-"))


def _usable(r: dict) -> bool:
    return isinstance(r.get("rtt_ms"), (int, float)) and r.get("loss", 100) < 100


def advice(views: list[dict]) -> list[str]:
    """One plain sentence per device that has something to say: a path that
    does not connect, which path to Tokyo is better, where the slowness is."""
    out = []
    for view in views:
        paths = [r for r in view.get("readings") or [] if r.get("key", "").startswith("net:")]
        tokyo = [r for r in paths if "tokyo" in r["key"]]
        local = [r for r in paths if "tokyo" not in r["key"]]
        name = f"**{_name(view)}**"
        dead = [r for r in tokyo if not _usable(r)]
        alive = [r for r in tokyo if _usable(r)]
        for r in dead:
            out.append(f"{name} 的 {_path(r['key'])} 连不上东京：这台可能没开它，或探测地址不对")
        if len(alive) >= 2:
            best = min(alive, key=lambda r: (r["loss"], r["rtt_ms"]))
            other = max(alive, key=lambda r: (r["loss"], r["rtt_ms"]))
            if best["loss"] + 5 < other["loss"] or best["rtt_ms"] * 1.3 < other["rtt_ms"]:
                out.append(f"{name} 到东京走 **{_path(best['key'])}** 更好"
                           f"（{_ms(best['rtt_ms'])}，{_path(other['key'])} {_ms(other['rtt_ms'])}"
                           + (f" 丢 {other['loss']:.0f}%" if other["loss"] else "") + "）")
        if alive and all(r.get("level") == WARN for r in alive):
            if local and all(r.get("level") == OK for r in local):
                out.append(f"{name} 本地出口正常，慢在去东京的那一段")
            elif local:
                out.append(f"{name} 本地出口本身就慢")
    return out


def _path_line(r: dict) -> str:
    if not _usable(r):
        return f"{_path(r['key'])}　<font color='red'>连不上</font>"
    value = _ms(r["rtt_ms"]) + (f" · 丢 {r['loss']:.0f}%" if r["loss"] else "")
    color = LEVEL_INK.get(r.get("level"))
    return f"{_path(r['key'])}　" + (f"<font color='{color}'>{value}</font>" if color else f"**{value}**")


def _network(views: list[dict], i: int) -> list[dict]:
    """One grey column per device: the hub's read of it, then each path."""
    columns = []
    for view in views:
        readings = view.get("readings") or []
        paths = [r for r in readings if r.get("key", "").startswith("net:")]
        link = next((r for r in readings if r.get("key") == "link"), None)
        # The hub reading itself (a few ms) says nothing about a network.
        if not paths and not (link and link.get("level") == WARN):
            continue
        lines = [cards._text(f"**{_name(view)}**")]
        if link:
            said = f"东京读它 {_ms(link['p50'] * 1000)}" + (
                f" · 失败 {link['fail']:.0f}%" if link["fail"] else "")
            color = LEVEL_INK.get(link.get("level"))
            lines.append(cards._caption(f"<font color='{color}'>{said}</font>" if color else said))
        lines += [cards._text(_path_line(r)) for r in paths]
        columns.append(lines)
    if not columns:
        return [cards._caption("还没有网络读数：设备的线路探测未开，或东京读它的次数还不够（约 5 分钟后出现）")]
    out = [cards._box(cards._eid("net", i), columns)]
    said = advice(views)
    if said:
        out.append(cards._text("\n".join(f"- {line}" for line in said)))
    out.append(cards._caption("往返＝设备连到对方用的时间（越小越好）· 东京读它＝东京每次读这台要多久"))
    return out


def _tiles(views: list[dict], device: str, i: int) -> list[dict]:
    view = _view(views, device)
    if view is None:
        return []
    tiles = []
    for m in ("cpu", "memory", "disk", "gpu", "swap"):
        r = current(view, m)
        if r is not None:
            tiles.append(cards._tile(r.get("label") or METRICS[m], _pct(r["value"]),
                                     cards.VALUE_COLOR.get(r.get("level"), "default")))
        if len(tiles) == 4:
            break
    if not tiles:
        why = next((r["text"] for r in view.get("readings") or []), "这次没读到读数")
        return [cards._text(f"**{_name(view)}**　<font color='grey'>{why}</font>")]
    return [cards._box(cards._eid("tiles", i, device), tiles)]


# -- the card ------------------------------------------------------------------

def compose(layout: list[dict[str, Any]], views: list[dict], *, now: datetime,
            histories: dict[str, dict] | None = None,
            running: dict[str, int] | None = None) -> dict:
    """One card, the blocks in the order given. A block with nothing to draw
    is left out, so a card is never a frame around blanks."""
    histories = histories or {}
    elements: list[dict] = []
    devices = {b.get("device") for b in layout if b.get("device")}
    shown = [v for v in views if not devices or v.get("device_ref") in devices] \
        if all(b.get("device") for b in layout) else views
    net_drawn = False
    for i, block in enumerate(layout):
        kind, metric, device = block["block"], block.get("metric"), block.get("device")
        if metric == "network" or kind == "network":
            # Loss as a bar or a ring misleads (one path that never connects
            # reads as 100%): the network is always drawn as its own block, once.
            if net_drawn:
                continue
            kind, net_drawn = "network", True
        if kind == "table":
            got = _table(views, i)
        elif kind == "bar":
            got = _bar(views, metric, i)
        elif kind == "ring":
            got = _rings(views, metric, device, i)
        elif kind == "radar":
            got = _radar(views, i)
        elif kind == "trend":
            got = _trend(views, histories, metric, device, i)
        elif kind == "rank":
            got = _rank(views, i)
        elif kind == "tiles":
            got = _tiles(views, device, i)
        elif kind == "network":
            got = _network([v for v in views if device is None or v.get("device_ref") == device], i)
        else:
            got = []
        elements += got
    if not elements:
        elements.append(cards._text("还没有可画的读数"))
    tasks = sum((running or {}).get(v.get("device_ref"), 0) for v in shown)
    foot = (f"{tasks} 个任务在跑 · " if tasks else "") + "「资源 东京」看单台 · 只读，不会动任何东西"
    elements.append(cards._caption(foot))
    what = "网络" if layout and all(b["block"] == "network" or b.get("metric") == "network"
                                     for b in layout) else "资源"
    if what == "网络":
        # A network card is judged by the network: a full swap on Tokyo is
        # not a reason for this header to say 3 台要看 (11-12 11:11).
        def net_level(view: dict) -> str:
            return worst(r.get("level") for r in view.get("readings") or []
                         if r.get("key") == "link" or str(r.get("key", "")).startswith("net:"))
        shown = [v for v in shown
                 if any(r.get("key") == "link" or str(r.get("key", "")).startswith("net:")
                        for r in v.get("readings") or [])
                 and (any(str(r.get("key", "")).startswith("net:") for r in v.get("readings") or [])
                      or net_level(v) == WARN)] or shown
        levels = {v.get("device_ref"): net_level(v) for v in shown}
    else:
        levels = {v.get("device_ref"): v.get("level") for v in shown}
    level = worst(levels.values()) if shown else UNCONFIRMED
    flagged = sum(1 for lv in levels.values() if lv in (WARN, ALERT))
    head = f"{flagged} 台要看" if flagged else ("全部正常" if level == OK else WORD.get(level, "无法确认"))
    title = (f"{_name(shown[0])} · {what}" if len(shown) == 1 and devices
             else f"Fleet {what} · {len(shown)} 台")
    return cards._card(TEMPLATE[level], title, cards._local(now, with_date=True), elements,
                       tags=[(head, cards.TAG_COLOR[level])],
                       summary=f"{title} · {head}")
