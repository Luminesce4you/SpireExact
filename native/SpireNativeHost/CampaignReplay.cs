using System.Globalization;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using HarmonyLib;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using MegaCrit.Sts2.Core.TestSupport;
using MegaCrit.Sts2.Core.Unlocks;
using OfflineSearchHarness;

namespace SpireNativeHost;

// This adapter executes installed game commands. No shadow card/monster rules.
// Replay prefixes are the state identity: diagnostic projections are never dedup keys.
internal sealed partial class CampaignReplay : ICardSelector
{
    internal sealed class NeedDecision : Exception { }
    readonly JsonElement[] history;
    readonly JsonElement request;
    readonly string output;
    readonly bool captureCheckpoints;
    readonly int stopAtFloor;
    readonly List<object> checkpointRefs = [];
    readonly StreamWriter traceWriter;
    readonly StreamWriter evidenceWriter;
    readonly bool lowIo;
    readonly bool eventDrivenSettle;
    JsonObject? restoredCheckpoint;
    ResearchProgressLifecycle? researchProgress;
    bool restoreMenuChecked;
    int restoredPrefix;
    int replayedActions;
    int generatedActions;
    static bool guardsInstalled;
    PhaseProfiler Perf => PhaseProfiler.Current;
    readonly MainLoopContext loop;
    int cursor;
    RunState run = null!;
    Player player = null!;
    object[]? menu;
    string phase = "initializing";
    bool menuComplete = true;
    string? boundary;
    bool? victory;
    static CampaignReplay? active;
    readonly List<Task> background = [];
    readonly Dictionary<string, Func<Task>> executions = [];
    readonly int choiceLimit;
    readonly bool generateCandidate;
    readonly bool stopAtStrategicDecision;
    bool stoppedAtStrategicDecision;
    readonly int maxDecisions;
    readonly Random policyRandom;
    // Coordinator-supplied allocation tiers keyed by decision label. Explicit
    // request data: never legality, never a bound, never an environment switch.
    // Values are one tier per act index (the last entry covers later acts).
    readonly Dictionary<string,int[]> prior = new(StringComparer.Ordinal);
    readonly List<JsonNode> transcript = [];
    readonly List<object> decisionEvidence = [];
    readonly string journal;
    BeamAdvisor? advisor;
    readonly bool captureF1Winners;
    readonly JsonObject? f1WinnerProposal;
    readonly List<JsonObject> f1WinnerCandidates=[];
    readonly List<string> observedF1BossWins=[];
    bool f1ProposalEntryChecked;
    bool f1ProposalGuardAttempted, f1ProposalSecondBossEntered;
    string? f1ProposalFailedGuard;
    readonly HashSet<TreasureRoom> openedChests = [];
    readonly HashSet<TreasureRoom> completedTreasureRooms = [];
    readonly List<Task> roomTasks = [];
    CardModel[] selectionCards = [];
    CardModel[] rewardCards = [];
    SelectionRequest? selectionRequest;
    readonly HashSet<Reward> declinedRewards=[];
    readonly HashSet<CardRewardAlternative> usedAlternatives=[];
    RewardsSet? currentRewards;
    IReadOnlyList<CardRewardAlternative> currentCardAlternatives=[];
    CrystalSphereMinigame? sphere;

    CampaignReplay(JsonElement request, MainLoopContext loop)
    {
        this.loop = loop;
        this.request = request.Clone();
        output = request.GetProperty("out").GetString()!;
        lowIo=request.TryGetProperty("low_io",out var io)&&io.GetBoolean();
        // Experimental until complete replay and native regression gates pass.
        // Part of the request identity, never an untracked environment override.
        eventDrivenSettle=request.TryGetProperty("event_driven_settle",out var eds)&&eds.GetBoolean();
        traceWriter = new StreamWriter(Path.Combine(output,"trace.jsonl"));
        evidenceWriter = lowIo ? new StreamWriter(Stream.Null) : new StreamWriter(Path.Combine(output,"decision-evidence.jsonl"));
        captureCheckpoints = request.TryGetProperty("capture_checkpoints", out var cc) && cc.GetBoolean();
        stopAtFloor = request.TryGetProperty("stop_at_floor", out var sf) ? sf.GetInt32() : int.MaxValue;
        journal = Path.Combine(request.GetProperty("out").GetString()!, "trace.jsonl");
        history = request.GetProperty("history").EnumerateArray().Select(x => x.Clone()).ToArray();
        choiceLimit = request.TryGetProperty("max_choices", out var limit) ? limit.GetInt32() : 10000;
        if (choiceLimit < 1) throw new ArgumentOutOfRangeException("max_choices");
        generateCandidate = request.TryGetProperty("generate_candidate", out var candidate) && candidate.GetBoolean();
        captureF1Winners=request.TryGetProperty("capture_f1_winners",out var captureF1)&&captureF1.GetBoolean();
        if(request.TryGetProperty("f1_winner_proposal",out var winner))
            f1WinnerProposal=JsonNode.Parse(winner.GetRawText())?.AsObject()
                ??throw new ArgumentException("F1_WINNER_PROPOSAL_REQUIRED");
        if(captureF1Winners||f1WinnerProposal!=null)
        {
            if(!generateCandidate||!request.TryGetProperty("advisor",out _))throw new ArgumentException("F1_WINNERS_NEED_ADVISOR");
            if(request.TryGetProperty("probe",out _)||request.TryGetProperty("card_menu_probe",out _))
                throw new ArgumentException("F1_WINNERS_NOT_SYNTHETIC");
            if(request.TryGetProperty("expected_evidence",out _))throw new ArgumentException("F1_WINNERS_NOT_VERIFICATION");
            // Capture-only sources retain the normal cache/checkpoint/worker
            // path. Opt-in Progress lifecycle restores their real initial/CP
            // save DTO; consumers still replay fresh and retain all guards.
            if(f1WinnerProposal!=null&&(captureCheckpoints||request.TryGetProperty("checkpoint",out _)))
                throw new ArgumentException("F1_WINNER_REPLAY_MUST_START_FROM_INITIAL_STATE_WITHOUT_CHECKPOINTS");
        }
        stopAtStrategicDecision=request.TryGetProperty("stop_at_strategic_decision",out var strategicStop)&&strategicStop.GetBoolean();
        maxDecisions = request.TryGetProperty("max_decisions", out var steps) ? steps.GetInt32() : 3000;
        policyRandom = new Random(request.TryGetProperty("policy_seed", out var ps) ? ps.GetInt32() : 0);
        if (request.TryGetProperty("policy_prior", out var tiers) && tiers.ValueKind == JsonValueKind.Object)
            foreach (var tier in tiers.EnumerateObject())
            {
                int[] weights = tier.Value.ValueKind == JsonValueKind.Array
                    ? tier.Value.EnumerateArray().Select(v => v.GetInt32()).ToArray() : [tier.Value.GetInt32()];
                if (weights.Length == 0 || weights.Any(w => w is < -8 or > 8)) throw new ArgumentOutOfRangeException("policy_prior");
                if (weights.Any(w => w != 0)) prior[tier.Name] = weights;
            }
        ProbeInit(request);
        CardMenuProbeInit(request);
        MapRouteInit(request);
        ShopPreparationInit(request);
        ResourceTelemetryInit(request);
        CompletedPrefixInit(request);
        PreparationMenusInit(request);
    }

