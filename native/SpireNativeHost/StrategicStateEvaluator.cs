using System.Reflection;
using System.Reflection.Emit;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;

namespace SpireNativeHost;

// Heuristic features only. Never an action-legality oracle, state key or bound.
// Values come from the current native models and their effect command metadata.
internal static class NativeEffectMetadata
{
    static readonly Dictionary<Type,HashSet<string>> cache=[];
    static readonly Dictionary<Type,HashSet<string>> directCache=[];
    static readonly Dictionary<short,OpCode> opcodes=typeof(OpCodes).GetFields(BindingFlags.Static|BindingFlags.Public)
        .Where(f=>f.FieldType==typeof(OpCode)).Select(f=>(OpCode)f.GetValue(null)!).ToDictionary(o=>o.Value);
    public static HashSet<string> For(Type type)
    {
        if(cache.TryGetValue(type,out var value))return value;
        value=[];cache[type]=value;
        var queue=new Queue<Type>();var seen=new HashSet<Type>{type};queue.Enqueue(type);
        while(queue.TryDequeue(out var current)) {
            var references=new HashSet<Type>();
            foreach(var method in EffectMethods(current))Inspect(method,value,new HashSet<MethodBase>(),2,references);
            foreach(var referenced in references)if(seen.Add(referenced))queue.Enqueue(referenced);
        }
        return value;
    }
    public static HashSet<string> DirectFor(Type type)
    {
        if(directCache.TryGetValue(type,out var value))return value;
        value=[];directCache[type]=value;
        foreach(var method in EffectMethods(type))Inspect(method,value,new HashSet<MethodBase>(),2);
        return value;
    }
    static IEnumerable<MethodInfo> EffectMethods(Type type)=>type.GetMethods(BindingFlags.Instance|BindingFlags.Static|BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.DeclaredOnly)
            .Where(m=>m.Name=="OnPlay" || m.Name.StartsWith("OnUse") || m.Name.StartsWith("After") || m.Name.StartsWith("Before") || m.Name.StartsWith("Modify"));
    public static HashSet<string> For(MethodInfo method)
    {var value=new HashSet<string>();Inspect(method,value,new HashSet<MethodBase>(),2);return value;}
    static void Inspect(MethodInfo method,HashSet<string> output,HashSet<MethodBase> visited,int depth,HashSet<Type>? linkedPowers=null)
    {
        if(!visited.Add(method))return;
        output.Add(method.Name);
        var state=method.GetCustomAttribute<AsyncStateMachineAttribute>()?.StateMachineType;
        if(state!=null) {var move=state.GetMethod("MoveNext",BindingFlags.Instance|BindingFlags.NonPublic|BindingFlags.Public);if(move!=null)Inspect(move,output,visited,depth,linkedPowers);}
        byte[]? il=method.GetMethodBody()?.GetILAsByteArray();if(il==null)return;
        for(int i=0;i<il.Length;)
        {
            short code=il[i++];if(code==0xfe)code=(short)(0xfe00|il[i++]);
            if(!opcodes.TryGetValue(code,out var op))return;
            int size=op.OperandType switch {
                OperandType.InlineNone=>0,OperandType.ShortInlineBrTarget or OperandType.ShortInlineI or OperandType.ShortInlineVar=>1,
                OperandType.InlineVar=>2,OperandType.InlineI8 or OperandType.InlineR=>8,
                OperandType.InlineSwitch=>4+4*BitConverter.ToInt32(il,i),_=>4};
            if(op.OperandType==OperandType.InlineMethod)
            {
                try {
                    var called=method.Module.ResolveMethod(BitConverter.ToInt32(il,i),method.DeclaringType?.GetGenericArguments(),method.GetGenericArguments());
                    if(called!=null) {
                        output.Add(called.DeclaringType?.Name+"."+called.Name);
                        if(called is MethodInfo mi) {
                            foreach(var type in mi.GetGenericArguments()) {
                                output.Add(type.Name);
                                if(typeof(PowerModel).IsAssignableFrom(type))linkedPowers?.Add(type);
                            }
                            if(depth>0 && mi.DeclaringType==method.DeclaringType)Inspect(mi,output,visited,depth-1,linkedPowers);
                        }
                    }
                } catch(ArgumentException) {output.Add("unresolved_metadata");}
            }
            i+=size;
        }
    }
}

