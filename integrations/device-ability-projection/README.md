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

## 到 33 工具 Fleet 消费端的路线（本 PR **不**做）

`CAPABILITY-PROJECTION.md` 说这一步「is B's file」，所以本目录**不动** `fleet_mcp/`、
不加第 34 个工具、不改任何 served surface。路线是：

1. `fleet_mcp` 侧（**B 的文件**）加一个 **read-scoped** 工具，输入 `device_ref` +
   `runtime` + `principal`，输出本模块的 dict；
2. 它只调用 `effective.project_device(...)`，**不重建**任何判断；
3. 它的 `source_revision` 就是它读的 git revision，随回执一起返回；
4. 33 工具的计数不变 —— 这是一个**读**面，与两个既有的 capability read 同级，
   而不是第 34 个可执行动词。

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

## 没有验证的（不要读成已验证）

- **没有联系任何设备**：所有 device 记录都是合成 fixture。
- **没有调用任何 Ability**，没有 GPU、没有飞书、没有凭据、没有权限变更。
- **没有证明**真实设备会报出这些观察，也没有证明权威 manifest 的安装记录与某台真实机器一致。
- 本目录之外的文件一个都没改。