    internal static object Run(JsonElement request, MainLoopContext loop)
    {
        var session = new CampaignReplay(request, loop);
        active = session;
        try
        {
            session.InstallGuards();
            using (session.Perf.Enter("run_setup_or_restore")) session.Start(request);
            if (session.generateCandidate && request.TryGetProperty("advisor", out var config))
                session.advisor = new BeamAdvisor(config, Path.Combine(request.GetProperty("out").GetString()!, "advisor"));
            if(session.captureF1Winners||session.f1WinnerProposal!=null)
                session.advisor!.ConfigureF1WinnerReuse(session.captureF1Winners,session.f1WinnerProposal);
            using var selector = CardSelectCmd.UseSelector(session);
            RewardsSet.testSelector = session.SelectRewards;
            if (session.restoredCheckpoint == null)
                using (session.Perf.Enter("initial_act")) session.Wait(RunManager.Instance.EnterAct(0, false));
            while (true)
            {
                using (session.Perf.Enter("settle")) session.Settle();
                session.CheckTasks();
                session.CommitCompletedBoundary();
                if (session.victory.HasValue || session.player.Creature.IsDead) break;
                session.executions.Clear();
                using (session.Perf.Enter("legal_menu")) session.BuildMenu();
                var action = session.Choose(session.phase, session.executions.Keys.Select(s => JsonNode.Parse(s)!).Cast<object>().ToArray());
                string key = action.ToJsonString();
                if(session.captureResourceTelemetry)session.RecordResourceAttempt(action);
                if(WorkerMemoryTelemetry.Enabled)session.completedOuterActionInFlight=true;
                using (session.Perf.Enter(session.cursor <= session.history.Length && session.generatedActions == 0
                    ? "prefix_native_execute" : "new_native_execute")) session.Wait(session.executions[key]());
                session.MarkOuterActionCompleted();
                if(session.mapRoutePlan!=null)session.MapRouteActionCompleted(action);
            }
            if (session.cursor != session.history.Length)
                throw new InvalidOperationException("History continues after the native terminal state");
        }
        catch (NeedDecision) { }
        catch(Exception error) when(WorkerMemoryTelemetry.IsOutOfMemory(error)) { throw; }
        catch (Exception error)
        {
            if (session.menu is null)
                session.boundary = error.ToString();
            else if (!ContainsDecisionException(error))
                session.boundary = error.ToString();
        }
        session.FinalizeMapRouteOutcome();
        session.traceWriter.Flush(); session.evidenceWriter.Flush();
        session.traceWriter.Dispose(); session.evidenceWriter.Dispose();
        session.Perf.Count("prefix_replayed_actions", session.replayedActions);
        session.Perf.Count("checkpoint_skipped_actions", session.restoredPrefix);
        session.Perf.Count("new_actions", session.generatedActions);
        session.Perf.Count("replay_prefix_solver_calls", 0);
        // A synthetic probe has its own answer: no value, never TERMINAL / BUDGET / DECISION.
        if (session.probe.HasValue) return session.WithCampaignMetadata(session.ProbeResult());
        if (session.cardMenuProbe.HasValue) return session.WithCampaignMetadata(session.CardMenuProbeResult());
        bool? won = session.victory ?? (session.player?.Creature.IsDead == true ? false : null);
        if (session.boundary != null) won = null;
        return session.WithCampaignMetadata(new
        {
            schema = "spire-native-decision/v1", phase = session.phase,
            status = session.boundary?.StartsWith("candidate_") == true ? "BUDGET" : session.boundary != null ? "UNSUPPORTED" : won.HasValue ? "TERMINAL" : "DECISION",
            actions = session.menu ?? [], complete = false,
            known_menu_enumerated = session.menuComplete && session.boundary == null,
            exhaustive_action_audit_passed = false,
            consumed = session.cursor, reason = session.boundary,
            requested_strategic_boundary = session.stoppedAtStrategicDecision,
            value = won.HasValue ? new[] { won.Value ? 1 : 0, ScoreUtility.CalculateScore(session.run, won.Value) } : null,
            observation = session.Observe(),
            terminal_combat = won == false ? session.TerminalCombat() : null,
            game_equivalence_verified = false,
            semantic_scope = "installed game commands in offline TestMode; native Godot parity not certified",
            objective = "whole_run_victory/v1", theoretical_upper_bound = 1,
            native_terminal_observed = session.victory.HasValue,
            trace = session.transcript, decision_evidence = session.decisionEvidence,
            checkpoints = session.checkpointRefs, restored_prefix = session.restoredPrefix,
            restore_menu_checked = session.restoreMenuChecked, advisor_metrics = session.advisor?.Metrics(),
            performance = session.Perf.Snapshot(), information_mode = "full"
        });
    }

    // i068 production adapter: read the generated native campaign, never infer
    // its act/boss count from an ascension number or a seed. This request flag
    // leaves older requests and every per-decision observation unchanged.
    // Reflect only our result DTO once, outside the decision/search hot path.
    object WithCampaignMetadata(object result)
    {
        bool includeCampaign=request.TryGetProperty("include_campaign_metadata",out var include)&&include.GetBoolean();
        if(!includeCampaign&&!captureF1Winners&&f1WinnerProposal==null&&researchProgress==null
            &&!captureRouteGraph&&mapRoutePlan==null&&!captureResourceTelemetry&&!CapturePreparationMenus)return result;
        var enriched = result.GetType().GetProperties().ToDictionary(p => p.Name, p => p.GetValue(result));
        enriched["campaign"] = run == null ? null : new
        {
            act_count = run.Acts.Count,
            final_act_boss_count = run.Acts.Last().SecondBossEncounter == null ? 1 : 2
        };
        if(captureF1Winners||f1WinnerProposal!=null)
        {
            enriched["f1_winner_candidates"]=f1WinnerCandidates;
            enriched["f1_winner_reuse"]=new {
                terminal_encounter=(run?.CurrentRoom as CombatRoom)?.CombatState.Encounter?.Id.Entry,
                final_second_encounter=run?.Acts.Last().SecondBossEncounter?.Id.Entry,
                native_identity=Program.NativeIdentity(),guard_attempted=f1ProposalGuardAttempted,
                failed_entry_guard=f1ProposalFailedGuard,
                native_boss_wins=observedF1BossWins,
                native_f1_won=f1WinnerProposal?["encounter"] is JsonValue f1Id
                    &&observedF1BossWins.Contains(f1Id.GetValue<string>()),
                native_f2_entered=f1ProposalSecondBossEntered,
                proposal_entry_checked=f1ProposalEntryChecked,deployment=advisor?.F1ReuseStatus(),
                scope="already computed forecasts; alternative real actions only; not a combat optimality proof"
            };
        }
        if(request.TryGetProperty("capture_card_menu_state",out var cardSourceCapture)&&cardSourceCapture.GetBoolean())
            enriched["card_menu_sources"]=cardMenuSources;
        if(researchProgress!=null)
            enriched["research_progress"]=new {
                schema=ResearchProgressLifecycle.Schema,
                baseline_sha256=researchProgress.BaselineSha,
                checkpoint_restored=researchProgress.CheckpointRestored,
                scope="native Progress save DTO restored from initial baseline or genuine map checkpoint; not complete-host isolation"
            };
        if(captureRouteGraph)enriched["map_decision_sources"]=mapDecisionSources;
        if(mapRoutePlan!=null)enriched["map_route_result"]=MapRouteResult();
        if(CaptureShopMetadata) {
            enriched["shop_inventory_sources"]=shopInventorySources;
            enriched["shop_purchase_events"]=shopPurchaseEvents;
        }
        if(captureResourceTelemetry)enriched["resource_telemetry"]=ResourceTelemetryResult();
        if(captureResourceTelemetry||captureRouteGraph)enriched["gate_entry_sources"]=gateEntrySources;
        if(CapturePreparationMenus)enriched["preparation_menu_sources"]=PreparationMenuSourcesResult();
        return enriched;
    }

