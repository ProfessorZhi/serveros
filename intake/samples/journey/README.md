# SAMPLE — 一条旅程的合成输入，以及它**该在哪里停下来**

这个目录是 fixture，不是本节点的业务状态。`oc_sample` 这个会话、`om_sample_0001` 这条
消息、`person:vince` 这个作出者都不指向任何真实对象；`sha256:0000…0001` 是一个**明显
不是**真摘要的占位符，这样它就不会被误读成某条真实消息的指纹。

它不是代码的 mock。`serverosctl journey walk` 对着它**零 stub** 跑：真的 B1 捕获、真的
B3 候选与决定门槛、真的 A7 观察判词、真的由 `runtime/daily_report.py` 组合与落盘、真的
由 `runtime/report_delivery.py` 记一条待发意图。

```bash
# 一条来源，没有人提出目标 —— 到 B1 为止
serverosctl journey walk \
  --input intake/samples/journey/01-capture-only.json \
  --out-dir "$ARTIFACTS_ROOT/journey-sample/01" --at 2026-10-14T10:00:00+09:00

# 有一个目标，还没有人的决定 —— 候选没有 work_ref
serverosctl journey walk \
  --input intake/samples/journey/02-needs-human.json \
  --out-dir "$ARTIFACTS_ROOT/journey-sample/02" --at 2026-10-14T10:00:00+09:00

# 决定、观察、报告、待发 —— 走到 B10，但**不投递**
serverosctl journey walk \
  --input intake/samples/journey/03-accepted.json \
  --out-dir "$ARTIFACTS_ROOT/journey-sample/03" --at 2026-10-14T10:00:00+09:00
```

## 三个输入各自要让哪一条停下来

| 输入 | `stop` | 它在这里要证明什么 |
|---|---|---|
| `01-capture-only.json` | `CAPTURED_ONLY` | 捕获**不是**立项。一条消息读进来，没有人给出目标，链就停在 B1，`refs` 里没有 `requirement_ref`。 |
| `02-needs-human.json` | `NEEDS_HUMAN` | 候选成形且输入成立，但**只差一个人的答复**。`refs` 里有 `requirement_ref`，**没有** `work_ref` —— 这一条是「不得未批准自动立项」在数据上的样子。 |
| `03-accepted.json` | `PENDING` | 有人接受了、观察通过了、报告落盘了、待发意图记下了。`PENDING` 不是失败：`--out-dir` 之外没有消费者，而「没有人试过」与「发失败了」是两件事。 |

## `03` 里那个 `for_revision` 是什么

`c02c82d563f3` 是对 `feishu:chat:oc_sample|om_sample_0001@sha256:0000…0001` 算出来的
来源版本摘要。它写在这里是**故意的**：一次人类决定说的是「我审的是**这一版**」，而不是
「我审的是这条需求」。所以同一批消息只要改过一个字，摘要就变，而这条旧决定会得到
`NEEDS_HUMAN` 而不是被套在新内容上——这正是 `docs/architecture/NATIVE-WORK-ENVIRONMENT.md`
§3 那条「来源版本变化后重新检查受影响决定」在收据链上的落点。

## 这里**没有**被验证的两件事，以及为什么

- **真实飞书投递。** `journey walk` 的 `sender` 恒为 `None`：在这台机器上真实投递不在
  授权范围内，而一个把「没发」渲染成「发好了」的 CLI 正是这个模块全部拒绝行为要防的事。
  投递的四个词（`DELIVERED/FAILED/UNSUPPORTED/UNKNOWN`）由 `tests/test_journey.py` 用注入
  的 sender 逐个验证——那条缝就是 `runtime/report_delivery.py` 自己设计的 `sender` 参数。
- **真实工作树差量。** 不给 `--worktree` 时报告写的是「没人看过」这句话，而不是
  「本周期没有提交」。两者在读者的眼里必须不同，所以 `03` 刻意不给：它是一条**合成**
  的旅程，而合成的东西不该长出一份真实工作的差量。
