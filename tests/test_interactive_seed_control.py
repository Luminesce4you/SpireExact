import json, shutil, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock
from tools.interactive_seed_control import attach, pause_evidence, process_record, finish
from spire_exact.planning.io import read_json, write_json


class SeedControlTests(unittest.TestCase):
    def fixture(self, root):
        workspace=root/'experiments/frozen-fixture'
        (workspace/'spire_exact/planning').mkdir(parents=True)
        shutil.copyfile(Path(__file__).resolve().parents[1]/'spire_exact/planning/gates.py',workspace/'spire_exact/planning/gates.py')
        report=root/'init.json'
        value={'passed':True,'returncode':0,'actions_observed':0,'full_search_started':False,'host_sha256':'host','source_version':'version',
               'pause_clock':{'enabled':True,'patched_methods':27}}
        write_json(report,value)
        write_json(workspace/'freeze.json',{'source_version':'version','capabilities':{'pause_clock_report':str(report)}})
        manifest={'run_id':'queue-fixture','workspace':str(workspace),'host_sha256':'host','version':'version',
                  'settings':['--gate-preset','escalate-evaluate','--final-gate-plan','open']}
        return workspace,manifest,report,value

    def test_valid_report_and_evaluate_plan_enable_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace,manifest,report,value=self.fixture(Path(tmp))
            result=pause_evidence(workspace,manifest)
            self.assertEqual(result,{'report_path':str(report.resolve()),'host_sha256':'host','source_version':'version'})

    def test_claim_or_nonmatching_report_does_not_enable_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace,manifest,report,value=self.fixture(Path(tmp))
            for key,invalid in [('passed',False),('returncode',1),('host_sha256','wrong'),('source_version','wrong'),
                                ('actions_observed',1),('full_search_started',True),('pause_clock',{'enabled':True,'patched_methods':0})]:
                write_json(report,{**value,key:invalid})
                self.assertIsNone(pause_evidence(workspace,manifest))
            report.unlink()
            self.assertIsNone(pause_evidence(workspace,manifest))

    def test_coordinator_retry_plan_disables_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace,manifest,report,value=self.fixture(Path(tmp))
            manifest['settings']=['--gate-preset','escalate','--final-gate-plan','open']
            self.assertIsNone(pause_evidence(workspace,manifest))

    def test_host_named_report_cache_without_freeze_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace,manifest,report,value=self.fixture(root)
            write_json(workspace/'freeze.json',{'source_version':'version'})
            cache=root/'dashboard/control-validation/host.json';write_json(cache,value)
            self.assertEqual(pause_evidence(workspace,manifest)['report_path'],str(cache.resolve()))

    def test_attach_binds_queued_run_and_preserves_coordinator_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace,manifest,report,value=self.fixture(root)
            control=Mock(SCHEMA='spire-job-control/v1')
            identity={'pid':7,'created_ticks':123}
            def initialize(folder):
                write_json(folder/'process.json',{'coordinator_pid':7,'coordinator_identity':identity})
                return {'schema':control.SCHEMA,'coordinator_identity':identity,'phase':'starting'}
            control.initialize.side_effect=initialize
            folder=root/'run';folder.mkdir()
            chosen,on=attach(folder,manifest,control=control)
            self.assertTrue(on);self.assertIs(chosen,control)
            self.assertEqual(read_json(folder/'control.json')['run_folder'],str(folder.resolve()))
            self.assertEqual(read_json(folder/'control.json')['run_id'],'queue-fixture')
            self.assertEqual(manifest['process_control_schema'],control.SCHEMA)
            self.assertTrue(manifest['pause_excluded_budget_clock'])
            process_record(folder,11,identity=lambda pid:{'pid':pid,'created_ticks':456})
            meta=read_json(folder/'process.json')
            self.assertEqual(meta['coordinator_identity'],identity)
            self.assertEqual(meta['owned_job_identity'],{'pid':11,'created_ticks':456})

    def test_missing_pause_evidence_still_registers_stop_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace,manifest,report,value=self.fixture(root);report.unlink()
            control=Mock(SCHEMA='schema');control.initialize.return_value={'schema':'schema'}
            folder=root/'run';folder.mkdir();chosen,on=attach(folder,manifest,control=control)
            self.assertFalse(on);self.assertFalse(manifest['pause_excluded_budget_clock'])
            self.assertEqual(manifest['process_control_schema'],'schema')

    def test_finished_marker_never_overwrites_cancelled_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);write_json(folder/'launch-state.json',{'phase':'stopped'})
            control=Mock();control.mark_finished.return_value=False
            finish(folder,control,'completed')
            self.assertEqual(read_json(folder/'launch-state.json')['phase'],'stopped')


if __name__=='__main__':unittest.main()
