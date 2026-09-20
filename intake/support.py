#!/usr/bin/env python3
"""A support question, and where the answer is allowed to come from.

Why this file exists
--------------------
§S's chain ends in a Deliverable. A support question is the same shape and had
no producer: an error is pasted, and the first thing that has to happen is not
"write a reply" -- it is "find out whether this instance already knows". Those
are different tasks. A bot that conflates them answers fluently from nothing,
and the person reading the reply has no way to tell that reply apart from one
backed by an entry.

So the reading this module produces is three-valued and always named:

  * ANSWERED                 an entry's own 主匹配 rule fired, and the answer is
                             assembled from that entry's sections only.
  * ANSWERED_FROM_AUXILIARY  no 主匹配 fired; a 辅助匹配 did. Real knowledge,
                             weaker rule, and `matched_by` says which.
  * NEEDS_MORE_INPUT         a 主匹配 could not be evaluated because a field
                             was not supplied. The reply names the field.
  * AMBIGUOUS_MATCH          two entries' 主匹配 rules both fired.
  * NO_ENTRY_MATCHES         nothing matched, and the gap is a fact about the
                             knowledge base.

None of those renders as an empty answer, which is the whole point: `[]` is
what "I found nothing" and "I could not look" both render as, and this product
exists to keep them apart.

What it is not
--------------
It is not a second copy of the knowledge base. Every string it returns comes
out of a file under `kb_root` at read time, and the shape it expects lives in
`support-contract.yaml` where a gate can read it. The KB is a separate
repository with its own author; `kb_root` is a parameter and never a constant.

It is not a writer. It has no write path to either root. An improvement
candidate is a record the caller may act on -- `never_writes` in the contract
is the sentence, and the reason is that the knowledge base is a protected
repository and §B authorizes source changes in this round's worktree only.

It does not evaluate the knowledge base. `字段匹配规则` is prose with literals
in it, and this module matches those literals TEXTUALLY. Turning `主匹配` into
an evaluator would make 「a document may run code here」 true of a repository
whose entire purpose is to be read. The cost is a matcher that can be wrong by
under-matching, which is the direction that reports a gap -- and it is reported
rather than hidden: a rule carrying a fragment no matcher can evaluate is
`RULE_CONTAINS_PROSE`, a finding against the knowledge base.

Read-only. It imports no mutating verb and has no execute path.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
CONTRACT = _HERE / "support-contract.yaml"

#: The three readings of a rule. `MAYBE` is a first-class verdict and not a
#: flavour of `NO`: a rule that could not be evaluated because a field is
#: missing is neither satisfied nor unsatisfied, and collapsing it into `NO`
#: would make an entry silently unreachable for a question that omitted one
#: field.
FIRES, NO, MAYBE = "FIRES", "NO", "MAYBE"

EXIT_OK = 0
EXIT_FAULT = 1
EXIT_COULD_NOT_ANSWER = 2


class Refused(ValueError):
    """A question could not be put. Argument-level, never a reading.

    Same split as `recipes.py#Refused`: this is the caller's mistake and it
    exits EXIT_FAULT. A reading that came back with no answer exits
    EXIT_COULD_NOT_ANSWER. The two are never the same exit code.
    """


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
_CONTRACT_CACHE: dict | None = None


def load_contract(path: str | Path = CONTRACT) -> dict:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    expected = "serveros/support-contract.v1"
    if document.get("schema") != expected:
        raise Refused(f"{path} declares schema {document.get('schema')!r}, not {expected!r}")
    return document


def contract() -> dict:
    global _CONTRACT_CACHE
    if _CONTRACT_CACHE is None:
        _CONTRACT_CACHE = load_contract()
    return _CONTRACT_CACHE


# ---------------------------------------------------------------------------
# Roots: what was read, and the boundary between the two
# ---------------------------------------------------------------------------
def _git_marker(path: Path) -> Path | None:
    """The `.git` entry at or above `path`, or None."""
    for candidate in (path, *path.parents):
        marker = candidate / ".git"
        if marker.exists():
            return marker
    return None


def repo_identity(path: Path) -> Path | None:
    """The repository a path belongs to, as one identity.

    A worktree's `.git` is a FILE holding `gitdir: <repo>/.git/worktrees/<n>`,
    and a plain checkout's is a directory. Two of this host's checkouts are
    worktrees of one repository and resolve to different `.git` paths, so
    comparing the markers would call them different repositories. Resolving the
    file form back to the repo it names is what makes the comparison mean
    "same repository" rather than "same `.git` file".
    """
    marker = _git_marker(path)
    if marker is None:
        return None
    if marker.is_dir():
        return marker.resolve()
    text = marker.read_text(encoding="utf-8", errors="replace").strip()
    if not text.startswith("gitdir:"):
        return marker.resolve()
    target = Path(text.split(":", 1)[1].strip())
    if not target.is_absolute():
        target = (marker.parent / target).resolve()
    # `<repo>/.git/worktrees/<name>` -> `<repo>/.git`
    if target.parent.name == "worktrees" and target.parent.parent.name == ".git":
        return target.parent.parent.resolve()
    return target.resolve()


def _boundary(kb_root: Path, code_root: Path | None) -> dict:
    """Whether the two roots are two repositories. Refuses when they are one."""
    if code_root is None:
        return {"code_root": None, "same_repository": None,
                "because": "no code_root was supplied, so no boundary was checked"}
    kb = Path(kb_root).expanduser()
    code = Path(code_root).expanduser()
    kb_ident, code_ident = repo_identity(kb), repo_identity(code)
    if kb_ident is not None and code_ident is not None:
        same = kb_ident == code_ident
    else:
        # One or both are not checkouts. Then the only claim available is about
        # the paths themselves, and it is made as a narrower one.
        same = kb.resolve() == code.resolve() if kb.exists() and code.exists() else False
    return {
        "code_root": str(code),
        "same_repository": same,
        "kb_identity": str(kb_ident) if kb_ident else None,
        "code_identity": str(code_ident) if code_ident else None,
        "because": (
            "the two roots resolve to one repository, so there is no boundary "
            "to keep -- §J's 双仓边界 / code/KB不混, and the support code "
            "repository's own 「知识不在这里」 would be false for this caller"
            if same else
            "the two roots are not the same repository"),
    }


# ---------------------------------------------------------------------------
# Reading the index
# ---------------------------------------------------------------------------
def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_index(kb_root: str | Path) -> dict:
    """The index table, and whether it was there at all.

    `rows: []` with `unreadable` set is a DIFFERENT fact from `rows: []` with
    `unreadable` unset, and both are different from a path that does not
    exist. Every one of the three is reported.
    """
    root = Path(kb_root).expanduser()
    name = contract()["kb"]["index"]
    path = root / name
    result = {"kb_root": str(root), "path": _rel(path, root), "exists": path.exists(),
              "rows": [], "row_count": 0, "unreadable": None}
    if not path.exists():
        result["unreadable"] = f"{name} is not in {root}"
        return result
    try:
        text = _read_text(path)
    except OSError as exc:
        result["unreadable"] = f"{name} could not be read: {exc}"
        return result
    columns = contract()["kb"]["index_table"]["columns"]
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(columns) or cells[0] in ("错误ID",) or set(cells[0]) <= set("-: "):
            continue
        if not re.fullmatch(r"[A-Z]+-\d+", cells[0]):
            continue
        result["rows"].append(dict(zip(columns, cells)))
    result["row_count"] = len(result["rows"])
    return result


# ---------------------------------------------------------------------------
# Reading the entries
# ---------------------------------------------------------------------------
def _split_sections(body: str) -> dict:
    """`### <name>` -> text, in file order."""
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^###\s+(?P<name>.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group("name")
            buf = []
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _section(sections: dict, want: str) -> tuple[str | None, str | None]:
    """(name, text) for the section whose name starts with `want`."""
    for name, text in sections.items():
        if name.startswith(want):
            return name, text
    return None, None


def _bullets(text: str) -> list[tuple[str | None, str]]:
    """Bullet lines -> (label, value), in order.

    `label` is None for a bullet with no `：` in it, and that case is not an
    edge: it is how 4 of the knowledge base's 5 disambiguation rules are
    written.

        - 排除：`Authentication Error - Expired Key` … 按 [PLAT-005] 处理
        - 若命中 `code=1210`，优先归入 [CLIENT-001]

    The first was the only shape the first version of this parser read, so the
    four colon-less ones were dropped by a bullet regex and the disambiguation
    silently never happened -- the same silent subset as the sample heading and
    the rule labels, found the same way. A bullet is a bullet; whether it has a
    label in front of the value is a property of the sentence, not of the
    format.
    """
    out = []
    for line in text.splitlines():
        m = re.match(r"^-\s*(?P<rest>.+?)\s*$", line)
        if not m:
            continue
        rest = m.group("rest")
        labelled = re.match(r"^(?P<label>[^：:]+?)\s*[：:]\s*(?P<value>.*)$", rest)
        if labelled:
            out.append((labelled.group("label").strip(), labelled.group("value").strip()))
        else:
            out.append((None, rest.strip()))
    return out


def _split_conjuncts(text: str) -> list[str]:
    parts = [text]
    for token in contract()["match"]["and_tokens"]:
        parts = [piece for part in parts for piece in part.split(token)]
    return [p.strip() for p in parts if p.strip()]


def _tokens(conjunct: str) -> list[str]:
    pattern = re.compile(contract()["match"]["literal_pattern"])
    return [next(g for g in m.groups() if g is not None) for m in pattern.finditer(conjunct)]


def _evaluate_rule(text: str, *, status_code, message) -> dict:
    """One rule line, read as text. Returns a verdict, never an exception.

    The conjunction is `&&`/`且`; within one conjunct a list of literals is
    satisfied by ANY of them appearing. A conjunct carrying no literal and no
    comparison is a fragment this matcher cannot evaluate, and a rule with one
    of those never FIRES -- it is MAYBE at best, and `unparsed` says why.
    """
    spec = contract()["match"]
    fields = set(spec["fields"])
    comparison = re.compile(spec["comparison_pattern"])
    conjuncts = _split_conjuncts(text)
    missing: list[str] = []
    unparsed: list[str] = []
    if not conjuncts:
        return {"verdict": MAYBE, "missing": [], "unparsed": [text.strip()],
                "because": "the rule line has no content to read"}

    for conjunct in conjuncts:
        predicates, literals = [], []
        for token in _tokens(conjunct):
            if token in fields:
                continue  # a field NAME, not a literal: `message` 包含 "X"
            m = comparison.match(token)
            if m:
                predicates.append((m.group("field"), m.group("value").strip()))
            else:
                literals.append(token)
        if not predicates and not literals:
            unparsed.append(conjunct)
            return {"verdict": MAYBE, "missing": missing, "unparsed": unparsed,
                    "because": f"no literal in {conjunct!r} can be evaluated"}

        for field, value in predicates:
            actual = {"status_code": status_code, "message": message}.get(field)
            if actual is None:
                if field not in missing:
                    missing.append(field)
                continue
            if field == "status_code":
                try:
                    ok = int(actual) == int(value)
                except (TypeError, ValueError):
                    ok = str(actual) == value
            else:
                ok = value in str(actual)
            if not ok:
                return {"verdict": NO, "missing": missing, "unparsed": unparsed,
                        "because": f"{field} does not equal {value!r}"}

        if literals:
            if message is None:
                if "message" not in missing:
                    missing.append("message")
                continue
            if not any(lit in message for lit in literals):
                return {"verdict": NO, "missing": missing, "unparsed": unparsed,
                        "because": "none of " + "、".join(repr(l) for l in literals)
                                   + " appears in the message"}

    if missing:
        return {"verdict": MAYBE, "missing": missing, "unparsed": unparsed,
                "because": "cannot be evaluated without " + ", ".join(missing)}
    if unparsed:
        return {"verdict": MAYBE, "missing": missing, "unparsed": unparsed,
                "because": "part of this rule is prose a matcher cannot evaluate"}
    return {"verdict": FIRES, "missing": [], "unparsed": [],
            "because": "every conjunct was satisfied"}


def _read_one_file(path: Path, root: Path) -> tuple[list[dict], str | None]:
    text = _read_text(path)
    kb = contract()["kb"]
    heading = re.compile(kb["entry"]["heading_pattern"], re.M)
    stamp_pattern = re.compile(kb["entry"]["file_stamp_pattern"], re.M)
    stamp = stamp_pattern.search(text)
    file_stamp = stamp.group("date") if stamp else None

    marks = list(heading.finditer(text))
    entries = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end]
        sections = _split_sections(body)

        meta_name, meta_text = _section(sections, "元数据")
        metadata = {k: v for k, v in _bullets(meta_text or "") if k} if meta_text else {}

        rule_name, rule_text = _section(sections, "字段匹配规则")
        rules, notes, redirects = [], [], []
        spec = contract()["match"]
        for label, value in _bullets(rule_text or ""):
            # The label is searched for anywhere in the line, not only in front
            # of a colon: `GLM/智谱辅助匹配` ends with the label and
            # `若命中 X，优先归入 [ID]` has no colon at all. See
            # `match.rule_labels_matched_by`.
            haystack = label if label else value
            shown = label if label else value
            if spec["primary_label"] in haystack:
                rules.append({"label": shown, "kind": "primary", "text": value})
            elif spec["auxiliary_label"] in haystack:
                rules.append({"label": shown, "kind": "auxiliary", "text": value})
            elif any(r in haystack for r in spec["redirect_labels"]):
                targets = re.findall(spec["reference_pattern"], value)
                redirects.append({"label": shown, "text": value, "targets": targets})
            else:
                notes.append({"label": shown, "text": value})

        sample_name, sample_text = _section(sections, kb["entry"]["sample_section_prefix"])
        related = []
        rel_name, rel_text = _section(sections, "关联信息")
        if rel_text:
            related = re.findall(contract()["match"]["reference_pattern"], rel_text)

        entries.append({
            "id": m.group("id"),
            "name": m.group("name").strip(),
            "file": _rel(path, root),
            "line": text[:m.start()].count("\n") + 1,
            "sections": list(sections),
            "missing_sections": [
                s for s in kb["entry"]["required_sections"]
                if _section(sections, s)[0] is None],
            "metadata": metadata,
            "updated": metadata.get("最后更新"),
            "confidence": metadata.get("置信度"),
            "rules": rules,
            "notes": notes,
            "redirects": redirects,
            "related_ids": sorted(set(related)),
            "sample_section": sample_name,
            "sample_text": sample_text or "",
        })
    return entries, file_stamp


def read_entries(kb_root: str | Path) -> dict:
    """Every formal entry, and everything the glob read and did not read."""
    root = Path(kb_root).expanduser()
    kb = contract()["kb"]
    result = {"kb_root": str(root), "entries": [], "files": [], "excluded": [],
              "entry_count": 0, "unreadable": None}
    if not root.exists():
        result["unreadable"] = f"{root} does not exist"
        return result
    exclusion = set(kb["exclusions"])
    for path in sorted(root.glob(kb["entry_glob"])):
        if not path.is_file():
            continue
        rel = _rel(path, root)
        if Path(rel).parts[0] in exclusion or rel in exclusion:
            result["excluded"].append(rel)
            continue
        if path.name == kb["index"]:
            result["excluded"].append(rel)
            continue
        try:
            entries, stamp = _read_one_file(path, root)
        except OSError as exc:
            result["excluded"].append(f"{rel} (unreadable: {exc})")
            continue
        result["files"].append({"path": rel, "entries": [e["id"] for e in entries],
                                "stamp": stamp})
        result["entries"].extend(entries)
    result["entry_count"] = len(result["entries"])
    if not result["files"]:
        # Nothing under the glob is not "a knowledge base with no entries" --
        # the glob itself may be aimed at the wrong root.
        result["unreadable"] = (
            f"no entry file matched {kb['entry_glob']!r} under {root}; "
            f"excluded: {result['excluded']}")
    return result


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _question(status_code, message) -> dict:
    return {"status_code": status_code, "message": message}


def _verdicts(entries: list[dict], question: dict) -> list[dict]:
    out = []
    for entry in entries:
        primary = [dict(_evaluate_rule(r["text"], **question), label=r["label"], text=r["text"])
                   for r in entry["rules"] if r["kind"] == "primary"]
        auxiliary = [dict(_evaluate_rule(r["text"], **question), label=r["label"], text=r["text"])
                     for r in entry["rules"] if r["kind"] == "auxiliary"]
        out.append({**entry, "primary": primary, "auxiliary": auxiliary,
                    "primary_verdict": _combine(primary),
                    "auxiliary_verdict": _combine(auxiliary)})
    return out


def _combine(rules: list[dict]) -> str:
    if not rules:
        return NO
    if any(r["verdict"] == FIRES for r in rules):
        return FIRES
    if any(r["verdict"] == MAYBE for r in rules):
        return MAYBE
    return NO


def _apply_redirects(candidates: list[dict], question: dict) -> tuple[list[dict], list[dict]]:
    """One pass. A redirect drops this entry only when its own condition holds
    AND its target also fired.

    The condition is evaluated on its own, separately from whether this entry's
    主匹配 fired -- a 排除 clause is a statement about which entry owns an error,
    and it can be right about that even when the entry's own primary rule is
    written in prose a matcher cannot read.

    Once, and not to a fixpoint: NET-001 and RESP-001 point at each other, each
    conditionally, and a second pass would be resolving that pair silently. A
    pair that drops both is reported as REDIRECTED_TO_NOTHING, which is a
    question about the knowledge base and not an answer.
    """
    firing = {e["id"] for e in candidates}
    kept, applied = [], []
    for entry in candidates:
        dropped_by = None
        for rule in entry["redirects"]:
            verdict = _evaluate_rule(rule["text"], **question)
            if verdict["verdict"] != FIRES:
                continue
            targets = [t for t in rule["targets"] if t in firing and t != entry["id"]]
            if targets:
                dropped_by = {**rule, "kept": targets}
                break
        if dropped_by is None:
            kept.append(entry)
        else:
            applied.append({"dropped": entry["id"], "label": dropped_by["label"],
                            "rule": dropped_by["text"], "kept": dropped_by["kept"]})
    return kept, applied


def match(kb_root: str | Path, *, status_code=None, message=None,
          code_root: str | Path | None = None) -> dict:
    """Which entry a question reaches, and by which of the KB's own rules."""
    if status_code is None and message is None:
        raise Refused(
            "a question needs a status code, a message, or both. Neither was "
            "given, and guessing either one would be an answer assembled from "
            "a fact nobody supplied")
    if status_code is not None:
        try:
            status_code = int(status_code)
        except (TypeError, ValueError):
            raise Refused(f"status_code is {status_code!r}, which is not an HTTP status code")

    spec = contract()
    boundary = _boundary(kb_root, code_root)
    result = {"outcome": None, "kb_root": str(Path(kb_root).expanduser()),
              "question": _question(status_code, message), "boundary": boundary,
              "matched_by": None, "chosen": None, "candidates": [], "redirects": [],
              "tie_break": None, "missing": [], "unreadable": None}
    if boundary["same_repository"]:
        result["outcome"] = "CODE_KB_SAME_REPO"
        return result

    reading = read_entries(kb_root)
    if reading["unreadable"]:
        result["outcome"] = "KB_UNREADABLE"
        result["unreadable"] = reading["unreadable"]
        return result

    question = _question(status_code, message)
    entries = _verdicts(reading["entries"], question)
    primary = [e for e in entries if e["primary_verdict"] == FIRES]
    kept, applied = _apply_redirects(primary, question)
    result["redirects"] = applied

    def names(rows):
        return [{"id": e["id"], "name": e["name"], "file": e["file"],
                 "because": [r["because"] for r in e["primary"]]} for e in rows]

    if primary and not kept:
        result["outcome"] = "REDIRECTED_TO_NOTHING"
        result["candidates"] = names(primary)
        result["matched_by"] = None
        return result
    if len(kept) > 1:
        # The KB states its own precedence rule -- 「正式条目优先解释错误链中最
        # 内层的真实根因」 -- and this applies it rather than inventing a
        # heuristic: the entry whose firing 主匹配 had to satisfy more
        # AND-conjuncts is naming more of the chain, which is what "inner root
        # cause" means here. A tie at the top is still a question about the
        # knowledge base, and it is reported as one.
        def specificity(entry):
            return max((len(_split_conjuncts(r["text"])) for r in entry["primary"]
                        if r["verdict"] == FIRES), default=0)

        ranked = sorted(kept, key=specificity, reverse=True)
        best = specificity(ranked[0])
        winners = [e for e in ranked if specificity(e) == best]
        if len(winners) > 1:
            result["outcome"] = "AMBIGUOUS_MATCH"
            result["candidates"] = names(winners)
            return result
        loser = ranked[1]
        result["tie_break"] = {
            "rule": spec["match"]["tie_break"]["rule"],
            "authority": spec["match"]["tie_break"]["authority"],
            "quote": spec["match"]["tie_break"]["quote"],
            "specificity": {ranked[0]["id"]: best, loser["id"]: specificity(loser)},
            "runner_up": {"id": loser["id"], "name": loser["name"],
                          "because": [r["because"] for r in loser["primary"]],
                          "primary_rules": [r["text"] for r in loser["primary"]]},
        }
        kept = [ranked[0]]
    if len(kept) == 1:
        result["outcome"] = "ANSWERED"
        result["matched_by"] = kept[0]["primary"][0]["label"]
        result["chosen"] = names(kept)[0]
        return result

    def leads(rows):
        """Candidates reached by 辅助匹配, and what each is still missing."""
        return [{"id": e["id"], "name": e["name"], "file": e["file"],
                 "led_by": [r["label"] for r in e["auxiliary"] if r["verdict"] == FIRES],
                 "primary_incomplete": [f for r in e["primary"] for f in r["missing"]]}
                for e in rows]

    def hits(entry):
        """辅助匹配 rules that FIRED. Not `len(entry["auxiliary"])`.

        The first version of this ranked on the number of auxiliary LINES the
        entry carries, which is a fact about how the document was formatted and
        not about the question. Measured on the real knowledge base:
        `--message '用户额度不足'` without a status code fires the auxiliary
        rules of PROV-001, PROV-002 and PROV-005, and the old ranking reported
        AMBIGUOUS_MATCH on whichever of them happened to have the most bullets.
        The question was not ambiguous. It was incomplete, and the one fact
        that would settle it -- the status code -- is a reply the person can
        send.
        """
        return sum(1 for r in entry["auxiliary"] if r["verdict"] == FIRES)

    auxiliary = [e for e in entries if e["auxiliary_verdict"] == FIRES]
    if auxiliary:
        best = max(hits(e) for e in auxiliary)
        top = [e for e in auxiliary if hits(e) == best]
        if len(top) > 1:
            # No 主匹配 fired, so nothing here IDENTIFIES an entry: the KB's own
            # design is that 辅助匹配 corroborates and does not identify. Among
            # several leads there is no entry-property to rank on, so the only
            # honest choice is between "a reply would narrow this" and "nothing
            # you could say would".
            missing: list[str] = []
            for entry in top:
                for rule in entry["primary"]:
                    for field in rule["missing"]:
                        if field not in missing:
                            missing.append(field)
            result["candidates"] = leads(top)
            if missing:
                result["outcome"] = "NEEDS_MORE_INPUT"
                result["matched_by"] = top[0]["auxiliary"][0]["label"]
                result["missing"] = missing
            else:
                result["outcome"] = "AMBIGUOUS_MATCH"
            return result
        result["outcome"] = "ANSWERED_FROM_AUXILIARY"
        result["matched_by"] = top[0]["auxiliary"][0]["label"]
        result["chosen"] = {**names(top)[0],
                            "led_by": [r["label"] for r in top[0]["auxiliary"]
                                       if r["verdict"] == FIRES]}
        result["corroborated_by_primary"] = False
        return result

    # An entry whose primary rule is MAYBE because a field was not supplied can
    # be moved to a verdict by naming that field. An entry whose primary rule is
    # MAYBE because part of it is prose cannot be moved at all -- no reply the
    # person could send would help -- so it is not a `NEEDS_MORE_INPUT`
    # candidate. It is a `RULE_CONTAINS_PROSE` finding, which `check()` reports.
    incomplete = [e for e in entries
                  if e["primary_verdict"] == MAYBE
                  and any(r["missing"] for r in e["primary"])]
    if incomplete:
        missing: list[str] = []
        for entry in incomplete:
            for rule in entry["primary"]:
                for field in rule["missing"]:
                    if field not in missing:
                        missing.append(field)
        result["outcome"] = "NEEDS_MORE_INPUT"
        result["missing"] = missing
        result["candidates"] = [
            {"id": e["id"], "name": e["name"], "file": e["file"],
             "because": [r["because"] for r in e["primary"]]} for e in incomplete]
        return result

    result["outcome"] = "NO_ENTRY_MATCHES"
    return result


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
def redact(text: str) -> tuple[str, list[dict]]:
    """What an answer must not carry into a chat other people can read."""
    if text is None:
        return None, []
    spec = contract()["answer"]["redaction"]
    hits = []
    out = text
    for name, pattern in spec["patterns"].items():
        found = re.findall(pattern, out)
        if found:
            hits.append({"class": name, "count": len(found)})
            out = re.sub(pattern, spec["replaces_with"], out)
    return out, hits


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------
def _samples_of(entry: dict) -> str:
    return entry.get("sample_text") or ""