    static bool ContainsDecisionException(Exception e) => e is NeedDecision
        || e.InnerException is not null && ContainsDecisionException(e.InnerException);
    internal static void SaveMismatch(string label,JsonNode? expected,JsonNode? actual)
    {
        if(active==null)return;
        string safe=new(label.Select(c=>char.IsLetterOrDigit(c)||c=='_'?c:'_').ToArray());
        File.WriteAllText(Path.Combine(active.output,"mismatch-"+safe+".json"),
            new JsonObject { ["label"]=label,["expected"]=expected?.DeepClone(),["actual"]=actual?.DeepClone() }.ToJsonString());
    }

    void Start(JsonElement request)
    {
        bool needsResearchProgress=captureF1Winners||f1WinnerProposal!=null||cardMenuProbe.HasValue||realCardMenuChoice.HasValue
            ||captureRouteGraph||mapRoutePlan!=null||preserveCompletedPrefix||CapturePreparationMenus
            ||request.TryGetProperty("capture_card_menu_state",out var cardCapture)&&cardCapture.GetBoolean();
        if(needsResearchProgress&&!request.TryGetProperty("research_progress",out _))
            throw new InvalidDataException("RESEARCH_PROGRESS_BASELINE_REQUIRED");
        researchProgress = ResearchProgressLifecycle.Begin(request);
        if (request.TryGetProperty("checkpoint", out var checkpoint) && checkpoint.ValueKind == JsonValueKind.String)
        {
            restoredCheckpoint = RoomCheckpoint.Load(checkpoint.GetString()!, request);
            var savedHistory = restoredCheckpoint["history"]!.AsArray();
            if (history.Length < savedHistory.Count) throw new InvalidDataException("Checkpoint is after requested prefix");
            for (int i=0;i<savedHistory.Count;i++)
                RoomCheckpoint.RequireEqual(savedHistory[i], JsonNode.Parse(history[i].GetRawText()), "history_prefix");
            foreach (var action in savedHistory) transcript.Add(action!.DeepClone());
            foreach (var e in restoredCheckpoint["evidence"]!.AsArray()) decisionEvidence.Add(e!.DeepClone());
            cursor = restoredPrefix = transcript.Count;
            foreach(var action in transcript) traceWriter.WriteLine(action.ToJsonString());
            if(!lowIo)foreach(var e in decisionEvidence) evidenceWriter.WriteLine(JsonSerializer.Serialize(e));
            string? checkpointProgress = researchProgress?.RestoreCheckpoint(restoredCheckpoint);
            var save = RoomCheckpoint.Deserialize(restoredCheckpoint["run"]!);
            run = RunState.FromSerializable(save);
            player = run.Players.Single();
            Wait(RunManager.Instance.SetUpSavedSingleplayer(run, save));
            // Native saved-run setup forces ShouldSave=true. Offline new runs use
            // false. Restore that HOST execution flag too: otherwise end-of-run
            // statistics/Steam/Godot side effects differ despite equal save JSON.
            AccessTools.Property(typeof(RunManager), nameof(RunManager.ShouldSave))
                .SetValue(RunManager.Instance, false);
            RunManager.Instance.Launch();
            Wait(RunManager.Instance.GenerateMap());
            // Native run save omits this lingering identity at out-of-combat
            // boundaries. It still appears in legal targeted potion actions.
            if(!restoredCheckpoint.ContainsKey("player_combat_id"))
                throw new InvalidDataException("CHECKPOINT_MISSING_TRANSIENT_ID");
            player.Creature.CombatId=restoredCheckpoint["player_combat_id"]?.GetValue<uint>();
            if (restoredCheckpoint["action_runtime"] is not JsonObject runtime)
                throw new InvalidDataException("CHECKPOINT_MISSING_ACTION_RUNTIME");
            MapActionRuntime.Restore(runtime);
            RoomCheckpoint.RequireEqual(restoredCheckpoint["run"], RoomCheckpoint.CaptureRun(), "native_run_roundtrip");
            if (checkpointProgress != null)
                ResearchProgressLifecycle.RequireUnchanged(checkpointProgress, "research_progress_after_run_restore");
            InstallRunCallbacks();
            return;
        }
        var character = ModelDb.AllCharacters.Single(c => c.Id.Entry == request.GetProperty("character").GetString());
        var unlock = request.GetProperty("unlocks").GetString() switch
        {
            "all" => UnlockState.all, "none" => UnlockState.none,
            _ => throw new ArgumentException("Unknown unlock configuration")
        };
        player = Player.CreateForNewRun(character, unlock, 1UL);
        run = RunState.CreateForNewRun([player], ActModel.GetDefaultList().Select(a => a.ToMutable()).ToList(), [],
            GameMode.Standard, request.GetProperty("ascension").GetInt32(), request.GetProperty("seed").GetString()!);
        RunManager.Instance.SetUpNewSingleplayer(run, false);
        Wait(RunManager.Instance.FinalizeStartingRelics());
        RunManager.Instance.Launch();
        InstallRunCallbacks();
    }

    void OnCombatWon(CombatRoom room)
    {
        CardMenuProbeCombatWon(room);
        if(captureF1Winners||f1WinnerProposal!=null)observedF1BossWins.Add(room.Encounter.Id.Entry);
        TrackRoomTask(room.Encounter.ShouldGiveRewards
            ?room.OfferRoomEndRewards():RunManager.Instance.ProceedFromTerminalRewardsScreen());
    }

    void InstallRunCallbacks()
    {
        // NCombatUi normally offers rewards after presentation waits. With no scene
        // tree we invoke the same native reward entry point at CombatWon.
        CombatManager.Instance.CombatWon += OnCombatWon;
        RunManager.Instance.TreasureRoomRelicSynchronizer.RelicsAwarded += results =>
        {
            if(run.CurrentRoom is TreasureRoom treasure)completedTreasureRooms.Add(treasure);
            foreach (var result in results)
                if (result.player != null)
                    TrackRoomTask(RelicCmd.Obtain(result.relic.ToMutable(), result.player));
        };
        InstallResourceTelemetry();
    }

    void TrackRoomTask(Task task) { roomTasks.Add(task); background.Add(task); }

    void Wait(Task task)
    {
        var timer = PauseClock.StartNew();
        while (!task.IsCompleted && menu == null && PauseClock.Elapsed(timer) < TimeSpan.FromSeconds(10))
        {
            loop.Pump(TimeSpan.FromMilliseconds(5));
            CheckTasks();
        }
        if (menu != null) throw new NeedDecision();
        if (!task.IsCompleted) throw new TimeoutException("Unobserved pending native choice/action");
        task.GetAwaiter().GetResult();
        CheckTasks();
    }

    void CheckTasks()
    {
        WorkerMemoryTelemetry.ThrowIfOutOfMemoryObserved();
        if (menu != null) throw new NeedDecision();
        foreach (var task in background)
            if (task.IsFaulted) task.GetAwaiter().GetResult();
        background.RemoveAll(t => t.IsCompletedSuccessfully);
    }

    void Settle()
    {
        var deadline = PauseClock.StartNew();
        do
        {
            if (eventDrivenSettle)
            {
                if (!loop.DrainAvailable()) continue;
                Perf.Count("settle_drained_batches");
            }
            else loop.Pump(TimeSpan.FromMilliseconds(5));
            CheckTasks();
            if (!roomTasks.Any(t => !t.IsCompleted)
                && (!CombatManager.Instance.IsInProgress || player.Creature.IsDead
                || (player.PlayerCombatState?.Phase == PlayerTurnPhase.Play
                    && player.Creature.CombatState?.CurrentSide == CombatSide.Player))) return;
            if (eventDrivenSettle)
            {
                Perf.Count("settle_pending_waits");
                loop.Pump(TimeSpan.FromMilliseconds(5));
            }
        } while (PauseClock.Elapsed(deadline) < TimeSpan.FromSeconds(5));
        throw new InvalidOperationException("Native turn did not settle; pending work is unsupported");
    }

