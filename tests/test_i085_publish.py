"""Actual local Git/file-remote tests; no GitHub writes or native execution."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from tools.publish_i085 import git,publish

@unittest.skipUnless(shutil.which('git'),'git unavailable')
class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        self.repo=root/'repo';self.repo.mkdir();self.remote=root/'remote.git'
        subprocess.run(['git','init','--bare',str(self.remote)],capture_output=True,check=True)
        git(self.repo,'init');git(self.repo,'config','user.name','Fixture');git(self.repo,'config','user.email','fixture@example.invalid')
        (self.repo/'source.txt').write_text('base\n');git(self.repo,'add','.');git(self.repo,'commit','-m','base')
        self.base=git(self.repo,'rev-parse','HEAD');self.branch='codex/i085-fixture'
        git(self.repo,'branch','-M',self.branch);git(self.repo,'remote','add','origin',str(self.remote))
        git(self.repo,'push','origin','HEAD')
        (self.repo/'source.txt').write_text('candidate\n');git(self.repo,'commit','-am','candidate');self.tip=git(self.repo,'rev-parse','HEAD')
        self.bundle=root/'candidate.bundle';git(self.repo,'bundle','create',str(self.bundle),f'{self.base}..{self.branch}')

    def test_actual_local_fast_forward_and_readback(self):
        r=publish(self.repo,self.bundle,tip=self.tip,base=self.base,branch=self.branch,execute=True)
        self.assertTrue(r['executed_push']);self.assertEqual(r['verified_remote_tip'],self.tip)
        self.assertEqual((self.repo/'source.txt').read_text(),'candidate\n')

    def test_dry_run_preserves_remote(self):
        r=publish(self.repo,self.bundle,tip=self.tip,base=self.base,branch=self.branch)
        self.assertFalse(r['executed_push']);self.assertTrue(git(self.repo,'ls-remote','origin','refs/heads/'+self.branch).startswith(self.base))

    def test_changed_remote_not_forced(self):
        git(self.repo,'push','origin','HEAD')
        with self.assertRaises(ValueError):publish(self.repo,self.bundle,tip=self.tip,base=self.base,branch=self.branch,execute=True)

    def test_wrong_bundle_identity_refused(self):
        with self.assertRaises(ValueError):publish(self.repo,self.bundle,tip='0'*40,base=self.base,branch=self.branch,execute=True)
