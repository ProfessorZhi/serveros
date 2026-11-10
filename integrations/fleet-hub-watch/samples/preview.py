"""Draw the sample cards as one HTML page, to look at before anything is sent.

    python3 integrations/fleet-hub-watch/samples/preview.py [--out preview.html] [name ...]

An approximation of how Feishu draws CardKit 2.0 — close enough to judge what
is said first, what is coloured and what repeats; not a pixel reference. It
reads the committed `samples/*.json`, so run `build_samples.py` first.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = ["status", "device-warn", "device-ok", "batch", "batch-done", "completed", "daily",
         "daily-quiet", "run-failed", "run-stalled", "alert-red", "alert-recovered",
         "resource-yellow"]
HEAD = {"green": ("#e8f8ee", "#1f7a3d"), "yellow": ("#fdf3d7", "#a3690a"),
        "orange": ("#fdecd8", "#b4540a"), "red": ("#fde7e5", "#c0271e"),
        "blue": ("#e6efff", "#1f55c7"), "grey": ("#eef0f2", "#4d5560")}
INK = {"grey": "#8f959e", "green": "#1f9d57", "orange": "#de7802", "red": "#d83931",
       "blue": "#3370ff", "neutral": "#646a73"}
TAG = {"green": ("#d9f5e3", "#186a3b"), "orange": ("#fde7cc", "#a14d05"),
       "red": ("#fbdcd9", "#a8221a"), "blue": ("#dbe7ff", "#1c4cba"),
       "neutral": ("#e4e6ea", "#3d444d")}
CSS = """
body{font:14px/1.6 -apple-system,'PingFang SC','Noto Sans CJK SC',sans-serif;background:#f2f3f5;
     margin:24px;color:#1f2329}
.grid{display:grid;grid-template-columns:repeat(2,460px);gap:28px;align-items:start}
.name{font-size:12px;color:#8f959e;margin:0 0 6px 2px}
.card{background:#fff;border-radius:10px;border:1px solid #dee0e3;overflow:hidden}
.head{padding:12px 16px}.head b{font-size:16px}.sub{font-size:12px;opacity:.85}
.body{padding:12px 16px 14px}.md{margin:6px 0;white-space:pre-wrap}
.heading{font-size:20px;line-height:1.3}.notation{font-size:12px}
.tag{display:inline-block;font-size:12px;line-height:18px;padding:0 6px;border-radius:4px;
     margin-left:6px;vertical-align:middle}
.cols{display:flex;gap:8px;margin:6px 0}.col{border-radius:8px;padding:8px 12px;min-width:0}
.col.grey{background:#f5f6f7}.col .md{margin:2px 0}
hr{border:0;border-top:1px solid #e5e6eb;margin:10px 0}
.panel{border:1px solid #dee0e3;border-radius:6px;padding:6px 10px;margin:6px 0}
.panel summary{cursor:pointer}.chart{margin:8px 0}
.btn{display:inline-block;border:1px solid #c9cdd4;border-radius:6px;padding:4px 12px;margin-top:6px}
"""


def md(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"&lt;font color='(\w+)'&gt;(.*?)&lt;/font&gt;",
                 lambda m: f"<span style='color:{INK.get(m[1], '#1f2329')}'>{m[2]}</span>", out, flags=re.S)
    out = re.sub(r"&lt;text_tag color='(\w+)'&gt;(.*?)&lt;/text_tag&gt;", lambda m: tag(m[2], m[1]), out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out, flags=re.S)
    return re.sub(r"(?m)^- ", "• ", out)


def tag(word: str, color: str) -> str:
    back, ink = TAG.get(color, TAG["neutral"])
    return f"<span class='tag' style='background:{back};color:{ink};margin-left:0'>{word}</span>"


def chart(spec: dict) -> str:
    values = spec["data"]["values"]
    times = list(dict.fromkeys(v["time"] for v in values))
    series = list(dict.fromkeys(v["type"] for v in values))
    colors = spec.get("color") or ["#3370FF", "#F5A623"]
    w, h = 420, 150
    parts = [f"<svg class='chart' width='{w}' height='{h + 34}' viewBox='0 0 {w} {h + 34}'>"]
    for mark in (0, 50, 100):
        y = h - mark / 100 * h + 4
        parts.append(f"<line x1='28' x2='{w}' y1='{y}' y2='{y}' stroke='#e5e6eb'/>"
                     f"<text x='0' y='{y + 4}' font-size='10' fill='#8f959e'>{mark}</text>")
    for i, name in enumerate(series):
        pts = {v["time"]: v["value"] for v in values if v["type"] == name}
        path = " ".join(f"{28 + (w - 34) * times.index(t) / max(1, len(times) - 1):.1f},"
                        f"{h - pts[t] / 100 * h + 4:.1f}" for t in times if t in pts)
        color = colors[i % len(colors)]
        parts.append(f"<polyline fill='none' stroke='{color}' stroke-width='2' points='{path}'/>"
                     f"<circle cx='{40 + i * 80}' cy='{h + 22}' r='4' fill='{color}'/>"
                     f"<text x='{48 + i * 80}' y='{h + 26}' font-size='11' fill='#646a73'>{name}</text>")
    return "".join(parts) + "</svg>"


def element(e: dict) -> str:
    kind = e.get("tag")
    if kind == "markdown":
        return f"<div class='md {e.get('text_size', '')}'>{md(e.get('content', ''))}</div>"
    if kind == "hr":
        return "<hr>"
    if kind == "column_set":
        cols = "".join(
            f"<div class='col {c.get('background_style', '')}' style='flex:{c.get('weight', 1)}'>"
            + "".join(element(x) for x in c.get("elements") or []) + "</div>"
            for c in e.get("columns") or [])
        return f"<div class='cols'>{cols}</div>"
    if kind == "collapsible_panel":
        inner = "".join(element(x) for x in e.get("elements") or [])
        return (f"<details class='panel' {'open' if e.get('expanded') else ''}><summary>"
                f"{md(e['header']['title']['content'])}</summary>{inner}</details>")
    if kind == "chart":
        return chart(e["chart_spec"])
    if kind == "button":
        return f"<span class='btn'>{html.escape(e.get('text', {}).get('content', '按钮'))}</span>"
    return f"<div class='md notation'>[{html.escape(str(kind))}]</div>"


def card(name: str, doc: dict) -> str:
    header = doc["header"]
    back, ink = HEAD.get(header.get("template"), HEAD["grey"])
    tags = "".join(
        f"<span class='tag' style='background:{TAG.get(t['color'], TAG['neutral'])[0]};"
        f"color:{TAG.get(t['color'], TAG['neutral'])[1]}'>{html.escape(t['text']['content'])}</span>"
        for t in header.get("text_tag_list") or [])
    body = "".join(element(e) for e in doc["body"]["elements"])
    return (f"<div><div class='name'>{name} · 会话列表摘要：{html.escape((doc['config'].get('summary') or {}).get('content', '—'))}</div>"
            f"<div class='card'><div class='head' style='background:{back};color:{ink}'>"
            f"<b>{html.escape(header['title']['content'])}</b>{tags}"
            f"<div class='sub'>{html.escape(header['subtitle']['content'])}</div></div>"
            f"<div class='body'>{body}</div></div></div>")


def page(names: list[str]) -> str:
    cards = "".join(card(n, json.loads((HERE / f"{n}.json").read_text(encoding="utf-8")))
                    for n in names if (HERE / f"{n}.json").is_file())
    return f"<!doctype html><meta charset='utf-8'><style>{CSS}</style><div class='grid'>{cards}</div>"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*")
    parser.add_argument("--out", default="preview.html")
    args = parser.parse_args(argv)
    Path(args.out).write_text(page(args.names or ORDER), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
