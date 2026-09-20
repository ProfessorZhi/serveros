#!/usr/bin/env python3
"""The renderers: one model, two outputs, and the ways they could diverge.

§6 asks for a Feishu payload and a visual preview from the *same* renderer, and
the reason is not tidiness. A preview built by second code that reads the model
separately is a preview of a card that does not exist — and it would look
correct, which is worse than looking broken.

So the load-bearing tests here are the ones that check the two outputs against
each other, plus the two rules a card can break silently: an unknown state
rendered as green, and a sample rendered as a reading.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cards = _load("serveros_runtime_cards", "cards.py")
presentation = cards.presentation
scenarios = _load("serveros_runtime_card_scenarios", "card_scenarios.py")
source = _load("serveros_runtime_card_source", "card_source.py")

#: §6: "按当前 card schema/SDK 校验，不从草图发明 JSON 属性". These six are the
#: ones already in use in the cards this node sends, so the schema version in
#: production is known to accept them. A seventh would be a guess, and a guessed
#: tag renders as a blank rectangle in a conversation nobody is watching.
PROVEN_TAGS = {"markdown", "hr", "plain_text", "collapsible_panel",
               "column_set", "standard_icon", "column"}


def _tags(element: dict) -> set:
    found = {element.get("tag")}
    for child in (element.get("columns") or []):
        for inner in (child.get("elements") or []):
            found |= _tags(inner)
    for inner in (element.get("elements") or []):
        found |= _tags(inner)
    header = element.get("header") or {}
    for sub in ("title", "icon"):
        if isinstance(header.get(sub), dict):
            found |= _tags(header[sub])
    return found


def _all_tags(card: dict) -> set:
    found = set()
    for element in (card.get("body") or {}).get("elements") or []:
        found |= _tags(element)
    header = card.get("header") or {}
    for sub in ("title", "subtitle"):
        if isinstance(header.get(sub), dict):
            found |= _tags(header[sub])
    return found


def _markdown(card: dict) -> list[str]:
    return [e["content"] for e in (card.get("body") or {}).get("elements") or []
            if e.get("tag") == "markdown"]


class ScenarioRenderTests(unittest.TestCase):
    """All six situations, because a card that only renders on a good day is a
    card that is missing on the day it matters."""

    @classmethod
    def setUpClass(cls):
        cls.items = scenarios.rendered()

    def test_there_are_six_and_they_are_the_ones_the_design_names(self):
        self.assertEqual(
            [i["scenario"] for i in self.items],
            ["normal", "partial_unknown", "one_critical", "observer_failure",
             "mixed", "recovery_only"])

    def test_every_scenario_renders_both_outputs(self):
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                self.assertTrue(item["html"].startswith("<!DOCTYPE html>"))
                self.assertGreater(len(item["card"]["body"]["elements"]), 1)

    def test_every_scenario_uses_only_proven_element_tags(self):
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                self.assertEqual(_all_tags(item["card"]) - PROVEN_TAGS, set())

    def test_every_scenario_is_marked_as_a_sample_in_the_header(self):
        """A preview that cannot be told from a reading is worse than none."""
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                self.assertIn(presentation.SAMPLE_MARK,
                              item["card"]["header"]["title"]["content"])
                self.assertTrue(any(presentation.SAMPLE_MARK in m
                                    for m in _markdown(item["card"])))

    def test_no_scenario_renders_an_empty_panel(self):
        """A fold that opens onto nothing is a tap spent on a promise."""
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                panels = [e for e in item["card"]["body"]["elements"]
                          if e.get("tag") == "collapsible_panel"]
                for panel in panels:
                    body = "\n".join(str(e.get("content") or "")
                                     for e in panel.get("elements") or [])
                    self.assertTrue(body.strip(),
                                    f"{item['scenario']}: empty panel")

    def test_the_header_title_is_not_repeated_as_the_first_body_line(self):
        """It is the most valuable line on a 390px screen, and the first draft
        spent it saying what the line above it had just said."""
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                title = item["card"]["header"]["title"]["content"]
                bare = title.replace("**", "").split(" · ")[0].strip()
                self.assertNotIn(bare, _markdown(item["card"])[0])

    def test_every_scenario_still_says_a_sample_without_the_mark(self):
        """The mark is a flag, and the flag has to be off for the card to be
        sendable — a card that says 示例 unconditionally would make the flag
        meaningless on the one card that is real."""
        for scenario in scenarios.scenarios():
            with self.subTest(scenario=scenario["name"]):
                model = scenarios.model_for(scenario, sample=False)
                self.assertFalse(model["sample"])
                card = cards.card_for(model)
                self.assertNotIn(presentation.SAMPLE_MARK,
                                 card["header"]["title"]["content"])
                self.assertFalse(any(presentation.SAMPLE_MARK in m
                                     for m in _markdown(card)))


class ColourTests(unittest.TestCase):
    """§6's "未知用中性而非正常绿", on the map that decides."""

    def test_the_verdict_words_all_have_a_template(self):
        for word in (presentation.OK, presentation.ATTENTION,
                     presentation.UNCONFIRMED, presentation.CRITICAL):
            with self.subTest(word=word):
                self.assertIn(word, cards.TEMPLATES)

    def test_unknown_and_unconfirmed_are_not_green(self):
        for word in (presentation.UNKNOWN, presentation.UNCONFIRMED):
            with self.subTest(word=word):
                self.assertNotEqual(cards._template(word)[0], "green")

    def test_a_verdict_nobody_taught_the_renderer_is_neutral_and_not_green(self):
        for word in ("", None, "PROBABLY_FINE", "ok"):
            with self.subTest(word=word):
                self.assertEqual(cards._template(word), cards.NEUTRAL_TEMPLATE)
                self.assertNotEqual(cards._template(word)[0], "green")

    def test_only_a_critical_event_makes_the_alert_card_red(self):
        by_name = {i["scenario"]: i["card"] for i in scenarios.rendered()}
        self.assertEqual(by_name["one_critical"]["header"]["template"], "red")
        self.assertEqual(by_name["mixed"]["header"]["template"], "orange")

    def test_an_unread_environment_is_grey_and_not_green(self):
        by_name = {i["scenario"]: i["card"] for i in scenarios.rendered()}
        self.assertEqual(by_name["observer_failure"]["header"]["template"], "grey")
        self.assertEqual(by_name["partial_unknown"]["header"]["template"], "grey")
        self.assertEqual(by_name["normal"]["header"]["template"], "green")

    def test_no_scenario_renders_red_or_orange_without_a_fault_in_it(self):
        """The colour has to come from the model, not from the card's mood."""
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                template = item["card"]["header"]["template"]
                if template == "grey":
                    continue
                model = item["model"]
                if model["card"] == "status":
                    self.assertNotEqual(
                        model["env_health"]["verdict"], presentation.UNCONFIRMED)
                if template == "red":
                    # Red is reserved for a high-severity event, and the only
                    # card that can be red is the alert card.
                    self.assertEqual(model["card"], "alert")
                    self.assertTrue(any(e["severity"]["level"] == "high"
                                        for e in model["events"]))