    JsonObject Choose(string name, object[] options)
    {
        phase = name;
        CaptureGateEntry(name);
        if (name == "map")
        {
            if (restoredCheckpoint != null && !restoreMenuChecked)
            {
                RoomCheckpoint.RequireEqual(restoredCheckpoint["actions"], JsonSerializer.SerializeToNode(options), "legal_menu");
                RoomCheckpoint.RequireEqual(restoredCheckpoint["observation"], JsonSerializer.SerializeToNode(Observe()), "observation");
                restoreMenuChecked = true;
            }
            if (captureCheckpoints) CaptureCheckpoint(options);
            if (run.TotalFloor >= stopAtFloor && cursor >= history.Length)
            {
                menu = options; boundary = "candidate_horizon"; throw new NeedDecision();
            }
        }
        BeforeCompletedPrefixDecision(name);
        if (menu != null) throw new NeedDecision();
        if (cursor < history.Length)
        {
            var choice = JsonNode.Parse(history[cursor].GetRawText())!;
            var matching = options.Select(o => JsonSerializer.SerializeToNode(o))
                .SingleOrDefault(o => JsonNode.DeepEquals(o, choice));
            if (matching == null) throw new InvalidOperationException($"Illegal replay action #{cursor} at {name}: {choice}");
            cursor++;
            replayedActions++;
            transcript.Add(matching.DeepClone());
            traceWriter.WriteLine(matching.ToJsonString());
            RecordEvidence(name, options);
            return matching.AsObject();
        }
        MapRouteCheckDecision(name,options);
        if((cardMenuProbe.HasValue||realCardMenuChoice.HasValue)&&CardMenuProbeStep(name,options) is {} cardMenuChoice)
            return cardMenuChoice;
        if (probe.HasValue && ProbeStep(name, options) is { } entering) return entering;
        if(stopAtStrategicDecision && (name is "map" or "shop" or "rest" or "card_reward" or "event" or "treasure"
            || name=="select_cards" && !CombatManager.Instance.IsInProgress))
        {
            // Stop only after the exact supplied prefix has executed. Effects,
            // RNG and pending native choices determine this next real menu.
            menu=options;stoppedAtStrategicDecision=true;
            if(options.Length==0)boundary="Nonterminal native menu is empty";
            throw new NeedDecision();
        }
        if (generateCandidate && options.Length > 0)
        {
            if (transcript.Count >= maxDecisions)
            {
                boundary = "candidate_decision_budget";
                throw new NeedDecision();
            }
            phase = name;
            CaptureMapDecisionSource(name,options);
            CaptureShopInventory(name,options);
            JsonObject selected;
            using (Perf.Enter("candidate_select")) {
                var chosen=ShopPreparationChoice(name,options)??RankCandidate(name,options);
                selected=MapRouteChooseMove(name,options,chosen).AsObject();
            }
            generatedActions++;
            transcript.Add(selected.DeepClone());
            traceWriter.WriteLine(selected.ToJsonString());
            RecordEvidence(name, options);
            return selected;
        }
        phase = name;
        menu = options;
        if (options.Length == 0) boundary = "Nonterminal native menu is empty";
        throw new NeedDecision();
    }

    void CaptureCheckpoint(object[] options)
    {
        if (CombatManager.Instance.IsInProgress || roomTasks.Any(t=>!t.IsCompleted)) return;
        if (background.Any(t => !t.IsCompleted)) { Perf.Count("checkpoint_pending_background_skipped"); return; }
        if (!options.Any(o=>JsonSerializer.SerializeToNode(o)?["kind"]?.GetValue<string>() == "map")) return;
        var actionRuntime = MapActionRuntime.Capture();
        if (actionRuntime == null) { Perf.Count("checkpoint_pending_action_skipped"); return; }
        using (Perf.Enter("checkpoint_capture"))
        {
            var payload = new JsonObject {
                ["context"]=RoomCheckpoint.Context(request),
                ["identity"]=JsonSerializer.SerializeToNode(Program.NativeIdentity()),
                ["history"]=JsonSerializer.SerializeToNode(transcript),
                ["evidence"]=JsonSerializer.SerializeToNode(decisionEvidence),
                ["run"]=RoomCheckpoint.CaptureRun(),
                ["player_combat_id"]=player.Creature.CombatId,
                ["action_runtime"]=actionRuntime,
                ["actions"]=JsonSerializer.SerializeToNode(options),
                ["observation"]=JsonSerializer.SerializeToNode(Observe()),
                ["scope"]="offline TestMode map boundary; no normal-Godot parity certification"
            };
            if (researchProgress != null)
                payload["native_progress_snapshot"] = researchProgress.CaptureCheckpoint();
            string path = Path.Combine(output,"checkpoints",$"map-{transcript.Count:D6}.json");
            if(lowIo)path+=".gz";
            // Successful request completion publishes decision.json.gz once.
            // Its full evidence prefix is shared by every map checkpoint.
            RoomCheckpoint.WriteAtomic(path,payload,referenceEvidence:lowIo);
            checkpointRefs.Add(new {path, prefix_length=transcript.Count, floor=run.TotalFloor,
                act=run.CurrentActIndex, bytes=new FileInfo(path).Length});
        }
    }

    void RecordEvidence(string name, object[] options)
    {
        using var timing = Perf.Enter("evidence_write");
        Perf.Count("decisions_"+name);
        if(transcript.LastOrDefault()?["kind"] is JsonNode actionKind)
            Perf.Count("actions_"+actionKind.GetValue<string>());
        if(name=="select_cards")Perf.Count("selection_purpose_"+(selectionRequest?.Purpose.ToString()??"Other"));
        // Labels name each non-combat option by native ids so the coordinator can
        // aggregate outcomes across prefixes. Diagnostic/allocation data only.
        object evidence = Labelled(name)
            ? new { phase = name, observation = Observe(), available_actions = options,
                option_labels = options.Select(o => Labels(name, JsonSerializer.SerializeToNode(o)!)).ToArray() }
            : new { phase = name, observation = Observe(), available_actions = options };
        evidence=CardMenuDecorateEvidence(name,evidence);
        if (request.TryGetProperty("expected_evidence",out var expected) && transcript.Count <= expected.GetArrayLength())
            RoomCheckpoint.RequireEqual(JsonNode.Parse(expected[transcript.Count-1].GetRawText()),
                JsonSerializer.SerializeToNode(evidence), "replay_trajectory_"+(transcript.Count-1));
        decisionEvidence.Add(evidence);
        CapturePreparationMenuEvidence(name,options,evidence);
        if(!lowIo)evidenceWriter.WriteLine(JsonSerializer.Serialize(evidence));
        // Flush once per decision: diagnostics survive a native crash without reopening files.
        if(!lowIo || transcript.Count%64==0){traceWriter.Flush(); evidenceWriter.Flush();}
    }