internal sealed record CardCapability(double Damage,double Block,double Draw,double Energy,double Strength,
    double Scaling,double Weak,double Vulnerable,double Aoe,double Exhaust,double ExhaustPayoff,double HpCost,
    double Cost,bool Burden,bool Power,bool SelfExhaust);

internal sealed class StrategicStateEvaluator
{
    readonly Player player;
    readonly RunState run;
    readonly CardCapability[] deckFeatures;
    readonly Dictionary<CardModel,double> values=[];
    public StrategicStateEvaluator(Player player,RunState run) {this.player=player;this.run=run;deckFeatures=player.Deck.Cards.Select(Describe).ToArray();}
    static double Variable(CardModel c,params string[] names)=>c.DynamicVars.Where(p=>names.Contains(p.Key,StringComparer.OrdinalIgnoreCase))
        .Sum(p=>Math.Max(0,(double)p.Value.BaseValue));
    static double Number(object? o,string property,double fallback=0)
    {if(o==null)return fallback;try{return Convert.ToDouble(o.GetType().GetProperty(property,BindingFlags.Instance|BindingFlags.NonPublic|BindingFlags.Public)?.GetValue(o)??fallback);}catch{return fallback;}}
    public static CardCapability Describe(CardModel c)
    {
        var effects=NativeEffectMetadata.For(c.GetType());bool Has(string s)=>effects.Any(v=>v.Contains(s,StringComparison.OrdinalIgnoreCase));
        var direct=NativeEffectMetadata.DirectFor(c.GetType());bool Direct(string s)=>direct.Any(v=>v.Contains(s,StringComparison.OrdinalIgnoreCase));
        double damage=Variable(c,"Damage");double hits=Variable(c,"Repeat","Hits","HitCount");if(hits>1)damage*=Math.Min(hits,5);
        double strength=Variable(c,"StrengthPower");
        double scaling=c.Type==CardType.Power ? 1 : 0;
        if(Has("ModifyDamage"))scaling+=.5;
        // Base includes native upgrades; global combat cost hooks are not run by
        // this long-term evaluator. X-cost uses a two-energy proxy, never legality.
        double cost=c.EnergyCost.CostsX?2:c.EnergyCost.GetWithModifiers((CostModifiers)0);
        return new(damage,Variable(c,"Block"),Direct("CardPileCmd.Draw")?Math.Max(1,Variable(c,"Cards","Draw")):0,
            Direct("PlayerCmd.GainEnergy")?Math.Max(1,Variable(c,"Energy")):0,strength,scaling,
            Variable(c,"WeakPower"),Variable(c,"VulnerablePower"),c.TargetType.ToString().Contains("AllEnemies")?damage:0,
            Direct("CardCmd.Exhaust")||c.Keywords.Contains(CardKeyword.Exhaust)?1:0,Has("AfterCardExhausted")?1:0,
            Variable(c,"HpLoss","SelfDamage"),Math.Max(0,cost),c.Type is CardType.Curse or CardType.Status,c.Type==CardType.Power,c.Keywords.Contains(CardKeyword.Exhaust));
    }
    CardCapability[] Features()=>deckFeatures;
    public object Snapshot()
    {
        var f=Features();double n=Math.Max(1,f.Length);
        static string N(double value)=>value.ToString("R",System.Globalization.CultureInfo.InvariantCulture);
        return new {schema="strategic-capability/v1",deck_size=f.Length,
            attack_density=N(f.Count(x=>x.Damage>0)/n),defense_density=N(f.Count(x=>x.Block>0)/n),
            damage_per_draw=N(f.Sum(x=>x.Damage)/n),block_per_draw=N(f.Sum(x=>x.Block)/n),
            draw_per_card=N(f.Sum(x=>x.Draw)/n),energy_per_card=N(f.Sum(x=>x.Energy)/n),
            mean_energy_cost=N(f.Sum(x=>x.Cost)/n),strength=N(f.Sum(x=>x.Strength)),scaling=N(f.Sum(x=>x.Scaling)),
            aoe=N(f.Sum(x=>x.Aoe)),weak=N(f.Sum(x=>x.Weak)),vulnerable=N(f.Sum(x=>x.Vulnerable)),
            exhaust=N(f.Sum(x=>x.Exhaust)),exhaust_payoff=N(f.Sum(x=>x.ExhaustPayoff)),burden=f.Count(x=>x.Burden),
            self_exhaust=f.Count(x=>x.SelfExhaust),
            steady_block_per_draw=N(f.Where(x=>!x.SelfExhaust&&!x.Power).Sum(x=>x.Block)/Math.Max(1,f.Count(x=>!x.SelfExhaust&&!x.Power))),
            deck_potential=N(DeckPotential(f)),
            upgrade_density=N(player.Deck.Cards.Count(c=>c.IsUpgraded)/n),
            score_is_heuristic=true};
    }
    public double CardValue(CardModel card)
    {
        if(values.TryGetValue(card,out double saved))return saved;
        var all=Features();var c=Describe(card);double n=Math.Max(1,all.Length);
        if(c.Burden)return -18;
        double attacks=all.Count(x=>x.Damage>0)/n,defends=all.Count(x=>x.Block>0)/n;
        double energy=all.Sum(x=>x.Energy)/n,draw=all.Sum(x=>x.Draw)/n;
        double damageWeight=1+Math.Max(0,.4-attacks)*2;
        double blockWeight=.9+Math.Max(0,.3-defends)*2+run.CurrentActIndex*.08;
        double repeated=all.Sum(x=>x.Strength)>0 ? 1.25 : 1;
        double immediate=(c.Damage*damageWeight+c.Block*blockWeight)/(1+Math.Max(0,c.Cost-1-energy)*.48);
        double drawValue=c.Draw*(4+Math.Min(2,energy*5));
        double energyValue=c.Energy*(4+Math.Min(4,draw*4));
        double scaleValue=c.Strength*(all.Sum(x=>x.Strength)<4?5:3)+c.Scaling*(run.CurrentActIndex+3);
        double control=c.Weak*(all.Sum(x=>x.Weak)<3?2.2:1)+c.Vulnerable*(all.Sum(x=>x.Vulnerable)<3?2.5:1.2);
        double coverage=c.Aoe>0?(all.Sum(x=>x.Aoe)<15?5:2):0;
        double synergy=c.Exhaust*(1+all.Sum(x=>x.ExhaustPayoff)*2)+c.ExhaustPayoff*(2+all.Sum(x=>x.Exhaust)*2);
        double result=immediate*repeated+drawValue+energyValue+scaleValue+control+coverage+synergy-c.HpCost*1.2;
        values[card]=result;return result;
    }
    // Cheap, explicit whole-deck proxy. It never executes game actions or
    // claims native combat predictions. Separate opening cards from a later
    // cycle so one-use defense is not mistaken for repeatable defense.
    static (double Damage,double Block,double Plays,double EmptyDefense) Cycle(CardCapability[] f,double strength)
    {
        double n=Math.Max(1,f.Length);
        double draws=5/(1-Math.Min(.55,f.Sum(c=>c.Draw)/n));
        double energy=3+draws*f.Sum(c=>c.Energy)/n;
        double plays=Math.Min(draws,energy/Math.Max(.5,f.Sum(c=>c.Cost)/n));
        double damage=plays*(f.Sum(c=>c.Damage)+strength*f.Count(c=>c.Damage>0))/n;
        double block=plays*f.Sum(c=>c.Block)/n;
        double noDefense=1;
        int blanks=f.Count(c=>c.Block<=0);
        for(int i=0;i<Math.Min(5,f.Length);i++)noDefense*=Math.Max(0,blanks-i)/(n-i);
        return (damage,block,plays,noDefense);
    }
    double DeckPotential(CardCapability[] f)
    {
        if(f.Length==0)return -100;
        var steady=f.Where(c=>!c.SelfExhaust&&!c.Power).ToArray();
        double strength=f.Sum(c=>c.Strength);
        var opening=Cycle(f,0);var sustained=Cycle(steady,strength);
        double damageNeed=12+run.CurrentActIndex*8;
        double blockNeed=8+run.CurrentActIndex*5;
        double Capability(double damage,double block)=>
            18*Math.Log(1+damage/damageNeed)+18*Math.Log(1+block/blockNeed);
        double openingValue=Capability(opening.Damage,opening.Block);
        double steadyValue=Capability(sustained.Damage,sustained.Block);
        double access=Math.Min(1,15d/f.Length);
        double control=access*(Math.Min(4,f.Sum(c=>c.Weak))+Math.Min(4,f.Sum(c=>c.Vulnerable)));
        double scaling=access*Math.Log(1+f.Sum(c=>c.Scaling))*(2+run.CurrentActIndex);
        double synergy=access*Math.Log(1+f.Sum(c=>c.Exhaust)*f.Sum(c=>c.ExhaustPayoff))*2;
        double hpCost=f.Sum(c=>c.HpCost)/f.Length;
        return .4*openingValue+.6*steadyValue+control+scaling+synergy
            -3*(opening.EmptyDefense+sustained.EmptyDefense)-hpCost
            -15d*f.Count(c=>c.Burden)/f.Length;
    }
    public double MarginalCard(CardModel card)
    {
        var deck=player.Deck.Cards;double mean=deck.Count>0?deck.Average(CardValue):0;
        int copies=deck.Count(c=>c.Id==card.Id);
        double redundancy=copies*Math.Max(0,CardValue(card)-mean)*.25;
        double slotTax=Math.Max(0,deck.Count-17)*.32;
        return CardValue(card)-mean-slotTax-redundancy;
    }
    public double SelectionSetValue(IEnumerable<CardModel> cards,CardSelectionPurpose purpose)
    {
        var selected=cards.ToArray();
        if(purpose is CardSelectionPurpose.Remove or CardSelectionPurpose.Transform)
        {
            var remaining=player.Deck.Cards.Where(c=>!selected.Contains(c)).Select(Describe).ToArray();
            // Transform outcome is uncertain; removal capability is a proposal
            // proxy only. Native full rollout resolves the actual generated card.
            return 4*(DeckPotential(remaining)-DeckPotential(deckFeatures));
        }
        return selected.Sum(c=>SelectionValue(c,purpose));
    }
    public double UpgradeGain(CardModel card)
    {
        if(!card.IsUpgradable)return -100;
        // Unowned canonical clone: native OnUpgrade modifies only this preview;
        // no live card mutation, RNG, hooks, piles or resources are executed.
        var before=ModelDb.GetById<CardModel>(card.Id).ToMutable();
        for(int i=0;i<card.CurrentUpgradeLevel;i++){before.UpgradeInternal();before.FinalizeUpgradeInternal();}
        double value=CardValue(before);before.UpgradeInternal();before.FinalizeUpgradeInternal();values.Remove(before);
        return CardValue(before)-value;
    }
    public double SelectionValue(CardModel card,CardSelectionPurpose purpose)
    {
        double mean=player.Deck.Cards.Count>0?player.Deck.Cards.Average(CardValue):0;
        return purpose switch {
            CardSelectionPurpose.Upgrade=>UpgradeGain(card),
            CardSelectionPurpose.Remove or CardSelectionPurpose.Transform=>SelectionSetValue([card],purpose),
            CardSelectionPurpose.Exhaust=>mean-CardValue(card)+(Describe(card).Burden?10:0),
            CardSelectionPurpose.Discard=>mean-CardValue(card),
            CardSelectionPurpose.Duplicate or CardSelectionPurpose.Obtain=>MarginalCard(card),
            _=>CardValue(card)};
    }
    static Dictionary<string,double> ModelVariables(AbstractModel model)
    {
        var data=model.GetType().GetProperty("DynamicVars")?.GetValue(model) as System.Collections.IEnumerable;
        var values=new Dictionary<string,double>(StringComparer.OrdinalIgnoreCase);
        if(data!=null)foreach(object pair in data) {
            string? key=pair.GetType().GetProperty("Key")?.GetValue(pair)?.ToString();
            object? value=pair.GetType().GetProperty("Value")?.GetValue(pair);
            if(key!=null)values[key]=Number(value,"BaseValue");
        }
        return values;
    }
    double RelicValue(RelicModel relic)
    {
        var effects=NativeEffectMetadata.For(relic.GetType());var vars=ModelVariables(relic);
        double total=6;
        foreach(var (name,value) in vars) {
            string key=name.ToUpperInvariant();double amount=Math.Clamp(value,0,20);
            if(key.Contains("ENERGY"))total+=amount*6;
            else if(key.Contains("STRENGTH"))total+=amount*4;
            else if(key.Contains("BLOCK"))total+=amount*.6;
            else if(key.Contains("HEAL"))total+=amount*.8;
            else if(key.Contains("CARDS"))total+=amount*3;
        }
        if(effects.Any(s=>s.Contains("AfterCardExhausted")))total+=2+deckFeatures.Sum(c=>c.Exhaust)*2;
        if(effects.Any(s=>s.Contains("AfterCardPlayed")))total+=Math.Min(4,deckFeatures.Count(c=>c.Cost<=1)*.3);
        if(effects.Any(s=>s.Contains("GainEnergy")))total+=5;
        if(effects.Any(s=>s.Contains("CardPileCmd.Draw")))total+=3;
        return total;
    }
    double PotionValue(PotionModel potion)
    {
        if(player.PotionSlots.All(p=>p!=null))return -100;
        var vars=ModelVariables(potion);var effects=NativeEffectMetadata.For(potion.GetType());
        double value=4;
        foreach(var (key,amount) in vars) {
            if(key.Contains("Damage",StringComparison.OrdinalIgnoreCase))value+=Math.Clamp(amount,0,60)*.15;
            if(key.Contains("Block",StringComparison.OrdinalIgnoreCase))value+=Math.Clamp(amount,0,60)*.15;
            if(key.Contains("Heal",StringComparison.OrdinalIgnoreCase))value+=Math.Min((double)(player.Creature.MaxHp-player.Creature.CurrentHp),Math.Max(0,amount))*.4;
            if(key.Contains("Strength",StringComparison.OrdinalIgnoreCase))value+=Math.Max(0,amount)*1.5;
        }
        if(effects.Any(s=>s.Contains("GainEnergy")))value+=2;
        return value;
    }
    public double ShopValue(MerchantEntry item)
    {
        double value=item switch {
            MerchantCardEntry c when c.CreationResult!=null=>Math.Max(-10,MarginalCard(c.CreationResult.Card))*1.4,
            MerchantRelicEntry r when r.Model!=null=>RelicValue(r.Model),
            MerchantPotionEntry p when p.Model!=null=>PotionValue(p.Model),
            MerchantCardRemovalEntry=>player.Deck.Cards.Where(c=>c.IsRemovable)
                .Select(c=>SelectionValue(c,CardSelectionPurpose.Remove)).DefaultIfEmpty(-100).Max(),
            _=>-100};
        // Resource price changes smoothly with the cash left after this purchase.
        double shadowPrice=.018+.02*Math.Clamp((150d-(player.Gold-item.Cost))/150d,0,1);
        return value-item.Cost*shadowPrice;
    }
    public double EventValue(EventOption option,EventModel model)
    {
        if(option.WillKillPlayer?.Invoke(player)==true)return -1000;
        var callback=typeof(EventOption).GetProperty("OnChosen",BindingFlags.Instance|BindingFlags.NonPublic)?.GetValue(option) as Delegate;
        if(callback==null)return option.IsProceed?0:-100;
        var effects=NativeEffectMetadata.For(callback.Method);var vars=ModelVariables(model);
        bool Has(string key)=>effects.Any(e=>e.Contains(key,StringComparison.OrdinalIgnoreCase));
        double V(string key,double fallback)=>vars.FirstOrDefault(v=>v.Key.Equals(key,StringComparison.OrdinalIgnoreCase)).Value is var value && value!=0?Math.Abs(value):fallback;
        double missing=(double)(player.Creature.MaxHp-player.Creature.CurrentHp);
        double value=0;
        if(Has("MimicRestSiteHeal")||Has("CreatureCmd.Heal"))value+=Math.Min(missing,V("Heal",(double)player.Creature.MaxHp*.3))*.7;
        if(Has("CreatureCmd.Damage")||Has("PlayerCmd.LoseHp"))value-=V("HpLoss",8)*(player.Creature.CurrentHp<player.Creature.MaxHp*.5m?1.2:.6);
        if(Has("GainGold"))value+=V("Gold",40)*.04;
        if(Has("LoseGold"))value-=V("Gold",40)*.03;
        if(Has("RelicCmd.Obtain"))value+=option.Relic!=null?RelicValue(option.Relic):7;
        if(Has("CardCmd.Upgrade"))value+=player.Deck.Cards.Where(c=>c.IsUpgradable).Select(UpgradeGain).DefaultIfEmpty(0).Max();
        if(Has("CardCmd.Remove"))value+=player.Deck.Cards.Where(c=>c.IsRemovable).Select(c=>SelectionValue(c,CardSelectionPurpose.Remove)).DefaultIfEmpty(0).Max();
        if(Has("CardCmd.Transform"))value+=3;
        if(Has("GainMaxHp"))value+=V("MaxHp",5)*.8;
        if(Has("LoseMaxHp"))value-=V("MaxHp",5);
        if(Has("EnterCombat"))value+=player.Creature.CurrentHp>player.Creature.MaxHp*.7m?2:-6;
        // Unknown effects are kept available and left for native rollout comparison.
        return value;
    }
}
