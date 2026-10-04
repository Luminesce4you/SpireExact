using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Saves;

namespace SpireNativeHost;

// Invoked only by an explicit fresh-process diagnostic command. Mutates the
// offline Progress object to test save/restore, never a run or game action.
internal static class ResearchProgressContracts
{
    public static object Run(JsonElement request)
    {
        var cases = new List<object>();
        var baseline = ResearchProgressLifecycle.ExportBaseline(request);
        string initialRaw = baseline["baseline_snapshot"]!.GetValue<string>();
        var researchRequest = Request(request, baseline);
        var lifecycle = ResearchProgressLifecycle.Begin(researchRequest)
            ?? throw new InvalidDataException("PROGRESS_CONTRACT_LIFECYCLE");
        ResearchProgressLifecycle.RequireUnchanged(initialRaw, "progress_contract_initial_roundtrip");
        cases.Add(new { name="initial_native_dto_roundtrip", passed=true });

        var progress = SaveManager.Instance.Progress;
        progress.CurrentScore = 17;
        progress.WongoPoints = 23;
        progress.MarkPotionAsSeen(ModelDb.AllPotions.First().Id);
        progress.MarkRelicAsSeen(ModelDb.AllRelics.First().Id);
        progress.GetOrCreateEncounterStats(ModelDb.AllEncounters.First().Id);
        progress.GetOrCreateEnemyStats(ModelDb.All.OfType<MonsterModel>().First().Id);
        progress.ObtainEpoch("IRONCLAD2_EPOCH");
        string checkpointRaw = NativeProgressGuard.RawCapture();
        if (RoomCheckpoint.Canonical(JsonNode.Parse(checkpointRaw)) == RoomCheckpoint.Canonical(JsonNode.Parse(initialRaw)))
            throw new InvalidDataException("PROGRESS_CONTRACT_POLLUTION_NOT_APPLIED");
        var checkpoint = new JsonObject { ["native_progress_snapshot"]=lifecycle.CaptureCheckpoint() };
        cases.Add(new { name="native_progress_snapshot_roundtrip", passed=true });

        _ = ResearchProgressLifecycle.Begin(researchRequest);
        ResearchProgressLifecycle.RequireUnchanged(initialRaw, "progress_contract_per_request_reset");
        cases.Add(new { name="polluted_worker_reset_to_initial_baseline", passed=true });
        string restored = lifecycle.RestoreCheckpoint(checkpoint);
        ResearchProgressLifecycle.RequireUnchanged(checkpointRaw, "progress_contract_real_snapshot_restore");
        if (!lifecycle.CheckpointRestored || restored != checkpointRaw)
            throw new InvalidDataException("PROGRESS_CONTRACT_CHECKPOINT_STATE");
        cases.Add(new { name="actual_captured_progress_restored", passed=true });

        string beforeOff = NativeProgressGuard.RawCapture();
        if (ResearchProgressLifecycle.Begin(request) != null)
            throw new InvalidDataException("PROGRESS_CONTRACT_OFF_LIFECYCLE");
        ResearchProgressLifecycle.RequireUnchanged(beforeOff, "progress_contract_off_unchanged");
        cases.Add(new { name="off_request_preserves_progress", passed=true });

        var wrongBaselineHash = baseline.DeepClone().AsObject();
        wrongBaselineHash["baseline_sha256"] = new string('0',64);
        Reject(cases, "baseline_bad_checksum", "RESEARCH_PROGRESS_BASELINE_CHECKSUM",
            () => ResearchProgressLifecycle.Begin(Request(request,wrongBaselineHash)));
        var wrongBaselineSchema = baseline.DeepClone().AsObject();
        wrongBaselineSchema["schema"] = "spire-research-progress/unsupported";
        Reject(cases, "baseline_wrong_schema", "RESEARCH_PROGRESS_SCHEMA",
            () => ResearchProgressLifecycle.Begin(Request(request,wrongBaselineSchema)));
        var wrongGame = baseline.DeepClone().AsObject();
        wrongGame["game_sha256"] = new string('0',64);
        Reject(cases, "baseline_wrong_game", "RESEARCH_PROGRESS_GAME_IDENTITY",
            () => ResearchProgressLifecycle.Begin(Request(request,wrongGame)));
        var wrongContext = baseline.DeepClone().AsObject();
        wrongContext["context"]!["seed"] = "different-context";
        Reject(cases, "baseline_wrong_context", "CHECKPOINT_MISMATCH:research_progress_context",
            () => ResearchProgressLifecycle.Begin(Request(request,wrongContext)));
        var wrongIdentity = baseline.DeepClone().AsObject();
        wrongIdentity["native_identity"]!["host_sha256"] = new string('0',64);
        Reject(cases, "baseline_wrong_host", "CHECKPOINT_MISMATCH:research_progress_binary_identity",
            () => ResearchProgressLifecycle.Begin(Request(request,wrongIdentity)));

