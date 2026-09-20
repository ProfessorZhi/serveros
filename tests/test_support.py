"""§J's support chain: a question in, an answer out, and the KB finding with it.

THE CRITERION THIS FILE IS ABOUT
--------------------------------
    | SYS-18 | 技术支持/杂事：一项技术支持输入到答复/知识改进的链，code/KB不混 |

The chain has four readings and they are the whole product. "I found nothing"
and "I could not look" must not render the same way, and neither may render as
an answer. So the tests below are mostly about the boundaries:

  * `test_an_uncurated_sample_is_never_quoted` puts a distinctive string in
    `99_待整理样本/` and asserts it appears in NO field of the answer. The
    knowledge base's own rule is 「不直接引用待整理样本」, and a chain that read
    the samples as entries would answer every question and be wrong about all
    of them.
  * `test_the_improvement_candidate_is_redacted_too` is the reverse direction
    of `test_an_identifier_is_redacted`, and it exists because the first
    version failed it. Redaction was a property of the assembled sections and
    not of the module's output, so the one field NOT assembled from the entry --
    the improvement candidate -- carried a raw user identifier into a record
    somebody would file.
  * `test_a_variant_is_not_reported_when_only_the_placeholder_differs` is the
    same defect one level up: the candidate fired on every question because the
    knowledge base writes `User=<USER>` and a real question carries the value.
    A finding that always fires is not a finding.
  * `test_a_rule_label_is_found_wherever_it_sits` and
    `test_the_sample_section_is_read_under_both_spellings` pin the two
    measured variants. A parser keyed on `辅助匹配` as a prefix reads 18 of 22
    rule lines; one keyed on `典型错误样本（原文）` reads 5 of 21 entries. Both
    report success.
  * `test_a_rule_with_no_colon_is_still_a_rule` is the third instance of the
    same class, and the one that was live: 4 of the knowledge base's 5
    disambiguation rules are written `- 若命中 X，优先按 [ID]` with no colon, and
    a bullet regex that required one dropped them all.
  * `test_a_repository_and_its_own_worktree_are_one_repository` is the
    code/KB boundary. Two checkouts on this host are worktrees of one
    repository, so comparing `.git` markers would call them different.

Nothing here reads a real knowledge base. The fixture below is built to the
shape measured on 2026-09-20, because a test that reached into a protected
repository would fail on the day that repository was edited by its author.
"""
import importlib.util
import re
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "serveros_support", ROOT / "intake" / "support.py")
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)


# ---------------------------------------------------------------------------
# The fixture: the knowledge base's real shape, measured
# ---------------------------------------------------------------------------
INDEX = """# 知识库索引与说明

> 最后更新：2026-08-25

## 错误 ID 编码规则

- CLIENT 客户端配置 / NET 网络连接 / PLAT 平台代理层

## 错误条目总表

| 错误ID | 名称 | 状态码 | 环节 | 所在文件 | 置信度 | 出现频率 |
|--------|------|--------|------|----------|--------|----------|
| PLAT-001 | 平台预算超限 ExceededBudget 402 | 402 | 平台代理层 | 03_平台/预算.md | 已确认 | 高 |
| PROV-001 | Anthropic 额度不足导致 500 + fallback 失败 + 重试耗尽 | 500 | 上游 provider | 04_上游/Anthropic.md | 推测 | 中 |

## 置信度定义

- **已确认**：来自平台实际日志
- **推测**：单案例，可能不准，agent 回答时需标注“可能是”

## 维护说明

- 回答员工时优先匹配正式条目的字段规则，不直接引用待整理样本
"""

#: The first spelling: 16 of the knowledge base's 21 entries use this one.
ENTRY_BUDGET = """## [PLAT-001] 平台预算超限 ExceededBudget 402

### 元数据
- 所属环节：平台代理层（预算/配额）
- 错误类型：预算超限
- 置信度：已确认（来自 1,064 条平台实际报错）
- 出现频率：中
- 最后更新：2026-08-24

### 字段匹配规则
- 主匹配：`status_code == 402` && `message` 包含 "ExceededBudget"
- 辅助匹配：`message` 包含 "over budget"、"Spend="、"Budget="
- 嵌套检查：通常出现在顶层 message
- 排除：`Authentication Error - Expired Key` 属于平台 Key 过期，按 [PLAT-002] 处理

### 典型错误样本（原文，已脱敏）

```
Error Code: 402
Message: ExceededBudget: User=<USER> over budget. Spend=20.0, Budget=20.0
```

### 根因分析
- 主根因：平台预算额度已用完。

### 解决方法
1. 等待预算自动刷新。
2. 联系平台管理员提升预算。用户标识 User=ou_9f3c1a77bb22 不要完整回显。

### 是否可重试
- **不建议立即重试**。

### 关联信息
- 易混淆：[PROV-001] Anthropic 额度不足
- 注意：回答时不要完整回显 User=ou_xxx。
"""

#: The second spelling: 5 of 21. A parser keyed on the first one misses these.
ENTRY_ANTHROPIC = """## [PROV-001] Anthropic 额度不足导致 500 + fallback 失败 + 重试耗尽

### 元数据
- 所属环节：上游 provider
- 错误类型：额度不足
- 置信度：推测
- 出现频率：低
- 最后更新：2026-08-24

### 字段匹配规则
- 主匹配：`status_code == 500` && `message` 包含 "litellm.APIConnectionError" && `message` 包含 "AnthropicException"
- 辅助匹配（内层真实错误）：`message` 包含 "用户额度不足" 或 "剩余额度"
- GLM/智谱辅助匹配：`message` 包含 "code=1113"
- 其他辅助匹配：`message` 包含 "无可用资源包"
- fallback 失败标识：`message` 包含 "No fallback model group found"

### 典型错误样本（原文）

```
Error Code: 500
Message: litellm.APIConnectionError: AnthropicException
```

### 根因分析
- 主根因：上游 Anthropic 账户余额不足。

### 解决方法
1. 充值上游账户。

### 是否可重试
- 充值后可重试。

### 关联信息
- 易混淆：[PLAT-001] 平台预算超限
"""

