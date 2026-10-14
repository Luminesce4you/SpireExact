"""Resolve this source checkout's configured SDK without a machine-specific path."""
from pathlib import Path
import json
import os
import shutil


def sdk_path(root):
    root = Path(root).resolve()
    owner = root.parent.parent if root.parent.name == 'experiments' and root.name.startswith('frozen-') else root
    config = owner/'.tools/source-setup.json'
    setup = json.loads(config.read_text(encoding='utf-8')) if config.is_file() else {}
    candidates = []
    if setup.get('dotnet'):
        candidates.append(Path(setup['dotnet']).resolve().parent)
    candidates += [owner.parent/'.tools/dotnet', owner/'.tools/dotnet']
    if os.environ.get('DOTNET_ROOT'):
        candidates.append(Path(os.environ['DOTNET_ROOT']))
    executable = shutil.which('dotnet')
    if executable:
        candidates.append(Path(executable).resolve().parent)
    for path in candidates:
        if (path/'dotnet.exe').is_file():
            return path
    raise RuntimeError('Run tools/setup_source.py with a .NET 9 SDK before starting a solver Job')
