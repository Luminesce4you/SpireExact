using System.Collections;
using System.Text.Json.Nodes;
using HarmonyLib;
using MegaCrit.Sts2.Core.Runs;

namespace SpireNativeHost;

// Comparison-only guardian for the audited synchronous TestMode reward
// callback. It never restores, clears queues, completes tasks or resumes work.
// AfterActionFinished clears CurrentlyRunningAction before CheckWinCondition;
// IsRunning can remain true while that callback awaits the reward selector.
internal static class CardMenuActionRuntime
{
    static object? Get(object value, string field) =>
        (AccessTools.Field(value.GetType(),field) ?? throw new InvalidDataException("CARD_MENU_RUNTIME_FIELD:"+field)).GetValue(value);
    static IList List(object value,string field) => (IList)Get(value,field)!;

    public static JsonObject? Capture()
    {
        var run=RunManager.Instance;
        var executor=run.ActionExecutor;
        var current=AccessTools.Property(executor.GetType(),"CurrentlyRunningAction")
            ??throw new InvalidDataException("CARD_MENU_CURRENT_ACTION_API");
        if(current.GetValue(executor)!=null)return null;
        var queues=run.ActionQueueSet;var sync=run.ActionQueueSynchronizer;
        if(!queues.IsEmpty || List(queues,"_actionsWaitingForResumption").Count!=0
            || List(sync,"_requestedActionsWaitingForPlayerTurn").Count!=0 || List(sync,"_hookActions").Count!=0)return null;
        var players=new JsonArray();
        foreach(object queue in List(queues,"_actionQueues"))
        {
            if(List(queue,"actions").Count!=0 || Get(queue,"actionCancellingPlayCardActions")!=null)return null;
            var entry=new JsonObject { ["owner_id"]=(ulong)Get(queue,"ownerId")! };
            foreach(string flag in new[]{"isCancellingPlayerDrivenCombatActions","isCancellingCombatActions","isPaused"})
                entry[flag]=(bool)Get(queue,flag)!;
            players.Add(entry);
        }
        return new JsonObject {
            ["scope"]="pending native reward comparison; no current action and all action queues empty; never a restore snapshot",
            ["executor_running"]=executor.IsRunning,["executor_paused"]=executor.IsPaused,
            ["current_action_is_null"]=true,["next_action_id"]=queues.NextActionId,["next_hook_id"]=sync.NextHookId,
            ["combat_state"]=Convert.ToInt32(sync.CombatState),["is_in_combat"]=(bool)Get(queues,"_isInCombat")!,
            ["was_reset"]=(bool)Get(queues,"_wasReset")!,["player_queues"]=players
        };
    }
}