def _normal_form(text: str) -> str:
    """One error line, with everything that is not the error taken out.

    Redact first, then collapse every `<...>` span. The knowledge base writes
    its samples with placeholders -- `User=<USER>`, `Spend=<实际花费>` -- and a
    real question carries the real values. Comparing the two raw would call
    every question a variant of the entry it exactly matches, which is a
    finding that fires always and is therefore not a finding. The redaction
    runs first so that the value being collapsed has already been replaced;
    `<已脱敏>` then collapses to the same `<*>` the KB's `<USER>` does.

    This is also why the candidate's own lines are copied from the NORMALISED
    text: the first version of this function compared raw lines and printed
    them, so the improvement candidate -- the one field in the whole answer
    that is not assembled from the entry -- carried a raw user identifier into
    a document somebody would paste into a chat. Redaction is a property of
    everything this module emits, not of the sections it assembles.
    """
    cleaned, _ = redact(text)
    return re.sub(r"<[^>]*>", "<*>", cleaned).strip()


_DIGITS = re.compile(r"\d+")


def _shape_of(line: str) -> str:
    """The stable part of an error line -- the comparison key for recurrence.

    Two occurrences of ONE error differ in their volatile values and agree in
    their words. Measured against the real knowledge base: PLAT-002's sample
    line is `Spend=20.09863283999999`, and a second occurrence carries some
    other amount, so a comparison on the literal text calls every recurrence a
    new variant -- which is the same "a finding that always fires" defect as the
    set-membership bug this replaced, one level deeper. Digits are the volatile
    part, so they collapse.

    What this gives up, named rather than discovered later: a line that differs
    only in a numeric `code=` value reads as seen. That is a variant the KB will
    not be told about, and it is the direction this module prefers to be wrong
    in -- a matcher that reports a gap rather than one that reports a phantom.
    The redacted form a person reads is `_normal_form`, not this.
    """
    return _DIGITS.sub("<*>", _normal_form(line))


