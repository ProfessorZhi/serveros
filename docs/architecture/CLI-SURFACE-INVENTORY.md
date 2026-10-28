# `serverosctl` 表面清单

`runtime/cli.py` 里有什么、谁在用它、以及一次拆分必须保住什么。
本文件是 **#429**（Fleet V1 D2/D3/D4/D10 目录收敛）里 D10 的只读清点快照，
**不是重构决定**：D10 的顺序写明是「在 D2/D3 之后，先收集子命令/输出 parity 和拟拆入口，
不盲目重构」。这里只放可复核的数字和由此得出的次序。

- 快照：`runtime/cli.py` 的 blob 在 `main @ e69fc03`、#438 与 #439 的 tip 上**是同一个**
  （`d5fca83`），所以下面每个数字对这三个 ref 同时成立 —— D2/D3 都没有碰过这个文件
- 生成（下半部分是原样输出，未手工编辑）：
  ```bash
  python scripts/cli_surface_inventory.py --with-calls
  ```
- 冻结基线：`docs/architecture/cli-surface-floor-20261028.json`（由
  `python scripts/cli_surface_inventory.py --write-floor <该文件>` 生成）
- 门禁：`tests/test_cli_surface_contract.py`（16 条）

## 六条结论

1. **表面比源码读起来大 22%。** 源码里能按字面认出的动词是 136 个，真实 parser 里是 **164 个**。
   差的 28 个来自三处**用数据表/循环**声明动词的地方（`lease`、`test`、`architecture`
   各有一个本地 helper，逐条 `add_parser(name, ...)`，`name` 是变量不是字面量）。
   ⇒ 任何「grep 一下看谁在用这个动词」的判断都会漏掉整个组；
   **表面的唯一权威是 `build_parser()` 返回的那棵树**，`docs/architecture/cli-surface-floor-20261028.json`
   因此从 parser 取，不从源码取。

2. **这个文件不是叶子，是组装根。** 96 处 `_load_module_from` 伸进 7 个顶层目录：
   `intake` 44、`runtime` 26、`context` 10、`abilities` 6、`space` 3、`memory` 3、`documents` 1。
   ⇒ 「把 handler 挪出去」挪的不是一段命令代码，而是一个**在运行时按 `REPO_ROOT` 相对路径
   加载别的层**的模块。它在文件里的深度就是它的定位方式（`REPO_ROOT / "intake" / ...`），
   所以任何一次搬家都要么保持深度，要么把这 96 处一起改成显式注入。

3. **兼容面在进程边界，不在模块边界。** `bin/serverosctl` 只做三件事：把 `runtime/`
   插进 `sys.path`、`import cli`、`sys.exit(cli.main())`。拆成几个文件都可以，
   只要 `import cli; cli.main()` 仍然是入口。这是整件事最便宜的一半，也是唯一必须提前钉住的一半。

4. **164 个动词没有一个是死的。** 38 个顶层命令在树里**全部**被调用过：195 个文件、
   660 次调用（docs 176、tests 130、runtime 112、workbench 99、integrations 39 …）。
   ⇒「先把没人用的子命令删掉再拆」这条路不存在，一次拆分是**全量搬迁**。

5. **瓶颈不是 164 个动词，是那 27% 的共享前置。** 16 144 行里：
   - `build_parser()` 占 2 871 行（17.8%），
   - handler 占 8 909 行（55.2%，按函数去重；`cmd_work_trust` 服务 3 个动词、
     `cmd_workspace` 服务 5 个），
   - 其余 **4 364 行（27.0%）是模块级共享前置**：加载器（`_load`、`_load_module_from`）、
     `_portable_stdout`、错误到退出码的映射、常量、以及几十个 `_` 开头的 helper。
   每个 handler 都调用它们，而它们现在**只存在于这一个模块里**。
   ⇒ D10 的第一件事不是拆动词，而是把这段前置变成可 import 的库（一个独立的、需要单独决策的改动，
   不是目录迁移）。

6. **拟拆次序由依赖决定，不由行数决定**（下表）。

## 拟拆入口（按依赖，不按行数）

