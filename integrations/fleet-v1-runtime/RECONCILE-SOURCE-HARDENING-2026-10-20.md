# R1 #248 / S2 #245 · reconcile-source hardening after QA2 #360

> **Base**：`#356` exact `b4020b73e6b7ec2b1f6301471a9872f8864c1643`。
> **Audit**：QA2 #360 `2888d8bda6828bd2c87c68a770440933fdb4ae23`（`QA_PARTIAL` /
> `NOT_RELEASE`，R1 2×P1 + 4×P2）。
> **Evidence level**：`CODE` / `LOCAL` / `SYNTHETIC_FIXTURE`。没有真实 send、
> model call、token/transcript、Provider、serverosd、Fleet/Connector、OAuth 或
> 部署变更。`REAL_TEMP` / `REAL_DEVICE` / `CLIENT_E2E` = `NOT_RUN`。

## 1. QA2 8 项 RED（在 #356 exact HEAD 复现）

命令（QA2 原始 probe，未修改其代码）：

```sh
cd <worktree at b4020b73>
R1_SNAPSHOT="$PWD" PYTHONDONTWRITEBYTECODE=1 nice -n 10 \
  systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
  timeout 120 python3 /tmp/qa2_r1_probe.py --mode findings
```

结果：**rc=1，8 failed / 10 total**。失败的 8 项：

| ID | 攻击 | v1 结果 |
| --- | --- | --- |
| R1-P1-1 | synthetic 结果带 `owner_signed=true`、`s2_verdict=SETTLE_APPLIED`、`settle_allowed=true` | 生产字段名被测试 HMAC 污染 |
| R1-P1-2a | `observation_id=""` + applied set `[""]` | 跳过 replay，`SYNTHETIC_ACCEPTED_APPLIED` |
| R1-P1-2b | `observation_id=None` | 同上 |
| R1-P2-1 | signed `source="forged:source"` | 未与 capability 比对，仍 APPLIED |
| R1-P2-2 | signed `observed_at="not-a-time"` | 未解析/未校验，仍 APPLIED |
| R1-P2-3a | `thread_known=false` + 声明 `CLIENT_TURN_ID` | 未交叉检查，仍 APPLIED |
| R1-P2-3b | `adapter=codex-cli` + 声明 `CLIENT_TURN_ID` | 未交叉检查，仍 APPLIED |
| R1-P2-4 | writer epoch 7→8，observation 不变，applied set 为空 | 仍 APPLIED |

两个 QA2 控制项（非空 id replay、同 epoch 下 replay id 命中）在 v1 即为 PASS，
本轮保留。

## 2. v2 契约：生产字段恒定拒绝，synthetic 独立

`reconcile_source_gate.py` 升级为
`serveros/fleet-v1-runtime-reconcile-source-gate.v2`。**共享生产字段**在两种
mode 下都只有拒绝：

```text
decision               = BLOCKED_PROVIDER_SOURCE
production_decision    = BLOCKED_PROVIDER_SOURCE
production_eligible    = false
send_allowed           = false
retry_allowed          = false
settle_allowed         = false
owner_signed           = false
s2_verdict             = null
s2_status              = BLOCKED_PROVIDER_SOURCE
```

**synthetic-only 字段**（只允许测试 harness 读取）：

```text
synthetic_decision            = SYNTHETIC_ACCEPTED_APPLIED | REFUSED_* | UNKNOWN | BLOCKED_*
synthetic_signature_valid     = bool
synthetic_settle_allowed      = bool（仅 SYNTHETIC_ACCEPTED_APPLIED）
synthetic_observation_id      = observation id
signature_authority           = SYNTHETIC_TEST_ONLY | null
```

一个只读 `decision` / `owner_signed` / `s2_verdict` / `settle_allowed` 的
未来 consumer，**永远拿不到 settle**。`SYNTHETIC_ACCEPTED_APPLIED` 不再是
`SETTLE_APPLIED`，也不再出现在 `s2_verdict`。

## 3. 修复对照

| QA2 finding | v2 修复 | 新拒绝/阻断 |
| --- | --- | --- |
| R1-P1-1 synthetic 生产字段 | 生产字段恒定拒绝；synthetic 独立命名 | 生产 `decision=BLOCKED_PROVIDER_SOURCE`；`synthetic_settle_allowed` 只读测试字段 |
| R1-P1-2 空/缺失 observation_id | 非空 id 是 replay 前置条件 | `REFUSED_MISSING_OBSERVATION_ID` |
| R1-P2-1 signed source 未绑定 | signed source 必须在 `codex:thread/turns/list` / `codex:thread/read`，并与 `source.read_path` 一致 | `REFUSED_SOURCE_MISMATCH` |
| R1-P2-2 observed_at 未校验 | 解析 zoned ISO-8601；未来拒绝；`now - observed_at <= max_age`（1..3600s） | `REFUSED_OBSERVED_AT_INVALID` / `_FUTURE` / `_STALE`；缺失 `now` → `BLOCKED_TIME_UNTRUSTED` |
| R1-P2-3 source 声明未交叉检查 | `(adapter, verb, thread_known) -> capability` 表；声明不一致即阻断 | `BLOCKED_PROVIDER_SOURCE` |
| R1-P2-4 signed payload 缺 writer fence | `writer_owner` / `writer_epoch` / `writer_token` 进入签名；同时保留 attempt writer vs current writer 检查 | `REFUSED_STALE_WRITER` |

时间由调用方传入 **trusted simulated now**，gate 不读 host clock；`max_age`
有界 1..3600 秒。没有真实 Provider 调用。

## 4. QA2 8 witnesses 的 v2 GREEN