def _variant_unseen(entry: dict, message: str | None) -> dict | None:
    """Whether this question's own error text is absent from the entry's samples.

    The KB's promotion rule counts recurrences -- 「出现 ≥ 2 次的错误，写成正式
    条目草稿」 -- so a second sighting of a known error is exactly the evidence
    that rule is waiting for. Long lines only: a short line like `429` appears
    in everything and would call every question a new variant.

    "Seen" is containment either way, not set membership. Asked with
    `--status-code 402 --message 'ExceededBudget'`, set membership reported the
    word `ExceededBudget` as unseen against a sample whose first two words are
    `ExceededBudget: User=<USER>` -- the sample is BUILT on that token. A
    finding that fires on the thing it exists to recognise is not a finding,
    which is the fourth time this module has been wrong in that direction.
    Containment errs toward silence: a question line that a sample line
    contains, or that contains a sample line, has been seen.
    """
    if not message:
        return None
    sample_lines = [ln for ln in (_shape_of(s) for s in _samples_of(entry).splitlines())
                    if ln]
    candidates = [ln.strip() for ln in message.splitlines() if len(ln.strip()) >= 12]

    def seen(line: str) -> bool:
        shape = _shape_of(line)
        return any(shape in s or s in shape for s in sample_lines)

    unseen = [ln for ln in candidates if not seen(ln)]
    if not unseen:
        return None
    return {"kind": "ANSWERED_BUT_VARIANT_UNSEEN",
            "entry": entry["id"], "kb_file": entry["file"],
            "section": entry["sample_section"],
            "why": (f"{len(unseen)} line(s) of this question appear nowhere in "
                    f"{entry['id']}'s sample section, once placeholders and "
                    f"identifiers are taken out. If this is the second time this "
                    f"exact error has been seen, the KB's own rule "
                    f"(「出现 ≥ 2 次的错误，写成正式条目草稿」) has the "
                    f"evidence it was waiting for."),
            "unseen_lines": [_normal_form(ln)[:120] for ln in unseen[:3]]}


