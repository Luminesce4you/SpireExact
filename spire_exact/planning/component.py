"""Component mode (PLAN-v2 R3): the whole-run planner, started at a recorded
door state and stopped once it has passed one gate.

The search, schedulers and plugins are the same code as in a whole run; only
three things differ, all through options the core already has:

- every request starts with the door prefix (diagnostic scope: nothing before
  the door is ever changed, Evaluator.request refuses it);
- requests stop at the first map decision on or after the gate's floor
  (stop_at_floor), so a passed gate costs no further floors;
- the run ends at the first result that passed the gate alive, instead of at
  a verified win; or, without a pass, once the search has nothing new to try:
  restarts from a door inside a fight replay the same fight (the combat solver
  does not use the policy seed), so when 2 x workers restarts in a row repeat
  a trajectory already seen and no repair is queued the run stops as
  'component_exhausted' (a stopping rule, not a proof that the gate is lost).

A door is an entry of the M1 gate library (refactor/gate-library.sqlite,
tools/codex_gate_library.py): the trace up to the first decision of a boss
fight, with the gate (A1, A2, F1, F2) that fight belongs to. The target gate
is that entry's gate unless another later gate is named.

A component result is a time and evaluation count to pass one gate in patched
offline TestMode, never a run result, a bound or production parity: result
status stays UNKNOWN unless a run win was verified on the way.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from ..canonical import ContractError, canonical
from .gatemodel import boss_fight_rows

GATES = {'A1': (0, 0), 'A2': (1, 0), 'F1': (2, 0), 'F2': (2, 1)}
LIBRARY = Path(__file__).resolve().parents[2] / 'refactor/gate-library.sqlite'
RUN_FIELDS = ('seed', 'character', 'ascension', 'unlocks')


class Component:
    """A door prefix and the gate to pass behind it."""

    def __init__(self, prefix, gate, *, stop_floor=None, run=None, source=None):
        if gate not in GATES:
            raise ContractError(f'unknown gate {gate!r}; one of {sorted(GATES)}')
        if not isinstance(prefix, list) or not prefix:
            raise ContractError('a component needs a non-empty door prefix')
        if stop_floor is not None and (type(stop_floor) is not int or stop_floor < 1):
            raise ContractError('component stop floor must be a positive integer')
        self.prefix, self.gate, self.stop_floor = deepcopy(prefix), gate, stop_floor
        self.run, self.source = dict(run or {}), dict(source or {})
        self.passed_at = None
        self.exhausted_at = None
        self.restart_traces = set()
        self.repeats = 0

    def passed(self, candidate) -> bool:
        """The candidate fought the target gate after the door and was alive after it."""
        return any(tuple(row['gate']) == GATES[self.gate] and row['index'] >= len(self.prefix) and row['lost'] is None
                   for row in boss_fight_rows(candidate))

    def observe(self, kind, candidate):
        """Count restarts in a row that repeat a trajectory after the door; any other result resets the count."""
        if kind != 'restart':
            self.repeats = 0
            return
        key = hashlib.sha256(canonical(candidate.get('trace', [])[len(self.prefix):])).hexdigest()
        if key in self.restart_traces:
            self.repeats += 1
        else:
            self.restart_traces.add(key)
            self.repeats = 0

    def exhaust(self, label, evaluator, completed, lanes, pending_repairs):
        if self.passed_at is None and self.exhausted_at is None and not pending_repairs and self.repeats >= 2 * lanes:
            self.exhausted_at = {'label': label, 'elapsed_seconds': evaluator.clock() - evaluator.started,
                                 'scheduled_evaluations': evaluator.serial, 'completed_candidates': completed,
                                 'repeated_restarts': self.repeats, 'distinct_restart_trajectories': len(self.restart_traces)}
        return self.exhausted_at

    def record(self, label, evaluator, completed):
        # evaluation_index: the passing request's place in submission order;
        # scheduled_evaluations also counts requests still in flight then.
        self.passed_at = {'label': label, 'evaluation_index': int(label.split('-')[1]),
                          'elapsed_seconds': evaluator.clock() - evaluator.started,
                          'scheduled_evaluations': evaluator.serial, 'completed_candidates': completed}
        return self.passed_at

    def describe(self):
        return {'schema': 'spire-component/v1', 'gate': self.gate, 'prefix_length': len(self.prefix),
                'stop_floor': self.stop_floor, 'run': self.run, 'source': self.source, 'passed': self.passed_at,
                'exhausted': self.exhausted_at,
                'scope': 'one gate from a recorded door in patched offline TestMode; not a run result or a bound'}


def from_entry(record, gate=None):
    """A Component for a gate-library record; `gate` may name a later gate than the entry's."""
    if record.get('schema') != 'spire-gate-entry/v1':
        raise ContractError('not a gate-library entry')
    entry_gate = record['gate']
    gate = gate or entry_gate
    if gate not in GATES or GATES[gate] < GATES[entry_gate]:
        raise ContractError(f'target gate {gate!r} is not {entry_gate} or a later gate')
    if list(record['gate_tuple']) != list(GATES[entry_gate]):
        raise ContractError('gate-library entry gate and gate tuple disagree')
    # The entry's own boss: stop at the next map after it (its floor is
    # known). A later gate's floor is not, so its requests run unbounded.
    floor = record['entry'].get('floor') if gate == entry_gate else None
    run = {k: record['recorded_request'][k] for k in RUN_FIELDS}
    source = {'library_id': record['id'], 'group': record['group'], 'run': record['run'], 'label': record['label'],
              'entry_gate': entry_gate, 'recorded_solver_seed': record.get('solver_seed'),
              'prefix_sha256': record['prefix_sha256'], 'recorded_passed': record['expected']['won'],
              'recorded_host_sha256': record['recorded_identity'].get('host_sha256')}
    return Component(record['prefix'], gate, stop_floor=int(floor) if floor is not None else None, run=run, source=source)


def load_entry(entry_id, library=LIBRARY):
    """One gate-library record (read-only)."""
    db = sqlite3.connect(f'file:{Path(library).resolve().as_posix()}?mode=ro', uri=True)
    try:
        row = db.execute('SELECT record FROM entries WHERE id=?', (entry_id,)).fetchone()
    finally:
        db.close()
    if row is None:
        raise ContractError(f'no gate-library entry {entry_id!r} in {library}')
    record = json.loads(row[0])
    if record.get('id') != entry_id:
        raise ContractError('gate-library entry id mismatch')
    return record


def check_run(component, ctx):
    """The planner context must be the run the door was recorded in."""
    different = {k: (component.run[k], ctx.get(k)) for k in RUN_FIELDS if k in component.run and component.run[k] != ctx.get(k)}
    if different:
        raise ContractError('component door belongs to another run: ' + json.dumps(different, ensure_ascii=False))
