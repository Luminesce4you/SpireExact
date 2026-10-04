using System.Text.Json;
using System.Text.Json.Nodes;
using System.Collections;
using HarmonyLib;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;

namespace SpireNativeHost;

// Experimental, off by default. The offered CardModel instances are returned
// through the existing native reward selector; no card is fabricated here.
internal sealed partial class CampaignReplay
{
    JsonElement? cardMenuProbe, realCardMenuChoice;
    bool cardMenuChoiceRecorded, cardMenuSampleApplied;
    int cardMenuChoiceIndex = -1;
    JsonNode? cardMenuRecordedChoice, cardMenuSourceObservation, cardMenuSourceActions;
    JsonNode? cardMenuSourceNativeState;
    string? cardMenuSourceProgressRaw;
    JsonNode? cardMenuF1Entry, cardMenuF2Entry, cardMenuSampleEntry;
    bool cardMenuF1Won, cardMenuF2Won;
    readonly List<object> cardMenuSources = [];

    void CardMenuProbeInit(JsonElement source)
    {
        if (source.TryGetProperty("card_menu_probe", out var synthetic))
        {
            if (synthetic.ValueKind != JsonValueKind.Object) throw new ArgumentException("CARD_MENU_PROBE_REQUIRED");
            cardMenuProbe = synthetic.Clone();
        }
        if (source.TryGetProperty("real_card_menu_choice", out var real))
        {
            if (real.ValueKind != JsonValueKind.Object) throw new ArgumentException("REAL_CARD_MENU_CHOICE_REQUIRED");
            realCardMenuChoice = real.Clone();
        }
        if (!cardMenuProbe.HasValue && !realCardMenuChoice.HasValue) return;
        if (cardMenuProbe.HasValue && realCardMenuChoice.HasValue) throw new ArgumentException("CARD_MENU_MODE_CONFLICT");
        if (!generateCandidate || stopAtStrategicDecision || source.TryGetProperty("stop_at_floor", out _))
            throw new ArgumentException("CARD_MENU_NEEDS_UNSCOPED_GENERATOR");
        if (source.TryGetProperty("probe", out _) || source.TryGetProperty("expected_evidence", out _)
            || source.TryGetProperty("f1_winner_proposal", out _)) throw new ArgumentException("CARD_MENU_REQUEST_CONFLICT");
        if (source.GetProperty("character").GetString() != "IRONCLAD" || source.GetProperty("ascension").GetInt32() != 10
            || source.GetProperty("unlocks").GetString() != "all") throw new ArgumentException("CARD_MENU_SCOPE");
        if (source.TryGetProperty("checkpoint", out _) || captureCheckpoints)
            throw new ArgumentException("CARD_MENU_PROBE_WITH_CHECKPOINT");
        if (cardMenuProbe.HasValue && !source.TryGetProperty("advisor", out _))
            throw new ArgumentException("CARD_MENU_PROBE_NEEDS_ADVISOR");
        var spec = cardMenuProbe ?? realCardMenuChoice!.Value;
        if (!spec.TryGetProperty("expected_entry_observation", out var observation) || observation.ValueKind != JsonValueKind.Object
            || !spec.TryGetProperty("expected_actions", out var actions) || actions.ValueKind != JsonValueKind.Array
            || !spec.TryGetProperty("choice", out var choice) || choice.ValueKind != JsonValueKind.Object
            || !spec.TryGetProperty("expected_source_native_state",out var nativeState) || nativeState.ValueKind != JsonValueKind.Object)
            throw new ArgumentException("CARD_MENU_GUARD_REQUIRED");
        if (cardMenuProbe.HasValue && (!spec.TryGetProperty("rng", out var rng) || !rng.TryGetUInt64(out _)))
            throw new ArgumentException("CARD_MENU_SAMPLE_REQUIRED");
    }