#: A second entry whose single-literal rule collides with PROV-001's
#: three-conjunct rule on the same question. This is the outer-layer /
#: inner-cause pair the knowledge base's own precedence sentence is about.
ENTRY_NET = """## [NET-001] 上游连接或超时异常

### 元数据
- 所属环节：网络连接
- 错误类型：连接异常
- 置信度：已确认（来自 529 条平台实际报错）
- 出现频率：高
- 最后更新：2026-08-25

### 字段匹配规则
- 主匹配：`APIConnectionError`、`Timeout`
- 若明确是流式响应已经开始后中断，优先按 [PROV-001]

### 典型错误样本（原文，已脱敏）

```
APIConnectionError: connection reset
```

### 根因分析
- 主根因：网络层连接中断。

### 解决方法
1. 确认网络连通后重试。

### 是否可重试
- 可以重试。

### 关联信息
- 易混淆：[RESP-001] 流式响应中断
"""

SAMPLE = """# 待整理样本

本目录内容为未验证的原始数据，agent 回答时不应直接引用
"""

#: A string that appears ONLY in the uncurated sample. If any field of any
#: answer contains it, the chain quoted a sample.
UNCURATED = "UNCURATED_SAMPLE_7f3a1c_should_never_be_quoted"

UNREADABLE_ENTRY = """## [PLAT-002] 一个没有必需节的条目

### 元数据
- 置信度：可能吧
- 最后更新：2026-01-01
"""


