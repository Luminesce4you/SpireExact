using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Runs;

namespace SpireNativeHost;

// Read the actual current act. GetAllMapPoints covers only Grid, so include
// native special points explicitly. No hidden '?' room is classified as Shop.
internal static class NativeRouteGraph
{
    public const string Schema="spire-current-act-map/v1";
    internal static JsonObject Coord(MapCoord coord)=>new JsonObject { ["col"]=coord.col,["row"]=coord.row };
    internal static string Key(MapCoord coord)=>coord.col+","+coord.row;
    internal static MapCoord ReadCoord(JsonNode node)=>new(node["col"]!.GetValue<int>(),node["row"]!.GetValue<int>());
    public static JsonObject Capture(RunState run)
    {
        var map=run.Map;
        var seeds=map.GetAllMapPoints().Concat([map.StartingMapPoint,map.BossMapPoint]);
        if(map.SecondBossMapPoint is {} second)seeds=seeds.Append(second);
        var points=new Dictionary<string,MapPoint>(StringComparer.Ordinal);
        var queue=new Queue<MapPoint>(seeds);
        while(queue.TryDequeue(out var point))
        {
            string key=Key(point.coord);
            if(points.TryGetValue(key,out var previous))
            {
                if(RoomCheckpoint.Canonical(Node(previous))!=RoomCheckpoint.Canonical(Node(point))
                    ||RoomCheckpoint.Canonical(Children(previous))!=RoomCheckpoint.Canonical(Children(point)))
                    throw new InvalidDataException("ROUTE_GRAPH_DUPLICATE_COORD:"+key);
                continue;
            }
            if(map.GetPoint(point.coord)==null)throw new InvalidDataException("ROUTE_GRAPH_DANGLING_NODE:"+key);
            points.Add(key,point);
            foreach(var child in point.Children)queue.Enqueue(child);
        }
        var ordered=points.Values.OrderBy(p=>p.coord.row).ThenBy(p=>p.coord.col).ToArray();
        var edges=new JsonArray();
        foreach(var point in ordered)
            foreach(var child in point.Children.OrderBy(p=>p.coord.row).ThenBy(p=>p.coord.col))
                edges.Add(new JsonObject { ["from"]=Coord(point.coord),["to"]=Coord(child.coord) });
        return new JsonObject {
            ["schema"]=Schema,["act"]=run.CurrentActIndex,
            ["current_coord"]=run.CurrentMapCoord is {} current?Coord(current):null,
            ["nodes"]=new JsonArray(ordered.Select(p=>(JsonNode)Node(p)).ToArray()),["edges"]=edges,
            ["starting_coord"]=Coord(map.StartingMapPoint.coord),["boss_coord"]=Coord(map.BossMapPoint.coord),
            ["second_boss_coord"]=map.SecondBossMapPoint is {} boss?Coord(boss.coord):null,
            ["start_coords"]=new JsonArray(map.startMapPoints.OrderBy(p=>p.coord.row).ThenBy(p=>p.coord.col)
                .Select(p=>(JsonNode)Coord(p.coord)).ToArray()),
            ["width"]=map.GetColumnCount(),["height"]=map.GetRowCount()
        };
    }
    static JsonObject Node(MapPoint point)=>new JsonObject {
        ["col"]=point.coord.col,["row"]=point.coord.row,["type"]=point.PointType.ToString(),
        ["can_modify"]=point.CanBeModified,
        ["quests"]=new JsonArray(point.Quests.Select(q=>(JsonNode?)JsonValue.Create(q.Id.ToString())).ToArray())
    };
    static JsonArray Children(MapPoint point)=>new(point.Children.OrderBy(p=>p.coord.row).ThenBy(p=>p.coord.col)
        .Select(p=>(JsonNode)Coord(p.coord)).ToArray());
    public static JsonObject Topology(JsonObject graph)
    {
        // FurCoat legitimately adds gameplay quest markers after obtaining the
        // relic in the Ancient room. Those markers do not change route links.
        // Keep them in Capture and the complete entry-state guardian; exclude
        // them only from the later travel comparison, never from native rules.
        var result=graph.DeepClone().AsObject();result.Remove("current_coord");
        if(result["nodes"] is JsonArray nodes)
            foreach(var node in nodes)
                if(node is JsonObject point)point.Remove("quests");
        return result;
    }
}
