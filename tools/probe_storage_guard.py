"""Exercise owned Windows Job cancellation at a tiny test-only write budget.

This is a storage harness probe, not a game win-rate evaluation. Production's
8 GB constant is not changed on disk or exposed as a permissive override.
"""
from pathlib import Path
import json, subprocess, sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.experiment import summarize

def main():
    out = ROOT/'experiments/iteration-018/storage-guard-probe'
    out.mkdir(exist_ok=False)
    seed = out/'seed-0'
    code = 'import tools.limited_cli as runner; runner.JOB_WRITE_CAP=4096; runner.main()'
    args = [sys.executable, '-c', code, 'solve-p5', '--seed', '0', '--out', str(seed),
            '--game-dir', str(ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'),
            '--workers', '1', '--dop', '1', '--seconds', '20', '--evaluations', '2',
            '--max-decisions', '100', '--budget-ms', '400']
    with (out/'console.log').open('wb') as log:
        result = subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    row = summarize(seed, result.returncode, 0)
    checks = {'owned_job_cancelled': result.returncode == 125,
              'not_game_failure': row['reason'] == 'STORAGE_LIMIT',
              'not_win': not row['win'],
              'not_timeout': not row['resources']['hard_timeout'],
              'write_counter_exercised': row['resources']['job_write_transfer_bytes'] >= 4096}
    report = {'passed': all(checks.values()), 'checks': checks, 'row': row,
              'scope': 'Storage cancellation only; deliberately tiny write cap; not a gameplay validation'}
    (out/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': checks}))
    raise SystemExit(0 if report['passed'] else 1)

if __name__ == '__main__': main()
