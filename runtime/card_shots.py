#!/usr/bin/env python3
"""把 `card_scenarios` 已经渲染好的预览页，拍成两种视口的截图。

它不渲染任何东西
----------------
这个模块**不调用** renderer，也**不重写**预览页。它启动 `card_scenarios.py`
产出 `.html`，再把那些 `.html` 原样加载进一个取景框里拍照。§11 要求
「CLI 预览、场景预览与生产 Feishu 必须来自同一渲染器」——所以唯一被允许的
做法是拍摄那份真实产物，而不是再造一份「看起来一样」的文档。取景框只决定
**画面框住哪一块**，不决定画什么：

    <iframe src="../previews/<scenario>.html"> 宽度 = 414 或 900
    再把 iframe 平移，使目标 `.frame` 的左上角落在视口原点。

`.frame.narrow` 是 390px、`body` 有 12px 内边距，所以窄屏视口 414 时左移 12px
之后可见区域恰好是那 390px 的真卡；桌面视口 900 时 `.wrap` 拿到满额 720px。
窗口尺寸随后**按量到的帧尺寸设定**，于是截图的像素尺寸与帧尺寸相等——这条
相等是取景成功的证据，也由 `_png_size()` 在拍完之后逐张核对。

为什么每一侧都要独立进程
------------------------
`card_scenarios` 用 `Path(__file__).parent` 找自己的兄弟模块，却又把加载结果
按固定名字缓存进 `sys.modules`。若在同一个进程里先后加载两个 revision 的
`card_scenarios`，第二个会命中第一个留下的 `presentation` / `cards`，于是
「Before」那一侧会用「After」的渲染器渲染——而且不会有任何东西报错，因为两次
都拿到了一个能用的模块。所以 `--renderer` 只被当作一个**要启动的子进程的
工作目录**，两侧永远不共享解释器状态。

Before 必须是基线自己的渲染器
-----------------------------
    --renderer <worktree>  那一侧的 `card_scenarios.py` 在哪，
                           `revision` 就取那一侧的 HEAD。

对本仓库的基线（`card-measurement-20260929`，clean）只做**读**：启动它的
`card_scenarios.py`，产物写到调用方给的目录。基线树本身不被写入、不被改动，
所以「Before」是它出厂时的样子，不是被谁修过一句的样子。

每一张截图上都带着它是谁拍的
----------------------------
`shots.json` 逐张记录 `renderer` / `revision` / `dirty` / `input_sha`（被拍那份
`.html` 的 sha256）/ `run_ref` / `viewport` / `scenario` / `label`，以及这张
截图的像素尺寸与帧尺寸是否相等。§12 要「截图元数据携带 revision/input_sha/
run_ref」就是为了这个：一张图离开这个目录之后，仍然能说出它是哪个 revision、
哪一份输入、哪一次运行拍出来的。

**截图不是投递证据。** 它证明 renderer 产出什么形状，不证明飞书怎么显示它。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

#: 一次拍两种视口，两种都是 §6 要求的：390px 是主视口，桌面端用来看折叠与
#: 两栏。`(输出后缀, iframe 视口宽, 目标 .frame 序号)`——序号由预览页决定，
#: 0 是 `.frame.narrow`，1 是桌面 `.frame`。序号不随视口宽变化，因为那一页
#: 无论多宽都渲染同样两个帧。
VIEWPORTS = (
    ("390", 414, 0),
    ("desktop", 900, 1),
)

#: 取景框。它只做两件事：给 iframe 定宽，然后在载入之后把目标帧挪到原点。
#: 量到的几何写进 `#probe`，调用方靠 `--dump-dom` 读它，这样帧尺寸是**量**出来
#: 的而不是假定出来的。
_WRAPPER = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:#fff;overflow:hidden}
iframe{border:0;display:block;transform-origin:0 0}
/* 量到的几何要能被 --dump-dom 读到，又绝不能出现在照片里：dump-dom 序列化
   的是 DOM，`display:none` 的节点照样有 textContent，而它不占据任何像素。 */
#probe{display:none}
</style></head><body>
<div id="probe">PENDING</div><iframe id="f" scrolling="no"></iframe>
<script>
const q = new URLSearchParams(location.search);
const f = document.getElementById('f');
f.style.width = Number(q.get('vw')) + 'px';
f.onload = () => {
  try {
    const d = f.contentDocument, frames = d.querySelectorAll('.frame');
    const el = frames[Number(q.get('i'))];
    if (!el) { throw new Error('no .frame at index ' + q.get('i')); }
    const r = el.getBoundingClientRect();
    f.style.height = d.documentElement.scrollHeight + 'px';
    f.style.transform = `translate(${-r.left}px, ${-r.top}px)`;
    document.getElementById('probe').textContent = 'GEOM ' + JSON.stringify(
      {frames: frames.length, w: Math.round(r.width), h: Math.round(r.height)});
  } catch (e) { document.getElementById('probe').textContent = 'GEOMERR ' + e; }
};
f.src = q.get('src');
</script></body></html>
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def _png_size(path: Path) -> tuple[int, int] | None:
    """A PNG's pixel size, read from IHDR and nothing else.

    WHY THIS AND NOT AN IMAGE LIBRARY
    ---------------------------------
    There is no image library on this node — no PIL in the test interpreter, no
    ImageMagick on the PATH. Rather than add a dependency to the acceptance
    path, the check this module actually needs is the first 24 bytes of the
    file: a screenshot whose width and height equal the frame's is a screenshot
    that framed the frame. Anything that did not (a caption, the whole page, a
    blank window) has the window's size instead, and the window is set from the
    frame.

    A `None` return means the file is not a PNG at all, which is what a hung
    Chrome leaves behind and what the caller must not read as a picture.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(24)
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))


