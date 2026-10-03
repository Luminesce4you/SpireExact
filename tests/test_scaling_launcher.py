import unittest
from tools.limited_cli import job_memory_mib


class ScalingLauncherTests(unittest.TestCase):
    def test_existing_job_caps_are_unchanged(self):
        self.assertEqual(job_memory_mib('A0-180s-v1', {}), 6144)
        self.assertEqual(job_memory_mib('A10-seed-v2', {}), 14336)
        self.assertEqual(job_memory_mib('A10-seed-v2-scaling16', {}), 14336)

    def test_scaling_cap_requires_explicit_recorded_authorization(self):
        for env in ({'SPIRE_JOB_MEMORY_MIB': '24576'},
                    {'SPIRE_JOB_MEMORY_MIB': '24576', 'SPIRE_RESOURCE_OVERRIDE_NOTE': ' '}):
            with self.assertRaises(ValueError):
                job_memory_mib('A10-seed-v2-scaling16', env)
        authorized = {'SPIRE_JOB_MEMORY_MIB': '24576', 'SPIRE_RESOURCE_OVERRIDE_NOTE': 'User authorized memory relaxation'}
        self.assertEqual(job_memory_mib('A10-seed-v2-scaling16', authorized), 24576)
        with self.assertRaises(ValueError):
            job_memory_mib('A0-180s-v1', authorized)
        for value in ('0', '-1', '28673'):
            with self.assertRaises(ValueError):
                job_memory_mib('A10-seed-v2', dict(authorized, SPIRE_JOB_MEMORY_MIB=value))
