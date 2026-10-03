import json
from pathlib import Path
import tempfile
import unittest
from tools.analyze_smt_seed import compare


class SmtAnalysisTests(unittest.TestCase):
    def arm(self, folder, order):
        seed=folder/'seed-101';seed.mkdir(parents=True)
        (folder/'validation-manifest.json').write_text(json.dumps({'cpu_set':[0,2],'version':'same'}))
        (folder/'baseline-report.json').write_text(json.dumps({'verified_win':True,'wall_seconds':4,'resources':{}}))
        (seed/'certificate.json').write_text('{}')
        (seed/'result.json').write_text(json.dumps({'resources':{'workers':2},'elapsed_seconds':4}))
        rows=[]
        for i in order:
            label=f'eval-{i:04d}-root_policy'
            target=seed/label/'data';target.mkdir(parents=True)
            obs={'act':2,'floor':49,'room':'Boss','hp':str(i)}
            document={'status':'TERMINAL','observation':obs,'trace':[{'kind':'event','index':i}]}
            (target/'decision.json').write_text(json.dumps(document))
            request={'history':[],'policy_seed':i,'out':str(target),'checkpoint':str(seed/'maps'/f'{i}.json')}
            (target.parent/'request.json').write_text(json.dumps(request))
            rows.append({'label':label,'observation':obs,'classification':'NATIVE_WIN_CANDIDATE' if i==2 else 'NATIVE_ROUTE_DEATH',
                         'completed_wall_seconds':i+1,'expanded_combat_nodes':i+1})
        (seed/'evaluations.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))

    def test_match_submission_labels_when_root_completion_order_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            left,right=Path(tmp)/'left',Path(tmp)/'right'
            self.arm(left,[1,0,2]);self.arm(right,[0,1,2])
            result=compare(left,right)
            self.assertFalse(result['ledger_completion_order_equal'])
            self.assertEqual(result['common_prefix_length'],3)
            self.assertTrue(result['all_common_game_bytes_equal'])
            self.assertTrue(all(r['algorithm_request_equal'] and r['nodes_equal'] for r in result['paired_evaluations']))
