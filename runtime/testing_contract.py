"""Project Testing Contract 的装载、解析与漂移检测。

**这个模块回答的问题**：「测这个项目，用什么样的环境、跑到什么程度、哪些值
可以改、改了要不要说。」它不建环境（那是 test-management），不跑场景
（那是 automated-testing），不展示（那是 report）。

四层，下层不得突破上层的 constraint：

    L0 全局测试政策    `testing/_policy.yaml`       硬不变量，不认识任何项目
    L1 可复用档位      test-management 的 payload   怎么把环境建出来
    L2 项目测试契约    `testing/<project>.yaml`     这个项目需要怎样的测试形态
    L3 本次解析        Item / Run 的 override       这一次用哪些值

**两根轴不要混。** `mutability` 决定**谁有权改**，`criticality` 决定**改了要不要
留痕**。一个 acceptance-critical 但 run-override 的旋钮是可以被一次 Run 改的，
只是改完要在记录里说清楚；一个 fixed 的 informational 旋钮谁都不能改，
但它改与不改没人关心。

**为什么 revision 是算出来的而不是写下来的**：一个作者手写的版本号会在第一次
编辑之后变成谎话，而 TestRun 会把它当事实抄走。所以这里对文件内容算 sha256 ——
它不可能与内容不一致。

**这里不发明默认值。** 契约里没有的旋钮不出现，而不是补一个「合理」的数字。
一个凭空补齐的默认值，会让一份没人做过的决定看起来像是项目本来就有的。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the runtime always ships with PyYAML
    yaml = None

SCHEMA = "serveros/project-testing/v1"
POLICY_SCHEMA = "serveros/testing-policy/v1"
SCHEMA_CONTRACT = "serveros/schema-contract.v1"

# 契约与政策在控制面下的哪个目录由 `Roots.testing_dir` / `Roots.schema_dir`
# 回答 —— 那份布局只有一处定义，本模块不再抄第二份。
POLICY_FILE = "_policy.yaml"

# L1 的档位表归 test-management。定位方式沿用 `abilities/taxonomy.py` 的
# `_default_ability_repo()`：部署用 `SERVEROS_ABILITY_REPO` 点名，源码检出
# 退回兄弟目录。「读哪个检出」是部署的事实，不是代码的事实。
ABILITY_REPO_ENV = "SERVEROS_ABILITY_REPO"
L1_ABILITY_DIR = "skills/test-management"
L1_PROFILE_NAME = "profiles.yaml"

# mutability 的权限梯子。右边包含左边：`run-override` 允许的层包含
# `item-override` 允许的层。所以一个标成 item-override 的旋钮，
# 单次 Run 是改不动的 —— 那正是它被标成 item-override 而不是 run-override 的意思。
LAYERS = ("project-default", "item-override", "run-override")
MUTABILITY_RANK = {"fixed": -1, "project-default": 0, "item-override": 1, "run-override": 2}


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_yaml(path: Path):
    """读一个 YAML 文档。读不到返回 (None, 原因) —— 不返回空字典。

    「空文档」与「读不到」在这里必须分得开：前者是一个可以被判断的事实，
    后者是没法判断。把它们都变成 `{}`，下游就会把「读不到」当成「没声明」。
    """
    if yaml is None:
        return None, "PyYAML 不可用"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    try:
        return yaml.safe_load(raw), None
    except yaml.YAMLError as exc:
        return None, f"YAML 解析失败: {exc}"


# ----------------------------------------------------------------- 定位

def testing_dir(roots) -> Path:
    return Path(roots.testing_dir)


def policy_path(roots) -> Path:
    return testing_dir(roots) / POLICY_FILE


def contract_path(roots, project: str) -> Path:
    return testing_dir(roots) / f"{project}.yaml"


def discover_projects(roots) -> list[str]:
    """测试目录里有哪些项目契约。下划线开头的是政策这类共享件，不是项目。"""
    directory = testing_dir(roots)
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml") if not p.name.startswith("_"))


def schema_contract_path(roots) -> Path:
    return Path(roots.schema_dir) / "project-testing.schema.yaml"


def ability_repo(roots=None) -> Path | None:
    """L1 档位表所在的检出。部署点名优先，源码检出退兄弟目录。"""
    import os

    configured = os.environ.get(ABILITY_REPO_ENV)
    if configured:
        return Path(configured)
    if roots is not None:
        sibling = Path(roots.workspace_root) / "src" / "ability-management"
        if (sibling / ".git").exists():
            return sibling
    return None


def ability_payload_dir(repo: Path) -> Path:
    """一个检出里 test-management 的 payload 根。两种布局都合法，都要认。

    仓库里 payload 在 `skills/<name>/payload/` 下；而**装出来的** composition 里，
    payload 的内容直接落在 `skills/<name>/` —— `_copy_payload` 部署的是 payload
    的**内容**，不搬那一层目录。判据与 registry 的 `_payload_root` 同规则：
    有 `payload/` 子目录就用它，否则用目录本身。

    一个只认其中一种的读法，在另一种上会报「文件不存在」—— 而那个报错听起来
    像这份文件真的没有，不像读法少了一种。开发时命中仓库布局、装出来之后命中
    部署布局，于是这种错会恰好在装完之后才出现。
    """
    skill = repo / L1_ABILITY_DIR
    candidate = skill / "payload"
    return candidate if candidate.is_dir() else skill


# ----------------------------------------------------------------- 装载

class Loaded:
    """一个装载结果。`declaration` 为 None 时 `reason` 说明为什么 ——
    调用方必须处理这两种情况之一，不能把 reason 丢掉当成空文档。"""

    def __init__(self, path: Path, declaration, revision: str | None, reason: str | None):
        self.path = path
        self.declaration = declaration
        self.revision = revision
        self.reason = reason

    @property
    def ok(self) -> bool:
        return self.declaration is not None

    def as_dict(self) -> dict:
        return {"path": str(self.path), "revision": self.revision,
                "loaded": self.ok, "reason": self.reason}


def _load(path: Path, expected_schema: str) -> Loaded:
    doc, reason = _read_yaml(path)
    if doc is None:
        return Loaded(path, None, None, reason)
    try:
        revision = _sha256_text(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return Loaded(path, None, None, f"读到内容却算不出 revision: {exc}")
    if not isinstance(doc, dict):
        return Loaded(path, None, revision, "顶层不是映射")
    declared = doc.get("schema")
    if declared != expected_schema:
        return Loaded(path, None, revision,
                      f"schema 是 {declared!r}，期望 {expected_schema!r}")
    return Loaded(path, doc, revision, None)


def load_policy(roots) -> Loaded:
    return _load(policy_path(roots), POLICY_SCHEMA)


def load_contract(roots, project: str) -> Loaded:
    return _load(contract_path(roots, project), SCHEMA)


def load_schema_contract(roots) -> Loaded:
    return _load(schema_contract_path(roots), SCHEMA_CONTRACT)


def load_profiles(roots) -> Loaded:
    repo = ability_repo(roots)
    if repo is None:
        return Loaded(Path(L1_ABILITY_DIR) / L1_PROFILE_NAME, None, None,
                      f"没有点名牌位表所在的检出（{ABILITY_REPO_ENV} 未设置，"
                      f"且兄弟检出不存在）：L1 无法判定")
    return _load(ability_payload_dir(repo) / L1_PROFILE_NAME,
                 "serveros/reusable-test-profiles/v1")


INPUT_UNREADABLE = "TEST_INPUT_UNREADABLE"
INPUT_UNJUDGED = "TEST_INPUT_UNJUDGED"

# `observe()` 说得出「为什么没判成」，但只有一部分「没判成」是故障：
# 契约声明了某一套而它不在，是故障；契约什么都没声明，是这件事还没发生。
UNJUDGED_IS_A_FAULT = frozenset({"root-missing", "named-stack-missing"})


def unreadable_inputs(*named: tuple[str, Loaded]) -> list[dict]:
    """每个输入先自证「我读到了」，再让下游拿它去比。

    少了这一步，闸门在输入缺席时给的绿与核对通过时给的绿是**同一个绿**：
    `profile_drift(None)` 返回 `[]`，`drift(contract, {})` 也返回 `[]`，
    空列表在下游长得和「核对过、一致」一模一样。同族的最短形态是
    `[ "" = "" ]` —— 两次查询都失败，比较的结果是相等。

    `Loaded` 自己的 docstring 已经写了「调用方必须处理这两种情况之一，
    不能把 reason 丢掉当成空文档」；这里就是那个处理。
    """
    findings: list[dict] = []
    for label, loaded in named:
        if loaded.ok:
            continue
        findings.append({
            "code": INPUT_UNREADABLE,
            "input": label,
            "path": str(loaded.path),
            "message": (f"{label} 读不出来（{loaded.reason}），因此它**没有参与**这次核对 —— "
                        f"这一项的绿是「没比」，不是「比过了」。位置：{loaded.path}"),
        })
    return findings


def unjudged_input(observed: dict) -> list[dict]:
    """契约声明了某一套环境、却没有一套被判决成 —— 那不是「没问题」，是「没比」。

    `observe()` 早就把原因写进 `not_observed` 了，但 `drift()` 在 `graded` 为空时
    直接返回 `[]`，于是那段理由没有任何读者，闸门照旧是绿的。
    """
    reason = observed.get("graded_absent")
    if reason not in UNJUDGED_IS_A_FAULT:
        return []
    return [{
        "code": INPUT_UNJUDGED,
        "input": "L2 环境定义",
        "reason": reason,
        "message": "；".join(observed.get("not_observed") or []) or
                   f"契约点名要判决的那一套没有被观测到（{reason}）",
    }]


def vocabulary(schema_contract: Loaded) -> dict:
    """从 schema 声明里取合法取值。**不在本模块里再抄一份词表** ——
    抄出来的第二份会先漂移，而这份文件存在的理由就是不让同一套词有两个定义。"""
    doc = schema_contract.declaration or {}
    knobs = doc.get("knobs") or {}
    capabilities = doc.get("environment_capabilities") or {}
    tiers = doc.get("evidence_tiers") or {}
    return {
        "mutability": list(knobs.get("mutability") or ()),
        "criticality": list(knobs.get("criticality") or ()),
        "violations": list((doc.get("violations") or {})),
        "required_metadata": list(knobs.get("required_metadata") or ()),
        "resolution_order": list(doc.get("resolution_order") or ()),
        "layer_sources": ["reusable-profile"] + list(LAYERS),
        # 环境能力的闭词表。L1 档位声称自己提供的能力必须在这里面 ——
        # 一个词表外的能力不是「更强」，是「没法核对」。
        "capabilities": list((capabilities.get("known") or {})),
        "evidence_tiers": list(tiers.get("known") or ()),
        "change_classes": list((doc.get("default_tiers") or {}).get("required") or ()),
    }


# ----------------------------------------------------------------- 接缝
#
# 三方在这一段合上，任何一方都不能替另一方回答：
#
#   automated-testing   这一次要证明到哪一级（E0–E4）
#   项目契约（L2）       那一级、那一个面，要求环境具备哪些**能力**
#   L1 档位             我能提供哪些能力
#
# 于是「该建成什么形状」只有一处权威，「怎么把它建出来」也只有一处。
# 从前是契约点名档位、档位自带 min_tier —— 两份文件各自成立，组合出来的
# 答案却可以是错的，而且没有任何东西会响。

FLOOR_NAME = "containerized_floor"
REAL_CHAIN = "real-chain"
MATCH_RULE = "满足要求的前提下取能力最少的一档；并列时按名字升序"


def _as_list(value) -> list:
    """声明里的一个值或一串值。**字符串不是一串字符** —— 把 `real-chain`
    拆成 10 个单字符能力，匹配会全绿而什么都没核对。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def capability_requirements(contract: dict, *, tier: str | None = None,
                            surfaces=()) -> dict[str, list[str]]:
    """这一次的要求集：能力 → 谁在要求它。

    留名是刻意的。「为什么这次需要 redis」必须能被回答成「E3 要求它」或
    「distributed_state 面要求它」，而不是「环境里本来就有」—— 后者无法反驳，
    也无法在需求消失时把环境收回去。
    """
    out: dict[str, list[str]] = {}

    def add(caps, source: str) -> None:
        for capability in _as_list(caps):
            out.setdefault(capability, [])
            if source not in out[capability]:
                out[capability].append(source)

    if tier:
        decl = (contract.get("evidence_tiers") or {}).get(tier)
        if isinstance(decl, dict):
            add(decl.get("requires"), f"tier:{tier}")
    for name in surfaces:
        decl = (contract.get("test_surfaces") or {}).get(name)
        if isinstance(decl, dict):
            add(decl.get("requires"), f"surface:{name}")
    return out


