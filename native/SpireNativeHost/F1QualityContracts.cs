using System.Text.Json;
using System.Text.Json.Nodes;

namespace SpireNativeHost;

// Pure fixtures over the exact helpers used by BeamAdvisor. No solver, game
// actions, live state, replay or native win assertion is involved.
internal static class F1QualityContracts
{
    public static object Run()
    {
        var cases = new List<object>();
        void Require(string name,bool passed)
        {
            if(!passed)throw new InvalidDataException("F1_QUALITY_CONTRACT:"+name);
            cases.Add(new {name,passed=true});
        }
        foreach(string configured in new[]{"auto","best","first_win"})
        {
            string legacyF1=configured=="auto"?"best":configured;
            string legacyOther=configured=="auto"?"first_win":configured;
            Require("off_f1_"+configured,BeamAdvisor.ResolveSelection(configured,true,false)==legacyF1);
            Require("off_other_"+configured,BeamAdvisor.ResolveSelection(configured,false,false)==legacyOther);
            Require("on_f1_"+configured,BeamAdvisor.ResolveSelection(configured,true,true)=="best");
            Require("on_other_"+configured,BeamAdvisor.ResolveSelection(configured,false,true)==legacyOther);
        }
        var low=new BeamAdvisor.Forecast(true,true,12,0,null,4,0,1);
        var high=new BeamAdvisor.Forecast(true,true,38,2,null,7,0,1);
        var loss=new BeamAdvisor.Forecast(false,true,80,0,null,null,1,0);
        Require("winning_hp_precedes_potion_and_end_turn",high.BetterThan(low)&&!low.BetterThan(high));
        Require("win_precedes_surviving_loss",low.BetterThan(loss)&&!loss.BetterThan(low));
        Require("equal_forecast_keeps_earlier_member",!high.BetterThan(high));
        var sameHpLessPotion=high with {Potions=1};
        Require("existing_potion_tiebreak_retained",sameHpLessPotion.BetterThan(high));
        var exported=JsonSerializer.SerializeToNode(high.Describe(),Program.Json)!.AsObject();
        Require("explicit_projected_integer_hp",exported["hp"]!.GetValue<int>()==38);
        Require("explicit_forecast_win_and_survival",exported["won"]!.GetValue<bool>()&&exported["survives"]!.GetValue<bool>());
        Require("explicit_forecast_nullable_death_turn",exported.ContainsKey("death_turn")&&exported["death_turn"]==null);
        Require("forecast_roundtrip_without_fractional_fields",JsonNode.DeepEquals(exported,JsonNode.Parse(exported.ToJsonString())));
        return new {
            schema="spire-f1-quality-contract/v1",passed=true,cases,
            synthetic=true,native_terminal_observed=false,value=(object?)null,
            additional_searches=0,
            scope="pure selection and forecast DTO fixtures; no live combat, route deployment, whole-run win or performance evidence"
        };
    }
}
