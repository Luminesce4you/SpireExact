using System.Reflection;
using System.Text.Json;
using Microsoft.Diagnostics.Tracing;
using Microsoft.Diagnostics.Tracing.Etlx;
using Microsoft.Diagnostics.Tracing.Parsers.Clr;

if (args.Length == 1 && args[0] == "--describe")
{
    foreach (Type type in new[] { typeof(TraceLog), typeof(EventPipeEventSource), typeof(GCAllocationTickTraceData), typeof(TraceCallStack) })
    {
        Console.WriteLine(type.FullName);
        foreach (var property in type.GetProperties()) Console.WriteLine("PROPERTY " + property);
        if (type == typeof(TraceLog))
            foreach (var method in type.GetMethods().Where(m => m.IsStatic && (m.Name.Contains("Open") || m.Name.Contains("Create"))))
                Console.WriteLine("METHOD " + method);
    }
    foreach (Type type in typeof(TraceLog).Assembly.GetTypes())
        foreach (var method in type.GetMethods(BindingFlags.Public | BindingFlags.Static).Where(m => m.Name == "CallStack"))
            Console.WriteLine("EXTENSION " + type.FullName + " " + method);
    return;
}
if (args.Length != 2) throw new ArgumentException("trace.nettrace output.json");
string etlx = Path.ChangeExtension(args[0], ".etlx");
if (!File.Exists(etlx)) TraceLog.CreateFromEventPipeDataFile(args[0], etlx, new TraceLogOptions());
using var trace = new TraceLog(etlx);
var types = new Dictionary<string, AllocationStat>();
var paths = new Dictionary<string, AllocationStat>();
var stages = new Dictionary<string, AllocationStat>();
var buckets = new Dictionary<string, AllocationStat>();
long allTicks = 0, allEstimated = 0, searchTicks = 0, searchEstimated = 0, noStackTicks = 0;
long incompleteStacks = 0;
var source = trace.Events.GetSource();
source.Clr.GCAllocationTick += data =>
{
    allTicks++;
    long estimate = Math.Max(0, data.AllocationAmount64 > 0 ? data.AllocationAmount64 : data.AllocationAmount);
    allEstimated += estimate;
    var frames = new List<string>();
    var stack = data.CallStack();
    if (stack == null) noStackTicks++;
    while (stack != null && frames.Count < 256)
    {
        frames.Add(stack.CodeAddress.FullMethodName ?? "unknown");
        stack = stack.Caller;
    }
    if (stack != null) incompleteStacks++;
    if (!frames.Any(n => n.Contains("CombatBeamSolver.SolveCore("))) return;
    searchTicks++; searchEstimated += estimate;
    string type = string.IsNullOrEmpty(data.TypeName) ? "unknown" : data.TypeName;
    string owner = frames.FirstOrDefault(n => n.Contains("CombatSolver.") && !n.Contains("CombatSolver_MemberwiseClone")) ?? "unknown";
    string stage = frames.Any(n => n.Contains(".Fork(")) ? "Fork"
        : frames.Any(n => n.Contains("CombatBeamSolver.Snapshot(")) ? "Snapshot"
        : frames.Any(n => n.Contains("ContinueCardPlayExecution(") || n.Contains(".ManualPlay(")) ? "CardExecution"
        : frames.Any(n => n.Contains("AdvanceRound(") || n.Contains("ContinuePlayerStart(")) ? "Round"
        : "OtherSearch";
    Add(types, type, estimate, data.ObjectSize);
    Add(paths, owner, estimate, data.ObjectSize);
    Add(stages, stage, estimate, data.ObjectSize);
    Add(buckets, $"{Math.Floor(data.TimeStampRelativeMSec / 1000)}|{stage}|{type}", estimate, data.ObjectSize);
};
source.Process();
var report = new
{
    trace_file = args[0], session_start_utc = trace.SessionStartTime.ToUniversalTime(),
    session_end_utc = trace.SessionEndTime.ToUniversalTime(), events_lost = trace.EventsLost,
    all_allocation_ticks = allTicks, all_allocation_interval_bytes = allEstimated,
    search_allocation_ticks = searchTicks, search_allocation_interval_bytes = searchEstimated,
    no_stack_ticks = noStackTicks, truncated_stacks = incompleteStacks,
    scope = "GC allocation tick estimates conditioned on SolveCore. Interval bytes are sampling estimates, not exact per-type allocated bytes. Sampled object sizes/counts are not all objects. Categories are exclusive attribution from stack, not native phase timers.",
    by_type = Ordered(types), by_nearest_solver_frame = Ordered(paths), by_stage = Ordered(stages), by_second_stage_type = Ordered(buckets)
};
File.WriteAllText(args[1], JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine(JsonSerializer.Serialize(new { allTicks, searchTicks, searchEstimated, noStackTicks, lost = trace.EventsLost }));

static void Add(Dictionary<string,AllocationStat> table, string key, long interval, long size)
{
    if (!table.TryGetValue(key,out var stat)) table[key] = stat = new();
    stat.Ticks++; stat.IntervalBytes += interval; stat.SampleObjectBytes += Math.Max(0,size);
}
static object[] Ordered(Dictionary<string,AllocationStat> table) => table.OrderByDescending(kv => kv.Value.IntervalBytes)
    .Select(kv => (object)new { name = kv.Key, ticks = kv.Value.Ticks, interval_bytes = kv.Value.IntervalBytes, sampled_object_bytes = kv.Value.SampleObjectBytes }).ToArray();

sealed class AllocationStat { public long Ticks; public long IntervalBytes; public long SampleObjectBytes; }
