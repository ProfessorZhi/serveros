"""Pure off-host health admission tests, never launch a process."""
import copy
import unittest
from fleet_mcp.health_admission import assess, SCHEMA


def snapshot():
    return {
        'schema': SCHEMA,
        'hosts': [{
            'device_ref': 'device:tokyo', 'sampled_at': 100, 'source': 'LIVE',
            'reachable': True, 'memory_psi_full_avg10': 0, 'swap_out_mib_s': 0,
            'blocked_tasks': 0, 'swap_used_percent': 100.0,
        }],
        'runtimes': [{
            'device_ref': 'device:tokyo', 'runtime_id': 'claude-vince-1',
            'owner_approved': True, 'source': 'LIVE', 'reachable': True, 'sampled_at': 100,
            'running_sessions': 0, 'max_sessions': 1,
        }],
    }


class HealthAdmission(unittest.TestCase):
    def test_full_swap_idle_reclaim_does_not_blanket_block(self):
        self.assertEqual(assess(snapshot(), now=110)['admissions'][0]['decision'],
                         'GO_ONE_BOUNDED_RUN')

    def test_memory_psi_full_blocks(self):
        row = snapshot()
        row['hosts'][0]['memory_psi_full_avg10'] = 74
        self.assertEqual(assess(row, now=110)['admissions'][0]['decision'], 'NO_GO')

    def test_active_swap_out_blocks(self):
        row = snapshot()
        row['hosts'][0]['swap_out_mib_s'] = 15
        self.assertIn('active swap writes', assess(row, now=110)['admissions'][0]['reasons'])

    def test_stale_telemetry_is_unknown_not_healthy(self):
        self.assertEqual(assess(snapshot(), now=300)['admissions'][0]['decision'], 'NO_GO')

    def test_owner_grant_is_required(self):
        row = snapshot()
        row['runtimes'][0]['owner_approved'] = False
        self.assertEqual(assess(row, now=110)['admissions'][0]['decision'], 'NO_GO')

    def test_runtime_concurrency_ceiling(self):
        row = snapshot()
        row['runtimes'][0]['running_sessions'] = 1
        self.assertEqual(assess(row, now=110)['admissions'][0]['decision'], 'NO_GO')

    def test_unhealthy_tokyo_does_not_block_healthy_windows(self):
        row = snapshot()
        row['hosts'].append({
            'device_ref': 'device:windows', 'sampled_at': 100, 'source': 'LIVE',
            'memory_psi_full_avg10': 0, 'swap_out_mib_s': 0, 'blocked_tasks': 0,
        })
        row['hosts'][0]['memory_psi_full_avg10'] = 74
        row['runtimes'].append({
            'device_ref': 'device:windows', 'runtime_id': 'codex-win', 'source': 'LIVE',
            'owner_approved': True, 'reachable': True, 'sampled_at': 100,
            'running_sessions': 0, 'max_sessions': 1,
        })
        actual = assess(row, now=110)['admissions']
        self.assertEqual([r['decision'] for r in actual],
                         ['NO_GO', 'GO_ONE_BOUNDED_RUN'])

    def test_duplicate_host_identity_fails_closed(self):
        row = snapshot()
        row['hosts'].append(copy.deepcopy(row['hosts'][0]))
        self.assertEqual(assess(row, now=110)['admissions'][0]['decision'], 'NO_GO')

    def test_disconnected_runtime_is_not_admitted(self):
        row = snapshot()
        row['runtimes'][0]['reachable'] = False
        self.assertEqual(assess(row, now=110)['admissions'][0]['decision'], 'NO_GO')

    def test_disconnected_host_is_not_admitted(self):
        row = snapshot()
        row['hosts'][0]['reachable'] = False
        self.assertEqual(assess(row, now=110)['admissions'][0]['decision'], 'NO_GO')

    def test_never_executes_side_effect(self):
        self.assertEqual(assess(snapshot(), now=110)['writes'], [])


if __name__ == '__main__':
    unittest.main()