def floor_capabilities(contract: dict) -> list[str]:
    """容器化地板。它约束的是**候选档位**，不是这一次的要求集 ——
    一个不建容器的档位（unit）不需要满足地板，因为它没有那个形状。"""
    return _as_list((contract.get(FLOOR_NAME) or {}).get("requires"))


def _profile_capabilities(decl: dict) -> list[str]:
    return _as_list(decl.get("capabilities"))


def profile_legality(decl: dict, floor: list[str]) -> list[str]:
    """一个档位自身合法吗：容器化的档位必须满足地板。

    返回它缺的能力。空的档位（不提供 real-chain）不受地板约束。
    """
    caps = _profile_capabilities(decl)
    if REAL_CHAIN not in caps:
        return []
    return [cap for cap in floor if cap not in caps]


def select_profile(contract: dict, profiles: dict, *, required, project: str) -> dict:
    """在 L1 里选出满足要求的那一档，并留下落选者的名字与缺项。

    落选者要留名，是因为「没有环境能满足 E4」这个结论必须能被人核对：
    一句话的拒绝无法反驳，一张写着每一档缺什么的表可以。
    """
    floor = floor_capabilities(contract)
    known = (profiles or {}).get("profiles") or {}
    candidates, rejected = [], []
    for name in sorted(known):
        decl = known.get(name) or {}
        if not isinstance(decl, dict):
            continue
        declared_project = decl.get("project")
        if declared_project and declared_project != project:
            rejected.append({"profile": name, "missing": [],
                             "why": f"绑在项目 {declared_project!r} 上"})
            continue
        caps = _profile_capabilities(decl)
        missing = [cap for cap in required if cap not in caps]
        illegal = profile_legality(decl, floor)
        if missing:
            rejected.append({"profile": name, "missing": sorted(missing),
                             "why": "缺少这次要求的能力"})
            continue
        if illegal or decl.get("narrowing"):
            # 窄档不参与匹配：它不是任何要求集的最小覆盖，只能被显式点名。
            rejected.append({"profile": name, "missing": sorted(illegal),
                             "why": "不满足容器化地板，只能被显式点名使用"})
            continue
        candidates.append({"profile": name, "capabilities": caps})

    candidates.sort(key=lambda item: (len(item["capabilities"]), item["profile"]))
    chosen = candidates[0] if candidates else None
    return {"chosen": chosen, "candidates": candidates, "rejected": rejected,
            "floor": floor, "rule": MATCH_RULE}


