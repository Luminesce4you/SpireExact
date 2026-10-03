using System.Reflection;
using HarmonyLib;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Models;

namespace SpireNativeHost;

internal enum CardSelectionPurpose { Upgrade,Remove,Exhaust,Discard,Retain,Transform,Duplicate,Obtain,ChooseForCombat,ChooseForEvent,ChooseForShop,Other }
internal sealed record SelectionRequest(CardSelectionPurpose Purpose,string NativeMethod,string Prompt,string? Source);
internal static class CardSelectionContext
{
    static readonly AsyncLocal<SelectionRequest?> current=new();
    public static SelectionRequest? Current=>current.Value;
    public static void Install(Harmony harmony)
    {
        foreach(var method in typeof(CardSelectCmd).GetMethods(BindingFlags.Public|BindingFlags.Static)
            .Where(m=>m.Name.StartsWith("From") && !m.IsGenericMethod))
            harmony.Patch(method,prefix:new HarmonyMethod(typeof(CardSelectionContext),nameof(Enter)),
                postfix:new HarmonyMethod(typeof(CardSelectionContext),nameof(Leave)));
    }
    static void Enter(MethodBase __originalMethod,object[] __args,out SelectionRequest? __state)
    {
        __state=current.Value;
        string prompt=__args.OfType<CardSelectorPrefs>().Select(p=>p.Prompt?.LocEntryKey??"").FirstOrDefault()??"";
        string method=__originalMethod.Name;string text=(prompt+" "+method).ToUpperInvariant();
        var purpose=text.Contains("UPGRADE")?CardSelectionPurpose.Upgrade:
            text.Contains("REMOVE")||text.Contains("REMOVAL")?CardSelectionPurpose.Remove:
            text.Contains("EXHAUST")?CardSelectionPurpose.Exhaust:
            text.Contains("DISCARD")?CardSelectionPurpose.Discard:
            text.Contains("TRANSFORM")?CardSelectionPurpose.Transform:
            text.Contains("DUPLICAT")||text.Contains("CLONE")?CardSelectionPurpose.Duplicate:
            text.Contains("RETAIN")?CardSelectionPurpose.Retain:
            text.Contains("REWARD")||text.Contains("CHOOSEACARD")?CardSelectionPurpose.Obtain:
            text.Contains("HAND")||text.Contains("COMBATPILE")?CardSelectionPurpose.ChooseForCombat:
            __state?.Purpose??CardSelectionPurpose.Other;
        current.Value=new(purpose,method,prompt,__args.OfType<AbstractModel>().FirstOrDefault()?.Id.Entry);
    }
    static void Leave(SelectionRequest? __state)=>current.Value=__state;
}
