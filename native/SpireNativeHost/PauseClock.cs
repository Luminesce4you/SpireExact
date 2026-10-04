using System.Diagnostics;
using System.Reflection;
using System.Reflection.Emit;
using System.Runtime.CompilerServices;
using System.Text.Json;
using HarmonyLib;

namespace SpireNativeHost;

// Interactive-only runtime clock adapter. No game assembly or BCL method is
// patched, and neither the pinned source nor its DLL is modified on disk.
// The dashboard freezes the whole OS Job and publishes the completed pause
// ledger before resuming it. This reader corrects active budget time afterwards.
internal static class PauseClock
{
    const BindingFlags All = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;
    static readonly string? ledger = Environment.GetEnvironmentVariable("SPIRE_PAUSE_LEDGER");
    static readonly object gate = new();
    static long nextRead;
    static double completed;
    static double? pausedAt;
    static readonly HashSet<Assembly> installed = [];
    static readonly ConditionalWeakTable<Stopwatch, WatchState> watches = new();
    static readonly Dictionary<MethodBase, MethodInfo> replacements = MakeReplacements();
    static readonly Dictionary<short, OpCode> opcodes = typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static)
        .Where(f => f.FieldType == typeof(OpCode)).Select(f => (OpCode)f.GetValue(null)!).ToDictionary(o => o.Value);
    static int patchedMethods;
    static readonly List<string> adaptedSites = [];
    sealed class WatchState
    {
        public double PausedAtStart;
        public long CompletedAdjustment;
        public bool Running;
    }

    public static bool Enabled => !string.IsNullOrWhiteSpace(ledger);

    public static double PausedSeconds() => ReadPausedSeconds(Stopwatch.GetTimestamp());

    static double ReadPausedSeconds(long raw)
    {
        if (!Enabled) return 0;
        double now = (double)raw / Stopwatch.Frequency;
        lock (gate)
        {
            // Cache is keyed by RAW QPC, so the first call after a long OS
            // suspension refreshes the ledger before checking any deadline.
            if (raw >= nextRead)
            {
                nextRead = raw + Stopwatch.Frequency / 40;
                try
                {
                    using var document = JsonDocument.Parse(File.ReadAllText(ledger!));
                    var root = document.RootElement;
                    double total = root.TryGetProperty("paused_total_seconds", out var totalValue) ? totalValue.GetDouble() : 0;
                    double? start = root.TryGetProperty("paused_started_monotonic", out var startValue) && startValue.ValueKind != JsonValueKind.Null
                        ? startValue.GetDouble() : null;
                    if (!double.IsFinite(total) || total < 0 || (start is {} value && !double.IsFinite(value)))
                        throw new InvalidDataException("Invalid pause ledger clock");
                    completed = total; pausedAt = start;
                }
                catch (Exception error) when (error is IOException or JsonException or InvalidOperationException or FormatException)
                {
                    // The owner uses atomic replacement; retain the last valid
                    // ledger across a transient sharing/read failure.
                }
            }
            return completed + (pausedAt is {} since ? Math.Max(0, now - since) : 0);
        }
    }

    public static long Timestamp()
    {
        long raw = Stopwatch.GetTimestamp();
        return raw - (long)(ReadPausedSeconds(raw) * Stopwatch.Frequency);
    }
    public static DateTime UtcNow() => DateTime.UtcNow - TimeSpan.FromSeconds(PausedSeconds());
    public static long TickCount64() => Environment.TickCount64 - (long)(PausedSeconds() * 1000);
    public static TimeSpan ElapsedTime(long startingTimestamp) => Stopwatch.GetElapsedTime(startingTimestamp, Timestamp());
    public static void ValidateSearchModes(JsonElement config)
    {
        if (!Enabled) return;
        string defaultMode = config.TryGetProperty("search_mode", out var mode) ? mode.GetString()! : "Evaluate";
        if (defaultMode != "Evaluate") throw new InvalidOperationException("INTERACTIVE_PAUSE_REQUIRES_EVALUATE_SEARCH_MODE");
        if (config.TryGetProperty("gate_plans", out var plans) && plans.ValueKind == JsonValueKind.Object)
            foreach (var plan in plans.EnumerateObject())
                foreach (var member in plan.Value.GetProperty("members").EnumerateArray())
                    if (member.TryGetProperty("mode", out var value) && value.GetString() != "Evaluate")
                        throw new InvalidOperationException("INTERACTIVE_PAUSE_REQUIRES_EVALUATE_GATE_MEMBERS");
    }

    public static Stopwatch New()
    {
        var watch = new Stopwatch();
        if (Enabled) watches.Add(watch, new WatchState { PausedAtStart = PausedSeconds() });
        return watch;
    }
    public static Stopwatch StartNew()
    {
        var watch = Stopwatch.StartNew();
        if (Enabled) watches.Add(watch, new WatchState { Running = true, PausedAtStart = PausedSeconds() });
        return watch;
    }
    public static void Start(Stopwatch watch)
    {
        if (!Enabled) { watch.Start(); return; }
        var state = watches.GetValue(watch, _ => new WatchState());
        lock (state)
        {
            if (!watch.IsRunning) { state.PausedAtStart = PausedSeconds(); state.Running = true; }
            watch.Start();
        }
    }
    public static void Stop(Stopwatch watch)
    {
        if (!Enabled) { watch.Stop(); return; }
        var state = watches.GetValue(watch, _ => new WatchState());
        lock (state)
        {
            if (state.Running) state.CompletedAdjustment += (long)(Math.Max(0, PausedSeconds() - state.PausedAtStart) * Stopwatch.Frequency);
            state.Running = false; watch.Stop();
        }
    }
    public static void Restart(Stopwatch watch)
    {
        if (!Enabled) { watch.Restart(); return; }
        var state = watches.GetValue(watch, _ => new WatchState());
        lock (state)
        {
            state.CompletedAdjustment = 0; state.PausedAtStart = PausedSeconds(); state.Running = true;
            watch.Restart();
        }
    }
    public static void Reset(Stopwatch watch)
    {
        if (!Enabled) { watch.Reset(); return; }
        var state = watches.GetValue(watch, _ => new WatchState());
        lock (state)
        {
            state.CompletedAdjustment = 0; state.PausedAtStart = PausedSeconds(); state.Running = false;
            watch.Reset();
        }
    }
    public static long ElapsedTicks(Stopwatch watch)
    {
        if (!Enabled) return watch.ElapsedTicks;
        if (!watches.TryGetValue(watch, out var state))
            throw new InvalidOperationException("UNREGISTERED_PAUSE_STOPWATCH: clock was created before the adapter");
        lock (state)
        {
            long correction = state.CompletedAdjustment + (state.Running
                ? (long)(Math.Max(0, PausedSeconds() - state.PausedAtStart) * Stopwatch.Frequency) : 0);
            return Math.Max(0, watch.ElapsedTicks - correction);
        }
    }
    public static long ElapsedMilliseconds(Stopwatch watch) => Enabled
        ? (long)(ElapsedTicks(watch) * 1000d / Stopwatch.Frequency) : watch.ElapsedMilliseconds;
    public static TimeSpan Elapsed(Stopwatch watch) => Enabled
        ? TimeSpan.FromTicks((long)(ElapsedTicks(watch) * (double)TimeSpan.TicksPerSecond / Stopwatch.Frequency)) : watch.Elapsed;

    static Dictionary<MethodBase, MethodInfo> MakeReplacements()
    {
        var result = new Dictionary<MethodBase, MethodInfo>();
        void Add(Type type, string source, string target, params Type[] arguments) => result.Add(
            type.GetMethod(source, All, arguments)!, typeof(PauseClock).GetMethod(target, All)!);
        result.Add(typeof(Stopwatch).GetConstructor(Type.EmptyTypes)!, typeof(PauseClock).GetMethod(nameof(New), All)!);
        Add(typeof(Stopwatch), nameof(Stopwatch.StartNew), nameof(StartNew));
        Add(typeof(Stopwatch), nameof(Stopwatch.Start), nameof(Start));
        Add(typeof(Stopwatch), nameof(Stopwatch.Stop), nameof(Stop));
        Add(typeof(Stopwatch), nameof(Stopwatch.Restart), nameof(Restart));
        Add(typeof(Stopwatch), nameof(Stopwatch.Reset), nameof(Reset));
        Add(typeof(Stopwatch), "get_Elapsed", nameof(Elapsed));
        Add(typeof(Stopwatch), "get_ElapsedMilliseconds", nameof(ElapsedMilliseconds));
        Add(typeof(Stopwatch), "get_ElapsedTicks", nameof(ElapsedTicks));
        Add(typeof(Stopwatch), nameof(Stopwatch.GetTimestamp), nameof(Timestamp));
        Add(typeof(Stopwatch), nameof(Stopwatch.GetElapsedTime), nameof(ElapsedTime), typeof(long));
        Add(typeof(DateTime), "get_UtcNow", nameof(UtcNow));
        Add(typeof(Environment), "get_TickCount64", nameof(TickCount64));
        return result;
    }

    public static void Install(Assembly assembly)
    {
        if (!Enabled || installed.Contains(assembly)) return;
        if (assembly.GetName().Name is not ("CombatSolver" or "OfflineSearchHarness"))
            throw new InvalidOperationException("Pause clock may only adapt the search and harness assemblies");
        var harmony = new Harmony("spire-exact.interactive-pause-clock");
        foreach (var type in assembly.GetTypes())
        {
            // The small validation harness links this adapter into its own
            // assembly. Never rewrite the adapter's RAW clock reads.
            if (type.Namespace == typeof(PauseClock).Namespace) continue;
            // Restrict adaptation to budget/pacing/measurement call sites.
            // Upload, diagnostics and GC cleanup clocks only report physical
            // time; rewriting their unrelated async EH bodies is unnecessary.
            string name = type.FullName!;
            bool solverBudget = name is "CombatSolver.CombatBeamSolver"
                or "CombatSolver.SearchWorkPacer" or "CombatSolver.SearchPerformanceMetrics"
                || name.StartsWith("CombatSolver.CombatBeamSolver+", StringComparison.Ordinal)
                || name.StartsWith("CombatSolver.SearchWorkPacer+", StringComparison.Ordinal)
                || name.StartsWith("CombatSolver.SearchPerformanceMetrics+", StringComparison.Ordinal);
            bool harnessBudget = name is "OfflineSearchHarness.ModRuntime" or "OfflineSearchHarness.MainLoopContext"
                || name.StartsWith("OfflineSearchHarness.ModRuntime+", StringComparison.Ordinal)
                || name.StartsWith("OfflineSearchHarness.MainLoopContext+", StringComparison.Ordinal);
            bool validationHarness = name.StartsWith("PauseClockValidation.", StringComparison.Ordinal);
            if (!solverBudget && !harnessBudget && !validationHarness) continue;
            var methods = type.GetMethods(All | BindingFlags.DeclaredOnly).Cast<MethodBase>()
                .Concat(type.GetConstructors(All | BindingFlags.DeclaredOnly));
            foreach (var method in methods)
            {
                if (method.GetMethodBody() == null) continue;
                if (!HasClockCall(method)) continue;
                if (method.ContainsGenericParameters)
                    throw new InvalidOperationException("Unsupported generic pause clock site: " + method);
                try { harmony.Patch(method, transpiler: new HarmonyMethod(typeof(PauseClock).GetMethod(nameof(Replace), All)!)); }
                catch (Exception error) { throw new InvalidOperationException("PAUSE_CLOCK_INSTALL_FAILED: " + name + "." + method.Name, error); }
                patchedMethods++;
                adaptedSites.Add(name + "." + method.Name);
            }
        }
        installed.Add(assembly);
    }
    static bool HasClockCall(MethodBase method)
    {
        // Scan metadata before asking Harmony to import a method. Generic
        // anonymous DTOs have no clock sites, but MonoMod cannot import their
        // open generic signatures merely to inspect their instructions.
        byte[] bytes = method.GetMethodBody()!.GetILAsByteArray()!;
        for (int offset = 0; offset < bytes.Length;)
        {
            short value = bytes[offset++];
            if (value == 0xfe) value = unchecked((short)(0xfe00 | bytes[offset++]));
            var opcode = opcodes[value];
            if (opcode.OperandType == OperandType.InlineMethod)
            {
                var target = method.Module.ResolveMethod(BitConverter.ToInt32(bytes, offset),
                    method.DeclaringType?.GetGenericArguments(), method is MethodInfo info ? info.GetGenericArguments() : null);
                if (target != null && replacements.ContainsKey(target)) return true;
            }
            offset += opcode.OperandType switch
            {
                OperandType.InlineNone => 0,
                OperandType.ShortInlineBrTarget or OperandType.ShortInlineI or OperandType.ShortInlineVar => 1,
                OperandType.InlineVar => 2,
                OperandType.InlineI8 or OperandType.InlineR => 8,
                OperandType.InlineSwitch => 4 + 4 * BitConverter.ToInt32(bytes, offset),
                _ => 4
            };
        }
        return false;
    }
    static IEnumerable<CodeInstruction> Replace(IEnumerable<CodeInstruction> instructions)
    {
        foreach (var instruction in instructions)
        {
            if (instruction.operand is MethodBase target && replacements.TryGetValue(target, out var replacement)
                && (instruction.opcode == OpCodes.Call || instruction.opcode == OpCodes.Callvirt || instruction.opcode == OpCodes.Newobj))
            {
                instruction.opcode = OpCodes.Call; instruction.operand = replacement;
            }
            yield return instruction;
        }
    }
    public static object Metrics() => new { enabled = Enabled, patched_methods = patchedMethods, adapted_sites = adaptedSites,
        paused_seconds = PausedSeconds(), ledger_poll_ms = 25,
        scope = "Evaluate search budget, pacing, search measurement and harness deadlines; Coordinator rejected for interactive jobs; physical diagnostic clocks unchanged" };
}