    // Candidate ordering only. These scores never provide bounds or prune proofs.
    JsonNode RankCandidate(string name, object[] options)
    {
        if(f1WinnerProposal!=null&&!f1ProposalEntryChecked&&name!="combat")
            throw new InvalidDataException("F1_WINNER_EXPECTED_FIRST_COMBAT_DECISION");
        if (name == "combat" && advisor != null)
        {
            JsonNode? suggestion;
            var state=CombatManager.Instance.DebugOnlyGetState()!;
            if(f1WinnerProposal!=null&&state.Encounter?.Id==run.Acts.Last().SecondBossEncounter?.Id)
                f1ProposalSecondBossEntered=true;
            bool capturingRoot=advisor.WillCaptureF1Root(state);
            JsonNode? capturedObservation=capturingRoot?JsonSerializer.SerializeToNode(Observe(),Program.Json):null;
            JsonNode? capturedProgress=capturingRoot?NativeProgressGuard.Capture():null;
            string? capturedProgressRaw=capturingRoot?NativeProgressGuard.RawCapture():null;
            if(f1WinnerProposal!=null&&!f1ProposalEntryChecked)
            {
                f1ProposalGuardAttempted=true;f1ProposalFailedGuard="f1_winner_schema";
                if(f1WinnerProposal["schema"]?.GetValue<string>()!="spire-f1-winner/v1")
                    throw new InvalidDataException("F1_WINNER_SCHEMA");
                CheckF1ProposalGuard(f1WinnerProposal["context"],RoomCheckpoint.Context(request),"f1_winner_context");
                CheckF1ProposalGuard(f1WinnerProposal["entry_history"],JsonSerializer.SerializeToNode(transcript,Program.Json),"f1_winner_history");
                CheckF1ProposalGuard(f1WinnerProposal["native_identity"],JsonSerializer.SerializeToNode(Program.NativeIdentity(),Program.Json),"f1_winner_native_identity");
                CheckF1ProposalGuard(f1WinnerProposal["advisor_binary_identity"],
                    JsonNode.Parse(request.GetProperty("advisor").GetProperty("binary_identity").GetRawText()),"f1_winner_advisor_identity");
                CheckF1ProposalGuard(f1WinnerProposal["entry_observation"],JsonSerializer.SerializeToNode(Observe(),Program.Json),"f1_winner_observation");
                f1ProposalFailedGuard="f1_winner_native_progress";
                if(f1WinnerProposal["native_progress"]==null)
                    throw new InvalidDataException("F1_WINNER_NATIVE_PROGRESS_REQUIRED");
                CheckF1ProposalGuard(f1WinnerProposal["native_progress"],
                    NativeProgressGuard.Capture(),"f1_winner_native_progress");
                f1ProposalEntryChecked=true;
            }
            using (Perf.Enter("combat_advisor")) suggestion = advisor.Suggest(state, player,
                options.Select(o => JsonSerializer.SerializeToNode(o)!).ToArray());
            if(captureF1Winners)
                foreach(var winner in advisor.TakeF1Winners())
                {
                    winner["entry_history"]=JsonSerializer.SerializeToNode(transcript,Program.Json);
                    winner["context"]=RoomCheckpoint.Context(request);
                    winner["native_identity"]=JsonSerializer.SerializeToNode(Program.NativeIdentity(),Program.Json);
                    winner["advisor_binary_identity"]=JsonNode.Parse(request.GetProperty("advisor").GetProperty("binary_identity").GetRawText());
                    winner["entry_observation"]=capturedObservation?.DeepClone()
                        ??throw new InvalidDataException("F1_WINNER_ENTRY_CAPTURE_MISSING");
                    winner["native_progress"]=capturedProgress?.DeepClone()
                        ??throw new InvalidDataException("F1_WINNER_PROGRESS_CAPTURE_MISSING");
                    winner["native_progress_raw"]=capturedProgressRaw;
                    f1WinnerCandidates.Add(winner);
                }
            if (suggestion != null) return suggestion;
        }
        if(name=="select_cards" && CombatManager.Instance.IsInProgress && advisor!=null)
        {
            var suggestion=advisor.SuggestSelection(selectionCards,
                options.Select(o=>JsonSerializer.SerializeToNode(o)!).ToArray(),selectionRequest?.Purpose??CardSelectionPurpose.Other);
            if(suggestion!=null)return suggestion;
        }
        var strategic=new StrategicStateEvaluator(player,run);
        double Score(JsonNode a)
        {
            string kind = a["kind"]!.GetValue<string>();
            double noise = policyRandom.NextDouble() * 2;
            if (kind == "play")
            {
                var card = player.PlayerCombatState!.Hand.Cards[a["index"]!.GetValue<int>()];
                double damage = card.DynamicVars.TryGetValue("Damage", out var d) ? (double)d.BaseValue : 0;
                double block = card.DynamicVars.TryGetValue("Block", out var b) ? (double)b.BaseValue : 0;
                double incoming = player.Creature.CombatState!.Enemies.Where(e => e.IsAlive)
                    .Sum(e => e.Monster!.NextMove.Intents.OfType<MegaCrit.Sts2.Core.MonsterMoves.Intents.AttackIntent>()
                        .Sum(intent => intent.GetTotalDamage([player.Creature], e)));
                double blockNeed = Math.Max(0, incoming - (double)player.Creature.Block);
                double score = 10 + damage + Math.Min(blockNeed, block) * 1.6 + noise;
                if (card.Type.ToString() == "Power") score += 9;
                if (a["target"] != null)
                {
                    var enemy = player.Creature.CombatState.GetCreature(a["target"]!.GetValue<uint>());
                    if (enemy?.Side == CombatSide.Enemy)
                        score += damage >= (double)(enemy.CurrentHp + enemy.Block) ? 30 : -0.02 * (double)enemy.CurrentHp;
                }
                return score;
            }
            if (kind == "end_turn") return -1000;
            if (kind == "sphere_cell" && sphere != null)
            {
                int x = a["x"]!.GetValue<int>(), y = a["y"]!.GetValue<int>();
                int radius = a["tool"]!.GetValue<string>() == "Big" ? 1 : 0;
                double total = 0;
                foreach (var cell in sphere.cells)
                    if (cell.IsHidden && Math.Abs(cell.X - x) <= radius && Math.Abs(cell.Y - y) <= radius)
                    {
                        string? item = cell.Item?.GetType().Name;
                        total += item == "CrystalSphereCurse" ? -40 : item == "CrystalSphereRelic" ? 8 : item != null ? 3 : 0.1;
                    }
                return total + noise * 0.01;
            }
            if (kind == "discard_potion") return -2000;
            if (kind == "use_potion") return player.Creature.CurrentHp < player.Creature.MaxHp * 0.55m
                || run.CurrentRoom?.RoomType is RoomType.Elite or RoomType.Boss ? 50 + noise : -900;
            if (kind == "rewards_skip") return -100;
            if (kind == "card_skip") return 0;
            if (kind == "reward") {
                int index=a["index"]!.GetValue<int>();
                if(currentRewards!=null && declinedRewards.Contains(currentRewards.Rewards[index]))return -200;
                return a["reward"]!.GetValue<string>() == "PotionReward"
                    && player.PotionSlots.All(p => p != null) ? -200 : 100-index;
            }
            if(kind=="card_alternative") {
                var alternative=currentCardAlternatives[a["index"]!.GetValue<int>()];
                if(alternative.OptionId.Equals("Skip",StringComparison.OrdinalIgnoreCase))return 0;
                if(usedAlternatives.Contains(alternative))return -1;
                return alternative.AfterSelected.ToString()=="EndSelectionAndCompleteReward"?2:.5;
            }
            if (kind == "map")
            {
                if(name=="shop")return 0;
                var point = run.Map.GetPoint(new MapCoord(a["col"]!.GetValue<int>(), a["row"]!.GetValue<int>()));
                double routeRisk = RouteRisk(point ?? throw new InvalidOperationException("Map point disappeared"), new Dictionary<MapPoint, double>());
                return -routeRisk * 3 + (point.PointType.ToString() switch
                {
                    "RestSite" => 20 + 30 * (1 - (double)(player.Creature.CurrentHp / player.Creature.MaxHp)) + noise,
                    "Elite" => -10 + noise, "Shop" => player.Gold >= 100 ? 15 + noise : -5 + noise,
                    "Treasure" => 50 + noise, "Unknown" => 9 + noise, _ => 5 + noise
                });
            }
            if (kind == "rest") return a["option"]!.GetValue<string>().Contains("HEAL", StringComparison.OrdinalIgnoreCase)
                ? (player.Creature.CurrentHp < player.Creature.MaxHp *
                    (run.CurrentMapPoint!.Children.Any(c => c.PointType == MapPointType.Boss) ? 0.95m : 0.75m) ? 100 : -5) : 20;
            if (kind == "select_cards")
            {
                var purpose=selectionRequest?.Purpose??CardSelectionPurpose.Other;
                return strategic.SelectionSetValue(a["indices"]!.AsArray()
                    .Select(index=>selectionCards[index!.GetValue<int>()]),purpose);
            }
            if (kind == "buy" && run.CurrentRoom is MerchantRoom merchant)
                return strategic.ShopValue(merchant.GetLocalInventory().AllEntries.ElementAt(a["index"]!.GetValue<int>()));
            if (kind == "card_reward")
            {
                var card = rewardCards[a["index"]!.GetValue<int>()];
                return strategic.MarginalCard(card)+noise*.03;
            }
            if (kind == "event") {
                var model=RunManager.Instance.EventSynchronizer.GetLocalEvent();
                return strategic.EventValue(model.CurrentOptions[a["index"]!.GetValue<int>()],model);
            }
            if (kind == "treasure_skip") return -100;
            return noise;
        }
        var nodes = options.Select(o => JsonSerializer.SerializeToNode(o)!).ToArray();
        JsonNode chosen;
        if (prior.Count == 0 || !Labelled(name)) chosen = nodes.MaxBy(Score)!;
        else
        {
            // Lexicographic (tier, native score); first maximum wins exactly like
            // MaxBy, and Score is still called once per option in menu order.
            chosen = nodes[0]; (int tier, double score) best = (int.MinValue, double.NegativeInfinity);
            foreach (var node in nodes)
            {
                double score = Score(node);
                int tier = EffectiveTier(name, node, score);
                if (tier > best.tier || tier == best.tier && score > best.score) { chosen = node; best = (tier, score); }
            }
            Perf.Count("prior_decisions");
            if (best.tier != 0) Perf.Count(best.tier > 0 ? "prior_preferred_choices" : "prior_avoided_choices");
        }
        if(name=="combat" || name=="select_cards" && CombatManager.Instance.IsInProgress)
            advisor?.RecordFallback(chosen["kind"]!.GetValue<string>());
        return chosen;
    }

