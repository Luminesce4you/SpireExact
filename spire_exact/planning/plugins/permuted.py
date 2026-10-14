"""P2: the gate model fitted on permuted targets (iteration-106 F0-3).

A null control for the gate model. Every fit regresses the same features on
the same targets, shuffled within that fit (a fresh shuffle per fit), so the
code path, the fit schedule (entry counts), the per-request tier noise and the
request shape stay the same while the model holds no information about which
entry state passes a gate.

Consumers of the gate model under the i054a configuration and what P2 does
to each:
- prior(serial) -> rollout tiers (--prior): values from the fits -> permuted.
- best(labels, act) -> FocusScheduler option order: values from the fits ->
  permuted.
- size_z (GateModels(size=True), off by default) is fitted on the same
  permuted rows.
- family(...) reads real outcomes: refused (--focus-family, retired).
- novelty(...) reads only feature support, but --focus-optimism is refused as
  well so that the arm differs by this one mechanism.
- probe tables (--gate-probe-schedule) carry real synthetic outcomes: refused.
- snapshot() reports real outcomes (report only, no decision); predict() has
  no caller.
Everything outside the gate model (utility, the elite pool, the near-miss rule
of gate retry, ...) keeps reading real results: P2 removes the gate model's
information only.

Shuffle seed: sha256(solver_seed, kind, gate, model version), so a run is
reproducible and the level and pair fits of one refit get different shuffles.
"""
from __future__ import annotations
import hashlib
import inspect
import random

from ...canonical import ContractError
from . import Plugin


def permuted(base):
    """Subclass of the gate model class `base` whose fits see permuted targets."""

    class PermutedGateModels(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.permuted_fits = {'level': 0, 'pairs': 0}

        def _targets(self, gate, rows, kind):
            rows = super()._targets(gate, rows, kind)
            gate_id = next((key for key, value in self.gates.items() if value is gate), None)
            digest = hashlib.sha256(f'{self.solver_seed}:permute:{kind}:{gate_id}:{self.version}'.encode()).digest()
            targets = [target for _, target in rows]
            random.Random(int.from_bytes(digest[:8], 'big')).shuffle(targets)
            self.permuted_fits[kind] += 1
            return [(features, target) for (features, _), target in zip(rows, targets)]

    PermutedGateModels.__name__ = PermutedGateModels.__qualname__ = 'Permuted' + base.__name__
    return PermutedGateModels


def _fit_minimum():
    from ..gatemodel import GateModels
    return inspect.signature(GateModels).parameters['minimum'].default


class PermutedPredictor(Plugin):
    name = 'p2-permuted-predictor'
    PARAMS = {}

    def model_class(self, base):
        return permuted(base)

    def attach(self, s):
        cfg = s.config
        if s.models is None:
            raise ContractError(self.name + ': the run has no gate model (needs --prior or --scheduler focus)')
        if cfg.focus_family or cfg.focus_optimism or cfg.gate_probe_schedule:
            raise ContractError(self.name + ': --focus-family, --focus-optimism and gate probes read the gate model '
                                'outside its fits; a permuted arm must not use them')

    def metrics(self, s):
        return {'permuted_fits': dict(getattr(s.models, 'permuted_fits', {})), 'model_version': s.models.version}

    def reaches(self, result):
        """A run reached P2 when some gate had enough entries to be fitted
        while it ran; before the first fit every value is 0 in both arms."""
        snapshot = result.get('gate_models')
        if not snapshot:
            return True
        minimum = _fit_minimum()
        return any(gate.get('entries', 0) >= minimum for gate in snapshot.get('gates', []))