# ----------------------------------------------------------------- 解析

class Violation:
    def __init__(self, code: str, message: str, *, knob: str | None = None,
                 layer: str | None = None, detail: dict | None = None):
        self.code = code
        self.message = message
        self.knob = knob
        self.layer = layer
        self.detail = detail or {}

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.knob is not None:
            out["knob"] = self.knob
        if self.layer is not None:
            out["layer"] = self.layer
        if self.detail:
            out["detail"] = self.detail
        return out


def _ceiling_for(knob_name: str, contract: dict) -> tuple[str, int] | None:
    """`<x>_bytes` 这个旋钮的 ceiling 是 `<x>_hard_max_bytes`。

    命名即声明：一个上限如果不能用名字认出它管的是哪个旋钮，
    它就不是一个能被机械执行的上限，只是一句散文。
    """
    limits = contract.get("resource_limits") or {}
    base = knob_name[:-6] if knob_name.endswith("_bytes") else knob_name
    key = f"{base}_hard_max_bytes"
    value = limits.get(key)
    if isinstance(value, int):
        return key, value
    return None


def _check_layer_permission(knob_name, spec, layer, vocab, violations):
    """这一层有没有权改。`fixed` 拒所有层；`project-default` 拒 Item 与 Run。

    两份权威，别混：**合法取值**由 schema 声明回答（那份文件是词表的唯一所在），
    **权限梯子**由本模块回答（那是解析语义）。两者不一致时是两个不同的结论：
    「这个契约不合法」与「这个契约合法，但本解析器没有执行它的规则」。
    """
    mutability = spec.get("mutability")
    declared = (vocab or {}).get("mutability") or list(MUTABILITY_RANK)
    if mutability not in declared:
        violations.append(Violation(
            "INVALID_OVERRIDE",
            f"{knob_name} 的 mutability 是 {mutability!r}，不在声明允许的 "
            f"{list(declared)} 里：契约本身不合法",
            knob=knob_name, layer="project-default"))
        return False
    if mutability not in MUTABILITY_RANK:
        violations.append(Violation(
            "REFUSED",
            f"{knob_name} 的 mutability 是声明里的 {mutability!r}，但这个解析器没有"
            f"它的权限规则：契约合法，执行不了，因此不产出有效环境",
            knob=knob_name, layer=layer,
            detail={"declared": sorted(declared), "executable": sorted(MUTABILITY_RANK)}))
        return False
    rank = MUTABILITY_RANK[mutability]
    needed = MUTABILITY_RANK[layer]
    if rank < needed:
        code = "REFUSED" if mutability == "fixed" else "INVALID_OVERRIDE"
        violations.append(Violation(
            code,
            f"{knob_name} 标为 {mutability}，{layer} 无权改它"
            + ("（fixed 的旋钮改它等于换一个契约版本）" if mutability == "fixed" else ""),
            knob=knob_name, layer=layer,
            detail={"mutability": mutability, "required": layer}))
        return False
    return True


def _check_value(knob_name, spec, value, layer, violations):
    """值本身的合法性：下界是硬的，上界是建议。"""
    minimum = spec.get("minimum")
    if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < minimum:
            violations.append(Violation(
                "INVALID_OVERRIDE",
                f"{knob_name} = {value} 低于下界 {minimum}",
                knob=knob_name, layer=layer,
                detail={"value": value, "minimum": minimum}))
            return False
    return True


def _check_reason(knob_name, spec, value, layer, reason, violations):
    """放宽要理由。临界值越高，理由要得越狠 —— 这是 criticality 的作用。"""
    default = spec.get("default")
    lowered = False
    if isinstance(value, bool) and isinstance(default, bool):
        lowered = default is True and value is False
    elif isinstance(value, (int, float)) and isinstance(default, (int, float)) \
            and not isinstance(value, bool) and not isinstance(default, bool):
        lowered = value < default
    elif isinstance(value, str) and isinstance(default, str):
        lowered = value != default

    criticality = spec.get("criticality")
    needs_reason = bool(spec.get("lowering_requires_reason")) and lowered
    if spec.get("disable_requires_reason") and isinstance(value, bool) and value is False:
        needs_reason = True
    if criticality in ("acceptance-critical", "safety-critical") and lowered:
        needs_reason = True

    if needs_reason and not (reason or "").strip():
        violations.append(Violation(
            "MISSING_REASON",
            f"{knob_name} 从默认 {default!r} 改成 {value!r}"
            + ("（关了它）" if isinstance(value, bool) and value is False else "（降低了）")
            + f"，criticality={criticality}，必须给出理由",
            knob=knob_name, layer=layer,
            detail={"default": default, "value": value, "criticality": criticality}))
        return False
    return True


def _check_ceiling(knob_name, value, contract, layer, violations):
    ceiling = _ceiling_for(knob_name, contract)
    if ceiling is None or not isinstance(value, int) or isinstance(value, bool):
        return True
    key, cap = ceiling
    if value > cap:
        violations.append(Violation(
            "INVALID_OVERRIDE",
            f"{knob_name} = {value} 越过硬上限 {key} = {cap}；"
            f"硬上限没有「给出理由就放行」这一档",
            knob=knob_name, layer=layer,
            detail={"value": value, key: cap}))
        return False
    return True


