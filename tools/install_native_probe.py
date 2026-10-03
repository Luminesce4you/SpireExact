"""Install/remove the guarded diagnostic probe in the pinned upstream source only.

Does not build, run, or install a mod. Never overwrites unrelated local changes.
"""
from pathlib import Path
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spire_exact.canonical import ContractError
from spire_exact.upstream import ROOT, run, verify_upstream

ANCHOR = '            payload["root"] = OfflineCombat.DescribeRoot(combat!);'
INSERT = ('            // SPIREEXACT_ROOT_PROBE_V1: diagnostic only, not an exact adapter.\n'
          '            if (Environment.GetEnvironmentVariable("SPIREEXACT_ROOT_PROBE") == "1")\n'
          '                SpireExactRootProbe.Write(combat!, options.OutputDirectory);\n')
RELATIVE = 'tools/OfflineSearchHarness/Program.cs'


def apply_patch(original: str) -> str:
    if original.count(ANCHOR) != 1:
        raise ContractError('pinned harness anchor not unique; refusing to guess a patch location')
    return original.replace(ANCHOR, INSERT + ANCHOR)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, default=Path('vendor/CombatSolver'))
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args()
    verify_upstream(args.upstream, allow_dirty=True)
    baseline = run(['git','show','HEAD:'+RELATIVE], args.upstream) + '\n'
    program = args.upstream / RELATIVE
    current = program.read_text(encoding='utf-8-sig').replace('\r\n','\n')
    expected = apply_patch(baseline)
    if current not in (baseline, expected):
        raise ContractError('Program.cs contains other changes; merge manually instead of overwriting')
    source = (ROOT/'native/SpireExactRootProbe.cs').read_text(encoding='utf-8')
    target = args.upstream/'tools/OfflineSearchHarness/SpireExactRootProbe.cs'
    if target.exists() and target.read_text(encoding='utf-8-sig') != source:
        raise ContractError('a different probe file exists; refusing to overwrite it')
    if args.remove:
        program.write_text(baseline, encoding='utf-8')
        if target.exists(): target.unlink()
        print('Diagnostic probe removed. No game files changed.')
    else:
        target.write_text(source, encoding='utf-8')
        program.write_text(expected, encoding='utf-8')
        print('Probe source installed, not compiled or run. Enable with SPIREEXACT_ROOT_PROBE=1 after building.')

if __name__ == '__main__':
    try: main()
    except (ContractError,OSError) as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)
