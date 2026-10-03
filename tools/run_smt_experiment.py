"""Detached sequential one-seed SMT experiment with automatic evidence summary."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import write_json
from spire_exact.planning.resources import available_memory
from tools.run_seed import spawn_detached, run_base


def memory_sample():
    class Memory(ctypes.Structure):
        _fields_=[('size',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(k,ctypes.c_ulonglong) for k in
            ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
    m=Memory();m.size=ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)): raise ctypes.WinError()
    return {'unix':time.time(),'available_physical_bytes':m.available,'physical_load_percent':m.load,
            'available_commit_bytes':m.page_available}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--iteration',required=True);p.add_argument('--workspace',type=Path,required=True)
    p.add_argument('--detach',action='store_true')
    a=p.parse_args()
    folder=run_base(a.iteration)
    if a.detach:
        command=[sys.executable,Path(__file__).resolve(),* [v for v in sys.argv[1:] if v!='--detach']]
        pid=spawn_detached(command,folder/'smt-controller.log',ROOT)
        write_json(folder/'controller.json',{'pid':pid,'command':[str(v) for v in command]})
        print(json.dumps({'detached_pid':pid,'folder':str(folder)}));return
    # The controller's sampling and reporting use E cores. run_seed sets the job's P affinity.
    ctypes.windll.kernel32.SetProcessAffinityMask(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.c_size_t((1<<28)|(1<<29)))
    note='User 2026-10-02 authorized P8/P16 SMT experiment on seed 101 and relaxed memory; 24 GiB Job + 2 GiB reserve'
    common=['--seed','101','--minutes','60','--iteration',a.iteration,'--workspace',str(a.workspace),
            '--solver-seed','271828','--window','56','--efficiency-class','1','--job-memory-mib','24576','--resource-note',note]
    arms=[('p8-w7-101',8,7,'server-large-gen0',1792),('p16-w14-101',16,14,'server-bounded-large-gen0',1024)]
    report={'state':'running','jobs':[],'scope':'One seed; SMT plus GC jointly changed; one run per arm; no promotion'}
    for name,cpus,workers,runtime,worker_mib in arms:
        # Do not start a silently clamped pool under a changing external memory load.
        required=(workers*worker_mib+512)*1024**2
        available=available_memory()
        if available<required:
            report.update(state='resource_admission_failed',required_physical_bytes=required,available_physical_bytes=available)
            write_json(folder/'status.json',report);raise SystemExit('Insufficient physical memory for exact worker count')
        command=[sys.executable,str(ROOT/'tools/run_seed.py'),*common,'--name',name,'--logical-cpus',str(cpus),'--workers',str(workers),
                 '--extra','--root-policies','pick,elo','--focus-cluster-cap','2','--final-gate-plan','open',
                 '--runtime-profile',runtime,'--worker-memory-mib',str(worker_mib),'--reserve-mib','512']
        row={'name':name,'started_unix':time.time(),'command':command,'state':'running'}
        report['jobs'].append(row);write_json(folder/'status.json',report)
        stop=threading.Event()
        def sample():
            with (folder/(name+'-system-memory.jsonl')).open('w',encoding='utf-8') as stream:
                while not stop.is_set():
                    stream.write(json.dumps(memory_sample())+'\n');stream.flush();stop.wait(10)
        observer=threading.Thread(target=sample,daemon=True);observer.start()
        try:
            with (folder/(name+'-launcher.log')).open('w',encoding='utf-8') as log:
                proc=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                                      env=dict(os.environ,PYTHONIOENCODING='utf-8'),creationflags=subprocess.CREATE_NO_WINDOW)
                row['launcher_pid']=proc.pid;write_json(folder/'status.json',report)
                code=proc.wait()
        finally:stop.set();observer.join()
        row.update(state='finished',exit_code=code,finished_unix=time.time())
        write_json(folder/'status.json',report)
        if code or not (folder/name/'baseline-report.json').exists():
            report['state']='needs_review';write_json(folder/'status.json',report);raise SystemExit(1)
    from tools.analyze_smt_seed import compare
    result=compare(folder/arms[0][0],folder/arms[1][0])
    write_json(folder/'comparison.json',result)
    document=ROOT/'experiments'/a.iteration
    write_json(document/'comparison.json',result)
    lines=['# 种子 101：7 / 14 worker 运行结果','',
           '日期：2026-10-02。两臂使用 A+B + final-gate-plan open。14 worker 同时改用有界 GC；只有一个种子、每臂一次，不作推广或可靠性结论。','',
           '| 配置 | 验证胜利 | 完整运行秒 | 评估数 | 评估 / h | 节点 / 墙钟秒 | Job 峰值 GiB |',
           '|---|---|---:|---:|---:|---:|---:|']
    for s in result['arms']:
        lines.append(f"| {s['run']} | {'是' if s['verified_win'] else '未解出'} | {s['wall_seconds']} | {s['evaluations']} | {s['evaluations_per_hour']:.1f} | {s['combat_nodes_per_wall_second']:.1f} | {s['peak_job_commit_gib']} |")
    lines+=['',f"公共前缀 {result['common_prefix_length']} 条评估；保留完整导出字节全部一致：{result['all_common_game_bytes_equal']}。",'',
            '逐评估、GC、时钟裕度、错误分类和实际资源见 comparison.json。时间截断、错误和未解出均不能证明种子无解。胜利范围仅是原生 DLL TestMode 中新进程完整重放观察到 OnEnded(true)，不代表正常 Godot 游戏等价验证。',
            '',f'产物目录：{folder}。']
    (document/'results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    report['state']='completed';write_json(folder/'status.json',report)
    print(json.dumps({'state':'completed','report':str(document/'results.md')},ensure_ascii=False))


if __name__=='__main__':main()
