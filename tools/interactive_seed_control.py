"""Attach queued seed launchers to the same live Job control as SpireBoard."""
from pathlib import Path
import importlib.util
from spire_exact.planning.io import read_json, write_json


def last_setting(settings, key):
    return next((settings[i+1] for i in range(len(settings)-2, -1, -1) if settings[i] == key), None)


def evaluate_only(workspace, settings):
    try:
        source = Path(workspace)/'spire_exact/planning/gates.py'
        spec = importlib.util.spec_from_file_location('_queued_gate_control', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        selected = module.preset(last_setting(settings, '--gate-preset'))
        plans = [selected]
        final = last_setting(settings, '--final-gate-plan')
        if final and final != 'none': plans.append(selected['final'][final])
        members = []
        def collect(value):
            if isinstance(value, dict):
                if 'members' in value: members.extend(value['members'])
                for key, child in value.items():
                    if key != 'members': collect(child)
            elif isinstance(value, (list, tuple)):
                for child in value: collect(child)
        collect(plans)
        return bool(members) and all(member.get('mode') == 'Evaluate' for member in members)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        return False


def pause_evidence(workspace, manifest, report_path=None):
    """Validate actual zero-action initialization, never just a feature label."""
    try:
        freeze = read_json(Path(workspace)/'freeze.json') if (Path(workspace)/'freeze.json').exists() else {}
        capabilities = freeze.get('capabilities', {})
        path = report_path or capabilities.get('pause_clock_report')
        if not path:
            from tools.rolling_storage import owner_root
            path = owner_root(workspace)/'dashboard/control-validation'/(manifest['host_sha256']+'.json')
        path = Path(path).resolve()
        report = read_json(path)
        if (report.get('passed') is not True or report.get('returncode') != 0 or report.get('actions_observed') != 0
                or report.get('full_search_started') is not False
                or report.get('host_sha256') != manifest['host_sha256']
                or report.get('source_version', freeze.get('source_version')) != manifest['version']
                or (report.get('pause_clock') or {}).get('enabled') is not True
                or (report.get('pause_clock') or {}).get('patched_methods', 0) < 1
                or not evaluate_only(workspace, manifest['settings'])):
            return None
        return {'report_path': str(path), 'host_sha256': manifest['host_sha256'], 'source_version': manifest['version']}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def attach(folder, manifest, report_path=None, *, control=None):
    if control is None:
        from dashboard import job_control as control
    folder = Path(folder).resolve()
    state = control.initialize(folder)
    state.update(run_id=manifest['run_id'], run_folder=str(folder))
    write_json(folder/'control.json', state)
    evidence = pause_evidence(manifest['workspace'], manifest, report_path)
    manifest.update(process_control_schema=control.SCHEMA, control_origin='seed_launcher',
                    pause_excluded_budget_clock=evidence is not None,
                    pause_ledger=str(folder/'pause-ledger.json'), pause_clock_validation=evidence,
                    pause_scope='live_named_job_memory_only', durable_restart_supported=False)
    write_json(folder/'launch-state.json', {'phase':'starting'})
    return control, evidence is not None


def process_record(folder, pid, *, identity=None):
    if identity is None:
        from dashboard.jobs import process_identity as identity
    record = read_json(Path(folder)/'process.json')
    owned = identity(pid)
    if owned is None: raise RuntimeError('Queued solver exited before process identity could be registered')
    record.update(owned_job_pid=pid, owned_job_identity=owned)
    write_json(Path(folder)/'process.json', record)


def finish(folder, control, phase):
    if control.mark_finished(folder, phase):
        write_json(Path(folder)/'launch-state.json', {'phase':phase})
