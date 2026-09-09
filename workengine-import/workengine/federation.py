"""Pure validation of metadata-only federated inventories."""
from __future__ import annotations

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
