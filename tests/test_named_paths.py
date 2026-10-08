"""一条规则，四个落点 —— 调用者点名的路径，按名字算，不按 cwd 算。

WHAT THIS FILE IS ABOUT
-----------------------
本产品把这条规则写在它自己的话里：**调用者点名的输出路径必须是绝对路径，否则拒**。
「写进调用者当时站着的目录里的 board，是一个没人知道存在的第二块 board」
（`runtime/cli.py`，board 那一对）。把 `is_absolute()` 这个动作在 `runtime/cli.py`
里数一遍：改前 21 处，改后 23 处——而命令不止这些。

这一包补的正是那两处漏掉的，加上两处**读**侧的对应缺陷：一个读别人写下的名字的动词，
在名字不在时的表现。

四个落点分成两对，每对是「生产者 → 消费者」：

  registration discover --out  →  registration plan --candidates
  memory project   --out       →  memory status --projection-dir

改前：相对路径照单全收，文件落在 cwd；换一个目录再读，读的是**另一个路径**。
`registration plan` 指一个不存在的文档，抛的是 `Path.read_text` 的 traceback——
它说得出 Python 的哪个调用失败了，说不出被找的是哪个路径、也没说怎么放一份在那儿。
`memory status` 更安静也更坏：它把「你点的目录不在」和「这里有个目录但里面没有投影」
答成**同一句话**，而那句话是关于**节点**的——于是点名一个写入者从没写过的路径的读者，
被告诉「本节点没有投影」。那句话从读者站着的地方既不能证实也不能证伪。

WHAT IT IS NOT ABOUT
--------------------
不是关于 projections 或 candidates 的内容。投影怎么构建、候选怎么授权，各有各的文件
（`tests/test_memory_ledger.py`、`tests/test_registration_pipeline.py`），这里只钉住
**路径这一层**：名字从哪来、姓名不成立时说什么。

WHY EVERY TEST STANDS IN ITS OWN DIRECTORY
------------------------------------------
这是台账 M5 那一课，`tests/test_understanding_patch.py` 把它写全了。第一版把
「相对路径被拒」的用例跑在仓库根，于是**删掉守卫的那个变体**就把文件写进了 checkout——
一条失败方式是「在源码树里留垃圾」的判据，在被测属性坏掉时留下证据，而那正是它要防的事。
`monkeypatch.chdir(tmp_path)` 让同一个变体写进一个 pytest 会删掉的目录，断言随后说出的
才是守卫的用途。

`--scope ignore` 在这些用例里不是随手挑的：它按 parser 自己的说明「remember the answer」，
也就是**什么都不写**。这个文件必须一个真实资源都不登记。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registration = _load("serveros_runtime_registration_for_named_paths",
                     ROOT / "runtime" / "registration.py")
cli = _load("serveros_runtime_cli_for_named_paths", ROOT / "runtime" / "cli.py")

EXIT_OK = 0
EXIT_FAULT = 1

OWNER = {"type": "project", "ref": "project:multi-user-claw"}
AT = "2026-09-20T10:00:00Z"


def run(*argv) -> tuple[int, str]:
    """Run the command's own parser, with both output faces captured as one."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = cli.main(list(argv))
        except SystemExit as exit_:
            code = int(exit_.code or 0)
    return code, out.getvalue()


def a_candidates_document(evidence_key: str = "launch:proj:bridge") -> dict:
    """The document `registration discover --out` writes, built without walking
    the host.

    Discover reads what is running on this machine at this instant, so a test
    that called it would read whatever happened to be up — a different
    assertion on a different afternoon. What the reader needs from the document
    is its SHAPE, and the shape is `registration`'s, so it is asked of
    `registration` rather than of the machine.
    """
    candidate = registration.from_launch_record(
        device_ref="device:tokyo-shared-dev-01", kind="mcp-server",
        owner=OWNER, launched_by=OWNER["ref"], evidence_key=evidence_key,
        detail={"command": "bridge"})
    return {"schema": registration.SCHEMA, "at": AT,
            "device_ref": "device:tokyo-shared-dev-01",
            "candidates": [candidate.as_dict()]}


def an_empty_reading() -> dict:
    """What `registration discover` observes, with the host taken out of it.

    `discover` enumerates the containers on this machine, so a test that let it
    do that would be asserting about the Docker daemon: the first version of
    the positive control below passed, and then failed on the next run, because
    the daemon on this host went into `snapshotter.Usage failed` partway
    through the afternoon — a host fault with nothing to do with where a file
    is written. A judging of "the document lands at the path it was given" has
    no business reading a container list, so the list is stubbed and the
    property is what remains.
    """
    return {"observed_at": AT, "unregistered": [], "gap": {},
            "counts": {"registered": 0, "containers_observed": 0,
                       "endpoints_observed": 0, "units_observed": 0,
                       "containers_unregistered": 0,
                       "endpoints_unregistered": 0,
                       "endpoints_with_no_visible_holder": 0}}


