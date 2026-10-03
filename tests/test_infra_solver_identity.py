import re
import unittest
from pathlib import Path


class InfraSolverIdentityTests(unittest.TestCase):
    def test_registered_solver_hashes_are_full_sha256_values(self):
        path=Path(__file__).resolve().parents[1]/'native/SpireNativeHost/AdvisorBlockCompensation.cs'
        values=re.findall(r'const string (\w+) = "([0-9A-F]+)";',path.read_text(encoding='utf-8'))
        self.assertTrue(values)
        for name,value in values:
            with self.subTest(name=name):self.assertRegex(value,r'^[0-9A-F]{64}$')
