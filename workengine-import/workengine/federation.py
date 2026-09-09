"""Pure validation of metadata-only federated inventories."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

def _drift(domain, object_id, observed, desired, drift_class, risk, proposed_action, approval, evidence_run):
    return {
        'domain': str(domain or ''),
        'object_id': str(object_id or ''),
        'observed': observed or {},
        'desired': desired or {},
        'class': drift_class,
        'risk': risk,
        'proposed_action': proposed_action,
        'approval': approval,
        'evidence_run': str(evidence_run or ''),
    }

def check_inventory(observed, desired, *, evidence_run=''):
    desired_ids={x.get('object_id') for x in desired}
    seen=set(); out=[]
    if len(desired_ids) != len(desired): raise ValueError('duplicate desired object id')
    for obj in observed:
        oid=obj.get('object_id')
        if oid and oid in seen: raise ValueError(f"duplicate object id: {oid}")
        if oid: seen.add(oid)
        if obj.get('error') in {'AUTH_EXPIRED','PERMISSION_DENIED','RATE_LIMIT_TRANSIENT'}:
            out.append(_drift(obj.get('domain'), oid, obj, {}, obj['error'], 'high', 'reauthenticate or retry', 'explicit', evidence_run)); continue
        desired_obj=next((x for x in desired if x.get('object_id')==oid),None)
        if desired_obj is not None and obj.get('parent') != desired_obj.get('parent'):
            out.append(_drift(obj.get('domain'), oid, obj, desired_obj, 'PLACEMENT_DRIFT', 'medium', 'propose placement', 'proposal-only', evidence_run))
        if oid and oid not in desired_ids:
            out.append(_drift(obj.get('domain'), oid, obj, {}, 'UNEXPECTED_OBJECT', 'medium', 'review object', 'proposal-only', evidence_run))
    observed_ids={x.get('object_id') for x in observed}
    for obj in desired:
        if obj.get('object_id') not in observed_ids:
            out.append(_drift(obj.get('domain'), obj.get('object_id'), {}, obj, 'MISSING_OBJECT', 'medium', 'create or reconcile object', 'proposal-only', evidence_run))
    return out

def inventory(domain, source, target):
    rows=[dict(json.loads(line), domain=domain) for line in Path(source).read_text(encoding='utf-8').splitlines() if line.strip()]
    if any(r.get('error') or r.get('validation_error') for r in rows): raise ValueError('inventory contains failure envelope')
    check_inventory(rows, rows)
    target=Path(target); target.parent.mkdir(parents=True, exist_ok=True)
    tmp=target.with_suffix(target.suffix+'.tmp'); tmp.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8'); tmp.replace(target)
    return {'domain':domain,'objects':len(rows),'locator':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}

def proposal(drifts):
    return sorted((dict(d) for d in drifts), key=lambda d: (str(d.get('risk')), str(d.get('proposed_action')), str(d.get('object_id'))))
