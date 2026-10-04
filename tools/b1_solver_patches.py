"""Pin-checked scalar Power COW prototype; no strategy/filter/key changes."""
import hashlib


def patch(out):
    changes=[]
    def edit(relative,fn):
        path=out/relative;before=path.read_bytes();text=before.decode().replace('\r\n','\n');after=fn(text)
        if after==text:raise ValueError('No B1 patch: '+relative)
        path.write_text(after,encoding='utf-8',newline='\n');changes.append({'path':relative,'before_sha256':hashlib.sha256(before).hexdigest(),'after_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    def replace(t,a,b,n=1):
        if t.count(a)!=n:raise ValueError('Pinned B1 contract changed: '+a[:80])
        return t.replace(a,b)
    def context(t):
        t=replace(t,'    public void Register<T>(T source, T fork)\n        where T : class\n    {\n        if (ReferenceEquals(source, fork))\n            return;',
            '    public void Register<T>(T source, T fork) where T : class => RegisterCore(source, fork, false);\n\n'
            '    internal void RegisterFrozenPower(MegaCrit.Sts2.Core.Models.PowerModel source) => RegisterCore(source, source, true);\n\n'
            '    private void RegisterCore<T>(T source, T fork, bool allowIdentity) where T : class\n    {\n        if (!allowIdentity && ReferenceEquals(source, fork))\n            return;')
        return t
    edit('src/Engine/Common/PredictionForking.cs',context)
    def fork(t):
        t=replace(t,'            fork._powers = new Dictionary',
            '            fork._sharedScalarPowers = new(System.Collections.Generic.ReferenceEqualityComparer.Instance);\n            fork._powers = new Dictionary')
        t=replace(t,'                fork._powers.Add((owner, type), ForkPower(power, context));',
            '            {\n                PowerModel mapped = ForkPowerForBranch(power, context);\n                fork._powers.Add((owner, type), mapped);\n                if (ReferenceEquals(mapped, power)) fork._sharedScalarPowers.Add(power);\n            }')
        return t
    edit('src/Search/SimulatedCombatState.Fork.cs',fork)
    def state(t):
        t=replace(t,'    private PowerModel GetMutablePowerInstance(PowerModel power)\n    {',
            '    private PowerModel GetMutablePowerInstance(PowerModel power)\n    {\n        if (TryMaterializeScalarPower(power, out PowerModel? materialized)) return materialized!;')
        t=replace(t,'        if (_powers != null && _powers.TryGetValue(key, out PowerModel? simulated))\n            return simulated;',
            '        if (_powers != null && _powers.TryGetValue(key, out PowerModel? simulated))\n        {\n'
            '            // A root/old alias can resolve to a current instance frozen by a later fork.\n'
            '            return TryMaterializeScalarPower(simulated, out PowerModel? ownedPower) ? ownedPower! : simulated;\n'
            '        }')
        return replace(t,'        if (captured && _powers![key].Amount != 0)\n            return _powers[key];',
            '        if (captured && _powers![key].Amount != 0)\n        {\n            PowerModel current = _powers[key];\n            return TryMaterializeScalarPower(current, out PowerModel? materialized) ? materialized! : current;\n        }')
    edit('src/Search/SimulatedCombatState.cs',state)
    def simulator(t):
        t=replace(t,'        StateStore = new PredictionStateStore();',
            '        StateStore = new PredictionStateStore();\n        if (combatState is global::CombatSolver.SimulatedCombatState cow) cow.AttachPowerCopyStore(StateStore);')
        return replace(t,'        StateStore = stateStore;',
            '        StateStore = stateStore;\n        if (state.CombatState is global::CombatSolver.SimulatedCombatState cow) cow.AttachPowerCopyStore(StateStore);')
    edit('src/Engine/InCombat/Simulation/CombatPredictionSimulator.cs',simulator)
    relative='src/Search/SimulatedCombatState.PowerCopyOnWrite.cs'
    source=out/relative
    source.write_text(HELPER,encoding='utf-8',newline='\n')
    changes.append({'path':relative,'before_sha256':None,'after_sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    source=out/'src/Search/ScalarPowerCowContract.cs';source.write_text(CONTRACT,encoding='utf-8',newline='\n')
    changes.append({'path':'src/Search/ScalarPowerCowContract.cs','before_sha256':None,'after_sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    return changes


HELPER='''// Project-owned B1 opt-in DLL prototype. Native clone stages remain intact.
using System.Reflection;
using MegaCrit.Sts2.Core.Models;
using CombatSolver.Engine.Common;

namespace CombatSolver;

internal sealed partial class SimulatedCombatState
{
    private HashSet<PowerModel>? _sharedScalarPowers;
    private PredictionStateStore? _powerCopyStore;
    internal void AttachPowerCopyStore(PredictionStateStore store) => _powerCopyStore = store;

    // These exact native leaf types have no private per-instance fields or owned
    // subgraphs beyond PowerModel's fixed base state. Other types remain eager.
    private static bool IsScalarPower(PowerModel power)
    {
        Type type = power.GetType();
        return type.Assembly == typeof(PowerModel).Assembly && type.Name is
            "StrengthPower" or "DexterityPower" or "WeakPower" or "VulnerablePower" or "FrailPower";
    }

    private PowerModel ForkPowerForBranch(PowerModel power, PredictionForkContext context)
    {
        if (!IsScalarPower(power)) return ForkPower(power, context);
        if (context.TryRemap(power, out PowerModel? mapped)) return mapped!;
        (_sharedScalarPowers ??= new(ReferenceEqualityComparer.Instance)).Add(power);
        context.RegisterFrozenPower(power);
        return power;
    }

    private bool TryMaterializeScalarPower(PowerModel source, out PowerModel? materialized)
    {
        materialized = null;
        if (!IsScalarPower(source)) return false;
        var key = (source.Owner, source.GetType());
        if (_powers is null || !_powers.TryGetValue(key, out PowerModel? current))
            return false;
        // Resolve singleton aliases first. The current instance may itself have been
        // frozen after the caller retained an earlier root/materialized reference.
        if (_sharedScalarPowers?.Contains(current) != true)
        {
            materialized = current;
            return true;
        }
        source = current;
        PowerModel clone = PredictionUtils.CloneModelForSimulation(source);
        clone._owner = source.Owner; clone._applier = source.Applier;
        clone._target = source.Target; clone._amount = source.Amount;
        _powers[key] = clone;
        if (_powerListenerOrder is not null)
            for (int i=0;i<_powerListenerOrder.Count;i++)
                if (ReferenceEquals(_powerListenerOrder[i],source)) _powerListenerOrder[i]=clone;
        (_powerCopyStore ?? throw new InvalidOperationException("B1 Power store not attached")).RemapModel(source,clone);
        // Published listener arrays are immutable snapshots. Clear derived views;
        // next lookup rebuilds the identical ordered sequence with current models.
        InvalidateHookListeners();
        materialized = clone;
        return true;
    }
}
'''

CONTRACT='''// Native L2 ownership fixture. It mutates only private test simulators.
using System.Reflection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Powers;
using CombatSolver.Engine.Common;
using CombatSolver.Engine.InCombat.Mirrors.Hooks;

namespace CombatSolver;

public static class ScalarPowerCowContract
{
    public static object Run(CombatState combat)
    {
        var owner = combat.Players.First().Creature;
        int liveAmount = owner.GetPower<StrengthPower>()?.Amount ?? 0;
        var parent = CombatRootSnapshot.Capture(combat).ForkSimulator();
        var p = (SimulatedCombatState)parent.State.CombatState;
        p.SetAmount<StrengthPower>(owner,7);
        var before = p.GetPower<StrengthPower>(owner)!;
        parent.StateStore.GetPowerAmount(before).Amount=11;
        var first=parent.Fork(); var second=parent.Fork();
        var a=(SimulatedCombatState)first.State.CombatState;
        var b=(SimulatedCombatState)second.State.CombatState;
        Require(ReferenceEquals(before,a.GetPower<StrengthPower>(owner)) && ReferenceEquals(before,b.GetPower<StrengthPower>(owner)),"frozen sharing");
        a.SetAmount<StrengthPower>(owner,9);
        var changed=a.GetPower<StrengthPower>(owner)!;
        Require(!ReferenceEquals(changed,before),"first write materialization");
        Require(p.GetAmount<StrengthPower>(owner)==7 && b.GetAmount<StrengthPower>(owner)==7 && before.Amount==7,"sibling and parent isolation");
        Require(first.StateStore.GetPowerAmount(changed).Amount==11,"StateStore alias preserved");
        first.StateStore.GetPowerAmount(changed).Amount=13;
        Require(parent.StateStore.GetPowerAmount(before).Amount==11 && second.StateStore.GetPowerAmount(before).Amount==11,"store isolation");
        Require(a.EffectivePowers().OfType<StrengthPower>().Single(x=>ReferenceEquals(x.Owner,owner))==changed,"listener remapping");
        var grandchild=first.Fork();var g=(SimulatedCombatState)grandchild.State.CombatState;
        g.SetAmount<StrengthPower>(owner,4);
        Require(a.GetAmount<StrengthPower>(owner)==9 && g.GetAmount<StrengthPower>(owner)==4,"multigeneration isolation");
        p.SetAmount<StrengthPower>(owner,5);
        Require(b.GetAmount<StrengthPower>(owner)==7 && a.GetAmount<StrengthPower>(owner)==9,"parent mutation after fork");
        a.SnapshotPowerAmountsAtTurnStart([owner]);
        Require(a.GetPower<StrengthPower>(owner)!.AmountOnTurnStart==9 && b.GetPower<StrengthPower>(owner)!.Amount==7,"turn-start boundary");
        b.SetAmount<StrengthPower>(owner,0); b.Apply<StrengthPower>(owner,2,owner);
        Require(b.GetAmount<StrengthPower>(owner)==2 && a.GetAmount<StrengthPower>(owner)==9 && p.GetAmount<StrengthPower>(owner)==5,"remove and reacquire");
        // Retain X across X -> Y materialization and then a second fork that freezes Y.
        // A write through X must materialize the current Y, rather than mutate its child.
        var aliasChild = first.Fork();
        var aliasChildState = (SimulatedCombatState)aliasChild.State.CombatState;
        var aliasShared = a.GetPower<StrengthPower>(owner)!;
        a.SetPowerAmount(before,12);
        var aliasChanged = a.GetPower<StrengthPower>(owner)!;
        Require(a.GetAmount<StrengthPower>(owner)==12 && aliasChildState.GetAmount<StrengthPower>(owner)==9
            && p.GetAmount<StrengthPower>(owner)==5 && b.GetAmount<StrengthPower>(owner)==2,"old alias after refork isolation");
        Require(!ReferenceEquals(aliasShared,aliasChanged)
            && ReferenceEquals(aliasShared,aliasChildState.GetPower<StrengthPower>(owner)),"old alias resolves current frozen lease");
        Require(ReferenceEquals(first.StateStore.GetPowerAmount(before),first.StateStore.GetPowerAmount(aliasChanged))
            && first.StateStore.GetPowerAmount(aliasChanged).Amount==13
            && aliasChild.StateStore.GetPowerAmount(aliasShared).Amount==13,"old alias StateStore chain after refork");

        // A live root reference need not have been in this branch's frozen marker set.
        // If this combat has no Strength, a detached native reference exercises the
        // identical singleton (owner,type) alias path without changing the live owner.
        var rootAlias = owner.GetPower<StrengthPower>();
        if (rootAlias is null)
        {
            rootAlias = PredictionUtils.CloneModelForSimulation(CanonicalModels.Power<StrengthPower>());
            rootAlias._owner = owner;
        }
        var rootAliasChild = first.Fork();
        var rootAliasChildState = (SimulatedCombatState)rootAliasChild.State.CombatState;
        var rootAliasShared = a.GetPower<StrengthPower>(owner)!;
        a.SetPowerAmount(rootAlias,14);
        Require(a.GetAmount<StrengthPower>(owner)==14 && rootAliasChildState.GetAmount<StrengthPower>(owner)==12
            && aliasChildState.GetAmount<StrengthPower>(owner)==9,"root alias resolves frozen current without sibling write");
        Require(!ReferenceEquals(a.GetPower<StrengthPower>(owner),rootAliasShared)
            && ReferenceEquals(rootAliasChildState.GetPower<StrengthPower>(owner),rootAliasShared),"root alias materialization identity");

        // Weak has a native DynamicVarSet. First-write cloning must preserve its
        // values and bind each cloned DynamicVar to the new Power, not its parent.
        var weakParent = CombatRootSnapshot.Capture(combat).ForkSimulator();
        var weakParentState = (SimulatedCombatState)weakParent.State.CombatState;
        weakParentState.SetAmount<WeakPower>(owner,3);
        var weakSource = weakParentState.GetPower<WeakPower>(owner)!;
        decimal weakBase = weakSource.DynamicVars["DamageDecrease"].BaseValue;
        var weakFirst = weakParent.Fork(); var weakSecond = weakParent.Fork();
        var weakFirstState = (SimulatedCombatState)weakFirst.State.CombatState;
        var weakSecondState = (SimulatedCombatState)weakSecond.State.CombatState;
        weakFirstState.SetPowerDynamicVar(weakFirst,weakSource,"DamageDecrease",1);
        var weakChanged = weakFirstState.GetPower<WeakPower>(owner)!;
        Require(weakChanged.DynamicVars["DamageDecrease"].BaseValue==1
            && weakParentState.GetPower<WeakPower>(owner)!.DynamicVars["DamageDecrease"].BaseValue==weakBase
            && weakSecondState.GetPower<WeakPower>(owner)!.DynamicVars["DamageDecrease"].BaseValue==weakBase,"DynamicVars sibling and parent isolation");
        Require(!ReferenceEquals(weakChanged.DynamicVars,weakSource.DynamicVars)
            && !ReferenceEquals(weakChanged.DynamicVars["DamageDecrease"],weakSource.DynamicVars["DamageDecrease"]),"native DynamicVars graph cloned");
        FieldInfo dynamicOwner = typeof(MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVar)
            .GetField("_owner",BindingFlags.Instance|BindingFlags.NonPublic)
            ?? throw new InvalidOperationException("Native DynamicVar owner contract changed");
        Require(ReferenceEquals(dynamicOwner.GetValue(weakChanged.DynamicVars["DamageDecrease"]),weakChanged)
            && ReferenceEquals(dynamicOwner.GetValue(weakSource.DynamicVars["DamageDecrease"]),weakSource)
            && ReferenceEquals(weakChanged.Owner,owner),"native DynamicVar owner rebound after first write");
        Require((owner.GetPower<StrengthPower>()?.Amount??0)==liveAmount,"live unchanged");
        return new { passed=true,checks=19,scope="actual native Strength/Weak, parent/siblings/multigeneration, old/root aliases after refork, StateStore chain, listener, turn-start, reacquire and DynamicVars ownership" };
    }
    static void Require(bool test,string name) { if(!test)throw new InvalidOperationException("B1_COW_CONTRACT_FAILED: "+name); }
}
'''
