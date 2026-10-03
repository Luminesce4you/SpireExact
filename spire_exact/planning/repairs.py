"""Heuristic repair ordering; no failure here proves infeasibility."""
class RepairQueue:
    def __init__(self,mode='fifo'):
        if mode not in ('fifo','deep_boss','deep_final_boss','gate'):raise ValueError('unknown repair mode')
        self.mode=mode;self.items=[]
    def __bool__(self):return bool(self.items)
    def __len__(self):return len(self.items)
    def append(self,item):self.items.append(item)
    def popleft(self):
        if self.mode=='fifo':return self.items.pop(0)
        if self.mode=='gate':
            # Best source trajectory first; equal priorities keep arrival order.
            index=max(range(len(self.items)),key=lambda i:(self.items[i].get('priority',()),-i))
            return self.items.pop(index)
        def priority(pair):
            index,item=pair;meta=item.get('repair')or{}
            boss=meta.get('room')=='Boss' and (self.mode=='deep_boss' or bool(meta.get('deep')))
            # Preserve FIFO for ordinary combats. Previously discovered bosses
            # cannot hide a final-boss failure behind dozens of old probes.
            return (int(boss),int(meta.get('floor',0)) if boss else 0,-index)
        index,_=max(enumerate(self.items),key=priority)
        return self.items.pop(index)

def deep_repair(mode,entry):
    # Allocation for the current three-act campaign objective only. This is
    # not a definition of native legality/terminal state or a new game rule.
    return entry.get('room')=='Boss' and (mode=='deep_boss' or
        mode=='deep_final_boss' and entry.get('act')==2)
