using System.Diagnostics;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace SpireNativeHost;

// Observational only. The timer reads GC/OS metrics and a copied primitive
// record, never a run, player, action, model, transcript or profiler dictionary.
internal sealed class WorkerMemoryTelemetry : IDisposable
{
    internal sealed record Progress(int CompletedActions,int CompletedPrefixLength,int RequestedPrefixLength,
        int RestoredPrefix,string Phase,int? Act,int? Floor,bool CombatActive,int SafeJournalPrefixLength);
    static WorkerMemoryTelemetry? current,lastTask;
    readonly string output,requestSha;
    readonly long started=Stopwatch.GetTimestamp(),startedUnixMs=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
    readonly int[] initialCollections=[GC.CollectionCount(0),GC.CollectionCount(1),GC.CollectionCount(2)];
    readonly object gate=new();
    readonly Timer timer;
    readonly object runtimeConfiguration;
    Progress progress;
    long sequence;
    int disposed,oomObserved;
    string? diagnosticError;
    public string RequestSha=>requestSha;
    public static bool Enabled=>Volatile.Read(ref current)!=null;
    public static void BeginRequest()=>Volatile.Write(ref lastTask,null);
    WorkerMemoryTelemetry(JsonElement request,string requestPath,string output)
    {
        this.output=output;
        using(var file=File.OpenRead(requestPath))requestSha=Convert.ToHexString(SHA256.HashData(file)).ToLowerInvariant();
        int prefix=request.TryGetProperty("history",out var history)&&history.ValueKind==JsonValueKind.Array?history.GetArrayLength():0;
        progress=new Progress(0,0,prefix,0,"initializing",null,null,false,0);
        runtimeConfiguration=RuntimeConfiguration();
        timer=new Timer(_=>Sample("heartbeat",false),null,Timeout.Infinite,Timeout.Infinite);
    }
    public static WorkerMemoryTelemetry? Start(JsonElement request,string requestPath,string output)
    {
        bool on=request.TryGetProperty("memory_telemetry",out var memory)&&memory.GetBoolean()
            ||request.TryGetProperty("preserve_completed_prefix",out var preserve)&&preserve.GetBoolean();
        if(!on){Volatile.Write(ref lastTask,null);return null;}
        var task=new WorkerMemoryTelemetry(request,requestPath,output);
        if(Interlocked.CompareExchange(ref current,task,null)!=null)
            throw new InvalidOperationException("MEMORY_TELEMETRY_TASK_OVERLAP");
        Volatile.Write(ref lastTask,task);
        task.Sample("heartbeat",true);
        task.timer.Change(2000,2000);
        ThrowIfOutOfMemoryObserved();
        return task;
    }
    public static void Publish(Progress snapshot)
    {
        var task=Volatile.Read(ref current);
        if(task!=null)Volatile.Write(ref task.progress,snapshot);
    }
    public static string? CurrentRequestSha=>Volatile.Read(ref current)?.requestSha;
    public static bool IsOutOfMemory(Exception error)=>error is OutOfMemoryException
        ||error is AggregateException aggregate&&aggregate.InnerExceptions.Any(IsOutOfMemory)
        ||error.InnerException!=null&&IsOutOfMemory(error.InnerException);
    public static void ThrowIfOutOfMemoryObserved()
    {
        if(Volatile.Read(ref current) is {} task&&Volatile.Read(ref task.oomObserved)!=0)
            throw new OutOfMemoryException("NATIVE_TASK_OUT_OF_MEMORY");
    }
    public void MarkOom()
    {
        Interlocked.Exchange(ref oomObserved,1);
        Sample("oom",true);
    }
    public static void ReportWorkerOom()
    {
        var task=Volatile.Read(ref current)??Volatile.Read(ref lastTask);
        if(task==null)return;
        Interlocked.Exchange(ref task.oomObserved,1);
        task.Sample("oom",true);
        WriteOomFailure(task.output,task.requestSha);
    }
    public static void WriteOomFailure(string output,string? requestSha)
    {
        // Best effort under OOM: protocol still carries the resource reason even
        // when there is insufficient memory to serialize or write a diagnostic.
        try { WriteAtomic(Path.Combine(output,"native-failure.json"),JsonSerializer.Serialize(new {
            schema="spire-native-failure/v1",status="NATIVE_TASK_OUT_OF_MEMORY",classification="RESOURCE_LIMIT",
            reason="NATIVE_TASK_OUT_OF_MEMORY",error_kind="NATIVE_TASK_OUT_OF_MEMORY",pid=Environment.ProcessId,
            request_sha256=requestSha,value=(object?)null,scope="managed allocation failure; UNKNOWN, never game defeat"
        })); } catch(Exception) { }
    }
    static object RuntimeConfiguration()
    {
        // Inspect only named CLR settings, never unrelated environment secrets.
        string[] names=["DOTNET_PROCESSOR_COUNT","DOTNET_gcServer","COMPlus_gcServer",
            "DOTNET_GCHeapCount","COMPlus_GCHeapCount","DOTNET_gcConcurrent","COMPlus_gcConcurrent",
            "DOTNET_GCHeapHardLimit","COMPlus_GCHeapHardLimit",
            "DOTNET_GCHeapHardLimitPercent","COMPlus_GCHeapHardLimitPercent",
            "DOTNET_GCHeapHardLimitSOH","COMPlus_GCHeapHardLimitSOH",
            "DOTNET_GCHeapHardLimitLOH","COMPlus_GCHeapHardLimitLOH",
            "DOTNET_GCHeapHardLimitPOH","COMPlus_GCHeapHardLimitPOH",
            "DOTNET_GCgen0size","COMPlus_GCgen0size",
            "DOTNET_GCConserveMemory","COMPlus_GCConserveMemory",
            "DOTNET_GCRetainVM","COMPlus_GCRetainVM"];
        return new {
            command_line=Environment.GetCommandLineArgs(),
            environment=names.ToDictionary(name=>name,name=>Environment.GetEnvironmentVariable(name),StringComparer.Ordinal),
            effective_processor_count=Environment.ProcessorCount,is_64_bit_process=Environment.Is64BitProcess,
            server_gc=System.Runtime.GCSettings.IsServerGC,
            scope="actual child CLR environment and command; configured values do not prove an effective heap limit"
        };
    }
    void Sample(string kind,bool mandatory)
    {
        if(!mandatory&&Volatile.Read(ref disposed)!=0)return;
        bool entered=false;
        try
        {
            if(mandatory){Monitor.Enter(gate);entered=true;}
            else if(!(entered=Monitor.TryEnter(gate)))return;
            if(!mandatory&&Volatile.Read(ref disposed)!=0)return;
            var gc=GC.GetGCMemoryInfo();
            using var process=Process.GetCurrentProcess();process.Refresh();
            var snapshot=Volatile.Read(ref progress);
            int[] collections=[GC.CollectionCount(0),GC.CollectionCount(1),GC.CollectionCount(2)];
            string line=JsonSerializer.Serialize(new {
                schema="spire-worker-memory/v1",pid=Environment.ProcessId,request_sha256=requestSha,
                sequence=++sequence,kind,sample_unix_ms=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                task_started_unix_ms=startedUnixMs,task_elapsed_seconds=Stopwatch.GetElapsedTime(started).TotalSeconds,
                gc_heap_size_bytes=gc.HeapSizeBytes,gc_total_committed_bytes=gc.TotalCommittedBytes,
                gc_total_available_memory_bytes=gc.TotalAvailableMemoryBytes,native_runtime_configuration=runtimeConfiguration,
                gc_fragmented_bytes=gc.FragmentedBytes,gc_pause_time_percentage=gc.PauseTimePercentage,
                gc_collection_counts=collections,gc_collection_deltas=collections.Select((n,i)=>n-initialCollections[i]).ToArray(),
                gc_index=gc.Index,gc_generation=gc.Generation,gc_memory_info_based_on_most_recent_gc=true,
                private_bytes=process.PrivateMemorySize64,rss_bytes=process.WorkingSet64,
                progress=new {completed_actions=snapshot.CompletedActions,completed_prefix_length=snapshot.CompletedPrefixLength,
                    requested_prefix_length=snapshot.RequestedPrefixLength,restored_prefix=snapshot.RestoredPrefix,
                    phase=snapshot.Phase,act=snapshot.Act,floor=snapshot.Floor,combat_active=snapshot.CombatActive,
                    safe_journal_prefix_length=snapshot.SafeJournalPrefixLength},
                error_kind=Volatile.Read(ref oomObserved)!=0?"NATIVE_TASK_OUT_OF_MEMORY":null,
                diagnostic_error=diagnosticError,
                scope="GC/OS observation; physical elapsed includes pause; completed progress published only by game thread"
            });
            WriteAtomic(Path.Combine(output,"memory-latest.json"),line);
            File.AppendAllText(Path.Combine(output,"memory-heartbeats.jsonl"),line+Environment.NewLine,new UTF8Encoding(false));
        }
        catch(Exception error) when(IsOutOfMemory(error)) { Interlocked.Exchange(ref oomObserved,1);diagnosticError="TELEMETRY_OUT_OF_MEMORY"; }
        catch(Exception error) { diagnosticError=error.GetType().Name; }
        finally {if(entered)Monitor.Exit(gate);}
    }
    internal static void WriteAtomic(string path,string text)
    {
        string temporary=path+".tmp";
        using(var file=new FileStream(temporary,FileMode.Create,FileAccess.Write,FileShare.None))
        using(var writer=new StreamWriter(file,new UTF8Encoding(false)))
        {writer.Write(text);writer.Flush();file.Flush(true);}
        File.Move(temporary,path,true);
    }
    internal static void WriteAtomicGzip(string path,string text)
    {
        string temporary=path+".tmp";
        using(var file=new FileStream(temporary,FileMode.Create,FileAccess.Write,FileShare.None))
        {
            using(var gzip=new GZipStream(file,CompressionLevel.Fastest,leaveOpen:true))
            using(var writer=new StreamWriter(gzip,new UTF8Encoding(false)))writer.Write(text);
            // Finalize the gzip footer before committing the complete file.
            file.Flush(true);
        }
        File.Move(temporary,path,true);
    }
    public void Dispose()
    {
        if(Interlocked.Exchange(ref disposed,1)!=0)return;
        timer.Dispose();
        Sample("task_end",true);
        Interlocked.CompareExchange(ref current,null,this);
        if(Volatile.Read(ref oomObserved)!=0)throw new OutOfMemoryException("NATIVE_TASK_OUT_OF_MEMORY");
    }
}
