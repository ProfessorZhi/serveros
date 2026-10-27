# `runtime/` 托管矩阵

谁在加载 `runtime/` 里的每个文件、谁正在改它、以及一次目录搬迁会付出什么代价。
本文件是 **#429**（Fleet V1 D2/D3/D4/D10 目录收敛）的只读清点快照，不是搬迁决定：
决定在各自的 PR 与 #429 里，这里只放可复核的数字。

- 快照：`main @ e69fc0337ae5d33000478e3d5162077f525bc8cc`（2026-10-28）
- 生成：
  ```bash
  python scripts/runtime_custody_matrix.py \
    --claims docs/architecture/runtime-custody-claims-20261028.json
  ```
  下半部分是本命令的原样输出。

## 五条结论

1. **计划里的「123 个模块」其实是 123 个被跟踪文件**：89 个 `.py` ＋ 34 个契约/配置
   （`runtime/actuator-contract.yaml`、`runtime/*.schema.json` 之类）。把 123 当模块数，
   会把搬运面高估 38%，并且让「搬完 123 个」变成一个无法收敛的验收条件。

2. **D2 的 8 个、D4 的 10 个能在树里逐个对上；D3 的 26 个只能对上 20 个。**
   `layers.yaml` 给 `runtime.parts.product` 写的是模式
   （`cards*` / `card_*` / `daily_report` / `report_*` / `alert_*` / `notifications` /
   `direct_lark_notify` / `live_sender` / `workbench*` / `presentation` / `business_calendar`），
   按模式选中 20 个模块；计划说的 26 个里剩下的 6 个**没有被任何一处点名**。
   D3 若要按「搬完 26 个」验收，这 6 个必须先有名字 —— 否则验收条件取决于谁来数。

3. **跨层引用面最大的是 `cli`（16 143 行、101 个消费者文件：tests 53、docs 14、runtime 13）**，
   其次 `daemon`（5 445 行）与 `agent_control_store`（3 100 行）。
   这就是 D10 的道理：`cli` 不是「一个大文件」，是全树 101 条引用汇到的一点；
   在 D2/D3 之前动它，等于在别人还在改它两侧的模块时改它的形状。

4. **26 个模块不加载任何别的模块**（下表 "Ready to move alone"）。但「依赖单向」
   不等于「现在能搬」——
   - **`#407` 独占 10 条 `runtime/` 路径**（`agent_control_store`、`owner_policy`、
     `live_sender`、`message_transport`、`document_transport`、`daemon`、`fleet_mcp_owner`
     等），其中 `agent_control_store` / `owner_policy` 正是 D4 的对象，`live_sender` 是 D3 的对象；
   - 搬到**新顶层目录**还要动两张表：`docs/architecture/layers.yaml`（#414 独占）与
     `scripts/build.py` 的 `CORE_MODULES`（#413 正在改，且改的正是打包组装那一段）。
     两者都在 RC（#427）的合并序列里。
   ⇒ **D2 的集成次序是 `#413 → #414 → D2`**。在 #413 合并之前做顶层 `fleet_hub/`，
   要么产物里静默缺文件（`CORE_MODULES` 不认的名字，打包循环当作「不是模块」跳过，
   见 `plugin/bundle.yaml` 里记了三次的 C4 缺陷），要么就得改一个别人正在改的文件。

5. **D4 只能只读。** `workengine/` 整目录属 #407，`agent_control_store` 与 `owner_policy`
   也在 #407 的 85 条路径里；lease / epoch / fence 的边界审计可以做，搬家不能，
   否则安全测试（S1/S2）的判据会在两个分支上各跑一遍、谁也不算数。

## 方法与边界（为什么这些数字可信，以及它们看不见什么）

- **入边用仓库自己的 `tests/loader_graph.py` 反查**，不另写正则：它是全树唯一「谁加载谁」
  的权威陈述，打包门禁 `production_closure()` 用的也是它。第二个私有扫描器只能和自己一致。
- **文本引用单独计数**（`.service` / `.timer` / `plugin/bundle.yaml` / 文档里的路径字符串）。
  它在搬迁时的失败方式和 import 不同：import 期不报错，运行期 `ExecStart` 找不到文件。
- **看不见的东西，明写**：`f-string` 拼出的路径、从配置文件读出的路径、`getattr`/反射加载，
  在图上都没有边。所以表里的 0 是**下界**，不是「没人用」。
