<!-- ⟦snapshot⟧ rendered 2026-09-19T19:39:00+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/foundation-skeleton · inputs: projects=serveros,litellm,multi-user-claw; project=litellm; item=LIT-0101; quay-run=~/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/quay; quay-bin=~/workspace/worktrees/quay/foundation-skeleton-develop/packages/quay/dist/quay.js; ability-repo=~/workspace/worktrees/ability-management/foundation-skeleton; reports-dir=~/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f; release-root=~/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/release/hwz-server-os/serveros-linux-work -->

# STATUS-PREVIEW

> **读数** — 本页内容是这台机器在一个时刻的测量值，重跑会得到**新的**读数，不应当相等。CPU/内存/磁盘占用是本机当前读数。`serverosctl workbench check` 因此只核对这些命令今天仍然回答、页面结构没有塌陷，不比对任何数字。

<!-- ⟦answers⟧
serverosctl card status --json	0
serverosctl card status	0
-->

## 状态卡预览

三块加一行 env-health。共享网关只在有 service binding 指名时才出现，所以没出现不等于它健康。

**人看到的（窄屏）**

```
{
  "body": {
    "direction": "vertical",
    "elements": [
      {
        "content": "**\u73af\u5883\u53ef\u7528\u6027** \u6709\u9879\u76ee\u5c1a\u672a\u8bfb\u5230\uff1agateway\n**\u89c2\u6d4b** \u6709\u9879\u76ee\u672a\u8bfb\u5230",
        "tag": "markdown"
      },
      {
        "content": "**\u6211\u7684\u8d44\u6e90 \u00b7 \u5f53\u524d\u8d26\u6237**\n\u8d26\u6237 uid 1002 \u00b7 \u8fdb\u7a0b\u6570 91",
        "tag": "markdown"
      },
      {
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "CPU \u5360\u6574\u673a 0.1%\uff080.07 \u6838\u7b49\u6548 / \u5171 128 \u6838\uff09\n\u5185\u5b58 9.5 GiB \u00b7 \u5360\u6574\u673a 3.9%\n\u8d26\u6237\u6570\u5b57\u7684\u6765\u6e90 sum of RSS over this uid's processes, sampled twice; an estimate of resident memory, not kernel per-uid accounting\n\u914d\u989d /data\uff08/dev/vdb\uff09 \u5df2\u7528 55.3 GiB / \u8f6f\u9650 450.0 GiB\uff0811.1%\uff09 \u00b7 \u786c\u9650 500.0 GiB\n\u672c\u8d26\u6237 systemd \u7528\u6237\u5355\u5143 13 \u4e2a\uff08\u5176\u4e2d active 3\uff09 \u00b7 linger yes \u00b7 supervisor systemd --user\n  dbus.service \u2014 active\n  desktop-commander-remote.service \u2014 active\n  serverosd.service \u2014 active\n\u672c\u8282\u70b9\u767b\u8bb0\u7684\u53d7\u7ba1\u8fd0\u884c\u65f6 4 \u4e2a\uff08\u767b\u8bb0 \u2260 \u6b63\u5728\u8fd0\u884c\uff0c\u4e5f \u2260 \u5df2\u6279\u51c6\u81ea\u52a8\u4fee\u590d\uff09",
            "tag": "markdown"
          }
        ],
        "expanded": false,
        "header": {
          "icon": {
            "size": "16px 16px",
            "tag": "standard_icon",
            "token": "down-small-ccm_outlined"
          },
          "icon_expanded_angle": -180,
          "icon_position": "follow_text",
          "title": {
            "content": "**\u6211\u7684\u8d44\u6e90 \u00b7 \u5f53\u524d\u8d26\u6237**",
            "tag": "markdown"
          },
          "vertical_align": "center"
        },
        "padding": "8px 8px 8px 8px",
        "tag": "collapsible_panel",
        "vertical_spacing": "8px"
      },
      {
        "content": "**\u6574\u673a\u8d44\u6e90**\nCPU 128 \u903b\u8f91\u6838 \u00b7 \u578b\u53f7 AMD EPYC 9K65 192-Core Processor",
        "tag": "markdown"
      },
      {
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "\u8d1f\u8f7d 3.65 / 3.2 / 3.23 \u00b7 \u53ef\u8fd0\u884c 5/7809\n\u5185\u5b58 \u5df2\u7528 37.5 GiB / 247.0 GiB \u00b7 \u53ef\u7528 209.5 GiB\uff08\u7f13\u5b58 152.2 GiB\uff09\n\u4ea4\u6362\u533a \u5df2\u7528 0.1%\uff089.5 MiB / 16.0 GiB\uff09 \u00b7 \u9608\u503c 90.0%\n\u6587\u4ef6\u7cfb\u7edf /\uff08ROOT_FS\uff09 \u5df2\u7528 48.2% \u00b7 inode 5.8% \u00b7 \u53ef\u7528 23.3 GiB\n\u6587\u4ef6\u7cfb\u7edf /data\uff08DOCKER_FS / STATE_FS / ARTIFACTS_FS\uff09 \u5df2\u7528 6.0% \u00b7 inode 1.0% \u00b7 \u53ef\u7528 3.8 TiB\nDocker 29.8.0 \u00b7 \u5bb9\u5668 \u8fd0\u884c 8 / \u505c\u6b62 1 / \u5171 9 \u00b7 \u955c\u50cf 19",
            "tag": "markdown"
          }
        ],
        "expanded": false,
        "header": {
          "icon": {
            "size": "16px 16px",
            "tag": "standard_icon",
            "token": "down-small-ccm_outlined"
          },
          "icon_expanded_angle": -180,
          "icon_position": "follow_text",
          "title": {
            "content": "**\u6574\u673a\u8d44\u6e90**",
            "tag": "markdown"
          },
          "vertical_align": "center"
        },
        "padding": "8px 8px 8px 8px",
        "tag": "collapsible_panel",
        "vertical_spacing": "8px"
      },
      {
        "content": "**\u5171\u4eab AI \u7f51\u5173 \u00b7 \u4e0e\u4e2a\u4eba container0 \u5206\u5f00**\n\u672a\u8bfb\u5230\uff1a\u6ca1\u6709\u4efb\u4f55 service binding \u6307\u660e\u672c\u8282\u70b9\u7684\u5171\u4eab\u7f51\u5173\u3002\u00a72 \u7981\u6b62\u6309\u5bb9\u5668\u540d\u6216\u6309 owner \u9009\u5b83\uff0c\u56e0\u6b64\u8fd9\u4e00\u680f\u62a5\u4e3a\u672a\u7ed1\u5b9a\uff0c\u800c\u4e0d\u662f\u62ff\u4e00\u4e2a\u957f\u5f97\u50cf\u7684\u5bf9\u8c61\u586b\u4e0a",
        "tag": "markdown"
      },
      {
        "content": "\u91c7\u96c6\u65f6\u95f4 2026-09-20 03:39:17 CST \u00b7 \u89c2\u6d4b\u8303\u56f4 host\u3001mine \u00b7 \u672a\u8bfb\u5230 gateway",
        "tag": "markdown"
      },
      {
        "content": "\u5171\u4eab\u7f51\u5173\u4e0e\u4e2a\u4eba container0 \u5206\u5f00\u5448\u73b0\uff1b\u540c\u4e00\u53f0\u673a\u5668\u4e0a\u7684\u4e24\u4e2a\u5bf9\u8c61\uff0c\u4e0d\u662f\u540c\u4e00\u4ef6\u4e8b",
        "tag": "markdown"
      },
      {
        "content": "\u672c\u5361\u53ea\u62a5\u544a ServerOS \u5df2\u7ecf\u8bb0\u5f55\u7684\u4e8b\u5b9e\u3002\u6ca1\u6709\u7ec8\u6b62\u3001\u6ca1\u6709\u91cd\u542f\u3001\u6ca1\u6709\u6e05\u7406 \u2014\u2014 \u9700\u8981\u52a8\u4f5c\u7684\u6761\u76ee\u5728 ServerOS \u91cc\u5e26\u5efa\u8bae\uff0c\u4e0d\u5728\u8fd9\u91cc\u3002",
        "tag": "markdown"
      }
    ]
  },
  "config": {
    "wide_screen_mode": true
  },
  "header": {
    "subtitle": {
      "content": "2026-09-20 03:39:17 CST \u66f4\u65b0",
      "tag": "plain_text"
    },
    "template": "grey",
    "title": {
      "content": "\u2754 \u672c\u673a \u00b7 \u73af\u5883\u72b6\u6001",
      "tag": "plain_text"
    }
  },
  "schema": "2.0"
}
```