def resolve(contract: dict, policy: dict | None = None, profiles: dict | None = None,
            *, project: str | None = None, item: str | None = None,
            run_id: str | None = None, profile_name: str | None = None,
            item_overrides: dict | None = None,
            run_overrides: dict | None = None,
            vocab: dict | None = None,
            tier: str | None = None, change_class: str | None = None,
            surfaces=(), chosen_because: str | None = None,
            verification: str | None = None) -> dict:
    """把四层压成「这一次到底用什么」。

    返回的每一个值都带 `source` 与 `declared_by`，所以「这个数是从哪来的」
    不需要问人，也不靠调用方记。有 violation 时 `resolved` 为 False，
    **不产出 effective 环境** —— 越界的配置不是一个可以带着跑的配置。

    环境档位由能力匹配选出（除非调用方显式点名）。调用方点名一个**不满足
    容器化地板**的档位，就是一次窄档使用：必须给出 `chosen_because`，
    且不得同时声称任何证据级别（那是「我证明了什么」，而窄档的射程里没有
    分布式），除非那一级本身不要求环境（E0–E2）。
    """
    item_overrides = item_overrides or {}
    run_overrides = run_overrides or {}
    surfaces = [s for s in _as_list(surfaces) if s]
    violations: list[Violation] = []

    knobs = contract.get("knobs") or {}
    if not knobs:
        violations.append(Violation(
            "REFUSED", "契约里一个旋钮都没有：没有可解析的默认值"))

    declared_by = f"{project or contract.get('project')}/testing-contract"
    values: dict[str, dict] = {}
    this_project = project or contract.get("project")

    # ---------------------------------------------------------- 接缝
    # 这一次要证明到哪一级。调用方点名优先；没说就以变化类别的下限为准；
    # 连变化类别都没说时用 bugfix 那一档 —— 与「谁都不说就按普通缺陷办」一致。
    tiers = contract.get("evidence_tiers") or {}
    default_tiers = contract.get("default_tiers") or {}
    known_classes = (vocab or {}).get("change_classes") or ()
    if change_class and change_class not in default_tiers:
        # 词表（schema）说的是「这些类别必须有下限」，契约说的是「我这个项目的
        # 下限是什么」。两者都只回答一半：一个类别在词表里而在契约里没有下限，
        # 与一个类别谁都没定义过，在这里是同一个答案 —— 没有下限可继承。
        violations.append(Violation(
            "REFUSED", f"契约 `default_tiers` 里没有变化类别 {change_class!r}"
                       f"（声明了 {sorted(default_tiers)}，词表要求至少 "
                       f"{list(known_classes)}）：缺哪一类，那一类就没有下限可继承",
            detail={"change_class": change_class, "declared": sorted(default_tiers)}))

    class_ = (change_class if change_class in default_tiers
              else (None if profile_name else "bugfix"))
    effective_tier = tier or (default_tiers.get(class_) if class_ else None)
    tier_source = ("named" if tier else
                   (f"contract-default:{class_}" if class_ and effective_tier else None))

    known_tiers = (vocab or {}).get("evidence_tiers") or []
    tier_unknown = False
    if tier and known_tiers and tier not in known_tiers:
        violations.append(Violation(
            "REFUSED", f"证据级别 {tier!r} 不在词表 {known_tiers} 里："
                       f"一个没人定义过的级别不能被当成要求集",
            detail={"tier": tier, "known": known_tiers}))
        effective_tier = None
        tier_unknown = True
    elif effective_tier and effective_tier not in tiers:
        # 契约声明了级别名却从没回答过它要求什么。把「没被回答」当成
        # 「不需要任何能力」，正是这条接缝要消灭的那类静默。
        violations.append(Violation(
            "REFUSED", f"证据级别 {effective_tier!r} 在契约的 `evidence_tiers` 里没有被"
                       f"映射过：这一级要求什么环境，这个契约没有回答",
            detail={"tier": effective_tier, "mapped": sorted(tiers)}))
        effective_tier = None
        tier_unknown = True

    for name in surfaces:
        if name not in (contract.get("test_surfaces") or {}):
            violations.append(Violation(
                "REFUSED", f"测试面 {name!r} 不在这个契约的 `test_surfaces` 里："
                           f"它需要什么环境无从判断",
                detail={"surface": name}))
    surfaces = [s for s in surfaces if s in (contract.get("test_surfaces") or {})]

    required = capability_requirements(contract, tier=effective_tier, surfaces=surfaces)
    floor = floor_capabilities(contract)
    profiles_known = ((profiles or {}).get("profiles") or {})
    # 级别没被回答时**不做能力核对**，理由与「档位表读不出来」是同一条：
    # 要求集是不完整的（缺了那一级该提的部分），拿它去比会得到一个
    # 「够」——而那个「够」正是把「没被回答」读成「不需要」的第二种形态。
    # 也不选档位：选出来的那个名字会看起来像一次已经做出的选择。
    can_judge = bool(profiles_known) and not tier_unknown

    selection = (select_profile(contract, profiles, required=required,
                                project=this_project) if can_judge else None)

    if profile_name:
        effective_profile = profile_name
        profile_source = "named"
    elif selection and selection["chosen"]:
        effective_profile = selection["chosen"]["profile"]
        profile_source = "matched"
    else:
        effective_profile = None
        profile_source = None

    narrowing, waived = False, []
    if can_judge:
        if profile_name:
            declared = profiles_known.get(profile_name)
            if not isinstance(declared, dict):
                violations.append(Violation(
                    "REFUSED",
                    f"点名了档位 {profile_name!r}，但它在 L1 里没有定义："
                    f"这一档能提供哪些能力无从核对",
                    layer="reusable-profile",
                    detail={"profile": profile_name,
                            "known": sorted(profiles_known)}))
            else:
                caps = _profile_capabilities(declared)
                missing = sorted(cap for cap in required if cap not in caps)
                waived = sorted(profile_legality(declared, floor))
                if missing:
                    # 显式点名也越不过「这一次要证明什么」。窄档能免掉的是地板，
                    # 不是那一级本身的要求 —— 否则窄档就成了绕过验收的入口。
                    violations.append(Violation(
                        "CAPABILITY_UNMET",
                        f"档位 {profile_name} 不具备这次要求的能力 {missing}："
                        f"缺少的能力决定这一次证明不到那么多，理由不能把它补上",
                        layer="reusable-profile",
                        detail={"profile": profile_name, "missing": missing,
                                "required_by": {cap: required[cap] for cap in missing},
                                "capabilities": caps}))
                elif waived or declared.get("narrowing"):
                    narrowing = True
                    if not chosen_because:
                        violations.append(Violation(
                            "MISSING_REASON",
                            f"档位 {profile_name} 不满足容器化地板（缺 {waived}），"
                            f"这是一次窄档使用，必须给出本次的 chosen_because",
                            layer="reusable-profile",
                            detail={"profile": profile_name, "waived": waived}))
        elif selection and not selection["chosen"]:
            violations.append(Violation(
                "CAPABILITY_UNMET",
                f"没有任何 L1 档位满足这次的要求 {sorted(required)}："
                f"这个项目的环境里没有能证明到这一级的形态",
                detail={"required": {cap: required[cap] for cap in sorted(required)},
                        "candidates": selection["rejected"],
                        "floor": floor}))

    known_profiles = profiles_known

    # 定了名就要按它解析。调用方没点名时，生效的是契约的默认档 ——
    # 如果只有「被点名」才应用钉值，那么 `resolve` 与
    # `resolve --profile <同一个名字>` 会给出不同的值，却报同一个档位名。
    profile_decl = known_profiles.get(effective_profile) or {}
    pins = profile_decl.get("knobs") or {}
    if not isinstance(pins, dict):
        pins = {}
    # 档位可以声明它绑在哪个项目上。一个绑在别处的档位被用在这里，两边描述的
    # 不是同一个环境，而结果看起来和成立的时候一模一样。
    profile_project = profile_decl.get("project")
    if profile_project and this_project and profile_project != this_project:
        violations.append(Violation(
            "REFUSED",
            f"档位 {effective_profile} 声明绑在项目 {profile_project!r} 上，"
            f"这次解析的是 {this_project!r}：这一档描述的环境不是这个项目的",
            layer="reusable-profile",
            detail={"profile": effective_profile, "profile_project": profile_project,
                    "project": this_project}))
    # 钉了一个契约里没有的旋钮，是一个**什么都没发生**的声明：档位描述的
    # 环境形态与契约描述的旋钮表不在说同一件事，而解析结果看不出来。
    stray = sorted(set(pins) - set(knobs))
    if stray:
        violations.append(Violation(
            "REFUSED",
            f"档位 {effective_profile} 钉了契约里没有的旋钮 {stray}："
            f"这些钉值不会影响任何值，因此这一次解析出来的环境不是档位描述的那一个",
            layer="reusable-profile",
            detail={"profile": effective_profile, "unknown_knobs": stray}))

    for name, spec in knobs.items():
        if not isinstance(spec, dict):
            violations.append(Violation(
                "REFUSED", f"{name} 不是一个带元数据的旋钮", knob=name))
            continue
        entry = {
            "value": spec.get("default"),
            "source": "project-default",
            "declared_by": declared_by,
            "mutability": spec.get("mutability"),
            "criticality": spec.get("criticality"),
            "reason": spec.get("reason"),
        }
        for extra in ("minimum", "recommended_max", "evidence_when_changed"):
            if spec.get(extra) is not None:
                entry[extra] = spec[extra]

        # L1 档位先于项目契约。档位是「这个环境形态本身」的约束，
        # 项目契约是在那个形态里做选择。
        #
        # 硬边界在**每一层**都是硬的。档位可以收窄（那是它存在的理由），
        # 但一个能被某个档位越过的硬上限不是上限，只是一句在多数情况下成立的
        # 陈述 —— 而 `resource_limits` 里的那条是共享主机上的资源承诺。
        #
        # 理由也一并在这一层要：档位是被评审过的文件，但那不等于它可以**沉默地**
        # 把一个 acceptance-critical 的旋钮降下来。评审的产物是这个文件，所以
        # 「为什么这一次要降」要写在这个文件里，而不是留在评审者的记忆里。
        if pins:
            pin = pins.get(name)
            if isinstance(pin, dict) and "value" in pin:
                pinned = pin["value"]
                if _check_value(name, spec, pinned, "reusable-profile", violations) \
                        and _check_ceiling(name, pinned, contract, "reusable-profile", violations) \
                        and _check_reason(name, spec, pinned, "reusable-profile",
                                          pin.get("reason"), violations):
                    entry["value"] = pinned
                    entry["source"] = "reusable-profile"
                    entry["declared_by"] = f"test-management/{effective_profile}"

        for layer, overrides in (("item-override", item_overrides),
                                 ("run-override", run_overrides)):
            if name not in overrides:
                continue
            payload = overrides[name]
            if isinstance(payload, dict):
                new_value, reason = payload.get("value"), payload.get("reason")
            else:
                new_value, reason = payload, None
            if not _check_layer_permission(name, spec, layer, vocab or {}, violations):
                continue
            if not _check_value(name, spec, new_value, layer, violations):
                continue
            if not _check_ceiling(name, new_value, contract, layer, violations):
                continue
            if not _check_reason(name, spec, new_value, layer, reason, violations):
                continue
            entry["value"] = new_value
            entry["source"] = layer
            # 这一层真的改了值，所以「理由」和「高于建议上限」这两个判断也归它。
            # 留着上一层的理由，会让一条理由挂在一个不是它解释的值上。
            entry.pop("override_reason", None)
            entry.pop("above_recommended_max", None)
            # 没点名 Item 也没点名 Run 时，这一层是匿名的。写一个与 source
            # 相同的字符串会让「谁改的」看起来已回答，而它恰恰没有。
            # 谁决定的问这一层是谁 —— 一个 Item 的 override 与一次 Run 的
            # override 都传了的时候，答案是「谁改的」而不是「谁在场」。
            decider = item if layer == "item-override" else run_id
            entry["declared_by"] = decider or None
            if reason:
                entry["override_reason"] = reason
            recommended_max = spec.get("recommended_max")
            if isinstance(new_value, (int, float)) and isinstance(recommended_max, (int, float)) \
                    and not isinstance(new_value, bool) and new_value > recommended_max:
                entry["above_recommended_max"] = True
        values[name] = entry

    # 整栈预算：逐服务之和不能超过硬上限。一个和上限相当的单服务值等于没有预算。
    limits = contract.get("resource_limits") or {}
    budget_key = next((k for k in limits if k.endswith("_hard_max_bytes")), None)
    budget = limits.get(budget_key) if budget_key else None

    constraints = [{"id": inv.get("id"), "title": inv.get("title"),
                    "rule": inv.get("rule"), "enforced_by": inv.get("enforced_by")}
                   for inv in ((policy or {}).get("invariants") or [])]

    capability_check = ("not-evaluated" if not can_judge else
                        ("narrowed" if narrowing else
                         ("ok" if not any(v.code == "CAPABILITY_UNMET"
                                          for v in violations) else "unmet")))
    capability_check_reason = None
    if capability_check == "not-evaluated":
        capability_check_reason = "tier-unanswered" if tier_unknown else "no-profiles"

    result = {
        "project": project or contract.get("project"),
        "item": item,
        "run_id": run_id,
        "profile": effective_profile,
        "profile_known": effective_profile in known_profiles,
        "profile_source": profile_source,
        "values": values,
        "constraints": constraints,
        # 这一次要证明什么、因此要求环境具备什么、选中的档位提供了什么。
        # 三者分开放，是因为它们的**权威**不同：级别来自 automated-testing，
        # 要求来自项目契约，提供的能力来自 test-management。合成一句话，
        # 就没法回答「这一项是谁提的要求」。
        "verification": {
            "profile": verification,
            "evidence_tier": effective_tier,
            "tier_source": tier_source,
            "change_class": class_,
            "decided_by": "automated-testing" if (verification or tier) else None,
        },
        "requirement": {
            "required": [{"capability": cap, "required_by": required[cap]}
                         for cap in sorted(required)],
            "floor": floor,
            "floor_source": FLOOR_NAME,
            "surfaces": list(surfaces),
        },
        "environment_profile": {
            "name": effective_profile,
            "source": profile_source,
            "capabilities": _profile_capabilities(profile_decl),
            "narrowing": narrowing,
            "waived": waived,
            "chosen_because": chosen_because if narrowing else None,
            "rule": MATCH_RULE,
            "candidates": (selection or {}).get("candidates") or [],
            "rejected": (selection or {}).get("rejected") or [],
        },
        # 「能力够不够」与「档位表读没读到」是两件事。读不到时答案是
        # 「无从核对」，不是「够」—— 把后者当前者，缺失的档位表会静默地
        # 让每一次解析都通过。
        "capability_check": capability_check,
        # 「没核对」也有两种原因，它们不能长得一样：档位表读不出来，
        # 与这一次的要求集本身不完整。两者的处置完全不同（去修档位表 /
        # 去把那一级的映射写下来），而 `not-evaluated` 这个词自己分不出来。
        "capability_check_reason": capability_check_reason,
        "violations": [v.as_dict() for v in violations],
        "resolved": not violations,
    }
    if budget_key:
        result["budget"] = {"ceiling_knob": budget_key, "ceiling": budget,
                            "ceiling_source": "project-contract"}
    return result


