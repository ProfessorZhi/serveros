# SAMPLE — 一条旅程的合成输入，它**该在哪里停下来**，以及什么**不能**让它继续

这个目录是 fixture，不是本节点的业务状态。`oc_sample` 这个会话、`om_sample_0001` 这条
消息、`feishu:union:on_sample` 这个主体都不指向任何真实对象；`sha256:0000…0001` 是一个
**明显不是**真摘要的占位符，这样它就不会被误读成某条真实消息的指纹。

它不是代码的 mock。`serverosctl journey walk` 对着它**零 stub** 跑：真的 B1 捕获、真的
B3 候选、真的 A7 观察判词、真的由 `runtime/daily_report.py` 组合与落盘、真的由
`runtime/report_delivery.py` 记一条待发意图。

## 生产 CLI 不能授权任何东西，而这是设计

```bash
serverosctl journey walk \
  --input intake/samples/journey/03-claimed-human.json \
  --out-dir "$ARTIFACTS_ROOT/journey-sample/03" --at 2026-10-14T10:00:00+09:00
```

`journey walk` 的 `verifier` 恒为 `None`，所以输入里那个 `approval_claim`——哪怕写着
`kind: human`、`by`、`trusted: true`、`for_revision`——得到的答案是 **`NEEDS_HUMAN`**，
铸造不出 `work_ref`。

这不是「还没接完」。`context/actor-context-contract.yaml` 的第一句话就是理由：
**identity is a lookup, not an argument**。一个能宣称自己背后有人的 Agent 运行时，可以为
它此后的每一次变更背书，而它背后每一道准入闸门都变成了装饰。所以调用方 JSON 里的
`kind` 只有两种去向：**具名拒绝**，或者**送去验证**——没有第三种。

| 输入 | `stop` / 退出码 | 它在这里要证明什么 |
|---|---|---|
| `01-capture-only.json` | `CAPTURED_ONLY` / 0 | 捕获**不是**立项。没有人给出目标，链停在 B1。 |
| `02-needs-human.json` | `NEEDS_HUMAN` / 0 | 候选成形，但**没有人的批准**。有 `requirement_ref`，**无** `work_ref`。 |
| `03-claimed-human.json` | `NEEDS_HUMAN` / 0 | 输入**自称**有人批准了。没有绑定的验证器，这个声称换不成一条记录——所以它和 `02` 一样停在同一个地方。 |
| `04-child-done-is-not-authorization.json` | 具名拒绝 / **1** | 一个 `child-done` 的来源：它不是格式错误，它只是**不产生授权**。 |

## MOCK accepted 与生产安全 CLI 的区别

被接受的路径**必须**有一份被验证过的批准，而在这台机器上没有任何真实来源可以给出它。
所以两侧是分开的，且分得很清楚：

| | 谁跑 | 验证器 | 结果 |
|---|---|---|---|
| **生产 CLI** | `serverosctl journey walk` | 恒为 `None` | 任何 `approval_claim` → `NEEDS_HUMAN` |
| **生产入口（未通）** | `runtime/journey.py#signed_delegation_provider` | `context/actor.py#verify_delegation`（Ed25519 签名委派） | 本机 **BLOCKED**：venv 里没有 `cryptography`，且 `~/.config/serveros/human-control-trust.json` 不存在 |
| **该入口被真正跑通过** | `pytest tests/test_journey_provider.py` | 同上——**未修改的** `verify_delegation` | 确定性：签名原语与信任根**位置**被 fake（`tests/journey_provider_fixture.py`），协议本身的每一道判据都是真的 |
| **MOCK** | `pytest tests/test_journey.py` | 注入一个**独立 fake** | 走完 B3→B4→A7→B8→B10，收据链七条齐 |

MOCK 那一栏里，fake 验证器返回的记录不是测试自己编的 dict——它是用
`context/actor.py#build` **真的造出来**的，所以这条链对身份的判断是被那个模块自己的
`validate()` 与 `authorizes()` 判的。这一点是可核对的，也是这个 fixture 目录里**没有**
一个「已接受」样例的原因：一个能在生产 CLI 上跑出 `ACCEPTED` 的样例，本身就是一个可以被
拿去当批准用的东西。

## 这里**没有**被验证的两件事，以及为什么

- **真实飞书投递。** `journey walk` 的 `sender` 恒为 `None`：真实投递不在授权范围内，
  而一个把「没发」渲染成「发好了」的 CLI 正是这个模块全部拒绝行为要防的事。投递的四个词
  （`DELIVERED/FAILED/UNSUPPORTED/UNKNOWN`）由 `tests/test_journey.py` 用注入的 sender
  逐个验证——那条缝就是 `runtime/report_delivery.py` 自己设计的 `sender` 参数。
- **真实工作树差量。** 不给 `--worktree` 时报告写的是「没人看过」这句话，而不是
  「本周期没有提交」。两者在读者的眼里必须不同。
