"""Open one HOLDOUT panel: reserve fresh seeds in the shared ledger and write the panel manifest.

Only at the end of a milestone and with the user's go-ahead (AGENTS.md M5). The registry draws seeds no earlier run
or panel has used. The panel records the frozen workspace, the wall cap and the solve-p5 settings of the check;
tools/run_seed.py --panel HOLDOUT refuses a run that does not match them. A panel file is never rewritten.

    python tools/create_holdout_panel.py --name holdout-01 --workspace experiments/frozen-i054a --minutes 30 \
        --note "user 2026-10-02: HOLDOUT check at the 30 minute budget" --extra --root-policies pick,elo
"""
from pathlib import Path
import argparse, datetime, json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.seed_registry import reserve
from tools.experiment import seed_set
from spire_exact.planning.io import read_json, write_json


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--name', required=True, help='panel file name without .json, e.g. holdout-01')
    p.add_argument('--workspace', type=Path, required=True, help='the frozen workspace the check runs from')
    p.add_argument('--minutes', type=int, required=True, help='wall cap of every run of the check')
    p.add_argument('--count', type=int, default=20)
    p.add_argument('--note', required=True, help="the user's authorization and any deviation from AGENTS.md M5")
    p.add_argument('--extra', nargs=argparse.REMAINDER, default=[], help='solve-p5 arguments every run of the check passes after --extra')
    a = p.parse_args()
    if not a.name.startswith('holdout-'):
        raise SystemExit('panel name must start with holdout-')
    workspace = (a.workspace if a.workspace.is_absolute() else ROOT / a.workspace).resolve()
    if not (workspace / 'freeze.json').is_file():
        raise SystemExit('a HOLDOUT check runs from a frozen workspace')
    path = ROOT / 'experiments/panels/A10-seed-v2' / (a.name + '.json')
    if path.exists():
        raise SystemExit('panel already opened; never redraw its seeds')
    version = read_json(workspace / 'freeze.json')['source_version']
    run_id = 'A10-seed-v2-panel-' + a.name
    seeds, ledger = reserve(ROOT, run_id, version, 'HOLDOUT', a.count, None, seed_set)
    write_json(path, {'schema': 'spire-panel/v1', 'protocol': 'A10-seed-v2', 'role': 'HOLDOUT', 'run_id': run_id,
                      'character': 'IRONCLAD', 'ascension': 10, 'unlocks': 'all', 'information': 'full', 'fresh_start': True,
                      'count': a.count, 'seeds': seeds, 'seed_registry': ledger, 'training_allowed': False, 'holdout_generated': True,
                      'opened': datetime.datetime.now().astimezone().isoformat(), 'workspace': str(workspace), 'source_version': version,
                      'wall_cap_minutes': a.minutes, 'extra': a.extra, 'authorization': a.note})
    print(json.dumps({'panel': str(path), 'seeds': seeds}))


if __name__ == '__main__':
    main()
