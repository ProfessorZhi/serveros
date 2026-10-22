"""Offline, fail-closed release-evidence linter for Fleet Next.

NOT a signing system or attestation verifier. A green manifest is only eligible
for human inspection against independent live receipts; never deploys anything.

What this gate is for, restated after #199 read as too strict in one place and
too vague in another
--------------------------------------------------------------------------------
The first version required every declared lifecycle operation to be `PASS` on
every runtime. That conflates two different things wearing one word:

*   operations **this release promises** for an owner-managed runtime, and
*   operations whose availability is a **property of the provider**, where
    `SUPPORTED`, `UNSUPPORTED` and `UNKNOWN` are three different answers and a
    provider that genuinely lacks one has not failed anything.

Under the flat rule a Codex runtime whose provider has no interactive approval
channel blocked the release forever, and the only way past it was to write
`PASS` — which is the second half of the same bug: an unsupported operation
reported as supported is a *false* receipt that the gate then treats as the
strongest evidence there is. Both directions are refused here.

So the operations are split (`MANAGED_ACTIONS`, `CAPABILITY_ACTIONS`), and the
provider-gated side has to be *evidenced* rather than asserted: a declared
`UNSUPPORTED` needs its own receipt, has to agree with the per-operation
verdict, and cannot be used for anything the release promises. `UNKNOWN` — or
a field that is merely absent — blocks. That is the rule the task states as
"想要的功能没有真实验证必须阻断".

The runtime census is a six-dimensional **managed matrix**, not a list of
devices:

    Device × OSUser × family × credentialInstance × Runtime × Project

The first version keyed a runtime by `device × runtime_id × family`, which is
exactly wide enough to be wrong on the two machines this release is about. Two
Claude accounts under one OS UID collapse into one row; Dell's `vince` (uid
1002) and `fjd` (uid 1000) collapse into one row; and "which project was this
actually exercised on" is not asked at all. Each of those is a way a green
manifest can describe a runtime that was never verified.

What is deliberately NOT here
-----------------------------
No OpenAgents layering, no long-horizon autonomous-agent self-governance, no
resource-trend forecasting or self-healing. Those are V2 questions and a gate
that required them would hold a V1 release hostage to a roadmap. The resource
half that *is* a V1 precondition — short-window PSI and real per-project caps —
is one named check (`psi_and_resource_caps`) and the admission side lives in
`health_admission.py`.

And nothing here can promote the candidate. The best verdict is
`REVIEW_CANDIDATE_ONLY`, which names a state, not a permission: a passing
manifest asks a human to go and look. There is no code path that deploys,
installs, cuts over or triggers a rollout, and `effects` is always empty.
"""
from __future__ import annotations

import argparse
import json
import re
from urllib.parse import urlsplit

SCHEMA = 'serveros/fleet-next-release-evidence/v1'

# The six scopes this surface mints, exactly. A manifest that authorizes five
# has not exercised the sixth; one that authorizes a seventh names authority no
# code path honours.
SCOPES = frozenset('fleet:' + s for s in ('read', 'send', 'create', 'control', 'approve', 'navigate'))

# The operations the release *promises* for an owner-managed Claude/Codex
# runtime. Evidence for every one of them is a precondition, and the four
# answers are not interchangeable: `UNSUPPORTED` here is a blocked release, not
# a narrower one, because a runtime that cannot do these is not a runtime this
# release claims to manage.
MANAGED_ACTIONS = (
    'list', 'read', 'create', 'send', 'get_run', 'interrupt', 'resume',
    # Survival across a lost and restored connection. Listed with the promise
    # rather than with the provider-gated set because "the session I was told I
    # had is still mine after the tunnel flaps" is part of what managed means.
    'recover_after_disconnect',
)

# The operations whose availability belongs to the provider. These are the
# three the task names (`approval`, `reconfigure`, `close`) and the reason the
# old flat rule was wrong: a provider that does not offer one has not failed.
CAPABILITY_ACTIONS = ('approval', 'reconfigure', 'close')

