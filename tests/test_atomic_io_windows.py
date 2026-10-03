import json,os,tempfile,threading,time,unittest
from pathlib import Path
from spire_exact.planning.io import write_json

@unittest.skipUnless(os.name=='nt','Windows file sharing regression')
class AtomicIoWindowsTests(unittest.TestCase):
    def test_transient_reader_does_not_abort_atomic_publication(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'result.json';write_json(path,{'version':0})
            reader=path.open('rb')
            released=threading.Event()
            def release():
                time.sleep(.06);reader.close();released.set()
            thread=threading.Thread(target=release);thread.start()
            try:
                write_json(path,{'version':1,'trace':list(range(500))})
                self.assertTrue(released.is_set())
                self.assertEqual(json.loads(path.read_text())['version'],1)
            finally:thread.join();reader.close()

    def test_dashboard_readers_and_repeated_writer_do_not_conflict(self):
        from dashboard.server import read
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'result.json';write_json(path,{'version':0,'trace':[0]*10000})
            stop=threading.Event();errors=[];seen=[]
            def watch():
                while not stop.is_set():
                    value=read(path)
                    if value is None:errors.append('incomplete or unavailable snapshot')
                    else:seen.append(value['version'])
            thread=threading.Thread(target=watch);thread.start()
            try:
                for i in range(1,31):write_json(path,{'version':i,'trace':[i]*10000})
            finally:stop.set();thread.join()
            self.assertTrue(seen);self.assertFalse(errors)
            self.assertEqual(read(path)['version'],30)

if __name__=='__main__':unittest.main()