def a_knowledge_base(root: Path, *, index=INDEX, entries=None, sample=SAMPLE,
                     extra_files=None) -> Path:
    """A knowledge base with the shape the real one has, in a temp directory."""
    kb = root / "knowledge"
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "00_索引与说明.md").write_text(index, encoding="utf-8")
    for rel, text in (entries if entries is not None
                      else {"03_平台/预算.md": ENTRY_BUDGET,
                            "04_上游/Anthropic.md": ENTRY_ANTHROPIC,
                            "02_网络/连接.md": ENTRY_NET}).items():
        path = kb / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if sample is not None:
        (kb / "99_待整理样本").mkdir(exist_ok=True)
        (kb / "99_待整理样本" / "README.md").write_text(sample, encoding="utf-8")
        (kb / "99_待整理样本" / "2026-08-25_未分类错误.json").write_text(
            '{"note": "%s"}' % UNCURATED, encoding="utf-8")
    (kb / "全量错误分析统计.md").write_text(
        "# 全量错误分析统计\n\n## 结果使用规则\n\n"
        "- 正式条目优先解释错误链中最内层的真实根因。\n", encoding="utf-8")
    for rel, text in (extra_files or {}).items():
        path = kb / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return kb


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.kb = a_knowledge_base(self.root)

    def tearDown(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# The two measured variants, and the third instance of the same class
# ---------------------------------------------------------------------------
class ReadingVariantsTest(Fixture):
    """A parser that reads a subset and reports success is the defect."""

    def test_the_sample_section_is_read_under_both_spellings(self):
        reading = support.read_entries(self.kb)
        by_id = {e["id"]: e for e in reading["entries"]}
        self.assertEqual(by_id["PLAT-001"]["sample_section"], "典型错误样本（原文，已脱敏）")
        self.assertEqual(by_id["PROV-001"]["sample_section"], "典型错误样本（原文）")
        self.assertTrue(by_id["PLAT-001"]["sample_text"])
        self.assertTrue(by_id["PROV-001"]["sample_text"])

    def test_the_contract_declares_a_prefix_that_covers_both_spellings(self):
        """The measured spellings are pinned here, not in the contract.

        If the contract narrowed to one spelling, this fails -- which is the
        point. A contract and a test that both read the same constant would
        agree with each other about a knowledge base neither of them read.
        """
        prefix = support.contract()["kb"]["entry"]["sample_section_prefix"]
        for measured in ("典型错误样本（原文，已脱敏）", "典型错误样本（原文）"):
            self.assertTrue(measured.startswith(prefix),
                            f"{measured!r} is not covered by prefix {prefix!r}")

    def test_a_rule_label_is_found_wherever_it_sits(self):
        """Three of the five auxiliary labels END with the label, not start."""
        entry = {e["id"]: e for e in support.read_entries(self.kb)["entries"]}["PROV-001"]
        labels = [r["label"] for r in entry["rules"] if r["kind"] == "auxiliary"]
        self.assertEqual(sorted(labels),
                         sorted(["辅助匹配（内层真实错误）", "GLM/智谱辅助匹配",
                                 "其他辅助匹配"]))

    def test_a_rule_with_no_colon_is_still_a_rule(self):
        """`- 若明确是…，优先按 [PROV-001]` has no colon and is still a rule."""
        entry = {e["id"]: e for e in support.read_entries(self.kb)["entries"]}["NET-001"]
        self.assertEqual([r["targets"] for r in entry["redirects"]], [["PROV-001"]])

    def test_a_colon_less_metadata_bullet_does_not_become_a_field(self):
        metadata = {e["id"]: e for e in support.read_entries(self.kb)["entries"]}["NET-001"]
        self.assertIsNone(metadata["metadata"].get(None))
        self.assertEqual(metadata["metadata"]["置信度"], "已确认（来自 529 条平台实际报错）")


# ---------------------------------------------------------------------------
# Rules, as text
# ---------------------------------------------------------------------------
class RuleEvaluationTest(Fixture):
    def test_and_joined_predicates_all_have_to_hold(self):
        ok = support._evaluate_rule('`status_code == 402` && `message` 包含 "ExceededBudget"',
                                    status_code=402, message="ExceededBudget over budget")
        self.assertEqual(ok["verdict"], support.FIRES)
        wrong_code = support._evaluate_rule(
            '`status_code == 402` && `message` 包含 "ExceededBudget"',
            status_code=429, message="ExceededBudget over budget")
        self.assertEqual(wrong_code["verdict"], support.NO)

    def test_a_conjunct_list_is_satisfied_by_any_one_literal(self):
        rule = '`message` 包含 "over budget"、"Spend="、"Budget="'
        for message in ("over budget", "Spend=1", "Budget=2"):
            self.assertEqual(
                support._evaluate_rule(rule, status_code=None, message=message)["verdict"],
                support.FIRES, message)

    def test_a_field_name_is_not_a_literal(self):
        """`` `message` 包含 "X" `` must not look for the string `message`."""
        verdict = support._evaluate_rule('`message` 包含 "ExceededBudget"',
                                         status_code=None, message="nothing here")
        self.assertEqual(verdict["verdict"], support.NO)

    def test_the_three_quote_styles_are_all_literals(self):
        for text in ('`code=1210`', '"ExceededBudget"', "“请使用 low、high 或 max”"):
            verdict = support._evaluate_rule(text, status_code=None,
                                             message="prefix " + text.strip('`"“”') + " suffix")
            self.assertEqual(verdict["verdict"], support.FIRES, text)

    def test_a_rule_that_needs_an_unsupplied_field_is_maybe_and_not_no(self):
        """The whole difference between "it does not fire" and "I cannot tell"."""
        verdict = support._evaluate_rule('`status_code == 402` && `message` 包含 "X"',
                                         status_code=None, message="X")
        self.assertEqual(verdict["verdict"], support.MAYBE)
        self.assertEqual(verdict["missing"], ["status_code"])

    def test_prose_in_a_rule_never_fires_and_names_itself(self):
        verdict = support._evaluate_rule(
            '`litellm.AuthenticationError` 且包含 Provider 异常',
            status_code=None, message="litellm.AuthenticationError: something")
        self.assertEqual(verdict["verdict"], support.MAYBE)
        self.assertEqual(verdict["unparsed"], ["包含 Provider 异常"])


# ---------------------------------------------------------------------------
# The outcomes, and the exit codes that carry them
# ---------------------------------------------------------------------------
class OutcomeTest(Fixture):
    def ask(self, **kwargs):
        return support.answer(self.kb, **kwargs)

    def test_one_primary_fires_and_the_answer_comes_from_that_entry(self):
        result = self.ask(status_code=402, message="ExceededBudget: over budget")
        self.assertEqual(result["outcome"], "ANSWERED")
        self.assertEqual(result["chosen"]["id"], "PLAT-001")

    def test_an_answer_carries_its_provenance_and_its_confidence(self):
        result = self.ask(status_code=402, message="ExceededBudget: over budget")
        provenance = result["answer"]["provenance"][0]
        self.assertEqual(provenance["entry"], "PLAT-001")
        self.assertEqual(provenance["file"], "03_平台/预算.md")
        self.assertEqual(provenance["updated"], "2026-08-24")
        self.assertEqual(provenance["matched_by"], "主匹配")
        self.assertEqual(result["answer"]["confidence"]["level"], "已确认")
        self.assertEqual(result["answer"]["confidence"]["as_written"],
                         "已确认（来自 1,064 条平台实际报错）")

    def test_the_hedge_is_a_field_and_not_prose(self):
        """`推测` is the level the index says to mark. It travels as data."""
        result = self.ask(message="用户额度不足，剩余额度 0")
        self.assertEqual(result["outcome"], "ANSWERED_FROM_AUXILIARY")
        self.assertEqual(result["chosen"]["id"], "PROV-001")
        self.assertEqual(result["answer"]["confidence"]["hedge"], "可能是")

    def test_only_the_answers_own_sections_are_assembled(self):
        result = self.ask(status_code=402, message="ExceededBudget: over budget")
        names = set(result["answer"]["text"])
        self.assertEqual(names, {"根因分析", "解决方法", "是否可重试"})
        # Not the rule that matched, and not the raw sample: the first is why
        # this entry was reached and the second is what the person already has.
        self.assertNotIn("字段匹配规则", names)
        self.assertNotIn("典型错误样本（原文，已脱敏）", names)

    def test_no_match_is_a_reading_and_carries_an_improvement_candidate(self):
        result = self.ask(status_code=418, message="I am a teapot")
        self.assertEqual(result["outcome"], "NO_ENTRY_MATCHES")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["improvement"]["kind"], "NO_ENTRY_MATCHES")

    def test_a_question_missing_one_field_names_that_field(self):
        result = self.ask(status_code=402)
        self.assertEqual(result["outcome"], "NEEDS_MORE_INPUT")
        self.assertEqual(result["missing"], ["message"])
        self.assertIn("PLAT-001", [c["id"] for c in result["candidates"]])

    def test_a_question_with_nothing_to_go_on_is_refused_rather_than_guessed(self):
        with self.assertRaises(support.Refused):
            support.match(self.kb)

    def test_an_unreadable_knowledge_base_is_not_a_no_match(self):
        result = self.ask(status_code=402, message="ExceededBudget: over budget")
        missing = support.answer(self.root / "nope", status_code=402,
                                 message="ExceededBudget: over budget")
        self.assertEqual(result["outcome"], "ANSWERED")
        self.assertEqual(missing["outcome"], "KB_UNREADABLE")
        self.assertIsNone(missing["answer"])
        self.assertIsNotNone(missing["unreadable"])

    def test_every_declared_outcome_has_an_exit_code_and_the_codes_are_the_three(self):
        codes = support.contract()["exit_codes"]
        for outcome, code in codes.items():
            with self.subTest(outcome=outcome):
                self.assertIn(code, (0, 1, 2))
                self.assertEqual(support.exit_code_for(outcome), code)
        self.assertEqual(codes["ANSWERED"], 0)
        self.assertEqual(codes["NO_ENTRY_MATCHES"], 2)
        self.assertEqual(codes["CODE_KB_SAME_REPO"], 1)

    def test_a_word_the_contract_does_not_declare_has_no_exit_code(self):
        with self.assertRaises(support.Refused):
            support.exit_code_for("SOMETHING_A_PERSON_TYPED")


