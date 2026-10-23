# G1 · Device Ability projection

**一个投影，不是一个存储。** 它回答：对某一台设备、某一个 principal、某一个 runtime，
权威 manifest 记了哪些 Ability 是 *installed*，其中哪些 *supported* / *ready* /
*granted*，以及在哪个 **source revision** 上——并且只在设备真的报过所有决定性项时，
才给出 `effective`。

`integrations/fleet-v1-mcp-conformance/CAPABILITY-PROJECTION.md` 点名的缺口就是它：
Fleet Next 没有任何 Device Ability 投影；两个 CloudCLI capability read 只投影 22 个镜像动词，
**不是**设备的 Ability registry，把它们当成 registry 正是架构禁止的替换。

## 它复用什么（不造第二个）

| 事实 | 来自 |
|---|---|
| 某设备装了哪些 Ability、哪个 revision / fingerprint | `runtime/ability_projection.py#installed_abilities`（权威 manifest 的 `revisions[].installations[]`） |
| 能不能调用（八轴交集） | `abilities/resolver.py#resolve` —— **每次能力判断都走它** |
| manifest 的读取 | `runtime/ability_projection.py#read_manifest` —— 从 **git object** 读，不读工作树 |

本模块不注册 Ability、不缓存 manifest、不写任何 registry、不定义第二套拒绝词表。

## 四个词

| 词 | 来源 | 值 |
|---|---|---|
| `installed` | 权威 manifest（**不是**设备观察） | `true` |
| `supported` | 设备报 `device_capabilities` **且** `profile` | `SUPPORTED` / `DENIED` / `UNKNOWN` |
| `ready` | 设备报 `health` | `READY` / `NOT_READY` / `UNKNOWN` |
| `grant` | 设备报 `policy` | `ALLOWED` / `DENIED` / `UNKNOWN` |

缺一个观察 → `UNKNOWN`，并写明缺哪个轴。**不是默认通过。**

## `effective` 为什么常常是 null

```text
effective = resolver.provider
  当且仅当 resolver.status ∈ {AVAILABLE, DEGRADED}
  且 supported / ready / grant 都不是 UNKNOWN
否则       null
```

这不是与 resolver 唱反调，而是问了一句更窄的话。`abilities/resolver.py#_input_policy` 说得很
清楚：*"policy is a filter over an answer, not a capability input"* —— 所以 resolver **可以**
在没有 `policy` / `health` 的情况下答 `AVAILABLE`，而且这是对的。G1 的规则是
**`effective` 只有在设备真的报过时才非 null**，所以投影把两个答案**并排放**，谁也不覆盖谁：

```json
{"status": "AVAILABLE", "reason": null,          // resolver 的原话
 "ready": "UNKNOWN", "grant": "UNKNOWN",
 "effective": null,
 "effective_why": "resolver answered AVAILABLE from the inputs it had, but this device
                   reported no ready, grant — printing the provider id here would be read
                   as callable"}
```

`effective` 是 **provider id 或 null**，永远不是 `true`：一个 `true` 说不出「由谁提供」。

## 21 个 family × 三类设备：覆盖矩阵

`coverage.py` 回答一个更粗的问题：权威的 **21 个语义 family**，在 Tokyo / Windows /
Dell A6000 三类设备上分别是什么状态。它把四个容易压成一个整数的词分开报：

| 词 | 来源 | 含义 |
|---|---|---|
| `declared` | `product-tree.yaml` | family 存在（永远 21 行） |
| `realized` | manifest `product_role.primary` | 有 identity 声明拥有这个职责；0 是**无主**，不是零覆盖 |
| `installed` | manifest `revisions[].installations[]` | **权威**为这个 device id 记了安装 |
| `observed` | 设备快照 | **设备**报出了匹配项；`strong`（stable_id/canonical source）与 `probable`（仅名字）分开 |

`ready` / `grant` 在本轮一律 `UNKNOWN`：扫描快照没有 health / per-principal policy 观察。
`NOT_OBSERVED` 是「这轮没人看」，`UNRESOLVED` 是「权威根本没有这个 device id」，
**都不等于 false**。按产品名把 Dell A6000 映射到 `dell-work`（Windows）是禁止的。

2026-10-20、权威 `4a46f2b` 的实测结果（详见
[`DEVICE-COVERAGE-MATRIX.md`](DEVICE-COVERAGE-MATRIX.md)）：

- 21 个 family 中 **19 个有 primary realization**；**B1 / B3 无 primary owner**（但被
  其他 family 的 identity cross-reference —— 是「无主」，不是「不存在」）。
- 61 个 identity 中 **38 个没有 `product_role.primary`**，无法归属任何 family；其中
  13 个有 Tokyo 安装记录、12 个有 `dell-work` 安装记录。**family 覆盖数是下界，不是全量。**
- Tokyo：权威记 **19 个 identity** 安装；设备快照 70 条观察（19 strong / 27 probable /
  1 ambiguous / 23 unmatched）。`ready` / `grant` = `UNKNOWN`。
