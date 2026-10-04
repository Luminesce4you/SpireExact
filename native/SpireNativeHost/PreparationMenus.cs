using System.Text.Json;
using System.Text.Json.Nodes;

namespace SpireNativeHost;

// Optional result metadata only. The exact mandatory observation/evidence stays
// unchanged, including fresh independent winning replays without this flag.
internal sealed partial class CampaignReplay
{
    bool capturePreparationMenus;
    readonly List<JsonObject> preparationMenuSources=[];
    bool CapturePreparationMenus=>capturePreparationMenus;

    void PreparationMenusInit(JsonElement source)
    {
        capturePreparationMenus=source.TryGetProperty("capture_preparation_menus",out var capture)&&capture.GetBoolean();
    }

    void CapturePreparationMenuEvidence(string name,object[] options,object evidence)
    {
        if(!capturePreparationMenus||name!="rest"||run.CurrentActIndex!=2
            ||run.CurrentMapCoord is not {} coord||transcript.Count==0)return;
        bool smith=options.Any(option=> {
            var action=JsonSerializer.SerializeToNode(option);
            return action?["kind"]?.GetValue<string>()=="rest"
                &&action["option"]?.GetValue<string>()?.Contains("SMITH",StringComparison.OrdinalIgnoreCase)==true;
        });
        if(!smith)return;
        var observation=JsonSerializer.SerializeToNode(evidence)?["observation"]?.DeepClone();
        if(observation is not JsonObject)return;
        preparationMenuSources.Add(new JsonObject {
            ["index"]=transcript.Count-1,["phase"]="rest",
            ["map_coord"]=NativeRouteGraph.Coord(coord),["observation"]=observation
        });
    }

    object PreparationMenuSourcesResult()=>preparationMenuSources;
}
