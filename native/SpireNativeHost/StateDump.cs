using System.Collections;
using System.Linq.Expressions;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Random;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.TestSupport;

namespace SpireNativeHost;

internal sealed partial class CampaignReplay
{
    // G2 fork: opt-in full state dump at the first decision after the supplied
    // history, then stop at that decision (status DECISION, like
    // stop_after_combat). Reads backing fields only:
    // no property getters, no RNG calls, no setters. Default off; a request
    // without dump_state_at_end is unchanged.
    bool dumpStateAtEnd;
    const int SnapDepth = 3;
    static readonly Dictionary<Type, FieldInfo[]> SnapFields = [];
    static readonly Dictionary<FieldInfo, Func<object, object?>> Getters = [];

    // FieldInfo.GetValue runs the declaring type's static constructor first.
    // Some model types (RelicModel: Godot StringName statics) never initialise
    // in this host and would crash natively. A compiled ldfld reads the
    // instance field without class initialisation.
    static object? Read(FieldInfo f, object target)
    {
        if (!Getters.TryGetValue(f, out var get))
        {
            var p = Expression.Parameter(typeof(object));
            var body = Expression.Convert(Expression.Field(Expression.Convert(p, f.DeclaringType!), f), typeof(object));
            Getters[f] = get = Expression.Lambda<Func<object, object?>>(body, p).Compile();
        }
        return get(target);
    }

    void StateDumpInit() =>
        dumpStateAtEnd = request.TryGetProperty("dump_state_at_end", out var d) && d.GetBoolean();

    void StateDumpAndStop(string name, object[] options)
    {
        string before = RoomCheckpoint.Canonical(AllRngFields());
        var dump = new JsonObject
        {
            ["schema"] = "spire-fork-state/v1", ["prefix_length"] = transcript.Count, ["phase"] = name,
            ["act"] = run.CurrentActIndex, ["floor"] = run.TotalFloor, ["room"] = run.CurrentRoom?.RoomType.ToString(),
            ["in_combat"] = CombatManager.Instance.IsInProgress,
        };
        try { dump["run"] = RoomCheckpoint.CaptureRun(); }
        catch (Exception e) { dump["run_error"] = e.GetType().Name + ": " + e.Message; }
        dump["rng"] = AllRngFields();
        dump["player"] = new JsonObject
        {
            ["creature"] = Snap(player.Creature), ["powers"] = SnapList(player.Creature.Powers),
            ["relics"] = SnapList(player.Relics), ["potion_slots"] = SnapList(player.PotionSlots),
            ["combat_state"] = player.PlayerCombatState is { } pcs ? Snap(pcs) : null,
            ["piles"] = player.PlayerCombatState is { } piles ? new JsonObject
            {
                ["hand"] = SnapList(piles.Hand.Cards), ["draw"] = SnapList(piles.DrawPile.Cards),
                ["discard"] = SnapList(piles.DiscardPile.Cards), ["exhaust"] = SnapList(piles.ExhaustPile.Cards),
                ["play"] = SnapList(piles.PlayPile.Cards),
            } : null,
            ["deck"] = SnapList(player.Deck.Cards),
        };
        var combat = CombatManager.Instance.DebugOnlyGetState();
        if (combat != null)
        {
            dump["combat_state"] = Snap(combat);
            dump["enemies"] = new JsonArray(combat.Enemies.Select(c => (JsonNode)new JsonObject
            {
                ["creature"] = Snap(c), ["powers"] = SnapList(c.Powers),
                ["monster"] = c.Monster != null ? Snap(c.Monster) : null,
            }).ToArray());
        }
        if (run.CurrentRoom is CombatRoom room) dump["encounter"] = Snap(room.Encounter);
        dump["test_rng_injector"] = new JsonObject(typeof(TestRngInjector)
            .GetFields(BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public)
            .Select(f => KeyValuePair.Create(f.Name, (JsonNode?)(f.GetValue(null) == null ? null : f.GetValue(null)!.GetType().Name))));
        dump["shuffle_hooks"] = new JsonArray(ShuffleHookModels().Select(x => (JsonNode)x).ToArray());
        dump["available_actions"] = JsonSerializer.SerializeToNode(options);
        dump["dump_side_effect_free"] = before == RoomCheckpoint.Canonical(AllRngFields());
        dump["forced"] = ForcedLazyState();
        File.WriteAllText(Path.Combine(output, "state-dump.json"), dump.ToJsonString());
        menu = options;
        if (options.Length == 0) boundary = "Nonterminal native menu is empty";
        throw new NeedDecision();
    }

