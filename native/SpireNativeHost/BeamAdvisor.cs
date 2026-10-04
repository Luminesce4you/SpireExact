using System.Reflection;
using System.Runtime.Loader;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;

namespace SpireNativeHost;

// Candidate proposals only. Continuation reuse requires upstream FULL StateText equality.
// Neither beam failure nor this cache certifies infeasibility or game equivalence.
internal sealed class BeamAdvisor
{
    // One upstream search configuration. A gate plan runs several members from
    // the SAME live root and deploys the best forecast; every member is still a
    // heuristic beam search, and losing every member proves nothing.
    sealed record Member(string Mode,int? Beam,int? Nodes,bool Portfolio,int[]? Widths,string? Profile)
    {
        public object Describe()=>new {mode=Mode,beam=Beam,nodes=Nodes,portfolio=Portfolio,widths=Widths,profile=Profile};
    }
    // EscalateBelow: after the first member forecasts a loss, later members run
    // only when it left at most this fraction of the live enemy HP (or already
    // removed an enemy life). null always escalates. Pure allocation.
    sealed record GatePlan(Member[] Members,string Select,double? EscalateBelow);
    // Forecast ordering among members of one plan. Lexicographic only: victory,
    // survival, projected HP, potions, then observed progress of a lost fight.
    internal readonly record struct Forecast(bool Won,bool Survives,int Hp,int Potions,int? DeathTurn,int? EndTurn,int EnemyHp,int EnemyDeaths)
    {
        (int,int,int,int,int,int,int) Key()=>(Won?1:0,Survives?1:0,Won?Hp:0,Won?-Potions:0,
            Won?0:EnemyDeaths,Won?0:DeathTurn??int.MaxValue,-EnemyHp);
        public bool BetterThan(Forecast other)=>Key().CompareTo(other.Key())>0;
        public object Describe()=>new {won=Won,survives=Survives,hp=Hp,potions=Potions,death_turn=DeathTurn,end_turn=EndTurn,
            enemy_hp=EnemyHp,enemy_deaths=EnemyDeaths};
    }

    readonly object options, loop;
    readonly MethodInfo search, captureLive, findCard;
    readonly MethodInfo applyFixedBudget, beginOfflineSession;
    readonly PropertyInfo sessionProperty;
    readonly Type sessionOptionsType;
    readonly Queue<object> pending = new();
    readonly Queue<object> pendingChoices = new();
    readonly MethodInfo matchesChoiceToken;
    readonly MethodInfo choiceCardKey;
    readonly Type stampType;
    readonly Dictionary<string,long> stats = [];
    object[] route = [], continuations = [];
    CombatState? lastCombat;
    int lastTurn=-1;
    bool needsReplan;
    int turnReplans;
    const int MaxReplansPerTurn = 4;
    public string LastMissReason { get; private set; } = "not_requested";
    readonly int ordinaryBudget, bossBudget, dop;
    readonly bool reuse;
    readonly bool completeContinuationsOnly;
    readonly bool preferF1Hp;
    readonly bool measureSearchPhases;
    readonly bool measureSearchWork;
    readonly bool verifyIncrementalSearch;
    readonly MethodInfo? validateScalarPowerCow;
    bool scalarPowerCowValidated;
    readonly Type? copyWorkProbe;
    readonly Member defaultMember;
    readonly int? normalNodes;
    readonly Dictionary<string,GatePlan> gatePlans = new(StringComparer.Ordinal);
    readonly string baseProfile;
    (bool portfolio,string widths,int budget)? sessionKey;
    bool predictsCompleteCombatVictory;
    static Type? initializedRuntime;
    readonly List<object> searchRecords = [];
    readonly List<object> mappingFailures = [];
    readonly List<object> continuationDifferences = [];
    bool captureF1Winners;
    JsonObject? forcedF1Winner;
    bool forcedF1Loaded;
    string? forcedF1RejectionReason;
    CombatState? forcedF1Combat, capturedF1Combat;
    readonly List<JsonObject> availableF1Winners = [];
    readonly Type? planActionType;
    sealed record RestoredContinuation(string StateText, int StartTurnNumber, int ForecastOffset);
    const BindingFlags All = BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static|BindingFlags.Instance;
    static object? Get(object obj,string key) => obj.GetType().GetProperty(key,All)?.GetValue(obj);
    static int Int(object obj,string key) => Convert.ToInt32(Get(obj,key));
    static void Set(object obj,string key,object? value) => (obj.GetType().GetProperty(key,All)
        ?? throw new InvalidOperationException("ADVISOR_OPTION_CONTRACT_CHANGED:"+key)).SetValue(obj,value);
    void Count(string name) => stats[name]=stats.GetValueOrDefault(name)+1;
    static Assembly Load(string path) => AssemblyLoadContext.Default.Assemblies.FirstOrDefault(a=>
        !a.IsDynamic && string.Equals(a.Location,Path.GetFullPath(path),StringComparison.Ordinal))
        ?? AssemblyLoadContext.Default.LoadFromAssemblyPath(Path.GetFullPath(path));

