using System.Globalization;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.Loader;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Serialization.Metadata;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using MegaCrit.Sts2.Core.Saves.Runs;
using MegaCrit.Sts2.Core.Unlocks;
using OfflineSearchHarness;

namespace SpireNativeHost;

internal static class Bootstrap
{
    [ModuleInitializer]
    internal static void InstallResolver()
    {
        AssemblyLoadContext.Default.Resolving += (context, name) =>
        {
            string directory = Environment.GetEnvironmentVariable("SPIRE_GAME_DATA")
                ?? (string?)AppContext.GetData("Sts2DataDir")
                ?? throw new InvalidOperationException("Missing game data directory");
            string file = Path.GetFullPath(Path.Combine(directory, name.Name + ".dll"));
            return File.Exists(file) ? context.LoadFromAssemblyPath(file) : null;
        };
    }
}

internal static class Program
{
    internal static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        IncludeFields = true,
        Converters = { new JsonStringEnumConverter() },
    };

    static bool initialized;
    static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--worker")
        {
            string? line;
            Console.WriteLine("SPIRE_WORKER_READY " + Environment.ProcessId);
            while ((line = Console.ReadLine()) != null)
            {
                if (line == "QUIT") break;
                bool healthy = true; string? failure = null;
                try { Execute([line]); }
                catch (Exception e) { healthy = false; failure = e.ToString(); }
                try { Cleanup(); } catch (Exception e) { healthy = false; failure ??= e.ToString(); }
                Console.WriteLine("SPIRE_WORKER_RESULT " + JsonSerializer.Serialize(new {healthy, error=failure, pid=Environment.ProcessId}));
                Console.Out.Flush();
                if (!healthy) return 1;
            }
            return 0;
        }
        // The outer method stays game-type-free so incompatible assemblies get a diagnostic.
        try { return Execute(args); }
        catch (Exception error)
        {
            Console.Error.WriteLine(JsonSerializer.Serialize(new
            {
                status = "NATIVE_HOST_FAILED", proven_optimal = false,
                error = error.ToString()
            }, Json));
            return 1;
        }
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    static int Execute(string[] args)
    {
        if (args.Length != 1) throw new ArgumentException("Usage: SpireNativeHost request.json");
        using JsonDocument document = JsonDocument.Parse(File.ReadAllText(args[0]));
        JsonElement request = document.RootElement;
        string output = request.GetProperty("out").GetString()!;
        if (Directory.Exists(output) && Directory.EnumerateFileSystemEntries(output).Any())
            throw new IOException("Output directory must be empty");
        Directory.CreateDirectory(output);
        string command = request.GetProperty("command").GetString()!;
        HarnessLog.TraceInit = false;
        HarnessLog.Language = "eng";
        MainLoopContext loop = new();
        SynchronizationContext.SetSynchronizationContext(loop);
        PhaseProfiler.Current = new();
        if (!initialized)
        {
            using (PhaseProfiler.Current.Enter("bootstrap"))
            {
                GameBootstrap.ApplyGodotBypasses();
                GameBootstrap.SkipGodotNodeStaticConstructors();
                GameBootstrap.InitializeStaticState();
            }
            initialized = true;
        }
        var identity = NativeIdentity();
        Write(output, "identity.json", identity);
        if (!request.TryGetProperty("compact", out var compact) || !compact.GetBoolean())
            Write(output, "api.json", ApiManifest());
        if (command == "catalog") Write(output, "catalog.json", Catalog());
        else if(command=="effect_metadata") Write(output,"effect-metadata.json",
            request.GetProperty("cards").EnumerateArray().Select(id=> {
                var card=ModelDb.AllCards.Single(c=>c.Id.Entry==id.GetString()).ToMutable();
                return new {card=card.Id.Entry,direct=NativeEffectMetadata.DirectFor(card.GetType()).Order().ToArray(),
                    linked=NativeEffectMetadata.For(card.GetType()).Order().ToArray(),
                    capability=StrategicStateEvaluator.Describe(card),scope="read-only native model metadata; no cards granted or played"};
            }).ToArray());
        else if (command == "seed") ExportSeed(request, output, loop);
        else if (command == "replay") {
            var decision=CampaignReplay.Run(request, loop);
            if(request.TryGetProperty("low_io",out var lowIo)&&lowIo.GetBoolean()) {
                using var file=File.Create(Path.Combine(output,"decision.json.gz"));
                using var gzip=new GZipStream(file,CompressionLevel.Fastest);
                JsonSerializer.Serialize(gzip,decision,new JsonSerializerOptions(Json){WriteIndented=false});
            } else Write(output,"decision.json",decision);
        }
        else if (command == "score")
        {
            var save = JsonSerializer.Deserialize<SerializableRun>(
                File.ReadAllText(request.GetProperty("save").GetString()!),
                JsonSerializationUtility.GetTypeInfo<SerializableRun>())
                ?? throw new InvalidDataException("Null run save");
            bool victory = request.GetProperty("victory").GetBoolean();
            Write(output, "score.json", new
            {
                score = ScoreUtility.CalculateScore(save, victory), victory,
                source = "installed sts2.dll: ScoreUtility.CalculateScore(SerializableRun, bool)",
                terminal_status_supplied_by_caller = true,
                proven_optimal = false
            });
        }
        else throw new ArgumentException("Unknown native command: " + command);
        Write(output, "result.json", new
        {
            status = "NATIVE_DATA_EXPORTED", command, identity,
            proven_optimal = false, game_equivalence_verified = false,
            game_data_source = "installed sts2.dll", gameplay_mods_loaded = false,
            bypasses = GameBootstrap.Bypasses
        });
        Write(output, "performance.json", PhaseProfiler.Current.Snapshot());
        Console.WriteLine("NATIVE_DATA_EXPORTED " + Path.GetFullPath(output));
        return 0;
    }

    static void Cleanup()
    {
        CampaignReplay.Detach();
        if (initialized) RunManager.Instance.CleanUp(false);
    }

    static void ExportSeed(JsonElement request, string output, MainLoopContext loop)
    {
        string seed = request.GetProperty("seed").GetString()!;
        string characterId = request.GetProperty("character").GetString()!;
        string unlocks = request.GetProperty("unlocks").GetString()!;
        int ascension = request.GetProperty("ascension").GetInt32();
        UnlockState unlockState = unlocks switch
        {
            "all" => UnlockState.all,
            "none" => UnlockState.none,
            _ => throw new ArgumentException("unlocks must be all or none; real profile not imported")
        };
        CharacterModel character = ModelDb.AllCharacters.Single(c =>
            c.Id.Entry.Equals(characterId, StringComparison.OrdinalIgnoreCase));
        RunState run = RunState.CreateForNewRun(
            [Player.CreateForNewRun(character, unlockState, 1UL)],
            ActModel.GetDefaultList().Select(a => a.ToMutable()).ToList(), [],
            GameMode.Standard, ascension, seed);
        RunManager.Instance.SetUpNewSingleplayer(run, shouldSave: false);
        loop.RunUntilCompleted(RunManager.Instance.FinalizeStartingRelics(),
            TimeSpan.FromSeconds(30), "starting relics");
        // Map generation only. Do not enter Neow or auto-select an event option.
        loop.RunUntilCompleted(RunManager.Instance.GenerateMap(),
            TimeSpan.FromSeconds(30), "native map generation");
        Write(output, "map.json", SerializableActMap.FromActMap(run.Map));
        SerializableRun snapshot = RunManager.Instance.ToSave(null);
        string nativeJson = JsonSerializationUtility.ToJson(snapshot);
        File.WriteAllText(Path.Combine(output, "run-save-native.json"), nativeJson);
        // v0.111.0 omits CanBeModified=false but its DTO constructor defaults to true.
        // Keep the original native bytes as evidence; adapt only this serialization
        // omission, without changing game objects, rules, or on-disk player saves.
        JsonSerializerOptions snapshotOptions = new(JsonSerializationUtility.Options)
        {
            TypeInfoResolver = JsonSerializationUtility.Options.TypeInfoResolver!
                .WithAddedModifier(info =>
                {
                    if (info.Type == typeof(SerializableMapPoint))
                        foreach (var property in info.Properties)
                            if (property.Name == "can_modify") property.ShouldSerialize = (_, _) => true;
                })
        };
        string savedJson = JsonSerializer.Serialize(snapshot, snapshotOptions);
        File.WriteAllText(Path.Combine(output, "run-save.json"), savedJson);
        SerializableRun restoredSave = JsonSerializer.Deserialize(savedJson,
            JsonSerializationUtility.GetTypeInfo<SerializableRun>())
            ?? throw new InvalidDataException("Native save deserialized to null");
        string restoredJson = JsonSerializer.Serialize(restoredSave, snapshotOptions);
        if (restoredJson != savedJson)
        {
            File.WriteAllText(Path.Combine(output, "run-save-roundtrip-diff.json"), restoredJson);
            throw new InvalidDataException("Native save serialization roundtrip changed data");
        }
        // Exercise native model reconstruction, separately from JSON roundtrip. This is
        // a pre-room run snapshot, never an arbitrary mid-combat checkpoint.
        RunState restored = RunState.FromSerializable(restoredSave);
        if (JsonSerializer.Serialize(restored.Rng.ToSerializable(), Json) !=
            JsonSerializer.Serialize(run.Rng.ToSerializable(), Json))
            throw new InvalidDataException("Native run reconstruction changed RNG");
        Write(output, "roundtrip.json", new
        {
            adapted_json_roundtrip_equal = true, native_run_reconstructed = true,
            serialization_adapter = "explicit SerializableMapPoint.can_modify false",
            native_json_needed_adapter = nativeJson != savedJson,
            run_rng_roundtrip_equal = true,
            arbitrary_combat_restore_verified = false, game_equivalence_verified = false
        });
        Write(output, "seed.json", new
        {
            schema = "spire-native-seed/v1", seed, character = character.Id.Entry,
            ascension, unlocks, started_with_neow = run.ExtraFields.StartedWithNeow,
            acts = run.Acts.Select(a => new { id = a.Id.Entry, native = a.ToSave() }),
            rng = run.Rng.ToSerializable(),
            initial_deck = run.Players[0].Deck.Cards.Select(c => c.Id.Entry),
            score_at_current_state = ScoreUtility.CalculateScore(run, false),
            scope = "initial act map and run setup; later maps depend on run state and are not precomputed",
            whole_run_transitions_implemented = false, proven_optimal = false
        });
    }

    static object Catalog()
    {
        return new
        {
            schema = "spire-native-catalog/v1",
            source = "ModelDb from installed sts2.dll; no card-effect reimplementation",
            cards = ModelDb.AllCards.OrderBy(c => c.Id.Entry).Select(c => new
            {
                id = c.Id.Entry, type = c.Type.ToString(), rarity = c.Rarity.ToString(),
                target = c.TargetType.ToString(), energy = typeof(CardModel)
                    .GetProperty("CanonicalEnergyCost", BindingFlags.Instance | BindingFlags.NonPublic)!
                    .GetValue(c),
                stars = c.CanonicalStarCost,
                keywords = c.CanonicalKeywords.Select(k => k.ToString()),
                variables = c.DynamicVars.Select(v => new
                {
                    name = v.Key, value = v.Value.BaseValue.ToString(CultureInfo.InvariantCulture),
                    encoding = "decimal-string"
                })
            }).ToArray(),
            models = ModelDb.All.OrderBy(m => m.Id.ToString()).Select(m => new
            {
                id = m.Id.ToString(), type = m.GetType().FullName,
                category = Category(m.GetType())
            }).ToArray(),
            characters = ModelDb.AllCharacters.Select(c => c.Id.Entry).ToArray(),
            score_methods = typeof(ScoreUtility).GetMethods(BindingFlags.Static | BindingFlags.Public)
                .Select(m => m.ToString()).Order().ToArray()
        };
    }

    static string Category(Type type)
    {
        while (type.BaseType != null && type.BaseType != typeof(AbstractModel)) type = type.BaseType;
        return type.Name;
    }

    static object? identityCache;
    internal static object NativeIdentity() => identityCache ??= new
    {
        schema = "spire-native-identity/v1", host_version = "1",
        host_sha256 = Hash(typeof(Program).Assembly.Location),
        game_sha256 = Hash(typeof(ModelDb).Assembly.Location),
        godot_sha256 = Hash(typeof(Godot.GodotObject).Assembly.Location),
        harmony_sha256 = Hash(typeof(HarmonyLib.Harmony).Assembly.Location),
        game_assembly = typeof(ModelDb).Assembly.FullName,
        bootstrap = "CombatSolver 4d2c550; independent host, no CombatSolver engine or RitsuLib dependency"
    };

    static object ApiManifest()
    {
        Type[] types = [typeof(ModelDb), typeof(RunState), typeof(RunManager), typeof(ScoreUtility),
            typeof(CardModel), typeof(ActModel), typeof(SerializableRun), typeof(SerializableActMap)];
        return new
        {
            schema = "spire-native-api/v1",
            types = types.OrderBy(t => t.FullName).Select(t => new
            {
                name = t.FullName,
                members = t.GetMembers(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static
                    | BindingFlags.DeclaredOnly).Select(m => m.MemberType + " " + m).Order().ToArray()
            }).ToArray()
        };
    }

    static string Hash(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
    static void Write(string output, string name, object value) =>
        File.WriteAllText(Path.Combine(output, name), JsonSerializer.Serialize(value, Json));
}
