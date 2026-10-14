"""Solver registry (configs/solvers.json) and game version profile (configs/game-*.json)."""
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spire_exact import game_profile as gp
from spire_exact import solvers
from spire_exact.canonical import ContractError, canonical
from spire_exact.planning.identity import advisor_identity

ROOT = Path(__file__).resolve().parents[1]


def touch(path, data=b'x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class Layout:
    """A fake workspace and game install with the two registry entries."""

    def __init__(self, tmp):
        self.root = Path(tmp) / 'ws'
        self.data = Path(tmp) / 'steam/common/Game/data_sts2'
        self.data.mkdir(parents=True)
        (self.data.parent / 'release_info.json').write_text('{"version": "v0.111.0"}', encoding='utf-8')
        workshop = Path(tmp) / 'steam/workshop/content/2868840/3747602295'
        touch(workshop / 'compat/0.111.0/STS2-RitsuLib.dll', b'ritsu')
        touch(workshop / 'shared/Shared.dll', b'shared')
        doc = json.loads((ROOT / 'configs/solvers.json').read_text(encoding='utf-8'))
        for entry in doc['solvers'].values():
            touch(self.root / entry['solver'], entry['solver'].encode())
            touch(self.root / entry['harness'], entry['harness'].encode())
        archive = self.root / 'native/cs050/vendor/CombatSolver'
        for name in solvers.ARCHIVE_FILES:
            touch(archive / name)
        self.tarball = touch(self.root / 'native/cs050-source-v0.50.1.tar.gz', b'tar')
        identity = {'upstream_tag': 'v0.50.1', 'archive_sha256': hashlib.sha256(b'tar').hexdigest()}
        (self.root / 'native/cs050/source-identity.json').write_text(json.dumps(identity), encoding='utf-8')
        self.registry = self.root / 'configs/solvers.json'
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        self.registry.write_text(json.dumps(doc), encoding='utf-8')
        self.workshop = workshop

    def resolve(self, name):
        with patch('spire_exact.solvers.verify_upstream', return_value={'commit': 'pin'}):
            return solvers.resolve(name, self.data, root=self.root, registry=self.registry)


class SolverRegistryTests(unittest.TestCase):
    def test_default_reproduces_the_pre_registry_advisor_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            lay = Layout(tmp)
            advisor, record = lay.resolve(None)
            # The NativeCampaignBackend construction before configs/solvers.json existed.
            upstream = lay.root / 'vendor/CombatSolver'
            old = {'solver': str(upstream / '.godot/mono/temp/bin/Release/CombatSolver.dll'),
                   'harness': str(upstream / 'tools/OfflineSearchHarness/bin/Release/net9.0/OfflineSearchHarness.dll'),
                   'dependency_dirs': [str(lay.workshop / 'compat/0.111.0'), str(lay.workshop / 'shared')],
                   'budget_ms': 200}
            old['binary_identity'] = advisor_identity(old)
            self.assertEqual(canonical(old), canonical(advisor))
            self.assertEqual(list(old), list(advisor))
            self.assertNotIn('solver', record)

    def test_other_entry_has_its_own_identity_and_is_named_in_provenance_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            lay = Layout(tmp)
            default, _ = lay.resolve(None)
            other, record = lay.resolve('cs-0.50.1')
            self.assertEqual(set(default), set(other))
            self.assertNotEqual(default['binary_identity'], other['binary_identity'])
            self.assertEqual(record['solver'], {'name': 'cs-0.50.1', 'status': 'experimental'})
            self.assertTrue(record['upstream']['archive_checked'])

    def test_archive_hash_mismatch_and_unknown_names_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            lay = Layout(tmp)
            lay.tarball.write_bytes(b'changed')
            with self.assertRaisesRegex(ContractError, 'sha256'):
                lay.resolve('cs-0.50.1')
            with self.assertRaisesRegex(ContractError, 'unknown solver'):
                lay.resolve('cs-9')

    def test_real_registry_default_is_the_i054a_solver(self):
        doc = solvers.load_registry()
        self.assertEqual(doc['default'], 'cs-4d2c550')
        self.assertEqual(doc['solvers'][doc['default']]['status'], 'validated-as-i054a')
        for name, entry in doc['solvers'].items():
            if name != doc['default']:
                self.assertEqual(entry['status'], 'experimental')

    def test_non_default_solver_is_an_experimental_mechanism(self):
        from types import SimpleNamespace
        from spire_exact.planning.registry import MECHANISMS
        entry = MECHANISMS['combat-solver-version']
        self.assertEqual(entry.status, 'experimental')
        self.assertFalse(entry.active(SimpleNamespace(combat_solver=None)))
        self.assertFalse(entry.active(SimpleNamespace(combat_solver='cs-4d2c550')))
        self.assertTrue(entry.active(SimpleNamespace(combat_solver='cs-0.50.1')))

    def test_bridge_contract_names_every_reflected_advisor_member(self):
        """Every literal name BeamAdvisor reads or sets by reflection is in SolverBridge.cs."""
        host = ROOT / 'native/SpireNativeHost'
        bridge = (host / 'SolverBridge.cs').read_text(encoding='utf-8')
        listed = set(re.findall(r'"([A-Za-z][\w.+]*)"', bridge))
        listed |= {part for name in listed for part in re.split(r'[.+]', name)}
        source = (host / 'BeamAdvisor.cs').read_text(encoding='utf-8')
        used = set(re.findall(r'\b(?:Get|Int|Set)\([^,()]+(?:\([^()]*\))?!?,"(\w+)"', source))
        used |= set(re.findall(r'\.Get(?:Method|Property|NestedType)\("(\w+)"', source))
        used |= set(re.findall(r'GetType\("([\w.]+)"', source))
        self.assertTrue(used)
        used = {name for name in used if name[0].isupper()}  # snake_case names are request JSON keys
        self.assertEqual(sorted(used - listed), [])


class GameProfileTests(unittest.TestCase):
    def test_reseed_list_is_read_from_gate_probe(self):
        self.assertEqual(gp.probe_reseed_streams(), ['Shuffle', 'CombatCardGeneration', 'CombatPotionGeneration',
            'CombatCardSelection', 'CombatEnergyCosts', 'CombatTargets', 'MonsterAi', 'Niche', 'CombatOrbs'])

    def test_checked_in_profiles_match_the_workspace(self):
        profiles = sorted((ROOT / 'configs').glob('game-*.json'))
        self.assertTrue(profiles)
        for path in profiles:
            doc = json.loads(path.read_text(encoding='utf-8'))
            with self.subTest(profile=path.name):
                self.assertEqual(path.name, f"game-{doc['game']['game_sha256'][:12]}.json")
                self.assertEqual(doc['probe_reseed_streams'], gp.probe_reseed_streams())
                self.assertLessEqual(set(doc['probe_reseed_streams']), set(doc['run_rng_streams']))
                self.assertEqual(doc['unsaved_state'], gp.unsaved_state_record())
                self.assertEqual(sorted(doc['solvers']), solvers.names())

    def test_compare_reports_what_to_regenerate(self):
        current = {'game': {'game_sha256': 'a' * 64}, 'api_sha256': '1', 'run_rng_streams': ['Shuffle'],
                   'probe_reseed_streams': ['Shuffle', 'Niche'], 'unsaved_state': {'sha256': 'u'}, 'solvers': {}}
        self.assertEqual(gp.compare(None, current)['status'], 'NO_PROFILE')
        profile = dict(current, api_sha256='0', run_rng_streams=['Shuffle', 'Niche'])
        report = gp.compare(profile, current)
        self.assertEqual(report['status'], 'CHANGED')
        self.assertEqual(report['items']['api_sha256'], 'CHANGED')
        self.assertEqual(report['items']['probe_reseed_streams'], 'MISSING_STREAM')
        self.assertEqual(report['items']['unsaved_state'], 'OK')
        self.assertEqual(sorted(report['regenerate']), ['api_sha256', 'probe_reseed_streams', 'run_rng_streams'])

    def test_routing_refuses_another_game_build_or_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            configs = Path(tmp)
            table = touch(configs / 'unsaved-state.json', b'{}')
            sha = 'b' * 64
            with self.assertRaisesRegex(ContractError, 'no configs/game-'):
                gp.require_routing(sha, configs, table)
            doc = {'schema': gp.SCHEMA, 'game': {'game_sha256': sha},
                   'unsaved_state': {'sha256': hashlib.sha256(b'{}').hexdigest()}}
            gp.profile_path(sha, configs).write_text(json.dumps(doc), encoding='utf-8')
            self.assertEqual(gp.require_routing(sha, configs, table)['game']['game_sha256'], sha)
            table.write_bytes(b'{"changed": 1}')
            with self.assertRaisesRegex(ContractError, 'not the table'):
                gp.require_routing(sha, configs, table)


if __name__ == '__main__':
    unittest.main()
