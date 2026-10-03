"""Narrow, fail-closed transformations of explicitly pinned upstream sources."""
import re

LAZY_HOOK_METHODS = (
    'BeforeBlockGained', 'AfterBlockGained', 'AfterStarsGained', 'ShouldDraw',
    'AfterCardExhausted', 'ModifyShuffleOrder', 'AfterShuffle', 'AfterCardDiscarded',
    'ShouldPlay', 'AfterModifyingCardPlayCount', 'BeforeCardPlayed', 'AfterDamageGiven',
    'BeforeDamageReceived', 'AfterDamageReceived', 'BeforeAttack', 'AfterOrbChanneled',
    'AfterOrbEvoked', 'BeforeDeath', 'AfterDeath',
)


def lazy_hook_contexts(source):
    """Retain each loop; only move pure argument-copy context creation inside it."""
    source = source.replace('\r\n', '\n')
    changed = []
    for name in LAZY_HOOK_METHODS:
        starts = list(re.finditer(r'    public static [^\n]+\b'+name+r'\(', source))
        if len(starts) != 1:
            raise ValueError('Pinned hook method changed: '+name)
        start = starts[0].start()
        end = source.find('\n    public static ', start+1)
        end = len(source) if end == -1 else end
        section = source[start:end]
        constructors = list(re.finditer(r'        var context = new (\w+)\s*(\{[^{}]*\});', section))
        if len(constructors) != 1:
            raise ValueError('Pinned hook context changed: '+name)
        constructor = constructors[0]
        context_type, initializer = constructor.groups()
        # Reject properties, calls, operators or additional initializer effects.
        for assignment in initializer[1:-1].strip().split(','):
            if not re.fullmatch(r'\s*\w+\s*=\s*\w+\s*', assignment):
                raise ValueError('Context initializer is not a pure argument copy: '+name)
        declaration = f'        {context_type}? context = null;'
        section = section[:constructor.start()]+declaration+section[constructor.end():]
        loop = r'(        foreach \(var listener in Iterate(?:Combat|Run)HookListeners\(simulator, MirroredHookMask\.\w+\)\)\n        \{\n)'
        hits = list(re.finditer(loop, section))
        expected = 2 if name == 'AfterDamageReceived' else 1
        if len(hits) != expected:
            raise ValueError('Pinned listener loop changed: '+name)
        declaration_end = constructor.start()+len(declaration)
        if 'context' in section[declaration_end:hits[0].start()]:
            raise ValueError('Context observed before first listener: '+name)
        init = '\n'.join('    '+line for line in ('        context ??= new '+context_type+' '+initializer+';').splitlines())
        section = re.sub(loop, lambda m: m.group(0)+init+'\n', section)
        source = source[:start]+section+source[end:]
        changed.append({'method': name, 'context_type': context_type, 'loops': expected})
    return source, changed


def indexed_hook_layouts(layout_source, hook_source):
    """Index existing immutable masks; leave the fallback and participation rules."""
    layout_source = layout_source.replace('\r\n', '\n')
    hook_source = hook_source.replace('\r\n', '\n')
    anchor = '    internal Entry[] Entries { get; } = entries;\n'
    addition = '''    // Type-only membership, shared with the immutable layout across forks.
    // No receiver or game-state identity is stored in this index.
    private readonly ulong[]? _singleHookMatches = BuildHookMatches(entries);

    private static ulong[]? BuildHookMatches(Entry[] entries)
    {
        if (entries.Length == 0 || entries.Length > 64)
            return null;
        ulong[] matches = new ulong[64];
        for (int index = 0; index < entries.Length; index++)
        {
            ulong bits = (ulong)entries[index].Mask;
            while (bits != 0)
            {
                int hook = System.Numerics.BitOperations.TrailingZeroCount(bits);
                matches[hook] |= 1UL << index;
                bits &= bits - 1;
            }
        }
        return matches;
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    internal int FindNextMatch(int start, MirroredHookMask mask)
    {
        // Check before shifting: CLR shifts by 64 are masked back to zero.
        if (start >= Entries.Length)
            return -1;
        ulong bits = (ulong)mask;
        if (_singleHookMatches is { } matches && bits != 0 && (bits & (bits - 1)) == 0)
        {
            ulong remaining = matches[System.Numerics.BitOperations.TrailingZeroCount(bits)]
                & (ulong.MaxValue << start);
            return remaining == 0 ? -1 : System.Numerics.BitOperations.TrailingZeroCount(remaining);
        }
        for (int index = start; index < Entries.Length; index++)
            if ((Entries[index].Mask & mask) != 0)
                return index;
        return -1;
    }
'''
    before = '''                    while (next < filtered.Layout.Entries.Length)
                    {
                        if ((filtered.Layout.Entries[next].Mask & mask) != 0)
                        {
                            _index = next;
                            return true;
                        }
                        next++;
                    }'''
    after = '''                    int matching = filtered.Layout.FindNextMatch(next, mask);
                    if (matching >= 0)
                    {
                        _index = matching;
                        return true;
                    }'''
    if layout_source.count(anchor) != 1 or hook_source.count(before) != 1:
        raise ValueError('Pinned hook layout/enumerator contract changed')
    return layout_source.replace(anchor, anchor+addition), hook_source.replace(before, after)
