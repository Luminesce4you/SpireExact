"""Serialized UI fixtures only; these tests are not native game evidence."""
import copy
import gzip
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from types import SimpleNamespace
from http.server import ThreadingHTTPServer

from dashboard.winning_route import WinningRouteStore
from spire_exact.mode1 import context, check_winning_replay, OBJECTIVE


class WinningRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.run=Path(self.tmp.name)/'run';self.root=self.run/'seed-10'
        self.label='eval-0001-macro_focus';self.manifest=self.run/'validation-manifest.json'
        self.ctx=context('10','IRONCLAD',10,'all')
        self.ident={'game_sha256':'a'*64,'host_sha256':'b'*64}
        self.trace=[{'kind':'map','row':1,'col':0},{'kind':'buy','index':2,'item_type':'MerchantCardEntry','cost':50}]
        before={'act':0,'floor':1,'hp':'70','max_hp':'80','gold':100,'rng':{'s0':2**64-1},'deck':[]}
        after={**before,'floor':2,'room':'Shop'}
        self.evidence=[{'phase':'map','observation':before,'available_actions':[self.trace[0]]},
                       {'phase':'shop','observation':after,'available_actions':[{'kind':'buy','index':0,'item_type':'MerchantCardEntry','cost':50},self.trace[1]],
                        'option_labels':[['card:WRONG'],['card:RIGHT']]}]
        candidate={'schema':'spire-native-decision/v1','status':'TERMINAL','reason':None,'value':[1],
                   'objective':OBJECTIVE['id'],'native_terminal_observed':True,'trace':self.trace,'consumed':2,
                   'decision_evidence':self.evidence,'observation':{**after,'gold':50,'hp':'61'},'performance':{'pid':101}}
        replay=copy.deepcopy(candidate);replay['performance']={'pid':102,'counters':{'replay_prefix_solver_calls':0,'checkpoint_skipped_actions':0}}
        self.put(self.manifest,{'run_id':'test-run','seed':'10'})
        self.put(self.root/'result.json',{'status':'VERIFIED_WIN_IN_NATIVE_HOST','best_label':self.label,'context':self.ctx})
        self.put(self.root/self.label/'data/decision.json',candidate)
        self.replay_path=self.root/('verify-'+self.label)/'data/decision.json'
        self.put(self.replay_path,replay)
        self.put(self.root/self.label/'data/identity.json',self.ident)
        self.put(self.replay_path.parent/'identity.json',self.ident)
        self.request_path=self.replay_path.parent.parent/'request.json'
        self.put(self.request_path,{'generate_candidate':False,'history':self.trace})
        cert=check_winning_replay(candidate,replay,{'context':self.ctx,'native':self.ident},{'context':self.ctx,'native':self.ident})
        self.put(self.root/'certificate.json',cert)
        self.store=WinningRouteStore()

    def put(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8')

    def test_complete_steps_menu_identity_and_final_boundary(self):
        route=self.store.load(self.manifest)
        self.assertTrue(route['verified']);self.assertEqual(route['total_steps'],2)
        self.assertEqual([s['number']for s in route['steps']],[1,2])
        detail=self.store.step(route,1)
        self.assertEqual(detail['summary']['chosen_indices'],[1]) # merchant index 2 is menu position 1
        self.assertIn('card:RIGHT',detail['summary']['title'])
        self.assertFalse(detail['options'][0]['chosen']);self.assertTrue(detail['options'][1]['chosen'])
        self.assertTrue(detail['after_is_terminal']);self.assertEqual(detail['after']['gold'],50)
        self.assertFalse(self.store.step(route,0)['after_is_terminal'])
        self.assertEqual(self.store.step(route,0)['after']['floor'],2)

    def test_rng_is_lossless_in_browser_and_export_retains_numeric_type(self):
        route=self.store.load(self.manifest)
        self.assertEqual(self.store.step(route,0)['before']['rng']['s0'],str(2**64-1))
        self.assertEqual(self.store.export(route)['decision_evidence'][0]['observation']['rng']['s0'],2**64-1)

    def test_summary_or_certificate_alone_cannot_claim_verified(self):
        self.put(self.request_path,{'generate_candidate':True,'advisor':{'solver':'bad'},'history':self.trace})
        route=self.store.load(self.manifest)
        self.assertFalse(route['verified']);self.assertTrue(route['available'])
        self.assertFalse(route['checks']['no_advisor_or_checkpoint'])
        self.assertFalse(route['checks']['explicit_replay'])

    def test_partial_replay_is_flagged_without_dropping_candidate_steps(self):
        doc=json.loads(self.replay_path.read_text());doc['decision_evidence'].pop()
        self.put(self.replay_path,doc)
        route=self.store.load(self.manifest)
        self.assertFalse(route['verified']);self.assertEqual(route['total_steps'],2)
        self.assertFalse(route['steps'][-1]['replay_match'])

    def test_changed_evidence_invalidates_cache_and_gzip_is_supported(self):
        self.assertTrue(self.store.load(self.manifest)['verified'])
        doc=json.loads(self.replay_path.read_text());doc['observation']['hp']='0'
        with gzip.open(str(self.replay_path)+'.gz','wt',encoding='utf-8')as f:json.dump(doc,f)
        self.replay_path.unlink()
        self.assertFalse(self.store.load(self.manifest)['verified'])

    def test_no_victory_and_unsafe_labels_do_not_expose_other_files(self):
        self.put(self.root/'result.json',{'status':'UNKNOWN'})
        self.assertFalse(self.store.load(self.manifest)['available'])
        self.put(self.root/'result.json',{'status':'VERIFIED_WIN_IN_NATIVE_HOST','best_label':'../outside'})
        with self.assertRaises(ValueError):self.store.load(self.manifest)

    def test_step_bounds(self):
        route=self.store.load(self.manifest)
        for index in(-1,2,999):
            with self.assertRaises(ValueError):self.store.step(route,index)

    def test_http_metadata_step_export_and_unknown_run(self):
        from dashboard import server
        with patch.object(server,'repo',SimpleNamespace(paths={'test-run':self.manifest})),patch.object(server,'winning_routes',self.store):
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
            base=f'http://127.0.0.1:{http.server_port}/api/winning-route?run=test-run'
            try:
                with urlopen(base)as response:self.assertEqual(json.load(response)['total_steps'],2)
                with urlopen(base+'&step=1')as response:self.assertTrue(json.load(response)['after_is_terminal'])
                with urlopen(base+'&export=1')as response:
                    self.assertIn('attachment',response.headers['Content-Disposition'])
                    self.assertEqual(len(json.load(response)['trace']),2)
                with self.assertRaises(HTTPError)as error:urlopen(base+'&step=999')
                self.assertEqual(error.exception.code,400)
                with self.assertRaises(HTTPError)as error:urlopen(base.replace('test-run','unknown'))
                self.assertEqual(error.exception.code,404)
            finally:http.shutdown();http.server_close();worker.join(2)