def _prefix_for(status_code, entries: list[dict]) -> tuple[str | None, str]:
    if status_code is None:
        return None, ("no status code was given, so no 环节 can be inferred from "
                      "the index -- the six prefixes are 环节 and not error classes")
    tally: dict[str, int] = {}
    for entry in entries:
        for rule in entry["rules"]:
            if rule["kind"] != "primary":
                continue
            if re.search(rf"status_code\s*==\s*{status_code}\b", rule["text"]):
                prefix = entry["id"].split("-")[0]
                tally[prefix] = tally.get(prefix, 0) + 1
    if not tally:
        return None, (f"no entry's 主匹配 rule names status_code == {status_code}, "
                      f"so no 环节 can be inferred")
    best = max(tally.values())
    winners = sorted(k for k, v in tally.items() if v == best)
    if len(winners) > 1:
        return None, (f"status_code == {status_code} appears under more than one 环节 "
                      f"({', '.join(winners)}), so the choice is not mine to make")
    return winners[0], f"status_code == {status_code} appears in the {winners[0]} entries"


def next_id(kb_root: str | Path, prefix: str) -> dict:
    """The next free ID under a prefix. OFFERED, never allocated.

    「ID 全局唯一，分配后不再变更」 makes an ID a commitment. A parser that
    auto-allocated one would be making that commitment on somebody's behalf and
    burning the number if the answer turned out to be wrong.
    """
    reading = read_entries(kb_root)
    if reading["unreadable"]:
        return {"prefix": prefix, "offered": None, "unreadable": reading["unreadable"]}
    known = contract()["kb"]["id_prefixes"]
    if prefix not in known:
        raise Refused(f"prefix {prefix!r} is not one of {sorted(known)}; "
                      f"the six prefixes are the KB's 环节, not free-form tags")
    used = sorted(int(e["id"].split("-")[1]) for e in reading["entries"]
                  if e["id"].startswith(prefix + "-"))
    nxt = (used[-1] + 1) if used else 1
    directory = None
    for entry in reading["entries"]:
        if entry["id"].startswith(prefix + "-"):
            directory = str(Path(entry["file"]).parent)
            break
    return {"prefix": prefix, "offered": f"{prefix}-{nxt:03d}", "used": used,
            "directory": directory, "unreadable": None}