    void CheckF1ProposalGuard(JsonNode? expected,JsonNode? actual,string label)
    {
        f1ProposalFailedGuard=label;
        RoomCheckpoint.RequireEqual(expected,actual,label);
        f1ProposalFailedGuard=null;
    }

    // Only quiescent non-combat menus carry labels. Combat stays with the advisor.
    bool Labelled(string name) => name is "map" or "shop" or "rest" or "card_reward" or "event" or "treasure" or "rewards"
        || name == "select_cards" && !CombatManager.Instance.IsInProgress;

    // Options the native policy rejects outright (declined rewards, full potion
    // slots, discards, lethal events) keep that rejection; an explicit skip is
    // always eligible so an avoided reward can be left behind.
    int EffectiveTier(string name, JsonNode a, double score)
    {
        string kind = a["kind"]!.GetValue<string>();
        bool skip = kind is "rewards_skip" or "treasure_skip" or "card_skip";
        if (!skip && score <= -100) return -1000;
        int tier = 0, act = run.CurrentActIndex;
        foreach (string label in Labels(name, a))
            if (prior.TryGetValue(label, out var weights)) tier += weights[Math.Min(act, weights.Length - 1)];
        // Paired F2 add-card evidence values a free card reward, not a purchase
        // with a gold cost. Its separate namespace cannot change shop, route,
        // removal, upgrade, or in-combat choices sharing a card ID.
        if (name == "card_reward")
            foreach (string label in Labels(name, a))
                if (prior.TryGetValue("paired:" + label, out var weights)) tier += weights[Math.Min(act, weights.Length - 1)];
        return tier;
    }

    string[] Labels(string name, JsonNode a)
    {
        string kind = a["kind"]!.GetValue<string>();
        switch (kind)
        {
            case "card_reward": return ["card:" + a["card"]!.GetValue<string>()];
            case "card_skip": return ["card_skip"];
            case "card_alternative":
            {
                int index = a["index"]!.GetValue<int>();
                // Only the native Skip is named; repeatable alternatives stay unlabelled.
                return index < currentCardAlternatives.Count
                    && currentCardAlternatives[index].OptionId.Equals("Skip", StringComparison.OrdinalIgnoreCase) ? ["card_skip"] : [];
            }
            case "event": return ["event:" + a["key"]!.GetValue<string>()];
            case "rest": return ["rest:" + a["option"]!.GetValue<string>()];
            case "treasure": return ["relic:" + a["relic"]!.GetValue<string>()];
            case "treasure_skip": return ["treasure_skip"];
            case "rewards_skip": return ["rewards_skip"];
            case "use_potion": return ["use_potion:" + a["potion"]!.GetValue<string>()];
            case "reward":
            {
                int index = a["index"]!.GetValue<int>();
                if (currentRewards == null || index >= currentRewards.Rewards.Count) return [];
                return currentRewards.Rewards[index] switch
                {
                    RelicReward { Relic: { } relic } => ["relic:" + relic.Id.Entry],
                    PotionReward { Potion: { } potion } => ["potion:" + potion.Id.Entry],
                    _ => []
                };
            }
            case "map":
            {
                // Leaving a shop competes with purchases; do not let a route label end shopping.
                if (name == "shop") return [];
                var point = run.Map.GetPoint(new MapCoord(a["col"]!.GetValue<int>(), a["row"]!.GetValue<int>()));
                return point == null ? [] : [$"go:{point.PointType}@a{run.CurrentActIndex}"];
            }
            case "buy":
            {
                if (run.CurrentRoom is not MerchantRoom merchant) return [];
                return merchant.GetLocalInventory().AllEntries.ElementAt(a["index"]!.GetValue<int>()) switch
                {
                    MerchantCardEntry { CreationResult: { } card } => ["card:" + card.Card.Id.Entry],
                    MerchantRelicEntry { Model: { } relic } => ["relic:" + relic.Id.Entry],
                    MerchantPotionEntry { Model: { } potion } => ["potion:" + potion.Id.Entry],
                    MerchantCardRemovalEntry => ["buy_removal"],
                    _ => []
                };
            }
            case "select_cards":
            {
                if (CombatManager.Instance.IsInProgress) return [];
                string purpose = (selectionRequest?.Purpose ?? CardSelectionPurpose.Other).ToString().ToLowerInvariant();
                return a["indices"]!.AsArray().Select(i => i!.GetValue<int>())
                    .Where(i => i >= 0 && i < selectionCards.Length)
                    .Select(i => purpose + ":" + selectionCards[i].Id.Entry).ToArray();
            }
            default: return [];
        }
    }

    // A route preference, not a bound: room effects can change every estimate.
    static double RouteRisk(MapPoint p, Dictionary<MapPoint, double> memo)
    {
        if (memo.TryGetValue(p, out var value)) return value;
        double cost = p.PointType.ToString() switch { "Elite" => 8, "Monster" => 2, "RestSite" => -4, "Treasure" => -2, _ => 0 };
        return memo[p] = cost + (p.Children.Count == 0 ? 0 : p.Children.Min(child => RouteRisk(child, memo)));
    }

    void Add(object action, Func<Task> execute)
    {
        string key = JsonSerializer.SerializeToNode(action)!.ToJsonString();
        executions.Add(key, execute);
    }