    // After the side-effect-free dump: run the getters that fill lazy caches
    // (CardModel Pool / Tags / TitleLocString / BaseStarCost / DynamicVars,
    // PotionModel DynamicVars) and snap the same objects again, so a cache that
    // is filled on one side only can be compared by value. The run stops right
    // after this decision, so the getters cannot affect later play.
    JsonObject ForcedLazyState()
    {
        var cards = new List<CardModel>(player.Deck.Cards);
        if (player.PlayerCombatState is { } pcs) foreach (var pile in pcs.AllPiles) cards.AddRange(pile.Cards);
        foreach (var card in cards)
        {
            _ = card.Pool; _ = card.Tags.Count(); _ = card.TitleLocString; _ = card.BaseStarCost; _ = card.DynamicVars;
        }
        foreach (var potion in player.PotionSlots) if (potion != null) _ = potion.DynamicVars;
        return new JsonObject
        {
            ["potion_slots"] = SnapList(player.PotionSlots), ["deck"] = SnapList(player.Deck.Cards),
            ["piles"] = player.PlayerCombatState is { } piles ? new JsonObject
            {
                ["hand"] = SnapList(piles.Hand.Cards), ["draw"] = SnapList(piles.DrawPile.Cards),
                ["discard"] = SnapList(piles.DiscardPile.Cards), ["exhaust"] = SnapList(piles.ExhaustPile.Cards),
                ["play"] = SnapList(piles.PlayPile.Cards),
            } : null,
        };
    }

    // Every Rng reachable from run, player, map, room and combat, read from fields.
    JsonObject AllRngFields()
    {
        var o = new JsonObject
        {
            ["run_rng"] = JsonSerializer.SerializeToNode(run.Rng.ToSerializable(), Program.Json),
            ["run_odds"] = JsonSerializer.SerializeToNode(run.Odds.ToSerializable(), Program.Json),
            ["player_rng"] = JsonSerializer.SerializeToNode(player.PlayerRng.ToSerializable(), Program.Json),
            ["player_odds"] = JsonSerializer.SerializeToNode(player.PlayerOdds.ToSerializable(), Program.Json),
        };
        var local = new JsonObject();
        if (run.Map != null) local["map"] = JsonSerializer.SerializeToNode(LocalRngFields(run.Map), Program.Json);
        if (run.CurrentRoom is CombatRoom room)
            local["encounter:" + room.Encounter.Id.Entry] = JsonSerializer.SerializeToNode(LocalRngFields(room.Encounter), Program.Json);
        var combat = CombatManager.Instance.DebugOnlyGetState();
        if (combat != null)
            foreach (var creature in combat.Enemies)
                if (creature.Monster != null)
                    local["monster:" + creature.CombatId + ":" + creature.Monster.Id.Entry] =
                        JsonSerializer.SerializeToNode(LocalRngFields(creature.Monster), Program.Json);
        o["local_rng"] = local;
        return o;
    }

