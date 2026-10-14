"""G3 safeguard 1 (g2/plan.md section 6): static list of model instance state the save does not carry.

Scans the decompiled game source (experiments/g1-rng/decompiled, v0.111.0) for every model class
under Relics / Cards / Enchantments / Potions / Powers / Afflictions / Modifiers, plus their base
classes, and lists each instance field or settable auto-property that

  - is not reachable from a [SavedProperty] property (the run save serialises only those), and
  - is written by some method other than the constructor / clone / creation hooks.

Each entry records the writing methods and a tier (first match wins):

  test    every later writer is a test / mock hook (Mock*, SetTest*, NeverEverCallThisOutsideOfTests_*)
          or the type is a Fake* / Mock* model: not reachable in a real run.
  visual  `_isActivating` written only by DoActivateVisuals: an animation flag.
  B       some later writer is a combat-boundary hook (BeforeCombatStart(Late) / AfterCombatStart /
          AfterCombatEnd / BeforeCombatEnd / AfterCombatVictory(Early)): the field is re-initialised
          around every combat, so the value carried across a map boundary is overwritten before
          the next combat reads it.
  R       some later writer is a turn-start or room-entry hook (BeforeSideTurnStart / AfterSideTurnStart /
          AfterEnergyReset / BeforeRoomEntered / AfterRoomEntered): reset before the first player turn.
  A       none of the above: a value can be carried across a map boundary and is lost by a
          checkpoint restore (BingBong._cardsToSkip is the case found by the fork check).
  lazy    only null-guarded fills (`x ?? (x = ...)`, `if (x == null) x = ...`): a derived cache,
          covered by the fork check's `forced` block.

Entries on the base classes (AbstractModel, CardModel, RelicModel, ...) are listed separately: every
door carries them, so the 20-door fork check (all deck cards, relics and potions dumped field by field,
`forced` block included) already compares them; they are not routing keys. Card and enchantment
state written in combat lives on the combat clone, which is discarded at combat end; the scan cannot
tell clone writes from deck writes, so those entries stay tier A (conservative).

This is a text-level over-approximation (regex over decompiled C#, no semantic model). An entry
is a reason to take the scratch-replay path, not a proof of divergence. `route_to_scratch` holds the
non-base, non-combat-only types with a tier A entry; `route_to_scratch_conservative` adds B and R.
The run-time 10% scratch spot check (g2/plan.md section 6) is the backstop for both.

Usage: python tools/unsaved_state_scan.py [--out ../g2/unsaved-state.json]
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import time

EXPERIMENTS = Path(__file__).resolve().parents[2]
SOURCE = EXPERIMENTS / 'g1-rng/decompiled'
FOLDERS = {'Relics': 'relic', 'Cards': 'card', 'Enchantments': 'enchantment', 'Potions': 'potion',
           'Powers': 'power', 'Afflictions': 'affliction', 'Modifiers': 'modifier'}
BASES = ('AbstractModel', 'RelicModel', 'CardModel', 'EnchantmentModel', 'PotionModel', 'PowerModel',
         'AfflictionModel', 'ModifierModel')
# Powers are removed at every combat end (Player.AfterCombatEnd -> Creature.RemoveAllPowersInternalExcept,
# Player.cs:837-839), so their state never reaches a map-boundary checkpoint.
COMBAT_ONLY = {'power'}
CREATION = re.compile(r'^(AfterCreated|OnCreated|Initialize\w*|DeepClone\w*|Clone\w*|MutableClone\w*|CopyFrom\w*|'
                      r'AfterCloned|AfterDeserialized\w*|LoadFrom\w*|SetUpFromSave\w*)$')
TURN_RESET = re.compile(r'^(BeforeSideTurnStart|AfterSideTurnStart|AfterEnergyReset|BeforeRoomEntered|AfterRoomEntered)$')
TEST = re.compile(r'^(Mock\w*|SetTest\w*|NeverEverCallThisOutsideOfTests\w*)$')
COMBAT_RESET = re.compile(r'^(BeforeCombatStart|BeforeCombatStartLate|AfterCombatStart|AfterCombatEnd|'
                          r'BeforeCombatEnd|AfterCombatVictory|AfterCombatVictoryEarly)$')
MODIFIERS = r'(?:(?:public|private|protected|internal|readonly|static|const|volatile|new|override|virtual|abstract|sealed|unsafe|required|extern)\s+)*'
FIELD = re.compile(r'^\s*' + MODIFIERS + r'(?P<type>[\w<>\[\]?,.\s()]+?)\s+(?P<name>[A-Za-z_]\w*)\s*(?:=.*)?;?\s*$')
AUTO = re.compile(r'^\s*' + MODIFIERS + r'(?P<type>[\w<>\[\]?,.\s()]+?)\s+(?P<name>[A-Za-z_]\w*)\s*\{\s*(?:\w+\s+)?get;\s*(?:(?:private|protected|internal)\s+)?(?:set|init);\s*\}')
HEADER_NAME = re.compile(r'([A-Za-z_]\w*)\s*(?:<[^()]*>)?\s*\(')
CLASS = re.compile(r'\bclass\s+(\w+)(?:<[^>]*>)?\s*(?::\s*([\w.]+))?')


def members(text):
    """Top-level members of the first class body: (header, body, attributes) at brace depth 1."""
    m = CLASS.search(text)
    if not m:
        return None, None, []
    start = text.index('{', m.end())
    out, i, depth, buf, attrs = [], start + 1, 1, '', []
    while i < len(text) and depth > 0:
        ch = text[i]
        if depth == 1 and ch == '[' and not buf.strip():
            j = text.index(']', i); attrs.append(text[i + 1:j]); i = j + 1; continue
        if ch == '/' and text.startswith('///', i) or ch == '/' and text.startswith('//', i):
            j = text.find('\n', i); i = len(text) if j < 0 else j; continue
        if depth == 1 and ch == '{':
            # member with a body: find the matching brace
            k, d = i, 0
            while k < len(text):
                if text[k] == '{': d += 1
                elif text[k] == '}':
                    d -= 1
                    if d == 0: break
                k += 1
            body = text[i:k + 1]
            # expression tail like `{ get; set; } = x;`
            rest = text[k + 1:k + 200]
            tail = re.match(r'\s*=[^;]*;', rest)
            out.append((buf.strip(), body, attrs)); buf, attrs = '', []
            i = k + 1 + (tail.end() if tail else 0); continue
        if depth == 1 and ch == ';':
            out.append((buf.strip(), '', attrs)); buf, attrs = '', []
            i += 1; continue
        if ch == '}':
            depth -= 1
        buf += ch; i += 1
    return m.group(1), m.group(2), out


def parse(path):
    text = path.read_text(encoding='utf-8', errors='replace')
    name, base, items = members(text)
    if not name:
        return None
    fields, props, methods = {}, {}, []
    for header, body, attrs in items:
        saved = any(a.strip().startswith('SavedProperty') for a in attrs)
        one = ' '.join(header.split())
        if not body and '=>' not in one and '(' not in one.split('=')[0]:
            f = FIELD.match(one)
            if f and ' static ' not in ' ' + one + ' ' and ' const ' not in ' ' + one + ' ':
                fields[f['name']] = {'type': ' '.join(f['type'].split()), 'readonly': ' readonly ' in ' ' + one + ' '}
            continue
        auto = AUTO.match(one + ' ' + ' '.join(body.split()))
        if auto and ' static ' not in ' ' + one + ' ':
            props[auto['name']] = {'saved': saved, 'auto': True, 'body': ''}
            continue
        pre = one.split('=>')[0]
        if '(' in pre and not re.search(r'\bthis\s*\[', pre):
            h = HEADER_NAME.findall(pre)
            if h:
                methods.append((h[-1], body + one[len(pre):]))
            continue
        pname = re.findall(r'([A-Za-z_]\w*)\s*$', pre.strip())
        if pname and ' static ' not in ' ' + one + ' ':
            props[pname[0]] = {'saved': saved, 'auto': False, 'body': body + one[len(pre):]}
    return {'name': name, 'base': base.split('.')[-1] if base else None, 'path': path, 'fields': fields,
            'props': props, 'methods': methods, 'text': text}


def writes(code, name):
    n = re.escape(name)
    # a private `_x` can only be written from its own class, also through another instance (`other._x = ...`)
    lead = r'(?<![\w])(?:\w+\.)?' if name.startswith('_') else r'(?<![\w.])(?:this\.|base\.)?'
    pats = [rf'{lead}{n}\s*(?:\[[^\]]*\]\s*)?(?:=(?!=)|\+=|-=|\*=|/=|%=|\|=|&=|\^=|<<=|>>=|\?\?=|\+\+|--)',
            rf'(?:\+\+|--)\s*(?:this\.|base\.)?{n}\b',
            rf'{lead}{n}\s*\.\s*(?:Add|AddRange|Remove|RemoveAt|RemoveAll|RemoveWhere|Clear|Insert|Push|Pop|Enqueue|Dequeue|TryAdd|UnionWith|ExceptWith|IntersectWith|Sort|Reverse)\s*\(',
            rf'\bref\s+(?:this\.)?{n}\b', rf'\bout\s+(?:this\.)?{n}\b']
    for p in pats:
        for m in re.finditer(p, code):
            # `new T { Name = ... }` object initialisers set a member of another object
            before = code[:m.start()].rstrip()
            if before.endswith(('{', ',')) and re.search(r'new[^;{}]*\{[^;{}]*$', code[:m.start()]):
                continue
            return True
    return False


def lazy_only(code, name):
    """Every write is a null-guarded fill."""
    n = re.escape(name)
    stripped = re.sub(rf'{n}\s*\?\?\s*\(\s*{n}\s*=', '', code)
    stripped = re.sub(rf'{n}\s*\?\?=', '', stripped)
    stripped = re.sub(rf'if\s*\(\s*{n}\s*==\s*null\s*\)\s*\{{?\s*{n}\s*=', 'if(', stripped)
    return not writes(stripped, name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=EXPERIMENTS / 'g2/unsaved-state.json')
    args = parser.parse_args()
    classes, files = {}, []
    for folder in ['MegaCrit.Sts2.Core.Models'] + [f'MegaCrit.Sts2.Core.Models.{f}' for f in FOLDERS]:
        for path in sorted((SOURCE / folder).glob('*.cs')):
            if folder == 'MegaCrit.Sts2.Core.Models' and path.stem not in BASES:
                continue
            c = parse(path)
            if c:
                c['kind'] = FOLDERS.get(folder.rsplit('.', 1)[-1], 'base')
                classes[c['name']] = c
                files.append(path)

    def chain(c):
        out = [c]
        while out[-1]['base'] in classes and out[-1]['base'] != out[-1]['name']:
            out.append(classes[out[-1]['base']])
        return out

    entries, saved_count = [], 0
    for c in classes.values():
        lineage = chain(c)
        saved_bodies = ' '.join(p['body'] for x in lineage for p in x['props'].values() if p['saved'])
        state = [(n, info, False) for n, info in c['fields'].items()] + \
                [(n, {'type': 'auto-property'}, True) for n, p in c['props'].items() if p['auto'] and not p['saved']]
        saved_count += sum(p['saved'] for p in c['props'].values())
        # wrapper properties (getter or setter touching the field) count as the field
        for name, info, is_auto in state:
            if not is_auto and re.search(rf'\b{re.escape(name)}\b', saved_bodies):
                continue
            aliases = [name] + [p for p, d in c['props'].items() if not d['auto'] and re.search(rf'\b{re.escape(name)}\b', d['body'])]
            if any(c['props'].get(a, {}).get('saved') for a in aliases[1:]):
                continue
            writers, lazy_writers = set(), set()
            # writes from this class, and from subclasses for non-private base fields
            scopes = [c] + ([x for x in classes.values() if c in chain(x)[1:]] if c['kind'] == 'base' else [])
            for scope in scopes:
                for mname, code in scope['methods']:
                    hit = [a for a in aliases if writes(code, a)]
                    if not hit:
                        continue
                    label = mname if scope is c else f'{scope["name"]}.{mname}'
                    (lazy_writers if all(lazy_only(code, a) for a in hit) else writers).add(label)
                for pname, d in scope['props'].items():
                    if not d['auto'] and pname not in aliases and any(writes(d['body'], a) for a in aliases):
                        label = f'{pname}(property)' if scope is c else f'{scope["name"]}.{pname}(property)'
                        (lazy_writers if all(lazy_only(d['body'], a) for a in aliases) else writers).add(label)
                for pname in aliases[1:]:
                    d = c['props'].get(pname)
                    if d and re.search(r'\bset\b|\binit\b', d['body']) and writes(d['body'], name):
                        if not lazy_only(d['body'], name):
                            setter_users = [m for m, code in scope['methods'] if writes(code, pname)]
                            for m in setter_users:
                                writers.add(m if scope is c else f'{scope["name"]}.{m}')
            ctor = {c['name']} | {x['name'] for x in scopes}
            later = {w for w in writers if w.split('.')[-1] not in ctor and not CREATION.match(w.split('.')[-1])}
            if not later and not lazy_writers:
                continue
            last = {w.split('.')[-1].replace('(property)', '') for w in later}
            if not later:
                tier = 'lazy'
            elif all(TEST.match(w) for w in last) or re.match(r'^(Fake|Mock)', c['name']):
                tier = 'test'
            elif name == '_isActivating' and last == {'DoActivateVisuals'}:
                tier = 'visual'
            elif any(COMBAT_RESET.match(w) for w in last):
                tier = 'B'
            elif any(TURN_RESET.match(w) for w in last):
                tier = 'R'
            else:
                tier = 'A'
            entries.append({'type': c['name'], 'kind': c['kind'], 'field': name, 'field_type': info.get('type'),
                            'tier': tier, 'combat_only': c['kind'] in COMBAT_ONLY,
                            'writers': sorted(later), 'lazy_writers': sorted(lazy_writers),
                            'source': str(c['path'].relative_to(SOURCE)).replace('\\', '/')})
    entries.sort(key=lambda e: (e['kind'], e['type'], e['field']))
    keys = [e for e in entries if e['kind'] != 'base' and not e['combat_only']]
    route = sorted({e['type'] for e in keys if e['tier'] == 'A'})
    conservative = sorted({e['type'] for e in keys if e['tier'] in ('A', 'B', 'R')})
    digest = hashlib.sha256(b''.join(p.read_bytes() for p in sorted(files))).hexdigest()
    report = {'schema': 'g2-unsaved-state/1', 'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
              'author': 'refactor main thread (Claude), tools/unsaved_state_scan.py',
              'purpose': __doc__.strip().splitlines()[0], 'game_source': 'experiments/g1-rng/decompiled (v0.111.0)',
              'source_sha256': digest, 'classes_scanned': len(classes), 'saved_properties_seen': saved_count,
              'rule': 'a door whose player owns (relic, deck card, card enchantment, potion, run modifier) any type in '
                      'route_to_scratch takes the scratch-replay path for result and cost; base-class entries are '
                      'compared by the fork check on every door, not routing keys; combat_only entries cannot reach a '
                      'map-boundary checkpoint',
              'counts': {t: sum(e['tier'] == t for e in entries) for t in ('A', 'B', 'R', 'visual', 'test', 'lazy')},
              'route_to_scratch': route, 'route_to_scratch_conservative': conservative,
              'base_class_entries': [e for e in entries if e['kind'] == 'base'],
              'entries': [e for e in entries if e['kind'] != 'base']}
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(args.out, report['counts'], 'classes', len(classes), 'route types', len(route), 'conservative', len(conservative))


if __name__ == '__main__':
    main()
