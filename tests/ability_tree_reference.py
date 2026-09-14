#!/usr/bin/env python3
"""The taxonomy two implementations must agree about, captured from the real one.

WHY THIS FILE IS A FIXTURE AND NOT AN INLINE LITERAL
----------------------------------------------------
§4 lets a node derive a discovery tree from the ability catalogue, and §13 says
a taxonomy change moves no file. Both are only checkable against a catalogue
that exists. `runtime/ability_projection.py`'s `domain_tree()` is one
implementation of that derivation; the authority has another, in
`scripts/render_ability_map.py` of ability-management, whose output is the
committed `ABILITY-MAP.md`.

The failure this file exists to prevent is the one this round keeps meeting: a
second spelling of the same idea that agrees with nothing. So `domain_tree()` is
compared against **what the other implementation actually emitted**, not against
what a reader of the spec thinks it should emit.

PROVENANCE — every value below was produced, not authored
---------------------------------------------------------
    source        ability-management `origin/main`, `abilities-manifest.yaml`
    rows          55 entries, projected exactly as `render_ability_map.py`
                  `_projection()` projects them, sorted by (name, id)
    expected      extracted from the rendered tree of
                  `python3 scripts/render_ability_map.py --stdout` run against
                  that manifest, which is byte-identical to the committed
                  `ABILITY-MAP.md` (verified by diff, 2026-09-14)
    map digest    `206b95accb89317d8f3543df1d6b9fee62cc7e088e8f8448cfb45343562087c4`
                  (that file's own metadata digest, for the whole catalogue)

If ability-management's taxonomy moves, this fixture does not follow it
automatically — and that is the intended behaviour. A fixture that re-derived
itself would agree with `domain_tree()` by construction and could never fail.
Refreshing it is a deliberate edit, and the digest above is what a refresher
checks against to know what they are replacing.
"""