# ----------------------------------------------------------------- 漂移

def _iter_definition_files(root: Path):
    """环境定义文件。只认这些后缀，且跳过明显的产物目录。"""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".yaml", ".yml"):
            continue
        if any(part in ("__pycache__", "node_modules", ".git") for part in path.parts):
            continue
        yield path


def _mem_bytes(text) -> int | None:
    """`2g` / `512m` / `1073741824` → 字节。认不出返回 None，不返回 0。"""
    if isinstance(text, int) and not isinstance(text, bool):
        return text
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b?)?\s*", text.lower())
    if not match:
        return None
    number = float(match.group(1))
    factor = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}[match.group(2)]
    return int(number * factor)


# ----------------------------------------------------------------- 视图


def _service_blocks(doc) -> dict:
    if not isinstance(doc, dict):
        return {}
    for key in ("services", "containers"):
        block = doc.get(key)
        if isinstance(block, dict):
            return block
        if isinstance(block, list):
            return {f"#{i}": item for i, item in enumerate(block) if isinstance(item, dict)}
    return {}


def _stack_facts(path: Path, relative_to: Path, workspace_root: Path) -> dict:
    """一个 compose 文件里的事实。**一份文件是一个栈** —— 不跨文件求和。

    跨文件求和是一个没有含义的量：`test-envs/litellm-bridge/` 下有十几个互斥的
    环境定义，把它们的 mem_limit 加起来得到的数字不对应任何一次真实运行。
    第一版就是这么写的，它报出 66 GiB —— 一个真的数字，和一个假的结论。

    `where` 是给人读的（相对 test-envs 根），`path` 是给匹配用的
    （相对工作区根）。契约点名环境时用的是后者：一个只在某个上下文里
    够用的短名，会让「点名了哪一套」与「观测到哪一套」在别处对不上。
    """
    doc, reason = _read_yaml(path)
    where = str(path.relative_to(relative_to))
    full = str(path.relative_to(workspace_root))
    if doc is None:
        return {"where": where, "path": full, "readable": False, "reason": reason}

    workers: set[int] = set()
    memory: list[dict] = []
    without_limit: list[str] = []
    redis: list[str] = []
    store_model: list[str] = []

    services = _service_blocks(doc)
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        image = str(service.get("image") or "")
        if "redis" in image.lower() or "redis" in str(name).lower():
            redis.append(f"{name}={image or '(no image)'}")
        for key in ("command", "entrypoint"):
            text = service.get(key)
            if isinstance(text, list):
                text = " ".join(str(x) for x in text)
            for match in re.finditer(r"--num_workers[= ]+(\d+)", str(text or "")):
                workers.add(int(match.group(1)))
        limit = _mem_bytes(service.get("mem_limit"))
        if limit is None:
            without_limit.append(str(name))
        else:
            memory.append({"service": str(name), "bytes": limit})
        for key in ("environment", "env"):
            env = service.get(key)
            if isinstance(env, dict):
                pairs = list(env.items())
            elif isinstance(env, list):
                pairs = [(str(x).split("=", 1) + [""])[:2] for x in env]
            else:
                pairs = []
            for k, v in pairs:
                if str(k).upper().startswith("STORE_MODEL_IN_DB"):
                    store_model.append(f"{name}:{k}={v}")

    return {
        "where": where,
        "path": full,
        "readable": True,
        "services": sorted(str(n) for n in services),
        "workers": sorted(workers) or None,
        "redis": sorted(redis) or None,
        "memory_limit_bytes": sum(m["bytes"] for m in memory) if memory else None,
        "memory_limit_parts": memory,
        "services_without_limit": without_limit,
        "memory_cap_enforced": bool(memory) and not without_limit,
        "store_model_in_db": sorted(store_model) or None,
    }