    void BuildMenu()
    {
        CombatState? combat = CombatManager.Instance.DebugOnlyGetState();
        if (CombatManager.Instance.IsInProgress && combat != null)
        {
            phase = "combat";
            var pcs = player.PlayerCombatState ?? throw new InvalidOperationException("No player combat state");
            if (pcs.Phase != PlayerTurnPhase.Play || combat.CurrentSide != CombatSide.Player)
                throw new InvalidOperationException("Native combat has not reached a decision boundary");
            var hand = pcs.Hand.Cards.ToArray();
            for (int i = 0; i < hand.Length; i++)
            {
                var card = hand[i];
                foreach (var target in combat.Creatures.Cast<Creature?>().Prepend(null))
                    if (card.CanPlayTargeting(target))
                        Add(new { kind = "play", index = i, card = card.Id.Entry, target = target?.CombatId },
                            () => Enqueue(new PlayCardAction(card, target)));
            }
            Add(new { kind = "end_turn" }, async () =>
            {
                await Enqueue(new EndPlayerTurnAction(player, pcs.TurnNumber));
            });
        }
        else if (run.CurrentRoom is EventRoom evt && !evt.LocalMutableEvent.IsFinished)
        {
            phase = "event";
            for (int i = 0; i < evt.LocalMutableEvent.CurrentOptions.Count; i++)
            {
                int at = i;
                var option = evt.LocalMutableEvent.CurrentOptions[i];
                if (!option.IsLocked)
                    Add(new { kind = "event", index = i, key = option.TextKey }, () =>
                    {
                        RunManager.Instance.EventSynchronizer.ChooseLocalOption(at);
                        return RunManager.Instance.EventSynchronizer.AwaitPendingOptionTasks();
                    });
            }
        }
        else if (run.CurrentRoom is RestSiteRoom rest && rest.Options.Count > 0)
        {
            phase = "rest";
            for (int i = 0; i < rest.Options.Count; i++)
            {
                int at = i;
                if (rest.Options[i].IsEnabled)
                    Add(new { kind = "rest", index = i, option = rest.Options[i].OptionId },
                        async () => { await RunManager.Instance.RestSiteSynchronizer.ChooseLocalOption(at); });
            }
        }
        else if (run.CurrentRoom is TreasureRoom unopened && !openedChests.Contains(unopened))
        {
            phase = "treasure";
            Add(new { kind = "open_chest" }, async () =>
            {
                openedChests.Add(unopened);
                await unopened.DoNormalRewards();
                await unopened.DoExtraRewardsIfNeeded();
                // Original NTreasureRoomRelicCollection.InitializeRelics/AnimIn
                // completes an empty native collection after its visual delay.
                // Keep that original gameplay callback; award nothing ourselves.
                var sync=RunManager.Instance.TreasureRoomRelicSynchronizer;
                if(sync.CurrentRelics is null || sync.CurrentRelics.Count==0) {
                    sync.CompleteWithNoRelics();
                    Perf.Count("native_empty_treasure_completed");
                }
            });
        }
        else if (run.CurrentRoom is TreasureRoom treasure
                 && !completedTreasureRooms.Contains(treasure)
                 && !RunManager.Instance.TreasureRoomRelicSynchronizer.GetPlayerVote(player).voteReceived)
        {
            phase = "treasure";
            var sync = RunManager.Instance.TreasureRoomRelicSynchronizer;
            var relics = sync.CurrentRelics ?? throw new InvalidOperationException("No treasure relics");
            for (int i = 0; i < relics.Count; i++)
            {
                int at = i;
                Add(new { kind = "treasure", index = i, relic = relics[i].Id.Entry }, async () =>
                {
                    await Enqueue(new PickRelicAction(player, at));
                });
            }
            Add(new { kind = "treasure_skip" }, () => { sync.SkipRelicLocally(); return Task.CompletedTask; });
        }
        else
        {
            phase = "map";
            if (run.CurrentRoom is MerchantRoom shop)
            {
                phase = "shop";
                var inventory = shop.GetLocalInventory();
                var entries = inventory.AllEntries.ToArray();
                for (int i = 0; i < entries.Length; i++)
                {
                    var entry = entries[i];
                    if (entry.IsStocked && entry.EnoughGold)
                    {
                        int entryIndex=i;
                        if(CaptureShopMetadata)
                            Add(new { kind = "buy", index = i, item_type = entry.GetType().Name, cost = entry.Cost },
                                () => PurchaseWithMetadata(entry,entryIndex,inventory));
                        else
                            Add(new { kind = "buy", index = i, item_type = entry.GetType().Name, cost = entry.Cost },
                                async () => { await entry.OnTryPurchaseWrapper(inventory); });
                    }
                }
            }
            IReadOnlyCollection<MapPoint> children = run.CurrentMapPoint is { } current
                ? current.Children : [run.Map.StartingMapPoint];
            foreach (var point in children.OrderBy(p => p.coord.row).ThenBy(p => p.coord.col))
            {
                var coord = point.coord;
                Add(new { kind = "map", col = coord.col, row = coord.row }, () => RunManager.Instance.EnterMapCoord(coord));
            }
            if (children.Count == 0 && run.CurrentRoom is CombatRoom { IsPreFinished: true, RoomType: RoomType.Boss })
                Add(new { kind = "next_act" }, () => RunManager.Instance.EnterNextAct());
            else if (children.Count == 0 && run.CurrentRoom?.IsVictoryRoom == true)
                Add(new { kind = "finish_run" }, () => RunManager.Instance.WinRun());
        }
        // Potion discard is a player decision, including outside combat.
        for (int i = 0; i < player.PotionSlots.Count; i++)
        {
            var potion = player.PotionSlots[i];
            if (potion == null || !player.CanUseOrRemovePotions || potion.IsQueued) continue;
            Add(new { kind = "discard_potion", slot = i, potion = potion.Id.Entry }, () => PotionCmd.Discard(potion));
            bool inCombat = CombatManager.Instance.IsInProgress;
            bool usable = potion.PassesCustomUsabilityCheck && (potion.Usage == PotionUsage.AnyTime
                || potion.Usage == PotionUsage.CombatOnly && inCombat && !CombatManager.Instance.PlayerActionsDisabled);
            if (!usable) continue;
            IEnumerable<Creature?> targets = inCombat ? combat!.Creatures.Cast<Creature?>().Prepend(null) : [null, player.Creature];
            foreach (var target in targets)
                if (potion.IsValidTarget(target))
                    Add(new { kind = "use_potion", slot = i, potion = potion.Id.Entry, target = target?.CombatId },
                        () => Enqueue(new UsePotionAction(potion, target, inCombat)));
        }
    }

    async Task Enqueue(GameAction action)
    {
        RunManager.Instance.ActionQueueSynchronizer.RequestEnqueue(action);
        await action.CompletionTask;
        if (action.Exception != null) throw action.Exception;
    }

    async Task SelectRewards(RewardsSet rewards)
    {
        var sync = RunManager.Instance.RewardsSetSynchronizer;
        currentRewards=rewards;
        while (!sync.IsRewardsSetCompleted(rewards))
        {
            currentRewards=rewards;
            var options = rewards.Rewards.Select((r, i) => (r, i)).Where(x => !x.r.SuccessfullySelected)
                .Select(x => (object)new { kind = "reward", index = x.i, reward = x.r.GetType().Name }).ToList();
            if (!rewards.DisallowSkipping) options.Add(new { kind = "rewards_skip" });
            var choice = Choose("rewards", options.ToArray());
            if (choice["kind"]!.GetValue<string>() == "rewards_skip") sync.SkipLocalRewardsSet();
            else {
                var reward=rewards.Rewards[choice["index"]!.GetValue<int>()];
                await sync.SelectLocalReward(reward);
                // Native Skip returns to the reward screen WITHOUT marking the
                // card reward complete. Keep it legal to reopen for explicit
                // search/replay, but don't greedily reopen a declined reward.
                if(!reward.SuccessfullySelected)declinedRewards.Add(reward);
            }
        }
        currentRewards=null;
    }

