using System.Collections;
using System.Text.Json.Nodes;
using HarmonyLib;
using MegaCrit.Sts2.Core.Runs;

namespace SpireNativeHost;

// The native run save omits these live executor fields. Only EMPTY, idle map
// queues are supported; never serialize or silently discard pending actions.
// Fields audited against the pinned user's sts2.dll, not an invented rule model.
internal static class MapActionRuntime
{
    static object? Get(object obj, string field) => AccessTools.Field(obj.GetType(), field).GetValue(obj);
    static void Set(object obj, string field, object? value) => AccessTools.Field(obj.GetType(), field).SetValue(obj, value);
    static IList List(object obj, string field) => (IList)Get(obj, field)!;
    static readonly string[] QueueFlags = ["isCancellingPlayerDrivenCombatActions", "isCancellingCombatActions", "isPaused"];

    public static JsonObject? Capture()
    {
        var run = RunManager.Instance;
        var queues = run.ActionQueueSet;
        var sync = run.ActionQueueSynchronizer;
        if (run.ActionExecutor.IsRunning || !queues.IsEmpty
            || List(queues, "_actionsWaitingForResumption").Count != 0
            || List(sync, "_requestedActionsWaitingForPlayerTurn").Count != 0
            || List(sync, "_hookActions").Count != 0) return null;
        var players = new JsonArray();
        foreach (object queue in List(queues, "_actionQueues"))
        {
            if (List(queue, "actions").Count != 0 || Get(queue, "actionCancellingPlayCardActions") != null) return null;
            var entry = new JsonObject { ["owner_id"] = (ulong)Get(queue, "ownerId")! };
            foreach (string flag in QueueFlags) entry[flag] = (bool)Get(queue, flag)!;
            players.Add(entry);
        }
        return new JsonObject {
            ["executor_paused"] = run.ActionExecutor.IsPaused,
            ["next_action_id"] = queues.NextActionId,
            ["next_hook_id"] = sync.NextHookId,
            ["combat_state"] = Convert.ToInt32(sync.CombatState),
            ["is_in_combat"] = (bool)Get(queues, "_isInCombat")!,
            ["was_reset"] = (bool)Get(queues, "_wasReset")!,
            ["player_queues"] = players
        };
    }

    public static void Restore(JsonObject saved)
    {
        var run = RunManager.Instance;
        if (Capture() == null) throw new InvalidDataException("CHECKPOINT_PENDING_ACTION_RUNTIME");
        var queues = run.ActionQueueSet;
        var sync = run.ActionQueueSynchronizer;
        var players = List(queues, "_actionQueues");
        var savedPlayers = saved["player_queues"]!.AsArray();
        if (players.Count != savedPlayers.Count) throw new InvalidDataException("CHECKPOINT_ACTION_QUEUE_COUNT");
        foreach (object queue in players)
        {
            ulong owner = (ulong)Get(queue, "ownerId")!;
            var row = savedPlayers.Single(p => p!["owner_id"]!.GetValue<ulong>() == owner)!;
            foreach (string flag in QueueFlags) Set(queue, flag, row[flag]!.GetValue<bool>());
        }
        Set(queues, "_nextId", saved["next_action_id"]!.GetValue<uint>());
        Set(queues, "_isInCombat", saved["is_in_combat"]!.GetValue<bool>());
        Set(queues, "_wasReset", saved["was_reset"]!.GetValue<bool>());
        Set(sync, "_nextHookId", saved["next_hook_id"]!.GetValue<uint>());
        var property = AccessTools.Property(sync.GetType(), "CombatState");
        property.SetValue(sync, Enum.ToObject(property.PropertyType, saved["combat_state"]!.GetValue<int>()));
        if (saved["executor_paused"]!.GetValue<bool>()) run.ActionExecutor.Pause();
        else run.ActionExecutor.Unpause();
        RoomCheckpoint.RequireEqual(saved, Capture(), "action_runtime_roundtrip");
    }
}
