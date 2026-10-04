using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Rooms;

namespace SpireNativeHost;

// Commit after native execution, unlike trace.jsonl (written before execution).
// Only idle MAP or first combat action boundaries are resumable proposals.
internal sealed partial class CampaignReplay
{
    bool preserveCompletedPrefix,completedOuterActionInFlight;
    int completedPrefixWatermark,executedPrefixWatermark,safeJournalPrefix;
    readonly HashSet<CombatState> prefixJournalCombatSeen=new(ReferenceEqualityComparer.Instance);
    void CompletedPrefixInit(JsonElement source)
    {
        preserveCompletedPrefix=source.TryGetProperty("preserve_completed_prefix",out var preserve)&&preserve.GetBoolean();
        if(preserveCompletedPrefix&&(probe.HasValue||cardMenuProbe.HasValue||source.TryGetProperty("expected_evidence",out _)))
            throw new ArgumentException("COMPLETED_PREFIX_NOT_SYNTHETIC_OR_VERIFICATION");
    }
    bool CompletedBoundaryIdle()=>!completedOuterActionInFlight
        &&!roomTasks.Any(t=>!t.IsCompleted)&&!background.Any(t=>!t.IsCompleted)
        &&run!=null&&MapActionRuntime.Capture()!=null;
    void CommitCompletedBoundary()
    {
        if(!WorkerMemoryTelemetry.Enabled)return;
        if(CompletedBoundaryIdle())completedPrefixWatermark=transcript.Count;
        PublishCompletedProgress();
    }
    void MarkOuterActionCompleted()
    {
        if(!WorkerMemoryTelemetry.Enabled)return;
        completedOuterActionInFlight=false;
        executedPrefixWatermark=transcript.Count;
        PublishCompletedProgress();
    }
    void PublishCompletedProgress()
    {
        WorkerMemoryTelemetry.Publish(new WorkerMemoryTelemetry.Progress(
            Math.Max(0,executedPrefixWatermark-restoredPrefix),Math.Max(executedPrefixWatermark,restoredPrefix),history.Length,restoredPrefix,
            phase,run?.CurrentActIndex,run?.TotalFloor,CombatManager.Instance.IsInProgress,safeJournalPrefix));
    }
    void BeforeCompletedPrefixDecision(string name)
    {
        if(!WorkerMemoryTelemetry.Enabled)return;
        PublishCompletedProgress();
        if(!preserveCompletedPrefix)return;
        bool combatEntry=false;
        if(name=="combat"&&run.CurrentRoom is CombatRoom room)
            combatEntry=prefixJournalCombatSeen.Add(room.CombatState);
        bool mapEntry=name=="map"&&!CombatManager.Instance.IsInProgress;
        if(!(combatEntry||mapEntry)||cursor<history.Length||transcript.Count<=history.Length
            ||transcript.Count<=safeJournalPrefix||completedPrefixWatermark!=transcript.Count||!CompletedBoundaryIdle())return;
        if(decisionEvidence.Count!=transcript.Count)throw new InvalidDataException("COMPLETED_PREFIX_EVIDENCE_LENGTH");
        string requestSha=WorkerMemoryTelemetry.CurrentRequestSha
            ??throw new InvalidDataException("COMPLETED_PREFIX_REQUEST_BINDING");
        var payload=new JsonObject {
            ["context"]=RoomCheckpoint.Context(request),["identity"]=JsonSerializer.SerializeToNode(Program.NativeIdentity()),
            ["request_sha256"]=requestSha,["request"]=JsonNode.Parse(request.GetRawText()),
            ["research_progress"]=request.TryGetProperty("research_progress",out var baseline)?JsonNode.Parse(baseline.GetRawText()):null,
            ["prefix_length"]=completedPrefixWatermark,["history"]=JsonSerializer.SerializeToNode(transcript,Program.Json),
            ["evidence"]=JsonSerializer.SerializeToNode(decisionEvidence,Program.Json),
            ["requested_prefix_length"]=history.Length,["restored_prefix"]=restoredPrefix,
            ["observation"]=JsonSerializer.SerializeToNode(Observe(),Program.Json),
            ["boundary"]=new JsonObject {["phase"]=name,["act"]=run.CurrentActIndex,["floor"]=run.TotalFloor,
                ["combat_entry"]=combatEntry,["quiescent_map"]=mapEntry,["room"]=run.CurrentRoom?.RoomType.ToString(),
                ["encounter"]=(run.CurrentRoom as CombatRoom)?.Encounter.Id.Entry},
            ["saved_at_unix_ms"]=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            ["status"]="UNKNOWN",["value"]=null,
            ["scope"]="completed real prefix only; no terminal result, mid-combat salvage or complete-host equivalence proof"
        };
        string canonical=RoomCheckpoint.Canonical(payload);
        var wrapper=new JsonObject {
            ["schema"]="spire-completed-prefix/v1",["payload"]=payload,["payload_canonical"]=canonical,
            ["sha256"]=Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(canonical))).ToLowerInvariant()
        };
        // Lossless transport only: all payload, canonical, hash and boundary
        // checks remain identical while avoiding repeated multi-MiB disk writes.
        WorkerMemoryTelemetry.WriteAtomicGzip(Path.Combine(output,"completed-prefix.json.gz"),wrapper.ToJsonString());
        safeJournalPrefix=completedPrefixWatermark;
        PublishCompletedProgress();
    }
}
