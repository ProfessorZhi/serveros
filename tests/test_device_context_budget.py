"""What a device agent is handed at session start stays small (FLEET-V1-OVERVIEW §2:
≤ ~8 KB with the rules and the resident skill descriptions from ability-management).

The parts that live in this repository: the local `serveros` skill core, and the
tool lists of the two MCP surfaces the device plugin starts — each served to an
agent as its `core` toolset, as the bundle declares.
"""
import importlib.util
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILL_CORE_MAX = 4096
MCP_TOOLS_MAX = 5200


def load(name, rel):
    sys.path.insert(0, str(ROOT / "mcp"))
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_local_serveros_skill_is_a_small_core_with_its_references():
    core = (ROOT / "skills/serveros/SKILL.md").read_text(encoding="utf-8")
    assert len(core.encode()) <= SKILL_CORE_MAX
    assert "硬边界" in core                      # the hard boundaries stay inline
    import re
    for ref in re.findall(r"`(references/[^`]+\.md)`", core):
        assert (ROOT / "skills/serveros" / ref).is_file(), ref


def test_the_bundle_serves_agents_the_core_toolsets():
    bundle = yaml.safe_load((ROOT / "plugin/bundle.yaml").read_text(encoding="utf-8"))
    env = {s["name"]: s.get("env") or {} for s in bundle["mcp"]["servers"]}
    assert env["serveros-node"]["SERVEROS_NODE_MCP_TOOLSET"] == "core"
    assert env["serveros-agent"]["SERVEROS_AGENT_MCP_TOOLSET"] == "core"


def test_the_two_core_tool_lists_fit_the_budget():
    node = load("node_mcp_budget", "mcp/node_mcp.py")
    agent = load("agent_mcp_budget", "mcp/agent_mcp.py")
    tools = list(node.served_tools("core")) + [t for t in agent.TOOLS
                                                if t["name"] in agent.served_names("core")]
    size = len(json.dumps(tools, ensure_ascii=False).encode())
    assert size <= MCP_TOOLS_MAX, size
    assert "agent.session.send" not in {t["name"] for t in tools}   # control goes through Fleet
