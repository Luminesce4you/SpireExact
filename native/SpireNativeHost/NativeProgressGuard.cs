using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Saves;

namespace SpireNativeHost;

// Research-only comparison guard, not a resumable complete-host snapshot.
// Audit: docs/PROGRESS_GUARD_AUDIT.md. Keep original native JSON separately.
// Normalization is valid only for this audited DLL and Boolean win objective.
internal static class NativeProgressGuard
{
    const string GameSha="0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9";
    public const string Schema="spire-progress-guard/v1-"+GameSha;
    static bool pinned;
    internal static void RequirePinnedGame()
    {
        if(pinned)return;
        using var file=File.OpenRead(typeof(SaveManager).Assembly.Location);
        if(Convert.ToHexString(SHA256.HashData(file)).ToLowerInvariant()!=GameSha)
            throw new InvalidDataException("PROGRESS_GUARD_GAME_NOT_AUDITED");
        pinned=true;
    }
    public static string RawCapture()
    {
        RequirePinnedGame();
        return JsonSerializer.Serialize(SaveManager.Instance.Progress.ToSerializable(),JsonSerializationUtility.Options);
    }
    static void NormalizePositiveTimestamp(JsonObject item,string name)
    {
        // ObtainDate's zero test is real control flow: retain absent/zero/negative
        // exactly. Only the physical Unix value of a positive timestamp varies.
        if(item[name] is JsonValue value&&value.TryGetValue<long>(out long time)&&time>0)item[name]=1L;
    }
    public static JsonObject Capture()
    {
        var root=JsonNode.Parse(RawCapture())?.AsObject()
            ??throw new InvalidDataException("PROGRESS_GUARD_NATIVE_JSON");
        if(root["unique_id"] is not JsonValue id||!id.TryGetValue<string>(out _))
            throw new InvalidDataException("PROGRESS_GUARD_PROFILE_ID_SCHEMA");
        // Audited reads: profile/metrics/feedback + LoomingFruit icon variant.
        // HasCornucopia is called only by get_IconBaseName; pickup HP is unaffected.
        root["unique_id"]="__profile_identifier__";
        if(root["epochs"] is JsonArray epochs)
            foreach(var item in epochs.OfType<JsonObject>())NormalizePositiveTimestamp(item,"obtain_date");
        if(root["unlocked_achievements"] is JsonArray achievements)
            foreach(var item in achievements.OfType<JsonObject>())NormalizePositiveTimestamp(item,"unlock_time");
        // Every other wire field, list order, discovery, statistic and counter is
        // retained. Unknown/new fields are never normalized or silently omitted.
        return new JsonObject {["normalization_schema"]=Schema,["native_json"]=RoomCheckpoint.Canonical(root)};
    }
}
