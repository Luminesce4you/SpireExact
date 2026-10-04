"""Manual controls preserve the local-origin/session boundary and 180-minute budget."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from dashboard import jobs,server


class DashboardControlTests(unittest.TestCase):
    def test_180_minutes_remains_valid_and_entry_records_latest_options(self):
        request=jobs.validate({'seed':'42','minutes':180})
        profile={'runtime_profile':'server-large-gen0','worker_memory_mib':1792,
                 'solver_settings':['--scheduler','focus','--prior','--root-async','--root-round','7','--paired-card-probes']}
        args=jobs.solver_settings(request,profile)
        self.assertEqual(args[args.index('--seconds')+1],'10770')
        self.assertEqual(args[args.index('--runtime-profile')+1],'server-large-gen0')
        self.assertIn('--paired-card-probes',args)
        for field in ('--seed','--out','--prefix','--scope-prefix'):
            with self.assertRaises(ValueError):
                jobs.solver_settings(request,{**profile,'solver_settings':[field,'unexpected']})

    def test_nonmanual_and_unknown_runs_cannot_be_controlled(self):
        with tempfile.TemporaryDirectory()as tmp:
            manifest=Path(tmp)/'validation-manifest.json'
            manifest.write_text('{"manual":false}')
            repo=SimpleNamespace(paths={'experiment':manifest})
            with self.assertRaises(ValueError):jobs.control('experiment','stop',{},repo)
            with self.assertRaises(KeyError):jobs.control('missing','stop',{},repo)
            with self.assertRaises(ValueError):jobs.control('experiment','stop',{'pid':1},repo)

    def test_old_frozen_host_cannot_pause_even_with_a_new_runner(self):
        with tempfile.TemporaryDirectory()as tmp:
            folder=Path(tmp);workspace=folder/'old-host';workspace.mkdir()
            (workspace/'freeze.json').write_text('{}')
            manifest=folder/'validation-manifest.json'
            manifest.write_text(json.dumps({'manual':True,'workspace':str(workspace),'pause_excluded_budget_clock':True}))
            repo=SimpleNamespace(paths={'old':manifest})
            with patch('dashboard.job_control.pause')as pause:
                with self.assertRaises(ValueError):jobs.control('old','pause',{},repo)
                pause.assert_not_called()
            (workspace/'freeze.json').write_text(json.dumps({'capabilities':{'interactive_pause_clock':'evaluate-native-and-python/v1'}}))
            self.assertTrue(jobs.pause_compatible(folder))

    def test_http_controls_require_token_origin_and_empty_payload(self):
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{http.server_port}'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def post(action,*,token=True,origin=None,payload=None):
            headers={'Content-Type':'application/json'}
            if token:headers['X-SpireBoard-Token']=jobs.TOKEN
            if origin:headers['Origin']=origin
            req=urllib.request.Request(base+'/api/jobs/manual-owned/'+action,
                data=json.dumps({}if payload is None else payload).encode(),headers=headers,method='POST')
            try:
                with opener.open(req,timeout=3)as response:return response.status,json.load(response)
            except urllib.error.HTTPError as error:
                with error:return error.code,json.load(error)
        try:
            with patch.object(jobs,'control')as control:
                self.assertEqual(post('pause',token=False)[0],403)
                self.assertEqual(post('pause',origin='https://example.invalid')[0],403)
                control.assert_not_called()
                for action,phase in (('pause','paused'),('resume','running'),('stop','stopped')):
                    control.return_value={'run_id':'manual-owned','job_control':{'phase':phase}}
                    code,result=post(action,origin=base)
                    self.assertEqual((code,result['job_control']['phase']),(200,phase))
                    self.assertEqual(control.call_args.args[:3],('manual-owned',action,{}))
                control.side_effect=ValueError('状态已改变')
                self.assertEqual(post('pause')[0],400)
        finally:
            http.shutdown();http.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
