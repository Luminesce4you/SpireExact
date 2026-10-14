using System.Reflection;
using System.Runtime.Loader;
using System.Security.Cryptography;

namespace SpireNativeHost;

// The reflective contract between this host and a CombatSolver build.
// BeamAdvisor, AdvisorBlockCompensation and AdvisorDiagnosticLog reach every
// name below by reflection; a rename in a new solver release fails there one
// name at a time, and a renamed result property silently reads as null.
// Check() lists every missing name at once, before any search runs. It is a
// separate command (--solver-contract); the request path is unchanged.
internal static class SolverBridge
{
    const BindingFlags All = BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static|BindingFlags.Instance;

    // (assembly, type, members). Nested types use '+'.
    static readonly (string Assembly,string Type,string[] Members)[] Contract =
    [
        ("solver","CombatSolver.SolverController",["DisplayServerNameProvider","FindCardForDeployment"]),
        ("solver","CombatSolver.ContinuationStamp",["CaptureLive","DescribeDifferences"]),
        ("solver","CombatSolver.PlanCardToken",[]),
        ("solver","CombatSolver.CardChoiceSupport",["MatchesToken","ChoiceCardKey"]),
        ("solver","CombatSolver.UnattendedTestRunner",["BeginOfflineSession"]),
        ("solver","CombatSolver.UnattendedTestRunner+OfflineSessionOptions",["BeamWidthPortfolioWidths","FixedSearchBudget",
            "MeasureSearchPhases","SearchBudgetOverrideMilliseconds","SearchMaxDegreeOfParallelism","UseBeamWidthPortfolio",
            "VerifyIncrementalSearch"]),
        ("solver","CombatSolver.SolverSettings",["Current"]),
        ("solver","CombatSolver.SolverSettingsData",["OnlineStatisticsEnabled"]),
        ("solver","CombatSolver.SolverResult",["BoundaryReason","CombatEndedTurn","DeathTurn","FrameRecoveryWaitCount",
            "FrameRecoveryWaitDuration","GcPauseDuration","OnlyDeathRoutesFound","ResultScope","SearchedTurns",
            "SingleSessionSearch","Snapshot","TotalExpandedNodes","WorkerAllocatedBytes","WorkerYieldCount","StartTurnNumber"]),
        ("solver","CombatSolver.PlanAction",["Kind","CardId","PotionId","PotionSlot","TargetCombatId","TurnStartChoices","Turn"]),
        ("solver","CombatSolver.PlanCardChoice",["Cards","Effect"]),
        ("solver","CombatSolver.CorePowerSupport",["ApplyCardPowers"]),
        ("solver","CombatSolver.CombatDiagnosticJournal",["WriteCore"]),
        ("harness","OfflineSearchHarness.HarnessOptions",["Parse","Beam","BudgetMilliseconds","MeasureSearchPhases","Nodes",
            "Profile","SearchMode","UsePortfolio"]),
        ("harness","OfflineSearchHarness.ModRuntime",["Session","ApplyFixedBudgetSettings","ApplyUnattendedOverrides",
            "Initialize","RunSearchDetailed"]),
        ("harness","OfflineSearchHarness.ModRuntime+SearchOutcome",["Result","TimeBoundaryObserved","WallSeconds"]),
        ("harness","OfflineSearchHarness.MainLoopContext",[]),
    ];

    // Names read from result objects whose declaring type is not fixed above:
    // each must exist on at least one type of the solver or harness.
    static readonly string[] Anywhere =
    [
        "AllEnemiesDead","PlayerDead","ProcessedEnemyDeaths","EnemyHp","ProjectedPlayerHp","PotionCount",
        "StateText","StateKey","OptionOccurrence","AllocatedBytes","Elapsed","Continuations","RouteActions",
        "GetActionChoicesInExecutionOrder",
    ];

    static Assembly Load(string path) => AssemblyLoadContext.Default.Assemblies.FirstOrDefault(a=>
        !a.IsDynamic && string.Equals(a.Location,Path.GetFullPath(path),StringComparison.Ordinal))
        ?? AssemblyLoadContext.Default.LoadFromAssemblyPath(Path.GetFullPath(path));

    static string Hash(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();

    static Type[] Types(Assembly assembly)
    {
        try { return assembly.GetTypes(); }
        catch (ReflectionTypeLoadException e) { return e.Types.Where(t=>t!=null).ToArray()!; }
    }

    public static object Check(string solverPath,string harnessPath,string[] dependencyDirs)
    {
        AssemblyLoadContext.Default.Resolving += (context,name)=> {
            foreach(string directory in dependencyDirs) {
                string file=Path.GetFullPath(Path.Combine(directory,name.Name+".dll"));
                if(File.Exists(file)) return Load(file);
            }
            return null;
        };
        var assemblies=new Dictionary<string,Assembly>{["solver"]=Load(solverPath),["harness"]=Load(harnessPath)};
        var everyType=assemblies.Values.SelectMany(Types).ToArray();
        var missingTypes=new List<object>();
        var missingMembers=new List<object>();
        int checkedNames=0;
        foreach(var (asm,typeName,members) in Contract) {
            checkedNames++;
            var type=assemblies[asm].GetType(typeName,false);
            if(type==null) {
                string simple=typeName.Split('.','+').Last();
                missingTypes.Add(new {assembly=asm,type=typeName,
                    candidates=everyType.Where(t=>t.Name==simple).Select(t=>t.FullName).Take(5).ToArray()});
                continue;
            }
            foreach(string member in members) {
                checkedNames++;
                if(type.GetMember(member,All).Length>0) continue;
                missingMembers.Add(new {assembly=asm,type=typeName,member,
                    found_on=everyType.Where(t=>t.GetMember(member,All|BindingFlags.DeclaredOnly).Length>0)
                        .Select(t=>t.FullName).Take(5).ToArray()});
            }
        }
        var missingAnywhere=Anywhere.Where(name=>!everyType.Any(t=>t.GetMember(name,All|BindingFlags.DeclaredOnly).Length>0)).ToArray();
        checkedNames+=Anywhere.Length;
        bool ok=missingTypes.Count==0&&missingMembers.Count==0&&missingAnywhere.Length==0;
        return new {
            schema="spire-solver-contract/v1", status=ok?"OK":"ADVISOR_CONTRACT_CHANGED",
            solver=new {path=Path.GetFullPath(solverPath),sha256=Hash(solverPath),
                version=assemblies["solver"].GetName().Version?.ToString()},
            harness=new {path=Path.GetFullPath(harnessPath),sha256=Hash(harnessPath)},
            checked_names=checkedNames, missing_types=missingTypes, missing_members=missingMembers,
            missing_anywhere=missingAnywhere,
            note="names only; signatures of patched methods are checked by AdvisorBlockCompensation / AdvisorDiagnosticLog when enabled",
        };
    }
}