# ---------------------------------------------------------------------------
# The tie-break: the knowledge base's own precedence rule, applied and reported
# ---------------------------------------------------------------------------
class TieBreakTest(Fixture):
    QUESTION = {"status_code": 500,
                "message": "litellm.APIConnectionError: AnthropicException"}

    def test_two_primaries_collide_because_both_rules_really_do_fire(self):
        """The collision is real, not a matcher bug: NET-001's literal is a
        substring of PROV-001's message, so NET-001's own rule is satisfied."""
        result = support.answer(self.kb, **self.QUESTION)
        self.assertIn(result["outcome"], ("ANSWERED", "AMBIGUOUS_MATCH"))

    def test_the_innermost_root_cause_wins_and_the_resolution_is_reported(self):
        result = support.answer(self.kb, **self.QUESTION)
        self.assertEqual(result["outcome"], "ANSWERED")
        self.assertEqual(result["chosen"]["id"], "PROV-001")
        tie = result["tie_break"]
        self.assertEqual(tie["rule"], "most_specific_firing_primary_wins")
        self.assertEqual(tie["authority"], "全量错误分析统计.md")
        self.assertEqual(tie["specificity"], {"PROV-001": 3, "NET-001": 1})
        self.assertEqual(tie["runner_up"]["id"], "NET-001")

    def test_the_sentence_the_resolution_came_from_travels_with_it(self):
        """A resolution with no citable authority is a heuristic."""
        result = support.answer(self.kb, **self.QUESTION)
        self.assertIn("最内层的真实根因", result["tie_break"]["quote"])

    def test_an_ambiguous_match_of_equal_specificity_is_not_broken(self):
        other = ENTRY_NET.replace("## [NET-001] 上游连接或超时异常",
                                  "## [NET-002] 另一个连接异常")
        kb = a_knowledge_base(
            self.root / "second",
            entries={"04_上游/Anthropic.md": ENTRY_ANTHROPIC,
                     "02_网络/连接.md": ENTRY_NET,
                     "02_网络/另一个.md": other.replace(
                         "`APIConnectionError`、`Timeout`",
                         "`status_code == 500` && `message` 包含 "
                         '"litellm.APIConnectionError" && `message` 包含 '
                         '"AnthropicException"')},
            sample=None)
        result = support.answer(kb, **self.QUESTION)
        self.assertEqual(result["outcome"], "AMBIGUOUS_MATCH")
        self.assertIsNone(result["answer"])
        self.assertIsNone(result["tie_break"])


# ---------------------------------------------------------------------------
# Redirects: an entry that names another entry
# ---------------------------------------------------------------------------
class RedirectTest(Fixture):
    def test_a_redirect_only_fires_when_its_target_also_fired(self):
        """`排除：… 按 [PLAT-002] 处理` names an entry the fixture lacks, so
        the redirect has nothing to hand the question to. It must not fire."""
        result = support.answer(self.kb, status_code=402,
                                message="ExceededBudget: over budget")
        self.assertEqual(result["outcome"], "ANSWERED")
        self.assertEqual(result["chosen"]["id"], "PLAT-001")
        self.assertEqual(result["redirects"], [])

    def test_a_redirect_hands_the_question_to_its_target(self):
        """Both fire; the 排除 clause says which one owns the error."""
        kb = a_knowledge_base(
            self.root / "third",
            entries={"03_平台/预算.md": ENTRY_BUDGET,
                     "03_平台/过期Key.md": ENTRY_BUDGET.replace(
                         "## [PLAT-001] 平台预算超限 ExceededBudget 402",
                         "## [PLAT-002] 平台 Key 已过期").replace(
                         "- 主匹配：`status_code == 402` && `message` 包含 \"ExceededBudget\"",
                         '- 主匹配：`message` 包含 "Authentication Error - Expired Key"')},
            sample=None)
        result = support.answer(
            kb, status_code=402,
            message="Authentication Error - Expired Key as well as ExceededBudget")
        self.assertEqual(result["outcome"], "ANSWERED")
        self.assertEqual(result["chosen"]["id"], "PLAT-002")
        self.assertEqual([r["dropped"] for r in result["redirects"]], ["PLAT-001"])
        self.assertEqual(result["redirects"][0]["kept"], ["PLAT-002"])

    def test_a_prose_conditional_redirect_does_not_fire_and_is_reported(self):
        check = support.check(self.kb, as_of="2026-09-20")
        prose = [f for f in check["findings"] if f["code"] == "RULE_CONTAINS_PROSE"]
        self.assertIn("NET-001", [f["subject"] for f in prose])