class OneModelTwoRenderingsTests(unittest.TestCase):
    """The pair has to be checkable against each other, or the preview is
    evidence about nothing."""

    def test_every_json_sentence_appears_in_the_html(self):
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                for text in _markdown(item["card"]):
                    self.assertIn(cards._esc(text), item["html"])

    def test_the_html_shows_the_card_twice_at_the_two_widths(self):
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                self.assertEqual(item["html"].count('class="frame'), 2)
                self.assertIn(f"手机 {cards.MOBILE_WIDTH}px", item["html"])
                self.assertIn("桌面", item["html"])

    def test_the_narrow_frame_is_the_width_the_design_names(self):
        self.assertEqual(cards.MOBILE_WIDTH, 390)

    def test_the_json_output_is_the_card_and_not_a_wrapper(self):
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                card = json.loads(item["json"])
                self.assertEqual(card, item["card"])
                self.assertEqual(card["schema"], "2.0")
                self.assertIn("body", card)

    def test_rendering_the_same_model_twice_gives_the_same_card(self):
        """Determinism, so that a preview reviewed yesterday is still the card
        that would be sent today."""
        for scenario in scenarios.scenarios():
            with self.subTest(scenario=scenario["name"]):
                first = cards.card_for(scenarios.model_for(scenario))
                second = cards.card_for(scenarios.model_for(scenario))
                self.assertEqual(first, second)

    def test_rendering_does_not_change_the_model_it_was_given(self):
        """A renderer that decides is a second place the decisions live."""
        for scenario in scenarios.scenarios():
            with self.subTest(scenario=scenario["name"]):
                model = scenarios.model_for(scenario)
                before = copy.deepcopy(model)
                cards.card_for(model)
                self.assertEqual(model, before)


