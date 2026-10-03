using System.Security.Cryptography;
using System.Text.Json;
using SpireNativeHost;

if (args.Length is < 1 or > 2)
{
    Console.Error.WriteLine("Usage: SolverContract <CombatSolver.dll> [canonical-output-path]");
    return 2;
}
try
{
    string path = Path.GetFullPath(args[0]);
    var fingerprint = SolverContractFingerprint.Inspect(path);
    if (args.Length == 2) File.WriteAllText(args[1], fingerprint.Canonical, new System.Text.UTF8Encoding(false));
    using var stream = File.OpenRead(path);
    Console.WriteLine(JsonSerializer.Serialize(new {
        schema = SolverContractFingerprint.Schema, guarded_type = SolverContractFingerprint.GuardedType,
        solver_sha256 = Convert.ToHexString(SHA256.HashData(stream)), contract_sha256 = fingerprint.Sha256,
        expected_contract_sha256 = SolverContractFingerprint.Expected,
        contract_matches = fingerprint.Sha256 == SolverContractFingerprint.Expected,
        type_count = fingerprint.Types, method_count = fingerprint.Methods,
        assembly_loaded_or_executed = false,
        canonical_bytes = System.Text.Encoding.UTF8.GetByteCount(fingerprint.Canonical)
    }));
    return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine(error.ToString());
    return 1;
}
