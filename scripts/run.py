#!/usr/bin/env python3
"""Run identical held-population sweeps sequentially on the common machines."""
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import math
import shlex
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from common import ROOT, LOCAL, inventory, ssh, upload, download_gzip
from placement import manifest, validate
from capacity import snapshot as capacity_snapshot

REMOTE = '/opt/rivet-celld-bench'
INFRA = inventory()

def parallel(hosts, fn):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        return list(pool.map(fn, hosts))

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')

def control(host, route='status', data=None):
    command = 'curl -fsS --max-time 10 '
    if data is not None: command += '-H "Content-Type: application/json" -d ' + shlex.quote(json.dumps(data)) + ' '
    return json.loads(ssh(host, command + 'http://127.0.0.1:9500/' + route).stdout)

def status(): return parallel(INFRA['loadgens'], control)

def valid(states, count):
    if states and 'ready_slots' in states[0]:
        return sum(s['ready_slots'] for s in states)==count and sum(s['connected']+s['opening']+s.get('retrying',0) for s in states)==count and all(s['errors']==0 and s['connect_errors']==0 for s in states)
    return sum(s['connected'] for s in states) == count and all(s['errors'] == 0 and s['connect_errors'] == 0 for s in states)

def start(platform, name, scenario, config):
    def service(host):
        if platform == 'celld' and scenario == 'churn':
            ssh(host,f'grep -qx "CELLD_IDLE_EVICT_S=1" {REMOTE}/celld.env')
        stop = 'cost-celld' if platform == 'rivet' else 'cost-rivet-runner cost-rivet-engine'
        start = 'cost-rivet-engine cost-rivet-runner' if platform == 'rivet' else 'cost-celld'
        ssh(host, f'sudo systemctl stop {stop}; sudo systemctl start {start}', timeout=180)
        if platform == 'rivet':
            # Read only the runtime knobs from the live process, never its credentials.
            program="import json,pathlib,subprocess; pid=subprocess.check_output(['systemctl','show','cost-rivet-engine','-p','MainPID','--value'],text=True).strip(); values=dict(v.split('=',1) for v in pathlib.Path('/proc/'+pid+'/environ').read_text().split('\\0') if '=' in v); print(json.dumps({k:values.get(k) for k in ['TOKIO_WORKER_THREADS','TOKIO_WORKER_THREADS_SUBTRACT']}))"
            runtime=json.loads(ssh(host,'python3 -c '+shlex.quote(program)).stdout)
            expected=config.get('rivet_tokio_worker_threads')
            assert runtime=={'TOKIO_WORKER_THREADS':str(expected) if expected is not None else None,'TOKIO_WORKER_THREADS_SUBTRACT':None}, 'Runtime differs from workload; run scripts/configure-runtime.py first'
    parallel(INFRA['nodes'], service)
    endpoints = ','.join(f"http://{h['private_ip']}:{6420 if platform == 'rivet' else 8080}" for h in INFRA['nodes'])
    def driver(item):
        shard, host = item
        driver_file = 'churn.mjs' if scenario == 'churn' else 'main.mjs'
        upload(host,ROOT/'driver'/driver_file,REMOTE+'/driver/'+driver_file)
        args = f'{REMOTE}/node/bin/node {REMOTE}/driver/{driver_file} --platform {platform} --endpoints {endpoints} --run {name} --shard {shard} --shards {len(INFRA["loadgens"])} --out {REMOTE}/results/{name}/{platform} --host 0.0.0.0 --port 9500'
        ssh(host, f'sudo systemctl stop cost-driver 2>/dev/null || true; sudo systemctl reset-failed cost-driver 2>/dev/null || true; sudo systemd-run --unit=cost-driver --uid={INFRA.get("admin_user", "rivet")} --property=LimitNOFILE=1048576 --property=WorkingDirectory={REMOTE} --property=EnvironmentFile={REMOTE}/driver.env {args}')
    parallel(list(enumerate(INFRA['loadgens'])), driver)
    deadline = time.monotonic() + 90
    while True:
        try:
            status(); return
        except Exception:
            if time.monotonic() > deadline: raise
            time.sleep(3)

def collect(name, platform, dest):
    def fetch(item):
        shard, host = item
        for filename in ('samples.ndjson', 'events.ndjson'):
            if filename == 'events.ndjson':
                download_gzip(host, f'{REMOTE}/results/{name}/{platform}/{filename}', dest/f'shard-{shard}-{filename}.gz')
                continue
            result = ssh(host, f'cat {REMOTE}/results/{name}/{platform}/{filename}', timeout=180)
            (dest / f'shard-{shard}-{filename}').write_text(result.stdout)
    parallel(list(enumerate(INFRA['loadgens'])), fetch)

def stop():
    parallel(INFRA['loadgens'], lambda h: ssh(h,'sudo systemctl stop cost-driver',check=False))
    parallel(INFRA['nodes'],lambda h:ssh(h,'sudo systemctl stop cost-sampler',check=False))

