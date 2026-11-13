#!/usr/bin/env python3
"""Install an optional Grafana board on the benchmark monitor via SSH."""
import json
from common import inventory,LOCAL,ssh

infra=inventory();nodes='|'.join(h['private_ip'].replace('.','[.]')+':9100' for h in infra['nodes'])
node=f'job="node",instance=~"{nodes}"'
queries=[
 ('Rivet tablets maintained per node','rivet_depot_tablets{job="engine",worker_role="maintained"}','short'),
 ('Rivet tablet replicas per node','rivet_depot_tablets{job="engine",worker_role="replica"}','short'),
 ('Connected agents','sum(bench_connected_actors{job="loadgen"})','short'),
 ('Acknowledged SQLite writes / second','sum(rate(bench_writes_total{job="loadgen"}[1m]))','ops'),
 ('Client errors','sum(bench_errors_total{job="loadgen"})','short'),
 ('Rivet object-store HTTP attempts / second','sum by (method) (rate(rivet_depot_object_store_http_attempts_total{job="engine"}[1m]))','ops'),
]
panels=[]
for index,(title,expr,unit) in enumerate(queries):
 panels.append({'id':index+1,'type':'timeseries','title':title,'gridPos':{'x':index%2*12,'y':index//2*8,'w':12,'h':8},'datasource':{'type':'prometheus','uid':'prometheus'},'targets':[{'refId':'A','expr':expr,'legendFormat':'{{instance}} {{method}}'}],'fieldConfig':{'defaults':{'unit':unit,'min':0,'custom':{'drawStyle':'line','lineInterpolation':'linear','lineWidth':2,'fillOpacity':8}},'overrides':[]},'options':{'legend':{'displayMode':'list','placement':'bottom'},'tooltip':{'mode':'multi'}}})
board={'uid':'rivet-celld-cost','title':'Rivet vs celld — held agents','schemaVersion':39,'version':1,'refresh':'10s','time':{'from':'now-1h','to':'now'},'panels':panels}
(LOCAL/'dashboard.json').write_text(json.dumps(board,indent=2))
program="""import json,sys,urllib.request,base64
body=json.load(sys.stdin)
r=urllib.request.Request('http://localhost:3000/api/dashboards/db',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Basic '+base64.b64encode(b'admin:admin').decode()})
print(json.load(urllib.request.urlopen(r)))
"""
import shlex
print(ssh(infra['monitor'],'python3 -c '+shlex.quote(program),input=json.dumps({'dashboard':board,'overwrite':True})).stdout)
