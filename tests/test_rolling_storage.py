import json, tempfile, unittest
from pathlib import Path
from tools.rolling_storage import prune_seed, completed_seeds, safe_unlink, active_seed, RollingConsole, logical_bytes

class RollingStorageTests(unittest.TestCase):
    def test_inventory_counts_live_file_sizes_and_never_follows_links(self):
        import os
        self.put('one',b'a'*3);self.put('nested/two',b'b'*7)
        expected=sum(p.stat().st_size for p in self.seed.rglob('*') if p.is_file())
        self.assertEqual(logical_bytes(self.seed),expected)
        self.put('one',b'x'*23)
        self.assertEqual(logical_bytes(self.seed),expected+20)
        outside=self.root/'outside';outside.mkdir();(outside/'large').write_bytes(b'z'*100)
        try:
            os.symlink(outside,self.seed/'dir-link',target_is_directory=True)
            os.symlink(outside/'large',self.seed/'file-link')
        except OSError:
            # Windows commonly denies symlink creation without developer mode;
            # the platform-independent reparse test below covers that branch.
            return
        self.assertEqual(logical_bytes(self.seed),expected+20)

    def test_inventory_skips_junction_metadata_without_traversing_it(self):
        import stat
        from types import SimpleNamespace
        from unittest.mock import Mock,MagicMock,patch
        entry=Mock(path='must-not-be-opened')
        entry.stat.return_value=SimpleNamespace(st_mode=stat.S_IFDIR,st_reparse_tag=getattr(stat,'IO_REPARSE_TAG_MOUNT_POINT',-1),st_size=0)
        scan=MagicMock();scan.__iter__.return_value=iter([entry])
        with patch('tools.rolling_storage.os.scandir',return_value=scan)as scandir:
            self.assertEqual(logical_bytes(self.root),0)
            self.assertEqual(scandir.call_count,1)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.seed = self.root/'seed-1'; self.seed.mkdir()
        (self.seed/'result.json').write_text(json.dumps({'evaluations': []}))

    def put(self, path, data=b'example'):
        p = self.seed/path; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p

    def test_old_detail_expires_but_replay_actions_and_identity_survive(self):
        d = self.put('eval-1/data/decision.json'); cp = self.put('eval-1/data/checkpoints/x.json')
        trace = self.put('eval-1/data/trace.jsonl'); ident = self.put('eval-1/data/identity.json')
        report = prune_seed(self.seed, self.root, False, True)
        self.assertFalse(d.exists()); self.assertFalse(cp.exists())
        self.assertTrue(trace.exists()); self.assertTrue(ident.exists()); self.assertEqual(report['removed_files'], 2)

    def test_proof_and_pinned_case_retained(self):
        self.put('verify-eval-1/data/decision.json')
        proof = self.put('eval-1/data/checkpoints/x.json')
        self.put('eval-2/.retain-detail'); pinned = self.put('eval-2/data/decision.json')
        prune_seed(self.seed, self.root, False, True)
        self.assertTrue(proof.exists()); self.assertTrue(pinned.exists())

    def test_active_seed_not_pruned_even_with_result(self):
        cp = self.put('eval-1/data/checkpoints/x.json')
        with active_seed(self.seed):
            self.assertEqual(completed_seeds([self.root]), [])
            self.assertTrue(prune_seed(self.seed, self.root, False, True)['skipped_active'])
        self.assertTrue(cp.exists())

    def test_outside_target_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            p = Path(other)/'keep'; p.write_text('user file')
            with self.assertRaises(ValueError): safe_unlink(p, self.root)
            self.assertTrue(p.exists())

    def test_direct_job_marker_also_excludes_seed(self):
        marker=self.seed.parent/(self.seed.name+'.job-active.json'); marker.write_text('{}')
        self.assertEqual(completed_seeds([self.root]), [])
        self.assertTrue(prune_seed(self.seed, self.root, False, True)['skipped_active'])

    def test_recent_detail_kept_but_cache_and_duplicate_removed(self):
        d = self.put('eval-1/data/decision.json'); j = self.put('eval-1/data/decision-evidence.jsonl')
        prune_seed(self.seed, self.root, True, True)
        self.assertTrue(d.exists()); self.assertFalse(j.exists())

    def test_console_chunk_larger_than_window_preserves_bounded_tail(self):
        path = self.root/'console.log'; writer = RollingConsole(path, 4, 2)
        writer.write(b'abcdefghijklmnopqr'); writer.close()
        self.assertEqual(Path(str(path)+'.2').read_bytes()+Path(str(path)+'.1').read_bytes()+path.read_bytes(), b'ijklmnopqr')
        self.assertLessEqual(sum(p.stat().st_size for p in self.root.glob('console*')), 12)

if __name__ == '__main__': unittest.main()