class Chrome:
    """The one browser this node has that finishes a headless screenshot.

    WHICH BINARY, AND WHY IT IS NOT A PREFERENCE
    --------------------------------------------
    Two were measured here. The Playwright Chromium prints the DOM fine but
    hangs on `--screenshot` and `--print-to-pdf` even for a page with one
    paragraph, and the shared persistent browser that the MCP server attaches
    to is a different account's process and is not ours to touch. The system
    Chrome at the path below produced a real PNG. So this is the binary that
    exists for this job, not the one that was picked.

    A timeout is an ERROR and never an empty image: `run()` raises, because a
    hung browser and a scenario that rendered nothing look the same in a
    directory listing and must not.
    """

    DEFAULT = "/opt/google/chrome/chrome"

    def __init__(self, binary: str, profile: Path, *, timeout: int = 120):
        self.binary = binary
        self.profile = profile
        self.timeout = timeout

    def _run(self, args: list[str]) -> str:
        if shutil.which(self.binary) is None and not Path(self.binary).exists():
            raise FileNotFoundError(f"没有可用的 Chrome：{self.binary}")
        self.profile.mkdir(parents=True, exist_ok=True)
        command = [self.binary, "--headless=new", "--no-sandbox", "--disable-gpu",
                   "--disable-dev-shm-usage", "--hide-scrollbars",
                   # 取景框要读 iframe 的 contentDocument，而 file:// 文档之间
                   # 的脚本访问默认是关的；没有这一条，平移不会发生，截到的是
                   # 页首那块留白——一张尺寸对不上帧的图，也就是下面会拒收的图。
                   "--allow-file-access-from-files",
                   f"--user-data-dir={self.profile}",
                   "--virtual-time-budget=6000", *args]
        try:
            proc = subprocess.run(command, capture_output=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Chrome 在 {self.timeout}s 内没有结束（{' '.join(args[-1:])}）——"
                f"超时不是「这一张没内容」，这一轮不算数") from exc
        return proc.stdout.decode("utf-8", "replace")

    def geometry(self, url: str) -> dict:
        dom = self._run(["--dump-dom", url])
        for line in dom.splitlines():
            if "GEOM " in line:
                for chunk in line.split("GEOM ")[1:]:
                    end = chunk.find("</div>")
                    payload = chunk[:end if end >= 0 else None].strip()
                    try:
                        value = json.loads(payload)
                    except ValueError:
                        continue
                    if isinstance(value, dict) and "w" in value:
                        return value
        raise RuntimeError(f"取景框没有报出几何：{dom[:400]}")

    def shoot(self, url: str, *, width: int, height: int, out: Path) -> None:
        if out.exists():
            out.unlink()
        self._run([f"--window-size={width},{height}", f"--screenshot={out}", url])


def _scenario_names(renderer: Path, only: set[str] | None) -> list[str]:
    """The scenarios to photograph, asked of the RENDERER's own declaration.

    Not read from the YAML by this module: which scenarios exist is the
    renderer's fact, and a list held here would be a second author of it.
    """
    script = (
        "import sys, json; sys.path.insert(0, sys.argv[1]);"
        "import card_scenarios as cs;"
        "print(json.dumps([s.get('name') for s in cs.scenarios()]))")
    proc = subprocess.run([sys.executable, "-c", script, str(renderer / "runtime")],
                          cwd=str(renderer), capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"读不到 {renderer} 的场景表："
                           f"{proc.stderr.decode('utf-8', 'replace')[-800:]}")
    names = json.loads(proc.stdout.decode("utf-8"))
    return [n for n in names if only is None or n in only]


def _render(renderer: Path, out: Path) -> None:
    """One subprocess per side, so the two revisions never share a module table."""
    proc = subprocess.run(
        [sys.executable, str(renderer / "runtime" / "card_scenarios.py"),
         "--out", str(out)],
        cwd=str(renderer), capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{renderer} 的 card_scenarios 退出 {proc.returncode}："
                           f"{proc.stderr.decode('utf-8', 'replace')[-800:]}")


