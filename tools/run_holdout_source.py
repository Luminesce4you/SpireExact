"""Public Windows launcher for the recorded holdout-01 configuration.

Uses local setup_source dependencies and the existing Windows Job runner.
This file prepares commands; it does not change campaign search or card policy.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def settings(seed: str, minutes: int, workers: int, solver_seed: int) -> list[str]:
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
        "--archive-entries", "256", "--scheduler", "focus", "--prior",
        "--gate-preset", "escalate", "--repair-mode", "gate", "--normal-nodes", "10000",
        "--runtime-profile", "server-large-gen0", "--worker-memory-mib", "1792",
        "--root-policies", "pick,elo", "--focus-cluster-cap", "2", "--final-gate-plan", "open",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--out", type=Path, required=True, help="New run folder, relative to the repository or absolute")
    parser.add_argument("--minutes", type=int, default=30, choices=range(1, 31), metavar="1..30")
    parser.add_argument("--solver-seed", type=int, default=271828)
    parser.add_argument("--workers", type=int, default=7, choices=range(1, 8), metavar="1..7")
    parser.add_argument("--dry-run", action="store_true", help="Print settings without setup checks, creating outputs, or running a solver")
    parser.add_argument("--detach", action="store_true", help="Start outside the caller's process tree using Windows WMI")
    parser.add_argument("--detached-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9]{1,64}", args.seed):
        parser.error("seed must contain 1..64 letters or digits")
    out = (args.out if args.out.is_absolute() else ROOT / args.out).resolve()
    data = ROOT / "runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64"
    command = [sys.executable, str(ROOT / "tools/limited_cli.py"), "solve-p5", "--out", str(out),
               "--game-dir", str(data), *settings(args.seed, args.minutes, args.workers, args.solver_seed)]
    if args.dry_run:
        print(json.dumps({"baseline": "holdout-01", "wall_cap_seconds": args.minutes * 60,
                          "command": command, "search_executed": False}, indent=2))
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
    environment["PATH"] = str(dotnet.parent) + os.pathsep + environment.get("PATH", "")
    environment["DOTNET_ROOT"] = str(dotnet.parent)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["SPIRE_PROTOCOL"] = "A10-seed-v2"
    environment["SPIRE_WALL_LIMIT_SECONDS"] = str(args.minutes * 60)
    for name in ("SPIRE_JOB_MEMORY_MIB", "SPIRE_RESOURCE_OVERRIDE_NOTE", "SPIRE_CPU_OFFSET"):
        environment.pop(name, None)
    check = subprocess.run([sys.executable, str(ROOT / "tools/setup_source.py"), "--check-only"],
                           cwd=ROOT, env=environment)
    if check.returncode:
        return check.returncode
    if args.detach:
        child_args = [value for value in sys.argv[1:] if value != "--detach"] + ["--detached-child"]
        # WMI receives a CreateProcess command line directly, without cmd.exe or shell expansion.
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
        print(json.dumps({"detached_pid": int(result.stdout.strip()), "out": str(out)}))
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    from tools.experiment import version_hash
    manifest = {"schema": "spire-public-run/v1", "protocol": "A10-seed-v2", "baseline": "holdout-01",
                "seed": args.seed, "solver_seed": args.solver_seed, "character": "IRONCLAD",
                "ascension": 10, "unlocks": "all", "fresh_start": True, "requested_workers": args.workers,
                "source_version": version_hash(), "wall_cap_seconds": args.minutes * 60,
                "workload_bytes": 14336 * 1024 * 1024, "os_reserve_bytes": 2 * 1024 * 1024 * 1024,
                "memory_protocol_override": "Source release A10-seed-v2: 14 GiB Job workload plus 2 GiB OS reserve",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(), "settings": command[7:],
                "historical_holdout_result": False,
                "note": "New user run; does not add to or reproduce a probability claim from the historical 17/20 report"}
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