def _named_stack(contract: dict) -> tuple[str | None, str | None]:
    """契约点名的那一套环境定义。返回 (目录, compose 文件)。

    漂移只在**被点名的那一套**上判。其余的栈照旧被观测、照旧出现在报告里，
    但不参与判决 —— 一个项目同时存在多个环境定义是常态，拿它们的并集去比
    契约，得到的差异无法归因到任何一套。
    """
    named = (contract.get("e2e_defaults") or {}).get("test_env")
    if isinstance(named, dict):
        return named.get("dir"), named.get("compose")
    if isinstance(named, str):
        return named, None
    return None, None


def observe(roots, contract: dict, project: str | None = None) -> dict:
    """从既有环境定义里读出**实际**的测试语义。

    读的是事实，不是意图：compose 里写了几条 `--num_workers`、有没有 redis 服务、
    内存上限之和是多少。读不到的部分老实报 `not_observed` ——
    「没找到」与「找到了但它等于零」是两件事，只有一个是一个结论。
    """
    project = project or contract.get("project")
    root_rel = ((contract.get("repo") or {}).get("test_envs"))
    observed: dict = {"sources": [], "stacks": [], "unreadable": [],
                      "not_observed": [], "graded": None, "graded_where": None,
                      # `not_observed` 是给人读的句子；这一个给判据读。少了它，
                      # 「契约点名的那一套不在」与「契约什么都没点名」在下游
                      # 长得一样 —— 而一个是故障，另一个是这件事还没发生。
                      "graded_absent": None}

    if not root_rel:
        observed["not_observed"].append("契约没有声明 repo.test_envs，没有可观测的面")
        observed["graded_absent"] = "repo-undeclared"
        return observed

    root = Path(roots.workspace_root) / root_rel
    if not root.is_dir():
        observed["not_observed"].append(
            f"{root_rel} 不存在：这个项目还没有可观测的环境定义")
        observed["graded_absent"] = "root-missing"
        return observed

    not_a_stack = 0
    for path in _iter_definition_files(root):
        facts = _stack_facts(path, root, Path(roots.workspace_root))
        observed["sources"].append(facts["where"])
        if not facts.get("readable"):
            observed["unreadable"].append(facts)
        elif not facts.get("services"):
            # 一个没有 services 的 YAML 不是一个栈（config.yaml、env 文件、
            # 各种清单都长这样）。把它算成一个「零服务的栈」会让栈数变成一个
            # 与事实无关的数，也会让「点名的那一套没找到」看起来像找到了。
            not_a_stack += 1
        else:
            observed["stacks"].append(facts)
    if not_a_stack:
        observed["not_observed"].append(
            f"{not_a_stack} 个 YAML 文件没有 services，未计作栈")

    named_dir, named_compose = _named_stack(contract)
    if named_dir is None:
        observed["not_observed"].append(
            "契约没有声明 e2e_defaults.test_env：观测到了环境定义，但没有一套被指名为"
            "被判决的那一套，因此没有任何一套拿来与契约比对")
        observed["graded_absent"] = "nothing-named"
        return observed

    wanted = str(Path(named_dir) / named_compose) if named_compose else str(Path(named_dir))
    matches = [s for s in observed["stacks"] if s["path"] == wanted]
    if not matches:
        observed["not_observed"].append(
            f"契约点名的 {wanted}（相对工作区根）不在观测到的环境定义里"
            f"（观测到 {len(observed['stacks'])} 套）。没有一套被判决 —— "
            f"这与「被点名的这一套没有问题」是两件事")
        observed["graded_absent"] = "named-stack-missing"
        return observed
    observed["graded"] = matches[0]
    observed["graded_where"] = matches[0]["where"]
    return observed


def drift(contract: dict, observed: dict) -> list[dict]:
    """契约声明与**被点名的那一套环境定义**不一致时报出来，不把契约改成观测值。

    调整默认值的路径是 observed → proposal → review → update contract。
    少掉中间两步，一条没人做过的决定就会以「默认值」的名义被执行。
    """
    findings: list[dict] = []
    graded = observed.get("graded")
    if not graded:
        return findings

    knobs = contract.get("knobs") or {}
    where = observed.get("graded_where")

    declared_workers = (knobs.get("workers") or {}).get("default")
    seen_workers = graded.get("workers")
    if isinstance(declared_workers, int) and seen_workers:
        if declared_workers not in seen_workers:
            findings.append({
                "code": "TEST_PROFILE_DRIFT",
                "knob": "workers",
                "declared": declared_workers,
                "observed": seen_workers,
                "where": where,
                "message": (f"契约声明 workers 默认 {declared_workers}，"
                            f"{where} 里写的是 {seen_workers}。契约不被自动改成观测值 —— "
                            f"要走 observed → proposal → review → update contract"),
            })
    elif isinstance(declared_workers, int) and not seen_workers:
        findings.append({
            "code": "TEST_PROFILE_DRIFT",
            "knob": "workers",
            "declared": declared_workers,
            "observed": None,
            "where": where,
            "message": (f"契约声明多 worker 是生产形态，但 {where} 里没有任何 "
                        f"--num_workers：这一套环境定义跑起来的 worker 数由镜像默认值决定，"
                        f"不由这份定义决定"),
        })

    hard_max = (contract.get("resource_limits") or {}).get("stack_memory_hard_max_bytes")
    if isinstance(hard_max, int):
        total = graded.get("memory_limit_bytes")
        if isinstance(total, int) and total > hard_max:
            findings.append({
                "code": "TEST_PROFILE_DRIFT",
                "knob": "stack_memory_bytes",
                "declared": hard_max,
                "observed": total,
                "where": where,
                "message": f"{where} 里 mem_limit 之和 {total} 超过契约硬上限 {hard_max}",
            })
        elif not graded.get("memory_cap_enforced"):
            missing = graded.get("services_without_limit") or []
            findings.append({
                "code": "TEST_PROFILE_DRIFT",
                "knob": "stack_memory_bytes",
                "declared": hard_max,
                "observed": total,
                "where": where,
                "message": (f"契约声明整栈硬上限 {hard_max}，但 {where} 里"
                            + (f" {len(missing)} 个服务没有 mem_limit（{', '.join(missing)}）"
                               if missing else "一个 mem_limit 都没有")
                            + "：上限没有被这套环境定义执行。"
                              "「没写上限」不是「没超过上限」"),
            })

    redis_spec = knobs.get("distributed_state_redis") or {}
    if redis_spec.get("default") == "enabled" and not graded.get("redis"):
        findings.append({
            "code": "TEST_PROFILE_DRIFT",
            "knob": "distributed_state_redis",
            "declared": "enabled",
            "observed": None,
            "where": where,
            "message": (f"契约把 redis 声明为 REQUIRED，但 {where} 里没有 redis 服务。"
                        f"容器 running 不等于被使用，没有服务就是没有"),
        })

    what = graded.get("store_model_in_db")
    if (knobs.get("model_authority") or {}).get("default") == "database" and not what:
        findings.append({
            "code": "TEST_PROFILE_DRIFT",
            "knob": "model_authority",
            "declared": "database",
            "observed": None,
            "where": where,
            "message": (f"契约把模型权威声明为数据库，但 {where} 里没有 STORE_MODEL_IN_DB："
                        f"这一套环境定义没有把模型目录交给数据库"),
        })
    return findings