- Windows（`dell-work`）：权威记 13 个 identity 安装，**本轮没有快照** →
  `NOT_OBSERVED`；观察相关的词全部 `UNKNOWN`。
- Dell A6000（Linux）：权威**没有 device id** → `UNRESOLVED`；设备相关词全部 `UNKNOWN`。

一次有界的只读 `ability resolve`（Tokyo，`ability:information-management`）返回
`status: UNKNOWN`，`inputs_missing` 列出 7 个轴（含 `policy`）—— 这是「可调用是合取」
的正面证据，不是失败被隐藏。Dell/Windows 两类没有 `device_ref`，记 `NOT_RUN`。

复现（需要权威 manifest；它不被复制进本仓库）：

```bash
python3 integrations/device-ability-projection/coverage.py \
  --manifest <ability-management>/abilities-manifest.yaml \
  --revision 4a46f2b178e5f93de12b7a27c9d0df00e45683ea \
  --evidence integrations/device-ability-projection/fixtures/resolve-evidence.json --check
```

**不要把 21 个声明语义读成 21 个已安装或已测试 Ability。** 矩阵的每个 cell 都在说它
回答的是哪一件事实。

## 输入

```python
effective.project_device(
    manifest=<parsed abilities-manifest.yaml>,
    source_revision=<40-hex git revision>,
    device={
        "device_ref": "device:tokyo-shared-dev-01",
        "device_ids": ["tokyo-shared-dev-01"],   # 可选，默认取 device_ref；legacy id 可列多个
        "principal": "person:vince",             # 必填：没有主体的绑定答不出「这是谁的」
        "runtime": "claude-code",                # 必填
        "observations": {                        # 只放**真的报过**的轴
            "providers": [...], "instances": [...], "profile": {...},
            "runtime_record": {...}, "device_capabilities": {...},
            "health": {...}, "policy": {...}
        },
    })
```

`observations` 的键就是 resolver 的输入键（`device_capabilities / profile / instances /
runtime_record / runtime_binding / health / policy / task_context / providers`）。写一个
未声明的轴会被拒绝——平行的词表就是本模块要避免的第二份定义。

`read_source(repository=..., revision="origin/main")` → `(manifest, resolved_sha)`。

## 到 Fleet 消费端的路线（本 PR **不**做；写于 33 工具时期）

`CAPABILITY-PROJECTION.md` 说这一步「is B's file」，所以本目录**不动** `fleet_mcp/`、
不加工具、不改任何 served surface。路线是：

1. `fleet_mcp` 侧（**B 的文件**）加一个 **read-scoped** 工具，输入 `device_ref` +
   `runtime` + `principal`，输出本模块的 dict；
2. 它只调用 `effective.project_device(...)`，**不重建**任何判断；
3. 它的 `source_revision` 就是它读的 git revision，随回执一起返回；
4. 它落地时工具计数只加一 —— 这是一个**读**面，与两个既有的 capability read 同级，
   而不是一个新的可执行动词。（本段写于 33 工具时期；今天是 `fleet-mcp-6`，
   34 = 10 手写 + 22 镜像 + 2 能力读，所以这个读面落地后是第 35 个。）

在 (1) 被 B 落地之前，本模块只能被直接 import 或在测试里跑。

## 跑测试

```bash
# bounded（本机 cgroup 边界）
systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 -p CPUQuota=200% -- \
  timeout 300 pytest tests/test_device_ability_projection.py -q
```

测试放在 `tests/` 里，文件名唯一。这一条不是风格：`tests/test_collection_scope.py` 明令
禁止 `tests/` 之外出现 `test_*.py`，因为裸 `pytest` 与仓库的测试普查必须看到**同一片地方**，
否则「测试跑了」与「测试被收集了」会悄悄分成两件事。本 PR 的第一版把测试放在
`integrations/device-ability-projection/tests/`，正是被这条守卫拦下的。

可选：`SERVEROS_ABILITY_REPO=<checkout>` 会让最后一个测试真的去解析权威 manifest
（`origin/main` 的 git object），而不是只看 fixture。

## 验证边界（不要读成已验证）

- Tokyo 的 observation 是一次**本地只读** `control_plane.py scan` 的真实结果，已脱敏
  （移除 `location` / `fingerprint` / `version` / `author` 等字段才进入仓库）。它证明
  「本机观察到 70 条 capability」，**不证明** Dell 或 Windows 的任何事。
- Dell A6000 与 Windows 两类**没有快照**：分别是 `UNRESOLVED` 与 `NOT_OBSERVED`，
  观察相关的词只能是 `UNKNOWN`。
- 一次只读 `ability resolve` 返回 `UNKNOWN`（缺 7 个轴，含 `policy`）；**没有执行**任何
  有副作用的 Ability 调用，没有 GPU / 飞书 / 凭据 / 权限变更，没有改 Windows。
- **没有证明**权威 installation 记录与真实机器一致。事实上 Tokyo 的 manifest 安装记录
  （19 个 identity）与扫描观察（70 条 capability）明显不一致 —— 这正是矩阵要显示的事实，
  不是矩阵的误差。
- 本目录之外的文件一个都没改（测试文件除外，它在 `tests/`）。