    JsonNode? CardMenuSourceNativeState()
    {
        // This is an identity guard, never a resumable snapshot. Pending task
        // semantics still come from a fresh opening replay of the full prefix.
        var runtime = CardMenuActionRuntime.Capture();
        if (runtime == null) return null;
        var rewards = CardMenuRewardState();
        if (rewards == null) return null;
        return new JsonObject {
            ["run"] = new JsonObject { ["native_json"] = RoomCheckpoint.Canonical(RoomCheckpoint.CaptureRun()) },
            ["progress"] = NativeProgressGuard.Capture(),
            ["action_runtime"] = runtime,
            ["reward_sync"] = rewards
        };
    }

    object? CardMenuField(object value,string field) =>
        (AccessTools.Field(value.GetType(),field) ?? throw new InvalidDataException("CARD_MENU_REWARD_FIELD:"+field)).GetValue(value);

    JsonObject? CardMenuRewardState()
    {
        // This iteration covers the ordinary single-player reward stack. A
        // nested set, buffered message or unknown serializer stays UNKNOWN.
        if(currentRewards==null || run.Players.Count!=1)return null;
        var sync=RunManager.Instance.RewardsSetSynchronizer;
        var states=(IList)CardMenuField(sync,"_rewardStates")!;
        if(states.Count!=1)return null;
        var state=states[0]!;
        if(((IList)CardMenuField(state,"bufferedMessages")!).Count!=0)return null;
        var stack=(IList)CardMenuField(state,"rewardsStack")!;
        if(stack.Count!=1 || !ReferenceEquals(CardMenuField(stack[0]!,"set"),currentRewards))return null;
        var completion=CardMenuField(stack[0]!,"completionSource")!;
        var completionTask=(Task)(AccessTools.Property(completion.GetType(),"Task")
            ??throw new InvalidDataException("CARD_MENU_REWARD_COMPLETION_API")).GetValue(completion)!;
        if(completionTask.IsCompleted || completionTask.IsFaulted || completionTask.IsCanceled)return null;
        var completed=(IDictionary)CardMenuField(state,"completedRewards")!;
        var completedRows=new JsonArray();
        foreach(object key in completed.Keys.Cast<object>().OrderBy(k=>Convert.ToInt64(k)))
            completedRows.Add(new JsonObject { ["id"]=Convert.ToInt64(key),["state"]=Convert.ToInt32(completed[key]) });
        var rewardRows=new JsonArray();
        int index=0;
        foreach(var reward in currentRewards.Rewards)
        {
            rewardRows.Add(new JsonObject { ["index"]=index++,["type"]=reward.GetType().FullName,
                ["selected"]=reward.SuccessfullySelected,["populated"]=reward.IsPopulated,
                ["rewards_set_index"]=reward.RewardsSetIndex,["declined"]=declinedRewards.Contains(reward),
                ["native_json"]=JsonSerializer.Serialize(reward.ToSerializable(),JsonSerializationUtility.Options) });
        }
        var cards=new JsonArray();
        foreach(var card in rewardCards)
            cards.Add(JsonSerializer.Serialize(card.ToSerializable(),JsonSerializationUtility.Options));
        var alternatives=new JsonArray();
        foreach(var alternative in currentCardAlternatives)
            alternatives.Add(new JsonObject { ["type"]=alternative.GetType().FullName,["option_id"]=alternative.OptionId,
                ["used"]=usedAlternatives.Contains(alternative) });
        return new JsonObject {
            ["scope"]="single pending reward set; buffered messages empty; tasks rebuilt only by a fresh prefix replay",
            ["set_id"]=currentRewards.Id,["disallow_skipping"]=currentRewards.DisallowSkipping,
            ["all_selected"]=currentRewards.AllRewardsSuccessfullySelected,
            ["set_completed"]=sync.IsRewardsSetCompleted(currentRewards),["completion_task_status"]=completionTask.Status.ToString(),
            ["next_set_id"]=Convert.ToInt64(CardMenuField(state,"nextId")),["completed_sets"]=completedRows,
            ["reward_cards"]=cards,["rewards"]=rewardRows,["alternatives"]=alternatives,
            ["pending_room_tasks"]=roomTasks.Count(t=>!t.IsCompleted),["pending_background_tasks"]=background.Count(t=>!t.IsCompleted)
        };
    }

