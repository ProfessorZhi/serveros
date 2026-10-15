"""One-shot, read-only Fleet Next admission decisions from fresh monitoring data.

The caller gathers measurements through separately authorized device probes.
This module does not poll, SSH, restart, kill, inspect credentials or run agents.

What the decision is, and what it is not
----------------------------------------
`GO_ONE_BOUNDED_RUN` is advice about *one more* bounded run on one managed
runtime. It is not a claim that the runtime is healthy, not a permission, and
not a trigger: the return value has no path to starting anything, and `writes`
is always empty. A caller that treats a GO as authorization has skipped the
owner grant this module only *reads*.

Why a runtime is addressed by six things
----------------------------------------
The first revision keyed an admission by `(device_ref, runtime_id)`. On the two
machines this release is about that is one axis short in two directions at
once: Dell's `vince` (uid 1002) and `fjd` (uid 1000) are one pair of strings
apart, and one OS UID running two Claude accounts is not two strings apart at
all. Either collision turns two runtimes into one admission decision, and the
decision that survives is whichever row was read last. So the key carries the
same dimensions the evidence matrix does:

    device_ref × os_user × uid × runtime_id × family × credential_instance

Stuck sessions, and the two ways to get them wrong
--------------------------------------------------
A session whose run is not progressing is the interesting case, because both
naive readings are wrong in opposite directions. Counting it as a free slot
admits a new run onto a runtime that is still holding whatever the wedged
session holds — the "misjudged as new" failure. Counting it as an ordinary
running session and nothing else hides it, so an operator sees a runtime at its
ceiling with no explanation. It is therefore counted *and* named: it consumes a
slot, and its presence is its own refusal reason with its own wording.

Liveness is required to be reported, not inferred. A row that does not say how
many sessions are stuck has not said the runtime is usable, and "unknown" is
not "zero".

Used Swap occupancy deliberately is NOT an automatic refusal. Linux may leave
cold anonymous pages swapped after earlier pressure; only current activity/PSI
and actual per-runtime isolation drive admission.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time

SCHEMA = 'serveros/fleet-next-health-snapshot/v1'
MAX_AGE_S = 60

# A Linux account name, matching `fleet_mcp/runtime_identity.py`.
_OS_USER = re.compile(r'^[a-z_][a-z0-9._-]{0,31}$')

# The credential states this module reasons about. `active` is the only one
# that can be admitted: a revoked credential is not a runtime the owner can be
# handed, and `restored` is deliberately *not* its own state — a credential
# that has been re-granted is `active` again and must carry a fresh sample like
# anything else, so the recovery path is the ordinary path rather than a
# shortcut past the freshness rule.
_CREDENTIAL_ACTIVE = 'active'
_CREDENTIAL_REVOKED = 'revoked'


def _valid_float(x):
    return type(x) in (int, float) and math.isfinite(x) and x >= 0


def _valid_uid(x):
    return type(x) is int and x > 0


def runtime_key(row):
    """The managed-matrix key, or None if any dimension is unusable.

    Exported because a caller assembling the snapshot is best placed to see
    which dimension it omitted, and a helper it can call before submitting is
    cheaper than a refusal it has to decode.
    """
    if not isinstance(row, dict):
        return None
    device, runtime_id = row.get('device_ref'), row.get('runtime_id')
    os_user, instance = row.get('os_user'), row.get('credential_instance')
    family, uid = row.get('family'), row.get('uid')
    if any(not isinstance(v, str) or not v for v in (device, runtime_id, instance)):
        return None
    if not isinstance(os_user, str) or not _OS_USER.fullmatch(os_user):
        return None
    if family not in ('claude', 'codex'):
        return None
    if not _valid_uid(uid):
        return None
    return (device, os_user, uid, runtime_id, family, instance)


def _tag(key):
    return ':'.join(str(part) for part in key)


def assess(snapshot, *, now=None):
    now = time.time() if now is None else now
    if not isinstance(snapshot, dict) or snapshot.get('schema') != SCHEMA:
        return {'status': 'INVALID_SNAPSHOT', 'admissions': [], 'writes': []}
    hosts = snapshot.get('hosts')
    runtimes = snapshot.get('runtimes')
    if not isinstance(hosts, list) or not isinstance(runtimes, list):
        return {'status': 'INVALID_SNAPSHOT', 'admissions': [], 'writes': []}
    host_by_id = {}
    duplicate_hosts = set()
    for h in hosts:
        if not isinstance(h, dict) or not isinstance(h.get('device_ref'), str):
            continue
        k = h['device_ref']
        if k in host_by_id:
            duplicate_hosts.add(k)
        host_by_id[k] = h

    # One OS account cannot be two uids on one device. Collected across the
    # whole snapshot rather than per row, because each row of the collision is
    # individually well formed — that is what makes it worth looking for.
    uid_by_account = {}
    uid_conflicts = set()
    for row in runtimes:
        key = runtime_key(row)
        if key is None:
            continue
        device, os_user, uid = key[0], key[1], key[2]
        prior = uid_by_account.get((device, os_user))
        if prior is not None and prior != uid:
            uid_conflicts.add((device, os_user))
        uid_by_account[(device, os_user)] = uid

    admissions = []
    seen_runtimes = set()
    for row in runtimes:
        if not isinstance(row, dict):
            admissions.append({'runtime_id': None, 'decision': 'NO_GO',
                               'reasons': ['malformed runtime record']})
            continue
        key = runtime_key(row)
        dev = row.get('device_ref')
        rid = row.get('runtime_id')
        reasons = []
        if key is None:
            # The specific dimensions are named, because "malformed" sends an
            # operator to re-read the whole schema and this sends them to the
            # one field that is missing.
            missing = []
            for name, ok in (
                ('device_ref', isinstance(dev, str) and bool(dev)),
                ('runtime_id', isinstance(rid, str) and bool(rid)),
                ('os_user', isinstance(row.get('os_user'), str)
                 and bool(_OS_USER.fullmatch(row.get('os_user') or ''))),
                ('uid', _valid_uid(row.get('uid'))),
                ('family', row.get('family') in ('claude', 'codex')),
                ('credential_instance', isinstance(row.get('credential_instance'), str)
                 and bool(row.get('credential_instance'))),
            ):
                if not ok:
                    missing.append(name)
            reasons.append('managed identity incomplete: ' + ','.join(missing))
        elif key in seen_runtimes:
            reasons.append('duplicate runtime identity')
        if key is not None:
            seen_runtimes.add(key)
            if (key[0], key[1]) in uid_conflicts:
                reasons.append('OS account reported under two uids on one device')

        h = host_by_id.get(dev)
        if dev in duplicate_hosts:
            reasons.append('host observation ambiguous')
        if h is None:
            reasons.append('no host observation')
        else:
            if h.get('reachable') is not True:
                reasons.append('host not confirmed reachable')
            ts = h.get('sampled_at')
            if h.get('source') != 'LIVE' or not _valid_float(ts) or not 0 <= now-ts <= MAX_AGE_S:
                reasons.append('host telemetry missing or stale')
            for name in ('memory_psi_full_avg10', 'swap_out_mib_s', 'blocked_tasks'):
                if not _valid_float(h.get(name)):
                    reasons.append('host '+name+' unknown')
            if _valid_float(h.get('memory_psi_full_avg10')) and h['memory_psi_full_avg10'] > 15:
                reasons.append('active memory stalls')
            if _valid_float(h.get('swap_out_mib_s')) and h['swap_out_mib_s'] > 2:
                reasons.append('active swap writes')
            if _valid_float(h.get('blocked_tasks')) and h['blocked_tasks'] > 20:
                reasons.append('blocked queue too large')

        if row.get('source') != 'LIVE' or row.get('owner_approved') is not True:
            reasons.append('runtime not owner-verified')
        if row.get('reachable') is not True:
            reasons.append('runtime not confirmed reachable')
        rts = row.get('sampled_at')
        if not _valid_float(rts) or not 0 <= now-rts <= MAX_AGE_S:
            reasons.append('runtime observation stale')

        state = row.get('credential_state')
        if state == _CREDENTIAL_REVOKED:
            reasons.append('credential revoked')
        elif state != _CREDENTIAL_ACTIVE:
            # Absent, misspelled, or a state this build does not know. All three
            # are "nobody has said this credential works", which is not a yes.
            reasons.append('credential state unknown')

        if row.get('family') == 'codex':
            provider = row.get('provider')
            if not isinstance(provider, str) or not provider.strip():
                # A Codex runtime with no provider is a runtime that has nothing
                # to run on; the model string is not a substitute for it.
                reasons.append('Codex provider missing')

        n = row.get('running_sessions')
        stuck = row.get('stuck_sessions')
        quota = row.get('max_sessions')
        if (type(n) is not int or type(quota) is not int or n < 0 or quota <= 0
                or type(stuck) is not int or stuck < 0):
            reasons.append('runtime concurrency or liveness budget unknown')
        else:
            if stuck > 0:
                # Counted *and* named. Counted, so the slot is not handed out
                # twice; named, so the operator is not left guessing why a
                # runtime with room in it was refused.
                reasons.append('a stuck session holds this runtime')
            if n + stuck >= quota:
                reasons.append('runtime concurrency ceiling reached')

        admissions.append({
            'device_ref': dev if isinstance(dev, str) else None,
            'runtime_id': rid if isinstance(rid, str) else None,
            'managed_key': list(key) if key is not None else None,
            'decision': 'GO_ONE_BOUNDED_RUN' if not reasons else 'NO_GO',
            'reasons': sorted(set(reasons)),
        })

    return {'status': 'READ_ONLY_ASSESSMENT', 'admissions': admissions, 'writes': [],
            'note': 'Advisory only; never executes an Agent or approves account grants.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--snapshot', required=True)
    args = parser.parse_args(argv)
    try:
        with open(args.snapshot, encoding='utf-8') as stream:
            obj = json.load(stream)
    except (OSError, ValueError):
        obj = {}
    result = assess(obj)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'READ_ONLY_ASSESSMENT' else 2


if __name__ == '__main__':
    raise SystemExit(main())