    static Member ParseMember(JsonElement e,Member fallback)
    {
        if(e.ValueKind!=JsonValueKind.Object)throw new ArgumentException("Advisor gate member must be an object");
        string mode=e.TryGetProperty("mode",out var m)?m.GetString()!:fallback.Mode;
        if(mode is not("Evaluate" or "Coordinator"))throw new ArgumentException("Unknown advisor search mode: "+mode);
        int? beam=e.TryGetProperty("beam",out var b)&&b.ValueKind==JsonValueKind.Number?b.GetInt32():fallback.Beam;
        int? nodes=e.TryGetProperty("nodes",out var n)&&n.ValueKind==JsonValueKind.Number?n.GetInt32():fallback.Nodes;
        if(beam is <1 or >512)throw new ArgumentException("Advisor beam must be within the upstream 1..512 range");
        if(nodes is <1)throw new ArgumentException("Positive advisor node budget required");
        bool portfolio=e.TryGetProperty("portfolio",out var p)?p.GetBoolean():fallback.Portfolio;
        int[]? widths=e.TryGetProperty("widths",out var w)&&w.ValueKind==JsonValueKind.Array
            ?w.EnumerateArray().Select(x=>x.GetInt32()).ToArray():fallback.Widths;
        if(widths!=null&&widths.Any(x=>x<1))throw new ArgumentException("Positive portfolio widths required");
        string? profile=e.TryGetProperty("profile",out var pr)&&pr.ValueKind==JsonValueKind.String?pr.GetString():fallback.Profile;
        return new Member(mode,beam,nodes,portfolio,widths,profile);
    }

