"""Validation and recoverable publication of metadata-only inventories."""
from __future__ import annotations
import base64
from contextlib import contextmanager, ExitStack, nullcontext
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

_OBJECT_SCHEMA = json.loads(
    (Path(__file__).with_name('schemas') / 'federation-object.schema.json').read_text(encoding='utf-8')
)
_JOURNAL_VERSION = 1
_HASH_RE = re.compile(r'^[0-9a-f]{64}$')
_NONCE_RE = re.compile(r'^[0-9a-f]{32}$')

# The allowed domains are NOT restated here — they are read from the object
# schema, which has declared them all along. Two lists of one thing is how the
# CLI and the schema came to disagree: the enum was enforced against each row's
# injected `domain` field, while the `domain` ARGUMENT was never checked.
_DOMAIN_ENUM = tuple(_OBJECT_SCHEMA['properties']['domain']['enum'])


def _validate_domain(domain):
    """Reject a domain outside the schema enum before any other work.

    This is called at the ENTRY of every federation operation, not only where a
    row happens to be validated. The earlier placement — enum-checked only
    inside `_validate_inventory_row` — meant a zero-row source skipped
    validation entirely and published a projection under an invented domain.
    An empty inventory is still a claim about a domain.
    """
    if domain not in _DOMAIN_ENUM:
        raise ValueError(
            f"unknown federation domain {domain!r}; allowed: {', '.join(_DOMAIN_ENUM)}"
        )


def _assert_domain_scope(collections, domain):
    """Every row must belong to the domain the caller says it is checking.

    This is what makes `domain` load-bearing on the check path. Previously the
    argument was accepted, passed nowhere, and discarded: `check` produced a
    byte-identical report for `feishu`, `github` and `bogusdomain` alike, so the
    command could not fail on a domain that does not exist.

    A row carrying a foreign domain is a caller error, not a drift finding —
    silently comparing it would report placement drift between two objects that
    were never in the same namespace.
    """
    for rows in collections:
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('inventory row must be an object')
            found = row.get('domain')
            if found is not None and found != domain:
                raise ValueError(
                    f"row for object {row.get('object_id')!r} declares domain "
                    f"{found!r} but this operation is scoped to {domain!r}"
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
    _validate_domain(domain)
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
    check_inventory(rows, rows, domain=domain)
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

def _write_transaction_file(directory, name, payload):
    path=directory / name
    flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor=os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path

def _canonical_target(target):
    target=Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    parent=target.parent.resolve(strict=True)
    canonical=parent / target.name
    if canonical.is_symlink():
        raise ValueError('publication target must not be a symlink')
    return canonical

def _read_regular_file(path):
    path=Path(path)
    try:
        before=os.lstat(path)
    except FileNotFoundError as exc:
        raise ValueError(f'publication transaction file is missing: {path.name}') from exc
    if stat.S_ISLNK(before.st_mode):
        raise ValueError(f'publication transaction file is a symlink: {path.name}')
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f'publication transaction file is not regular: {path.name}')
    flags=os.O_RDONLY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        descriptor=os.open(path, flags)
    except FileNotFoundError as exc:
        raise ValueError(f'publication transaction file is missing: {path.name}') from exc
    try:
        details=os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ValueError(f'publication transaction file is not regular: {path.name}')
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor=None
            return stream.read()
    finally:
        if descriptor is not None:
            os.close(descriptor)

def _file_hash(path):
    return hashlib.sha256(_read_regular_file(path)).hexdigest()

def _capture_metadata(path):
    details=os.stat(path, follow_symlinks=False)
    attributes={}
    if hasattr(os, 'listxattr'):
        for name in os.listxattr(path, follow_symlinks=False):
            value=os.getxattr(path, name, follow_symlinks=False)
            attributes[name]=base64.b64encode(value).decode('ascii')
    return {
        'mode':stat.S_IMODE(details.st_mode),
        'atime_ns':details.st_atime_ns,
        'mtime_ns':details.st_mtime_ns,
        'uid':details.st_uid,
        'gid':details.st_gid,
        'xattrs':attributes,
    }

