using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Rooms;

namespace SpireNativeHost;

// A route proposal is executed through the existing native MAP action. It
// constrains travel only, never fabricates room outcomes or completes a shop.
internal sealed partial class CampaignReplay
{
    bool captureRouteGraph;
    JsonObject? mapRoutePlan;
    JsonObject? mapRouteTopology;
    readonly List<JsonObject> mapDecisionSources=[];
    readonly List<JsonObject> mapRouteExecuted=[];
    readonly List<JsonObject> mapRouteArrivedShops=[];
    readonly HashSet<string> mapRouteTargetKeys=new(StringComparer.Ordinal);
    bool mapRouteEntryChecked;
    int mapRouteMoveIndex;
    JsonObject? mapRoutePendingMove;
    int mapRoutePendingActionIndex;
    string? mapRouteFailedGuard,mapRouteFailureReason;
    const string MapSourceSchema="spire-map-decision-source/v1";
    const string MapPlanSchema="spire-map-route-plan/v1";

    void MapRouteInit(JsonElement source)
    {
        captureRouteGraph=source.TryGetProperty("capture_route_graph",out var capture)&&capture.GetBoolean();
        if(!source.TryGetProperty("map_route_plan",out var plan))return;
        mapRoutePlan=JsonNode.Parse(plan.GetRawText()) as JsonObject
            ??throw new ArgumentException("MAP_ROUTE_PLAN_REQUIRED");
        if(!generateCandidate||stopAtStrategicDecision||source.TryGetProperty("stop_at_floor",out _)
            ||source.TryGetProperty("checkpoint",out _)||captureCheckpoints
            ||source.TryGetProperty("expected_evidence",out _)||probe.HasValue||cardMenuProbe.HasValue
            ||realCardMenuChoice.HasValue||f1WinnerProposal!=null)
            throw new ArgumentException("MAP_ROUTE_CONSUMER_MUST_REPLAY_FRESH_UNSCOPED");
        if(mapRoutePlan["schema"]?.GetValue<string>()!=MapPlanSchema
            ||mapRoutePlan["source"] is not JsonObject entry||entry["schema"]?.GetValue<string>()!=MapSourceSchema
            ||entry["phase"]?.GetValue<string>()!="map"||entry["graph"] is not JsonObject graph
            ||graph["schema"]?.GetValue<string>()!=NativeRouteGraph.Schema
            ||entry["entry_history"] is not JsonArray prefix||entry["index"]?.GetValue<int>()!=prefix.Count
            ||prefix.Count!=history.Length||entry["available_actions"] is not JsonArray
            ||entry["entry_observation"] is not JsonObject||entry["context"] is not JsonObject
            ||entry["native_identity"] is not JsonObject||entry["source_native_state"] is not JsonObject nativeState
            ||nativeState["run"] is not JsonObject||nativeState["progress"] is not JsonObject
            ||nativeState["action_runtime"] is not JsonObject||mapRoutePlan["moves"] is not JsonArray {Count:>0} moves
            ||mapRoutePlan["target_shops"] is not JsonArray targets)
            throw new ArgumentException("MAP_ROUTE_GUARDS_REQUIRED");
        int act=graph["act"]!.GetValue<int>();
        var cursor=graph["current_coord"] is JsonObject current?NativeRouteGraph.ReadCoord(current):(MapCoord?)null;
        var visited=new HashSet<string>(StringComparer.Ordinal);
        int moveIndex=0;
        foreach(var move in moves)
        {
            if(move is not JsonObject step||step["act"]?.GetValue<int>()!=act)
                throw new ArgumentException("MAP_ROUTE_MOVE_ACT");
            var next=NativeRouteGraph.ReadCoord(step);
            if(!visited.Add(NativeRouteGraph.Key(next)))throw new ArgumentException("MAP_ROUTE_REPEATED_MOVE");
            // The source's real menu defines the first admissible move,
            // including maps whose native initial entry uses startMapPoints.
            bool connected=moveIndex==0
                ?entry["available_actions"]!.AsArray().Any(a=>a!["kind"]?.GetValue<string>()=="map"
                    &&a["col"]!.GetValue<int>()==next.col&&a["row"]!.GetValue<int>()==next.row)
                :cursor is {} previous&&graph["edges"]!.AsArray().Any(e=>JsonNode.DeepEquals(e!["from"],NativeRouteGraph.Coord(previous))
                    &&JsonNode.DeepEquals(e["to"],NativeRouteGraph.Coord(next)));
            if(!connected)throw new ArgumentException("MAP_ROUTE_DISCONNECTED_MOVE");
            cursor=next;moveIndex++;
        }
        foreach(var target in targets)
        {
            if(target is not JsonObject shop||shop["act"]?.GetValue<int>()!=act)
                throw new ArgumentException("MAP_ROUTE_TARGET_ACT");
            var coord=NativeRouteGraph.ReadCoord(shop);
            if(!visited.Contains(NativeRouteGraph.Key(coord))||!graph["nodes"]!.AsArray().Any(n=>
                n!["col"]!.GetValue<int>()==coord.col&&n["row"]!.GetValue<int>()==coord.row&&n["type"]!.GetValue<string>()=="Shop")
                ||!mapRouteTargetKeys.Add(act+":"+NativeRouteGraph.Key(coord)))
                throw new ArgumentException("MAP_ROUTE_TARGET_NOT_KNOWN_SHOP");
        }
        mapRouteTopology=NativeRouteGraph.Topology(graph);
    }

