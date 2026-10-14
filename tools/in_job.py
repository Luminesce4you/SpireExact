"""Run a Python tool inside its own Windows Job Object with a memory cap.

The shared worker-memory policy (105 A: `--worker-memory-policy shared`)
budgets one Job cap for all workers instead of a fixed size per worker, and
tools refuse it unless an enforced cap encloses them. `run_seed.py` gives a
real run that Job; this wrapper gives one to a tool started from a shell.

  python tools/in_job.py --job-mib 24576 tools/speed_calibration.py --reference ... --out ...

The cap counts committed memory (RAM plus page file), as the 105 runs did.
Closing the Job (this process exiting) kills every worker it owns.
"""
import ctypes as c
from ctypes import wintypes as w
import runpy
import sys

JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
EXTENDED_LIMIT_INFORMATION = 9


class Basic(c.Structure):
    _fields_ = [('PerProcessUserTimeLimit', c.c_longlong), ('PerJobUserTimeLimit', c.c_longlong),
                ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', c.c_size_t), ('MaximumWorkingSetSize', c.c_size_t),
                ('ActiveProcessLimit', w.DWORD), ('Affinity', c.c_size_t), ('PriorityClass', w.DWORD),
                ('SchedulingClass', w.DWORD)]


class IO(c.Structure):
    _fields_ = [(x, c.c_ulonglong) for x in ['ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount']]


class Extended(c.Structure):
    _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IO), ('ProcessMemoryLimit', c.c_size_t),
                ('JobMemoryLimit', c.c_size_t), ('PeakProcessMemoryUsed', c.c_size_t), ('PeakJobMemoryUsed', c.c_size_t)]


def enter_job(mib):
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.GetCurrentProcess.restype = w.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError(c.get_last_error(), 'CreateJobObjectW failed')
    info = Extended()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    info.JobMemoryLimit = mib * 1024 * 1024
    if not kernel.SetInformationJobObject(w.HANDLE(job), EXTENDED_LIMIT_INFORMATION, c.byref(info), c.sizeof(info)):
        raise OSError(c.get_last_error(), 'SetInformationJobObject failed')
    if not kernel.AssignProcessToJobObject(w.HANDLE(job), w.HANDLE(kernel.GetCurrentProcess())):
        raise OSError(c.get_last_error(), 'AssignProcessToJobObject failed')
    return job  # keep the handle open for the lifetime of this process


def main():
    args = sys.argv[1:]
    if len(args) < 3 or args[0] != '--job-mib':
        raise SystemExit(__doc__)
    job = enter_job(int(args[1]))
    sys.argv = args[2:]
    runpy.run_path(sys.argv[0], run_name='__main__')
    return job


if __name__ == '__main__':
    main()
