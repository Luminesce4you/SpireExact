"""Lossless output compression and storage admission; never a game outcome."""
import os,shutil,subprocess
from pathlib import Path

GIB=1024**3

def ensure_capacity(path,parallel_jobs=1):
    free=shutil.disk_usage(path).free
    required=(8+2*parallel_jobs)*GIB
    if free<required:
        raise RuntimeError(f'STORAGE_ADMISSION_DENIED: free={free}, required={required}; no seed was executed')
    return {'free_bytes':free,'required_free_bytes':required}

def prepare_storage(path,parallel_jobs=1):
    path=Path(path).resolve()
    report=ensure_capacity(path,parallel_jobs)
    report['logical_file_bytes_unchanged']=True
    report['compression']='none'
    if os.name=='nt':
        compact=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/compact.exe'
        completed=subprocess.run([str(compact),'/C','/I','/Q',str(path)],capture_output=True,timeout=30)
        import ctypes
        get_attributes=ctypes.WinDLL('kernel32',use_last_error=True).GetFileAttributesW
        get_attributes.argtypes=[ctypes.c_wchar_p];get_attributes.restype=ctypes.c_uint32
        attributes=get_attributes(str(path))
        if completed.returncode!=0 or attributes==0xffffffff or not attributes&0x800:
            raise RuntimeError('STORAGE_COMPRESSION_UNAVAILABLE: output directory was not marked compressed')
        report['compression']='NTFS transparent lossless; inherited by new files'
    return report
