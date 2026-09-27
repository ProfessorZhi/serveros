<!-- ⟦snapshot⟧ rendered 2026-09-27T06:51:41+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/serveros-completion-20260925 · inputs: projects=serveros,litellm,multi-user-claw; project=litellm; item=LIT-0101; quay-run=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/quay-run; quay-bin=~/workspace/worktrees/quay/foundation-skeleton/plugin/vendor/quay/dist/quay.js; ability-repo=~/workspace/src/ability-management; reports-dir=~/artifacts/work-items/serveros/SRV-0011/RUN-0001; release-root=~/runtime-assets/hwz-server-os/serveros-linux-work -->

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
        "content": "**\u64ad\u62a5\u65e5\u7a0b** 2026-09-27 \u662f\u4f11\u606f\u65e5\uff0c\u4e0d\u521b\u5efa\u4f8b\u884c\u72b6\u6001\u5361\uff0c\u4e5f\u4e0d\u8865\u53d1\uff1b\u4e8b\u6545\u5361\u53e6\u6309 P1/P2 \u7b56\u7565",
        "tag": "markdown"
      },
      {
        "content": "**\u5f53\u5929\u5361** \u4eca\u5929\u8fd8\u6ca1\u6709\u5f53\u5929\u7684\u5361",
        "tag": "markdown"
      },
      {
        "content": "**\u6211\u7684\u8d44\u6e90 \u00b7 \u5f53\u524d\u8d26\u6237**\n\u8d26\u6237 uid 1002 \u00b7 \u8fdb\u7a0b\u6570 125",
        "tag": "markdown"
      },
      {
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "CPU \u5360\u6574\u673a 0.2%\uff080.2 \u6838\u7b49\u6548 / \u5171 128 \u6838\uff09\n\u5185\u5b58 11.0 GiB \u00b7 \u5360\u6574\u673a 4.5%\n\u8d26\u6237\u6570\u5b57\u7684\u6765\u6e90 sum of RSS over this uid's processes, sampled twice; an estimate of resident memory, not kernel per-uid accounting\n\u914d\u989d /data\uff08/dev/vdb\uff09 \u5df2\u7528 89.3 GiB / \u8f6f\u9650 450.0 GiB\uff0817.9%\uff09 \u00b7 \u786c\u9650 500.0 GiB\n\u672c\u8d26\u6237 systemd \u7528\u6237\u5355\u5143 19 \u4e2a\uff08\u5176\u4e2d active 7\uff09 \u00b7 linger yes \u00b7 supervisor systemd --user\n  at-spi-dbus-bus.service \u2014 active\n  dbus.service \u2014 active\n  desktop-commander-remote.service \u2014 active\n  laya-shared.service \u2014 active\n  openviking.service \u2014 active\n  serverosd.service \u2014 active\n  tailscaled.service \u2014 active\n\u672c\u8282\u70b9\u767b\u8bb0\u7684\u53d7\u7ba1\u8fd0\u884c\u65f6 4 \u4e2a\uff08\u767b\u8bb0 \u2260 \u6b63\u5728\u8fd0\u884c\uff0c\u4e5f \u2260 \u5df2\u6279\u51c6\u81ea\u52a8\u4fee\u590d\uff09",
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
            "content": "\u8d1f\u8f7d 9.42 / 9.41 / 10.85 \u00b7 \u53ef\u8fd0\u884c 8/10237\n\u5185\u5b58 \u5df2\u7528 48.6 GiB / 247.0 GiB \u00b7 \u53ef\u7528 198.3 GiB\uff08\u7f13\u5b58 110.1 GiB\uff09\n\u4ea4\u6362\u533a \u5df2\u7528 33.7%\uff085.4 GiB / 16.0 GiB\uff09 \u00b7 \u9608\u503c 90.0%\n\u6587\u4ef6\u7cfb\u7edf /\uff08ROOT_FS\uff09 \u5df2\u7528 70.0% \u00b7 inode 15.9% \u00b7 \u53ef\u7528 12.6 GiB\n\u6587\u4ef6\u7cfb\u7edf /data\uff08DOCKER_FS / STATE_FS / ARTIFACTS_FS\uff09 \u5df2\u7528 14.6% \u00b7 inode 3.8% \u00b7 \u53ef\u7528 3.4 TiB\nDocker 29.8.0 \u00b7 \u5bb9\u5668 \u8fd0\u884c 13 / \u505c\u6b62 6 / \u5171 19 \u00b7 \u955c\u50cf 23",
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
        "content": "\u91c7\u96c6\u65f6\u95f4 09-27 14:54 \u00b7 \u89c2\u6d4b\u8303\u56f4 host\u3001mine \u00b7 \u672a\u8bfb\u5230 gateway",
        "tag": "markdown"
      },
      {
        "content": "\u5171\u4eab\u7f51\u5173\u4e0e\u4e2a\u4eba container0 \u5206\u5f00\u5448\u73b0\uff1b\u540c\u4e00\u53f0\u673a\u5668\u4e0a\u7684\u4e24\u4e2a\u5bf9\u8c61\uff0c\u4e0d\u662f\u540c\u4e00\u4ef6\u4e8b",
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
      "content": "09-27 14:54 \u66f4\u65b0",
      "tag": "plain_text"
    },
    "template": "grey",
    "title": {
      "content": "\u2754 \u672c\u673a \u00b7 \u8d44\u6e90\u64ad\u62a5",
      "tag": "plain_text"
    }
  },
  "schema": "2.0"
}
```

**同源的飞书 card JSON** —— `schema: 2.0`，标题模板 `grey`，元素 11 个。

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
        "content": "**播报日程** 2026-09-27 是休息日，不创建例行状态卡，也不补发；事故卡另按 P1/P2 策略",
        "tag": "markdown"
      },
      {
        "content": "**当天卡** 今天还没有当天的卡",
        "tag": "markdown"
      },
      {
        "content": "**我的资源 · 当前账户**\n账户 uid 1002 · 进程数 125",
        "tag": "markdown"
      },
      {
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "CPU 占整机 0.4%（0.49 核等效 / 共 128 核）\n内存 11.1 GiB · 占整机 4.5%\n账户数字的来源 sum of RSS over this uid's processes, sampled twice; an estimate of resident memory, not kernel per-uid accounting\n配额 /data（/dev/vdb） 已用 89.3 GiB / 软限 450.0 GiB（17.9%） · 硬限 500.0 GiB\n本账户 systemd 用户单元 19 个（其中 active 7） · linger yes · supervisor systemd --user\n  at-spi-dbus-bus.service — active\n  dbus.service — active\n  desktop-commander-remote.service — active\n  laya-shared.service — active\n  openviking.service — active\n  serverosd.service — active\n  tailscaled.service — active\n本节点登记的受管运行时 4 个（登记 ≠ 正在运行，也 ≠ 已批准自动修复）",
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
            "content": "负载 8.94 / 9.32 / 10.82 · 可运行 14/10268\n内存 已用 48.2 GiB / 247.0 GiB · 可用 198.7 GiB（缓存 110.1 GiB）\n交换区 已用 33.7%（5.4 GiB / 16.0 GiB） · 阈值 90.0%\n文件系统 /（ROOT_FS） 已用 70.0% · inode 15.9% · 可用 12.6 GiB\n文件系统 /data（DOCKER_FS / STATE_FS / ARTIFACTS_FS） 已用 14.6% · inode 3.8% · 可用 3.4 TiB\nDocker 29.8.0 · 容器 运行 13 / 停止 6 / 共 19 · 镜像 23",
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
        "content": "采集时间 09-27 14:54 · 观测范围 host、mine · 未读到 gateway",
        "tag": "markdown"
      },
      {
        "content": "共享网关与个人 container0 分开呈现；同一台机器上的两个对象，不是同一件事",
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
      "content": "09-27 14:54 更新",
      "tag": "plain_text"
    },
    "template": "grey",
    "title": {
      "content": "❔ 本机 · 资源播报",
      "tag": "plain_text"
    }
  },
  "schema": "2.0"
}
```

**未投递。** 这一节由 `card` 渲染，而 `card` 不持有任何 transport。真实投递是 [DEPLOYMENT.md](DEPLOYMENT.md) 里的 P3/P5，需要单独批准。
