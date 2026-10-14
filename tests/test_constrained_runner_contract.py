#!/usr/bin/env python3
"""§169 · the runner's declaration and its code must say the same numbers.

WHY THIS FILE EXISTS
--------------------
The envelope an operator reads (`runtime/constrained-test-runner-config.yaml`)
and the envelope the runner asks systemd for (`runtime/constrained_runner.py`)
are two copies of one decision, and two copies drift. The one that drifts is
always the one a person reads, because a person reads the document and a
program reads the code — so a `MemoryMax` raised in code and not in the file
would leave a runbook that is confidently wrong about the bound, which is the
kind of wrong this whole round is about.

So the numbers are not restated here either. This file compares the two copies
that exist and fails when they disagree. It adds no third one.

The other half is the promise the runner makes to §167: the config declares a
read interface and no threshold. If a later edit puts a threshold or a
notification path in the config, the runner would have become a second alerting
system, and §167's brief forbids exactly that. That is asserted, not trusted to
review.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
CONFIG = RUNTIME / "constrained-test-runner-config.yaml"
RUNBOOK = REPO / "docs" / "operations" / "constrained-test-runner.md"

GIB = 1024 ** 3


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cr():
    return _load("serveros_runtime_constrained_runner", "constrained_runner.py")


@pytest.fixture(scope="module")
def cli_mod():
    return _load("serveros_runtime_cli", "cli.py")


@pytest.fixture(scope="module")
def config():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


class TestTheTwoCopiesOfTheEnvelopeAgree:
    def test_the_config_declares_exactly_the_profiles_the_module_has(self, cr, config):
        assert set(config["profiles"]) == set(cr.PROFILES)

    def test_every_number_matches(self, cr, config):
        for name, declared in config["profiles"].items():
            limits = cr.resolve_limits(name, authorized=True)
            assert declared["memory_high_gib"] * GIB == limits["memory_high_bytes"], name
            assert declared["memory_max_gib"] * GIB == limits["memory_max_bytes"], name
            assert declared["cpu_quota_percent"] == limits["cpu_quota_percent"], name
            assert declared["runtime_max_sec"] == limits["runtime_max_sec"], name
            assert declared["tasks_max"] == limits["tasks_max"], name
            assert declared["needs_grant"] == cr.PROFILES[name]["requires_authorization"], name

    def test_the_ceiling_in_the_config_is_the_ceiling_that_is_enforced(self, cr, config):
        ceiling_bytes = config["escalation"]["ceiling_memory_gib"] * GIB
        ceiling_sec = config["escalation"]["ceiling_runtime_sec"]
        for name in cr.PROFILES:
            limits = cr.resolve_limits(name, authorized=True)
            assert limits["memory_max_bytes"] <= ceiling_bytes, name
            assert limits["runtime_max_sec"] <= ceiling_sec, name

    def test_the_config_names_the_flags_the_verb_actually_takes(self, cli_mod, config):
        parser = cli_mod.build_parser()
        args = parser.parse_args([
            "test", "run", "--worktree", "/tmp/wt", "--all", "--authorized",
            "--reason", "why",
        ])
        assert getattr(args, config["escalation"]["reason_flag"].lstrip("-")
                       .replace("-", "_")) == "why"
        assert args.authorized is True


class TestTheConfigIsNotASecondAlertingSystem:
    def test_the_config_declares_no_threshold(self, config):
        """§167 owns thresholds; a number here would be a second opinion.

        The check is over KEYS, not over the whole document: the file has to be
        able to *say* that §167 owns the per-uid thresholds without that
        sentence being mistaken for one of its own. What must not appear is a
        threshold this file defines.
        """
        keys: list[str] = []

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    keys.append(str(key))
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(config)
        for forbidden in ("threshold", "cooldown", "notify", "notification",
                          "webhook", "alert_when", "percent_of_host", "delivery"):
            assert not any(forbidden in key.lower() for key in keys), forbidden

    def test_the_linkage_is_one_way_and_states_its_consumer(self, config):
        linkage = config["alerting_linkage"]
        assert "#167" in linkage["measured_by"]
        assert "#167" in linkage["detected_by"]
        assert linkage["shared_subject"] == "uid:<n>"
        assert "index.jsonl" in linkage["produced_by_this_runner"]

    def test_the_runner_records_the_fields_the_linkage_promises(self, cr, config):
        """A declared read interface must name keys the receipt actually has."""
        promised = set(config["alerting_linkage"]["fields"])
        source = (RUNTIME / "constrained_runner.py").read_text(encoding="utf-8")
        for field in promised:
            assert field in source, field


class TestTheNeverListIsStructural:
    def test_the_slice_prohibition_is_declared(self, config):
        text = " ".join(str(entry) for entry in config["never"])
        assert "user.slice" in text and "root.slice" in text

    def test_the_module_emits_no_property_that_names_a_slice(self, cr, tmp_path):
        for name in cr.PROFILES:
            argv = cr.build_launch_argv(
                unit="serveros-test-20260101T000000Z-abc.service",
                limits=cr.resolve_limits(name, authorized=True),
                worktree=tmp_path, pytest_bin="/venv/bin/pytest",
                test_args=["tests"], run_dir=tmp_path, systemd_run_bin="systemd-run")
            assert ".slice" not in " ".join(argv), name

    def test_the_home_prohibition_is_declared(self, config):
        text = " ".join(str(entry) for entry in config["never"])
        assert "home directory" in text


class TestTheVerbIsReachableAndDocumented:
    def test_the_run_verb_exists_under_the_test_group(self, cli_mod):
        args = cli_mod.build_parser().parse_args(
            ["test", "run", "--worktree", "/tmp/wt", "--all"])
        assert args.command == "test"
        assert args.subcommand == "run"
        assert args.func is cli_mod.cmd_test_run

    def test_an_empty_selection_is_not_accepted_by_the_parser(self, cli_mod):
        args = cli_mod.build_parser().parse_args(["test", "run", "--worktree", "/tmp/wt"])
        assert args.tests == []
        assert args.all_tests is False

    def test_the_runbook_names_the_command_the_parser_accepts(self, cli_mod):
        """The gate for `serverosctl <a> <b>` in prose lives in
        `tests/test_declared_commands.py`; this is the narrower claim that the
        runbook's own line is the verb this branch added."""
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "serverosctl test run" in text
        args = cli_mod.build_parser().parse_args(
            ["test", "run", "--worktree", "/tmp/wt", "--tests", "tests/test_a.py"])
        assert args.func is cli_mod.cmd_test_run

    def test_the_runbook_states_the_default_envelope(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "3 GiB" in text and "4 GiB" in text
        assert "600 s" in text and "400%" in text