# Exported for callers that want the whole surface; the split above is what the
# gate actually reads.
LIFECYCLE = MANAGED_ACTIONS + CAPABILITY_ACTIONS

VERDICT_PASS = 'PASS'
VERDICT_UNSUPPORTED = 'UNSUPPORTED'
VERDICT_UNKNOWN = 'UNKNOWN'

# Provider profiles this build knows how to reason about. `other` is kept in
# `KINDS` because a manifest may name it — and is refused by name below, so it
# cannot be used as a profile that skips the model and permission rules.
PROFILE_KINDS = ('cloudcli-deepseek', 'official-claude', 'marketops-claude', 'codex')
UNKNOWN_KIND = 'other'
KINDS = frozenset(PROFILE_KINDS + (UNKNOWN_KIND,))

# Acceptance items. Every entry is a V1 precondition and carries its own LIVE
# receipt; the list is named rather than implied so that adding one is a diff
# somebody reviews. `real_time_monitoring` from the first revision is renamed
# `psi_and_resource_caps`: the old name read as "monitoring is done", which a
# dashboard can satisfy, while the actual precondition is two specific things —
# a short-window PSI figure and real per-project caps. The V2 halves of
# monitoring (trend, forecast, self-heal) are not checks here.
CHECKS = (
    'full_regression', 'next_service_cgroup_readback', 'v1_health_before',
    'v1_health_after', 'next_health', 'next_only_rollback', 'public_https',
    'oauth_discovery', 'oauth_dcr_pkce', 'feishu_login', 'oauth_refresh',
    'oauth_revocation', 'scope_grants_and_refusals',
    'all_tools_discovered_and_exercised', 'mcp_skills_first_install',
    'approved_avatar_rendered', 'same_plugin_id_zip_update',
    'backend_upgrade_without_plugin_reinstall', 'psi_and_resource_caps',
)

ID = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')
# A Linux account name, as `fleet_mcp/runtime_identity.py` spells it. Narrow on
# purpose: this string is the axis that keeps two accounts on one box apart, so
# a value that cannot be an account name is not an account.
OS_USER = re.compile(r'^[a-z_][a-z0-9._-]{0,31}$')
DEFAULT_DEEPSEEK = 'deepseek-v4-flash-vision-anthropic'


def _https(value):
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme == 'https' and bool(parts.hostname)
                and parts.path.rstrip('/').endswith('/mcp')
                and '..' not in parts.path.split('/') and not (
                parts.username or parts.password or parts.query or parts.fragment))
    except ValueError:
        return False


def _receipt(row, key='receipt_ref'):
    """A live receipt reference, or None. Non-empty string is the whole rule."""
    value = row.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _key(row):
    """The managed-matrix key, or None if any dimension is unusable.

    `uid` is carried beside `os_user` rather than instead of it: a name is a
    claim about a machine's passwd file and a numeric id is what the kernel
    hands out, and the failure this matrix exists to catch — Dell's two accounts
    read as one row — is a collision that a name-only key does not see when the
    same account is spelled two ways.
    """
    if not isinstance(row, dict):
        return None
    device, os_user, runtime_id = row.get('device'), row.get('os_user'), row.get('runtime_id')
    family, instance = row.get('family'), row.get('credential_instance')
    uid = row.get('uid')
    if any(not isinstance(v, str) or not ID.fullmatch(v)
           for v in (device, runtime_id, instance)):
        return None
    if not isinstance(os_user, str) or not OS_USER.fullmatch(os_user):
        return None
    if type(uid) is not int or uid <= 0:
        return None
    if family not in ('claude', 'codex'):
        return None
    return (device, os_user, uid, runtime_id, family, instance)


def _tag(key):
    return ':'.join(str(part) for part in key)