    object CardMenuDecorateEvidence(string name, object evidence)
    {
        if (name != "card_reward" || run.CurrentActIndex != 2
            || !request.TryGetProperty("capture_card_menu_state",out var capture) || !capture.GetBoolean()) return evidence;
        // A missing serializer/runtime guard defers B; it must not break the
        // ordinary legal rollout or be treated as an empty complete state.
        // Keep research artifacts outside decision_evidence: independent
        // whole-run verification compares that original evidence exactly.
        try { cardMenuSources.Add(new { index = transcript.Count - 1, source_native_state = CardMenuSourceNativeState(),
            native_progress_raw = NativeProgressGuard.RawCapture() }); }
        catch (Exception error) when(!WorkerMemoryTelemetry.IsOutOfMemory(error)) { cardMenuSources.Add(new { index = transcript.Count - 1, source_native_state = (JsonNode?)null, error = error.GetType().Name }); }
        return evidence;
    }

    JsonObject? CardMenuProbeStep(string name, object[] options)
    {
        if (!cardMenuChoiceRecorded)
        {
            if (name != "card_reward" || run.CurrentActIndex != 2 || run.Acts.Count != 3
                || run.Act.SecondBossEncounter == null || CombatManager.Instance.IsInProgress)
                throw new InvalidOperationException("CARD_MENU_NOT_AT_REAL_THIRD_ACT_REWARD");
            var spec = cardMenuProbe ?? realCardMenuChoice!.Value;
            var observation = JsonSerializer.SerializeToNode(Observe(), Program.Json);
            var actions = JsonSerializer.SerializeToNode(options, Program.Json);
            RoomCheckpoint.RequireEqual(JsonNode.Parse(spec.GetProperty("expected_entry_observation").GetRawText()), observation,
                "card_menu_source_observation");
            RoomCheckpoint.RequireEqual(JsonNode.Parse(spec.GetProperty("expected_actions").GetRawText()), actions,
                "card_menu_source_actions");
            var nativeState = CardMenuSourceNativeState() ?? throw new InvalidOperationException("CARD_MENU_NATIVE_STATE_UNAVAILABLE");
            RoomCheckpoint.RequireEqual(JsonNode.Parse(spec.GetProperty("expected_source_native_state").GetRawText()), nativeState,
                "card_menu_source_native_state");
            var wanted = JsonNode.Parse(spec.GetProperty("choice").GetRawText());
            var chosen = options.Select(o => JsonSerializer.SerializeToNode(o, Program.Json))
                .SingleOrDefault(o => JsonNode.DeepEquals(o, wanted))?.AsObject()
                ?? throw new InvalidOperationException("CARD_MENU_CHOICE_NOT_LEGAL");
            string kind = chosen["kind"]!.GetValue<string>();
            if (kind is not ("card_reward" or "card_skip")) throw new ArgumentException("CARD_MENU_ARM_SCOPE");
            cardMenuSourceObservation = observation;
            cardMenuSourceActions = actions;
            cardMenuSourceNativeState = nativeState;
            cardMenuSourceProgressRaw = NativeProgressGuard.RawCapture();
            cardMenuRecordedChoice = chosen.DeepClone();
            cardMenuChoiceIndex = transcript.Count;
            cardMenuChoiceRecorded = true;
            generatedActions++;
            transcript.Add(chosen.DeepClone());
            traceWriter.WriteLine(chosen.ToJsonString());
            RecordEvidence(name, options);
            // The original GetSelectedCardReward switch executes this selection,
            // with the original offered instance, upgrades and acquisition hooks.
            return chosen;
        }
        if (!cardMenuProbe.HasValue) return null;
        if (!cardMenuSampleApplied && name == "map" && run.CurrentActIndex == 2 && cardMenuF1Entry == null
            && run.CurrentMapPoint?.Children.Any(p => p.PointType == MapPointType.Boss) == true)
        {
            cardMenuSampleEntry = JsonSerializer.SerializeToNode(Observe(), Program.Json);
            ReseedCombat(cardMenuProbe.Value.GetProperty("rng").GetUInt64());
            cardMenuSampleApplied = true;
            // The native EnterMapCoord remains the entering operation. The
            // encounter itself, HP, deck and rewards are not replaced.
        }
        if ((name == "combat" || name == "select_cards" && CombatManager.Instance.IsInProgress)
            && run.CurrentActIndex == 2 && run.CurrentRoom is CombatRoom room && room.RoomType.ToString() == "Boss")
        {
            if (room.Encounter.Id == run.Act.BossEncounter.Id && cardMenuF1Entry == null)
            {
                if (!cardMenuSampleApplied) throw new InvalidOperationException("CARD_MENU_NO_REAL_F1_MAP_SAMPLE");
                cardMenuF1Entry = JsonSerializer.SerializeToNode(Observe(), Program.Json);
            }
            else if (run.Act.SecondBossEncounter is { } second && room.Encounter.Id == second.Id && cardMenuF2Entry == null)
                cardMenuF2Entry = JsonSerializer.SerializeToNode(Observe(), Program.Json);
        }
        return null;
    }

