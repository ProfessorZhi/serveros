"""A section the ledger cites must have a heading, or the citation is a dangle.

WHY THIS EXISTS, AND IT IS NOT HYPOTHETICAL. While inserting an entry into
`docs/CC-EXECUTION-LEDGER.md`, an edit used the line

    ## 12. 最终报告（计划 §16 要求的那一份）

as its anchor and did not re-emit it. The heading was deleted, the section's
body stayed, and the change was merged. Nothing objected, because a heading is
not a value any test reads — it is a REFERENCE, and the ledger cites `§12` in
its own text at least eight times, including the sentence that tells a reader
where the final report is:

    兑现它的那一节是 §12（终报）

So the document went on pointing at a section that no longer had a name, and
`grep '^## 12\\.'` stopped finding it. This is the same shape as §9.58 one
document over: a name that can no longer be asked for. The fix is the heading;
this file is so that the next deletion of one is a failure rather than a
discovery.

WHAT IS CHECKED IS THE LEDGER'S OWN TOP-LEVEL SECTIONS, `0` THROUGH `12`, AND
NOTHING ELSE. The ledger also cites `§16` and `§9.55`-style entries, which are
the PLAN's sections and this file's own subsections — demanding headings for
those would assert something false. The set below is the numbered top-level
sequence this document defines for itself, and `§12.5` does not create a
requirement for `## 12.5.` because it is a citation of a subsection, not a
heading grammar.
"""
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "CC-EXECUTION-LEDGER.md"

#: The top-level sections this document defines for itself.
SECTIONS = tuple(range(0, 13))

#: `## 12. Title` is a top-level heading; `## 12.5 Title` is not.
#: The lookahead is the whole discrimination, and without it `## 12.5` would
#: satisfy a requirement for section 12 — the control test below caught exactly
#: that when this pattern was first written as `^## (\d+)\.`.
HEADING = re.compile(r"^## (?P<num>\d+)\.(?=\s|$)")


def unfenced(text: str) -> list[str]:
    """The document's lines with every fenced code block removed.

    THE QUOTATION IS THE WHOLE PROBLEM, AND THIS FILE'S OWN CASE PROVES IT. The
    entry describing the deleted heading QUOTES that heading inside a ```text
    fence — so a scanner that reads raw lines finds `## 12.` and is satisfied by
    the quotation of a heading that may not exist. Measured: the first version of
    this gate passed for exactly that reason, reporting the quoted copy and never
    reaching the real heading 38 lines below. A scanner that cannot tell a
    quotation from the thing quoted cannot report on either, which is the lesson
    `test_no_node_paths_in_product_data.py` learned about values and sentences and
    this file learns about headings and their citations.
    """
    lines, out, fenced = text.splitlines(), [], False
    for line in lines:
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return out


def headings(text: str) -> dict[int, str]:
    """`number -> the line`, for every `## <n>.` heading outside a code fence."""
    found = {}
    for line in unfenced(text):
        match = HEADING.match(line)
        if match:
            found.setdefault(int(match.group("num")), line.strip())
    return found


class TheControlTests(unittest.TestCase):
    def test_it_finds_a_heading(self):
        self.assertIn(12, headings("## 12. The final report\nbody\n"))

    def test_it_is_not_fooled_by_a_citation_of_the_same_number(self):
        """`§12` in prose is not a heading, and this is the discrimination."""
        self.assertEqual(headings("see §12 for the report\n## 11. prior\n"),
                         {11: "## 11. prior"})

    def test_it_is_not_fooled_by_a_subsection_citation(self):
        self.assertEqual(headings("## 12.5 the day cycle\n"), {})

    def test_it_is_not_fooled_by_a_QUOTED_heading_in_a_code_fence(self):
        """The discrimination this gate originally lacked, and its own subject.

        A package that deletes a heading will describe the deletion, and the
        description quotes the heading. If the quote counts, the gate passes on
        the quote and the document stays broken.
        """
        text = ('## 11. prior\n'
                '\n'
                'The anchor was:\n'
                '\n'
                '```text\n'
                '## 12. The final report\n'
                '```\n')
        self.assertEqual(headings(text), {11: "## 11. prior"})


class TheLedgerSectionsTests(unittest.TestCase):
    def test_the_ledger_is_there_to_check(self):
        """A missing file and a file with no findings are the same output."""
        self.assertTrue(LEDGER.exists(), f"{LEDGER} is absent")
        self.assertGreater(LEDGER.stat().st_size, 100_000, "too small to be the ledger")

    def test_every_top_level_section_has_a_heading(self):
        present = headings(LEDGER.read_text(encoding="utf-8"))
        missing = [n for n in SECTIONS if n not in present]
        self.assertEqual(
            missing, [],
            "these numbered sections are cited by this document's own text and "
            "have no heading: a citation that resolves to nothing reads exactly "
            "like a section nobody wrote")

    def test_the_section_the_ledger_sends_readers_to_is_one_of_them(self):
        """`§12` is cited more than any other; it is the one that was deleted."""
        text = LEDGER.read_text(encoding="utf-8")
        self.assertIn("§12", text, "the citation this check exists for is gone")
        self.assertIn(12, headings(text))


if __name__ == "__main__":
    unittest.main()
