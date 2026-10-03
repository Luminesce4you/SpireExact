using System.Reflection;
using HarmonyLib;

namespace SpireNativeHost;

// Optional diagnostic sink filtering only. No state, action, observer or search
// budget is changed. The upstream search boundary observer precedes this sink.
internal static class AdvisorDiagnosticLog
{
    static volatile bool enabled;
    static bool installed;
    static long suppressedInfo, suppressedDebug;
    const BindingFlags All = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;

    public static void Configure(Assembly solver, bool quiet)
    {
        if (quiet && !installed)
        {
            var type = solver.GetType("CombatSolver.CombatDiagnosticJournal", true)!;
            var method = type.GetMethod("WriteCore", All)
                ?? throw new MissingMethodException(type.FullName, "WriteCore");
            // Fail explicitly if the pinned logging contract has changed.
            var parameters = method.GetParameters();
            if (method.ReturnType != typeof(void) || parameters.Length != 3
                || parameters[1].Name != "level" || parameters[1].ParameterType != typeof(string)
                || parameters[2].Name != "message" || parameters[2].ParameterType != typeof(string))
                throw new InvalidOperationException("ADVISOR_LOG_CONTRACT_CHANGED");
            new Harmony("spire-exact.advisor-diagnostic-sink").Patch(method,
                prefix: new HarmonyMethod(typeof(AdvisorDiagnosticLog).GetMethod(nameof(Keep), All)!));
            installed = true;
        }
        Interlocked.Exchange(ref suppressedInfo, 0);
        Interlocked.Exchange(ref suppressedDebug, 0);
        enabled = quiet;
    }

    static bool Keep(string level, string message)
    {
        if (!enabled || (level != "info" && level != "debug")) return true;
        if (message.Contains("reason=time", StringComparison.Ordinal)
            || message.Contains("UNSUPPORTED", StringComparison.Ordinal)
            || message.Contains("MISMATCH", StringComparison.Ordinal)
            || message.Contains("EXCEPTION", StringComparison.Ordinal)
            || message.StartsWith("[CombatSolver/Test] HEAP_RECLAIM ", StringComparison.Ordinal)
            || message.StartsWith("[CombatSolver/Test] GC_SEARCH_ALLOCATION_LIMIT ", StringComparison.Ordinal)
            || message.StartsWith("[CombatSolver/Test] GC_ALLOCATION_CAPACITY ", StringComparison.Ordinal)
            || message.StartsWith("[CombatSolver/Test] GC_FRAGMENTATION_COMPACTION ", StringComparison.Ordinal)
            || message.StartsWith("[CombatSolver/Test] POTION_GRADIENT_MEMORY_DECISION ", StringComparison.Ordinal)
            || message.StartsWith("[CombatSolver/Test] MAIN_THREAD_FRAMES ", StringComparison.Ordinal)
            || message.StartsWith("[CombatSolver/Test] SEARCH_GC_LIFECYCLE ", StringComparison.Ordinal)) return true;
        if (level == "info") Interlocked.Increment(ref suppressedInfo);
        else Interlocked.Increment(ref suppressedDebug);
        return false;
    }

    public static object Metrics() => new
    {
        enabled,
        suppressed_info = Interlocked.Read(ref suppressedInfo),
        suppressed_debug = Interlocked.Read(ref suppressedDebug),
        warnings_and_errors_preserved = true
    };
}
