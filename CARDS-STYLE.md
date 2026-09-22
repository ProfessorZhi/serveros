# CARDS-STYLE · the copy rules for a ServerOS card

`runtime/cards.py`, `runtime/presentation.py` and `runtime/card-definitions.yaml`
cite this file by rule number. It exists because those three files are where a
card's sentences live, and a sentence written without a rule beside it is a
sentence the next edit rewrites in the opposite direction.

## The register

**不要"机器语言"，也不要"聊天语言"；用专业、简洁的 SRE/NOC 运维语言。**

Both failures have already happened on this node, in that order, and this file is
the correction for both.

The first version of the alert card printed the register: `session-15413:pid1315723`,
`HIGH_MEMORY_SUSTAINED`, `WARNING`. Everything on it was true and a person could
not read it. The correction overshot into the other failure — a card that
explained itself to the reader ("本卡不会停止、重启或清理任何东西", "影响尚未确认 ——
本卡没有这条代码的影响证据"). That is a chatbot's register: it narrates its own
state instead of the machine's.

A NOC console line is neither. It is short, it is about the machine, and it
assumes a competent reader:

```text
⚠️ 需确认 · 1 项
Claude 会话 15413 · 遗留进程组
状态  会话已结束，关联进程仍在运行
影响  暂未发现明确影响
建议  核对进程归属，确认是否仍需保留
首次观测  09-21 16:36
▸ 技术详情
只读告警 · 未执行终止、重启或清理操作
```

## The face

Every event face is exactly these five labelled lines, in this order, then the
optional owner line. The labels are fixed: `状态` / `影响` / `建议` / `首次观测`,
and the fold is `技术详情`.

| field | what belongs there | what does not |
|---|---|---|
| `状态` | the measured state, as a state | the subject's existence, a cause, an operation |
| `影响` | the impact, or `暂未发现明确影响` | an explanation of why the card cannot say |
| `建议` | a thing to look at, with its object named | a place to go, an operation to carry out |
| `首次观测` | local time, with `持续` riding the same line | an ISO instant |

## The rules

**R1 · 说状态，不说存在。** `what` states a state, not a discovery. The subject is
already on the line above; a sentence that opens with 「有一个」/「有一组」/「有一条」
spends its first three characters establishing what the card just established.

- ✗ 有一个受管运行时比它的归属对象活得更久
- ✓ 归属对象已结束，该进程组仍在运行

The catalogue is per-code and subject-independent, so the entry cannot name its
subject. It names the state. That is why the card reads 「归属对象已结束，该进程组仍在
运行」 where a hand-written line would read 「会话已结束，关联进程仍在运行」: 会话 is the
subject, and this catalogue entry is shared by every subject the code can fire on,
so the entry says 归属对象 and the subject is named once, on the line above, by a
rule that does know which subject it is (R9).

**R2 · 卡面不自述。** Nothing on the face describes the card, the renderer, the
ledger or the reader's situation. A refusal, a caveat or a definition of terms is
said **once**, where it belongs — the §15 refusal at the foot, the ledger
arithmetic in the detail — and not beside every line it applies to.

- ✗ 本卡不会停止、重启或清理任何东西 （said on every event; by the third, unread）
- ✓ 只读告警 · 未执行终止、重启或清理操作 （said once, at the foot）
- ✗ 未决定不等于不重要——这是声明里的一个洞
- ✓ 未定级不等于不重要

**R3 · 卡面写名字，标识下沉技术详情。** A name a person recognises on the face; an
identifier they do not in the fold. `Claude 会话 15413` on the face, its pid in
`技术详情 · **对象**`. The identifier is never dropped — it is the only handle
somebody has when they go to look — only moved.

A `subject_display` rule may shorten a face only if it also returns the precise
form. A rule that shortens and keeps nothing is how `session-150:pid28541`
becomes a sentence nobody can act on.

**R4 · 量词用「项」，主体用「个」。** Findings are counted in 项; subjects are
counted in 个 (`1 项`, `3 个主体失去观测`). 件 and 条 are not used on a card: 件
reads as a parcel of work rather than a count of findings, and a card that counts
the same rows in two units is a card whose numbers cannot be added up by a reader.

**R5 · 建议写真实动作。** An action names a thing to look at, and the object is in
the same breath. 「在 ServerOS 登记表里查看该条目」 is not an action — it sends a
person to a surface without saying what for, which is the shape that made the
first version of this card read as an explanation.

- ✗ 在 ServerOS 登记表里查看该条目
- ✓ 核对进程归属，确认是否仍需保留
- ✓ 无需动作；未归属本身不构成危险

No action line names an operation. That ban has no exceptions: the one refusal is
made once for the whole card, in `cards.FOOTNOTE_READ_ONLY`.

**R6 · 定义不连接两个事实。** No cause word in `what` or `impact`. §4 names the
failure by hand — reading `restart failed` as `network fault` — and a definition
that connects two facts has authored a root cause nobody reviewed.
Enforced by `tests/test_presentation.py`.

**R7 · 影响未确认时写「暂未发现明确影响」。** §4: 「没有影响证据时明确影响尚未
确认」. The phrase is the declared value, carried verbatim in the model, the JSON
payload and the CLI. A plausible impact written for a code is how a card starts
asserting harm nobody measured; a sentence explaining the absence is how it
starts explaining itself.

**R8 · 次要信息最多一行，账本算术进技术详情。** Counts of what the card did *not*
print go on at most one secondary line under `**其他**`, in 项, and the ledger's
own bookkeeping (`beyond_card_cap` versus `rows`, the interval state, the
per-code breakdown) goes in the fold. The first screen does not explain the
ledger's internal arithmetic.

```text
**其他** 487 项低优先级 · 4 项已恢复 · 110 项历史积压
```

An empty alert card still prints this line. "没有新变化" over a register with 113
backlogged rows is true and misleading, and the counts are what stop it — not a
sentence telling the reader how to read a card.

**R9 · 事件行写「主体 · 这条 code 的名字」。** The header carries one attention word
for the whole card, so the event line owes the reader what this particular finding
*is*: `Claude 会话 15413 · 遗留进程组`. The noun is the catalogue's `label` for the
code, and it reaches the line through the event's `what` block — a field the
renderer reads and does not look up, because the renderer may not read the
catalogue.

A card that prints the bare subject is not wrong: a code the catalogue does not
define has no noun, and the fallback to the subject alone is correct. That is
exactly why the label was missing from the face for as long as it was without
anything failing — a dropped label and an undeclared one look the same on a card.
The rule is therefore held by a test that reads the catalogue, not by the
renderer's own docstring.

## Words that come from a contract

The attention words on a card header — `需处理` / `需确认` / `值得留意` / `供参考` /
`无事` — are declared in `runtime/notification-contract.yaml`, not here and not in
the renderer. This file describes the register they are written in; it does not
own them. A renderer holding its own copy is how `ACTION_REQUIRED` comes to read
as "warning" on one surface and "critical" on another.

## Where each rule is kept true

Rules that a test can hold are held by a test; the rest are held by whoever edits
the file next, which is what this section is for.

| rule | enforced by |
|---|---|
| R1, R2 (no cause word) | `test_presentation.py::DefinitionCatalogueTests::test_no_definition_connects_two_facts` |
| R2, R5 (§15, one refusal) | `test_presentation.py::…::test_no_action_names_an_operation_even_to_refuse_one` + `…::test_the_card_states_the_refusal_once_and_in_the_refusing_form` |
| R3 | `test_presentation.py::SubjectDisplayTests::test_a_session_process_tree_names_the_session_and_puts_the_pid_below` |
| R6 | `test_presentation.py::…::test_a_says_word_is_held_to_the_same_bans_as_a_what_line` |
| R7 | `presentation.IMPACT_UNCONFIRMED`; the value §4 names |
| R9 | `test_cards.py::TheEventLineWearsTheCataloguesLabelTests::test_every_alert_scenario_carries_the_declared_label_onto_the_face` |
| R1, R2, R4, R5, R8 | this file, and review |

One rule here is not a copy rule and is on the table because a card broke it: **the
instant a card is rendered at is an ISO instant and not a display string.** The two
look interchangeable and are not — a duration is computed from that value, and a
display form is unreadable to the parser that computes it. Held by
`test_serverosctl.py::test_the_card_clock_is_an_instant_and_not_a_display_string`.

A rule added here without a test is still a rule; it is just a rule that lives on
review alone, and this table says so rather than implying otherwise.
