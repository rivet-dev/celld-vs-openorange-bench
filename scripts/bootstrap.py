#!/usr/bin/env python3
"""Start Rivet, provision balanced partitions, create its pool, and deploy celld."""
import concurrent.futures
import time
from common import inventory, ssh, LOCAL
from placement import main as provision

nodes=inventory()['nodes']
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    list(pool.map(lambda h:ssh(h,'sudo systemctl start cost-rivet-engine'),nodes))
for attempt in range(60):
    try: provision(); break
    except Exception:
        if attempt==59: raise
        time.sleep(3)
program='''import json,urllib.request,time,urllib.error
c=json.load(open('/opt/rivet-celld-bench/engine.json'))
for attempt in range(60):
 r=urllib.request.Request('http://127.0.0.1:6420/pools/default?namespace=default',data=b'{}',method='PUT',headers={'Authorization':'Bearer '+c['auth']['admin_token'],'Content-Type':'application/json'})
 try:
  urllib.request.urlopen(r,timeout=30).read(); print('Default pool ready'); break
 except urllib.error.HTTPError:
  if attempt==59: raise
  time.sleep(3)
'''
print(ssh(nodes[0],'python3 -',input=program,timeout=240).stdout)
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    list(pool.map(lambda h:ssh(h,'sudo systemctl restart cost-rivet-runner'),nodes))
prefix=(LOCAL/'object-prefix').read_text().strip()
r=ssh(nodes[0],f'set -a; . /opt/rivet-celld-bench/celld.env; set +a; cd /opt/rivet-celld-bench; bin/celld deploy celld/app --bucket az://benchmark/{prefix}',timeout=180)
print(r.stdout)