    void CardMenuProbeCombatWon(CombatRoom room)
    {
        if (!cardMenuProbe.HasValue || !cardMenuChoiceRecorded || run.CurrentActIndex != 2) return;
        if (room.Encounter.Id == run.Act.BossEncounter.Id) cardMenuF1Won = true;
        else if (run.Act.SecondBossEncounter is { } second && room.Encounter.Id == second.Id) cardMenuF2Won = true;
    }

    object CardMenuProbeResult()
    {
        bool dead = player?.Creature.IsDead == true;
        bool complete = boundary == null && cardMenuChoiceRecorded && cursor == history.Length && (dead || victory.HasValue);
        return new
        {
            schema = "spire-native-card-menu-probe/v1", phase,
            status = complete ? "CARD_MENU_PROBE" : "UNSUPPORTED", synthetic = true,
            reason = complete ? null : boundary ?? "CARD_MENU_PROBE_INCOMPLETE",
            value = (int[]?)null, native_terminal_observed = false, consumed = cursor,
            card_menu_probe = new
            {
                menu_checked = cardMenuChoiceRecorded, choice_index = cardMenuChoiceIndex,
                choice = cardMenuRecordedChoice, source_observation = cardMenuSourceObservation,
                source_actions = cardMenuSourceActions, rng = cardMenuProbe!.Value.GetProperty("rng").GetUInt64(),
                source_native_state = cardMenuSourceNativeState,
                native_progress_raw = cardMenuSourceProgressRaw,
                sample_applied = cardMenuSampleApplied, sample_entry = cardMenuSampleEntry,
                native_finished = complete, native_won = complete && victory == true,
                f1_entry = cardMenuF1Entry, f2_entry = cardMenuF2Entry,
                f1_won = cardMenuF1Won, f2_won = cardMenuF2Won
            },
            observation = Observe(), terminal_combat = dead ? TerminalCombat() : null,
            trace = transcript, decision_evidence = decisionEvidence, checkpoints = Array.Empty<object>(),
            restored_prefix = 0, restore_menu_checked = false, advisor_metrics = advisor?.Metrics(),
            performance = Perf.Snapshot(), information_mode = "full", game_equivalence_verified = false,
            semantic_scope = "synthetic combat-stream samples after a real third-act card menu; native legal selection and continuation; no route, checkpoint, cache, bound or proof"
        };
    }
}
