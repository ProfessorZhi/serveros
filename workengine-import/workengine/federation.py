"""Pure validation of metadata-only federated inventories."""
from __future__ import annotations
import json
from pathlib import Path

def check_inventory(observed, desired):
    desired_ids={x.get('object_id') for x in desired}
    seen=set(); out=[]
    for obj in observed:
        oid=obj.get('object_id')
        if oid and oid in seen: raise ValueError(f"duplicate object id: {oid}")
        if oid: seen.add(oid)
        if obj.get('error') in {'AUTH_EXPIRED','PERMISSION_DENIED','RATE_LIMIT_TRANSIENT'}:
            out.append({'domain':obj.get('domain'),'object_id':oid,'observed':obj,'desired':None,'class':obj['error'],'risk':'high','proposed_action':'reauthenticate or retry','approval':'explicit','evidence_run':obj.get('evidence_run')}); continue
        if obj.get('parent_id') and obj['parent_id'] not in desired_ids:
            out.append({'domain':obj.get('domain'),'object_id':oid,'observed':obj,'desired':None,'class':'PLACEMENT_DRIFT','risk':'medium','proposed_action':'propose placement','approval':'proposal','evidence_run':obj.get('evidence_run')})
    return out

def inventory(domain, source, target):
    rows=[dict(json.loads(line), domain=domain) for line in Path(source).read_text(encoding='utf-8').splitlines() if line.strip()]
    check_inventory(rows, rows)
    target=Path(target); target.parent.mkdir(parents=True, exist_ok=True)
    tmp=target.with_suffix(target.suffix+'.tmp'); tmp.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8'); tmp.replace(target)
    return {'domain':domain,'objects':len(rows),'locator':str(target)}

def proposal(drifts):
    return [dict(d, action=d.get('proposed_action')) for d in drifts]
