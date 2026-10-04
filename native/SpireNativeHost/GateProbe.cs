using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Rngs;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Random;
using MegaCrit.Sts2.Core.Runs;

namespace SpireNativeHost;

// Synthetic gate probe (request field "probe"). The supplied history is replayed to a quiescent
// map menu; there the deck is edited with the game's own commands and ONE fight is entered and
// played by the advisor. The request stops at the first decision after that fight.
//
// A probe is an allocation signal for the coordinator and nothing else. Its state never existed
// on a real route, so the answer carries no value, is never a trajectory, a checkpoint or a
// cached result, and a won or lost probe proves nothing about any real state. The coordinator
// runs probes in a worker that it discards afterwards.
//
//   "probe": { "edits": [ {"op":"add","card":ID,"upgrade":0}, {"op":"remove","card":ID,"upgrade":0},
//                         {"op":"upgrade","card":ID,"upgrade":0} ],
//              "hp": 55,                              optional current HP override
//              "rng": 12345,                          optional sample: re-seeds the combat streams
//              "enter": {"kind":"map","col":3,"row":15}          a legal map action of that menu, or
//                       {"kind":"encounter","boss":0|1}          this act's first / second boss, or
//                       {"kind":"encounter","id":ENCOUNTER_ID} }
internal sealed partial class CampaignReplay
{
    JsonElement? probe;
    bool probeEntered, probeFought, probeFinished;
    int probeEntryIndex = -1;
    ulong? probeEncounterSeed;
    readonly List<object> probeEdits = [];

    void ProbeInit(JsonElement request)
    {
        if (!request.TryGetProperty("probe", out var spec) || spec.ValueKind != JsonValueKind.Object) return;
        if (captureCheckpoints) throw new ArgumentException("PROBE_WITH_CHECKPOINT_CAPTURE");
        if (!generateCandidate) throw new ArgumentException("PROBE_NEEDS_GENERATED_FIGHT");
        if (stopAtStrategicDecision) throw new ArgumentException("PROBE_WITH_STRATEGIC_STOP");
        if (request.TryGetProperty("expected_evidence", out _)) throw new ArgumentException("PROBE_IN_VERIFICATION_REPLAY");
        if (request.TryGetProperty("stop_at_floor", out _)) throw new ArgumentException("PROBE_WITH_STOP_FLOOR");
        probe = spec.Clone();
    }

    // Called by Choose once the history has been consumed. Returns the entering action for the
    // main loop, null to let the fight continue, or throws NeedDecision when the request ends.
    JsonObject? ProbeStep(string name, object[] options)
    {
        if (!probeEntered)
        {
            if (name is not ("map" or "shop") || CombatManager.Instance.IsInProgress || roomTasks.Any(t => !t.IsCompleted))
            {
                boundary = "PROBE_NOT_AT_MAP_BOUNDARY:" + name; menu = options; throw new NeedDecision();
            }
            return EnterProbe(name, options);
        }
        if (name == "combat" || name == "select_cards" && CombatManager.Instance.IsInProgress)
        {
            probeFought = true;
            return null;
        }
        probeFinished = true; menu = options; throw new NeedDecision();
    }

    JsonObject EnterProbe(string name, object[] options)
    {
        var spec = probe!.Value;
        // Production paired probes must first reproduce the source map entry
        // (deck, resources and RNG included). A mismatch stays unsupported;
        // never apply synthetic edits to a different replayed entry.
        if (spec.TryGetProperty("expected_entry_observation", out var expected))
        {
            if (expected.ValueKind != JsonValueKind.Object) throw new ArgumentException("PROBE_ENTRY_OBSERVATION_REQUIRED");
            RoomCheckpoint.RequireEqual(JsonNode.Parse(expected.GetRawText()),
                JsonSerializer.SerializeToNode(Observe(), Program.Json), "probe_source_entry");
        }
        if (spec.TryGetProperty("edits", out var edits))
            foreach (var edit in edits.EnumerateArray()) probeEdits.Add(ApplyEdit(edit));
        if (spec.TryGetProperty("hp", out var hp))
        {
            int value = hp.GetInt32();
            if (value < 1 || value > player.Creature.MaxHp) throw new ArgumentOutOfRangeException("probe.hp");
            player.Creature.SetCurrentHpInternal(value);
        }
        if (spec.TryGetProperty("rng", out var sample)) ReseedCombat(sample.GetUInt64());
        var enter = spec.GetProperty("enter");
        JsonObject action;
        switch (enter.GetProperty("kind").GetString())
        {
            case "map":
            {
                var wanted = JsonNode.Parse(enter.GetRawText());
                action = options.Select(o => JsonSerializer.SerializeToNode(o)!).SingleOrDefault(o => JsonNode.DeepEquals(o, wanted))?.AsObject()
                    ?? throw new InvalidOperationException("PROBE_ENTER_NOT_LEGAL");
                break;
            }
            case "encounter":
            {
                EncounterModel canonical = enter.TryGetProperty("id", out var id)
                    ? ModelDb.All.OfType<EncounterModel>().Single(e => e.Id.Entry == id.GetString()).CanonicalInstance
                    : enter.GetProperty("boss").GetInt32() == 0 ? run.Act.BossEncounter
                    : run.Act.SecondBossEncounter ?? throw new InvalidOperationException("PROBE_NO_SECOND_BOSS");
                EncounterModel encounter = canonical.ToMutable();
                if (probeEncounterSeed.HasValue) encounter._rng = new Rng(probeEncounterSeed.Value);
                if (enter.TryGetProperty("act_floor", out var floor)) run.ActFloor = floor.GetInt32();
                action = new JsonObject { ["kind"] = "probe_enter", ["encounter"] = canonical.Id.Entry };
                executions[action.ToJsonString()] = () => RunManager.Instance.EnterRoomDebug(encounter.RoomType, MapPointType.Unassigned, encounter, false);
                break;
            }
            default: throw new ArgumentException("probe.enter.kind");
        }
        probeEntered = true;
        probeEntryIndex = transcript.Count;
        generatedActions++;
        transcript.Add(action.DeepClone());
        traceWriter.WriteLine(action.ToJsonString());
        // The observation of this row is the entry state after the edits.
        RecordEvidence(name, options);
        return action;
    }

