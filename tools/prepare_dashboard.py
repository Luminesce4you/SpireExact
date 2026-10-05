"""Bind the latest SpireBoard frontend to the user's built source (public profile i100).

One max_decisions=0 initialization checks the native pause adapter and ABI.
It performs no selected game actions, combat search, whole-seed search or probe.
All generated dependencies, readiness and reports remain private local files.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spire_exact.canonical import ContractError
from spire_exact.upstream import file_hash
from tools import setup_source
from tools.experiment import version_hash

READY = ROOT / ".tools/dashboard-ready.json"
PROFILE = ROOT / "dashboard/solver-profile.json"
from spire_exact.planning.final_defaults import PUBLIC_PROFILE  # noqa: E402
PUBLIC_RELEASE = PUBLIC_PROFILE


def public_settings() -> list[str]:
    """The frontend's planner recipe: the public profile with every switch explicit."""
    from spire_exact.planning.final_defaults import i100_argv
    return ["--feature-profile", PUBLIC_PROFILE, *i100_argv()]


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def check_setup() -> dict:
    """The same immutable inputs/artifacts used by source-setup --check-only."""
    stamp = json.loads(setup_source.SOURCE_BUILD_STAMP.read_text(encoding="utf-8"))
    upstream = setup_source.check_upstream(require_patch=True)
    artifacts = setup_source.built_artifacts()
    inputs = setup_source.build_configuration_inputs()
    dependencies = setup_source.dependency_check(setup_source.DATA, setup_source.WORKSHOP)
    host_stamp = json.loads((setup_source.HOST / "bin/build-identity.json").read_text(encoding="utf-8"))
    if (stamp.get("upstream_source_sha256") != upstream["source_and_config_tree_sha256"]
            or stamp.get("artifacts") != artifacts or stamp.get("build_configuration_inputs") != inputs
            or stamp.get("dependency_lock_sha256") != file_hash(setup_source.DEPENDENCY_LOCK)
            or not dependencies["matches_locked_dependencies"]
            or host_stamp.get("inputs") != setup_source.input_fingerprint(setup_source.DATA)
            or host_stamp.get("host_sha256") != file_hash(setup_source.HOST_DLL)):
        raise ContractError("Source build identity changed; run tools/setup_source.py again before preparing the frontend")
    return {"source_version": version_hash(ROOT), "host_sha256": file_hash(setup_source.HOST_DLL),
            "build_identity_sha256": file_hash(setup_source.SOURCE_BUILD_STAMP),
            "artifacts": artifacts, "dependency_lock_sha256": file_hash(setup_source.DEPENDENCY_LOCK)}


def validate_ready(profile: dict, *, full: bool = True) -> dict:
    if not isinstance(profile, dict) or profile.get("public_source") is not True or profile.get("workspace") != ".":
        raise ContractError("Public frontend profile must use this verified source root")
    if (profile.get("solver_settings") != public_settings()
            or profile.get("runtime_profile") != "server-bounded-large-gen0"
            or profile.get("worker_memory_mib") != 1792 or profile.get("max_minutes") != 45):
        raise ContractError("Frontend settings differ from the public " + PUBLIC_PROFILE + " recipe")
    ready = json.loads(READY.read_text(encoding="utf-8"))
    report_path = (ROOT / ready["report_path"]).resolve()
    if not report_path.is_relative_to((ROOT / "outputs/dashboard-prepare").resolve()):
        raise ContractError("Frontend readiness report path is outside its private output root")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    clock = report.get("pause_clock", {})
    if (ready.get("schema") != "spire-public-dashboard-ready/v1" or ready.get("feature_profile") != PUBLIC_PROFILE
            or ready.get("capabilities", {}).get("interactive_pause_clock") != "evaluate-native-and-python/v1"
            or ready.get("capabilities", {}).get("validated_host_sha256") != ready.get("host_sha256")
            or ready.get("profile_sha256") != file_hash(PROFILE)
            or ready.get("report_sha256") != file_hash(report_path)
            or report.get("passed") is not True or report.get("returncode") != 0
            or report.get("actions_observed") != 0 or report.get("full_search_started") is not False
            or clock.get("enabled") is not True or type(clock.get("patched_methods")) is not int
            or clock["patched_methods"] <= 0 or report.get("block_compensation_enabled") is not True
            or ready.get("source_version") != version_hash(ROOT)
            or ready.get("host_sha256") != file_hash(setup_source.HOST_DLL)
            or report.get("source_version") != ready.get("source_version")
            or report.get("host_sha256") != ready.get("host_sha256")
            or ready.get("build_identity_sha256") != file_hash(setup_source.SOURCE_BUILD_STAMP)):
        raise ContractError("Frontend preparation is stale or invalid; run tools/prepare_dashboard.py again")
    if full:
        checked = check_setup()
        if checked["source_version"] != ready["source_version"] or checked["host_sha256"] != ready["host_sha256"]:
            raise ContractError("Frontend source/build identity mismatch")
    return ready