def _check_lifecycle(key, row, unsupported):
    """Verdicts for one runtime's declared operations.

    The two groups are judged by different rules, and the difference is the
    point of this function:

    *   a promised operation is `PASS` or the release is blocked;
    *   a provider-gated operation may be `PASS` or `UNSUPPORTED`, and an
        `UNSUPPORTED` has to be declared — with its own receipt — rather than
        appearing in the verdict map alone.

    Both groups refuse `UNKNOWN` and refuse absence, because those are the same
    answer to a reviewer and neither is evidence.
    """
    actions = row.get('lifecycle')
    if not isinstance(actions, dict):
        actions = {}
    problems = []
    for act in MANAGED_ACTIONS:
        verdict = actions.get(act)
        if verdict == VERDICT_PASS:
            continue
        if verdict == VERDICT_UNSUPPORTED:
            problems.append('runtime ' + key + ': lifecycle.' + act +
                            ' is a promised operation and cannot be waived as UNSUPPORTED')
        elif verdict == VERDICT_UNKNOWN or verdict is None:
            problems.append('runtime ' + key + ': lifecycle.' + act + ' unverified')
        else:
            problems.append('runtime ' + key + ': lifecycle.' + act +
                            ' has no defined verdict')
    for act in CAPABILITY_ACTIONS:
        verdict = actions.get(act)
        if verdict == VERDICT_PASS:
            if act in unsupported:
                # Declared impossible and reported working. One of the two is
                # wrong, and a gate that picked a side would be inventing the
                # receipt it is supposed to be reading.
                problems.append('runtime ' + key + ': lifecycle.' + act +
                                ' is UNSUPPORTED in its capability record and PASS here')
            continue
        if verdict == VERDICT_UNSUPPORTED:
            if act not in unsupported:
                problems.append('runtime ' + key + ': lifecycle.' + act +
                                ' claims UNSUPPORTED with no capability receipt')
            continue
        if verdict == VERDICT_UNKNOWN or verdict is None:
            problems.append('runtime ' + key + ': lifecycle.' + act + ' unverified')
            continue
        problems.append('runtime ' + key + ': lifecycle.' + act + ' has no defined verdict')
    return problems


def _capability_records(key, row):
    """The declared-unsupported map, refusing every way it can mean too much."""
    raw = row.get('capability_unsupported')
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, ['runtime ' + key + ': capability_unsupported must be a mapping']
    problems = []
    out = {}
    for act, ref in raw.items():
        if act in MANAGED_ACTIONS:
            # The promise cannot be narrowed by declaring part of it missing.
            problems.append('runtime ' + key + ': capability_unsupported names ' + str(act) +
                            ', which is a promised operation and not the provider\'s to waive')
            continue
        if act not in CAPABILITY_ACTIONS:
            problems.append('runtime ' + key + ': capability_unsupported names ' + str(act) +
                            ', which is not an operation this gate knows')
            continue
        if not isinstance(ref, str) or not ref.strip():
            problems.append('runtime ' + key + ': capability_unsupported[' + str(act) +
                            '] needs its own receipt; an unsupported claim without one is '
                            'an assertion, not evidence')
            continue
        out[act] = ref
    return out, problems


def _project_problems(key, target, row):
    """Projects are the sixth dimension, checked rather than assumed."""
    wanted = target.get('projects')
    if not isinstance(wanted, list) or not wanted:
        return ['runtime ' + key + ': required runtime names no project']
    have = row.get('projects')
    if not isinstance(have, list):
        return ['runtime ' + key + ': observed runtime names no project']
    problems = []
    for project in wanted:
        if not isinstance(project, str) or not ID.fullmatch(project):
            problems.append('runtime ' + key + ': project id ' + repr(project) + ' is malformed')
        elif project not in have:
            problems.append('runtime ' + key + ': project ' + project + ' was never exercised')
    return problems