# ---------------------------------------------------------------------------
# Redaction, and the two leaks that were real
# ---------------------------------------------------------------------------
class RedactionTest(Fixture):
    def test_an_identifier_is_redacted(self):
        result = support.answer(self.kb, status_code=402,
                                message="ExceededBudget: over budget")
        text = "\n".join(result["answer"]["text"].values())
        self.assertNotIn("ou_9f3c1a77bb22", text)
        self.assertIn("<已脱敏>", text)

    def test_the_pattern_has_no_length_floor(self):
        """The knowledge base's own example is 「User=ou_xxx」 -- three chars.

        This was `{6,}`, so it passed a fixture built from a long identifier and
        leaked the short one the knowledge base actually warns about.
        """
        for short in ("ou_x", "ou_abc", "op_1"):
            cleaned, hits = support.redact(f"User={short} failed")
            with self.subTest(short=short):
                self.assertNotIn(short, cleaned)
                self.assertTrue(hits)

    def test_the_improvement_candidate_is_redacted_too(self):
        """The one field NOT assembled from the entry, and the one that leaked."""
        result = support.answer(
            self.kb, status_code=402,
            message="ExceededBudget: User=ou_9f3c1a77bb22 over budget. Spend=9, Budget=1")
        improvement = result["improvement"]
        self.assertIsNotNone(improvement)
        blob = repr(improvement)
        self.assertNotIn("ou_9f3c1a77bb22", blob)
        self.assertIn("<*>", blob)

    def test_a_variant_is_not_reported_when_only_the_placeholder_differs(self):
        """The knowledge base writes `User=<USER>`; a question carries a value.

        Comparing raw text made this candidate fire on every question that
        exactly matched an entry, which is a finding that carries nothing.
        """
        result = support.answer(
            self.kb, status_code=402,
            message="Message: ExceededBudget: User=ou_9f3c1a77bb22 over budget. "
                    "Spend=20.0, Budget=20.0")
        self.assertEqual(result["outcome"], "ANSWERED")
        self.assertIsNone(result["improvement"])

    def test_a_variant_is_reported_when_the_line_is_genuinely_new(self):
        result = support.answer(
            self.kb, status_code=402,
            message="Message: ExceededBudget: over budget by a completely "
                    "different route than the one on file. Spend=99, Budget=1")
        self.assertEqual(result["improvement"]["kind"], "ANSWERED_BUT_VARIANT_UNSEEN")
        self.assertEqual(result["improvement"]["entry"], "PLAT-001")

    def test_a_line_the_sample_contains_is_not_reported_as_unseen(self):
        """The sample is BUILT on this token; calling it unseen is the defect.

        Measured live against the real knowledge base:
        `--status-code 402 --message 'ExceededBudget'` was reported as a new
        variant against a sample beginning `ExceededBudget: User=<USER>`. The
        comparison was set membership -- is this line one of the sample's lines
        -- which is only ever true when the question reproduces the sample
        verbatim.
        """
        result = support.answer(self.kb, status_code=402, message="ExceededBudget")
        self.assertEqual(result["outcome"], "ANSWERED")
        self.assertIsNone(result["improvement"])

    def test_containment_does_not_swallow_a_reference_line(self):
        """The sample's own leading line, quoted back shorter, is still seen."""
        result = support.answer(
            self.kb, status_code=402,
            message="ExceededBudget: User=<USER> over budget. Spend=20.0, Budget=20.0")
        self.assertIsNone(result["improvement"])

    def test_a_recurrence_with_a_different_volatile_value_is_still_seen(self):
        """The second sighting of an error is a second sighting.

        The sample says `Spend=20.0`; a recurrence says something else, because
        that is what a recurrence is. Comparing the literal text made this
        candidate fire on every recurrence -- the promotion rule 「出现 ≥ 2 次」
        is exactly about the second time, so it was being told the thing it
        already knew, and never the thing it was waiting for.
        """
        result = support.answer(
            self.kb, status_code=402,
            message="ExceededBudget: User=<USER> over budget. "
                    "Spend=87.4512, Budget=20.0")
        self.assertEqual(result["outcome"], "ANSWERED")
        self.assertIsNone(result["improvement"],
                          "a different spend amount is the same error")

    def test_the_shape_key_is_the_words_and_not_the_numbers(self):
        self.assertEqual(support._shape_of("ExceededBudget: Spend=20.09863283999999"),
                         support._shape_of("ExceededBudget: Spend=20.0"))
        self.assertNotEqual(support._shape_of("ExceededBudget: over budget"),
                            support._shape_of("ExceededBudget: key rotated"))

    def test_an_uncurated_sample_is_never_quoted(self):
        """`99_待整理样本/` is readable and is never a source of an answer."""
        for status_code, message in ((402, "ExceededBudget: over budget"),
                                     (500, "litellm.APIConnectionError: AnthropicException"),
                                     (418, "teapot"),
                                     (None, "用户额度不足")):
            result = support.answer(self.kb, status_code=status_code, message=message)
            with self.subTest(status_code=status_code):
                self.assertNotIn(UNCURATED, repr(result))

    def test_the_uncurated_directory_is_reported_as_excluded_not_as_entries(self):
        listing = support.listing(self.kb)
        self.assertNotIn(UNCURATED, repr(listing))
        self.assertTrue(any("99_待整理样本" in e for e in listing["excluded"]))
        self.assertEqual([e["id"] for e in listing["entries"]],
                         ["NET-001", "PLAT-001", "PROV-001"])

    def test_an_email_and_a_key_are_redacted(self):
        cleaned, hits = support.redact(
            "write to someone@example.com or use sk-abcdefghijklmnopqrst")
        self.assertNotIn("someone@example.com", cleaned)
        self.assertNotIn("sk-abcdefghijklmnopqrst", cleaned)
        self.assertEqual(len(hits), 2)