def _revision(renderer: Path) -> dict:
    def git(*args: str) -> str:
        # `status --porcelain` 是这里唯一一个 git 可能用写来回答的查询（刷新
        # 索引的 stat cache）；`GIT_OPTIONAL_LOCKS=0` 才让它真的是一次读。
        # 截图工具对着的是一棵只读的基线树，改它的索引比改任何东西都更不该发生。
        proc = subprocess.run(["git", "-C", str(renderer), *args],
                              capture_output=True,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        return proc.stdout.decode("utf-8", "replace").strip()
    porcelain = git("status", "--porcelain")
    return {"path": str(renderer),
            "revision": git("rev-parse", "HEAD"),
            "dirty": bool(porcelain),
            "changed_files": len([line for line in porcelain.splitlines() if line]),
            "subject": git("log", "-1", "--format=%s")}


def shoot(renderer: Path, out_dir: Path, *, label: str, run_ref: str,
          only: set[str] | None = None, chrome: str = Chrome.DEFAULT) -> dict:
    renderer = renderer.resolve()
    out_dir = Path(out_dir)
    previews = out_dir / "previews"
    wrap_dir = out_dir / "_wrap"
    shots_dir = out_dir / "shots"
    for directory in (wrap_dir, shots_dir):
        directory.mkdir(parents=True, exist_ok=True)

    _render(renderer, previews)
    browser = Chrome(chrome, out_dir / "_chrome-profile")
    wrap_dir.joinpath("wrap.html").write_text(_WRAPPER, encoding="utf-8")

    shots: list[dict] = []
    for name in _scenario_names(renderer, only):
        page = previews / f"{name}.html"
        if not page.is_file():
            raise RuntimeError(f"renderer 声明了场景 {name} 但没有产出 {page}")
        input_sha = _sha256(page)
        for suffix, viewport, index in VIEWPORTS:
            url = (f"file://{wrap_dir}/wrap.html"
                   f"?src=../previews/{name}.html&i={index}&vw={viewport}")
            geometry = browser.geometry(url)
            # 帧尺寸决定窗口尺寸，所以「截图尺寸 == 帧尺寸」这条等式不是巧合而是
            # 构造——它同时是取景成功的证据：任何没被框住的东西（标题、整页、
            # 空白）都拿不到这个大小。
            target = shots_dir / f"{name}.{suffix}.png"
            browser.shoot(url, width=geometry["w"], height=geometry["h"],
                          out=target)
            size = _png_size(target)
            shots.append({
                "label": label, "scenario": name, "viewport": suffix,
                "file": str(target.relative_to(out_dir)),
                "frame": {"w": geometry["w"], "h": geometry["h"],
                          "frames_on_page": geometry["frames"]},
                "png": {"w": size[0] if size else None,
                        "h": size[1] if size else None,
                        "bytes": target.stat().st_size if target.exists() else 0},
                "framed": bool(size) and size == (geometry["w"], geometry["h"]),
                "iframe_width": viewport, "frame_index": index,
                "input_sha": input_sha, "run_ref": run_ref,
            })

    manifest = {
        "schema": "serveros/card-shots.v1",
        "label": label, "run_ref": run_ref,
        "renderer": _revision(renderer),
        "previews": str(previews.relative_to(out_dir)),
        "viewports": [{"suffix": s, "iframe_width": v, "frame_index": i}
                      for s, v, i in VIEWPORTS],
        # 一句必须跟着这批图走的话：这些是 renderer 的产物，不是对话的产物。
        "note": ("这些截图由 card_scenarios 的真实产出拍摄，未经重绘；"
                 "它们证明 renderer 产出什么形状，不证明飞书怎么显示，"
                 "也不代表任何东西已被发送。"),
        "shots": shots,
    }
    manifest["unframed"] = [s["file"] for s in shots if not s["framed"]]
    out_dir.joinpath("shots.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--renderer", default=str(_HERE.parent),
                        help="哪一侧的 card_scenarios 参与渲染（该树的根目录）")
    parser.add_argument("--out", required=True)
    parser.add_argument("--label", required=True, choices=("before", "after"))
    parser.add_argument("--run-ref", required=True)
    parser.add_argument("--only", help="逗号分隔的场景名，默认全部")
    parser.add_argument("--chrome", default=Chrome.DEFAULT)
    args = parser.parse_args(argv)

    only = {n.strip() for n in args.only.split(",")} if args.only else None
    manifest = shoot(Path(args.renderer), Path(args.out), label=args.label,
                     run_ref=args.run_ref, only=only, chrome=args.chrome)
    for shot in manifest["shots"]:
        mark = "ok " if shot["framed"] else "BAD"
        print(f"{mark} {shot['scenario']}.{shot['viewport']} "
              f"{shot['png']['w']}x{shot['png']['h']} {shot['png']['bytes']}B")
    print(f"\nrevision={manifest['renderer']['revision']} "
          f"dirty={manifest['renderer']['dirty']} "
          f"shots={len(manifest['shots'])} unframed={len(manifest['unframed'])}")
    return 1 if manifest["unframed"] else 0


if __name__ == "__main__":  # pragma: no cover - hand check
    raise SystemExit(main())
