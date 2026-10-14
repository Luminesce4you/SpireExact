"""CombatSolver registry: which solver/harness build the native host loads.

The host has no compile-time CombatSolver dependency; BeamAdvisor.cs loads the
solver and harness from the paths in each request. Switching solver versions
is therefore a registry entry (configs/solvers.json), not a second host copy.

The default entry reproduces the advisor config byte for byte as it was before
the registry existed: request bytes, and so result caches and certificates,
are unchanged. The entry name is recorded in advisor-provenance.json only.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .canonical import ContractError
from .upstream import ROOT, file_hash, verify_upstream

REGISTRY = ROOT / 'configs/solvers.json'
SCHEMA = 'spire-solver-registry/v1'
ARCHIVE_FILES = ('CombatSolver.csproj', 'LICENSE', 'THIRD_PARTY_NOTICES.md')


def load_registry(path: Path = REGISTRY) -> dict:
    doc = json.loads(path.read_text(encoding='utf-8'))
    if doc.get('schema') != SCHEMA:
        raise ContractError('solver registry schema mismatch: ' + str(path))
    if doc.get('default') not in doc.get('solvers', {}):
        raise ContractError('solver registry default is not an entry')
    return doc


def names(path: Path = REGISTRY) -> list[str]:
    return sorted(load_registry(path)['solvers'])


def default_name(path: Path = REGISTRY) -> str:
    return load_registry(path)['default']


def dependency_dirs(kind: str, data: Path) -> list[Path]:
    if kind != 'workshop-ritsulib':
        raise ContractError('unknown solver dependency kind: ' + kind)
    version = json.loads((data.parent / 'release_info.json').read_text(encoding='utf-8'))['version'].removeprefix('v')
    workshop = data.parents[2] / 'workshop/content/2868840/3747602295'
    return [workshop / 'compat' / version, workshop / 'shared']


def verify_archive(directory: Path, identity: Path) -> dict:
    """Provenance of a source tree unpacked from a release archive (no .git)."""
    for relative in ARCHIVE_FILES:
        if not (directory / relative).is_file():
            raise ContractError('missing archived-source file: ' + relative)
    tree = hashlib.sha256()
    count = 0
    for item in sorted(p for p in directory.rglob('*') if p.is_file()):
        relative = item.relative_to(directory).as_posix()
        if relative.split('/')[0] in ('.godot', 'bin', 'obj') or '/bin/' in relative or '/obj/' in relative:
            continue
        if item.suffix not in ('.cs', '.csproj', '.props', '.targets', '.json'):
            continue
        tree.update((relative + '\0' + file_hash(item) + '\n').encode('utf-8'))
        count += 1
    doc = json.loads(identity.read_text(encoding='utf-8'))
    archive = identity.parent.parent / f"cs050-source-{doc.get('upstream_tag', '')}.tar.gz"
    if archive.is_file() and file_hash(archive) != doc.get('archive_sha256'):
        raise ContractError('archived source does not match its recorded sha256: ' + str(archive))
    return {'archive_identity': doc, 'archive_checked': archive.is_file(),
            'selected_source_sha256': {r: file_hash(directory / r) for r in ARCHIVE_FILES},
            'source_and_config_tree_sha256': tree.hexdigest(), 'source_and_config_files_hashed': count}


def resolve(name: str | None, data: Path, root: Path = ROOT, registry: Path = REGISTRY) -> tuple[dict, dict]:
    """(advisor config, provenance) for a registry entry; None means the default."""
    doc = load_registry(registry)
    name = name or doc['default']
    entry = doc['solvers'].get(name)
    if entry is None:
        raise ContractError(f'unknown solver {name!r}; registry has {sorted(doc["solvers"])}')
    source = entry['source']
    upstream = root / source['dir']
    if source['kind'] == 'git':
        provenance = verify_upstream(upstream, allow_dirty=True)
    elif source['kind'] == 'archive':
        provenance = verify_archive(upstream, root / source['identity'])
    else:
        raise ContractError('unknown solver source kind: ' + source['kind'])
    solver, harness = root / entry['solver'], root / entry['harness']
    dirs = dependency_dirs(entry['dependencies'], data)
    for file in (solver, harness, dirs[0] / 'STS2-RitsuLib.dll'):
        if not file.is_file():
            raise ContractError('optional CombatSolver advisor missing: ' + str(file))
    advisor = {'solver': str(solver), 'harness': str(harness),
               'dependency_dirs': [str(p) for p in dirs], 'budget_ms': 200}
    from .planning.identity import advisor_identity
    advisor['binary_identity'] = advisor_identity(advisor)
    record = {'upstream': provenance, 'solver_sha256': file_hash(solver), 'harness_sha256': file_hash(harness),
              'use': 'candidate proposals only; never an upper bound or infeasibility proof'}
    if name != doc['default']:
        record['solver'] = {'name': name, 'status': entry['status']}
    return advisor, record


def solver_contract(name: str | None, data: Path, timeout: int = 120) -> dict:
    """Ask the built host which reflective names the solver build lacks
    (SolverBridge.cs; loads assemblies only, runs no search or game)."""
    import os
    import subprocess
    from .native import build_host
    advisor, _ = resolve(name, data)
    executable, assembly, _ = build_host(data)
    command = [executable, str(assembly), '--solver-contract', advisor['solver'], advisor['harness'],
               *advisor['dependency_dirs']]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=timeout,
                               env=dict(os.environ, SPIRE_GAME_DATA=str(data.resolve()),
                                        DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1'))
    if completed.returncode:
        raise ContractError('solver contract check failed: ' + completed.stderr[-4000:])
    reply = json.loads(completed.stdout[completed.stdout.index('{'):])
    reply['registry_name'] = name or default_name()
    return reply


if __name__ == '__main__':
    import argparse
    from .native import game_data
    parser = argparse.ArgumentParser(description='Check a configs/solvers.json entry against the host contract')
    parser.add_argument('names', nargs='*')
    parser.add_argument('--game-dir', type=Path)
    args = parser.parse_args()
    data = game_data(args.game_dir)
    failed = False
    for entry in args.names or names():
        reply = solver_contract(entry, data)
        failed |= reply['status'] != 'OK'
        print(json.dumps(reply, ensure_ascii=False, indent=1))
    raise SystemExit(1 if failed else 0)