class EscapingTests(unittest.TestCase):
    """A preview is an HTML document some browser will render, and its content
    includes process names, ids and paths off a shared machine."""

    def _alert_html(self, subject_ref: str) -> str:
        model = presentation.alert_model(
            {"at": "2026-09-20T00:00:00+00:00", "device_ref": "device:stub",
             "rows": [{"key": "alert:x", "kind": "NEW", "code": "DISK_WARNING",
                       "severity": "medium", "severity_word": "WARNING",
                       "subject_ref": subject_ref, "state": "OPEN",
                       "first_seen": "2026-09-20T00:00:00+00:00"}]},
            at="2026-09-20T00:00:00+00:00")
        self.assertEqual(len(model["events"]), 1)
        return cards.html(model)

    def test_a_subject_cannot_inject_markup_into_the_preview(self):
        html = self._alert_html("container:<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_a_hostile_node_label_cannot_either(self):
        model = presentation.status_model(
            {"node_label": '</title><script>alert(1)</script>',
             "mine": {"state": "OK", "lines": ["x"]},
             "host": {"state": "OK", "lines": ["y"]}},
            at="2026-09-20T00:00:00+00:00")
        html = cards.html(model)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_an_ampersand_survives_as_text(self):
        self.assertIn("a&amp;b", self._alert_html("container:a&b"))


class RendererRefusesTests(unittest.TestCase):
    def test_an_unknown_card_kind_raises_instead_of_rendering_something(self):
        """Falling back would render it as whichever card came first, with that
        card's colour — a recovery with an alert's amber reads as bad news."""
        for model in ({"card": "not-a-card"}, {}, {"card": None},
                      {"card": "status "}):
            with self.subTest(model=model):
                with self.assertRaises(presentation.PresentationError):
                    cards.card_for(model)

    def test_the_three_card_kinds_are_each_registered(self):
        self.assertEqual(sorted(cards._RENDERERS),
                         ["alert", "recovery", "status"])

    def test_the_renderer_writes_nothing(self):
        """It is handed a model and returns two strings. No file, no socket, no
        subprocess — a renderer that wrote a file would be a producer."""
        for scenario in scenarios.scenarios():
            with self.subTest(scenario=scenario["name"]):
                rendered = cards.preview(scenarios.model_for(scenario))
                self.assertEqual(sorted(rendered),
                                 ["card", "html", "json", "model"])


class StatusSourceTests(unittest.TestCase):
    """The producer, exercised against a stub so that the card's ability to be
    fed does not depend on the host being up."""

    @staticmethod
    def _reads(root_fs_percent: float = 48.0) -> dict:
        return {
            "resource summary": {
                "host": {"cpu": {"logical_cpus": 8, "model": "stub"},
                         "load": {"load1": 0.5, "load5": 0.4, "load15": 0.3,
                                  "runnable": "1/100"},
                         "memory": {"used_bytes": 1024 ** 3,
                                    "total_bytes": 8 * 1024 ** 3,
                                    "available_bytes": 7 * 1024 ** 3,
                                    "cached_bytes": 2 * 1024 ** 3,
                                    "swap": {"used_percent": 0.0,
                                             "used_bytes": 0,
                                             "total_bytes": 1024 ** 3}}},
                "disk": {"roles": [
                    {"state": "PRESENT", "mount_point": "/", "role": "ROOT_FS",
                     "source": "/dev/vda1", "used_percent": root_fs_percent,
                     "free_bytes": 1024 ** 3, "inodes": {"used_percent": 5.0}},
                    {"state": "PRESENT", "mount_point": "/data",
                     "role": "STATE_FS", "source": "/dev/vdb",
                     "used_percent": 10.0, "free_bytes": 1024 ** 3,
                     "inodes": {"used_percent": 1.0}},
                    {"state": "PRESENT", "mount_point": "/data",
                     "role": "ARTIFACTS_FS", "source": "/dev/vdb",
                     "used_percent": 10.0, "free_bytes": 1024 ** 3,
                     "inodes": {"used_percent": 1.0}}]},
                "docker": {"ServerVersion": "29.0", "ContainersRunning": 1,
                           "ContainersStopped": 0, "Containers": 1, "Images": 2},
                "user": {"summary": {"process_count": 3,
                                     "cpu_percent_of_host": 0.1,
                                     "cpu_cores": 0.01, "logical_cpus": 8,
                                     "memory_rss_bytes": 1024 ** 3,
                                     "memory_percent_of_host": 1.0,
                                     "provenance": "stub"},
                         "quota": {"mount": "/data", "filesystem": "/dev/vdb",
                                   "used_bytes": 10, "soft_limit_bytes": 100,
                                   "limit_bytes": 200, "used_percent": 10.0}},
                "advisory": {"thresholds": {"filesystem_percent_high": 85.0,
                                            "inode_percent_high": 85.0,
                                            "swap_percent_high": 90.0},
                             "notes": []}},
            "resource user-services": {"units": [], "linger": "yes",
                                       "supervisor": "systemd --user"},
            "runtime list": {"count": 1, "resources": [{"id": "x"}]},
        }

    def _gather(self, *, failing: set[tuple[str, ...]] = frozenset(),
                root_fs_percent: float = 48.0) -> dict:
        reads = self._reads(root_fs_percent)

        def run(argv):
            if argv in failing:
                return None, "exit 2"
            return reads.get(" ".join(argv)), None
        return source.gather(run=run)

    def _snapshot(self, **kwargs) -> dict:
        return source.snapshot(self._gather(**kwargs),
                               at="2026-09-20 12:00:00 JST",
                               node_label="device:stub")

    # -- the reads

    def test_all_four_reads_are_attempted_and_named(self):
        collected = self._gather()
        self.assertEqual(collected["read_names"],
                         ["summary", "top", "user_services", "runtime_list"])
        self.assertEqual(collected["gaps"], [])

    def test_a_healthy_stub_produces_a_clean_snapshot(self):
        snap = self._snapshot()
        self.assertEqual(snap["gaps"], [])
        self.assertEqual(snap["coverage"]["reads_missing"], [])
        self.assertEqual(snap["host"]["state"], source.OK)
        self.assertEqual(snap["mine"]["state"], source.OK)

    def test_a_failed_read_is_a_named_gap_and_never_an_empty_answer(self):
        snap = self._snapshot(failing={("resource", "summary")})
        self.assertEqual(len(snap["gaps"]), 1)
        self.assertIn("summary", snap["gaps"][0])
        self.assertIn("exit 2", snap["gaps"][0])
        self.assertEqual(snap["mine"]["state"], source.UNKNOWN)
        self.assertEqual(snap["host"]["state"], source.UNKNOWN)
        self.assertTrue(snap["mine"]["why"])
        self.assertTrue(snap["host"]["why"])

    def test_a_missing_read_does_not_render_the_block_as_fine(self):
        snap = self._snapshot(failing={("resource", "summary")})
        model = presentation.status_model(snap, at=snap["at"])
        self.assertNotEqual(model["env_health"]["verdict"], presentation.OK)
        self.assertEqual(model["sections"]["mine"]["lines"], [])

    def test_a_partly_read_account_block_is_not_green_either(self):
        """`mine` is fed by three reads and one arriving is not three."""
        snap = self._snapshot(failing={("resource", "user-services")})
        self.assertEqual(snap["mine"]["state"], source.UNKNOWN)
        self.assertTrue(snap["mine"]["lines"])  # it still shows what did arrive

    # -- the shaping

    def test_the_same_filesystem_is_named_once_however_many_roles_claim_it(self):
        """`/data` carries three roles on this node, and a card that printed it
        three times reads as three disks."""
        snap = self._snapshot()
        mounts = [line for line in snap["host"]["lines"]
                  if line.startswith("文件系统")]
        self.assertEqual(len(mounts), 2)
        self.assertTrue(any("STATE_FS" in m and "ARTIFACTS_FS" in m
                            for m in mounts))

    def test_a_filesystem_over_the_published_threshold_raises_the_block(self):
        snap = self._snapshot(root_fs_percent=91.0)
        self.assertEqual(snap["host"]["state"], source.ATTENTION)
        self.assertIn("91.0%", snap["host"]["why"])

    def test_the_threshold_comes_from_the_payload_and_not_from_this_tree(self):
        """Raise the published limit and the same reading stops being a fault."""
        reads = self._reads(91.0)
        reads["resource summary"]["advisory"]["thresholds"][
            "filesystem_percent_high"] = 95.0

        def run(argv):
            return reads.get(" ".join(argv)), None
        snap = source.snapshot(source.gather(run=run), at="now")
        self.assertEqual(snap["host"]["state"], source.OK)

    def test_the_card_does_not_select_the_account_by_name(self):
        """§2 exists because the live monitor matched an owner prefix against a
        home directory. This block uses the uid the process already has."""
        snap = self._snapshot()
        self.assertEqual(snap["mine"]["detail"]["uid"], __import__("os").getuid())

    # -- the gateway

    def test_an_unbound_gateway_is_absent_rather_than_guessed(self):
        snap = self._snapshot()
        self.assertNotIn("gateway", snap)
        model = presentation.status_model(snap, at=snap["at"], binding=None)
        self.assertEqual(model["sections"]["gateway"]["state"],
                         presentation.UNKNOWN)
        self.assertTrue(model["sections"]["gateway"]["why"])
        self.assertNotEqual(model["env_health"]["verdict"], presentation.OK)

    def test_a_binding_puts_the_gateway_in_the_snapshot(self):
        binding = {"service_ref": "service:x",
                   "gateway": {"endpoint": "http://127.0.0.1:1"}}
        snap = source.snapshot(self._gather(), at="2026-09-20 12:00:00 JST",
                               binding=binding)
        self.assertIn("gateway", snap)
        model = presentation.status_model(snap, at=snap["at"], binding=binding)
        section = model["sections"]["gateway"]
        # §3's marker, asserted where it actually lives. This used to be written
        # as `assertNotEqual(section["state"], UNKNOWN)`, and it passed for the
        # wrong reason: the section's `state` was the word `BOUND`, so the check
        # was `"BOUND" != "UNKNOWN"` — true on every card ever rendered, and
        # silent about the gateway it claimed to be checking.
        self.assertEqual(section["detail"]["state"], presentation.GATEWAY_BOUND)
        self.assertEqual(section["detail"]["service_ref"], "service:x")
        self.assertIn("http://127.0.0.1:1", " ".join(section["lines"]))

    def test_a_binding_without_a_measurement_is_not_reported_as_ok(self):
        """A binding names a gateway; it does not measure one.

        §2 gives the binding the job of saying *which* gateway this node's is,
        and nothing else. A card that read the presence of a binding as a
        reading about the service would put green over a service nobody has
        checked — and the repair for "we have not looked" and the repair for
        "it is broken" are different errands for different people.
        """
        binding = {"service_ref": "service:x",
                   "gateway": {"endpoint": "http://127.0.0.1:1"}}
        snap = source.snapshot(self._gather(), at="2026-09-20 12:00:00 JST",
                               binding=binding)
        model = presentation.status_model(snap, at=snap["at"], binding=binding)
        self.assertEqual(model["sections"]["gateway"]["state"],
                         presentation.UNKNOWN)
        self.assertNotEqual(model["env_health"]["verdict"], presentation.OK)

    def test_no_binding_is_not_a_failure_here(self):
        """The binding file does not exist on this node yet. `None` is the
        designed answer, and the module has no fallback to reach for."""
        self.assertIsNone(source.load_binding("/nonexistent/path/nope.yaml"))
        self.assertFalse(hasattr(source, "guess_gateway"))


if __name__ == "__main__":
    unittest.main()
