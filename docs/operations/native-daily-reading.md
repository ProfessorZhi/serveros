# 有限的日常阅读与工程续跑

`recipe run --recipe perception` 现在可以调用原生 Claude 阅读已有的固定版本来源。
采集仍由原 Source Provider 负责；没有材料变更时不需要调用模型。

在实例的 `state/recipes/model-binding.yaml` 中显式绑定：

```yaml
transport: native-claude-reader
wall_seconds: 120
max_cost_usd: 1.0
```

项目的 `context/sources.yaml` 可以声明已有卡片与已采集来源：

```yaml
sources:
  - id: project-work-doc
    kind: understanding
    card: information/understanding/projects/example.md
    manifest: feishu/chats/people/example/oc_example/links/work-doc/manifest.json
    grant_ref: grant:example-reading
```

`grant_ref` 可省略，此时只准备待对齐内容。Web 外置快照还需显式的
`source_asset_roots: [<absolute-asset-root>]`。项目入口复用已有的 recipe：

```text
serverosctl recipe run --recipe perception --scope project:example
  --period-start <ISO-with-offset> --period-end <ISO-with-offset>
  --budget 2 --run-dir <absolute-owned-artifact-directory> --json
```

Node/Item scope 可用 `--sources <absolute-yaml>` 指定绑定。跨时段复用同一个
阅读产物目录，已产生的 reading 和 invocation receipt 可复用；有歧义的调用不盲目重试。
模型仅收到所选 packet，关闭工具、插件、hooks、MCP 和其他自定义上下文。
文档引用由模型选行，控制器复制原始字节；IM 使用已读位置。超出输入预算明确拒绝，
不静默截断来源。写入前重新核对来源与卡片版本，使用已有 semantic writer。

`alignment.json` 从各 reading 的 resolution 生成，保留需要人决定的内容。
它不接受需求、不生成 Work Item，也不等于语义接受记录。接受前仍需版本核验。
一个事实的写入 receipt 完成，不表示同批提案已获接受。
同一 reading 里的事实已经写入后，可继续通过 `work understanding-semantic --commit --accepted ...`
接受仍在等待的 claim。每次确认追加独立收据，保留
原始收据及其哈希；只允许尚未提交的 claim，不能借此改写既有确认。来源必须仍是
原 revision，卡片只能发生已记录的 writer 变更；出现人工编辑时先重新阅读。
后续对齐列表读取这些收据，移除已提交项，复跑不调用模型或重复写入。

## 20:00 晚报：19:20 的准备必须真的送到人手上

19:20 只写出 `alignment.json`。一份没人看到的待决，在盘上和一份从来没被准备过的
待决长得一模一样，所以当晚还要把它变成一次真实投递：

```text
serverosctl report alignment --run-dir <19:20 的 run 目录> --out-dir <投递目录>
  --run-record <perception 的 run 记录> --period <当天> --json
serverosctl report request  --out-dir <投递目录> --recipient user:<open_id>
  --requested-by <谁要的> --why <为什么>
serverosctl report deliver  --out-dir <投递目录> --conversation user:<open_id>
  --authorization <刚刚复查了什么>
```

`report alignment` 只写两份东西：四段简报 `alignment-brief.md`，和指明这一版是什么的
`version.json`。它不发送任何东西，投递完全走已有的 `report request` / `report deliver`
——幂等键、回执、失败不谎报成功、被中断的尝试要去对账而不是重发，全部沿用同一条路径，
没有第二份拷贝。`report deliver` 认这个目录的 `version.json`，与它认日报的
`publication-receipt.json` 是同一个解析器。

管线分成两步而不是一步，是因为收件人、内容版本和授权都要在真正发送的那一刻重新查一次。
中途任何一处变了，投递都会被拒，而不是把一份没人要求过的内容发出去。

四段固定：**自动更新**（机器已按既有授权自己提交的）、**需要人决定**（人不点头就停在那里）、
**冲突**（新读解与卡片现状或另一次读解互相矛盾）、**理解缺口**（这一轮没能读到的来源）。
每一节都会把自己的条数写出来，空的也写「本轮没有……」：空的一节和没人检查过的一节，
在卡片上长得一样。

