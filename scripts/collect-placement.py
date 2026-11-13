#!/usr/bin/env python3
"""Archive and validate the monitor's continuous Rivet tablet-placement samples."""
import argparse,json,shlex
from common import ROOT,inventory,ssh
from measurements import completed_levels
p=argparse.ArgumentParser();p.add_argument('run');a=p.parse_args();infra=inventory()
meta=json.loads((ROOT/'results'/a.run/'rivet/run.json').read_text())
completed_levels(meta)
nodes=meta.get('node_count',6);tablets=meta['workload']['rivet_tablets']
start=meta['stages'][0]['start_ms']/1000;end=meta['stages'][-1]['end_ms']/1000
program='''import json,sys,urllib.request,urllib.parse
args=json.load(sys.stdin)
url='http://localhost:9090/api/v1/query_range?'+urllib.parse.urlencode(args)
print(json.dumps(json.load(urllib.request.urlopen(url))))
'''
response=ssh(infra['monitor'],'python3 -c '+shlex.quote(program),input=json.dumps({'query':'rivet_depot_tablets{job="engine"}','start':start,'end':end,'step':15}))
data=json.loads(response.stdout);assert data['status']=='success'
names={h['private_ip']+':6430':h['name'] for h in infra['nodes'][:nodes]}
records=[]
for row in data['data']['result']:
 instance=row['metric']['instance'];role=row['metric']['worker_role']
 if instance not in names or role not in ['maintained','replica']:continue
 for stage in meta['stages']:
  selected=[float(v) for t,v in row['values'] if stage['start_ms']<=t*1000<stage['end_ms']]
  assert len(selected)>=19 and all(v==(tablets//nodes if role=='maintained' else tablets*3//nodes) for v in selected),(names[instance],role,stage['actors'],selected)
 records.append({'node':names[instance],'role':role,'values':row['values']})
assert len(records)==nodes*2
out=ROOT/'results'/a.run/'rivet/placement-timeseries.json';out.write_text(json.dumps(records,indent=2)+'\n')
print(f'All {nodes} nodes maintained {tablets//nodes} tablets and held {tablets*3//nodes} replicas throughout every measured hold.')
