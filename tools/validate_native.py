"""Local installed-DLL integration checks; separate from Python model tests.

Does not launch Godot or certify real-game action equivalence.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spire_exact.canonical import read_json, write_json
from spire_exact.native import export_native

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--game-dir', type=Path)
args = parser.parse_args()
if args.out.exists() and any(args.out.iterdir()):
    raise SystemExit('Output must be empty')
args.out.mkdir(parents=True, exist_ok=True)
checks = []
report = {'schema': 'spire-native-validation/v1', 'successful': False, 'checks': checks,
          'offline_game_dll_executed': True, 'godot_game_tests_run': 0,
          'whole_run_solver_verified': False, 'game_equivalence_verified': False}
try:
    export_native('catalog', args.out / 'catalog', args.game_dir)
    catalog = read_json(args.out / 'catalog/data/catalog.json')
    assert len(catalog['cards']) > 0 and len(catalog['models']) > len(catalog['cards'])
    checks.append({'test': 'native_catalog', 'cards': len(catalog['cards']), 'models': len(catalog['models'])})
    for name, seed in (('seed42a', '42'), ('seed42b', '42'), ('seed43', '43')):
        export_native('seed', args.out / name, args.game_dir, seed=seed,
                      character='IRONCLAD', ascension=0, unlocks='all')
        roundtrip = read_json(args.out / name / 'data/roundtrip.json')
        assert roundtrip['adapted_json_roundtrip_equal'] and roundtrip['run_rng_roundtrip_equal']
    for filename in ('seed.json', 'map.json'):
        assert (args.out / 'seed42a/data' / filename).read_bytes() == (args.out / 'seed42b/data' / filename).read_bytes()
    checks.append({'test': 'same_seed_native_data_reproducible', 'seed': '42'})
    assert (args.out / 'seed42a/data/map.json').read_bytes() != (args.out / 'seed43/data/map.json').read_bytes()
    checks.append({'test': 'different_seed_changes_native_map'})
    checks.append({'test': 'native_save_and_rng_roundtrip', 'seeds': ['42', '43']})
    snapshot = args.out / 'seed42a/data/run-save.json'
    export_native('score', args.out / 'score', args.game_dir, save=str(snapshot.resolve()), victory=False)
    score = read_json(args.out / 'score/data/score.json')['score']
    expected = read_json(args.out / 'seed42a/data/seed.json')['score_at_current_state']
    assert score == expected
    checks.append({'test': 'native_score_matches_after_serialization', 'score': score})
    report['successful'] = True
except Exception as error:
    report['error'] = repr(error)
    raise
finally:
    write_json(args.out / 'validation.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
