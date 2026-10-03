import unittest
from types import SimpleNamespace
from spire_exact.planning.pool import runtime_settings,apply_heap_limit

class RuntimeProfileTests(unittest.TestCase):
    def test_combined_profile_sets_both_limits_without_mutating_parent_environment(self):
        plan=SimpleNamespace(dop=1,effective_cpus=8,worker_memory_bytes=1792*1024**2)
        runtime=runtime_settings(plan,'server-bounded-large-gen0')
        original={'COMPlus_GCgen0size':'100','DOTNET_GCHeapHardLimitSOH':'200'}
        env=apply_heap_limit(original,runtime)
        self.assertEqual(int(env['DOTNET_GCgen0size'],16),1<<30)
        self.assertEqual(int(env['DOTNET_GCHeapHardLimit'],16),1344*1024**2)
        self.assertNotIn('COMPlus_GCgen0size',env)
        self.assertNotIn('DOTNET_GCHeapHardLimitSOH',env)
        self.assertEqual(original,{'COMPlus_GCgen0size':'100','DOTNET_GCHeapHardLimitSOH':'200'})
        self.assertTrue(runtime['server_gc'])
    def test_heap_budget_is_derived_from_worker_not_whole_job(self):
        plan=SimpleNamespace(dop=1,effective_cpus=8,worker_memory_bytes=1536*1024**2)
        runtime=runtime_settings(plan,'server-bounded-heap')
        self.assertEqual(runtime['gc_heap_hard_limit_bytes'],1152*1024**2)
        self.assertTrue(runtime['server_gc']);self.assertEqual(runtime['solver_dop'],1)
        original={'DOTNET_GCHeapHardLimitSOH':'dead','COMPlus_GCHeapHardLimitLOH':'beef','OTHER':'keep'}
        env=apply_heap_limit(original,runtime)
        self.assertEqual(int(env['DOTNET_GCHeapHardLimit'],16),1152*1024**2)
        self.assertNotIn('DOTNET_GCHeapHardLimitSOH',env)
        self.assertNotIn('COMPlus_GCHeapHardLimitLOH',env)
        self.assertEqual(env['OTHER'],'keep');self.assertIn('DOTNET_GCHeapHardLimitSOH',original)
    def test_existing_profiles_do_not_modify_heap_environment(self):
        original={'DOTNET_GCHeapHardLimit':'123'}
        self.assertEqual(apply_heap_limit(original,runtime_settings(SimpleNamespace(dop=1,effective_cpus=8),'server-one-heap')),original)
    def test_background_runtime_threads_do_not_change_solver_dop(self):
        plan=SimpleNamespace(dop=1,effective_cpus=8)
        runtime=runtime_settings(plan,'server-one-heap')
        self.assertEqual(runtime['solver_dop'],1);self.assertEqual(runtime['processors'],2)
        self.assertEqual(runtime['gc_heaps'],1);self.assertTrue(runtime['server_gc'])
        self.assertEqual(plan.dop,1)
    def test_profiles_do_not_silently_exceed_single_cpu_assignment(self):
        with self.assertRaises(ValueError):runtime_settings(SimpleNamespace(dop=1,effective_cpus=1),'workstation-2')
    def test_no_untested_search_parallelism_combination(self):
        with self.assertRaises(ValueError):runtime_settings(SimpleNamespace(dop=2,effective_cpus=8),'server-one-heap')
    def test_default_remains_legacy_until_native_promotion(self):
        self.assertEqual(runtime_settings(SimpleNamespace(dop=1,effective_cpus=8))['processors'],1)

if __name__=='__main__':unittest.main()
