"""One-shot, read-only Fleet Next admission decisions from fresh monitoring data.

The caller gathers measurements through separately authorized device probes.
This module does not poll, SSH, restart, kill, inspect credentials or run agents.
"""
from __future__ import annotations

import argparse
import json
import math
import time

SCHEMA = 'serveros/fleet-next-health-snapshot/v1'
MAX_AGE_S = 60


def _valid_float(x):
    return type(x) in (int, float) and math.isfinite(x) and x >= 0


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
    admissions = []
    seen_runtimes = set()
    for row in runtimes:
        if not isinstance(row, dict):
            admissions.append({'runtime_id': None, 'decision': 'NO_GO',
                               'reasons': ['malformed runtime record']})
            continue
        dev, rid = row.get('device_ref'), row.get('runtime_id')
        reasons = []
        if not isinstance(dev, str) or not dev or not isinstance(rid, str) or not rid:
            reasons.append('device/runtime identity missing')
        key = (dev, rid)
        if key in seen_runtimes:
            reasons.append('duplicate runtime identity')
        seen_runtimes.add(key)
        h = host_by_id.get(dev)
        if dev in duplicate_hosts:
            reasons.append('host observation ambiguous')
        if h is None:
            reasons.append('no host observation')
        else:
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
        rts = row.get('sampled_at')
        if not _valid_float(rts) or not 0 <= now-rts <= MAX_AGE_S:
            reasons.append('runtime observation stale')
        n, quota = row.get('running_sessions'), row.get('max_sessions')
        if type(n) is not int or type(quota) is not int or n < 0 or quota <= 0:
            reasons.append('runtime concurrency budget unknown')
        elif n >= quota:
            reasons.append('runtime concurrency ceiling reached')
        admissions.append({'device_ref': dev if isinstance(dev, str) else None,
                           'runtime_id': rid if isinstance(rid, str) else None,
                           'decision': 'GO_ONE_BOUNDED_RUN' if not reasons else 'NO_GO',
                           'reasons': sorted(set(reasons))})
    # Used Swap occupancy deliberately is NOT an automatic refusal. Linux may
    # leave cold anonymous pages swapped after earlier pressure; only current
    # activity/PSI and actual per-runtime isolation drive admission.
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
