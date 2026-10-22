# ServerOS

**A personal agent work system for Claude Code and Codex.**

It does not replace the model, and it does not put a scheduler on top of one. It makes sure the model has the right environment, context, memory, capabilities and permission boundary at the moment it needs them.

[中文](README.md) · [Architecture guide](ARCHITECTURE.zh.md) · [Full architecture (normative)](ARCHITECTURE.md) · [Documentation map](docs/README.md)

---

## The problem it solves

When you work with an agent every day, the expensive part is rarely the code. It is the other four things:

- **Scattered material.** Feishu, GitHub, the web and meeting notes live in different places; every task starts with finding and re-explaining them.
- **Broken sessions.** A fresh session needs the earlier conclusions, constraints and progress all over again.
- **Heavy setup.** Before the agent can do the work, someone has to prepare directories, dependencies, test environments and permissions.
- **Disconnected devices.** Work does not carry across machines or projects.

ServerOS turns those into standing personal infrastructure: a node Core observes and manages the machine, Skills and MCPs deliver capabilities and context to the model, and the Work Engine records requirements, execution and results.

It is **not** a new chat client, a new model, or a second task scheduler. The model still decides how. ServerOS guarantees that it knows what it needs to know, can reach what it needs to use, and leaves evidence behind.

## Current status

| Area | Status | Notes |
|---|---|---|
| Tokyo node, daily use | In use | Core, plugin, MCPs and Work Engine run on the host |
| Fleet MCP (cross-device sessions) | Candidate | 34 tools (`fleet-mcp-5`); Tokyo and Dell enrolled, Windows pending native acceptance; V1 not released |
| Feishu information plane | Partial | Capture, Follow and Owned-document stewardship are connected ability by ability |
| Monitoring and status cards | Partial | Resource and runtime observation works; alerts and cards are still converging |
| Fleet V2 (autonomous organization, delegation) | Design only | No service or revision exists; do not treat it as a current capability |

Status is judged on three separate layers: **code exists ≠ installed ≠ accepted on real devices**. Criteria and evidence are in the [documentation map](docs/README.md).

## Five-minute start

The commands below were run in a fresh clone with an isolated venv (Linux, Python 3.12). ServerOS is a control plane for one authorized device: the repository-wide suite reads that device's profile and records. The subset below is the zero-configuration core.

```bash
git clone https://github.com/ProfessorZhi/serveros.git
cd serveros

python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install pytest pytest-xdist pytest-timeout pyyaml cryptography

export TMPDIR=/tmp/serveros-test-tmp
mkdir -p "$TMPDIR"
CUDA_VISIBLE_DEVICES="" nice -n 10 python -m pytest -q -n 8 --timeout=300 \
  tests/test_actor_context.py tests/test_agent_control_router.py \
  tests/test_agent_control_store.py tests/test_agent_mcp.py \
  tests/test_agent_sessions.py tests/test_approval.py \
  tests/test_business_calendar.py tests/test_card_delivery.py \
  tests/test_bootstrap_skill_scope.py tests/test_ability_resolver.py \
  tests/test_ability_projection.py
```

Measured: `638 passed, 185 subtests passed in 16.07s`. `TMPDIR` must be short; some tests create deep socket/work paths.

CLI development needs no running service. The two entry points are:

```bash
bin/serverosctl --help
python -m workengine --help
```

The repository-wide suite (`python -m pytest tests -q -n 8 --timeout=300`) additionally needs a real device profile, the records tree and a sibling `ability-management` checkout. With those roots on this host it measured `7561 passed, 79 failed, 71 skipped, 4532 subtests passed`; the remaining failures are bound to the live host, not a clean-machine baseline.

## One diagram

```text
You / CloudCLI / Feishu
        │
        ▼
Claude Code / Codex                     ← the mind: understand, research, design, build
        │
        ▼
ServerOS Plugin                         ← loads only the relevant context / Skills / MCPs
   ├─ serveros-node-mcp ─────────┐
   └─ serveros-agent-mcp ────────┤
                                 ▼
                              serverosd
                     the one resident Node Core
          node facts / admission / leases / effect coordination / self-health
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
   semantic and work         external providers       ability registry
   Claims / Requirement      Feishu / GitHub          Skills / MCPs
   Work Engine / Control     OpenViking / Quay        Plugins / Providers
```