def answer(kb_root: str | Path, *, status_code=None, message=None,
           code_root: str | Path | None = None) -> dict:
    """A reply a person can send, and the improvement it implies."""
    result = match(kb_root, status_code=status_code, message=message, code_root=code_root)
    result["answer"] = None
    result["improvement"] = None
    if result["outcome"] in ("CODE_KB_SAME_REPO", "KB_UNREADABLE",
                             "AMBIGUOUS_MATCH", "NEEDS_MORE_INPUT",
                             "REDIRECTED_TO_NOTHING"):
        return result

    reading = read_entries(kb_root)
    for entry in reading["entries"]:
        entry["_kb_root"] = str(Path(kb_root).expanduser())
    spec = contract()["answer"]
    by_id = {e["id"]: e for e in reading["entries"]}

    if result["outcome"] == "NO_ENTRY_MATCHES":
        prefix, why = _prefix_for(status_code, reading["entries"])
        offered = next_id(kb_root, prefix) if prefix else {"offered": None}
        result["improvement"] = {
            "kind": "NO_ENTRY_MATCHES",
            # Redacted on the way in, not on the way out: this field is the
            # question's own text, and the candidate is a record somebody may
            # file. A candidate carrying the identifier the answer carefully
            # removed would be the same leak in a file that outlives the chat.
            "question_shape": {"status_code": status_code,
                               "message": redact((message or "")[:200])[0] or None},
            "id_prefix": prefix, "offered_id": offered.get("offered"),
            "kb_file": (f"{offered.get('directory')}/" if offered.get("directory") else None),
            "why": (f"no 主匹配 or 辅助匹配 rule in {reading['entry_count']} entries "
                    f"reached this question. {why}."),
        }
        return result

    chosen = by_id[result["chosen"]["id"]]
    sections = _entry_sections(chosen)
    used = {}
    for want in spec["sections_used"]:
        name, text = _section(sections, want)
        if name:
            used[name] = text

    provenance = [{"entry": chosen["id"], "name": chosen["name"], "file": chosen["file"],
                   "updated": chosen["updated"], "matched_by": result["matched_by"]}]
    related = []
    for rid in chosen["related_ids"]:
        linked = by_id.get(rid)
        related.append({"id": rid, "name": linked["name"] if linked else None,
                        "in_kb": linked is not None})

    redactions = []
    body = {}
    for name, text in used.items():
        clean, hits = redact(text)
        body[name] = clean
        for hit in hits:
            redactions.append({**hit, "section": name})

    result["answer"] = {
        "text": body,
        "provenance": provenance,
        "confidence": {
            "level": _level_of(chosen["confidence"]),
            "as_written": chosen["confidence"],
            # `confidence.hedge`, not `answer.hedge`: the contract keeps the
            # marker beside the levels it marks, and reading it out of the
            # answer section returned None for every 推测 entry while looking
            # like it had answered.
            "hedge": contract()["confidence"]["hedge"].get(
                _level_of(chosen["confidence"])),
        },
        "related": related,
        "redactions": redactions,
        "from": "formal entries only",
    }
    result["improvement"] = _variant_unseen(chosen, message)
    return result


