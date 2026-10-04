"""Pure protocol/archive/cache counterexamples; no native restore or process."""
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from spire_exact.canonical import ContractError,canonical
from spire_exact.mode1 import context
from spire_exact.planning.archive import CheckpointArchive,ResultCache
from spire_exact.planning.io import write_json
from spire_exact.planning.research_progress import (
    require_research_progress,checkpoint_progress_matches,SCHEMA,SNAPSHOT_SCHEMA)

CTX=context('42','IRONCLAD',10,'all')
NATIVE_CTX={**{k:CTX[k] for k in ('seed','character','ascension','unlocks')},
            'information':'full','objective':'whole_run_victory/v1'}
GAME='a'*64
ID={'schema':'spire-native-identity/v1','host_sha256':'b'*64,'game_sha256':GAME}


def sha(raw):return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def baseline(raw='{"schema_version":0,"discovered_cards":["药水"],"ratio":1.2500}'):
    return {'schema':SCHEMA,'game_sha256':GAME,'native_identity':deepcopy(ID),
            'context':deepcopy(NATIVE_CTX),'baseline_sha256':sha(raw),'baseline_snapshot':raw}


def payload(history=None,progress=None,raw='{"schema_version":0,"discovered_relics":["R"],"count":2}'):
    history=[{'kind':'map','col':0,'row':1}] if history is None else history
    progress=baseline() if progress is None else progress
    return {'context':deepcopy(NATIVE_CTX),'identity':deepcopy(ID),'history':deepcopy(history),
            'evidence':[{} for _ in history],'observation':{'act':2,'floor':len(history)},
            'native_progress_snapshot':{'schema':SNAPSHOT_SCHEMA,'game_sha256':GAME,
                'native_identity':deepcopy(ID),'context':deepcopy(NATIVE_CTX),
                'baseline_sha256':progress['baseline_sha256'],'native_sha256':sha(raw),'native_json':raw}}


class ResearchProgressProtocolTests(unittest.TestCase):
    def test_validation_keeps_raw_decimal_unicode_and_deep_copies_metadata(self):
        source=baseline();checked=require_research_progress(source,CTX,ID,GAME)
        self.assertEqual(checked,source);self.assertIsNot(checked,source)
        source['native_identity']['host_sha256']='changed';source['context']['seed']='wrong'
        self.assertEqual(checked['native_identity'],ID);self.assertEqual(checked['context'],NATIVE_CTX)
        self.assertIn('1.2500',checked['baseline_snapshot'])
        self.assertEqual(checked['baseline_sha256'],sha(checked['baseline_snapshot']))

    def test_baseline_rejects_empty_missing_null_and_wrong_protocol_fields(self):
        for invalid in (None,{},[],False):
            with self.subTest(invalid=invalid),self.assertRaises(ContractError):
                require_research_progress(invalid,CTX,ID)
        for field in baseline():
            for operation in ('missing','null'):
                invalid=baseline()
                if operation=='missing':invalid.pop(field)
                else:invalid[field]=None
                with self.subTest(field=field,operation=operation),self.assertRaises(ContractError):
                    require_research_progress(invalid,CTX,ID)
        invalid=baseline();invalid['schema']='spire-research-progress/v2'
        with self.assertRaises(ContractError):require_research_progress(invalid,CTX,ID)

    def test_binding_rejects_other_seed_objective_game_and_binary(self):
        for path,value in ((('context','seed'),'43'),(('context','ascension'),True),
                (('context','objective'),'score/v1'),(('context','information'),'partial'),
                (('native_identity','host_sha256'),'c'*64),(('game_sha256',),'c'*64)):
            invalid=baseline();owner=invalid
            for key in path[:-1]:owner=owner[key]
            owner[path[-1]]=value
            with self.subTest(path=path),self.assertRaises(ContractError):
                require_research_progress(invalid,CTX,ID)
        with self.assertRaises(ContractError):require_research_progress(baseline(),CTX,ID,'c'*64)

    def test_raw_hash_binds_original_bytes_not_reformatted_json(self):
        old=baseline('{"a":1,"b":2}')
        changed=deepcopy(old);changed['baseline_snapshot']='{ "b": 2, "a": 1 }'
        with self.assertRaises(ContractError):require_research_progress(changed,CTX,ID)
        changed['baseline_sha256']=sha(changed['baseline_snapshot'])
        self.assertEqual(require_research_progress(changed,CTX,ID),changed)
        self.assertNotEqual(changed['baseline_sha256'],old['baseline_sha256'])

    def test_malformed_native_json_is_not_a_valid_raw_baseline(self):
        for raw in ('','null','[]','{"x":NaN}','{"x":1,"x":2}','{"x":1,}'):
            invalid=baseline(raw)
            with self.subTest(raw=raw),self.assertRaises(ContractError):
                require_research_progress(invalid,CTX,ID)

    def test_checkpoint_missing_snapshot_or_malformed_binding_is_ineligible(self):
        valid=payload();self.assertTrue(checkpoint_progress_matches(valid,baseline(),CTX,ID))
        for value in (None,{},[],False):
            invalid=deepcopy(valid);invalid['native_progress_snapshot']=value
            self.assertFalse(checkpoint_progress_matches(invalid,baseline(),CTX,ID))
        invalid=deepcopy(valid);invalid.pop('native_progress_snapshot')
        self.assertFalse(checkpoint_progress_matches(invalid,baseline(),CTX,ID))
        for field in valid['native_progress_snapshot']:
            invalid=deepcopy(valid);invalid['native_progress_snapshot'].pop(field)
            with self.subTest(field=field):
                self.assertFalse(checkpoint_progress_matches(invalid,baseline(),CTX,ID))

    def test_checkpoint_changes_to_state_baseline_context_or_binary_are_rejected(self):
        mutations=[('native_json','{"count":3}'),('native_sha256','c'*64),
                   ('baseline_sha256','c'*64),('schema','future'),('game_sha256','c'*64)]
        for field,value in mutations:
            invalid=payload();invalid['native_progress_snapshot'][field]=value
            with self.subTest(field=field):
                self.assertFalse(checkpoint_progress_matches(invalid,baseline(),CTX,ID))
        for outer in ('context','identity'):
            invalid=payload();invalid[outer]={}
            self.assertFalse(checkpoint_progress_matches(invalid,baseline(),CTX,ID))
        invalid=payload();invalid['native_progress_snapshot']['context']['ascension']=True
        self.assertFalse(checkpoint_progress_matches(invalid,baseline(),CTX,ID))
        invalid=payload();invalid['native_progress_snapshot']['native_identity']['host_sha256']='c'*64
        self.assertFalse(checkpoint_progress_matches(invalid,baseline(),CTX,ID))


class ResearchCheckpointArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name);self.progress=baseline()
        self.archive=CheckpointArchive(CTX,ID,research_progress=self.progress)
    def tearDown(self):self.temp.cleanup()
    def write(self,name,history,*,legacy=False,document=None):
        row=payload(history,self.progress)
        if legacy:row.pop('native_progress_snapshot')
        doc=document or {'schema':'spire-map-checkpoint/v1','payload':row,'sha256':'native-checks-outer-hash'}
        path=self.root/name;write_json(path,doc);return path

    def test_research_constructor_empty_object_cannot_silently_disable_guard(self):
        with self.assertRaises(ContractError):CheckpointArchive(CTX,ID,research_progress={})

    def test_longest_legacy_checkpoint_cannot_hide_shorter_valid_prefix(self):
        first={'a':1};second={'b':2};query=[first,second,{'c':3}]
        shorter=self.write('short.json',[first]);longer=self.write('old.json',[first,second],legacy=True)
        self.assertTrue(self.archive.add(shorter));self.assertFalse(self.archive.add(longer))
        chosen=self.archive.nearest(query)
        self.assertEqual(chosen.path,shorter.resolve());self.assertEqual(chosen.prefix_length,1)
        self.assertTrue(longer.exists());self.assertEqual(len(self.archive.entries),1)

    def test_legacy_same_prefix_cannot_replace_valid_checkpoint(self):
        history=[{'a':1}];valid=self.write('valid.json',history);old=self.write('old.json',history,legacy=True)
        self.assertTrue(self.archive.add(valid));self.assertFalse(self.archive.add(old))
        self.assertEqual(self.archive.nearest(history).path,valid.resolve())

    def test_rejected_replacement_at_same_path_removes_stale_reference(self):
        history=[{'a':1}];path=self.write('cp.json',history);self.archive.add(path)
        self.write('cp.json',history,legacy=True);self.assertFalse(self.archive.add(path))
        self.assertIsNone(self.archive.nearest(history));self.assertTrue(path.exists())
        self.write('cp.json',history);self.archive.add(path)
        write_json(path,{'schema':'future'});self.assertFalse(self.archive.add(path))
        self.assertIsNone(self.archive.nearest(history))

    def test_original_baseline_mutation_does_not_change_archive_contract(self):
        self.progress['baseline_snapshot']='{}';self.progress['baseline_sha256']=sha('{}')
        original=payload([{'a':1}],baseline())
        path=self.write('cp.json',[],document={'schema':'spire-map-checkpoint/v1','payload':original,'sha256':'native'})
        self.assertTrue(self.archive.add(path))

    def test_import_result_rejects_missing_progress_without_deleting_file(self):
        path=self.write('legacy.json',[{'a':1}],legacy=True)
        self.archive.import_result({'checkpoints':[{'path':str(path)}]})
        self.assertEqual(self.archive.entries,{});self.assertTrue(path.exists())

    def test_off_archive_keeps_legacy_prefix_behavior(self):
        archive=CheckpointArchive(CTX,ID);history=[{'a':1},{'b':2}]
        path=self.write('legacy.json',history,legacy=True)
        self.assertTrue(archive.add(path));self.assertEqual(archive.nearest(history).prefix_length,2)


class ResearchProgressCacheTests(unittest.TestCase):
    def test_real_cache_keeps_valid_source_hit_but_separates_baseline_bytes_and_protocol(self):
        cache=ResultCache();base={'seed':'42','history':[{'a':1}],
                                 'capture_f1_winners':True,'research_progress':baseline()}
        key=lambda request:canonical({'inputs':{'host':ID},'request':request})
        cache.put(key(base),{'value':[0],'marker':'source'})
        self.assertEqual(cache.get(key(deepcopy(base))),{'value':[0],'marker':'source'})
        old=deepcopy(base);old.pop('research_progress')
        different=deepcopy(base);different['research_progress']=baseline('{ "schema_version": 0 }')
        future=deepcopy(base);future['research_progress']['schema']='spire-research-progress/v2'
        for request in (old,different,future):self.assertIsNone(cache.get(key(request)))
        self.assertEqual(cache.hits,1)
