"""Pinned-source, measurement-only hooks. No pruning, ordering or state changes."""
from pathlib import Path
import hashlib
import re


def patch(out, sources):
    changes=[]
    def edit(relative, function):
        path=out/relative;old=path.read_bytes();text=old.decode('utf-8').replace('\r\n','\n')
        updated=function(text)
        if updated==text:raise ValueError('No diagnostic hook applied: '+relative)
        path.write_text(updated,encoding='utf-8',newline='\n')
        changes.append({'path':relative,'before_sha256':hashlib.sha256(old).hexdigest(),'after_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    def replace(text,old,new,count=1):
        if text.count(old)!=count:raise ValueError('Pinned diagnostic source contract changed: '+old[:90])
        return text.replace(old,new)
    def simulator(t):
        t=replace(t,'    public CombatPredictionSimulator Fork()\n    {','    public CombatPredictionSimulator Fork()\n    {\n        using var copyProbe = global::CombatSolver.CopyWorkProbe.Forking();')
        # Only this return has the trace/state/stateStore/history tuple.
        t=replace(t,'        return new CombatPredictionSimulator(\n            trace,\n            state,','        var copyFork = new CombatPredictionSimulator(\n            trace,\n            state,')
        t=replace(t,'            ShuffleEventCount,\n            ActionRelicTriggers);\n    }','            ShuffleEventCount,\n            ActionRelicTriggers);\n        copyProbe.Complete(copyFork);\n        return copyFork;\n    }')
        return t
    edit('src/Engine/InCombat/Simulation/CombatPredictionSimulator.cs',simulator)
    def execution_fork(t):
        t=replace(t,'        PredictionForkContext context, out PredictionExecutionContinuation continuation)\n    {',
            '        PredictionForkContext context, out PredictionExecutionContinuation continuation)\n    {\n        using var copyProbe = global::CombatSolver.CopyWorkProbe.Forking();')
        return replace(t,'        return child;','        copyProbe.Complete(child);\n        return child;')
    edit('src/Engine/InCombat/Simulation/CombatPredictionSimulator.ExecutionContinuation.cs',execution_fork)
    def card_fork(t):
        t=replace(t,'        out ManualCardChoiceFrame frame)\n    {',
            '        out ManualCardChoiceFrame frame)\n    {\n        using var copyProbe = global::CombatSolver.CopyWorkProbe.Forking();')
        return replace(t,'        return child;','        copyProbe.Complete(child);\n        return child;')
    edit('src/Engine/InCombat/Simulation/CombatPredictionSimulator.CardContinuation.cs',card_fork)
    def card(t):
        t=replace(t,'    internal PredictedCard Fork(PredictionForkContext context)\n    {','    internal PredictedCard Fork(PredictionForkContext context)\n    {\n        var copyPoint = global::CombatSolver.CopyWorkProbe.Start();')
        return replace(t,'        context.Register(this, fork);\n        return fork;','        context.Register(this, fork);\n        return global::CombatSolver.CopyWorkProbe.Copied(fork, "card_wrapper", copyPoint);')
    edit('src/Engine/Common/PredictedCard.cs',card)
    def power(t):
        t=replace(t,'        PowerModel fork = PredictionUtils.CloneModelForSimulation(source);','        var copyPoint = CopyWorkProbe.Start();\n        PowerModel fork = PredictionUtils.CloneModelForSimulation(source);')
        return replace(t,'        context.Register(source, fork);\n        return fork;','        context.Register(source, fork);\n        return CopyWorkProbe.Copied(fork, "power", copyPoint);')
    edit('src/Search/SimulatedCombatState.Fork.cs',power)
    # Harmony cannot patch open generic method definitions. Count their actual
    # Power field assignments at the pinned source statements instead.
    generic_methods = {'src/Search/SimulatedCombatState.cs':
        ['ApplyWithBeforeApplied', 'SetAmount', 'ApplyTargeted', 'CreatePowerForApplication', 'GetOrCreatePower'],
        'src/Search/SimulatedCombatState.CardLifecycle.cs': ['AddPowerInstance']}
    for relative, methods in generic_methods.items():
        def generic_writes(t, methods=methods):
            for name in methods:
                match = re.search(r'^    (?:public|private) [^\n]*\b'+name+r'<T>\(', t, re.M)
                if not match: raise ValueError('Pinned generic writer missing: '+name)
                end = t.index('\n    }', match.end())+6
                body = t[match.start():end]
                pattern = r'^(\s*)(\w+)\.(\w+) = ([^;\n]*);'
                stores = 0
                def store(m):
                    nonlocal stores
                    if m[3] not in ('_amount', '_owner', '_applier', '_target'): return m[0]
                    stores += 1
                    return m[0]+f'\n{m[1]}CopyWorkProbe.FieldStore({m[2]}, "{m[3]}");'
                updated = re.sub(pattern,store,body,flags=re.M)
                if not stores: raise ValueError('Pinned generic writer has no recognized stores: '+name)
                t = t[:match.start()]+updated+t[end:]
            return t
        edit(relative,generic_writes)
    def collections(t):
        types=['ForkableList<T>','ForkableDictionary<TKey, TValue>','ForkableSet<T>']
        for typ in types:
            old=f'    public {typ} Fork()\n    {{\n        _storage.Shared = true;\n        return new {typ}(_storage);\n    }}'
            new=f'    public {typ} Fork()\n    {{\n        var copyPoint = CopyWorkProbe.Start();\n        _storage.Shared = true;\n        return CopyWorkProbe.Copied(new {typ}(_storage), "forkable_collection", copyPoint);\n    }}'
            t=replace(t,old,new)
        for constructor in ['new List<T>(_storage.Values)','new Dictionary<TKey, TValue>(_storage.Values, _storage.Values.Comparer)','new HashSet<T>(_storage.Values, _storage.Values.Comparer)']:
            old=f'        _storage = new Storage({constructor});'
            t=replace(t,old,old+'\n        CopyWorkProbe.CollectionStore(this);')
        return t
    edit('src/Search/ForkableCollections.cs',collections)
    def pile(t):
        t=replace(t,'    internal SimCardPile Fork(PredictionForkContext context)\n    {','    internal SimCardPile Fork(PredictionForkContext context)\n    {\n        var copyPoint = global::CombatSolver.CopyWorkProbe.Start();')
        t=replace(t,'        context.Register(this, fork);\n        return fork;','        context.Register(this, fork);\n        return global::CombatSolver.CopyWorkProbe.Copied(fork, "card_pile", copyPoint);')
        for statement in ('_cards.Add(card);','_cards.Insert(index, card);','_cards.Clear();'):
            t=replace(t,'        '+statement,'        '+statement+'\n        global::CombatSolver.CopyWorkProbe.PileWrite(this);')
        t=replace(t,'        if (!_cards.Remove(card))\n            return false;','        if (!_cards.Remove(card))\n            return false;\n        global::CombatSolver.CopyWorkProbe.PileWrite(this);')
        return t
    edit('src/Engine/Common/SimCardPile.cs',pile)
    for relative in ('src/Search/CombatBeamSolver.Expansion.cs','src/Search/CombatBeamSolver.ParallelExpansion.cs'):
        edit(relative,lambda t:replace(t,'        _run.Expanded++;','        _run.Expanded++;\n        CopyWorkProbe.Expanded(node);'))
    def dominated(t):
        t=replace(t,'                _run.DominatedActionsPruned++;\n                candidate.Node.Snapshot.ReleaseSimulator();','                _run.DominatedActionsPruned++;\n                CopyWorkProbe.Dropped(candidate.Node, "dominance");\n                candidate.Node.Snapshot.ReleaseSimulator();')
        return replace(t,'            _run.DominatedActionsPruned++;\n            current.Node.Snapshot.ReleaseSimulator();','            _run.DominatedActionsPruned++;\n            CopyWorkProbe.Dropped(current.Node, "dominance");\n            current.Node.Snapshot.ReleaseSimulator();')
    edit('src/Search/CombatBeamSolver.Expansion.Candidates.cs',dominated)
    def candidates(t):
        return replace(t,'                candidate.Node.Snapshot.ReleaseSimulator();\n            }\n        }\n        int yieldedCandidateCount',
            '                CopyWorkProbe.Dropped(candidate.Node, "candidate_cut");\n                candidate.Node.Snapshot.ReleaseSimulator();\n            }\n        }\n        int yieldedCandidateCount')
    edit('src/Search/CombatBeamSolver.Expansion.cs',candidates)
    def transitions(t):
        return replace(t,'        bool capturingExecution = allowExecutionCapture && parentSnapshot != null && actions.Count == 1',
            '        if (parentSnapshot is not null && countTransition)\n            CopyWorkProbe.Transition(simulator, actions.Count);\n\n        bool capturingExecution = allowExecutionCapture && parentSnapshot != null && actions.Count == 1')
    edit('src/Search/CombatBeamSolver.Expansion.Replay.cs',transitions)
    def continuation(t):
        return replace(t,'                return (child, copied, copied.Steps.Select',
            '                if (!_rootSetup) CopyWorkProbe.Transition(child, 1);\n                return (child, copied, copied.Steps.Select')
    edit('src/Search/CombatBeamSolver.ExecutionChoiceContinuation.cs',continuation)
    helper=out/'src/CopyWorkProbe.cs';helper.write_bytes((sources/'CopyWorkProbe.cs').read_bytes())
    changes.append({'path':'src/CopyWorkProbe.cs','before_sha256':None,'after_sha256':hashlib.sha256(helper.read_bytes()).hexdigest()})
    return changes
