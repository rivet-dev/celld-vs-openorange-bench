#!/usr/bin/env python3
"""Apply the workload's Rivet runtime setting before bootstrap."""
import argparse,concurrent.futures,json
from common import ROOT,LOCAL,inventory,ssh,upload
from setup import secret_file

p=argparse.ArgumentParser();p.add_argument('workload');a=p.parse_args()
config=json.loads((ROOT/a.workload).read_text());workers=config.get('rivet_tokio_worker_threads')
assert workers is None or type(workers) is int and 1<=workers<=256
nodes=inventory()['nodes'];assert len(nodes)==config['nodes']
def configure(host):
    ssh(host,'sudo systemctl stop cost-rivet-runner cost-rivet-engine',timeout=180)
    path=LOCAL/'deploy'/host['name']/'engine.env'
    lines=[line for line in path.read_text().splitlines() if not line.startswith(('TOKIO_WORKER_THREADS=','TOKIO_WORKER_THREADS_SUBTRACT='))]
    if workers is not None:lines.append(f'TOKIO_WORKER_THREADS={workers}')
    secret_file(path,'\n'.join(lines)+'\n')
    upload(host,path,'/opt/rivet-celld-bench/engine.env')
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(configure,nodes))
print(f'Rivet async worker threads per process: {workers or "default"}. Run bootstrap next.')