    IEnumerable<string> ShuffleHookModels()
    {
        var models = new List<AbstractModel>();
        models.AddRange(player.Relics);
        models.AddRange(player.Deck.Cards);
        if (player.PlayerCombatState is { } pcs) foreach (var pile in pcs.AllPiles) models.AddRange(pile.Cards);
        foreach (var model in models)
        {
            var method = model.GetType().GetMethod("ModifyShuffleOrder", BindingFlags.Instance | BindingFlags.Public);
            if (method != null && method.DeclaringType != typeof(AbstractModel)) yield return model.GetType().Name;
            foreach (var f in Fields(model.GetType()))
                if (Read(f, model) is AbstractModel inner && inner.GetType().GetMethod("ModifyShuffleOrder",
                        BindingFlags.Instance | BindingFlags.Public)?.DeclaringType is { } t && t != typeof(AbstractModel))
                    yield return model.GetType().Name + "/" + inner.GetType().Name;
        }
    }

    static FieldInfo[] Fields(Type type)
    {
        if (SnapFields.TryGetValue(type, out var cached)) return cached;
        var list = new List<FieldInfo>();
        for (Type? t = type; t != null && t != typeof(object); t = t.BaseType)
            list.AddRange(t.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)
                .Where(f => !typeof(Delegate).IsAssignableFrom(f.FieldType)));
        return SnapFields[type] = list.ToArray();
    }

    static JsonArray SnapList<T>(IEnumerable<T> items) =>
        new(items.Select(x => x == null ? null : (JsonNode?)Snap(x)).ToArray());

    static JsonObject Snap(object target) => SnapObject(target, 0, new HashSet<object>(ReferenceEqualityComparer.Instance));

    static JsonObject SnapObject(object target, int depth, HashSet<object> seen)
    {
        seen.Add(target);
        var o = new JsonObject { ["$type"] = target.GetType().Name };
        foreach (var f in Fields(target.GetType()))
            o[f.DeclaringType!.Name + "." + f.Name] = Value(Read(f, target), depth + 1, seen);
        return o;
    }

    static bool Ours(Type t) => t.Namespace?.StartsWith("MegaCrit", StringComparison.Ordinal) == true;

    static JsonNode? Value(object? v, int depth, HashSet<object> seen)
    {
        switch (v)
        {
            case null: return null;
            case string s: return s;
            case bool b: return b;
            case Enum e: return e.ToString();
            case Rng r: return JsonSerializer.SerializeToNode(r.ToSerializable(), Program.Json);
            case ModelId id: return id.ToString();
            case AbstractModel m: return "model:" + m.GetType().Name + ":" + m.Id;   // a reference; targets are snapped separately
            case Creature c: return "creature:" + c.CombatId;
            case Player: return "player";
            // Run-level state is compared through the run save and the RNG block;
            // a restored map is a SavedActMap without generation-only fields.
            case RunState: return "$RunState";
            case ActMap: return "$ActMap";
            case Delegate: return null;
        }
        var type = v.GetType();
        if (type.IsPrimitive || v is decimal) return JsonSerializer.SerializeToNode(v, type);
        if (v is IDictionary dictionary)
        {
            var entries = new List<JsonNode>();
            foreach (DictionaryEntry entry in dictionary)
                entries.Add(new JsonArray(Value(entry.Key, depth, seen), Value(entry.Value, depth, seen)));
            return Sorted(entries);
        }
        if (v is IEnumerable sequence && (type.IsArray || type.IsGenericType))
        {
            var items = new List<JsonNode?>();
            foreach (var item in sequence) items.Add(Value(item, depth, seen));
            // Hash-ordered sets of references enumerate in a per-process order.
            bool unordered = type.GetInterfaces().Any(i => i.IsGenericType && i.GetGenericTypeDefinition() == typeof(ISet<>));
            return unordered ? Sorted(items) : new JsonArray(items.ToArray());
        }
        if (!Ours(type) && !(type.IsValueType && type.Namespace?.StartsWith("System", StringComparison.Ordinal) != true))
            return "$" + type.Name;   // runtime/framework objects (tasks, clocks, Godot): not game state
        if (depth > SnapDepth || seen.Contains(v)) return "$" + type.Name + "@depth";
        return SnapObject(v, depth, seen);
    }

    static JsonArray Sorted(IEnumerable<JsonNode?> items) =>
        new(items.OrderBy(RoomCheckpoint.Canonical, StringComparer.Ordinal).ToArray());
}
