"""Prepare private dependencies and build the Windows source release; never run it.

Requires Python 3.11+, Git, a .NET 9+ SDK, and the user's own supported game
and complete RitsuLib Workshop bundle. No mod is installed into the game.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.canonical import ContractError
from spire_exact.native import input_fingerprint
from spire_exact.upstream import PIN, REPOSITORY, fetch_upstream, file_hash, verify_upstream

VERSION = "0.111.0"
GAME_SHA256 = "0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9"
CONFIG = ROOT / ".tools/source-setup.json"
DATA = ROOT / "runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64"
WORKSHOP = ROOT / "runtime/steamapps/workshop/content/2868840/3747602295"
UPSTREAM = ROOT / "vendor/CombatSolver"
HOST = ROOT / "native/SpireNativeHost"
SOLVER_DLL = UPSTREAM / ".godot/mono/temp/bin/Release/CombatSolver.dll"
HARNESS_DLL = UPSTREAM / "tools/OfflineSearchHarness/bin/Release/net9.0/OfflineSearchHarness.dll"
HOST_DLL = HOST / "bin/Release/net9.0/SpireNativeHost.dll"
CONTRACT_ROOT = ROOT / "tools/solver_contract"
CONTRACT_DLL = CONTRACT_ROOT / "bin/Release/net9.0/SolverContract.dll"
CONTRACT_STAMP = ROOT / ".tools/solver-contract-build-identity.json"
SOURCE_BUILD_STAMP = ROOT / ".tools/source-build-identity.json"
DEPENDENCY_LOCK = ROOT / "tools/source_dependencies.lock.json"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def steam_libraries() -> list[Path]:
    import winreg
    roots = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
        roots.append(steam)
        vdf = steam / "steamapps/libraryfolders.vdf"
        if vdf.is_file():
            roots += [Path(p.replace("\\\\", "\\")) for p in
                      re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8"))]
    except OSError:
        pass
    return sorted(set(roots))


def find_game(path: Path | None) -> Path:
    roots = [path] if path else [p / "steamapps/common/Slay the Spire 2" for p in steam_libraries()]
    candidates = {item.resolve() for root in roots for item in
                  (root, root / "data_sts2_windows_x86_64") if (item / "sts2.dll").is_file()}
    if len(candidates) != 1:
        raise ContractError("Supply --game-dir: expected one game installation, found " + str(len(candidates)))
    data = candidates.pop()
    for name in ("sts2.dll", "GodotSharp.dll", "0Harmony.dll"):
        if not (data / name).is_file():
            raise ContractError("Missing game dependency: " + str(data / name))
    if file_hash(data / "sts2.dll") != GAME_SHA256:
        raise ContractError("Unsupported sts2.dll: holdout-01 requires v0.111.0 SHA256 " + GAME_SHA256)
    release = data.parent / "release_info.json"
    if not release.is_file():
        raise ContractError("Missing release_info.json next to the game data directory")
    version = json.loads(release.read_text(encoding="utf-8-sig")).get("version", "").removeprefix("v")
    if version != VERSION:
        raise ContractError("Unsupported release_info.json version: " + str(version))
    return data


def find_workshop(path: Path | None, data: Path) -> Path:
    if path:
        candidate = path.resolve()
        if candidate.is_file():
            candidate = candidate.parent
        if candidate.name == VERSION and candidate.parent.name == "compat":
            candidate = candidate.parents[1]
        candidates = [candidate]
    else:
        roots = [data.parents[2]] + [p / "steamapps" for p in steam_libraries()]
        candidates = sorted({p / "workshop/content/2868840/3747602295" for p in roots
                             if (p / "workshop/content/2868840/3747602295").is_dir()})
    candidates = [p for p in candidates if (p / "compat" / VERSION / "STS2-RitsuLib.dll").is_file()]
    if len(candidates) != 1:
        raise ContractError("Supply --ritsu-dir: expected one complete RitsuLib Workshop bundle with compat/" + VERSION)
    workshop = candidates[0]
    required = ["RitsuLib.References.props", "compat/0.111.0/STS2-RitsuLib.dll",
                "compat/0.111.0/STS2-RitsuLib.Runtime.dll", "shared/STS2-RitsuLib.Shared.dll",
                "shared/STS2-RitsuLib.Ui.dll", "shared/STS2-RitsuLib.Settings.dll"]
    for name in required:
        if not (workshop / name).is_file():
            raise ContractError("Incomplete RitsuLib bundle: " + str(workshop / name))
    return workshop


def sdk(path: str | None) -> tuple[str, list[str]]:
    requested = path or shutil.which("dotnet")
    if requested and Path(requested).is_dir():
        requested = str(Path(requested) / "dotnet.exe")
    if not requested or not Path(requested).is_file():
        raise ContractError("Install .NET 9+ SDK or supply --dotnet <path/to/dotnet.exe>")
    executable = str(Path(requested).resolve())
    result = subprocess.run([executable, "--list-sdks"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    installed = result.stdout.strip().splitlines()
    if result.returncode or not any(int(m.group(1)) >= 9 for line in installed
                                    if (m := re.match(r"(\d+)\.", line))):
        raise ContractError("A .NET 9+ SDK is required; runtime-only installations cannot build source")
    return executable, installed


def copy_private(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if file_hash(source) != file_hash(destination):
            raise ContractError("Private dependency already differs; preserve it and use a fresh release directory: " + str(destination))
        return
    shutil.copy2(source, destination)


def prepare_runtime(data: Path, workshop: Path) -> dict:
    # Assemblies and dependency metadata only; no game assets or executables.
    for item in data.iterdir():
        if item.is_file() and (item.suffix.lower() == ".dll" or item.name.endswith(".deps.json")):
            copy_private(item, DATA / item.name)
    copy_private(data.parent / "release_info.json", DATA.parent / "release_info.json")
    for folder in ("compat/" + VERSION, "shared"):
        for item in (workshop / folder).iterdir():
            if item.is_file() and item.suffix.lower() == ".dll":
                copy_private(item, WORKSHOP / folder / item.name)
    copy_private(workshop / "RitsuLib.References.props", WORKSHOP / "RitsuLib.References.props")
    hashes = {p.relative_to(ROOT / "runtime").as_posix(): file_hash(p)
              for p in sorted((ROOT / "runtime").rglob("*")) if p.is_file()}
    return {"game_input_directory": str(data), "ritsu_input_directory": str(workshop),
            "local_game_data": str(DATA), "local_ritsu_bundle": str(WORKSHOP),
            "private_dependency_sha256": hashes, "game_installed_or_modified": False}


def dependency_check(data: Path, workshop: Path) -> dict:
    lock = json.loads(DEPENDENCY_LOCK.read_text(encoding="utf-8"))
    game_prefix = "steamapps/common/Slay the Spire 2/"
    ritsu_prefix = "steamapps/workshop/content/2868840/3747602295/"
    actual = {}
    failures = []
    for relative, expected in lock["required_runtime_sha256"].items():
        if relative.startswith(game_prefix):
            item = data.parent / relative.removeprefix(game_prefix)
        elif relative.startswith(ritsu_prefix):
            item = workshop / relative.removeprefix(ritsu_prefix)
        else:
            raise ContractError("Invalid dependency lock path: " + relative)
        observed = file_hash(item) if item.is_file() else None
        actual[relative] = observed
        if observed != expected:
            failures.append(relative)
    return {"lock_sha256": file_hash(DEPENDENCY_LOCK), "matches_holdout_dependencies": not failures,
            "required_runtime_sha256": actual, "mismatches": failures, "additional_assemblies_allowed": True}


def command(argv: list[str], log: Path, report: dict, step: str,
            environment: dict | None = None, timeout: int = 600) -> None:
    record = {"step": step, "command": argv, "log": str(log)}
    report["steps"].append(record)
    print("Running " + step + " ...", flush=True)
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                env=environment, timeout=timeout)
    record["exit_code"] = result.returncode
    if result.returncode:
        raise ContractError(step + " failed; inspect " + str(log))


def check_upstream(require_patch: bool = False) -> dict:
    # The pinned projects import these optional files; .local/ is ignored by
    # upstream Git, so porcelain/source-tree hashing alone cannot see them.
    # The public source recipe has no private endpoint or property overrides.
    private_overrides = [name for name in ("local.props", ".local/presence.props", ".local/showcase.props")
                         if (UPSTREAM / name).exists()]
    if private_overrides:
        raise ContractError("Private upstream build overrides are unsupported; preserve them and use a fresh release directory: "
                            + str(private_overrides))
    provenance = verify_upstream(UPSTREAM, allow_dirty=True)
    # verify_upstream trims the whole porcelain response; its first entry
    # can lose the leading blank status column.
    names = {re.sub(r"^[ MADRCU?!]{1,2}\s+", "", line) for line in provenance["dirty_entries"]}
    allowed = {"tools/OfflineSearchHarness/Program.cs", "tools/OfflineSearchHarness/SpireExactRootProbe.cs"}
    if names - allowed:
        raise ContractError("Unexpected upstream changes; use a fresh release directory: " + str(sorted(names - allowed)))
    # Check exact contents too: allowed filenames alone are not authorization.
    from tools.install_native_probe import RELATIVE, apply_patch
    baseline = subprocess.run(["git", "show", "HEAD:" + RELATIVE], cwd=UPSTREAM,
                              capture_output=True, text=True, encoding="utf-8", errors="strict", check=True).stdout.replace("\r\n", "\n")
    current = (UPSTREAM / RELATIVE).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    # Match the original installer, which uses run(...).strip() + a newline.
    baseline = baseline.strip() + "\n"
    if current not in (baseline, apply_patch(baseline)):
        raise ContractError("Unexpected pinned harness Program.cs contents")
    probe = UPSTREAM / "tools/OfflineSearchHarness/SpireExactRootProbe.cs"
    if probe.exists() and probe.read_text(encoding="utf-8-sig") != (ROOT / "native/SpireExactRootProbe.cs").read_text(encoding="utf-8"):
        raise ContractError("Unexpected pinned harness root-probe contents")
    if require_patch and (current != apply_patch(baseline) or not probe.is_file()):
        raise ContractError("Required pinned harness patch is missing; run setup again")
    return provenance


def prepare_upstream(report: dict, report_dir: Path) -> None:
    if UPSTREAM.exists():
        provenance = check_upstream()
    else:
        if not shutil.which("git"):
            raise ContractError("Git is required to fetch the locked CombatSolver source")
        provenance = fetch_upstream(UPSTREAM)
    report["upstream_before_patch"] = provenance
    # The existing installer checks exact baseline/patch contents; no patch upgrade.
    command([sys.executable, str(ROOT / "tools/install_native_probe.py"), "--upstream", str(UPSTREAM)],
            report_dir / "source-patch.log", report, "pinned_harness_patch")
    report["upstream"] = check_upstream(require_patch=True)


def contract_inputs() -> dict:
    files = [CONTRACT_ROOT / "SolverContract.csproj", CONTRACT_ROOT / "Program.cs", HOST / "SolverContractFingerprint.cs"]
    return {p.relative_to(ROOT).as_posix(): file_hash(p) for p in files}


def build_configuration_inputs() -> dict:
    files = [ROOT / "NuGet.Config", ROOT / "Directory.Build.props", DEPENDENCY_LOCK]
    # Also bind absent automatic MSBuild/NuGet imports: adding a new local
    # targets/props/config file must invalidate readiness instead of hiding
    # behind an ignored directory. All project ancestors within ROOT matter.
    projects = [HOST, UPSTREAM, UPSTREAM / "tools/OfflineSearchHarness", CONTRACT_ROOT]
    for project in projects:
        folder = project
        while folder.is_relative_to(ROOT):
            files.extend(folder / name for name in ("Directory.Build.props", "Directory.Build.targets",
                         "Directory.Packages.props", "NuGet.Config", "global.json"))
            if folder == ROOT:
                break
            folder = folder.parent
    return {p.relative_to(ROOT).as_posix(): file_hash(p) if p.is_file() else "MISSING"
            for p in sorted(set(files))}


def compensation_check(executable: str) -> dict:
    source = (HOST / "AdvisorBlockCompensation.cs").read_text(encoding="utf-8")
    approved = sorted(set(re.findall(r'const string \w+ = "([0-9A-F]{64})";', source)))
    actual = file_hash(SOLVER_DLL).upper() if SOLVER_DLL.is_file() else None
    stamp = json.loads(CONTRACT_STAMP.read_text(encoding="utf-8")) if CONTRACT_STAMP.is_file() else {}
    current = (stamp.get("inputs") == contract_inputs() and CONTRACT_DLL.is_file()
               and stamp.get("assembly_sha256") == file_hash(CONTRACT_DLL))
    contract = None
    if current and SOLVER_DLL.is_file():
        result = subprocess.run([executable, str(CONTRACT_DLL), str(SOLVER_DLL)], capture_output=True,
                                text=True, encoding="utf-8", errors="strict", timeout=60)
        if result.returncode:
            raise ContractError("Static solver-contract inspection failed: " + result.stderr[-2000:])
        contract = json.loads(result.stdout)
    accepted = actual in approved or bool(contract and contract["contract_matches"])
    return {"enabled": True, "solver_sha256": actual, "approved_solver_sha256": approved,
            "approved_binary_match": actual in approved, "contract_inspector_current": current,
            "canonical_contract": contract, "audited_guard_accepts_build": accepted,
            "failure": None if accepted else "ADVISOR_BLOCK_COMPENSATION_BINARY_CHANGED",
            "host_guard_disabled_or_bypassed": False, "runtime_contract_executed": False}


def built_artifacts() -> dict:
    artifacts = {}
    for name, path in (("native_host", HOST_DLL), ("combat_solver", SOLVER_DLL), ("offline_harness", HARNESS_DLL),
                       ("static_contract_inspector", CONTRACT_DLL)):
        artifacts[name] = {"relative_path": path.relative_to(ROOT).as_posix(), "exists": path.is_file(),
                           "sha256": file_hash(path) if path.is_file() else None}
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, help="Your v0.111.0 game root or Windows data directory")
    parser.add_argument("--ritsu-dir", type=Path, help="Your complete RitsuLib Workshop 3747602295 root")
    parser.add_argument("--dotnet", help="Optional absolute path to dotnet.exe (otherwise PATH)")
    parser.add_argument("--check-only", action="store_true", help="Check setup/build identity without copying, fetching, building, or running native code")
    args = parser.parse_args()
    report_dir = ROOT / "outputs/source-setup" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report_dir.mkdir(parents=True)
    report = {"schema": "spire-source-setup/v1", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "source_release": "holdout-01/frozen-i054a", "upstream_repository": REPOSITORY,
              "upstream_commit": PIN, "steps": [], "compiled": False, "runtime_ready": False,
              "native_requests_executed": False, "search_or_probe_experiment_executed": False,
              "game_equivalence_verified": False, "game_installed_or_modified": False}
    status = 1
    try:
        if os.name != "nt":
            raise ContractError("This release setup is supported on Windows only")
        if sys.version_info < (3, 11):
            raise ContractError("Python 3.11+ is required")
        config = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.is_file() else {}
        executable, sdks = sdk(args.dotnet or config.get("dotnet"))
        report.update(dotnet=executable, installed_sdks=sdks)
        if args.check_only:
            data = find_game(DATA)
            workshop = find_workshop(WORKSHOP, data)
            report["upstream"] = check_upstream(require_patch=True)
        else:
            data = find_game(args.game_dir)
            workshop = find_workshop(args.ritsu_dir, data)
        report["dependency_check"] = dependency_check(data, workshop)
        if not report["dependency_check"]["matches_holdout_dependencies"]:
            raise ContractError("Dependencies differ from holdout-01; required SHA256 mismatches: "
                                + str(report["dependency_check"]["mismatches"]))
        if not args.check_only:
            report["runtime"] = prepare_runtime(data, workshop)
            write_json(CONFIG, {"dotnet": executable, "game_data": str(DATA), "ritsu_bundle": str(WORKSHOP),
                       "input_game_dir": str(data), "input_ritsu_dir": str(workshop), "source_release": report["source_release"]})
            prepare_upstream(report, report_dir)
            env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")
            command([executable, "build", str(CONTRACT_ROOT / "SolverContract.csproj"), "-c", "Release", "-p:NuGetAudit=false"],
                    report_dir / "static-contract-inspector.log", report, "static_contract_inspector", env)
            write_json(CONTRACT_STAMP, {"inputs": contract_inputs(), "assembly_sha256": file_hash(CONTRACT_DLL)})
            props = ["-p:CopyModOnBuild=false", "-p:Sts2DataDir=" + str(DATA), "-p:Sts2Dir=" + str(DATA.parent),
                     "-p:RitsuWorkshopRoot=" + str(WORKSHOP), "-p:RitsuLibDir=" + str(WORKSHOP / "compat" / VERSION),
                     "-p:RitsuLibReferenceTarget=" + VERSION, "-p:NuGetAudit=false"]
            command([executable, "build", str(HOST / "SpireNativeHost.csproj"), "-c", "Release", *props,
                     "-p:OutputPath=" + str(HOST_DLL.parent) + "/"], report_dir / "native-host.log", report, "native_host", env)
            # Matches frozen cloud build: the offline solver does not use the net48 Windows GUI helper.
            command([executable, "build", str(UPSTREAM / "CombatSolver.csproj"), "-c", "Release", *props,
                     "-p:OS=Unix", "-p:OutputPath=" + str(SOLVER_DLL.parent) + "/"],
                    report_dir / "combat-solver.log", report, "combat_solver", env)
            command([executable, "build", str(UPSTREAM / "tools/OfflineSearchHarness/OfflineSearchHarness.csproj"),
                     "-c", "Release", *props, "-p:OS=Unix", "-p:OutputPath=" + str(HARNESS_DLL.parent) + "/"],
                    report_dir / "offline-harness.log", report, "offline_harness", env)
            host_step = next(p for p in report["steps"] if p["step"] == "native_host")
            write_json(HOST / "bin/build-identity.json", {"inputs": input_fingerprint(DATA), "host_sha256": file_hash(HOST_DLL),
                       "build_completed": True, "game_equivalence_verified": False, "log": host_step["log"], "command": host_step["command"]})
            write_json(SOURCE_BUILD_STAMP, {"upstream_source_sha256": report["upstream"]["source_and_config_tree_sha256"],
                       "artifacts": built_artifacts(), "dependency_lock_sha256": file_hash(DEPENDENCY_LOCK),
                       "build_configuration_inputs": build_configuration_inputs()})
        report["artifacts"] = built_artifacts()
        report["compiled"] = all(value["exists"] for value in report["artifacts"].values())
        stamp_path = HOST / "bin/build-identity.json"
        stamp = json.loads(stamp_path.read_text(encoding="utf-8")) if stamp_path.is_file() else {}
        report["native_host_identity_current"] = (
            stamp.get("inputs") == input_fingerprint(DATA)
            and HOST_DLL.is_file() and stamp.get("host_sha256") == file_hash(HOST_DLL))
        source_stamp = json.loads(SOURCE_BUILD_STAMP.read_text(encoding="utf-8")) if SOURCE_BUILD_STAMP.is_file() else {}
        report["source_build_identity_current"] = (
            source_stamp.get("upstream_source_sha256") == report["upstream"]["source_and_config_tree_sha256"]
            and source_stamp.get("artifacts") == report["artifacts"]
            and source_stamp.get("dependency_lock_sha256") == file_hash(DEPENDENCY_LOCK)
            and source_stamp.get("build_configuration_inputs") == build_configuration_inputs())
        report["build_configuration_inputs"] = build_configuration_inputs()
        report["compensation"] = compensation_check(executable)
        report["runtime_ready"] = (report["compiled"] and report["native_host_identity_current"]
                                   and report["source_build_identity_current"]
                                   and report["compensation"]["audited_guard_accepts_build"])
        if not report["runtime_ready"]:
            raise ContractError("Built artifacts or audited solver contract are not current; inspect report.json and BUILD.md. "
                                "Re-run setup to rebuild. No guard was bypassed.")
        status = 0
    except (ContractError, OSError, ValueError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
        print(str(error), file=sys.stderr)
    finally:
        report["successful"] = status == 0
        write_json(report_dir / "report.json", report)
        print(json.dumps({"report": str(report_dir / "report.json"), "compiled": report["compiled"],
                          "runtime_ready": report["runtime_ready"], "successful": report["successful"]}), flush=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
