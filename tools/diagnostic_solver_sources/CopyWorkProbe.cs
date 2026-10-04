// Project-owned measurement code. Pinned upstream sources and licenses are preserved by the builder.
using System.Collections;
using System.Diagnostics;
using System.Reflection;
using System.Reflection.Emit;
using System.Runtime.CompilerServices;
using HarmonyLib;
using MegaCrit.Sts2.Core.Models;
using CombatSolver.Engine.Common;
using CombatSolver.Engine.InCombat.Simulation;

namespace CombatSolver;

/// <summary>Measurement only. Never changes a state key, candidate, budget or retained set.</summary>
public static class CopyWorkProbe
{
    public readonly record struct Point(long Ticks, long Bytes);
    sealed class Part(Record record, string kind, string type)
    {
        public readonly Record Record = record;
        public readonly string Kind = kind, Type = type;
        public bool Written, WrittenBeforeSnapshot, PossibleGraphWrite, WrittenIncludingFork;
        public long Ticks, Bytes;
    }
    sealed class Owners { public readonly List<Part> Parts = []; }
    sealed class Record
    {
        public int Id; public bool Constructing = true, Frozen, Expanded, Released;
        public long Ticks, Bytes, Transitions, ProbeTicks, ProbeBytes; public string? Drop; public bool Terminal, HasNode;
        public readonly Dictionary<string, long> Copies = [], Written = [], Before = [], Possible = [], CopyTicks = [], CopyBytes = [], WrittenTicks = [], UncertainTicks = [], AllWritten = [], AllWrittenTicks = [];
    }
    sealed class NodeRecord(SearchNode node, Record? fork)
    {
        public readonly WeakReference<SimulationSnapshot> Snapshot = new(node.Snapshot);
        public readonly Record? Fork = fork;
        public readonly bool Root = node.Parent is null, Terminal = node.IsTerminal;
        public bool Expanded; public string? Drop;
    }
    sealed class Session
    {
        public readonly ConditionalWeakTable<object, Owners> Owners = new();
        public readonly ConditionalWeakTable<CombatPredictionSimulator, Record> Simulators = new();
        public readonly ConditionalWeakTable<SimulationSnapshot, Record> Snapshots = new();
        public readonly ConditionalWeakTable<SearchNode, NodeRecord> Nodes = new();
        public readonly List<Record> Forks = [];
        public readonly List<NodeRecord> Born = [];
        public readonly Dictionary<string, long> WriteSites = [], UnknownGraphs = [];
        public long RawWrites, InitializationWrites, ExpansionEvents, RootExpansionEvents, LateSnapshots, AliasChanges, UnattributedTransitions;
    }
    static readonly BindingFlags All = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
    static readonly object Sync = new();
    static bool enabled, patched, recordWrites = true;
    static Session? session;
    [ThreadStatic] static Record? constructing;
    [ThreadStatic] static int retentionDepth;
    static readonly Dictionary<string, int> patchCounts = [];
    static readonly List<string> unsupported = [];
    static readonly Dictionary<ushort, OpCode> opcodes = typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static)
        .Where(f => f.FieldType == typeof(OpCode)).Select(f => (OpCode)f.GetValue(null)!).ToDictionary(o => unchecked((ushort)o.Value));

    public static void SetMode(string mode)
    {
        if (mode is not ("writes" or "costs")) throw new ArgumentException("Unknown copy measurement mode");
        if (session is not null) throw new InvalidOperationException("Cannot change copy measurement mode within a search");
        recordWrites = mode == "writes";
    }

    static IEnumerable<FieldInfo> StoredFields(MethodBase method)
    {
        // Read metadata directly. Harmony's dynamic IL reader cannot import an open
        // generic signature; those writer sites must be reported, not silently skipped.
        byte[] code = method.GetMethodBody()!.GetILAsByteArray()!;
        int at = 0;
        while (at < code.Length)
        {
            ushort value = code[at++]; if (value == 0xfe) value = (ushort)(0xfe00 | code[at++]);
            OpCode opcode = opcodes[value];
            if (opcode == OpCodes.Stfld)
                yield return method.Module.ResolveField(BitConverter.ToInt32(code, at),
                    method.DeclaringType?.GetGenericArguments(), method is MethodInfo info ? info.GetGenericArguments() : null)!;
            at += opcode.OperandType switch {
                OperandType.InlineNone => 0,
                OperandType.ShortInlineBrTarget or OperandType.ShortInlineI or OperandType.ShortInlineVar => 1,
                OperandType.InlineVar => 2,
                OperandType.InlineI8 or OperandType.InlineR => 8,
                OperandType.InlineSwitch => 4 + 4 * BitConverter.ToInt32(code, at),
                _ => 4 };
        }
    }

    public static void Configure(bool value)
    {
        enabled = value;
        if (!value || patched) return;
        var harmony = new Harmony("SpireExact.CopyWorkProbe.Measurement");
        // Patch real stores in game/solver IL. Construction targets are registered only
        // after creation; writes during their enclosing Fork are counted separately.
        if(recordWrites)
        foreach (Assembly assembly in new[] { typeof(PowerModel).Assembly, typeof(CopyWorkProbe).Assembly })
        foreach (Type type in assembly.GetTypes())
        {
            if (IsProbe(type)) continue;
            IEnumerable<MethodBase> methods = type.GetMethods(All | BindingFlags.DeclaredOnly).Cast<MethodBase>()
                .Concat(type.GetConstructors(All | BindingFlags.DeclaredOnly));
            foreach (MethodBase method in methods)
            {
                if (method.IsAbstract || method.GetMethodBody() is null) continue;
                bool candidate = StoredFields(method).Any(Watched);
                if (!candidate) continue;
                if (method.ContainsGenericParameters)
                {
                    if (method.DeclaringType == typeof(SimulatedCombatState) && method.Name is
                        "AddPowerInstance" or "ApplyWithBeforeApplied" or "SetAmount" or "ApplyTargeted" or "CreatePowerForApplication" or "GetOrCreatePower") continue;
                    unsupported.Add(method.DeclaringType + "." + method.Name); continue;
                }
                harmony.Patch(method, transpiler: new HarmonyMethod(typeof(CopyWorkProbe), nameof(Stores)));
                string name = assembly.GetName().Name!; patchCounts[name] = patchCounts.GetValueOrDefault(name) + 1;
            }
        }
        Patch(harmony, typeof(CombatBeamSolver).GetMethod("Snapshot", All)!, nameof(SnapshotBefore), nameof(SnapshotAfter));
        foreach (ConstructorInfo ctor in typeof(SearchNode).GetConstructors(All))
            Patch(harmony, ctor, null, ctor.GetParameters().Any(p => p.ParameterType == typeof(SimulationSnapshot)) ? nameof(NodeBorn) : nameof(NodeCopied));
        Patch(harmony, typeof(SimulationSnapshot).GetMethod("ReleaseSimulator", All)!, nameof(Released), null);
        Patch(harmony, typeof(CombatBeamSolver).GetMethod("TryAcceptTransposition", All)!, null, nameof(Admission));
        Patch(harmony, typeof(CombatBeamSolver).GetMethod("TryMarkExpandedState", All)!, null, nameof(ExpansionAdmission));
        var prune = typeof(CombatBeamSolver).GetMethods(All).Single(m => m.Name == "Prune");
        harmony.Patch(prune, prefix: new HarmonyMethod(typeof(CopyWorkProbe), nameof(RetentionStart)),
            finalizer: new HarmonyMethod(typeof(CopyWorkProbe), nameof(RetentionEnd)));
        // Same-value write test exercises exactly the emitted stfld hook, without game models.
        if(recordWrites) harmony.Patch(typeof(CopyWorkProbe).GetMethod(nameof(TestStore), All)!, transpiler: new HarmonyMethod(typeof(CopyWorkProbe), nameof(Stores)));
        patched = true;
        if(recordWrites) SelfTest();
    }
    static bool IsProbe(Type type) => type == typeof(CopyWorkProbe) || type.DeclaringType is {} parent && IsProbe(parent);
    static bool Watched(FieldInfo field)
    {
        Type type = field.DeclaringType!;
        return !type.IsValueType && (typeof(PowerModel).IsAssignableFrom(type) || type.IsAssignableFrom(typeof(PowerModel))
            || type == typeof(PredictedCard) || type == typeof(TestCell)
            || type.Namespace?.Contains("Localization.DynamicVars") == true);
    }
    static void Patch(Harmony h, MethodBase m, string? before, string? after) => h.Patch(m,
        prefix: before is null ? null : new HarmonyMethod(typeof(CopyWorkProbe), before),
        postfix: after is null ? null : new HarmonyMethod(typeof(CopyWorkProbe), after));
    static IEnumerable<CodeInstruction> Stores(IEnumerable<CodeInstruction> code, ILGenerator generator)
    {
        var hook = typeof(CopyWorkProbe).GetMethod(nameof(RawWrite), All)!;
        var instructions = code.ToList();
        for (int index = 0; index < instructions.Count; index++)
        {
            var instruction = instructions[index];
            var prefixes = new List<CodeInstruction>();
            if (instruction.opcode == OpCodes.Volatile || instruction.opcode == OpCodes.Unaligned)
            {
                while (index < instructions.Count && (instructions[index].opcode == OpCodes.Volatile || instructions[index].opcode == OpCodes.Unaligned)) prefixes.Add(instructions[index++]);
                if (index == instructions.Count) throw new InvalidOperationException("Invalid trailing IL prefix");
                instruction = instructions[index];
            }
            if (instruction.opcode != OpCodes.Stfld || instruction.operand is not FieldInfo f || !Watched(f))
            { foreach (var prefix in prefixes) yield return prefix; yield return instruction; continue; }
            var value = generator.DeclareLocal(f.FieldType);
            var target = generator.DeclareLocal(f.DeclaringType!);
            var first = new CodeInstruction(OpCodes.Stloc, value);
            foreach (var prefix in prefixes) { first.labels.AddRange(prefix.labels); prefix.labels.Clear(); }
            first.labels.AddRange(instruction.labels); instruction.labels.Clear();
            first.blocks.AddRange(instruction.blocks.Where(b => b.blockType != ExceptionBlockType.EndExceptionBlock));
            instruction.blocks.RemoveAll(b => b.blockType != ExceptionBlockType.EndExceptionBlock);
            yield return first;
            yield return new CodeInstruction(OpCodes.Dup);
            yield return new CodeInstruction(OpCodes.Stloc, target);
            yield return new CodeInstruction(OpCodes.Ldloc, value);
            foreach (var prefix in prefixes) yield return prefix;
            yield return instruction; // original assignment; a throwing store never reaches the hook
            yield return new CodeInstruction(OpCodes.Ldloc, target);
            yield return new CodeInstruction(OpCodes.Ldstr, f.DeclaringType!.FullName + "." + f.Name);
            yield return new CodeInstruction(OpCodes.Call, hook);
        }
    }
    public static void BeginSearch()
    {
        if (!enabled) return;
        session = new Session(); constructing = null; retentionDepth = 0;
    }
    public static Point Start() => enabled && session is not null ? new(Stopwatch.GetTimestamp(), GC.GetAllocatedBytesForCurrentThread()) : default;
    public sealed class ForkScope : IDisposable
    {
        readonly Record? old; readonly Record? current; readonly Point start;
        internal ForkScope(bool active = true)
        {
            if (!active) return;
            old = constructing;
            if (!enabled || session is null) return;
            current = new Record { Id = session.Forks.Count };
            session.Forks.Add(current); constructing = current;
            // Exclude allocating this diagnostic record and growing its list.
            start = Start();
        }
        internal void Complete(CombatPredictionSimulator simulator)
        {
            if (current is null || session is null) return;
            current.Ticks = Stopwatch.GetTimestamp() - start.Ticks;
            current.Bytes = GC.GetAllocatedBytesForCurrentThread() - start.Bytes;
            current.Constructing = false;
            session.Simulators.Add(simulator, current);
        }
        public void Dispose() { constructing = old; }
    }
    static readonly ForkScope InactiveFork = new(false);
    public static ForkScope Forking() => enabled && session is not null ? new() : InactiveFork;
    public static T Copied<T>(T target, string kind, Point point) where T : class
    {
        if (!enabled || session is null || constructing is null) return target;
        Record record = constructing;
        Add(record.Copies, kind, 1);
        long ticks = Stopwatch.GetTimestamp() - point.Ticks;
        long bytes = GC.GetAllocatedBytesForCurrentThread() - point.Bytes;
        Point bookkeeping = Start();
        Add(record.CopyTicks, kind, ticks);
        Add(record.CopyBytes, kind, bytes);
        var part = new Part(record, kind, target.GetType().FullName!);
        part.Ticks = ticks; part.Bytes = bytes;
        var owners = session.Owners.GetOrCreateValue(target); owners.Parts.Add(part);
        if (recordWrites && target is PowerModel) RegisterPowerGraph(target, part);
        record.ProbeTicks += Stopwatch.GetTimestamp() - bookkeeping.Ticks;
        record.ProbeBytes += GC.GetAllocatedBytesForCurrentThread() - bookkeeping.Bytes;
        return target;
    }
    static void Add(Dictionary<string, long> counts, string key, long value) => counts[key] = counts.GetValueOrDefault(key) + value;
    static void RegisterPowerGraph(object power, Part part)
    {
        // Only owned DynamicVar objects are watched beyond the model itself. Other
        // reference-bearing fields remain explicit coverage uncertainty; final-value
        // equality is never evidence of no write.
        for (Type? type = power.GetType(); type is not null; type = type.BaseType)
        foreach (FieldInfo field in type.GetFields(All | BindingFlags.DeclaredOnly).Where(f => !f.IsStatic))
        {
            object? value = field.GetValue(power);
            if (value is null || field.FieldType.IsValueType || value is string || value is Delegate) continue;
            string ns = value.GetType().Namespace ?? "";
            if (ns.Contains("Localization.DynamicVars"))
            {
                session!.Owners.GetOrCreateValue(value).Parts.Add(part);
                // DynamicVars derives from a dictionary. Walk dictionary values without
                // invoking model getters; an unsupported shape is retained as uncertainty.
                object? dictionaryObject = value is IDictionary ? value : value.GetType().GetField("_vars", All)?.GetValue(value);
                if (dictionaryObject is IDictionary dictionary)
                {
                    foreach (object? child in dictionary.Values)
                        if (child is not null && child.GetType().Namespace?.Contains("Localization.DynamicVars") == true)
                            session.Owners.GetOrCreateValue(child).Parts.Add(part);
                }
                else Unknown(part, field.Name);
            }
            else if (value is IEnumerable && value is not AbstractModel)
                Unknown(part, field.Name);
        }
    }
    static void Unknown(Part part, string field)
    {
        Add(session!.UnknownGraphs, part.Type + ":" + field, 1);
        if (!part.PossibleGraphWrite) { part.PossibleGraphWrite = true; Add(part.Record.Possible, part.Kind, 1); Add(part.Record.UncertainTicks, part.Kind, part.Ticks); }
    }
    static void RawWrite(object target, string field)
    {
        Session? s = session;
        if (!enabled || !recordWrites || s is null || !s.Owners.TryGetValue(target, out var owners)) return;
        lock (Sync)
        foreach (Part part in owners.Parts)
        {
            // Link/observer stores after a copied object is registered are real
            // writes too, even if the enclosing simulator Fork has not returned.
            if (!part.WrittenIncludingFork) { part.WrittenIncludingFork = true; Add(part.Record.AllWritten, part.Kind, 1); Add(part.Record.AllWrittenTicks, part.Kind, part.Ticks); }
            if (part.Record.Constructing) { s.InitializationWrites++; continue; }
            s.RawWrites++; Add(s.WriteSites, field, 1);
            if (!part.Written) { part.Written = true; Add(part.Record.Written, part.Kind, 1); Add(part.Record.WrittenTicks, part.Kind, part.Ticks); }
            if (!part.Record.Frozen && !part.WrittenBeforeSnapshot)
            { part.WrittenBeforeSnapshot = true; Add(part.Record.Before, part.Kind, 1); }
        }
    }
    public static void CollectionStore(object target) => RawWrite(target, "storage_cow_field_assignment");
    public static void PileWrite(object target) => RawWrite(target, "pile_successful_list_mutation");
    public static void FieldStore(object target, string field) => RawWrite(target, "generic_power_source_store:"+field);
    internal static void Transition(CombatPredictionSimulator simulator, int count)
    {
        if (!enabled || session is null) return;
        if (session.Simulators.TryGetValue(simulator, out var record)) record.Transitions += count;
        else session.UnattributedTransitions += count;
    }
    internal static void Expanded(SearchNode node)
    {
        if (!enabled || session is null) return;
        NodeRecord n = Node(node); n.Expanded = true;
        session.ExpansionEvents++; if (n.Root) session.RootExpansionEvents++;
        if (n.Fork is {} r) r.Expanded = true;
    }
    internal static void Dropped(SearchNode node, string reason)
    {
        if (!enabled || session is null) return;
        var n = Node(node); n.Drop ??= reason;
        if (n.Fork is {} r) r.Drop ??= reason;
    }
    static NodeRecord Node(SearchNode node)
    {
        var s = session!;
        if (s.Nodes.TryGetValue(node, out var n))
        {
            if (n.Snapshot.TryGetTarget(out var oldSnapshot) && ReferenceEquals(oldSnapshot, node.Snapshot)) return n;
            s.AliasChanges++; s.Nodes.Remove(node);
        }
        s.Snapshots.TryGetValue(node.Snapshot, out var fork);
        n = new(node, fork); s.Nodes.Add(node, n); s.Born.Add(n);
        if (fork is not null) { fork.HasNode = true; fork.Terminal |= node.IsTerminal; }
        return n;
    }
    static void NodeBorn(object __instance) { if (enabled && session is not null) _ = Node((SearchNode)__instance); }
    static void NodeCopied(object __instance, object[] __args)
    {
        if (!enabled || session is null || __args.Length != 1 || __args[0] is not SearchNode old) return;
        var copy = (SearchNode)__instance;
        if (session.Nodes.TryGetValue(old, out var n)) session.Nodes.Add(copy, n);
    }
    static void SnapshotBefore(CombatPredictionSimulator simulator)
    { if (session?.Simulators.TryGetValue(simulator, out var r) == true) r.Frozen = true; }
    static void SnapshotAfter(CombatPredictionSimulator simulator, SimulationSnapshot __result)
    {
        if (session?.Simulators.TryGetValue(simulator, out var r) != true) return;
        if (!r.Frozen) { session.LateSnapshots++; r.Frozen = true; }
        session.Snapshots.Add(__result, r);
    }
    static void Released(SimulationSnapshot __instance, string caller)
    {
        if (session?.Snapshots.TryGetValue(__instance, out var r) != true) return;
        r.Frozen = true; r.Released = true;
        if (retentionDepth > 0 || caller == "ReleaseDroppedSnapshots") r.Drop ??= "layer_retention";
    }
    static void Admission(SearchNode candidate, bool __result) { if (!__result) Dropped(candidate, "transposition_admission"); }
    static void ExpansionAdmission(SearchNode node, bool __result) { if (!__result) Dropped(node, "transposition_expansion"); }
    static void RetentionStart() { retentionDepth++; }
    static Exception? RetentionEnd(Exception? __exception) { retentionDepth--; return __exception; }
    static string Fate(Record r) => r.Expanded ? "expanded" : r.Drop ?? (r.Terminal ? "terminal" : r.HasNode ? "frontier_budget_or_other" : "auxiliary_or_pre_node");
    static string Fate(NodeRecord r) => r.Expanded ? "expanded" : r.Drop ?? r.Fork?.Drop ?? (r.Terminal ? "terminal" : "frontier_budget_or_other");
    public static object EndSearch()
    {
        Session s = session ?? throw new InvalidOperationException("Copy diagnostic session absent");
        var groups = s.Forks.GroupBy(Fate).ToDictionary(g => g.Key, g => new {
            forks = g.Count(), counted_transitions = g.Sum(r => r.Transitions), copies = Sum(g, r => r.Copies), actual_written_lifetime = Sum(g, r => r.Written),
            actual_written_before_snapshot = Sum(g, r => r.Before), measured_copy_ticks_inclusive = Sum(g, r => r.CopyTicks),
            actual_written_including_fork_links = Sum(g, r => r.AllWritten), actual_written_including_fork_copy_ticks = Sum(g, r => r.AllWrittenTicks),
            power_reference_graph_uncertainty = Sum(g, r => r.Possible),
            actual_written_copy_ticks_inclusive = Sum(g, r => r.WrittenTicks), uncertain_copy_ticks_inclusive = Sum(g, r => r.UncertainTicks),
            measured_copy_allocated_bytes_inclusive = Sum(g, r => r.CopyBytes),
            instrumented_fork_ticks = g.Sum(r => r.Ticks), instrumented_fork_bytes = g.Sum(r => r.Bytes),
            observed_probe_bookkeeping_ticks = g.Sum(r => r.ProbeTicks), observed_probe_bookkeeping_bytes = g.Sum(r => r.ProbeBytes),
            fork_ticks_excluding_observed_bookkeeping = g.Sum(r => r.Ticks-r.ProbeTicks),
            fork_bytes_excluding_observed_bookkeeping = g.Sum(r => r.Bytes-r.ProbeBytes) });
        var relation = s.Forks.GroupBy(r => (cards: r.Copies.GetValueOrDefault("card_wrapper"), powers: r.Copies.GetValueOrDefault("power")))
            .Select(g => new { cards = g.Key.cards, powers = g.Key.powers, forks = g.Count(), mean_instrumented_ticks = g.Average(r => (double)r.Ticks), mean_instrumented_bytes = g.Average(r => (double)r.Bytes),
                mean_ticks_excluding_bookkeeping = g.Average(r => (double)(r.Ticks-r.ProbeTicks)),
                mean_bytes_excluding_bookkeeping = g.Average(r => (double)(r.Bytes-r.ProbeBytes)) }).ToArray();
        object result = new { schema = "spire-copy-work/v1", stopwatch_frequency = Stopwatch.Frequency,
            forks = s.Forks.Count, child_nodes = s.Born.Count(n => !n.Root),
            counted_transitions = s.Forks.Sum(r => r.Transitions), unattributed_transitions = s.UnattributedTransitions,
            child_node_fates = s.Born.Where(n => !n.Root).GroupBy(Fate).ToDictionary(g => g.Key, g => g.Count()),
            root_nodes = s.Born.Count(n => n.Root), expansion_events = s.ExpansionEvents, root_expansion_events = s.RootExpansionEvents,
            raw_observed_field_writes = s.RawWrites, excluded_fork_initialization_writes = s.InitializationWrites,
            late_snapshot_freezes = s.LateSnapshots, copied_node_snapshot_alias_changes = s.AliasChanges,
            fork_fates = groups, size_relation = relation, raw_write_sites = s.WriteSites,
            untracked_power_reference_fields = s.UnknownGraphs, unsupported_generic_writer_methods = unsupported,
            patched_methods_by_assembly = patchCounts, same_value_store_selftest = recordWrites, measurement_mode = recordWrites ? "writes" : "costs",
            coverage = "PredictedCard fields, PowerModel/base/subclass fields and registered DynamicVar fields use actual post-stfld hooks, including same-value stores. Forkable collection first storage field writes and successful pile list mutations are direct source hooks. Unsupported generic writers and Power collection/reference subgraphs remain explicit uncertainty. No final-value comparison is used as never-written evidence.",
            timing_scope = "Instrumented measurement only; nested copy durations overlap. Costs mode additionally subtracts measured Copied() bookkeeping and excludes record creation; it does not remove timer/Harmony/JIT/GC perturbation. Not production speed evidence." };
        session = null; constructing = null; return result;
    }
    static Dictionary<string, long> Sum(IEnumerable<Record> records, Func<Record, Dictionary<string, long>> selector)
    { var total = new Dictionary<string, long>(); foreach (var r in records) foreach (var p in selector(r)) Add(total, p.Key, p.Value); return total; }
    sealed class TestCell { public int Value; }
    [MethodImpl(MethodImplOptions.NoInlining)] static void TestStore(TestCell cell, int value) { cell.Value = value; }
    static void SelfTest()
    {
        session = new Session(); var record = new Record { Constructing = false }; var cell = new TestCell();
        session.Owners.GetOrCreateValue(cell).Parts.Add(new Part(record, "test", "test"));
        TestStore(cell, 0); TestStore(cell, 0);
        if (session.RawWrites != 2 || record.Written.GetValueOrDefault("test") != 1 || cell.Value != 0)
            throw new InvalidOperationException("RAW_SAME_VALUE_STORE_DIAGNOSTIC_FAILED");
        session = null;
    }
}
