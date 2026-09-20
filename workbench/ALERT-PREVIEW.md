<!-- ⟦snapshot⟧ rendered 2026-09-20T14:06:52+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/SRV-0011 · inputs: projects=serveros,litellm,multi-user-claw; project=litellm; item=LIT-0101; quay-run=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/quay-run; quay-bin=~/workspace/worktrees/quay/foundation-skeleton/plugin/vendor/quay/dist/quay.js; ability-repo=~/workspace/worktrees/ability-management/SRV-0011; reports-dir=~/artifacts/work-items/serveros/SRV-0011/RUN-0001; release-root=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/release/dist/serveros-linux-work -->

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
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "**\u53d1\u751f\u4e86\u4ec0\u4e48** \u6709\u4e00\u4e2a\u8fdb\u7a0b\u7ec4\u6301\u7eed\u5360\u7528\u5185\u5b58\n**\u4e8b\u4ef6\u7c7b\u578b** NEW \u00b7 **\u539f\u4e25\u91cd\u5ea6** WARNING\uff08medium\uff09\n**\u5f71\u54cd** \u5f71\u54cd\u5c1a\u672a\u786e\u8ba4\n**\u786e\u5b9a\u6027** UNKNOWN \u2014\u2014 \u751f\u4ea7\u8005\u5728\u8fd9\u4e00\u884c\u6ca1\u6709\u8bb0\u5f55 freshness\uff0c\u56e0\u6b64\u8fd9\u6b21\u8bfb\u6570\u662f\u5426\u4ecd\u7136\u65b0\u9c9c\u65e0\u6cd5\u5224\u5b9a \u00b7 \u6765\u6e90 \u672a\u8bb0\u5f55\n**\u5efa\u8bae** REVIEW \u2014\u2014 \u67e5\u770b\u8be5\u8fdb\u7a0b\u7ec4\u7684\u5f52\u5c5e\u4e0e\u5360\u7528\u8d21\u732e\u8005\n**\u5efa\u8bae\u7684\u6548\u529b** \u5efa\u8bae\uff0c\u4e0d\u662f\u6388\u6743\uff1b\u6ca1\u6709\u4efb\u4f55\u52a8\u4f5c\u4f1a\u56e0\u6b64\u53d1\u751f\n**\u9996\u6b21\u89c2\u6d4b** 2026-09-18T06:15:57.175939+00:00 \u00b7 **\u6700\u8fd1** 2026-09-20 22:06:52 CST\n**\u6301\u7eed\u65f6\u95f4** \u672a\u77e5\uff08\u6ca1\u6709\u53ef\u89e3\u6790\u7684 at \u4f5c\u4e3a\u8ba1\u91cf\u7ec8\u70b9\uff09\n**\u5f15\u7528** alert:2d78bf9461e7bca6\n**\u8bc1\u636e\u4e0e\u8be6\u60c5**\nWARNING \u00b7 medium\nmeasured: no measurement \u2014 this code is a declaration\nobserved: no timestamp (unknown, source unknown)\nduration: unknown (seen 1 times)\nrecommendation: REVIEW \u00b7 nothing was stopped, restarted or removed",
            "tag": "markdown"
          }
        ],
        "expanded": true,
        "header": {
          "icon": {
            "size": "16px 16px",
            "tag": "standard_icon",
            "token": "down-small-ccm_outlined"
          },
          "icon_expanded_angle": -180,
          "icon_position": "follow_text",
          "title": {
            "content": "**WARNING \u00b7 \u8fdb\u7a0b\u7ec4 docker-08e734361c06\u2026**",
            "tag": "markdown"
          },
          "vertical_align": "center"
        },
        "padding": "8px 8px 8px 8px",
        "tag": "collapsible_panel",
        "vertical_spacing": "8px"
      },
      {
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "**\u53d1\u751f\u4e86\u4ec0\u4e48** \u6709\u4e00\u4e2a\u8fdb\u7a0b\u7ec4\u6301\u7eed\u5360\u7528\u5185\u5b58\n**\u4e8b\u4ef6\u7c7b\u578b** NEW \u00b7 **\u539f\u4e25\u91cd\u5ea6** WARNING\uff08medium\uff09\n**\u5f71\u54cd** \u5f71\u54cd\u5c1a\u672a\u786e\u8ba4\n**\u786e\u5b9a\u6027** UNKNOWN \u2014\u2014 \u751f\u4ea7\u8005\u5728\u8fd9\u4e00\u884c\u6ca1\u6709\u8bb0\u5f55 freshness\uff0c\u56e0\u6b64\u8fd9\u6b21\u8bfb\u6570\u662f\u5426\u4ecd\u7136\u65b0\u9c9c\u65e0\u6cd5\u5224\u5b9a \u00b7 \u6765\u6e90 \u672a\u8bb0\u5f55\n**\u5efa\u8bae** REVIEW \u2014\u2014 \u67e5\u770b\u8be5\u8fdb\u7a0b\u7ec4\u7684\u5f52\u5c5e\u4e0e\u5360\u7528\u8d21\u732e\u8005\n**\u5efa\u8bae\u7684\u6548\u529b** \u5efa\u8bae\uff0c\u4e0d\u662f\u6388\u6743\uff1b\u6ca1\u6709\u4efb\u4f55\u52a8\u4f5c\u4f1a\u56e0\u6b64\u53d1\u751f\n**\u9996\u6b21\u89c2\u6d4b** 2026-09-18T06:35:10.798465+00:00 \u00b7 **\u6700\u8fd1** 2026-09-20 22:06:52 CST\n**\u6301\u7eed\u65f6\u95f4** \u672a\u77e5\uff08\u6ca1\u6709\u53ef\u89e3\u6790\u7684 at \u4f5c\u4e3a\u8ba1\u91cf\u7ec8\u70b9\uff09\n**\u5f15\u7528** alert:813fd81cdb193552\n**\u8bc1\u636e\u4e0e\u8be6\u60c5**\nWARNING \u00b7 medium\nmeasured: no measurement \u2014 this code is a declaration\nobserved: no timestamp (unknown, source unknown)\nduration: unknown (seen 7 times)\nrecommendation: REVIEW \u00b7 nothing was stopped, restarted or removed",
            "tag": "markdown"
          }
        ],
        "expanded": true,
        "header": {
          "icon": {
            "size": "16px 16px",
            "tag": "standard_icon",
            "token": "down-small-ccm_outlined"
          },
          "icon_expanded_angle": -180,
          "icon_position": "follow_text",
          "title": {
            "content": "**WARNING \u00b7 \u8fdb\u7a0b\u7ec4 docker-491d41804a0e\u2026**",
            "tag": "markdown"
          },
          "vertical_align": "center"
        },
        "padding": "8px 8px 8px 8px",
        "tag": "collapsible_panel",
        "vertical_spacing": "8px"
      },
      {
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "**\u53d1\u751f\u4e86\u4ec0\u4e48** \u6709\u4e00\u4e2a\u8fdb\u7a0b\u7ec4\u6301\u7eed\u5360\u7528\u5185\u5b58\n**\u4e8b\u4ef6\u7c7b\u578b** NEW \u00b7 **\u539f\u4e25\u91cd\u5ea6** WARNING\uff08medium\uff09\n**\u5f71\u54cd** \u5f71\u54cd\u5c1a\u672a\u786e\u8ba4\n**\u786e\u5b9a\u6027** UNKNOWN \u2014\u2014 \u751f\u4ea7\u8005\u5728\u8fd9\u4e00\u884c\u6ca1\u6709\u8bb0\u5f55 freshness\uff0c\u56e0\u6b64\u8fd9\u6b21\u8bfb\u6570\u662f\u5426\u4ecd\u7136\u65b0\u9c9c\u65e0\u6cd5\u5224\u5b9a \u00b7 \u6765\u6e90 \u672a\u8bb0\u5f55\n**\u5efa\u8bae** REVIEW \u2014\u2014 \u67e5\u770b\u8be5\u8fdb\u7a0b\u7ec4\u7684\u5f52\u5c5e\u4e0e\u5360\u7528\u8d21\u732e\u8005\n**\u5efa\u8bae\u7684\u6548\u529b** \u5efa\u8bae\uff0c\u4e0d\u662f\u6388\u6743\uff1b\u6ca1\u6709\u4efb\u4f55\u52a8\u4f5c\u4f1a\u56e0\u6b64\u53d1\u751f\n**\u9996\u6b21\u89c2\u6d4b** 2026-09-18T06:43:16.462862+00:00 \u00b7 **\u6700\u8fd1** 2026-09-20 22:06:52 CST\n**\u6301\u7eed\u65f6\u95f4** \u672a\u77e5\uff08\u6ca1\u6709\u53ef\u89e3\u6790\u7684 at \u4f5c\u4e3a\u8ba1\u91cf\u7ec8\u70b9\uff09\n**\u5f15\u7528** alert:fe3a7cf437529387\n**\u8bc1\u636e\u4e0e\u8be6\u60c5**\nWARNING \u00b7 medium\nmeasured: no measurement \u2014 this code is a declaration\nobserved: no timestamp (unknown, source unknown)\nduration: unknown (seen 3289 times)\nrecommendation: REVIEW \u00b7 nothing was stopped, restarted or removed",
            "tag": "markdown"
          }
        ],
        "expanded": true,
        "header": {
          "icon": {
            "size": "16px 16px",
            "tag": "standard_icon",
            "token": "down-small-ccm_outlined"
          },
          "icon_expanded_angle": -180,
          "icon_position": "follow_text",
          "title": {
            "content": "**WARNING \u00b7 \u8fdb\u7a0b\u7ec4 docker-b1e7988a3409\u2026**",
            "tag": "markdown"
          },
          "vertical_align": "center"
        },
        "padding": "8px 8px 8px 8px",
        "tag": "collapsible_panel",
        "vertical_spacing": "8px"
      },
      {
        "content": "\u53e6\u6709 361 \u6761\u540c\u7c7b\u4e8b\u4ef6\u672a\u8fdb\u5361\u9762 \u00b7 1866 \u6761\u5728\u66f4\u65e9\u7684\u6279\u6b21\u91cc\u5df2\u88ab\u8ba1\u6570 \u00b7 111 \u6761\u5728\u6295\u9012\u57fa\u7ebf\u4e4b\u524d\u5c31\u5df2\u5b58\u5728\uff08\u79ef\u538b\uff0c\u4e0d\u91cd\u590d\u64ad\u62a5\uff09 \u00b7 20 \u6761\u4ece\u672a\u544a\u77e5\u8fc7\uff0c\u4e0d\u8ba1\u5165\u6062\u590d",
        "tag": "markdown"
      },
      {
        "content": "\u91c7\u96c6 2026-09-20 22:06:52 CST \u00b7 \u8bbe\u5907 device:tokyo-shared-dev-01",
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
      "content": "2026-09-20 22:06:52 CST \u66f4\u65b0",
      "tag": "plain_text"
    },
    "template": "orange",
    "title": {
      "content": "\u26a0\ufe0f \u9700\u8981\u5173\u6ce8 \u00b7 3 \u4ef6",
      "tag": "plain_text"
    }
  },
  "schema": "2.0"
}
```

**同源的飞书 card JSON** —— `schema: 2.0`，标题模板 `orange`，元素 6 个。

```json
{
  "body": {
    "direction": "vertical",
    "elements": [
      {
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "**发生了什么** 有一个进程组持续占用内存\n**事件类型** NEW · **原严重度** WARNING（medium）\n**影响** 影响尚未确认\n**确定性** UNKNOWN —— 生产者在这一行没有记录 freshness，因此这次读数是否仍然新鲜无法判定 · 来源 未记录\n**建议** REVIEW —— 查看该进程组的归属与占用贡献者\n**建议的效力** 建议，不是授权；没有任何动作会因此发生\n**首次观测** 2026-09-18T06:15:57.175939+00:00 · **最近** 2026-09-20 22:06:52 CST\n**持续时间** 未知（没有可解析的 at 作为计量终点）\n**引用** alert:2d78bf9461e7bca6\n**证据与详情**\nWARNING · medium\nmeasured: no measurement — this code is a declaration\nobserved: no timestamp (unknown, source unknown)\nduration: unknown (seen 1 times)\nrecommendation: REVIEW · nothing was stopped, restarted or removed",
            "tag": "markdown"
          }
        ],
        "expanded": true,
        "header": {
          "icon": {
            "size": "16px 16px",
            "tag": "standard_icon",
            "token": "down-small-ccm_outlined"
          },
          "icon_expanded_angle": -180,
          "icon_position": "follow_text",
          "title": {
            "content": "**WARNING · 进程组 docker-08e734361c06…**",
            "tag": "markdown"
          },
          "vertical_align": "center"
        },
        "padding": "8px 8px 8px 8px",
        "tag": "collapsible_panel",
        "vertical_spacing": "8px"
      },
      {
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "**发生了什么** 有一个进程组持续占用内存\n**事件类型** NEW · **原严重度** WARNING（medium）\n**影响** 影响尚未确认\n**确定性** UNKNOWN —— 生产者在这一行没有记录 freshness，因此这次读数是否仍然新鲜无法判定 · 来源 未记录\n**建议** REVIEW —— 查看该进程组的归属与占用贡献者\n**建议的效力** 建议，不是授权；没有任何动作会因此发生\n**首次观测** 2026-09-18T06:35:10.798465+00:00 · **最近** 2026-09-20 22:06:52 CST\n**持续时间** 未知（没有可解析的 at 作为计量终点）\n**引用** alert:813fd81cdb193552\n**证据与详情**\nWARNING · medium\nmeasured: no measurement — this code is a declaration\nobserved: no timestamp (unknown, source unknown)\nduration: unknown (seen 7 times)\nrecommendation: REVIEW · nothing was stopped, restarted or removed",
            "tag": "markdown"
          }
        ],
        "expanded": true,
        "header": {
          "icon": {
            "size": "16px 16px",
            "tag": "standard_icon",
            "token": "down-small-ccm_outlined"
          },
          "icon_expanded_angle": -180,
          "icon_position": "follow_text",
          "title": {
            "content": "**WARNING · 进程组 docker-491d41804a0e…**",
            "tag": "markdown"
          },
          "vertical_align": "center"
        },
        "padding": "8px 8px 8px 8px",
        "tag": "collapsible_panel",
        "vertical_spacing": "8px"
      },
      {
        "border": {
          "color": "grey",
          "corner_radius": "5px"
        },
        "elements": [
          {
            "content": "**发生了什么** 有一个进程组持续占用内存\n**事件类型** NEW · **原严重度** WARNING（medium）\n**影响** 影响尚未确认\n**确定性** UNKNOWN —— 生产者在这一行没有记录 freshness，因此这次读数是否仍然新鲜无法判定 · 来源 未记录\n**建议** REVIEW —— 查看该进程组的归属与占用贡献者\n**建议的效力** 建议，不是授权；没有任何动作会因此发生\n**首次观测** 2026-09-18T06:43:16.462862+00:00 · **最近** 2026-09-20 22:06:52 CST\n**持续时间** 未知（没有可解析的 at 作为计量终点）\n**引用** alert:fe3a7cf437529387\n**证据与详情**\nWARNING · medium\nmeasured: no measurement — this code is a declaration\nobserved: no timestamp (unknown, source unknown)\nduration: unknown (seen 3289 times)\nrecommendation: REVIEW · nothing was stopped, restarted or removed",
            "tag": "markdown"
          }
        ],
        "expanded": true,
        "header": {
          "icon": {
            "size": "16px 16px",
            "tag": "standard_icon",
            "token": "down-small-ccm_outlined"
          },
          "icon_expanded_angle": -180,
          "icon_position": "follow_text",
          "title": {
            "content": "**WARNING · 进程组 docker-b1e7988a3409…**",
            "tag": "markdown"
          },
          "vertical_align": "center"
        },
        "padding": "8px 8px 8px 8px",
        "tag": "collapsible_panel",
        "vertical_spacing": "8px"
      },
      {
        "content": "另有 361 条同类事件未进卡面 · 1866 条在更早的批次里已被计数 · 111 条在投递基线之前就已存在（积压，不重复播报） · 20 条从未告知过，不计入恢复",
        "tag": "markdown"
      },
      {
        "content": "采集 2026-09-20 22:06:52 CST · 设备 device:tokyo-shared-dev-01",
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
      "content": "2026-09-20 22:06:52 CST 更新",
      "tag": "plain_text"
    },
    "template": "orange",
    "title": {
      "content": "⚠️ 需要关注 · 3 件",
      "tag": "plain_text"
    }
  },
  "schema": "2.0"
}
```

**未投递。** 这一节由 `card` 渲染，而 `card` 不持有任何 transport。真实投递是 [DEPLOYMENT.md](DEPLOYMENT.md) 里的 P3/P5，需要单独批准。