# `render_ability_map.py`'s row shape verbatim: what it reads out of the
# manifest, and the only fields its tree algorithm is allowed to see.
ROWS = [
    {"name": "ability-management", "id": "ProfessorZhi/ability-management", "path": "skills/ability-management", "primary": "serveros.core", "refs": ["serveros.core"]},
    {"name": "agent-plugins", "id": "KuaaMU/agent-plugins", "path": "plugins/agent-plugins", "primary": None, "refs": []},
    {"name": "agent-session-management", "id": "ProfessorZhi/agent-session-management", "path": "skills/agent-session-management", "primary": "serveros.operations.runtime", "refs": ["serveros.operations"]},
    {"name": "archguard", "id": "ProfessorZhi/archguard", "path": "mcps/archguard", "primary": None, "refs": []},
    {"name": "archguard", "id": "ProfessorZhi/archguard", "path": "plugins/archguard", "primary": None, "refs": []},
    {"name": "backend-solution-design", "id": "VenusFennn/Backend-Solution-Design", "path": "skills/backend-solution-design", "primary": "serveros.engineering", "refs": ["serveros.engineering"]},
    {"name": "better-know", "id": "VenusFennn/Better-Know", "path": "plugins/better-know", "primary": None, "refs": []},
    {"name": "browser-use", "id": "browser-use/browser-use", "path": "mcps/browser-use", "primary": None, "refs": []},
    {"name": "browser-use", "id": "browser-use/browser-use", "path": "plugins/browser-use", "primary": None, "refs": []},
    {"name": "claude-plugins-official", "id": "anthropics/claude-plugins-official", "path": "plugins/claude-plugins-official", "primary": None, "refs": []},
    {"name": "codex-builtin", "id": "openai/codex-builtin", "path": "plugins/codex-builtin", "primary": None, "refs": []},
    {"name": "codex-curated-remote", "id": "openai/codex-curated-remote", "path": "plugins/codex-curated-remote", "primary": None, "refs": []},
    {"name": "codex-node-repl", "id": "openai/codex-node-repl", "path": "mcps/codex-node-repl", "primary": None, "refs": []},
    {"name": "codex-pr-body", "id": "openai/codex/codex-pr-body", "path": "skills/codex-pr-body", "primary": None, "refs": []},
    {"name": "codex-primary-runtime", "id": "openai/codex-primary-runtime", "path": "plugins/codex-primary-runtime", "primary": None, "refs": []},
    {"name": "computer-use", "id": "zavora-ai/computer-use", "path": "mcps/computer-use", "primary": None, "refs": []},
    {"name": "design-and-contract", "id": "ProfessorZhi/design-and-contract", "path": "skills/design-and-contract", "primary": None, "refs": []},
    {"name": "dev-doctor", "id": "ProfessorZhi/dev-doctor", "path": "skills/dev-doctor", "primary": None, "refs": []},
    {"name": "feature-developer", "id": "ProfessorZhi/archguard/feature-developer", "path": "skills/feature-developer", "primary": None, "refs": []},
    {"name": "feature-development", "id": "ProfessorZhi/feature-development", "path": "skills/feature-development", "primary": None, "refs": []},
    {"name": "feishu-report", "id": "ProfessorZhi/feishu-report", "path": "skills/feishu-report", "primary": "serveros.corporate.feishu", "refs": ["serveros.corporate"]},
    {"name": "feishu-space-management", "id": "ProfessorZhi/feishu-space-management", "path": "skills/feishu-space-management", "primary": "serveros.corporate.feishu", "refs": ["serveros.corporate"]},
    {"name": "fjllm-switch", "id": "ProfessorZhi/fjllm-switch", "path": "skills/fjllm-switch", "primary": "serveros.corporate", "refs": ["serveros.corporate"]},
    {"name": "fleet-coordination", "id": "ProfessorZhi/fleet-coordination", "path": "skills/fleet-coordination", "primary": "serveros.operations.fleet", "refs": ["serveros.operations"]},
    {"name": "frontend-design", "id": "anthropics/claude-plugins-official/frontend-design", "path": "skills/frontend-design", "primary": None, "refs": []},
    {"name": "frontend-solution-design", "id": "VenusFennn/Frontend-Solution-Design", "path": "skills/frontend-solution-design", "primary": "serveros.engineering", "refs": ["serveros.engineering"]},
    {"name": "good-pr", "id": "adewale/good-pr", "path": "skills/good-pr", "primary": None, "refs": []},
    {"name": "hooks", "id": "ProfessorZhi/hooks", "path": "hooks/hooks", "primary": None, "refs": []},
    {"name": "human-control", "id": "ProfessorZhi/human-control", "path": "skills/human-control", "primary": "serveros.operations.human-control", "refs": ["serveros.operations"]},
    {"name": "hwz-server-os", "id": "ProfessorZhi/hwz-server-os", "path": "plugins/hwz-server-os", "primary": None, "refs": []},
    {"name": "implementation-delivery", "id": "ProfessorZhi/implementation-delivery", "path": "skills/implementation-delivery", "primary": None, "refs": []},
    {"name": "information-management", "id": "ProfessorZhi/information-management", "path": "skills/information-management", "primary": "serveros.information", "refs": ["serveros.information"]},
    {"name": "instructions", "id": "ProfessorZhi/instructions", "path": "instructions/instructions", "primary": None, "refs": []},
    {"name": "inventory-publishing", "id": "ProfessorZhi/inventory-publishing", "path": "skills/inventory-publishing", "primary": "serveros.operations.inventory-publishing", "refs": ["serveros.operations"]},
    {"name": "meta-cc", "id": "ProfessorZhi/meta-cc", "path": "mcps/meta-cc", "primary": None, "refs": []},
    {"name": "meta-cc", "id": "ProfessorZhi/meta-cc", "path": "plugins/meta-cc", "primary": None, "refs": []},
    {"name": "muc-playwright-e2e", "id": "ProfessorZhi/muc-playwright-e2e", "path": "skills/muc-playwright-e2e", "primary": None, "refs": []},
    {"name": "opencli", "id": "jackwener/opencli", "path": "skills/opencli", "primary": None, "refs": []},
    {"name": "opencli", "id": "jackwener/opencli", "path": "plugins/opencli", "primary": None, "refs": []},
    {"name": "playwright-mcp", "id": "microsoft/playwright-mcp", "path": "mcps/playwright-mcp", "primary": None, "refs": []},
    {"name": "playwright-mcp", "id": "microsoft/playwright-mcp", "path": "plugins/playwright-mcp", "primary": None, "refs": []},
    {"name": "product-requirements-and-design", "id": "ProfessorZhi/product-requirements-and-design", "path": "skills/product-requirements-and-design", "primary": None, "refs": []},
    {"name": "project-semantics-discovery", "id": "ProfessorZhi/archguard/project-semantics-discovery", "path": "skills/project-semantics-discovery", "primary": None, "refs": []},
    {"name": "quality-feedback-and-iteration", "id": "ProfessorZhi/quality-feedback-and-iteration", "path": "skills/quality-feedback-and-iteration", "primary": None, "refs": []},
    {"name": "runtime-management", "id": "ProfessorZhi/runtime-management", "path": "skills/runtime-management", "primary": "serveros.operations.runtime", "refs": ["serveros.operations"]},
    {"name": "serveros", "id": "ProfessorZhi/serveros", "path": "skills/serveros", "primary": "serveros.core", "refs": ["serveros.core"]},
    {"name": "session-manager", "id": "ProfessorZhi/session-manager", "path": "skills/session-manager", "primary": None, "refs": []},
    {"name": "shared-memory", "id": "ProfessorZhi/shared-memory", "path": "skills/shared-memory", "primary": "serveros.memory", "refs": ["serveros.memory"]},
    {"name": "software-journey-orchestrator", "id": "ProfessorZhi/software-journey-orchestrator", "path": "skills/software-journey-orchestrator", "primary": None, "refs": []},
    {"name": "superpowers@cc-server-local", "id": "obra/superpowers", "path": "plugins/superpowers", "primary": None, "refs": []},
    {"name": "technical-architecture-and-planning", "id": "ProfessorZhi/technical-architecture-and-planning", "path": "skills/technical-architecture-and-planning", "primary": None, "refs": []},
    {"name": "test-management", "id": "ProfessorZhi/test-management", "path": "skills/test-management", "primary": "serveros.operations.test", "refs": ["serveros.engineering.quality", "serveros.operations"]},
    {"name": "vision", "id": "ProfessorZhi/vision", "path": "skills/vision", "primary": None, "refs": []},
    {"name": "work-management", "id": "ProfessorZhi/work-management", "path": "skills/work-management", "primary": "serveros.operations.work", "refs": ["serveros.operations"]},
    {"name": "workspace-management", "id": "ProfessorZhi/workspace-maintainer", "path": "skills/workspace-management", "primary": "serveros.operations.workspace", "refs": ["serveros.operations"]},
]

