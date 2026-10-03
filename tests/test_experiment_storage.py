import unittest
from unittest.mock import patch
from types import SimpleNamespace
from tools.experiment_storage import ensure_capacity,GIB

class StorageTests(unittest.TestCase):
    def test_insufficient_space_stops_before_a_seed_is_executed(self):
        with patch('tools.experiment_storage.shutil.disk_usage',return_value=SimpleNamespace(free=3*GIB)):
            with self.assertRaisesRegex(RuntimeError,'STORAGE_ADMISSION_DENIED'):ensure_capacity('.',3)
    def test_parallel_jobs_reserve_more_headroom(self):
        with patch('tools.experiment_storage.shutil.disk_usage',return_value=SimpleNamespace(free=12*GIB)):
            self.assertEqual(ensure_capacity('.',1)['required_free_bytes'],10*GIB)
            with self.assertRaisesRegex(RuntimeError,'STORAGE_ADMISSION_DENIED'):ensure_capacity('.',3)

if __name__=='__main__':unittest.main()