- **归属是快照**：`docs/architecture/runtime-custody-claims-20261028.json` 记的是当时
  20 个 open PR 的 187 条路径声明。归属每小时在变，**引用前先重跑**。

## 复现

```bash
# 1. 归属快照（需要 gh 已登录；20 个 PR，逐条取 files）
gh pr list --state open --json number | jq -r '.[].number' | while read n; do
  gh pr view "$n" --json number,files
done            # → 汇总成 {"path": [pr, ...]} 的 JSON

# 2. 矩阵
python scripts/runtime_custody_matrix.py --claims <那段 JSON>
python scripts/runtime_custody_matrix.py --json     # 同一份数据的机器可读形态
```

---

以下为生成输出，未手工编辑。

`runtime/` holds **123 tracked files**, **89** of them Python modules and 34 contracts/configs.
Regenerate with `python scripts/runtime_custody_matrix.py`.

## Work-package reconciliation

| group | plan says | this census finds |
|---|---|---|
| D2 hub (`fleet_hub/`) | 8 | 8 |
| D3 product (`reports/`) | 26 | 20 |
| D4 work control (`workengine/agent_control/`) | 10 | 10 |
| L1 remainder | — | 51 |

## Ownership

From `docs/architecture/runtime-custody-claims-20261028.json`: 10 `runtime/` path(s) are claimed by an open PR.

| file | open PR(s) |
|---|---|
| `runtime/agent_control_store.py` | #407 |
| `runtime/daemon.py` | #407 |
| `runtime/document_transport.py` | #407 |
| `runtime/fleet-event-delivery-contract.yaml` | #407 |
| `runtime/fleet_event_delivery.py` | #407 |
| `runtime/fleet_mcp_owner.py` | #407 |
| `runtime/live_sender.py` | #407 |
| `runtime/message_transport.py` | #407 |
| `runtime/owner_operations.py` | #407 |
| `runtime/owner_policy.py` | #407 |

### What a new top-level directory costs

| file | why a move needs it | open PR(s) |
|---|---|---|
| `docs/architecture/layers.yaml` | register the directory; the layer guard reads it | #414, #427 |
| `scripts/build.py` | `CORE_MODULES` decides what an artifact copies at all | #413, #427 |
| `plugin/bundle.yaml` | declares the shipped path; the packaging loop reads it | — |
| `scripts/runtime_truth.py` | reports which core modules a node actually has | #421, #427 |
| `scripts/release.py` | the release path verifies the same composition | #419, #427, #433 |
| `tests/test_repository_layers.py` | asserts the registered set and the layer rules | — |

## Modules

`in` = inbound consumers by category (loader edges from `tests/loader_graph.py`, inverted, plus text references); `out` = modules this one loads; `move` = what a move costs by dependency shape alone.

