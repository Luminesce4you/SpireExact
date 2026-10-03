import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dashboard import server


class DashboardComponentTests(unittest.TestCase):
    def test_fidelity_replay_counts_never_become_fresh_wins(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);target=root/'smoke';target.mkdir()
            (target/'partial-results.json').write_text(json.dumps({'rows':[{'entry_verified':True,'replay_verified':True,
                'counters':{'continuation_state_mismatch':2}}]}))
            m={'cases_root':str(root),'gate_report':str(root/'gate.json'),'fidelity_phases':[{'name':'smoke','cases':4}]}
            d=server.Repository().fidelity_detail('fidelity',root,m)
            self.assertEqual((d['evaluations'],d['planned'],d['wins']),(1,8,0))
            self.assertEqual(d['component']['continuation_mismatches'],2)
            self.assertEqual(sum(d['classes'].values()),1)
            self.assertEqual(d['status'],'pending')
            (root/'gate.json').write_text('{"valid":false}')
            self.assertEqual(server.Repository().fidelity_detail('fidelity',root,m)['status'],'error')

    def test_component_discovery_progress_and_exit_do_not_claim_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'experiments/iteration-024/smt'
            folder.mkdir(parents=True)
            def write(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding='utf-8')
            write(folder / 'validation-manifest.json', {'run_id':'smt','counts_as_planner_win':False,
                  'arms':{'16t-workers15':[16,15,'legacy']},'source_requests':28,'repeated_blocks':2})
            (folder / 'events.jsonl').write_text(json.dumps({'event':'component_started','arm':'16t-workers15','repeat':0,'time':100})+'\n')
            (folder / 'repeat-0-16t-workers15-events.jsonl').write_text(json.dumps({'event':'started','pid':123,
                  'cpus':list(range(16)),'physical_core_count':8,'smt_used':True})+'\n')
            write(folder / 'repeat-0-16t-workers15/case-000/data/result.json',{'status':'NATIVE_DATA_EXPORTED'})
            with patch.object(server,'ROOT',root):
                repo=server.Repository()
                d=repo.detail('smt')
                self.assertEqual((d['evaluations'],d['planned'],d['wins']),(1,112,0))
                self.assertEqual(d['component']['pid'],123)
                self.assertEqual(len(d['manifest']['cpu_set']),16)
                repo.live['smt']={'alive':True}
                self.assertEqual(repo.snapshot()['selected']['id'],'smt')
                with(folder/'events.jsonl').open('a')as f:
                    f.write(json.dumps({'event':'component_paused','time':110})+'\n')
                self.assertEqual(repo.detail('smt')['status'],'paused')
                (folder/'events.jsonl').write_text(json.dumps({'event':'component_started','arm':'16t-workers15','repeat':0,'time':100})+'\n')
                write(folder/'report.json',{'complete':True,'passed':True,'comparisons':[]})
                d=repo.detail('smt')
                self.assertEqual(d['status'],'completed')
                self.assertEqual(d['wins'],0)
                self.assertFalse(d['normal_godot_verified'])

    def test_tactical_rescue_is_not_a_campaign_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'cases').mkdir()
            (folder/'cases/partial-results.json').write_text(json.dumps({'rows':[{
                'case':'floor-17-00','arm':'wide-600k','classification':'SEARCH_BUDGET',
                'independent_replay_verified':True,'searches':[{'expanded_nodes':25}]}]}))
            m={'tactical_arms':{'wide-600k':['VeryHigh',600000]},'workers':4,'runtime_profile':'legacy'}
            d=server.Repository().tactical_detail('tactic',folder,m)
            self.assertEqual(d['wins'],0)
            self.assertEqual(d['classes'],{'TACTICAL_RESCUE_VERIFIED':1})
            self.assertEqual(d['nodes'],25)
            self.assertEqual(d['planned'],36)

    def test_preparation_live_counts_are_native_evaluations_not_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);case=folder/'cases/root-repeat-0';case.mkdir(parents=True)
            (folder/'events.jsonl').write_text(json.dumps({'event':'preparation_diagnosis_started','time':100})+'\n')
            (folder/'cases/events.jsonl').write_text(json.dumps({'event':'preparation_root_started','time':105,'root':'root','repeat':0})+'\n')
            (folder/'cases/roots.json').write_text('[{"id":"root"}]')
            (case/'result.json').write_text(json.dumps({'evaluations':[{'label':'eval-0000-baseline','classification':'SEARCH_BUDGET',
                                                        'expanded_combat_nodes':20,'completed_wall_seconds':7}]}))
            m={'evaluations_per_root':48,'repeats':1}
            d=server.Repository().preparation_detail('prep',folder,m)
            self.assertEqual((d['evaluations'],d['planned'],d['wins']),(1,48,0))
            self.assertEqual(d['points'][0]['seconds'],12)
            self.assertEqual(d['status'],'pending')