def _profile_problems(key, row):
    problems = []
    family, kind = row.get('family'), row.get('kind')
    if kind not in KINDS:
        problems.append('runtime ' + key + ': invalid profile kind')
        return problems
    if kind == UNKNOWN_KIND:
        # Refused rather than ignored: `other` is the one value that would let a
        # manifest skip every model and permission rule below by declining to
        # say what it is.
        problems.append('runtime ' + key + ': profile kind is unknown, and an unknown '
                        'profile is not a verified one')
    model = row.get('effective_model')
    mode = row.get('effective_permission_mode')
    if family == 'claude' and kind == 'cloudcli-deepseek':
        if model != DEFAULT_DEEPSEEK or mode != 'bypassPermissions':
            problems.append('runtime ' + key + ': DeepSeek/CC owner default not verified')
    if family == 'codex':
        if not isinstance(model, str) or not model.strip() or model.startswith(('deepseek', 'v4.1flash')):
            problems.append('runtime ' + key + ': Codex requires a verified native model')
        # A Codex runtime without its provider is not a Codex runtime. The
        # model string alone can be typed by hand; the provider is what the
        # runtime actually talks to, so it is required and it is required to
        # carry a receipt of its own.
        provider = row.get('provider')
        if not isinstance(provider, str) or not provider.strip():
            problems.append('runtime ' + key + ': Codex names no provider')
        elif row.get('provider_verified') is not True or not _receipt(row, 'provider_receipt_ref'):
            problems.append('runtime ' + key + ': Codex provider is not verified by a receipt')
    if kind in ('official-claude', 'marketops-claude') and isinstance(model, str) and (
            'deepseek' in model.lower() or 'v4.1flash' in model.lower()):
        problems.append('runtime ' + key + ': dedicated Claude account inherits DeepSeek')
    return problems


