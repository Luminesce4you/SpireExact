using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using MegaCrit.Sts2.Core.Saves.Validation;

namespace SpireNativeHost;

// Opt-in research lifecycle for the native save DTO only. Not a full-host
// checkpoint and never a restore from an F1/card-menu proposal's expected guard.
internal sealed class ResearchProgressLifecycle
{
    public const string Schema = "spire-research-progress/v1";
    public const string SnapshotSchema = "spire-native-progress-snapshot/v1";
    const string GameSha = "0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9";
    readonly JsonElement request;
    readonly string baselineSha;
    readonly string baselineRaw;
    public string BaselineSha => baselineSha;
    public bool CheckpointRestored { get; private set; }

    ResearchProgressLifecycle(JsonElement request, JsonObject baseline)
    {
        this.request = request.Clone();
        RequireMetadata(baseline, Schema, request);
        baselineRaw = RequiredString(baseline, "baseline_snapshot");
        baselineSha = RequiredString(baseline, "baseline_sha256");
        if (baselineSha != HashRaw(baselineRaw))
            throw new InvalidDataException("RESEARCH_PROGRESS_BASELINE_CHECKSUM");
    }

    public static ResearchProgressLifecycle? Begin(JsonElement request)
    {
        if (!request.TryGetProperty("research_progress", out var baseline)) return null;
        if (baseline.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("RESEARCH_PROGRESS_BASELINE_REQUIRED");
        if (request.TryGetProperty("expected_evidence", out _))
            throw new InvalidDataException("RESEARCH_PROGRESS_NOT_VERIFICATION");
        if (request.TryGetProperty("checkpoint", out var checkpoint)
            && (checkpoint.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(checkpoint.GetString())))
            throw new InvalidDataException("CHECKPOINT_RESEARCH_PROGRESS_PATH");
        // Fixed native API: public IsInProgress is exactly `State != null`.
        // Do not depend on access to the private State property/publicizer.
        if (RunManager.Instance.IsInProgress)
            throw new InvalidDataException("RESEARCH_PROGRESS_RUN_STILL_ACTIVE");
        var lifecycle = new ResearchProgressLifecycle(request, JsonNode.Parse(baseline.GetRawText())!.AsObject());
        RestoreRaw(lifecycle.baselineRaw, "research_progress_initial");
        return lifecycle;
    }

    // Called exactly once by a newly bootstrapped, disposable process before
    // Player/CreateRun. No player profile is read and no game action is executed.
    public static JsonObject ExportBaseline(JsonElement request)
    {
        if (RunManager.Instance.IsInProgress || request.TryGetProperty("checkpoint", out _)
            || request.TryGetProperty("research_progress", out _))
            throw new InvalidDataException("RESEARCH_PROGRESS_BASELINE_MUST_BE_FRESH");
        string raw = NativeProgressGuard.RawCapture();
        _ = DeserializeExact(raw, "research_progress_baseline_export");
        var baseline = Metadata(Schema, request);
        baseline["baseline_snapshot"] = raw;
        baseline["baseline_sha256"] = HashRaw(raw);
        return baseline;
    }

    public JsonObject CaptureCheckpoint()
    {
        string raw = NativeProgressGuard.RawCapture();
        // Verify that a native reload can preserve every raw field/list before
        // publishing an apparently usable checkpoint. Warning/drop/merge is UNKNOWN.
        _ = DeserializeExact(raw, "research_progress_checkpoint_capture");
        var snapshot = Metadata(SnapshotSchema, request);
        snapshot["baseline_sha256"] = baselineSha;
        snapshot["native_json"] = raw;
        snapshot["native_sha256"] = HashRaw(raw);
        return snapshot;
    }

    public string RestoreCheckpoint(JsonObject checkpoint)
    {
        if (checkpoint["native_progress_snapshot"] is not JsonObject snapshot)
            throw new InvalidDataException("CHECKPOINT_RESEARCH_PROGRESS_REQUIRED");
        RequireMetadata(snapshot, SnapshotSchema, request);
        if (RequiredString(snapshot, "baseline_sha256") != baselineSha)
            throw new InvalidDataException("CHECKPOINT_RESEARCH_PROGRESS_BASELINE");
        string raw = RequiredString(snapshot, "native_json");
        if (RequiredString(snapshot, "native_sha256") != HashRaw(raw))
            throw new InvalidDataException("CHECKPOINT_RESEARCH_PROGRESS_CHECKSUM");
        // Must precede RunState.FromSerializable: Player's constructor reads
        // live Progress.MaxAscensionWhenRunStarted even while loading a run.
        RestoreRaw(raw, "research_progress_checkpoint");
        CheckpointRestored = true;
        return raw;
    }

    public static void RequireUnchanged(string expectedRaw, string label) =>
        RoomCheckpoint.RequireEqual(ParseRaw(expectedRaw), ParseRaw(NativeProgressGuard.RawCapture()), label);

    static ProgressState DeserializeExact(string raw, string label)
    {
        var expected = ParseRaw(raw);
        if (expected["schema_version"] is not JsonValue version || !version.TryGetValue<int>(out int schema)
            || schema != 0)
            throw new InvalidDataException("RESEARCH_PROGRESS_NATIVE_SCHEMA");
        var save = JsonSerializer.Deserialize(raw, JsonSerializationUtility.GetTypeInfo<SerializableProgress>())
            ?? throw new InvalidDataException("RESEARCH_PROGRESS_EMPTY_NATIVE_SAVE");
        if (save.SchemaVersion != 0)
            throw new InvalidDataException("RESEARCH_PROGRESS_NATIVE_SCHEMA");
        var context = new DeserializationContext();
        var restored = ProgressState.FromSerializable(save, context);
        if (context.Errors.Count != 0)
            throw new InvalidDataException("RESEARCH_PROGRESS_NATIVE_VALIDATION:" + JsonSerializer.Serialize(context.Errors));
        var roundtrip = JsonNode.Parse(JsonSerializer.Serialize(restored.ToSerializable(), JsonSerializationUtility.Options));
        // Compare raw JSON, including UniqueId and actual physical timestamps.
        // NativeProgressGuard's audited comparison normalization is NOT a restore.
        RoomCheckpoint.RequireEqual(expected, roundtrip, label + "_roundtrip");
        return restored;
    }

    static void RestoreRaw(string raw, string label)
    {
        var restored = DeserializeExact(raw, label);
        SaveManager.Instance.Progress = restored;
        RequireUnchanged(raw, label + "_live");
    }

    static JsonObject ParseRaw(string raw) => JsonNode.Parse(raw) as JsonObject
        ?? throw new InvalidDataException("RESEARCH_PROGRESS_NATIVE_JSON");

    static JsonObject Metadata(string schema, JsonElement request)
    {
        NativeProgressGuard.RequirePinnedGame();
        return new JsonObject {
            ["schema"] = schema, ["game_sha256"] = GameSha,
            ["native_identity"] = JsonSerializer.SerializeToNode(Program.NativeIdentity()),
            ["context"] = RoomCheckpoint.Context(request)
        };
    }

    static void RequireMetadata(JsonObject metadata, string schema, JsonElement request)
    {
        NativeProgressGuard.RequirePinnedGame();
        if (RequiredString(metadata, "schema") != schema)
            throw new InvalidDataException("RESEARCH_PROGRESS_SCHEMA");
        if (RequiredString(metadata, "game_sha256") != GameSha)
            throw new InvalidDataException("RESEARCH_PROGRESS_GAME_IDENTITY");
        if (metadata["native_identity"] is not JsonObject || metadata["context"] is not JsonObject)
            throw new InvalidDataException("RESEARCH_PROGRESS_METADATA_REQUIRED");
        RoomCheckpoint.RequireEqual(metadata["native_identity"], JsonSerializer.SerializeToNode(Program.NativeIdentity()), "research_progress_binary_identity");
        RoomCheckpoint.RequireEqual(metadata["context"], RoomCheckpoint.Context(request), "research_progress_context");
    }

    static string RequiredString(JsonObject metadata, string name) =>
        metadata[name] is JsonValue value && value.TryGetValue<string>(out string? text) && !string.IsNullOrEmpty(text)
            ? text : throw new InvalidDataException("RESEARCH_PROGRESS_FIELD_REQUIRED:" + name);

    static string HashRaw(string raw) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(raw))).ToLowerInvariant();
}