    public BeamAdvisor(JsonElement config,string output)
    {
        PauseClock.ValidateSearchModes(config);
        ordinaryBudget = config.TryGetProperty("budget_ms",out var b) ? b.GetInt32():300;
        bossBudget = config.TryGetProperty("boss_budget_ms",out var bb) ? bb.GetInt32():ordinaryBudget*4;
        reuse = !config.TryGetProperty("reuse_continuations",out var rc) || rc.GetBoolean();
        completeContinuationsOnly=config.TryGetProperty("complete_continuations_only",out var complete)&&complete.GetBoolean();
        preferF1Hp=config.TryGetProperty("prefer_f1_hp",out var preferHp)&&preferHp.GetBoolean();
        measureSearchPhases=config.TryGetProperty("measure_search_phases",out var phases)&&phases.GetBoolean();
        measureSearchWork=config.TryGetProperty("measure_search_work",out var work)&&work.GetBoolean();
        verifyIncrementalSearch=config.TryGetProperty("verify_incremental_search",out var verify)&&verify.GetBoolean();
        if(ordinaryBudget<1||bossBudget<1) throw new ArgumentException("Positive advisor budget required");
        var directories=config.GetProperty("dependency_dirs").EnumerateArray().Select(v=>v.GetString()!).ToArray();
        AssemblyLoadContext.Default.Resolving += (context,name)=> {
            foreach(string directory in directories) {
                string file=Path.GetFullPath(Path.Combine(directory,name.Name+".dll"));
                if(File.Exists(file)) return Load(file);
            }
            return null;
        };
        var solver=Load(config.GetProperty("solver").GetString()!);
        planActionType=solver.GetType("CombatSolver.PlanAction",true)!;
        PauseClock.Install(solver);
        if(config.TryGetProperty("validate_scalar_power_cow",out var validate)&&validate.GetBoolean())
            validateScalarPowerCow=solver.GetType("CombatSolver.ScalarPowerCowContract",true)!.GetMethod("Run",All)!;
        if(config.TryGetProperty("measure_fork_writes",out var copy)&&copy.GetBoolean())
        {
            copyWorkProbe=solver.GetType("CombatSolver.CopyWorkProbe",true)!;
            if(config.TryGetProperty("fork_measurement_mode",out var mode))
                copyWorkProbe.GetMethod("SetMode",All)!.Invoke(null,[mode.GetString()!]);
            copyWorkProbe.GetMethod("Configure",All)!.Invoke(null,[true]);
        }
        AdvisorDiagnosticLog.Configure(solver, config.TryGetProperty("quiet_diagnostics", out var quiet) && quiet.GetBoolean());
        AdvisorBlockCompensation.Configure(solver, config.TryGetProperty("fix_consumed_block_compensation", out var blockFix) && blockFix.GetBoolean());
        var controller=solver.GetType("CombatSolver.SolverController",true)!;
        controller.GetProperty("DisplayServerNameProvider",All)!.SetValue(null,(Func<string>)(()=>"headless"));
        stampType=solver.GetType("CombatSolver.ContinuationStamp",true)!;
        captureLive=stampType.GetMethod("CaptureLive",All)!;
        findCard=controller.GetMethod("FindCardForDeployment",All)!;
        var tokenType=solver.GetType("CombatSolver.PlanCardToken",true)!;
        matchesChoiceToken=solver.GetType("CombatSolver.CardChoiceSupport",true)!.GetMethod("MatchesToken",All,
            binder:null,types:[typeof(CardModel),tokenType],modifiers:null)!;
        choiceCardKey=solver.GetType("CombatSolver.CardChoiceSupport",true)!.GetMethod("ChoiceCardKey",All,
            binder:null,types:[typeof(CardModel)],modifiers:null)!;
        var runner=solver.GetType("CombatSolver.UnattendedTestRunner",true)!;
        sessionOptionsType=runner.GetNestedType("OfflineSessionOptions",All)
            ?? throw new InvalidOperationException("ADVISOR_OFFLINE_SESSION_CONTRACT_CHANGED");
        beginOfflineSession=runner.GetMethod("BeginOfflineSession",All,binder:null,types:[sessionOptionsType],modifiers:null)
            ?? throw new InvalidOperationException("ADVISOR_OFFLINE_SESSION_CONTRACT_CHANGED");
        var harness=Load(config.GetProperty("harness").GetString()!);
        PauseClock.Install(harness);
        Directory.CreateDirectory(output);
        baseProfile=config.TryGetProperty("profile",out var pr)?pr.GetString()!:"Low";
        dop=config.TryGetProperty("dop",out var dopValue)?dopValue.GetInt32():1;
        string defaultMode=config.TryGetProperty("search_mode",out var sm)?sm.GetString()!:"Evaluate";
        if(defaultMode is not("Evaluate" or "Coordinator"))throw new ArgumentException("Unknown advisor search mode: "+defaultMode);
        string[] args=["--out",output,"--profile",baseProfile,"--dop",dop.ToString(),
            "--budget-ms",ordinaryBudget.ToString(),"--search-mode",defaultMode];
        options=harness.GetType("OfflineSearchHarness.HarnessOptions",true)!.GetMethod("Parse",All)!.Invoke(null,[args])!;
        Set(options,"MeasureSearchPhases",measureSearchPhases);
        int? configuredNodes=config.TryGetProperty("nodes",out var nodes)?nodes.GetInt32():null;
        int? configuredBeam=null;
        if(configuredNodes!=null) Set(options,"Nodes",configuredNodes);
        if(config.TryGetProperty("beam",out var beam)) {
            if(beam.GetInt32()<1)throw new ArgumentException("Positive advisor beam required");
            configuredBeam=beam.GetInt32();Set(options,"Beam",configuredBeam);
        }
        bool defaultPortfolio=config.TryGetProperty("use_portfolio",out var up)&&up.GetBoolean();
        defaultMember=new Member(defaultMode,configuredBeam,configuredNodes,defaultPortfolio,null,null);
        // Ordinary hallway fights may use a smaller count budget than gates.
        // Explicit request data; never an environment or wall-clock override.
        if(config.TryGetProperty("normal_nodes",out var nn)&&nn.ValueKind==JsonValueKind.Number) {
            if(nn.GetInt32()<1)throw new ArgumentException("Positive advisor node budget required");
            normalNodes=nn.GetInt32();
        }
        if(config.TryGetProperty("gate_plans",out var plans)&&plans.ValueKind==JsonValueKind.Object)
            foreach(var plan in plans.EnumerateObject()) {
                var members=plan.Value.GetProperty("members").EnumerateArray().Select(e=>ParseMember(e,defaultMember)).ToArray();
                if(members.Length==0)throw new ArgumentException("Advisor gate plan needs at least one member");
                string select=plan.Value.TryGetProperty("select",out var s)?s.GetString()!:"auto";
                if(select is not("auto" or "best" or "first_win"))throw new ArgumentException("Unknown gate selection: "+select);
                // Integer percent: request identity is exact JSON without floats.
                int? percent=plan.Value.TryGetProperty("escalate_percent",out var ep)&&ep.ValueKind==JsonValueKind.Number?ep.GetInt32():null;
                if(percent is <0 or >100)throw new ArgumentException("Gate escalation percent must be within 0..100");
                gatePlans[plan.Name]=new GatePlan(members,select,percent/100.0);
            }
        var runtime=harness.GetType("OfflineSearchHarness.ModRuntime",true)!;
        sessionProperty=runtime.GetProperty("Session",All)!;
        applyFixedBudget=runtime.GetMethod("ApplyFixedBudgetSettings",All)!;
        (sessionProperty.GetValue(null) as IDisposable)?.Dispose();
        if(initializedRuntime==null) { runtime.GetMethod("Initialize",All)!.Invoke(null,[options]); initializedRuntime=runtime; }
        else {
            applyFixedBudget.Invoke(null,[options]);
            runtime.GetMethod("ApplyUnattendedOverrides",All)!.Invoke(null,[options]);
        }
        var settings=solver.GetType("CombatSolver.SolverSettings",true)!.GetProperty("Current",All)!.GetValue(null)!;
        if((bool)Get(settings,"OnlineStatisticsEnabled")!)throw new InvalidOperationException("OFFLINE_UPLOADS_NOT_DISABLED");
        Count("offline_statistics_disabled");
        loop=Activator.CreateInstance(harness.GetType("OfflineSearchHarness.MainLoopContext",true)!,true)!;
        search=runtime.GetMethod("RunSearchDetailed",All)!;
    }
    public object Metrics()=>new {counters=stats, searches=searchRecords,mapping_failures=mappingFailures,
        continuation_differences=continuationDifferences, diagnostic_log=AdvisorDiagnosticLog.Metrics(),
        block_compensation=AdvisorBlockCompensation.Metrics(), pause_clock=PauseClock.Metrics()};
    public void Invalidate(string reason) {
        pending.Clear();pendingChoices.Clear();route=[];continuations=[];needsReplan=true;predictsCompleteCombatVictory=false;
        Count("invalidations");Count("invalidated_"+reason);LastMissReason=reason;
    }
    public void RecordFallback(string actionKind) {
        RequireNoForcedF1Fallback("native_fallback:"+actionKind);
        Count("fallback_count");Count("fallback_reason_"+LastMissReason);
        Count("fallback_action_"+actionKind);Count("fallback_source_native_greedy");
    }
    bool StrictF1 => forcedF1Loaded && ReferenceEquals(lastCombat, forcedF1Combat);
    void RequireNoForcedF1Fallback(string reason) {
        if(StrictF1)throw RejectF1Winner("F1_WINNER_DEPLOYMENT_MISMATCH:"+reason);
    }
    InvalidDataException RejectF1Winner(string reason)
    {
        forcedF1RejectionReason=reason;Count("f1_winner_rejections");
        return new InvalidDataException(reason);
    }
    JsonNode? Miss(string reason) {RequireNoForcedF1Fallback(reason);LastMissReason=reason;Count("advisor_miss");Count("miss_"+reason);return null;}

