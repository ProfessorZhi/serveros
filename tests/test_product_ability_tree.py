
import pytest
import yaml

from test_ability_projection import authority, ability, manifest, projection, read, NOW, DEVICE_IDS
from test_daemon import daemon_factory, daemon_mod, a_resource


def vocabulary():
    return {'families': {'A': 'Infrastructure', 'B': 'Delivery'}, 'abilities': {
        'A7': {'name': 'Testing', 'family': 'A', 'subroles': ['execution', 'acceptance']},
        'B3': {'name': 'Requirements', 'family': 'B', 'subroles': ['criteria']}}}


def realization(kind='skill'):
    return {**ability('testing'), 'type': kind, 'product_role': {
        'primary': 'A7', 'subroles': ['execution'], 'realization_role': 'specialist',
        'cross_refs': ['B3']}}


def test_declared_empty_responsibility_and_cross_reference_do_not_disappear():
    tree = projection.product_tree(vocabulary(), [realization(), realization('plugin')])
    assert tree['responsibility_count'] == tree['returned'] == 2
    assert tree['realization_count'] == 1
    a, b = tree['nodes']
    assert a['realizations'][0]['forms'] == ['plugin', 'skill']
    assert a['unmapped_subroles'] == ['acceptance']
    assert b['realizations'] == []
    assert b['cross_references'] == ['ProfessorZhi/testing']
    assert tree['completion'] == 'NOT_ASSESSED'
    one = projection.product_tree(vocabulary(), [realization()], responsibility='B3')
    assert one['returned'] == 1 and one['nodes'][0]['id'] == 'B3'


def test_conflicting_distribution_metadata_does_not_choose_an_owner():
    other = realization('plugin')
    other['product_role']['subroles'] = ['acceptance']
    tree = projection.product_tree(vocabulary(), [realization(), other])
    assert tree['conflicts'] == ['ProfessorZhi/testing']
    assert tree['realization_count'] == 0


def test_unknown_responsibility_is_not_a_successful_empty_result():
    with pytest.raises(ValueError, match='unknown product responsibility'):
        projection.product_tree(vocabulary(), [], responsibility='B99')


def test_refresh_pins_vocabulary_and_manifest_to_same_git_revision(authority, tmp_path):
    (authority.repo / 'product-tree.yaml').write_text(yaml.safe_dump(vocabulary()))
    rev = authority.write(manifest(realization()))
    (authority.repo / 'product-tree.yaml').write_text('abilities: {}')
    state = tmp_path / 'state'
    projection.refresh(authority.repo, rev, DEVICE_IDS, state, now=NOW)
    data = read(state / 'abilities/catalogue.json')
    assert data['source_revision'] == rev
    assert set(data['product_vocabulary']['abilities']) == {'A7', 'B3'}
    assert data['product_vocabulary_error'] is None
    projection.refresh(authority.repo, 'missing-ref', DEVICE_IDS, state, now=NOW)
    stale = read(state / 'abilities/catalogue.json')
    assert stale['registry_status'] == 'STALE'
    assert stale['product_vocabulary'] == data['product_vocabulary']


def test_missing_vocabulary_does_not_erase_an_older_manifest(authority, tmp_path):
    rev = authority.write(manifest(realization()))
    state = tmp_path / 'state'
    projection.refresh(authority.repo, rev, DEVICE_IDS, state, now=NOW)
    data = read(state / 'abilities/catalogue.json')
    assert data['entries'] and data['product_vocabulary'] is None
    assert data['product_vocabulary_error']


def test_daemon_reads_full_catalogue_product_view_with_provenance(daemon_factory, tmp_path, monkeypatch):
    state = tmp_path / 'product-state'
    class Paths:
        def root(self, name, *, unbound_ok=False):
            assert name == 'state'
            return state
    monkeypatch.setattr(daemon_mod.paths_mod, 'Paths', Paths)
    data = projection.catalogue(manifest(realization()), resolved_sha='pinned-revision',
                                now=NOW, vocabulary=vocabulary())
    projection.write_atomic(state / 'abilities/catalogue.json', data)
    daemon = daemon_factory(a_resource('product-tree'))
    result = daemon.handle({'method': 'ability.tree', 'params': {
        'view': 'product', 'responsibility': 'B3'}})['result']
    assert result['source_revision'] == 'pinned-revision'
    assert result['returned'] == 1
    assert result['nodes'][0]['cross_references'] == ['ProfessorZhi/testing']
    del data['product_vocabulary']
    projection.write_atomic(state / 'abilities/catalogue.json', data)
    result = daemon.handle({'method': 'ability.tree', 'params': {'view': 'product'}})['result']
    assert result['nodes'] is None and 'Refresh' in result['note']


def test_cli_product_filter_reaches_daemon_and_renders_only_selected_role(monkeypatch, capsys):
    from test_serverosctl import cli_mod
    calls = []
    def ask(method, params):
        calls.append((method, params))
        return projection.product_tree(vocabulary(), [realization()], responsibility=params['responsibility'])
    monkeypatch.setattr(cli_mod, 'ask_daemon', ask)
    assert cli_mod.main(['ability', 'tree', '--view', 'product', '--responsibility', 'B3']) == 0
    output = capsys.readouterr().out
    assert calls == [('ability.tree', {'view': 'product', 'responsibility': 'B3'})]
    assert 'Requirements' in output and 'Testing:' not in output
    assert 'NOT_ASSESSED' in output


def test_mcp_product_arguments_are_forwarded_without_another_tree_owner():
    from test_node_mcp import FakeDaemon, NodeMcpServer, rpc
    daemon = FakeDaemon(results={'ability.tree': {'basis': 'product_role.primary', 'nodes': []}})
    result = rpc(NodeMcpServer(daemon), 'tools/call', name='ability.tree',
                 arguments={'view': 'product', 'responsibility': 'A7'})['result']
    assert result['isError'] is False
    assert daemon.calls == [('ability.tree', {'view': 'product', 'responsibility': 'A7'})]


def test_new_cli_does_not_silently_accept_old_daemon_domain_tree(monkeypatch, capsys):
    from test_serverosctl import cli_mod
    monkeypatch.setattr(cli_mod, 'ask_daemon', lambda *_: {'basis': 'domain.primary', 'nodes': []})
    assert cli_mod.main(['ability', 'tree', '--view', 'product', '--json']) == 2
    assert 'PRODUCT_VIEW_UNSUPPORTED' in capsys.readouterr().out
