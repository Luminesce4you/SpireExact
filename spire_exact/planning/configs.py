"""Named planner configurations (configs/<name>.toml) as command-line arguments.

A configuration has a [planner] table of options, keyed by the option name
without its leading dashes (true = the flag, false = left out, an integer or
string = its value, in table order), and optional [plugins.<name>] tables of
integer plugin parameters. Run identity (seed, output, prefix, imported
checkpoints) is never part of a configuration.

`--config` puts these arguments before the command line's own, so a value on
the command line overrides the configuration (argparse keeps the last one).
Overrides are for experiments; a run that names only a configuration is
that configuration.
"""
from __future__ import annotations
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[2] / 'configs'
FORBIDDEN = ('seed', 'out', 'config', 'prefix', 'import-checkpoints', 'plugin')
SECTIONS = ('name', 'description', 'planner', 'plugins')


def resolve(text):
    """A configuration file: an existing path, else configs/<text>.toml."""
    path = Path(text)
    if path.is_file():
        return path
    named = CONFIG_DIR / (text + '.toml')
    if named.is_file():
        return named
    raise ValueError(f'no configuration {text!r} (neither a file nor {named})')


def _toml(path):
    # tomllib is Python 3.11+; the planner also runs on 3.10 (tomli or toml).
    try:
        import tomllib as parser
    except ModuleNotFoundError:
        try:
            import tomli as parser
        except ModuleNotFoundError:
            import toml
            with open(path, encoding='utf-8') as handle:
                return toml.load(handle)
    with open(path, 'rb') as handle:
        return parser.load(handle)


def load(path):
    doc = _toml(path)
    unknown = sorted(set(doc) - set(SECTIONS))
    if unknown:
        raise ValueError(f'{path}: unknown section(s) {", ".join(unknown)}')
    return doc


def config_argv(path):
    """Command-line arguments of one configuration file."""
    doc = load(path)
    argv = []
    for key, value in doc.get('planner', {}).items():
        option = key.replace('_', '-')
        if option in FORBIDDEN:
            raise ValueError(f'{path}: --{option} is run identity or selection, not configuration')
        if value is True:
            argv.append('--' + option)
        elif value is False:
            continue
        elif type(value) in (int, str):
            argv += ['--' + option, str(value)]
        else:
            raise ValueError(f'{path}: {key} must be a boolean, an integer or a string')
    for name, params in doc.get('plugins', {}).items():
        if type(params) is not dict or any(type(v) is not int for v in params.values()):
            raise ValueError(f'{path}: [plugins.{name}] holds integer parameters only')
        argv += ['--plugin', name + (':' + ','.join(f'{k}={v}' for k, v in params.items()) if params else '')]
    return argv
