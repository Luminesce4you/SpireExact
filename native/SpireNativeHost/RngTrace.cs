using System.Reflection;
using System.Text.Json;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Random;
using MegaCrit.Sts2.Core.Rooms;

namespace SpireNativeHost;

internal sealed partial class CampaignReplay
{
    // G1: opt-in sidecar only. No RNG calls, setters, patches, observation or
    // checkpoint changes. Old expected_evidence remains usable with tracing on.
    StreamWriter? rngTrace;
    int rngTraceSequence;
    static readonly JsonSerializerOptions RngTraceJson = new(Program.Json) { WriteIndented = false };
    static readonly Dictionary<Type, FieldInfo[]> RngFields = [];

    void RngTraceInit()
    {
        if (request.TryGetProperty("capture_rng_streams", out var capture) && capture.GetBoolean())
            rngTrace = new StreamWriter(Path.Combine(output, "rng-streams.jsonl"));
    }

    static object LocalRngFields(object owner)
    {
        var type = owner.GetType();
        if (!RngFields.TryGetValue(type, out var fields))
        {
            var list = new List<FieldInfo>();
            for (Type? current = type; current != null; current = current.BaseType)
                list.AddRange(current.GetFields(BindingFlags.Instance | BindingFlags.Public |
                    BindingFlags.NonPublic | BindingFlags.DeclaredOnly).Where(f => f.FieldType == typeof(Rng)));
            RngFields[type] = fields = list.ToArray();
        }
        // Read backing fields, not potentially lazy getters. A null stream stays
        // absent: logging must not initialise an encounter or consume Chaotic.
        return fields.Where(f => f.GetValue(owner) is Rng).ToDictionary(
            f => f.DeclaringType!.Name + "." + f.Name,
            f => ((Rng)f.GetValue(owner)!).ToSerializable());
    }

    void RngTraceCapture(string point, string decisionPhase)
    {
        if (rngTrace == null || run == null) return;
        var local = new Dictionary<string, object>();
        if (run.Map != null) local["map"] = LocalRngFields(run.Map);
        if (run.CurrentRoom is CombatRoom room)
            local["encounter:" + room.Encounter.Id.Entry] = LocalRngFields(room.Encounter);
        if (run.CurrentRoom is EventRoom eventRoom)
        {
            var model = eventRoom.LocalMutableEvent;
            if (model != null) local["event:" + model.Id.Entry] = LocalRngFields(model);
        }
        var combat = CombatManager.Instance.DebugOnlyGetState();
        if (combat != null)
            foreach (var creature in combat.Enemies)
                if (creature.Monster != null)
                    local["monster:" + creature.CombatId + ":" + creature.Monster.Id.Entry] = LocalRngFields(creature.Monster);
        if (sphere != null) local["crystal_sphere"] = LocalRngFields(sphere);
        var row = new
        {
            schema = "spire-rng-streams/v1", sequence = rngTraceSequence++, point,
            prefix_length = transcript.Count, phase = decisionPhase,
            act = run.CurrentActIndex, floor = run.TotalFloor,
            room = run.CurrentRoom?.RoomType.ToString(),
            previous_action = transcript.LastOrDefault(),
            run_rng = run.Rng.ToSerializable(), run_odds = run.Odds.ToSerializable(),
            players = run.Players.Select((p, slot) => new
            {
                slot, rng = p.PlayerRng.ToSerializable(), odds = p.PlayerOdds.ToSerializable()
            }).ToArray(),
            local_rng = local,
            scope = "read-only decision boundary; local RNGs are room-scoped; counters are API draws, not PRNG steps"
        };
        rngTrace.WriteLine(JsonSerializer.Serialize(row, RngTraceJson));
        rngTrace.Flush();
    }

    void RngTraceFinish()
    {
        if (rngTrace == null) return;
        try { RngTraceCapture("return", phase); }
        finally { rngTrace.Dispose(); rngTrace = null; }
    }
}