| module | layer | lines | in | out | move  claimed |
|---|---|---:|---|---|------|
| `cli` | L1? | 16143 | runtime 13, tests 53, scripts 3, control-plane 2, plugins 3, integrations 4, docs 14, other 9 | packs, resolver, runtime_binding, runtime_parity, taxonomy, delegation, disclosure, res… | with abilities, context, documents, intake, mcp, memory, space, workengine  — |
| `daemon` | L1? | 5445 | runtime 9, tests 30, scripts 2, control-plane 2, plugins 2, integrations 13, native 1, docs 24, ci 1, other 1 | resolver, actor, resolver, ability_projection, agent_control_store, agent_sessions, ale… | with abilities, context  #407 |
| `agent_control_store` | L0 | 3100 | runtime 20, tests 20, control-plane 2, plugins 1, integrations 4, docs 19, other 2 | actor | with context  #407 |
| `paths` | L1? | 1308 | runtime 17, tests 18, scripts 4, control-plane 7, plugins 2, docs 3, ci 1, other 4 | — | alone (loads nothing)  — |
| `coordination` | L0 | 675 | runtime 3, tests 5, scripts 1, control-plane 3, integrations 7, docs 13, other 1 | — | alone (loads nothing)  — |
| `registry` | L1? | 1157 | runtime 18, tests 6, control-plane 1, integrations 5, docs 1 | — | alone (loads nothing)  — |
| `card_delivery` | product | 853 | runtime 10, tests 7, control-plane 1, plugins 1, docs 5 | agent_control_store, message_transport | with 2 runtime modules  — |
| `wake` | L0 | 1251 | runtime 6, tests 7, scripts 1, control-plane 2, plugins 1, integrations 2, docs 5 | delegation, daily_cycle, agent_control_store, bounded_invocation, continuation | with context, intake  — |
| `workbench` | product | 1830 | runtime 4, tests 1, plugins 1, docs 18 | workbench_sources | with workbench_sources  — |
| `alerting` | L1? | 1800 | runtime 10, tests 4, control-plane 1, plugins 1, docs 4, other 2 | agent_control_store, anomaly, graph, notifications, presentation | with 5 runtime modules  — |
| `health` | L1? | 532 | runtime 9, tests 3, control-plane 3, integrations 1, docs 3, other 3 | registry | with registry  — |
| `anomaly` | L1? | 1283 | runtime 10, tests 2, control-plane 1, plugins 1, docs 5 | attribution | with attribution  — |
| `card_source` | product | 797 | runtime 9, tests 4, control-plane 1, plugins 1, native 1, docs 3 | alert_digest, business_calendar, card_day | with 3 runtime modules  — |
| `inventory` | L1? | 2289 | runtime 7, tests 2, control-plane 1, plugins 1, native 4, docs 4 | anomaly, paths, registry | with 3 runtime modules  — |
| `notifications` | product | 304 | runtime 5, tests 6, control-plane 1, docs 5, other 2 | transport | with fleet  — |
| `ability_projection` | L1? | 844 | runtime 3, tests 5, control-plane 1, plugins 1, integrations 3, docs 2, other 3 | paths | with paths  — |
| `bounded_invocation` | L1? | 253 | runtime 5, tests 5, scripts 1, control-plane 1, plugins 1, integrations 1, native 1, docs 3 | agent_control_store | with agent_control_store  — |
| `graph` | L1? | 1359 | runtime 8, tests 3, control-plane 1, native 3, docs 2 | attribution | with attribution  — |
| `alert_digest` | product | 1853 | runtime 10, tests 3, plugins 1, docs 2 | agent_control_store, alerting, graph, presentation | with 4 runtime modules  — |
| `message_transport` | L1? | 438 | runtime 7, tests 6, docs 3 | document_transport | with document_transport  #407 |
| `business_calendar` | product | 570 | runtime 7, tests 4, control-plane 1, docs 3 | — | alone (loads nothing)  — |
| `dispatch_attempts` | L0 | 669 | runtime 2, tests 4, control-plane 1, integrations 3, docs 5 | — | alone (loads nothing)  — |
| `agent_sessions` | L0 | 92 | runtime 1, tests 4, control-plane 1, integrations 3, docs 3, other 2 | — | alone (loads nothing)  — |
| `probes` | L1? | 1030 | runtime 7, tests 2, docs 4, other 1 | health, registry | with 2 runtime modules  — |
| `recipes` | L1? | 1813 | runtime 5, tests 2, control-plane 1, plugins 1, docs 4, other 1 | daily_reading, workchain, agent_control_store, daily_report, native_reader, paths, stat… | with intake  — |
| `ownership_gate` | L1? | 1050 | runtime 6, tests 2, control-plane 2, plugins 1, docs 1, other 1 | containers, hygiene, inventory, paths, probes, protection, registry | with 7 runtime modules  — |
| `report_delivery` | product | 732 | runtime 5, tests 3, plugins 1, docs 3, other 1 | daily_report | with daily_report  — |
| `approval` | L0 | 367 | runtime 2, tests 5, plugins 1, docs 1, other 3 | actor | with context  — |
| `cards` | product | 2346 | runtime 5, tests 2, plugins 1, docs 4 | presentation | with presentation  — |
| `fleet_monitor_source` | L2-hub | 1203 | runtime 3, tests 4, native 1, docs 4 | card_source, fleet_audience, fleet_monitor_card | with 3 runtime modules  — |
| `lease_store` | L1? | 469 | runtime 5, tests 4, docs 2, other 1 | environment, paths | with 2 runtime modules  — |
| `resource_anomaly` | L1? | 1710 | runtime 5, tests 3, plugins 1, docs 3 | anomaly, graph | with 2 runtime modules  — |
| `workbench_sources` | product | 659 | runtime 3, tests 2, scripts 1, plugins 1, docs 4, other 1 | paths | with paths  — |
| `card_day` | product | 1322 | runtime 4, tests 3, plugins 1, docs 3 | agent_control_store, business_calendar, card_delivery, registry | with 4 runtime modules  — |
| `continuation` | L0 | 1066 | runtime 4, tests 2, scripts 1, control-plane 2, plugins 1, docs 1 | — | alone (loads nothing)  — |
| `daily_report` | product | 1063 | runtime 6, tests 1, docs 2, other 2 | agent_control_store, alerting | with 2 runtime modules  — |
| `state_root` | L1? | 560 | runtime 5, tests 3, scripts 1, control-plane 1, other 1 | conformance, paths | with 2 runtime modules  — |
| `bounded_executor` | L1? | 516 | runtime 3, tests 3, integrations 1, docs 3 | — | alone (loads nothing)  — |
| `conformance` | L1? | 870 | runtime 3, tests 1, control-plane 1, plugins 1, docs 3, other 1 | paths, state_root | with 2 runtime modules  — |
| `document_transport` | L1? | 363 | runtime 6, tests 2, plugins 1, docs 1 | — | alone (loads nothing)  #407 |
| `fleet_monitor_card` | L2-hub | 1101 | runtime 3, tests 5, docs 2 | — | alone (loads nothing)  — |
| `presentation` | product | 2755 | runtime 5, tests 2, plugins 1, docs 2 | alert_digest, alerting, card_day | with 3 runtime modules  — |
| `view` | L1? | 1812 | runtime 4, tests 3, plugins 1, native 1, docs 1 | anomaly, attribution, containers, daemon, desired, environment, fs_hygiene, graph, heal… | with 21 runtime modules  — |
| `attribution` | L1? | 1524 | runtime 7, control-plane 1, docs 1 | — | alone (loads nothing)  — |
| `card_period` | product | 2128 | runtime 3, tests 3, docs 3 | agent_control_store, business_calendar, card_day, card_delivery, fleet_monitor_card, re… | with 6 runtime modules  — |
| `card_surface` | product | 980 | runtime 4, tests 2, docs 3 | agent_control_store, card_delivery, registry | with 3 runtime modules  — |
| `workspace` | L1? | 1061 | runtime 2, tests 4, control-plane 1, plugins 1, docs 1 | paths | with paths  — |
| `owner_policy` | L0 | 291 | runtime 2, tests 4, docs 2 | — | alone (loads nothing)  #407 |
| `containers` | L1? | 1658 | runtime 3, control-plane 1, docs 3 | attribution | with attribution  — |
| `fleet_mcp_owner` | L2-hub | 54 | tests 2, docs 5 | errors, fleet, runtime, agent_control_store, registry | with fleet_mcp  #407 |
| `fs_hygiene` | L1? | 2108 | runtime 3, plugins 1, docs 2, other 1 | paths | with paths  — |
| `human_console` | L1? | 723 | runtime 3, tests 1, control-plane 1, docs 2 | — | alone (loads nothing)  — |
| `monitoring_policy` | L1? | 751 | runtime 3, tests 3, plugins 1 | — | alone (loads nothing)  — |
| `protection` | L1? | 403 | runtime 3, tests 2, other 1 | anomaly, registry | with 2 runtime modules  — |
| `constrained_runner` | L1? | 790 | runtime 3, tests 1, docs 1 | — | alone (loads nothing)  — |
| `continuation_control` | L0 | 557 | tests 2, control-plane 1, plugins 1, docs 1 | delegation, wake | with context  — |
| `desired` | L1? | 529 | runtime 4, docs 1 | anomaly, registry | with 2 runtime modules  — |
| `direct_lark_notify` | product | 255 | runtime 1, tests 1, docs 3 | message_transport | with message_transport  — |
| `hygiene` | L1? | 695 | runtime 5 | attribution, inventory, paths, registry | with 4 runtime modules  — |
| `native_core_facts` | L1? | 987 | runtime 2, docs 2, ci 1 | — | alone (loads nothing)  — |
| `report_publication` | product | 317 | runtime 1, tests 1, plugins 1, docs 2 | — | alone (loads nothing)  — |
| `testing_contract` | L1? | 1429 | runtime 1, tests 2, plugins 1, other 1 | — | alone (loads nothing)  — |
| `card_surface_runner` | product | 729 | runtime 1, tests 1, docs 2 | alert_digest, business_calendar, card_period, card_source, card_surface, fleet_monitor_… | with 6 runtime modules  — |
| `environment` | L1? | 575 | runtime 3, tests 1 | ownership_gate | with ownership_gate  — |
| `launcher_guard` | L1? | 221 | runtime 1, tests 2, scripts 1 | credential_shapes | with credential_shapes  — |
| `managed_mutation` | L1? | 758 | runtime 2, docs 1, other 1 | ownership_gate, paths, state_root | with 3 runtime modules  — |
| `native_reader` | L1? | 175 | runtime 1, plugins 1, native 1, other 1 | — | alone (loads nothing)  — |
| `network_policy` | L1? | 736 | runtime 2, tests 1, other 1 | — | alone (loads nothing)  — |
| `snapshot_journal` | L1? | 195 | runtime 3, docs 1 | — | alone (loads nothing)  — |
| `card_shots` | product | 343 | runtime 1, tests 1, plugins 1 | card_scenarios | with card_scenarios  — |
| `fleet_core_bridge` | L2-hub | 551 | docs 3 | card_source, fleet_monitor_card, fleet_monitor_source | with 3 runtime modules  — |
| `fleet_audience` | L2-hub | 215 | runtime 2, docs 1 | — | alone (loads nothing)  — |
| `infrastructure` | L1? | 1924 | runtime 3 | attribution, graph, health, registry | with 4 runtime modules  — |
| `registration` | L1? | 788 | runtime 1, tests 1, scripts 1 | graph, registry | with 2 runtime modules  — |
| `resource_policy` | L1? | 525 | runtime 1, tests 1, other 1 | anomaly | with anomaly  — |
| `review_receipt` | L0 | 59 | tests 2, docs 1 | daily_cycle | with intake  — |
| `runtime_hygiene` | L1? | 392 | runtime 1, docs 2 | hygiene, registry | with 2 runtime modules  — |
| `surface_cli` | L1? | 328 | runtime 1, docs 2 | business_calendar, card_period, card_source, card_surface, card_surface_runner, live_se… | with 8 runtime modules  — |
| `card_scenarios` | product | 422 | runtime 1, plugins 1 | alert_digest, business_calendar, card_day, card_source, cards, presentation | with 6 runtime modules  — |
| `credential_shapes` | L1? | 176 | runtime 1, scripts 1 | — | alone (loads nothing)  — |
| `fleet_monitor_plan` | L2-hub | 391 | tests 1, docs 1 | card_delivery, fleet_audience | with 2 runtime modules  — |
| `fleet_recovery_events` | L2-hub | 285 | docs 2 | card_surface | with card_surface  — |
| `network_evidence` | L1? | 310 | runtime 1, other 1 | probes | with probes  — |
| `network_mutation` | L1? | 361 | runtime 2 | network_evidence, network_policy, ownership_gate, probes | with 4 runtime modules  — |
| `v2_role_session_preview` | L1? | 159 | tests 1, docs 1 | — | alone (loads nothing)  — |
| `agent_event_bridge` | L2-hub | 100 | tests 1 | — | alone (loads nothing)  — |
| `live_sender` | product | 401 | runtime 1 | message_transport | with message_transport  #407 |
| `ports` | L1? | 715 | runtime 1 | containers, graph, infrastructure | with 3 runtime modules  — |
| `surface_lock` | L1? | 143 | runtime 1 | — | alone (loads nothing)  — |

