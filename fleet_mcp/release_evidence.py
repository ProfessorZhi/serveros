"""Offline, fail-closed release-evidence linter for Fleet Next.

NOT a signing system or attestation verifier. A green manifest is only eligible
for human inspection against independent live receipts; never deploys anything.
"""
from __future__ import annotations

import argparse
import json
import re
from urllib.parse import urlsplit

SCHEMA = 'serveros/fleet-next-release-evidence/v1'
SCOPES = frozenset('fleet:' + s for s in ('read', 'send', 'create', 'control', 'approve', 'navigate'))
KINDS = frozenset(('cloudcli-deepseek', 'official-claude', 'marketops-claude', 'codex', 'other'))
CHECKS = (
    'full_regression', 'next_service_cgroup_readback', 'v1_health_before',
    'v1_health_after', 'next_health', 'next_only_rollback', 'public_https',
    'oauth_discovery', 'feishu_login', 'oauth_refresh', 'oauth_revocation',
    'scope_grants_and_refusals', 'all_tools_discovered_and_exercised',
    'mcp_skills_first_install', 'approved_avatar_rendered',
    'same_plugin_id_zip_update', 'backend_upgrade_without_plugin_reinstall',
    'real_time_monitoring',
)
LIFECYCLE = ('list', 'read', 'create', 'send', 'interrupt', 'reconfigure',
             'approval', 'close', 'resume', 'recover_after_disconnect')
ID = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')
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


def evaluate(report):
    problems = []
    if not isinstance(report, dict) or report.get('schema') != SCHEMA:
        return {'decision': 'BLOCKED', 'blockers': ['schema: wrong or missing'],
                'runtime_count': 0, 'effects': []}

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
    if (not isinstance(names, list) or len(names) != 33
            or any(not isinstance(n, str) or not n for n in names)
            or len(set(names)) != 33):
        block('tool_surface: must discover exactly 33 distinct real tools')
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

    def runtime_key(row):
        if not isinstance(row, dict):
            return None
        vals = (row.get('device'), row.get('runtime_id'), row.get('family'))
        if any(not isinstance(v, str) or not ID.fullmatch(v) for v in vals):
            return None
        return tuple(vals)

    target_keys = [runtime_key(r) for r in targets]
    observed_keys = [runtime_key(r) for r in observed]
    if any(k is None for k in target_keys + observed_keys):
        block('runtimes: device/runtime_id/family must be well formed')
    if len(target_keys) != len(set(target_keys)):
        block('required_runtimes: duplicate or ambiguous runtime')
    if len(observed_keys) != len(set(observed_keys)):
        block('observed_runtimes: duplicate runtime')
    by_key = {runtime_key(r): r for r in observed if runtime_key(r) is not None}

    for target in targets:
        k = runtime_key(target)
        if k is None:
            continue
        row = by_key.get(k)
        tag = ':'.join(k)
        if row is None:
            block('runtime ' + tag + ': no live observation')
            continue
        if row.get('kind') not in KINDS or target.get('kind') not in KINDS:
            block('runtime ' + tag + ': invalid profile kind')
        if k[2] not in ('claude', 'codex'):
            block('runtime ' + tag + ': unknown agent family')
        if target.get('kind') != row.get('kind'):
            block('runtime ' + tag + ': configured profile differs from target')
        if row.get('owner_verified') is not True or row.get('auth_isolated') is not True:
            block('runtime ' + tag + ': ownership or credential isolation missing')
        if (row.get('provenance') != 'LIVE' or
                not isinstance(row.get('receipt_ref'), str) or
                not row['receipt_ref'].strip()):
            block('runtime ' + tag + ': observation not backed by live receipt')
        actions = row.get('lifecycle')
        if not isinstance(actions, dict):
            actions = {}
        for act in LIFECYCLE:
            if actions.get(act) != 'PASS':
                block('runtime ' + tag + ': lifecycle.' + act + ' unverified')
        family = row.get('family')
        model = row.get('effective_model')
        mode = row.get('effective_permission_mode')
        if family == 'claude' and row.get('kind') == 'cloudcli-deepseek':
            if model != DEFAULT_DEEPSEEK or mode != 'bypassPermissions':
                block('runtime ' + tag + ': DeepSeek/CC owner default not verified')
        if family == 'codex' and (not model or model.startswith(('deepseek', 'v4.1flash'))):
            block('runtime ' + tag + ': Codex requires verified native model')
        if row.get('kind') in ('official-claude', 'marketops-claude') and (
                isinstance(model, str) and ('deepseek' in model.lower() or 'v4.1flash' in model.lower())):
            block('runtime ' + tag + ': dedicated Claude account inherits DeepSeek')

    return {
        'decision': 'REVIEW_CANDIDATE_ONLY' if not problems else 'BLOCKED',
        'blockers': sorted(set(problems)),
        'runtime_count': len(observed),
        'effects': [],
        'warning': ('JSON entries are self-reported. An independent reviewer must '
                    'validate live receipt provenance, resource safety, provider '
                    'identity and user consent before any deployment/cutover.'),
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
