using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using Microsoft.Diagnostics.Tracing;

static class PmuAggregate
{
    sealed record Snapshot(double Time, int NewThread, int NewPid, ulong[] Values);
    sealed class Total
    {
        public long Intervals;
        public double OnCpuMs;
        public ulong[] Counters = new ulong[4];
        public void Add(Total value)
        {
            Intervals += value.Intervals; OnCpuMs += value.OnCpuMs;
            for (int n = 0; n < 4; n++) Counters[n] = checked(Counters[n] + value.Counters[n]);
        }
    }

    public static void Run(string etl, string settingsFile, string output)
    {
        using var settings = JsonDocument.Parse(File.ReadAllText(settingsFile));
        var pids = settings.RootElement.GetProperty("worker_pids").EnumerateArray().Select(v => v.GetInt32()).ToHashSet();
        double beginUnix = settings.RootElement.GetProperty("begin_unix").GetDouble();
        double endUnix = settings.RootElement.GetProperty("end_unix").GetDouble();
        var previous = new Dictionary<int, Snapshot>();
        var totals = new Dictionary<(int Pid, int Thread, int Cpu), Total>();
        var ownership = new Dictionary<int, HashSet<int>>();
        var activeOwner = new Dictionary<int, int>();
        var threadNames = new Dictionary<(int Pid, int Tid), HashSet<string>>();
        var sourceIds = new Dictionary<int, int[]>();
        var metadataNames = new List<string[]>();
        long contextSwitches = 0, snapshots = 0, missing = 0, discontinuities = 0, decreases = 0, boundary = 0;
        long metadataErrors = 0;
        double skippedMs = 0;
        using var source = new ETWTraceEventSource(etl);
        double startUnix = (source.SessionStartTime.ToUniversalTime() - DateTime.UnixEpoch).TotalSeconds;
        double begin = (beginUnix - startUnix) * 1000, end = (endUnix - startUnix) * 1000;
        void Owner(int tid, int pid)
        {
            if (tid <= 0 || pid <= 0) return;
            if (!ownership.TryGetValue(tid, out var values)) ownership[tid] = values = [];
            values.Add(pid);
        }
        void ThreadInfo(Microsoft.Diagnostics.Tracing.Parsers.Kernel.ThreadTraceData data)
        {
            Owner(data.ThreadID, data.ProcessID);
            if (data.ThreadID > 0 && data.ProcessID > 0) activeOwner[data.ThreadID] = data.ProcessID;
            if (!string.IsNullOrWhiteSpace(data.ThreadName))
            {
                if (!threadNames.TryGetValue((data.ProcessID, data.ThreadID), out var names)) threadNames[(data.ProcessID, data.ThreadID)] = names = [];
                names.Add(data.ThreadName);
            }
        }
        source.Kernel.ThreadStart += ThreadInfo;
        source.Kernel.ThreadDCStart += ThreadInfo;
        source.Kernel.ThreadStop += ThreadInfo;
        source.Kernel.ThreadDCStop += ThreadInfo;
        source.Kernel.ThreadSetName += data =>
        {
            Owner(data.ThreadID, data.ProcessID);
            if (!threadNames.TryGetValue((data.ProcessID, data.ThreadID), out var names)) threadNames[(data.ProcessID, data.ThreadID)] = names = [];
            names.Add(data.ThreadName);
        };
        source.Kernel.ThreadCSwitch += data =>
        {
            contextSwitches++;
            Owner(data.OldThreadID, data.OldProcessID); Owner(data.NewThreadID, data.NewProcessID);
            var values = NativePmc.Read(data);
            int cpu = data.ProcessorNumber;
            double timestamp = data.TimeStampRelativeMSec;
            if (values.Length != 4)
            {
                if (timestamp >= begin && timestamp <= end) missing++;
                previous.Remove(cpu); return;
            }
            snapshots++;
            if (previous.TryGetValue(cpu, out var old))
            {
                double duration = timestamp - old.Time;
                if (old.Time < begin || timestamp > end) boundary++;
                else if (duration < 0 || old.NewThread != data.OldThreadID)
                {
                    discontinuities++; skippedMs += Math.Max(0, duration);
                }
                else if (values.Zip(old.Values).Any(pair => pair.First < pair.Second))
                {
                    decreases++; skippedMs += duration;
                }
                else
                {
                    // Bind the interval to the process recorded when the thread entered
                    // this CPU. A later TID reuse cannot retroactively change its owner.
                    var key = (old.NewPid, data.OldThreadID, cpu);
                    if (!totals.TryGetValue(key, out var total)) totals[key] = total = new();
                    total.Intervals++; total.OnCpuMs += duration;
                    for (int n = 0; n < 4; n++) total.Counters[n] = checked(total.Counters[n] + values[n] - old.Values[n]);
                }
            }
            int newPid = data.NewProcessID;
            if (newPid <= 0) newPid = activeOwner.GetValueOrDefault(data.NewThreadID, -1);
            previous[cpu] = new(timestamp, data.NewThreadID, newPid, values);
        };
        source.AllEvents += data =>
        {
            if (data.TaskGuid != new Guid("ce1dbfb4-137e-4da6-87b0-3f59aa102cbc")) return;
            if ((int)data.Opcode == 48)
            {
                byte[] bytes = new byte[data.EventDataLength]; Marshal.Copy(data.DataStart, bytes, 0, bytes.Length);
                if (bytes.Length < 4) { metadataErrors++; return; }
                int count = BitConverter.ToInt32(bytes, 0);
                var names = Encoding.Unicode.GetString(bytes, 4, bytes.Length - 4).Split('\0', StringSplitOptions.RemoveEmptyEntries);
                if (names.Length != count || count != 4) metadataErrors++;
                else metadataNames.Add(names);
            }
            else if ((int)data.Opcode == 49)
            {
                byte[] bytes = new byte[data.EventDataLength]; Marshal.Copy(data.DataStart, bytes, 0, bytes.Length);
                if (bytes.Length < 8) { metadataErrors++; return; }
                int cpu = BitConverter.ToInt32(bytes, 0), count = BitConverter.ToInt32(bytes, 4);
                if (count != 4 || bytes.Length != 8 + count * 12) { metadataErrors++; return; }
                sourceIds[cpu] = Enumerable.Range(0, count).Select(n => BitConverter.ToInt32(bytes, 8 + n * 12)).ToArray();
            }
        };
        source.Process();
        string[] namesFinal = metadataNames.FirstOrDefault() ?? [];
        bool metadataValid = namesFinal.Length == 4 && metadataErrors == 0
            && metadataNames.All(n => n.SequenceEqual(namesFinal)) && sourceIds.Count > 0
            && sourceIds.Values.All(ids => ids.SequenceEqual(new[] { 26, 29, 28, 25 }))
            && namesFinal.SequenceEqual(new[] { "InstructionRetired", "LLCMisses", "LLCReference", "UnhaltedCoreCycles" });
        var processTotals = new Dictionary<int, Total>();
        var cpuTotals = new Dictionary<int, Total>();
        var threadTotals = new Dictionary<(int Pid, int Tid), Total>();
        long ambiguousIntervals = 0, unknownIntervals = 0, ambiguousWorkerIntervals = 0;
        double ambiguousWorkerMs = 0;
        foreach (var (key, value) in totals)
        {
            int pid = key.Pid;
            if (pid <= 0)
            {
                if (!ownership.TryGetValue(key.Thread, out var owners)) { unknownIntervals += value.Intervals; continue; }
                if (owners.Count != 1)
                {
                    ambiguousIntervals += value.Intervals;
                    if (owners.Overlaps(pids)) { ambiguousWorkerIntervals += value.Intervals; ambiguousWorkerMs += value.OnCpuMs; }
                    continue;
                }
                pid = owners.Single();
            }
            if (!pids.Contains(pid)) continue;
            if (!processTotals.TryGetValue(pid, out var process)) processTotals[pid] = process = new();
            if (!cpuTotals.TryGetValue(key.Cpu, out var cpu)) cpuTotals[key.Cpu] = cpu = new();
            if (!threadTotals.TryGetValue((pid, key.Thread), out var thread)) threadTotals[(pid, key.Thread)] = thread = new();
            process.Add(value); cpu.Add(value); thread.Add(value);
        }
        var all = new Total(); foreach (var total in processTotals.Values) all.Add(total);
        object Result(Total value) => new
        {
            value.Intervals, on_cpu_seconds = value.OnCpuMs / 1000,
            counters = namesFinal.Length == 4 ? namesFinal.Select((name, n) => (name, value.Counters[n])).ToDictionary(v => v.name, v => v.Item2) : new(),
            ipc = value.Counters[3] > 0 ? (double)value.Counters[0] / value.Counters[3] : (double?)null,
            llc_mpki = value.Counters[0] > 0 ? 1000.0 * value.Counters[1] / value.Counters[0] : (double?)null,
            llc_miss_fraction = value.Counters[2] > 0 ? (double)value.Counters[1] / value.Counters[2] : (double?)null,
            cycles_per_on_cpu_second = value.OnCpuMs > 0 ? value.Counters[3] / (value.OnCpuMs / 1000) : (double?)null
        };
        bool valid = metadataValid && source.EventsLost == 0 && decreases == 0 && ambiguousWorkerIntervals == 0 && all.Counters.All(c => c > 0)
            && pids.All(pid => processTotals.ContainsKey(pid));
        File.WriteAllText(output, JsonSerializer.Serialize(new
        {
            schema = "spire-cswitch-pmc/v2", etl, settingsFile, valid, source.EventsLost, source.SessionStartTime, source.SessionEndTime,
            counter_names = namesFinal, counter_source_ids_by_cpu = sourceIds, metadataValid, metadataErrors,
            begin_relative_ms = begin, end_relative_ms = end, contextSwitches, snapshots,
            missing_snapshots_in_window = missing, discontinuities, decreases, boundary_intervals = boundary, skipped_cpu_ms = skippedMs,
            unknown_intervals = unknownIntervals, ambiguous_thread_reuse_intervals = ambiguousIntervals,
            ambiguous_worker_intervals = ambiguousWorkerIntervals, ambiguous_worker_on_cpu_seconds = ambiguousWorkerMs / 1000,
            missing_worker_pids = pids.Except(processTotals.Keys), total = Result(all),
            processes = processTotals.ToDictionary(v => v.Key, v => Result(v.Value)),
            cpus = cpuTotals.ToDictionary(v => v.Key, v => Result(v.Value)),
            threads = threadTotals.OrderByDescending(v => v.Value.OnCpuMs).Select(v => new { pid = v.Key.Pid, tid = v.Key.Tid, names = threadNames.GetValueOrDefault(v.Key) ?? [], metrics = Result(v.Value) }),
            scope = "Cumulative per-logical-CPU PMC deltas between consecutive continuous CSwitch events, attributed to outgoing worker thread; includes CLR/GC and interrupt execution during its scheduled interval. LLC events are not DRAM bytes."
        }, new JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine(JsonSerializer.Serialize(new { valid, source.EventsLost, metadataValid, snapshots, worker_pids = processTotals.Count, on_cpu_seconds = all.OnCpuMs / 1000 }));
        if (!valid) Environment.ExitCode = 2;
    }
}