    object ApplyEdit(JsonElement edit)
    {
        string op = edit.GetProperty("op").GetString()!;
        string id = edit.GetProperty("card").GetString()!;
        int level = edit.TryGetProperty("upgrade", out var u) ? u.GetInt32() : 0;
        switch (op)
        {
            case "add":
            {
                CardModel canonical = ModelDb.AllCards.Single(c => c.Id.Entry == id);
                CardModel card = run.CreateCard(canonical, player);
                for (int i = 0; i < level && card.IsUpgradable; i++) CardCmd.Upgrade(card, CardPreviewStyle.None);
                var added = CardPileCmd.Add(card, PileType.Deck);
                Wait(added);
                if (!added.Result.success) throw new InvalidOperationException("PROBE_EDIT_REJECTED:add:" + id);
                break;
            }
            case "remove":
            {
                CardModel card = player.Deck.Cards.FirstOrDefault(c => c.Id.Entry == id && c.CurrentUpgradeLevel == level)
                    ?? throw new InvalidOperationException("PROBE_EDIT_MISSING_CARD:remove:" + id);
                Wait(CardPileCmd.RemoveFromDeck(card, false));
                break;
            }
            case "upgrade":
            {
                CardModel card = player.Deck.Cards.FirstOrDefault(c => c.Id.Entry == id && c.CurrentUpgradeLevel == level && c.IsUpgradable)
                    ?? throw new InvalidOperationException("PROBE_EDIT_MISSING_CARD:upgrade:" + id);
                CardCmd.Upgrade(card, CardPreviewStyle.None);
                break;
            }
            default: throw new ArgumentException("probe.edits.op");
        }
        return new { op, card = id, upgrade = level };
    }

    // Same construction as the pinned solver's pre-combat forecast: one independent stream seed
    // per combat RNG from a SplitMix64 sequence, so a sample number names a reproducible fight.
    void ReseedCombat(ulong sample)
    {
        RunRngType[] streams =
        [
            RunRngType.Shuffle, RunRngType.CombatCardGeneration, RunRngType.CombatPotionGeneration,
            RunRngType.CombatCardSelection, RunRngType.CombatEnergyCosts, RunRngType.CombatTargets,
            RunRngType.MonsterAi, RunRngType.Niche, RunRngType.CombatOrbs,
        ];
        ulong state = sample;
        foreach (var stream in streams) run.Rng.MockRng(stream, NextSample(ref state));
        probeEncounterSeed = NextSample(ref state);
    }

    static ulong NextSample(ref ulong state)
    {
        state += 0x9E3779B97F4A7C15UL;
        ulong value = state;
        value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9UL;
        value = (value ^ (value >> 27)) * 0x94D049BB133111EBUL;
        return value ^ (value >> 31);
    }

    object ProbeResult()
    {
        bool dead = player?.Creature.IsDead == true;
        bool complete = boundary == null && probeEntered && (probeFinished || dead);
        return new
        {
            schema = "spire-native-probe/v1", phase,
            status = complete ? "PROBE" : "UNSUPPORTED", synthetic = true,
            reason = complete ? null : boundary ?? "PROBE_INCOMPLETE",
            value = (int[]?)null, native_terminal_observed = false,
            consumed = cursor,
            probe = new
            {
                edits = probeEdits, entered = probeEntered, fought = probeFought,
                won = complete && probeFought ? !dead : (bool?)null, entry_index = probeEntryIndex,
            },
            observation = Observe(),
            terminal_combat = dead ? TerminalCombat() : null,
            trace = transcript, decision_evidence = decisionEvidence,
            checkpoints = Array.Empty<object>(), restored_prefix = restoredPrefix,
            restore_menu_checked = restoreMenuChecked, advisor_metrics = advisor?.Metrics(),
            performance = Perf.Snapshot(), information_mode = "full",
            game_equivalence_verified = false,
            semantic_scope = "synthetic deck edit and one fight in offline TestMode; allocation signal only, never a route, checkpoint, cached result, bound or proof",
        };
    }
}