# ---------------------------------------------------------------------------
# Leads: 辅助匹配 corroborates, and does not identify
# ---------------------------------------------------------------------------
class AuxiliaryLeadTest(Fixture):
    """Several leads and no 主匹配 is not a tie to break.

    The first version ranked leads by `len(entry["auxiliary"])` -- the number of
    辅助匹配 bullets the entry carries, which is a fact about the document's
    formatting. Measured live, `--message '用户额度不足'` with no status code
    fires the auxiliary rules of three PROV entries and the module answered
    AMBIGUOUS_MATCH on whichever had the most bullets.
    """

    #: One lead only, and the entry's 主匹配 did not fire.
    def test_a_lone_lead_is_answered_and_says_the_primary_did_not_corroborate(self):
        result = support.answer(self.kb, message="用户额度不足，剩余额度 0")
        self.assertEqual(result["outcome"], "ANSWERED_FROM_AUXILIARY")
        self.assertEqual(result["chosen"]["id"], "PROV-001")
        self.assertFalse(result["corroborated_by_primary"])

    def test_several_leads_with_a_missing_field_ask_for_that_field(self):
        """The reply is a smaller next step, not a knowledge dump."""
        kb = a_knowledge_base(
            self.root / "leads", sample=None,
            entries={"04_上游/Anthropic.md": ENTRY_ANTHROPIC,
                     "04_上游/另一个.md": ENTRY_ANTHROPIC.replace(
                         "## [PROV-001]", "## [PROV-003]").replace(
                         "- 主匹配：`status_code == 500` && `message` 包含 "
                         '"litellm.APIConnectionError" && `message` 包含 '
                         '"AnthropicException"',
                         "- 主匹配：`status_code == 429`")})
        result = support.answer(kb, message="用户额度不足，剩余额度 0")
        self.assertEqual(result["outcome"], "NEEDS_MORE_INPUT")
        self.assertEqual(result["missing"], ["status_code"])
        self.assertIsNone(result["chosen"])
        self.assertEqual(sorted(c["id"] for c in result["candidates"]),
                         ["PROV-001", "PROV-003"])
        # And each lead carries how it was reached, or the reader cannot tell
        # a lead from an identification.
        self.assertTrue(all(c["led_by"] for c in result["candidates"]))

    def test_the_line_count_of_the_entry_decides_nothing(self):
        """Same two leads, different bullet counts, same outcome.

        This is the assertion the defect fails: padding one entry's 辅助匹配
        section with bullets that do not fire used to change the outcome.
        """
        base = ENTRY_ANTHROPIC.replace("## [PROV-001]", "## [PROV-003]")
        # The padding goes INSIDE 字段匹配规则, where 辅助匹配 bullets are read
        # from -- put anywhere else it would not be a rule at all and the test
        # would pass without touching the line the defect was on.
        padded = base.replace(
            "- 其他辅助匹配：`message` 包含 \"无可用资源包\"",
            "- 其他辅助匹配：`message` 包含 \"无可用资源包\"\n"
            "- 不相关的辅助匹配甲：`message` 包含 \"zzz-zebra\"\n"
            "- 不相关的辅助匹配乙：`message` 包含 \"yyy-yak\"")
        plain = a_knowledge_base(
            self.root / "plain", sample=None,
            entries={"04_上游/Anthropic.md": ENTRY_ANTHROPIC,
                     "04_上游/另一个.md": base})
        padded_kb = a_knowledge_base(
            self.root / "padded", sample=None,
            entries={"04_上游/Anthropic.md": ENTRY_ANTHROPIC,
                     "04_上游/另一个.md": padded})
        question = {"message": "用户额度不足，剩余额度 0"}
        counts = {e["id"]: sum(1 for r in e["rules"] if r["kind"] == "auxiliary")
                  for e in support.read_entries(padded_kb)["entries"]}
        # The padding really did land: the point of the test is that this
        # difference exists and decides nothing.
        self.assertEqual(counts["PROV-003"], 5)  # three real + two padding
        self.assertEqual(counts["PROV-001"], 3)
        self.assertEqual(support.answer(plain, **question)["outcome"],
                         support.answer(padded_kb, **question)["outcome"])
        self.assertEqual(support.answer(padded_kb, **question)["outcome"],
                         "NEEDS_MORE_INPUT")


