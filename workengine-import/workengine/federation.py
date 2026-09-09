"""Validation and recoverable publication of metadata-only inventories."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

_OBJECT_SCHEMA = json.loads(
    (Path(__file__).with_name('schemas') / 'federation-object.schema.json').read_text(encoding='utf-8')
)

def _validate_type(value, expected):
    choices = expected if isinstance(expected, list) else [expected]
    return any(
        (choice == 'string' and isinstance(value, str))
        or (choice == 'object' and isinstance(value, dict))
        or (choice == 'null' and value is None)
        for choice in choices
    )

def _validate_inventory_row(row, row_number):
    if not isinstance(row, dict):
        raise ValueError(f'inventory row {row_number} must be an object')
    missing = sorted(set(_OBJECT_SCHEMA['required']) - set(row))
    if missing:
        raise ValueError(f'inventory row {row_number} missing required fields: {", ".join(missing)}')
    properties = _OBJECT_SCHEMA['properties']
    extras = sorted(set(row) - set(properties))
    if extras and _OBJECT_SCHEMA.get('additionalProperties') is False:
        raise ValueError(f'inventory row {row_number} has unsupported fields: {", ".join(extras)}')
    for name, value in row.items():
        rule = properties[name]
        if 'type' in rule and not _validate_type(value, rule['type']):
            raise ValueError(f'inventory row {row_number} field {name} has invalid type')
        if 'enum' in rule and value not in rule['enum']:
            raise ValueError(f'inventory row {row_number} field {name} is outside the allowed values')
        if isinstance(value, str) and len(value) < rule.get('minLength', 0):
            raise ValueError(f'inventory row {row_number} field {name} is empty')
        if rule.get('format') == 'date-time' and value is not None:
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError as exc:
                raise ValueError(f'inventory row {row_number} field {name} is not a date-time') from exc
            if parsed.tzinfo is None:
                raise ValueError(f'inventory row {row_number} field {name} must include a timezone')

def _prepare_inventory(domain, source):
    rows=[]
    for number, line in enumerate(Path(source).read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        raw=json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError(f'inventory row {number} must be an object')
        rows.append(dict(raw, domain=domain))
    if any(r.get('error') or r.get('validation_error') for r in rows): raise ValueError('inventory contains failure envelope')
    for number, row in enumerate(rows, 1):
        _validate_inventory_row(row, number)
    check_inventory(rows, rows)
    payload=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows).encode()
    return rows, payload, hashlib.sha256(payload).hexdigest()

def _write_staged(directory, prefix, payload):
    fd, name = tempfile.mkstemp(prefix=prefix, dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)

def _sync_directory(directory):
    descriptor=os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

@contextmanager
def _publication_lock(target):
    lock_path = target.with_name(f'.{target.name}.publish.lock')
    with lock_path.open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield

def _publish(target, payload):
    target=Path(target); target.parent.mkdir(parents=True, exist_ok=True)
    staged=_write_staged(target.parent, f'.{target.name}.stage.', payload)
    try:
        staged.replace(target)
        _sync_directory(target.parent)
    finally:
        staged.unlink(missing_ok=True)

def _journal_path(target):
    return target.with_name(f'.{target.name}.publish.json')

def _write_journal(target, record):
    journal=_journal_path(target)
    staged=_write_staged(
        target.parent, f'.{target.name}.journal.',
        (json.dumps(record, sort_keys=True)+'\n').encode(),
    )
    try:
        staged.replace(journal)
        _sync_directory(target.parent)
    finally:
        staged.unlink(missing_ok=True)
    return journal

def _remove_pending(journal, backup):
    journal.unlink(missing_ok=True)
    if backup is not None:
        backup.unlink(missing_ok=True)
    _sync_directory(journal.parent)

def _restore_pending(target, journal, pending):
    backup=Path(pending['backup']) if pending.get('backup') else None
    if pending.get('had_current'):
        if backup is None or not backup.is_file():
            raise RuntimeError('publication recovery backup is missing')
        backup.replace(target)
    else:
        target.unlink(missing_ok=True)
    _sync_directory(target.parent)
    _remove_pending(journal, backup)

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
    rows, payload, digest = _prepare_inventory(domain, source)
    target=Path(target)
    _publish(target, payload)
    return {'domain':domain,'objects':len(rows),'locator':str(target),'sha256':digest}

def publish_inventory(domain, source, target, evidence_run, receipt_recorder, receipt_lookup=None):
    rows, payload, digest = _prepare_inventory(domain, source)
    target=Path(target); target.parent.mkdir(parents=True, exist_ok=True)
    with _publication_lock(target):
        journal=_journal_path(target)
        if journal.exists():
            pending=json.loads(journal.read_text(encoding='utf-8'))
            same_identity=(
                pending.get('domain') == domain
                and pending.get('evidence_run') == evidence_run
                and pending.get('locator') == str(target)
            )
            same_attempt=(
                same_identity
                and pending.get('sha256') == digest
            )
            target_matches=(
                target.is_file()
                and hashlib.sha256(target.read_bytes()).hexdigest() == pending.get('sha256')
            )
            existing=(
                receipt_lookup(
                    pending.get('domain'), pending.get('locator'), pending.get('evidence_run'),
                )
                if receipt_lookup else None
            )
            if existing is not None:
                if existing.get('sha256') != pending.get('sha256') or not target_matches:
                    raise RuntimeError('committed publication receipt does not match pending current projection')
                receipt=existing
            elif target_matches:
                try:
                    receipt=receipt_recorder(
                        pending.get('domain'), pending.get('locator'),
                        pending.get('sha256'), pending.get('evidence_run'),
                    )
                except Exception:
                    _restore_pending(target, journal, pending)
                    raise
            else:
                _restore_pending(target, journal, pending)
                receipt=None
            if receipt is not None:
                backup=Path(pending['backup']) if pending.get('backup') else None
                _remove_pending(journal, backup)
                if same_attempt:
                    return {
                        'domain':domain, 'objects':len(rows), 'locator':str(target),
                        'sha256':digest, 'evidence_run':evidence_run, 'receipt':receipt,
                    }
                if same_identity:
                    raise ValueError('publication identity already has a different projection')

        staged=_write_staged(target.parent, f'.{target.name}.stage.', payload)
        backup=None
        if target.exists():
            backup=_write_staged(target.parent, f'.{target.name}.backup.', target.read_bytes())
        pending={
            'domain':domain, 'locator':str(target), 'sha256':digest,
            'evidence_run':evidence_run, 'had_current':backup is not None,
            'backup':str(backup) if backup is not None else None,
        }
        try:
            journal=_write_journal(target, pending)
            staged.replace(target)
            _sync_directory(target.parent)
            try:
                receipt=receipt_recorder(domain, str(target), digest, evidence_run)
            except Exception:
                _restore_pending(target, journal, pending)
                raise
        except BaseException:
            staged.unlink(missing_ok=True)
            if not _journal_path(target).exists() and backup is not None:
                backup.unlink(missing_ok=True)
            raise
        _remove_pending(journal, backup)
    return {
        'domain':domain, 'objects':len(rows), 'locator':str(target),
        'sha256':digest, 'evidence_run':evidence_run, 'receipt':receipt,
    }

def proposal(drifts):
    return sorted((dict(d) for d in drifts), key=lambda d: (str(d.get('risk')), str(d.get('proposed_action')), str(d.get('object_id'))))