def _entry_sections(entry: dict) -> dict:
    """Re-read one entry's sections from its file.

    The `reading` pass keeps the section NAMES for the required-section check
    and the sample text for the variant check, but not every section's body:
    carrying all 21 entries' full text through a listing would make every
    `entries` call read like a dump. This re-reads the one entry being answered.
    """
    root = Path(entry["_kb_root"]) if "_kb_root" in entry else None
    if root is None:
        return {}
    path = root / entry["file"]
    text = _read_text(path)
    heading = re.compile(contract()["kb"]["entry"]["heading_pattern"], re.M)
    marks = list(heading.finditer(text))
    for i, m in enumerate(marks):
        if m.group("id") != entry["id"]:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        return _split_sections(text[m.end():end])
    return {}


def _level_of(confidence: str | None) -> str | None:
    """The level inside `已确认（来自 529 条平台实际报错）`.

    The count is the most useful thing in the field for a reader deciding
    whether to believe the entry, and it exists ONLY in the entry -- the index
    column drops it. So the level decides the hedging and the whole value
    travels on as `as_written`.
    """
    if not confidence:
        return None
    for level in contract()["confidence"]["levels"]:
        if confidence.startswith(level):
            return level
    return None


# ---------------------------------------------------------------------------
# The check: index against entries
# ---------------------------------------------------------------------------
def check(kb_root: str | Path, *, as_of: str | None = None,
          code_root: str | Path | None = None) -> dict:
    """Whether the index and the entries still say the same thing.

    The two are not symmetric. The entry is the authority and the index is a
    projection of it, so every finding is phrased in that direction. And an
    unreadable knowledge base produces NO findings -- it produces
    `KB_UNREADABLE`, because `findings: []` is what "checked and clean" and
    "never checked" both render as.
    """
    from datetime import date, timedelta  # local: the only clock use in this module

    spec = contract()
    boundary = _boundary(kb_root, code_root)
    result = {"kb_root": str(Path(kb_root).expanduser()), "as_of": as_of,
              "boundary": boundary, "entry_count": None, "index_row_count": None,
              "findings": [], "counts": {}, "unreadable": None, "excluded": []}
    if boundary["same_repository"]:
        result["unreadable"] = "the knowledge base and the code root are one repository"
        result["outcome"] = "CODE_KB_SAME_REPO"
        return result

    reading = read_entries(kb_root)
    index = read_index(kb_root)
    result["excluded"] = reading["excluded"]
    if reading["unreadable"] and index["unreadable"]:
        result["unreadable"] = f"{reading['unreadable']}; and {index['unreadable']}"
        result["outcome"] = "KB_UNREADABLE"
        return result

    def add(code, subject, detail):
        result["findings"].append({"code": code, "subject": subject, "detail": detail})

    entries = reading["entries"]
    for entry in entries:
        entry["_kb_root"] = str(Path(kb_root).expanduser())
    by_id = {e["id"]: e for e in entries}
    rows = {r["错误ID"]: r for r in index["rows"]}
    result["entry_count"] = len(entries)
    result["index_row_count"] = index["row_count"]

    for eid in sorted(set(by_id) - set(rows)):
        add("INDEX_MISSING", eid, f"{by_id[eid]['file']} carries {eid}; the index has no row")
    for eid in sorted(set(rows) - set(by_id)):
        add("INDEX_ORPHAN", eid, f"the index lists {eid}; no entry file carries it")
    if index["row_count"] != len(entries) and not index["unreadable"]:
        add("INDEX_COUNT_DISAGREES", spec["kb"]["index"],
            f"{len(entries)} entries on disk, {index['row_count']} rows in the index")

    root = Path(kb_root).expanduser()
    for eid, row in sorted(rows.items()):
        named = row.get("所在文件", "")
        if not named:
            continue
        if not (root / named).exists():
            add("FILE_MISSING", eid, f"the index points at {named}, which is not in {root}")
            continue
        if eid not in by_id:
            continue
        entry = by_id[eid]
        if entry["file"] != named:
            add("ID_NOT_IN_NAMED_FILE", eid,
                f"the index points at {named}; the entry is in {entry['file']}")
        if row.get("名称") and row["名称"] != entry["name"]:
            add("NAME_DRIFT", eid,
                f"index name {row['名称']!r} is not the entry's heading {entry['name']!r}")
        level = _level_of(entry["confidence"])
        if level is not None and row.get("置信度") and row["置信度"] != level:
            add("CONFIDENCE_LEVEL_DISAGREES", eid,
                f"index says {row['置信度']!r}; the entry's level is {level!r}")
        if row.get("状态码"):
            codes = set(re.findall(r"status_code\s*==\s*(\d{3})",
                                   " ".join(r["text"] for r in entry["rules"])))
            if codes and row["状态码"] not in codes:
                add("STATUS_CODE_DISAGREES", eid,
                    f"index says {row['状态码']!r}; the entry's 主匹配 names {sorted(codes)}")

    variants: dict[str, int] = {}
    for entry in entries:
        if entry["sample_section"]:
            variants[entry["sample_section"]] = variants.get(entry["sample_section"], 0) + 1
    if len(variants) > 1:
        add("SAMPLE_SECTION_VARIANTS", "knowledge base",
            "the sample section is spelled "
            + " / ".join(f"{n} x {re or 'unnamed'}" for re, n in sorted(variants.items()))
            + " -- a parser keyed on one spelling reads a subset and reports success")

    for entry in entries:
        # Whether the level is one the contract declares is a fact about the
        # ENTRY and has nothing to do with the index. It was first written
        # inside the loop above, which iterates the index's rows -- so an entry
        # with no row at all was never checked for it, which is exactly the
        # entry a reader most needs to be told about.
        level = _level_of(entry["confidence"])
        if entry["confidence"] and level is None:
            add("CONFIDENCE_LEVEL_UNKNOWN", entry["id"],
                f"置信度 {entry['confidence']!r} starts with none of "
                f"{spec['confidence']['levels']}")
        for missing in entry["missing_sections"]:
            add("SECTION_MISSING", entry["id"], f"no section starting with {missing!r}")
        for rule in entry["rules"]:
            if rule["kind"] != "primary":
                continue
            verdict = _evaluate_rule(rule["text"], status_code=None, message=None)
            if verdict["unparsed"]:
                add("RULE_CONTAINS_PROSE", entry["id"],
                    f"主匹配 {rule['text']!r} carries {verdict['unparsed']} -- "
                    f"as written this rule can never fire here")
        for rule in entry["redirects"]:
            verdict = _evaluate_rule(rule["text"], status_code=None, message=None)
            if verdict["unparsed"]:
                add("RULE_CONTAINS_PROSE", entry["id"],
                    f"消歧规则 {rule['label']} {rule['text']!r} carries "
                    f"{verdict['unparsed']} -- this disambiguation never happens")
            for target in rule["targets"]:
                if target not in by_id:
                    add("REDIRECT_TARGET_MISSING", entry["id"],
                        f"{rule['label']} names [{target}], which no entry carries")

    for f in reading["files"]:
        if not f["stamp"]:
            continue
        for eid in f["entries"]:
            entry = by_id.get(eid)
            if entry and entry["updated"] and entry["updated"] > f["stamp"]:
                add("FILE_STAMP_OLDER_THAN_ENTRY", eid,
                    f"{f['path']} is stamped {f['stamp']}; {eid} says {entry['updated']}")

    if as_of:
        try:
            horizon = (date.fromisoformat(as_of)
                       - timedelta(days=spec["checks"]["staleness_days"])).isoformat()
        except ValueError:
            raise Refused(f"as_of is {as_of!r}, which is not an ISO date")
        for entry in entries:
            if entry["updated"] and entry["updated"] < horizon:
                add("STALE", entry["id"],
                    f"最后更新 {entry['updated']} predates {horizon} "
                    f"({spec['checks']['staleness_days']} days before {as_of})")

    counts: dict[str, int] = {}
    for finding in result["findings"]:
        counts[finding["code"]] = counts.get(finding["code"], 0) + 1
    result["counts"] = counts
    result["outcome"] = "CHECKED"
    return result


