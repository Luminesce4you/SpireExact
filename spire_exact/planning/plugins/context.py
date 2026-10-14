"""Limited capabilities exposed to optional planner plugins.

A plugin gets this facade rather than core.Search.  The built-in mechanisms
still use Search directly because they are part of the frozen i054a baseline.
This is an API boundary for maintainability, NOT a security sandbox: a Python
plugin executes trusted code in the planner's process.
"""
from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core import Search
    from ..search import SearchConfig


class PluginContext:
    """Read-only planner facts plus one explicit request-construction capability.

    Plugins own their queues and may return Source objects, but cannot reach
    the planner's archives, allocator, repair queue or evaluator through this
    public interface.  A returned request is ordinary owned request data; the
    core continues to patch, count and dispatch it in the original order.
    """

    __slots__ = ("__search",)

    def __init__(self, search: Search) -> None:
        self.__search = search

    @property
    def config(self) -> SearchConfig:
        """The frozen configuration; plugins must not mutate nested values."""
        return self.__search.config

    @property
    def native_capabilities(self) -> frozenset[str]:
        stamp = getattr(self.__search.pool, "stamp", None) or {}
        return frozenset(stamp.get("capabilities") or ())

    @property
    def advisor(self) -> dict | None:
        """A detached advisor configuration, never the evaluator's live dict."""
        return deepcopy(self.__search.ev.advisor)

    @property
    def scoped(self) -> bool:
        return self.__search.scope_prefix is not None

    @property
    def component_mode(self) -> bool:
        return self.__search.component is not None

    @property
    def has_gate_model(self) -> bool:
        return self.__search.models is not None

    def gate_model_metrics(self) -> dict:
        """Only reporting data; no mutable access to the live gate model."""
        model = self.__search.models
        return {
            "permuted_fits": dict(getattr(model, "permuted_fits", {})),
            "model_version": getattr(model, "version", None),
        }

    def request(self, prefix: list, **kwargs: Any) -> dict:
        """Build a request using exactly the evaluator's existing semantics."""
        return self.__search.ev.request(prefix, **kwargs)
