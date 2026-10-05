"""Run a fresh i082/i085 A10 seed from the Windows source package.

The feature table and effective values come from the selected planner's own
final_defaults and argument parser. Dry-run only parses: no setup, native
requests, output creation, or search execution.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FROZEN_SOURCE_VERSION = "e1b10f6f10f1c1d27f5f176f8b4e73ce8aa07fc2a9ab94b9eaf2d9cd10bd7c74"
SOURCE_RELEASE = "i082/frozen-i082"


def settings(seed: str, minutes: int, workers: int, solver_seed: int, feature_profile: str = "i082") -> list[str]:
    from spire_exact.planning.final_defaults import i082_argv, i085_argv, i085_final01_argv
    if feature_profile not in ('i082','i085','i085-final01'):
        raise ValueError("This launcher specifies i082/i085/i085-final01; use the planner CLI for other profiles")
    seconds = minutes * 60
    return [
        "--seed", seed, "--character", "IRONCLAD", "--ascension", "10", "--unlocks", "all",
        "--workers", str(workers), "--dop", "1", "--reserve-mib", "1024",
        "--evaluations", "1000000", "--seconds", str(max(10, seconds - 30)),
        "--task-seconds", str(min(1200, seconds)), "--max-decisions", "12000",
        "--lookahead-actions", "12000", "--lookahead-floors", "99", "--alternatives", "8",
        "--survivors", "2", "--budget-ms", "600000", "--boss-budget-ms", "600000",
        "--nodes", "60000", "--profile", "Low", "--dispatch", "ordered",
        "--dispatch-window", "56", "--solver-seed", str(solver_seed), "--low-io",
        "--event-driven-settle", "--checkpoint-mib", "1024", "--cache-mib", "128",
        "--archive-entries", "256", "--feature-profile", feature_profile,
        *(i085_argv() if feature_profile == 'i085' else i085_final01_argv() if feature_profile == 'i085-final01'
          else i082_argv()),
    ]


def effective_parameters(arguments: list[str]) -> dict:
    """Capture the authoritative parser before native/resource initialization."""
    from spire_exact.planning import __main__ as planner
    from spire_exact.planning.final_defaults import resolve_entry_defaults
    parse = argparse.ArgumentParser.parse_args
    captured = None

    class Parsed(Exception):
        pass

    def capture(parser, argv=None, namespace=None):
        nonlocal captured
        captured = parse(parser, argv, namespace)
        raise Parsed()

    with patch.object(argparse.ArgumentParser, "parse_args", capture):
        try:
            planner.main(arguments)
        except Parsed:
            pass
    if captured is None:
        raise RuntimeError("Could not capture the planner parser")
    return {key: str(value) if isinstance(value, Path) else value
            for key, value in vars(resolve_entry_defaults(captured)).items()}


def detached_arguments(arguments: list[str]) -> list[str]:
    """Keep launcher flags ahead of argparse.REMAINDER's --extra boundary."""
    boundary = arguments.index("--extra") if "--extra" in arguments else len(arguments)
    return [v for v in arguments[:boundary] if v != "--detach"] + ["--detached-child"] + arguments[boundary:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--out", type=Path, required=True, help="New run folder; existing runs are preserved")
    parser.add_argument("--feature-profile", choices=["i082", "i085", "i085-final01"], default="i082")
    parser.add_argument("--minutes", type=int, default=30, choices=range(1, 46), metavar="1..45")
    parser.add_argument("--research-minutes",type=int,choices=range(1,181),metavar="1..180")
    parser.add_argument("--solver-seed", type=int, default=271828)
    parser.add_argument("--workers", type=int, default=7, choices=range(1, 8), metavar="1..7")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print effective settings without running anything")
    parser.add_argument("--detach", action="store_true", help="Start outside the calling app's process tree using Windows WMI")
    parser.add_argument("--detached-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument('--extra', nargs=argparse.REMAINDER, default=[],
                        help='Explicit planner overrides, after profile defaults')
    args = parser.parse_args()
    if args.research_minutes is not None:args.minutes=args.research_minutes
    if not re.fullmatch(r"[A-Za-z0-9]{1,64}", args.seed):
        parser.error("seed must contain 1..64 letters or digits")
    out = (args.out if args.out.is_absolute() else ROOT / args.out).resolve()
    data = ROOT / "runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64"
    selected = settings(args.seed, args.minutes, args.workers, args.solver_seed, args.feature_profile)
    selected += args.extra
    planner_arguments = ["--out", str(out), "--game-dir", str(data), *selected]
    effective = effective_parameters(planner_arguments)
    if effective['feature_profile'] != args.feature_profile:
        parser.error('--extra may not change the feature profile')
    source_release = (SOURCE_RELEASE if args.feature_profile == 'i082' else 'i085/final01-candidate'
                      if args.feature_profile == 'i085-final01' else 'i085/macro-candidate')
    frozen_version = FROZEN_SOURCE_VERSION if args.feature_profile == 'i082' else None
    command = [sys.executable, str(ROOT / "tools/limited_cli.py"), "solve-p5", *planner_arguments]
    if args.dry_run:
        print(json.dumps({"source_release": source_release, "frozen_source_version": frozen_version,
                          "feature_profile": args.feature_profile, "wall_cap_seconds": args.minutes * 60,
                          "protocol": "A10-seed-v2", "command": command, "effective_parameters": effective,
                          "native_requests_executed": False, "search_executed": False}, indent=2))
        return 0
    if os.name != "nt":
        parser.error("this launcher requires Windows Job Objects")
    if sys.version_info < (3, 11):
        parser.error("Python 3.11+ is required")
    if out.exists():
        parser.error("use a new output folder; existing runs are preserved")
    config_path = ROOT / ".tools/source-setup.json"
    if not config_path.is_file():
        parser.error("run tools/setup_source.py first")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    environment = dict(os.environ)
    dotnet = Path(config["dotnet"])
    environment.update(PATH=str(dotnet.parent) + os.pathsep + environment.get("PATH", ""),
                       DOTNET_ROOT=str(dotnet.parent), PYTHONIOENCODING="utf-8",
                       SPIRE_PROTOCOL="A10-seed-v2", SPIRE_WALL_LIMIT_SECONDS=str(args.minutes * 60),
                       SPIRE_REQUIRE_EXACT_WORKERS="1")
    for name in ("SPIRE_JOB_MEMORY_MIB", "SPIRE_RESOURCE_OVERRIDE_NOTE", "SPIRE_CPU_OFFSET"):
        environment.pop(name, None)
    check = subprocess.run([sys.executable, str(ROOT / "tools/setup_source.py"), "--check-only"],
                           cwd=ROOT, env=environment)
    if check.returncode:
        return check.returncode
    if args.detach:
        child_args = detached_arguments(sys.argv[1:])
        detach_environment = dict(environment)
        detach_environment["SPIRE_RELEASE_CHILD_COMMAND"] = subprocess.list2cmdline(
            [sys.executable, str(Path(__file__).resolve()), *child_args])
        detach_environment["SPIRE_RELEASE_CHILD_CWD"] = str(ROOT)
        script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
                  "@{CommandLine=$env:SPIRE_RELEASE_CHILD_COMMAND; CurrentDirectory=$env:SPIRE_RELEASE_CHILD_CWD}; "
                  "if ($r.ReturnValue -ne 0) { exit 1 }; $r.ProcessId")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                env=detach_environment, capture_output=True, text=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            return result.returncode
        print(json.dumps({"detached_pid": int(result.stdout.strip()), "out": str(out),
                          "feature_profile": args.feature_profile}))
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    from tools.experiment import version_hash
    manifest = {"schema": "spire-public-run/v2", "protocol": "A10-seed-v2", "source_release": source_release,
                "feature_profile": args.feature_profile, "frozen_source_version": frozen_version,
                "source_version": version_hash(), "seed": args.seed, "solver_seed": args.solver_seed,
                "character": "IRONCLAD", "ascension": 10, "unlocks": "all", "fresh_start": True,
                "requested_workers": args.workers, "wall_cap_seconds": args.minutes * 60,
                "workload_bytes": 14336 * 1024 * 1024, "os_reserve_bytes": 2 * 1024 * 1024 * 1024,
                "memory_protocol_override": "Source release A10-seed-v2: 14 GiB Job workload plus 2 GiB OS reserve",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(), "settings": selected,
                "effective_parameters": effective, "historical_holdout_result": False,
                "note": "New user run on " + args.feature_profile + "; historical holdout-01 17/20 belongs to its original frozen source"}
    manifest_path = out.parent / (out.name + ".validation-manifest.json")
    if manifest_path.exists():
        parser.error("the adjacent run manifest already exists; use a new output name")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log_path = out.parent / (out.name + ".launcher.log")
    with log_path.open("x", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
    print(json.dumps({"exit_code": result.returncode, "out": str(out), "manifest": str(manifest_path),
                      "log": str(log_path), "verified_win_requires_new_certificate": True}))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
