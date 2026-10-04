using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Rooms;

namespace SpireNativeHost;

// Read-only native events. An attempted use is not consumption; a removed
// potion is not proof its effect completed. Never alter the combat policy.
internal sealed partial class CampaignReplay
{
    bool captureResourceTelemetry,resourceTelemetryAttached;
    int? resourceTelemetryCaptureFrom;
    readonly List<JsonObject> resourceEvents=[];
    readonly Dictionary<string,int> resourceCounters=new(StringComparer.Ordinal) {
        ["use_attempt"]=0,["used_removed"]=0,["obtained"]=0,["discarded"]=0,["discard_attempt"]=0
    };
    readonly Dictionary<PotionModel,int> observedPotionSlots=new(ReferenceEqualityComparer.Instance);
    readonly HashSet<CombatState> gateEntryCombats=new(ReferenceEqualityComparer.Instance);
    readonly List<JsonObject> gateEntrySources=[];
    void ResourceTelemetryInit(JsonElement source)=>captureResourceTelemetry=
        source.TryGetProperty("capture_resource_telemetry",out var capture)&&capture.GetBoolean();
    void InstallResourceTelemetry()
    {
        if(!captureResourceTelemetry)return;
        resourceTelemetryCaptureFrom=restoredPrefix;
        for(int i=0;i<player.PotionSlots.Count;i++)if(player.PotionSlots[i] is {} potion)observedPotionSlots[potion]=i;
        player.PotionProcured+=ResourcePotionProcured;
        player.PotionDiscarded+=ResourcePotionDiscarded;
        player.UsedPotionRemoved+=ResourcePotionUsedRemoved;
        resourceTelemetryAttached=true;
    }
    void DetachResourceTelemetry()
    {
        if(!resourceTelemetryAttached||player==null)return;
        player.PotionProcured-=ResourcePotionProcured;
        player.PotionDiscarded-=ResourcePotionDiscarded;
        player.UsedPotionRemoved-=ResourcePotionUsedRemoved;
        resourceTelemetryAttached=false;
    }
    void ResourcePotionProcured(PotionModel potion)=>ResourcePotionEvent("obtained",potion,"Player.PotionProcured",false);
    void ResourcePotionDiscarded(PotionModel potion)=>ResourcePotionEvent("discarded",potion,"Player.PotionDiscarded",true);
    void ResourcePotionUsedRemoved(PotionModel potion)=>ResourcePotionEvent("used_removed",potion,"Player.UsedPotionRemoved",true);
    void ResourcePotionEvent(string name,PotionModel potion,string source,bool removed)
    {
        if(!resourceTelemetryAttached||!ReferenceEquals(active,this))return;
        int? slot=null;
        for(int i=0;i<player.PotionSlots.Count;i++)if(ReferenceEquals(player.PotionSlots[i],potion)){slot=i;break;}
        if(slot.HasValue)observedPotionSlots[potion]=slot.Value;
        else if(observedPotionSlots.TryGetValue(potion,out int before))slot=before;
        var row=ResourceEvent(name,source);
        row["potion"]=new JsonObject { ["id"]=potion.Id.Entry,["slot"]=slot };
        resourceEvents.Add(row);resourceCounters[name]++;
        if(removed)observedPotionSlots.Remove(potion);
    }
    JsonObject ResourceEvent(string name,string source)=>new JsonObject {
        ["event"]=name,["action_index"]=transcript.Count-1,["act"]=run.CurrentActIndex,["floor"]=run.TotalFloor,
        ["room"]=run.CurrentRoom?.RoomType.ToString(),
        ["coord"]=run.CurrentMapCoord is {} coord?NativeRouteGraph.Coord(coord):null,
        ["source"]=source,["potion_slots"]=PotionSlotsMetadata(),
        ["hp"]=player.Creature.CurrentHp.ToString(CultureInfo.InvariantCulture),
        ["max_hp"]=player.Creature.MaxHp.ToString(CultureInfo.InvariantCulture),["gold"]=player.Gold,
        ["player_dead"]=player.Creature.IsDead
    };
    void RecordResourceAttempt(JsonObject action)
    {
        if(!captureResourceTelemetry||!resourceTelemetryAttached)return;
        string? name=action["kind"]?.GetValue<string>() switch {"use_potion"=>"use_attempt","discard_potion"=>"discard_attempt",_=>null};
        if(name==null)return;
        int slot=action["slot"]!.GetValue<int>();
        if(slot>=0&&slot<player.PotionSlots.Count&&player.PotionSlots[slot] is {} potion)observedPotionSlots[potion]=slot;
        var row=ResourceEvent(name,"native_action_selected_before_execution");
        row["potion"]=new JsonObject { ["id"]=action["potion"]?.GetValue<string>(),["slot"]=slot };
        row["target"]=action["target"]?.DeepClone();
        resourceEvents.Add(row);resourceCounters[name]++;
    }
    void CaptureGateEntry(string name)
    {
        if(!(captureResourceTelemetry||captureRouteGraph)||name!="combat"||run.CurrentRoom is not CombatRoom room
            ||!gateEntryCombats.Add(room.CombatState))return;
        bool finalFirst=run.CurrentActIndex==run.Acts.Count-1&&room.Encounter.RoomType.ToString()=="Boss"
            &&(run.Acts.Last().SecondBossEncounter==null||run.Acts.Last().SecondBossEncounter!.Id!=room.Encounter.Id);
        gateEntrySources.Add(new JsonObject {
            ["schema"]="spire-native-gate-entry/v1",["index"]=transcript.Count,["act"]=run.CurrentActIndex,
            ["floor"]=run.TotalFloor,["encounter"]=room.Encounter.Id.Entry,["room"]=room.Encounter.RoomType.ToString(),
            ["is_final_first_boss"]=finalFirst,["observation"]=JsonSerializer.SerializeToNode(Observe(),Program.Json)
        });
    }
    object ResourceTelemetryResult()=>new {
        schema="spire-resource-telemetry/v1",enabled=true,
        coverage=new {complete_prefix=resourceTelemetryCaptureFrom==0,capture_from_index=resourceTelemetryCaptureFrom,
            restored_prefix=restoredPrefix,attached_after_setup=resourceTelemetryCaptureFrom.HasValue,
            restored_inventory_not_counted=true},
        counters=resourceCounters,events=resourceEvents,
        scope="read-only actual Player potion events plus selected action attempts; CP-skipped prefix unobserved; removed does not certify effect completion"
    };
}