def launch_status() -> dict:
    try:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        validate_ready(profile, full=False)
        return {"ready": True, "feature_profile": PUBLIC_PROFILE, "max_minutes": 45}
    except (OSError, ValueError, KeyError, TypeError, ContractError):
        return {"ready": False, "feature_profile": PUBLIC_PROFILE, "max_minutes": 45,
                "unavailable_reason": "请先按 README 构建，然后运行 python tools/prepare_dashboard.py。"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Verify existing frontend binding without native initialization")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This frontend requires Windows Job Objects")
    if sys.version_info < (3, 11):
        parser.error("Python 3.11+ is required")
    try:
        config = json.loads(setup_source.CONFIG.read_text(encoding="utf-8"))
        executable = config["dotnet"]
        snapshot = check_setup()
        if args.check_only:
            ready = validate_ready(json.loads(PROFILE.read_text(encoding="utf-8")))
            print(json.dumps({"frontend_ready": True, "feature_profile": PUBLIC_PROFILE, "host_sha256": ready["host_sha256"],
                              "native_initializations_executed": 0}))
            return 0
        folder = ROOT / "outputs/dashboard-prepare" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder.mkdir(parents=True)
        ledger = folder / "pause-ledger.json"
        write(ledger, {"schema": "spire-pause/v1", "paused_total_seconds": 0, "paused_started_monotonic": None})
        dependency_dirs = [setup_source.WORKSHOP / "compat/0.111.0", setup_source.WORKSHOP / "shared"]
        from spire_exact.planning.identity import advisor_identity
        advisor = {"solver": str(setup_source.SOLVER_DLL), "harness": str(setup_source.HARNESS_DLL),
                   "dependency_dirs": [str(path) for path in dependency_dirs], "budget_ms": 600000,
                   "boss_budget_ms": 600000, "dop": 1, "profile": "Low", "search_mode": "Evaluate",
                   "nodes": 60000, "fix_consumed_block_compensation": True, "quiet_diagnostics": True,
                   "reuse_continuations": True, "complete_continuations_only": False}
        advisor["binary_identity"] = advisor_identity(advisor)
        request = {"command": "replay", "out": str(folder / "data"), "compact": True,
                   "seed": "0", "character": "IRONCLAD", "ascension": 10, "unlocks": "all", "history": [],
                   "generate_candidate": True, "max_decisions": 0, "policy_seed": 0, "capture_checkpoints": False,
                   "low_io": True, "event_driven_settle": True, "advisor": advisor}
        write(folder / "request.json", request)
        environment = dict(os.environ, SPIRE_GAME_DATA=str(setup_source.DATA), SPIRE_PAUSE_LEDGER=str(ledger),
                           DOTNET_ROOT=str(Path(executable).parent), DOTNET_CLI_TELEMETRY_OPTOUT="1")
        result = subprocess.run([executable, str(setup_source.HOST_DLL), str(folder / "request.json")],
                                cwd=ROOT, env=environment, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60)
        (folder / "host.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        decision_path = folder / "data/decision.json.gz"
        response = json.loads(gzip.decompress(decision_path.read_bytes())) if decision_path.is_file() else {}
        trace_path = folder / "data/trace.jsonl"
        actions = len(trace_path.read_text(encoding="utf-8").splitlines()) if trace_path.is_file() else -1
        metrics = response.get("advisor_metrics") or {}
        clock = metrics.get("pause_clock") or {}
        no_searches = metrics.get("searches") == []
        enabled = (metrics.get("block_compensation") or {}).get("enabled") is True
        passed = (result.returncode == 0 and actions == 0 and response.get("trace") == [] and no_searches
                  and response.get("status") == "BUDGET" and response.get("reason") == "candidate_decision_budget"
                  and clock.get("enabled") is True and clock.get("patched_methods", 0) > 0 and enabled)
        report = {"schema": "spire-public-frontend-prepare/v1", "passed": passed, "returncode": result.returncode,
                  "source_release": PUBLIC_RELEASE, "source_version": snapshot["source_version"],
                  "host_sha256": snapshot["host_sha256"], "native_actions_requested": 0, "actions_observed": actions,
                  "full_search_started": False if no_searches else None, "pause_clock": clock,
                  "block_compensation_enabled": enabled, "status": response.get("status"), "reason": response.get("reason"),
                  "scope": "zero-action initialization and native pause-clock/ABI binding; no combat, selected game action, probe or campaign search",
                  "live_job_pause_resume_executed": False}
        write(folder / "report.json", report)
        if not passed:
            raise ContractError("Zero-action frontend preparation failed; inspect " + str(folder / "report.json"))
        # New local storage only. Preserve existing user configuration on conflict.
        artifact = ROOT / "outputs/spireboard"
        artifact.mkdir(parents=True, exist_ok=True)
        (ROOT / "experiments").mkdir(exist_ok=True)
        storage_path = ROOT / "storage-policy.json"
        storage = {"schema": "spire-storage/v1", "artifact_roots": [str(artifact)],
                   "task_cap_decimal_gb": 150, "admission_cap_decimal_gb": 110, "per_job_write_cap_decimal_gb": 8,
                   "max_concurrent_seed_jobs": 4, "recent_detailed_seed_window": 40,
                   "notes": "Generated local SpireBoard artifacts; source and evidence are preserved"}
        if storage_path.exists() and json.loads(storage_path.read_text(encoding="utf-8")) != storage:
            raise ContractError("Existing storage-policy.json preserved; use a fresh public source directory")
        write(storage_path, storage)
        seed_ledger = ROOT / "experiments/seed-ledger.json"
        if not seed_ledger.exists():
            write(seed_ledger, {"reserved": ["0", "1", "2", "42"], "runs": []})
        profile = {"workspace": ".", "public_source": True, "source_release": PUBLIC_RELEASE,
                   "runtime_profile": "server-bounded-large-gen0", "worker_memory_mib": 1792,
                   "long_run_ready": True, "max_minutes": 45, "queue_policy": "fifo",
                   "solver_settings": public_settings(),
                   "notes": "Latest " + PUBLIC_PROFILE + " source recipe; live pause requires matching locally prepared source/host binding"}
        write(PROFILE, profile)
        ready = {**snapshot, "schema": "spire-public-dashboard-ready/v1", "feature_profile": PUBLIC_PROFILE,
                 "capabilities": {"interactive_pause_clock": "evaluate-native-and-python/v1",
                                  "validated_host_sha256": snapshot["host_sha256"]},
                 "report_path": (folder / "report.json").relative_to(ROOT).as_posix(),
                 "report_sha256": file_hash(folder / "report.json"), "profile_sha256": file_hash(PROFILE)}
        write(READY, ready)
        validate_ready(profile)
        print(json.dumps({"frontend_ready": True, "feature_profile": PUBLIC_PROFILE, "max_minutes": 45,
                          "report": str(folder / "report.json"), "actions_observed": 0,
                          "patched_methods": clock["patched_methods"], "search_executed": False}))
        return 0
    except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