def profile_drift(contract: dict, profiles: dict | None,
                  vocab: dict | None = None) -> list[dict]:
    """契约与 **L1 档位表**之间的一致性。

    与 `drift` 分开算，因为它们读的是两份不同的东西：`drift` 比的是契约与
    磁盘上的环境定义，这里比的是契约与档位表**自己声称的形状**。

    这一条正是那条接缝的静态形态。曾经的组合是：契约把 `distributed_state_redis`
    标成 fixed/enabled，档位表里有一档写着「不需要 redis」。两份文件各自都成立，
    各自都没撒谎，组合出的环境却不适用于生产 —— 而没有任何东西会响，
    直到有人拿那个环境的结论去下判断。静态可查的不一致就不该等到运行时才暴露。
    """
    findings: list[dict] = []
    floor = floor_capabilities(contract)
    known = (profiles or {}).get("profiles") or {}
    if not known:
        return findings
    project = contract.get("project")
    named_dir, named_compose = _named_stack(contract)
    wanted = (str(Path(named_dir) / named_compose) if named_compose
              else str(Path(named_dir))) if named_dir else None
    vocabulary_of_capabilities = set((vocab or {}).get("capabilities") or ())

    for name in sorted(known):
        decl = known.get(name)
        if not isinstance(decl, dict):
            continue
        declared_project = decl.get("project")
        if declared_project and declared_project != project:
            continue
        if "capabilities" not in decl:
            # 一个不声明自己提供什么的档位，在匹配里长得像「什么都不需要」，
            # 于是它要么永远不被选中、要么被当成 unit 用。缺字段不是空的形状。
            findings.append({
                "code": "TEST_PROFILE_DRIFT",
                "profile": name,
                "message": (f"L1 档位 {name} 没有声明 `capabilities`："
                            f"它提供什么无从核对，匹配会把缺字段读成「什么都不提供」"),
            })
            continue
        caps = _profile_capabilities(decl)
        unknown = sorted(c for c in caps if vocabulary_of_capabilities
                         and c not in vocabulary_of_capabilities)
        if unknown:
            findings.append({
                "code": "TEST_PROFILE_DRIFT",
                "profile": name,
                "message": (f"L1 档位 {name} 声称提供了词表之外的能力 {unknown}："
                            f"词表外的能力不是「更强」，是没法核对"),
            })
        if decl.get("narrowing"):
            continue
        illegal = profile_legality(decl, floor)
        if illegal:
            findings.append({
                "code": "TEST_PROFILE_DRIFT",
                "profile": name,
                "knob": "containerized_floor",
                "declared": floor,
                "observed": sorted(set(floor) - set(illegal)),
                "message": (f"L1 档位 {name} 是容器化档（提供 {REAL_CHAIN}）却缺少"
                            f"地板能力 {sorted(illegal)}：契约把那些能力定成容器化环境的"
                            f"默认形态。要么补上，要么把它声明成窄档（narrowing: true）"
                            f"并只能被显式点名使用"),
            })
        env = decl.get("env") or {}
        # 档位的 `env.dir` 锚在**契约的** `repo.test_envs` 上（L1 这么写的），
        # 而契约点名的那一套是相对工作区根的。两个相对路径来自两条不同的规则，
        # 直接比大小会把「同一个目录的两种写法」报成两套环境 —— 比较之前先把
        # 锚点接上，否则这条判据自己就是那个它要防的错误。
        test_envs = (contract.get("repo") or {}).get("test_envs")
        if wanted and test_envs and isinstance(env, dict) and env.get("dir"):
            built = str(Path(str(test_envs)) / str(env["dir"]))
            if env.get("compose"):
                built = str(Path(built) / str(env["compose"]))
            # 档位建的环境不在被点名判决的那一套上时，`--strict` 会对着
            # 一套没人跑过的定义报绿。这不是「另一套环境也有问题」，
            # 是「被判决的那一套不是被使用的那一套」。
            if built.rstrip("/") != wanted.rstrip("/"):
                findings.append({
                    "code": "TEST_PROFILE_DRIFT",
                    "profile": name,
                    "message": (f"L1 档位 {name} 建的是 {built}，而契约点名判决的是 "
                                f"{wanted}：两套不同，于是判决与运行各自成立"),
                })
    return findings