## Ready to move alone

26 module(s) load no other module, so a move rewires only their consumers. Ranked by consumer count; the ownership column, when given, is the gate that comes before dependency shape.

| module | layer | consumers | claimed |
|---|---|---:|---|
| `paths` | L1? | 56 | — |
| `coordination` | L0 | 33 | — |
| `registry` | L1? | 31 | — |
| `business_calendar` | product | 15 | — |
| `dispatch_attempts` | L0 | 15 | — |
| `agent_sessions` | L0 | 14 | — |
| `continuation` | L0 | 11 | — |
| `bounded_executor` | L1? | 10 | — |
| `document_transport` | L1? | 10 | #407 |
| `fleet_monitor_card` | L2-hub | 10 | — |
| `attribution` | L1? | 9 | — |
| `owner_policy` | L0 | 8 | #407 |
| `human_console` | L1? | 7 | — |
| `monitoring_policy` | L1? | 7 | — |
| `constrained_runner` | L1? | 5 | — |
| `native_core_facts` | L1? | 5 | — |
| `report_publication` | product | 5 | — |
| `testing_contract` | L1? | 5 | — |
| `native_reader` | L1? | 4 | — |
| `network_policy` | L1? | 4 | — |
| `snapshot_journal` | L1? | 4 | — |
| `fleet_audience` | L2-hub | 3 | — |
| `credential_shapes` | L1? | 2 | — |
| `v2_role_session_preview` | L1? | 2 | — |
| `agent_event_bridge` | L2-hub | 1 | — |
| `surface_lock` | L1? | 1 | — |

