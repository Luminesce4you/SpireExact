using System.Reflection;
using System.Security.Cryptography;
using HarmonyLib;
using MegaCrit.Sts2.Core.Models.Cards;

namespace SpireNativeHost;

// Adapter for pinned CombatSolver (Torch1230, MIT; see THIRD_PARTY_NOTICES).
// Only the predictor's fallback guard changes. Native game state is untouched.
internal static class AdvisorBlockCompensation
{
    const BindingFlags All = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;
    const string PinnedSolver = "0AF01356BE8B46BCE4D2D17088E83184D7205C8958C089C8D9DCAEC312E38C32";
    // iteration-039 project-owned builds from the same pinned tree. ILSpy's
    // complete CorePowerSupport output is byte-identical for all three DLLs:
    // 668b53a6135392e7a4865661d203df9ba8d70723580fe0abbc36a48bf1e2b831.
    // Originally exact hashes only. The public source package additionally
    // accepts an independently audited whole-class canonical CIL fingerprint;
    // MVID/PDB/path noise from a local rebuild does not change that contract.
    // Any different class or nested generated type still fails closed. The candidate
    // changes only StateFingerprintBuilder's integer inlining hint.
    const string RebuiltBaseline = "EE1598B7385D26D55E36F3FD22101FD2549DFE3E062669DEDFD97432E5C523C8";
    const string FingerprintInline = "5EC668D18922B72314451AE67945F05167191E2448FE081381D1754C557F043E";
    // iteration-040 fork-capacity: the complete CorePowerSupport decompilation
    // is byte-identical to the audited class above; only list capacities differ.
    const string ForkCapacity = "2CB5D36D7281FD54A4C77BD042C97586F043670D9132997C52475FD4C51CB86D";
    // iteration-041: lazy hook contexts; same whole-class block contract audit.
    const string LazyHookContexts = "CE1359CB5C83AE7B8B785F79EE392626B02E7F865565E32BB2BED427C72F40ED";
    // iteration-041: immutable hook-mask lookup index; identical block contract.
    const string HookMaskIndex = "C5782A436FBFB814C02FEFE690D9F9ECC35B2D48696BFA70BC86565954DCC1E4";
    static bool installed;
    static volatile bool enabled;
    static PropertyInfo? preview;
    static long guardedBlockCards;

    public static void Configure(Assembly solver, bool useFix)
    {
        if (useFix && !installed)
        {
            using var file = File.OpenRead(solver.Location);
            string hash = Convert.ToHexString(SHA256.HashData(file));
            if (hash != PinnedSolver && hash != RebuiltBaseline && hash != FingerprintInline && hash != ForkCapacity && hash != LazyHookContexts && hash != HookMaskIndex)
            {
                // Re-read the actual PE; no external registration or self-reported
                // build JSON can authorize a different compensation contract.
                if (SolverContractFingerprint.Compute(solver.Location) != SolverContractFingerprint.Expected)
                    throw new InvalidOperationException("ADVISOR_BLOCK_COMPENSATION_BINARY_CHANGED");
            }
            var method = solver.GetType("CombatSolver.CorePowerSupport", true)!.GetMethod("ApplyCardPowers", All)!;
            var args = method.GetParameters();
            if (!method.IsStatic || method.ReturnType != typeof(bool) || args.Length != 9
                || args[2].Name != "playedCard" || args[5].Name != "ownerBlockBefore" || args[5].ParameterType != typeof(int)
                || args[6].Name != "cardBlockGained" || args[6].ParameterType != typeof(decimal))
                throw new InvalidOperationException("ADVISOR_BLOCK_COMPENSATION_CONTRACT_CHANGED");
            preview = args[2].ParameterType.GetProperty("Preview", All)
                ?? throw new MissingMemberException(args[2].ParameterType.FullName, "Preview");
            new Harmony("spire-exact.advisor-consumed-block").Patch(method,
                prefix: new HarmonyMethod(typeof(AdvisorBlockCompensation).GetMethod(nameof(Guard), All)!));
            installed = true;
        }
        Interlocked.Exchange(ref guardedBlockCards, 0);
        enabled = useFix;
    }

    static void Guard(object playedCard, ref int ownerBlockBefore, decimal cardBlockGained)
    {
        if (!enabled || cardBlockGained <= 0m || preview!.GetValue(playedCard) is not (Armaments or IronWave or Taunt)) return;
        // The pinned method uses this argument ONLY in its fallback comparison:
        // remaining block <= pre-play block. Already-granted block can be spent
        // by thorns, so remaining block is not a valid grant ledger. A sentinel
        // makes that fallback false without removing already-applied effects,
        // touching the live player, or skipping any other ApplyCardPowers logic.
        ownerBlockBefore = int.MinValue;
        Interlocked.Increment(ref guardedBlockCards);
    }

    public static object Metrics() => new { enabled, guarded_block_cards = Interlocked.Read(ref guardedBlockCards) };
}
