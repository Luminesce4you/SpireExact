using System.Diagnostics;
using System.Runtime.InteropServices;
namespace SpireNativeHost;

// Exclusive wall time: nested stages are charged to the innermost active stage.
// CPU/GC counters are process-wide observations, NOT per-stage attribution.
internal sealed class PhaseProfiler
{
    sealed class Row { public long Calls; public double Milliseconds; }
    readonly Dictionary<string, Row> stages = [];
    readonly Dictionary<string, long> counters = [];
    readonly Stack<string> stack = new();
    readonly Stopwatch clock = Stopwatch.StartNew();
    readonly TimeSpan cpu = Process.GetCurrentProcess().TotalProcessorTime;
    readonly ulong? cycles = ReadCycles();
    readonly long allocated = GC.GetTotalAllocatedBytes(false);
    readonly int[] gc = [GC.CollectionCount(0), GC.CollectionCount(1), GC.CollectionCount(2)];
    double last;
    public static PhaseProfiler Current { get; set; } = new();
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool QueryProcessCycleTime(IntPtr process, out ulong cycleTime);
    static ulong? ReadCycles() => OperatingSystem.IsWindows() && QueryProcessCycleTime(new IntPtr(-1), out ulong value)
        ? value : null;
    void Charge()
    {
        double now = clock.Elapsed.TotalMilliseconds;
        if (stack.TryPeek(out string? name)) stages[name].Milliseconds += now - last;
        last = now;
    }
    public IDisposable Enter(string name)
    {
        Charge();
        if (!stages.TryGetValue(name, out var row)) stages[name] = row = new Row();
        row.Calls++;
        stack.Push(name);
        return new Scope(this, name);
    }
    sealed class Scope(PhaseProfiler owner, string name) : IDisposable
    {
        bool disposed;
        public void Dispose()
        {
            if (disposed) return;
            owner.Charge();
            if (owner.stack.Pop() != name) throw new InvalidOperationException("Profiler scope order");
            disposed = true;
        }
    }
    public void Count(string name, long count = 1) => counters[name] = counters.GetValueOrDefault(name) + count;
    public object Snapshot()
    {
        Charge();
        using var process = Process.GetCurrentProcess();
        ulong? endCycles = ReadCycles();
        return new {
            schema = "spire-perf/v1", wall_us = (long)(clock.Elapsed.TotalMilliseconds*1000),
            cpu_us = (long)((process.TotalProcessorTime - cpu).TotalMilliseconds*1000),
            cycles = cycles.HasValue && endCycles.HasValue && endCycles.Value >= cycles.Value
                ? endCycles.Value - cycles.Value : (ulong?)null,
            cycles_source = OperatingSystem.IsWindows() ? "QueryProcessCycleTime" : "unavailable",
            exclusive_stages = stages.ToDictionary(k => k.Key, v => new { calls = v.Value.Calls, us = (long)(v.Value.Milliseconds*1000) }),
            counters, allocated_bytes = GC.GetTotalAllocatedBytes(false) - allocated,
            collections = Enumerable.Range(0,3).Select(i => GC.CollectionCount(i)-gc[i]).ToArray(),
            working_set_bytes = process.WorkingSet64, private_bytes = process.PrivateMemorySize64,
            peak_working_set_bytes = process.PeakWorkingSet64,
            managed_heap_bytes = GC.GetTotalMemory(false), pid = Environment.ProcessId,
            runtime_processor_count = Environment.ProcessorCount,
            gc_server = System.Runtime.GCSettings.IsServerGC,
            gc_configuration = GC.GetConfigurationVariables()
        };
    }
}