# ---------------------------------------------------------------------------
# The boundary: two repositories, and the refusal when they are one
# ---------------------------------------------------------------------------
class BoundaryTest(Fixture):
    def test_the_code_root_may_not_be_the_knowledge_base(self):
        result = support.answer(self.kb, status_code=402,
                                message="ExceededBudget", code_root=self.kb)
        self.assertEqual(result["outcome"], "CODE_KB_SAME_REPO")
        self.assertIsNone(result["answer"])
        self.assertTrue(result["boundary"]["same_repository"])

    def test_two_roots_that_are_two_directories_are_two_repositories(self):
        code = self.root / "code"
        code.mkdir()
        result = support.answer(self.kb, status_code=402,
                                message="ExceededBudget: over budget", code_root=code)
        self.assertEqual(result["outcome"], "ANSWERED")
        self.assertFalse(result["boundary"]["same_repository"])

    def test_a_repository_and_its_own_worktree_are_one_repository(self):
        """Two of this host's checkouts are worktrees of one repository.

        Their `.git` markers are different FILES, so comparing the markers
        would call them different repositories and the boundary would be
        unenforced on exactly the pair it was written for.
        """
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        worktree = self.root / "wt"
        worktree.mkdir()
        # A worktree's marker is a FILE -- `gitdir: <repo>/.git/worktrees/<n>`
        # -- where the repository's own marker is a DIRECTORY. That difference
        # is exactly what a naive comparison keys on.
        (repo / ".git" / "worktrees" / "wt").mkdir(parents=True)
        (worktree / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / 'wt'}\n", encoding="utf-8")

        self.assertEqual(support.repo_identity(repo), support.repo_identity(worktree))
        # The knowledge base is checked out inside the repository and the code
        # root handed in is a worktree OF that repository. Two paths, one
        # repository, and the question the boundary exists to refuse.
        kb = a_knowledge_base(repo, sample=None)
        result = support.answer(kb, status_code=402, message="ExceededBudget",
                                code_root=worktree)
        self.assertEqual(result["outcome"], "CODE_KB_SAME_REPO")
        self.assertTrue(result["boundary"]["same_repository"])
        self.assertIsNone(result["answer"])

    def test_no_code_root_means_no_boundary_was_checked_and_it_says_so(self):
        result = support.answer(self.kb, status_code=402, message="ExceededBudget")
        self.assertIsNone(result["boundary"]["same_repository"])
        self.assertIn("no boundary was checked", result["boundary"]["because"])


# ---------------------------------------------------------------------------
# check(): the index against the entries, as a projection
# ---------------------------------------------------------------------------
class CheckTest(Fixture):
    def test_a_correct_fixture_reports_the_four_findings_it_really_has(self):
        """Not "no findings" -- the fixture is correct AND has four.

        Each one is a real fact about this knowledge base and none of them is
        noise, which is the property worth pinning: a check that goes quiet on
        a correct input is one that cannot be told from a check that never ran.
        """
        check = support.check(self.kb, as_of="2026-09-20")
        self.assertEqual(check["outcome"], "CHECKED")
        self.assertEqual(check["entry_count"], 3)
        self.assertEqual(check["index_row_count"], 2)
        self.assertEqual(check["counts"], {
            "INDEX_MISSING": 1,          # NET-001 has no index row
            "INDEX_COUNT_DISAGREES": 1,  # so the two totals differ by one
            "RULE_CONTAINS_PROSE": 1,    # NET-001's redirect is prose-conditional
            "REDIRECT_TARGET_MISSING": 1,  # PLAT-001 排除s into a [PLAT-002] that is absent
            "SAMPLE_SECTION_VARIANTS": 1,  # the fixture carries both spellings, as the real KB does
        })

    def test_an_entry_the_index_never_mentions_is_a_finding(self):
        check = support.check(self.kb, as_of="2026-09-20")
        missing = [f for f in check["findings"] if f["code"] == "INDEX_MISSING"]
        self.assertEqual([f["subject"] for f in missing], ["NET-001"])

    def test_the_confidence_is_compared_as_a_level_not_as_a_string(self):
        """The entry carries the evidence count; the index column drops it.

        Comparing the raw values reports a mismatch on every correct entry,
        which is 21 false findings on the real knowledge base.
        """
        check = support.check(self.kb, as_of="2026-09-20")
        self.assertNotIn("CONFIDENCE_LEVEL_DISAGREES", check["counts"])
        entry = {e["id"]: e for e in support.read_entries(self.kb)["entries"]}["PLAT-001"]
        self.assertEqual(entry["confidence"], "已确认（来自 1,064 条平台实际报错）")
        self.assertEqual(support._level_of(entry["confidence"]), "已确认")

    def test_a_confidence_level_the_contract_never_declared_is_a_gap(self):
        kb = a_knowledge_base(self.root / "fourth",
                              entries={"03_平台/怪.md": UNREADABLE_ENTRY}, sample=None,
                              index=INDEX.replace("PLAT-001", "PLAT-002").replace(
                                  "03_平台/预算.md", "03_平台/怪.md"))
        check = support.check(kb, as_of="2026-09-20")
        codes = check["counts"]
        self.assertIn("CONFIDENCE_LEVEL_UNKNOWN", codes)
        self.assertIn("SECTION_MISSING", codes)
        # The index has no row for this id either, and that is a second fact.
        self.assertIn("INDEX_ORPHAN", codes)

    def test_an_index_row_pointing_at_the_wrong_file_is_a_finding(self):
        """The row that looks right and points somewhere else.

        Worse than an absent row, because a reader following it lands on a real
        file that answers a different question.
        """
        kb = a_knowledge_base(
            self.root / "fifth", sample=None,
            index=INDEX.replace("| 04_上游/Anthropic.md |", "| 03_平台/预算.md |"))
        check = support.check(kb, as_of="2026-09-20")
        wrong = [f for f in check["findings"] if f["code"] == "ID_NOT_IN_NAMED_FILE"]
        self.assertEqual([f["subject"] for f in wrong], ["PROV-001"])

    def test_an_index_row_naming_a_file_that_is_not_there_is_a_finding(self):
        kb = a_knowledge_base(self.root / "sixth", sample=None,
                              index=INDEX.replace("04_上游/Anthropic.md", "04_上游/没了.md"))
        check = support.check(kb, as_of="2026-09-20")
        self.assertIn("FILE_MISSING", check["counts"])

    def test_a_stale_entry_is_only_stale_relative_to_the_as_of_it_was_given(self):
        """Never read from the clock: a check whose answer depends on when it
        ran cannot be compared against a recorded one."""
        fresh = support.check(self.kb, as_of="2026-09-20")
        self.assertNotIn("STALE", fresh["counts"])
        later = support.check(self.kb, as_of="2027-09-20")
        self.assertEqual(later["counts"].get("STALE"), 3)
        self.assertIsNone(support.check(self.kb)["as_of"])
        self.assertNotIn("STALE", support.check(self.kb)["counts"])

    def test_an_unreadable_knowledge_base_reports_a_reading_and_not_clean(self):
        """`findings: []` is what "checked and clean" and "never checked" both
        render as, so an unreadable root must not produce it."""
        check = support.check(self.root / "nowhere", as_of="2026-09-20")
        self.assertEqual(check["outcome"], "KB_UNREADABLE")
        self.assertIsNotNone(check["unreadable"])
        self.assertEqual(check["findings"], [])

    def test_a_root_with_no_entry_files_is_unreadable_not_empty(self):
        """A glob aimed at the wrong root is not a knowledge base with none."""
        empty = self.root / "empty"
        (empty / "knowledge").mkdir(parents=True)
        check = support.check(empty / "knowledge", as_of="2026-09-20")
        self.assertEqual(check["outcome"], "KB_UNREADABLE")

    def test_a_file_stamp_older_than_its_entries_is_a_finding_and_the_reverse_is_not(self):
        older = a_knowledge_base(
            self.root / "seventh",
            entries={"03_平台/预算.md": ENTRY_BUDGET.replace("2026-08-24", "2026-08-20")},
            sample=None)
        check = support.check(older, as_of="2026-09-20")
        # The file has no `> 最后更新` line at all in this fixture, so the
        # comparison has nothing to make; the entry's own three sections are
        # what it reports instead.
        self.assertNotIn("FILE_STAMP_OLDER_THAN_ENTRY", check["counts"])


