from __future__ import annotations
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys
from .canonical import ContractError, UnsupportedSemantic, read_json, write_json
from .core import Limits, solve
from .symbolic import GraphModel, SymbolicModel
from .verify import replay_trace, verify_graph
from .rpc import RpcModel, serve
from .upstream import ROOT, audit_source, doctor, fetch_upstream, build_native, native_baseline
from .native import export_native, compare_exports
from .information import solve_information
from .mode1 import CandidateBudget, context as mode1_context, solve_mode1, verify_mode1


def load_model(path: str, seed: str):
    spec = read_json(path)
    if spec.get('schema') == 'spire-reference-ir/v1': return SymbolicModel(spec, seed)
    if spec.get('schema') == 'spire-explicit-model/v1': return GraphModel(spec)
    raise ContractError('unknown model schema')


def add_limits(parser):
    parser.add_argument('--max-states', type=int, default=100_000)
    parser.add_argument('--max-expansions', type=int, default=100_000)
    parser.add_argument('--max-edges', type=int, default=1_000_000)
    parser.add_argument('--max-seconds', type=float)
    parser.add_argument('--out', type=Path, default=Path('outputs/search'))


def publish_search(model, args):
    if args.out.exists() and any(args.out.iterdir()):
        raise ContractError('output directory is not empty; use a new directory to avoid stale proof files')
    result = solve(model, Limits(args.max_states, args.max_expansions, args.max_edges, args.max_seconds))
    replay = None
    verified = None
    if result.result['best_terminal'] is not None:
        try:
            replay = replay_trace(model, [step['action'] for step in result.result['trace']])
            if replay['terminal_value'] != result.result['best_value']:
                raise ContractError('best route failed replay')
            replay['verified'] = True
        except UnsupportedSemantic as error:
            if result.graph['closed']:
                raise ContractError('closed-model witness became unsupported during replay') from error
            replay = {'verified': False, 'reason': str(error)}
    if result.graph['closed']:
        verified = verify_graph(model, result.graph)
    result.result['witness_replay_verified'] = bool(replay and replay.get('verified'))
    result.result['closure_check_verified'] = verified is not None
    args.out.mkdir(parents=True, exist_ok=True)
    write_json(args.out / 'result.json', result.result)
    write_json(args.out / 'graph.json', result.graph)
    write_json(args.out / 'exit-states.json', [result.graph['nodes'][i]['state'] for i in result.result['terminal_states']])
    if replay is not None: write_json(args.out / 'replay.json', replay)
    if verified is not None: write_json(args.out / 'verification.json', verified)
    lines = [result.result['status'], 'Scope: supplied reference/backend model; NOT verified STS2 semantics.',
             'Best value: ' + str(result.result['best_value']), 'Statistics: ' + str(result.result['statistics']), 'Route:']
    lines += [f"{i+1:02d}. {step['action']}" for i, step in enumerate(result.result['trace'])]
    (args.out / 'route.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    summary = {key: result.result[key] for key in ('status','identity','proven_optimal_in_model',
                'game_equivalence_verified','best_value','stop_reason','statistics')}
    summary['output_directory'] = str(args.out.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.graph['closed'] else 2


def parser():
    root = argparse.ArgumentParser(prog='python -m spire_exact', description='SpireExact: native STS2 data and exact information-policy kernels; whole-run native transitions in development.')
    commands = root.add_subparsers(dest='command', required=True)
    commands.add_parser('solve-p5', help='persistent native P0–P5 planner; use solve-p5 --help')
    demo = commands.add_parser('demo', help='solve the explicitly synthetic two-room reference model')
    demo.add_argument('--seed', default='42')
    add_limits(demo)
    solve_cmd = commands.add_parser('solve', help='solve a declarative reference IR or explicit graph file')
    solve_cmd.add_argument('model')
    solve_cmd.add_argument('--seed', default='42')
    add_limits(solve_cmd)
    backend = commands.add_parser('solve-backend', help='call an explicitly configured JSONL transition host')
    backend.add_argument('--config', required=True)
    add_limits(backend)
    check = commands.add_parser('verify', help='independently check closure against the model again')
    check.add_argument('model')
    check.add_argument('graph')
    check.add_argument('--seed', default='42')
    server = commands.add_parser('rpc-server', help='reference JSONL host; not CombatSolver')
    server.add_argument('--model', required=True)
    server.add_argument('--seed', default='42')
    server.add_argument('--max-successors', type=int, default=100_000)
    environment = commands.add_parser('doctor', help='local-only dependency inspection and binary hashing')
    for name in ('game-dir','ritsu-dir','upstream','mods-dir'):
        environment.add_argument('--'+name, type=Path)
    environment.add_argument('--out', type=Path, default=Path('outputs/environment.json'))
    fetch = commands.add_parser('fetch-upstream', help='fetch the pinned public source; does not execute or install it')
    fetch.add_argument('--destination', type=Path, default=Path('vendor/CombatSolver'))
    audit = commands.add_parser('audit-upstream', help='write a static audit index, not a correctness proof')
    audit.add_argument('--upstream', type=Path, default=Path('vendor/CombatSolver'))
    audit.add_argument('--out', type=Path, default=Path('outputs/upstream-audit.json'))
    build = commands.add_parser('build-native', help='build upstream and its offline harness; requires local game libraries')
    build.add_argument('--upstream', type=Path, default=Path('vendor/CombatSolver'))
    build.add_argument('--game-data-dir', type=Path, required=True)
    build.add_argument('--ritsu-dir', type=Path, required=True)
    build.add_argument('--out', type=Path, default=Path('outputs/native-build'))
    baseline = commands.add_parser('native-baseline', help='run the upstream BEAM baseline, never mark it exact')
    baseline.add_argument('--upstream', type=Path, default=Path('vendor/CombatSolver'))
    baseline.add_argument('--seed', default='42')
    baseline.add_argument('--request', type=Path)
    baseline.add_argument('--budget-ms', type=int, default=30_000)
    baseline.add_argument('--out', type=Path, default=Path('outputs/native-baseline'))
    seed = commands.add_parser('solve-seed', help='mode1: generate whole-run winning witnesses and verify in a fresh native process')
    seed.add_argument('seed')
    seed.add_argument('--mode', choices=['mode1'], default='mode1')
    seed.add_argument('--character', default='IRONCLAD')
    seed.add_argument('--ascension', type=int, default=0)
    seed.add_argument('--unlocks', choices=['all', 'none'], default='all')
    seed.add_argument('--game-dir', type=Path)
    seed.add_argument('--attempts', type=int, default=16)
    seed.add_argument('--max-decisions', type=int, default=3000)
    seed.add_argument('--max-seconds', type=int, default=120)
    seed.add_argument('--advisor', choices=['none', 'combatsolver'], default='none')
    seed.add_argument('--prefix', type=Path, help='untrusted earlier decision.json/route.json to replay before proposing more actions')
    seed.add_argument('--out', type=Path, default=Path('outputs/mode1'))
    mode1_check = commands.add_parser('verify-mode1', help='re-execute an entire mode1 winning route without heuristic/advisor code')
    mode1_check.add_argument('route', type=Path)
    mode1_check.add_argument('--out', required=True, type=Path)
    mode1_check.add_argument('--game-dir', type=Path)
    for name in ('native-catalog', 'native-seed', 'native-score'):
        native = commands.add_parser(name, help='read data and rules directly from installed sts2.dll')
        native.add_argument('--game-dir', type=Path)
        native.add_argument('--out', type=Path, required=True)
        if name == 'native-seed':
            native.add_argument('seed')
            native.add_argument('--character', default='IRONCLAD')
            native.add_argument('--ascension', type=int, default=0)
            native.add_argument('--unlocks', choices=['all', 'none'], default='all')
        elif name == 'native-score':
            native.add_argument('--save', type=Path, required=True)
            native.add_argument('--victory', action='store_true')
    comparison = commands.add_parser('compare-native', help='compare game API and data across updates')
    comparison.add_argument('old', type=Path)
    comparison.add_argument('new', type=Path)
    policy = commands.add_parser('solve-policy', help='exact finite-prior policy; requires an explicit model')
    policy.add_argument('model', type=Path)
    policy.add_argument('--mode', choices=['hidden', 'omniscient'], default='hidden')
    policy.add_argument('--world')
    add_limits(policy)
    return root


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == 'solve-p5':
        from .planning.__main__ import main as planning_main
        planning_main(arguments[1:])
        return 0
    args = parser().parse_args(arguments)
    try:
        if args.command == 'demo':
            return publish_search(SymbolicModel.from_file(str(ROOT/'examples/reference_campaign.json'), args.seed), args)
        if args.command == 'solve': return publish_search(load_model(args.model, args.seed), args)
        if args.command == 'solve-backend':
            with RpcModel.from_config(args.config) as model:
                return publish_search(model, args)
        if args.command == 'verify':
            print(json.dumps(verify_graph(load_model(args.model, args.seed), read_json(args.graph)), indent=2))
            return 0
        if args.command == 'rpc-server':
            serve(load_model(args.model, args.seed), args.max_successors)
            return 0
        if args.command == 'doctor':
            result = doctor(args.game_dir, args.ritsu_dir, args.upstream, args.mods_dir)
            write_json(args.out, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['native_dependencies_found'] else 2
        if args.command.startswith('native-') and args.command != 'native-baseline':
            command = args.command.removeprefix('native-')
            params = {}
            if command == 'seed':
                if args.ascension < 0: raise ContractError('ascension must be nonnegative')
                params = dict(seed=args.seed, character=args.character, ascension=args.ascension, unlocks=args.unlocks)
            elif command == 'score':
                params = dict(save=str(args.save.resolve()), victory=args.victory)
            result = export_native(command, args.out, args.game_dir, **params)
            print(json.dumps({'status': result['status'], 'out': str(args.out.resolve()),
                              'proven_optimal': False}, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'compare-native':
            print(json.dumps(compare_exports(args.old, args.new), ensure_ascii=False, indent=2))
            return 0
        if args.command == 'verify-mode1':
            result = verify_mode1(args.route, args.out, args.game_dir)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'solve-policy':
            if args.out.exists() and any(args.out.iterdir()):
                raise ContractError('output directory must be empty')
            result = solve_information(read_json(args.model), args.mode, args.world,
                Limits(args.max_states, args.max_expansions, args.max_edges, args.max_seconds))
            args.out.mkdir(parents=True, exist_ok=True)
            write_json(args.out / 'policy.json', result)
            print(json.dumps({k: v for k, v in result.items() if k not in ('policy', 'initial_observations')},
                             ensure_ascii=False, indent=2))
            return 0 if result['proven_optimal_in_model'] else 2
        if args.command == 'fetch-upstream':
            result = fetch_upstream(args.destination)
            write_json(Path('outputs/upstream-resolved.json'), result)
        elif args.command == 'audit-upstream':
            result = audit_source(args.upstream)
            write_json(args.out, result)
        elif args.command == 'build-native':
            result = build_native(args.upstream, args.game_data_dir, args.ritsu_dir, args.out)
        elif args.command == 'native-baseline':
            if args.budget_ms <= 0: raise ContractError('--budget-ms must be positive')
            result = native_baseline(args.upstream, args.out, args.seed, args.request, args.budget_ms)
            result = {key: value for key, value in result.items() if key != 'harness_result'}
        elif args.command == 'solve-seed':
            ctx = mode1_context(args.seed, args.character, args.ascension, args.unlocks)
            def progress(record):
                print(json.dumps({'candidate': record['policy'], 'status': record['status'],
                    'floor': (record.get('observation') or {}).get('floor'),
                    'verification': record.get('verification')}, ensure_ascii=False), flush=True)
            result = solve_mode1(ctx, args.out, CandidateBudget(args.attempts, args.max_decisions, args.max_seconds),
                                args.game_dir, progress=progress, advisor=args.advisor,
                                initial_prefix=read_json(args.prefix)['trace'] if args.prefix else None)
            print(json.dumps({k: v for k, v in result.items() if k != 'attempts'}, ensure_ascii=False, indent=2))
            return 0 if result['optimal_in_backend'] else 2
        else: raise ContractError('unknown command')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ContractError, UnsupportedSemantic, OSError, ValueError, KeyError) as error:
        print(json.dumps({'status':'ERROR', 'error':str(error), 'proven_optimal':False}, ensure_ascii=False), file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
