"""Pure helpers of the fight benchmark (no native process)."""
import io,json,tempfile,unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from tools.fight_bench import fights,summarize,compare,compare_exact,algorithm_request

def combat(act,floor,room,turn,hp=50):
    return {'phase':'combat','observation':{'act':act,'floor':floor,'room':room,'turn':turn,'hp':str(hp),'max_hp':'80','deck':[{}]*12,
                                           'enemies':[{'id':'FOE','hp':'30'}]}}
def other(floor):return {'phase':'map','observation':{'act':0,'floor':floor}}

class FightBenchTests(unittest.TestCase):
    def test_solver_comparison_preserves_every_other_input_and_dependency(self):
        original={'history':[{'a':1}], 'advisor':{'solver':'solver-a.dll','nodes':100,
                  'binary_identity':{str(Path('solver-a.dll').resolve()):'a','harness.dll':'h','game.dll':'g'}}}
        other=json.loads(json.dumps(original))
        other['advisor']['solver']='solver-b.dll'
        other['advisor']['binary_identity'].pop(str(Path('solver-a.dll').resolve()))
        other['advisor']['binary_identity'][str(Path('solver-b.dll').resolve())]='b'
        self.assertEqual(algorithm_request(original),algorithm_request(other))
        self.assertEqual(original['advisor']['solver'],'solver-a.dll')
        other['advisor']['binary_identity']['harness.dll']='changed'
        self.assertNotEqual(algorithm_request(original),algorithm_request(other))
        other['advisor']['binary_identity']['harness.dll']='h'
        other['advisor']['nodes']=101
        self.assertNotEqual(algorithm_request(original),algorithm_request(other))
    def test_strict_comparison_checks_every_repeat_and_node_count(self):
        def row(repeat, **changes):
            return {'case':'x','repeat':repeat,'status':'BUDGET','request_sha256':'request','trace_sha256':'actions',
                    'game_sha256':'game','nodes':100,**changes}
        with tempfile.TemporaryDirectory()as d:
            a=Path(d)/'a.jsonl';b=Path(d)/'b.jsonl'
            def write(path,rows):path.write_text('\n'.join(json.dumps(r)for r in rows),encoding='utf-8')
            write(a,[row(0),row(1)]);write(b,[row(0),row(1)])
            self.assertTrue(compare_exact(a,b)['equivalent'])
            for altered in ([row(0),row(1,nodes=101)], [row(0),row(1,trace_sha256='different')],
                            [row(0)], [row(0),row(1,error='boom')], [row(0),row(0)],
                            [row(0),row(1,request_sha256='changed')], [row(0),row(1,game_sha256='changed')],
                            [row(0),row(1,reported_time_boundary=True)]):
                write(b,altered)
                self.assertFalse(compare_exact(a,b)['equivalent'])
    def test_empty_or_all_failed_comparison_does_not_succeed_or_crash(self):
        with tempfile.TemporaryDirectory()as d:
            a=Path(d)/'a.jsonl';b=Path(d)/'b.jsonl'
            a.write_text('',encoding='utf-8');b.write_text('',encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                self.assertFalse(compare(SimpleNamespace(a=a,b=b,show=0))['equivalent'])
            a.write_text('{"case":"x","repeat":0,"error":"boom"}\n',encoding='utf-8')
            b.write_text(a.read_text(),encoding='utf-8')
            self.assertFalse(compare_exact(a,b)['equivalent'])
    def test_fights_start_at_their_first_combat_decision_and_respect_the_prefix(self):
        evidence=[other(1),combat(0,1,'Monster',1),combat(0,1,'Monster',2),other(2),combat(0,2,'Elite',1),other(3),combat(0,3,'Boss',1)]
        decision={'trace':[{}]*len(evidence),'decision_evidence':evidence}
        rows=fights(decision)
        self.assertEqual([(r['cut'],r['floor'],r['room'])for r in rows],[(1,1,'Monster'),(4,2,'Elite'),(6,3,'Boss')])
        self.assertEqual([r['cut']for r in fights(decision,start=4)],[4,6])
        self.assertEqual(rows[0]['encounter'],['FOE']);self.assertEqual(rows[0]['deck'],12)
    def test_summary_hashes_only_the_new_actions_and_never_reports_hp_for_a_lost_fight(self):
        searches=[{'wall_us':2_000_000,'expanded_nodes':3000,'gc_pause_ms':100,'allocated_bytes':2**30}]
        transport={'wall_seconds':3.0,'peak_sampled_rss':900<<20}
        base={'trace':[{'a':1},{'a':2},{'a':3}],'advisor_metrics':{'searches':searches}}
        won=summarize({**base,'status':'BUDGET','reason':'candidate_horizon:floor','observation':{'floor':5,'hp':'41','potions':[None,'X']}},1,5,transport)
        lost=summarize({**base,'status':'TERMINAL','value':[0],'observation':{'floor':5,'hp':'0','potions':[None,None]}},1,5,transport)
        self.assertTrue(won['won_fight']);self.assertEqual((won['hp_out'],won['potions_out']),('41',1))
        self.assertFalse(lost['won_fight']);self.assertIsNone(lost['hp_out'])
        self.assertEqual(won['trace_sha256'],lost['trace_sha256'])              # same two new actions
        self.assertEqual((won['nodes'],won['nodes_per_s'],won['peak_rss_mb']),(3000,1500,900))
        other=summarize({**base,'trace':[{'a':9},{'a':2},{'a':3}],'status':'TERMINAL','value':[0],'observation':{'floor':5}},1,5,transport)
        self.assertEqual(other['trace_sha256'],won['trace_sha256'])             # the replayed prefix is not hashed
    def test_compare_reports_identical_traces_and_flips(self):
        def row(case,sha,won,wall):
            return {'case':case,'repeat':0,'trace_sha256':sha,'won_fight':won,'hp_out':'10' if won else None,'nodes':1000,'search_wall':wall,
                    'gc_pause':0.1,'allocated_gb':0.2,'peak_rss_mb':800}
        with tempfile.TemporaryDirectory()as d:
            a=Path(d)/'a.jsonl';b=Path(d)/'b.jsonl'
            a.write_text('\n'.join(json.dumps(r)for r in[row('x','s1',True,2.0),row('y','s2',False,4.0),{'case':'z','error':'boom'}]),encoding='utf-8')
            b.write_text('\n'.join(json.dumps(r)for r in[row('x','s1',True,1.0),row('y','s3',True,4.0)]),encoding='utf-8')
            out=io.StringIO()
            with redirect_stdout(out):compare(SimpleNamespace(a=a,b=b,show=5))
        text=out.getvalue()
        self.assertIn('paired 2, identical new-action traces 1',text);self.assertIn('ratio A/B 2.000',text)
        self.assertIn('fight results flipped: 1 (A-only wins 0, B-only wins 1)',text);self.assertIn('differs y',text)

if __name__=='__main__':unittest.main()