# ---------------------------------------------------------------------------
# Outcomes and exits
# ---------------------------------------------------------------------------
def exit_code_for(outcome: str) -> int:
    codes = contract()["exit_codes"]
    if outcome not in codes:
        raise Refused(f"{outcome!r} is not an outcome this contract declares; "
                      f"the declared set is {sorted(codes)}")
    return codes[outcome]


def listing(kb_root: str | Path) -> dict:
    reading = read_entries(kb_root)
    return {"kb_root": reading["kb_root"], "entry_count": reading["entry_count"],
            "files": reading["files"], "excluded": reading["excluded"],
            "entries": [{"id": e["id"], "name": e["name"], "file": e["file"],
                         "updated": e["updated"], "confidence": e["confidence"],
                         "primary_rules": [r["text"] for r in e["rules"]
                                           if r["kind"] == "primary"],
                         "related_ids": e["related_ids"]} for e in reading["entries"]],
            "unreadable": reading["unreadable"]}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="support",
        description="A support question, and where the answer is allowed to come from.")
    parser.add_argument("--kb-root", required=True,
                        help="the knowledge base checkout. Required and never "
                             "defaulted: it belongs to another author and moves "
                             "on its own schedule.")
    parser.add_argument("--code-root", default=None,
                        help="the support CODE checkout, passed only so the two "
                             "can be compared. Nothing is read from it; if it "
                             "and --kb-root are one repository the call is refused.")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("entries", help="every formal entry the glob reads")
    m = sub.add_parser("match", help="which entry a question reaches")
    m.add_argument("--status-code", type=int, default=None)
    m.add_argument("--message", default=None)
    a = sub.add_parser("answer", help="a reply, and the improvement it implies")
    a.add_argument("--status-code", type=int, default=None)
    a.add_argument("--message", default=None)
    c = sub.add_parser("check", help="the index against the entries")
    c.add_argument("--as-of", default=None,
                   help="the date to judge staleness against. Required for the "
                        "STALE check and never read from the clock: a check whose "
                        "answer depends on when it ran cannot be compared against "
                        "a recorded one.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.verb == "entries":
            payload = listing(args.kb_root)
            outcome = "KB_UNREADABLE" if payload["unreadable"] else "ANSWERED"
        elif args.verb == "match":
            payload = match(args.kb_root, status_code=args.status_code,
                            message=args.message, code_root=args.code_root)
            outcome = payload["outcome"]
        elif args.verb == "answer":
            payload = answer(args.kb_root, status_code=args.status_code,
                             message=args.message, code_root=args.code_root)
            outcome = payload["outcome"]
        else:
            payload = check(args.kb_root, as_of=args.as_of, code_root=args.code_root)
            outcome = payload.get("outcome", "CHECKED")
            if outcome == "CHECKED":
                # A check with findings is not a failure of the check. It is the
                # check working, and the exit says only whether it could run.
                outcome = "KB_UNREADABLE" if payload["unreadable"] else "ANSWERED"
    except Refused as exc:
        print(f"support: refused: {exc}", file=sys.stderr)
        return EXIT_FAULT

    if args.json:
        _print_json(payload)
    else:
        print(render(args.verb, payload))
    try:
        return exit_code_for(outcome)
    except Refused:
        return EXIT_FAULT


def render(verb: str, payload: dict) -> str:
    if verb == "entries":
        lines = [f"{payload['entry_count']} entries under {payload['kb_root']}"]
        for entry in payload["entries"]:
            lines.append(f"  {entry['id']}  {entry['name']}")
            lines.append(f"      {entry['file']}  最后更新 {entry['updated']}  "
                         f"置信度 {entry['confidence']}")
            for rule in entry["primary_rules"]:
                lines.append(f"      主匹配: {rule}")
        if payload["unreadable"]:
            lines.append(f"  UNREADABLE: {payload['unreadable']}")
        return "\n".join(lines)

    if verb == "check":
        lines = [f"check {payload['entry_count']} entries against "
                 f"{payload['index_row_count']} index rows, as of {payload['as_of']}"]
        if payload["unreadable"]:
            return lines[0] + f"\n  COULD NOT CHECK: {payload['unreadable']}"
        if not payload["findings"]:
            lines.append("  no findings")
        for code, n in sorted(payload["counts"].items()):
            lines.append(f"  {n:3d}  {code}")
        for finding in payload["findings"]:
            lines.append(f"  {finding['code']}  {finding['subject']}: {finding['detail']}")
        return "\n".join(lines)

    lines = [f"outcome: {payload['outcome']}"]
    if payload.get("unreadable"):
        lines.append(f"  UNREADABLE: {payload['unreadable']}")
    if payload.get("matched_by"):
        lines.append(f"  matched by: {payload['matched_by']}")
    if payload.get("missing"):
        lines.append(f"  missing: {', '.join(payload['missing'])}")
    for candidate in payload.get("candidates") or []:
        lines.append(f"  candidate {candidate['id']}  {candidate['name']}")
    for redirect in payload.get("redirects") or []:
        lines.append(f"  {redirect['dropped']} dropped by {redirect['label']} "
                     f"({redirect['rule']}) -> {redirect['kept']}")
    tie = payload.get("tie_break")
    if tie:
        runner = tie["runner_up"]
        lines.append(f"  two 主匹配 rules fired; resolved by {tie['rule']} "
                     f"({tie['authority']}: 「{tie['quote']}」)")
        lines.append(f"  specificity {tie['specificity']}")
        lines.append(f"  runner-up {runner['id']} {runner['name']}")
        for rule in runner["primary_rules"]:
            lines.append(f"    declined 主匹配: {rule}")
    reply = payload.get("answer")
    if reply:
        lines.append("")
        lines.append(f"confidence: {reply['confidence']['level']} "
                     f"({reply['confidence']['as_written']})")
        if reply["confidence"]["hedge"]:
            lines.append(f"hedge: {reply['confidence']['hedge']}")
        for name, text in reply["text"].items():
            lines.append("")
            lines.append(f"### {name}")
            lines.append(text)
        lines.append("")
        for prov in reply["provenance"]:
            lines.append(f"provenance: {prov['entry']} in {prov['file']} "
                         f"(最后更新 {prov['updated']}, matched by {prov['matched_by']})")
        for rel in reply["related"]:
            lines.append(f"related: {rel['id']} {rel['name']}")
        if reply["redactions"]:
            lines.append("redacted: " + ", ".join(
                f"{r['section']}:{r['class']} x{r['count']}" for r in reply["redactions"]))
    improvement = payload.get("improvement")
    if improvement:
        lines.append("")
        lines.append(f"knowledge improvement candidate: {improvement['kind']}")
        for key in ("entry", "kb_file", "section", "id_prefix", "offered_id", "why"):
            if improvement.get(key):
                lines.append(f"  {key}: {improvement[key]}")
        for line in improvement.get("unseen_lines") or []:
            lines.append(f"  unseen: {line}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