def _apply_metadata(path, metadata):
    details=os.stat(path, follow_symlinks=False)
    if (details.st_uid, details.st_gid) != (metadata['uid'], metadata['gid']):
        os.chown(path, metadata['uid'], metadata['gid'], follow_symlinks=False)
    os.chmod(path, metadata['mode'], follow_symlinks=False)
    if hasattr(os, 'listxattr'):
        expected=metadata['xattrs']
        for name in os.listxattr(path, follow_symlinks=False):
            if name not in expected:
                os.removexattr(path, name, follow_symlinks=False)
        for name, value in expected.items():
            os.setxattr(path, name, base64.b64decode(value, validate=True), follow_symlinks=False)
    os.utime(path, ns=(metadata['atime_ns'], metadata['mtime_ns']), follow_symlinks=False)

def _validate_metadata(metadata):
    required={'mode','atime_ns','mtime_ns','uid','gid','xattrs'}
    if not isinstance(metadata, dict) or set(metadata) != required:
        raise ValueError('publication journal has invalid old metadata')
    if any(not isinstance(metadata[name], int) for name in required - {'xattrs'}):
        raise ValueError('publication journal has invalid old metadata values')
    if not isinstance(metadata['xattrs'], dict):
        raise ValueError('publication journal has invalid xattrs')
    try:
        for name, value in metadata['xattrs'].items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise ValueError
            base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError('publication journal has invalid xattrs') from exc

def _validate_backup_metadata(path, metadata):
    details=os.stat(path, follow_symlinks=False)
    if stat.S_IMODE(details.st_mode) != metadata['mode']:
        raise ValueError('publication recovery backup mode does not match journal')
    if details.st_mtime_ns != metadata['mtime_ns']:
        raise ValueError('publication recovery backup mtime does not match journal')
    if (details.st_uid, details.st_gid) != (metadata['uid'], metadata['gid']):
        raise ValueError('publication recovery backup owner does not match journal')
    if hasattr(os, 'listxattr'):
        actual={
            name:base64.b64encode(os.getxattr(path, name, follow_symlinks=False)).decode('ascii')
            for name in os.listxattr(path, follow_symlinks=False)
        }
        if actual != metadata['xattrs']:
            raise ValueError('publication recovery backup xattrs do not match journal')

def _sync_directory(directory):
    descriptor=os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

