"""i100 auxiliary-work governor. Allocation only, nothing is dropped.

i082 queues several kinds of auxiliary work in the FIFO `urgent` queue ahead of
every gate retry and focus/explorer pick: paired card tables (five synthetic
batches at once, from the very first final-F1 entry), isolated shop/low-HP
route consumers and forge alternatives (fresh process, full replay without a
checkpoint), F1-winner reuse and real-card-menu work. holdout-01 (i054a, 17/20
at 30 minutes) had none of them. On a seed that is still progressing they
compete with the core search for the same workers.

The governor keeps such items in the queue, unchanged and in order, while
their dispatched share exceeds a cap: a small quiet cap until the gate clinic
reports a contested furthest gate, a larger one afterwards. Core items
(deepen, root/restart, requeued core work, memory-prefix recovery, macro picks,
gate retries) are never held. A held item is dispatched as soon as the share
allows; no proposal is deleted or reordered relative to its peers.
"""
from __future__ import annotations

from collections import Counter

from .preparation import ROUTE_KINDS

AUX_KINDS = frozenset(('paired_card_probe', 'f1_winner_reuse', 'macro_forge', 'real_card_menu_probe',
                       'real_card_menu_followup', 'gate_probe', *ROUTE_KINDS))


class AuxGovernor:
    def __init__(self, enabled: bool = False, *, quiet: int = 10, contended: int = 30, contended_fn=None):
        if type(enabled) is not bool or any(type(v) is not int or not 0 <= v <= 100 for v in (quiet, contended)):
            raise ValueError('invalid auxiliary governor settings')
        self.enabled = enabled
        self.quiet, self.contended = quiet / 100.0, contended / 100.0
        self.contended_fn = contended_fn or (lambda: False)
        self.aux = 0
        self.total = 0
        self.held = Counter()
        self.kinds = Counter()
        self.contended_dispatches = 0

    def share(self) -> float:
        return self.contended if self.contended_fn() else self.quiet

    def allow(self, spec) -> bool:
        if not self.enabled or spec.get('kind') not in AUX_KINDS:
            return True
        if self.aux + 1 <= self.share() * (self.total + 1):
            return True
        self.held[spec['kind']] += 1
        return False

    def record(self, spec) -> None:
        self.total += 1
        if spec.get('kind') in AUX_KINDS:
            self.aux += 1
            self.kinds[spec['kind']] += 1
            self.contended_dispatches += int(self.contended_fn())

    def snapshot(self) -> dict:
        return {'enabled': self.enabled, 'quiet_share_percent': round(100 * self.quiet),
                'contended_share_percent': round(100 * self.contended), 'auxiliary_dispatched': self.aux,
                'all_dispatched': self.total, 'auxiliary_by_kind': dict(self.kinds),
                'hold_decisions_by_kind': dict(self.held), 'auxiliary_dispatched_while_contended': self.contended_dispatches,
                'scope': 'dispatch share of auxiliary proposals; held items stay queued in order, nothing is dropped'}
