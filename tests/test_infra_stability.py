import unittest
from tools.validate_infra_stability import stability_checks


class InfraStabilityTests(unittest.TestCase):
    def resources(self, **changes):
        return {'wall_seconds':10775,'enforced':True,'peak_job_commit_bytes':12*1024**3,
                'job_write_transfer_bytes':2_000_000_000,'storage_limit':False,**changes}

    def test_early_win_cannot_be_a_full_duration_stability_pass(self):
        checks=stability_checks(self.resources(wall_seconds=3000),{},True)
        self.assertFalse(checks['full_180min_protocol_window'])
        self.assertTrue(checks['resource_limits_zero'])

    def test_full_run_requires_actual_resources_and_no_memory_failure(self):
        self.assertTrue(all(stability_checks(self.resources(),{},True).values()))
        self.assertFalse(all(stability_checks({}, {}, True).values()))
        self.assertFalse(all(stability_checks(self.resources(),{'RESOURCE_LIMIT':1},True).values()))
        self.assertFalse(all(stability_checks(self.resources(),{'MECHANISM_OR_HOST_GAP':1},True).values()))
        self.assertFalse(all(stability_checks(self.resources(peak_job_commit_bytes=13*1024**3),{},True).values()))
        self.assertFalse(all(stability_checks(self.resources(),{},False).values()))
        self.assertFalse(all(stability_checks(self.resources(job_write_transfer_bytes=5_000_000_000),{},True).values()))
