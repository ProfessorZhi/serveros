<!-- ⟦snapshot⟧ rendered 2026-09-27T06:51:41+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/serveros-completion-20260925 · inputs: projects=serveros,litellm,multi-user-claw; project=litellm; item=LIT-0101; quay-run=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/quay-run; quay-bin=~/workspace/worktrees/quay/foundation-skeleton/plugin/vendor/quay/dist/quay.js; ability-repo=~/workspace/src/ability-management; reports-dir=~/artifacts/work-items/serveros/SRV-0011/RUN-0001; release-root=~/runtime-assets/hwz-server-os/serveros-linux-work -->

# ALERT-PREVIEW

> **读数** — 本页内容是这台机器在一个时刻的测量值，重跑会得到**新的**读数，不应当相等。告警条数与账本游标随每次采集变化。`serverosctl workbench check` 因此只核对这些命令今天仍然回答、页面结构没有塌陷，不比对任何数字。

<!-- ⟦answers⟧
serverosctl card alert --json	0
serverosctl card alert	0
-->

## 告警卡预览

最多三条事件，每条带「没放进来多少」的计数。一个未放进的计数比一条被挤掉的告警更有用 —— 后者看起来像没有发生。

**人看到的（窄屏）**

```
{
  "body": {
    "direction": "vertical",
    "elements": [
      {
        "content": "**\u5176\u4ed6** 542 \u9879\u4f4e\u4f18\u5148\u7ea7 \u00b7 14 \u9879\u5df2\u6062\u590d \u00b7 94 \u9879\u5386\u53f2\u79ef\u538b",
        "tag": "markdown"
      },
      {
        "content": "\u91c7\u96c6 09-27 14:52 \u00b7 \u8bbe\u5907 device:tokyo-shared-dev-01",
        "tag": "markdown"
      },
      {
        "content": "\u53ea\u8bfb\u544a\u8b66 \u00b7 \u672a\u6267\u884c\u7ec8\u6b62\u3001\u91cd\u542f\u6216\u6e05\u7406\u64cd\u4f5c",
        "tag": "markdown"
      }
    ]
  },
  "config": {
    "wide_screen_mode": true
  },
  "header": {
    "subtitle": {
      "content": "09-27 14:52 \u66f4\u65b0",
      "tag": "plain_text"
    },
    "template": "green",
    "title": {
      "content": "\u2705 \u6ca1\u6709\u9700\u8981\u5904\u7406\u7684\u53d8\u5316",
      "tag": "plain_text"
    }
  },
  "schema": "2.0"
}
```

**同源的飞书 card JSON** —— `schema: 2.0`，标题模板 `green`，元素 3 个。

```json
{
  "body": {
    "direction": "vertical",
    "elements": [
      {
        "content": "**其他** 541 项低优先级 · 14 项已恢复 · 94 项历史积压",
        "tag": "markdown"
      },
      {
        "content": "采集 09-27 14:51 · 设备 device:tokyo-shared-dev-01",
        "tag": "markdown"
      },
      {
        "content": "只读告警 · 未执行终止、重启或清理操作",
        "tag": "markdown"
      }
    ]
  },
  "config": {
    "wide_screen_mode": true
  },
  "header": {
    "subtitle": {
      "content": "09-27 14:51 更新",
      "tag": "plain_text"
    },
    "template": "green",
    "title": {
      "content": "✅ 没有需要处理的变化",
      "tag": "plain_text"
    }
  },
  "schema": "2.0"
}
```

**未投递。** 这一节由 `card` 渲染，而 `card` 不持有任何 transport。真实投递是 [DEPLOYMENT.md](DEPLOYMENT.md) 里的 P3/P5，需要单独批准。
