#!/usr/bin/env python3
"""Stop only benchmark services; optionally deallocate the dedicated VMs."""
import argparse,concurrent.futures,subprocess
from common import inventory,ssh
p=argparse.ArgumentParser();p.add_argument('--deallocate',action='store_true');a=p.parse_args();infra=inventory()
def stop(h):
 ssh(h,'sudo systemctl stop cost-driver cost-sampler cost-rivet-runner cost-rivet-engine cost-celld 2>/dev/null || true',timeout=240)
 print(h['name'],'benchmark services stopped',flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(stop,infra['nodes']+infra['loadgens']))
if a.deallocate:
 for h in infra['nodes']+infra['loadgens']:
  subprocess.run(['az','vm','deallocate','--resource-group',infra['resource_group'],'--name',h['name'],'--no-wait'],check=True)
 print('VM deallocation requested; disks and Blob data retained.')
