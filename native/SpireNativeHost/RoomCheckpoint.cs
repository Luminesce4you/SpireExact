using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization.Metadata;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using MegaCrit.Sts2.Core.Saves.Runs;

namespace SpireNativeHost;

// Only quiescent MAP decision boundaries. This is never a combat/task snapshot.
// Scope: Boolean whole-run victory in the bundled offline TestMode host.
internal static class RoomCheckpoint
{
    public const string Schema = "spire-map-checkpoint/v1";
    static readonly JsonSerializerOptions Options = new(JsonSerializationUtility.Options)
    {
        TypeInfoResolver = JsonSerializationUtility.Options.TypeInfoResolver!.WithAddedModifier(info => {
            if (info.Type == typeof(SerializableMapPoint))
                foreach (var property in info.Properties)
                    if (property.Name == "can_modify") property.ShouldSerialize = (_, _) => true;
        })
    };
    public static JsonNode Context(JsonElement request) => new JsonObject {
        ["seed"] = request.GetProperty("seed").GetString(),
        ["character"] = request.GetProperty("character").GetString(),
        ["ascension"] = request.GetProperty("ascension").GetInt32(),
        ["unlocks"] = request.GetProperty("unlocks").GetString(),
        ["information"] = "full", ["objective"] = "whole_run_victory/v1"
    };
    public static JsonNode CaptureRun()
    {
        var save = RunManager.Instance.ToSave(null);
        // Only presentation/time/platform fields are normalized. Time/score objectives unsupported.
        save.SaveTime = 0; save.StartTime = 0; save.RunTime = 0; save.WinTime = 0;
        save.NumReloads = 0; save.PlatformType = default; save.MapDrawings = null;
        save.PreFinishedRoom = null; // no room is pending at this map checkpoint
        var root = JsonNode.Parse(JsonSerializer.Serialize(save, Options))!;
        // Native restore maps empty localization variable dictionaries to null.
        if (root["map_point_history"] is JsonArray acts)
            foreach (var act in acts.OfType<JsonArray>())
                foreach (var entry in act.OfType<JsonObject>())
                    if (entry["player_stats"] is JsonArray stats)
                        foreach (var stat in stats.OfType<JsonObject>())
                            if (stat["event_choices"] is JsonArray events)
                                foreach (var evt in events.OfType<JsonObject>())
                                    if (evt["variables"] is JsonObject {Count:0}) evt.Remove("variables");
        return root;
    }
    public static string Canonical(JsonNode? node) => Normalize(node)?.ToJsonString() ?? "null";
    static JsonNode? Normalize(JsonNode? node) => node switch {
        JsonObject obj => new JsonObject(obj.OrderBy(k => k.Key, StringComparer.Ordinal)
            .Select(k => KeyValuePair.Create(k.Key, Normalize(k.Value)))),
        JsonArray array => new JsonArray(array.Select(Normalize).ToArray()),
        _ => node?.DeepClone()
    };
    public static string Hash(JsonNode node) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Canonical(node)))).ToLowerInvariant();
    public static SerializableRun Deserialize(JsonNode state) => JsonSerializer.Deserialize(state.ToJsonString(),
        JsonSerializationUtility.GetTypeInfo<SerializableRun>()) ?? throw new InvalidDataException("Empty run save");
    public static void RequireEqual(JsonNode? expected, JsonNode? actual, string label)
    {
        // Never deduplicate/certify by hash equality alone.
        if (Canonical(expected) != Canonical(actual)) {
            CampaignReplay.SaveMismatch(label,expected,actual);
            throw new InvalidDataException("CHECKPOINT_MISMATCH:" + label);
        }
    }
    static string ReadText(string path)
    {
        if(path.EndsWith(".gz",StringComparison.Ordinal)) {
            using var file=File.OpenRead(path);
            using var gzip=new System.IO.Compression.GZipStream(file,System.IO.Compression.CompressionMode.Decompress);
            using var reader=new StreamReader(gzip);return reader.ReadToEnd();
        }
        return File.ReadAllText(path);
    }
    public static JsonObject Load(string path, JsonElement request)
    {
        var wrapper = JsonNode.Parse(ReadText(path))!.AsObject();
        if (wrapper["schema"]?.GetValue<string>() != Schema) throw new InvalidDataException("CHECKPOINT_SCHEMA");
        var payload = wrapper["payload"] ?? throw new InvalidDataException("CHECKPOINT_PAYLOAD");
        if (wrapper["evidence_ref"] is JsonObject reference)
        {
            if (reference["file"]?.GetValue<string>() != "decision.json.gz" || payload["evidence"] != null)
                throw new InvalidDataException("CHECKPOINT_EVIDENCE_REFERENCE");
            string decisionPath = Path.Combine(Directory.GetParent(Path.GetDirectoryName(Path.GetFullPath(path))!)!.FullName, "decision.json.gz");
            var evidence = JsonNode.Parse(ReadText(decisionPath))!["decision_evidence"]!.AsArray();
            int count = reference["count"]!.GetValue<int>();
            if (count < 0 || count > evidence.Count || count != payload["history"]!.AsArray().Count)
                throw new InvalidDataException("CHECKPOINT_EVIDENCE_LENGTH");
            payload["evidence"] = new JsonArray(evidence.Take(count).Select(e => e!.DeepClone()).ToArray());
        }
        // Integrity is still checked against the ENTIRE reconstructed payload,
        // including all evidence bytes. A file reference never defines equality.
        if (Hash(payload) != wrapper["sha256"]!.GetValue<string>()) throw new InvalidDataException("CHECKPOINT_CHECKSUM");
        RequireEqual(payload["context"], Context(request), "context");
        RequireEqual(payload["identity"], JsonSerializer.SerializeToNode(Program.NativeIdentity()), "binary_identity");
        return payload.AsObject();
    }
    public static void WriteAtomic(string path, JsonObject payload, bool referenceEvidence = false)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var wrapper = new JsonObject { ["schema"]=Schema, ["sha256"]=Hash(payload), ["payload"]=payload };
        if (referenceEvidence)
        {
            wrapper["evidence_ref"] = new JsonObject { ["file"]="decision.json.gz", ["count"]=payload["evidence"]!.AsArray().Count };
            payload.AsObject().Remove("evidence");
        }
        string temporary = path + ".tmp";
        if(path.EndsWith(".gz",StringComparison.Ordinal)) {
            using var file=File.Create(temporary);
            using var gzip=new System.IO.Compression.GZipStream(file,System.IO.Compression.CompressionLevel.Fastest);
            using var writer=new StreamWriter(gzip);writer.Write(wrapper.ToJsonString());
        } else File.WriteAllText(temporary, wrapper.ToJsonString());
        File.Move(temporary,path,true);
    }
}