        Reject(cases, "legacy_checkpoint_without_progress", "CHECKPOINT_RESEARCH_PROGRESS_REQUIRED",
            () => lifecycle.RestoreCheckpoint(new JsonObject()));
        Reject(cases, "null_checkpoint_progress", "CHECKPOINT_RESEARCH_PROGRESS_REQUIRED",
            () => lifecycle.RestoreCheckpoint(new JsonObject { ["native_progress_snapshot"]=null }));
        var wrongCheckpointHash = checkpoint.DeepClone().AsObject();
        wrongCheckpointHash["native_progress_snapshot"]!["native_sha256"] = new string('0',64);
        Reject(cases, "checkpoint_bad_native_checksum", "CHECKPOINT_RESEARCH_PROGRESS_CHECKSUM",
            () => lifecycle.RestoreCheckpoint(wrongCheckpointHash));
        var wrongCheckpointSchema = checkpoint.DeepClone().AsObject();
        wrongCheckpointSchema["native_progress_snapshot"]!["schema"] = "spire-native-progress-snapshot/unsupported";
        Reject(cases, "checkpoint_wrong_schema", "RESEARCH_PROGRESS_SCHEMA",
            () => lifecycle.RestoreCheckpoint(wrongCheckpointSchema));
        var wrongCheckpointBaseline = checkpoint.DeepClone().AsObject();
        wrongCheckpointBaseline["native_progress_snapshot"]!["baseline_sha256"] = new string('0',64);
        Reject(cases, "checkpoint_other_baseline", "CHECKPOINT_RESEARCH_PROGRESS_BASELINE",
            () => lifecycle.RestoreCheckpoint(wrongCheckpointBaseline));

        var repairedNative = baseline.DeepClone().AsObject();
        var rawNode = JsonNode.Parse(initialRaw)!.AsObject();
        rawNode["current_score"] = -1;
        string badRaw = rawNode.ToJsonString();
        repairedNative["baseline_snapshot"] = badRaw;
        repairedNative["baseline_sha256"] = HashRaw(badRaw);
        Reject(cases, "native_loader_repair_rejected", "RESEARCH_PROGRESS_NATIVE_VALIDATION",
            () => ResearchProgressLifecycle.Begin(Request(request,repairedNative)));
        var droppedNative = baseline.DeepClone().AsObject();
        rawNode = JsonNode.Parse(initialRaw)!.AsObject();
        rawNode["unknown_progress_contract_field"] = "must not be lost";
        badRaw = rawNode.ToJsonString();
        droppedNative["baseline_snapshot"] = badRaw;
        droppedNative["baseline_sha256"] = HashRaw(badRaw);
        Reject(cases, "unknown_native_field_drop_rejected", "CHECKPOINT_MISMATCH:research_progress_initial_roundtrip",
            () => ResearchProgressLifecycle.Begin(Request(request,droppedNative)));

        // Bad input must have failed before assigning live state.
        ResearchProgressLifecycle.RequireUnchanged(beforeOff, "progress_contract_rejections_preserve_live_state");
        cases.Add(new { name="rejections_preserve_live_progress", passed=true });
        return new {
            schema="spire-progress-contract/v1", passed=true, cases,
            synthetic=true, native_terminal_observed=false, value=(object?)null,
            baseline_sha256=baseline["baseline_sha256"]!.GetValue<string>(),
            scope="synthetic native Progress DTO contracts; offline memory only; no game action, run, combat, plan deployment or full-host parity proof"
        };
    }

    static JsonElement Request(JsonElement template,JsonObject baseline)
    {
        var request = JsonNode.Parse(template.GetRawText())!.AsObject();
        request["research_progress"] = baseline.DeepClone();
        return JsonSerializer.SerializeToElement(request);
    }
    static void Reject(List<object> cases,string name,string expectedCode,Action action)
    {
        try { action(); }
        catch(InvalidDataException error) when(error.Message.Contains(expectedCode,StringComparison.Ordinal))
        { cases.Add(new {name,passed=true,rejection=error.Message}); return; }
        throw new InvalidDataException("PROGRESS_CONTRACT_EXPECTED_REJECTION:"+name);
    }
    static string HashRaw(string raw) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(raw))).ToLowerInvariant();
}