| 家族 | 动词 | handler 行数 | 树内调用 | 依赖 | 何时可动 |
|---|---:|---:|---:|---|---|
| product（`card` / `alert` / `report` / `workbench`） | 29 | 1 419 | 136 | `card_*`、`card_delivery`、`presentation`、`daily_report`、`report_delivery`、`message_transport` —— **正是 D3 要搬的那批** | D3 阶段 2 之后（同一条 seam） |
| `work` | 32 | 3 052 | 138 | `intake` 23 处 ＋ `runtime/agent_control_store.py`（**#407 持有**） | D4 之后 |
| host/runtime 观测（`runtime` / `resource` / `device` / `process` / `anomaly` / `container` / `fs` / `tunnel`） | 30 | 1 321 | 65 | 只读 `runtime/*`，无 product、无 D4 | **最先可动**（收益也最小） |
| 其余（`space` / `context` / `memory` / `path` / `ability` / `information` …） | 73 | 3 543 | 282 | 各自的层，逐个看 | 随各自层 |

`work` 一组的数字值得单独说：**32 个动词、3 052 行、138 次调用、23 处 intake 加载**。
它是这个文件里最大的连续块，也是唯一一块动它就会同时撞上 #407 与 D4 的块。

## 方法边界（这些数字看不见什么）

- **动词集合取自真实 parser**（`build_parser()`），因此包含数据表声明的 28 个；
  **行数、handler 归属、跨层加载取自源码 AST**，因此只能看见字面量形式的那部分 ——
  两者之差本身就是结论 1，不是缺陷。
- **两处 `_load_module_from` 的路径是运行时算出来的**（`(path)` 与 `(_HERE)` 各若干），
  静态不可知，表里单列。它们意味着「cli 不动态加载」这句话**不成立**。
- **调用面是文本计数**（`git grep` 命中的 `serverosctl …` / `cli.py …`），
  看得见写下来的命令，看不见从配置里拼出来的命令。所以 660 是**下界**。
- 归属与快照同 `RUNTIME-CUSTODY-MATRIX.md`：每小时会变，**引用前先重跑**。

---

以下为生成输出，未手工编辑。

# serverosctl 表面清单（D10 · 只读）

- 文件：`runtime/cli.py` —— 16144 行 / 843288 字节
- 解析器：`build_parser()` L13209–L16079，2871 行（全文 17.8%）
- 处理函数：152 个不同函数，8909 行（55.2%）；其中 6 个被多个子命令复用
- 动词：真实 parser 里 164 个叶子动词，38 个顶层，最深 3 层
- 源码直读：172 次 `add_parser`，其中 136 个动词能在源码里按字面认出来；**28 个看不见**（名字来自数据表/循环：architecture、card、lease、mutation、network、support、taxonomy、test）
- 全局开关：--json --registry 声明在 `add_help=False` 的共享父 parser 上，因此每个子命令都继承；handler 里读 `args.json` 共 179 处
- 开放尾巴：`work dispatch`, `workengine`（`accepts_unknown_argv`：其后所有 token 原样转交）
- 退出码：EXIT_OK=0、EXIT_FAULT=1、EXIT_COULD_NOT_ANSWER=2、EXIT_REFUSED=3
- 跨层加载：96 处 `_load_module_from` —— intake 44、runtime 26、context 10、abilities 6、space 3、memory 3、动态路径(path)（静态不可知） 2、documents 1、动态路径(_HERE)（静态不可知） 1
- 调用面：195 个文件、660 次调用（不含 cli.py 自身）
  - 按目录：docs 176、tests 130、runtime 112、workbench 99、integrations 39、intake 16、plugin 16、skills 16、fleet 15、abilities 10、context 9、mcp 9、scripts 8、space 2、(root) 1、memory 1、workengine 1

## 每个顶层命令的字数与调用面

handler 行数按**动词**累加，复用同一个函数的动词会重复计（上面的总数是按函数去重的）。

