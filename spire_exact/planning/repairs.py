"""Heuristic repair ordering; no failure here proves infeasibility."""
class RepairQueue:
    def __init__(self,mode='fifo'):
        if mode not in ('fifo','deep_boss','deep_final_boss','gate'):raise ValueError('unknown repair mode')
        self.mode=mode;self.items=[];self.deferred_picks=0;self.deferred_only_picks=0
    def __bool__(self):return bool(self.items)
    def __len__(self):return len(self.items)
    def append(self,item):self.items.append(item)
    def popleft(self,blocked=None,deferred=None,rank=None):
        # Deferred lineages remain unresolved and in the original queue. Rank
        # only eligible work; the default path keeps the existing priority.
        candidates=[i for i,item in enumerate(self.items) if blocked is None or not blocked(item)]
        if not candidates:return None
        if deferred is not None:
            preferred=[i for i in candidates if not deferred(self.items[i])]
            if preferred:
                self.deferred_picks+=int(len(preferred)<len(candidates))
                candidates=preferred
            else:
                # A weak synthetic score supplies no deletion or starvation
                # reason. With no alternative, keep the old priority/order.
                self.deferred_only_picks+=1
        if self.mode=='fifo':return self.items.pop(candidates[0])
        if self.mode=='gate':
            # Best source trajectory first; equal priorities keep arrival order.
            # i085-final01 `rank` (lower first) orders whole diagnosis classes
            # before the existing priority; None keeps the historical order.
            if rank is not None:
                index=max(candidates,key=lambda i:(-rank(self.items[i]),self.items[i].get('priority',()),-i))
            else:
                index=max(candidates,key=lambda i:(self.items[i].get('priority',()),-i))
            return self.items.pop(index)
        def priority(pair):
            index,item=pair;meta=item.get('repair')or{}
            boss=meta.get('room')=='Boss' and (self.mode=='deep_boss' or bool(meta.get('deep')))
            # Preserve FIFO for ordinary combats. Previously discovered bosses
            # cannot hide a final-boss failure behind dozens of old probes.
            return (int(boss),int(meta.get('floor',0)) if boss else 0,-index)
        index,_=max(((i,self.items[i])for i in candidates),key=priority)
        return self.items.pop(index)

def deep_repair(mode,entry):
    # Allocation for the current three-act campaign objective only. This is
    # not a definition of native legality/terminal state or a new game rule.
    return entry.get('room')=='Boss' and (mode=='deep_boss' or
        mode=='deep_final_boss' and entry.get('act')==2)