**同源的飞书 card JSON** —— `schema: 2.0`，标题模板 `grey`，元素 9 个。

```json
{
  "body": {
    "direction": "vertical",
    "elements": [
      {
        "content": "**环境可用性** 有项目尚未读到：gateway\n**观测** 有项目未读到",
        "tag": "markdown"
      },
      {
        "content": "**我的资源 · 当前账户**\n账户 uid 1002 · 进程数 91",
        "tag": "markdown"
      },
      {
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "CPU 占整机 0.0%（0.04 核等效 / 共 128 核）\n内存 9.5 GiB · 占整机 3.9%\n账户数字的来源 sum of RSS over this uid's processes, sampled twice; an estimate of resident memory, not kernel per-uid accounting\n配额 /data（/dev/vdb） 已用 55.3 GiB / 软限 450.0 GiB（11.1%） · 硬限 500.0 GiB\n本账户 systemd 用户单元 13 个（其中 active 3） · linger yes · supervisor systemd --user\n  dbus.service — active\n  desktop-commander-remote.service — active\n  serverosd.service — active\n本节点登记的受管运行时 4 个（登记 ≠ 正在运行，也 ≠ 已批准自动修复）",
            "tag": "markdown"
          }
        ],
        "expanded": false,
        "header": {
          "icon": {
            "size": "16px 16px",
            "tag": "standard_icon",
            "token": "down-small-ccm_outlined"
          },
          "icon_expanded_angle": -180,
          "icon_position": "follow_text",
          "title": {
            "content": "**我的资源 · 当前账户**",
            "tag": "markdown"
          },
          "vertical_align": "center"
        },
        "padding": "8px 8px 8px 8px",
        "tag": "collapsible_panel",
        "vertical_spacing": "8px"
      },
      {
        "content": "**整机资源**\nCPU 128 逻辑核 · 型号 AMD EPYC 9K65 192-Core Processor",
        "tag": "markdown"
      },
      {
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "负载 3.65 / 3.2 / 3.23 · 可运行 6/7808\n内存 已用 37.1 GiB / 247.0 GiB · 可用 209.9 GiB（缓存 152.2 GiB）\n交换区 已用 0.1%（9.5 MiB / 16.0 GiB） · 阈值 90.0%\n文件系统 /（ROOT_FS） 已用 48.2% · inode 5.8% · 可用 23.3 GiB\n文件系统 /data（DOCKER_FS / STATE_FS / ARTIFACTS_FS） 已用 6.0% · inode 1.0% · 可用 3.8 TiB\nDocker 29.8.0 · 容器 运行 8 / 停止 1 / 共 9 · 镜像 19",
            "tag": "markdown"
          }
        ],
        "expanded": false,
        "header": {
          "icon": {
            "size": "16px 16px",
            "tag": "standard_icon",
            "token": "down-small-ccm_outlined"
          },
          "icon_expanded_angle": -180,
          "icon_position": "follow_text",
          "title": {
            "content": "**整机资源**",
            "tag": "markdown"
          },
          "vertical_align": "center"
        },
        "padding": "8px 8px 8px 8px",
        "tag": "collapsible_panel",
        "vertical_spacing": "8px"
      },
      {
        "content": "**共享 AI 网关 · 与个人 container0 分开**\n未读到：没有任何 service binding 指明本节点的共享网关。§2 禁止按容器名或按 owner 选它，因此这一栏报为未绑定，而不是拿一个长得像的对象填上",
        "tag": "markdown"
      },
      {
        "content": "采集时间 2026-09-20 03:39:15 CST · 观测范围 host、mine · 未读到 gateway",
        "tag": "markdown"
      },
      {
        "content": "共享网关与个人 container0 分开呈现；同一台机器上的两个对象，不是同一件事",
        "tag": "markdown"
      },
      {
        "content": "本卡只报告 ServerOS 已经记录的事实。没有终止、没有重启、没有清理 —— 需要动作的条目在 ServerOS 里带建议，不在这里。",
        "tag": "markdown"
      }
    ]
  },
  "config": {
    "wide_screen_mode": true
  },
  "header": {
    "subtitle": {
      "content": "2026-09-20 03:39:15 CST 更新",
      "tag": "plain_text"
    },
    "template": "grey",
    "title": {
      "content": "❔ 本机 · 环境状态",
      "tag": "plain_text"
    }
  },
  "schema": "2.0"
}
```

**未投递。** 这一节由 `card` 渲染，而 `card` 不持有任何 transport。真实投递是 [DEPLOYMENT.md](DEPLOYMENT.md) 里的 P3/P5，需要单独批准。