| 命令 | 动词 | handler 行数 | 调用次数 |
|---|---:|---:|---:|
| `work` | 32 | 3052 | 138 |
| `card` | 11 | 560 | 49 |
| `runtime` | 13 | 490 | 6 |
| `alert` | 10 | 471 | 54 |
| `ability` | 6 | 462 | 64 |
| `report` | 6 | 304 | 11 |
| `resource` | 7 | 269 | 6 |
| `information` | 3 | 258 | 13 |
| `document` | 3 | 256 | 3 |
| `registration` | 3 | 250 | 4 |
| `test` | 5 | 227 | 12 |
| `space` | 2 | 221 | 17 |
| `recipe` | 3 | 211 | 11 |
| `device` | 2 | 201 | 41 |
| `workspace` | 5 | 200 | 2 |
| `memory` | 4 | 183 | 9 |
| `pack` | 1 | 169 | 1 |
| `taxonomy` | 4 | 160 | 13 |
| `requirement` | 1 | 148 | 17 |
| `lease` | 5 | 126 | 4 |
| `context` | 2 | 118 | 12 |
| `anomaly` | 2 | 99 | 3 |
| `support` | 5 | 96 | 7 |
| `fs` | 1 | 92 | 3 |
| `coordination` | 4 | 89 | 4 |
| `console` | 3 | 84 | 3 |
| `workbench` | 2 | 84 | 22 |
| `process` | 2 | 83 | 2 |
| `status` | 1 | 73 | 42 |
| `path` | 4 | 60 | 17 |
| `container` | 2 | 58 | 2 |
| `mutation` | 2 | 48 | 3 |
| `ownership` | 1 | 34 | 3 |
| `tunnel` | 1 | 29 | 2 |
| `network` | 2 | 28 | 3 |
| `probe` | 1 | 19 | 5 |
| `workengine` | 1 | 17 | 3 |
| `architecture` | 2 | 6 | 10 |

## 全部绑定路径

