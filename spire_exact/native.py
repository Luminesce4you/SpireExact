"""Read installed game data through a separate, fingerprinted native process.

No game files are copied into the repository. Data exports never certify gameplay
equivalence or optimality. Build identity includes all host sources and inputs.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from .canonical import ContractError, digest, read_json, write_json
from .upstream import ROOT, file_hash

HOST = ROOT / 'native/SpireNativeHost'


def game_data(path: Path | None = None) -> Path:
    roots = [path] if path else []
    if not path and os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
                steam = Path(winreg.QueryValueEx(key, 'SteamPath')[0])
            libraries = [steam]
            vdf = steam / 'steamapps/libraryfolders.vdf'
            if vdf.is_file():
                libraries += [Path(p.replace('\\\\', '\\')) for p in
                              re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding='utf-8'))]
            roots = [p / 'steamapps/common/Slay the Spire 2' for p in libraries]
        except OSError:
            pass
    candidates = set()
    for root in roots:
        for item in (root, root / 'data_sts2_windows_x86_64', root / 'data_sts2_linuxbsd_x86_64'):
            if (item / 'sts2.dll').is_file():
                candidates.add(item.resolve())
    if len(candidates) != 1:
        raise ContractError('Specify --game-dir: expected one installed STS2, found ' + str(len(candidates)))
    return candidates.pop()


def dotnet() -> str:
    local = ROOT / '.tools/dotnet' / ('dotnet.exe' if os.name == 'nt' else 'dotnet')
    found = str(local) if local.is_file() else shutil.which('dotnet')
    if not found:
        raise ContractError('A .NET 9 SDK is required to build the native data host')
    return found


def input_fingerprint(data: Path) -> dict:
    sources = {str(p.relative_to(HOST)).replace('\\', '/'): file_hash(p)
               for p in sorted(HOST.rglob('*')) if p.is_file()
               and p.suffix in ('.cs', '.csproj') and not {'bin', 'obj'} & set(p.relative_to(HOST).parts)}
    dependencies = {}
    for name in ('sts2.dll', 'GodotSharp.dll', '0Harmony.dll'):
        if not (data / name).is_file():
            raise ContractError('Missing game dependency: ' + str(data / name))
        dependencies[name] = file_hash(data / name)
    return {'sources': sources, 'dependencies': dependencies, 'game_data_dir': str(data.resolve())}


def build_host(data: Path) -> tuple[str, Path, dict]:
    from .build_lock import native_build_lock
    # Fingerprint/cache check belongs INSIDE the cross-process lock. A second
    # caller must see the completed first build, not race its obj/bin writes.
    with native_build_lock(HOST/'bin/build.lock'):
        return _build_host_locked(data)


def _build_host_locked(data: Path) -> tuple[str, Path, dict]:
    executable = dotnet()
    identity = input_fingerprint(data)
    stamp_file = HOST / 'bin/build-identity.json'
    assembly = HOST / 'bin/Release/net9.0/SpireNativeHost.dll'
    if stamp_file.is_file() and assembly.is_file():
        stamp = read_json(stamp_file)
        if stamp.get('inputs') == identity and stamp.get('host_sha256') == file_hash(assembly):
            return executable, assembly, stamp
    report_dir = ROOT / 'outputs/native-builds'
    report_dir.mkdir(parents=True, exist_ok=True)
    log = report_dir / (digest(identity) + '.log')
    command = [executable, 'build', str(HOST / 'SpireNativeHost.csproj'), '-c', 'Release',
               '-p:Sts2DataDir=' + str(data.resolve()), '-p:CopyModOnBuild=false',
               '-p:OutputPath=' + str(assembly.parent.resolve()) + '/',
               '-p:NuGetAudit=false']
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1', NUGET_CERT_REVOCATION_MODE='offline',
               DOTNET_NUGET_SIGNATURE_VERIFICATION='false')
    with log.open('w', encoding='utf-8') as stream:
        try:
            completed = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                       env=env, timeout=180)
        except subprocess.TimeoutExpired as error:
            raise ContractError('Native host build timed out; log: ' + str(log)) from error
    if completed.returncode:
        raise ContractError('Native host build failed; log: ' + str(log))
    stamp = {'inputs': identity, 'host_sha256': file_hash(assembly), 'build_completed': True,
             'game_equivalence_verified': False, 'log': str(log), 'command': command}
    write_json(stamp_file, stamp)
    return executable, assembly, stamp


def export_native(command: str, output: Path, game_dir: Path | None = None, *, timeout_seconds: int = 90, **params) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ContractError('Native export output must be empty')
    data = game_data(game_dir)
    executable, assembly, stamp = build_host(data)
    output.mkdir(parents=True, exist_ok=True)
    request = {'command': command, 'out': str((output / 'data').resolve()), **params}
    write_json(output / 'request.json', request)
    env = dict(os.environ, SPIRE_GAME_DATA=str(data))
    with (output / 'host.log').open('w', encoding='utf-8') as log:
        try:
            completed = subprocess.run([executable, str(assembly), str((output / 'request.json').resolve())],
                                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            write_json(output / 'status.json', {'status': 'NATIVE_HOST_TIMEOUT', 'proven_optimal': False})
            raise ContractError('Native export timeout; no solution certified') from error
    status = {'status': 'NATIVE_DATA_EXPORTED' if completed.returncode == 0 else 'NATIVE_HOST_FAILED',
              'exit_code': completed.returncode, 'build': stamp,
              'game_data_dir': str(data), 'proven_optimal': False}
    write_json(output / 'status.json', status)
    if completed.returncode:
        raise ContractError('Native export failed; inspect ' + str(output / 'host.log'))
    loaded = read_json(output / 'data/identity.json')
    for key, filename in (('game_sha256', 'sts2.dll'), ('godot_sha256', 'GodotSharp.dll'),
                          ('harmony_sha256', '0Harmony.dll')):
        if loaded.get(key) != stamp['inputs']['dependencies'][filename]:
            status['status'] = 'INPUTS_CHANGED_DURING_EXPORT'
            write_json(output / 'status.json', status)
            raise ContractError('Loaded game dependencies differ from build inputs; rerun in a new directory')
    return status


def compare_exports(old: Path, new: Path) -> dict:
    """Report removed/added API members and game IDs, never auto-certify semantics."""
    def api(folder):
        return {row['name'] + '::' + member for row in read_json(folder / 'api.json')['types']
                for member in row['members']}
    before, after = api(old), api(new)
    old_identity, new_identity = read_json(old / 'identity.json'), read_json(new / 'identity.json')
    changed = any(old_identity.get(k) != new_identity.get(k) for k in
                  ('host_sha256', 'game_sha256', 'godot_sha256', 'harmony_sha256'))
    result = {'status': 'REVALIDATION_REQUIRED' if changed else 'IDENTICAL_BINARY_INPUTS',
              'api_removed': sorted(before - after), 'api_added': sorted(after - before),
              'matching_binary_inputs': not changed,
              # These exports carry no semantic proof, seed context or active mod set.
              'reuse_old_gameplay_proofs': False, 'game_equivalence_verified': False}
    if (old / 'catalog.json').is_file() and (new / 'catalog.json').is_file():
        a = {c['id']: c for c in read_json(old / 'catalog.json')['cards']}
        b = {c['id']: c for c in read_json(new / 'catalog.json')['cards']}
        result.update(cards_added=sorted(b.keys() - a.keys()), cards_removed=sorted(a.keys() - b.keys()),
                      cards_changed=sorted(k for k in a.keys() & b.keys() if a[k] != b[k]))
    return result
