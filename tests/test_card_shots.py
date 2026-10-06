"""`runtime/card_shots.py` 的测试 —— 合同说它有一份，而它一直没有。

THE CLAIM THIS FILE MAKES TRUE
------------------------------
`runtime/actuator-contract.yaml` 的 §12 段落写着：这个模块「no caller in this
repository, and the only thing that imports it is `tests/test_card_shots.py`」。

2026-10-07 按 B5（「Chrome 路径按本机实际安装、执行与覆盖入口判断」）核对：

    $ git log --all -- tests/test_card_shots.py     # 本 clone 的每一个 ref
    (空)
    $ command grep -rln --include='*.py' card_shots .
    (空 —— 连这个模块自己都不提自己的名字)

也就是说：那个文件**从来没有存在过**，而这个模块当时在全树里**没有任何
importer**。合同不是「记错了」，是先替一份还没写的测试把话说完了。

方向不是把那一句删掉。删掉之后覆盖缺口仍然在，只是不再有人看得见它 ——
而这句话的全部用处就是让人看得见它。所以这里做的是把那份测试写出来。

WHAT IS PINNED HERE, AND WHY EACH ONE IS NOT A COMMENT
------------------------------------------------------
一个驱动真浏览器的模块，最容易写的测试是「起一台真的 Chrome 拍一张图」，
而那恰好是最不该写的那一种：它把套件的速度、这台机器上有没有 Chrome、
`/opt` 下那个二进制今天是什么版本，全都变成判据的一部分。本文件因此
**不启动任何真实浏览器**（`Chrome.DEFAULT` 只被当作一个**字符串**读），
被驱动的是一个会记 argv、会按环境变量回话的替身。

被钉住的四件事：

  * **它是参数，不是常量** —— 默认值是 `/opt/google/chrome/chrome`，但
    `Chrome()`、`shoot(chrome=)`、CLI 的 `--chrome` 都能换掉它。这条是 B5
    的「覆盖入口」那一半，而它有一条**对照组**：不给 `--chrome` 时收到的
    必须正好是默认值，否则「参数被消费」与「参数被忽略、恰好蒙对」分不开。
  * **两条失败方向** —— 二进制不在 ⇒ 指名报错；超时 ⇒ `RuntimeError`，
    **绝不**返回空串。第二条是这个模块的 docstring 点名的：挂死的浏览器与
    「这一张渲染出来是空的」在目录列表里长得一样，所以两者不许同形。
  * **`shoot()` 先删旧图再拍** —— 否则「这张是新拍的」没有任何判据：
    一个没被删掉的旧文件与一次成功的拍摄，`ls` 给出的答案是同一个。
  * **`geometry()` 量不到几何要报错** —— 空框会让「截图尺寸 == 帧尺寸」
    这条等式在两边都取零时也成立，而那正是它被当成证据的地方。

最后两条是端到端的（假 renderer 是一个真的 git 仓库），它们钉的不是上面
任何一条，而是 `shoot()` 把这几件拼起来之后**留下的那份 manifest**，以及
「两侧不共享解释器」这条理由的可核形状。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from runtime import card_shots

#: 一台不是浏览器的浏览器。它做四件事：把 argv 记下来、按 `CARD_SHOTS_FAKE_MODE`
#: 回话、按 `CARD_SHOTS_FAKE_PNG` 产出一张**结构完整**的 PNG、把「拍的时候
#: 目标文件在不在」也记下来。
#:
#: PNG 里除了 IHDR 之外的字节没有任何读取者（`_png_size` 的 docstring 写明它只读
#: 前 24 个字节），仍然写成一张合法的图，是因为这个替身要冒充的是「浏览器拍出来
#: 的东西」：一张 320x180 的图在文件头上就该是 320x180。
#:
#: 「拍的时候目标文件在不在」只有**子进程**看得见，这正是它值钱的地方：
#: `shoot()` 有没有先删旧图，从父进程那一侧看只是「文件在/不在」，而两者在一次
#: 成功的拍摄之后是同一个答案。
BROWSER = '''\
#!/usr/bin/env python3
"""A browser that is not one: it records the argv and answers on command."""
import json, os, pathlib, struct, sys, time, zlib


def png(width, height):
    raw = b"".join(b"\\x00" + b"\\x00\\x00\\x00" * width for _ in range(height))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\\x89PNG\\r\\n\\x1a\\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


record = {"argv": sys.argv[1:]}
mode = os.environ.get("CARD_SHOTS_FAKE_MODE", "dom")
if mode == "sleep":
    time.sleep(float(os.environ.get("CARD_SHOTS_FAKE_SLEEP", "30")))
    sys.exit(0)

for arg in sys.argv[1:]:
    if arg.startswith("--screenshot="):
        out = pathlib.Path(arg.split("=", 1)[1])
        record["out_existed_at_exec"] = out.exists()
        width, height = (int(n) for n in os.environ["CARD_SHOTS_FAKE_PNG"].split("x"))
        out.write_bytes(png(width, height))
    if arg.startswith("--user-data-dir="):
        record["profile_existed_at_exec"] = pathlib.Path(
            arg.split("=", 1)[1]).exists()

# 同一个目录里一个不在 shoot() 管辖范围内的文件。这条是上面那条的**正控**：
# 没有它，「没看见旧图」与「这个探针什么文件都看不见」是同一个读数。
record["sibling_visible"] = pathlib.Path(
    os.environ["CARD_SHOTS_FAKE_LOG"]).parent.joinpath("sibling.txt").exists()

log = pathlib.Path(os.environ["CARD_SHOTS_FAKE_LOG"])
log.write_text(json.dumps(record), encoding="utf-8")
sys.stdout.write(os.environ.get("CARD_SHOTS_FAKE_DOM", ""))
'''

#: 一个不是渲染器的渲染器：按 `_scenario_names` 要的形状报出自己的场景表，按
#: `_render` 要的形状写出一份预览页。两个角色在同一个文件里，因为生产侧也是
#: 同一个文件（`card_scenarios.py` 既是模块，也是 `--out` 的入口）。
RENDERER = '''\
import argparse, pathlib

SCENARIOS = [{"name": "alpha"}, {"name": "beta"}]


def scenarios():
    return SCENARIOS


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        out.joinpath(scenario["name"] + ".html").write_text(
            "<!DOCTYPE html><html><body><div class='frame'>"
            + scenario["name"] + "</div></body></html>", encoding="utf-8")
'''


@pytest.fixture
def fake_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """一台替身浏览器，和它那一份读数。

    `read()` 给出替身记下的那一次调用；**没被调用过**时它 `pytest.fail` 而不是
    返回一个空记录 —— 一个没有 argv 的替身与「这个替身根本没跑」是同一个空
    文件，正是本仓库反复记着的那一族。
    """
    binary = tmp_path / "browser"
    binary.write_text(BROWSER, encoding="utf-8")
    binary.chmod(0o755)
    log = tmp_path / "argv.json"
    monkeypatch.setenv("CARD_SHOTS_FAKE_LOG", str(log))
    # 默认就报出一份几何：`geometry()` 是消费 `--dump-dom` 的那一个入口，而
    # 「量不到就报错」是它**另一条**测试的事（那一条自己把这个变量换掉）。
    monkeypatch.setenv("CARD_SHOTS_FAKE_DOM",
                       '<div id="probe">GEOM {"w":320,"h":180,"frames":1}</div></body>')
    monkeypatch.setenv("CARD_SHOTS_FAKE_PNG", "320x180")

    def read() -> dict:
        if not log.exists():
            pytest.fail("替身浏览器一次都没有被调用（没有 argv 记录）")
        return json.loads(log.read_text(encoding="utf-8"))

    return binary, read


@pytest.fixture
def fake_renderer(tmp_path: Path) -> Path:
    """一个真的 git 仓库，装着那份假的 `card_scenarios.py`。

    `_revision()` 用的是真的 `git -C`，所以「这一版是哪一版」在测试里也是真的
    量出来的，而不是被替换掉的。
    """
    renderer = tmp_path / "renderer"
    (renderer / "runtime").mkdir(parents=True)
    renderer.joinpath("runtime", "card_scenarios.py").write_text(
        RENDERER, encoding="utf-8")
    # `_scenario_names` 用 `python -c "import card_scenarios"` 读场景表，所以
    # renderer 里会多出一个 `__pycache__`。它**不是**这个模块写的（模块只读
    # renderer），所以按真实仓库的样子忽略掉，而不是让「谁写了 renderer」这条
    # 判据被一个解释器缓存搅浑。
    renderer.joinpath(".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "Test Actor", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "Test Actor",
           "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    for command in (["init", "-q"], ["add", "-A"],
                    ["commit", "-q", "-m", "renderer"]):
        proc = subprocess.run(["git", "-C", str(renderer), *command],
                              capture_output=True, env=env)
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return renderer


# --------------------------------------------------------------------------
# 它是参数，不是常量
# --------------------------------------------------------------------------

def test_the_default_is_the_measured_binary_and_it_is_a_parameter():
    """B5 的三分之一：本机实际装在那里的那个，且它是可以被换掉的。

    两个读数一起才是这条：只断言默认值，读不出「换个二进制可行」；只断言
    「能传参」，读不出默认值是哪一台。而默认值是**量**出来的（docstring 记着
    「Two were measured here」），所以它必须是一条测试而不是一句注释。
    """
    assert card_shots.Chrome.DEFAULT == "/opt/google/chrome/chrome"

    # 二进制从构造起就是调用方给的，不是类里写死的。
    parameters = inspect.signature(card_shots.Chrome.__init__).parameters
    assert parameters["binary"].default is inspect.Parameter.empty

    # 而它一路透到模块级入口与 CLI。
    assert (inspect.signature(card_shots.shoot).parameters["chrome"].default
            == card_shots.Chrome.DEFAULT)


# --------------------------------------------------------------------------
# 失败方向一：二进制不在
# --------------------------------------------------------------------------

def test_a_missing_binary_names_itself_and_never_returns_an_empty_dom(
        tmp_path: Path):
    """报错里必须有那个路径：否则「Chrome 不在」读起来像「这个页面是空的」。"""
    browser = card_shots.Chrome(str(tmp_path / "not-here"), tmp_path / "profile")
    with pytest.raises(FileNotFoundError) as caught:
        browser.geometry("data:text/html,x")
    assert "not-here" in str(caught.value)


# --------------------------------------------------------------------------
# 失败方向二：超时
# --------------------------------------------------------------------------

def test_a_timeout_is_an_error_and_never_an_empty_picture(
        fake_browser, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """挂死与空结果必须不同形 —— 这是模块 docstring 点名的那一条。

    两半都要读：`RuntimeError`（不是返回空串），**并且**没有产出文件。
    只读前一半的话，一个「先写了张空图再抛异常」的实现也会通过。
    """
    binary, _ = fake_browser
    monkeypatch.setenv("CARD_SHOTS_FAKE_MODE", "sleep")
    monkeypatch.setenv("CARD_SHOTS_FAKE_SLEEP", "5")
    out = tmp_path / "shot.png"
    browser = card_shots.Chrome(str(binary), tmp_path / "profile", timeout=1)
    with pytest.raises(RuntimeError) as caught:
        browser.shoot("data:text/html,x", width=320, height=180, out=out)
    assert "超时" in str(caught.value)
    assert not out.exists()


# --------------------------------------------------------------------------
# argv 的形状：合同逐条声明过的那一份
# --------------------------------------------------------------------------

def test_the_argv_prefix_is_the_reach_the_contract_declares(
        fake_browser, tmp_path: Path):
    """`_run` 拼出来的前缀就是合同第 12 节声明的那几条，顺序也在。

    顺序不是装饰：`--user-data-dir` 与 `--virtual-time-budget` 在**模式参数
    之前**，`--dump-dom` / `--screenshot=` 在最后。而 `--no-sandbox` 抽掉，
    这台机器上的 Chrome 直接起不来 —— 「起不来」在这里长成「拍不出图」。
    """
    binary, read = fake_browser
    profile = tmp_path / "profile"
    card_shots.Chrome(str(binary), profile).geometry("data:text/html,x")

    argv = read()["argv"]
    assert argv[:6] == ["--headless=new", "--no-sandbox", "--disable-gpu",
                        "--disable-dev-shm-usage", "--hide-scrollbars",
                        "--allow-file-access-from-files"]
    assert argv[6] == f"--user-data-dir={profile}"
    assert argv[7] == "--virtual-time-budget=6000"
    assert argv[8] == "--dump-dom"
    assert argv[9] == "data:text/html,x"
    # profile 是**子进程启动之前**由 `_run` mkdir 出来的，所以它已经在了：
    # 少这一步，Chrome 会自己造一个临时 profile，而「共用哪个 profile」就
    # 不再由调用方决定。
    assert read()["profile_existed_at_exec"] is True


# --------------------------------------------------------------------------
# 先删旧图再拍
# --------------------------------------------------------------------------

def test_shoot_replaces_the_previous_picture_rather_than_leaving_it(
        fake_browser, tmp_path: Path):
    """旧图必须**在子进程启动之前**就没了。

    判据由子进程给出，因为父进程这一侧看不出区别：`shoot()` 之后文件总是在的。
    同目录那个 sibling.txt 是**正控**：它证明这个探针确实看得见「本来就在那儿
    的文件」，所以「没看见旧图」不是探针瞎了。
    """
    binary, read = fake_browser
    out = tmp_path / "shot.png"
    out.write_bytes(b"not a png, and yesterday's")
    tmp_path.joinpath("sibling.txt").write_text("here all along", encoding="utf-8")

    card_shots.Chrome(str(binary), tmp_path / "profile").shoot(
        "data:text/html,x", width=320, height=180, out=out)

    record = read()
    assert record["out_existed_at_exec"] is False
    assert record["sibling_visible"] is True
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert card_shots._png_size(out) == (320, 180)


# --------------------------------------------------------------------------
# 几何：量到才算数
# --------------------------------------------------------------------------

def test_geometry_reads_the_frame_size_out_of_the_dom(
        fake_browser, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    binary, _ = fake_browser
    monkeypatch.setenv(
        "CARD_SHOTS_FAKE_DOM",
        '<div id="probe">GEOM {"w":414,"h":532,"frames":1}</div></body>')
    assert card_shots.Chrome(str(binary), tmp_path / "profile").geometry("u") == {
        "w": 414, "h": 532, "frames": 1}


def test_a_dom_without_a_measurement_is_refused_not_read_as_an_empty_frame(
        fake_browser, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """取景框没报几何 ⇒ 报错。空框会让「截图尺寸 == 帧尺寸」在两边都取零时成立。"""
    binary, _ = fake_browser
    monkeypatch.setenv("CARD_SHOTS_FAKE_DOM",
                       '<div id="probe">PENDING</div></body>')
    with pytest.raises(RuntimeError) as caught:
        card_shots.Chrome(str(binary), tmp_path / "profile").geometry("u")
    assert "几何" in str(caught.value)
    assert "PENDING" in str(caught.value)


# --------------------------------------------------------------------------
# 覆盖入口：CLI 那一半，带对照组
# --------------------------------------------------------------------------

def test_the_cli_override_reaches_the_producer_and_the_default_does_too(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    """`--chrome` 被消费了吗 —— 只有和「不给」的那一次比才读得出来。

    两次调用都用一个假的 `shoot` 接住，所以这里钉的是**参数的传递**，不是拍摄
    本身：给了 `--chrome /other/chrome` 就必须收到它；不给就必须正好收到
    `Chrome.DEFAULT`。少了第二次，「参数被消费」与「参数被忽略、恰好蒙对」给出
    同一个答案。
    """
    seen: list[dict] = []

    def recorder(renderer, out_dir, **kwargs):
        seen.append({"renderer": renderer, "out_dir": out_dir, **kwargs})
        # `main` 读的比 shots 多：它还要把 renderer 的 revision 印出来。少给
        # 这一格，这条测试就会以一个 KeyError 收场，而那不是「参数没传到」。
        return {"shots": [], "unframed": [],
                "renderer": {"revision": "0" * 40, "dirty": False}}

    monkeypatch.setattr(card_shots, "shoot", recorder)
    base = ["--renderer", str(tmp_path), "--out", str(tmp_path / "out"),
            "--label", "after", "--run-ref", "RUN-0008"]

    assert card_shots.main([*base, "--chrome", "/other/chrome"]) == 0
    assert seen[-1]["chrome"] == "/other/chrome"
    # 其余三个旋钮也真的透下去了：只钉 `chrome` 的话，一个把别的都扔掉的
    # 实现也会通过。
    assert seen[-1]["label"] == "after" and seen[-1]["run_ref"] == "RUN-0008"
    assert seen[-1]["only"] is None

    assert card_shots.main(base) == 0
    assert seen[-1]["chrome"] == card_shots.Chrome.DEFAULT

    assert card_shots.main([*base, "--only", "alpha,beta"]) == 0
    assert seen[-1]["only"] == {"alpha", "beta"}

    capsys.readouterr()  # 每次调用各印一行汇总，那不是本测试的判据


def test_the_script_entry_names_the_override_in_its_own_help(tmp_path: Path):
    """`python runtime/card_shots.py --help` 里必须出现 `--chrome`。

    这是「覆盖入口」最外面那一层：参数存在，而且一个只跑过一次这个脚本的人
    能看见它。它不启动浏览器 —— `--help` 在参数解析处就退出了，所以这里连
    替身都不需要，也正因如此它可以放心地对着**真的**那份脚本跑。
    """
    proc = subprocess.run(
        [sys.executable, str(Path(card_shots.__file__)), "--help"],
        capture_output=True, text=True, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "--chrome CHROME" in proc.stdout


# --------------------------------------------------------------------------
# 端到端：拼起来之后留下的那份 manifest
# --------------------------------------------------------------------------

def test_a_run_leaves_a_manifest_that_says_which_revision_and_which_input(
        fake_browser, fake_renderer: Path, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch):
    """一张图离开这个目录之后，仍然说得出它是谁拍的、拍的是哪一份输入。

    `renderer.revision` 取自那个假 renderer 自己的 HEAD（一次真的 `git` 调用），
    `input_sha` 是预览页本身的 sha256 —— 两条都是**量**出来的。`framed` 全真，
    因为替身按量到的帧尺寸产出同样尺寸的图：那条等式就是取景成功的证据。
    """
    binary, _ = fake_browser
    monkeypatch.setenv(
        "CARD_SHOTS_FAKE_DOM",
        '<div id="probe">GEOM {"w":320,"h":180,"frames":1}</div></body>')
    out_dir = tmp_path / "out"

    manifest = card_shots.shoot(fake_renderer, out_dir, label="after",
                                run_ref="RUN-0008", chrome=str(binary))

    head = subprocess.run(["git", "-C", str(fake_renderer), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert manifest["renderer"]["revision"] == head
    assert manifest["renderer"]["dirty"] is False
    assert manifest["label"] == "after" and manifest["run_ref"] == "RUN-0008"

    # 一个场景都没拍的话，下面每一条断言都会空过 —— 先把它钉住。
    assert len(manifest["shots"]) == 4, "2 个场景 x 2 个视口"
    assert manifest["unframed"] == []
    assert manifest["shots"][0]["png"]["w"] == 320
    assert manifest["shots"][0]["png"]["h"] == 180
    assert manifest["shots"][0]["png"]["bytes"] > 0
    assert manifest["shots"][0]["frame"]["frames_on_page"] == 1
    for shot in manifest["shots"]:
        assert shot["framed"] is True
        # 每一张都指着**它自己**那一份预览页：按场景算 sha，否则两个场景会被
        # 拿来跟同一个数比，而那条断言在只有一个场景的树上也绿。
        page = out_dir / "previews" / f"{shot['scenario']}.html"
        assert shot["input_sha"] == hashlib.sha256(page.read_bytes()).hexdigest()
    assert len({shot["input_sha"] for shot in manifest["shots"]}) == 2
    # 写在盘上的那一份与返回的是同一份，且带着那句「这些图不是投递证据」。
    written = json.loads((out_dir / "shots.json").read_text(encoding="utf-8"))
    assert written == manifest
    assert "不证明" in written["note"]


def test_a_run_never_writes_into_the_renderer_it_is_shooting(
        fake_browser, fake_renderer: Path, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch):
    """`_render` 是**启动一个子进程**，不是 import，而 renderer 只被读。

    这一条钉的是模块头那段理由的一半：两侧永远不共享解释器状态，而基线那一侧
    是一棵只读的树。可核的形状就是 —— 一次运行之后那个仓库仍然干净，
    `previews/` 落在调用方给的目录里，而不是落到 renderer 里。
    """
    binary, _ = fake_browser
    monkeypatch.setenv(
        "CARD_SHOTS_FAKE_DOM",
        '<div id="probe">GEOM {"w":320,"h":180,"frames":1}</div></body>')
    out_dir = tmp_path / "out"

    manifest = card_shots.shoot(fake_renderer, out_dir, label="before",
                                run_ref="RUN-0008", only={"alpha"},
                                chrome=str(binary))

    assert not (fake_renderer / "previews").exists()
    assert subprocess.run(["git", "-C", str(fake_renderer), "status",
                           "--porcelain"],
                          capture_output=True, text=True).stdout.strip() == ""
    assert (out_dir / "previews" / "alpha.html").is_file()
    # `--only` 真的收窄了拍摄范围（manifest 里只剩一个场景的两个视口）。
    assert [s["scenario"] for s in manifest["shots"]] == ["alpha", "alpha"]