| 路径 | handler | 解析器行 | handler 行 |
|---|---|---:|---:|
| `ability inspect` | `cmd_ability_inspect` | 13725 | 52 |
| `ability list` | `cmd_ability_list` | 13709 | 44 |
| `ability refresh` | `cmd_ability_refresh` | 13767 | 116 |
| `ability resolve` | `cmd_ability_resolve` | 13731 | 116 |
| `ability status` | `cmd_ability_status` | 13713 | 41 |
| `ability tree` | `cmd_ability_tree` | 13717 | 93 |
| `alert ack` | `cmd_alert_ack` | 14464 | 18 |
| `alert current` | `cmd_alert_current` | 14452 | 55 |
| `alert digest` | `cmd_alert_digest` | 14494 | 44 |
| `alert digest-account` | `cmd_alert_digest_account` | 14520 | 91 |
| `alert digest-baseline` | `cmd_alert_digest_baseline` | 14501 | 19 |
| `alert digest-record` | `cmd_alert_digest_record` | 14507 | 66 |
| `alert inspect` | `cmd_alert_inspect` | 14458 | 26 |
| `alert list` | `cmd_alert_list` | 14446 | 37 |
| `alert snooze` | `cmd_alert_snooze` | 14473 | 19 |
| `alert sync` | `cmd_alert_sync` | 14482 | 96 |
| `anomaly explain` | `cmd_anomaly_explain` | 13665 | 55 |
| `anomaly list` | `cmd_anomaly_list` | 13658 | 44 |
| `architecture check` | `cmd_architecture_check` | None | 3 |
| `architecture project` | `cmd_architecture_project` | None | 3 |
| `card alert` | `cmd_card_alert` | None | 3 |
| `card day` | `cmd_card_day` | 14229 | 40 |
| `card day-advance` | `cmd_card_day_advance` | 14249 | 43 |
| `card day-announce` | `cmd_card_day_announce` | 14281 | 42 |
| `card deliveries` | `cmd_card_deliveries` | 14188 | 37 |
| `card fault` | `cmd_card_fault` | 14354 | 111 |
| `card incident` | `cmd_card_incident` | 14201 | 39 |
| `card notice` | `cmd_card_notice` | 14310 | 189 |
| `card record-delivery` | `cmd_card_record_delivery` | 14148 | 50 |
| `card recovery` | `cmd_card_recovery` | None | 3 |
| `card status` | `cmd_card_status` | None | 3 |
| `console operations` | `cmd_console_operations` | 14057 | 36 |
| `console select` | `cmd_console_select` | 14041 | 24 |
| `console selection` | `cmd_console_selection` | 14051 | 24 |
| `container attribution` | `cmd_container_attribution` | 13674 | 29 |
| `container protection` | `cmd_container_protection` | 13677 | 29 |
| `context current` | `cmd_context_current` | 13288 | 40 |
| `context startup` | `cmd_context_startup` | 13290 | 78 |
| `coordination bind` | `cmd_coordination_bind` | 14013 | 27 |
| `coordination close` | `cmd_coordination_close` | 14026 | 20 |
| `coordination list` | `cmd_coordination_list` | 14032 | 13 |
| `coordination resolve` | `cmd_coordination_resolve` | 14007 | 29 |
| `device agents` | `cmd_device_agents` | 13272 | 169 |
| `device show` | `cmd_device_show` | 13270 | 32 |
| `document publish` | `cmd_document_publish` | 15756 | 140 |
| `document resolve` | `cmd_document_resolve` | 15710 | 70 |
| `document stewardship` | `cmd_document_stewardship` | 15727 | 46 |
| `fs hygiene` | `cmd_fs_hygiene` | 13688 | 92 |
| `information captured` | `cmd_information_captured` | 15812 | 98 |
| `information reference` | `cmd_information_reference` | 15847 | 51 |
| `information watched` | `cmd_information_watched` | 15823 | 109 |
| `lease declare` | `cmd_lease_declare` | None | 16 |
| `lease environment` | `cmd_lease_environment` | None | 40 |
| `lease inspect` | `cmd_lease_inspect` | None | 19 |
| `lease list` | `cmd_lease_list` | None | 22 |
| `lease release` | `cmd_lease_release` | None | 29 |
| `memory audit` | `cmd_memory_audit` | 13863 | 27 |
| `memory project` | `cmd_memory_project` | 13869 | 56 |
| `memory status` | `cmd_memory_status` | 13881 | 38 |
| `memory subject` | `cmd_memory_subject` | 13889 | 62 |
| `mutation plan` | `cmd_mutation` | None | 24 |
| `mutation run` | `cmd_mutation` | None | 24 |
| `network create` | `cmd_network` | None | 14 |
| `network preflight` | `cmd_network` | None | 14 |
| `ownership check` | `cmd_ownership` | 13943 | 34 |
| `pack inspect` | `cmd_pack_inspect` | 13802 | 169 |
| `path admit` | `cmd_path_admit` | 13301 | 17 |
| `path conformance` | `cmd_path_conformance` | 13308 | 25 |
| `path contract` | `cmd_path_contract` | 13306 | 8 |
| `path resolve` | `cmd_path_resolve` | 13296 | 10 |
| `probe` | `cmd_probe` | 13998 | 19 |
| `process top` | `cmd_process_top` | 13642 | 20 |
| `process trace` | `cmd_process_trace` | 13647 | 63 |
| `recipe list` | `cmd_recipe_list` | 15520 | 43 |
| `recipe run` | `cmd_recipe_run` | 15526 | 132 |
| `recipe status` | `cmd_recipe_status` | 15603 | 36 |
| `registration discover` | `cmd_registration_discover` | 14381 | 98 |
| `registration plan` | `cmd_registration_plan` | 14398 | 74 |
| `registration resolve` | `cmd_registration_resolve` | 14435 | 78 |
| `report alignment` | `cmd_report_alignment` | 15354 | 40 |
| `report daily` | `cmd_report_daily` | 15302 | 88 |
| `report deliver` | `cmd_report_deliver` | 15486 | 58 |
| `report evening` | `cmd_report_evening` | 15386 | 91 |
| `report request` | `cmd_report_request` | 15439 | 17 |
| `report withdraw` | `cmd_report_withdraw` | 15472 | 10 |
| `requirement board` | `cmd_requirement_board` | 14547 | 148 |
| `resource anomaly` | `cmd_resource_anomaly` | 13414 | 60 |
| `resource containers` | `cmd_resource_docker_list` | 13393 | 15 |
| `resource docker` | `cmd_resource_docker` | 13405 | 38 |
| `resource ports` | `cmd_resource_ports` | 13398 | 44 |
| `resource summary` | `cmd_resource_summary` | 13387 | 82 |
| `resource top` | `cmd_resource_top` | 13389 | 17 |
| `resource user-services` | `cmd_resource_user_services` | 13396 | 13 |
| `runtime budget` | `cmd_runtime_budget` | 13622 | 28 |
| `runtime classify` | `cmd_runtime_classify` | 13632 | 27 |
| `runtime desired` | `cmd_runtime_desired` | 13626 | 24 |
| `runtime egress` | `cmd_runtime_egress` | 13593 | 43 |
| `runtime explain` | `cmd_runtime_explain` | 13573 | 35 |
| `runtime group` | `cmd_runtime_group` | 13611 | 25 |
| `runtime groups` | `cmd_runtime_groups` | 13608 | 20 |
| `runtime hygiene` | `cmd_runtime_hygiene` | 13576 | 41 |
| `runtime infrastructure` | `cmd_runtime_infrastructure` | 13582 | 63 |
| `runtime list` | `cmd_runtime_list` | 13566 | 25 |
| `runtime status` | `cmd_runtime_status` | 13568 | 52 |
| `runtime tree` | `cmd_runtime_tree` | 13615 | 64 |
| `runtime tunnels` | `cmd_runtime_tunnels` | 13588 | 43 |
| `space meeting` | `cmd_space_meeting` | 15887 | 142 |
| `space resolve` | `cmd_space_resolve` | 15870 | 79 |
| `status` | `cmd_status` | 13263 | 73 |
| `support answer` | `cmd_support_answer` | None | 13 |
| `support check` | `cmd_support_check` | None | 16 |
| `support entries` | `cmd_support_entries` | None | 18 |
| `support handoff` | `cmd_support_handoff` | None | 36 |
| `support match` | `cmd_support_match` | None | 13 |
| `taxonomy search` | `cmd_taxonomy` | None | 40 |
| `taxonomy show` | `cmd_taxonomy` | None | 40 |
| `taxonomy tree` | `cmd_taxonomy` | None | 40 |
| `taxonomy validate` | `cmd_taxonomy` | None | 40 |
| `test contract` | `cmd_test_contract` | None | 24 |
| `test project` | `cmd_test_project` | None | 46 |
| `test projects` | `cmd_test_projects` | None | 40 |
| `test resolve` | `cmd_test_resolve` | None | 73 |
| `test run` | `cmd_test_run` | None | 44 |
| `tunnel list` | `cmd_tunnel_list` | 13698 | 29 |
| `work accept` | `cmd_work_accept` | 14877 | 52 |
| `work admissions` | `cmd_work_admissions` | 14788 | 76 |
| `work admit` | `cmd_work_admit` | 14763 | 93 |
| `work board` | `cmd_work_board` | 14618 | 24 |
| `work checkpoint` | `cmd_work_checkpoint` | 14896 | 131 |
| `work claim-recall` | `cmd_work_claim_recall` | 15168 | 36 |
| `work confirm` | `cmd_work_confirm` | 14794 | 61 |
| `work continuations` | `cmd_work_continuations` | 14937 | 292 |
| `work coverage` | `cmd_work_coverage` | 15052 | 91 |
| `work delegate` | `cmd_work_delegate` | 14703 | 179 |
| `work dispatch` | `cmd_work_dispatch` | 14814 | 244 |
| `work focus` | `cmd_work_focus` | 14628 | 218 |
| `work grants` | `cmd_work_grants` | 14640 | 166 |
| `work issue-grant` | `cmd_work_issue_grant` | 14652 | 12 |
| `work item` | `cmd_work_item` | 15024 | 158 |
| `work material-index` | `cmd_work_material_index` | 15213 | 56 |
| `work park` | `cmd_work_park` | 14953 | 60 |
| `work retire` | `cmd_work_retire` | 14866 | 45 |
| `work revoke` | `cmd_work_revoke` | 14749 | 63 |
| `work skeleton` | `cmd_work_skeleton` | 15044 | 83 |
| `work source-policy` | `cmd_work_source_policy` | 15197 | 68 |
| `work today` | `cmd_work_today` | 15032 | 34 |
| `work trust bind` | `cmd_work_trust` | 14672 | 33 |
| `work trust disable` | `cmd_work_trust` | 14694 | 33 |
| `work trust status` | `cmd_work_trust` | 14663 | 33 |
| `work understanding` | `cmd_work_understanding` | 15062 | 85 |
| `work understanding-brief` | `cmd_work_understanding_brief` | 15092 | 112 |
| `work understanding-patch` | `cmd_work_understanding_patch` | 15073 | 71 |
| `work understanding-semantic` | `cmd_work_understanding_semantic` | 15117 | 150 |
| `work unpark` | `cmd_work_unpark` | 14971 | 55 |
| `work wake` | `cmd_work_wake` | 14987 | 194 |
| `work waterline` | `cmd_work_waterline` | 15187 | 44 |
| `workbench check` | `cmd_workbench` | 15293 | 42 |
| `workbench render` | `cmd_workbench` | 15288 | 42 |
| `workengine` | `cmd_workengine` | 16027 | 17 |
| `workspace drift` | `cmd_workspace` | 16012 | 40 |
| `workspace inspect` | `cmd_workspace` | 16006 | 40 |
| `workspace locate` | `cmd_workspace` | 15995 | 40 |
| `workspace overview` | `cmd_workspace` | 15973 | 40 |
| `workspace policy` | `cmd_workspace` | 15984 | 40 |
