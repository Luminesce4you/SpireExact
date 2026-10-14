# Planner plugin capability API (i100-r refactor)

An optional planner plugin receives `PluginContext` at every runtime hook
(`attach`, `patch_request`, `observe`, `after_collect`, `sources`, and
`metrics`). The built-in i054a mechanisms keep their original `Search`
argument and order.

The facade offers only:

- `config`: the frozen `SearchConfig` (nested configuration values must not be changed).
- `native_capabilities`: immutable collection of host capability names.
- `advisor`: a detached copy of the evaluator's advisor settings, or `None`.
- `scoped` and `component_mode`: search restrictions.
- `has_gate_model` and `gate_model_metrics()`: availability and reporting, not the model object.
- `request(prefix, **kwargs)`: forwards to the unchanged evaluator request builder.

A plugin proposes evaluations through its own queue and a `Source` returned
by `sources(context)`; it cannot append directly to the core's urgent or
repair queues. The allocator retains its existing priority: urgent, built-in
repairs, plugin sources, strategic scheduler, restart. Hooks run in the same
order, and `model_class(base)` / `tables()` are initialization hooks that
do not receive a context.

P1 (F2 reroll) now obtains the host capabilities, advisor, and request builder
through the facade. P2 (permuted predictor) sees only gate-model availability
and a snapshot of its reporting counters.

**Scope:** this is an incremental developer-API tightening, not a secure
sandbox and not a new search algorithm. No plugin is enabled in the default
`i054a-shm` configuration. A plugin written against raw `Search` must be
adapted before registration. Golden request-for-request replay remains the
required release gate for the default configuration; P1/P2 require their own
native paired experiments before any efficacy claim.