def _human_bytes(value) -> str:
    if not isinstance(value, int) or isinstance(value, bool):
        return str(value)
    for unit, factor in (("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if value >= factor and value % factor == 0:
            return f"{value} ({value // factor} {unit})"
    return str(value)


def render_contract(loaded: Loaded, policy: Loaded, drift_findings: list[dict]) -> list[str]:
    """人读视图：两栏 —— 「可调整」与「不可降低」。

    这不是把 JSON 打印得好看一点。这一屏要回答的是「我能不能为了让这次
    跑得快一点而动某个东西，动了之后要不要向谁解释」。
    """
    lines: list[str] = []
    doc = loaded.declaration
    if doc is None:
        return [f"契约读不出来：{loaded.reason}", f"  位置：{loaded.path}"]
    lines.append(f"项目 {doc.get('project')} · {SCHEMA}")
    lines.append(f"契约版本 {loaded.revision}")
    if policy.ok:
        lines.append(f"全局政策 {policy.revision}（{len(policy.declaration.get('invariants') or [])} 条硬不变量）")
    else:
        lines.append(f"全局政策 读不出来：{policy.reason}")
    lines.append("")

    knobs = doc.get("knobs") or {}
    adjustable, frozen = [], []
    for name, spec in knobs.items():
        if not isinstance(spec, dict):
            continue
        (frozen if spec.get("mutability") == "fixed" else adjustable).append((name, spec))

    lines.append(f"可调整（{len(adjustable)}）")
    for name, spec in adjustable:
        lines.append(f"  {name} = {spec.get('default')!r}"
                     f"   [{spec.get('mutability')} / {spec.get('criticality')}]")
        if spec.get("minimum") is not None or spec.get("recommended_max") is not None:
            lines.append(f"      范围 {spec.get('minimum')} .. 建议不超过 {spec.get('recommended_max')}")
        if spec.get("lowering_requires_reason"):
            lines.append("      降低需要理由，且理由会进 TestRun")
        if spec.get("reason"):
            lines.append(f"      为什么：{_wrap(str(spec['reason']))}")

    lines.append("")
    lines.append(f"不可降低（{len(frozen)}）")
    for name, spec in frozen:
        lines.append(f"  {name} = {spec.get('default')!r}   [{spec.get('criticality')}]")
        if spec.get("reason"):
            lines.append(f"      为什么不能动：{_wrap(str(spec['reason']))}")

    lines.append("")
    limits = doc.get("resource_limits") or {}
    lines.append("硬上限")
    for key, value in limits.items():
        lines.append(f"  {key} = {_human_bytes(value)}")

    lines.append("")
    # 「没比」与「比过了、一致」必须印成两行不同的话。前者不是结论，
    # 它是这次核对**少了一个输入**；把它印成「未发现」正是那个它要防的形状。
    unjudged = [i for i in drift_findings
                if i["code"] in (INPUT_UNREADABLE, INPUT_UNJUDGED)]
    drift_only = [i for i in drift_findings if i not in unjudged]
    if unjudged:
        lines.append(f"没比成（{len(unjudged)}）：这些输入没有参与核对，下面的漂移因此不完整")
        for item in unjudged:
            lines.append(f"  {item['code']} {item.get('input') or '-'}: {item['message']}")
    if drift_only:
        lines.append(f"漂移（{len(drift_only)}）：契约与观测不一致。契约**不会**被自动改成观测值")
        for item in drift_only:
            # 漂移有两种：契约与磁盘上的环境定义不一致（带 knob），
            # 契约与 L1 档位表不一致（带 profile）。两者都可能没有对方那个键，
            # 所以取哪一栏是判断出来的，不是假定出来的。
            subject = item.get("profile") or item.get("knob") or item.get("input") or "-"
            lines.append(f"  {item['code']} {subject}: {item['message']}")
    elif not unjudged:
        lines.append("漂移：未发现（契约与既有环境定义一致，或没有可观测的环境定义）")
    return lines


def _wrap(text: str, width: int = 62) -> str:
    text = " ".join(text.split())
    out, line = [], ""
    for word in text.split(" "):
        if len(line) + len(word) + 1 > width and line:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return ("\n" + " " * 6).join(out)


#: 「没有档位」的几种原因，它们不能长得一样：一档都没覆盖得了要求、要求集
#: 本身不完整、以及档位表读不出来。三件事的处置完全不同（换一档 / 把那一级
#: 的映射写下来 / 去修档位表），所以第一行就要分得开。
WHY_NO_PROFILE = {
    "unmet": "没有一档覆盖得了这一次要求的环境能力",
    "tier-unanswered": "这一次要证明的级别没有被回答过，要求集不完整",
    "no-profiles": "档位表读不出来，没有可对照的档位",
}


def render_resolution(result: dict) -> list[str]:
    """人读视图。machine-readable 的那份由 --json 给；两者看的是同一批事实。"""
    env = result.get("environment_profile") or {}
    verification = result.get("verification") or {}
    tier = verification.get("evidence_tier")
    name, source = env.get("name"), env.get("source")
    how = {"named": "点名", "matched": "按能力匹配"}.get(source)
    if name:
        head = (f"项目 {result.get('project')} · 环境档位 {name}"
                f"（{how or '来源未定'}）")
    else:
        why = (WHY_NO_PROFILE.get(result.get("capability_check_reason"))
               or WHY_NO_PROFILE.get(result.get("capability_check")))
        head = (f"项目 {result.get('project')} · 环境档位未定"
                + (f"（{why}）" if why else ""))
    lines = [head]
    if tier:
        lines.append(f"证据级别 {tier}（{verification.get('tier_source')}）"
                     + (f" · automated-testing 档位 {verification['profile']}"
                        if verification.get("profile") else ""))
    if result.get("item"):
        lines.append(f"Item {result['item']}" + (f" · Run {result['run_id']}" if result.get("run_id") else ""))
    lines.append("")

    requirement = result.get("requirement") or {}
    required = requirement.get("required") or []
    floor = requirement.get("floor") or []
    if required or (floor and name):
        lines.append("这一次要求环境具备：")
        for item in required:
            lines.append(f"  {item['capability']:<20} ← {'、'.join(item['required_by'])}")
        if not required:
            lines.append("  （没有：这一级在构造输入上判定，不需要真实环境）")
        provided = env.get("capabilities") or []
        # 地板是**候选档位**的合法性条件，不是这一次的要求集 —— 一个不建容器的
        # 档位不需要满足它。把它印在「要求」里会让 unit 那一档看起来缺少
        # 五项能力，而它本来就什么都不需要。
        if not name:
            lines.append(f"  容器化地板 {floor}（来自契约 {requirement.get('floor_source')}）："
                         f"这一次没有选中的档位，地板没有用来排除任何一档")
        elif REAL_CHAIN in provided:
            lines.append(f"  容器化地板 {floor}"
                         f"（来自契约 {requirement.get('floor_source')}）")
        else:
            lines.append(f"  容器化地板 {floor} 不适用：所选档位不建容器")
        if name:
            lines.append(f"  所选档位提供 {provided}")
        if result.get("capability_check") != "ok":
            lines.append(f"  能力核对：{result.get('capability_check')}"
                         + (f"（{result.get('capability_check_reason')}）"
                            if result.get("capability_check_reason") else ""))
        if env.get("narrowing"):
            lines.append(f"  **窄档**：豁免 {env.get('waived')}"
                         f"；理由：{env.get('chosen_because')}"
                         f"；不得声称任何证据级别")
        lines.append("")

    width = max((len(k) for k in result.get("values") or {}), default=8)
    lines.append(f"{'旋钮'.ljust(width)}  值")
    for name, entry in (result.get("values") or {}).items():
        flag = ""
        if entry.get("above_recommended_max"):
            flag = "  ⚠ 高于建议上限"
        if entry.get("override_reason"):
            flag += f"  理由：{entry['override_reason']}"
        source, decided_by = entry.get("source"), entry.get("declared_by")
        provenance = f"{source} · {decided_by}" if decided_by else str(source)
        lines.append(f"{name.ljust(width)}  {entry.get('value')!r}"
                     f"   [{provenance}]{flag}")

    if result.get("budget"):
        budget = result["budget"]
        lines.append("")
        lines.append(f"整栈预算上限 {_human_bytes(budget.get('ceiling'))}"
                     f"  （{budget.get('ceiling_knob')}，来自 {budget.get('ceiling_source')}）")

    if result.get("constraints"):
        lines.append("")
        lines.append(f"约束来自全局政策（{len(result['constraints'])} 条，任何一层都不能突破）")
        for item in result["constraints"]:
            lines.append(f"  {item.get('id')}: {item.get('title')}")

    lines.append("")
    if result.get("violations"):
        lines.append(f"违规 {len(result['violations'])} 处 —— **不产出有效环境**")
        for item in result["violations"]:
            lines.append(f"  {item['code']}  {item.get('knob') or '-'}  {item['message']}")
    else:
        lines.append("无违规：这套值可以被执行")
    # 「点名的档位在 L1 里找不到」只在真的点了名的时候才是一句话。没点名而
    # 没有档位时印「档位 None 找不到定义」，是把一件没有发生的事说成发生了。
    if result.get("profile") and not result.get("profile_known", True):
        lines.append(f"注意：档位 {result.get('profile')} 在 L1 里找不到定义，"
                     f"它的约束没有参与解析")
    return lines


def as_json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