两处刻意不装作知道。其一，19:20 那次运行若没跑成，`alignment.json` 是旧的，
这时合成会被拒——否则收件人会收到一张和平时一样平静的卡，而它说的其实是昨天的事。
其二，运行若停在 `BUDGET_EXHAUSTED` / `SOURCE_UNAVAILABLE`，未走到的区间不会出现在
`unread` 里（没走到就没失败），所以缺口那一节会点名这次停在哪里，
而不是报一个「缺口：0」。

投递目录必须与 run 目录分开：run 目录每次运行都会被覆盖，而投递回执要活得比它久。
收件人由人指定（`--conversation`，或服务里的 `SERVEROS_REPORT_CONVERSATION`）；
没有指定时回答 `PENDING_CONSUMER`——一条待发，不是一次失败，也没有任何人被打扰。

### 定时器只调一条命令，三条判断在命令里

一个每晚 20:00 的单元不能自己算「今天是哪一天（北京）」「今晚的运行记录是哪一份」——
写进单元文件的一行 shell 没有东西能测，而它们**取错值时都不报错**。所以这三样在
`report evening` 里算：

```text
serverosctl report evening --run-dir <19:20 的目录> --record-dir <perception 运行记录目录>
  --out-root <投递根> --requested-by <谁要的> --why <为什么> [--conversation user:<open_id>]
```

它按北京日取当天目录（`<out-root>/<YYYY-MM-DD>/`），所以同一晚跑第二次落在**同一版**上，
而不是造出第二版。运行记录按**记录自己的 `finished_at`** 挑，不按文件 mtime：从备份还原、
手工拷贝或 `retake` 留下的记录，mtime 是今天而 `finished_at` 是昨天——按 mtime 挑，缺口
那一节就会由**另一天**的那一次来填，而填满的一节和真量过的一节长得一模一样。当天没有
记录时它宁可不填，缺口那一节照实写「没有量过」。

`--requested-by` 与 `--why` 是必填的，理由与 `report request` 相同：定时器触发不是「有人
要求过」。收件人没配置时，`report evening` **连待发意图都不记**，退出码 2——一份写好了
却没有任何人会被通知的简报，与一份送达的简报，在任何一张健康表上都必须是不同的两行。

### 重试不会变成第二封消息

幂等键取自**版本 sha256**（`runtime/report_delivery.py#intent_key`），所以这条成立与否
全看「同一晚跑两次是不是同一版」。简报的标题因此写的是**这一天**（`# 对齐 · 2026-10-06`），
不是渲染那一刻的墙上时钟：标题带分钟时，相隔五分钟的两次运行就是两个版本、两个意图，
而第二个意图会**真的再发一次**——本机实测过，投递通道被调用了两次，同一个人收到两张卡。
按天写的标题在整个晚上不变，于是第二次运行要么得到 `ALREADY_REQUESTED`（还没投出去），
要么由回执回答 `ALREADY_DELIVERED`（已经投出去了），两者都不再发送。
已经落定为 DELIVERED 的那一版再被 `report request` 问一次，也不会往 outbox 里再记一条
「待发」——那条永远不会被投递的记录会让 outbox 看上去还有事在排队。

`--at` 仍然可以指定，但定时器**不传**：它要问的就是「今天是哪一天」。

Focus 从 Work Engine 投影中的唯一 READY Activity 读取动作，从当前 Item card
计算 scope digest，再关联唯一有效 grant。多个可执行 Activity 或多个 grant 不自动择一。
scope 变化会被原有准入政策拒绝；旧投影的时间不会被重新标成当前时间。

有工程权限的 Parent 可调用 `continuation.execute(key, argv)`。镜像 digest、专属
worktree、内存/CPU/PID 上限来自已批准 grant；执行容器无网络、无宿主秘密与 Docker socket，
只挂载该 worktree 与当次产物目录。普通命令不能改变镜像或挂载。退出、超时、输出上限、
幂等重试和容器终止都有独立 receipt，命令退出不等于 Item 验收。

长期 grant 使用 Provider 对完整授权范围的签名，在签发时验证人类证据新鲜度；
后续仍检查有效期、撤销、预算和 epoch。旧的短期上下文不会自动升级为长期授权。
Provider trust binding 与真实的人类批准事件必须单独接好，源码支持不能记为 LIVE。