    JsonObject? MapSourceNativeState()
    {
        var runtime=MapActionRuntime.Capture();
        if(runtime==null||CombatManager.Instance.IsInProgress||roomTasks.Any(t=>!t.IsCompleted)
            ||background.Any(t=>!t.IsCompleted))return null;
        // MapPoint quests are not represented by the native map save DTO. Read
        // their IDs in graph metadata but refuse an incomplete identity guardian.
        if(run.Map.GetAllMapPoints().Concat([run.Map.StartingMapPoint,run.Map.BossMapPoint])
            .Any(p=>p.Quests.Count!=0)||run.Map.SecondBossMapPoint?.Quests.Count>0)return null;
        return new JsonObject {
            ["run"]=new JsonObject { ["native_json"]=RoomCheckpoint.Canonical(RoomCheckpoint.CaptureRun()) },
            ["progress"]=NativeProgressGuard.Capture(),["action_runtime"]=runtime
        };
    }
    void CaptureMapDecisionSource(string name,object[] options)
    {
        if(!captureRouteGraph||cursor<history.Length||name is not("map" or "shop"))return;
        JsonObject? graph=null,nativeState=null;string? error=null;
        try { graph=NativeRouteGraph.Capture(run);nativeState=MapSourceNativeState();
            if(nativeState==null)error="MAP_SOURCE_NATIVE_STATE_UNSUPPORTED"; }
        catch(Exception failure) when(!WorkerMemoryTelemetry.IsOutOfMemory(failure)) { error=failure.GetType().Name+":"+failure.Message; }
        mapDecisionSources.Add(new JsonObject {
            ["schema"]=MapSourceSchema,["phase"]=name,["index"]=transcript.Count,
            ["entry_history"]=JsonSerializer.SerializeToNode(transcript,Program.Json),
            ["context"]=RoomCheckpoint.Context(request),["native_identity"]=JsonSerializer.SerializeToNode(Program.NativeIdentity()),
            ["entry_observation"]=JsonSerializer.SerializeToNode(Observe(),Program.Json),
            ["available_actions"]=JsonSerializer.SerializeToNode(options,Program.Json),["graph"]=graph,
            ["source_native_state"]=nativeState,["source_error"]=error,
            ["baseline_sha256"]=researchProgress?.BaselineSha,
            ["native_progress_raw"]=nativeState!=null?NativeProgressGuard.RawCapture():null
        });
    }
    void RequireMapGuard(JsonNode? expected,JsonNode? actual,string name)
    {
        mapRouteFailedGuard=name;
        if(expected==null||actual==null)throw RouteRejected("MAP_ROUTE_GUARD_REQUIRED:"+name);
        try { RoomCheckpoint.RequireEqual(expected,actual,name); }
        catch(InvalidDataException failure) { throw RouteRejected(failure.Message); }
        mapRouteFailedGuard=null;
    }
    InvalidDataException RouteRejected(string reason)
    { mapRouteFailureReason=reason;return new InvalidDataException(reason); }
    bool MapRouteComplete=>mapRoutePlan!=null&&mapRouteEntryChecked
        &&mapRouteMoveIndex==mapRoutePlan["moves"]!.AsArray().Count
        &&mapRouteArrivedShops.Count==mapRouteTargetKeys.Count&&mapRoutePendingMove==null;
    void MapRouteCheckDecision(string name,object[] options)
    {
        if(mapRoutePlan==null)return;
        if(!mapRouteEntryChecked)
        {
            if(name!="map")throw RouteRejected("MAP_ROUTE_ENTRY_PHASE:"+name);
            var entry=mapRoutePlan["source"]!.AsObject();
            RequireMapGuard(entry["entry_history"],JsonSerializer.SerializeToNode(transcript,Program.Json),"map_route_history");
            RequireMapGuard(entry["context"],RoomCheckpoint.Context(request),"map_route_context");
            RequireMapGuard(entry["native_identity"],JsonSerializer.SerializeToNode(Program.NativeIdentity()),"map_route_native_identity");
            RequireMapGuard(entry["baseline_sha256"],JsonValue.Create(researchProgress?.BaselineSha),"map_route_baseline");
            RequireMapGuard(entry["entry_observation"],JsonSerializer.SerializeToNode(Observe(),Program.Json),"map_route_observation");
            RequireMapGuard(entry["available_actions"],JsonSerializer.SerializeToNode(options,Program.Json),"map_route_legal_actions");
            RequireMapGuard(entry["graph"],NativeRouteGraph.Capture(run),"map_route_graph_entry");
            RequireMapGuard(entry["source_native_state"],MapSourceNativeState(),"map_route_native_state");
            mapRouteEntryChecked=true;
        }
        if(!MapRouteComplete&&(name is "map" or "shop"))
        {
            if(run.CurrentActIndex!=mapRouteTopology!["act"]!.GetValue<int>())throw RouteRejected("MAP_ROUTE_ACT_CHANGED");
            RequireMapGuard(mapRouteTopology,NativeRouteGraph.Topology(NativeRouteGraph.Capture(run)),"map_route_graph_changed");
        }
    }
    JsonNode MapRouteChooseMove(string name,object[] options,JsonNode original)
    {
        if(mapRoutePlan==null||MapRouteComplete)return original;
        string kind=original["kind"]!.GetValue<string>();
        if(kind is "next_act" or "finish_run")throw RouteRejected("MAP_ROUTE_LEFT_ACT_BEFORE_COMPLETION");
        if(kind!="map")return original; // shopping/events/potions retain their own policy
        if(name is not("map" or "shop")||mapRoutePendingMove!=null)throw RouteRejected("MAP_ROUTE_MOVE_STATE");
        var step=mapRoutePlan["moves"]!.AsArray()[mapRouteMoveIndex]!.AsObject();
        var coord=NativeRouteGraph.ReadCoord(step);
        var legal=options.Select(o=>JsonSerializer.SerializeToNode(o)!).SingleOrDefault(a=>
            a["kind"]!.GetValue<string>()=="map"&&a["col"]!.GetValue<int>()==coord.col&&a["row"]!.GetValue<int>()==coord.row);
        if(legal==null)throw RouteRejected("MAP_ROUTE_NEXT_MOVE_NOT_LEGAL");
        mapRoutePendingMove=step.DeepClone().AsObject();mapRoutePendingActionIndex=transcript.Count;
        return legal;
    }
    void MapRouteActionCompleted(JsonObject action)
    {
        if(mapRoutePendingMove==null)return;
        var move=mapRoutePendingMove;var expected=NativeRouteGraph.ReadCoord(move);
        if(action["kind"]?.GetValue<string>()!="map"||run.CurrentActIndex!=move["act"]!.GetValue<int>()
            ||run.CurrentMapCoord is not {} actual||actual.col!=expected.col||actual.row!=expected.row)
            throw RouteRejected("MAP_ROUTE_MOVE_NOT_REALIZED");
        RequireMapGuard(mapRouteTopology,NativeRouteGraph.Topology(NativeRouteGraph.Capture(run)),"map_route_graph_after_move");
        mapRouteExecuted.Add(new JsonObject { ["index"]=mapRoutePendingActionIndex,["act"]=run.CurrentActIndex,
            ["col"]=actual.col,["row"]=actual.row,["point_type"]=run.CurrentMapPoint?.PointType.ToString(),
            ["actual_room"]=run.CurrentRoom?.GetType().Name });
        string key=run.CurrentActIndex+":"+NativeRouteGraph.Key(actual);
        if(mapRouteTargetKeys.Contains(key))
        {
            if(run.CurrentRoom is not MerchantRoom)throw RouteRejected("MAP_ROUTE_KNOWN_SHOP_NOT_MERCHANT_ROOM");
            mapRouteArrivedShops.Add(new JsonObject { ["index"]=mapRoutePendingActionIndex,["act"]=run.CurrentActIndex,
                ["col"]=actual.col,["row"]=actual.row,["native_room"]="MerchantRoom" });
        }
        mapRouteMoveIndex++;mapRoutePendingMove=null;
    }
    void FinalizeMapRouteOutcome()
    {
        if(mapRoutePlan==null||MapRouteComplete)return;
        // A real route can die before reaching all targets. Keep that native
        // TERMINAL loss/value/terminal-combat snapshot; incompletion is metadata.
        // A budget, guard or mechanism failure must never be promoted to death.
        if(mapRouteEntryChecked&&!probe.HasValue&&!cardMenuProbe.HasValue
            &&player?.Creature.IsDead==true&&boundary==null
            &&mapRouteFailedGuard==null&&mapRouteFailureReason==null)
        {
            mapRouteFailureReason="MAP_ROUTE_DEATH_BEFORE_COMPLETION";
            return;
        }
        mapRouteFailureReason??=boundary??"MAP_ROUTE_UNFULFILLED";
        boundary??=mapRouteFailureReason;
    }
    object MapRouteResult()=>new {
        schema="spire-map-route-result/v1",requested=mapRoutePlan!=null,entry_checked=mapRouteEntryChecked,
        failed_guard=mapRouteFailedGuard,failure_reason=mapRouteFailureReason,
        planned_moves=mapRoutePlan?["moves"]?.AsArray().Count??0,executed_moves=mapRouteMoveIndex,
        complete=MapRouteComplete,executed=mapRouteExecuted,target_shops=mapRoutePlan?["target_shops"],
        arrived_shops=mapRouteArrivedShops,shop_policy=shopPreparationPolicy,
        scope="real native MAP actions; target shops require actual MerchantRoom; no room-outcome or optimality proof"
    };
}