| witness | production decision | synthetic decision | settle_allowed | s2_verdict |
| --- | --- | --- | --- | --- |
| empty observation id | `BLOCKED_PROVIDER_SOURCE` | `REFUSED_MISSING_OBSERVATION_ID` | false | null |
| absent observation id | `BLOCKED_PROVIDER_SOURCE` | `REFUSED_MISSING_OBSERVATION_ID` | false | null |
| forged signed source | `BLOCKED_PROVIDER_SOURCE` | `REFUSED_SOURCE_MISMATCH` | false | null |
| observed_at invalid | `BLOCKED_PROVIDER_SOURCE` | `REFUSED_OBSERVED_AT_INVALID` | false | null |
| observed_at future | `BLOCKED_PROVIDER_SOURCE` | `REFUSED_OBSERVED_AT_FUTURE` | false | null |
| observed_at stale | `BLOCKED_PROVIDER_SOURCE` | `REFUSED_OBSERVED_AT_STALE` | false | null |
| synthetic production fields | `BLOCKED_PROVIDER_SOURCE` | `SYNTHETIC_ACCEPTED_APPLIED`（test-only） | false | null |
| writer epoch 7→8 | `BLOCKED_PROVIDER_SOURCE` | `REFUSED_STALE_WRITER` | false | null |
| source thread_known=false | `BLOCKED_PROVIDER_SOURCE` | `BLOCKED_PROVIDER_SOURCE` | false | null |
| source CLI declares id | `BLOCKED_PROVIDER_SOURCE` | `BLOCKED_PROVIDER_SOURCE` | false | null |

全部 production 字段：`owner_signed=false`、`settle_allowed=false`、
`s2_verdict=null`、`production_eligible=false`、`send_allowed=false`、
`retry_allowed=false`。

## 5. 语义迁移（不是删案例，也不是放宽）

- 原 18 个 fixture case **全部保留**；期望从 v1 的 `decision` /
  `owner_signed` / `s2_verdict` / `settle_allowed` 迁移为
  `synthetic_decision` / `synthetic_signature_valid` /
  `synthetic_settle_allowed` + 生产恒定拒绝断言。
- 原 17 个控制测试保留（forged locator、cross-owner、cross-project、changed
  payload、missing parent、replay、stale writer、NOT_APPLIED、UNKNOWN、
  capability matrix、purity 等），并增加 source/time/writer 的新断言。
- QA2 的 8 个攻击全部成为显式测试，不再依赖“非空 id 才检查 replay”的旧逻辑。
- 新增 `qa2-missing-now`、future/stale observed_at、thread_known=false、CLI
  adapter 四个额外反例。
- 总测试：**61 passed**（28 个 fixture case + 33 个控制/负测），无 skip。

## 6. 生产仍 `BLOCKED_PROVIDER_SOURCE`

两个条件仍未同时满足：

1. **provider 明确支持 pre-send id + 可重新查询**：Codex app-server 在 stable
   v2 schema 级满足；不是 live-proven。
2. **owner-signed observation**：S2 kernel/store/daemon 只有 principal、
   writer、intent/locator、`source` provenance；没有签名。production
   `dispatch_observation_source=None`。

因此 v2 在 production mode 固定返回
`decision=BLOCKED_PROVIDER_SOURCE`、
`production_reason=BLOCKED_OWNER_SIGNATURE`、`settle_allowed=false`、
`s2_verdict=null`。没有 send、retry、automatic settle；没有 NOT_APPLIED；
没有 exactly-once。只有未来同时具备可信 pre-send identifier、live owner
signature、scope/window/fence/readback 时，才允许另开生产实现。本轮不允许
用 test HMAC 开通生产。

## 7. 验证

```sh
cd /data/home/vince/workspace/worktrees/serveros/r1-reconcile-hardening-20261020

PYTHONDONTWRITEBYTECODE=1 nice -n 10 \
  flock -n /tmp/serveros-fleet-v1-runtime-hardening.lock \
  systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
  timeout 120 /data/home/vince/toolchains/serveros-test/bin/pytest \
    tests/test_fleet_v1_runtime_reconcile_source.py -q -p no:cacheprovider
```

实测：**61 passed in 0.60s**，exit 0。迁移后的 8 witnesses GREEN 见 §4。
未跑 heavier 186 集；本轮只跑本 lane 的 61 项，避免与 I1/其他 Agent 同跑重型
测试。

## 8. 明确未声称

- 没有真实 send / model call / 付费消息。
- 没有读取 token、transcript、环境或凭据；没有登录或借用户 credential。
- 没有触碰 canonical `src/serveros/main@665067e` 的 `M fleet/registry.yaml`、
  正在运行的 serverosd/Fleet/Connector、生产 OS owner policy/grant/凭据、
  真实 Provider、OAuth、部署或设备服务。
- 没有修改 I1/RC-v7、Q1、X2、S1 或其他 Agent 分支/文件。
- 没有把 synthetic HMAC 提升为 owner authority；没有 QA_SIGNED / V1_RELEASE
  声明。
- `REAL_TEMP` / `REAL_DEVICE` / `CLIENT_E2E` = `NOT_RUN`；下一 RC 仍需要独立
  产品审查和真实 owner-signed source。

## 9. Pinned refs

- #356 exact base `b4020b73e6b7ec2b1f6301471a9872f8864c1643`
- QA2 #360 `2888d8bda6828bd2c87c68a770440933fdb4ae23`
- R1 #311 `34b215c355d93efe9e7b06fcc09f9a803c061f00`
- S2 #328 `cf32af82c03eb52a4f34e2907e670f23b04a9b57`
- authoritative main `771c81c`
- local CLI audit unchanged from #356: Codex app-server stable v2 schema,
  Claude Code CLI `--session-id`, BotMux provider-id read, CloudCLI `runId`
