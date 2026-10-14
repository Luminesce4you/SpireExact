"""Optional planner mechanisms (plugins).

A plugin is one mechanism that the default configuration (configs/i054a-shm.toml)
does not contain. It reaches the search only through five hooks (PLAN-v2 R1):

1. request patch   patch_request(s, spec): edit spec['request'] before it is
                   counted and dispatched (core.Search.prepare).
2. proposer        observe(s, spec, candidate, label, classification, family)
                   for every absorbed real result, after the built-in mechanisms;
                   after_collect(s, trials) once per collected batch, after Deepen.
                   A proposer pushes request specs to s.urgent, s.repairs or a
                   queue of its own that one of its sources pops.
3. source          sources(s): extra core.Source objects for the allocator,
                   placed after the failure repairs and before the strategic
                   scheduler; a source may hold a share of everything scheduled.
4. model provider  model_class(base): a subclass of the gate model class to use
                   instead of `base` (the hook inside the model is
                   GateModels._targets).
5. policy table    tables(): {name: tier table}, registered as root policies
                   (policies.register) before the root policies are validated.

No scheduler-internal hooks. Without plugins the search is frozen-i054a-shm
(tools/replay_golden.py). With plugins the report gains `plugins` (each
plugin's describe()) and search_metrics.plugins (each plugin's metrics()).

A plugin is selected by `--plugin NAME[:key=value,...]` or a [plugins.NAME]
table of a named config (configs.py); its parameters are the integers it
declares in PARAMS. Every plugin is listed in registry.PLUGINS with its status
and evidence; load() only builds plugins listed there.
"""
from __future__ import annotations
import importlib


class Plugin:
    name = ''
    # Parameter -> integer default. Parameters are integers only, so that a
    # plugin's whole configuration is canonical request-safe data.
    PARAMS = {}

    def __init__(self, **params):
        unknown = sorted(set(params) - set(self.PARAMS))
        if unknown:
            raise ValueError(f'plugin {self.name}: unknown parameter(s) {", ".join(unknown)}')
        self.params = {**self.PARAMS, **params}
        for key, value in self.params.items():
            if type(value) is not int:
                raise ValueError(f'plugin {self.name}: parameter {key} must be an integer')

    # Hooks (no-ops by default) -------------------------------------------

    def attach(self, s):
        """Called once by core.Search after the built-in mechanisms exist and
        before the allocator is built. Refuse unsupported searches here."""

    def patch_request(self, s, spec):
        """Request-patch hook. Edit spec['request'] in place; never mutate an
        object the request shares with others (replace it with a copy)."""

    def observe(self, s, spec, candidate, label, classification, family):
        """Proposer hook, per absorbed real result (never synthetic probes)."""

    def after_collect(self, s, trials):
        """Proposer hook, once per collected batch."""

    def sources(self, s):
        """Allocator sources (core.Source) this plugin adds."""
        return ()

    def model_class(self, base):
        """Model-provider hook: the gate model class to use instead of `base`."""
        return base

    def tables(self):
        """Root-policy-table hook: {policy name: tier table}."""
        return {}

    # Reporting -----------------------------------------------------------

    def metrics(self, s):
        """Plugin counters for search_metrics.plugins[name]."""
        return {}

    def reaches(self, result):
        """Whether a recorded run reached this plugin's scope, i.e. whether the
        plugin could have changed that run's behaviour (PLAN-v2 R2a, no-run
        rule). The default is conservative: every run."""
        return True

    def describe(self):
        return {'name': self.name, 'params': dict(sorted(self.params.items()))}


def parse(text):
    """'NAME[:key=value,...]' -> (NAME, {key: int})."""
    name, _, rest = str(text).partition(':')
    name = name.strip()
    if not name:
        raise ValueError('plugin: empty name in ' + repr(text))
    params = {}
    for item in filter(None, (part.strip() for part in rest.split(','))):
        key, sep, value = item.partition('=')
        key = key.strip().replace('-', '_')
        if not sep or not key:
            raise ValueError(f'plugin {name}: parameter {item!r} must be key=value')
        if key in params:
            raise ValueError(f'plugin {name}: parameter {key} given twice')
        try:
            params[key] = int(value)
        except ValueError:
            raise ValueError(f'plugin {name}: parameter {key} must be an integer') from None
    return name, params


def load(texts):
    """Plugin instances for `--plugin` values, in the given order. Only plugins
    listed in registry.PLUGINS can be built; a plugin can be given once."""
    from ..registry import PLUGINS
    plugins, seen = [], set()
    for text in texts:
        name, params = parse(text)
        if name in seen:
            raise ValueError('plugin given twice: ' + name)
        seen.add(name)
        entry = PLUGINS.get(name)
        if entry is None:
            raise ValueError('unknown plugin: ' + name + ' (known: ' + ', '.join(sorted(PLUGINS)) + ')')
        module, _, attribute = entry.target.partition(':')
        cls = getattr(importlib.import_module(module), attribute)
        if cls.name != name:
            raise ValueError(f'registry entry {name} points at plugin {cls.name}')
        plugins.append(cls(**params))
    return plugins


def register_tables(plugins):
    """Registers every plugin's root policy tables (policies.register)."""
    from ..policies import register
    for plugin in plugins:
        for name, table in sorted(plugin.tables().items()):
            register(name, table)
