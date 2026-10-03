"""Create a private, portable continuation snapshot without touching game/mod/save files."""
from pathlib import Path
import hashlib, json, shutil, subprocess, sys

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'deliverables' / 'cloud-stage' / 'STS2'
GAME = Path('D:/SteamLibrary/steamapps/common/Slay the Spire 2')
WORKSHOP = Path('D:/SteamLibrary/steamapps/workshop/content/2868840/3747602295')

def copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

def main():
    if DEST.exists():
        raise SystemExit('Refusing to overwrite an existing snapshot: ' + str(DEST))
    excluded = {'.git', '.tools', 'vendor', 'deliverables', '__pycache__', 'bin', 'obj'}
    count = 0
    for src in ROOT.rglob('*'):
        rel = src.relative_to(ROOT)
        if not src.is_file() or set(rel.parts) & excluded or src.suffix.lower() in {'.pyc', '.dll', '.exe', '.pdb'}:
            continue
        if src.name == 'local.json' or src.name.startswith('.env'):
            continue
        copy(src, DEST / rel)
        count += 1
    upstream = ROOT / 'vendor/CombatSolver'
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=upstream).decode().split('\0')
    for name in names:
        if name and (upstream/name).is_file():
            copy(upstream/name, DEST/'vendor/CombatSolver'/name)
    # Preserve the shallow pinned commit, index and two local changes; omit hooks,
    # logs, remotes, credentials, and local Git configuration.
    git_src, git_dst = upstream/'.git', DEST/'vendor/CombatSolver/.git'
    for name in ('objects', 'refs'):
        if (git_src/name).exists(): shutil.copytree(git_src/name, git_dst/name)
    for name in ('HEAD', 'index', 'shallow', 'packed-refs'):
        if (git_src/name).is_file(): copy(git_src/name, git_dst/name)
    (git_dst/'config').write_text('[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tautocrlf = true\n\tfilemode = false\n', encoding='utf-8')
    data = GAME/'data_sts2_windows_x86_64'
    target = DEST/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    assemblies = ['sts2.dll','GodotSharp.dll','0Harmony.dll','JetBrains.Annotations.dll',
        'MonoMod.Backports.dll','MonoMod.ILHelpers.dll','System.IO.Hashing.dll','Sentry.dll','Sentry.Godot.dll',
        'SharpGen.Runtime.COM.dll','SharpGen.Runtime.dll','SmartFormat.dll','SmartFormat.ZString.dll',
        'Steamworks.NET.dll','Vortice.DirectX.dll','Vortice.DXGI.dll','Vortice.Mathematics.dll',
        'sts2.xml','sts2.deps.json']
    for name in assemblies: copy(data/name, target/name)
    copy(GAME/'release_info.json', target.parent/'release_info.json')
    workshop_dst = DEST/'runtime/steamapps/workshop/content/2868840/3747602295'
    for folder in ('compat/0.111.0', 'shared'):
        for src in (WORKSHOP/folder).iterdir():
            if src.suffix.lower() in {'.dll','.xml','.txt'}: copy(src, workshop_dst/folder/src.name)
    copy(WORKSHOP/'RitsuLib.References.props', workshop_dst/'RitsuLib.References.props')
    # Existing investigation material, labelled as extracted game reference data.
    for src in (ROOT/'.tools/decompiled').glob('*.cs'):
        copy(src, DEST/'research/decompiled'/src.name)
    # Local NuGet feed for builds when outbound access is unavailable.
    packages = Path.home()/'.nuget/packages'
    for src in packages.rglob('*.nupkg'):
        copy(src, DEST/'cloud/nuget-feed'/src.name)
    source_hashes = {p.relative_to(DEST).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for folder in ('spire_exact','native','tests') for p in (DEST/folder).rglob('*') if p.is_file()}
    (DEST/'cloud/source-snapshot.json').write_text(json.dumps(source_hashes, indent=2)+'\n')
    print(json.dumps({'snapshot':str(DEST),'project_files':count,'all_files':sum(p.is_file() for p in DEST.rglob('*'))}))

if __name__ == '__main__': main()