    public void ConfigureF1WinnerReuse(bool capture, JsonObject? proposal)
    {
        captureF1Winners=capture;
        forcedF1Winner=proposal?.DeepClone().AsObject();
    }
    public JsonObject[] TakeF1Winners()
    {
        var result=availableF1Winners.ToArray();availableF1Winners.Clear();return result;
    }
    public bool WillCaptureF1Root(CombatState state)=>captureF1Winners
        &&HpCarriesIntoNextBoss(state)&&!ReferenceEquals(capturedF1Combat,state);
    public object F1ReuseStatus()=>new {requested=forcedF1Winner!=null,loaded=forcedF1Loaded,
        member_index=forcedF1Winner?["member_index"]?.GetValue<int>(),
        rejection_reason=forcedF1RejectionReason,
        captured_f1_root_count=stats.GetValueOrDefault("f1_winner_roots_captured"),
        mapped_f1_actions=stats.GetValueOrDefault("f1_winner_actions_mapped"),
        mapped_f1_selections=stats.GetValueOrDefault("f1_winner_selections_mapped"),
        strict_deployment=true,additional_f1_searches=0};
    public JsonNode? Suggest(CombatState state,Player player,JsonNode[] legal)
    {
        if(pendingChoices.Count>0)Invalidate("unconsumed_choice_plan");
        int turn=player.PlayerCombatState!.TurnNumber;
        if(state!=lastCombat) {
            if(StrictF1&&state.Encounter?.Id==forcedF1Combat?.Encounter?.Id)
                throw RejectF1Winner("F1_WINNER_DEPLOYMENT_MISMATCH:combat_instance_changed");
            Invalidate("new_combat");lastCombat=state;lastTurn=-1;
        }
        if(turn!=lastTurn)
        {
            turnReplans=0;
            pending.Clear();
            bool matched=false;
            bool canReuse=StrictF1 || reuse && (!completeContinuationsOnly || predictsCompleteCombatVictory);
            if(reuse && completeContinuationsOnly && !predictsCompleteCombatVictory && route.Length>0)
                Count("turn_reuse_deferred_incomplete_forecast");
            if(canReuse && route.Length>0)
            {
                using(PhaseProfiler.Current.Enter("continuation_compare"))
                {
                    object live=captureLive.Invoke(null,[state])!;
                    string actual=(string)Get(live,"StateText")!;
                    var expected=continuations.Where(c=>Int(c,"StartTurnNumber")==turn).ToArray();
                    // Equality remains the full upstream state text, including
                    // RNG/piles/identities. Diagnostic truncation below NEVER
                    // participates in continuation reuse or state equality.
                    matched=expected.Any(c=>(string?)Get(c,"StateText")==actual);
                    Count(expected.Length==0?"continuation_not_predicted":matched?
                        "continuation_state_match":"continuation_state_mismatch");
                    if(expected.Length>0 && !matched && continuationDifferences.Count<8) {
                        object predicted=Activator.CreateInstance(stampType,[(string)Get(expected[0],"StateText")!])!;
                        var differences=(IEnumerable<string>)stampType.GetMethod("DescribeDifferences",All)!.Invoke(predicted,[live,4])!;
                        continuationDifferences.Add(new {turn,encounter=state.Encounter!.Id.Entry,
                            expected_count=expected.Length,diagnostic_only=true,
                            differences=differences.Select(s=>new {text=s.Length>512?s[..512]:s,truncated=s.Length>512}).ToArray()});
                    }
                }
                Count(matched?"turn_reuse_matched":"turn_reuse_mismatch");
            }
            if(!matched) {
                RequireNoForcedF1Fallback("turn_continuation_state");
                Search(state,turn);
            }
            else EnqueueTurn(turn);
            lastTurn=turn;
        }
        // A nested choice or failed deployment invalidates the prediction inside
        // this turn. Replan against the LIVE state, without waiting for EndTurn.
        for(int attempt=0;attempt<2;attempt++)
        {
        if(needsReplan || pending.Count==0)
        {
            RequireNoForcedF1Fallback("plan_exhausted_or_replan");
            if(pending.Count==0) Count("plan_exhausted");
            if(turnReplans>=MaxReplansPerTurn) return Miss("replan_limit");
            turnReplans++;Count("replans");Search(state,turn);
        }
        if(!pending.TryDequeue(out var plan)) return Miss("search_returned_no_action");
        string kind=Get(plan,"Kind")!.ToString()!;
        JsonNode? mapped=null;
        if(kind=="EndTurn") mapped=legal.FirstOrDefault(a=>a["kind"]!.GetValue<string>()=="end_turn");
        else if(kind=="UsePotion") {
            int slot=Int(plan,"PotionSlot");
            var potion=slot>=0&&slot<player.PotionSlots.Count?player.PotionSlots[slot]:null;
            if(potion!=null && potion.Id.Entry==(string?)Get(plan,"PotionId")) {
                // The pinned solver uses null for Self/AnyPlayer potions.
                // Native PotionModel.EnqueueManualUse resolves null to Owner
                // when IsValidTarget(Owner.Creature); cards have other semantics.
                object? target=Get(plan,"TargetCombatId");
                if(target==null && potion.IsValidTarget(player.Creature)) {
                    target=player.Creature.CombatId;Count("potion_owner_target_resolved");
                }
                mapped=legal.FirstOrDefault(a=>a["kind"]!.GetValue<string>()=="use_potion"
                    && a["slot"]!.GetValue<int>()==slot
                    && JsonNode.DeepEquals(a["target"],JsonSerializer.SerializeToNode(target)));
            }
        }
        else if(kind=="PlayCard")
        {
            try {
                var card=(CardModel)findCard.Invoke(null,[player.PlayerCombatState.Hand.Cards,plan])!;
                int index=player.PlayerCombatState.Hand.Cards.ToList().IndexOf(card);
                mapped=legal.FirstOrDefault(a=>a["kind"]!.GetValue<string>()=="play"
                    &&a["index"]!.GetValue<int>()==index&&TargetMatches(a,plan));
            } catch(TargetInvocationException) { Count("card_mapping_failed"); }
        }
        if(mapped==null) {
            RequireNoForcedF1Fallback("legal_action:"+kind);
            Count("mapping_failed_"+kind);
            if(mappingFailures.Count<16)mappingFailures.Add(new {kind,turn,
                card=Get(plan,"CardId"),potion=Get(plan,"PotionId"),slot=Get(plan,"PotionSlot"),
                target=Get(plan,"TargetCombatId"),legal=legal.Select(a=>a.DeepClone()).ToArray()});
            Invalidate("legal_action_mismatch");continue;
        }
        var actionChoices=plan.GetType().GetMethod("GetActionChoicesInExecutionOrder",All)!.Invoke(plan,null) as System.Collections.IEnumerable;
        if(actionChoices!=null)foreach(var choice in actionChoices)pendingChoices.Enqueue(choice);
        if(Get(plan,"TurnStartChoices") is System.Collections.IEnumerable turnChoices)
            foreach(var choice in turnChoices)pendingChoices.Enqueue(choice);
        Count("plan_actions_used");Count("advisor_hit");LastMissReason="none";
        if(StrictF1)Count("f1_winner_actions_mapped");
        return mapped;
        }
        return Miss("mapping_retry_exhausted");
    }
    public JsonNode? SuggestSelection(CardModel[] options,JsonNode[] legal,CardSelectionPurpose purpose)
    {
        if(!pendingChoices.TryDequeue(out var choice)) {RequireNoForcedF1Fallback("unplanned_nested_selection");Invalidate("unplanned_nested_selection");Count("selection_miss");return null;}
        string effect=Get(choice,"Effect")!.ToString()!;
        bool purposeMatches=purpose switch {
            CardSelectionPurpose.Exhaust=>effect=="Exhaust",
            CardSelectionPurpose.Discard=>effect is "Discard" or "DiscardAndDraw",
            CardSelectionPurpose.Upgrade=>effect=="Upgrade",
            CardSelectionPurpose.Transform=>effect=="Transform",
            CardSelectionPurpose.Duplicate=>effect is "Duplicate" or "Nightmare",
            _=>true};
        var indices=new List<int>();
        if(purposeMatches && Get(choice,"Cards") is System.Collections.IEnumerable tokens)
        {
            foreach(var token in tokens)
            {
                var matches=options.Select((card,index)=>(card,index))
                    .Where(x=>(bool)matchesChoiceToken.Invoke(null,[x.card,token])!).ToArray();
                int occurrence=Int(token,"OptionOccurrence");
                if(occurrence<0||occurrence>=matches.Length) {purposeMatches=false;break;}
                var selected=matches[occurrence];
                // The pinned MatchesToken only compares ID/upgrade. Also bind
                // its richer choice key and occurrence; this is a proposal
                // deployment check, never a complete-state deduplication key.
                string expected=(string)Get(token,"StateKey")!;
                if(!string.IsNullOrEmpty(expected) && (string)choiceCardKey.Invoke(null,[selected.card])! != expected)
                {purposeMatches=false;Count("selection_state_key_mismatch");break;}
                indices.Add(selected.index);
            }
        }
        var action=JsonSerializer.SerializeToNode(new {kind="select_cards",indices})!;
        var matched=purposeMatches && indices.Distinct().Count()==indices.Count
            ? legal.FirstOrDefault(a=>JsonNode.DeepEquals(a,action)) : null;
        if(matched!=null){Count("selection_hit");Count("advisor_hit");if(StrictF1)Count("f1_winner_selections_mapped");return matched;}
        RequireNoForcedF1Fallback("nested_selection_identity_mismatch");
        Invalidate("nested_selection_identity_mismatch");Count("selection_miss");return null;
    }
    static bool TargetMatches(JsonNode action,object plan) => JsonNode.DeepEquals(action["target"],JsonSerializer.SerializeToNode(Get(plan,"TargetCombatId")));
    object? WorkMetrics(object result)
    {
        if(!measureSearchWork)return null;
        // Read existing completed-result counters only. No additional search or replay.
        string[] names={"ExpandedNodes","TotalExpandedNodes","ForkCount","ReplayCount","TransitionCount",
            "TotalTransitionCount","ReusedNodeSnapshots","TransitionCacheHits","ChoiceBranchesEvaluated",
            "TotalChoiceBranchesEvaluated","ChoiceReplayAttempts","ChoiceReplayBudgetExhaustions",
            "RoundReplayPrefixCaptures","RoundReplayPrefixReuses","ExecutionChoiceCaptures","ExecutionChoiceReuses",
            "CardChoicePrefixAttempts","CardChoicePrefixCaptures","CardChoicePrefixReuses","CardChoicePrefixFallbacks",
            "PotionChoicePrefixForks","PotionChoicePrefixCaptures","PotionChoicePrefixReuses","PotionChoicePrefixFallbacks",
            "DuplicateCardBranchesPruned","TranspositionBranchesPruned","DominatedActionsPruned","TopQueueActionsDropped",
            "ActionAdmissionRepresentativesProtected","RepeatableNoProgressBranchesPruned","PrimaryIncumbentBranchesPruned",
            "ChoiceBranchesDroppedByBudget","CycleRegionCandidatesAdmitted","CycleRegionCandidatesDropped",
            "Gen0Collections","Gen1Collections","Gen2Collections","TotalGen0Collections","TotalGen1Collections","TotalGen2Collections"};
        var counts=new Dictionary<string,long>();
        foreach(string name in names) {
            object? value=Get(result,name);
            if(value is not int && value is not long)throw new InvalidOperationException("ADVISOR_WORK_METRICS_CONTRACT_CHANGED: "+name);
            counts[name]=Convert.ToInt64(value);
        }
        return new {scope=Get(result,"ResultScope")?.ToString(),single_session=Get(result,"SingleSessionSearch"),counts};
    }

