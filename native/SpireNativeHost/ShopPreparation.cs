using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Hooks;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Saves;

namespace SpireNativeHost;

// Local to genuinely reached target shops. One legal removal is permanent;
// the opt-in mode additionally prioritizes potions and positive-value relics.
// This does not alter any
// price, inventory, card removal effect, potion rule or non-target policy.
internal sealed partial class CampaignReplay
{
    string? shopPreparationPolicy;
    readonly List<JsonObject> shopInventorySources=[];
    readonly List<JsonObject> shopPurchaseEvents=[];
    readonly HashSet<string> preparationRemovalsCompleted=new(StringComparer.Ordinal);
    bool preparationRemovalInFlight;
    const string ShopPreparationSchema="potions_remove_relic/v1";
    const string ShopRemovalOnlySchema="removal-only/v1";
    bool CaptureShopMetadata=>captureRouteGraph||captureResourceTelemetry||shopPreparationPolicy!=null;
    string CurrentShopKey=>run.CurrentActIndex+":"+(run.CurrentMapCoord is {} coord?NativeRouteGraph.Key(coord):"null");
    bool IsPreparationShop=>shopPreparationPolicy!=null&&mapRouteEntryChecked&&run.CurrentRoom is MerchantRoom
        &&mapRouteTargetKeys.Contains(CurrentShopKey)
        &&mapRouteArrivedShops.Any(s=>s["act"]!.GetValue<int>()==run.CurrentActIndex
            &&s["col"]!.GetValue<int>()==run.CurrentMapCoord!.Value.col&&s["row"]!.GetValue<int>()==run.CurrentMapCoord.Value.row);
    void ShopPreparationInit(JsonElement source)
    {
        // The policy belongs to the guarded route plan, as emitted by the
        // planner. Reading a top-level field silently left target shops idle.
        if(mapRoutePlan==null||mapRouteTargetKeys.Count==0)
        {
            if(mapRoutePlan?["shop_policy"]!=null)throw new ArgumentException("SHOP_PREPARATION_REQUIRES_TARGET_SHOPS");
            return;
        }
        // Omission/null cannot disable the user's mandatory target-shop removal.
        var policyNode=mapRoutePlan["shop_policy"];
        if(policyNode!=null&&policyNode is not JsonValue)
            throw new ArgumentException("SHOP_PREPARATION_INVALID_POLICY");
        shopPreparationPolicy=policyNode is JsonValue policy
            ?policy.GetValue<string>():ShopRemovalOnlySchema;
        if(shopPreparationPolicy is not(ShopPreparationSchema or ShopRemovalOnlySchema))
            throw new ArgumentException("SHOP_PREPARATION_REQUIRES_TARGET_SHOPS");
    }
    JsonArray PotionSlotsMetadata()=>new(player.PotionSlots.Select((p,index)=>(JsonNode)new JsonObject {
        ["slot"]=index,["id"]=p?.Id.Entry,["queued"]=p?.IsQueued??false
    }).ToArray());
    JsonObject ShopEntryMetadata(MerchantEntry entry,int index,JsonNode[] legal)
    {
        string semantic="unknown";string? id=null,native=null;int? upgrade=null;
        switch(entry)
        {
            case MerchantCardEntry cardEntry:
                semantic="card";
                if(cardEntry.CreationResult is {} card) {
                    id=card.Card.Id.Entry;upgrade=card.Card.CurrentUpgradeLevel;
                    native=JsonSerializer.Serialize(card.Card.ToSerializable(),JsonSerializationUtility.Options);
                }
                break;
            case MerchantRelicEntry relic:
                semantic="relic";id=relic.Model?.Id.Entry;
                native=relic.Model!=null?JsonSerializer.Serialize(relic.Model.ToSerializable(),JsonSerializationUtility.Options):null;break;
            case MerchantPotionEntry potion:
                semantic="potion";id=potion.Model?.Id.Entry;
                // Shop stock has no player-owned potion slot. Preserve native
                // potion identity with the explicit unowned slot sentinel.
                native=potion.Model!=null?JsonSerializer.Serialize(potion.Model.ToSerializable(-1),JsonSerializationUtility.Options):null;break;
            case MerchantCardRemovalEntry:semantic="removal";break;
        }
        bool stocked=entry.IsStocked;int? cost=stocked?entry.Cost:null;
        return new JsonObject {
            ["index"]=index,["item_type"]=entry.GetType().Name,["semantic"]=semantic,["id"]=id,["upgrade"]=upgrade,
            ["model_native_json"]=native,["cost"]=cost,["stocked"]=stocked,
            ["affordable"]=cost.HasValue&&cost.Value<=player.Gold,
            ["legal"]=legal.Any(a=>a["kind"]?.GetValue<string>()=="buy"&&a["index"]!.GetValue<int>()==index)
        };
    }
    void CaptureShopInventory(string name,object[] options)
    {
        if(!CaptureShopMetadata||cursor<history.Length||name!="shop"||run.CurrentRoom is not MerchantRoom shop)return;
        var legal=options.Select(o=>JsonSerializer.SerializeToNode(o)!).ToArray();
        shopInventorySources.Add(new JsonObject {
            ["index"]=transcript.Count,["act"]=run.CurrentActIndex,["floor"]=run.TotalFloor,
            ["coord"]=run.CurrentMapCoord is {} coord?NativeRouteGraph.Coord(coord):null,["gold"]=player.Gold,
            ["potion_slots"]=PotionSlotsMetadata(),["empty_potion_slots"]=player.PotionSlots.Count(p=>p==null),
            ["entries"]=new JsonArray(shop.GetLocalInventory().AllEntries.Select((entry,index)=>(JsonNode)ShopEntryMetadata(entry,index,legal)).ToArray()),
            ["removable_cards"]=new JsonArray(player.Deck.Cards.Select((card,index)=>(card,index))
                .Where(x=>x.card.IsRemovable).Select(x=>(JsonNode)new JsonObject {
                    ["deck_index"]=x.index,["id"]=x.card.Id.Entry,["upgrade"]=x.card.CurrentUpgradeLevel,
                    ["model_native_json"]=JsonSerializer.Serialize(x.card.ToSerializable(),JsonSerializationUtility.Options)
                }).ToArray())
        });
    }
    bool PreparationPotionAllowed(MerchantPotionEntry entry)=>entry.Model is {} potion&&player.PotionSlots.Any(p=>p==null)
        &&Hook.ShouldProcurePotion(run,player.Creature.CombatState,potion,player);
    JsonNode? ShopPreparationChoice(string name,object[] options)
    {
        if(!IsPreparationShop)return null;
        var legal=options.Select(o=>JsonSerializer.SerializeToNode(o)!).ToArray();
        var strategic=new StrategicStateEvaluator(player,run);
        if(name=="select_cards"&&preparationRemovalInFlight&&selectionRequest?.Purpose==CardSelectionPurpose.Remove)
        {
            int Category(CardModel c)=>c.Type==CardType.Curse?3:c.Id.Entry=="STRIKE_IRONCLAD"?2:c.Id.Entry=="DEFEND_IRONCLAD"?1:0;
            var candidates=legal.Where(a=>a["kind"]?.GetValue<string>()=="select_cards"&&a["indices"] is JsonArray {Count:1})
                .Select(a=>(action:a,index:a["indices"]![0]!.GetValue<int>()))
                .Where(x=>x.index>=0&&x.index<selectionCards.Length&&selectionCards[x.index].IsRemovable)
                .OrderByDescending(x=>Category(selectionCards[x.index]))
                .ThenByDescending(x=>strategic.SelectionValue(selectionCards[x.index],CardSelectionPurpose.Remove)).ToArray();
            if(candidates.Length==0)throw RouteRejected("SHOP_PREPARATION_REMOVAL_NOT_LEGAL");
            return candidates[0].action;
        }
        if(name!="shop")return null;
        var inventory=((MerchantRoom)run.CurrentRoom!).GetLocalInventory();var entries=inventory.AllEntries.ToArray();
        var buys=legal.Where(a=>a["kind"]?.GetValue<string>()=="buy")
            .Select(a=>(action:a,index:a["index"]!.GetValue<int>()))
            .Where(x=>x.index>=0&&x.index<entries.Length&&entries[x.index].IsStocked&&entries[x.index].EnoughGold).ToArray();
        JsonNode? Best(IEnumerable<(JsonNode action,int index)> items)=>items.OrderByDescending(x=>strategic.ShopValue(entries[x.index]))
            .ThenBy(x=>x.index).Select(x=>x.action).FirstOrDefault();
        if(shopPreparationPolicy==ShopPreparationSchema)
        {
            var fill=Best(buys.Where(x=>entries[x.index] is MerchantPotionEntry potion&&PreparationPotionAllowed(potion)));
            if(fill!=null)return fill;
        }
        if(!preparationRemovalsCompleted.Contains(CurrentShopKey)&&player.Deck.Cards.Any(c=>c.IsRemovable))
        {
            var remove=buys.FirstOrDefault(x=>entries[x.index] is MerchantCardRemovalEntry);
            if(remove.action!=null)return remove.action;
        }
        if(shopPreparationPolicy==ShopPreparationSchema)
        {
            var relic=Best(buys.Where(x=>entries[x.index] is MerchantRelicEntry&&strategic.ShopValue(entries[x.index])>0));
            if(relic!=null)return relic;
        }
        // Resume the existing strategy, with only infeasible potion purchases
        // and the explicitly completed one-removal stage excluded locally.
        var remaining=options.Where(o=> {
            var action=JsonSerializer.SerializeToNode(o)!;
            if(action["kind"]?.GetValue<string>()!="buy")return true;
            var entry=entries[action["index"]!.GetValue<int>()];
            return entry switch {
                MerchantPotionEntry potion=>PreparationPotionAllowed(potion),
                MerchantCardRemovalEntry=>!preparationRemovalsCompleted.Contains(CurrentShopKey)&&player.Deck.Cards.Any(c=>c.IsRemovable),
                _=>true
            };
        }).ToArray();
        if(remaining.Length==0)throw RouteRejected("SHOP_PREPARATION_NO_LEGAL_CONTINUATION");
        return RankCandidate(name,remaining);
    }
    async Task PurchaseWithMetadata(MerchantEntry entry,int index,MerchantInventory inventory)
    {
        int actionIndex=transcript.Count-1,goldBefore=player.Gold,removalsBefore=player.ExtraFields.CardShopRemovalsUsed;
        int actBefore=run.CurrentActIndex,floorBefore=run.TotalFloor;var coordBefore=run.CurrentMapCoord;
        string shopKeyBefore=CurrentShopKey;
        var slotsBefore=PotionSlotsMetadata();bool prepared=IsPreparationShop;
        var item=ShopEntryMetadata(entry,index,[JsonSerializer.SerializeToNode(new {kind="buy",index})!]);
        bool success=false;
        preparationRemovalInFlight=prepared&&entry is MerchantCardRemovalEntry;
        try { success=await entry.OnTryPurchaseWrapper(inventory); }
        finally { preparationRemovalInFlight=false; }
        shopPurchaseEvents.Add(new JsonObject {
            ["index"]=actionIndex,["act"]=actBefore,["floor"]=floorBefore,
            ["coord"]=coordBefore is {} coord?NativeRouteGraph.Coord(coord):null,["item"]=item,
            ["purchase_succeeded"]=success,["gold_before"]=goldBefore,["gold_after"]=player.Gold,
            ["potion_slots_before"]=slotsBefore,["potion_slots_after"]=PotionSlotsMetadata(),
            ["removal_count_before"]=removalsBefore,["removal_count_after"]=player.ExtraFields.CardShopRemovalsUsed,
            ["shop_policy"]=prepared?shopPreparationPolicy:null
        });
        if(prepared&&entry is MerchantCardRemovalEntry&&success)preparationRemovalsCompleted.Add(shopKeyBefore);
        if(prepared&&!success)throw RouteRejected("SHOP_PREPARATION_NATIVE_PURCHASE_REJECTED");
    }
}
