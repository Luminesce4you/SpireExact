using System.Text.Json.Nodes;

namespace SpireNativeHost;

// Pure CLR fixtures invoke the production JSON topology projection. No run,
// map/game object, model, room, combat, replay or Godot bootstrap is created.
internal static class I080Contracts
{
    public static object Run()
    {
        var cases=new List<object>();
        var graph=Fixture();
        string original=graph.ToJsonString();

        void Expect(string name,Action<JsonObject> mutate,bool same)
        {
            var changed=graph.DeepClone().AsObject();
            mutate(changed);
            bool equal=JsonNode.DeepEquals(NativeRouteGraph.Topology(graph),NativeRouteGraph.Topology(changed));
            if(equal!=same)throw new InvalidDataException("I080_TOPOLOGY_CONTRACT_FAILED:"+name);
            if(graph.ToJsonString()!=original)
                throw new InvalidDataException("I080_TOPOLOGY_INPUT_MUTATED:"+name);
            cases.Add(new {name,passed=true,expected_same_topology=same});
        }

        Expect("current_coord_changed",g=>g["current_coord"]=Coord(3,0),true);
        Expect("eight_fur_coat_gameplay_quests_added",AddFurCoatQuests,true);
        Expect("current_coord_and_eight_fur_coat_quests_changed",g=> {
            g["current_coord"]=Coord(3,0);AddFurCoatQuests(g);
        },true);
        Expect("edge_target_changed",g=>g["edges"]![0]!["to"]!["col"]=2,false);
        Expect("edge_source_changed",g=>g["edges"]![0]!["from"]!["row"]=-1,false);
        Expect("edge_removed",g=>g["edges"]!.AsArray().RemoveAt(0),false);
        Expect("edge_added",g=>g["edges"]!.AsArray().Add(Edge(3,0,1,2)),false);
        Expect("node_column_changed",g=>g["nodes"]![1]!["col"]=2,false);
        Expect("node_row_changed",g=>g["nodes"]![1]!["row"]=2,false);
        Expect("node_type_changed",g=>g["nodes"]![1]!["type"]="Shop",false);
        Expect("node_modifiability_changed",g=>g["nodes"]![1]!["can_modify"]=true,false);
        Expect("starting_special_point_changed",g=>g["starting_coord"]!["col"]=2,false);
        Expect("boss_special_point_changed",g=>g["boss_coord"]!["col"]=2,false);
        Expect("second_boss_special_point_changed",g=>g["second_boss_coord"]!["col"]=2,false);
        Expect("second_boss_special_point_removed",g=>g["second_boss_coord"]=null,false);
        Expect("start_points_changed",g=>g["start_coords"]![0]!["col"]=2,false);
        Expect("width_changed",g=>g["width"]=8,false);
        Expect("height_changed",g=>g["height"]=15,false);
        Expect("act_changed",g=>g["act"]=1,false);
        Expect("schema_changed",g=>g["schema"]="unsupported",false);

        var output=NativeRouteGraph.Topology(graph);
        if(output.ContainsKey("current_coord")||output["nodes"]!.AsArray().Any(n=>n!.AsObject().ContainsKey("quests")))
            throw new InvalidDataException("I080_TOPOLOGY_DYNAMIC_FIELDS_RETAINED");
        output["nodes"]![0]!["type"]="mutated_output";
        output["edges"]![0]!["to"]!["col"]=99;
        if(graph.ToJsonString()!=original)
            throw new InvalidDataException("I080_TOPOLOGY_OUTPUT_ALIASES_INPUT");
        cases.Add(new {name="projection_does_not_mutate_or_alias_original_graph",passed=true});

        return new {
            schema="spire-i080-contracts/v1",passed=true,cases,
            synthetic=true,native_terminal_observed=false,value=(object?)null,
            game_initialized=false,proven_optimal=false,
            scope="pure CLR JSON fixtures calling production NativeRouteGraph.Topology; no game initialization or action; entry-state guards, route execution, death accounting and gameplay equivalence are not validated here"
        };
    }

    static void AddFurCoatQuests(JsonObject graph)
    {
        // The eight marked coordinates match the retained failure documents.
        for(int index=1;index<=8;index++)
            graph["nodes"]![index]!["quests"]=new JsonArray(JsonValue.Create("RELIC.FUR_COAT"));
    }
    static JsonObject Coord(int col,int row)=>new() { ["col"]=col,["row"]=row };
    static JsonObject Edge(int fromCol,int fromRow,int toCol,int toRow)=>new() {
        ["from"]=Coord(fromCol,fromRow),["to"]=Coord(toCol,toRow)
    };
    static JsonObject Node(int col,int row,string type,bool canModify)=>new() {
        ["col"]=col,["row"]=row,["type"]=type,["can_modify"]=canModify,["quests"]=new JsonArray()
    };
    static JsonObject Fixture()=>new() {
        ["schema"]=NativeRouteGraph.Schema,["act"]=2,["current_coord"]=null,
        ["nodes"]=new JsonArray(
            Node(3,0,"Ancient",true),
            Node(1,1,"Monster",false),Node(5,1,"Monster",false),
            Node(1,2,"Monster",true),Node(3,5,"Monster",true),
            Node(3,8,"Monster",true),Node(4,8,"Elite",true),
            Node(2,11,"Monster",true),Node(3,12,"Elite",true),
            Node(3,14,"Boss",false),Node(3,15,"Boss",false)),
        ["edges"]=new JsonArray(
            Edge(3,0,1,1),Edge(3,0,5,1),Edge(1,1,1,2),Edge(5,1,1,2),
            Edge(1,2,3,5),Edge(3,5,3,8),Edge(3,5,4,8),
            Edge(3,8,2,11),Edge(4,8,2,11),Edge(2,11,3,12),
            Edge(3,12,3,14),Edge(3,14,3,15)),
        ["starting_coord"]=Coord(3,0),["boss_coord"]=Coord(3,14),["second_boss_coord"]=Coord(3,15),
        ["start_coords"]=new JsonArray(Coord(1,1),Coord(5,1)),["width"]=7,["height"]=14
    };
}
