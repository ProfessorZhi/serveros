import hashlib
import json
import sqlite3

import pytest

from workengine.archive_restore import restore_items
from workengine.board import render
from workengine.model import WorkItem, Activity
from workengine.store import ServerOSStore


def fixture(tmp_path):
    old = ServerOSStore(tmp_path / 'old')
    old.register_project('litellm', 'LIT')
    old.create_item(WorkItem('LIT-0104', 'litellm', 'preserved work', 'investigation', '/old/card.yaml'))
    old.add_activity(Activity('LIT-0104', 'inspect', 'investigation'))
    old.begin_run('LIT-0104', 'inspect', 'archived-worker', 'once', tmp_path / 'old-artifacts')
    archive = tmp_path / 'archive.db'
    with old.connection() as source, sqlite3.connect(archive) as copy:
        source.backup(copy)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    live = ServerOSStore(tmp_path / 'live')
    live.register_project('litellm', 'LIT')
    live.create_item(WorkItem('LIT-0220', 'litellm', 'new live work', 'bug', '/new/card.yaml'))
    with live.transaction(immediate=True) as c:
        c.execute("UPDATE id_sequences SET next_value=221 WHERE scope='litellm' AND kind='item'")
    return archive, digest, live


def test_additive_recovery_preserves_live_items_history_and_high_water_mark(tmp_path):
    archive, digest, live = fixture(tmp_path)
    preview = restore_items(live, archive, ['litellm'])
    assert preview['restored'] == ['LIT-0104']
    assert [i.id for i in live.list_items('litellm')] == ['LIT-0220']
    result = restore_items(live, archive, ['litellm'], expected_sha256=digest, apply=True)
    assert result['restored'] == ['LIT-0104'] and result['copied_rows']['runs'] == 1
    assert result['historical_nonterminal_runs'][0]['state'] == 'ALLOCATED'
    assert not result['leases_imported']
    assert {i.id for i in live.list_items('litellm')} == {'LIT-0104', 'LIT-0220'}
    replay = restore_items(live, archive, ['litellm'], expected_sha256=digest, apply=True)
    assert replay['restored'] == [] and replay['already_restored'] == ['LIT-0104']
    assert live.allocate_item_id('litellm') == 'LIT-0221'
    with live.connection() as c:
        assert c.execute('SELECT count(*) FROM runs').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM leases').fetchone()[0] == 0
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest


def test_wrong_archive_or_existing_item_refuses_without_partial_import(tmp_path):
    archive, digest, live = fixture(tmp_path)
    with pytest.raises(ValueError, match='SHA256'):
        restore_items(live, archive, ['litellm'], expected_sha256='0'*64, apply=True)
    live.create_item(WorkItem('LIT-0104', 'litellm', 'do not overwrite', 'test', '/live/card.yaml'))
    with pytest.raises(ValueError, match='overwritten'):
        restore_items(live, archive, ['litellm'], expected_sha256=digest, apply=True)
    assert live.get_item('LIT-0104').title == 'do not overwrite'


def test_populated_board_cannot_drop_old_items_when_new_items_exist(tmp_path):
    _, _, live = fixture(tmp_path)
    out = tmp_path / 'board';out.mkdir()
    before = json.dumps({'items': [{'id': 'LIT-0104'}]})
    (out / 'current.json').write_text(before)
    (out / 'current.md').write_text('preserve historical board')
    for override in [False, True]:
        with pytest.raises(ValueError, match='LIT-0104'):
            render(live, 'litellm', out, allow_empty=override)
        assert (out / 'current.json').read_text() == before
        assert (out / 'current.md').read_text() == 'preserve historical board'


def test_unreadable_board_does_not_get_treated_as_empty(tmp_path):
    _, _, live = fixture(tmp_path)
    out = tmp_path / 'board';out.mkdir()
    (out / 'current.json').write_text('{broken')
    with pytest.raises(ValueError, match='unreadable'):
        render(live, 'litellm', out)
    assert (out / 'current.json').read_text() == '{broken'
