#!/usr/bin/env python3
"""Start a fresh benchmark namespace, preserving previous data and deployments."""
import concurrent.futures,json,secrets
from common import LOCAL,inventory,ssh,upload,ROOT
from setup import secret_file
nodes=inventory()['nodes']
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
 list(pool.map(lambda h:ssh(h,'sudo systemctl stop cost-rivet-runner cost-rivet-engine cost-celld',timeout=180),nodes))
old=(LOCAL/'object-prefix').read_text().strip();new='sweep-'+secrets.token_hex(6)
(LOCAL/'object-prefix').write_text(new)
def update(h):
 d=LOCAL/'deploy'/h['name']
 previous=json.loads((d/'engine.json').read_text())['object_storage']['primary']['azure']['prefix']
 for name in ['engine.json','operator.json','celld.env','cost-celld.service']:
  p=d/name;secret_file(p,p.read_text().replace(previous,new));upload(h,p,'/opt/rivet-celld-bench/'+name)
 ssh(h,f'mkdir -p /var/lib/rivet/cost-{new}/rivet /var/lib/rivet/cost-{new}/celld; sudo cp /opt/rivet-celld-bench/cost-celld.service /etc/systemd/system/; sudo systemctl daemon-reload')
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(update,nodes))
for h in inventory()['loadgens']:upload(h,ROOT/'driver/main.mjs','/opt/rivet-celld-bench/driver/main.mjs')
print('Fresh isolated namespace prepared. Run scripts/bootstrap.py next.')