@contextmanager
def _publication_lock(target):
    lock_path = target.with_name(f'.{target.name}.publish.lock')
    flags=os.O_RDWR | os.O_CREAT
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        descriptor=os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ValueError('publication lock must be a regular non-symlink file') from exc
    details=os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode):
        os.close(descriptor)
        raise ValueError('publication lock must be a regular non-symlink file')
    with os.fdopen(descriptor, 'a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield

def _journal_path(target):
    return target.with_name(f'.{target.name}.publish.json')

def _transaction_names(target, nonce):
    return (
        f'.{target.name}.publish.{nonce}.stage',
        f'.{target.name}.publish.{nonce}.backup',
    )

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

def _remove_pending(journal, stage, backup):
    journal.unlink(missing_ok=True)
    if stage is not None:
        stage.unlink(missing_ok=True)
    if backup is not None:
        backup.unlink(missing_ok=True)
    _sync_directory(journal.parent)

def _load_pending(target, domain):
    journal=_journal_path(target)
    if journal.is_symlink():
        raise ValueError('publication journal must not be a symlink')
    try:
        pending=json.loads(_read_regular_file(journal))
    except json.JSONDecodeError as exc:
        raise ValueError('publication journal is not valid JSON') from exc
    required={
        'version','domain','target','evidence_run','nonce','stage','backup',
        'had_current','old_sha256','new_sha256','old_metadata',
    }
    if not isinstance(pending, dict) or set(pending) != required:
        raise ValueError('publication journal has invalid fields')
    if pending['version'] != _JOURNAL_VERSION:
        raise ValueError('publication journal has unsupported version')
    if pending['domain'] != domain or not isinstance(pending['evidence_run'], str) or not pending['evidence_run']:
        raise ValueError('publication journal identity does not match request')
    if pending['target'] != str(target):
        raise ValueError('publication journal target does not match request')
    nonce=pending['nonce']
    if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
        raise ValueError('publication journal has invalid nonce')
    stage_name, backup_name=_transaction_names(target, nonce)
    if pending['stage'] != stage_name:
        raise ValueError('publication journal has invalid stage path')
    had_current=pending['had_current']
    if not isinstance(had_current, bool):
        raise ValueError('publication journal has invalid current state')
    expected_backup=backup_name if had_current else None
    if pending['backup'] != expected_backup:
        raise ValueError('publication journal has invalid backup path')
    if not isinstance(pending['new_sha256'], str) or not _HASH_RE.fullmatch(pending['new_sha256']):
        raise ValueError('publication journal has invalid new hash')
    if had_current:
        if not isinstance(pending['old_sha256'], str) or not _HASH_RE.fullmatch(pending['old_sha256']):
            raise ValueError('publication journal has invalid old hash')
        _validate_metadata(pending['old_metadata'])
    elif pending['old_sha256'] is not None or pending['old_metadata'] is not None:
        raise ValueError('publication journal has unexpected old state')
    stage=target.parent / stage_name
    backup=target.parent / backup_name if had_current else None
    for path in (stage, backup):
        if path is not None and path.is_symlink():
            raise ValueError('publication transaction paths must not be symlinks')
    if stage.exists() and _file_hash(stage) != pending['new_sha256']:
        raise ValueError('publication stage hash does not match journal')
    if backup is not None:
        if _file_hash(backup) != pending['old_sha256']:
            raise ValueError('publication recovery backup hash does not match journal')
        _validate_backup_metadata(backup, pending['old_metadata'])
    if target.exists():
        current_hash=_file_hash(target)
        allowed={pending['new_sha256']}
        if pending['old_sha256'] is not None:
            allowed.add(pending['old_sha256'])
        if current_hash not in allowed:
            raise ValueError('current projection hash does not match publication journal')
    return pending, stage, backup

def _restore_pending(target, journal, pending, stage, backup):
    if pending.get('had_current'):
        if backup is None:
            raise ValueError('publication recovery backup is missing')
        # Hashing during validation may update atime; restore all recorded metadata last.
        _apply_metadata(backup, pending['old_metadata'])
        backup.replace(target)
    else:
        if target.exists():
            if _file_hash(target) != pending['new_sha256']:
                raise ValueError('refusing to remove an unrecognized current projection')
            target.unlink()
    _sync_directory(target.parent)
    _remove_pending(journal, stage, backup)

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

def check_inventory(observed, desired, *, domain=None, evidence_run=''):
    if domain is not None:
        _validate_domain(domain)
        _assert_domain_scope((observed, desired), domain)
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

def inventory(domain, source):
    """Validate and hash an inventory without publishing a current projection."""
    rows, _, digest = _prepare_inventory(domain, source)
    return {'domain':domain,'objects':len(rows),'sha256':digest}

def publish_inventory(domain, source, target, evidence_run, receipt_recorder, receipt_lookup=None):
    # Actual Work producer: Work BEGIN IMMEDIATE -> publication flock. Keep
    # the flock through SQL commit and only then delete the durable journal.
    # Generic recorders retain the existing standalone publication protocol.
    from .store import ServerOSStore
    store = getattr(receipt_recorder, "__self__", None)
    if isinstance(store, ServerOSStore) and getattr(receipt_recorder, "__func__", None) is ServerOSStore.record_publication_receipt:
        cleanup = []
        with ExitStack() as locks:
            with store.transaction(immediate=True) as conn:
                item, _ = store._resolve_evidence_run_tx(conn, evidence_run)
                store._refuse_managed_effect(conn, item_ref=item)
                prepared = _prepare_inventory(domain, source)
                target = _canonical_target(target)
                locks.enter_context(_publication_lock(target))
                # Recovery belongs to the journal's original Item, which may
                # differ from this incoming request and may have enrolled since
                # the publisher died. Check BOTH scopes before any restoration,
                # receipt recording, cleanup or new projection staging.
                if os.path.lexists(_journal_path(target)):
                    pending, _, _ = _load_pending(target, domain)
                    pending_item, _ = store._resolve_evidence_run_tx(conn, pending["evidence_run"])
                    store._refuse_managed_effect(conn, item_ref=pending_item)
                    if pending["evidence_run"] != evidence_run or pending["new_sha256"] != prepared[2]:
                        raise ValueError('reconcile the pending publication identity before publishing a different attempt')
                def record(*args):
                    return store._record_publication_receipt_tx(conn, *args)
                def lookup(domain, locator, evidence):
                    row = conn.execute("SELECT * FROM publication_receipts WHERE domain=? AND locator=? AND evidence_run=?", (domain, locator, evidence)).fetchone()
                    return dict(row) if row else None
                result = _publish_inventory(domain, source, target, evidence_run, record, lookup, locked=True, cleanup=cleanup, prepared=prepared)
            for pending in cleanup:
                _remove_pending(*pending)
        return result
    return _publish_inventory(domain, source, target, evidence_run, receipt_recorder, receipt_lookup)


def _publish_inventory(domain, source, target, evidence_run, receipt_recorder, receipt_lookup=None, *, locked=False, cleanup=None, prepared=None):
    rows, payload, digest = prepared if prepared is not None else _prepare_inventory(domain, source)
    target=_canonical_target(target)
    with (nullcontext() if locked else _publication_lock(target)):
        journal=_journal_path(target)
        if os.path.lexists(journal):
            pending, pending_stage, pending_backup=_load_pending(target, domain)
            same_identity=(
                pending.get('domain') == domain
                and pending.get('evidence_run') == evidence_run
                and pending.get('target') == str(target)
            )
            same_attempt=(
                same_identity
                and pending.get('new_sha256') == digest
            )
            target_matches=(
                target.is_file()
                and _file_hash(target) == pending.get('new_sha256')
            )
            existing=(
                receipt_lookup(
                    pending.get('domain'), pending.get('target'), pending.get('evidence_run'),
                )
                if receipt_lookup else None
            )
            if existing is not None:
                if existing.get('sha256') != pending.get('new_sha256') or not target_matches:
                    raise RuntimeError('committed publication receipt does not match pending current projection')
                receipt=existing
            elif target_matches:
                try:
                    receipt=receipt_recorder(
                        pending.get('domain'), pending.get('target'),
                        pending.get('new_sha256'), pending.get('evidence_run'),
                    )
                except Exception:
                    _restore_pending(target, journal, pending, pending_stage, pending_backup)
                    raise
            else:
                _restore_pending(target, journal, pending, pending_stage, pending_backup)
                receipt=None
            if receipt is not None:
                if cleanup is not None:
                    if not same_attempt:
                        raise ValueError('reconcile the pending publication identity before publishing a different attempt')
                    cleanup.append((journal, pending_stage, pending_backup))
                else:
                    _remove_pending(journal, pending_stage, pending_backup)
                if same_attempt:
                    return {
                        'domain':domain, 'objects':len(rows), 'locator':str(target),
                        'sha256':digest, 'evidence_run':evidence_run, 'receipt':receipt,
                    }
                if same_identity:
                    raise ValueError('publication identity already has a different projection')

        nonce=secrets.token_hex(16)
        stage_name, backup_name=_transaction_names(target, nonce)
        staged=_write_transaction_file(target.parent, stage_name, payload)
        backup=None
        old_sha256=None
        old_metadata=None
        try:
            if target.exists():
                if target.is_symlink():
                    raise ValueError('publication target must not be a symlink')
                old_metadata=_capture_metadata(target)
                previous=_read_regular_file(target)
                old_sha256=hashlib.sha256(previous).hexdigest()
                backup=_write_transaction_file(target.parent, backup_name, previous)
                _apply_metadata(backup, old_metadata)
            pending={
                'version':_JOURNAL_VERSION, 'domain':domain, 'target':str(target),
                'evidence_run':evidence_run, 'nonce':nonce, 'stage':stage_name,
                'backup':backup_name if backup is not None else None,
                'had_current':backup is not None, 'old_sha256':old_sha256,
                'new_sha256':digest, 'old_metadata':old_metadata,
            }
            journal=_write_journal(target, pending)
            staged.replace(target)
            _sync_directory(target.parent)
            try:
                receipt=receipt_recorder(domain, str(target), digest, evidence_run)
            except Exception:
                pending, pending_stage, pending_backup=_load_pending(target, domain)
                _restore_pending(target, journal, pending, pending_stage, pending_backup)
                raise
        except BaseException:
            staged.unlink(missing_ok=True)
            if not _journal_path(target).exists() and backup is not None:
                backup.unlink(missing_ok=True)
            raise
        if cleanup is not None:
            cleanup.append((journal, staged, backup))
        else:
            _remove_pending(journal, staged, backup)
    return {
        'domain':domain, 'objects':len(rows), 'locator':str(target),
        'sha256':digest, 'evidence_run':evidence_run, 'receipt':receipt,
    }

def proposal(drifts):
    return sorted((dict(d) for d in drifts), key=lambda d: (str(d.get('risk')), str(d.get('proposed_action')), str(d.get('object_id'))))