# ---------------------------------------------------------------------------
# 第一对 · 生产者：路径不成立时，动手之前就拒
# ---------------------------------------------------------------------------


def test_the_registration_writer_refuses_a_relative_out_and_writes_nothing(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, text = run("registration", "discover", "--out", "rel.json", "--json")

    assert code == EXIT_FAULT, text
    assert "must be absolute, got rel.json" in text
    # 说出**为什么**：这个文档的存在意义就是被另一个动词读回来。
    assert "registration plan --candidates" in text
    assert not (tmp_path / "rel.json").exists(), (
        "拒绝了却还是写了——那拒的是什么")


def test_the_registration_writer_refuses_before_it_walks_the_host(
        tmp_path, monkeypatch):
    """顺序本身是判据，不是实现细节。

    守卫挪到观测之前，是为了让「拒」在读到任何东西之前发生。用爆炸替身而不是计数器
    钉住它：一个从观测之后才拒的实现会在这里停下来，而不是安静地多跑一遍、再通过一次。
    """
    def forbidden(*a, **k):
        raise AssertionError(
            "`registration discover` 走了一遍主机才去拒一个它已经拒绝的 --out。"
            "拒就该在动手之前，理由和路径无关。")

    monkeypatch.setattr(cli, "_observed_without_a_registration", forbidden)
    monkeypatch.chdir(tmp_path)
    code, text = run("registration", "discover", "--out", "rel.json", "--json")
    assert code == EXIT_FAULT, text
    assert "must be absolute" in text


def test_the_registration_writer_writes_an_absolute_out(tmp_path, monkeypatch):
    """正控制。一条只会在坏状态下通过的判据不算判据。

    观测被替成一份空的读数：这一格判的是**文档落在它被给的那个名字下**，
    不是这台机器上恰好跑着什么。
    """
    monkeypatch.setattr(cli, "_observed_without_a_registration",
                        lambda reg: an_empty_reading())
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "abs.json"
    code, text = run("registration", "discover", "--out", str(target), "--json")

    assert code == EXIT_OK, text
    assert target.is_file()
    document = json.loads(target.read_text(encoding="utf-8"))
    assert "candidates" in document and document["device_ref"]


def test_the_projection_writer_refuses_a_relative_out_before_it_reads_the_ledger(
        tmp_path, monkeypatch):
    ledger = tmp_path / "ledger"
    ledger.mkdir()

    def forbidden(*a, **k):
        raise AssertionError(
            "`memory project` 先读了 ledger、审计过、把投影构建出来，才去拒一个它已经"
            "拒绝的 --out。做了全部工作再拒绝说出东西放哪，是把工作本身留在一个刚被"
            "否掉的名字后面。")

    monkeypatch.setattr(cli, "_memory_modules", forbidden)
    monkeypatch.chdir(tmp_path)
    code, text = run("memory", "project", "--ledger-dir", str(ledger),
                     "--out", "relproj", "--json")

    assert code == EXIT_FAULT, text
    assert "must be absolute, got relproj" in text
    assert "memory status" in text  # 说出谁会读它
    assert not (tmp_path / "relproj").exists()


def test_the_projection_writer_writes_an_absolute_out(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    target = tmp_path / "absproj"
    monkeypatch.chdir(tmp_path)
    code, text = run("memory", "project", "--ledger-dir", str(ledger),
                     "--out", str(target), "--json")

    assert code == EXIT_OK, text
    assert (target / "projection.yaml").is_file()
    assert (target / "memory.json").is_file()


# ---------------------------------------------------------------------------
# 第一对 · 消费者：名字不在，说出名字
# ---------------------------------------------------------------------------


def test_the_reader_answers_about_a_document_that_is_not_there(
        tmp_path, monkeypatch):
    """改前这里是 `Path.read_text` 的 traceback：说得出 Python 的哪个调用失败，
    说不出被找的是哪个路径，也没说怎么放一份在那儿。"""
    absent = tmp_path / "absent.json"
    monkeypatch.chdir(tmp_path)
    code, text = run("registration", "plan", "--candidates", str(absent),
                     "--evidence-key", "launch:x", "--scope", "ignore",
                     "--actor", "probe", "--json")

    assert code == EXIT_FAULT, text
    payload = json.loads(text)
    assert payload["code"] == "CANDIDATES_NOT_READABLE"
    assert str(absent) in payload["detail"], "没说出被找的是哪个路径"
    assert "registration discover --out" in payload["detail"], (
        "说得出路径不在，说不出怎么放一份在那儿")
    assert "Traceback" not in text


def test_the_reader_answers_about_a_document_that_is_not_json(
        tmp_path, monkeypatch):
    broken = tmp_path / "broken.json"
    broken.write_text("this is not json at all\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, text = run("registration", "plan", "--candidates", str(broken),
                     "--evidence-key", "launch:x", "--scope", "ignore",
                     "--actor", "probe", "--json")

    assert code == EXIT_FAULT, text
    payload = json.loads(text)
    assert payload["code"] == "CANDIDATES_NOT_READABLE"
    assert str(broken) in payload["detail"]
    assert "not readable JSON" in payload["detail"]
    assert "Traceback" not in text


def test_the_reader_reads_the_document_it_was_given(tmp_path, monkeypatch):
    """正控制：同一个动词，名字成立时照旧工作——而且是从**另一个** cwd 读的。

    这就是整条链存在的理由：写入者把文档放在一个名字下，读的人从别处按同一个名字
    拿回来。相对路径把这条链断在「读的人在哪个目录」上。
    """
    document = tmp_path / "candidates.json"
    document.write_text(json.dumps(a_candidates_document()), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # 站在别处：相对写法在这里必然读不到

    code, text = run("registration", "plan", "--candidates", str(document),
                     "--evidence-key", "launch:proj:bridge",
                     "--scope", "ignore", "--actor", "probe", "--json")

    assert code == EXIT_OK, text
    payload = json.loads(text)
    assert sorted(payload) == ["at", "device_ref", "plan", "schema"]


# ---------------------------------------------------------------------------
# 第二对 · 消费者：路径不在，和「目录在但里面没有投影」是两句话
# ---------------------------------------------------------------------------


def test_a_path_that_is_not_there_is_answered_about_as_a_path(
        tmp_path, monkeypatch):
    """改前这一格答的是「本节点没有投影」——一句关于**节点**的话。

    点名一个写入者从没写过的路径的读者，被告诉了一个关于主机的事实，而那个事实
    从读者站着的地方既不能证实也不能证伪。路径这件事归路径，投影那件事归投影。
    """
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    absent = tmp_path / "never-written"
    monkeypatch.chdir(tmp_path)
    code, text = run("memory", "status", "--projection-dir", str(absent),
                     "--ledger-dir", str(ledger), "--json")

    assert code == EXIT_FAULT, text
    payload = json.loads(text)
    because = payload["because"]
    assert payload["status"] == "MISSING"
    assert because.startswith(str(absent)), (
        "这句话应当以被点名的那个路径开头，因为它说的是那个路径")
    assert "is not a directory" in because
    assert f"memory project --out {absent}" in because, "说得出怎么在那儿写一个"
    assert "nothing was read" in because


def test_a_directory_with_no_projection_in_it_is_still_answered_about_the_projection(
        tmp_path, monkeypatch):
    """反过来的那一半：目录在、里面没有投影，那**是**一句关于投影的话。

    这一格和上一格改前答的是同一句话。改后它们必须分开：把两件事答成一句，
    就是同一张表对同一个东西给出两个相反答案的另一种写法。
    """
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    empty = tmp_path / "directory-with-nothing-in-it"
    empty.mkdir()
    monkeypatch.chdir(tmp_path)
    code, text = run("memory", "status", "--projection-dir", str(empty),
                     "--ledger-dir", str(ledger), "--json")

    assert code == EXIT_FAULT, text
    payload = json.loads(text)
    assert payload["status"] == "MISSING"
    assert "nothing is here" in payload["because"], (
        "目录在，所以这句话是关于投影的，是模块自己那一句")
    assert "is not a directory" not in payload["because"], (
        "目录明明在——这条分支不该开")


def test_the_reader_reads_the_projection_that_is_there(tmp_path, monkeypatch):
    """正控制，也是整条链的闭合：写入者把投影放在一个名字下，读的人按同一个名字
    从**另一个** cwd 拿回来说它是 CURRENT。"""
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    target = tmp_path / "absproj"
    monkeypatch.chdir(tmp_path)
    code, text = run("memory", "project", "--ledger-dir", str(ledger),
                     "--out", str(target), "--json")
    assert code == EXIT_OK, text

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    code, text = run("memory", "status", "--projection-dir", str(target),
                     "--ledger-dir", str(ledger), "--json")

    assert code == EXIT_OK, text
    payload = json.loads(text)
    assert payload["status"] == "CURRENT"
    assert payload["directory"] == str(target)


def test_the_human_readable_face_says_the_same_thing(tmp_path, monkeypatch):
    """两条输出面说的是同一件事。`--json` 那一面有上面的用例钉着，
    这一条只是不让给人读的那一面在后面悄悄走散。"""
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    absent = tmp_path / "never-written"
    monkeypatch.chdir(tmp_path)
    code, text = run("memory", "status", "--projection-dir", str(absent),
                     "--ledger-dir", str(ledger))

    assert code == EXIT_FAULT, text
    assert f"projection  {absent}" in text
    assert "status      MISSING" in text
    assert "is not a directory" in text
