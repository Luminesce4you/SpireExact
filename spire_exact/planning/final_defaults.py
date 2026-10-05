"""User-authorized i081 entry defaults. Trigger checks remain in modules.

The legacy profile is explicit for comparisons; it is not the final entry's
default. Keep the previously separate B experiment unchanged.
"""
FINAL_FEATURES = {
    'f1_winner_reuse': True,
    'prefer_f1_hp': True,
    'gold_shop_routes': True,
    'low_hp_routes': True,
    'lean_third_act': True,
    'shop_preparation': True,
    'resource_telemetry': True,
    'memory_telemetry': True,
    'preserve_completed_prefix': True,
}

# The user's i081 request explicitly enables the four new switches. Their
# prerequisites and sample thresholds still govern when they take effect.
I081_FEATURES = {
    'f2_readiness_probes': True,
    'f2_dead_retry': True,
    'f2_joint_focus': True,
    'f2_joint_model': True,
}
F2_READINESS_EVERY_DEFAULT = 20  # Taskbook estimate, not a measured optimum.

# i082 (user 2026-10-04: "改成i082 / 然后把这些功能都默认开启"). The i081 command
# template lost every i080 flag that had been passed explicitly; this one table
# is the whole entry next to the i075 nine and i081 four above. Planner and
# run_seed both read it. dest: (i082 value, value of every other profile = the
# former argparse default, so i081 / i075-final / legacy resolve unchanged).
I082_PLANNER = {
    'scheduler': ('focus', 'weighted'),
    'prior': (True, False),
    'gate_preset': ('escalate-evaluate', 'none'),
    'repair_mode': ('gate', 'fifo'),
    'normal_nodes': (10000, None),
    'runtime_profile': ('server-bounded-large-gen0', 'legacy'),
    'worker_memory_mib': (1792, 900),
    'root_policies': ('pick,elo', ''),
    'focus_cluster_cap': (2, 0),
    'final_gate_plan': ('open', 'none'),
    'root_async': (True, False),
    'requeue_lost': (1, 0),
    'focus_stall': (32, 0),
    'focus_stall_extended': (True, False),
    'focus_stall_f2': (True, False),
    'low_hp_routes_any_act': (True, False),
    'paired_card_every': (32, 0),
    'paired_card_dedup': (True, False),
    'paired_card_merge': ('mean', 'latest'),
    # Not rendered by i082_argv: in i082 it follows the joint model, so an
    # explicit --no-f2-joint-model needs no second flag.
    'paired_card_joint': (True, False),
}
I085_PLANNER = {
    "tail_mode": ("shadow", "off"),
    'macro_plateau': (True, False),
    'macro_widening': (True, False),
    'macro_fair': (True, False),
    # Compound routes lack native efficacy evidence. Explicit 'on' is an arm.
    'macro_routes': ('shadow', 'off'),
}
FEATURE_PROFILES = ('i082', 'i081', 'i075-final', 'legacy', 'i085')


def i085_argv():
    argv = i082_argv()
    for key, (value, _) in I085_PLANNER.items():
        flag = '--' + key.replace('_', '-')
        argv += [flag] if value is True else [flag, str(value)]
    return argv


def i082_argv():
    """The i082 entry as explicit planner arguments. tools/run_seed.py puts them
    after its --profile arguments and before the user's flags and --extra."""
    argv = ['--' + key.replace('_', '-') for key, on in (FINAL_FEATURES | I081_FEATURES).items() if on]
    for key, (value, _) in I082_PLANNER.items():
        if key == 'paired_card_joint':
            continue
        flag = '--' + key.replace('_', '-')
        argv += [flag] if value is True else ['--no-' + flag[2:]] if value is False else [flag, str(value)]
    return argv


def resolve_entry_defaults(args):
    final = args.feature_profile in ('i075-final','i081','i082','i085')
    for key, on in FINAL_FEATURES.items():
        if getattr(args, key) is None:
            setattr(args, key, on if final else False)
    for key,on in I081_FEATURES.items():
        if getattr(args,key,None)is None:
            setattr(args,key,on if args.feature_profile in ('i081','i082','i085') else False)
    i082 = args.feature_profile in ('i082','i085')
    for key, (value, other) in I082_PLANNER.items():
        if getattr(args, key, None) is None:
            if key == 'paired_card_joint' and i082:
                value = bool(getattr(args, 'f2_joint_model', False) and getattr(args, 'f2_readiness_probes', False)
                             and getattr(args, 'paired_card_probes', True))
            setattr(args, key, value if i082 else other)
    for key, (value, other) in I085_PLANNER.items():
        if getattr(args, key, None) is None:
            setattr(args, key, value if args.feature_profile == 'i085' else other)
    if args.ascension is None:
        args.ascension = 10 if final else 0
    if args.nodes is None and final:
        args.nodes = 60000
    return args