def run(platform, name, smoke=False, scenario="held", workload=None, levels=None):
    dest = ROOT / ('.local/smoke' if smoke else 'results') / name / platform
    if (dest/'run.json').exists():
        raise RuntimeError(f'Run already exists: {dest}; choose a new --name')
    dest.mkdir(parents=True, exist_ok=True)
    config_path = ROOT/(workload or ('workload-churn.json' if scenario == 'churn' else 'workload.json'))
    config = json.loads(config_path.read_text())
    if levels is not None:
        assert levels==sorted(set(levels)) and all(n in config['levels'] for n in levels), 'Select ordered levels from the workload'
        config['levels']=levels
    expected={'write_interval_ms':10000,'write_jitter_fraction':0.2,'payload_bytes':64,'rows_per_actor':16}
    assert all(config[k]==v for k,v in expected.items()), 'Workload metadata must match the two fixtures and shared driver'
    assert len(INFRA['nodes'])==config['nodes'], 'Inventory must match the workload node count'
    assert all(n % len(INFRA['loadgens']) == 0 for n in config['levels']), 'Levels must divide evenly across load generators'
    metadata = {'platform': platform, 'run': name, 'workload': config, 'node_count':len(INFRA['nodes']), 'loadgen_count':len(INFRA['loadgens']), 'stages': [], 'started_ms': round(time.time()*1000), 'complete': False}
    metadata['node_sizes']=dict(Counter(h['size'] for h in INFRA['nodes']))
    if not smoke:
        sources=['rivet/app/index.ts','celld/app/index.ts','celld/app/wrangler.json','driver/main.mjs','driver/churn.mjs','workload.json','workload-churn.json','rivet/engine/overrides.json','versions.json','versions.sha256.json','pnpm-lock.yaml','pricing.json']
        if str(config_path.relative_to(ROOT)) not in sources:sources.append(str(config_path.relative_to(ROOT)))
        hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources}
        manifest_path=dest.parent/'source-sha256.json'
        if manifest_path.exists():assert json.loads(manifest_path.read_text())==hashes,'Benchmark source changed between platforms'
        save(manifest_path,hashes)
        for source in sources:
            target=dest.parent/'source'/source;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/source).read_bytes())
    save(dest/'run.json', metadata)
    start(platform, name, scenario, config)
    try:
        for level in ([8] if smoke else config['levels']):
            parallel(INFRA['loadgens'], lambda h: control(h,'stage',{'target':level//len(INFRA['loadgens']), 'level':level}))
            deadline = time.monotonic() + 180
            while True:
                states = status()
                if valid(states, level): break
                if any(s['errors'] or s['connect_errors'] for s in states) or time.monotonic() > deadline:
                    raise RuntimeError(f'{platform} {level}: population not ready: {states}')
                time.sleep(3)
            print(f'{platform}: {level} actors connected; settling', flush=True)
            if platform == 'rivet':
                placement=manifest(); validate(placement, node_count=len(INFRA['nodes']), tablet_count=config['rivet_tablets']); save(dest/f'placement-{level}.json',placement)
            until = time.time() + (10 if smoke else config['settle_seconds'])
            measure_start = until if smoke else math.ceil(until/60)*60
            while time.time() < measure_start:
                time.sleep(min(5, max(0,measure_start-time.time())))
                assert valid(status(),level), 'population changed during settling'
            before = status()
            stage = {'actors':level,'node_count':len(INFRA['nodes']),'start_ms':round(measure_start*1000),'before':before}
            duration = (130 if scenario == 'churn' else 30) if smoke else config['measure_seconds']
            end = measure_start+duration
            print(f'{platform}: measuring {level} actors for {duration}s',flush=True)
            while time.time() < end:
                time.sleep(min(10,max(0,end-time.time())))
                states=status()
                assert valid(states,level), f'population/errors changed: {states}'
            stage.update({'end_ms':round(end*1000),'after':status()})
            stage['counter_delta_writes'] = sum(s['writes'] for s in stage['after'])-sum(s['writes'] for s in before)
            assert stage['counter_delta_writes'] > 0, 'no acknowledged SQLite writes'
            metadata['stages'].append(stage); save(dest/'run.json',metadata)
            if not smoke and level == config['levels'][-1]:
                stop()
            if not smoke: capacity_snapshot(name,platform,level)
            collect(name,platform,dest)
            print(f'{platform}: completed {level} actors, {stage["counter_delta_writes"]} writes',flush=True)
        if smoke and scenario == 'churn':
            subprocess.run([sys.executable,str(ROOT/'scripts/probe-retained.py'),name,platform],check=True)
        metadata.update({'complete':True,'finished_ms':round(time.time()*1000)})
    finally:
        stop(); collect(name,platform,dest); save(dest/'run.json',metadata)
    return dest

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--platform', choices=['rivet','celld','both'],default='both')
    parser.add_argument('--name',default=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--scenario',choices=['held','churn'],default='held')
    parser.add_argument('--workload',help='Workload JSON, including levels and node count')
    parser.add_argument('--levels',type=int,nargs='+',help='Run only these levels from the workload')
    args=parser.parse_args()
    for platform in (['rivet','celld'] if args.platform=='both' else [args.platform]):
        print(run(platform,args.name,args.smoke,args.scenario,args.workload,args.levels),flush=True)
