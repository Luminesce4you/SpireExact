// First native integration probe, targeted to CombatSolver commit
// 4d2c55069f2cf9f8c621631594857b412cf9660f.
// Source reviewed against upstream signatures; NOT compiled or game-tested in this delivery.
// This is diagnostic evidence only, not a lossless rehydratable state adapter.
using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using CombatSolver;
using MegaCrit.Sts2.Core.Combat;

namespace OfflineSearchHarness;

internal static class SpireExactRootProbe
{
    internal static void Write(CombatState combat, string outputDirectory)
    {
        // Run only on the harness main thread at its stable M1 boundary.
        var first = CombatRootSnapshot.Capture(combat);
        var second = CombatRootSnapshot.Capture(combat);
        bool continuationEqual = string.Equals(first.ContinuationStamp.StateText,
            second.ContinuationStamp.StateText, StringComparison.Ordinal);
        bool liveEqual = string.Equals(first.LiveStamp.StateText,
            second.LiveStamp.StateText, StringComparison.Ordinal);
        if (!continuationEqual || !liveEqual)
            throw new InvalidOperationException("SpireExact probe: root changed between captures.");

        var payload = new
        {
            schema = "spire-native-root-probe/v1",
            pinnedCommit = "4d2c55069f2cf9f8c621631594857b412cf9660f",
            gameAssembly = typeof(CombatState).Assembly.GetName().FullName,
            gameAssemblySha256 = FileSha256(typeof(CombatState).Assembly.Location),
            solverAssemblySha256 = FileSha256(typeof(CombatRootSnapshot).Assembly.Location),
            repeatedRootCaptureMatched = true,
            isRehydratableSnapshot = false,
            exhaustiveActionAdapterImplemented = false,
            gameEquivalenceVerified = false,
            first.PlayerCount,
            first.StartTurnNumber,
            first.TotalFloor,
            first.InitialPlayerHp,
            first.InitialPlayerMaxHp,
            first.CapturedCardCount,
            first.CapturedPowerCount,
            first.CapturedHookListenerCount,
            first.CapturedRunModSubscriberCount,
            first.CapturedCombatModSubscriberCount,
            first.SearchablePotionCount,
            // Preserve raw diagnostic text; do not mistake it for complete StateKey proof.
            continuationText = first.ContinuationStamp.StateText,
            liveText = first.LiveStamp.StateText
        };
        Directory.CreateDirectory(outputDirectory);
        File.WriteAllText(Path.Combine(outputDirectory, "spireexact-root-probe.json"),
            JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true }),
            new UTF8Encoding(false));
    }

    private static string? FileSha256(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || !File.Exists(path)) return null;
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }
}
