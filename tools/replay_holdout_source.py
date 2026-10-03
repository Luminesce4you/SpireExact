"""Replay a context + complete action witness twice under the current native identity.

Historical certificates and host fingerprints are never reused. A new certificate
requires two fresh initial executions with identical complete decision evidence.
--dry-run validates only the input JSON and creates no output or native process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))

from spire_exact.canonical import ContractError, canonical, digest, read_json
from spire_exact.mode1 import context, check_winning_replay, is_winning_candidate
from spire_exact.planning.io import write_json


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def route_input(path: Path) -> tuple[dict, list[dict]]:
    route = read_json(path)
    if type(route) is not dict or type(route.get("context")) is not dict:
        raise ContractError("route must contain a context object and a complete trace array")
    supplied = route["context"]
    try:
        expected = context(supplied["seed"], supplied["character"], supplied["ascension"], supplied["unlocks"])
    except (KeyError, TypeError, AttributeError) as error:
        raise ContractError("route context requires seed, character, ascension and unlocks") from error
    if canonical(supplied) != canonical(expected):
        raise ContractError("route context must match the supported Standard mode1 initial context")
    trace = route.get("trace")
    if type(trace) is not list or not trace:
        raise ContractError("route trace must be a nonempty array of action objects")
    for index, action in enumerate(trace):
        if type(action) is not dict or type(action.get("kind")) is not str or not action["kind"]:
            raise ContractError(f"trace[{index}] must be an action object with a nonempty kind")
    # canonical() rejects unsupported values, floats, and ambiguous semantic JSON.
    canonical(trace)
    return expected, trace


def local_setup() -> Path:
    config_path = ROOT / ".tools/source-setup.json"
    if not config_path.is_file():
        raise ContractError("run tools/setup_source.py before replaying a witness")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    executable = Path(config["dotnet"])
    if not executable.is_file():
        raise ContractError("configured .NET SDK is unavailable; rerun tools/setup_source.py")
    os.environ["PATH"] = str(executable.parent) + os.pathsep + os.environ.get("PATH", "")
    os.environ["DOTNET_ROOT"] = str(executable.parent)
    os.environ["PYTHONIOENCODING"] = "utf-8"
    checked = subprocess.run(
        [sys.executable, str(ROOT / "tools/setup_source.py"), "--check-only"],
        cwd=ROOT, env=dict(os.environ), check=False,
    )
    if checked.returncode:
        raise ContractError(f"setup_source.py --check-only failed ({checked.returncode}); inspect outputs/source-setup")
    return Path(config["game_data"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", required=True, type=Path, help="JSON containing the initial context and complete trace")
    parser.add_argument("--out", required=True, type=Path, help="New output folder; existing folders are preserved")
    parser.add_argument("--seconds", type=positive, default=180, help="Safety timeout for each fresh replay (default: 180)")
    parser.add_argument("--worker-memory-mib", type=positive, default=1000, help="One worker memory budget, at least 64 MiB")
    parser.add_argument("--dry-run", action="store_true", help="Validate route JSON only; no setup check, native requests or output files")
    args = parser.parse_args()
    if args.worker_memory_mib < 64:
        parser.error("--worker-memory-mib must be at least 64")
    route_path = (args.route if args.route.is_absolute() else ROOT / args.route).resolve()
    out = (args.out if args.out.is_absolute() else ROOT / args.out).resolve()
    if not args.dry_run and out.exists():
        parser.error("use a new output folder; existing evidence is preserved")

    report = {
        "schema": "spire-action-witness-replay/v1", "status": "UNKNOWN",
        "whole_run_verified": False, "native_requests_executed": 0,
        "route": str(route_path), "historical_certificate_reused": False,
        "historical_holdout_result_inherited": False, "game_equivalence_verified": False,
        "proof_scope": "installed game commands in offline TestMode",
        "advisor_enabled": False, "checkpoint_restore_enabled": False, "result_cache_enabled": False,
        "seconds_per_replay": args.seconds,
    }
    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=False)
    exit_code = 1
    try:
        ctx, trace = route_input(route_path)
        report.update(context=ctx, actions=len(trace), trace_sha256=digest(trace),
                      route_file_sha256=hashlib.sha256(route_path.read_bytes()).hexdigest())
        if args.dry_run:
            report.update(status="VALIDATED_INPUT_ONLY", dry_run=True, planned_fresh_replays=2,
                          setup_check_executed=False, output_created=False)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if os.name != "nt":
            raise ContractError("this source release supports Windows only")
        if sys.version_info < (3, 11):
            raise ContractError("Python 3.11+ is required")
        data = local_setup()
        report["setup_check_passed"] = True
        # Importing/creating the native pool happens only after setup validation.
        from spire_exact.native import game_data
        from spire_exact.planning.pool import NativePool
        from spire_exact.planning.resources import ResourcePlan
        resources = ResourcePlan.detect(1, 1, args.worker_memory_mib)
        report["resources"] = resources.as_dict()
        request = {key: ctx[key] for key in ("seed", "character", "ascension", "unlocks")}
        request.update(history=trace, generate_candidate=False, capture_checkpoints=False,
                       max_decisions=max(3000, len(trace) + 1))
        with NativePool(game_data(data), out / "workers", resources) as pool:
            report["native_requests_executed"] = 1
            first, first_identity = pool.run(request, out / "first-replay", args.seconds, fresh=True)
            report["first_replay_status"] = first.get("status")
            if (not is_winning_candidate(first) or first.get("consumed") != len(trace)
                    or canonical(first.get("trace")) != canonical(trace)):
                raise ContractError("first fresh replay did not consume the complete witness and reach native whole-run victory")
            report["native_requests_executed"] = 2
            second, second_identity = pool.run(request, out / "second-independent-replay", args.seconds, fresh=True)
            report["second_replay_status"] = second.get("status")
            certificate = check_winning_replay(
                first, second, {"context": ctx, "native": first_identity},
                {"context": ctx, "native": second_identity},
            )
            # check_winning_replay compares complete actions, visible evidence,
            # terminal observations, current executable identities and the objective.
        write_json(out / "certificate.json", certificate)
        report.update(status="VERIFIED_WIN_IN_NATIVE_HOST", whole_run_verified=True,
                      current_identity=certificate["identity"], certificate="certificate.json")
        exit_code = 0
    except KeyboardInterrupt:
        report.update(status="UNKNOWN", whole_run_verified=False)
        report["reason"] = "replay interrupted; no new victory certified"
        exit_code = 130
    except Exception as error:
        report.update(status="UNKNOWN", whole_run_verified=False)
        report["reason"] = str(error)
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        write_json(out / "report.json", report)
        print(json.dumps({"status": report["status"], "whole_run_verified": report["whole_run_verified"],
                          "native_requests_executed": report["native_requests_executed"],
                          "report": str(out / "report.json")}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