# ---------------------------------------------------------------------------
# next_id: the ID is a commitment, and this only offers one
# ---------------------------------------------------------------------------
class NextIdTest(Fixture):
    def test_the_next_id_is_offered_after_the_highest_one_in_use(self):
        offered = support.next_id(self.kb, "PLAT")
        self.assertEqual(offered["offered"], "PLAT-002")
        self.assertEqual(offered["used"], [1])

    def test_a_prefix_the_contract_never_declared_is_refused(self):
        """The six prefixes are the knowledge base's 环节, not free-form tags."""
        with self.assertRaises(support.Refused):
            support.next_id(self.kb, "MADEUP")

    def test_offering_an_id_writes_nothing(self):
        before = sorted(p.relative_to(self.kb) for p in self.kb.rglob("*"))
        support.next_id(self.kb, "PLAT")
        after = sorted(p.relative_to(self.kb) for p in self.kb.rglob("*"))
        self.assertEqual(before, after)

    def test_the_offered_id_matches_the_knowledge_bases_own_format(self):
        """Pinned against the ids the knowledge base actually carries, not
        against a format string this module chose."""
        pattern = re.compile(r"^[A-Z]{2,}-\d{3}$")
        for letter in ("PLAT", "PROV", "NET"):
            with self.subTest(prefix=letter):
                self.assertTrue(pattern.match(support.next_id(self.kb, letter)["offered"]))

    def test_no_match_candidate_targets_a_real_directory(self):
        result = support.answer(self.kb, status_code=402)  # NEEDS_MORE_INPUT
        self.assertIsNone(result["improvement"])
        none_matched = support.answer(self.kb, status_code=418, message="teapot")
        offered = none_matched["improvement"]["offered_id"]
        self.assertIsNone(offered)
        self.assertIn("no entry's 主匹配 rule names status_code == 418",
                      none_matched["improvement"]["why"])


# ---------------------------------------------------------------------------
# It is read-only, and the contract says what it may emit
# ---------------------------------------------------------------------------
class ReadOnlyTest(Fixture):
    def test_the_module_has_no_write_path_to_either_root(self):
        source = (ROOT / "intake" / "support.py").read_text(encoding="utf-8")
        body = source.split('"""', 2)[2]   # past the module docstring
        for forbidden in ("write_text(", "open(", "os.remove", "os.rename",
                          "shutil.", "subprocess", "unlink(", "rmdir("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_every_outcome_the_contract_declares_has_a_sentence(self):
        """A word with no meaning is one a reader has to guess at."""
        for outcome, code in support.contract()["exit_codes"].items():
            with self.subTest(outcome=outcome):
                self.assertIsInstance(code, int)

    def test_the_declared_never_write_rule_is_in_the_contract(self):
        self.assertTrue(support.contract()["improvement"]["never_writes"])

    def test_the_boundary_is_a_refusal_and_not_a_warning(self):
        """A chain handed one checkout for both roots has no boundary to keep."""
        self.assertEqual(
            support.contract()["exit_codes"]["CODE_KB_SAME_REPO"], 1)


if __name__ == "__main__":
    unittest.main()