def evaluate(report):
    problems = []
    if not isinstance(report, dict) or report.get('schema') != SCHEMA:
        return {'decision': 'BLOCKED', 'blockers': ['schema: wrong or missing'],
                'runtime_count': 0, 'managed_matrix': [], 'effects': []}

    def block(detail):
        problems.append(detail)

    identity = report.get('identity')
    if not isinstance(identity, dict):
        identity = {}
    for f in ('v1_plugin_id', 'next_plugin_id', 'next_release_id', 'next_backend_sha'):
        if not isinstance(identity.get(f), str) or not ID.fullmatch(identity[f]):
            block('identity.' + f + ': missing or invalid')
    if identity.get('v1_plugin_id') == identity.get('next_plugin_id'):
        block('identity: Next and V1 plugin IDs must be different')
    if not _https(identity.get('v1_mcp_url')) or not _https(identity.get('next_mcp_url')):
        block('identity: both real HTTPS MCP URLs required')
    elif identity['v1_mcp_url'].rstrip('/') == identity['next_mcp_url'].rstrip('/'):
        block('identity: Next and V1 endpoints may not coincide')

    surface = report.get('tool_surface')
    if not isinstance(surface, dict):
        surface = {}
    names = surface.get('observed_tools')
    if (not isinstance(names, list) or len(names) != 34
            or any(not isinstance(n, str) or not n for n in names)
            or len(set(names)) != 34):
        block('tool_surface: must discover exactly 34 distinct real tools')
    given_scopes = surface.get('authorized_scopes')
    if (not isinstance(given_scopes, list) or
            any(not isinstance(v, str) for v in given_scopes) or
            set(given_scopes) != SCOPES):
        block('tool_surface: six exact OAuth scopes must be authorized')

    checks = report.get('checks')
    if not isinstance(checks, dict):
        checks = {}
    for label in CHECKS:
        row = checks.get(label)
        if (not isinstance(row, dict) or row.get('result') != 'PASS'
                or row.get('provenance') != 'LIVE'
                or not isinstance(row.get('receipt_ref'), str)
                or not row['receipt_ref'].strip()):
            block('checks.' + label + ': missing LIVE PASS and receipt')

    targets = report.get('required_runtimes')
    observed = report.get('observed_runtimes')
    if not isinstance(targets, list) or not targets:
        targets = []
        block('required_runtimes: list must not be empty')
    if not isinstance(observed, list):
        observed = []

    target_keys = [_key(r) for r in targets]
    observed_keys = [_key(r) for r in observed]
    if any(k is None for k in target_keys + observed_keys):
        # Named once, generally, because the missing dimension is what a reader
        # needs to go and find; the per-row names below say which row.
        block('runtimes: every runtime must name device, os_user, uid, runtime_id, '
              'family and credential_instance')
    if len(target_keys) != len(set(target_keys)):
        block('required_runtimes: duplicate or ambiguous managed runtime')
    if len(observed_keys) != len(set(observed_keys)):
        block('observed_runtimes: duplicate managed runtime')
    by_key = {_key(r): r for r in observed if _key(r) is not None}

    # One OS account cannot be two different uids on one device. This is the
    # "Dell two accounts must not be crossed" rule stated as a contradiction to
    # look for, rather than trusting the rows to be individually well formed.
    seen_uid = {}
    for key, row in by_key.items():
        device, os_user, uid = key[0], key[1], key[2]
        prior = seen_uid.get((device, os_user))
        if prior is not None and prior != uid:
            block('runtime ' + _tag(key) + ': OS account ' + os_user + ' on ' + device +
                  ' is reported under two different uids (' + str(prior) + ' and ' + str(uid) + ')')
        seen_uid[(device, os_user)] = uid

    matrix = []
    for target in targets:
        k = _key(target)
        if k is None:
            continue
        tag = _tag(k)
        matrix.append(k)
        row = by_key.get(k)
        if row is None:
            block('runtime ' + tag + ': no live observation')
            continue
        if target.get('kind') not in PROFILE_KINDS:
            block('runtime ' + tag + ': required profile kind unknown')
        if row.get('kind') not in PROFILE_KINDS:
            block('runtime ' + tag + ': observed profile kind unknown')
        elif target.get('kind') != row.get('kind'):
            block('runtime ' + tag + ': configured profile differs from target')
        if row.get('owner_verified') is not True or row.get('auth_isolated') is not True:
            block('runtime ' + tag + ': ownership or credential isolation missing')
        if row.get('reachable') is not True:
            # A runtime that is not reachable has no live observation, whatever
            # the receipt file says.
            block('runtime ' + tag + ': runtime not confirmed reachable')
        if row.get('provenance') != 'LIVE' or not _receipt(row):
            block('runtime ' + tag + ': observation not backed by live receipt')
        unsupported, cap_problems = _capability_records(tag, row)
        problems.extend(cap_problems)
        problems.extend(_check_lifecycle(tag, row, unsupported))
        problems.extend(_project_problems(tag, target, row))
        problems.extend(_profile_problems(tag, row))

    capabilities = {_tag(k): sorted(_capability_records(_tag(k), by_key[k])[0])
                    for k in by_key}

    return {
        'decision': 'REVIEW_CANDIDATE_ONLY' if not problems else 'BLOCKED',
        'blockers': sorted(set(problems)),
        'runtime_count': len(observed),
        'managed_matrix': [list(k) for k in matrix],
        'capability_unsupported': capabilities,
        'effects': [],
        'warning': ('JSON entries are self-reported. An independent reviewer must '
                    'validate live receipt provenance, resource safety, provider '
                    'identity and user consent before any deployment/cutover. '
                    'REVIEW_CANDIDATE_ONLY names a state to look at, not a '
                    'permission to release.'),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--evidence', required=True)
    args = p.parse_args(argv)
    try:
        with open(args.evidence, encoding='utf-8') as f:
            report = json.load(f)
    except (OSError, ValueError):
        print(json.dumps({'decision': 'BLOCKED',
                          'blockers': ['evidence file unreadable'], 'effects': []}))
        return 2
    result = evaluate(report)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result['decision'] == 'REVIEW_CANDIDATE_ONLY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
