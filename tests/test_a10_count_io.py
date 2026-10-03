import gzip,json,tempfile,unittest
from pathlib import Path
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.search import SearchConfig,Evaluator
from spire_exact.mode1 import context

class A10CountIoTests(unittest.TestCase):
    def test_compact_atomic_json_preserves_exact_payload(self):
        with tempfile.TemporaryDirectory()as folder:
            p=Path(folder);value={'rng':[2**64-1,2,1],'history':[{'instance':i,'order':[3,1,2]}for i in range(30)],'wall':0.125,'message':'警告'}
            write_json(p/'pretty.json',value);write_json(p/'compact.json',value,compact=True)
            self.assertEqual(read_json(p/'pretty.json'),read_json(p/'compact.json'))
            self.assertLess((p/'compact.json').stat().st_size,(p/'pretty.json').stat().st_size)
            self.assertFalse((p/'compact.json.tmp').exists())
    def test_gzip_transport_keeps_complete_payload_and_order(self):
        with tempfile.TemporaryDirectory()as t:
            p=Path(t)/'decision.json';payload={'rng':{'counter':91},'trace':[{'instance':2},{'instance':1}],'hidden':[0,1,0]}
            with gzip.open(str(p)+'.gz','wt',encoding='utf-8')as f:json.dump(payload,f)
            self.assertEqual(read_json(p),payload)

    def test_solver_seed_derivation_is_stable_and_keeps_game_seed_separate(self):
        ctx=context('123','IRONCLAD',10,'all')
        a=Evaluator(None,ctx,Path('.'),SearchConfig(solver_seed=7,low_io=True))
        b=Evaluator(None,ctx,Path('.'),SearchConfig(solver_seed=8,low_io=True))
        self.assertEqual(a.request([]),a.request([]))
        self.assertNotEqual(a.request([])['policy_seed'],b.request([])['policy_seed'])
        self.assertEqual(a.request([])['seed'],'123')

    def test_combat_repair_increases_count_budget_not_only_wall_cap(self):
        ctx=context('123','IRONCLAD',10,'all')
        advisor={'nodes':60000,'budget_ms':600000,'boss_budget_ms':600000}
        ev=Evaluator(None,ctx,Path('.'),SearchConfig(),advisor)
        repair=ev.request([],advisor_scale=3)
        self.assertEqual(repair['advisor']['nodes'],180000)
        self.assertEqual(advisor['nodes'],60000)
        self.assertEqual(ev.request([])['advisor']['nodes'],60000)
        self.assertTrue(repair['advisor']['fix_consumed_block_compensation'])
        self.assertNotIn('fix_consumed_block_compensation',advisor)
        control=Evaluator(None,ctx,Path('.'),SearchConfig(),{**advisor,'fix_consumed_block_compensation':False})
        self.assertFalse(control.request([])['advisor']['fix_consumed_block_compensation'])

    def test_shared_evidence_transport_restores_complete_prefix_in_order(self):
        with tempfile.TemporaryDirectory()as folder:
            data=Path(folder);(data/'checkpoints').mkdir()
            evidence=[{'instance':2,'rng':[1,2]},{'instance':1,'rng':[2,1]},{'tail':True}]
            with gzip.open(data/'decision.json.gz','wt',encoding='utf-8')as f:json.dump({'decision_evidence':evidence},f)
            checkpoint={'schema':'spire-map-checkpoint/v1','payload':{'history':[{'a':1},{'a':2}]},
                        'evidence_ref':{'file':'decision.json.gz','count':2}}
            path=data/'checkpoints'/'map.json'
            path.write_text(json.dumps(checkpoint))
            self.assertEqual(read_json(path)['payload']['evidence'],evidence[:2])
            self.assertNotIn('evidence',read_json(path,resolve_checkpoint=False)['payload'])
            checkpoint['evidence_ref']['count']=3;path.write_text(json.dumps(checkpoint))
            with self.assertRaisesRegex(ValueError,'CHECKPOINT_EVIDENCE_LENGTH'):read_json(path)

if __name__=='__main__':unittest.main()