    object? PhaseMetrics(object result)
    {
        if(!measureSearchPhases)return null;
        // Pinned upstream measures exclusive per-thread stages, subtracting
        // nested scopes. Keep this diagnostic opt-in: timestamps cost work.
        var phases=new Dictionary<string,object>();
        foreach(var property in result.GetType().GetProperties(All))
        {
            if(property.PropertyType.FullName!="CombatSolver.SearchPhaseMetric")continue;
            var metric=property.GetValue(result)!;
            phases.Add(property.Name,new {
                wall_us=(long)(((TimeSpan)Get(metric,"Elapsed")!).TotalMilliseconds*1000),
                allocated_bytes=Convert.ToInt64(Get(metric,"AllocatedBytes"))});
        }
        if(phases.Count==0)throw new InvalidOperationException("ADVISOR_PHASE_METRICS_CONTRACT_CHANGED");
        return phases;
    }
    void EnqueueTurn(int turn) { foreach(var a in route) if(Int(a,"Turn")==turn) pending.Enqueue(a); }

    // HP and potions carry into an immediately following fight only for the
    // first boss of a two-boss final act. Read from native run state; this is
    // an allocation rule for the member list, never a legality or win claim.
    static bool HpCarriesIntoNextBoss(CombatState state)
    {
        if(state.Encounter!.RoomType.ToString()!="Boss"||state.RunState is not RunState run)return false;
        if(run.CurrentActIndex<run.Acts.Count-1)return false;
        return run.Act.SecondBossEncounter is {} second && second.Id!=state.Encounter!.Id;
    }
    // Boss fights of the last act use the plan named "FinalBoss" when the request has one: there
    // the first boss decides the HP of the second and the second one ends the run. Allocation of
    // members only; the fight is still an ordinary boss fight for every other rule.
    static bool InFinalAct(CombatState state)=>state.RunState is RunState run&&run.CurrentActIndex>=run.Acts.Count-1;
    // The pre-existing auto selection already chooses best for final F1. The
    // opt-in only also overrides an explicit first_win there; it does not add
    // members, widen their budgets or remove the existing escalation guard.
    internal static string ResolveSelection(string select,bool hpCarries,bool preferHp)
        =>preferHp&&hpCarries?"best":select=="auto"?(hpCarries?"best":"first_win"):select;
    (Member[] members,string select,bool gate,double? escalateBelow) PlanFor(CombatState state)
    {
        string room=state.Encounter!.RoomType.ToString();
        if((room=="Boss"&&InFinalAct(state)&&gatePlans.TryGetValue("FinalBoss",out var plan))||gatePlans.TryGetValue(room,out plan)) {
            string select=ResolveSelection(plan.Select,HpCarriesIntoNextBoss(state),preferF1Hp);
            return (plan.Members,select,true,plan.EscalateBelow);
        }
        var member=room=="Monster"&&normalNodes!=null?defaultMember with {Nodes=normalNodes}:defaultMember;
        return ([member],"first_win",false,null);
    }
    void Apply(Member member,int budget)
    {
        Set(options,"Profile",member.Profile??baseProfile);
        Set(options,"Beam",member.Beam);
        Set(options,"Nodes",member.Nodes);
        Set(options,"SearchMode",member.Mode);
        Set(options,"UsePortfolio",member.Portfolio);
        Set(options,"BudgetMilliseconds",budget);
        applyFixedBudget.Invoke(null,[options]);
        var key=(member.Portfolio,member.Widths==null?"":string.Join(",",member.Widths),budget);
        if(sessionKey.HasValue&&sessionKey.Value==key)return;
        // Same mapping the pinned harness performs, plus explicit member widths.
        (sessionProperty.GetValue(null) as IDisposable)?.Dispose();
        object session=Activator.CreateInstance(sessionOptionsType,true)!;
        Set(session,"FixedSearchBudget",true);
        Set(session,"MeasureSearchPhases",measureSearchPhases);
        Set(session,"VerifyIncrementalSearch",verifyIncrementalSearch);
        Set(session,"SearchBudgetOverrideMilliseconds",(int?)budget);
        Set(session,"SearchMaxDegreeOfParallelism",(int?)dop);
        Set(session,"UseBeamWidthPortfolio",member.Portfolio);
        Set(session,"BeamWidthPortfolioWidths",member.Widths);
        sessionProperty.SetValue(null,beginOfflineSession.Invoke(null,[session]));
        sessionKey=key;
    }
    static Forecast Describe(object result)
    {
        var snapshot=Get(result,"Snapshot")!;
        bool dead=(bool)Get(snapshot,"PlayerDead")!;
        int hp=Int(snapshot,"ProjectedPlayerHp");
        bool onlyDeath=(bool)Get(result,"OnlyDeathRoutesFound")!;
        int? end=(int?)Get(result,"CombatEndedTurn"),death=(int?)Get(result,"DeathTurn");
        bool won=end!=null&&death==null&&!onlyDeath&&(bool)Get(snapshot,"AllEnemiesDead")!&&!dead&&hp>0;
        int deaths=Get(snapshot,"ProcessedEnemyDeaths") is System.Collections.ICollection processed?processed.Count
            :Get(snapshot,"ProcessedEnemyDeaths") is System.Collections.IEnumerable items?items.Cast<object>().Count():0;
        return new Forecast(won,!dead&&!onlyDeath&&death==null&&hp>0,hp,Int(result,"PotionCount"),death,end,Int(snapshot,"EnemyHp"),deaths);
    }
    void Search(CombatState state,int turn)
    {
        if(forcedF1Winner!=null&&!forcedF1Loaded)
        {
            if(!HpCarriesIntoNextBoss(state))throw RejectF1Winner("F1_WINNER_NOT_FINAL_FIRST_BOSS");
            string live=(string)Get(captureLive.Invoke(null,[state])!,"StateText")!;
            if(forcedF1Winner["root_state_text"]?.GetValue<string>()!=live
                ||forcedF1Winner["entry_turn"]?.GetValue<int>()!=turn
                ||forcedF1Winner["encounter"]?.GetValue<string>()!=state.Encounter!.Id.Entry)
                throw RejectF1Winner("F1_WINNER_ROOT_STATE_MISMATCH");
            if(forcedF1Winner["forecast"]?["won"]?.GetValue<bool>()!=true)
                throw RejectF1Winner("F1_WINNER_FORECAST_REQUIRED");
            var actions=forcedF1Winner["route_actions"]?.AsArray()
                ??throw RejectF1Winner("F1_WINNER_ROUTE_REQUIRED");
            if(actions.Count==0)throw RejectF1Winner("F1_WINNER_ROUTE_EMPTY");
            route=actions.Select(a=>JsonSerializer.Deserialize(a!.ToJsonString(),planActionType!,Program.Json)
                ??throw new InvalidDataException("F1_WINNER_PLAN_ACTION")).ToArray();
            continuations=(forcedF1Winner["continuations"]?.AsArray()
                ??throw new InvalidDataException("F1_WINNER_CONTINUATIONS_REQUIRED"))
                .Select(a=>(object)new RestoredContinuation(a!["StateText"]!.GetValue<string>(),
                    a["StartTurnNumber"]!.GetValue<int>(),a["ForecastOffset"]!.GetValue<int>())).ToArray();
            forcedF1Loaded=true;forcedF1Combat=state;pending.Clear();pendingChoices.Clear();needsReplan=false;
            predictsCompleteCombatVictory=true;EnqueueTurn(turn);Count("f1_winner_routes_loaded");
            return;
        }
        RequireNoForcedF1Fallback("unexpected_search");
        if(!scalarPowerCowValidated&&validateScalarPowerCow is not null)
        {
            _=validateScalarPowerCow.Invoke(null,[state]);
            scalarPowerCowValidated=true;Count("b1_power_cow_contract_passed");
        }
        pending.Clear();needsReplan=false;
        Count("search_calls");
        using var timing=PhaseProfiler.Current.Enter("beam_search");
        string room=state.Encounter!.RoomType.ToString();
        int budget=room=="Boss"?bossBudget:ordinaryBudget;
        var (members,select,gate,escalateBelow)=PlanFor(state);
        if(gate)Count("gate_search_calls");
        decimal liveEnemyHp=state.Enemies.Where(e=>e.IsAlive).Sum(e=>e.CurrentHp);
        bool captureThisRoot=captureF1Winners&&HpCarriesIntoNextBoss(state)&&!ReferenceEquals(capturedF1Combat,state);
        string? f1RootText=captureThisRoot?(string)Get(captureLive.Invoke(null,[state])!,"StateText")!:null;
        object? chosen=null;Forecast best=default;int chosenIndex=-1;
        var rows=new List<(int index,Member member,object outcome,Forecast forecast,object? copy)>();
        for(int index=0;index<members.Length;index++)
        {
            Apply(members[index],budget);
            copyWorkProbe?.GetMethod("BeginSearch",All)!.Invoke(null,null);
            var outcome=search.Invoke(null,[state,options,loop,null])!;
            object? copyMetrics=copyWorkProbe?.GetMethod("EndSearch",All)!.Invoke(null,null);
            var forecast=Describe(Get(outcome,"Result")!);
            rows.Add((index,members[index],outcome,forecast,copyMetrics));
            // Strictly better only: ties keep the earlier (cheaper) member.
            if(chosen==null||forecast.BetterThan(best)) {chosen=outcome;best=forecast;chosenIndex=index;}
            Count("member_searches");
            if(select=="first_win"&&forecast.Won)break;
            if(index==0&&!forecast.Won&&escalateBelow is {} threshold&&forecast.EnemyDeaths==0
                &&(liveEnemyHp<=0||forecast.EnemyHp>(double)liveEnemyHp*threshold))
            {
                // The cheapest member was nowhere near: spend the wider members elsewhere.
                Count("gate_escalation_skipped");break;
            }
        }
        route=(object[])Get(chosen!,"RouteActions")!;
        continuations=(object[])Get(chosen!,"Continuations")!;
        // A solver forecast is only a proposal, never a native win certificate.
        // Receding-horizon mode re-solves incomplete/death forecasts next turn;
        // predicted complete wins STILL require exact live StateText equality.
        predictsCompleteCombatVictory=best.Won;
        if(captureThisRoot)
        {
            Count("f1_winner_roots_captured");
            capturedF1Combat=state;
            foreach(var row in rows.Where(r=>r.forecast.Won))
            {
                availableF1Winners.Add(new JsonObject {
                    ["schema"]="spire-f1-winner/v1",["root_state_text"]=f1RootText,["entry_turn"]=turn,
                    ["encounter"]=state.Encounter!.Id.Entry,["member_index"]=row.index,
                    ["member"]=JsonSerializer.SerializeToNode(row.member.Describe(),Program.Json),
                    ["forecast"]=JsonSerializer.SerializeToNode(row.forecast.Describe(),Program.Json),
                    ["forecast_native_json"]=JsonSerializer.Serialize(row.forecast.Describe(),Program.Json),
                    ["selected"]=row.index==chosenIndex,
                    ["route_actions"]=JsonSerializer.SerializeToNode((object[])Get(row.outcome,"RouteActions")!,Program.Json),
                    ["continuations"]=JsonSerializer.SerializeToNode((object[])Get(row.outcome,"Continuations")!,Program.Json),
                    ["forecast_is_unverified_proposal"]=true
                });
            }
        }
        if(gate) { Count(best.Won?"gate_forecast_win":"gate_forecast_no_win"); if(chosenIndex>0)Count("gate_selected_later_member"); }
        foreach(var row in rows)
        {
            var result=Get(row.outcome,"Result")!;
            object entry=new {turn,budget_ms=budget,beam_override=row.member.Beam,wall_us=(long)((double)Get(row.outcome,"WallSeconds")!*1_000_000),
                expanded_nodes=Get(result,"TotalExpandedNodes"),searched_turns=Get(result,"SearchedTurns"),
                boundary=Get(result,"BoundaryReason")?.ToString(),time_boundary=Get(row.outcome,"TimeBoundaryObserved"),
                worker_yields=Get(result,"WorkerYieldCount"),frame_waits=Get(result,"FrameRecoveryWaitCount"),
                frame_wait_ms=((TimeSpan)Get(result,"FrameRecoveryWaitDuration")!).TotalMilliseconds,
                gc_pause_ms=((TimeSpan)Get(result,"GcPauseDuration")!).TotalMilliseconds,
                allocated_bytes=Get(result,"WorkerAllocatedBytes"),
                measure_search_phases=measureSearchPhases,phase_metrics=PhaseMetrics(result),work_metrics=WorkMetrics(result),
                projected_combat_end_turn=Get(result,"CombatEndedTurn"),projected_death_turn=Get(result,"DeathTurn"),
                only_death_routes_found=Get(result,"OnlyDeathRoutesFound"),runtime_processor_count=Environment.ProcessorCount,
                predicts_complete_combat_victory=row.forecast.Won,
                room,encounter=state.Encounter!.Id.Entry,gate,select,member_index=row.index,member_count=members.Length,
                member=row.member.Describe(),forecast=row.forecast.Describe(),selected=row.index==chosenIndex};
            if(row.copy is not null)
            {
                var json=(JsonObject)JsonSerializer.SerializeToNode(entry)!;
                json["copy_diagnostics"]=JsonSerializer.SerializeToNode(row.copy);
                searchRecords.Add(json);
            }
            else searchRecords.Add(entry);
        }
        PhaseProfiler.Current.Count("advisor_search_calls");
        EnqueueTurn(turn);
    }
}
