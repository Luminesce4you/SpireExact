"""Build a project-owned solver variant from the pinned Git tree; never edit vendor.

Variants use the same build-only isolation changes. Search patches preserve
values and enumeration order; no gameplay/filter/ranking change is intended.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = '4d2c55069f2cf9f8c621631594857b412cf9660f'


def prepare(variant, out):
    upstream = ROOT/'vendor/CombatSolver'
    actual = subprocess.check_output(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != PIN:
        raise ValueError('Pinned vendor commit changed')
    out.mkdir(parents=True, exist_ok=False)
    archive = subprocess.check_output(['git', '-C', str(upstream), 'archive', '--format=zip', PIN])
    # Only solver build inputs, excluding upstream tools and instruction/history files.
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        for item in z.infolist():
            if item.is_dir(): continue
            if not (item.filename.startswith('src/') or item.filename in
                    ('CombatSolver.csproj', 'LICENSE', 'THIRD_PARTY_NOTICES.md')):
                continue
            target = out/item.filename
            if not target.resolve().is_relative_to(out.resolve()):
                raise ValueError('Unsafe archive member')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(item))
    file = out/'src/Search/StateFingerprint.cs'
    source = file.read_text(encoding='utf-8')
    before = hashlib.sha256(file.read_bytes()).hexdigest()
    if variant == 'fingerprint-inline':
        needle = '    public void Add(ulong value)\n'
        if source.count(needle) != 1:
            raise ValueError('Pinned fingerprint source contract changed')
        source = source.replace(needle,
            '    [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.AggressiveInlining)]\n'+needle)
        file.write_text(source, encoding='utf-8', newline='\n')
    changes = []
    if variant == 'hook-mask-index':
        from tools.solver_patches import indexed_hook_layouts
        relatives = ['src/Engine/Common/MirroredHookListenerFilter.cs',
                     'src/Engine/InCombat/Mirrors/HookMirrors.cs']
        originals = [(out/relative).read_bytes() for relative in relatives]
        contents = indexed_hook_layouts(*(data.decode('utf-8') for data in originals))
        for relative, original, content in zip(relatives, originals, contents):
            target = out/relative
            target.write_text(content, encoding='utf-8', newline='\n')
            changes.append({'path': relative, 'before_sha256': hashlib.sha256(original).hexdigest(),
                            'after_sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    if variant == 'lazy-hook-contexts':
        from tools.solver_patches import lazy_hook_contexts
        relative = 'src/Engine/InCombat/Mirrors/HookMirrors.cs'
        target = out/relative
        original = target.read_bytes()
        content, methods = lazy_hook_contexts(original.decode('utf-8'))
        target.write_text(content, encoding='utf-8', newline='\n')
        changes.append({'path': relative, 'before_sha256': hashlib.sha256(original).hexdigest(),
                        'after_sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'methods': methods})
    if variant == 'fork-capacity':
        patches = {
            'src/Engine/Common/SimCardPile.cs': [
                ('List<PredictedCard> cards = new(_cards.Count);',
                 '// Reserve the first insertion without copying an exact-length fork again.\n'
                 '        List<PredictedCard> cards = new(_cards.Count == 0 ? 0 : checked(_cards.Count + 1));')],
            'src/Search/ForkableCollections.cs': [
                ('public void Add(T value)\n    {\n        EnsureWritable();',
                 'public void Add(T value)\n    {\n        EnsureWritableForInsert();'),
                ('public void Insert(int index, T value)\n    {\n        EnsureWritable();',
                 'public void Insert(int index, T value)\n    {\n        EnsureWritableForInsert();'),
                ('    private void EnsureWritable()\n    {\n        if (!_storage.Shared)\n            return;\n        _storage = new Storage(new List<T>(_storage.Values));\n    }',
                 '    private void EnsureWritableForInsert()\n    {\n'
                 '        if (!_storage.Shared)\n            return;\n'
                 '        List<T> previous = _storage.Values;\n'
                 '        // Match the capacity the original exact copy would grow to on Add.\n'
                 '        int capacity = previous.Count == 0 ? 4 :\n'
                 '            (int)Math.Min((long)previous.Count * 2, Array.MaxLength);\n'
                 '        List<T> values = new(capacity);\n'
                 '        values.AddRange(previous);\n'
                 '        _storage = new Storage(values);\n    }\n\n'
                 '    private void EnsureWritable()\n    {\n        if (!_storage.Shared)\n            return;\n        _storage = new Storage(new List<T>(_storage.Values));\n    }')],
        }
        for relative, replacements in patches.items():
            target = out/relative
            original = target.read_bytes()
            content = original.decode('utf-8').replace('\r\n', '\n')
            for old, new in replacements:
                if content.count(old) != 1:
                    raise ValueError('Pinned fork-capacity source contract changed: '+relative)
                content = content.replace(old, new)
            target.write_text(content, encoding='utf-8', newline='\n')
            changes.append({'path': relative, 'before_sha256': hashlib.sha256(original).hexdigest(),
                            'after_sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    # Build-only changes apply equally to baseline and candidate, and physically
    # remove deployment targets. No local props, endpoint or upload secrets imported.
    project = out/'CombatSolver.csproj'
    tree = ET.parse(project)
    root = tree.getroot()
    for node in list(root):
        if node.tag == 'Import' and node.get('Project', '').startswith('./'):
            root.remove(node)
        elif node.tag == 'Target' and node.get('Name') in ('BuildMemoryCleaner', 'CleanMemoryCleaner', 'CopyMod'):
            root.remove(node)
    tree.write(project, encoding='utf-8', xml_declaration=True)
    nuget = ET.parse(ROOT/'NuGet.Config')
    for item in nuget.findall('./packageSources/add'):
        value = item.get('value', '')
        if not '://' in value and not Path(value).is_absolute():
            item.set('value', str((ROOT/value).resolve()))
    for item in nuget.findall('./config/add'):
        if item.get('key') == 'globalPackagesFolder':
            item.set('value', str((ROOT/item.get('value')).resolve()))
    nuget.write(out/'NuGet.Config', encoding='utf-8', xml_declaration=True)
    manifest = {'variant': variant, 'upstream_commit': PIN, 'upstream_archive_sha256': hashlib.sha256(archive).hexdigest(),
                'source_before_sha256': before, 'source_after_sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                'build_only_changes': ['remove local endpoint props imports', 'remove mod deployment and memory-cleaner targets'],
                'search_change': {'baseline': None, 'fingerprint-inline': 'AggressiveInlining on StateFingerprintBuilder.Add(ulong)',
                                  'fork-capacity': 'avoid duplicate backing-array allocation on forked-list insertion',
                                  'lazy-hook-contexts': 'create pure argument-copy contexts only at first actual hook listener',
                                  'hook-mask-index': 'indexed lookup of existing immutable hook masks, preserving callback order'}[variant],
                'patch_files': changes,
                'compiled': False, 'native_verified': False, 'promoted': False}
    (out/'variant.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--variant', choices=['baseline', 'fingerprint-inline', 'fork-capacity', 'lazy-hook-contexts', 'hook-mask-index'], required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--build-prepared', action='store_true')
    a = p.parse_args()
    sys.path.insert(0, str(ROOT))
    from spire_exact.native import game_data, file_hash
    from tools.fight_bench import _affinity
    _affinity('e')
    out = a.out.resolve()
    manifest = json.loads((out/'variant.json').read_text()) if a.build_prepared else prepare(a.variant, out)
    if manifest['variant'] != a.variant or manifest['compiled']:
        raise ValueError('Variant differs or is already built; never overwrite a tested binary')
    if a.prepare_only: return
    data = game_data(ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64')
    sdk = ROOT.parent/'.tools/dotnet'
    workshop = data.parents[2]/'workshop/content/2868840/3747602295'
    env = dict(os.environ, DOTNET_ROOT=str(sdk), DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1')
    env['PATH'] = str(sdk)+os.pathsep+env.get('PATH', '')
    command = [str(sdk/'dotnet.exe'), 'build', str(out/'CombatSolver.csproj'), '-c', 'Release',
               '-p:CopyModOnBuild=false', '-p:NuGetAudit=false', '-p:Sts2DataDir='+str(data),
               '-p:RitsuWorkshopRoot='+str(workshop), '-p:RitsuLibDir='+str(workshop/'compat/0.111.0'),
               '-p:SourceRevisionId='+PIN, '-p:UseSharedCompilation=false']
    with (out/'build.log').open('w', encoding='utf-8') as log:
        done = subprocess.run(command, cwd=out, env=env, stdout=log, stderr=subprocess.STDOUT)
    manifest.update(command=command, build_exit_code=done.returncode)
    dll = out/'.godot/mono/temp/bin/Release/CombatSolver.dll'
    if done.returncode == 0 and dll.is_file():
        manifest.update(compiled=True, solver=str(dll), solver_sha256=file_hash(dll), game_sha256=file_hash(data/'sts2.dll'))
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
            shutil.copy2(out/name, dll.parent/name)
    (out/'variant.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest), flush=True)
    if not manifest['compiled']: raise SystemExit(1)


if __name__ == '__main__':
    main()
