"""Run fight_bench inside the existing A10 Windows Job and storage guard."""
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    args = sys.argv[1:]
    if '--out' not in args:
        raise SystemExit('--out is required')
    sdk = ROOT.parent/'.tools/dotnet'
    if not (sdk/'dotnet.exe').exists():
        sdk = ROOT.parents[2]/'.tools/dotnet'
    os.environ['PATH'] = str(sdk)+os.pathsep+os.environ.get('PATH', '')
    os.environ['DOTNET_ROOT'] = str(sdk)
    os.environ['SPIRE_WALL_LIMIT_SECONDS'] = '7200'
    from tools.fight_bench import _affinity
    kind = args[args.index('--cpus')+1] if '--cpus' in args else 'p'
    os.environ['SPIRE_PROTOCOL'] = 'A10-seed-v2-scaling16' if kind=='p-smt' else 'A10-seed-v2'
    _affinity(kind)
    from spire_exact.native import build_host
    build_host(ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64')
    from tools import limited_cli
    sys.argv = [__file__, '--out', args[args.index('--out')+1]]
    def action(*unused_args, **unused_kwargs):
        from tools import fight_bench
        sys.argv = [str(ROOT/'tools/fight_bench.py'), 'run', *args]
        fight_bench.main()
    limited_cli.runpy.run_module = action
    limited_cli.main()


if __name__ == '__main__':
    main()