    public Task<IEnumerable<CardModel>> GetSelectedCards(IEnumerable<CardModel> options, int minSelect, int maxSelect)
    {
        var cards = options.ToArray();
        selectionCards = cards;
        selectionRequest=CardSelectionContext.Current;
        var selections = new List<object>();
        void Permute(List<int> selected, HashSet<int> used)
        {
            if (selections.Count >= choiceLimit) { menuComplete = false; return; }
            if (selected.Count >= minSelect) selections.Add(new { kind = "select_cards", indices = selected.ToArray() });
            if (selected.Count >= Math.Min(maxSelect, cards.Length)) return;
            for (int i = 0; i < cards.Length; i++)
                if (used.Add(i)) { selected.Add(i); Permute(selected, used); selected.RemoveAt(selected.Count - 1); used.Remove(i); }
        }
        Permute([], []);
        var choice = Choose("select_cards", selections.ToArray());
        return Task.FromResult(choice["indices"]!.AsArray().Select(i => cards[i!.GetValue<int>()]));
    }

    public CardRewardSelection GetSelectedCardReward(IReadOnlyList<CardCreationResult> options, IReadOnlyList<CardRewardAlternative> alternatives)
    {
        currentCardAlternatives=alternatives;
        rewardCards=options.Select(c=>c.Card).ToArray();
        var actions = options.Select((c, i) => (object)new { kind = "card_reward", index = i, card = c.Card.Id.Entry }).ToList();
        actions.AddRange(alternatives.Select((a, i) => (object)new { kind = "card_alternative", index = i, alternative = a.GetType().Name }));
        if(alternatives.Any(a=>a.OptionId.Equals("Skip",StringComparison.OrdinalIgnoreCase)))actions.Add(new { kind = "card_skip" });
        var choice = Choose("card_reward", actions.ToArray());
        if(choice["kind"]!.GetValue<string>()=="card_alternative") {
            var selected=alternatives[choice["index"]!.GetValue<int>()];usedAlternatives.Add(selected);
            if(selected.OptionId.Equals("Skip",StringComparison.OrdinalIgnoreCase))Perf.Count("skipped_rewards");
        }
        else if(choice["kind"]!.GetValue<string>()=="card_skip")Perf.Count("skipped_rewards");
        return choice["kind"]!.GetValue<string>() switch
        {
            "card_reward" => new CardRewardSelection { card = options[choice["index"]!.GetValue<int>()].Card },
            "card_alternative" => new CardRewardSelection { alternative = alternatives[choice["index"]!.GetValue<int>()] },
            _ => new CardRewardSelection()
        };
    }

    // Observe() deliberately hides inactive combat data; keep a separate native
    // loss snapshot before teardown for M0 diagnostics/ranking, not for bounds.
    object? TerminalCombat()
    {
        if (player?.Creature.IsDead != true || run?.CurrentRoom is not CombatRoom room) return null;
        var state = room.CombatState;
        return new {
            act=run.CurrentActIndex, floor=run.TotalFloor,
            turn=player.PlayerCombatState?.TurnNumber ?? state.RoundNumber,
            enemies=state.Enemies.Select(e => new {
                id=e.Monster?.Id.Entry, combat_id=e.CombatId,
                hp=e.CurrentHp.ToString(CultureInfo.InvariantCulture),
                max_hp=e.MaxHp.ToString(CultureInfo.InvariantCulture)
            }).ToArray()
        };
    }

    object? Observe()
    {
        if (run == null) return null;
        var combat = CombatManager.Instance.IsInProgress ? player.PlayerCombatState : null;
        static string Num(decimal x) => x.ToString(CultureInfo.InvariantCulture);
        object Card(CardModel c) => new { id = c.Id.Entry, upgrade = c.CurrentUpgradeLevel };
        return new
        {
            act = run.CurrentActIndex, floor = run.TotalFloor, room = phase == "map" ? null : run.CurrentRoom?.RoomType.ToString(),
            hp = Num(player.Creature.CurrentHp), max_hp = Num(player.Creature.MaxHp), gold = player.Gold,
            deck = player.Deck.Cards.Select(Card).ToArray(), relics = player.Relics.Select(r => r.Id.Entry).ToArray(),
            strategic = new StrategicStateEvaluator(player,run).Snapshot(),
            // cards: the candidates in the order select_cards indices refer to. Record only;
            // pile and choose-a-card screens are otherwise invisible in the observation.
            selection = phase=="select_cards" && selectionRequest!=null ? new {
                purpose=selectionRequest.Purpose.ToString(),native_method=selectionRequest.NativeMethod,
                prompt=selectionRequest.Prompt,source=selectionRequest.Source,
                cards=selectionCards.Select(Card).ToArray() } : null,
            hand = combat?.Hand.Cards.Select(Card).ToArray(), turn = combat?.TurnNumber,
            energy = combat?.Energy, block = combat == null ? "0" : Num(player.Creature.Block),
            enemies = combat == null ? null : player.Creature.CombatState?.Enemies.Select(e => new
            { id = e.Monster?.Id.Entry, combat_id = e.CombatId, hp = Num(e.CurrentHp), block = Num(e.Block) }).ToArray(),
            potions = player.PotionSlots.Select(p=>p?.Id.Entry).ToArray(),
            character = player.Character.Id.Entry,
            rng = JsonSerializer.SerializeToNode(run.Rng.ToSerializable(), Program.Json),
            score = ScoreUtility.CalculateScore(run, false)
        };
    }

    internal static void Detach()
    {
        if (active != null)
        {
            CombatManager.Instance.CombatWon -= active.OnCombatWon;
            active.DetachResourceTelemetry();
        }
        RewardsSet.testSelector = null;
        active = null;
    }
    void InstallGuards()
    {
        if (guardsInstalled) return;
        guardsInstalled = true;
        var harmony = new Harmony("spire-exact.campaign-observer");
        CardSelectionContext.Install(harmony);
        foreach (var method in typeof(TaskHelper).GetMethods(BindingFlags.Public | BindingFlags.Static)
                     .Where(m => m.Name == "RunSafely" && !m.IsGenericMethod && m.GetParameters().FirstOrDefault()?.ParameterType == typeof(Task)))
            harmony.Patch(method, prefix: new HarmonyMethod(typeof(CampaignReplay), nameof(TrackTask)));
        harmony.Patch(AccessTools.Method(typeof(RunManager), "OnEnded"),
            postfix: new HarmonyMethod(typeof(CampaignReplay), nameof(Ended)));
        harmony.Patch(AccessTools.Method(typeof(MegaCrit.Sts2.Core.Nodes.Events.Custom.CrystalSphere.NCrystalSphereScreen), "ShowScreen"),
            prefix: new HarmonyMethod(typeof(CampaignReplay), nameof(SphereScreen)));
    }
    static bool SphereScreen(CrystalSphereMinigame grid, ref MegaCrit.Sts2.Core.Nodes.Events.Custom.CrystalSphere.NCrystalSphereScreen? __result)
    {
        var session = active ?? throw new InvalidOperationException("No campaign selection driver");
        session.TrackRoomTask(session.PlaySphere(grid));
        __result = null;
        return false;
    }
    async Task PlaySphere(CrystalSphereMinigame grid)
    {
        sphere = grid;
        while (grid.DivinationCount > 0)
        {
            var options = new List<object>();
            foreach (var cell in grid.cells)
                if (cell.IsHidden)
                    foreach (string tool in new[] { "Small", "Big" })
                        options.Add(new { kind = "sphere_cell", x = cell.X, y = cell.Y, tool });
            var action = Choose("crystal_sphere", options.ToArray());
            grid.SetTool(Enum.Parse<CrystalSphereMinigame.CrystalSphereToolType>(action["tool"]!.GetValue<string>()));
            await grid.CellClicked(grid.cells[action["x"]!.GetValue<int>(), action["y"]!.GetValue<int>()]);
        }
        sphere = null;
    }
    static void TrackTask(Task __0) => active?.background.Add(__0);
    static void Ended(bool isVictory) { if (active != null) active.victory = isVictory; }
}
