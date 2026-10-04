"""Conditional release controller tests with fake proof files/processes only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from spire_exact.canonical import digest
from tools import run_release20 as release


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class SequenceRandom:
    def __init__(self):
        self.next = 0
        self.calls = 0

    def randrange(self, limit):
        self.calls += 1
        if limit != release.MAX_SEED + 1:
            raise AssertionError('wrong numeric seed domain')
        value = self.next
        self.next += 1
        return value


def fixture(base):
    root = Path(base) / 'P5plus-f1-card-switches'
    workspace = root / 'experiments/frozen-i075-final-gzip'
    put(workspace / 'freeze.json', {'source_version': release.VERSION})
    host = workspace / 'native/SpireNativeHost/bin/Release/net9.0/SpireNativeHost.dll'
    host.parent.mkdir(parents=True, exist_ok=True)
    host.write_bytes(b'fake native host, never loaded')
    host_sha = hashlib.sha256(host.read_bytes()).hexdigest()
    artifact = Path(base) / 'artifacts'
    put(root / 'storage-policy.json', {'artifact_roots': [str(artifact)]})
    queue = root / 'experiments/iteration-075/final-gzip-queue.json'
    job = ['--seed', release.SOURCE_SEED, '--name', release.SOURCE_NAME, '--minutes', '30',
           '--iteration', 'iteration-075-final-tests', '--workspace', str(workspace),
           '--profile', 'focus', '--feature-profile', 'i075-final', '--workers', '7',
           '--logical-cpus', '8', '--panel', 'USER_INTERACTIVE', '--extra',
           '--runtime-profile', 'server-bounded-large-gen0', '--root-policies', 'pick,elo',
           '--focus-cluster-cap', '2', '--final-gate-plan', 'open', '--root-async',
           '--root-round', '7', '--requeue-lost', '1', '--gate-preset', 'escalate-evaluate']
    put(queue, [job])
    log = queue.with_suffix('.log.jsonl')
    objective = {'id': 'whole_run_victory/v1', 'information': 'full', 'minimum': 0,
                 'maximum': 1, 'secondary_objective': None}
    context = {'seed': release.SOURCE_SEED, 'character': 'IRONCLAD', 'ascension': 10,
               'unlocks': 'all', 'objective': objective}
    trace = [{'kind': 'map', 'col': 0, 'row': 1}, {'kind': 'event', 'index': 0}]
    native = {'host_sha256': host_sha, 'game_sha256': release.GAME_SHA256}
    folder = artifact / 'iteration-075-final-tests' / release.SOURCE_NAME
    target = folder / ('seed-' + release.SOURCE_SEED)
    settings = ['--seed', release.SOURCE_SEED, '--character', 'IRONCLAD', '--ascension', '10',
                '--unlocks', 'all', '--workers', '7', '--feature-profile', 'i075-final',
                '--runtime-profile', 'server-bounded-large-gen0', '--gate-preset', 'escalate-evaluate']
    put(folder / 'validation-manifest.json', {'run_id': release.SOURCE_NAME, 'seed': release.SOURCE_SEED,
        'version': release.VERSION, 'workspace': str(workspace), 'workspace_frozen': True,
        'host_sha256': host_sha, 'wall_cap_seconds': 1800, 'profile': 'focus',
        'panel': 'USER_INTERACTIVE', 'workload_bytes': 14336 * 1024 ** 2,
        'cpu_set': list(range(8)), 'efficiency_class': 1, 'settings': settings})
    put(folder / 'baseline-report.json', {'seed': release.SOURCE_SEED, 'verified_win': True, 'exit_code': 0})
    put(target / 'result.json', {'context': context, 'status': 'VERIFIED_WIN_IN_NATIVE_HOST', 'optimal_in_backend': True})
    put(target / 'winning-route.json', {'context': context, 'trace': trace})
    put(target / 'certificate.json', {'schema': 'spire-mode1-certificate/v1', 'objective': objective,
        'identity': {'context': context, 'native': native}, 'trace_length': len(trace),
        'trace_sha256': digest(trace), 'lower_bound': 1, 'upper_bound': 1,
        'optimal_in_backend': True, 'proves_no_better_boolean_value': True})
    put(root / 'experiments/seed-ledger.json', {'reserved': ['0'], 'runs': [{'run_id': 'past', 'seeds': ['1']}]})
    main = root.parent / 'P5plus'
    put(main / 'experiments/seed-ledger.json', {'reserved': ['2'], 'runs': [{'run_id': 'old', 'seeds': ['3']}]})
    put(workspace / 'experiments/seed-ledger.json', {'runs': [{'seed': '4'}]})
    put(root / 'experiments/panels/train.json', {'seeds': ['5']})
    put(main / 'experiments/panels/dev.json', {'seed': '6'})
    row = {'index': 0, 'job': job, 'exit_code': 0, 'stdout': json.dumps({
        'seed': release.SOURCE_SEED, 'verified_win': True, 'exit_code': 0}) + '\n'}
    directory = root / 'experiments/iteration-075/release20'
    return SimpleNamespace(root=root, workspace=workspace, queue=queue, log=log,
                           job=job, row=row, directory=directory, folder=folder, target=target)


def finish_source(f, row=None):
    f.log.write_text(json.dumps(row or f.row) + '\n', encoding='utf-8')


def controller_events(f):
    return [json.loads(line) for line in (f.directory / 'controller.log.jsonl').read_text(encoding='utf-8').splitlines()]


class Release20Tests(unittest.TestCase):
    def test_verified_gate_binds_manifest_certificate_host_and_complete_route(self):
        with TemporaryDirectory() as base:
            f = fixture(base)
            proof = release.verified_condition(f.root, f.job, f.row)
            self.assertEqual(proof['source_version'], release.VERSION)
            self.assertTrue(proof['certificate'].endswith('certificate.json'))
        for filename, mutate in (
            ('validation-manifest.json', lambda d: d.update(version='different')),
            ('baseline-report.json', lambda d: d.update(verified_win=False)),
            ('seed/certificate.json', lambda d: d['identity']['context'].update(seed='other')),
            ('seed/certificate.json', lambda d: d['identity']['native'].update(host_sha256='other')),
            ('seed/winning-route.json', lambda d: d['trace'].append({'kind': 'illegal'})),
            ('seed/result.json', lambda d: d.update(status='UNKNOWN'))):
            with self.subTest(filename=filename), TemporaryDirectory() as base:
                f = fixture(base)
                path = f.target / filename[5:] if filename.startswith('seed/') else f.folder / filename
                data = json.loads(path.read_text(encoding='utf-8')); mutate(data); put(path, data)
                with self.assertRaises(ValueError):
                    release.verified_condition(f.root, f.job, f.row)

    def test_unrelated_or_partial_queue_log_does_not_satisfy_gate(self):
        with TemporaryDirectory() as base:
            f = fixture(base)
            other = deepcopy(f.row); other['job'][3] = 'unrelated-win'
            f.log.write_text(json.dumps(other) + '\n' + json.dumps(f.row), encoding='utf-8')
            self.assertIsNone(release.finished_row(f.log, f.job))
            with f.log.open('a', encoding='utf-8') as stream:
                stream.write('\n')
            self.assertEqual(release.finished_row(f.log, f.job), f.row)

    def test_failure_timeout_or_missing_certificate_never_draws_seeds_or_starts_queue(self):
        for kind in ('unsolved', 'timeout', 'missing_certificate'):
            with self.subTest(kind=kind), TemporaryDirectory() as base:
                f = fixture(base); row = deepcopy(f.row)
                if kind == 'missing_certificate':
                    (f.target / 'certificate.json').unlink()
                else:
                    note = json.loads(row['stdout']); note['verified_win'] = False
                    note['exit_code'] = 124 if kind == 'timeout' else 0
                    row['stdout'] = json.dumps(note)
                finish_source(f, row)
                with patch.object(release.random, 'SystemRandom') as rng:
                    result = release.control(f.root, f.queue, f.log, f.directory,
                                             run=lambda *a, **k: self.fail('must not launch'))
                    rng.assert_not_called()
                self.assertEqual(result, 0)
                self.assertFalse((f.directory / 'seeds.json').exists())
                self.assertEqual(controller_events(f)[-1]['event'], 'condition-failed')

    def test_waits_without_draw_then_generates_twenty_and_continues_all_failures(self):
        with TemporaryDirectory() as base:
            f = fixture(base); rng = SequenceRandom(); calls = []

            def wait(_):
                self.assertEqual(rng.calls, 0)
                self.assertFalse((f.directory / 'seeds.json').exists())
                finish_source(f)

            def fake_run(command, **kwargs):
                calls.append(command)
                self.assertIn('--wait-idle', command)
                self.assertNotIn('--stop-unsolved', command)
                self.assertEqual(kwargs['cwd'], f.root)
                jobs = json.loads((f.directory / 'queue.json').read_text(encoding='utf-8'))
                with (f.directory / 'queue.log.jsonl').open('w', encoding='utf-8') as stream:
                    for index, job in enumerate(jobs):
                        # Every run is censored/failed in this fake batch. The
                        # controller still accepts all 20 actual completion rows.
                        stream.write(json.dumps({'index': index, 'job': job, 'exit_code': 0 if index % 2 else 1,
                            'stdout': json.dumps({'verified_win': False, 'exit_code': 124})}) + '\n')
                return SimpleNamespace(returncode=0, stdout='', stderr='')

            with patch.object(release.random, 'SystemRandom', return_value=rng):
                result = release.control(f.root, f.queue, f.log, f.directory, sleep=wait, run=fake_run)
            self.assertEqual(result, 0)
            self.assertEqual(len(calls), 1)
            seeds = json.loads((f.directory / 'seeds.json').read_text(encoding='utf-8'))['seeds']
            self.assertEqual(seeds, [str(n) for n in range(7, 27)])
            self.assertEqual(len(set(seeds)), 20)
            jobs = json.loads((f.directory / 'queue.json').read_text(encoding='utf-8'))
            for index, job in enumerate(jobs, 1):
                expected = deepcopy(f.job)
                for key, value in {'--seed': seeds[index - 1], '--name': 'release20-%02d-%s' % (index, seeds[index - 1]),
                                   '--iteration': release.ITERATION, '--minutes': '30', '--panel': 'USER_INTERACTIVE'}.items():
                    expected[expected.index(key) + 1] = value
                self.assertEqual(job, expected)
            ledger = json.loads((f.root / 'experiments/seed-ledger.json').read_text(encoding='utf-8'))
            reserved = [row for row in ledger['runs'] if row.get('release20')]
            self.assertEqual(len(reserved), 20)
            self.assertTrue(all(row['kind'] == 'user_interactive' and row['reserved']
                                and row['not_a_holdout_result'] and row['version'] == release.VERSION for row in reserved))
            self.assertEqual([row['event'] for row in controller_events(f)],
                             ['waiting', 'generated', 'started', 'completed'])
            self.assertEqual(controller_events(f)[-1]['finished'], 20)

    def test_existing_seed_artifact_cannot_be_redrawn_or_overwritten(self):
        with TemporaryDirectory() as base:
            f = fixture(base); f.directory.mkdir(parents=True)
            put(f.directory / 'seeds.json', {'seeds': ['immutable']})
            before = (f.directory / 'seeds.json').read_bytes(); rng = SequenceRandom()
            with self.assertRaisesRegex(ValueError, 'never redraw'):
                release.generate_release(f.root, f.directory, f.job, {}, rng)
            self.assertEqual(rng.calls, 0)
            self.assertEqual((f.directory / 'seeds.json').read_bytes(), before)

    def test_user_stop_during_waiting_never_draws(self):
        with TemporaryDirectory() as base:
            f = fixture(base)
            def stop(_):
                (f.directory / 'queue.stop').touch()
            with patch.object(release.random, 'SystemRandom') as rng:
                self.assertEqual(release.control(f.root, f.queue, f.log, f.directory, sleep=stop,
                                                run=lambda *a, **k: self.fail('must not run')), 0)
                rng.assert_not_called()
            self.assertEqual(controller_events(f)[-1]['event'], 'cancelled')

    def test_unexpected_queue_failure_is_reported_without_fabricated_completion(self):
        with TemporaryDirectory() as base:
            f = fixture(base); finish_source(f)
            with patch.object(release.random, 'SystemRandom', return_value=SequenceRandom()):
                result = release.control(f.root, f.queue, f.log, f.directory,
                    run=lambda *a, **k: SimpleNamespace(returncode=1, stdout='', stderr='fake script error'))
            self.assertEqual(result, 1)
            self.assertEqual(controller_events(f)[-1]['event'], 'error')
            self.assertNotIn('completed', [row['event'] for row in controller_events(f)])

    def test_detach_only_spawns_controller_and_does_not_draw_or_run_solver(self):
        with TemporaryDirectory() as base:
            f = fixture(base)
            with patch.object(release, 'ROOT', f.root), patch.object(release, 'spawn_detached', return_value=123) as spawn, \
                    patch.object(release.random, 'SystemRandom') as rng:
                self.assertEqual(release.main(['--detach', '--source-queue', str(f.queue),
                                              '--release-dir', str(f.directory)]), 0)
                rng.assert_not_called()
                command, log, cwd = spawn.call_args.args
            self.assertNotIn('--detach', command)
            self.assertNotIn('solve-p5', command)
            self.assertEqual(cwd, f.root)
            self.assertEqual(log, f.directory / 'controller.log')
            self.assertFalse((f.directory / 'seeds.json').exists())


if __name__ == '__main__':
    unittest.main()