# The node set and the placement of every ability, as the OTHER implementation
# rendered them. `nodes` are dotted paths; `placements` maps an ability name to
# the node it was placed under, exactly once (§22).
EXPECTED_NODES = [
    "serveros",
    "serveros.core",
    "serveros.corporate",
    "serveros.corporate.feishu",
    "serveros.engineering",
    "serveros.information",
    "serveros.memory",
    "serveros.operations",
    "serveros.operations.fleet",
    "serveros.operations.human-control",
    "serveros.operations.inventory-publishing",
    "serveros.operations.runtime",
    "serveros.operations.test",
    "serveros.operations.work",
    "serveros.operations.workspace",
]

EXPECTED_PLACEMENTS = {
    "ability-management": "serveros.core",
    "agent-session-management": "serveros.operations.runtime",
    "backend-solution-design": "serveros.engineering",
    "feishu-report": "serveros.corporate.feishu",
    "feishu-space-management": "serveros.corporate.feishu",
    "fjllm-switch": "serveros.corporate",
    "fleet-coordination": "serveros.operations.fleet",
    "frontend-solution-design": "serveros.engineering",
    "human-control": "serveros.operations.human-control",
    "information-management": "serveros.information",
    "inventory-publishing": "serveros.operations.inventory-publishing",
    "runtime-management": "serveros.operations.runtime",
    "serveros": "serveros.core",
    "shared-memory": "serveros.memory",
    "test-management": "serveros.operations.test",
    "work-management": "serveros.operations.work",
    "workspace-management": "serveros.operations.workspace",
}

EXPECTED_CROSS_REFS = {
    "agent-session-management": ["serveros.operations"],
    "feishu-report": ["serveros.corporate"],
    "feishu-space-management": ["serveros.corporate"],
    "fleet-coordination": ["serveros.operations"],
    "human-control": ["serveros.operations"],
    "inventory-publishing": ["serveros.operations"],
    "runtime-management": ["serveros.operations"],
    "test-management": ["serveros.engineering.quality", "serveros.operations"],
    "work-management": ["serveros.operations"],
    "workspace-management": ["serveros.operations"],
}

# The counts the fixture is asserted against, so an emptied or truncated
# fixture fails instead of agreeing with an empty tree.
TOTAL = 55
CLASSIFIED = 17

