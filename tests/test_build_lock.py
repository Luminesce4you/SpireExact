import os,subprocess,sys,tempfile,unittest
from pathlib import Path
from spire_exact.build_lock import native_build_lock

class BuildLockTests(unittest.TestCase):
    def test_other_process_cannot_enter_until_owner_releases(self):
        with tempfile.TemporaryDirectory()as t:
            lock=Path(t)/'build.lock'
            code='from spire_exact.build_lock import native_build_lock; import sys\nwith native_build_lock(sys.argv[1], timeout=.2): print("entered")'
            with native_build_lock(lock):
                other=subprocess.run([sys.executable,'-c',code,str(lock)],capture_output=True,text=True)
                self.assertNotEqual(other.returncode,0)
                self.assertIn('NATIVE_BUILD_LOCK_TIMEOUT',other.stderr)
                self.assertNotIn('entered',other.stdout)
            other=subprocess.run([sys.executable,'-c',code,str(lock)],capture_output=True,text=True)
            self.assertEqual(other.returncode,0);self.assertEqual(other.stdout.strip(),'entered')

    def test_exception_releases_lock(self):
        with tempfile.TemporaryDirectory()as t:
            lock=Path(t)/'build.lock'
            with self.assertRaises(ValueError):
                with native_build_lock(lock):raise ValueError('compiler failure')
            with native_build_lock(lock,timeout=.1):pass

if __name__=='__main__':unittest.main()
