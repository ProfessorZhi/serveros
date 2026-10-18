# Research Exchange — 协议实现

本目录是 [研究交接协议](../docs/architecture/RESEARCH-EXCHANGE.md) 的**协议实现**：契约 schema、
空模板、标准库结构检查器和它的机制测试。从原 `ProfessorZhi/serveros-research-exchange`
（提交 `bdde66b349076467c05d9c3848b7f54dc81a5558`）迁入；原仓库待验收后归档，
当前**尚未归档**。

## 材料包不放在这里

**真正的研究材料包（packets / inputs / results）今后放在私有的
`hwz-records/research-packets/`，不放进 `serveros`。** `serveros` 是 Public 仓库，
而材料包按定义可能包含获准传播的摘要、片段或分级数据；把两者放在同一个仓库里，会让
每一次普通的代码提交都承担数据分发的责任。本目录只保留**协议本身**——不含任何业务材料、
不含任何真实 packet。

边界因此是：

| 放什么 | 放哪里 |
|---|---|
| 契约 schema、模板、检查器、检查器的测试 | 本目录（`serveros`，Public） |
| 一次研究的 `packet.json`、`inputs/`、`results/` | 私有 `hwz-records/research-packets/` |

## 目录

```text
contracts/
  research-packet.schema.json   输入请求 manifest（research-packet.v1）
  research-result.schema.json   结果 manifest（research-result.v1）
templates/
  packet.json                   空模板，不是可执行请求
  brief.md                      给研究者的短入口
  result.json
  report.md
tools/check_packet.py           仅用标准库的结构/完整性检查器
tests/check_packet.py           检查器的机制测试（合成 fixtures）
```

packet 的布局约定（在私有仓库内）是：

```text
research-packets/<project>/<request-id>/
  packet.json
  brief.md
  inputs/                       明确允许分享的必要材料
  results/<result-id>/
    result.json                 对应输入 commit/digest、覆盖与限制
    report.md                   带引用的结果
```

## 使用

1. Parent 给研究任务绑定已有 project / Item / Run，准备短 brief。
2. 仅将明确允许分享的最小材料写进私有 `hwz-records/research-packets/<project>/<request-id>/inputs/`，
   填写 packet manifest，提交并固定输入 commit。
3. 把 commit、brief 位置和问题交给 GPT/Research Provider。先确认该实际入口能读到这一版；
   仓库存在不等于对方已连接。
4. 研究者输出带引用的 report 与 result manifest，写入该 packet 的 `results/<result-id>/`。
   读取能力与 GitHub 写回能力分别核对；没有写能力时由 CC 接收输出并提交。
5. Parent 检查版本、来源、覆盖及冲突，接受结果后才更新原正式记录或交付方案。

在研究确有必要时才创建材料包；普通本地/联网调查可以直接使用 Native Agent。不要复制全部
Session 历史或业务仓库。

## 结构检查

`tools/check_packet.py` 检查 `contracts/` 下的 schema，并执行 schema 之外的语义检查。
它对**任意** packet 目录工作，因此可以直接指向 `hwz-records` 的私有 checkout：

```sh
# 只检查 packet：schema、相对路径安全、inputs 的 SHA256
python3 research-exchange/tools/check_packet.py \
  --packet /path/to/hwz-records/research-packets/<project>/<request-id>/packet.json

# 连同 result 检查：request 对应、report_path、与实际传入 commit 的版本匹配
python3 research-exchange/tools/check_packet.py \
  --packet /path/to/hwz-records/research-packets/<project>/<request-id>/packet.json \
  --result /path/to/hwz-records/research-packets/<project>/<request-id>/results/<result-id>/result.json \
  --input-revision <40-hex-commit>
```

输出为单行 JSON，通过时退出码 0，否则非零。路径相对 packet 目录解析，拒绝绝对路径、
`..`、符号链接组件和目录逃逸；`report_path` 必须位于该 packet 的 `results/` 内且存在。
预算要求 wall/output 为正、工具调用与费用非负，拒绝 NaN 和用布尔冒充整数。空必填
（`templates/` 里的空模板）会被拒绝。

边界说明：检查器只对 `data_policy` 标签和 `permission_ref` 做格式检查，**不授予传播
权限、不做脱敏、也不验证任何真人批准**；只有调用者显式传入 `--input-revision` 且与
result 一致时才把它标记为版本匹配已验证，否则不会声称匹配通过。`fixtures` 与模板都是
机制验证，不是业务证据。

测试：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 research-exchange/tests/check_packet.py -v
```

测试文件名为 `check_packet.py`，而不是 `test_*.py` 或 `*_test.py`：它在仓库根
`tests/` 之外，若按 pytest 的默认收集 glob 命名，裸 `pytest` 会把整个文件当测试收集，
与 `pytest.ini` 的 `testpaths = tests` 和 `scripts/verify.py` 的 census 不一致
（`tests/test_collection_scope.py` 守着这条边界）。因此它**不进入** `pytest` 默认套件，
按上面的命令单独运行。

## 权威规范

- [ServerOS 当前目标架构](../docs/architecture/NATIVE-WORK-ENVIRONMENT.md)
- [研究交接协议](../docs/architecture/RESEARCH-EXCHANGE.md)
- [CC 执行计划](../docs/plans/plan-01-native-work-environment.md)

输入提交固定后不覆盖结果来假装幂等；新结论用新 result-id 或明确修订。结果中的建议和
APPROVE 文字都不是人类授权。

## 数据范围

仅可存已获准传播的摘要、公开资料引用、必要的获准代码片段、脱敏聚合和研究结果。禁止凭据、
Cookie、生产 raw prompt/response、完整日志/ES export、未获准的人员或商业数据。

生产原文留在原系统/受控数据层；材料包用安全 locator 引用。私有仓库本身不等于数据可外传的
批准。
