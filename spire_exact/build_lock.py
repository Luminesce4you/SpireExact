"""Serialize native compilation across processes sharing one checkout."""
from contextlib import contextmanager
from pathlib import Path
import os,time

@contextmanager
def native_build_lock(path,timeout=240):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b')as f:
        f.seek(0,2)
        if not f.tell():f.write(b'0');f.flush()
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            deadline=time.monotonic()+timeout
            while True:
                try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if time.monotonic()>=deadline:raise TimeoutError('NATIVE_BUILD_LOCK_TIMEOUT')
                    time.sleep(.1)
        else:
            import fcntl
            deadline=time.monotonic()+timeout
            while True:
                try:fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()>=deadline:raise TimeoutError('NATIVE_BUILD_LOCK_TIMEOUT')
                    time.sleep(.1)
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)