## Contracts and configs beside them

| file | lines | in | move |
|---|---:|---|---|
| `runtime/actuator-contract.yaml` | 1928 | runtime 2, tests 4, scripts 1, plugins 3, integrations 1, docs 5 | with <unparsed> |
| `runtime/agent-session-contract.yaml` | 300 | runtime 1, tests 3, control-plane 3, integrations 2, docs 8, other 1 | with <unparsed> |
| `runtime/approval-contract.yaml` | 140 | — | with <unparsed> |
| `runtime/attribution-contract.yaml` | 1385 | runtime 2, plugins 1, docs 2 | with <unparsed> |
| `runtime/card-definitions.yaml` | 413 | plugins 1, docs 1 | with <unparsed> |
| `runtime/card-scenario-reads.json` | 675 | runtime 1, plugins 1 | alone (loads nothing) |
| `runtime/card-scenarios.yaml` | 1514 | runtime 1, plugins 1, docs 2 | with <unparsed> |
| `runtime/constrained-test-runner-config.yaml` | 138 | tests 1, docs 1 | with <unparsed> |
| `runtime/environment-contract.yaml` | 181 | docs 1, other 1 | with <unparsed> |
| `runtime/fleet-monitor-card.fixture.json` | 243 | docs 1 | alone (loads nothing) |
| `runtime/fleet-monitor-device.fixture.json` | 89 | — | alone (loads nothing) |
| `runtime/fleet-monitor.fixture.json` | 95 | — | alone (loads nothing) |
| `runtime/managed-mutation-contract.yaml` | 202 | runtime 1 | with <unparsed> |
| `runtime/managed-runtime-contract.yaml` | 915 | docs 2, other 2 | with <unparsed> |
| `runtime/monitoring-policy.yaml` | 142 | runtime 2, plugins 1, docs 1 | with <unparsed> |
| `runtime/native-node-measurement.fixture.json` | 268 | — | alone (loads nothing) |
| `runtime/network-contract.yaml` | 415 | — | with <unparsed> |
| `runtime/notification-contract.yaml` | 868 | runtime 3, tests 2, plugins 1, docs 4, other 2 | with <unparsed> |
| `runtime/ownership-contract.yaml` | 327 | runtime 1, tests 1 | with <unparsed> |
| `runtime/periodic-report-contract.yaml` | 64 | runtime 2 | with <unparsed> |
| `runtime/recipe-contract.yaml` | 414 | runtime 2, tests 1, docs 2 | with <unparsed> |
| `runtime/report-publication-contract.yaml` | 177 | docs 1 | with <unparsed> |
| `runtime/resource-governance-contract.yaml` | 168 | docs 1 | with <unparsed> |
| `runtime/serveros-alert-sync.service` | 147 | runtime 1, tests 2, plugins 1, docs 3 | with <unparsed> |
| `runtime/serveros-alert-sync.timer` | 38 | runtime 1, tests 1, docs 3 | with <unparsed> |
| `runtime/serveros-continuation-wake.service` | 147 | tests 1, plugins 1 | with <unparsed> |
| `runtime/serveros-continuation-wake.timer` | 66 | tests 1 | with <unparsed> |
| `runtime/serveros-daily.service` | 114 | runtime 1, tests 1, plugins 1, docs 1 | with <unparsed> |
| `runtime/serveros-daily.timer` | 36 | tests 1 | with <unparsed> |
| `runtime/serveros-evening-alignment.service` | 167 | tests 1, plugins 1, docs 2 | with <unparsed> |
| `runtime/serveros-evening-alignment.timer` | 55 | tests 1, plugins 1 | with <unparsed> |
| `runtime/serveros-native-facts.service` | 88 | docs 1 | with <unparsed> |
| `runtime/serveros-wake-parent` | 258 | tests 2, scripts 1, plugins 1 | with <unparsed> |
| `runtime/serverosd.service` | 203 | runtime 2, tests 4, native 1, docs 1 | with <unparsed> |
