"""Pure validation of metadata-only federated inventories."""
from __future__ import annotations
import json
from pathlib import Path

def check_inventory(observed, desired):
    desired_ids={x.get('object_id') for x in desired}
    seen=set(); out=[]
    if len(desired_ids) != len(desired): raise ValueError('duplicate desired object id')
    for obj in observed:
        oid=obj.get('object_id')
        if oid and oid in seen: raise ValueError(f"duplicate object id: {oid}")
        if oid: seen.add(oid)
        if obj.get('error') in {'AUTH_EXPIRED','PERMISSION_DENIED','RATE_LIMIT_TRANSIENT'}:
            out.append({'domain':obj.get('domain'),'object_id':oid,'observed':obj,'desired':None,'class':obj['error'],'risk':'high','proposed_action':'reauthenticate or retry','approval':'explicit','evidence_run':obj.get('evidence_run')}); continue
        desired_obj=next((x for x in desired if x.get('object_id')==oid),None)
        if desired_obj is not None and obj.get('parent') != desired_obj.get('parent'):
            out.append({'domain':obj.get('domain'),'object_id':oid,'observed':obj,'desired':desired_obj,'class':'PLACEMENT_DRIFT','risk':'medium','proposed_action':'propose placement','approval':'proposal','evidence_run':obj.get('evidence_run')})
        if oid and oid not in desired_ids:
            out.append({'domain':obj.get('domain'),'object_id':oid,'observed':obj,'desired':None,'class':'UNEXPECTED_OBJECT','risk':'medium','proposed_action':'review object','approval':'proposal','evidence_run':obj.get('evidence_run')})
    observed_ids={x.get('object_id') for x in observed}
    for obj in desired:
        if obj.get('object_id') not in observed_ids:
            out.append({'domain':obj.get('domain'),'object_id':obj.get('object_id'),'observed':None,'desired':obj,'class':'MISSING_OBJECT','risk':'medium','proposed_action':'create or reconcile object','approval':'proposal','evidence_run':None})
    return out

def inventory(domain, source, target):
    rows=[dict(json.loads(line), domain=domain) for line in Path(source).read_text(encoding='utf-8').splitlines() if line.strip()]
    if any(r.get('error') or r.get('validation_error') for r in rows): raise ValueError('inventory contains failure envelope')
    check_inventory(rows, rows)
    target=Path(target); target.parent.mkdir(parents=True, exist_ok=True)
    tmp=target.with_suffix(target.suffix+'.tmp'); tmp.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8'); tmp.replace(target)
    return {'domain':domain,'objects':len(rows),'locator':str(target)}

def proposal(drifts):
    return sorted((dict(d, action=d.get('proposed_action')) for d in drifts), key=lambda d: (str(d.get('risk')), str(d.get('action')), str(d.get('object_id'))))