Three things are enough to remember:

- **The model is the mind.** Research, experiments, decomposition, tool use and iteration belong to Claude Code / Codex.
- **The Core is the resident layer.** `serverosd` observes real resources, handles admission and leases, and coordinates external effects. It is not an LLM planner and does not reimplement systemd.
- **The Plugin is the entry point.** The two MCPs are adapters; neither keeps a second copy of ServerOS state.

## Five loops

Automation is five connected loops that degrade independently. They are not a mandatory workflow; one unavailable provider only affects the work that depends on it:

| Loop | From | To |
|---|---|---|
| Information | IM / docs / meetings / web / files | capture → structured understanding → memory → requirement candidates |
| Work | a formal requirement | Work Item → 0–2 Focus items → execution → acceptance → Outcome |
| Capability | a real capability gap | check native/installed/upstream → thinnest realization → eval/install → real consumer |
| Knowledge | a source revision | read only the delta → update understanding → maintain documents → read back |
| Runtime | observation | health/impact/ownership → bounded recovery → status card/Incident → a human only when needed |

Memory, authority and evidence cross all five. Details are in the [architecture guide](ARCHITECTURE.zh.md).

## Documentation map

| I want to… | Start here |
|---|---|
| Know what this is and what works | This page + [architecture guide](ARCHITECTURE.zh.md) |
| Operate or deploy | [Documentation map · operations](docs/README.md#运行与部署) |
| Change code, run tests | [Documentation map · development](docs/README.md#开发与契约) |
| Read the design and boundaries | [ARCHITECTURE.md](ARCHITECTURE.md) (normative) |
| See historical plans and evidence | [docs/plans](docs/plans), [docs/proposals](docs/proposals), [docs/reviews](docs/reviews) |

The full index is [docs/README.md](docs/README.md).

## Terms

| Term | What it means here |
|---|---|
| Agent | A native intelligence such as Claude Code or Codex. ServerOS does not implement its own. |
| Skill | A reusable working method loaded into the agent on demand. |
| MCP | Model Context Protocol; here, the adapter layer that exposes tools to an agent. |
| Ability | A product-level responsibility (such as "session management"), realized by Skills / MCPs / Plugins. |
| Node Core | `serverosd`, the one resident core on each managed device. |
| Fleet | The cross-device layer: discovering devices, routing sessions, constraining authority. |
| Item | A piece of formal work with stable identity, evidence and acceptance criteria. |
| Run | One independent, resumable, traceable execution. |
| Quay | An execution accelerator: delegating implementation, experiments and independent verification. |
| OpenViking | The long-term memory provider for cross-session, cross-project recall. |

## Repositories

ServerOS code lives only in **serveros** (the complete product, including `workengine/`) and **ability-management** (capability registry and Skill distribution). **hwz-records** is the private data repository the node reads and writes, not a second code repository.

| Repository | Owns |
|---|---|
| serveros | product architecture, plugin composition, entry points, deterministic contracts, node runtime, Work Engine |
| ability-management | capability identity, provenance, revisions, reusable Skills and distribution |
| hwz-records | understanding, work intent, designs, topology, policy and observations (private data) |
| marketops | MarketOps application/MCP, research pipeline and business truth |
| marketops-session | MarketOps session-derived design analysis and tooling |
| llmlab | LLM Lab gateway, evaluation and deployment templates |

The target structure is three public repositories (`serveros`, `marketops`, `llmlab`) and three private ones (`ability-management`, `marketops-session`, `hwz-records`). MarketOps and Support Bot remain independent parallel business projects; ServerOS does not take over their roadmaps.

## A design is not a deployment

Keep three things separate when reading this README:

- **The code exists**: the repository has an implementation and tests.
- **It is installed**: it actually runs on the target device.
- **It is accepted**: it passed real scenarios on real devices with real clients.

This page, file counts, tool listings and a successful documentation commit replace neither of the last two.
